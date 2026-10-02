"""Lock platform: vehicle lock / unlock + 后备箱锁（v3.0.1）。

门锁：DoorLock / DoorUnlock。
后备箱锁：解锁 = TrunkUnlock（后备箱弹开可开启），锁定 = DoorLock（全车上锁，
网关无独立后备箱锁命令，后备箱随全车锁定）；按车型能力过滤创建
（有尾门/内尾门字段才创建）。
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import capability
from .const import CMD_LOCK, CMD_TRUNK_UNLOCK, CMD_UNLOCK, DOMAIN
from .coordinator import FordPassCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    # v3.1.6: 多 VIN——每辆车独立创建锁实体（按各自车型能力过滤）
    payload = hass.data[DOMAIN][entry.entry_id]
    coordinators = payload.get("coordinators") or [payload["coordinator"]]
    locks: list[LockEntity] = []
    for coordinator in coordinators:
        locks.append(FordPassLock(coordinator))
        # 后备箱锁：有尾门 / 内尾门字段（支持后备箱解锁）才创建
        status = coordinator.data.get("vehiclestatus", {}) or {}
        if capability.usable(
            status, [["doorStatus", "tailgateDoor"], ["doorStatus", "innerTailgateDoor"]]
        ):
            locks.append(FordPassTrunkLock(coordinator))
    async_add_entities(locks)


class FordPassLock(LockEntity):
    """Lock entity controlling the vehicle doors."""

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-lock"
        self._attr_name = "门锁"
        self._attr_has_entity_name = False
        self._attr_device_info = coordinator.device_info
        self._attr_icon = "mdi:car-door-lock"

    @property
    def available(self) -> bool:
        return True  # v3.1.2: 不随福特云刷新失败而不可用（保留最后已知状态）

    @property
    def is_locked(self) -> bool:
        status = self.coordinator.data.get("vehiclestatus", {})
        value = status.get("lockStatus", {}).get("value")
        return value == "LOCKED" if value else None

    async def async_lock(self, **kwargs: Any) -> None:
        await self.coordinator.run_command(CMD_LOCK)

    async def async_unlock(self, **kwargs: Any) -> None:
        await self.coordinator.run_command(CMD_UNLOCK)

    async def async_update(self) -> None:
        await self.coordinator.async_request_refresh()


class FordPassTrunkLock(LockEntity):
    """后备箱锁（v3.0.1，由 switch 改为 lock）。

    is_locked=True = 后备箱已锁定；is_locked=False = 已解锁可开启。
    解锁 = TrunkUnlock；锁定 = DoorLock（全车上锁，网关无独立后备箱锁命令）。
    初始状态从门锁状态推断，操作后按记忆状态显示。
    """

    _attr_assumed_state = True
    _attr_icon = "mdi:car-back"

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-trunk"
        self._attr_name = "后备箱锁"
        self._attr_has_entity_name = False
        self._attr_device_info = coordinator.device_info
        self._state: bool | None = None  # True=锁定

    @property
    def available(self) -> bool:
        return True  # v3.1.2: 不随福特云刷新失败而不可用（保留最后已知状态）

    @property
    def is_locked(self) -> bool | None:
        # 优先返回记忆状态（用户操作后）
        if self._state is not None:
            return self._state
        # 初始/重启后从门锁状态推断：LOCKED → 锁定(True)，UNLOCKED → 解锁(False)
        status = self.coordinator.data.get("vehiclestatus", {})
        lock = capability.leaf(status, "lockStatus")
        if lock == "LOCKED":
            return True
        if lock == "UNLOCKED":
            return False
        return None

    async def async_lock(self, **kwargs: Any) -> None:
        """锁定后备箱：全车上锁（网关无独立 TrunkLock 命令）。"""
        try:
            await self.coordinator.run_command(CMD_LOCK)
        except Exception:
            raise
        self._state = True
        self.async_write_ha_state()

    async def async_unlock(self, **kwargs: Any) -> None:
        """解锁后备箱（TrunkUnlock）。"""
        try:
            await self.coordinator.run_command(CMD_TRUNK_UNLOCK)
        except Exception:
            raise
        self._state = False
        self.async_write_ha_state()

