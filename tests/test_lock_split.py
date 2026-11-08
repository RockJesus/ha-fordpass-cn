"""v6.4.3 回归测试：门锁由 lock 实体拆为「开锁」「关锁」两个动作按钮。

原 lock 实体依赖云端 lockStatus 快照回读，命令完成后快照滞后导致锁卡片
与实车不同步；改为动作按钮后不承载状态，按下即发命令并记录结果。
本测试只做源码与翻译文件的静态校验，不依赖 HA 运行时。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "custom_components" / "fordpass_cn"

DOOR_BUTTONS = [
    ("unlock_door", "CMD_UNLOCK", "开锁", "Unlock Doors"),
    ("lock_door", "CMD_LOCK", "关锁", "Lock Doors"),
]


def test_lock_entity_removed():
    """lock 平台不再创建整车门锁实体（旧 `-lock` unique_id 会残留）。"""
    text = (SRC / "lock.py").read_text(encoding="utf-8")
    assert "class FordPassLock(" not in text
    assert '{coordinator.uid_key}-lock"' not in text


def test_lock_platform_cleans_stale_registry():
    """拆分后必须显式清理旧 lock 实体，否则注册表残留永远 unavailable。"""
    text = (SRC / "lock.py").read_text(encoding="utf-8")
    assert "async_entries_for_config_entry" in text
    assert "async_remove" in text
    # 跳过用户手动禁用的条目
    assert 'disabled_by == "user"' in text


@pytest.mark.parametrize("key,command,zh,en", DOOR_BUTTONS)
def test_door_button_created_with_command(key, command, zh, en):
    text = (SRC / "button.py").read_text(encoding="utf-8")
    assert re.search(rf'FordPassButton\(\s*coordinator, "{key}"', text), f"缺按钮 {key}"
    assert command in text, f"缺命令常量 {command}"


@pytest.mark.parametrize("key,command,zh,en", DOOR_BUTTONS)
def test_door_button_translated(key, command, zh, en):
    for rel, expected in (
        ("strings.json", zh),
        ("translations/zh-Hans.json", zh),
        ("translations/en.json", en),
    ):
        data = json.loads((SRC / rel).read_text(encoding="utf-8"))
        name = data.get("entity", {}).get("button", {}).get(key, {}).get("name")
        assert name == expected, f"{rel} 的 button.{key} 名称应为 {expected}，实际 {name}"


def test_door_buttons_use_translation_key():
    """两个动作按钮走翻译键（否则只有内置中文名、切英文不生效）。"""
    text = (SRC / "button.py").read_text(encoding="utf-8")
    block = text.split("_TRANSLATED_KEYS = frozenset({", 1)[1].split("})", 1)[0]
    for key, *_ in DOOR_BUTTONS:
        assert f'"{key}"' in block, f"{key} 未列入 _TRANSLATED_KEYS"


def test_old_lock_translation_removed():
    """旧门锁翻译条目应一并移除（trunk 后备箱锁保留）。"""
    zh = json.loads((SRC / "translations" / "zh-Hans.json").read_text(encoding="utf-8"))
    lock = zh.get("entity", {}).get("lock", {})
    assert "lock" not in lock
    assert lock.get("trunk", {}).get("name") == "后备箱锁"
