"""Image platform: vehicle model picture (e.g. 锐际 Escape render)."""
from __future__ import annotations

import logging
import time

import aiohttp

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import FordPassCoordinator

_LOGGER = logging.getLogger(__name__)

_CACHE_TTL = 3600  # 1 hour


class FordPassVehicleImage(ImageEntity):
    """Show the vehicle model picture from the Ford CDN.

    The picture URL (vehicleImageUrl from /v5/vehicles/list, e.g. the 锐际 Escape
    45° render) is fixed per vehicle; we download it once and cache the bytes.
    """

    def __init__(self, hass: HomeAssistant, coordinator: FordPassCoordinator) -> None:
        super().__init__(hass)
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-vehicle_image"
        self._attr_name = "车辆图片"
        self._attr_has_entity_name = False
        self._attr_device_info = coordinator.device_info
        self._attr_icon = "mdi:car"
        self._attr_content_type = "image/png"
        self._cache: tuple[bytes, float] | None = None

    @property
    def available(self) -> bool:
        return bool(self.coordinator.vehicle_image_url)

    async def async_image(self) -> bytes | None:
        url = self.coordinator.vehicle_image_url
        if not url:
            return None
        now = time.monotonic()
        if self._cache and now - self._cache[1] < _CACHE_TTL:
            return self._cache[0]
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=20),
                headers={"user-agent": "Mozilla/5.0 (FordPass HA integration)"},
            ) as resp:
                if resp.status != 200:
                    _LOGGER.warning("vehicle image fetch failed: HTTP %s", resp.status)
                    return None
                data = await resp.read()
                if data:
                    self._cache = (data, now)
                return data or None
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("vehicle image fetch error: %s", exc)
            return None


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: FordPassCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    if coordinator.vehicle_image_url:
        async_add_entities([FordPassVehicleImage(hass, coordinator)])
