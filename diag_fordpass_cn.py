#!/usr/bin/env python3
"""福特派（中国）DLT 令牌诊断脚本（2026-08 版）

用法：
    python diag_fordpass_cn.py --token "<从App抓包到的 auth-token 值>"

作用：
    用你从福特派 App 请求头抓到的 DLT 访问令牌（auth-token），逐一探测
    车辆后端各接口，告诉你令牌是否有效、哪些接口可用。

依赖：仅 Python 标准库（无需安装任何包）。
"""

import argparse
import json
import ssl
import sys
import urllib.request

CV_URL = "https://cnapi.cv.ford.com.cn"
MPS_URL = "https://cn.api.mps.ford.com.cn"

APPID = "46409D04-BD1B-40C6-9D51-13A52666E9F9"   # consumer 环境，2026 实测有效
UA = "fordpass-cn/320 CFNetwork/1331.0.7 Darwin/21.4.0"

# Ngsdn 网关必填头（缺失/错误即被拒）
NGSDN = {
    "appVersion": "6.15.0",
    "osType": "android",
    "clientType": "fp",
}


def request(url, method="GET", headers=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Application-Id", APPID)
    req.add_header("User-Agent", UA)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        r = urllib.request.urlopen(req, context=ssl.create_default_context(), timeout=20)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return -1, str(e)


def pretty(body):
    try:
        obj = json.loads(body)
        return json.dumps(obj, ensure_ascii=False, indent=1)[:900]
    except Exception:
        return body[:900]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", required=True, help="从福特派 App 请求头抓到的 auth-token（DLT 访问令牌）")
    ap.add_argument("--vin", help="可选：指定 VIN 测试单台车辆状态/远程指令")
    args = ap.parse_args()

    token = args.token.strip()
    hdr = {"auth-token": token, **NGSDN}

    print("=" * 64)
    print("福特派（中国）DLT 令牌诊断")
    print("令牌: %s…%s" % (token[:12], token[-6:]))
    print("=" * 64)

    tests = [
        ("用户信息", "GET", f"{CV_URL}/api/users"),
        ("车辆列表", "GET", f"{CV_URL}/api/users/vehicles"),
    ]
    if args.vin:
        tests += [
            ("车辆实时状态", "GET", f"{CV_URL}/api/vehicles/v5/{args.vin}/status"),
            ("车辆授权状态", "GET", f"{CV_URL}/api/vehicles/{args.vin}/authstatus"),
            ("远程启动(实际发送!)", "PUT", f"{CV_URL}/api/vehicles/v2/{args.vin}/engine/start"),
        ]

    for name, method, url in tests:
        code, body = request(url, method, headers=hdr)
        print(f"\n--- {name}  [{method}] {url}")
        print(f"    HTTP {code}")
        print("    " + pretty(body).replace("\n", "\n    "))

    print("\n" + "=" * 64)
    print("DLT 刷新端点探测（2026 新认证；字段已按逆向反推填充）")
    import time as _t
    code, body = request(
        f"{MPS_URL}/api/cnxapi-token-exchange/v1/app/refresh-dlt-token",
        method="POST", headers=NGSDN,
        body={
            "brand": "FORD",                       # 2026-08 实测唯一合法值
            "timestamp": int(_t.time() * 1000),    # 毫秒时间戳
            "xjw": "0" * 32,                        # 16/24/32 位 hex 通过格式校验
            "sign": "0" * 64,                       # 64 位 hex = SHA-256 长度
            "encryptedRefreshToken": "x",
        },
    )
    print(f"    HTTP {code}")
    print("    " + pretty(body).replace("\n", "\n    "))
    print("    → 若提示 'sign is error'：字段格式全部正确，只差加密层与验签密钥")

    print("\n" + "=" * 64)
    print("结论判读：")
    print("  * 用户信息/车辆列表返回 200 + JSON  → 令牌有效，集成可直接使用")
    print("  * 返回 401/479/100504               → 令牌已过期/无效，需重新抓包")
    print("  * 返回 500                          → 后端网关异常，稍后再试或检查令牌格式")
    print("  * 远程指令类返回 commandId           → 远程控制可用")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(1)
