"""Constants for the FordPass China integration."""
from __future__ import annotations

DOMAIN = "fordpass_cn"

# Production gateways (verified from official app captures)
BASE_URL = "https://cn.api.mps.ford.com.cn"
LBS_BASE_URL = "https://api-connect.ford.com.cn"
# In-car connectivity / media-data H5 backend (流量管理 / 影音娱乐), verified
# from the official app's WebView capture:
#   POST /ford-phase3/h5/login/{vin_token}/1          -> abilityUserId/customerId/shopCode
#   POST /ford-phase3/h5/traffic/manage/{vin_token}/2 -> traffic quotas
# The {vin_token} (64-hex, encryType=1) is what the app puts in the FlowMgt
# page URL; the integration exposes it as an optional config option.
VENUS_BASE_URL = "https://ford.venusplatform.com/ford-phase3"
X_V_KEY = "be13d03807d84c3ea1733a92edaa5923"  # fixed X-V-Key header (in H5 JS)

# LBS (vehicle location) gateway — recovered from the app's Dart AOT code and
# verified against 26 live captures (x-sign 26/26).
#   x-sign = base64(SHA256(SK + sorted-body-params + ts + PKL + SK[:5] + ts[8:])).upper()
LBS_APP_ID = "632b3c058abe42f8b82889a935296911"   # ocp-application-id
LBS_APP_KEY = "211c2122-d829-4f5e-ad36-95d16fd0bf16"  # x-appkey == signature_lbs_secretKey
LBS_PAYLOAD_KEY = "3e432474-6f0e-4c48-ab48-d4858ccb8df2"  # signature_lbs_payLoadKey

# Fixed headers (observed from official app)
APPLICATION_ID = "46409D04-BD1B-40C6-9D51-13A52666E9F9"
APP_VERSION = "6.14.0"
CLIENT_TYPE = "FP"
OS_TYPE = "android"
OS_VERSION = "12"

# sign secrets (recovered from the app binary, verified against live captures)
# secretKey2 rotates every day:
#   secretKey2 = URLEncode(SHA256(SHA256(BeijingDate "yyyy-MM-dd") + PAYLOAD_KEY))
# (the old fixed constant 1607f4c12d... was the 2026-09-25 derived value and is
#  rejected by the server since 2026-09-26; compute_sign() derives it at runtime)
SECRET_KEY = "as24#02_"
PAYLOAD_KEY = "B_sd*ow7"

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
# Vehicle health alerts (明文中文告警, auth-token only, no sign) — verified
# from the official app capture:
#   GET /api/cnxapi-cds/v1/vha/activealert?hmiPreferredLanguage=zh-cn&
#       preferredLanguage=zh-cn&source=TCU&encryptedVin={wb}&xjw={iv}
PATH_ACTIVE_ALERT = "/api/cnxapi-cds/v1/vha/activealert"
# Media-data H5 backend (venusplatform)
PATH_TRAFFIC_LOGIN = "/h5/login/{token}/1"
PATH_TRAFFIC_MANAGE = "/h5/traffic/manage/{token}/2"

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
CONF_TRAFFIC_TOKEN = "traffic_token"  # optional 64-hex FlowMgt VIN token

# Defaults
DEFAULT_SCAN_INTERVAL_SECONDS = 300
DEFAULT_CONF_FLOW_TITLE = "福特派互联"

# Platforms
PLATFORMS = ["lock", "switch", "button", "sensor", "device_tracker"]
