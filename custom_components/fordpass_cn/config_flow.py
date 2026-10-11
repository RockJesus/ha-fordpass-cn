"""Config flow for the FordPass China integration.

Two login methods (selectable in step 1, v2.7.4):

  * SMS  — phone number + 6-digit SMS passcode (two steps);
  * B2C  — username (phone) + password via the official Azure AD B2C flow
           (authorize -> SelfAsserted -> confirmed(code) -> token exchange).

The password is used once during setup and is never stored in the entry
data (only the access/refresh JWTs are persisted, as with the SMS login).
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import FordPassApi, FordPassApiError
from .const import (
    CONF_COORDINATE_SYSTEM,
    CONF_LOGIN_MODE,
    CONF_PASSWORD,
    CONF_PHONE,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    COORDINATE_GCJ02,
    COORDINATE_WGS84,
    DEFAULT_COORDINATE_SYSTEM,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    LOGIN_MODE_PASSWORD,
    LOGIN_MODE_SMS,
)

_LOGGER = logging.getLogger(__name__)

LOGIN_MODE_LABELS = {
    LOGIN_MODE_SMS: "手机号验证码登录",
    LOGIN_MODE_PASSWORD: "用户名密码登录",
}

COORDINATE_LABELS = {
    COORDINATE_WGS84: "WGS-84（官方地图/OSM 精确）",
    COORDINATE_GCJ02: "GCJ-02（高德/腾讯地图）",
}

# 集成偏好表单（状态刷新间隔 / 位置跟踪 / 坐标系）——OptionsFlow 与
# reconfigure 共用，避免两份 schema 漂移。
def _preference_schema(defaults: dict) -> vol.Schema:
    return vol.Schema(
        {
            # v2.7.8: scan interval is entered in MINUTES (default 30).
            vol.Required(
                CONF_SCAN_INTERVAL,
                default=defaults.get(
                    CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES
                ),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=1440)),
            vol.Optional(
                "track_location", default=defaults.get("track_location", True)
            ): bool,
            vol.Optional(
                CONF_COORDINATE_SYSTEM,
                default=defaults.get(
                    CONF_COORDINATE_SYSTEM, DEFAULT_COORDINATE_SYSTEM
                ),
            ): vol.In(COORDINATE_LABELS),
        }
    )

STEP_LOGIN_MODE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_LOGIN_MODE, default=LOGIN_MODE_SMS): vol.In(
            LOGIN_MODE_LABELS
        ),
    }
)
STEP_PHONE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PHONE): str,
    }
)
STEP_CODE_SCHEMA = vol.Schema(
    {
        vol.Required("passcode"): str,
    }
)
STEP_PASSWORD_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


def _normalize_phone(raw: str) -> str:
    """Strip whitespace and an optional +86 / 86 country prefix."""
    phone = raw.strip()
    if phone.startswith("+"):
        phone = phone[1:]
    if phone.startswith("86") and len(phone) == 13:
        phone = phone[2:]
    return phone


class FordPassConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._phone: str | None = None
        self._xjw: str | None = None
        self._username: str | None = None
        # v3.9.8: reauth（token 过期重新认证）标记——登录流程最终动作
        # 由"创建 entry"改为"更新已有 entry 的 token + abort"。
        self._reauth: bool = False

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            mode = user_input[CONF_LOGIN_MODE]
            if mode == LOGIN_MODE_PASSWORD:
                return self.async_show_form(
                    step_id="password",
                    data_schema=STEP_PASSWORD_SCHEMA,
                    description_placeholders={"login_mode": LOGIN_MODE_LABELS[LOGIN_MODE_PASSWORD]},
                )
            return self.async_show_form(
                step_id="phone",
                data_schema=STEP_PHONE_SCHEMA,
                description_placeholders={"login_mode": LOGIN_MODE_LABELS[LOGIN_MODE_SMS]},
            )
        return self.async_show_form(
            step_id="user",
            data_schema=STEP_LOGIN_MODE_SCHEMA,
            errors=errors,
            description_placeholders={"login_modes": "、".join(LOGIN_MODE_LABELS.values())},
        )

    async def async_step_reauth(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """token 过期后的重新认证（v3.9.8）。

        __init__.py 检测到 401 时触发 SOURCE_REAUTH flow；复用登录方式
        选择 → 手机验证码/密码登录，成功后更新已有 entry 的 token 并
        abort（不重建 entry，实体保留）。
        """
        self._reauth = True
        return await self.async_step_user(user_input)

    def _finish_login(self, mode: str, tokens: dict) -> FlowResult:
        """登录成功后的收尾（v3.9.8）：reauth 更新 entry；否则创建 entry。"""
        if self._reauth:
            entry = self._get_reauth_entry()
            self.hass.config_entries.async_update_entry(
                entry,
                data={
                    **entry.data,
                    CONF_LOGIN_MODE: mode,
                    CONF_PHONE: self._phone,
                    CONF_USERNAME: self._username,
                    "access_token": tokens["access_token"],
                    "refresh_token": tokens["refresh_token"],
                },
            )
            return self.async_abort(reason="reauth_successful")
        return self.async_create_entry(
            title=(self._phone or self._username) or DOMAIN,
            data={
                CONF_LOGIN_MODE: mode,
                CONF_PHONE: self._phone,
                CONF_USERNAME: self._username,
                "access_token": tokens["access_token"],
                "refresh_token": tokens["refresh_token"],
            },
            options={
                CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL_MINUTES,
                "track_location": True,
                CONF_COORDINATE_SYSTEM: DEFAULT_COORDINATE_SYSTEM,
            },
        )

    async def async_step_phone(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._phone = _normalize_phone(user_input[CONF_PHONE])
            if not self._phone.isdigit() or len(self._phone) != 11:
                errors["base"] = "bad_phone"
            else:
                if not self._reauth:
                    await self.async_set_unique_id(self._phone)
                    self._abort_if_unique_id_configured()
                session = async_get_clientsession(self.hass)
                api = FordPassApi(session, _LOGGER)
                try:
                    self._xjw = await api.generate_passcode(self._phone)
                except ImportError as err:
                    errors["base"] = "missing_deps"
                    _LOGGER.exception("dependency import failed: %s", err)
                except FordPassApiError as err:
                    errors["base"] = "passcode_failed"
                    _LOGGER.warning(
                        "send passcode failed: code=%s msg=%s", err.code, err.message
                    )
                except Exception as err:  # noqa: BLE001
                    errors["base"] = "unknown"
                    _LOGGER.exception("unexpected error sending passcode: %s", err)
                else:
                    return self.async_show_form(
                        step_id="code",
                        data_schema=STEP_CODE_SCHEMA,
                        description_placeholders={"phone": self._phone},
                    )
        return self.async_show_form(
            step_id="phone",
            data_schema=STEP_PHONE_SCHEMA,
            errors=errors,
            description_placeholders={"login_mode": LOGIN_MODE_LABELS[LOGIN_MODE_SMS]},
        )

    async def async_step_code(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            api = FordPassApi(session, _LOGGER)
            try:
                tokens = await api.passcode_login(
                    self._phone or "",
                    user_input["passcode"].strip(),
                    self._xjw,
                )
            except ImportError as err:
                errors["base"] = "missing_deps"
                _LOGGER.exception("dependency import failed: %s", err)
            except FordPassApiError as err:
                errors["base"] = "login_failed"
                _LOGGER.warning(
                    "login failed: code=%s msg=%s", err.code, err.message
                )
            except Exception as err:  # noqa: BLE001
                errors["base"] = "unknown"
                _LOGGER.exception("unexpected error during login: %s", err)
            else:
                return self._finish_login(LOGIN_MODE_SMS, tokens)
        return self.async_show_form(
            step_id="code",
            data_schema=STEP_CODE_SCHEMA,
            errors=errors,
            description_placeholders={"phone": self._phone or ""},
        )

    async def async_step_password(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._username = _normalize_phone(user_input[CONF_USERNAME])
            if not self._username.isdigit() or len(self._username) != 11:
                errors["base"] = "bad_username"
            else:
                if not self._reauth:
                    await self.async_set_unique_id(self._username)
                    self._abort_if_unique_id_configured()
                session = async_get_clientsession(self.hass)
                api = FordPassApi(session, _LOGGER)
                try:
                    tokens = await api.password_login(
                        self._username, user_input[CONF_PASSWORD]
                    )
                except ImportError as err:
                    errors["base"] = "missing_deps"
                    _LOGGER.exception("dependency import failed: %s", err)
                except FordPassApiError as err:
                    errors["base"] = "password_login_failed"
                    _LOGGER.warning(
                        "password login failed: code=%s msg=%s", err.code, err.message
                    )
                except Exception as err:  # noqa: BLE001
                    errors["base"] = "unknown"
                    _LOGGER.exception("unexpected error during password login: %s", err)
                else:
                    # The password itself is never persisted — only the
                    # exchanged JWTs, exactly like the SMS login.
                    return self._finish_login(LOGIN_MODE_PASSWORD, tokens)
        return self.async_show_form(
            step_id="password",
            data_schema=STEP_PASSWORD_SCHEMA,
            errors=errors,
            description_placeholders={"login_mode": LOGIN_MODE_LABELS[LOGIN_MODE_PASSWORD]},
        )

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return FordPassOptionsFlow(config_entry)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """处理 HA 前端"重新配置"入口（v3.9.7 修复）。

        根因：HA 2026.x 前端在集成卡片点"配置/选项"默认启动
        reconfigure flow，而本 flow 此前只实现了 OptionsFlow，未实现
        async_step_reconfigure → 保存返回 400 "Handler FordPassConfigFlow
        doesn't support step reconfigure" → 用户设置的任何选项（如状态
        刷新间隔）都无法写入 entry.options（实测 entry.options 恒为空）。

        这里复用偏好表单，提交后写入 entry.options（与 async_setup_entry /
        async_update_options 读取的字段一致），并触发 update_listener
        （async_update_options）即时应用，无需重启。
        """
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            self.hass.config_entries.async_update_entry(
                entry,
                options=dict(user_input),
            )
            return self.async_abort(reason="reconfigure_successful")
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_preference_schema(entry.options),
        )


class FordPassOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=_preference_schema(self._config_entry.options),
        )
