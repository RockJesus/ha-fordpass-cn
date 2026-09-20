"""Constants for the FordPass integration."""
from __future__ import annotations

from homeassistant.const import Platform

# Domain
DOMAIN = "fordpass"
PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.LOCK, Platform.SWITCH]

# Version
VERSION = "2.0.0"

# API Configuration
DEFAULT_BASE_URL = "https://api-connect.ford.com.cn/lbsp2c-app"

# API Endpoints
API_LOGIN = "/v1/device/login"
API_LOGOUT = "/v1/user/logout"
API_USER_INFO = "/v1/user/info"
API_VEHICLE_LIST = "/v1/vehicles/search"
API_VEHICLE_STATUS = "/v1/vehicle"
API_VEHICLE_COMMAND = "/v1/vehicles/command"
API_VEHICLE_SEND = "/v1/vehicles/send"

# Vehicle Commands
COMMAND_LOCK = "lock"
COMMAND_UNLOCK = "unlock"
COMMAND_START = "start"
COMMAND_STOP = "stop"
COMMAND_HORN = "horn"
COMMAND_LIGHTS = "lights"
