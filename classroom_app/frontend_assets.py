import json
from functools import lru_cache
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from .config import STATIC_DIR


ASSET_MANIFEST_PATH = STATIC_DIR / "vendor" / "manifest.json"


def _normalize_static_path(value: str) -> str:
    raw_value = str(value or "").replace("\\", "/").lstrip("/")
    if raw_value.startswith("static/"):
        raw_value = raw_value[len("static/"):]

    path = PurePosixPath(raw_value)
    parts: list[str] = []
    for part in path.parts:
        if part in ("", "."):
            continue
        if part == "..":
            raise ValueError(f"Invalid static asset path: {value!r}")
        parts.append(part)

    if not parts:
        raise ValueError("Static asset path cannot be empty")

    return PurePosixPath(*parts).as_posix()


def _resolve_static_file(relative_path: str) -> Path:
    return STATIC_DIR.joinpath(*PurePosixPath(relative_path).parts)


def _resolve_asset_revision(file_path: Path, entry: dict) -> str:
    version = str(entry.get("version") or "").strip()
    if version:
        return version
    return str(int(file_path.stat().st_mtime))


@lru_cache(maxsize=1)
def load_frontend_asset_manifest() -> dict[str, dict]:
    if not ASSET_MANIFEST_PATH.is_file():
        return {}

    raw_manifest = json.loads(ASSET_MANIFEST_PATH.read_text(encoding="utf-8"))
    assets: dict[str, dict] = {}

    for asset_name, entry in raw_manifest.items():
        if not isinstance(entry, dict):
            raise ValueError(f"Invalid asset manifest entry: {asset_name}")

        relative_path = _normalize_static_path(entry.get("path", ""))
        file_path = _resolve_static_file(relative_path)
        if not file_path.is_file():
            raise FileNotFoundError(f"Static asset not found for manifest entry {asset_name}: {file_path}")

        asset_data = dict(entry)
        asset_data["path"] = relative_path
        asset_data["revision"] = _resolve_asset_revision(file_path, entry)
        assets[str(asset_name)] = asset_data

    return assets


def get_frontend_asset(name_or_path: str) -> dict:
    manifest = load_frontend_asset_manifest()
    asset = manifest.get(str(name_or_path))
    if asset:
        return dict(asset)

    relative_path = _normalize_static_path(name_or_path)
    file_path = _resolve_static_file(relative_path)
    revision = str(int(file_path.stat().st_mtime)) if file_path.exists() else ""
    return {
        "path": relative_path,
        "revision": revision,
    }


def asset_url(name_or_path: str) -> str:
    asset = get_frontend_asset(name_or_path)
    url = f"/static/{asset['path']}"
    revision = str(asset.get("revision") or "").strip()
    if revision:
        return f"{url}?v={quote(revision, safe='')}"
    return url
