"""加密表定位与按需获取（v6.1.0）。

11 张白盒 AES 表 + 每个场景的密钥流是实现云端请求签名所必需的第三方
逆向材料，自 v6.1.0 起不再随代码仓库分发。本模块按四级优先级定位：

1. ``FORDPASS_DATA_DIR`` 环境变量 —— 用户自备，完全离线可用
2. ``<config>/fordpass_cn_data/`` —— 用户自备，放在 HA 配置目录
3. ``<config>/.storage/fordpass_cn/data/`` —— 本模块下载后的缓存
4. 远程下载 —— 首次 setup 自动完成，SHA-256 校验通过才落盘

四级全部落空时**不阻断 setup**：集成照常加载，仅依赖签名的实体不可用，
并以持久通知给出可操作的补齐方式（下载 / 手动放置 / 指向私有镜像）。
这样用户看到的是"差一步"而不是"集成加载失败"。
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import shutil
import zipfile

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import wbsk
from .const import (
    CRYPTO_DATA_SHA256,
    CRYPTO_DATA_URLS,
    FORD_DATA_DIR_ENV,
    FORD_DATA_SUBDIR,
    FORD_DATA_URL_ENV,
)

_LOGGER = logging.getLogger(__name__)

_MAX_BYTES = 8 * 1024 * 1024  # 整包上限：防异常响应把内存撑爆
_HTTP_TIMEOUT = 60  # 首启一次下载，给足超时（396KB 在慢网下也可完成）
_CACHE_SUBDIR = ("fordpass_cn", "data")
# 下载缓存目录的清单文件名（记录整包 SHA-256，用于换包后判定缓存过期）。
# 下划线开头：不在 data_ready 检查的表/密钥名单里，不影响齐备性判定。
_MANIFEST_NAME = "_pack.json"
_CACHE_LABEL = "本地下载缓存"


def cache_dir(hass: HomeAssistant) -> str:
    """自动下载的落盘位置（HA 的 .storage 下，随备份一起走）。"""
    return hass.config.path(".storage", *_CACHE_SUBDIR)


def candidates(hass: HomeAssistant) -> list[tuple[str, str]]:
    """按优先级返回 (目录, 来源说明)。远程下载是最后兜底，不在此列。

    顺序刻意把「随包分发」放在自备之后、其余之前：过渡版本（数据仍随包
    发布）与本地开发命中它即可，**不发起任何网络请求**；等 data/ 从仓库
    移除后这一项自然落空，集成才继续往下找。
    """
    out: list[tuple[str, str]] = []
    env = os.environ.get(FORD_DATA_DIR_ENV)
    if env:
        out.append((env, "环境变量自备"))
    out.append((wbsk.bundled_data_dir(), "随包分发"))
    out.append((hass.config.path(FORD_DATA_SUBDIR), "配置目录自备"))
    out.append((cache_dir(hass), _CACHE_LABEL))
    return out


def _manifest_matches(dest: str) -> bool:
    """缓存目录是否仍对应集成当前声明的数据包。

    下载落盘时会把整包 SHA-256 写进 ``_pack.json``。换包 = 更新 const 的
    ``CRYPTO_DATA_SHA256`` + 发版，此后：manifest 缺失（v6.1.1 时代的
    缓存）或 sha 不符的缓存一律视为**过期**，重新下载一次。没有这道检查，
    缓存永远优先命中，换包将无法触达存量用户——他们会一直用旧表直到
    手动删除缓存。
    """
    try:
        with open(os.path.join(dest, _MANIFEST_NAME), encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, ValueError):
        return False
    return isinstance(meta, dict) and meta.get("sha256") == CRYPTO_DATA_SHA256


def _urls() -> tuple[str, ...]:
    """下载源：环境变量单源优先（内网/私有镜像），否则用内置列表。"""
    override = os.environ.get(FORD_DATA_URL_ENV)
    if override:
        return (override,)
    return CRYPTO_DATA_URLS


def _pick_local(hass: HomeAssistant) -> tuple[str, str] | None:
    """遍历本地候选，返回第一个可用来源（阻塞调用，走 executor）。

    ``data_ready`` 的 isfile 探测与缓存清单的 open 都是磁盘 IO，不能在
    事件循环里跑（HA 会报 blocking call）。整个探测统一放 executor。
    """
    for path, label in candidates(hass):
        if not wbsk.data_ready(path):
            continue
        if label == _CACHE_LABEL and not _manifest_matches(path):
            # 缓存与当前声明的数据包不符（换了包 / v6.1.1 遗留无清单），
            # 作废并落到下面的重新下载——不能让旧表永远挡住新包。
            _LOGGER.info("FordPass 加密表缓存已过期（与当前数据包不符），重新下载")
            continue
        return path, label
    return None


async def async_ensure_crypto_data(hass: HomeAssistant) -> tuple[bool, str]:
    """确保加密表可用，返回 ``(是否可用, 说明)``。

    命中本地任一级即返回，不发起网络请求；只有全部落空才下载——避免每次
    重载集成都打一次服务器。
    """
    picked = await hass.async_add_executor_job(_pick_local, hass)
    if picked is not None:
        path, label = picked
        wbsk.set_data_dir(path)
        _LOGGER.debug("FordPass 加密表已就绪（%s）：%s", label, path)
        return True, f"{label}（{path}）"

    blob = await _async_fetch(hass)
    if blob is None:
        return False, "本地未找到数据，且远程下载失败或校验不通过"

    dest = cache_dir(hass)
    try:
        await hass.async_add_executor_job(_extract_atomic, blob, dest)
    except Exception as err:  # noqa: BLE001 - 解压失败不阻断 setup
        _LOGGER.warning("FordPass 加密表解压失败：%s", err)
        return False, f"数据包解压失败：{err}"

    if not wbsk.data_ready(dest):
        return False, "数据包内容不完整（缺少表或密钥文件）"

    wbsk.set_data_dir(dest)
    _LOGGER.info("FordPass 加密表已下载至 %s", dest)
    return True, f"已下载（{dest}）"


async def _async_fetch(hass: HomeAssistant) -> bytes | None:
    """按顺序尝试各下载源，返回第一个通过 SHA-256 校验的整包。"""
    session = async_get_clientsession(hass)
    for url in _urls():
        try:
            async with asyncio.timeout(_HTTP_TIMEOUT):
                async with session.get(url) as resp:
                    if resp.status != 200:
                        _LOGGER.debug(
                            "FordPass 加密表下载 %s 返回 HTTP %s", url, resp.status
                        )
                        continue
                    blob = await resp.read()
        except (TimeoutError, OSError) as err:
            _LOGGER.debug("FordPass 加密表下载失败 %s：%s", url, err)
            continue
        except Exception as err:  # noqa: BLE001 - 单源失败继续下一源
            _LOGGER.debug("FordPass 加密表下载异常 %s：%s", url, err)
            continue

        if len(blob) > _MAX_BYTES:
            _LOGGER.warning(
                "FordPass 加密表响应过大（%d 字节），已忽略 %s", len(blob), url
            )
            continue
        digest = hashlib.sha256(blob).hexdigest()
        if CRYPTO_DATA_SHA256 and digest != CRYPTO_DATA_SHA256:
            # 校验失败通常意味着镜像内容被替换或上传了错误文件，
            # 明确告警而不是静默重试——这类问题必须让人看到。
            _LOGGER.warning(
                "FordPass 加密表校验失败 %s（期望 %s，实得 %s）",
                url,
                CRYPTO_DATA_SHA256[:16],
                digest[:16],
            )
            continue
        return blob
    return None


def _extract_atomic(blob: bytes, dest: str) -> None:
    """解压到临时目录，成功后整体替换 dest（阻塞调用，走 executor）。

    先落临时目录再 rename，避免解压中途失败留下半套表——半套表会被
    ``data_ready`` 判为不可用，但残留文件会让下次排查产生误导。
    落盘时一并写 ``_pack.json``（整包 SHA-256），作为缓存过期判据。
    """
    parent = os.path.dirname(dest)
    os.makedirs(parent, exist_ok=True)
    tmp = f"{dest}.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    try:
        written = 0
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            for info in archive.infolist():
                name = info.filename
                if name.endswith("/"):
                    continue
                # 防 zip slip：拒绝绝对路径与任何 .. 段
                if name.startswith(("/", "\\")) or ".." in name.split("/"):
                    raise ValueError(f"压缩包含不安全路径：{name}")
                target = os.path.join(tmp, os.path.basename(name))
                with archive.open(info) as src, open(target, "wb") as fh:
                    shutil.copyfileobj(src, fh)
                written += 1
        manifest = {
            "sha256": hashlib.sha256(blob).hexdigest(),
            "files": written,
        }
        with open(os.path.join(tmp, _MANIFEST_NAME), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    shutil.rmtree(dest, ignore_errors=True)
    os.replace(tmp, dest)


def missing_help(hass: HomeAssistant) -> str:
    """数据缺失时给用户的三选一指引（用于持久通知正文）。"""
    urls = _urls()
    primary = urls[0] if urls else "（未配置下载源）"
    return (
        "福特派互联（非官方）需要一份加密数据包才能与云端通信，"
        "当前未找到。任选一种方式补齐即可——集成每几分钟自动重试，"
        "**补齐后无需任何操作**，恢复后本通知自动消失：\n\n"
        f"**1. 自动下载**（把文件放到可访问的位置，或直接让它联网）\n"
        f"{primary}\n\n"
        "**2. 手动放置**（离线环境推荐）\n"
        f"把压缩包解压到 `{hass.config.path(FORD_DATA_SUBDIR)}`，"
        "确保目录内有 11 个 `wbsk_tables_*.bin` 和 "
        "`whitebox_keys_for_prod.json`。\n\n"
        "**3. 指向私有镜像**\n"
        f"设置环境变量 `{FORD_DATA_URL_ENV}` 为完整下载地址，"
        "或 `FORDPASS_DATA_DIR` 直接指向已解压的目录。\n\n"
        "想跳过等待立即生效：**设置 → 设备与服务 → 福特派互联 → 重新加载**。"
    )
