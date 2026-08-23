"""福特派中国区 API 客户端.

支持两种认证方式:
1. B2C OAuth 授权码 + PKCE（推荐，2025年后福特全球统一认证）
2. refresh_token 直接刷新（备选）
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import time
from enum import Enum
from typing import Any, Optional
from urllib.parse import urlencode, parse_qs, urlparse

import aiohttp

_LOGGER = logging.getLogger(__name__)

# ============================================================
# 中国区 API 端点
# ============================================================
SSO_URL = "https://sso.ci.ford.com.cn/"
CV_URL = "https://cnapi.cv.ford.com.cn/"
API_URL = "https://cn.api.mps.ford.com.cn/"

# Azure AD B2C 配置（福特全球统一 tenant）
B2C_TENANT_ID = "4566605f-43a7-400a-946e-89cc9fdb0bd7"
B2C_POLICY = "B2C_1A_SignInSignUp_zh-CN"
B2C_CLIENT_ID = "09852200-05fd-41f6-8c21-d36d3497dc64"
B2C_SCOPE = "09852200-05fd-41f6-8c21-d36d3497dc64 openid"
B2C_REDIRECT_URI = "fordapp://userauthorized"
B2C_FORD_APP_ID = "5C80A6BB-CF0D-4A30-BDBF-FC804B5C1A98"

# 应用 ID（福特 / 林肯，用于车辆 API 的 Application-Id 头）
APPLICATION_ID = {
    "ford": "35F9024B-010E-4FE7-B202-62D941F8681C",
    "lincoln": "5EE5E683-1B71-4D6B-BAA8-F344D6672796",
}

# 默认请求头
DEFAULT_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh-Hans;q=0.9",
    "User-Agent": "fordpass-cn/320 CFNetwork/1331.0.7 Darwin/21.4.0",
    "Accept-Encoding": "gzip, deflate",
}

# B2C 登录页面请求头（模拟移动端 Safari）
B2C_LOGIN_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                  "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 "
                  "Mobile/15E148 Safari/604.1",
    "Accept-Language": "zh-CN,zh-Hans;q=0.9",
}


class CommandResult(str, Enum):
    """命令执行结果."""

    SUCCESS = "success"
    PENDING = "pending"
    FAILED = "failed"


class FordPassAPI:
    """福特派中国区 API 客户端."""

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
        self._code_verifier: Optional[str] = None

        if refresh_token:
            self._expires = time.time() - 100

    # ============================================================
    # B2C OAuth 授权码流程
    # ============================================================

    @staticmethod
    def _generate_code_verifier() -> str:
        """生成 PKCE code_verifier（43-128 字符的随机字符串）."""
        return secrets.token_urlsafe(64)

    @staticmethod
    def _generate_code_challenge(verifier: str) -> str:
        """根据 code_verifier 生成 S256 code_challenge."""
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    def generate_auth_url(self) -> tuple[str, str]:
        """生成 B2C 授权登录 URL.

        Returns:
            (auth_url, code_verifier): 登录 URL 和对应的 PKCE 验证器
        """
        self._code_verifier = self._generate_code_verifier()
        code_challenge = self._generate_code_challenge(self._code_verifier)

        params = {
            "redirect_uri": B2C_REDIRECT_URI,
            "response_type": "code",
            "max_age": "3600",
            "scope": B2C_SCOPE,
            "client_id": B2C_CLIENT_ID,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "ui_locales": "zh-CN",
            "language_code": "zh-CN",
            "country_code": "CHN",
            "ford_application_id": B2C_FORD_APP_ID,
        }

        auth_url = (
            f"{SSO_URL}{B2C_TENANT_ID}/{B2C_POLICY}/oauth2/v2.0/authorize"
            f"?{urlencode(params)}"
        )
        _LOGGER.debug("生成 B2C 授权 URL: %s", auth_url)
        return auth_url, self._code_verifier

    @staticmethod
    def extract_code_from_redirect(redirect_url: str) -> Optional[str]:
        """从浏览器回调 URL 中提取授权码 code.

        支持的格式:
        - fordapp://userauthorized/?code=xxx
        - https://...?code=xxx
        - 直接粘贴 code 值
        """
        if not redirect_url:
            return None

        redirect_url = redirect_url.strip()

        # 如果直接是 code（不包含 URL 特征）
        if "=" not in redirect_url and "://" not in redirect_url and len(redirect_url) > 20:
            return redirect_url

        # 解析 URL
        parsed = urlparse(redirect_url)
        if parsed.query:
            qs = parse_qs(parsed.query)
            if "code" in qs:
                return qs["code"][0]

        # 有些回调把 code 放在 fragment 中
        if parsed.fragment:
            qs = parse_qs(parsed.fragment)
            if "code" in qs:
                return qs["code"][0]

        return None

    async def exchange_code_for_token(
        self, code: str, code_verifier: Optional[str] = None
    ) -> bool:
        """用授权码 code 交换 B2C token，再换福特 API token.

        步骤:
        1. POST B2C token 端点，用 code + code_verifier 换 B2C access_token
        2. POST cat-with-b2c-access-token，用 B2C access_token 换福特 API token

        Args:
            code: 从浏览器回调中获取的授权码
            code_verifier: PKCE 验证器（如果为 None，使用实例中保存的）
        """
        verifier = code_verifier or self._code_verifier
        if not verifier:
            _LOGGER.error("缺少 code_verifier，无法交换 token")
            return False

        # Step 1: 用 code 换 B2C access_token
        token_url = (
            f"{SSO_URL}{B2C_TENANT_ID}/{B2C_POLICY}/oauth2/v2.0/token"
        )
        data = {
            "client_id": B2C_CLIENT_ID,
            "grant_type": "authorization_code",
            "code_verifier": verifier,
            "code": code,
            "redirect_uri": B2C_REDIRECT_URI,
        }
        headers = {
            **B2C_LOGIN_HEADERS,
            "Content-Type": "application/x-www-form-urlencoded",
        }

        code_b2c, resp_b2c = await self._call_api(
            url=token_url, method="post", data=data, headers=headers
        )

        if code_b2c != 200 or not isinstance(resp_b2c, dict) or "access_token" not in resp_b2c:
            _LOGGER.error("B2C token 交换失败: %s %s", code_b2c, resp_b2c)
            return False

        b2c_access_token = resp_b2c["access_token"]
        _LOGGER.debug("B2C access_token 获取成功")

        # Step 2: 用 B2C access_token 换福特 API token
        ford_token_url = f"{API_URL}api/token/v2/cat-with-b2c-access-token"
        ford_data = json.dumps({"idpToken": b2c_access_token})
        ford_headers = {
            **DEFAULT_HEADERS,
            "Content-Type": "application/json",
            "Application-Id": self._app_id,
        }

        code_ford, resp_ford = await self._call_api(
            url=ford_token_url, method="post", data=ford_data, headers=ford_headers
        )

        if code_ford == 200 and isinstance(resp_ford, dict) and "access_token" in resp_ford:
            self._token = resp_ford["access_token"]
            self._refresh_token = resp_ford.get("refresh_token", self._refresh_token)
            expires_in = resp_ford.get("expires_in", 3600)
            self._expires = time.time() + expires_in - 60
            _LOGGER.info("福特 API token 获取成功，有效期 %s 秒", expires_in)
            # 清除 code_verifier，一次性使用
            self._code_verifier = None
            return True

        _LOGGER.error("福特 API token 交换失败: %s %s", code_ford, resp_ford)
        return False

    # ============================================================
    # refresh_token 刷新（备选方式）
    # ============================================================

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

    # ============================================================
    # 通用 API 调用
    # ============================================================

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
                method=method, url=url,
                timeout=aiohttp.ClientTimeout(total=30), **kwargs
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
                    _LOGGER.error("API %s 返回 %d: %s", url, code, response)
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
        self._code_verifier = None

    @property
    def refresh_token_value(self) -> Optional[str]:
        """返回当前 refresh_token."""
        return self._refresh_token

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

    # ============================================================
    # 用户 & 车辆信息
    # ============================================================

    async def get_user_info(self) -> Optional[dict]:
        """获取用户信息."""
        code, response = await self._safe_call_api(url=f"{CV_URL}api/users")
        if code == 200 and isinstance(response, dict):
            return response.get("profile")
        return None

    async def get_vehicles(self) -> Optional[list[dict]]:
        """获取用户车辆列表."""
        code, response = await self._safe_call_api(url=f"{CV_URL}api/users/vehicles")
        if code == 200 and isinstance(response, dict):
            vehicles = response.get("vehicles", {})
            if isinstance(vehicles, dict):
                return vehicles.get("$values", [])
            if isinstance(vehicles, list):
                return vehicles
        return None

    async def get_vehicle_detail(self, vin: str) -> Optional[dict]:
        """获取车辆详细信息."""
        code, response = await self._safe_call_api(
            url=f"{CV_URL}api/users/vehicles/{vin}/detail"
        )
        if code == 200 and isinstance(response, dict):
            return response.get("vehicle")
        return None

    async def get_vehicle_status(self, vin: str) -> Optional[dict]:
        """获取车辆实时状态（核心数据接口）."""
        code, response = await self._safe_call_api(
            url=f"{CV_URL}api/vehicles/v5/{vin}/status"
        )
        if code == 200 and isinstance(response, dict):
            return response.get("vehiclestatus")
        return None

    # ============================================================
    # 远程控制命令
    # ============================================================

    async def _send_command(self, url: str, method: str) -> Optional[str]:
        """发送远程控制命令，返回 commandId."""
        _LOGGER.debug("发送命令: %s %s", method.upper(), url)
        code, response = await self._safe_call_api(url=url, method=method)
        if code == 200 and isinstance(response, dict):
            return response.get("commandId")
        _LOGGER.error("命令发送失败: %s", response)
        return None

    async def _check_command(self, url: str) -> CommandResult:
        """检查命令执行状态（200=成功, 552=执行中）."""
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
        """发送开关类命令（远程启动、锁车等）."""
        method = "put" if turn_on else "delete"
        return await self._send_command(
            url=f"{CV_URL}api/vehicles/v2/{vin}/{end_point}",
            method=method,
        )

    async def get_switch_completed(
        self, vin: str, end_point: str, command_id: str
    ) -> CommandResult:
        """查询开关命令执行结果."""
        return await self._check_command(
            url=f"{CV_URL}api/vehicles/v2/{vin}/{end_point}/{command_id}"
        )

    async def remote_start(self, vin: str, start: bool = True) -> Optional[str]:
        """远程启动/关闭发动机."""
        return await self.set_switch(vin, "engine/start", start)

    async def lock_doors(self, vin: str, lock: bool = True) -> Optional[str]:
        """锁车/解锁."""
        return await self.set_switch(vin, "doors/lock", lock)


# 避免在模块顶层 import asyncio 导致循环
import asyncio  # noqa: E402
