"""DataUpdateCoordinator for the FordPass China integration."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
import time
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
        # active_alerts 失败降级：连续失败 2 次后 1 小时内不再请求，
        # 避免接口 404 时每轮都发无效请求并刷日志噪音（v2.7.5）。
        self._alerts_fail = 0
        self._alerts_hold_until: float | None = None
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

    async def force_refresh(self) -> None:
        """Fetch data immediately and push it to entities (v2.7.0).

        HA's ``async_request_refresh`` only executes right away when the last
        poll is older than ``update_interval``; otherwise it re-schedules the
        refresh to the next interval tick, which makes a manual "刷新车辆状态"
        button appear dead.  This method bypasses that throttle: it fetches
        now and pushes the result with ``async_set_updated_data``.
        """
        try:
            data = await self._async_update_data()
        except Exception as exc:  # noqa: BLE001 - keep old data, log clearly
            self.logger.error("FordPass forced refresh failed: %s", exc)
            raise UpdateFailed(f"FordPass update failed: {exc}") from exc
        self.async_set_updated_data(data)

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
        # 失败降级：连续失败 2 次 → 抑制 1 小时再试（v2.7.5）。
        now = time.monotonic()
        if self._alerts_hold_until is None or now >= self._alerts_hold_until:
            try:
                alerts = await self.api.get_active_alerts(self.vin)
                data["active_alerts"] = alerts or []
                self._alerts_fail = 0
                self._alerts_hold_until = None
            except Exception as exc:  # noqa: BLE001
                self.logger.debug("FordPass active alerts fetch failed: %s", exc)
                data["active_alerts"] = []
                self._alerts_fail += 1
                if self._alerts_fail >= 2:
                    self._alerts_hold_until = now + 3600
                    self._alerts_fail = 0
                    self.logger.info(
                        "FordPass active alerts failing; suppressing retries for 1h"
                    )
        else:
            data["active_alerts"] = []
        return data
