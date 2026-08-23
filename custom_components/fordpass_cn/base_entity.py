"""基础实体类."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import FordPassCoordinator


class FordPassBaseEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, key, name):
        super().__init__(coordinator)
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"{coordinator.vin}_{key}"
        self._vin = coordinator.vin

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={(DOMAIN, self._vin)},
            name=f"{self.coordinator.nickname or self.coordinator.model} ({self._vin[-6:]})",
            manufacturer="Ford",
            model=f"{self.coordinator.year} {self.coordinator.model}",
            hw_version=self._vin,
        )

    @property
    def available(self):
        return self.coordinator.data is not None

    def _get_data(self, *keys, default=None):
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
