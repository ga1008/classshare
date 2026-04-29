from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence


BASE_DIR = Path(__file__).resolve().parent.parent


def _read_first_path_env(names: Sequence[str]) -> Path | None:
    for name in names:
        raw_value = str(os.getenv(name, "") or "").strip()
        if raw_value:
            return Path(raw_value).expanduser()
    return None


def read_data_root() -> Path:
    return _read_first_path_env(("LANSHARE_DATA_ROOT", "MAIN_DATA_DIR")) or BASE_DIR / "data"


DATA_ROOT = read_data_root()

NEW_DB_PATH = DATA_ROOT / "db" / "classroom.db"
LEGACY_DB_PATH = DATA_ROOT / "classroom.db"

NEW_HOMEWORK_SUBMISSIONS_DIR = DATA_ROOT / "files" / "submissions"
LEGACY_HOMEWORK_SUBMISSIONS_DIR = BASE_DIR / "homework_submissions"

NEW_SHARE_DIR = DATA_ROOT / "files" / "legacy_shared"
LEGACY_SHARE_DIR = BASE_DIR / "shared_files"

NEW_ROSTER_DIR = DATA_ROOT / "imports" / "rosters"
LEGACY_ROSTER_DIR = BASE_DIR / "rosters"

NEW_ATTENDANCE_DIR = DATA_ROOT / "imports" / "attendance"
LEGACY_ATTENDANCE_DIR = BASE_DIR / "attendance"

NEW_CHAT_LOG_DIR = DATA_ROOT / "logs" / "chat_logs"
LEGACY_CHAT_LOG_DIR = BASE_DIR / "chat_logs"

NEW_GLOBAL_FILES_DIR = DATA_ROOT / "media" / "blobs" / "sha256"
LEGACY_GLOBAL_FILES_DIR = BASE_DIR / "storage" / "global_files"

NEW_TEXTBOOK_ATTACHMENT_DIR = DATA_ROOT / "files" / "textbook_attachments"
LEGACY_TEXTBOOK_ATTACHMENT_DIR = BASE_DIR / "storage" / "textbook_attachments"

NEW_CHUNKED_UPLOADS_DIR = DATA_ROOT / "tmp" / "chunked_uploads"
LEGACY_CHUNKED_UPLOADS_DIR = BASE_DIR / "storage" / "chunked_uploads"

NEW_RUNTIME_STATE_PATH = DATA_ROOT / "runtime" / "runtime_state.json"
LEGACY_RUNTIME_STATE_PATH = DATA_ROOT / "runtime_state.json"


_PLACEHOLDER_FILENAMES = {
    "__init__.py",
    ".gitkeep",
    ".keep",
}


def _is_placeholder_file(path: Path) -> bool:
    name = path.name.lower()
    if name in _PLACEHOLDER_FILENAMES:
        return True
    if name.endswith(".txt"):
        try:
            return path.stat().st_size == 0
        except OSError:
            return True
    return False


def path_has_payload(path: Path) -> bool:
    """Return true when a legacy path appears to contain real runtime data."""
    try:
        if path.is_file():
            return path.stat().st_size > 0
        if not path.is_dir():
            return False
        for child in path.iterdir():
            if child.is_file() and _is_placeholder_file(child):
                continue
            return True
    except OSError:
        return False
    return False


def select_compatible_file(
    env_names: Sequence[str],
    new_path: Path,
    legacy_paths: Sequence[Path] = (),
) -> Path:
    env_path = _read_first_path_env(env_names)
    if env_path is not None:
        return env_path
    if new_path.exists():
        return new_path
    for legacy_path in legacy_paths:
        if legacy_path.exists():
            return legacy_path
    return new_path


def select_compatible_dir(
    env_names: Sequence[str],
    new_path: Path,
    legacy_paths: Sequence[Path] = (),
) -> Path:
    env_path = _read_first_path_env(env_names)
    if env_path is not None:
        return env_path
    if path_has_payload(new_path):
        return new_path
    for legacy_path in legacy_paths:
        if path_has_payload(legacy_path):
            return legacy_path
    return new_path


def select_preferred_dir(env_names: Sequence[str], new_path: Path) -> Path:
    return _read_first_path_env(env_names) or new_path


def unique_paths(paths: Iterable[Path]) -> tuple[Path, ...]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = str(path.resolve() if path.exists() else path.absolute()).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return tuple(result)


def legacy_candidates(active_path: Path, candidates: Iterable[Path]) -> tuple[Path, ...]:
    return unique_paths(
        candidate
        for candidate in candidates
        if str(candidate.absolute()).lower() != str(active_path.absolute()).lower()
    )


def extract_relative_after_markers(stored_path: str, markers: Sequence[str]) -> str | None:
    normalized = str(stored_path or "").replace("\\", "/").strip()
    if not normalized:
        return None

    for marker in sorted({item.strip("/") for item in markers if item.strip("/")}, key=len, reverse=True):
        for token in (f"/{marker}/", f"{marker}/"):
            index = normalized.rfind(token)
            if index < 0:
                continue
            relative_path = normalized[index + len(token):].strip("/")
            return relative_path or None
    return None


def resolve_migrated_file_path(
    stored_path: str,
    *,
    active_root: Path,
    legacy_roots: Sequence[Path] = (),
    markers: Sequence[str] = (),
) -> Path | None:
    if not stored_path:
        return None

    direct_path = Path(stored_path)
    if direct_path.is_file():
        return direct_path

    search_roots = unique_paths((active_root, *legacy_roots))
    relative_path = extract_relative_after_markers(stored_path, markers)
    if relative_path:
        for root in search_roots:
            candidate = root.joinpath(*PurePosixPath(relative_path).parts)
            if candidate.is_file():
                return candidate

    if not direct_path.is_absolute():
        normalized_relative = str(stored_path).replace("\\", "/").strip("/")
        if normalized_relative:
            for root in search_roots:
                candidate = root.joinpath(*PurePosixPath(normalized_relative).parts)
                if candidate.is_file():
                    return candidate

    return None


def data_layout_manifest() -> dict[str, Path]:
    return {
        "data_root": DATA_ROOT,
        "db": NEW_DB_PATH,
        "submissions": NEW_HOMEWORK_SUBMISSIONS_DIR,
        "shared_files": NEW_SHARE_DIR,
        "rosters": NEW_ROSTER_DIR,
        "attendance": NEW_ATTENDANCE_DIR,
        "chat_logs": NEW_CHAT_LOG_DIR,
        "global_files": NEW_GLOBAL_FILES_DIR,
        "textbook_attachments": NEW_TEXTBOOK_ATTACHMENT_DIR,
        "chunked_uploads": NEW_CHUNKED_UPLOADS_DIR,
        "runtime_state": NEW_RUNTIME_STATE_PATH,
    }
