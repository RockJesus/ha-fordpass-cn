#!/usr/bin/env python3
"""福特派（中国）白盒 AES 密钥破解脚本（2026-08）

背景：2026 新认证体系中，手机号 / 验证码 / refresh_token 在传输前经
Dart 侧 pointycastle 的 AES-CBC/PKCS7 加密（"白盒"实现），密钥硬编码在
libapp.so 里。libapp.so 中找到了 9 个 32 字符随机串（不同服务的 secretKey
池）与若干 IV 候选。要确定「令牌加密」用的是哪一组 key+IV，需要一个
【已知明密文对】：你输入自己的手机号（明文），再给它 App 抓包得到的
encryptedPhoneNumber（密文），脚本会穷举所有 key/IV/密钥长度组合，
找出能正确加解密的那一组。

用法：
    python crack_aes.py --phone "13800138000" --ct "aB3xYz...=="

--ct 来源：抓包福特派 App 的 POST
    /api/cnxapi-token-exchange/v1/app/dlt-token-by-phone-passcode-login
    请求体里的 encryptedPhoneNumber 字段值。

依赖：仅 Python 标准库（AES 用纯 Python 实现的 Crypto 不可用时的备用）。
推荐先 pip install pycryptodome 提速（可选）。
"""

import argparse
import base64
import itertools
import sys

# ---- libapp.so 中提取的候选密钥（32 字符，各服务 secretKey 池）----
KEYS_32 = [
    "mOCBCVsQX8KvCnjThkxM4vwxoegWZKy2",   # 距 encryptedAuthCode 仅 746B
    "qA6i0NStHI2T5QFc2ZHUG0cH9nH3rKxF",   # 邻近 aesEncryptStringWithBase64
    "aRsQCHpTyNyTKAPb4bRlzF0EMMS8H8nV",   # 邻近 providerBase64Iv/encryptedPhoneNumber
    "kMuOFcvY13435zQLw1Kpki7IH0Wp696Q",   # 邻近 encryptedRefreshToken
    "4qjSFHwgd9IBrT35vzNmiY1S4cHqD5Lo",
    "f6ZHP0g5yNbPRvRio9tjvf621lypsLut",
    "vQojyNASF35KA5gfjPFQ66Dg3A09zC1i",
    "xKCV6GDQaWMddjsZTe0qcR21ePYzwfg4",
    "Ym+ackMJrwkOqo2O0fPnUy55iTuLKsRK",   # 含 +，base64 特征
]

# ---- IV 候选 ----
IVS = [
    "d558Gq0YQK2QUlM2",
    "0000000000000000",
    "1234567890abcdef",
    "",
]

# 常见静态 IV
IVS += ["\x00" * 16, "0" * 16, "a" * 16]


def to_key_candidates(s: str):
    """把一个候选串扩展为可能的密钥字节：UTF-8 / base64 解码。"""
    yield s.encode("utf-8")                    # 直接 UTF-8（32B → AES-256）
    if len(s) >= 24:
        try:
            yield base64.b64decode(s + "=" * (-len(s) % 4))   # base64 → 24B AES-192
        except Exception:
            pass
    if len(s) >= 16 and len(s) < 24:
        try:
            yield base64.b64decode(s + "=" * (-len(s) % 4))   # base64 → 16B AES-128
        except Exception:
            pass


def to_iv_bytes(s: str):
    if not s:
        yield b"\x00" * 16
        return
    yield s.encode("utf-8")[:16].ljust(16, b"\x00")
    if len(s) == 16:
        try:
            yield base64.b64decode(s)
        except Exception:
            pass


def plaintext_candidates(phone: str):
    p = phone.strip()
    yield p
    yield p.replace(" ", "")
    yield p.replace("-", "")
    if not p.startswith("+"):
        yield "+86" + p
        yield "86" + p
    yield '"' + p + '"'
    yield '{"phone":"%s"}' % p
    yield '{"phoneNumber":"%s"}' % p
    yield '{"mobile":"%s"}' % p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phone", required=True, help="你的手机号（明文）")
    ap.add_argument("--ct", required=True, help="抓包到的 encryptedPhoneNumber（base64 密文）")
    args = ap.parse_args()

    try:
        ct = base64.b64decode(args.ct)
    except Exception:
        # 兼容非标准 padding
        ct = base64.b64decode(args.ct + "=" * (-len(args.ct) % 4))

    # 优先 pycryptodome，否则用纯 Python 备用实现
    try:
        from Crypto.Cipher import AES
        aes_new = AES.new
        mod = "pycryptodome"
    except ImportError:
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            def aes_new(key, mode, iv):
                return Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
            mod = "cryptography"
        except ImportError:
            sys.exit("请先安装 pycryptodome 或 cryptography：python -m pip install pycryptodome -i https://pypi.tuna.tsinghua.edu.cn/simple")
    print(f"[*] 使用加密后端: {mod}")
    print(f"[*] 明文候选: {args.phone.strip()}, 密文长度: {len(ct)} 字节\n")

    pads = [
        lambda d: d + bytes([16 - len(d) % 16]) * (16 - len(d) % 16),  # PKCS7（Dart AesCbcPkcs7）
        lambda d: d,                                                     # 无填充（须 16 对齐）
        lambda d: d + b"\x00" * (-len(d) % 16),                          # 零填充
    ]

    found = []
    total = 0
    for ks in KEYS_32 + ["d558Gq0YQK2QUlM2"]:
        for kb in to_key_candidates(ks):
            if len(kb) not in (16, 24, 32):
                continue
            for ivs in IVS:
                for ivb in to_iv_bytes(ivs):
                    if len(ivb) != 16:
                        continue
                    for pt in plaintext_candidates(args.phone):
                        for pad in pads:
                            total += 1
                            data = pad(pt.encode("utf-8"))
                            if len(data) % 16 != 0:
                                continue
                            try:
                                enc = aes_new(kb, 2, ivb)  # 2 = MODE_CBC
                                out = enc.encrypt(data)
                            except Exception:
                                continue
                            if base64.b64encode(out).decode() == args.ct.strip() or out == ct:
                                found.append((ks, kb, ivs, ivb, pt))
                                print(f"[+] 命中! key={ks!r} (len={len(kb)}) iv={ivs!r} plaintext={pt!r}")
    print(f"\n[*] 共尝试 {total} 种组合，命中 {len(found)} 条")
    if not found:
        print("[!] 未命中。请确认：")
        print("    1. 手机号输入是否与 App 中登录的手机号完全一致")
        print("    2. 密文是否为请求体 encryptedPhoneNumber 的完整原值（不是 URL 编码/截断的）")
        print("    3. 若手机号在 App 里被拼接了区号/JSON 包装，可尝试修改 plaintext_candidates()")


if __name__ == "__main__":
    main()
