"""v6.5.3 回归：车控命令「点击即返回」+ 执行状态可见（A+C）。

回归保护点（旧行为会被本测试判失败）：
- v6.5.2 及以前：``run_command`` 在服务调用内同步轮询（18s）+ 兜底刷新，
  按钮点下去仍要等十几到几十秒才返回。
- v6.5.3：``run_command`` 发完命令**立即返回**，「轮询 → 兜底刷新 →
  再轮询」整段在后台任务里跑；执行结果写进 ``command_state``（按钮/开关/
  门锁状态的属性可见）；同一命令 5 秒内连点直接去重。

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
    """按调用次序返回预设结果，并记录收到的 timeout；可配置发送异常。"""

    def __init__(
        self,
        results: list[tuple[bool, dict | None]],
        send_error: Exception | None = None,
    ) -> None:
        self._results = list(results)
        self._send_error = send_error
        self.calls: list[tuple] = []
        self.timeouts: list[float] = []
        self.send_count = 0

    async def send_command(self, vin: str, command_type: str, cmd_spec=None):
        self.calls.append((vin, command_type, cmd_spec))
        self.send_count += 1
        if self._send_error is not None:
            raise self._send_error
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
    coord._command_states = {}
    coord._command_baselines = {}
    coord._last_cmd_sent = {}
    coord._persist_tasks = set()
    coord._cmd_refresh_active = False
    coord.force_refresh_calls = 0
    coord.pushed: list[dict] = []
    coord.listener_calls = 0

    async def _force_refresh(force_all: bool = False) -> None:
        coord.force_refresh_calls += 1
        await asyncio.sleep(0)  # 真实刷新有网络 await 点，桩保留让出以验证单飞

    def _update_listeners() -> None:
        coord.listener_calls += 1

    coord.force_refresh = _force_refresh
    coord.async_set_updated_data = lambda data: coord.pushed.append(data)
    coord.async_update_listeners = _update_listeners
    return coord


def _snapshot(lock_status: str = "Locked") -> dict:
    return {"vehiclestatus": {"lockStatus": {"value": lock_status}}}


def _run(coord, *args, **kwargs):
    mod = sys.modules["fpcmd.coordinator"]
    return mod.FordPassCoordinator.run_command(coord, *args, **kwargs)


@pytest.mark.asyncio
async def test_command_windows_are_background_only():
    """窗口常量：后台第一段 18s、总预算 60s、去重窗口 5s。"""
    cls = _coordinator_cls()
    assert cls.COMMAND_SYNC_WAIT < 30.0, "后台第一段窗口必须远低于旧 60s"
    assert cls.COMMAND_TOTAL_WAIT == 60.0, "命令执行期总预算应与旧基线一致"
    assert cls.COMMAND_DEDUP_WINDOW == 5.0, "连点去重窗口应为 5 秒"


@pytest.mark.asyncio
async def test_press_returns_without_any_wait():
    """A：发送成功后立即返回——服务调用内不出现任何轮询等待。"""
    api = _FakeApi([(True, _snapshot("Unlocked"))])
    coord = _build_coordinator(api)

    await _run(coord, "DoorUnlock")

    assert api.send_count == 1, "必须把命令发出去"
    assert api.timeouts == [], "点击即返回：同步路径里不允许等轮询"
    assert coord.force_refresh_calls == 0, "同步路径里不允许兜底刷新"
    assert len(coord._command_followers) == 1, "轮询必须转为后台任务"
    assert coord.pushed == [], "后台还没跑，此时不应有推送"
    st = coord.command_state("DoorUnlock")
    assert st["state"] == "pending", "下发后状态必须是执行中"
    assert st["command_id"] == "cid-1"
    await asyncio.gather(*coord.hass.tasks)


@pytest.mark.asyncio
async def test_background_flip_pushes_snapshot_and_marks_done():
    """C：后台等到的翻转直接推快照，状态置「已完成」，登记表清空。"""
    api = _FakeApi([(True, _snapshot("Unlocked"))])
    coord = _build_coordinator(api)

    await _run(coord, "DoorUnlock")
    await asyncio.gather(*coord.hass.tasks)

    assert api.timeouts == [coord.COMMAND_SYNC_WAIT], "第一段用后台窗口时长"
    assert coord.force_refresh_calls == 0, "窗口内翻转不该再兜底刷新"
    pushed = coord.pushed[-1]
    assert pushed["vehiclestatus"]["lockStatus"]["value"] == "Unlocked"
    # v5.3.4 合并推送：其它键不能被覆盖丢失
    assert pushed["location"] == {"lat": 1}
    assert pushed["active_alerts"] == []
    assert coord.command_state("DoorUnlock")["state"] == "done"
    assert coord._command_followers == {}, "任务结束后登记表应清空"


@pytest.mark.asyncio
async def test_background_timeout_refreshes_then_marks_timeout():
    """后台两段都没翻转：先兜底刷新一次，最终状态「未确认」且不推旧快照。"""
    api = _FakeApi([(False, None), (False, None)])
    coord = _build_coordinator(api)

    await _run(coord, "DoorLock")
    await asyncio.gather(*coord.hass.tasks)

    assert api.timeouts == [
        coord.COMMAND_SYNC_WAIT,
        coord.COMMAND_TOTAL_WAIT - coord.COMMAND_SYNC_WAIT,
    ], "两段等待必须分别使用后台窗口与剩余预算"
    assert coord.force_refresh_calls == 1, "第一段超时后必须兜底刷新一次"
    assert coord.pushed == [], "超时不能把旧快照当新结果推送"
    assert coord.command_state("DoorLock")["state"] == "timeout"


@pytest.mark.asyncio
async def test_repeated_press_is_deduped():
    """A：同一命令 5 秒内重复点击直接忽略，不再下发（防连点放大请求）。"""
    api = _FakeApi([(False, None), (False, None)])
    coord = _build_coordinator(api)

    await _run(coord, "DoorUnlock")
    await _run(coord, "DoorUnlock")

    assert api.send_count == 1, "去重窗口内的第二次点击不能再发命令"
    assert len(coord._command_followers) == 1, "同一命令只允许一个后台任务"
    await asyncio.gather(*coord.hass.tasks)


@pytest.mark.asyncio
async def test_dedup_window_expires():
    """去重窗口过期后允许再次下发（不是永久锁死）。"""
    api = _FakeApi([(True, _snapshot()), (True, _snapshot())])
    coord = _build_coordinator(api)

    await _run(coord, "DoorUnlock")
    coord._last_cmd_sent["DoorUnlock"] -= coord.COMMAND_DEDUP_WINDOW + 1.0
    await _run(coord, "DoorUnlock")

    assert api.send_count == 2, "窗口过期后必须允许重新下发"
    await asyncio.gather(*coord.hass.tasks)


@pytest.mark.asyncio
async def test_send_failure_raises_and_marks_failed():
    """发送失败仍同步抛出（UI 立刻 toast），且状态记为失败。"""
    api = _FakeApi([], send_error=RuntimeError("100502 非法命令"))
    coord = _build_coordinator(api)

    with pytest.raises(RuntimeError):
        await _run(coord, "DoorLock")

    st = coord.command_state("DoorLock")
    assert st["state"] == "failed"
    assert "100502" in st["message"]
    assert len(coord._command_followers) == 0


@pytest.mark.asyncio
async def test_wait_false_refreshes_in_background_only():
    """wait=False：不开轮询任务，刷新也放后台，调用方立即返回。"""
    api = _FakeApi([])
    coord = _build_coordinator(api)

    await _run(coord, "AutoRefresh", False)

    assert coord._command_followers == {}, "wait=False 不应开轮询任务"
    assert coord.force_refresh_calls == 0, "刷新必须在后台跑，不能同步等"
    await asyncio.gather(*coord.hass.tasks)
    assert coord.force_refresh_calls == 1, "后台应补一次刷新"


@pytest.mark.asyncio
async def test_latest_command_state_picks_newest():
    """开关一开一关共用状态位：取最近更新的那条。"""
    coord = _build_coordinator(_FakeApi([]))
    coord._command_states["CMD_A"] = {
        "state": "done",
        "updated_at": "2026-11-09T10:00:00+08:00",
    }
    coord._command_states["CMD_B"] = {
        "state": "pending",
        "updated_at": "2026-11-09T10:00:05+08:00",
    }
    assert coord.latest_command_state("CMD_A", "CMD_B")["state"] == "pending"
    assert coord.latest_command_state("CMD_A")["state"] == "done"
    assert coord.latest_command_state("CMD_X") == {}


@pytest.mark.asyncio
async def test_concurrent_commands_share_one_background_refresh():
    """不同命令同时超时：兜底刷新单飞，只跑一轮（请求量不放大）。"""
    api = _FakeApi([(False, None), (False, None), (False, None), (False, None)])
    coord = _build_coordinator(api)

    await _run(coord, "DoorUnlock")
    await _run(coord, "DoorLock")  # 不同命令，不受去重影响
    await asyncio.gather(*coord.hass.tasks)

    assert api.send_count == 2, "两条不同命令都应下发"
    assert coord.force_refresh_calls == 1, "并发的命令后刷新必须单飞成一轮"
    assert coord.command_state("DoorUnlock")["state"] == "timeout"
    assert coord.command_state("DoorLock")["state"] == "timeout"


def test_button_press_uses_coordinator_run_command():
    """按钮仍统一走 run_command（不允许自己发命令绕过反馈链路）。"""
    src = (SRC / "button.py").read_text(encoding="utf-8")
    flat = "".join(src.split())
    assert "awaitself.coordinator.run_command(" in flat
    assert 'self._command!=CMD_REFRESH_STATUS' in flat


def test_entities_expose_command_state():
    """C 的接入断言：按钮/开关/门锁状态都要把执行状态挂在属性上。"""
    btn = (SRC / "button.py").read_text(encoding="utf-8")
    sw = (SRC / "switch.py").read_text(encoding="utf-8")
    sen = (SRC / "sensor.py").read_text(encoding="utf-8")
    assert "command_state" in btn and "_command_state_attrs" in btn
    assert "_command_types" in sw and "latest_command_state" in sw
    assert "command_state" in sen and 'self._key == "lock_status"' in sen
