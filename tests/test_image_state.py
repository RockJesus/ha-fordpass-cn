"""v6.4.7 回归测试：图片实体状态必须是有效时间戳（不覆盖 core 的 state）。

HA 2026.11 的 image 实体把状态固定为「图片最后更新时间」（core 的 state
返回 ``image_last_updated.isoformat()``），前端对 image 域一律按时间戳渲染，
状态不是有效时间就显示「时间戳无效」。子类覆盖 state 返回中文名称会让界面
全部变成这行报错，因此这里锁死：不得覆盖 state，且必须提供 image_last_updated。
"""
from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "fordpass_cn"
IMAGE_PY = SRC / "image.py"


def _text() -> str:
    return IMAGE_PY.read_text(encoding="utf-8")


def test_image_entity_does_not_override_state():
    """image 平台不得覆盖 state（覆盖后前端显示「时间戳无效」）。"""
    text = _text()
    assert "def state" not in text, "image.py 覆盖了 state——image 域状态只能是时间戳"


def test_all_image_entities_provide_last_updated():
    """三个图片实体都必须继承时间戳 mixin，并提供 image_last_updated。"""
    text = _text()
    assert "class _ImageTimestampMixin" in text
    assert "def image_last_updated(self)" in text
    subclasses = re.findall(r"class (FordPass\w+)\((.*?)\):", text, re.S)
    assert len(subclasses) == 3, subclasses
    for name, bases in subclasses:
        assert "_ImageTimestampMixin" in bases, f"{name} 未继承时间戳 mixin"


def test_image_refresh_marks_last_updated():
    """新图下载成功后要推进 image_last_updated（否则状态时间停在重启时刻）。"""
    text = _text()
    assert text.count("self._mark_image_updated()") >= 3, "三处下载分支都应标记更新时间"
    assert text.count("await self._async_seed_image_timestamp()") >= 3, "本地缓存命中应播种时间"
    assert "datetime.fromtimestamp(mtime, tz=timezone.utc)" in text, "播种时间必须带 UTC 时区"
