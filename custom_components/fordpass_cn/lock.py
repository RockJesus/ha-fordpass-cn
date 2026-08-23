"""锁平台 - 中控锁."""
from __future__ import annotations
import logging
from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .base_entity import FordPassBaseEntity
from .const import DOMAIN, VEHICLES
from .coordinator import FordPassCoordinator

_LOGGER = logging.getLogger(__name__)


class FordPassDoorLock(FordPassBaseEntity, LockEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator, "lock", "中控锁")
        self._attr_icon = "mdi:lock"
    @property
    def is_locked(self):
        value = self._get_data("lockStatus", "value")
        if value is None:
            return None
        return value == "LOCKED"
    async def async_lock(self, **kwargs):
        success = await self.coordinator.async_lock(lock=True)
        if not success:
            _LOGGER.error("锁车失败")
        await self.coordinator.async_request_refresh()
    async def async_unlock(self, **kwargs):
        success = await self.coordinator.async_lock(lock=False)
        if not success:
            _LOGGER.error("解锁失败")
        await self.coordinator.async_request_refresh()


async def async_setup_entry(hass, entry, async_add_entities):
    coordinators = hass.data[DOMAIN][entry.entry_id][VEHICLES]
    async_add_entities([FordPassDoorLock(c) for c in coordinators])
