"""v5.10.0: 云端最后刷新诊断实体（last_poll 收口）的行为测试。

回归保护点：
- 诊断实体声明（DIAGNOSTIC / TIMESTAMP / 默认停用 / 不轮询）；
- last_poll 为空时 available=False 且状态为 None；
- native_value 直接透传 coordinator.last_poll（UTC 语义由 dt_util.as_utc 承担）。
"""
from __future__ import annotations

import importlib.util
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "fordpass_cn"


def _install_fake_modules() -> None:
    if "fpcn.sensor" in sys.modules:
        return

    def mk(name: str, **attrs: Any) -> types.ModuleType:
        module = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        sys.modules[name] = module
        return module

    class _Dummy:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

    class SensorDeviceClass:
        TIMESTAMP = "timestamp"
        BATTERY = "battery"
        VOLTAGE = "voltage"
        PRESSURE = "pressure"
        DISTANCE = "distance"

    class SensorStateClass:
        TOTAL = "total"
        MEASUREMENT = "measurement"

    class SensorEntity(_Dummy):
        pass

    class EntityCategory:
        DIAGNOSTIC = "diagnostic"
        CONFIG = "config"

    class RestoreEntity(_Dummy):
        pass

    _dt = mk("homeassistant.util.dt")
    _dt.as_utc = lambda value: value.astimezone(timezone.utc)

    for name in ("homeassistant", "homeassistant.components",
                 "homeassistant.helpers", "homeassistant.util"):
        mk(name)
    mk("homeassistant.components.sensor",
       SensorDeviceClass=SensorDeviceClass, SensorStateClass=SensorStateClass,
       SensorEntity=SensorEntity)
    mk("homeassistant.config_entries", ConfigEntry=object)
    mk("homeassistant.const", EntityCategory=EntityCategory,
       UnitOfLength=types.SimpleNamespace(KILOMETERS="km"),
       UnitOfPressure=types.SimpleNamespace(KPA="kPa"),
       UnitOfTime=types.SimpleNamespace(MINUTES="min"))
    mk("homeassistant.core", HomeAssistant=object, callback=lambda f: f)
    mk("homeassistant.helpers.entity_registry").async_get = lambda hass: _Dummy()
    mk("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
    mk("homeassistant.helpers.restore_state", RestoreEntity=RestoreEntity)
    sys.modules["homeassistant.util.dt"] = _dt

    pkg = mk("fpcn")
    pkg.__path__ = [str(SRC)]  # type: ignore[attr-defined]
    mk("fpcn.coordinator", FordPassCoordinator=_Dummy)
    mk("fpcn.capability")
    mk("fpcn.const", DOMAIN="fordpass_cn", ANNOUNCE_ENUM_CN={}, CHIRP_TO_ANNOUNCE={})

    spec = importlib.util.spec_from_file_location("fpcn.sensor", SRC / "sensor.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["fpcn.sensor"] = module
    spec.loader.exec_module(module)


def load_sensor_cls():
    _install_fake_modules()
    return sys.modules["fpcn.sensor"].FordPassLastPollSensor


def test_diag_sensor_defaults():
    cls = load_sensor_cls()
    coord = MagicMock()
    coord.uid_key = "VIN123"
    ent = cls(coord)
    assert ent._attr_unique_id == "VIN123-last_poll"
    # 假 SensorEntity 没有 HA 的 property 转发，直接断言 _attr_* 类属性
    assert cls._attr_entity_category == "diagnostic"
    assert cls._attr_should_poll is False
    assert cls._attr_entity_registry_enabled_default is False
    assert cls._attr_device_class == "timestamp"
    assert ent._attr_name == "云端最后刷新"


def test_diag_sensor_value_and_availability():
    cls = load_sensor_cls()
    coord = MagicMock()
    coord.uid_key = "VIN123"
    ent = cls(coord)

    coord.last_poll = None
    assert ent.available is False
    assert ent.native_value is None

    stamp = datetime(2026, 10, 28, 0, 30, tzinfo=timezone.utc)
    coord.last_poll = stamp
    assert ent.available is True
    assert ent.native_value is stamp
