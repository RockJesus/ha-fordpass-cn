"""时间字段格式统一回归（v6.5.0）。

回归保护点：
- 云端"北京时间明文"（``2026/11/08 15:11:13``）必须能转成带 ``+08:00``
  的 ISO 8601；解析不出来、空值、云端占位（早于 2000 年）一律给 None，
  不能把原样字符串或占位值当时间输出；
- ``CN_TZ`` 固定 UTC+8（中国无夏令时），换了主机时区也不影响云端时间解释；
- 属性层不再输出无时区标识的 ``YYYY-MM-DD HH:MM:SS``——sensor / event
  两个平台里的时间格式化都已改为 ``isoformat``。
"""
from __future__ import annotations

import importlib.util
from datetime import timedelta
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "fordpass_cn"


def _load_capability():
    spec = importlib.util.spec_from_file_location(
        "fordpass_capability_v650", SRC / "capability.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


capability = _load_capability()


def test_cn_tz_is_fixed_utc8():
    assert capability.CN_TZ.utcoffset(None) == timedelta(hours=8)


def test_slash_datetime_becomes_iso_with_offset():
    assert capability.local_str_to_iso("2026/11/08 15:11:13") == (
        "2026-11-08T15:11:13+08:00"
    )


def test_dash_and_iso_inputs_normalized():
    assert capability.local_str_to_iso("2026-11-08 15:11:13") == (
        "2026-11-08T15:11:13+08:00"
    )
    assert capability.local_str_to_iso("2026-11-08T15:11:13") == (
        "2026-11-08T15:11:13+08:00"
    )


def test_minute_and_date_only_inputs():
    assert capability.local_str_to_iso("2026/11/08 15:11") == (
        "2026-11-08T15:11:00+08:00"
    )
    assert capability.local_str_to_iso("2026/11/08") == "2026-11-08T00:00:00+08:00"
    assert capability.local_str_to_iso("2026-11-08") == "2026-11-08T00:00:00+08:00"


def test_existing_offset_preserved():
    # 已带偏移的 ISO 原串不该被重新贴 +08:00
    assert capability.local_str_to_iso("2026-11-08T14:02:50+00:00") == (
        "2026-11-08T14:02:50+00:00"
    )
    assert capability.local_str_to_iso("2026-11-08T14:02:50Z") == (
        "2026-11-08T14:02:50+00:00"
    )


def test_invalid_and_placeholder_return_none():
    for bad in (
        None,
        "",
        "   ",
        "not a time",
        "0001-01-01 00:00:00",  # 云端占位
        "1970-01-01 08:00:00",  # epoch 占位
        1234567890,  # 数字由 _ts_to_local_any 负责，不走本函数
    ):
        assert capability.local_str_to_iso(bad) is None


def test_sensor_and_event_emit_isoformat():
    """两个平台的时间格式化都必须走 isoformat，不得再拼固定格式串。"""
    for name in ("sensor.py", "event.py"):
        src = (SRC / name).read_text(encoding="utf-8")
        assert 'strftime("%Y-%m-%d %H:%M:%S")' not in src, name
    assert 'isoformat(timespec="seconds")' in (SRC / "sensor.py").read_text(
        encoding="utf-8"
    )
    assert 'isoformat(timespec="seconds")' in (SRC / "event.py").read_text(
        encoding="utf-8"
    )


def test_clock_skew_limit_declared():
    """请求层时钟自检阈值与比对入口存在（120s 保守线）。"""
    src = (SRC / "api.py").read_text(encoding="utf-8")
    assert "CLOCK_SKEW_LIMIT = 120.0" in src
    assert "def _note_clock(" in src
    assert "def _note_clock_reject(" in src
    assert '"timestamp is out of range"' in src
