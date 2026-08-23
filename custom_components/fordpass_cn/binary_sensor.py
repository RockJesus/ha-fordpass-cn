"""二元传感器平台."""
from __future__ import annotations
from dataclasses import dataclass
from homeassistant.components.binary_sensor import (BinarySensorDeviceClass,
    BinarySensorEntity, BinarySensorEntityDescription)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .base_entity import FordPassBaseEntity
from .const import DOMAIN, VEHICLES
from .coordinator import FordPassCoordinator


@dataclass(frozen=True)
class FordPassBinarySensorDescription(BinarySensorEntityDescription):
    value_path: tuple = ()
    on_value = True


BINARY_SENSOR_DESCRIPTIONS = (
    FordPassBinarySensorDescription(key="ignition", name="发动机状态", icon="mdi:engine",
        device_class=BinarySensorDeviceClass.RUNNING, value_path=("ignitionStatus","value"), on_value="ON"),
    FordPassBinarySensorDescription(key="door_fl", name="左前车门", icon="mdi:car-door",
        device_class=BinarySensorDeviceClass.DOOR, value_path=("doorStatus","driverDoorOpen")),
    FordPassBinarySensorDescription(key="door_fr", name="右前车门", icon="mdi:car-door",
        device_class=BinarySensorDeviceClass.DOOR, value_path=("doorStatus","passengerDoorOpen")),
    FordPassBinarySensorDescription(key="door_rl", name="左后车门", icon="mdi:car-door",
        device_class=BinarySensorDeviceClass.DOOR, value_path=("doorStatus","rearLeftDoorOpen")),
    FordPassBinarySensorDescription(key="door_rr", name="右后车门", icon="mdi:car-door",
        device_class=BinarySensorDeviceClass.DOOR, value_path=("doorStatus","rearRightDoorOpen")),
    FordPassBinarySensorDescription(key="hood", name="发动机舱盖", icon="mdi:car",
        device_class=BinarySensorDeviceClass.DOOR, value_path=("doorStatus","hoodOpen")),
    FordPassBinarySensorDescription(key="trunk", name="后备箱", icon="mdi:car-back",
        device_class=BinarySensorDeviceClass.DOOR, value_path=("doorStatus","trunkOpen")),
)


class FordPassBinarySensor(FordPassBaseEntity, BinarySensorEntity):
    entity_description: FordPassBinarySensorDescription
    def __init__(self, coordinator, description):
        super().__init__(coordinator, description.key, description.name)
        self.entity_description = description
    @property
    def is_on(self):
        value = self._get_data(*self.entity_description.value_path)
        if value is None:
            return None
        return value == self.entity_description.on_value


async def async_setup_entry(hass, entry, async_add_entities):
    coordinators = hass.data[DOMAIN][entry.entry_id][VEHICLES]
    entities = [FordPassBinarySensor(c, d) for c in coordinators for d in BINARY_SENSOR_DESCRIPTIONS]
    async_add_entities(entities)
