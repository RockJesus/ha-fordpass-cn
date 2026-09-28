# FordPass China 福特派互联

![FordPass China](custom_components/fordpass_cn/brand/logo.png)

Home Assistant 自定义集成，接入福特中国（长安福特）福特派互联服务，支持短信验证码登录、远程控车与车辆定位追踪。

> ⚠️ 本项目为个人逆向研究作品，与福特官方无任何关联。使用本集成即表示同意自行承担相关风险与责任。

## 功能特性

- **手机号短信验证码登录**：无需密码，验证码直登
- **远程控车**：上锁 / 解锁 / 远程启动 / 远程熄火 / 鸣笛 / 报警 / 刷新车辆状态
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

1. 下载最新版 Release（`fordpass_cn_2.7.3.zip`）
2. 解压后将 `custom_components/fordpass_cn/` 整个目录复制到 HA 的 `/config/custom_components/` 下
3. 重启 Home Assistant

## 配置

1. 设置 → 设备与服务 → 添加集成 → 搜索「福特派互联」
2. 输入注册福特派的手机号
3. 获取短信验证码并输入，完成登录
4. 在集成选项（Options）中可开启「启用车辆定位追踪」

> 依赖 `unicorn`（ARM64 白盒 AES 仿真）与 `pyelftools`，HA 首次加载时会自动安装。

## 支持的实体

| 类型 | 实体 | 说明 |
|---|---|---|
| device_tracker | 车辆定位 | GPS 坐标 + 地址属性，地图可显示 |
| image | 车辆图片 | 车型渲染图（如「锐际 Escape」） |
| lock | 车门锁 | 上锁 / 解锁 |
| button | 远程启动 / 熄火 | 远程启动引擎 |
| switch | 远程启动状态 | 已远程启动 / 未远程启动 |
| sensor | 车辆状态 | 门锁、报警、燃油、胎压、里程、车牌等 |
| sensor | 车辆异常警示 | 真实告警接口（胎压监测系统警告等），无异常显示「无异常」 |

## 版本历史

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

## 免责声明

本项目仅供学习研究使用，使用过程中产生的任何账号风险、功能失效或法律问题，均由使用者自行承担。
