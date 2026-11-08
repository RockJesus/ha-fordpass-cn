"""v6.4.2 回归测试：has_update 覆写 + 翻译键完整性（不依赖 HA 运行时）。"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
import types
from pathlib import Path
from typing import Any

import pytest

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "fordpass_cn"
ROOT = Path(__file__).resolve().parents[1]


class _FakeUpdateEntity:
    """替代 homeassistant.components.update.UpdateEntity（框架默认语义）。"""

    _attr_has_entity_name = None
    _attr_title = None

    async def async_added_to_hass(self):
        return None

    def async_write_ha_state(self):
        return None

    @property
    def installed_version(self):
        return None

    @property
    def latest_version(self):
        return None

    @property
    def in_progress(self):
        return False

    @property
    def has_update(self):
        return self.latest_version != self.installed_version


class _FakeCoordinator:
    uid_key = "test"

    def __init__(self, data: dict | None = None) -> None:
        self.data = data
        self.device_info: dict[str, Any] = {}

    def async_add_listener(self, cb):
        return lambda: None


def _install_fake_modules() -> None:
    if "fpcn_t.update" in sys.modules:
        return

    def mk(name: str, **attrs: Any) -> types.ModuleType:
        module = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        sys.modules[name] = module
        return module

    for name in ("homeassistant", "homeassistant.components", "homeassistant.helpers"):
        mk(name)
    mk(
        "homeassistant.components.update",
        UpdateEntity=_FakeUpdateEntity,
        UpdateDeviceClass=types.SimpleNamespace(FIRMWARE="firmware"),
        UpdateEntityFeature=types.SimpleNamespace(PROGRESS=4),
    )
    mk("homeassistant.config_entries", ConfigEntry=object)
    mk("homeassistant.const", EntityCategory=types.SimpleNamespace(DIAGNOSTIC="diagnostic"))
    mk("homeassistant.core", HomeAssistant=object, callback=lambda f: f)
    mk("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)

    pkg = mk("fpcn_t")
    pkg.__path__ = [str(SRC)]  # type: ignore[attr-defined]
    mk("fpcn_t.coordinator", FordPassCoordinator=_FakeCoordinator)

    spec = importlib.util.spec_from_file_location("fpcn_t.update", SRC / "update.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["fpcn_t.update"] = module
    spec.loader.exec_module(module)


def load_update():
    _install_fake_modules()
    return sys.modules["fpcn_t.update"]


def ota(**kw) -> dict:
    return {"ota_setting": kw}


def make(ota_payload: dict, in_progress: Any = None):
    mod = load_update()
    payload: dict[str, Any] = dict(ota_payload)
    if in_progress is not None:
        payload["vehiclestatus"] = {"firmwareUpgInProgress": {"value": in_progress}}
    entity = mod.FordPassFirmwareUpdate(_FakeCoordinator(payload))
    entity.hass = object()
    return entity


# ---------- has_update ----------


def test_has_update_new_version():
    assert make(ota(asIsVersion="1.2.0", toBeVersion="1.3.0")).has_update is True


def test_has_update_same_version_different_format():
    """云端同一版本的不同书写（v 前缀 / 前导零 / 大小写）不得误判为有更新。"""
    assert make(ota(asIsVersion="1.02.0", toBeVersion="V1.2.0")).has_update is False


def test_has_update_in_progress_suppresses():
    entity = make(ota(asIsVersion="1.2.0", toBeVersion="1.3.0"), in_progress=True)
    assert entity.in_progress is True
    assert entity.has_update is False


def test_has_update_latest_wording_wins():
    """云端明确「已是最新」时，即使版本串不一致也不提示更新。"""
    entity = make(
        ota(asIsVersion="1.2.0", toBeVersion="1.3.0", statusDescription="系统已是最新")
    )
    assert entity.has_update is False


def test_has_update_missing_version_falls_back():
    """版本号缺失时回落框架默认（此处 latest 回退到 installed，均为 None）。"""
    entity = make({})
    assert entity.installed_version is None
    assert entity.latest_version is None
    assert entity.has_update is False


def test_norm_version():
    mod = load_update()
    assert mod._norm_version(" V01.002.0 ") == "1.2.0"
    assert mod._norm_version("ABC") == "abc"
    assert mod._norm_version("") is None


# ---------- 翻译完整性 ----------

STATIC_KEY_RE = re.compile(r'_attr_translation_key = "([a-z0-9_]+)"')
DYNAMIC = {
    "image": ["parking_remote_image", "traffic_remote_image"],
    "button": ["parking_image", "traffic_image"],
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _keys_in_code(platform: str) -> set[str]:
    text = (SRC / f"{platform}.py").read_text(encoding="utf-8")
    return set(STATIC_KEY_RE.findall(text)) | set(DYNAMIC.get(platform, []))


@pytest.mark.parametrize("platform", ["sensor", "button", "image", "switch",
                                     "select", "event", "device_tracker", "update"])
def test_translation_key_exists_in_all_files(platform):
    keys = _keys_in_code(platform)
    assert keys, f"{platform}.py 没有翻译键"
    strings = _load(SRC / "strings.json")
    en = _load(SRC / "translations" / "en.json")
    zh = _load(SRC / "translations" / "zh-Hans.json")
    for pkg in (strings, en, zh):
        section = pkg.get("entity", {}).get(platform, {})
        missing = [k for k in keys if not section.get(k, {}).get("name")]
        assert not missing, f"{platform} 缺翻译条目: {missing}"


def test_chinese_names_match_live_baseline():
    """中文名必须与现网 registry 的 original_name 逐字一致（实体名零漂移）。"""
    # 基线随仓库入库（tests/fixtures），CI 与本地读同一份
    baseline = json.loads(
        (ROOT / "tests" / "fixtures" / "entity_baseline.json").read_text(encoding="utf-8")
    )
    zh = _load(SRC / "translations" / "zh-Hans.json")["entity"]
    mismatched = []
    for row in baseline:
        got = zh.get(row["platform"], {}).get(row["key"], {}).get("name")
        if got != row["zh"]:
            mismatched.append((row["platform"], row["key"], row["zh"], got))
    assert not mismatched, f"实体名漂移: {mismatched}"


def test_no_leftover_attr_name_in_migrated_files():
    """迁移过的平台文件不应再有硬编码中文 _attr_name（白名单分支除外）。"""
    offenders = []
    # v6.4.4: lock 平台已退役（文件删除），不再纳入检查
    for platform in ("device_tracker", "event", "update"):
        text = (SRC / f"{platform}.py").read_text(encoding="utf-8")
        if "_attr_name = " in text:
            offenders.append(platform)
    assert not offenders, f"仍存在 _attr_name: {offenders}"


UID_RE = re.compile(r'_attr_unique_id = f"[^"]*?-([a-z0-9_-]+)"')
KEY_RE = re.compile(r'_attr_translation_key = "([a-z0-9_]+)"')


@pytest.mark.parametrize("platform", ["sensor", "button", "image", "switch",
                                     "select", "event", "device_tracker", "update"])
def test_translation_key_matches_unique_id_suffix(platform):
    """翻译键必须与 unique_id 后缀一致（否则实体名会取到别的条目）。"""
    text = (SRC / f"{platform}.py").read_text(encoding="utf-8")
    blocks = re.split(r"\nclass ", text)
    bad = []
    for block in blocks:
        uid = UID_RE.search(block)
        key = KEY_RE.search(block)
        if uid and key and uid.group(1).replace("-", "_") != key.group(1):
            bad.append((uid.group(1), key.group(1)))
    assert not bad, f"{platform} 键与 unique_id 后缀不一致: {bad}"


SERVICE_SENSOR_RE = re.compile(
    r'super\(\)\.__init__\(coordinator, "(\w+)", "[^"]*", "[^"]*", uid="(\w+)"\)'
)


def test_service_sensor_translation_key_uses_uid():
    """v6.4.6 回归：共用同一数据键的传感器必须按 uid 取翻译键。

    prognostic 端点下有 机油寿命 / 慢漏气车胎 / 预测性诊断 三个传感器，都从
    coordinator.data["prognostic"] 读数据。此前一律按数据键取翻译键，三个实体
    在界面上全部显示「机油寿命」——uid 明确时必须以 uid 为翻译键。
    """
    text = (SRC / "sensor.py").read_text(encoding="utf-8")
    base = text.split("class _ServiceInfoSensor")[1].split("\nclass ")[0]
    assert "tl_key = uid or key" in base, "_ServiceInfoSensor 未按 uid 优先取翻译键"
    assert "if tl_key in _TRANSLATED_KEYS" in base, "翻译键判断未使用 uid 派生键"

    uids = [m.group(2) for m in SERVICE_SENSOR_RE.finditer(text)
            if m.group(1) == "prognostic"]
    assert len(uids) == 3, f"prognostic 家族应有 3 个传感器，实际 {uids}"
    assert len(set(uids)) == 3, f"prognostic 家族 uid 重复: {uids}"
    zh = _load(SRC / "translations" / "zh-Hans.json")["entity"]["sensor"]
    for uid in uids:
        assert zh.get(uid, {}).get("name"), f"缺翻译条目: {uid}"
