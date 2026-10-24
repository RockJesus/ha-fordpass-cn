# FordPass China 福特派互联 Home Assistant 集成

<div align="center">

![FordPass China 福特派互联](custom_components/fordpass_cn/brand/logo.png)

[![HACS 默认仓库](https://img.shields.io/badge/HACS-%E9%BB%98%E8%AE%A4%E4%BB%93%E5%BA%93-41BDF5.svg?style=flat-square)](https://github.com/hacs/default)
[![最新版本](https://img.shields.io/github/v/release/RockJesus/ha-fordpass-cn?style=flat-square)](https://github.com/RockJesus/ha-fordpass-cn/releases/latest)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.6%2B-blue?style=flat-square)](https://www.home-assistant.io/)
[![许可证](https://img.shields.io/github/license/RockJesus/ha-fordpass-cn?style=flat-square)](LICENSE)

</div>

> **当前中国区唯一活跃维护的福特派 / 林肯之道 Home Assistant 集成**——全车型能力自适应，远程控车、状态监控、EVCC 联动一站式。

✍️ 欢迎来我的 blog：[![Blog](https://img.shields.io/badge/Blog-RockJesus-purple.svg)](https://rockjesus.cn)

---

<details>
<summary>English summary</summary>

Custom Home Assistant integration for **FordPass China (Changan Ford) / Lincoln Way (China)** cloud services. Unofficial, reverse-engineered from the FordPass CN / Lincoln Way mobile app.

- **Remote control** — lock / unlock / remote start / stop / honk / lights / manual refresh
- **Vehicle status** — fuel & range, odometer, tire pressure, doors & windows, ignition, location tracking, alerts
- **Smart features** — departure scheduling & charging (EV/PHEV/HEV), wallbox management, remote image, OTA status, maintenance plan, recall info
- **Per-vehicle entities** — entities are created per vehicle capability (fuel type, cloud feature bitmap), no useless entities
- **Two brands** — FordPass CN and Lincoln Way share the same cloud encryption; brand is selected at first login step
- **Two login methods** — SMS passcode or username/password (Azure B2C)

**Installation**: add `https://github.com/RockJesus/ha-fordpass-cn` via **HACS → ⋯ → Custom repositories** (category: Integration), download & restart, then add integration "FordPass China 福特派互联".

> Disclaimer: This is an independent, non-official project for research and personal use only. Use it at your own risk. No affiliation with Ford Motor Company.

</details>

---

Home Assistant 自定义集成，接入福特中国（长安福特）福特派互联服务：支持**手机号验证码 / 用户名密码**两种登录方式，覆盖远程控车、车辆状态监控、定位追踪、预约出发/充电、家充桩管理、远程影像等全功能。兼容 Home Assistant 2024.6 及以上版本（已实测至 2026.10.2）。

> ⚠️ 本项目为个人开发作品，与福特官方无任何关联。使用本集成即表示同意自行承担相关风险与责任。

## ✨ 功能特性

**🔑 登录与品牌**
- 双登录方式：手机号短信验证码（无需密码）或用户名密码（Azure B2C 官方流程），配置时可自由选择
- 双品牌支持（v4.7.4）：登录第一步选择「福特派 FordPass」或「林肯之道 Lincoln Way」——共用同一套云端加密体系；林肯之道 B2C 登录端点待抓包补齐，当前选中会提示暂不可登录

**🔐 远程控车**
- 上锁 / 解锁 / 远程启动 / 远程熄火 / 鸣笛寻车 / 声光寻车 / 灯光寻车 / 手动刷新车辆状态
- 鸣笛持续时长与类型可选（雨落荷叶 / 急浪拍岸 / 汽笛长鸣 / 空谷回音 / 声光共舞），选择即云端保存、鸣笛参数直达车机
- 拖车灯光检测（皮卡）按云端命令白名单自动创建
- > v5.2.4：分车门锁定、遥控泊车、远程关窗、哨兵模式、立即充电、车载冰箱、前备箱锁已移除——命令载荷未对照官方 App 抓包确认，审核要求不得下发猜测命令，待抓包确认后恢复

**⚡ 充电与能源（纯电/插混/混动车型）**
- 预约出发 / 预约充电 / 立即充电 / 停止充电
- 家充桩 BLE 自动认证：授权后插枪即由桩与车 BLE 自动配对认证（手机不在场也能启动充电）

**🚗 车辆状态**
- 门锁、报警、燃油、胎压、里程、车窗、车门、自动熄火倒计时等实时状态
- 车辆异常警示：真实告警接口明文中文显示（如「胎压监测系统警告」）
- 保养计划 / 召回信息 / SIM 卡 / WiFi 热点 / 空调滤芯 / 机油寿命 / 慢漏气胎 / 预测性诊断
- 车辆服务信息：云端能力位图 + 救援/客服/售后电话 + 电子说明书
- 自动令牌刷新：访问令牌过期自动刷新，无需重新登录

**📍 定位与影像**
- GPS 车辆定位追踪（设备追踪器，HA 地图可显示，可选 WGS-84 / GCJ-02 坐标体系）
- 车辆图片 / 3D 车型图（持久保存不删除）
- 行车记录视频录制启停（带记录仪硬件的车型）

**能力自适应**
- 实体全部数据驱动：登录后按本车云端能力与车辆数据判断，**多 VIN 账号每辆车独立创建自己支持的实体组**
- 不支持的命令按下会返回网关明确报错，不制造静默无效实体
- 刷新失败保留最后已知状态，0 unavailable

> 蓝牙域边界：汽车 BLE 车控指令（一键制冷/制热、熄火、门锁、后备箱、车窗、寻车、前备箱、举升顶、车窗通风）与 PAAK 数字钥匙为**纯 BLE 物理链路**（需手机在车旁 10 米内 + 安全会话），云端无 HTTP 通道，HA 云集成无法执行——支持 BLE 的车型请用手机在车旁操作福特派 App。本集成云端可用的蓝牙域能力（家充桩 BLE 自动认证）已接入（分车门锁定在 v5.2.4 因命令载荷未确认移除）。

## 📦 安装方法

### 方式一：通过 HACS 安装（推荐）

本集成已收录于 **HACS 官方默认仓库**，点击下方按钮即可在你的 Home Assistant 中直接打开 HACS 并一键安装：

[![在 HACS 中打开](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=RockJesus&repository=ha-fordpass-cn&category=integration)

> 按钮无法使用？也可手动操作：HACS → 集成 → 搜索「**福特派互联**」→ 下载 → 重启 Home Assistant。

### 方式二：手动安装

1. 在 [Releases](https://github.com/RockJesus/ha-fordpass-cn/releases) 下载最新版压缩包
2. 解压后将 `custom_components/fordpass_cn/` 整个目录复制到 HA 的 `/config/custom_components/` 下
3. 重启 Home Assistant

### ⚙️ 环境要求

- Home Assistant **2024.6 及以上**（Config Flow UI 配置）
- 纯 Python 实现，**零外部依赖**，无需安装任何额外 Python 包

## 🚀 配置步骤

<div align="center">

[![开始配置](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=fordpass_cn)
[![打开设备与服务](https://my.home-assistant.io/badges/integrations.svg)](https://my.home-assistant.io/redirect/integrations/)

</div>

1. 设置 → 设备与服务 → 添加集成 → 搜索「福特派互联」（点击上方「开始配置」可直接打开添加流程）
2. 选择登录方式：**手机号验证码登录**（输入手机号 → 获取短信验证码 → 输入完成）或 **用户名密码登录**（密码仅用于本次登录换令牌、不会保存）
3. 按所选方式完成登录
4. 在集成选项（Options）中可开启「启用车辆定位追踪」、选择坐标体系（WGS-84 / GCJ-02）；状态刷新间隔为固定默认 30 分钟（v4.9.6 起不再用户可配置）

**配置参数（集成选项 Options）**：

| 参数 | 默认值 | 说明 |
|---|---|---|
| 启用车辆定位追踪 | 开 | 关闭后不再创建/更新 `device_tracker` 车辆定位 |
| 坐标体系 | WGS-84 | 车辆定位坐标输出：WGS-84（地图通用）或 GCJ-02（国内地图偏移系） |

> 登录信息（手机号/品牌/登录方式）在集成「重新配置」入口维护；令牌过期时集成自动弹出重新认证。

**服务（v5.1.7）**：`fordpass_cn.refresh_status`（可选 `vin` 参数：缺省刷新全部车辆、指定 VIN 只刷新该车；VIN 不在当前账号车辆中时返回错误）、`fordpass_cn.clear_tokens`（清除令牌重置会话，重载集成后重新认证）——可在自动化/脚本中直接调用。

> 白盒加密内核为**纯 Python 查表实现**（v4.2.7 起 100% 破译，零外部依赖），无需安装任何额外 Python 包。

## 📈 数据统计与云端健康（v5.4.7）

- **长期统计**：电量、油量、总里程、续航里程、剩余可行驶里程、机油寿命均为数值传感器（distance / battery 分类 + measurement 统计），在「开发者工具 → 统计」可直接查看 30 天趋势图。
- **可进趋势图的全部分类**（v5.4.8 起全部数值传感器均带 measurement）：燃油量/动力电池电量/尿素液位（%）、总里程/续航里程/纯电续航/尿素续航（km）、左前/右前/左后/右后/内后双胎压+推荐胎压（kPa）、蓄电池电压（V）、车内温度（°C）、机油寿命（%）、剩余续航（km）、上次充电电量（%）、距离自动熄火时间（分钟）。
- **充电统计**：家充桩充电记录含真实电量字段的车型，会创建「上次充电电量」传感器（电量 %，可统计）。行程（trip）统计暂未提供——福特中国 API 无行程数据端点。
- **云端同步状态**：每辆车都会创建「云端同步状态」诊断传感器，复用每轮刷新结果（零额外请求）——连续失败自动转「异常」，属性带最后成功/失败时间、连续失败次数与失败摘要（限流 / WAF 拦截 / 网关 / 业务错误已在日志分级），可接自动化告警，云端接口变更后第一时间发现。

## 📖 自动化示例与用例

**出门前检查车辆状态**（早晨 8 点自动刷新并提醒）：
```yaml
alias: 出门前检查车辆
trigger:
  - platform: time
    at: "08:00:00"
action:
  - service: fordpass_cn.refresh_status
  - delay: "00:00:30"
  - service: notify.mobile_app
    data:
      title: 车辆状态
      message: >
        油量 {{ states('sensor.<vin>_fuel_level') }}%，续航
        {{ states('sensor.<vin>_driving_range') }} 公里
```

**夜间忘锁车提醒**：
```yaml
alias: 夜间锁车提醒
trigger:
  - platform: time
    at: "23:00:00"
condition:
  - condition: state
    entity_id: lock.<vin>_door_lock
    state: "unlocked"
action:
  - service: notify.mobile_app
    data:
      message: 车门未锁，请确认
```

**云端同步异常监控**（后端接口变更/风控拦截第一时间发现，v5.5.2 模板——利用集成自带「云端同步状态」诊断传感器，零额外请求）：
```yaml
alias: 福特云端同步异常提醒
trigger:
  - platform: state
    entity_id: sensor.<vin>_sync_health   # 「云端同步状态」实体
    to: "异常"
action:
  - service: notify.mobile_app
    data:
      title: 福特派同步异常
      message: >
        云端同步连续失败，请检查福特服务器/账号状态。
        失败摘要：{{ state_attr('sensor.<vin>_sync_health', 'last_failure') }}
```
> 恢复后该传感器自动回到「正常」，无需人工复位。

**胎压告警通知**（利用胎压系统状态传感器）：
```yaml
alias: 胎压告警提醒
trigger:
  - platform: state
    entity_id: sensor.<vin>_tpms_system_status   # 「胎压系统状态」实体
    to: "Fault"
action:
  - service: notify.mobile_app
    data:
      message: >
        胎压系统报警！左前 {{ states('sensor.<vin>_lf_tire') }} kPa，
        右前 {{ states('sensor.<vin>_rf_tire') }} kPa，
        左后 {{ states('sensor.<vin>_lr_tire') }} kPa，
        右后 {{ states('sensor.<vin>_rr_tire') }} kPa
```

**保养到期提醒**（利用「保养计划」实体，可按需加时间条件避免每日重复提醒）：
```yaml
alias: 保养到期提醒
trigger:
  - platform: time
    at: "10:00:00"
condition:
  - condition: template
    value_template: "{{ '保养' in states('sensor.<vin>_maintenance_plan') }}"
action:
  - service: notify.mobile_app
    data:
      title: 车辆保养提醒
      message: "{{ states('sensor.<vin>_maintenance_plan') }}"
```

> 实体 id 中的 `<vin>` 需替换为实际车辆识别码；全部实体名与服务名可在 HA 开发者工具中查询确认。

## 🎛 仪表盘卡片示例（v5.5.2）

推荐配合 **Vehicle Status Card**（HACS → Frontend 搜索安装，通用车辆卡片）把福特派实体拼成车况面板。在仪表盘「原始配置编辑器」中加一张卡（YAML）：

```yaml
type: custom:vehicle-status-card
vehicle: 福特锐际
image: /local/fordpass_cn/vehicle.png   # 可选：集成车辆图片
entities:
  - entity: lock.<vin>_door_lock        # 门锁状态
    label: 门锁
  - entity: sensor.<vin>_fuel_level     # 燃油量 %
    unit: "%"
  - entity: sensor.<vin>_driving_range  # 续航 km
    unit: km
  - entity: sensor.<vin>_odometer       # 总里程 km
    unit: km
  - entity: sensor.<vin>_lf_tire        # 左前轮胎压 kPa
    unit: kPa
  - entity: sensor.<vin>_rr_tire        # 右后轮胎压 kPa
    unit: kPa
buttons:
  - entity: button.<vin>_refresh        # 刷新车辆状态
  - entity: button.<vin>_horn           # 鸣笛（按车型可用）
  - entity: button.<vin>_remote_start   # 远程启动（按车型可用）
map:
  entity: device_tracker.<vin>_location  # 车辆定位
```

> 不同车型可用按钮不同（鸣笛/远程启动/尾门/空调/充电开关等），在开发者工具按 `button.` + 车型前缀查询实际实体；定位实体需在集成选项开启「位置跟踪」。卡片配置细节以 Vehicle Status Card 官方文档为准。

## 🔌 与 EVCC 联动（v5.4.6）

纯电 / 插混车型的充电相关实体已按 EVCC（开源充电调度器）可直接消费的格式标准化，配合 EVCC 的 Home Assistant 数据源即可实现自动充电调度。可直接读取的实体：

| 实体 | 类型 | 说明 |
|---|---|---|
| `sensor.<vin>_battery_fill_level` | 数值 % | 动力电池电量（SOC），device_class=battery，自动进长期统计 |
| `sensor.<vin>_prognostic_range` | 数值 km | 剩余可行驶里程，device_class=distance、state_class=measurement（v5.4.6 起数值化，此前为「123 km」文本） |
| `sensor.<vin>_ev_dte` | 数值 km | 纯电续航 |
| `sensor.<vin>_distance_to_empty` | 数值 km | 续航里程 |
| `sensor.<vin>_odometer` | 数值 km | 总里程，device_class=distance |
| `sensor.<vin>_charging_status` | 文本 + 数值属性 | 充电状态（充电中/未充电/已充满），属性 `evcc_charge_state` 为数值（0 未知 / 1 未充电 / 2 充电中 / 3 已充满） |
| `switch.<vin>_wallbox_charge` | 开关 | 家充桩充电控制（smartwallbox 绑定桩车型） |

EVCC 侧示例配置（`evcc.yaml`，按你的 EVCC 版本模板字段微调）：

```yaml
vehicles:
  - name: mach_e
    type: template
    template: mach-e
    source: homeassistant
    title: 电马
    soc:
      source: homeassistant
      entity: sensor.<vin>_battery_fill_level
    range:
      source: homeassistant
      entity: sensor.<vin>_prognostic_range
    chargeState:
      source: homeassistant
      entity: sensor.<vin>_charging_status
      attribute: evcc_charge_state
    enable: switch.<vin>_wallbox_charge
```

> `evcc_charge_state` 数值语义与 EVCC 一致：0=未知、1=未充电、2=充电中、3=已充满。家充桩充电开关仅在 smartwallbox 探测到绑定桩的车型出现（纯电/插混家充桩车型）。

## 🗑 移除集成

1. 设置 → 设备与服务 → 找到「福特派互联」条目 → 删除该配置（其下设备/实体一并移除）
2. 若要同时卸载代码：HACS → 已下载 → FordPass China → 卸载；或删除 `/config/custom_components/fordpass_cn/` 目录后重启
3. 车辆图片等缓存存于 `.storage` 私有目录，随集成删除一并清理，不残留
4. 云端账号不受影响——福特派 App 照常使用

> 卸载不会删除车辆数据与配置备份；重新安装后重新登录即可恢复使用。

## 📊 支持的实体

> 车型 × 能力矩阵（v5.1.7）：✔=该车型支持；✘=不支持；「按车型」=实体按云端能力位图/命令白名单自动创建，非该能力车型不出现。燃料类型由车辆业务字段（fuelType）判断：纯油（G/D）、混动（PHEV/HEV/MHEV）、纯电（BEV）。

| 功能域 | 汽油/柴油 | 混动 (PHEV/HEV/MHEV) | 纯电 (BEV) | 备注 |
|---|---|---|---|---|
| 远程控车（上锁/解锁/远程启动/熄火/鸣笛·声光·灯光寻车/手动刷新） | ✔ | ✔ | ✔ | 按云端命令白名单创建 |
| 拖车灯光检测 | 皮卡 ✔ | — | — | 按车型 |
| 预约出发 / 预约充电 | ✘ | ✔ | ✔ | cevs 域 |
| 立即充电 / 停止充电 | ✘ | ✔ | ✔ | |
| 家充桩 BLE 自动认证 | ✘ | ✔ | ✔ | 授权后插枪即自动配对充电 |
| 燃油 / 续航 | ✔ / ✔ | ✔ / ✔ | ✘ / 电池续航 | 油量与电量实体独立 |
| 车辆状态监控（里程/胎压/门窗/点火/报警/异常警示中文） | ✔ | ✔ | ✔ | 全车型核心状态 |
| 服务信息（保养计划/召回/SIM/WiFi/空调滤芯/机油寿命/慢漏气胎/预测性诊断/客服电话/电子说明书） | 按车型能力 | ✔ | ✔ | |
| 影像（车辆图片/3D 车型图/行车记录） | 按车型 | 按车型 | 按车型 | 行车记录按车载硬件 |

| 类型 | 实体 | 说明 |
|---|---|---|
| device_tracker | 车辆定位 | GPS 坐标 + 地址属性，地图可显示 |
| image | 车辆图片 / 3D 车型图 | 车型渲染图，探测到 modelUrl 才创建（全车型自动适配） |
| lock | 车门锁 / 后备箱锁 | 上锁 / 解锁；后备箱锁按车型尾门能力创建（前备箱锁 v5.2.4 因命令载荷未确认移除） |
| button | 手动刷新状态 / 中央区灯光 / 辅助设置 / OTA 激活排程 / 重置空调滤芯 / 拖车灯光检测 / 标记消息已读 / 行车记录启停 | 均按车型云端能力自动创建 |
| switch | 远程启动 / 灯光寻车 / 鸣笛寻车 / 声光寻车 / 预约充电 | 鸣笛寻车类型 0-3；声光共舞走 `send-command InitialVA`（App 官方通道） |
| select | 鸣笛持续时长 / 鸣笛类型 | 选择即保存，鸣笛时参数直达车机 |
| sensor | 车辆状态 / 异常警示 / 保养计划 / 召回 / SIM / WiFi / 空调滤芯 / 机油寿命 / 预测性诊断 / 车辆服务信息 / 鸣笛设置云端状态 / 车辆在线状态 / 车辆设备信息 / 授权状态 / SRS 档案 / 传感器影子 / 行车记录视频 / 最新消息 / OTA 提醒 / 车辆清单 / 消息中心 / 共享列表 / 设备列表 | 全部数据驱动：登录后一次性探测，探测到数据才创建（多 VIN 每车独立） |

> 实体创建全部数据驱动：登录后按本车云端能力与车辆数据判断（多 VIN 账号下每辆车独立创建自己支持的实体组），不支持的命令按下会返回网关明确报错，不制造静默无效实体；刷新失败保留最后已知状态，0 unavailable。

## 🛠 故障排查

<div align="center">

[![查看实体状态](https://my.home-assistant.io/badges/developer_states.svg)](https://my.home-assistant.io/redirect/developer_states/)
[![查看系统日志](https://my.home-assistant.io/badges/logs.svg)](https://my.home-assistant.io/redirect/logs/)

</div>

| 问题 | 处理 |
|---|---|
| 用户名密码登录失败/被拦截（`AADB2C: An exception has occurred`、567） | 福特登录页（login.ford.com.cn）前置腾讯云 EdgeOne 安全防护，按 TLS/行为指纹拦截纯 HTTP 客户端（2026-10-24 实测 requests 与 curl_cffi 全指纹均返回 567 拦截页）——**密码登录须走浏览器辅助**：按配置向导第 2 步在浏览器打开登录链接完成登录，复制地址栏 `?code=xxx` 粘贴回集成（一次性有效）。被拦截/不便时**改用短信验证码登录**（登录后令牌自动续期，无需频繁重登） |
| 某车型实体没有创建 | 集成按云端能力/车型字段判断，**探测端点 200 ≠ 车型支持**——未支持的功能不创建假实体，属正常设计 |
| 重新加载弹「Setup ... cancelled」 | v4.9.8 已修复：setup 首次刷新 20s 短超时转 ConfigEntryNotReady 自动重试，请更新到最新版 |
| 密钥轮换后无法登录/加密失败 | 福特轮换密钥后需发布新版本适配，请关注 Releases 更新 |
| 实体状态不变/不刷新 | 后台刷新固定 30 分钟；可点「手动刷新车辆状态」按钮强制车机上报 |
| 视频说明书网址太长/想点击打开 | v5.0.2 起状态显示「查看视频说明书」，完整 URL 在属性 `manual_url`。**注意：sensor 状态文字本身不可点击（HA 平台限制），需添加下方 Markdown 卡片到仪表盘，点击卡片标题即可跳转浏览器打开**： |

```yaml
type: markdown
content: >-
  [📖 打开视频说明书]({{ state_attr('sensor.<你的视频说明书实体>', 'manual_url') }})
```

替换 `<你的视频说明书实体>` 为你的实体 ID：**设置 → 设备与服务 → 福特派 → 实体 → 找到「视频说明书」→ 复制实体 ID**。添加方式：仪表盘右上角编辑 → 添加卡片 → 点「手动」/「YAML 模式」→ 粘贴以上配置 → 保存。

## 🔒 隐私与安全

本仓库与集成运行时的个人信息防护措施（v5.6.1 起持续加固，v5.7.6 更新）：

- **仓库零个人信息**：真实 VIN、手机号、密码等一律使用测试假数据（假 VIN `LVSHFAKE000000001`、示例号码）；真实格式 VIN（17 位）禁止入库，发布脚本内置断言拦截。
- **git 历史无残留**：历史曾出现的敏感内容已通过 filter-repo 全历史重写清除（1235→1214 commits），并已删除历史 release 的全部 zip 快照（快照不受历史重写影响）；不可达旧 commit 已提交 GitHub Support 请求清除，`docs/privacy_audit.py` 支持 `--dangling <sha>` 逐点复查。
- **实体唯一标识脱敏**：所有平台实体 unique_id 与设备标识使用 `sha256(VIN) 前 16 位` 确定性哈希前缀（稳定、不可逆），registry 不存真实 VIN。
- **活动历史不记录个人信息**：车辆识别码（VIN）、车牌号、车辆定位三个实体的状态值不写入 logbook 活动历史（按 unique_id 尾缀精确跳过）；车辆定位 device_tracker 状态为 zone 状态（home/not_home）不含坐标，其经纬度仅在 attribute 中。
- **日志掩码**：debug 日志对 VIN / 经纬度 / 地址掩码输出（VIN 保留首 4 末 4）；车辆图片缓存文件名使用 sha1 摘要不含 VIN；操作日志不打印完整 VIN。
- **发布前强制审计**：发布脚本内置断言（禁词 + 真实格式 VIN 正则 + BOM 检查 + hacs.json 归档模式断言）+ `docs/privacy_audit.py` 全库审计（需设置 `GITHUB_TOKEN` 环境变量，覆盖可达历史 + dangling commit + releases + codeload 归档），输出 **PASS** 方可发布；发布后复检 `/releases/latest` 无 zip 资产。
- **数据本地**：您的车辆数据（VIN、位置、车况等）仅存储在您自己的 Home Assistant 中，集成除福特/林肯官方服务端外不向任何第三方传输、共享您的数据。

## ⚠️ 免责声明

1. **非官方项目**：本项目为个人开发作品，与福特汽车（中国）有限公司、长安福特汽车有限公司、林肯中国及任何官方主体**无任何关联、未经任何授权**；「福特派」「林肯之道」等商标归各自权利人所有，本文中引用仅用于识别所对接的服务。
2. **仅供学习研究**：本项目仅限**个人学习研究**与**自用**，请勿用于商业用途。
3. **风险自担**：使用本集成可能导致账号风控、功能失效、违反服务条款等后果，**一切风险与责任由使用者自行承担**；作者不提供任何服务可用性担保。
4. **数据本地**：车辆数据（VIN、位置、车况等）仅存储在您自己的 Home Assistant 中，集成除福特/林肯官方服务端外不向任何第三方传输、共享您的数据。
5. **下架配合**：若相关权利方要求移除相关素材或本仓库，作者将配合下架。

## 📜 更新日志

<details>
<summary>最近版本（完整历史见 <a href="https://github.com/RockJesus/ha-fordpass-cn/releases">GitHub Releases</a>）</summary>

- **v5.8.1**（2026-10-24）：**修复旧 VIN 设备清理失败（HA 2026.10 API 签名变更）**——`_cleanup_legacy_vin` 调 `DeviceRegistry.async_get_device_by_identifier` 时 HA 2026.10 起需必填 `config_entry_id` 参数，旧调用缺参每次启动抛 `TypeError`（日志 `fordpass_cn legacy vin device cleanup failed`），导致升级后旧 VIN 设备条目无法清理；补传 `entry.entry_id` 修复（清理失败不阻断 setup 的兜底逻辑保留）。
- **v5.8.0**（2026-10-24）：**B2C 密码登录引导优化（EdgeOne WAF 实证说明）**——2026-10-24 手机号密码登录 HAR 实测：login.ford.com.cn 前置腾讯云 EdgeOne 安全防护，按 TLS/行为指纹拦截纯 HTTP 客户端（requests 完整伪造 WebView UA/headers 与 curl_cffi impersonate chrome110/120/chrome 全指纹均返回 567 拦截页；集成四步流参数与 App HAR 逐项一致，非参数问题）——密码登录无法改为纯 API 直连，浏览器辅助是唯一可靠路径。① 配置向导「用户名密码登录」第 2 步改为分步操作说明：EdgeOne 拦截原因、电脑/手机浏览器登录步骤、地址栏复制 `?code=` 指引、授权码一次性有效提示、被拦截时改用短信验证码建议；② 密码步骤补充「密码仅保存在 Home Assistant 本地配置中」；③ README 故障排查表同步更新该说明（含实测日期与拦截机理）。
- **v5.7.9**（2026-10-24）：**服务日志/异常消息 VIN 掩码收口（运行时 VIN 暴露最后残留）**——① `fordpass_cn.refresh_status` 服务 info 日志此前打印完整 VIN（`vin=%s`），现只留尾段 `vin=…%s`（`coord.vin[-6:]`）；② 服务调用时 VIN 不在当前账号车辆的异常消息（HomeAssistantError）同步掩码为 `VIN …尾段`（此前完整 VIN 进错误消息/前端通知/日志）；③ 全量复查确认运行时完整 VIN 输出点已清零：图片缓存文件名 SHA1 摘要（v5.1.8）、实体 unique_id/设备标识 sha256 前缀（v5.7.1）、logbook 隐私尾缀跳过（v5.7.0）、api.py vehicle-status raw debug 掩码（v5.6.10）、diagnostics redact（v4.9.5）、coordinator 恢复日志 VIN 尾段（v5.6.x）均已在册。
- **v5.7.8**（2026-10-24）：**林肯登录参数对齐（App 6.0.0 HAR 实证）**——① 林肯品牌 `app_version` 由 6.7.0 对齐为 **6.0.0**（2026-10-24 林肯之道 App 实拍：generate-passcode/passcode-login headers `appversion=6.0.0`、vehicles/list query `appVersion=6.0.0`，与福特侧同规：网关按 App 实拍版本发请求最稳）；② `generate_passcode` 补 `auth-token: None` 防御（与 `passcode_login` 一致——token-exchange 家族 App 实拍均不带 auth-token 头，reauth 场景防旧 token 残留导致网关校验失败；v5.6.9 修复时漏掉发码端点）。其余登录参数经逐项核对全部与 App 一致：touchPoint=LINCOLN_APP_LOGIN / brand=LINCOLN / appKey=lincoln / clientType=LW / tncAccepted / tncOutBoundAccepted / osType=ANDROID / timestamp+sign 签名 / 林肯自生成会话 IV（不沿用发码 IV，App 实证 generate/login xjw 不同仍 200）。
- **v5.7.7**（2026-10-24）：**token 刷新响应结构保护（修复诊断信息丢失）**——`refresh_token` 对「HTTP 200 但响应无 token 字段」的异常结构（网关异常响应/结构变更/业务拒绝）此前直接 `KeyError: encryptedAccessToken`，被 `_as_refresh_failed` 按 500 处理、仅记录「token refresh failed」，无法区分业务拒绝与结构异常；现与 `passcode_login` 对齐：无 token 字段时携带服务端 status/error/errorCode 抛 `FordPassApiError`，日志可直接定位真实原因（2026-10-24 福特网关 whitebox decrypt 500 熔断期间该缺口导致排障只能靠猜）。
- **v5.7.6**（2026-10-24）：**活动日志隐私跳过优化 + README 脱敏清单更新**——① `_log_refresh_states` 隐私尾缀常量提升为模块级 `_PRIVACY_UID_SUFFIXES`（VIN/车牌/定位三实体跳过逻辑不变），循环内按 entity_id 的 registry 查询改为预构建映射（一次遍历替代每实体一次查询）；② 注释明确车辆定位 device_tracker（unique_id 尾缀 `-tracker`）**不进跳过名单的原因**：其 state 为 zone 状态（home/not_home）而非坐标/地址，经纬度仅在 attribute 中，保留「车辆离家/回家」活动显示价值——防止后续误加导致活动显示缺失；③ 隐私与安全章节更新为完整脱敏措施清单（历史重写/zip 快照清除/unique_id 哈希/logbook 跳过/日志掩码/发布前强制审计/数据本地）。
- **v5.7.5**（2026-10-24）：**修复重启后活动补写崩溃（hass.helpers 移除）**——`_log_refresh_states` 用 `self.hass.helpers.entity_registry` 快捷属性获取实体注册表，该属性在 HA 2026.10 已移除（`AttributeError: 'HomeAssistant' object has no attribute 'helpers'`），重启后 45 秒延迟补写活动任务直接崩溃报「Task exception was never retrieved」；改为标准导入 `from homeassistant.helpers import entity_registry as er` + `er.async_get(hass)`（与集成内其他 2 处一致），重启后活动补写恢复正常。
- **v5.7.4**（2026-10-24）：**TTL 缓存修复（告警/未读消息不再每轮真拉）**——`active_alerts` 与 `messages` 的 TTL 原为 1800s，与 30 分钟刷新间隔**等长**，每次轮询时缓存恰好过期、永不命中（等效每轮真拉，白写缓存）；改为 3600s 后每两轮命中一次缓存，云端请求量下降；告警/消息实时性仍为 30 分钟轮询粒度内（最多延迟 1 个周期），手动刷新按钮 force 绕过缓存照常全量真拉不受影响。
- **v5.7.3**（2026-10-24）：**隐私审计工具去 SHA 索引**——`docs/privacy_audit.py` 不再把已知旧 commit 的具体 SHA 硬编码入库（硬编码等于在仓库中公开含敏感数据的不可达旧 commit 的访问索引，任何人按 SHA 即可读取历史残留）；改为审计时通过 `--dangling <sha>` 命令行参数临时传入。仓库除 main 与全部可达 git 历史零个人信息外，可读渠道（releases、codeload 归档）复检全净。
- **v5.7.2**（2026-10-24）：修复 v5.7.1 引入的废弃 API 警告——设备清理改用 sync_get_device_by_identifier（sync_get_device 将在 HA 2027.8 移除）。
- **v5.7.1**（2026-10-24）：**实体 unique_id 全面脱敏**——所有平台实体 unique_id 与设备标识（identifiers）由真实 VIN 改为 **sha256(VIN) 前 16 位**确定性哈希前缀（同一车辆跨版本稳定、不可逆、无个人标识）；升级时自动清理旧 VIN 前缀实体/设备条目（registry 不再残留真实 VIN）；同步脱敏 coordinator 名称、默认车辆名、能力检测 debug 日志与按钮操作日志的 VIN 尾缀。
- **v5.7.0**（2026-10-24）：**隐私脱敏加固**——① logbook 活动历史不再写入车辆识别码（VIN）、车牌号、车辆定位三个实体（按 unique_id 尾缀精确跳过，避免个人信息进 recorder 持久化）；② api.py vehicle-status raw debug 日志对 VIN / 经纬度 / 地址掩码输出（VIN 保留首 4 末 4，经纬度与地址整体掩码）。
- **v5.6.9**（2026-10-24）：**彻底修复 token 刷新失败**（HAR 6.16.0 完整 body/headers 取证）：① token-exchange 家族端点（刷新/撤销/验证码登录/B2C 换 token）App 实拍**均不带 auth-token**——集成此前给刷新请求带上已失效的旧 access token，网关校验失败返回 `401/600`（这是 v5.6.6 刷新失败真根因）；② refresh/revoke 请求体恢复 `brand` 字段（该字段必填，缺失返回 400 `errorCode 100502 brand can not be empty`）。修复后刷新/撤销请求与 App 完全一致（encryptedRefreshToken+brand+xjw+timestamp+sign，无 auth-token）。
- **v5.6.8**（2026-10-24）：**修复 Ruff CI 红叉**——image.py 未使用的 `import re` 触发 F401（`re imported but unused`），删除该导入，Ruff Lint 恢复全绿。
- **v5.6.7**（2026-10-24）：**修复 token 刷新被网关拒绝（refresh-dlt-token 401/600）**——登录成功但刷新令牌时报 `token refresh failed (401/600 after retry)`：集成刷新请求体此前多带 `brand` 字段，与 App 6.16.0 实测不符（HAR 实拍刷新请求仅 `encryptedRefreshToken`+`xjw` 两个字段），网关 DTO 严格校验拒绝额外字段；同步对齐登出撤销请求（App 实测仅 `encryptedRefreshToken` 一个字段）。修复后刷新链路与 App 完全一致。
- **v5.6.6**（2026-10-24）：**修复登录白盒加密 IV 回归（generate-passcode 500）**——v5.6.0 起 `_new_iv()` 误改为 `os.urandom(16)`，任意字节 IV 被网关拒绝（`whitebox decrypt error / 100500`，福特/林肯双品牌登录发码均失败）；恢复与 App 6.16.0 一致的 16 位小写字母 IV（实机抓包逐字符核对），修复后集成加密实测发码接口 **200 成功**。
- **v5.6.5**（2026-10-24）：**运行时 VIN 暴露收口 + 审计工具补 dangling 扫描**——① 日志隐私：按钮操作 info 日志 VIN 掩码只留后 6 位（此前 `重置空调滤芯/清空预约出发` 打完整 VIN）；② 车辆图片缓存文件名不再含 VIN（一律 sha1 摘要，杜绝 VIN 落盘）；③ `docs/privacy_audit.py` 新增 **dangling commit 扫描**：filter-repo 重写后不可达旧 commit 仍可按 SHA 从 GitHub 读取，审计时通过 `--dangling <sha>` 参数临时传入已知旧 commit 逐一点查（git/trees + contents 全文件），发现真实 VIN/手机号即 **FAIL** 并提示提交 GitHub Support 清除——不再误报 PASS；④ unique_id/设备标识符保留 VIN（HA 官方要求稳定唯一标识，仅存本地注册表，不对外暴露）。
- **v5.6.4**（2026-10-24）：**移除测试套件与历史残留，隐私最小化**——① 删除仓库全部测试相关文件与代码（`tests/` 目录、`pytest.ini`、`.github/workflows/pytest.yml`），并同步从 **git 全历史**移除（filter-repo 重写，1235→1214 commits，历史中 tests 路径零残留），杜绝测试数据被误读为个人信息的可能；② CONTRIBUTING 同步移除测试流程指引；③ 隐私审计工具 `docs/privacy_audit.py` 继续作为发布前唯一质量门。
- **v5.6.3**（2026-10-24）：**全局个人信息脱敏加固**——① 新增 `docs/privacy_audit.py` 隐私审计工具（真实 VIN/手机号/密码/身份证/邮箱五类扫描，覆盖全 git 历史 + release 元数据 + codeload 归档，设置 `GITHUB_TOKEN` 环境变量即可运行），发布前后一键自查；② 全量重建 297 个 GitHub release，清除 release 元数据中残留的历史 commit 引用；③ 旧历史 commit 对象（含脱敏前内容）已提交 GitHub Support 清除不可达对象。仓库已确认无任何个人信息残留（真实 VIN/手机号/密码零命中），测试数据一律使用假 VIN `LVSHFAKE000000001` 与示例号码。
- **v5.6.2**（2026-10-24）：**云端 500 熔断不再误触重新认证**——福特网关 `whitebox decrypt` 500 故障（10-24 实测：token refresh 端点熔断）旧代码把所有刷新异常包装成 401 → entry 每轮 setup_error + 用户被要求重新认证（token 实际未失效）。本次把刷新失败按性质分类：**业务性认证失败（401/403/600，token 真失效）仍触发重新认证；网关 5xx / 网络异常保留原码走 HA 自动重试（ConfigEntryNotReady），用户无感知**。新增分类单元测试 3 例，全量 436 项通过。
- **v5.6.1**（2026-10-24）：**仓库脱敏 + 免责声明修正**——① 移除测试中误入的真实格式 VIN（`tests/test_wbsk.py`、`tests/test_init.py`，统一替换为测试专用假 VIN `LVSHFAKE000000001`），避免作者个人信息进入公开仓库；② 免责声明措辞中性化（删除素材来源自认表述，改为「个人开发作品 / 相关素材」），降低法律风险；③ 发布前隐私断言新增真实格式 VIN 检查（17 位车架号禁止入库，测试仅允许 `LVSHFAKE` 假 VIN）。
- **v5.6.0**（2026-10-24）：**代码审查 P0/P1/P2 修复（认证生命周期 + 多账号 + 健壮性）**——依据《FordPass-CN 代码审查报告》（v5.5.9）逐项验证属实后修复并发布：
  - **认证生命周期**：① reauth/reconfigure 完成后由 HA 重载 entry（token 立即生效，不再持续报 401 直到手动重载；HA 2025.1+ 走 async_update_reload_and_abort，旧版本自动回退）；② reauth/reconfigure 从原 entry 恢复品牌——林肯 entry 重新认证不再被当成福特登录（轻则 passcode_failed，重则拿错租户 token）；③ 并发 401 刷新竞态修复——等待方直接复用正在进行的刷新结果（单飞锁+5 秒缓存），不再 sleep 后拿旧 token 重试误触发 reauth；④ 首次刷新失败不再导致平台 setup 崩溃（全部平台用空 dict 兜底，云端瞬断也能保留最后已知状态）。
  - **多账号/双品牌**：⑤ 服务注册迁移到集成级（async_setup）——多 entry 时 refresh_status/clear_tokens 不再互相覆盖、卸载任一账号服务不再消失；⑥ 活动日志按 entry 限定实体（多账号不串写、事件量不再翻倍），并修正 v4.7.6 迁移笔误（platform==DOMAIN 恒为空 → 改为 domain==DOMAIN，活动实体关联恢复）；⑦ 重复 entry 查重兼容密码登录（手机号在 username 字段时也能拦截）。
  - **健壮性/配置流程**：⑧ 林肯品牌隐藏密码登录（无 B2C 租户，避免落福特登录页死胡同）；⑨ 白盒解密校验 PKCS#7 填充与 base64（坏密文抛明确错误，不再静默返回空串）；⑩ 手动刷新按钮 30 秒等待改后台任务（服务调用不再挂起、可随集成卸载）；⑪ LBS/honk 请求统一 60 秒超时；⑫ 命令状态轮询非 dict 响应不再 60 秒忙循环；⑬ 传感器关闭平台默认轮询（纯推送实体）；⑭ 设备信息按品牌显示 Lincoln/Ford；⑮ IV 改用 CSPRNG 生成；⑯ 清理死代码与过期文案（normalize_gcj02、const.PLATFORMS、unicorn/pyelftools 依赖提示、password_login_failed 死键、重复循环）。
  - 最低 HA 版本提升至 **2025.1.0**（reauth 重载 API 依赖）；保留密码持久化（v4.4.6 用户明确要求）；LBS 过期时间语义待真实抓包确认（未盲改）。新增回归测试 5 项，全量 433 项通过、ruff 全绿。
- **v5.5.9**（2026-10-23）：**双品牌全车型 mock 测试矩阵（防回归）**——新增福特与林肯全车型 mock 测试 14 例：福特按真实车系（锐际/探险者/全顺柴油/蒙迪欧插混/锐界 L 油混/蒙迪欧运动版/领睿轻混/Mustang Mach-E，覆盖 G/D/P/HEV/MHEV/BEV 六种动力）、林肯按主流车系（冒险家/航海家/飞行家/Z 等，同六种动力），验证：① 按动力类型创建传感器集合正确（纯电车型不建燃油量/续航/机油寿命实体，其余动力全建；总里程/胎压/门锁/报警等通用实体全建）；② 按能力位图与状态字段创建按钮集合正确（crccFlag→中央区灯光、双后轮→拖车灯检测、固件升级→OTA 排程、辅助设置、手动刷新全车型、停车影像按硬件创建）；③ 按能力创建开关集合正确（远程启动/灯光/鸣笛/声光寻车/预约出发/家充桩）；④ 车辆状态字段解析与 ccfeatures 能力位图解码（双品牌共用字段与能力名表）；⑤ 燃料类型中文聚合。双品牌数据对现有全车型实体创建机制零改动全部通过，固化品牌无关性防回归。
- **v5.5.8**（2026-10-23）：**林肯 5.8.0 APK 逆向实证清理**——① 逆向林肯之道 App 5.8.0 确认：与福特共用 cnxapi-vds/cvinfo/cds/token-exchange 网关、v5 控制命令集（FORD_ENGINE_START/LOCK/HONK/拖车灯等）、车型字段（fuelType/ccfeatures/cvfeatures/vehicleStatus）与状态字段（gpsState/油量/里程/胎压/机油寿命），林肯车辆实体创建与解析逻辑直接复用现有全车型机制，无品牌专属缺口；品牌参数（clientType=LW/app_version/touchPoint=LINCOLN_APP_LOGIN/brand=LINCOLN）与白盒加密 libwbsk 与福特共用（登录已实测通过）；② 清理林肯登录链路就绪前的「待抓包」死代码分支（lincoln_pending 提示页与对应翻译/测试）——林肯 profile 已就绪、该分支永不触发，同时移除其未使用导入；③ 品牌参数注释更新为 5.8.0 逆向实证结论
- **v5.5.7**（2026-10-23）：**登录失败真实原因透出（修复 unexpected error）**——`passcode_login` 响应若不含 token 字段（HTTP 200 业务失败：验证码错误/过期、风控拒绝，或响应结构差异）此前直接 KeyError 被当作「unexpected error」，用户看不到原因；现改为携带服务端 status/error/errorCode 抛出登录失败错误，配置流程直接显示服务端返回的具体原因，并输出告警日志便于排查
- **v5.5.6**（2026-10-23）：**林肯登录 whitebox decrypt error 修复 + 配置文案再精简**——① 修复林肯验证码登录报「whitebox decrypt error / errorCode 100500」：`passcode_login` 在 iv=None（林肯分支）时手机号与验证码两次加密各生成不同随机 IV，请求只携带手机号的 xjw，服务端解验证码字段失败——改为自生成一次会话 IV 并复用，两字段同 IV（与官方 App 会话内同 IV 行为一致）；② 配置流程文案再精简：删除全部字段灰色说明（data_description）、登录方式页去掉「失败时查看日志」冗余提示
- **v5.5.5**（2026-10-23）：**同手机号双品牌并存 + 配置流程文案精简**——① 同一手机号可同时配置福特派与林肯之道两个品牌账号（此前 unique_id 按手机号查重，已配福特后登录林肯会提示「该手机号已配置」）；现改为「手机号+品牌」双重判定，仅同品牌重复配置才拦截，升版前的旧条目（无品牌字段）按福特处理不受影响；② 配置流程各步骤文字精简：品牌选择页去掉「林肯暂不可登录」过时说明（v5.5.3 起林肯已可登录）、密码登录页删除 WAF 长段说明、重新认证页缩短提示；③ 配置条目标题带品牌标签（手机号 + 福特派/林肯之道），多账号并存时更易区分
- **v5.5.4**（2026-10-23）：**CI 修复（林肯登录 NameError + 测试对齐）**——① 修复 config_flow 漏导入 BRAND_LINCOLN 导致林肯验证码登录直接崩溃（提交验证码即 NameError，福特登录不受影响）；② 清理 api.py 未使用导入 TOUCH_POINT（Ruff F401）；③ 双品牌参数化测试改按 test_api_core 同构方式 mock 加密（crypto 为只读属性，赋值写法会 AttributeError），补齐有效密钥样例；④ 品牌链路测试对齐新行为：林肯 profile 就绪后选中直接进入登录方式选择（原断言停留在未就绪时代的 lincoln_pending 提示页），v5 直控 401 测试改引用 const.TOUCH_POINT。修复后本地 pytest 409 全绿 + Ruff 零告警
- **v5.5.3**（2026-10-23）：**林肯之道登录修复（品牌参数化）**——林肯登录全链路参数按模拟器抓包对齐：验证码登录触达点用 LINCOLN_APP_LOGIN、请求体品牌 LINCOLN、公共请求头 appversion/clienttype 用 6.7.0/LW、车辆列表查询参数 appKey=lincoln；登录请求不再沿用验证码阶段的会话密钥（林肯 App 每次请求独立生成，抓包实证 generate/login 密钥不同仍成功），福特派验证码登录行为不变；新增双品牌参数化测试 7 例
- **v5.5.2**（2026-10-23）：**隐私加固 + 文档增强**——① 日志隐私：`vehicles` 调试日志剔除解密后车牌、VIN 掩码只留后 6 位（解密字段沿用加密键名会绕过旧掩码逻辑，现已显式处理）；② 文档新增：云端同步异常监控、胎压告警、保养到期提醒三个现成自动化模板，Vehicle Status Card 车况面板配置示例（门锁/油量/续航/里程/胎压/车控按钮/定位映射）
- **v5.5.1**（2026-10-23）：**日志修复**——首轮补写日志去掉硬编码版本号（v5.1.6 起多轮未更新，日志显示失真版本误导排查）
- **v5.5.0**（2026-10-23）：**风控与 TTL 专项加固**——手动全量刷新（刷新车辆状态按钮）加 30 秒冷却：force_all 绕过全部 TTL 强制真拉，连点会成倍放大云端请求量触发风控；冷却期内重复点击静默跳过（日志可查），车控命令后的自动刷新不受限制。集成风控体系（TTL 分层缓存/账号级共享/per-key 锁/401 单飞/6 类风控响应分类/探测一次性缓存/失败保留最后已知状态）经专项审计确认达标
- **v5.4.9**（2026-10-23）：**探测明细补全 + 单位规范化**——① 消息中心/保养历史/车辆共享/充电订单与事件/家充桩记录/充电站 6 类探测数据，属性新增「最近记录」（最近 3 条逐条结构化：标题/时间、项目/日期、对象/状态、订单号/金额、电量/时间、站名/距离，宽松提取 + raw 兜底防丢数据）与「记录条数」，HA 里直接可读不必查 JSON；② 时长类单位「分钟」→ 官方单位码 `min`（HA 长期统计可识别，趋势图正常绘制）——远程启动时长、距离自动熄火时间两处
- **v5.4.8**（2026-10-23）：**充电日志明细 + 趋势图统计补全**——① 充电记录实体（cevs 车型）属性升级：最近 3 条逐条结构化（时间/电量/时长宽松提取，提取不到保留原始记录防丢数据），新增「最近记录原始」兼容旧属性名；配合 v5.4.7「上次充电电量」形成充电统计闭环；② 「距离自动熄火时间」补 measurement 统计标记——集成全部数值传感器（油量/电量/里程/胎压/温度/电压/续航/机油寿命/充电电量等）均已可进长期趋势图
- **v5.4.7**（2026-10-23）：**充电统计 + 云端同步健康 + 增长文案**——① 新增「上次充电电量」传感器（家充桩充电记录真实电量字段，电量 % 可进趋势图）；行程统计暂不做（福特中国 API 无行程数据端点，如实说明）；② 新增「云端同步状态」诊断传感器（每车无条件创建）：复用每轮刷新结果零额外请求，连续失败自动转「异常」，属性带最后成功/失败时间、连续失败次数与失败摘要（限流/WAF/网关/业务分类已在日志），云端接口变更后第一时间能发现，可接自动化告警；③ README 首屏加「当前中国区唯一活跃维护」卖点，新增「数据统计与云端健康」章节
- **v5.4.6**（2026-10-23）：**EVCC 对接 + 长期统计**——① 剩余可行驶里程数值化（此前状态为「123 km」文本，无法进趋势图也不能被 EVCC 读取，现改为纯数值 + km 单位 + 距离分类 + 测量级统计）；② 总里程 / 续航里程 / 纯电续航补距离分类，机油寿命补测量级统计——里程、油量、机油、电量全部可看 30 天趋势图；③ 充电状态实体新增 `evcc_charge_state` 数值属性（0 未知 / 1 未充电 / 2 充电中 / 3 已充满，语义与 EVCC 一致），配合动力电池电量、剩余里程、家充桩充电开关可直接被 EVCC 消费，实现自动充电调度；④ README 新增「与 EVCC 联动」章节（实体清单 + 配置示例）
- **v5.4.5**（2026-10-23）：**补测收尾 + CI 全绿 + 两处修复**——① 鸣笛寻车开关修复：后台任务跟踪此前只接在声光寻车开关上，鸣笛开关触发后的状态轮询与自动复位会直接报错（找不到方法），现已提取为两个开关共用的实现，鸣笛触发后能正常复位、命令状态传感器正常更新；② 重新认证流程修复：确认页空表单提交不再报错（此前会因缺少登录方式键值直接 500）；③ 测试补强：全量 396 例测试通过、整体行覆盖 65%（API 层 89%、能力判定 99%、配置流程 92%、数据协调 84%），质量评分 silver 档测试覆盖项标记完成；④ CI 统一到 Python 3.12 并固化测试依赖（此前 3.14 环境与本地不一致），pytest / Hassfest / HACS 校验 / ruff 四条流水线全部通过
- **v5.4.4**（2026-10-23）：**CI 修复 + README 清理**——pytest 流水线适配 Home Assistant 2026.10（Python 3.14、官方测试框架 pytest-homeassistant-custom-component、asyncio 运行模式配置），36 例测试全绿；测试用例对齐 HA 2026.10 新 API（MockConfigEntry 来源、配置流上下文可写化、选项流属性注入、登录返回结构）；README 徽标区去掉重复的 Blog 徽标（保留欢迎语里的链接）；移除仓库根空的 tests/__init__.py（避免遮蔽测试工具包）
- **v5.4.3**（2026-10-22）：**测试覆盖补强 + 状态声明修正**——新增 api 纯函数测试（请求签名、坐标转换、业务错误判断、远程影像提取、日志摘要）与常量/能力映射完整性测试，本地 32 例测试全绿；CI 新增 pytest 流水线（完整 Home Assistant 环境，覆盖配置流程用例）；修正 quality_scale.yaml 中 has-entity-name 状态与 v5.4.2 实际迁移一致（已 done）；顺带修复 capability.leaf 末端键缺失时不返回默认值的问题
- **v5.4.2**（2026-10-22）：**实体名称改为 Home Assistant 官方「设备名 + 实体名」规范显示**——此前全部实体关闭官方实体命名（），实体名单独显示（如「燃油量」）；本版按官方规范开启（41 处实体全部迁移），实体名保持原值、显示时自动带上车型前缀（如「锐际 Escape 燃油量」），多车/多设备同时接入时一眼能分清归属；实体 ID、自动化引用、传感器数值均不受影响，只是显示名称变化
- **v5.4.1**（2026-10-22）：**配置与选项流程迁移到 Home Assistant 2026.10 新版写法**——HA 2026.10 起配置流程底层从 voluptuous 迁到 probatio，且选项流基类不再接收构造参数；本版把集成选项流程改为无参构造、表单校验按新版写法（低版本 HA 自动回退旧实现，2024.6 及以上均可正常使用），在 HA 2026.10 上打开「选项」不再有兼容隐患，功能与之前完全一致
- **v5.4.0**（2026-10-22）：**修复「重新配置」与「集成选项」同构的语义错位**——此前重新配置入口复用偏好表单、写入的是选项（刷新间隔等），和点「选项」看到的内容一样，想改手机号/登录方式没有合法入口（换账号只能删了重加）；现在按 HA 官方语义整改：重新配置会引导完整登录流程（登录方式→手机号→验证码/密码），登录成功后更新集成核心配置（手机号/登录方式/凭据），**允许更换手机号**（区别于 token 过期的重新认证——那要求同手机号）；已设置的选项偏好原样保留，重启后按新账号自动重建车辆实体；顺带补上了重新配置入口缺失的配置项解析方法（低版本 HA 点重新配置会直接报错，现已兼容）
- **v5.3.9**（2026-10-22）：**修复远程启动剩余时间显示错误**——车机返回毫秒时间戳时（部分车型实测），剩余时间计算把它当成秒，`当前时间 - 毫秒时间戳` 得到巨大负差值，前端显示数十亿分钟的错误值；现在与时间显示逻辑一致做毫秒/秒自适应，剩余时间恢复正常（秒车型行为不变）
- **v5.3.8**（2026-10-22）：**修复卸载报错 + 服务不可用 + 日志泄露**——① 卸载或重新加载集成报 `Error unloading entry ... TypeError: 'NoneType' object is not callable`：两个服务的注销回调写错了（传了立即调用的返回值 None 而不是回调），导致每次 setup 后服务被立即删除（`fordpass_cn.refresh_status` / `clear_tokens` 实际不可用），卸载时卸载栈里是 None 直接报错；已改为标准回调写法，服务现在真正随集成生命周期注册/注销；② 异常路径日志不再输出完整车辆对象——无 VIN 车辆被跳过时此前会把含手机号/用户 ID 的车辆字段写进 warning 日志，现在先过滤敏感字段再输出
- **v5.3.7**（2026-10-22）：**时间处理改用 Home Assistant 官方时区**——刷新时间记录、活动去重窗口、能力重探冷却三处时间改用 `dt_util.now()`（时区感知，跟随 HA 配置时区），此前用系统本地时间，在 HA 容器时区与宿主机不一致的环境下显示会偏差；行为不变
- **v5.3.6**（2026-10-22）：**修复卸载后后台任务未彻底停止**——① 卸载或重新加载集成时，正在执行的命令状态轮询、45 秒延迟补写活动、开关自动复位、数据落盘等后台任务现在一并取消（此前只停止定时刷新循环，这些任务会继续跑完：命令状态轮询期间仍会请求福特云、卸载后还会写无意义的活动日志）；② 车辆能力检测日志降为调试级别，减少默认日志输出量
- **v5.3.5**（2026-10-22）：**修整集成卸载与后台任务生命周期**——① 重新加载集成或卸载后，旧的后台轮询任务不再残留：此前卸载只会卸载实体，30 分钟定时刷新循环还会继续跑（多次重载会积累多份并发请求，卸载后也仍持续访问福特云），现在卸载时一并关闭轮询；② 命令状态轮询与 45 秒补写活动等后台任务全部加引用跟踪（快速操作、卸载重启等场景下任务不再可能被事件循环丢弃，开关自动复位与命令状态显示更可靠）；③ 两个服务（刷新车辆状态/清除令牌）随集成卸载一并注销，不再残留失效服务；④ 车辆清单启动日志降级为调试级别，减少默认日志输出量
- **v5.3.4**（2026-10-22）：**修复命令操作后服务信息实体短暂变空 + 三处健壮性加固**——① 车控命令（门锁/后备箱/发动机/声光寻车/鸣笛等）执行完成后，原来会用状态结果覆盖整个协调器数据，导致保养/滤芯/OTA/位置/告警等服务信息实体立即显示空值（最长 30 分钟轮询才恢复）；改为合并更新，命令执行后其它实体保持原值；② 令牌过期判定不再依赖错误文案里的「token」字样——福特云调整提示文案（如改为 Jwt expired）时令牌失效仍能正确触发刷新与重新认证，不会静默卡死；③ 车辆状态解析失败重试覆盖解密异常——瞬时解密失败现在也会自动重试一次，不再直接报错；④ 账号级共享缓存加并发锁（多车同时过期只真拉一次）；⑤ 声光寻车/鸣笛自动复位任务加引用跟踪（快速多次触发时任务不再可能被事件循环丢弃）；⑥ 设置类选择器云端监听改为实体成功加入后再注册，卸载时正常注销
- **v5.3.3**（2026-10-22）：**修复 401 刷新重试无限递归**——① 令牌刷新后重试仍返回 401（新令牌仍被拒、或刷新接口本身失效）时，旧代码会无限递归重试直至 Python 递归上限（数分钟挂起、几百次重复请求、报错噪音）；现在刷新只重试一次，仍失败直接触发重新认证提示，不再死循环；② 所有手写请求块（鸣笛/远程影像/车控命令等 16 处）刷新后重发仍 401 时统一带「需重新认证」标记，与轮询主路径行为一致；③ 远程影像图下载失败会记住失败的地址，前端不再每次打开都重复请求，新抓拍地址变化后照常拉新图（失败期间保留旧图）；④ 状态数据落盘任务加引用跟踪，重启/卸载时不再可能被事件循环丢弃
- **v5.3.2**（2026-10-22）：**代码审查第二轮修复**——① 停车影像/行车监控图按 URL 变化更新：此前首次抓拍后图片缓存不失效，再点「停车影像/行车监控」查询按钮显示的还是第一张图；现在新抓拍 URL 变化时自动拉取新图并覆盖本地缓存，拉取失败保留旧图（实体不显示不可用）；② 多车账号并发 401 修复：token 过期那一轮有多辆车同时刷新时，只有第一辆刷新成功、其余车辆直接报错；现在检测到已有刷新进行中会短等后自动重试，不再失败；③ 重新认证触发判定加固：不再靠异常文案字符串匹配，改由显式标志判定（文案调整不再误判）
- **v5.3.1**（2026-10-22）：**代码审查修复**——① 统一实体可用性策略：sensor/button/image 刷新失败不再显示不可用，与 switch/lock/select/device_tracker 对齐保留最后已知状态（此前同一轮刷新失败一半实体 unavailable）；② `clear_tokens` 服务同时清空配置中的令牌（只清内存会导致重载后旧令牌复活）；③ 林肯之道 4 个专属探测端点走持久化缓存（重载不再重复探测）；④ 空账号转配置错误（不再无限重试）；⑤ refresh_token 加锁 + 5 秒窗口缓存（多车并发 401 不再双刷新），云端轮换 refresh token 时自动回存；⑥ 账号级共享缓存提前到建立协调器时生效；⑦ 修复 options 落盘日志参数错位
- **v5.3.0**（2026-10-21）：**quality_scale 达标整改**——① 运行时数据迁移到 `ConfigEntry.runtime_data`（替代 `hass.data` 手工管理，多账号/卸载生命周期交给 HA）；② 服务失败抛异常：`refresh_status` 指定 VIN 不在账号车辆中时返回明确错误，自动化可感知；③ 配置表单字段增加说明文案（手机号/验证码/账号密码/品牌/登录方式）；④ 新增 `quality_scale.yaml` 进度跟踪与豁免声明（多账号、保留最后已知状态、无触发器/发现机制）；⑤ 文档补齐：配置参数表、自动化示例与用例、移除集成说明；⑥ 新增配置流测试骨架（品牌选择/验证码步骤/SMS 全路径/选项流，mock 云端 API）
- **v5.2.9**（2026-10-21）：**代码审查清理**——① ruff 全规则清零：7 个实体平台 6 处裸异常（`except: pass`）改为记录调试日志、lock/switch 4 处冗余异常包裹移除、import 排序整理；测试文件同步清理（编码声明/无用 noqa/import 排序）；② 代码与 README 注释去掉 HACS 审核历史引用，表述更直接；③ `.gitignore` 补 `.ruff_cache/`
- **v5.2.8**（2026-10-21）：**按官方质量分级补齐两项**——① 7 个实体平台（button/device_tracker/image/lock/select/sensor/switch）各加 `PARALLEL_UPDATES = 1`（单实体并行更新上限）；② 云端刷新失败日志只警告一次：首次失败正常记录（error 级），后续失败降为调试级——默认 30 分钟一轮轮询不再刷屏，恢复成功时提示一次；活动日志不受影响，每轮各实体刷新状态照常显示
- **v5.2.7**（2026-10-21）：**修复 HACS 审核二轮 2 项**——① LBS 定位 token 响应日志收敛：`get_lbs_token` 的 debug 日志不再打印响应正文（原 `text[:300]` 含 `data.encryptedAccessToken`，其解密密钥随集成发布，值进日志即等于泄露 token），改为只记状态码与 data 字段名；相关错误信息（HTTP 错误/non-JSON/缺 token）同步收敛，不带响应正文；② 清理旧版（<v5.2.4）写入 `/config/www/fordpass_cn` 的遗留车辆图片/泊车/行驶监控帧——/www 下内容无需认证可经 `/local/` 下载，setup 时在 executor 中删除一次该目录（删后不存在，天然只执行一次），新版本私有缓存（`.storage/fordpass_cn`）不受影响
- **v5.2.6**（2026-10-21）：**清理已移除按钮的残留实体**——v5.2.4/v5.2.5 移除的控件（分车门锁定×4、v5 主动刷新、v5 刷新轮询）"不再创建"≠"自动消失"，旧 registry 条目会一直 unavailable 占位；本次在 button 平台加入显式清理（限定本 entry、跳过用户禁用项，与 sensor 平台 v5.2.4 同款逻辑），重载后残留按钮自动移除
- **v5.2.5**（2026-10-21）：**移除 v5 刷新轮询与 v5 主动刷新车况按钮**——两按钮走 v5 PUT 命令通道（`PUT /api/vehicles/v5/{vin}/status`），载荷未经官方 App 抓包确认，与 v5.2.4 移除的猜测命令同类，一并移除（send-command 手动刷新按钮保留，命令载荷已确认）；顺带清理 coordinator 死属性 `_v5_refresh_cid`
- **v5.2.4**（2026-10-21）：**修复 HACS 审核 5 项问题**——① 车辆图片/远程影像/3D 图缓存从 `/config/www` 迁至 `/config/.storage` 私有目录（www 经 `/local/` 无认证可下载，任何知道 VIN 的人都能拉取图片）；② 实体状态与属性不再暴露完整手机号/用户 ID——车辆授权列表、家充桩分享优先显示昵称、手机号仅显示后四位掩码，SIM 传感器与 4 处原始响应复制属性的传感器统一过滤敏感字段；③ debug 日志不再记录请求体值（含 `encryptedRefreshToken`/`encryptedPasscode`/`encryptedAuthCode` 与 LBS token 的请求只记路径与字段名）；④ 传感器清理逻辑限定到本账号（entry）自己的实体，多账号时设置一个账号不再误删其它账号的传感器（重命名/区域/自定义不再丢失）；⑤ 移除未对照官方 App 抓包确认的「猜测命令」控件：分车门锁定、遥控泊车、远程关窗、哨兵模式、立即充电、车载冰箱、前备箱锁（待抓包确认载荷后恢复）
- **v5.2.3**（2026-10-20）：**代码审查 ruff 全规则清理**——修复审查发现的 16 项代码风格问题：合并冗余布尔判断与嵌套 if（SIM103/SIM102）、字典遍历改用 values()（PERF102）、去除冗余 int 转换（RUF046）、日志异常参数精简（TRY401），以及 4 处类级可变默认加 ClassVar 类型注解（单例缓存设计不变，行为不变）；Actions 规则（F/E9/B）与全规则复查全绿
- **v5.2.2**（2026-10-20）：**回退 HACS 下载方式**——v5.2.1 尝试启用 zip_release（下载发布压缩包）后，HACS 安装文件路径异常导致 HA 找不到集成（全部实体 unavailable）。v5.2.2 回退为原下载方式（仓库分支拉取），恢复稳定；代码内容（ruff 清理 + 文档措辞修正）与 v5.2.1 一致
- **v5.2.1**（2026-10-20）：**代码风格清理 + 文档措辞修正**——① 按 ruff 清理 40 余处代码风格问题（import 排序、类型注解写法等，功能行为不变）；② README 更新日志去掉分级编号等措辞，表述更直接；③ 尝试启用 zip_release 下载方式，因 HACS 安装路径异常在 v5.2.2 回退
- **v5.2.0**（2026-10-20）：**按钮「待命」兜底修复生效**——v5.1.9 在 async_added_to_hass 写入「待命」仍被 restore 流程覆盖（HA button 平台从未按过的按钮无历史状态时置 unknown，写入时序竞争）。v5.2.0 改为 override state property：读取时 _attr_state 为空/unknown 一律返回「待命」，按下后返回时间戳，不受恢复时序影响
- **v5.1.9**（2026-10-20）：**修复重启后未按过的按钮显示 unknown**——HA ButtonEntity 内置状态恢复：实体重启后恢复最后按下时间戳，但从未按过的按钮无历史状态，恢复流程把 state 置为 unknown（覆盖「待命」初始值，v5.0.6 修复在集成重载时有效、HA 完整重启后失效）。新增统一按钮基类：实体加入后无历史状态时写「待命」，有按下历史保持时间戳；覆盖全部 14 类按钮
- **v5.1.8**（2026-10-20）：**代码审查遗留问题全部修复**——① 账户级共享缓存：chirp_cloud/未读消息摘要是账户级数据，多车账号每轮只真拉一次，其余车辆命中共享缓存，不再每车重复请求；② 活动日志失败节流：连续 3 轮全部写入失败自动暂停活动写入 1 小时，服务恢复后自动解除——正常时每轮逐实体刷新状态活动照常显示，不受影响；③ 其余审查项（_is_err_resp 未定义 / get_vehicles 解密兜底 / FordPassCrypto 场景字典缓存 / 闭包晚绑定 / 空响应与业务拒绝区分 / reauth 手机号一致校验 / VIN 文件名白名单）逐项核对均已在之前版本修复；④ 新增 `CONTRIBUTING.md` 贡献指南（隐私红线/开发环境/PR 流程/版本规则）和通用 Bug 报告模板（`.github/ISSUE_TEMPLATE/bug-report.md`），报障时按模板填写即可
- **v5.1.7**（2026-10-20）：**新增服务层 + README 车型能力矩阵**——① 集成服务 `fordpass_cn.refresh_status`（可选 VIN 参数：缺省刷新账号下全部车辆、指定 VIN 只刷新该车）与 `fordpass_cn.clear_tokens`（清除令牌重置会话，重载后重新认证），自动化可直接调用；② README 新增「车型 × 能力矩阵表」（汽油/柴油、混动、纯电三列 + 特殊车型备注），车型支持范围直接可查
- **v5.1.6**（2026-10-19）：**消除重启后 system_log 补写 warning**——首轮补写特征日志由 warning 降级为 debug（重启后 system_log 不再刷“首轮补写开始/完成”两条 warning；功能不变，调试时开 debug 日志仍可查）
- **v5.1.5**（2026-10-18）：**修复重启后逐实体活动补写未生效**——v5.1.4 的补写触发条件按“实体未注册”判断，但重启/重载后实体条目仍常驻注册表（状态未写入），条件永不成立导致补写未执行；现改为按“本轮实际写入 0 条”触发，45 秒后自动补写一轮各实体刷新状态，并加补写执行日志便于诊断
- **v5.1.4**（2026-10-18）：**车辆识别码活动只显示 VIN**——“X 个实体暂无数据（未开通）”摘要不再关联车辆识别码实体（VIN 实体活动历史恢复只显示 VIN 值）；**修复重启后逐实体状态活动不显示**——HA 重启/集成重载后首轮刷新在实体注册前完成，逐实体活动写不了（只剩摘要），现改为实体注册完成后自动补写一轮各实体刷新状态（仅写本地活动，不额外请求云端）
- **v5.1.3**（2026-10-17）：**修复手动刷新状态活动双写**——逐实体状态活动统一由轮询完成后写一次（自动/手动共用），按钮层不再重复写，避免手动刷新后每条状态活动出现两遍
- **v5.1.2**（2026-10-17）：**每次轮询逐实体显示刷新状态**——自动轮询（默认每 30 分钟）与手动刷新一样，为各实体写“实体名：状态值”活动（此前只写一条“车辆状态刷新成功”摘要），活动页随时可看到每个实体最近一次刷新后的真实状态；不再依赖“车辆信息”传感器
- **v5.1.1**（2026-10-17）：**修复活动页“来源”过滤视图看不到轮询摘要**——常规轮询的“车辆状态刷新成功”摘要改关联车辆识别码（VIN）实体（此前关联的“车辆信息”传感器已删除导致摘要无实体关联、被集成设备页过滤不可见），现在每轮摘要可在集成活动页正常显示
- **v5.1.0**（2026-10-17）：**活动页不再显示「不可用」刷新条目**——手动刷新后逐实体写活动时跳过 unavailable/unknown/空状态实体（如鸣笛类型/鸣笛持续时长/车辆定位/未读消息等无实际值的实体），活动列表只显示有实际状态的刷新记录，减少无意义噪音
- **v5.0.9**（2026-10-17）：**恢复设备页右上角显示燃油量**——HA 设备页主显示机制只认 `device_class=battery` 传感器（前端 `findBatteryEntity` 硬规则，图标固定电池样式），v5.0.7 改 None 导致右上角不再显示油量；恢复 battery 后右上角显示油量数值（电池图标为 HA 官方固定样式，无法自定义）。按车型：油车显示燃油量、纯电显示动力电池电量、混动显示燃油量（电量实体并列保留）
- **v5.0.8**（2026-10-17）：**设备页主显示按车型匹配**——汽油车显示燃油量、纯电车显示动力电池电量、混动车油量与电量并列（HA 设备页取第一个 sensor 实体，注册顺序按车型重排）；燃油量图标不再误用电池
- **v5.0.7**（2026-10-17）：**传感器规范修复**——数值传感器补充 `state_class: measurement`（燃油量/续航/电压/里程/机油寿命/胎压等），启用长期统计与趋势图；修复燃油量 device_class（不再误显示电池图标）
- **v5.0.6**（2026-10-17）：**修复分车门锁定按钮显示 unknown**——所有按钮实体增加初始状态「待命」（HA button 平台默认按下前无状态）；顺带修复 S110 裸异常
- **v5.0.5**（2026-10-17）：**README 法律敏感叙述清理 + 视频说明书卡片模板完善**——
  ① 删除 README 中私有二进制/密钥素材相关法律敏感叙述
  ② 白盒加密技术说明精简为纯 Python 查表实现
  ③ 视频说明书 FAQ 升级为完整可复制的 Markdown 卡片（含查找实体 ID 步骤 + YAML 模式添加方式），并注明 sensor 状态不可点击需用卡片
- **v5.0.4**（2026-10-17）：**CI 修复**——manifest 键序修正（quality_scale 移入字母序位置，hassfest 校验通过）；
  tests/test_wbsk.py 移除未用 import pytest 补入 main（ruff 全绿）；v5.0.3 的两个 CI 失败项在 v5.0.4 全绿
- **v5.0.3**（2026-10-17）：**仓库合规整改（PR 审核准备）+ 9 个功能 bug 修复**——
  ① 新增 GitHub Actions `hassfest` + `hacs` 两个官方校验工作流（持续全绿）
  ② README 新增英文安装/功能说明（英文优先，符合 HACS 默认仓库审核要求）
  ③ 修复 api.py 缺失的 9 个路径常量 import（IDUL 分车门锁定、v5 状态刷新、林肯会员/我的页面、lwosb 维保预约、家充桩蓝牙添加——此前点击按钮即 NameError）
  ④ 清理测试未使用导入；ruff 全绿 + pytest 14 通过
- **v5.0.2**（2026-10-17）：**视频说明书改为可点击链接**——状态显示「查看视频说明书」短文案（不再刷屏长 URL），完整链接在属性 `manual_url`；README 常见问题新增 Markdown 卡片配置模板，点击即跳浏览器打开。
- **v5.0.1**（2026-10-17）：**所有实体按车型+按数据非空创建**——19 个探测实体（全车型 6 种 + 充电域 13 种）由无条件创建改为「探测数据非空才创建」，无数据不显示「暂无数据」占位噪音；旧实体由清理逻辑自动移除。
- **v5.0.0**（2026-10-16）：**README 优化与免责声明**——重写精炼 README（功能分组、常见问题、精简实体表）；免责声明扩容为五项（非官方/仅供研究/风险自担/数据本地/下架配合）。
- **v4.9.9**（2026-10-16）：**清理不必要代码**——移除 9 个文件中的未使用导入；无功能变更。
- **v4.9.8**（2026-10-16）：**修复重新加载弹错**——setup 首次刷新加 20s 短超时，快速转 ConfigEntryNotReady 自动重试。
- **v4.9.7**（2026-10-16）：**第二轮合规整改**——探测/诊断传感器补 `entity_category: diagnostic`；补全 pytest 单测 14 项全过。
- **v4.9.6**（2026-10-16）：**HA 规范合规整改**——移除用户可配置刷新间隔（固定 30 分钟）；删除调试服务残留；README 大幅精简。
- **v4.9.5**（2026-10-16）：新增 `diagnostics.py`；根 `CODEOWNERS`；`async_migrate_entry`。
- **v4.9.4**（2026-10-16）：manifest 增加 `quality_scale: bronze`。
- **v4.9.3**（2026-10-16）：所有实体按车型能力创建。
- **v4.9.2**（2026-10-16）：清理 data/ 无用文件（零外部依赖）。
- **v4.9.1**（2026-10-16）：定位传感器 lat/lon 属性数值化。

</details>
