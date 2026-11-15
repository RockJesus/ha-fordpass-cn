"""v6.7.0 回归：添加集成时按「首选品牌 → 另一品牌」判定车辆归属。

品牌选错时登录照样成功、车辆列表却是空的，用户只能看到「设置失败：账号下
未发现车辆」。判定逻辑必须满足：首选品牌有车就不打扰；首选为空且另一品牌
有车时自动切换；都为空返回 None 交由流程明确中止；单品牌请求失败不能中断
另一品牌的尝试。
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "fordpass_cn"


def _install() -> types.ModuleType:
    name = "fp670.brand_probe"
    if name in sys.modules:
        return sys.modules[name]
    pkg = types.ModuleType("fp670")
    pkg.__path__ = [str(SRC)]  # type: ignore[attr-defined]
    sys.modules["fp670"] = pkg
    const = types.ModuleType("fp670.const")
    const.BRAND_FORD = "fordpass"
    const.BRAND_LINCOLN = "lincoln"
    sys.modules["fp670.const"] = const
    import importlib

    module = importlib.import_module(name)
    sys.modules[name] = module
    return module


class _Api:
    def __init__(self, brand: str, vehicles: list, raise_it: BaseException | None = None) -> None:
        self.brand = brand
        self.vehicles = vehicles
        self.raise_it = raise_it

    async def get_vehicles(self):
        if self.raise_it is not None:
            raise self.raise_it
        return self.vehicles


def _factory(mapping: dict[str, _Api]):
    calls: list[str] = []

    def make(brand: str) -> _Api:
        calls.append(brand)
        return mapping[brand]

    return make, calls


def test_preferred_brand_with_vehicle_wins():
    mod = _install()
    make, calls = _factory({
        "fordpass": _Api("fordpass", [{"vin": "VIN123"}]),
        "lincoln": _Api("lincoln", [{"vin": "VIN123"}]),
    })
    got = asyncio.run(mod.brand_with_vehicles(make, "fordpass"))
    assert got == ("fordpass", True)
    assert calls == ["fordpass"], "首选品牌有车时不应再探测另一品牌"


def test_falls_back_to_other_brand():
    mod = _install()
    make, calls = _factory({
        "fordpass": _Api("fordpass", []),
        "lincoln": _Api("lincoln", [{"vin": "VIN123"}]),
    })
    got = asyncio.run(mod.brand_with_vehicles(make, "fordpass"))
    assert got == ("lincoln", True)
    assert calls == ["fordpass", "lincoln"]


def test_both_empty_is_reported_as_known_empty():
    """两个品牌都返回空列表 → (None, True)，调用方可以判定「确实没车」。"""
    mod = _install()
    make, _ = _factory({
        "fordpass": _Api("fordpass", []),
        "lincoln": _Api("lincoln", []),
    })
    assert asyncio.run(mod.brand_with_vehicles(make, "fordpass")) == (None, True)


def test_both_empty_returns_none():
    mod = _install()
    make, calls = _factory({
        "fordpass": _Api("fordpass", []),
        "lincoln": _Api("lincoln", []),
    })
    got, reachable = asyncio.run(mod.brand_with_vehicles(make, "lincoln"))
    assert got is None and reachable is True
    assert calls == ["lincoln", "fordpass"]


def test_request_failure_does_not_block_other_brand():
    """令牌不适用于另一品牌/网关拒绝时，另一品牌仍要被尝试。"""
    mod = _install()
    make, calls = _factory({
        "fordpass": _Api("fordpass", [], raise_it=RuntimeError("401 from alt brand")),
        "lincoln": _Api("lincoln", [{"vin": "VIN123"}]),
    })
    got = asyncio.run(mod.brand_with_vehicles(make, "fordpass"))
    assert got == ("lincoln", True)
    assert calls == ["fordpass", "lincoln"]


def test_all_failed_returns_none():
    mod = _install()
    make, _ = _factory({
        "fordpass": _Api("fordpass", [], raise_it=RuntimeError("boom")),
        "lincoln": _Api("lincoln", [], raise_it=RuntimeError("boom")),
    })
    # 全部请求失败 → (None, False)：不能误判成「账号没有车」
    assert asyncio.run(mod.brand_with_vehicles(make, "fordpass")) == (None, False)


def test_alternate_brand_is_symmetric():
    mod = _install()
    assert mod.alternate_brand("fordpass") == "lincoln"
    assert mod.alternate_brand("lincoln") == "fordpass"
    assert mod.alternate_brand("weird") == "fordpass"
