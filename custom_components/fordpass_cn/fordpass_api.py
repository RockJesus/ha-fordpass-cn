"""福特派中国区 API 客户端.

支持三种认证方式:
1. access_token 直接使用（推荐，抓包任意API请求的 auth-token 头）
2. refresh_token 自动刷新
3. B2C OAuth 授权码 + PKCE（备选）
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import time
from enum import Enum
from typing import Any, Optional
from urllib.parse import urlencode, parse_qs, urlparse

import aiohttp

_LOGGER = logging.getLogger(__name__)

SSO_URL = "https://sso.ci.ford.com.cn/"
CV_URL = "https://cnapi.cv.ford.com.cn/"
API_URL = "https://cn.api.mps.ford.com.cn/"

B2C_TENANT_ID = "4566605f-43a7-400a-946e-89cc9fdb0bd7"
B2C_POLICY = "B2C_1A_SignInSignUp_zh-CN"
B2C_CLIENT_ID = "09852200-05fd-41f6-8c21-d36d3497dc64"
B2C_SCOPE = "09852200-05fd-41f6-8c21-d36d3497dc64 openid"
B2C_REDIRECT_URI = "fordapp://userauthorized"
B2C_FORD_APP_ID = "5C80A6BB-CF0D-4A30-BDBF-FC804B5C1A98"

APPLICATION_ID = {
    "ford": "35F9024B-010E-4FE7-B202-62D941F8681C",
    "lincoln": "5EE5E683-1B71-4D6B-BAA8-F344D6672796",
}

DEFAULT_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh-Hans;q=0.9",
    "User-Agent": "fordpass-cn/320 CFNetwork/1331.0.7 Darwin/21.4.0",
    "Accept-Encoding": "gzip, deflate",
}

B2C_LOGIN_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1",
    "Accept-Language": "zh-CN,zh-Hans;q=0.9",
}


class CommandResult(str, Enum):
    SUCCESS = "success"
    PENDING = "pending"
    FAILED = "failed"


class FordPassAPI:
    def __init__(self, session, vehicle_type="ford", refresh_token=None, access_token=None):
        self._session = session
        self._vehicle_type = vehicle_type if vehicle_type in APPLICATION_ID else "ford"
        self._app_id = APPLICATION_ID[self._vehicle_type]
        self._token = access_token
        self._refresh_token = refresh_token
        self._expires = None
        self._code_verifier = None
        self._auth_mode = "unknown"

        if access_token:
            self._expires = time.time() + 7200
            self._auth_mode = "access_token"
        elif refresh_token:
            self._expires = time.time() - 100
            self._auth_mode = "refresh_token"

    def set_access_token(self, token, expires_in=7200):
        self._token = token
        self._expires = time.time() + expires_in - 60
        self._auth_mode = "access_token"

    @property
    def auth_mode(self):
        return self._auth_mode

    @property
    def access_token_value(self):
        return self._token

    @property
    def refresh_token_value(self):
        return self._refresh_token

    @staticmethod
    def _generate_code_verifier():
        return secrets.token_urlsafe(64)

    @staticmethod
    def _generate_code_challenge(verifier):
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    def generate_auth_url(self):
        self._code_verifier = self._generate_code_verifier()
        code_challenge = self._generate_code_challenge(self._code_verifier)
        params = {
            "redirect_uri": B2C_REDIRECT_URI, "response_type": "code", "max_age": "3600",
            "scope": B2C_SCOPE, "client_id": B2C_CLIENT_ID,
            "code_challenge": code_challenge, "code_challenge_method": "S256",
            "ui_locales": "zh-CN", "language_code": "zh-CN", "country_code": "CHN",
            "ford_application_id": B2C_FORD_APP_ID,
        }
        auth_url = f"{SSO_URL}{B2C_TENANT_ID}/{B2C_POLICY}/oauth2/v2.0/authorize?{urlencode(params)}"
        return auth_url, self._code_verifier

    @staticmethod
    def extract_code_from_redirect(redirect_url):
        if not redirect_url:
            return None
        redirect_url = redirect_url.strip()
        if "=" not in redirect_url and "://" not in redirect_url and len(redirect_url) > 20:
            return redirect_url
        parsed = urlparse(redirect_url)
        if parsed.query:
            qs = parse_qs(parsed.query)
            if "code" in qs:
                return qs["code"][0]
        if parsed.fragment:
            qs = parse_qs(parsed.fragment)
            if "code" in qs:
                return qs["code"][0]
        return None

    async def exchange_code_for_token(self, code, code_verifier=None):
        verifier = code_verifier or self._code_verifier
        if not verifier:
            return False
        token_url = f"{SSO_URL}{B2C_TENANT_ID}/{B2C_POLICY}/oauth2/v2.0/token"
        data = {"client_id": B2C_CLIENT_ID, "grant_type": "authorization_code",
                "code_verifier": verifier, "code": code, "redirect_uri": B2C_REDIRECT_URI}
        headers = {**B2C_LOGIN_HEADERS, "Content-Type": "application/x-www-form-urlencoded"}
        code_b2c, resp_b2c = await self._call_api(url=token_url, method="post", data=data, headers=headers)
        if code_b2c != 200 or not isinstance(resp_b2c, dict) or "access_token" not in resp_b2c:
            return False
        b2c_access_token = resp_b2c["access_token"]
        ford_token_url = f"{API_URL}api/token/v2/cat-with-b2c-access-token"
        ford_data = json.dumps({"idpToken": b2c_access_token})
        ford_headers = {**DEFAULT_HEADERS, "Content-Type": "application/json", "Application-Id": self._app_id}
        code_ford, resp_ford = await self._call_api(url=ford_token_url, method="post", data=ford_data, headers=ford_headers)
        if code_ford == 200 and isinstance(resp_ford, dict) and "access_token" in resp_ford:
            self._token = resp_ford["access_token"]
            self._refresh_token = resp_ford.get("refresh_token", self._refresh_token)
            expires_in = resp_ford.get("expires_in", 3600)
            self._expires = time.time() + expires_in - 60
            self._auth_mode = "oauth"
            self._code_verifier = None
            return True
        return False

    async def refresh_token(self):
        if not self._refresh_token:
            return False
        self._token = None
        self._expires = None
        data = json.dumps({"refresh_token": self._refresh_token})
        code, response = await self._call_api(
            url=f"{API_URL}api/token/v2/cat-with-refresh-token",
            method="post", data=data, headers=self._make_api_header(use_token=False))
        if code == 200 and isinstance(response, dict) and "access_token" in response:
            self._token = response["access_token"]
            self._refresh_token = response.get("refresh_token", self._refresh_token)
            expires_in = response.get("expires_in", 3600)
            self._expires = time.time() + expires_in - 60
            self._auth_mode = "refresh_token"
            return True
        return False

    def _make_api_header(self, use_token=True):
        header = {**DEFAULT_HEADERS, "Content-Type": "application/json", "Application-Id": self._app_id}
        if use_token and self._token:
            header["auth-token"] = self._token
        return header

    async def _call_api(self, url, method, **kwargs):
        try:
            async with self._session.request(method=method, url=url, timeout=aiohttp.ClientTimeout(total=30), **kwargs) as resp:
                code = resp.status
                raw = await resp.read()
                if not raw:
                    return code, {}
                try:
                    response = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    return code, {"raw": raw.decode("utf-8", errors="replace")}
                if code != 200:
                    _LOGGER.error("API %s 返回 %d: %s", url, code, response)
                return code, response
        except aiohttp.ClientError as err:
            return -1, {"error": str(err)}
        except asyncio.TimeoutError:
            return -1, {"error": "timeout"}

    def clear_tokens(self):
        self._token = None
        self._refresh_token = None
        self._expires = None
        self._code_verifier = None

    async def _check_token(self):
        if self._token and self._expires and time.time() < self._expires:
            return True
        if self._refresh_token:
            return await self.refresh_token()
        if self._auth_mode == "access_token" and self._token:
            return True
        return False

    async def _safe_call_api(self, url, method="get", extra_headers=None):
        if not await self._check_token():
            return -1, {"error": "auth_failed"}
        headers = self._make_api_header()
        if extra_headers:
            headers.update(extra_headers)
        return await self._call_api(url=url, method=method, headers=headers)

    async def get_user_info(self):
        code, response = await self._safe_call_api(url=f"{CV_URL}api/users")
        if code == 200 and isinstance(response, dict):
            return response.get("profile")
        return None

    async def get_vehicles(self):
        code, response = await self._safe_call_api(url=f"{CV_URL}api/users/vehicles")
        if code == 200 and isinstance(response, dict):
            vehicles = response.get("vehicles", {})
            if isinstance(vehicles, dict):
                return vehicles.get("$values", [])
            if isinstance(vehicles, list):
                return vehicles
        return None

    async def get_vehicle_status(self, vin):
        code, response = await self._safe_call_api(url=f"{CV_URL}api/vehicles/v5/{vin}/status")
        if code == 200 and isinstance(response, dict):
            return response.get("vehiclestatus")
        return None

    async def _send_command(self, url, method):
        code, response = await self._safe_call_api(url=url, method=method)
        if code == 200 and isinstance(response, dict):
            return response.get("commandId")
        return None

    async def _check_command(self, url):
        code, response = await self._safe_call_api(url=url)
        if code == 200 and isinstance(response, dict):
            status = response.get("status")
            if status == 200:
                return CommandResult.SUCCESS
            if status == 552:
                return CommandResult.PENDING
        return CommandResult.FAILED

    async def set_switch(self, vin, end_point, turn_on):
        method = "put" if turn_on else "delete"
        return await self._send_command(url=f"{CV_URL}api/vehicles/v2/{vin}/{end_point}", method=method)

    async def get_switch_completed(self, vin, end_point, command_id):
        return await self._check_command(url=f"{CV_URL}api/vehicles/v2/{vin}/{end_point}/{command_id}")

    async def remote_start(self, vin, start=True):
        return await self.set_switch(vin, "engine/start", start)

    async def lock_doors(self, vin, lock=True):
        return await self.set_switch(vin, "doors/lock", lock)


import asyncio
