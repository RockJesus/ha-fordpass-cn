"""image 平台「令牌只播种不轮换」测试（不依赖 Home Assistant 运行时）。

回归保护点（对应 core homeassistant/components/image/__init__.py）：
- ``ImageEntity.__init__`` 依赖 ``async_update_token()`` 创建首个 token——
  覆盖成纯空操作会让 ``state_attributes`` 取空 deque 抛 IndexError（实体
  创建即坏），因此实现必须「首次播种、之后不轮换」；
- core 每 5 分钟无条件调用 ``async_update_token()`` +
  ``async_write_ha_state()``——不轮换后 attributes 恒等，状态机走
  ``state_reported`` 短路，不再产生 state_changed / recorder 行；
- 图片内容「只拉一次」：本地 .storage 命中即返回，不发起网络请求。
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import tempfile
import types
from collections import deque
from pathlib import Path
from typing import Any

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "fordpass_cn"

TOKEN_HEX = "a" * 64  # core: hex(getrandbits(256))[2:] == 64 位十六进制


class _FakeImageEntity:
    """替代 homeassistant.components.image.ImageEntity 的最小实现。"""

    _attr_should_poll = None
    _attr_content_type: str | None = None

    def __init__(self, hass: Any = None) -> None:
        self.hass = hass
        self.access_tokens: deque[str] = deque(maxlen=2)
        self.async_update_token()  # core __init__ 同款调用

    @property
    def state_attributes(self) -> dict[str, str]:
        return {"access_token": self.access_tokens[-1]}

    @property
    def name(self) -> str | None:
        """与 core Entity.name 同语义：返回 _attr_name（has_entity_name 下即实体名）。"""
        return getattr(self, "_attr_name", None)

    def async_update_token(self) -> None:
        """core 原版：每次追加随机 token（子类应覆盖为只播种）。"""
        self.access_tokens.append(TOKEN_HEX)

    def async_on_remove(self, _cb) -> None:  # noqa: ANN001
        """测试里不需要管理移除回调。"""

    def async_write_ha_state(self) -> None:
        """测试里不需要真正写状态。"""


class _FakeConfig:
    def path(self, *parts: str) -> str:
        return os.path.join(tempfile.gettempdir(), "fpcn_test", *parts)


class _FakeHass:
    config = _FakeConfig()

    async def async_add_executor_job(self, func, *args):  # noqa: ANN001
        return func(*args)


class _FakeCoordinator:
    """image.py 只用到这些成员。"""

    uid_key = "VIN123"
    vin = "VIN123"
    car_id = None
    device_info: dict[str, Any] = {}
    vehicle_model = "测试车型"
    extra_probes: dict[str, Any] = {}

    def __init__(self, data: dict | None = None) -> None:
        self.data = data or {}

    def async_add_listener(self, callback):  # noqa: ANN001
        return lambda: None


def _install_fake_modules() -> None:
    if "fpcn.image" in sys.modules:
        return

    def mk(name: str, **attrs: Any) -> types.ModuleType:
        module = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        sys.modules[name] = module
        return module

    for name in ("homeassistant", "homeassistant.components", "homeassistant.helpers"):
        mk(name)
    # aiohttp 桩：本机未安装；image.py / api.py 只在导入时引用这些名字
    mk(
        "aiohttp",
        ClientTimeout=lambda **kw: None,
        ClientSession=object,
        ClientError=Exception,
        ClientConnectionError=Exception,
        ClientResponse=object,
        ContentTypeError=Exception,
        TCPConnector=object,
        BasicAuth=object,
    )
    mk("homeassistant.components.image", ImageEntity=_FakeImageEntity)
    mk("homeassistant.config_entries", ConfigEntry=object)
    mk("homeassistant.core", HomeAssistant=object, callback=lambda func: func)
    mk(
        "homeassistant.helpers.aiohttp_client",
        async_get_clientsession=lambda hass: None,
    )
    mk("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)

    pkg = mk("fpcn")
    pkg.__path__ = [str(SRC)]  # type: ignore[attr-defined]
    mk("fpcn.coordinator", FordPassCoordinator=_FakeCoordinator)
    # fpcn.api / fpcn.wbsk 由 event 测试的假模块 harness 或真实源码提供：
    # image.py 只用 extract_remote_image_url，api.py 无 homeassistant 依赖，
    # 直接按真实源码加载（wbsk 同理，api 顶部才 from .wbsk import）。
    for sub in ("api", "wbsk", "const"):
        if f"fpcn.{sub}" in sys.modules:
            continue
        spec = importlib.util.spec_from_file_location(f"fpcn.{sub}", SRC / f"{sub}.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"fpcn.{sub}"] = module
        spec.loader.exec_module(module)

    spec = importlib.util.spec_from_file_location("fpcn.image", SRC / "image.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["fpcn.image"] = module
    spec.loader.exec_module(module)


def load_image():
    _install_fake_modules()
    return sys.modules["fpcn.image"]


def make_vehicle_image():
    mod = load_image()
    return mod.FordPassVehicleImage(_FakeHass(), _FakeCoordinator(), "side")


def test_token_seeded_once_on_init() -> None:
    """__init__ 必须产出首个 token（空 deque 会让 state_attributes 崩）。"""
    ent = make_vehicle_image()
    assert len(ent.access_tokens) == 1
    assert ent.access_tokens[-1]
    # state_attributes 不抛 IndexError，且与 deque 末位一致
    assert ent.state_attributes["access_token"] == ent.access_tokens[-1]


def test_token_not_rotated_on_core_interval_call() -> None:
    """core 每 5 分钟调用 async_update_token 后 token 不得变化。"""
    ent = make_vehicle_image()
    before = tuple(ent.access_tokens)
    for _ in range(5):  # 模拟多次轮换调用
        ent.async_update_token()
    assert tuple(ent.access_tokens) == before


def test_base_class_still_rotates() -> None:
    """假基类自身仍会轮换——证明子类覆盖确实生效，而非 harness 失效。"""
    ent = _FakeImageEntity(_FakeHass())
    ent.async_update_token()
    assert len(ent.access_tokens) == 2  # maxlen=2, 基类每次追加


def test_three_image_classes_covered() -> None:
    """三个 image 类都必须覆盖 async_update_token。"""
    mod = load_image()
    coord = _FakeCoordinator({"remote_image": {"kind": "parking"}})
    remote = mod.FordPassRemoteImageEntity(_FakeHass(), coord, "parking")
    three_d = mod.FordPass3dModelImage(_FakeHass(), coord)
    for ent in (remote, three_d):
        before = tuple(ent.access_tokens)
        ent.async_update_token()
        assert tuple(ent.access_tokens) == before
        assert ent.state_attributes["access_token"] == ent.access_tokens[-1]


def test_async_image_reads_local_file_without_network() -> None:
    """本地已有持久化图片时 async_image 直接返回、不发网络请求。"""
    mod = load_image()
    coord = _FakeCoordinator()
    coord.vehicle_image_url = None  # 无远程 URL 兜底
    ent = mod.FordPassVehicleImage(_FakeHass(), coord, "side")
    os.makedirs(os.path.dirname(ent._persist_path), exist_ok=True)
    with open(ent._persist_path, "wb") as fh:
        fh.write(b"PNGDATA")
    data = asyncio.run(ent.async_image())
    assert data == b"PNGDATA"
    assert ent._image_bytes == b"PNGDATA"


def test_state_shows_entity_nickname() -> None:
    """v6.0.4: 图片实体状态显示本实体昵称，不再所有实体都返回车型名。"""
    mod = load_image()
    side = mod.FordPassVehicleImage(_FakeHass(), _FakeCoordinator(), "side")
    overlook = mod.FordPassVehicleImage(_FakeHass(), _FakeCoordinator(), "overlook")
    assert side.state == "侧视车型图"
    assert overlook.state == "俯视车型图"
    assert side.state != overlook.state  # 修复前两者都返回「测试车型」


def test_state_prefers_registry_rename() -> None:
    """用户在注册表里重命名后，状态跟随即时生效。"""
    mod = load_image()
    ent = mod.FordPassVehicleImage(_FakeHass(), _FakeCoordinator(), "side")
    ent.registry_entry = types.SimpleNamespace(name="我的爱车侧视图")
    assert ent.state == "我的爱车侧视图"


def test_remote_image_state_and_attribute() -> None:
    """远程影像状态=昵称；影像有无由 image_status 属性承载（信息不丢）。"""
    mod = load_image()
    coord = _FakeCoordinator({"remote_image": {"kind": "parking"}})
    ent = mod.FordPassRemoteImageEntity(_FakeHass(), coord, "parking")
    assert ent.state == "停车影像图"
    assert ent.extra_state_attributes["image_status"] == "暂无影像"
    ent._last_url = "https://example.invalid/p.jpg"
    assert ent.state == "停车影像图"
    assert ent.extra_state_attributes["image_status"] == "最新影像"
