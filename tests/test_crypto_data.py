"""crypto_data 加密表定位与下载测试（v6.1.0）。

用最小假模块替换 ``homeassistant.*``，直接加载 ``crypto_data.py``，因此
不需要装 HA、也不会真的联网。

回归保护点（每一条都对应一个真实故障模式）：
- 本地已有数据时**不得发起任何网络请求**（重载集成不应每次都打服务器）；
- 四级优先级顺序正确（环境变量 > 配置目录 > 缓存 > 下载）；
- 远程包 SHA-256 不符**必须拒绝落盘**（否则镜像被投毒后全量用户算错密文，
  表现为请求全被网关拒绝且极难定位）；
- 压缩包含 ``..`` 路径必须拒绝（防写到 .storage 之外）；
- 解压成功后 ``data_ready`` 为真，且已注入 wbsk 的数据目录。
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import io
import json
import sys
import types
import zipfile
from pathlib import Path
from typing import Any

import pytest

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "fordpass_cn"


class _FakeConfig:
    def __init__(self, root: Path) -> None:
        self._root = root

    def path(self, *parts: str) -> str:
        return str(self._root.joinpath(*parts))


class _FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    async def read(self) -> bytes:
        return self._body

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False


class _FakeSession:
    """按 URL 返回预置响应；记录被请求过的 URL 以断言"不联网"。"""

    def __init__(self, mapping: dict[str, tuple[int, bytes]]) -> None:
        self._mapping = mapping
        self.calls: list[str] = []

    def get(self, url: str) -> _FakeResponse:
        self.calls.append(url)
        status, body = self._mapping.get(url, (404, b""))
        return _FakeResponse(status, body)


class _FakeHass:
    def __init__(self, root: Path, session: _FakeSession | None = None) -> None:
        self.config = _FakeConfig(root)
        self._session = session or _FakeSession({})

    async def async_add_executor_job(self, func: Any, *args: Any) -> Any:
        return func(*args)


def _install_fake_modules() -> None:
    if "fpcn.crypto_data" in sys.modules:
        return

    def mk(name: str, **attrs: Any) -> types.ModuleType:
        module = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        sys.modules[name] = module
        return module

    for name in ("homeassistant", "homeassistant.helpers"):
        mk(name)
    mk("homeassistant.core", HomeAssistant=object)
    mk(
        "homeassistant.helpers.aiohttp_client",
        async_get_clientsession=lambda hass: hass._session,
    )

    pkg = mk("fpcn")
    pkg.__path__ = [str(SRC)]  # type: ignore[attr-defined]
    for sub in ("const", "wbsk", "crypto_data"):
        if f"fpcn.{sub}" in sys.modules:
            continue
        spec = importlib.util.spec_from_file_location(f"fpcn.{sub}", SRC / f"{sub}.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"fpcn.{sub}"] = module
        spec.loader.exec_module(module)
        setattr(pkg, sub, module)


def load_module() -> types.ModuleType:
    _install_fake_modules()
    return sys.modules["fpcn.crypto_data"]


def wbsk_module() -> types.ModuleType:
    _install_fake_modules()
    return sys.modules["fpcn.wbsk"]


def _write_data_dir(path: Path) -> Path:
    """写出一个"齐备"的数据目录（内容无关，data_ready 只看文件名）。"""
    path.mkdir(parents=True, exist_ok=True)
    for name in (*wbsk_module().TABLE_FILES, wbsk_module().KEY_FILE):
        (path / name).write_bytes(b"\x00" * 16)
    return path


def _zip_payload(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buf.getvalue()


def _full_payload() -> bytes:
    entries = {name: b"\x00" * 16 for name in wbsk_module().TABLE_FILES}
    entries[wbsk_module().KEY_FILE] = b"{}"
    return _zip_payload(entries)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """隔离每个用例：屏蔽随包目录、清空环境变量、结束后恢复数据目录。

    必须屏蔽 ``_DEFAULT_DATA_DIR``——真实仓库里 ``data/`` 存在，不屏蔽的话
    每一个用例都会先命中"随包分发"，自备/缓存/下载分支永远测不到。
    """
    mod = wbsk_module()
    original = mod.data_dir()
    monkeypatch.setattr(mod, "_DEFAULT_DATA_DIR", str(tmp_path / "no_bundled_data"))
    monkeypatch.delenv("FORDPASS_DATA_DIR", raising=False)
    monkeypatch.delenv("FORDPASS_DATA_URL", raising=False)
    yield
    mod.set_data_dir(original)


def test_local_data_skips_network(tmp_path, monkeypatch) -> None:
    """配置目录已有数据 → 不发起任何请求（重载集成不该每次都打服务器）。"""
    monkeypatch.delenv("FORDPASS_DATA_DIR", raising=False)
    mod = load_module()
    _write_data_dir(tmp_path / "fordpass_cn_data")
    session = _FakeSession({})
    hass = _FakeHass(tmp_path, session)

    ok, where = asyncio.run(mod.async_ensure_crypto_data(hass))

    assert ok is True
    assert "配置目录自备" in where
    assert session.calls == []
    assert wbsk_module().data_ready()


def test_bundled_dir_preferred_when_present(tmp_path, monkeypatch) -> None:
    """随包目录存在时直接命中（过渡版本：升级后零网络请求）。"""
    mod = load_module()
    bundled = _write_data_dir(tmp_path / "bundled")
    monkeypatch.setattr(wbsk_module(), "_DEFAULT_DATA_DIR", str(bundled))
    _write_data_dir(tmp_path / "fordpass_cn_data")  # 自备目录也有，但随包优先
    session = _FakeSession({})

    ok, where = asyncio.run(mod.async_ensure_crypto_data(_FakeHass(tmp_path, session)))

    assert ok is True
    assert "随包" in where
    assert session.calls == []


def test_priority_env_over_config(tmp_path, monkeypatch) -> None:
    """环境变量优先级最高，命中后不再看配置目录。"""
    env_dir = _write_data_dir(tmp_path / "env_dir")
    _write_data_dir(tmp_path / "fordpass_cn_data")
    monkeypatch.setenv("FORDPASS_DATA_DIR", str(env_dir))
    mod = load_module()

    ok, where = asyncio.run(mod.async_ensure_crypto_data(_FakeHass(tmp_path)))

    assert ok is True
    assert "环境变量" in where
    assert wbsk_module().data_dir() == str(env_dir)


def test_download_when_nothing_local(tmp_path, monkeypatch) -> None:
    """本地全空 → 下载、校验、解压、注入，全链路成功。"""
    monkeypatch.delenv("FORDPASS_DATA_DIR", raising=False)
    monkeypatch.delenv("FORDPASS_DATA_URL", raising=False)
    mod = load_module()
    payload = _full_payload()
    url = mod._urls()[0]
    monkeypatch.setattr(mod, "CRYPTO_DATA_SHA256", hashlib.sha256(payload).hexdigest())
    session = _FakeSession({url: (200, payload)})

    ok, where = asyncio.run(mod.async_ensure_crypto_data(_FakeHass(tmp_path, session)))

    assert ok is True, where
    assert "已下载" in where
    dest = Path(mod.cache_dir(_FakeHass(tmp_path)))
    assert dest.is_dir()
    assert wbsk_module().data_dir() == str(dest)
    assert wbsk_module().data_ready()


def test_bad_digest_is_rejected(tmp_path, monkeypatch) -> None:
    """SHA-256 不符必须拒绝落盘——镜像投毒的唯一防线。"""
    monkeypatch.delenv("FORDPASS_DATA_DIR", raising=False)
    monkeypatch.delenv("FORDPASS_DATA_URL", raising=False)
    mod = load_module()
    payload = _full_payload()
    url = mod._urls()[0]
    monkeypatch.setattr(mod, "CRYPTO_DATA_SHA256", "0" * 64)
    session = _FakeSession({url: (200, payload)})

    ok, where = asyncio.run(mod.async_ensure_crypto_data(_FakeHass(tmp_path, session)))

    assert ok is False
    assert "校验" in where or "下载失败" in where
    assert not Path(mod.cache_dir(_FakeHass(tmp_path))).exists()
    assert session.calls  # 确实尝试过下载


def test_zip_slip_rejected(tmp_path) -> None:
    """含 ``..`` 的成员名必须拒绝，不得写到 .storage 之外。"""
    mod = load_module()
    payload = _zip_payload({"../../evil.bin": b"x"})
    dest = tmp_path / "cache"

    with pytest.raises(ValueError):
        mod._extract_atomic(payload, str(dest))

    assert not dest.exists()
    assert not (tmp_path / "evil.bin").exists()


def test_partial_payload_reports_incomplete(tmp_path, monkeypatch) -> None:
    """包内缺文件时报"不完整"，不得把半套表注入使用。"""
    monkeypatch.delenv("FORDPASS_DATA_DIR", raising=False)
    monkeypatch.delenv("FORDPASS_DATA_URL", raising=False)
    mod = load_module()
    payload = _zip_payload({"wbsk_tables_T_INIT.bin": b"\x00" * 16})
    url = mod._urls()[0]
    monkeypatch.setattr(mod, "CRYPTO_DATA_SHA256", hashlib.sha256(payload).hexdigest())
    session = _FakeSession({url: (200, payload)})

    ok, where = asyncio.run(mod.async_ensure_crypto_data(_FakeHass(tmp_path, session)))

    assert ok is False
    assert "不完整" in where


def test_url_env_override(monkeypatch) -> None:
    """内网环境用单个自建源覆盖，内置列表不再参与。"""
    mod = load_module()
    monkeypatch.setenv("FORDPASS_DATA_URL", "http://192.168.1.10/ford.zip")
    assert mod._urls() == ("http://192.168.1.10/ford.zip",)


def test_missing_help_mentions_three_paths(tmp_path) -> None:
    """指引必须覆盖三种补齐方式，否则用户只能干瞪眼。"""
    mod = load_module()
    text = mod.missing_help(_FakeHass(tmp_path))
    assert "fordpass_cn_data" in text
    assert "FORDPASS_DATA_URL" in text
    assert "重新加载" in text


# ---------------------------------------------------------------------------
# 缓存清单（v6.1.2）：换包后旧缓存必须作废，否则换包永远触达不到存量用户
# ---------------------------------------------------------------------------
def _setup_download(tmp_path, monkeypatch, payload: bytes):
    """配置"本地全空 + 可下载"的环境，返回 (mod, session)。"""
    monkeypatch.delenv("FORDPASS_DATA_DIR", raising=False)
    monkeypatch.delenv("FORDPASS_DATA_URL", raising=False)
    mod = load_module()
    url = mod._urls()[0]
    monkeypatch.setattr(mod, "CRYPTO_DATA_SHA256", hashlib.sha256(payload).hexdigest())
    session = _FakeSession({url: (200, payload)})
    return mod, session


def _seed_cache(hass_root: Path, sha: str | None) -> Path:
    """在 .storage 下构造"齐备"的下载缓存目录（manifest 可选）。"""
    dest = Path(hass_root / ".storage" / "fordpass_cn" / "data")
    _write_data_dir(dest)
    if sha is not None:
        (dest / load_module()._MANIFEST_NAME).write_text(
            json.dumps({"sha256": sha, "files": 12}), encoding="utf-8"
        )
    return dest


def test_cache_valid_manifest_hits(tmp_path, monkeypatch) -> None:
    """缓存 manifest 与声明一致 → 命中，不联网。"""
    mod, session = _setup_download(tmp_path, monkeypatch, _full_payload())
    sha = mod.CRYPTO_DATA_SHA256
    _seed_cache(tmp_path, sha)

    ok, where = asyncio.run(
        mod.async_ensure_crypto_data(_FakeHass(tmp_path, session))
    )

    assert ok is True
    assert "本地下载缓存" in where
    assert session.calls == []


def test_cache_stale_manifest_redownloads(tmp_path, monkeypatch) -> None:
    """缓存 manifest 与声明不符（换过包）→ 作废并重新下载。"""
    payload = _full_payload()
    mod, session = _setup_download(tmp_path, monkeypatch, payload)
    _seed_cache(tmp_path, "f" * 64)

    ok, where = asyncio.run(
        mod.async_ensure_crypto_data(_FakeHass(tmp_path, session))
    )

    assert ok is True, where
    assert "已下载" in where
    assert session.calls


def test_cache_without_manifest_redownloads(tmp_path, monkeypatch) -> None:
    """v6.1.1 遗留缓存（无 manifest）→ 视为过期，重新下载一次。"""
    payload = _full_payload()
    mod, session = _setup_download(tmp_path, monkeypatch, payload)
    _seed_cache(tmp_path, None)

    ok, where = asyncio.run(
        mod.async_ensure_crypto_data(_FakeHass(tmp_path, session))
    )

    assert ok is True, where
    assert "已下载" in where
    assert session.calls


def test_download_writes_manifest(tmp_path, monkeypatch) -> None:
    """下载落盘必须带 manifest，且记录整包 SHA-256。"""
    payload = _full_payload()
    mod, session = _setup_download(tmp_path, monkeypatch, payload)

    ok, where = asyncio.run(
        mod.async_ensure_crypto_data(_FakeHass(tmp_path, session))
    )

    assert ok is True, where
    manifest = (
        Path(mod.cache_dir(_FakeHass(tmp_path))) / mod._MANIFEST_NAME
    ).read_text(encoding="utf-8")
    meta = json.loads(manifest)
    assert meta["sha256"] == mod.CRYPTO_DATA_SHA256
    assert meta["files"] == len(wbsk_module().TABLE_FILES) + 1


def test_fallback_to_second_url(tmp_path, monkeypatch) -> None:
    """第一源失败 → 自动尝试下一源（多源兜底回归，用临时 URL 对验证机制）。"""
    payload = _full_payload()
    monkeypatch.delenv("FORDPASS_DATA_DIR", raising=False)
    monkeypatch.delenv("FORDPASS_DATA_URL", raising=False)
    mod = load_module()
    assert len(mod.CRYPTO_DATA_URLS) >= 1, "至少要有一个默认下载源"
    monkeypatch.setattr(mod, "CRYPTO_DATA_SHA256", hashlib.sha256(payload).hexdigest())
    # 用临时构造的双源验证按序尝试机制（默认 const 可能只有单源）
    primary, fallback = "https://primary.invalid/pack.zip", mod.CRYPTO_DATA_URLS[0]
    monkeypatch.setattr(mod, "CRYPTO_DATA_URLS", (primary, fallback))
    # _FakeSession 未配置的 URL 一律返回 404，即第一源必然失败
    session = _FakeSession({fallback: (200, payload)})

    ok, where = asyncio.run(
        mod.async_ensure_crypto_data(_FakeHass(tmp_path, session))
    )

    assert ok is True, where
    assert primary in session.calls, "应先尝试过第一源"
    assert fallback in session.calls, "第一源失败后应尝试兜底源"


def test_default_urls_privacy() -> None:
    """默认下载源不得包含维护者自建服务器地址（隐私红线，v6.1.5）。"""
    mod = load_module()
    for url in mod.CRYPTO_DATA_URLS:
        assert "rockjesus" not in url, f"默认源泄露自建域名: {url}"
