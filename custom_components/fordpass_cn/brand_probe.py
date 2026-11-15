"""品牌与车辆归属判定（v6.7.0）。

同一手机号在福特派与林肯之道是两套账号体系：登录网关共用，但车辆列表按
``appKey`` / ``clientType`` 区分品牌。用户在第一歩选错品牌时，登录能成功、
车辆列表却是空的，前端只会看到「设置失败：账号下未发现车辆」，很难自行
定位到「品牌选错」。

本模块只做一件事：按「首选品牌 → 另一品牌」的顺序取车辆列表，返回真正
有车的品牌；两个品牌都取不到车（或请求异常）返回 ``None``。不依赖 HA，
便于直接单测。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from .const import BRAND_FORD, BRAND_LINCOLN

_LOGGER = logging.getLogger(__name__)

# 单品牌探测超时：登录刚完成，云端正常 <5s；超过 20s 视为该品牌不适用，
# 不要让它拖住整个配置流程。
PROBE_TIMEOUT = 20.0


def alternate_brand(brand: str) -> str:
    """返回另一品牌（福特 ⇄ 林肯），未知品牌按福特处理。"""
    return BRAND_LINCOLN if brand == BRAND_FORD else BRAND_FORD


async def brand_with_vehicles(
    api_factory: Callable[[str], Any],
    preferred: str,
    timeout: float = PROBE_TIMEOUT,
) -> tuple[str | None, bool]:
    """返回 ``(品牌, 是否有品牌成功响应)``。

    品牌为 ``None`` 时用第二个值区分两种情况：``True`` = 两个品牌都成功
    返回了空列表（账号确实没有车）；``False`` = 请求全部失败（网络/超时），
    调用方不应据此判定「没有车」，交给上层按临时故障处理。

    ``api_factory(brand)`` 需返回已注入令牌的 api 实例（提供 ``get_vehicles``
    协程）。异常（令牌不适用于该品牌、网关拒绝、超时）只记录 debug 并继续
    试下一个品牌——判定用途下失败等价于「该品牌没有车」，不应把配置流程
    整体带崩。
    """
    reachable = False
    for brand in (preferred, alternate_brand(preferred)):
        try:
            api = api_factory(brand)
            async with asyncio.timeout(timeout):
                vehicles = await api.get_vehicles()
        except Exception as err:  # noqa: BLE001 - 单品牌失败按「该品牌不适用」继续
            _LOGGER.debug("品牌 %s 车辆探测失败：%s", brand, err)
            continue
        reachable = True
        if vehicles:
            return brand, True
    return None, reachable



async def vehicles_with_brand_fallback(
    api_factory: Callable[[str], Any],
    preferred: str,
    timeout: float = PROBE_TIMEOUT,
) -> tuple[str, list[Any], Any]:
    """按「首选品牌 → 另一品牌」取车辆列表，返回真正有车的那一份。

    v6.7.6: ``brand_with_vehicles`` 只服务添加流程；重启加载（setup）此前只
    查 entry 里存的品牌，旧条目或「添加时自动切换但未落盘」的条目一旦品牌
    与实际归属不符，就会在每次重启时报「账号下未发现车辆」。这里把同一套
    兜底搬到 setup 使用。

    返回 ``(品牌, 车辆列表, 该品牌对应的 api 实例)``：
    - 首选品牌有车 → 原样返回首选品牌（不触发纠正）；
    - 首选为空、另一品牌有车 → 返回另一品牌与其 api，调用方据此更新 entry；
    - 两品牌都空 / 请求全失败 → ``(preferred, [], 首选 api)``，由调用方按
      「账号确实没有车」处理。
    单个品牌请求异常只记 debug 并继续下一个品牌，不把 setup 带崩。
    """
    last_api: Any = None
    for brand in (preferred, alternate_brand(preferred)):
        try:
            api = api_factory(brand)
        except Exception as err:  # noqa: BLE001 - 构造失败按该品牌不适用继续
            _LOGGER.debug("品牌 %s api 构造失败：%s", brand, err)
            continue
        if last_api is None:
            last_api = api
        try:
            async with asyncio.timeout(timeout):
                vehicles = await api.get_vehicles()
        except Exception as err:  # noqa: BLE001 - 单品牌失败按「该品牌不适用」继续
            _LOGGER.debug("品牌 %s 车辆查询失败：%s", brand, err)
            continue
        if vehicles:
            return brand, vehicles, api
    return preferred, [], last_api
