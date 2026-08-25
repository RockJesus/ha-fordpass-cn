# FordPass China（福特派）Home Assistant 集成

将**中国大陆的福特 / 林肯**车辆接入 Home Assistant（HAOS），可远程监控车况、远程启动/熄火、远程解锁/上锁、查看车辆实时位置。

本集成基于国内「福特派 / 林肯之道」App 后端接口逆向实现，原项目为
[georgezhao2010/fordpass_china](https://github.com/georgezhao2010/fordpass_china)，
本版本针对 2026 年的 Home Assistant 做了现代化改造（async 平台转发、Coordinator 模式、现代实体 API），
并保留了对接口的容错解析。

> ⚠️ 该接口为福特私有/未公开接口，福特随时可能调整导致集成失效，属正常现象，按文末「接口失效怎么办」处理即可。

---

## 一、支持的车型与功能

- 车型：支持福特派 / 林肯之道 App 远程控制的所有中国大陆福特/林肯车型
  （车辆需已绑定福特派，且车载 TCU 联网模块正常）。
- 远程控制：
  - `switch.xxx_remote_start` —— 远程启动 / 熄火
  - `lock.xxx_lock` —— 中控锁上锁 / 解锁
- 远程监控：
  - 车辆 GPS 位置（自动转换火星坐标 → 标准坐标，地图可直接显示）
  - 剩余油量 / 总里程 / 剩余里程 / 机油寿命
  - 发动机运转状态、报警状态
  - 四车门 / 机舱盖 / 尾门开关状态
  - 四轮胎压、电瓶健康、电瓶电压
  - 四车窗开启位置

## 二、安装

### 方式一：HACS（推荐）
1. HACS → 右上角 `⋮` → `自定义存储库`：
   - 仓库地址：`https://github.com/georgezhao2010/fordpass_china`
   - 类型：`集成`
2. 搜索 `FordPass China` 安装，重启 Home Assistant。

### 方式二：手动
将 `custom_components/fordpass_china` 整个目录复制到 HA 配置目录
（HAOS 一般位于 `/config/custom_components/fordpass_china`），重启 HA。

## 三、获取 DLT 访问令牌（2026 新认证，关键步骤）

> ⚠️ **为什么抓不到 refresh_token？** 2026 年起福特中国把认证迁移到了 **DLT 令牌**体系：
> 手机号、验证码、刷新令牌在传输时都经过**白盒 AES 加密 + sign/xjw 签名**
> （端点 `/api/cnxapi-token-exchange/v1/app/*`），所以抓包看到的是密文，拿不到明文 refresh_token。
> 因此本版本改用 **DLT 访问令牌（auth-token）直接认证**，它在 App 请求头里是明文，可直接复制。

**方法：手机抓包（推荐 Fiddler / Charles / HttpCanary，任选其一）**

1. 电脑和手机连接同一 Wi-Fi；
2. 电脑开启抓包工具并开启 HTTPS 解密，手机 Wi-Fi 代理指向电脑；
3. 打开手机**福特派 App**，随便操作一下（刷新车辆状态即可）——这一步会让 App 请求车辆服务；
4. 在抓包结果中筛选域名 `cnapi.cv.ford.com.cn`，打开任意一条请求（如
   `GET /api/users/vehicles` 或 `GET /api/vehicles/v5/{vin}/status`）；
5. 查看该请求的 **请求头（Request Headers）**，复制 **`auth-token`** 的值（一长串 base64/JWT 样式的字符串，即 DLT 访问令牌）；
6. 关闭代理，进入 HA → 设置 → 设备与服务 → 添加 `FordPass China`，选择
   「DLT 访问令牌」并粘贴该值。

> 提示：
> - DLT 访问令牌有有效期，过期后 App 内再刷新一次车况、重新抓取 `auth-token` 粘贴即可。
> - 若你仍持有旧版 `refresh_token`（2026 前抓的），集成也保留了对它的兼容。
> - 集成会自动带上福特新网关要求的请求头（`Application-Id` / `appVersion` / `osType` / `clientType`）。

## 四、配置

设置 → 设备与服务 → 添加集成 → `FordPass China`：

1. 认证方式选择 `DLT 访问令牌（2026 新认证，推荐）`；
2. 应用类型：`福特派` 或 `林肯之道`（两者账户通用但车辆数据不互通）；
3. 粘贴从 App 请求头抓到的 `auth-token`；
4. 成功后自动发现账号下的车辆。

选项里可调整**轮询间隔**（默认 5 分钟；建议不低于 5 分钟，避免触发风控）。

## 五、实体一览

| 实体 | 类型 | 说明 |
|---|---|---|
| `device_tracker.xxx` | device_tracker | 车辆 GPS 位置 |
| `switch.xxx_remote_start` | switch | 远程启动开关 |
| `lock.xxx_lock` | lock | 中控锁 |
| `sensor.xxx_fuel` | sensor | 剩余油量 % |
| `sensor.xxx_range` | sensor | 剩余里程 km |
| `sensor.xxx_odometer` | sensor | 总里程 km |
| `sensor.xxx_oil_life` | sensor | 机油寿命 % |
| `sensor.xxx_alarm` | sensor | 报警状态 |
| `sensor.xxx_battery_voltage` | sensor | 电瓶电压 V |
| `sensor.xxx_battery_health` | sensor | 电瓶健康 |
| `sensor.xxx_{四轮}_tire_pressure` | sensor | 四轮胎压 kPa |
| `sensor.xxx_{四窗}_window_position` | sensor | 四车窗位置 |
| `binary_sensor.xxx_{车门}_door` | binary_sensor | 车门/机舱盖/尾门开关 |
| `binary_sensor.xxx_ignition_status` | binary_sensor | 发动机运转 |

其中 `xxx` 为小写 VIN。实体 ID 由 HA 按名称自动生成，可自行重命名。

## 六、服务

- `fordpass_china.refresh_status`：立即刷新某 VIN 车辆状态（留空刷新全部）。
- `fordpass_china.clear_tokens`：清除并移除集成（令牌失效时用）。

## 七、调试日志

```yaml
logger:
  default: warn
  logs:
    custom_components.fordpass_china: debug
```

## 八、接口失效怎么办

福特可能随时调整后端。集成的基础 URL 全部集中在
`custom_components/fordpass_china/ford/fordpass.py` 顶部的常量中：
`SSO_URL` / `CV_URL` / `API_URL`。接口变动时，抓包 App 拿到新的域名后改这里即可。
详见 `docs/API_NOTES.md`（记录了 2026-08 实测的在线状态）。

## 免责声明

本项目与福特汽车无关，使用私有/未公开接口，仅供个人自动化使用。
请合理设置轮询频率，避免频繁远程指令对车辆或账号造成影响。
