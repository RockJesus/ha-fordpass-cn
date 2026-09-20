"""Switch platform for FordPass."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
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
    """Set up FordPass switches based on a config entry."""
    client: FordPassClient = hass.data[DOMAIN][entry.entry_id]

    entities = []

    for vehicle in client.vehicles:
        vin = vehicle.get("vin") or vehicle.get("id")
        if vin:
            entities.append(FordPassRemoteStartSwitch(client, vehicle, vin))
            entities.append(FordPassHornSwitch(client, vehicle, vin))

    async_add_entities(entities, True)


class FordPassRemoteStartSwitch(SwitchEntity):
    """Representation of a FordPass remote start switch."""

    _attr_has_entity_name = True
    _attr_name = "远程启动"
    _attr_icon = "mdi:engine"

    def __init__(self, client: FordPassClient, vehicle: dict[str, Any], vin: str) -> None:
        """Initialize the switch."""
        self._client = client
        self._vehicle = vehicle
        self._vin = vin
        self._attr_unique_id = f"fordpass_remote_start_{vin}"
        self._attr_is_on = False

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self._vin)},
            "name": f"福特派车辆 {self._vehicle.get('modelName', '')}",
            "manufacturer": "Ford",
            "model": self._vehicle.get("modelName", ""),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on remote start."""
        try:
            await self._client.start_vehicle(self._vin)
            self._attr_is_on = True
            self.async_write_ha_state()
        except Exception as err:
            _LOGGER.error("Failed to start vehicle: %s", err)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off remote start."""
        try:
            await self._client.stop_vehicle(self._vin)
            self._attr_is_on = False
            self.async_write_ha_state()
        except Exception as err:
            _LOGGER.error("Failed to stop vehicle: %s", err)

    async def async_update(self) -> None:
        """Update the switch."""
        try:
            status = await self._client.get_vehicle_status(self._vin)
            ignition = status.get("ignition", {})
            self._attr_is_on = ignition.get("on", False)
        except Exception as err:
            _LOGGER.warning("Failed to update remote start: %s", err)


class FordPassHornSwitch(SwitchEntity):
    """Representation of a FordPass horn switch."""

    _attr_has_entity_name = True
    _attr_name = "鸣笛寻车"
    _attr_icon = "mdi:car-hatch"

    def __init__(self, client: FordPassClient, vehicle: dict[str, Any], vin: str) -> None:
        """Initialize the switch."""
        self._client = client
        self._vehicle = vehicle
        self._vin = vin
        self._attr_unique_id = f"fordpass_horn_{vin}"
        self._attr_is_on = False

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self._vin)},
            "name": f"福特派车辆 {self._vehicle.get('modelName', '')}",
            "manufacturer": "Ford",
            "model": self._vehicle.get("modelName", ""),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on horn."""
        try:
            await self._client.honk_vehicle(self._vin)
            self._attr_is_on = True
            self.async_write_ha_state()
        except Exception as err:
            _LOGGER.error("Failed to honk: %s", err)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off horn."""
        self._attr_is_on = False
        self.async_write_ha_state()

    async def async_update(self) -> None:
        """Update the switch."""
        self._attr_is_on = False
