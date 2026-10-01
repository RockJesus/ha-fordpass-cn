"""Switch platform: remote engine start + 灯光寻车 + 鸣笛寻车。

v2.10.0: 灯光寻车开关——开 = ZoneLightingON（灯光寻车），关 = ZoneLightingOFF（关闭灯光寻车）。
v3.0.1: 后备箱锁已从 switch 迁移为 lock 实体（见 lock.py），此处仅保留远程启动与灯光寻车。
v3.0.5: 修复灯光寻车初始状态 unknown（默认关闭）；鸣笛寻车从 button 迁移为 switch。
v3.0.7: 鸣笛寻车真实双通道（静态逆向还原 App 协议）——开 = POST /api/vehicles/v5/{vin}/honk
（body: ChirpOrHonkDuration/IntervalBetweenRequests/ChirpType），关 = DELETE 同路径（App 的
FordHonkCancelCommand 通道）；触发后 30 秒自动复位。
v3.0.8: 鸣笛开关开启时读取「鸣笛持续时长 / 鸣笛类型」设置（select 实体，存于
config entry options），POST 参数直达车机——设置即生效（等效上传车机）。
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
    CMD_ENGINE_START,
    CMD_ENGINE_STOP,
    CMD_LIGHT_FIND_OFF,
    CMD_LIGHT_FIND_ON,
    DOMAIN,
    HONK_AUTO_OFF_SECONDS,
)
from .coordinator import FordPassCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: FordPassCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    status = coordinator.data.get("vehiclestatus", {}) or {}
    switches: list[SwitchEntity] = [FordPassEngineSwitch(coordinator)]
    # 灯光寻车：远程控车功能开启（crccFlag 有效）才创建
    if capability.usable(status, [["crccFlag"]]):
        switches.append(FordPassLightSwitch(coordinator))
        # 鸣笛寻车（v3.0.5 由按钮迁移为开关，同为远程控车能力）
        switches.append(FordPassHonkSwitch(coordinator))
    async_add_entities(switches)


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
        return self.coordinator.last_update_success

    @property
    def is_on(self) -> bool:
        status = self.coordinator.data.get("vehiclestatus", {})
        value = status.get("remoteStartStatus", {}).get("value")
        return bool(value) if value is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.run_command(CMD_ENGINE_START)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.run_command(CMD_ENGINE_STOP)


class FordPassLightSwitch(SwitchEntity):
    """灯光寻车开关（ZoneLightingON / ZoneLightingOFF）。

    灯光寻车是瞬时命令、车辆不回报灯光状态，开关状态为记忆值（assumed）。
    v3.0.5: 初始状态默认关闭（False），避免实体显示 unknown。
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
        return self.coordinator.last_update_success

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
    开关在触发后仍按 HONK_AUTO_OFF_SECONDS 自动复位（车辆鸣笛约 30 秒自停）。
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
        return self.coordinator.last_update_success

    @property
    def is_on(self) -> bool:
        return self._state

    async def _auto_off(self) -> None:
        await asyncio.sleep(HONK_AUTO_OFF_SECONDS)
        self._state = False
        self.async_write_ha_state()

    def _settings(self) -> tuple[int, int]:
        """读取当前鸣笛设置（select 实体保存于 hass.data / entry options）。

        v3.0.8: 返回 (持续时长秒, ChirpType)。找不到设置时回退默认
        （时长 10 秒 / 汽笛长鸣 ChirpType=3），与 App 默认一致。
        """
        settings = (
            self.coordinator.hass.data.get(DOMAIN, {})
            .get(self.coordinator.entry_id, {})
            .get("honk_settings", {})
        )
        try:
            duration = int(settings.get("honk_duration", 10) or 10)
        except (TypeError, ValueError):
            duration = 10
        try:
            chirp_type = int(settings.get("chirp_type", 3) or 3)
        except (TypeError, ValueError):
            chirp_type = 3
        return duration, chirp_type

    async def async_turn_on(self, **kwargs: Any) -> None:
        # v3.0.8: 鸣笛参数来自设置实体——持续时长/鸣笛类型直达车机
        duration, chirp_type = self._settings()
        try:
            resp = await self.coordinator.api.honk_command(
                self.coordinator.vin,
                duration=duration,
                interval=1,
                chirp_type=chirp_type,
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("FordPass 鸣笛寻车（POST 开）失败: %s", exc)
            raise
        if isinstance(resp, dict) and resp.get("commandId"):
            _LOGGER.info(
                "FordPass 鸣笛寻车已触发 commandId=%s（时长=%ss 类型=%s）",
                resp["commandId"], duration, chirp_type,
            )
        self._state = True
        self.async_write_ha_state()
        # 鸣笛 30 秒自动结束，开关同步复位
        self.coordinator.hass.async_create_task(self._auto_off())

    async def async_turn_off(self, **kwargs: Any) -> None:
        # 真实停止通道：DELETE /api/vehicles/v5/{vin}/honk（与 App 取消鸣笛一致）
        try:
            resp = await self.coordinator.api.honk_cancel_command(self.coordinator.vin)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("FordPass 鸣笛寻车（DELETE 关）失败: %s", exc)
            raise
        if isinstance(resp, dict) and resp.get("commandId"):
            _LOGGER.info("FordPass 鸣笛寻车已停止 commandId=%s", resp["commandId"])
        self._state = False
        self.async_write_ha_state()
