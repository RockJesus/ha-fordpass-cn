# FordPass 中国版（福特派互联）Home Assistant 集成

通过手机号+短信验证码登录福特派，在 Home Assistant 中远程控制你的福特/林肯车辆（中国区）。

## 功能

- **门锁**：远程上锁 / 解锁
- **远程启动**：启动发动机 / 熄火
- **按钮**：鸣笛寻车、报警、强制刷新车辆状态
- **传感器**：燃油量、续航里程、总里程、机油寿命、蓄电池电压、四轮胎压、门锁/报警/远程启动状态

## 安装

1. 将 `custom_components/fordpass_cn` 整个文件夹复制到 Home Assistant 的 `custom_components/` 目录下
2. 重启 Home Assistant
3. 设置 → 设备与服务 → 添加集成 → 搜索 **福特派互联**
4. 输入注册福特派的**手机号** → 点下一步（将发送短信验证码）
5. 输入收到的 **6 位验证码** → 完成登录
6. 集成会自动绑定该账号下的第一辆车（后续版本支持多车选择）

## 依赖

- `unicorn`（CPU 模拟器，用于执行 App 的白盒 AES 加密代码）。首次安装集成时 HA 会自动安装；若失败，请在 HA 容器/宿主机执行 `pip install unicorn`。
- 需要 Home Assistant 能够访问 `cn.api.mps.ford.com.cn`（中国区福特网关）。

## 工作原理（协议已从官方 App 6.14.0 逆向并实测验证）

- 认证：`generate-passcode`（发验证码）→ `dlt-token-by-phone-passcode-login`（验证码换 JWT）→ `auth-token` 请求头
- 加密：敏感字段使用 App 内置的**白盒 AES-256-CBC**（运行时无明文密钥），集成通过 Unicorn 执行 App 原始机器码，结果与官方 App 逐位一致
- 签名：`sign = SHA256(secretKey2 & 参数 & secretKey)`，已对 19 组真实请求验证
- 状态：`GET /api/cnxapi-cvinfo/v1/vehicle-status`，响应体加密后解密
- 命令：`POST /api/cnxapi-cvinfo/v1/vehicles/send-command`

## 注意事项

- **车控命令值**（上锁/解锁/启动）依据 APK 内的命令枚举实现（`Lock`/`Unlock`/`EngineStart`/`EngineStop`）。如果首次使用时命令报错，请打开 Debug 日志并反馈，集成会适配为正确的命令值。
- 状态默认每 5 分钟刷新一次，可在集成选项中调整（60–3600 秒）。
- 车辆定位（LBS）接口需要额外的 APIM 订阅密钥（App 白盒保护），暂未集成。
- 请勿将登录 token 透露给第三方；token 过期后集成会自动用 refresh token 续期。

## 文件结构

```
custom_components/fordpass_cn/
├── __init__.py       # 集成入口
├── config_flow.py    # 手机号+验证码登录流程
├── api.py            # 福特网关 API 客户端（签名/加密/接口）
├── wbsk.py           # 白盒 AES 加解密（Unicorn 执行官方机器码）
├── const.py          # 常量（端点、命令、密钥）
├── coordinator.py    # 数据轮询协调器
├── lock.py / switch.py / button.py / sensor.py
├── data/             # 官方白盒加密库与密钥文件（来自官方 App）
└── translations/
```

## 版本

2.1.0（2026-09-25）· 已在真实账号上端到端验证（车辆列表、车辆状态、token 解密）
