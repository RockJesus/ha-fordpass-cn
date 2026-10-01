"""Select platform: 鸣笛寻车设置（持续时长 / 鸣笛类型）。

v3.0.8: 从福特派 App 设置页还原（2026-10-01 截图 + libapp.so 逆向）：
- 持续时长：5-20 秒（App 滑块，选项 5/10/15/20，默认 10）
- 鸣笛类型：雨落荷叶 / 急浪拍岸 / 汽笛长鸣 / 空谷回音 / 声光共舞
  （App 设置页 5 种，强度从弱到强；ChirpType = 索引 + 1，
   ChirpType=1 已对活网关实测有效，映射按 App 设置顺序推断）
选择即保存到 config entry options；鸣笛寻车开关开启时按此设置调用
POST /api/vehicles/v5/{vin}/honk（参数直达车机，等效上传设置）。
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import capability
from .const import (
    CHIRP_TYPE_OPTIONS,
    CONF_CHIRP_TYPE,
    CONF_HONK_DURATION,
    DEFAULT_CHIRP_TYPE,
    DEFAULT_HONK_DURATION,
    DOMAIN,
    HONK_DURATION_OPTIONS,
)
from .coordinator import FordPassCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: FordPassCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    status = coordinator.data.get("vehiclestatus", {}) or {}
    entities: list[SelectEntity] = []
    # 鸣笛寻车设置与鸣笛开关同为远程控车能力（crccFlag 有效才创建）
    if capability.usable(status, [["crccFlag"]]):
        settings = hass.data[DOMAIN][entry.entry_id].setdefault(
            "honk_settings",
            {
                CONF_HONK_DURATION: int(
                    entry.options.get(CONF_HONK_DURATION, DEFAULT_HONK_DURATION)
                ),
                CONF_CHIRP_TYPE: entry.options.get(
                    CONF_CHIRP_TYPE, DEFAULT_CHIRP_TYPE
                ),
            },
        )
        entities.append(
            FordPassHonkDurationSelect(hass, entry, coordinator, settings)
        )
        entities.append(
            FordPassChirpTypeSelect(hass, entry, coordinator, settings)
        )
    async_add_entities(entities)


class _FordPassHonkSettingSelect(SelectEntity):
    """Base class: persist option to config entry options + memory settings."""

    _attr_has_entity_name = False
    _attr_assumed_state = True

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: FordPassCoordinator,
        settings: dict[str, Any],
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self._settings = settings

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    def _persist(self, key: str, value: Any) -> None:
        """Write to memory settings + config entry options (survives restart)."""
        self._settings[key] = value
        new_options = {**self.entry.options, key: value}
        self.hass.config_entries.async_update_entry(self.entry, options=new_options)
        _LOGGER.info(
            "FordPass 鸣笛设置 %s -> %s（已保存）", key, value
        )


class FordPassHonkDurationSelect(_FordPassHonkSettingSelect):
    """持续时长（秒）：5 / 10 / 15 / 20，默认 10。"""

    _attr_icon = "mdi:timer-outline"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: FordPassCoordinator,
        settings: dict[str, Any],
    ) -> None:
        super().__init__(hass, entry, coordinator, settings)
        self._attr_unique_id = f"{coordinator.vin}-honk_duration"
        self._attr_name = "鸣笛持续时长"
        self._attr_device_info = coordinator.device_info
        self._attr_options = [str(v) for v in HONK_DURATION_OPTIONS]
        self._attr_current_option = str(
            settings.get(CONF_HONK_DURATION, DEFAULT_HONK_DURATION)
        )

    async def async_select_option(self, option: str) -> None:
        value = int(option)
        self._persist(CONF_HONK_DURATION, value)
        self._attr_current_option = str(value)
        self.async_write_ha_state()


class FordPassChirpTypeSelect(_FordPassHonkSettingSelect):
    """鸣笛类型：雨落荷叶…声光共舞（ChirpType 1-5，默认汽笛长鸣）。"""

    _attr_icon = "mdi:bullhorn-variant"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: FordPassCoordinator,
        settings: dict[str, Any],
    ) -> None:
        super().__init__(hass, entry, coordinator, settings)
        self._attr_unique_id = f"{coordinator.vin}-chirp_type"
        self._attr_name = "鸣笛类型"
        self._attr_device_info = coordinator.device_info
        self._attr_options = list(CHIRP_TYPE_OPTIONS)
        self._attr_current_option = settings.get(
            CONF_CHIRP_TYPE, DEFAULT_CHIRP_TYPE
        )

    async def async_select_option(self, option: str) -> None:
        self._persist(CONF_CHIRP_TYPE, option)
        self._attr_current_option = option
        self.async_write_ha_state()
