"""随集成自带的前端资源：爱车卡片 ha-car。

卡片是本集成实体的一对一可视化前端（HACS 政策要求随集成发布），
这里把构建产物挂在集成的静态路径下，并在 setup 时注册为前端资源，
用户更新集成即可拿到最新卡片，无需另外安装 HACS 插件。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

CARD_URL_PATH = "/fordpass_cn/ha-car.js"
_STATIC_URL = "/fordpass_cn"
_DONE_KEY = "fordpass_cn_frontend_registered"


def _card_path() -> Path:
    return Path(__file__).parent / "www" / "ha-car.js"


def _version() -> str:
    """读取 manifest.json 版本号（同步 IO，必须在 executor 中调用）。"""
    try:
        manifest = Path(__file__).parent / "manifest.json"
        return str(json.loads(manifest.read_text("utf-8")).get("version", "0"))
    except Exception:  # noqa: BLE001 - 版本仅用于缓存串
        return "0"


async def async_register_card(hass: HomeAssistant) -> None:
    """注册静态路径与卡片前端资源（幂等，reload 不会重复注册）。"""
    if hass.data.get(_DONE_KEY):
        return

    if getattr(hass, "http", None) is None:
        # http 组件尚未就绪（极少见）：交给 entry setup 再注册，避免这里静默失败
        return

    path = _card_path()
    if not path.is_file():
        _LOGGER.warning("福特派互联：缺少内置爱车卡片资源 %s", path)
        return

    try:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(_STATIC_URL, str(path.parent), False)]
        )
    except Exception as err:  # noqa: BLE001 - 已注册（热重载）时忽略
        _LOGGER.debug("福特派互联：静态路径已存在，跳过注册：%s", err)

    # 带版本号做缓存击穿：升级后浏览器立刻拿到新卡片
    # 版本读取是同步文件 IO，放 executor，避免事件循环阻塞告警
    try:
        version = await hass.async_add_executor_job(_version)
        add_extra_js_url(hass, f"{CARD_URL_PATH}?v={version}")
    except Exception as err:  # noqa: BLE001 - 前端资源注册失败不应阻断集成加载
        _LOGGER.warning("福特派互联：爱车卡片前端资源注册失败：%s", err)
        return
    hass.data[_DONE_KEY] = True
    _LOGGER.debug("福特派互联：爱车卡片已注册为前端资源 %s", CARD_URL_PATH)
