"""DataUpdateCoordinator for the FordPass China integration."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import FordPassApi, FordPassApiError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class FordPassCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch vehicle list + status on a schedule."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: FordPassApi,
        vin: str,
        interval: int,
        vehicle_name: str | None = None,
        license_plate: str | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-{vin[-6:]}",
            update_interval=timedelta(seconds=interval),
        )
        self.api = api
        self.vin = vin
        self.license_plate = license_plate
        self._vehicle_name = vehicle_name or f"Ford {vin[-6:]}"

    @property
    def device_info(self) -> dict[str, Any]:
        """One shared device for every entity of this vehicle."""
        return dr.DeviceInfo(
            identifiers={(DOMAIN, self.vin)},
            name=self._vehicle_name,
            manufacturer="Ford",
            model=self._vehicle_name,
            sw_version="6.14.0",
        )

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            status = await self.api.get_vehicle_status(self.vin)
            data: dict[str, Any] = {
                "status": status,
                "vehiclestatus": status.get("vehiclestatus", status),
            }
        except FordPassApiError as err:
            raise UpdateFailed(f"FordPass update failed: {err}") from err
        except asyncio.TimeoutError as err:
            raise UpdateFailed("FordPass update timed out") from err

        # Best-effort location (LBS endpoint may require an extra APIM
        # subscription key; never fail the whole update for it).
        try:
            loc = await self.api.get_location(self.vin)
            if isinstance(loc, dict) and loc.get("lat"):
                data["location"] = loc
            else:
                data["location"] = None
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("FordPass location fetch failed: %s", exc)
            data["location"] = None
        return data
