from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

from classroom_app.storage_paths import (  # noqa: E402
    LEGACY_ATTENDANCE_DIR,
    LEGACY_CHAT_LOG_DIR,
    LEGACY_CHUNKED_UPLOADS_DIR,
    LEGACY_DB_PATH,
    LEGACY_GLOBAL_FILES_DIR,
    LEGACY_HOMEWORK_SUBMISSIONS_DIR,
    LEGACY_ROSTER_DIR,
    LEGACY_RUNTIME_STATE_PATH,
    LEGACY_SHARE_DIR,
    LEGACY_TEXTBOOK_ATTACHMENT_DIR,
    NEW_ATTENDANCE_DIR,
    NEW_CHAT_LOG_DIR,
    NEW_CHUNKED_UPLOADS_DIR,
    NEW_DB_PATH,
    NEW_GLOBAL_FILES_DIR,
    NEW_HOMEWORK_SUBMISSIONS_DIR,
    NEW_ROSTER_DIR,
    NEW_RUNTIME_STATE_PATH,
    NEW_SHARE_DIR,
    NEW_TEXTBOOK_ATTACHMENT_DIR,
    resolve_migrated_file_path,
)


@dataclass(frozen=True)
class MigrationItem:
    label: str
    source: Path
    target: Path
    kind: str


MIGRATION_ITEMS = (
    MigrationItem("sqlite database", LEGACY_DB_PATH, NEW_DB_PATH, "sqlite"),
    MigrationItem("runtime state", LEGACY_RUNTIME_STATE_PATH, NEW_RUNTIME_STATE_PATH, "file"),
    MigrationItem("submission files", LEGACY_HOMEWORK_SUBMISSIONS_DIR, NEW_HOMEWORK_SUBMISSIONS_DIR, "dir"),
    MigrationItem("legacy shared files", LEGACY_SHARE_DIR, NEW_SHARE_DIR, "dir"),
    MigrationItem("roster imports", LEGACY_ROSTER_DIR, NEW_ROSTER_DIR, "dir"),
    MigrationItem("attendance exports", LEGACY_ATTENDANCE_DIR, NEW_ATTENDANCE_DIR, "dir"),
    MigrationItem("chat logs", LEGACY_CHAT_LOG_DIR, NEW_CHAT_LOG_DIR, "dir"),
    MigrationItem("global hash files", LEGACY_GLOBAL_FILES_DIR, NEW_GLOBAL_FILES_DIR, "hash_dir"),
    MigrationItem("textbook attachments", LEGACY_TEXTBOOK_ATTACHMENT_DIR, NEW_TEXTBOOK_ATTACHMENT_DIR, "dir"),
    MigrationItem("chunked upload temp files", LEGACY_CHUNKED_UPLOADS_DIR, NEW_CHUNKED_UPLOADS_DIR, "dir"),
)


HASH_TABLE_COLUMNS = (
    ("course_files", "file_hash"),
    ("course_materials", "file_hash"),
    ("discussion_attachments", "file_hash"),
    ("custom_emojis", "file_hash"),
    ("blog_attachments", "file_hash"),
    ("blog_media_assets", "file_hash"),
    ("app_feedback_attachments", "file_hash"),
    ("teachers", "avatar_file_hash"),
    ("students", "avatar_file_hash"),
)


def _same_path(left: Path, right: Path) -> bool:
    return str(left.absolute()).lower() == str(right.absolute()).lower()


def _iter_files(path: Path):
    if path.is_file():
        yield path
        return
    if not path.is_dir():
        return
    for child in path.rglob("*"):
        if child.is_file():
            yield child


def _describe_path(path: Path) -> str:
    if not path.exists():
        return "missing"
    files = list(_iter_files(path))
    size = sum(item.stat().st_size for item in files)
    return f"{len(files)} file(s), {size / 1024 / 1024:.2f} MB"


def _copy_file(source: Path, target: Path, *, apply: bool, overwrite: bool) -> str:
    if not source.exists():
        return "missing"
    if target.exists():
        if target.stat().st_size == source.stat().st_size and not overwrite:
            return "exists"
        if not overwrite:
            return "conflict"
    if apply:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return "copied"


def _copy_tree(source: Path, target: Path, *, apply: bool, overwrite: bool) -> dict[str, int]:
    stats = {"missing": 0, "copied": 0, "exists": 0, "conflict": 0}
    if not source.exists():
        stats["missing"] += 1
        return stats
    for file_path in _iter_files(source):
        relative_path = file_path.relative_to(source)
        status = _copy_file(file_path, target / relative_path, apply=apply, overwrite=overwrite)
        stats[status] = stats.get(status, 0) + 1
    return stats


def _hash_target(root: Path, file_name: str) -> Path:
    normalized = str(file_name or "").strip().lower()
    if len(normalized) >= 4:
        return root / normalized[:2] / normalized[2:4] / normalized
    return root / normalized


def _copy_hash_tree(source: Path, target: Path, *, apply: bool, overwrite: bool) -> dict[str, int]:
    stats = {"missing": 0, "copied": 0, "exists": 0, "conflict": 0}
    if not source.exists():
        stats["missing"] += 1
        return stats
    for file_path in _iter_files(source):
        target_path = _hash_target(target, file_path.name)
        status = _copy_file(file_path, target_path, apply=apply, overwrite=overwrite)
        stats[status] = stats.get(status, 0) + 1
    return stats


def _backup_sqlite(source: Path, target: Path, *, apply: bool, overwrite: bool) -> str:
    if not source.exists():
        return "missing"
    if target.exists() and not overwrite:
        return "exists"
    if apply:
        target.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as source_conn:
            with sqlite3.connect(target) as target_conn:
                source_conn.backup(target_conn)
    return "copied"


def run_migration(*, apply: bool, overwrite: bool) -> int:
    print("LanShare data layout migration")
    print(f"mode: {'apply' if apply else 'dry-run'}")
    print("")

    exit_code = 0
    for item in MIGRATION_ITEMS:
        if _same_path(item.source, item.target):
            print(f"- {item.label}: already unified at {item.target}")
            continue
        if item.kind == "sqlite":
            status = _backup_sqlite(item.source, item.target, apply=apply, overwrite=overwrite)
            print(f"- {item.label}: {status} | {item.source} -> {item.target}")
        elif item.kind == "file":
            status = _copy_file(item.source, item.target, apply=apply, overwrite=overwrite)
            print(f"- {item.label}: {status} | {item.source} -> {item.target}")
        else:
            if item.kind == "hash_dir":
                stats = _copy_hash_tree(item.source, item.target, apply=apply, overwrite=overwrite)
            else:
                stats = _copy_tree(item.source, item.target, apply=apply, overwrite=overwrite)
            print(
                f"- {item.label}: copied={stats['copied']} exists={stats['exists']} "
                f"conflict={stats['conflict']} missing={stats['missing']}"
            )
            print(f"  source: {_describe_path(item.source)}")
            print(f"  target: {item.target}")
            if stats["conflict"]:
                exit_code = 2
    return exit_code


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.Error:
        return set()
    return {str(row[1]) for row in rows}


def _hash_exists(file_hash: str) -> bool:
    normalized_hash = str(file_hash or "").strip().lower()
    if not normalized_hash:
        return True
    candidates = []
    for root in (NEW_GLOBAL_FILES_DIR, LEGACY_GLOBAL_FILES_DIR):
        if len(normalized_hash) >= 4:
            candidates.append(root / normalized_hash[:2] / normalized_hash[2:4] / normalized_hash)
        candidates.append(root / normalized_hash)
    return any(candidate.is_file() for candidate in candidates)


def _verify_hash_references(conn: sqlite3.Connection) -> tuple[int, list[str]]:
    checked = 0
    missing: list[str] = []
    for table, column in HASH_TABLE_COLUMNS:
        if column not in _table_columns(conn, table):
            continue
        rows = conn.execute(
            f"""
            SELECT DISTINCT {column}
            FROM {table}
            WHERE {column} IS NOT NULL AND TRIM({column}) != ''
            """
        ).fetchall()
        for row in rows:
            checked += 1
            file_hash = str(row[0] or "").strip().lower()
            if not _hash_exists(file_hash):
                missing.append(f"{table}.{column}:{file_hash}")
    return checked, missing


def _verify_submission_paths(conn: sqlite3.Connection) -> tuple[int, list[str]]:
    if "stored_path" not in _table_columns(conn, "submission_files"):
        return 0, []
    rows = conn.execute(
        """
        SELECT id, stored_path, original_filename, relative_path
        FROM submission_files
        ORDER BY id
        """
    ).fetchall()
    missing: list[str] = []
    for row in rows:
        file_id = int(row[0])
        stored_path = str(row[1] or "")
        resolved = resolve_migrated_file_path(
            stored_path,
            active_root=NEW_HOMEWORK_SUBMISSIONS_DIR,
            legacy_roots=(LEGACY_HOMEWORK_SUBMISSIONS_DIR,),
            markers=("homework_submissions", "files/submissions"),
        )
        if not resolved:
            label = str(row[2] or row[3] or stored_path or "").strip()
            if label:
                missing.append(f"submission_files.id={file_id} ({label})")
            else:
                missing.append(f"submission_files.id={file_id}")
    return len(rows), missing


def run_verification() -> int:
    db_path = NEW_DB_PATH if NEW_DB_PATH.exists() else LEGACY_DB_PATH
    if not db_path.exists():
        print(f"verify: database not found at {NEW_DB_PATH} or {LEGACY_DB_PATH}")
        return 2

    with sqlite3.connect(db_path) as conn:
        hash_checked, hash_missing = _verify_hash_references(conn)
        submission_checked, submission_missing = _verify_submission_paths(conn)

    print("")
    print("Verification")
    print(f"- database: {db_path}")
    print(f"- hash-backed files checked: {hash_checked}, missing: {len(hash_missing)}")
    print(f"- submission files checked: {submission_checked}, missing: {len(submission_missing)}")

    for item in [*hash_missing[:20], *submission_missing[:20]]:
        print(f"  missing: {item}")

    if len(hash_missing) > 20 or len(submission_missing) > 20:
        print("  ... more missing references omitted")

    return 1 if hash_missing or submission_missing else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate LanShare runtime data into the unified data directory.")
    parser.add_argument("--apply", action="store_true", help="copy files into the new layout")
    parser.add_argument("--overwrite", action="store_true", help="overwrite conflicting target files")
    parser.add_argument("--verify", action="store_true", help="verify database file references after planning/applying")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    migration_code = run_migration(apply=bool(args.apply), overwrite=bool(args.overwrite))
    if args.verify:
        verify_code = run_verification()
        return migration_code or verify_code
    return migration_code


if __name__ == "__main__":
    raise SystemExit(main())
