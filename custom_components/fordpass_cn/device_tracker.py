"""Device tracker platform: vehicle location from the LBS API."""
from __future__ import annotations

import logging

try:
    # Recommended path on modern HA; also valid on older versions
    from homeassistant.components.device_tracker import TrackerEntity
except ImportError:  # pragma: no cover - very old HA
    from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import FordPassCoordinator
from .sensor import FordPassRestoreMixin

_LOGGER = logging.getLogger(__name__)


PARALLEL_UPDATES = 1

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    # v3.1.6: 多 VIN——每辆车独立创建定位实体
    payload = entry.runtime_data  # v5.3.0: runtime_data 替代 hass.data
    coordinators = payload.get("coordinators") or [payload["coordinator"]]
    # Always create the tracker entities. The option toggle only controls
    # availability + whether the coordinator fetches LBS data, so flipping
    # "track_location" in the options flow takes effect immediately without
    # restarting HA (see async_update_options in __init__.py).
    trackers = [FordPassDeviceTracker(c) for c in coordinators]
    async_add_entities(trackers)
    # v4.8.5: 实体订阅协调器刷新广播——每次刷新成功（即使车况未变）
    # async_write_ha_state；失败轮不触发，保留旧值。v5.9.8 起属性不再
    # 含每轮变化的 last_poll，值未变的实体不会产生 state_changed。
    try:

        def _mk_refresh(ent):
            def _refresh():
                # v4.8.6: hass 注入前（实体添加阶段）回调会被预热刷新触发，
                # 直接 async_write_ha_state 会 RuntimeError: hass is None
                if ent.hass is not None:
                    ent.async_write_ha_state()
            return _refresh

        for _ent in trackers:
            _ent.async_on_remove(
                _ent.coordinator.async_add_listener(_mk_refresh(_ent))
            )
    except Exception:
        _LOGGER.debug("entity listener registration failed", exc_info=True)



class FordPassDeviceTracker(FordPassRestoreMixin, TrackerEntity):
    # v5.9.6: 位置由 coordinator 刷新广播驱动（LBS 每轮拉取），关闭 HA 每 30
    # 秒的平台默认轮询。
    _attr_should_poll = False

    def __init__(self, coordinator: FordPassCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.uid_key}-tracker"
        # v6.0.4: 与定位地址传感器区分（两者此前同名「车辆定位」）——
        # 本实体是 GPS 轨迹（state=home/not_home/zone），故命名「车辆位置」。
        self._attr_name = "车辆位置"
        self._attr_has_entity_name = True
        self._attr_device_info = coordinator.device_info
        self._attr_icon = "mdi:map-marker"

    @property
    def available(self) -> bool:
        """v4.8.7: 有最后已知坐标即可用——修复控制实体 unavailable。"""
        return bool(self.coordinator.data)

    @property
    def source_type(self) -> str:
        """Return the tracker source type (GPS)."""
        return "gps"

    @property
    def state(self) -> str | None:
        # v3.8.9: 重启/重载后坐标未就绪时恢复最后已知位置状态，消除 unavailable
        live = super().state
        if live not in (None, "unknown"):
            return live
        return self._restored if self._restored is not None else live

    @property
    def _location(self) -> dict | None:
        data = self.coordinator.data or {}
        loc = data.get("location")
        return loc if isinstance(loc, dict) else None

    @property
    def latitude(self) -> float | None:
        if self._location and self._location.get("lat"):
            try:
                return float(self._location["lat"])
            except (TypeError, ValueError):
                return None
        return None

    @property
    def longitude(self) -> float | None:
        if self._location and self._location.get("lon"):
            try:
                return float(self._location["lon"])
            except (TypeError, ValueError):
                return None
        return None

    @property
    def gps_accuracy(self) -> int | None:
        return 0

    @property
    def extra_state_attributes(self) -> dict:
        loc = self._location or {}
        # v4.8.9: 修复 v4.8.5 引入的 NameError（attrs 未定义）——该 bug 导致
        # 定位实体写状态失败、永远 unavailable。
        # v5.9.8: last_poll 移出属性（每轮变化 → state_changed 噪音），
        # 权威来源见「云端最后刷新」诊断实体；upload_time 为云端数据时间戳。
        return {
            "address": loc.get("address"),
            "upload_time": loc.get("uploadTime"),
        }
