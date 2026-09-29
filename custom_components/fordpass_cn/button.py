"""Button platform: one-shot remote actions (honk / panic / refresh)."""
from __future__ import annotations

import logging
from typing import Any

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

    async def _command_id(self, resp: Any) -> str | None:
        """Extract commandId from the decrypted send-command response."""
        if isinstance(resp, dict):
            cid = resp.get("commandId")
            if cid:
                return str(cid)
            inner = resp.get("data")
            if isinstance(inner, dict) and inner.get("commandId"):
                return str(inner["commandId"])
        return None

    async def async_press(self) -> None:
        """Send the command, then refresh entities so the UI always moves.

        ForceRefresh (v2.7.1, verified from the official app capture):
          * send-command returns a commandId,
          * the app polls command-execution-status every ~2 s until the
            decrypted vehiclestatus flips from placeholder (LAST_KNOWN,
            01-01-0001, vin=null) to CURRENT with the real snapshot,
          * the finished response itself already carries the complete fresh
            data — we push it straight to the entities.
        We also force-refresh once immediately for fast UI feedback and as a
        fallback if the command never completes.  A failed command must never
        block the refresh.
        """
        resp: Any = None
        try:
            resp = await self.coordinator.api.send_command(
                self.coordinator.vin, self._command
            )
        except Exception as exc:  # noqa: BLE001 - keep going so entities still refresh
            _LOGGER.warning("FordPass command %s failed: %s", self._command, exc)

        if self._command == CMD_REFRESH_STATUS:
            command_id = await self._command_id(resp)
            if command_id:
                # Immediate refresh: fast UI feedback (bypasses HA's interval).
                try:
                    await self.coordinator.force_refresh()
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.warning("FordPass immediate refresh failed: %s", exc)
                # Poll command-execution-status until the fresh snapshot lands.
                done, result = await self.coordinator.api.wait_command_complete(
                    self.coordinator.vin, command_id, self._command
                )
                _LOGGER.info(
                    "FordPass ForceRefresh: completed=%s has_result=%s",
                    done, result is not None,
                )
                if done and isinstance(result, dict):
                    vs = result.get("vehiclestatus", result)
                    self.coordinator.async_set_updated_data(
                        {"status": vs, "vehiclestatus": vs}
                    )
                    return
                # Fallback: fetch vehicle-status once more.
                try:
                    await self.coordinator.force_refresh()
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.warning("FordPass fallback refresh failed: %s", exc)
            else:
                # 发送失败（resp=None）已在上面打过 warning，这里不再重复；
                # 仅在响应存在但缺少 commandId（接口结构变化）时提醒。
                if resp is None:
                    _LOGGER.debug(
                        "FordPass ForceRefresh: send_command had no response, fallback refresh"
                    )
                else:
                    _LOGGER.warning(
                        "FordPass ForceRefresh: no commandId in response, resp=%s",
                        (str(resp)[:300]),
                    )
                try:
                    await self.coordinator.force_refresh()
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.warning("FordPass refresh failed: %s", exc)
        else:
            try:
                await self.coordinator.force_refresh()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("FordPass refresh after %s failed: %s", self._command, exc)
