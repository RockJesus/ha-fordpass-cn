"""DataUpdateCoordinator for the FordPass China integration."""
from __future__ import annotations

import asyncio
import datetime
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
        track_location: bool = True,
        nickname: str | None = None,
        vehicle_image_url: str | None = None,
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
        self.nickname = nickname
        self.track_location = track_location
        self.vehicle_image_url = vehicle_image_url
        self._vehicle_name = vehicle_name or f"Ford {vin[-6:]}"
        # UTC timestamp of the most recent *successful* fetch — manual refresh
        # feedback for the "最后刷新时间" sensor (v2.6.7).
        self._last_success_ts: str | None = None

    @property
    def last_success_ts(self) -> str | None:
        """Local time string of the last successful data fetch."""
        return self._last_success_ts

    @property
    def vehicle_model(self) -> str:
        """车型名（如「锐际 Escape」），用于车辆图片实体显示。"""
        return self._vehicle_name

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

        # Successful fetch — remember the time for manual-refresh feedback.
        try:
            now = datetime.datetime.now(datetime.timezone.utc).astimezone()
            self._last_success_ts = now.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:  # noqa: BLE001
            pass

        # Best-effort location (only when the user enabled tracking; the LBS
        # gateway is a separate APIM endpoint and costs a remote round trip).
        if self.track_location:
            try:
                loc = await self.api.get_location(self.vin)
                if isinstance(loc, dict) and loc.get("lat"):
                    data["location"] = loc
                else:
                    data["location"] = None
            except Exception as exc:  # noqa: BLE001
                self.logger.debug("FordPass location fetch failed: %s", exc)
                data["location"] = None

        # Vehicle health alerts (plaintext Chinese headlines) — best effort.
        try:
            alerts = await self.api.get_active_alerts(self.vin)
            data["active_alerts"] = alerts or []
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("FordPass active alerts fetch failed: %s", exc)
            data["active_alerts"] = []
        return data
