"""配置流程必须先备好运行时数据包，再调用需要签名的接口（v6.3.9 回归）。

首次安装时还没有 config entry，``__init__.py`` 的 setup 从未跑过，数据包
也就没人触发获取；而短信登录第一步就要加密手机号
（``FordPassCrypto.get()``），缺包时抛 ``CryptoDataMissing``，前端只能看到
「未知错误」。v6.3.9 之前只有密码登录的 b2c 步骤兜了这一次下载。

这里用 AST 静态检查：三个会用到签名的步骤都必须调用
``self._async_ensure_crypto()``。新增登录步骤时若忘了兜底，测试直接失败。
"""
from __future__ import annotations

import ast
import importlib.util

import pytest

from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "fordpass_cn"
HELPER = "_async_ensure_crypto"
STEPS = ("async_step_phone", "async_step_code", "async_step_b2c")


def _flow_class() -> ast.ClassDef:
    tree = ast.parse((SRC / "config_flow.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "FordPassConfigFlow":
            return node
    raise AssertionError("config_flow.py 里找不到 FordPassConfigFlow")


def _method(name: str) -> ast.AsyncFunctionDef:
    for node in _flow_class().body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"FordPassConfigFlow 缺少 {name}")


def _calls_helper(node: ast.AST) -> bool:
    return any(
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == HELPER
        for call in ast.walk(node)
    )


def test_helper_exists() -> None:
    assert _method(HELPER), "数据包兜底方法不见了"


@pytest.mark.parametrize("step", STEPS)
def test_step_ensures_crypto_first(step: str) -> None:
    assert _calls_helper(_method(step)), f"{step} 未在调用接口前确保数据包可用"


def test_missing_pack_raises_dedicated_error(tmp_path: Path) -> None:
    """缺包抛专用异常：配置流程据此给出「缺数据包」提示，而不是「未知错误」。"""
    spec = importlib.util.spec_from_file_location("fpcn_wbsk_probe", SRC / "wbsk.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert issubclass(module.CryptoDataMissing, RuntimeError)
    with pytest.raises(module.CryptoDataMissing):
        module.FordPassCrypto(key_file=str(tmp_path / "no_such_key.json"))
