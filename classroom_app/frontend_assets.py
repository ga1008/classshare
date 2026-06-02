import json
import os
from functools import lru_cache
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from markupsafe import Markup, escape

from .config import STATIC_DIR


ASSET_MANIFEST_PATH = STATIC_DIR / "vendor" / "manifest.json"
VITE_DIST_DIR = STATIC_DIR / "dist"
VITE_MANIFEST_PATH = VITE_DIST_DIR / "manifest.json"


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


def _vite_dev_server_url() -> str:
    return os.getenv("LANSHARE_VITE_DEV_SERVER", "").strip().rstrip("/")


def _normalize_vite_dist_file(value: str) -> str:
    raw_value = str(value or "").replace("\\", "/").lstrip("/")
    for prefix in ("static/dist/", "dist/"):
        if raw_value.startswith(prefix):
            raw_value = raw_value[len(prefix):]

    path = PurePosixPath(raw_value)
    parts: list[str] = []
    for part in path.parts:
        if part in ("", "."):
            continue
        if part == "..":
            raise ValueError(f"Invalid Vite asset path: {value!r}")
        parts.append(part)

    if not parts:
        raise ValueError("Vite asset path cannot be empty")

    return PurePosixPath(*parts).as_posix()


def _normalize_vite_entry_path(value: str) -> str:
    path = _normalize_static_path(value)
    return path


def _vite_asset_url(file_name: str) -> str:
    return f"/static/dist/{_normalize_vite_dist_file(file_name)}"


@lru_cache(maxsize=1)
def load_vite_manifest() -> dict[str, dict]:
    if _vite_dev_server_url() or not VITE_MANIFEST_PATH.is_file():
        return {}

    raw_manifest = json.loads(VITE_MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw_manifest, dict):
        raise ValueError("Invalid Vite manifest")

    manifest: dict[str, dict] = {}
    for entry_name, entry in raw_manifest.items():
        if not isinstance(entry, dict):
            raise ValueError(f"Invalid Vite manifest entry: {entry_name}")

        file_name = entry.get("file")
        if file_name:
            dist_file = _normalize_vite_dist_file(str(file_name))
            file_path = VITE_DIST_DIR.joinpath(*PurePosixPath(dist_file).parts)
            if not file_path.is_file():
                raise FileNotFoundError(f"Vite asset not found for manifest entry {entry_name}: {file_path}")

        manifest[str(entry_name)] = dict(entry)

    return manifest


def get_vite_entry(entry_name: str) -> dict | None:
    manifest = load_vite_manifest()
    if not manifest:
        return None

    normalized_entry_name = _normalize_vite_entry_path(entry_name)
    entry = manifest.get(normalized_entry_name) or manifest.get(str(entry_name))
    if entry:
        return dict(entry)

    for key, candidate in manifest.items():
        candidate_src = str(candidate.get("src") or "").replace("\\", "/").lstrip("/")
        candidate_name = str(candidate.get("name") or "").strip()
        if (
            candidate_src == normalized_entry_name
            or key.replace("\\", "/").lstrip("/") == normalized_entry_name
            or candidate_name == entry_name
        ):
            return dict(candidate)

    return None


def _collect_vite_css(entry: dict, manifest: dict[str, dict], seen: set[str] | None = None) -> list[str]:
    seen = seen or set()
    css_files: list[str] = []

    for import_name in entry.get("imports") or []:
        imported_entry = manifest.get(str(import_name))
        if imported_entry:
            css_files.extend(_collect_vite_css(imported_entry, manifest, seen))

    for css_file in entry.get("css") or []:
        normalized_css = _normalize_vite_dist_file(str(css_file))
        if normalized_css not in seen:
            seen.add(normalized_css)
            css_files.append(normalized_css)

    return css_files


def _collect_vite_import_files(entry: dict, manifest: dict[str, dict], seen: set[str] | None = None) -> list[str]:
    seen = seen or set()
    files: list[str] = []

    for import_name in entry.get("imports") or []:
        imported_entry = manifest.get(str(import_name))
        if not imported_entry:
            continue

        imported_file = str(imported_entry.get("file") or "").strip()
        if imported_file:
            normalized_file = _normalize_vite_dist_file(imported_file)
            if normalized_file not in seen:
                seen.add(normalized_file)
                files.append(normalized_file)

        files.extend(_collect_vite_import_files(imported_entry, manifest, seen))

    return files


def vite_entry_tags(entry_name: str) -> Markup:
    dev_server_url = _vite_dev_server_url()
    normalized_entry_name = _normalize_vite_entry_path(entry_name)
    if dev_server_url:
        dev_client = f"{dev_server_url}/@vite/client"
        dev_entry = f"{dev_server_url}/{normalized_entry_name}"
        return Markup(
            f'<script type="module" src="{escape(dev_client)}"></script>\n'
            f'<script type="module" src="{escape(dev_entry)}"></script>'
        )

    entry = get_vite_entry(normalized_entry_name)
    if not entry:
        return Markup("")

    manifest = load_vite_manifest()
    tags: list[str] = []
    for import_file in _collect_vite_import_files(entry, manifest):
        href = _vite_asset_url(import_file)
        tags.append(f'<link rel="modulepreload" crossorigin href="{escape(href)}">')

    for css_file in _collect_vite_css(entry, manifest):
        href = _vite_asset_url(css_file)
        tags.append(f'<link rel="stylesheet" href="{escape(href)}">')

    script_file = str(entry.get("file") or "").strip()
    if script_file:
        src = _vite_asset_url(script_file)
        tags.append(f'<script type="module" crossorigin src="{escape(src)}"></script>')

    return Markup("\n".join(tags))
