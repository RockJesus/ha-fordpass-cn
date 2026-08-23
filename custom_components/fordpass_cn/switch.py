"""开关平台 - 远程发动机启动."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .base_entity import FordPassBaseEntity
from .const import DOMAIN, VEHICLES
from .coordinator import FordPassCoordinator

_LOGGER = logging.getLogger(__name__)


class FordPassRemoteStartSwitch(FordPassBaseEntity, SwitchEntity):
    """远程发动机启动开关."""

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        super().__init__(coordinator, "remote_start", "远程启动")
        self._attr_icon = "mdi:engine-outline"

    @property
    def is_on(self) -> bool | None:
        """返回远程启动状态."""
        # 通过发动机运转状态判断
        value = self._get_data("ignitionStatus", "value")
        if value is None:
            return None
        return value == "ON"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """远程启动发动机."""
        _LOGGER.debug("远程启动发动机: %s", self._vin)
        success = await self.coordinator.async_remote_start(start=True)
        if not success:
            _LOGGER.error("远程启动失败")
        # 立即刷新
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """远程关闭发动机."""
        _LOGGER.debug("远程关闭发动机: %s", self._vin)
        success = await self.coordinator.async_remote_start(start=False)
        if not success:
            _LOGGER.error("远程关闭失败")
        await self.coordinator.async_request_refresh()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """设置开关平台."""
    coordinators = hass.data[DOMAIN][entry.entry_id][VEHICLES]
    entities = [FordPassRemoteStartSwitch(c) for c in coordinators]
    async_add_entities(entities)
