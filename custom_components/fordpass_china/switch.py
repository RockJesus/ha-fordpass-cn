"""开关平台（远程启动）。"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .baseentity import VEHICLE_SWITCHES, FordpassSwitchEntity
from .const import DOMAIN, FORD_VEHICLES


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    switches = []
    for vehicle in hass.data[DOMAIN][entry.entry_id][FORD_VEHICLES]:
        for key in VEHICLE_SWITCHES:
            switches.append(FordVehicleSwitch(vehicle, key))
    async_add_entities(switches, update_before_add=True)


class FordVehicleSwitch(FordpassSwitchEntity, SwitchEntity):
    """远程启动开关。"""

    def __init__(self, coordinator, state_key) -> None:
        super().__init__(coordinator, state_key, platform_domain="switch")
        if "icon" in state_key:
            self._attr_icon = state_key["icon"]

    @property
    def is_on(self) -> bool | None:
        value = self.get_value()
        if value is None or value == "unknown" or value == "":
            return None
        return value not in (0, "0", "Off", "off", "OFF")

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.async_switch_on()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.async_switch_off()
