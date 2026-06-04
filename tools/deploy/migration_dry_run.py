from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sqlite3
import stat
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
TEMP_ROOT = REPO_ROOT / ".codex-temp"
DEFAULT_RUNTIME_ROOT = TEMP_ROOT / "deploy-migration-dry-run"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def resolve_runtime_root(raw: str | None) -> Path:
    runtime = Path(raw) if raw else DEFAULT_RUNTIME_ROOT
    if not runtime.is_absolute():
        runtime = REPO_ROOT / runtime
    runtime = runtime.resolve()
    temp_root = TEMP_ROOT.resolve()
    if runtime != temp_root and temp_root not in runtime.parents:
        raise ValueError(f"migration dry-run root must stay under {temp_root}; got {runtime}")
    return runtime


def source_db_path(raw: str | None = None) -> Path:
    if raw:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = REPO_ROOT / candidate
        candidate = candidate.resolve()
        if not candidate.is_file():
            raise FileNotFoundError(f"source database not found: {candidate}")
        return candidate

    candidates = (
        REPO_ROOT / "data" / "db" / "classroom.db",
        REPO_ROOT / "data" / "classroom.db",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError("Cannot find data/db/classroom.db or data/classroom.db")


def copy_runtime_db(runtime_root: Path, source_db: Path) -> Path:
    runtime_root = resolve_runtime_root(str(runtime_root))
    if runtime_root.exists():
        shutil.rmtree(runtime_root)
    db_dir = runtime_root / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "classroom.db"
    shutil.copy2(source_db, db_path)
    db_path.chmod(db_path.stat().st_mode | stat.S_IREAD | stat.S_IWRITE)
    return db_path


def _sqlite_snapshot(db_path: Path) -> dict[str, Any]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        quick_check = str(conn.execute("PRAGMA quick_check").fetchone()[0])
        table_count = int(
            conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0]
        )
        index_count = int(
            conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'index'").fetchone()[0]
        )
        user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        key_tables: dict[str, int | None] = {}
        for table in (
            "teachers",
            "students",
            "assignments",
            "submissions",
            "course_materials",
            "material_ai_import_records",
            "background_task_ledger",
        ):
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
            key_tables[table] = (
                int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) if exists else None
            )
    return {
        "quick_check": quick_check,
        "table_count": table_count,
        "index_count": index_count,
        "user_version": user_version,
        "key_tables": key_tables,
    }


def run_migration_dry_run(runtime_root: Path, source_db: Path) -> dict[str, Any]:
    runtime_root = resolve_runtime_root(str(runtime_root))
    db_path = copy_runtime_db(runtime_root, source_db)

    os.environ["LANSHARE_DATA_ROOT"] = str(runtime_root)
    os.environ["MAIN_DATA_DIR"] = str(runtime_root)
    os.environ["MAIN_DB_PATH"] = str(db_path)

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    from classroom_app import config as app_config
    from classroom_app import database as app_database

    app_config.DB_PATH = db_path
    app_database.DB_PATH = db_path

    before = _sqlite_snapshot(db_path)
    app_database.init_database()
    after = _sqlite_snapshot(db_path)

    return {
        "status": "ok" if after["quick_check"] == "ok" else "failed",
        "started_at": _now(),
        "source_db": str(source_db),
        "runtime_root": str(runtime_root),
        "copied_db": str(db_path),
        "safety": {
            "runtime_root_under_codex_temp": TEMP_ROOT.resolve() in runtime_root.parents
            or runtime_root == TEMP_ROOT.resolve(),
            "source_db_was_copied": True,
            "production_data_modified": False,
        },
        "before": before,
        "after": after,
    }


def _write_report(report: dict[str, Any], output: Path | None) -> None:
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if output is None:
        print(text)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text + "\n", encoding="utf-8")
    print(f"migration dry-run report written: {output}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run database init/migration against a copied SQLite DB.")
    parser.add_argument("--runtime-root", type=str)
    parser.add_argument("--source-db", type=str)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args(argv)

    try:
        report = run_migration_dry_run(
            resolve_runtime_root(args.runtime_root),
            source_db_path(args.source_db),
        )
    except Exception as exc:
        report = {
            "status": "failed",
            "error": str(exc),
            "runtime_root": str(args.runtime_root or DEFAULT_RUNTIME_ROOT),
            "source_db": str(args.source_db or ""),
            "production_data_modified": False,
        }

    _write_report(report, args.json_output)
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())

