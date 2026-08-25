# 国内福特派 App 接口调研笔记（2026-08-25 实测）

> 目标：确认「福特派（中国）」App 后端接口现状，用于 HAOS 集成。
> 结论先说：**车辆数据/控制接口仍在线；旧版 SSO 认证端点已迁移失效，认证需走 refresh_token。**

## 一、接口域名与实测状态

| 域名 | 用途 | 2026-08-25 实测 |
|---|---|---|
| `https://cnapi.cv.ford.com.cn/` | 车辆服务（Azure APIM）：车辆列表/状态/远程指令 | ✅ 在线（无 token 返回 401，需 `Application-Id` + `auth-token`） |
| `https://cn.api.mps.ford.com.cn/` | 令牌交换 / 服务历史（Azure APIM） | ✅ 在线（POST token 接口返回 400=请求不合法，证明路径有效） |
| `https://sso.ci.ford.com.cn/` | 旧版 SSO 密码授权（PingFederate） | ⚠️ 已迁移：整域置于腾讯 EdgeOne CDN 后，`/oidc/*`、`/.well-known/*` 全部 404 |

## 二、认证流程（本集成实现）

```
① 首选（推荐）：
   POST https://cn.api.mps.ford.com.cn/api/token/v2/cat-with-refresh-token
   body: {"refresh_token": "<从App抓包获得>"}
   → 返回 {access_token, refresh_token, expires_in}
   集成内部自动续期。

② 后备（SSO 恢复后可用）：
   POST https://sso.ci.ford.com.cn/oidc/endpoint/default/token   (password grant)
   → ci access_token
   POST https://cn.api.mps.ford.com.cn/api/token/v2/cat-with-ci-access-token
   body: {"ciToken": "<上一步 access_token>"}
   → 福特派 token + refresh_token
```

**实测佐证（2026-08-25）**：向 `cat-with-refresh-token` 发送一个格式完整的占位 token，
服务器返回 `{"message":"Invalid B2CToken","errorCode":"479"}` —— 说明该端点**在主动校验
“B2C 令牌”**（内部身份体系），请求路径与格式完全正确；只要填入真实 refresh_token 即可换取令牌。

## 三、关键常量（逆向自国内福特派 App）

- 福特派 Application-Id：`35F9024B-010E-4FE7-B202-62D941F8681C`
- 林肯之道 Application-Id：`5EE5E683-1B71-4D6B-BAA8-F344D6672796`
- SSO client_id：`6487f540-5f6b-4c04-8384-23827b00b4ba`
- App UA：`fordpass-cn/320 CFNetwork/1331.0.7 Darwin/21.4.0`
- Android 包名：`com.ford.fordpasscn`（厂商：福特汽车(中国)有限公司，2026-08 最新约 v6.15.0）

## 四、车辆数据接口

| 接口 | 说明 |
|---|---|
| `GET {CV}/api/users` | 用户信息（profile.userId） |
| `GET {CV}/api/users/vehicles` | 车辆列表（vehicles.$values） |
| `GET {CV}/api/users/vehicles/{vin}/detail` | 单台车辆详情 |
| `GET {CV}/api/vehicles/v5/{vin}/status` | 车辆实时状态（油量/里程/胎压/车门/车窗/GPS…） |
| `GET {CV}/api/vehicles/{vin}/authstatus` | 车辆授权状态 |
| `GET {API}/api/servicehistory/v1/service-history?vin={vin}` | 维保历史 |

## 五、远程控制接口

| 动作 | 接口 |
|---|---|
| 远程启动 | `PUT {CV}/api/vehicles/v2/{vin}/engine/start` |
| 远程熄火 | `DELETE {CV}/api/vehicles/v2/{vin}/engine/start` |
| 上锁 | `PUT {CV}/api/vehicles/v2/{vin}/doors/lock` |
| 解锁 | `DELETE {CV}/api/vehicles/v2/{vin}/doors/lock` |

返回 `commandId`，随后轮询
`GET {CV}/api/vehicles/v2/{vin}/{endpoint}/{commandId}`：
- `status == 200` → 成功
- `status == 552` → 执行中（pending）

## 六、状态 JSON 取值路径（示例）

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

## 七、历史参考

- 国际版福特派接口（北美/欧洲）：`api.mps.ford.com` / `usapi.cv.ford.com` / `sso.ci.ford.com`
  —— 与国内**完全不同**，已有大量开源实现（itchannel/fordpass-ha、marq24/ha-fordpass 等），
  但不能直接用于国内车辆。
- 国内版原开源实现：`georgezhao2010/fordpass_china`（v0.3.5，2022 年停更），
  本集成即在其基础上做 2026 现代化改造。
