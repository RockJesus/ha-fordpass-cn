# -*- coding: utf-8 -*-
"""白盒 AES（wbsk）核心单测——v4.9.7 新增。

覆盖：11 张导出表加载、密钥文件解析、加密确定性（固定 IV）、
加密→解密自洽还原、输出格式（base64 + xjw hex）。
运行：pytest tests/test_wbsk.py（仓库根执行）。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from custom_components.fordpass_cn import wbsk  # noqa: E402

DATA_DIR = os.path.join(
    os.path.dirname(os.path.abspath(wbsk.__file__)), "data"
)

TABLE_FILES = [
    "wbsk_tables_T_INIT.bin",
    "wbsk_tables_T_KEY.bin",
    "wbsk_tables_T_OUT.bin",
    "wbsk_tables_TB.bin",
    "wbsk_tables_D_INIT.bin",
    "wbsk_tables_D_FUS.bin",
    "wbsk_tables_D_OUT.bin",
    "wbsk_tables_D_VIEW0.bin",
    "wbsk_tables_D_VIEW8.bin",
    "wbsk_tables_D_VIEW16.bin",
    "wbsk_tables_D_VIEW24.bin",
]


def test_tables_all_exist() -> None:
    """11 张导出表必须全部存在（缺表 = 加密全挂）。"""
    for name in TABLE_FILES:
        p = os.path.join(DATA_DIR, name)
        assert os.path.exists(p), f"缺表: {name}"
        assert os.path.getsize(p) > 0, f"空表: {name}"


def test_key_file_parses() -> None:
    """whitebox_keys_for_prod.json 可解析且含 9 场景 encrypt/decrypt。"""
    path = wbsk._KEY_PATH
    assert os.path.exists(path), "缺 whitebox_keys_for_prod.json"
    enc, dec = wbsk._load_key_hex(path, "x_api")
    assert isinstance(enc, str) and enc.strip(), "encrypt body 为空"
    assert isinstance(dec, str) and dec.strip(), "decrypt body 为空"
    # body 应为 244 字节（240 字节密钥流 + 4 字节 skb 头），488 hex 字符
    assert len(enc) // 2 == 244, f"encrypt body 长度异常: {len(enc)//2}"
    assert len(dec) // 2 == 244, f"decrypt body 长度异常: {len(dec)//2}"


def test_rounds_constant() -> None:
    """位宽档 256 → rounds = 256/32+6 = 14。"""
    assert wbsk._ROUNDS == 14


def test_encrypt_deterministic_with_fixed_iv() -> None:
    """固定 IV 下同输入两次加密结果 bit 级一致（确定性）。"""
    crypto = wbsk.FordPassCrypto.get()
    iv = b"0123456789abcdef"
    a1, iv1 = crypto.encrypt_field("测试AB123", iv=iv)
    a2, iv2 = crypto.encrypt_field("测试AB123", iv=iv)
    assert a1 == a2
    assert iv1 == iv2


def test_decrypt_roundtrip() -> None:
    """同一 xjw 下 decrypt(encrypt(x)) 还原原文（协议自洽）。"""
    crypto = wbsk.FordPassCrypto.get()
    for sample in ("LVSHFCAC3LS950635", "测试AB123", "{}", "a" * 200):
        enc, iv = crypto.encrypt_field(sample)
        assert crypto.decrypt_field(enc, iv) == sample


def test_output_format() -> None:
    """输出格式：密文 base64、xjw 为 hex 字符串（非空）。"""
    crypto = wbsk.FordPassCrypto.get()
    enc, iv = crypto.encrypt_field("hello")
    import base64

    base64.b64decode(enc)  # 抛异常 = 非 base64
    assert len(iv) == 32  # 16 字节 hex
    int(iv, 16)  # 抛异常 = 非 hex
