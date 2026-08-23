"""开关平台 - 远程启动."""
from __future__ import annotations
import logging
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .base_entity import FordPassBaseEntity
from .const import DOMAIN, VEHICLES
from .coordinator import FordPassCoordinator

_LOGGER = logging.getLogger(__name__)


class FordPassRemoteStartSwitch(FordPassBaseEntity, SwitchEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator, "remote_start", "远程启动")
        self._attr_icon = "mdi:engine-outline"
    @property
    def is_on(self):
        value = self._get_data("ignitionStatus", "value")
        if value is None:
            return None
        return value == "ON"
    async def async_turn_on(self, **kwargs):
        success = await self.coordinator.async_remote_start(start=True)
        if not success:
            _LOGGER.error("远程启动失败")
        await self.coordinator.async_request_refresh()
    async def async_turn_off(self, **kwargs):
        success = await self.coordinator.async_remote_start(start=False)
        if not success:
            _LOGGER.error("远程关闭失败")
        await self.coordinator.async_request_refresh()


async def async_setup_entry(hass, entry, async_add_entities):
    coordinators = hass.data[DOMAIN][entry.entry_id][VEHICLES]
    async_add_entities([FordPassRemoteStartSwitch(c) for c in coordinators])
