"""锁与开关平台必须关闭轮询（v6.1.6 回归保护）。

背景：lock / switch 都是协调器刷新广播驱动的纯推送实体，轮询从不产出
新信息；且 PARALLEL_UPDATES=1 让轮询与命令服务调用共用一把信号量——
锁/寻车等长命令链（等命令完成 + force_refresh，实测 60-90s）期间，
平台轮询轮被堵在 semaphore.acquire() 上，触发
「Updating fordpass_cn lock took longer than the scheduled update
interval 0:00:30」警告（2026-10-29 11:25 实测复现，4 次/90s）。

修复是给全部实体类声明 ``_attr_should_poll = False``。本测试用 AST
静态检查，不加载 homeassistant 假模块：任何人再加一个漏设该属性的
SwitchEntity / LockEntity 子类（或删掉现有声明）都会立即失败。
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "custom_components" / "fordpass_cn"


def _classes(tree: ast.Module) -> dict[str, ast.ClassDef]:
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
    }


def _has_should_poll_false(cls: ast.ClassDef) -> bool:
    for node in cls.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
            for t in targets:
                if isinstance(t, ast.Name) and t.id == "_attr_should_poll":
                    return (
                        isinstance(node.value, ast.Constant)
                        and node.value.value is False
                    )
    return False


def _base_names(cls: ast.ClassDef) -> set[str]:
    names = set()
    for b in cls.bases:
        if isinstance(b, ast.Name):
            names.add(b.id)
        elif isinstance(b, ast.Attribute):
            names.add(b.attr)
    return names


def test_lock_classes_no_polling() -> None:
    tree = ast.parse((ROOT / "lock.py").read_text(encoding="utf-8"))
    classes = _classes(tree)
    lock_classes = [
        n for n, c in classes.items() if "LockEntity" in _base_names(c)
    ]
    assert "FordPassLock" in lock_classes, "应找到 LockEntity 子类"
    for name in lock_classes:
        assert _has_should_poll_false(classes[name]), (
            f"lock.py 的 {name} 缺少 _attr_should_poll = False："
            "纯推送实体轮询会与 PARALLEL_UPDATES=1 的服务调用抢信号量，"
            "命令链期间触发 took longer 警告"
        )


def test_switch_classes_no_polling() -> None:
    tree = ast.parse((ROOT / "switch.py").read_text(encoding="utf-8"))
    classes = _classes(tree)

    # 传递性基类解析
    def bases_of(name: str, seen: frozenset[str] = frozenset()) -> set[str]:
        if name in seen or name not in classes:
            return set()
        out = set(_base_names(classes[name]))
        for b in list(out):
            out |= bases_of(b, seen | {name})
        return out

    # 每个直接继承 SwitchEntity 的类，要么自己声明 _attr_should_poll = False，
    # 要么从某个已声明的基类继承
    for name, cls in classes.items():
        if "SwitchEntity" not in _base_names(cls):
            continue
        chain = {name, *bases_of(name)}
        ok = any(
            c in chain
            and c in classes
            and _has_should_poll_false(classes[c])
            for c in chain
        )
        assert ok, (
            f"switch.py 的 {name} 及其基类都没有 _attr_should_poll = False"
        )
