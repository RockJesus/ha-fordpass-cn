"""Image platform: vehicle model pictures (侧视车型图 + 俯视车型图).

The pictures (vehicleImageUrl=侧视45view / imageUrl=俯视birdview from
/v5/vehicles/list, HAR 2026-10-03 实测) are downloaded once and persisted to
``/config/www/fordpass_cn/<vin>[.overlook].png`` so they survive restarts and
never need to be re-downloaded (or lost).  The entity's state shows the model
name (e.g. 锐际 Escape) for readability — the picture itself is rendered by the
frontend from the image proxy.
"""
from __future__ import annotations

import logging
import os

import aiohttp

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .api import extract_remote_image_url
from .coordinator import FordPassCoordinator

_LOGGER = logging.getLogger(__name__)


def _local_path(hass: HomeAssistant, vin: str, suffix: str = "") -> str:
    return os.path.join(hass.config.path("www"), "fordpass_cn", f"{vin}{suffix}.png")


def _read_local(path: str) -> bytes | None:
    """Read the persisted picture from www/fordpass_cn (run in executor)."""
    try:
        if os.path.isfile(path):
            with open(path, "rb") as fh:
                return fh.read()
    except OSError as exc:
        _LOGGER.warning("vehicle image local read failed: %s", exc)
    return None


def _persist_sync(path: str, data: bytes) -> None:
    """Write the picture to www/fordpass_cn atomically (run in executor)."""
    try:
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except OSError as exc:
        _LOGGER.warning("vehicle image persist failed: %s", exc)


class FordPassVehicleImage(ImageEntity):
    """Vehicle model picture, persisted locally and never deleted.

    v3.3.9: kind 参数化——"side"=侧视车型图（vehicleImageUrl，原名车辆图片，
    unique_id 保持 {vin}-vehicle_image 不丢实体）；"overlook"=俯视车型图
    （imageUrl，unique_id {vin}-vehicle_overlook_image）。
    """

    def __init__(self, hass: HomeAssistant, coordinator: FordPassCoordinator, kind: str) -> None:
        super().__init__(hass)
        self.coordinator = coordinator
        if kind == "overlook":
            self._attr_unique_id = f"{coordinator.vin}-vehicle_overlook_image"
            self._attr_name = "俯视车型图"
            self._url_key = "vehicle_overlook_url"
            self._persist_suffix = "_overlook"
        else:
            self._attr_unique_id = f"{coordinator.vin}-vehicle_image"
            self._attr_name = "侧视车型图"
            self._url_key = "vehicle_image_url"
            self._persist_suffix = ""
        self._attr_has_entity_name = False
        self._attr_device_info = coordinator.device_info
        self._attr_icon = "mdi:car"
        self._attr_content_type = "image/png"
        self._persist_path = _local_path(hass, coordinator.vin, self._persist_suffix)
        self._image_bytes: bytes | None = None

    @property
    def available(self) -> bool:
        return True  # v3.1.2: 图片实体恒可用（无 URL 时保留已下载图片）

    @property
    def state(self) -> str:
        """Show the model name (e.g. 锐际 Escape) instead of an empty value.

        HA image entities normally have no textual state; returning the model
        name makes the card readable while the picture still renders from the
        image proxy.
        """
        return self.coordinator.vehicle_model

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return {
            "vehicle_model": self.coordinator.vehicle_model,
            "image_url": getattr(self.coordinator, self._url_key, None) or "",
            "image_path": self._persist_path,
        }

    async def async_image(self) -> bytes | None:
        """Return the picture: local file first, then remote URL once.

        Once downloaded the bytes are kept in memory AND written to the www
        folder, so the image is never deleted or re-downloaded on restart.
        File IO runs in the executor to keep the event loop responsive.
        """
        if self._image_bytes:
            return self._image_bytes
        local = await self.hass.async_add_executor_job(_read_local, self._persist_path)
        if local:
            self._image_bytes = local
            return local
        url = getattr(self.coordinator, self._url_key, None)
        if not url:
            return None
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=20),
                headers={"user-agent": "Mozilla/5.0 (FordPass HA integration)"},
            ) as resp:
                if resp.status != 200:
                    _LOGGER.warning("vehicle image fetch failed: HTTP %s", resp.status)
                    return None
                data = await resp.read()
                if data:
                    self._image_bytes = data
                    await self.hass.async_add_executor_job(
                        _persist_sync, self._persist_path, data
                    )
                return data or None
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("vehicle image fetch error: %s", exc)
            return None



class FordPassRemoteImageEntity(ImageEntity):
    """远程影像图（停车影像 / 行车监控，v3.4.4，全车型）。

    仅当车辆列表 encryptedCarId 非空时创建（与「停车影像/行车监控」查询
    按钮同条件；锐际 null = 车型无远程影像硬件，不创建、0 unavailable）。
    图片 URL 来自按钮查询响应（coordinator.data["remote_image"]），
    响应结构随车型/云端变化，由 extract_remote_image_url 宽松提取；
    有 URL 则下载并持久化到 www/fordpass_cn/{vin}_{kind}.png（保留最后
    一张），无 URL 时状态显示「暂无影像」且不丢已下载图片。
    """

    def __init__(self, hass: HomeAssistant, coordinator: FordPassCoordinator, kind: str) -> None:
        super().__init__(hass)
        self.coordinator = coordinator
        self._kind = kind
        self._attr_unique_id = f"{coordinator.vin}-{kind}_remote_image"
        self._attr_name = "停车影像图" if kind == "parking" else "行车监控图"
        self._attr_has_entity_name = False
        self._attr_device_info = coordinator.device_info
        self._attr_icon = "mdi:car-multiple" if kind == "parking" else "mdi:cctv"
        self._attr_content_type = "image/png"
        self._persist_path = _local_path(hass, coordinator.vin, f"_{kind}")
        self._image_bytes: bytes | None = None
        self._last_url: str | None = None

    @property
    def available(self) -> bool:
        return True  # v3.1.2: 图片实体恒可用（无 URL 时保留已下载图片）

    def _resp(self):
        data = self.coordinator.data or {}
        img = data.get("remote_image") or {}
        if isinstance(img, dict) and img.get("kind") == self._kind:
            return img
        return None

    def _image_url(self) -> str | None:
        img = self._resp()
        if img:
            url = img.get("image_url")
            if url:
                self._last_url = url
                return url
            hit = extract_remote_image_url(img.get("resp"))
            if hit:
                self._last_url = hit
                return hit
        return self._last_url

    @property
    def state(self) -> str:
        """图片可用性提示：有 URL 显示「最新影像」，否则「暂无影像」。"""
        return "最新影像" if self._image_url() else "暂无影像"

    @property
    def extra_state_attributes(self) -> dict:
        img = self._resp()
        attrs = {
            "image_url": self._image_url() or "",
            "kind": self._kind,
            "car_id": (img or {}).get("car_id") or getattr(self.coordinator, "car_id", None),
            "image_path": self._persist_path,
        }
        resp = (img or {}).get("resp") if img else None
        if isinstance(resp, dict):
            attrs["captured_at"] = resp.get("captureTime") or resp.get("time") or resp.get("captureDate")
        return attrs

    async def async_image(self) -> bytes | None:
        """Return the picture: local file first, then remote URL once."""
        if self._image_bytes:
            return self._image_bytes
        local = await self.hass.async_add_executor_job(_read_local, self._persist_path)
        if local:
            self._image_bytes = local
            return local
        url = self._image_url()
        if not url:
            return None
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=20),
                headers={"user-agent": "Mozilla/5.0 (FordPass HA integration)"},
            ) as resp:
                if resp.status != 200:
                    _LOGGER.warning("remote image fetch failed: HTTP %s", resp.status)
                    return None
                data = await resp.read()
                if data:
                    self._image_bytes = data
                    await self.hass.async_add_executor_job(
                        _persist_sync, self._persist_path, data
                    )
                return data or None
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("remote image fetch error: %s", exc)
            return None

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    # v3.1.6: 多 VIN——每辆车独立创建图片实体（有车辆图片 URL 才创建）
    # v3.3.9: 侧视（vehicleImageUrl）+ 俯视（imageUrl）分别创建，互不影响
    payload = hass.data[DOMAIN][entry.entry_id]
    coordinators = payload.get("coordinators") or [payload["coordinator"]]
    entities = [
        FordPassVehicleImage(hass, coordinator, kind)
        for coordinator in coordinators
        for kind, attr in (("side", "vehicle_image_url"), ("overlook", "vehicle_overlook_url"))
        if getattr(coordinator, attr, None)
    ]
    # v3.4.4: 远程影像图（停车影像/行车监控）——车辆列表 encryptedCarId 非空
    # 才创建（锐际 null = 无远程影像硬件，0 unavailable；其他车型自动出现）
    remote_entities = [
        FordPassRemoteImageEntity(hass, coordinator, kind)
        for coordinator in coordinators
        for kind in ("parking", "traffic")
        if getattr(coordinator, "car_id", None)
    ]
    entities = entities + remote_entities
    if entities:
        async_add_entities(entities)
