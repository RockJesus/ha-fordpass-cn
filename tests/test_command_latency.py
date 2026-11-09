"""v6.5.2 回归测试：命令反馈不再把用户卡住 60 秒。

回归保护点（旧行为会被本测试判失败）：
- 旧实现：``run_command`` 用 ``wait_command_complete`` 的默认 60s 超时，
  按钮/开关服务调用一直挂着，用户点「开锁」要等约 1 分钟才有反馈；
  实测云端快照 09:51:02 已翻转、集成 09:51:47 才写入，滞后 45s。
- 新实现：同步窗口 ``COMMAND_SYNC_WAIT``（18s）内翻转则立即推送；未翻转
  则先兜底 ``force_refresh`` 让状态尽早可见，再把剩余等待交给后台任务
  （总预算 ``COMMAND_TOTAL_WAIT`` 60s，单命令请求量不变），服务调用立即
  返回。

测试用轻量桩替代 HA 运行时（沿用 test_command_refresh 的同款做法）。
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import sys
import types
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "fordpass_cn"


def _mk(name: str, **attrs) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


class _FakeDataUpdateCoordinator:
    """替代 homeassistant.helpers.update_coordinator.DataUpdateCoordinator。"""

    __class_getitem__ = classmethod(lambda cls, _item: cls)

    async def async_shutdown(self) -> None:
        return None


class _UpdateFailed(Exception):
    """替代 UpdateFailed。"""


def _install_fakes() -> None:
    if "fpcmd.coordinator" in sys.modules:
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

    class _AuthFailed(Exception):
        """替代 ConfigEntryAuthFailed。"""

    need("homeassistant.exceptions", ConfigEntryAuthFailed=_AuthFailed)
    need("homeassistant.helpers")
    need("homeassistant.helpers.device_registry", DeviceInfo=dict)
    need("homeassistant.helpers.storage", Store=object)
    need(
        "homeassistant.helpers.update_coordinator",
        DataUpdateCoordinator=_FakeDataUpdateCoordinator,
        UpdateFailed=_UpdateFailed,
    )
    need("homeassistant.util")
    _dt = need("homeassistant.util.dt")
    if not hasattr(_dt, "now"):
        _dt.now = lambda: datetime.datetime.now(
            datetime.timezone(datetime.timedelta(hours=8))
        )
        _dt.utcnow = lambda: datetime.datetime.now(datetime.timezone.utc)
    sys.modules["homeassistant.util"].dt = _dt

    # 集成包：真实 const，桩 api
    pkg = types.ModuleType("fpcmd")
    pkg.__path__ = [str(SRC)]  # type: ignore[attr-defined]
    sys.modules["fpcmd"] = pkg

    class _ApiError(Exception):
        pass

    class _ProbeRejected(Exception):
        pass

    _mk(
        "fpcmd.api",
        FordPassApi=object,
        FordPassApiError=_ApiError,
        FordPassProbeRejected=_ProbeRejected,
    )

    import importlib

    sys.modules["fpcmd.coordinator"] = importlib.import_module("fpcmd.coordinator")


def _coordinator_cls():
    _install_fakes()
    return sys.modules["fpcmd.coordinator"].FordPassCoordinator


class _FakeHass:
    def __init__(self) -> None:
        self.tasks: list[asyncio.Task] = []

    def async_create_task(self, coro):
        task = asyncio.ensure_future(coro)
        self.tasks.append(task)
        return task


class _FakeApi:
    """按调用次序返回预设结果，并记录收到的 timeout。"""

    def __init__(self, results: list[tuple[bool, dict | None]]) -> None:
        self._results = list(results)
        self.calls: list[tuple] = []
        self.timeouts: list[float] = []

    async def send_command(self, vin: str, command_type: str, cmd_spec=None):
        self.calls.append((vin, command_type, cmd_spec))
        return {"commandId": "cid-1"}

    async def wait_command_complete(
        self, vin, command_id, command_type, timeout=60.0, poll=3.0
    ):
        self.timeouts.append(timeout)
        idx = len(self.timeouts) - 1
        result = self._results[idx] if idx < len(self._results) else (False, None)
        return result


def _build_coordinator(api: _FakeApi):
    cls = _coordinator_cls()
    coord = object.__new__(cls)
    coord.api = api
    coord.vin = "VIN123"
    coord.logger = logging.getLogger("test.fordpass")
    coord.data = {"location": {"lat": 1}, "active_alerts": []}
    coord.hass = _FakeHass()
    coord._command_followers = {}
    coord.force_refresh_calls = 0
    coord.pushed: list[dict] = []

    async def _force_refresh(force_all: bool = False) -> None:
        coord.force_refresh_calls += 1

    coord.force_refresh = _force_refresh
    coord.async_set_updated_data = lambda data: coord.pushed.append(data)
    return coord


def _snapshot(lock_status: str = "Locked") -> dict:
    return {"vehiclestatus": {"lockStatus": {"value": lock_status}}}


@pytest.mark.asyncio
async def test_sync_window_is_short_and_total_budget_kept():
    """同步窗口必须明显短于 60s，命令执行期总预算保持 60s。"""
    cls = _coordinator_cls()
    assert cls.COMMAND_SYNC_WAIT < 30.0, "同步等待必须远低于旧 60s 超时"
    assert cls.COMMAND_TOTAL_WAIT == 60.0, "命令执行期总预算应与旧基线一致"


@pytest.mark.asyncio
async def test_completed_within_window_pushes_without_follower():
    """同步窗口内翻转：直推快照，不开后台任务、不额外刷新。"""
    api = _FakeApi([(True, _snapshot("Unlocked")), (True, _snapshot())])
    coord = _build_coordinator(api)

    await sys.modules["fpcmd.coordinator"].FordPassCoordinator.run_command(
        coord, "Unlock Doors"
    )

    assert api.timeouts == [coord.COMMAND_SYNC_WAIT], "必须传入同步窗口时长"
    assert coord.force_refresh_calls == 0, "窗口内已完成时不该再兜底刷新"
    assert coord._command_followers == {}, "窗口内已完成时不该开后台任务"
    pushed = coord.pushed[-1]
    assert pushed["vehiclestatus"]["lockStatus"]["value"] == "Unlocked"
    # v5.3.4 合并推送：其它键不能被覆盖丢失
    assert pushed["location"] == {"lat": 1}
    assert pushed["active_alerts"] == []


@pytest.mark.asyncio
async def test_timeout_returns_fast_and_hands_off_to_background():
    """窗口内没完成：先兜底刷新，剩余等待转后台，调用方立即返回。"""
    api = _FakeApi([(False, None), (True, _snapshot("Unlocked"))])
    coord = _build_coordinator(api)
    mod = sys.modules["fpcmd.coordinator"]

    await mod.FordPassCoordinator.run_command(coord, "Unlock Doors")

    assert coord.force_refresh_calls == 1, "未完成时必须兜底刷新一次"
    assert len(coord._command_followers) == 1, "剩余等待必须转成后台任务"
    assert coord.hass.tasks, "后台任务应通过 hass.async_create_task 创建"

    await asyncio.gather(*coord.hass.tasks)
    assert api.timeouts[1] == coord.COMMAND_TOTAL_WAIT - coord.COMMAND_SYNC_WAIT
    assert coord.pushed[-1]["vehiclestatus"]["lockStatus"]["value"] == "Unlocked"
    assert coord._command_followers == {}, "任务结束后登记表应清空"


@pytest.mark.asyncio
async def test_follower_timeout_does_not_push_stale_snapshot():
    """后台等待也超时：不推送任何快照，交给下一轮自动刷新。"""
    api = _FakeApi([(False, None), (False, None)])
    coord = _build_coordinator(api)
    mod = sys.modules["fpcmd.coordinator"]

    await mod.FordPassCoordinator.run_command(coord, "Lock Doors")
    await asyncio.gather(*coord.hass.tasks)

    assert coord.pushed == [], "后台超时不能把旧快照当新结果推送"


@pytest.mark.asyncio
async def test_duplicate_command_id_is_not_followed_twice():
    """同一 commandId 连点只跟一个后台任务，避免成倍放大云端请求。"""
    api = _FakeApi([(False, None), (False, None), (False, None)])
    coord = _build_coordinator(api)
    mod = sys.modules["fpcmd.coordinator"]

    coord.api = api
    await mod.FordPassCoordinator.run_command(coord, "Unlock Doors")
    await mod.FordPassCoordinator.run_command(coord, "Unlock Doors")

    assert len(coord._command_followers) == 1, "同一命令只允许一个后台任务"
    await asyncio.gather(*coord.hass.tasks)


def test_button_press_uses_coordinator_run_command():
    """按钮仍统一走 run_command（不允许自己发命令绕过反馈链路）。"""
    src = (SRC / "button.py").read_text(encoding="utf-8")
    flat = "".join(src.split())
    assert "awaitself.coordinator.run_command(" in flat
    assert 'self._command!=CMD_REFRESH_STATUS' in flat
