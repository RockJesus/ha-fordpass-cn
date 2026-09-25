"""Lock platform: vehicle lock / unlock."""
from __future__ import annotations

from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CMD_LOCK, CMD_UNLOCK, DOMAIN
from .coordinator import FordPassCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: FordPassCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([FordPassLock(coordinator)])


class FordPassLock(LockEntity):
    """Lock entity controlling the vehicle doors."""

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-lock"
        self._attr_name = "门锁"
        self._attr_has_entity_name = False
        self._attr_device_info = coordinator.device_info
        self._attr_icon = "mdi:car-door-lock"

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def is_locked(self) -> bool:
        status = self.coordinator.data.get("vehiclestatus", {})
        value = status.get("lockStatus", {}).get("value")
        return value == "LOCKED" if value else None

    async def async_lock(self, **kwargs: Any) -> None:
        await self.coordinator.api.send_command(self.coordinator.vin, CMD_LOCK)
        await self.coordinator.async_request_refresh()

    async def async_unlock(self, **kwargs: Any) -> None:
        await self.coordinator.api.send_command(self.coordinator.vin, CMD_UNLOCK)
        await self.coordinator.async_request_refresh()

    async def async_update(self) -> None:
        await self.coordinator.async_request_refresh()
