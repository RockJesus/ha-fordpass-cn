"""中控锁平台。"""

from __future__ import annotations

from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_LOCKED, STATE_UNLOCKED
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .baseentity import VEHICLE_LOCKS, FordpassSwitchEntity
from .const import DOMAIN, FORD_VEHICLES


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    locks = []
    for vehicle in hass.data[DOMAIN][entry.entry_id][FORD_VEHICLES]:
        for key in VEHICLE_LOCKS:
            locks.append(FordVehicleLock(vehicle, key))
    async_add_entities(locks, update_before_add=True)


class FordVehicleLock(FordpassSwitchEntity, LockEntity):
    """车辆中控锁。"""

    def __init__(self, coordinator, state_key) -> None:
        super().__init__(coordinator, state_key, platform_domain="lock")
        if "icon" in state_key:
            self._attr_icon = state_key["icon"]

    @property
    def is_locked(self) -> bool | None:
        value = self.get_value()
        if value is None or value == "unknown" or value == "":
            return None
        # 1/True/"Locked"/"锁" 视为已上锁
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "locked", "lock", "已锁", "上锁")
        return bool(value)

    async def async_lock(self, **kwargs: Any) -> None:
        await self.async_switch_on()  # 上锁 = endpoint put

    async def async_unlock(self, **kwargs: Any) -> None:
        await self.async_switch_off()  # 解锁 = endpoint delete
