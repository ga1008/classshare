"""LessonDoc 平台内置引擎资产读取(static/lessondoc/<ver>/).

生成学习文档包时把这些文件复制进包内 assets/(离线可用、版本锁定);
「刷新包内引擎」亦从此处取最新副本。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from . import spec

# classroom_app/services/lessondoc/assets.py → 仓库根/static/lessondoc/2.0
_REPO_ROOT = Path(__file__).resolve().parents[3]
ASSETS_DIR = _REPO_ROOT / "static" / spec.ASSET_STATIC_SUBDIR


class LessonDocAssetError(RuntimeError):
    """平台引擎资产缺失或不可读."""


def asset_path(name: str) -> Path:
    if name not in spec.ASSET_FILES:
        raise LessonDocAssetError(f"未知引擎资产: {name}")
    return ASSETS_DIR / name


def read_asset_text(name: str) -> str:
    path = asset_path(name)
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LessonDocAssetError(f"引擎资产不可读: {path}") from exc


def load_all_assets() -> dict[str, str]:
    """返回 {文件名: 文本内容};任一缺失即报错(建包不允许半套引擎)."""
    return {name: read_asset_text(name) for name in spec.ASSET_FILES}


_fingerprint_cache: str | None = None


def assets_fingerprint() -> str:
    """全部资产的联合 sha256(用于判断包内引擎是否过期)。

    进程内缓存:引擎文件只随部署变化,列表页逐请求重算 6 个文件的 sha 纯属
    浪费。开发环境改引擎后需重启进程才刷新——与静态资源的常规行为一致。
    """
    global _fingerprint_cache
    if _fingerprint_cache is not None:
        return _fingerprint_cache
    digest = hashlib.sha256()
    for name in spec.ASSET_FILES:
        digest.update(name.encode("utf-8"))
        digest.update(read_asset_text(name).encode("utf-8"))
    _fingerprint_cache = digest.hexdigest()
    return _fingerprint_cache


def reset_fingerprint_cache_for_tests() -> None:
    global _fingerprint_cache
    _fingerprint_cache = None
