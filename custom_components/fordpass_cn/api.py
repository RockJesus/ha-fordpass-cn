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

import hashlib
import json
import time
import uuid
from typing import Any

import aiohttp

from .const import (
    APP_VERSION,
    APPLICATION_ID,
    BASE_URL,
    CLIENT_TYPE,
    LBS_BASE_URL,
    OS_TYPE,
    OS_VERSION,
    PATH_COMMAND_STATUS,
    PATH_GENERATE_PASSCODE,
    PATH_PASSCODE_LOGIN,
    PATH_QUERY_LOCATION,
    PATH_REFRESH_TOKEN,
    PATH_REVOKE_TOKEN,
    PATH_SEND_COMMAND,
    PATH_VEHICLES_LIST,
    PATH_VEHICLE_STATUS,
    SECRET_KEY,
    SECRET_KEY2,
    TOUCH_POINT,
)
from .wbsk import FordPassCrypto


def _canonical(params: dict[str, Any]) -> str:
    items = sorted((str(k), str(params[k])) for k in params if k != "sign")
    return "&".join(f"{k}={v}" for k, v in items)


def compute_sign(params: dict[str, Any]) -> str:
    """Re-produce the app's request signature.

    sign = SHA256("secretKey2=<sk2>&" + sorted(k=v) + "&secretKey=<sk1>")
    """
    biz = _canonical(params)
    raw = f"secretKey2={SECRET_KEY2}&{biz}&secretKey={SECRET_KEY}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _new_traceid() -> str:
    return f"FPC{uuid.uuid4()}{int(time.time() * 1000)}"


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

    @property
    def crypto(self) -> FordPassCrypto:
        return FordPassCrypto.get()

    def set_token(self, token: str | None) -> None:
        self._access_token = token

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
    async def generate_passcode(self, phone: str) -> None:
        enc, xjw = self.crypto.encrypt_field(phone)
        await self._request(
            "POST",
            PATH_GENERATE_PASSCODE,
            {"encryptedPhoneNumber": enc, "touchPoint": TOUCH_POINT, "xjw": xjw},
        )

    async def passcode_login(self, phone: str, passcode: str) -> dict[str, str]:
        """Exchange SMS passcode for access/refresh JWTs (verified live)."""
        enc_phone, xjw = self.crypto.encrypt_field(phone)
        enc_code, _ = self.crypto.encrypt_field(passcode)
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
            "access_token": self.crypto.decrypt_field(
                inner["encryptedAccessToken"], xjw2
            ),
            "refresh_token": self.crypto.decrypt_field(
                inner["encryptedRefreshToken"], xjw2
            ),
        }

    async def refresh_token(self, refresh_token: str) -> dict[str, str]:
        enc, xjw = self.crypto.encrypt_field(refresh_token)
        data = await self._request(
            "POST",
            PATH_REFRESH_TOKEN,
            {"encryptedRefreshToken": enc, "brand": "FORD", "xjw": xjw},
        )
        inner = data.get("data", {})
        xjw2 = inner.get("xjw", xjw)
        return {
            "access_token": self.crypto.decrypt_field(
                inner["encryptedAccessToken"], xjw2
            ),
            "expires_in": inner.get("expiresIn"),
        }

    async def revoke_token(self, refresh_token: str) -> None:
        enc, xjw = self.crypto.encrypt_field(refresh_token)
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
                        v[key] = self.crypto.decrypt_field(v[key], xjw)
            vehicles.append(v)
        return vehicles

    async def get_vehicle_status(self, vin: str) -> dict[str, Any]:
        """GET /v1/vehicle-status, response body is encrypted (verified live)."""
        enc_vin, xjw = self.crypto.encrypt_field(vin)
        data = await self._request(
            "GET",
            PATH_VEHICLE_STATUS,
            query={"encryptedVin": enc_vin, "xjw": xjw},
        )
        inner = data.get("data", {})
        if inner.get("encryptedResponseBody"):
            plain = self.crypto.decrypt_field(inner["encryptedResponseBody"], inner["xjw"])
            return json.loads(plain)
        return inner

    # ------------------------------------------------------------- commands
    async def send_command(self, vin: str, command_type: str) -> dict[str, Any]:
        """POST /v1/vehicles/send-command (verified live for Auto/ForceRefresh)."""
        enc_vin, xjw = self.crypto.encrypt_field(vin)
        data = await self._request(
            "POST",
            PATH_SEND_COMMAND,
            {"commandType": command_type, "encryptedVin": enc_vin, "xjw": xjw},
        )
        inner = data.get("data", {})
        if inner.get("encryptedResponseBody"):
            plain = self.crypto.decrypt_field(inner["encryptedResponseBody"], inner["xjw"])
            return json.loads(plain)
        return inner

    async def command_status(
        self, vin: str, command_id: str, command_type: str
    ) -> dict[str, Any]:
        enc_vin, xjw = self.crypto.encrypt_field(vin)
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
            plain = self.crypto.decrypt_field(inner["encryptedResponseBody"], inner["xjw"])
            return json.loads(plain)
        return inner

    # ------------------------------------------------------------ location
    async def get_location(self, vin: str) -> dict[str, Any]:
        """POST https://api-connect.ford.com.cn/lbs-map/.../queryLocation.

        Request body carries an opaque `token` (base64 of VIN|uuid) plus an
        AES-CBC encrypted VIN; lat/lon/address come back encrypted with `iv`.
        """
        enc_vin, iv = self.crypto.encrypt_field(vin)
        token_b64 = self.crypto.encrypt_field(f"{vin}|{uuid.uuid4()}")[0]
        data = await self._request(
            "POST",
            PATH_QUERY_LOCATION,
            body={"token": token_b64, "vin": enc_vin, "iv": iv, "source": 0, "secretVersion": "1.0"},
            base=LBS_BASE_URL,
            raw=True,
        )
        out = data.get("data", data)
        if out.get("lat"):
            try:
                return {
                    "lat": self.crypto.decrypt_field(out["lat"], out["iv"]),
                    "lon": self.crypto.decrypt_field(out["lon"], out["iv"]),
                    "address": self.crypto.decrypt_field(out["address"], out["iv"]),
                    "uploadTime": out.get("uploadTime"),
                }
            except Exception:  # noqa: BLE001 - keep raw on parse failure
                self._log.warning("Location decryption failed, returning raw payload")
        return out
