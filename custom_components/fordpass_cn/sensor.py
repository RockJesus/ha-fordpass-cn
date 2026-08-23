"""传感器平台."""
from __future__ import annotations
from dataclasses import dataclass
from homeassistant.components.sensor import (SensorDeviceClass, SensorEntity,
    SensorEntityDescription, SensorStateClass)
from homeassistant.const import PERCENTAGE, UnitOfElectricPotential, UnitOfLength, UnitOfPressure
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .base_entity import FordPassBaseEntity
from .const import DOMAIN, VEHICLES
from .coordinator import FordPassCoordinator


@dataclass(frozen=True)
class FordPassSensorDescription(SensorEntityDescription):
    value_path: tuple = ()


SENSOR_DESCRIPTIONS = (
    FordPassSensorDescription(key="fuel", name="燃油量", icon="mdi:fuel",
        native_unit_of_measurement=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT,
        value_path=("fuel","fuelLevel")),
    FordPassSensorDescription(key="odometer", name="总里程", icon="mdi:counter",
        device_class=SensorDeviceClass.DISTANCE, native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.TOTAL_INCREASING, value_path=("odometer","value")),
    FordPassSensorDescription(key="range", name="剩余续航", icon="mdi:map-marker-distance",
        device_class=SensorDeviceClass.DISTANCE, native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.MEASUREMENT, value_path=("fuel","distanceToEmpty")),
    FordPassSensorDescription(key="oil_life", name="机油寿命", icon="mdi:oil",
        native_unit_of_measurement=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT,
        value_path=("oil","oilLife")),
    FordPassSensorDescription(key="battery_voltage", name="电瓶电压", icon="mdi:car-battery",
        device_class=SensorDeviceClass.VOLTAGE, native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT, value_path=("battery","batteryVoltage")),
    FordPassSensorDescription(key="battery_health", name="电瓶健康状态", icon="mdi:heart-pulse",
        value_path=("battery","batteryHealthStatus")),
    FordPassSensorDescription(key="tire_fl", name="左前胎压", icon="mdi:car-tire-alert",
        device_class=SensorDeviceClass.PRESSURE, native_unit_of_measurement=UnitOfPressure.KPA,
        state_class=SensorStateClass.MEASUREMENT, value_path=("TPMS","leftFrontTirePressure","value")),
    FordPassSensorDescription(key="tire_fr", name="右前胎压", icon="mdi:car-tire-alert",
        device_class=SensorDeviceClass.PRESSURE, native_unit_of_measurement=UnitOfPressure.KPA,
        state_class=SensorStateClass.MEASUREMENT, value_path=("TPMS","rightFrontTirePressure","value")),
    FordPassSensorDescription(key="tire_rl", name="左后胎压", icon="mdi:car-tire-alert",
        device_class=SensorDeviceClass.PRESSURE, native_unit_of_measurement=UnitOfPressure.KPA,
        state_class=SensorStateClass.MEASUREMENT, value_path=("TPMS","leftRearTirePressure","value")),
    FordPassSensorDescription(key="tire_rr", name="右后胎压", icon="mdi:car-tire-alert",
        device_class=SensorDeviceClass.PRESSURE, native_unit_of_measurement=UnitOfPressure.KPA,
        state_class=SensorStateClass.MEASUREMENT, value_path=("TPMS","rightRearTirePressure","value")),
    FordPassSensorDescription(key="window_fl", name="左前车窗位置", icon="mdi:window-open",
        native_unit_of_measurement=PERCENTAGE, value_path=("windowPosition","driverWindowPosition")),
    FordPassSensorDescription(key="window_fr", name="右前车窗位置", icon="mdi:window-open",
        native_unit_of_measurement=PERCENTAGE, value_path=("windowPosition","passengerWindowPosition")),
    FordPassSensorDescription(key="alarm", name="报警状态", icon="mdi:alarm-light",
        value_path=("alarm","alarmStatus")),
)


class FordPassSensor(FordPassBaseEntity, SensorEntity):
    entity_description: FordPassSensorDescription
    def __init__(self, coordinator, description):
        super().__init__(coordinator, description.key, description.name)
        self.entity_description = description
    @property
    def native_value(self):
        value = self._get_data(*self.entity_description.value_path)
        if value is None:
            return None
        try:
            if isinstance(value, str):
                return float(value) if "." in value else int(value)
            return value
        except (ValueError, TypeError):
            return value


async def async_setup_entry(hass, entry, async_add_entities):
    coordinators = hass.data[DOMAIN][entry.entry_id][VEHICLES]
    entities = []
    for coordinator in coordinators:
        for description in SENSOR_DESCRIPTIONS:
            entities.append(FordPassSensor(coordinator, description))
    async_add_entities(entities)
