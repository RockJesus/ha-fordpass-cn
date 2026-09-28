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

    async def _current_stamp(self) -> str | None:
        """Data timestamp BEFORE sending the refresh command."""
        try:
            status = await self.coordinator.api.get_vehicle_status(self.coordinator.vin)
            vs = status.get("vehiclestatus", status)
            return str(vs.get("lastModifiedDate") or vs.get("lastRefresh") or "")
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("FordPass baseline fetch failed: %s", exc)
            return None

    async def async_press(self) -> None:
        """Send the command, then refresh entities so the UI always moves.

        ForceRefresh only tells Ford's backend to pull fresh data from the car
        (the vehicle may be asleep and takes tens of seconds to report).  The
        refresh is done in three steps (v2.7.0):
          1. record the pre-command data timestamp,
          2. send the command,
          3. force-refresh right away (fast UI feedback, bypasses HA's
             update_interval throttle), poll until Ford's timestamp moves,
             then force-refresh once more with the fresh snapshot.
        A failed command must never block the refresh.
        """
        baseline: str | None = None
        if self._command == CMD_REFRESH_STATUS:
            baseline = await self._current_stamp()
            _LOGGER.info("FordPass ForceRefresh baseline=%s", baseline)

        try:
            await self.coordinator.api.send_command(self.coordinator.vin, self._command)
        except Exception as exc:  # noqa: BLE001 - keep going so entities still refresh
            _LOGGER.warning("FordPass command %s failed: %s", self._command, exc)

        if self._command == CMD_REFRESH_STATUS:
            try:
                await self.coordinator.force_refresh()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("FordPass immediate refresh failed: %s", exc)
            # Wait for Ford's backend to propagate the fresh snapshot.
            changed, stamp = await self.coordinator.api.wait_status_refresh(
                self.coordinator.vin, baseline=baseline
            )
            _LOGGER.info(
                "FordPass ForceRefresh done: changed=%s stamp=%s", changed, stamp
            )
            try:
                await self.coordinator.force_refresh()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("FordPass final refresh failed: %s", exc)
        else:
            try:
                await self.coordinator.force_refresh()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("FordPass refresh after %s failed: %s", self._command, exc)
