# FordPass China 福特派互联 Home Assistant 自定义集成

<img width="256" height="256" alt="images" src=custom_components/fordpass_cn/brand/logo.png />


Home Assistant 自定义集成，接入福特中国（长安福特）福特派互联服务，支持短信验证码 / 用户名密码两种登录方式、远程控车与车辆定位追踪。

> ⚠️ 本项目为个人逆向研究作品，与福特官方无任何关联。使用本集成即表示同意自行承担相关风险与责任。

## 功能特性

- **双登录方式**：手机号短信验证码登录（无需密码）或用户名密码登录（Azure B2C 官方流程），配置时可自由选择。> 注意：用户名密码登录走微软 Azure AD B2C 网关，其风控可能拦截非浏览器自动化请求（返回 `AADB2C: An exception has occurred` 或 567）；如遇到此类失败，请改用短信验证码登录（登录后令牌自动续期，无需频繁重登）。
- **车辆异常警示**：接入福特 vha/activealert 真实告警接口，明文中文告警显示
- **远程控车**：上锁 / 解锁 / 远程启动 / 远程熄火 / 鸣笛寻车 / 灯光寻车 / 刷新车辆状态
- **车辆状态**：门锁、报警、燃油量、胎压、里程等实时状态
- **车辆异常警示**：接入福特 vha/activealert 真实告警接口，明文中文显示（如「胎压监测系统警告」）
- **车辆图片**：展示车型渲染图（如「锐际 Escape」），图片持久保存不删除
- **车辆定位追踪**：GPS 车辆位置（设备追踪器），可在 HA 地图上显示
- **多实体中文命名**：设备名为车型（如「锐际 Escape」），车牌号、地址等属性齐全
- **自动令牌刷新**：访问令牌过期自动刷新，无需重新登录

## 安装

### 方式一：HACS（推荐）

1. 将 `https://github.com/RockJesus/ha-fordpass-cn` 添加为 HACS 自定义仓库（类别：Integration）
2. 搜索并安装 `FordPass China 福特派互联`
3. 重启 Home Assistant

### 方式二：手动安装

1. 下载最新版 Release（`fordpass_cn_3.1.16.zip`）
2. 解压后将 `custom_components/fordpass_cn/` 整个目录复制到 HA 的 `/config/custom_components/` 下
3. 重启 Home Assistant

## 配置

1. 设置 → 设备与服务 → 添加集成 → 搜索「福特派互联」
2. 选择登录方式：**手机号验证码登录**（输入手机号 → 获取短信验证码 → 输入完成）或 **用户名密码登录**（输入福特派账号手机号与密码，密码仅用于本次登录换令牌、不会保存）
3. 按所选方式完成登录
4. 在集成选项（Options）中可开启「启用车辆定位追踪」

> 依赖 `unicorn`（ARM64 白盒 AES 仿真）与 `pyelftools`，HA 首次加载时会自动安装。

## 支持的实体

| 类型 | 实体 | 说明 |
|---|---|---|
| device_tracker | 车辆定位 | GPS 坐标 + 地址属性，地图可显示 |
| image | 车辆图片 | 车型渲染图（如「锐际 Escape」） |
| lock | 车门锁 / 后备箱锁 | 上锁 / 解锁；后备箱锁（TrunkUnlock，解锁弹开 / 随全车锁定，按车型尾门能力创建） |
| button | 手动拉取最新状态 / 请求车机刷新状态 / 中央区灯光 / 语音助手初始化/取消 / 辅助设置 / OTA 激活排程 / 重置空调滤芯 / 拖车灯光检测 | 手动拉取（ForceRefresh）；请求车机刷新（AutoRefresh）；中央区灯光/语音助手/辅助设置/OTA 激活排程按车型能力创建（云端不支持的车型按下返回网关明确报错）；重置空调滤芯（`PUT /api/cnxapi-vds/v1/aar/status`）；拖车灯光检测（双后轮皮卡） |
| switch | 远程启动 / 灯光寻车 / 鸣笛寻车 | 远程启动/熄火；灯光寻车（ZoneLightingON/OFF，按车型能力创建）；鸣笛寻车（开 = `POST /api/vehicles/v5/{vin}/honk`，关 = `DELETE` 同路径；鸣笛时长与类型按设置生效） |
| select | 鸣笛持续时长 / 鸣笛类型 | 鸣笛寻车设置：持续时长 5/10/15/20 秒；鸣笛类型 雨落荷叶/急浪拍岸/汽笛长鸣/空谷回音/声光共舞（选择即保存，鸣笛时参数直达车机） |
| sensor | 车辆状态 | 门锁、报警、燃油、胎压、里程、车牌、自动熄火倒计时、车窗（未关闭显示程度/百分比）等 |
| sensor | 车辆异常警示 | 真实告警接口（胎压监测系统警告等），无异常显示「无异常」 |
| sensor | 保养计划 / 召回信息 / SIM 卡 / WiFi 热点 | 服务信息（v3.1.5，`GET maintenance-plan / recall / sim/info / wifi/status`，实测 200；数据无效或服务端业务失败时如实显示，不创建失败实体） |
| sensor | 空调滤芯状态 | 滤芯健康度 + 上次更换日期（v3.1.7，`GET /api/cnxapi-vds/v1/aar/status`，实测 200；App 6.16.0 新增功能，无 AAR 能力的车型不创建实体） |
| sensor | 车辆服务信息 | 云端能力位图 + 救援/客服/售后电话 + 电子说明书地址（v3.1.9，`GET /api/cnxapi-vds/v2/vehicles/ccfeatures`，实测 200；App 用于判断每辆车支持哪些功能的权威云端清单） |
| sensor | 机油寿命 / 剩余可行驶里程 / 慢漏气胎 / 预测性诊断 | 预测性诊断（v3.1.9，`GET /api/cnxapi-cds/prognostic/v1/list`，实测 200）：机油寿命百分比、按寿命剩余里程、慢漏气胎标识、诊断提示（featureType=OL 机油寿命族） |
| sensor | 鸣笛设置云端状态 | 福特账户云端 VehicleAnnouncementSetting 回读（类型 + 时长），App/其他设备改动可同步感知 |

> 实体创建全部数据驱动：登录后按本车云端能力与车辆数据判断（多 VIN 账号下
> 每辆车独立创建自己支持的实体组），不支持的命令按下会返回网关明确报错，
> 不制造静默无效实体；刷新失败保留最后已知状态，0 unavailable。

## 版本历史

<details>
<summary>📜 版本历史（点击展开）</summary>

- **v3.1.17**：语音助手按钮补齐 cmdSpec + 新增未读消息传感器——① **InitialVA/CancelVA 修复**：HAR 实测（2026-10-03）确认福特派真实请求带 `cmdSpec`（InitialVA = VAType:4 + Duration:15、CancelVA = VAType:4），此前集成只发 commandType 导致网关 `228205 cmdSpec can not empty` / 部分车型无响应——`send_command` 新增可选 cmdSpec 参数，语音助手按钮按福特派真实参数发送；② **未读消息传感器**：`GET /api/cnxapi-message/app/messages/summary`（HAR 实测 200，仅 timestamp+sign 签名）——显示「有/无未读消息」+ 属性含未读描述 / 最新消息主题 / 未读分类列表；无该数据的车型不创建，0 unavailable
- **v3.1.15**：cvfeatures v4 端点请求修正——v3.1.14 用 `appKey/appVersion/clientType` query 实测 404（Resource not found），本次改用 `_get_signed`（encryptedVin/xjw query + R3 签名，与 ccfeatures(v2) 同通道）重新实测；「车辆能力清单」传感器如实显示结果（成功=能力名列表，失败=错误原因，0 unavailable）
- **v3.1.14**：「车辆能力清单」传感器增强——任何 dict 响应都创建实体（0 unavailable），获取失败时如实显示错误原因（如 `获取失败：404 Not Found...`），成功但结构未识别时显示「已获取（结构待车型实测解析）」+ 原始响应属性——本轮 cvfeatures v4 端点实测诊断用（此前 404 疑缺完整 query，实体属性可查实际响应/错误）
- **v3.1.13**：「都继续」四线全部落地——① **鸣笛命令状态传感器**：逆向确认 App 的 `GET /api/vehicles/v5/{vin}/announcestatus/{commandId}/`（v5 命令轮询组，与 statusrefresh/{commandId} 并列），鸣笛开关触发后自动轮询 announcestatus 三次（2s/5s/10s），结果写入「鸣笛命令状态」传感器（未触发/执行中/网关状态/查询失败，永远可用不显示 unavailable）；② **停车影像 / 行车监控**：逆向确认 App `VehicleManagerEndpoint.searchVehicleParkingImage / searchVehicleMonitorTraffic`（PDS 网关），请求体含 `carId`=车辆列表 `encryptedCarId`——非空才创建按钮（锐际 encryptedCarId=null = 无远程影像硬件，不创建、0 unavailable）；③ **authedFeatures 双解释**：`A4=0xA4=164=10100100₂` 逐位拆解为 VDSFeatureType 特性名（bit2 计划保养服务 / bit5 道路救援 / bit7 福特金融，待多车型验证）；④ **车辆能力清单 v4**：逆向确认 `GET /api/cnxapi-vds/v4/vehicles/cvfeatures`（App `VcsRepositoryProvider::fetchCapabilityV4`，首页能力卡片 PAAK/EV 管理/哨兵/灯光等的权威来源）——接入「车辆能力清单」传感器；此前 404 疑缺完整 query，本次接入后实测
- **v3.1.12**：能力位图解码完成——逆向福特派 6.16.0 `libapp.so`（VS Build Tools + Dart 3.8.1 SDK 源码 + blutter 全链 MSVC 编译，stripped 库打 magic 快照扫描/段表补丁），从 Object Pool 还原 `VDSFeatureType` 枚举全集 24 项（osb 在线服务 / maintenanceSchedule 保养计划 / serviceHistory 服务记录 / scheduledServicePlan 计划保养服务 / guides 指南 / rsa 道路救援 / extendedWarranty 延保 / fordCredit 福特金融 / privateChargingService 私充服务 / eCard 电子卡 / WallBoxAutoAuth 家充桩自动认证 / customerFeedback 客户反馈 / carGuide 用车指南 / personalizedPicture 个性化照片 / rccAuto / InteSubscription 国际订阅 / MySubscription 我的订阅 / MyTestDrive 我的试驾 / MyOrder 我的订单 / ReservationInquiry 预约查询 / MaintenanceWorkOrder 保养工单 / CarPickupDeliveryInquiry 取送车查询 / MyRights 我的权益 / SyncToCarNavigation 同步到车机导航）——**锐际位图解出**：`03,04,05,06,07,08,10,11,23` = 计划保养服务、指南、道路救援、延保、福特金融、私充服务、我的订阅、我的试驾 + 未定义特性（0x23=35 超 App 枚举范围，`checkVDSFeature` 忽略）。「车辆服务信息」传感器属性升级：`feature_XX` 输出 `成员名（中文含义）`、新增 `features` 汇总、authedFeatures 标注位掩码未完全解码；解码映射写死进集成，任何车型登录后位图即可翻译
- **v3.1.11**：能力位图（ccfeatures `availableFeatures`）结构化增强——逐项展开属性（`feature_03`~`feature_23` = 特性 ID 0x03…，十进制对照）+ `feature_count` + 位图二进制视图（`feature_bits`，MSB 在前，1 = 该特性可用）+ `authedFeatures` 展开（A4 = 0xA4 = 164）；为多车型位图对比解码提供数据基础（同一枚举下数字重叠 = 同能力）
- **v3.1.10**：全车型支持策略回归——恢复 v3.1.8 因锐际实测网关拒绝（228205/402）而临时移除的实体创建：**后备箱锁**（TrunkUnlock，按尾门能力创建）、**灯光寻车**（ZoneLightingON/OFF）、**中央区灯光 / 语音助手初始化/取消 / 辅助设置 / OTA 激活排程**（按各自车型能力路径判断）——集成包含全部实体代码，登录后按车辆能力判断创建，云端不支持的车型按下返回网关明确报错、如实提示；删除 `CMD_EXTEND_START` 遗留误导别名。README：版本历史全部折叠；移除「实测限制」段落（实体能力差异已由「按车型能力创建 + 按下明确报错」机制覆盖）
- **v3.1.9**：新增两组云端服务信息实体（逆向 6.16.0 确认、全部 200 实测）——①**车辆服务信息**（`GET /api/cnxapi-vds/v2/vehicles/ccfeatures`）：云端能力位图 availableFeatures + authedFeatures + 道路救援/客服/售后电话 + 车型电子说明书 URL，这是 App 判断「每辆车支持哪些功能」的权威云端清单；②**预测性诊断**（`GET /api/cnxapi-cds/prognostic/v1/list`）：机油寿命百分比（iolm=41）、剩余可行驶里程（4000km）、寿命归零月份、慢漏气胎标识、诊断提示，拆分 4 个传感器；③修正：删除 `CMD_EXTEND_START` 误导别名（曾错误指向 TrunkUnlock）；实测 send-command 白名单完整枚举确认 ExtendStart（远程启动延长）不在其中，锐际不支持，不创建实体。数据驱动创建——无 ccfeatures/prognostic 数据的车型不创建实体，0 unavailable
- **v3.1.8**：按实车测试清单修复与收敛——①**移除网关不支持实体**：后备箱锁（TrunkUnlock）、灯光寻车（ZoneLighting）、中央区灯光（402）、语音助手初始化/取消、辅助设置、OTA 激活排程在锐际实测均被网关拒绝（`228205 cmdSpec can not empty` / 402），移除实体避免假按钮；②**鸣笛类型/时长参数修正**：ChirpType 从 1-5 修正为 App 枚举 0-4（此前差一错位）；「声光共舞」改为走独立 panic 端点 `POST /api/vehicles/v5/{vin}/panic/{duration}`（灯+喇叭，锐际 404 明确报错）；③**车窗状态**：未关闭时显示「未关闭（部分开启/全开）」，数值型数据（部分车型）显示「未关闭 N%」；④**远程启动时间**：修复毫秒时间戳解析（此前 13 位毫秒解析失败显示「未启动」）；⑤确认门锁/远程启动/重置空调滤芯/刷新类命令均实测正常

- **v3.1.6**：全车型自动适配 + 多 VIN 支持——账号下每一辆车建立独立 coordinator / 设备 / 实体组（设备名 = 车辆昵称/车型，实体前缀 = 车辆名拼音，此前只加载第一辆车）。实体创建全部数据驱动：传感器按 vehicle-status 实际字段过滤（`skip_if_missing` + 创建期 `data_usable`），控制实体按车型能力过滤（`crccFlag` 远程控车 / `dualRearWheel` 双后轮拖车 / 尾门字段 / `preCondStatusDsply` 远程空调等）。因此**任何车型登录（电马纯电 / 锐界L混动 / 领裕柴油 / Ranger皮卡 / 锐际燃油等）只加载自己车真实支持的设备与实体，0 unavailable**（刷新失败保留最后已知状态）。修改扫描间隔/定位开关即时应用到全部车辆

- **v3.1.4**：鸣笛寻车设置云端保存真正打通——逆向还原福特派 App 的 UserPreferenceV2 通道（`POST /api/cnxapi-pds/v1/user/preference-by-groups`，`VehicleAnnouncementSetting` 组：`vehicleAnnouncementSoundType` 5 种类型 + `vehicleAnnouncementDuration` 5-20 秒），`保存鸣笛设置`按钮现在把设置**真实写入福特账户云端**（旧 RCC profile-by-vin 通道 100400 根因 = signatureR2 独立密钥体系，实为误入）；签名层修正：headers 用 App 真实无连字符名（`appversion/ostype/osversion/clienttype`）、嵌套参数按 App 序列化（list→`[a&b]`、dict→`{k=v}`）；新增「鸣笛设置云端状态」传感器（`GET preference-list` 回读，App/其他设备改动可同步感知）。同时打通此前 100400 的保养计划/召回/SIM/WiFi 四个端点（实测 200），新增对应服务信息传感器（数据无效时不创建）；质保端点已过验签但服务端参数校验 100502（待 App 抓包），暂不接入
- **v3.1.3**：新增「OTA 设置状态」传感器（`GET /api/alert/v1/ota/setting-info`，实测 200）——展示远程 OTA 开关、激活排程、当前/目标版本、状态描述；车辆无 OTA 能力或端点失败时不创建实体。同时完成对逆向清单中其余候选端点的实机验证：警报寻车（v5 `/panic/{duration}` 404）、单门独立解锁（v5 `/door/{doortype}/lock` 404，send-command 拒绝 `doorType` 字段）、OTA 详情/版本（`capabilityMmota is false` 206004）——以上端点在本车型/网关不可用，均不接入，避免制造失败实体
- **v3.1.2**：所有实体不再因福特云刷新失败而显示「不可用」——`available` 统一固定为可用（开关/锁/按钮/选择器/传感器/定位/图片共 15 处），刷新失败时保留最后已知状态，不再整体变灰；操作类实体在云异常时调用仍会返回明确错误提示，但实体本身始终可用
- **v3.1.1**：修复保存集成选项时的崩溃——`async_update_options` 在集成尚未加载完成时被调用（重启中 / 加载失败 / 已卸载后再改选项）会抛 `KeyError: 'fordpass_cn'`；本次改为防御性读取：未加载时安全跳过，新选项在下次加载时从 `entry.options` 自动生效（无状态丢失）
- **v3.1.0**：修复「保存鸣笛设置」按钮崩溃——点击报 `'FordPassCoordinator' object has no attribute 'entry_id'`（v3.0.8 引入：按钮读取配置时依赖 coordinator 上的 entry_id，但 coordinator 未保存该属性）；本次给 coordinator 增加 `entry_id` 属性（构造时由配置条目传入），按钮读取改为「entry_id 直取 + 遍历兜底」，任何构造路径下点击均不再报错
- **v3.0.9**：网络异常加固——修复网关偶发返回非文本响应（二进制/损坏 body）时的 `'utf-8' codec can't decode` 解码崩溃（v2.7.8 曾触发；v3.0.6 已保护主请求通道）；本次将鸣笛开/关（`v5 honk`）、LBS 令牌交换、位置查询共 4 处直连通道与主请求通道对齐，统一捕获解码异常 → 读取原始字节诊断 → 转为明确的 `FordPassApiError`（由上层重试策略处理），不再让裸解码错误冒泡到日志
- **v3.0.8**：新增「鸣笛寻车设置」——持续时长（5/10/15/20 秒）与鸣笛类型（雨落荷叶/急浪拍岸/汽笛长鸣/空谷回音/声光共舞）两个 `select` 实体，选择即保存到集成配置（重启不丢失）；鸣笛寻车开关开启时按设置把 `ChirpOrHonkDuration`/`ChirpType` 参数传给车机（等效上传车机）；新增「保存鸣笛设置」按钮——调用官方 App 同款 RCC Profile 端点 `POST /api/cnxapi-cds/crcc/v1/profile-by-vin`（`{userPreferences:[{preferenceType,preferenceValue}]}`，字段结构已通过服务端校验）尝试账户云端持久化，云端签名体系（App signatureR2）尚未还原时回退为本地保存（下次鸣笛仍按新设置执行）
- **v3.0.7**：鸣笛寻车升级为真实「开/关」双通道——静态逆向还原福特派 App 官方协议（`libapp.so`）：开 = `POST /api/vehicles/v5/{vin}/honk`（明文 JSON body：`ChirpOrHonkDuration` / `IntervalBetweenRequests` / `ChirpType`，即 App 的 `FordHonkCommand` 通道），关 = `DELETE /api/vehicles/v5/{vin}/honk`（App 的 `FordHonkCancelCommand` 通道）；「关」由原来的本地复位升级为真实停止通道
- **v3.0.6**：修复「手动拉取最新状态」偶发报错——福特网关在 ForceRefresh 后立即拉取 vehicle-status 时偶发返回非文本响应（原日志 `'utf-8' codec can't decode byte 0xfb`）；现在对非文本响应明确报错并自动重试一次（间隔 2 秒），不再让解码错误打断刷新流程
- **v3.0.5**：鸣笛寻车由按钮迁移为开关实体（开 = v5 网关 `DELETE /api/vehicles/v5/{vin}/honk` 真实通道触发鸣笛，鸣笛约 30 秒自动停止、开关自动复位；关闭为本地复位——官方 App 停止鸣笛的 POST 加密信封尚未还原）；修复「灯光寻车」开关初始状态显示 `unknown`（默认关闭）；README 折叠历史版本
- **v3.0.4**：鸣笛寻车落地——中国区 `send-command` 网关白名单不含 `Honk`（HTTP 400 100502），已切换为 v5 网关真实通道（`DELETE /api/vehicles/v5/{vin}/honk`，实测返回 200 + commandId），按钮按下即走该通道下发；后续若拿到官方 App POST 鸣笛的加密信封，将升级为开始/停止双通道（实体不变）

- **v2.7.6**：修复用户名密码登录被 Azure AD B2C 风控拦截——登录请求（authorize → SelfAsserted → confirmed）改用同步 requests 客户端执行（实测 aiohttp 客户端会被 B2C 反自动化风控以 GlobalException 拦截，requests 客户端携带同样的 Cookie/CSRF/参数可正常通过）；csrf 与事务号（tx）优先从登录页 HTML 提取（与官方 WebView 一致）；提交凭证时手机号 `+` 正确 URL 编码；登录失败时区分"风控拦截"（提示等待后再试）与"凭证错误"；登录为一次性配置操作，在线程执行不阻塞事件循环
- **v2.7.5**：日志与稳定性优化——「车辆异常警示」接口连续失败（如 404）时自动降级：连续 2 次失败后 1 小时内不再请求该接口（避免每轮轮询发无效请求并刷日志噪音），接口恢复后自动重试；令牌自动刷新（HTTP 401 / Cat2 token expired）日志级别从 INFO 降为 DEBUG，减少轮询期噪音；「刷新车辆状态」按钮在命令发送失败时不再重复打印 `no commandId` 警告
- **v2.7.4**：新增用户名密码登录（配置流程可选两种登录方式：短信验证码 / 用户名密码；用户名密码走官方 Azure AD B2C 流程——authorize → SelfAsserted → confirmed 取授权码 → dlt-token-by-b2c-auth-code 换令牌，密码仅用于本次登录换取令牌、不持久化保存）；修复「车辆异常警示」activealert 接口持续 404——官方 App 该请求携带标准 `timestamp`+`sign` 签名，v2.7.4 起改用带签名的统一请求（并保留 vha 服务专用 appversion=1.0.0），接口恢复返回明文中文告警（如「胎压监测系统警告」）
- **v2.7.3**：修复福特网关 `600 Cat2 token expired`（`Swap token failed`）业务级 token 过期不刷新问题——`_request` 现在把 HTTP 200 但业务码 600 且含 token 错误的响应等同于 HTTP 401 处理：自动刷新一次 access token 并重试（实测刷新按钮流程完成后出现该错误，不加此修复后续轮询会持续拿到空数据）
- **v2.7.2**：所有车辆状态传感器新增福特原始数据属性——`timestamp`（该字段福特上报时间）、`source_status`（CURRENT/LAST_KNOWN）、`vehicle_data_time`（整份快照的 lastModifiedDate）；车辆定位传感器新增 `latitude`/`longitude`/`upload_time`/`address` 属性；车辆异常警示新增 `event_time`/`alerts`/`source` 属性；`send-command` 增加响应解密日志便于排查「刷新无 commandId」
- **v2.7.1**：刷新按钮改为与官方 App 一致的流程（经抓包验证）——`send-command` 返回 `commandId` 后，轮询 `command-execution-status`（每 2 秒，最长 60 秒）直到解密数据从占位（`LAST_KNOWN`/`01-01-0001`/`vin=null`）变为 `CURRENT` 真实快照，命令完成响应本身已含完整最新数据，直接推送实体；不再轮询 vehicle-status 时间戳（官方并不以此判断刷新完成）
- **v2.7.0**：修复「刷新车辆状态」按钮不生效的根因（HA `async_request_refresh` 受 `update_interval` 节流，距上次轮询不足 60 秒时会重新调度而不是立即刷新）——新增 `force_refresh()` 绕过节流直接拉数据并立即推送实体；删除「最后刷新时间」传感器实体（避免每轮轮询都刷新刷屏）
- **v2.6.9**：修复「车辆图片」实体在事件循环内的阻塞文件 IO——本地图片读取与持久化写入改由 executor 线程池执行（`hass.async_add_executor_job`），消除 `Detected blocking call to open ... image.py` 警告，事件循环不再被磁盘操作卡住
- **v2.6.8**：符合 HA 集成开发规范——manifest 补全 `integration_type: hub`、`requirements` 版本钉住（`pyelftools==0.29`）；hacs.json 补全 HACS 必填字段（`domains`、`iot_class`）；确认 `brand/`（logo/icon）、`data/`（白盒密钥库）、`translations/` 均在 `custom_components/fordpass_cn/` 规范目录下
- **v2.6.7**：修复「刷新车辆状态」按钮——`ForceRefresh` 只通知福特服务器从车机拉取最新数据，车机唤醒+数据回传需要数秒；此前点击后立即拉取拿到的是旧缓存，导致所有实体看起来"没刷新"。现在点击后自动轮询等待福特数据时间戳（`lastModifiedDate`）变化（最多 30 秒）再更新实体；命令失败不再阻断刷新；新增「最后刷新时间」传感器（显示最近一次成功获取车辆数据的时间，点刷新后有明确反馈）
- **v2.6.6**：车辆定位坐标从 WGS-84 转换为 GCJ-02（国测局加密坐标）——福特派 LBS 返回 GPS 原始坐标，直接画在国内高德/腾讯等地图底图上会偏移约 600 米；现在在集成源头转换，所有地图卡片（ha-map-card / 官方地图 / 高德卡片）均能精确定位车辆，无需在卡片层再做转换（卡片坐标体系请选 `gaode`）
- **v2.6.5**：「车辆异常警示」合并为单一实体（真实告警接口优先，接口不可用时回退 `PrmtAlarmEvent` 字段，无异常显示「无异常」）；「车辆图片」实体状态值显示车型名（如「锐际 Escape」），图片下载后持久保存到 `/config/www/fordpass_cn/` 不删除，重启无需重新下载
- **v2.6.4**：移除流量管理（影音娱乐剩余流量）传感器组及其选项配置；状态刷新间隔默认改为 60 秒；新增车辆图片实体（车型渲染图，如「锐际 Escape」）
- **v2.6.3**：接入真实告警接口（`/vha/activealert`）——车辆异常警示显示明文中文告警（如「胎压监测系统警告」）；新增流量管理传感器组（剩余流量/流量总量/已用流量/剩余比例/到期时间，影音娱乐 H5 后端，需在选项配置流量管理令牌）；报警状态映射更新（`SET`=车辆已设防，`NOTSET`=车辆未设防，`NOT_IN_ALARM`/`DISARMED`=解除报警，`ALARM`/`ARMED`=被盗声光报警中）
- **v2.6.1**：新增 21 个实体——车门（主驾/副驾/左后/右后/尾门/引擎盖）、点火状态、车窗（四门）、机油/蓄电池健康、胎压系统与四轮胎压状态、推荐胎压（自动换算 kPa）、远程启动时长、授权状态、远程控车功能、电池生命周期模式、出行状态；全部状态值中文化
- **v2.6.0**：校准车辆异常警示（真实字段 `PrmtAlarmEvent`，无异常时显示「无异常」）；修复报警状态 `SET` 映射（设防 = 车辆被盗）；移除空调滤芯、剩余流量传感器（`vehicle-status` 接口无对应数据源，避免长期 `unknown`）
- **v2.5.9**：新增传感器——车辆昵称、车辆识别码（VIN）
- **v2.5.8**：新增传感器——空调滤芯状态、车辆异常警示、剩余流量（影音娱乐）
- **v2.5.7**：修复「车辆定位追踪」开关无法即时生效——设备追踪实体始终创建，开关状态通过实体可用性即时反映（无需重启）
- **v2.5.6**：选项变更即时生效（无需重启 HA，扫描间隔 / 车辆定位开关实时应用）；README 添加 logo
- **v2.5.5**：brand 目录规范（logo/icon 移至 brand/）；车辆定位追踪默认开启
- **v2.5.4**：添加集成 logo / icon，更新仓库地址（github.com/RockJesus/ha-fordpass-cn），通过隐私信息检测
- **v2.5.3**：LBS 白盒加密初始化移出事件循环（asyncio.to_thread），消除 unicorn/读文件导致的阻塞警告
- **v2.5.2**：device_tracker 不再依赖易变的常量导入（SOURCE_TYPE_GPS 直接用 "gps"），彻底消除废弃别名警告与加载失败
- **v2.5.1**：修复新版 Home Assistant（2025+）中 device_tracker 旧导入路径导致的加载失败，兼容新旧版本
- **v2.5.0**：打通车辆定位全链路（LBS token 服务端签发 + 白盒加密 VIN + x-sign 签名 + 响应解密），HA 地图可显示车辆位置
- **v2.4.0**：LBS 签名与 VIN 加密破解，车辆定位实验版
- **v2.3.0**：实体中文化、车牌传感器、设备名=车型、令牌自动刷新修复
- **v2.1.0**：修复 refresh-dlt-token 请求体多余字段导致的 400 错误

</details>

# 欢迎来我的博客 Welcome to my blog: [![RJ](https://img.shields.io/badge/Rock-Jesus-purple.svg)](https://rockjesus.cn)

# ************ BUY ME A COFFEE 您的鼓励是我的荣幸 ************
| 支付宝打赏                                                                                                                                                              | 微信打赏                                                                                                                                                              |  微信赞赏                                                                                                                                                              | 
| ----------------------------------------------------------   | ----------------------------------------------------------   | ----------------------------------------------------------   |
| ![zfb](https://user-images.githubusercontent.com/23656651/111026777-3011dc80-8427-11eb-931e-8731a12cc3b4.jpeg) | ![wx](https://user-images.githubusercontent.com/23656651/111026785-3ef88f00-8427-11eb-9c0b-d773e2da067d.jpeg) | ![zsm](https://user-images.githubusercontent.com/23656651/111026828-9434a080-8427-11eb-809e-b67a010447ce.png) | 

## 免责声明

本项目仅供学习研究使用，使用过程中产生的任何账号风险、功能失效或法律问题，均由使用者自行承担。
