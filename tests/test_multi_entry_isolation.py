"""v7.1.8: 同一 HA 内多条目（福特 + 林肯）互不干扰的行为约束。

四条规则：
1. 每条 entry 的 api 只读自己品牌的固定值快照，另一品牌后续加载不影响它；
2. 同目录刷新主包常量不得清空另一品牌的覆盖值，只有数据目录变更才失效；
3. 主包整体替换数据目录时必须保留附加包文件；
4. 同一份参数按两个品牌的快照签名，结果不同（互不串用）。
"""
from __future__ import annotations

import importlib
import io
import json
import logging
import sys
import types
import zipfile
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "fordpass_cn"


def _need(name: str, **attrs: object) -> types.ModuleType:
    mod = sys.modules.get(name)
    if mod is None:
        mod = types.ModuleType(name)
        mod.__path__ = []  # type: ignore[attr-defined]
        sys.modules[name] = mod
    for key, val in attrs.items():
        if not hasattr(mod, key):
            setattr(mod, key, val)
    return mod


def _bootstrap() -> None:
    _need(
        "aiohttp",
        ClientTimeout=type("ClientTimeout", (), {}),
        ClientSession=type("ClientSession", (), {}),
    )
    ha = _need("homeassistant")
    ha.__path__ = []  # type: ignore[attr-defined]
    _need("homeassistant.core", HomeAssistant=object)
    helpers = _need("homeassistant.helpers")
    helpers.__path__ = []  # type: ignore[attr-defined]
    _need(
        "homeassistant.helpers.aiohttp_client",
        async_get_clientsession=lambda *a, **k: None,
    )


def _load(name: str) -> types.ModuleType:
    pkg_name = "fplk"
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(SRC)]  # type: ignore[attr-defined]
        sys.modules[pkg_name] = pkg
    return importlib.import_module(f"{pkg_name}.{name}")


_bootstrap()
app_constants = _load("app_constants")
crypto_data = _load("crypto_data")
api_mod = _load("api")

BASE = {
    "APP_VERSION": "test-base",
    "SECRET_KEY": "value-ford-test",
    "PAYLOAD_KEY": "payload-test",
}
OVERLAY = {"LINCOLN_CLIENT_TYPE": "LW", "SECRET_KEY": "value-lincoln-test"}


@pytest.fixture(autouse=True)
def _reset_globals():
    app_constants._VALUES = {}
    app_constants._LINCOLN = {}
    app_constants._LINCOLN_DIR = None
    yield


@pytest.fixture()
def data_dir(tmp_path):
    (tmp_path / app_constants.APP_CONSTANTS_FILE).write_text(
        json.dumps(BASE), encoding="utf-8"
    )
    (tmp_path / app_constants.LINCOLN_CONSTANTS_FILE).write_text(
        json.dumps(OVERLAY), encoding="utf-8"
    )
    return tmp_path


def test_snapshots_are_isolated_per_brand(data_dir):
    assert app_constants.load_from(str(data_dir))
    assert app_constants.load_lincoln_from(str(data_dir))

    ford = app_constants.snapshot("ford")
    lincoln = app_constants.snapshot("lincoln")

    assert ford["SECRET_KEY"] == "value-ford-test"
    assert "LINCOLN_CLIENT_TYPE" not in ford
    assert lincoln["SECRET_KEY"] == "value-lincoln-test"
    assert lincoln["LINCOLN_CLIENT_TYPE"] == "LW"
    # 快照是副本，后续模块级变更不得回写
    app_constants._LINCOLN["SECRET_KEY"] = "changed"
    assert lincoln["SECRET_KEY"] == "value-lincoln-test"


def test_same_dir_reload_keeps_overlay(data_dir):
    app_constants.load_from(str(data_dir))
    app_constants.load_lincoln_from(str(data_dir))
    assert app_constants.lincoln_loaded()

    # 另一个条目（福特）在同目录再刷新一次主包，林肯覆盖必须还在
    assert app_constants.load_from(str(data_dir))
    assert app_constants.lincoln_loaded()
    assert app_constants.snapshot("lincoln")["SECRET_KEY"] == "value-lincoln-test"


def test_dir_change_drops_overlay(data_dir, tmp_path_factory):
    app_constants.load_from(str(data_dir))
    app_constants.load_lincoln_from(str(data_dir))
    assert app_constants.lincoln_loaded()

    other = tmp_path_factory.mktemp("other")
    (other / app_constants.APP_CONSTANTS_FILE).write_text(
        json.dumps(BASE), encoding="utf-8"
    )
    assert app_constants.load_from(str(other))
    assert not app_constants.lincoln_loaded()


def test_pack_replace_keeps_extra_files(data_dir):
    key_fp = data_dir / "whitebox_keys_lincoln.json"
    key_fp.write_bytes(b'{"k": "v"}')

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("app_constants.json", json.dumps(BASE))
        z.writestr("table_main.json", "{}")

    crypto_data._extract_atomic(buf.getvalue(), str(data_dir))

    assert (data_dir / "app_constants.json").exists()
    assert key_fp.exists() and key_fp.read_bytes() == b'{"k": "v"}'
    assert (data_dir / app_constants.LINCOLN_CONSTANTS_FILE).exists()


def test_signature_uses_own_snapshot(data_dir):
    app_constants.load_from(str(data_dir))
    app_constants.load_lincoln_from(str(data_dir))

    logger = logging.getLogger("isolation-test")
    ford_api = api_mod.FordPassApi(None, logger, brand="ford")
    lincoln_api = api_mod.FordPassApi(None, logger, brand="lincoln")

    params = {"timestamp": 1764000000000, "key": "value"}
    ford_sign = api_mod.compute_sign(
        params, ford_api._const.get("SECRET_KEY"), ford_api._const.get("PAYLOAD_KEY")
    )
    lincoln_sign = api_mod.compute_sign(
        params,
        lincoln_api._const.get("SECRET_KEY"),
        lincoln_api._const.get("PAYLOAD_KEY"),
    )
    assert ford_sign != lincoln_sign
    # 林肯条目自身也拿到了林肯侧覆盖值，福特条目不受影响
    assert lincoln_api._const["LINCOLN_CLIENT_TYPE"] == "LW"
    assert "LINCOLN_CLIENT_TYPE" not in ford_api._const
