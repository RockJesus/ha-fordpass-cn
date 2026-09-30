"""Sensor platform: vehicle status readings."""
from __future__ import annotations

import datetime
import time

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfLength, UnitOfPressure
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import FordPassCoordinator


def _leaf(status, *keys, default=None):
    cur = status
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    if isinstance(cur, dict):
        return cur.get("value", default)
    return cur


def _leaf_node(status, *keys):
    """Return the raw dict node (value/status/timestamp) if it is a dict."""
    cur = status
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur if isinstance(cur, dict) else None


def _first_leaf(status, paths, default=None):
    """Try several candidate paths and return the first non-None value."""
    for p in paths:
        val = _leaf(status, *p)
        if val is not None:
            return val
    return default


def _first_leaf_node(status, paths):
    """Try several candidate paths and return the first dict node."""
    for p in paths:
        node = _leaf_node(status, *p)
        if node is not None:
            return node
    return None


# 无实际数据的值（v2.9.0）：这些字段值为 None / "Null" / "Not_Supported" /
# "NotAvailable" / 空串时视为无效——实体不创建，避免其他车型用户看到一屏 unknown。
_INVALID_VALUES = {"", "null", "none", "n/a", "not_supported", "notsupported",
                   "not available", "notavailable", "unknown"}


def _is_usable(status, paths) -> bool:
    """值是否有效（非 None / 无效占位串）——决定实体是否创建。"""
    val = _first_leaf(status, paths)
    if val is None:
        return False
    if isinstance(val, str):
        return val.strip().lower() not in _INVALID_VALUES
    if isinstance(val, (list, dict)):
        return False
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: FordPassCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    sensors = [
        FordPassSensor(coordinator, "fuel_level", "燃油量", "%", SensorDeviceClass.BATTERY, "mdi:fuel", ["fuel", "fuelLevel"], round_value=True),
        FordPassSensor(coordinator, "distance_to_empty", "续航里程", UnitOfLength.KILOMETERS, None, "mdi:road-variant", ["fuel", "distanceToEmpty"]),
        FordPassSensor(coordinator, "odometer", "总里程", UnitOfLength.KILOMETERS, None, "mdi:counter", ["odometer"]),
        FordPassSensor(coordinator, "oil_life", "机油寿命", "%", None, "mdi:oil", ["oil", "oilLifeActual"]),
        FordPassSensor(coordinator, "battery_voltage", "蓄电池电压", "V", SensorDeviceClass.VOLTAGE, "mdi:car-battery", ["battery", "batteryStatusActual"]),
        FordPassSensor(coordinator, "lf_tire", "左前轮胎压", UnitOfPressure.KPA, SensorDeviceClass.PRESSURE, "mdi:gauge", ["TPMS", "leftFrontTirePressure"], round_value=True),
        FordPassSensor(coordinator, "rf_tire", "右前轮胎压", UnitOfPressure.KPA, SensorDeviceClass.PRESSURE, "mdi:gauge", ["TPMS", "rightFrontTirePressure"], round_value=True),
        FordPassSensor(coordinator, "lr_tire", "左后轮胎压", UnitOfPressure.KPA, SensorDeviceClass.PRESSURE, "mdi:gauge", ["TPMS", "outerLeftRearTirePressure"], round_value=True),
        FordPassSensor(coordinator, "rr_tire", "右后轮胎压", UnitOfPressure.KPA, SensorDeviceClass.PRESSURE, "mdi:gauge", ["TPMS", "outerRightRearTirePressure"], round_value=True),
        FordPassSensor(coordinator, "lock_status", "门锁状态", None, None, "mdi:lock", ["lockStatus"], enum_map={"LOCKED": "已锁定", "UNLOCKED": "已解锁", 1: "已锁定", 0: "已解锁", "1": "已锁定", "0": "已解锁", True: "已锁定", False: "已解锁"}),
        # 报警状态映射（v2.6.3 最新语义）：SET=已设防 / NOTSET=未设防 /
        # NOT_IN_ALARM·DISARMED=解除报警 / ALARM·ARMED=被盗声光报警中
        FordPassSensor(coordinator, "alarm_status", "报警状态", None, None, "mdi:alarm", ["alarm"],
                       enum_map={"SET": "车辆已设防", "NOTSET": "车辆未设防",
                                 "NOT_IN_ALARM": "解除报警", "NOT_IN_ALARMS": "解除报警",
                                 "DISARMED": "解除报警", "ALARM": "被盗声光报警中",
                                 "ARMED": "被盗声光报警中", 0: "解除报警", 1: "被盗声光报警中",
                                 "0": "解除报警", "1": "被盗声光报警中",
                                 False: "解除报警", True: "被盗声光报警中"}),
        FordPassSensor(coordinator, "remote_start", "远程启动状态", None, None, "mdi:engine", ["remoteStartStatus"], enum_map={1: "已远程启动", 0: "未远程启动", "1": "已远程启动", "0": "未远程启动", True: "已远程启动", False: "未远程启动", "true": "已远程启动", "false": "未远程启动"}),
    ]
    # 车辆异常警示：v2.6.5 起合并为单一实体 —— 优先读 vha/activealert
    # 真实告警（明文中文标题）；接口不可用/无数据时回退 PrmtAlarmEvent
    # 等历史字段（Null = 无异常）。原 vehicle_warning_fallback 实体已移除。
    sensors.append(FordPassAlertSensor(coordinator))
    sensors.append(FordPassVehicleAttrSensor(coordinator, "license_plate", "车牌号", "license_plate", "mdi:car"))
    sensors.append(FordPassVehicleAttrSensor(coordinator, "vehicle_nickname", "车辆昵称", "nickname", "mdi:car-info"))
    sensors.append(FordPassVehicleAttrSensor(coordinator, "vehicle_vin", "车辆识别码", "vin", "mdi:identifier"))
    sensors.append(FordPassLocationSensor(coordinator))

    # ===== A 组：车辆状态（车门 / 点火 / 车窗） =====
    _door_map = {"Closed": "已关闭", "Open": "已打开", "Ajar": "未关紧", "Unknown": "未知"}
    _win_map = {"Fully_Closed": "已关闭", "Fully_Open": "完全开启", "Partially_Open": "部分开启",
                "Partial_Open": "部分开启", "Unknown": "未知"}
    sensors += [
        FordPassSensor(coordinator, "driver_door", "主驾车门", None, None, "mdi:car-door", ["doorStatus", "driverDoor"], enum_map=_door_map),
        FordPassSensor(coordinator, "passenger_door", "副驾车门", None, None, "mdi:car-door", ["doorStatus", "passengerDoor"], enum_map=_door_map),
        FordPassSensor(coordinator, "left_rear_door", "左后车门", None, None, "mdi:car-door", ["doorStatus", "leftRearDoor"], enum_map=_door_map),
        FordPassSensor(coordinator, "right_rear_door", "右后车门", None, None, "mdi:car-door", ["doorStatus", "rightRearDoor"], enum_map=_door_map),
        FordPassSensor(coordinator, "tailgate", "尾门", None, None, "mdi:car-back", ["doorStatus", "tailgateDoor"], enum_map=_door_map),
        FordPassSensor(coordinator, "hood", "引擎盖", None, None, "mdi:car", ["doorStatus", "hoodDoor"], enum_map=_door_map),
        FordPassSensor(coordinator, "ignition", "点火状态", None, None, "mdi:engine", ["ignitionStatus"], enum_map={"Off": "已熄火", "On": "已启动", "Run": "已启动", "Unknown": "未知"}),
        FordPassSensor(coordinator, "driver_window", "主驾车窗", None, None, "mdi:window-closed", ["windowPosition", "driverWindowPosition"], enum_map=_win_map),
        FordPassSensor(coordinator, "passenger_window", "副驾车窗", None, None, "mdi:window-closed", ["windowPosition", "passWindowPosition"], enum_map=_win_map),
        FordPassSensor(coordinator, "left_rear_window", "左后车窗", None, None, "mdi:window-closed", ["windowPosition", "rearDriverWindowPos"], enum_map=_win_map),
        FordPassSensor(coordinator, "right_rear_window", "右后车窗", None, None, "mdi:window-closed", ["windowPosition", "rearPassWindowPos"], enum_map=_win_map),
    ]

    # ===== B 组：健康诊断（机油 / 电池 / 胎压） =====
    _health_map = {"STATUS_GOOD": "良好", "STATUS_NEEDS_SERVICE": "需要保养", "STATUS_WARNING": "警告", "STATUS_CRITICAL": "严重"}
    _tire_status_map = {"Fault": "故障", "OK": "正常", "Normal": "正常", "Not_Supported": "不支持"}
    sensors += [
        FordPassSensor(coordinator, "oil_health", "机油健康状态", None, None, "mdi:oil", ["oil", "oilLife"], enum_map=_health_map),
        FordPassSensor(coordinator, "battery_health", "蓄电池健康状态", None, None, "mdi:car-battery", ["battery", "batteryHealth"], enum_map=_health_map),
        FordPassSensor(coordinator, "tpms_system_status", "胎压系统状态", None, None, "mdi:car-tire-alert", ["TPMS", "tirePressureSystemStatus"],
                       enum_map={"Systm_Fault_Composite_Stat": "系统故障", "Systm_Normal_Stat": "正常", "Systm_Warning_Stat": "系统警告", "Normal": "正常"}),
        FordPassSensor(coordinator, "lf_tire_status", "左前胎状态", None, None, "mdi:car-tire-alert", ["TPMS", "leftFrontTireStatus"], enum_map=_tire_status_map),
        FordPassSensor(coordinator, "rf_tire_status", "右前胎状态", None, None, "mdi:car-tire-alert", ["TPMS", "rightFrontTireStatus"], enum_map=_tire_status_map),
        FordPassSensor(coordinator, "lr_tire_status", "左后胎状态", None, None, "mdi:car-tire-alert", ["TPMS", "outerLeftRearTireStatus"], enum_map=_tire_status_map),
        FordPassSensor(coordinator, "rr_tire_status", "右后胎状态", None, None, "mdi:car-tire-alert", ["TPMS", "outerRightRearTireStatus"], enum_map=_tire_status_map),
        # 推荐胎压：原始值单位 psi，换算为 kPa（1 psi ≈ 6.89476 kPa）与实车胎压单位一致
        FordPassSensor(coordinator, "recommended_front_pressure", "推荐前轮胎压", UnitOfPressure.KPA, SensorDeviceClass.PRESSURE, "mdi:gauge",
                       ["TPMS", "recommendedFrontTirePressure"], round_value=True, multiplier=6.89476),
        FordPassSensor(coordinator, "recommended_rear_pressure", "推荐后轮胎压", UnitOfPressure.KPA, SensorDeviceClass.PRESSURE, "mdi:gauge",
                       ["TPMS", "recommendedRearTirePressure"], round_value=True, multiplier=6.89476),
    ]

    # ===== C 组：其他状态 =====
    sensors += [
        FordPassSensor(coordinator, "remote_start_duration", "远程启动时长", "分钟", None, "mdi:clock-outline", ["remoteStart", "remoteStartDuration"]),
        # v2.8.0: 远程启动时间（unix 秒 → 本地时间字符串）与动态"距离自动熄火时间"倒计时
        # v3.0.2: 未启动（值为 0）时显示「未启动」，不再显示 unknown
        FordPassSensor(coordinator, "remote_start_time", "远程启动时间", None, None, "mdi:clock-start",
                       ["remoteStart", "remoteStartTime"], transform=_remote_start_time),
        FordPassAutoOffSensor(coordinator),
        FordPassSensor(coordinator, "authorization", "授权状态", None, None, "mdi:shield-check", ["authorization"],
                       enum_map={"AUTHORIZED": "已授权", "UNAUTHORIZED": "未授权", "EXPIRED": "已过期"}),
        FordPassSensor(coordinator, "crcc_flag", "远程控车功能", None, None, "mdi:remote", ["crccFlag"],
                       enum_map={"ON": "已开启", "OFF": "已关闭"}),
        FordPassSensor(coordinator, "life_cycle_mode", "电池生命周期模式", None, None, "mdi:car-battery", ["lifeCycMode"],
                       enum_map={"Normal": "标准模式", "Life_Cycle_Mode": "长寿命模式", "Deep_Discharge": "深度放电"}),
        FordPassSensor(coordinator, "out_and_about", "出行状态", None, None, "mdi:map-marker-path", ["outandAbout"],
                       transform=lambda v: "不可用" if isinstance(v, str) and "NotAvailable" in v else v),
    ]

    # ===== D 组：各车型可选字段（纯电/混动/柴油/拖车/车内环境等，v2.9.0） =====
    # 全部 skip_if_missing=True：值无效（null/Not_Supported/...）的实体不创建，
    # 保证任何车型登录后只看到自己有真实数据的实体（如锐际看不到充电状态）。
    _charge_map = {"Charging": "充电中", "NotCharging": "未充电", "FullyCharged": "已充满",
                   "Complete": "已完成", "Charged": "已充满", "Discharging": "放电中"}
    _plug_map = {"Connected": "已连接", "Disconnected": "未连接", "Unplugged": "未插枪"}
    _bool_map = {True: "是", False: "否", "true": "是", "false": "否", 1: "是", 0: "否", "1": "是", "0": "否"}
    _hybrid_map = {"EV": "纯电模式", "HEV": "混动模式", "ER": "增程模式", "Off": "关闭"}
    _precond_map = {"ON": "开启", "OFF": "关闭", "Running": "运行中", "PENDING": "等待中", "ERROR": "异常"}
    _diesel_metric_map = {"Active": "激活", "Inactive": "未激活", "Regeneration": "再生中"}
    sensors += [
        # 纯电 / 插混（电马 Mustang Mach-E、锐界 L 混动等）
        FordPassSensor(coordinator, "battery_fill_level", "动力电池电量", "%", SensorDeviceClass.BATTERY, "mdi:battery-high", ["batteryFillLevel"], round_value=True, skip_if_missing=True),
        FordPassSensor(coordinator, "ev_dte", "纯电续航", UnitOfLength.KILOMETERS, None, "mdi:lightning-bolt", ["elVehDTE"], skip_if_missing=True),
        FordPassSensor(coordinator, "charging_status", "充电状态", None, None, "mdi:power-plug", ["chargingStatus"], enum_map=_charge_map, skip_if_missing=True),
        FordPassSensor(coordinator, "plug_status", "充电插枪状态", None, None, "mdi:power-plug", ["plugStatus"], enum_map=_plug_map, skip_if_missing=True),
        FordPassSensor(coordinator, "charge_start_time", "充电开始时间", None, None, "mdi:clock-start", ["chargeStartTime"], transform=_ts_or_str, skip_if_missing=True),
        FordPassSensor(coordinator, "charge_end_time", "充电结束时间", None, None, "mdi:clock-end", ["chargeEndTime"], transform=_ts_or_str, skip_if_missing=True),
        FordPassSensor(coordinator, "battery_charge_status", "动力电池状态", None, None, "mdi:battery-charging", ["batteryChargeStatus"], enum_map=_charge_map, skip_if_missing=True),
        FordPassSensor(coordinator, "batt_trac_low_soc", "纯电低电量阈值", "%", None, "mdi:battery-low", ["battTracLoSocDDsply"], skip_if_missing=True),
        FordPassSensor(coordinator, "battery_perf_status", "动力电池性能", None, None, "mdi:battery-outline", ["batteryPerfStatus"], enum_map=_bool_map, skip_if_missing=True),
        # 远程空调 / 混动模式
        FordPassSensor(coordinator, "pre_cond_status", "远程空调状态", None, None, "mdi:air-conditioner", ["preCondStatusDsply"], enum_map=_precond_map, skip_if_missing=True),
        FordPassSensor(coordinator, "hybrid_mode", "驱动模式", None, None, "mdi:car-electric", ["hybridModeStatus"], enum_map=_hybrid_map, skip_if_missing=True),
        # 柴油（领裕/撼路者柴油版等）
        FordPassSensor(coordinator, "diesel_urea", "尿素液位", "%", None, "mdi:water-percent", ["dieselSystemStatus", "exhaustFluidLevel"], skip_if_missing=True),
        FordPassSensor(coordinator, "diesel_urea_range", "尿素续航", UnitOfLength.KILOMETERS, None, "mdi:road-variant", ["dieselSystemStatus", "ureaRange"], skip_if_missing=True),
        FordPassSensor(coordinator, "diesel_metric", "柴油系统状态", None, None, "mdi:engine", ["dieselSystemStatus", "metricType"], enum_map=_diesel_metric_map, skip_if_missing=True),
        FordPassSensor(coordinator, "diesel_filter_soot", "颗粒滤清器积碳", None, None, "mdi:filter", ["dieselSystemStatus", "filterSoot"], skip_if_missing=True),
        FordPassSensor(coordinator, "diesel_filter_regeneration", "滤清器再生状态", None, None, "mdi:autorenew", ["dieselSystemStatus", "filterRegenerationStatus"], enum_map=_diesel_metric_map, skip_if_missing=True),
        # 六胎车型（皮卡/拖车）内胎与双后轮
        FordPassSensor(coordinator, "inner_lr_tire", "内左后轮胎压", UnitOfPressure.KPA, SensorDeviceClass.PRESSURE, "mdi:gauge", ["TPMS", "innerLeftRearTirePressure"], round_value=True, skip_if_missing=True),
        FordPassSensor(coordinator, "inner_rr_tire", "内右后轮胎压", UnitOfPressure.KPA, SensorDeviceClass.PRESSURE, "mdi:gauge", ["TPMS", "innerRightRearTirePressure"], round_value=True, skip_if_missing=True),
        FordPassSensor(coordinator, "inner_lr_tire_status", "内左后胎状态", None, None, "mdi:car-tire-alert", ["TPMS", "innerLeftRearTireStatus"], enum_map=_tire_status_map, skip_if_missing=True),
        FordPassSensor(coordinator, "inner_rr_tire_status", "内右后胎状态", None, None, "mdi:car-tire-alert", ["TPMS", "innerRightRearTireStatus"], enum_map=_tire_status_map, skip_if_missing=True),
        FordPassSensor(coordinator, "dual_rear_wheel", "双后轮", None, None, "mdi:car", ["TPMS", "dualRearWheel"], enum_map={1: "启用", 0: "停用", "1": "启用", "0": "停用", True: "启用", False: "停用"}, skip_if_missing=True),
        # 车门 / 车内环境 / 状态标志
        FordPassSensor(coordinator, "inner_tailgate", "内尾门", None, None, "mdi:car-back", ["doorStatus", "innerTailgateDoor"], enum_map=_door_map, skip_if_missing=True),
        FordPassSensor(coordinator, "cabin_temp", "车内温度", "°C", SensorDeviceClass.TEMPERATURE, "mdi:thermometer", ["CabnAmbTeActl"], round_value=True, skip_if_missing=True),
        FordPassSensor(coordinator, "deep_sleep", "深度睡眠模式", None, None, "mdi:sleep", ["deepSleepInProgress"], enum_map=_bool_map, skip_if_missing=True),
        FordPassSensor(coordinator, "firmware_upgrade", "固件升级中", None, None, "mdi:update", ["firmwareUpgInProgress"], enum_map=_bool_map, skip_if_missing=True),
        # v3.0.1: 天窗（部分车型上报 sunroofPosition；无该字段的车型不创建）
        FordPassSensor(coordinator, "sunroof", "天窗", None, None, "mdi:car-select",
                       [["sunroofPosition"], ["windowPosition", "sunroofPosition"], ["sunroof"]],
                       enum_map={"Closed": "已关闭", "Open": "已打开", "Open_Tilt": "倾斜开启",
                                 "Tilt": "倾斜", "Partially_Open": "部分开启", "Vent": "通风",
                                 "Unknown": "未知"}, skip_if_missing=True),
    ]

    # v2.9.0: 创建期过滤——数据无效（null/Not_Supported/...）的实体不创建，
    # 任何车型登录后只出现有真实数据的实体，不再显示一屏 unknown。
    sensors = [s for s in sensors if getattr(s, "data_usable", True)]
    async_add_entities(sensors)


class FordPassVehicleAttrSensor(SensorEntity):
    """Reads a fixed attribute stored on the coordinator (e.g. license plate)."""

    def __init__(self, coordinator, key, label, attr, icon=None) -> None:
        self.coordinator = coordinator
        self._attr_key = attr
        self._attr_unique_id = f"{coordinator.vin}-{key}"
        self._attr_name = label
        self._attr_has_entity_name = False
        self._attr_device_info = coordinator.device_info
        if icon:
            self._attr_icon = icon

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def native_value(self):
        return getattr(self.coordinator, self._attr_key, None)

    @property
    def extra_state_attributes(self) -> dict:
        """v2.7.8: last_poll（最后拉取状态）"""
        return {
            "last_poll": (
                f"{self.coordinator.last_poll:%Y-%m-%d %H:%M:%S}"
                if self.coordinator.last_poll
                else None
            )
        }


class FordPassLocationSensor(SensorEntity):
    """Vehicle location (address / coordinates), best effort."""

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-location"
        self._attr_name = "车辆定位"
        self._attr_has_entity_name = False
        self._attr_device_info = coordinator.device_info
        self._attr_icon = "mdi:map-marker"

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data or {}
        loc = data.get("location")
        attrs: dict = {}
        if isinstance(loc, dict):
            attrs = {
                "latitude": loc.get("lat"),
                "longitude": loc.get("lon"),
                "upload_time": loc.get("uploadTime"),
                "address": loc.get("address"),
            }
        # v2.7.8: last_poll（最后拉取状态）
        attrs["last_poll"] = (
            f"{self.coordinator.last_poll:%Y-%m-%d %H:%M:%S}"
            if self.coordinator.last_poll
            else None
        )
        return attrs

    @property
    def native_value(self):
        data = self.coordinator.data or {}
        loc = data.get("location")
        if not loc or not isinstance(loc, dict):
            return "定位不可用"
        address = loc.get("address")
        if address:
            return address
        lat, lon = loc.get("lat"), loc.get("lon")
        if lat and lon:
            return f"{lat}, {lon}"
        return "定位不可用"


class FordPassSensor(SensorEntity):
    def __init__(self, coordinator, key, label, unit, device_class, icon, path,
                 round_value: bool = False, enum_map: dict | None = None,
                 multiplier: float | None = None, transform=None,
                 skip_if_missing: bool = False) -> None:
        self.coordinator = coordinator
        self._key = key
        # Normalise `path`: a single path (["a","b"]) or a list of candidate
        # paths ([["a","b"],["c"]]); the first hit wins.
        if path and isinstance(path[0], str):
            self._paths = [path]
        else:
            self._paths = [p for p in (path or []) if p]
        self._round_value = round_value
        self._enum_map = enum_map
        self._multiplier = multiplier
        self._transform = transform
        # v2.9.0: 数据无效（null/Not_Supported/...）时不创建该实体，其他车型
        # 用户不会看到 unknown 实体；字段恢复有效后重载集成即可出现。
        self._skip_if_missing = skip_if_missing
        self._attr_unique_id = f"{coordinator.vin}-{key}"
        self._attr_name = label
        self._attr_has_entity_name = False
        self._attr_device_info = coordinator.device_info
        self._attr_icon = icon
        if unit:
            self._attr_native_unit_of_measurement = unit
        if device_class:
            self._attr_device_class = device_class

    @property
    def data_usable(self) -> bool:
        """创建期过滤：skip_if_missing 的实体仅当字段当前有有效值时创建。"""
        if not self._skip_if_missing:
            return True
        status = self.coordinator.data.get("vehiclestatus", {})
        return _is_usable(status, self._paths)

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def extra_state_attributes(self) -> dict:
        """Expose Ford's own data timestamp/status for this reading (v2.7.2)."""
        status = self.coordinator.data.get("vehiclestatus", {})
        attrs: dict = {}
        node = _first_leaf_node(status, self._paths)
        if isinstance(node, dict):
            attrs["timestamp"] = node.get("timestamp")
            attrs["source_status"] = node.get("status")
        if isinstance(status, dict):
            attrs["vehicle_data_time"] = status.get("lastModifiedDate") or status.get("lastRefresh")
        # v2.7.8: last_poll（最后拉取状态）——最近一次成功拉取（自动/手动）的本地时间
        attrs["last_poll"] = (
            f"{self.coordinator.last_poll:%Y-%m-%d %H:%M:%S}"
            if self.coordinator.last_poll
            else None
        )
        return attrs

    @property
    def native_value(self):
        status = self.coordinator.data.get("vehiclestatus", {})
        value = _first_leaf(status, self._paths)
        if isinstance(value, (list, dict)):
            if isinstance(value, list) and value and all(isinstance(x, str) for x in value):
                value = "、".join(value)
            else:
                return None
        if self._multiplier is not None and isinstance(value, (int, float)) and not isinstance(value, bool):
            value = value * self._multiplier
        if self._round_value and isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(round(value))
        if self._enum_map is not None:
            value = self._enum_map.get(value, value)
        if self._transform is not None:
            value = self._transform(value)
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value


class FordPassAlertSensor(SensorEntity):
    """Vehicle health alerts, single merged entity (v2.6.5).

    Primary source: /vha/activealert plaintext Chinese headlines (joined with
    "、").  When the endpoint returns nothing or is unavailable, fall back to
    the vehicle-status PrmtAlarmEvent / warning fields (Null => 无异常).
    """

    _FALLBACK_PATHS = [
        ["PrmtAlarmEvent"], ["warning"], ["warnings"], ["alerts"],
        ["vehicleAbnormal"], ["abnormal"], ["abnormalWarning"],
        ["abnormalStatus"], ["warningStatus"], ["faults"],
        ["diagnostics"], ["warningInfo"], ["vehicleWarning"],
    ]

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-vehicle_alerts"
        self._attr_name = "车辆异常警示"
        self._attr_has_entity_name = False
        self._attr_device_info = coordinator.device_info
        self._attr_icon = "mdi:alert"

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data or {}
        attrs: dict = {}
        alerts = data.get("active_alerts")
        if isinstance(alerts, list):
            # v3.0.2: 同 headline 告警去重（保序）
            seen: dict[str, dict] = {}
            for a in alerts:
                if not isinstance(a, dict):
                    continue
                key = a.get("headline")
                if key not in seen:
                    seen[key] = {
                        "headline": a.get("headline"),
                        "severity": a.get("severity"),
                        "body": a.get("body"),
                        "event_time": a.get("eventTime"),
                    }
            attrs["alerts"] = list(seen.values())
            times = [a.get("eventTime") for a in seen.values() if a.get("eventTime")]
            if times:
                attrs["event_time"] = times[0]
            attrs["source"] = "vha"
        elif isinstance(alerts, str):
            attrs["source"] = "vha"
        else:
            status = self.coordinator.data.get("vehiclestatus", {})
            node = _leaf_node(status, "PrmtAlarmEvent")
            if isinstance(node, dict):
                attrs["source"] = "vehicle_status"
                attrs["timestamp"] = node.get("timestamp")
                attrs["source_status"] = node.get("status")
        return attrs

    def _active_titles(self) -> str | None:
        data = self.coordinator.data or {}
        alerts = data.get("active_alerts")
        if alerts:
            if isinstance(alerts, list):
                titles = [
                    a.get("headline")
                    for a in alerts
                    if isinstance(a, dict) and a.get("headline")
                ]
                if titles:
                    # v3.0.2: 去重（保序）后拼接，避免同一条告警重复出现
                    return "、".join(dict.fromkeys(titles))
            elif isinstance(alerts, str) and alerts.strip():
                return alerts
        return None

    def _fallback_value(self):
        status = self.coordinator.data.get("vehiclestatus", {})
        for path in self._FALLBACK_PATHS:
            value = _leaf(status, *path)
            if value is not None:
                return value
        return None

    @property
    def native_value(self):
        titles = self._active_titles()
        if titles:
            return titles
        value = self._fallback_value()
        if value is None:
            return "无异常"
        if isinstance(value, str) and value.strip().lower() in ("null", "none", "n/a", ""):
            return "无异常"
        if isinstance(value, (list, dict)):
            return "无异常"
        if isinstance(value, bool):
            return "车辆异常" if value else "无异常"
        return str(value)


def _ts_to_local(v):
    """Unix 秒时间戳 → 本地时间字符串（0/None → None）。"""
    if not v:
        return None
    try:
        return datetime.datetime.fromtimestamp(int(v)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return None


def _remote_start_time(v):
    """远程启动时间（v3.0.2）：unix 秒 → 本地时间；0/空（未启动）→「未启动」。"""
    if not v:
        return "未启动"
    try:
        return datetime.datetime.fromtimestamp(int(v)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return "未启动"


def _ts_or_str(v):
    """充电开始/结束时间：unix 秒/毫秒时间戳 → 本地时间，字符串原样（v2.9.0）。"""
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        ts = float(v) / 1000 if float(v) > 1e12 else float(v)  # 毫秒/秒自适应
        return _ts_to_local(int(ts))
    return str(v)


class FordPassAutoOffSensor(SensorEntity):
    """距离自动熄火时间（动态倒计时，v2.8.0）。

    远程启动进行中（remoteStartStatus=1）且福特返回了启动时长与启动时刻时，
    按 剩余 = duration(分钟) - (now - startTime) 估算剩余分钟数；未启动或
    数据缺失时返回 0（属性 running=False 可区分）。
    说明：remoteStartDuration 按官方 App「自动熄火时间」语义视为分钟。
    """

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-auto_off_remaining"
        self._attr_name = "距离自动熄火时间"
        self._attr_has_entity_name = False
        self._attr_device_info = coordinator.device_info
        self._attr_icon = "mdi:timer-sand"
        self._attr_native_unit_of_measurement = "分钟"

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def extra_state_attributes(self) -> dict:
        status = self.coordinator.data.get("vehiclestatus", {})
        rs = status.get("remoteStart") or {}
        rss = status.get("remoteStartStatus") or {}
        t0 = rs.get("remoteStartTime")
        attrs: dict = {
            "running": bool(rss.get("value")),
            "duration_minutes": rs.get("remoteStartDuration"),
            "start_time": _ts_to_local(t0),
            "source_status": rss.get("status") or rs.get("status"),
            "timestamp": rss.get("timestamp") or rs.get("timestamp"),
        }
        # v2.7.8: last_poll（最后拉取状态）
        attrs["last_poll"] = (
            f"{self.coordinator.last_poll:%Y-%m-%d %H:%M:%S}"
            if self.coordinator.last_poll
            else None
        )
        return attrs

    @property
    def native_value(self):
        status = self.coordinator.data.get("vehiclestatus", {})
        rs = status.get("remoteStart") or {}
        rss = status.get("remoteStartStatus") or {}
        running = rss.get("value")
        dur = rs.get("remoteStartDuration")
        t0 = rs.get("remoteStartTime")
        if not running or not dur or not t0:
            return 0
        try:
            remaining = float(dur) * 60 - (time.time() - float(t0))
        except (TypeError, ValueError):
            return 0
        if remaining <= 0:
            return 0
        return round(remaining / 60, 1)
