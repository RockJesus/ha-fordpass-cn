<div align="center">

<img src="custom_components/fordpass_cn/brand/logo.png" alt="FordPass China / Lincoln Way" width="380">

# FordPass China 福特派互联 / Lincoln Way 林肯之道

**把爱车接进 Home Assistant** —— 远程控车 · 车况监控 · 定位追踪 · 预约充电 · EVCC 联动

[![HACS 默认仓库](https://img.shields.io/badge/HACS-%E9%BB%98%E8%AE%A4%E4%BB%93%E5%BA%93-41BDF5?style=flat-square)](https://github.com/hacs/default)
[![最新版本](https://img.shields.io/github/v/release/RockJesus/ha-fordpass-cn?style=flat-square&color=2ea44f)](https://github.com/RockJesus/ha-fordpass-cn/releases/latest)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2025.1%2B-03A9F4?style=flat-square)](https://www.home-assistant.io/)
[![Ruff](https://img.shields.io/github/actions/workflow/status/RockJesus/ha-fordpass-cn/ruff.yml?style=flat-square&label=Ruff)](https://github.com/RockJesus/ha-fordpass-cn/actions/workflows/ruff.yml)
[![许可证](https://img.shields.io/github/license/RockJesus/ha-fordpass-cn?style=flat-square)](LICENSE)

[功能特性](#-功能特性) · [安装](#-安装) · [配置](#-配置) · [服务](#-服务) · [支持的实体](#-支持的实体) · [自动化示例](#-自动化示例) · [EVCC 联动](#-与-evcc-联动) · [故障排查](#-故障排查) · [隐私与安全](#-隐私与安全) · [版本与更新](#-版本与更新)

</div>

---

Home Assistant 自定义集成，接入**福特派互联（长安福特）**与**林肯之道**云服务：远程控车、车辆状态监控、定位追踪、预约出发/充电、家充桩管理、远程影像。实体按本车云端能力自动创建，多 VIN 账号下每辆车各自拥有独立的实体组。

> ⚠️ 本项目为个人开发作品，与福特汽车（中国）有限公司、长安福特汽车有限公司、林肯中国**无任何关联**。使用即表示自行承担相关风险。

<details>
<summary><b>English summary</b></summary>

Custom Home Assistant integration for **FordPass China (Changan Ford)** and **Lincoln Way (China)** cloud services. Unofficial community project, interoperating with the same cloud services the official mobile apps use.

- **Remote control** — lock / unlock / remote start / stop / horn / lights / manual refresh
- **Vehicle status** — fuel & range, odometer, tyre pressure, doors & windows, ignition, alerts
- **Smart features** — departure scheduling & charging (EV/PHEV/HEV), wallbox management, remote image, OTA status, maintenance plan, recall info
- **Per-vehicle entities** — entities are created per vehicle capability, so no useless entities appear
- **Two brands** — FordPass CN and Lincoln Way share the same cloud crypto; brand is picked on the first config step
- **Login** — SMS passcode (both brands) or username/password (FordPass only, browser-assisted)
- **Zero dependencies** — pure Python, no extra packages

**Install**: HACS → search "福特派互联" → Download → restart. Or add this repository as a custom integration repository. Then add the integration "FordPass China 福特派互联".

> Disclaimer: independent, non-official project for research and personal use only. No affiliation with Ford Motor Company. Use at your own risk.

</details>

## ✨ 功能特性

**🔑 登录与账号**
- 双品牌：登录第一步选择「福特派 FordPass」或「林肯之道 Lincoln Way」
- 双登录方式：手机号短信验证码（无需密码）、用户名密码（浏览器辅助换码）——**林肯之道仅支持短信验证码**
- 令牌自动续期，过期时集成主动弹出重新认证；云端账号不受影响，官方 App 照常使用

**🔐 远程控车**
- 上锁 / 解锁 / 远程启动 / 远程熄火 / 鸣笛寻车 / 声光寻车 / 灯光寻车 / 手动刷新车辆状态
- 鸣笛持续时长与类型可选（雨落荷叶 / 急浪拍岸 / 汽笛长鸣 / 空谷回音 / 声光共舞），选择即云端保存、参数直达车机
- 分车门锁定、遥控泊车、远程关窗、哨兵模式、车载冰箱、前备箱锁、拖车灯光检测（皮卡）

**⚡ 充电与能源**（纯电 / 插混 / 混动车型）
- 预约出发 / 预约充电 / 立即充电 / 停止充电 / 充电枪锁解锁
- 家充桩 BLE 自动认证：授权后插枪即由桩与车 BLE 自动配对认证（手机不在场也能启动充电）
- 家充桩分享与撤销、充电记录管理

**🚗 车辆状态**
- 门锁、报警、燃油、胎压（四轮 + 备胎）、里程、车窗、车门、自动熄火倒计时
- 车辆异常警示：真实告警接口明文中文显示（如「胎压监测系统警告」）
- 保养计划 / 召回信息 / SIM 卡 / WiFi 热点 / 空调滤芯 / 机油寿命 / 慢漏气胎 / 预测性诊断 / 紧急联系人档案
- 车辆服务信息：云端能力位图、救援/客服/售后电话、电子说明书

**📍 定位与影像**
- GPS 车辆定位（设备追踪器，HA 地图可显示，可选 WGS-84 / GCJ-02）
- 车辆图片 / 3D 车型图、行车记录视频录制启停（带记录仪硬件的车型）

**🧩 能力自适应**
- 实体全部数据驱动：登录后按本车云端能力与车辆数据判断，拿到有效数据才创建，**不制造静默无效实体**
- 不支持的命令按下即返回网关明确报错；刷新失败保留最后已知状态
- 命令失败黑名单带 90 天自动过期，云端后续放开的能力会自动补建实体，选项页可一键重置

> **蓝牙域边界**：汽车 BLE 车控指令（一键制冷/制热、熄火、门锁、后备箱、车窗、寻车、前备箱、举升顶、车窗通风）与 PAAK 数字钥匙是**纯 BLE 物理链路**（需手机在车旁约 10 米内并建立安全会话），云端无 HTTP 通道，因此本云集成无法执行——支持 BLE 的车型请用手机在车旁操作官方 App。云端可用的蓝牙域能力（家充桩 BLE 自动认证）已接入。

## 📦 安装

### 方式一：HACS（推荐）

本集成已收录于 **HACS 官方默认仓库**，点下方按钮可直接在 Home Assistant 中打开并一键安装：

[![在 HACS 中打开](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=RockJesus&repository=ha-fordpass-cn&category=integration)

> 按钮不可用？手动操作：HACS → 集成 → 搜索「**福特派互联**」→ 下载 → 重启 Home Assistant。

### 方式二：手动安装

1. 在 [Releases](https://github.com/RockJesus/ha-fordpass-cn/releases/latest) 下载最新源码包
2. 将 `custom_components/fordpass_cn/` 整个目录放入 HA 的 `/config/custom_components/` 下
3. 重启 Home Assistant

> 首次启动会自动下载一份约 400KB 的运行时数据包，校验通过后缓存到 `.storage/`，之后不再重复下载。完全离线的环境请参考 [故障排查](#-故障排查) 中的自备数据方式。

### 环境要求

| 项 | 要求 |
|---|---|
| Home Assistant | **2025.1 及以上**（`hacs.json` 声明的最低版本；已在 **2026.10.4** 实测）。更早版本走兼容分支，未逐一验证 |
| Python 依赖 | **零外部依赖**（`requirements: []`），无需安装任何额外包 |
| 网络 | 需能访问福特 / 林肯中国云端接口 |

## 🚀 配置

<div align="center">

[![开始配置](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=fordpass_cn)
[![打开设备与服务](https://my.home-assistant.io/badges/integrations.svg)](https://my.home-assistant.io/redirect/integrations/)

</div>

1. **设置 → 设备与服务 → 添加集成**，搜索「福特派互联」（或点上方「开始配置」直接进入）
2. **选择品牌**：福特派 / 林肯之道
3. **选择登录方式**
   - **短信验证码**（两种品牌都支持）：输入手机号 → 获取验证码 → 填入
   - **用户名密码**（仅福特派）：福特登录页有前置安全防护，纯 HTTP 客户端会被指纹拦截，因此走**浏览器辅助**——按向导在浏览器打开登录链接，完成后把地址栏 `?code=xxx` 粘贴回集成（一次性有效）
4. 完成登录后，可按需在**集成选项**中调整：

| 选项 | 默认值 | 说明 |
|---|---|---|
| 启用车辆定位追踪 | 开 | 关闭后不再创建 / 更新 `device_tracker` |
| 坐标体系 | WGS-84 | `WGS-84`（通用）或 `GCJ-02`（国内地图偏移系） |
| 重置车型能力黑名单 | 关 | 一次性动作：清空已记录的「不支持命令」，重启后按车型能力重建实体 |

> 手机号 / 品牌 / 登录方式在集成「重新配置」入口维护；令牌失效时集成会自动弹出重新认证。

## 🧰 服务

全部服务可在自动化、脚本、`service:` 动作中直接调用。

| 服务 | 参数 | 说明 |
|---|---|---|
| `fordpass_cn.refresh_status` | `vin`（可选） | 立即请求云端刷新车况。缺省刷新账号下全部车辆，指定 VIN 只刷该车；VIN 不属于当前账号时报错。云端刷新为命令式，响应约 30–60 秒 |
| `fordpass_cn.clear_tokens` | — | 清除已保存令牌并重置会话，重载集成后需重新认证。用于登录异常、令牌疑似失效 |
| `fordpass_cn.share_wallbox` | `phone`（必填）、`vin`、`wallbox_id` | 把家充桩分享给家人；`wallbox_id` 缺省取默认绑定桩 |
| `fordpass_cn.create_departure` | `vin`、`structure` | 保存 / 新建预约出发充电计划；`structure` 缺省时按云端现有计划回填 |
| `fordpass_cn.update_srs` | `vin` | 保存 / 更新紧急联系人（SRS）档案 |
| `fordpass_cn.approve_auth` | `approve`、`vin` | 审批他人对你这辆车的授权申请，`approve` 默认 `true` |

> 写入类服务（分享家充桩、预约计划、授权审批）会直接改云端状态，建议先在官方 App 中确认语义再接入自动化。

## 📊 支持的实体

### 车型 × 能力矩阵

> ✔ = 该车型支持；✘ = 不支持；「按车型」= 实体按云端能力位图 / 命令白名单自动创建，非该能力车型不出现。燃料类型取自车辆业务字段 `fuelType`：纯油（G/D）、混动（PHEV/HEV/MHEV）、纯电（BEV）。

| 功能域 | 汽油 / 柴油 | 混动 (PHEV/HEV/MHEV) | 纯电 (BEV) | 备注 |
|---|:---:|:---:|:---:|---|
| 远程控车（锁 / 解锁 / 启动 / 熄火 / 鸣笛·声光·灯光寻车 / 刷新） | ✔ | ✔ | ✔ | 按云端命令白名单创建 |
| 拖车灯光检测 | 皮卡 ✔ | — | — | 按车型 |
| 预约出发 / 预约充电 | ✘ | ✔ | ✔ | 充电域 |
| 立即充电 / 停止充电 / 充电枪锁 | ✘ | ✔ | ✔ | |
| 家充桩 BLE 自动认证 | ✘ | ✔ | ✔ | 授权后插枪即自动配对 |
| 燃油 / 续航 | ✔ / ✔ | ✔ / ✔ | ✘ / 电池续航 | 油量与电量实体独立 |
| 车况监控（里程 / 胎压 / 门窗 / 点火 / 报警 / 中文异常警示） | ✔ | ✔ | ✔ | 全车型核心状态 |
| 服务信息（保养 / 召回 / SIM / WiFi / 滤芯 / 机油寿命 / 客服电话 / 说明书） | 按车型 | ✔ | ✔ | |
| 影像（车辆图片 / 3D 车型图 / 行车记录） | 按车型 | 按车型 | 按车型 | 行车记录按车载硬件 |

### 平台 × 实体

| 平台 | 实体 | 说明 |
|---|---|---|
| `device_tracker` | 车辆定位 | GPS 坐标 + 地址属性，地图可显示 |
| `image` | 车辆图片 / 3D 车型图 | 云端返回图片地址才创建 |
| `lock` | 车门锁 / 后备箱锁 | 后备箱锁按尾门能力创建 |
| `button` | 手动刷新 / 中央区灯光 / 辅助设置 / OTA 激活排程 / 重置空调滤芯 / 拖车灯光检测 / 标记消息已读 / 行车记录启停 | 均按云端能力创建 |
| `switch` | 远程启动 / 灯光寻车 / 鸣笛寻车 / 声光寻车 / 预约充电 | 鸣笛寻车类型 0–3 |
| `select` | 鸣笛持续时长 / 鸣笛类型 | 选择即保存，鸣笛时参数直达车机 |
| `event` | 车辆告警事件 / 车门开合事件 | 告警按标题去重、出现新条目才触发；车门变动一次发一个事件，`changes` 属性列出全部变化的门位 |
| `update` | 车机固件 | OTA 回读到版本号才创建，仅展示（固件下发需在车机或 App 确认） |
| `sensor` | 车况 / 异常警示 / 保养计划 / 召回 / SIM / WiFi / 空调滤芯 / 机油寿命 / 预测性诊断 / 车辆服务信息 / 鸣笛设置 / 在线状态 / 设备信息 / 授权状态 / SRS 档案 / 传感器影子 / 行车记录 / 最新消息 / OTA 提醒 / 车辆清单 / 消息中心 / 共享列表 / 设备列表 / 质保信息 / 车辆能力清单 / 车辆信息 / 维保可约资源 | 一次性探测，探测到数据才创建 |

> **实体 ID 前缀不是 VIN**：实体 `unique_id` 与设备标识统一使用 `sha256(VIN)` 前 16 位十六进制（稳定、不可逆），因此实体形如 `sensor.3f9a1c0b7d2e4a58_fuel_level`。找实际 ID 请到「设置 → 设备与服务 → 实体」按中文名搜索，或在开发者工具中按中文名筛选——不要试图从 VIN 推导。

## 📈 数据统计与云端健康

- **长期统计**：油量、电量、总里程、续航里程、纯电续航、机油寿命等均为数值传感器（`measurement` + `distance`/`battery` 分类），在「开发者工具 → 统计」中可直接看趋势图。
- **可进趋势图的分类**：燃油量 / 动力电池电量 / 尿素液位（%）、总里程 / 续航 / 纯电续航 / 尿素续航（km）、四轮胎压 + 推荐胎压（kPa）、蓄电池电压（V）、车内温度（°C）、机油寿命（%）、上次充电电量（%）、自动熄火剩余时间（分钟）。
- **云端同步状态**：每辆车都有一个诊断传感器，复用每轮刷新的结果（**零额外请求**）。连续失败自动转「异常」，属性带最后成功/失败时间、连续失败次数与失败摘要（限流 / 风控拦截 / 网关 / 业务错误已分级），可接自动化告警。

## 🤖 自动化示例

出门前检查车辆状态：

<details>
<summary>出门前检查车辆状态（自动化 YAML）</summary>

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
        油量 {{ states('sensor.<前缀>_fuel_level') }}%，续航
        {{ states('sensor.<前缀>_driving_range') }} 公里
```

</details>

新增告警时推送（`event` 实体出现时才可用）：

<details>
<summary>新增车辆告警时推送（自动化 YAML）</summary>

```yaml
alias: 车辆新增告警通知
trigger:
  - platform: state
    entity_id: event.<前缀>_vehicle_alert_event
    not_to: ["unknown", "unavailable"]
condition:
  - condition: template
    value_template: "{{ trigger.to_state.attributes.event_type == 'alert' }}"
action:
  - service: notify.mobile_app
    data:
      title: "{{ trigger.to_state.attributes.headline }}"
      message: >-
        等级 {{ trigger.to_state.attributes.severity }}｜
        {{ trigger.to_state.attributes.body }}（共
        {{ trigger.to_state.attributes.total_count }} 项告警）
```

</details>

夜间忘锁车提醒：

<details>
<summary>夜间忘锁车提醒（自动化 YAML）</summary>

```yaml
alias: 夜间锁车提醒
trigger:
  - platform: time
    at: "23:00:00"
condition:
  - condition: state
    entity_id: lock.<前缀>_door_lock
    state: "unlocked"
action:
  - service: notify.mobile_app
    data:
      message: 车门未锁，请确认
```

</details>

云端同步异常监控（后端接口变更 / 风控拦截第一时间发现）：

<details>
<summary>云端同步异常监控（自动化 YAML）</summary>

```yaml
alias: 福特云端同步异常提醒
trigger:
  - platform: state
    entity_id: sensor.<前缀>_sync_health      # 「云端同步状态」
    to: "异常"
action:
  - service: notify.mobile_app
    data:
      title: 福特派同步异常
      message: >
        云端同步连续失败，请检查福特服务器 / 账号状态。
        失败摘要：{{ state_attr('sensor.<前缀>_sync_health', 'last_failure') }}
```

</details>
> 恢复后自动回到「正常」，无需人工复位。

胎压告警通知：

<details>
<summary>胎压告警通知（自动化 YAML）</summary>

```yaml
alias: 胎压告警提醒
trigger:
  - platform: state
    entity_id: sensor.<前缀>_tpms_system_status    # 「胎压系统状态」
    to: "Fault"
action:
  - service: notify.mobile_app
    data:
      message: >
        胎压系统报警！左前 {{ states('sensor.<前缀>_lf_tire') }} kPa，
        右前 {{ states('sensor.<前缀>_rf_tire') }} kPa，
        左后 {{ states('sensor.<前缀>_lr_tire') }} kPa，
        右后 {{ states('sensor.<前缀>_rr_tire') }} kPa
```

</details>

保养到期提醒：

<details>
<summary>保养到期提醒（自动化 YAML）</summary>

```yaml
alias: 保养到期提醒
trigger:
  - platform: time
    at: "10:00:00"
condition:
  - condition: template
    value_template: "{{ '保养' in states('sensor.<前缀>_maintenance_plan') }}"
action:
  - service: notify.mobile_app
    data:
      title: 车辆保养提醒
      message: "{{ states('sensor.<前缀>_maintenance_plan') }}"
```

</details>

> 示例中的 `<前缀>` 是本车 VIN 的 sha256 前 16 位（见上文「实体 ID 前缀」说明），实际取值请在「设置 → 设备与服务 → 实体」中按中文名复制。

## 🎛 仪表盘卡片示例

推荐搭配 **Vehicle Status Card**（HACS → Frontend 搜索安装）把实体拼成车况面板。在仪表盘「原始配置编辑器」中添加：

<details>
<summary>Vehicle Status Card 卡片配置（YAML）</summary>

```yaml
type: custom:vehicle-status-card
vehicle: 我的车
image: /local/fordpass_cn/vehicle.png        # 可选：自定义车辆图片
entities:
  - entity: lock.<前缀>_door_lock           # 门锁
    label: 门锁
  - entity: sensor.<前缀>_fuel_level        # 燃油量 %
    unit: "%"
  - entity: sensor.<前缀>_driving_range     # 续航 km
    unit: km
  - entity: sensor.<前缀>_odometer          # 总里程 km
    unit: km
  - entity: sensor.<前缀>_lf_tire           # 左前胎压 kPa
    unit: kPa
  - entity: sensor.<前缀>_rr_tire           # 右后胎压 kPa
    unit: kPa
buttons:
  - entity: button.<前缀>_refresh           # 刷新车辆状态
  - entity: button.<前缀>_horn              # 鸣笛（按车型）
  - entity: button.<前缀>_remote_start      # 远程启动（按车型）
map:
  entity: device_tracker.<前缀>_location    # 车辆定位
```

</details>

> 不同车型可用按钮不同，在开发者工具按 `button.` 前缀查询实际实体；定位实体需在集成选项中开启「车辆定位追踪」。卡片字段以 Vehicle Status Card 官方文档为准。

## ⚡ 与 EVCC 联动

纯电 / 插混车型的充电相关实体已按 EVCC（开源充电调度器）可直接消费的格式标准化，配合 EVCC 的 Home Assistant 数据源即可实现自动充电调度。

| 实体 | 类型 | 说明 |
|---|---|---|
| `sensor.<前缀>_battery_fill_level` | 数值 % | 动力电池电量（SOC），`device_class=battery`，自动进长期统计 |
| `sensor.<前缀>_prognostic_range` | 数值 km | 剩余可行驶里程，`device_class=distance`、`state_class=measurement` |
| `sensor.<前缀>_ev_dte` | 数值 km | 纯电续航 |
| `sensor.<前缀>_distance_to_empty` | 数值 km | 续航里程 |
| `sensor.<前缀>_odometer` | 数值 km | 总里程 |
| `sensor.<前缀>_charging_status` | 文本 + 数值属性 | 充电状态（充电中 / 未充电 / 已充满），属性 `evcc_charge_state`：0 未知 / 1 未充电 / 2 充电中 / 3 已充满 |
| `switch.<前缀>_wallbox_charge` | 开关 | 家充桩充电控制（仅绑定桩车型） |

EVCC 侧示例（`evcc.yaml`，按你的 EVCC 版本微调）：

<details>
<summary>EVCC 车辆配置（evcc.yaml）</summary>

```yaml
vehicles:
  - name: mach_e
    type: template
    template: mach-e
    source: homeassistant
    title: 电马
    soc:
      source: homeassistant
      entity: sensor.<前缀>_battery_fill_level
    range:
      source: homeassistant
      entity: sensor.<前缀>_prognostic_range
    chargeState:
      source: homeassistant
      entity: sensor.<前缀>_charging_status
      attribute: evcc_charge_state
    enable: switch.<前缀>_wallbox_charge
```

</details>

> `evcc_charge_state` 语义与 EVCC 一致。家充桩充电开关仅在绑定桩的车型出现。

## 🛠 故障排查

<div align="center">

[![查看实体状态](https://my.home-assistant.io/badges/developer_states.svg)](https://my.home-assistant.io/redirect/developer_states/)
[![查看系统日志](https://my.home-assistant.io/badges/logs.svg)](https://my.home-assistant.io/redirect/logs/)

</div>

| 现象 | 处理 |
|---|---|
| 密码登录失败 / 被拦截 | 官方登录页对纯 HTTP 客户端有安全防护，**密码登录需走浏览器辅助**：按向导第 2 步在浏览器打开登录链接完成登录，把地址栏的授权码参数粘贴回集成（一次性有效）。被拦截或不便时**改用短信验证码登录**，登录后令牌自动续期 |
| 某车型实体没有创建 | 集成按云端能力 / 车型字段判断，**接口返回正常 ≠ 车型支持**；不支持的功能不创建假实体，属正常设计 |
| 重新加载弹「Setup ... cancelled」 | setup 首次刷新有 20 秒短超时，超时转 `ConfigEntryNotReady` 自动重试 |
| 提示数据包获取失败 | 首次启动需能访问数据源。三种补齐方式任选：① 什么都不做，HA 会退避重试，补齐后自动恢复无需手动重载 ② 把数据包解压到 `config/fordpass_cn_data/` ③ 设 `FORDPASS_DATA_URL` 指向可达地址，或用 `FORDPASS_DATA_DIR` 指向本地目录（完全离线）后重启 |
| 升级后无法登录 / 认证失败 | 云端接口调整后需发布新版本适配，请关注 Releases |
| 实体状态长期不变 | 后台刷新固定 30 分钟；可点「手动刷新车辆状态」按钮强制车机上报 |
| 日志出现 `took longer than scheduled update interval` | 重启 / 重载初始窗口的瞬时现象，锁与开关实体已改为后台调度刷新、立即返回，若仍出现请附日志提 issue |
| 视频说明书网址太长 / 想点击打开 | 状态显示「查看视频说明书」，完整 URL 在属性 `manual_url`。sensor 状态文字本身不可点击（HA 平台限制），用下面的 Markdown 卡片实现点击跳转 |

<details>
<summary>视频说明书可点击卡片（YAML）</summary>

```yaml
type: markdown
content: >-
  [📖 打开视频说明书]({{ state_attr('sensor.<你的视频说明书实体>', 'manual_url') }})
```

</details>

替换 `<你的视频说明书实体>`：**设置 → 设备与服务 → 福特派 → 实体 → 找到「视频说明书」→ 复制实体 ID**。添加方式：仪表盘右上角编辑 → 添加卡片 → 手动 / YAML 模式 → 粘贴 → 保存。

## 🔒 隐私与安全

本仓库与运行时的个人信息防护措施：

- **仓库零个人信息**：真实 VIN、手机号、密码、车牌一律使用假数据（假 VIN `LVSHFAKE000000001`、示例号码）；真实格式 VIN（17 位）禁止入库。
- **实体标识脱敏**：实体 `unique_id` 与设备 `identifiers` 使用 `sha256(VIN)` 前 16 位确定性哈希前缀，entity / device registry 中不出现真实 VIN。
- **活动历史不落隐私**：VIN、车牌号、车辆定位三个实体的状态值不写入 logbook（按 `unique_id` 尾缀精确跳过）；定位 `device_tracker` 状态为 zone（`home` / `not_home`），坐标仅在属性中。
- **日志掩码**：debug 日志对 VIN、经纬度、地址做掩码（VIN 保留首 4 末 4）；车辆图片缓存文件名用摘要，不含 VIN。
- **仓库自带审计脚本**：`docs/privacy_audit.py` 覆盖全 git 可达历史、commit message、Releases body、codeload 归档与 dangling commit，输出 PASS 方可发布。
- **互操作固定值随数据包下发（v6.2.1）**：云端互操作所需的 App 固定值（LBS 网关标识、应用 ID、客户端/OS 标识、签名密钥等）不再硬编码于集成代码，随运行时数据包 `app_constants.json` 下发并按 SHA-256 校验，缺失时按既有降级策略处理。
- **数据本地**：车辆数据仅保存在你自己的 Home Assistant 中，集成除福特 / 林肯官方服务端外不向任何第三方传输。首次启动会向数据源发起一次 GET 请求下载运行时数据包（约 400KB，请求不携带 VIN、账号或任何车辆信息），校验通过后缓存本地，之后不再重复下载。

> **建议把隐私实体排除出历史库**：集成保证这三个实体不写 logbook 活动，但它们的**状态与属性仍会进入 HA 数据库**（recorder）。若不希望车牌、VIN、定位坐标留在历史记录里，在 `configuration.yaml` 中追加下面的配置：

<details>
<summary>recorder 隐私排除配置（YAML）</summary>

```yaml
recorder:
  exclude:
    entity_globs:
      - sensor.<前缀>_vehicle_vin        # 车辆识别码
      - sensor.<前缀>_license_plate      # 车牌号
      - device_tracker.<前缀>_location   # 车辆定位（经纬度在属性中）
```

`<前缀>` 是本车 VIN 的 sha256 前 16 位，在「设置 → 设备与服务 → 实体」中复制实际实体 ID 更稳妥。修改 recorder 配置后需**重启** Home Assistant；已产生的历史不会自动清除，可用 `recorder.purge_entities` 服务手动清理。

</details>

## 🗑 移除集成

1. **设置 → 设备与服务** → 找到「福特派互联」条目 → 删除该配置（其下设备与实体一并移除）
2. 若要同时卸载代码：HACS → 已下载 → FordPass China → 卸载；或删除 `/config/custom_components/fordpass_cn/` 后重启
3. 车辆图片等缓存位于 `.storage` 私有目录，随集成删除一并清理
4. 云端账号不受影响，官方 App 照常使用

## 📜 版本与更新

**变更记录统一发布在 GitHub Releases**，本 README 不再维护更新日志：

- 全部版本与变更说明 → [Releases](https://github.com/RockJesus/ha-fordpass-cn/releases)
- 当前版本 → 见顶部版本徽章，或 HACS「已下载」列表
- 升级方式 → HACS → 福特派互联 → **更新**（或「重新下载」）→ 重启 Home Assistant
- 需要强制升级的接口变更会在 Release 标题中标注

## ⚠️ 免责声明

1. **非官方项目**：与福特汽车（中国）有限公司、长安福特汽车有限公司、林肯中国及任何官方主体**无任何关联、未经任何授权**；「福特派」「林肯之道」等商标归各自权利人所有，本文引用仅用于识别所对接的服务。
2. **仅供学习研究**：仅限个人学习研究与自用，请勿用于商业用途。
3. **风险自担**：使用本集成可能导致账号风控、功能失效、违反服务条款等后果，一切风险与责任由使用者自行承担；作者不提供任何服务可用性担保。
4. **数据本地**：车辆数据仅存储于你自己的 Home Assistant 中。
5. **下架配合**：若相关权利方要求移除相关素材或本仓库，作者将配合下架。

---

<div align="center">

✍️ 作者博客：[rockjesus.cn](https://rockjesus.cn)

</div>
