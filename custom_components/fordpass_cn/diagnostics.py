"""Diagnostics support for the FordPass China integration (v4.9.5)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from . import capability

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
    # v6.1.0: 加密数据来源。表不再随仓库分发后，"为什么实体不可用"
    # 十有八九是数据目录落空或下载校验失败，这条能一句话定位。
    # 只报目录类型与该目录是否齐备，不报绝对路径（用户家目录结构不外泄）。
    try:
        from . import wbsk

        active = wbsk.data_dir()
        bundled = wbsk.bundled_data_dir()
        if active == bundled:
            source = "bundled"
        elif ".storage" in active:
            source = "downloaded_cache"
        else:
            source = "user_supplied"
        payload["crypto_data"] = {"source": source, "ready": wbsk.data_ready()}
    except Exception as exc:  # noqa: BLE001 - 诊断接口绝不让异常打断
        payload["crypto_data"] = {"error": str(exc)}
    try:
        data = getattr(entry, "runtime_data", None)  # v5.3.0
        if isinstance(data, dict) and (coord := data.get("coordinator")):
            # v6.4.9: 取不到时间时不再输出字符串 "None"——按类型判断，
            # 非 datetime 一律给空值，避免"没有记录"与"值是 None"被混为一谈。
            success_time = None
            for attr in (
                "last_success_time",  # 集成自己的只读属性（首选）
                "last_update_success_time",
                "last_update",
            ):
                cand = getattr(coord, attr, None)
                if isinstance(cand, datetime):
                    success_time = cand
                    break
            payload["coordinator"] = {
                # v5.9.7: HA 早已移除 DataUpdateCoordinator.last_update，
                # 取它永远是 None。改用集成自己的 last_poll（成功刷新时写入），
                # 兼容性兜底再取 last_update_success_time / last_update。
                "last_poll": capability.iso_or_none(getattr(coord, "last_poll", None)),
                "last_success_time": capability.iso_or_none(success_time),
                "last_update_success": bool(
                    getattr(coord, "last_update_success", None)
                ),
                "data_available": bool(getattr(coord, "data", None)),
            }
            # v6.5.0: 主机时钟偏移自检结果——签名请求的 timestamp 由主机
            # 时间生成，偏差超 120s 就会被云端按 t-2m~t+8m 窗口拒掉
            # （表现为 token refresh 返回 100400）。这里给出实测偏差与结论，
            # 排查时不必再手工比对 Date 头。
            clock_off = getattr(
                getattr(coord, "api", None), "clock_offset_seconds", None
            )
            if isinstance(clock_off, (int, float)):
                off_val: float | None = round(float(clock_off), 1)
            else:
                off_val = None
            clock_ok = None if off_val is None else abs(off_val) <= 120.0
            payload["coordinator"]["clock_offset_seconds"] = off_val
            payload["coordinator"]["clock_synced"] = clock_ok
            payload["coordinator"]["clock_hint"] = (
                None
                if clock_ok is not False
                else "主机时间与云端偏差超过 120 秒，请在 HA 设置→系统 同步时间"
            )
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
