"""二元传感器平台 - 发动机状态、车门状态等."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .base_entity import FordPassBaseEntity
from .const import DOMAIN, VEHICLES
from .coordinator import FordPassCoordinator


@dataclass(frozen=True)
class FordPassBinarySensorDescription(BinarySensorEntityDescription):
    """FordPass 二元传感器描述."""

    value_path: tuple[str, ...] = ()
    on_value: str | int | bool = True


# ============================================================
# 二元传感器定义
# ============================================================

BINARY_SENSOR_DESCRIPTIONS: tuple[FordPassBinarySensorDescription, ...] = (
    # 发动机运转状态
    FordPassBinarySensorDescription(
        key="ignition_status",
        name="发动机状态",
        icon="mdi:engine",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_path=("ignitionStatus", "value"),
        on_value="ON",
    ),
    # 左前车门
    FordPassBinarySensorDescription(
        key="door_front_left",
        name="左前车门",
        icon="mdi:car-door",
        device_class=BinarySensorDeviceClass.DOOR,
        value_path=("doorStatus", "driverDoorOpen"),
        on_value=True,
    ),
    # 右前车门
    FordPassBinarySensorDescription(
        key="door_front_right",
        name="右前车门",
        icon="mdi:car-door",
        device_class=BinarySensorDeviceClass.DOOR,
        value_path=("doorStatus", "passengerDoorOpen"),
        on_value=True,
    ),
    # 左后车门
    FordPassBinarySensorDescription(
        key="door_rear_left",
        name="左后车门",
        icon="mdi:car-door",
        device_class=BinarySensorDeviceClass.DOOR,
        value_path=("doorStatus", "rearLeftDoorOpen"),
        on_value=True,
    ),
    # 右后车门
    FordPassBinarySensorDescription(
        key="door_rear_right",
        name="右后车门",
        icon="mdi:car-door",
        device_class=BinarySensorDeviceClass.DOOR,
        value_path=("doorStatus", "rearRightDoorOpen"),
        on_value=True,
    ),
    # 发动机舱盖
    FordPassBinarySensorDescription(
        key="hood",
        name="发动机舱盖",
        icon="mdi:car",
        device_class=BinarySensorDeviceClass.DOOR,
        value_path=("doorStatus", "hoodOpen"),
        on_value=True,
    ),
    # 后备箱
    FordPassBinarySensorDescription(
        key="trunk",
        name="后备箱",
        icon="mdi:car-back",
        device_class=BinarySensorDeviceClass.DOOR,
        value_path=("doorStatus", "trunkOpen"),
        on_value=True,
    ),
)


class FordPassBinarySensor(FordPassBaseEntity, BinarySensorEntity):
    """FordPass 二元传感器实体."""

    entity_description: FordPassBinarySensorDescription

    def __init__(
        self,
        coordinator: FordPassCoordinator,
        description: FordPassBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator, description.key, description.name)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """返回传感器状态."""
        value = self._get_data(*self.entity_description.value_path)
        if value is None:
            return None
        return value == self.entity_description.on_value


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """设置二元传感器平台."""
    coordinators = hass.data[DOMAIN][entry.entry_id][VEHICLES]
    entities = []

    for coordinator in coordinators:
        for description in BINARY_SENSOR_DESCRIPTIONS:
            entities.append(FordPassBinarySensor(coordinator, description))

    async_add_entities(entities)
