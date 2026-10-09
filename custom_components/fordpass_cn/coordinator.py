"""DataUpdateCoordinator for the FordPass China integration."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import FordPassApi, FordPassApiError
from .const import COORDINATE_WGS84, DOMAIN

_LOGGER = logging.getLogger(__name__)


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

    async def _cached_fetch(self, key: str, ttl: float, factory) -> Any:
        """TTL 缓存包装：命中直接返回旧值；失败保留旧值；成功写缓存。

        v3.4.2: ``factory`` 为协程工厂（如 ``lambda: api.get_x(vin)``）——
        只有 TTL 未命中（真正要发请求）时才创建协程并 await，避免
        TTL 命中时产生 "coroutine was never awaited" RuntimeWarning。
        """
        hit, old = self._cache_hit(key)
        if hit:
            return old
        try:
            val = await factory()
        except Exception as exc:  # noqa: BLE001 - best effort，失败不阻塞刷新
            self.logger.debug("FordPass %s fetch failed: %s", key, exc)
            if old is not None:
                return old
            return None
        if val is not None:
            self._cache[key] = (time.monotonic() + ttl, val)
        return val

    async def async_probe_capabilities(self) -> None:
        """探测云端命令白名单 + 新增 GET 端点结构（v3.3.7，尽力而为）。

        setup 阶段调用一次：成功后 command_whitelist 供各平台按命令创建
        实体（全车型自动适配）；失败保持 None，平台不创建新增实体
        （现有实体不受影响）。探测均为只读/幂等，不触发任何车辆动作。
        """
        try:
            self.command_whitelist = await self.api.probe_command_whitelist(self.vin)
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("FordPass capability probe failed: %s", exc)
            self.command_whitelist = None
        try:
            self.extra_probes = await self._cached_fetch(
                "extra_probes", 86400,
                lambda: self.api.probe_extra_endpoints(self.vin),
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("FordPass extra endpoint probe failed: %s", exc)
        # v3.7.6: cevs 探测诊断（预约出发/充电三端点探测结果，供诊断传感器
        # 直接读取——不再依赖导出日志即可从 HA 侧确认探测成败）
        # v3.7.10: 直接引用 api.cevs_diag 同一 dict——probe_cevs_variant 服务
        # 写入后 sensor 立即可见（拷贝会失去同步）
        self.cevs_diag = getattr(self.api, "cevs_diag", {})
        # v3.4.5: 家充桩管理（smartwallbox）端点探测——登录后一次 + 24h TTL
        # 缓存（探测类请求绝不进入常规轮询，防福特云限流）；探测结果供
        # sensor 按"探测到数据才创建"接入。失败/非家充桩车型保持 None。
        self.smartwallbox = await self._cached_fetch(
            "smartwallbox", 86400,
            lambda: self.api.probe_smartwallbox(self.vin),
        )

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
        # Best-effort location（track_location 开启时；LBS 独立网关，
        # 车停着坐标不变 → 15 分钟 TTL）
        if self.track_location:
            loc = await self._cached_fetch(
                "location", 900,
                lambda: self.api.get_location(self.vin, self.coordinate_system),
            )
            if isinstance(loc, dict) and loc.get("lat"):
                data["location"] = loc
            elif (self.data or {}).get("location"):
                # v3.4.3: LBS 偶发失败时保留上一轮有效坐标，
                # 避免 device_tracker / 定位传感器丢位置（保留最后已知状态）
                data["location"] = (self.data or {}).get("location")
            else:
                data["location"] = None
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
        # 服务信息端点（保养计划 12h / 召回 24h / SIM 6h / WiFi 15min TTL）
        for key, ttl, factory in (
            ("maintenance_plan", 43200, lambda: self.api.get_maintenance_plan(self.vin)),
            ("recall", 86400, lambda: self.api.get_recall(self.vin)),
            ("sim_info", 21600, lambda: self.api.get_sim_info(self.vin)),
            ("wifi_status", 900, lambda: self.api.get_wifi_status(self.vin)),
        ):
            data[key] = await self._cached_fetch(key, ttl, factory)
        # 鸣笛设置云端查询（UserPreferenceV2，1 小时 TTL——用户改设置时
        # select 已即时上云+本地写入，此查询仅作初始化同步）
        data["chirp_cloud"] = await self._cached_fetch(
            "chirp_preference", 3600, lambda: self.api.get_chirp_preference()
        )
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
        return data
