# -*- coding: utf-8 -*-
"""能力位图解析单测——v4.9.7 新增。

覆盖：真实 availableFeatures 位图 → 中文能力名；异常输入 → []。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from custom_components.fordpass_cn import capability  # noqa: E402


def _resp(features: str) -> dict:
    """构造与福特云 ccfeatures 响应同构的输入（含 data 层）。"""
    return {
        "status": 200,
        "version": "1.0",
        "data": {
            "featureData": {
                "availableFeatures": features,
                "authedFeatures": "A4",
            }
        },
    }


def test_parse_real_bitmap() -> None:
    """锐际纯油真实位图（日志实证）→ 非空中文能力集。"""
    feats = capability.parse_cloud_features(
        _resp("03,04,05,06,07,08,10,11,23")
    )
    assert feats, "真实位图应解析出能力"
    assert all(f["zh"] for f in feats), "每项都应有中文名或标注"
    codes = [f["raw"] for f in feats]
    assert codes == ["03", "04", "05", "06", "07", "08", "10", "11", "23"]


def test_parse_empty() -> None:
    """空/异常输入 → []（调用方据此不创建实体）。"""
    assert capability.parse_cloud_features(None) == []
    assert capability.parse_cloud_features({}) == []
    assert capability.parse_cloud_features({"data": None}) == []
    assert capability.parse_cloud_features({"data": {"featureData": None}}) == []
    assert capability.parse_cloud_features(_resp("")) == []
    assert capability.parse_cloud_features(_resp("  ")) == []


def test_parse_unknown_code_kept() -> None:
    """未映射特性保留原始码（不丢弃）。"""
    feats = capability.parse_cloud_features(_resp("23"))
    assert feats and any("23" in f["zh"] for f in feats)
