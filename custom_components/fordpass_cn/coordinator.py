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


# ============================================================
# 坐标转换 - 福特中国返回的是 GCJ-02（火星坐标）
# 需要转换为 WGS-84 供 HA 使用
# ============================================================

def _transform_lat(x: float, y: float) -> float:
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * PI) + 20.0 * math.sin(2.0 * x * PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * PI) + 40.0 * math.sin(y / 3.0 * PI)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * PI) + 320 * math.sin(y * PI / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lon(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * PI) + 20.0 * math.sin(2.0 * x * PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * PI) + 40.0 * math.sin(x / 3.0 * PI)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * PI) + 300.0 * math.sin(x / 30.0 * PI)) * 2.0 / 3.0
    return ret


def gcj02_to_wgs84(lat: float, lon: float) -> tuple[float, float]:
    """GCJ-02 转 WGS-84."""
    a = 6378245.0
    ee = 0.00669342162296594323
    dlat = _transform_lat(lon - 105.0, lat - 35.0)
    dlon = _transform_lon(lon - 105.0, lat - 35.0)
    radlat = lat / 180.0 * PI
    magic = math.sin(radlat)
    magic = 1 - ee * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((a * (1 - ee)) / (magic * sqrtmagic) * PI)
    dlon = (dlon * 180.0) / (a / sqrtmagic * math.cos(radlat) * PI)
    return lat - dlat, lon - dlon


class FordPassCoordinator(DataUpdateCoordinator):
    """单辆车的数据协调器."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: FordPassAPI,
        vehicle_info: dict,
        update_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"fordpass_cn_{vehicle_info.get('vin', 'unknown')}",
            update_interval=timedelta(minutes=update_interval),
        )
        self._api = api
        self._vin = vehicle_info.get("vin", "")
        self._model = vehicle_info.get("modelName", "未知车型")
        self._year = vehicle_info.get("modelYear", "")
        self._nickname = vehicle_info.get("nickName", "")
        self._vehicle_info = vehicle_info

        # 命令状态
        self._pending_command_id: Optional[str] = None
        self._pending_endpoint: Optional[str] = None
        self._pending_turn_on: Optional[bool] = None

    # ----------------------------------------------------------
    # 属性
    # ----------------------------------------------------------

    @property
    def vin(self) -> str:
        return self._vin

    @property
    def model(self) -> str:
        return self._model

    @property
    def year(self) -> str:
        return self._year

    @property
    def nickname(self) -> str:
        return self._nickname

    @property
    def vehicle_info(self) -> dict:
        return self._vehicle_info

    @property
    def api(self) -> FordPassAPI:
        return self._api

    def set_update_interval(self, minutes: int) -> None:
        """更新轮询间隔."""
        self.update_interval = timedelta(minutes=minutes)
        _LOGGER.debug("车辆 %s 轮询间隔设置为 %d 分钟", self._vin, minutes)

    # ----------------------------------------------------------
    # 数据更新
    # ----------------------------------------------------------

    async def _async_update_data(self) -> dict[str, Any]:
        """从 API 获取车辆状态."""
        _LOGGER.debug("正在更新车辆 %s 状态...", self._vin)
        try:
            data = await self._api.get_vehicle_status(self._vin)
            if data is None:
                raise UpdateFailed(f"获取车辆 {self._vin} 状态失败")

            # 坐标转换
            gps = data.get("gps")
            if gps and "latitude" in gps and "longitude" in gps:
                try:
                    lat = float(gps["latitude"])
                    lon = float(gps["longitude"])
                    data["gps"]["latitude"], data["gps"]["longitude"] = gcj02_to_wgs84(lat, lon)
                except (ValueError, TypeError):
                    pass

            return data
        except Exception as err:
            raise UpdateFailed(f"更新车辆状态出错: {err}") from err

    # ----------------------------------------------------------
    # 远程命令
    # ----------------------------------------------------------

    async def _wait_for_command(self, endpoint: str) -> bool:
        """等待命令执行完成，最多轮询 30 次."""
        if not self._pending_command_id:
            return False

        for i in range(30):
            try:
                result = await self._api.get_switch_completed(
                    self._vin, endpoint, self._pending_command_id
                )
                if result == CommandResult.SUCCESS:
                    _LOGGER.debug("命令 %s 执行成功", self._pending_command_id)
                    self._clear_pending_command()
                    await self.async_request_refresh()
                    return True
                if result == CommandResult.PENDING:
                    await asyncio.sleep(1)
                    continue
                _LOGGER.error("命令 %s 执行失败", self._pending_command_id)
                break
            except Exception as err:
                _LOGGER.error("检查命令状态出错: %s", err)
                break

        self._clear_pending_command()
        return False

    def _clear_pending_command(self) -> None:
        self._pending_command_id = None
        self._pending_endpoint = None
        self._pending_turn_on = None

    def has_pending_command(self, endpoint: str, turn_on: bool) -> bool:
        """检查是否有相同的待执行命令."""
        return (
            self._pending_command_id is not None
            and self._pending_endpoint == endpoint
            and self._pending_turn_on == turn_on
        )

    async def async_set_switch(self, endpoint: str, turn_on: bool) -> bool:
        """发送开关类远程命令.

        Args:
            endpoint: 命令端点，如 "engine/start", "doors/lock"
            turn_on: True=开启/锁车, False=关闭/解锁
        """
        if self._pending_command_id:
            _LOGGER.warning("已有待执行命令 %s，忽略新命令", self._pending_command_id)
            return False

        command_id = await self._api.set_switch(self._vin, endpoint, turn_on)
        if not command_id:
            _LOGGER.error("发送命令失败: %s", endpoint)
            return False

        self._pending_command_id = command_id
        self._pending_endpoint = endpoint
        self._pending_turn_on = turn_on

        # 后台等待命令完成
        self.hass.async_create_background_task(
            self._wait_for_command(endpoint),
            name=f"fordpass_cn_command_{command_id}",
        )
        return True

    async def async_remote_start(self, start: bool = True) -> bool:
        """远程启动/关闭发动机."""
        return await self.async_set_switch("engine/start", start)

    async def async_lock(self, lock: bool = True) -> bool:
        """锁车/解锁."""
        return await self.async_set_switch("doors/lock", lock)
