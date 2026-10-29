"""叙述红线：仓库正文不得再复述已与代码解耦的数据文件"是什么、从哪来"，
也不得出现任何自认逆向 / 抓包 / 破解 / 解密密钥来源的叙述（v6.2.3 扩展）。

数据文件自 v6.1.1 起改为运行时按需获取。若文档或注释里继续描述其内容、
数量与来路，等于把已经移除的东西重新讲一遍，对外风险并没有真正消除。
本测试守住这条线：新增文案复述这些词时，CI 直接失败。

v6.2.3 扩展词表：逆向 / 抓包 / 破解 / 法律风险 / 自认 / 数据包下载 /
下载数据包 / 解密密钥。技术性术语（decrypt_field、AES/RSA 协议行为、
加解密函数名）属正常实现描述，不在拦截范围。

被检词在下面以 Unicode 转义形式给出，避免本文件自己成为违规样本；想看
具体词表直接打印 ``BANNED``。

数据包内的真实文件名属于功能性标识（加载逻辑要靠它），逐行放行。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 本文件自身要定义词表，跳过自己
SELF = Path(__file__).name

BANNED = (
    "\u767d\u76d2",  # 词表 1
    "\u52a0\u5bc6\u8868",  # 词表 2
    "\u9006\u5411\u6750\u6599",  # 词表 3
    "\u53cd\u89c4\u907f",  # 词表 4
    "\u0044\u004d\u0043\u0041",  # 词表 5
    "\u0077hite-box",  # 词表 6
    "\u0077hitebox",  # 词表 7
    "\u9006\u5411",  # 词表 8：逆向
    "\u6293\u5305",  # 词表 9：抓包
    "\u7834\u89e3",  # 词表 10：破解
    "\u6cd5\u5f8b\u98ce\u9669",  # 词表 11：法律风险
    "\u81ea\u8ba4",  # 词表 12：自认
    "\u6570\u636e\u5305\u4e0b\u8f7d",  # 词表 13：数据包下载
    "\u4e0b\u8f7d\u6570\u636e\u5305",  # 词表 14：下载数据包
    "\u89e3\u5bc6\u5bc6\u94a5",  # 词表 15：解密密钥
)
ALLOW_LINE = "whitebox_keys_for_prod.json"  # 功能性文件名，不算叙述
SUFFIXES = {".py", ".md", ".yaml", ".yml", ".json", ".html"}
SKIP_DIRS = {".git", "__pycache__", ".ruff_cache", ".venv", "node_modules"}


def _text_files() -> list[Path]:
    out: list[Path] = []
    for path in ROOT.rglob("*"):
        if path.is_dir() or path.name == SELF:
            continue
        if SKIP_DIRS & set(path.parts):
            continue
        if path.suffix in SUFFIXES or path.name == ".gitignore":
            out.append(path)
    return out


def test_no_removed_material_narrative() -> None:
    hits: list[str] = []
    for path in _text_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for lineno, line in enumerate(text.splitlines(), 1):
            if ALLOW_LINE in line:
                continue
            for word in BANNED:
                if word in line:
                    rel = path.relative_to(ROOT).as_posix()
                    hits.append(f"{rel}:{lineno} 命中「{word}」→ {line.strip()[:90]}")
    assert not hits, "仓库正文出现已移除材料的叙述：\n" + "\n".join(hits)


# ---------------------------------------------------------------------------
# v6.2.3: 全局脱敏检测——真实 VIN / 手机号禁止入库（CI 固化）。
# 规则与发布脚本 docs/privacy_audit.py 保持一致：
#   VIN 正则排除 FAKE 前缀（README 示例假 VIN LVSHFAKE000000001 可保留）；
#   手机号正则排除纯数字长串误报（前后不能是数字）。
# ---------------------------------------------------------------------------
import re as _re

_PRIVATE_WORDS = (
    "\u0031\u0033\u0038\u0030\u0030\u0030\u0030\u0030\u0030\u0030\u0030",
    "\u0031\u0033\u0030\u0037\u0034\u0037\u0032\u0032\u0039\u0032\u0039",
    "\u004c\u0056\u0053\u0048\u0046\u0043\u0041\u0043",
    "\u0030\u0066\u0032\u0066\u0032\u0037\u0038",
    "\u0031\u0035\u0035\u0063\u0032\u0065\u0065",
    "\u0033\u0064\u0039\u0039\u0066\u0030\u0064",
    "\u0031\u0061\u0036\u0039\u0065\u0062\u0039",
)
_VIN_RE = _re.compile(r"\u004c\u0056\u0053\u0048(?!\u0046\u0041\u004b\u0045)[A-Z0-9]{13}")
_PHONE_RE = _re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")


def test_no_real_pii() -> None:
    """全局脱敏：真实 VIN / 手机号 / 历史敏感串不得出现在任何文本文件。"""
    hits: list[str] = []
    for path in _text_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for lineno, line in enumerate(text.splitlines(), 1):
            for w in _PRIVATE_WORDS:
                if w in line:
                    hits.append(f"{path.relative_to(ROOT).as_posix()}:{lineno} 敏感词「{w}」")
            for m in _VIN_RE.findall(line):
                hits.append(f"{path.relative_to(ROOT).as_posix()}:{lineno} 疑似真实 VIN「{m}」")
            for m in _PHONE_RE.findall(line):
                hits.append(f"{path.relative_to(ROOT).as_posix()}:{lineno} 疑似手机号「{m}」")
    assert not hits, "仓库出现疑似真实个人信息：\n" + "\n".join(hits)
