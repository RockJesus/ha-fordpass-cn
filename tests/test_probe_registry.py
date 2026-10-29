"""探测端点三处清单一致性（v6.1.9 回归保护）。

新增一个探测端点要同步改三处：

1. ``api.py`` probe 注册表（vehicle 级 ``probes`` 字典的键，
   或 ``probe_fposb`` 内部 ``probes`` 字典的键）
2. ``sensor.py`` 探测实体标签清单（(key, 标签, kind) 元组）
3. ``coordinator.py`` ``_probe_names`` 活动日志名单

漏掉任何一处都会出现"探测有数据却没有实体"或"实体暂无数据但不写
活动"的静默缺陷。本测试用 AST 提取三处键集合做对称校验，不加载
homeassistant 假模块。充电域键（fuelType 过滤）同样要求三处一致。
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "custom_components" / "fordpass_cn"


def _tree(name: str) -> ast.Module:
    return ast.parse((ROOT / name).read_text(encoding="utf-8"))


def _str_dict_keys(d: ast.Dict) -> set[str]:
    return {
        k.value
        for k in d.keys
        if isinstance(k, ast.Constant) and isinstance(k.value, str)
    }


def _tuple_first_elements(tuples: list[ast.Tuple]) -> set[str]:
    return {
        t.elts[0].value
        for t in tuples
        if isinstance(t, ast.Tuple)
        and t.elts
        and isinstance(t.elts[0], ast.Constant)
        and isinstance(t.elts[0].value, str)
    }


def _find_dict_by_key_prefix(tree: ast.Module, prefix: str) -> ast.Dict | None:
    """在模块里找注释紧邻前缀的字典字面量（probe 注册表带版本注释）。

    用「字典首个键以 prefix 开头」定位，避免依赖行号。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict) and node.keys:
            first = node.keys[0]
            if (
                isinstance(first, ast.Constant)
                and isinstance(first.value, str)
                and first.value.startswith(prefix)
            ):
                return node
    return None


def _probe_registry_keys() -> set[str]:
    tree = _tree("api.py")
    keys: set[str] = set()
    # 主注册表：首个键 "maintenance-history"
    main = _find_dict_by_key_prefix(tree, "maintenance-history")
    if main is not None:
        keys |= _str_dict_keys(main)
    # probe_fposb 内部表：首个键 "fposb_history"
    fposb = _find_dict_by_key_prefix(tree, "fposb_history")
    if fposb is not None:
        keys |= _str_dict_keys(fposb)
    return keys


def _sensor_label_keys() -> set[str]:
    """sensor.py 探测实体标签元组清单：探测标签是「元组的元组」（括号包裹、
    无方括号），AST 上是一个最大的 ast.Tuple（其 elts 全是三元组）。"""
    tree = _tree("sensor.py")
    best: list[ast.Tuple] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Tuple)
            and len(node.elts) > len(best)
            and all(isinstance(e, ast.Tuple) and len(e.elts) == 3 for e in node.elts)
        ):
            best = list(node.elts)
    return _tuple_first_elements(best)


def _coordinator_probe_names() -> set[str]:
    tree = _tree("coordinator.py")
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Dict)
            and node.keys
            and isinstance(node.keys[0], ast.Constant)
            and node.keys[0].value == "wallbox_bound"
        ):
            return _str_dict_keys(node)
    return set()


def test_probe_registry_matches_sensor_labels() -> None:
    registry = _probe_registry_keys()
    labels = _sensor_label_keys()
    # 刻意不走通用探测实体清单的键（各有专属展示或等待实测）：
    # - fposb_history/detail/pud/sa：FordPassFposbSensor 专属传感器展示
    # - vpoi_chargestations_v4 / vpoi_saved_v4：v5.8.4 先探测落盘，
    #   数据格式待 App 实测确认后再替换 v3 传感器数据源，暂不建实体
    exceptions = {
        "fposb_history", "fposb_detail", "fposb_pud", "fposb_sa",
        "vpoi_chargestations_v4", "vpoi_saved_v4",
    }
    missing_label = sorted(registry - labels - {"uservehicles"} - exceptions)
    # 反向：标签键必须能在 api.py（探测注册表/专属 probe 方法）或
    # coordinator.py（lincoln/lwosb 运行时注入）中找到同名 quoted 字符串，
    # 防止实体标签键写错探测键名。
    api_src = (ROOT / "api.py").read_text(encoding="utf-8")
    coord_src = (ROOT / "coordinator.py").read_text(encoding="utf-8")
    missing_probe = sorted(
        k for k in labels - {"uservehicles"}
        if f'"{k}"' not in api_src and f'"{k}"' not in coord_src
    )
    assert not missing_label, f"探测注册表有键但 sensor.py 无实体标签: {missing_label}"
    assert not missing_probe, f"sensor.py 有实体标签但探测/协调器均无该键: {missing_probe}"


def test_probe_registry_matches_coordinator_names() -> None:
    """coordinator._probe_names 只收「无数据要写活动」的键（历史上就不含
    ota_versions 等全量键），故只校验 v4.3.8 名单已覆盖的组别不被新键遗漏：
    充电域键（wallbox_/evss_/vpoi_ 前缀中在注册表里的）必须都在名单里。"""
    registry = _probe_registry_keys()
    names = _coordinator_probe_names()
    charge = sorted(
        k for k in registry
        if k.split("_", 1)[0] in ("wallbox", "evss", "vpoi")
    )
    missing = sorted(set(charge) - names - {"vpoi_chargestations_v4", "vpoi_saved_v4"})
    assert not missing, f"充电域探测键在 coordinator._probe_names 缺失: {missing}"


def test_new_endpoints_v619_present() -> None:
    """v6.1.9 新接入的 7 个只读端点必须在三处清单里（防误删）。"""
    expected = {
        "warranty",
        "capability_v4",
        "vehicle_info",
        "fposb_resources",
        "evss_freepass_notice",
        "wallbox_process_log",
        "wallbox_tnc_content",
    }
    assert expected <= _probe_registry_keys()
    assert expected <= _sensor_label_keys()
    assert expected <= _coordinator_probe_names()
