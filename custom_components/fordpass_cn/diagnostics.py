"""Diagnostics support for the FordPass China integration (v4.9.5)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

# 脱敏字段：任何含这些 key（或子串命中）的配置/选项值在诊断输出中被替换为 ***
TO_REDACT = {
    "access_token",
    "refresh_token",
    "token",
    "vin",
    "phone",
    "username",
    "password",
    "latitude",
    "longitude",
    "xjw",
    "encrypted_vin",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry (官方 diagnostics 通道).

    输出：entry 基础信息 + 脱敏后的 entry.data/options + 协调器运行状态
    （最后更新时间/是否成功），便于用户上报问题时定位，不含任何凭据。
    """
    payload: dict[str, Any] = {
        "entry_id": entry.entry_id,
        "version": entry.version,
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "options": async_redact_data(dict(entry.options), TO_REDACT),
    }
    try:
        data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if isinstance(data, dict) and (coord := data.get("coordinator")):
            payload["coordinator"] = {
                "last_update": str(getattr(coord, "last_update", None)),
                "last_update_success": bool(
                    getattr(coord, "last_update_success", None)
                ),
                "data_available": bool(getattr(coord, "data", None)),
            }
        payload["entity_count"] = len(
            [
                e
                for e in hass.states.async_all()
                if e.entity_id.startswith(f"{DOMAIN}.")
            ]
        )
    except Exception as exc:  # noqa: BLE001 - 诊断接口绝不让异常打断
        payload["error"] = str(exc)
    return payload
