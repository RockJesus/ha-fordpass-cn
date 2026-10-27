"""Lock platform: vehicle lock / unlock + 后备箱锁。

门锁：DoorLock / DoorUnlock。
后备箱锁（v3.1.10 恢复）：解锁 = TrunkUnlock（后备箱弹开可开启），锁定 =
DoorLock（全车上锁，网关无独立后备箱锁命令，后备箱随全车锁定）。锐际实测
TrunkUnlock 被网关 228205「cmdSpec can not empty」拒绝（App 端该命令依赖
蓝牙 TIMA），但其他车型云端可能支持——全车型支持策略：登录后按车辆能力
（尾门/内尾门字段）判断创建，云端不支持的车型按下会返回网关明确报错。
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import capability
from .const import CMD_LOCK, CMD_TRUNK_UNLOCK, CMD_UNLOCK
from .coordinator import FordPassCoordinator
from .sensor import FordPassRestoreMixin

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    # v3.1.6: 多 VIN——每辆车独立创建锁实体（按各自车型能力过滤）
    payload = entry.runtime_data  # v5.3.0: runtime_data 替代 hass.data
    coordinators = payload.get("coordinators") or [payload["coordinator"]]
    locks: list[LockEntity] = []
    for coordinator in coordinators:
        locks.append(FordPassLock(coordinator))
        # 后备箱锁：有尾门 / 内尾门字段（支持后备箱解锁的车型）才创建
        # v5.8.3: 按车型创建——TrunkUnlock 曾被云端确定性拒绝（实测失败，
        # 如锐际 228205 依赖蓝牙 TIMA）的车型不再创建，无记录车型按字段创建
        status = (coordinator.data or {}).get("vehiclestatus", {}) or {}
        if (
            capability.usable(
                status, [["doorStatus", "tailgateDoor"], ["doorStatus", "innerTailgateDoor"]]
            )
            and not coordinator.cmd_rejected(CMD_TRUNK_UNLOCK)
        ):
            locks.append(FordPassTrunkLock(coordinator))
        # v3.3.7: 前备箱锁——v5.2.4 起移除：开/关命令按子串匹配 + fallback
        # "first/last" 属猜测命令，未对照实测确认前不得下发
        # （待实测确认后恢复）。
    async_add_entities(locks)
    # v4.8.5: 实体订阅协调器刷新广播——每次刷新成功（即使车况未变）
    # async_write_ha_state，attrs 中 last_poll 每轮变化 → state_changed →
    # 实体 last_updated 每轮刷新（撤销"实体不随轮询刷新"）；失败轮不触发，
    # 保留旧值。
    try:

        def _mk_refresh(ent):
            def _refresh():
                # v4.8.6: hass 注入前（实体添加阶段）回调会被预热刷新触发，
                # 直接 async_write_ha_state 会 RuntimeError: hass is None
                if ent.hass is not None:
                    ent.async_write_ha_state()
            return _refresh

        for _ent in locks:
            _ent.async_on_remove(
                _ent.coordinator.async_add_listener(_mk_refresh(_ent))
            )
    except Exception:
        _LOGGER.debug("entity listener registration failed", exc_info=True)



class FordPassLock(FordPassRestoreMixin, LockEntity):
    """Lock entity controlling the vehicle doors."""

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.uid_key}-lock"
        self._attr_name = "门锁"
        self._attr_has_entity_name = True
        self._attr_device_info = coordinator.device_info
        self._attr_icon = "mdi:car-door-lock"
        # v5.8.3: 操作中的乐观目标状态（None=无操作，读真实快照）——
        # 门锁操作后立即显示目标状态，不再等 30s 轮询才有反馈；
        # 命令完成由真实快照纠正，失败回滚并如实报错。
        self._pending: bool | None = None

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)  # v4.8.7: 有最后已知数据即可用——修复控制实体 unavailable（restored 残留）

    @property
    def is_locked(self) -> bool:
        if self._pending is not None:  # v5.8.3: 操作中显示目标状态（乐观反馈）
            return self._pending
        status = (self.coordinator.data or {}).get("vehiclestatus", {})
        value = status.get("lockStatus", {}).get("value")
        return self._restore_bool(value == "LOCKED" if value else None)

    async def async_lock(self, **kwargs: Any) -> None:
        self._pending = True
        self.async_write_ha_state()
        try:
            await self.coordinator.run_command(CMD_LOCK)
        except Exception:
            self._pending = None
            self.async_write_ha_state()
            raise
        self._pending = None
        self.async_write_ha_state()

    async def async_unlock(self, **kwargs: Any) -> None:
        self._pending = False
        self.async_write_ha_state()
        try:
            await self.coordinator.run_command(CMD_UNLOCK)
        except Exception:
            self._pending = None
            self.async_write_ha_state()
            raise
        self._pending = None
        self.async_write_ha_state()

    async def async_update(self) -> None:
        # v3.8.0: 锁状态由 coordinator 定时刷新驱动，实体轮询不再强制
        # 全量刷新并阻塞等待——修复 "Updating fordpass_cn lock took longer
        # than the scheduled update interval" 警告（轮询周期 < 刷新周期时
        # 每次都要等全量刷新完成）。锁操作链路（run_command 内命令状态
        # 轮询 + force_refresh）独立，不受影响。
        # v5.8.5: HA 2026.10 的 async_request_refresh 为 async def，await 会
        # 等待整轮全量刷新完成——重启后 data=None 的初始窗口叠加系统忙/
        # 云端慢时（实测伴随 hassio Supervisor 超时），实体 update 挂起
        # 超 30s 调度间隔触发警告。改为 hass.async_create_task 后台调度，
        # 实体 update 立即返回（coordinator 自带去抖 + 30min 调度兜底）。
        if self.coordinator.data is None:
            self.hass.async_create_task(
                self.coordinator.async_request_refresh()
            )


class FordPassTrunkLock(FordPassRestoreMixin, LockEntity):
    """后备箱锁（v3.0.1，由 switch 改为 lock）。

    is_locked=True = 后备箱已锁定；is_locked=False = 已解锁可开启。
    解锁 = TrunkUnlock；锁定 = DoorLock（全车上锁，网关无独立后备箱锁命令）。
    初始状态从门锁状态推断，操作后按记忆状态显示。
    """

    _attr_assumed_state = True
    _attr_icon = "mdi:car-back"

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.uid_key}-trunk"
        self._attr_name = "后备箱锁"
        self._attr_has_entity_name = True
        self._attr_device_info = coordinator.device_info
        self._state: bool | None = None  # True=锁定

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)  # v4.8.7: 有最后已知数据即可用——修复控制实体 unavailable（restored 残留）

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
        return self._restore_bool(None)

    async def async_lock(self, **kwargs: Any) -> None:
        """锁定后备箱：全车上锁（网关无独立 TrunkLock 命令）。"""
        await self.coordinator.run_command(CMD_LOCK)
        self._state = True
        self.async_write_ha_state()

    async def async_unlock(self, **kwargs: Any) -> None:
        """解锁后备箱（TrunkUnlock）。"""
        await self.coordinator.run_command(CMD_TRUNK_UNLOCK)
        self._state = False
        self.async_write_ha_state()

