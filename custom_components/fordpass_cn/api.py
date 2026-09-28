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
import time
import urllib.parse
import uuid
from typing import Any

import aiohttp

from .const import (
    APP_VERSION,
    APPLICATION_ID,
    BASE_URL,
    CLIENT_TYPE,
    LBS_APP_ID,
    LBS_APP_KEY,
    LBS_BASE_URL,
    LBS_PAYLOAD_KEY,
    OS_TYPE,
    OS_VERSION,
    PATH_ACTIVE_ALERT,
    PATH_COMMAND_STATUS,
    PATH_GENERATE_PASSCODE,
    PATH_PASSCODE_LOGIN,
    PATH_QUERY_LOCATION,
    PATH_REFRESH_TOKEN,
    PATH_REVOKE_TOKEN,
    PATH_SEND_COMMAND,
    PATH_THIRD_PARTY_TOKEN,
    PATH_VEHICLES_LIST,
    PATH_VEHICLE_STATUS,
    PAYLOAD_KEY,
    SECRET_KEY,
    TOUCH_POINT,
)
from .wbsk import FordPassCrypto


def _canonical(params: dict[str, Any]) -> str:
    items = sorted((str(k), str(params[k])) for k in params if k != "sign")
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
    ) -> Any:
        url = base + path
        if body is not None:
            signed: dict[str, Any] = dict(body)
            signed["timestamp"] = int(time.time() * 1000)
            signed["sign"] = compute_sign(signed)
            kwargs: dict[str, Any] = {"json": signed}
        else:
            params = dict(query or {})
            params["timestamp"] = int(time.time() * 1000)
            params["sign"] = compute_sign(params)
            kwargs = {"params": params}
        self._log.debug("FordPass %s %s payload=%s", method, url, kwargs)
        async with self._session.request(
            method, url, headers=self._headers(), **kwargs
        ) as resp:
            text = await resp.text()
            self._log.debug("FordPass %s -> %s %s", path, resp.status, text[:2000])
            if resp.status == 401 and self._refresh_token and not self._retrying:
                self._retrying = True
                try:
                    self._log.info("FordPass access token expired, refreshing once")
                    new = await self.refresh_token(self._refresh_token)
                    self._access_token = new["access_token"]
                    if self.on_token_refresh is not None:
                        self.on_token_refresh(new["access_token"])
                    return await self._request(method, path, body, query, base, raw)
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
        """GET /v1/vehicle-status, response body is encrypted (verified live)."""
        enc_vin, xjw = await asyncio.to_thread(lambda: self.crypto.encrypt_field(vin))
        data = await self._request(
            "GET",
            PATH_VEHICLE_STATUS,
            query={"encryptedVin": enc_vin, "xjw": xjw},
        )
        inner = data.get("data", {})
        if inner.get("encryptedResponseBody"):
            plain = await asyncio.to_thread(lambda: self.crypto.decrypt_field(inner["encryptedResponseBody"], inner["xjw"]))
            result = json.loads(plain)
            self._log.debug("FordPass vehicle-status raw: %s", json.dumps(result, ensure_ascii=False)[:6000])
            return result
        return inner

    # ------------------------------------------------------------- commands
    async def send_command(self, vin: str, command_type: str) -> dict[str, Any]:
        """POST /v1/vehicles/send-command (verified live for Auto/ForceRefresh)."""
        enc_vin, xjw = await asyncio.to_thread(lambda: self.crypto.encrypt_field(vin))
        data = await self._request(
            "POST",
            PATH_SEND_COMMAND,
            {"commandType": command_type, "encryptedVin": enc_vin, "xjw": xjw},
        )
        inner = data.get("data", {})
        if inner.get("encryptedResponseBody"):
            plain = await asyncio.to_thread(lambda: self.crypto.decrypt_field(inner["encryptedResponseBody"], inner["xjw"]))
            return json.loads(plain)
        return inner

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

        Verified from the official app capture: this endpoint takes the normal
        white-box encrypted VIN + random xjw, reuses the main auth-token header
        and needs NO sign.  Response ``data.VehicleAlertResponseList[].ActiveAlerts[]``
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
        headers = {
            "user-agent": "Dart (dart:io)",
            "appversion": "1.0.0",  # the vha service uses its own appversion
            "ostype": OS_TYPE,
            "clienttype": CLIENT_TYPE,
            "application-id": APPLICATION_ID,
            "content-type": "application/json; charset=utf-8",
            "x-dynatrace": "",
        }
        if self._access_token:
            headers["auth-token"] = self._access_token
        self._log.debug("FordPass GET %s params=%s", PATH_ACTIVE_ALERT, params)
        async with self._session.get(
            BASE_URL + PATH_ACTIVE_ALERT, headers=headers, params=params
        ) as resp:
            text = await resp.text()
            self._log.debug("FordPass activealert -> %s %s", resp.status, text[:400])
            if resp.status >= 400:
                raise FordPassApiError(resp.status, text[:300])
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            raise FordPassApiError(resp.status, f"non-JSON activealert: {text[:200]}")
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
            text = await resp.text()
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

    async def get_location(self, vin: str) -> dict[str, Any]:
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
            text = await resp.text()
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
            return {
                "lat": await asyncio.to_thread(lambda: resp_crypto.decrypt_field(inner["lat"], iv)),
                "lon": await asyncio.to_thread(lambda: resp_crypto.decrypt_field(inner["lon"], iv)),
                "address": await asyncio.to_thread(lambda: resp_crypto.decrypt_field(inner["address"], iv)),
                "uploadTime": inner.get("uploadTime"),
                "iv": iv,
            }
        except Exception:  # noqa: BLE001 - keep raw on parse failure
            self._log.warning("Location decryption failed, returning raw payload")
        return inner
