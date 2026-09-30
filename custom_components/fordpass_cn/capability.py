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
