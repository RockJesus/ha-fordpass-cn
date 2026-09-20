"""Lock platform for FordPass."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .api import FordPassClient

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up FordPass locks based on a config entry."""
    client: FordPassClient = hass.data[DOMAIN][entry.entry_id]

    entities = []

    for vehicle in client.vehicles:
        vin = vehicle.get("vin") or vehicle.get("id")
        if vin:
            entities.append(FordPassLock(client, vehicle, vin))

    async_add_entities(entities, True)


class FordPassLock(LockEntity):
    """Representation of a FordPass vehicle lock."""

    _attr_has_entity_name = True
    _attr_name = "车门锁"
    _attr_icon = "mdi:car-key"

    def __init__(self, client: FordPassClient, vehicle: dict[str, Any], vin: str) -> None:
        """Initialize the lock."""
        self._client = client
        self._vehicle = vehicle
        self._vin = vin
        self._attr_unique_id = f"fordpass_lock_{vin}"
        self._attr_is_locked = False

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self._vin)},
            "name": f"福特派车辆 {self._vehicle.get('modelName', '')}",
            "manufacturer": "Ford",
            "model": self._vehicle.get("modelName", ""),
        }

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the vehicle."""
        try:
            await self._client.lock_vehicle(self._vin)
            self._attr_is_locked = True
            self.async_write_ha_state()
        except Exception as err:
            _LOGGER.error("Failed to lock vehicle: %s", err)

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the vehicle."""
        try:
            await self._client.unlock_vehicle(self._vin)
            self._attr_is_locked = False
            self.async_write_ha_state()
        except Exception as err:
            _LOGGER.error("Failed to unlock vehicle: %s", err)

    async def async_update(self) -> None:
        """Update the lock."""
        try:
            status = await self._client.get_vehicle_status(self._vin)
            doors = status.get("doors", {})
            self._attr_is_locked = doors.get("locked", False)
        except Exception as err:
            _LOGGER.warning("Failed to update lock: %s", err)
