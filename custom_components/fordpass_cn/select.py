"""Select platform: 鸣笛寻车设置（持续时长 / 鸣笛类型）。

v3.0.8: 从福特派 App 设置页还原（2026-10-01 截图 + libapp.so 逆向）：
- 持续时长：5-20 秒（App 滑块，选项 5/10/15/20，默认 10）
- 鸣笛类型：雨落荷叶 / 急浪拍岸 / 汽笛长鸣 / 空谷回音 / 声光共舞
  （App 设置页 5 种，强度从弱到强；ChirpType = 索引 + 1，
   ChirpType=1 已对活网关实测有效，映射按 App 设置顺序推断）
选择即保存到 config entry options；鸣笛寻车开关开启时按此设置调用
POST /api/vehicles/v5/{vin}/honk（参数直达车机，等效上传设置）。
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import capability
from .const import (
    ANNOUNCE_ENUM_CN,
    CHIRP_TO_ANNOUNCE,
    CHIRP_TYPE_OPTIONS,
    CONF_CHIRP_TYPE,
    CONF_HONK_DURATION,
    CONF_REMOTE_TEMP,
    DEFAULT_CHIRP_TYPE,
    DEFAULT_HONK_DURATION,
    DEFAULT_REMOTE_TEMP,
    DOMAIN,
    HONK_DURATION_OPTIONS,
    PREF_TARGET_TEMP,
    REMOTE_CLIMATE_TEMP_OPTIONS,
)
from .coordinator import FordPassCoordinator

_LOGGER = logging.getLogger(__name__)


def _cloud_pref(coordinator: FordPassCoordinator) -> tuple[str | None, str | None]:
    """读云端鸣笛设置（v3.3.3）：返回 (类型中文名, 持续时长数字字符串) 或 None。

    数据源 coordinator.data["chirp_cloud"]（GET preference-list 回读）：
    新槽位 AnnouncementType（"0"-"4" 枚举数字）/ Duration（"5"-"20"），
    旧槽位 vehicleAnnouncementSoundType（枚举名）仅兜底。
    """
    pref = (coordinator.data or {}).get("chirp_cloud") or {}
    if not isinstance(pref, dict):
        return None, None
    sound = pref.get("AnnouncementType") or pref.get("vehicleAnnouncementSoundType")
    duration = pref.get("Duration") or pref.get("vehicleAnnouncementDuration")
    if sound is None and duration is None:
        return None, None
    type_cn = next(
        (cn for cn, en in CHIRP_TO_ANNOUNCE.items() if en == str(sound)),
        None,
    )
    if type_cn is None:
        type_cn = ANNOUNCE_ENUM_CN.get(str(sound), None)
    return type_cn, (str(duration) if duration is not None else None)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    # v3.1.6: 多 VIN——每辆车独立创建设置实体；
    # 设置内容为账户级（云端 UserPreference），多车共享同一份 settings
    payload = hass.data[DOMAIN][entry.entry_id]
    coordinators = payload.get("coordinators") or [payload["coordinator"]]
    settings = payload.setdefault(
        "honk_settings",
        {
            CONF_HONK_DURATION: int(
                entry.options.get(CONF_HONK_DURATION, DEFAULT_HONK_DURATION)
            ),
            CONF_CHIRP_TYPE: entry.options.get(
                CONF_CHIRP_TYPE, DEFAULT_CHIRP_TYPE
            ),
        },
    )
    entities: list[SelectEntity] = []
    for coordinator in coordinators:
        status = coordinator.data.get("vehiclestatus", {}) or {}
        # 鸣笛寻车设置与鸣笛开关同为远程控车能力（crccFlag 有效才创建）
        if capability.usable(status, [["crccFlag"]]):
            entities.append(
                FordPassHonkDurationSelect(hass, entry, coordinator, settings)
            )
            entities.append(
                FordPassChirpTypeSelect(hass, entry, coordinator, settings)
            )
        # v3.4.5: 远程空调目标温度（EV/插混 preconditioning 专属）——
        # vehicle-status preCondStatusDsply 有值才创建（锐际纯油无 → 不创建，0 unavailable）
        if capability.usable(status, [["preCondStatusDsply"]]):
            entities.append(
                FordPassRemoteTempSelect(hass, entry, coordinator)
            )
    async_add_entities(entities)


class _FordPassHonkSettingSelect(SelectEntity):
    """Base class: persist option to config entry options + memory settings.

    v3.3.3: 监听 coordinator 数据更新——「鸣笛设置云端状态」回读的
    AnnouncementType/Duration 变化时自动同步到本 select（App/其他设备
    改动后，HA 侧显示与持久化设置自动跟随云端）。
    """

    _attr_has_entity_name = False
    _attr_assumed_state = True

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: FordPassCoordinator,
        settings: dict[str, Any],
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self._settings = settings
        # v3.3.3: 云端同步监听（coordinator 每次数据更新后回调）
        self._cloud_unsub = self.coordinator.async_add_listener(self._on_cloud_update)

    async def async_will_remove_from_hass(self) -> None:
        """卸载时注销云端同步监听（防止 reload 后重复回调）。"""
        if self._cloud_unsub is not None:
            self._cloud_unsub()
            self._cloud_unsub = None

    def _on_cloud_update(self) -> None:
        """云端设置变化 → 同步本地 select（幂等：与当前值不同才更新）。"""
        type_cn, duration = _cloud_pref(self.coordinator)
        if duration is not None:
            try:
                dv = int(duration)
            except (TypeError, ValueError):
                dv = None
            if dv is not None and dv in HONK_DURATION_OPTIONS and isinstance(
                self, FordPassHonkDurationSelect
            ):
                cur = f"{dv} 秒"
                if self._attr_current_option != cur:
                    self._attr_current_option = cur
                    self._persist(CONF_HONK_DURATION, dv)
                    self.async_write_ha_state()
        if type_cn is not None and isinstance(self, FordPassChirpTypeSelect):
            if type_cn in CHIRP_TYPE_OPTIONS and self._attr_current_option != type_cn:
                self._attr_current_option = type_cn
                self._persist(CONF_CHIRP_TYPE, type_cn)
                self.async_write_ha_state()

    async def _save_cloud(self) -> None:
        """把当前鸣笛设置即时上传福特账户云端（v3.3.4）。

        类型/时长 select 任一改动即上传（与 App 保存操作逐字一致——
        UserPreferenceV2 preference-by-groups），成功后回读云端
        preference 更新 chirp_cloud（「鸣笛设置云端状态」传感器），
        实现 App / HA / 云端三方同步；「保存鸣笛设置」按钮已删除。
        """
        options: dict[str, Any] = {}
        entry_id = getattr(self.coordinator, "entry_id", None)
        if entry_id:
            entry = self.coordinator.hass.config_entries.async_get_entry(entry_id)
            options = entry.options if entry else {}
        duration = int(
            options.get(CONF_HONK_DURATION, DEFAULT_HONK_DURATION)
            or DEFAULT_HONK_DURATION
        )
        chirp_name = options.get(CONF_CHIRP_TYPE) or DEFAULT_CHIRP_TYPE
        if chirp_name in CHIRP_TYPE_OPTIONS:
            chirp_type = CHIRP_TYPE_OPTIONS.index(chirp_name) + 1
        else:
            chirp_type = 3
        try:
            await self.coordinator.api.save_honk_settings(
                self.coordinator.vin, duration=duration, chirp_type=chirp_type
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning(
                "FordPass 鸣笛设置云端保存失败（本地设置仍生效）: %s", exc
            )
            raise
        _LOGGER.info(
            "FordPass 鸣笛设置已即时上传云端（类型=%s 时长=%ss）",
            CHIRP_TYPE_OPTIONS[chirp_type - 1], duration,
        )
        # 回读云端 preference → 更新 chirp_cloud（「鸣笛设置云端状态」同步）
        try:
            pref = await self.coordinator.api.get_chirp_preference()
            if isinstance(pref, dict) and pref:
                data = dict(self.coordinator.data or {})
                data["chirp_cloud"] = pref
                self.coordinator.async_set_updated_data(data)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("FordPass 鸣笛设置云端回读失败: %s", exc)

    @property
    def available(self) -> bool:
        return True  # v3.1.2: 不随福特云刷新失败而不可用（保留最后已知状态）

    def _persist(self, key: str, value: Any) -> None:
        """Write to memory settings + config entry options (survives restart)."""
        self._settings[key] = value
        new_options = {**self.entry.options, key: value}
        self.hass.config_entries.async_update_entry(self.entry, options=new_options)
        _LOGGER.info(
            "FordPass 鸣笛设置 %s -> %s（已保存）", key, value
        )


class FordPassHonkDurationSelect(_FordPassHonkSettingSelect):
    """持续时长（秒）：5 / 10 / 15 / 20，默认 10。"""

    _attr_icon = "mdi:timer-outline"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: FordPassCoordinator,
        settings: dict[str, Any],
    ) -> None:
        super().__init__(hass, entry, coordinator, settings)
        self._attr_unique_id = f"{coordinator.vin}-honk_duration"
        self._attr_name = "鸣笛持续时长"
        self._attr_device_info = coordinator.device_info
        self._attr_options = [f"{v} 秒" for v in HONK_DURATION_OPTIONS]
        self._attr_current_option = (
            f"{settings.get(CONF_HONK_DURATION, DEFAULT_HONK_DURATION)} 秒"
        )

    async def async_select_option(self, option: str) -> None:
        # v3.2.4: 选项显示带「秒」单位（如 15 秒），持久化仍存纯数字
        value = int(str(option).replace("秒", "").strip())
        self._persist(CONF_HONK_DURATION, value)
        self._attr_current_option = f"{value} 秒"
        self.async_write_ha_state()
        # v3.3.4: 选择后即时上传福特账户云端（App/HA/云端三方同步）
        await self._save_cloud()


class FordPassChirpTypeSelect(_FordPassHonkSettingSelect):
    """鸣笛类型：雨落荷叶…声光共舞（ChirpType 1-5，默认汽笛长鸣）。"""

    _attr_icon = "mdi:bullhorn-variant"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: FordPassCoordinator,
        settings: dict[str, Any],
    ) -> None:
        super().__init__(hass, entry, coordinator, settings)
        self._attr_unique_id = f"{coordinator.vin}-chirp_type"
        self._attr_name = "鸣笛类型"
        self._attr_device_info = coordinator.device_info
        self._attr_options = list(CHIRP_TYPE_OPTIONS)
        self._attr_current_option = settings.get(
            CONF_CHIRP_TYPE, DEFAULT_CHIRP_TYPE
        )

    async def async_select_option(self, option: str) -> None:
        self._persist(CONF_CHIRP_TYPE, option)
        self._attr_current_option = option
        self.async_write_ha_state()
        # v3.3.4: 选择后即时上传福特账户云端（App/HA/云端三方同步）
        await self._save_cloud()

def _cloud_temp(coordinator: FordPassCoordinator) -> int | None:
    """读云端远程空调目标温度（v3.4.5，候选组 RemoteClimateSetting）。"""
    pref = (coordinator.data or {}).get("remote_climate_cloud") or {}
    if not isinstance(pref, dict):
        return None
    raw = pref.get(PREF_TARGET_TEMP)
    if raw is None:
        return None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


class FordPassRemoteTempSelect(_FordPassHonkSettingSelect):
    """远程空调目标温度（v3.4.5，EV/插混 preconditioning 专属）。

    创建条件=vehicle-status preCondStatusDsply 有值（锐际纯油无 → 不创建）。
    选项 16-30°C（候选范围，默认 24）；选择后即时上传福特账户云端
    （RemoteClimateSetting/TargetTemp 候选偏好通道，与鸣笛设置同构），
    回读云端 preference 更新 remote_climate_cloud，实现 App/HA/云端同步。
    通道为按 App 命名风格推断的候选值，待电马/插混账号 HAR 实测校准——
    保存失败仅记日志，实体恒可用。
    """

    _attr_icon = "mdi:thermometer"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: FordPassCoordinator,
    ) -> None:
        settings = {CONF_REMOTE_TEMP: int(
            entry.options.get(CONF_REMOTE_TEMP, DEFAULT_REMOTE_TEMP)
        )}
        super().__init__(hass, entry, coordinator, settings)
        self._attr_unique_id = f"{coordinator.vin}-remote_temp"
        self._attr_name = "远程空调目标温度"
        self._attr_device_info = coordinator.device_info
        self._attr_options = [f"{v} °C" for v in REMOTE_CLIMATE_TEMP_OPTIONS]
        self._attr_current_option = f"{settings.get(CONF_REMOTE_TEMP, DEFAULT_REMOTE_TEMP)} °C"

    def _on_cloud_update(self) -> None:
        """云端温度变化 → 同步本地 select（幂等）。"""
        temp = _cloud_temp(self.coordinator)
        if temp is not None and temp in REMOTE_CLIMATE_TEMP_OPTIONS:
            cur = f"{temp} °C"
            if self._attr_current_option != cur:
                self._attr_current_option = cur
                self._persist(CONF_REMOTE_TEMP, temp)
                self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        value = int(str(option).replace("°C", "").strip())
        self._persist(CONF_REMOTE_TEMP, value)
        self._attr_current_option = f"{value} °C"
        self.async_write_ha_state()
        # v3.4.5: 选择后即时上传福特账户云端（App/HA/云端三方同步）
        try:
            await self.coordinator.api.save_remote_climate_target_temp(value)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning(
                "FordPass 远程空调目标温度云端保存失败（本地设置仍生效，通道待 EV 车型实测）: %s", exc,
            )
            raise
        _LOGGER.info(
            "FordPass 远程空调目标温度已即时上传云端（%s°C，候选通道 RemoteClimateSetting/TargetTemp）", value,
        )
        # 回读云端 preference → 更新 remote_climate_cloud（云端同步）
        try:
            pref = await self.coordinator.api.get_remote_climate_preference()
            if isinstance(pref, dict) and pref:
                data = dict(self.coordinator.data or {})
                data["remote_climate_cloud"] = pref
                self.coordinator.async_set_updated_data(data)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("FordPass 远程空调目标温度云端回读失败: %s", exc)
