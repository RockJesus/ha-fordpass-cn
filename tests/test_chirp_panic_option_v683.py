"""v6.8.3 回归测试：寻车类型偏好选项加回「声光共舞」，鸣笛命令通道仍隔离。

回归保护点：
- ``CHIRP_TYPE_OPTIONS`` 与 App 设置页对齐为 5 种，含「声光共舞」(panic=4)，
  可选并保存到云端 AnnouncementType=4。
- ``HONK_COMMAND_TYPES`` 只含鸣笛 v5 /honk 可下发的 0-3 类型，不含 panic 4；
  灯+喇叭共舞仍专属独立「声光寻车」开关，鸣笛开关不切 panic 通道。
- 偏好 → AnnouncementType / ChirpType 映射齐全；select 的 index+1 序号链路
  反查不越界（共舞序号 5，反查 OPTIONS[4] 命中共舞）。
"""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "fordpass_cn"


def _load_const():
    if "fchirp" not in sys.modules:
        pkg = types.ModuleType("fchirp")
        pkg.__path__ = [str(SRC)]  # type: ignore[attr-defined]
        sys.modules["fchirp"] = pkg
    return importlib.import_module("fchirp.const")


const = _load_const()


def test_preference_options_align_with_app():
    """偏好下拉 5 种，含共舞；默认类型在选项内。"""
    assert "声光共舞" in const.CHIRP_TYPE_OPTIONS
    assert len(const.CHIRP_TYPE_OPTIONS) == 5
    assert const.DEFAULT_CHIRP_TYPE in const.CHIRP_TYPE_OPTIONS


def test_honk_command_types_exclude_panic():
    """鸣笛可下发类型 0-3，不含 panic 共舞。"""
    assert "声光共舞" not in const.HONK_COMMAND_TYPES
    assert len(const.HONK_COMMAND_TYPES) == 4
    # 鸣笛集合是偏好选项的子集
    assert set(const.HONK_COMMAND_TYPES) <= set(const.CHIRP_TYPE_OPTIONS)


def test_all_preference_types_have_mappings():
    """每个偏好类型都能映射到 AnnouncementType 与 ChirpType。"""
    for name in const.CHIRP_TYPE_OPTIONS:
        assert name in const.CHIRP_TO_ANNOUNCE, f"{name} 缺 AnnouncementType 映射"
        assert name in const.CHIRP_TO_TYPE, f"{name} 缺 ChirpType 映射"


def test_panic_maps_to_four():
    """共舞 = panic 枚举 4（云端 AnnouncementType 与 v5 ChirpType 一致）。"""
    assert const.CHIRP_TO_ANNOUNCE["声光共舞"] == "4"
    assert const.CHIRP_TO_TYPE["声光共舞"] == 4


def test_index_serial_link_is_in_range():
    """select index+1 序号链路：共舞序号 5，api 反查 OPTIONS[4] 命中共舞。"""
    idx = const.CHIRP_TYPE_OPTIONS.index("声光共舞")
    serial = idx + 1
    assert serial == 5
    assert const.CHIRP_TYPE_OPTIONS[serial - 1] == "声光共舞"
    assert 0 < serial <= len(const.CHIRP_TYPE_OPTIONS)
