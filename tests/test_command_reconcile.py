"""v6.6.4 回归：命令「未确认」时的快照反证纠偏。

背景（本轮实测）：点按开锁后云端 command-execution-status 一直不回写完成，
60s 后按钮记「未确认」；但车辆快照 ``lockStatus`` 已在 19s 内翻转成
Unlocked。旧行为会一直显示「未确认」，用户以为命令失败。

回归保护点（旧行为会被本测试判失败）：
1. 被测命令仍处 timeout、快照目标已达成、基线不同 → 改记 done；
2. 命令前已是目标状态（基线相同）→ 不纠偏（防误报成功）；
3. 快照未翻转 → 保持 timeout；
4. 超过纠偏窗口（15 分钟）→ 不纠偏；
5. remoteStartStatus 为带 value 的 dict 时同样能识别；
6. 未登记目标字段的命令（如 TrunkUnlock）→ 不纠偏。
"""
from __future__ import annotations

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
    __class_getitem__ = classmethod(lambda cls, _item: cls)

    async def async_shutdown(self) -> None:
        return None


def _now():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))


def _install_fakes() -> None:
    if "fprec.coordinator" in sys.modules:
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
        pass

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

    pkg = types.ModuleType("fprec")
    pkg.__path__ = [str(SRC)]  # type: ignore[attr-defined]
    sys.modules["fprec"] = pkg
    _mk(
        "fprec.api",
        FordPassApi=object,
        FordPassApiError=type("_ApiError", (Exception,), {}),
        FordPassProbeRejected=type("_ProbeRejected", (Exception,), {}),
    )

    import importlib

    sys.modules["fprec.coordinator"] = importlib.import_module("fprec.coordinator")


def _mod():
    _install_fakes()
    return sys.modules["fprec.coordinator"]


def _build(sent_seconds_ago: float = 30.0):
    cls = _mod().FordPassCoordinator
    coord = object.__new__(cls)
    coord.data = {"vehiclestatus": {"lockStatus": {"value": "Locked"}}}
    coord.logger = logging.getLogger("test.fordpass.reconcile")
    coord.listener_calls = 0
    coord._command_states = {}
    coord._command_baselines = {}
    coord.async_update_listeners = lambda: setattr(
        coord, "listener_calls", coord.listener_calls + 1
    )
    sent_at = (_now() - datetime.timedelta(seconds=sent_seconds_ago)).isoformat(
        timespec="seconds"
    )
    coord._sent_at = sent_at
    return coord


def _set_state(coord, command_type: str, state: str = "timeout", sent_at=None):
    coord._command_states[command_type] = {
        "state": state,
        "command_id": "cid-1",
        "sent_at": sent_at or coord._sent_at,
        "updated_at": coord._sent_at,
        "message": "命令已下发，60s 内未确认执行完成，交给下一轮自动刷新",
    }


def test_unlock_reconciled_from_snapshot():
    """快照已解锁 + 命令前是锁定 → 未确认改记已完成。"""
    coord = _build()
    _set_state(coord, "DoorUnlock")
    coord._command_baselines["DoorUnlock"] = 1  # 命令前锁定

    _mod().FordPassCoordinator._reconcile_command_states(
        coord, {"lockStatus": {"value": "Unlocked"}}
    )

    state = coord._command_states["DoorUnlock"]
    assert state["state"] == "done"
    assert "云端快照已确认" in state["message"]
    assert "门锁已解锁" in state["message"]
    assert state["sent_at"] == coord._sent_at, "下发时间不能被纠偏覆盖"


def test_lock_reconciled_from_snapshot():
    """锁车命令同样能反证（快照 Locked）。"""
    coord = _build()
    _set_state(coord, "DoorLock")
    coord._command_baselines["DoorLock"] = 0  # 命令前解锁

    _mod().FordPassCoordinator._reconcile_command_states(
        coord, {"lockStatus": {"value": "LOCKED"}}
    )

    assert coord._command_states["DoorLock"]["state"] == "done"


def test_baseline_already_target_not_reconciled():
    """命令前已是目标状态 → 不纠偏（不能把「本来就这样」算成命令生效）。"""
    coord = _build()
    _set_state(coord, "DoorUnlock")
    coord._command_baselines["DoorUnlock"] = 0  # 命令前就是解锁

    _mod().FordPassCoordinator._reconcile_command_states(
        coord, {"lockStatus": {"value": "Unlocked"}}
    )

    assert coord._command_states["DoorUnlock"]["state"] == "timeout"


def test_snapshot_not_flipped_stays_timeout():
    """快照仍是锁定 → 保持未确认。"""
    coord = _build()
    _set_state(coord, "DoorUnlock")
    coord._command_baselines["DoorUnlock"] = 1

    _mod().FordPassCoordinator._reconcile_command_states(
        coord, {"lockStatus": {"value": "Locked"}}
    )

    assert coord._command_states["DoorUnlock"]["state"] == "timeout"


def test_old_command_outside_window_not_reconciled():
    """超过 15 分钟窗口的旧命令不再纠偏。"""
    coord = _build(sent_seconds_ago=1200)
    _set_state(coord, "DoorUnlock")
    coord._command_baselines["DoorUnlock"] = 1

    _mod().FordPassCoordinator._reconcile_command_states(
        coord, {"lockStatus": {"value": "Unlocked"}}
    )

    assert coord._command_states["DoorUnlock"]["state"] == "timeout"


def test_remotestart_dict_value_reconciled():
    """remoteStartStatus 是带 value 的 dict 时也能识别启动/停止。"""
    coord = _build()
    _set_state(coord, "RemoteStart")
    coord._command_baselines["RemoteStart"] = 0

    _mod().FordPassCoordinator._reconcile_command_states(
        coord, {"remoteStartStatus": {"value": 1}}
    )

    assert coord._command_states["RemoteStart"]["state"] == "done"
    assert "远程启动已生效" in coord._command_states["RemoteStart"]["message"]


def test_unknown_command_untouched():
    """未登记目标字段的命令（TrunkUnlock）不纠偏。"""
    coord = _build()
    _set_state(coord, "TrunkUnlock")

    _mod().FordPassCoordinator._reconcile_command_states(
        coord, {"lockStatus": {"value": "Unlocked"}}
    )

    assert coord._command_states["TrunkUnlock"]["state"] == "timeout"


def test_failed_command_not_reconciled():
    """发送失败的命令不会被快照纠偏（失败就是失败）。"""
    coord = _build()
    _set_state(coord, "DoorUnlock", state="failed")
    coord._command_baselines["DoorUnlock"] = 1

    _mod().FordPassCoordinator._reconcile_command_states(
        coord, {"lockStatus": {"value": "Unlocked"}}
    )

    assert coord._command_states["DoorUnlock"]["state"] == "failed"


def test_capture_baseline_uses_current_snapshot():
    """下发前基线取当前快照的 lockStatus。"""
    coord = _build()
    _mod().FordPassCoordinator._capture_command_baseline(coord, "DoorLock")

    assert coord._command_baselines["DoorLock"] == 1  # 快照是 Locked


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (True, 1),
        (False, 0),
        (1, 1),
        (0, 0),
        ("LOCKED", 1),
        ("Unlocked", 0),
        ("running", 1),
        (None, None),
        ("??", None),
    ],
)
def test_cmd_flag_normalisation(raw, expected):
    """云端布尔/枚举值归一（大小写、dict 解包前的原始值）。"""
    assert _mod()._cmd_flag(raw) == expected
