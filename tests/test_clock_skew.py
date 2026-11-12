"""v6.6.6 回归测试：云端时间窗拒绝要能自愈，不能当凭据失效。

回归保护点（旧行为会被本测试判失败）：
- 旧实现把 100400 / timestamp is out of range 与 401 一起归为认证失败
  （refresh_failed=True）→ setup 抛 ConfigEntryAuthFailed → 条目卡在
  setup_error、78 个实体全部 unavailable，只能人工 reload。
- 旧实现只在日志里报警时钟偏差，签名时间戳仍用本机时间，被拉偏的主机
  永远刷不上 token。

2026-11-12 实测背景：HA 重启瞬间主机时钟慢 2359 秒，首轮 token 刷新被
云端按 t-2m ~ t+8m 窗口拒掉。
"""
from __future__ import annotations

import asyncio
import email.utils
import importlib
import logging
import sys
import time
import types
from pathlib import Path
from typing import Any

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "fordpass_cn"


def _ensure_aiohttp() -> None:
    """CI 精简环境没有 aiohttp——api 模块顶层 import 会整文件收集失败。"""
    try:
        import aiohttp  # noqa: F401

        return
    except ModuleNotFoundError:
        pass
    stub = types.ModuleType("aiohttp")

    class _ClientTimeout:
        def __init__(self, *a, **kw):
            self.args = a
            self.kwargs = kw

    class _ClientSession:
        def __init__(self, *a, **kw):
            self.args = a
            self.kwargs = kw

    stub.ClientTimeout = _ClientTimeout
    stub.ClientSession = _ClientSession
    sys.modules["aiohttp"] = stub


_ensure_aiohttp()


def _load_api():
    if "fpclockskew" not in sys.modules:
        pkg = types.ModuleType("fpclockskew")
        pkg.__path__ = [str(SRC)]  # type: ignore[attr-defined]
        sys.modules["fpclockskew"] = pkg
    return importlib.import_module("fpclockskew.api")


api_mod = _load_api()
FordPassApi = api_mod.FordPassApi
FordPassApiError = api_mod.FordPassApiError

SKEW_TEXT = (
    "token refresh returned no token fields(status=600, "
    "error=timestamp is out of range: t-2m ~ t+8m, errorCode=100400)"
)


def _make_api() -> FordPassApi:
    api = FordPassApi.__new__(FordPassApi)
    api.clock_offset_seconds = None
    api._clock_warned_at = 0.0
    api._clock_compensation = 0.0
    api._clock_reject_warned_at = 0.0
    api._log = logging.getLogger("test_clock_skew")
    return api


def test_is_clock_skew_matches_gateway_wording() -> None:
    assert api_mod._is_clock_skew(SKEW_TEXT) is True
    assert api_mod._is_clock_skew("TIMESTAMP IS OUT OF RANGE") is True
    assert api_mod._is_clock_skew("errorCode=100400") is True
    assert api_mod._is_clock_skew("token refresh failed (401 after retry)") is False
    assert api_mod._is_clock_skew(None) is False


def test_refresh_failure_keeps_clock_skew_retryable() -> None:
    """时间窗拒绝不得被包装成 401/refresh_failed（否则整条目 setup_error）。"""
    wrapped = api_mod._as_refresh_failed(FordPassApiError(600, SKEW_TEXT))
    assert wrapped.code == 600
    assert wrapped.refresh_failed is False
    assert wrapped.clock_skew is True


def test_refresh_failure_real_auth_still_flags_reauth() -> None:
    wrapped = api_mod._as_refresh_failed(FordPassApiError(600, "device locked"))
    assert wrapped.code == 401
    assert wrapped.refresh_failed is True


def test_clock_skew_flag_propagates_from_exception_object() -> None:
    wrapped = api_mod._as_refresh_failed(
        FordPassApiError(600, "gateway said no", clock_skew=True)
    )
    assert wrapped.clock_skew is True
    assert wrapped.refresh_failed is False


def test_signed_timestamp_uses_compensation() -> None:
    api = _make_api()
    now = 1_700_000_000.0
    assert api._signed_ts_ms(now) == int(now * 1000)
    api._clock_compensation = 2359.0  # 本机比云端慢时补正
    assert api._signed_ts_ms(now) == int((now + 2359.0) * 1000)


def test_note_clock_measures_offset() -> None:
    api = _make_api()
    local = time.time()
    header = email.utils.formatdate(local - 2359.0, usegmt=True)
    api._note_clock(header)
    assert api.clock_offset_seconds is not None
    assert abs(api.clock_offset_seconds - 2359.0) < 3.0


def test_clock_reject_sets_compensation_and_returns_true() -> None:
    api = _make_api()
    api.clock_offset_seconds = -2359.0  # 本机比云端慢
    assert api._note_clock_reject(SKEW_TEXT) is True
    assert api._clock_compensation == 2359.0


def test_non_skew_reject_does_not_compensate() -> None:
    api = _make_api()
    api.clock_offset_seconds = -2359.0
    assert api._note_clock_reject("12345 车辆不存在") is False
    assert api._clock_compensation == 0.0


class _FakeResp:
    def __init__(self, status: int, body: str, date: str) -> None:
        self.status = status
        self._body = body
        self.headers = {"Date": date, "Content-Type": "application/json"}

    async def text(self) -> str:
        return self._body

    async def read(self) -> bytes:
        return self._body.encode()


class _FakeSession:
    """按顺序回放响应，并记录每次请求的签名时间戳。"""

    def __init__(self, responses: list[_FakeResp]) -> None:
        self._responses = responses
        self.timestamps: list[int] = []

    def request(self, method: str, url: str, **kwargs: Any):
        payload = kwargs.get("json") or kwargs.get("params") or {}
        self.timestamps.append(int(payload.get("timestamp", 0)))
        resp = self._responses.pop(0)

        class _Ctx:
            async def __aenter__(self):
                return resp

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def test_request_retries_once_after_clock_compensation(monkeypatch) -> None:
    """时间窗拒绝 → 按 Date 头补偿后重发一次，成功返回。"""
    monkeypatch.setattr(api_mod, "compute_sign", lambda params: "sig")
    local = time.time()
    # 实测场景：HA 主机时钟慢 39 分钟 → 云端 Date 比本机早 2359 秒
    server_date = email.utils.formatdate(local + 2359.0, usegmt=True)
    api = _make_api()
    api._access_token = "tok"
    api._refresh_token = None
    api._session = _FakeSession(
        [
            _FakeResp(600, SKEW_TEXT, server_date),
            _FakeResp(200, '{"ok": true}', email.utils.formatdate(local, usegmt=True)),
        ]
    )
    api._app_version = "9.9.9"
    api._client_type = "FP"

    result = asyncio.run(
        api._request("GET", "/api/test", query={"vin": "VIN123"})
    )
    assert result == {"ok": True}
    assert len(api._session.timestamps) == 2, "时间窗拒绝必须重试一次"
    delta = api._session.timestamps[1] - api._session.timestamps[0]
    assert abs(delta - 2359 * 1000) < 5000, "重试时间戳应带时钟补偿"


def test_request_raises_clock_skew_error_when_retry_also_fails(monkeypatch) -> None:
    """补偿重试仍被拒 → 抛 clock_skew=True 的错误（上层走 NotReady 而非 reauth）。"""
    monkeypatch.setattr(api_mod, "compute_sign", lambda params: "sig")
    local = time.time()
    # 实测场景：HA 主机时钟慢 39 分钟 → 云端 Date 比本机早 2359 秒
    server_date = email.utils.formatdate(local + 2359.0, usegmt=True)
    api = _make_api()
    api._access_token = "tok"
    api._refresh_token = None
    api._session = _FakeSession(
        [
            _FakeResp(600, SKEW_TEXT, server_date),
            _FakeResp(600, SKEW_TEXT, server_date),
        ]
    )
    api._app_version = "9.9.9"
    api._client_type = "FP"

    try:
        asyncio.run(api._request("GET", "/api/test", query={"vin": "VIN123"}))
    except FordPassApiError as err:
        assert err.clock_skew is True
        assert err.refresh_failed is False
    else:  # pragma: no cover - 必须抛错
        raise AssertionError("补偿重试仍失败时必须抛错")
