"""Device tracker platform: vehicle location from the LBS API."""
from __future__ import annotations

import logging

from homeassistant.components.device_tracker import TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import FordPassCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: FordPassCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    if not entry.options.get("track_location", False):
        _LOGGER = logging.getLogger(__name__)
        _LOGGER.debug("device tracker disabled (track_location off)")
        return
    async_add_entities([FordPassDeviceTracker(coordinator)])


class FordPassDeviceTracker(TrackerEntity):
    def __init__(self, coordinator: FordPassCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-tracker"
        self._attr_name = "位置"
        self._attr_has_entity_name = False
        self._attr_device_info = coordinator.device_info
        self._attr_icon = "mdi:map-marker"

    @property
    def _location(self) -> dict | None:
        data = self.coordinator.data or {}
        loc = data.get("location")
        return loc if isinstance(loc, dict) else None

    @property
    def latitude(self) -> float | None:
        if self._location and self._location.get("lat"):
            return float(self._location["lat"])
        return None

    @property
    def longitude(self) -> float | None:
        if self._location and self._location.get("lon"):
            return float(self._location["lon"])
        return None

    @property
    def extra_state_attributes(self) -> dict:
        loc = self._location or {}
        return {
            "address": loc.get("address"),
            "upload_time": loc.get("uploadTime"),
        }
