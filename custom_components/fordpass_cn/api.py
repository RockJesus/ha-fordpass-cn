"""API client for FordPass China (cn.api.mps.ford.com.cn).

Protocol reproduced from the official FordPass China app 6.14.0 and verified
against a live capture:

* every request carries fixed headers (application-id / appversion / ...);
* sensitive fields (phone, passcode, VIN, response bodies) are encrypted with
  a custom white-box AES-256-CBC (see wbsk.py);
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
import uuid
import urllib.parse
import uuid
from typing import Any

import aiohttp

from .const import (
    APPLICATION_ID,
    APP_VERSION,
    B2C_AUTHORITY,
    B2C_CLIENT_ID,
    B2C_PATH_AUTHORIZE,
    B2C_PATH_CONFIRMED,
    B2C_PATH_SELF_ASSERTED,
    B2C_POLICY,
    B2C_REDIRECT_URI,
    BASE_URL,
    CHIRP_TO_ANNOUNCE,
    CHIRP_TYPE_OPTIONS,
    CLIENT_TYPE,
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
    PATH_B2C_TOKEN,
    PATH_CCFEATURES,
    PATH_CHARGELOGS_RETRIEVE,
    PATH_COMMAND_STATUS,
    PATH_CRCC_PROFILE,
    PATH_CVFEATURES,
    PATH_CEVS_COMMAND_STATUS,
    CEVS_RSA_PUBKEYS,
    PATH_DEPARTURE_TIMES_RETRIEVE,
    PATH_DEPARTURE_TOGGLE_OFF,
    PATH_DEPARTURE_TOGGLE_ON,
    PATH_DEVICE_LIST,
    PATH_GENERATE_PASSCODE,
    PATH_MAINTENANCE_HISTORY,
    PATH_MAINTENANCE_PLAN,
    PATH_MCM_MESSAGES_V3,
    PATH_MESSAGES_PAGE,
    PATH_MESSAGES_READ,
    PATH_MESSAGES_SUMMARY,
    PATH_MESSAGES_V2_PAGE,
    PATH_MONITOR_TRAFFIC,
    PATH_ONLINENR,
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
    PATH_SHARE_LIST,
    PATH_SIM_INFO,
    PATH_SMARTWALLBOX_BINDING,
    PATH_SMARTWALLBOX_CHARGE_START,
    PATH_SMARTWALLBOX_CHARGE_STOP,
    PATH_SMARTWALLBOX_UNBIND,
    PATH_SMARTWALLBOX_AUTHORITY,
    PATH_SMARTWALLBOX_CHG_STATUS_WB,
    PATH_SMARTWALLBOX_CHG_WB,
    PATH_SMARTWALLBOX_ORDERS,
    PATH_SMARTWALLBOX_SHARING_QUERY,
    PATH_SMARTWALLBOX_CONFIG,
    PATH_SMARTWALLBOX_RECORDS,
    PATH_SRS_PROFILE,
    PATH_THIRD_PARTY_TOKEN,
    PATH_USER_AUTH_STATUS,
    PATH_USER_PREF_GROUPS,
    PATH_USER_PREF_LIST,
    PATH_USER_REDDOT,
    PATH_USER_VEHICLES,
    PATH_V5_ANNOUNCE_STATUS,
    PATH_V5_HONK,
    PATH_V5_PANIC,
    PATH_EVSS_ORDERS,
    PATH_EVSS_STATIONS,
    PATH_VEHICLE_USER_AUTH,
    PATH_VEHICLES_LIST,
    PATH_VPOI_CHARGESTATIONS,
    PATH_VEHICLE_STATUS,
    PATH_VIDEO_FILE,
    PATH_VIDEO_START,
    PATH_VIDEO_STOP,
    PATH_WARRANTY,
    PATH_WIFI_STATUS,
    PAYLOAD_KEY,
    PREF_DURATION,
    PREF_SOUND_TYPE,
    PREF_TARGET_TEMP,
    SECRET_KEY,
    TOUCH_POINT,
    V5_BASE_URL
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


def normalize_gcj02(lat, lng):
    """Apply WGS84->GCJ02 and preserve the original value type (str/float)."""
    lat_f = float(lat)
    lng_f = float(lng)
    lat_g, lng_g = wgs84_to_gcj02(lat_f, lng_f)
    if isinstance(lat, str):
        return f"{lat_g:.6f}", f"{lng_g:.6f}"
    return lat_g, lng_g


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
    """App 签名序列化（v3.1.4，从 R3Signature 反编译还原）：
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

    Recovered from the app's Dart AOT code (R3Signature::_getAppSecret2):
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
    """Re-produce the LBS gateway `x-sign` (recovered from the app's Dart AOT
    code `LbsSignature::signatureR3` + heap dump of the assembled string).

    raw = SK + sorted_body_params + ts + PKL + SK[:5] + ts[8:]
    x-sign = base64(SHA256(utf8(raw))).upper()
    Verified 26/26 against live captures (same as the app: the result is
    upper-cased before it is sent in the x-sign header).
    """
    biz = "&".join(f"{k}={body[k]}" for k in sorted(body))
    ts = str(timestamp_ms)
    raw = f"{LBS_APP_KEY}{biz}{ts}{LBS_PAYLOAD_KEY}{LBS_APP_KEY[:5]}{ts[8:]}"
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return base64.b64encode(digest).decode("ascii").upper()



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

    def __init__(self, code: Any = None, message: str | None = None) -> None:
        super().__init__(message or f"FordPass error ({code})")
        self.code = code
        self.message = message


class FordPassApi:
    def __init__(self, session: aiohttp.ClientSession, logger) -> None:
        self._session = session
        self._log = logger
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._lbs_token: str | None = None
        self._lbs_token_expiry: float | None = None
        self._retrying = False
        self.on_token_refresh = None
        # v3.7.6: cevs 域探测诊断（预约出发/充电）——每个端点探测结果
        # 存这里（ok/rejected/failed + 摘要），coordinator 暴露给诊断
        # 传感器，HA 侧可直接读取（无需导出日志）。
        self.cevs_diag: dict[str, str] = {}

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
            "appversion": APP_VERSION,
            "ostype": OS_TYPE,
            "osversion": OS_VERSION,
            "clienttype": CLIENT_TYPE,
            "application-id": APPLICATION_ID,
            "content-type": "application/json; charset=utf-8",
            "accept-encoding": "gzip",
            "x-dynatrace": "",
            "x-b3-traceid": _new_traceid(),
        }
        if self._access_token:
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
        self._log.debug("FordPass %s %s payload=%s", method, url, kwargs)
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
                )
            self._log.debug("FordPass %s -> %s %s", path, resp.status, text[:2000])
            if resp.status == 401 and self._refresh_token and not self._retrying:
                self._retrying = True
                try:
                    self._log.debug("FordPass access token expired, refreshing once")
                    new = await self.refresh_token(self._refresh_token)
                    self._access_token = new["access_token"]
                    if self.on_token_refresh is not None:
                        self.on_token_refresh(new["access_token"])
                    return await self._request(method, path, body, query, base, raw, headers)
                except Exception as exc:  # noqa: BLE001
                    self._log.error("FordPass token refresh failed: %s", exc)
                    raise FordPassApiError(401, f"token refresh failed: {exc}") from exc
                finally:
                    self._retrying = False
            if resp.status >= 400:
                raise FordPassApiError(resp.status, text[:300])
            if not text:
                return None
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                if raw:
                    return text
                raise FordPassApiError(resp.status, f"non-JSON response: {text[:200]}")
        if isinstance(data, dict):
            # Business-layer token expiry: the gateway answers HTTP 200 with
            # {"status":600,"error":"Swap token failed {…Cat2 token expired…}",
            #  "errorCode":"100502"} once the internal Cat2 token lapses
            # (observed 2026-09-29 after the ForceRefresh flow succeeded).
            # Treat it exactly like an HTTP 401: refresh once and retry.
            status = data.get("status")
            error = str(data.get("error") or "")
            if (
                status == 600
                and "token" in error.lower()
                and self._refresh_token
                and not self._retrying
            ):
                self._retrying = True
                try:
                    self._log.debug("FordPass Cat2 token expired, refreshing once")
                    new = await self.refresh_token(self._refresh_token)
                    self._access_token = new["access_token"]
                    if self.on_token_refresh is not None:
                        self.on_token_refresh(new["access_token"])
                    return await self._request(method, path, body, query, base, raw, headers)
                except Exception as exc:  # noqa: BLE001
                    self._log.error("FordPass token refresh failed: %s", exc)
                    raise FordPassApiError(401, f"token refresh failed: {exc}") from exc
                finally:
                    self._retrying = False
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
            {"encryptedPhoneNumber": enc, "touchPoint": TOUCH_POINT, "xjw": xjw},
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
        fails with ``whitebox decrypt error``.
        """
        iv = bytes.fromhex(iv_hex) if iv_hex else None
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
                "touchPoint": TOUCH_POINT,
                "brand": "FORD",
                "osType": "ANDROID",
                "xjw": xjw,
            },
        )
        inner = data.get("data", {})
        xjw2 = inner.get("xjw", xjw)
        return {
            "access_token": await asyncio.to_thread(lambda: self.crypto.decrypt_field(
                inner["encryptedAccessToken"], xjw2
            )),
            "refresh_token": await asyncio.to_thread(lambda: self.crypto.decrypt_field(
                inner["encryptedRefreshToken"], xjw2
            )),
        }

    async def refresh_token(self, refresh_token: str) -> dict[str, str]:
        # The server DTO (RefreshDLTTokenRequest) rejects any extra field
        # (e.g. touchPoint -> JSON parse error); only these three are allowed.
        enc, xjw = await asyncio.to_thread(lambda: self.crypto.encrypt_field(refresh_token))
        data = await self._request(
            "POST",
            PATH_REFRESH_TOKEN,
            {"encryptedRefreshToken": enc, "brand": "FORD", "xjw": xjw},
        )
        inner = data.get("data", {})
        xjw2 = inner.get("xjw", xjw)
        return {
            "access_token": await asyncio.to_thread(lambda: self.crypto.decrypt_field(
                inner["encryptedAccessToken"], xjw2
            )),
            "expires_in": inner.get("expiresIn"),
        }

    async def revoke_token(self, refresh_token: str) -> None:
        enc, xjw = await asyncio.to_thread(lambda: self.crypto.encrypt_field(refresh_token))
        await self._request(
            "POST",
            PATH_REVOKE_TOKEN,
            {"encryptedRefreshToken": enc, "brand": "FORD", "xjw": xjw},
        )

    # -------------------------------------------------------------- vehicles
    async def get_vehicles(self) -> list[dict[str, Any]]:
        """GET /v5/vehicles/list (verified live)."""
        data = await self._request(
            "GET",
            PATH_VEHICLES_LIST,
            query={"appKey": "fordpass", "appVersion": APP_VERSION, "clientType": CLIENT_TYPE},
        )
        inner = data.get("data", {})
        xjw = inner.get("xjw")
        vehicles: list[dict[str, Any]] = []
        for item in inner.get("list", []):
            v = dict(item)
            if xjw:
                for key in ("encryptedVin", "encryptedNickName", "encryptedLicenseplate"):
                    if v.get(key):
                        v[key] = await asyncio.to_thread(lambda: self.crypto.decrypt_field(v[key], xjw))
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
                    plain = await asyncio.to_thread(
                        lambda: self.crypto.decrypt_field(
                            inner["encryptedResponseBody"], inner["xjw"]
                        )
                    )
                    result = json.loads(plain)
                    self._log.debug(
                        "FordPass vehicle-status raw: %s",
                        json.dumps(result, ensure_ascii=False)[:6000],
                    )
                    return result
                return inner
            except (FordPassApiError, json.JSONDecodeError, UnicodeDecodeError) as exc:
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
        CancelVA 带 [{key: VAType, value: 4}]——HAR 实测确认）。
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

        2026-10-01 从福特派 App（libapp.so, blutter 反编译）还原：
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
                )
            self._log.debug("FordPass v5 honk POST -> %s %s", resp.status, text[:500])
            if resp.status == 401 and self._refresh_token and not self._retrying:
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
                            raise FordPassApiError(resp2.status, text2[:300])
                        if not text2:
                            return {}
                        try:
                            return json.loads(text2)
                        except json.JSONDecodeError:
                            return {"raw": text2}
                except Exception as exc:  # noqa: BLE001
                    self._log.error("FordPass v5 honk token refresh failed: %s", exc)
                    raise FordPassApiError(401, f"token refresh failed: {exc}") from exc
                finally:
                    self._retrying = False
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

        v3.2.1: HAR 实证（2026-10-03，福特派 6.16.0）——App 的「鸣笛寻车
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
        """POST /api/vehicles/v5/{vin}/panic/{paniconduration} — 声光共舞。

        App 的 FordPanicCommand（v3.1.8 还原）：灯+喇叭警报，duration 直接
        写在路径里。锐际实测 404（云端不支持该通道），调用方应捕获
        FordPassApiError(404) 并向用户明确提示车型不支持。
        """
        url = V5_BASE_URL + PATH_V5_PANIC.format(vin=vin, duration=duration)
        async with self._session.request("POST", url, headers=self._headers()) as resp:
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
                )
            self._log.debug("FordPass v5 panic POST -> %s %s", resp.status, text[:300])
            if resp.status == 401 and self._refresh_token and not self._retrying:
                self._retrying = True
                try:
                    new = await self.refresh_token(self._refresh_token)
                    self._access_token = new["access_token"]
                    if self.on_token_refresh is not None:
                        self.on_token_refresh(new["access_token"])
                    async with self._session.request(
                        "POST", url, headers=self._headers()
                    ) as resp2:
                        text2 = await resp2.text()
                        if resp2.status >= 400:
                            raise FordPassApiError(resp2.status, text2[:300])
                        if not text2:
                            return {}
                        try:
                            return json.loads(text2)
                        except json.JSONDecodeError:
                            return {"raw": text2}
                except Exception as exc:  # noqa: BLE001
                    self._log.error("FordPass v5 panic token refresh failed: %s", exc)
                    raise FordPassApiError(401, f"token refresh failed: {exc}") from exc
                finally:
                    self._retrying = False
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
                )
            self._log.debug("FordPass v5 honk DELETE -> %s %s", resp.status, text[:500])
            if resp.status == 401 and self._refresh_token and not self._retrying:
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
                            raise FordPassApiError(resp2.status, text2[:300])
                        if not text2:
                            return {}
                        try:
                            return json.loads(text2)
                        except json.JSONDecodeError:
                            return {"raw": text2}
                except Exception as exc:  # noqa: BLE001
                    self._log.error("FordPass v5 honk token refresh failed: %s", exc)
                    raise FordPassApiError(401, f"token refresh failed: {exc}") from exc
                finally:
                    self._retrying = False
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

        v3.1.13 从福特派 6.16.0（libapp.so, blutter 对象池）还原：App
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
                )
            self._log.debug(
                "FordPass v5 announcestatus -> %s %s", resp.status, text[:500]
            )
            if resp.status == 401 and self._refresh_token and not self._retrying:
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
                            raise FordPassApiError(resp2.status, text2[:300])
                        if not text2:
                            return {}
                        try:
                            return json.loads(text2)
                        except json.JSONDecodeError:
                            return {"raw": text2}
                except Exception as exc:  # noqa: BLE001
                    self._log.error(
                        "FordPass v5 announcestatus token refresh failed: %s", exc
                    )
                    raise FordPassApiError(401, f"token refresh failed: {exc}") from exc
                finally:
                    self._retrying = False
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

        v3.1.13 从福特派 6.16.0 还原端点：App VcsRepositoryProvider::
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

        v3.1.13 还原：App VehicleManagerEndpoint.searchVehicleParkingImage，
        请求体含 carId（= 车辆列表 encryptedCarId）。锐际 encryptedCarId=null
        → 集成不创建按钮、不调用。响应含图片记录列表（车型支持时）。
        """
        return await self._request(
            "POST", PATH_PARKING_IMAGE, {"carId": car_id, "vin": vin}
        )

    async def monitor_traffic(self, vin: str, car_id: str) -> dict[str, Any]:
        """POST /api/cnxapi-pds/v1/search-vehicle-monitor-traffic — 行车监控查询。

        App VehicleManagerEndpoint.searchVehicleMonitorTraffic，与停车影像
        同组（PDS 网关），请求体含 carId。锐际无该硬件 → 不创建实体。
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

        v3.1.4 UserPreferenceV2 真通道；v3.2.2 修正字段与值（HAR 实证
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
        App 命名风格推断的候选值，待电马/插混账号 HAR 实测校准——保存失败
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
        """
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
        """
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
        """未读消息摘要：GET /api/cnxapi-message/app/messages/summary（HAR 实测 200）。

        2026-10-03 抓包还原：无 encryptedVin 参数，仅 timestamp+sign 签名
        （_request 自动附加）。响应 data.summary.{allRedDotStatus,
        unReadCategoryId, unReadCategoryDescription, readMessageSubject}
        + data.categories[]。
        """
        return await self._request("GET", PATH_MESSAGES_SUMMARY)

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

        实测响应（2026-10-02，锐际）：
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

        实测响应（2026-10-02，锐际）：
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
        """SIM 卡信息：GET /api/cnxapi-cds/v1/vehicle/sim/info（200 实测）。"""
        return await self._get_signed(PATH_SIM_INFO, vin)

    async def get_wifi_status(self, vin: str) -> dict[str, Any]:
        """WiFi 热点状态：GET /api/cnxapi-cds/v1/vehicle/wifi/status（200 实测）。"""
        return await self._get_signed(PATH_WIFI_STATUS, vin)

    async def get_warranty(self, vin: str) -> dict[str, Any]:
        """质保信息：GET /api/cnxapi-cds/v1/warranty。

        签名已过（R3 无 appKey），但服务端 100502 Validation exception
        （参数校验，App 请求参数尚未完全还原，待模拟器抓包补充）。
        """
        return await self._get_signed(PATH_WARRANTY, vin)

    async def wait_command_complete(
        self, vin: str, command_id: str, command_type: str,
        timeout: float = 60.0, poll: float = 2.0,
    ) -> tuple[bool, dict[str, Any] | None]:
        """Poll command-execution-status until ForceRefresh finishes (v2.7.1).

        Verified from the official app capture (2026-09-29): after
        send-command returns a commandId, the app polls
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
                    continue
                last = result
                vs = result.get("vehiclestatus", result)
                if isinstance(vs, dict) and vs.get("vin"):
                    # fresh snapshot: key fields carry status=CURRENT
                    if any(
                        isinstance(v, dict) and v.get("status") == "CURRENT"
                        for v in vs.values()
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

        Verified from the official app capture (2026-09-29): this endpoint
        takes the normal white-box encrypted VIN + random xjw, reuses the
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

    # ------------------------------------------------------------ ota setting
    async def get_ota_setting(self, vin: str) -> dict[str, Any] | None:
        """GET /api/alert/v1/ota/setting-info — OTA 设置状态（v3.1.3）。

        实测（2026-10-02）：标准 R3 签名（timestamp+sign+encryptedVin/xjw）
        返回 HTTP 200；同族的 ota/detail、ota/versions 在锐际上被服务端拒绝
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
    @staticmethod
    def _rsa_encrypt_cevs(payload: bytes, pubkey_pem: str) -> str:
        """RSA/ECB/PKCS1v15 加密（v3.7.4，cevs 域；v3.7.9 支持 payload 变体）。

        福特派 App 的 encryptByRSA 用 RSA 公钥加密敏感字段后放进
        cevs 请求体。v3.7.9：payload 变体枚举（纯 VIN / JSON{"vin"} /
        JSON{"vin","xjw"} / AES-ECB(vin,key=xjw)），服务器解密层行为
        由诊断传感器自读区分。cryptography 为 HA Core 自带依赖。
        """
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives import serialization

        pub = serialization.load_pem_public_key(pubkey_pem.encode("utf-8"))
        ct = pub.encrypt(payload, padding.PKCS1v15())
        return base64.b64encode(ct).decode("ascii")

    @staticmethod
    def _cevs_variant_payloads(vin: str, xjw: str) -> list[tuple[str, bytes]]:
        """cevs 加密链变体 payload（v3.7.14：3 公钥 × 2 payload 形态）。

        vname = k{1,2,3}_{plain,vinxjw}：
          k1 = libapp.so PKCS#1 2048；k2 = rsa_feed_back 2048；k3 = cnesl_prod 4096
          plain   = RSA(VIN)（payload 仅 VIN 明文）
          vinxjw  = RSA(VIN + xjw)（组合串，混合加密标准形态）
        """
        variants: list[tuple[str, bytes]] = [
            (f"k1_plain", vin.encode("utf-8")),
            (f"k2_plain", vin.encode("utf-8")),
            (f"k3_plain", vin.encode("utf-8")),
            (f"k1_vinxjw", (vin + xjw).encode("utf-8")),
            (f"k2_vinxjw", (vin + xjw).encode("utf-8")),
            (f"k3_vinxjw", (vin + xjw).encode("utf-8")),
        ]
        return variants

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

    async def probe_cevs_variant(self, vin: str, vname: str) -> None:
        """手动触发 cevs 指定变体探测（service 调用，3 请求/次，防风控降级）。

        v3.7.14: 变体 k{1,2,3}_{plain,vinxjw}——k1=libapp2048 / k2=feed_back2048 /
        k3=cnesl4096，plain=RSA(VIN)、vinxjw=RSA(VIN+xjw)；**只对 vname 指定公钥发
        1 次**（3 请求/次，保持风控安全）。departuretimes/commandstatus body 也
        补 xjw（DTO 可能要求 {vin,xjw}）。plaintext 系列沿用 v3.7.13。
        """
        _, xjw_now = await asyncio.to_thread(lambda: self.crypto.encrypt_field(vin))
        payloads = dict(self._cevs_variant_payloads(vin, xjw_now))
        plaintext_ok = vname in ("plaintext", "plaintext_xjw")
        wbk_ok = vname in ("wbk_plain", "wbk_shared")
        if vname not in payloads and not plaintext_ok and not wbk_ok:
            self.cevs_diag["variant_error"] = f"unknown variant {vname}"
            return
        pems: list[str] = [None]
        if not plaintext_ok and not wbk_ok:
            try:
                kidx = int(vname.split("_")[0][1:]) - 1
                pems = [CEVS_RSA_PUBKEYS[kidx]]
            except Exception:
                self.cevs_diag["variant_error"] = f"bad variant {vname}"
                return
        for name, path, body in (
            ("departuretimes", PATH_DEPARTURE_TIMES_RETRIEVE, {"vin": None}),
            ("chargelogs", PATH_CHARGELOGS_RETRIEVE, {"vin": None, "xjw": xjw_now}),
            ("cevs_command_status", PATH_CEVS_COMMAND_STATUS, {"vin": None}),
        ):
            b = dict(body)
            if wbk_ok:
                # v3.7.16: cevs 真实加密 = 白盒 AES-CBC（与 login/token refresh 同链，
                # xjw 即 CBC IV hex）——此前全部 RSA 变体方向错误（服务器白盒解密失败）
                key = f"{name}_wbk_{vname}"
                try:
                    iv = None if vname == "wbk_plain" else xjw_now
                    enc, xjw2 = await asyncio.to_thread(
                        lambda: self.crypto.encrypt_field(vin, iv),
                    )
                    b["vin"] = enc
                    b["xjw"] = xjw2
                    data = await self._request("POST", path, b, raw_body=True)
                except Exception as exc:  # noqa: BLE001
                    self.cevs_diag[key] = f"failed: {str(exc)[:200]}"
                    self._log.debug(
                        "FordPass probe %s wbk_%s failed: %s", name, vname, exc,
                    )
                    continue
                if isinstance(data, dict) and data:
                    if _is_err_resp(data):
                        self.cevs_diag[key] = f"rejected: {json.dumps(data, ensure_ascii=False)[:200]}"
                        continue
                    self.cevs_diag[key] = f"ok: {json.dumps(data, ensure_ascii=False)[:200]}"
                else:
                    self.cevs_diag[key] = "ok(empty)"
                continue
            b["xjw"] = xjw_now  # 三端点统一补 xjw（DTO {vin,xjw} 假设）
            for pem in pems:
                if plaintext_ok:
                    b["vin"] = vin
                    key = f"{name}_rsa_{vname}"
                    try:
                        data = await self._request("POST", path, b, raw_body=True)
                    except Exception as exc:  # noqa: BLE001
                        self.cevs_diag[key] = f"failed: {str(exc)[:200]}"
                        self._log.debug(
                            "FordPass probe %s rsa_%s failed: %s", name, vname, exc,
                        )
                        continue
                    if isinstance(data, dict) and data:
                        if _is_err_resp(data):
                            self.cevs_diag[key] = f"rejected: {json.dumps(data, ensure_ascii=False)[:200]}"
                            continue
                        self.cevs_diag[key] = f"ok: {json.dumps(data, ensure_ascii=False)[:200]}"
                    else:
                        self.cevs_diag[key] = "ok(empty)"
                else:
                    await self._probe_cevs_variant_once(
                        name, path, b, vname, payloads[vname], pem,
                    )

    async def probe_extra_endpoints(self, vin: str) -> dict[str, Any] | None:
        """探测只读 GET 端点响应并返回结果供实体创建（v3.4.6）。

        v3.3.7 起探测 maintenance-history / departuretimes retrieve 仅打日志；
        v3.4.6 改为**返回探测结果**（coordinator 缓存 24h），sensor 按
        "探测到数据才创建"接入实体。全部只读/幂等 GET，不触发任何车辆
        动作；非能力车型/无数据端点返回空，不创建实体（全车型适配）。
        端点来源：6.16.0 APK libapp.so 字符串逆向（2026-10-08）+ 既有探测。

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

        def _is_err_resp(data: dict) -> bool:
            if "errorCode" in data or "error" in data:
                return True
            st = data.get("status")
            if isinstance(st, (int, str)):
                try:
                    return int(st) not in (0, 200)
                except (TypeError, ValueError):
                    pass
            return False

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
            # v3.5.0: APK 6.16.0 补全端点（全部只读/幂等，不触发车辆动作）
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
        }
        # v3.5.1: 账号级端点。v3.5.6 实证——GET 全 404（v3.5.1/3.5.2 移除
        # 车辆参数后仍 404 → 路由为 POST-only）；POST body 需
        # clientType/appVersion/encryptedVin/xjw（uservehicles 错误消息
        # 明确给出）。GET 保持无参（同 messages/summary），POST 带参。
        account_probes = {
            "uservehicles": (PATH_USER_VEHICLES, {"clientType": CLIENT_TYPE,
                                                  "appVersion": APP_VERSION}, True),
            "messages_v2": (PATH_MESSAGES_V2_PAGE, None, False),
            "share_list": (PATH_SHARE_LIST, None, True),
            # v3.6.2: 待开发清单补全——消息中心 v3（mcm 域，GET 无参同 summary）、
            # 用户级红点（cnesl-user，账号级只读）。探测到数据才创建。
            "mcm_messages_v3": (PATH_MCM_MESSAGES_V3, None, True),
            "user_reddot": (PATH_USER_REDDOT, None, True),
            # v3.7.2: 车辆授权管理列表（vehicle-user-auth 查询该车辆授权用户，
            # 只读；非授权/无数据的车型不创建）。公共充电探测（纯电/插混专属，
            # 位置相关无参探测失败→不创建，全车型安全）：
            "user_auth_list": (PATH_VEHICLE_USER_AUTH, None, True),
            "evss_stations": (PATH_EVSS_STATIONS, None, True),
            "evss_orders": (PATH_EVSS_ORDERS, None, True),
            "vpoi_chargestations": (PATH_VPOI_CHARGESTATIONS, None, True),
        }

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
                except Exception as exc:  # noqa: BLE001 - 探测失败只记录
                    self._log.debug("FordPass probe %s %s failed: %s", name, method, exc)
                    continue
                if isinstance(data, dict) and data:
                    if _is_err_resp(data):
                        # 业务拒绝（车型无能力）不试下一种形态
                        self._log.info(
                            "FordPass probe %s %s -> (rejected: %s)",
                            name, method, json.dumps(data, ensure_ascii=False)[:200],
                        )
                        return None
                    self._log.info(
                        "FordPass probe %s %s -> %s",
                        name, method, json.dumps(data, ensure_ascii=False)[:600],
                    )
                    return data
            self._log.info("FordPass probe %s -> (empty)", name)
            return None

        result: dict[str, Any] = {}
        # 车辆级端点：带 encryptedVin/xjw 签名
        for name, spec in probes.items():
            data = await _probe(name, spec, signed=True)
            if data:
                result[name] = data
        # 账号级探测结果 api 实例级缓存（v3.5.2）：多 VIN 账号每车一个
        # coordinator 并发探测时仅首车发网络请求，其余车直接复用——
        # 符合风控"探测类请求只在登录后执行一次并缓存"（防福特云限流）
        account_cache = getattr(self, "_account_probe_cache", None)
        if account_cache is not None:
            result.update(account_cache)
        else:
            account_res: dict[str, Any] = {}
            for name, spec in account_probes.items():
                data = await _probe(name, spec, signed=False)
                if data:
                    account_res[name] = data
            self._account_probe_cache = dict(account_res)
            result.update(account_res)
        # v3.7.4/3.7.5: cevs 域 RSA 探测变体——原白盒 AES 密文/明文 vin 均被
        # 服务器 RSA 解密失败（Impossible modulus）；v3.7.4 日志实证 RSA 密文
        # vin 通过 modulus 校验（不再 Impossible modulus），DTO 报
        # Unrecognized field "timestamp"（不接受外层签名）→ raw_body 裸发；
        # chargelogs 报 Missing required creator property 'xjw' → 补 xjw
        # （与 encrypt_field 同会话 IV）。失败仅日志，不创建、不进轮询。
        _, xjw_now = await asyncio.to_thread(lambda: self.crypto.encrypt_field(vin))
        # v3.7.10: 单变体（plain）基线探测——v3.7.9 一次发 12 请求（3 端点×
        # 4 变体）被福特云风控静默降级（全部 200+空 body），故初始只发 1 变体；
        # 其余变体用 fordpass_cn.probe_cevs_variant service 人工逐个触发
        # （每次 3 请求，诊断传感器自读区分解密层行为）。
        payloads = dict(self._cevs_variant_payloads(vin, xjw_now))
        for name, path, body in (
            ("departuretimes", PATH_DEPARTURE_TIMES_RETRIEVE, {"vin": None}),
            ("chargelogs", PATH_CHARGELOGS_RETRIEVE, {"vin": None, "xjw": xjw_now}),
            ("cevs_command_status", PATH_CEVS_COMMAND_STATUS, {"vin": None}),
        ):
            for pem in CEVS_RSA_PUBKEYS:
                await self._probe_cevs_variant_once(
                    name, path, body, "plain", payloads["plain"], pem,
                )
        return result or None

    async def probe_smartwallbox(self, vin: str) -> dict[str, Any] | None:
        """探测家充桩管理（smartwallbox）端点结构（只读/幂等，v3.4.5）。

        6.16.0 APK libapp.so 字符串逆向端点（2026-10-08，请求细节待 HAR
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

    # ------------------------------------------------ v3.5.0 新增命令/查询
    async def mark_messages_read(
        self, category_id: str | None = None, message_id: str | None = None
    ) -> dict[str, Any]:
        """标记消息已读：POST /api/cnxapi-message/app/messages/read（v3.5.0）。

        6.16.0 APK 端点（2026-10-09 逆向），请求体随 App 版本/消息分类可能
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

    # --------------------------------------------------------- b2c login
    async def password_login(
        self, username: str, password: str
    ) -> dict[str, str]:
        """Username/password login via the Azure AD B2C flow (v2.7.4).

        v2.7.6: B2C 的 SelfAsserted 请求改用同步 requests 客户端执行。
        抓包实测（2026-09-29）：aiohttp 客户端会被 Azure AD B2C 反自动化风控
        拦截（返回 GlobalException HTML 页），而 requests（urllib3 HTTP/1.1）
        客户端携带同样的 Cookie/CSRF/参数可以正常通过（authorize -> SelfAsserted
        -> confirmed -> code）。登录是一次性配置操作，在 executor 线程执行，
        不阻塞事件循环；换取 DLT JWT 仍走标准签名请求。
        """
        code = await asyncio.to_thread(self._b2c_login_sync, username, password)

        # 4) exchange the code for DLT tokens (standard signed request)
        enc_code, xjw = await asyncio.to_thread(lambda: self.crypto.encrypt_field(code))
        data = await self._request(
            "POST",
            PATH_B2C_TOKEN,
            {
                "encryptedAuthCode": enc_code,
                "xjw": xjw,
                "brand": "FORD",
                "osType": "ANDROID",
                "policyType": "SIGNINSIGNUP",
            },
        )
        inner = data.get("data", {})
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
        import requests  # noqa: F401  (Home Assistant 运行时自带)

        # v3.3.0: UA/headers/参数对齐福特派 6.16.0 HAR 实测（2026-10-03）。
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
            # v3.3.0: App 6.16.0 HAR 实证携带 consent 参数（此前 v2.7.6 实测
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
            # v3.3.0: 567 = 腾讯云 EdgeOne WAF 拦截（login.ford.com.cn 前置
            # EdgeOne 安全防护）。实测 2026-10-03 起纯 HTTP 客户端（requests/
            # curl_cffi 全指纹/Chrome headless）均返回 567 拦截页——WAF 策略
            # 变严，非代码问题；App WebView 在策略窗口内可登录。
            if "EdgeOne" in html or 'id="statusCode">567' in html:
                raise FordPassApiError(
                    r.status_code,
                    "B2C 登录被福特登录页安全防护（腾讯云 EdgeOne）拦截——"
                    "当前为纯 HTTP 客户端无法通过 WAF，请改用短信验证码登录"
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
        (path verified from live captures; the response's encryptedAccessToken
        is white-box decrypted with the x_api scene, yielding the base64 token
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
            "brand": "FORD",
            "encryptedVin": enc_vin,
            "xjw": xjw,
        }
        params["sign"] = compute_sign(params)
        async with self._session.get(
            BASE_URL + PATH_THIRD_PARTY_TOKEN, headers=self._headers(), params=params
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
                )
            self._log.debug("FordPass third-party-token -> %s %s", resp.status, text[:300])
            if resp.status >= 400:
                raise FordPassApiError(resp.status, text[:300])
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            raise FordPassApiError(resp.status, f"non-JSON token response: {text[:200]}")
        inner = data.get("data", {})
        enc_token = inner.get("encryptedAccessToken")
        if not enc_token:
            raise FordPassApiError(data.get("code"), f"no token in response: {text[:200]}")
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
          * vin      = whitebox_AES("lbs_p2c" scene, plaintext VIN), IV random
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
        self._log.debug("FordPass LBS POST %s headers=%s body=%s",
                        PATH_QUERY_LOCATION,
                        {k: v for k, v in headers.items() if k != "x-sign"},
                        json.dumps(body)[:300])
        async with self._session.post(
            LBS_BASE_URL + PATH_QUERY_LOCATION, headers=headers, json=body
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
                )
            self._log.debug("FordPass LBS -> %s %s", resp.status, text[:500])
            if resp.status >= 400:
                raise FordPassApiError(resp.status, text[:300])
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            raise FordPassApiError(resp.status, f"non-JSON LBS response: {text[:200]}")
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
