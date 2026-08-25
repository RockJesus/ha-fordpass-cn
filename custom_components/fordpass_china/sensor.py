"""传感器平台。"""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .baseentity import VEHICLE_SENSORS, FordpassEntity
from .const import DOMAIN, FORD_VEHICLES


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    sensors = []
    for vehicle in hass.data[DOMAIN][entry.entry_id][FORD_VEHICLES]:
        for key in VEHICLE_SENSORS:
            sensors.append(FordVehicleSensor(vehicle, key))
    async_add_entities(sensors, update_before_add=True)


class FordVehicleSensor(FordpassEntity, SensorEntity):
    """福特派传感器。"""

    def __init__(self, coordinator, state_key) -> None:
        super().__init__(coordinator, state_key, platform_domain="sensor")
        if "unit" in state_key:
            self._attr_native_unit_of_measurement = state_key["unit"]

    @property
    def native_value(self) -> StateType:
        return self.get_value()
