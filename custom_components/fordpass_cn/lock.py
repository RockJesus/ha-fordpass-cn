"""Lock platform: vehicle lock / unlock + 后备箱锁。

门锁：DoorLock / DoorUnlock。
后备箱锁（v3.1.10 恢复）：解锁 = TrunkUnlock（后备箱弹开可开启），锁定 =
DoorLock（全车上锁，网关无独立后备箱锁命令，后备箱随全车锁定）。实测
TrunkUnlock 被网关 228205「cmdSpec can not empty」拒绝（App 端该命令依赖
蓝牙 TIMA），但其他车型云端可能支持——全车型支持策略：登录后按车辆能力
（尾门/内尾门字段）判断创建，云端不支持的车型按下会返回网关明确报错。
"""
from __future__ import annotations

import asyncio
import logging
import time
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

# v6.3.7: lockStatus 云端既可能是 dict（{"value": "LOCKED"}），也可能是
# 标量 "LOCKED"/bool/1/0（sensor.py 的 enum_map 实证）。统一归一化——
# 直接 ``status.get("lockStatus", {}).get(...)`` 在标量时 AttributeError，
# 且 "1"/"0" 此前永远判为未锁定。
_LOCKED_VALUES = {"locked", "lock", "1", "true", "yes", "on"}
_UNLOCKED_VALUES = {"unlocked", "unlock", "0", "false", "no", "off"}
# 后备箱记忆状态有效期（秒）：超过后回落到云端快照纠正。
_TRUNK_STATE_TTL = 60


def _lock_state(status: dict | None) -> bool | None:
    """从未知结构的 lockStatus 解析出 True=锁定 / False=解锁 / None=未知。"""
    raw = capability.leaf(status, "lockStatus")
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if text in _LOCKED_VALUES:
        return True
    if text in _UNLOCKED_VALUES:
        return False
    return None


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
        # 如部分车型 228205 依赖蓝牙 TIMA）的车型不再创建，无记录车型按字段创建
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
    # async_write_ha_state；失败轮不触发，保留旧值。v5.9.8 起属性不再
    # 含每轮变化的 last_poll，值未变的实体不会产生 state_changed。
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

    # v6.1.6: 锁状态由协调器刷新广播驱动（v4.8.5），轮询从不产出新信息。
    # 此前默认每 30s 空轮询，且 PARALLEL_UPDATES=1 让轮询与锁操作服务调用
    # 共用一把信号量——锁命令链（等命令完成 + force_refresh，实测 60-90s）
    # 期间轮询轮被堵在 acquire() 上，触发「Updating fordpass_cn lock took
    # longer than the scheduled update interval」警告（11:25 实测复现）。
    # 关闭轮询后平台更新轮没有任何实体，该警告从机制上不可能再发生。
    _attr_should_poll = False

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
        # v6.3.4: HA lock 标准操作中属性——操作期间前端锁卡片显示
        # 「正在锁定/解锁」动画（对齐福特派 App 的执行中反馈）。
        self._attr_is_locking = False
        self._attr_is_unlocking = False
        # v6.3.7: 快照确认改后台任务（见 async_lock/unlock）——避免服务
        # 调用被 PARALLEL_UPDATES=1 的信号量堵住最长 60s+。
        self._confirm_task: asyncio.Task | None = None
        self.async_on_remove(self._cancel_confirm)

    def _set_pending(self, target: bool) -> None:
        """置操作中标记：乐观目标状态 + is_locking/is_unlocking 动画。"""
        self._pending = target
        self._attr_is_locking = bool(target)
        self._attr_is_unlocking = not bool(target)
        self.async_write_ha_state()

    def _clear_pending(self) -> None:
        self._pending = None
        self._attr_is_locking = False
        self._attr_is_unlocking = False
        self.async_write_ha_state()

    def _cancel_confirm(self) -> None:
        if self._confirm_task is not None:
            self._confirm_task.cancel()
            self._confirm_task = None

    async def _confirm_lock_snapshot(self, target: bool) -> bool:
        """v6.3.6: 命令链路结束后云端快照可能尚未翻转（实测最迟 ~60s）。

        保持乐观目标状态（_pending 不清），每 10s force_refresh 一次核对
        lockStatus，直到与目标一致——消除「乐观解除→读到旧快照→回跳→
        最长 30min 空窗」的不实时窗口；快照翻转后返回 True 由调用方清
        乐观标记（此时读快照即为新状态）。超时（6 次仍未翻转）返回 False，
        调用方照常 clear_pending 如实显示快照，由后台轮询兜底纠正——
        不无限乐观，避免命令实际失败时状态永久失真。
        """
        for _ in range(6):
            status = (self.coordinator.data or {}).get("vehiclestatus", {}) or {}
            if _lock_state(status) is target:
                return True
            await asyncio.sleep(10)
            try:
                await self.coordinator.force_refresh()
            except Exception as exc:  # noqa: BLE001 - 保留乐观状态，10s 后循环下一轮再试
                _LOGGER.debug("lock snapshot confirm refresh failed: %s", exc)
        return False

    def _schedule_confirm(self, target: bool) -> None:
        """v6.3.7: 后台确认快照并清乐观标记——服务调用立即返回。"""
        self._cancel_confirm()
        self._confirm_task = self.hass.async_create_task(self._async_confirm(target))

    async def _async_confirm(self, target: bool) -> None:
        try:
            await self._confirm_lock_snapshot(target)
        finally:
            # v6.3.7: 仅当本任务仍是当前确认任务时才清理——连续 lock→unlock
            # 时旧任务被取消后其 finally 可能晚于新任务启动，无条件清理会把
            # 新操作的乐观状态/动画清掉。
            if self._confirm_task is asyncio.current_task():
                self._confirm_task = None
                self._clear_pending()

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)  # v4.8.7: 有最后已知数据即可用——修复控制实体 unavailable（restored 残留）

    @property
    def is_locked(self) -> bool:
        if self._pending is not None:  # v5.8.3: 操作中显示目标状态（乐观反馈）
            return self._pending
        status = (self.coordinator.data or {}).get("vehiclestatus", {})
        return self._restore_bool(_lock_state(status))

    async def async_lock(self, **kwargs: Any) -> None:
        # P1-5: 新操作前先取消上一轮的快照确认任务——旧任务在新命令执行
        # 期间仍可能读到快照并合法翻转，清掉本次的乐观状态（竞态）。
        self._cancel_confirm()
        self._set_pending(True)
        try:
            await self.coordinator.run_command(CMD_LOCK)
        except Exception:
            self._clear_pending()
            raise
        # v6.3.7: 快照确认转后台任务——保持乐观态直到云端确认或超时，
        # 但不再阻塞本服务调用（原实现最长等 60s+ 且堵住后备箱锁）。
        self._schedule_confirm(True)

    async def async_unlock(self, **kwargs: Any) -> None:
        self._cancel_confirm()  # P1-5: 同 async_lock，先取消旧确认任务
        self._set_pending(False)
        try:
            await self.coordinator.run_command(CMD_UNLOCK)
        except Exception:
            self._clear_pending()
            raise
        self._schedule_confirm(False)

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
    # v6.1.6: 纯推送（同 FordPassLock 注释）——不继承 FordPassLock，需单列
    _attr_should_poll = False

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.uid_key}-trunk"
        self._attr_name = "后备箱锁"
        self._attr_has_entity_name = True
        self._attr_device_info = coordinator.device_info
        self._state: bool | None = None  # True=锁定
        # v6.3.7: 记忆状态的时效——超时后回落到云端快照，避免命令实际
        # 未生效时永久显示错误状态（此前 _state 永久记忆，不再纠正）。
        self._state_at: float | None = None
        # v6.3.4: 操作中动画（对齐 App 执行中反馈，同门锁）
        self._attr_is_locking = False
        self._attr_is_unlocking = False

    def _set_pending(self, target: bool) -> None:
        self._attr_is_locking = bool(target)
        self._attr_is_unlocking = not bool(target)
        self.async_write_ha_state()

    def _clear_pending(self) -> None:
        self._attr_is_locking = False
        self._attr_is_unlocking = False
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)  # v4.8.7: 有最后已知数据即可用——修复控制实体 unavailable（restored 残留）

    @property
    def is_locked(self) -> bool | None:
        # 优先返回记忆状态（用户操作后），但仅在有效期内（v6.3.7）——
        # 超时后回落到云端快照，命令未生效时也能被纠正。
        if self._state is not None and self._state_at is not None:
            if time.monotonic() - self._state_at < _TRUNK_STATE_TTL:
                return self._state
        # 初始/重启后从门锁状态推断：LOCKED → 锁定(True)，UNLOCKED → 解锁(False)
        status = (self.coordinator.data or {}).get("vehiclestatus", {})
        lock = _lock_state(status)
        if lock is not None:
            return lock
        return self._restore_bool(None)

    async def async_lock(self, **kwargs: Any) -> None:
        """锁定后备箱：全车上锁（网关无独立 TrunkLock 命令）。"""
        self._set_pending(True)
        try:
            await self.coordinator.run_command(CMD_LOCK)
        except Exception:
            self._clear_pending()
            raise
        self._state = True
        self._state_at = time.monotonic()
        self._clear_pending()

    async def async_unlock(self, **kwargs: Any) -> None:
        """解锁后备箱（TrunkUnlock）。"""
        self._set_pending(False)
        try:
            await self.coordinator.run_command(CMD_TRUNK_UNLOCK)
        except Exception:
            self._clear_pending()
            raise
        self._state = False
        self._state_at = time.monotonic()
        self._clear_pending()

