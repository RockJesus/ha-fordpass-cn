"""FordPass China (福特派) Home Assistant 集成。"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_SCAN_INTERVAL, CONF_USERNAME
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .const import (
    CONF_REFRESH_TOKEN,
    CONF_VEHICLE_TYPE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    FORD_VEHICLES,
    PLATFORMS,
)
from .ford.fordpass import FordPass
from .vehicle import FordVehicle

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """一次性初始化。"""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """加载一个配置条目：建立 API 客户端 + 车辆协调器 + 各平台。"""
    config = entry.data
    vehicle_type = config.get(CONF_VEHICLE_TYPE, "ford")
    username = config.get(CONF_USERNAME)
    password = config.get(CONF_PASSWORD)
    refresh_token = config.get(CONF_REFRESH_TOKEN)

    session = async_create_clientsession(hass)
    fordpass = FordPass(
        session=session,
        username=username,
        password=password,
        vehicle_type=vehicle_type,
        refresh_token=refresh_token,
    )

    # 先验证令牌可用
    if not await fordpass.refresh_token():
        _LOGGER.error(
            "[%s] 无法用 refresh_token 换到令牌，请重新配置（token 失效或被福特撤销）",
            entry.title,
        )
        return False

    vehicles = await fordpass.get_vehicles()
    if not vehicles:
        _LOGGER.warning("[%s] 未在账号下发现已授权车辆", entry.title)
        # 仍允许加载，等待用户车辆在 App 中完成激活后刷新即可

    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    coordinator_list: list[FordVehicle] = []
    if vehicles:
        for s_vehicle in vehicles:
            # 兼容新旧字段：授权标记 + TCU 启用
            authorized = _as_bool(
                s_vehicle.get("vehicleAuthorizationIndicator")
                or s_vehicle.get("authorizationIndicator")
                or s_vehicle.get("isActive")
            )
            tcu_enabled = _as_bool(s_vehicle.get("tcuEnabled"))
            if not authorized or not tcu_enabled:
                _LOGGER.debug(
                    "[%s] 跳过车辆 %s（authorized=%s, tcu=%s）",
                    entry.title,
                    s_vehicle.get("vin"),
                    authorized,
                    tcu_enabled,
                )
                continue
            coord = FordVehicle(hass, fordpass, s_vehicle, scan_interval)
            coordinator_list.append(coord)
            await coord.async_config_entry_first_refresh()

    hass.data[DOMAIN].setdefault(entry.entry_id, {})
    hass.data[DOMAIN][entry.entry_id][FORD_VEHICLES] = coordinator_list

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(update_listener))
    await _async_register_services(hass)
    return True


async def _async_register_services(hass: HomeAssistant) -> None:
    """注册 refresh_status / clear_tokens 服务。"""

    async def _handle_refresh_status(call: ServiceCall) -> None:
        vin = (call.data or {}).get("vin")
        for entry_id, entry_data in hass.data.get(DOMAIN, {}).items():
            for coordinator in entry_data.get(FORD_VEHICLES, []):
                if vin and coordinator.vin.upper() != vin.upper():
                    continue
                await coordinator.async_request_refresh()

    async def _handle_clear_tokens(call: ServiceCall) -> None:
        for entry in hass.config_entries.async_entries(DOMAIN):
            # 删除集成缓存的令牌，之后需重新配置
            await hass.config_entries.async_remove(entry.entry_id)

    if hass.services.has_service(DOMAIN, "refresh_status"):
        return
    hass.services.async_register(DOMAIN, "refresh_status", _handle_refresh_status)
    hass.services.async_register(DOMAIN, "clear_tokens", _handle_clear_tokens)


def _as_bool(value: Any) -> bool:
    if value is None:
        return True  # 缺省视为通过，避免因字段改名误跳过车辆
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return False


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """卸载配置条目。"""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """选项变更时更新轮询间隔。"""
    update_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    for coordinator in hass.data[DOMAIN].get(entry.entry_id, {}).get(FORD_VEHICLES, []):
        coordinator.update_interval_seconds = update_interval
