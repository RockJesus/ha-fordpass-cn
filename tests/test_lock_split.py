"""v6.4.3 / v6.4.4 回归测试：lock 平台整体退役，控制动作全部迁到 button。

v6.4.3：整车门锁 → 「开锁」「关锁」两个动作按钮（原实体依赖云端 lockStatus
快照回读，快照滞后导致卡片与实车不同步）。
v6.4.4：后备箱锁 → 「后备箱解锁」动作按钮（网关只有 TrunkUnlock 弹开命令，
没有独立的后备箱锁命令），lock 平台整体不再加载。

本测试只做源码与翻译文件的静态校验，不依赖 HA 运行时。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "custom_components" / "fordpass_cn"

ACTION_BUTTONS = [
    ("unlock_door", "CMD_UNLOCK", "开锁", "Unlock Doors"),
    ("lock_door", "CMD_LOCK", "关锁", "Lock Doors"),
    ("trunk_unlock", "CMD_TRUNK_UNLOCK", "后备箱解锁", "Unlock Trunk"),
]


def test_lock_platform_retired():
    """lock 平台整体退役：平台文件已删除、PLATFORMS 不再转发 lock。"""
    assert not (SRC / "lock.py").exists(), "lock.py 应已删除（锁平台整体迁到 button）"
    init = (SRC / "__init__.py").read_text(encoding="utf-8")
    assert "Platform.LOCK" not in init, "PLATFORMS 不应再包含 lock 平台"


def test_lock_entities_cleaned_from_registry():
    """旧 lock.* 条目必须显式清理，否则注册表残留永远 unavailable。"""
    init = (SRC / "__init__.py").read_text(encoding="utf-8")
    assert "_cleanup_retired_lock_entities" in init
    block = init.split("async def _cleanup_retired_lock_entities", 1)[1].split("\nasync def ", 1)[0]
    assert "async_entries_for_config_entry" in block
    assert "async_remove" in block
    assert 'startswith("lock.")' in block
    # 跳过用户手动禁用的条目（用户选择不可被集成覆盖）
    assert 'disabled_by == "user"' in block
    # 清理函数必须在平台转发后执行
    assert init.index("_cleanup_retired_lock_entities(hass, entry)") > init.index(
        "async_forward_entry_setups(entry, PLATFORMS)"
    )


@pytest.mark.parametrize("key,command,zh,en", ACTION_BUTTONS)
def test_action_button_created_with_command(key, command, zh, en):
    text = (SRC / "button.py").read_text(encoding="utf-8")
    assert re.search(rf'FordPassButton\(\s*coordinator, "{key}"', text), f"缺按钮 {key}"
    assert command in text, f"缺命令常量 {command}"


@pytest.mark.parametrize("key,command,zh,en", ACTION_BUTTONS)
def test_action_button_translated(key, command, zh, en):
    for rel, expected in (
        ("strings.json", zh),
        ("translations/zh-Hans.json", zh),
        ("translations/en.json", en),
    ):
        data = json.loads((SRC / rel).read_text(encoding="utf-8"))
        name = data.get("entity", {}).get("button", {}).get(key, {}).get("name")
        assert name == expected, f"{rel} 的 button.{key} 名称应为 {expected}，实际 {name}"


def test_action_buttons_use_translation_key():
    """三个动作按钮走翻译键（否则只有内置中文名、切英文不生效）。"""
    text = (SRC / "button.py").read_text(encoding="utf-8")
    block = text.split("_TRANSLATED_KEYS = frozenset({", 1)[1].split("})", 1)[0]
    for key, *_ in ACTION_BUTTONS:
        assert f'"{key}"' in block, f"{key} 未列入 _TRANSLATED_KEYS"


def test_trunk_button_capability_gated():
    """后备箱封锁依赖尾门能力，且曾被网关拒绝的车型不创建。"""
    text = (SRC / "button.py").read_text(encoding="utf-8")
    assert '"tailgateDoor"' in text and '"innerTailgateDoor"' in text
    assert "cmd_rejected(CMD_TRUNK_UNLOCK)" in text


def test_lock_translations_removed():
    """lock 平台段翻译应整段移除（门锁与后备箱都已迁到 button）。"""
    for rel in ("strings.json", "translations/zh-Hans.json", "translations/en.json"):
        data = json.loads((SRC / rel).read_text(encoding="utf-8"))
        assert "lock" not in data.get("entity", {}), f"{rel} 仍保留 entity.lock 段"
        assert (
            data["entity"]["button"]["trunk_unlock"]["name"]
        ), f"{rel} 缺 button.trunk_unlock"
