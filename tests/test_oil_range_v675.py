"""机油剩余可行驶里程改名 + 机油归零预计日期实体（v6.7.5）。

背景
----
预测性诊断端点（prognostic）一次返回三件相关数据：
- ``iolm``：机油寿命百分比；
- ``remainingKMs``：机油寿命归零前还能跑的公里数（此前实体名「剩余可行驶里程」
  极易与燃油续航空 ``distanceToEmpty`` 混淆，本版改名「机油剩余可行驶里程」）；
- ``dateOnZero``：机油归零预计年月（实测形如 ``2027/01``），本版独立成实体。

回归保护点
-----------
1. ``capability.zero_month`` 只接受合法年月，缺月份 / 越界 / 占位串返回 None，
   不得把「只有年份」的值编造成 1 月，也不得把占位串当日期。
2. 新实体走翻译键 ``oil_zero_date``（中英双语），创建条件是云端 ``dateOnZero``
   有值——无该字段的车型不创建，不留恒 unknown 的假实体。
3. 改名只改显示名（翻译值），``unique_id`` 保持 ``prognostic_range`` 不变
   ——升级后实体 ID 与历史统计不断链。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "custom_components" / "fordpass_cn"


def _load_capability():
    name = "fp_capability_zero_month"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SRC / "capability.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_zero_month_accepts_real_cloud_formats():
    cap = _load_capability()
    cases = {
        "2027/01": "2027-01",       # 实测格式
        "2027-01": "2027-01",
        "2027.01": "2027-01",
        "2027/1": "2027-01",
        "2027/01/15": "2027-01",
        "2027-01-15": "2027-01",
        "2027年01月": "2027-01",
        " 2027/01 ": "2027-01",
        "2026/12": "2026-12",
    }
    for raw, want in cases.items():
        assert cap.zero_month(raw) == want, raw


def test_zero_month_rejects_ambiguous_or_placeholder():
    cap = _load_capability()
    for raw in (None, "", "null", "None", "N/A", "2027", "27/01", "2027/13",
                "2027/00", "1979/12", "2101/01", "Not_Supported", "abc"):
        assert cap.zero_month(raw) is None, raw


def test_new_sensor_uses_translation_key_and_gate():
    src = (SRC / "sensor.py").read_text(encoding="utf-8")
    assert '"oil_zero_date"' in src, "翻译键白名单缺 oil_zero_date"
    assert 'uid="oil_zero_date"' in src, "实体未使用 oil_zero_date 翻译键"
    assert 'prog_payload.get("dateOnZero") is not None' in src, "创建条件未按 dateOnZero 门控"
    assert "FordPassOilZeroDateSensor(coordinator)" in src, "创建列表未加入新实体"


def test_range_sensor_renamed_without_changing_unique_id():
    src = (SRC / "sensor.py").read_text(encoding="utf-8")
    assert '"机油剩余可行驶里程"' in src, "标签未改名"
    assert 'uid="prognostic_range"' in src, "unique_id 不得变化（实体 ID 与统计不能断链）"


def test_translations_bilingual():
    for fname, want in (("zh-Hans.json", "机油归零预计日期"),
                        ("en.json", "Oil Life Zero Date")):
        data = json.loads((SRC / "translations" / fname).read_text(encoding="utf-8"))
        node = data["entity"]["sensor"]["oil_zero_date"]["name"]
        assert node == want, f"{fname} -> {node}"
    # strings.json（HA 翻译源）与 zh-Hans 保持一致
    strings = json.loads((SRC / "strings.json").read_text(encoding="utf-8"))
    assert strings["entity"]["sensor"]["oil_zero_date"]["name"] == "机油归零预计日期"
    assert strings["entity"]["sensor"]["prognostic_range"]["name"] == "机油剩余可行驶里程"
    # v6.7.3 的 climate 键此前只进了 translations，strings.json 需同步
    assert strings["entity"]["climate"]["remote_climate"]["name"] == "远程空调"
