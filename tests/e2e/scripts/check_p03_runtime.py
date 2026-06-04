from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
TEMP_ROOT = (REPO_ROOT / ".codex-temp").resolve()
DEFAULT_RUNTIME_ROOT = TEMP_ROOT / "p03-runtime"


def _resolve_runtime_root(raw: str | None) -> Path:
    runtime = Path(raw) if raw else DEFAULT_RUNTIME_ROOT
    if not runtime.is_absolute():
        runtime = REPO_ROOT / runtime
    runtime = runtime.resolve()
    if runtime != TEMP_ROOT and TEMP_ROOT not in runtime.parents:
        raise SystemExit(f"P03 runtime root must stay under {TEMP_ROOT}; got {runtime}")
    return runtime


def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = [
        "teachers",
        "students",
        "classes",
        "courses",
        "class_offerings",
        "assignments",
        "submissions",
        "course_materials",
        "course_material_assignments",
        "message_center_notifications",
    ]
    result: dict[str, int] = {}
    for table in tables:
        try:
            result[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        except sqlite3.Error:
            result[table] = -1
    return result


def check(runtime_root: Path) -> dict[str, object]:
    db_path = (runtime_root / "db" / "classroom.db").resolve()
    if not db_path.exists():
        raise SystemExit(f"Missing P03 runtime database: {db_path}")
    if TEMP_ROOT not in db_path.parents:
        raise SystemExit(f"P03 runtime database must stay under {TEMP_ROOT}; got {db_path}")
    real_paths = {
        (REPO_ROOT / "data" / "classroom.db").resolve(),
        (REPO_ROOT / "data" / "db" / "classroom.db").resolve(),
    }
    if db_path in real_paths:
        raise SystemExit(f"P03 runtime database points at a real local data file: {db_path}")
    with sqlite3.connect(db_path) as conn:
        quick_check = str(conn.execute("PRAGMA quick_check").fetchone()[0])
        counts = _counts(conn)
    return {
        "status": "success" if quick_check == "ok" else "failed",
        "runtimeRoot": str(runtime_root),
        "databasePath": str(db_path),
        "quickCheck": quick_check,
        "counts": counts,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    payload = check(_resolve_runtime_root(args.runtime_root))
    print(json.dumps(payload, ensure_ascii=True, indent=2) if args.json else payload)
    return 0 if payload["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
