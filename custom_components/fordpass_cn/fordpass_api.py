"""福特派中国区 API 客户端."""

from __future__ import annotations

import json
import logging
import time
from enum import Enum
from typing import Any, Optional

import aiohttp

_LOGGER = logging.getLogger(__name__)

# ============================================================
# 中国区 API 端点
# ============================================================
SSO_URL = "https://sso.ci.ford.com.cn/"
CV_URL = "https://cnapi.cv.ford.com.cn/"
API_URL = "https://cn.api.mps.ford.com.cn/"

# 应用 ID（福特 / 林肯）
APPLICATION_ID = {
    "ford": "35F9024B-010E-4FE7-B202-62D941F8681C",
    "lincoln": "5EE5E683-1B71-4D6B-BAA8-F344D6672796",
}

# SSO 客户端 ID
CLIENT_ID = "6487f540-5f6b-4c04-8384-23827b00b4ba"

# 默认请求头
DEFAULT_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh-Hans;q=0.9",
    "User-Agent": "fordpass-cn/320 CFNetwork/1331.0.7 Darwin/21.4.0",
    "Accept-Encoding": "gzip, deflate",
}


class CommandResult(str, Enum):
    """命令执行结果."""

    SUCCESS = "success"
    PENDING = "pending"
    FAILED = "failed"


class FordPassAPI:
    """福特派中国区 API 客户端.

    认证流程:
    1. (已废弃) 用户名密码 -> SSO token -> 换 access_token
    2. refresh_token -> 直接换 access_token (当前可用方式)
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        vehicle_type: str = "ford",
        refresh_token: Optional[str] = None,
    ) -> None:
        self._session = session
        self._vehicle_type = vehicle_type if vehicle_type in APPLICATION_ID else "ford"
        self._app_id = APPLICATION_ID[self._vehicle_type]
        self._token: Optional[str] = None
        self._refresh_token: Optional[str] = refresh_token
        self._expires: Optional[float] = None

        if refresh_token:
            # 标记为已过期，下次调用时自动刷新
            self._expires = time.time() - 100

    # ----------------------------------------------------------
    # 认证相关
    # ----------------------------------------------------------

    def _make_api_header(self, use_token: bool = True) -> dict[str, str]:
        """构造 API 请求头."""
        header = {
            **DEFAULT_HEADERS,
            "Content-Type": "application/json",
            "Application-Id": self._app_id,
        }
        if use_token and self._token:
            header["auth-token"] = self._token
        return header

    async def _call_api(
        self, url: str, method: str, **kwargs: Any
    ) -> tuple[int, Any]:
        """调用 API 并返回 (状态码, 解析后的JSON)."""
        try:
            async with self._session.request(
                method=method, url=url, timeout=aiohttp.ClientTimeout(total=30), **kwargs
            ) as resp:
                code = resp.status
                raw = await resp.read()
                if not raw:
                    return code, {}
                try:
                    response = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    _LOGGER.debug("API 返回非 JSON 内容: %s", raw[:200])
                    return code, {"raw": raw.decode("utf-8", errors="replace")}

                if code != 200:
                    _LOGGER.error(
                        "API %s 返回 %d: %s", url, code, response
                    )
                return code, response
        except aiohttp.ClientError as err:
            _LOGGER.error("API 请求失败 %s: %s", url, err)
            return -1, {"error": str(err)}
        except asyncio.TimeoutError:
            _LOGGER.error("API 请求超时 %s", url)
            return -1, {"error": "timeout"}

    def clear_tokens(self) -> None:
        """清除所有 token."""
        self._token = None
        self._refresh_token = None
        self._expires = None

    @property
    def refresh_token_value(self) -> Optional[str]:
        """返回当前 refresh_token."""
        return self._refresh_token

    async def refresh_token(self) -> bool:
        """使用 refresh_token 获取新的 access_token.

        端点: POST {API_URL}api/token/v2/cat-with-refresh-token
        Body: {"refresh_token": "..."}
        """
        if not self._refresh_token:
            _LOGGER.error("没有可用的 refresh_token")
            return False

        self._token = None
        self._expires = None

        data = json.dumps({"refresh_token": self._refresh_token})
        code, response = await self._call_api(
            url=f"{API_URL}api/token/v2/cat-with-refresh-token",
            method="post",
            data=data,
            headers=self._make_api_header(use_token=False),
        )

        if code == 200 and isinstance(response, dict) and "access_token" in response:
            self._token = response["access_token"]
            self._refresh_token = response.get("refresh_token", self._refresh_token)
            expires_in = response.get("expires_in", 3600)
            self._expires = time.time() + expires_in - 60
            _LOGGER.debug("Token 刷新成功，有效期 %s 秒", expires_in)
            return True

        _LOGGER.error("Token 刷新失败: code=%s, response=%s", code, response)
        self.clear_tokens()
        return False

    async def _check_token(self) -> bool:
        """检查 token 是否有效，无效则刷新."""
        if self._expires and time.time() < self._expires:
            return True
        return await self.refresh_token()

    async def _safe_call_api(
        self, url: str, method: str = "get", extra_headers: Optional[dict] = None
    ) -> tuple[int, Any]:
        """带 token 检查的 API 调用."""
        if not await self._check_token():
            return -1, {"error": "auth_failed"}

        headers = self._make_api_header()
        if extra_headers:
            headers.update(extra_headers)

        return await self._call_api(url=url, method=method, headers=headers)

    # ----------------------------------------------------------
    # 用户 & 车辆信息
    # ----------------------------------------------------------

    async def get_user_info(self) -> Optional[dict]:
        """获取用户信息.

        GET {CV_URL}api/users
        """
        code, response = await self._safe_call_api(url=f"{CV_URL}api/users")
        if code == 200 and isinstance(response, dict):
            return response.get("profile")
        return None

    async def get_vehicles(self) -> Optional[list[dict]]:
        """获取用户车辆列表.

        GET {CV_URL}api/users/vehicles
        返回: vehicles.$values 数组
        """
        code, response = await self._safe_call_api(url=f"{CV_URL}api/users/vehicles")
        if code == 200 and isinstance(response, dict):
            vehicles = response.get("vehicles", {})
            if isinstance(vehicles, dict):
                return vehicles.get("$values", [])
            if isinstance(vehicles, list):
                return vehicles
        return None

    async def get_vehicle_detail(self, vin: str) -> Optional[dict]:
        """获取车辆详细信息.

        GET {CV_URL}api/users/vehicles/{vin}/detail
        """
        code, response = await self._safe_call_api(
            url=f"{CV_URL}api/users/vehicles/{vin}/detail"
        )
        if code == 200 and isinstance(response, dict):
            return response.get("vehicle")
        return None

    async def get_vehicle_status(self, vin: str) -> Optional[dict]:
        """获取车辆实时状态（核心数据接口）.

        GET {CV_URL}api/vehicles/v5/{vin}/status
        返回: vehiclestatus 对象
        """
        code, response = await self._safe_call_api(
            url=f"{CV_URL}api/vehicles/v5/{vin}/status"
        )
        if code == 200 and isinstance(response, dict):
            return response.get("vehiclestatus")
        return None

    # ----------------------------------------------------------
    # 远程控制命令
    # ----------------------------------------------------------

    async def _send_command(self, url: str, method: str) -> Optional[str]:
        """发送远程控制命令，返回 commandId."""
        _LOGGER.debug("发送命令: %s %s", method.upper(), url)
        code, response = await self._safe_call_api(url=url, method=method)
        if code == 200 and isinstance(response, dict):
            return response.get("commandId")
        _LOGGER.error("命令发送失败: %s", response)
        return None

    async def _check_command(self, url: str) -> CommandResult:
        """检查命令执行状态.

        状态码: 200=成功, 552=执行中
        """
        code, response = await self._safe_call_api(url=url)
        if code == 200 and isinstance(response, dict):
            status = response.get("status")
            if status == 200:
                return CommandResult.SUCCESS
            if status == 552:
                return CommandResult.PENDING
            _LOGGER.error("命令返回异常状态: %s", response)
        return CommandResult.FAILED

    async def set_switch(
        self, vin: str, end_point: str, turn_on: bool
    ) -> Optional[str]:
        """发送开关类命令（远程启动、锁车等）.

        PUT = 开启 / DELETE = 关闭
        端点: {CV_URL}api/vehicles/v2/{vin}/{end_point}
        """
        method = "put" if turn_on else "delete"
        return await self._send_command(
            url=f"{CV_URL}api/vehicles/v2/{vin}/{end_point}",
            method=method,
        )

    async def get_switch_completed(
        self, vin: str, end_point: str, command_id: str
    ) -> CommandResult:
        """查询开关命令执行结果.

        GET {CV_URL}api/vehicles/v2/{vin}/{end_point}/{command_id}
        """
        return await self._check_command(
            url=f"{CV_URL}api/vehicles/v2/{vin}/{end_point}/{command_id}"
        )

    # 常用命令端点快捷方法
    async def remote_start(self, vin: str, start: bool = True) -> Optional[str]:
        """远程启动/关闭发动机."""
        return await self.set_switch(vin, "engine/start", start)

    async def lock_doors(self, vin: str, lock: bool = True) -> Optional[str]:
        """锁车/解锁."""
        return await self.set_switch(vin, "doors/lock", lock)


# 避免在模块顶层 import asyncio 导致循环
import asyncio  # noqa: E402
