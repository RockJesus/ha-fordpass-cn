"""API client for FordPass China (cn.api.mps.ford.com.cn).

Protocol aligned with the cloud gateway's client behaviour and verified
against a live capture:

* every request carries fixed headers (application-id / appversion / ...);
* sensitive fields (phone, passcode, VIN, response bodies) go through a
  custom symmetric cipher implemented in pure Python (see wbsk.py);
* a `sign` (SHA-256 over a canonical parameter string, wrapped with two fixed
  secrets) is attached to every request;
* after login, an `auth-token` header (JWT) is attached to every request.
"""
from __future__ import annotations

import asyncio
import base64
import datetime
import hashlib
import json
import math
import re
import time
import urllib.parse
import uuid
from typing import Any

import aiohttp

from .const import (
    APPLICATION_ID,
    B2C_AUTHORITY,
    B2C_CLIENT_ID,
    B2C_PATH_AUTHORIZE,
    B2C_PATH_CONFIRMED,
    B2C_PATH_SELF_ASSERTED,
    B2C_POLICY,
    B2C_REDIRECT_URI,
    BRAND_FORD,
    BRAND_PROFILE,
    BASE_URL,
    CHIRP_TO_ANNOUNCE,
    CHIRP_TYPE_OPTIONS,
    DEFAULT_CHIRP_TYPE,
    DEFAULT_HONK_DURATION,
    GROUP_REMOTE_CLIMATE,
    GROUP_VEHICLE_ANNOUNCEMENT,
    LBS_APP_ID,
    LBS_APP_KEY,
    LBS_BASE_URL,
    LBS_PAYLOAD_KEY,
    OS_TYPE,
    OS_VERSION,
    PATH_3D_MODEL,
    PATH_AAR_STATUS,
    PATH_ACTIVE_ALERT,
    PATH_ACTIVE_ALERT_LIST,
    PATH_APP_INIT_SETTINGS,
    PATH_B2C_TOKEN,
    PATH_CCFEATURES,
    PATH_CEVS_COMMAND_STATUS,
    PATH_CHARGELOGS_DELETE,
    PATH_CHARGELOGS_RETRIEVE,
    PATH_CMCC_TRAFFIC,
    PATH_COMMAND_STATUS,
    PATH_CVFEATURES,
    PATH_DEPARTURE_DELETE_ALL,
    PATH_DEPARTURE_SAVE,
    PATH_DEPARTURE_TIMES_RETRIEVE,
    PATH_DEPARTURE_TOGGLE_OFF,
    PATH_DEPARTURE_TOGGLE_ON,
    PATH_DEVICE_LIST,
    PATH_EVSS_ACCOUNTS,
    PATH_EVSS_APPS_INFO,
    PATH_EVSS_CHARGER_INFO,
    PATH_EVSS_CHARGER_LOCK,
    PATH_EVSS_EVALUATE_URL,
    PATH_EVSS_EVENTS_V3,
    PATH_EVSS_FREEPASS_STATUS,
    PATH_EVSS_INVOICEABLES,
    PATH_EVSS_ORDER_EVENTS,
    PATH_EVSS_ORDER_STATUS,
    PATH_EVSS_ORDERS,
    PATH_EVSS_PARKING,
    PATH_EVSS_PAYMENT_SUMMARY,
    PATH_EVSS_FREEPASS_NOTICE,
    PATH_EVSS_STATIONS,
    PATH_EVSS_USAGE_NOTICE,
    PATH_FPOSB_AVAILABLE_RESOURCE,
    PATH_FPOSB_BOOKING_DETAIL,
    PATH_FPOSB_CANCEL,
    PATH_FPOSB_HISTORY,
    PATH_FPOSB_ORDER,
    PATH_FPOSB_PUD_DETAIL,
    PATH_FPOSB_SA_INFO,
    PATH_GENERATE_PASSCODE,
    PATH_LINCOLN_MEMBER_INFO,
    PATH_LINCOLN_MY_PAGE,
    PATH_LWOSB_BOOKINGS,
    PATH_LWOSB_ORDER_STATUS,
    PATH_MAINTENANCE_HISTORY,
    PATH_MAINTENANCE_PLAN,
    PATH_MCM_MESSAGES_V3,
    PATH_MESSAGES_PAGE,
    PATH_MESSAGES_READ,
    PATH_MESSAGES_SUMMARY,
    PATH_MESSAGES_V2_PAGE,
    PATH_MESSAGES_V2_READBYID,
    PATH_MONITOR_TRAFFIC,
    PATH_ONLINENR,
    PATH_OTA_AGREEMENTS,
    PATH_OTA_DETAIL,
    PATH_OTA_NEW_STATUS,
    PATH_OTA_REDDOT,
    PATH_OTA_SEARCH_DETAILS,
    PATH_OTA_SETTING,
    PATH_OTA_VERSIONS,
    PATH_PARKING_IMAGE,
    PATH_PASSCODE_LOGIN,
    PATH_PDS_DEVICE,
    PATH_PROGNOSTIC,
    PATH_QUERY_LOCATION,
    PATH_RECALL,
    PATH_REFRESH_TOKEN,
    PATH_REVOKE_TOKEN,
    PATH_SCHEDULE_DEPARTURE,
    PATH_SEND_COMMAND,
    PATH_SENSOR_SHADOW,
    PATH_SHARE_CAPABILITY,
    PATH_SHARE_HISTORY,
    PATH_SHARE_LIST,
    PATH_SIM_INFO,
    PATH_SMARTWALLBOX_AUTHORITY,
    PATH_SMARTWALLBOX_BINDING,
    PATH_SMARTWALLBOX_BLE_ADD,
    PATH_SMARTWALLBOX_BOUND,
    PATH_SMARTWALLBOX_CHARGE_START,
    PATH_SMARTWALLBOX_CHARGE_STOP,
    PATH_SMARTWALLBOX_CHG_STATUS_WB,
    PATH_SMARTWALLBOX_CHG_WB,
    PATH_SMARTWALLBOX_CONFIG,
    PATH_SMARTWALLBOX_INFO,
    PATH_SMARTWALLBOX_ORDERS,
    PATH_SMARTWALLBOX_PROCESS_LOG,
    PATH_SMARTWALLBOX_RECORDS,
    PATH_SMARTWALLBOX_RECORDS_V2,
    PATH_SMARTWALLBOX_SHARING_QUERY,
    PATH_SMARTWALLBOX_UNBIND,
    PATH_SRS_ADD,
    PATH_SRS_PROFILE,
    PATH_SRS_UPDATE,
    PATH_THIRD_PARTY_TOKEN,
    PATH_USER_AUTH_STATUS,
    PATH_USER_LOGIN_STATUS,
    PATH_USER_PREF_GROUPS,
    PATH_USER_PREF_LIST,
    PATH_USER_REDDOT,
    PATH_USER_VEHICLES,
    PATH_V5_ANNOUNCE_STATUS,
    PATH_V5_DOOR_LOCK,
    PATH_V5_DOOR_LOCK_STATUS,
    PATH_V5_DOORS_LOCK,
    PATH_V5_ENGINE_START,
    PATH_V5_HONK,
    PATH_V5_PANIC,
    PATH_V5_STATUS,
    PATH_V5_STATUSREFRESH,
    PATH_VEHICLE_INFO,
    PATH_VEHICLE_MODEL_MASTERDATA,
    PATH_VEHICLE_STATUS,
    PATH_VEHICLE_USER_AUTH,
    PATH_VEHICLE_USER_AUTH_APPROVAL,
    PATH_VEHICLES_LIST,
    PATH_VIDEO_FILE,
    PATH_VIDEO_START,
    PATH_VIDEO_STOP,
    PATH_VISITOR_MODE,
    PATH_VPOI_CHARGESTATIONS,
    PATH_VPOI_CHARGESTATIONS_V4,
    PATH_VPOI_SAVED,
    PATH_VPOI_SAVED_V4,
    PATH_VPOI_STATUS,
    PATH_WALLBOX_BLE_VEHICLES,
    PATH_WALLBOX_SHARE,
    PATH_WALLBOX_SHARER_INFO,
    PATH_WALLBOX_TNC_CONTENT,
    PATH_WALLBOX_TNC_SIGN,
    PATH_WALLBOX_UNSHARE,
    PATH_WARRANTY,
    PATH_WIFI_STATUS,
    PAYLOAD_KEY,
    PREF_DURATION,
    PREF_SOUND_TYPE,
    PREF_TARGET_TEMP,
    SECRET_KEY,
    V5_BASE_URL,
)
from .wbsk import FordPassCrypto


def wgs84_to_gcj02(lat: float, lng: float) -> tuple[float, float]:
    """Convert WGS-84 (GPS raw) coordinates to GCJ-02 (国测局加密坐标).

    China map tiles (Gaode, Tencent, ...) use GCJ-02, so a raw WGS-84 point
    lands ~600 m off on those maps.
    """
    if lng < 72.004 or lng > 137.8347 or lat < 0.8293 or lat > 55.8271:
        # outside China: no offset applies
        return lat, lng

    a = 6378245.0
    ee = 0.00669342162296594323

    def _lat(x: float, y: float) -> float:
        ret = -100.0 + 2.0*x + 3.0*y + 0.2*y*y + 0.1*x*y + 0.2*math.sqrt(abs(x))
        ret += (20.0*math.sin(6.0*x*math.pi) + 20.0*math.sin(2.0*x*math.pi)) * 2.0/3.0
        ret += (20.0*math.sin(y*math.pi) + 40.0*math.sin(y/3.0*math.pi)) * 2.0/3.0
        ret += (160.0*math.sin(y/12.0*math.pi) + 320.0*math.sin(y*math.pi/30.0)) * 2.0/3.0
        return ret

    def _lng(x: float, y: float) -> float:
        ret = 300.0 + x + 2.0*y + 0.1*x*x + 0.1*x*y + 0.1*math.sqrt(abs(x))
        ret += (20.0*math.sin(6.0*x*math.pi) + 20.0*math.sin(2.0*x*math.pi)) * 2.0/3.0
        ret += (20.0*math.sin(x*math.pi) + 40.0*math.sin(x/3.0*math.pi)) * 2.0/3.0
        ret += (150.0*math.sin(x/12.0*math.pi) + 300.0*math.sin(x/30.0*math.pi)) * 2.0/3.0
        return ret

    d_lat = _lat(lng - 105.0, lat - 35.0)
    d_lng = _lng(lng - 105.0, lat - 35.0)
    rad_lat = lat / 180.0 * math.pi
    magic = math.sin(rad_lat)
    magic = 1 - ee * magic * magic
    sqrt_magic = math.sqrt(magic)
    d_lat = (d_lat * 180.0) / ((a * (1 - ee)) / (magic * sqrt_magic) * math.pi)
    d_lng = (d_lng * 180.0) / (a / sqrt_magic * math.cos(rad_lat) * math.pi)
    return lat + d_lat, lng + d_lng


def gcj02_to_wgs84(lng: float, lat: float) -> tuple[float, float]:
    """Convert GCJ-02 (国测局加密坐标) back to WGS-84 (GPS raw).

    Single-iteration inverse of wgs84_to_gcj02: g(x) = x + d(x), so the
    inverse is x - d(g(x)) applied once (accuracy < ~1 m inside China).
    """
    if lng < 72.004 or lng > 137.8347 or lat < 0.8293 or lat > 55.8271:
        # outside China: no offset applies
        return lat, lng

    lat_g, lng_g = wgs84_to_gcj02(lat, lng)
    return lat - (lat_g - lat), lng - (lng_g - lng)


def normalize_wgs84(lat, lng):
    """Apply GCJ02->WGS84 inverse and preserve the original value type (str/float)."""
    lat_f = float(lat)
    lng_f = float(lng)
    lat_w, lng_w = gcj02_to_wgs84(lng_f, lat_f)
    if isinstance(lat, str):
        return f"{lat_w:.6f}", f"{lng_w:.6f}"
    return lat_w, lng_w


def _ser_sign(value: Any) -> str:
    """App 签名序列化（v3.1.4，按签名算法结构整理）：
    - dict  -> "{k=v&k2=v2}"（键排序，递归）
    - list  -> "[a&b]"（元素依次，递归）
    - 其他  -> str(value)
    顶层拼接沿用现有平面 k=v&...（compute_sign 的 biz 不包花括号）。
    """
    if isinstance(value, dict):
        return "{" + "&".join(
            f"{k}={_ser_sign(v)}" for k, v in sorted(value.items())
        ) + "}"
    if isinstance(value, list):
        return "[" + "&".join(_ser_sign(v) for v in value) + "]"
    return str(value)


def _canonical(params: dict[str, Any]) -> str:
    items = sorted((str(k), _ser_sign(params[k])) for k in params if k != "sign")
    return "&".join(f"{k}={v}" for k, v in items)


def _secret_key2(timestamp_ms: int) -> str:
    """secretKey2 = URLEncode(SHA256(SHA256(BeijingDate) + PAYLOAD_KEY)).

    Interoperability note (aligned with the cloud gateway's signing scheme):
    the inner SHA-256 is computed over the Beijing date string ("yyyy-MM-dd")
    derived from the request timestamp; the result is salted with the
    PAYLOAD_KEY constant and hashed again.  This is why the server started
    rejecting the old fixed 2026-09-25 value from 2026-09-26 onward.
    """
    beijing = datetime.datetime.fromtimestamp(
        timestamp_ms / 1000,
        tz=datetime.timezone(datetime.timedelta(hours=8)),
    )
    date = beijing.strftime("%Y-%m-%d")
    h1 = hashlib.sha256(date.encode("utf-8")).hexdigest()
    h2 = hashlib.sha256((h1 + PAYLOAD_KEY).encode("utf-8")).hexdigest()
    return urllib.parse.quote(h2, safe="")


def compute_sign(params: dict[str, Any]) -> str:
    """Re-produce the app's request signature.

    sign = SHA256("secretKey2=<sk2>&" + sorted(k=v) + "&secretKey=<sk1>")
    where sk2 rotates daily (see _secret_key2).
    """
    biz = _canonical(params)
    ts = int(params.get("timestamp") or 0)
    sk2 = _secret_key2(ts)
    raw = f"secretKey2={sk2}&{biz}&secretKey={SECRET_KEY}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _new_traceid() -> str:
    return f"FPC{uuid.uuid4()}{int(time.time() * 1000)}"


def compute_lbs_sign(body: dict[str, Any], timestamp_ms: int) -> str:
    """Re-produce the LBS gateway `x-sign`.

    raw = SK + sorted_body_params + ts + PKL + SK[:5] + ts[8:]
    x-sign = base64(SHA256(utf8(raw))).upper()
    Verified against live request/response captures (26/26 matches); the
    result is upper-cased before it is sent in the x-sign header.
    """
    biz = "&".join(f"{k}={body[k]}" for k in sorted(body))
    ts = str(timestamp_ms)
    raw = f"{LBS_APP_KEY}{biz}{ts}{LBS_PAYLOAD_KEY}{LBS_APP_KEY[:5]}{ts[8:]}"
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return base64.b64encode(digest).decode("ascii").upper()


def _is_err_resp(data: dict) -> bool:
    """业务错误响应判断：errorCode/error 字段存在或 status 非 0/200 → 车型无能力。

    v4.0.5: 提升为模块级（原为 probe_extra_endpoints 内嵌套函数，被
    _probe_cevs_variant_once / probe_cevs_auto 引用会 NameError——
    v3.7.22 注释只修了 wbk 一处，另两处漏改；v4.9.6 移除手动探测服务）。
    """
    if "errorCode" in data or "error" in data:
        return True
    st = data.get("status")
    if isinstance(st, (int, str)):
        try:
            return int(st) not in (0, 200)
        except (TypeError, ValueError):
            pass
    return False



_IMAGE_URL_KEYS = (
    "imageUrl", "picUrl", "photoUrl", "pictureUrl", "thumbnailUrl", "url",
    "downloadUrl", "image", "photo", "thumbnail",
)


def extract_remote_image_url(payload: Any) -> str | None:
    """递归兼容提取远程影像（停车图/行车监控）响应里的图片 URL（v3.4.4）。

    PDS 停车影像/行车监控响应结构随车型/云端版本变化，做宽松提取：
    - 直接是字符串且以 http 开头 → 视为 URL
    - dict/list 递归查找常见图片字段（imageUrl/picUrl/photoUrl/...），取第一个
      非空 http(s) 值；单张与列表（含嵌套 data[]）均兼容。
    找不到返回 None（实体显示「暂无影像」并保留最后一张）。
    """
    if isinstance(payload, str):
        s = payload.strip()
        return s if s.lower().startswith(("http://", "https://")) else None
    if isinstance(payload, dict):
        for key in _IMAGE_URL_KEYS:
            val = payload.get(key)
            if val is None:
                continue
            hit = extract_remote_image_url(val)
            if hit:
                return hit
        # 遍历其余值，兼容自定义字段名
        for val in payload.values():
            hit = extract_remote_image_url(val)
            if hit:
                return hit
        return None
    if isinstance(payload, list):
        for item in payload:
            hit = extract_remote_image_url(item)
            if hit:
                return hit
    return None


class FordPassApiError(Exception):
    """Raised when the FordPass API returns an error."""

    def __init__(
        self,
        code: Any = None,
        message: str | None = None,
        refresh_failed: bool = False,
    ) -> None:
        super().__init__(message or f"FordPass error ({code})")
        self.code = code
        self.message = message
        # v5.3.2: 显式标志——access token 续期失败（区别于普通业务拒绝）；
        # coordinator 据此触发 reauth，不再靠字符串匹配异常文案
        self.refresh_failed = refresh_failed


def _as_refresh_failed(exc: Exception) -> FordPassApiError:
    """刷新失败分类（v5.6.2）：业务性认证失败（401/403/600）转
    refresh_failed=True 触发 reauth；网关 5xx / 网络异常保留原状（非
    refresh_failed）——__init__ 据此走 ConfigEntryNotReady 自动重试，不再
    误弹 reauth。2026-10-24 实测：福特网关解密失败 500 熔断期间，
    旧代码把所有刷新异常包成 401 → entry 每轮 setup_error + 用户被要求
    重新认证（token 实际未失效）。
    """
    if isinstance(exc, FordPassApiError) and exc.code in (401, 403, 600):
        # 业务性认证失败：token 真失效 / 被拒——refresh_failed=True 触发 reauth
        return FordPassApiError(
            401, f"token refresh failed: {exc}", refresh_failed=True
        )
    # 网关 5xx / 网络异常：保留原码、refresh_failed=False——__init__ 走
    # ConfigEntryNotReady 自动重试，用户无感知、不弹重新认证
    return FordPassApiError(
        exc.code if isinstance(exc, FordPassApiError) else 500,
        f"token refresh failed: {exc}",
        refresh_failed=False,
    )


class FordPassProbeRejected(FordPassApiError):
    """探测端点返回业务拒绝（errorCode/error 或 status≠0/200）——车型无能力。

    v4.0.9: 与"空响应/网络错误"区分：拒绝=确定性能力缺失，coordinator 会
    把它持久化缓存（TTL 内不再重探）；其它异常（网关瞬时空响应等）不落盘，
    避免把瞬时故障错误钉死 7 天。
    """


def _log_summary(text: str, limit: int = 2000) -> str:
    """DEBUG 日志响应摘要（v4.3.0）：超长字符串字段（如 preference-list
    的 avatar base64 PNG，单值可达数 KB）替换为占位符，其余字段原样保留，
    避免刷屏且不丢失可读信息。非 JSON 响应原样截断。"""
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return text[:limit]

    def _trim(v):
        if isinstance(v, str) and len(v) > 120:
            return f"<str {len(v)} chars>"
        if isinstance(v, dict):
            return {k: _trim(x) for k, x in v.items()}
        if isinstance(v, list):
            return [_trim(x) for x in v]
        return v

    out = json.dumps(_trim(obj), ensure_ascii=False)
    return out[:limit]


def _token_resp_summary(text: str) -> str:
    """LBS third-party-token 响应 DEBUG 摘要（v5.2.7）：
    data.encryptedAccessToken 的密钥（x_api scene）随集成发布，值不得进日志——
    只保留状态码与 data 字段名（含加密字段的键名，不含任何值）。"""
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return f"<non-json {len(text)} bytes>"
    data = obj.get("data")
    if isinstance(data, dict):
        return "data_fields=" + ",".join(sorted(data.keys()))
    return "data=<non-dict>"


# v5.6.10: debug 日志脱敏——vehicle-status raw 等整包 JSON 日志对 VIN、
# 经纬度、地址等个人标识掩码，防止 debug 日志外泄（recorder/system_log
# 均可能持久化）。键匹配只收窄到明确敏感键，不做宽泛子串匹配。
_VIN_MASK_SUFFIXES = ("vin", "vehicleid", "chassisnumber", "chassisno")
_COORD_MASK_KEYS = frozenset(
    {"lat", "lon", "lng", "latitude", "longitude", "latdeg", "londeg"}
)
_ADDR_MASK_SUFFIXES = ("address",)
_UNMASKED = object()


def _mask_debug_value(key: str, value: Any) -> Any:
    """单个键值脱敏：敏感键返回掩码值，其余返回 _UNMASKED 哨兵（继续递归）。"""
    kl = key.lower()
    if isinstance(value, str) and (
        kl == "vin" or kl.endswith(_VIN_MASK_SUFFIXES)
    ):
        # 保留首 4 末 4（VIN 17 位），中间掩码——保留可辨识片段便于调试
        return f"{value[:4]}****{value[-4:]}" if len(value) >= 8 else "****"
    if kl in _COORD_MASK_KEYS or kl.endswith(("latitude", "longitude")):
        # 经纬度整体掩码（小数位泄露街道级精度，整数度亦泄露城市位置）
        return "***"
    if isinstance(value, str) and kl.endswith(_ADDR_MASK_SUFFIXES):
        return "***"
    return _UNMASKED


def _mask_debug_json(obj: Any) -> Any:
    """递归脱敏 dict/list 中 VIN、经纬度、地址（v5.6.10）。

    仅用于 debug 日志输出，不改动原始响应数据。"""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            masked = _mask_debug_value(k, v)
            if masked is _UNMASKED:
                out[k] = _mask_debug_json(v)
            else:
                out[k] = masked
        return out
    if isinstance(obj, list):
        return [_mask_debug_json(x) for x in obj]
    return obj


class FordPassApi:

    def __init__(
        self,
        session: aiohttp.ClientSession,
        logger,
        brand: str = BRAND_FORD,
    ) -> None:
        self._session = session
        self._log = logger
        # v5.5.3: 品牌参数化（林肯登录修复）——默认福特行为不变；林肯
        # 实机样本(2026-10-23 v6.7.0) 实证：touchPoint=LINCOLN_APP_LOGIN、
        # brand=LINCOLN、vehicles/list appKey=lincoln&clientType=LW
        _profile = BRAND_PROFILE.get(brand) or BRAND_PROFILE[BRAND_FORD]
        self._brand = brand
        self._brand_code = _profile["brand_code"]
        self._app_key = _profile["app_key"]
        self._client_type = _profile["client_type"]
        self._app_version = _profile["app_version"]
        self._touch_point = _profile["touch_point"]
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._lbs_token: str | None = None
        self._lbs_token_expiry: float | None = None
        self._retrying = False
        # v5.3.1: refresh_token 单飞锁 + 短窗口结果缓存——多车 coordinator
        # 并发 401 时（同一 api 实例共享 token，过期那轮同时触发），此前
        # _retrying 布尔在并发检查下会同时放行两个协程各自 refresh 一次
        # （后写者覆盖 on_token_refresh 持久化）；现所有 refresh 调用在
        # refresh_token 方法内经锁串行，且 5s 窗口内同 refresh_token 复用
        # 第一次的结果，真正单飞。_retrying 保留作为递归防环标志。
        self._refresh_lock = asyncio.Lock()
        self._refresh_cache: tuple[str, float, dict[str, str]] | None = None
        self.on_token_refresh = None
        # v3.7.6: cevs 域探测诊断（预约出发/充电）——每个端点探测结果
        # 存这里（ok/rejected/failed + 摘要），coordinator 暴露给诊断
        # 传感器，HA 侧可直接读取（无需导出日志）。
        self.cevs_diag: dict[str, str] = {}
        # v4.0.12: 账户级端点共享缓存（chirp_cloud / remote_climate_cloud /
        # messages_summary 等 UserPreference 账户级数据）——api 实例按账号
        # 维度（entry）创建，多 VIN 账号每车一个 coordinator 并发刷新时，
        # 账户级端点只发一次网络请求，其余车直接复用；锁保证并发 miss
        # 时单飞（防风控）。
        self._account_cache: dict[str, tuple[float, Any]] = {}
        self._account_locks: dict[str, asyncio.Lock] = {}

    async def _account_cached(
        self, key: str, ttl: float, factory
    ) -> Any:
        """v4.0.12: 账户级 TTL 缓存——命中直接返回；并发 miss 时经 per-key
        锁只放行一次网络请求，其余协程等待后复用结果。"""
        now = time.monotonic()
        ent = self._account_cache.get(key)
        if ent and now < ent[0]:
            return ent[1]
        lock = self._account_locks.setdefault(key, asyncio.Lock())
        async with lock:
            ent = self._account_cache.get(key)
            if ent and time.monotonic() < ent[0]:
                return ent[1]
            val = await factory()
            self._account_cache[key] = (time.monotonic() + ttl, val)
            return val

    @property
    def crypto(self) -> FordPassCrypto:
        return FordPassCrypto.get()

    def set_token(self, token: str | None, refresh_token: str | None = None) -> None:
        self._access_token = token
        if refresh_token is not None:
            self._refresh_token = refresh_token

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "user-agent": "Dart (dart:io)",
            "appversion": self._app_version,
            "ostype": OS_TYPE,
            "osversion": OS_VERSION,
            "clienttype": self._client_type,
            "application-id": APPLICATION_ID,
            "content-type": "application/json; charset=utf-8",
            "accept-encoding": "gzip",
            "x-dynatrace": "",
            "x-b3-traceid": _new_traceid(),
        }
        # v5.6.9: token-exchange 家族（refresh/revoke/login/sms 等）App 实拍
        # 均不带 auth-token（实机样本 6.16.0 实证 27 个无 auth-token 请求全部属
        # token-exchange/app-init 域）——refresh/revoke 时 access token 已失效，
        # 带旧 auth-token 会让网关校验失败返回 401/600。extra 传
        # {"auth-token": None} 表示该请求必须不带 auth-token。
        if extra is not None and "auth-token" in extra:
            if extra["auth-token"] is None:
                extra = {k: v for k, v in extra.items() if k != "auth-token"}
            else:
                headers["auth-token"] = extra["auth-token"]
        elif self._access_token:
            headers["auth-token"] = self._access_token
        if extra:
            headers.update(extra)
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        base: str = BASE_URL,
        raw: bool = False,
        headers: dict[str, str] | None = None,
        raw_body: bool = False,
        _retried: bool = False,
    ) -> Any:
        url = base + path
        if body is not None:
            signed: dict[str, Any] = dict(body)
            if not raw_body:
                # v3.7.5: cevs 域 DTO 不接受 timestamp/sign（日志实证
                # Unrecognized field "timestamp"）——raw_body=True 时 body
                # 原样发送（cevs 走 RSA 密文自校验，不依赖外层签名）
                signed["timestamp"] = int(time.time() * 1000)
                signed["sign"] = compute_sign(signed)
            kwargs: dict[str, Any] = {"json": signed}
        else:
            params = dict(query or {})
            params["timestamp"] = int(time.time() * 1000)
            params["sign"] = compute_sign(params)
            kwargs = {"params": params}
        # v5.2.4: 请求体日志只记录字段名不记录值——encryptedRefreshToken/
        # encryptedPasscode/encryptedAuthCode 的解密密钥就在集成内，完整值进
        # debug 日志等于泄露 refresh token（用户会把日志贴进 issue）。
        _payload_fields = (
            ",".join(sorted(kwargs["json"].keys()))
            if "json" in kwargs and isinstance(kwargs["json"], dict)
            else ",".join(sorted(kwargs["params"].keys()))
            if "params" in kwargs and isinstance(kwargs["params"], dict)
            else ""
        )
        self._log.debug("FordPass %s %s payload_fields=%s", method, url, _payload_fields)
        # v3.3.2: 显式超时 60s——HA 默认 aiohttp session timeout 仅 ~10s，
        # 福特网关（尤其 ForceRefresh 后立即拉取 vehicle-status）偶发慢响应
        # 会被截断抛 asyncio.TimeoutError → coordinator 每轮刷
        # "FordPass update timed out" ERROR 且数据取不到。
        async with self._session.request(
            method,
            url,
            headers=self._headers(headers),
            timeout=aiohttp.ClientTimeout(total=60),
            **kwargs,
        ) as resp:
            try:
                text = await resp.text()
            except UnicodeDecodeError:
                # v3.0.6: 网关偶发返回非文本响应（二进制/损坏 body，如
                # ForceRefresh 后立即拉取 vehicle-status 时），先读原始字节
                # 便于诊断，再转成明确的 FordPassApiError——不再让 aiohttp
                # 的裸 codec 错误冒泡到日志。
                raw_bytes = await resp.read()
                self._log.warning(
                    "FordPass %s -> 非文本响应 %d bytes (status=%s): %s",
                    path, len(raw_bytes), resp.status, raw_bytes[:80].hex(),
                )
                raise FordPassApiError(
                    resp.status, "网关返回非文本响应（瞬时异常，已按重试策略处理）"
                ) from None
            self._log.debug("FordPass %s -> %s %s", path, resp.status, _log_summary(text))
            # v4.7.0: 风控响应分类——429=限流、403+HTML=安全防护/CDN 拦截页、
            # 5xx=网关熔断；与业务失败（2xx/4xx JSON 业务错误码）区分，便于
            # 诊断与处置（识别见 references/risk-control.md §1）。仅记录
            # 分类日志，不改变现有异常流（仍抛 FordPassApiError，由
            # coordinator 失败保留旧值兜底，不连环重试）。
            ctype = (resp.headers.get("Content-Type") or "").lower()
            is_html = "html" in ctype or text.lstrip().startswith("<")
            if resp.status == 429:
                self._log.warning(
                    "FordPass %s -> HTTP 429 限流信号（请求过频），停手退避: %s",
                    path, _log_summary(text),
                )
            elif resp.status == 403 and is_html:
                self._log.warning(
                    "FordPass %s -> HTTP 403 + HTML = 安全防护/CDN 拦截页（外部限制，非业务问题）: %s",
                    path, _log_summary(text),
                )
            elif resp.status == 403:
                self._log.debug(
                    "FordPass %s -> HTTP 403（业务拒绝/无权限，非限流）: %s",
                    path, _log_summary(text),
                )
            elif resp.status >= 500:
                self._log.warning(
                    "FordPass %s -> HTTP %s 网关层故障（熔断/5xx，退避后自愈）: %s",
                    path, resp.status, _log_summary(text),
                )
            if resp.status == 401 and self._refresh_token:
                # v5.3.3: 已重试过一次仍 401（刷新后新 token 仍被拒，或
                # refresh 端点自身 401）——直接抛 refresh_failed 触发 reauth，
                # 禁止继续递归（旧代码无条件 return await self._request(...)
                # 无限递归直至 RecursionError：刷新成功路径中 finally 在
                # return await 求值之后才执行，_retrying 恒为 True 会反复
                # 走 sleep+递归分支）。
                if _retried:
                    raise FordPassApiError(
                        401, "token refresh failed (401 after retry)", refresh_failed=True
                    )
                if not self._retrying:
                    self._retrying = True
                    try:
                        self._log.debug("FordPass access token expired, refreshing once")
                        new = await self.refresh_token(self._refresh_token)
                        self._access_token = new["access_token"]
                        if self.on_token_refresh is not None:
                            self.on_token_refresh(new["access_token"])
                        return await self._request(method, path, body, query, base, raw, headers, raw_body, _retried=True)
                    except Exception as exc:
                        self._log.error("FordPass token refresh failed: %s", exc)
                        raise _as_refresh_failed(exc) from exc
                    finally:
                        self._retrying = False
                # v5.3.2: 已有协程正在刷新（_retrying=True）——短等后重试本次请求，
                # 复用新 token（此前并发 401 时非首个协程直接抛错，多车账号该轮失败）
                # P0-5（v5.6.0）：不能 sleep(0.5) 后拿**旧 token** 重试——刷新通常
                # >0.5s，重试必再 401 且 _retried=True 直接 refresh_failed 误触
                # reauth。等待方改为直接 await refresh_token（内部单飞锁 + 5 秒
                # 结果缓存，等待时不会再发起第二次刷新），拿到新 token 后重试。
                try:
                    new = await self.refresh_token(self._refresh_token)
                    self._access_token = new["access_token"]
                    if self.on_token_refresh is not None:
                        self.on_token_refresh(new["access_token"])
                except Exception as exc:  # noqa: BLE001 - 刷新失败才置 refresh_failed
                    self._log.error("FordPass concurrent token refresh failed: %s", exc)
                    raise FordPassApiError(
                        401, f"token refresh failed: {exc}", refresh_failed=True
                    ) from exc
                return await self._request(method, path, body, query, base, raw, headers, raw_body, _retried=True)
            if resp.status >= 400:
                raise FordPassApiError(resp.status, text[:300])
            if not text:
                return None
            try:
                data = json.loads(text)
            except json.JSONDecodeError as err:
                if raw:
                    return text
                # v4.7.0: HTTP 200 但返回 HTML = 网关层拦截页伪装成成功响应
                # （CDN 校验页/验证码页），归类为风控拦截而非普通解析错误。
                if text.lstrip().startswith("<"):
                    self._log.warning(
                        "FordPass %s -> HTTP 200 + HTML = 网关拦截页（风控/CDN 校验，非业务失败）: %s",
                        path, _log_summary(text),
                    )
                raise FordPassApiError(resp.status, f"non-JSON response: {text[:200]}") from err
        if isinstance(data, dict):
            # Business-layer token expiry: the gateway answers HTTP 200 with a
            # business status code once the token lapses. Observed variants:
            #   {"status":600,"error":"Swap token failed {…Cat2 token expired…}",
            #    "errorCode":"100502"}  (2026-09-29, ForceRefresh flow)
            #   {"status":401,"error":"…verify token error:. JwtValidationException:
            #    …Jwt expired at <ts>…","errorCode":"100505"}  (2026-10-12, honk
            #    settings cloud save — select platform surfaced the warning)
            # Both mean the access token lapsed mid-flight; treat exactly like an
            # HTTP 401: refresh once and retry.
            status = data.get("status")
            # v5.3.4: 业务 401/600 判定不再依赖错误文案——此前要求
            # "token" in error.lower()，福特云若改文案（如 "Jwt expired"/
            # "Session invalid" 不含 token 字样）则 token 失效不刷新不报错、
            # 实体静止无 reauth。status 401/600 本身即强失效信号，直接刷新
            # 一次（v5.3.3 _retried 防环保证最多一次，刷新失败自动
            # refresh_failed 触发 reauth）。
            if (
                status in (401, 600)
                and self._refresh_token
            ):
                # v5.3.3: 与 HTTP 401 分支同款防递归——刷新后重试仍返回
                # 401/600 业务 token 错误时直接 refresh_failed 触发 reauth，
                # 不再无条件递归（旧代码 finally 滞后导致 _retrying 恒 True，
                # 无限递归至 RecursionError）。
                if _retried:
                    raise FordPassApiError(
                        401, "token refresh failed (401/600 after retry)", refresh_failed=True
                    )
                if not self._retrying:
                    self._retrying = True
                    try:
                        self._log.debug(
                            "FordPass business-layer token expired (status=%s), refreshing once", status
                        )
                        new = await self.refresh_token(self._refresh_token)
                        self._access_token = new["access_token"]
                        if self.on_token_refresh is not None:
                            self.on_token_refresh(new["access_token"])
                        return await self._request(method, path, body, query, base, raw, headers, raw_body, _retried=True)
                    except Exception as exc:
                        self._log.error("FordPass token refresh failed: %s", exc)
                        raise _as_refresh_failed(exc) from exc
                    finally:
                        self._retrying = False
                # v5.3.2: 已有协程正在刷新（_retrying=True）——短等后重试本次请求，
                # 复用新 token（此前并发 401 时非首个协程直接抛错，多车账号该轮失败）
                await asyncio.sleep(0.5)
                return await self._request(method, path, body, query, base, raw, headers, raw_body, _retried=True)
            code = data.get("code")
            if code not in (None, 0, "0", 200, "200", "0000"):
                raise FordPassApiError(
                    code, data.get("message") or data.get("msg") or data.get("errmsg")
                )
        return data

    # ------------------------------------------------------------------ auth
    async def generate_passcode(
        self, phone: str, iv_hex: str | None = None
    ) -> str:
        """Send the SMS passcode; returns the session xjw (hex IV).

        The official app reuses the same IV across the whole login session
        (verified live: captures #79 and #83 carry the identical xjw), so the
        caller should keep the returned xjw and pass it to passcode_login.
        """
        iv = bytes.fromhex(iv_hex) if iv_hex else None
        enc, xjw = await asyncio.to_thread(lambda: self.crypto.encrypt_field(phone, iv))
        await self._request(
            "POST",
            PATH_GENERATE_PASSCODE,
            {"encryptedPhoneNumber": enc, "touchPoint": self._touch_point, "xjw": xjw},
            # v5.7.8: 与 passcode_login 一致的防御——token-exchange 家族 App
            # 实拍均不带 auth-token（实机样本2026-10-24 林肯 6.0.0 实证
            # generate-passcode 无 auth-token 头）；reauth 场景若内存残留旧
            # access token，默认 _headers 会带上导致网关校验失败。当前
            # config_flow 每次新建 api 实例不 set_token 不触发，但防未来
            # 复用路径（v5.6.9 修 passcode_login 时漏掉 generate_passcode）。
            headers={"auth-token": None},
        )
        return xjw

    async def passcode_login(
        self,
        phone: str,
        passcode: str,
        iv_hex: str | None = None,
    ) -> dict[str, str]:
        """Exchange SMS passcode for access/refresh JWTs (verified live).

        Phone and passcode are encrypted with the SAME session IV (one xjw
        field per request, as the official app does); otherwise the server
        fails with a decrypt error.
        """
        if iv_hex:
            iv = bytes.fromhex(iv_hex)
        else:
            # v5.5.6: iv=None（林肯分支）——自生成一次会话 IV 并复用。
            # 若两次 encrypt_field 各传 None 会生成两个不同随机 IV，请求只
            # 携带 phone 的 xjw，服务端解 encryptedPasscode 报解密失败
            # （errorCode 100500）。
            iv = FordPassCrypto._new_iv()
        enc_phone, xjw = await asyncio.to_thread(lambda: self.crypto.encrypt_field(phone, iv))
        enc_code, _ = await asyncio.to_thread(lambda: self.crypto.encrypt_field(passcode, iv))
        data = await self._request(
            "POST",
            PATH_PASSCODE_LOGIN,
            {
                "encryptedPhoneNumber": enc_phone,
                "encryptedPasscode": enc_code,
                "tncAccepted": 1,
                "tncOutBoundAccepted": 1,
                "touchPoint": self._touch_point,
                "brand": self._brand_code,
                "osType": "ANDROID",
                "xjw": xjw,
            },
            # v5.6.9: token-exchange 家族不带 auth-token（App 实机样本实证）；
            # reauth 场景旧 access token 仍在内存，必须显式排除。
            headers={"auth-token": None},
        )
        inner = data.get("data", {}) if isinstance(data, dict) else {}
        xjw2 = inner.get("xjw", xjw)
        if not isinstance(inner, dict) or "encryptedAccessToken" not in inner:
            # v5.5.7: HTTP 200 但无 token 字段——业务层失败（验证码错误/过期、
            # 风控拒绝）或响应结构变更。此前直接 KeyError 被 config flow 当
            # "unexpected error"，用户看不到真实原因。带服务端 status/error/
            # errorCode 抛 FordPassApiError，config flow 显示 login_failed。
            status = data.get("status") if isinstance(data, dict) else None
            err_text = (
                "passcode login returned no token fields"
                f"(status={status}, error={data.get('error') if isinstance(data, dict) else '?'}, "
                f"errorCode={data.get('errorCode') if isinstance(data, dict) else '?'})"
            )
            self._log.warning("FordPass %s -> %s", PATH_PASSCODE_LOGIN, err_text)
            raise FordPassApiError(status if isinstance(status, int) else 200, err_text)
        return {
            "access_token": await asyncio.to_thread(lambda: self.crypto.decrypt_field(
                inner["encryptedAccessToken"], xjw2
            )),
            "refresh_token": await asyncio.to_thread(lambda: self.crypto.decrypt_field(
                inner["encryptedRefreshToken"], xjw2
            )),
        }

    async def refresh_token(self, refresh_token: str) -> dict[str, str]:
        # v5.3.1: 锁 + 5s 窗口结果缓存——并发 401 时多协程同时到达，锁保证
        # 串行，窗口缓存让后续协程直接复用第一次的刷新结果，避免重复请求
        # 云端与重复持久化（后写者覆盖）。
        async with self._refresh_lock:
            if self._refresh_cache and self._refresh_cache[0] == refresh_token:
                if time.monotonic() < self._refresh_cache[1]:
                    return self._refresh_cache[2]
                self._refresh_cache = None
            # v5.6.9（2026-10-24 实机样本 6.16.0 完整 body 记录）：refresh-dlt-token
            # 与 App 完全一致 = encryptedRefreshToken + brand + xjw + timestamp
            # + sign 五字段（timestamp/sign 由 _request 通用层补）。brand 必填
            # （去掉返回 400 errorCode 100502 "brand can not be empty"）。
            # 关键：本端点**不带 auth-token**（App 无 auth-token，集成 v5.6.6
            # 带旧 access token 致网关 401/600 业务拒绝——这是 v5.6.6 刷新
            # 失败的真根因；v5.6.7 误删 brand 反把 401/600 改成 400 必填）。
            enc, xjw = await asyncio.to_thread(lambda: self.crypto.encrypt_field(refresh_token))
            # v5.8.4: 防 refresh 端点自身 401/600 时 _request 递归刷新死锁——
            # refresh_token 已持有 _refresh_lock（asyncio.Lock 不可重入），若
            # refresh-dlt-token 响应业务 401/600 且 _retrying=False（直接调用
            # 场景如 config flow / coordinator），_request 会再调 refresh_token
            # 二次拿锁 → 永久阻塞。置空 self._refresh_token 使 _request 的
            # HTTP 401 / 业务 401/600 分支都因「无 refresh_token」直接抛错
            # （不触发刷新），由下方退避循环负责重试；finally 恢复。
            _saved_rt = self._refresh_token
            self._refresh_token = None
            try:
                # v5.8.4: refresh-dlt-token 失败退避重试——福特云对密集命令/会话
                # 敏感端点有窗口式风控（2026-10-25 实测：命令路径 401/600 时
                # refresh 端点同步被拒，但同一 access token 后台轮询成功 →
                # 非 token 失效而是网关瞬时拒绝）。sleep 1/2/4s 有限重试 3 次
                # （共 4 次尝试），仍失败抛最后异常由 _request 401 分支按
                # refresh_failed 处理。与 lessons「失败退避与抑制」一致：有限
                # 重试、不无限轰炸。
                _attempts = 0
                while True:
                    try:
                        data = await self._request(
                            "POST",
                            PATH_REFRESH_TOKEN,
                            {"encryptedRefreshToken": enc, "brand": self._brand_code, "xjw": xjw},
                            headers={"auth-token": None},
                        )
                        break
                    except (FordPassApiError, OSError, asyncio.TimeoutError) as exc:
                        _attempts += 1
                        if _attempts > 3:
                            raise
                        delay = 2 ** (_attempts - 1)
                        self._log.warning(
                            "FordPass %s failed (attempt %d/4, retry in %ds): %s",
                            PATH_REFRESH_TOKEN, _attempts + 1, delay, exc,
                        )
                        await asyncio.sleep(delay)
            finally:
                self._refresh_token = _saved_rt
            inner = data.get("data", {})
            xjw2 = inner.get("xjw", xjw)
            if not isinstance(inner, dict) or "encryptedAccessToken" not in inner:
                # v5.7.7: 与 passcode_login 同等的响应结构保护——HTTP 200 但
                # 无 token 字段（网关异常响应/结构变更/业务拒绝）抛带
                # status/error/errorCode 的 FordPassApiError，避免裸 KeyError
                # 被 _as_refresh_failed 按 500 处理丢失诊断信息（2026-10-24
                # 福特网关解密失败 500 期间日志只有 "token refresh
                # failed"，无法区分业务拒绝与结构异常）。
                status = data.get("status") if isinstance(data, dict) else None
                err_text = (
                    "token refresh returned no token fields"
                    f"(status={status}, error={data.get('error') if isinstance(data, dict) else '?'}, "
                    f"errorCode={data.get('errorCode') if isinstance(data, dict) else '?'})"
                )
                self._log.warning("FordPass %s -> %s", PATH_REFRESH_TOKEN, err_text)
                raise FordPassApiError(status if isinstance(status, int) else 200, err_text)
            result: dict[str, str] = {
                "access_token": await asyncio.to_thread(lambda: self.crypto.decrypt_field(
                    inner["encryptedAccessToken"], xjw2
                )),
                "expires_in": inner.get("expiresIn"),
            }
            # v5.3.1: 防御性回存新 refresh_token——若云端在刷新响应里轮换了
            # refresh token（当前福特云实测不轮换，仅防御），解密后更新内存
            # 与 entry.data（on_token_refresh 持久化），避免下次续期用旧值
            # 失效触发 reauth。
            new_refresh = inner.get("encryptedRefreshToken")
            if new_refresh:
                try:
                    rt = await asyncio.to_thread(
                        lambda body=new_refresh, x=xjw2: self.crypto.decrypt_field(body, x)
                    )
                    if rt:
                        self._refresh_token = rt
                        if self.on_token_refresh is not None:
                            # v5.3.1: 回调带 (access, refresh) 双参数——旧签名
                            # 单参数会把 refresh token 误写进 access_token 字段
                            self.on_token_refresh(result["access_token"], rt)
                        result["refresh_token"] = rt
                except Exception:  # noqa: BLE001 - 轮换回存失败不影响本次刷新
                    self._log.debug("FordPass refresh token rotation persist failed")
            self._refresh_cache = (refresh_token, time.monotonic() + 5, result)
            return result

    async def revoke_token(self, refresh_token: str) -> None:
        # v5.6.9（实机样本 6.16.0 完整 body 记录）：revoke-dlt-token 与 App 一致 =
        # encryptedRefreshToken + brand + xjw + timestamp + sign 五字段
        # （timestamp/sign 由 _request 补）；同样**不带 auth-token**。
        enc, xjw = await asyncio.to_thread(lambda: self.crypto.encrypt_field(refresh_token))
        await self._request(
            "POST",
            PATH_REVOKE_TOKEN,
            {"encryptedRefreshToken": enc, "brand": self._brand_code, "xjw": xjw},
            headers={"auth-token": None},
        )

    # -------------------------------------------------------------- vehicles
    async def get_vehicles(self) -> list[dict[str, Any]]:
        """GET /v5/vehicles/list (verified live)."""
        data = await self._request(
            "GET",
            PATH_VEHICLES_LIST,
            query={"appKey": self._app_key, "appVersion": self._app_version, "clientType": self._client_type},
        )
        inner = data.get("data", {})
        xjw = inner.get("xjw")
        vehicles: list[dict[str, Any]] = []
        for item in inner.get("list", []):
            v = dict(item)
            if xjw:
                for key in ("encryptedVin", "encryptedNickName", "encryptedLicenseplate"):
                    if v.get(key):
                        # v4.0.6: 解密失败兜底——单字段失败保留密文原值并记
                        # debug，不中断整个 get_vehicles（否则任意一辆车任一
                        # 字段解密失败 → 整个账号 ConfigEntryNotReady 无法
                        # 加载，殃及其他正常车辆）。
                        try:
                            # v4.0.7: 默认参数绑定 v[key]/xjw，消除闭包晚绑定
                            # （B023）——lambda 默认参数在定义时求值，杜绝
                            # 依赖 asyncio.to_thread 提交即执行的巧合。
                            v[key] = await asyncio.to_thread(
                                lambda val=v.get(key), x=xjw: self.crypto.decrypt_field(val, x)  # noqa: B008 - lambda 默认参数绑定（B023 修复的正确模式，B008 对 lambda 误报）
                            )
                        except Exception as exc:  # noqa: BLE001
                            self._log.debug(
                                "FordPass vehicle field %s decrypt failed: %s", key, exc
                            )
            vehicles.append(v)
        return vehicles

    async def get_vehicle_status(self, vin: str) -> dict[str, Any]:
        """GET /v1/vehicle-status, response body is encrypted (verified live).

        v3.0.6: ForceRefresh 后立即拉取时福特网关偶发返回瞬时异常（非文本/
        空 data/解密失败），自动重试一次（间隔 2 秒）再放弃，避免「手动拉取
        最新状态」流程被偶发抖动打断。
        """
        for attempt in (1, 2):
            try:
                enc_vin, xjw = await asyncio.to_thread(lambda: self.crypto.encrypt_field(vin))
                data = await self._request(
                    "GET",
                    PATH_VEHICLE_STATUS,
                    query={"encryptedVin": enc_vin, "xjw": xjw},
                )
                inner = data.get("data", {})
                if inner.get("encryptedResponseBody"):
                    # v4.0.7: 默认参数绑定消除闭包晚绑定（B023，inner 在
                    # for attempt 循环内每轮重新赋值）。
                    plain = await asyncio.to_thread(
                        lambda body=inner["encryptedResponseBody"], x=inner["xjw"]:
                        self.crypto.decrypt_field(body, x)
                    )
                    result = json.loads(plain)
                    self._log.debug(
                        "FordPass vehicle-status raw: %s",
                        json.dumps(_mask_debug_json(result), ensure_ascii=False)[:6000],
                    )
                    return result
                return inner
            except Exception as exc:  # noqa: BLE001 - v5.3.4: 加宽捕获——解密（decrypt_field 抛 ValueError 等）与解析瞬时失败同样重试一次（CancelledError 为 BaseException 不受影响）；原元组只含 TimeoutError/FordPassApiError/JSONDecodeError/UnicodeDecodeError，解密失败不重试直接冒泡
                if attempt == 1:
                    self._log.warning(
                        "FordPass vehicle-status 瞬时异常（第 1 次，2 秒后重试）: %s",
                        exc,
                    )
                    await asyncio.sleep(2)
                    continue
                raise

    # ------------------------------------------------------------- commands
    async def send_command(
        self, vin: str, command_type: str, cmd_spec: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        """POST /v1/vehicles/send-command (verified live for Auto/ForceRefresh).

        v3.1.17: 支持 cmd_spec（福特派真实请求：InitialVA 带
        [{key: VAType, value: 4}, {key: Duration, value: 15}]，
        CancelVA 带 [{key: VAType, value: 4}]——实机样本确认）。
        """
        enc_vin, xjw = await asyncio.to_thread(lambda: self.crypto.encrypt_field(vin))
        body: dict[str, Any] = {
            "commandType": command_type, "encryptedVin": enc_vin, "xjw": xjw,
        }
        if cmd_spec:
            body["cmdSpec"] = cmd_spec
        data = await self._request("POST", PATH_SEND_COMMAND, body)
        inner = data.get("data", {})
        if inner.get("encryptedResponseBody"):
            plain = await asyncio.to_thread(lambda: self.crypto.decrypt_field(inner["encryptedResponseBody"], inner["xjw"]))
            result = json.loads(plain)
            self._log.debug(
                "FordPass send-command %s decrypted: %s",
                command_type, json.dumps(result, ensure_ascii=False)[:400],
            )
            return result
        self._log.debug(
            "FordPass send-command %s raw data (no encryptedResponseBody): %s",
            command_type, json.dumps(inner, ensure_ascii=False)[:400],
        )
        return inner

    async def honk_command(
        self,
        vin: str,
        duration: int = 30,
        interval: int = 0,
        chirp_type: int = 1,
    ) -> dict[str, Any]:
        """POST /api/vehicles/v5/{vin}/honk — 鸣笛寻车「开」通道。

        2026-10-01 从福特派 App 客户端整理，依据接口行为分析：
        FordHonkCommand::sendCommand 构造参数 map
        {ChirpOrHonkDuration, IntervalBetweenRequests, ChirpType}
        → FordRemoteControlApiService::sendHonkCommand（POST + requestBody）。
        v5 网关 POST honk 为明文 JSON（无需 wbsk 加密信封），
        默认 30 秒鸣笛、ChirpType=1（喇叭）。
        """
        url = V5_BASE_URL + PATH_V5_HONK.format(vin=vin)
        body = {
            "ChirpOrHonkDuration": duration,
            "IntervalBetweenRequests": interval,
            "ChirpType": chirp_type,
        }
        async with self._session.request(
            "POST", url, headers=self._headers(), json=body
        ) as resp:
            try:
                text = await resp.text()
            except UnicodeDecodeError:
                # v3.0.9: 与 _request 的 v3.0.6 保护对齐——网关偶发返回
                # 非文本响应（二进制/损坏 body）时先读原始字节诊断，
                # 再转成明确的 FordPassApiError，避免裸 codec 错误冒泡。
                raw_bytes = await resp.read()
                self._log.warning(
                    "FordPass %s -> 非文本响应 %d bytes (status=%s): %s",
                    url, len(raw_bytes), resp.status, raw_bytes[:80].hex(),
                )
                raise FordPassApiError(
                    resp.status, "网关返回非文本响应（瞬时异常，已按重试策略处理）"
                ) from None
            self._log.debug("FordPass v5 honk POST -> %s %s", resp.status, text[:500])
            if resp.status == 401 and self._refresh_token:
                if not self._retrying:
                    self._retrying = True
                    try:
                        new = await self.refresh_token(self._refresh_token)
                        self._access_token = new["access_token"]
                        if self.on_token_refresh is not None:
                            self.on_token_refresh(new["access_token"])
                        async with self._session.request(
                            "POST", url, headers=self._headers(), json=body
                        ) as resp2:
                            text2 = await resp2.text()
                            if resp2.status >= 400:
                                raise FordPassApiError(resp2.status, text2[:300], refresh_failed=(resp2.status == 401))
                            if not text2:
                                return {}
                            try:
                                return json.loads(text2)
                            except json.JSONDecodeError:
                                return {"raw": text2}
                    except Exception as exc:
                        self._log.error("FordPass v5 honk token refresh failed: %s", exc)
                        raise _as_refresh_failed(exc) from exc
                    finally:
                        self._retrying = False
                # v5.3.2: 已有协程正在刷新（_retrying=True）——短等后重试本次请求，
                # 复用新 token（此前并发 401 时非首个协程直接抛错，多车账号该轮失败）
                await asyncio.sleep(0.5)
                async with self._session.request(
                    "POST", url, headers=self._headers(), json=body
                ) as resp2:
                    text2 = await resp2.text()
                    if resp2.status >= 400:
                        raise FordPassApiError(resp2.status, text2[:300], refresh_failed=(resp2.status == 401))
                    if not text2:
                        return {}
                    try:
                        return json.loads(text2)
                    except json.JSONDecodeError:
                        return {"raw": text2}
            if resp.status >= 400:
                raise FordPassApiError(resp.status, text[:300])
            if not text:
                return {}
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"raw": text}

    async def va_command(
        self,
        vin: str,
        command_type: str,
        vatype: int = 4,
        duration: int | None = None,
    ) -> dict[str, Any]:
        """send-command InitialVA / CancelVA — 鸣笛通告（Vehicle Announcement）。

        v3.2.1: 实机样本实证（2026-10-03，福特派 6.16.0）——App 的「鸣笛寻车
        （声光共舞）」走 cn.api.mps.ford.com.cn send-command：
          InitialVA: {"commandType":"InitialVA","cmdSpec":[{"key":"VAType",
            "value":"4"},{"key":"Duration","value":"15"}]} → 200 + commandId=26
          CancelVA:  {"commandType":"CancelVA","cmdSpec":[{"key":"VAType",
            "value":"4"}]} → 200 + commandId=476
        VAType = VehicleAnnouncementType 枚举（chrip1=0 / chirp2=1 /
        chirpHonk=2 / honk=3 / panic=4）。类型 4（声光共舞=灯+喇叭）走此
        通道——V5 /panic 端点在中国区网关实测 404。
        """
        cmd_spec: list[dict[str, Any]] = [{"key": "VAType", "value": str(vatype)}]
        if duration is not None:
            cmd_spec.append({"key": "Duration", "value": str(duration)})
        return await self.send_command(vin, command_type, cmd_spec)

    async def panic_command(self, vin: str, duration: int = 10) -> dict[str, Any]:
        """PUT /api/vehicles/v5/{vin}/panic/{paniconduration} — 声光共舞。

        v4.2.5 方法对齐 6.16.0 接口行为分析：sendPanicCommand 为 PUT
        （此前误用 POST，网关对支持 panic 的车型会 405）。duration 直接
        写在路径里。实测返回 404（云端不支持该通道），调用方应捕获
        FordPassApiError(404) 并向用户明确提示车型不支持。
        """
        return await self._v5_verb("PUT", PATH_V5_PANIC.format(vin=vin, duration=duration),
                                   "panic")

    async def panic_cancel_command(self, vin: str, duration: int = 10) -> dict[str, Any]:
        """DELETE /api/vehicles/v5/{vin}/panic/{paniconduration} — 取消声光警报。

        v4.2.5：6.16.0 实测确认 sendPanicCancelCommand 为 DELETE（此前缺失）。
        """
        return await self._v5_verb("DELETE", PATH_V5_PANIC.format(vin=vin, duration=duration),
                                   "panic-cancel")

    async def _v5_verb(self, method: str, url: str, what: str) -> dict[str, Any]:
        """v5 直控通用请求封装（v4.2.5 重构）：任意 HTTP 方法 + 401 刷新重试。

        6.16.0 Dart 接口分析确认 v5 命令方法语义化（PUT=执行、DELETE=取消/反向、
        POST=鸣笛、GET=轮询），各命令通道共用本封装避免重复 401 分支。
        """
        async with self._session.request(method, url, headers=self._headers()) as resp:
            try:
                text = await resp.text()
            except UnicodeDecodeError:
                raw_bytes = await resp.read()
                self._log.warning(
                    "FordPass %s -> 非文本响应 %d bytes (status=%s): %s",
                    url, len(raw_bytes), resp.status, raw_bytes[:80].hex(),
                )
                raise FordPassApiError(
                    resp.status, "网关返回非文本响应（瞬时异常，已按重试策略处理）"
                ) from None
            self._log.debug("FordPass v5 %s %s -> %s %s", what, method, resp.status, text[:300])
            if resp.status == 401 and self._refresh_token:
                if not self._retrying:
                    self._retrying = True
                    try:
                        new = await self.refresh_token(self._refresh_token)
                        self._access_token = new["access_token"]
                        if self.on_token_refresh is not None:
                            self.on_token_refresh(new["access_token"])
                        async with self._session.request(
                            method, url, headers=self._headers()
                        ) as resp2:
                            text2 = await resp2.text()
                            if resp2.status >= 400:
                                raise FordPassApiError(resp2.status, text2[:300], refresh_failed=(resp2.status == 401))
                            if not text2:
                                return {}
                            try:
                                return json.loads(text2)
                            except json.JSONDecodeError:
                                return {"raw": text2}
                    except Exception as exc:
                        self._log.error("FordPass v5 %s token refresh failed: %s", what, exc)
                        raise _as_refresh_failed(exc) from exc
                    finally:
                        self._retrying = False
                # v5.3.2: 已有协程正在刷新（_retrying=True）——短等后重试本次请求，
                # 复用新 token（此前并发 401 时非首个协程直接抛错，多车账号该轮失败）
                await asyncio.sleep(0.5)
                async with self._session.request(
                    method, url, headers=self._headers()
                ) as resp2:
                    text2 = await resp2.text()
                    if resp2.status >= 400:
                        raise FordPassApiError(resp2.status, text2[:300], refresh_failed=(resp2.status == 401))
                    if not text2:
                        return {}
                    try:
                        return json.loads(text2)
                    except json.JSONDecodeError:
                        return {"raw": text2}
            if resp.status >= 400:
                raise FordPassApiError(resp.status, text[:300])
            if not text:
                return {}
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"raw": text}

    async def idul_lock(self, vin: str, doortype: str) -> dict[str, Any]:
        """DELETE /api/vehicles/v5/{vin}/door/{doortype}/lock — 分车门锁定（IDUL）。

        接口分析（福特派 6.16.0，2026-10-15）：
        FordRemoteControlApiService::sendIDULCommand = DELETE + doortype 路径
        参数，指定单门上锁（如 front-left=驾驶员门）。v5 网关明文 JSON（无
        wbsk 加密信封），返回 commandId → getIDULCommandState 轮询。
        doortype 按福特 v5 惯例：front-left/front-right/rear-left/rear-right；
        实测若个别车型枚举不同会返回网关明确报错（如实提示用户）。
        """
        url = V5_BASE_URL + PATH_V5_DOOR_LOCK.format(vin=vin, doortype=doortype)
        async with self._session.request("DELETE", url, headers=self._headers()) as resp:
            try:
                text = await resp.text()
            except UnicodeDecodeError:
                raw_bytes = await resp.read()
                self._log.warning(
                    "FordPass v5 IDUL -> 非文本响应 %d bytes (status=%s): %s",
                    len(raw_bytes), resp.status, raw_bytes[:80].hex(),
                )
                raise FordPassApiError(
                    resp.status, "网关返回非文本响应（瞬时异常）"
                ) from None
            self._log.debug("FordPass v5 IDUL %s DELETE -> %s %s", doortype, resp.status, text[:400])
            if resp.status == 401 and self._refresh_token:
                if not self._retrying:
                    self._retrying = True
                    try:
                        new = await self.refresh_token(self._refresh_token)
                        self._access_token = new["access_token"]
                        if self.on_token_refresh is not None:
                            self.on_token_refresh(new["access_token"])
                        async with self._session.request("DELETE", url, headers=self._headers()) as resp2:
                            text2 = await resp2.text()
                            if resp2.status >= 400:
                                raise FordPassApiError(resp2.status, text2[:300], refresh_failed=(resp2.status == 401))
                            if not text2:
                                return {}
                            try:
                                return json.loads(text2)
                            except json.JSONDecodeError:
                                return {"raw": text2}
                    except Exception as exc:
                        self._log.error("FordPass v5 IDUL token refresh failed: %s", exc)
                        raise _as_refresh_failed(exc) from exc
                    finally:
                        self._retrying = False
                # v5.3.2: 已有协程正在刷新（_retrying=True）——短等后重试本次请求，
                # 复用新 token（此前并发 401 时非首个协程直接抛错，多车账号该轮失败）
                await asyncio.sleep(0.5)
                async with self._session.request("DELETE", url, headers=self._headers()) as resp2:
                    text2 = await resp2.text()
                    if resp2.status >= 400:
                        raise FordPassApiError(resp2.status, text2[:300], refresh_failed=(resp2.status == 401))
                    if not text2:
                        return {}
                    try:
                        return json.loads(text2)
                    except json.JSONDecodeError:
                        return {"raw": text2}
            if resp.status >= 400:
                raise FordPassApiError(resp.status, text[:300])
            if not text:
                return {}
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"raw": text}

    async def idul_lock_status(self, vin: str, command_id: str) -> dict[str, Any]:
        """GET /api/vehicles/v5/{vin}/door/lock/{commandId} — IDUL 分车门命令状态。"""
        url = V5_BASE_URL + PATH_V5_DOOR_LOCK_STATUS.format(vin=vin, commandId=command_id)
        async with self._session.request("GET", url, headers=self._headers()) as resp:
            try:
                text = await resp.text()
            except UnicodeDecodeError:
                raw_bytes = await resp.read()
                self._log.warning(
                    "FordPass v5 IDUL status -> 非文本响应 %d bytes (status=%s)",
                    len(raw_bytes), resp.status,
                )
                raise FordPassApiError(resp.status, "网关返回非文本响应（瞬时异常）") from None
            self._log.debug("FordPass v5 IDUL status -> %s %s", resp.status, text[:400])
            if resp.status == 401 and self._refresh_token:
                if not self._retrying:
                    self._retrying = True
                    try:
                        new = await self.refresh_token(self._refresh_token)
                        self._access_token = new["access_token"]
                        if self.on_token_refresh is not None:
                            self.on_token_refresh(new["access_token"])
                        async with self._session.request("GET", url, headers=self._headers()) as resp2:
                            text2 = await resp2.text()
                            if resp2.status >= 400:
                                raise FordPassApiError(resp2.status, text2[:300], refresh_failed=(resp2.status == 401))
                            if not text2:
                                return {}
                            try:
                                return json.loads(text2)
                            except json.JSONDecodeError:
                                return {"raw": text2}
                    except Exception as exc:
                        self._log.error("FordPass v5 IDUL status token refresh failed: %s", exc)
                        raise _as_refresh_failed(exc) from exc
                    finally:
                        self._retrying = False
                # v5.3.2: 已有协程正在刷新（_retrying=True）——短等后重试本次请求，
                # 复用新 token（此前并发 401 时非首个协程直接抛错，多车账号该轮失败）
                await asyncio.sleep(0.5)
                async with self._session.request("GET", url, headers=self._headers()) as resp2:
                    text2 = await resp2.text()
                    if resp2.status >= 400:
                        raise FordPassApiError(resp2.status, text2[:300], refresh_failed=(resp2.status == 401))
                    if not text2:
                        return {}
                    try:
                        return json.loads(text2)
                    except json.JSONDecodeError:
                        return {"raw": text2}
            if resp.status >= 400:
                raise FordPassApiError(resp.status, text[:300])
            if not text:
                return {}
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"raw": text}

    async def v5_refresh_status(self, vin: str) -> dict[str, Any]:
        """PUT /api/vehicles/v5/{vin}/status — v5 主动刷新车况（v4.7.2）。

        接口行为分析：sendRefreshVehicleStatusRequest = PUT /status，返回
        commandId → GET /statusrefresh/{commandId} 轮询。实测（2026-10-15）
        网关当前对 PUT 命令返回 500 FIG 错误（与 IDUL 同因：v5 PUT/DELETE
        命令通道暂不可用，仅 POST honk 正常）——按钮点击会如实报错提示，
        网关恢复后自动可用。
        """
        url = V5_BASE_URL + PATH_V5_STATUS.format(vin=vin)
        async with self._session.request("PUT", url, headers=self._headers()) as resp:
            try:
                text = await resp.text()
            except UnicodeDecodeError:
                raw_bytes = await resp.read()
                self._log.warning(
                    "FordPass v5 status -> 非文本响应 %d bytes (status=%s)",
                    len(raw_bytes), resp.status,
                )
                raise FordPassApiError(resp.status, "网关返回非文本响应（瞬时异常）") from None
            self._log.debug("FordPass v5 status PUT -> %s %s", resp.status, text[:400])
            if resp.status == 401 and self._refresh_token:
                if not self._retrying:
                    self._retrying = True
                    try:
                        new = await self.refresh_token(self._refresh_token)
                        self._access_token = new["access_token"]
                        if self.on_token_refresh is not None:
                            self.on_token_refresh(new["access_token"])
                        async with self._session.request("PUT", url, headers=self._headers()) as resp2:
                            text2 = await resp2.text()
                            if resp2.status >= 400:
                                raise FordPassApiError(resp2.status, text2[:300], refresh_failed=(resp2.status == 401))
                            if not text2:
                                return {}
                            try:
                                return json.loads(text2)
                            except json.JSONDecodeError:
                                return {"raw": text2}
                    except Exception as exc:
                        self._log.error("FordPass v5 status token refresh failed: %s", exc)
                        raise _as_refresh_failed(exc) from exc
                    finally:
                        self._retrying = False
                # v5.3.2: 已有协程正在刷新（_retrying=True）——短等后重试本次请求，
                # 复用新 token（此前并发 401 时非首个协程直接抛错，多车账号该轮失败）
                await asyncio.sleep(0.5)
                async with self._session.request("PUT", url, headers=self._headers()) as resp2:
                    text2 = await resp2.text()
                    if resp2.status >= 400:
                        raise FordPassApiError(resp2.status, text2[:300], refresh_failed=(resp2.status == 401))
                    if not text2:
                        return {}
                    try:
                        return json.loads(text2)
                    except json.JSONDecodeError:
                        return {"raw": text2}
            if resp.status >= 400:
                raise FordPassApiError(resp.status, text[:300])
            if not text:
                return {}
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"raw": text}

    async def v5_refresh_status_poll(self, vin: str, command_id: str) -> dict[str, Any]:
        """GET /api/vehicles/v5/{vin}/statusrefresh/{commandId} — v5 刷新命令轮询。"""
        url = V5_BASE_URL + PATH_V5_STATUSREFRESH.format(vin=vin, commandId=command_id)
        async with self._session.request("GET", url, headers=self._headers()) as resp:
            try:
                text = await resp.text()
            except UnicodeDecodeError:
                raw_bytes = await resp.read()
                self._log.warning(
                    "FordPass v5 statusrefresh -> 非文本响应 %d bytes (status=%s)",
                    len(raw_bytes), resp.status,
                )
                raise FordPassApiError(resp.status, "网关返回非文本响应（瞬时异常）") from None
            self._log.debug("FordPass v5 statusrefresh -> %s %s", resp.status, text[:400])
            if resp.status == 401 and self._refresh_token:
                if not self._retrying:
                    self._retrying = True
                    try:
                        new = await self.refresh_token(self._refresh_token)
                        self._access_token = new["access_token"]
                        if self.on_token_refresh is not None:
                            self.on_token_refresh(new["access_token"])
                        async with self._session.request("GET", url, headers=self._headers()) as resp2:
                            text2 = await resp2.text()
                            if resp2.status >= 400:
                                raise FordPassApiError(resp2.status, text2[:300], refresh_failed=(resp2.status == 401))
                            if not text2:
                                return {}
                            try:
                                return json.loads(text2)
                            except json.JSONDecodeError:
                                return {"raw": text2}
                    except Exception as exc:
                        self._log.error("FordPass v5 statusrefresh token refresh failed: %s", exc)
                        raise _as_refresh_failed(exc) from exc
                    finally:
                        self._retrying = False
                # v5.3.2: 已有协程正在刷新（_retrying=True）——短等后重试本次请求，
                # 复用新 token（此前并发 401 时非首个协程直接抛错，多车账号该轮失败）
                await asyncio.sleep(0.5)
                async with self._session.request("GET", url, headers=self._headers()) as resp2:
                    text2 = await resp2.text()
                    if resp2.status >= 400:
                        raise FordPassApiError(resp2.status, text2[:300], refresh_failed=(resp2.status == 401))
                    if not text2:
                        return {}
                    try:
                        return json.loads(text2)
                    except json.JSONDecodeError:
                        return {"raw": text2}
            if resp.status >= 400:
                raise FordPassApiError(resp.status, text[:300])
            if not text:
                return {}
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"raw": text}

    async def honk_cancel_command(self, vin: str) -> dict[str, Any]:
        """DELETE /api/vehicles/v5/{vin}/honk — 鸣笛寻车「关」通道。

        与 App 一致：FordHonkCancelCommand → honkCancelCommand →
        sendHonkCancelCommand（DELETE，无 body）。
        若服务器当前无进行中鸣笛，DELETE 返回 200 + commandId（无害幂等）。
        """
        url = V5_BASE_URL + PATH_V5_HONK.format(vin=vin)
        async with self._session.request(
            "DELETE", url, headers=self._headers()
        ) as resp:
            try:
                text = await resp.text()
            except UnicodeDecodeError:
                # v3.0.9: 与 _request 的 v3.0.6 保护对齐——网关偶发返回
                # 非文本响应（二进制/损坏 body）时先读原始字节诊断，
                # 再转成明确的 FordPassApiError，避免裸 codec 错误冒泡。
                raw_bytes = await resp.read()
                self._log.warning(
                    "FordPass %s -> 非文本响应 %d bytes (status=%s): %s",
                    url, len(raw_bytes), resp.status, raw_bytes[:80].hex(),
                )
                raise FordPassApiError(
                    resp.status, "网关返回非文本响应（瞬时异常，已按重试策略处理）"
                ) from None
            self._log.debug("FordPass v5 honk DELETE -> %s %s", resp.status, text[:500])
            if resp.status == 401 and self._refresh_token:
                if not self._retrying:
                    self._retrying = True
                    try:
                        new = await self.refresh_token(self._refresh_token)
                        self._access_token = new["access_token"]
                        if self.on_token_refresh is not None:
                            self.on_token_refresh(new["access_token"])
                        async with self._session.request(
                            "DELETE", url, headers=self._headers()
                        ) as resp2:
                            text2 = await resp2.text()
                            if resp2.status >= 400:
                                raise FordPassApiError(resp2.status, text2[:300], refresh_failed=(resp2.status == 401))
                            if not text2:
                                return {}
                            try:
                                return json.loads(text2)
                            except json.JSONDecodeError:
                                return {"raw": text2}
                    except Exception as exc:
                        self._log.error("FordPass v5 honk token refresh failed: %s", exc)
                        raise _as_refresh_failed(exc) from exc
                    finally:
                        self._retrying = False
                # v5.3.2: 已有协程正在刷新（_retrying=True）——短等后重试本次请求，
                # 复用新 token（此前并发 401 时非首个协程直接抛错，多车账号该轮失败）
                await asyncio.sleep(0.5)
                async with self._session.request(
                    "DELETE", url, headers=self._headers()
                ) as resp2:
                    text2 = await resp2.text()
                    if resp2.status >= 400:
                        raise FordPassApiError(resp2.status, text2[:300], refresh_failed=(resp2.status == 401))
                    if not text2:
                        return {}
                    try:
                        return json.loads(text2)
                    except json.JSONDecodeError:
                        return {"raw": text2}
            if resp.status >= 400:
                raise FordPassApiError(resp.status, text[:300])
            if not text:
                return {}
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"raw": text}

    async def announcestatus(self, vin: str, command_id: str) -> dict[str, Any]:
        """GET /api/vehicles/v5/{vin}/announcestatus/{commandId}/ — 鸣笛命令状态。

        v3.1.13 从福特派 6.16.0 客户端（Dart 对象池）整理：App
        FordRemoteControlApiService 的 v5 命令轮询组（statusrefresh/{commandId} /
        engine/start/{commandId} / doors/lock/{commandId} / announcestatus/{commandId}
        并列）——鸣笛（POST honk）返回 commandId 后，以此查询命令执行结果。
        与 honk 同为 v5 明文通道（无 wbsk 加密信封）。
        """
        url = V5_BASE_URL + PATH_V5_ANNOUNCE_STATUS.format(
            vin=vin, command_id=command_id
        )
        async with self._session.request("GET", url, headers=self._headers()) as resp:
            try:
                text = await resp.text()
            except UnicodeDecodeError:
                raw_bytes = await resp.read()
                self._log.warning(
                    "FordPass %s -> 非文本响应 %d bytes (status=%s): %s",
                    url, len(raw_bytes), resp.status, raw_bytes[:80].hex(),
                )
                raise FordPassApiError(
                    resp.status, "网关返回非文本响应（瞬时异常，已按重试策略处理）"
                ) from None
            self._log.debug(
                "FordPass v5 announcestatus -> %s %s", resp.status, text[:500]
            )
            if resp.status == 401 and self._refresh_token:
                if not self._retrying:
                    self._retrying = True
                    try:
                        new = await self.refresh_token(self._refresh_token)
                        self._access_token = new["access_token"]
                        if self.on_token_refresh is not None:
                            self.on_token_refresh(new["access_token"])
                        async with self._session.request(
                            "GET", url, headers=self._headers()
                        ) as resp2:
                            text2 = await resp2.text()
                            if resp2.status >= 400:
                                raise FordPassApiError(resp2.status, text2[:300], refresh_failed=(resp2.status == 401))
                            if not text2:
                                return {}
                            try:
                                return json.loads(text2)
                            except json.JSONDecodeError:
                                return {"raw": text2}
                    except Exception as exc:
                        self._log.error(
                            "FordPass v5 announcestatus token refresh failed: %s", exc
                        )
                        raise _as_refresh_failed(exc) from exc
                    finally:
                        self._retrying = False
                # v5.3.2: 已有协程正在刷新（_retrying=True）——短等后重试本次请求，
                # 复用新 token（此前并发 401 时非首个协程直接抛错，多车账号该轮失败）
                await asyncio.sleep(0.5)
                async with self._session.request(
                    "GET", url, headers=self._headers()
                ) as resp2:
                    text2 = await resp2.text()
                    if resp2.status >= 400:
                        raise FordPassApiError(resp2.status, text2[:300], refresh_failed=(resp2.status == 401))
                    if not text2:
                        return {}
                    try:
                        return json.loads(text2)
                    except json.JSONDecodeError:
                        return {"raw": text2}
            if resp.status >= 400:
                raise FordPassApiError(resp.status, text[:300])
            if not text:
                return {}
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"raw": text}

    async def capability_v4(self, vin: str) -> dict[str, Any]:
        """车辆能力清单 v4（cvfeatures）——端点实测迭代。

        v3.1.13 对齐福特派 6.16.0 端点：App VcsRepositoryProvider::
        fetchCapabilityV4 → GET /api/cnxapi-vds/v4/vehicles/cvfeatures（首页
        能力卡片 PAAK/EV 管理/哨兵/灯光等的权威来源）。
        v3.1.14 实测 GET（appKey query）→ 404；v3.1.15 实测 GET（encryptedVin/
        xjw + R3 签名，与 ccfeatures(v2) 同通道）→ 仍 404——疑 APIM 路由仅接受
        POST 或该路径在当前网关版本未开放；v3.1.16 试 POST（encryptedVin/xjw
        body + R3 签名）。失败如实报错（实体显示错误原因，0 unavailable）。
        """
        enc_vin, xjw = await asyncio.to_thread(
            lambda: self.crypto.encrypt_field(vin)
        )
        return await self._request(
            "POST", PATH_CVFEATURES, body={"encryptedVin": enc_vin, "xjw": xjw}
        )

    async def parking_image(self, vin: str, car_id: str) -> dict[str, Any]:
        """POST /api/cnxapi-pds/v1/search-vehicle-parking-image — 停车影像查询。

        v3.1.13 对齐：App VehicleManagerEndpoint.searchVehicleParkingImage，
        请求体含 carId（= 车辆列表 encryptedCarId）。部分车型 encryptedCarId=null
        → 集成不创建按钮、不调用。响应含图片记录列表（车型支持时）。
        """
        return await self._request(
            "POST", PATH_PARKING_IMAGE, {"carId": car_id, "vin": vin}
        )

    async def monitor_traffic(self, vin: str, car_id: str) -> dict[str, Any]:
        """POST /api/cnxapi-pds/v1/search-vehicle-monitor-traffic — 行车监控查询。

        App VehicleManagerEndpoint.searchVehicleMonitorTraffic，与停车影像
        同组（PDS 网关），请求体含 carId。部分车型无该硬件 → 不创建实体。
        """
        return await self._request(
            "POST", PATH_MONITOR_TRAFFIC, {"carId": car_id, "vin": vin}
        )

    async def save_honk_settings(
        self,
        vin: str,
        duration: int = DEFAULT_HONK_DURATION,
        chirp_type: int = 3,
    ) -> dict[str, Any]:
        """保存鸣笛寻车设置到福特账户云端（v3.2.2，字段对齐 App 官方协议）。

        v3.1.4 UserPreferenceV2 真通道；v3.2.2 修正字段与值（实机样本实证
        2026-10-03 福特派 6.16.0）——App 保存/读取用 AnnouncementType +
        Duration（值=枚举数字字符串），旧槽位 vehicleAnnouncementSoundType/
        vehicleAnnouncementDuration 仅历史残留、App 不读（此前写旧槽位
        导致「保存设置无效」根因）：
        POST /api/cnxapi-pds/v1/user/preference-by-groups
        body: {preferenceGroups: [{groupName: "VehicleAnnouncementSetting",
               userPreferences: [{preferenceType: "AnnouncementType",
                                  preferenceValue: "<0-4 枚举数字>"},
                                 {preferenceType: "Duration",
                                  preferenceValue: "<5-20>"}]}], timestamp, sign}
        - 顶层不包 encryptedVin/xjw（DTO 拒绝未知字段，实测 400）
        - sign = compute_sign({preferenceGroups, timestamp})（R3 双重 +
          嵌套序列化：list→[a&b]、dict→{k=v}）——200 保存成功；
          GET preference-list 回读确认（AnnouncementType/Duration 槽位）。
        旧 RCC profile-by-vin 通道（CHIRP_TYPE）已废弃（其 100400 sign 为
        R2 独立密钥体系；真实通道即本 UserPreferenceV2）。
        """
        announce = CHIRP_TO_ANNOUNCE.get(
            CHIRP_TYPE_OPTIONS[chirp_type - 1] if 0 < chirp_type <= 5 else DEFAULT_CHIRP_TYPE,
            "chirpHonk",
        )
        groups = [
            {
                "groupName": GROUP_VEHICLE_ANNOUNCEMENT,
                "userPreferences": [
                    {"preferenceType": PREF_SOUND_TYPE, "preferenceValue": announce},
                    {"preferenceType": PREF_DURATION, "preferenceValue": str(duration)},
                ],
            }
        ]
        body = {"preferenceGroups": groups}
        data = await self._request("POST", PATH_USER_PREF_GROUPS, body=body)
        if isinstance(data, dict) and data.get("status") not in (None, 200):
            raise FordPassApiError(data.get("errorCode"), data.get("error"))
        return {"cloud": True, "announce": announce, "duration": duration}

    async def save_remote_climate_target_temp(self, temp: int) -> dict[str, Any]:
        """保存远程空调目标温度到福特账户云端（v3.4.5，候选通道）。

        与鸣笛设置同构（UserPreferenceV2 preference-by-groups）：
        POST /api/cnxapi-pds/v1/user/preference-by-groups
        body: {preferenceGroups: [{groupName: "RemoteClimateSetting",
               userPreferences: [{preferenceType: "TargetTemp",
                                  preferenceValue: "<摄氏度整数字符串>"}]}],
              timestamp, sign}
        顶层不包 encryptedVin/xjw（DTO 拒绝未知字段）；sign 由 _request 自动
        附加（R3 双重 + 嵌套序列化，与鸣笛保存同一实现）。组名/类型为按
        App 命名风格推断的候选值，待电马/插混账号实机样本实测校准——保存失败
        只记日志，不影响实体可用性（实体恒可用）。
        """
        groups = [
            {
                "groupName": GROUP_REMOTE_CLIMATE,
                "userPreferences": [
                    {"preferenceType": PREF_TARGET_TEMP, "preferenceValue": str(temp)},
                ],
            }
        ]
        body = {"preferenceGroups": groups}
        data = await self._request("POST", PATH_USER_PREF_GROUPS, body=body)
        if isinstance(data, dict) and data.get("status") not in (None, 200):
            raise FordPassApiError(data.get("errorCode"), data.get("error"))
        return {"cloud": True, "target_temp": temp}

    async def get_remote_climate_preference(self) -> dict[str, Any]:
        """读取福特账户云端的远程空调目标温度（v3.4.5，候选组名）。

        GET /api/cnxapi-pds/v1/user/preference-list?timestamp=&sign=（与鸣笛
        设置同一查询通道）。返回 {preferenceType: preferenceValue} 或 {}。
        v4.0.12: 账户级共享缓存（TTL 3600s）——多车账号 setup 探测只发一次。
        """
        return await self._account_cached(
            "remote_climate_preference", 3600, self._remote_climate_fetch
        )

    async def _remote_climate_fetch(self) -> dict[str, Any]:
        try:
            data = await self._request("GET", PATH_USER_PREF_LIST, query={})
        except FordPassApiError:
            return {}
        payload = data.get("data", {}) if isinstance(data, dict) else {}
        groups = payload.get("groups") if isinstance(payload, dict) else None
        if not isinstance(groups, list):
            return {}
        for group in groups:
            if isinstance(group, dict) and group.get("groupName") == GROUP_REMOTE_CLIMATE:
                prefs = group.get("userPreferences") or []
                out: dict[str, str] = {}
                for pref in prefs:
                    if isinstance(pref, dict):
                        out[pref.get("preferenceType")] = pref.get("preferenceValue")
                return out
        return {}

    async def get_chirp_preference(self) -> dict[str, Any]:
        """读取福特账户云端的鸣笛寻车设置（v3.1.4，UserPreferenceV2 查询）。

        GET /api/cnxapi-pds/v1/user/preference-list?timestamp=&sign=
        （仅两键——不带 encryptedVin/xjw；2026-10-02 实测 200 回读
        VehicleAnnouncementSetting 组已存值）。
        返回 {groupName, userPreferences: [...]} 或 {}（无数据）。
        v4.0.12: 账户级共享缓存（TTL 60s）——多车账号每车每轮只发一次请求，
        其余车复用；App 改动后 ≤60s 内 HA 同步（原"每轮真拉"语义近似保留）。
        """
        return await self._account_cached("chirp_preference", 60, self._chirp_pref_fetch)

    async def _chirp_pref_fetch(self) -> dict[str, Any]:
        try:
            data = await self._request("GET", PATH_USER_PREF_LIST, query={})
        except FordPassApiError:
            return {}
        payload = data.get("data", {}) if isinstance(data, dict) else {}
        groups = payload.get("groups") if isinstance(payload, dict) else None
        if not isinstance(groups, list):
            return {}
        for group in groups:
            if isinstance(group, dict) and group.get("groupName") == GROUP_VEHICLE_ANNOUNCEMENT:
                prefs = group.get("userPreferences") or []
                out: dict[str, str] = {}
                for pref in prefs:
                    if isinstance(pref, dict):
                        out[pref.get("preferenceType")] = pref.get("preferenceValue")
                return out
        return {}

    # ------------------------------------------------------------ service info
    async def _get_signed(
        self,
        path: str,
        vin: str,
        query: dict[str, Any] | None = None,
        method: str = "GET",
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """R3 compute_sign + 修正 headers + 无 appKey 的标准请求（v3.1.4）。

        2026-10-02 实测：maintenance-plan / recall / sim/info / wifi/status
        均 200（headers 必须用 App 真实无连字符名，且 query 必须带
        encryptedVin/xjw——见 probe_r2f）。

        v3.5.5: 支持 POST + body（探测多形态自适应：部分端点 GET 404、
        实际为 POST-only 路由，如 cnxapi-message/cnxapi-vds 族）。
        """
        q = dict(query or {})
        enc_vin, xjw = await asyncio.to_thread(
            lambda: self.crypto.encrypt_field(vin)
        )
        q["encryptedVin"] = enc_vin
        q["xjw"] = xjw
        if method == "POST":
            # v3.5.7: POST body 只补 encryptedVin/xjw（所有端点都要）。
            # clientType/appVersion 仅 uservehicles 接受（日志实证其余端点
            # 报 Unrecognized field "clientType" 400）——由该端点 body 模板
            # 显式提供，不通用补。body 模板含 "vin": None 时替换为明文
            # VIN（departuretimes/chargelogs）。
            b = dict(body or {})
            b.setdefault("encryptedVin", enc_vin)
            b.setdefault("xjw", xjw)
            if b.get("vin") is None and "vin" in b:
                b["vin"] = vin
            if b.get("__skip_xjw__"):
                # v3.6.6: cevs 域 DTO 连 xjw 也不接受（v3.6.5 日志：body 只留
                # vin+xjw 仍 Impossible modulus）——body 只留明文 vin
                b.pop("xjw", None)
            if b.pop("__skip_encrypted_vin__", None):
                # v3.5.7: OTARedDotStatusRequestDTO 不接受 encryptedVin
                # （日志实证 Unrecognized field）——body 只留 xjw
                b.pop("encryptedVin", None)
            data = await self._request("POST", path, body=b)
        else:
            data = await self._request("GET", path, query=q)
        return data if isinstance(data, dict) else {"raw": data}

    async def get_maintenance_plan(self, vin: str) -> dict[str, Any]:
        """保养计划：GET /api/cnxapi-cds/v1/maintenance-plan（200 实测）。"""
        return await self._get_signed(PATH_MAINTENANCE_PLAN, vin)

    async def get_recall(self, vin: str) -> dict[str, Any]:
        """召回信息：GET /api/cnxapi-vds/v1/vehicles/recall（200 实测）。"""
        return await self._get_signed(PATH_RECALL, vin)

    # ------------------------------------------------------------ 未读消息（v3.1.17）
    async def get_messages_summary(self) -> dict[str, Any]:
        """未读消息摘要：GET /api/cnxapi-message/app/messages/summary（实机样本实测 200）。

        2026-10-03 实测确认：无 encryptedVin 参数，仅 timestamp+sign 签名
        （_request 自动附加）。响应 data.summary.{allRedDotStatus,
        unReadCategoryId, unReadCategoryDescription, readMessageSubject}
        + data.categories[]。
        v4.0.12: 账户级共享缓存（TTL 1800s，与 coordinator 调用一致）——
        多车账号下多车共用同一份消息摘要，只发一次请求。
        """
        return await self._account_cached(
            "messages_summary", 1800, lambda: self._request("GET", PATH_MESSAGES_SUMMARY)
        )

    # ------------------------------------------------------------ 空调滤芯（v3.1.7）
    async def get_air_filter_status(self, vin: str) -> dict[str, Any]:
        """空调滤芯状态：GET /api/cnxapi-vds/v1/aar/status（200 实测）。

        实测响应（2026-10-02）：
          {"status":200,"version":"1.0","data":{
             "xjw":"...",
             "airFilter":{"isHealthy":true,
                          "lastReplaceTimestamp":1779615729937,
                          "lastReplaceTime":"2026/05/24"},
             "encryptedVin":"..."}}
        isHealthy=false 表示滤芯需更换；lastReplaceTime 为上次重置（更换）日期。
        """
        return await self._get_signed(PATH_AAR_STATUS, vin)

    async def reset_air_filter(self, vin: str) -> dict[str, Any]:
        """重置空调滤芯：PUT /api/cnxapi-vds/v1/aar/status（200 实测）。

        body = {channel:"IVI", filterStatus:0, xjw, encryptedVin} + sign；
        实测 200 {"status":200,"version":"1.0","data":"success"}，随后
        GET aar/status 的 lastReplaceTime 更新为当天（重置生效）。
        filterStatus 仅接受 0/1；channel 必须为 "IVI"（其余渠道触发 BESL
        103400 illegal channel）。
        """
        enc_vin, xjw = await asyncio.to_thread(
            lambda: self.crypto.encrypt_field(vin)
        )
        body = {
            "channel": "IVI",
            "filterStatus": 0,
            "xjw": xjw,
            "encryptedVin": enc_vin,
        }
        data = await self._request("PUT", PATH_AAR_STATUS, body=body)
        return data if isinstance(data, dict) else {"raw": data}

    async def get_ccfeatures(self, vin: str) -> dict[str, Any]:
        """云端能力+服务信息：GET /api/cnxapi-vds/v2/vehicles/ccfeatures（v3.1.9，200 实测）。

        实测响应（2026-10-02，实测）：
          {"status":200,"data":{
             "rsaNumber":"4006501668", "contactNumber":"4009213266",
             "afterSalesNumber":"4000031111",
             "videoManualUrl":"https://h5fya.fuyu.club/manual/escape/index.html",
             "featureData":{"availableFeatures":"03,04,05,06,07,08,10,11,23",
                            "authedFeatures":"A4", "specifiedFeatures":null},
             "sign":"...","timestamp":...}}
        featureData.availableFeatures 是云端能力位图（数字编码，含义逐车型确认）；
        rsa/contact/afterSalesNumber 为救援/客服/售后电话；videoManualUrl 为
        车型电子说明书 H5。App 用该接口判断每辆车支持哪些功能。
        """
        return await self._get_signed(PATH_CCFEATURES, vin)

    async def get_prognostic(self, vin: str) -> dict[str, Any]:
        """预测性诊断：GET /api/cnxapi-cds/prognostic/v1/list（v3.1.9，200 实测）。

        实测响应（2026-10-02，实测）：
          {"status":200,"data":{
             "featureType":"OL", "confidenceLevel":2, "shouldShow":false,
             "urgency":"N", "messageDesc":null, "messageCode":null,
             "eventTime":"2026/10/02 15:09:15", "iolm":41, "remainingKMs":4000,
             "dateOnZero":"2027/02", "tiresWithSlowLeak":null, ...}}
        - featureType "OL" = Oil Life（机油寿命）；iolm 为寿命百分比（41）；
        - remainingKMs 为按当前寿命剩余可行驶里程（4000 km）；
        - dateOnZero 为寿命归零（需保养）的预计月份；
        - tiresWithSlowLeak 为慢漏气胎标识（null=无）；urgency/messageDesc 为
          需展示的诊断提示（urgency 枚举，messageDesc 为文案）。
        """
        return await self._get_signed(PATH_PROGNOSTIC, vin)

    async def get_sim_info(self, vin: str) -> dict[str, Any]:
        """SIM 卡信息：GET /api/cnxapi-cds/v1/vehicle/sim/info（200 实测）。

        v4.3.0: 部分车型（纯油）该端点确定性业务拒绝
        （HTTP 200 但 data.returnSuccess=false / returnErrCode=402
        CONS.SYS.0002）——识别后抛 FordPassProbeRejected，由
        coordinator._cached_fetch 落盘 None，TTL 内不再真拉
        （防风控 + 去日志噪音）。
        """
        data = await self._get_signed(PATH_SIM_INFO, vin)
        inner = data.get("data") if isinstance(data, dict) else None
        if isinstance(inner, dict) and inner.get("returnSuccess") is False:
            code = f"{inner.get('returnErrCode', '')} {inner.get('returnErrMsg', '')}".strip()
            raise FordPassProbeRejected(
                inner.get("returnStatus", 200), f"sim/info business rejected: {code}"
            )
        return data

    async def get_wifi_status(self, vin: str) -> dict[str, Any] | None:
        """WiFi 热点状态：GET /api/cnxapi-cds/v1/vehicle/wifi/status（200 实测）。

        v3.8.8: 跟随状态刷新间隔每轮刷新（用户要求），失败保留旧值。
        v4.3.0: 确定性业务拒绝（returnSuccess=false / CONS.AUTH.0005）
        识别——降噪（同类型 1 小时限频一次 INFO）并返回 None，
        coordinator 保留最后已知状态（拒绝 ≠ 实体显示不可用）。
        v6.0.3: 该拒绝是确定性的（纯油车型无车联网热点服务，永远不会
        成功），改抛 FordPassProbeRejected，由 coordinator 落拒绝标记
        长缓存——TTL 内不再每轮白打请求（日志噪音同除）；成功路径
        行为不变（仍每轮真拉）。
        """
        data = await self._get_signed(PATH_WIFI_STATUS, vin)
        inner = data.get("data") if isinstance(data, dict) else None
        if isinstance(inner, dict) and inner.get("returnSuccess") is False:
            code = f"{inner.get('returnErrCode', '')} {inner.get('returnErrMsg', '')}".strip()
            raise FordPassProbeRejected(
                inner.get("returnStatus", 200), f"wifi/status business rejected: {code}"
            )
        return data

    async def get_warranty(self, vin: str) -> dict[str, Any]:
        """质保信息：GET /api/cnxapi-cds/v1/warranty。

        签名已过（R3 无 appKey），但服务端 100502 Validation exception
        （参数校验，App 请求参数尚未完全对齐，待实车实测补充）。
        """
        return await self._get_signed(PATH_WARRANTY, vin)

    async def wait_command_complete(
        self, vin: str, command_id: str, command_type: str,
        timeout: float = 60.0, poll: float = 3.0,
    ) -> tuple[bool, dict[str, Any] | None]:
        """Poll command-execution-status until ForceRefresh finishes (v2.7.1).
        v3.8.0: poll 2s→5s（单命令最多 12 次而非 30 次，防福特云限流）。
        v5.8.3: poll 5s→3s——门锁等操作感知从 ~30s 降到 ~15s 内（App 实测
        间隔 ~2.2s）；仅命令执行期短暂轮询（60s 上限 20 次），非持续高频，
        风控风险可控，与 App 节奏对齐。

        Verified from a live capture (2026-09-29): after
        send-command returns a commandId, the gateway polls
        /vehicles/command-execution-status?commandType=..&commandId=..
        every ~2.2 s.  While the command is still running the decrypted
        vehiclestatus is a placeholder (every field status=LAST_KNOWN,
        timestamp=01-01-0001, vin=null); once it finishes every field flips
        to status=CURRENT with the real timestamp AND the response already
        carries the complete fresh snapshot.

        Returns ``(completed, latest_result)`` where latest_result is the
        decrypted command-execution-status body (contains vehiclestatus).
        """
        start = time.monotonic()
        last: dict[str, Any] | None = None
        while time.monotonic() - start < timeout:
            try:
                result = await self.command_status(vin, command_id, command_type)
                if not isinstance(result, dict):
                    # P2-1（v5.6.0）：非 dict 响应同样等待 poll 再继续——
                    # 直接 continue 会跳过循环末尾 sleep，60 秒满速打接口。
                    await asyncio.sleep(poll)
                    continue
                last = result
                vs = result.get("vehiclestatus", result)
                if (
                    isinstance(vs, dict)
                    and vs.get("vin")
                    # fresh snapshot: key fields carry status=CURRENT
                    and any(
                        isinstance(v, dict) and v.get("status") == "CURRENT"
                        for v in vs.values()
                    )
                ):
                    return True, result
            except Exception as exc:  # noqa: BLE001 - keep polling on transient errors
                self._log.debug("wait_command_complete poll failed: %s", exc)
            await asyncio.sleep(poll)
        return False, last

    async def command_status(
        self, vin: str, command_id: str, command_type: str
    ) -> dict[str, Any]:
        enc_vin, xjw = await asyncio.to_thread(lambda: self.crypto.encrypt_field(vin))
        data = await self._request(
            "GET",
            PATH_COMMAND_STATUS,
            query={
                "commandType": command_type,
                "commandId": command_id,
                "encryptedVin": enc_vin,
                "xjw": xjw,
            },
        )
        inner = data.get("data", {})
        if inner.get("encryptedResponseBody"):
            plain = await asyncio.to_thread(lambda: self.crypto.decrypt_field(inner["encryptedResponseBody"], inner["xjw"]))
            return json.loads(plain)
        return inner

    # ------------------------------------------------------------ alerts
    async def get_active_alerts(self, vin: str) -> list[dict[str, Any]]:
        """GET /api/cnxapi-cds/v1/vha/activealert (明文中文告警).

        Verified from a live capture (2026-09-29): this endpoint
        takes the usual encrypted VIN + random xjw, reuses the
        main auth-token header, and — like every other gateway call — carries
        ``timestamp`` + ``sign`` (a bare GET without them is 404'd by the
        server since 2026-09-29, fixed in v2.7.4).  The vha service uses its
        own appversion (1.0.0).  Response ``data.VehicleAlertResponseList[].ActiveAlerts[]``
        carries plaintext Chinese headlines like 胎压监测系统警告.
        """
        enc_vin, xjw = await asyncio.to_thread(lambda: self.crypto.encrypt_field(vin))
        params = {
            "hmiPreferredLanguage": "zh-cn",
            "preferredLanguage": "zh-cn",
            "source": "TCU",
            "encryptedVin": enc_vin,
            "xjw": xjw,
        }
        data = await self._request(
            "GET",
            PATH_ACTIVE_ALERT,
            query=params,
            headers={"appversion": "1.0.0"},  # the vha service's own appversion
        )
        alerts: list[dict[str, Any]] = []
        for entry in data.get("data", {}).get("VehicleAlertResponseList", []) or []:
            for item in entry.get("ActiveAlerts", []) or []:
                if isinstance(item, dict) and item.get("Headline"):
                    alerts.append(
                        {
                            "headline": item.get("Headline"),
                            "severity": (item.get("Metadata") or {}).get("severity"),
                            "wilCode": (item.get("Metadata") or {}).get("wilCode"),
                            "body": item.get("Body") or "",
                            "eventTime": entry.get("EventTimeStamp"),
                        }
                    )
        return alerts

    async def probe_active_alert_list(self, vin: str) -> dict[str, Any] | None:
        """探测 /api/v1/activealertlist 车辆主动告警列表（只读/幂等，v4.3.4）。

        6.16.0 228 端点全量比对后唯一未接入的只读业务端点——
        App 首页告警聚合（与 vha/activealert 并列的旧版独立端点，结构化
        告警列表）。标准 R3 签名（timestamp+sign+encryptedVin/xjw），只读
        不触发动作。返回原始响应供实体按"探测成功才创建"接入：HTTP 200
        业务成功即视为该车型支持（空列表显示「无告警」，与 SIM 卡传感器
        v4.3.2 无条件创建教训一致——避免创建条件过严致实体缺失）。
        """
        enc_vin, xjw = await asyncio.to_thread(lambda: self.crypto.encrypt_field(vin))
        params = {
            "encryptedVin": enc_vin,
            "xjw": xjw,
            "hmiPreferredLanguage": "zh-cn",
            "preferredLanguage": "zh-cn",
        }
        try:
            data = await self._request(
                "GET", PATH_ACTIVE_ALERT_LIST, query=params
            )
        except FordPassApiError as exc:
            # v4.5.8: HTTP 404 = 确定性无该服务——转 ProbeRejected 落盘
            # None（TTL 内不再重探），此前每次 setup 重探 1 次 404 噪音
            if exc.code in (404,):
                raise FordPassProbeRejected(exc.code, str(exc)) from exc
            self._log.debug("FordPass activealertlist failed: %s", exc)
            return None
        except Exception as exc:  # noqa: BLE001 - 探测失败只记录
            self._log.debug("FordPass activealertlist failed: %s", exc)
            return None
        if isinstance(data, dict):
            if "errorCode" in data or "error" in data:
                self._log.info(
                    "FordPass activealertlist -> (rejected: %s)",
                    json.dumps(data, ensure_ascii=False)[:200],
                )
                return None
            self._log.info(
                "FordPass activealertlist probe -> %s",
                json.dumps(data, ensure_ascii=False)[:800],
            )
            return {"signed": True, "data": data}
        if data is not None:
            self._log.info("FordPass activealertlist probe -> raw %s", str(data)[:200])
            return {"signed": True, "raw": str(data)[:200]}
        return None

    # -------------------------------------------------- lincoln exclusive
    async def _probe_lincoln_get(self, path: str, tag: str) -> dict[str, Any] | None:
        """林肯专属只读 GET 探测（v4.8.1，林肯之道 5.8.0 实测）。

        动词未实测配对（静态 100%/动态 0%），按只读 GET 探测：HTTP 200
        业务成功视为该品牌账号有数据（探测到才创建）；404 = 确定性无该
        服务转 ProbeRejected 落盘（TTL 内不重探）；其它异常降噪返回 None。
        """
        try:
            data = await self._request("GET", path)
        except FordPassApiError as exc:
            if exc.code in (404,):
                raise FordPassProbeRejected(exc.code, str(exc)) from exc
            self._log.debug("Lincoln %s failed: %s", tag, exc)
            return None
        except Exception as exc:  # noqa: BLE001 - 探测失败只记录
            self._log.debug("Lincoln %s failed: %s", tag, exc)
            return None
        if isinstance(data, dict):
            if "errorCode" in data or "error" in data:
                self._log.info(
                    "Lincoln %s -> (rejected: %s)",
                    tag, json.dumps(data, ensure_ascii=False)[:200],
                )
                return None
            self._log.info(
                "Lincoln %s probe -> %s",
                tag, json.dumps(data, ensure_ascii=False)[:800],
            )
            return {"signed": True, "data": data}
        if data is not None:
            self._log.info("Lincoln %s probe -> raw %s", tag, str(data)[:200])
            return {"signed": True, "raw": str(data)[:200]}
        return None

    async def probe_lincoln_member_info(self) -> dict[str, Any] | None:
        """GET /api/cnesl-lincoln-club/v1/member-info — 林肯俱乐部会员信息。"""
        return await self._probe_lincoln_get(
            PATH_LINCOLN_MEMBER_INFO, "member-info"
        )

    async def probe_lincoln_my_page(self) -> dict[str, Any] | None:
        """GET /api/cnesl-lincoln-club/v1/my-page — 林肯「我的页面」聚合。"""
        return await self._probe_lincoln_get(PATH_LINCOLN_MY_PAGE, "my-page")

    async def probe_lwosb_bookings(self) -> dict[str, Any] | None:
        """GET /api/cnxapi-lwosb/v2/app/bookings — 取送车/代驾预约列表。"""
        return await self._probe_lincoln_get(PATH_LWOSB_BOOKINGS, "lwosb-bookings")

    async def probe_lwosb_order_status(self) -> dict[str, Any] | None:
        """GET /api/cnxapi-lwosb/v2/app/bookingorderstatus — 预约订单状态。"""
        return await self._probe_lincoln_get(
            PATH_LWOSB_ORDER_STATUS, "lwosb-order-status"
        )

    # ------------------------------------------------------------ ota setting
    async def get_ota_setting(self, vin: str) -> dict[str, Any] | None:
        """GET /api/alert/v1/ota/setting-info — OTA 设置状态（v3.1.3）。

        实测（2026-10-02）：标准 R3 签名（timestamp+sign+encryptedVin/xjw）
        返回 HTTP 200；同族的 ota/detail、ota/versions 在实测车型上被服务端拒绝
        （errorCode 206004 "capabilityMmota is false"），故仅此端点接入。
        响应 data 字段：remoteOTAFlag / asuState / activationDayOfWeek /
        activationScheduleTime / toBeVersion / toBeReleaseNote / asIsVersion /
        asIsReleaseNote / statusName / statusDescription。
        """
        enc_vin, xjw = await asyncio.to_thread(lambda: self.crypto.encrypt_field(vin))
        data = await self._request(
            "GET",
            PATH_OTA_SETTING,
            query={"encryptedVin": enc_vin, "xjw": xjw},
        )
        inner = data.get("data") if isinstance(data, dict) else None
        return inner if isinstance(inner, dict) else None

    # --------------------------------------------------- 能力探测（v3.3.7）
    async def probe_command_whitelist(self, vin: str) -> list[str] | None:
        """探测 send-command 白名单（全车型能力驱动的权威来源）。

        发送非法 commandType → 网关 HTTP 400，错误消息体枚举合法命令列表
        （"commandType should be in (..., DoorLock, ...)"）。非法类型不产生
        任何车辆动作（幂等安全），仅用于读取该账号/该车真正支持的命令集。
        返回合法命令列表；探测失败返回 None（调用方不创建新实体）。
        """
        enc_vin, xjw = await asyncio.to_thread(
            lambda: self.crypto.encrypt_field(vin)
        )
        body = {
            "commandType": "FordPassWhitelistProbe",
            "encryptedVin": enc_vin,
            "xjw": xjw,
        }
        try:
            await self._request("POST", PATH_SEND_COMMAND, body=body)
        except FordPassApiError as exc:
            msg = str(exc)
            m = re.search(r"should be in\s*\(([^)]*)\)", msg)
            if m:
                cmds = [c.strip() for c in m.group(1).split(",") if c.strip()]
                self._log.info(
                    "FordPass command whitelist (%d): %s", len(cmds), ", ".join(cmds)
                )
                return cmds
            # v4.0.9: 非标准 400/404 = 确定性能力缺失（网关拒绝探测命令/
            # 路由不存在）→ 抛 ProbeRejected 让 coordinator 落盘缓存，
            # TTL 内不再重探；其它错误码保持返回 None（不落盘，reload 重探）。
            if exc.code in (400, 404):
                raise FordPassProbeRejected(exc.code, msg[:200]) from exc
            self._log.debug(
                "FordPass whitelist probe non-standard 400: %s", msg[:200]
            )
            return None
        except Exception as exc:  # noqa: BLE001 - 探测失败不阻塞集成
            self._log.debug("FordPass whitelist probe failed: %s", exc)
            return None
        self._log.debug("FordPass whitelist probe unexpected 2xx (no whitelist)")
        return None

    @staticmethod
    def _rsa_encrypt_cevs(payload: bytes, pubkey_pem: str) -> str:
        """RSA/ECB/PKCS1v15 加密（v3.7.4，cevs 域；v3.7.9 支持 payload 变体）。

        福特派 App 的 encryptByRSA 用 RSA 公钥加密敏感字段后放进
        cevs 请求体。v3.7.9：payload 变体枚举（纯 VIN / JSON{"vin"} /
        JSON{"vin","xjw"} / AES-ECB(vin,key=xjw)），服务器解密层行为
        由诊断传感器自读区分。cryptography 为 HA Core 自带依赖。
        """
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        pub = serialization.load_pem_public_key(pubkey_pem.encode("utf-8"))
        ct = pub.encrypt(payload, padding.PKCS1v15())
        return base64.b64encode(ct).decode("ascii")

    @staticmethod
    def _cevs_variant_payloads(vin: str, xjw: str) -> list[tuple[str, bytes]]:
        """cevs 加密链变体 payload（v3.7.14：3 公钥 × 2 payload 形态）。

        vname = k{1,2,3}_{plain,vinxjw}：
          k1 = 客户端 PKCS#1 2048；k2 = rsa_feed_back 2048；k3 = cnesl_prod 4096
          plain   = RSA(VIN)（payload 仅 VIN 明文）
          vinxjw  = RSA(VIN + xjw)（组合串，混合加密标准形态）
        """
        variants: list[tuple[str, bytes]] = [
            ("k1_plain", vin.encode("utf-8")),
            ("k2_plain", vin.encode("utf-8")),
            ("k3_plain", vin.encode("utf-8")),
            ("k1_vinxjw", (vin + xjw).encode("utf-8")),
            ("k2_vinxjw", (vin + xjw).encode("utf-8")),
            ("k3_vinxjw", (vin + xjw).encode("utf-8")),
        ]
        return variants


    async def _probe_cevs_variant_wbk(
        self, name: str, path: str, body: dict, vname: str,
    ) -> None:
        """AES-CBC 变体探测（v3.7.16/17，cevs 真实加密链与
        login/token refresh 一致：vin=encrypt_field(明文)，xjw=IV hex）。"""
        key = f"{name}_wbk_{vname}"
        try:
            data = await self._request("POST", path, body, raw_body=True)
        except Exception as exc:  # noqa: BLE001
            self.cevs_diag[key] = f"failed: {str(exc)[:200]}"
            self._log.debug("FordPass probe %s wbk_%s failed: %s", name, vname, exc)
            return
        if isinstance(data, dict) and data:
            # v4.0.5: _is_err_resp 已提升为模块级函数（曾为 probe_extra_endpoints
            # 嵌套函数，模块级 helper 引用会 NameError；v3.7.22 只修 wbk 一处，
            # rsa/wbk 循环另两处漏改）——此处保持内联语义一致，统一走模块级
            is_err = (
                "errorCode" in data or "error" in data
                or str(data.get("status")) not in ("0", "200", "None")
            )
            if is_err:
                self.cevs_diag[key] = f"rejected: {json.dumps(data, ensure_ascii=False)[:200]}"
                self._log.info(
                    "FordPass probe %s wbk_%s -> (rejected: %s)",
                    name, vname, json.dumps(data, ensure_ascii=False)[:200],
                )
                return
            self.cevs_diag[key] = f"ok: {json.dumps(data, ensure_ascii=False)[:200]}"
            # v3.7.24: 成功响应存入 cevs_probe 供实体创建
            if vname == "wbk_cevs_shared":
                self.cevs_probe[name] = data
        else:
            self.cevs_diag[key] = "ok(empty)"

    async def _probe_cevs_variant_once(
        self, name: str, path: str, body: dict, vname: str, payload: bytes,
        pem: str,
    ) -> None:
        """对单个端点单个变体发一次探测，结果写 cevs_diag（诊断自读）。"""
        key = f"{name}_rsa_{vname}"
        try:
            rsa_ct = await asyncio.to_thread(self._rsa_encrypt_cevs, payload, pem)
            b = dict(body)
            b["vin"] = rsa_ct
            data = await self._request("POST", path, b, raw_body=True)
        except Exception as exc:  # noqa: BLE001 - 探测失败只记录
            self.cevs_diag[key] = f"failed: {str(exc)[:200]}"
            self._log.debug("FordPass probe %s rsa_%s failed: %s", name, vname, exc)
            return
        if isinstance(data, dict) and data:
            if _is_err_resp(data):
                self.cevs_diag[key] = f"rejected: {json.dumps(data, ensure_ascii=False)[:200]}"
                self._log.info(
                    "FordPass probe %s rsa_%s -> (rejected: %s)",
                    name, vname, json.dumps(data, ensure_ascii=False)[:200],
                )
                return
            self.cevs_diag[key] = f"ok: {json.dumps(data, ensure_ascii=False)[:200]}"
            self._log.info(
                "FordPass probe %s rsa_%s -> %s",
                name, vname, json.dumps(data, ensure_ascii=False)[:600],
            )
        else:
            self.cevs_diag[key] = "ok(empty)"

    async def probe_extra_endpoints(
        self, vin: str, fuel_type: str | None = None
    ) -> dict[str, Any] | None:
        """探测只读 GET 端点响应并返回结果供实体创建（v3.4.6）。

        v3.3.7 起探测 maintenance-history / departuretimes retrieve 仅打日志；
        v3.4.6 改为**返回探测结果**（coordinator 缓存 24h），sensor 按
        "探测到数据才创建"接入实体。全部只读/幂等 GET，不触发任何车辆
        动作；非能力车型/无数据端点返回空，不创建实体（全车型适配）。
        端点来源：6.16.0 接口字符串整理（2026-10-08）+ 既有探测。

        v3.5.4: 过滤业务错误响应——errorCode 206004 "capabilityMmota is
        false" 等被网关拒绝的响应是 dict 非空，此前被误存为"探测到数据"
        创建假实体；errorCode/error 存在或 status 非 0/200 一律视为
        "车型无能力"（打 INFO rejected，不创建实体）。

        v3.5.5: 多形态自适应——日志实证 v3.5.4 中 uservehicles/messages_v2/
        share_list 及多数车辆级端点 GET 全 404 Resource not found（路由存在
        但方法不对，App 消息中心等必然可用 → 实际为 POST-only 路由）；
        对每个端点先 GET（onlinernr 等实证可用的保持原样零额外请求），
        GET 404/失败再试 POST 空 body，成功即缓存该端点的请求形态。
        """

        # v3.5.6: 值结构 (path, post_body 模板, allow_post)。
        # 日志实证（2026-10-09 v3.5.5）：多数端点 GET 404、POST body 缺字段
        # 400（错误消息给出所需 body 字段）→ POST 变体按模板补字段：
        #   departuretimes/chargelogs 需明文 "vin"
        #   uservehicles 需 clientType/appVersion/encryptedVin/xjw（通用自动补）
        #   其余端点 encryptedVin/xjw 由 _get_signed 自动补
        # 双方法都 404 / 业务拒绝的端点（messages_*/sensor_shadow/
        # schedule_departure/pds_device/maintenance-history/ota_versions/
        # ota_detail/onlinernr）allow_post=False 仅 GET，不白试变体
        probes = {
            "maintenance-history": (PATH_MAINTENANCE_HISTORY, None, False),
            "departuretimes": (PATH_DEPARTURE_TIMES_RETRIEVE, {"vin": None, "__skip_encrypted_vin__": True}, True),
            "chargelogs": (PATH_CHARGELOGS_RETRIEVE, {"vin": None, "__skip_encrypted_vin__": True}, True),
            "ota_versions": (PATH_OTA_VERSIONS, None, False),
            "ota_detail": (PATH_OTA_DETAIL, None, False),
            "ota_search_details": (PATH_OTA_SEARCH_DETAILS, None, True),
            "ota_new_status": (PATH_OTA_NEW_STATUS, None, True),
            # v3.5.0: 6.16.0 补全端点（全部只读/幂等，不触发车辆动作）
            "onlinernr": (PATH_ONLINENR, None, False),
            "3d_model": (PATH_3D_MODEL, None, True),
            "schedule_departure": (PATH_SCHEDULE_DEPARTURE, None, False),
            "pds_device": (PATH_PDS_DEVICE, None, False),
            "user_auth": (PATH_USER_AUTH_STATUS, None, True),
            "srs_profile": (PATH_SRS_PROFILE, None, True),
            "sensor_shadow": (PATH_SENSOR_SHADOW, None, True),
            "video_file": (PATH_VIDEO_FILE, None, True),
            "messages_page": (PATH_MESSAGES_PAGE, None, False),
            "ota_reddot": (PATH_OTA_REDDOT, {"__skip_encrypted_vin__": True}, True),
            # v3.5.1: 车辆级端点（search-vehicle-device-list 按车辆签名）
            "device_list": (PATH_DEVICE_LIST, None, True),
            # v4.2.6: 移动流量商城 H5 地址（6.16.0 独立实测确认，GET 只读）
            "cmcc_traffic": (PATH_CMCC_TRAFFIC, None, False),
            # v4.3.5: 228 端点比对后未接入的只读端点补全（全部无需实测
            # ——GET + 标准签名，探测到数据才创建，与现有实体不重复）。
            # 家充桩只读扩展（smartwallbox，探测到绑定桩才创建）：
            "wallbox_bound": (PATH_SMARTWALLBOX_BOUND, None, True),
            "wallbox_info": (PATH_SMARTWALLBOX_INFO, None, True),
            "wallbox_records_v2": (PATH_SMARTWALLBOX_RECORDS_V2, None, True),
            # 充电域只读扩展（evss/vpoi，电/插混/混动探测到数据才创建）：
            "evss_order_status": (PATH_EVSS_ORDER_STATUS, None, True),
            "evss_apps_info": (PATH_EVSS_APPS_INFO, None, True),
            "evss_usage_notice": (PATH_EVSS_USAGE_NOTICE, None, True),
            "vpoi_status": (PATH_VPOI_STATUS, None, True),
            "vpoi_saved": (PATH_VPOI_SAVED, None, True),
            # OTA 协议（全车型只读）：
            "ota_agreements": (PATH_OTA_AGREEMENTS, None, True),
            # v4.3.6: 136 未接入清单第二轮筛选——剩余只读业务端点补全
            # （无需实测：探测多形态自适应 GET→POST 空 body，探测到数据才创建）。
            # 充电域订单/事件/评价（evss，仅电/插混/混动创建）：
            "evss_payment_summary": (PATH_EVSS_PAYMENT_SUMMARY, None, True),
            "evss_invoiceables": (PATH_EVSS_INVOICEABLES, None, True),
            "evss_events_v3": (PATH_EVSS_EVENTS_V3, None, True),
            "evss_evaluate_url": (PATH_EVSS_EVALUATE_URL, None, True),
            # 家充桩 BLE 充电车辆信息（探测到绑定桩才创建）：
            "wallbox_ble_vehicles": (PATH_WALLBOX_BLE_VEHICLES, None, True),
            # app-init 域（全车型公开配置类只读）：
            "app_init_settings": (PATH_APP_INIT_SETTINGS, None, True),
            "vehicle_model_masterdata": (PATH_VEHICLE_MODEL_MASTERDATA, None, True),
            # v5.8.4: 访客模式 v3 页面初始化信息（App 设置页入口，全车型
            # 公开配置类只读；探测到数据才创建「访客模式」传感器）
            "visitor_mode": (PATH_VISITOR_MODE, None, True),
            # v5.8.4: vpoi v4（App 已升级 v4 路由；数据格式待 App 实测确认
            # 后替换 v3 传感器数据源——先探测落盘）
            "vpoi_chargestations_v4": (PATH_VPOI_CHARGESTATIONS_V4, None, True),
            "vpoi_saved_v4": (PATH_VPOI_SAVED_V4, None, True),
            # v6.1.9: 剩余只读业务端点补全（探测到数据才创建，与现有实体
            # 不重复；全部 GET + 标准签名 / 多形态自适应 POST）：
            # 质保信息（v6.1.9 前实测签名通过但服务端 100502 参数校验拒绝
            # ——探测失败不建实体、确定性拒绝落盘缓存，云端放开后自动出现）
            "warranty": (PATH_WARRANTY, None, False),
            # 车辆能力清单 v4（cvfeatures，v3.1.16 实测 GET/POST 均 404——
            # 网关未开放；同上探测自适应，未来开放自动创建）
            "capability_v4": (PATH_CVFEATURES, None, True),
            # 车辆信息 v5（vehicles/info，未实测——探测自适应）
            "vehicle_info": (PATH_VEHICLE_INFO, None, False),
            # 充电域只读扩展（免密支付通知 / 充电过程日志 / 分享条款内容；
            # 纯油/柴油车型由下方 fuelType 过滤跳过）：
            "evss_freepass_notice": (PATH_EVSS_FREEPASS_NOTICE, None, True),
            "wallbox_process_log": (PATH_SMARTWALLBOX_PROCESS_LOG, None, True),
            "wallbox_tnc_content": (PATH_WALLBOX_TNC_CONTENT, None, False),
        }
        # v3.5.1: 账号级端点。v3.5.6 实证——GET 全 404（v3.5.1/3.5.2 移除
        # 车辆参数后仍 404 → 路由为 POST-only）；POST body 需
        # clientType/appVersion/encryptedVin/xjw（uservehicles 错误消息
        # 明确给出）。GET 保持无参（同 messages/summary），POST 带参。
        account_probes = {
            "uservehicles": (PATH_USER_VEHICLES, {"clientType": self._client_type,
                                                  "appVersion": self._app_version}, True),
            "messages_v2": (PATH_MESSAGES_V2_PAGE, None, False),
            "share_list": (PATH_SHARE_LIST, None, True),
            # v3.6.2: 待开发清单补全——消息中心 v3（mcm 域，GET 无参同 summary）、
            # 用户级红点（cnesl-user，账号级只读）。探测到数据才创建。
            "mcm_messages_v3": (PATH_MCM_MESSAGES_V3, None, True),
            "user_reddot": (PATH_USER_REDDOT, None, True),
            # v4.3.5: 车辆分享能力/分享历史（cnesl-user 账号级只读，探测到
            # 数据才创建——分享功能为静态触顶，探测一次即知是否支持）。
            "share_capability": (PATH_SHARE_CAPABILITY, None, True),
            "share_history": (PATH_SHARE_HISTORY, None, True),
            # v4.3.6: 用户登录状态（cnesl-user 账号级只读，探测到数据才创建）
            "user_login_status": (PATH_USER_LOGIN_STATUS, None, True),
            # v3.7.2: 车辆授权管理列表（vehicle-user-auth 查询该车辆授权用户，
            # 只读；非授权/无数据的车型不创建）。公共充电探测（纯电/插混专属，
            # 位置相关无参探测失败→不创建，全车型安全）：
            "user_auth_list": (PATH_VEHICLE_USER_AUTH, None, True),
            "evss_stations": (PATH_EVSS_STATIONS, None, True),
            "evss_orders": (PATH_EVSS_ORDERS, None, True),
            "vpoi_chargestations": (PATH_VPOI_CHARGESTATIONS, None, True),
        }
        # v4.5.6: 按车型跳过充电域探测——汽油(G)/柴油(D)车无家充桩/公共充电，
        # smartwallbox/evss/vpoi 端点每次 setup 探测制造 6-12 次 404 噪音
        # （与 cevs 按车型跳过对称）。电/插混/混动保留探测。
        fuel = str(fuel_type or "").upper()
        if fuel in ("", "G", "D"):
            for _k in (
                "chargelogs",
                "wallbox_bound", "wallbox_info", "wallbox_records_v2",
                "wallbox_ble_vehicles", "wallbox_process_log", "wallbox_tnc_content",
                "evss_order_status", "evss_apps_info", "evss_usage_notice",
                "evss_payment_summary", "evss_invoiceables", "evss_events_v3",
                "evss_evaluate_url", "evss_freepass_notice",
                "vpoi_status", "vpoi_saved",
                "vpoi_chargestations_v4", "vpoi_saved_v4",
                "evss_stations", "evss_orders", "vpoi_chargestations",
            ):
                probes.pop(_k, None)
                account_probes.pop(_k, None)

        async def _probe(name: str, spec, signed: bool) -> dict[str, Any] | None:
            """多形态探测：GET → allow_post 时再试 POST(带 body 模板)；
            业务拒绝/双 404 不再试变体。"""
            path, post_body, allow_post = spec
            variants = [("GET", None)]
            if allow_post:
                variants.append(("POST", post_body or {}))
            for method, body in variants:
                try:
                    if signed:
                        data = await self._get_signed(path, vin, method=method, body=body)
                    elif method == "POST":
                        # v3.5.7: 账号级 POST body 只补 encryptedVin/xjw
                        # （share_list 报 Unrecognized field "clientType"；
                        # 仅 uservehicles 的模板自带 clientType/appVersion）
                        b = dict(body or {})
                        enc_vin, xjw = await asyncio.to_thread(
                            lambda: self.crypto.encrypt_field(vin))
                        b.setdefault("encryptedVin", enc_vin)
                        b.setdefault("xjw", xjw)
                        data = await self._request("POST", path, body=b)
                    else:
                        data = await self._request("GET", path)
                except FordPassProbeRejected:
                    raise
                except FordPassApiError as exc:
                    # v4.0.9: HTTP 404 = 确定性能力缺失（v3.5.5 实测：探测
                    # 端点路由对无能力车型返回 404）——转为 ProbeRejected 让
                    # coordinator 落盘缓存（TTL 内不重探）；其它错误码（5xx/
                    # 网关抖动）保持不落盘，下次 reload 重探。
                    if exc.code in (404,):
                        raise FordPassProbeRejected(exc.code, str(exc)) from exc
                    self._log.debug("FordPass probe %s %s failed: %s", name, method, exc)
                    continue
                except Exception as exc:  # noqa: BLE001 - 探测失败只记录
                    self._log.debug("FordPass probe %s %s failed: %s", name, method, exc)
                    continue
                if isinstance(data, dict) and data:
                    if _is_err_resp(data):
                        # v4.0.9: 业务拒绝（车型无能力）抛专用异常——coordinator
                        # 据此落盘持久化缓存（TTL 内不再重探）；不再 return None
                        # 与"空响应/网络错误"混为一谈（旧实现把瞬时故障也钉死
                        # 7 天）。
                        msg = json.dumps(data, ensure_ascii=False)[:200]
                        self._log.info(
                            "FordPass probe %s %s -> (rejected: %s)",
                            name, method, msg,
                        )
                        raise FordPassProbeRejected(
                            data.get("status") or data.get("errorCode"), msg
                        )
                    self._log.info(
                        "FordPass probe %s %s -> %s",
                        name, method, json.dumps(data, ensure_ascii=False)[:600],
                    )
                    return data
            self._log.info("FordPass probe %s -> (empty)", name)
            return None

        result: dict[str, Any] = {}
        any_rejected = False

        async def _probe_loop(items, signed):
            """探测全部端点：拒绝/失败只影响该端点（继续探测其余）；
            返回 (结果dict, 是否有确定性拒绝)。"""
            nonlocal any_rejected
            got: dict[str, Any] = {}
            for name, spec in items.items():  # v4.5.6: 修复 dict 迭代按 key 解包（key 字符串>2 报 too many values）
                try:
                    data = await _probe(name, spec, signed=signed)
                    if data:
                        got[name] = data
                except FordPassProbeRejected as exc:
                    # v4.0.9: 端点级拒绝不中断探测；整体在方法结尾按
                    # any_rejected + 无任何数据 决定是否向 coordinator
                    # 抛拒绝（落盘 None）。
                    any_rejected = True
                    self._log.debug("FordPass probe %s rejected: %s", name, exc)
            return got

        # 车辆级端点：带 encryptedVin/xjw 签名
        result.update(await _probe_loop(probes, signed=True))
        # 账号级探测结果 api 实例级缓存（v3.5.2）：多 VIN 账号每车一个
        # coordinator 并发探测时仅首车发网络请求，其余车直接复用——
        # 符合风控"探测类请求只在登录后执行一次并缓存"（防福特云限流）
        account_cache = getattr(self, "_account_probe_cache", None)
        if account_cache is not None:
            result.update(account_cache)
        else:
            account_res: dict[str, Any] = await _probe_loop(account_probes, signed=False)
            self._account_probe_cache = dict(account_res)
            result.update(account_res)
        # v4.0.9: 全部端点无数据但存在确定性拒绝 → 抛拒绝让 coordinator
        # 落盘 None（车型无能力，TTL 内不重探）；部分端点有数据则正常返回。
        if not result and any_rejected:
            raise FordPassProbeRejected("all", "all probe endpoints rejected (no capability)")
        # v3.7.4/3.7.5: cevs 域 RSA 探测变体——原 AES 密文/明文 vin 均被
        # 服务器 RSA 解密失败（Impossible modulus）；v3.7.4 日志实证 RSA 密文
        # vin 通过 modulus 校验（不再 Impossible modulus），DTO 报
        # Unrecognized field "timestamp"（不接受外层签名）→ raw_body 裸发；
        # chargelogs 报 Missing required creator property 'xjw' → 补 xjw
        # （与 encrypt_field 同会话 IV）。失败仅日志，不创建、不进轮询。
        # v3.7.18: cevs 自动探测独立为 probe_cevs_auto（wbk_shared 单变体，
        # 3 请求防风控）——此前嵌在 probe_extra_endpoints 尾部，前段探测
        # 失败即中断、cevs_diag 为空、诊断传感器不创建。
        try:
            await self.probe_cevs_auto(vin)
        except Exception as exc:  # noqa: BLE001
            self._log.debug("FordPass cevs auto probe failed: %s", exc)
        return result or None

    async def probe_cevs_auto(self, vin: str) -> None:
        """cevs 域自动探测（v3.7.20）：**链式模式 + cevs 场景专用密钥**
        （数据包中的 cevs.encrypt——v3.7.20 实证此前 x_api 场景加密被
        服务器 cevs 场景解密报 bad key）。单变体
        wbk_cevs_shared（共享会话 IV），3 请求/次防福特云风控静默降级；
        结果写 api.cevs_diag（coordinator 引用同一对象，诊断传感器可读）。"""
        # v3.7.24: 成功响应解析进 self.cevs_probe（预约出发/充电实体数据源）——
        # departuretimes: isEnabled/goTimesScheduleCloudData/syncStatus；
        # chargelogs: chargeLogs/nextChrgTimestamp；commandstatus 需 commandId
        # 业务字段（非加密问题），不建实体。
        # v3.7.21: 整个自动探测独立 try——异常写入 cevs_diag["auto_error"]
        # （诊断传感器直接可读，不再被 coordinator debug 吞掉）；端点循环
        # 内每端点独立 try，一个端点失败不中断其余。
        self.cevs_probe: dict[str, Any] = {}
        try:
            _, xjw_now = await asyncio.to_thread(lambda: self.crypto.encrypt_field(vin))
            cevs_crypto = await asyncio.to_thread(FordPassCrypto.get, None, "cevs")
            for name, path, body in (
                ("departuretimes", PATH_DEPARTURE_TIMES_RETRIEVE, {"vin": None}),
                ("chargelogs", PATH_CHARGELOGS_RETRIEVE, {"vin": None, "xjw": xjw_now}),
                ("cevs_command_status", PATH_CEVS_COMMAND_STATUS, {"vin": None}),
            ):
                try:
                    b = dict(body)
                    enc, xjw2 = await asyncio.to_thread(
                        lambda: cevs_crypto.encrypt_field(vin, bytes.fromhex(xjw_now)),
                    )
                    # v3.7.23: DTO 反序列化错误实证——三端点必填字段均为 "vin"
                    # （v3.7.22 encryptedVin 报 Unrecognized field/Missing vin；
                    # CommandStatusV2Request 类名暴露）。cevs 场景密文+vin。
                    b["vin"] = enc
                    b["xjw"] = xjw2
                    await self._probe_cevs_variant_wbk(name, path, b, "wbk_cevs_shared")
                except Exception as exc:  # noqa: BLE001
                    self.cevs_diag[f"{name}_wbk_cevs_shared"] = f"enc_err: {type(exc).__name__}: {str(exc)[:160]}"
                    self._log.info("FordPass cevs %s wbk_cevs_shared error: %s", name, exc)
        except Exception as exc:  # noqa: BLE001
            self.cevs_diag["auto_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            self._log.info("FordPass cevs auto probe error: %s", exc)

    async def _cevs_write(
        self, vin: str, path: str, extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """cevs 域写操作公共封装（v4.2.4，6.16.0 Dart 接口分析确认）。

        与 probe_cevs_auto 成功变体（wbk_cevs_shared，v3.7.20/23 实证服务器
        按 cevs 场景解密）同构：body = {"vin": cevs 场景 AES 密文, "xjw": IV
        hex, ...extra}，raw_body=True（cevs 域 DTO 拒绝外层 timestamp/sign）。
        """
        cevs_crypto = await asyncio.to_thread(FordPassCrypto.get, None, "cevs")
        enc, xjw = await asyncio.to_thread(lambda: cevs_crypto.encrypt_field(vin))
        b: dict[str, Any] = {"vin": enc, "xjw": xjw}
        if extra:
            b.update(extra)
        data = await self._request("POST", path, b, raw_body=True)
        return data if isinstance(data, dict) else {"raw": data}

    async def cevs_delete_all_departures(self, vin: str) -> dict[str, Any]:
        """清空全部预约出发/充电计划。

        实测 6.16.0（接口行为分析）：POST /api/cevs/v2/departuretimes/
        deleteall（deleteAllScheduledDepartureTimesV2，body 键 data）。删除后
        由按钮触发状态刷新让实体回读新值。
        """
        result = await self._cevs_write(vin, PATH_DEPARTURE_DELETE_ALL)
        self._log.info("FordPass cevs deleteall -> %s",
                       json.dumps(result, ensure_ascii=False)[:400])
        return result

    async def probe_smartwallbox(self, vin: str) -> dict[str, Any] | None:
        """探测家充桩管理（smartwallbox）端点结构（只读/幂等，v3.4.5）。

        6.16.0 接口字符串整理端点（2026-10-08，请求细节待实机样本
        校准）：GET binding/v5（绑定桩列表）、config/common/query/v2（通用
        配置）、records/single/list/v2r（最近充电记录）。对每个端点尝试
        带 encryptedVin/xjw 与不带两种形态；全部为只读查询，不触发任何
        车辆/充电桩动作。返回探测摘要供实体按"探测到数据才创建"接入
        （全车型能力驱动——非家充桩车型 404/参数校验失败，不创建实体）。
        """
        result: dict[str, Any] = {}
        probes = {
            "wallbox_binding": PATH_SMARTWALLBOX_BINDING,
            "wallbox_config": PATH_SMARTWALLBOX_CONFIG,
            "wallbox_records": PATH_SMARTWALLBOX_RECORDS,
        }
        for name, path in probes.items():
            for signed in (True, False):
                try:
                    if signed:
                        data = await self._get_signed(path, vin)
                    else:
                        data = await self._request("GET", path)
                except Exception as exc:  # noqa: BLE001 - 探测失败只记录
                    self._log.debug(
                        "FordPass smartwallbox %s (signed=%s) failed: %s",
                        name, signed, exc,
                    )
                    continue
                if isinstance(data, dict):
                    if "errorCode" in data or "error" in data:
                        self._log.info(
                            "FordPass smartwallbox %s -> (rejected: %s)",
                            name, json.dumps(data, ensure_ascii=False)[:200],
                        )
                    else:
                        result[name] = {"signed": signed, "data": data}
                        self._log.info(
                            "FordPass smartwallbox probe %s -> %s",
                            name, json.dumps(data, ensure_ascii=False)[:800],
                        )
                    break
                if data is not None:
                    result[name] = {"signed": signed, "raw": str(data)[:200]}
                    break
        # v3.7.2: 家充桩授权查询（只读，加入探测摘要——有绑定的车型才返回数据）
        for name, path in (("wallbox_authority", PATH_SMARTWALLBOX_AUTHORITY),):
            for signed in (True, False):
                try:
                    if signed:
                        data = await self._get_signed(path, vin)
                    else:
                        data = await self._request("GET", path)
                except Exception as exc:  # noqa: BLE001 - 探测失败只记录
                    self._log.debug(
                        "FordPass smartwallbox %s (signed=%s) failed: %s",
                        name, signed, exc,
                    )
                    continue
                if isinstance(data, dict):
                    if "errorCode" in data or "error" in data:
                        self._log.info(
                            "FordPass smartwallbox %s -> (rejected: %s)",
                            name, json.dumps(data, ensure_ascii=False)[:200],
                        )
                    else:
                        result[name] = {"signed": signed, "data": data}
                        self._log.info(
                            "FordPass smartwallbox probe %s -> %s",
                            name, json.dumps(data, ensure_ascii=False)[:800],
                        )
                    break
                if data is not None:
                    result[name] = {"signed": signed, "raw": str(data)[:200]}
                    break
        # v3.7.3: 桩级端点（分享列表/桩级充电状态/充电信息/充电订单）——
        # 先尝试 GET；绑定桩探测有 wallboxId 时再补一次带 wallboxId 的
        # POST（App 实际走 POST）。全部只读，无绑定车型不创建实体。
        wb_bind = result.get("wallbox_binding", {})
        wb_data = wb_bind.get("data") if isinstance(wb_bind, dict) else None
        wb_id: str | None = None
        if isinstance(wb_data, dict):
            for key in ("wallboxId", "defaultWallboxId", "id", "equipmentId"):
                if wb_data.get(key):
                    wb_id = str(wb_data[key])
                    break
            if wb_id is None:
                lst = wb_data.get("list") or wb_data.get("wallboxList")
                if isinstance(lst, list) and lst and isinstance(lst[0], dict):
                    for key in ("wallboxId", "id", "equipmentId", "serialNumber"):
                        if lst[0].get(key):
                            wb_id = str(lst[0][key])
                            break
        for name, path in (
            ("wallbox_sharing_query", PATH_SMARTWALLBOX_SHARING_QUERY),
            ("wallbox_charge_status", PATH_SMARTWALLBOX_CHG_STATUS_WB),
            ("wallbox_charging", PATH_SMARTWALLBOX_CHG_WB),
            ("wallbox_orders", PATH_SMARTWALLBOX_ORDERS),
        ):
            for signed in (True, False):
                try:
                    if signed:
                        data = await self._get_signed(path, vin)
                    else:
                        data = await self._request("GET", path)
                except Exception as exc:  # noqa: BLE001
                    self._log.debug(
                        "FordPass smartwallbox %s (signed=%s) failed: %s",
                        name, signed, exc,
                    )
                    continue
                if isinstance(data, dict):
                    if "errorCode" in data or "error" in data:
                        self._log.info(
                            "FordPass smartwallbox %s -> (rejected: %s)",
                            name, json.dumps(data, ensure_ascii=False)[:200],
                        )
                    else:
                        result[name] = {"signed": signed, "data": data}
                        self._log.info(
                            "FordPass smartwallbox probe %s -> %s",
                            name, json.dumps(data, ensure_ascii=False)[:800],
                        )
                    break
                if data is not None:
                    result[name] = {"signed": signed, "raw": str(data)[:200]}
                    break
            # 绑定桩 id 可用且 GET 未命中数据时，补一次带 wallboxId 的 POST
            if wb_id and name not in result:
                try:
                    data = await self._request("POST", path, {"wallboxId": wb_id})
                except Exception as exc:  # noqa: BLE001
                    self._log.debug(
                        "FordPass smartwallbox %s POST(wallboxId) failed: %s",
                        name, exc,
                    )
                    continue
                if isinstance(data, dict) and not ("errorCode" in data or "error" in data):
                    result[name] = {"signed": False, "post_wallbox": True, "data": data}
                    self._log.info(
                        "FordPass smartwallbox probe %s POST(wallboxId) -> %s",
                        name, json.dumps(data, ensure_ascii=False)[:800],
                    )
        return result or None

    # ------------------------------------------------ v4.3.3 fposb/evss 探测
    async def probe_fposb(self, vin: str) -> dict[str, Any] | None:
        """探测 fposb 4S 维保预约端点（只读/幂等，v4.3.3）。

        6.16.0 接口端点（cnjvsl-fposb/v2，方法表：query*=GET、
        osborder=POST、osbcancel=DELETE）——本集成仅接入只读查询：
        osbhistory 预约历史 / osbbookingdetail 预约详情 / puddetail 取送车
        订单详情 / sainfo 服务顾问。全部带 encryptedVin/xjw 签名 GET，
        不触发任何下单/取消动作。返回探测摘要供实体按"探测到数据才创建"
        （全车型能力驱动——无维保服务车型 404/空响应不创建实体）。
        """
        result: dict[str, Any] = {}
        probes = {
            "fposb_history": PATH_FPOSB_HISTORY,
            "fposb_detail": PATH_FPOSB_BOOKING_DETAIL,
            "fposb_pud": PATH_FPOSB_PUD_DETAIL,
            "fposb_sa": PATH_FPOSB_SA_INFO,
            # v6.1.9: 维保可约资源查询（GET 只读，不触发下单动作）
            "fposb_resources": PATH_FPOSB_AVAILABLE_RESOURCE,
        }
        for name, path in probes.items():
            try:
                data = await self._get_signed(path, vin)
            except FordPassApiError as exc:
                # v4.5.8: HTTP 404 = 确定性无该服务——转 ProbeRejected 落盘
                # None（TTL 内不再重探），此前每次 setup 重探 4 次 404 噪音
                if exc.code in (404,):
                    raise FordPassProbeRejected(exc.code, str(exc)) from exc
                self._log.debug("FordPass fposb %s failed: %s", name, exc)
                continue
            except Exception as exc:  # noqa: BLE001 - 探测失败只记录
                self._log.debug("FordPass fposb %s failed: %s", name, exc)
                continue
            if isinstance(data, dict):
                if "errorCode" in data or "error" in data:
                    self._log.info(
                        "FordPass fposb %s -> (rejected: %s)",
                        name, json.dumps(data, ensure_ascii=False)[:200],
                    )
                else:
                    result[name] = {"signed": True, "data": data}
                    self._log.info(
                        "FordPass fposb probe %s -> %s",
                        name, json.dumps(data, ensure_ascii=False)[:800],
                    )
            elif data is not None:
                result[name] = {"signed": True, "raw": str(data)[:200]}
        return result or None

    async def probe_evss(self, vin: str) -> dict[str, Any] | None:
        """探测 evss 充电站/免密支付端点（只读/幂等，v4.3.3）。

        6.16.0 接口端点（方法表全部 GET）：accounts/v3 EV 账户、
        chargers/v1/info 充电站信息、chargers/v2/parking 停车位、
        chargers/v2/events/order 充电订单事件、freepassword/v1/status
        免密支付状态。全部带 encryptedVin/xjw 签名 GET，只读不触发动作。
        返回探测摘要供实体按"探测到数据才创建"；车型过滤（纯油/柴油
        不探测）在 coordinator 按 fuelType 控制。
        """
        result: dict[str, Any] = {}
        probes = {
            "evss_accounts": PATH_EVSS_ACCOUNTS,
            "evss_charger_info": PATH_EVSS_CHARGER_INFO,
            "evss_parking": PATH_EVSS_PARKING,
            "evss_order_events": PATH_EVSS_ORDER_EVENTS,
            "evss_freepass": PATH_EVSS_FREEPASS_STATUS,
        }
        for name, path in probes.items():
            try:
                data = await self._get_signed(path, vin)
            except Exception as exc:  # noqa: BLE001 - 探测失败只记录
                self._log.debug("FordPass evss %s failed: %s", name, exc)
                continue
            if isinstance(data, dict):
                if "errorCode" in data or "error" in data:
                    self._log.info(
                        "FordPass evss %s -> (rejected: %s)",
                        name, json.dumps(data, ensure_ascii=False)[:200],
                    )
                else:
                    result[name] = {"signed": True, "data": data}
                    self._log.info(
                        "FordPass evss probe %s -> %s",
                        name, json.dumps(data, ensure_ascii=False)[:800],
                    )
            elif data is not None:
                result[name] = {"signed": True, "raw": str(data)[:200]}
        return result or None

    # ------------------------------------------------ v3.7.2 家充桩控制
    async def wallbox_charge_start(self, vin: str, wallbox_id: str) -> dict[str, Any]:
        """开始充电：POST charging/start/v2（v3.7.2，wallboxId 由调用方传入）。"""
        body = {"wallboxId": wallbox_id}
        return await self._request("POST", PATH_SMARTWALLBOX_CHARGE_START, body)

    async def wallbox_charge_stop(self, vin: str, wallbox_id: str) -> dict[str, Any]:
        """停止充电：POST charging/stop/v2（v3.7.2）。"""
        body = {"wallboxId": wallbox_id}
        return await self._request("POST", PATH_SMARTWALLBOX_CHARGE_STOP, body)

    async def wallbox_unbind(self, vin: str, wallbox_id: str) -> dict[str, Any]:
        """解绑充电桩：POST binding/unbind/v2（v3.7.2）。"""
        body = {"wallboxId": wallbox_id}
        return await self._request("POST", PATH_SMARTWALLBOX_UNBIND, body)

    async def wallbox_ble_add(self, vin: str, wallbox_id: str) -> dict[str, Any]:
        """家充桩 BLE 自动认证（v4.7.3）：POST auto/charging/ble/add/v3。

        6.16.0 独立实测确认（桩车 BLE 自动授权——插枪后桩与车 BLE
        自动配对认证，无需手机在场）。仅 smartwallbox 探测到绑定桩的
        车型创建按钮；纯油/柴油车不创建（无绑定桩）。请求体按端点收窄
        只带 wallboxId（同 unbind 范式）；若网关要求额外字段，日志会给出
        Unrecognized field 提示，届时按错误消息补字段（见 lessons.md）。
        """
        body = {"wallboxId": wallbox_id}
        return await self._request("POST", PATH_SMARTWALLBOX_BLE_ADD, body)

    # ------------------------------------------------ v3.5.0 新增命令/查询
    async def mark_messages_read(
        self, category_id: str | None = None, message_id: str | None = None
    ) -> dict[str, Any]:
        """标记消息已读：POST /api/cnxapi-message/app/messages/read（v3.5.0）。

        6.16.0 APK 端点（2026-10-09 实测），请求体随 App 版本/消息分类可能
        有 categoryId/messageId 两种形态；按调用方传入参数构造，无参数时
        提交空分类标记（服务端按当前未读处理）。失败抛 FordPassApiError
        由按钮实体统一 catch 上报，不产生车辆动作。
        """
        body: dict[str, Any] = {}
        if category_id:
            body["categoryId"] = category_id
        if message_id:
            body["messageId"] = message_id
        return await self._request("POST", PATH_MESSAGES_READ, body)

    async def departure_toggle(
        self, vin: str, enable: bool, task_id: str | None = None
    ) -> dict[str, Any]:
        """预约充电/预约出发启停：POST cevs/v2/departuretimes/toggleon|off。

        v3.5.0：task_id（departureTimesColumUpdateId）存在时带上，服务端按
        具体任务切换；缺失时按默认任务切换。全部为计划任务开关，不涉及
        门锁/发动机等即时安全动作。
        """
        enc_vin, xjw = await asyncio.to_thread(lambda: self.crypto.encrypt_field(vin))
        body: dict[str, Any] = {"encryptedVin": enc_vin, "xjw": xjw}
        if task_id:
            body["departureTimesColumUpdateId"] = task_id
        return await self._request(
            "POST",
            PATH_DEPARTURE_TOGGLE_ON if enable else PATH_DEPARTURE_TOGGLE_OFF,
            body,
        )

    async def start_video_recording(self, vin: str) -> dict[str, Any]:
        """开始行车记录仪录制：POST pds/v1/start-video-recording（v3.5.0）。

        带 encryptedVin/xjw（与 send_command 同族加密）；App 在远程监控页
        调用，仅对带行车记录仪硬件的车型有效（无硬件车型服务端拒绝）。
        """
        enc_vin, xjw = await asyncio.to_thread(lambda: self.crypto.encrypt_field(vin))
        return await self._request(
            "POST", PATH_VIDEO_START, {"encryptedVin": enc_vin, "xjw": xjw}
        )

    async def stop_video_recording(self, vin: str) -> dict[str, Any]:
        """停止行车记录仪录制：POST pds/v1/stop-video-recording（v3.5.0）。"""
        enc_vin, xjw = await asyncio.to_thread(lambda: self.crypto.encrypt_field(vin))
        return await self._request(
            "POST", PATH_VIDEO_STOP, {"encryptedVin": enc_vin, "xjw": xjw}
        )

    # ------------------------------------------------ v5.8.2 未接入端点补全
    # （2026-10-24 端点比对 8 项功能缺口全部接入。写入类 body 结构来自既有
    #  探测返回回填 + 接口实测，字段名非猜测；遥控命令类（v5 doors/engine）
    #  仅实现 API 方法不建实体——v5.2.4 纪律：未对照实测确认的
    #  遥控载荷不下发，待实测后启用对应实体。）
    async def v5_doors_lock(self, vin: str, lock: bool = True) -> dict[str, Any]:
        """整门锁/解锁（v5 原生通道，6.16.0 Dart 确认 PUT=执行/DELETE=取消
        语义化）。载荷未实测——仅方法实现，不建实体。"""
        method = "PUT" if lock else "DELETE"
        return await self._v5_verb(
            method, V5_BASE_URL + PATH_V5_DOORS_LOCK.format(vin=vin), "doors_lock"
        )

    async def v5_engine_start(self, vin: str, start: bool = True) -> dict[str, Any]:
        """v5 引擎启停原生通道：PUT=启动、DELETE=停止（v5 语义化）。
        载荷未实测——仅方法实现，不建实体（现有 send-command
        RemoteStart 通道已确认可用）。"""
        method = "PUT" if start else "DELETE"
        return await self._v5_verb(
            method, V5_BASE_URL + PATH_V5_ENGINE_START.format(vin=vin), "engine"
        )

    async def cevs_save_departure(
        self, vin: str, structure: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """保存/新建预约出发充电计划：POST cevs/v2/departuretimes/save。

        structure 缺省时由按钮/服务从 cevs_probe['departuretimes'] 首个
        计划结构回填（App「复制计划」语义）；字段名全部来自 retrieve 实测
        返回，非猜测。走 _cevs_write（cevs 场景加密 AES + raw_body）。
        """
        result = await self._cevs_write(vin, PATH_DEPARTURE_SAVE, structure or None)
        self._log.info(
            "FordPass cevs save -> %s", json.dumps(result, ensure_ascii=False)[:400]
        )
        return result

    async def cevs_delete_chargelog(
        self, vin: str, log_id: str | None = None,
    ) -> dict[str, Any]:
        """删除充电记录：POST cevs/v2/chargelogs/delete。

        log_id 缺省由按钮从 chargelogs 探测返回回填（chargeLogId 实测键）。
        """
        extra = {"chargeLogId": log_id} if log_id else None
        return await self._cevs_write(vin, PATH_CHARGELOGS_DELETE, extra)

    async def vehicle_auth_approval(
        self, vin: str, auth_id: str | None = None, approve: bool = True,
    ) -> dict[str, Any]:
        """车辆授权审批：POST vds/v1/vehicles/vehicle-user-auth-approval。

        同意/拒绝他人申请授权你的车；auth_id 由按钮从 user_auth_list 探测
        返回提取（兼容 authId/approvalId/applyId 实测键）。"""
        body: dict[str, Any] = {"approve": approve}
        if auth_id:
            body["authId"] = auth_id
        return await self._request("POST", PATH_VEHICLE_USER_AUTH_APPROVAL, body)

    async def srs_save_profile(
        self, vin: str, profile: dict[str, Any] | None = None, update: bool = True,
    ) -> dict[str, Any]:
        """SRS 紧急联系人档案保存：POST add-srs-profile（无档案）/
        update-srs-profile（已有档案）。profile 缺省从 srs_profile 探测
        返回回填（srsProfile 结构键名全部来自实测返回）。"""
        path = PATH_SRS_UPDATE if update else PATH_SRS_ADD
        return await self._request("POST", path, dict(profile or {}))

    async def wallbox_share(self, vin: str, wallbox_id: str, phone: str) -> dict[str, Any]:
        """家充桩分享：POST sharing/share/v3（分享给家人手机号）。"""
        return await self._request(
            "POST", PATH_WALLBOX_SHARE, {"wallboxId": wallbox_id, "phone": phone}
        )

    async def wallbox_unshare(
        self, vin: str, wallbox_id: str, sharer_id: str | None = None,
    ) -> dict[str, Any]:
        """取消家充桩分享：POST sharing/unshare/v2（sharerId 由按钮从
        sharing/query 探测返回回填）。"""
        body: dict[str, Any] = {"wallboxId": wallbox_id}
        if sharer_id:
            body["sharerId"] = sharer_id
        return await self._request("POST", PATH_WALLBOX_UNSHARE, body)

    async def wallbox_sharer_info(self, vin: str, wallbox_id: str) -> dict[str, Any]:
        """家充桩分享人信息：POST sharing/sharerInfo/v2（只读）。"""
        return await self._request(
            "POST", PATH_WALLBOX_SHARER_INFO, {"wallboxId": wallbox_id}
        )

    async def wallbox_tnc_content(self, vin: str) -> dict[str, Any]:
        """家充桩分享条款内容：POST tnc/content/v3（只读）。"""
        return await self._request("POST", PATH_WALLBOX_TNC_CONTENT)

    async def wallbox_sign_tnc(self, vin: str) -> dict[str, Any]:
        """签署家充桩分享条款：POST tnc/sign/v3。"""
        return await self._request("POST", PATH_WALLBOX_TNC_SIGN)

    async def fposb_order(
        self, vin: str, booking: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """维保预约下单：POST fposb osborder。

        booking 参数复杂（门店/工位/时间/服务项目），推荐经服务调用传入；
        缺省空 body 由网关给出缺字段错误消息便于校准。"""
        return await self._request("POST", PATH_FPOSB_ORDER, dict(booking or {}))

    async def fposb_cancel(
        self, vin: str, order_id: str | None = None,
    ) -> dict[str, Any]:
        """取消维保预约：DELETE fposb osbcancel（orderId 由按钮从
        osbhistory 探测返回回填）。"""
        if order_id:
            return await self._request("DELETE", PATH_FPOSB_CANCEL, {"orderId": order_id})
        return await self._request("DELETE", PATH_FPOSB_CANCEL)

    async def evss_charger_lock(
        self, vin: str, equipment_id: str | None = None, down: bool = True,
    ) -> dict[str, Any]:
        """充电枪锁/解锁：POST evss chargers/v1/brand/equipment/lock/down。

        equipmentId 由按钮从 evss_charger_info 探测返回回填（品牌专属桩）；
        仅电/插混/混动车型创建实体。"""
        body: dict[str, Any] = {"down": down}
        if equipment_id:
            body["equipmentId"] = equipment_id
        return await self._request("POST", PATH_EVSS_CHARGER_LOCK, body)

    async def message_read_by_id(self, message_id: str | None = None) -> dict[str, Any]:
        """单条消息已读：POST cnxapi-message/app/message/v2/readbyid。

        message_id 由按钮从 messages_page 探测返回回填（首条未读）。"""
        body: dict[str, Any] = {}
        if message_id:
            body["messageId"] = message_id
        return await self._request("POST", PATH_MESSAGES_V2_READBYID, body)

    # --------------------------------------------------------- b2c login
    async def password_login(
        self, username: str, password: str | None = None, code: str | None = None
    ) -> dict[str, str]:
        """Username/password login via the Azure AD B2C flow (v2.7.4 / v4.4.6).

        v2.7.6: B2C 的 SelfAsserted 请求改用同步 requests 客户端执行。
        实测（2026-09-29）：aiohttp 客户端会被登录服务反自动化风控
        拦截（返回 GlobalException HTML 页），而 requests（urllib3 HTTP/1.1）
        客户端携带同样的 Cookie/CSRF/参数可以正常通过（authorize -> SelfAsserted
        -> confirmed -> code）。登录是一次性配置操作，在 executor 线程执行，
        不阻塞事件循环；换取 DLT JWT 仍走标准签名请求。

        v4.4.6: 2026-10-03 起 login.ford.com.cn 前置访问防护连
        纯 HTTP 客户端（requests/curl_cffi 全指纹/Chrome headless）都返回
        567 拦截页，仅放行真实浏览器/App WebView——requests 四步流已不可用。
        新增 code 参数：由用户用真实浏览器（或辅助脚本）完成 B2C 登录后
        粘贴 authorization code，本方法跳过浏览器部分直接换 DLT token，
        从而避开该限制，无需任何绕过手段。code 为 None 时保留 requests 通道
        （防护策略窗口内仍可用）。
        """
        if code is None:
            if not password:
                raise FordPassApiError(
                    400, "B2C 密码登录需要提供密码或 authorization code"
                )
            code = await asyncio.to_thread(self._b2c_login_sync, username, password)

        return await self._exchange_b2c_code(code)

    async def _exchange_b2c_code(self, code: str) -> dict[str, str]:
        """Exchange a B2C authorization code for DLT tokens (v4.4.6).

        独立自 password_login：浏览器辅助登录（真实浏览器过访问防护）
        拿到 code 后直接调用本方法换 token，与验证码登录同构。
        """
        # exchange the code for DLT tokens (standard signed request)
        enc_code, xjw = await asyncio.to_thread(lambda: self.crypto.encrypt_field(code))
        data = await self._request(
            "POST",
            PATH_B2C_TOKEN,
            {
                "encryptedAuthCode": enc_code,
                "xjw": xjw,
                "brand": self._brand_code,
                "osType": "ANDROID",
                "policyType": "SIGNINSIGNUP",
            },
            # v5.6.9: token-exchange 家族不带 auth-token（App 实机样本实证）
            headers={"auth-token": None},
        )
        inner = data.get("data", {})
        # v4.5.4: code 无效/过期时福特派返回 {"status":600,"error":"Auth code is
        # invalid or has expired","errorCode":"201352"}（HTTP 200）——此前直接
        # inner["encryptedAccessToken"] 抛 KeyError，config_flow 报"未知错误"；
        # 现在按业务错误抛出，config_flow 显示"登录码无效或已过期"。
        if data.get("status") not in (None, 200):
            msg = data.get("error") or "B2C code exchange failed"
            ec = data.get("errorCode")
            raise FordPassApiError(
                data.get("status", 400), f"{msg} (errorCode={ec})" if ec else msg
            )
        xjw2 = inner.get("xjw", xjw)
        return {
            "access_token": await asyncio.to_thread(lambda: self.crypto.decrypt_field(
                inner["encryptedAccessToken"], xjw2
            )),
            "refresh_token": await asyncio.to_thread(lambda: self.crypto.decrypt_field(
                inner["encryptedRefreshToken"], xjw2
            )),
        }

    def _b2c_login_sync(self, username: str, password: str) -> str:
        """同步 B2C 登录（requests），返回 authorization code。"""
        import requests

        # v3.3.0: UA/headers/参数对齐福特派 6.16.0实机样本（2026-10-03）实测。
        # 此前 UA 用 Android 15 V2284A 且 authorize 不带 consent 参数——
        # B2C 风控策略更新后返回 567 拦截页（"B2C authorize failed"）。
        # App 实证：WebView UA（Android 12 BRA-AL00）、x-requested-with
        # com.ford.fordpasscn、sec-fetch-* 全家桶、authorize 带
        # tnc_consent1_accepted=true&tnc_consent2_accepted=false。
        ua = (
            "Mozilla/5.0 (Linux; Android 12; BRA-AL00 Build/V417IR; wv) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
            "Chrome/110.0.5481.154 Mobile Safari/537.36 channel/app"
        )
        authorize_query: dict[str, Any] = {
            "client_id": B2C_CLIENT_ID,
            "nonce": "defaultNonce",
            "redirect_uri": B2C_REDIRECT_URI,
            "scope": "openid",
            "response_type": "code",
            "prompt": "login",
            "country_code": "CHN",
            "ford_application_id": APPLICATION_ID,
            "language_code": "zh-CN",
            "tnc_accepted": "true",
            # v3.3.0: App 6.16.0 实机样本实证携带 consent 参数（此前 v2.7.6 实测
            # 不带能过、带会被拦——服务端策略已反转，现在必须带）。
            "tnc_consent1_accepted": "true",
            "tnc_consent2_accepted": "false",
        }
        s = requests.Session()
        s.headers.update({
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-User": "?1",
            "Sec-Fetch-Dest": "document",
            "X-Requested-With": "com.ford.fordpasscn",
        })
        # 1) authorize
        r = s.get(
            B2C_AUTHORITY + B2C_PATH_AUTHORIZE,
            params=authorize_query,
            timeout=30,
        )
        html = r.text
        if r.status_code != 200:
            # v3.3.0: 567 = 登录页访问防护拦截（login.ford.com.cn 前置防护）。
            # 2026-10-03 起纯 HTTP 客户端（requests/curl_cffi 全指纹/
            # Chrome headless）均返回 567 拦截页——防护策略变严，非代码问题；
            # App WebView 在策略窗口内可登录。
            if r.status_code == 567 or 'id="statusCode">567' in html:
                raise FordPassApiError(
                    r.status_code,
                    "B2C 登录被福特登录页访问防护拦截——"
                    "当前为纯 HTTP 客户端无法通过，请改用短信验证码登录"
                    "或稍后重试（App 登录不受影响）",
                )
            raise FordPassApiError(r.status_code, f"B2C authorize failed: {html[:200]}")
        m_csrf = re.search(r'"csrf":\s*"([^"]+)"', html)
        csrf = m_csrf.group(1) if m_csrf else ""
        if not csrf:
            csrf = s.cookies.get("x-ms-cpim-csrf") or ""
        if not csrf:
            raise FordPassApiError(
                None, "B2C authorize returned no csrf (login page blocked?)"
            )
        m_tx = re.search(r'"transId":\s*"([^"]+)"', html)
        if m_tx:
            tx_qs = m_tx.group(1)
        else:
            req_id = r.headers.get("x-request-id") or ""
            tx_raw = json.dumps({"TID": req_id}, separators=(",", ":")).encode()
            tx_qs = f"StateProperties={base64.b64encode(tx_raw).decode('ascii').rstrip('=')}"
        self._log.debug("FordPass B2C authorize ok: tx=%s csrf_len=%d", tx_qs[:60], len(csrf))

        # 2) SelfAsserted
        sign_in_name = username if username.startswith("+") else f"+86{username}"
        form = (
            "request_type=RESPONSE"
            f"&signInName={urllib.parse.quote(sign_in_name, safe='')}"
            f"&password={urllib.parse.quote(password, safe='')}"
        )
        referer = (
            B2C_AUTHORITY + B2C_PATH_AUTHORIZE + "?" +
            urllib.parse.urlencode(authorize_query)
        )
        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRF-Token": csrf,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": B2C_AUTHORITY,
            "Referer": referer,
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        }
        r2 = s.post(
            B2C_AUTHORITY + B2C_PATH_SELF_ASSERTED,
            params={"tx": tx_qs, "p": B2C_POLICY},
            data=form,
            headers=headers,
            timeout=30,
        )
        text = r2.text
        self._log.debug("FordPass B2C SelfAsserted -> %s %s", r2.status_code, text[:200])
        if r2.status_code != 200:
            raise FordPassApiError(r2.status_code, f"B2C SelfAsserted failed: {text[:200]}")
        if '"status":"200"' not in text.replace(" ", ""):
            if text.lstrip().startswith("<"):
                raise FordPassApiError(
                    None,
                    "B2C 登录被风控拦截（可能尝试过于频繁），请等待几分钟后再试或改用短信验证码登录",
                )
            raise FordPassApiError(None, f"B2C credentials rejected: {text[:200]}")

        # 3) confirmed -> 302 Location carries ?code=
        # v3.3.0: 对齐 App 6.16.0——带 diags（pageViewId 随机 UUID + pageId），
        # headers 带 WebView x-requested-with / sec-fetch 导航头。
        diags = json.dumps({
            "pageViewId": str(uuid.uuid4()),
            "pageId": "CombinedSigninAndSignup",
        }, separators=(",", ":"))
        r3 = s.get(
            B2C_AUTHORITY + B2C_PATH_CONFIRMED,
            params={
                "rememberMe": "false",
                "csrf_token": csrf,
                "tx": tx_qs,
                "p": B2C_POLICY,
                "diags": diags,
            },
            headers={
                "User-Agent": ua,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                "X-Requested-With": "com.ford.fordpasscn",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-User": "?1",
                "Sec-Fetch-Dest": "document",
                "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            },
            timeout=30,
            allow_redirects=False,
        )
        location = r3.headers.get("Location", "")
        if r3.status_code != 302:
            raise FordPassApiError(
                r3.status_code, f"B2C confirmed expected 302, got {r3.status_code}: {location[:200]}"
            )
        code = urllib.parse.parse_qs(
            urllib.parse.urlparse(location).query
        ).get("code", [""])[0]
        if not code:
            raise FordPassApiError(None, f"B2C confirmed 302 without code: {location[:200]}")
        self._log.debug("FordPass B2C authorization code acquired (len=%d)", len(code))
        return code



    # ------------------------------------------------------------ location
    async def get_lbs_token(self, vin: str) -> str:
        """Exchange an LBS token for the location gateway.

        GET /api/cnxapi-token-exchange/v1/app/third-party-token?tokenType=LBS&...
        (path verified from live request/response captures; the response's encryptedAccessToken
        is decrypted with the x_api scene, yielding the base64 token
        used as queryLocation's `token` field — VIN+UUID+server-signed tail).
        """
        now = time.time()
        if self._lbs_token and self._lbs_token_expiry and now < self._lbs_token_expiry:
            return self._lbs_token
        xapi = await asyncio.to_thread(FordPassCrypto.get, None, "x_api")
        enc_vin, xjw = await asyncio.to_thread(lambda: xapi.encrypt_field(vin))
        ts = int(now * 1000)
        params = {
            "timestamp": ts,
            "tokenType": "LBS",
            "brand": self._brand_code,
            "encryptedVin": enc_vin,
            "xjw": xjw,
        }
        params["sign"] = compute_sign(params)
        async with self._session.get(
            BASE_URL + PATH_THIRD_PARTY_TOKEN, headers=self._headers(), params=params,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            try:
                text = await resp.text()
            except UnicodeDecodeError:
                # v3.0.9: 与 _request 的 v3.0.6 保护对齐——网关偶发返回
                # 非文本响应（二进制/损坏 body）时先读原始字节诊断，
                # 再转成明确的 FordPassApiError，避免裸 codec 错误冒泡。
                raw_bytes = await resp.read()
                self._log.warning(
                    "FordPass %s -> 非文本响应 %d bytes (status=%s): %s",
                    PATH_THIRD_PARTY_TOKEN, len(raw_bytes), resp.status, raw_bytes[:80].hex(),
                )
                raise FordPassApiError(
                    resp.status, "网关返回非文本响应（瞬时异常，已按重试策略处理）"
                ) from None
            # v5.2.7: LBS token 响应日志只保留字段名，不打印任何值——
            # data.encryptedAccessToken 的密钥（x_api scene）随集成发布，
            # 值进 debug 日志即等于泄露。
            self._log.debug(
                "FordPass third-party-token -> %s %s", resp.status, _token_resp_summary(text)
            )
            if resp.status >= 400:
                raise FordPassApiError(
                    resp.status, f"third-party-token request failed (status={resp.status})"
                )
        try:
            data = json.loads(text)
        except json.JSONDecodeError as err:
            raise FordPassApiError(
                resp.status, f"non-JSON token response ({len(text)} bytes)"
            ) from err
        inner = data.get("data", {})
        enc_token = inner.get("encryptedAccessToken")
        if not enc_token:
            raise FordPassApiError(
                data.get("code"), f"no encryptedAccessToken in response (data_fields={','.join(sorted(inner.keys()))})"
            )
        token = await asyncio.to_thread(
            lambda: xapi.decrypt_field(enc_token, inner.get("xjw", xjw))
        )
        expiry_ms = inner.get("expiresIn")
        self._lbs_token = token
        self._lbs_token_expiry = expiry_ms / 1000 if expiry_ms else None
        self._log.debug("FordPass LBS token exchanged (len=%d, expiry=%s)",
                        len(token), expiry_ms)
        return token

    async def get_location(
        self, vin: str, coordinate_system: str = "wgs84"
    ) -> dict[str, Any]:
        """POST https://api-connect.ford.com.cn/lbs-map/v2/public/app/queryLocation.

        Fully reversed and verified against a live session:
          * LBS token is EXCHANGED via get_lbs_token() (server-signed tail;
            the app does not construct it locally).
          * vin      = cipher("lbs_p2c" scene, plaintext VIN), IV random
            (server accepted: no more "bad ciphertext" 2013).
          * x-sign   = base64(SHA256(SK + sorted_body + ts + PKL + SK[:5] +
            ts[-5:])).upper()  (26/26 verified).
          * response lat/lon/address are decrypted with the "lbs_p2c" scene
            key and the response's own iv (verified live: real coordinates).
        """
        token = await self.get_lbs_token(vin)
        lbs_crypto = await asyncio.to_thread(FordPassCrypto.get, None, "lbs_p2c")
        enc_vin, _iv = await asyncio.to_thread(lambda: lbs_crypto.encrypt_field(vin))
        body: dict[str, Any] = {
            "token": token,
            "vin": enc_vin,
            "iv": _iv,
            "source": 0,
            "secretVersion": "1.0",
        }
        ts = int(time.time() * 1000)
        headers = {
            "user-agent": "Dart (dart:io)",
            "x-dynatrace": "",
            "accept-encoding": "gzip",
            "ocp-application-id": LBS_APP_ID,
            "x-timestamp": str(ts),
            "x-appkey": LBS_APP_KEY,
            "ostype": "android",
            "content-type": "application/json",
            "clienttype": "FP",
            "x-sign": compute_lbs_sign(body, ts),
        }
        # v5.2.4: LBS 请求日志不记录 body 值——body 携带 LBS 明文 token
        # （token 不得进日志）。header 仅保留键名，
        # 省略 x-sign 值。
        self._log.debug("FordPass LBS POST %s header_fields=%s body_fields=%s",
                        PATH_QUERY_LOCATION,
                        ",".join(sorted(headers.keys())),
                        ",".join(sorted(body.keys())) if isinstance(body, dict) else "")
        async with self._session.post(
            LBS_BASE_URL + PATH_QUERY_LOCATION, headers=headers, json=body,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            try:
                text = await resp.text()
            except UnicodeDecodeError:
                # v3.0.9: 与 _request 的 v3.0.6 保护对齐——网关偶发返回
                # 非文本响应（二进制/损坏 body）时先读原始字节诊断，
                # 再转成明确的 FordPassApiError，避免裸 codec 错误冒泡。
                raw_bytes = await resp.read()
                self._log.warning(
                    "FordPass %s -> 非文本响应 %d bytes (status=%s): %s",
                    PATH_QUERY_LOCATION, len(raw_bytes), resp.status, raw_bytes[:80].hex(),
                )
                raise FordPassApiError(
                    resp.status, "网关返回非文本响应（瞬时异常，已按重试策略处理）"
                ) from None
            self._log.debug("FordPass LBS -> %s %s", resp.status, text[:500])
            if resp.status >= 400:
                raise FordPassApiError(resp.status, text[:300])
        try:
            data = json.loads(text)
        except json.JSONDecodeError as err:
            raise FordPassApiError(resp.status, f"non-JSON LBS response: {text[:200]}") from err
        inner = data.get("data", data)
        if not inner.get("iv") or not inner.get("lat"):
            return inner
        try:
            iv = inner["iv"]
            resp_crypto = await asyncio.to_thread(FordPassCrypto.get, None, "lbs_p2c")
            lat = await asyncio.to_thread(lambda: resp_crypto.decrypt_field(inner["lat"], iv))
            lon = await asyncio.to_thread(lambda: resp_crypto.decrypt_field(inner["lon"], iv))
            # FordPass CN LBS returns GCJ-02 (火星坐标), verified live 2026-09-30:
            # the raw point (111.719153, 40.828175) matches the official app's
            # map marker exactly, while re-converting it WGS-84->GCJ-02 shifted
            # the marker ~600 m. v2.7.9:
            #   * "wgs84" (HA official map / OSM): convert GCJ-02 -> WGS-84 so
            #     the built-in map shows the vehicle accurately;
            #   * "gcj02" (高德/腾讯地图): keep as-is (already GCJ-02).
            if coordinate_system == "wgs84":
                lat, lon = normalize_wgs84(lat, lon)
            return {
                "lat": lat,
                "lon": lon,
                "address": await asyncio.to_thread(lambda: resp_crypto.decrypt_field(inner["address"], iv)),
                "uploadTime": inner.get("uploadTime"),
                "iv": iv,
            }
        except Exception:  # noqa: BLE001 - keep raw on parse failure
            self._log.warning("Location decryption failed, returning raw payload")
        return inner
