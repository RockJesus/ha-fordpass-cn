"""FordPass China 配置流程。"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_SCAN_INTERVAL, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .const import (
    CONF_AUTH_TYPE,
    CONF_DLT_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_VEHICLE_TYPE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .ford.fordpass import FordPass

_LOGGER = logging.getLogger(__name__)

VEHICLE_TYPES = {
    "ford": "福特派",
    "lincoln": "林肯之道",
}

AUTH_TYPES = {
    "dlt": "DLT 访问令牌（2026 新认证，推荐）",
    "refresh_token": "使用 refresh_token（旧认证，兼容）",
    "account": "使用账户密码（SSO 已迁移，通常不可用）",
}


async def _validate_dlt(hass: HomeAssistant, vehicle_type: str, dlt_token: str) -> tuple[bool, str | None]:
    """用抓包到的 DLT 访问令牌直接读取用户信息，验证令牌可用。返回 (ok, userId)。"""
    session = async_create_clientsession(hass)
    fordpass = FordPass(session=session, dlt_token=dlt_token, vehicle_type=vehicle_type)
    info = await fordpass.get_user_info()
    user_id = info.get("userId") if info else None
    return (user_id is not None), user_id


async def _validate_token(hass: HomeAssistant, vehicle_type: str, refresh_token: str) -> tuple[bool, str | None]:
    """用 refresh_token 尝试换取 token，并读取用户信息。返回 (ok, userId)。"""
    session = async_create_clientsession(hass)
    fordpass = FordPass(session=session, refresh_token=refresh_token, vehicle_type=vehicle_type)
    if not await fordpass.refresh_token():
        return False, None
    info = await fordpass.get_user_info()
    user_id = info.get("userId") if info else None
    return True, user_id


async def _validate_account(hass: HomeAssistant, vehicle_type: str, username: str, password: str) -> bool:
    session = async_create_clientsession(hass)
    fordpass = FordPass(session=session, username=username, password=password, vehicle_type=vehicle_type)
    return await fordpass.auth()


class FordPassChinaConfigFlow(ConfigFlow, domain=DOMAIN):
    """FordPass China 配置流程。"""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> dict:
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_AUTH_TYPE] == "account":
                return await self.async_step_account()
            if user_input[CONF_AUTH_TYPE] == "dlt":
                return await self.async_step_dlt()
            return await self.async_step_token()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_AUTH_TYPE, default="dlt"): vol.In(AUTH_TYPES),
                }
            ),
            errors=errors,
            description_placeholders={
                "hint": "2026 新认证：从福特派 App 的 cnapi.cv.ford.com.cn 请求头里抓取 auth-token（即 DLT 访问令牌）"
            },
        )

    async def async_step_dlt(self, user_input: dict[str, Any] | None = None) -> dict:
        errors: dict[str, str] = {}
        if user_input is not None:
            vehicle_type = user_input[CONF_VEHICLE_TYPE]
            dlt_token = user_input[CONF_DLT_TOKEN].strip()
            ok, user_id = await _validate_dlt(self.hass, vehicle_type, dlt_token)
            if not ok:
                errors["base"] = "cant_auth"
            else:
                title = f"{user_id or dlt_token[-6:]}@{VEHICLE_TYPES[vehicle_type]}"
                for entry in self._async_current_entries():
                    if entry.title == title:
                        errors["base"] = "account_exist"
                        break
                if not errors:
                    user_input[CONF_AUTH_TYPE] = "dlt"
                    return self.async_create_entry(title=title, data=user_input)
        return self.async_show_form(
            step_id="dlt",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_VEHICLE_TYPE, default="ford"): vol.In(VEHICLE_TYPES),
                    vol.Required(CONF_DLT_TOKEN): str,
                }
            ),
            errors=errors,
            description_placeholders={"hint": "从福特派 App 请求 cnapi.cv.ford.com.cn 的请求头 auth-token 复制（见 README）"},
        )

    async def async_step_token(self, user_input: dict[str, Any] | None = None) -> dict:
        errors: dict[str, str] = {}
        if user_input is not None:
            vehicle_type = user_input[CONF_VEHICLE_TYPE]
            refresh_token = user_input[CONF_REFRESH_TOKEN].strip()
            ok, user_id = await _validate_token(self.hass, vehicle_type, refresh_token)
            if not ok:
                errors["base"] = "cant_auth"
            else:
                title = f"{user_id or refresh_token[-6:]}@{VEHICLE_TYPES[vehicle_type]}"
                for entry in self._async_current_entries():
                    if entry.title == title:
                        errors["base"] = "account_exist"
                        break
                if not errors:
                    return self.async_create_entry(title=title, data=user_input)
        return self.async_show_form(
            step_id="token",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_VEHICLE_TYPE, default="ford"): vol.In(VEHICLE_TYPES),
                    vol.Required(CONF_REFRESH_TOKEN): str,
                }
            ),
            errors=errors,
            description_placeholders={"hint": "从福特派 App 抓包获得（见 README 说明）"},
        )

    async def async_step_account(self, user_input: dict[str, Any] | None = None) -> dict:
        errors: dict[str, str] = {}
        if user_input is not None:
            vehicle_type = user_input[CONF_VEHICLE_TYPE]
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]
            ok = await _validate_account(self.hass, vehicle_type, username, password)
            if not ok:
                errors["base"] = "cant_login"
            else:
                title = f"{username}@{VEHICLE_TYPES[vehicle_type]}"
                for entry in self._async_current_entries():
                    if entry.title == title:
                        errors["base"] = "account_exist"
                        break
                if not errors:
                    return self.async_create_entry(title=title, data=user_input)
        return self.async_show_form(
            step_id="account",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_VEHICLE_TYPE, default="ford"): vol.In(VEHICLE_TYPES),
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return FordPassChinaOptionsFlow(config_entry)


class FordPassChinaOptionsFlow(OptionsFlow):
    """选项：轮询间隔。"""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> dict:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        scan_interval = self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_SCAN_INTERVAL, default=scan_interval): vol.All(
                        vol.Coerce(int), vol.Range(min=1, max=1440)
                    )
                }
            ),
        )
