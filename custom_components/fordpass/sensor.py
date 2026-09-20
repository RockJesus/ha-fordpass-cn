"""Sensor platform for FordPass."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
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
    """Set up FordPass sensors based on a config entry."""
    client: FordPassClient = hass.data[DOMAIN][entry.entry_id]

    entities = [FordPassUserSensor(client)]

    async_add_entities(entities, True)


class FordPassUserSensor(SensorEntity):
    """Representation of a FordPass user sensor."""

    _attr_has_entity_name = True
    _attr_name = "用户信息"
    _attr_icon = "mdi:account"

    def __init__(self, client: FordPassClient) -> None:
        """Initialize the sensor."""
        self._client = client
        self._attr_unique_id = f"fordpass_user_{client.user_id or 'unknown'}"

    @property
    def native_value(self) -> str:
        """Return the state of the sensor."""
        return self._client.nickname or "未登录"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        return {
            "user_id": self._client.user_id,
            "nickname": self._client.nickname,
            "vehicle_count": len(self._client.vehicles),
        }

    async def async_update(self) -> None:
        """Update the sensor."""
        try:
            await self._client.get_user_info()
            await self._client.get_vehicle_list()
        except Exception as err:
            _LOGGER.warning("Failed to update: %s", err)
