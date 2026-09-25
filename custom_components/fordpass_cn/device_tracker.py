"""Device tracker platform: vehicle location from the LBS API."""
from __future__ import annotations

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
        return
    async_add_entities([FordPassDeviceTracker(coordinator)])


class FordPassDeviceTracker(TrackerEntity):
    def __init__(self, coordinator: FordPassCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-tracker"
        self._attr_name = f"{coordinator.vin[-6:]} 位置"
        self._attr_has_entity_name = False
        self._attr_icon = "mdi:map-marker"
        self._location: dict | None = None

    @property
    def latitude(self) -> float | None:
        return self._location and float(self._location.get("lat"))

    @property
    def longitude(self) -> float | None:
        return self._location and float(self._location.get("lon"))

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "address": self._location and self._location.get("address"),
            "upload_time": self._location and self._location.get("uploadTime"),
        }

    async def async_update(self) -> None:
        if self.hass and self.coordinator.api:
            self._location = await self.coordinator.api.get_location(self.coordinator.vin)
