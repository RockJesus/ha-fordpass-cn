"""Constants for the FordPass China integration."""
from __future__ import annotations

DOMAIN = "fordpass_cn"

# Production gateways (verified from official app captures)
BASE_URL = "https://cn.api.mps.ford.com.cn"
LBS_BASE_URL = "https://api-connect.ford.com.cn"

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
# Vehicle health alerts (明文中文告警) — verified from the official app
# capture (2026-09-29): this endpoint REQUIRES the standard request signature.
#   GET /api/cnxapi-cds/v1/vha/activealert?hmiPreferredLanguage=zh-cn&
#       preferredLanguage=zh-cn&source=TCU&encryptedVin={wb}&xjw={iv}&
#       timestamp={ms}&sign={sha256}
# (server 404s a bare GET without timestamp/sign — fixed in v2.7.4)
PATH_ACTIVE_ALERT = "/api/cnxapi-cds/v1/vha/activealert"

# Username/password (B2C) login — recovered from the official app capture
# (2026-09-29). Azure AD B2C four-step flow:
#   1. GET  authorize            -> HTML form + cookies (x-ms-cpim-csrf) + x-request-id
#   2. POST SelfAsserted         -> {"status":"200"} on success
#   3. GET  CombinedSigninAndSignup/confirmed -> 302, Location carries ?code=<JWT>
#   4. POST dlt-token-by-b2c-auth-code       -> same access/refresh JWTs as SMS
# The authorization code is exchanged for the same DLT tokens as passcode
# login; the code itself is white-box encrypted (x_api scene).
B2C_AUTHORITY = "https://login.ford.com.cn"
B2C_PATH_AUTHORIZE = (
    "/CNB2CFORD.partner.onmschina.cn/B2C_1A_SIGNINSIGNUP_ZH-CN/oauth2/v2.0/authorize"
)
B2C_PATH_SELF_ASSERTED = (
    "/CNB2CFORD.partner.onmschina.cn/B2C_1A_SignInSignUp_zh-CN/SelfAsserted"
)
B2C_PATH_CONFIRMED = (
    "/CNB2CFORD.partner.onmschina.cn/B2C_1A_SignInSignUp_zh-CN/"
    "api/CombinedSigninAndSignup/confirmed"
)
B2C_POLICY = "B2C_1A_SignInSignUp_zh-CN"
B2C_CLIENT_ID = "72af5fa7-d101-425d-ba47-c392e3970399"
B2C_REDIRECT_URI = "https://www.ford.com.cn/support/category/fordpass"
PATH_B2C_TOKEN = "/api/cnxapi-token-exchange/v1/app/dlt-token-by-b2c-auth-code"

# Remote command values (v2.7.7 — aligned to the server whitelist).
# The gateway rejects unknown commandType with HTTP 400
# "commandType should be in (...)" (errorCode 100502).  The authoritative
# whitelist returned by the server on 2026-09-29:
#   ForceRefresh, TrailerLightCheckStart, TrailerLightCheckStop, TrunkUnlock,
#   DoorLock, DoorUnlock, RemoteStart, RemoteStop, InitialVA, CancelVA,
#   CentralZoneLightingON, ZoneLightingON, ZoneLightingOFF,
#   OTAActivationSchedule, ASUSetting, AutoRefresh
# v2.7.6 used "Lock/Unlock/EngineStart/EngineStop/Honk/Panic" which are NOT in
# the whitelist, so lock/unlock/engine buttons always failed with 100502.
CMD_LOCK = "DoorLock"
CMD_UNLOCK = "DoorUnlock"
CMD_ENGINE_START = "RemoteStart"
CMD_ENGINE_STOP = "RemoteStop"
# NOTE: the China gateway has no Honk/Panic command (removed in v2.7.7 — the
# old 鸣笛寻车/报警 buttons always returned 400 errorCode 100502).
CMD_EXTEND_START = "TrunkUnlock"  # 后备箱解锁 (whitelisted, reserved)
CMD_REFRESH_STATUS = "ForceRefresh"
CMD_AUTO_REFRESH = "AutoRefresh"

# Config / storage keys
CONF_PHONE = "phone"
CONF_TOKENS = "tokens"
CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_VEHICLE_INDEX = "vehicle_index"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_LOGIN_MODE = "login_mode"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"

# Login methods (v2.7.4 — both selectable in the config flow)
LOGIN_MODE_SMS = "sms"
LOGIN_MODE_PASSWORD = "password"

# Defaults
DEFAULT_SCAN_INTERVAL_SECONDS = 60
DEFAULT_CONF_FLOW_TITLE = "福特派互联"

# Platforms
PLATFORMS = ["lock", "switch", "button", "sensor", "device_tracker"]
