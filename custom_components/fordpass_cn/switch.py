"""Switch platform: remote engine start + 鸣笛寻车。

v2.10.0: 灯光寻车开关（ZoneLightingON/OFF）——v3.1.8 移除：锐际实测
send-command 网关返回 228205「cmdSpec can not empty」（该车型无此命令
规范），实体已删除，避免制造无反应的假实体。
v3.0.1: 后备箱锁已从 switch 迁移为 lock 实体（见 lock.py），此处仅保留远程启动与鸣笛寻车。
v3.0.5: 修复灯光寻车初始状态 unknown（默认关闭）；鸣笛寻车从 button 迁移为 switch。
v3.0.7: 鸣笛寻车真实双通道（静态逆向还原 App 协议）——开 = POST /api/vehicles/v5/{vin}/honk
（body: ChirpOrHonkDuration/IntervalBetweenRequests/ChirpType），关 = DELETE 同路径（App 的
FordHonkCancelCommand 通道）；触发后 30 秒自动复位。
v3.0.8: 鸣笛开关开启时读取「鸣笛持续时长 / 鸣笛类型」设置（select 实体，存于
config entry options），POST 参数直达车机——设置即生效（等效上传车机）。
v3.1.8: ChirpType 改为 App 枚举 0-4（此前 1-5 差一错位，类型设置无效的
根因）；「声光共舞」=4 走独立 panic 端点（POST /panic/{duration}，灯+喇叭
警报，锐际 404 会明确报错）；其余 0-3 走 POST /honk。
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
    DOMAIN,
    HONK_AUTO_OFF_SECONDS,
)
from .coordinator import FordPassCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    # v3.1.6: 多 VIN——每辆车独立创建开关组（按各自车型能力过滤）
    payload = hass.data[DOMAIN][entry.entry_id]
    coordinators = payload.get("coordinators") or [payload["coordinator"]]
    switches: list[SwitchEntity] = []
    for coordinator in coordinators:
        switches.append(FordPassEngineSwitch(coordinator))
        # 远程控车能力 crccFlag 有效才创建寻车类开关
        status = coordinator.data.get("vehiclestatus", {}) or {}
        if capability.usable(status, [["crccFlag"]]):
            # v3.1.10: 恢复灯光寻车（v3.1.8 曾因锐际网关 228205 移除；
            # 其他车型云端可能支持——登录后按能力创建，按下报错即如实提示）
            switches.append(FordPassLightSwitch(coordinator))
            # 鸣笛寻车（v3.0.5 由按钮迁移为开关）
            switches.append(FordPassHonkSwitch(coordinator))
            # v3.3.3: 声光寻车（鸣笛+灯光）开关——由 v3.2.1 的两个按钮
            # （InitialVA/CancelVA）合并；开 = send-command InitialVA
            # （VAType=4 + Duration，App 官方声光共舞通道），关 = CancelVA
            switches.append(FordPassVAswitch(coordinator))
        # v3.3.7: 云端命令白名单驱动——车型专属开关按各自白名单自动创建
        # （电马哨兵/充电、猛禽/领裕车载冰箱等；锐际白名单无则自动跳过）
        wl = coordinator.command_whitelist
        if isinstance(wl, list):
            if _wl_matches(wl, "Sentry"):
                switches.append(_FordPassWlSwitch(
                    coordinator, "sentry", "哨兵模式", "mdi:shield-car",
                    *_wl_pair(wl, "Sentry")))
            if _wl_matches(wl, "Charge"):
                switches.append(_FordPassWlSwitch(
                    coordinator, "charge", "立即充电", "mdi:ev-station",
                    *_wl_pair(wl, "Charge")))
            if _wl_matches(wl, "Fridge"):
                switches.append(_FordPassWlSwitch(
                    coordinator, "fridge", "车载冰箱", "mdi:fridge",
                    *_wl_pair(wl, "Fridge")))
    async_add_entities(switches)


def _wl_matches(wl: list[str] | None, *kws: str) -> list[str]:
    """白名单中匹配任一关键字的命令列表。"""
    if not wl:
        return []
    return [c for c in wl if any(k in c for k in kws)]


def _wl_pair(cmds: list[str], *kws: str) -> tuple[str | None, str | None]:
    """尽力返回 (on_cmd, off_cmd)：含 ON/Start/Enable 视为开、OFF/Stop/Disable 视为关；
    无法区分时开=首个、关=末个（命令参数未知时按下报错即如实提示车型是否支持）。"""
    matched = _wl_matches(cmds, *kws)
    if not matched:
        return None, None
    on = next((c for c in matched if any(k in c for k in ("ON", "Start", "Enable"))), None)
    off = next((c for c in matched if any(k in c for k in ("OFF", "Stop", "Disable"))), None)
    if on is None:
        on = matched[0]
    if off is None:
        off = matched[-1]
    return on, off


class _FordPassWlSwitch(SwitchEntity):
    """白名单驱动的通用开关（v3.3.7，车型专属功能）。

    仅在云端命令白名单包含对应命令时创建；开/关分别发送匹配命令，
    命令无参数（网关参数格式未知时按下返回错误即如实提示）。
    """

    _attr_assumed_state = True

    def __init__(
        self,
        coordinator: FordPassCoordinator,
        suffix: str,
        name: str,
        icon: str,
        on_cmd: str | None,
        off_cmd: str | None,
    ) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-{suffix}"
        self._attr_name = name
        self._attr_has_entity_name = False
        self._attr_icon = icon
        self._attr_device_info = coordinator.device_info
        self._on_cmd = on_cmd
        self._off_cmd = off_cmd
        self._state: bool = False

    @property
    def available(self) -> bool:
        return True  # v3.1.2: 不随福特云刷新失败而不可用（保留最后已知状态）

    @property
    def is_on(self) -> bool:
        return self._state

    async def async_turn_on(self, **kwargs: Any) -> None:
        if not self._on_cmd:
            raise RuntimeError(f"{self._attr_name}：该车型云端不支持开启命令")
        await self.coordinator.run_command(self._on_cmd)
        self._state = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        if not self._off_cmd:
            raise RuntimeError(f"{self._attr_name}：该车型云端不支持关闭命令")
        await self.coordinator.run_command(self._off_cmd)
        self._state = False
        self.async_write_ha_state()


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
        return True  # v3.1.2: 不随福特云刷新失败而不可用（保留最后已知状态）

    @property
    def is_on(self) -> bool:
        status = self.coordinator.data.get("vehiclestatus", {})
        value = status.get("remoteStartStatus", {}).get("value")
        return bool(value) if value is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.run_command(CMD_ENGINE_START)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.run_command(CMD_ENGINE_STOP)


class FordPassVAswitch(SwitchEntity):
    """声光寻车开关（v3.3.3）——由「声光寻车触发/取消」两按钮合并。

    HAR 实证（2026-10-03，福特派 6.16.0）：App 的声光寻车（鸣笛+灯光
    共舞，VAType=4=panic）走 send-command 通道：
      - 开 = InitialVA  cmdSpec [{VAType:4}, {Duration:<秒>}] → 200+commandId=26
      - 关 = CancelVA   cmdSpec [{VAType:4}]                → 200+commandId=476
    触发后车机按 Duration 自动停止，开关同步按设置时长自动复位
    （v3.3.6: 跟随「鸣笛持续时长」设置，不再固定 30 秒）。
    若车型/车机对 CancelVA 无响应（取消无效），命令状态会如实写入
    「鸣笛命令状态」传感器供诊断——为网关/车型行为，非参数错误。
    """

    _attr_assumed_state = True
    _attr_icon = "mdi:lightbulb-on"

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-va_switch"
        self._attr_name = "声光寻车"
        self._attr_has_entity_name = False
        self._attr_device_info = coordinator.device_info
        self._state: bool = False

    @property
    def available(self) -> bool:
        return True  # v3.1.2: 不随福特云刷新失败而不可用（保留最后已知状态）

    @property
    def is_on(self) -> bool:
        return self._state

    def _duration(self) -> int:
        """读取鸣笛持续时长设置（与鸣笛寻车同源：config entry options）。"""
        options: dict = {}
        entry_id = getattr(self.coordinator, "entry_id", None)
        if entry_id:
            entry = self.coordinator.hass.config_entries.async_get_entry(entry_id)
            options = entry.options if entry else {}
        try:
            return int(options.get(CONF_HONK_DURATION, 15) or 15)
        except (TypeError, ValueError):
            return 15

    @staticmethod
    def _command_id(resp: Any) -> str | None:
        if isinstance(resp, dict):
            cid = resp.get("commandId")
            if cid:
                return str(cid)
            inner = resp.get("data")
            if isinstance(inner, dict) and inner.get("commandId"):
                return str(inner["commandId"])
        return None

    async def _poll_cmd_status(self, command_type: str, command_id: str) -> None:
        """send-command 轮询 command-execution-status → 「鸣笛命令状态」传感器。"""
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

    async def _auto_off(self, duration: int | None = None) -> None:
        """按本次触发时长自动复位；时长缺失/非法时回退 HONK_AUTO_OFF_SECONDS。"""
        await asyncio.sleep(duration if isinstance(duration, int) and duration > 0 else HONK_AUTO_OFF_SECONDS)
        self._state = False
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        duration = self._duration()
        try:
            resp = await self.coordinator.api.va_command(
                self.coordinator.vin, CMD_VA_INIT, vatype=4, duration=duration
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("FordPass 声光寻车（开）失败: %s", exc)
            raise
        cid = self._command_id(resp)
        if cid:
            _LOGGER.info("FordPass 声光寻车已触发 commandId=%s（时长=%ss）", cid, duration)
            self.coordinator.hass.async_create_task(
                self._poll_cmd_status(CMD_VA_INIT, cid)
            )
        self._state = True
        self.async_write_ha_state()
        self.coordinator.hass.async_create_task(self._auto_off(duration))

    async def async_turn_off(self, **kwargs: Any) -> None:
        try:
            resp = await self.coordinator.api.va_command(
                self.coordinator.vin, CMD_VA_CANCEL, vatype=4
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("FordPass 声光寻车（关）失败: %s", exc)
            raise
        cid = self._command_id(resp)
        if cid:
            _LOGGER.info("FordPass 声光寻车已取消 commandId=%s", cid)
            self.coordinator.hass.async_create_task(
                self._poll_cmd_status(CMD_VA_CANCEL, cid)
            )
        self._state = False
        self.async_write_ha_state()


class FordPassLightSwitch(SwitchEntity):
    """灯光寻车开关（ZoneLightingON / ZoneLightingOFF）。

    灯光寻车是瞬时命令、车辆不回报灯光状态，开关状态为记忆值（assumed）。
    v3.0.5: 初始状态默认关闭（False），避免实体显示 unknown。
    v3.1.8: 曾因锐际网关 228205「cmdSpec can not empty」临时移除——App 端
    该命令依赖蓝牙 TIMA 通道，云端 send-command 网关仅部分车型支持。
    v3.1.10: 恢复创建（全车型支持策略：登录后按车型能力判断，云端支持的
    车型可用；不支持时按下返回网关明确报错，如实提示）。
    """

    _attr_assumed_state = True
    _attr_icon = "mdi:car-light-high"

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-light_find"
        self._attr_name = "灯光寻车"
        self._attr_has_entity_name = False
        self._attr_device_info = coordinator.device_info
        self._state: bool = False

    @property
    def available(self) -> bool:
        return True  # v3.1.2: 不随福特云刷新失败而不可用（保留最后已知状态）

    @property
    def is_on(self) -> bool:
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


class FordPassHonkSwitch(SwitchEntity):
    """鸣笛寻车开关（v3.0.7，真实开/关双通道）。

    2026-10-01 静态逆向还原 App 官方协议（libapp.so, blutter）：
    - 开 = POST /api/vehicles/v5/{vin}/honk（body: ChirpOrHonkDuration/
      IntervalBetweenRequests/ChirpType，明文 JSON）——FordHonkCommand
    - 关 = DELETE /api/vehicles/v5/{vin}/honk（无 body）——FordHonkCancelCommand
    开关在触发后按本次「鸣笛持续时长」自动复位（v3.3.6: 跟随设置时长
    而非固定 30 秒；时长缺失时回退 HONK_AUTO_OFF_SECONDS）。

    v3.1.8: ChirpType 改为 App 枚举 0-4（此前 1-5 差一错位——类型设置无效
    的根因）；「声光共舞」=4 走独立 panic 端点（POST /panic/{duration}，
    灯+喇叭警报；锐际实测 404，会抛出明确错误提示车型不支持）。
    """

    _attr_assumed_state = True
    _attr_icon = "mdi:bullhorn"

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-honk"
        self._attr_name = "鸣笛寻车"
        self._attr_has_entity_name = False
        self._attr_device_info = coordinator.device_info
        self._state: bool = False

    @property
    def available(self) -> bool:
        return True  # v3.1.2: 不随福特云刷新失败而不可用（保留最后已知状态）

    @property
    def is_on(self) -> bool:
        return self._state

    async def _auto_off(self, duration: int | None = None) -> None:
        """按本次触发时长自动复位；时长缺失/非法时回退 HONK_AUTO_OFF_SECONDS。"""
        await asyncio.sleep(duration if isinstance(duration, int) and duration > 0 else HONK_AUTO_OFF_SECONDS)
        self._state = False
        self.async_write_ha_state()

    def _settings(self) -> tuple[int, str]:
        """读取当前鸣笛设置（select 持久化于 entry options——权威来源）。

        v3.1.8: 返回 (持续时长秒, 鸣笛类型中文名)——类型数字由
        CHIRP_TO_TYPE 统一映射为 App 枚举 0-4。找不到设置时回退默认
        （时长 10 秒 / 汽笛长鸣 chirpHonk=2），与 App 默认一致。
        v3.2.3: 改读 entry.options（select 每次选择即写入的持久化值），
        修复重启后与内存 honk_settings 分裂导致用默认设置鸣笛的问题。
        """
        options: dict = {}
        entry_id = getattr(self.coordinator, "entry_id", None)
        if entry_id:
            entry = self.coordinator.hass.config_entries.async_get_entry(entry_id)
            options = entry.options if entry else {}
        try:
            duration = int(options.get(CONF_HONK_DURATION, 10) or 10)
        except (TypeError, ValueError):
            duration = 10
        chirp_name = options.get(CONF_CHIRP_TYPE) or "汽笛长鸣"
        return duration, str(chirp_name)

    @staticmethod
    def _command_id(resp: Any) -> str | None:
        """Extract commandId from the decrypted send-command response."""
        if isinstance(resp, dict):
            cid = resp.get("commandId")
            if cid:
                return str(cid)
            inner = resp.get("data")
            if isinstance(inner, dict) and inner.get("commandId"):
                return str(inner["commandId"])
        return None

    async def _poll_cmd_status(self, command_type: str, command_id: str) -> None:
        """send-command 轮询 command-execution-status（HAR：InitialVA=26/
        CancelVA=476）——结果写入 announce_status 传感器，尽力而为。
        """
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
                    "error": "command-execution-status 轮询超时（鸣笛命令可能已执行完成）",
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

    async def async_turn_on(self, **kwargs: Any) -> None:
        # v3.0.8: 鸣笛参数来自设置实体——持续时长/鸣笛类型直达车机
        duration, chirp_name = self._settings()
        chirp_type = CHIRP_TO_TYPE.get(chirp_name, 2)  # 0-4，App 枚举
        try:
            if chirp_type == 4:
                # v3.2.1: 声光共舞改走 send-command InitialVA（HAR 实证 200 +
                # commandId=26）——App 的「鸣笛寻车」即此通道（VAType=4=panic
                # 灯+喇叭）；V5 /panic 端点锐际实测 404，不再使用。
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
                    self.coordinator.hass.async_create_task(
                        self._poll_cmd_status(CMD_VA_INIT, cid)
                    )
            else:
                resp = await self.coordinator.api.honk_command(
                    self.coordinator.vin,
                    duration=duration,
                    interval=1,
                    chirp_type=chirp_type,
                )
        except Exception as exc:  # noqa: BLE001
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
            self.coordinator.hass.async_create_task(
                self._poll_announce(self.coordinator.vin, resp["commandId"])
            )
        self._state = True
        self.async_write_ha_state()
        # 按本次触发时长自动结束，开关同步复位（v3.3.6: 跟随设置时长而非固定 30s）
        self.coordinator.hass.async_create_task(self._auto_off(duration))

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
        # 真实停止通道：类型 0-3 = DELETE /api/vehicles/v5/{vin}/honk（与 App
        # 取消鸣笛一致）；类型 4（声光共舞）= send-command CancelVA（HAR 实证
        # 200 + commandId=476，App 取消声光寻车即此通道）。
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
                    self.coordinator.hass.async_create_task(
                        self._poll_cmd_status(CMD_VA_CANCEL, cid)
                    )
            else:
                resp = await self.coordinator.api.honk_cancel_command(self.coordinator.vin)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("FordPass 鸣笛寻车（关）失败: %s", exc)
            raise
        if isinstance(resp, dict) and resp.get("commandId"):
            _LOGGER.info("FordPass 鸣笛寻车已停止 commandId=%s", resp["commandId"])
        self._state = False
        self.async_write_ha_state()
