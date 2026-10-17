# FordPass China 福特派互联 Home Assistant 集成

![brand](custom_components/fordpass_cn/brand/logo.png)

[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![GitHub release](https://img.shields.io/github/v/release/RockJesus/ha-fordpass-cn.svg)](https://github.com/RockJesus/ha-fordpass-cn/releases)
[![HA](https://img.shields.io/badge/Home%20Assistant-2024.6%2B-blue.svg)](https://www.home-assistant.io)

Custom Home Assistant integration for **FordPass China (Changan Ford) / Lincoln Way (China)** cloud services. Unofficial, reverse-engineered from the FordPass CN / Lincoln Way mobile app.

## Features

- **Remote control** — lock / unlock / remote start / stop / honk / lights / manual refresh
- **Vehicle status** — fuel & range, odometer, tire pressure, doors & windows, ignition, location tracking, alerts
- **Smart features** — departure scheduling & charging (EV/PHEV/HEV), wallbox management, remote image, OTA status, maintenance plan, recall info
- **Per-vehicle entities** — entities are created per vehicle capability (fuel type, cloud feature bitmap), no useless entities
- **Two brands** — FordPass CN and Lincoln Way share the same cloud encryption; brand is selected at first login step
- **Two login methods** — SMS passcode or username/password (Azure B2C)

## Installation

1. In Home Assistant go to **Settings → Devices & Services → HACS → ⋯ → Custom repositories**
2. Add `https://github.com/RockJesus/ha-fordpass-cn` with category **Integration**
3. Click **Download** (or use the `update` entity to update later versions), then **restart Home Assistant**
4. Add the integration via **Settings → Devices & Services → Add Integration → "FordPass China 福特派互联"**

> Disclaimer: This is an independent, non-official project for research and personal use only. Use it at your own risk. No affiliation with Ford Motor Company.

---
Home Assistant 自定义集成，接入福特中国（长安福特）福特派互联服务：支持**手机号验证码 / 用户名密码**两种登录方式，覆盖远程控车、车辆状态监控、定位追踪、预约出发/充电、家充桩管理、远程影像等全功能。

> ⚠️ 本项目为个人逆向研究作品，与福特官方无任何关联。使用本集成即表示同意自行承担相关风险与责任。

## 功能特性

**登录与品牌**
- 双登录方式：手机号短信验证码（无需密码）或用户名密码（Azure B2C 官方流程），配置时可自由选择
- 双品牌支持（v4.7.4）：登录第一步选择「福特派 FordPass」或「林肯之道 Lincoln Way」——共用同一套云端加密体系；林肯之道 B2C 登录端点待抓包补齐，当前选中会提示暂不可登录

**远程控车**
- 上锁 / 解锁 / 远程启动 / 远程熄火 / 鸣笛寻车 / 声光寻车 / 灯光寻车 / 手动刷新车辆状态
- 鸣笛持续时长与类型可选（雨落荷叶 / 急浪拍岸 / 汽笛长鸣 / 空谷回音 / 声光共舞），选择即云端保存、鸣笛参数直达车机
- 分车门锁定（IDUL，按车型能力创建）、哨兵模式（电马）、遥控泊车、远程关窗、拖车灯光检测（皮卡）、车载冰箱（猛禽/领裕）——均按云端命令白名单自动创建

**充电与能源（纯电/插混/混动车型）**
- 预约出发 / 预约充电 / 立即充电 / 停止充电
- 家充桩 BLE 自动认证：授权后插枪即由桩与车 BLE 自动配对认证（手机不在场也能启动充电）

**车辆状态**
- 门锁、报警、燃油、胎压、里程、车窗、车门、自动熄火倒计时等实时状态
- 车辆异常警示：真实告警接口明文中文显示（如「胎压监测系统警告」）
- 保养计划 / 召回信息 / SIM 卡 / WiFi 热点 / 空调滤芯 / 机油寿命 / 慢漏气胎 / 预测性诊断
- 车辆服务信息：云端能力位图 + 救援/客服/售后电话 + 电子说明书
- 自动令牌刷新：访问令牌过期自动刷新，无需重新登录

**定位与影像**
- GPS 车辆定位追踪（设备追踪器，HA 地图可显示，可选 WGS-84 / GCJ-02 坐标体系）
- 车辆图片 / 3D 车型图（持久保存不删除）
- 行车记录视频录制启停（带记录仪硬件的车型）

**能力自适应**
- 实体全部数据驱动：登录后按本车云端能力与车辆数据判断，**多 VIN 账号每辆车独立创建自己支持的实体组**
- 不支持的命令按下会返回网关明确报错，不制造静默无效实体
- 刷新失败保留最后已知状态，0 unavailable

> 蓝牙域边界：汽车 BLE 车控指令（一键制冷/制热、熄火、门锁、后备箱、车窗、寻车、前备箱、举升顶、车窗通风）与 PAAK 数字钥匙为**纯 BLE 物理链路**（需手机在车旁 10 米内 + 安全会话），云端无 HTTP 通道，HA 云集成无法执行——支持 BLE 的车型请用手机在车旁操作福特派 App。本集成云端可用的蓝牙域能力（分车门锁定 + 家充桩 BLE 自动认证）均已接入。

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
2. 选择登录方式：**手机号验证码登录**（输入手机号 → 获取短信验证码 → 输入完成）或 **用户名密码登录**（密码仅用于本次登录换令牌、不会保存）
3. 按所选方式完成登录
4. 在集成选项（Options）中可开启「启用车辆定位追踪」、选择坐标体系（WGS-84 / GCJ-02）；状态刷新间隔为固定默认 30 分钟（v4.9.6 起不再用户可配置）

> 白盒加密内核为**纯 Python 查表实现**（v4.2.7 起 100% 破译，零外部依赖），无需安装任何额外 Python 包。

## 支持的实体

| 类型 | 实体 | 说明 |
|---|---|---|
| device_tracker | 车辆定位 | GPS 坐标 + 地址属性，地图可显示 |
| image | 车辆图片 / 3D 车型图 | 车型渲染图，探测到 modelUrl 才创建（全车型自动适配） |
| lock | 车门锁 / 后备箱锁 / 前备箱锁 | 上锁 / 解锁；后备箱锁、前备箱锁按车型尾门/命令白名单创建 |
| button | 手动刷新状态 / 中央区灯光 / 辅助设置 / OTA 激活排程 / 重置空调滤芯 / 拖车灯光检测 / 遥控泊车 / 远程关窗 / 标记消息已读 / 行车记录启停 | 均按车型云端能力自动创建 |
| switch | 远程启动 / 灯光寻车 / 鸣笛寻车 / 声光寻车 / 哨兵模式 / 立即充电 / 车载冰箱 / 预约充电 | 鸣笛寻车类型 0-3；声光共舞走 `send-command InitialVA`（App 官方通道） |
| select | 鸣笛持续时长 / 鸣笛类型 | 选择即保存，鸣笛时参数直达车机 |
| sensor | 车辆状态 / 异常警示 / 保养计划 / 召回 / SIM / WiFi / 空调滤芯 / 机油寿命 / 预测性诊断 / 车辆服务信息 / 鸣笛设置云端状态 / 车辆在线状态 / 车辆设备信息 / 授权状态 / SRS 档案 / 传感器影子 / 行车记录视频 / 最新消息 / OTA 提醒 / 车辆清单 / 消息中心 / 共享列表 / 设备列表 | 全部数据驱动：登录后一次性探测，探测到数据才创建（多 VIN 每车独立） |

> 实体创建全部数据驱动：登录后按本车云端能力与车辆数据判断（多 VIN 账号下每辆车独立创建自己支持的实体组），不支持的命令按下会返回网关明确报错，不制造静默无效实体；刷新失败保留最后已知状态，0 unavailable。

## 常见问题

| 问题 | 处理 |
|---|---|
| 用户名密码登录失败/被拦截（`AADB2C: An exception has occurred`、567） | 微软 Azure AD B2C 网关风控可能拦截非浏览器自动化请求——**改用短信验证码登录**（登录后令牌自动续期，无需频繁重登） |
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

## 免责声明

1. **非官方项目**：本项目为个人逆向研究作品，与福特汽车（中国）有限公司、长安福特汽车有限公司、林肯中国及任何官方主体**无任何关联、未经任何授权**；「福特派」「林肯之道」等商标归各自权利人所有，本文中引用仅用于识别所对接的服务。
2. **仅供学习研究**：本项目包含逆向工程所得素材（白盒加密表、签名算法等），仅限**个人学习研究**与**自用**，请勿用于商业用途。
3. **风险自担**：使用本集成可能导致账号风控、功能失效、违反服务条款等后果，**一切风险与责任由使用者自行承担**；作者不提供任何服务可用性担保。
4. **数据本地**：车辆数据（VIN、位置、车况等）仅存储在您自己的 Home Assistant 中，集成除福特/林肯官方服务端外不向任何第三方传输、共享您的数据。
5. **下架配合**：若相关权利方要求移除逆向素材或本仓库，作者将配合下架。

## 支持作者

[![RJ](https://img.shields.io/badge/Rock-Jesus-purple.svg)](https://rockjesus.cn)

## 版本历史

<details>
<summary>最近版本（完整历史见 <a href="https://github.com/RockJesus/ha-fordpass-cn/releases">GitHub Releases</a>）</summary>

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
