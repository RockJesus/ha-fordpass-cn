"""v7.2.4 回归：林肯之道开放用户名密码登录。

旧版（v5.6.0）在没有独立登录租户证据时，把林肯品牌的密码方式强制改成
短信验证码并从选项里隐藏。林肯之道 App 自身支持账号密码登录，且与福特派
共用同一登录页与账号体系，因此 v7.2.4 重新开放。四条契约：

1. ``async_step_login_mode`` 不再把林肯的 PASSWORD 改成 SMS；
2. 登录方式 schema 对两种品牌一致（同时提供验证码与密码）；
3. 三处翻译的密码描述都带 ``{brand}`` 占位，文案按品牌渲染；
4. README 不再宣称「林肯之道仅支持短信验证码」。
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "fordpass_cn"
ROOT = Path(__file__).resolve().parents[1]


def _method(tree: ast.Module, cls_name: str, name: str):
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == cls_name:
            for item in node.body:
                if (
                    isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and item.name == name
                ):
                    return item
    raise AssertionError(f"{cls_name} 缺少 {name}")


def test_login_mode_no_longer_forces_sms_for_lincoln():
    src = (SRC / "config_flow.py").read_text(encoding="utf-8")
    flow = _method(ast.parse(src), "FordPassConfigFlow", "async_step_login_mode")
    text = ast.get_source_segment(src, flow) or ""
    assert "mode = LOGIN_MODE_SMS" not in text, "林肯密码登录仍被强制改成短信"
    assert "LOGIN_MODE_PASSWORD" in text, "密码登录分支缺失"
    assert "step_id=\"password\"" in text


def test_login_mode_schema_has_both_methods():
    src = (SRC / "config_flow.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    schema_src = None
    labels_src = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if getattr(t, "id", "") == "STEP_LOGIN_MODE_SCHEMA":
                    schema_src = ast.get_source_segment(src, node)
                if getattr(t, "id", "") == "LOGIN_MODE_LABELS":
                    labels_src = ast.get_source_segment(src, node)
    assert schema_src, "STEP_LOGIN_MODE_SCHEMA 缺失"
    assert "LOGIN_MODE_LABELS" in schema_src, "登录方式 schema 未覆盖完整选项表"
    assert labels_src, "LOGIN_MODE_LABELS 缺失"
    assert "LOGIN_MODE_SMS" in labels_src and "LOGIN_MODE_PASSWORD" in labels_src


def test_password_description_uses_brand_placeholder():
    for rel in (
        "custom_components/fordpass_cn/strings.json",
        "custom_components/fordpass_cn/translations/zh-Hans.json",
        "custom_components/fordpass_cn/translations/en.json",
    ):
        data = json.loads((ROOT / rel).read_text(encoding="utf-8"))
        step = data["config"]["step"]
        assert "{brand}" in step["password"]["description"], f"{rel} 密码描述未品牌化"
        assert "{brand}" in step["b2c"]["description"], f"{rel} 授权码描述未品牌化"
        user_desc = step["user"]["description"]
        assert "仅支持短信" not in user_desc
        assert "SMS code login" not in user_desc


def test_readme_states_both_brands_support_password():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "林肯之道仅支持短信验证码" not in readme
    assert "两种品牌都支持" in readme
