# FordPass China 福特派互联 Home Assistant 集成

![brand](custom_components/fordpass_cn/brand/logo.png)

[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![GitHub release](https://img.shields.io/github/v/release/RockJesus/ha-fordpass-cn.svg)](https://github.com/RockJesus/ha-fordpass-cn/releases)
[![HA](https://img.shields.io/badge/Home%20Assistant-2024.6%2B-blue.svg)](https://www.home-assistant.io)

Home Assistant 自定义集成，接入福特中国（长安福特）福特派互联服务：支持**手机号验证码 / 用户名密码**两种登录方式，远程控车、车辆状态监控、车辆定位追踪、预约出发/充电、家充桩管理、远程影像等全功能覆盖。

> ⚠️ 本项目为个人逆向研究作品，与福特官方无任何关联。使用本集成即表示同意自行承担相关风险与责任。

> ⚠️ 本集成包含逆向工程所得的私有二进制与密钥素材（`data/libwbsk_crypto_tool.so`、`data/whitebox_keys_for_prod.json`），仅用于个人研究；若相关权利方要求移除，作者将配合下架。密钥轮换后需发布新版本适配。

## 功能特性

- **双登录方式**：手机号短信验证码登录（无需密码）或用户名密码登录（Azure B2C 官方流程），配置时可自由选择。
- **双品牌支持**（v4.7.4）：登录第一步选择「福特派 FordPass」或「林肯之道 Lincoln Way」——福特派完整可用；林肯之道与福特派共用同一套云端加密体系（逆向确认白盒库包名绑定含林肯），但其 B2C 登录页/验证码端点/App 指纹待抓包补齐（当前选中会提示暂不可登录，抓包后下版本完整接入）。> 注意：用户名密码登录走微软 Azure AD B2C 网关，其风控可能拦截非浏览器自动化请求（返回 `AADB2C: An exception has occurred` 或 567）；如遇到此类失败，请改用短信验证码登录（登录后令牌自动续期，无需频繁重登）。
- **远程控车**：上锁 / 解锁 / 远程启动 / 远程熄火 / 鸣笛寻车 / 声光寻车 / 灯光寻车 / 刷新车辆状态
- **家充桩 BLE 自动认证**（充电车型）：授权后插枪即由桩与车 BLE 自动配对认证（手机不在场也能启动充电）
- **蓝牙域边界说明**：汽车 BLE 车控指令（一键制冷/制热、熄火、门锁、后备箱、车窗、寻车、前备箱、举升顶、车窗通风）与 PAAK 数字钥匙是**纯 BLE 物理链路**（需手机在车旁 10 米内 + 安全会话），云端无 HTTP 通道，HA 云集成无法执行——支持 BLE 的车型请用手机在车旁操作福特派 App。本集成云端可用的蓝牙域能力为：分车门锁定（IDUL）+ 家充桩 BLE 自动认证，均已接入。
- **车辆状态**：门锁、报警、燃油量、胎压、里程、车窗、车门等实时状态
- **车辆异常警示**：接入福特 vha/activealert 真实告警接口，明文中文告警显示（如「胎压监测系统警告」）
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

1. 在 [Releases](https://github.com/RockJesus/ha-fordpass-cn/releases) 下载最新版压缩包
2. 解压后将 `custom_components/fordpass_cn/` 整个目录复制到 HA 的 `/config/custom_components/` 下
3. 重启 Home Assistant

## 配置

1. 设置 → 设备与服务 → 添加集成 → 搜索「福特派互联」
2. 选择登录方式：**手机号验证码登录**（输入手机号 → 获取短信验证码 → 输入完成）或 **用户名密码登录**（输入福特派账号手机号与密码，密码仅用于本次登录换令牌、不会保存）
3. 按所选方式完成登录
4. 在集成选项（Options）中可开启「启用车辆定位追踪」、选择坐标体系（WGS-84 / GCJ-02）；状态刷新间隔为固定默认 30 分钟（v4.9.6 起不再用户可配置）

> 白盒加密内核为**纯 Python 查表实现**（v4.2.7 起由 `libwbsk_crypto_tool.so` 11 张导出表 100% 破译，零外部依赖），不再需要 `unicorn`/`pyelftools`，无需额外安装任何 Python 包。

## 支持的实体

| 类型 | 实体 | 说明 |
|---|---|---|
| device_tracker | 车辆定位 | GPS 坐标 + 地址属性，地图可显示 |
| image | 车辆图片 | 车型渲染图（如「锐际 Escape」） |
| image | 3D 车型图 | v3.5.0，`GET vds/v1/vehicle/search-3d-vehicle-model-url`，探测到 modelUrl 才创建（全车型自动适配） |
| lock | 车门锁 / 后备箱锁 / 前备箱锁 | 上锁 / 解锁；后备箱锁（TrunkUnlock，解锁弹开 / 随全车锁定，按车型尾门能力创建）；前备箱锁（v3.3.7，电马等车型——按云端命令白名单自动创建） |
| button | 手动刷新车辆状态 / 中央区灯光 / 辅助设置 / OTA 激活排程 / 重置空调滤芯 / 拖车灯光检测 / 遥控泊车 / 远程关窗 / 标记消息已读 / 开始行车记录 / 停止行车记录 | 手动刷新（请求车机上报→延时拉取最新状态）；中央区灯光/辅助设置/OTA 激活排程按车型能力创建（云端不支持的车型按下返回网关明确报错）；重置空调滤芯（`PUT /api/cnxapi-vds/v1/aar/status`）；拖车灯光检测（双后轮皮卡）；遥控泊车（v3.3.7，电马选装）、远程关窗（v3.3.7，支持车窗升降车型）——均按云端命令白名单自动创建；标记消息已读（v3.5.0，`POST messages/read`，有消息中心的车创建）；行车记录录制启停（v3.5.0，`POST pds/v1/start|stop-video-recording`，带记录仪硬件的车创建） |
| switch | 远程启动 / 灯光寻车 / 鸣笛寻车 / 声光寻车 / 哨兵模式 / 立即充电 / 车载冰箱 / 预约充电 | 远程启动/熄火；灯光寻车（ZoneLightingON/OFF，按车型能力创建）；鸣笛寻车（类型 0-3：开 = `POST /api/vehicles/v5/{vin}/honk`、关 = `DELETE` 同路径；类型「声光共舞」：开 = `send-command InitialVA`（cmdSpec VAType=4 + Duration）、关 = `send-command CancelVA`——HAR 实证 App 官方声光寻车通道，灯+喇叭同响）；鸣笛时长与类型按设置生效；哨兵模式（电马）、立即充电/停止（纯电/插混）、车载冰箱（猛禽/领裕）——v3.3.7 起按云端命令白名单自动创建；预约充电（v3.5.0，`POST departuretimes/toggleon|off`，预约出发数据探测到才创建） |
| select | 鸣笛持续时长 / 鸣笛类型 | 鸣笛寻车设置：持续时长 5/10/15/20 秒；鸣笛类型 雨落荷叶/急浪拍岸/汽笛长鸣/空谷回音/声光共舞（选择即保存，鸣笛时参数直达车机） |
| sensor | 车辆状态 | 门锁、报警、燃油、胎压、里程、车牌、自动熄火倒计时、车窗（未关闭显示程度/百分比）等 |
| sensor | 车辆异常警示 | 真实告警接口（胎压监测系统警告等），无异常显示「无异常」 |
| sensor | 保养计划 / 召回信息 / SIM 卡 / WiFi 热点 | 服务信息（v3.1.5，`GET maintenance-plan / recall / sim/info / wifi/status`，实测 200；数据无效或服务端业务失败时如实显示，不创建失败实体） |
| sensor | 空调滤芯状态 | 滤芯健康度 + 上次更换日期（v3.1.7，`GET /api/cnxapi-vds/v1/aar/status`，实测 200；App 6.16.0 新增功能，无 AAR 能力的车型不创建实体） |
| sensor | 车辆服务信息 | 云端能力位图 + 救援/客服/售后电话 + 电子说明书地址（v3.1.9，`GET /api/cnxapi-vds/v2/vehicles/ccfeatures`，实测 200；App 用于判断每辆车支持哪些功能的权威云端清单） |
| sensor | 机油寿命 / 剩余可行驶里程 / 慢漏气胎 / 预测性诊断 | 预测性诊断（v3.1.9，`GET /api/cnxapi-cds/prognostic/v1/list`，实测 200）：机油寿命百分比、按寿命剩余里程、慢漏气胎标识、诊断提示（featureType=OL 机油寿命族） |
| sensor | 鸣笛设置云端状态 | 福特账户云端 VehicleAnnouncementSetting 回读（类型 + 时长），App/其他设备改动可同步感知 |
| sensor | 车辆在线状态 / 车辆设备信息 / 车辆用户授权状态 / SRS 安全档案 / 传感器影子 / 行车记录视频 / 最新消息 / OTA 更新提醒 | v3.5.0，APK 6.16.0 端点补全（`onlinernr` / `pds/v1/device` / `search-vehicle-user-auth-status` / `search-srs-profile` / `sensor/shadow` / `search-video-file-url` / `messages/page` / `vehicle-reddot-status`）——登录后一次性探测，探测到数据才创建（全车型自动适配） |
| sensor | 车辆清单 / 消息中心 v2 / 车辆共享列表 / 车辆设备列表 | v3.5.1，账号级只读端点补全（`vds/v5/uservehicles` 完整车辆清单 / `messages/v2/page` / `search-vehicle-share-list` 共享状态 / `search-vehicle-device-list`）——探测到数据才创建 |

> 实体创建全部数据驱动：登录后按本车云端能力与车辆数据判断（多 VIN 账号下
> 每辆车独立创建自己支持的实体组），不支持的命令按下会返回网关明确报错，
> 不制造静默无效实体；刷新失败保留最后已知状态，0 unavailable。


## 版本历史

<details>
<summary>最近版本（完整历史见 <a href="https://github.com/RockJesus/ha-fordpass-cn/releases">GitHub Releases</a>）</summary>

- **v4.9.6**（2026-10-16）：**HA 规范合规整改三项**——① 移除用户可配置的刷新间隔（`scan_interval`，HA 官方规则"轮询间隔不可用户配置"），后台刷新固定默认 30 分钟；② 删除 `probe_cevs_variant` 调试服务（逆向调试残留，自动探测已覆盖）；③ README 大幅精简（删除历史版本日志，完整历史移至 GitHub Releases）。
- **v4.9.5**（2026-10-16）：HA 规范合规补全——新增 `diagnostics.py`（官方诊断通道，配置/选项/协调器状态脱敏输出，不含凭据）；新增根目录 `CODEOWNERS`；补 `async_migrate_entry`（官方迁移钩子）；README 最低版本徽章对齐 hacs.json（2024.6+）。
- **v4.9.4**（2026-10-16）：HA 官方发布规范合规补全——manifest 增加 `quality_scale: bronze`；ConfigFlow VERSION 核对合规。
- **v4.9.3**（2026-10-16）：所有实体按车型能力创建——纯电车型（fuelType=E/BEV）不再创建燃油量/续航里程/机油健康/机油寿命实体；远程启动开关改为按 remoteStartStatus 数据或云端 RemoteStart 白名单创建。
- **v4.9.2**（2026-10-16）：清理集成 data/ 目录无用文件 libwbsk_crypto_tool.so（v4.2.7 白盒 AES 纯 Python 化后零 .so 依赖）。
- **v4.9.1**（2026-10-16）：车辆定位传感器 lat/lon 属性数值化（便于自动化比较）。

</details>

# 欢迎来我的博客 Welcome to my blog: [![RJ](https://img.shields.io/badge/Rock-Jesus-purple.svg)](https://rockjesus.cn)

# ************ BUY ME A COFFEE 您的鼓励是我的荣幸 ************
| 支付宝打赏                                                                                                                                                              | 微信打赏                                                                                                                                                              |  微信赞赏                                                                                                                                                              | 
| ----------------------------------------------------------   | ----------------------------------------------------------   | ----------------------------------------------------------   |
| ![zfb](https://user-images.githubusercontent.com/23656651/111026777-3011dc80-8427-11eb-931e-8731a12cc3b4.jpeg) | ![wx](https://user-images.githubusercontent.com/23656651/111026785-3ef88f00-8427-11eb-9c0b-d773e2da067d.jpeg) | ![zsm](https://user-images.githubusercontent.com/23656651/111026828-9434a080-8427-11eb-809e-b67a010447ce.png) | 

## 免责声明

本项目仅供学习研究使用，使用过程中产生的任何账号风险、功能失效或法律问题，均由使用者自行承担。
