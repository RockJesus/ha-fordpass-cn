"""Sensor platform: vehicle status readings."""
from __future__ import annotations

import datetime
import json
import logging
import re
import time

_LOGGER = logging.getLogger(__name__)
from typing import Any, ClassVar

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfLength, UnitOfPressure, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from . import capability
from .const import ANNOUNCE_ENUM_CN, CHIRP_TO_ANNOUNCE, DOMAIN
from .coordinator import FordPassCoordinator

def async_sync_entity_categories(hass: HomeAssistant, domain: str, entities) -> None:
    """v5.9.3: 把代码声明的 entity_category 同步到已存在的注册表条目。

    Home Assistant 只在实体首次注册时采用集成给出的类目，之后不会自动覆盖
    已有值（避免盖掉用户在界面里手动切换），因此升级后必须显式补一次。
    策略是「只补不覆盖」：仅当注册表里为空、且代码确实声明了类目时写入。
    """
    registry = er.async_get(hass)
    updated = 0
    for entity in entities:
        unique_id = getattr(entity, "unique_id", None)
        category = getattr(entity, "entity_category", None)
        if not unique_id or not category:
            continue
        entity_id = registry.async_get_entity_id(domain, DOMAIN, unique_id)
        if not entity_id:
            continue
        reg_entry = registry.async_get(entity_id)
        if reg_entry is None or reg_entry.entity_category is not None:
            continue
        registry.async_update_entity(entity_id, entity_category=category)
        updated += 1
    if updated:
        _LOGGER.info("已同步 %s 个实体的类目（entity_category）", updated)

PARALLEL_UPDATES = 1

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
    return not isinstance(val, (list, dict))


# v5.2.4: 敏感个人标识字段——实体 state/attributes 会进 recorder/logbook，
# 手机号/用户 ID 等必须掩码或剔除（指出：
# 车辆授权列表/家充桩分享/SIM 等暴露完整手机号）。
_PHONE_KEYS = ("phone", "phonenumber", "mobile", "mobileno", "mobilephone")
_IDENTITY_KEYS = ("userid", "user_id", "uid", "account", "accountid",
                  "loginname", "username", "idcard", "idno", "passport",
                  "openid", "unionid", "email", "identityno")


def _mask_phone(value) -> str:
    """手机号掩码——只保留后四位。"""
    s = str(value)
    digits = re.sub(r"\D", "", s)
    if len(digits) >= 4:
        return f"****{digits[-4:]}"
    return "****"


def _safe_identity_name(user: dict, finder) -> str | None:
    """授权/分享用户显示名：优先昵称/用户名，手机号仅掩码展示，用户 ID 不外泄。

    供 auth_list / wallbox_sharing 使用——要求实体
    state 不得暴露完整手机号与用户 ID。
    """
    for key in ("userName", "nickName", "nickname", "displayName", "name"):
        nm = finder(user, key)
        if nm is not None:
            return str(nm)
    phone = finder(user, "phone") or finder(user, "mobile") or finder(user, "phoneNumber")
    if phone is not None:
        return _mask_phone(phone)
    return None


def _safe_scalar_attrs(data: dict, limit: int = 12) -> dict:
    """复制原始响应标量字段到 attributes 时的安全过滤器。

    v5.2.4: 剔除/掩码敏感字段——手机号、用户 ID、账号、证件号等个人标识
    不得进入实体 attributes（会进 recorder/logbook）。
    其余标量字段原样保留（供诊断）。
    """
    out: dict[str, object] = {}
    for k, v in list(data.items())[:limit]:
        if not isinstance(v, (str, int, float, bool)) and v is not None:
            continue
        kl = str(k).lower()
        if kl in _PHONE_KEYS or kl in _IDENTITY_KEYS:
            continue
        if any(ident in kl for ident in ("userid", "user_id", "phonenumber",
                                         "mobile", "idcard", "loginname", "accountid")):
            continue
        if kl.endswith(("phone", "mobile", "phonenumber")):
            out[str(k)] = _mask_phone(v) if isinstance(v, str) else v
            continue
        out[str(k)] = v
    return out


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    # v3.1.6: 多 VIN——每辆车按各自 vehicle-status 数据独立构建传感器组
    payload = entry.runtime_data  # v5.3.0: runtime_data 替代 hass.data
    coordinators = payload.get("coordinators") or [payload["coordinator"]]
    all_sensors: list[SensorEntity] = []
    for coordinator in coordinators:
        all_sensors.extend(_make_sensors(coordinator))
    async_add_entities(all_sensors)
    # v4.8.5: 实体订阅协调器刷新广播——每次刷新成功（即使车况未变）
    # async_write_ha_state；失败轮不触发，保留旧值。v5.9.8 起属性不再
    # 含每轮变化的 last_poll，值未变的实体不会产生 state_changed。
    try:

        def _mk_refresh(ent):
            def _refresh():
                # v4.8.6: hass 注入前（实体添加阶段）回调会被预热刷新触发，
                # 直接 async_write_ha_state 会 RuntimeError: hass is None
                if ent.hass is not None:
                    ent.async_write_ha_state()
            return _refresh

        for _ent in all_sensors:
            _ent.async_on_remove(
                _ent.coordinator.async_add_listener(_mk_refresh(_ent))
            )
    except Exception:
        _LOGGER.debug("entity listener registration failed", exc_info=True)


    # v3.5.8: 清理升级残留的旧 sensor 实体——新版不再创建（如双后轮 value=0
    # 非该能力车型）但旧 registry 条目仍在时会一直 unavailable，违反
    # "实体不显示不可用"要求。凡 platform==fordpass_cn 的 sensor 且 unique_id
    # 不在新实体集合、且未被用户手动禁用的，显式移除。
    # v5.2.4: 清理范围限定为【本 entry 自己的实体】——此前按全 domain
    # 过滤会误删其它账号（多 config entry）的传感器，用户的重命名/区域/
    # 自定义随之丢失。config flow
    # 支持多账号（一个 entry 对应一个手机号），设置一个 entry 不得删别的。
    reg = er.async_get(hass)
    new_ids = {s.unique_id for s in all_sensors}
    # v3.6.5: HAOS 2026.10 移除 EntityRegistry.async_entries_for_domain，
    # 改用 async_entries_for_config_entry 精确限定本 entry（v5.2.4）。
    reg_entries = er.async_entries_for_config_entry(reg, entry.entry_id)
    # v4.3.0: 日志口径修正——registry 计数只统计 sensor.* 条目
    # （此前打印全 domain 实体数 80，含 lock/switch/button/tracker/image/
    # select 等 16 个非 sensor，与"只清理 sensor 实体"的实际行为不符，
    # 易误导排查）。
    sensor_regs = [e for e in reg_entries if e.entity_id.startswith("sensor.")]
    _LOGGER.debug("sensor cleanup: entry=%s registry=%d new_ids=%d", entry.entry_id, len(sensor_regs), len(new_ids))
    for entry_ in sensor_regs:
        if entry_.disabled_by is not None:
            continue
        if entry_.unique_id not in new_ids:
            _LOGGER.debug("sensor cleanup: removing stale %s", entry_.entity_id)
            reg.async_remove(entry_.entity_id)  # v3.7.15: HA 2026.x async_remove 为同步返回 None，await 报 TypeError


def _make_sensors(coordinator: FordPassCoordinator) -> list[SensorEntity]:
    # v4.9.3: 按车型能力创建——纯电（fuelType=E/BEV）无燃油/机油系统，
    # 燃油量/续航里程/机油健康/机油寿命实体不创建（全车型正确性；
    # 燃油/柴油/混动/插混车型照常创建）。
    _fuel = str(coordinator.vehicle_info.get("fuelType") or "").upper()
    _not_ev = _fuel not in ("E", "BEV")
    sensors: list[SensorEntity] = []
    if _not_ev:
        sensors += [
            FordPassSensor(coordinator, "fuel_level", "燃油量", "%", SensorDeviceClass.BATTERY, "mdi:fuel", ["fuel", "fuelLevel"], round_value=True),  # v5.0.9: 燃油量用 battery device_class——HA 设备页右上角主显示只认 battery 类传感器（前端 findBatteryEntity 硬规则），改 None 会导致右上角不显示油量；图标由 HA 固定渲染电池样式（官方机制限制，无法自定义）
            FordPassSensor(coordinator, "distance_to_empty", "续航里程", UnitOfLength.KILOMETERS, SensorDeviceClass.DISTANCE, "mdi:road-variant", ["fuel", "distanceToEmpty"]),
        ]
    sensors += [
        FordPassSensor(coordinator, "odometer", "总里程", UnitOfLength.KILOMETERS, SensorDeviceClass.DISTANCE, "mdi:counter", ["odometer"]),
        # v3.3.9: 机油寿命仅保留 prognostic 版（FordPassOilLifeSensor，含归零月份/
        # 剩余公里属性）；原 oil_life（oil.oilLifeActual）重复实体已删除。
        FordPassSensor(coordinator, "battery_voltage", "蓄电池电压", "V", SensorDeviceClass.VOLTAGE, "mdi:car-battery", ["battery", "batteryStatusActual"]),
        FordPassSensor(coordinator, "lf_tire", "左前轮胎压", UnitOfPressure.KPA, SensorDeviceClass.PRESSURE, "mdi:gauge", ["TPMS", "leftFrontTirePressure"], round_value=True),
        FordPassSensor(coordinator, "rf_tire", "右前轮胎压", UnitOfPressure.KPA, SensorDeviceClass.PRESSURE, "mdi:gauge", ["TPMS", "rightFrontTirePressure"], round_value=True),
        FordPassSensor(coordinator, "lr_tire", "左后轮胎压", UnitOfPressure.KPA, SensorDeviceClass.PRESSURE, "mdi:gauge", ["TPMS", "outerLeftRearTirePressure"], round_value=True),
        FordPassSensor(coordinator, "rr_tire", "右后轮胎压", UnitOfPressure.KPA, SensorDeviceClass.PRESSURE, "mdi:gauge", ["TPMS", "outerRightRearTirePressure"], round_value=True),
        FordPassSensor(coordinator, "lock_status", "门锁状态", None, None, "mdi:lock", ["lockStatus"], enum_map={"LOCKED": "已锁定", "UNLOCKED": "已解锁", 1: "已锁定", 0: "已解锁", "1": "已锁定", "0": "已解锁"}),
        # 报警状态映射（v2.6.3 最新语义）：SET=已设防 / NOTSET=未设防 /
        # NOT_IN_ALARM·DISARMED=解除报警 / ALARM·ARMED=被盗声光报警中
        FordPassSensor(coordinator, "alarm_status", "报警状态", None, None, "mdi:alarm", ["alarm"],
                       enum_map={"SET": "车辆已设防", "NOTSET": "车辆未设防",
                                 "NOT_IN_ALARM": "解除报警", "NOT_IN_ALARMS": "解除报警",
                                 "DISARMED": "解除报警", "ALARM": "被盗声光报警中",
                                 "ARMED": "被盗声光报警中", 0: "解除报警", 1: "被盗声光报警中",
                                 "0": "解除报警", "1": "被盗声光报警中",
                                 }),
        FordPassSensor(coordinator, "remote_start", "远程启动状态", None, None, "mdi:engine", ["remoteStartStatus"], enum_map={1: "已远程启动", 0: "未远程启动", "1": "已远程启动", "0": "未远程启动", "false": "未远程启动"}),
    ]
    # 车辆异常警示：v2.6.5 起合并为单一实体 —— 优先读 vha/activealert
    # 真实告警（明文中文标题）；接口不可用/无数据时回退 PrmtAlarmEvent
    # 等历史字段（Null = 无异常）。原 vehicle_warning_fallback 实体已移除。
    sensors.append(FordPassAlertSensor(coordinator))
    # v4.3.4: 车辆主动告警列表（/api/v1/activealertlist 独立端点）——探测
    # 成功（业务 200）即创建（空列表显示「无告警」），失败/拒绝不创建。
    if isinstance(coordinator.activealertlist, dict) and coordinator.activealertlist:
        sensors.append(FordPassActiveAlertListSensor(coordinator))
    # v5.9.2: 身份与静态属性归诊断类目（不占主实体区、不进自动仪表盘）。
    sensors.append(FordPassVehicleAttrSensor(coordinator, "license_plate", "车牌号", "license_plate", "mdi:car", EntityCategory.DIAGNOSTIC))
    sensors.append(FordPassVehicleAttrSensor(coordinator, "vehicle_nickname", "车辆昵称", "nickname", "mdi:car-info", EntityCategory.DIAGNOSTIC))
    sensors.append(FordPassVehicleAttrSensor(coordinator, "vehicle_vin", "车辆识别码", "vin", "mdi:identifier", EntityCategory.DIAGNOSTIC))
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
    if _not_ev:  # v4.9.3: 机油健康仅非纯电车型创建
        sensors.append(FordPassSensor(coordinator, "oil_health", "机油健康状态", None, None, "mdi:oil", ["oil", "oilLife"], enum_map=_health_map))
    sensors += [
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
        FordPassSensor(coordinator, "remote_start_duration", "远程启动时长", UnitOfTime.MINUTES, None, "mdi:clock-outline", ["remoteStart", "remoteStartDuration"], category=EntityCategory.DIAGNOSTIC),
        # v2.8.0: 远程启动时间（unix 秒 → 本地时间字符串）与动态"距离自动熄火时间"倒计时
        # v3.0.2: 未启动（值为 0）时显示「未启动」，不再显示 unknown
        FordPassSensor(coordinator, "remote_start_time", "远程启动时间", None, None, "mdi:clock-start",
                       ["remoteStart", "remoteStartTime"], transform=_remote_start_time),
        FordPassAutoOffSensor(coordinator),
        # v3.6.10: 移除 authorization（vehicle-status 授权状态）——与
        # user_auth「车辆用户授权状态」语义重复（两实体都显示授权状态），
        # 保留 user_auth（authorizationStatus+role+ownerMobile 信息更全）。
        # 旧条目由 setup 清理段自动 async_remove。
        # FordPassSensor(coordinator, "authorization", "授权状态", None, None, "mdi:shield-check", ["authorization"],
        #                enum_map={"AUTHORIZED": "已授权", "UNAUTHORIZED": "未授权", "EXPIRED": "已过期"}),
        FordPassSensor(coordinator, "crcc_flag", "远程控车功能", None, None, "mdi:remote", ["crccFlag"],
                       enum_map={"ON": "已开启", "OFF": "已关闭"}, category=EntityCategory.DIAGNOSTIC),
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
    # 保证任何车型登录后只看到自己有真实数据的实体（如实测车型看不到充电状态）。
    _charge_map = {"Charging": "充电中", "NotCharging": "未充电", "FullyCharged": "已充满",
                   "Complete": "已完成", "Charged": "已充满", "Discharging": "放电中"}
    _plug_map = {"Connected": "已连接", "Disconnected": "未连接", "Unplugged": "未插枪"}
    _bool_map = {"true": "是", "false": "否", 1: "是", 0: "否", "1": "是", "0": "否"}
    _hybrid_map = {"EV": "纯电模式", "HEV": "混动模式", "ER": "增程模式", "Off": "关闭"}
    _precond_map = {"ON": "开启", "OFF": "关闭", "Running": "运行中", "PENDING": "等待中", "ERROR": "异常"}
    _diesel_metric_map = {"Active": "激活", "Inactive": "未激活", "Regeneration": "再生中"}
    sensors += [
        # 纯电 / 插混（电马 Mustang Mach-E、锐界 L 混动等）
        FordPassSensor(coordinator, "battery_fill_level", "动力电池电量", "%", SensorDeviceClass.BATTERY, "mdi:battery-high", ["batteryFillLevel"], round_value=True, skip_if_missing=True),
        FordPassSensor(coordinator, "ev_dte", "纯电续航", UnitOfLength.KILOMETERS, SensorDeviceClass.DISTANCE, "mdi:lightning-bolt", ["elVehDTE"], skip_if_missing=True),
        FordPassSensor(coordinator, "charging_status", "充电状态", None, None, "mdi:power-plug", ["chargingStatus"], enum_map=_charge_map, skip_if_missing=True, evcc_state={"已充满": 3, "充电中": 2, "未充电": 1}),
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
        FordPassSensor(coordinator, "dual_rear_wheel", "双后轮", None, None, "mdi:car", ["TPMS", "dualRearWheel"], enum_map={1: "启用", 0: "停用", "1": "启用", "0": "停用"}, skip_if_missing=True, skip_if_zero=True),
        # 车门 / 车内环境 / 状态标志
        FordPassSensor(coordinator, "inner_tailgate", "内尾门", None, None, "mdi:car-back", ["doorStatus", "innerTailgateDoor"], enum_map=_door_map, skip_if_missing=True),
        FordPassSensor(coordinator, "cabin_temp", "车内温度", "°C", SensorDeviceClass.TEMPERATURE, "mdi:thermometer", ["CabnAmbTeActl"], round_value=True, skip_if_missing=True),
        FordPassSensor(coordinator, "deep_sleep", "深度睡眠模式", None, None, "mdi:sleep", ["deepSleepInProgress"], enum_map=_bool_map, skip_if_missing=True),
        FordPassSensor(coordinator, "firmware_upgrade", "固件升级中", None, None, "mdi:update", ["firmwareUpgInProgress"], enum_map=_bool_map, skip_if_missing=True, category=EntityCategory.DIAGNOSTIC),
        # v3.0.1: 天窗（部分车型上报 sunroofPosition；无该字段的车型不创建）
        FordPassSensor(coordinator, "sunroof", "天窗", None, None, "mdi:car-select",
                       [["sunroofPosition"], ["windowPosition", "sunroofPosition"], ["sunroof"]],
                       enum_map={"Closed": "已关闭", "Open": "已打开", "Open_Tilt": "倾斜开启",
                                 "Tilt": "倾斜", "Partially_Open": "部分开启", "Vent": "通风",
                                 "Unknown": "未知"}, skip_if_missing=True),
    ]

    # v3.3.9: 柴油实体（领裕/撼路者柴油版等）——仅柴油燃料车型（fuelType=D）
    # 才创建；汽油车即使云端返回 dieselSystemStatus 字段也跳过（实测车型曾显示
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
    if (coordinator.data or {}).get("ota_setting"):
        sensors.append(FordPassOtaSettingSensor(coordinator))
    # v3.1.4: 服务信息（保养计划/召回/SIM/WiFi）——拉取成功才创建
    for key, cls, _label in (
        ("maintenance_plan", FordPassMaintenancePlanSensor, "保养计划"),
        ("recall", FordPassRecallSensor, "召回信息"),
        ("wifi_status", FordPassWifiSensor, "WiFi 热点"),
    ):
        payload = (coordinator.data or {}).get(key)
        if payload:
            sensors.append(cls(coordinator))
    # v4.3.2: SIM 卡无条件创建——v4.3.0 起 sim_info 对未开通车型（纯油）
    # 确定性业务拒绝（CONS.SYS.0002）落盘 None，旧"拉取成功才创建"
    # （if payload:）导致该实体被 sensor cleanup 删除（用户实测实体数
    # 80→79 少 1 个）。v3.3.0 语义即「未开通」，实体应常驻显示——
    # sim_info 为 None/空时 _format 返回「车辆未开通该服务」而非 unknown。
    sensors.append(FordPassSimSensor(coordinator))
    # v3.1.4: 鸣笛设置云端状态
    # v3.9.2: 无条件创建（不再要求查询成功且有值）——probe 一次性初始化
    # 若云端暂不可达/无数据，sensor 显示「未获取」并保留最后已知状态，
    # 避免实体缺失（v3.9.1 曾因探测空值导致此传感器不创建）。
    # v5.4.7: 云端同步健康（后端变更监控）——诊断实体无条件创建
    sensors.append(FordPassSyncHealthSensor(coordinator))
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
        # v5.4.7: 上次充电电量（充电记录含真实电量字段才创建）
        sensors.append(FordPassLastChargeSensor(coordinator))
    # v4.3.3: fposb 4S 维保预约传感器——探测到数据才创建（无维保服务
    # 车型探测空 → 0 实体）；全部只读查询，不触发下单/取消动作。
    if isinstance(coordinator.fposb, dict) and coordinator.fposb:
        sensors.append(FordPassFposbSensor(coordinator, "fposb_history", "最近维保预约", "detail"))
        sensors.append(FordPassFposbSensor(coordinator, "fposb_history", "维保预约数量", "count"))
    # v4.3.3: evss 充电站/免密支付传感器——车型过滤已在 coordinator
    # （仅电/插混/混动探测，纯油/柴油 evss=None）；探测到数据才创建。
    if isinstance(coordinator.evss, dict) and coordinator.evss:
        sensors.append(FordPassEvssSensor(coordinator, "evss_charger_info", "充电站数量", "station_count"))
        sensors.append(FordPassEvssSensor(coordinator, "evss_freepass", "充电免密支付状态", "freepass"))
        sensors.append(FordPassEvssSensor(coordinator, "evss_order_events", "最近充电订单", "order"))
    # v3.8.3: 已移除「预约出发/充电探测诊断」传感器（用户要求不创建诊断
    # 实体；探测仍执行，结果写 api.cevs_diag 供日志/排查）。
    # v3.7.24: 预约出发/充电业务传感器——探测到数据才创建
    # （cevs_probe 非空 = 端点返回真实 200）。
    # v3.8.2: 再按车型过滤——纯油/柴油车 cevs 端点同样返回 200 但业务
    # 恒空（isEnabled=Off / chargeLogs=[]），预约出发/充电是 EV/PHEV/
    # 混动功能；仅 fuelType ∈ 电/插混/混动集合才创建，其余车型不创建
    # （升级残留由 async_setup_entry 的 registry 清理自动移除）。
    cevs_probe = getattr(coordinator, "cevs_probe", {}) or {}
    fuel = str(coordinator.vehicle_info.get("fuelType") or "").upper()
    cevs_supported = fuel in ("E", "BEV", "P", "PHEV", "H", "HEV", "MHEV")
    if cevs_supported and cevs_probe.get("departuretimes"):
        sensors.append(FordPassCevsDepartureSensor(coordinator))
    if cevs_supported and cevs_probe.get("chargelogs"):
        sensors.append(FordPassCevsChargelogSensor(coordinator))
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
            # v3.5.0: 6.16.0 补全端点（探测到数据才创建，全车型自动适配）
            ("onlinernr", "车辆在线状态", "online"),
            ("3d_model", "3D 车型图地址", "model3d"),
            ("schedule_departure", "预约行程", "schedule"),
            ("pds_device", "车辆设备信息", "device"),
            ("user_auth", "车辆用户授权状态", "auth"),
            ("srs_profile", "SRS 安全档案", "srs"),
            ("sensor_shadow", "传感器影子", "shadow"),
            ("video_file", "行车记录视频", "video"),
            ("messages_page", "最新消息", "messages"),
            ("ota_reddot", "OTA 更新提醒", "ota_reddot"),
            # v3.7.1: 保养历史（maintenance-history）——v3.3.7 起探测，
            # 此前账号非车主时网关业务拒绝（User not vehicle owner）无数据
            # 不创建；账号认证车主后探测有数据即创建（全车型自动适配）。
            ("maintenance-history", "保养历史", "maintenance"),
            # v3.5.1: 账号级只读端点补全（探测到数据才创建）
            ("uservehicles", "车辆清单", "uservehicles"),
            ("messages_v2", "消息中心 v2", "messages_v2"),
            ("share_list", "车辆共享列表", "share_list"),
            ("device_list", "车辆设备列表", "device_list"),
            # v4.2.6: 移动流量商城 H5 地址（6.16.0 独立实测确认，
            # 探测到数据才创建，全车型自动适配）
            ("cmcc_traffic", "移动流量商城", "cmcc"),
            # v3.6.2: 待开发清单补全（mcm 消息中心 v3 / 用户级红点）
            ("mcm_messages_v3", "消息中心", "mcm_messages"),
            ("user_reddot", "红点状态", "user_reddot"),
            # v3.7.2: 车辆授权列表（vehicle-user-auth）+ 公共充电探测
            # （EVSS/vpoi 纯电插混专属——探测到数据才创建，纯油车型无数据
            # 不创建，全车型自动适配）
            ("user_auth_list", "车辆授权列表", "auth_list"),
            ("evss_stations", "公共充电站", "evss_stations"),
            ("evss_orders", "充电订单", "evss_orders"),
            ("vpoi_chargestations", "充电站地图", "vpoi_chargestations"),
            # v3.7.3: 家充桩扩展（分享列表/桩级充电状态/充电信息/充电订单）
            # ——仅探测到绑定桩+桩级数据才创建（纯油车型无墙盒不创建）
            ("wallbox_sharing_query", "家充桩分享列表", "wallbox_sharing"),
            ("wallbox_charge_status", "家充桩充电状态(桩级)", "wallbox_charge_status"),
            ("wallbox_charging", "家充桩充电信息", "wallbox_charging"),
            ("wallbox_orders", "家充桩充电订单", "wallbox_orders"),
            # v4.3.5: 228 端点比对后未接入的只读端点补全（全部无需实测
            # ——GET + 标准签名，探测到数据才创建，与现有实体不重复）：
            # 家充桩只读扩展（smartwallbox 绑定桩才创建）：
            ("wallbox_bound", "家充桩绑定", "wallbox_bound"),
            ("wallbox_info", "家充桩墙盒详情", "wallbox_info"),
            ("wallbox_records_v2", "家充桩充电记录", "wallbox_records_v2"),
            # 充电域只读扩展（evss/vpoi，电/插混/混动探测到数据才创建）：
            ("evss_order_status", "充电订单状态", "evss_order_status"),
            ("evss_apps_info", "EVSS 服务信息", "evss_apps_info"),
            ("evss_usage_notice", "充电使用通知", "evss_usage_notice"),
            ("vpoi_status", "充电站状态", "vpoi_status"),
            ("vpoi_saved", "收藏充电站", "vpoi_saved"),
            # OTA 协议（全车型只读）：
            ("ota_agreements", "OTA 协议", "ota_agreements"),
            # 车辆分享能力/历史（cnesl-user 账号级只读）：
            ("share_capability", "车辆分享能力", "share_capability"),
            ("share_history", "车辆分享历史", "share_history"),
            # v4.3.6: 136 未接入清单第二轮筛选——剩余只读业务端点补全
            # （探测到数据才创建，与现有实体不重复）：
            ("evss_payment_summary", "充电支付汇总", "evss_payment_summary"),
            ("evss_invoiceables", "可开票充电订单", "evss_invoiceables"),
            ("evss_events_v3", "充电事件", "evss_events_v3"),
            ("evss_evaluate_url", "充电订单评价链接", "evss_evaluate_url"),
            ("wallbox_ble_vehicles", "家充桩 BLE 车辆信息", "wallbox_ble_vehicles"),
            ("app_init_settings", "应用设置信息", "app_init_settings"),
            ("vehicle_model_masterdata", "车型主数据", "vehicle_model_masterdata"),
            ("user_login_status", "用户登录状态", "user_login_status"),
            # v5.8.4: 访客模式 v3 页面初始化信息（App 设置页入口；实测确认
            # /api/app-init/v3/visitor-mode/init-page-info，探测到数据才创建，
            # 全车型自动适配）
            ("visitor_mode", "访客模式", "visitor_mode"),
            # v4.8.1: 林肯之道专属账号级只读端点（5.8.0 实测）——仅
            # brand==lincoln 时 coordinator 探测，键存在（探测成功）才创建；
            # 福特派账号不探测、键不存在不创建，零副作用。
            ("lincoln_member_info", "林肯会员信息", "lincoln_member"),
            ("lincoln_my_page", "林肯我的页面", "lincoln_page"),
            ("lwosb_bookings", "取送车预约", "lwosb_bookings"),
            ("lwosb_order_status", "取送车预约状态", "lwosb_status"),
        ):
            # v3.6.2: uservehicles 探测实为注册端点（rejected 不存 _ep），
            # 但 vehicles_list（v5/vehicles/list，登录已验证）恒有值——
            # 「车辆清单」传感器据此创建。
            # v4.3.9: 按车型创建实体，不要创建无用实体——13 个充电域实体
            # （家充桩/EVSS/VPOI/充电订单等）对纯油(G)/柴油(D)车业务恒空
            # （探测 200 但无业务数据），按 fuelType 创建：电/插混/混动
            # （E/BEV/P/PHEV/H/HEV/MHEV）才创建。
            # v5.0.1: 全部探测实体统一「按车型 + 按数据非空才创建」——全车型
            # 6 种（OTA 协议/车辆分享/应用设置/车型主数据/用户登录状态）与
            # 充电域 13 种同样要求探测数据非空（_ep.get(_k) 为真值），无数据
            # 不创建实体、不显示「暂无数据」占位噪音；旧实体由下方清理段
            # （unique_id 不在新集合即移除）自动删除。
            _charge_kinds = ("wallbox_bound", "wallbox_info", "wallbox_records_v2",
                             "evss_order_status", "evss_apps_info", "evss_usage_notice",
                             "vpoi_status", "vpoi_saved",
                             "evss_payment_summary", "evss_invoiceables",
                             "evss_events_v3", "evss_evaluate_url",
                             "wallbox_ble_vehicles")
            _common_kinds = ("ota_agreements", "share_capability", "share_history",
                             "app_init_settings", "vehicle_model_masterdata",
                             "user_login_status", "visitor_mode")
            _fuel = str(coordinator.vehicle_info.get("fuelType") or "").upper()
            _charge_ok = _fuel in ("E", "BEV", "P", "PHEV", "H", "HEV", "MHEV")
            _probe_ok = bool(_ep.get(_k))  # v5.0.1: 数据非空才创建
            if (
                (_k in _common_kinds and _probe_ok)
                or (_k in _charge_kinds and _charge_ok and _probe_ok)
                or (_k not in _charge_kinds and _k not in _common_kinds
                    and _probe_ok)
                or (_k == "uservehicles"
                    and getattr(coordinator, "vehicles_list", None))
            ):
                sensors.append(FordPassProbeSensor(coordinator, _k, _lbl, _kind))
    # v3.1.7: 空调滤芯状态（AAR 能力车型；无 airFilter 字段的车型不创建，
    # 保证其他用户登录各自车型时不出现不支持的实体）
    air_filter = (coordinator.data or {}).get("air_filter")
    if isinstance(air_filter, dict):
        af_payload = air_filter.get("data") if isinstance(air_filter.get("data"), dict) else None
        if isinstance(af_payload, dict) and isinstance(af_payload.get("airFilter"), dict):
            sensors.append(FordPassAirFilterSensor(coordinator))
    # v3.1.9: 云端能力探测（ccfeatures 能力位图；位图有已开通能力才创建——
    # 全车型自动适配：各自位图 → 各自能力集 → 实体自动创建）。
    # v3.2.4: 不再创建「车辆服务信息」传感器（ccfeatures 明细，用户要求删除）。
    ccf = (coordinator.data or {}).get("ccfeatures")
    if isinstance(ccf, dict) and capability.parse_cloud_features(ccf):
        sensors.append(FordPassCloudProbeSensor(coordinator))
    # v4.6.4: ccfeatures 服务信息 7 字段 → 7 个独立实体（用户要求分实体显示）。
    # 每字段有数据才创建对应实体（全车型自动适配，无数据不建无用实体）。
    # 响应样例（2026-10-15 实机）：rsaNumber=4006501668 / contactNumber=
    # 4009213266 / afterSalesNumber=4000031111 / videoManualUrl=... /
    # featureData.availableFeatures="03,04,..." / authedFeatures="A4" /
    # specifiedFeatures=null（null → 不创建「指定功能」实体）。
    # v4.6.3 聚合「车辆服务信息」实体废弃，旧条目由下方清理段自动移除。
    _ccf_payload = ccf.get("data") if isinstance(ccf, dict) else None
    if isinstance(_ccf_payload, dict):
        for _key, _name, _icon, _path, _cat in _SERVICE_INFO_FIELDS:
            if _service_field_value(_ccf_payload, _path) not in (None, "", "null"):
                sensors.append(
                    FordPassServiceFieldSensor(
                        coordinator, _key, _name, _icon, _path, _cat
                    )
                )
    # v3.1.13: 鸣笛命令状态（最近一次鸣笛的 announcestatus 结果；未触发时
    # 显示「未触发」，永远可用——v3.1.2 规则）
    sensors.append(FordPassAnnounceStatusSensor(coordinator))
    # v3.1.9: 预测性诊断（prognostic，200 实测）——有业务数据才创建
    prog = (coordinator.data or {}).get("prognostic")
    if isinstance(prog, dict):
        prog_payload = prog.get("data") if isinstance(prog.get("data"), dict) else None
        if isinstance(prog_payload, dict):
            # v4.9.3: 机油寿命仅非纯电车型创建（纯电 prognostic 无 iolm）
            if _not_ev:
                sensors.append(FordPassOilLifeSensor(coordinator))
            # v3.3.9: 剩余可行驶里程仅在 remainingKMs 有值时创建
            # （实测车型 prognostic 曾返回无该字段 → 实体恒 unknown，不创建）
            if prog_payload.get("remainingKMs") is not None:
                sensors.append(FordPassRangeSensor(coordinator))
            sensors.append(FordPassSlowLeakSensor(coordinator))
            sensors.append(FordPassDiagnosticSensor(coordinator))
    # v3.1.17: 未读消息（GET /api/cnxapi-message/app/messages/summary，
    # 实机样本实测 200）——有 data.summary 才创建（无数据车型不创建，
    # 不显示不可用）
    msg = (coordinator.data or {}).get("messages")
    if isinstance(msg, dict):
        msg_payload = msg.get("data") if isinstance(msg.get("data"), dict) else None
        if isinstance(msg_payload, dict) and isinstance(msg_payload.get("summary"), dict):
            sensors.append(FordPassMessageSensor(coordinator))
    # v5.0.8: 设备页主显示传感器按车型匹配（HA 设备页取设备第一个 sensor 实体）——
    # 汽油/柴油：燃油量；纯电：动力电池电量；混动/插混/油电混合：油量+电量都靠前
    _fuel = str(coordinator.vehicle_info.get("fuelType") or "").upper()
    if _fuel in ("E", "BEV"):
        _primary_order = ["battery_fill_level"]
    elif _fuel in ("G", "D"):
        _primary_order = ["fuel_level"]
    else:  # 混动等：油量、电量并列为设备主显示
        _primary_order = ["fuel_level", "battery_fill_level"]
    if _primary_order:
        _pri: list = []
        for _key in _primary_order:
            for _s in list(sensors):
                if getattr(_s, "_key", None) == _key:
                    _pri.append(_s)
                    sensors.remove(_s)
        sensors = _pri + sensors
    # v5.9.8: last_poll 从全部实体属性收口到唯一的诊断实体（默认停用，
    # 需要的自动化场景手动启用——启用后它自己每轮变化，但只此一个）
    sensors.append(FordPassLastPollSensor(coordinator))
    return sensors



class FordPassVehicleAttrSensor(SensorEntity):
    """Reads a fixed attribute stored on the coordinator (e.g. license plate)."""

    def __init__(self, coordinator, key, label, attr, icon=None, category=None) -> None:
        self.coordinator = coordinator
        self._attr_key = attr
        self._attr_unique_id = f"{coordinator.uid_key}-{key}"
        self._attr_name = label
        self._attr_has_entity_name = True
        self._attr_device_info = coordinator.device_info
        if icon:
            self._attr_icon = icon
        if category:
            self._attr_entity_category = category

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)  # v5.3.1: 保留最后已知状态——刷新失败不置不可用（与 switch/lock/select/device_tracker 对齐）

    @property
    def native_value(self):
        return getattr(self.coordinator, self._attr_key, None)

    @property
    def extra_state_attributes(self) -> dict:
        # v5.9.8: 不再随实体携带 last_poll——该值每轮刷新都变，会把值
        # 未变化的实体也拉进 state_changed（recorder 行数随实体数线性
        # 增长）。全局唯一权威来源见「云端最后刷新」诊断实体。
        return {}


class FordPassLocationSensor(SensorEntity):
    """Vehicle location (address / coordinates), best effort."""

    # v5.9.2: 与 device_tracker 保持一致——位置属设备自身信息，归诊断类目。
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.uid_key}-location"
        self._attr_name = "车辆定位"
        self._attr_has_entity_name = True
        self._attr_device_info = coordinator.device_info
        self._attr_icon = "mdi:map-marker"
        self._last_known: str | None = None  # v3.4.3: 最后已知地址，LBS 失败时兜底

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)  # v5.3.1: 保留最后已知状态——刷新失败不置不可用（与 switch/lock/select/device_tracker 对齐）

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data or {}
        loc = data.get("location")
        attrs: dict = {}
        if isinstance(loc, dict):
            def _to_float(v):
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None
            attrs = {
                # v4.9.1: 数值化 lat/lon（此前是字符串，自动化比较不便）
                "latitude": _to_float(loc.get("lat")),
                "longitude": _to_float(loc.get("lon")),
                "upload_time": loc.get("uploadTime"),
                "address": loc.get("address"),
            }
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



class FordPassRestoreMixin(RestoreEntity):
    """v3.8.9: 重启/集成重载后先恢复最后已知状态显示（restore_state 机制），
    消除 unavailable 过渡态——实体在 coordinator 数据未就绪时返回恢复值，
    新数据到达后自动覆盖为实时值。

    async_get_last_state() 是 RestoreEntity 提供的方法，普通 Entity 没有，
    因此 mixin 必须继承 RestoreEntity（独立 mixin，与 Entity 无 MRO 冲突）。
    """

    _restored: str | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self._restored = last.state
        # v4.8.7: 实体激活后立即强制写一次活跃状态——覆盖 restore_state 恢复的
        # unavailable/旧值残留（v4.8.5 添加失败期恢复的 restored 状态会让实体
        # 一直显示 unavailable 且不随刷新更新）。coordinator 有数据即可用，
        # 写入后实体恢复显示并随后续每次刷新更新 last_updated。
        coord = getattr(self, "coordinator", None)
        if coord is not None and coord.data is not None:
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """透传子类属性。v5.9.8: 不再统一注入 last_poll——该值每轮刷新
        都变，会把值未变化的实体也拉进 state_changed（recorder 行数随
        实体数线性增长）。全局唯一权威来源见「云端最后刷新」诊断实体。
        兼容逻辑说明：v4.8.5 曾漏 @property 导致控制类实体添加报
        TypeError: 'method' object is not iterable——@property 必加。"""
        attrs: dict[str, object] = {}
        # v4.8.8: 兼容 HA 2026.10——基类 Entity.extra_state_attributes 是
        # @property（返回 None），直接 super().extra_state_attributes() 会
        # TypeError: 'NoneType' object is not callable（实体添加失败、永远
        # unavailable）。统一取值：属性→dict 直接用，方法→调用，None→跳过。
        try:
            parent = super().extra_state_attributes
            if callable(parent):
                parent = parent()
            if parent and isinstance(parent, dict):
                attrs.update(parent)
        except (AttributeError, TypeError):
            pass
        return attrs

    def _restore_bool(self, live: bool | None) -> bool | None:
        """bool 状态兜底（is_locked/is_on）：live 有效返回 live，
        否则按 HA 状态机文本（locked/unlocked、on/off）解析恢复值。"""
        if live is not None:
            return live
        if self._restored is None:
            return None
        return self._restored.lower() in ("on", "locked", "true", "1")

    def _restore_str(self, live):
        """字符串状态兜底（sensor/select/device_tracker）：live 有效返回
        live，否则返回恢复值。"""
        if live is not None:
            return live
        return self._restored


class FordPassLastPollSensor(SensorEntity):
    """云端最后刷新时间（v5.9.8，诊断实体，默认停用）。

    v5.9.8: last_poll 从全部实体属性中移除——该值每轮刷新都变，会把
    值未变化的实体也拉进 state_changed，recorder 行数随实体数线性增长
    （实测一次刷新 80 个实体同时写入、每天上万行）。全局唯一权威来源
    收口到本实体：依赖「最后刷新时间」的自动化迁移到本实体
    （device_class=TIMESTAMP，前端显示相对时间、可直接做时间比较）。

    本实体默认停用（_attr_entity_registry_enabled_default=False）：
    启用后它自己每轮变化写 recorder，但只此一个，不再有 80 倍噪音。
    """

    _attr_should_poll = False  # coordinator listener 驱动
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_registry_enabled_default = False
    _attr_icon = "mdi:cloud-clock"
    _attr_has_entity_name = True

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.uid_key}-last_poll"
        self._attr_name = "云端最后刷新"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        return self.coordinator.last_poll is not None

    @property
    def native_value(self) -> datetime | None:
        lp = self.coordinator.last_poll
        return dt_util.as_utc(lp) if lp else None


class FordPassSensor(FordPassRestoreMixin, SensorEntity):
    # P2-7（v5.6.0）：纯推送实体（coordinator listener 驱动），关闭 HA
    # 每 30 秒的轮询——此前 100+ 个传感器被平台默认轮询，负载与云请求
    # 白增（本地无更新接口，轮询只会反复读旧快照）。
    _attr_should_poll = False

    def __init__(self, coordinator, key, label, unit, device_class, icon, path,
                 round_value: bool = False, enum_map: dict | None = None,
                 multiplier: float | None = None, transform=None,
                 skip_if_missing: bool = False,
                 skip_if_zero: bool = False,
                 evcc_state: dict | None = None,
                 category=None) -> None:
        # v5.4.6: EVCC 对接——充电状态等 enum 实体附带数值型 evcc_charge_state
        # 属性（0 未知/1 未充电/2 充电中/3 已充满，EVCC chargeState 语义）
        self._evcc_state = evcc_state
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
        # v3.5.7: 值为 0/停用 时不创建（如 dualRearWheel=0 = 非双后轮车型）
        self._skip_if_zero = skip_if_zero
        self._attr_unique_id = f"{coordinator.uid_key}-{key}"
        self._attr_name = label
        self._attr_has_entity_name = True
        self._attr_device_info = coordinator.device_info
        self._attr_icon = icon
        if unit:
            self._attr_native_unit_of_measurement = unit
            # v5.0.7: 带单位的数值传感器标记为 measurement，启用长期统计/趋势图
            self._attr_state_class = SensorStateClass.MEASUREMENT
        if device_class:
            self._attr_device_class = device_class
        if category:
            self._attr_entity_category = category

    @property
    def data_usable(self) -> bool:
        """创建期过滤：skip_if_missing 的实体仅当字段当前有有效值时创建；
        skip_if_zero 的实体值为 0/停用 时（非该能力车型）不创建。"""
        if self._skip_if_missing:
            status = (self.coordinator.data or {}).get("vehiclestatus", {})
            if not _is_usable(status, self._paths):
                return False
        if self._skip_if_zero:
            val = _first_leaf((self.coordinator.data or {}).get("vehiclestatus", {}), self._paths)
            if val in (0, "0", False, "false", "False", "停用", None):
                return False
        return True

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)  # v5.3.1: 保留最后已知状态——刷新失败不置不可用（与 switch/lock/select/device_tracker 对齐）

    @property
    def extra_state_attributes(self) -> dict:
        """Expose Ford's own data timestamp/status for this reading (v2.7.2)."""
        status = (self.coordinator.data or {}).get("vehiclestatus", {})
        attrs: dict = {}
        node = _first_leaf_node(status, self._paths)
        if isinstance(node, dict):
            attrs["timestamp"] = node.get("timestamp")
            attrs["source_status"] = node.get("status")
        if isinstance(status, dict):
            attrs["vehicle_data_time"] = status.get("lastModifiedDate") or status.get("lastRefresh")
        # v5.4.6: EVCC 对接——charging_status 中文 enum -> 数值 chargeState
        # （0 未知/1 未充电/2 充电中/3 已充满，EVCC chargeState 语义）
        if self._evcc_state and self.native_value is not None:
            attrs["evcc_charge_state"] = self._evcc_state.get(self.native_value, 0)
        # v4.9.0: 过滤空值属性（source_status: null 等噪音键）
        return {k: v for k, v in attrs.items() if v not in (None, "")}

    @property
    def native_value(self):
        status = (self.coordinator.data or {}).get("vehiclestatus", {})
        value = _first_leaf(status, self._paths)
        if value is None:
            # v3.8.9: 重启/重载后数据未就绪时恢复最后已知状态，消除 unavailable
            return self._restored
        if isinstance(value, (list, dict)):
            if isinstance(value, list) and value and all(isinstance(x, str) for x in value):
                value = "、".join(value)
            else:
                return None
        if self._multiplier is not None and isinstance(value, (int, float)) and not isinstance(value, bool):
            value = value * self._multiplier
        if self._round_value and isinstance(value, (int, float)) and not isinstance(value, bool):
            return round(value)
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

    _FALLBACK_PATHS: ClassVar[list[list[str]]] = [
        ["PrmtAlarmEvent"], ["warning"], ["warnings"], ["alerts"],
        ["vehicleAbnormal"], ["abnormal"], ["abnormalWarning"],
        ["abnormalStatus"], ["warningStatus"], ["faults"],
        ["diagnostics"], ["warningInfo"], ["vehicleWarning"],
    ]

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.uid_key}-vehicle_alerts"
        self._attr_name = "车辆异常警示"
        self._attr_has_entity_name = True
        self._attr_device_info = coordinator.device_info
        self._attr_icon = "mdi:alert"

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)  # v5.3.1: 保留最后已知状态——刷新失败不置不可用（与 switch/lock/select/device_tracker 对齐）

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
            status = (self.coordinator.data or {}).get("vehiclestatus", {})
            node = _leaf_node(status, "PrmtAlarmEvent")
            if isinstance(node, dict):
                attrs["source"] = "vehicle_status"
                attrs["timestamp"] = node.get("timestamp")
                attrs["source_status"] = node.get("status")
        # v4.9.0: 过滤空值属性（source_status/timestamp null 噪音）
        return {k: v for k, v in attrs.items() if v not in (None, "")}

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
        status = (self.coordinator.data or {}).get("vehiclestatus", {})
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


class FordPassActiveAlertListSensor(SensorEntity):
    """车辆主动告警列表（v4.3.4，/api/v1/activealertlist）。

    数据源 coordinator.activealertlist（api.probe_active_alert_list 探测
    结果，登录后一次 + TTL 持久化缓存）。探测成功（业务 200）即创建——
    空列表显示「无告警」（与 SIM 卡传感器 v4.3.2 教训一致，创建条件过严
    会致实体缺失）；探测失败/业务拒绝保持 None 不创建。与「车辆异常警示」
    （vha/activealert）为两个独立端点，本实体展示结构化告警列表。
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:alert-circle-outline"

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.uid_key}-active_alert_list"
        self._attr_name = "车辆告警列表"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)  # v5.3.1: 保留最后已知状态——刷新失败不置不可用（与 switch/lock/select/device_tracker 对齐）

    def _probe(self) -> dict | None:
        ent = self.coordinator.activealertlist or {}
        if isinstance(ent, dict):
            data = ent.get("data")
            if isinstance(data, dict):
                return data
        return None

    def _alerts(self) -> list[dict]:
        data = self._probe()
        if data is None:
            return []
        for key in ("data", "list", "alerts", "AlertList", "VehicleAlertResponseList"):
            v = data.get(key)
            if isinstance(v, list):
                return [a for a in v if isinstance(a, dict)]
        # 单层包装（data: {alerts: [...]}）
        inner = data.get("data")
        if isinstance(inner, dict):
            for key in ("list", "alerts", "AlertList"):
                v = inner.get(key)
                if isinstance(v, list):
                    return [a for a in v if isinstance(a, dict)]
        return []

    @property
    def native_value(self) -> str | None:
        alerts = self._alerts()
        if not alerts:
            return "无告警"
        heads = []
        for a in alerts:
            for key in ("headline", "Headline", "title", "Title", "alertName", "message"):
                if a.get(key):
                    heads.append(str(a[key]))
                    break
        return "、".join(heads) if heads else f"{len(alerts)} 条告警"

    @property
    def extra_state_attributes(self) -> dict:
        attrs: dict = {}
        alerts = self._alerts()
        if alerts:
            attrs["count"] = len(alerts)
            for i, a in enumerate(alerts[:8], 1):
                for key in ("headline", "Headline", "title", "Title", "alertName", "message", "severity", "status", "type"):
                    if a.get(key):
                        attrs[f"alert_{i}_{key}"] = a[key]
        return attrs


def _ts_to_local(v):
    """Unix 秒时间戳 → 本地时间字符串（0/None/极小占位值 → None）。

    v5.8.6: 增加可信下界——云端未启动/占位时返回 epoch 附近小值
    （实测远程启动时间显示 1970-01-01 08:00:10 = epoch+10s），
    早于 2000-01-01（946684800）的时间戳一律视为无效。
    """
    if not v:
        return None
    try:
        ts = int(v)
        if ts < 946684800:  # 2000-01-01 之前不可信（云端占位）
            return None
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return None


def _window_state(v):
    """车窗位置（v3.1.8 + v3.3.3）：字符串枚举→中文；数值→百分比。

    实测云端返回字符串枚举（Fully_Closed / BetFully_10PercentOpen /
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
    v5.8.6: 增加可信下界——云端未启动/占位时返回 epoch 附近小值（实测
    显示 1970-01-01 08:00:10 = epoch+10s），早于 2000-01-01 的时间戳
    一律视为「未启动」。
    """
    if not v:
        return "未启动"
    try:
        fv = float(v)
        ts = fv / 1000 if fv > 1e12 else fv  # 毫秒/秒自适应
        if ts < 946684800:  # 2000-01-01 之前不可信（云端占位）
            return "未启动"
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
        self._attr_unique_id = f"{coordinator.uid_key}-auto_off_remaining"
        self._attr_name = "距离自动熄火时间"
        self._attr_has_entity_name = True
        self._attr_device_info = coordinator.device_info
        self._attr_icon = "mdi:timer-sand"
        self._attr_native_unit_of_measurement = UnitOfTime.MINUTES
        # v5.4.8: 数值传感器补 measurement——可进长期统计趋势图
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)  # v5.3.1: 保留最后已知状态——刷新失败不置不可用（与 switch/lock/select/device_tracker 对齐）

    @property
    def extra_state_attributes(self) -> dict:
        status = (self.coordinator.data or {}).get("vehiclestatus", {})
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
        return attrs

    @property
    def native_value(self):
        status = (self.coordinator.data or {}).get("vehiclestatus", {})
        rs = status.get("remoteStart") or {}
        rss = status.get("remoteStartStatus") or {}
        running = rss.get("value")
        dur = rs.get("remoteStartDuration")
        t0 = rs.get("remoteStartTime")
        if not running or not dur or not t0:
            return 0
        try:
            # v5.3.9: remoteStartTime 毫秒/秒自适应（与 _remote_start_time
            # transform 同款）——毫秒（13 位）不除 1000 时 time.time()-t0
            # ≈ -1.7e12，剩余时间算出数十亿分钟的巨额错误值。
            t0f = float(t0)
            t0s = t0f / 1000 if t0f > 1e12 else t0f
            remaining = float(dur) * 60 - (time.time() - t0s)
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

    # v5.9.4: 只读传感器不能声明为配置类目（HA 会拒绝添加该实体），
    # 车辆 OTA 配置的回读值按诊断信息归类。
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    _OTA_FLAG_MAP: ClassVar[dict[int, str]] = {0: "未开通远程OTA", 1: "已开通", 2: "未知", None: "未知"}

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.uid_key}-ota_setting"
        self._attr_name = "OTA 设置状态"
        self._attr_has_entity_name = True
        self._attr_device_info = coordinator.device_info
        self._attr_icon = "mdi:update"

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)  # v5.3.1: 保留最后已知状态——刷新失败不置不可用（与 switch/lock/select/device_tracker 对齐）

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
        # v4.9.0: 过滤空值（未开通 OTA 时的 8 个空串属性噪音）
        return {k: v for k, v in attrs.items() if v not in (None, "")}

    @property
    def native_value(self):
        ota = (self.coordinator.data or {}).get("ota_setting") or {}
        flag = ota.get("remoteOTAFlag")
        return self._OTA_FLAG_MAP.get(flag, "未知")


class _ServiceInfoSensor(SensorEntity):
    """服务信息实体基类（v3.1.4）：从 coordinator.data[key] 读已拉取数据。

    端点 2026-10-02 实测 200（R3 compute_sign + 修正 headers + 无 appKey）。
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator, key: str, label: str, icon: str, uid: str | None = None) -> None:
        self.coordinator = coordinator
        self._data_key = key
        self._attr_unique_id = f"{coordinator.uid_key}-{uid or key}"
        self._attr_name = label
        self._attr_device_info = coordinator.device_info
        self._attr_icon = icon

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)  # v5.3.1: 保留最后已知状态——刷新失败不置不可用（与 switch/lock/select/device_tracker 对齐）

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
        attrs: dict[str, Any] = {}
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

    解析 ccfeatures availableFeatures 位图（VDSFeatureType，实测福特派
    6.16.0 客户端定义的 24 项枚举）→ 该车开通的云端服务能力集。
    位图不同 → 能力集不同 → 实体自动适配：如纯油车型
    03,04,05,06,07,08,10,11,23 = 计划保养服务/指南/道路救援/延保/福特金融/
    私充服务/我的订阅/我的试驾 + 未定义特性 0x23。无位图数据的车型不创建。
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:cloud-search"
    # v4.9.7: 云端能力探测属集成诊断数据（排查车型功能支持），按 HA 官方
    # 规范归入诊断类目（前端自动进诊断区，不参与常规视图统计）。
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.uid_key}-cloud_probe"
        self._attr_name = "云端能力探测"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)  # v5.3.1: 保留最后已知状态——刷新失败不置不可用（与 switch/lock/select/device_tracker 对齐）

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
        attrs: dict[str, Any] = {}
        if not feats:
            return attrs
        attrs["capabilities"] = [f"{f['zh']}（{f['en']}）" for f in feats]
        attrs["feature_ids"] = ", ".join(f["raw"] for f in feats)
        # v4.9.0: 删除逐位 capability_XX 属性（与 capabilities 数组重复）
        return attrs


def _fuel_type_zh(code) -> str | None:
    """燃料类型码 → 中文（v4.6.5）。未知码保留原文。"""
    if code is None:
        return None
    txt = str(code).strip().upper()
    return {
        "G": "汽油", "D": "柴油", "E": "纯电", "BEV": "纯电",
        "P": "插电混动", "PHEV": "插电混动", "H": "油电混动",
        "HEV": "油电混动", "MHEV": "轻混",
    }.get(txt) or (txt or None)


def _joint_venture_zh(code) -> str | None:
    """合资品牌码 → 中文（v4.6.5）。未知码保留原文。"""
    if code is None:
        return None
    txt = str(code).strip().upper()
    return {"CAF": "长安福特"}.get(txt) or (txt or None)


def _brand_zh(code) -> str | None:
    """品牌码 → 中文（v4.6.5）。未知码保留原文。"""
    if code is None:
        return None
    txt = str(code).strip().upper()
    return {"F": "福特"}.get(txt) or (txt or None)


def _vehicle_masterdata_zh(vi: dict | None) -> str | None:
    """车型主数据中文聚合（v4.6.5/v4.6.6）：<车型>（年款 · 汽油 ·
    长安福特）。车辆信息 dict 无有效字段时返回 None（调用方回退探测数据）。"""
    if not isinstance(vi, dict) or not vi:
        return None
    zh: list[str] = []
    lm = vi.get("localMarketValue")
    if lm:
        zh.append(str(lm))
    my = vi.get("modelYear")
    if my:
        zh.append(f"{my} 款")
    ft = _fuel_type_zh(vi.get("fuelType"))
    if ft:
        zh.append(ft)
    jv = _joint_venture_zh(vi.get("jointVenture"))
    if jv:
        zh.append(jv)
    if not zh:
        return None
    head = zh[0]
    rest = " · ".join(zh[1:])
    return f"{head}（{rest}）" if rest else head


# v4.6.4: ccfeatures 服务信息 7 字段 → 7 个独立实体（用户要求）。
# 顺序：(unique_id 后缀, 实体名, 图标, 响应 data 取值路径)
# 路径为 tuple 时逐层下钻（如 ("featureData", "availableFeatures")）。
_SERVICE_INFO_FIELDS: list[tuple[str, str, str, str | tuple[str, ...], str]] = [
    ("rsa_number", "救援电话", "mdi:phone-alert", "rsaNumber", EntityCategory.DIAGNOSTIC),
    ("contact_number", "客服电话", "mdi:phone", "contactNumber", EntityCategory.DIAGNOSTIC),
    ("after_sales_number", "售后电话", "mdi:phone-in-talk", "afterSalesNumber", EntityCategory.DIAGNOSTIC),
    ("video_manual", "视频说明书", "mdi:book-open-variant", "videoManualUrl", EntityCategory.DIAGNOSTIC),
    # v4.6.8: 「可用功能」与「云端能力探测」（同源 availableFeatures 位图、
    # 信息更全）重复，删除本实体；旧条目由 setup 清理段自动移除。
    # v4.6.9: 「已授权功能」（authedFeatures="A4"）——已知信息仅有字段名
    # （客户端字符串表），无编码映射与中文含义，无法中文化 → 删除；旧条目
    # 由 setup 清理段自动移除。
    ("specified_features", "指定功能", "mdi:star-check", ("featureData", "specifiedFeatures"), EntityCategory.DIAGNOSTIC),
]


def _service_field_value(payload: dict, path: str | tuple[str, ...]):
    """按路径取 ccfeatures data 字段值；路径为 tuple 时逐层下钻。"""
    if isinstance(path, str):
        return payload.get(path)
    cur: Any = payload
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


class FordPassServiceFieldSensor(SensorEntity):
    """ccfeatures 服务信息单字段实体（v4.6.4）。

    响应中救援/客服/售后电话、视频说明书、可用/已授权/指定功能各建一个
    独立传感器。字段有值才创建，无数据不建无用实体；authedFeatures /
    specifiedFeatures 编码含义逐车型确认前保留原文。
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator,
        key: str,
        name: str,
        icon: str,
        path: str | tuple[str, ...],
        category: str | None = None,
    ) -> None:
        self.coordinator = coordinator
        self._key = key  # v4.6.7: 用于「可用功能」中文解析分支
        self._attr_unique_id = f"{coordinator.uid_key}-{key}"
        self._attr_name = name
        self._attr_icon = icon
        self._attr_device_info = coordinator.device_info
        self._path = path
        if category:
            self._attr_entity_category = category

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)  # v5.3.1: 保留最后已知状态——刷新失败不置不可用（与 switch/lock/select/device_tracker 对齐）

    def _payload(self) -> dict | None:
        ccf = (self.coordinator.data or {}).get("ccfeatures")
        if not isinstance(ccf, dict):
            return None
        p = ccf.get("data")
        return p if isinstance(p, dict) else None

    @property
    def native_value(self) -> str:
        v = _service_field_value(self._payload() or {}, self._path)
        if v is None or v == "":
            return "暂无"
        # v5.0.2: 视频说明书 URL 太长（如 https://h5fya.fuyu.club/manual/xxx/
        # index.html）刷屏——状态显示短文案，完整 URL 移入 manual_url 属性，
        # 前端 Markdown 卡片引用即可点击跳转浏览器打开（见 README）。
        if self._key == "video_manual":
            return "查看视频说明书"
        return str(v)

    @property
    def extra_state_attributes(self) -> dict:
        attrs: dict = {}
        # v5.0.2: 完整 URL 入属性供前端链接使用
        if self._key == "video_manual":
            url = _service_field_value(self._payload() or {}, self._path)
            if url:
                attrs["manual_url"] = url
        return attrs


class FordPassOilLifeSensor(_ServiceInfoSensor):
    """机油寿命（v3.1.9）：prognostic.data.iolm（%）。

    v3.3.10: 值 = 百分比 + 归零年月份 + 剩余公里（如「40% · 归零 2028-03 ·
    剩余 12000 km」）；各字段仍同步进属性供自动化使用。
    """

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "prognostic", "机油寿命", "mdi:oil")
        # v4.9.0: 百分比数值单位（此前拼在 state 字符串里无法图表化）
        self._attr_native_unit_of_measurement = "%"
        # v5.4.6: 标记 measurement 启用长期统计（30 天机油寿命趋势）
        self._attr_state_class = SensorStateClass.MEASUREMENT

    def _format(self, data: dict) -> str | None:
        payload = data.get("data") if isinstance(data, dict) else None
        iolm = payload.get("iolm") if isinstance(payload, dict) else None
        # v4.0.2: prognostic 全 null（云端暂无该车预测数据）时，兜底读
        # vehicle-status oil.oilLifeActual（每次刷新都有），保证机油寿命
        # 实体永不 unknown；prognostic 有值仍优先（含归零月份/剩余公里）。
        if iolm is None:
            vs = (self.coordinator.data or {}).get("vehiclestatus")
            oil = vs.get("oil") if isinstance(vs, dict) else None
            if isinstance(oil, dict):
                iolm = oil.get("oilLifeActual")
        if iolm is None:
            return None
        # v4.9.0: 数值化——state 只放百分比数值 + unit(%),「归零/剩余」移入
        # 属性（_summary 的 zero_on/remaining_km），支持图表与数值比较。
        return str(int(iolm))

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
        # v5.4.6: 数值化（此前 state 为「123 km」文本无法统计/EVCC 读取）——
        # 纯数值 + km 单位 + measurement + distance，支持长期趋势图与 EVCC 消费
        self._attr_native_unit_of_measurement = "km"
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_device_class = SensorDeviceClass.DISTANCE

    def _format(self, data: dict) -> str | None:
        payload = data.get("data") if isinstance(data, dict) else None
        km = payload.get("remainingKMs") if isinstance(payload, dict) else None
        if km is None:
            return None
        return str(int(km))

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

    _attr_entity_category = EntityCategory.DIAGNOSTIC

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
        # v4.6.5: 主值 = 当前总里程对应的下一保养档（用户要求）——在保养
        # 计划档位里找里程 >= 当前总里程的最近一档，显示该档全部项目；
        # 无总里程数据时退回首档全量显示；所有档位都已低于总里程时显示
        # 最后档并标注。项目/档数展示沿用 v3.3.0（完整档 + 档数）。
        payload = data.get("data") if isinstance(data, dict) else None
        items = payload.get("values") if isinstance(payload, dict) else None
        if not isinstance(items, list) or not items:
            return None

        def _ops_of(it: dict) -> list[str]:
            out = []
            for o in (it.get("operations") or []):
                if not isinstance(o, dict):
                    continue
                # v4.9.0: 清洗云端原文残留（前导空格 / "6个月^)" 的 ^）
                d = str(o.get("description") or "").strip().replace("^", "")
                if d:
                    out.append(d)
            return out

        def _mileage(it: dict) -> int | None:
            m = it.get("mileage")
            if m is None:
                return None
            try:
                return int(m)
            except (TypeError, ValueError):
                return None

        odo = self._odometer()
        picked: tuple[int | None, dict] | None = None
        last: tuple[int | None, dict] | None = None
        for it in items:
            if not isinstance(it, dict):
                continue
            ops = _ops_of(it)
            if not ops:
                continue
            m = _mileage(it)
            last = (m, it)
            if m is not None and odo is not None and m >= odo:
                picked = (m, it)
                break
        if picked is None:
            # 无总里程 → 首档；总里程已超全部档 → 最后档并标注
            if last is not None:
                m, it = last
                ops = _ops_of(it)
                head = f"{m} 公里" if m is not None else "保养档"
                if odo is not None:
                    first = f"总里程 {odo} 公里已超保养计划，最后档 {head}：{'、'.join(ops)}"
                else:
                    first = f"{head}：{'、'.join(ops)}"
            else:
                return None
        else:
            m, it = picked
            ops = _ops_of(it)
            first = f"总里程 {odo} 公里 · 下一档 {m} 公里：{'、'.join(ops)}"
        if len(items) > 1:
            first += f"（共 {len(items)} 档）"
        # HA state 有 255 字符上限；项目多时截断加省略号
        if len(first) > 230:
            first = first[:227] + "…"
        return first

    def _odometer(self) -> int | None:
        """当前总里程（vehiclestatus.odometer，可能为 {value:...} 节点）。"""
        vs = (self.coordinator.data or {}).get("vehiclestatus")
        if not isinstance(vs, dict):
            return None
        node = vs.get("odometer")
        v = (node or {}).get("value") if isinstance(node, dict) else node
        if v is None:
            return None
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None

    def _summary(self, data: dict) -> dict:
        payload = data.get("data") if isinstance(data, dict) else None
        items = payload.get("values") if isinstance(payload, dict) else None
        if not isinstance(items, list) or not items:
            return {}
        plans = []
        for it in items[:3]:  # v4.9.0: 只保留最近 3 档（当前/下一档最有价值），
            # 防 attributes 膨胀（此前 6 档×9 项每轮重写几十 KB）
            if not isinstance(it, dict):
                continue
            ops_desc = []
            for o in (it.get("operations") or []):
                if not isinstance(o, dict):
                    continue
                d = str(o.get("description") or "").strip().replace("^", "")
                if d:
                    ops_desc.append(d)
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

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "sim_info", "SIM 卡", "mdi:sim")

    def _format(self, data: dict) -> str | None:
        if not isinstance(data, dict) or not data:
            # v4.3.2: sim_info 对未开通车型（纯油）确定性业务拒绝落盘
            # None/空——按 v3.3.0 语义显示「车辆未开通该服务」而非 unknown
            return "车辆未开通该服务"
        payload = data.get("data") if isinstance(data.get("data"), dict) else {}
        # 服务端业务失败（实测 payload.returnSuccess false / CONS.SYS.0002）：
        # 纯油车型无车联网 SIM 服务，App 无该功能入口（实机样本全量 1152 条
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
                # v5.2.4: phoneNumber 掩码（后四位）——完整手机号会进实体
                # attributes 与 recorder/logbook
                keep[k] = _mask_phone(info[k]) if k == "phoneNumber" else info[k]
        return keep


class FordPassWifiSensor(_ServiceInfoSensor):
    """WiFi 热点状态（v3.1.4）：GET /api/cnxapi-cds/v1/vehicle/wifi/status。"""

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "wifi_status", "WiFi 热点", "mdi:wifi")

    def _format(self, data: dict) -> str | None:
        if not isinstance(data, dict):
            return None
        payload = data.get("data") if isinstance(data.get("data"), dict) else {}
        # v3.3.0: 与 SIM 一致——纯油车型无车联网热点服务，业务失败（CONS.AUTH.0005
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

   实机样本（2026-10-03）实测：data.summary.{allRedDotStatus, unReadCategoryId,
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

    _attr_has_entity_name = True
    _attr_icon = "mdi:cloud-check"
    # v5.9.4: 同上——只读回读值归诊断类目，可写的配置项在 select 平台。
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.uid_key}-chirp_cloud"
        self._attr_name = "鸣笛设置云端状态"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)  # v5.3.1: 保留最后已知状态——刷新失败不置不可用（与 switch/lock/select/device_tracker 对齐）

    @property
    def native_value(self) -> str | None:
        pref = (self.coordinator.data or {}).get("chirp_cloud") or {}
        if not isinstance(pref, dict) or not pref:
            # v3.9.2: 云端数据未获取时显示「未获取」（不显示 unknown/
            # unavailable——保留实体可读状态）
            return "未获取"
        # v3.2.2: 优先 App 官方槽位 AnnouncementType/Duration（实机样本实证），
        # 旧槽位 vehicleAnnouncementSoundType/vehicleAnnouncementDuration
        # 仅兜底（历史残留、App 新逻辑不读）。
        sound = pref.get("AnnouncementType") or pref.get("vehicleAnnouncementSoundType")
        duration = pref.get("Duration") or pref.get("vehicleAnnouncementDuration")
        if sound is None and duration is None:
            return "未获取"
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
        # v4.9.0: 不再暴露云端原始字段（AnnouncementType/Duration/
        # vehicleAnnouncement* 重复且新老槽位数值矛盾）——只留解析后
        # 的中文类型与显示时长，与 native_value 一致。
        pref = (self.coordinator.data or {}).get("chirp_cloud") or {}
        attrs: dict[str, Any] = {}
        if isinstance(pref, dict) and pref:
            sound = pref.get("AnnouncementType") or pref.get("vehicleAnnouncementSoundType")
            duration = pref.get("Duration") or pref.get("vehicleAnnouncementDuration")
            type_cn = next(
                (cn for cn, en in CHIRP_TO_ANNOUNCE.items() if en == str(sound)),
                None,
            )
            if type_cn is None:
                type_cn = ANNOUNCE_ENUM_CN.get(str(sound), str(sound))
            attrs["type"] = type_cn
            if duration is not None:
                attrs["duration"] = f"{duration} 秒"
        return attrs


class FordPassAnnounceStatusSensor(SensorEntity):
    """鸣笛命令状态（v3.1.13）：最近一次鸣笛的 announcestatus 结果。

    鸣笛开关触发后轮询 GET /api/vehicles/v5/{vin}/announcestatus/{commandId}/
    （App FordRemoteControlApiService v5 命令轮询组）写入
    coordinator.announce_status。未触发/查询失败均显示明确中文状态，
    永远可用（v3.1.2 规则，不显示 unavailable）。
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:bullhorn-outline"

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.uid_key}-announce_status"
        self._attr_name = "鸣笛命令状态"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)  # v5.3.1: 保留最后已知状态——刷新失败不置不可用（与 switch/lock/select/device_tracker 对齐）

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


def _first_list(node):
    """递归找第一个「元素全部为 dict 的列表」（v5.4.9，通用业务记录列表
    提取——消息/保养/订单/共享/充电站等探测响应容器形态多样，深度优先
    取第一个业务记录列表；纯数值/字符串数组跳过）。"""
    if isinstance(node, dict):
        for v in node.values():
            r = _first_list(v)
            if r is not None:
                return r
    elif isinstance(node, list):
        if node and all(isinstance(x, dict) for x in node):
            return node
        for item in node:
            r = _first_list(item)
            if r is not None:
                return r
    return None


def _detail_entries(records: list, fields: dict, n: int = 3) -> list:
    """v5.4.9: 最近 n 条记录逐条结构化——fields: {标签: [候选key...]}，
    宽松匹配（_swb_find）；提取不到关键字段保留 raw 兜底防丢数据。"""
    out: list = []
    for rec in reversed(records[-n:]):
        if not isinstance(rec, dict):
            out.append({"raw": rec})
            continue
        entry: dict = {}
        for label, keys in fields.items():
            for k in keys:
                v = _swb_find(rec, k)
                if v is not None:
                    entry[label] = v
                    break
        if not entry:
            entry = {"raw": json.dumps(rec, ensure_ascii=False)}
        out.append(entry)
    return out


def _swb_list(node):
    """递归找第一个充电桩（wallbox/charger）对象列表（v3.4.5）。"""
    if isinstance(node, dict):
        for v in node.values():
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


class FordPassLastChargeSensor(SensorEntity):
    """上次充电电量（v5.4.7，充电统计——smartwallbox 充电记录含真实电量
    字段 batteryLevel/soc/chargePercent；福特中国 API 无行程(trip)数据源，
    行程统计暂不做）。BATTERY + measurement，可进长期趋势图。"""

    _attr_has_entity_name = True
    _attr_icon = "mdi:battery-charging"
    _attr_native_unit_of_measurement = "%"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.uid_key}-last_charge_soc"
        self._attr_name = "上次充电电量"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)  # 保留最后已知状态

    @property
    def native_value(self) -> int | None:
        swb = self.coordinator.smartwallbox
        if not isinstance(swb, dict):
            return None
        pct = (_swb_find(swb, "batteryLevel") or _swb_find(swb, "soc")
               or _swb_find(swb, "chargePercent"))
        if pct is None:
            return None
        try:
            return round(float(pct))
        except (TypeError, ValueError):
            return None


class FordPassSyncHealthSensor(SensorEntity):
    """云端同步状态（v5.4.7，后端变更监控）——复用每轮轮询结果，零额外
    请求：coordinator 成功/失败打点，连续失败转「异常」，属性带失败摘要
    与分类信息，用户可接自动化告警（限流/安全防护/网关/业务分类已在 api 日志）。
    诊断实体恒可用。"""

    _attr_has_entity_name = True
    _attr_icon = "mdi:cloud-sync"
    # v5.9.2: 集成自身健康度，标准诊断实体。
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.uid_key}-sync_health"
        self._attr_name = "云端同步状态"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        return True  # v5.4.7: 诊断实体恒可用

    @property
    def native_value(self) -> str:
        if getattr(self.coordinator, "_consecutive_failures", 0) == 0:
            return "正常"
        return "异常"

    @property
    def extra_state_attributes(self) -> dict:
        c = self.coordinator
        attrs: dict = {}
        st = getattr(c, "_last_success_time", None)
        et = getattr(c, "_last_error_time", None)
        if st is not None:
            attrs["last_success"] = st.isoformat()
        if et is not None:
            attrs["last_error"] = et.isoformat()
        attrs["consecutive_failures"] = getattr(c, "_consecutive_failures", 0)
        det = getattr(c, "_last_error_detail", None)
        if det:
            attrs["last_error_detail"] = det
        return attrs


class FordPassSmartWallboxSensor(SensorEntity):
    """家充桩管理（smartwallbox）摘要传感器（v3.4.5）。

    数据源 coordinator.smartwallbox（api.probe_smartwallbox 探测结果，
    登录后一次 + 24h TTL 缓存，见 coordinator.async_probe_capabilities）。
    探测成功才创建（非家充桩车型探测无数据 → 0 实体）；字段宽松提取，
    缺失显示友好中文；永远可用（v3.1.2 规则，不显示 unavailable）。
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:ev-station"

    def __init__(self, coordinator, key: str, label: str, kind: str) -> None:
        self.coordinator = coordinator
        self._key = key
        self._kind = kind
        self._attr_unique_id = f"{coordinator.uid_key}-{key}-{kind}"
        self._attr_name = label
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)  # v5.3.1: 保留最后已知状态——刷新失败不置不可用（与 switch/lock/select/device_tracker 对齐）

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
            # v5.2.4: 敏感字段过滤（手机号/用户ID 等不进 attributes）
            attrs = _safe_scalar_attrs(data)
        return attrs


class FordPassFposbSensor(SensorEntity):
    """fposb 4S 维保预约摘要传感器（v4.3.3）。

    数据源 coordinator.fposb（api.probe_fposb 探测结果，登录后一次 +
    TTL 持久化缓存）。探测到数据才创建（无维保服务车型 → 0 实体）；
    宽松字段提取，缺失显示友好中文；永远可用（v3.1.2 规则）。
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:garage"

    def __init__(self, coordinator, key: str, label: str, kind: str) -> None:
        self.coordinator = coordinator
        self._key = key
        self._kind = kind
        self._attr_unique_id = f"{coordinator.uid_key}-fposb-{key}-{kind}"
        self._attr_name = label
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)  # v5.3.1: 保留最后已知状态——刷新失败不置不可用（与 switch/lock/select/device_tracker 对齐）

    def _probe(self) -> dict | None:
        fp = self.coordinator.fposb or {}
        ent = fp.get(self._key)
        if isinstance(ent, dict):
            data = ent.get("data")
            if isinstance(data, dict):
                return data
        return None

    def _booking_list(self) -> list[dict]:
        data = self._probe()
        if data is None:
            return []
        lst = data.get("data") or data.get("list") or data.get("bookingList")
        if isinstance(lst, list):
            return [b for b in lst if isinstance(b, dict)]
        if isinstance(data, dict):
            for k in ("records", "osbList", "result"):
                v = data.get(k)
                if isinstance(v, list):
                    return [b for b in v if isinstance(b, dict)]
        return []

    @property
    def native_value(self) -> str | None:
        books = self._booking_list()
        if self._kind == "count":
            return f"{len(books)} 条预约" if books else "无预约记录"
        if not books:
            return "无预约记录"
        b = books[0]
        for key in ("bookingType", "serviceType", "type", "itemName", "serviceName"):
            if b.get(key):
                return str(b[key])
        return "有预约记录"

    @property
    def extra_state_attributes(self) -> dict:
        attrs: dict = {}
        books = self._booking_list()
        if books and isinstance(books[0], dict):
            # v5.2.4: 敏感字段过滤（预约记录可能含手机号/用户ID）
            attrs = _safe_scalar_attrs(books[0], limit=10)
        attrs["count"] = len(books)
        return attrs


class FordPassEvssSensor(SensorEntity):
    """evss 充电站/免密支付摘要传感器（v4.3.3，仅电/插混/混动车型创建）。

    数据源 coordinator.evss（api.probe_evss 探测结果，登录后一次 + TTL
    持久化缓存；纯油/柴油车型探测跳过 → 0 实体）。探测到数据才创建；
    永远可用（v3.1.2 规则）。
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:ev-station"

    def __init__(self, coordinator, key: str, label: str, kind: str) -> None:
        self.coordinator = coordinator
        self._key = key
        self._kind = kind
        self._attr_unique_id = f"{coordinator.uid_key}-evss-{key}-{kind}"
        self._attr_name = label
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)  # v5.3.1: 保留最后已知状态——刷新失败不置不可用（与 switch/lock/select/device_tracker 对齐）

    def _probe(self) -> dict | None:
        ev = self.coordinator.evss or {}
        ent = ev.get(self._key)
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
            if self._kind == "station_count":
                lst = data.get("data") or data.get("list") or data.get("chargers")
                if isinstance(lst, list):
                    return f"{len(lst)} 个充电站" if lst else "无充电站数据"
                total = data.get("total") or data.get("totalCount")
                if total is not None:
                    return f"{total} 个充电站"
                return "充电站数据已连接"
            if self._kind == "freepass":
                st = data.get("status") or data.get("data")
                if isinstance(st, dict):
                    on = st.get("enable") or st.get("enabled") or st.get("open")
                    if on is not None:
                        return "已开通" if str(on).lower() in ("1", "true", "yes", "on") else "未开通"
                    return "已连接"
                if st is not None:
                    return str(st)
                return "已连接"
            if self._kind == "order":
                lst = data.get("data") or data.get("list") or data.get("orders")
                if isinstance(lst, list) and lst:
                    o = lst[0] if isinstance(lst[0], dict) else {}
                    for key in ("orderStatus", "status", "stationName", "orderNo"):
                        if o.get(key):
                            return str(o[key])
                    return "最近充电订单已连接"
                return "无充电订单"
            return "已连接"
        except Exception:  # noqa: BLE001
            return None

    @property
    def extra_state_attributes(self) -> dict:
        attrs: dict = {}
        data = self._probe()
        if isinstance(data, dict):
            # v5.2.4: 敏感字段过滤（手机号/用户ID 等不进 attributes）
            attrs = _safe_scalar_attrs(data)
        return attrs


class FordPassCevsDepartureSensor(SensorEntity):
    """预约出发状态传感器（v3.7.24，cevs 域）。

    数据源 coordinator.cevs_probe["departuretimes"]（探测到数据才创建）：
    isEnabled（On/Off）+ goTimesScheduleCloudData.calendarDaysList（日历
    计划）+ syncStatus（同步状态）。非 cevs 车型/端点失败不创建。
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.uid_key}-cevs-departure"
        self._attr_name = "预约出发"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)  # v5.3.1: 保留最后已知状态——刷新失败不置不可用（与 switch/lock/select/device_tracker 对齐）

    @property
    def native_value(self) -> str:
        data = self.coordinator.cevs_probe.get("departuretimes") or {}
        enabled = str(data.get("isEnabled", "")).lower() == "on"
        return "已启用" if enabled else "未启用"

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.cevs_probe.get("departuretimes") or {}
        attr: dict = {}
        cloud = data.get("goTimesScheduleCloudData") or {}
        days = cloud.get("calendarDaysList") or []
        attr["同步状态"] = data.get("syncStatus")
        attr["计划条数"] = len(days)
        attr["isEnabled"] = data.get("isEnabled")
        if days:
            attr["日历计划"] = json.dumps(days, ensure_ascii=False)
        return attr


class FordPassCevsChargelogSensor(SensorEntity):
    """充电记录传感器（v3.7.24，cevs 域）。

    数据源 coordinator.cevs_probe["chargelogs"]：chargeLogs 列表 + 下一条
    充电时间 nextChrgTimestamp。非 cevs 车型/端点失败不创建。
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:battery-charging"

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.uid_key}-cevs-chargelogs"
        self._attr_name = "充电记录"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)  # v5.3.1: 保留最后已知状态——刷新失败不置不可用（与 switch/lock/select/device_tracker 对齐）

    @property
    def native_value(self) -> str:
        data = self.coordinator.cevs_probe.get("chargelogs") or {}
        logs = data.get("chargeLogs") or []
        return f"{len(logs)} 条"

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.cevs_probe.get("chargelogs") or {}
        logs = data.get("chargeLogs") or []
        attr: dict = {
            "记录条数": len(logs),
            "下次充电时间": data.get("nextChrgTimestamp"),
        }
        # v5.4.8: 充电日志明细——最近 3 条逐条结构化（时间/电量/时长宽松
        # 提取，字段名各车型有差异；提取不到保留原始记录，不丢数据）。
        # 配合 v5.4.7「上次充电电量」形成充电统计闭环，HA 里直接可读，
        # 不必再看 JSON。
        if logs:
            detail = []
            for rec in reversed(logs[-3:]):
                if not isinstance(rec, dict):
                    detail.append({"raw": rec})
                    continue
                t = (_swb_find(rec, "startTime") or _swb_find(rec, "endTime")
                     or _swb_find(rec, "chargingStartTime")
                     or _swb_find(rec, "chargingEndTime")
                     or _swb_find(rec, "date") or _swb_find(rec, "time")
                     or _swb_find(rec, "chargingDate"))
                soc = (_swb_find(rec, "batteryLevel") or _swb_find(rec, "soc")
                       or _swb_find(rec, "chargePercent")
                       or _swb_find(rec, "endSoc"))
                dur = (_swb_find(rec, "chargeDuration") or _swb_find(rec, "duration")
                       or _swb_find(rec, "chargingTime") or _swb_find(rec, "chargeTime"))
                entry: dict = {}
                if t is not None:
                    entry["time"] = t
                if soc is not None:
                    entry["soc"] = soc
                if dur is not None:
                    entry["duration"] = dur
                # raw 兜底：宽松提取未命中关键字段时保留原始记录防丢数据
                if not entry:
                    entry = {"raw": json.dumps(rec, ensure_ascii=False)}
                detail.append(entry)
            attr["最近记录"] = detail
            # 兼容旧格式：最近 1 条原始 JSON（原属性名保留，避免破坏现有自动化）
            attr["最近记录原始"] = json.dumps(logs[-1], ensure_ascii=False)
        return attr


class FordPassProbeSensor(SensorEntity):
    """只读探测端点摘要传感器（v3.4.6，探测数据驱动创建）。

    数据源 coordinator.extra_probes = api.probe_extra_endpoints()（登录后
    一次 + 24h TTL 缓存，全部只读/幂等 GET）：OTA 版本/详情/新状态、
    预约出发（departuretimes/retrieve）、充电日志（chargelogs/retrieve）。
    探测到数据才创建；字段宽松递归提取（_swb_find），缺失显示友好中文；
    永远可用（v3.1.2 规则，不显示 unavailable）。
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:update"
    # v5.9.2: 探测端点原始摘要，属排查用诊断数据。
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, key: str, label: str, kind: str) -> None:
        self.coordinator = coordinator
        self._key = key
        self._kind = kind
        self._attr_unique_id = f"{coordinator.uid_key}-{key}"
        self._attr_name = label
        self._attr_device_info = coordinator.device_info
        if kind in ("departure", "chargelog"):
            self._attr_icon = "mdi:calendar-clock"

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)  # v5.3.1: 保留最后已知状态——刷新失败不置不可用（与 switch/lock/select/device_tracker 对齐）

    def _data(self) -> dict | None:
        ep = self.coordinator.extra_probes or {}
        data = ep.get(self._key)
        return data if isinstance(data, dict) else None

    @property
    def native_value(self) -> str | None:
        data = self._data()
        if data is None and self._kind == "uservehicles":
            # v3.6.7: uservehicles 是注册端点（rejected 不在 extra_probes），
            # 创建/刷新都以 coordinator.vehicles_list（v5/vehicles/list）为准——
            # 否则 _data() 恒 None → 状态永远 unknown。
            vl = getattr(self.coordinator, "vehicles_list", None) or []
            if vl:
                data = {"list": vl}
        if data is None:
            # v4.6.6: 车型主数据探测端点无数据时也用车辆信息（vehicles/list
            # 明文）作主值——用户要求主值显示车型信息而非「暂无数据」。
            if self._kind == "vehicle_model_masterdata":
                zh = _vehicle_masterdata_zh(self.coordinator.vehicle_info)
                if zh:
                    return zh
            # v4.3.7: v4.3.5/v4.3.6 无条件创建的探测端点实体，探测无数据
            # （None/拒绝）时显示「暂无数据」友好文案——实体常驻、永不
            # unknown/unavailable（用户硬性要求，v4.3.2 SIM 先例）。
            if self._kind in ("wallbox_bound", "wallbox_info", "wallbox_records_v2",
                              "evss_order_status", "evss_apps_info", "evss_usage_notice",
                              "vpoi_status", "vpoi_saved", "ota_agreements",
                              "share_capability", "share_history",
                              "evss_payment_summary", "evss_invoiceables",
                              "evss_events_v3", "evss_evaluate_url",
                              "wallbox_ble_vehicles", "app_init_settings",
                              "vehicle_model_masterdata", "user_login_status"):
                return "暂无数据"
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
            # ---- v3.5.0: 6.16.0 补全端点（探测数据驱动，全车型适配）----
            if self._kind == "online":
                # v3.5.5: onlinernr 响应含 data.rnrStatusDesc/rnrStatusCode
                # （"已通过"= 车辆在线可达）——优先取 rnrStatusCode 映射
                # "在线/离线"，rnrStatusDesc 文字作回退
                rnr_code = _swb_find(data, "rnrStatusCode")
                if rnr_code is not None:
                    return {"2": "在线", "1": "离线", "0": "未知"}.get(
                        str(rnr_code), str(rnr_code))
                rnr = _swb_find(data, "rnrStatusDesc")
                if rnr is not None:
                    return str(rnr)
                st = (_swb_find(data, "onLineState") or _swb_find(data, "online")
                      or _swb_find(data, "status"))
                if st is not None:
                    if str(st) == "200":
                        return "在线"  # v3.5.4: 响应仅 status:200 = 车辆在线可达
                    return {"1": "在线", "0": "离线", "true": "在线", "false": "离线",
                            "ONLINE": "在线", "OFFLINE": "离线"}.get(str(st), str(st))
                return "未知"
            if self._kind == "model3d":
                u = (_swb_find(data, "modelUrl") or _swb_find(data, "url")
                     or _swb_find(data, "threeDModelUrl"))
                if u is not None:
                    return str(u)
                return "暂无 3D 车型图"
            if self._kind == "cmcc":
                # v4.2.6: 移动流量商城 H5 地址（CDS 域只读探测，字段名按
                # 多候选 key 提取；探测有 URL 才创建）
                u = (_swb_find(data, "url") or _swb_find(data, "h5Url")
                     or _swb_find(data, "trafficUrl") or _swb_find(data, "cmccUrl"))
                if u is not None:
                    return str(u)
                return "暂无流量商城"
            if self._kind == "schedule":
                lst = _swb_list(data)
                if lst:
                    first = lst[0] if isinstance(lst[0], dict) else {}
                    t = (_swb_find(first, "departureTime") or _swb_find(first, "startTime")
                         or _swb_find(first, "scheduleTime"))
                    if t is not None:
                        return f"{len(lst)} 个预约行程（最近 {t}）"
                    return f"{len(lst)} 个预约行程"
                t = (_swb_find(data, "departureTime") or _swb_find(data, "nextDeparture"))
                if t is not None:
                    return str(t)
                return "无预约行程"
            if self._kind == "device":
                d = _swb_find(data, "deviceId") or _swb_find(data, "id")
                tp = _swb_find(data, "deviceType") or _swb_find(data, "type")
                if d is not None or tp is not None:
                    return " / ".join(str(x) for x in (tp, d) if x)
                return "暂无设备信息"
            if self._kind == "auth":
                # v3.6.7: user_auth 响应字段是 authorizationStatus（"Authorized"），
                # 原解析找 status 拿到外层 HTTP 200——优先 authorizationStatus。
                st = (_swb_find(data, "authorizationStatus") or _swb_find(data, "authStatus")
                      or _swb_find(data, "status"))
                if st is not None:
                    return {"1": "已授权", "0": "未授权", "true": "已授权",
                            "false": "未授权", "AUTHORIZED": "已授权",
                            "UNAUTHORIZED": "未授权", "Authorized": "已授权",
                            "Unauthorized": "未授权"}.get(str(st), str(st))
                return "未知"
            if self._kind == "srs":
                txt = (_swb_find(data, "srsProfile") or _swb_find(data, "profile")
                       or _swb_find(data, "srsProfileName"))
                if txt is not None:
                    return str(txt)
                lst = _swb_list(data)
                if lst:
                    return f"{len(lst)} 条安全档案"
                return "暂无安全档案"
            if self._kind == "shadow":
                lst = _swb_list(data)
                if lst:
                    return f"{len(lst)} 项传感器状态"
                return "暂无传感器影子数据"
            if self._kind == "video":
                u = (_swb_find(data, "videoFileUrl") or _swb_find(data, "fileUrl")
                     or _swb_find(data, "videoUrl"))
                if u is not None:
                    return "最近录像可查看"
                return "暂无行车记录视频"
            if self._kind == "messages":
                lst = _swb_list(data)
                if lst:
                    first = lst[0] if isinstance(lst[0], dict) else {}
                    subj = (_swb_find(first, "subject") or _swb_find(first, "title")
                            or _swb_find(first, "content"))
                    if subj is not None:
                        return str(subj)
                    return f"{len(lst)} 条消息"
                return "无消息"
            if self._kind == "ota_reddot":
                st = (_swb_find(data, "redDotStatus") or _swb_find(data, "status")
                      or _swb_find(data, "allRedDotStatus"))
                if st is not None:
                    try:
                        return "有 OTA 更新" if int(st) != 0 else "无 OTA 更新"
                    except (TypeError, ValueError):
                        return str(st)
                return "未知"
            # ---- v3.5.1: 账号级只读端点（探测数据驱动，全车型适配）----
            if self._kind == "uservehicles":
                # v3.6.2: uservehicles 实为车辆注册端点（"Vehicle already
                # registered"），列表以 coordinator.vehicles_list（v5/vehicles/list，
                # 已验证 live）为准；probe 数据仅作兜底。
                lst = _swb_list(data)
                vl = getattr(self.coordinator, "vehicles_list", None) or []
                if not lst and isinstance(vl, list) and vl:
                    lst = vl
                if lst:
                    models = []
                    for v in lst:
                        if not isinstance(v, dict):
                            continue
                        m = (_swb_find(v, "localMarketValue") or _swb_find(v, "vehicleModelName")
                             or _swb_find(v, "modelName")
                             or _swb_find(v, "model") or _swb_find(v, "vehicleName"))
                        vin = _swb_find(v, "vin") or _swb_find(v, "encryptedVin")
                        if m is not None:
                            models.append(str(m))
                        elif vin is not None:
                            models.append(str(vin)[-6:])
                    if models:
                        return f"{len(lst)} 辆车：{'、'.join(list(dict.fromkeys(models))[:6])}"
                    return f"{len(lst)} 辆车"
                return "无车辆清单数据"
            if self._kind == "messages_v2":
                lst = _swb_list(data)
                if lst:
                    first = lst[0] if isinstance(lst[0], dict) else {}
                    subj = (_swb_find(first, "readMessageSubject")
                            or _swb_find(first, "subject") or _swb_find(first, "title")
                            or _swb_find(first, "content"))
                    if subj is not None:
                        return str(subj)
                    return f"{len(lst)} 条消息"
                return "无消息"
            if self._kind == "share_list":
                lst = _swb_list(data)
                if lst:
                    active = 0
                    for s in lst:
                        if not isinstance(s, dict):
                            continue
                        stt = (_swb_find(s, "status") or _swb_find(s, "shareStatus"))
                        if stt is not None and str(stt) in ("1", "ACTIVE", "active", "有效"):
                            active += 1
                    return f"{len(lst)} 个共享（{active} 个有效）"
                return "无车辆共享"
            if self._kind == "device_list":
                lst = _swb_list(data)
                if lst:
                    names = []
                    for d in lst:
                        if not isinstance(d, dict):
                            continue
                        nm = (_swb_find(d, "deviceName") or _swb_find(d, "name")
                              or _swb_find(d, "deviceType"))
                        if nm is not None:
                            names.append(str(nm))
                    if names:
                        return f"{len(lst)} 个设备：{'、'.join(list(dict.fromkeys(names))[:6])}"
                    return f"{len(lst)} 个设备"
                return "暂无设备列表"
            # v3.7.1: 保养历史（maintenance-history，账号认证车主后可探测）。
            # 响应结构各车型有差异（list / {data:[...]} / {records:[...]}），
            # 通用解析：递归取第一条记录摘要 + 总条数；真实结构在
            # FordPass probe 日志输出后下版精解析对齐。
            if self._kind == "maintenance":
                lst = _swb_list(data)
                if lst is None:
                    for _v in data.values():
                        if isinstance(_v, list) and _v:
                            lst = _v
                            break
                if lst:
                    first = lst[0] if isinstance(lst[0], dict) else {}
                    when = (_swb_find(first, "maintenanceDate") or _swb_find(first, "date")
                            or _swb_find(first, "serviceDate") or _swb_find(first, "time"))
                    what = (_swb_find(first, "description") or _swb_find(first, "item")
                            or _swb_find(first, "taskName") or _swb_find(first, "content")
                            or _swb_find(first, "workItem"))
                    parts = [str(x) for x in (when, what) if x]
                    if parts:
                        return f"{len(lst)} 条记录，最近：{' '.join(parts)}"
                    return f"{len(lst)} 条保养记录"
                return "暂无保养记录"
            # v3.7.2: 车辆授权列表（vehicle-user-auth，授权用户/审批状态）
            if self._kind == "auth_list":
                lst = _swb_list(data)
                if lst:
                    users = []
                    for u in lst:
                        if not isinstance(u, dict):
                            continue
                        # v5.2.4: 优先昵称/用户名，手机号掩码，用户 ID 不外泄
                        # （state 会进 recorder/logbook）
                        nm = _safe_identity_name(u, _swb_find)
                        if nm is not None:
                            users.append(nm)
                    if users:
                        return f"{len(lst)} 个授权用户：{'、'.join(list(dict.fromkeys(users))[:6])}"
                    return f"{len(lst)} 个授权用户"
                st = _swb_find(data, "authorizationStatus") or _swb_find(data, "status")
                if st is not None:
                    return str(st)
                return "暂无授权用户"
            # v3.7.2: 公共充电探测（EVSS 充电站/订单、vpoi 充电站地图）——
            # 纯电/插混车型专属；位置相关无参探测失败→探测无数据不创建
            if self._kind in ("evss_stations", "vpoi_chargestations"):
                lst = _swb_list(data)
                if lst:
                    return f"{len(lst)} 个充电站"
                cnt = _swb_find(data, "total") or _swb_find(data, "count")
                if cnt is not None:
                    return f"{cnt} 个充电站"
                return "暂无充电站数据"
            # v5.8.4: 访客模式 v3 页面初始化信息（DTO 结构：DisplayProfile/
            # Tab/Feature）。字段名待 App 实测确认，先按通用 key 探测；
            # 主值取访客模式开关状态/配置标题，实测校准后细化。
            if self._kind == "visitor_mode":
                for _k in ("visitorModeStatus", "visitorMode", "status",
                           "enable", "enabled", "isOpen", "openStatus"):
                    _v = _swb_find(data, _k)
                    if _v is not None:
                        return str(_v)
                _profile = data.get("displayProfile") if isinstance(data, dict) else None
                if isinstance(_profile, dict):
                    for _k in ("title", "subTitle", "description"):
                        _v = _profile.get(_k)
                        if _v:
                            return str(_v)
                _lst = _swb_list(data)
                if _lst:
                    return f"{len(_lst)} 项访客配置"
                return "访客模式数据已连接"
            if self._kind == "evss_orders":
                lst = _swb_list(data)
                if lst:
                    return f"{len(lst)} 条充电订单"
                return "暂无充电订单"
            # v3.7.3: 家充桩扩展解析（分享列表/桩级充电状态/充电信息/充电订单）
            if self._kind == "wallbox_sharing":
                lst = _swb_list(data)
                if lst:
                    users = []
                    for u in lst:
                        if not isinstance(u, dict):
                            continue
                        # v5.2.4: 优先昵称/用户名，手机号掩码，用户 ID 不外泄
                        nm = _safe_identity_name(u, _swb_find)
                        if nm is not None:
                            users.append(nm)
                    if users:
                        return f"{len(lst)} 个共享用户：{'、'.join(list(dict.fromkeys(users))[:5])}"
                    return f"{len(lst)} 个共享用户"
                return "暂无分享"
            if self._kind == "wallbox_charge_status":
                st = (_swb_find(data, "chargingStatus") or _swb_find(data, "status")
                      or _swb_find(data, "chargingState") or _swb_find(data, "state"))
                if st is not None:
                    return str(st)
                lst = _swb_list(data)
                if lst:
                    return f"{len(lst)} 条充电状态"
                return "未知充电状态"
            if self._kind == "wallbox_charging":
                pct = (_swb_find(data, "batteryLevel") or _swb_find(data, "soc")
                       or _swb_find(data, "chargePercent"))
                if pct is not None:
                    tail = []
                    for k in ("remainingTime", "chargeTime", "power", "currentPower"):
                        v = _swb_find(data, k)
                        if v is not None:
                            tail.append(f"{k}={v}")
                    return f"电量 {pct}%" + ((" · " + "、".join(tail)) if tail else "")
                return "暂无充电信息"
            if self._kind == "wallbox_orders":
                lst = _swb_list(data)
                if lst:
                    return f"{len(lst)} 条充电订单"
                return "暂无充电订单"
            # ---- v3.6.2: 待开发清单补全（mcm 消息中心 v3 / 用户级红点）----
            if self._kind == "mcm_messages":
                lst = _swb_list(data)
                if lst:
                    first = lst[0] if isinstance(lst[0], dict) else {}
                    subj = (_swb_find(first, "readMessageSubject")
                            or _swb_find(first, "subject") or _swb_find(first, "title")
                            or _swb_find(first, "content"))
                    if subj is not None:
                        return str(subj)
                    return f"{len(lst)} 条消息"
                return "无消息"
            if self._kind == "user_reddot":
                st = (_swb_find(data, "redDotStatus") or _swb_find(data, "reddotStatus")
                      or _swb_find(data, "status") or _swb_find(data, "unreadCount"))
                if st is not None:
                    try:
                        return "有未读红点" if int(st) != 0 else "无红点"
                    except (TypeError, ValueError):
                        return str(st)
                # 无显式字段：递归找第一个整型字段（红点数）
                for _v in data.values():
                    if isinstance(_v, bool):
                        return "有未读红点" if _v else "无红点"
                for _v in data.values():
                    if isinstance(_v, int) and _v >= 0:
                        return f"{_v} 个红点"
                return "无红点"
            # ---- v4.3.5: 228 端点比对后未接入的只读端点补全 ----
            if self._kind == "wallbox_bound":
                lst = _swb_list(data)
                if lst:
                    names = []
                    for it in lst[:6]:
                        if isinstance(it, dict):
                            n = (_swb_find(it, "nickName") or _swb_find(it, "wallboxName")
                                 or _swb_find(it, "name") or _swb_find(it, "wallboxId"))
                            if n:
                                names.append(str(n))
                    return "、".join(names) if names else f"{len(lst)} 个已绑定墙盒"
                return "暂无绑定墙盒"
            if self._kind == "wallbox_info":
                n = (_swb_find(data, "wallboxName") or _swb_find(data, "name")
                     or _swb_find(data, "nickName") or _swb_find(data, "model"))
                if n is not None:
                    return str(n)
                lst = _swb_list(data)
                if lst:
                    return f"{len(lst)} 个墙盒详情"
                return "暂无墙盒详情"
            if self._kind == "wallbox_records_v2":
                lst = _swb_list(data)
                if lst:
                    return f"{len(lst)} 条充电记录"
                return "暂无充电记录"
            if self._kind == "evss_order_status":
                st = (_swb_find(data, "orderStatus") or _swb_find(data, "status")
                      or _swb_find(data, "chargingStatus"))
                if st is not None:
                    return {"CHARGING": "充电中", "FINISHED": "已完成", "CANCELED": "已取消",
                            "PENDING": "等待中", "0": "充电中", "1": "已完成"}.get(str(st), str(st))
                lst = _swb_list(data)
                if lst:
                    return f"{len(lst)} 条订单状态"
                return "暂无充电订单状态"
            if self._kind == "evss_apps_info":
                n = (_swb_find(data, "appName") or _swb_find(data, "name")
                     or _swb_find(data, "version"))
                if n is not None:
                    return str(n)
                return "EVSS 服务可用"
            if self._kind == "evss_usage_notice":
                lst = _swb_list(data)
                if lst:
                    return f"{len(lst)} 条使用通知"
                for _v in data.values():
                    if isinstance(_v, int) and _v >= 0:
                        return f"{_v} 条通知"
                return "暂无使用通知"
            if self._kind == "vpoi_status":
                st = (_swb_find(data, "status") or _swb_find(data, "chargeStatus")
                      or _swb_find(data, "state"))
                if st is not None:
                    return str(st)
                lst = _swb_list(data)
                if lst:
                    return f"{len(lst)} 个充电站状态"
                return "暂无充电站状态"
            if self._kind == "vpoi_saved":
                lst = _swb_list(data)
                if lst:
                    return f"收藏 {len(lst)} 个充电站"
                return "暂无收藏充电站"
            if self._kind == "ota_agreements":
                lst = _swb_list(data)
                if lst:
                    return f"{len(lst)} 份 OTA 协议"
                return "暂无 OTA 协议"
            if self._kind == "share_capability":
                st = (_swb_find(data, "shareCapability") or _swb_find(data, "capability")
                      or _swb_find(data, "supported") or _swb_find(data, "support"))
                if st is not None:
                    if isinstance(st, bool):
                        return "支持分享" if st else "不支持分享"
                    return {"1": "支持分享", "0": "不支持分享", "true": "支持分享",
                            "false": "不支持分享", "Y": "支持分享", "N": "不支持分享"}.get(
                        str(st), str(st))
                return "不支持车辆分享"
            if self._kind == "share_history":
                lst = _swb_list(data)
                if lst:
                    return f"{len(lst)} 条分享记录"
                return "暂无分享记录"
            # ---- v4.3.6: 136 未接入清单第二轮筛选补全 ----
            if self._kind == "evss_payment_summary":
                st = (_swb_find(data, "payStatus") or _swb_find(data, "status")
                      or _swb_find(data, "paymentStatus"))
                if st is not None:
                    return {"PAID": "已支付", "UNPAID": "未支付", "PENDING": "待支付",
                            "SUCCESS": "支付成功", "FAILED": "支付失败"}.get(str(st), str(st))
                amt = (_swb_find(data, "totalAmount") or _swb_find(data, "amount")
                       or _swb_find(data, "payAmount"))
                if amt is not None:
                    return f"应付 {amt}"
                return "暂无支付汇总"
            if self._kind == "evss_invoiceables":
                lst = _swb_list(data)
                if lst:
                    return f"{len(lst)} 笔可开票订单"
                return "暂无待开票订单"
            if self._kind == "evss_events_v3":
                lst = _swb_list(data)
                if lst:
                    return f"{len(lst)} 条充电事件"
                return "暂无充电事件"
            if self._kind == "evss_evaluate_url":
                u = (_swb_find(data, "evaluateUrl") or _swb_find(data, "url")
                     or _swb_find(data, "h5Url"))
                if u is not None:
                    return str(u)
                return "暂无评价链接"
            if self._kind == "wallbox_ble_vehicles":
                lst = _swb_list(data)
                if lst:
                    return f"{len(lst)} 辆 BLE 车辆"
                return "暂无 BLE 车辆信息"
            if self._kind == "app_init_settings":
                v = (_swb_find(data, "version") or _swb_find(data, "appVersion")
                     or _swb_find(data, "settingVersion"))
                if v is not None:
                    return str(v)
                lst = _swb_list(data)
                if lst:
                    return f"{len(lst)} 项设置"
                return "暂无应用设置"
            if self._kind == "vehicle_model_masterdata":
                # v4.6.5/v4.6.6: 车型主数据 = 车辆信息 11 字段中文聚合（用户
                # 要求）。coordinator.vehicle_info（vehicles/list 明文）优先，
                # 探测端点 modelName 兜底（data None 时已在上面分支先行处理）。
                zh = _vehicle_masterdata_zh(self.coordinator.vehicle_info)
                if zh:
                    return zh
                n = (_swb_find(data, "modelName") or _swb_find(data, "name")
                     or _swb_find(data, "vehicleModelName"))
                if n is not None:
                    return str(n)
                lst = _swb_list(data)
                if lst:
                    return f"{len(lst)} 款车型主数据"
                return "暂无车型主数据"
            if self._kind == "user_login_status":
                st = (_swb_find(data, "loginStatus") or _swb_find(data, "status")
                      or _swb_find(data, "loginState"))
                if st is not None:
                    return {"1": "已登录", "0": "未登录", "true": "已登录",
                            "false": "未登录", "LOGIN": "已登录", "LOGOUT": "未登录"}.get(
                        str(st), str(st))
                return "登录状态未知"
        except Exception:  # noqa: BLE001 - 解析失败不抛给 HA
            return "解析失败"
        return None

    @property
    def extra_state_attributes(self) -> dict:
        data = self._data()
        attrs: dict = {}
        if isinstance(data, dict):
            # v5.2.4: 敏感字段过滤（原始响应可能含手机号/用户ID）
            attrs = _safe_scalar_attrs(data)
        # v4.6.5: 车型主数据附 11 项车辆信息（中文标签 + 可翻译字段中文化，
        # 技术码保留原文供排查）。
        if self._kind == "vehicle_model_masterdata":
            vi = self.coordinator.vehicle_info or {}
            if vi:
                extra: dict[str, Any] = {
                    "燃料类型": _fuel_type_zh(vi.get("fuelType")),
                    "款型": f"{vi.get('modelYear')} 款" if vi.get("modelYear") else None,
                    "合资品牌": _joint_venture_zh(vi.get("jointVenture")),
                    "品牌": _brand_zh(vi.get("brandCode")),
                    "车型线功能码": vi.get("vehicleLineFeatureCode"),
                    "本地市场名称": vi.get("localMarketValue"),
                    "车机类型": vi.get("vehicleType"),
                    "版本": vi.get("version"),
                    "车辆云": vi.get("vehicleCloud"),
                    "平台标志": vi.get("plFlag"),
                    "保修起始": str(vi.get("warrantyStartDate") or "")[:10] or None,
                }
                attrs.update({k: v for k, v in extra.items() if v is not None and v != ""})
        # v5.4.9: 明细补全——消息/保养/共享/充电订单/家充桩记录/充电站
        # 探测到的列表数据逐条结构化（最近 3 条），HA 里直接可读不必查 JSON
        _detail_fields = {
            "messages": ("标题", ("subject", "title", "content", "messageTitle",
                                  "readMessageSubject")),
            "messages_v2": ("标题", ("subject", "title", "content", "messageTitle",
                                    "readMessageSubject")),
            "mcm_messages": ("标题", ("subject", "title", "content", "messageTitle",
                                      "readMessageSubject")),
            "maintenance": ("项目", ("description", "item", "taskName", "content",
                                     "workItem")),
            "share_list": ("对象", ("shareUserName", "nickName", "userName", "name")),
            "share_history": ("对象", ("shareUserName", "nickName", "userName", "name")),
            "evss_orders": ("订单号", ("orderId", "orderNo", "id", "eventId")),
            "evss_events_v3": ("事件", ("eventId", "id", "eventName", "title", "content")),
            "evss_invoiceables": ("订单号", ("orderId", "orderNo", "id", "invoiceId")),
            "evss_payment_summary": ("订单号", ("orderId", "orderNo", "id", "payId")),
            "wallbox_records_v2": ("电量", ("batteryLevel", "soc", "chargePercent", "power")),
            "evss_stations": ("站名", ("stationName", "name", "title", "poiName", "address")),
            "vpoi_chargestations": ("站名", ("stationName", "name", "title", "poiName", "address")),
        }
        _second_fields = {
            "messages": ("时间", ("sendTime", "publishTime", "createTime", "date", "time")),
            "messages_v2": ("时间", ("sendTime", "publishTime", "createTime", "date", "time")),
            "mcm_messages": ("时间", ("sendTime", "publishTime", "createTime", "date", "time")),
            "maintenance": ("日期", ("maintenanceDate", "date", "serviceDate", "time")),
            "share_list": ("状态", ("shareStatus", "status", "authorizationStatus", "state")),
            "share_history": ("时间", ("shareTime", "createTime", "date", "time")),
            "evss_orders": ("金额", ("amount", "price", "totalAmount", "payAmount")),
            "evss_events_v3": ("时间", ("eventTime", "createTime", "date", "time", "startTime")),
            "evss_invoiceables": ("金额", ("amount", "price", "totalAmount", "payAmount")),
            "evss_payment_summary": ("金额", ("amount", "price", "totalAmount", "payAmount")),
            "wallbox_records_v2": ("时间", ("startTime", "endTime", "date", "time", "chargingTime")),
            "evss_stations": ("距离", ("distance", "dist", "distanceMeter")),
            "vpoi_chargestations": ("距离", ("distance", "dist", "distanceMeter")),
        }
        if self._kind in _detail_fields:
            lst = _first_list(data)
            if lst:
                label1, keys1 = _detail_fields[self._kind]
                label2, keys2 = _second_fields[self._kind]
                entries = []
                for rec in reversed(lst[-3:]):
                    if not isinstance(rec, dict):
                        entries.append({"raw": rec})
                        continue
                    e: dict = {}
                    for k in keys1:
                        v = _swb_find(rec, k)
                        if v is not None:
                            e[label1] = v
                            break
                    for k in keys2:
                        v = _swb_find(rec, k)
                        if v is not None:
                            e[label2] = v
                            break
                    if not e:
                        e = {"raw": json.dumps(rec, ensure_ascii=False)}
                    entries.append(e)
                attrs["最近记录"] = entries
                attrs["记录条数"] = len(lst)
        return attrs

