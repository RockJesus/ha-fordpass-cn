# -*- coding: utf-8 -*-
"""实体类目（entity_category）同步工具。

Home Assistant 只在实体首次注册时采用集成声明的 entity_category；条目已存在时
不会自动覆盖（避免盖掉用户在界面上手动改的值）。因此升级后新增的分类必须显式
写一次注册表。本模块提供「只补不覆盖」的同步函数，各平台 setup 末尾调用。
"""
from __future__ import annotations

import logging

from homeassistant.helpers import entity_registry as er

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def async_sync_entity_categories(hass, domain: str, entities) -> int:
    """把代码声明的 entity_category 同步到已存在的注册表条目（只补不覆盖）。"""
    registry = er.async_get(hass)
    updated = 0
    for entity in entities:
        unique_id = getattr(entity, "unique_id", None)
        category = getattr(entity, "entity_category", None)
        if not unique_id or not category:
            continue
        entity_id = registry.async_get_entity_id(domain, DOMAIN, unique_id)
        if not entity_id:
            continue
        reg_entry = registry.async_get(entity_id)
        if reg_entry is None or reg_entry.entity_category is not None:
            continue
        registry.async_update_entity(entity_id, entity_category=category)
        updated += 1
    if updated:
        _LOGGER.info("已同步 %s 个实体的类目（entity_category）", updated)
    return updated
