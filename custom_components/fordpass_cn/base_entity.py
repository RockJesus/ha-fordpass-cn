"""基础实体类."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import FordPassCoordinator


class FordPassBaseEntity(CoordinatorEntity):
    """FordPass 基础实体."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FordPassCoordinator,
        key: str,
        name: str,
    ) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"{coordinator.vin}_{key}"
        self._vin = coordinator.vin

    @property
    def device_info(self) -> DeviceInfo:
        """返回设备信息."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._vin)},
            name=f"{self.coordinator.nickname or self.coordinator.model} ({self._vin[-6:]})",
            manufacturer="Ford" if self.coordinator.vehicle_info.get("brandCode") != "L" else "Lincoln",
            model=f"{self.coordinator.year} {self.coordinator.model}",
            sw_version=None,
            hw_version=self._vin,
        )

    @property
    def available(self) -> bool:
        """实体是否可用."""
        return self.coordinator.data is not None

    def _get_data(self, *keys: str, default=None):
        """从协调器数据中安全获取嵌套值."""
        data = self.coordinator.data
        if not data:
            return default
        for key in keys:
            if isinstance(data, dict):
                data = data.get(key)
            else:
                return default
            if data is None:
                return default
        return data
