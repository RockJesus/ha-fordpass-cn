"""配置流程 - FordPass CN."""

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


class FordPassConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """处理配置流程."""

    VERSION = 1

    def __init__(self) -> None:
        self._refresh_token: str | None = None
        self._vehicle_type: str = DEFAULT_VEHICLE_TYPE

    def _is_already_configured(self, title: str) -> bool:
        """检查是否已配置相同账户."""
        for entry in self._async_current_entries():
            if entry.title == title:
                return True
        return False

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        """第一步：选择车辆类型并输入 refresh_token."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._vehicle_type = user_input.get(CONF_VEHICLE_TYPE, DEFAULT_VEHICLE_TYPE)
            self._refresh_token = user_input.get(CONF_REFRESH_TOKEN, "").strip()

            if not self._refresh_token:
                errors["base"] = "missing_refresh_token"
            else:
                # 验证 refresh_token 是否有效
                session = async_create_clientsession(self.hass)
                api = FordPassAPI(
                    session=session,
                    vehicle_type=self._vehicle_type,
                    refresh_token=self._refresh_token,
                )

                if await api.refresh_token():
                    # 获取用户信息作为配置标题
                    user_info = await api.get_user_info()
                    if user_info:
                        user_id = user_info.get("userId", "unknown")
                    else:
                        user_id = "user"

                    title = f"{user_id}@{VEHICLE_TYPES.get(self._vehicle_type, '福特派')}"

                    if self._is_already_configured(title):
                        errors["base"] = "already_configured"
                    else:
                        return self.async_create_entry(
                            title=title,
                            data={
                                CONF_VEHICLE_TYPE: self._vehicle_type,
                                CONF_REFRESH_TOKEN: self._refresh_token,
                            },
                        )
                else:
                    errors["base"] = "invalid_refresh_token"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_VEHICLE_TYPE, default=DEFAULT_VEHICLE_TYPE
                    ): vol.In(VEHICLE_TYPES),
                    vol.Required(CONF_REFRESH_TOKEN): str,
                }
            ),
            errors=errors if errors else None,
            description_placeholders={
                "token_help": "请在福特派 App 登录后通过抓包获取 refresh_token",
            },
        )

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
                        description={"suggested_value": scan_interval},
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
                }
            ),
        )
