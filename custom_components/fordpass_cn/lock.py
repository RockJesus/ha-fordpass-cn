"""Lock platform: 后备箱锁（门锁已拆为 button 平台两个动作按钮）。

门锁（v6.4.3 变更）：原整车门锁 LockEntity 依赖云端 lockStatus 快照回读，
命令完成后快照仍可能滞后数十秒至数分钟，锁卡片显示的锁状态与实车不同步。
现改为 button 平台的「开锁」「关锁」两个动作按钮（见 button.py），不承载
状态；锁状态仍由「门锁状态」传感器（云端 lockStatus）提供。

后备箱锁（v3.1.10 恢复）：解锁 = TrunkUnlock（后备箱弹开可开启），锁定 =
DoorLock（全车上锁，网关无独立后备箱锁命令，后备箱随全车锁定）。实测
TrunkUnlock 被网关 228205「cmdSpec can not empty」拒绝（App 端该命令依赖
蓝牙 TIMA），但其他车型云端可能支持——全车型支持策略：登录后按车辆能力
（尾门/内尾门字段）判断创建，云端不支持的车型按下会返回网关明确报错。
"""
from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import capability
from .const import CMD_LOCK, CMD_TRUNK_UNLOCK
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
        # v6.4.3: 整车门锁已迁到 button 平台（开锁/关锁两个动作按钮），
        # 本平台只保留后备箱锁。
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

    # v6.4.3: 清理门锁拆分后的 registry 残留——旧 `-lock` 条目不再创建，
    # 留在注册表里会永远 unavailable。仅限本 entry（多账号互不影响），
    # 跳过用户手动禁用的条目（用户选择不可被集成覆盖）。
    reg = er.async_get(hass)
    new_ids = {ent.unique_id for ent in locks}
    for entry_ in er.async_entries_for_config_entry(reg, entry.entry_id):
        if not entry_.entity_id.startswith("lock."):
            continue
        if entry_.disabled_by == "user":
            continue
        if entry_.unique_id not in new_ids:
            _LOGGER.debug("lock cleanup: removing stale %s", entry_.entity_id)
            reg.async_remove(entry_.entity_id)


class FordPassTrunkLock(FordPassRestoreMixin, LockEntity):
    """后备箱锁（v3.0.1，由 switch 改为 lock）。

    is_locked=True = 后备箱已锁定；is_locked=False = 已解锁可开启。
    解锁 = TrunkUnlock；锁定 = DoorLock（全车上锁，网关无独立后备箱锁命令）。
    初始状态从门锁状态推断，操作后按记忆状态显示。
    """

    _attr_assumed_state = True
    _attr_icon = "mdi:car-back"
    # v6.1.6: 纯推送（同原门锁实体注释）——不继承 FordPassLock，需单列
    _attr_should_poll = False

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.uid_key}-trunk"
        self._attr_translation_key = "trunk"
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
