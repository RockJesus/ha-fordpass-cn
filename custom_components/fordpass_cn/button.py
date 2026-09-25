"""Button platform: one-shot remote actions (honk / panic / refresh)."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CMD_HONK, CMD_PANIC, CMD_REFRESH_STATUS, DOMAIN
from .coordinator import FordPassCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: FordPassCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities(
        [
            FordPassButton(coordinator, "honk", "鸣笛寻车", "mdi:bullhorn", CMD_HONK),
            FordPassButton(coordinator, "panic", "报警", "mdi:alarm-light", CMD_PANIC),
            FordPassButton(coordinator, "refresh", "刷新车辆状态", "mdi:refresh", CMD_REFRESH_STATUS),
        ]
    )


class FordPassButton(ButtonEntity):
    def __init__(self, coordinator, key, label, icon, command) -> None:
        self.coordinator = coordinator
        self._command = command
        self._attr_unique_id = f"{coordinator.vin}-{key}"
        self._attr_name = f"{coordinator.vin[-6:]} {label}"
        self._attr_has_entity_name = False
        self._attr_icon = icon

    async def async_press(self) -> None:
        await self.coordinator.api.send_command(self.coordinator.vin, self._command)
        await self.coordinator.async_request_refresh()
