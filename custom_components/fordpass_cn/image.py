"""Image platform: vehicle model pictures (侧视车型图 + 俯视车型图).

The pictures (vehicleImageUrl=侧视45view / imageUrl=俯视birdview from
/v5/vehicles/list, HAR 2026-10-03 实测) are downloaded once and persisted to
``/config/.storage/fordpass_cn/<vin>[.overlook].png`` so they survive restarts
and never need to be re-downloaded (or lost).  The entity's state shows the
model name (e.g. 锐际 Escape) for readability — the picture itself is rendered
by the frontend from the image proxy.

v5.2.4: 图片缓存从 /config/www 迁至 /config/.storage 私有目录——www 目录经
/local/ 无认证可下载，任何人知道 VIN 就能拉取车辆图片。.storage 由 HA 私有管理、不对外服务。
"""
from __future__ import annotations

import hashlib
import logging
import os
import re

import aiohttp
from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import extract_remote_image_url
from .coordinator import FordPassCoordinator

_LOGGER = logging.getLogger(__name__)


PARALLEL_UPDATES = 1

def _local_path(hass: HomeAssistant, vin: str, suffix: str = "") -> str:
    """v4.0.14: VIN 白名单校验——VIN 直接拼路径存在路径穿越风险（../../）。

    云端字段异常/被篡改时用 sha1 摘要兜底作文件名（稳定唯一），杜绝把
    用户可控字符写进对外目录路径。
    v5.2.4: 缓存目录由 www/fordpass_cn 改为 .storage/fordpass_cn（私有目录，
    /local/ 无认证对外暴露）；hass.config.path(".storage")
    即 /config/.storage，由 HA 私有管理。
    """
    safe = vin if re.fullmatch(r"[A-Za-z0-9]{6,20}", vin) else hashlib.sha1(vin.encode("utf-8")).hexdigest()[:16]
    return os.path.join(hass.config.path(".storage"), "fordpass_cn", f"{safe}{suffix}.png")


def _read_local(path: str) -> bytes | None:
    """Read the persisted picture from .storage/fordpass_cn (run in executor)."""
    try:
        if os.path.isfile(path):
            with open(path, "rb") as fh:
                return fh.read()
    except OSError as exc:
        _LOGGER.warning("vehicle image local read failed: %s", exc)
    return None


def _persist_sync(path: str, data: bytes) -> None:
    """Write the picture to .storage/fordpass_cn atomically (run in executor)."""
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
        return bool(self.coordinator.data)  # v5.3.1: 保留最后已知状态——刷新失败不置不可用（与 switch/lock/select/device_tracker 对齐）

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
        # v4.9.0: 删除 image_path（本地 /config/www 路径噪音）
        return {
            "vehicle_model": self.coordinator.vehicle_model,
            "image_url": getattr(self.coordinator, self._url_key, None) or "",
        }

    async def async_image(self) -> bytes | None:
        """Return the picture: local file first, then remote URL once.

        Once downloaded the bytes are kept in memory AND written to the
        private .storage folder, so the image is never deleted or re-downloaded
        on restart. File IO runs in the executor to keep the event loop
        responsive.
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
    有 URL 则下载并持久化到 .storage/fordpass_cn/{vin}_{kind}.png（保留最后
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
        # v5.3.2: 记录 _image_bytes/本地文件对应的 URL——新抓拍 URL 变化时
        # 缓存必须失效重拉（此前首次缓存后永不更新，用户点查询按钮后
        # state 显示"最新影像"但前端仍展示第一张图）
        self._cached_url: str | None = None

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)  # v5.3.1: 保留最后已知状态——刷新失败不置不可用（与 switch/lock/select/device_tracker 对齐）

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
        }
        resp = (img or {}).get("resp") if img else None
        if isinstance(resp, dict):
            attrs["captured_at"] = resp.get("captureTime") or resp.get("time") or resp.get("captureDate")
        return attrs

    async def async_image(self) -> bytes | None:
        """Return the picture: cache first; re-fetch when the URL changes.

        v5.3.2: 修复"远程影像永不更新"——此前 _image_bytes / 本地文件首次
        命中后永不失效，用户再点「停车影像/行车监控」查询按钮后 state 显示
        "最新影像"但前端仍展示第一张图。现按 URL 变化失效缓存：新抓拍 URL
        变了 → 重拉新图并覆盖本地文件；新 URL 拉取失败 → 回退旧图（保留
        最后已知图片，实体不 unavailable）。
        """
        url = self._image_url()  # 同步更新 self._last_url
        if self._image_bytes is not None and self._last_url == self._cached_url:
            return self._image_bytes
        local = await self.hass.async_add_executor_job(_read_local, self._persist_path)
        if local and self._last_url == self._cached_url:
            # URL 未变：本地缓存直接用（重启后首读）
            self._image_bytes = local
            return local
        if not url:
            # 无 URL：回退旧图（内存或本地文件）
            if self._image_bytes is not None:
                return self._image_bytes
            if local:
                self._image_bytes = local
            return local
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=20),
                headers={"user-agent": "Mozilla/5.0 (FordPass HA integration)"},
            ) as resp:
                if resp.status != 200:
                    _LOGGER.warning("remote image fetch failed: HTTP %s", resp.status)
                    return self._image_bytes or local
                data = await resp.read()
                if data:
                    self._image_bytes = data
                    self._cached_url = url
                    await self.hass.async_add_executor_job(
                        _persist_sync, self._persist_path, data
                    )
                return data or self._image_bytes or local
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("remote image fetch error: %s", exc)
            return self._image_bytes or local

class FordPass3dModelImage(ImageEntity):
    """3D 车型图（v3.5.0）：GET vds/v1/vehicle/search-3d-vehicle-model-url。

    探测到 modelUrl 才创建（非能力车型无数据自动跳过，全车型适配）；
    下载后持久化到 .storage（_3d 后缀），重启不重复拉取；无 URL 时显示
    车型名、保留已有图片（v3.1.2 规则，不 unavailable）。
    """

    def __init__(self, hass: HomeAssistant, coordinator: FordPassCoordinator) -> None:
        super().__init__(hass)
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.vin}-vehicle_3d_image"
        self._attr_name = "3D 车型图"
        self._attr_has_entity_name = False
        self._attr_device_info = coordinator.device_info
        self._attr_icon = "mdi:car"
        self._attr_content_type = "image/png"
        self._persist_path = _local_path(hass, coordinator.vin, "_3d")
        self._image_bytes: bytes | None = None

    def _model_url(self) -> str | None:
        ep = self.coordinator.extra_probes or {}
        data = ep.get("3d_model")
        if not isinstance(data, dict):
            return None
        for d in (data, data.get("data")):
            if not isinstance(d, dict):
                continue
            for k in ("modelUrl", "url", "threeDModelUrl", "threeDModelURL"):
                v = d.get(k)
                if v:
                    return str(v)
        return None

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)  # v5.3.1: 保留最后已知状态——刷新失败不置不可用（与 switch/lock/select/device_tracker 对齐）

    @property
    def state(self) -> str:
        return self.coordinator.vehicle_model

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        # v4.9.0: 删除 image_path（本地 /config/www 路径噪音）
        return {
            "vehicle_model": self.coordinator.vehicle_model,
            "image_url": self._model_url() or "",
        }

    async def async_image(self) -> bytes | None:
        if self._image_bytes:
            return self._image_bytes
        local = await self.hass.async_add_executor_job(_read_local, self._persist_path)
        if local:
            self._image_bytes = local
            return local
        url = self._model_url()
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
                    _LOGGER.warning("3d model image fetch failed: HTTP %s", resp.status)
                    return None
                data = await resp.read()
                if data:
                    self._image_bytes = data
                    await self.hass.async_add_executor_job(
                        _persist_sync, self._persist_path, data
                    )
                return data or None
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("3d model image fetch error: %s", exc)
            return None


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    # v3.1.6: 多 VIN——每辆车独立创建图片实体（有车辆图片 URL 才创建）
    # v3.3.9: 侧视（vehicleImageUrl）+ 俯视（imageUrl）分别创建，互不影响
    payload = entry.runtime_data  # v5.3.0: runtime_data 替代 hass.data
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
    # v3.5.0: 3D 车型图——探测到 modelUrl 才创建（全车型自动适配）
    entities = entities + [
        FordPass3dModelImage(hass, coordinator)
        for coordinator in coordinators
        if isinstance((coordinator.extra_probes or {}).get("3d_model"), dict)
    ]
    if entities:
        async_add_entities(entities)
    # v4.8.5: 实体订阅协调器刷新广播——每次刷新成功（即使车况未变）
    # async_write_ha_state，attrs 中 last_poll 每轮变化 → state_changed →
    # 实体 last_updated 每轮刷新（撤销"实体不随轮询刷新"）；失败轮不触发，
    # 保留旧值。
    try:

        def _mk_refresh(ent):
            def _refresh():
                # v4.8.6: hass 注入前（实体添加阶段）回调会被预热刷新触发，
                # 直接 async_write_ha_state 会 RuntimeError: hass is None
                if ent.hass is not None:
                    ent.async_write_ha_state()
            return _refresh

        for _ent in entities:
            _ent.async_on_remove(
                _ent.coordinator.async_add_listener(_mk_refresh(_ent))
            )
    except Exception:
        _LOGGER.debug("entity listener registration failed", exc_info=True)

