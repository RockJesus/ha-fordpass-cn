"""Constants for the FordPass China integration."""
from __future__ import annotations

DOMAIN = "fordpass_cn"

# Production gateways (verified from official app captures)
BASE_URL = "https://cn.api.mps.ford.com.cn"
LBS_BASE_URL = "https://api-connect.ford.com.cn"

# Fixed headers (observed from official app)
APPLICATION_ID = "46409D04-BD1B-40C6-9D51-13A52666E9F9"
APP_VERSION = "6.14.0"
CLIENT_TYPE = "FP"
OS_TYPE = "android"
OS_VERSION = "12"

# sign secrets (fixed constants, recovered from app memory)
SECRET_KEY = "as24#02_"
SECRET_KEY2 = "1607f4c12d200c8e7303e273aef64791eca3f10ab1c4abc529eb16d511b8c015"

TOUCH_POINT = "FORD_APP_LOGIN"

# API paths (all verified against live captures)
PATH_GENERATE_PASSCODE = "/api/cnxapi-user-validation/v1/app/sms/generate-passcode"
PATH_PASSCODE_LOGIN = "/api/cnxapi-token-exchange/v1/app/dlt-token-by-phone-passcode-login"
PATH_REFRESH_TOKEN = "/api/cnxapi-token-exchange/v1/app/refresh-dlt-token"
PATH_REVOKE_TOKEN = "/api/cnxapi-token-exchange/v1/app/revoke-dlt-token"
PATH_THIRD_PARTY_TOKEN = "/api/cnxapi-token-exchange/v1/app/third-party-token"
PATH_VEHICLES_LIST = "/api/cnxapi-vds/v5/vehicles/list"
PATH_VEHICLE_INFO = "/api/cnxapi-vds/v5/vehicles/info"
PATH_VEHICLE_STATUS = "/api/cnxapi-cvinfo/v1/vehicle-status"
PATH_SEND_COMMAND = "/api/cnxapi-cvinfo/v1/vehicles/send-command"
PATH_COMMAND_STATUS = "/api/cnxapi-cvinfo/v1/vehicles/command-execution-status"
PATH_QUERY_LOCATION = "/lbs-map/v2/public/app/queryLocation"

# Remote command values (camelCase style, consistent with Auto/ForceRefresh;
# FORD_* enum lives in the app; verify against a live command before trusting)
CMD_LOCK = "Lock"
CMD_UNLOCK = "Unlock"
CMD_ENGINE_START = "EngineStart"
CMD_ENGINE_STOP = "EngineStop"
CMD_EXTEND_START = "ExtendStart"
CMD_HONK = "Honk"
CMD_HONK_CANCEL = "HonkCancel"
CMD_PANIC = "Panic"
CMD_PANIC_CANCEL = "PanicCancel"
CMD_REFRESH_STATUS = "ForceRefresh"
CMD_AUTO_REFRESH = "AutoRefresh"

# Config / storage keys
CONF_PHONE = "phone"
CONF_TOKENS = "tokens"
CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_VEHICLE_INDEX = "vehicle_index"
CONF_SCAN_INTERVAL = "scan_interval"

# Defaults
DEFAULT_SCAN_INTERVAL_SECONDS = 300
DEFAULT_CONF_FLOW_TITLE = "福特派互联"

# Platforms
PLATFORMS = ["lock", "switch", "button", "sensor"]
