"""二进制传感器平台（车门/车窗/发动机状态）。"""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .baseentity import VEHICLE_BINARY_SENSORS, FordpassEntity
from .const import DOMAIN, FORD_VEHICLES


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    sensors = []
    for vehicle in hass.data[DOMAIN][entry.entry_id][FORD_VEHICLES]:
        for key in VEHICLE_BINARY_SENSORS:
            sensors.append(FordVehicleBinarySensor(vehicle, key))
    async_add_entities(sensors, update_before_add=True)


class FordVehicleBinarySensor(FordpassEntity, BinarySensorEntity):
    """福特派二进制传感器。"""

    def __init__(self, coordinator, state_key) -> None:
        super().__init__(coordinator, state_key, platform_domain="binary_sensor")
        if "device_class" in state_key:
            self._attr_device_class = state_key["device_class"]
        if "icon" in state_key:
            self._attr_icon = state_key["icon"]

    @property
    def is_on(self) -> bool | None:
        value = self.get_value()
        if value is None or value == "unknown" or value == "":
            return None
        # off_state 表示“关闭/正常”对应的取值，其它视为开启/异常
        return value != self._state_key.get("off_state")
