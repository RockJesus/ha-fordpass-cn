"""配置流程 - FordPass CN.

支持两种认证方式:
1. B2C OAuth 授权码（推荐）: 集成生成登录 URL → 用户浏览器登录 → 粘贴回调 URL
2. refresh_token（备选）: 直接粘贴 refresh_token
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .const import (
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
AUTH_TYPE_OAUTH = "oauth"
AUTH_TYPE_TOKEN = "token"

AUTH_TYPES = {
    AUTH_TYPE_OAUTH: "浏览器登录（推荐，OAuth 授权码）",
    AUTH_TYPE_TOKEN: "直接输入 refresh_token",
}


class FordPassConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """处理配置流程."""

    VERSION = 1

    def __init__(self) -> None:
        self._vehicle_type: str = DEFAULT_VEHICLE_TYPE
        self._auth_type: str = AUTH_TYPE_OAUTH
        self._code_verifier: str | None = None
        self._auth_url: str | None = None
        self._api: FordPassAPI | None = None

    def _is_already_configured(self, title: str) -> bool:
        """检查是否已配置相同账户."""
        for entry in self._async_current_entries():
            if entry.title == title:
                return True
        return False

    # ----------------------------------------------------------
    # Step 1: 选择认证方式
    # ----------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        """第一步：选择车辆类型和认证方式."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._vehicle_type = user_input.get(CONF_VEHICLE_TYPE, DEFAULT_VEHICLE_TYPE)
            self._auth_type = user_input.get("auth_type", AUTH_TYPE_OAUTH)

            if self._auth_type == AUTH_TYPE_OAUTH:
                return await self.async_step_oauth_url()
            else:
                return await self.async_step_token()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_VEHICLE_TYPE, default=DEFAULT_VEHICLE_TYPE
                    ): vol.In(VEHICLE_TYPES),
                    vol.Required("auth_type", default=AUTH_TYPE_OAUTH): vol.In(AUTH_TYPES),
                }
            ),
            errors=errors if errors else None,
        )

    # ----------------------------------------------------------
    # Step 2a: OAuth - 显示登录 URL
    # ----------------------------------------------------------

    async def async_step_oauth_url(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        """生成并显示 B2C 登录 URL，用户确认后进入下一步."""
        if self._api is None:
            session = async_create_clientsession(self.hass)
            self._api = FordPassAPI(
                session=session,
                vehicle_type=self._vehicle_type,
            )

        # 生成授权 URL 和 code_verifier
        self._auth_url, self._code_verifier = self._api.generate_auth_url()

        # 如果用户点击了"下一步"，进入粘贴 code 的步骤
        if user_input is not None:
            return await self.async_step_oauth_code()

        return self.async_show_form(
            step_id="oauth_url",
            data_schema=vol.Schema({}),
            description_placeholders={
                "auth_url": self._auth_url or "",
                "instructions": (
                    "请按以下步骤操作：\n\n"
                    "1. 复制上面的登录 URL\n"
                    "2. 在浏览器（建议无痕模式）中打开该 URL\n"
                    "3. 使用你的福特派账号登录\n"
                    "4. 登录后页面会显示错误/转圈（正常现象），此时地址栏会变成 "
                    "fordapp://userauthorized/?code=... 格式\n"
                    "5. 复制完整的地址栏 URL，下一步粘贴\n\n"
                    "注意：如果浏览器无法打开 fordapp:// 链接，请按 F12 打开开发者工具，"
                    "在 Network（网络）标签中找到最后一个请求，复制其 Location 响应头中的完整 URL。\n\n"
                    "完成浏览器登录后，点击下方'下一步'继续。"
                ),
            },
        )

    # ----------------------------------------------------------
    # Step 2b: OAuth - 接收回调 URL 并交换 token
    # ----------------------------------------------------------

    async def async_step_oauth_code(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        """接收用户粘贴的回调 URL，提取 code 并交换 token."""
        errors: dict[str, str] = {}

        if user_input is not None:
            redirect_url = user_input.get("redirect_url", "").strip()

            # 提取授权码
            code = FordPassAPI.extract_code_from_redirect(redirect_url)

            if not code:
                errors["base"] = "invalid_code"
            else:
                assert self._api is not None
                # 用 code 交换 token
                success = await self._api.exchange_code_for_token(
                    code=code, code_verifier=self._code_verifier
                )

                if success:
                    # 获取用户信息
                    user_info = await self._api.get_user_info()
                    if user_info:
                        user_id = user_info.get("userId", "unknown")
                    else:
                        user_id = "user"

                    title = (
                        f"{user_id}@{VEHICLE_TYPES.get(self._vehicle_type, '福特派')}"
                    )

                    if self._is_already_configured(title):
                        errors["base"] = "already_configured"
                    else:
                        # 保存 refresh_token 到配置
                        refresh_token = self._api.refresh_token_value or ""
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
            data_schema=vol.Schema(
                {
                    vol.Required("redirect_url"): str,
                }
            ),
            errors=errors if errors else None,
            description_placeholders={
                "hint": "粘贴登录后浏览器地址栏中的完整 URL（以 fordapp:// 开头），或直接粘贴 code 值",
            },
        )

    # ----------------------------------------------------------
    # Step 2（备选）: 直接输入 refresh_token
    # ----------------------------------------------------------

    async def async_step_token(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        """直接输入 refresh_token 认证."""
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
                    if user_info:
                        user_id = user_info.get("userId", "unknown")
                    else:
                        user_id = "user"

                    title = (
                        f"{user_id}@{VEHICLE_TYPES.get(self._vehicle_type, '福特派')}"
                    )

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
            step_id="token",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_REFRESH_TOKEN): str,
                }
            ),
            errors=errors if errors else None,
            description_placeholders={
                "hint": "如果你已经通过抓包获取了有效的 refresh_token，可以直接粘贴在这里",
            },
        )

    # ----------------------------------------------------------
    # 选项流程
    # ----------------------------------------------------------

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> FordPassOptionsFlow:
        """返回选项流程."""
        return FordPassOptionsFlow(config_entry)


class FordPassOptionsFlow(config_entries.OptionsFlow):
    """配置选项流程."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        """管理配置选项."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        scan_interval = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=scan_interval,
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
                }
            ),
        )
