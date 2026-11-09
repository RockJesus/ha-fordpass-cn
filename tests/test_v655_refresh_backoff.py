"""v6.5.6 回归测试：认证类 token 刷新失败后进入 60 秒退避窗口。

回归保护点（旧行为会被本测试判失败）：
- 旧实现：每次 ``_request`` 收到 401/600 都直接调用刷新端点；多车/多实体
  并发时每个协程各打一次，上游窗口式拒绝期间 4 分钟内日志刷 8 次同一错误。
- 新实现：失败一次后 ``_REFRESH_FAIL_BACKOFF`` 秒内不发云端请求，直接抛出
  同一结论（refresh_failed 语义不变，仍由上层触发 reauth）；成功即清除。

测试用 ``__new__`` 绕过真实构造，只验证包装层的退避语义。
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import sys
import types
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "fordpass_cn"


def _load_api():
    """以包上下文加载真实 api 模块（const/wbsk 均为纯 Python，无 HA 依赖）。"""
    if "fpbackoff" not in sys.modules:
        pkg = types.ModuleType("fpbackoff")
        pkg.__path__ = [str(SRC)]  # type: ignore[attr-defined]
        sys.modules["fpbackoff"] = pkg
    return importlib.import_module("fpbackoff.api")


api_mod = _load_api()
FordPassApi = api_mod.FordPassApi
FordPassApiError = api_mod.FordPassApiError


def _make_api(backoff: float = 0.03) -> FordPassApi:
    api = FordPassApi.__new__(FordPassApi)
    api._refresh_backoff_until = 0.0
    api._refresh_backoff_err = ""
    api._REFRESH_FAIL_BACKOFF = backoff
    api._log = logging.getLogger("test_refresh_backoff")
    return api


def test_auth_failure_enters_backoff_and_skips_cloud() -> None:
    asyncio.run(_auth_failure_enters_backoff_and_skips_cloud())


async def _auth_failure_enters_backoff_and_skips_cloud() -> None:
    """认证失败后窗口内不得再请求刷新端点。"""
    api = _make_api()
    calls: list[str] = []

    async def fail(rt: str) -> dict[str, str]:
        calls.append("fail")
        raise FordPassApiError(401, "gateway rejected", refresh_failed=True)

    api._refresh_token_locked = fail
    with pytest.raises(FordPassApiError) as first:
        await api.refresh_token("rt")
    assert first.value.refresh_failed is True
    assert api._refresh_backoff_until > 0

    async def success(rt: str) -> dict[str, str]:
        calls.append("ok")
        return {"access_token": "a-new"}

    api._refresh_token_locked = success
    with pytest.raises(FordPassApiError) as second:
        await api.refresh_token("rt")
    assert "backoff" in str(second.value)
    assert second.value.refresh_failed is True
    assert calls == ["fail"], "退避窗口内仍打到了刷新端点"


def test_backoff_expires_and_refresh_recovers() -> None:
    asyncio.run(_backoff_expires_and_refresh_recovers())


async def _backoff_expires_and_refresh_recovers() -> None:
    """退避过期后恢复正常刷新，并清除退避状态。"""
    api = _make_api()
    api._refresh_backoff_until = 0.0
    api._refresh_backoff_err = "old"

    async def success(rt: str) -> dict[str, str]:
        return {"access_token": "a-new"}

    api._refresh_token_locked = success
    result = await api.refresh_token("rt")
    assert result["access_token"] == "a-new"
    assert api._refresh_backoff_until == 0.0
    assert api._refresh_backoff_err == ""


def test_non_auth_failure_does_not_enter_backoff() -> None:
    asyncio.run(_non_auth_failure_does_not_enter_backoff())


async def _non_auth_failure_does_not_enter_backoff() -> None:
    """网关 5xx / 网络异常不进入退避（由 ConfigEntryNotReady 路径重试）。"""
    api = _make_api()

    async def gateway_down(rt: str) -> dict[str, str]:
        raise FordPassApiError(500, "gateway 500", refresh_failed=False)

    api._refresh_token_locked = gateway_down
    with pytest.raises(FordPassApiError):
        await api.refresh_token("rt")
    assert api._refresh_backoff_until == 0.0


def test_backoff_wall_clock_matches_constant() -> None:
    asyncio.run(_backoff_wall_clock_matches_constant())


async def _backoff_wall_clock_matches_constant() -> None:
    """默认窗口为 60 秒（与发布说明一致）。"""
    api = _make_api(backoff=FordPassApi._REFRESH_FAIL_BACKOFF)
    assert api._REFRESH_FAIL_BACKOFF == 60.0

    async def fail(rt: str) -> dict[str, str]:
        raise FordPassApiError(401, "nope", refresh_failed=True)

    api._refresh_token_locked = fail
    with pytest.raises(FordPassApiError):
        await api.refresh_token("rt")
    import time as _t

    remaining = api._refresh_backoff_until - _t.monotonic()
    assert 55.0 <= remaining <= 60.5
