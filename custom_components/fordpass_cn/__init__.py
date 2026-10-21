"""The FordPass China integration."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import capability
from .api import FordPassApi, FordPassApiError
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_CHIRP_TYPE,
    CONF_COORDINATE_SYSTEM,
    CONF_HONK_DURATION,
    CONF_REFRESH_TOKEN,
    DEFAULT_CHIRP_TYPE,
    DEFAULT_COORDINATE_SYSTEM,
    DEFAULT_HONK_DURATION,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
)
from .coordinator import FordPassCoordinator
from .wbsk import FordPassCrypto

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

# v5.1.7: 服务层（对齐 HA 官方 services.yaml 规范，自动化可直接调用）
SERVICE_REFRESH_STATUS = "refresh_status"
SERVICE_CLEAR_TOKENS = "clear_tokens"


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """迁移旧版 config entry 到当前 schema（v4.9.5 补官方规范钩子）。

    历史 entry 均为 VERSION=1 且无结构变更（唯一字段变更由
    async_update_entry 渐进写入，不涉及 minor 迁移），此处返回 True
    即视为迁移完成，并为未来 schema 变更预留分段迁移入口。
    """
    # 未来版本降级到此版时的保护（正常不应发生）
    return config_entry.version <= 1


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

    # v4.2.9: options 为空（旧版本前端保存失败/从未保存过，实测 entry
    # options 恒为 {}）时，落盘默认选项——让"选项/重新配置"表单有真实
    # 默认值、快照比较与 update_listener 有据可依，用户后续设置才能
    # 正确读写。仅当 options 完全为空时补默认，用户已保存的值原样保留，
    # 绝不覆盖。async_update_entry 会触发 update_listener，此时
    # coordinator 尚未建立，由 async_update_options 的 guard 兜底
    # （"options saved before entry loaded"，下次 setup 生效）。
    # v4.9.6: 移除用户可配置刷新间隔（HA 官方规则禁止），调度间隔固定
    # 默认值；此处只补位置跟踪/坐标系两项默认，保留已存在字段绝不覆盖。
    if not entry.options.get("track_location") or not entry.options.get(
        CONF_COORDINATE_SYSTEM
    ):
        try:
            hass.config_entries.async_update_entry(
                entry,
                options={
                    **entry.options,  # 保留已存在字段（历史业务残留等）
                    "track_location": True,
                    CONF_COORDINATE_SYSTEM: DEFAULT_COORDINATE_SYSTEM,
                },
            )
        except Exception as exc:  # noqa: BLE001 - 落盘失败不阻断 setup
            _LOGGER.warning("fordpass_cn default options persist failed: %s", exc)
        else:
            # v4.5.0: 兜底落盘成功打一条 INFO（setup 一次，非轮询噪音），
            # 便于确认 options 已写入（此前 options 显示为空无法区分
            # "API 脱敏"与"真未写入"）。
            _LOGGER.info(
                "fordpass_cn options defaults persisted: track_location=%s "
                "track_location=True coordinate_system=%s",
                DEFAULT_SCAN_INTERVAL_MINUTES,
                DEFAULT_COORDINATE_SYSTEM,
            )

    try:
        # v4.9.8: setup 首次刷新加 20s 短超时——福特云慢响应（实测挂起
        # 32s+，_request 通用超时 60s）会让 setup 长期卡在 SETUP_IN_PROGRESS，
        # 用户此时点"重新加载"会取消挂起的 setup 协程 → 前端弹错
        # "Setup ... cancelled"。20s 足够正常响应（实测 <5s），超时立即转
        # ConfigEntryNotReady 交给 HA 自动重试，用户无感知。
        async with asyncio.timeout(20):
            vehicles = await api.get_vehicles()
    except TimeoutError as err:
        raise ConfigEntryNotReady(f"FordPass timeout: {err}") from err
    except FordPassApiError as err:
        if err.code in (401, 403):
            # v4.4.9: token 失效（api 内部已自动 refresh 一次仍失败）→ 抛
            # ConfigEntryAuthFailed：HA 停止 setup 重试（不再每轮打云请求
            # 制造噪音）并自动启动 reauth flow（前端提示"需要重新认证"，
            # 不卸载集成、不删实体）；用户重新认证完成后 entry 更新，
            # HA 自动重新 setup（官方 reauth 指南路径）。
            raise ConfigEntryAuthFailed(f"FordPass login expired: {err}") from err
        # 其它 4xx/5xx：多数为临时网络/网关故障，交给 HA 自动重试
        raise ConfigEntryNotReady(f"FordPass login failed: {err}") from err
    except Exception as err:
        raise ConfigEntryNotReady(f"FordPass setup error: {err}") from err

    if not vehicles:
        raise RuntimeError("No vehicles found on this FordPass account")

    # v4.2.7: 预热白盒密码学单例（x_api/cevs/lbs_p2c 三个场景）——纯 Python
    # 查表实现（v4.2.7 起不再加载 ELF/.so/Unicorn）。表加载与 key 文件读取
    # 仍统一放在线程中完成，双保险防止未来新增未包裹 to_thread 的调用路径
    # 造成 blocking call 回归。
    for scene in ("x_api", "cevs", "lbs_p2c"):
        try:
            await asyncio.to_thread(FordPassCrypto.get, None, scene)
        except Exception as exc:  # noqa: BLE001 - 预热失败不阻断 setup
            _LOGGER.warning("FordPass crypto warmup %s failed: %s", scene, exc)

    # v5.2.7: 清理旧版（<v5.2.4）写入 /config/www/fordpass_cn 的遗留车辆
    # 图片/泊车/行驶监控帧——/www 下内容无需认证即可经 /local/ 下载
    # （frenck 审核 #11629 二轮）。v5.2.4 起缓存已迁至 .storage/fordpass_cn
    # 私有目录；此处 setup 时在 executor 中删除一次遗留目录，目录删掉后
    # 不存在，天然只执行一次，不影响新版本私有缓存。
    legacy_www_dir = hass.config.path("www", "fordpass_cn")
    if os.path.isdir(legacy_www_dir):
        try:
            await asyncio.to_thread(shutil.rmtree, legacy_www_dir, ignore_errors=False)
            _LOGGER.info(
                "fordpass_cn removed legacy /config/www/fordpass_cn: %s",
                legacy_www_dir,
            )
        except Exception as exc:  # noqa: BLE001 - 清理失败不阻断 setup
            _LOGGER.warning("fordpass_cn legacy www cleanup failed: %s", exc)

    # v3.1.6: 多 VIN 支持——账号下每一辆车建立独立的 coordinator / 设备 /
    # 实体组。各平台按各自车型的 vehicle-status 数据创建实体（无效字段不建），
    # 因此其他用户用各自福特派账号登录时，只加载自己车型真正支持的设备与实体。
    _LOGGER.info(
        "fordpass_cn vehicles: %d 辆, fields[0]=%s",
        len(vehicles),
        json.dumps(vehicles[0], ensure_ascii=False, default=str)[:2500],
    )

    # v4.9.6: 轮询间隔不再用户可配置（HA 官方规则），固定默认 30 分钟。
    interval = DEFAULT_SCAN_INTERVAL_MINUTES
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
                # v4.6.5: 车型主数据中文化需要补全字段（vehicles/list 明文）
                "brandCode", "vehicleLineFeatureCode", "version",
                "vehicleCloud", "plFlag",
            )
        }
        coordinator = FordPassCoordinator(
            hass, api, vin, interval, _vehicle_label(vehicle), license_plate,
            track_location, nickname, vehicle_image_url, vehicle_overlook_url,
            vehicle_info, coordinate_system,
            entry_id=entry.entry_id,
            car_id=vehicle.get("encryptedCarId") or None,
            config_entry=entry,
        )
        coordinators.append(coordinator)

    if not coordinators:
        raise RuntimeError("No usable vehicles found on this FordPass account")

    for coordinator in coordinators:
        # v3.6.2: 全量车辆清单（v5/vehicles/list 已验证 live）共享给每车，
        # 供「车辆清单」传感器显示账号下全部车辆。
        coordinator.vehicles_list = vehicles
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
        except Exception:
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
        # v5.1.8: 账号级共享缓存（问题 7 P2 修复）——账户级端点
        # （chirp_cloud/messages 等）多车共享同一份缓存，消除多车每轮
        # 重复请求同一账户数据的浪费。创建后回填给所有 coordinator。
        "account_cache": {},
    }
    for coordinator in coordinators:
        coordinator.account_cache = hass.data[DOMAIN][entry.entry_id]["account_cache"]

    # v5.1.7: 注册服务（services.yaml）——refresh_status（可选 VIN 全车/单车
    # 刷新）+ clear_tokens（清令牌重置会话）。闭包持有 coordinators/api，
    # 多 entry 同域注册同名服务由 HA 覆盖，无副作用。
    async def _service_refresh_status(call) -> None:
        vin = call.data.get("vin")
        targets = [c for c in coordinators if not vin or c.vin == vin]
        if not targets:
            _LOGGER.warning(
                "fordpass_cn refresh_status: VIN %s 不在当前账号车辆中", vin
            )
            return
        for coord in targets:
            _LOGGER.info(
                "fordpass_cn service refresh_status vin=%s", coord.vin
            )
            await coord.async_request_refresh()

    async def _service_clear_tokens(call) -> None:
        _LOGGER.warning(
            "fordpass_cn clear_tokens: 已清除访问令牌，重载集成后需重新认证"
        )
        api.set_token("", "")

    hass.services.async_register(
        DOMAIN,
        SERVICE_REFRESH_STATUS,
        _service_refresh_status,
        schema=vol.Schema({vol.Optional("vin"): cv.string}),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEAR_TOKENS,
        _service_clear_tokens,
        schema=vol.Schema({}),
    )

    # v4.9.6: 移除 cevs 变体手动探测调试服务（probe_cevs_variant）——
    # 合规审查项：逆向调试服务残留，自动化探测（probe_cevs_auto）已覆盖
    # 所需变体，无需人工触发。对应 services.yaml 一并删除。
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
    track_location = bool(entry.options.get("track_location", True))
    coordinate_system = entry.options.get(
        CONF_COORDINATE_SYSTEM, DEFAULT_COORDINATE_SYSTEM
    )
    # v4.0.0: 仅"调度相关选项"（定位/坐标系，v4.9.6 起不含刷新间隔）变化才
    # 更新并立即刷新。鸣笛设置/远程空调温度等业务 select 每次云端同步写入
    # entry.options 也会触发本 listener——旧版无条件 async_request_refresh
    # 导致每轮主轮询后 10 秒（Debouncer cooldown）又打一轮福特云请求，产生
    # 双轮双 logbook，既浪费请求量又制造噪音。快照比较：业务设置变化时两项
    # 不变 → 直接跳过。
    store = hass.data[DOMAIN][entry.entry_id]
    snapshot = (track_location, coordinate_system)
    if store.get("_opt_snapshot") == snapshot:
        return
    store["_opt_snapshot"] = snapshot
    for coordinator in coordinators:
        coordinator.track_location = bool(entry.options.get("track_location", True))
        coordinator.coordinate_system = entry.options.get(
            CONF_COORDINATE_SYSTEM, DEFAULT_COORDINATE_SYSTEM
        )
    _LOGGER.info(
        "fordpass_cn options updated: track_location=%s coordinate_system=%s (vehicles=%d)",
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
