"""DataUpdateCoordinator for the FordPass China integration."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any, ClassVar

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import FordPassApi, FordPassApiError, FordPassProbeRejected
from .const import (
    BRAND_LINCOLN,
    CONF_BRAND,
    COORDINATE_WGS84,
    DEFAULT_BRAND,
    DOMAIN,
    PROBE_FORCE_COOLDOWN,
    PROBE_PERSIST_TTL,
)

# v5.7.6: 隐私实体 unique_id 尾缀（logbook 活动跳过）从函数内提升为
# 模块级常量。device_tracker（-tracker）state 为 zone 状态
# （home/not_home）而非坐标/地址，不进活动历史，无需跳过——保留
# "车辆离家/回家"活动显示价值（其经纬度仅在 attribute 中）。
# 其余隐私尾缀在 v5.7.0 起精确跳过：VIN/车牌/定位的 state 值
# （真实 VIN、车牌、地址/坐标字符串）不得进 logbook 活动历史。
_PRIVACY_UID_SUFFIXES = ("-vehicle_vin", "-license_plate", "-location")

# v5.9.9: TTL 缓存两个缺陷的统一上限——「成功但云端返回 null」的短缓存
# TTL 与「请求失败回退旧值」的重试退避 TTL 都收敛到 1h：慢变端点
# （TTL 6h~24h）在故障/空值期间从每轮（30min）真拉降为约每 1h 一次，
# 云端恢复后最多 1h 内数据自动回归正常。
_TTL_EMPTY_RESULT = 3600.0
_TTL_FAIL_RETRY = 3600.0

_LOGGER = logging.getLogger(__name__)


def _uid_key(vin: str) -> str:
    """v5.7.1: VIN 脱敏唯一键——sha256 前 16 位 hex。

    实体 unique_id 与设备 identifiers 一律用此前缀，避免真实 VIN 进入
    entity/device registry（recorder 持久化、HA 导出、日志均可能暴露）。
    同一 VIN 永远同值（稳定，跨版本不变），不可逆（无法从哈希反推 VIN）。
    """
    return hashlib.sha256((vin or "").encode("utf-8")).hexdigest()[:16]


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
    for node in wp.values():
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
        uid_key = _uid_key(vin)  # v5.7.1: 脱敏前缀（name/identifiers/实体 unique_id）
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-{uid_key[-6:]}",
            update_interval=timedelta(minutes=interval),
            config_entry=config_entry,
        )
        self.api = api
        self.vin = vin  # 仅运行时云端请求用（加密字段），不进任何 registry
        self.uid_key = uid_key  # v5.7.1: 实体 unique_id / 设备 identifiers 前缀
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
        self._vehicle_name = vehicle_name or f"Ford {self.uid_key[-6:]}"
        # v3.4.1: active_alerts 频率由 TTL 缓存控制（30 分钟）——取代旧
        # "_alerts_fail/_alerts_hold_until 连续失败 2 次抑制 1h" 逻辑
        # （TTL 命中天然抑制重试，失败保留最后已知状态）。
        # v3.0.10: 保存鸣笛设置按钮经由此定位 hass.data[DOMAIN][entry_id] 的
        # honk_settings（按钮 _settings 依赖 entry_id 取配置实时值）。
        self.entry_id = entry_id
        # v3.1.13: 车辆列表 encryptedCarId（停车影像/行车监控请求参数——
        # 部分车型为 null = 车型无远程影像硬件，影像按钮不创建）
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
        # v5.3.3: 最后已知数据落盘任务引用跟踪——之前 async_create_task 无
        # 引用（fire-and-forget），卸载/重启竞态下任务可能被事件循环丢弃；
        # 现保存到集合 + done 回调清理，保证写盘任务存活到完成。
        self._persist_tasks: set[asyncio.Task] = set()
        # v5.5.0: 手动全量刷新冷却（防连点叠加风控请求量）
        self._last_force_all: float = 0.0
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
        # v5.8.7: probe 缓存写盘 3s 防抖（合并连续探测/命令失败落盘）+ 脏标记
        self._probe_save_ts: float = 0.0
        self._probe_save_task: asyncio.Task | None = None
        # v5.8.7: 命令失败黑名单 TTL——90 天自动过期（云端车型能力可能后台
        # 变更，永久黑名单会导致新能力永远不出现）
        self._cmd_fail_ttl_seconds: float = 90 * 86400
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
        # v5.1.8: 账号级共享缓存（问题 7 P2 修复）——chirp_cloud / messages
        # 等账户级端点（云端 UserPreference/未读消息摘要，多车共享同一份）
        # 存到这里，多车账号下每轮只真拉一次，其余车辆命中缓存，消除
        # 每车每轮重复请求同一账户数据的浪费。由 __init__.py 在 payload
        # 创建后回填（hass.data[DOMAIN][entry_id]["account_cache"]），
        # 所有 coordinator 共享同一 dict。
        self.account_cache: dict[str, tuple[float, Any]] | None = None
        # v5.3.4: 账号级共享缓存 per-key 锁——多车并发同时 miss（TTL 过期/
        # force 手动刷新）时只允许一个协程真拉，其余等锁后命中新缓存，
        # 消除 coordinator 层重复请求窗口（见 _cached_fetch）。
        self._account_locks: dict[str, asyncio.Lock] = {}
        # v5.1.8: logbook 失败节流（问题 11 P2 修复）——logbook 服务连续
        # 失败时暂停活动写入 1 小时，防止"每轮失败重试 + 日志风暴"；
        # 正常时行为不变（用户诉求：每轮显示各实体刷新状态）。
        self._logbook_fail_streak = 0
        self._logbook_hold_until: float | None = None
        # v5.2.8: 网络/云端不可用一次性警告标志（quality_scale silver 要求：
        # 不可用只警告一次、恢复时提示一次；重复失败降 debug 避免刷屏）
        self._update_failure_warned = False
        # v5.4.7: 云端同步健康状态（后端变更监控——复用轮询结果零额外请求）
        self._last_success_time = None
        self._last_error_time = None
        self._consecutive_failures = 0
        self._last_error_detail = None

    def _log_exception(self, err: Exception, message: str) -> None:
        """覆盖 DataUpdateCoordinator 失败日志：网络/云端不可用只警告一次。

        silver 要求「Log a warning once when unavailable, log once when
        reconnected」。首次失败走父类（error 级）；重复失败（默认 30 分钟
        一轮轮询）降级为 debug，不刷屏。活动日志不受影响——每轮活动显示
        各实体刷新状态由 _log_activity 负责，独立于本方法。恢复提示在
        _async_update_data 成功路径发出。
        """
        if self._update_failure_warned:
            self.logger.debug("%s (suppressed repeat): %s", message, err)
            return
        self._update_failure_warned = True
        super()._log_exception(err, message)

    def _cache_hit(self, key: str, account: bool = False) -> tuple[bool, Any]:
        store = self.account_cache if account else self._cache
        if not store:
            return False, None
        ent = store.get(key)
        if ent and time.monotonic() < ent[0]:
            return True, ent[1]
        return False, None

    async def _cached_fetch(
        self, key: str, ttl: float, factory, persist: bool = False,
        force: bool = False, account: bool = False,
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
        v5.1.8: ``account=True`` 走账号级共享缓存（self.account_cache）——
        账户级端点（chirp_cloud/messages 等，多车共享同一份）只真拉一次，
        其余车辆命中账号级缓存；force 时同样绕过并更新账号级缓存。
        """
        store = self.account_cache if account else self._cache
        if not force:
            hit, old = self._cache_hit(key, account=account)
            if hit:
                return old
            if not account and persist and self._probe_cache:
                ent = self._probe_cache.get(key)
                if ent and time.time() < ent.get("expire", 0):
                    # 持久化命中：回填内存缓存（防止同轮重复读盘）
                    self._cache[key] = (time.monotonic() + ttl, ent["value"])
                    return ent["value"]
            old = None
        else:
            ent = (store or {}).get(key)
            old = ent[1] if ent else None

        # v5.3.4: account 账号级共享缓存加 per-key 锁——多车并发同时 miss
        # （TTL 过期/force 手动刷新）时只允许一个协程真拉，其余等锁后命中
        # 新缓存（api 层 _account_cached 另有兜底，此锁消除 coordinator 层
        # 重复请求窗口，减少风控请求量）。
        async def _do_fetch() -> Any:
            try:
                val = await factory()
            except FordPassProbeRejected as exc:
                # v4.0.9: 确定性能力缺失（业务拒绝/404）——落盘 None 并返回：
                # TTL 内不再重探（reload 不制造 6 次 404 噪音），与"瞬时故障"
                # 严格区分。
                self.logger.debug("FordPass %s probe rejected: %s", key, exc)
                if store is not None:
                    store[key] = (time.monotonic() + ttl, None)
                if not account and persist and self._probe_store is not None:
                    self._probe_cache[key] = {"expire": time.time() + ttl, "value": None}
                    await self._probe_save()
                return None
            except Exception as exc:  # noqa: BLE001 - best effort，失败不阻塞刷新
                # v4.0.9: 网络瞬时故障/空响应等异常**不落盘**——下次 reload 重探，
                # 避免把瞬时故障错误钉死 7 天。
                self.logger.debug("FordPass %s fetch failed: %s", key, exc)
                # v5.9.9: 失败把旧值写回缓存并续期（上限 _TTL_FAIL_RETRY）——
                # 此前 expire 留在过去，下一轮必然再 miss 再真拉：云端故障
                # 持续多久，故障端点就每轮白打多久请求（风控请求量暴露面）。
                # 续期取 max（不缩短 force 路径原缓存剩余的未过期 TTL）。
                if store is not None:
                    prev = store.get(key)
                    fallback = old if old is not None else (prev[1] if prev else None)
                    if fallback is not None:
                        backoff = max(
                            prev[0] if prev else 0.0,
                            time.monotonic() + min(ttl, _TTL_FAIL_RETRY),
                        )
                        store[key] = (backoff, fallback)
                        return fallback
                if old is not None:
                    return old
                if not account and persist and self._probe_cache:
                    ent = self._probe_cache.get(key)
                    if ent:
                        return ent["value"]
                return None
            if val is not None:
                if store is not None:
                    store[key] = (time.monotonic() + ttl, val)
                # v4.0.9: 落盘只发生在"探测成功有数据"（本分支）或
                # FordPassProbeRejected（确定性能力缺失，落盘 None）——
                # val=None 的其它来源（空响应/网络瞬时失败）不再落盘，
                # 下次 reload 重探，避免把瞬时故障钉死 7 天。
                if not account and persist and self._probe_store is not None:
                    self._probe_cache[key] = {
                        "expire": time.time() + ttl,
                        "value": val,
                    }
                    await self._probe_save()
            elif store is not None:
                # v5.9.9: 请求成功但云端返回 null 也写短 TTL 缓存——此前
                # None 不落缓存，TTL 永不命中 → 该端点每轮（30min）必真拉，
                # 云端长期返回 null 的端点每天白打 48 次请求。短 TTL 保证
                # 「暂无数据」不是永久态，云端补上后最多 1h 内回归；调用方
                # 对 None 已有「沿用上一轮 data」兜底，实体行为不变。落盘
                # 仍不持久化 None（reload 重探语义不变）。
                store[key] = (time.monotonic() + min(ttl, _TTL_EMPTY_RESULT), None)
            return val

        if account:
            lock = self._account_locks.setdefault(key, asyncio.Lock())
            async with lock:
                # 双重检查：等待锁期间其他车辆协程可能已刷新缓存
                if not force:
                    hit, old2 = self._cache_hit(key, account=True)
                    if hit:
                        return old2
                return await _do_fetch()
        return await _do_fetch()

    async def _probe_save(self) -> None:
        """v5.8.7: probe 缓存写盘 3s 防抖（HA Store 原子写）。

        探测结果落盘与命令失败黑名单落盘共用：距上次写盘 ≥3s 立即写，
        否则标记脏并调度 3s 后合并写——连续探测/连续命令失败不再每次
        全量 async_save（HA Store 本身原子写：临时文件 + rename，防断电
        损坏，无需自建 .tmp）。
        """
        if self._probe_store is None:
            return
        now = time.monotonic()
        if now - self._probe_save_ts < 3.0:
            if self._probe_save_task is None or self._probe_save_task.done():
                self._probe_save_task = asyncio.create_task(
                    self._probe_save_delayed()
                )
                # v6.3.7: 纳入 _persist_tasks——卸载时统一 cancel/await，
                # 此前该任务游离于任务集合之外，卸载后仍会跑一拍写盘。
                self._persist_tasks.add(self._probe_save_task)
                self._probe_save_task.add_done_callback(self._persist_tasks.discard)
            return
        self._probe_save_ts = now
        try:
            await self._probe_store.async_save(self._probe_cache)
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("FordPass probe cache save failed: %s", exc)

    async def _probe_save_delayed(self) -> None:
        """防抖延迟写：sleep 3s 后合并写盘（防重复调度由 _probe_save 保证）。"""
        await asyncio.sleep(3.0)
        if self._probe_store is None:
            return
        self._probe_save_ts = time.monotonic()
        try:
            await self._probe_store.async_save(self._probe_cache)
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("FordPass probe cache save failed (delayed): %s", exc)

    async def _load_probe_cache(self) -> None:
        """v3.8.6: 从 HA Store 读取探测结果持久化缓存（setup 时调用一次）。

        v5.8.7: 加载后对命令失败黑名单做 90 天 TTL 淘汰——云端车型能力
        可能后台变更，永久黑名单会导致新能力永远不出现；旧版无 ts 的
        历史条目保留（无法判断时间，兼容升级）。
        """
        if self._probe_store is None:
            return
        try:
            data = await self._probe_store.async_load()
            if isinstance(data, dict):
                self._probe_cache = data
                # v5.8.7: cmd_fail TTL 淘汰（条目结构 {"msg":..., "ts":...}）
                fails = self._probe_cache.get("cmd_fail")
                if isinstance(fails, dict):
                    cutoff = time.time() - self._cmd_fail_ttl_seconds
                    stale = [
                        k
                        for k, v in fails.items()
                        if isinstance(v, dict) and v.get("ts", 0) < cutoff
                    ]
                    for k in stale:
                        fails.pop(k, None)
                    if stale:
                        self.logger.info(
                            "FordPass probe cache: %s 条命令失败黑名单已过期（>%ds）淘汰",
                            len(stale), int(self._cmd_fail_ttl_seconds),
                        )
                        await self._probe_store.async_save(self._probe_cache)
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
            now = dt_util.now()
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
        # v5.3.1: 4 端点改走 _cached_fetch(persist=True)——此前每次
        # setup/重载都真拉 4 次请求，违背「探测类请求只在登录后执行一次
        # 并缓存」风控策略；现 TTL 内重载复用、ProbeRejected 落盘 None
        # 不再重探。
        if self.brand == BRAND_LINCOLN:
            for _lkey, _lfn in (
                ("lincoln_member_info", self.api.probe_lincoln_member_info),
                ("lincoln_my_page", self.api.probe_lincoln_my_page),
                ("lwosb_bookings", self.api.probe_lwosb_bookings),
                ("lwosb_order_status", self.api.probe_lwosb_order_status),
            ):
                try:
                    _lr = await self._cached_fetch(
                        _lkey, PROBE_PERSIST_TTL, _lfn, persist=True, force=force
                    )
                    if _lr:
                        self.extra_probes = {
                            **(self.extra_probes or {}), _lkey: _lr,
                        }
                except Exception as exc:  # noqa: BLE001 - 单端点失败不阻断其余探测
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
            # v5.3.3: 任务引用跟踪（见 __init__._persist_tasks）——防止
            # fire-and-forget 任务在卸载/重启竞态被丢弃；done 后自动清出集合。
            task = self.hass.async_create_task(self._async_persist_last_data(data))
            self._persist_tasks.add(task)
            task.add_done_callback(self._persist_tasks.discard)

    async def _async_persist_last_data(self, data: dict[str, Any]) -> None:
        try:
            await self._last_store.async_save(data)
        except Exception as exc:  # noqa: BLE001 - 持久化失败不影响运行
            self.logger.debug("FordPass persist last data failed: %s", exc)
    @property
    def vehicle_model(self) -> str:
        """车型名（如「<车型>」），用于车辆图片实体显示。"""
        return self._vehicle_name

    @property
    def is_diesel(self) -> bool:
        """燃料类型是否为柴油（v3.3.9）——汽油车不创建柴油系统实体。"""
        return str(self.vehicle_info.get("fuelType") or "").upper() == "D"

    @property
    def device_info(self) -> dict[str, Any]:
        """One shared device for every entity of this vehicle."""
        # P2-6（v5.6.0）：按品牌填 manufacturer/sw_version——此前对所有
        # 品牌硬编码 Ford/6.14.0，林肯车辆设备信息显示福特品牌。
        if self.brand == BRAND_LINCOLN:
            manufacturer = "Lincoln"
            sw_version = "6.7.0"
        else:
            manufacturer = "Ford"
            sw_version = "6.14.0"
        return dr.DeviceInfo(
            identifiers={(DOMAIN, self.uid_key)},  # v5.7.1: VIN 脱敏（sha256 前缀）
            name=self._vehicle_name,
            manufacturer=manufacturer,
            model=self._vehicle_name,
            sw_version=sw_version,
        )

    FORCE_ALL_COOLDOWN = 30.0  # 手动全量刷新冷却秒数（v5.5.0，防连点）

    async def force_refresh(self, force_all: bool = False) -> None:
        """Fetch data immediately and push it to entities (v2.7.0).

        v5.5.0: force_all=True（手动刷新按钮）加 30s 冷却——force_all 绕过
        所有 TTL 强制真拉，连点会成倍放大云端请求量触发风控；冷却期内
        重复点击静默跳过（debug 日志）。车控命令后的 force_refresh()
        （force_all=False）不受冷却限制，保证命令后即时状态更新。"""
        if force_all:
            now = time.monotonic()
            if now - self._last_force_all < self.FORCE_ALL_COOLDOWN:
                self.logger.debug(
                    "FordPass manual force-refresh skipped (cooldown %.0fs)",
                    self.FORCE_ALL_COOLDOWN - (now - self._last_force_all),
                )
                return
            self._last_force_all = now

        try:
            data = await self._async_update_data(force_all=force_all)
        except Exception as exc:
            self.logger.error("FordPass forced refresh failed: %s", exc)
            raise UpdateFailed(f"FordPass update failed: {exc}") from exc
        self.async_set_updated_data(data)

    def cmd_rejected(self, command_type: str) -> str | None:
        """该命令被云端确定性拒绝的摘要（None=无记录，可按车型创建）。

        v5.8.3: 「按车型创建」的实测驱动依据——命令执行被网关确定性拒绝
        （如 228205 cmdSpec can not empty / 100502 非法命令 / 402 业务拒绝）
        后记录并持久化，重启后该车型不再创建对应实体；无记录的车型按既有
        能力字段创建（不臆断车型支持与否，见 entity-vehicle-creation.md）。
        """
        if not isinstance(self._probe_cache, dict):
            return None
        fails = self._probe_cache.get("cmd_fail")
        if not isinstance(fails, dict):
            return None
        v = fails.get(command_type)
        # v5.8.7: 兼容旧版字符串值（无 ts 的历史条目）与新版 {"msg","ts"} 结构
        if isinstance(v, dict):
            return str(v.get("msg", v))
        return str(v) if v else None

    async def async_record_cmd_fail(
        self, command_type: str, exc: BaseException
    ) -> None:
        """命令被网关确定性拒绝 → 记录"该车型不支持"并持久化（v5.8.3）。

        仅确定性业务拒绝记录（228205/100502/402/400/404 或 cmdSpec/
        not support/not allowed 关键词）；网络超时、5xx 熔断、401/403
        风控属瞬时/外部限制，不记录，避免误判车型不支持而砍实体。
        """
        if not isinstance(exc, FordPassApiError):
            self.logger.debug(
                "FordPass 命令 %s 失败（非网关拒绝，不记录车型不支持）: %s",
                command_type, str(exc)[:160],
            )
            return
        code = exc.code
        msg = str(exc).lower()
        deterministic = (
            code in (100502, 228205, 402, 400, 404)
            or "cmdspec" in msg
            or "not support" in msg
            or "not allowed" in msg
        )
        if not deterministic:
            self.logger.debug(
                "FordPass 命令 %s 失败码 %s（非确定性拒绝，不记录）: %s",
                command_type, code, str(exc)[:160],
            )
            return
        if not isinstance(self._probe_cache, dict):
            return
        fails = self._probe_cache.get("cmd_fail")
        if not isinstance(fails, dict):
            fails = {}
            self._probe_cache["cmd_fail"] = fails
        fails[command_type] = {
            "msg": str(exc)[:200],
            "ts": time.time(),  # v5.8.7: 记录时间戳，供 90 天 TTL 淘汰
        }
        self.logger.warning(
            "FordPass 命令 %s 被云端拒绝（记录为不支持，下次重启不再创建该实体）: %s",
            command_type, str(exc)[:200],
        )
        await self._probe_save()

    async def async_reset_cmd_fail(self) -> None:
        """v5.8.7: 清空命令失败黑名单（options flow「重置车型能力黑名单」）。

        立即写盘（不等防抖窗口），下次重启该车型全部命令实体按既有能力
        字段重新创建——云端能力后台变更后可手动恢复。
        """
        if not isinstance(self._probe_cache, dict):
            return
        fails = self._probe_cache.get("cmd_fail")
        if not isinstance(fails, dict) or not fails:
            self.logger.info("FordPass 命令失败黑名单已为空，无需重置")
            return
        self._probe_cache["cmd_fail"] = {}
        if self._probe_store is not None:
            try:
                await self._probe_store.async_save(self._probe_cache)
            except Exception as save_exc:  # noqa: BLE001
                self.logger.debug("FordPass cmd_fail 重置持久化失败: %s", save_exc)
        self.logger.info(
            "FordPass 命令失败黑名单已重置（%s 条清空，重启后按车型能力重新创建实体）",
            len(fails),
        )

    async def run_command(self, command_type: str, wait: bool = True) -> None:
        """Send a remote command and reflect the result immediately (v2.7.7).

        Used by lock/switch/button so a press is not silently lost behind HA's
        refresh throttle: we send the command, poll command-execution-status
        until the decrypted snapshot flips to CURRENT, then push it straight
        to the entities.  A send failure raises so the UI reports it
        (v5.8.3: 同时记录确定性拒绝 → 该车型下次重启不再创建对应实体).

        ``wait=False`` skips the polling loop and only force-refreshes.
        """
        try:
            resp = await self.api.send_command(self.vin, command_type)
        except Exception as exc:  # noqa: BLE001 - 记录车型不支持后如实上报
            await self.async_record_cmd_fail(command_type, exc)
            raise
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
                # v5.3.4: 合并推送而非整体替换——async_set_updated_data 会
                # 覆盖整个 data，若只写 status/vehiclestatus 两键，location/
                # active_alerts/ota_setting/air_filter/ccfeatures/prognostic/
                # messages/保养/SIM/WiFi/chirp_cloud 等键全部丢失，服务信息
                # 类实体立即变空值（None），最长 30 分钟轮询才恢复。
                self.async_set_updated_data(
                    {**(self.data or {}), "status": vs, "vehiclestatus": vs}
                )
                return
        # Fallback: command sent but no fresh snapshot yet — force-refresh once.
        try:
            await self.force_refresh()
        except Exception as exc:  # noqa: BLE001 - refresh must not mask the result
            self.logger.warning(
                "FordPass refresh after %s failed: %s", command_type, exc
            )

    def _mark_sync_failure(self, exc: Exception) -> None:
        """v5.4.7: 云端同步健康打点——连续失败计数 + 最后失败时间/摘要。
        后端变更监控的廉价方案：失败时诊断实体「云端同步状态」转异常并
        带失败摘要，用户可接自动化告警；无额外请求。"""
        self._last_error_time = dt_util.now()
        self._consecutive_failures = getattr(self, "_consecutive_failures", 0) + 1
        detail = str(exc)
        self._last_error_detail = detail[:400] if detail else "未知错误"

    async def _async_update_data(self, force_all: bool = False) -> dict[str, Any]:
        try:
            status = await self.api.get_vehicle_status(self.vin)
            data: dict[str, Any] = {
                "status": status,
                "vehiclestatus": status.get("vehiclestatus", status),
            }
        except FordPassApiError as err:
            if err.code in (401, 403) and getattr(err, "refresh_failed", False):
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
            self._mark_sync_failure(err)
            raise UpdateFailed(f"FordPass update failed: {err}") from err
        except TimeoutError as err:
            await self._log_activity(
                "车辆状态刷新失败（保留最后已知状态）：请求超时"
            )
            self._mark_sync_failure(err)
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
        # Vehicle health alerts（60 分钟 TTL；失败保留旧列表/清空，
        # TTL 命中天然抑制重试——取代旧"连续失败 2 次抑制 1h"逻辑）
        # v5.7.4: TTL 1800→3600——旧值与刷新间隔 30min 等长，每轮 TTL 刚
        # 过期导致缓存永不命中（每轮真拉）；拉长到 60min 后每两轮命中一次，
        # 告警实时性仍为 30min 轮询粒度内可接受。
        alerts = await self._cached_fetch(
            "active_alerts", 3600, lambda: self.api.get_active_alerts(self.vin), force=force_all
        )
        # v5.9.6: 拉取失败（返回 None）时沿用上一轮列表，不再强制写 []。
        # 此前 None → []，「拿不到」与「确实没有告警」被混为一谈，导致：
        # ① 告警事件实体把 [] 判成"告警全部消失"→ 误发 cleared；
        # ②「车辆异常警示」传感器落到 vehicle-status 兜底 → 误显「无异常」。
        # 只有云端真的返回空列表才写 []（这才是「无告警」的确证）。
        if isinstance(alerts, list):
            data["active_alerts"] = alerts
        else:
            prev_alerts = (self.data or {}).get("active_alerts")
            data["active_alerts"] = prev_alerts if isinstance(prev_alerts, list) else []
            self.logger.debug("FordPass active_alerts 拉取失败，沿用上一轮列表")
        # OTA 设置状态（6 小时 TTL——OTA 排程配置几乎不变）
        # v5.9.6: 同理保留上一轮字典——失败写 None 会让 update 实体短暂变
        # unknown（已安装/最新版本都取不到），OTA 传感器也退成「暂无数据」。
        ota = await self._cached_fetch(
            "ota_setting", 21600, lambda: self.api.get_ota_setting(self.vin), force=force_all
        )
        if isinstance(ota, dict):
            data["ota_setting"] = ota
        else:
            prev_ota = (self.data or {}).get("ota_setting")
            data["ota_setting"] = prev_ota if isinstance(prev_ota, dict) else None
            self.logger.debug("FordPass ota_setting 拉取失败，沿用上一轮数据")
        # 服务信息端点（保养计划 12h / 召回 24h TTL；WiFi 状态
        # v3.8.8 起跟随状态刷新间隔每轮刷新，不再固定 15 分钟）
        for key, ttl, factory in (
            ("maintenance_plan", 43200, lambda: self.api.get_maintenance_plan(self.vin)),
            ("recall", 86400, lambda: self.api.get_recall(self.vin)),
        ):
            data[key] = await self._cached_fetch(
                key, ttl, factory, force=force_all
            )
        # v6.0.1: SIM 卡信息改为持久化一次拉取——ICCID/运营商/手机号是
        # 写卡即定的终身恒量，此前 6h TTL 意味着每天真拉 4 次、每次重启
        # 重拉。改走探测同级缓存（PROBE_PERSIST_TTL=7 天 + 落盘持久化，
        # 重启/重载复用）：纯油车确定性业务拒绝（FordPassProbeRejected）
        # 照旧落盘 None 不再重探；手动「刷新车辆状态」仍强制真拉。
        data["sim_info"] = await self._cached_fetch(
            "sim_info", PROBE_PERSIST_TTL,
            lambda: self.api.get_sim_info(self.vin),
            persist=True, force=force_all,
        )
        # 车载 WiFi 状态（v3.8.8: 跟随状态刷新间隔每轮刷新；失败保留旧值）
        # v4.3.0: get_wifi_status 对确定性业务拒绝返回 None（已降噪），
        # None 同样保留最后已知状态（拒绝 ≠ 实体显示不可用）。
        # v6.0.3: CONS.AUTH.0005 是确定性拒绝（纯油车型无热点服务，永不成功），
        # 不再每轮白打——首次拒绝后在 probe_cache 落标记（持久化，
        # PROBE_PERSIST_TTL 内直接沿用旧值，手动刷新 force_all 可越过）；
        # 成功路径行为不变（仍每轮真拉，用户要求）。不用 _cached_fetch 包装，
        # 因为它会把成功值也缓存 TTL——违背「每轮刷新」语义。
        _wifi_rej = (self._probe_cache or {}).get("wifi_status_rejected")
        if not force_all and _wifi_rej and time.time() < _wifi_rej.get("expire", 0):
            data["wifi_status"] = (self.data or {}).get("wifi_status")
        else:
            try:
                wifi = await self.api.get_wifi_status(self.vin)
                data["wifi_status"] = (
                    wifi if wifi is not None else (self.data or {}).get("wifi_status")
                )
                if wifi is not None and "wifi_status_rejected" in self._probe_cache:
                    # 服务恢复可用：清掉拒绝标记，回到每轮真拉
                    self._probe_cache.pop("wifi_status_rejected", None)
                    await self._probe_save()
            except FordPassProbeRejected:
                # 确定性拒绝：落盘标记（v6.0.3），TTL 内不再请求
                self._probe_cache["wifi_status_rejected"] = {
                    "expire": time.time() + PROBE_PERSIST_TTL,
                    "value": None,
                }
                await self._probe_save()
                self.logger.info(
                    "FordPass wifi/status 确定性拒绝（车型无热点服务），"
                    "%d 天内不再请求（手动刷新可越过）",
                    int(PROBE_PERSIST_TTL // 86400),
                )
                data["wifi_status"] = (self.data or {}).get("wifi_status")
            except Exception as exc:  # noqa: BLE001
                self.logger.debug("FordPass wifi_status fetch failed: %s", exc)
                data["wifi_status"] = (self.data or {}).get("wifi_status")
        # 鸣笛设置云端状态（chirp_cloud）——v3.9.3: 恢复纳入常规轮询
        # （跟随状态刷新间隔每轮真拉，失败保留旧值）：App/车机端改动
        # 后 HA 自动同步「鸣笛设置云端状态」。v3.9.1 曾因"轮询会把 App
        # 覆盖后的值写回 data → select 云端回调回写 → 改设置回弹"移出
        # 轮询——回弹已由 v3.9.0 的 _syncing 上传保护根治（上传期间
        # select 忽略云端回写），故恢复轮询不再有回弹风险。
        # v5.1.8: 云端 UserPreference 为账户级（多车共享同一份）——改为
        # 账号级共享缓存（TTL 600s），多车账号每轮只真拉一次，其余车辆
        # 命中账号级缓存；单车主每轮（30min > TTL）行为不变。App 端改动
        # 同步延迟最多 10 分钟（select 上传仍即时）。
        data["chirp_cloud"] = await self._cached_fetch(
            "chirp_cloud", 600, lambda: self.api.get_chirp_preference(),
            force=force_all, account=True,
        )
        if data["chirp_cloud"] is None:
            data["chirp_cloud"] = (self.data or {}).get("chirp_cloud")
        # 空调滤芯状态（6 小时 TTL——健康度变化慢）
        data["air_filter"] = await self._cached_fetch(
            "air_filter", 21600, lambda: self.api.get_air_filter_status(self.vin), force=force_all
        )
        # 云端能力+服务信息：ccfeatures v6.0.1 起持久化一次拉取——救援/
        # 客服/售后电话、视频说明书 URL、能力位图（availableFeatures）都是
        # 出厂即定的恒量，此前 24h TTL 每天真拉 1 次；现改探测同级缓存
        # （PROBE_PERSIST_TTL=7 天 + 落盘，重启/重载复用），新购云端功能
        # 最长 7 天自动同步，「手动刷新车辆状态」仍立即重拉。
        # 预测性诊断（prognostic 6h TTL——机油寿命/剩余里程变化慢，属
        # 会变化数据，保持周期轮询）。
        data["ccfeatures"] = await self._cached_fetch(
            "ccfeatures", PROBE_PERSIST_TTL,
            lambda: self.api.get_ccfeatures(self.vin),
            persist=True, force=force_all,
        )
        data["prognostic"] = await self._cached_fetch(
            "prognostic", 21600, lambda: self.api.get_prognostic(self.vin),
            force=force_all,
        )
        # 未读消息摘要（60 分钟 TTL；v5.1.8: 账号级共享缓存——账户级
        # 端点多车共享一份，多车每轮只真拉一次）
        # v5.7.4: TTL 1800→3600——与刷新间隔等长导致缓存永不命中，改后
        # 每两轮命中一次账号级缓存，减少云端请求。
        data["messages"] = await self._cached_fetch(
            "messages", 3600, lambda: self.api.get_messages_summary(),
            force=force_all, account=True,
        )
        # v2.7.8: record the successful poll time for the sensor last_poll
        # attribute (每轮自动刷新/手动刷新成功都会更新).
        self.last_poll = dt_util.now()
        # v5.1.2: 每次轮询（自动/手动）都逐实体写各实体刷新状态活动——
        # 不再只写一条笼统摘要（摘要无实体关联时还会被设备页过滤不可见）。
        # 用户诉求 2026-10-17：每次轮询活动中要显示各实体的刷新状态。
        # _log_refresh_states 跳过 button./image. 与 unavailable/unknown/空
        # 状态实体，每轮为有实际状态的实体写「实体名：状态值」活动。
        try:
            _n = await self._log_refresh_states()
        except Exception:
            self.logger.debug("fordpass_cn logbook entry failed", exc_info=True)
            _n = 0
        # v5.1.4/v5.1.5: 重启/重载后首轮刷新时实体虽已在 entity_registry
        # （持久化，all_entity_ids() 非空）但状态尚未写入（states.get 全
        # None），逐实体活动一条都写不了（只剩摘要）——按「本次实际写入
        # 条数 n==0」判定，调度 45s 后补写一次（届时实体已注册状态就绪，
        # 仅写本地 logbook、不额外拉福特云）。
        if _n == 0 and not self._initial_states_pending:
            self._initial_states_pending = True
            # v5.3.5: 任务引用跟踪（_persist_tasks）——与 v5.3.3 落盘任务同款，
            # 防卸载/重启竞态下 45s 补写任务被事件循环丢弃
            _task = self.hass.async_create_task(self._delayed_initial_states())
            self._persist_tasks.add(_task)
            _task.add_done_callback(self._persist_tasks.discard)
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
                # v6.1.9: 剩余只读端点补全
                "warranty": "质保信息", "capability_v4": "车辆能力清单",
                "vehicle_info": "车辆信息", "fposb_resources": "维保可约资源",
                "evss_freepass_notice": "免密支付通知",
                "wallbox_process_log": "充电过程日志",
                "wallbox_tnc_content": "家充桩分享条款",
            }
            _charge_kinds = ("wallbox_bound", "wallbox_info", "wallbox_records_v2",
                             "evss_order_status", "evss_apps_info", "evss_usage_notice",
                             "vpoi_status", "vpoi_saved",
                             "evss_payment_summary", "evss_invoiceables",
                             "evss_events_v3", "evss_evaluate_url",
                             "wallbox_ble_vehicles",
                             "evss_freepass_notice", "wallbox_process_log",
                             "wallbox_tnc_content")
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
        except Exception:
            self.logger.debug("fordpass_cn probe activity failed", exc_info=True)
        # v5.2.8: 网络/云端不可用恢复提示——与 _log_exception 的一次性警告
        # 配对：失败只警告一次，恢复时提示一次（quality_scale silver 要求）
        if self._update_failure_warned:
            self._update_failure_warned = False
            self.logger.info("FordPass 云端恢复：车辆状态刷新成功")
        # v5.4.7: 云端同步健康打点（后端变更监控——复用轮询结果零额外请求）
        self._last_success_time = dt_util.now()
        self._consecutive_failures = 0
        return data


    def activity_entity_id(self) -> str | None:
        """v5.1.1: 实时查 registry 的"车辆识别码"传感器 entity_id（活动日志关联）。

        unique_id=f"{uid_key}-vehicle_vin"（VIN 实体无条件创建、稳定存在；v5.7.1 起 uid_key 为 sha256 脱敏前缀）。
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
                "sensor", DOMAIN, f"{self.uid_key}-vehicle_vin"
            )
        except Exception:  # noqa: BLE001 - 找不到不阻断活动写入
            return None

    _STATE_ZH: ClassVar[dict[str, str]] = {
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

    def _logbook_throttled(self) -> bool:
        """v5.1.8: logbook 失败节流判定（问题 11 P2 修复）。

        连续失败（3 轮）后暂停活动写入 1 小时——logbook 服务故障时
        「每轮失败重试 + 每条日志」会无限放大，暂停避免日志风暴与无效
        重试；服务恢复后（写入成功一轮）自动解除。正常时恒 False，
        不影响用户「每轮显示各实体刷新状态」诉求。
        """
        return bool(
            self._logbook_hold_until
            and time.monotonic() < self._logbook_hold_until
        )

    def _logbook_fail(self) -> None:
        self._logbook_fail_streak += 1
        if self._logbook_fail_streak >= 3 and (
            not self._logbook_hold_until
            or time.monotonic() >= self._logbook_hold_until
        ):
            self._logbook_hold_until = time.monotonic() + 3600
            self._logbook_fail_streak = 0
            _LOGGER.warning(
                "fordpass_cn logbook 连续失败，暂停活动写入 1 小时"
            )

    def _logbook_ok(self) -> None:
        self._logbook_fail_streak = 0

    async def _log_refresh_states(self) -> int:
        """v4.7.7: 手动刷新完成后逐实体写刷新后的状态值（替代笼统的
        「手动刷新完成」消息）。

        每个实体的活动历史显示「实体名：状态值(单位)」——用户可看到
        各实体刷新后的真实状态（车况未变时亦显示当前值）。button 实体
        state 为最后按下时间戳（无状态含义），跳过。单条失败不影响其余。
        返回实际写入条数（v5.1.5: 首轮 n==0 时调度延迟补写）。
        v5.1.8: 失败节流——整轮 0 成功连续 3 轮则暂停 1 小时（问题 11）。
        """
        if self._logbook_throttled():
            return 0
        _written = 0
        _failed = 0
        # v5.6.10: 隐私实体不写 logbook——VIN（车辆识别码）、车牌号、
        # 车辆定位的状态值会进活动历史（recorder 持久化），按 unique_id
        # 尾缀精确跳过（entity_id 为拼音 slug 不稳定）。registry 查询
        # 为 dict 读取，开销可忽略。
        # v5.7.5: `hass.helpers` 快捷属性在 HA 2026.10 已移除
        # （AttributeError: 'HomeAssistant' object has no attribute
        # 'helpers'），改用标准导入方式（与 973/1111 行一致）。
        from homeassistant.helpers import entity_registry as er

        _registry = er.async_get(self.hass)
        # v5.7.6: 预构建 entity_id→entry 映射（一次遍历），替代循环内
        # 每实体一次 async_get 查询；尾缀常量提升为模块级
        # _PRIVACY_UID_SUFFIXES。
        _reg_entities = _registry.entities
        for eid in self.all_entity_ids():
            if eid.startswith(("button.", "image.")):
                continue  # button=按下时间戳 / image=图片URL，无状态含义
            _ent = _reg_entities.get(eid)
            if _ent is not None and (_ent.unique_id or "").endswith(
                _PRIVACY_UID_SUFFIXES
            ):
                continue  # VIN/车牌/定位属个人信息，不进 logbook
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
                _written += 1
            except Exception:
                _failed += 1
                self.logger.debug("fordpass_cn refresh state logbook failed: %s", eid, exc_info=True)
        if _written > 0 and _failed == 0:
            self._logbook_ok()
        elif _written == 0 and _failed > 0:
            self._logbook_fail()
        return _written

    async def _delayed_initial_states(self) -> None:
        """v5.1.4: 首轮刷新延迟补写逐实体活动（45s 后实体已注册）。

        重启/重载后 async_config_entry_first_refresh 先于实体状态写入，
        逐实体活动写不了（只剩摘要）——本方法在实体注册/状态就绪后
        补写一次，仅写本地 logbook、不拉福特云。
        """
        # v5.1.6: 验证完成，特征日志降级 debug——重启后不再产生
        # system_log warning 噪音（调试时开 debug 日志仍可查）。
        # v5.5.1: 日志去掉硬编码版本号（v5.1.6 起多轮未更新失真，误导排查）
        self.logger.debug("fordpass_cn 首轮补写开始（45s 延迟）")
        try:
            await asyncio.sleep(45)
        finally:
            self._initial_states_pending = False
        if self.data is not None:
            n = await self._log_refresh_states()
            self.logger.debug("fordpass_cn 首轮补写完成，写入 %d 条", n)
        else:
            self.logger.debug("fordpass_cn 首轮补写跳过（data 为空）")

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
            # sensor.py 同款修复）——改用 reg.entities.values() 过滤。
            # P1-7（v5.6.0）：按本 entry 限定，避免多账号/多 entry 时给
            # 对方全部实体写 logbook 活动、事件量成倍放大。
            # v5.8.8 修正：RegistryEntry 的 domain 是**实体域**（sensor /
            # switch / lock …），集成域在 platform 字段——此前写成
            # `e.domain == DOMAIN`（恒不成立，v4.7.6 起列表一直为空，手动
            # 刷新事件的实体关联从未生效），改为 platform==DOMAIN +
            # config_entry_id==本 entry + 未禁用。
            return [
                e.entity_id
                for e in reg.entities.values()
                if e.platform == DOMAIN
                and e.disabled_by is None
                and e.config_entry_id == self.entry_id
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
        v5.1.8: 失败节流——连续 3 次调用全部失败则暂停 1 小时（问题 11）。
        """
        if self._logbook_throttled():
            return
        now = dt_util.now()
        last_at = self._last_logbook_at
        if dedup and not (
            last_at is None
            or (now - last_at).total_seconds() >= 15
            or message != self._last_logbook_msg
        ):
            return
        _ok = False
        _fail = 0
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
                        _ok = True
                    except Exception:
                        _fail += 1
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
                _ok = True
        except Exception:
            self.logger.debug("fordpass_cn logbook entry failed", exc_info=True)
            _fail += 1
        if _ok:
            self._logbook_ok()
        elif _fail > 0:
            self._logbook_fail()
        if dedup:
            self._last_logbook_at = now
            self._last_logbook_msg = message
