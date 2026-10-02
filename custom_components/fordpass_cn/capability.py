"""全车型能力检测（v2.10.0）。

福特中国各车型的 vehicle-status 字段差异很大：纯电/插混有充电字段、
柴油有尿素字段、皮卡/拖车有双后轮与拖车检测、部分车型无远程控车
（crccFlag）。本模块提供数据驱动的"能力判定"——值为 null /
Not_Supported / 空 或不符合条件时，对应的传感器/按钮/开关实体不创建，
保证任何车型登录后只看到自己有真实数据与真实功能的能力。
"""
from __future__ import annotations

from typing import Any

# 无实际数据的占位值（与 sensor.py 保持一致）
_INVALID_VALUES = {"", "null", "none", "n/a", "not_supported", "notsupported",
                   "not available", "notavailable", "unknown"}


def leaf(status: dict | None, *keys: str, default: Any = None) -> Any:
    """沿路径取叶值（dict 节点取 value 字段）。"""
    cur = status
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    if isinstance(cur, dict):
        return cur.get("value", default)
    return cur


def leaf_node(status: dict | None, *keys: str) -> dict | None:
    """沿路径取 dict 节点本身（含 value/status/timestamp）。"""
    cur = status
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur if isinstance(cur, dict) else None


def first_leaf(status: dict | None, paths: list[list[str]], default: Any = None) -> Any:
    """多路径取第一个非 None 值。"""
    for p in paths:
        val = leaf(status, *p)
        if val is not None:
            return val
    return default


def usable(status: dict | None, paths: list[list[str]]) -> bool:
    """值是否有效（非 None / 无效占位串 / 非集合类型）。"""
    val = first_leaf(status, paths)
    if val is None:
        return False
    if isinstance(val, str):
        return val.strip().lower() not in _INVALID_VALUES
    if isinstance(val, (list, dict)):
        return False
    return True


def node_usable(status: dict | None, paths: list[list[str]]) -> bool:
    """路径指向的节点是否为有效 dict（节点存在即认为支持）。"""
    for p in paths:
        node = leaf_node(status, *p)
        if node is not None:
            return True
    return False


def is_on(status: dict | None, paths: list[list[str]]) -> bool:
    """值是否为开启态（1 / True / 'true' / 'ON'）。"""
    val = first_leaf(status, paths)
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val == 1
    if isinstance(val, str):
        return val.strip().lower() in {"1", "true", "on", "yes", "enabled"}
    return False


# ---------------------------------------------------------------------------
# v3.1.20: 云端能力探测（ccfeatures availableFeatures 位图 → VDSFeatureType）
# 全车型自动创建的关键——每辆车登录后按各自云端能力位图探测开通的服务能力，
# 位图不同 → 能力集不同 → 实体自动适配（如锐际纯油 03,04,05,06,07,08,10,
# 11,23 = 计划保养服务/指南/道路救援/延保/福特金融/私充服务/我的订阅/我的试驾
# + 未定义特性 0x23）。
# VDSFeatureType 枚举（App: mobile_cn_data_adapter vds_type.dart）——逆向自
# 福特派 6.16.0 libapp.so 对象池：off_8=枚举值, off_10=成员名。
# vdsFeatureList 在 App 中即为该全集（24 项），availableFeatures 按枚举值过滤。
# ---------------------------------------------------------------------------
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

# 能力 → 集成实体创建映射（全车型自动创建）：位图含该能力时，
# 对应服务实体可创建（vehicle-status 判断之外的第二层云端能力判定）。
CLOUD_FEATURE_ENTITIES: dict[int, tuple[str, str]] = {
    0x1: ("maintenance_plan", "保养计划"),
    0x3: ("maintenance_plan", "计划保养服务"),
}


def parse_cloud_features(ccfeatures_data: dict | None) -> list[dict[str, Any]]:
    """解析 ccfeatures 响应 → 已开通云端能力列表。

    每项 {id: int, en: str, zh: str, raw: str}；无 featureData /
    availableFeatures 为空 / 数据结构异常时返回 []（调用方不创建实体）。
    """
    if not isinstance(ccfeatures_data, dict):
        return []
    payload = ccfeatures_data.get("data")
    if not isinstance(payload, dict):
        return []
    fd = payload.get("featureData")
    if not isinstance(fd, dict):
        return []
    raw = fd.get("availableFeatures")
    if not isinstance(raw, str) or not raw.strip():
        return []
    out: list[dict[str, Any]] = []
    for part in raw.split(","):
        p = part.strip()
        if not p:
            continue
        try:
            fid = int(p, 16)
        except ValueError:
            if p.isdigit():
                fid = int(p, 10)
            else:
                continue
        en, zh = VDS_FEATURE_NAMES.get(fid, ("unknown", "未定义特性"))
        out.append({"id": fid, "en": en, "zh": zh, "raw": p})
    return out
