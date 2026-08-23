"""数据协调器 - 负责车辆状态轮询和远程命令."""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import timedelta
from typing import Any, Optional

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .fordpass_api import CommandResult, FordPassAPI

_LOGGER = logging.getLogger(__name__)
PI = 3.1415926536


def _transform_lat(x, y):
    ret = -100.0 + 2.0*x + 3.0*y + 0.2*y*y + 0.1*x*y + 0.2*math.sqrt(abs(x))
    ret += (20.0*math.sin(6.0*x*PI) + 20.0*math.sin(2.0*x*PI)) * 2.0/3.0
    ret += (20.0*math.sin(y*PI) + 40.0*math.sin(y/3.0*PI)) * 2.0/3.0
    ret += (160.0*math.sin(y/12.0*PI) + 320*math.sin(y*PI/30.0)) * 2.0/3.0
    return ret

def _transform_lon(x, y):
    ret = 300.0 + x + 2.0*y + 0.1*x*x + 0.1*x*y + 0.1*math.sqrt(abs(x))
    ret += (20.0*math.sin(6.0*x*PI) + 20.0*math.sin(2.0*x*PI)) * 2.0/3.0
    ret += (20.0*math.sin(x*PI) + 40.0*math.sin(x/3.0*PI)) * 2.0/3.0
    ret += (150.0*math.sin(x/12.0*PI) + 300.0*math.sin(x/30.0*PI)) * 2.0/3.0
    return ret

def gcj02_to_wgs84(lat, lon):
    a = 6378245.0
    ee = 0.00669342162296594323
    dlat = _transform_lat(lon-105.0, lat-35.0)
    dlon = _transform_lon(lon-105.0, lat-35.0)
    radlat = lat/180.0*PI
    magic = math.sin(radlat)
    magic = 1 - ee*magic*magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat*180.0)/((a*(1-ee))/(magic*sqrtmagic)*PI)
    dlon = (dlon*180.0)/(a/sqrtmagic*math.cos(radlat)*PI)
    return lat-dlat, lon-dlon


class FordPassCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, api, vehicle_info, update_interval):
        super().__init__(hass, _LOGGER, name=vehicle_info.get("vin","unknown"),
                         update_interval=timedelta(minutes=update_interval))
        self._api = api
        self._vin = vehicle_info.get("vin","")
        self._model = vehicle_info.get("modelName","未知车型")
        self._year = vehicle_info.get("modelYear","")
        self._nickname = vehicle_info.get("nickName","")
        self._vehicle_info = vehicle_info
        self._pending_command_id = None
        self._pending_endpoint = None
        self._pending_turn_on = None

    @property
    def vin(self): return self._vin
    @property
    def model(self): return self._model
    @property
    def year(self): return self._year
    @property
    def nickname(self): return self._nickname
    @property
    def vehicle_info(self): return self._vehicle_info
    @property
    def api(self): return self._api

    def set_update_interval(self, minutes):
        self.update_interval = timedelta(minutes=minutes)

    async def _async_update_data(self):
        data = await self._api.get_vehicle_status(self._vin)
        if data is None:
            raise UpdateFailed(f"获取车辆 {self._vin} 状态失败")
        gps = data.get("gps")
        if gps and "latitude" in gps and "longitude" in gps:
            try:
                lat = float(gps["latitude"])
                lon = float(gps["longitude"])
                data["gps"]["latitude"], data["gps"]["longitude"] = gcj02_to_wgs84(lat, lon)
            except (ValueError, TypeError):
                pass
        return data

    async def _wait_for_command(self, endpoint):
        if not self._pending_command_id:
            return False
        for i in range(30):
            try:
                result = await self._api.get_switch_completed(self._vin, endpoint, self._pending_command_id)
                if result == CommandResult.SUCCESS:
                    self._pending_command_id = None
                    self._pending_endpoint = None
                    self._pending_turn_on = None
                    await self.async_request_refresh()
                    return True
                if result == CommandResult.PENDING:
                    await asyncio.sleep(1)
                    continue
                break
            except Exception:
                break
        self._pending_command_id = None
        self._pending_endpoint = None
        self._pending_turn_on = None
        return False

    async def async_set_switch(self, endpoint, turn_on):
        if self._pending_command_id:
            return False
        command_id = await self._api.set_switch(self._vin, endpoint, turn_on)
        if not command_id:
            return False
        self._pending_command_id = command_id
        self._pending_endpoint = endpoint
        self._pending_turn_on = turn_on
        self.hass.async_create_background_task(
            self._wait_for_command(endpoint),
            name=f"fordpass_cn_cmd_{command_id}")
        return True

    async def async_remote_start(self, start=True):
        return await self.async_set_switch("engine/start", start)

    async def async_lock(self, lock=True):
        return await self.async_set_switch("doors/lock", lock)
