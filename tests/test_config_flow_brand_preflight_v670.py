"""v6.7.0 回归：登录收尾阶段必须做品牌-车辆归属判定，且不能吞掉临时故障。

静态检查（AST）三条契约：
1. ``_finish_login`` 是协程（判定需要 await 网络请求），两处登录成功调用都 await；
2. 新建 entry 之前调用 ``_async_resolve_brand``，并存在 ``no_vehicles`` 中止；
3. 判定结果用 ``elif`` 链处理——``resolved is None`` 不能落到「切换品牌」分支
   （曾在实现中把品牌写成 None）。
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "fordpass_cn"


def _tree() -> ast.Module:
    return ast.parse((SRC / "config_flow.py").read_text(encoding="utf-8"))


def _cls(name: str) -> ast.ClassDef:
    for node in _tree().body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"找不到类 {name}")


def _method(cls_name: str, name: str):
    for node in _cls(cls_name).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{cls_name} 缺少 {name}")


def test_finish_login_is_coroutine():
    node = _method("FordPassConfigFlow", "_finish_login")
    assert isinstance(node, ast.AsyncFunctionDef), "_finish_login 必须是 async"


def test_login_success_paths_await_finish_login():
    tree = _tree()
    awaits = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Await)
        and isinstance(n.value, ast.Call)
        and getattr(n.value.func, "attr", "") == "_finish_login"
    ]
    assert len(awaits) >= 2, f"两处登录成功都应 await _finish_login，实际 {len(awaits)}"


def test_preflight_before_create_entry():
    node = _method("FordPassConfigFlow", "_finish_login")
    calls = [
        n for n in ast.walk(node)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "_async_resolve_brand"
    ]
    assert calls, "新建 entry 前必须调用 _async_resolve_brand"
    aborts = [
        n for n in ast.walk(node)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "async_abort"
        and any(isinstance(k.value, ast.Constant) and k.value.value == "no_vehicles"
                for k in n.keywords)
    ]
    assert aborts, "两个品牌都为空时必须 abort(reason=no_vehicles)"


def test_none_branch_does_not_switch_brand():
    """品牌切换必须挂在「resolved != 当前品牌」分支，resolved 为 None 时不得赋值。

    实现中曾写成两条独立 if：``if resolved is None: warn`` + ``if resolved != self._brand``，
    网络全失败时 resolved=None 会满足后一条，把 ``self._brand`` 写成 None。
    """
    src = (SRC / "config_flow.py").read_text(encoding="utf-8")
    assert "elif resolved is None:" in src, "resolved 为 None 必须是 elif 分支"
    assert "elif resolved != self._brand:" in src, "品牌切换必须是 elif 分支"
    assert "self._brand = resolved" not in src, "不得静默把品牌切成另一品牌"
    # 两条 elif 必须出现在同一个 if 链、且切换在两个 None 分支之后
    idx_abort = src.index("if resolved is None and reachable:")
    idx_none = src.index("elif resolved is None:")
    idx_switch = src.index("elif resolved != self._brand:")
    assert idx_abort < idx_none < idx_switch, "分支顺序应为 abort → 判空 → 切换"


def test_resolver_returns_tuple_contract():
    node = _method("FordPassConfigFlow", "_async_resolve_brand")
    assert isinstance(node, ast.AsyncFunctionDef)
    ret_ann = node.returns
    assert ret_ann is not None, "应标注返回类型 (brand|None, reachable)"
