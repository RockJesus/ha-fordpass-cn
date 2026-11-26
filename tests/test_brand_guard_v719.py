"""v7.1.9 回归：品牌归属判定不得静默顶替用户选择。

实测故障：用户选「林肯之道」登录（同一手机号已有福特条目），品牌预检
发现林肯侧无车、福特侧有车后静默改成福特创建新条目——绕过"同号同品牌"
查重，最终多出一条重复的福特条目并立刻报 600。三条契约：
1. ``_finish_login`` 中出现 ``brand_mismatch`` 中止，且品牌切换只在查重之后；
2. ``async_create_entry`` 之前必须再做一次 ``_check_duplicate_entry``；
3. setup 的品牌纠正分支必须先判定同号另一品牌条目冲突。
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "fordpass_cn"


def _tree(name: str) -> ast.Module:
    return ast.parse((SRC / name).read_text(encoding="utf-8"))


def _method(tree: ast.Module, cls_name: str, name: str):
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == cls_name:
            for item in node.body:
                if (
                    isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and item.name == name
                ):
                    return item
    raise AssertionError(f"{cls_name} 缺少 {name}")


def _calls(node: ast.AST, attr: str) -> list[ast.Call]:
    return [
        n for n in ast.walk(node)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == attr
    ]


def test_brand_mismatch_aborts_instead_of_switching():
    flow = _method(_tree("config_flow.py"), "FordPassConfigFlow", "_finish_login")
    text = ast.get_source_segment(
        (SRC / "config_flow.py").read_text(encoding="utf-8"), flow
    )
    assert "no_vehicles_in_selected_brand" in text, "缺少「所选品牌无车」的中止"
    # 不得把品牌静默切成另一品牌
    assert "self._brand = resolved" not in text, "仍存在静默切换品牌"
    # 真实原因必须先报：如实中止出现在任何查重之前
    assert text.index("no_vehicles_in_selected_brand") < text.index("_check_duplicate_entry")


def test_duplicate_check_runs_before_create_entry():
    flow = _method(_tree("config_flow.py"), "FordPassConfigFlow", "_finish_login")
    dup_lines = [c.lineno for c in _calls(flow, "_check_duplicate_entry")]
    create_lines = [c.lineno for c in _calls(flow, "async_create_entry")]
    assert create_lines, "_finish_login 未创建 entry"
    assert dup_lines, "创建前缺少最终品牌查重"
    assert min(dup_lines) < min(create_lines), "查重必须在创建 entry 之前"


def test_setup_brand_fix_guards_same_phone_conflict():
    src = (SRC / "__init__.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    update_calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "async_update_entry"
    ]
    assert update_calls, "setup 品牌纠正写入不见了"
    brand_updates = [
        c for c in update_calls
        if "CONF_BRAND" in (ast.get_source_segment(src, c) or "")
    ]
    assert brand_updates, "setup 品牌纠正写入不见了"
    assert "_conflict" in src, "品牌纠正缺少同号条目冲突判定"
    assert "FordPass 品牌纠正跳过" in src


def test_brand_probe_reports_at_warning():
    src = (SRC / "brand_probe.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            seg = ast.get_source_segment(src, node) or ""
            if "车辆探测失败" in seg:
                assert "_LOGGER.warning" in seg, "探测失败仍是 debug，默认日志不可见"
