"""DataUpdateCoordinator for the FordPass China integration."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import FordPassApi, FordPassApiError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class FordPassCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch vehicle list + status on a schedule."""

    def __init__(self, hass: HomeAssistant, api: FordPassApi, vin: str, interval: int) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-{vin[-6:]}",
            update_interval=timedelta(seconds=interval),
        )
        self.api = api
        self.vin = vin

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            status = await self.api.get_vehicle_status(self.vin)
            return {"status": status, "vehiclestatus": status.get("vehiclestatus", status)}
        except FordPassApiError as err:
            raise UpdateFailed(f"FordPass update failed: {err}") from err
        except asyncio.TimeoutError as err:
            raise UpdateFailed("FordPass update timed out") from err
