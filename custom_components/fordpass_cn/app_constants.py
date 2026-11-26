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

# v7.1.2: 林肯附加包的固定值覆盖文件（可选）。只有配置为林肯品牌的 entry
# 才会取到它；取不到时字典为空，get() 继续返回主包（福特）的值。
LINCOLN_CONSTANTS_FILE = "lincoln_app_constants.json"

# 全局值字典：数据包就绪后填充
_VALUES: dict[str, str] = {}

# 林肯覆盖值（可选附加包提供，优先级高于主包同名键）
_LINCOLN: dict[str, str] = {}


def load_from(path: str) -> bool:
    """从数据目录读取 app_constants.json 并填充全局字典。

    目录里没有该文件（旧版数据包 / 未获取）返回 False，字典保持空——
    与数据包缺失的既有降级语义一致。文件内容非法同样返回 False 并告警。

    读取成功会一并清空林肯覆盖值：换目录后旧的附加包覆盖必须失效，
    否则会在新目录下静默沿用上一份的值（与 wbsk 换目录清缓存同理）。
    """
    global _VALUES, _LINCOLN
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
    _LINCOLN = {}
    _LOGGER.debug("FordPass App 固定值已加载（%d 项）", len(_VALUES))
    return True


def load_lincoln_from(path: str) -> bool:
    """读取林肯附加包的固定值覆盖（可选）。

    与 ``load_from`` 一样只看单文件；不存在/非法一律返回 False 且不改动
    已有覆盖值以外的内容——附加包缺失属于正常情况（福特用户永远没有它）。
    """
    global _LINCOLN
    fp = os.path.join(path, LINCOLN_CONSTANTS_FILE)
    if not os.path.isfile(fp):
        _LINCOLN = {}
        return False
    try:
        with open(fp, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as err:
        _LOGGER.warning("FordPass lincoln_app_constants.json 读取失败：%s", err)
        _LINCOLN = {}
        return False
    if not isinstance(data, dict):
        _LOGGER.warning("FordPass lincoln_app_constants.json 内容非法（非对象）")
        _LINCOLN = {}
        return False
    _LINCOLN = {str(k): str(v) for k, v in data.items() if isinstance(v, (str, int))}
    _LOGGER.debug("FordPass 林肯固定值覆盖已加载（%d 项）", len(_LINCOLN))
    return True


def get(name: str) -> str | None:
    """按名字取 App 固定值；林肯覆盖优先，其次主包，缺失返回 None。"""
    if name in _LINCOLN:
        return _LINCOLN[name]
    return _VALUES.get(name)


def loaded_names() -> tuple[str, ...]:
    """已加载的键集合（诊断用，含林肯覆盖键）。"""
    return tuple(sorted(set(_VALUES) | set(_LINCOLN)))


def lincoln_loaded() -> bool:
    """林肯覆盖是否生效（诊断用）。"""
    return bool(_LINCOLN)
