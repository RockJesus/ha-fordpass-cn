"""Switch platform: remote engine start + 灯光寻车开关。

v2.10.0: 灯光寻车开关——开 = ZoneLightingON（灯光寻车），关 = ZoneLightingOFF（关闭灯光寻车）。
v3.0.1: 后备箱锁已从 switch 迁移为 lock 实体（见 lock.py），此处仅保留远程启动与灯光寻车。
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import capability
from .const import (
    CMD_ENGINE_START,
    CMD_ENGINE_STOP,
    CMD_LIGHT_FIND_OFF,
    CMD_LIGHT_FIND_ON,
    DOMAIN,
)
from .coordinator import FordPassCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: FordPassCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    status = coordinator.data.get("vehiclestatus", {}) or {}
    switches: list[SwitchEntity] = [FordPassEngineSwitch(coordinator)]
    # 灯光寻车：远程控车功能开启（crccFlag 有效）才创建
    if capability.usable(status, [["crccFlag"]]):
        switches.append(FordPassLightSwitch(coordinator))
    async_add_entities(switches)


class FordPassEngineSwitch(SwitchEntity):
    """Remote engine start / stop."""

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-engine"
        self._attr_name = "远程启动"
        self._attr_has_entity_name = False
        self._attr_device_info = coordinator.device_info
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
        await self.coordinator.run_command(CMD_ENGINE_START)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.run_command(CMD_ENGINE_STOP)


class FordPassLightSwitch(SwitchEntity):
    """灯光寻车开关（ZoneLightingON / ZoneLightingOFF）。

    灯光寻车是瞬时命令、车辆不回报灯光状态，开关状态为记忆值（assumed）。
    """

    _attr_assumed_state = True
    _attr_icon = "mdi:car-light-high"

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-light_find"
        self._attr_name = "灯光寻车"
        self._attr_has_entity_name = False
        self._attr_device_info = coordinator.device_info
        self._state: bool | None = None

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def is_on(self) -> bool | None:
        return self._state

    async def async_turn_on(self, **kwargs: Any) -> None:
        try:
            await self.coordinator.run_command(CMD_LIGHT_FIND_ON)
        except Exception:
            raise
        self._state = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        try:
            await self.coordinator.run_command(CMD_LIGHT_FIND_OFF)
        except Exception:
            raise
        self._state = False
        self.async_write_ha_state()
