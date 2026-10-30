"""v6.2.8: 车辆数据时间实体（取云端 lastModifiedDate/lastRefresh）的行为测试。

回归保护点：
- 实体声明（DIAGNOSTIC / TIMESTAMP / 不轮询 / 默认启用 / has_entity_name）；
- native_value 从 vehiclestatus 顶层 lastModifiedDate/lastRefresh 解析
  （秒/毫秒自适应、2000 年前占位视为无效）；
- lastModifiedDate 缺失时回退 lastRefresh；
- available 随 coordinator.data（保留最后已知状态，失败不置不可用）；
- extra_state_attributes 透传原始时间戳 raw_timestamp。
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
    _dt.utc_from_timestamp = lambda ts: datetime.fromtimestamp(ts, tz=timezone.utc)

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
    return sys.modules["fpcn.sensor"].FordPassVehicleDataTimeSensor


def _mk_coord(data: dict | None = None) -> MagicMock:
    coord = MagicMock()
    coord.uid_key = "VIN123"
    coord.data = data
    return coord


def test_diag_sensor_defaults():
    cls = load_sensor_cls()
    ent = cls(_mk_coord({"vehiclestatus": {}}))
    assert ent._attr_unique_id == "VIN123-vehicle_data_time"
    # 假 SensorEntity 没有 HA 的 property 转发，直接断言 _attr_* 类属性
    assert cls._attr_entity_category == "diagnostic"
    assert cls._attr_should_poll is False
    assert cls._attr_device_class == "timestamp"
    assert cls._attr_has_entity_name is True
    # v6.2.8: 默认启用（不设 enabled_default=False，与旧 last_poll 相反）
    assert getattr(cls, "_attr_entity_registry_enabled_default", True) is not False
    assert ent._attr_name == "车辆数据时间"


def test_native_value_last_modified_date_seconds():
    cls = load_sensor_cls()
    # 10 位秒时间戳
    ent = cls(_mk_coord({"vehiclestatus": {"lastModifiedDate": 1730075400}}))
    assert ent.native_value == datetime.fromtimestamp(1730075400, tz=timezone.utc)
    assert ent.available is True


def test_native_value_millis_fallback_last_refresh():
    cls = load_sensor_cls()
    # lastModifiedDate 缺失 → 回退 lastRefresh；13 位毫秒自适应
    ent = cls(_mk_coord({"vehiclestatus": {"lastRefresh": 1730075400123}}))
    assert ent.native_value == datetime.fromtimestamp(1730075400.123, tz=timezone.utc)


def test_placeholder_and_missing_value_invalid():
    cls = load_sensor_cls()
    # 2000-01-01 前占位值视为无效
    ent = cls(_mk_coord({"vehiclestatus": {"lastModifiedDate": 0}}))
    assert ent.native_value is None
    # 顶层字段缺失
    ent2 = cls(_mk_coord({"vehiclestatus": {}}))
    assert ent2.native_value is None


def test_availability_and_raw_attribute():
    cls = load_sensor_cls()
    ent = cls(_mk_coord(None))
    assert ent.available is False
    ent2 = cls(_mk_coord({"vehiclestatus": {"lastModifiedDate": 1730075400}}))
    assert ent2.available is True
    assert ent2.extra_state_attributes == {"raw_timestamp": 1730075400}
