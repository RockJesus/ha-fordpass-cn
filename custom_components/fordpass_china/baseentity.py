"""福特派 China 实体基类与实体定义表。"""

from __future__ import annotations

import logging

from homeassistant.const import (
    ELECTRIC_POTENTIAL_VOLT,
    LENGTH_KILOMETERS,
    PERCENTAGE,
    STATE_UNKNOWN,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 实体定义表（key_path 为车辆状态 JSON 中的取值路径）
# ---------------------------------------------------------------------------
VEHICLE_SENSORS = [
    {"key": "alarm", "name": "Alarm", "key_path": ["alarm", "value"], "icon": "mdi:bell"},
    {
        "key": "odometer",
        "name": "Odometer",
        "key_path": ["odometer", "value"],
        "icon": "mdi:counter",
        "unit": LENGTH_KILOMETERS,
    },
    {
        "key": "fuel",
        "name": "Fuel",
        "key_path": ["fuel", "fuelLevel"],
        "icon": "mdi:fuel",
        "unit": PERCENTAGE,
    },
    {
        "key": "range",
        "name": "Range",
        "key_path": ["fuel", "distanceToEmpty"],
        "icon": "mdi:map-marker-distance",
        "unit": LENGTH_KILOMETERS,
    },
    {
        "key": "battery_health",
        "name": "Battery Health",
        "key_path": ["battery", "batteryHealth", "value"],
        "icon": "mdi:car-battery",
    },
    {
        "key": "battery_voltage",
        "name": "Battery Voltage",
        "key_path": ["battery", "batteryStatusActual", "value"],
        "icon": "mdi:car-battery",
        "unit": ELECTRIC_POTENTIAL_VOLT,
    },
    {
        "key": "oil_life",
        "name": "Oil Life",
        "key_path": ["oil", "oilLifeActual"],
        "icon": "mdi:oil",
        "unit": PERCENTAGE,
    },
    {
        "key": "left_front_tire_pressure",
        "name": "Left Front Tire Pressure",
        "key_path": ["TPMS", "leftFrontTirePressure", "value"],
        "icon": "mdi:gauge",
        "unit": "kPa",
    },
    {
        "key": "right_front_tire_pressure",
        "name": "Right Front Tire Pressure",
        "key_path": ["TPMS", "rightFrontTirePressure", "value"],
        "icon": "mdi:gauge",
        "unit": "kPa",
    },
    {
        "key": "left_rear_tire_pressure",
        "name": "Left Rear Tire Pressure",
        "key_path": ["TPMS", "outerLeftRearTirePressure", "value"],
        "icon": "mdi:gauge",
        "unit": "kPa",
    },
    {
        "key": "right_rear_tire_pressure",
        "name": "Right Rear Tire Pressure",
        "key_path": ["TPMS", "outerRightRearTirePressure", "value"],
        "icon": "mdi:gauge",
        "unit": "kPa",
    },
    {
        "key": "driver_window_position",
        "name": "Driver Window Position",
        "key_path": ["windowPosition", "driverWindowPosition", "value"],
        "icon": "mdi:dock-window",
    },
    {
        "key": "pass_window_position",
        "name": "Passenger Window Position",
        "key_path": ["windowPosition", "passWindowPosition", "value"],
        "icon": "mdi:dock-window",
    },
    {
        "key": "rear_driver_window_pos",
        "name": "Rear Driver Window Position",
        "key_path": ["windowPosition", "rearDriverWindowPos", "value"],
        "icon": "mdi:dock-window",
    },
    {
        "key": "rear_pass_window_pos",
        "name": "Rear Passenger Window Position",
        "key_path": ["windowPosition", "rearPassWindowPos", "value"],
        "icon": "mdi:dock-window",
    },
]

VEHICLE_SWITCHES = [
    {
        "key": "remote_start",
        "name": "Remote Start",
        "key_path": ["remoteStartStatus", "value"],
        "op_endpoint": "engine/start",
        "icon": "mdi:engine",
    }
]

VEHICLE_LOCKS = [
    {
        "key": "lock",
        "name": "Lock",
        "key_path": ["lockStatus", "value"],
        "op_endpoint": "doors/lock",
        "icon": "mdi:lock",
    }
]

VEHICLE_BINARY_SENSORS = [
    {
        "key": "driver_door",
        "name": "Driver Door",
        "key_path": ["doorStatus", "driverDoor", "value"],
        "icon": "mdi:car-door",
        "off_state": "Closed",
        "device_class": "door",
    },
    {
        "key": "passenger_door",
        "name": "Passenger Door",
        "key_path": ["doorStatus", "passengerDoor", "value"],
        "icon": "mdi:car-door",
        "off_state": "Closed",
        "device_class": "door",
    },
    {
        "key": "left_rear_door",
        "name": "Left Rear Door",
        "key_path": ["doorStatus", "leftRearDoor", "value"],
        "icon": "mdi:car-door",
        "off_state": "Closed",
        "device_class": "door",
    },
    {
        "key": "right_rear_door",
        "name": "Right Rear Door",
        "key_path": ["doorStatus", "rightRearDoor", "value"],
        "icon": "mdi:car-door",
        "off_state": "Closed",
        "device_class": "door",
    },
    {
        "key": "hood_door",
        "name": "Hood",
        "key_path": ["doorStatus", "hoodDoor", "value"],
        "icon": "mdi:car-door",
        "off_state": "Closed",
        "device_class": "door",
    },
    {
        "key": "tail_gate_door",
        "name": "Tail Gate Door",
        "key_path": ["doorStatus", "tailgateDoor", "value"],
        "icon": "mdi:car-door",
        "off_state": "Closed",
        "device_class": "door",
    },
    {
        "key": "inner_tail_gate_door",
        "name": "Inner Tail Gate Door",
        "key_path": ["doorStatus", "innerTailgateDoor", "value"],
        "icon": "mdi:car-door",
        "off_state": "Closed",
        "device_class": "door",
    },
    {
        "key": "ignition_status",
        "name": "Ignition",
        "key_path": ["ignitionStatus", "value"],
        "icon": "mdi:engine",
        "off_state": "Off",
        "device_class": "power",
    },
]


class FordpassEntity(CoordinatorEntity):
    """福特派实体基类。"""

    _attr_should_poll = False

    def __init__(self, coordinator, state_key=None, platform_domain: str = "sensor") -> None:
        super().__init__(coordinator)
        self._state_key = state_key
        self._platform_domain = platform_domain
        if state_key is None:
            self._attr_unique_id = f"{DOMAIN}_{self.coordinator.vin.lower()}"
        else:
            self._attr_unique_id = f"{DOMAIN}_{self.coordinator.vin.lower()}_{state_key['key']}"
        self._attr_device_info = {
            "manufacturer": "Ford Motor Company",
            "model": self.coordinator.model,
            "identifiers": {(DOMAIN, self.coordinator.vin.lower())},
            "name": self.coordinator.vehicle_name,
            "sw_version": self.coordinator.year,
        }
        # 用 VIN 前缀命名，让自动生成的 entity_id 保持 sensor.{vin}_{key} 风格
        if state_key is not None:
            self._attr_name = f"{self.coordinator.vin.lower()} {state_key['name']}"
        else:
            self._attr_name = f"{self.coordinator.vin.lower()}"

    def get_value(self):
        value = self.coordinator.data
        try:
            for key in self._state_key["key_path"]:
                value = value[key]
            if isinstance(value, float):
                return round(value, 2)
        except (TypeError, KeyError, IndexError):
            value = STATE_UNKNOWN
        return value

    @property
    def icon(self):
        return self._state_key.get("icon") if self._state_key else None


class FordpassSwitchEntity(FordpassEntity):
    """可控制实体基类（开关/锁）。"""

    async def async_switch_on(self):
        await self.coordinator.async_set_switch(self._state_key["op_endpoint"], turn_on=True)

    async def async_switch_off(self):
        await self.coordinator.async_set_switch(self._state_key["op_endpoint"], turn_on=False)
