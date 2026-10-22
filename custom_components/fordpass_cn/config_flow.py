"""Config flow for the FordPass China integration.

Two login methods (selectable in step 1, v2.7.4):

  * SMS  — phone number + 6-digit SMS passcode (two steps);
  * B2C  — username (phone) + password via the official Azure AD B2C flow
           (authorize -> SelfAsserted -> confirmed(code) -> token exchange).

v4.4.6: 2026-10-03 起 login.ford.com.cn 前置腾讯云 EdgeOne WAF 拦截纯
HTTP 客户端（567）——requests 四步流不可用。密码登录改为两步：
① 输入用户名/密码（密码按用户要求持久化到 entry.data，仅存本地配置）；
② 用真实浏览器完成 B2C 登录，把地址栏的 authorization code 粘贴进
config flow——集成用 code 直接换 DLT token（api.password_login(code=...)），
绕开 WAF 而无需任何绕过手段。code 一次性使用，换取后即失效。
"""
from __future__ import annotations

import logging
from typing import Any

try:
    import probatio as vol
except ImportError:  # HA < 2026.10（data_entry_flow 未迁移到 probatio）
    import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import FordPassApi, FordPassApiError
from .const import (
    APPLICATION_ID,
    B2C_AUTHORITY,
    B2C_CLIENT_ID,
    B2C_PATH_AUTHORIZE,
    B2C_REDIRECT_URI,
    BRAND_FORD,
    BRAND_LABELS,
    BRAND_PROFILE,
    CONF_BRAND,
    CONF_COORDINATE_SYSTEM,
    CONF_LOGIN_MODE,
    CONF_PASSWORD,
    CONF_PHONE,
    CONF_USERNAME,
    COORDINATE_GCJ02,
    COORDINATE_WGS84,
    DEFAULT_BRAND,
    DEFAULT_COORDINATE_SYSTEM,
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

# 集成偏好表单（位置跟踪 / 坐标系；v4.9.6 起无刷新间隔）——OptionsFlow 与
# reconfigure 共用，避免两份 schema 漂移。
def _preference_schema(defaults: dict) -> vol.Schema:
    return vol.Schema(
        {
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

STEP_BRAND_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_BRAND, default=BRAND_FORD): vol.In(
            BRAND_LABELS
        ),
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
# v4.4.6: B2C 浏览器辅助登录——粘贴 authorization code（真实浏览器登录后
# 从地址栏 redirect_uri?code=xxx 复制）。code 一次性、换 token 后即失效。
STEP_B2C_CODE_SCHEMA = vol.Schema(
    {
        vol.Required("code"): str,
    }
)


def _build_b2c_login_url() -> str:
    """构造 B2C authorize 登录地址（与 api._b2c_login_sync 参数一致）。

    v4.4.6: 供用户在真实浏览器打开完成登录；redirect 后地址栏
    redirect_uri?code=xxx 中的 xxx 即 authorization code。
    """
    from urllib.parse import urlencode

    query = {
        "client_id": B2C_CLIENT_ID,
        "nonce": "defaultNonce",
        "redirect_uri": B2C_REDIRECT_URI,
        "scope": "openid",
        "response_type": "code",
        "prompt": "login",
        "country_code": "CHN",
        "ford_application_id": APPLICATION_ID,
        "language_code": "zh-CN",
        "tnc_accepted": "true",
        "tnc_consent1_accepted": "true",
        "tnc_consent2_accepted": "false",
    }
    return B2C_AUTHORITY + B2C_PATH_AUTHORIZE + "?" + urlencode(query)


def _normalize_phone(raw: str) -> str:
    """Strip whitespace and an optional +86 / 86 country prefix."""
    phone = raw.strip()
    phone = phone.removeprefix("+")
    if phone.startswith("86") and len(phone) == 13:
        phone = phone[2:]
    return phone


class FordPassConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    # v4.7.10: HA 2026.x 前端"重新配置"入口需 supports_reconfigure=True
    # 才显示（async_step_reconfigure v3.9.7 已实现但缺此开关）；补上后
    # 集成卡片"重新配置"可用，与 OptionsFlow 并存。
    supports_reconfigure = True

    def __init__(self) -> None:
        self._phone: str | None = None
        self._xjw: str | None = None
        self._username: str | None = None
        # v4.4.6: B2C 密码登录——密码暂存 flow 内存，完成时写入 entry.data
        self._password: str | None = None
        # v3.9.8: reauth（token 过期重新认证）标记——登录流程最终动作
        # 由"创建 entry"改为"更新已有 entry 的 token + abort"。
        self._reauth: bool = False
        self._reconfigure: bool = False  # v5.4.0: reconfigure 标志（重配核心配置，区别于 reauth）
        # v4.7.5: 品牌（福特派/林肯之道）——登录第一步选择，默认福特派；
        # reauth/_finish_login 均读取该值，必须在此初始化
        self._brand: str = DEFAULT_BRAND

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """v4.7.5: 登录第一步——选择品牌（福特派/林肯之道）。"""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._brand = user_input[CONF_BRAND]
            profile = BRAND_PROFILE.get(self._brand, {})
            if not profile.get("ready"):
                # 林肯之道登录链路待抓包（B2C tenant/验证码端点/App 指纹无
                # 逆向数据，不编造）——提示页，可返回重选品牌
                return self.async_show_form(
                    step_id="lincoln_pending",
                    data_schema=vol.Schema({}),
                    description_placeholders={"brand": BRAND_LABELS.get(self._brand, self._brand)},
                )
            return await self.async_step_login_mode()
        return self.async_show_form(
            step_id="user",
            data_schema=STEP_BRAND_SCHEMA,
            errors=errors,
            description_placeholders={"brands": "、".join(BRAND_LABELS.values())},
        )

    async def async_step_login_mode(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """v4.7.5: 登录方式选择（原 async_step_user 逻辑，品牌选定后进入）。"""
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
            step_id="login_mode",
            data_schema=STEP_LOGIN_MODE_SCHEMA,
            errors=errors,
            description_placeholders={"login_modes": "、".join(LOGIN_MODE_LABELS.values())},
        )

    async def async_step_lincoln_pending(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """v4.7.5: 林肯之道登录链路未就绪提示页——提交后返回重选品牌。"""
        if user_input is not None:
            return await self.async_step_user()
        return self.async_show_form(
            step_id="lincoln_pending",
            data_schema=vol.Schema({}),
            description_placeholders={"brand": BRAND_LABELS.get(self._brand, self._brand)},
        )

    async def async_step_reauth(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """token 过期后的重新认证（v3.9.8）。

        __init__.py 检测到 401 时触发 SOURCE_REAUTH flow；先显示确认
        对话框（reauth_confirm，v4.4.9 官方规范）→ 复用登录方式选择 →
        手机验证码/密码登录，成功后更新已有 entry 的 token 并 abort
        （不重建 entry，实体保留）。
        """
        self._reauth = True
        return await self.async_step_reauth_confirm(user_input)

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """v4.4.9: reauth 确认对话框（官方 reauth 指南要求）。"""
        if user_input is not None:
            return await self.async_step_login_mode(user_input)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({}),
        )

    def _finish_login(
        self, mode: str, tokens: dict, extra_data: dict[str, Any] | None = None
    ) -> FlowResult:
        """登录成功后的收尾（v3.9.8）：reauth 更新 entry；否则创建 entry。

        v4.0.11: reauth 分支先校验登录账号与原 entry 手机号一致——换账号
        reauth 会把新账号 token 写进旧账号 entry，导致实体绑旧 VIN、token
        属新账号的错配；不一致直接 abort（提示需删除重配）。

        v4.4.6: extra_data 合并进 entry.data（如 B2C 密码登录的密码，
        按用户要求持久化到本地配置，供浏览器辅助重登录使用）。
        """
        if self._reconfigure:
            # v5.4.0: reconfigure = 重配核心配置（换手机号/登录方式/账号），
            # 更新 entry.data，**不校验手机号一致**（换账号是合法场景，
            # 区别于 reauth 的"必须同手机号"）；options 偏好原样保留。
            entry = self._reconfigure_entry
            self.hass.config_entries.async_update_entry(
                entry,
                data={
                    **entry.data,
                    **(extra_data or {}),
                    CONF_LOGIN_MODE: mode,
                    CONF_PHONE: self._phone,
                    CONF_USERNAME: self._username,
                    "access_token": tokens["access_token"],
                    "refresh_token": tokens["refresh_token"],
                },
            )
            return self.async_abort(reason="reconfigure_successful")
        if self._reauth:
            entry = self._get_reauth_entry()
            old_phone = entry.data.get(CONF_PHONE)
            if old_phone and self._phone:
                try:
                    same = _normalize_phone(self._phone) == _normalize_phone(old_phone)
                except Exception:  # noqa: BLE001 - 归一化失败则保守比较原文
                    same = self._phone == old_phone
                if not same:
                    _LOGGER.error(
                        "FordPass reauth phone mismatch: %s != %s",
                        self._phone, old_phone,
                    )
                    return self.async_abort(reason="reauth_phone_mismatch")
            self.hass.config_entries.async_update_entry(
                entry,
                data={
                    **entry.data,
                    **(extra_data or {}),
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
                **(extra_data or {}),
                CONF_LOGIN_MODE: mode,
                CONF_BRAND: self._brand,
                CONF_PHONE: self._phone,
                CONF_USERNAME: self._username,
                "access_token": tokens["access_token"],
                "refresh_token": tokens["refresh_token"],
            },
            options={
                "track_location": True,
                CONF_COORDINATE_SYSTEM: DEFAULT_COORDINATE_SYSTEM,
            },
        )

    def _get_reauth_entry(self):
        """v4.0.11: 解析 reauth 对应的原 config entry。

        此前该方法不存在（AttributeError），reauth 走到 _finish_login 必然
        崩溃；entry_id 经 __init__.py async_init(data={"entry_id": ...})
        传入，flow context 或 init_data 兜底读取。
        """
        entry_id = self.context.get("entry_id") or (self.init_data or {}).get("entry_id")
        if not entry_id:
            raise ValueError("reauth: missing entry_id in flow context")
        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            raise ValueError(f"reauth: entry {entry_id} not found")
        return entry

    def _get_reconfigure_entry(self):
        """v5.4.0: 解析 reconfigure 对应的原 config entry。

        HA 2026.x 基类已提供同名官方方法，此处显式实现以兼容低版本
        HA（基类无该方法时 AttributeError，用户点"重新配置"直接 500）；
        entry_id 经 flow context 读取（reconfigure 由 HA 核心以
        entry_id 启动），兜底 init_data。
        """
        entry_id = self.context.get("entry_id") or (self.init_data or {}).get("entry_id")
        if not entry_id:
            raise ValueError("reconfigure: missing entry_id in flow context")
        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            raise ValueError(f"reconfigure: entry {entry_id} not found")
        return entry

    async def async_step_phone(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._phone = _normalize_phone(user_input[CONF_PHONE])
            if not self._phone.isdigit() or len(self._phone) != 11:
                errors["base"] = "bad_phone"
            else:
                if not (self._reauth or self._reconfigure):
                    await self.async_set_unique_id(self._phone)
                    self._abort_if_unique_id_configured()
                session = async_get_clientsession(self.hass)
                api = FordPassApi(session, _LOGGER)
                try:
                    self._xjw = await api.generate_passcode(self._phone)
                except ImportError:
                    errors["base"] = "missing_deps"
                    _LOGGER.exception("dependency import failed")
                except FordPassApiError as err:
                    errors["base"] = "passcode_failed"
                    _LOGGER.warning(
                        "send passcode failed: code=%s msg=%s", err.code, err.message
                    )
                except Exception:
                    errors["base"] = "unknown"
                    _LOGGER.exception("unexpected error sending passcode")
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
            except ImportError:
                errors["base"] = "missing_deps"
                _LOGGER.exception("dependency import failed")
            except FordPassApiError as err:
                errors["base"] = "login_failed"
                _LOGGER.warning(
                    "login failed: code=%s msg=%s", err.code, err.message
                )
            except Exception:
                errors["base"] = "unknown"
                _LOGGER.exception("unexpected error during login")
            else:
                return self._finish_login(LOGIN_MODE_SMS, tokens)
        return self.async_show_form(
            step_id="code",
            data_schema=STEP_CODE_SCHEMA,
            errors=errors,
            description_placeholders={"phone": self._phone or ""},
        )

    async def async_step_password(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """B2C 密码登录第一步：用户名 + 密码（v4.4.6）。

        EdgeOne WAF 拦截纯 HTTP 客户端后不再直接跑 requests 四步流——
        密码仅暂存于 flow 内存，跳转到第二步粘贴浏览器登录的 code。
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            self._username = _normalize_phone(user_input[CONF_USERNAME])
            if not self._username.isdigit() or len(self._username) != 11:
                errors["base"] = "bad_username"
            else:
                if not (self._reauth or self._reconfigure):
                    await self.async_set_unique_id(self._username)
                    self._abort_if_unique_id_configured()
                self._password = user_input[CONF_PASSWORD]
                return await self.async_step_b2c()
        return self.async_show_form(
            step_id="password",
            data_schema=STEP_PASSWORD_SCHEMA,
            errors=errors,
            description_placeholders={"login_mode": LOGIN_MODE_LABELS[LOGIN_MODE_PASSWORD]},
        )

    async def async_step_b2c(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """B2C 密码登录第二步：粘贴浏览器登录得到的 authorization code。

        v4.4.6: 用户在真实浏览器（App 同款 UA 环境）打开下方登录地址完成
        登录，浏览器会 302 跳到 redirect_uri 并带 ?code=xxx —— 把 xxx 粘贴
        到本步。集成用 code 直接换 DLT token（api.password_login(code=...)），
        纯 API 通道绕开 EdgeOne WAF 对 HTTP 客户端的拦截，无需任何绕过手段。
        code 一次性有效，换取 token 后即失效。
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            code = (user_input.get("code") or "").strip()
            if not code:
                errors["base"] = "bad_code"
            else:
                session = async_get_clientsession(self.hass)
                api = FordPassApi(session, _LOGGER)
                try:
                    tokens = await api.password_login(
                        self._username or "", code=code
                    )
                except ImportError:
                    errors["base"] = "missing_deps"
                    _LOGGER.exception("dependency import failed")
                except FordPassApiError as err:
                    errors["base"] = "bad_code"
                    _LOGGER.warning(
                        "b2c code exchange failed: code=%s msg=%s", err.code, err.message
                    )
                except Exception:
                    errors["base"] = "unknown"
                    _LOGGER.exception("unexpected error during b2c code exchange")
                else:
                    # v4.4.6: 密码按用户要求持久化到 entry.data（仅本地配置），
                    # 供浏览器辅助重登录/自动化使用；token 与短信登录一致持久化。
                    return self._finish_login(
                        LOGIN_MODE_PASSWORD,
                        tokens,
                        extra_data={CONF_PASSWORD: self._password or ""},
                    )
        login_url = _build_b2c_login_url()
        return self.async_show_form(
            step_id="b2c",
            data_schema=STEP_B2C_CODE_SCHEMA,
            errors=errors,
            description_placeholders={
                "login_url": login_url,
                "username": self._username or "",
            },
        )

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        # v5.4.1: OptionsFlow 无参构造（HA 2026.10 基类 __init__ 不接收参数，
        # config_entry 为只读 property，async_step_* 内可用）
        return FordPassOptionsFlow()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """处理 HA 前端"重新配置"入口（v3.9.7 修复，v5.4.0 语义整改）。

        HA 官方语义：reconfigure 重配 entry 的**核心配置**（手机号/登录
        方式/凭据，写入 entry.data），options flow 才负责偏好（扫描间隔等
        entry.options）。此前 reconfigure 复用偏好表单写 entry.options，
        与"集成选项"完全同构——用户无法在此改手机号（换账号只能删了
        重加），语义错位（quality_scale/审核视角不合格）。

        v5.4.0：reconfigure 复用登录链路（品牌→登录方式→验证码/密码→
        _finish_login），登录成功后更新 entry.data（**允许更换手机号**，
        区别于 reauth 的"必须同手机号"）；entry.options 偏好原样保留；
        setup 重载后按新账号车辆重建实体（旧 VIN 实体由 setup 清理）。
        """
        entry = self._get_reconfigure_entry()
        # v4.5.0: entry.unique_id 为 None（历史 entry）时跳过 unique_id
        # 校验——async_set_unique_id(None or "") 会设空字符串，
        # _abort_if_unique_id_mismatch 立即 abort，用户 UI 保存永远失败。
        # 有 unique_id 才按官方规范校验对应原账号。
        if entry.unique_id:
            await self.async_set_unique_id(entry.unique_id)
            self._abort_if_unique_id_mismatch()
        self._reconfigure = True
        self._reconfigure_entry = entry
        return await self.async_step_login_mode(user_input)


class FordPassOptionsFlow(config_entries.OptionsFlow):
    # v5.4.1: 无参构造（HA 2026.10 OptionsFlow 基类 __init__ 不接收参数，
    # config_entry 为只读 property：hass.config_entries.async_get_known_entry
    # (self._config_entry_id)，__init__ 阶段不可用、async_step_* 内可用）

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=_preference_schema(self.config_entry.options),
        )
