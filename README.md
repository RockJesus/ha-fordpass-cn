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

1. 下载最新版 Release（`fordpass_cn_3.4.3.zip`）
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
| lock | 车门锁 / 后备箱锁 / 前备箱锁 | 上锁 / 解锁；后备箱锁（TrunkUnlock，解锁弹开 / 随全车锁定，按车型尾门能力创建）；前备箱锁（v3.3.7，电马等车型——按云端命令白名单自动创建） |
| button | 手动刷新车辆状态 / 中央区灯光 / 辅助设置 / OTA 激活排程 / 重置空调滤芯 / 拖车灯光检测 / 遥控泊车 / 远程关窗 | 手动刷新（请求车机上报→延时拉取最新状态）；中央区灯光/辅助设置/OTA 激活排程按车型能力创建（云端不支持的车型按下返回网关明确报错）；重置空调滤芯（`PUT /api/cnxapi-vds/v1/aar/status`）；拖车灯光检测（双后轮皮卡）；遥控泊车（v3.3.7，电马选装）、远程关窗（v3.3.7，支持车窗升降车型）——均按云端命令白名单自动创建 |
| switch | 远程启动 / 灯光寻车 / 鸣笛寻车 / 声光寻车 / 哨兵模式 / 立即充电 / 车载冰箱 | 远程启动/熄火；灯光寻车（ZoneLightingON/OFF，按车型能力创建）；鸣笛寻车（类型 0-3：开 = `POST /api/vehicles/v5/{vin}/honk`、关 = `DELETE` 同路径；类型「声光共舞」：开 = `send-command InitialVA`（cmdSpec VAType=4 + Duration）、关 = `send-command CancelVA`——HAR 实证 App 官方声光寻车通道，灯+喇叭同响）；鸣笛时长与类型按设置生效；哨兵模式（电马）、立即充电/停止（纯电/插混）、车载冰箱（猛禽/领裕）——v3.3.7 起按云端命令白名单自动创建，车型支持即出现 |
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

- **v3.4.7**：**全车型兼容性征集启动**——新增 GitHub issue 模板「车型兼容性报告」（`.github/ISSUE_TEMPLATE/ford-model-compatibility.md`，标签 `compatibility`）：车主提交车型/年款/实体缺失项/控制实测/脱敏日志即可参与全车型适配（**无需提供 VIN、账号密码、精确位置**，登录与操作均在车主本人设备完成）；本版为文档与模板更新，无代码功能变更；版本号十进制 +1（3.4.6→3.4.7）
**接入高价值只读端点（OTA 详情/预约出发/充电日志，探测数据驱动）**——登录后一次只读幂等探测（24h TTL 缓存，不进入常规轮询，防福特云限流）：① **OTA 版本/更新详情**（alert 族 ota/versions、ota/detail、search-ota-details、search-new-ota-status——APK 6.16.0 逆向）→ 新增「OTA 新版本状态」「OTA 版本」「OTA 更新详情」实体（最新版本号/更新内容/状态）；② **预约出发**（cevs departuretimes/retrieve，v3.3.7 已探测，本次解析为「预约出发」实体——下次出发时间/任务数）；③ **充电日志**（cevs chargelogs/retrieve）→ 「充电日志」实体；④ 家充桩探测族并入 charging/records/process/log/v3（充电过程日志，只读）。全部探测到数据才创建，非能力车型/无数据 0 实体（全车型适配，0 unavailable）；**写操作端点**（家充桩开始/停止充电、设默认桩、预约出发保存/开关、远程录像）请求参数需 HAR 校准，待抓包后下版接入（不编造参数）；版本号十进制 +1（3.4.5→3.4.6）
**接入家充桩管理（smartwallbox，全车型能力驱动）**——6.16.0 APK 逆向还原福特派「家充桩管理」云端服务（/api/smartwallbox/processor/ 家族：binding/v5 绑定桩列表、charging/status/wallboxId/v2 充电状态、records/single/list/v2r 充电记录、config/common/query/v2 通用配置，能力位 0x8 privateChargingService / 0xA WallBoxAutoAuth 车型）；登录后**只读幂等探测**一次（24h TTL 缓存，绝不进入常规轮询，防福特云限流），探测到数据才创建实体：**家充桩数量**（绑定桩列表数）、**默认充电桩**（品牌+型号+序列号/ MAC）、**最近充电记录**（充电时长等）、**家充桩充电状态**——无家充桩/纯油车型探测 404 无数据 → 0 实体（全车型自动适配，0 unavailable）；字段宽松递归提取（各车型响应结构差异兼容），探测响应结构与请求形态（带/不带 encryptedVin）INFO 日志输出便于校准；版本号十进制 +1（3.4.4→3.4.5）
**接入远程影像/停车图 + 远程空调目标温度（全车型能力驱动）**——① 「停车影像」「行车监控」查询按钮按下后，新增「**停车影像图**」「**行车监控图**」image 图片实体展示最新照片（有远程影像硬件的车型自动创建，encryptedCarId 非空判断；图片 URL 宽松提取 imageUrl/picUrl/photoUrl/url 并持久化保留最后一张，无影像时显示「暂无影像」不丢图；锐际无远程影像硬件不创建，0 unavailable）；② 「**远程空调目标温度**」select 实体（16-30°C 步进 1，默认 24°C）——选择即上传福特账户云端（UserPreferenceV2 候选偏好通道 RemoteClimateSetting/TargetTemp，与鸣笛设置同构）并回读云端实现 App/HA/云端三方同步，实体创建条件=车辆状态 preCondStatusDsply 有值（纯电/插混车型登录自动出现，锐际纯油无此功能不创建，0 unavailable）；③ hacs.json 启用 zip_release（HACS 从 release asset 下载发布包，发布链路稳定）；版本号十进制 +1（3.4.3→3.4.4）
**车辆定位保留最后已知状态**——LBS 定位偶发失败时，定位传感器（车辆定位）显示最后一次成功地址/坐标（不再回落"定位不可用"），定位实体（device_tracker）也保留上一轮有效坐标；首次成功获取前才显示"定位不可用"
- **v3.4.2**：**修复 TTL 缓存协程泄漏警告**——`_cached_fetch` 改为接收协程工厂（仅在需要请求时才创建协程），消除 v3.4.1 每次刷新命中缓存时产生的 `coroutine was never awaited` RuntimeWarning（HAOS 日志噪音清零）；功能不变
- **v3.4.1**：**防福特云限流优化**——给慢变数据加 TTL 缓存，只有车辆状态（vehicle-status）每轮拉取：告警 30 分钟、未读消息 30 分钟、定位 15 分钟、WiFi 15 分钟、机油寿命/空调滤芯/OTA/SIM 6 小时、保养计划 12 小时、召回/能力位图 24 小时、鸣笛偏好 1 小时；TTL 内直接复用上次数据不发请求，请求失败保留最后已知状态。刷新间隔即使调小（如 5 分钟），每轮请求数从约 12 个降至 2-3 个，大幅降低限流风险
- **v3.4.0**：**出行状态映射修正 / 手动刷新延时 30 秒 / 机油寿命值扩展**——①「出行状态」映射对调修正：`PwPckOffTqNotAvailable`（停车扭矩不可用=已驻车）=**已泊车**、`Available`/「不可用」=**外出中**；②「手动刷新车辆状态」车机上报→拉取最新状态中间延时由 3 秒改为 **30 秒**（与 App 下拉刷新节奏一致，避免拉到旧数据）；③「机油寿命」值改为**百分比+归零年月份+剩余公里**组合（如「40% · 归零 2028-03 · 剩余 12000 km」）
- **v3.3.9**：**图片改名+新增俯视车型图+车辆信息；修复柴油/出行状态/机油寿命重复**——①「车辆图片」改为「**侧视车型图**」；②新增「**俯视车型图**」图片实体（值=`imageUrl`，birdview 俯视，HAR 实测字段，独立持久化不覆盖侧视图）；③新增「**车辆信息**」传感器（值=`jointVenture+localMarketValue+modelYear+vehicleType+fuelType`，如「长安福特 · 锐际 Escape · 2020 · SYNC + · 汽油」，各字段同步进属性）；④**汽油车不再创建柴油实体**（按 `fuelType=D` 判定——锐际此前误显示「柴油系统状态=激活」已修复）；⑤「出行状态」映射修正：`PwPckOffTqNotAvailable`=**外出中**、`Available`/「不可用」=**已泊车**；⑥「机油寿命」重复实体去重（仅保留 prognostic 版，含归零月份/剩余公里属性）；⑦「剩余可行驶里程」仅在 `remainingKMs` 有值时创建（无数据不再显示 unknown）
- **v3.3.8**：**A 类端点探测日志升级（INFO 级，HAOS 日志可见）**——「保养历史」「预约出发」端点响应结构探测由 DEBUG 改为 INFO 输出，重启后可在 HAOS 日志直接查看真实响应（此前 DEBUG 级用户看不到）；拿到响应结构后即可解析为传感器实体（下版）
- **v3.3.7**：**A/B 全量实体接入（云端命令白名单驱动，全车型自动适配）**——集成启动时探测福特网关 `send-command` 白名单（发送非法命令读取服务器枚举的合法命令集，幂等安全不触发车辆动作），按白名单自动创建车型专属实体：**哨兵模式**（电马）、**立即充电/停止**（纯电/插混）、**车载冰箱**（猛禽/领裕）开关，**前备箱锁**（电马）、**遥控泊车**（电马选装）、**远程关窗**（支持车窗升降车型）——各车型登录即自动出现自己支持的功能，不支持的命令按下返回网关明确报错；同时探测保养历史（`maintenance-history`）/预约出发（`departuretimes`）端点响应结构（日志输出，供后续版本解析接入）；A 类「质保」（`warranty`）服务端参数校验 100502 未还原暂不接入（需抓包补充）
- **v3.3.6**：**寻车开关自动复位跟随「鸣笛持续时长」**——「声光寻车」「鸣笛寻车」开关触发后的自动复位时间由固定 30 秒改为按本次设置时长（缺失/非法时回退 30 秒），开关状态与车辆实际鸣笛停止时间保持一致
- **v3.3.5**：**修复重启后集成加载失败**——v3.3.3 的「最后已知状态恢复」在持久化数据首次生效后触发 `AttributeError`（coordinator 日志属性笔误 `self._log` → `self.logger`），导致重启后 fordpass_cn 无法 setup、实体消失；已修复并重新发布。此问题仅在「有历史持久化数据 + 重启」时出现，v3.3.4 部署时的 setup 报错即由此引起
- **v3.3.4**：**设置即时上云 + 刷新合并**——③「鸣笛类型」「鸣笛持续时长」选择后**立即上传福特账户云端**（UserPreferenceV2 保存与 App 逐字一致，回读确认同步），实现 App / HA / 云端三方同步；「保存鸣笛设置」按钮已删除（不再需要手动保存）；④「请求车机刷新状态」「手动拉取最新状态」合并为**「手动刷新车辆状态」**——先请求车机上报云端，延时 3 秒（车机上报+云端落盘）后拉取最新状态并刷新实体；① 尾门自动打开/关闭：中国区福特派云端**无电动尾门开/关命令**（send-command 白名单14 项仅 TrunkUnlock，App 逆向亦无尾门控制通道），远程控制暂不可行——但「尾门/内尾门/引擎盖」**状态传感器**已提供（doorStatus 轮询，与 App 同源）；② brand 品牌图已按官方规范（icon 256×256、logo 最短边 128-256）；⑤ 实体云端同步机制已梳理（传感器=云端轮询同源、控制命令=云端回读、开关触发态=本地，详见集成说明）
- **v3.3.3**：**5 项体验修复**——①「鸣笛设置云端状态」自动同步到「鸣笛持续时长 / 鸣笛类型」（云端 AnnouncementType/Duration 变化时 select 自动跟随并持久化，App/其他设备改动 HA 立即同步）；②「声光寻车触发（鸣笛+灯光）」「声光寻车取消」两按钮合并为 switch「声光寻车」（开 = send-command InitialVA（VAType=4+设置时长）、关 = CancelVA，与 App 官方通道逐字一致；命令执行结果写入「鸣笛命令状态」传感器——若车型/车机对取消命令无响应属网关行为，可查该传感器诊断）；③「未读消息」状态改显示最新消息主题 `readMessageSubject`；④ 车窗百分比枚举解析（`BetFully_10PercentOpen` →「未关闭 10%」，与数值型显示统一）；⑤ **最后已知状态持久化**（HA Store）——重启/重载后实体直接用最后可用数据创建，不再闪「不可用/未知」，首次刷新失败不阻塞加载（后台自动重试，云端恢复后更新）。注：远程关闭车窗——中国区 send-command 网关白名单（2026-09-29 服务器返回的 14 项权威清单）无任何车窗命令，App 亦无远程关窗入口，暂无法接入；后续若网关开放将补上
- **v3.3.2**：**修复 HAOS 集成 0 实体与轮询报错**——① 所有请求显式超时 60s（HA 默认 aiohttp session timeout 仅 ~10s，福特网关尤其 ForceRefresh 后立即拉取 vehicle-status 时偶发慢响应被截断，coordinator 每轮刷 `FordPass update timed out` ERROR 且数据取不到，实体创建被卡）；② 修复后配合 HAOS 重装（HACS 全量替换），确认 83 个已注册实体（锁/开关/按钮/传感器）恢复创建，0 unavailable
- **v3.3.1**：**严格按照 HA 官方集成发布规范修复（为申请加入 HACS 默认仓库）**
  - `manifest.json`：移除官方规范不存在的 `homeassistant` 键（自定义集成最低版本由 `hacs.json` 的 `homeassistant` 键声明）；键序对齐官方（`domain`、`name` 在前，其余键按字母序）
  - `hacs.json`：移除 HACS Action 校验不接受的 `domains` / `iot_class` 键
  - 新增官方 CI 工作流 `.github/workflows/hassfest.yaml` + `hacs.yaml`（push 自动跑 hassfest + HACS Action 两项校验）
  - 补齐官方必填 `translations/en.json`（英文文案，与 strings.json 键结构一致）
  - `brand/` 品牌图标尺寸合规：`icon.png` 128→**256×256**、`logo.png` 512→**256×256**（官方品牌规范：icon 256×256、logo 最短边 128-256；本地 brand 图优先于 CDN，无需再向 brands 仓库提交 PR）
- **v3.3.0**：**手机号密码（B2C）登录对齐福特派 6.16.0 + EdgeOne WAF 诊断**——对照 HAR 实测将 B2C 登录四步全部对齐 App 真实请求（WebView UA Android 12、`x-requested-with: com.ford.fordpasscn`、完整 sec-fetch 头、authorize 补 `tnc_consent1_accepted=true&tnc_consent2_accepted=false`、confirmed 带 diags）；福特登录页已前置**腾讯云 EdgeOne 安全防护**，实测纯 HTTP 客户端（requests/curl_cffi 全指纹/Chrome headless）均被 567 拦截——WAF 策略变严所致非代码问题，现自动识别拦截页并给出明确提示（改用短信验证码登录或稍后重试），App 登录不受影响；待 WAF 放宽后密码登录参数已就绪可直接生效
- **v3.2.6**：**SIM/WiFi「未开通」判定修正**——业务失败响应的错误码（`returnErrCode=402/401`）与错误描述（`returnErrMsg=CONS.SYS.0002/CONS.AUTH.0005`）均在业务层给出；v3.2.5 只认 `CONS.` 前缀错误码，实际错误码是数字（402/401），导致仍显示「查询失败（402/401）」。现任一字段命中 `CONS.` 前缀或错误码为 `401/402/403/404` 即显示「**车辆未开通该服务**」，错误码/描述保留在属性
- **v3.2.5**：**传感器数据对齐 HAR 实证**——全量对比福特派 6.16.0 抓包（1152 条）与集成解析：vehicle-status（R3 解密体）/vha 主动告警/空调滤芯/未读消息/保养计划/召回/预测性诊断（机油寿命·剩余里程·慢漏气）/云端能力/偏好 全部字段一致（里程 59258、油量 49.67%、胎压 222/227/239/247 kPa、机油寿命 41% 等逐项核对）；两处修正——①「保养计划」由只显示首项改为显示**完整首档全部项目 + 档数**（App 展示全部里程档）；②「SIM 卡」「WiFi 热点」服务端返回业务失败（`CONS.SYS.0002`/`CONS.AUTH.0005`）时语义由「查询失败（错误码）」改为「**车辆未开通该服务**」——锐际纯油无车联网 SIM/热点服务、App 无该功能入口（HAR 无任何 sim/wifi 请求），错误码保留在属性；`sensor/shadow` 判定为订阅接口（返回 shadowId 走推送通道）、`onlinernr` 为账号实名认证状态，均不接入
- **v3.2.4**：**实体精简与显示优化**——「鸣笛持续时长」选项显示加单位（`5 秒/10 秒/15 秒/20 秒`，持久化仍存数字）；删除「车辆服务信息」「车辆能力清单」两个传感器（含停用 cvfeatures v4 拉取，消除每次刷新的 404 请求）；全平台实体 available 恒 True（保留最后已知状态，0 unavailable）
- **v3.2.3**：**鸣笛设置同步修复**——保存按钮/鸣笛开关改读 config entry options（select 每次选择即持久化的权威值），修复 HA 重启后与内存 honk_settings 分裂导致「保存按钮存默认值、鸣笛用默认类型/时长」的问题（v3.2.2 实测：select 显示声光共舞/15 秒，保存后云端却写汽笛长鸣/10 秒——根因即此）
- **v3.2.2**：**鸣笛设置保存修复**——HAR 实证 App 保存写入 `AnnouncementType`（值=枚举数字 `0-4`）+ `Duration`（值=`5-20`）；集成此前误写旧槽位 `vehicleAnnouncementSoundType`（枚举名）/`vehicleAnnouncementDuration`（App 新逻辑不读取，导致「更改类型/时长保存无效」）。保存按钮与「鸣笛设置云端状态」传感器改读写 AnnouncementType + Duration（与 App 逐字一致），旧槽位仅作回读兜底（枚举名自动转中文）
- **v3.2.1**：**声光共舞修复**——鸣笛寻车开关在类型为「声光共舞」时不再走 V5 `/panic/{duration}`（中国区网关 404），改走 **send-command `InitialVA`**（cmdSpec `VAType=4`+`Duration`，HAR 实测 200 + commandId，App 官方声光寻车通道：灯+喇叭同响），关闭改走 `send-command CancelVA`；命令结果经 `command-execution-status` 轮询写入「鸣笛命令状态」传感器。同时修正误标：`InitialVA`/`CancelVA` 实为鸣笛通告通道（FORD_HONK/FORD_HONK_CANCEL，非语音助手），「语音助手初始化/取消」按钮重命名为「声光寻车触发（鸣笛+灯光）/取消」
- **v3.2.0**：**版本号规则修正**——按十进制、每段不超过 9、超 9 前一位 +1（如 2.1.9 → 2.2.0）回归合规版本轨道；功能与 v3.1.20 一致（云端能力探测等）
- **v3.1.20**：新增**云端能力探测**传感器（全车型自动创建的关键）——把 ccfeatures `availableFeatures` 位图（VDSFeatureType 24 项枚举，逆向自福特派 6.16.0 libapp.so）解析为该车开通的云端服务能力集，状态直接显示「已开通 N 项：计划保养服务、指南、道路救援…」，属性含 capabilities 中文列表 / feature_ids 原始位图 / 逐项 capability_XX（如锐际纯油 `03,04,05,06,07,08,10,11,23` = 计划保养服务/指南/道路救援/延保/福特金融/私充服务/我的订阅/我的试驾 + 未定义特性 0x23）；解码表移入 `capability.py`（`parse_cloud_features`）供全平台复用——不同车型登录后按各自位图自动创建，无位图数据的车型不创建，0 unavailable
- **v3.1.19**：`capability_v4`（cvfeatures v4）拉取失败日志降级——该路径经 v3.1.14-16 多轮 GET/POST 实测均 404（中国区网关无此路由，App 当前版本未实际调用），为预期状态，不再每次刷新刷 WARNING（降为 DEBUG）；「车辆能力清单」传感器仍如实显示获取失败原因，0 unavailable——能力数据以已生效的 ccfeatures（v2）位图 + VDSFeatureType 解码为准
- **v3.1.18**：修复 v3.1.17 未读消息传感器未创建问题——`api.py` 缺失 `PATH_MESSAGES_SUMMARY` 导入导致拉取时 NameError（coordinator 捕获后 data 置 None、实体不创建）——补齐导入后 messages/summary 正常拉取（HAR + 实机 200：allRedDotStatus=1 有未读），「未读消息」传感器按数据创建，0 unavailable
- **v3.1.17**：语音助手按钮补齐 cmdSpec + 新增未读消息传感器——① **InitialVA/CancelVA 修复**：HAR 实测（2026-10-03）确认福特派真实请求带 `cmdSpec`（InitialVA = VAType:4 + Duration:15、CancelVA = VAType:4），此前集成只发 commandType 导致网关 `228205 cmdSpec can not empty` / 部分车型无响应——`send_command` 新增可选 cmdSpec 参数，语音助手按钮按福特派真实参数发送；② **未读消息传感器**：`GET /api/cnxapi-message/app/messages/summary`（HAR 实测 200，仅 timestamp+sign 签名）——显示「有/无未读消息」+ 属性含未读描述 / 最新消息主题 / 未读分类列表；无该数据的车型不创建，0 unavailable
- **v3.1.15**：cvfeatures v4 端点请求修正——v3.1.14 用 `appKey/appVersion/clientType` query 实测 404（Resource not found），本次改用 `_get_signed`（encryptedVin/xjw query + R3 签名，与 ccfeatures(v2) 同通道）重新实测；「车辆能力清单」传感器如实显示结果（成功=能力名列表，失败=错误原因，0 unavailable）
- **v3.1.14**：「车辆能力清单」传感器增强——任何 dict 响应都创建实体（0 unavailable），获取失败时如实显示错误原因（如 `获取失败：404 Not Found...`），成功但结构未识别时显示「已获取（结构待车型实测解析）」+ 原始响应属性——本轮 cvfeatures v4 端点实测诊断用（此前 404 疑缺完整 query，实体属性可查实际响应/错误）
- **v3.1.13**：**鸣笛命令状态传感器**：逆向确认 App 的 `GET /api/vehicles/v5/{vin}/announcestatus/{commandId}/`（v5 命令轮询组，与 statusrefresh/{commandId} 并列），鸣笛开关触发后自动轮询 announcestatus 三次（2s/5s/10s），结果写入「鸣笛命令状态」传感器（未触发/执行中/网关状态/查询失败，永远可用不显示 unavailable）；② **停车影像 / 行车监控**：逆向确认 App `VehicleManagerEndpoint.searchVehicleParkingImage / searchVehicleMonitorTraffic`（PDS 网关），请求体含 `carId`=车辆列表 `encryptedCarId`——非空才创建按钮（锐际 encryptedCarId=null = 无远程影像硬件，不创建、0 unavailable）；③ **authedFeatures 双解释**：`A4=0xA4=164=10100100₂` 逐位拆解为 VDSFeatureType 特性名（bit2 计划保养服务 / bit5 道路救援 / bit7 福特金融，待多车型验证）；④ **车辆能力清单 v4**：逆向确认 `GET /api/cnxapi-vds/v4/vehicles/cvfeatures`（App `VcsRepositoryProvider::fetchCapabilityV4`，首页能力卡片 PAAK/EV 管理/哨兵/灯光等的权威来源）——接入「车辆能力清单」传感器；此前 404 疑缺完整 query，本次接入后实测
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
