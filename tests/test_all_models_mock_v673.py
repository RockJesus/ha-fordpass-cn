"""福特 / 林肯全车型 mock 测试（v6.7.3）。

覆盖范围
--------
1. README「支持的车型」列出的每一款车都必须在本测试的样本矩阵里有对应条目
   ——车型清单只认 README 这个单一权威源，避免测试悄悄漏掉新加车型。
2. 每个样本按燃料类型（G 汽油 / D 柴油 / PHEV / HEV / MHEV / BEV）断言三条创建规则：
   - 电动化能力（`EV_FUEL_TYPES`）：决定 cevs（预约出发 / 充电 / 预约充电）、
     家充桩、动力电池类实体是否创建；
   - 座舱温控能力（vehicle-status `preCondStatusDsply` 有值）：决定
     `climate` 远程空调与 `select` 目标温度是否创建；
   - 纯油 / 柴油车型即使在云端拿到「端点 200 但业务恒空」的响应，也不得创建
     上述实体（v3.8.2 教训：端点存在 ≠ 车型支持）。
3. 门控口径一致性：`climate.py` 与 `select.py` 必须用同一个 preCond 判定；
   源码里所有内联电动化燃料元组必须与 `EV_FUEL_TYPES` 完全一致（防止
   某个文件被单独改动后产生漂移）。
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "custom_components" / "fordpass_cn"
README = ROOT / "README.md"

PRECOND_PATHS = [["preCondStatusDsply"]]


def _load_capability():
    name = "fp_capability_all_models"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SRC / "capability.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# 车型样本矩阵：(品牌, 车型名, fuelType, 云端是否返回 preCondStatusDsply)
# 车型名与 README「支持的车型」章节逐字一致；同一车型的动力变体单独列一行。
# has_precond 表示「该车云端返回了座舱温控字段」，纯油 / 柴油车型一律 False。
# ---------------------------------------------------------------------------
MODEL_SAMPLES = [
    # ---------------- 福特 ----------------
    ("Ford", "锐际 Escape", "G", False),
    ("Ford", "锐际 PHEV", "PHEV", True),
    ("Ford", "锐界 Edge", "G", False),
    ("Ford", "锐界L Edge L", "HEV", True),
    ("Ford", "探险者 Explorer", "G", False),
    ("Ford", "领裕 Equator", "G", False),
    ("Ford", "领睿 Equator Sport", "G", False),
    ("Ford", "蒙迪欧 Mondeo", "G", False),
    ("Ford", "蒙迪欧 混动", "HEV", True),
    ("Ford", "蒙迪欧 轻混", "MHEV", True),
    ("Ford", "致胜", "G", False),
    ("Ford", "EVOS", "G", False),
    ("Ford", "烈马 Bronco", "G", False),
    ("Ford", "智趣烈马", "BEV", True),
    ("Ford", "游骑侠 Ranger", "D", False),
    ("Ford", "F-150 猛禽 Raptor", "G", False),
    ("Ford", "Mustang 野马", "G", False),
    ("Ford", "电马 Mustang Mach-E", "BEV", True),
    ("Ford", "途睿欧 Tourneo", "D", False),
    ("Ford", "全顺 Transit", "D", False),
    ("Ford", "福克斯 Focus", "G", False),
    ("Ford", "嘉年华 Fiesta", "G", False),
    ("Ford", "福睿斯 Escort", "G", False),
    ("Ford", "金牛座 Taurus", "G", False),
    ("Ford", "翼虎 Kuga", "G", False),
    ("Ford", "翼虎 PHEV", "PHEV", True),
    ("Ford", "翼搏 EcoSport", "G", False),
    ("Ford", "领界 Territory", "G", False),
    ("Ford", "撼路者 Everest", "D", False),
    # ---------------- 林肯 ----------------
    ("Lincoln", "冒险家 Corsair", "G", False),
    ("Lincoln", "冒险家 PHEV", "PHEV", True),
    ("Lincoln", "航海家 Nautilus", "HEV", True),
    ("Lincoln", "飞行家 Aviator", "G", False),
    ("Lincoln", "飞行家 PHEV", "PHEV", True),
    ("Lincoln", "领航员 Navigator", "G", False),
    ("Lincoln", "林肯Z Zephyr", "G", False),
    ("Lincoln", "林肯Z 混动", "HEV", True),
    ("Lincoln", "大陆 Continental", "G", False),
    ("Lincoln", "MKZ", "HEV", True),
    ("Lincoln", "MKX", "G", False),
]


def _mock_status(fuel: str, has_precond: bool) -> dict:
    """构造一份 vehicle-status 快照（含纯油车型「端点 200 但业务恒空」的形态）。"""
    status: dict = {
        "fuelType": fuel,
        "vin": "MOCKVIN0123456789",  # 虚构值，仅用于 mock
        "odometer": 12345,
        "doorLockStatus": "LOCKED",
    }
    if has_precond:
        status["preCondStatusDsply"] = "OFF"
        status["remoteStartStatus"] = False
        status["CabnAmbTeActl"] = 21
    else:
        # 纯油/柴油车：云端不返回该字段（历史实测为空 / Off）
        status["preCondStatusDsply"] = None
    # cevs 三端点在纯油车上同样返回 200，只是业务恒空（v3.8.2 实测形态）
    status["departureTime"] = []
    status["chargeLogs"] = []
    status["isEnabled"] = "Off"
    return status


def _readme_models() -> list[str]:
    text = README.read_text(encoding="utf-8")
    block = re.search(r"## 🚗 支持的车型(.*?)\n## ", text, re.S)
    assert block, "README 缺「支持的车型」章节"
    body = block.group(1)
    models = []
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("**") or line.startswith(">"):
            continue
        for item in re.split(r"·|、|，", line):
            item = item.strip()
            if not item or item.startswith(("#", "能否接入", "注", "-")):
                continue
            if "：" in item or ":" in item or len(item) > 20:
                continue  # 章节说明句，不是车型名
            models.append(item)
    return models


def test_readme_models_all_covered():
    """README 列出的每一款车都必须在样本矩阵里出现。"""
    cap = _load_capability()
    del cap  # 仅确保模块可用
    covered = [m[1] for m in MODEL_SAMPLES]
    missing = [name for name in _readme_models()
               if not any(c == name or c.startswith(name) for c in covered)]
    assert not missing, f"README 车型未覆盖: {missing}"


def test_samples_cover_all_fuel_types_and_both_brands():
    fuels = {m[2] for m in MODEL_SAMPLES}
    brands = {m[0] for m in MODEL_SAMPLES}
    assert brands == {"Ford", "Lincoln"}, brands
    cap = _load_capability()
    # 样本须覆盖全部语义类别（EV_FUEL_TYPES 里的 E/P/H 是 BEV/PHEV/HEV 的旧别名）
    assert {"G", "D", "BEV", "PHEV", "HEV", "MHEV"} <= fuels, fuels
    assert fuels <= set(cap.EV_FUEL_TYPES) | {"G", "D"}, fuels
    # 别名与长名判定必须等价
    for alias, canonical in (("E", "BEV"), ("P", "PHEV"), ("H", "HEV")):
        assert (cap.battery_lifecycle_supported(alias)
                == cap.battery_lifecycle_supported(canonical))


def test_every_model_follows_capability_rules():
    """逐车型断言：电动化能力 / 座舱温控能力 / 纯油车不建 cevs 实体。"""
    cap = _load_capability()
    errors = []
    for brand, model, fuel, has_precond in MODEL_SAMPLES:
        status = _mock_status(fuel, has_precond)
        electrified = cap.battery_lifecycle_supported(fuel)
        expect_electrified = fuel.upper() in cap.EV_FUEL_TYPES

        # ① 电动化能力（cevs / 家充桩 / 动力电池类实体）由燃料类型决定
        if electrified != expect_electrified:
            errors.append(f"{brand} {model}: electrified={electrified} 期望 {expect_electrified}")

        # ② climate / select 的座舱温控门控：只认云端字段是否有值
        precond_on = cap.usable(status, PRECOND_PATHS)
        if precond_on is not has_precond:
            errors.append(f"{brand} {model}: precond_gate={precond_on} 期望 {has_precond}")

        # ③ 业务恒空不得翻转结论：cevs 端点有 200 响应，纯油车仍不创建
        if not expect_electrified and (status["chargeLogs"] or status["isEnabled"] != "Off"):
            errors.append(f"{brand} {model}: mock 形态不符合纯油车业务恒空")
        if not expect_electrified and precond_on:
            errors.append(f"{brand} {model}: 纯油/柴油车被判定为支持座舱温控")

    assert not errors, "\n".join(errors)


def test_lincoln_and_ford_gating_identical():
    """同燃料类型的福特与林肯车型，门控结论必须完全一致（品牌不得参与能力判定）。"""
    cap = _load_capability()
    for fuel in ("G", "D", "PHEV", "HEV", "BEV"):
        ford = [m for m in MODEL_SAMPLES if m[0] == "Ford" and m[2] == fuel]
        linc = [m for m in MODEL_SAMPLES if m[0] == "Lincoln" and m[2] == fuel]
        for _fb, f_model, _, f_pre in ford:
            for _lb, l_model, _, l_pre in linc:
                f_gate = cap.usable(_mock_status(fuel, f_pre), PRECOND_PATHS)
                l_gate = cap.usable(_mock_status(fuel, l_pre), PRECOND_PATHS)
                if f_pre == l_pre:
                    assert f_gate == l_gate, f"{f_model} vs {l_model}"

        # 电动化判定对两品牌同燃料类型一致
        assert cap.battery_lifecycle_supported(fuel) == (
            fuel in cap.EV_FUEL_TYPES)


def test_precond_gate_shared_by_climate_and_select():
    """climate 与 select 必须用同一条 preCond 判定表达式（口径不能分叉）。"""
    climate_src = (SRC / "climate.py").read_text(encoding="utf-8")
    select_src = (SRC / "select.py").read_text(encoding="utf-8")
    expr = 'capability.usable(status, [["preCondStatusDsply"]])'
    assert expr in climate_src, "climate 未按 preCond 门控"
    assert expr in select_src, "select 未按 preCond 门控"


def test_inline_fuel_tuples_match_authority():
    """源码里内联的电动化燃料元组必须与 EV_FUEL_TYPES 完全一致（防漂移）。"""
    cap = _load_capability()
    authority = set(cap.EV_FUEL_TYPES)
    pattern = re.compile(r'in \(\s*("E",\s*"BEV"[^)]*)\)')
    drift = []
    for path in SRC.glob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            m = pattern.search(line)
            if not m:
                continue
            items = set(re.findall(r'"([^"]+)"', m.group(1)))
            if items == authority:
                continue  # 电动化全集，与权威一致
            if items == {"E", "BEV"}:
                continue  # 纯电子集（纯电专属实体用，语义不同且必须是全集的子集）
            drift.append(f"{path.name}:{lineno} {sorted(items)}")
    assert not drift, "内联燃料元组与 EV_FUEL_TYPES 不一致: " + "; ".join(drift)
