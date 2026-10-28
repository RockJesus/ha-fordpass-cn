"""coordinator TTL 缓存测试（不依赖 Home Assistant 运行时）。

v5.9.9 回归保护点：
- TTL 命中不再真拉（既有行为）；
- 请求**成功但返回 None** 也写短 TTL 缓存（此前永不命中 → 每轮真拉）；
- 请求**失败**时把旧值写回缓存并续期（此前 expire 留在过去 → 每轮重试）；
- 失败续期不得缩短 force 路径原缓存剩余的未过期 TTL；
- FordPassProbeRejected（确定性能力缺失）仍按完整 TTL 缓存 None。
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
import time
import types
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "fordpass_cn"


def _install_fake_modules() -> None:
    if "fpcnc.coord" in sys.modules:
        return

    def mk(name: str, **attrs: Any) -> types.ModuleType:
        module = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        sys.modules[name] = module
        return module

    class _UpdateFailed(Exception):
        pass

    class _ConfigEntryAuthFailed(Exception):
        pass

    class _DataUpdateCoordinator:
        """仅够 coordinator.py 完成类定义（测试用 __new__ 绕过 __init__）。"""

        __class_getitem__ = classmethod(lambda cls, item: cls)

        def __init__(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
            raise RuntimeError("tests construct via __new__")

    class _FakeStore:
        def __init__(self) -> None:
            self.saved: dict | None = None

        async def async_save(self, data: dict) -> None:
            self.saved = data

    for name in ("homeassistant", "homeassistant.helpers", "homeassistant.util"):
        mk(name)
    mk("homeassistant.core", HomeAssistant=object, callback=lambda f: f)
    mk(
        "homeassistant.exceptions",
        ConfigEntryAuthFailed=_ConfigEntryAuthFailed,
        HomeAssistantError=Exception,
    )
    mk("homeassistant.helpers.device_registry")
    mk("homeassistant.helpers.storage", Store=_FakeStore)
    mk(
        "homeassistant.helpers.update_coordinator",
        DataUpdateCoordinator=_DataUpdateCoordinator,
        UpdateFailed=_UpdateFailed,
    )
    _fake_dt = mk("homeassistant.util.dt")
    _fake_dt.now = lambda tz=None: datetime.now(tz or timezone.utc)
    sys.modules["homeassistant.util"].dt = _fake_dt

    pkg = mk("fpcnc")
    pkg.__path__ = [str(SRC)]  # type: ignore[attr-defined]

    class _ProbeRejected(Exception):
        pass

    api = mk(
        "fpcnc.api",
        FordPassApi=object,
        FordPassApiError=Exception,
        FordPassProbeRejected=_ProbeRejected,
    )
    # ProbeRejected 需要与 coordinator 内 except 匹配：普通 Exception 子类即可
    api.FordPassProbeRejected = _ProbeRejected

    spec = importlib.util.spec_from_file_location(
        "fpcnc.coord", SRC / "coordinator.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["fpcnc.coord"] = module
    spec.loader.exec_module(module)


def load_coord():
    _install_fake_modules()
    return sys.modules["fpcnc.coord"]


def make_coord() -> Any:
    mod = load_coord()
    coord = mod.FordPassCoordinator.__new__(mod.FordPassCoordinator)
    coord._cache = {}
    coord.account_cache = None
    coord._account_locks = {}
    coord._probe_cache = {}
    coord._probe_store = None
    coord.logger = logging.getLogger("fpcnc-test")
    return coord


def test_ttl_hit_does_not_refetch() -> None:
    coord = make_coord()
    calls = []

    async def factory():
        calls.append(1)
        return "v1"

    async def run():
        assert await coord._cached_fetch("k", 3600, factory) == "v1"
        assert await coord._cached_fetch("k", 3600, factory) == "v1"

    asyncio.run(run())
    assert len(calls) == 1


def test_none_success_is_cached_short() -> None:
    """成功但云端返回 null：短 TTL 内不再真拉（v5.9.9 修复）。"""
    coord = make_coord()
    calls = []

    async def factory():
        calls.append(1)
        return None

    async def run():
        assert await coord._cached_fetch("k", 21600, factory) is None
        assert await coord._cached_fetch("k", 21600, factory) is None
        ent = coord._cache["k"]
        left = ent[0] - time.monotonic()
        assert 3500 < left <= 3600.5, left

    asyncio.run(run())
    assert len(calls) == 1


def test_failure_retries_then_backoff() -> None:
    """失败序列：无旧值重试 → 成功 → 过期后失败回退旧值并续期。"""
    coord = make_coord()
    calls = []
    state = {"fail": True}

    async def factory():
        calls.append(1)
        if state["fail"]:
            raise RuntimeError("cloud down")
        return "good"

    async def run():
        # 第 1 次：失败、无旧值 → None，不缓存（下轮仍会重试）
        assert await coord._cached_fetch("k", 21600, factory) is None
        assert "k" not in coord._cache
        # 第 2 次：成功
        state["fail"] = False
        assert await coord._cached_fetch("k", 21600, factory) == "good"
        # 第 3 次：TTL 过期 + 云端又故障 → 回退旧值并续期 ~1h
        coord._cache["k"] = (time.monotonic() - 1, "good")
        state["fail"] = True
        assert await coord._cached_fetch("k", 21600, factory) == "good"
        left = coord._cache["k"][0] - time.monotonic()
        assert 3500 < left <= 3600.5, left
        # 第 4 次：命中退避缓存，不再真拉
        n = len(calls)
        assert await coord._cached_fetch("k", 21600, factory) == "good"
        assert len(calls) == n

    asyncio.run(run())


def test_force_failure_never_shortens_remaining_ttl() -> None:
    """force 失败：回退旧值，但不得把未过期的长 TTL 缩短到 1h。"""
    coord = make_coord()
    remain = 100000.0
    coord._cache["k"] = (time.monotonic() + remain, "old")

    async def factory():
        raise RuntimeError("down")

    async def run():
        assert await coord._cached_fetch("k", 21600, factory, force=True) == "old"
        left = coord._cache["k"][0] - time.monotonic()
        assert left > remain - 5, left

    asyncio.run(run())


def test_probe_rejected_caches_full_ttl() -> None:
    """确定性能力缺失（业务拒绝/404）仍按完整 TTL 缓存 None。"""
    coord = make_coord()
    rejected = sys.modules["fpcnc.api"].FordPassProbeRejected("404")
    calls = []

    async def factory():
        calls.append(1)
        raise rejected

    async def run():
        assert await coord._cached_fetch("k", 7200, factory) is None
        assert await coord._cached_fetch("k", 7200, factory) is None
        left = coord._cache["k"][0] - time.monotonic()
        assert 7100 < left <= 7200.5, left

    asyncio.run(run())
    assert len(calls) == 1


def test_account_cache_hit() -> None:
    coord = make_coord()
    coord.account_cache = {}
    calls = []

    async def factory():
        calls.append(1)
        return {"p": 1}

    async def run():
        assert await coord._cached_fetch("chirp_cloud", 600, factory, account=True) == {"p": 1}
        assert await coord._cached_fetch("chirp_cloud", 600, factory, account=True) == {"p": 1}

    asyncio.run(run())
    assert len(calls) == 1
