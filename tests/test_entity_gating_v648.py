"""实体「按车型/能力创建」门控回归（v6.4.8）。

回归保护点：
- 远程 OTA 相关实体（OTA 激活排程按钮、OTA 设置状态传感器）必须按
  ``remoteOTAFlag=1`` 创建。此前只判 ``firmwareUpgInProgress`` 字段存在 /
  端点有响应，未开通远程 OTA 的车型会建出按下必失败的按钮与
  「未开通远程OTA」提示实体（实车 remoteOTAFlag=0 仍被创建）。
- 电池生命周期模式（``lifeCycMode``）属动力电池能力，仅电动化燃料类型
  （E/BEV/P/PHEV/H/HEV/MHEV）创建；纯油/柴油车拿到默认值 Normal 不得建。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "fordpass_cn"


def _load_capability():
    name = "fp_capability_gating"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SRC / "capability.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_ota_remote_enabled_true_variants():
    """开通位为 1 / "1" / True 时放行。"""
    cap = _load_capability()
    assert cap.ota_remote_enabled({"remoteOTAFlag": 1}) is True
    assert cap.ota_remote_enabled({"remoteOTAFlag": "1"}) is True
    assert cap.ota_remote_enabled({"remoteOTAFlag": True}) is True


def test_ota_remote_enabled_false_variants():
    """未开通、缺失、非 dict 一律不放行（不建实体）。"""
    cap = _load_capability()
    assert cap.ota_remote_enabled({"remoteOTAFlag": 0}) is False
    assert cap.ota_remote_enabled({"remoteOTAFlag": "0"}) is False
    assert cap.ota_remote_enabled({"remoteOTAFlag": None}) is False
    assert cap.ota_remote_enabled({"remoteOTAFlag": "Off"}) is False
    assert cap.ota_remote_enabled({}) is False
    assert cap.ota_remote_enabled(None) is False
    assert cap.ota_remote_enabled("not-a-dict") is False


def test_battery_lifecycle_only_electrified():
    """电动化燃料类型才支持；纯油/柴油/空值不支持。"""
    cap = _load_capability()
    for fuel in ("E", "BEV", "P", "PHEV", "H", "HEV", "MHEV",
                 "e", "phev", " MHEV "):
        assert cap.battery_lifecycle_supported(fuel) is True, fuel
    for fuel in ("G", "D", "", None, "GASOLINE", "DIESEL"):
        assert cap.battery_lifecycle_supported(fuel) is False, fuel
