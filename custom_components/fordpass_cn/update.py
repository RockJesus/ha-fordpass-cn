"""Update platform: 车机固件版本。

v5.9.5: 把「固件升级中」这个文本传感器升级为 HA 标准的 ``update`` 实体。
数据仍来自 OTA setting-info 回读（``asIsVersion`` / ``toBeVersion`` /
``toBeReleaseNote``）与车况里的 ``firmwareUpgInProgress``，只是改成官方
语义：``installed_version`` / ``latest_version`` / ``in_progress``。

好处是前端会按「有更新 / 升级中 / 已是最新」三种状态渲染，语音和仪表盘
都能直接识别；固件下发必须在车机或福特派 App 上确认，因此本实体只做展示，
不实现 ``install``（不声明 INSTALL 能力，界面上不出现安装按钮）。
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import FordPassCoordinator

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

_FALSE_VALUES = {"false", "0", "null", "none", "not_supported", "notsupported", ""}


def _as_bool(value: Any) -> bool:
    """云端布尔字段解析（真布尔 / 0-1 / "true"-"false" 都能认）。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() not in _FALSE_VALUES
    return bool(value)


class FordPassFirmwareUpdate(UpdateEntity):
    """车机固件：已安装版本 vs 可升级版本，附带升级进行中状态。"""

    _attr_has_entity_name = True
    _attr_name = "车机固件"
    _attr_title = "车机系统"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    # 只读展示：固件下发需在车机/App 确认，不声明 INSTALL 能力。
    # 必须声明 PROGRESS，否则框架会用内部安装标志覆盖 in_progress，
    # 车机自己升级时前端看不到「升级中」。
    _attr_supported_features = UpdateEntityFeature.PROGRESS
    # 版本号属设备自身信息，归诊断类目（与「固件升级中」传感器一致）
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.uid_key}-firmware_update"
        self._attr_device_info = coordinator.device_info
        self._unsub: Any = None

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._unsub is None:
            self._unsub = self.coordinator.async_add_listener(self._on_update)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    @callback
    def _on_update(self) -> None:
        if self.hass is not None:
            self.async_write_ha_state()

    @property
    def _ota(self) -> dict[str, Any]:
        ota = (self.coordinator.data or {}).get("ota_setting")
        return ota if isinstance(ota, dict) else {}

    @staticmethod
    def _text(value: Any, limit: int | None = None) -> str | None:
        """云端字段转字符串（空值返回 None，过长的截断）。"""
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        return text[:limit] if limit else text

    @property
    def installed_version(self) -> str | None:
        return self._text(self._ota.get("asIsVersion"))

    @property
    def latest_version(self) -> str | None:
        return self._text(self._ota.get("toBeVersion")) or self.installed_version

    @property
    def release_summary(self) -> str | None:
        # HA 只保留 255 字符（state_attributes 里硬截断），这里先截避免噪音
        return self._text(self._ota.get("toBeReleaseNote"), 255)

    @property
    def in_progress(self) -> bool:
        status = (self.coordinator.data or {}).get("vehiclestatus") or {}
        if not isinstance(status, dict):
            return False
        node = status.get("firmwareUpgInProgress")
        if isinstance(node, dict):
            node = node.get("value")
        return _as_bool(node)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """OTA 配置回读到版本号才创建（无 OTA 能力的车型不建空实体）。"""
    payload = entry.runtime_data  # v5.3.0: runtime_data 替代 hass.data
    coordinators = payload.get("coordinators") or [payload["coordinator"]]
    entities: list[UpdateEntity] = []
    for coordinator in coordinators:
        ota = (coordinator.data or {}).get("ota_setting")
        if not isinstance(ota, dict) or not ota:
            continue
        if not (ota.get("asIsVersion") or ota.get("toBeVersion")):
            continue
        entities.append(FordPassFirmwareUpdate(coordinator))
    if entities:
        async_add_entities(entities)
