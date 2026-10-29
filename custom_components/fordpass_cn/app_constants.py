"""App 固定值运行时加载器（v6.2.1）。

LBS 网关 ID/Key、应用 ID、客户端/OS 标识、签名密钥等云端互操作所需的
App 固定值，自 v6.2.1 起不再硬编码在 const.py，改由**运行时数据包**
（data-pack 内的 app_constants.json）提供：

- 数据包就绪（wbsk.set_data_dir 注入数据目录）后调用 :func:`load_from`
  填充本模块的全局字典；
- 读取统一走 :func:`get`（缺失返回 None，调用方按数据包缺失的既有
  降级策略处理——集成照常加载、仅依赖签名的请求不可用）。

无个人信息（VIN/手机号/账号），只含互操作固定值；不依赖 const/api，
避免循环导入。
"""
from __future__ import annotations

import json
import logging
import os

_LOGGER = logging.getLogger(__name__)

# 数据包内承载 App 固定值的文件名
APP_CONSTANTS_FILE = "app_constants.json"

# 全局值字典：数据包就绪后填充
_VALUES: dict[str, str] = {}


def load_from(path: str) -> bool:
    """从数据目录读取 app_constants.json 并填充全局字典。

    目录里没有该文件（旧版数据包 / 未下载）返回 False，字典保持空——
    与数据包缺失的既有降级语义一致。文件内容非法同样返回 False 并告警。
    """
    global _VALUES
    fp = os.path.join(path, APP_CONSTANTS_FILE)
    if not os.path.isfile(fp):
        return False
    try:
        with open(fp, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as err:
        _LOGGER.warning("FordPass app_constants.json 读取失败：%s", err)
        return False
    if not isinstance(data, dict):
        _LOGGER.warning("FordPass app_constants.json 内容非法（非对象）")
        return False
    _VALUES = {str(k): str(v) for k, v in data.items() if isinstance(v, (str, int))}
    _LOGGER.debug("FordPass App 固定值已加载（%d 项）", len(_VALUES))
    return True


def get(name: str) -> str | None:
    """按名字取 App 固定值；数据包未就绪或键不存在返回 None。"""
    return _VALUES.get(name)


def loaded_names() -> tuple[str, ...]:
    """已加载的键集合（诊断用）。"""
    return tuple(sorted(_VALUES))
