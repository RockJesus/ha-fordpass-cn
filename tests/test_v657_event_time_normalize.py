"""v6.5.7 回归测试：事件时间属性展示层归一化。

背景：事件实体用 RestoreEntity 恢复最后一次事件，恢复的是历史快照。
v6.5.0 之前的 event_time / event_time_utc 是无时区格式
（"2026-11-06 17:37:38"），升级不会改写，会一直停在前端与自动化里。
归一化只在展示层进行——不得重放事件（重放会把事件时刻改成重启时刻）。

断言旧行为会失败的点：
- 无时区本地串未被补上 +08:00；
- 无偏移 UTC 串被按北京时间解释（偏 8 小时）；
- 已带偏移的值被二次改写；
- 无法解析的文本被吞成 None。
"""
from __future__ import annotations

import importlib.util
import pathlib

SRC = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "fordpass_cn"


def _load_capability():
    spec = importlib.util.spec_from_file_location("fp_capability", SRC / "capability.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


cap = _load_capability()
normalize = cap.event_time_normalize


def test_legacy_local_string_gets_offset() -> None:
    assert normalize("2026-11-06 17:37:38") == "2026-11-06T17:37:38+08:00"


def test_legacy_utc_string_gets_utc_offset() -> None:
    # 旧 event_time_utc 是 UTC 口径；按北京时间解释会偏 8 小时
    assert normalize("2026-11-06 09:37:38", utc=True) == "2026-11-06T09:37:38+00:00"


def test_offset_strings_pass_through_unchanged() -> None:
    for value in ("2026-11-06T17:37:38+08:00", "2026-11-06T09:37:38+00:00",
                  "2026-11-06T09:37:38Z"):
        assert normalize(value) == value
        assert normalize(value, utc=True) == value


def test_slash_format_is_converted() -> None:
    # 云端北京时间明文（斜杠格式）仍应转 ISO
    assert normalize("2026/11/08 15:11:13") == "2026-11-08T15:11:13+08:00"


def test_non_time_values_pass_through() -> None:
    assert normalize(None) is None
    assert normalize("") == ""
    assert normalize("not a time") == "not a time"
    assert normalize("有告警") == "有告警"


def test_placeholder_year_kept_as_is() -> None:
    # 云端占位 0001-01-01 这类值不伪造为有效时间，原样保留
    assert normalize("0001-01-01 00:00:00") == "0001-01-01 00:00:00"
