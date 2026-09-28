"""Button platform: one-shot remote actions (honk / panic / refresh)."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CMD_HONK, CMD_PANIC, CMD_REFRESH_STATUS, DOMAIN
from .coordinator import FordPassCoordinator

_LOGGER = logging.getLogger(__name__)


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
        self._attr_name = label
        self._attr_has_entity_name = False
        self._attr_device_info = coordinator.device_info
        self._attr_icon = icon

    async def async_press(self) -> None:
        """Send the command, wait for Ford to propagate, then refresh entities.

        A failed command must never block the refresh, otherwise a manual
        refresh would silently stop updating all entities (v2.6.7).
        """
        try:
            await self.coordinator.api.send_command(self.coordinator.vin, self._command)
        except Exception as exc:  # noqa: BLE001 - keep going so entities still refresh
            _LOGGER.warning("FordPass command %s failed: %s", self._command, exc)

        if self._command == CMD_REFRESH_STATUS:
            # ForceRefresh only tells Ford's backend to pull fresh data from the
            # car; the backend takes seconds to propagate.  Poll until its data
            # timestamp moves so the entities actually show new values.
            changed, stamp = await self.coordinator.api.wait_status_refresh(
                self.coordinator.vin
            )
            _LOGGER.info(
                "FordPass ForceRefresh done: changed=%s stamp=%s", changed, stamp
            )

        await self.coordinator.async_request_refresh()
