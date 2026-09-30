"""Switch platform: remote engine start + 合成开关（灯光寻车 / 后备箱锁）。

v2.10.0:
- 灯光寻车开关：开 = ZoneLightingON（灯光寻车），关 = ZoneLightingOFF（关闭灯光寻车）
- 后备箱锁开关：开 = TrunkUnlock（解锁后备箱），关 = DoorLock（全车上锁，后备箱随之锁定；
  网关无独立的 TrunkLock 命令，关闭后备箱锁以全车上锁实现）
- 两个开关均按车型能力过滤创建（crccFlag 开启 / 有尾门字段），不支持则不创建
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
    CMD_LOCK,
    CMD_TRUNK_UNLOCK,
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
    # 后备箱锁：有尾门 / 内尾门字段（支持后备箱解锁）才创建
    if capability.usable(
        status, [["doorStatus", "tailgateDoor"], ["doorStatus", "innerTailgateDoor"]]
    ):
        switches.append(FordPassTrunkSwitch(coordinator))
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


class FordPassTrunkSwitch(SwitchEntity):
    """后备箱锁开关（TrunkUnlock 解锁 / DoorLock 全车上锁）。

    is_on=True = 后备箱已解锁可开；is_on=False = 后备箱已锁定。
    网关无独立 TrunkLock 命令：关闭后备箱锁通过全车上锁（DoorLock）实现，
    车门与后备箱一并锁定。初始状态从门锁状态推断。
    """

    _attr_assumed_state = True
    _attr_icon = "mdi:car-back"

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-trunk"
        self._attr_name = "后备箱锁"
        self._attr_has_entity_name = False
        self._attr_device_info = coordinator.device_info
        self._state: bool | None = None

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def is_on(self) -> bool | None:
        # 优先返回记忆状态（用户操作后）
        if self._state is not None:
            return self._state
        # 初始/重启后从门锁状态推断：LOCKED → 已锁定(off)，UNLOCKED → 已解锁(on)
        status = self.coordinator.data.get("vehiclestatus", {})
        lock = capability.leaf(status, "lockStatus")
        if lock == "LOCKED":
            return False
        if lock == "UNLOCKED":
            return True
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """解锁后备箱（TrunkUnlock）。"""
        try:
            await self.coordinator.run_command(CMD_TRUNK_UNLOCK)
        except Exception:
            raise
        self._state = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """锁定后备箱：全车上锁（网关无独立 TrunkLock 命令）。"""
        try:
            await self.coordinator.run_command(CMD_LOCK)
        except Exception:
            raise
        self._state = False
        self.async_write_ha_state()
