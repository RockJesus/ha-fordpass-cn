"""Device tracker platform: vehicle location from the LBS API."""
from __future__ import annotations

import logging

try:
    # Recommended path on modern HA; also valid on older versions
    from homeassistant.components.device_tracker import TrackerEntity
except ImportError:  # pragma: no cover - very old HA
    from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import FordPassCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: FordPassCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    # Always create the tracker entity. The option toggle only controls
    # availability + whether the coordinator fetches LBS data, so flipping
    # "track_location" in the options flow takes effect immediately without
    # restarting HA (see async_update_options in __init__.py).
    async_add_entities([FordPassDeviceTracker(coordinator)])


class FordPassDeviceTracker(TrackerEntity):
    def __init__(self, coordinator: FordPassCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-tracker"
        self._attr_name = "车辆定位"
        self._attr_has_entity_name = False
        self._attr_device_info = coordinator.device_info
        self._attr_icon = "mdi:map-marker"

    @property
    def available(self) -> bool:
        """Unavailable while the user disables location tracking."""
        return self.coordinator.track_location and super().available

    @property
    def source_type(self) -> str:
        """Return the tracker source type (GPS)."""
        return "gps"

    @property
    def _location(self) -> dict | None:
        data = self.coordinator.data or {}
        loc = data.get("location")
        return loc if isinstance(loc, dict) else None

    @property
    def latitude(self) -> float | None:
        if self._location and self._location.get("lat"):
            try:
                return float(self._location["lat"])
            except (TypeError, ValueError):
                return None
        return None

    @property
    def longitude(self) -> float | None:
        if self._location and self._location.get("lon"):
            try:
                return float(self._location["lon"])
            except (TypeError, ValueError):
                return None
        return None

    @property
    def gps_accuracy(self) -> int | None:
        return 0

    @property
    def extra_state_attributes(self) -> dict:
        loc = self._location or {}
        return {
            "address": loc.get("address"),
            "upload_time": loc.get("uploadTime"),
        }
