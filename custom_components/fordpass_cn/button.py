"""Button platform: one-shot remote actions.

v2.10.0: 覆盖中国区网关 send-command 白名单内全部命令，并按车型能力
（vehicle-status 字段）过滤创建——数据无效 / 车型不支持的按钮不创建。
灯光寻车与后备箱解锁已合成 switch 实体（见 switch.py）。
v3.0.3: 「刷新车辆状态」→「手动拉取最新状态」、「自动刷新状态」→
「请求车机刷新状态」；新增「鸣笛寻车」按钮。
v3.0.4: 「鸣笛寻车」切换为 v5 网关真实通道（DELETE /api/vehicles/v5/{vin}/honk，
实测 200 + commandId）——中国区 send-command 白名单不含 Honk。
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
    CMD_ASU_SETTING,
    CMD_AUTO_REFRESH,
    CMD_CENTRAL_LIGHTING,
    CMD_HONK,
    CMD_OTA_SCHEDULE,
    CMD_REFRESH_STATUS,
    CMD_TRAILER_CHECK_START,
    CMD_TRAILER_CHECK_STOP,
    CMD_VA_CANCEL,
    CMD_VA_INIT,
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
    # 鸣笛寻车（v3.0.3）
    ("honk", "鸣笛寻车", "mdi:bullhorn", CMD_HONK, [["crccFlag"]], "usable"),
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
        if self._command == CMD_HONK:
            # v3.0.4: 中国区 send-command 网关白名单不含 Honk（HTTP 400 100502），
            # 鸣笛寻车切换为 v5 网关真实通道（DELETE /api/vehicles/v5/{vin}/honk，
            # 实测 200 + commandId）。按下即调用 v5 通道；成功后照常刷新实体。
            try:
                resp = await self.coordinator.api.honk_command(
                    self.coordinator.vin
                )
                if isinstance(resp, dict) and resp.get("commandId"):
                    _LOGGER.info("FordPass 鸣笛寻车已下发（v5 通道）commandId=%s", resp["commandId"])
            except Exception as exc:  # noqa: BLE001 - keep going so entities still refresh
                _LOGGER.warning("FordPass 鸣笛寻车（v5 通道）失败: %s", exc)
            try:
                await self.coordinator.force_refresh()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("FordPass refresh after honk failed: %s", exc)
            return

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
