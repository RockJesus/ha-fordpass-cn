"""FordPass CN - 福特派中国区 Home Assistant 集成."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    CONF_VEHICLE_TYPE,
    COORDINATOR,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    FORD_API,
    PLATFORMS,
    SERVICE_CLEAR_TOKENS,
    SERVICE_REFRESH_VEHICLE,
    VEHICLES,
)
from .coordinator import FordPassCoordinator
from .fordpass_api import FordPassAPI

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """设置集成（YAML 方式，当前不支持）."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """通过配置流程设置集成."""
    config = entry.data

    vehicle_type = config.get(CONF_VEHICLE_TYPE, "ford")
    refresh_token = config.get(CONF_REFRESH_TOKEN, "")
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

    # 创建 API 客户端
    session = async_create_clientsession(hass)
    api = FordPassAPI(
        session=session,
        vehicle_type=vehicle_type,
        refresh_token=refresh_token,
    )

    # 验证 token
    if not await api.refresh_token():
        _LOGGER.error("FordPass CN: refresh_token 验证失败")
        return False

    # 获取车辆列表
    vehicles = await api.get_vehicles()
    if not vehicles:
        _LOGGER.error("FordPass CN: 未找到已绑定的车辆")
        return False

    # 为每辆车创建协调器
    coordinators: list[FordPassCoordinator] = []
    for vehicle in vehicles:
        vin = vehicle.get("vin", "")
        # 只添加已授权且 TCU 已启用的车辆
        if vehicle.get("vehicleAuthorizationIndicator") != 1:
            _LOGGER.warning("车辆 %s 未授权，跳过", vin)
            continue
        if not vehicle.get("tcuEnabled"):
            _LOGGER.warning("车辆 %s TCU 未启用，跳过", vin)
            continue

        coordinator = FordPassCoordinator(
            hass=hass,
            api=api,
            vehicle_info=vehicle,
            update_interval=scan_interval,
        )
        # 首次刷新数据
        await coordinator.async_config_entry_first_refresh()
        coordinators.append(coordinator)
        _LOGGER.info("已添加车辆: %s (%s %s)", vin, vehicle.get("modelYear"), vehicle.get("modelName"))

    if not coordinators:
        _LOGGER.error("FordPass CN: 没有可用的已授权车辆")
        return False

    # 存储数据
    hass.data[DOMAIN][entry.entry_id] = {
        FORD_API: api,
        VEHICLES: coordinators,
    }

    # 转发到各平台
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # 注册更新监听器
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # 注册服务
    _register_services(hass, entry)

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """处理配置选项更新."""
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    entry_data = hass.data[DOMAIN].get(entry.entry_id)
    if entry_data:
        for coordinator in entry_data[VEHICLES]:
            coordinator.set_update_interval(scan_interval)


def _register_services(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """注册集成服务."""

    async def _refresh_vehicle(call: Any) -> None:
        """手动刷新车辆数据."""
        entry_data = hass.data[DOMAIN].get(entry.entry_id)
        if not entry_data:
            return
        for coordinator in entry_data[VEHICLES]:
            await coordinator.async_request_refresh()

    async def _clear_tokens(call: Any) -> None:
        """清除 token（需要重新配置）."""
        entry_data = hass.data[DOMAIN].get(entry.entry_id)
        if not entry_data:
            return
        api: FordPassAPI = entry_data[FORD_API]
        api.clear_tokens()
        _LOGGER.warning("FordPass CN tokens 已清除，请重新配置集成")

    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH_VEHICLE):
        hass.services.async_register(DOMAIN, SERVICE_REFRESH_VEHICLE, _refresh_vehicle)
    if not hass.services.has_service(DOMAIN, SERVICE_CLEAR_TOKENS):
        hass.services.async_register(DOMAIN, SERVICE_CLEAR_TOKENS, _clear_tokens)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """卸载集成."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """重新加载集成."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
