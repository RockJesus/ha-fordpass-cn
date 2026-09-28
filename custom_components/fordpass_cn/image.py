"""Image platform: vehicle model picture (e.g. 锐际 Escape render).

The picture (vehicleImageUrl from /v5/vehicles/list) is downloaded once and
persisted to ``/config/www/fordpass_cn/<vin>.png`` so it survives restarts and
never needs to be re-downloaded (or lost).  The entity's state shows the model
name (e.g. 锐际 Escape) for readability — the picture itself is rendered by the
frontend from the image proxy.
"""
from __future__ import annotations

import logging
import os

import aiohttp

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import FordPassCoordinator

_LOGGER = logging.getLogger(__name__)


def _local_path(hass: HomeAssistant, vin: str) -> str:
    return os.path.join(hass.config.path("www"), "fordpass_cn", f"{vin}.png")


class FordPassVehicleImage(ImageEntity):
    """Vehicle model picture, persisted locally and never deleted."""

    def __init__(self, hass: HomeAssistant, coordinator: FordPassCoordinator) -> None:
        super().__init__(hass)
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-vehicle_image"
        self._attr_name = "车辆图片"
        self._attr_has_entity_name = False
        self._attr_device_info = coordinator.device_info
        self._attr_icon = "mdi:car"
        self._attr_content_type = "image/png"
        self._persist_path = _local_path(hass, coordinator.vin)
        self._image_bytes: bytes | None = None

    @property
    def available(self) -> bool:
        return bool(self.coordinator.vehicle_image_url)

    @property
    def state(self) -> str:
        """Show the model name (e.g. 锐际 Escape) instead of an empty value.

        HA image entities normally have no textual state; returning the model
        name makes the card readable while the picture still renders from the
        image proxy.
        """
        return self.coordinator.vehicle_model

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return {
            "vehicle_model": self.coordinator.vehicle_model,
            "image_url": self.coordinator.vehicle_image_url or "",
            "image_path": self._persist_path,
        }

    def _read_local(self) -> bytes | None:
        """Read the persisted picture from www/fordpass_cn (never delete it)."""
        try:
            if os.path.isfile(self._persist_path):
                with open(self._persist_path, "rb") as fh:
                    return fh.read()
        except OSError as exc:
            _LOGGER.warning("vehicle image local read failed: %s", exc)
        return None

    async def _persist(self, data: bytes) -> None:
        try:
            directory = os.path.dirname(self._persist_path)
            os.makedirs(directory, exist_ok=True)
            tmp = f"{self._persist_path}.tmp"
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, self._persist_path)
        except OSError as exc:
            _LOGGER.warning("vehicle image persist failed: %s", exc)

    async def async_image(self) -> bytes | None:
        """Return the picture: local file first, then remote URL once.

        Once downloaded the bytes are kept in memory AND written to the www
        folder, so the image is never deleted or re-downloaded on restart.
        """
        if self._image_bytes:
            return self._image_bytes
        local = self._read_local()
        if local:
            self._image_bytes = local
            return local
        url = self.coordinator.vehicle_image_url
        if not url:
            return None
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
                    self._image_bytes = data
                    await self._persist(data)
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
