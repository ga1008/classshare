from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMP_ROOT = REPO_ROOT / ".codex-temp"
DEFAULT_RUNTIME_ROOT = TEMP_ROOT / "db-backup-rollback"
KEY_TABLES = (
    "teachers",
    "students",
    "assignments",
    "submissions",
    "submission_files",
    "course_materials",
    "email_outbox",
    "classroom_behavior_events",
)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def resolve_runtime_root(raw: str | Path | None = None) -> Path:
    runtime_root = Path(raw) if raw else DEFAULT_RUNTIME_ROOT
    if not runtime_root.is_absolute():
        runtime_root = REPO_ROOT / runtime_root
    runtime_root = runtime_root.resolve()
    temp_root = TEMP_ROOT.resolve()
    if runtime_root != temp_root and not _is_relative_to(runtime_root, temp_root):
        raise ValueError(f"backup rollback runtime root must stay under {temp_root}; got {runtime_root}")
    return runtime_root


def source_db_path(raw: str | Path | None = None) -> Path:
    if raw:
        source = Path(raw)
        if not source.is_absolute():
            source = REPO_ROOT / source
        source = source.resolve()
        if not source.is_file():
            raise FileNotFoundError(f"source database not found: {source}")
        return source
    for candidate in (REPO_ROOT / "data" / "db" / "classroom.db", REPO_ROOT / "data" / "classroom.db"):
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError("Cannot find data/db/classroom.db or data/classroom.db")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup_sqlite(source_db: Path, target_db: Path) -> None:
    target_db.parent.mkdir(parents=True, exist_ok=True)
    if target_db.exists():
        target_db.unlink()
    source_uri = source_db.resolve().as_uri() + "?mode=ro"
    source_conn = sqlite3.connect(source_uri, uri=True)
    target_conn = sqlite3.connect(target_db)
    try:
        source_conn.backup(target_conn)
    finally:
        target_conn.close()
        source_conn.close()


def _snapshot(db_path: Path) -> dict[str, Any]:
    uri = db_path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        quick_check = str(conn.execute("PRAGMA quick_check").fetchone()[0])
        table_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchone()[0]
        )
        index_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
            ).fetchone()[0]
        )
        key_counts: dict[str, int | None] = {}
        for table in KEY_TABLES:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            key_counts[table] = int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]) if exists else None
    finally:
        conn.close()
    return {
        "path": str(db_path),
        "quick_check": quick_check,
        "size_bytes": db_path.stat().st_size,
        "sha256": _sha256_file(db_path),
        "user_table_count": table_count,
        "user_index_count": index_count,
        "key_counts": key_counts,
    }


def _postgres_command_templates() -> dict[str, list[str]]:
    return {
        "pre_cutover_dump": [
            "docker compose -f docker-compose.yml -f docker-compose.postgres.yml exec -T postgres pg_dump -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\" -Fc > /lanshare/data/postgres-backups/pre-cutover.dump",
            "sha256sum /lanshare/data/postgres-backups/pre-cutover.dump",
        ],
        "restore_to_temp_database": [
            "docker compose -f docker-compose.yml -f docker-compose.postgres.yml exec -T postgres createdb -U \"$POSTGRES_USER\" lanshare_restore_drill",
            "docker compose -f docker-compose.yml -f docker-compose.postgres.yml exec -T postgres pg_restore -U \"$POSTGRES_USER\" -d lanshare_restore_drill --clean --if-exists /backups/pre-cutover.dump",
        ],
        "early_failure_rollback": [
            "freeze writes or enter maintenance window",
            "restore the pre-cutover docker.env backup with DB_ENGINE=sqlite",
            "docker compose up -d app mailer blog-crawler agent-worker",
            "run tools/deploy/postflight.ps1 without -CheckPostgres",
        ],
    }


def _load_postgres_drill_report(path: Path | str | None) -> dict[str, Any]:
    if not path:
        return {}
    report_path = Path(path)
    if not report_path.is_absolute():
        report_path = REPO_ROOT / report_path
    try:
        return json.loads(report_path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {"status": "missing", "path": str(report_path)}
    except Exception as exc:
        return {"status": "unreadable", "path": str(report_path), "error": str(exc)}


def run_backup_rollback_drill(
    runtime_root: Path | str | None = None,
    *,
    source_db: Path | str | None = None,
    postgres_drill_report: Path | str | None = None,
) -> dict[str, Any]:
    runtime_root = resolve_runtime_root(runtime_root)
    if runtime_root.exists():
        shutil.rmtree(runtime_root)
    backup_dir = runtime_root / "backups"
    restore_dir = runtime_root / "restore-drill"
    reports_dir = runtime_root / "reports"
    for directory in (backup_dir, restore_dir, reports_dir):
        directory.mkdir(parents=True, exist_ok=True)

    source = source_db_path(source_db)
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_db = backup_dir / f"classroom-sqlite-{timestamp}.db"
    restored_db = restore_dir / "classroom-restored.db"

    _backup_sqlite(source, backup_db)
    _backup_sqlite(backup_db, restored_db)

    source_snapshot = _snapshot(source)
    backup_snapshot = _snapshot(backup_db)
    restore_snapshot = _snapshot(restored_db)
    counts_match = source_snapshot["key_counts"] == restore_snapshot["key_counts"]
    quick_checks_ok = backup_snapshot["quick_check"] == "ok" and restore_snapshot["quick_check"] == "ok"
    postgres_report = _load_postgres_drill_report(postgres_drill_report)
    postgres_dump_executed = bool(postgres_report.get("postgres_dump_executed"))
    postgres_restore_executed = bool(postgres_report.get("postgres_restore_executed"))
    postgres_drill_ok = postgres_report.get("status") == "ok" and postgres_dump_executed and postgres_restore_executed

    return {
        "status": "ok" if counts_match and quick_checks_ok and (not postgres_drill_report or postgres_drill_ok) else "failed",
        "generated_at": _now(),
        "runtime_root": str(runtime_root),
        "source_db": str(source),
        "backup_db": str(backup_db),
        "restored_db": str(restored_db),
        "reports_dir": str(reports_dir),
        "source_snapshot": source_snapshot,
        "backup_snapshot": backup_snapshot,
        "restore_snapshot": restore_snapshot,
        "key_counts_match": counts_match,
        "sqlite_restore_drill_executed": True,
        "postgres_dump_drill_executed": postgres_dump_executed,
        "postgres_restore_drill_executed": postgres_restore_executed,
        "postgres_drill_report": postgres_report,
        "postgres_command_templates": _postgres_command_templates(),
        "rollback_decision_points": [
            "Before DB_ENGINE=postgres: keep SQLite active and fix migration blockers.",
            "Early post-cutover failure: freeze writes, decide whether PostgreSQL writes need export, then restore docker.env to SQLite only if data loss is understood.",
            "Late post-cutover failure: prefer fixing PostgreSQL; rollback to SQLite requires a reverse-sync or compensation plan.",
        ],
        "safety": {
            "runtime_root_under_codex_temp": True,
            "source_db_was_read_only": True,
            "production_data_modified": False,
            "remote_data_modified": False,
        },
    }


def write_json(report: dict[str, Any], output: Path | None) -> None:
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if output is None:
        print(text)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text + "\n", encoding="utf-8")
    print(f"backup rollback report written: {output}")


def write_markdown(report: dict[str, Any], output: Path | None) -> None:
    if output is None:
        return
    lines = [
        "# Database Backup And Rollback Drill",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Source DB: `{report.get('source_db')}`",
        f"- Backup DB: `{report.get('backup_db')}`",
        f"- Restored DB: `{report.get('restored_db')}`",
        f"- SQLite restore drill executed: `{report.get('sqlite_restore_drill_executed')}`",
        f"- PostgreSQL dump drill executed: `{report.get('postgres_dump_drill_executed')}`",
        f"- PostgreSQL restore drill executed: `{report.get('postgres_restore_drill_executed')}`",
        f"- Key counts match: `{report.get('key_counts_match')}`",
        f"- Production data modified: `{report.get('safety', {}).get('production_data_modified')}`",
        "",
        "## Rollback Decision Points",
        "",
    ]
    for item in report.get("rollback_decision_points", []):
        lines.append(f"- {item}")
    lines.extend(["", "## PostgreSQL Command Templates", ""])
    for name, commands in report.get("postgres_command_templates", {}).items():
        lines.append(f"### {name}")
        lines.append("")
        lines.append("```bash")
        lines.extend(commands)
        lines.append("```")
        lines.append("")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"backup rollback markdown written: {output}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a local copied-SQLite backup and rollback drill.")
    parser.add_argument("--runtime-root", type=str)
    parser.add_argument("--source-db", type=str)
    parser.add_argument("--postgres-drill-report", type=Path)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args(argv)

    try:
        report = run_backup_rollback_drill(
            args.runtime_root,
            source_db=args.source_db,
            postgres_drill_report=args.postgres_drill_report,
        )
    except Exception as exc:
        report = {
            "status": "failed",
            "error": str(exc),
            "production_data_modified": False,
            "remote_data_modified": False,
        }
    write_json(report, args.json_output)
    write_markdown(report, args.markdown_output)
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
