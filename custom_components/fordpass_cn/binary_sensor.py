"""车门窗未关汇总 binary_sensor（v6.6.8）。

对齐福特派 App 6.18 的车门/车窗/后备箱未关提醒：把 vehicle-status 里分立的
开闭状态聚合为一个「有未关」布尔实体，供自动化与通知直接使用（App 只在界面
提示，没有可供联动的状态出口）。

本实体为纯读派生：不改任何云端状态、不触发车辆动作。字段缺失（车型无该字段）
不计入判定，避免把「不支持」误报成「未关」。
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import FordPassCoordinator

# doorStatus 下的子字段（中文名用于属性展示）
_DOORS: tuple[tuple[str, str], ...] = (
    ("driverDoor", "主驾车门"),
    ("passengerDoor", "副驾车门"),
    ("leftRearDoor", "左后车门"),
    ("rightRearDoor", "右后车门"),
    ("tailgateDoor", "尾门"),
    ("hoodDoor", "引擎盖"),
    ("innerTailgateDoor", "内尾门"),
)

# windowPosition 下的子字段（天窗单独处理：可能在顶层，也可能在该子字段里）
_WINDOWS: tuple[tuple[str, str], ...] = (
    ("driverWindowPosition", "主驾车窗"),
    ("passWindowPosition", "副驾车窗"),
    ("rearDriverWindowPos", "左后车窗"),
    ("rearPassWindowPos", "右后车窗"),
)

_UNKNOWN_DOOR = {"unknown", "not supported", "not_supported", "null", ""}
_UNKNOWN_WINDOW = {"unknown", "not supported", "not_supported", "null", ""}


def _door_open(value: Any) -> bool | None:
    """车门开闭：True=未关，False=已关闭，None=未知/无该字段（不计入）。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value) != 0
    s = str(value).strip().lower()
    if s in _UNKNOWN_DOOR:
        return None
    if s in ("open", "opened", "ajar"):
        return True
    if s in ("closed", "close"):
        return False
    if s in ("1", "true"):
        return True
    if s in ("0", "false"):
        return False
    return None


def _window_open(value: Any) -> bool | None:
    """车窗/天窗位置：True=未关，False=已关闭，None=未知（不计入）。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value) > 0
    if value is None:
        return None
    s = str(value).strip().lower().replace("_", " ")
    if s in _UNKNOWN_WINDOW:
        return None
    if s in ("fully closed", "closed", "close"):
        return False
    if "closed" in s:
        return False
    # fully open / partially open / betfully 10 percent open 等未关闭枚举
    return True


class FordPassAjarBinarySensor(BinarySensorEntity):
    """车门窗未关（聚合）。on = 至少一处未关。"""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:car-door-lock-open"
    _attr_name = "车门窗未关"

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.uid_key}-ajar"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        # 与其他控制实体一致：有最后已知数据即可用，不因单轮失败变 unavailable
        return bool(self.coordinator.data)

    def _status(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        status = data.get("vehiclestatus") or {}
        return status if isinstance(status, dict) else {}

    def _open_items(self) -> list[str]:
        status = self._status()
        items: list[str] = []

        doors = status.get("doorStatus")
        if isinstance(doors, dict):
            for key, label in _DOORS:
                if _door_open(doors.get(key)):
                    items.append(label)

        windows = status.get("windowPosition")
        windows_dict = windows if isinstance(windows, dict) else {}
        for key, label in _WINDOWS:
            if _window_open(windows_dict.get(key)):
                items.append(label)

        # 天窗：顶层 sunroofPosition 优先，其次 windowPosition.sunroofPosition
        roof = status.get("sunroofPosition")
        if roof is None:
            roof = windows_dict.get("sunroofPosition")
        if _window_open(roof):
            items.append("天窗")

        return items

    @property
    def is_on(self) -> bool:
        return bool(self._open_items())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        items = self._open_items()
        return {
            "open_items": items,
            "open_count": len(items),
            "open_summary": "、".join(items) if items else "全部已关闭",
        }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    payload = entry.runtime_data
    coordinators = payload.get("coordinators") or [payload["coordinator"]]
    entities: list[FordPassAjarBinarySensor] = []
    for coordinator in coordinators:
        status = (coordinator.data or {}).get("vehiclestatus") or {}
        # 车型没有车门/车窗字段时不创建（避免恒 off 的假实体）
        if not (
            isinstance(status.get("doorStatus"), dict)
            or isinstance(status.get("windowPosition"), dict)
        ):
            continue
        entities.append(FordPassAjarBinarySensor(coordinator))

    if not entities:
        return
    async_add_entities(entities)

    def _mk_refresh(ent: FordPassAjarBinarySensor):
        def _refresh() -> None:
            # hass 注入前（实体添加阶段）预热刷新会触发，此时写状态会报错
            if ent.hass is not None:
                ent.async_write_ha_state()

        return _refresh

    for ent in entities:
        ent.async_on_remove(ent.coordinator.async_add_listener(_mk_refresh(ent)))
