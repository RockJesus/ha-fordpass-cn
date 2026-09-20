"""Constants for the FordPass integration."""
from __future__ import annotations

from homeassistant.const import Platform

# Domain
DOMAIN = "fordpass"
PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.LOCK]

# Version
VERSION = "1.0.1"

# API Configuration
DEFAULT_BASE_URL = "https://api-connect.ford.com.cn"

# API Endpoints
API_LOGIN = "/v1/device/login"
API_LOGOUT = "/v1/user/logout"
API_USER_INFO = "/v1/user/info"
API_VEHICLE_LIST = "/v1/vehicle/list"
API_VEHICLE_STATUS = "/v1/vehicle/status"
API_LOCK = "/v1/vehicle/lock"
API_UNLOCK = "/v1/vehicle/unlock"
