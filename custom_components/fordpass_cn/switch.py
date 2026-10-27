"""Switch platform: remote engine start + 鸣笛寻车。

v2.10.0: 灯光寻车开关（ZoneLightingON/OFF）——v3.1.8 移除：实测
send-command 网关返回 228205「cmdSpec can not empty」（该车型无此命令
规范），实体已删除，避免制造无反应的假实体。
v3.0.1: 后备箱锁已从 switch 迁移为 lock 实体（见 lock.py），此处仅保留远程启动与鸣笛寻车。
v3.0.5: 修复灯光寻车初始状态 unknown（默认关闭）；鸣笛寻车从 button 迁移为 switch。
v3.0.7: 鸣笛寻车真实双通道（按接口行为整理的命令协议）——开 = POST /api/vehicles/v5/{vin}/honk
（body: ChirpOrHonkDuration/IntervalBetweenRequests/ChirpType），关 = DELETE 同路径（App 的
FordHonkCancelCommand 通道）；触发后 30 秒自动复位。
v3.0.8: 鸣笛开关开启时读取「鸣笛持续时长 / 鸣笛类型」设置（select 实体，存于
config entry options），POST 参数直达车机——设置即生效（等效上传车机）。
v3.1.8: ChirpType 改为 App 枚举 0-4（此前 1-5 差一错位，类型设置无效的
根因）；「声光共舞」=4 走独立 panic 端点（POST /panic/{duration}，灯+喇叭
警报，实测车型 404 会明确报错）；其余 0-3 走 POST /honk。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import capability
from .const import (
    CHIRP_TO_TYPE,
    CMD_ENGINE_START,
    CMD_ENGINE_STOP,
    CMD_LIGHT_FIND_OFF,
    CMD_LIGHT_FIND_ON,
    CMD_VA_CANCEL,
    CMD_VA_INIT,
    CONF_CHIRP_TYPE,
    CONF_HONK_DURATION,
    DEFAULT_CHIRP_TYPE,
    DEFAULT_HONK_DURATION,
    HONK_AUTO_OFF_SECONDS,
    LIGHT_FIND_AUTO_OFF_SECONDS,
)
from .coordinator import FordPassCoordinator
from .sensor import FordPassRestoreMixin

_LOGGER = logging.getLogger(__name__)


PARALLEL_UPDATES = 1

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    # v3.1.6: 多 VIN——每辆车独立创建开关组（按各自车型能力过滤）
    payload = entry.runtime_data  # v5.3.0: runtime_data 替代 hass.data
    coordinators = payload.get("coordinators") or [payload["coordinator"]]
    switches: list[SwitchEntity] = []
    for coordinator in coordinators:
        status = (coordinator.data or {}).get("vehiclestatus", {}) or {}
        # v4.9.3: 远程启动开关按车型能力创建——remoteStartStatus 字段
        # 数据可用或云端命令白名单含 RemoteStart 才创建（纯电/无远程
        # 启动能力车型不创建，全车型正确性）
        _wl0 = coordinator.command_whitelist
        if capability.usable(status, [["remoteStartStatus"]]) or (
            isinstance(_wl0, list) and any("RemoteStart" in c for c in _wl0)
        ):
            switches.append(FordPassEngineSwitch(coordinator))
        # 远程控车能力 crccFlag 有效才创建寻车类开关
        if capability.usable(status, [["crccFlag"]]):
            # v3.1.10: 恢复灯光寻车（v3.1.8 曾因实测网关 228205 移除；
            # 其他车型云端可能支持——登录后按能力创建）
            # v5.8.3: 按车型创建——灯光寻车命令被云端确定性拒绝（实测
            # 失败）的车型不再创建，无记录车型按能力创建
            if not coordinator.cmd_rejected(CMD_LIGHT_FIND_ON):
                switches.append(FordPassLightSwitch(coordinator))
            # 鸣笛寻车（v3.0.5 由按钮迁移为开关）
            switches.append(FordPassHonkSwitch(coordinator))
            # v3.3.3: 声光寻车（鸣笛+灯光）开关——由 v3.2.1 的两个按钮
            # （InitialVA/CancelVA）合并；开 = send-command InitialVA
            # （VAType=4 + Duration，App 官方声光共舞通道），关 = CancelVA
            switches.append(FordPassVAswitch(coordinator))
        # v3.3.7: 哨兵模式/立即充电/车载冰箱白名单开关——v5.2.4 起移除：
        # 开/关命令按子串匹配 + "first for on, last for off" 兜底属猜测
        # 命令，未对照实测确认前不得下发（# 要求移除，待确认后恢复）。
        # v3.5.0: 预约充电/预约出发开关——departuretimes/retrieve 探测有数据
        # 才创建（支持该功能的车型自动出现；toggle 参数随探测结果自动带）
        _ep = coordinator.extra_probes
        if isinstance(_ep, dict) and _ep.get("departuretimes"):
            switches.append(FordPassDepartureSwitch(coordinator))
        # v3.7.2: 家充桩充电开关——smartwallbox 探测到绑定桩才创建
        # （纯电/插混家充桩车型自动出现；开=charging/start、关=charging/stop）
        if isinstance(coordinator.smartwallbox, dict) and coordinator.smartwallbox:
            switches.append(FordPassWallboxChargeSwitch(coordinator))
    async_add_entities(switches)
    # v4.8.5: 实体订阅协调器刷新广播——每次刷新成功（即使车况未变）
    # async_write_ha_state；失败轮不触发，保留旧值。v5.10.0 起属性不再
    # 含每轮变化的 last_poll，值未变的实体不会产生 state_changed。
    try:

        def _mk_refresh(ent):
            def _refresh():
                # v4.8.6: hass 注入前（实体添加阶段）回调会被预热刷新触发，
                # 直接 async_write_ha_state 会 RuntimeError: hass is None
                if ent.hass is not None:
                    ent.async_write_ha_state()
            return _refresh

        for _ent in switches:
            _ent.async_on_remove(
                _ent.coordinator.async_add_listener(_mk_refresh(_ent))
            )
    except Exception:
        _LOGGER.debug("entity listener registration failed", exc_info=True)



class FordPassTaskTracker:
    """后台任务引用跟踪（v5.3.5，v5.4.5 提取为共享 mixin）——命令状态轮询/
    自动复位等 fire-and-forget 任务挂到 coordinator._persist_tasks，防卸载/
    重启竞态下被事件循环丢弃（丢弃后果：开关状态不自动复位、命令状态传感器
    不更新）。VAswitch/HonkSwitch 共用（HonkSwitch 曾漏继承导致鸣笛后
    AttributeError: no attribute '_track_task'）。"""

    def _track_task(self, coro: Any) -> asyncio.Task:
        task = self.coordinator.hass.async_create_task(coro)
        self.coordinator._persist_tasks.add(task)
        task.add_done_callback(self.coordinator._persist_tasks.discard)
        return task


class FordPassMomentarySwitch(FordPassTaskTracker):
    """瞬时命令开关共用基类（v5.8.10）——灯光寻车 / 鸣笛寻车 / 声光寻车。

    三者行为一致：命令是一次性触发（车只响应一小段时间后自行停止），云端
    不回报「灯正亮着 / 喇叭正响着」这类状态，因此 is_on 只能是乐观值
    （_attr_assumed_state = True，HA 前端据此渲染成两个闪电按钮而非开关），
    并在触发后按设定时长自动复位显示。

    v5.8.10 修复两处：
      1. 灯光寻车此前**没有任何自动复位**——点一次就永久停在「开」，与
         鸣笛/声光不一致；现统一走 _schedule_auto_off()。
      2. 声光寻车读的是 entry.options（默认 15 秒），而鸣笛寻车读内存
         honk_settings（默认 10 秒）。select 自 v4.3.1 起只写内存，options
         仅作旧版残留兜底 → 用户改了「鸣笛持续时长」后只有鸣笛生效，声光
         仍按旧值/默认值停。现统一由 _settings() 取同一来源。

    另外 _schedule_auto_off() 会取消上一轮未到点的复位任务——连续点两次时
    旧计时器不会提前把开关打回「关」。
    """

    coordinator: FordPassCoordinator
    _state: bool
    _auto_off_task: asyncio.Task | None = None

    def _settings(self) -> tuple[int, str]:
        """(持续时长秒, 鸣笛类型中文名)：内存 honk_settings → options → 默认常量。

        内存是权威来源（select v4.3.1 起只写内存，避免 update_listener 噪音
        与调度设置被重置）；options 兜底旧版安装的残留值。
        """
        settings: dict[str, Any] = {}
        entry_id = getattr(self.coordinator, "entry_id", None)
        if entry_id:
            payload = getattr(self.coordinator.config_entry, "runtime_data", None) or {}
            settings = payload.get("honk_settings") or {}
        options: dict[str, Any] = {}
        if entry_id:
            entry = self.coordinator.hass.config_entries.async_get_entry(entry_id)
            options = entry.options if entry else {}
        try:
            duration = int(
                settings.get(CONF_HONK_DURATION)
                or options.get(CONF_HONK_DURATION)
                or DEFAULT_HONK_DURATION
            )
        except (TypeError, ValueError):
            duration = DEFAULT_HONK_DURATION
        chirp_name = (
            settings.get(CONF_CHIRP_TYPE)
            or options.get(CONF_CHIRP_TYPE)
            or DEFAULT_CHIRP_TYPE
        )
        return duration, str(chirp_name)

    def _duration(self) -> int:
        """本次触发的持续时长（秒）。"""
        return self._settings()[0]

    def _cancel_auto_off(self) -> None:
        """取消未到点的自动复位任务（手动关闭或再次触发时调用）。"""
        task, self._auto_off_task = self._auto_off_task, None
        if task is not None and not task.done():
            task.cancel()

    def _schedule_auto_off(self, duration: int | None = None) -> None:
        """安排自动复位；先取消上一轮的，避免旧计时器提前打断。"""
        self._cancel_auto_off()
        self._auto_off_task = self._track_task(self._auto_off(duration))

    async def _auto_off(self, duration: int | None = None) -> None:
        """按本次触发时长自动复位显示；时长缺失/非法回退 HONK_AUTO_OFF_SECONDS。

        被 _cancel_auto_off() 取消时不复位（取消场景下状态由调用方自己写）。
        """
        try:
            await asyncio.sleep(
                duration if isinstance(duration, int) and duration > 0 else HONK_AUTO_OFF_SECONDS
            )
        finally:
            # 被取消时 CancelledError 会在此继续抛出，下面的状态复位不会执行
            if self._auto_off_task is asyncio.current_task():
                self._auto_off_task = None
        self._state = False
        self.async_write_ha_state()

    @staticmethod
    def _command_id(resp: Any) -> str | None:
        """从（已解密的）send-command 响应里取 commandId。"""
        if isinstance(resp, dict):
            cid = resp.get("commandId")
            if cid:
                return str(cid)
            inner = resp.get("data")
            if isinstance(inner, dict) and inner.get("commandId"):
                return str(inner["commandId"])
        return None

    async def _poll_cmd_status(self, command_type: str, command_id: str) -> None:
        """轮询 command-execution-status，结果写入「鸣笛命令状态」传感器（尽力而为）。"""
        self.coordinator.announce_status = {
            "command_id": command_id, "status": "执行中", "command_type": command_type,
        }
        try:
            done, result = await self.coordinator.api.wait_command_complete(
                self.coordinator.vin, command_id, command_type
            )
            if done and isinstance(result, dict):
                st = result.get("vehiclestatus", result)
                st.setdefault("command_id", command_id)
                st.setdefault("command_type", command_type)
                self.coordinator.announce_status = st
            else:
                self.coordinator.announce_status = {
                    "command_id": command_id,
                    "status": "超时或未完成",
                    "command_type": command_type,
                    "error": "command-execution-status 轮询超时（命令可能已执行完成）",
                }
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("FordPass %s 状态轮询失败: %s", command_type, exc)
            self.coordinator.announce_status = {
                "command_id": command_id,
                "status": "查询失败",
                "command_type": command_type,
                "error": str(exc),
            }
        self.coordinator.async_update_listeners()


class FordPassWallboxChargeSwitch(FordPassRestoreMixin, SwitchEntity):
    """家充桩充电开关（v3.7.2）——开=charging/start/v2、关=charging/stop/v2。

    仅 smartwallbox 探测到绑定桩的车型创建（纯电/插混家充桩车型）；
    wallboxId 从探测结果 wallbox_binding 提取默认桩。assumed_state：
    按下即翻转，云端结果如实写入「家充桩充电状态」传感器（若存在）。
    """

    _attr_assumed_state = True
    _attr_icon = "mdi:ev-station"

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.uid_key}-wallbox_charge"
        self._attr_name = "家充桩充电"
        self._attr_has_entity_name = True
        self._attr_device_info = coordinator.device_info
        self._state = False

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)  # v4.8.7: 有最后已知数据即可用——修复控制实体 unavailable（restored 残留）

    @property
    def is_on(self) -> bool:
        return self._restore_bool(self._state)

    def _wallbox_id(self) -> str | None:
        """从探测结果取默认桩 id。"""
        wb = self.coordinator.smartwallbox or {}
        bind = wb.get("wallbox_binding")
        data = bind.get("data") if isinstance(bind, dict) else None
        if not isinstance(data, dict):
            return None
        for key in ("wallboxId", "defaultWallboxId", "id", "equipmentId"):
            v = data.get(key)
            if v:
                return str(v)
        lst = data.get("list") or data.get("wallboxList")
        if isinstance(lst, list) and lst and isinstance(lst[0], dict):
            for key in ("wallboxId", "id", "equipmentId", "serialNumber"):
                v = lst[0].get(key)
                if v:
                    return str(v)
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        wid = self._wallbox_id()
        if not wid:
            # v5.8.10: 此前完全静默（点了没反应也无日志），改为留痕
            _LOGGER.warning("FordPass 家充桩充电（开）跳过：探测结果里没有可用的 wallboxId")
            self._state = False
            self.async_write_ha_state()
            return
        try:
            await self.coordinator.api.wallbox_charge_start(self.coordinator.vin, wid)
            self._state = True
        except Exception as exc:  # noqa: BLE001 - 网关拒绝时如实提示
            # v5.8.10: 原为纯静默吞掉，用户只看到开关弹回、无任何提示
            _LOGGER.warning("FordPass 家充桩充电（开）失败: %s", exc)
            self._state = False
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        wid = self._wallbox_id()
        if not wid:
            _LOGGER.warning("FordPass 家充桩充电（关）跳过：探测结果里没有可用的 wallboxId")
            self._state = False
            self.async_write_ha_state()
            return
        try:
            await self.coordinator.api.wallbox_charge_stop(self.coordinator.vin, wid)
            self._state = False
        except Exception as exc:  # noqa: BLE001 - 网关拒绝时如实提示
            _LOGGER.warning("FordPass 家充桩充电（关）失败: %s", exc)
            self._state = True
        self.async_write_ha_state()


class FordPassDepartureSwitch(FordPassRestoreMixin, SwitchEntity):
    """预约充电/预约出发开关（v3.5.0）：POST departuretimes/toggleon|off。

    departuretimes/retrieve 探测有数据才创建；状态取探测结果中的
    switchStatus/toggleStatus/enabled 等字段（字段缺失时 assumed_state，
    按下后乐观更新）。参数中带 departureTimesColumUpdateId（探测结果第一
    个任务 id），服务端按具体任务切换；非该能力车型不创建。
    """

    _attr_assumed_state = True

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.uid_key}-departure_toggle"
        self._attr_name = "预约充电"
        self._attr_has_entity_name = True
        self._attr_icon = "mdi:calendar-clock"
        self._attr_device_info = coordinator.device_info
        self._state: bool | None = None
        self._task_id: str | None = None

    def _probe(self) -> dict | None:
        ep = self.coordinator.extra_probes or {}
        return ep.get("departuretimes") if isinstance(ep, dict) else None

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)  # v4.8.7: 有最后已知数据即可用——修复控制实体 unavailable（restored 残留）

    @property
    def is_on(self) -> bool | None:
        return self._restore_bool(self._state)

    async def async_update(self) -> None:
        # v5.8.7: 与 lock v5.8.5 同源——HA 2026.10 重启/重载初始窗口
        # （coordinator.data=None）叠加云端慢时，实体 update 挂起会触发
        # "Updating fordpass_cn switch took longer than the scheduled
        # update interval 0:00:30" 警告。data 未就绪时后台调度刷新并
        # 立即返回（coordinator 自带去抖 + 30min 调度兜底）。
        if self.coordinator.data is None:
            self.hass.async_create_task(
                self.coordinator.async_request_refresh()
            )
            return
        data = self._probe()
        if not isinstance(data, dict):
            return
        payload = data.get("data") if isinstance(data, dict) else None
        if not isinstance(payload, dict):
            return
        for k in ("switchStatus", "toggleStatus", "enabled", "status", "onOffStatus"):
            v = payload.get(k)
            if v is not None:
                self._state = str(v).lower() in ("1", "true", "on", "yes", "open")
                break
        lst = payload.get("list") or payload.get("departureTimes") or payload.get("tasks")
        if isinstance(lst, list) and lst and isinstance(lst[0], dict):
            self._task_id = str(lst[0].get("departureTimesColumUpdateId")
                               or lst[0].get("id") or "")

    async def async_turn_on(self) -> None:
        await self.coordinator.api.departure_toggle(
            self.coordinator.vin, True, self._task_id or None
        )
        self._state = True
        self.async_write_ha_state()

    async def async_turn_off(self) -> None:
        await self.coordinator.api.departure_toggle(
            self.coordinator.vin, False, self._task_id or None
        )
        self._state = False
        self.async_write_ha_state()


class FordPassEngineSwitch(FordPassRestoreMixin, SwitchEntity):
    """Remote engine start / stop."""

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.uid_key}-engine"
        self._attr_name = "远程启动"
        self._attr_has_entity_name = True
        self._attr_device_info = coordinator.device_info
        self._attr_icon = "mdi:engine"

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)  # v4.8.7: 有最后已知数据即可用——修复控制实体 unavailable（restored 残留）

    @property
    def is_on(self) -> bool:
        status = (self.coordinator.data or {}).get("vehiclestatus", {})
        value = status.get("remoteStartStatus", {}).get("value")
        return self._restore_bool(bool(value) if value is not None else None)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.run_command(CMD_ENGINE_START)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.run_command(CMD_ENGINE_STOP)


class FordPassVAswitch(FordPassMomentarySwitch, FordPassRestoreMixin, SwitchEntity):
    """声光寻车开关（v3.3.3）——由「声光寻车触发/取消」两按钮合并。

    App 的声光寻车（鸣笛+灯光共舞，VAType=4=panic）走 send-command 通道：
      - 开 = InitialVA  cmdSpec [{VAType:4}, {Duration:<秒>}] → commandId=26
      - 关 = CancelVA   cmdSpec [{VAType:4}]                → commandId=476
    触发后车机按 Duration 自动停止，开关同步按设置时长自动复位（v5.8.10 起
    与鸣笛寻车同源读取 honk_settings；此前读 entry.options 默认 15 秒，改
    设置后声光不跟随）。
    若车型/车机对 CancelVA 无响应（取消无效），命令状态会如实写入
    「鸣笛命令状态」传感器供诊断——为网关/车型行为，非参数错误。
    """

    _attr_assumed_state = True
    _attr_icon = "mdi:lightbulb-on"

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.uid_key}-va_switch"
        self._attr_name = "声光寻车"
        self._attr_has_entity_name = True
        self._attr_device_info = coordinator.device_info
        self._state: bool = False

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)  # v4.8.7: 有最后已知数据即可用——修复控制实体 unavailable（restored 残留）

    @property
    def is_on(self) -> bool:
        return self._restore_bool(self._state)

    async def async_turn_on(self, **kwargs: Any) -> None:
        duration = self._duration()
        try:
            resp = await self.coordinator.api.va_command(
                self.coordinator.vin, CMD_VA_INIT, vatype=4, duration=duration
            )
        except Exception as exc:
            _LOGGER.warning("FordPass 声光寻车（开）失败: %s", exc)
            raise
        cid = self._command_id(resp)
        if cid:
            _LOGGER.info("FordPass 声光寻车已触发 commandId=%s（时长=%ss）", cid, duration)
            self._track_task(self._poll_cmd_status(CMD_VA_INIT, cid))
        self._state = True
        self.async_write_ha_state()
        # v5.8.10: 统一走 _schedule_auto_off（内部先取消上一轮计时器）
        self._schedule_auto_off(duration)

    async def async_turn_off(self, **kwargs: Any) -> None:
        try:
            resp = await self.coordinator.api.va_command(
                self.coordinator.vin, CMD_VA_CANCEL, vatype=4
            )
        except Exception as exc:
            _LOGGER.warning("FordPass 声光寻车（关）失败: %s", exc)
            raise
        cid = self._command_id(resp)
        if cid:
            _LOGGER.info("FordPass 声光寻车已取消 commandId=%s", cid)
            self._track_task(self._poll_cmd_status(CMD_VA_CANCEL, cid))
        self._cancel_auto_off()
        self._state = False
        self.async_write_ha_state()


class FordPassLightSwitch(FordPassMomentarySwitch, FordPassRestoreMixin, SwitchEntity):
    """灯光寻车开关（ZoneLightingON / ZoneLightingOFF）。

    灯光寻车是瞬时命令、车辆不回报灯光状态，开关状态为记忆值（assumed）。
    v3.0.5: 初始状态默认关闭（False），避免实体显示 unknown。
    v3.1.8: 曾因实测网关 228205「cmdSpec can not empty」临时移除——App 端
    该命令依赖蓝牙 TIMA 通道，云端 send-command 网关仅部分车型支持。
    v3.1.10: 恢复创建（全车型支持策略：登录后按车型能力判断，云端支持的
    车型可用；不支持时按下返回网关明确报错，如实提示）。
    v5.8.10: 补上缺失的自动复位——此前触发后 _state 永远停在 True（开关
    长期显示「开」，与鸣笛/声光行为不一致）。ZoneLightingON 不带时长参数，
    这里按 LIGHT_FIND_AUTO_OFF_SECONDS 复位**显示状态**，不下发 OFF 命令
    （灯由车机自行熄灭，额外发命令只会打扰）。
    """

    _attr_assumed_state = True
    _attr_icon = "mdi:car-light-high"

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.uid_key}-light_find"
        self._attr_name = "灯光寻车"
        self._attr_has_entity_name = True
        self._attr_device_info = coordinator.device_info
        self._state: bool = False

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)  # v4.8.7: 有最后已知数据即可用——修复控制实体 unavailable（restored 残留）

    @property
    def is_on(self) -> bool:
        return self._restore_bool(self._state)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.run_command(CMD_LIGHT_FIND_ON)
        self._state = True
        self.async_write_ha_state()
        self._schedule_auto_off(LIGHT_FIND_AUTO_OFF_SECONDS)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.run_command(CMD_LIGHT_FIND_OFF)
        self._cancel_auto_off()
        self._state = False
        self.async_write_ha_state()


class FordPassHonkSwitch(FordPassMomentarySwitch, FordPassRestoreMixin, SwitchEntity):
    """鸣笛寻车开关（v3.0.7，真实开/关双通道）。

    开 = 车辆 v5 鸣笛通道（ChirpOrHonkDuration / IntervalBetweenRequests /
    ChirpType 参数直达车机）——对应 App 的 FordHonkCommand；
    关 = 同路径取消（无 body）——对应 App 的 FordHonkCancelCommand。
    开关在触发后按本次「鸣笛持续时长」自动复位（v3.3.6: 跟随设置时长
    而非固定 30 秒；时长缺失时回退 HONK_AUTO_OFF_SECONDS）。

    ChirpType 为 App 枚举 0-4（v3.1.8 修正，此前 1-5 差一错位——类型设置
    无效的根因）；「声光共舞」=4 与「声光寻车」同走 send-command 的
    InitialVA（VAType=4）通道。
    """

    _attr_assumed_state = True
    _attr_icon = "mdi:bullhorn"

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.uid_key}-honk"
        self._attr_name = "鸣笛寻车"
        self._attr_has_entity_name = True
        self._attr_device_info = coordinator.device_info
        self._state: bool = False

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)  # v4.8.7: 有最后已知数据即可用——修复控制实体 unavailable（restored 残留）

    @property
    def is_on(self) -> bool:
        return self._restore_bool(self._state)

    async def async_turn_on(self, **kwargs: Any) -> None:
        # v3.0.8: 鸣笛参数来自设置实体——持续时长/鸣笛类型直达车机
        duration, chirp_name = self._settings()
        chirp_type = CHIRP_TO_TYPE.get(chirp_name, 2)  # 0-4，App 枚举
        try:
            if chirp_type == 4:
                # v3.2.1: 声光共舞改走 send-command InitialVA（实机样本实证 200 +
                # commandId=26）——App 的「鸣笛寻车」即此通道（VAType=4=panic
                # 灯+喇叭）；V5 /panic 端点实测返回 404，不再使用。
                _LOGGER.info(
                    "FordPass 声光共舞（InitialVA VAType=4）触发 duration=%ss", duration,
                )
                resp = await self.coordinator.api.va_command(
                    self.coordinator.vin, CMD_VA_INIT, vatype=4, duration=duration
                )
                cid = self._command_id(resp)
                if cid:
                    _LOGGER.info(
                        "FordPass 声光共舞已触发 commandId=%s（时长=%ss）",
                        cid, duration,
                    )
                    self._track_task(self._poll_cmd_status(CMD_VA_INIT, cid))
            else:
                resp = await self.coordinator.api.honk_command(
                    self.coordinator.vin,
                    duration=duration,
                    interval=1,
                    chirp_type=chirp_type,
                )
        except Exception as exc:
            _LOGGER.warning("FordPass 鸣笛寻车失败（类型=%s）: %s", chirp_name, exc)
            raise
        # 类型 0-3（V5 honk 通道）：commandId 走 V5 announcestatus 轮询；
        # 类型 4（InitialVA）已在分支内用 command-execution-status 轮询，跳过。
        if chirp_type != 4 and isinstance(resp, dict) and resp.get("commandId"):
            _LOGGER.info(
                "FordPass 鸣笛寻车已触发 commandId=%s（时长=%ss 类型=%s）",
                resp["commandId"], duration, chirp_name,
            )
            # v3.1.13: 鸣笛后轮询 announcestatus/{commandId} 查询命令执行
            # 结果（App FordRemoteControlApiService v5 轮询组），写入
            # coordinator.announce_status 供「鸣笛命令状态」传感器读取。
            self._track_task(self._poll_announce(self.coordinator.vin, resp["commandId"]))
        self._state = True
        self.async_write_ha_state()
        # 按本次触发时长自动结束，开关同步复位（v3.3.6: 跟随设置时长而非固定 30s）
        self._schedule_auto_off(duration)

    async def _poll_announce(self, vin: str, command_id: str) -> None:
        """轮询 v5 announcestatus/{commandId}（2s/5s/10s 三次，尽力而为）。

        结果写入 coordinator.announce_status；全部失败置 error 对象，
        传感器仍显示「查询失败」而非不可用（v3.1.2 规则）。
        """
        self.coordinator.announce_status = {"command_id": command_id, "status": "执行中"}
        for delay in (2, 5, 10):
            await asyncio.sleep(delay)
            try:
                st = await self.coordinator.api.announcestatus(vin, command_id)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("FordPass announcestatus 查询失败（%ss 后重试）: %s", delay, exc)
                continue
            if isinstance(st, dict) and st:
                st.setdefault("command_id", command_id)
                self.coordinator.announce_status = st
                self.coordinator.async_update_listeners()
                return
        self.coordinator.announce_status = {
            "command_id": command_id,
            "status": "查询超时",
            "error": "announcestatus 三次查询均失败（鸣笛命令可能已执行完成）",
        }
        self.coordinator.async_update_listeners()

    async def async_turn_off(self, **kwargs: Any) -> None:
        # 真实停止通道：类型 0-3 = 车辆 v5 鸣笛取消（与 App 取消鸣笛一致）；
        # 类型 4（声光共舞）= send-command CancelVA（App 取消声光寻车即此通道）。
        _, chirp_name = self._settings()
        chirp_type = CHIRP_TO_TYPE.get(chirp_name, 2)
        try:
            if chirp_type == 4:
                resp = await self.coordinator.api.va_command(
                    self.coordinator.vin, CMD_VA_CANCEL, vatype=4
                )
                cid = self._command_id(resp)
                if cid:
                    _LOGGER.info("FordPass 声光共舞已取消 commandId=%s", cid)
                    self._track_task(self._poll_cmd_status(CMD_VA_CANCEL, cid))
            else:
                resp = await self.coordinator.api.honk_cancel_command(self.coordinator.vin)
        except Exception as exc:
            _LOGGER.warning("FordPass 鸣笛寻车（关）失败: %s", exc)
            raise
        if isinstance(resp, dict) and resp.get("commandId"):
            _LOGGER.info("FordPass 鸣笛寻车已停止 commandId=%s", resp["commandId"])
        self._cancel_auto_off()
        self._state = False
        self.async_write_ha_state()
