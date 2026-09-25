"""The FordPass China integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .api import FordPassApi, FordPassApiError
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_PHONE,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import FordPassCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.LOCK, Platform.SWITCH, Platform.BUTTON, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = hass.helpers.aiohttp_client.async_get_clientsession()
    api = FordPassApi(session, _LOGGER)
    api.set_token(entry.data.get(CONF_ACCESS_TOKEN))

    try:
        vehicles = await api.get_vehicles()
    except FordPassApiError as err:
        # token may have expired -> try refresh with the stored refresh token
        try:
            new = await api.refresh_token(entry.data.get(CONF_REFRESH_TOKEN, ""))
            api.set_token(new["access_token"])
            entry.data = {**entry.data, CONF_ACCESS_TOKEN: new["access_token"]}
            hass.config_entries.async_update_entry(entry, data=entry.data)
            vehicles = await api.get_vehicles()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"FordPass login expired: {err}") from exc

    if not vehicles:
        raise RuntimeError("No vehicles found on this FordPass account")

    vin = vehicles[0].get("encryptedVin") or vehicles[0].get("vin")
    if not vin:
        raise RuntimeError("Vehicle VIN missing from response")

    interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS)
    coordinator = FordPassCoordinator(hass, api, vin, int(interval))
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
        "vehicles": vehicles,
        "vin": vin,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
