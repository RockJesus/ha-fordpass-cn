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
# 分车门锁定（IDUL，v4.7.1）——依据福特派 6.16.0 v5 命令族（Dart 层
# 接口分析）：DELETE /door/{doortype}/lock = sendIDULCommand 指定单门上锁，
# GET /door/lock/{commandId} = getIDULCommandState 轮询命令结果。
# doortype 枚举（福特 v5 惯例）：front-left / front-right / rear-left /
# rear-right；DELETE 下发返回 commandId（两段式：下发→轮询）。
PATH_V5_DOOR_LOCK = "/api/vehicles/v5/{vin}/door/{doortype}/lock"
PATH_V5_DOOR_LOCK_STATUS = "/api/vehicles/v5/{vin}/door/lock/{commandId}"
# v5 主动刷新车况（v4.7.2）：PUT /status = sendRefreshVehicleStatusRequest
# （Dart 层对齐；实测网关当前 500 FIG——与 IDUL 同因，v5 PUT/DELETE 命令
# 通道暂不可用，实体点击如实报错，网关恢复后自动可用）
PATH_V5_STATUS = "/api/vehicles/v5/{vin}/status"
PATH_V5_STATUSREFRESH = "/api/vehicles/v5/{vin}/statusrefresh/{commandId}"
# v5.8.2 未接入端点补全（2026-10-24 端点比对）：整门锁/引擎启停 v5 原生
# 通道（6.16.0 Dart 确认 PUT=执行/DELETE=取消语义化；载荷未实测，
# 仅实现 API 方法不建实体——v5.2.4 遥控命令纪律：未验证载荷不下发）
PATH_V5_DOORS_LOCK = "/api/vehicles/v5/{vin}/doors/lock"
PATH_V5_ENGINE_START = "/api/vehicles/v5/{vin}/engine/start/"

# 鸣笛寻车设置（v3.1.4）——「保存上传到车机」真云端通道：
# 账户云端持久化 = UserPreferenceV2（2026-10-02 实机验证）：
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
# v3.2.2: 字段名对齐 App 官方协议（实机样本实证 2026-10-03）——App 保存/读取
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
# 中文类型 → VehicleAnnouncementType（v3.9.6 用户实测校准：App 真实枚举
# 为 chrip1=0/chirp2=1/honk=2/chirpHonk=3/panic=4——HA 设「汽笛长鸣」App
# 显示「空谷回音」、设「空谷回音」App 显示「汽笛长鸣」，即 2↔3 写反，
# 其余 0/1/4 对应正确）；「保存鸣笛设置」上传云端的 AnnouncementType
# 值为枚举数字字符串（实机样本实证：{"preferenceType":"AnnouncementType",
# "preferenceValue":"2"}）
CHIRP_TO_ANNOUNCE = {
    "雨落荷叶": "0",
    "急浪拍岸": "1",
    "空谷回音": "2",
    "汽笛长鸣": "3",
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
# 中文类型 → v5 honk 请求的 ChirpType 数字（0-4，与 App 枚举一致，v3.1.8；
# v3.9.6 与 CHIRP_TO_ANNOUNCE 同步校准——同属 App 枚举体系，实测
# honk=2/chirpHonk=3）：0=chrip1 / 1=chirp2 / 2=honk / 3=chirpHonk / 4=panic。
# 注意：4（声光共舞 panic）在部分车型走独立的 /panic/{duration} 端点
# （灯+喇叭警报），实测返回 404（云端不支持）；其余 0-3 走 POST /honk。
CHIRP_TO_TYPE = {
    "雨落荷叶": 0,
    "急浪拍岸": 1,
    "空谷回音": 2,
    "汽笛长鸣": 3,
    "声光共舞": 4,
}
# 保存到 config entry options 的键
CONF_HONK_DURATION = "honk_duration"
CONF_CHIRP_TYPE = "chirp_type"
# v3.4.5: 远程空调目标温度（EV/插混 preconditioning 专属，全车型能力驱动）
# 创建条件=vehicle-status preCondStatusDsply 有值（纯油车型无 -> 不创建）。
# 云端偏好通道候选（与鸣笛设置同构）：组名 RemoteClimateSetting +
# preferenceType TargetTemp，按 App 命名风格推断（未实测），待电马/插混实机样本校准。
GROUP_REMOTE_CLIMATE = "RemoteClimateSetting"
PREF_TARGET_TEMP = "TargetTemp"
CONF_REMOTE_TEMP = "remote_target_temp"
# 目标温度选项（摄氏度，候选范围 16-30，默认 24，与 App Auto=22 相近）
REMOTE_CLIMATE_TEMP_OPTIONS = list(range(16, 31))
DEFAULT_REMOTE_TEMP = 24


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
# v4.3.4: 车辆主动告警列表（6.16.0 228 端点全量比对后唯一未接入的
# 只读业务端点）——/api/v1/activealertlist 为 App 首页告警聚合（与 vha/
# activealert 并列的旧版独立端点，结构化为告警列表）。只读 GET 探测，
# 探测到数据才创建「车辆告警列表」传感器（空列表显示「无告警」）。
PATH_ACTIVE_ALERT_LIST = "/api/v1/activealertlist"

# 林肯之道专属端点（5.8.0 实测，2026-10-15）：
# 与福特派共用 CNESL/CNXAPI 网关，林肯专属命名空间：cnesl-lincoln-club
# （林肯俱乐部会员）与 cnxapi-lwosb（取送车/代驾服务总线）。端点↔动词配对
# 可解析部分为 0%（动态调用受阻），按只读 GET 探测接入——探测到数据才创建、
# 404/业务拒绝落盘不重探（与既有账号级探测一致）。
PATH_LINCOLN_MEMBER_INFO = "/api/cnesl-lincoln-club/v1/member-info"
PATH_LINCOLN_MY_PAGE = "/api/cnesl-lincoln-club/v1/my-page"
PATH_LWOSB_BOOKINGS = "/api/cnxapi-lwosb/v2/app/bookings"
PATH_LWOSB_ORDER_STATUS = "/api/cnxapi-lwosb/v2/app/bookingorderstatus"

# OTA 设置状态（v3.1.3，实测 2026-10-02）：GET /api/alert/v1/ota/setting-info
# 走标准 R3 签名（timestamp+sign+encryptedVin/xjw）返回 HTTP 200；同族的
# ota/detail、ota/versions 在实测车型上被服务端拒绝（errorCode 206004
# "capabilityMmota is false"），故仅 setting-info 接入。
PATH_OTA_SETTING = "/api/alert/v1/ota/setting-info"
# 服务信息端点（v3.1.4，实测 2026-10-02）：R3 compute_sign + 修正 headers +
# 无 appKey（maintenance/recall 早期版本带 appKey 参与签名；headers 修正后
# 无 appKey 亦 200）。warranty 已过验签但服务端 100502（参数校验，待 App 实测）。
PATH_MAINTENANCE_PLAN = "/api/cnxapi-cds/v1/maintenance-plan"
PATH_MAINTENANCE_HISTORY = "/api/cnxapi-cds/v1/maintenance-history"   # v3.3.7 探测
PATH_DEPARTURE_TIMES_RETRIEVE = "/api/cevs/v2/departuretimes/retrieve"  # v3.3.7 探测
PATH_RECALL = "/api/cnxapi-vds/v1/vehicles/recall"
PATH_SIM_INFO = "/api/cnxapi-cds/v1/vehicle/sim/info"
PATH_WIFI_STATUS = "/api/cnxapi-cds/v1/vehicle/wifi/status"
PATH_WARRANTY = "/api/cnxapi-cds/v1/warranty"
# 空调滤芯状态/重置（v3.1.7，2026-10-02 实测确认 + 实机 200）：
#   GET  /api/cnxapi-vds/v1/aar/status → data.airFilter.{isHealthy,lastReplaceTime,
#         lastReplaceTimestamp}（AARStatusResponseDataAirFilter，6.16.0 App 新增）
#   PUT  /api/cnxapi-vds/v1/aar/status body={channel:"IVI", filterStatus:0, xjw,
#         encryptedVin} → data:"success"；重置后 lastReplaceTime 更新为当天。
#   内部链路：vds 网关转调 BESL /api/besl/maintenance/reminder/ivi/filter/v1/status。
PATH_AAR_STATUS = "/api/cnxapi-vds/v1/aar/status"
# v3.1.9: 云端能力+服务信息（200 实测）
PATH_CCFEATURES = "/api/cnxapi-vds/v2/vehicles/ccfeatures"
# v3.1.9: 预测性诊断（机油寿命/剩余里程/慢漏气胎，200 实测）
PATH_PROGNOSTIC = "/api/cnxapi-cds/prognostic/v1/list"
# v3.1.17: 未读消息摘要（实机样本实测 200）：GET /api/cnxapi-message/app/messages/summary
# ?timestamp=&sign=（无 encryptedVin 参数）→ data.summary.{allRedDotStatus,
# unReadCategoryId/unReadCategoryDescription/readMessageSubject} + data.categories[]
PATH_MESSAGES_SUMMARY = "/api/cnxapi-message/app/messages/summary"

# 家充桩管理（smartwallbox 智能壁挂充电桩，6.16.0 接口字符串整理
# 2026-10-08）：福特派 App「家充桩管理」对应云端 smartwallbox 服务（能力位
# 0x8 privateChargingService / 0xA WallBoxAutoAuth 车型），端点均为
# /api/smartwallbox/processor/... 家族：
#   查询（只读/幂等，探测接入）：
#     GET /api/smartwallbox/processor/binding/v5            绑定桩列表（含默认桩）
#     GET /api/smartwallbox/processor/charging/status/wallboxId/v2
#                                                          按桩查充电状态（wallboxId 路径占位）
#     GET /api/smartwallbox/processor/charging/records/single/list/v2r  最近充电记录
#     GET /api/smartwallbox/processor/config/common/query/v2            通用配置查询
#   写操作（需实机样本确认 body 后接入，绝不自动调用）：
#     /api/smartwallbox/processor/binding/updateDefaultWallbox/v2 设置默认桩
#     /api/smartwallbox/processor/charging/start/v2              开始充电
#     /api/smartwallbox/processor/sharing/share/v3 / unshare/v2  桩共享
PATH_SMARTWALLBOX_BINDING = "/api/smartwallbox/processor/binding/v5"
PATH_SMARTWALLBOX_CHG_STATUS = "/api/smartwallbox/processor/charging/status/wallboxId/v2"
PATH_SMARTWALLBOX_RECORDS = "/api/smartwallbox/processor/charging/records/single/list/v2r"
PATH_SMARTWALLBOX_CONFIG = "/api/smartwallbox/processor/config/common/query/v2"
# v3.7.2: 家充桩控制端点（6.16.0 接口实测）——仅探测到家充桩绑定的车型创建
#   charging/start/v2        开始充电（POST，body 需 wallboxId）
#   charging/stop/v2         停止充电
#   binding/unbind/v2        解绑充电桩
#   authority/query/v3       桩授权查询（只读）
PATH_SMARTWALLBOX_CHARGE_START = "/api/smartwallbox/processor/charging/start/v2"
PATH_SMARTWALLBOX_CHARGE_STOP = "/api/smartwallbox/processor/charging/stop/v2"
PATH_SMARTWALLBOX_UNBIND = "/api/smartwallbox/processor/binding/unbind/v2"
PATH_SMARTWALLBOX_AUTHORITY = "/api/smartwallbox/processor/authority/query/v3"
# v3.7.3: 家充桩分享/桩级状态/充电订单（6.16.0 接口实测）——仅家充桩车型
#   探测到绑定桩后才尝试带 wallboxId 的 POST 探测，无绑定车型不创建：
#   sharing/query/v3                 桩分享列表（只读）
#   charging/status/wallbox          桩级充电状态（wallboxId 路径占位）
#   charging/wallbox                 桩充电信息
#   charging-center/v2/wallbox/orders  家充桩充电订单
PATH_SMARTWALLBOX_SHARING_QUERY = "/api/smartwallbox/processor/sharing/query/v3"
PATH_SMARTWALLBOX_CHG_STATUS_WB = "/api/smartwallbox/processor/charging/status/wallbox"
PATH_SMARTWALLBOX_CHG_WB = "/api/smartwallbox/processor/charging/wallbox"
PATH_SMARTWALLBOX_ORDERS = "/api/charging-center/v2/wallbox/orders"
# v5.8.2 未接入端点补全（2026-10-24 端点比对）——家充桩分享（6.16.0 实测
# 端点：share 分享家人/unshare 取消/sharerInfo 分享人信息/signTnc 签署/
# tnc content+sign 条款；写入类 body 结构从 sharing/query 探测返回回填）
PATH_WALLBOX_SHARE = "/api/smartwallbox/processor/sharing/share/v3"
PATH_WALLBOX_UNSHARE = "/api/smartwallbox/processor/sharing/unshare/v2"
PATH_WALLBOX_SHARER_INFO = "/api/smartwallbox/processor/sharing/sharerInfo/v2"
PATH_WALLBOX_TNC_CONTENT = "/api/smartwallbox/processor/tnc/content/v3"
PATH_WALLBOX_TNC_SIGN = "/api/smartwallbox/processor/tnc/sign/v3"
PATH_WALLBOX_SHARE_SIGN_TNC = "/api/smartwallbox/processor/sharer/signTnc/v3"

# v3.7.2: 车辆授权管理（vds/v1/vehicles/vehicle-user-auth 查询授权列表）与
# 公共充电探测（纯电/插混车型专属，数据驱动创建，纯油车型探测无数据不创建）：
#   evss/stations/v1            公共充电站（位置相关，无参探测失败不创建）
#   evss/orders/v2              充电订单
#   vpoi/chargestations/v3      充电站地图（位置相关）
PATH_VEHICLE_USER_AUTH = "/api/cnxapi-vds/v1/vehicles/vehicle-user-auth"
# v5.8.2: 车辆授权审批（写入——同意/拒绝他人申请授权你的车）
PATH_VEHICLE_USER_AUTH_APPROVAL = "/api/cnxapi-vds/v1/vehicles/vehicle-user-auth-approval"
PATH_EVSS_STATIONS = "/api/evss/stations/v1"
PATH_EVSS_ORDERS = "/api/evss/orders/v2"
PATH_VPOI_CHARGESTATIONS = "/api/vpoi/chargestations/v3"

# OTA 版本/详情（6.16.0 接口实测，2026-10-08，只读 GET 探测接入 v3.4.6）：
#   alert/ota/versions             OTA 版本列表
#   alert/ota/detail               OTA 详情（当前/目标版本等）
#   alert/search-ota-details        OTA 更新详情
#   alert/search-new-ota-status    新 OTA 状态
PATH_OTA_VERSIONS = "/api/alert/v1/ota/versions"
PATH_OTA_DETAIL = "/api/alert/v1/ota/detail"
PATH_OTA_SEARCH_DETAILS = "/api/alert/v1/search-ota-details"
PATH_OTA_NEW_STATUS = "/api/alert/v1/search-new-ota-status"
# 充电日志（6.16.0 接口实测，2026-10-08，只读 GET）：cevs/v2/chargelogs/retrieve
PATH_CHARGELOGS_RETRIEVE = "/api/cevs/v2/chargelogs/retrieve"
# 家充桩充电过程日志（只读 GET，并入 smartwallbox 探测族 v3.4.6）
PATH_SMARTWALLBOX_PROCESS_LOG = "/api/smartwallbox/processor/charging/records/process/log/v3"

# ---- v3.5.0：6.16.0 端点补全（全车型探测接入，2026-10-09 实测） ----
# 消息列表/标记已读（cnxapi-message）
PATH_MESSAGES_PAGE = "/api/cnxapi-message/app/messages/page"
PATH_MESSAGES_READ = "/api/cnxapi-message/app/messages/read"
# v5.8.2 未接入端点补全（2026-10-24 端点比对）——消息单条已读
PATH_MESSAGES_V2_READBYID = "/api/cnxapi-message/app/message/v2/readbyid"
# OTA 红点（alert/v1/vehicle-reddot-status——首页 OTA 可用角标）
PATH_OTA_REDDOT = "/api/alert/v1/vehicle-reddot-status"
# 车辆在线状态（vds/v1/vehicles/onlinernr）
PATH_ONLINENR = "/api/cnxapi-vds/v1/vehicles/onlinernr"
# 3D 车型图（vds/v1/vehicle/search-3d-vehicle-model-url）
PATH_3D_MODEL = "/api/cnxapi-vds/v1/vehicle/search-3d-vehicle-model-url"
# 预约行程任务（pds/v1/search-schedule-departure-tasks）
PATH_SCHEDULE_DEPARTURE = "/api/cnxapi-pds/v1/search-schedule-departure-tasks"
# 预约充电启停/保存（cevs/v2/departuretimes）
PATH_DEPARTURE_TOGGLE_ON = "/api/cevs/v2/departuretimes/toggleon"
PATH_DEPARTURE_TOGGLE_OFF = "/api/cevs/v2/departuretimes/toggleoff"
# v3.7.4: cevs 域命令状态查询（只读，6.16.0 实测）——
#   commandstatus/retrieve 检索预约/充电命令执行状态
PATH_CEVS_COMMAND_STATUS = "/api/cevs/v2/commandstatus/retrieve"
# v4.2.4: cevs 域写操作（6.16.0 Dart 接口分析确认方法/键）——
#   departuretimes/deleteall 清空全部预约出发/充电计划（body: data 包装+加密
#   加密；集成侧与 retrieve 同构：vin=cevs 场景密文 + xjw，raw_body 无外层签名）
PATH_DEPARTURE_DELETE_ALL = "/api/cevs/v2/departuretimes/deleteall"
# v5.8.2 未接入端点补全（2026-10-24 端点比对）——cevs 写入补全：
#   departuretimes/save 保存/新建预约出发充电计划（App 创建计划；写入
#     body 从 retrieve 探测返回结构回填，字段名全部实测）
#   chargelogs/delete    删除充电记录（chargeLogId 从 chargelogs 探测回填）
PATH_DEPARTURE_SAVE = "/api/cevs/v2/departuretimes/save"
PATH_CHARGELOGS_DELETE = "/api/cevs/v2/chargelogs/delete"
# v4.2.6: 移动流量商城 H5 地址（6.16.0 独立实测确认——CDS 域
#   GET vehicle/search-cmcc-traffic-h5-url，只读，keys data/status）
PATH_CMCC_TRAFFIC = "/api/cnxapi-cds/v1/vehicle/search-cmcc-traffic-h5-url"
# v4.3.3: fposb 4S 维保预约（cnjvsl-fposb/v2，全车型只读探测）——
#   osbhistory 预约历史 / osbbookingdetail 预约详情 / availableresource
#   可用资源（门店·工位·时间）/ sainfo 服务顾问 / puddetail 取送车订单详情
PATH_FPOSB_HISTORY = "/api/cnjvsl-fposb/v2/book/query/osbhistory"
PATH_FPOSB_BOOKING_DETAIL = "/api/cnjvsl-fposb/v2/book/query/osbbookingdetail"
PATH_FPOSB_AVAILABLE_RESOURCE = "/api/cnjvsl-fposb/v2/book/query/availableresource"
PATH_FPOSB_SA_INFO = "/api/cnjvsl-fposb/v2/book/query/sainfo"
PATH_FPOSB_PUD_DETAIL = "/api/cnjvsl-fposb/v2/book/pudorder/puddetail"
# v5.8.2 未接入端点补全（2026-10-24 端点比对）——fposb 写操作：
#   osborder 维保预约下单（POST）/ osbcancel 取消预约（DELETE）
PATH_FPOSB_ORDER = "/api/cnjvsl-fposb/v2/book/operation/osborder"
PATH_FPOSB_CANCEL = "/api/cnjvsl-fposb/v2/book/operation/osbcancel"
# v4.3.3: evss 充电站/免密支付（仅电/插混/混动车型探测创建）——
#   accounts 账户 / chargers/v1/info 充电站信息 / chargers/v2/parking 停车位 /
#   chargers/v2/events/order 充电订单事件 / freepassword 免密支付状态·通知
PATH_EVSS_ACCOUNTS = "/api/evss/accounts/v3"
PATH_EVSS_CHARGER_INFO = "/api/evss/chargers/v1/info"
PATH_EVSS_PARKING = "/api/evss/chargers/v2/parking"
PATH_EVSS_ORDER_EVENTS = "/api/evss/chargers/v2/events/order"
PATH_EVSS_FREEPASS_STATUS = "/api/evss/freepassword/v1/status"
PATH_EVSS_FREEPASS_NOTICE = "/api/evss/freepassword/v1/notice"
# v5.8.2 未接入端点补全（2026-10-24 端点比对）——evss 充电枪锁/解锁
# （品牌专属桩 equipment 锁枪/解锁，equipmentId 从 chargers/v1/info 回填）
PATH_EVSS_CHARGER_LOCK = "/api/evss/chargers/v1/brand/equipment/lock/down"

# v3.7.4: cevs 域 RSA 加密候选公钥（App encryptByRSA 用公钥加密敏感字段后
# POST；集成原 AES 密文被服务器 RSA 解密失败 → Impossible modulus）。
# 公钥取自 客户端字符串表 2088-2089 行（完整 2048-bit PKCS#8
# SPKI，MIIBI 开头——与 App encryptByRSA 调用一致）。字符串表其余两处
# "-----BEGIN PUBLIC KEY-----"（19274/22157 行）base64 被截断无法拼出
# 完整 DER，不臆造；若 k1 命中失败，需实机样本确认第二候选。
CEVS_RSA_PUBKEYS = [
    # k1: 客户端 PKCS#1 2048（v3.7.14：raw_body 形态下首次测）
    "-----BEGIN PUBLIC KEY-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAv1XJU2JkRL0yvJoIwqpQ5ycYRi/2GsIgunOsz1Y+qDhYN/u+YQrWJj8iu6DypF1iDe9Lgtp62koR7mODtMjzRDzK1PzZFNMfHS+aD+VegMWfm834fS/wSNMP9J7DYx05TT5e3+lcmxBOF86euJqDBZsPhX5P1hNimRa97S4cVA1G9iw2ZY6YLU1nbBpPiG1V97TLbATqRKx6yXNmGcZwrE0sIXS62PTO24/9y6H0Jiq5EJcshXlvAHIl3usoPSmSBJWiE7acdOqWuiAiwaI/PnaXSgbtg1LErWJhT9Nv5R84DFiAlLJz7Hg7MRuQ2XO6pPEH5QiFb7tMqzU9WSGsuQIDAQAB\n-----END PUBLIC KEY-----",
    # k2: rsa_feed_back_public_key.txt PKCS#1 2048（未测）
    "-----BEGIN PUBLIC KEY-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAjmLaNFOoEzPYqs0WeN7UICxDTfBVer2mOyMW52Zw0T4gP4u8x+SEsUGXgs4sltA83EYJVb4X0cmr/PJK92j20RjtZvkyeWIWIpxABzQC+DZ8YAQlXTab4O4hQPD6QnuqlhSJ7n7DPGELnG8nbJYKwYzs3+ssNJAa3OMg4fwK6aaMxGYrUUGn2NEbSxNZIQ+3/OKW+UCAImv0cLl2dGPeCC14VLS8EsPVPmD8cmT3vSNIvqdtpmwos8af43SNgZ6o68tJv5W1ujZ8Op+dQnOaccFtugZMLCQCcpYh8l66p6Zf8Mf+XKtqyVO1V1uVHuD/tALZP1lZsGBmFwkiZSX2/wIDAQAB\n-----END PUBLIC KEY-----",
    # k3: cnesl_prod 4096（v3.7.8 已测，保留作对照）
    "-----BEGIN PUBLIC KEY-----\nMIICITANBgkqhkiG9w0BAQEFAAOCAg4AMIICCQKCAgAILgUAjKBUk9+7E2iXVN+arLgZZUCXAvMZlGXvLnj0RYCZotUpEz92vet4iWraXSGaR7m7aFld2FxOS5ZmoPiaHu/M5xaD9fxygF5ODDmUEw3l/Gos/xu/Y1FyDiYRK6gG/X4ZGZXBK/bH77cS7EVhgkHeX9qwmITxJ7oy2V0SLDo3PBIv0+uxQCglgRluFRjPNoGiLHPEOxa6OWjlX0upOZHL3xHik6GPpidRnk96WBHfSwBf3s/t7akfTGp/6+i0UrTY4tQxfy7JqlRvf58gLEfMtJVvJ989IN03I5fcgHcHA0BRPiq5YR9iF5G/ma1vbwv6kGn1oE8+T93xq/PKjhAfaU7qsQHjjFTl50YhQg/LDeQ0zkvpqXDtBOv/Kem1CxplXIiL9nryyLvF+IQ/s9LOhkQITkHWAlIkkYabTbxzwtQrPWRMpM0Eax8Z+aa2LZ3T2AHWoh3KQgA7Njp6sasCVkYGc6wuUaUcUcJvxtDqGtZlC+3TD8tcMRoL4qCa2NKKR0GJ8F3In0qEj3f9+H6djVz4Q+go2/GKRmTB7npNVdhWtK3rpAJWH4wXBXxtRC1TXm9HjoU4qL3WwJjUHcr6BHGkhMZ1yBHIb80y9yzkOipqMW0kRsfLlQTgSlLngELNvZha1OYBwUcecdDBN1CHhWPiQccBSPhc4/sCbQIDAQAB\n-----END PUBLIC KEY-----",
]
# 车辆设备档案（pds/v1/device）
PATH_PDS_DEVICE = "/api/cnxapi-pds/v1/device"
# 车辆用户授权状态（vds/v1/vehicles/search-vehicle-user-auth-status）
PATH_USER_AUTH_STATUS = "/api/cnxapi-vds/v1/vehicles/search-vehicle-user-auth-status"
# SRS 安全档案（cds/v1/vehicles/search-srs-profile）
PATH_SRS_PROFILE = "/api/cnxapi-cds/v1/vehicles/search-srs-profile"
# v5.8.2 未接入端点补全（2026-10-24 端点比对）——SRS 紧急联系人档案写操作
# （add 新增/update 更新，body 从 search-srs-profile 探测返回回填）
PATH_SRS_ADD = "/api/cnxapi-cds/v1/vehicles/add-srs-profile"
PATH_SRS_UPDATE = "/api/cnxapi-cds/v1/vehicles/update-srs-profile"
# 传感器影子（cnjvsl-sensor-mapping/v2/sensor/shadow）
PATH_SENSOR_SHADOW = "/api/cnjvsl-sensor-mapping/v2/sensor/shadow"

# v4.3.5: 228 端点比对后未接入的只读 GET 端点补全（全部无需实测——
# 标准 R3 签名 GET，探测到数据才创建实体）。来源：6.16.0 APK 端点全集
# 归一化 diff（2026-10-13），与现有实体不重复、非变体、非 App 专用。
# --- 家充桩只读扩展（smartwallbox，探测到绑定桩才创建） ---
PATH_SMARTWALLBOX_BOUND = "/api/smartwallbox/processor/binding/myBoundWallbox/v3"
PATH_SMARTWALLBOX_INFO = "/api/smartwallbox/processor/binding/wallboxInfo/v3"
PATH_SMARTWALLBOX_RECORDS_V2 = "/api/smartwallbox/processor/charging/records/list/v2"
# --- 充电域只读扩展（evss/vpoi，仅电/插混/混动探测到数据才创建） ---
PATH_EVSS_ORDER_STATUS = "/api/evss/chargers/v2/events/orderStatus"
PATH_EVSS_APPS_INFO = "/api/evss/apps/v1/info"
PATH_EVSS_USAGE_NOTICE = "/api/evss/chargers/v1/events/usage-notice"
PATH_VPOI_STATUS = "/api/vpoi/chargestations/v3/status/"
PATH_VPOI_SAVED = "/api/vpoi/chargestations/v3/saved"
# v5.8.4: vpoi v4（App 已升级 v4 路由；数据格式待 App 实测确认后替换 v3
# 传感器数据源——先探测落盘，实测校准后启用实体）
PATH_VPOI_CHARGESTATIONS_V4 = "/api/vpoi/chargestations/v4"
PATH_VPOI_SAVED_V4 = "/api/vpoi/chargestations/v4/saved"
# --- OTA 协议（全车型，只读） ---
PATH_OTA_AGREEMENTS = "/api/alert/v1/search-ota-agreements"

# v4.3.6: 136 未接入清单第二轮筛选——剩余只读业务端点补全（全部无需
# 实测：标准 R3 签名，探测多形态自适应 GET→POST 空 body，探测到数据才创建）。
# --- 充电域订单/事件/评价（evss，仅电/插混/混动探测到数据才创建） ---
PATH_EVSS_PAYMENT_SUMMARY = "/api/evss/orders/v2/payment/summary"
PATH_EVSS_INVOICEABLES = "/api/evss/orders/v2/invoices/invoiceables"
PATH_EVSS_EVENTS_V3 = "/api/evss/chargers/v3/events"
PATH_EVSS_EVALUATE_URL = "/api/evss/orders/v1/evaluate/evaluate-url"
# --- 家充桩 BLE 充电车辆信息（smartwallbox，探测到绑定桩才创建） ---
PATH_WALLBOX_BLE_VEHICLES = "/api/smartwallbox/processor/auto/charging/ble/queryVehicleInfos/v3"
PATH_SMARTWALLBOX_BLE_ADD = "/api/smartwallbox/processor/auto/charging/ble/add/v3"
# --- app-init 域（全车型：应用设置/车型主数据，公开配置类只读） ---
PATH_APP_INIT_SETTINGS = "/api/app-init/v1/app-setting/info"
PATH_VEHICLE_MODEL_MASTERDATA = "/api/app-init/vehicleModel/v1/masterdata"
# v5.8.4: 访客模式（visitor_mode v3 页面初始化信息，App 设置页入口；
# 实测确认（2026-10-25 接口定义 VisitorModeV3DTO）只读 GET，探测到数据才创建）
PATH_VISITOR_MODE = "/api/app-init/v3/visitor-mode/init-page-info"

# 行车记录仪视频（pds/v1 视频录制/查询）
PATH_VIDEO_START = "/api/cnxapi-pds/v1/start-video-recording"
PATH_VIDEO_STOP = "/api/cnxapi-pds/v1/stop-video-recording"
PATH_VIDEO_FILE = "/api/cnxapi-pds/v1/search-video-file-url"

# v3.5.1: 账号级只读端点补全（全车型/多账号适配）
# 用户完整车辆清单（vds/v5/uservehicles——含每车 featureData 能力位图，全车型适配基石）
PATH_USER_VEHICLES = "/api/cnxapi-vds/v5/uservehicles"
# 消息中心 v2（6.16.0 主用版本）
PATH_MESSAGES_V2_PAGE = "/api/cnxapi-message/app/messages/v2/page"
# 车辆共享列表（cnesl-user 只读——他人授权访问状态）
PATH_SHARE_LIST = "/api/cnesl-user/v1/search-vehicle-share-list"
# v4.3.5: 车辆分享能力/分享历史（cnesl-user 只读探测，探测到数据才创建）
PATH_SHARE_CAPABILITY = "/api/cnesl-user/v1/search-vehicle-share-capability"
PATH_SHARE_HISTORY = "/api/cnesl-user/v1/search-vehicle-share-history-list"
# v4.3.6: 用户登录状态（cnesl-user 账号级只读，探测到数据才创建）
PATH_USER_LOGIN_STATUS = "/api/cnesl-user/v1/user/login/status"
# 车辆设备列表（vds/v1/search-vehicle-device-list）
PATH_DEVICE_LIST = "/api/cnxapi-vds/v1/search-vehicle-device-list"

# v3.6.2: 待开发清单补全（6.16.0 端点）
# 消息中心 v3（mcm 域，APK 主用——替代 cnxapi-message 双 404）
PATH_MCM_MESSAGES_V3 = "/api/mcm/messagecenter/v3/user/messages"
# 用户级红点（cnesl-user 域——账号级统一红点，字段更少，替代 alert 缺字段红点）
PATH_USER_REDDOT = "/api/cnesl-user/v1/reddot-status"

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
# 的真实规范是 FORD_EXTEND_START，但实测 send-command 网关白名单不含此项，
# 不支持——见 v3.1.9 实测限制）。
# v5.8.4: 重新接入 FORD_EXTEND_START（2026-10-25 接口定义 CommandTypeV2 枚举确认
# App 存在该命令 = 远程启动延时）。⚠️ commandType 字符串为推断值
# "RemoteStartExtend"（RemoteStart 族命名惯例，仅见枚举名未见图文字符串），
# 待实车实测校准——实测后仅改此处。创建由服务端 command_whitelist
# 门控（白名单含该命令才出现；2026-09-29 服务端枚举 16 命令尚不含它，
# 若福特云后续开放则自动出现，不会制造假实体）。
CMD_EXTEND_START = "RemoteStartExtend"
CMD_CENTRAL_LIGHTING = "CentralZoneLightingON"      # 中央区灯光开（无独立关闭命令）
CMD_AUTO_REFRESH = "AutoRefresh"                    # 自动刷新
# 鸣笛寻车（v3.0.5 起为 switch）：中国区 send-command 网关白名单不含 Honk（HTTP 400 100502），
# 走 v5 网关真实通道（DELETE /api/vehicles/v5/{vin}/honk，实测 200 + commandId）
CMD_HONK = "Honk"
HONK_AUTO_OFF_SECONDS = 30                          # 鸣笛寻车自动复位秒数（App 鸣笛约 30 秒自动停止）
# v5.8.10: 灯光寻车自动复位秒数——ZoneLightingON 命令不带时长参数，灯由车机
# 自行熄灭；此值只决定开关实体的显示复位时间（不下发 OFF 命令，避免打扰）。
LIGHT_FIND_AUTO_OFF_SECONDS = 30
# 声光共舞（panic）端点（v3.1.8）：App 的 FordPanicCommand 走
# POST /api/vehicles/v5/{vin}/panic/{paniconduration}（灯+喇叭警报）；
# 实测返回 404（云端不支持，App 中该类型可能也只在部分车型/蓝牙下可用）。
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
# 部分车型 encryptedCarId=null = 车型无远程影像硬件——实体不创建）
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

# 品牌（v4.7.4：登录第一步选择福特派/林肯之道）
CONF_BRAND = "brand"
BRAND_FORD = "fordpass"
BRAND_LINCOLN = "lincoln"
BRAND_LABELS = {
    BRAND_FORD: "福特派 FordPass",
    BRAND_LINCOLN: "林肯之道 Lincoln Way",
}
DEFAULT_BRAND = BRAND_FORD

# 品牌档案表（v4.7.4）：登录/API 常量按品牌取用。
# 林肯之道（com.lincoln.lincolnwaycn / clutchcn）与福特派共用同一加密库
# （加密模块 包名绑定含林肯），但 B2C tenant/验证码登录端点/
# App 指纹头无实测数据——先占位 None 不编造值，ready=False 时 config flow
# 提示"登录链路待实测"，实测补齐后置 ready=True 即可完整支持。
BRAND_PROFILE = {
    BRAND_FORD: {
        "base_url": BASE_URL,
        "touch_point": TOUCH_POINT,
        "brand_code": "FORD",
        "app_key": "fordpass",
        "client_type": CLIENT_TYPE,
        "app_version": APP_VERSION,
        "passcode_login": PATH_PASSCODE_LOGIN,
        "b2c_authority": B2C_AUTHORITY,
        "b2c_policy": B2C_POLICY,
        "b2c_client_id": B2C_CLIENT_ID,
        "b2c_redirect": B2C_REDIRECT_URI,
        "ready": True,
    },
    BRAND_LINCOLN: {
        # v5.5.3: 林肯登录链路参数（2026-10-23 林肯 App v6.7.0 实机样本实证）——
        # touchPoint=LINCOLN_APP_LOGIN / brand=LINCOLN / vehicles/list
        # appKey=lincoln&clientType=LW；网关与验证码/换 token 路径与福特共用
        # v5.5.8: 林肯 App 5.8.0 实机验证——cnxapi-vds/cvinfo/cds/token-exchange、
        # v5 控制、cevs/smartwallbox/evss/vpoi/lwosb v2/message/OTA 全系列与福特
        # 共用同一网关与命令集（FORD_ENGINE_START/LOCK/HONK/TRAILERLIGHT 等）；
        # 车型字段（fuelType/modelName/ccfeatures/cvfeatures/vehicleStatus）与
        # 状态字段（gpsState/fuelLevel/odometer/tirePressure/oilLife）一致，
        # 实体创建与解析逻辑直接复用；加密 加密模块 与福特
        # 共用。林肯专属 cnesl-user 车辆共享/俱乐部、sensor-shadow 为附加功能
        "base_url": BASE_URL,
        "touch_point": "LINCOLN_APP_LOGIN",
        "brand_code": "LINCOLN",
        "app_key": "lincoln",
        "client_type": "LW",
        "app_version": "6.0.0",
        "passcode_login": PATH_PASSCODE_LOGIN,
        "b2c_authority": None,
        "b2c_policy": None,
        "b2c_client_id": None,
        "b2c_redirect": None,
        # v4.8.1: 林肯之道 5.8.0 实测确认包名（X-Requested-With 客户端指纹）
        "app_package": "com.lincoln.lincolnwaycn",
        "ready": True,
    },
}

# Defaults（v2.7.8：刷新间隔以分钟为单位，默认 30 分钟）
DEFAULT_SCAN_INTERVAL_MINUTES = 30

# v4.6.2: 手动刷新探测重探冷却（秒）——60s 内重复点按不重探探测端点
# （探测 ~39 请求突发，高频点按会触发云端限流），只做常规全量刷新
PROBE_FORCE_COOLDOWN = 60
DEFAULT_CONF_FLOW_TITLE = "福特派互联"

# v3.8.6: 探测类请求持久化 TTL（秒 = 7 天）——探测结果落盘，重启/重载后
# setup 在 TTL 内复用（不再重探制造噪音），过期后才重新探测（捕捉车型
# 云端能力变化）。
PROBE_PERSIST_TTL = 604800.0

# Platforms
# P2-8（v5.6.0）：删除——真正的平台列表在 __init__.py PLATFORMS（含 image），
# 此副本缺 image 且从未被引用，保留只会误导。
