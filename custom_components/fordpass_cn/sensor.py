"""Sensor platform: vehicle status readings."""
from __future__ import annotations

import datetime
import re
import time
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfLength, UnitOfPressure
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import capability
from .const import ANNOUNCE_ENUM_CN, CHIRP_TO_ANNOUNCE, DOMAIN
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
        # v3.3.9: 机油寿命仅保留 prognostic 版（FordPassOilLifeSensor，含归零月份/
        # 剩余公里属性）；原 oil_life（oil.oilLifeActual）重复实体已删除。
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
    # v3.3.9: 车辆信息（jointVenture+localMarketValue+modelYear+vehicleType+fuelType）
    sensors.append(FordPassVehicleInfoSensor(coordinator))
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
                       # v3.3.10: 映射修正——PwPckOffTqNotAvailable（停车扭矩不可用=
                       # 已驻车熄火）=已泊车；PwPckOffTqAvailable/「不可用」=外出中。
                       transform=lambda v: (
                           "已泊车" if isinstance(v, str) and "NotAvailable" in v
                           else "外出中" if isinstance(v, str) and ("Available" in v or v == "不可用")
                           else v
                       )),
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

    # v3.3.9: 柴油实体（领裕/撼路者柴油版等）——仅柴油燃料车型（fuelType=D）
    # 才创建；汽油车即使云端返回 dieselSystemStatus 字段也跳过（锐际曾显示
    # 「柴油系统状态=激活」属误创建）。
    if coordinator.is_diesel:
        sensors += [
            FordPassSensor(coordinator, "diesel_urea", "尿素液位", "%", None, "mdi:water-percent", ["dieselSystemStatus", "exhaustFluidLevel"], skip_if_missing=True),
            FordPassSensor(coordinator, "diesel_urea_range", "尿素续航", UnitOfLength.KILOMETERS, None, "mdi:road-variant", ["dieselSystemStatus", "ureaRange"], skip_if_missing=True),
            FordPassSensor(coordinator, "diesel_metric", "柴油系统状态", None, None, "mdi:engine", ["dieselSystemStatus", "metricType"], enum_map=_diesel_metric_map, skip_if_missing=True),
            FordPassSensor(coordinator, "diesel_filter_soot", "颗粒滤清器积碳", None, None, "mdi:filter", ["dieselSystemStatus", "filterSoot"], skip_if_missing=True),
            FordPassSensor(coordinator, "diesel_filter_regeneration", "滤清器再生状态", None, None, "mdi:autorenew", ["dieselSystemStatus", "filterRegenerationStatus"], enum_map=_diesel_metric_map, skip_if_missing=True),
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
    # v3.4.5: 家充桩管理（smartwallbox 探测数据驱动——非家充桩车型不创建）。
    # 数据源 coordinator.smartwallbox = api.probe_smartwallbox()（登录后一次
    # + 24h TTL 缓存，端点见 const.py smartwallbox 段）。有探测数据才创建，
    # 全车型自动适配（纯油/无家充桩车型探测 404 无数据 → 0 实体）。
    if isinstance(coordinator.smartwallbox, dict) and coordinator.smartwallbox:
        sensors.append(FordPassSmartWallboxSensor(coordinator, "wallbox_binding", "家充桩数量", "count"))
        sensors.append(FordPassSmartWallboxSensor(coordinator, "wallbox_binding", "默认充电桩", "default"))
        sensors.append(FordPassSmartWallboxSensor(coordinator, "wallbox_records", "最近充电记录", "record"))
        sensors.append(FordPassSmartWallboxSensor(coordinator, "wallbox_records", "家充桩充电状态", "status"))
    # v3.4.6: 只读探测实体（OTA 新版本/详情、预约出发、充电日志）——探测
    # 到数据才创建，非能力车型/无数据不创建（全车型适配，0 unavailable）。
    _ep = coordinator.extra_probes
    if isinstance(_ep, dict) and _ep:
        for _k, _lbl, _kind in (
            ("ota_new_status", "OTA 新版本状态", "ota_status"),
            ("ota_versions", "OTA 版本", "ota_version"),
            ("ota_search_details", "OTA 更新详情", "ota_detail"),
            ("ota_detail", "OTA 详情", "ota_detail"),
            ("departuretimes", "预约出发", "departure"),
            ("chargelogs", "充电日志", "chargelog"),
        ):
            if _ep.get(_k):
                sensors.append(FordPassProbeSensor(coordinator, _k, _lbl, _kind))
    # v3.1.7: 空调滤芯状态（AAR 能力车型；无 airFilter 字段的车型不创建，
    # 保证其他用户登录各自车型时不出现不支持的实体）
    air_filter = coordinator.data.get("air_filter")
    if isinstance(air_filter, dict):
        af_payload = air_filter.get("data") if isinstance(air_filter.get("data"), dict) else None
        if isinstance(af_payload, dict) and isinstance(af_payload.get("airFilter"), dict):
            sensors.append(FordPassAirFilterSensor(coordinator))
    # v3.1.9: 云端能力探测（ccfeatures 能力位图；位图有已开通能力才创建——
    # 全车型自动适配：各自位图 → 各自能力集 → 实体自动创建）。
    # v3.2.4: 不再创建「车辆服务信息」传感器（ccfeatures 明细，用户要求删除）。
    ccf = coordinator.data.get("ccfeatures")
    if isinstance(ccf, dict) and capability.parse_cloud_features(ccf):
        sensors.append(FordPassCloudProbeSensor(coordinator))
    # v3.1.13: 鸣笛命令状态（最近一次鸣笛的 announcestatus 结果；未触发时
    # 显示「未触发」，永远可用——v3.1.2 规则）
    sensors.append(FordPassAnnounceStatusSensor(coordinator))
    # v3.1.9: 预测性诊断（prognostic，200 实测）——有业务数据才创建
    prog = coordinator.data.get("prognostic")
    if isinstance(prog, dict):
        prog_payload = prog.get("data") if isinstance(prog.get("data"), dict) else None
        if isinstance(prog_payload, dict):
            sensors.append(FordPassOilLifeSensor(coordinator))
            # v3.3.9: 剩余可行驶里程仅在 remainingKMs 有值时创建
            # （锐际 prognostic 曾返回无该字段 → 实体恒 unknown，不创建）
            if prog_payload.get("remainingKMs") is not None:
                sensors.append(FordPassRangeSensor(coordinator))
            sensors.append(FordPassSlowLeakSensor(coordinator))
            sensors.append(FordPassDiagnosticSensor(coordinator))
    # v3.1.17: 未读消息（GET /api/cnxapi-message/app/messages/summary，
    # HAR 实测 200）——有 data.summary 才创建（无数据车型不创建，
    # 不显示不可用）
    msg = coordinator.data.get("messages")
    if isinstance(msg, dict):
        msg_payload = msg.get("data") if isinstance(msg.get("data"), dict) else None
        if isinstance(msg_payload, dict) and isinstance(msg_payload.get("summary"), dict):
            sensors.append(FordPassMessageSensor(coordinator))
    return sensors


class FordPassVehicleInfoSensor(SensorEntity):
    """车辆信息（v3.3.9）：jointVenture+localMarketValue+modelYear+vehicleType+fuelType。

    数据源 /v5/vehicles/list 明文字段（HAR 2026-10-03 实测）：CAF/锐际
    Escape/2020/SYNC +/G。值=「长安福特 · 锐际 Escape · 2020 · SYNC + · 汽油」，
    各字段同时放入 attributes 供自动化使用。
    """

    _JOINT_VENTURE = {
        "CAF": "长安福特", "JMC": "江铃福特", "LMC": "林肯中国",
        "CHANA": "长安", "FORD": "福特中国", "Ford": "福特中国",
    }
    _FUEL_TYPE = {
        "G": "汽油", "D": "柴油", "E": "纯电", "BEV": "纯电",
        "H": "混动", "HEV": "混动", "P": "插混", "PHEV": "插混", "MHEV": "轻混",
    }

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-vehicle_info"
        self._attr_name = "车辆信息"
        self._attr_has_entity_name = False
        self._attr_device_info = coordinator.device_info
        self._attr_icon = "mdi:car-info"

    @property
    def available(self) -> bool:
        return True  # v3.1.2: 不随福特云刷新失败而不可用（保留最后已知状态）

    @property
    def native_value(self) -> str:
        info = self.coordinator.vehicle_info or {}
        jv = self._JOINT_VENTURE.get(info.get("jointVenture"), info.get("jointVenture"))
        fuel = self._FUEL_TYPE.get(info.get("fuelType"), info.get("fuelType"))
        parts = [jv, info.get("localMarketValue"), info.get("modelYear"),
                 info.get("vehicleType"), fuel]
        return " · ".join(str(p) for p in parts if p) or "暂无"

    @property
    def extra_state_attributes(self) -> dict:
        info = self.coordinator.vehicle_info or {}
        return {k: info.get(k) for k in (
            "jointVenture", "localMarketValue", "modelYear", "vehicleType",
            "fuelType", "warrantyStartDate",
        )}


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
        self._last_known: str | None = None  # v3.4.3: 最后已知地址，LBS 失败时兜底

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
            # v3.4.3: LBS 偶发无数据时保留最后已知地址，不回落"定位不可用"
            return self._last_known or "定位不可用"
        address = loc.get("address")
        if address:
            self._last_known = address
            return address
        lat, lon = loc.get("lat"), loc.get("lon")
        if lat and lon:
            self._last_known = f"{lat}, {lon}"
            return f"{lat}, {lon}"
        return self._last_known or "定位不可用"


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
    """车窗位置（v3.1.8 + v3.3.3）：字符串枚举→中文；数值→百分比。

    锐际实测云端返回字符串枚举（Fully_Closed / BetFully_10PercentOpen /
    BetFully_50PercentOpen 等，无纯数字）；部分车型可能返回数值
    （0=关闭、100=全开）。v3.3.3：新增解析枚举内嵌百分比数字——
    BetFully_10PercentOpen →「未关闭 10%」（与数值型显示格式一致）。
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
        # v3.3.3: BetFully_10PercentOpen / BetFully_50PercentOpen 等枚举
        # 内嵌百分比 →「未关闭 N%」（与数值型显示格式统一）
        m = re.search(r"(\d+)\s*percent", s)
        if m:
            return f"未关闭 {int(m.group(1))}%"
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

class FordPassCloudProbeSensor(SensorEntity):
    """云端能力探测（v3.1.20）——全车型自动创建的关键。

    解析 ccfeatures availableFeatures 位图（VDSFeatureType，逆向福特派
    6.16.0 libapp.so 还原的 24 项枚举）→ 该车开通的云端服务能力集。
    位图不同 → 能力集不同 → 实体自动适配：如锐际纯油
    03,04,05,06,07,08,10,11,23 = 计划保养服务/指南/道路救援/延保/福特金融/
    私充服务/我的订阅/我的试驾 + 未定义特性 0x23。无位图数据的车型不创建。
    """

    _attr_has_entity_name = False
    _attr_icon = "mdi:cloud-search"

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-cloud_probe"
        self._attr_name = "云端能力探测"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        return True  # v3.1.2: 不随福特云刷新失败而不可用（保留最后已知状态）

    @property
    def native_value(self) -> str:
        feats = capability.parse_cloud_features(
            (self.coordinator.data or {}).get("ccfeatures")
        )
        if not feats:
            return "无能力数据"
        named = [f["zh"] for f in feats]
        return f"已开通 {len(feats)} 项：{'、'.join(named)}"

    @property
    def extra_state_attributes(self) -> dict:
        feats = capability.parse_cloud_features(
            (self.coordinator.data or {}).get("ccfeatures")
        )
        attrs: dict[str, Any] = {
            "last_poll": (
                f"{self.coordinator.last_poll:%Y-%m-%d %H:%M:%S}"
                if self.coordinator.last_poll
                else None
            )
        }
        if not feats:
            return attrs
        attrs["capabilities"] = [f"{f['zh']}（{f['en']}）" for f in feats]
        attrs["feature_ids"] = ", ".join(f["raw"] for f in feats)
        for f in feats:
            attrs[f"capability_{f['id']:02x}"] = f"{f['zh']}（{f['en']}）"
        return attrs


class FordPassOilLifeSensor(_ServiceInfoSensor):
    """机油寿命（v3.1.9）：prognostic.data.iolm（%）。

    v3.3.10: 值 = 百分比 + 归零年月份 + 剩余公里（如「40% · 归零 2028-03 ·
    剩余 12000 km」）；各字段仍同步进属性供自动化使用。
    """

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "prognostic", "机油寿命", "mdi:oil")

    def _format(self, data: dict) -> str | None:
        payload = data.get("data") if isinstance(data, dict) else None
        iolm = payload.get("iolm") if isinstance(payload, dict) else None
        if iolm is None:
            return None
        parts = [f"{int(iolm)}%"]
        if payload.get("dateOnZero"):
            parts.append(f"归零 {payload['dateOnZero']}")
        if payload.get("remainingKMs") is not None:
            parts.append(f"剩余 {int(payload['remainingKMs'])} km")
        return " · ".join(parts)

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
        # 此前读 data.data / data.values 两层都错位，state 恒为 unknown）。
        # v3.3.0: 显示完整首档（该里程下所有项目）+ 档数，替代仅首项——
        # HAR（2026-10-03）确认 App 保养计划页展示全部里程档的全部项目。
        payload = data.get("data") if isinstance(data, dict) else None
        items = payload.get("values") if isinstance(payload, dict) else None
        if not isinstance(items, list) or not items:
            return None
        descs: list[str] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            ops = [
                str(o.get("description"))
                for o in (it.get("operations") or [])
                if isinstance(o, dict) and o.get("description")
            ]
            if not ops:
                continue
            mileage = it.get("mileage")
            head = f"{mileage} 公里" if mileage is not None else "保养档"
            descs.append(f"{head}：{'、'.join(ops)}")
        if not descs:
            return None
        first = descs[0]
        if len(items) > 1:
            first += f"（共 {len(items)} 档）"
        # HA state 有 255 字符上限；首档项目多时截断加省略号
        if len(first) > 230:
            first = first[:227] + "…"
        return first

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
        # 服务端业务失败（实测 payload.returnSuccess false / CONS.SYS.0002）：
        # 锐际纯油车型无车联网 SIM 服务，App 无该功能入口（HAR 全量 1152 条
        # 无 sim/info 请求）——v3.3.0 语义修正为「未开通」而非「查询失败」，
        # 错误码保留在 attributes（_summary 一并带出）。
        if payload.get("returnSuccess") is False:
            code = str(payload.get("returnErrCode") or "")
            msg = str(payload.get("returnErrMsg") or "")
            # v3.2.6: 错误码（402/CONS.SYS.0002）与错误描述（CONS.AUTH.0005）
            # 都在业务失败时给出——任一命中即判定为「未开通该服务」；
            # 401/402/403/404 归入同一语义（无权限/未开通/路由不存在）。
            if code.startswith("CONS.") or msg.startswith("CONS.") or code in ("401", "402", "403", "404"):
                return "车辆未开通该服务"
            return f"查询失败（{code or msg or '未知'}）"
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
        for k in ("iccid", "simICCID", "simStatus", "phoneNumber", "carrier", "operator", "status",
                  "returnSuccess", "returnErrCode", "returnErrMsg"):
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
        # v3.3.0: 与 SIM 一致——锐际纯油无车联网热点服务，业务失败（CONS.AUTH.0005
        # 等）语义修正为「未开通」，错误码保留 attributes。
        if payload.get("returnSuccess") is False:
            code = str(payload.get("returnErrCode") or "")
            msg = str(payload.get("returnErrMsg") or "")
            # v3.2.6: 错误码（402/CONS.SYS.0002）与错误描述（CONS.AUTH.0005）
            # 都在业务失败时给出——任一命中即判定为「未开通该服务」；
            # 401/402/403/404 归入同一语义（无权限/未开通/路由不存在）。
            if code.startswith("CONS.") or msg.startswith("CONS.") or code in ("401", "402", "403", "404"):
                return "车辆未开通该服务"
            return f"查询失败（{code or msg or '未知'}）"
        state = payload.get("wifiStatus")
        if state is None or state == "" or state == "Null":
            return None
        return "开启" if str(state).lower() == "on" else str(state)

    def _summary(self, data: dict) -> dict:
        status = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), dict) else {}
        keep = {}
        for k in ("ssid", "wifiStatus", "encryptedVin", "returnSuccess", "returnErrCode", "returnErrMsg"):
            if status.get(k) not in (None, "", "Null"):
                keep[k] = status[k]
        return keep


class FordPassMessageSensor(_ServiceInfoSensor):
    """未读消息（v3.1.17）：GET /api/cnxapi-message/app/messages/summary。

    HAR 实测（2026-10-03）：data.summary.{allRedDotStatus, unReadCategoryId,
    unReadCategoryDescription, readMessageSubject} + data.categories[]。
    allRedDotStatus = 0 无未读 / 1 有未读；categories[].redDotStatus 为各
    分类红点。无 summary 数据的车型不创建（0 unavailable）。
    """

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "messages", "未读消息", "mdi:email-alert")

    def _format(self, data: dict) -> str | None:
        payload = data.get("data") if isinstance(data, dict) else None
        summary = payload.get("summary") if isinstance(payload, dict) else None
        if not isinstance(summary, dict):
            return None
        red = summary.get("allRedDotStatus")
        if red is None:
            return None
        try:
            has = int(red) != 0
        except (TypeError, ValueError):
            has = bool(red)
        # v3.3.3: 有未读时状态直接显示最新消息主题 readMessageSubject
        # （App 首页消息卡显示的标题），无未读显示「无未读消息」。
        if not has:
            return "无未读消息"
        subject = summary.get("readMessageSubject")
        if subject:
            return str(subject)
        return "有未读消息"

    def _summary(self, data: dict) -> dict:
        payload = data.get("data") if isinstance(data, dict) else None
        summary = payload.get("summary") if isinstance(payload, dict) else None
        out: dict[str, Any] = {}
        if isinstance(summary, dict):
            if summary.get("unReadCategoryDescription"):
                out["unread_description"] = str(summary["unReadCategoryDescription"])
            if summary.get("readMessageSubject"):
                out["latest_message"] = str(summary["readMessageSubject"])
            if summary.get("unReadCategoryId"):
                out["unread_category_id"] = str(summary["unReadCategoryId"])
        cats = payload.get("categories") if isinstance(payload, dict) else None
        if isinstance(cats, list):
            unread = [
                c.get("categoryId") for c in cats
                if isinstance(c, dict) and c.get("redDotStatus")
            ]
            if unread:
                out["unread_categories"] = unread
        return out


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
        # v3.2.2: 优先 App 官方槽位 AnnouncementType/Duration（HAR 实证），
        # 旧槽位 vehicleAnnouncementSoundType/vehicleAnnouncementDuration
        # 仅兜底（历史残留、App 新逻辑不读）。
        sound = pref.get("AnnouncementType") or pref.get("vehicleAnnouncementSoundType")
        duration = pref.get("Duration") or pref.get("vehicleAnnouncementDuration")
        if sound is None and duration is None:
            return None
        # 新槽位=枚举数字（0-4）；旧槽位=枚举名（chrip1...panic）——分别映射
        type_cn = next(
            (cn for cn, en in CHIRP_TO_ANNOUNCE.items() if en == str(sound)),
            None,
        )
        if type_cn is None:
            type_cn = ANNOUNCE_ENUM_CN.get(str(sound), str(sound))
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

def _swb_find(node, key: str):
    """递归宽松提取 smartwallbox 响应字段（key 大小写不敏感，v3.4.5）。

    家充桩端点响应结构（binding v5 / records v2r 等）各车型/版本存在差异，
    用递归 + 大小写不敏感匹配提取字段，避免依赖固定 JSON 路径。
    """
    if isinstance(node, dict):
        for k, v in node.items():
            if str(k).lower() == key.lower():
                return v
            r = _swb_find(v, key)
            if r is not None:
                return r
    elif isinstance(node, list):
        for item in node:
            r = _swb_find(item, key)
            if r is not None:
                return r
    return None


def _swb_list(node):
    """递归找第一个充电桩（wallbox/charger）对象列表（v3.4.5）。"""
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, list) and v and all(isinstance(x, dict) for x in v) and any(
                str(kk).lower() in ("wallboxid", "wallbox", "chargerid", "serialnumber",
                                    "serialno", "equipmentid")
                for x in v for kk in x
            ):
                return v
            r = _swb_list(v)
            if r is not None:
                return r
    elif isinstance(node, list):
        for item in node:
            r = _swb_list(item)
            if r is not None:
                return r
    return None


class FordPassSmartWallboxSensor(SensorEntity):
    """家充桩管理（smartwallbox）摘要传感器（v3.4.5）。

    数据源 coordinator.smartwallbox（api.probe_smartwallbox 探测结果，
    登录后一次 + 24h TTL 缓存，见 coordinator.async_probe_capabilities）。
    探测成功才创建（非家充桩车型探测无数据 → 0 实体）；字段宽松提取，
    缺失显示友好中文；永远可用（v3.1.2 规则，不显示 unavailable）。
    """

    _attr_has_entity_name = False
    _attr_icon = "mdi:ev-station"

    def __init__(self, coordinator, key: str, label: str, kind: str) -> None:
        self.coordinator = coordinator
        self._key = key
        self._kind = kind
        self._attr_unique_id = f"{coordinator.vin}-{key}-{kind}"
        self._attr_name = label
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        return True  # v3.1.2: 不随福特云刷新失败而不可用（保留最后已知状态）

    def _probe(self) -> dict | None:
        swb = self.coordinator.smartwallbox or {}
        ent = swb.get(self._key)
        if isinstance(ent, dict):
            data = ent.get("data")
            if isinstance(data, dict):
                return data
        return None

    @property
    def native_value(self) -> str | None:
        data = self._probe()
        if data is None:
            return None
        try:
            if self._kind == "count":
                boxes = _swb_list(data)
                return f"{len(boxes)} 个充电桩" if boxes else "未绑定充电桩"
            if self._kind == "default":
                boxes = _swb_list(data) or []
                if not boxes:
                    return "未绑定充电桩"
                default = next(
                    (b for b in boxes if str(_swb_find(b, "isDefault")).lower() in ("true", "1")
                     or _swb_find(b, "default") is True),
                    boxes[0],
                )
                brand = _swb_find(default, "brandName")
                model = _swb_find(default, "modelName")
                sn = _swb_find(default, "serialNumber") or _swb_find(default, "serialNo")
                mac = _swb_find(default, "macAddress")
                parts = [str(x) for x in (brand, model) if x]
                name = " ".join(parts) if parts else "家充桩"
                if sn:
                    name += f"（SN {sn}）"
                elif mac:
                    name += f"（{mac}）"
                return name
            if self._kind == "record":
                dur = _swb_find(data, "chargeDuration")
                if dur is not None:
                    return f"充电时长 {dur}"
                rec = _swb_find(data, "recordId")
                if rec is not None:
                    return f"记录 {rec}"
                return "暂无充电记录"
            if self._kind == "status":
                st = _swb_find(data, "chargingStatus") or _swb_find(data, "chargerStatus")
                if isinstance(st, str):
                    return {
                        "Charging": "充电中", "NotCharging": "未充电",
                        "FullyCharged": "已充满", "Complete": "已完成",
                        "Charged": "已充满", "Discharging": "放电中",
                        "0": "未充电", "1": "充电中",
                    }.get(st, st)
                return "未知"
        except Exception:  # noqa: BLE001 - 解析失败不抛给 HA
            return "解析失败"
        return None

    @property
    def extra_state_attributes(self) -> dict:
        data = self._probe()
        attrs: dict = {}
        if isinstance(data, dict):
            for k, v in list(data.items())[:12]:
                if isinstance(v, (str, int, float, bool)) or v is None:
                    attrs[str(k)] = v
        attrs["last_poll"] = (
            f"{self.coordinator.last_poll:%Y-%m-%d %H:%M:%S}"
            if self.coordinator.last_poll
            else None
        )
        return attrs

class FordPassProbeSensor(SensorEntity):
    """只读探测端点摘要传感器（v3.4.6，探测数据驱动创建）。

    数据源 coordinator.extra_probes = api.probe_extra_endpoints()（登录后
    一次 + 24h TTL 缓存，全部只读/幂等 GET）：OTA 版本/详情/新状态、
    预约出发（departuretimes/retrieve）、充电日志（chargelogs/retrieve）。
    探测到数据才创建；字段宽松递归提取（_swb_find），缺失显示友好中文；
    永远可用（v3.1.2 规则，不显示 unavailable）。
    """

    _attr_has_entity_name = False
    _attr_icon = "mdi:update"

    def __init__(self, coordinator, key: str, label: str, kind: str) -> None:
        self.coordinator = coordinator
        self._key = key
        self._kind = kind
        self._attr_unique_id = f"{coordinator.vin}-{key}"
        self._attr_name = label
        self._attr_device_info = coordinator.device_info
        if kind in ("departure", "chargelog"):
            self._attr_icon = "mdi:calendar-clock"

    @property
    def available(self) -> bool:
        return True  # v3.1.2: 不随福特云刷新失败而不可用（保留最后已知状态）

    def _data(self) -> dict | None:
        ep = self.coordinator.extra_probes or {}
        data = ep.get(self._key)
        return data if isinstance(data, dict) else None

    @property
    def native_value(self) -> str | None:
        data = self._data()
        if data is None:
            return None
        try:
            if self._kind == "ota_version":
                ver = (_swb_find(data, "version") or _swb_find(data, "otaVersion")
                       or _swb_find(data, "versionNumber") or _swb_find(data, "targetVersion"))
                if ver is not None:
                    return str(ver)
                return "暂无 OTA 版本信息"
            if self._kind == "ota_detail":
                ver = (_swb_find(data, "targetVersion") or _swb_find(data, "otaVersion")
                       or _swb_find(data, "version"))
                desc = _swb_find(data, "description") or _swb_find(data, "content")
                parts = [str(x) for x in (ver, desc) if x]
                return " / ".join(parts) if parts else "暂无 OTA 更新详情"
            if self._kind == "ota_status":
                st = (_swb_find(data, "status") or _swb_find(data, "newOtaStatus")
                      or _swb_find(data, "otaStatus"))
                if isinstance(st, str):
                    return {
                        "Available": "有可用更新", "NotAvailable": "无可用更新",
                        "Downloading": "下载中", "Installing": "安装中",
                        "Installed": "已安装", "Failed": "安装失败",
                    }.get(st, st)
                if st is not None:
                    return str(st)
                return "暂无新 OTA"
            if self._kind == "departure":
                t = (_swb_find(data, "departureTime") or _swb_find(data, "nextDeparture")
                     or _swb_find(data, "departureTimes"))
                if t is not None:
                    return str(t)
                lst = _swb_list(data)
                if lst:
                    return f"{len(lst)} 个预约出发任务"
                return "未设置预约出发"
            if self._kind == "chargelog":
                dur = _swb_find(data, "chargeDuration")
                if dur is not None:
                    return f"充电时长 {dur}"
                t = _swb_find(data, "chargeStartTime") or _swb_find(data, "startTime")
                if t is not None:
                    return f"最近充电 {t}"
                lst = _swb_list(data)
                if lst:
                    return f"{len(lst)} 条充电记录"
                return "暂无充电日志"
        except Exception:  # noqa: BLE001 - 解析失败不抛给 HA
            return "解析失败"
        return None

    @property
    def extra_state_attributes(self) -> dict:
        data = self._data()
        attrs: dict = {}
        if isinstance(data, dict):
            for k, v in list(data.items())[:12]:
                if isinstance(v, (str, int, float, bool)) or v is None:
                    attrs[str(k)] = v
        attrs["last_poll"] = (
            f"{self.coordinator.last_poll:%Y-%m-%d %H:%M:%S}"
            if self.coordinator.last_poll
            else None
        )
        return attrs

