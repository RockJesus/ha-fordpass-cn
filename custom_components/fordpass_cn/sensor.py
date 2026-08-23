"""传感器平台 - 油量、里程、机油、电瓶等."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricPotential,
    UnitOfLength,
    UnitOfPressure,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.config_entries import ConfigEntry

from .base_entity import FordPassBaseEntity
from .const import DOMAIN, VEHICLES
from .coordinator import FordPassCoordinator


@dataclass(frozen=True)
class FordPassSensorDescription(SensorEntityDescription):
    """FordPass 传感器描述."""

    value_path: tuple[str, ...] = ()
    unit_conversion: str | None = None


# ============================================================
# 传感器定义
# ============================================================

SENSOR_DESCRIPTIONS: tuple[FordPassSensorDescription, ...] = (
    # 燃油
    FordPassSensorDescription(
        key="fuel",
        name="燃油量",
        icon="mdi:fuel",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_path=("fuel", "fuelLevel"),
    ),
    # 总里程
    FordPassSensorDescription(
        key="odometer",
        name="总里程",
        icon="mdi:counter",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_path=("odometer", "value"),
    ),
    # 剩余续航
    FordPassSensorDescription(
        key="range",
        name="剩余续航",
        icon="mdi:map-marker-distance",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        value_path=("fuel", "distanceToEmpty"),
    ),
    # 机油寿命
    FordPassSensorDescription(
        key="oil_life",
        name="机油寿命",
        icon="mdi:oil",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_path=("oil", "oilLife"),
    ),
    # 电瓶电压
    FordPassSensorDescription(
        key="battery_voltage",
        name="电瓶电压",
        icon="mdi:car-battery",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        value_path=("battery", "batteryVoltage"),
    ),
    # 电瓶健康状态
    FordPassSensorDescription(
        key="battery_health",
        name="电瓶健康状态",
        icon="mdi:heart-pulse",
        value_path=("battery", "batteryHealthStatus"),
    ),
    # 左前胎压
    FordPassSensorDescription(
        key="tire_pressure_front_left",
        name="左前胎压",
        icon="mdi:car-tire-alert",
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.KPA,
        state_class=SensorStateClass.MEASUREMENT,
        value_path=("TPMS", "leftFrontTirePressure", "value"),
    ),
    # 右前胎压
    FordPassSensorDescription(
        key="tire_pressure_front_right",
        name="右前胎压",
        icon="mdi:car-tire-alert",
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.KPA,
        state_class=SensorStateClass.MEASUREMENT,
        value_path=("TPMS", "rightFrontTirePressure", "value"),
    ),
    # 左后胎压
    FordPassSensorDescription(
        key="tire_pressure_rear_left",
        name="左后胎压",
        icon="mdi:car-tire-alert",
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.KPA,
        state_class=SensorStateClass.MEASUREMENT,
        value_path=("TPMS", "leftRearTirePressure", "value"),
    ),
    # 右后胎压
    FordPassSensorDescription(
        key="tire_pressure_rear_right",
        name="右后胎压",
        icon="mdi:car-tire-alert",
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.KPA,
        state_class=SensorStateClass.MEASUREMENT,
        value_path=("TPMS", "rightRearTirePressure", "value"),
    ),
    # 左前车窗位置
    FordPassSensorDescription(
        key="window_front_left",
        name="左前车窗位置",
        icon="mdi:window-open",
        native_unit_of_measurement=PERCENTAGE,
        value_path=("windowPosition", "driverWindowPosition"),
    ),
    # 右前车窗位置
    FordPassSensorDescription(
        key="window_front_right",
        name="右前车窗位置",
        icon="mdi:window-open",
        native_unit_of_measurement=PERCENTAGE,
        value_path=("windowPosition", "passengerWindowPosition"),
    ),
    # 报警状态
    FordPassSensorDescription(
        key="alarm",
        name="报警状态",
        icon="mdi:alarm-light",
        value_path=("alarm", "alarmStatus"),
    ),
)


class FordPassSensor(FordPassBaseEntity, SensorEntity):
    """FordPass 传感器实体."""

    entity_description: FordPassSensorDescription

    def __init__(
        self,
        coordinator: FordPassCoordinator,
        description: FordPassSensorDescription,
    ) -> None:
        super().__init__(coordinator, description.key, description.name)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """返回传感器值."""
        value = self._get_data(*self.entity_description.value_path)
        if value is None:
            return None

        # 尝试转换为数值
        try:
            if isinstance(value, str):
                return float(value) if "." in value else int(value)
            return value
        except (ValueError, TypeError):
            return value


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """设置传感器平台."""
    coordinators = hass.data[DOMAIN][entry.entry_id][VEHICLES]
    entities = []

    for coordinator in coordinators:
        for description in SENSOR_DESCRIPTIONS:
            entities.append(FordPassSensor(coordinator, description))

    async_add_entities(entities)
