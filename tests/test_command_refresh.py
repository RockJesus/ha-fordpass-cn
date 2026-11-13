"""v6.4.5 回归测试：动作按钮按下后要「等命令执行完成再推送新快照」。

回归保护点（旧行为会被本测试判失败）：
- 旧实现：普通动作命令（开锁/关锁/后备箱解锁）只调用一次
  ``coordinator.force_refresh()`` —— 命令刚下发、云端 lockStatus 尚未翻转，
  拉到的仍是命令前的旧快照，「门锁状态」完全不跟随操作，只能等下一轮
  30 分钟轮询。
- 新实现：统一走 ``coordinator.run_command(command, cmd_spec=...)``——
  发送 → 轮询 command-execution-status → 推送翻转后的新鲜快照；失败如实
  抛出 HomeAssistantError。
- cmd_spec 必须透传（InitialVA / CancelVA 等命令不带 cmdSpec 会被网关拒绝）。

测试用轻量桩替代 HA 运行时（沿用 test_sensor_pipeline 的同款做法）。
"""
from __future__ import annotations

import asyncio
import datetime
import sys
import types
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "fordpass_cn"


class _FakeButtonEntity:
    """替代 homeassistant.components.button.ButtonEntity。

    v6.6.7: 不提供 ``_attr_name`` 默认值——HA 2026.11 的 Entity 只留了
    ``_attr_name: str | None`` 类型注解、没有类属性默认值，用 translation_key
    命名的实体真的没有这个属性。桩里给默认值会把「直接读 self._attr_name」
    的崩溃掩盖成通过（v6.6.7 线上实测踩到）。
    """

    _attr_should_poll = True
    _attr_state: str | None = None
    _attr_icon: str | None = None
    _attr_unique_id: str | None = None
    _attr_translation_key: str | None = None
    _attr_has_entity_name = False
    _attr_device_info: dict | None = None


def _mk(name: str, **attrs) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


def _install_fakes() -> None:
    """装载 HA 依赖桩 + 集成自身的 api/coordinator 桩，然后加载 button。"""
    if "fpbtn.button" in sys.modules:
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

    need("homeassistant.components")
    need("homeassistant.components.button", ButtonEntity=_FakeButtonEntity)
    sys.modules["homeassistant.components"].button = sys.modules[
        "homeassistant.components.button"
    ]
    need("homeassistant.config_entries", ConfigEntry=object)
    need("homeassistant.core", HomeAssistant=object)

    class _HAError(Exception):
        """替代 homeassistant.exceptions.HomeAssistantError。"""

    need("homeassistant.exceptions", HomeAssistantError=_HAError)
    need("homeassistant.helpers")
    need("homeassistant.helpers.entity_registry")
    need("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
    need("homeassistant.util")
    _dt = need("homeassistant.util.dt")
    if not hasattr(_dt, "now"):
        _dt.now = lambda: datetime.datetime.now(
            datetime.timezone(datetime.timedelta(hours=8))
        )
    sys.modules["homeassistant.util"].dt = _dt

    # 集成包：真实 const / capability，桩 api / coordinator
    pkg = types.ModuleType("fpbtn")
    pkg.__path__ = [str(SRC)]  # type: ignore[attr-defined]
    sys.modules["fpbtn"] = pkg
    _mk("fpbtn.api", extract_remote_image_url=lambda *a, **k: None)
    _mk("fpbtn.coordinator", FordPassCoordinator=object)

    import importlib

    sys.modules["fpbtn.button"] = importlib.import_module("fpbtn.button")


class _FakeApi:
    """记录发送的命令与 cmdSpec；可配置是否返回 commandId。"""

    def __init__(self, resp: dict | None = None) -> None:
        self.calls: list[tuple] = []
        self._resp = resp if resp is not None else {"commandId": "cid-1"}

    async def send_command(self, vin: str, command_type: str, cmd_spec=None):
        self.calls.append((vin, command_type, cmd_spec))
        return self._resp

    async def wait_command_complete(self, vin, command_id, command_type, timeout=60.0, poll=3.0):
        self.wait_calls = getattr(self, "wait_calls", [])
        self.wait_calls.append((vin, command_id, command_type))
        return False, None


class _FakeCoordinator:
    """FordPassButton 只用到这些成员。"""

    uid_key = "VIN123"
    vin = "VIN123"
    device_info: dict = {}
    _vehicle_name = "锐际"
    data: dict = {}

    def __init__(self, raise_on_run: Exception | None = None) -> None:
        self.api = _FakeApi()
        self.run_calls: list[tuple] = []
        self.force_refresh_calls = 0
        self._raise = raise_on_run

    async def run_command(self, command_type: str, wait: bool = True, cmd_spec=None) -> None:
        self.run_calls.append((command_type, wait, cmd_spec))
        if self._raise:
            raise self._raise

    async def force_refresh(self, force_all: bool = False) -> None:
        self.force_refresh_calls += 1

    def async_set_updated_data(self, data: dict) -> None:
        self.data = data

    async def async_record_cmd_fail(self, command_type: str, exc) -> None:
        self.cmd_fail_records = getattr(self, "cmd_fail_records", [])
        self.cmd_fail_records.append((command_type, exc))


def _make_button(coordinator, key="lock_door", command="DoorLock", cmd_spec=None):
    _install_fakes()
    button_mod = sys.modules["fpbtn.button"]
    return button_mod.FordPassButton(
        coordinator, key, "关锁", "mdi:car-door-lock", command, cmd_spec
    )


def test_action_command_goes_through_run_command():
    """普通动作命令必须走 run_command（等执行完成），而不是只做一次 force_refresh。"""
    coord = _FakeCoordinator()
    button = _make_button(coord)
    asyncio.run(button._do_press())
    assert coord.run_calls == [("DoorLock", True, None)], "动作命令应交给 run_command 统一处理"
    assert coord.force_refresh_calls == 0, "不应再拉命令前的旧快照"
    assert coord.api.calls == [], "按钮自己不应再直接发命令（会重复下发）"


def test_cmd_spec_is_forwarded():
    """cmdSpec 必须透传（InitialVA/CancelVA 等不带 cmdSpec 会被网关拒绝）。"""
    coord = _FakeCoordinator()
    spec = [{"key": "VAType", "value": "4"}]
    button = _make_button(coord, key="unlock_va", command="InitialVA", cmd_spec=spec)
    asyncio.run(button._do_press())
    assert coord.run_calls == [("InitialVA", True, spec)]


def test_press_failure_is_reported():
    """命令失败必须抛 HomeAssistantError（前端 toast），不能静默。"""
    coord = _FakeCoordinator(raise_on_run=RuntimeError("网关拒绝"))
    button = _make_button(coord)
    err_mod = sys.modules["homeassistant.exceptions"]
    with pytest.raises(err_mod.HomeAssistantError) as ei:
        asyncio.run(button._do_press())
    assert "关锁" in str(ei.value) and "网关拒绝" in str(ei.value)


def test_refresh_command_keeps_own_sequence():
    """刷新类命令（AutoRefresh/ForceRefresh）保留「先立即刷新、再等完成」时序。"""
    coord = _FakeCoordinator()
    button = _make_button(coord, key="refresh_status", command="ForceRefresh")
    asyncio.run(button._do_press())
    assert coord.run_calls == [], "刷新命令不应走 run_command（时序不同）"
    assert coord.api.calls and coord.api.calls[0][1] == "ForceRefresh"
    assert coord.force_refresh_calls == 1, "刷新命令仍需立即 force_refresh 写活动记录"


def test_trunk_button_uses_same_path():
    """后备箱解锁按钮与门锁按钮共用同一执行入口。"""
    coord = _FakeCoordinator()
    button = _make_button(coord, key="trunk_unlock", command="TrunkUnlock")
    asyncio.run(button._do_press())
    assert coord.run_calls == [("TrunkUnlock", True, None)]
