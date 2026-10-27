"""Event platform: 车辆瞬时事件（告警 / 车门开合）。

v5.9.5: 引入 HA 的 ``event`` 域。与 sensor 的区别在于语义——event 实体的
状态是「事件发生的时间」，事件类型放在 ``event_type``，属性放明细。
自动化可以直接用状态变化触发，不需要自己保存上一次的文本再去比较：

    trigger:
      - platform: state
        entity_id: event.<前缀>_vehicle_alert
        not_to: ["unknown", "unavailable"]

两个实体：
- 车辆告警事件：``active_alerts`` 里出现新条目时触发（按标题去重，
  首次加载只建立基线、不补发历史），清空时触发 ``cleared``。
- 车门开合事件：``doorStatus`` 下各门位状态变化时触发，属性列出
  本次所有变化的门位，不必为每个门单独建实体。
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import FordPassCoordinator

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

# doorStatus 下的门位字段 → 中文名（与 sensor 平台的车门传感器同一份数据源）
DOOR_FIELDS: dict[str, str] = {
    "driverDoor": "主驾车门",
    "passengerDoor": "副驾车门",
    "leftRearDoor": "左后车门",
    "rightRearDoor": "右后车门",
    "tailgateDoor": "尾门",
    "hoodDoor": "引擎盖",
    "innerTailgateDoor": "内尾门",
}

# 云端取值 → 事件类型（小写比较；Unknown 不触发，避免误报）
DOOR_EVENT_MAP: dict[str, str] = {
    "open": "opened",
    "ajar": "ajar",
    "closed": "closed",
}


def _status(coordinator: FordPassCoordinator) -> dict[str, Any]:
    """取车辆状态字典（vehiclestatus 为主，status 兜底）。"""
    data = coordinator.data or {}
    for key in ("vehiclestatus", "status"):
        node = data.get(key)
        if isinstance(node, dict) and node:
            return node
    return {}


class _FordPassEventBase(EventEntity):
    """共享协调器订阅——事件实体不轮询，靠刷新广播驱动。"""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_device_info = coordinator.device_info
        self._unsub: Any = None
        self._primed = False

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._unsub is None:
            self._unsub = self.coordinator.async_add_listener(self._on_update)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    @callback
    def _on_update(self) -> None:
        """刷新广播 → 比对差异 → 触发事件。

        首次回调只建立基线（登录后已有的告警/已开的车门不算新事件），
        刷新失败（data 为空）时不做任何比较，避免误判成「全部清零」。
        """
        if not self.coordinator.data:
            return
        try:
            self._process()
        except Exception:  # noqa: BLE001
            _LOGGER.debug("FordPass 事件实体处理失败", exc_info=True)

    def _process(self) -> None:
        raise NotImplementedError

    def _fire(self, event_type: str, attributes: dict[str, Any]) -> None:
        """记录事件并写状态。

        HA 的 ``_trigger_event`` 只更新内部记录、不落状态机
        （见 ``EventEntity._trigger_event``），必须紧跟一次
        ``async_write_ha_state`` 才会产生 state_changed，自动化才能触发。
        """
        self._trigger_event(event_type, attributes)
        if self.hass is not None:
            self.async_write_ha_state()


class FordPassAlertEvent(_FordPassEventBase):
    """车辆告警事件（/vha/activealert）。

    按标题去重后与上一轮比对：出现新标题触发 ``alert``，全部消失触发
    ``cleared``。属性带上本条告警的标题/等级/正文/发生时间，自动化可直接
    把 ``trigger.to_state.attributes.headline`` 塞进通知。
    """

    _attr_name = "车辆告警事件"
    _attr_icon = "mdi:alert-circle"
    _attr_event_types = ["alert", "cleared"]

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.uid_key}-vehicle_alert_event"
        self._seen: set[str] = set()

    def _alerts(self) -> list[dict[str, Any]]:
        alerts = (self.coordinator.data or {}).get("active_alerts")
        if not isinstance(alerts, list):
            return []
        return [a for a in alerts if isinstance(a, dict)]

    @staticmethod
    def _title(alert: dict[str, Any]) -> str:
        for key in ("headline", "Headline", "title", "alertName", "message", "body"):
            value = alert.get(key)
            if value:
                return str(value).strip()
        return ""

    def _process(self) -> None:
        alerts = self._alerts()
        titles = {t for t in (self._title(a) for a in alerts) if t}

        if not self._primed:
            self._seen = titles
            self._primed = True
            return

        new_titles = titles - self._seen
        if new_titles:
            fresh = [a for a in alerts if self._title(a) in new_titles]
            first = fresh[0]
            self._seen = titles
            self._fire(
                "alert",
                {
                    "headline": self._title(first),
                    "severity": first.get("severity"),
                    "body": first.get("body"),
                    "event_time": first.get("eventTime"),
                    "new_count": len(new_titles),
                    "total_count": len(titles),
                    "titles": sorted(new_titles),
                },
            )
            return

        if not titles and self._seen:
            previous = len(self._seen)
            self._seen = set()
            self._fire("cleared", {"previous_count": previous})
            return

        self._seen = titles


class FordPassDoorEvent(_FordPassEventBase):
    """车门开合事件（doorStatus）。

    一次刷新内可能有多扇门同时变化，因此只发一个事件，把全部变化放进
    ``changes`` 属性（事件类型取其中最"值得注意"的一种：开门 > 未关紧 >
    关门）。仅为某一扇门做自动化时，按 ``trigger.to_state.attributes.door``
    过滤即可。
    """

    _attr_name = "车门开合事件"
    _attr_icon = "mdi:car-door"
    _attr_event_types = ["opened", "ajar", "closed"]

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.uid_key}-door_event"
        self._doors: dict[str, str] = {}

    def _current(self) -> dict[str, str]:
        doors = _status(self.coordinator).get("doorStatus")
        if not isinstance(doors, dict):
            return {}
        result: dict[str, str] = {}
        for field, label in DOOR_FIELDS.items():
            raw = doors.get(field)
            if isinstance(raw, dict):
                raw = raw.get("value")
            if isinstance(raw, str):
                # 云端大小写不统一（Closed / CLOSED / closed 都出现过），统一小写比对
                state = DOOR_EVENT_MAP.get(raw.strip().lower())
                if state:
                    result[label] = state
        return result

    def _process(self) -> None:
        current = self._current()
        if not current:
            return

        if not self._primed:
            self._doors = current
            self._primed = True
            return

        changes = {
            label: state
            for label, state in current.items()
            if self._doors.get(label) not in (None, state)
        }
        self._doors = current
        if not changes:
            return

        changed = list(changes.values())
        if "opened" in changed:
            event_type = "opened"
        elif "ajar" in changed:
            event_type = "ajar"
        else:
            event_type = "closed"
        self._fire(
            event_type,
            {
                "door": next(iter(changes)),
                "doors": list(changes),
                "changes": changes,
                "count": len(changes),
            },
        )


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """每辆车两个事件实体：告警、车门开合（全车型通用，无需能力判断）。"""
    payload = entry.runtime_data  # v5.3.0: runtime_data 替代 hass.data
    coordinators = payload.get("coordinators") or [payload["coordinator"]]
    # 告警事件只在账号确实能拿到告警列表时创建（无该端点的车型不建空实体）
    entities: list[EventEntity] = []
    for coordinator in coordinators:
        entities.append(FordPassDoorEvent(coordinator))
        if isinstance((coordinator.data or {}).get("active_alerts"), list):
            entities.append(FordPassAlertEvent(coordinator))
    if entities:
        async_add_entities(entities)
