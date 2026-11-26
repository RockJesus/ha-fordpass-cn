"""v7.1.2: 林肯侧密钥分流与可选附加包的行为约束。

四条不能破的规则：
1. 福特品牌**永远**使用主包密钥（`None`），即使林肯附加包已就位；
2. 林肯品牌只有在附加包文件存在时才换钥，缺失必须回落 `None`；
3. 附加包落盘只写白名单文件，**不得**删改主包任何文件；
4. 附加包缺失/写入失败不抛异常（不阻断 setup）。
"""
from __future__ import annotations

import importlib
import io
import json
import os
import sys
import types
import zipfile
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "fordpass_cn"


def _need(name: str, **attrs: object) -> types.ModuleType:
    """CI 精简环境（只装 pytest）按需补齐依赖模块。"""
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
    # api.py 顶层 import aiohttp；crypto_data 依赖 homeassistant 两处符号。
    _need("aiohttp", ClientTimeout=type("ClientTimeout", (), {}),
          ClientSession=type("ClientSession", (), {}))
    ha = _need("homeassistant")
    ha.__path__ = []  # type: ignore[attr-defined]
    _need("homeassistant.core", HomeAssistant=object)
    helpers = _need("homeassistant.helpers")
    helpers.__path__ = []  # type: ignore[attr-defined]
    _need("homeassistant.helpers.aiohttp_client",
          async_get_clientsession=lambda *a, **k: None)


def _load(name: str) -> types.ModuleType:
    """以私有包名加载，绕开 __init__.py 的 voluptuous 依赖。"""
    pkg_name = "fplk"
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(SRC)]  # type: ignore[attr-defined]
        sys.modules[pkg_name] = pkg
    return importlib.import_module(f"{pkg_name}.{name}")


_bootstrap()
api_mod = _load("api")
app_constants = _load("app_constants")
crypto_data = _load("crypto_data")
wbsk = _load("wbsk")
const_mod = _load("const")
BRAND_FORD, BRAND_LINCOLN = const_mod.BRAND_FORD, const_mod.BRAND_LINCOLN

MAIN_KEY = "whitebox_keys_for_prod.json"
LINCOLN_KEY = wbsk.LINCOLN_KEY_FILE
LINCOLN_CONST = app_constants.LINCOLN_CONSTANTS_FILE


@pytest.fixture()
def tmp_data(tmp_path):
    """把数据目录指向临时目录，测试结束恢复原目录。"""
    old = wbsk.data_dir()
    wbsk.set_data_dir(str(tmp_path))
    try:
        yield tmp_path
    finally:
        wbsk.set_data_dir(old)


def _touch(path: Path, payload: str = "{}") -> None:
    path.write_text(payload, encoding="utf-8")


# ----------------------------------------------------------------------
# 1) wbsk.lincoln_key_file：只做存在性判断
# ----------------------------------------------------------------------
def test_lincoln_key_file_none_when_absent(tmp_data):
    assert wbsk.lincoln_key_file() is None


def test_lincoln_key_file_returns_path_when_present(tmp_data):
    _touch(tmp_data / LINCOLN_KEY, '{"x_api": {"encrypt": "00", "decrypt": "00"}}')
    got = wbsk.lincoln_key_file()
    assert got is not None and got.endswith(LINCOLN_KEY)
    assert os.path.isfile(got)


# ----------------------------------------------------------------------
# 2) app_constants：林肯覆盖优先，缺失不影响主包值
# ----------------------------------------------------------------------
def test_lincoln_constants_override_wins(tmp_data):
    _touch(tmp_data / app_constants.APP_CONSTANTS_FILE,
           json.dumps({"LINCOLN_APP_VERSION": "6.0.0", "APP_VERSION": "6.14.0"}))
    _touch(tmp_data / LINCOLN_CONST, json.dumps({"LINCOLN_APP_VERSION": "6.7.0"}))
    assert app_constants.load_from(str(tmp_data)) is True
    assert app_constants.load_lincoln_from(str(tmp_data)) is True
    assert app_constants.get("LINCOLN_APP_VERSION") == "6.7.0"
    assert app_constants.get("APP_VERSION") == "6.14.0"
    assert app_constants.lincoln_loaded() is True


def test_lincoln_constants_absent_keeps_main_value(tmp_data):
    _touch(tmp_data / app_constants.APP_CONSTANTS_FILE,
           json.dumps({"LINCOLN_APP_VERSION": "6.0.0"}))
    assert app_constants.load_from(str(tmp_data)) is True
    assert app_constants.load_lincoln_from(str(tmp_data)) is False
    assert app_constants.get("LINCOLN_APP_VERSION") == "6.0.0"
    assert app_constants.lincoln_loaded() is False


# ----------------------------------------------------------------------
# 3) api._crypto_key_file：品牌路由
# ----------------------------------------------------------------------
def _api_with_brand(brand: str) -> api_mod.FordPassApi:
    obj = object.__new__(api_mod.FordPassApi)
    obj._brand = brand
    return obj


def test_ford_brand_never_uses_lincoln_key(tmp_data):
    _touch(tmp_data / LINCOLN_KEY, "{}")
    assert _api_with_brand(BRAND_FORD)._crypto_key_file() is None


def test_lincoln_brand_uses_lincoln_key_when_present(tmp_data):
    _touch(tmp_data / LINCOLN_KEY, "{}")
    got = _api_with_brand(BRAND_LINCOLN)._crypto_key_file()
    assert got is not None and got.endswith(LINCOLN_KEY)


def test_lincoln_brand_falls_back_when_pack_missing(tmp_data):
    assert _api_with_brand(BRAND_LINCOLN)._crypto_key_file() is None


# ----------------------------------------------------------------------
# 4) 附加包落盘：只写白名单，主包文件 byte-identical
# ----------------------------------------------------------------------
def _make_pack() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(LINCOLN_KEY, '{"x_api": {"encrypt": "00", "decrypt": "00"}}')
        z.writestr(LINCOLN_CONST, json.dumps({"LINCOLN_APP_VERSION": "6.7.0"}))
        # 非白名单文件：必须被跳过，不能写进数据目录
        z.writestr("app_constants.json", json.dumps({"APP_VERSION": "9.9.9"}))
        z.writestr("wbsk_tables_T_INIT.bin", b"EVIL")
    return buf.getvalue()


def test_extract_only_writes_whitelist(tmp_data):
    main_key = tmp_data / MAIN_KEY
    table = tmp_data / "wbsk_tables_T_INIT.bin"
    _touch(main_key, '{"x_api": {"encrypt": "keep", "decrypt": "keep"}}')
    table.write_bytes(b"ORIGINAL")

    written = crypto_data._extract_lincoln_into(_make_pack(), str(tmp_data))

    assert written == 2
    assert (tmp_data / LINCOLN_KEY).is_file()
    assert (tmp_data / LINCOLN_CONST).is_file()
    # 主包文件不得被改动
    assert main_key.read_text(encoding="utf-8").find("keep") != -1
    assert table.read_bytes() == b"ORIGINAL"
    # 非白名单的 app_constants.json 不得被写入
    assert not (tmp_data / app_constants.APP_CONSTANTS_FILE).exists()


def test_extract_rejects_pack_without_expected_files(tmp_data):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("readme.txt", "nothing")
    with pytest.raises(ValueError):
        crypto_data._extract_lincoln_into(buf.getvalue(), str(tmp_data))


def test_extract_blocks_path_traversal(tmp_data):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../escape.txt", "x")
        z.writestr(LINCOLN_KEY, "{}")
    with pytest.raises(ValueError):
        crypto_data._extract_lincoln_into(buf.getvalue(), str(tmp_data))
    assert not (tmp_data.parent / "escape.txt").exists()
