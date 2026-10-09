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
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import capability
from .const import (
    CHIRP_TYPE_OPTIONS,
    CMD_ASU_SETTING,
    CMD_AUTO_REFRESH,
    CMD_CENTRAL_LIGHTING,
    CMD_OTA_SCHEDULE,
    CMD_REFRESH_STATUS,
    CMD_TRAILER_CHECK_START,
    CMD_TRAILER_CHECK_STOP,
    CMD_VA_CANCEL,
    CMD_VA_INIT,
    CONF_CHIRP_TYPE,
    CONF_HONK_DURATION,
    DOMAIN,
)
from .api import extract_remote_image_url
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
    payload = hass.data[DOMAIN][entry.entry_id]
    coordinators = payload.get("coordinators") or [payload["coordinator"]]
    buttons: list[ButtonEntity] = []
    for coordinator in coordinators:
        status = coordinator.data.get("vehiclestatus", {}) or {}
        buttons.extend(
            FordPassButton(coordinator, key, label, icon, command, cmd_spec)
            for key, label, icon, command, paths, check, cmd_spec in _BUTTONS
            if _capability_ok(status, paths, check)
        )
        # v3.3.4: 「手动刷新车辆状态」——所有车型都有（合并原
        # 「请求车机刷新状态」「手动拉取最新状态」两按钮）
        buttons.append(FordPassManualRefreshButton(coordinator))
        # v3.3.4: 「保存鸣笛设置」按钮已删除——鸣笛类型/时长 select
        # 选择后即时上传云端（见 select.py 云端同步），无需手动保存。
        # v3.1.7: 空调滤芯重置（AAR 能力车型——vehicle-status 无关，按
        # coordinator 已拉取的 air_filter 数据创建，无该数据的车型不创建）
        air_filter = coordinator.data.get("air_filter")
        if isinstance(air_filter, dict):
            af_payload = air_filter.get("data") if isinstance(air_filter.get("data"), dict) else None
            if isinstance(af_payload, dict) and isinstance(af_payload.get("airFilter"), dict):
                buttons.append(FordPassAirFilterResetButton(coordinator))
        # v3.1.13: 停车影像 / 行车监控（车辆列表 encryptedCarId 非空才创建——
        # 锐际 encryptedCarId=null = 车型无远程影像硬件，不创建、0 unavailable）
        if getattr(coordinator, "car_id", None):
            buttons.append(FordPassImageButton(coordinator, "停车影像", "mdi:car-multiple", "parking"))
            buttons.append(FordPassImageButton(coordinator, "行车监控", "mdi:cctv", "traffic"))
        # v3.3.7: 云端命令白名单驱动——车型专属按钮按各自白名单自动创建
        # （电马遥控泊车、支持车窗升降的车型等；锐际白名单无则自动跳过）
        wl = coordinator.command_whitelist
        if isinstance(wl, list):
            parking = [c for c in wl if "Parking" in c]
            if parking:
                buttons.append(_FordPassWlButton(
                    coordinator, "parking", "遥控泊车", "mdi:car-shift-pattern",
                    parking[0]))
            window = [c for c in wl if "Window" in c and "Check" not in c]
            if window:
                buttons.append(_FordPassWlButton(
                    coordinator, "window_close", "远程关窗", "mdi:car-door",
                    window[0]))
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
    async_add_entities(buttons)


class FordPassMarkReadButton(ButtonEntity):
    """标记消息已读（v3.5.0）：POST messages/read。

    messages_page 探测有数据才创建；按 message 实体的最新分类/消息 id
    标记已读（未读到 id 时按空分类提交，服务端按当前未读处理）。
    标记已读不可逆但无破坏性；失败时按钮上报错误信息。
    """

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-messages_read"
        self._attr_name = "标记消息已读"
        self._attr_has_entity_name = False
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
        except Exception as exc:  # noqa: BLE001 - 按钮失败向用户如实报错
            raise HomeAssistantError(f"标记已读失败: {exc}") from exc


class FordPassVideoStartButton(ButtonEntity):
    """开始行车记录仪录制（v3.5.0）：POST start-video-recording。

    仅带行车记录仪硬件车型（video_file 探测有数据）创建。
    """

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-video_start"
        self._attr_name = "开始行车记录"
        self._attr_has_entity_name = False
        self._attr_device_info = coordinator.device_info
        self._attr_icon = "mdi:record-rec"

    async def async_press(self) -> None:
        try:
            await self.coordinator.api.start_video_recording(self.coordinator.vin)
        except Exception as exc:  # noqa: BLE001
            raise HomeAssistantError(f"开始录制失败: {exc}") from exc


class FordPassVideoStopButton(ButtonEntity):
    """停止行车记录仪录制（v3.5.0）：POST stop-video-recording。"""

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-video_stop"
        self._attr_name = "停止行车记录"
        self._attr_has_entity_name = False
        self._attr_device_info = coordinator.device_info
        self._attr_icon = "mdi:stop"

    async def async_press(self) -> None:
        try:
            await self.coordinator.api.stop_video_recording(self.coordinator.vin)
        except Exception as exc:  # noqa: BLE001
            raise HomeAssistantError(f"停止录制失败: {exc}") from exc


class _FordPassWlButton(ButtonEntity):
    """白名单驱动的通用按钮（v3.3.7，车型专属功能）。

    仅在云端命令白名单包含对应命令时创建；按下发送匹配命令，
    命令无参数（网关参数格式未知时按下返回错误即如实提示）。
    """

    def __init__(
        self,
        coordinator: FordPassCoordinator,
        suffix: str,
        label: str,
        icon: str,
        command: str,
    ) -> None:
        self.coordinator = coordinator
        self._command = command
        self._attr_unique_id = f"{coordinator.vin}-{suffix}"
        self._attr_name = label
        self._attr_has_entity_name = False
        self._attr_device_info = coordinator.device_info
        self._attr_icon = icon

    @property
    def available(self) -> bool:
        return True  # v3.1.2: 不随福特云刷新失败而不可用（保留最后已知状态）

    async def async_press(self) -> None:
        await self.coordinator.run_command(self._command)


class FordPassButton(ButtonEntity):
    def __init__(self, coordinator, key, label, icon, command, cmd_spec=None) -> None:
        self.coordinator = coordinator
        self._command = command
        self._cmd_spec = cmd_spec
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


class FordPassManualRefreshButton(ButtonEntity):
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
        self._attr_has_entity_name = False
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        return True  # v3.1.2: 不随福特云刷新失败而不可用（保留最后已知状态）

    async def async_press(self) -> None:
        vin = self.coordinator.vin
        # 1) 请求车机刷新状态（上报云端）
        try:
            await self.coordinator.api.send_command(vin, CMD_AUTO_REFRESH)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("FordPass 请求车机刷新状态失败: %s", exc)
            raise
        # 2) 延时 30 秒（v3.3.10：车机上报 + 云端落盘需要时间，实测 3 秒过短
        # 常拉到旧数据；30 秒与 App 下拉刷新节奏一致）
        await asyncio.sleep(30)
        # 3) 手动拉取最新状态（ForceRefresh）+ 立即刷新实体
        try:
            resp = await self.coordinator.api.send_command(vin, CMD_REFRESH_STATUS)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("FordPass 手动拉取最新状态失败: %s", exc)
            raise
        try:
            await self.coordinator.force_refresh()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("FordPass 手动刷新后数据拉取失败: %s", exc)
        _LOGGER.info("FordPass 手动刷新车辆状态完成（车机→云端→HA）")
        return


class FordPassAirFilterResetButton(ButtonEntity):
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
        self._attr_has_entity_name = False
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        return True  # 同保存鸣笛设置：不随福特云刷新失败而不可用

    async def async_press(self) -> None:
        _LOGGER.info("FordPass 重置空调滤芯：PUT %s", self.coordinator.vin)
        try:
            resp = await self.coordinator.api.reset_air_filter(self.coordinator.vin)
            _LOGGER.info("FordPass 空调滤芯重置成功: %s", str(resp)[:200])
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("FordPass 空调滤芯重置失败: %s", exc)
            raise
        # 重置后立即刷新，让传感器显示新的 lastReplaceTime
        try:
            await self.coordinator.force_refresh()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("FordPass 空调滤芯重置后刷新失败: %s", exc)


class FordPassImageButton(ButtonEntity):
    """停车影像 / 行车监控查询按钮（v3.1.13）。

    仅当车辆列表 encryptedCarId 非空时创建（锐际 null = 车型无远程影像
    硬件，不创建）。按下调用 App 的 VehicleManagerEndpoint：
    - parking:  POST /api/cnxapi-pds/v1/search-vehicle-parking-image
    - traffic:  POST /api/cnxapi-pds/v1/search-vehicle-monitor-traffic
    请求体含 carId（= encryptedCarId）。响应写入 coordinator.data
    ["remote_image"]（含 kind 标记），供实体/日志查看；响应结构以
    车型实测为准（云端可能返回记录列表或图片 URL）。
    """

    _attr_has_entity_name = False

    def __init__(self, coordinator: FordPassCoordinator, label: str, icon: str, kind: str) -> None:
        self.coordinator = coordinator
        self._kind = kind
        self._attr_unique_id = f"{coordinator.vin}-{kind}_image"
        self._attr_name = label
        self._attr_icon = icon
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        return True  # v3.1.2: 不随福特云刷新失败而不可用（保留最后已知状态）

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
        except Exception as exc:  # noqa: BLE001
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
