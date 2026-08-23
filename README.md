# ha-fordpass-cn
Homeassistant FordPass CN - 福特派中国区 Home Assistant 集成
# FordPass CN - 福特派中国区 Home Assistant 集成

适用于中国大陆福特派（FordPass）/ 林肯之道（Lincoln Way）的 Home Assistant 自定义集成。

支持福特锐际、蒙迪欧、探险者、福克斯等所有支持福特派 App 的车型。

## 功能

### 远程控制
- 远程启动 / 关闭发动机
- 远程锁车 / 解锁

### 实时监控
- 车辆位置（GPS，自动 GCJ-02 转 WGS-84）
- 燃油量、剩余续航
- 总里程
- 机油寿命
- 电瓶电压、电瓶健康状态
- 四轮胎压
- 发动机运转状态
- 车门 / 车窗 / 发动机舱盖 / 后备箱状态
- 报警状态

## 安装

### 方法一：HACS 自定义仓库（推荐）

1. 打开 HACS → 右上角三个点 → **自定义存储库**
2. 填入仓库地址（本仓库地址），类型选 **Integration**
3. 搜索 `FordPass CN` 并安装
4. 重启 Home Assistant

### 方法二：手动安装

1. 下载本仓库的 `custom_components/fordpass_cn` 文件夹
2. 将整个 `fordpass_cn` 文件夹复制到你的 Home Assistant 配置目录的 `custom_components/` 下
3. 重启 Home Assistant

## 配置

### 前置条件

1. 你的车辆已在福特派 App 中绑定并激活
2. 车辆的 TCU（远程通信模块）已启用

### 获取 refresh_token

由于福特中国已禁用用户名密码直接登录 API，需要通过抓包获取 `refresh_token`：

#### 方法 A：手机抓包（推荐）

1. 在手机上安装抓包工具：
   - Android: [HttpCanary](https://github.com/MegatronKing/HttpCanary) / [Charles](https://www.charlesproxy.com/)
   - iOS: [Stream](https://apps.apple.com/cn/app/stream/id1312141691) / Charles

2. 配置抓包工具并安装 SSL 证书（按工具说明操作）

3. 启动抓包，然后打开福特派 App 并登录

4. 在抓包结果中搜索以下请求：
   ```
   POST https://cn.api.mps.ford.com.cn/api/token/v2/cat-with-refresh-token
   ```
   或
   ```
   POST https://cn.api.mps.ford.com.cn/api/token/v2/cat-with-ci-access-token
   ```

5. 查看请求的 **Request Body**，找到 `refresh_token` 字段，复制其值（一长串字符）

#### 方法 B：电脑浏览器 + 开发者工具

1. 电脑浏览器打开福特派官网并登录
2. 按 `F12` 打开开发者工具 → **Network（网络）** 标签
3. 刷新页面，在请求列表中筛选 `token`
4. 找到 `cat-with-refresh-token` 或 `cat-with-ci-access-token` 请求
5. 在 **Payload** 或 **Request** 标签中找到 `refresh_token`，复制其值

### 添加集成

1. HA → **设置** → **设备与服务** → **添加集成**
2. 搜索 **福特派 FordPass CN**
3. 选择车辆类型（福特派 / 林肯之道）
4. 粘贴上一步获取的 `refresh_token`
5. 点击提交，集成会自动验证 token 并发现你的车辆

## 配置选项

添加集成后，点击集成卡片上的 **配置** 可以调整：
- **数据刷新间隔**：默认 5 分钟，范围 1-60 分钟

> 注意：过于频繁的刷新可能会增加车载电瓶消耗，建议不低于 3 分钟。

## 服务

| 服务 | 说明 |
|------|------|
| `fordpass_cn.refresh_vehicle` | 立即刷新所有车辆数据 |
| `fordpass_cn.clear_tokens` | 清除认证令牌（需重新配置） |

## 实体列表

配置完成后，每辆车会生成以下实体（`{vin}` 为车架号后几位）：

### 传感器 sensor
- `sensor.{vin}_fuel` — 燃油量 (%)
- `sensor.{vin}_odometer` — 总里程 (km)
- `sensor.{vin}_range` — 剩余续航 (km)
- `sensor.{vin}_oil_life` — 机油寿命 (%)
- `sensor.{vin}_battery_voltage` — 电瓶电压 (V)
- `sensor.{vin}_battery_health` — 电瓶健康状态
- `sensor.{vin}_tire_pressure_front_left` — 左前胎压 (kPa)
- `sensor.{vin}_tire_pressure_front_right` — 右前胎压 (kPa)
- `sensor.{vin}_tire_pressure_rear_left` — 左后胎压 (kPa)
- `sensor.{vin}_tire_pressure_rear_right` — 右后胎压 (kPa)
- `sensor.{vin}_window_front_left` — 左前车窗位置 (%)
- `sensor.{vin}_window_front_right` — 右前车窗位置 (%)
- `sensor.{vin}_alarm` — 报警状态

### 二元传感器 binary_sensor
- `binary_sensor.{vin}_ignition_status` — 发动机运转状态
- `binary_sensor.{vin}_door_front_left` — 左前车门
- `binary_sensor.{vin}_door_front_right` — 右前车门
- `binary_sensor.{vin}_door_rear_left` — 左后车门
- `binary_sensor.{vin}_door_rear_right` — 右后车门
- `binary_sensor.{vin}_hood` — 发动机舱盖
- `binary_sensor.{vin}_trunk` — 后备箱

### 开关 switch
- `switch.{vin}_remote_start` — 远程启动

### 锁 lock
- `lock.{vin}_lock` — 中控锁

### 设备追踪 device_tracker
- `device_tracker.{vin}` — 车辆位置

## 调试

在 `configuration.yaml` 中添加以下配置开启调试日志：

```yaml
logger:
  default: warning
  logs:
    custom_components.fordpass_cn: debug
```

## 常见问题

### Q: 提示 refresh_token 无效
A: refresh_token 可能已过期，请重新抓包获取新的 token。token 有效期有限，建议获取后立即使用。

### Q: 集成添加成功但没有实体
A: 检查车辆是否在福特派 App 中已激活，且 TCU 功能已启用。可以在 App 中确认能否远程启动车辆。

### Q: 车辆位置偏移
A: 集成已自动将福特返回的 GCJ-02（火星坐标）转换为 WGS-84。如果仍有偏移，可能是 GPS 信号问题。

### Q: 远程命令无反应
A: 远程命令通过车辆 TCU 执行，需要车辆有网络信号。命令发送后会在后台轮询执行状态，最多等待 30 秒。

## 免责声明

本集成基于逆向工程的福特派私有 API，非官方支持。福特可能随时更改 API 导致集成失效。使用本集成可能导致福特派账户被临时限制，建议使用副账号（通过主账号邀请驾驶员共享车辆）。

使用风险自负。

## 致谢

本集成参考了以下项目：
- [georgezhao2010/fordpass_china](https://github.com/georgezhao2010/fordpass_china)
- [itchannel/fordpass-ha](https://github.com/itchannel/fordpass-ha)
- [marq24/ha-fordpass](https://github.com/marq24/ha-fordpass)
