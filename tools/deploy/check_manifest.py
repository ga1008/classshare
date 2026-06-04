from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path, PurePosixPath
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIST_DIR = REPO_ROOT / "static" / "dist"
DEFAULT_MANIFEST_PATH = DEFAULT_DIST_DIR / "manifest.json"

DEFAULT_REQUIRED_ENTRIES = (
    "frontend/src/islands/app-shell.tsx",
    "frontend/src/islands/classroom-page.tsx",
    "frontend/src/islands/materials-manage-page.tsx",
    "frontend/src/islands/message-center-page.tsx",
    "frontend/src/islands/message-center-sync.tsx",
    "frontend/src/islands/message-center-workspace-sync.tsx",
    "frontend/src/islands/assignment-submit-sync.tsx",
    "frontend/src/islands/assignment-task-board-sync.tsx",
)


def _normalize_entry(value: str) -> str:
    return str(value or "").replace("\\", "/").lstrip("/")


def _normalize_dist_file(value: str) -> str:
    normalized = str(value or "").replace("\\", "/").lstrip("/")
    if not normalized:
        raise ValueError("empty dist file path")
    if normalized.startswith("../") or "/../" in normalized or normalized == "..":
        raise ValueError(f"unsafe dist file path: {value}")
    return normalized


def _load_manifest(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"Vite manifest not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Vite manifest must be a JSON object")
    manifest: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            raise ValueError(f"Invalid Vite manifest entry: {key}")
        manifest[str(key)] = dict(value)
    return manifest


def _resolve_entry(manifest: dict[str, dict[str, Any]], required: str) -> tuple[str, dict[str, Any]] | None:
    normalized = _normalize_entry(required)
    if normalized in manifest:
        return normalized, manifest[normalized]
    for key, entry in manifest.items():
        if _normalize_entry(key) == normalized:
            return key, entry
        if _normalize_entry(str(entry.get("src") or "")) == normalized:
            return key, entry
        if str(entry.get("name") or "").strip() == required:
            return key, entry
    return None


def _iter_entry_file_refs(
    manifest: dict[str, dict[str, Any]],
    entry_key: str,
    seen_entries: set[str] | None = None,
) -> list[str]:
    seen_entries = seen_entries or set()
    if entry_key in seen_entries:
        return []
    seen_entries.add(entry_key)

    entry = manifest.get(entry_key) or {}
    refs: list[str] = []
    file_name = entry.get("file")
    if file_name:
        refs.append(str(file_name))
    for css_file in entry.get("css") or []:
        refs.append(str(css_file))
    for asset_file in entry.get("assets") or []:
        refs.append(str(asset_file))
    for import_key in entry.get("imports") or []:
        refs.extend(_iter_entry_file_refs(manifest, str(import_key), seen_entries))
    return refs


def check_manifest(
    *,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    dist_dir: Path = DEFAULT_DIST_DIR,
    required_entries: tuple[str, ...] = DEFAULT_REQUIRED_ENTRIES,
) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    dist_dir = dist_dir.resolve()
    manifest = _load_manifest(manifest_path)

    missing_entries: list[str] = []
    missing_files: list[str] = []
    checked_files: set[str] = set()
    resolved_entries: dict[str, str] = {}

    for required in required_entries:
        resolved = _resolve_entry(manifest, required)
        if resolved is None:
            missing_entries.append(required)
            continue
        entry_key, _entry = resolved
        resolved_entries[required] = entry_key
        for ref in _iter_entry_file_refs(manifest, entry_key):
            normalized_ref = _normalize_dist_file(ref)
            checked_files.add(normalized_ref)
            path = dist_dir.joinpath(*PurePosixPath(normalized_ref).parts)
            if not path.is_file():
                missing_files.append(normalized_ref)

    status = "ok" if not missing_entries and not missing_files else "failed"
    return {
        "status": status,
        "manifest_path": str(manifest_path),
        "dist_dir": str(dist_dir),
        "manifest_entry_count": len(manifest),
        "required_entries": list(required_entries),
        "resolved_entries": resolved_entries,
        "checked_files": sorted(checked_files),
        "missing_entries": missing_entries,
        "missing_files": sorted(set(missing_files)),
    }


def _write_report(report: dict[str, Any], output: Path | None) -> None:
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if output is None:
        print(text)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text + "\n", encoding="utf-8")
    print(f"manifest report written: {output}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check Vite manifest entries and built files.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--dist-dir", type=Path, default=DEFAULT_DIST_DIR)
    parser.add_argument("--required-entry", action="append", default=[])
    parser.add_argument("--no-default-required", action="store_true")
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args(argv)

    required = tuple(args.required_entry)
    if not args.no_default_required:
        required = tuple(dict.fromkeys((*DEFAULT_REQUIRED_ENTRIES, *required)))

    try:
        report = check_manifest(
            manifest_path=args.manifest,
            dist_dir=args.dist_dir,
            required_entries=required,
        )
    except Exception as exc:
        report = {
            "status": "failed",
            "error": str(exc),
            "manifest_path": str(args.manifest),
            "dist_dir": str(args.dist_dir),
            "required_entries": list(required),
        }

    _write_report(report, args.json_output)
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())

