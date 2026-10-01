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

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
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
from .coordinator import FordPassCoordinator

_LOGGER = logging.getLogger(__name__)


# (key, 名称, 图标, 命令, 能力检测路径, 检测方式)
# 检测方式: None=总是创建 / "usable"=值有效 / "on"=值为开启态 / "node"=节点存在
_BUTTONS: list[tuple[str, str, str, str, list[list[str]] | None, str | None]] = [
    # 通用命令——所有车型都有
    ("refresh", "手动拉取最新状态", "mdi:refresh", CMD_REFRESH_STATUS, None, None),
    ("auto_refresh", "请求车机刷新状态", "mdi:refresh-auto", CMD_AUTO_REFRESH, None, None),
    # 远程控车开启（crccFlag=ON）才有意义
    ("central_lighting", "中央区灯光", "mdi:car-light-high", CMD_CENTRAL_LIGHTING,
     [["crccFlag"]], "usable"),
    ("va_init", "语音助手初始化", "mdi:assistant", CMD_VA_INIT, [["crccFlag"]], "on"),
    ("va_cancel", "语音助手取消", "mdi:assistant", CMD_VA_CANCEL, [["crccFlag"]], "on"),
    # 固件/OTA 相关（firmwareUpgInProgress 字段存在即认为支持）
    ("ota_schedule", "OTA 激活排程", "mdi:update", CMD_OTA_SCHEDULE,
     [["firmwareUpgInProgress"]], "usable"),
    ("asu_setting", "辅助设置", "mdi:cog-outline", CMD_ASU_SETTING,
     [["ccsSettings"]], "node"),
    # 皮卡/拖车（双后轮启用才创建）
    ("trailer_check_start", "拖车灯光检测开始", "mdi:truck-trailer", CMD_TRAILER_CHECK_START,
     [["TPMS", "dualRearWheel"]], "on"),
    ("trailer_check_stop", "拖车灯光检测停止", "mdi:truck-trailer", CMD_TRAILER_CHECK_STOP,
     [["TPMS", "dualRearWheel"]], "on"),
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
    coordinator: FordPassCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    status = coordinator.data.get("vehiclestatus", {}) or {}
    buttons = [
        FordPassButton(coordinator, key, label, icon, command)
        for key, label, icon, command, paths, check in _BUTTONS
        if _capability_ok(status, paths, check)
    ]
    # v3.0.8: 保存鸣笛设置（与鸣笛开关同为远程控车能力 crccFlag）
    if capability.usable(status, [["crccFlag"]]):
        buttons.append(FordPassSaveHonkSettingsButton(coordinator))
    async_add_entities(buttons)


class FordPassButton(ButtonEntity):
    def __init__(self, coordinator, key, label, icon, command) -> None:
        self.coordinator = coordinator
        self._command = command
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
                self.coordinator.vin, self._command
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


class FordPassSaveHonkSettingsButton(ButtonEntity):
    """保存鸣笛设置（v3.1.4）。

    按下即把 select 实体的「持续时长 / 鸣笛类型」保存：
    1) 本地持久化：config entry options（select 变化时已实时写入，
       此处再次确认，保证按钮点击时使用最新值）；
    2) 上传福特账户云端：UserPreferenceV2
       POST /api/cnxapi-pds/v1/user/preference-by-groups
       （VehicleAnnouncementSetting 组，2026-10-02 逆向还原 + 实机 200
       保存成功并回读确认——不再走旧 RCC profile-by-vin 通道）。
    """

    _attr_icon = "mdi:content-save"

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-save_honk_settings"
        self._attr_name = "保存鸣笛设置"
        self._attr_has_entity_name = False
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        return True  # v3.1.2: 不随福特云刷新失败而不可用（保留最后已知状态）

    def _settings(self) -> tuple[int, int]:
        # v3.0.10: entry_id 优先从 coordinator 取（coordinator 构造时已保存）；
        # 兜底遍历 hass.data[DOMAIN] 找到包含本 coordinator 的条目——
        # 避免 coordinator 由其他路径构造（entry_id 缺失）时按钮崩溃。
        settings: dict[str, Any] = {}
        entry_id = getattr(self.coordinator, "entry_id", None)
        domain_data = self.coordinator.hass.data.get(DOMAIN, {})
        if entry_id is not None and entry_id in domain_data:
            settings = domain_data[entry_id].get("honk_settings", {})
        else:
            for _eid, payload in domain_data.items():
                if (
                    isinstance(payload, dict)
                    and payload.get("coordinator") is self.coordinator
                ):
                    settings = payload.get("honk_settings", {})
                    break
        duration = int(settings.get(CONF_HONK_DURATION, 10) or 10)
        chirp_name = settings.get(CONF_CHIRP_TYPE) or CHIRP_TYPE_OPTIONS[2]
        if chirp_name in CHIRP_TYPE_OPTIONS:
            chirp_type = CHIRP_TYPE_OPTIONS.index(chirp_name) + 1
        else:
            chirp_type = 3
        return duration, chirp_type

    async def async_press(self) -> None:
        duration, chirp_type = self._settings()
        _LOGGER.info(
            "FordPass 保存鸣笛设置：时长=%ss 类型=%s(ChirpType=%s)",
            duration, CHIRP_TYPE_OPTIONS[chirp_type - 1], chirp_type,
        )
        try:
            resp = await self.coordinator.api.save_honk_settings(
                self.coordinator.vin,
                duration=duration,
                chirp_type=chirp_type,
            )
            if isinstance(resp, dict) and resp.get("cloud") is False:
                _LOGGER.warning(
                    "FordPass 鸣笛设置已本地保存；账户云端持久化失败：%s",
                    resp.get("error"),
                )
            else:
                _LOGGER.info(
                    "FordPass 鸣笛设置已保存上传福特云端（类型=%s 时长=%ss）",
                    (resp or {}).get("announce") if isinstance(resp, dict) else "?",
                    (resp or {}).get("duration") if isinstance(resp, dict) else duration,
                )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("FordPass 保存鸣笛设置失败（本地设置仍生效）: %s", exc)
            raise
