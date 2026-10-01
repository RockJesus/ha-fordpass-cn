"""Constants for the FordPass China integration."""
from __future__ import annotations

DOMAIN = "fordpass_cn"

# Production gateways (verified from official app captures)
BASE_URL = "https://cn.api.mps.ford.com.cn"
LBS_BASE_URL = "https://api-connect.ford.com.cn"
# v5 REST gateway — recovered from the app's Dart AOT code and verified live
# (2026-10-01): POST /api/vehicles/v5/{vin}/honk (body: ChirpOrHonkDuration/
# IntervalBetweenRequests/ChirpType) returns HTTP 200 + commandId; DELETE on the
# same path (the app's FordHonkCancelCommand) also returns 200 + commandId.
# Both channels verified against the live gateway with the standard DLT headers
# (no sign/timestamp envelope needed on this route).
V5_BASE_URL = "https://cnapi.cv.ford.com.cn"
PATH_V5_HONK = "/api/vehicles/v5/{vin}/honk"

# 鸣笛寻车设置（v3.0.8）——「保存上传到车机」双通道：
# 1) 车机生效通道（已验证）：POST v5 honk 的 body 参数（ChirpOrHonkDuration /
#    ChirpType）直接决定车机鸣笛时长与音效——每次鸣笛都把设置参数传给车机；
# 2) 账户云端持久化通道（尽力而为）：RCC Profile 保存端点，字段结构已通过
#    服务端 JSON 校验（{userPreferences: [{preferenceType, preferenceValue}],
#    encryptedVin, xjw} + timestamp/sign）；签名体系为 App 的 signatureR2
#    （独立 secretKey/payLoadKey，尚未还原），当前用集成已有 R3 签名尝试，
#    失败时回退为本地保存（下次鸣笛仍按设置生效）。
PATH_CRCC_PROFILE = "/api/cnxapi-cds/crcc/v1/profile-by-vin"
# 持续时长选项（App 设置页滑块 5-20 秒，默认 10 秒）
HONK_DURATION_OPTIONS = [5, 10, 15, 20]
DEFAULT_HONK_DURATION = 10
# 鸣笛类型选项（App 设置页 5 种，按强度从弱到强，ChirpType=索引+1；
# ChirpType=1 已对活网关实测有效，映射按 App 设置顺序推断）
CHIRP_TYPE_OPTIONS = ["雨落荷叶", "急浪拍岸", "汽笛长鸣", "空谷回音", "声光共舞"]
DEFAULT_CHIRP_TYPE = "汽笛长鸣"  # App 设置页默认选中项
# 保存到 config entry options 的键
CONF_HONK_DURATION = "honk_duration"
CONF_CHIRP_TYPE = "chirp_type"

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
# OTA 设置状态（v3.1.3，实测 2026-10-02）：GET /api/alert/v1/ota/setting-info
# 走标准 R3 签名（timestamp+sign+encryptedVin/xjw）返回 HTTP 200；同族的
# ota/detail、ota/versions 在锐际上被服务端拒绝（errorCode 206004
# "capabilityMmota is false"），故仅 setting-info 接入。
PATH_OTA_SETTING = "/api/alert/v1/ota/setting-info"

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
# v2.9.0: 后备箱解锁 / 灯光寻车（均在中国区网关白名单内，效果视车型支持）
CMD_TRUNK_UNLOCK = "TrunkUnlock"
CMD_LIGHT_FIND_ON = "ZoneLightingON"
CMD_LIGHT_FIND_OFF = "ZoneLightingOFF"
CMD_EXTEND_START = CMD_TRUNK_UNLOCK  # 兼容旧名（后备箱解锁）
# v2.10.0: 白名单全部命令补全（此前未接入的项）
CMD_CENTRAL_LIGHTING = "CentralZoneLightingON"      # 中央区灯光开（无独立关闭命令）
CMD_AUTO_REFRESH = "AutoRefresh"                    # 自动刷新
# 鸣笛寻车（v3.0.5 起为 switch）：中国区 send-command 网关白名单不含 Honk（HTTP 400 100502），
# 走 v5 网关真实通道（DELETE /api/vehicles/v5/{vin}/honk，实测 200 + commandId）
CMD_HONK = "Honk"
HONK_AUTO_OFF_SECONDS = 30                          # 鸣笛寻车自动复位秒数（App 鸣笛约 30 秒自动停止）
CMD_TRAILER_CHECK_START = "TrailerLightCheckStart"  # 拖车灯光检测开始（皮卡/拖车）
CMD_TRAILER_CHECK_STOP = "TrailerLightCheckStop"    # 拖车灯光检测停止
CMD_VA_INIT = "InitialVA"                           # 语音助手初始化
CMD_VA_CANCEL = "CancelVA"                          # 语音助手取消
CMD_OTA_SCHEDULE = "OTAActivationSchedule"          # OTA 激活排程
CMD_ASU_SETTING = "ASUSetting"                      # 辅助设置
CMD_REFRESH_STATUS = "ForceRefresh"

# Config / storage keys
CONF_PHONE = "phone"
CONF_TOKENS = "tokens"
CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_VEHICLE_INDEX = "vehicle_index"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_COORDINATE_SYSTEM = "coordinate_system"
CONF_LOGIN_MODE = "login_mode"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"

# 坐标体系（v2.7.9，实测修正）：福特 LBS 返回 GCJ-02（火星坐标，与官方 App
# 高德地图标记一致）；"wgs84" 选项做 GCJ-02→WGS-84 逆转换供官方地图/OSM 精确
# 显示；"gcj02" 选项原样输出供高德/腾讯地图。默认 WGS-84（官方地图精确）。
COORDINATE_WGS84 = "wgs84"
COORDINATE_GCJ02 = "gcj02"
DEFAULT_COORDINATE_SYSTEM = COORDINATE_WGS84

# Login methods (v2.7.4 — both selectable in the config flow)
LOGIN_MODE_SMS = "sms"
LOGIN_MODE_PASSWORD = "password"

# Defaults（v2.7.8：刷新间隔以分钟为单位，默认 30 分钟）
DEFAULT_SCAN_INTERVAL_MINUTES = 30
DEFAULT_CONF_FLOW_TITLE = "福特派互联"

# Platforms
PLATFORMS = ["lock", "switch", "button", "sensor", "device_tracker", "select"]
