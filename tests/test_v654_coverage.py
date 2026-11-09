"""v6.5.4 覆盖回归：鸣笛恢复默认 / 告警条数 / 下次保养里程 / 探测形态。

用 AST + 轻量假模块，不加载 homeassistant 真实包（与既有测试同路数）。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from unittest.mock import MagicMock

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "fordpass_cn"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_last_poll_sensor import _install_fake_modules  # noqa: E402


def _trees():
    return {n: ast.parse((SRC / n).read_text(encoding="utf-8"))
            for n in ("api.py", "sensor.py", "button.py")}


# ---------------------------------------------------------------- 探测形态
def test_vehicle_level_probes_carry_vin_post_body():
    """4 个车辆级端点必须带 POST body 模板（{"vin": None}）。

    此前 3D/流量/质保/车辆信息只试 GET（或空 body POST），车辆级查询
    缺车辆参数被网关拒绝，实体永不创建——本测试锁住形态。
    """
    tree = _trees()["api.py"]

    def find_dict(prefix: str):
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict) and node.keys:
                first = node.keys[0]
                if (isinstance(first, ast.Constant)
                        and isinstance(first.value, str)
                        and first.value.startswith(prefix)):
                    return node
        return None

    reg = find_dict("maintenance-history")
    assert reg is not None
    specs = {}
    for k, v in zip(reg.keys, reg.values):
        if isinstance(k, ast.Constant) and isinstance(v, ast.Tuple):
            specs[k.value] = v
    for key in ("3d_model", "cmcc_traffic", "warranty", "vehicle_info"):
        tup = specs[key]
        body = tup.elts[1]
        allow_post = tup.elts[2]
        assert isinstance(body, ast.Dict) and body.keys, key
        assert isinstance(allow_post, ast.Constant) and allow_post.value is True, key
        names = {e.value for e in body.keys if isinstance(e, ast.Constant)}
        assert "vin" in names, f"{key} body 缺 vin"


# ---------------------------------------------------------------- 告警条数
def _load_sensor():
    _install_fake_modules()
    mod = sys.modules["fpcn.sensor"]
    cap = sys.modules["fpcn.capability"]
    # alert 属性会调用时间统一函数；测试里原样透传，聚焦计数口径
    cap.local_str_to_iso = lambda v: v
    return mod


def _coord(data):
    c = MagicMock()
    c.uid_key = "VIN123"
    c.data = data
    return c


def test_alert_count_keeps_raw_and_dedup():
    mod = _load_sensor()
    alerts = [
        {"headline": "胎压监测系统警告", "severity": 3,
         "eventTime": "2026-11-09T10:00:34+08:00"},
        {"headline": "胎压监测系统警告", "severity": 3,
         "eventTime": "2026-11-09T10:00:34+08:00"},
    ]
    ent = mod.FordPassAlertSensor(_coord({"active_alerts": alerts}))
    attrs = ent.extra_state_attributes
    # App 「爱车自检」按条目显示 2 项；HA 主值去重，属性保留两种口径
    assert attrs["alert_count"] == 2
    assert attrs["unique_alert_count"] == 1
    assert len(attrs["alerts"]) == 1
    assert attrs["latest_alert_time"] == "2026-11-09T10:00:34+08:00"


# ---------------------------------------------------------------- 下次保养里程
def test_maintenance_summary_next_mileage():
    mod = _load_sensor()
    coord = _coord({
        "vehiclestatus": {"odometer": {"value": 60729}},
        "maintenance_plan": {"data": {"values": [
            {"mileage": 60000, "operations": [{"description": "机油"}]},
            {"mileage": 65000, "operations": [{"description": "蓄电池（6个月)"}]},
            {"mileage": 70000, "operations": [{"description": "制动系统"}]},
        ]}},
    })
    ent = mod.FordPassMaintenancePlanSensor(coord)
    summary = ent._summary(coord.data["maintenance_plan"])
    # 下一档 = 第一档 >= 当前总里程
    assert summary["next_mileage"] == 65000
    assert summary["remaining_km"] == 65000 - 60729


def test_maintenance_summary_without_odometer():
    mod = _load_sensor()
    coord = _coord({
        "maintenance_plan": {"data": {"values": [
            {"mileage": 5000, "operations": [{"description": "首保"}]},
        ]}},
    })
    ent = mod.FordPassMaintenancePlanSensor(coord)
    summary = ent._summary(coord.data["maintenance_plan"])
    assert summary["next_mileage"] == 5000
    assert "remaining_km" not in summary


# ---------------------------------------------------------------- 鸣笛恢复默认
def test_honk_reset_button_contract():
    """按钮契约：固定 unique_id / 中文名 / 门控 / 调用云端保存。"""
    src_text = (SRC / "button.py").read_text(encoding="utf-8")
    tree = ast.parse(src_text)
    classes = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    cls = classes["FordPassHonkResetButton"]
    src = ast.get_source_segment(src_text, cls)
    assert "honk_reset" in src
    assert 'self._attr_name = "鸣笛设置恢复默认"' in src
    assert "save_honk_settings" in src
    assert "DEFAULT_HONK_DURATION" in src and "DEFAULT_CHIRP_TYPE" in src
    # 创建点与两个 select 同门控
    assert ('capability.usable(status, [["crccFlag"]]):\n'
            "            buttons.append(FordPassHonkResetButton") in src_text
