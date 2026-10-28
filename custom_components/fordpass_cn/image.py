"""Image platform: vehicle model pictures (侧视车型图 + 俯视车型图).

The pictures (vehicleImageUrl=侧视45view / imageUrl=俯视birdview from
/v5/vehicles/list，实机样本 2026-10-03 实测) are downloaded once and persisted to
``/config/.storage/fordpass_cn/<vin>[.overlook].png`` so they survive restarts
and never need to be re-downloaded (or lost).  v6.0.4: 实体状态显示本实体
昵称（注册表重命名优先），用于区分同车型的多张图片实体——图片本身由前端
通过 image 代理渲染。

v5.2.4: 图片缓存从 /config/www 迁至 /config/.storage 私有目录——www 目录经
/local/ 无认证可下载，任何人知道 VIN 就能拉取车辆图片。.storage 由 HA 私有管理、不对外服务。
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets

import aiohttp
from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
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
    # v5.6.5: 文件名一律用 sha1 摘要（不再以完整 VIN 命名），杜绝 VIN 落盘
    safe = hashlib.sha1(vin.encode("utf-8")).hexdigest()[:16]
    return os.path.join(hass.config.path(".storage"), "fordpass_cn", f"{safe}{suffix}.png")


def _entity_nickname(entity) -> str:
    """v6.0.4: 取实体昵称——注册表用户重命名优先，其次集成给的名称。

    ``Entity.registry_entry`` 是 HA 平台在实体注册后回填的公开属性；用户
    在前端改过的名称（entry.name）优先于集成提供的 _attr_name，这样图片
    实体的状态能跟随用户自己的命名。
    """
    entry = getattr(entity, "registry_entry", None)
    if entry is not None and entry.name:
        return entry.name
    name = getattr(entity, "name", None)
    return name if isinstance(name, str) else ""


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

    # v5.9.6: 图片由 async_image 按需返回、URL 变化才重下，关闭 HA 每 30 秒的
    # 平台默认轮询（轮询不会带来新图，只会反复读同一份缓存）。
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, coordinator: FordPassCoordinator, kind: str) -> None:
        super().__init__(hass)
        self.coordinator = coordinator
        if kind == "overlook":
            self._attr_unique_id = f"{coordinator.uid_key}-vehicle_overlook_image"
            self._attr_name = "俯视车型图"
            self._url_key = "vehicle_overlook_url"
            self._persist_suffix = "_overlook"
        else:
            self._attr_unique_id = f"{coordinator.uid_key}-vehicle_image"
            self._attr_name = "侧视车型图"
            self._url_key = "vehicle_image_url"
            self._persist_suffix = ""
        self._attr_has_entity_name = True
        self._attr_device_info = coordinator.device_info
        self._attr_icon = "mdi:car"
        self._attr_content_type = "image/png"
        self._persist_path = _local_path(hass, coordinator.vin, self._persist_suffix)
        self._image_bytes: bytes | None = None

    @callback
    def async_update_token(self) -> None:
        """只播种一次访问令牌，之后跳过 HA core 每 5 分钟的轮换。

        core 的 image 组件对组件内全部实体无条件 async_update_token() +
        async_write_ha_state()（TOKEN_CHANGE_INTERVAL=5min），且
        ImageEntity.__init__ 也靠本方法创建首个 token——因此不能覆盖成
        纯空操作（空 deque 会让 state_attributes 取 access_tokens[-1]
        时 IndexError，实体创建即坏）。token 是 /api/image_proxy 的防盗
        链参数，只服务于 ``image_url`` 代理取图；本平台图片由
        async_image() 自取（.storage 持久化 + 内存缓存），轮换 token 对
        取图毫无作用，唯一效果是令 attributes 每次都变 → 状态机
        same_attr 不成立 → 每 5 分钟一次 state_changed（token 虽被列为
        不记录属性，recorder 仍落一行状态）→ 每实体每天 288 行纯噪音。
        首次播种后不再轮换：state/attributes 恒等，core 的定时强制写被
        状态机以 state_reported 短路，不再产生任何 recorder 行。
        """
        if not self.access_tokens:
            # core 的 hex(SystemRandom().getrandbits(256))[2:] 等价写法
            self.access_tokens.append(secrets.token_hex(32))

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)  # v5.3.1: 保留最后已知状态——刷新失败不置不可用（与 switch/lock/select/device_tracker 对齐）

    @property
    def state(self) -> str:
        """v6.0.4: 状态显示本实体昵称（此前所有图片实体都返回车型名，无法区分）。"""
        return _entity_nickname(self) or self.coordinator.vehicle_model

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
    按钮同条件；部分车型 null = 车型无远程影像硬件，不创建、0 unavailable）。
    图片 URL 来自按钮查询响应（coordinator.data["remote_image"]），
    响应结构随车型/云端变化，由 extract_remote_image_url 宽松提取；
    有 URL 则下载并持久化到 .storage/fordpass_cn/{vin}_{kind}.png（保留最后
    一张），无 URL 时状态显示「暂无影像」且不丢已下载图片。
    """

    _attr_should_poll = False  # v5.9.6: 同 FordPassVehicleImage——纯推送

    def __init__(self, hass: HomeAssistant, coordinator: FordPassCoordinator, kind: str) -> None:
        super().__init__(hass)
        self.coordinator = coordinator
        self._kind = kind
        self._attr_unique_id = f"{coordinator.uid_key}-{kind}_remote_image"
        self._attr_name = "停车影像图" if kind == "parking" else "行车监控图"
        self._attr_has_entity_name = True
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

    @callback
    def async_update_token(self) -> None:
        """只播种一次令牌、之后不轮换——理由见 FordPassVehicleImage（不走 image_url 代理）。"""
        if not self.access_tokens:
            self.access_tokens.append(secrets.token_hex(32))

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
        """v6.0.4: 状态显示本实体昵称（原为「最新影像/暂无影像」，两张图无法区分）。

        影像有无改由属性 image_status 承载，信息不丢失。
        """
        return _entity_nickname(self) or ("最新影像" if self._image_url() else "暂无影像")

    @property
    def extra_state_attributes(self) -> dict:
        img = self._resp()
        attrs = {
            "image_url": self._image_url() or "",
            "image_status": "最新影像" if self._image_url() else "暂无影像",
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
                    # v5.3.3: 失败也记忆 URL——此前失败不更新 _cached_url，
                    # 前端每次拉图都重试失败的 URL（重复请求）。URL 未变则
                    # 保持旧图；新抓拍 URL 变化才重新拉取。
                    self._cached_url = url
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
            self._cached_url = url
            return self._image_bytes or local

class FordPass3dModelImage(ImageEntity):
    """3D 车型图（v3.5.0）：GET vds/v1/vehicle/search-3d-vehicle-model-url。

    探测到 modelUrl 才创建（非能力车型无数据自动跳过，全车型适配）；
    下载后持久化到 .storage（_3d 后缀），重启不重复拉取；无 URL 时显示
    车型名、保留已有图片（v3.1.2 规则，不 unavailable）。
    """

    _attr_should_poll = False  # v5.9.6: 同 FordPassVehicleImage——纯推送

    def __init__(self, hass: HomeAssistant, coordinator: FordPassCoordinator) -> None:
        super().__init__(hass)
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.uid_key}-vehicle_3d_image"
        self._attr_name = "3D 车型图"
        self._attr_has_entity_name = True
        self._attr_device_info = coordinator.device_info
        self._attr_icon = "mdi:car"
        self._attr_content_type = "image/png"
        self._persist_path = _local_path(hass, coordinator.vin, "_3d")
        self._image_bytes: bytes | None = None

    @callback
    def async_update_token(self) -> None:
        """只播种一次令牌、之后不轮换——理由见 FordPassVehicleImage（不走 image_url 代理）。"""
        if not self.access_tokens:
            self.access_tokens.append(secrets.token_hex(32))

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
        """v6.0.4: 状态显示本实体昵称（此前返回车型名，与其他图片实体重复）。"""
        return _entity_nickname(self) or self.coordinator.vehicle_model

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
    # 才创建（部分车型 null = 无远程影像硬件，0 unavailable；其他车型自动出现）
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
    # async_write_ha_state；失败轮不触发，保留旧值。v5.9.8 起属性不再
    # 含每轮变化的 last_poll，值未变的实体不会产生 state_changed。
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

