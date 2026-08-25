"""车辆协调器：轮询状态、发送远程控制命令。"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .ford.fordpass import CommandResult, FordPass

_LOGGER = logging.getLogger(__name__)

PI = 3.141592653589793


# ---------------------------------------------------------------------------
# 坐标转换：GCJ-02（国测局/高德火星坐标，App 返回值）→ WGS-84（标准 GPS）
# ---------------------------------------------------------------------------
def _transformlat(x: float, y: float) -> float:
    ret = (
        -100.0
        + 2.0 * x
        + 3.0 * y
        + 0.2 * y * y
        + 0.1 * x * y
        + 0.2 * math.sqrt(abs(x))
    )
    ret += (20.0 * math.sin(6.0 * x * PI) + 20.0 * math.sin(2.0 * x * PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * PI) + 40.0 * math.sin(y / 3.0 * PI)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * PI) + 320 * math.sin(y * PI / 30.0)) * 2.0 / 3.0
    return ret


def _transformlon(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * PI) + 20.0 * math.sin(2.0 * x * PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * PI) + 40.0 * math.sin(x / 3.0 * PI)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * PI) + 300.0 * math.sin(x / 30.0 * PI)) * 2.0 / 3.0
    return ret


def gps_unshift(wglat: float, wglon: float) -> tuple[float, float]:
    """GCJ-02 → WGS-84。"""
    a = 6378245.0
    ee = 0.00669342162296594323
    dlat = _transformlat(wglon - 105.0, wglat - 35.0)
    dlon = _transformlon(wglon - 105.0, wglat - 35.0)
    radlat = wglat / 180.0 * PI
    magic = math.sin(radlat)
    magic = 1 - ee * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((a * (1 - ee)) / (magic * sqrtmagic) * PI)
    dlon = (dlon * 180.0) / (a / sqrtmagic * math.cos(radlat) * PI)
    return wglat - dlat, wglon - dlon


class FordVehicle(DataUpdateCoordinator):
    """单台车辆的协调器。"""

    def __init__(
        self,
        hass: HomeAssistant,
        fordpass: FordPass,
        vehicle_info: dict,
        update_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=vehicle_info.get("vin", "ford"),
            update_interval=timedelta(minutes=update_interval),
        )
        self._fordpass = fordpass
        self._vin = vehicle_info.get("vin")
        self._model = vehicle_info.get("modelName", "")
        self._year = vehicle_info.get("modelYear", "")
        self._vehicle_name = vehicle_info.get("nickName") or vehicle_info.get("modelName") or self._vin
        self._commandid: str | None = None
        self._command_end_point: str | None = None
        self._command_turn_on: bool | None = None

    @property
    def vin(self) -> str:
        return self._vin

    @property
    def model(self) -> str:
        return self._model

    @property
    def year(self) -> str:
        return str(self._year)

    @property
    def vehicle_name(self) -> str:
        return self._vehicle_name

    @property
    def update_interval_seconds(self) -> int:
        return int(self.update_interval.total_seconds() // 60) if self.update_interval else 5

    @update_interval_seconds.setter
    def update_interval_seconds(self, minutes: int) -> None:
        self.update_interval = timedelta(minutes=int(minutes))

    async def _async_update_data(self):
        _LOGGER.debug("[%s] Updating vehicle status...", self._vin)
        try:
            async with asyncio.timeout(25):
                data = await self._fordpass.get_vehicle_status(self._vin)
                if data is None:
                    raise UpdateFailed("无法获取车辆状态（token 或接口异常）")
                # GPS 坐标转换：火星坐标 → WGS-84
                gps = data.get("gps")
                if isinstance(gps, dict) and gps.get("latitude") and gps.get("longitude"):
                    try:
                        wglat = float(gps["latitude"])
                        wglon = float(gps["longitude"])
                        gps["latitude"], gps["longitude"] = gps_unshift(wglat, wglon)
                        gps["source_type"] = "gps"
                    except (TypeError, ValueError):
                        _LOGGER.warning("[%s] GPS 坐标解析失败", self._vin)
                return data
        except TimeoutError:
            _LOGGER.warning("[%s] Vehicle status update timed out", self._vin)
            raise UpdateFailed("车辆状态更新超时") from None

    # ------------------------------------------------------------------
    # 远程命令
    # ------------------------------------------------------------------
    def check_command_pending(self, end_point: str, turn_on: bool) -> bool:
        return bool(
            self._commandid
            and self._command_end_point == end_point
            and self._command_turn_on == turn_on
        )

    async def _check_command(self, end_point: str) -> None:
        refresh_data = False
        try:
            for i in range(30):
                if i >= 25:
                    _LOGGER.error(
                        "[%s] Command %s at %s timed out",
                        self._vin,
                        self._commandid,
                        self._command_end_point,
                    )
                    break
                result = await self._fordpass.async_get_switch_completed(
                    self._vin, end_point, self._commandid
                )
                if result == CommandResult.PENDING:
                    await asyncio.sleep(1)
                    continue
                if result == CommandResult.SUCCESS:
                    refresh_data = True
                break
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("[%s] Command check error: %s", self._vin, err)
        self._commandid = None
        self._command_end_point = None
        self._command_turn_on = None
        if refresh_data:
            await self.async_request_refresh()

    async def async_set_switch(self, end_point: str, turn_on: bool) -> None:
        if self._commandid:
            _LOGGER.warning("[%s] Last command %s is pending", self._vin, self._commandid)
            return
        self._commandid = await self._fordpass.async_set_switch(self._vin, end_point, turn_on)
        if self._commandid:
            self._command_end_point = end_point
            self._command_turn_on = turn_on
            self.hass.loop.create_task(self._check_command(end_point))
        else:
            _LOGGER.error("[%s] Failed to dispatch command %s", self._vin, end_point)
