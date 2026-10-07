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
    CONF_CHIRP_TYPE,
    CONF_COORDINATE_SYSTEM,
    CONF_HONK_DURATION,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    DEFAULT_CHIRP_TYPE,
    DEFAULT_COORDINATE_SYSTEM,
    DEFAULT_HONK_DURATION,
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
    Platform.SELECT,
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

    # v3.1.6: 多 VIN 支持——账号下每一辆车建立独立的 coordinator / 设备 /
    # 实体组。各平台按各自车型的 vehicle-status 数据创建实体（无效字段不建），
    # 因此其他用户用各自福特派账号登录时，只加载自己车型真正支持的设备与实体。
    _LOGGER.info(
        "fordpass_cn vehicles: %d 辆, fields[0]=%s",
        len(vehicles),
        json.dumps(vehicles[0], ensure_ascii=False, default=str)[:2500],
    )

    # v2.7.8: scan_interval is stored/entered in MINUTES (default 30).
    interval = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES))
    track_location = bool(entry.options.get("track_location", True))
    coordinate_system = entry.options.get(
        CONF_COORDINATE_SYSTEM, DEFAULT_COORDINATE_SYSTEM
    )

    def _vehicle_label(vehicle: dict) -> str | None:
        for key in ("localMarketValue", "displayModelName", "modelName",
                    "vehicleModel", "model", "carModel", "vehicleType",
                    "encryptedNickName", "nickName"):
            val = vehicle.get(key)
            if val and not str(val).strip().startswith("SYNC"):
                return str(val)
        for key in ("encryptedNickName", "nickName"):
            val = vehicle.get(key)
            if val and not str(val).strip().startswith("SYNC"):
                return str(val)
        return None

    coordinators = []
    for vehicle in vehicles:
        vin = vehicle.get("encryptedVin") or vehicle.get("vin")
        if not vin:
            _LOGGER.warning("fordpass_cn skip vehicle without VIN: %s",
                            json.dumps(vehicle, ensure_ascii=False, default=str)[:500])
            continue
        license_plate = (
            vehicle.get("encryptedLicenseplate") or vehicle.get("licenseplate") or None
        )
        nickname = vehicle.get("encryptedNickName") or vehicle.get("nickName") or None
        # v3.3.9: 拆开两张车型图——vehicleImageUrl=侧视（45view）、imageUrl=俯视
        # （birdview，HAR 2026-10-03 实测字段；两字段各自独立创建图片实体）
        vehicle_image_url = vehicle.get("vehicleImageUrl") or None
        vehicle_overlook_url = vehicle.get("imageUrl") or None
        # v3.3.9: 车辆信息（jointVenture/localMarketValue/modelYear/vehicleType/
        # fuelType——vehicles/list 明文字段，HAR 实测；「车辆信息」传感器读取）
        vehicle_info = {
            k: vehicle.get(k)
            for k in (
                "jointVenture", "localMarketValue", "modelYear", "vehicleType",
                "fuelType", "warrantyStartDate",
            )
        }
        coordinator = FordPassCoordinator(
            hass, api, vin, interval, _vehicle_label(vehicle), license_plate,
            track_location, nickname, vehicle_image_url, vehicle_overlook_url,
            vehicle_info, coordinate_system,
            entry_id=entry.entry_id,
            car_id=vehicle.get("encryptedCarId") or None,
        )
        coordinators.append(coordinator)

    if not coordinators:
        raise RuntimeError("No usable vehicles found on this FordPass account")

    for coordinator in coordinators:
        # v3.3.3: 先恢复最后已知数据（实体立即有值，不显示 unavailable/
        # unknown 中间态），再后台刷新；云端暂不可达时首次刷新失败
        # 不阻塞集成加载——实体用最后数据创建，轮询恢复后自动更新。
        await coordinator.async_load_last_data()
        # v3.3.7: 探测云端命令白名单（全车型实体动态创建依据）——失败不阻塞
        try:
            await coordinator.async_probe_capabilities()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("fordpass_cn capability probe skipped: %s", exc)
        try:
            await coordinator.async_config_entry_first_refresh()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning(
                "fordpass_cn 首次刷新失败（保留最后已知状态，后台自动重试）: %s",
                exc,
            )
            # 立即再触发一次刷新（失败会按 update_interval 重新调度），
            # 云端恢复后数据自动更新、实体保留最后已知状态。
            await coordinator.async_request_refresh()

    # v2.10.0: 全车型兼容检测日志——基于 vehicle-status 数据判定车型能力，
    # 与实体创建保持一致（无效字段/不支持功能不创建实体）。
    for coordinator in coordinators:
        try:
            _vs = coordinator.data.get("vehiclestatus", {}) or {}
            _caps = {
                "vin": coordinator.vin,
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

    # v3.0.8: 鸣笛寻车设置（持续时长/鸣笛类型）初始值——来自 entry.options
    # （select 实体修改时实时写入），默认时长 10 秒、汽笛长鸣（ChirpType=3）。
    # 云端 UserPreference 为账户级，多车共享同一份设置。
    honk_settings = {
        CONF_HONK_DURATION: int(
            entry.options.get(CONF_HONK_DURATION, DEFAULT_HONK_DURATION)
        ),
        CONF_CHIRP_TYPE: entry.options.get(CONF_CHIRP_TYPE, DEFAULT_CHIRP_TYPE),
    }
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "api": api,
        "coordinator": coordinators[0],
        "coordinators": coordinators,
        "vehicles": vehicles,
        "vin": coordinators[0].vin,
        "honk_settings": honk_settings,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply option changes immediately without restarting HA.

    Called by Home Assistant whenever the entry options change (e.g. the
    user edits the scan interval or toggles location tracking in the
    integration options flow). We update the live coordinator instead of
    reloading the whole entry, so no entities flicker.

    v3.1.1: 选项可能在集成尚未加载完成时被保存（重启中 / 加载失败 /
    已卸载后再次打开选项页）——此时 hass.data[DOMAIN] 还不存在，直接
    索引会抛 KeyError。防御：未加载时安全跳过，新选项会由下次
    async_setup_entry 从 entry.options 读取生效（无状态丢失）。
    """
    payload = (hass.data.get(DOMAIN) or {}).get(entry.entry_id)
    coordinators = (
        payload.get("coordinators")
        if isinstance(payload, dict) and payload.get("coordinators")
        else ([payload["coordinator"]] if isinstance(payload, dict) else None)
    )
    if not coordinators:
        _LOGGER.info(
            "fordpass_cn options saved before entry loaded (entry_id=%s); "
            "will take effect on next entry setup",
            entry.entry_id,
        )
        return
    interval = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES))
    track_location = bool(entry.options.get("track_location", True))
    coordinate_system = entry.options.get(
        CONF_COORDINATE_SYSTEM, DEFAULT_COORDINATE_SYSTEM
    )
    for coordinator in coordinators:
        coordinator.update_interval = timedelta(minutes=interval)
        coordinator.track_location = bool(entry.options.get("track_location", True))
        coordinator.coordinate_system = entry.options.get(
            CONF_COORDINATE_SYSTEM, DEFAULT_COORDINATE_SYSTEM
        )
    _LOGGER.info(
        "fordpass_cn options updated: scan_interval=%smin track_location=%s coordinate_system=%s (vehicles=%d)",
        interval,
        track_location,
        coordinate_system,
        len(coordinators),
    )
    for coordinator in coordinators:
        await coordinator.async_request_refresh()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
