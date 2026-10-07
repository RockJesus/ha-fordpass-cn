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
        self.coordinate_system = coordinate_system
        # 最近一次成功拉取的时间（v2.7.8），暴露为传感器 last_poll 属性
        self.last_poll: datetime | None = None
        self._vehicle_name = vehicle_name or f"Ford {vin[-6:]}"
        # active_alerts 失败降级：连续失败 2 次后 1 小时内不再请求，
        # 避免接口 404 时每轮都发无效请求并刷日志噪音（v2.7.5）。
        self._alerts_fail = 0
        self._alerts_hold_until: float | None = None
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

        # Best-effort location (only when the user enabled tracking; the LBS
        # gateway is a separate APIM endpoint and costs a remote round trip).
        if self.track_location:
            try:
                loc = await self.api.get_location(self.vin, self.coordinate_system)
                if isinstance(loc, dict) and loc.get("lat"):
                    data["location"] = loc
                else:
                    data["location"] = None
            except Exception as exc:  # noqa: BLE001
                self.logger.debug("FordPass location fetch failed: %s", exc)
                data["location"] = None

        # Vehicle health alerts (plaintext Chinese headlines) — best effort.
        # 失败降级：连续失败 2 次 → 抑制 1 小时再试（v2.7.5）。
        now = time.monotonic()
        if self._alerts_hold_until is None or now >= self._alerts_hold_until:
            try:
                alerts = await self.api.get_active_alerts(self.vin)
                data["active_alerts"] = alerts or []
                self._alerts_fail = 0
                self._alerts_hold_until = None
            except Exception as exc:  # noqa: BLE001
                self.logger.debug("FordPass active alerts fetch failed: %s", exc)
                data["active_alerts"] = []
                self._alerts_fail += 1
                if self._alerts_fail >= 2:
                    self._alerts_hold_until = now + 3600
                    self._alerts_fail = 0
                    self.logger.info(
                        "FordPass active alerts failing; suppressing retries for 1h"
                    )
        else:
            data["active_alerts"] = []
        # v3.1.3: OTA 设置状态（尽力而为，与 alerts 同模式——失败不阻塞刷新）
        try:
            ota = await self.api.get_ota_setting(self.vin)
            data["ota_setting"] = ota or None
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("FordPass OTA setting fetch failed: %s", exc)
            data["ota_setting"] = None
        # v3.1.4: 服务信息端点（保养计划/召回/SIM/WiFi；warranty 100502 暂不拉取）
        for key, coro in (
            ("maintenance_plan", self.api.get_maintenance_plan(self.vin)),
            ("recall", self.api.get_recall(self.vin)),
            ("sim_info", self.api.get_sim_info(self.vin)),
            ("wifi_status", self.api.get_wifi_status(self.vin)),
        ):
            try:
                data[key] = await coro
            except Exception as exc:  # noqa: BLE001
                self.logger.debug("FordPass %s fetch failed: %s", key, exc)
                data[key] = None
        # v3.1.4: 鸣笛设置云端查询（UserPreferenceV2，尽力而为）
        try:
            data["chirp_cloud"] = await self.api.get_chirp_preference()
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("FordPass chirp preference fetch failed: %s", exc)
            data["chirp_cloud"] = None
        # v3.1.7: 空调滤芯状态（GET /api/cnxapi-vds/v1/aar/status，尽力而为；
        # 无 AAR 能力/接口失败的车型 data 置 None——实体不创建，不显示不可用）
        try:
            data["air_filter"] = await self.api.get_air_filter_status(self.vin)
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("FordPass air filter status fetch failed: %s", exc)
            data["air_filter"] = None
        # v3.1.9: 云端能力+服务信息（ccfeatures）与预测性诊断（prognostic），
        # 均 200 实测；失败置 None——实体不创建，不显示不可用
        for key, coro in (
            ("ccfeatures", self.api.get_ccfeatures(self.vin)),
            ("prognostic", self.api.get_prognostic(self.vin)),
        ):
            try:
                data[key] = await coro
            except Exception as exc:  # noqa: BLE001
                self.logger.debug("FordPass %s fetch failed: %s", key, exc)
                data[key] = None
        # v3.1.17: 未读消息摘要（HAR 实测 200；无 encryptedVin，失败置 None——
        # 实体不创建，不显示不可用）
        try:
            data["messages"] = await self.api.get_messages_summary()
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("FordPass messages summary fetch failed: %s", exc)
            data["messages"] = None
        # v2.7.8: record the successful poll time for the sensor last_poll
        # attribute (每轮自动刷新/手动刷新成功都会更新).
        self.last_poll = datetime.now()
        return data
