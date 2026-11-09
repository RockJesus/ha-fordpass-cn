"""event 平台逻辑测试（不依赖 Home Assistant 运行时）。

用最小假模块替换 ``homeassistant.*``，再直接加载 ``event.py``，因此可以在
纯 Python 环境跑，不需要装 HA、也不需要连车。

回归保护点：
- 取不到告警（``active_alerts`` 非列表）不得当成「告警全部消失」；
- 只有云端明确返回空列表才允许触发 ``cleared``；
- 首次回调只建基线，不补发历史；
- 车门事件多门合并、大小写兼容、``Unknown`` 忽略；
- 重启恢复交给 HA 原生 ``EventEntity``（v6.3.8 删除自实现），本集成
  不得再自造一套，且非法事件类型仍被框架拒绝。
"""
from __future__ import annotations

import importlib.util
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "fordpass_cn"


class _FakeEntity:
    """替代 homeassistant.helpers.entity.Entity 的最小实现。"""

    _attr_should_poll = None

    def __init__(self) -> None:
        self.hass: Any = None

    def async_write_ha_state(self) -> None:
        """测试里不需要真正写状态。"""


class _FakeRestoreEntity(_FakeEntity):
    """替代 homeassistant.helpers.restore_state.RestoreEntity。

    event.py 只是把它列进基类；恢复由 HA 原生 EventEntity 完成，测试不再
    注入历史状态（v6.3.8 起集成没有自实现恢复路径）。
    """

    async def async_get_last_state(self):
        return None


class _FakeEventEntity(_FakeEntity):
    _attr_event_types: list[str] = []

    @property
    def event_types(self) -> list[str]:
        return list(self._attr_event_types)

    def _trigger_event(self, event_type: str, attributes: dict | None = None) -> None:
        if event_type not in self.event_types:
            raise ValueError(f"invalid event type {event_type}")
        self.__dict__.setdefault("_fired", []).append((event_type, attributes))

    async def async_added_to_hass(self) -> None:
        """无需恢复状态。"""

    @property
    def available(self) -> bool:
        return True


class _FakeCoordinator:
    """event.py 只用到这几个成员。"""

    uid_key = "test"
    device_info: dict[str, Any] = {}

    def __init__(self, data: dict | None = None) -> None:
        self.data = data
        self.device_info = {}

    def async_add_listener(self, callback):
        return lambda: None


def _install_fake_modules() -> None:
    if "fpcn.event" in sys.modules:
        return

    def mk(name: str, **attrs: Any) -> types.ModuleType:
        module = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        sys.modules[name] = module
        return module

    for name in ("homeassistant", "homeassistant.components", "homeassistant.helpers",
                 "homeassistant.util"):
        mk(name)
    # v6.3.1: event.py 用 dt_util 生成事件时间（固定 UTC 时刻 06:09:26，
    # 本地 +8 为 14:09:26，便于断言本地/UTC 双口径属性）
    _DT_NOW = datetime(2026, 10, 30, 6, 9, 26, tzinfo=timezone.utc)
    _TZ_CN = timezone(timedelta(hours=8))
    _dt = mk("homeassistant.util.dt")
    _dt.utcnow = lambda: _DT_NOW
    _dt.as_local = lambda dt: dt.astimezone(_TZ_CN) if dt.tzinfo else dt.replace(tzinfo=_TZ_CN)
    _dt.as_utc = lambda dt: dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    sys.modules["homeassistant.util.dt"] = _dt
    mk("homeassistant.components.event", EventEntity=_FakeEventEntity)
    mk("homeassistant.config_entries", ConfigEntry=object)
    mk("homeassistant.core", HomeAssistant=object, callback=lambda func: func)
    mk("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
    mk("homeassistant.helpers.restore_state", RestoreEntity=_FakeRestoreEntity)

    pkg = mk("fpcn")
    pkg.__path__ = [str(SRC)]  # type: ignore[attr-defined]
    mk("fpcn.coordinator", FordPassCoordinator=_FakeCoordinator)

    spec = importlib.util.spec_from_file_location("fpcn.event", SRC / "event.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["fpcn.event"] = module
    spec.loader.exec_module(module)


def load_event():
    _install_fake_modules()
    return sys.modules["fpcn.event"]


def fired(entity) -> list[tuple[str, dict | None]]:
    return entity.__dict__.setdefault("_fired", [])


def alerts(*titles: str) -> dict:
    return {"active_alerts": [{"headline": t, "severity": 1} for t in titles]}


def doors(**kwargs: str) -> dict:
    return {"vehiclestatus": {"doorStatus": kwargs}}


def make_alert(data):
    entity = load_event().FordPassAlertEvent(_FakeCoordinator(data))
    entity.hass = object()
    return entity


def make_door(data):
    entity = load_event().FordPassDoorEvent(_FakeCoordinator(data))
    entity.hass = object()
    return entity


def test_alert_first_pass_only_baselines() -> None:
    entity = make_alert(alerts("A"))
    entity._process()
    assert fired(entity) == []


def test_alert_same_batch_no_event() -> None:
    entity = make_alert(alerts("A"))
    entity._process()
    entity.coordinator.data = alerts("A")
    entity._process()
    assert fired(entity) == []


def test_alert_new_title_fires() -> None:
    entity = make_alert(alerts("A"))
    entity._process()
    entity.coordinator.data = alerts("A", "B")
    entity._process()
    events = fired(entity)
    assert [e[0] for e in events] == ["alert"]
    assert events[0][1]["headline"] == "B"


def test_alert_missing_data_does_not_clear() -> None:
    """v5.9.6 回归：拉取失败（None / 缺键）不能被当成「告警全部消失」。"""
    entity = make_alert(alerts("A", "B"))
    entity._process()

    entity.coordinator.data = {"active_alerts": None}
    entity._process()
    assert fired(entity) == []

    entity.coordinator.data = {}
    entity._process()
    assert fired(entity) == []


def test_alert_empty_list_clears() -> None:
    entity = make_alert(alerts("A", "B"))
    entity._process()
    entity.coordinator.data = alerts()
    entity._process()
    events = fired(entity)
    assert [e[0] for e in events] == ["cleared"]
    assert events[0][1]["previous_count"] == 2


def test_alert_new_after_clear() -> None:
    entity = make_alert(alerts("A"))
    entity._process()
    entity.coordinator.data = alerts()
    entity._process()
    entity.coordinator.data = alerts("C")
    entity._process()
    assert [e[0] for e in fired(entity)][-1] == "alert"


def test_door_first_pass_only_baselines() -> None:
    entity = make_door(doors(driverDoor="Closed", tailgateDoor="Closed"))
    entity._process()
    assert fired(entity) == []


def test_door_open_then_close() -> None:
    entity = make_door(doors(driverDoor="Closed", tailgateDoor="Closed"))
    entity._process()

    entity.coordinator.data = doors(driverDoor="Open", tailgateDoor="Closed")
    entity._process()
    assert [e[0] for e in fired(entity)] == ["opened"]
    assert fired(entity)[0][1]["door"] == "主驾车门"

    entity.coordinator.data = doors(driverDoor="Closed", tailgateDoor="Closed")
    entity._process()
    assert [e[0] for e in fired(entity)][-1] == "closed"


def test_fire_attributes_local_time_and_cn() -> None:
    """v6.3.1: 事件属性带本地时间（+8）、UTC 时间与中文事件类型。

    state 是框架语义（UTC 时间戳），本地时间放属性供展示。
    """
    entity = make_door(doors(driverDoor="Closed"))
    entity._process()
    entity.coordinator.data = doors(driverDoor="Open")
    entity._process()
    attrs = fired(entity)[0][1]
    assert attrs["event_time"] == "2026-10-30T14:09:26+08:00"  # 本地 +8（v6.5.0 带偏移）
    assert attrs["event_time_utc"] == "2026-10-30T06:09:26+00:00"  # UTC 原始（v6.5.0 带偏移）
    assert attrs["event_type_cn"] == "已开门"

    entity.coordinator.data = doors(driverDoor="Closed")
    entity._process()
    attrs = fired(entity)[-1][1]
    assert attrs["event_type_cn"] == "已关门"


def test_alert_fire_attributes_cn() -> None:
    entity = make_alert(alerts("A"))
    entity._process()
    entity.coordinator.data = alerts("A", "B")
    entity._process()
    attrs = fired(entity)[0][1]
    assert attrs["event_type_cn"] == "有告警"
    assert attrs["event_time_utc"] == "2026-10-30T06:09:26+00:00"

    entity.coordinator.data = alerts()
    entity._process()
    attrs = fired(entity)[-1][1]
    assert attrs["event_type_cn"] == "已解除"


def test_door_multiple_changes_merged() -> None:
    entity = make_door(doors(driverDoor="Closed", tailgateDoor="Closed"))
    entity._process()
    entity.coordinator.data = doors(driverDoor="Open", tailgateDoor="Open")
    entity._process()
    events = fired(entity)
    assert len(events) == 1
    assert len(events[0][1]["changes"]) == 2


def test_door_case_insensitive() -> None:
    entity = make_door(doors(driverDoor="Open"))
    entity._process()
    entity.coordinator.data = doors(driverDoor="CLOSED")
    entity._process()
    assert [e[0] for e in fired(entity)][-1] == "closed"


def test_door_unknown_ignored() -> None:
    entity = make_door(doors(driverDoor="Open"))
    entity._process()
    entity.coordinator.data = doors(driverDoor="Unknown")
    entity._process()
    assert fired(entity) == []


def test_door_missing_key_ignored() -> None:
    entity = make_door(doors(driverDoor="Open"))
    entity._process()
    entity.coordinator.data = {"vehiclestatus": {}}
    entity._process()
    assert fired(entity) == []


# ---------------------------------------------------------------------------
# 重启恢复：由 HA 原生 EventEntity 负责（v6.3.8 移除自实现）
# ---------------------------------------------------------------------------
def test_no_custom_restore_path() -> None:
    """集成不得再自实现恢复逻辑。

    HA 2026.10 的 ``EventEntity`` 自身继承 ``RestoreEntity``，并在
    ``async_internal_added_to_hass`` 里用 ``ExtraStoredData`` 回填事件类型与
    属性；恢复过程不经过 ``_trigger_event``，因此不写状态机、不会误触自动化。

    旧的自实现（v6.1.2）读的是 ``last_event_attributes`` 这个属性——新版
    HA 已不再输出，恢复恒为空，属死代码，故删除。这里守住"不要再长回来"：
    一旦有人重新定义 ``_restore_last_event`` 或覆盖恢复入口，测试失败。
    """
    module = load_event()
    classes = {
        "_FordPassEventBase": module._FordPassEventBase,
        "FordPassAlertEvent": module.FordPassAlertEvent,
        "FordPassDoorEvent": module.FordPassDoorEvent,
    }
    for name, cls in classes.items():
        assert "_restore_last_event" not in vars(cls), f"{name} 重新实现了旧恢复逻辑"
        assert "async_internal_added_to_hass" not in vars(cls), (
            f"{name} 覆盖了框架的恢复入口"
        )


def test_restored_type_must_be_declared() -> None:
    """损坏的恢复数据不会伪造事件：未声明的事件类型被框架拒绝。

    HA 原生恢复把历史事件类型回填进内部记录，若状态机里残留的是旧版本
    已废弃的类型，框架的 ``_trigger_event`` 会抛 ``ValueError``，而不是把
    它写成一个谁也不认识的事件。
    """
    alert = make_alert(alerts("A"))
    assert alert.event_types, "事件集不能为空，否则恢复与触发都失去约束"
    with pytest.raises(ValueError):
        alert._trigger_event("not_a_declared_type", {})

    door = make_door(doors(driverDoor="Closed"))
    assert door.event_types
    with pytest.raises(ValueError):
        door._trigger_event("not_a_declared_type", {})
