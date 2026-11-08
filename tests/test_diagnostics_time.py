"""诊断接口时间字段序列化回归（v6.4.9）。

回归保护点：
- 诊断包里的 ``last_poll`` / ``last_success_time`` 取不到时间时必须给空值，
  不能出现字符串 ``"None"``。此前实现是 ``str(getattr(coord, ...))``，
  集成自己的成功时间字段取不到时就把 None 变成了字符串，排查时无法区分
  「没有记录」与「记录值就是 None」。
- 能取到时间时给 ISO 8601 串（带时区），与 HA 诊断包其它时间字段一致。
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "fordpass_cn"


def _load_capability():
    name = "fp_capability_iso"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SRC / "capability.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_iso_or_none_keeps_real_datetime():
    cap = _load_capability()
    dt = datetime(2026, 11, 8, 23, 54, 47, tzinfo=timezone(timedelta(hours=8)))
    assert cap.iso_or_none(dt) == "2026-11-08T23:54:47+08:00"


def test_iso_or_none_returns_none_for_missing_values():
    """None、HA 已被移除的字段（取到方法对象）、字符串 "None" 都不得回显。"""
    cap = _load_capability()
    assert cap.iso_or_none(None) is None
    assert cap.iso_or_none("None") is None
    assert cap.iso_or_none(datetime.now) is None  # 方法对象
    assert cap.iso_or_none(0) is None


def test_diagnostics_fields_are_json_nullable():
    """把序列化结果塞进诊断字典后，None 得以保留为 JSON null。"""
    import json

    cap = _load_capability()
    payload = {
        "last_poll": cap.iso_or_none(None),
        "last_success_time": cap.iso_or_none(None),
        "last_update_success": True,
        "data_available": True,
    }
    dumped = json.dumps(payload)
    assert '"last_poll": null' in dumped
    assert '"None"' not in dumped
