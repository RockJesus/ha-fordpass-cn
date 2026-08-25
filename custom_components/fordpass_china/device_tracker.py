"""设备跟踪平台（车辆 GPS 位置）。"""

from __future__ import annotations

from homeassistant.components.device_tracker.config_entry import (
    BaseTrackerEntity,
    TrackerEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .baseentity import FordpassEntity
from .const import DOMAIN, FORD_VEHICLES


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    trackers = []
    for vehicle in hass.data[DOMAIN][entry.entry_id][FORD_VEHICLES]:
        trackers.append(FordVehicleTracker(vehicle))
    async_add_entities(trackers, update_before_add=True)


class FordVehicleTracker(FordpassEntity, TrackerEntity):
    """车辆 GPS 位置跟踪。"""

    _attr_force_update = False
    _attr_icon = "mdi:car"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, state_key=None, platform_domain="device_tracker")

    @property
    def source_type(self) -> str:
        return "gps"

    @property
    def latitude(self) -> float | None:
        gps = self.coordinator.data.get("gps") if self.coordinator.data else None
        if isinstance(gps, dict):
            try:
                return float(gps.get("latitude"))
            except (TypeError, ValueError):
                return None
        return None

    @property
    def longitude(self) -> float | None:
        gps = self.coordinator.data.get("gps") if self.coordinator.data else None
        if isinstance(gps, dict):
            try:
                return float(gps.get("longitude"))
            except (TypeError, ValueError):
                return None
        return None
