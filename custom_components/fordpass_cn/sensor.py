"""Sensor platform: vehicle status readings."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfLength, UnitOfPressure
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import FordPassCoordinator


def _leaf(status, *keys, default=None):
    cur = status
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    if isinstance(cur, dict):
        return cur.get("value", default)
    return cur


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: FordPassCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    vin = coordinator.vin
    sensors = [
        FordPassSensor(coordinator, "fuel_level", "燃油量", "%", SensorDeviceClass.BATTERY, "mdi:fuel", ["fuel", "fuelLevel"], round_value=True),
        FordPassSensor(coordinator, "distance_to_empty", "续航里程", UnitOfLength.KILOMETERS, None, "mdi:road-variant", ["fuel", "distanceToEmpty"]),
        FordPassSensor(coordinator, "odometer", "总里程", UnitOfLength.KILOMETERS, None, "mdi:counter", ["odometer"]),
        FordPassSensor(coordinator, "oil_life", "机油寿命", "%", None, "mdi:oil", ["oil", "oilLifeActual"]),
        FordPassSensor(coordinator, "battery_voltage", "蓄电池电压", "V", SensorDeviceClass.VOLTAGE, "mdi:car-battery", ["battery", "batteryStatusActual"]),
        FordPassSensor(coordinator, "lf_tire", "左前轮胎压", UnitOfPressure.KPA, SensorDeviceClass.PRESSURE, "mdi:gauge", ["TPMS", "leftFrontTirePressure"], round_value=True),
        FordPassSensor(coordinator, "rf_tire", "右前轮胎压", UnitOfPressure.KPA, SensorDeviceClass.PRESSURE, "mdi:gauge", ["TPMS", "rightFrontTirePressure"], round_value=True),
        FordPassSensor(coordinator, "lr_tire", "左后轮胎压", UnitOfPressure.KPA, SensorDeviceClass.PRESSURE, "mdi:gauge", ["TPMS", "outerLeftRearTirePressure"], round_value=True),
        FordPassSensor(coordinator, "rr_tire", "右后轮胎压", UnitOfPressure.KPA, SensorDeviceClass.PRESSURE, "mdi:gauge", ["TPMS", "outerRightRearTirePressure"], round_value=True),
        FordPassSensor(coordinator, "lock_status", "门锁状态", None, None, "mdi:lock", ["lockStatus"], enum_map={"LOCKED": "已锁定", "UNLOCKED": "已解锁", 1: "已锁定", 0: "已解锁", "1": "已锁定", "0": "已解锁", True: "已锁定", False: "已解锁"}),
        FordPassSensor(coordinator, "alarm_status", "报警状态", None, None, "mdi:alarm", ["alarm"], enum_map={"NOT_IN_ALARM": "解除报警", "ALARM": "车辆被盗", "ARMED": "车辆被盗", "NOT_IN_ALARMS": "解除报警", 0: "解除报警", 1: "车辆被盗", "0": "解除报警", "1": "车辆被盗", False: "解除报警", True: "车辆被盗"}),
        FordPassSensor(coordinator, "remote_start", "远程启动状态", None, None, "mdi:engine", ["remoteStartStatus"], enum_map={1: "已远程启动", 0: "未远程启动", "1": "已远程启动", "0": "未远程启动", True: "已远程启动", False: "未远程启动", "true": "已远程启动", "false": "未远程启动"}),
    ]
    sensors.append(FordPassVehicleAttrSensor(coordinator, "license_plate", "车牌号", "license_plate", "mdi:car"))
    sensors.append(FordPassLocationSensor(coordinator))
    async_add_entities(sensors)


class FordPassVehicleAttrSensor(SensorEntity):
    """Reads a fixed attribute stored on the coordinator (e.g. license plate)."""

    def __init__(self, coordinator, key, label, attr, icon=None) -> None:
        self.coordinator = coordinator
        self._attr_key = attr
        self._attr_unique_id = f"{coordinator.vin}-{key}"
        self._attr_name = label
        self._attr_has_entity_name = False
        self._attr_device_info = coordinator.device_info
        if icon:
            self._attr_icon = icon

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def native_value(self):
        return getattr(self.coordinator, self._attr_key, None)


class FordPassLocationSensor(SensorEntity):
    """Vehicle location (address / coordinates), best effort."""

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-location"
        self._attr_name = "车辆定位"
        self._attr_has_entity_name = False
        self._attr_device_info = coordinator.device_info
        self._attr_icon = "mdi:map-marker"

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def native_value(self):
        data = self.coordinator.data or {}
        loc = data.get("location")
        if not loc or not isinstance(loc, dict):
            return "定位不可用"
        address = loc.get("address")
        if address:
            return address
        lat, lon = loc.get("lat"), loc.get("lon")
        if lat and lon:
            return f"{lat}, {lon}"
        return "定位不可用"


class FordPassSensor(SensorEntity):
    def __init__(self, coordinator, key, label, unit, device_class, icon, path,
                 round_value: bool = False, enum_map: dict | None = None) -> None:
        self.coordinator = coordinator
        self._key = key
        self._path = path
        self._round_value = round_value
        self._enum_map = enum_map
        self._attr_unique_id = f"{coordinator.vin}-{key}"
        self._attr_name = label
        self._attr_has_entity_name = False
        self._attr_device_info = coordinator.device_info
        self._attr_icon = icon
        if unit:
            self._attr_native_unit_of_measurement = unit
        if device_class:
            self._attr_device_class = device_class

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def native_value(self):
        status = self.coordinator.data.get("vehiclestatus", {})
        value = _leaf(status, *self._path)
        if self._round_value and isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(round(value))
        if self._enum_map is not None:
            return self._enum_map.get(value, value)
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value
