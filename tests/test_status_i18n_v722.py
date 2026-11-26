"""v7.2.2：云端状态枚举中文化回归。

云端 vehiclestatus 里的状态字段是 ``STATUS_LOW`` / ``STATUS_NEEDS_SERVICE``
这类全大写枚举串。此前后端只显式映射了少数几个值，未命中的值会原样透出
到实体状态（机油健康状态显示 ``STATUS_LOW``）。
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "fordpass_cn" / "sensor.py"


def _load_helpers() -> dict:
    """从源码里取出 ``_STATUS_WORDS`` 与 ``_localize_enum`` 单独执行。

    精简 CI 环境没有 homeassistant 依赖，无法 import sensor 模块整体，
    所以按 AST 抽取两个纯 Python 节点构造一个最小命名空间。
    """
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    picked = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if set(names) & {"_STATUS_WORDS", "_ENUM_PREFIXES"}:
                picked.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name == "_localize_enum":
            picked.append(node)
    ns: dict = {}
    module = ast.Module(body=picked, type_ignores=[])
    exec(compile(module, str(SRC), "exec"), ns)
    return ns


def test_status_enum_is_chinese() -> None:
    f = _load_helpers()["_localize_enum"]
    assert f("STATUS_LOW") == "偏低"
    assert f("STATUS_GOOD") == "良好"
    assert f("STATUS_OK") == "正常"
    assert f("STATUS_NEEDS_SERVICE") == "需要保养"
    assert f("STATUS_CRITICAL") == "严重"


def test_localized_or_numeric_values_untouched() -> None:
    f = _load_helpers()["_localize_enum"]
    for raw in ("充电中", "Systm_Normal_Stat", "Not_Supported", "已关闭", "", None, 42, 1.5, True):
        assert f(raw) == raw, raw
    # 无法完整翻译的枚举保持原样，不拼出半中半英
    assert f("STATUS_SOMETHING_NEW") == "STATUS_SOMETHING_NEW"


def test_health_map_covers_low() -> None:
    src = SRC.read_text(encoding="utf-8")
    assert '"STATUS_LOW": "偏低"' in src
    assert '"STATUS_NEEDS_SERVICE": "需要保养"' in src


def test_native_value_falls_back_to_localizer() -> None:
    src = SRC.read_text(encoding="utf-8")
    assert "value = _localize_enum(value)" in src
