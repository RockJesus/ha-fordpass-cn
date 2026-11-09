# -*- coding: utf-8 -*-
"""实体类目（entity_category）同步工具。

Home Assistant 只在实体首次注册时采用集成声明的 entity_category；条目已存在时
不会自动跟随代码变化（新增分类写不进去，删掉的分类也退不回控制区）。因此分类的
增与减都必须显式写一次注册表。本模块负责让注册表与代码声明保持一致，各平台
setup 末尾调用。
"""
from __future__ import annotations

import logging

from homeassistant.helpers import entity_registry as er

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def async_sync_entity_categories(hass, domain: str, entities) -> int:
    """让注册表的 entity_category 与代码声明一致：缺的补上，多/错的回滚。

    声明为 None 表示「归主控区」，此时会把注册表里残留的 config/diagnostic 清掉。
    """
    registry = er.async_get(hass)
    updated = 0
    for entity in entities:
        unique_id = getattr(entity, "unique_id", None)
        if not unique_id:
            continue
        declared = getattr(entity, "entity_category", None)
        entity_id = registry.async_get_entity_id(domain, DOMAIN, unique_id)
        if not entity_id:
            continue
        reg_entry = registry.async_get(entity_id)
        if reg_entry is None or reg_entry.entity_category == declared:
            continue
        registry.async_update_entity(entity_id, entity_category=declared)
        updated += 1
    if updated:
        _LOGGER.info("已同步 %s 个实体的类目（entity_category）", updated)
    return updated
