# -*- coding: utf-8 -*-
"""实体类目（entity_category）同步工具。

Home Assistant 只在实体首次注册时采用集成声明的 entity_category；条目已存在时
不会自动跟随代码变化（新增分类写不进去，删掉的分类也退不回控制区）。因此分类的
增与减都必须显式写一次注册表。本模块负责补齐声明，并对显式列出的实体做回滚，
各平台 setup 末尾调用。
"""
from __future__ import annotations

import logging

from homeassistant.helpers import entity_registry as er

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def async_sync_entity_categories(hass, domain: str, entities, clear_unique_ids=None) -> int:
    """补齐注册表缺失的分类；clear_unique_ids 中的实体按代码「无分类」退回主控区。

    不做无条件双向同步：注册表里有一部分分类是早期代码声明留下的历史值，当前代码
    已不再声明（如部分车辆信息实体）。无条件回滚会把它们一起清空，因此需要退回
    主控区的实体必须逐个列出，其余条目不碰。
    """
    registry = er.async_get(hass)
    updated = 0
    for entity in entities:
        unique_id = getattr(entity, "unique_id", None)
        if not unique_id:
            continue
        entity_id = registry.async_get_entity_id(domain, DOMAIN, unique_id)
        if not entity_id:
            continue
        reg_entry = registry.async_get(entity_id)
        if reg_entry is None:
            continue
        declared = getattr(entity, "entity_category", None)
        if declared is not None:
            if reg_entry.entity_category == declared:
                continue
            registry.async_update_entity(entity_id, entity_category=declared)
            updated += 1
        elif clear_unique_ids and unique_id in clear_unique_ids and reg_entry.entity_category is not None:
            registry.async_update_entity(entity_id, entity_category=None)
            updated += 1
    if updated:
        _LOGGER.info("已同步 %s 个实体的类目（entity_category）", updated)
    return updated
