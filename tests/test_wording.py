"""叙述红线：仓库正文不得再复述已与代码解耦的数据文件"是什么、从哪来"。

数据文件自 v6.1.1 起改为运行时按需获取。若文档或注释里继续描述其内容、
数量与来路，等于把已经移除的东西重新讲一遍，对外风险并没有真正消除。
本测试守住这条线：新增文案复述这些词时，CI 直接失败。

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
    "DMCA",
    "white-box",
    "whitebox",
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
