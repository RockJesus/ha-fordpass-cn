"""API client for FordPass."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp

from .const import (
    API_LOGIN,
    API_LOGOUT,
    API_USER_INFO,
    API_VEHICLE_LIST,
    API_VEHICLE_STATUS,
    DEFAULT_BASE_URL,
)

_LOGGER = logging.getLogger(__name__)


class FordPassAuthError(Exception):
    """Authentication error."""


class FordPassApiError(Exception):
    """API error."""


class FordPassClient:
    """API client for FordPass."""

    def __init__(
        self,
        username: str,
        password: str,
        base_url: str = DEFAULT_BASE_URL,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Initialize the API client."""
        self.username = username
        self.password = password
        self.base_url = base_url.rstrip("/")
        self._session = session or aiohttp.ClientSession()

        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._token_expires: int = 0

        self.user_id: str | None = None
        self.nickname: str | None = None
        self.vehicles: list[dict[str, Any]] = []

    @property
    def access_token(self) -> str | None:
        """Return access token."""
        return self._access_token

    def _base_headers(self, token: str | None = None) -> dict[str, str]:
        """Build base request headers."""
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "FordPass/6.14.0 (iPhone; iOS 17.0; Scale/3.00)",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        token: str | None = None,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Perform a request and return decoded JSON."""
        url = f"{self.base_url}{path}"
        headers = self._base_headers(token)

        _LOGGER.debug("Request: %s %s", method, url)
        if payload:
            _LOGGER.debug("Payload: %s", str(payload)[:200])

        async with self._session.request(
            method,
            url,
            headers=headers,
            json=payload if payload is not None else None,
            params=params,
            ssl=False,
        ) as resp:
            _LOGGER.debug("Response status: %s", resp.status)
            text = await resp.text()
            _LOGGER.debug("Response text: %s", text[:500])

            try:
                data = await resp.json(content_type=None)
            except Exception as err:
                raise FordPassApiError(
                    f"Invalid JSON response: {text[:200]}"
                ) from err

            if isinstance(data, dict):
                code = data.get("code")
                try:
                    code_int = int(code)
                except (TypeError, ValueError):
                    code_int = None

                if code_int not in (0, 200, None):
                    if resp.status == 401 or code_int in (401, 1001):
                        raise FordPassAuthError("令牌无效或已过期")
                    raise FordPassApiError(
                        f"API error: code {code}: {str(data.get('msg') or data.get('message'))[:200]}"
                    )

            if resp.status >= 400:
                raise FordPassApiError(f"HTTP {resp.status}: {text[:200]}")

            return data

    async def login(self) -> None:
        """Login with username and password."""
        payload = {
            "account": self.username,
            "password": self.password,
            "deviceId": "homeassistant",
            "clientType": "android",
        }

        _LOGGER.debug("Logging in...")
        data = await self._request("POST", API_LOGIN, payload=payload)

        result = data.get("data", data)
        self._access_token = result.get("accessToken") or result.get("access_token") or result.get("token")
        self._refresh_token = result.get("refreshToken") or result.get("refresh_token")

        if not self._access_token:
            raise FordPassAuthError("登录失败：未返回 accessToken")

        self._token_expires = int(time.time()) + 7 * 24 * 3600
        _LOGGER.info("Login successful")

        # Get user info
        try:
            await self.get_user_info()
        except Exception as err:
            _LOGGER.warning("Failed to get user info: %s", err)

    async def get_user_info(self) -> dict[str, Any]:
        """Get current user info."""
        data = await self._request("GET", API_USER_INFO, token=self._access_token)
        result = data.get("data", data)

        if isinstance(result, dict):
            self.user_id = str(result.get("id", ""))
            self.nickname = result.get("nickname", "")
            _LOGGER.info("User: %s (id: %s)", self.nickname, self.user_id)

        return result

    async def get_vehicle_list(self) -> list[dict[str, Any]]:
        """Get vehicle list."""
        data = await self._request("GET", API_VEHICLE_LIST, token=self._access_token)
        raw = data.get("data", data)
        if isinstance(raw, list):
            self.vehicles = raw
            return raw
        if isinstance(raw, dict):
            vehicles = raw.get("list") or raw.get("vehicles") or []
            if isinstance(vehicles, list):
                self.vehicles = vehicles
                return vehicles
        return []

    async def get_vehicle_status(self, vehicle_id: str) -> dict[str, Any]:
        """Get vehicle status."""
        data = await self._request(
            "GET",
            API_VEHICLE_STATUS,
            token=self._access_token,
            params={"vehicleId": vehicle_id},
        )
        return data.get("data", data)

    async def async_close(self) -> None:
        """Close the session."""
        await self._session.close()
