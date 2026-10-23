"""Button platform: one-shot remote actions.

v2.10.0: 覆盖中国区网关 send-command 白名单内全部命令，并按车型能力
（vehicle-status 字段）过滤创建——数据无效 / 车型不支持的按钮不创建。
灯光寻车与后备箱解锁已合成 switch 实体（见 switch.py）。
v3.0.3: 「刷新车辆状态」→「手动拉取最新状态」、「自动刷新状态」→
「请求车机刷新状态」；新增「鸣笛寻车」按钮。
v3.0.5: 「鸣笛寻车」按钮迁移为开关实体（见 switch.py）——开 = v5 网关
DELETE /api/vehicles/v5/{vin}/honk 真实通道，鸣笛 30 秒自动复位。
v3.0.8: 新增「保存鸣笛设置」按钮——把持续时长/鸣笛类型保存并上传：
先本地保存（select 已实时写入 config entry options），再调用 RCC Profile
端点（POST /api/cnxapi-cds/crcc/v1/profile-by-vin）尝试账户云端持久化；
云端持久化的 signatureR2 签名未还原时回退为本地保存（下次鸣笛仍按
新设置把参数传给车机，等效上传车机）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import capability
from .api import extract_remote_image_url
from .const import (
    CMD_ASU_SETTING,
    CMD_AUTO_REFRESH,
    CMD_CENTRAL_LIGHTING,
    CMD_OTA_SCHEDULE,
    CMD_REFRESH_STATUS,
    CMD_TRAILER_CHECK_START,
    CMD_TRAILER_CHECK_STOP,
    DOMAIN,
)
from .coordinator import FordPassCoordinator

_LOGGER = logging.getLogger(__name__)


# (key, 名称, 图标, 命令, 能力检测路径, 检测方式, cmd_spec)
# 检测方式: None=总是创建 / "usable"=值有效 / "on"=值为开启态 / "node"=节点存在
# cmd_spec: 可选的 [{"key":..,"value":..}] 列表——HAR 实测（2026-10-03）确认
#   福特派 InitialVA 带 VAType=4 + Duration=15、CancelVA 带 VAType=4；
#   不带 cmdSpec 的命令在部分网关/车型下无响应。
# v3.1.10: 恢复中央区灯光/语音助手初始化/取消/辅助设置/OTA 激活排程（v3.1.8
# 曾因锐际网关 228205/402 临时移除）——全车型支持策略：登录后按车型能力判断
# 创建，云端不支持的车型按下会返回网关明确报错，如实提示。
_BUTTONS: list[tuple[str, str, str, str, list[list[str]] | None, str | None, list[dict[str, str]] | None]] = [
    # v3.3.4: 原「手动拉取最新状态」+「请求车机刷新状态」两按钮已合并为
    # 「手动刷新车辆状态」（见 FordPassManualRefreshButton：先请求车机上报
    # 云端，延时 3 秒后拉取最新状态）——此处不再创建独立按钮。
    # 远程控车开启（crccFlag=ON）才有意义
    ("central_lighting", "中央区灯光", "mdi:car-light-high", CMD_CENTRAL_LIGHTING,
     [["crccFlag"]], "usable", None),
    # v3.3.3: 声光寻车（InitialVA/CancelVA）已由 v3.2.1 的两个按钮合并为
    # switch「声光寻车」（见 switch.py FordPassVAswitch）——此处不再创建
    # 按钮，避免重复入口；命令通道与 cmdSpec 完全一致。
    # 固件/OTA 相关（firmwareUpgInProgress 字段存在即认为支持）
    ("ota_schedule", "OTA 激活排程", "mdi:update", CMD_OTA_SCHEDULE,
     [["firmwareUpgInProgress"]], "usable", None),
    ("asu_setting", "辅助设置", "mdi:cog-outline", CMD_ASU_SETTING,
     [["ccsSettings"]], "node", None),
    # 皮卡/拖车（双后轮启用才创建）
    ("trailer_check_start", "拖车灯光检测开始", "mdi:truck-trailer", CMD_TRAILER_CHECK_START,
     [["TPMS", "dualRearWheel"]], "on", None),
    ("trailer_check_stop", "拖车灯光检测停止", "mdi:truck-trailer", CMD_TRAILER_CHECK_STOP,
     [["TPMS", "dualRearWheel"]], "on", None),
]


PARALLEL_UPDATES = 1

def _capability_ok(status: dict, paths: list[list[str]] | None, check: str | None) -> bool:
    if paths is None or check is None:
        return True
    if check == "usable":
        return capability.usable(status, paths)
    if check == "on":
        return capability.is_on(status, paths)
    if check == "node":
        return capability.node_usable(status, paths)
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    # v3.1.6: 多 VIN——每辆车按各自车型能力过滤创建按钮组
    payload = entry.runtime_data  # v5.3.0: runtime_data 替代 hass.data
    coordinators = payload.get("coordinators") or [payload["coordinator"]]
    buttons: list[ButtonEntity] = []
    for coordinator in coordinators:
        status = (coordinator.data or {}).get("vehiclestatus", {}) or {}
        buttons.extend(
            FordPassButton(coordinator, key, label, icon, command, cmd_spec)
            for key, label, icon, command, paths, check, cmd_spec in _BUTTONS
            if _capability_ok(status, paths, check)
        )
        # v3.3.4: 「手动刷新车辆状态」——所有车型都有（合并原
        # 「请求车机刷新状态」「手动拉取最新状态」两按钮）
        buttons.append(FordPassManualRefreshButton(coordinator))
        # v4.7.2: v5 主动刷新车况 + v5 刷新轮询——v5.2.5 起移除：v5 PUT 命令
        # 通道载荷（PUT /api/vehicles/v5/{vin}/status）未经官方 App 抓包确认，
        # 与 v5.2.4 移除的猜测命令同类（send-command 手动刷新保留，已确认）。
        # v4.7.1: 分车门锁定（IDUL）——v5.2.4 起移除：doortype 四门枚举属
        # "按福特 v5 惯例"推测、未对照官方 App 抓包确认，遥控类命令不得
        # 发送未验证载荷（待官方 App 抓包确认后恢复）。整车门锁（lock.py）不受影响。
        # v3.3.4: 「保存鸣笛设置」按钮已删除——鸣笛类型/时长 select
        # 选择后即时上传云端（见 select.py 云端同步），无需手动保存。
        # v3.1.7: 空调滤芯重置（AAR 能力车型——vehicle-status 无关，按
        # coordinator 已拉取的 air_filter 数据创建，无该数据的车型不创建）
        air_filter = (coordinator.data or {}).get("air_filter")
        if isinstance(air_filter, dict):
            af_payload = air_filter.get("data") if isinstance(air_filter.get("data"), dict) else None
            if isinstance(af_payload, dict) and isinstance(af_payload.get("airFilter"), dict):
                buttons.append(FordPassAirFilterResetButton(coordinator))
        # v3.1.13: 停车影像 / 行车监控（车辆列表 encryptedCarId 非空才创建——
        # 锐际 encryptedCarId=null = 车型无远程影像硬件，不创建、0 unavailable）
        if getattr(coordinator, "car_id", None):
            buttons.append(FordPassImageButton(coordinator, "停车影像", "mdi:car-multiple", "parking"))
            buttons.append(FordPassImageButton(coordinator, "行车监控", "mdi:cctv", "traffic"))
        # v3.3.7: 遥控泊车/远程关窗按钮——v5.2.4 起移除：白名单命令无参数
        # 直接下发属"猜测命令"，遥控泊车绝不能触发未确认载荷（# 要求移除，待对照官方 App 抓包确认后恢复）。
        # v3.5.0: APK 6.16.0 补全命令——消息已读 / 行车记录仪录制。
        # 消息已读：messages_page 探测有数据才创建（有消息中心的车）。
        # 录制按钮：video_file 探测有数据才创建（带行车记录仪硬件的车），
        # 无硬件车型 404/无数据自动跳过（全车型适配）。
        _ep = coordinator.extra_probes
        if isinstance(_ep, dict) and _ep.get("messages_page"):
            buttons.append(FordPassMarkReadButton(coordinator))
        if isinstance(_ep, dict) and _ep.get("video_file"):
            buttons.append(FordPassVideoStartButton(coordinator))
            buttons.append(FordPassVideoStopButton(coordinator))
        # v3.7.2: 家充桩解绑按钮——smartwallbox 探测到绑定桩才创建
        if isinstance(coordinator.smartwallbox, dict) and coordinator.smartwallbox:
            buttons.append(FordPassWallboxUnbindButton(coordinator))
        # v4.7.3: 家充桩 BLE 自动认证按钮——smartwallbox 探测到绑定桩才创建
        # （6.16.0 逆向端点 auto/charging/ble/add/v3，桩车 BLE 自动授权；
        # 纯油/无绑定桩车型不创建，全车型适配）。注意：汽车 BLE 车控 15 条
        # 指令（制冷/制热/熄火/门锁/车窗/寻车/前备箱等）是纯 BLE 物理链路
        # （车旁 + PAAK 安全会话），云端无 HTTP 通道，HA 云集成不可执行，
        # 全车型用户请用手机在车旁操作福特派 App——README 有边界说明。
        if isinstance(coordinator.smartwallbox, dict) and coordinator.smartwallbox:
            buttons.append(FordPassWallboxBleAddButton(coordinator))
        # v4.2.4: 清空预约出发/充电——cevs 写操作（6.16.0 逆向确认
        # departuretimes/deleteall）。仅 cevs 车型（fuelType ∈ E/P/H 系）
        # 且探测到预约计划数据才创建；纯油/柴油车不创建（探测 200 ≠ 车型
        # 支持，业务恒空，见 references/entity-vehicle-creation.md）。
        _cevs_probe = getattr(coordinator, "cevs_probe", {}) or {}
        _fuel = str(coordinator.vehicle_info.get("fuelType") or "").upper()
        _cevs_supported = _fuel in ("E", "BEV", "P", "PHEV", "H", "HEV", "MHEV")
        if _cevs_supported and _cevs_probe.get("departuretimes"):
            buttons.append(FordPassCevsDeleteAllButton(coordinator))
    async_add_entities(buttons)
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

        for _ent in buttons:
            _ent.async_on_remove(
                _ent.coordinator.async_add_listener(_mk_refresh(_ent))
            )
    except Exception:  # 注册失败不影响实体创建
        _LOGGER.debug("FordPass 实体注册回调失败（忽略）", exc_info=True)

    # v5.2.6: 清理移除控件的 registry 残留——v5.2.4 移除 IDUL 分车门锁定/
    # 遥控泊车/远程关窗/哨兵/立即充电/车载冰箱/前备箱锁，v5.2.5 移除
    # v5 主动刷新/v5 刷新轮询后，"不再创建"≠"自动消失"：旧 registry 条目
    # 会一直 unavailable 占位（实测 6 个）。凡本 entry 的 button 且 unique_id
    # 不在新实体集合、未被用户手动禁用的，显式移除（与 sensor.py 同款，
    # 限定本 entry，多账号互不误删）。
    reg = er.async_get(hass)
    new_ids = {b.unique_id for b in buttons}
    reg_entries = er.async_entries_for_config_entry(reg, entry.entry_id)
    btn_regs = [e for e in reg_entries if e.entity_id.startswith("button.")]
    _LOGGER.debug("button cleanup: entry=%s registry=%d new_ids=%d", entry.entry_id, len(btn_regs), len(new_ids))
    for entry_ in btn_regs:
        if entry_.disabled_by is not None:
            continue
        if entry_.unique_id not in new_ids:
            _LOGGER.debug("button cleanup: removing stale %s", entry_.entity_id)
            reg.async_remove(entry_.entity_id)



class _FordPassButtonBase(ButtonEntity):
    """未按下过的按钮显示「待命」兜底（v5.1.9 初版 → v5.2.0 决定性修复）。

    HA ButtonEntity 平台：按下时自动把 state 写为按下时间戳；但**从未按下过的
    按钮无历史状态，重启/reload 时 restore 流程把 state 置为 unknown**——无论
    在 async_added_to_hass 里何时写「待命」都会被后续覆盖（时序竞争，v5.1.9
    实测仍 unknown）。v5.2.0 改为 **override state property 兜底**：读取时
    _attr_state 为空/unknown 一律返回「待命」，按下后 _attr_state=时间戳则
    原样返回——不受恢复时序影响，逻辑必然生效。
    """
    _attr_state = "待命"

    @property
    def state(self) -> str | None:
        st = self._attr_state
        if st is None or st in ("unknown", "unavailable", ""):
            return "待命"
        return st


class FordPassWallboxUnbindButton(_FordPassButtonBase):
    _attr_state = "待命"
    """解绑家充桩（v3.7.2）：POST binding/unbind/v2。

    仅 smartwallbox 探测到绑定桩的车型创建；wallboxId 取默认桩。
    解绑为不可逆操作，云端失败时如实抛错（HomeAssistantError）。
    """

    _attr_icon = "mdi:link-off"

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-wallbox_unbind"
        self._attr_name = "解绑家充桩"
        self._attr_has_entity_name = True
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)  # v5.3.1: 保留最后已知状态——刷新失败不置不可用（与 switch/lock/select/device_tracker 对齐）

    def _wallbox_id(self) -> str | None:
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

    async def async_press(self) -> None:
        wid = self._wallbox_id()
        if not wid:
            raise HomeAssistantError("未找到已绑定的家充桩")
        try:
            await self.coordinator.api.wallbox_unbind(self.coordinator.vin, wid)
        except Exception as err:
            raise HomeAssistantError(f"解绑家充桩失败: {err}") from err


class FordPassWallboxBleAddButton(_FordPassButtonBase):
    _attr_state = "待命"
    """家充桩 BLE 自动认证（v4.7.3）：POST auto/charging/ble/add/v3。

    6.16.0 逆向端点——桩车 BLE 自动授权：授权后插枪即由桩与车 BLE
    自动配对认证（手机不在场也能启动充电）。仅 smartwallbox 探测到
    绑定桩的车型创建；wallboxId 取默认桩。云端失败如实抛错。
    """

    _attr_icon = "mdi:bluetooth-connect"

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-wallbox_ble_add"
        self._attr_name = "家充桩 BLE 自动认证"
        self._attr_has_entity_name = True
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)  # v5.3.1: 保留最后已知状态——刷新失败不置不可用（与 switch/lock/select/device_tracker 对齐）

    def _wallbox_id(self) -> str | None:
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

    async def async_press(self) -> None:
        wid = self._wallbox_id()
        if not wid:
            raise HomeAssistantError("未找到已绑定的家充桩")
        try:
            await self.coordinator.api.wallbox_ble_add(self.coordinator.vin, wid)
        except Exception as err:
            raise HomeAssistantError(f"家充桩 BLE 自动认证失败: {err}") from err


class FordPassMarkReadButton(_FordPassButtonBase):
    _attr_state = "待命"
    """标记消息已读（v3.5.0）：POST messages/read。

    messages_page 探测有数据才创建；按 message 实体的最新分类/消息 id
    标记已读（未读到 id 时按空分类提交，服务端按当前未读处理）。
    标记已读不可逆但无破坏性；失败时按钮上报错误信息。
    """

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-messages_read"
        self._attr_name = "标记消息已读"
        self._attr_has_entity_name = True
        self._attr_device_info = coordinator.device_info
        self._attr_icon = "mdi:email-check-outline"

    async def async_press(self) -> None:
        try:
            category_id: str | None = None
            message_id: str | None = None
            ep = self.coordinator.extra_probes or {}
            mp = ep.get("messages_page") or {}
            payload = mp.get("data") if isinstance(mp, dict) else None
            lst = payload.get("messages") if isinstance(payload, dict) else None
            if not isinstance(lst, list):
                lst = payload.get("list") if isinstance(payload, dict) else None
            if isinstance(lst, list) and lst and isinstance(lst[0], dict):
                message_id = str(lst[0].get("messageId") or "")
                category_id = str(lst[0].get("categoryId") or "")
            await self.coordinator.api.mark_messages_read(
                category_id or None, message_id or None
            )
        except Exception as exc:
            raise HomeAssistantError(f"标记已读失败: {exc}") from exc


class FordPassVideoStartButton(_FordPassButtonBase):
    _attr_state = "待命"
    """开始行车记录仪录制（v3.5.0）：POST start-video-recording。

    仅带行车记录仪硬件车型（video_file 探测有数据）创建。
    """

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-video_start"
        self._attr_name = "开始行车记录"
        self._attr_has_entity_name = True
        self._attr_device_info = coordinator.device_info
        self._attr_icon = "mdi:record-rec"

    async def async_press(self) -> None:
        try:
            await self.coordinator.api.start_video_recording(self.coordinator.vin)
        except Exception as exc:
            raise HomeAssistantError(f"开始录制失败: {exc}") from exc


class FordPassVideoStopButton(_FordPassButtonBase):
    _attr_state = "待命"
    """停止行车记录仪录制（v3.5.0）：POST stop-video-recording。"""

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-video_stop"
        self._attr_name = "停止行车记录"
        self._attr_has_entity_name = True
        self._attr_device_info = coordinator.device_info
        self._attr_icon = "mdi:stop"

    async def async_press(self) -> None:
        try:
            await self.coordinator.api.stop_video_recording(self.coordinator.vin)
        except Exception as exc:
            raise HomeAssistantError(f"停止录制失败: {exc}") from exc


class FordPassButton(_FordPassButtonBase):
    _attr_state = "待命"
    def __init__(self, coordinator, key, label, icon, command, cmd_spec=None) -> None:
        self.coordinator = coordinator
        self._command = command
        self._cmd_spec = cmd_spec
        self._attr_unique_id = f"{coordinator.vin}-{key}"
        self._attr_name = label
        self._attr_has_entity_name = True
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
                self.coordinator.vin, self._command, self._cmd_spec
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
                    # v5.3.4: 合并推送而非整体替换（与 coordinator.run_command
                    # 同款修复）——覆盖 data 会清空服务信息类键，实体变空值。
                    self.coordinator.async_set_updated_data(
                        {**(self.coordinator.data or {}), "status": vs, "vehiclestatus": vs}
                    )
                else:
                    # v4.0.3: 不再兜底 force_refresh()。上方的立即 force_refresh
                    # 已拉取最新状态并写入活动日志；wait 无新鲜快照（命令仍在
                    # 执行/超时）时保持该结果即可，下次自动轮询会更新。旧版
                    # 在这里再 force_refresh() 一次 → 双 logbook + 双福特云
                    # 请求（12:21 0.6s / 13:00 9.6s 成对条目根因）。
                    _LOGGER.info(
                        "FordPass ForceRefresh: no fresh payload yet, "
                        "keeping immediate refresh result"
                    )
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


class FordPassManualRefreshButton(_FordPassButtonBase):
    """手动刷新车辆状态（v3.3.4）——合并 v3.0.3 的两个刷新按钮。

    与 App「下拉刷新车辆状态」时序一致，顺序执行：
      1) send-command AutoRefresh —— 请求车机把最新状态上报云端；
      2) 延时 3 秒（车机上报 + 云端落盘需要时间）；
      3) send-command ForceRefresh —— 拉取云端最新车辆状态，
         并立即 force_refresh 更新实体（fast UI feedback）。
    旧「请求车机刷新状态」「手动拉取最新状态」两按钮已删除（实体
    注册表残留按需求 5 清理，不显示 unavailable）。
    """

    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-manual_refresh"
        self._attr_name = "手动刷新车辆状态"
        self._attr_has_entity_name = True
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        return True  # v4.5.5: 手动刷新按钮保持恒可用（刷新失败时仍需可点以重试）

    async def async_press(self) -> None:
        vin = self.coordinator.vin
        # v4.4.0: 点击立即写活动——完整流程（车机上报 30s + 拉取 + 刷新实体）
        # 需数十秒到数分钟，此前要等 force_refresh 完成才写摘要，用户点击后
        # 活动记录器长时间无条目（"点了没反应"）。现在点击即写「已触发」，
        # 完成后 coordinator 再写成功摘要（失败走 v4.3.8 失败活动路径）。
        # v4.4.1: logbook 失败改 warning 诊断（v4.4.0/4.4.1 发布漏发 button.py
        # 致本段未上线，system_log 无特征日志定位）。
        # v4.4.2: 补发 button.py 到 main；移除诊断特征日志，仅异常时 warning。
        # v4.4.3: 复刻 App 下拉刷新——force_refresh(force_all=True) 绕过慢变
        # 端点 TTL 缓存强制全量真拉（保养/召回/SIM/OTA/滤芯/ccfeatures/
        # prognostic/告警/消息一次性更新到最新），与 App 下拉刷新一致。
        try:
            service_data = {
                "name": self.coordinator._vehicle_name or "福特派互联",
                "message": "手动刷新已触发（全量）：请求车机上报最新状态（约 30 秒后完成）",
                "domain": DOMAIN,
            }
            # v4.5.1: 关联本车"车辆信息"传感器（集成详情页活动标签可见，
            # 实体页不刷屏）；找不到时保持无关联（不报错）。
            act_eid = self.coordinator.activity_entity_id()
            if act_eid:
                service_data["entity_id"] = act_eid
            # v4.4.4: 关联车辆实体——不带 entity_id 的 logbook 事件在集成
            # 设备页活动标签不可见（entity_id=None 被设备过滤）。
            await self.hass.services.async_call("logbook", "log", service_data)
        except Exception as exc:  # noqa: BLE001 - logbook 失败不影响刷新
            _LOGGER.warning("FordPass manual refresh logbook failed: %s", exc)
        # P2-3（v5.6.0）：30 秒等待 + 全量刷新改受跟踪后台任务——此前
        # async_press 内 await sleep(30) 使服务调用挂起 30 秒、不可取消、
        # 集成卸载后任务仍继续执行。点击已即时写「已触发」活动，后台任务
        # 完成后由 coordinator 写成功摘要（失败走既有失败活动路径）。
        async def _refresh_flow() -> None:
            # 1) 请求车机刷新状态（上报云端）
            try:
                await self.coordinator.api.send_command(vin, CMD_AUTO_REFRESH)
            except Exception as exc:
                _LOGGER.warning("FordPass 请求车机刷新状态失败: %s", exc)
                return
            # 2) 延时 30 秒（v3.3.10：车机上报 + 云端落盘需要时间，实测 3 秒
            # 过短常拉到旧数据；30 秒与 App 下拉刷新节奏一致）
            await asyncio.sleep(30)
            # 3) 手动拉取最新状态（ForceRefresh）+ 全量刷新实体
            try:
                await self.coordinator.api.send_command(vin, CMD_REFRESH_STATUS)
            except Exception as exc:
                _LOGGER.warning("FordPass 手动拉取最新状态失败: %s", exc)
                return
            try:
                await self.coordinator.force_refresh(force_all=True)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("FordPass 手动刷新后全量数据拉取失败: %s", exc)
            # v4.6.1: 手动刷新同时强制重探全部探测端点（车型主数据/OTA
            # 协议/应用设置/分享能力/用户登录状态/充电域/维保预约/告警等）。
            try:
                await self.coordinator.async_probe_capabilities(force=True)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("FordPass 手动刷新后探测端点重探失败: %s", exc)
            _LOGGER.info("FordPass 手动刷新车辆状态完成（车机→云端→HA 全量）")

        self.hass.async_create_task(_refresh_flow())


class FordPassAirFilterResetButton(_FordPassButtonBase):
    """重置空调滤芯（v3.1.7）。

    PUT /api/cnxapi-vds/v1/aar/status body={channel:"IVI", filterStatus:0,
    xjw, encryptedVin}（R3 签名，2026-10-02 实机 200 success）——重置后
    GET aar/status 的 lastReplaceTime 更新为当天。成功后立即刷新数据，
    让空调滤芯传感器显示新日期。
    """

    _attr_icon = "mdi:air-filter"

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-reset_air_filter"
        self._attr_name = "重置空调滤芯"
        self._attr_has_entity_name = True
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)  # v5.3.1: 保留最后已知状态——刷新失败不置不可用（与 switch/lock/select/device_tracker 对齐）

    async def async_press(self) -> None:
        _LOGGER.info("FordPass 重置空调滤芯：PUT %s", self.coordinator.vin)
        try:
            resp = await self.coordinator.api.reset_air_filter(self.coordinator.vin)
            _LOGGER.info("FordPass 空调滤芯重置成功: %s", str(resp)[:200])
        except Exception as exc:
            _LOGGER.warning("FordPass 空调滤芯重置失败: %s", exc)
            raise
        # 重置后立即刷新，让传感器显示新的 lastReplaceTime
        try:
            await self.coordinator.force_refresh()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("FordPass 空调滤芯重置后刷新失败: %s", exc)


class FordPassCevsDeleteAllButton(_FordPassButtonBase):
    """清空全部预约出发/充电计划（v4.2.4）。

    逆向 6.16.0（Dart 深度反编译）：POST /api/cevs/v2/departuretimes/
    deleteall（deleteAllScheduledDepartureTimesV2）——预约出发/充电是一套
    完整 CRUD（retrieve/save/deleteall/toggle），此前集成只接了只读+toggle，
    写操作缺失。body 与 retrieve 同构：vin=cevs 场景白盒密文 + xjw，
    raw_body 无外层签名（v3.7.23 实证 DTO 必填字段为 vin）。
    仅 cevs 车型（fuelType ∈ E/P/H 系）且探测到预约计划数据才创建；
    纯油/柴油车不创建（探测 200 ≠ 车型支持，业务恒空）。
    """

    _attr_icon = "mdi:calendar-remove"

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-cevs_deleteall"
        self._attr_name = "清空预约出发/充电"
        self._attr_has_entity_name = True
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)  # v5.3.1: 保留最后已知状态——刷新失败不置不可用（与 switch/lock/select/device_tracker 对齐）

    async def async_press(self) -> None:
        _LOGGER.info("FordPass 清空预约出发/充电：POST %s", self.coordinator.vin)
        try:
            resp = await self.coordinator.api.cevs_delete_all_departures(
                self.coordinator.vin
            )
            _LOGGER.info("FordPass 预约清空结果: %s", str(resp)[:300])
        except Exception as exc:
            _LOGGER.warning("FordPass 预约清空失败: %s", exc)
            raise
        try:
            await self.coordinator.force_refresh()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("FordPass 预约清空后刷新失败: %s", exc)


class FordPassImageButton(_FordPassButtonBase):
    """停车影像 / 行车监控查询按钮（v3.1.13）。

    仅当车辆列表 encryptedCarId 非空时创建（锐际 null = 车型无远程影像
    硬件，不创建）。按下调用 App 的 VehicleManagerEndpoint：
    - parking:  POST /api/cnxapi-pds/v1/search-vehicle-parking-image
    - traffic:  POST /api/cnxapi-pds/v1/search-vehicle-monitor-traffic
    请求体含 carId（= encryptedCarId）。响应写入 coordinator.data
    ["remote_image"]（含 kind 标记），供实体/日志查看；响应结构以
    车型实测为准（云端可能返回记录列表或图片 URL）。
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: FordPassCoordinator, label: str, icon: str, kind: str) -> None:
        self.coordinator = coordinator
        self._kind = kind
        self._attr_unique_id = f"{coordinator.vin}-{kind}_image"
        self._attr_name = label
        self._attr_icon = icon
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)  # v5.3.1: 保留最后已知状态——刷新失败不置不可用（与 switch/lock/select/device_tracker 对齐）

    async def async_press(self) -> None:
        car_id = getattr(self.coordinator, "car_id", None)
        if not car_id:
            raise RuntimeError("车辆无远程影像硬件（encryptedCarId 为空），无法查询")
        _LOGGER.info("FordPass %s 查询发起 carId=%s", self._kind, car_id)
        try:
            if self._kind == "parking":
                resp = await self.coordinator.api.parking_image(self.coordinator.vin, car_id)
            else:
                resp = await self.coordinator.api.monitor_traffic(self.coordinator.vin, car_id)
        except Exception as exc:
            _LOGGER.warning("FordPass %s 查询失败: %s", self._kind, exc)
            raise
        self.coordinator.data["remote_image"] = {
            "kind": self._kind,
            "car_id": car_id,
            "resp": resp if isinstance(resp, dict) else {"raw": str(resp)[:2000]},
            # v3.4.4: 同步提取图片 URL，供「停车影像图/行车监控图」image 实体展示
            "image_url": extract_remote_image_url(resp),
        }
        self.coordinator.async_update_listeners()
        _LOGGER.info("FordPass %s 查询完成: %s", self._kind, str(resp)[:2000])
