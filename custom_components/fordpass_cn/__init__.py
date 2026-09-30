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
from . import capability
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_COORDINATE_SYSTEM,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    DEFAULT_COORDINATE_SYSTEM,
    DEFAULT_SCAN_INTERVAL_MINUTES,
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
    Platform.IMAGE,
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

    # v2.7.8: scan_interval is stored/entered in MINUTES (default 30).
    interval = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES))
    track_location = bool(entry.options.get("track_location", True))
    coordinate_system = entry.options.get(
        CONF_COORDINATE_SYSTEM, DEFAULT_COORDINATE_SYSTEM
    )
    vehicle_image_url = (
        vehicles[0].get("vehicleImageUrl")
        or vehicles[0].get("imageUrl")
        or None
    )
    coordinator = FordPassCoordinator(
        hass, api, vin, interval, vehicle_label, license_plate,
        track_location, nickname, vehicle_image_url, coordinate_system,
    )
    await coordinator.async_config_entry_first_refresh()

    # v2.10.0: 全车型兼容检测日志——基于 vehicle-status 数据判定车型能力，
    # 与实体创建保持一致（无效字段/不支持功能不创建实体）。
    try:
        _vs = coordinator.data.get("vehiclestatus", {}) or {}
        _caps = {
            "remote_ctrl": capability.usable(_vs, [["crccFlag"]]),
            "tailgate": capability.usable(
                _vs, [["doorStatus", "tailgateDoor"], ["doorStatus", "innerTailgateDoor"]]
            ),
            "ev_charge": capability.usable(_vs, [["chargingStatus"], ["plugStatus"]]),
            "ev_battery": capability.usable(_vs, [["batteryFillLevel"]]),
            "diesel": capability.usable(_vs, [["dieselSystemStatus", "exhaustFluidLevel"]]),
            "trailer": capability.is_on(_vs, [["TPMS", "dualRearWheel"]]),
            "cabin_temp": capability.usable(_vs, [["CabnAmbTeActl"]]),
            "deep_sleep": capability.usable(_vs, [["deepSleepInProgress"]]),
        }
        _LOGGER.info(
            "fordpass_cn 全车型能力检测: %s",
            json.dumps(_caps, ensure_ascii=False),
        )
    except Exception:  # noqa: BLE001 - 检测失败不影响集成运行
        _LOGGER.debug("fordpass_cn capability detection failed", exc_info=True)

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
    interval = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES))
    coordinator.update_interval = timedelta(minutes=interval)
    coordinator.track_location = bool(entry.options.get("track_location", True))
    coordinator.coordinate_system = entry.options.get(
        CONF_COORDINATE_SYSTEM, DEFAULT_COORDINATE_SYSTEM
    )
    _LOGGER.info(
        "fordpass_cn options updated: scan_interval=%smin track_location=%s coordinate_system=%s",
        interval,
        coordinator.track_location,
        coordinator.coordinate_system,
    )
    await coordinator.async_request_refresh()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
