# 国内福特派 App 接口调研笔记（2026-08-25 实测，含 v6.15.0 APK 逆向）

> 目标：确认「福特派（中国）」App 后端接口现状，用于 HAOS 集成。
> 结论先说：**2026 认证已迁移到「DLT 令牌」体系，登录/刷新全程白盒 AES 加密 +
> sign/xjw 签名，抓不到明文 refresh_token 是正常现象**；车辆数据/控制接口仍在
> `cnapi.cv.ford.com.cn`，用 DLT 访问令牌（auth-token）直连可用。

## 一、接口域名与实测状态

| 域名 | 用途 | 2026-08-25 实测 |
|---|---|---|
| `https://cnapi.cv.ford.com.cn/` | 车辆服务：车辆列表/状态/远程指令 | ✅ 在线（v5 status 端点返回 FIG 处理响应；带无效 token 时 users/vehicles 返回 500） |
| `https://cn.api.mps.ford.com.cn/` | 令牌交换 / 账户 / 服务历史 | ✅ 在线（DLT 令牌端点活跃，逐头校验：appVersion→osType→Application-Id→clientType） |
| `https://sso.ci.ford.com.cn/` | 旧版 SSO（IBM CI） | ⚠️ 已失效：整域置于腾讯 EdgeOne，旧 `/oidc/*` 路径全 404 |

## 二、2026 新认证体系（逆向自 App v6.15.0）

**核心结论：**
- 令牌体系为 **DLT 令牌**（`dltAccessToken` + `dltRefreshToken`），请求头 `auth-token=<dltAccessToken>`。
- 登录/刷新端点全部在 `https://cn.api.mps.ford.com.cn`：

| 端点 | 用途 |
|---|---|
| `POST /api/cnxapi-token-exchange/v1/app/dlt-token-by-phone-passcode-login` | 手机号 + 短信验证码 → DLT 令牌 |
| `POST /api/cnxapi-token-exchange/v1/app/dlt-token-by-b2c-auth-code` | B2C 网页授权码 → DLT 令牌 |
| `POST /api/cnxapi-token-exchange/v1/app/dlt-token-by-phone-one-click-login` | 手机号一键登录 → DLT 令牌 |
| `POST /api/cnxapi-token-exchange/v1/app/b2c-jwt-token-by-social-login` | 微信/Apple 社交登录 → B2C JWT |
| `POST /api/cnxapi-token-exchange/v1/app/refresh-dlt-token` | 刷新 DLT 令牌 |
| `POST /api/cnxapi-token-exchange/v1/app/revoke-dlt-token` | 注销令牌 |
| `POST /api/cnxapi-user-validation/v1/app/sms/generate-passcode` | 发送短信验证码 |

**必填请求头（逐头实测验证，缺失/错误即被拒）：**
- `Application-Id: 46409D04-BD1B-40C6-9D51-13A52666E9F9`（**consumer 环境**；
  prod 的 `AD5D8A89-…` 已被网关拒绝）
- `appVersion: 6.15.0`（其他值报 "this app version: xxx is invalid"）
- `osType: android`（也接受 ios）
- `clientType: fp`（福特派；其他值报 "this clientType: xxx is invalid"）

**请求体字段（空 body 实测反馈）：**
- `dlt-token-by-b2c-auth-code`：`brand`、`timestamp`、`osType`、`encryptedAuthCode`、`xjw`、`sign`
- `refresh-dlt-token`：`timestamp`、`xjw`、`encryptedRefreshToken`、`brand`、`sign`
- `sms/generate-passcode`：`encryptedPhoneNumber`、`touchPoint`、`xjw`、`timestamp`、`sign`
- `dlt-token-by-phone-passcode-login`：`touchPoint`、`brand`、`encryptedPhoneNumber`、`tncAccepted`、`timestamp`、`osType`、`tncOutBoundAccepted`…

> **加密说明**：`encrypted*` 字段由白盒 AES（`AES_CBC_PKCS7Padding`，`WhiteBoxAesUtil`，
> 密钥内嵌在 `libPaakUtil.so`）加密，配合 `sign`（请求签名）与 `xjw`（安全令牌）。
> 这是福特的反自动化设计 —— 这也是为什么旧集成「抓包拿不到明文 refresh_token」。

## 三、车辆数据接口（仍在 cnapi.cv.ford.com.cn）

| 接口 | 说明 |
|---|---|
| `GET {CV}/api/users` | 用户信息（profile.userId） |
| `GET {CV}/api/users/vehicles` | 车辆列表（vehicles.$values） |
| `GET {CV}/api/users/vehicles/{vin}/detail` | 单台车辆详情 |
| `GET {CV}/api/vehicles/v5/{vin}/status` | 车辆实时状态（油量/里程/胎压/车门/车窗/GPS…） |
| `GET {CV}/api/vehicles/{vin}/authstatus` | 车辆授权状态 |
| `GET {API}/api/servicehistory/v1/service-history?vin={vin}` | 维保历史 |

车辆请求头同样需带 `Application-Id` + `auth-token`（=DLT 访问令牌）等头。

## 四、远程控制接口

| 动作 | 接口 |
|---|---|
| 远程启动 | `PUT {CV}/api/vehicles/v2/{vin}/engine/start` |
| 远程熄火 | `DELETE {CV}/api/vehicles/v2/{vin}/engine/start` |
| 上锁 | `PUT {CV}/api/vehicles/v2/{vin}/doors/lock` |
| 解锁 | `DELETE {CV}/api/vehicles/v2/{vin}/doors/lock` |

返回 `commandId`，随后轮询
`GET {CV}/api/vehicles/v2/{vin}/{endpoint}/{commandId}`：
- `status == 200` → 成功；`status == 552` → 执行中（pending）

## 五、App 内置服务配置（componentList，v6.15.0）

逆向自 APK 内置 JSON，展示了全部后端服务与（部分）主机：

- `tokenManager` / `vehicleInfo` / `account` / `paakService` → `https://cn.api.mps.ford.com.cn`
- `fordRemoteControl.fordService` / `vehicleAuth` → `https://cnapi.cv.ford.com.cn`
- `ibmciService`（SSO）→ `https://sso.ci.ford.com.cn`（已失效）
- 其他：`api-connect.ford.com.cn`（LBS/POI）、`www.fordpass.com.cn`（内容/文档）、
  `vconsf.jmc.com.cn`（江铃 TIMA）、`jmc-dk.ingeek.cn`（蓝钥匙 PaaK）、`iot.shzhida.com`（充电桩）

## 六、旧版常量（2022 开源版，已失效）

- 旧福特派 Application-Id：`35F9024B-010E-4FE7-B202-62D941F8681C`（已失效）
- 旧 SSO client_id：`6487f540-5f6b-4c04-8384-23827b00b4ba`
- 新 App 包名：`com.ford.fordpasscn`，v6.15.0（2026-08），MD5 `599bc410a8efebc3c923b6702b2a5eb5`

## 七、状态 JSON 取值路径（示例）

```jsonc
{
  "gps":  { "latitude": "31.xxxx", "longitude": "121.xxxx" },   // 火星坐标 GCJ-02
  "fuel": { "fuelLevel": 96.74, "distanceToEmpty": 599.9 },
  "odometer": { "value": 19487.0 },
  "oil": { "oilLifeActual": 99 },
  "battery": { "batteryHealth": { "value": "STATUS_GOOD" },
               "batteryStatusActual": { "value": 13 } },
  "TPMS": { "leftFrontTirePressure": { "value": 273 },
            "outerRightRearTirePressure": { "value": 268 } },
  "doorStatus": { "driverDoor": { "value": "Closed" }, ... },
  "windowPosition": { "driverWindowPosition": { "value": 0 }, ... },
  "lockStatus": { "value": 1 },
  "remoteStartStatus": { "value": 0 },
  "ignitionStatus": { "value": "Off" },
  "alarm": { "value": "..." }
}
```

GPS 为高德火星坐标（GCJ-02），集成已内置转换到 WGS-84 后再交给 HA 地图。

## 八、加密层逆向进展（2026-08-25 完成 APK 完整下载后）

- **白盒 AES 在 Dart 侧（libapp.so / pointycastle）实现**，不在 libPaakUtil.so——
  libPaakUtil.so 实为 PaaK 数字车钥匙库（BPEK/CAK/DSK、RSA/GCM）。
- Dart 函数：`getAesCipher` / `getIv` / `providerBase64Iv` / `encryptByAES` /
  `decryptByAES` / `aesEncryptStringWithBase64` / `aesEncryptToBase64List` /
  `aesDecryptWithBase64List`，算法 `AesCbcPkcs7`（AES-CBC + PKCS7）。
- libapp.so 内提取到 **9 个 32 字符随机串**（各服务 secretKey 池）：
  `mOCBCVsQX8KvCnjThkxM4vwxoegWZKy2`（距 encryptedAuthCode 仅 746B，强候选）、
  `qA6i0NStHI2T5QFc2ZHUG0cH9nH3rKxF`（邻近 aesEncryptStringWithBase64）、
  `aRsQCHpTyNyTKAPb4bRlzF0EMMS8H8nV`（邻近 providerBase64Iv/encryptedPhoneNumber）、
  `kMuOFcvY13435zQLw1Kpki7IH0Wp696Q`（邻近 encryptedRefreshToken）、
  `4qjSFHwgd9IBrT35vzNmiY1S4cHqD5Lo`、`f6ZHP0g5yNbPRvRio9tjvf621lypsLut`、
  `vQojyNASF35KA5gfjPFQ66Dg3A09zC1i`、`xKCV6GDQaWMddjsZTe0qcR21ePYzwfg4`、
  `Ym+ackMJrwkOqo2O0fPnUy55iTuLKsRK`。
- IV 候选：`d558Gq0YQK2QUlM2`（16 字符）等。
- **签名参数（refresh-dlt-token 实测反推）**：
  - `brand` 合法值 = **`FORD`**（"ford" 等均报 brand format invalid）
  - `timestamp` = 当前毫秒时间戳（0 报 timestamp min invalid）
  - `xjw` = 16/24/32 位 hex 安全令牌（格式+长度校验通过后进入 sign 校验）
  - `sign` = **恰好 64 位 hex = SHA-256**（格式/长度校验通过后报 `sign is error`
    errorCode 100400，说明服务器会验签）。Dart 侧 `_getSHA256Base64` /
    `_getSignedQueryParams` / `signR1Parameters`/`signR2Parameters`/`signWithCommonR2`
    佐证；secretKey 存于 SharedPreferences（`signatureR1_secretKey` 等键）。
- **锁定密钥的实操路径（已备好 crack_aes.py）**：用「你的手机号（明文）→ 抓包的
  encryptedPhoneNumber（密文）」作为已知明密文对，穷举 9 个 key × IV × 填充，
  即可确定令牌加密实际使用的 key+IV。拿到后即可在纯 Python 实现
  `encryptedPhoneNumber` / `encryptedRefreshToken` 加密，从而让集成支持
  **手机号+验证码一键登录 / DLT 自动续期**。

## 九、未来解锁点

实现「集成内一键登录/自动续期」需要完成 `libPaakUtil.so` 白盒 AES 的密钥提取与
`sign`/`xjw` 算法复刻。当前版本走「抓包粘贴 DLT 访问令牌」路径，令牌过期后重新粘贴即可。
