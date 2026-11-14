"""v6.6.8 回归测试：车门窗未关汇总 + 鸣笛/声光通道拆分 + 设置来源归一。

回归保护点：
1. 车门窗未关（binary_sensor）：任一车门/车窗/天窗未关 → on，属性列出具体项；
   未知值不计入（不得把 Unknown 误报成未关）；车型无车门/车窗字段不创建。
2. 鸣笛开关只走 V5 /honk：历史类型「声光共舞」(panic=4) 已被移除并归一为
   默认类型——旧实现会把鸣笛切到 InitialVA VAType=4，与「声光寻车」完全
   同通道（用户实测两开关反馈一样）。
3. 鸣笛设置来源：内存 → 云端回读 → options → 默认，保证「所见即所发」。
"""
from __future__ import annotations

import asyncio
import datetime
import sys
import types
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "fordpass_cn"


class _FakeBinarySensorEntity:
    _attr_should_poll = True
    _attr_has_entity_name = False
    _attr_name: str | None = None
    _attr_unique_id: str | None = None
    _attr_device_class = None
    _attr_icon = None
    _attr_device_info = None
    hass = None

    def async_write_ha_state(self) -> None:  # noqa: D102 - 桩
        return None

    def async_on_remove(self, func) -> None:  # noqa: D102 - 桩
        return None


class _FakeSwitchEntity:
    _attr_should_poll = False
    _attr_has_entity_name = False
    _attr_name: str | None = None
    _attr_unique_id: str | None = None
    _attr_icon = None
    _attr_device_info = None
    hass = None

    def async_write_ha_state(self) -> None:  # noqa: D102 - 桩
        return None


class _FakeRestoreMixin:
    def _restore_bool(self, default: bool) -> bool:
        return default


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
    if "fp668.binary_sensor" in sys.modules:
        return

    _need("homeassistant")
    sys.modules["homeassistant"].__path__ = []  # type: ignore[attr-defined]
    _need("homeassistant.components")
    bs = _need(
        "homeassistant.components.binary_sensor",
        BinarySensorEntity=_FakeBinarySensorEntity,
        BinarySensorDeviceClass=type("BinarySensorDeviceClass", (), {"PROBLEM": "problem"}),
    )
    sys.modules["homeassistant.components"].binary_sensor = bs
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
        _dt.now = lambda: datetime.datetime.now(
            datetime.timezone(datetime.timedelta(hours=8))
        )
    sys.modules["homeassistant.util"].dt = _dt

    # 集成包：真实 const / binary_sensor / switch；coordinator、sensor、capability 用桩
    pkg = types.ModuleType("fp668")
    pkg.__path__ = [str(SRC)]  # type: ignore[attr-defined]
    sys.modules["fp668"] = pkg
    _mk("fp668.coordinator", FordPassCoordinator=object)
    _mk("fp668.sensor", FordPassRestoreMixin=_FakeRestoreMixin)
    _mk("fp668.capability", usable=lambda *a, **k: True)

    import importlib

    sys.modules["fp668.binary_sensor"] = importlib.import_module("fp668.binary_sensor")
    sys.modules["fp668.switch"] = importlib.import_module("fp668.switch")


# ---------- 1. 车门窗未关聚合 ----------


class _FakeCoordinator:
    uid_key = "VIN123"
    vin = "VIN123"
    device_info: dict = {}

    def __init__(self, data: dict | None = None, settings: dict | None = None,
                 options: dict | None = None, entry_id: str | None = "e1") -> None:
        self.data = data or {}
        self.entry_id = entry_id
        self.config_entry = types.SimpleNamespace(runtime_data={"honk_settings": settings or {}})
        self._entry = types.SimpleNamespace(options=options or {})
        self.hass = types.SimpleNamespace(
            config_entries=types.SimpleNamespace(
                async_get_entry=lambda eid: self._entry if eid == self.entry_id else None
            )
        )

    def async_set_updated_data(self, data: dict) -> None:
        self.data = data

    def async_add_listener(self, cb):
        return lambda: None


def _ajar(coordinator):
    _install_fakes()
    return sys.modules["fp668.binary_sensor"].FordPassAjarBinarySensor(coordinator)


def test_ajar_all_closed_is_off():
    coord = _FakeCoordinator({
        "vehiclestatus": {
            "doorStatus": {"driverDoor": "Closed", "tailgateDoor": "Closed"},
            "windowPosition": {"driverWindowPosition": "Fully_Closed"},
        }
    })
    ent = _ajar(coord)
    assert ent.is_on is False
    assert ent.extra_state_attributes["open_count"] == 0


def test_ajar_door_open_lists_item():
    coord = _FakeCoordinator({
        "vehiclestatus": {"doorStatus": {"driverDoor": "Open", "tailgateDoor": "Ajar"}}
    })
    ent = _ajar(coord)
    assert ent.is_on is True
    items = ent.extra_state_attributes["open_items"]
    assert "主驾车门" in items and "尾门" in items
    assert ent.extra_state_attributes["open_count"] == 2


def test_ajar_window_percent_open():
    coord = _FakeCoordinator({
        "vehiclestatus": {"windowPosition": {"driverWindowPosition": "BetFully_50PercentOpen"}}
    })
    assert _ajar(coord).is_on is True


def test_ajar_numeric_zero_is_closed():
    coord = _FakeCoordinator({"vehiclestatus": {"windowPosition": {"rearPassWindowPos": 0}}})
    assert _ajar(coord).is_on is False


def test_ajar_unknown_is_not_reported():
    coord = _FakeCoordinator({
        "vehiclestatus": {"doorStatus": {"hoodDoor": "Unknown"}, "windowPosition": {}}
    })
    assert _ajar(coord).is_on is False, "Unknown 不得误报为未关"


def test_ajar_sunroof_top_level():
    coord = _FakeCoordinator({"vehiclestatus": {"sunroofPosition": "PartiallyOpen"}})
    assert _ajar(coord).is_on is True
    assert "天窗" in _ajar(coord).extra_state_attributes["open_items"]


def test_ajar_not_created_without_fields():
    """车型没有 doorStatus/windowPosition 时不创建实体（避免恒 off 的假实体）。"""
    _install_fakes()
    bs = sys.modules["fp668.binary_sensor"]
    created: list = []
    entry = types.SimpleNamespace(runtime_data={"coordinators": [_FakeCoordinator({"vehiclestatus": {}})]})
    asyncio.run(bs.async_setup_entry(None, entry, created.append))
    assert created == []


def test_ajar_created_with_fields():
    _install_fakes()
    bs = sys.modules["fp668.binary_sensor"]
    created: list = []
    entry = types.SimpleNamespace(
        runtime_data={"coordinators": [_FakeCoordinator({"vehiclestatus": {"doorStatus": {"driverDoor": "Closed"}}})]}
    )
    asyncio.run(bs.async_setup_entry(None, entry, created.append))
    assert len(created) == 1


# ---------- 2/3. 鸣笛开关通道与设置来源 ----------


class _FakeHonkApi:
    def __init__(self) -> None:
        self.honk_calls: list[dict] = []
        self.va_calls: list[dict] = []

    async def honk_command(self, vin, duration=30, interval=0, chirp_type=1):
        self.honk_calls.append({"vin": vin, "duration": duration, "chirp_type": chirp_type})
        return {"commandId": "cid-honk"}

    async def honk_cancel_command(self, vin):
        return {"commandId": "cid-cancel"}

    async def va_command(self, vin, command_type, vatype=4, duration=None):
        self.va_calls.append({"command_type": command_type, "vatype": vatype})
        return {"commandId": "cid-va"}


def _honk_switch(coordinator):
    _install_fakes()
    cls = sys.modules["fp668.switch"].FordPassHonkSwitch
    ent = object.__new__(cls)
    ent.coordinator = coordinator
    ent.hass = None
    ent._state = False
    ent._auto_off_task = None
    ent._last_cmd_time = None
    ent._last_cmd_result = None
    ent._last_cmd_error = None
    coordinator.api = _FakeHonkApi()
    ent._set_cmd_result = lambda ok, err=None: None
    ent._schedule_auto_off = lambda duration=None: None
    ent._cancel_auto_off = lambda: None
    ent._track_task = lambda coro: coro.close() or None
    return ent


def test_chirp_options_exclude_panic():
    _install_fakes()
    const = sys.modules["fp668.const"]
    assert "声光共舞" not in const.CHIRP_TYPE_OPTIONS
    assert const.DEFAULT_CHIRP_TYPE in const.CHIRP_TYPE_OPTIONS
    # 旧值仍可识别（回读/兼容），但不在可选集
    assert const.CHIRP_TO_TYPE["声光共舞"] == 4


def test_legacy_panic_settings_normalised():
    coord = _FakeCoordinator(settings={"chirp_type": "声光共舞"}, options={"honk_duration": 15})
    ent = _honk_switch(coord)
    duration, chirp_name = ent._settings()
    assert chirp_name == sys.modules["fp668.const"].DEFAULT_CHIRP_TYPE
    assert duration == 15


def test_settings_falls_back_to_cloud():
    """内存/options 都没有时，用云端回读（AnnouncementType=0 → 雨落荷叶）。"""
    coord = _FakeCoordinator(data={"chirp_cloud": {"AnnouncementType": "0", "Duration": "10"}})
    ent = _honk_switch(coord)
    _, chirp_name = ent._settings()
    assert chirp_name == "雨落荷叶"


def test_legacy_cloud_panic_ignored():
    """云端残留 4（声光共舞）不得让鸣笛开关回落到 panic 类型。"""
    coord = _FakeCoordinator(data={"chirp_cloud": {"AnnouncementType": "4"}})
    ent = _honk_switch(coord)
    _, chirp_name = ent._settings()
    assert chirp_name not in ("声光共舞",)


def test_turn_on_only_uses_honk_channel():
    coord = _FakeCoordinator(settings={"chirp_type": "雨落荷叶", "honk_duration": 10})
    ent = _honk_switch(coord)
    asyncio.run(ent.async_turn_on())
    assert coord.api.va_calls == [], "鸣笛开关不得再走 InitialVA VAType=4（与声光寻车重叠）"
    assert coord.api.honk_calls == [{"vin": "VIN123", "duration": 10, "chirp_type": 0}]


def test_turn_on_legacy_panic_still_honk():
    """历史设置停在「声光共舞」时，鸣笛开关也只鸣笛。"""
    coord = _FakeCoordinator(settings={"chirp_type": "声光共舞"})
    ent = _honk_switch(coord)
    asyncio.run(ent.async_turn_on())
    assert coord.api.va_calls == []
    assert len(coord.api.honk_calls) == 1
    assert coord.api.honk_calls[0]["chirp_type"] != 4


def test_turn_off_only_uses_honk_cancel():
    coord = _FakeCoordinator(settings={"chirp_type": "声光共舞"})
    ent = _honk_switch(coord)
    asyncio.run(ent.async_turn_off())
    assert coord.api.va_calls == []


# ---------- 4. 探测降噪白名单 ----------


def test_quiet_probe_paths_cover_known_failures():
    api_src = (SRC / "api.py").read_text(encoding="utf-8")
    for path in (
        "/api/alert/v1/search-new-ota-status",
        "/api/cnxapi-vds/v1/search-vehicle-device-list",
        "/api/cnesl-user/v1/search-vehicle-share-history-list",
    ):
        assert path in api_src
    assert "_QUIET_PROBE_PATHS" in api_src
