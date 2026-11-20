"""v6.7.9 回归测试：声光寻车 panic 通道 URL 必须带 v5 基础域名。

回归保护点（旧 bug 会被本测试判失败）：
- ``panic_command`` / ``panic_cancel_command`` 此前把 ``PATH_V5_PANIC``
  相对路径直接传给 ``_v5_verb``，未拼 ``V5_BASE_URL``；aiohttp 收到相对
  URL 抛 ``InvalidUrlClientError``，请求根本没到网关，404 回落 VA 的逻辑
  也不会触发，HA 前端弹「无法执行动作 homeassistant/toggle」。
- ``_v5_verb`` 对漏拼 base 的相对路径做防御性补全，避免同类复发。

测试用 ``__new__`` 绕过真实构造，只验证 URL 构造与 HTTP 方法，不发真实请求。
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import sys
import types
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "fordpass_cn"


def _ensure_aiohttp() -> None:
    """CI 精简环境没有 aiohttp——api 模块顶层 `import aiohttp` 会收集失败。"""
    try:
        import aiohttp  # noqa: F401
        return
    except ModuleNotFoundError:
        pass
    stub = types.ModuleType("aiohttp")

    class _ClientTimeout:
        def __init__(self, *a, **kw):
            self.args = a
            self.kwargs = kw

    class _ClientSession:
        def __init__(self, *a, **kw):
            self.args = a
            self.kwargs = kw

    stub.ClientTimeout = _ClientTimeout
    stub.ClientSession = _ClientSession
    sys.modules["aiohttp"] = stub


_ensure_aiohttp()


def _load_api():
    """以包上下文加载真实 api 模块（const/wbsk 均为纯 Python，无 HA 依赖）。"""
    if "fpanic" not in sys.modules:
        pkg = types.ModuleType("fpanic")
        pkg.__path__ = [str(SRC)]  # type: ignore[attr-defined]
        sys.modules["fpanic"] = pkg
    return importlib.import_module("fpanic.api")


api_mod = _load_api()
FordPassApi = api_mod.FordPassApi
V5_BASE_URL = api_mod.V5_BASE_URL


class _Resp:
    status = 200

    async def text(self):
        return "{}"


class _Ctx:
    def __init__(self, resp: _Resp) -> None:
        self._resp = resp

    async def __aenter__(self) -> _Resp:
        return self._resp

    async def __aexit__(self, *a: object) -> bool:
        return False


class _FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def request(self, method: str, url: str, headers: dict | None = None) -> _Ctx:
        self.calls.append((method, url))
        return _Ctx(_Resp())


def _make_api() -> FordPassApi:
    api = FordPassApi.__new__(FordPassApi)
    api._session = _FakeSession()  # type: ignore[attr-defined]
    api._log = logging.getLogger("test_panic_url")
    api._app_version = None
    api._client_type = None
    api._access_token = None
    api._refresh_token = None
    api.on_token_refresh = None
    api._retrying = False
    return api


def _paniced_url(duration: int) -> str:
    return V5_BASE_URL + f"/api/vehicles/v5/VIN123/panic/{duration}"


def test_panic_command_url_is_full_http() -> None:
    api = _make_api()
    asyncio.run(api.panic_command("VIN123", 5))
    method, url = api._session.calls[0]  # type: ignore[attr-defined]
    assert method == "PUT"
    assert url == _paniced_url(5), f"panic 开通道 URL 漏拼 base: {url!r}"
    assert url.startswith(("http://", "https://")), "URL 必须是完整 http 地址"


def test_panic_cancel_command_url_is_full_http() -> None:
    api = _make_api()
    asyncio.run(api.panic_cancel_command("VIN123", 5))
    method, url = api._session.calls[0]  # type: ignore[attr-defined]
    assert method == "DELETE"
    assert url == _paniced_url(5), f"panic 取消通道 URL 漏拼 base: {url!r}"


def test_v5_verb_prefers_full_url() -> None:
    """调用方已拼完整 URL 时不得二次拼接。"""
    api = _make_api()
    full = "https://example.test/api/vehicles/v5/VIN123/panic/5"
    asyncio.run(api._v5_verb("PUT", full, "panic"))
    _, url = api._session.calls[0]  # type: ignore[attr-defined]
    assert url == full


def test_v5_verb_fills_base_for_relative_path() -> None:
    """漏拼 base 的相对路径由封装兜底补全（防御性）。"""
    api = _make_api()
    asyncio.run(api._v5_verb("PUT", "/relative/x", "t"))
    _, url = api._session.calls[0]  # type: ignore[attr-defined]
    assert url == V5_BASE_URL + "/relative/x"
