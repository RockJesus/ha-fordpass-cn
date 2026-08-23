"""配置流程 - FordPass CN.

支持三种认证方式:
1. access_token（推荐）: 抓包任意API请求的 auth-token 头，直接粘贴
2. refresh_token: 如果能抓到 refresh_token
3. OAuth 授权码: 如果中国区B2C端点可用
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .const import (
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    CONF_VEHICLE_TYPE,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_VEHICLE_TYPE,
    DOMAIN,
    VEHICLE_TYPES,
)
from .fordpass_api import FordPassAPI

_LOGGER = logging.getLogger(__name__)

# 认证方式
AUTH_TYPE_ACCESS = "access_token"
AUTH_TYPE_TOKEN = "refresh_token"
AUTH_TYPE_OAUTH = "oauth"

AUTH_TYPES = {
    AUTH_TYPE_ACCESS: "Access Token（推荐，抓包获取）",
    AUTH_TYPE_TOKEN: "Refresh Token",
    AUTH_TYPE_OAUTH: "浏览器 OAuth 登录（备选）",
}


class FordPassConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """处理配置流程."""

    VERSION = 1

    def __init__(self) -> None:
        self._vehicle_type: str = DEFAULT_VEHICLE_TYPE
        self._auth_type: str = AUTH_TYPE_ACCESS
        self._code_verifier: str | None = None
        self._auth_url: str | None = None
        self._api: FordPassAPI | None = None

    def _is_already_configured(self, title: str) -> bool:
        for entry in self._async_current_entries():
            if entry.title == title:
                return True
        return False

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> Any:
        """第一步：选择车辆类型和认证方式."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._vehicle_type = user_input.get(CONF_VEHICLE_TYPE, DEFAULT_VEHICLE_TYPE)
            self._auth_type = user_input.get("auth_type", AUTH_TYPE_ACCESS)

            if self._auth_type == AUTH_TYPE_ACCESS:
                return await self.async_step_access_token()
            elif self._auth_type == AUTH_TYPE_TOKEN:
                return await self.async_step_refresh_token()
            else:
                return await self.async_step_oauth_url()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_VEHICLE_TYPE, default=DEFAULT_VEHICLE_TYPE): vol.In(VEHICLE_TYPES),
                    vol.Required("auth_type", default=AUTH_TYPE_ACCESS): vol.In(AUTH_TYPES),
                }
            ),
            errors=errors if errors else None,
        )

    # ----------------------------------------------------------
    # 方式1: 直接输入 access_token
    # ----------------------------------------------------------

    async def async_step_access_token(self, user_input: dict[str, Any] | None = None) -> Any:
        """直接输入 access_token（从抓包获取的 auth-token）."""
        errors: dict[str, str] = {}

        if user_input is not None:
            access_token = user_input.get(CONF_ACCESS_TOKEN, "").strip()

            if not access_token:
                errors["base"] = "missing_access_token"
            else:
                session = async_create_clientsession(self.hass)
                api = FordPassAPI(
                    session=session,
                    vehicle_type=self._vehicle_type,
                    access_token=access_token,
                )

                # 验证 token 是否有效（尝试获取用户信息）
                user_info = await api.get_user_info()
                vehicles = await api.get_vehicles()

                if user_info or vehicles:
                    user_id = (user_info or {}).get("userId", "user")
                    title = f"{user_id}@{VEHICLE_TYPES.get(self._vehicle_type, '福特派')}"

                    if self._is_already_configured(title):
                        errors["base"] = "already_configured"
                    else:
                        return self.async_create_entry(
                            title=title,
                            data={
                                CONF_VEHICLE_TYPE: self._vehicle_type,
                                CONF_ACCESS_TOKEN: access_token,
                            },
                        )
                else:
                    errors["base"] = "invalid_access_token"

        return self.async_show_form(
            step_id="access_token",
            data_schema=vol.Schema({vol.Required(CONF_ACCESS_TOKEN): str}),
            errors=errors if errors else None,
            description_placeholders={
                "hint": (
                    "获取方法：\n"
                    "1. 手机安装抓包工具（HttpCanary / Stream / Charles）\n"
                    "2. 启动抓包，打开福特派 App，任意操作（如刷新车辆状态）\n"
                    "3. 在抓包结果中找到 cnapi.cv.ford.com.cn 或 cn.api.mps.ford.com.cn 的请求\n"
                    "4. 查看请求头，找到 auth-token 字段，复制其值（一长串JWT token）\n\n"
                    "注意：access_token 通常有效期 1-24 小时，过期后需重新抓包更新。"
                ),
            },
        )

    # ----------------------------------------------------------
    # 方式2: refresh_token
    # ----------------------------------------------------------

    async def async_step_refresh_token(self, user_input: dict[str, Any] | None = None) -> Any:
        """直接输入 refresh_token."""
        errors: dict[str, str] = {}

        if user_input is not None:
            refresh_token = user_input.get(CONF_REFRESH_TOKEN, "").strip()

            if not refresh_token:
                errors["base"] = "missing_refresh_token"
            else:
                session = async_create_clientsession(self.hass)
                api = FordPassAPI(
                    session=session,
                    vehicle_type=self._vehicle_type,
                    refresh_token=refresh_token,
                )

                if await api.refresh_token():
                    user_info = await api.get_user_info()
                    user_id = (user_info or {}).get("userId", "user")
                    title = f"{user_id}@{VEHICLE_TYPES.get(self._vehicle_type, '福特派')}"

                    if self._is_already_configured(title):
                        errors["base"] = "already_configured"
                    else:
                        return self.async_create_entry(
                            title=title,
                            data={
                                CONF_VEHICLE_TYPE: self._vehicle_type,
                                CONF_REFRESH_TOKEN: refresh_token,
                            },
                        )
                else:
                    errors["base"] = "invalid_refresh_token"

        return self.async_show_form(
            step_id="refresh_token",
            data_schema=vol.Schema({vol.Required(CONF_REFRESH_TOKEN): str}),
            errors=errors if errors else None,
        )

    # ----------------------------------------------------------
    # 方式3: OAuth 授权码
    # ----------------------------------------------------------

    async def async_step_oauth_url(self, user_input: dict[str, Any] | None = None) -> Any:
        """生成并显示 B2C 登录 URL."""
        if self._api is None:
            session = async_create_clientsession(self.hass)
            self._api = FordPassAPI(session=session, vehicle_type=self._vehicle_type)

        self._auth_url, self._code_verifier = self._api.generate_auth_url()

        if user_input is not None:
            return await self.async_step_oauth_code()

        return self.async_show_form(
            step_id="oauth_url",
            data_schema=vol.Schema({}),
            description_placeholders={
                "auth_url": self._auth_url or "",
                "instructions": "复制上面的URL在浏览器打开，登录后粘贴回调URL到下一步",
            },
        )

    async def async_step_oauth_code(self, user_input: dict[str, Any] | None = None) -> Any:
        """接收回调 URL 并交换 token."""
        errors: dict[str, str] = {}

        if user_input is not None:
            redirect_url = user_input.get("redirect_url", "").strip()
            code = FordPassAPI.extract_code_from_redirect(redirect_url)

            if not code:
                errors["base"] = "invalid_code"
            else:
                assert self._api is not None
                success = await self._api.exchange_code_for_token(
                    code=code, code_verifier=self._code_verifier
                )

                if success:
                    user_info = await self._api.get_user_info()
                    user_id = (user_info or {}).get("userId", "user")
                    title = f"{user_id}@{VEHICLE_TYPES.get(self._vehicle_type, '福特派')}"
                    refresh_token = self._api.refresh_token_value or ""

                    if self._is_already_configured(title):
                        errors["base"] = "already_configured"
                    else:
                        return self.async_create_entry(
                            title=title,
                            data={
                                CONF_VEHICLE_TYPE: self._vehicle_type,
                                CONF_REFRESH_TOKEN: refresh_token,
                            },
                        )
                else:
                    errors["base"] = "token_exchange_failed"

        return self.async_show_form(
            step_id="oauth_code",
            data_schema=vol.Schema({vol.Required("redirect_url"): str}),
            errors=errors if errors else None,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> "FordPassOptionsFlow":
        return FordPassOptionsFlow(config_entry)


class FordPassOptionsFlow(config_entries.OptionsFlow):
    """配置选项流程."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> Any:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        scan_interval = self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_SCAN_INTERVAL, default=scan_interval): vol.All(
                        vol.Coerce(int), vol.Range(min=1, max=60)
                    ),
                }
            ),
        )
