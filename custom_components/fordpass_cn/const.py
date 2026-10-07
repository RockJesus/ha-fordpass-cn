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

# 鸣笛寻车设置（v3.1.4）——「保存上传到车机」真云端通道：
# 账户云端持久化 = UserPreferenceV2（2026-10-02 逆向 libapp.so 还原，实机验证）：
#   POST /api/cnxapi-pds/v1/user/preference-by-groups
#   body: {preferenceGroups: [{groupName: "VehicleAnnouncementSetting",
#          userPreferences: [{preferenceType: "vehicleAnnouncementSoundType",
#                             preferenceValue: "<chrip1|chirp2|chirpHonk|honk|panic>"},
#                            {preferenceType: "vehicleAnnouncementDuration",
#                             preferenceValue: "<5-20>"}]}], timestamp, sign}
#   GET  /api/cnxapi-pds/v1/user/preference-list?timestamp=&sign=（查询已存设置）
# 签名 = 集成 compute_sign（R3 双重），嵌套 list/dict 按 App 序列化
# （list → "[a&b]"、dict → "{k=v&...}"、顶层平面 k=v&...）；已 200 保存成功
# 并回读确认（chirp2 + 10 秒写入福特云端）。车机生效通道（POST v5 honk 参数
# 直达车机）保持不变——鸣笛时仍把设置参数传给车机。
PATH_CRCC_PROFILE = "/api/cnxapi-cds/crcc/v1/profile-by-vin"  # 旧 RCC 通道（已弃用，保留历史）
PATH_USER_PREF_GROUPS = "/api/cnxapi-pds/v1/user/preference-by-groups"
PATH_USER_PREF_LIST = "/api/cnxapi-pds/v1/user/preference-list"
GROUP_VEHICLE_ANNOUNCEMENT = "VehicleAnnouncementSetting"
# v3.2.2: 字段名对齐 App 官方协议（HAR 实证 2026-10-03）——App 保存/读取
# 用 AnnouncementType + Duration（值=枚举数字字符串）；旧槽位
# vehicleAnnouncementSoundType（枚举名）/vehicleAnnouncementDuration 仅历史
# 残留，App 新逻辑不读取——此前集成写旧槽位导致「保存无效」根因。
PREF_SOUND_TYPE = "AnnouncementType"
PREF_DURATION = "Duration"
# 持续时长选项（App 设置页滑块 5-20 秒，默认 10 秒）
HONK_DURATION_OPTIONS = [5, 10, 15, 20]
DEFAULT_HONK_DURATION = 10
# 鸣笛类型选项（App 设置页 5 种，按强度从弱到强）
CHIRP_TYPE_OPTIONS = ["雨落荷叶", "急浪拍岸", "汽笛长鸣", "空谷回音", "声光共舞"]
DEFAULT_CHIRP_TYPE = "汽笛长鸣"  # App 设置页默认选中项
# 中文类型 → VehicleAnnouncementType（App 反编译枚举：chrip1=0/chirp2=1/
# chirpHonk=2/honk=3/panic=4）；「保存鸣笛设置」上传云端的 AnnouncementType
# 值为枚举数字字符串（HAR 实证：{"preferenceType":"AnnouncementType",
# "preferenceValue":"2"}）
CHIRP_TO_ANNOUNCE = {
    "雨落荷叶": "0",
    "急浪拍岸": "1",
    "汽笛长鸣": "2",
    "空谷回音": "3",
    "声光共舞": "4",
}
# 旧槽位 vehicleAnnouncementSoundType 历史值（枚举名）→ 中文（兼容回读）
ANNOUNCE_ENUM_CN = {
    "chrip1": "雨落荷叶",
    "chirp2": "急浪拍岸",
    "chirpHonk": "汽笛长鸣",
    "honk": "空谷回音",
    "panic": "声光共舞",
}
# 中文类型 → v5 honk 请求的 ChirpType 数字（0-4，与 App 枚举一致，v3.1.8）：
# 0=chrip1 / 1=chirp2 / 2=chirpHonk / 3=honk / 4=panic。
# 注意：4（声光共舞 panic）在部分车型走独立的 /panic/{duration} 端点
# （灯+喇叭警报），锐际实测 404（云端不支持）；其余 0-3 走 POST /honk。
CHIRP_TO_TYPE = {
    "雨落荷叶": 0,
    "急浪拍岸": 1,
    "汽笛长鸣": 2,
    "空谷回音": 3,
    "声光共舞": 4,
}
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
# 服务信息端点（v3.1.4，实测 2026-10-02）：R3 compute_sign + 修正 headers +
# 无 appKey（maintenance/recall 早期版本带 appKey 参与签名；headers 修正后
# 无 appKey 亦 200）。warranty 已过验签但服务端 100502（参数校验，待 App 抓包）。
PATH_MAINTENANCE_PLAN = "/api/cnxapi-cds/v1/maintenance-plan"
PATH_MAINTENANCE_HISTORY = "/api/cnxapi-cds/v1/maintenance-history"   # v3.3.7 探测
PATH_DEPARTURE_TIMES_RETRIEVE = "/api/cevs/v2/departuretimes/retrieve"  # v3.3.7 探测
PATH_RECALL = "/api/cnxapi-vds/v1/vehicles/recall"
PATH_SIM_INFO = "/api/cnxapi-cds/v1/vehicle/sim/info"
PATH_WIFI_STATUS = "/api/cnxapi-cds/v1/vehicle/wifi/status"
PATH_WARRANTY = "/api/cnxapi-cds/v1/warranty"
# 空调滤芯状态/重置（v3.1.7，2026-10-02 逆向还原 + 实机 200）：
#   GET  /api/cnxapi-vds/v1/aar/status → data.airFilter.{isHealthy,lastReplaceTime,
#         lastReplaceTimestamp}（AARStatusResponseDataAirFilter，6.16.0 App 新增）
#   PUT  /api/cnxapi-vds/v1/aar/status body={channel:"IVI", filterStatus:0, xjw,
#         encryptedVin} → data:"success"；重置后 lastReplaceTime 更新为当天。
#   内部链路：vds 网关转调 BESL /api/besl/maintenance/reminder/ivi/filter/v1/status。
PATH_AAR_STATUS = "/api/cnxapi-vds/v1/aar/status"
# v3.1.9: 云端能力+服务信息（200 实测，锐际）
PATH_CCFEATURES = "/api/cnxapi-vds/v2/vehicles/ccfeatures"
# v3.1.9: 预测性诊断（机油寿命/剩余里程/慢漏气胎，200 实测，锐际）
PATH_PROGNOSTIC = "/api/cnxapi-cds/prognostic/v1/list"
# v3.1.17: 未读消息摘要（HAR 实测 200）：GET /api/cnxapi-message/app/messages/summary
# ?timestamp=&sign=（无 encryptedVin 参数）→ data.summary.{allRedDotStatus,
# unReadCategoryId/unReadCategoryDescription/readMessageSubject} + data.categories[]
PATH_MESSAGES_SUMMARY = "/api/cnxapi-message/app/messages/summary"

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
# v3.1.9: 删除 CMD_EXTEND_START 旧别名（曾错误指向 TrunkUnlock；App 远程启动延长
# 的真实规范是 FORD_EXTEND_START，但锐际实测 send-command 网关白名单不含此项，
# 不支持——见 v3.1.9 实测限制）。
CMD_CENTRAL_LIGHTING = "CentralZoneLightingON"      # 中央区灯光开（无独立关闭命令）
CMD_AUTO_REFRESH = "AutoRefresh"                    # 自动刷新
# 鸣笛寻车（v3.0.5 起为 switch）：中国区 send-command 网关白名单不含 Honk（HTTP 400 100502），
# 走 v5 网关真实通道（DELETE /api/vehicles/v5/{vin}/honk，实测 200 + commandId）
CMD_HONK = "Honk"
HONK_AUTO_OFF_SECONDS = 30                          # 鸣笛寻车自动复位秒数（App 鸣笛约 30 秒自动停止）
# 声光共舞（panic）端点（v3.1.8）：App 的 FordPanicCommand 走
# POST /api/vehicles/v5/{vin}/panic/{paniconduration}（灯+喇叭警报）；
# 锐际实测 404（云端不支持，App 中该类型可能也只在部分车型/蓝牙下可用）。
PATH_V5_PANIC = "/api/vehicles/v5/{vin}/panic/{duration}"
# v3.1.13: 鸣笛命令状态查询（App FordRemoteControlApiService，v5 命令轮询组：
# statusrefresh/{commandId} / engine/start/{commandId} / doors/lock/{commandId} /
# announcestatus/{commandId} 并列——honk 返回 commandId 后查命令执行结果）
PATH_V5_ANNOUNCE_STATUS = "/api/vehicles/v5/{vin}/announcestatus/{command_id}/"
# v3.1.13: 车辆能力清单 v4（App VcsRepositoryProvider::fetchCapabilityV4，
# GET /api/cnxapi-vds/v4/vehicles/cvfeatures——App 首页能力卡片 PAAK/EV 管理/
# 哨兵/灯光等的权威来源；此前实测 404，可能缺完整 query，本次接入后实测）
PATH_CVFEATURES = "/api/cnxapi-vds/v4/vehicles/cvfeatures"
# v3.1.13: 停车影像 / 行车监控（App VehicleManagerEndpoint：searchVehicleParkingImage /
# searchVehicleMonitorTraffic，PDS 网关；请求体含 carId=车辆列表 encryptedCarId，
# 锐际 encryptedCarId=null = 车型无远程影像硬件——实体不创建）
PATH_PARKING_IMAGE = "/api/cnxapi-pds/v1/search-vehicle-parking-image"
PATH_MONITOR_TRAFFIC = "/api/cnxapi-pds/v1/search-vehicle-monitor-traffic"
CMD_TRAILER_CHECK_START = "TrailerLightCheckStart"  # 拖车灯光检测开始（皮卡/拖车）
CMD_TRAILER_CHECK_STOP = "TrailerLightCheckStop"    # 拖车灯光检测停止
CMD_VA_INIT = "InitialVA"                           # 鸣笛通告触发（FORD_HONK——v3.2.1 修正：非语音助手）
CMD_VA_CANCEL = "CancelVA"                          # 鸣笛通告取消（FORD_HONK_CANCEL）
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
