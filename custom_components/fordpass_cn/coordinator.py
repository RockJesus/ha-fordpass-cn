"""DataUpdateCoordinator for the FordPass China integration."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
import re
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import FordPassApi, FordPassApiError, FordPassProbeRejected
from .const import (
    PROBE_FORCE_COOLDOWN,
    COORDINATE_WGS84,
    DOMAIN,
    PROBE_PERSIST_TTL,
    CONF_BRAND,
    DEFAULT_BRAND,
    BRAND_LINCOLN,
)

_LOGGER = logging.getLogger(__name__)


def _status_summary(data: dict[str, Any]) -> str:
    """轮询后关键实体状态摘要（v4.0.1：随活动日志展示每次刷新后的状态）。

    从 vehicle-status 原始字段提取门锁/点火/油量/续航/里程/车窗/车门/出行
    等用户关心的核心状态，拼成一行可读摘要；字段缺失或值无意义时跳过该项。
    """
    vs = (data or {}).get("vehiclestatus")
    if not isinstance(vs, dict):
        return "车辆状态刷新成功"

    def val(node):
        return (node or {}).get("value") if isinstance(node, dict) else node

    def win_state(v):
        if isinstance(v, str):
            s = v.strip().lower().replace("_", " ")
            if s in ("fully closed", "closed", "close"):
                return "已关闭"
            if s in ("fully open", "fully opened", "open"):
                return "未关闭（全开）"
            m = re.search(r"(\d+)\s*percent", s)
            if m:
                return f"未关闭 {int(m.group(1))}%"
            return None
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            pct = int(v)
            return "已关闭" if pct <= 0 else f"未关闭 {pct}%"
        return None

    parts: list[str] = []
    lock = val(vs.get("lockStatus"))
    if lock is not None:
        parts.append("门锁：" + ("已锁定" if lock in ("LOCKED", 1, "1", True) else "已解锁"))
    ign = val(vs.get("ignitionStatus"))
    if ign is not None:
        s = str(ign).lower()
        parts.append("点火：" + ("已启动" if s in ("on", "run", "running", "1") else "已熄火"))
    fuel = vs.get("fuel") or {}
    fl = fuel.get("fuelLevel")
    if fl is not None:
        try:
            parts.append(f"油量：{float(fl):.1f}%")
        except (TypeError, ValueError):
            parts.append(f"油量：{fl}%")
    dte = fuel.get("distanceToEmpty")
    if dte is not None:
        try:
            parts.append(f"续航：{int(float(dte))} km")
        except (TypeError, ValueError):
            parts.append(f"续航：{dte} km")
    odo = val(vs.get("odometer"))
    if odo is not None:
        try:
            parts.append(f"里程：{int(float(odo))} km")
        except (TypeError, ValueError):
            parts.append(f"里程：{odo} km")
    wp = vs.get("windowPosition") or {}
    win_states: list[str] = []
    for _k, node in wp.items():
        if isinstance(node, dict):
            st = win_state(val(node))
            if st and st != "已关闭":
                win_states.append(st)
    if wp:
        parts.append("车窗：" + ("全部关闭" if not win_states else "、".join(dict.fromkeys(win_states))))
    ds = vs.get("doorStatus") or {}
    DOOR_CN = {
        "driverDoor": "主驾门",
        "passengerDoor": "副驾门",
        "leftRearDoor": "左后门",
        "rightRearDoor": "右后门",
        "hoodDoor": "前机舱盖",
        "tailgateDoor": "尾门",
        "innerTailgateDoor": "内尾门",
    }
    open_doors: list[str] = []
    for k, cn in DOOR_CN.items():
        v = val(ds.get(k))
        if v is not None and str(v).lower() not in ("closed", "close", "locked", "0"):
            open_doors.append(cn)
    if ds:
        parts.append("车门：" + ("全部关闭" if not open_doors else "、".join(open_doors)))
    oaa = val(vs.get("outandAbout"))
    if oaa is not None:
        s = str(oaa)
        parts.append(
            "出行：" + ("已泊车" if "NotAvailable" in s else ("外出中" if "Available" in s else s))  # v4.0.17: 删死代码 s=="不可用"
        )
    return "车辆状态刷新成功 | " + " | ".join(parts) if parts else "车辆状态刷新成功"


class FordPassCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch vehicle list + status on a schedule."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: FordPassApi,
        vin: str,
        interval: int,
        vehicle_name: str | None = None,
        license_plate: str | None = None,
        track_location: bool = True,
        nickname: str | None = None,
        vehicle_image_url: str | None = None,
        vehicle_overlook_url: str | None = None,
        vehicle_info: dict[str, Any] | None = None,
        coordinate_system: str = COORDINATE_WGS84,
        entry_id: str | None = None,
        car_id: str | None = None,
        config_entry=None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-{vin[-6:]}",
            update_interval=timedelta(minutes=interval),
            config_entry=config_entry,
        )
        self.api = api
        self.vin = vin
        # v4.8.1: 品牌（福特派/林肯之道）——config flow 第一步选择，
        # 林肯专属端点（俱乐部/取送车）仅在本品牌下探测
        self.brand = (
            (config_entry.data or {}).get(CONF_BRAND, DEFAULT_BRAND)
            if config_entry else DEFAULT_BRAND
        )
        self.license_plate = license_plate
        self.nickname = nickname
        self.track_location = track_location
        self.vehicle_image_url = vehicle_image_url
        self.vehicle_overlook_url = vehicle_overlook_url
        self.vehicle_info = vehicle_info or {}
        self.coordinate_system = coordinate_system
        # 最近一次成功拉取的时间（v2.7.8），暴露为传感器 last_poll 属性
        self.last_poll: datetime | None = None
        # v4.0.3: 活动日志去重状态（同摘要 15s 窗口内不重复写，防并发双条）
        self._last_logbook_at: datetime | None = None
        self._last_probe_force_at: datetime | None = None  # v4.6.2: 探测重探冷却
        # v4.8.2: "暂无数据"活动去重——仅 empty 集合与上次不同才写（稳定
        # 状态不重复刷屏；v4.3.8 起每轮写 1 条，30min 轮询+手动刷新=81 条/24h 噪音）
        self._last_empty_probes: tuple[str, ...] | None = None
        self._last_logbook_msg: str | None = None
        # v5.1.4: 首轮刷新延迟补写逐实体活动标志（重启/重载后 setup 时
        # 实体尚未注册，all_entity_ids() 为空 → 45s 后补写一次）
        self._initial_states_pending = False
        self._v5_refresh_cid: str | None = None  # v4.7.2: v5 主动刷新最近 commandId
        self._vehicle_name = vehicle_name or f"Ford {vin[-6:]}"
        # v3.4.1: active_alerts 频率由 TTL 缓存控制（30 分钟）——取代旧
        # "_alerts_fail/_alerts_hold_until 连续失败 2 次抑制 1h" 逻辑
        # （TTL 命中天然抑制重试，失败保留最后已知状态）。
        # v3.0.10: 保存鸣笛设置按钮经由此定位 hass.data[DOMAIN][entry_id] 的
        # honk_settings（按钮 _settings 依赖 entry_id 取配置实时值）。
        self.entry_id = entry_id
        # v3.1.13: 车辆列表 encryptedCarId（停车影像/行车监控请求参数——
        # 锐际为 null = 车型无远程影像硬件，影像按钮不创建）
        self.car_id = car_id
        # v3.1.13: 最近一次鸣笛命令状态（鸣笛开关触发后轮询
        # announcestatus/{commandId} 写入；「鸣笛命令状态」传感器读取）
        self.announce_status: dict[str, Any] | None = None
        # v3.3.3: 最后已知数据持久化（HA Store）——重启/重载后先恢复
        # 再后台刷新，实体直接显示最后可用状态，不出现 unavailable/
        # unknown 中间态（需求：刷新失败保留最后已知状态）。
        self._last_store: Store | None = None
        if entry_id:
            self._last_store = Store(
                hass, 1, f"{DOMAIN}.{entry_id}.{vin[-6:]}.last_data"
            )
        # v3.8.6: 探测结果持久化（HA Store）——探测类请求（命令白名单/
        # 额外端点/cevs/家充桩）只在登录 setup 时执行，结果落盘；重启或
        # 重新加载后 setup 直接复用（TTL 内不重探），避免每次 reload 白打
        # 一批探测请求制造噪音（用户：每个账号登录时或重新加载时才 setup，
        # 不要制造不必要的噪音）。
        self._probe_store: Store | None = None
        if entry_id:
            self._probe_store = Store(
                hass, 1, f"{DOMAIN}.{entry_id}.{vin[-6:]}.probe_cache"
            )
        self._probe_cache: dict[str, dict[str, Any]] = {}
        # v3.3.7: 云端 send-command 白名单（探测结果）——平台实体按此动态创建
        self.command_whitelist: list[str] | None = None
        # v3.4.5: 家充桩管理探测结果（None=无该功能/未探测成功）
        self.smartwallbox: dict[str, Any] | None = None
        # v4.3.3: fposb 4S 维保预约 / evss 充电站免密支付 探测结果
        self.fposb: dict[str, Any] | None = None
        self.evss: dict[str, Any] | None = None
        # v4.3.4: 车辆主动告警列表 探测结果
        self.activealertlist: dict[str, Any] | None = None
        # v3.4.6: 只读探测结果（OTA 详情/预约出发/充电日志等，None=无数据）
        self.extra_probes: dict[str, Any] | None = None
        # v3.7.6: cevs 探测诊断（预约出发/充电三端点探测结果，供诊断传感器
        # 直接读取——不再依赖导出日志即可从 HA 侧确认探测成败）
        self.cevs_diag: dict[str, str] = {}
        # v3.4.1: 慢变数据 TTL 缓存（key -> (expire_monotonic, data)）——
        # 防止福特云限流：只有 vehicle-status 每轮拉取，其余按 TTL 命中
        # 直接复用上次数据，不发请求；请求失败保留旧缓存（最后已知状态）。
        self._cache: dict[str, tuple[float, Any]] = {}

    def _cache_hit(self, key: str) -> tuple[bool, Any]:
        ent = self._cache.get(key)
        if ent and time.monotonic() < ent[0]:
            return True, ent[1]
        return False, None

    async def _cached_fetch(
        self, key: str, ttl: float, factory, persist: bool = False,
        force: bool = False,
    ) -> Any:
        """TTL 缓存包装：命中直接返回旧值；失败保留旧值；成功写缓存。

        v3.4.2: ``factory`` 为协程工厂（如 ``lambda: api.get_x(vin)``）——
        只有 TTL 未命中（真正要发请求）时才创建协程并 await，避免
        TTL 命中时产生 "coroutine was never awaited" RuntimeWarning。
        v3.8.6: ``persist=True`` 时三级缓存——内存 TTL → HA Store 持久化
        TTL（重启/重载后 setup 复用，不再重探）→ factory → 写内存+落盘。
        探测类请求（白名单/额外端点/cevs/家充桩）一律 persist=True；
        常规轮询数据保持内存缓存（避免高频写盘）。
        v4.4.3: ``force=True`` 时绕过内存+持久化 TTL 命中，强制真拉——
        「手动刷新车辆状态」按钮复刻 App 下拉刷新（全量拉取所有状态端点），
        成功后照常写缓存刷新过期时间。force 时同样保留 old（内存现值）供
        失败兜底（实体保留最后已知状态），只是不因 TTL 命中短路。
        """
        if not force:
            hit, old = self._cache_hit(key)
            if hit:
                return old
            if persist and self._probe_cache:
                ent = self._probe_cache.get(key)
                if ent and time.time() < ent.get("expire", 0):
                    # 持久化命中：回填内存缓存（防止同轮重复读盘）
                    self._cache[key] = (time.monotonic() + ttl, ent["value"])
                    return ent["value"]
            old = None
        else:
            ent = self._cache.get(key)
            old = ent[1] if ent else None
        try:
            val = await factory()
        except FordPassProbeRejected as exc:
            # v4.0.9: 确定性能力缺失（业务拒绝/404）——落盘 None 并返回：
            # TTL 内不再重探（reload 不制造 6 次 404 噪音），与"瞬时故障"
            # 严格区分。
            self.logger.debug("FordPass %s probe rejected: %s", key, exc)
            self._cache[key] = (time.monotonic() + ttl, None)
            if persist and self._probe_store is not None:
                self._probe_cache[key] = {"expire": time.time() + ttl, "value": None}
                try:
                    await self._probe_store.async_save(self._probe_cache)
                except Exception as save_exc:  # noqa: BLE001
                    self.logger.debug("FordPass probe cache save failed: %s", save_exc)
            return None
        except Exception as exc:  # noqa: BLE001 - best effort，失败不阻塞刷新
            # v4.0.9: 网络瞬时故障/空响应等异常**不落盘**——下次 reload 重探，
            # 避免把瞬时故障错误钉死 7 天。
            self.logger.debug("FordPass %s fetch failed: %s", key, exc)
            if old is not None:
                return old
            if persist and self._probe_cache:
                ent = self._probe_cache.get(key)
                if ent:
                    return ent["value"]
            return None
        if val is not None:
            self._cache[key] = (time.monotonic() + ttl, val)
            # v4.0.9: 落盘只发生在"探测成功有数据"（本分支）或
            # FordPassProbeRejected（确定性能力缺失，落盘 None）——
            # val=None 的其它来源（空响应/网络瞬时失败）不再落盘，
            # 下次 reload 重探，避免把瞬时故障钉死 7 天。
            if persist and self._probe_store is not None:
                self._probe_cache[key] = {
                    "expire": time.time() + ttl,
                    "value": val,
                }
                try:
                    await self._probe_store.async_save(self._probe_cache)
                except Exception as exc:  # noqa: BLE001 - 落盘失败不影响运行
                    self.logger.debug("FordPass probe cache save failed: %s", exc)
        return val

    async def _load_probe_cache(self) -> None:
        """v3.8.6: 从 HA Store 读取探测结果持久化缓存（setup 时调用一次）。"""
        if self._probe_store is None:
            return
        try:
            data = await self._probe_store.async_load()
            if isinstance(data, dict):
                self._probe_cache = data
                self.logger.debug(
                    "FordPass probe cache loaded: %s entries", len(data)
                )
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("FordPass probe cache load failed: %s", exc)

    async def async_probe_capabilities(self, force: bool = False) -> None:
        """探测云端命令白名单 + 新增 GET 端点结构（v3.3.7，尽力而为）。

        setup 阶段调用一次：成功后 command_whitelist 供各平台按命令创建
        实体（全车型自动适配）；失败保持 None，平台不创建新增实体
        （现有实体不受影响）。探测均为只读/幂等，不触发任何车辆动作。
        v3.8.6: 探测结果持久化——重启/重载后 setup 先读 Store，TTL 内
        直接复用（不再重探制造噪音）；TTL 过期或首次才真正探测。
        v4.6.1: ``force=True`` 绕过 TTL 强制重探全部探测端点——「手动刷新
        车辆状态」按钮在 force_refresh 后调用，让探测类实体（车型主数据/
        OTA 协议/应用设置/分享能力/用户登录状态/充电域/维保预约/告警等）
        与常规轮询实体同步刷新（此前探测只在 setup 一次，手动刷新不覆盖）。
        v4.6.2: ``force=True`` 加冷却——60s 内重复手动刷新不再重探探测端点
        （探测 ~39 请求突发，高频点按会触发云端限流），仅常规全量刷新；
        setup 调用（force=False）不受冷却影响。
        """
        if force:
            now = datetime.now()
            if self._last_probe_force_at is not None and (
                now - self._last_probe_force_at
            ).total_seconds() < PROBE_FORCE_COOLDOWN:
                self.logger.debug(
                    "FordPass probe force skipped (cooldown %ss)",
                    PROBE_FORCE_COOLDOWN,
                )
                return
            self._last_probe_force_at = now
        await self._load_probe_cache()
        try:
            self.command_whitelist = await self._cached_fetch(
                "whitelist", PROBE_PERSIST_TTL,
                lambda: self.api.probe_command_whitelist(self.vin),
                persist=True, force=force,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("FordPass capability probe failed: %s", exc)
            self.command_whitelist = None
        fuel = str(self.vehicle_info.get("fuelType") or "").upper()
        try:
            self.extra_probes = await self._cached_fetch(
                "extra_probes", PROBE_PERSIST_TTL,
                lambda: self.api.probe_extra_endpoints(self.vin, fuel_type=fuel),
                persist=True, force=force,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("FordPass extra endpoint probe failed: %s", exc)
        # v4.8.1: 林肯之道专属端点探测（brand==lincoln 时，setup 一次性）：
        # 结果并入 extra_probes（探测到数据才创建传感器；福特派账号不探测、
        # 键不存在不创建，零副作用）。404/业务拒绝由 api 抛 ProbeRejected
        # 或返回 None，均不会进入 extra_probes。
        if self.brand == BRAND_LINCOLN:
            for _lkey, _lfn in (
                ("lincoln_member_info", self.api.probe_lincoln_member_info),
                ("lincoln_my_page", self.api.probe_lincoln_my_page),
                ("lwosb_bookings", self.api.probe_lwosb_bookings),
                ("lwosb_order_status", self.api.probe_lwosb_order_status),
            ):
                try:
                    _lr = await _lfn()
                    if _lr:
                        self.extra_probes = {
                            **(self.extra_probes or {}), _lkey: _lr,
                        }
                except FordPassProbeRejected:
                    continue
                except Exception as exc:  # noqa: BLE001
                    self.logger.debug("Lincoln %s probe failed: %s", _lkey, exc)
        # v3.7.6: cevs 探测诊断（预约出发/充电三端点探测结果，供诊断传感器
        # 直接读取——不再依赖导出日志即可从 HA 侧确认探测成败）
        # v3.7.10: 直接引用 api.cevs_diag 同一 dict——自动探测写入后 sensor
        # 立即可见（拷贝会失去同步）；v4.9.6 起无 probe_cevs_variant 服务
        # v3.7.18: cevs 自动探测独立 try（probe_cevs_auto）——此前嵌在
        # extra_probes 探测内部，前段失败即中断、diag 不创建
        # v3.8.5: 按车型跳过探测——纯油(G)/柴油(D)车 cevs 三端点（预约出发/
        # 充电/命令状态）业务恒空且不创建实体（v3.8.2 创建规则），探测白打
        # 请求并制造 Impossible modulus 噪音；与创建规则对称：fuelType ∈
        # 电/插混/混动才探测。
        # v3.8.6: 探测结果持久化（probe+diag 一并缓存）——reload 后 setup
        # TTL 内复用，不重探。
        if fuel in ("E", "BEV", "P", "PHEV", "H", "HEV", "MHEV"):
            try:
                async def _cevs_factory():
                    await self.api.probe_cevs_auto(self.vin)
                    return {
                        "probe": getattr(self.api, "cevs_probe", {}),
                        "diag": getattr(self.api, "cevs_diag", {}),
                    }

                cevs = await self._cached_fetch(
                    "cevs", PROBE_PERSIST_TTL, _cevs_factory, persist=True,
                    force=force,
                ) or {}
                self.cevs_probe = cevs.get("probe", {})
                self.cevs_diag = cevs.get("diag", {})
            except Exception as exc:  # noqa: BLE001
                self.logger.debug("FordPass cevs auto probe failed: %s", exc)
                self.cevs_probe = {}
                self.cevs_diag = {}
        else:
            self.cevs_probe = {}
            self.cevs_diag = {}
            self.logger.info(
                "FordPass cevs probe skipped for fuelType %s",
                fuel or "unknown",
            )
        # v3.4.5: 家充桩管理（smartwallbox）端点探测——登录后一次 + TTL
        # 持久化缓存（v3.8.6 起 reload 复用，不再重探；探测类请求绝不进入
        # 常规轮询，防福特云限流）；探测结果供 sensor 按"探测到数据才创建"
        # 接入。失败/非家充桩车型保持 None。
        # v4.5.7: **按车型跳过**——汽油(G)/柴油(D)车无家充桩，此前每次
        # setup 探测 8 端点×2 形态全 404（~16 次噪音），与 evss/cevs 对称。
        if fuel in ("", "G", "D"):
            self.smartwallbox = None
            self.logger.info(
                "FordPass smartwallbox probe skipped for fuelType %s",
                fuel or "unknown",
            )
        else:
            self.smartwallbox = await self._cached_fetch(
                "smartwallbox", PROBE_PERSIST_TTL,
                lambda: self.api.probe_smartwallbox(self.vin),
                persist=True, force=force,
            )

        # v4.3.3: fposb 4S 维保预约探测（登录后一次 + TTL 持久化缓存，
        # 不进常规轮询防限流）——结果供 sensor 按"探测到数据才创建"接入；
        # 无维保服务车型探测空 → None → 0 实体。
        self.fposb = await self._cached_fetch(
            "fposb", PROBE_PERSIST_TTL,
            lambda: self.api.probe_fposb(self.vin),
            persist=True, force=force,
        )

        # v4.3.3: evss 充电站/免密支付探测——**仅电/插混/混动车型执行**
        # （fuelType 过滤，纯油/柴油不探测不创建，与 cevs 规则对称）。
        if fuel in ("E", "BEV", "P", "PHEV", "H", "HEV", "MHEV"):
            self.evss = await self._cached_fetch(
                "evss", PROBE_PERSIST_TTL,
                lambda: self.api.probe_evss(self.vin),
                persist=True, force=force,
            )
        else:
            self.evss = None
            self.logger.info(
                "FordPass evss probe skipped for fuelType %s", fuel or "unknown"
            )

        # v4.3.4: 车辆主动告警列表（activealertlist）探测——登录后一次 +
        # TTL 持久化缓存，不进常规轮询；探测成功（业务 200）即创建实体，
        # 空列表显示「无告警」，失败/拒绝保持 None（不创建）。
        self.activealertlist = await self._cached_fetch(
            "activealertlist", PROBE_PERSIST_TTL,
            lambda: self.api.probe_active_alert_list(self.vin),
            persist=True, force=force,
        )

        # v3.9.1: 鸣笛设置云端状态（chirp_cloud）初始化——只在登录后探测
        # 阶段拉取一次（TTL 持久化缓存），**绝不进入常规轮询**：常规轮询
        # 若回读 preference，云端被 App/车机覆盖后会把旧值写回 data，
        # select 云端同步回调据此回写用户刚改的设置（改设置回弹）。
        # _save_cloud 上传成功后的回读仍会更新此值（权威确认）。
        try:
            data = dict(self.data or {})
            # v3.9.2: 探测无数据/失败时保留已恢复的最后已知值（不覆盖为空，
            # 防「鸣笛设置云端状态」传感器因空值不创建/显示未知）
            data["chirp_cloud"] = (
                await self._cached_fetch(
                    "chirp_preference", PROBE_PERSIST_TTL,
                    lambda: self.api.get_chirp_preference(),
                    persist=True, force=force,
                )
                or data.get("chirp_cloud") or {}
            )
            self.async_set_updated_data(data)
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("FordPass chirp preference init failed: %s", exc)
        # v3.9.1: 远程空调目标温度云端状态（remote_climate_cloud）初始化，
        # 与 chirp_cloud 同理（仅登录后一次，不进入常规轮询）。
        try:
            data = dict(self.data or {})
            data["remote_climate_cloud"] = (
                await self._cached_fetch(
                    "remote_climate_preference", PROBE_PERSIST_TTL,
                    lambda: self.api.get_remote_climate_preference(),
                    persist=True, force=force,
                )
                or data.get("remote_climate_cloud") or {}
            )
            self.async_set_updated_data(data)
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("FordPass remote climate pref init failed: %s", exc)
        # v4.6.1: force 重探后整体推送一次——extra_probes/cevs/fposb 等
        # 变化推给已创建的探测实体（sensor 在 update 时读这些属性）。
        if force:
            try:
                self.async_set_updated_data(dict(self.data or {}))
            except Exception as exc:  # noqa: BLE001
                self.logger.debug("FordPass probe force push failed: %s", exc)

    async def async_load_last_data(self) -> None:
        """Restore last-known data before first refresh (v3.3.3)."""
        if self._last_store is None:
            return
        try:
            data = await self._last_store.async_load()
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("FordPass load last data failed: %s", exc)
            return
        if isinstance(data, dict) and data:
            self.async_set_updated_data(data)
            self.logger.info(
                "FordPass restored last-known data for %s", self.vin[-6:]
            )

    def async_set_updated_data(self, data: dict[str, Any]) -> None:
        """Persist the fresh snapshot, then push to entities (v3.3.3)."""
        super().async_set_updated_data(data)
        if self._last_store is not None:
            self.hass.async_create_task(self._async_persist_last_data(data))

    async def _async_persist_last_data(self, data: dict[str, Any]) -> None:
        try:
            await self._last_store.async_save(data)
        except Exception as exc:  # noqa: BLE001 - 持久化失败不影响运行
            self.logger.debug("FordPass persist last data failed: %s", exc)
    @property
    def vehicle_model(self) -> str:
        """车型名（如「锐际 Escape」），用于车辆图片实体显示。"""
        return self._vehicle_name

    @property
    def is_diesel(self) -> bool:
        """燃料类型是否为柴油（v3.3.9）——汽油车不创建柴油系统实体。"""
        return str(self.vehicle_info.get("fuelType") or "").upper() == "D"

    @property
    def device_info(self) -> dict[str, Any]:
        """One shared device for every entity of this vehicle."""
        return dr.DeviceInfo(
            identifiers={(DOMAIN, self.vin)},
            name=self._vehicle_name,
            manufacturer="Ford",
            model=self._vehicle_name,
            sw_version="6.14.0",
        )

    async def force_refresh(self, force_all: bool = False) -> None:
        """Fetch data immediately and push it to entities (v2.7.0).

        HA's ``async_request_refresh`` only executes right away when the last
        poll is older than ``update_interval``; otherwise it re-schedules the
        refresh to the next interval tick, which makes a manual "刷新车辆状态"
        button appear dead.  This method bypasses that throttle: it fetches
        now and pushes the result with ``async_set_updated_data``.

        v4.4.3: ``force_all=True`` 复刻 App 下拉刷新——绕过所有慢变端点的
        TTL 缓存强制真拉（保养/召回/SIM/OTA/滤芯/ccfeatures/prognostic/
        告警/消息全量更新），与 App 下拉刷新拿全车最新状态一致。
        """
        try:
            data = await self._async_update_data(force_all=force_all)
        except Exception as exc:  # noqa: BLE001 - keep old data, log clearly
            self.logger.error("FordPass forced refresh failed: %s", exc)
            raise UpdateFailed(f"FordPass update failed: {exc}") from exc
        self.async_set_updated_data(data)

    async def run_command(self, command_type: str, wait: bool = True) -> None:
        """Send a remote command and reflect the result immediately (v2.7.7).

        Used by lock/switch/button so a press is not silently lost behind HA's
        refresh throttle: we send the command, poll command-execution-status
        until the decrypted snapshot flips to CURRENT, then push it straight
        to the entities.  A send failure raises so the UI reports it.

        ``wait=False`` skips the polling loop and only force-refreshes.
        """
        resp = await self.api.send_command(self.vin, command_type)
        command_id: str | None = None
        if isinstance(resp, dict):
            cid = resp.get("commandId")
            if not cid and isinstance(resp.get("data"), dict):
                cid = resp["data"].get("commandId")
            command_id = str(cid) if cid else None
        if command_id and wait:
            done, result = await self.api.wait_command_complete(
                self.vin, command_id, command_type
            )
            if done and isinstance(result, dict):
                vs = result.get("vehiclestatus", result)
                self.async_set_updated_data({"status": vs, "vehiclestatus": vs})
                return
        # Fallback: command sent but no fresh snapshot yet — force-refresh once.
        try:
            await self.force_refresh()
        except Exception as exc:  # noqa: BLE001 - refresh must not mask the result
            self.logger.warning(
                "FordPass refresh after %s failed: %s", command_type, exc
            )

    async def _async_update_data(self, force_all: bool = False) -> dict[str, Any]:
        try:
            status = await self.api.get_vehicle_status(self.vin)
            data: dict[str, Any] = {
                "status": status,
                "vehiclestatus": status.get("vehiclestatus", status),
            }
        except FordPassApiError as err:
            if err.code in (401, 403) and "token refresh failed" in str(err):
                # v4.5.0: 仅 refresh 也失败（api 内部已自动 refresh 一次且
                # refresh_token 本身失效）才是凭据永久失效 → 抛
                # ConfigEntryAuthFailed 停止轮询（避免每轮打云请求制造
                # "刷新失败"噪音），HA 自动启动 reauth flow；实体保留最后
                # 已知状态，重新认证后恢复。其他 401/403（无 refresh_token
                # 的瞬时 401、单端点业务 401 等）走下方 UpdateFailed 保留
                # 旧值，不误停轮询（此前所有 401 都停，实测 token 有效却
                # "实体不刷新"）。
                await self._log_activity("登录凭据已失效，请重新认证（设置 → 设备与服务 → 福特派互联）")
                raise ConfigEntryAuthFailed(f"FordPass token expired: {err}") from err
            # v4.3.8: 刷新失败也写活动（"不可用活动"）——此前 UpdateFailed
            # 直接抛出，摘要代码不执行 → 活动日志无任何记录，用户看不到
            # "刷新失败/云端不可用"的活动。失败保留最后已知状态（实体恒
            # available），但活动日志如实记录失败原因。
            await self._log_activity(
                f"车辆状态刷新失败（保留最后已知状态）：{err}"
            )
            raise UpdateFailed(f"FordPass update failed: {err}") from err
        except asyncio.TimeoutError as err:
            await self._log_activity(
                "车辆状态刷新失败（保留最后已知状态）：请求超时"
            )
            raise UpdateFailed("FordPass update timed out") from err

        # ---- v3.4.1 防限流优化：以下均走 TTL 缓存，慢变数据命中即复用，
        # 不发请求；请求失败保留旧缓存（最后已知状态），不阻塞主刷新。
        # （v3.4.2: 传协程工厂，TTL 命中时不创建协程，杜绝 RuntimeWarning）
        # 位置 LBS（track_location 开启时；v3.8.8: 跟随状态刷新间隔每轮
        # 刷新——不再固定 15 分钟 TTL，与车辆状态同步；失败保留最后已知
        # 坐标，避免 device_tracker / 定位传感器丢位置）
        if self.track_location:
            try:
                loc = await self.api.get_location(self.vin, self.coordinate_system)
                if isinstance(loc, dict) and loc.get("lat"):
                    data["location"] = loc
                elif (self.data or {}).get("location"):
                    data["location"] = (self.data or {}).get("location")
                else:
                    data["location"] = None
            except Exception as exc:  # noqa: BLE001
                self.logger.debug("FordPass location fetch failed: %s", exc)
                data["location"] = (self.data or {}).get("location")
        # Vehicle health alerts（30 分钟 TTL；失败保留旧列表/清空，
        # TTL 命中天然抑制重试——取代旧"连续失败 2 次抑制 1h"逻辑）
        alerts = await self._cached_fetch(
            "active_alerts", 1800, lambda: self.api.get_active_alerts(self.vin), force=force_all
        )
        data["active_alerts"] = alerts if isinstance(alerts, list) else []
        # OTA 设置状态（6 小时 TTL——OTA 排程配置几乎不变）
        data["ota_setting"] = await self._cached_fetch(
            "ota_setting", 21600, lambda: self.api.get_ota_setting(self.vin), force=force_all
        )
        # 服务信息端点（保养计划 12h / 召回 24h / SIM 6h TTL；WiFi 状态
        # v3.8.8 起跟随状态刷新间隔每轮刷新，不再固定 15 分钟）
        for key, ttl, factory in (
            ("maintenance_plan", 43200, lambda: self.api.get_maintenance_plan(self.vin)),
            ("recall", 86400, lambda: self.api.get_recall(self.vin)),
            ("sim_info", 21600, lambda: self.api.get_sim_info(self.vin)),
        ):
            data[key] = await self._cached_fetch(
                key, ttl, factory, force=force_all
            )
        # 车载 WiFi 状态（v3.8.8: 跟随状态刷新间隔每轮刷新；失败保留旧值）
        # v4.3.0: get_wifi_status 对确定性业务拒绝返回 None（已降噪），
        # None 同样保留最后已知状态（拒绝 ≠ 实体显示不可用）。
        try:
            wifi = await self.api.get_wifi_status(self.vin)
            data["wifi_status"] = wifi if wifi is not None else (self.data or {}).get("wifi_status")
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("FordPass wifi_status fetch failed: %s", exc)
            data["wifi_status"] = (self.data or {}).get("wifi_status")
        # 鸣笛设置云端状态（chirp_cloud）——v3.9.3: 恢复纳入常规轮询
        # （跟随状态刷新间隔每轮真拉，失败保留旧值）：App/车机端改动
        # 后 HA 自动同步「鸣笛设置云端状态」。v3.9.1 曾因"轮询会把 App
        # 覆盖后的值写回 data → select 云端回调回写 → 改设置回弹"移出
        # 轮询——回弹已由 v3.9.0 的 _syncing 上传保护根治（上传期间
        # select 忽略云端回写），故恢复轮询不再有回弹风险。
        try:
            data["chirp_cloud"] = await self.api.get_chirp_preference()
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("FordPass chirp preference fetch failed: %s", exc)
            data["chirp_cloud"] = (self.data or {}).get("chirp_cloud")
        # 空调滤芯状态（6 小时 TTL——健康度变化慢）
        data["air_filter"] = await self._cached_fetch(
            "air_filter", 21600, lambda: self.api.get_air_filter_status(self.vin), force=force_all
        )
        # 云端能力+服务信息（ccfeatures 24h TTL——出厂能力几乎永不变）
        # 与预测性诊断（prognostic 6h TTL——机油寿命/剩余里程变化慢）
        for key, ttl, factory in (
            ("ccfeatures", 86400, lambda: self.api.get_ccfeatures(self.vin)),
            ("prognostic", 21600, lambda: self.api.get_prognostic(self.vin)),
        ):
            data[key] = await self._cached_fetch(
                key, ttl, factory, force=force_all
            )
        # 未读消息摘要（30 分钟 TTL）
        data["messages"] = await self._cached_fetch(
            "messages", 1800, lambda: self.api.get_messages_summary(), force=force_all
        )
        # v2.7.8: record the successful poll time for the sensor last_poll
        # attribute (每轮自动刷新/手动刷新成功都会更新).
        self.last_poll = datetime.now()
        # v5.1.2: 每次轮询（自动/手动）都逐实体写各实体刷新状态活动——
        # 不再只写一条笼统摘要（摘要无实体关联时还会被设备页过滤不可见）。
        # 用户诉求 2026-10-17：每次轮询活动中要显示各实体的刷新状态。
        # _log_refresh_states 跳过 button./image. 与 unavailable/unknown/空
        # 状态实体，每轮为有实际状态的实体写「实体名：状态值」活动。
        try:
            await self._log_refresh_states()
        except Exception:  # noqa: BLE001 - logbook 失败不影响数据刷新
            self.logger.debug("fordpass_cn logbook entry failed", exc_info=True)
        # v5.1.4: 首轮刷新（重启/重载后 setup，实体尚未注册）时
        # all_entity_ids() 为空 → 逐实体活动写不了——调度 45s 后补写
        # （届时实体已注册，仅写本地 logbook，不额外拉福特云）。
        if not self.all_entity_ids() and not self._initial_states_pending:
            self._initial_states_pending = True
            self.hass.async_create_task(self._delayed_initial_states())
        # v4.3.8: 无条件创建的探测端点实体中"暂无数据"（探测无数据/拒绝）的
        # 也写活动（用户要求"包括不可用活动"）——纯油车充电域/无绑定桩等
        # 实体常驻显示「暂无数据」，活动日志同步可见（数量稳定，15s 去重
        # 只在首次写入；实体数据变化后自动恢复明细，不再写"暂无数据"）。
        # v4.3.9: 按车型创建——充电域实体纯油/柴油车不再创建（不统计不写
        # 活动，避免"无用实体"噪音）；仅统计实际创建的实体（全车型通用 +
        # 电/混动车的充电域）。
        try:
            _probe_names = {
                "wallbox_bound": "家充桩绑定", "wallbox_info": "家充桩墙盒详情",
                "wallbox_records_v2": "家充桩充电记录", "evss_order_status": "充电订单状态",
                "evss_apps_info": "EVSS 服务信息", "evss_usage_notice": "充电使用通知",
                "vpoi_status": "充电站状态", "vpoi_saved": "收藏充电站",
                "ota_agreements": "OTA 协议", "share_capability": "车辆分享能力",
                "share_history": "车辆分享历史", "evss_payment_summary": "充电支付汇总",
                "evss_invoiceables": "可开票充电订单", "evss_events_v3": "充电事件",
                "evss_evaluate_url": "充电订单评价链接",
                "wallbox_ble_vehicles": "家充桩 BLE 车辆信息",
                "app_init_settings": "应用设置信息",
                "vehicle_model_masterdata": "车型主数据",
                "user_login_status": "用户登录状态",
            }
            _charge_kinds = ("wallbox_bound", "wallbox_info", "wallbox_records_v2",
                             "evss_order_status", "evss_apps_info", "evss_usage_notice",
                             "vpoi_status", "vpoi_saved",
                             "evss_payment_summary", "evss_invoiceables",
                             "evss_events_v3", "evss_evaluate_url",
                             "wallbox_ble_vehicles")
            _fuel = str(self.vehicle_info.get("fuelType") or "").upper()
            _charge_ok = _fuel in ("E", "BEV", "P", "PHEV", "H", "HEV", "MHEV")
            ep = self.extra_probes or {}
            # v4.8.2: 车型主数据 v4.6.6 起主值来自车辆信息（vehicles/list
            # 明文 vehicle_info），探测端点无数据 ≠ 实体暂无数据——与
            # sensor.py 创建/取值条件对齐，不再误计入「暂无数据」。
            _masterdata_ok = bool(self.vehicle_info)
            empty = [
                n for k, n in _probe_names.items()
                if not ep.get(k)
                and not (k == "vehicle_model_masterdata" and _masterdata_ok)
                and (k not in _charge_kinds or _charge_ok)
            ]
            # v4.8.2: 仅 empty 集合变化时写活动（首次出现/实体恢复/新增
            # 无数据项），稳定态不重复刷屏——活动页保留「不可用活动」可见
            # 性，同时消除 81 条/24h 的同内容噪音。
            if empty and tuple(empty) != self._last_empty_probes:
                self._last_empty_probes = tuple(empty)
                shown = "、".join(empty[:5])
                more = f" 等 {len(empty)} 项" if len(empty) > 5 else ""
                await self._log_activity(
                    f"{len(empty)} 个实体暂无数据（未开通）：{shown}{more}",
                    associate=False,  # v5.1.4: 不挂车辆识别码（用户要求 VIN 只显示值）
                )
            elif not empty:
                self._last_empty_probes = None
        except Exception:  # noqa: BLE001 - logbook 失败不影响数据刷新
            self.logger.debug("fordpass_cn probe activity failed", exc_info=True)
        return data


    def activity_entity_id(self) -> str | None:
        """v5.1.1: 实时查 registry 的"车辆识别码"传感器 entity_id（活动日志关联）。

        unique_id=f"{vin}-vehicle_vin"（VIN 实体无条件创建、稳定存在）。
        历史教训：v4.5.1 关联"车辆信息"传感器（unique_id={vin}-vehicle_info），
        v4.5.2 删除该传感器后摘要活动 entity_id=None——全局活动页可见但
        集成设备页"来源"过滤视图不可见（entity_id=None 事件被设备过滤）。
        **不缓存**（每次实时查 registry），实体不存在时返回 None（活动事件
        不带实体关联，全局活动日志仍完整显示）。查询为内存操作，频率低
        （每轮刷新 1-2 次）。
        v4.5.10: 手动刷新完成事件改由 all_entity_ids() 关联全部实体（用户
        诉求：车况未变也要所有实体有刷新活动），本方法仅用于常规轮询摘要。
        """
        try:
            from homeassistant.helpers import entity_registry as er
            reg = er.async_get(self.hass)
            return reg.async_get_entity_id(
                "sensor", DOMAIN, f"{self.vin}-vehicle_vin"
            )
        except Exception:  # noqa: BLE001 - 找不到不阻断活动写入
            return None

    _STATE_ZH = {
        "locked": "已锁定",
        "unlocked": "已解锁",
        "jammed": "卡住",
        "locking": "锁定中",
        "unlocking": "解锁中",
        "on": "已开启",
        "off": "已关闭",
        "true": "是",
        "false": "否",
    }

    async def _log_refresh_states(self) -> None:
        """v4.7.7: 手动刷新完成后逐实体写刷新后的状态值（替代笼统的
        「手动刷新完成」消息）。

        每个实体的活动历史显示「实体名：状态值(单位)」——用户可看到
        各实体刷新后的真实状态（车况未变时亦显示当前值）。button 实体
        state 为最后按下时间戳（无状态含义），跳过。单条失败不影响其余。
        """
        for eid in self.all_entity_ids():
            if eid.startswith(("button.", "image.")):
                continue  # button=按下时间戳 / image=图片URL，无状态含义
            st = self.hass.states.get(eid)
            # v5.1.0: 跳过不可用/未知/空状态——活动页不显示无意义
            # 的「不可用」条目（用户 2026-10-17 要求：只显示有实际
            # 状态的刷新活动）。实体 unavailable 时保留最后已知值，
            # 但刷新活动不为其写「不可用」噪音。
            if st is None or st.state in ("unavailable", "unknown", ""):
                continue
            unit = st.attributes.get("unit_of_measurement", "")
            # v4.7.8: 消息只显示状态值（实体名由活动历史关联显示）——
            # 锁/开关类翻译成中文，数字类保留数值+单位（如 36%）。
            value = self._STATE_ZH.get(st.state, st.state)
            message = f"{value}{unit}"
            try:
                await self.hass.services.async_call(
                    "logbook", "log",
                    {
                        "name": self._vehicle_name or "福特派互联",
                        "message": message,
                        "domain": DOMAIN,
                        "entity_id": eid,
                    },
                )
            except Exception:  # noqa: BLE001 - 单条失败不影响其余
                self.logger.debug("fordpass_cn refresh state logbook failed: %s", eid, exc_info=True)

    async def _delayed_initial_states(self) -> None:
        """v5.1.4: 首轮刷新延迟补写逐实体活动（45s 后实体已注册）。

        重启/重载后 async_config_entry_first_refresh 先于实体注册执行，
        all_entity_ids() 为空导致逐实体活动写不了（只剩摘要）——本方法
        在实体注册完成后补写一次，仅写本地 logbook、不拉福特云。
        """
        try:
            await asyncio.sleep(45)
        finally:
            self._initial_states_pending = False
        if self.data is not None:
            await self._log_refresh_states()

    def all_entity_ids(self) -> list[str]:
        """v4.5.10: 集成全部实体 entity_id 列表（活动事件关联用）。

        手动刷新完成后写 logbook 事件并关联本集成所有实体——即使车况
        未变（实体值不变无 state_changed），每个实体的活动历史也能看到
        "手动刷新完成"刷新记录（用户诉求 2026-10-15）。registry 内存
        查询，频率低。查询失败返回空列表（事件不带关联，不阻断）。
        """
        try:
            from homeassistant.helpers import entity_registry as er
            reg = er.async_get(self.hass)
            # v4.7.6: HA 2026.10 已移除 async_entries_for_domain（v3.6.5
            # sensor.py 同款修复）——改用 reg.entities.values() 过滤
            # platform==DOMAIN；跳过用户禁用实体（disabled_by）。
            return [
                e.entity_id
                for e in reg.entities.values()
                if e.platform == DOMAIN and e.disabled_by is None
            ]
        except Exception:  # noqa: BLE001
            return []

    async def _log_activity(
        self,
        message: str,
        entity_ids: list[str] | None = None,
        dedup: bool = True,
        associate: bool = True,
    ) -> None:
        """活动日志写入（v4.3.8 抽取）：成功摘要/失败原因/暂无数据实体共用。

        15s 去重防并发双条（手动刷新与自动轮询撞车）；不同消息不受去重限制。
        logbook 失败不影响数据刷新（v4.0.13：直接 await，异常落进调用方
        except，不留 "Task exception was never retrieved" 噪音）。
        v4.4.4: 写入时关联车辆实体（entity_id）——不带 entity_id 的事件在
        HA 前端集成设备页活动标签不可见（entity_id=None 被设备过滤）。
        v4.5.10: 可选 entity_ids 参数——手动刷新完成事件传全实体列表，
        车况未变时每个实体的活动历史也有刷新记录（用户诉求）。
        """
        now = datetime.now()
        last_at = self._last_logbook_at
        if dedup and not (
            last_at is None
            or (now - last_at).total_seconds() >= 15
            or message != self._last_logbook_msg
        ):
            return
        try:
            # v4.4.8: 活动日志不带车辆实体——事件恢复全局日志面板可见、
            # 门锁等实体活动历史不被集成事件刷屏（用户 2026-10-14 要求）。
            # v4.5.1: 关联本车"车辆信息"传感器——集成详情页活动标签按集成
            # 实体过滤（entity_id=null 事件不可见）；关联后每次轮询/失败/
            # 暂无数据活动在该标签可见；门锁/油量等实体页不被刷屏。
            # v4.7.6: logbook.log 的 entity_id 只接受单个字符串（HA
            # 2026.10.1 实测传列表报 invalid_format）——entity_ids 参数
            # 改为逐实体循环写（每实体一条），满足"手动刷新后所有实体
            # 活动历史有刷新记录"；该模式 dedup=False（逐实体不受全局
            # 15s 去重吞掉）。
            if entity_ids:
                for eid in entity_ids:
                    try:
                        await self.hass.services.async_call(
                            "logbook", "log",
                            {
                                "name": self._vehicle_name or "福特派互联",
                                "message": message,
                                "domain": DOMAIN,
                                "entity_id": eid,
                            },
                        )
                    except Exception:  # noqa: BLE001 - 单条失败不影响其余
                        self.logger.debug("fordpass_cn logbook entry failed: %s", eid, exc_info=True)
            else:
                service_data: dict[str, Any] = {
                    "name": self._vehicle_name or "福特派互联",
                    "message": message,
                    "domain": DOMAIN,
                }
                act_eid = self.activity_entity_id() if associate else None
                if act_eid:
                    service_data["entity_id"] = act_eid
                await self.hass.services.async_call("logbook", "log", service_data)
        except Exception:  # noqa: BLE001 - logbook 失败不影响数据刷新
            self.logger.debug("fordpass_cn logbook entry failed", exc_info=True)
        if dedup:
            self._last_logbook_at = now
            self._last_logbook_msg = message
