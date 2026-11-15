"""Climate platform: 远程空调（座舱温控）。

v6.7.3：对齐福特派 App「车内温度远程」卡片——把「目标温度设置」与
「远程启停」合并为 HA 标准 climate 实体，可直接用空调卡片、语音助手
与自动化（此前只有独立的「远程空调目标温度」select + 「远程启动」switch，
用户需要组合两个实体才能表达一次座舱预热/预冷）。

创建条件与 select 平台一致：vehicle-status 的 preCondStatusDsply 有值
（EV / 插混的 preconditioning 能力）。纯油 / 柴油车型该字段不存在 →
不创建，不留 unavailable 假实体（见 references/entity-vehicle-creation.md）。

控制通道沿用已实测的 send-command 网关（RemoteStart / RemoteStop），
温度偏好走福特账户云端 preference（RemoteClimateSetting/TargetTemp，
与 select 同一通道），不新增任何未验证的遥控载荷。
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import capability
from .const import (
    CMD_ENGINE_START,
    CMD_ENGINE_STOP,
    CONF_REMOTE_TEMP,
    DEFAULT_REMOTE_TEMP,
    PREF_TARGET_TEMP,
    REMOTE_CLIMATE_TEMP_OPTIONS,
)
from .coordinator import FordPassCoordinator
from .registry_sync import async_sync_entity_categories
from .sensor import FordPassRestoreMixin

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1

# preCondStatusDsply 取值中视为「座舱温控运行中」的枚举（与 sensor._precond_map 同源）
_PRECOND_ON = {"ON", "RUNNING", "PENDING"}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    payload = entry.runtime_data  # v5.3.0: runtime_data 替代 hass.data
    coordinators = payload.get("coordinators") or [payload["coordinator"]]
    entities: list[ClimateEntity] = []
    for coordinator in coordinators:
        status = (coordinator.data or {}).get("vehiclestatus", {}) or {}
        # v6.7.3: preCondStatusDsply 有值 = 车型具备远程座舱温控（EV/插混）
        if capability.usable(status, [["preCondStatusDsply"]]):
            entities.append(FordPassRemoteClimate(hass, entry, coordinator))
    async_add_entities(entities)
    async_sync_entity_categories(hass, "climate", entities)
    # 实体订阅协调器刷新广播（每次刷新成功即写状态，失败轮保留旧值）
    try:

        def _mk_refresh(ent: FordPassRemoteClimate):
            def _refresh() -> None:
                # hass 注入前（实体添加阶段）回调会被预热刷新触发
                if ent.hass is not None:
                    ent.async_write_ha_state()

            return _refresh

        for _ent in entities:
            _ent.async_on_remove(
                _ent.coordinator.async_add_listener(_mk_refresh(_ent))
            )
    except Exception:  # noqa: BLE001 - 监听注册失败不影响实体创建
        _LOGGER.debug("climate listener registration failed", exc_info=True)


class FordPassRemoteClimate(FordPassRestoreMixin, ClimateEntity):
    """远程空调：目标温度（16-30°C）+ 远程启停。"""

    # 纯推送实体：状态来自 coordinator 广播与云端回读，无本地可轮询状态
    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_translation_key = "remote_climate"
    _attr_icon = "mdi:air-conditioner"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.HEAT_COOL, HVACMode.OFF]
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_min_temp = min(REMOTE_CLIMATE_TEMP_OPTIONS)
    _attr_max_temp = max(REMOTE_CLIMATE_TEMP_OPTIONS)
    _attr_target_temperature_step = 1

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: FordPassCoordinator,
    ) -> None:
        self.coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{coordinator.uid_key}-remote_climate"
        self._attr_device_info = coordinator.device_info
        # 操作中乐观目标状态（None=无操作，读真实快照）——对齐 App「执行中」反馈
        self._pending: bool | None = None
        self._target = int(
            entry.options.get(CONF_REMOTE_TEMP, DEFAULT_REMOTE_TEMP)
        )

    # ---------------------------------------------------------- 状态
    @property
    def available(self) -> bool:
        # v6.7.3: 有最后已知数据即可用——刷新失败不置 unavailable
        return bool(self.coordinator.data)

    def _cloud_target(self) -> int | None:
        """读云端远程空调目标温度（RemoteClimateSetting/TargetTemp）。"""
        pref = (self.coordinator.data or {}).get("remote_climate_cloud") or {}
        if not isinstance(pref, dict):
            return None
        raw = pref.get(PREF_TARGET_TEMP)
        if raw is None:
            return None
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            return None
        return value if value in REMOTE_CLIMATE_TEMP_OPTIONS else None

    def _status_on(self) -> bool:
        status = (self.coordinator.data or {}).get("vehiclestatus", {}) or {}
        pre = str(capability.leaf(status, "preCondStatusDsply") or "").upper()
        if pre in _PRECOND_ON:
            return True
        rs = capability.leaf(status, "remoteStartStatus")
        return str(rs).strip().lower() in ("true", "1", "on", "yes")

    @property
    def hvac_mode(self) -> HVACMode:
        on = self._pending if self._pending is not None else self._status_on()
        return HVACMode.HEAT_COOL if on else HVACMode.OFF

    @property
    def current_temperature(self) -> None:
        # 云端不提供真实座舱温度，只有目标温度
        return None

    @property
    def target_temperature(self) -> int:
        return self._cloud_target() or self._target

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        status = (self.coordinator.data or {}).get("vehiclestatus", {}) or {}
        pre = capability.leaf(status, "preCondStatusDsply")
        attrs: dict[str, Any] = {"cloud_precond_status": pre}
        remain = capability.leaf(status, "remoteStartRemainingTime")
        if remain not in (None, "", "null"):
            attrs["remote_start_remaining"] = remain
        return {k: v for k, v in attrs.items() if v not in (None, "")}

    # ---------------------------------------------------------- 控制
    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self.async_turn_off()
        else:
            await self.async_turn_on()

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._pending = True
        self.async_write_ha_state()
        try:
            await self.coordinator.run_command(CMD_ENGINE_START)
        except Exception:
            self._pending = None
            self.async_write_ha_state()
            raise
        # 命令已受理；真实快照由 coordinator force_refresh 回写，
        # 下一轮广播即纠正 hvac_mode（无需额外确认任务）
        self._pending = None
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._pending = False
        self.async_write_ha_state()
        try:
            await self.coordinator.run_command(CMD_ENGINE_STOP)
        except Exception:
            self._pending = None
            self.async_write_ha_state()
            raise
        self._pending = None
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        value = max(min(int(round(float(temp))), self.max_temp), self.min_temp)
        self._target = value
        self.async_write_ha_state()
        try:
            await self.coordinator.api.save_remote_climate_target_temp(value)
        except Exception as exc:
            _LOGGER.warning(
                "FordPass 远程空调目标温度云端保存失败（本地设置仍生效）: %s", exc
            )
            raise
        _LOGGER.info("FordPass 远程空调目标温度已上传云端（%s°C）", value)
        # 回读云端 preference → 更新 remote_climate_cloud（App/HA/云端同步）
        try:
            pref = await self.coordinator.api.get_remote_climate_preference()
            if isinstance(pref, dict) and pref:
                data = dict(self.coordinator.data or {})
                data["remote_climate_cloud"] = pref
                self.coordinator.async_set_updated_data(data)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("FordPass 远程空调云端回读失败: %s", exc)
