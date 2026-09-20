# 福特派 Home Assistant 集成

[![HACS Default](https://img.shields.io/badge/HACS-Default-orange.svg)](https://hacs.xyz/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Home Assistant 自定义集成，用于连接福特派（FordPass）智能家居平台。

## 功能特性

- ✅ 用户名密码登录
- ✅ Token 自动刷新（长期有效）
- ✅ 用户信息传感器
- ✅ 车辆列表获取
- 🚧 车辆状态监控（开发中）
- 🚧 远程控车（开发中）
- 🚧 车门锁控制（开发中）

## 安装

### 方法一：HACS 安装（推荐）

1. 确保已安装 [HACS](https://hacs.xyz/)
2. 在 HACS 中搜索 "福特派" 或 "fordpass"
3. 点击下载并重启 Home Assistant

### 方法二：手动安装

1. 下载 `custom_components/fordpass/` 文件夹
2. 将其复制到 Home Assistant 的 `custom_components/` 目录
3. 重启 Home Assistant

## 配置

1. 在 Home Assistant 中，进入 **设置** > **设备与服务**
2. 点击 **添加集成**
3. 搜索 "福特派"
4. 输入用户名和密码
5. 点击提交

## 实体说明

### 传感器实体

- **用户信息** - 当前登录用户昵称
  - 属性：user_id、nickname、vehicle_count

### 锁实体（开发中）

- **车门锁** - 车辆门锁状态
- **控制**：锁车、解锁

## API 说明

本集成基于福特派 App 抓包分析，主要接口：

| 接口 | 方法 | 说明 |
|------|------|------|
| `/v1/user/login` | POST | 登录 |
| `/v1/user/logout` | POST | 登出 |
| `/v1/user/info` | GET | 获取用户信息 |
| `/v1/vehicle/list` | GET | 获取车辆列表 |
| `/v1/vehicle/status` | GET | 获取车辆状态 |
| `/v1/vehicle/lock` | POST | 锁车 |
| `/v1/vehicle/unlock` | POST | 解锁 |

## 注意事项

- 本集成仅供学习交流使用
- 请遵守福特派相关服务条款
- 如有问题请提交 Issue

## License

MIT License
