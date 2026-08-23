"""FordPass CN - 福特派中国区 Home Assistant 集成."""

from __future__ import annotations
import logging
from typing import Any
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.typing import ConfigType

from .const import (CONF_ACCESS_TOKEN, CONF_REFRESH_TOKEN, CONF_SCAN_INTERVAL,
    CONF_VEHICLE_TYPE, DEFAULT_SCAN_INTERVAL, DOMAIN, FORD_API, PLATFORMS,
    SERVICE_CLEAR_TOKENS, SERVICE_REFRESH_VEHICLE, VEHICLES)
from .coordinator import FordPassCoordinator
from .fordpass_api import FordPassAPI

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass, config):
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass, entry):
    config = entry.data
    vehicle_type = config.get(CONF_VEHICLE_TYPE, "ford")
    refresh_token = config.get(CONF_REFRESH_TOKEN)
    access_token = config.get(CONF_ACCESS_TOKEN)
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

    session = async_create_clientsession(hass)
    api = FordPassAPI(session=session, vehicle_type=vehicle_type,
        refresh_token=refresh_token, access_token=access_token)

    vehicles = await api.get_vehicles()
    if not vehicles:
        _LOGGER.error("FordPass CN: 无法获取车辆列表，token可能无效。认证模式: %s", api.auth_mode)
        vehicles = []

    coordinators = []
    for vehicle in vehicles:
        vin = vehicle.get("vin", "")
        if vehicle.get("vehicleAuthorizationIndicator") != 1:
            continue
        if not vehicle.get("tcuEnabled"):
            continue
        coordinator = FordPassCoordinator(hass=hass, api=api, vehicle_info=vehicle, update_interval=scan_interval)
        await coordinator.async_config_entry_first_refresh()
        coordinators.append(coordinator)

    hass.data[DOMAIN][entry.entry_id] = {FORD_API: api, VEHICLES: coordinators}
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    _register_services(hass, entry)
    return True


async def _async_update_listener(hass, entry):
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    entry_data = hass.data[DOMAIN].get(entry.entry_id)
    if entry_data:
        for coordinator in entry_data[VEHICLES]:
            coordinator.set_update_interval(scan_interval)


def _register_services(hass, entry):
    async def _refresh_vehicle(call):
        entry_data = hass.data[DOMAIN].get(entry.entry_id)
        if entry_data:
            for coordinator in entry_data[VEHICLES]:
                await coordinator.async_request_refresh()

    async def _clear_tokens(call):
        entry_data = hass.data[DOMAIN].get(entry.entry_id)
        if entry_data:
            entry_data[FORD_API].clear_tokens()

    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH_VEHICLE):
        hass.services.async_register(DOMAIN, SERVICE_REFRESH_VEHICLE, _refresh_vehicle)
    if not hass.services.has_service(DOMAIN, SERVICE_CLEAR_TOKENS):
        hass.services.async_register(DOMAIN, SERVICE_CLEAR_TOKENS, _clear_tokens)


async def async_unload_entry(hass, entry):
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
