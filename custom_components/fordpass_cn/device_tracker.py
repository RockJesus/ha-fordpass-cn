"""设备追踪平台 - 车辆位置."""
from __future__ import annotations
from homeassistant.components.device_tracker import SourceType
from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .base_entity import FordPassBaseEntity
from .const import DOMAIN, VEHICLES
from .coordinator import FordPassCoordinator


class FordPassDeviceTracker(FordPassBaseEntity, TrackerEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator, "location", "车辆位置")
        self._attr_icon = "mdi:car"
    @property
    def source_type(self):
        return SourceType.GPS
    @property
    def latitude(self):
        value = self._get_data("gps", "latitude")
        if value is not None:
            try:
                return float(value)
            except (ValueError, TypeError):
                pass
        return None
    @property
    def longitude(self):
        value = self._get_data("gps", "longitude")
        if value is not None:
            try:
                return float(value)
            except (ValueError, TypeError):
                pass
        return None
    @property
    def location_accuracy(self):
        value = self._get_data("gps", "accuracy")
        if value is not None:
            try:
                return int(float(value))
            except (ValueError, TypeError):
                pass
        return 10


async def async_setup_entry(hass, entry, async_add_entities):
    coordinators = hass.data[DOMAIN][entry.entry_id][VEHICLES]
    async_add_entities([FordPassDeviceTracker(c) for c in coordinators])
