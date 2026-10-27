"""event 平台逻辑测试（不依赖 Home Assistant 运行时）。

用最小假模块替换 ``homeassistant.*``，再直接加载 ``event.py``，因此可以在
纯 Python 环境跑，不需要装 HA、也不需要连车。

回归保护点：
- 取不到告警（``active_alerts`` 非列表）不得当成「告警全部消失」；
- 只有云端明确返回空列表才允许触发 ``cleared``；
- 首次回调只建基线，不补发历史；
- 车门事件多门合并、大小写兼容、``Unknown`` 忽略。
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "fordpass_cn"


class _FakeEntity:
    """替代 homeassistant.helpers.entity.Entity 的最小实现。"""

    _attr_should_poll = None

    def __init__(self) -> None:
        self.hass: Any = None

    def async_write_ha_state(self) -> None:
        """测试里不需要真正写状态。"""


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

    for name in ("homeassistant", "homeassistant.components", "homeassistant.helpers"):
        mk(name)
    mk("homeassistant.components.event", EventEntity=_FakeEventEntity)
    mk("homeassistant.config_entries", ConfigEntry=object)
    mk("homeassistant.core", HomeAssistant=object, callback=lambda func: func)
    mk("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)

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
