"""sensor 平台取值链路与诊断传感器的回归测试（不依赖 HA 运行时）。

回归保护点：
- v6.0.2: ``round_value`` 取整后**不得**跳过 enum_map / transform——此前
  取整分支直接 return，同一实体同时配 round_value 与 enum_map/transform
  时映射与换算被静默丢弃（无报错、难排查）；
- v6.0.2: 「云端同步状态」诊断传感器不得再携带每轮变化的时间戳属性
  （last_success）——它与 v5.9.8 清掉的 last_poll 同类：值没变但属性变，
  仍会每轮落一条 recorder 状态行。
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "fordpass_cn"


class _FakeSensorEntity:
    """替代 homeassistant.components.sensor.SensorEntity。"""

    _attr_should_poll = True
    _attr_native_unit_of_measurement: str | None = None
    _attr_state_class: str | None = None
    _attr_device_class: str | None = None
    _attr_entity_category: Any = None

    def async_write_ha_state(self) -> None:
        """测试中不需要真正写状态。"""

    @property
    def extra_state_attributes(self) -> dict | None:
        return None


class _FakeCoordinator:
    """sensor.py 的 FordPassSensor / 同步健康传感器只用到这些成员。"""

    uid_key = "VIN123"
    vin = "VIN123"
    device_info: dict[str, Any] = {}
    last_poll: Any = None
    _consecutive_failures = 0
    _last_success_time: Any = None
    _last_error_time: Any = None
    _last_error_detail: Any = None

    def __init__(self, data: dict | None = None) -> None:
        self.data = data or {}


def _mk(name: str, **attrs: Any) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


def _install_fake_modules() -> None:
    if "fpcn.sensor" in sys.modules:
        return
    for name in (
        "homeassistant",
        "homeassistant.components",
        "homeassistant.const",
        "homeassistant.helpers",
        "homeassistant.helpers.entity",
        "homeassistant.helpers.entity_platform",
        "homeassistant.helpers.restore_state",
        "homeassistant.helpers.device_registry",
        "homeassistant.helpers.update_coordinator",
        "homeassistant.util",
        "homeassistant.util.dt",
    ):
        _mk(name)
    sys.modules["homeassistant.const"].EntityCategory = types.SimpleNamespace(
        CONFIG="config", DIAGNOSTIC="diagnostic"
    )
    sys.modules["homeassistant.const"].UnitOfLength = types.SimpleNamespace(
        KILOMETERS="km", KILOMETERS_PER_HOUR="km/h", METERS="m"
    )
    sys.modules["homeassistant.const"].UnitOfTemperature = types.SimpleNamespace(
        CELSIUS="°C", FAHRENHEIT="°F"
    )
    sys.modules["homeassistant.const"].PERCENTAGE = "%"
    sys.modules["homeassistant.const"].UnitOfTime = types.SimpleNamespace(
        SECONDS="s", MINUTES="min", HOURS="h", DAYS="d"
    )
    sys.modules["homeassistant.const"].UnitOfElectricPotential = types.SimpleNamespace(
        VOLT="V"
    )
    sys.modules["homeassistant.const"].UnitOfEnergy = types.SimpleNamespace(
        KILO_WATT_HOUR="kWh"
    )
    sys.modules["homeassistant.const"].UnitOfPower = types.SimpleNamespace(
        KILO_WATT="kW", WATT="W"
    )
    sys.modules["homeassistant.const"].UnitOfMass = types.SimpleNamespace(
        GRAMS="g", KILOGRAMS="kg"
    )
    sys.modules["homeassistant.const"].UnitOfPressure = types.SimpleNamespace(
        KPA="kPa", BAR="bar", PSI="psi"
    )
    sys.modules["homeassistant.const"].UnitOfVolume = types.SimpleNamespace(
        LITERS="L"
    )
    sys.modules["homeassistant.const"].UnitOfVolumeFlowRate = types.SimpleNamespace(
        CUBIC_METERS_PER_HOUR="m³/h"
    )
    sys.modules["homeassistant.const"].UnitOfFrequency = types.SimpleNamespace(
        HERTZ="Hz"
    )
    sys.modules["homeassistant.const"].UnitOfIrradiance = types.SimpleNamespace(
        WATTS_PER_SQUARE_METER="W/m²"
    )
    sys.modules["homeassistant.const"].UnitOfSpeed = types.SimpleNamespace(
        KILOMETERS_PER_HOUR="km/h", METERS_PER_SECOND="m/s", MILES_PER_HOUR="mph"
    )
    sys.modules["homeassistant.const"].UnitOfInformation = types.SimpleNamespace(
        MEGABYTES="MB", GIGABYTES="GB"
    )
    sys.modules["homeassistant.const"].UnitOfDataRate = types.SimpleNamespace(
        MEGABYTES_PER_SECOND="MB/s"
    )
    sys.modules["homeassistant.const"].UnitOfElectricCurrent = (
        types.SimpleNamespace(AMPERE="A", MILLIAMPERE="mA")
    )
    sys.modules["homeassistant.const"].CONF_SCAN_INTERVAL = "scan_interval"
    _mk(
        "homeassistant.components.sensor",
        SensorEntity=_FakeSensorEntity,
        SensorDeviceClass=types.SimpleNamespace(
            BATTERY="battery", TIMESTAMP="timestamp", TEMPERATURE="temperature",
            DISTANCE="distance", PRESSURE="pressure", VOLTAGE="voltage",
            CURRENT="current", POWER="power", ENERGY="energy", SPEED="speed",
            DURATION="duration", DATE="date", ENUM="enum", HUMIDITY="humidity",
            ILLUMINANCE="illuminance", SIGNAL_STRENGTH="signal_strength",
            WATER="water", WEIGHT="weight", AQI="aqi", CO="co", CO2="co2",
            DATA_SIZE="data_size", DATA_RATE="data_rate", FREQUENCY="frequency",
            GAS="gas", MOISTURE="moisture", MONETARY="monetary", NITROGEN_DIOXIDE=(
                "nitrogen_dioxide"
            ), NITROGEN_MONOXIDE="nitrogen_monoxide", NITROUS_OXIDE="nitrous_oxide",
            OZONE="ozone", PH="ph", PM1="pm1", PM10="pm10", PM25="pm25",
            POWER_FACTOR="power_factor", PRECIPITATION="precipitation",
            PRECIPITATION_INTENSITY="precipitation_intensity",
            REACTIVE_POWER="reactive_power", SOUND_PRESSURE="sound_pressure",
            SULPHUR_DIOXIDE="sulphur_dioxide", TEMPERATURE_DELTA=None,
            VOLATILE_ORGANIC_COMPOUNDS="volatile_organic_compounds",
            VOLUME="volume", VOLUME_FLOW_RATE="volume_flow_rate",
            VOLUME_STORAGE="volume_storage", WIND_SPEED="wind_speed",
        ),
        SensorStateClass=types.SimpleNamespace(
            MEASUREMENT="measurement", TOTAL="total",
            TOTAL_INCREASING="total_increasing",
        ),
    )
    _mk("homeassistant.core", HomeAssistant=object, callback=lambda func: func)
    _mk("homeassistant.config_entries", ConfigEntry=object)
    _mk("homeassistant.components.device_tracker", TrackerEntity=object, SourceType=object)
    _mk(
        "homeassistant.helpers.restore_state",
        RestoreEntity=object,
        ExtraStoredData=object,
    )
    sys.modules["homeassistant.helpers.entity"].Entity = object
    sys.modules["homeassistant.helpers.entity_platform"].AddEntitiesCallback = object
    sys.modules["homeassistant.util.dt"].as_utc = lambda v: v
    sys.modules["homeassistant.util.dt"].now = lambda: None
    _mk(
        "homeassistant.exceptions",
        HomeAssistantError=type("HomeAssistantError", (Exception,), {}),
        ConfigEntryAuthFailed=type("ConfigEntryAuthFailed", (Exception,), {}),
        ConfigEntryNotReady=type("ConfigEntryNotReady", (Exception,), {}),
    )
    _mk("homeassistant.components.recorder", get_instance=lambda hass: None)
    _mk(
        "homeassistant.components.recorder.history",
        get_significant_states=lambda *a, **k: {},
    )
    _mk("homeassistant.helpers.entity_registry", async_get=lambda hass: None)
    sys.modules["homeassistant.helpers.entity_registry"].EntityRegistry = object
    sys.modules["homeassistant.helpers.entity_registry"].RegistryEntry = object

    pkg = _mk("fpcn")
    pkg.__path__ = [str(SRC)]  # type: ignore[attr-defined]
    _mk("fpcn.coordinator", FordPassCoordinator=_FakeCoordinator)
    for sub in ("api", "wbsk", "const", "capability"):
        if f"fpcn.{sub}" in sys.modules:
            continue
        spec = importlib.util.spec_from_file_location(f"fpcn.{sub}", SRC / f"{sub}.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"fpcn.{sub}"] = module
        spec.loader.exec_module(module)

    spec = importlib.util.spec_from_file_location("fpcn.sensor", SRC / "sensor.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["fpcn.sensor"] = module
    spec.loader.exec_module(module)


def load_sensor():
    _install_fake_modules()
    return sys.modules["fpcn.sensor"]


def _make(round_value=False, enum_map=None, transform=None, multiplier=None):
    mod = load_sensor()
    coord = _FakeCoordinator({"vehiclestatus": {"fuel": {"fuelLevel": 55.6}}})
    ent = mod.FordPassSensor(
        coord, "test", "测试", "%", None, "mdi:fuel", ["fuel", "fuelLevel"],
        round_value=round_value, enum_map=enum_map, transform=transform,
        multiplier=multiplier,
    )
    ent._restored = None
    return ent


def test_round_value_alone() -> None:
    """仅 round_value：四舍五入为整数。"""
    assert _make(round_value=True).native_value == 56


def test_round_value_then_enum_map_applied() -> None:
    """v6.0.2: round_value 与 enum_map 同时配置时映射仍生效。"""
    ent = _make(round_value=True, enum_map={56: "半箱以上"})
    assert ent.native_value == "半箱以上"


def test_round_value_then_transform_applied() -> None:
    """v6.0.2: round_value 与 transform 同时配置时换算仍生效。"""
    ent = _make(round_value=True, transform=lambda v: v * 2)
    assert ent.native_value == 112


def test_multiplier_before_round() -> None:
    """multiplier 先于取整（1.5 倍再取整）。"""
    assert _make(round_value=True, multiplier=1.5).native_value == 83


def test_no_round_keeps_float() -> None:
    """未开取整时保留原始数值。"""
    assert _make().native_value == 55.6


def test_sync_health_has_no_per_poll_timestamp() -> None:
    """v6.0.2: 同步健康传感器属性中不得含每轮变化的 last_success。"""
    mod = load_sensor()
    import datetime

    coord = _FakeCoordinator({})
    coord._last_success_time = datetime.datetime(2026, 10, 28, 10, 0, 0)
    ent = mod.FordPassSyncHealthSensor(coord)
    attrs = ent.extra_state_attributes
    assert "last_success" not in attrs
    # 失败信息仍需保留（只在失败时变化，常态静止）
    coord._consecutive_failures = 2
    coord._last_error_detail = "限流"
    attrs = ent.extra_state_attributes
    assert attrs["consecutive_failures"] == 2
    assert attrs["last_error_detail"] == "限流"
    assert ent.native_value == "异常"
