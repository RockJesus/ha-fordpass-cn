"""设备追踪平台 - 车辆位置."""

from __future__ import annotations

import logging

from homeassistant.components.device_tracker import SourceType
from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .base_entity import FordPassBaseEntity
from .const import DOMAIN, VEHICLES
from .coordinator import FordPassCoordinator

_LOGGER = logging.getLogger(__name__)


class FordPassDeviceTracker(FordPassBaseEntity, TrackerEntity):
    """车辆位置追踪器."""

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        super().__init__(coordinator, "location", "车辆位置")
        self._attr_icon = "mdi:car"

    @property
    def source_type(self) -> SourceType:
        """返回位置来源类型."""
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        """返回纬度."""
        value = self._get_data("gps", "latitude")
        if value is not None:
            try:
                return float(value)
            except (ValueError, TypeError):
                pass
        return None

    @property
    def longitude(self) -> float | None:
        """返回经度."""
        value = self._get_data("gps", "longitude")
        if value is not None:
            try:
                return float(value)
            except (ValueError, TypeError):
                pass
        return None

    @property
    def location_accuracy(self) -> int:
        """返回位置精度（米）."""
        value = self._get_data("gps", "accuracy")
        if value is not None:
            try:
                return int(float(value))
            except (ValueError, TypeError):
                pass
        return 10


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """设置设备追踪平台."""
    coordinators = hass.data[DOMAIN][entry.entry_id][VEHICLES]
    entities = [FordPassDeviceTracker(c) for c in coordinators]
    async_add_entities(entities)
