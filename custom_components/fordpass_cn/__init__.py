"""The FordPass China integration."""
from __future__ import annotations

import json
import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import FordPassApi, FordPassApiError
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    CONF_TRAFFIC_TOKEN,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DOMAIN,
)
from .coordinator import FordPassCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.LOCK,
    Platform.SWITCH,
    Platform.BUTTON,
    Platform.SENSOR,
    Platform.DEVICE_TRACKER,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = async_get_clientsession(hass)
    api = FordPassApi(session, _LOGGER)
    api.set_token(
        entry.data.get(CONF_ACCESS_TOKEN),
        entry.data.get(CONF_REFRESH_TOKEN),
    )

    def _persist_token(tok: str) -> None:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_ACCESS_TOKEN: tok}
        )

    api.on_token_refresh = _persist_token

    # Apply option changes (scan interval / location tracking) without restart
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    try:
        vehicles = await api.get_vehicles()
    except FordPassApiError as err:
        # a 401 is retried automatically by the client (refresh + retry once)
        raise RuntimeError(f"FordPass login expired: {err}") from err

    if not vehicles:
        raise RuntimeError("No vehicles found on this FordPass account")

    vin = vehicles[0].get("encryptedVin") or vehicles[0].get("vin")
    if not vin:
        raise RuntimeError("Vehicle VIN missing from response")

    _LOGGER.info("fordpass_cn vehicles[0] fields: %s",
                 json.dumps(vehicles[0], ensure_ascii=False, default=str)[:2500])
    vehicle_label = None
    for key in ("localMarketValue", "displayModelName", "modelName",
                "vehicleModel", "model", "carModel", "vehicleType",
                "encryptedNickName", "nickName"):
        val = vehicles[0].get(key)
        if val and not str(val).strip().startswith("SYNC"):
            vehicle_label = str(val)
            break
    if not vehicle_label:
        for key in ("encryptedNickName", "nickName"):
            val = vehicles[0].get(key)
            if val and not str(val).strip().startswith("SYNC"):
                vehicle_label = str(val)
                break

    license_plate = (
        vehicles[0].get("encryptedLicenseplate")
        or vehicles[0].get("licenseplate")
        or None
    )

    nickname = (
        vehicles[0].get("encryptedNickName")
        or vehicles[0].get("nickName")
        or None
    )

    interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS)
    track_location = bool(entry.options.get("track_location", True))
    traffic_token = str(entry.options.get(CONF_TRAFFIC_TOKEN, "") or "").strip()
    coordinator = FordPassCoordinator(
        hass, api, vin, int(interval), vehicle_label, license_plate,
        track_location, nickname, traffic_token,
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
        "vehicles": vehicles,
        "vin": vin,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply option changes immediately without restarting HA.

    Called by Home Assistant whenever the entry options change (e.g. the
    user edits the scan interval or toggles location tracking in the
    integration options flow). We update the live coordinator instead of
    reloading the whole entry, so no entities flicker.
    """
    coordinator: FordPassCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    interval = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS))
    coordinator.update_interval = timedelta(seconds=interval)
    coordinator.track_location = bool(entry.options.get("track_location", True))
    coordinator.traffic_token = str(
        entry.options.get(CONF_TRAFFIC_TOKEN, "") or ""
    ).strip()
    _LOGGER.info(
        "fordpass_cn options updated: scan_interval=%ss track_location=%s traffic_token=%s",
        interval,
        coordinator.track_location,
        bool(coordinator.traffic_token),
    )
    await coordinator.async_request_refresh()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
