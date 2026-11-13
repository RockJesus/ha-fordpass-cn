"""v6.6.7 回归：HA 2026.11 实体命名 + 云端 401/600 拒绝分类。

两个线上实测故障（2026-11-13 13:03-13:06）：

1. 后备箱解锁 button/press 报 ``'FordPassButton' object has no attribute
   '_attr_name'``。HA 2026.11 的 Entity 只保留 ``_attr_name: str | None``
   类型注解，没有类属性默认值，用 translation_key 命名的实体（lock_door /
   unlock_door / trunk_unlock 等）根本不存在该属性。旧实现在失败提示里直接
   读 ``self._attr_name`` → 抛 AttributeError，用户永远看不到真实失败原因。
2. 灯光寻车 switch 报 ``token refresh failed (401/600 after retry)``：
   refresh 端点其实刷新成功了（日志里没有任何刷新失败告警），随后重试仍被
   云端 401/600 拒——命令端点短时限流。旧实现把这种情况归为
   refresh_failed=True，会触发 ConfigEntryAuthFailed 逼用户重新登录，
   而凭据本身有效。
"""
from __future__ import annotations

import asyncio
import datetime
import importlib
import logging
import sys
import types
from pathlib import Path
from typing import Any

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "fordpass_cn"


def _mk(name: str, **attrs) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


class _FakeButtonEntity:
    """替代 ButtonEntity：与 HA 2026.11 一致，没有 _attr_name 默认值。"""

    _attr_should_poll = True
    _attr_state: str | None = None
    _attr_icon: str | None = None
    _attr_unique_id: str | None = None
    _attr_translation_key: str | None = None
    _attr_has_entity_name = False
    _attr_device_info: dict | None = None
    hass = None

    def async_write_ha_state(self) -> None:
        return None


class _HAError(Exception):
    """替代 HomeAssistantError。"""


def _install_button_fakes() -> None:
    if "fpnm.button" in sys.modules:
        return
    if "homeassistant" not in sys.modules:
        _mk("homeassistant")
    sys.modules["homeassistant"].__path__ = []  # type: ignore[attr-defined]

    def need(name: str, **attrs):
        if name not in sys.modules:
            _mk(name, **attrs)
            return sys.modules[name]
        module = sys.modules[name]
        for key, value in attrs.items():
            if not hasattr(module, key):
                setattr(module, key, value)
        return module

    need("homeassistant.components")
    need("homeassistant.components.button", ButtonEntity=_FakeButtonEntity)
    sys.modules["homeassistant.components"].button = sys.modules[
        "homeassistant.components.button"
    ]
    need("homeassistant.config_entries", ConfigEntry=object)
    need("homeassistant.core", HomeAssistant=object)
    need("homeassistant.exceptions", HomeAssistantError=_HAError)
    need("homeassistant.helpers")
    need("homeassistant.helpers.entity_registry")
    need("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
    need("homeassistant.util")
    _dt = need("homeassistant.util.dt")
    if not hasattr(_dt, "now"):
        _dt.now = lambda: datetime.datetime.now(
            datetime.timezone(datetime.timedelta(hours=8))
        )
    sys.modules["homeassistant.util"].dt = _dt

    pkg = types.ModuleType("fpnm")
    pkg.__path__ = [str(SRC)]  # type: ignore[attr-defined]
    sys.modules["fpnm"] = pkg
    _mk("fpnm.api", extract_remote_image_url=lambda *a, **k: None)
    _mk("fpnm.coordinator", FordPassCoordinator=object)
    # 全量跑时 homeassistant.* 桩由先执行的测试文件建立（need 只补缺不覆盖），
    # 这里补齐本文件依赖的两点：Entity 的 hass/写状态方法（真实 Entity 有，
    # 别的测试桩可能没有），否则失败路径记录结果时会 AttributeError。
    btn_entity = sys.modules["homeassistant.components.button"].ButtonEntity
    if not hasattr(btn_entity, "hass"):
        btn_entity.hass = None
    if not hasattr(btn_entity, "async_write_ha_state"):
        btn_entity.async_write_ha_state = lambda self: None
    sys.modules["fpnm.button"] = importlib.import_module("fpnm.button")


def _ha_error():
    """取运行时真正注册的 HomeAssistantError 类（可能由别的测试桩提供）。"""
    return sys.modules["homeassistant.exceptions"].HomeAssistantError


class _FakeCoordinator:
    uid_key = "VIN123"
    vin = "VIN123"
    device_info: dict = {}

    def __init__(self, raise_on_run: Exception | None = None) -> None:
        self._raise = raise_on_run

    async def run_command(self, command_type: str, wait: bool = True, cmd_spec=None) -> None:
        if self._raise:
            raise self._raise

    async def async_record_cmd_fail(self, command_type: str, exc) -> None:
        return None


def _button(key: str, label: str, command: str, coord=None):
    _install_button_fakes()
    mod = sys.modules["fpnm.button"]
    return mod.FordPassButton(coord or _FakeCoordinator(), key, label,
                              "mdi:car", command, None)


def test_translated_button_has_no_attr_name() -> None:
    """translation_key 命名的实体确实没有 _attr_name（HA 2026.11 事实）。"""
    btn = _button("trunk_unlock", "后备箱解锁", "TrunkUnlock")
    assert not hasattr(btn, "_attr_name")


def test_display_name_falls_back_to_label() -> None:
    btn = _button("trunk_unlock", "后备箱解锁", "TrunkUnlock")
    assert btn._display_name() == "后备箱解锁"


def test_display_name_uses_attr_name_when_present() -> None:
    btn = _button("honk_reset", "鸣笛设置恢复默认", "HonkReset")
    btn._attr_name = "鸣笛设置恢复默认"
    assert btn._display_name() == "鸣笛设置恢复默认"


def test_command_failure_message_is_not_attribute_error() -> None:
    """失败提示必须说清命令失败原因，而不是 AttributeError。"""
    exc = RuntimeError("cloud said no")
    btn = _button("trunk_unlock", "后备箱解锁", "TrunkUnlock",
                  _FakeCoordinator(raise_on_run=exc))
    with __import__("pytest").raises(_ha_error()) as info:
        asyncio.run(btn.async_press())
    msg = str(info.value)
    assert "后备箱解锁" in msg
    assert "'_attr_name'" not in msg


class _Rejected(Exception):
    """模拟 api 抛出的 gateway_rejected 错误。"""

    def __init__(self) -> None:
        super().__init__("token refresh failed (401/600 after retry)")
        self.code = 600
        self.gateway_rejected = True
        self.refresh_failed = False


def test_gateway_rejected_message_says_retry_later() -> None:
    btn = _button("trunk_unlock", "后备箱解锁", "TrunkUnlock",
                  _FakeCoordinator(raise_on_run=_Rejected()))
    with __import__("pytest").raises(_ha_error()) as info:
        asyncio.run(btn.async_press())
    msg = str(info.value)
    assert "暂时被云端拒绝" in msg
    assert "稍后重试" in msg
    # 不能误导成「该车不支持此命令 / 重新加载后不再创建」
    assert "不再创建" not in msg


# ---------- api 侧：401/600 重试后仍拒不得触发重新认证 ----------

def _ensure_aiohttp() -> None:
    try:
        import aiohttp  # noqa: F401
        return
    except ModuleNotFoundError:
        pass
    stub = types.ModuleType("aiohttp")
    stub.ClientTimeout = lambda *a, **k: None
    stub.ClientSession = lambda *a, **k: None
    sys.modules["aiohttp"] = stub


_ensure_aiohttp()


def _load_api():
    if "fpnmapi" not in sys.modules:
        pkg = types.ModuleType("fpnmapi")
        pkg.__path__ = [str(SRC)]  # type: ignore[attr-defined]
        sys.modules["fpnmapi"] = pkg
    return importlib.import_module("fpnmapi.api")


api_mod = _load_api()
FordPassApi = api_mod.FordPassApi
FordPassApiError = api_mod.FordPassApiError


class _FakeResp:
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self._body = body
        self.headers = {"Date": "Thu, 13 Nov 2026 05:10:00 GMT",
                        "Content-Type": "application/json"}

    async def text(self) -> str:
        return self._body

    async def read(self) -> bytes:
        return self._body.encode()


class _FakeSession:
    def __init__(self, responses: list[_FakeResp]) -> None:
        self._responses = responses

    def request(self, method: str, url: str, **kwargs: Any):
        resp = self._responses.pop(0)

        class _Ctx:
            async def __aenter__(self):
                return resp

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def _make_api(responses: list[_FakeResp]) -> FordPassApi:
    api = FordPassApi.__new__(FordPassApi)
    api._log = logging.getLogger("test_ha2026_naming_reject")
    api._clock_compensation = 0.0
    api._clock_warned_at = 0.0
    api._clock_reject_warned_at = 0.0
    api.clock_offset_seconds = None
    api._access_token = "tok"
    api._refresh_token = "rt"
    api._retrying = False
    api.on_token_refresh = None
    api._refresh_backoff_until = 0.0
    api._refresh_backoff_err = ""
    api._app_version = "9.9.9"
    api._client_type = "FP"
    api._session = _FakeSession(responses)
    return api


def test_gateway_reject_after_refresh_does_not_trigger_reauth(monkeypatch) -> None:
    """刷新成功、重试仍 600 → gateway_rejected，不置 refresh_failed。"""
    monkeypatch.setattr(api_mod, "compute_sign", lambda params: "sig")

    async def _refresh(_rt):
        return {"access_token": "new-tok"}

    api = _make_api([
        _FakeResp(200, '{"status": 600, "error": "rate limited"}'),
        _FakeResp(200, '{"status": 600, "error": "rate limited"}'),
    ])
    api.refresh_token = _refresh
    with __import__("pytest").raises(FordPassApiError) as info:
        asyncio.run(api._request("POST", "/api/send-command", body={"vin": "VIN123"}))
    err = info.value
    assert err.code == 600
    assert err.refresh_failed is False
    assert err.gateway_rejected is True


def test_real_auth_failure_still_triggers_reauth(monkeypatch) -> None:
    """刷新本身失败（401/600）才置 refresh_failed，保留重新认证入口。"""
    monkeypatch.setattr(api_mod, "compute_sign", lambda params: "sig")

    async def _refresh(_rt):
        raise FordPassApiError(600, "device locked")

    api = _make_api([_FakeResp(200, '{"status": 401, "error": "jwt expired"}')])
    api.refresh_token = _refresh
    with __import__("pytest").raises(FordPassApiError) as info:
        asyncio.run(api._request("POST", "/api/send-command", body={"vin": "VIN123"}))
    err = info.value
    assert err.code == 401
    assert err.refresh_failed is True
