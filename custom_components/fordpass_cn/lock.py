"""锁平台 - 车辆中控锁."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .base_entity import FordPassBaseEntity
from .const import DOMAIN, VEHICLES
from .coordinator import FordPassCoordinator

_LOGGER = logging.getLogger(__name__)


class FordPassDoorLock(FordPassBaseEntity, LockEntity):
    """车辆中控锁."""

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        super().__init__(coordinator, "lock", "中控锁")
        self._attr_icon = "mdi:lock"

    @property
    def is_locked(self) -> bool | None:
        """返回锁状态."""
        value = self._get_data("lockStatus", "value")
        if value is None:
            # 备用：通过车门状态推断
            return None
        # LOCKED = 已锁, UNLOCKED = 未锁
        return value == "LOCKED"

    async def async_lock(self, **kwargs: Any) -> None:
        """锁车."""
        _LOGGER.debug("锁车: %s", self._vin)
        success = await self.coordinator.async_lock(lock=True)
        if not success:
            _LOGGER.error("锁车失败")
        await self.coordinator.async_request_refresh()

    async def async_unlock(self, **kwargs: Any) -> None:
        """解锁."""
        _LOGGER.debug("解锁: %s", self._vin)
        success = await self.coordinator.async_lock(lock=False)
        if not success:
            _LOGGER.error("解锁失败")
        await self.coordinator.async_request_refresh()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """设置锁平台."""
    coordinators = hass.data[DOMAIN][entry.entry_id][VEHICLES]
    entities = [FordPassDoorLock(c) for c in coordinators]
    async_add_entities(entities)
