# -*- coding: utf-8 -*-
"""manifest.json 规范单测——v4.9.7 新增。

按 HA 官方发布规范（references/ha-official-standards.md）断言关键字段，
防止发布版本号/必填字段回退。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MANIFEST = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "custom_components",
    "fordpass_cn",
    "manifest.json",
)


def _manifest() -> dict:
    with open(MANIFEST, encoding="utf-8") as fh:
        return json.load(fh)


def test_domain_rule() -> None:
    m = _manifest()
    assert m["domain"] == "fordpass_cn"
    assert m["domain"].islower() and not m["domain"][0].isdigit()
    assert m["domain"][0] != "_"


def test_version_semver_single_digit() -> None:
    """版本 SemVer 三段 + 段内每数字 ≤9（用户十进制规则）。"""
    parts = _manifest()["version"].split(".")
    assert len(parts) == 3 and all(p.isdigit() for p in parts)
    assert all(int(p) < 10 for p in parts), "段内数字超 9（违反满 9 进位规则）"


def test_required_fields() -> None:
    m = _manifest()
    assert m.get("name")
    assert isinstance(m.get("codeowners", []), list) and m["codeowners"]
    assert m.get("documentation", "").startswith("https://")
    assert m.get("issue_tracker", "").startswith("https://")
    assert m.get("integration_type") in (
        "device", "entity", "hardware", "helper", "hub", "service", "system",
        "virtual",
    )
    assert m.get("iot_class") in (
        "assumed_state", "cloud_polling", "cloud_push", "local_polling",
        "local_push", "calculated",
    )
    assert m.get("quality_scale") == "bronze"


def test_config_flow_declared() -> None:
    m = _manifest()
    assert m.get("config_flow") is True
    assert os.path.exists(
        os.path.join(
            os.path.dirname(MANIFEST), "config_flow.py"
        )
    ), "config_flow=true 但缺 config_flow.py"


def test_no_bom() -> None:
    with open(MANIFEST, "rb") as fh:
        raw = fh.read(3)
    assert raw != b"\xef\xbb\xbf", "manifest 含 BOM"
