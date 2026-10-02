"""Sensor platform: vehicle status readings."""
from __future__ import annotations

import datetime
import time
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfLength, UnitOfPressure
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CHIRP_TO_ANNOUNCE, DOMAIN
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
    # v3.1.6: 多 VIN——每辆车按各自 vehicle-status 数据独立构建传感器组
    payload = hass.data[DOMAIN][entry.entry_id]
    coordinators = payload.get("coordinators") or [payload["coordinator"]]
    all_sensors: list[SensorEntity] = []
    for coordinator in coordinators:
        all_sensors.extend(_make_sensors(coordinator))
    async_add_entities(all_sensors)


def _make_sensors(coordinator: FordPassCoordinator) -> list[SensorEntity]:
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
    sensors += [
        FordPassSensor(coordinator, "driver_door", "主驾车门", None, None, "mdi:car-door", ["doorStatus", "driverDoor"], enum_map=_door_map),
        FordPassSensor(coordinator, "passenger_door", "副驾车门", None, None, "mdi:car-door", ["doorStatus", "passengerDoor"], enum_map=_door_map),
        FordPassSensor(coordinator, "left_rear_door", "左后车门", None, None, "mdi:car-door", ["doorStatus", "leftRearDoor"], enum_map=_door_map),
        FordPassSensor(coordinator, "right_rear_door", "右后车门", None, None, "mdi:car-door", ["doorStatus", "rightRearDoor"], enum_map=_door_map),
        FordPassSensor(coordinator, "tailgate", "尾门", None, None, "mdi:car-back", ["doorStatus", "tailgateDoor"], enum_map=_door_map),
        FordPassSensor(coordinator, "hood", "引擎盖", None, None, "mdi:car", ["doorStatus", "hoodDoor"], enum_map=_door_map),
        FordPassSensor(coordinator, "ignition", "点火状态", None, None, "mdi:engine", ["ignitionStatus"], enum_map={"Off": "已熄火", "On": "已启动", "Run": "已启动", "Unknown": "未知"}),
        # 车窗（v3.1.8）：字符串枚举→已关闭/未关闭（程度）；数值→未关闭 N%
        FordPassSensor(coordinator, "driver_window", "主驾车窗", None, None, "mdi:window-closed", ["windowPosition", "driverWindowPosition"], transform=_window_state),
        FordPassSensor(coordinator, "passenger_window", "副驾车窗", None, None, "mdi:window-closed", ["windowPosition", "passWindowPosition"], transform=_window_state),
        FordPassSensor(coordinator, "left_rear_window", "左后车窗", None, None, "mdi:window-closed", ["windowPosition", "rearDriverWindowPos"], transform=_window_state),
        FordPassSensor(coordinator, "right_rear_window", "右后车窗", None, None, "mdi:window-closed", ["windowPosition", "rearPassWindowPos"], transform=_window_state),
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
    # v3.1.3: OTA 设置状态（GET /api/alert/v1/ota/setting-info，实测 200；
    # 端点失败或车辆无 OTA 能力时不创建）
    if coordinator.data.get("ota_setting"):
        sensors.append(FordPassOtaSettingSensor(coordinator))
    # v3.1.4: 服务信息（保养计划/召回/SIM/WiFi）——拉取成功才创建
    for key, cls, label in (
        ("maintenance_plan", FordPassMaintenancePlanSensor, "保养计划"),
        ("recall", FordPassRecallSensor, "召回信息"),
        ("sim_info", FordPassSimSensor, "SIM 卡"),
        ("wifi_status", FordPassWifiSensor, "WiFi 热点"),
    ):
        payload = coordinator.data.get(key)
        if payload:
            sensors.append(cls(coordinator))
    # v3.1.4: 鸣笛设置云端状态（查询成功且能读出类型或时长才创建）
    chirp_cloud = coordinator.data.get("chirp_cloud")
    if isinstance(chirp_cloud, dict) and chirp_cloud:
        sensors.append(FordPassChirpCloudSensor(coordinator))
    # v3.1.7: 空调滤芯状态（AAR 能力车型；无 airFilter 字段的车型不创建，
    # 保证其他用户登录各自车型时不出现不支持的实体）
    air_filter = coordinator.data.get("air_filter")
    if isinstance(air_filter, dict):
        af_payload = air_filter.get("data") if isinstance(air_filter.get("data"), dict) else None
        if isinstance(af_payload, dict) and isinstance(af_payload.get("airFilter"), dict):
            sensors.append(FordPassAirFilterSensor(coordinator))
    # v3.1.9: 云端能力+服务信息（ccfeatures，200 实测）——有 featureData 才创建
    ccf = coordinator.data.get("ccfeatures")
    if isinstance(ccf, dict):
        ccf_payload = ccf.get("data") if isinstance(ccf.get("data"), dict) else None
        if isinstance(ccf_payload, dict) and ccf_payload.get("featureData"):
            sensors.append(FordPassCFeaturesSensor(coordinator))
    # v3.1.13: 鸣笛命令状态（最近一次鸣笛的 announcestatus 结果；未触发时
    # 显示「未触发」，永远可用——v3.1.2 规则）
    sensors.append(FordPassAnnounceStatusSensor(coordinator))
    # v3.1.13: 车辆能力清单 v4（cvfeatures；数据可用才创建）
    cvv = coordinator.data.get("capability_v4")
    if isinstance(cvv, dict) and (cvv.get("data") or cvv.get("list")):
        sensors.append(FordPassCapabilityV4Sensor(coordinator))
    # v3.1.9: 预测性诊断（prognostic，200 实测）——有业务数据才创建
    prog = coordinator.data.get("prognostic")
    if isinstance(prog, dict):
        prog_payload = prog.get("data") if isinstance(prog.get("data"), dict) else None
        if isinstance(prog_payload, dict):
            sensors.append(FordPassOilLifeSensor(coordinator))
            sensors.append(FordPassRangeSensor(coordinator))
            sensors.append(FordPassSlowLeakSensor(coordinator))
            sensors.append(FordPassDiagnosticSensor(coordinator))
    return sensors


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
        return True  # v3.1.2: 不随福特云刷新失败而不可用（保留最后已知状态）

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
        return True  # v3.1.2: 不随福特云刷新失败而不可用（保留最后已知状态）

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
        return True  # v3.1.2: 不随福特云刷新失败而不可用（保留最后已知状态）

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
        return True  # v3.1.2: 不随福特云刷新失败而不可用（保留最后已知状态）

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


def _window_state(v):
    """车窗位置（v3.1.8）：字符串枚举→中文；数值（0-100 百分比）→百分比。

    锐际实测云端返回字符串（Fully_Closed 等，无百分比数字）；部分车型
    可能返回数值（0=关闭、100=全开）——数值时显示「未关闭 N%」。
    """
    if isinstance(v, str):
        s = v.strip().lower().replace("_", " ")
        if s in ("fully closed", "closed", "close"):
            return "已关闭"
        if s in ("fully open", "fully opened", "open"):
            return "未关闭（全开）"
        if s in ("partially open", "partially opened", "partial open", "vent"):
            return "未关闭（部分开启）"
        if s in ("unknown", "not supported", "not_supported", "null"):
            return None
        return f"未关闭（{v}）"
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        pct = int(v)
        if pct <= 0:
            return "已关闭"
        return f"未关闭 {pct}%"
    return None


def _remote_start_time(v):
    """远程启动时间（v3.1.8）：unix 秒/毫秒自适应 → 本地时间；0/空 →「未启动」。

    此前按 unix 秒解析——若车机返回毫秒时间戳（13 位）会解析失败退化为
    「未启动」（用户实测"时间不对"的根因）。现在毫秒/秒自适应。
    """
    if not v:
        return "未启动"
    try:
        fv = float(v)
        ts = fv / 1000 if fv > 1e12 else fv  # 毫秒/秒自适应
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
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
        return True  # v3.1.2: 不随福特云刷新失败而不可用（保留最后已知状态）

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


class FordPassOtaSettingSensor(SensorEntity):
    """OTA 设置状态（v3.1.3）——来自 GET /api/alert/v1/ota/setting-info。

    实测（2026-10-02）：该端点走标准 R3 签名返回 HTTP 200，展示车辆的
    OTA 配置（远程 OTA 开关、激活排程、当前/目标版本、状态描述）。
    """

    _OTA_FLAG_MAP = {0: "未开通远程OTA", 1: "已开通", 2: "未知", None: "未知"}

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-ota_setting"
        self._attr_name = "OTA 设置状态"
        self._attr_has_entity_name = False
        self._attr_device_info = coordinator.device_info
        self._attr_icon = "mdi:update"

    @property
    def available(self) -> bool:
        return True  # v3.1.2: 不随福特云刷新失败而不可用（保留最后已知状态）

    @property
    def extra_state_attributes(self) -> dict:
        ota = (self.coordinator.data or {}).get("ota_setting") or {}
        attrs = {
            "remote_ota": ota.get("remoteOTAFlag"),
            "asu_state": ota.get("asuState") or "",
            "activation_day": ota.get("activationDayOfWeek") or "",
            "activation_time": ota.get("activationScheduleTime") or "",
            "to_be_version": ota.get("toBeVersion") or "",
            "to_be_release_note": ota.get("toBeReleaseNote") or "",
            "as_is_version": ota.get("asIsVersion") or "",
            "as_is_release_note": ota.get("asIsReleaseNote") or "",
            "status_name": ota.get("statusName") or "",
            "status_description": ota.get("statusDescription") or "",
        }
        attrs["last_poll"] = (
            f"{self.coordinator.last_poll:%Y-%m-%d %H:%M:%S}"
            if self.coordinator.last_poll
            else None
        )
        return attrs

    @property
    def native_value(self):
        ota = (self.coordinator.data or {}).get("ota_setting") or {}
        flag = ota.get("remoteOTAFlag")
        return self._OTA_FLAG_MAP.get(flag, "未知")


class _ServiceInfoSensor(SensorEntity):
    """服务信息实体基类（v3.1.4）：从 coordinator.data[key] 读已拉取数据。

    端点 2026-10-02 实测 200（R3 compute_sign + 修正 headers + 无 appKey）。
    """

    _attr_has_entity_name = False

    def __init__(self, coordinator, key: str, label: str, icon: str, uid: str | None = None) -> None:
        self.coordinator = coordinator
        self._data_key = key
        self._attr_unique_id = f"{coordinator.vin}-{uid or key}"
        self._attr_name = label
        self._attr_device_info = coordinator.device_info
        self._attr_icon = icon

    @property
    def available(self) -> bool:
        return True  # v3.1.2: 不随福特云刷新失败而不可用（保留最后已知状态）

    @property
    def native_value(self):
        data = (self.coordinator.data or {}).get(self._data_key) or {}
        return self._format(data)

    def _format(self, data: dict) -> str | None:  # pragma: no cover - override
        return None

    @property
    def extra_state_attributes(self) -> dict:
        """精简属性（v3.1.5）：只保留摘要级字段，避免 recorder 16384 字节
        上限告警（保养计划原始响应可超 16KB，会整段拒绝存储）。"""
        data = (self.coordinator.data or {}).get(self._data_key) or {}
        attrs: dict[str, Any] = {"last_poll": (
            f"{self.coordinator.last_poll:%Y-%m-%d %H:%M:%S}"
            if self.coordinator.last_poll
            else None
        )}
        if not isinstance(data, dict):
            return attrs
        # 服务端业务状态（returnSuccess/returnErrCode 在 data.data 内，实测层级）
        payload = data.get("data") if isinstance(data.get("data"), dict) else {}
        if payload.get("returnSuccess") is not None:
            attrs["service_ok"] = bool(payload.get("returnSuccess"))
            if payload.get("returnErrMsg"):
                attrs["service_error"] = str(payload["returnErrMsg"])
        # 业务数据：由各传感器提供摘要
        summary = self._summary(data)
        if summary:
            attrs.update(summary)
        return attrs

    def _summary(self, data: dict) -> dict:  # pragma: no cover - override
        """返回需要展示在 attributes 的摘要级字段（防超限）。"""
        return {}


class FordPassAirFilterSensor(_ServiceInfoSensor):
    """空调滤芯状态（v3.1.7）：GET /api/cnxapi-vds/v1/aar/status。

    实测响应（2026-10-02）data.airFilter.{isHealthy, lastReplaceTime,
    lastReplaceTimestamp}——isHealthy=false 表示滤芯需更换；lastReplaceTime
    为上次重置（更换）日期，重置成功后更新为当天。
    """

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "air_filter", "空调滤芯", "mdi:air-filter")

    def _format(self, data: dict) -> str | None:
        payload = data.get("data") if isinstance(data, dict) else None
        air = payload.get("airFilter") if isinstance(payload, dict) else None
        if not isinstance(air, dict):
            return None
        healthy = air.get("isHealthy")
        if healthy is None:
            return None
        if str(healthy).lower() in ("true", "1", "yes", "on"):
            return "正常"
        return "需更换"

    def _summary(self, data: dict) -> dict:
        payload = data.get("data") if isinstance(data, dict) else None
        air = payload.get("airFilter") if isinstance(payload, dict) else None
        if not isinstance(air, dict):
            return {}
        out: dict[str, Any] = {}
        if air.get("lastReplaceTime"):
            out["last_replace"] = str(air["lastReplaceTime"])
        if air.get("lastReplaceTimestamp") is not None:
            out["last_replace_timestamp"] = air["lastReplaceTimestamp"]
        return out


# ---------------------------------------------------------------------------
# v3.1.9: 云端能力+服务信息（ccfeatures）与预测性诊断（prognostic）
# 实测（2026-10-02，锐际）均为 HTTP 200；无数据车型不创建实体。
# ---------------------------------------------------------------------------
class FordPassCFeaturesSensor(_ServiceInfoSensor):
    """云端能力+服务信息（v3.1.9）：GET /api/cnxapi-vds/v2/vehicles/ccfeatures。

    data.featureData.availableFeatures 为云端能力位图（两位 hex 特性 ID 集合，
    v3.1.12 起按 VDSFeatureType 枚举（逆向福特派 6.16.0 libapp.so 的
    Object Pool 还原）逐项解码：ID → App 枚举成员名 → 中文含义；
    authedFeatures 为已授权特性（单个 hex，如 A4 = 0xA4 = 164）；
    rsa/contact/afterSalesNumber 为道路救援/客服/售后电话；
    videoManualUrl 为车型电子说明书 H5 地址。
    """

    # VDSFeatureType 枚举（App: mobile_cn_data_adapter vds_type.dart）——
    # 逆向自福特派 6.16.0 libapp.so 对象池（pp.txt）：off_8=枚举值, off_10=成员名。
    # vdsFeatureList 在 App 中即为该全集（24 项），availableFeatures 按枚举值过滤。
    VDS_FEATURE_NAMES: dict[int, tuple[str, str]] = {
        0x0: ("osb", "在线服务"),
        0x1: ("maintenanceSchedule", "保养计划"),
        0x2: ("serviceHistory", "服务记录"),
        0x3: ("scheduledServicePlan", "计划保养服务"),
        0x4: ("guides", "指南"),
        0x5: ("rsa", "道路救援"),
        0x6: ("extendedWarranty", "延保"),
        0x7: ("fordCredit", "福特金融"),
        0x8: ("privateChargingService", "私充服务"),
        0x9: ("eCard", "电子卡"),
        0xA: ("WallBoxAutoAuth", "家充桩自动认证"),
        0xB: ("customerFeedback", "客户反馈"),
        0xC: ("carGuide", "用车指南"),
        0xD: ("personalizedPicture", "个性化照片"),
        0xE: ("rccAuto", "RCC 自动"),
        0xF: ("InteSubscription", "国际订阅"),
        0x10: ("MySubscription", "我的订阅"),
        0x11: ("MyTestDrive", "我的试驾"),
        0x12: ("MyOrder", "我的订单"),
        0x13: ("ReservationInquiry", "预约查询"),
        0x14: ("MaintenanceWorkOrder", "保养工单"),
        0x15: ("CarPickupDeliveryInquiry", "取送车查询"),
        0x16: ("MyRights", "我的权益"),
        0x17: ("SyncToCarNavigation", "同步到车机导航"),
    }
    # 枚举上限 = 0x17（23）；超过该范围的特性 ID（如锐际的 0x23=35）
    # 在 App 中无对应枚举成员，checkVDSFeature 会直接忽略。

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "ccfeatures", "车辆服务信息", "mdi:car-info")

    @staticmethod
    def _features_list(fd: dict) -> list[int]:
        raw = fd.get("availableFeatures")
        if not isinstance(raw, str) or not raw.strip():
            return []
        out = []
        for part in raw.split(","):
            p = part.strip()
            if not p:
                continue
            try:
                out.append(int(p, 16))
            except ValueError:
                out.append(int(p, 10) if p.isdigit() else -1)
        return out

    def _feature_label(self, i: int) -> str:
        if i in self.VDS_FEATURE_NAMES:
            en, zh = self.VDS_FEATURE_NAMES[i]
            return f"{en}（{zh}）"
        return "未定义特性 ID（App 枚举越界，忽略）"

    def _format(self, data: dict) -> str | None:
        payload = data.get("data") if isinstance(data, dict) else None
        fd = payload.get("featureData") if isinstance(payload, dict) else None
        if not isinstance(fd, dict):
            return None
        feats = fd.get("availableFeatures")
        if not feats:
            return "无能力数据"
        # 文本保留云端原文（两位 hex），名称见属性
        return str(feats)

    def _summary(self, data: dict) -> dict:
        payload = data.get("data") if isinstance(data, dict) else None
        if not isinstance(payload, dict):
            return {}
        out: dict[str, Any] = {}
        fd = payload.get("featureData")
        if isinstance(fd, dict):
            ids = self._features_list(fd)
            # v3.1.12: 位图逐项解码（feature_03 = scheduledServicePlan（计划保养服务））
            for i in sorted(ids):
                out[f"feature_{i:02x}"] = f"特性 ID 0x{i:02X}（{i}）= {self._feature_label(i)}"
            out["feature_count"] = len(ids)
            named = [self._feature_label(i) for i in sorted(ids) if i in self.VDS_FEATURE_NAMES]
            if named:
                out["features"] = "、".join(named)
            # 位图二进制视图（bit0..bitN，1 = 该特性可用）——多车型对比用
            if ids:
                maxbit = max(ids)
                bits = ["0"] * (maxbit + 1)
                for i in ids:
                    if 0 <= i <= maxbit:
                        bits[i] = "1"
                out["feature_bits"] = "".join(reversed(bits))  # MSB 在前
            if fd.get("authedFeatures"):
                a = str(fd["authedFeatures"])
                try:
                    av = int(a, 16)
                    # v3.1.13: 双解释——整体值 + 逐位拆解（bitN 对应
                    # VDSFeatureType 特性 ID；如 A4=0xA4=164=10100100₂ 拆位
                    # bit2(4)=scheduledServicePlan 计划保养服务 / bit5(32)=rsa
                    # 道路救援 / bit7(128)=fordCredit 福特金融——推测，待多
                    # 车型数据验证；App dump 中仅见 "A4""A5" 相邻字符串）
                    bits = [i for i in range(16) if av & (1 << i)]
                    if bits:
                        bit_desc = "、".join(
                            f"bit{i}=0x{i:X} {self._feature_label(i)}" for i in bits
                        )
                        out["authed_features"] = (
                            f"{a}（0x{a} = {av}；整体值=已授权特性位掩码；"
                            f"拆位：{bit_desc}；具体位义待多车型数据验证）"
                        )
                    else:
                        out["authed_features"] = f"{a}（0x{a} = {av}，无已知位）"
                except ValueError:
                    out["authed_features"] = a
            if fd.get("specifiedFeatures") is not None:
                out["specified_features"] = fd["specifiedFeatures"]
        for key, label in (
            ("rsaNumber", "roadside_assist"),
            ("contactNumber", "customer_service"),
            ("afterSalesNumber", "after_sales"),
        ):
            if payload.get(key):
                out[label] = str(payload[key])
        if payload.get("videoManualUrl"):
            out["manual_url"] = str(payload["videoManualUrl"])
        return out


class FordPassOilLifeSensor(_ServiceInfoSensor):
    """机油寿命（v3.1.9）：prognostic.data.iolm（%）。"""

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "prognostic", "机油寿命", "mdi:oil")

    def _format(self, data: dict) -> str | None:
        payload = data.get("data") if isinstance(data, dict) else None
        iolm = payload.get("iolm") if isinstance(payload, dict) else None
        if iolm is None:
            return None
        return f"{int(iolm)}%"

    def _summary(self, data: dict) -> dict:
        payload = data.get("data") if isinstance(data, dict) else None
        if not isinstance(payload, dict):
            return {}
        out: dict[str, Any] = {}
        if payload.get("dateOnZero"):
            out["zero_on"] = str(payload["dateOnZero"])  # 寿命归零（需保养）月份
        if payload.get("remainingKMs") is not None:
            out["remaining_km"] = payload["remainingKMs"]
        return out


class FordPassRangeSensor(_ServiceInfoSensor):
    """剩余可行驶里程（v3.1.9）：prognostic.data.remainingKMs（km）。"""

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "prognostic", "剩余可行驶里程", "mdi:road-variant", uid="prognostic_range")

    def _format(self, data: dict) -> str | None:
        payload = data.get("data") if isinstance(data, dict) else None
        km = payload.get("remainingKMs") if isinstance(payload, dict) else None
        if km is None:
            return None
        return f"{int(km)} km"

    def _summary(self, data: dict) -> dict:
        payload = data.get("data") if isinstance(data, dict) else None
        if not isinstance(payload, dict):
            return {}
        out: dict[str, Any] = {}
        if payload.get("iolm") is not None:
            out["oil_life"] = f"{int(payload['iolm'])}%"
        if payload.get("dateOnZero"):
            out["zero_on"] = str(payload["dateOnZero"])
        return out


class FordPassSlowLeakSensor(_ServiceInfoSensor):
    """慢漏气胎（v3.1.9）：prognostic.data.tiresWithSlowLeak（null=无）。"""

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "prognostic", "慢漏气胎", "mdi:tire", uid="prognostic_slowleak")

    def _format(self, data: dict) -> str | None:
        payload = data.get("data") if isinstance(data, dict) else None
        leak = payload.get("tiresWithSlowLeak") if isinstance(payload, dict) else None
        if leak is None:
            return "无"
        if isinstance(leak, (list, str)) and len(str(leak)) == 0:
            return "无"
        return str(leak)

    def _summary(self, data: dict) -> dict:
        payload = data.get("data") if isinstance(data, dict) else None
        if not isinstance(payload, dict):
            return {}
        out: dict[str, Any] = {}
        if payload.get("eventTime"):
            out["event_time"] = str(payload["eventTime"])
        if payload.get("remainingKMs") is not None:
            out["remaining_km"] = payload["remainingKMs"]
        return out


class FordPassDiagnosticSensor(_ServiceInfoSensor):
    """预测性诊断提示（v3.1.9）：prognostic 的 urgency/messageDesc/messageCode。"""

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "prognostic", "预测性诊断", "mdi:alert-decagram", uid="prognostic_msg")

    def _format(self, data: dict) -> str | None:
        payload = data.get("data") if isinstance(data, dict) else None
        if not isinstance(payload, dict):
            return None
        desc = payload.get("messageDesc")
        if desc:
            return str(desc)
        urgency = payload.get("urgency")
        if urgency in (None, "", "N"):
            return "无异常"
        return f"提示（{urgency}）"

    def _summary(self, data: dict) -> dict:
        payload = data.get("data") if isinstance(data, dict) else None
        if not isinstance(payload, dict):
            return {}
        out: dict[str, Any] = {}
        if payload.get("featureType"):
            out["feature_type"] = str(payload["featureType"])  # OL=机油寿命
        if payload.get("urgency"):
            out["urgency"] = str(payload["urgency"])
        if payload.get("messageCode"):
            out["message_code"] = payload["messageCode"]
        if payload.get("eventTime"):
            out["event_time"] = str(payload["eventTime"])
        return out


class FordPassMaintenancePlanSensor(_ServiceInfoSensor):
    """保养计划（v3.1.4）：GET /api/cnxapi-cds/v1/maintenance-plan。"""

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "maintenance_plan", "保养计划", "mdi:calendar-check")

    def _format(self, data: dict) -> str | None:
        # 实测响应 data.data.values[].{mileage, operations[]}（v3.1.5 修正：
        # 此前读 data.data / data.values 两层都错位，state 恒为 unknown）
        payload = data.get("data") if isinstance(data, dict) else None
        items = payload.get("values") if isinstance(payload, dict) else None
        if not isinstance(items, list) or not items:
            return None
        first = items[0]
        if not isinstance(first, dict):
            return str(items)
        mileage = first.get("mileage")
        ops = first.get("operations") or []
        label = "保养计划"
        if ops and isinstance(ops[0], dict):
            label = str(ops[0].get("description") or label)
        if mileage is not None:
            return f"{label}（{mileage} 公里）"
        return label

    def _summary(self, data: dict) -> dict:
        payload = data.get("data") if isinstance(data, dict) else None
        items = payload.get("values") if isinstance(payload, dict) else None
        if not isinstance(items, list) or not items:
            return {}
        plans = []
        for it in items[:6]:  # 只保留前 6 个保养节点，防 attributes 超限
            if not isinstance(it, dict):
                continue
            ops = it.get("operations") or []
            ops_desc = [str(o.get("description")) for o in ops if isinstance(o, dict) and o.get("description")]
            plans.append({
                "mileage": it.get("mileage"),
                "items": ops_desc,
            })
        return {"plans": plans}


class FordPassRecallSensor(_ServiceInfoSensor):
    """召回信息（v3.1.4）：GET /api/cnxapi-vds/v1/vehicles/recall。"""

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "recall", "召回信息", "mdi:alert-box-outline")

    def _format(self, data: dict) -> str | None:
        items = data.get("data") if isinstance(data, dict) else None
        if isinstance(items, list) and items:
            return f"{len(items)} 条召回"
        if isinstance(data, dict) and data.get("data") == []:
            return "无召回"
        return None

    def _summary(self, data: dict) -> dict:
        items = data.get("data") if isinstance(data, dict) else None
        if isinstance(items, list) and items:
            rows = []
            for it in items[:5]:
                if isinstance(it, dict):
                    rows.append({
                        "title": it.get("title") or it.get("name") or it.get("campaignName"),
                        "date": it.get("date") or it.get("startDate") or it.get("issueDate"),
                        "status": it.get("status") or it.get("repairStatus"),
                    })
            return {"recalls": rows}
        return {}


class FordPassSimSensor(_ServiceInfoSensor):
    """SIM 卡信息（v3.1.4）：GET /api/cnxapi-cds/v1/vehicle/sim/info。"""

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "sim_info", "SIM 卡", "mdi:sim")

    def _format(self, data: dict) -> str | None:
        if not isinstance(data, dict):
            return None
        payload = data.get("data") if isinstance(data.get("data"), dict) else {}
        # 服务端业务失败（实测 payload.returnSuccess false / CONS.SYS.0002）：如实显示
        if payload.get("returnSuccess") is False:
            return f"查询失败（{payload.get('returnErrMsg') or payload.get('returnErrCode') or '未知'}）"
        info = payload
        iccid = info.get("iccid") or info.get("simICCID")
        if iccid:
            return str(iccid)
        if info.get("simStatus") is not None:
            return "已开通" if str(info["simStatus"]).lower() in ("on", "active", "1", "true") else str(info["simStatus"])
        return None

    def _summary(self, data: dict) -> dict:
        info = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), dict) else {}
        keep = {}
        for k in ("iccid", "simICCID", "simStatus", "phoneNumber", "carrier", "operator", "status"):
            if info.get(k) is not None:
                keep[k] = info[k]
        return keep


class FordPassWifiSensor(_ServiceInfoSensor):
    """WiFi 热点状态（v3.1.4）：GET /api/cnxapi-cds/v1/vehicle/wifi/status。"""

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "wifi_status", "WiFi 热点", "mdi:wifi")

    def _format(self, data: dict) -> str | None:
        if not isinstance(data, dict):
            return None
        payload = data.get("data") if isinstance(data.get("data"), dict) else {}
        if payload.get("returnSuccess") is False:
            return f"查询失败（{payload.get('returnErrMsg') or payload.get('returnErrCode') or '未知'}）"
        state = payload.get("wifiStatus")
        if state is None or state == "" or state == "Null":
            return None
        return "开启" if str(state).lower() == "on" else str(state)

    def _summary(self, data: dict) -> dict:
        status = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), dict) else {}
        keep = {}
        for k in ("ssid", "wifiStatus", "encryptedVin"):
            if status.get(k) not in (None, "", "Null"):
                keep[k] = status[k]
        return keep


class FordPassChirpCloudSensor(SensorEntity):
    """鸣笛设置云端状态（v3.1.4）：福特账户云端 VehicleAnnouncementSetting。

    从 GET preference-list 回读（coordinator.data["chirp_cloud"]），显示
    「类型 + 时长」；与本地 select 不同步时提示（App/其他设备改过）。
    """

    _attr_has_entity_name = False
    _attr_icon = "mdi:cloud-check"

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-chirp_cloud"
        self._attr_name = "鸣笛设置云端状态"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        return True  # v3.1.2: 不随福特云刷新失败而不可用（保留最后已知状态）

    @property
    def native_value(self) -> str | None:
        pref = (self.coordinator.data or {}).get("chirp_cloud") or {}
        if not isinstance(pref, dict):
            return None
        sound = pref.get("vehicleAnnouncementSoundType") or pref.get("AnnouncementType")
        duration = pref.get("vehicleAnnouncementDuration") or pref.get("Duration")
        if sound is None and duration is None:
            return None
        type_cn = next(
            (cn for cn, en in CHIRP_TO_ANNOUNCE.items() if en == str(sound)),
            str(sound),
        )
        return f"{type_cn} / {duration} 秒"

    @property
    def extra_state_attributes(self) -> dict:
        pref = (self.coordinator.data or {}).get("chirp_cloud") or {}
        attrs = dict(pref) if isinstance(pref, dict) else {}
        attrs["last_poll"] = (
            f"{self.coordinator.last_poll:%Y-%m-%d %H:%M:%S}"
            if self.coordinator.last_poll
            else None
        )
        return attrs


class FordPassAnnounceStatusSensor(SensorEntity):
    """鸣笛命令状态（v3.1.13）：最近一次鸣笛的 announcestatus 结果。

    鸣笛开关触发后轮询 GET /api/vehicles/v5/{vin}/announcestatus/{commandId}/
    （App FordRemoteControlApiService v5 命令轮询组）写入
    coordinator.announce_status。未触发/查询失败均显示明确中文状态，
    永远可用（v3.1.2 规则，不显示 unavailable）。
    """

    _attr_has_entity_name = False
    _attr_icon = "mdi:bullhorn-outline"

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-announce_status"
        self._attr_name = "鸣笛命令状态"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        return True  # v3.1.2: 不随福特云刷新失败而不可用（保留最后已知状态）

    @property
    def native_value(self) -> str:
        st = getattr(self.coordinator, "announce_status", None)
        if not st:
            return "未触发"
        if st.get("status") == "执行中":
            return "鸣笛命令执行中"
        if st.get("status") == "查询超时":
            return "查询失败（可能已执行完成）"
        # 网关返回的命令状态字段（如 completed / processing / failed）
        s = st.get("status") or st.get("commandStatus") or "已发送"
        return str(s)

    @property
    def extra_state_attributes(self) -> dict:
        st = getattr(self.coordinator, "announce_status", None) or {}
        attrs = dict(st) if isinstance(st, dict) else {}
        attrs["last_poll"] = (
            f"{self.coordinator.last_poll:%Y-%m-%d %H:%M:%S}"
            if self.coordinator.last_poll
            else None
        )
        return attrs


class FordPassCapabilityV4Sensor(SensorEntity):
    """车辆能力清单 v4（v3.1.13）：GET /api/cnxapi-vds/v4/vehicles/cvfeatures。

    App VcsRepositoryProvider::fetchCapabilityV4——首页能力卡片（PAAK /
    EV 管理 / 车辆状态 / VA / WIFI / RCC / 哨兵 / 灯光寻车等）的权威来源。
    数据可用才创建（无数据车型不创建、不显示 unavailable）；响应结构以
    车型实测为准，能力名列表存放于 extra attributes。
    """

    _attr_has_entity_name = False
    _attr_icon = "mdi:car-info"

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-capability_v4"
        self._attr_name = "车辆能力清单"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        return True  # v3.1.2: 不随福特云刷新失败而不可用（保留最后已知状态）

    @property
    def native_value(self) -> str:
        data = (self.coordinator.data or {}).get("capability_v4") or {}
        if not isinstance(data, dict):
            return "无数据"
        payload = data.get("data")
        if isinstance(payload, dict) and payload.get("capabilityList"):
            items = payload["capabilityList"]
            if isinstance(items, list) and items:
                names = [
                    str(i.get("capabilityName") or i.get("name") or i.get("type"))
                    for i in items
                    if isinstance(i, dict)
                ]
                if names:
                    return "、".join(n for n in names if n)
        return "能力清单已获取（结构待车型实测解析）"

    @property
    def extra_state_attributes(self) -> dict:
        data = (self.coordinator.data or {}).get("capability_v4") or {}
        attrs = dict(data) if isinstance(data, dict) else {}
        attrs["last_poll"] = (
            f"{self.coordinator.last_poll:%Y-%m-%d %H:%M:%S}"
            if self.coordinator.last_poll
            else None
        )
        return attrs
