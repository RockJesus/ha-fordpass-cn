"""v6.7.6 回归：重启加载（setup）也要按「首选品牌 → 另一品牌」兜底取车。

v6.7.0 的跨品牌纠正只在添加流程生效，条目一旦落盘（旧版本添加的、或添加时
自动切换未落盘的），setup 只查条目里存的品牌；品牌与实际归属不符时会每次
重启都报「账号下未发现车辆」。本版把同一套兜底搬到 setup，并覆盖三条不变量：

1. 首选品牌有车 → 原样返回，不改条目品牌（不打扰正常账号）；
2. 首选为空、另一品牌有车 → 返回另一品牌及其 api，调用方据此更新条目；
3. 都为空 / 请求失败 → 返回首选品牌与空列表，由调用方按「确实没有车」处理，
   单品牌异常不得把 setup 带崩。
"""
from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "fordpass_cn"
FORD = "fordpass"
LINCOLN = "lincoln"


def _install() -> types.ModuleType:
    name = "fp676.brand_probe"
    if name in sys.modules:
        return sys.modules[name]
    pkg = types.ModuleType("fp676")
    pkg.__path__ = [str(SRC)]  # type: ignore[attr-defined]
    sys.modules["fp676"] = pkg
    const = types.ModuleType("fp676.const")
    const.BRAND_FORD = FORD
    const.BRAND_LINCOLN = LINCOLN
    sys.modules["fp676.const"] = const
    module = importlib.import_module(name)
    sys.modules[name] = module
    return module


class _Api:
    def __init__(self, brand: str, vehicles: list, raise_it: BaseException | None = None) -> None:
        self.brand = brand
        self.vehicles = vehicles
        self.raise_it = raise_it
        self.calls = 0

    async def get_vehicles(self):
        self.calls += 1
        if self.raise_it is not None:
            raise self.raise_it
        return self.vehicles


def _factory(vehicles_by_brand: dict, raises: dict | None = None):
    raises = raises or {}
    made: dict[str, _Api] = {}

    def factory(brand: str) -> _Api:
        api = _Api(brand, vehicles_by_brand.get(brand, []), raises.get(brand))
        made[brand] = api
        return api

    factory.made = made  # type: ignore[attr-defined]
    return factory


def test_preferred_brand_with_vehicle_is_returned_untouched():
    bp = _install()
    factory = _factory({FORD: [{"vin": "x"}], LINCOLN: []})
    brand, vehicles, api = asyncio.run(bp.vehicles_with_brand_fallback(factory, FORD))
    assert brand == FORD
    assert vehicles == [{"vin": "x"}]
    assert api.brand == FORD
    assert LINCOLN not in factory.made, "首选品牌已有车时不应再去查另一品牌"


def test_empty_preferred_falls_back_to_other_brand():
    bp = _install()
    factory = _factory({FORD: [], LINCOLN: [{"vin": "x"}]})
    brand, vehicles, api = asyncio.run(bp.vehicles_with_brand_fallback(factory, FORD))
    assert brand == LINCOLN
    assert vehicles == [{"vin": "x"}]
    assert api.brand == LINCOLN, "返回的 api 必须是另一品牌的实例，供 setup 继续使用"


def test_both_empty_returns_preferred_with_empty_list():
    bp = _install()
    factory = _factory({FORD: [], LINCOLN: []})
    brand, vehicles, api = asyncio.run(bp.vehicles_with_brand_fallback(factory, LINCOLN))
    assert brand == LINCOLN
    assert vehicles == []
    assert api.brand == LINCOLN


def test_preferred_failure_does_not_block_other_brand():
    bp = _install()
    factory = _factory({FORD: [], LINCOLN: [{"vin": "x"}]},
                       raises={FORD: RuntimeError("gateway 500")})
    brand, vehicles, _api = asyncio.run(bp.vehicles_with_brand_fallback(factory, FORD))
    assert brand == LINCOLN
    assert vehicles == [{"vin": "x"}]


def test_both_failures_are_swallowed():
    bp = _install()
    factory = _factory({}, raises={FORD: TimeoutError("slow"), LINCOLN: RuntimeError("nope")})
    brand, vehicles, _api = asyncio.run(bp.vehicles_with_brand_fallback(factory, FORD))
    assert brand == FORD
    assert vehicles == []


def test_setup_wires_fallback_and_updates_entry_brand():
    init_src = (SRC / "__init__.py").read_text(encoding="utf-8")
    assert "vehicles_with_brand_fallback" in init_src, "setup 未接入品牌兜底"
    assert "entry, data={**entry.data, CONF_BRAND: _brand}" in init_src, (
        "纠正品牌时必须写回 entry，否则下次重启又回到错误品牌"
    )
    assert "两个品牌均已尝试" in init_src or "品牌均已尝试" in init_src, "报错文案未说明已试两品牌"
