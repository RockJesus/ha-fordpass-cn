"""White-box AES for FordPass China.

The official app encrypts sensitive fields (phone number, SMS passcode, VIN)
with a custom white-box AES-256-CBC implementation (libwbsk_crypto_tool.so,
package com.wbsk.CryptoTool). No raw AES key exists at runtime.

We execute the original, verified machine code with the Unicorn CPU emulator,
so results are bit-for-bit identical to the official app. The 244-byte key
file is de-obfuscated to a 240-byte (15 round-key) body; CBC chaining and
PKCS#7 padding are reproduced here.
"""
from __future__ import annotations

import base64
import json
import os
import random
import string
import threading

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_SO_PATH = os.path.join(_DATA_DIR, "libwbsk_crypto_tool.so")
_KEY_PATH = os.path.join(_DATA_DIR, "whitebox_keys_for_prod.json")

_PAGE = 0x1000
_GUARD = 0x600000
_SCRATCH = 0x700000
_STACK_BASE = 0x800000
_HALT = 0x900000


class FordPassCrypto:
    """Thread-safe wrapper around the emulated white-box block cipher."""

    _instance: "FordPassCrypto | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        from elftools.elf.elffile import ELFFile
        from elftools.elf.relocation import RelocationSection
        from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_LITTLE_ENDIAN, UC_PROT_ALL
        from unicorn.arm64_const import UC_ARM64_REG_SP, UC_ARM64_REG_X0, UC_ARM64_REG_X1
        from unicorn.arm64_const import UC_ARM64_REG_X2, UC_ARM64_REG_X3, UC_ARM64_REG_LR

        self._regs = dict(
            SP=UC_ARM64_REG_SP, X0=UC_ARM64_REG_X0, X1=UC_ARM64_REG_X1,
            X2=UC_ARM64_REG_X2, X3=UC_ARM64_REG_X3, LR=UC_ARM64_REG_LR,
        )

        with open(_SO_PATH, "rb") as fh:
            elf = ELFFile(fh)
            loads = [
                (s["p_vaddr"], s["p_filesz"], s.data(), s["p_memsz"])
                for s in elf.iter_segments()
                if s["p_type"] == "PT_LOAD"
            ]
            uc = Uc(UC_ARCH_ARM64, UC_MODE_LITTLE_ENDIAN)
            ad = lambda x: x & ~(_PAGE - 1)
            au = lambda x: (x + _PAGE - 1) & ~(_PAGE - 1)
            pages: set[int] = set()
            for v, sz, _d, ms in loads:
                a = ad(v)
                while a < au(v + max(sz, ms)):
                    pages.add(a)
                    a += _PAGE
            for p in sorted(pages):
                uc.mem_map(p, _PAGE, UC_PROT_ALL)
            for v, _sz, d, _ms in loads:
                uc.mem_write(v, d)
            for sec in elf.iter_sections():
                if isinstance(sec, RelocationSection):
                    for rel in sec.iter_relocations():
                        if rel["r_info_type"] == 1027:  # R_AARCH64_RELATIVE
                            uc.mem_write(rel["r_offset"], rel["r_addend"].to_bytes(8, "little"))

        # stack canard
        uc.mem_map(_GUARD, _PAGE)
        uc.mem_write(_GUARD, b"\x00" * 8)
        uc.mem_write(0x7AF90, _GUARD.to_bytes(8, "little"))
        # scratch pages
        uc.mem_map(_SCRATCH, 0x10000)
        self._in = _SCRATCH
        self._out = _SCRATCH + 0x100
        self._key = _SCRATCH + 0x200
        # de-obfuscate encrypt / decrypt bodies
        with open(_KEY_PATH, encoding="utf-8") as fh:
            wj = json.load(fh)
        self._bodies: dict[str, int] = {}
        for direction, addr in (("encrypt", _SCRATCH + 0x300), ("decrypt", _SCRATCH + 0x1300)):
            raw = bytes.fromhex(wj["x_api"][direction])
            body = bytes(x ^ raw[(i + 4) % 3] for i, x in enumerate(raw[4:]))
            if len(body) != 240:
                raise RuntimeError("Unexpected white-box key length")
            uc.mem_write(addr, body)
            self._bodies[direction] = addr
        uc.mem_write(self._key + 8, (240).to_bytes(8, "little"))
        uc.mem_write(self._key + 0x10, (256).to_bytes(4, "little"))  # AES-256
        uc.mem_map(_STACK_BASE, 0x10000)
        uc.mem_map(_HALT, _PAGE)
        self._uc = uc

    @classmethod
    def get(cls) -> "FordPassCrypto":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _block(self, data: bytes, direction: str) -> bytes:
        if len(data) != 16:
            raise ValueError("block must be 16 bytes")
        fn = 0x3004 if direction == "encrypt" else 0x41C4
        uc = self._uc
        r = self._regs
        uc.mem_write(self._in, data)
        uc.mem_write(self._key, self._bodies[direction].to_bytes(8, "little"))
        uc.reg_write(r["SP"], _STACK_BASE + 0x8000)
        uc.reg_write(r["X0"], self._in)
        uc.reg_write(r["X1"], self._out)
        uc.reg_write(r["X2"], self._key)
        uc.reg_write(r["X3"], 0)
        uc.reg_write(r["LR"], _HALT)
        uc.emu_start(fn, _HALT, count=400000)
        return bytes(uc.mem_read(self._out, 16))

    @staticmethod
    def _new_iv() -> bytes:
        return "".join(random.choice(string.ascii_lowercase) for _ in range(16)).encode()

    @staticmethod
    def _pkcs7(data: bytes) -> bytes:
        p = 16 - len(data) % 16
        return data + bytes([p]) * p

    def encrypt_field(self, text: str) -> tuple[str, str]:
        """Encrypt a string the way the app does.

        Returns (base64_ciphertext, xjw) where xjw is the hex-encoded IV.
        """
        with self._lock:
            iv = self._new_iv()
            data = self._pkcs7(text.encode("utf-8"))
            prev, out = iv, b""
            for i in range(0, len(data), 16):
                blk = bytes(a ^ b for a, b in zip(data[i:i + 16], prev))
                c = self._block(blk, "encrypt")
                out += c
                prev = c
        return base64.b64encode(out).decode("ascii"), iv.hex()

    def decrypt_field(self, ciphertext_b64: str, xjw: str) -> str:
        with self._lock:
            ct = base64.b64decode(ciphertext_b64)
            prev = bytes.fromhex(xjw)
            out = b""
            for i in range(0, len(ct), 16):
                mid = self._block(ct[i:i + 16], "decrypt")
                out += bytes(a ^ b for a, b in zip(mid, prev))
                prev = ct[i:i + 16]
            pad = out[-1]
            return out[:-pad].decode("utf-8")
