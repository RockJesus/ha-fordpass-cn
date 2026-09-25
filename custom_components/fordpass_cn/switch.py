"""Switch platform: remote engine start."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CMD_ENGINE_START, CMD_ENGINE_STOP, DOMAIN
from .coordinator import FordPassCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: FordPassCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([FordPassEngineSwitch(coordinator)])


class FordPassEngineSwitch(SwitchEntity):
    """Remote engine start / stop."""

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-engine"
        self._attr_name = f"{coordinator.vin[-6:]} 远程启动"
        self._attr_has_entity_name = False
        self._attr_icon = "mdi:engine"

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def is_on(self) -> bool:
        status = self.coordinator.data.get("vehiclestatus", {})
        value = status.get("remoteStartStatus", {}).get("value")
        return bool(value) if value is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.api.send_command(self.coordinator.vin, CMD_ENGINE_START)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.api.send_command(self.coordinator.vin, CMD_ENGINE_STOP)
        await self.coordinator.async_request_refresh()
