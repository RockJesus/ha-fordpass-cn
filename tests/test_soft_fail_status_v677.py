"""v6.7.7 回归测试：车辆状态端点失败后改为软失败，不再中断整轮刷新。

回归保护点（旧行为会被本测试判失败）：
1. ``get_vehicle_status`` 抛普通业务 ``FordPassApiError``（非凭据永久失效）
   → 不再 ``raise UpdateFailed``；保留上一轮状态快照，后续端点照常刷新。
2. 超时 ``TimeoutError`` 同样软失败，回填旧快照并继续后续端点。
3. 凭据永久失效（``refresh_failed`` 的 401/403）仍抛 ``ConfigEntryAuthFailed``，
   不误停轮询的判定不受影响。
4. 成功路径：新快照正常写入 ``status`` / ``vehiclestatus``。

测试用轻量桩替代 HA 运行时（沿用 test_command_reconcile 的同款做法）。
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import sys
import types
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "fordpass_cn"


def _mk(name: str, **attrs) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


class _FakeDataUpdateCoordinator:
    __class_getitem__ = classmethod(lambda cls, _item: cls)

    async def async_shutdown(self) -> None:
        return None


def _now():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))


class _ApiError(Exception):
    """桩异常：对齐 FordPassApiError 的 code / refresh_failed 属性。"""

    def __init__(self, code=None, message=None, refresh_failed=False, **kw):
        super().__init__(message or f"error {code}")
        self.code = code
        self.refresh_failed = refresh_failed


class _AuthFailed(Exception):
    pass


def _install_fakes() -> None:
    if "fpv7.coordinator" in sys.modules:
        return

    if "homeassistant" not in sys.modules:
        _mk("homeassistant")
    sys.modules["homeassistant"].__path__ = []  # type: ignore[attr-defined]

    def need(name: str, **attrs):
        if name not in sys.modules:
            _mk(name, **attrs)
            return sys.modules[name]
        module = sys.modules[name]
        for key, value in attrs.items():
            if not hasattr(module, key):
                setattr(module, key, value)
        return module

    need("homeassistant.core", HomeAssistant=object)

    need("homeassistant.exceptions", ConfigEntryAuthFailed=_AuthFailed)
    need("homeassistant.helpers")
    need("homeassistant.helpers.device_registry", DeviceInfo=dict)
    need("homeassistant.helpers.storage", Store=object)
    need(
        "homeassistant.helpers.update_coordinator",
        DataUpdateCoordinator=_FakeDataUpdateCoordinator,
        UpdateFailed=type("_UpdateFailed", (Exception,), {}),
    )
    need("homeassistant.util")
    _dt = need("homeassistant.util.dt")
    _dt.now = _now
    _dt.utcnow = lambda: datetime.datetime.now(datetime.timezone.utc)
    if not hasattr(_dt, "parse_datetime"):
        _dt.parse_datetime = lambda value: datetime.datetime.fromisoformat(str(value))
    sys.modules["homeassistant.util"].dt = _dt

    pkg = types.ModuleType("fpv7")
    pkg.__path__ = [str(SRC)]  # type: ignore[attr-defined]
    sys.modules["fpv7"] = pkg
    _mk(
        "fpv7.api",
        FordPassApi=object,
        FordPassApiError=_ApiError,
        FordPassProbeRejected=type("_ProbeRejected", (Exception,), {}),
    )

    import importlib

    sys.modules["fpv7.coordinator"] = importlib.import_module("fpv7.coordinator")


def _mod():
    _install_fakes()
    return sys.modules["fpv7.coordinator"]


class _FakeHass:
    def async_create_task(self, coro):
        task = asyncio.get_event_loop().create_task(coro)
        return task


class _FakeApi:
    """get_vehicle_status 可配置抛错/返回；其余端点返回 None（由桩缓存兜住）。"""

    def __init__(self, status_result=None, status_exc=None) -> None:
        self._result = status_result
        self._exc = status_exc

    async def get_vehicle_status(self, vin: str):
        if self._exc is not None:
            raise self._exc
        return self._result

    def __getattr__(self, name):
        async def _fn(*args, **kwargs):
            return None

        return _fn


def _build(status_result=None, status_exc=None):
    cls = _mod().FordPassCoordinator
    coord = object.__new__(cls)
    coord.vin = "VIN123"
    coord.data = {
        "status": {"lockStatus": {"value": "Locked"}},
        "vehiclestatus": {"lockStatus": {"value": "Locked"}},
    }
    coord.logger = logging.getLogger("test.fordpass.softfail")
    coord.track_location = False
    coord.vehicle_info = {}
    coord.extra_probes = {}
    coord._probe_cache = {}
    coord._initial_states_pending = True
    coord._persist_tasks = set()
    coord._command_states = {}
    coord._command_baselines = {}
    coord._last_empty_probes = None
    coord._update_failure_warned = False
    coord._consecutive_failures = 0
    coord.hass = _FakeHass()
    coord.api = _FakeApi(status_result, status_exc)

    coord._cached_keys: list[str] = []

    async def _cached_fetch(key, ttl, factory, persist=False, force=False, account=False):
        coord._cached_keys.append(key)
        return None

    coord._cached_fetch = _cached_fetch

    coord._activity: list[str] = []

    async def _log_activity(msg, associate=True):
        coord._activity.append(str(msg))

    coord._log_activity = _log_activity

    coord._failures: list[Exception] = []

    def _mark_sync_failure(exc):
        coord._failures.append(exc)

    coord._mark_sync_failure = _mark_sync_failure

    async def _log_refresh_states():
        return 0

    coord._log_refresh_states = _log_refresh_states

    async def _probe_save():
        return None

    coord._probe_save = _probe_save
    return coord


async def test_business_error_soft_fail_keeps_snapshot_and_continues():
    err = _ApiError(401, "business rejected", refresh_failed=False)
    coord = _build(status_exc=err)

    data = await coord._async_update_data()

    # 旧快照回填，不丢车况
    assert data["status"] == {"lockStatus": {"value": "Locked"}}
    assert data["vehiclestatus"] == {"lockStatus": {"value": "Locked"}}
    # 失败被打点、记活动，但没有中断整轮
    assert len(coord._failures) == 1
    assert coord._activity
    # 后续慢变端点仍被触达（链路继续，不是整轮中断）
    assert coord._cached_keys


async def test_timeout_soft_fail_keeps_snapshot_and_continues():
    coord = _build(status_exc=TimeoutError("timed out"))

    data = await coord._async_update_data()

    assert data["status"] == {"lockStatus": {"value": "Locked"}}
    assert data["vehiclestatus"] == {"lockStatus": {"value": "Locked"}}
    assert len(coord._failures) == 1
    assert coord._cached_keys


async def test_auth_failed_still_propagates():
    err = _ApiError(401, "token expired", refresh_failed=True)
    coord = _build(status_exc=err)
    AuthFailed = _mod().ConfigEntryAuthFailed

    try:
        await coord._async_update_data()
    except AuthFailed:
        pass
    else:
        raise AssertionError("凭据永久失效必须透传 ConfigEntryAuthFailed")


async def test_success_writes_new_snapshot():
    new_status = {"vehiclestatus": {"lockStatus": {"value": "Unlocked"}}}
    coord = _build(status_result=new_status)

    data = await coord._async_update_data()

    assert data["status"] == new_status
    assert data["vehiclestatus"] == {"lockStatus": {"value": "Unlocked"}}
    assert not coord._failures
