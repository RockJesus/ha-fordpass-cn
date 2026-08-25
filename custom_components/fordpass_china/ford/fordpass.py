"""FordPass China (福特派) API 客户端。

基于国内福特派 App 后端逆向实现。2026-08 实测：
  - https://cnapi.cv.ford.com.cn  车辆服务（Azure APIM，仍在用）
  - https://cn.api.mps.ford.com.cn 令牌交换/服务历史（Azure APIM，仍在用）
  - https://sso.ci.ford.com.cn    旧版 SSO（已迁移到腾讯 EdgeOne，旧路径失效，
    保留在此仅作为“账户密码”后备认证路径；认证请优先使用 refresh_token）

所有基础 URL 均集中在文件顶部常量中，福特一旦变更，改这里即可。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from aiohttp import ClientError, ClientSession

_LOGGER = logging.getLogger(__name__)

# ---- 基础 URL（2026-08 实测在线；福特变更时只需改这里）----
SSO_URL = "https://sso.ci.ford.com.cn/"
CV_URL = "https://cnapi.cv.ford.com.cn/"
API_URL = "https://cn.api.mps.ford.com.cn/"

# ---- App 常量（逆向自国内福特派 App）----
DEFAULT_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh-Hans;q=0.9",
    "User-Agent": "fordpass-cn/320 CFNetwork/1331.0.7 Darwin/21.4.0",
    "Accept-Encoding": "gzip, deflate",
}

# 福特派 / 林肯之道 各自的 Application-Id
APPLICATION_ID = {
    "ford": "35F9024B-010E-4FE7-B202-62D941F8681C",
    "lincoln": "5EE5E683-1B71-4D6B-BAA8-F344D6672796",
}

CLIENT_ID = "6487f540-5f6b-4c04-8384-23827b00b4ba"


class CommandResult(str):
    SUCCESS = "success"
    PENDING = "pending"
    FAILED = "failed"


class FordPass:
    """福特派 China API 客户端。"""

    def __init__(
        self,
        session: ClientSession,
        username: str | None = None,
        password: str | None = None,
        vehicle_type: str = "ford",
        refresh_token: str | None = None,
    ) -> None:
        self._session = session
        self._AppID = APPLICATION_ID.get(vehicle_type, APPLICATION_ID["ford"])
        self._username = username
        self._password = password
        self._token: str | None = None
        self._refresh_token = refresh_token
        if refresh_token:
            # 强制在首次调用前用 refresh_token 换取新 token
            self._expires: float | None = time.time() - 100
        else:
            self._expires = None

    # ------------------------------------------------------------------
    # 基础请求
    # ------------------------------------------------------------------
    def make_api_header(self, use_token: bool = True) -> dict[str, str]:
        header = {
            **DEFAULT_HEADERS,
            "Content-Type": "application/json",
            "Application-Id": self._AppID,
        }
        if use_token and self._token:
            header["auth-token"] = self._token
        return header

    async def call_api(self, url: str, method: str, **kwargs: Any) -> tuple[int, Any]:
        """发送请求并解析 JSON；对非 JSON 返回做容错。"""
        try:
            r = await self._session.request(method=method, url=url, timeout=30, **kwargs)
        except (ClientError, TimeoutError, OSError) as err:
            _LOGGER.error("Request %s %s failed: %s", method, url, err)
            return -1, None
        code = r.status
        raw = await r.read()
        try:
            response = json.loads(raw)
        except (ValueError, TypeError):
            response = raw.decode("utf-8", errors="replace")
        if code not in (200, 201):
            _LOGGER.debug("API %s result code: %s, reason: %s, body: %s", url, code, r.reason, response)
        return code, response

    def clear_token(self) -> None:
        self._token = None
        self._refresh_token = None
        self._expires = None

    # ------------------------------------------------------------------
    # 认证
    # ------------------------------------------------------------------
    async def auth(self) -> bool:
        """SSO 密码授权 → 换取福特派 token（后备路径，SSO 可能已变更）。"""
        data = {
            "client_id": CLIENT_ID,
            "grant_type": "password",
            "username": self._username,
            "password": self._password,
        }
        headers = {**DEFAULT_HEADERS, "Content-Type": "application/x-www-form-urlencoded"}
        code, response = await self.call_api(
            url=f"{SSO_URL}oidc/endpoint/default/token",
            method="post",
            data=data,
            headers=headers,
        )
        if code == 200 and isinstance(response, dict):
            data = {"ciToken": response.get("access_token")}
            code, response = await self.call_api(
                url=f"{API_URL}api/token/v2/cat-with-ci-access-token",
                method="post",
                data=json.dumps(data),
                headers=self.make_api_header(use_token=False),
            )
            if code == 200 and isinstance(response, dict):
                self._token = response.get("access_token")
                self._refresh_token = response.get("refresh_token")
                self._expires = time.time() + int(response.get("expires_in", 0)) - 100
                return bool(self._token)
        return False

    async def refresh_token(self) -> bool:
        """用 refresh_token 换取新的 access_token / refresh_token（主认证路径）。"""
        if not self._refresh_token:
            _LOGGER.error("No refresh_token configured")
            return False
        self._token = None
        self._expires = None
        data = {"refresh_token": self._refresh_token}
        code, response = await self.call_api(
            url=f"{API_URL}api/token/v2/cat-with-refresh-token",
            method="post",
            data=json.dumps(data),
            headers=self.make_api_header(use_token=False),
        )
        if code == 200 and isinstance(response, dict):
            self._token = response.get("access_token")
            new_refresh = response.get("refresh_token")
            if new_refresh:
                self._refresh_token = new_refresh
            self._expires = time.time() + int(response.get("expires_in", 3600)) - 100
            return bool(self._token)
        self.clear_token()
        if code == 401:
            _LOGGER.warning("refresh_token expired/invalid, trying password auth")
            return await self.auth()
        _LOGGER.error("Refresh token failed with status %s: %s", code, response)
        return False

    async def check_token(self) -> bool:
        if self._expires and time.time() > self._expires - 60:
            _LOGGER.debug("Token nearly expired, refreshing...")
            await self.refresh_token()
        elif not self._expires:
            await self.auth()
        return self._token is not None

    async def safe_call_api(self, url: str, method: str = "get", headers: dict | None = None) -> tuple[int, Any]:
        if not await self.check_token():
            return -1, None
        merged = dict(headers.items(), **self.make_api_header()) if headers else self.make_api_header()
        return await self.call_api(url=url, method=method, headers=merged)

    # ------------------------------------------------------------------
    # 数据接口
    # ------------------------------------------------------------------
    async def get_user_info(self) -> dict | None:
        code, response = await self.safe_call_api(url=f"{CV_URL}api/users")
        if code == 200 and isinstance(response, dict):
            return response.get("profile")
        return None

    async def get_vehicles(self) -> list[dict] | None:
        """返回车辆列表。兼容新旧两种返回结构。"""
        code, response = await self.safe_call_api(url=f"{CV_URL}api/users/vehicles")
        if code != 200 or not isinstance(response, dict):
            return None
        # 新结构：vehicles.$values / 旧结构：直接数组
        vehicles = response.get("vehicles")
        if isinstance(vehicles, dict) and "$values" in vehicles:
            return vehicles["$values"]
        if isinstance(vehicles, list):
            return vehicles
        return None

    async def get_vehicle_info(self, vin: str) -> dict | None:
        code, response = await self.safe_call_api(url=f"{CV_URL}api/users/vehicles/{vin}/detail")
        if code == 200 and isinstance(response, dict):
            return response.get("vehicle")
        return None

    async def get_vehicle_status(self, vin: str) -> dict | None:
        code, response = await self.safe_call_api(url=f"{CV_URL}api/vehicles/v5/{vin}/status")
        if code == 200 and isinstance(response, dict):
            return response.get("vehiclestatus")
        return None

    async def get_vehicle_auth_status(self, vin: str) -> dict | None:
        code, response = await self.safe_call_api(url=f"{CV_URL}api/vehicles/{vin}/authstatus")
        if code == 200 and isinstance(response, dict):
            return response.get("vehicleAuthorizationStatus", {}).get("authorization")
        return None

    async def get_vehicle_service_history(self, vin: str) -> dict | None:
        code, response = await self.safe_call_api(
            url=f"{API_URL}api/servicehistory/v1/service-history?vin={vin}"
        )
        return response if code == 200 else None

    # ------------------------------------------------------------------
    # 远程控制
    # ------------------------------------------------------------------
    async def _send_command(self, url: str, method: str) -> str | None:
        _LOGGER.debug("Send command URL: %s, method: %s", url, method)
        code, response = await self.safe_call_api(url=url, method=method)
        if code == 200 and isinstance(response, dict):
            return response.get("commandId")
        _LOGGER.error("Command failed %s %s -> %s %s", method, url, code, response)
        return None

    async def _check_command(self, url: str) -> CommandResult:
        code, response = await self.safe_call_api(url=url)
        if code == 200 and isinstance(response, dict):
            status = response.get("status")
            if status == 200:
                _LOGGER.debug("Command %s completed", url)
                return CommandResult.SUCCESS
            if status == 552:  # pending
                _LOGGER.debug("Command %s pending", url)
                return CommandResult.PENDING
            _LOGGER.error("Unexpected status for %s: %s", url, response)
        return CommandResult.FAILED

    async def async_set_switch(self, vin: str, end_point: str, turn_on: bool) -> str | None:
        method = "put" if turn_on else "delete"
        return await self._send_command(
            url=f"{CV_URL}api/vehicles/v2/{vin}/{end_point}", method=method
        )

    async def async_get_switch_completed(self, vin: str, end_point: str, command_id: str) -> CommandResult:
        return await self._check_command(
            url=f"{CV_URL}api/vehicles/v2/{vin}/{end_point}/{command_id}"
        )
