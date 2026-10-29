"""FordPass China 的请求签名运算核——纯 Python，无原生依赖。

运算所需的表数据由**运行时数据包**提供，不随代码仓库分发：

- 默认目录：``custom_components/fordpass_cn/data/``
- 运行时可由 ``set_data_dir`` 注入（用户自备目录，或集成自动下载的缓存）
- 齐备判据：``TABLE_FILES`` 内文件全部存在，且 ``KEY_FILE`` 存在

上层协议与云端一致：填充、链式模式、文本编码，细节见
``FordPassCrypto.encrypt_field`` / ``decrypt_field``。
"""
from __future__ import annotations

import base64
import json
import os
import random
import string
import struct
import threading
from typing import ClassVar

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_DATA_DIR = os.path.join(_PKG_DIR, "data")
_DATA_DIR = _DEFAULT_DATA_DIR
KEY_FILE = "whitebox_keys_for_prod.json"
_KEY_PATH = os.path.join(_DATA_DIR, KEY_FILE)

# 数据目录齐备的判据：下列表文件 + KEY_FILE，缺一不可。
TABLE_FILES: tuple[str, ...] = (
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
)

# 轮数：由数据包对应的算法参数决定
_ROUNDS = 14


def _load_key_hex(path: str, scene: str = "x_api") -> tuple[str, str]:
    """Read a key file. Supports the app's JSON (per-scene
    encrypt / decrypt, e.g. x_api / lbs / lbs_p2c) and the raw
    244-hex-char txt variant (single direction file, used by e.g. the LBS
    'wb_local_*' keys: the same file serves as both encrypt and decrypt
    body)."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read().strip()
    if text.startswith("{"):
        wj = json.loads(text)
        return wj[scene]["encrypt"], wj[scene]["decrypt"]
    # raw hex: 244 chars -> 122 bytes; de-obfuscated body is 240 bytes
    return text, text


class _WhiteBox:
    """表驱动的块运算（共享单例，只读）。

    从数据目录加载 ``TABLE_FILES`` 中的表，实现与云端一致的加/解密块；
    支持外部密钥流 rk（第二索引源）注入与任意轮数。
    """

    ROT = (0, 24, 16, 8)  # 加密行 0..3 的循环右移量

    def __init__(self) -> None:
        d = _DATA_DIR
        with open(os.path.join(d, "wbsk_tables_T_INIT.bin"), "rb") as fh:
            self.T_INIT = fh.read()
        with open(os.path.join(d, "wbsk_tables_T_KEY.bin"), "rb") as fh:
            self.T_KEY = fh.read()
        with open(os.path.join(d, "wbsk_tables_T_OUT.bin"), "rb") as fh:
            self.T_OUT = fh.read()
        with open(os.path.join(d, "wbsk_tables_TB.bin"), "rb") as fh:
            self.TB = list(struct.unpack("<256I", fh.read()))
        with open(os.path.join(d, "wbsk_tables_D_INIT.bin"), "rb") as fh:
            self.D_INIT = fh.read()
        with open(os.path.join(d, "wbsk_tables_D_FUS.bin"), "rb") as fh:
            self.D_FUS = fh.read()
        with open(os.path.join(d, "wbsk_tables_D_OUT.bin"), "rb") as fh:
            self.D_OUT = fh.read()
        self._views = []
        for name in ("D_VIEW0", "D_VIEW8", "D_VIEW16", "D_VIEW24"):
            with open(os.path.join(d, f"wbsk_tables_{name}.bin"), "rb") as fh:
                self._views.append(fh.read())
        # 末轮输出字节置换（列映射）
        self.PERM = [(4 * (j // 4) + 5 * (j % 4)) % 16 for j in range(16)]

    @staticmethod
    def _rol32(v: int, n: int) -> int:
        n &= 31
        return ((v << n) | (v >> (32 - n))) & 0xFFFFFFFF

    @staticmethod
    def _tb(v: int) -> bytes:
        return bytes(((v >> 24) & 0xFF, (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF))

    def _view(self, st: bytes, row: int) -> bytes:
        o = bytearray(16)
        rot = self.ROT[row]
        for c in range(4):
            src = (4 * c + 5 * row) % 16
            o[c * 4:c * 4 + 4] = self._tb(self._rol32(self.TB[st[src]], rot))
        return bytes(o)

    def encrypt_block(self, block: bytes, rk: bytes | None = None,
                      rounds: int = _ROUNDS) -> bytes:
        if len(block) != 16:
            raise ValueError("block must be 16 bytes")
        if rk is None or len(rk) < 16 * (rounds + 1):
            rk = bytes(16 * (rounds + 1))
        TK = self.T_KEY
        st = bytearray(self.T_INIT[(block[i] << 8) | rk[i]] for i in range(16))
        for r in range(1, rounds):
            V = [self._view(st, row) for row in range(4)]
            tA = bytes(TK[(V[0][i] << 8) | V[1][i]] for i in range(16))
            tA = bytes(TK[(tA[i] << 8) | V[2][i]] for i in range(16))
            t2 = bytes(TK[(tA[i] << 8) | V[3][i]] for i in range(16))
            st = bytearray(TK[(t2[i] << 8) | rk[r * 16 + i]] for i in range(16))
        return bytes(self.T_OUT[(st[self.PERM[j]] << 8) | rk[rounds * 16 + j]]
                     for j in range(16))

    @staticmethod
    def _dview(tab: bytes, idx: int) -> bytes:
        w = struct.unpack_from("<I", tab, idx * 4)[0]
        return bytes(((w >> 24) & 0xFF, (w >> 16) & 0xFF, (w >> 8) & 0xFF, w & 0xFF))

    def decrypt_block(self, block: bytes, rk: bytes | None = None,
                      rounds: int = _ROUNDS) -> bytes:
        if len(block) != 16:
            raise ValueError("block must be 16 bytes")
        if rk is None or len(rk) < 16 * (rounds + 1):
            rk = bytes(16 * (rounds + 1))
        FUS = self.D_FUS
        st = bytes(self.D_INIT[(block[i] << 8) | rk[i]] for i in range(16))
        for r in range(1, rounds):
            b58 = bytearray(16)
            b68 = bytearray(16)
            for c in range(4):
                b58[c * 4:c * 4 + 4] = self._dview(self._views[0], st[4 * c])
            for c, s in enumerate((13, 1, 5, 9)):
                b68[c * 4:c * 4 + 4] = self._dview(self._views[1], st[s])
            for i in range(16):
                b58[i] = FUS[(b58[i] << 8) | b68[i]]
            for c, s in enumerate((10, 14, 2, 6)):
                b68[c * 4:c * 4 + 4] = self._dview(self._views[2], st[s])
            for i in range(16):
                b58[i] = FUS[(b58[i] << 8) | b68[i]]
            for c, s in enumerate((7, 11, 15, 3)):
                b68[c * 4:c * 4 + 4] = self._dview(self._views[3], st[s])
            for i in range(16):
                b58[i] = FUS[(b58[i] << 8) | b68[i]]
            st = bytes(FUS[(b58[i] << 8) | rk[r * 16 + i]] for i in range(16))
        out = bytearray(16)
        for j in range(16):
            out[j] = self.D_OUT[(st[(0 - 3 * j) % 16] << 8) | rk[rounds * 16 + j]]
        return bytes(out)


_tables: _WhiteBox | None = None
_tables_lock = threading.Lock()


def _get_tables() -> _WhiteBox:
    global _tables
    if _tables is None:
        with _tables_lock:
            if _tables is None:
                _tables = _WhiteBox()
    return _tables


class FordPassCrypto:
    """Thread-safe wrapper around the pure-Python table-driven block cipher.

    全局单例按身份（key_file#scene）缓存——每个场景（x_api/cevs/lbs_p2c/
    lbs 等）各持一份密钥流与实例锁；表数据为模块级共享只读单例，
    构造零阻塞（不加载原生库、不映射内存）。
    """

    _instances: ClassVar[dict[str, FordPassCrypto]] = {}
    _lock = threading.Lock()

    @classmethod
    def get(cls, key_file: str | None = None, scene: str = "x_api") -> FordPassCrypto:
        """Per-identity instance cache; key_file + scene select the key.
        The default is the main prod key (x_api); the LBS gateway uses its own
        key (lbs_p2c for request fields, lbs for response fields), cevs uses
        the shared-session key."""
        identity = f"{key_file or _KEY_PATH}#{scene}"
        inst = cls._instances.get(identity)
        if inst is None:
            with cls._lock:
                inst = cls._instances.get(identity)
                if inst is None:
                    inst = cls(key_file, scene)
                    cls._instances[identity] = inst
        return inst

    def __init__(self, key_file: str | None = None, scene: str = "x_api") -> None:
        if not os.path.exists(key_file or _KEY_PATH):
            raise RuntimeError(
                "FordPass 运行时数据包缺失："
                f"{os.path.dirname(key_file or _KEY_PATH)} 下找不到所需文件。"
                "集成 setup 会自动尝试获取；若已看到本错误，请查看持久通知"
                "（设置 → 设备与服务），或把数据包解压到 "
                "config/fordpass_cn_data/ 后重新加载集成。"
            )
        enc_hex, dec_hex = _load_key_hex(key_file or _KEY_PATH, scene)
        raw_e = bytes.fromhex(enc_hex)
        raw_d = bytes.fromhex(dec_hex)
        self._body_enc = bytes(x ^ raw_e[(i + 4) % 3] for i, x in enumerate(raw_e[4:]))
        self._body_dec = bytes(x ^ raw_d[(i + 4) % 3] for i, x in enumerate(raw_d[4:]))
        if len(self._body_enc) != 240 or len(self._body_dec) != 240:
            raise RuntimeError("Unexpected key material length")
        self._lock = threading.Lock()

    def _block(self, data: bytes, direction: str) -> bytes:
        if len(data) != 16:
            raise ValueError("block must be 16 bytes")
        wb = _get_tables()
        if direction == "encrypt":
            return wb.encrypt_block(data, rk=self._body_enc, rounds=_ROUNDS)
        return wb.decrypt_block(data, rk=self._body_dec, rounds=_ROUNDS)

    @staticmethod
    def _new_iv() -> bytes:
        # 修复（v5.6.6）：IV 必须为 16 个小写字母（a-z）——App 与网关协议
        # 要求 xjw 为字母 hex；v5.6.0 误改 os.urandom 后任意字节 IV 被网关
        # 拒绝（generate-passcode 返回 500 解密失败）。
        # 与实测 IV（oihilknuiwtxnm 等）逐字符一致。
        return "".join(random.choice(string.ascii_lowercase) for _ in range(16)).encode()

    @staticmethod
    def _pkcs7(data: bytes) -> bytes:
        p = 16 - len(data) % 16
        return data + bytes([p]) * p

    def encrypt_field(self, text: str, iv: bytes | None = None) -> tuple[str, str]:
        """Encrypt a string the way the app does.

        The gateway reuses one IV (xjw) for every encrypted field of the
        same login session (verified against live captures #79/#83), so a
        caller may pass the session IV explicitly; otherwise a fresh random
        IV is generated.

        Returns (base64_ciphertext, xjw) where xjw is the hex-encoded IV.
        """
        with self._lock:
            iv = iv if iv is not None else self._new_iv()
            data = self._pkcs7(text.encode("utf-8"))
            prev, out = iv, b""
            for i in range(0, len(data), 16):
                blk = bytes(a ^ b for a, b in zip(data[i:i + 16], prev, strict=False))
                c = self._block(blk, "encrypt")
                out += c
                prev = c
        return base64.b64encode(out).decode("ascii"), iv.hex()

    def decrypt_field(self, ciphertext_b64: str, xjw: str) -> str:
        with self._lock:
            # P1-11（v5.6.0）：base64 非法字符不再静默丢弃（validate=True）；
            # 密文长度与 PKCS#7 填充严格校验——末字节 0/超 16 曾静默返回
            # 空串/截错，VIN/地址/token 莫名变空被掩盖到下游。
            ct = base64.b64decode(ciphertext_b64, validate=True)
            if not ct or len(ct) % 16:
                raise ValueError(
                    f"ciphertext length not multiple of 16: {len(ct)}"
                )
            prev = bytes.fromhex(xjw)
            out = b""
            for i in range(0, len(ct), 16):
                mid = self._block(ct[i:i + 16], "decrypt")
                out += bytes(a ^ b for a, b in zip(mid, prev, strict=False))
                prev = ct[i:i + 16]
            pad = out[-1]
            if not 1 <= pad <= 16 or out[-pad:] != bytes([pad]) * pad:
                raise ValueError(f"bad PKCS#7 padding: pad={pad}")
            return out[:-pad].decode("utf-8")


# ---------------------------------------------------------------------------
# v6.1.0: 数据目录解析与注入
# ---------------------------------------------------------------------------
# v6.2.1: App 固定值文件（app_constants.json）纳入齐备判据——旧数据包
# （v1.0，无此文件）会被判"不齐备"而触发换包下载；自备目录缺少该文件
# 同样会落到自动下载/补齐提示，避免"表齐了但固定值缺失"的隐性问题。
APP_CONSTANTS_FILE = "app_constants.json"


def data_ready(path: str | None = None) -> bool:
    """目录内是否齐备（``TABLE_FILES`` + ``KEY_FILE`` + ``app_constants.json``）。

    只做静态检查，不加载表——用于在 setup 阶段判定"够不够用"，避免为了
    探测而把整包表读进内存。v6.2.1 起 App 固定值文件也必需（缺失即旧包，
    触发换包下载）。
    """
    d = _DATA_DIR if path is None else path
    try:
        return all(
            os.path.isfile(os.path.join(d, n))
            for n in (*TABLE_FILES, KEY_FILE, APP_CONSTANTS_FILE)
        )
    except OSError:
        return False


def data_dir() -> str:
    """当前生效的数据目录（诊断用）。"""
    return _DATA_DIR


def bundled_data_dir() -> str:
    """随包目录（custom_components/fordpass_cn/data）。

    过渡版本仍随包分发数据时由这里命中；移除 data/ 之后该目录不存在，
    ``data_ready`` 返回 False，集成自动落到自备/缓存/下载等其他来源。
    """
    return _DEFAULT_DATA_DIR


def set_data_dir(path: str) -> None:
    """注入数据目录并使已缓存的表/密钥失效。

    单例按目录缓存：``_get_tables()`` 缓存整批表、``FordPassCrypto._instances``
    按 ``key_file#scene`` 缓存实例。换目录后不清空会继续用旧表（数据目录
    漂移时静默算错密文），所以必须一并清掉。
    """
    global _DATA_DIR, _KEY_PATH
    if os.path.abspath(path) == os.path.abspath(_DATA_DIR):
        return
    _DATA_DIR = path
    _KEY_PATH = os.path.join(path, KEY_FILE)
    _invalidate_cache()


def _invalidate_cache() -> None:
    global _tables
    with _tables_lock:
        _tables = None
    with FordPassCrypto._lock:
        FordPassCrypto._instances.clear()
