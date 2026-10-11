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
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import FordPassApi, FordPassApiError, FordPassProbeRejected
from .const import COORDINATE_WGS84, DOMAIN, PROBE_PERSIST_TTL

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
            "出行：" + ("已泊车" if "NotAvailable" in s else ("外出中" if ("Available" in s or s == "不可用") else s))
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
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-{vin[-6:]}",
            update_interval=timedelta(minutes=interval),
        )
        self.api = api
        self.vin = vin
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
        self._last_logbook_msg: str | None = None
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
        self, key: str, ttl: float, factory, persist: bool = False
    ) -> Any:
        """TTL 缓存包装：命中直接返回旧值；失败保留旧值；成功写缓存。

        v3.4.2: ``factory`` 为协程工厂（如 ``lambda: api.get_x(vin)``）——
        只有 TTL 未命中（真正要发请求）时才创建协程并 await，避免
        TTL 命中时产生 "coroutine was never awaited" RuntimeWarning。
        v3.8.6: ``persist=True`` 时三级缓存——内存 TTL → HA Store 持久化
        TTL（重启/重载后 setup 复用，不再重探）→ factory → 写内存+落盘。
        探测类请求（白名单/额外端点/cevs/家充桩）一律 persist=True；
        常规轮询数据保持内存缓存（避免高频写盘）。
        """
        hit, old = self._cache_hit(key)
        if hit:
            return old
        if persist and self._probe_cache:
            ent = self._probe_cache.get(key)
            if ent and time.time() < ent.get("expire", 0):
                # 持久化命中：回填内存缓存（防止同轮重复读盘）
                self._cache[key] = (time.monotonic() + ttl, ent["value"])
                return ent["value"]
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

    async def async_probe_capabilities(self) -> None:
        """探测云端命令白名单 + 新增 GET 端点结构（v3.3.7，尽力而为）。

        setup 阶段调用一次：成功后 command_whitelist 供各平台按命令创建
        实体（全车型自动适配）；失败保持 None，平台不创建新增实体
        （现有实体不受影响）。探测均为只读/幂等，不触发任何车辆动作。
        v3.8.6: 探测结果持久化——重启/重载后 setup 先读 Store，TTL 内
        直接复用（不再重探制造噪音）；TTL 过期或首次才真正探测。
        """
        await self._load_probe_cache()
        try:
            self.command_whitelist = await self._cached_fetch(
                "whitelist", PROBE_PERSIST_TTL,
                lambda: self.api.probe_command_whitelist(self.vin),
                persist=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("FordPass capability probe failed: %s", exc)
            self.command_whitelist = None
        try:
            self.extra_probes = await self._cached_fetch(
                "extra_probes", PROBE_PERSIST_TTL,
                lambda: self.api.probe_extra_endpoints(self.vin),
                persist=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("FordPass extra endpoint probe failed: %s", exc)
        # v3.7.6: cevs 探测诊断（预约出发/充电三端点探测结果，供诊断传感器
        # 直接读取——不再依赖导出日志即可从 HA 侧确认探测成败）
        # v3.7.10: 直接引用 api.cevs_diag 同一 dict——probe_cevs_variant 服务
        # 写入后 sensor 立即可见（拷贝会失去同步）
        # v3.7.18: cevs 自动探测独立 try（probe_cevs_auto）——此前嵌在
        # extra_probes 探测内部，前段失败即中断、diag 不创建
        # v3.8.5: 按车型跳过探测——纯油(G)/柴油(D)车 cevs 三端点（预约出发/
        # 充电/命令状态）业务恒空且不创建实体（v3.8.2 创建规则），探测白打
        # 请求并制造 Impossible modulus 噪音；与创建规则对称：fuelType ∈
        # 电/插混/混动才探测。
        # v3.8.6: 探测结果持久化（probe+diag 一并缓存）——reload 后 setup
        # TTL 内复用，不重探。
        fuel = str(self.vehicle_info.get("fuelType") or "").upper()
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
        self.smartwallbox = await self._cached_fetch(
            "smartwallbox", PROBE_PERSIST_TTL,
            lambda: self.api.probe_smartwallbox(self.vin),
            persist=True,
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
                    persist=True,
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
                    persist=True,
                )
                or data.get("remote_climate_cloud") or {}
            )
            self.async_set_updated_data(data)
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("FordPass remote climate pref init failed: %s", exc)

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

    async def force_refresh(self) -> None:
        """Fetch data immediately and push it to entities (v2.7.0).

        HA's ``async_request_refresh`` only executes right away when the last
        poll is older than ``update_interval``; otherwise it re-schedules the
        refresh to the next interval tick, which makes a manual "刷新车辆状态"
        button appear dead.  This method bypasses that throttle: it fetches
        now and pushes the result with ``async_set_updated_data``.
        """
        try:
            data = await self._async_update_data()
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

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            status = await self.api.get_vehicle_status(self.vin)
            data: dict[str, Any] = {
                "status": status,
                "vehiclestatus": status.get("vehiclestatus", status),
            }
        except FordPassApiError as err:
            raise UpdateFailed(f"FordPass update failed: {err}") from err
        except asyncio.TimeoutError as err:
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
            "active_alerts", 1800, lambda: self.api.get_active_alerts(self.vin)
        )
        data["active_alerts"] = alerts if isinstance(alerts, list) else []
        # OTA 设置状态（6 小时 TTL——OTA 排程配置几乎不变）
        data["ota_setting"] = await self._cached_fetch(
            "ota_setting", 21600, lambda: self.api.get_ota_setting(self.vin)
        )
        # 服务信息端点（保养计划 12h / 召回 24h / SIM 6h TTL；WiFi 状态
        # v3.8.8 起跟随状态刷新间隔每轮刷新，不再固定 15 分钟）
        for key, ttl, factory in (
            ("maintenance_plan", 43200, lambda: self.api.get_maintenance_plan(self.vin)),
            ("recall", 86400, lambda: self.api.get_recall(self.vin)),
            ("sim_info", 21600, lambda: self.api.get_sim_info(self.vin)),
        ):
            data[key] = await self._cached_fetch(key, ttl, factory)
        # 车载 WiFi 状态（v3.8.8: 跟随状态刷新间隔每轮刷新；失败保留旧值）
        try:
            data["wifi_status"] = await self.api.get_wifi_status(self.vin)
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
            "air_filter", 21600, lambda: self.api.get_air_filter_status(self.vin)
        )
        # 云端能力+服务信息（ccfeatures 24h TTL——出厂能力几乎永不变）
        # 与预测性诊断（prognostic 6h TTL——机油寿命/剩余里程变化慢）
        for key, ttl, factory in (
            ("ccfeatures", 86400, lambda: self.api.get_ccfeatures(self.vin)),
            ("prognostic", 21600, lambda: self.api.get_prognostic(self.vin)),
        ):
            data[key] = await self._cached_fetch(key, ttl, factory)
        # 未读消息摘要（30 分钟 TTL）
        data["messages"] = await self._cached_fetch(
            "messages", 1800, lambda: self.api.get_messages_summary()
        )
        # v2.7.8: record the successful poll time for the sensor last_poll
        # attribute (每轮自动刷新/手动刷新成功都会更新).
        self.last_poll = datetime.now()
        # v4.0.3: 活动日志 15s 去重——并发刷新（手动按钮立即 force_refresh 与
        # 自动轮询撞车 / 命令完成后二次拉取）会在几秒内写两条相同摘要，既刷屏
        # 又无意义。同摘要且距上次写入 <15s 时跳过；轮询间隔 ≥5 分钟，正常
        # 记录不受影响，每条轮询仍按间隔显示。
        try:
            summary = _status_summary(data)
            now = datetime.now()
            last_at = self._last_logbook_at
            if (
                last_at is None
                or (now - last_at).total_seconds() >= 15
                or summary != self._last_logbook_msg
            ):
                # v4.0.13: 由 async_create_task 改为直接 await——此前 task 内部
                # async_call 的异常无人捕获（外层 try 只包创建动作），logbook
                # 服务调用失败会留下"Task exception was never retrieved"噪音；
                # await 后异常落进下方 except，刷新流程不受影响。
                # 每轮轮询仍写入（用户诉求：活动日志可见每次轮询状态摘要），
                # 15s 去重仅防手动刷新与自动轮询撞车的重复条目。
                try:
                    await self.hass.services.async_call(
                        "logbook",
                        "log",
                        {
                            "name": self._vehicle_name or "福特派互联",
                            "message": summary,
                            "domain": DOMAIN,
                        },
                    )
                except Exception:  # noqa: BLE001 - logbook 失败不影响数据刷新
                    self.logger.debug("fordpass_cn logbook entry failed", exc_info=True)
                self._last_logbook_at = now
                self._last_logbook_msg = summary
        except Exception:  # noqa: BLE001 - logbook 失败不影响数据刷新
            self.logger.debug("fordpass_cn logbook entry failed", exc_info=True)
        return data
