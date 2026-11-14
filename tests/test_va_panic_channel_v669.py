"""v6.6.9 回归测试：声光寻车优先走 App 的独立 panic 命令，不支持时回落 VA。

背景：App 6.18.0 的声光寻车是 FordPanicCommand（PUT /api/vehicles/v5/{vin}/
panic/{duration}），带独立的 FordPanicCancelCommand（DELETE 同路径）；集成
此前只用 InitialVA VAType=4。择优策略：首次先试 panic，云端以「不支持该
通道」（400/404/405）拒绝时回落 VA 并记住结论，本次运行内不再试探。
"""
from __future__ import annotations

import asyncio
import datetime
import sys
import types
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "fordpass_cn"


class _FakeSwitchEntity:
    _attr_should_poll = False
    _attr_has_entity_name = False
    _attr_name: str | None = None
    _attr_unique_id: str | None = None
    _attr_icon = None
    _attr_device_info = None
    hass = None

    def async_write_ha_state(self) -> None:
        return None


class _FakeRestoreMixin:
    def _restore_bool(self, default: bool) -> bool:
        return default


class _FakeApiError(Exception):
    """替代 api.FordPassApiError：带 status，供通道判定使用。"""

    def __init__(self, status: int, msg: str = "") -> None:
        super().__init__(f"HTTP {status}: {msg}")
        self.status = status


def _mk(name: str, **attrs) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


def _need(name: str, **attrs):
    if name not in sys.modules:
        return _mk(name, **attrs)
    module = sys.modules[name]
    for key, value in attrs.items():
        if not hasattr(module, key):
            setattr(module, key, value)
    return module


def _install_fakes() -> None:
    if "fp669.switch" in sys.modules:
        return
    _need("homeassistant")
    sys.modules["homeassistant"].__path__ = []  # type: ignore[attr-defined]
    _need("homeassistant.components")
    sw = _need("homeassistant.components.switch", SwitchEntity=_FakeSwitchEntity)
    sys.modules["homeassistant.components"].switch = sw
    _need("homeassistant.config_entries", ConfigEntry=object)
    _need("homeassistant.core", HomeAssistant=object)
    _need("homeassistant.exceptions", HomeAssistantError=type("HomeAssistantError", (Exception,), {}))
    _need("homeassistant.helpers")
    _need("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
    _need("homeassistant.util")
    _dt = _need("homeassistant.util.dt")
    if not hasattr(_dt, "now"):
        _dt.now = lambda: datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    sys.modules["homeassistant.util"].dt = _dt

    pkg = types.ModuleType("fp669")
    pkg.__path__ = [str(SRC)]  # type: ignore[attr-defined]
    sys.modules["fp669"] = pkg
    _mk("fp669.coordinator", FordPassCoordinator=object)
    _mk("fp669.sensor", FordPassRestoreMixin=_FakeRestoreMixin)
    _mk("fp669.capability", usable=lambda *a, **k: True)
    _mk("fp669.api", FordPassApiError=_FakeApiError)

    import importlib

    sys.modules["fp669.switch"] = importlib.import_module("fp669.switch")


class _FakeApi:
    def __init__(self, panic_status: int | None = None) -> None:
        self.panic_calls: list[tuple] = []
        self.panic_cancel_calls: list[tuple] = []
        self.va_calls: list[tuple] = []
        self._panic_status = panic_status

    async def panic_command(self, vin, duration):
        self.panic_calls.append((vin, duration))
        if self._panic_status:
            raise _FakeApiError(self._panic_status, "panic unsupported")
        return {"commandId": "cid-panic"}

    async def panic_cancel_command(self, vin, duration):
        self.panic_cancel_calls.append((vin, duration))
        if self._panic_status:
            raise _FakeApiError(self._panic_status, "panic unsupported")
        return {"commandId": "cid-panic-cancel"}

    async def va_command(self, vin, command_type, vatype=4, duration=None):
        self.va_calls.append((vin, command_type, vatype, duration))
        return {"commandId": "cid-va"}


class _FakeCoordinator:
    uid_key = "VIN123"
    vin = "VIN123"
    device_info: dict = {}

    def __init__(self, panic_channel=None, panic_status: int | None = None) -> None:
        self.panic_channel = panic_channel
        self.api = _FakeApi(panic_status)
        self.data: dict = {"vehiclestatus": {}}

    def latest_command_state(self, *types_):
        return None


def _va_switch(coord, duration=10):
    _install_fakes()
    cls = sys.modules["fp669.switch"].FordPassVAswitch
    ent = object.__new__(cls)
    ent.coordinator = coord
    ent.hass = None
    ent._state = False
    ent._auto_off_task = None
    ent._last_cmd_time = None
    ent._last_cmd_result = None
    ent._last_cmd_error = None
    ent._duration = lambda: duration
    ent._set_cmd_result = lambda ok, err=None: None
    ent._schedule_auto_off = lambda d=None: None
    ent._cancel_auto_off = lambda: None
    ent._track_task = lambda coro: coro.close() or None
    return ent


def test_first_trigger_uses_panic():
    coord = _FakeCoordinator()
    ent = _va_switch(coord)
    asyncio.run(ent.async_turn_on())
    assert coord.api.panic_calls == [("VIN123", 10)]
    assert coord.api.va_calls == [], "panic 可用时不应再走 VA"
    assert coord.panic_channel is True


def test_unsupported_panic_falls_back_to_va_once():
    coord = _FakeCoordinator(panic_status=404)
    ent = _va_switch(coord)
    asyncio.run(ent.async_turn_on())
    assert len(coord.api.panic_calls) == 1
    assert coord.api.va_calls == [("VIN123", "InitialVA", 4, 10)], "404 后应回落 InitialVA"
    assert coord.panic_channel is False


def test_known_unsupported_skips_panic():
    coord = _FakeCoordinator(panic_channel=False, panic_status=404)
    ent = _va_switch(coord)
    asyncio.run(ent.async_turn_on())
    asyncio.run(ent.async_turn_on())
    assert coord.api.panic_calls == [], "已判定不支持后不再试探 panic"
    assert len(coord.api.va_calls) == 2


def test_known_supported_does_not_touch_va():
    coord = _FakeCoordinator(panic_channel=True)
    ent = _va_switch(coord)
    asyncio.run(ent.async_turn_on())
    assert len(coord.api.panic_calls) == 1
    assert coord.api.va_calls == []


def test_turn_off_uses_panic_cancel():
    coord = _FakeCoordinator(panic_channel=True)
    ent = _va_switch(coord)
    asyncio.run(ent.async_turn_off())
    assert coord.api.panic_cancel_calls == [("VIN123", 10)]
    assert coord.api.va_calls == []


def test_turn_off_uses_va_cancel_when_fallen_back():
    coord = _FakeCoordinator(panic_channel=False)
    ent = _va_switch(coord)
    asyncio.run(ent.async_turn_off())
    assert coord.api.va_calls == [("VIN123", "CancelVA", 4, None)]
    assert coord.api.panic_cancel_calls == []


def test_turn_off_404_falls_back():
    coord = _FakeCoordinator(panic_channel=None, panic_status=404)
    ent = _va_switch(coord)
    asyncio.run(ent.async_turn_off())
    assert coord.panic_channel is False
    assert coord.api.va_calls == [("VIN123", "CancelVA", 4, None)]


def test_channel_attribute_exposed():
    coord = _FakeCoordinator(panic_channel=False)
    ent = _va_switch(coord)
    assert ent.extra_state_attributes["light_horn_channel"] == "va"
    ent2 = _va_switch(_FakeCoordinator())
    assert ent2.extra_state_attributes["light_horn_channel"] == "panic"


def test_other_errors_are_not_swallowed():
    """非「通道不支持」的失败必须上抛，不能悄悄回落掩盖真实错误。"""
    coord = _FakeCoordinator(panic_status=500)
    ent = _va_switch(coord)
    try:
        asyncio.run(ent.async_turn_on())
    except _FakeApiError as exc:
        assert exc.status == 500
    else:
        raise AssertionError("500 不应回落，应该上抛")
    assert coord.api.va_calls == []
