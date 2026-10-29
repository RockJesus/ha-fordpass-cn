"""The FordPass China integration."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from typing import Any

import voluptuous as vol
from homeassistant.components import persistent_notification
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
    ConfigEntryState,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import capability
from .api import FordPassApi, FordPassApiError
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_BRAND,
    CONF_CHIRP_TYPE,
    CONF_COORDINATE_SYSTEM,
    CONF_HONK_DURATION,
    CONF_REFRESH_TOKEN,
    DEFAULT_BRAND,
    DEFAULT_CHIRP_TYPE,
    DEFAULT_COORDINATE_SYSTEM,
    DEFAULT_HONK_DURATION,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
)
from .coordinator import FordPassCoordinator
from .crypto_data import async_ensure_crypto_data, missing_help
from .wbsk import FordPassCrypto

_LOGGER = logging.getLogger(__name__)

# v5.3.8: 车辆对象日志安全转储——车辆 dict 含手机号/用户 ID 等个人标识
# 字段（sensor.py 的 _PHONE_KEYS/_IDENTITY_KEYS 掩码逻辑证明），直接
# dump 进日志（尤其 warning 级）会泄露。过滤敏感键后再截断。
_PHONE_KEYS = ("phone", "phonenumber", "mobile", "mobileno", "mobilephone")
_IDENTITY_KEYS = ("userid", "user_id", "uid", "account", "accountid",
                  "loginname", "username", "idcard", "idno", "passport",
                  "openid", "unionid", "email", "identityno")


def _safe_vehicle_dump(vehicle: dict, limit: int = 500) -> str:
    """车辆对象日志安全转储：剔除/掩码手机号与身份标识键后截断。

    v5.5.2: 解密后字段仍沿用加密键名（encryptedVin/encryptedLicenseplate），
    不会被 _PHONE_KEYS/_IDENTITY_KEYS 命中——车牌直接剔除（本地诊断用不到）、
    VIN 仅保留后 6 位（车架号全文不必入日志）。
    """
    out: dict[str, object] = {}
    for k, v in vehicle.items():
        kl = str(k).lower()
        if kl in ("encryptedlicenseplate", "licenseplate", "plate"):
            continue  # 车牌本地诊断不需要
        if kl == "encryptedvin" or kl == "vin":
            s = str(v)
            out[str(k)] = f"****{s[-6:]}" if len(s) > 6 else "****"
            continue
        if kl in _PHONE_KEYS or kl in _IDENTITY_KEYS:
            continue
        if any(ident in kl for ident in ("userid", "user_id", "phonenumber",
                                         "mobile", "idcard", "loginname", "accountid")):
            continue
        if kl.endswith(("phone", "mobile", "phonenumber")):
            s = str(v)
            digits = "".join(ch for ch in s if ch.isdigit())
            out[str(k)] = f"****{digits[-4:]}" if len(digits) >= 4 else "****"
            continue
        out[str(k)] = v
    return json.dumps(out, ensure_ascii=False, default=str)[:limit]


async def _cleanup_legacy_vin(hass: HomeAssistant, entry: ConfigEntry, coord) -> None:
    """v5.7.1: 清理旧格式（真实 VIN 前缀）实体与设备条目。

    unique_id 与设备 identifiers 自 v5.7.1 起改为 sha256(vin) 脱敏前缀
    （coordinator.uid_key）。升级时旧条目（unique_id 以真实 VIN + "-" 开头、
    设备 identifiers=(DOMAIN, 真实 VIN)）仍在 entity/device registry 中——
    必须显式移除，否则 registry 持久化文件残留真实 VIN 且旧实体永远
    unavailable。各平台"unique_id 不在新集合即移除"只覆盖有清理段的
    平台（sensor/button），本函数对全平台兜底。
    """
    try:
        from homeassistant.helpers import entity_registry as er
        reg = er.async_get(hass)
        for entry_ in er.async_entries_for_config_entry(reg, entry.entry_id):
            if entry_.unique_id and entry_.unique_id.startswith(coord.vin + "-"):
                _LOGGER.debug("fordpass_cn cleanup legacy vin entity: %s", entry_.entity_id)
                reg.async_remove(entry_.entity_id)  # v3.7.15: 同步返回 None
    except Exception:  # noqa: BLE001 - 清理失败不阻断 setup
        _LOGGER.debug("fordpass_cn legacy vin entity cleanup failed", exc_info=True)
    try:
        from homeassistant.helpers import device_registry as dr
        dreg = dr.async_get(hass)
        # v5.7.2: async_get_device 已废弃（2027.8 移除）——改用按 identifier 查询
        # v5.8.1: HA 2026.10 async_get_device_by_identifier 增加必填 config_entry_id
        #         参数（缺失导致 TypeError 清理失败，日志 legacy vin device cleanup
        #         failed）——补传 entry.entry_id
        # v6.0.3: HA 2026.10 实际签名为 (identifier: tuple, config_entry_id)——
        #         接收**单个 tuple** 而非 set。此前传 {(DOMAIN, vin)} 导致 HA 内部
        #         identifiers={{...}} 抛 "cannot use 'set' as a set element"，
        #         本清理自 v5.8.1 起每次启动都静默失败（日志可见）。
        dev = dreg.async_get_device_by_identifier((DOMAIN, coord.vin), entry.entry_id)
        if dev is not None:
            _LOGGER.debug("fordpass_cn cleanup legacy vin device: %s", dev.id)
            dreg.async_remove_device(dev.id)
    except Exception:  # noqa: BLE001
        _LOGGER.debug("fordpass_cn legacy vin device cleanup failed", exc_info=True)


PLATFORMS = [
    Platform.LOCK,
    Platform.SWITCH,
    Platform.BUTTON,
    Platform.SENSOR,
    Platform.DEVICE_TRACKER,
    Platform.IMAGE,
    Platform.SELECT,
    Platform.EVENT,  # v5.9.5: 告警 / 车门开合事件
    Platform.UPDATE,  # v5.9.5: 车机固件版本
]

# v5.1.7: 服务层（对齐 HA 官方 services.yaml 规范，自动化可直接调用）
SERVICE_REFRESH_STATUS = "refresh_status"
SERVICE_CLEAR_TOKENS = "clear_tokens"
# v5.8.2 服务层扩展（未接入端点补全的带参入口；按钮为无参快捷方式）：
SERVICE_SHARE_WALLBOX = "share_wallbox"
SERVICE_CREATE_DEPARTURE = "create_departure"
SERVICE_UPDATE_SRS = "update_srs"
SERVICE_APPROVE_AUTH = "approve_auth"


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """迁移旧版 config entry 到当前 schema（v4.9.5 补官方规范钩子）。

    历史 entry 均为 VERSION=1 且无结构变更（唯一字段变更由
    async_update_entry 渐进写入，不涉及 minor 迁移），此处返回 True
    即视为迁移完成，并为未来 schema 变更预留分段迁移入口。
    """
    # P2-11（v5.6.0）：version=0 的历史 entry 迁移完成后回写 version=1——
    # 此前只返回 True 不回写，每次加载都重跑迁移。
    if config_entry.version == 0:
        hass.config_entries.async_update_entry(config_entry, version=1)
    # 未来版本降级到此版时的保护（正常不应发生）
    return config_entry.version <= 1


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """注册集成级服务（v5.6.0，P1-6）。

    此前服务在 async_setup_entry 内按 entry 注册——第二个 entry 会覆盖
    第一个的 handler（refresh_status 只刷新最后注册的 entry），卸载任一
    entry 都会 async_remove 掉全局服务。改为 async_setup 注册一次、遍历
    全部已加载 entry 的 runtime_data；最后一个 entry 卸载时再移除服务
    （见 async_unload_entry）。
    """

    def _all_coordinators() -> list[FordPassCoordinator]:
        out: list[FordPassCoordinator] = []
        for e in hass.config_entries.async_entries(DOMAIN):
            if e.state != ConfigEntryState.LOADED:
                continue
            payload = getattr(e, "runtime_data", None)
            if not isinstance(payload, dict):
                continue
            coords = payload.get("coordinators") or (
                [payload["coordinator"]] if payload.get("coordinator") else []
            )
            out.extend(coords)
        return out

    async def _service_refresh_status(call) -> None:
        vin = call.data.get("vin")
        targets = [c for c in _all_coordinators() if not vin or c.vin == vin]
        if not targets:
            # v5.3.0: action-exceptions（quality_scale Silver）——服务失败抛异常，
            # 自动化/脚本可感知（HomeAssistantError 在服务调用处显示失败原因）。
            # v5.7.9: 异常消息掩码 VIN 尾段（完整 VIN 曾进错误消息/日志）
            raise HomeAssistantError(
                f"fordpass_cn refresh_status: VIN …{vin[-6:]} 不在当前账号车辆中"
            )
        for coord in targets:
            # v5.7.9: 服务日志掩码 VIN 尾段（info 级曾打印完整 VIN）
            _LOGGER.info(
                "fordpass_cn service refresh_status vin=…%s", coord.vin[-6:]
            )
            await coord.async_request_refresh()

    async def _service_clear_tokens(call) -> None:
        # P1-6（v5.6.0）：多 entry 时逐一清除各账号令牌（内存 + entry.data
        # 落盘），最后一个 entry 卸载后服务随 async_setup 卸载流程移除。
        cleared = 0
        for e in hass.config_entries.async_entries(DOMAIN):
            if e.state != ConfigEntryState.LOADED:
                continue
            payload = getattr(e, "runtime_data", None)
            if not isinstance(payload, dict) or "api" not in payload:
                continue
            payload["api"].set_token("", "")
            try:
                hass.config_entries.async_update_entry(
                    e,
                    data={**e.data, CONF_ACCESS_TOKEN: "", CONF_REFRESH_TOKEN: ""},
                )
            except Exception as exc:  # noqa: BLE001 - 落盘失败不阻断清令牌
                _LOGGER.warning("fordpass_cn clear_tokens persist failed: %s", exc)
            cleared += 1
        _LOGGER.warning(
            "fordpass_cn clear_tokens: 已清除 %d 个账号访问令牌，重载集成后需重新认证",
            cleared,
        )

    # ---- v5.8.2 未接入端点补全服务（share_wallbox / create_departure /
    # update_srs / approve_auth）——按钮为无参快捷方式，服务为自动化带参
    # 入口；日志与错误消息一律掩码 VIN 尾段（v5.7.9 纪律）。
    def _pick_id(data: Any, keys: tuple[str, ...]) -> str | None:
        """从探测返回结构递归提取第一个命中的 id 字段（与 button.py 同款）。"""
        if isinstance(data, dict):
            for k in keys:
                v = data.get(k)
                if v not in (None, "", 0, False):
                    return str(v)
            for v in data.values():
                r = _pick_id(v, keys)
                if r:
                    return r
        elif isinstance(data, list):
            for it in data:
                r = _pick_id(it, keys)
                if r:
                    return r
        return None

    async def _service_share_wallbox(call) -> None:
        vin = call.data.get("vin") or ""
        phone = call.data.get("phone") or ""
        wid = call.data.get("wallbox_id")
        targets = [c for c in _all_coordinators() if not vin or c.vin == vin]
        if not targets:
            raise HomeAssistantError(
                f"fordpass_cn share_wallbox: VIN …{vin[-6:] if vin else ''} 不在当前账号车辆中"
            )
        coord = targets[0]
        if not wid:
            wb = coord.smartwallbox or {}
            sq = wb.get("wallbox_sharing_query")
            data = sq.get("data") if isinstance(sq, dict) else None
            if isinstance(data, dict):
                data = data.get("data", data)
            wid = _pick_id(data, ("wallboxId", "defaultWallboxId", "id", "equipmentId"))
        if not wid:
            raise HomeAssistantError("未找到已绑定的家充桩（wallboxId）")
        if not phone:
            raise HomeAssistantError("share_wallbox 需要 phone（目标手机号）参数")
        _LOGGER.info("fordpass_cn service share_wallbox vin=…%s", coord.vin[-6:])
        await coord.api.wallbox_share(coord.vin, wid, phone)

    async def _service_create_departure(call) -> None:
        vin = call.data.get("vin") or ""
        structure = call.data.get("structure")
        targets = [c for c in _all_coordinators() if not vin or c.vin == vin]
        if not targets:
            raise HomeAssistantError(
                f"fordpass_cn create_departure: VIN …{vin[-6:] if vin else ''} 不在当前账号车辆中"
            )
        coord = targets[0]
        _LOGGER.info("fordpass_cn service create_departure vin=…%s", coord.vin[-6:])
        await coord.api.cevs_save_departure(coord.vin, structure)

    async def _service_update_srs(call) -> None:
        vin = call.data.get("vin") or ""
        targets = [c for c in _all_coordinators() if not vin or c.vin == vin]
        if not targets:
            raise HomeAssistantError(
                f"fordpass_cn update_srs: VIN …{vin[-6:] if vin else ''} 不在当前账号车辆中"
            )
        coord = targets[0]
        _LOGGER.info("fordpass_cn service update_srs vin=…%s", coord.vin[-6:])
        ep = coord.extra_probes or {}
        sp = ep.get("srs_profile")
        data = sp.get("data") if isinstance(sp, dict) else None
        if isinstance(data, dict):
            data = data.get("data", data)
        profile = data if isinstance(data, dict) else None
        await coord.api.srs_save_profile(coord.vin, profile, update=True)

    async def _service_approve_auth(call) -> None:
        vin = call.data.get("vin") or ""
        approve = bool(call.data.get("approve", True))
        targets = [c for c in _all_coordinators() if not vin or c.vin == vin]
        if not targets:
            raise HomeAssistantError(
                f"fordpass_cn approve_auth: VIN …{vin[-6:] if vin else ''} 不在当前账号车辆中"
            )
        coord = targets[0]
        ep = coord.extra_probes or {}
        lst = ep.get("user_auth_list")
        data = lst.get("data") if isinstance(lst, dict) else None
        if isinstance(data, dict):
            data = data.get("data", data)
        auth_id = _pick_id(data, ("authId", "approvalId", "applyId", "id"))
        if not auth_id:
            raise HomeAssistantError("未找到待审批的授权申请")
        _LOGGER.info("fordpass_cn service approve_auth vin=…%s", coord.vin[-6:])
        await coord.api.vehicle_auth_approval(coord.vin, auth_id, approve)

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
    hass.services.async_register(
        DOMAIN,
        SERVICE_SHARE_WALLBOX,
        _service_share_wallbox,
        schema=vol.Schema(
            {
                vol.Optional("vin"): cv.string,
                vol.Required("phone"): cv.string,
                vol.Optional("wallbox_id"): cv.string,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CREATE_DEPARTURE,
        _service_create_departure,
        schema=vol.Schema(
            {
                vol.Optional("vin"): cv.string,
                vol.Optional("structure"): dict,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_SRS,
        _service_update_srs,
        schema=vol.Schema({vol.Optional("vin"): cv.string}),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_APPROVE_AUTH,
        _service_approve_auth,
        schema=vol.Schema(
            {vol.Optional("vin"): cv.string, vol.Optional("approve"): cv.boolean}
        ),
    )
    return True


_NOTIFICATION_CRYPTO_MISSING = "fordpass_cn_crypto_data_missing"


def _async_notify_missing_crypto(hass: HomeAssistant) -> None:
    """运行时数据包缺失时发持久通知（固定 id，多车/多次重试只更新不刷屏）。"""
    try:
        persistent_notification.async_create(
            hass,
            missing_help(hass),
            title="福特派互联：缺少运行时数据包",
            notification_id=_NOTIFICATION_CRYPTO_MISSING,
        )
    except Exception as err:  # noqa: BLE001 - 通知失败不应影响 setup 结果
        _LOGGER.warning("fordpass_cn 数据包缺失通知发送失败：%s", err)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # v6.1.0: 运行时数据包不再随代码仓库分发，setup 时先定位/按需获取。
    # 缺失时用 ConfigEntryNotReady 而不是普通异常：HA 会按退避自动重试，
    # 用户把数据补齐后无需手动重载即可恢复；同时发持久通知给出补齐方式，
    # 避免只留下一句没有信息量的"集成加载失败"。
    crypto_ok, crypto_note = await async_ensure_crypto_data(hass)
    if not crypto_ok:
        _async_notify_missing_crypto(hass)
        raise ConfigEntryNotReady(f"福特派互联缺少运行时数据包：{crypto_note}")
    # v6.1.2: 数据已齐（含自动重试成功的情况），清掉可能挂着的缺失通知。
    # dismiss 不存在的通知是幂等的，无需先查询。
    persistent_notification.async_dismiss(hass, _NOTIFICATION_CRYPTO_MISSING)

    session = async_get_clientsession(hass)
    api = FordPassApi(session, _LOGGER, brand=entry.data.get(CONF_BRAND, DEFAULT_BRAND))
    api.set_token(
        entry.data.get(CONF_ACCESS_TOKEN),
        entry.data.get(CONF_REFRESH_TOKEN),
    )

    # v5.3.1: _persist_token 扩展为 (access, refresh=None) 双参数——api 侧
    # 常规续期只传 access_token；refresh token 轮换时传双参数，避免新
    # refresh_token 被误写进 CONF_ACCESS_TOKEN 字段。
    def _persist_token(tok: str, refresh: str | None = None) -> None:
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_ACCESS_TOKEN: tok,
                **({CONF_REFRESH_TOKEN: refresh} if refresh is not None else {}),
            },
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
            # v5.3.1: 修复日志参数错位——旧文案两个 %s 传的是
            # (DEFAULT_SCAN_INTERVAL_MINUTES, DEFAULT_COORDINATE_SYSTEM)，
            # 把刷新间隔填进了 track_location 的位置且 track_location=True
            # 硬编码重复。
            _LOGGER.info(
                "fordpass_cn options defaults persisted: track_location=%s "
                "coordinate_system=%s",
                True,
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
        # v5.3.1: 空账号不再抛 RuntimeError（HA 不自动重试、entry 永久失败
        # 态）——转 ConfigEntryError：显示配置错误、不循环重试、用户在
        # 配置中修改（如换绑了车辆再重新配置）。
        raise ConfigEntryError(
            "账号下未发现车辆——请先在福特派/林肯之道 App 中确认已绑定车辆"
        )

    # v4.2.7: 预热签名模块单例（x_api/cevs/lbs_p2c 三个场景）——纯 Python
    # 查表实现（v4.2.7 起不再依赖原生库）。表加载与 key 文件读取
    # 仍统一放在线程中完成，双保险防止未来新增未包裹 to_thread 的调用路径
    # 造成 blocking call 回归。
    for scene in ("x_api", "cevs", "lbs_p2c"):
        try:
            await asyncio.to_thread(FordPassCrypto.get, None, scene)
        except Exception as exc:  # noqa: BLE001 - 预热失败不阻断 setup
            _LOGGER.warning("FordPass crypto warmup %s failed: %s", scene, exc)

    # v5.2.7: 清理旧版（<v5.2.4）写入 /config/www/fordpass_cn 的遗留车辆
    # 图片/泊车/行驶监控帧——/www 下内容无需认证即可经 /local/ 下载
    # （审核二轮指出）。v5.2.4 起缓存已迁至 .storage/fordpass_cn
    # 私有目录；此处 setup 时在 executor 中删除一次遗留目录，目录删掉后
    # 不存在，天然只执行一次，不影响新版本私有缓存。
    legacy_www_dir = hass.config.path("www", "fordpass_cn")
    if await asyncio.to_thread(os.path.isdir, legacy_www_dir):
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
    _LOGGER.debug(
        "fordpass_cn vehicles: %d 辆, fields[0]=%s",
        len(vehicles),
        _safe_vehicle_dump(vehicles[0], 2500),
    )

    # v4.9.6: 轮询间隔不再用户可配置（HA 官方规则），固定默认 30 分钟。
    interval = DEFAULT_SCAN_INTERVAL_MINUTES
    track_location = bool(entry.options.get("track_location", True))
    coordinate_system = entry.options.get(
        CONF_COORDINATE_SYSTEM, DEFAULT_COORDINATE_SYSTEM
    )

    def _vehicle_label(vehicle: dict) -> str | None:
        # P2-9（v5.6.0）：nickName 两键已含在首段 key 列表，删除重复循环。
        for key in ("localMarketValue", "displayModelName", "modelName",
                    "vehicleModel", "model", "carModel", "vehicleType",
                    "encryptedNickName", "nickName"):
            val = vehicle.get(key)
            if val and not str(val).strip().startswith("SYNC"):
                return str(val)
        return None

    coordinators = []
    # v5.3.1: 账号级共享缓存提前创建——此前在全部 first_refresh 之后才
    # 回填 account_cache，setup 首轮（load/probe/first_refresh）期间
    # coordinator.account_cache 为 None，account=True 端点（chirp_cloud/
    # messages）不缓存直接真拉；现建 coordinator 时立即回填，首轮即生效。
    account_cache: dict[str, tuple[float, object]] = {}
    for vehicle in vehicles:
        vin = vehicle.get("encryptedVin") or vehicle.get("vin")
        if not vin:
            _LOGGER.warning("fordpass_cn skip vehicle without VIN: %s",
                            _safe_vehicle_dump(vehicle))
            continue
        license_plate = (
            vehicle.get("encryptedLicenseplate") or vehicle.get("licenseplate") or None
        )
        nickname = vehicle.get("encryptedNickName") or vehicle.get("nickName") or None
        # v3.3.9: 拆开两张车型图——vehicleImageUrl=侧视（45view）、imageUrl=俯视
        # （birdview，实机样本2026-10-03 实测字段；两字段各自独立创建图片实体）
        vehicle_image_url = vehicle.get("vehicleImageUrl") or None
        vehicle_overlook_url = vehicle.get("imageUrl") or None
        # v3.3.9: 车辆信息（jointVenture/localMarketValue/modelYear/vehicleType/
        # fuelType——vehicles/list 明文字段，实机样本实测；「车辆信息」传感器读取）
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
        # v5.3.1: 建立即回填账号级共享缓存（首轮刷新即可命中复用）
        coordinator.account_cache = account_cache
        coordinators.append(coordinator)

    if not coordinators:
        raise ConfigEntryError(
            "账号下未发现可用车辆（无有效 VIN）——请检查账号内车辆信息"
        )

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
            _vs = (coordinator.data or {}).get("vehiclestatus", {}) or {}
            _caps = {
                "vin": coordinator.uid_key,
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
            _LOGGER.debug(
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
    # v5.3.0: 迁移到 ConfigEntry.runtime_data（quality_scale Bronze runtime-data
    # 规则）——coordinator/API/缓存统一挂 entry.runtime_data，替代 hass.data 手
    # 工管理，卸载/多 entry 生命周期由 HA 接管。
    entry.runtime_data = {
        "api": api,
        "coordinator": coordinators[0],
        "coordinators": coordinators,
        "vehicles": vehicles,
        "vin": coordinators[0].vin,
        "honk_settings": honk_settings,
        # v5.1.8: 账号级共享缓存（问题 7 P2 修复）——账户级端点
        # （chirp_cloud/messages 等）多车共享同一份缓存，消除多车每轮
        # 重复请求同一账户数据的浪费；v5.3.1 起该 dict 在 coordinator
        # 建立时即回填（见上方），此处直接挂 runtime_data 供诊断等读取。
        "account_cache": account_cache,
    }

    # v5.6.0（P1-6）：服务注册已迁移到 async_setup（集成级，不随 entry
    # 生命周期），此处不再注册/注销。

    # v4.9.6: 移除 cevs 变体手动探测调试服务（probe_cevs_variant）——
    # 合规审查项：调试接口残留，自动化探测（probe_cevs_auto）已覆盖
    # 所需变体，无需人工触发。对应 services.yaml 一并删除。
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # v5.7.1: unique_id/identifiers 脱敏迁移——清理旧 VIN 前缀实体与设备
    # （registry 持久化不得再留真实 VIN）。在平台注册新实体后执行，
    # 新实体（uid_key 前缀）不受影响。
    for _coord in coordinators:
        await _cleanup_legacy_vin(hass, entry, _coord)
    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply option changes immediately without restarting HA.

    Called by Home Assistant whenever the entry options change (e.g. the
    user edits the scan interval or toggles location tracking in the
    integration options flow). We update the live coordinator instead of
    reloading the whole entry, so no entities flicker.

    v3.1.1: 选项可能在集成尚未加载完成时被保存（重启中 / 加载失败 /
    已卸载后再次打开选项页）——此时 runtime_data 为空，直接
    索引会抛 KeyError。防御：未加载时安全跳过，新选项会由下次
    async_setup_entry 从 entry.options 读取生效（无状态丢失）。
    """
    # v5.3.0: runtime_data 替代 hass.data——entry 卸载后 runtime_data 值仍保留
    # （HA 管理），getattr 兜底兼容旧版 HA（无 runtime_data 属性时 None）。
    payload = getattr(entry, "runtime_data", None)
    if not payload:
        return
    coordinators = (
        payload.get("coordinators")
        or [payload["coordinator"]]
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
    store = entry.runtime_data  # v5.3.0: runtime_data 替代 hass.data
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
    # v5.3.5: 显式关闭 coordinator 定时刷新——DataUpdateCoordinator 的
    # 30 分钟轮询循环不随 unload_platforms 自动停止，且 entry.runtime_data
    # 卸载后仍保留引用（coordinator 不释放）；不 shutdown 会残留轮询任务：
    # 每次重载积累多份并发云请求（多车多倍请求量、风控风险）+ 继续写
    # logbook 活动；用户卸载集成后仍持续访问福特云。async_shutdown 自
    # HA 2022.10 提供，2026.10 确认可用。
    payload = getattr(entry, "runtime_data", None)
    if isinstance(payload, dict):
        coords = payload.get("coordinators") or (
            [payload["coordinator"]] if payload.get("coordinator") else []
        )
        for coord in coords:
            try:
                # v5.3.6: async_shutdown 只停定时调度，不会取消 _persist_tasks
                # 里正在运行的后台任务（命令状态轮询 ≤60s+ / 45s 延迟补写 /
                # 自动复位 / 数据落盘）——不取消则卸载后仍短暂访问福特云、
                # 写无意义 logbook 活动。先逐任务 cancel（done_callback 自动
                # discard 清理集合），再 shutdown。落盘任务被取消丢一次
                # 落盘，卸载场景可接受。
                for t in list(coord._persist_tasks):
                    t.cancel()
                await coord.async_shutdown()
            except Exception as exc:  # noqa: BLE001 - 关闭失败不阻断卸载
                _LOGGER.warning("fordpass_cn coordinator shutdown failed: %s", exc)
    # v5.3.0: runtime_data 由 HA 生命周期管理，无需手动 pop hass.data
    # v5.6.0（P1-6）：服务注册在 async_setup（集成级）；最后一个 entry
    # 卸载后移除服务，避免残留服务调用时遍历不到任何 coordinator。
    if not any(
        e.state == ConfigEntryState.LOADED
        for e in hass.config_entries.async_entries(DOMAIN)
    ):
        # v5.9.6: 六个服务全部移除。此前只移除前两个，卸载集成后
        # share_wallbox / create_departure / update_srs / approve_auth
        # 仍留在服务目录里，调用时报的是「遍历不到 coordinator」而不是
        # 「服务不存在」，且重新安装前一直占着开发者工具的服务列表。
        for _service in (
            SERVICE_REFRESH_STATUS,
            SERVICE_CLEAR_TOKENS,
            SERVICE_SHARE_WALLBOX,
            SERVICE_CREATE_DEPARTURE,
            SERVICE_UPDATE_SRS,
            SERVICE_APPROVE_AUTH,
        ):
            if hass.services.has_service(DOMAIN, _service):
                hass.services.async_remove(DOMAIN, _service)
    return unload_ok
