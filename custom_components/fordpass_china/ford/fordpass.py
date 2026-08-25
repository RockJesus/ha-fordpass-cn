"""FordPass China (福特派) API 客户端。

基于国内福特派 App（v6.15.0，2026-08）逆向实现。2026 新认证体系结论：
  - 车辆服务 https://cnapi.cv.ford.com.cn（在线，仍用旧 /api/vehicles/* 路径）
  - 令牌交换 https://cn.api.mps.ford.com.cn（在线）
  - 认证已从「账号密码 SSO + refresh_token」迁移为「DLT 令牌」体系：
      * 登录/刷新端点走 /api/cnxapi-token-exchange/v1/app/*（DLT 令牌）
      * 敏感字段（手机号/验证码/刷新令牌）在传输时经白盒 AES 加密 + sign/xjw 签名，
        因此抓包看到的 refresh_token 是密文 —— 这就是旧集成“抓不到 refresh_token”的根因
  - 新 App 实际使用 consumer 环境的 Application-Id（下方 APPLICATION_ID["ford"]）

所有基础 URL / App 常量均集中在文件顶部，福特一旦变更，改这里即可。
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

# ---- App 常量（逆向自国内福特派 App v6.15.0）----
DEFAULT_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh-Hans;q=0.9",
    "User-Agent": "fordpass-cn/320 CFNetwork/1331.0.7 Darwin/21.4.0",
    "Accept-Encoding": "gzip, deflate",
}

# 2026-08 实测有效的 Ngsdn 网关头（缺一即被拒：header xxx is invalid）
NGSDN_HEADERS = {
    "appVersion": "6.15.0",   # 无效会报 "this app version: xxx is invalid"
    "osType": "android",      # 也接受 ios
    "clientType": "fp",       # 福特派；其他值报 "this clientType: xxx is invalid"
}

# 福特派 / 林肯之道 各自的 Application-Id。
# 2026-08 实测：生产 App 实际使用 consumer 环境的 46409D04-…（prod 的 AD5D8A89-… 已被网关拒绝）
APPLICATION_ID = {
    "ford": "46409D04-BD1B-40C6-9D51-13A52666E9F9",
    "lincoln": "5EE5E683-1B71-4D6B-BAA8-F344D6672796",
}

# 旧版 Application-Id（2022 开源版；已失效，保留作历史参考）
LEGACY_APPLICATION_ID = {
    "ford": "35F9024B-010E-4FE7-B202-62D941F8681C",
}

CLIENT_ID = "6487f540-5f6b-4c04-8384-23827b00b4ba"

# 2026 DLT 令牌交换端点（cn.api.mps.ford.com.cn）
DLT_TOKEN_EXCHANGE = "api/cnxapi-token-exchange/v1/app/"
#   登录/换发：dlt-token-by-phone-passcode-login / dlt-token-by-b2c-auth-code /
#             dlt-token-by-phone-one-click-login / b2c-jwt-token-by-social-login
#   刷新：     refresh-dlt-token
# 注意：这些端点请求体要求 encryptedPhoneNumber / encryptedAuthCode / encryptedRefreshToken
#       （白盒 AES）+ sign + xjw + timestamp + brand，非纯文本可直连，需先完成加密层逆向。


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
        dlt_token: str | None = None,
    ) -> None:
        self._session = session
        self._AppID = APPLICATION_ID.get(vehicle_type, APPLICATION_ID["ford"])
        self._username = username
        self._password = password
        self._token: str | None = None
        self._refresh_token = refresh_token
        self._dlt_token = dlt_token
        if refresh_token:
            # 强制在首次调用前用 refresh_token 换取新 token
            self._expires: float | None = time.time() - 100
        elif dlt_token:
            # DLT 访问令牌直接可用；有效期未知，超时后需重新粘贴
            self._token = dlt_token
            self._expires = None
        else:
            self._expires = None

    # ------------------------------------------------------------------
    # 基础请求
    # ------------------------------------------------------------------
    def make_api_header(self, use_token: bool = True) -> dict[str, str]:
        header = {
            **DEFAULT_HEADERS,
            **NGSDN_HEADERS,
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
        if self._dlt_token:
            # DLT 令牌粘贴模式：令牌已就绪，直接使用。
            # 2026 新体系下 DLT 刷新需白盒 AES 加密（见 DLT_TOKEN_EXCHANGE 注释），
            # 无法在纯 Python 侧完成；令牌过期后需在「选项」里重新粘贴。
            return self._token is not None
        if self._expires and time.time() > self._expires - 60:
            _LOGGER.debug("Token nearly expired, refreshing...")
            await self.refresh_token()
        elif not self._expires:
            await self.auth()
        return self._token is not None

    async def refresh_dlt_token(self) -> bool:
        """尝试通过 2026 DLT 刷新端点续期（需白盒 AES，当前未实现加密）。

        该方法保留调用框架：一旦加密层（encryptedRefreshToken + sign + xjw）逆向完成，
        只需填充下方请求体即可启用。当前返回 False 并给出明确提示。
        """
        _LOGGER.warning(
            "DLT token refresh requires white-box AES encryption (encryptedRefreshToken + sign + xjw), "
            "not yet implemented. Please re-paste a fresh auth-token from the FordPass app."
        )
        return False

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
