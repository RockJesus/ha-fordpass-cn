"""Diagnostics support for the FordPass China integration (v4.9.5)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

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
        data = getattr(entry, "runtime_data", None)  # v5.3.0
        if isinstance(data, dict) and (coord := data.get("coordinator")):
            payload["coordinator"] = {
                # v5.9.7: HA 早已移除 DataUpdateCoordinator.last_update，
                # 取它永远是 None。改用集成自己的 last_poll（成功刷新时写入），
                # 兼容性兜底再取 last_update_success_time / last_update。
                "last_poll": str(getattr(coord, "last_poll", None)),
                "last_success_time": str(
                    getattr(coord, "last_update_success_time", None)
                    or getattr(coord, "last_update", None)
                ),
                "last_update_success": bool(
                    getattr(coord, "last_update_success", None)
                ),
                "data_available": bool(getattr(coord, "data", None)),
            }
        # v5.9.6: 原实现用 hass.states.async_all() 匹配 "fordpass_cn." 前缀——
        # entity_id 的域是 sensor/lock/switch…，集成域不会出现，计数恒为 0。
        # 改为从实体注册表按 entry 取实际涉及的实体域。只列域、不给数量：
        # 诊断包通常贴在 issue 里公开，实体总数属于个人安装指纹。
        registry = er.async_get(hass)
        payload["entity_domains"] = sorted(
            {
                item.entity_id.split(".", 1)[0]
                for item in er.async_entries_for_config_entry(registry, entry.entry_id)
            }
        )
    except Exception as exc:  # noqa: BLE001 - 诊断接口绝不让异常打断
        payload["error"] = str(exc)
    return payload
