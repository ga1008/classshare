from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMP_ROOT = REPO_ROOT / ".codex-temp"
DEFAULT_LAB_ROOT = TEMP_ROOT / "pg-migration-lab"
DEFAULT_REPORTS_DIRNAME = "reports"
DEFAULT_LOGS_DIRNAME = "logs"
DEFAULT_DATA_DIRNAME = "data"
DEFAULT_DB_DIRNAME = "db"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _resolve_repo_path(raw: str | Path | None, default: Path) -> Path:
    path = Path(raw) if raw else default
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def resolve_lab_root(raw: str | Path | None = None) -> Path:
    lab_root = _resolve_repo_path(raw, DEFAULT_LAB_ROOT)
    temp_root = TEMP_ROOT.resolve()
    if lab_root != temp_root and not _is_relative_to(lab_root, temp_root):
        raise ValueError(f"lab root must stay under {temp_root}; got {lab_root}")
    return lab_root


def source_db_path(raw: str | Path | None = None) -> Path:
    if raw:
        source = _resolve_repo_path(raw, REPO_ROOT / "data" / "classroom.db")
        if not source.is_file():
            raise FileNotFoundError(f"source database not found: {source}")
        return source

    candidates = (
        REPO_ROOT / "data" / "db" / "classroom.db",
        REPO_ROOT / "data" / "classroom.db",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError("Cannot find data/db/classroom.db or data/classroom.db")


def _ensure_lab_path(path: Path, lab_root: Path) -> Path:
    resolved = path.resolve()
    if resolved != lab_root and not _is_relative_to(resolved, lab_root):
        raise ValueError(f"path must stay under lab root {lab_root}; got {resolved}")
    return resolved


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_snapshot(db_path: Path) -> dict[str, Any]:
    uri = db_path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.row_factory = sqlite3.Row
        quick_check = str(conn.execute("PRAGMA quick_check").fetchone()[0])
        table_count = int(
            conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        )
        index_count = int(
            conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index'").fetchone()[0]
        )
        user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()
    return {
        "quick_check": quick_check,
        "table_count": table_count,
        "index_count": index_count,
        "user_version": user_version,
        "size_bytes": db_path.stat().st_size,
        "sha256": _sha256_file(db_path),
    }


def backup_sqlite_database(source_db: Path, copied_db: Path) -> None:
    copied_db.parent.mkdir(parents=True, exist_ok=True)
    if copied_db.exists():
        copied_db.unlink()
    source_uri = source_db.resolve().as_uri() + "?mode=ro"
    source_conn = sqlite3.connect(source_uri, uri=True)
    target_conn = sqlite3.connect(copied_db)
    try:
        source_conn.backup(target_conn)
    finally:
        target_conn.close()
        source_conn.close()


def prepare_lab(
    lab_root: Path | str | None = None,
    *,
    source_db: Path | str | None = None,
    clean: bool = False,
) -> dict[str, Any]:
    lab_root = resolve_lab_root(lab_root)
    source_db = source_db_path(source_db)

    if clean and lab_root.exists():
        shutil.rmtree(lab_root)

    data_dir = _ensure_lab_path(lab_root / DEFAULT_DATA_DIRNAME, lab_root)
    reports_dir = _ensure_lab_path(lab_root / DEFAULT_REPORTS_DIRNAME, lab_root)
    logs_dir = _ensure_lab_path(lab_root / DEFAULT_LOGS_DIRNAME, lab_root)
    db_dir = _ensure_lab_path(lab_root / DEFAULT_DB_DIRNAME, lab_root)
    for directory in (data_dir, reports_dir, logs_dir, db_dir):
        directory.mkdir(parents=True, exist_ok=True)

    copied_db = db_dir / "classroom.db"
    backup_sqlite_database(source_db, copied_db)

    report = {
        "status": "ok",
        "generated_at": _now(),
        "lab_root": str(lab_root),
        "data_dir": str(data_dir),
        "reports_dir": str(reports_dir),
        "logs_dir": str(logs_dir),
        "source_db": str(source_db),
        "copied_db": str(copied_db),
        "sqlite_source": _sqlite_snapshot(source_db),
        "sqlite_copy": _sqlite_snapshot(copied_db),
        "safety": {
            "lab_root_under_codex_temp": True,
            "source_db_was_copied": True,
            "production_data_modified": False,
            "real_source_db_modified": False,
        },
    }
    return report


def _run_version_command(name: str, args: Sequence[str], *, timeout: int = 15) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            list(args),
            cwd=REPO_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return {"name": name, "status": "missing", "command": list(args)}
    except subprocess.TimeoutExpired:
        return {"name": name, "status": "timeout", "command": list(args), "timeout_seconds": timeout}

    output = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part.strip())
    return {
        "name": name,
        "status": "ok" if completed.returncode == 0 else "failed",
        "command": list(args),
        "exit_code": completed.returncode,
        "output": output[:2000],
    }


def redact_database_url(value: str) -> str:
    if not value:
        return ""
    try:
        from classroom_app.db.errors import redact_database_url as app_redact

        return app_redact(value)
    except Exception:
        if "@" not in value:
            return value
        prefix, suffix = value.rsplit("@", 1)
        if "://" in prefix:
            scheme, _rest = prefix.split("://", 1)
            return f"{scheme}://***:***@{suffix}"
        return f"***:***@{suffix}"


def collect_environment(lab_root: Path | str | None = None, *, database_url: str | None = None) -> dict[str, Any]:
    lab_root = resolve_lab_root(lab_root)
    effective_database_url = database_url if database_url is not None else os.getenv("DATABASE_URL", "")
    commands = [
        _run_version_command("python", [sys.executable, "--version"]),
        _run_version_command("node", ["node", "--version"]),
        _run_version_command("npm", ["npm", "--version"]),
        _run_version_command("docker", ["docker", "--version"]),
        _run_version_command("docker-compose", ["docker", "compose", "version"]),
        _run_version_command("psql", ["psql", "--version"]),
    ]
    return {
        "status": "ok",
        "generated_at": _now(),
        "repo_root": str(REPO_ROOT),
        "lab_root": str(lab_root),
        "platform": platform.platform(),
        "python_executable": sys.executable,
        "db_engine_env": os.getenv("DB_ENGINE", "sqlite"),
        "database_url_configured": bool(effective_database_url),
        "database_url_redacted": redact_database_url(effective_database_url),
        "postgres_backend_ready_env": os.getenv("POSTGRES_BACKEND_READY", "false"),
        "commands": commands,
        "offline_note": (
            "Missing docker, psql, or image versions are environment readiness issues; "
            "the script does not download dependencies."
        ),
    }


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return {"status": "unreadable", "error": str(exc)}


def _report_statuses(reports_dir: Path) -> list[dict[str, Any]]:
    statuses: list[dict[str, Any]] = []
    if not reports_dir.is_dir():
        return statuses
    for path in sorted(reports_dir.glob("*.json")):
        if path.name == "lab-summary.json":
            continue
        data = _load_json(path)
        entry: dict[str, Any] = {
            "file": str(path),
            "name": path.stem,
            "status": data.get("status", "unknown"),
        }
        if path.name == "migration-readiness.json":
            entry["quick_check"] = data.get("quick_check")
            entry["foreign_key_violations"] = data.get("foreign_key_violations")
            entry["table_count"] = data.get("table_count")
        elif path.name == "file-integrity.json":
            entry["references_checked"] = data.get("references_checked")
            entry["missing_references"] = data.get("missing_references")
            entry["orphan_files"] = {
                key: value.get("orphan_files")
                for key, value in dict(data.get("orphan_files") or {}).items()
                if isinstance(value, dict)
            }
        elif path.name == "migration-dry-run.json":
            entry["source_db"] = data.get("source_db")
            entry["copied_db"] = data.get("copied_db")
            entry["quick_check_after"] = (data.get("after") or {}).get("quick_check")
        elif path.name == "environment.json":
            entry["missing_commands"] = [
                command.get("name")
                for command in data.get("commands", [])
                if command.get("status") == "missing"
            ]
        elif path.name in {"remote-postgres-load-drill.json", "postgres-load-drill.json"}:
            entry["schema_loaded"] = data.get("schema_loaded")
            entry["data_loaded"] = data.get("data_loaded")
            entry["constraints_loaded"] = data.get("constraints_loaded")
            entry["postgres_dump_executed"] = data.get("postgres_dump_executed")
            entry["postgres_restore_executed"] = data.get("postgres_restore_executed")
        statuses.append(entry)
    return statuses


def _postgres_load_report(reports_dir: Path) -> dict[str, Any]:
    for name in ("remote-postgres-load-drill.json", "postgres-load-drill.json"):
        path = reports_dir / name
        if path.is_file():
            return _load_json(path)
    return {}


def summarize_lab(lab_root: Path | str | None = None, *, database_url: str | None = None) -> dict[str, Any]:
    lab_root = resolve_lab_root(lab_root)
    reports_dir = _ensure_lab_path(lab_root / DEFAULT_REPORTS_DIRNAME, lab_root)
    copied_db = lab_root / DEFAULT_DB_DIRNAME / "classroom.db"
    report_statuses = _report_statuses(reports_dir)
    failed = [item for item in report_statuses if item.get("status") not in {"ok", "not_run", "skipped"}]
    postgres_load = _postgres_load_report(reports_dir)
    postgres_loaded = (
        postgres_load.get("status") == "ok"
        and postgres_load.get("schema_loaded") is True
        and postgres_load.get("data_loaded") is True
        and postgres_load.get("constraints_loaded") is True
    )
    return {
        "status": "failed" if failed else "ok",
        "generated_at": _now(),
        "lab_root": str(lab_root),
        "reports_dir": str(reports_dir),
        "copied_db": str(copied_db) if copied_db.exists() else "",
        "postgres_target": {
            "database_url_configured": bool(database_url or os.getenv("DATABASE_URL", "")),
            "database_url_redacted": redact_database_url(database_url or os.getenv("DATABASE_URL", "")),
            "actual_postgres_data_load_executed": postgres_loaded,
            "postgres_dump_executed": bool(postgres_load.get("postgres_dump_executed")),
            "postgres_restore_executed": bool(postgres_load.get("postgres_restore_executed")),
            "reason": (
                "Remote isolated PostgreSQL load drill completed."
                if postgres_loaded
                else "PostgreSQL runtime adapter and data loader are still gated by P01 migration targets."
            ),
        },
        "reports": report_statuses,
        "safety": {
            "lab_root_under_codex_temp": True,
            "production_data_modified": False,
            "remote_data_modified": False,
        },
    }


def cleanup_plan(lab_root: Path | str | None = None) -> dict[str, Any]:
    lab_root = resolve_lab_root(lab_root)
    return {
        "status": "ok",
        "generated_at": _now(),
        "lab_root": str(lab_root),
        "exists": lab_root.exists(),
        "safe_to_delete": True,
        "allowed_delete_root": str(TEMP_ROOT.resolve()),
        "safety": {
            "will_only_delete_lab_root": True,
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
    print(f"report written: {output}")


def write_markdown_summary(report: dict[str, Any], output: Path | None) -> None:
    if output is None:
        return
    lines = [
        "# PostgreSQL Migration Lab Summary",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Lab root: `{report.get('lab_root')}`",
        f"- Copied SQLite DB: `{report.get('copied_db') or 'not found'}`",
        f"- PostgreSQL data load executed: `{report.get('postgres_target', {}).get('actual_postgres_data_load_executed')}`",
        f"- Production data modified: `{report.get('safety', {}).get('production_data_modified')}`",
        f"- Remote data modified: `{report.get('safety', {}).get('remote_data_modified')}`",
        "",
        "## Reports",
        "",
    ]
    for item in report.get("reports", []):
        lines.append(f"- `{item.get('name')}`: `{item.get('status')}`")
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- This lab is a local Win11 safety harness. It does not modify production SQLite data or remote Docker volumes.",
            "- PostgreSQL data loading remains blocked until the dedicated schema/data migration targets pass.",
        ]
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"markdown summary written: {output}")


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--lab-root", type=str, default=str(DEFAULT_LAB_ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare and summarize the local PostgreSQL migration lab.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser("prepare")
    _add_common_args(prepare_parser)
    prepare_parser.add_argument("--source-db", type=str)
    prepare_parser.add_argument("--clean", action="store_true")
    prepare_parser.add_argument("--json-output", type=Path)

    environment_parser = subparsers.add_parser("environment")
    _add_common_args(environment_parser)
    environment_parser.add_argument("--database-url", type=str)
    environment_parser.add_argument("--json-output", type=Path)

    summarize_parser = subparsers.add_parser("summarize")
    _add_common_args(summarize_parser)
    summarize_parser.add_argument("--database-url", type=str)
    summarize_parser.add_argument("--json-output", type=Path)
    summarize_parser.add_argument("--markdown-output", type=Path)

    cleanup_parser = subparsers.add_parser("cleanup-plan")
    _add_common_args(cleanup_parser)
    cleanup_parser.add_argument("--json-output", type=Path)

    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            report = prepare_lab(args.lab_root, source_db=args.source_db, clean=args.clean)
            write_json(report, args.json_output)
        elif args.command == "environment":
            report = collect_environment(args.lab_root, database_url=args.database_url)
            write_json(report, args.json_output)
        elif args.command == "summarize":
            report = summarize_lab(args.lab_root, database_url=args.database_url)
            write_json(report, args.json_output)
            write_markdown_summary(report, args.markdown_output)
        elif args.command == "cleanup-plan":
            report = cleanup_plan(args.lab_root)
            write_json(report, args.json_output)
        else:
            raise ValueError(f"unsupported command: {args.command}")
    except Exception as exc:
        report = {
            "status": "failed",
            "generated_at": _now(),
            "error": str(exc),
            "production_data_modified": False,
        }
        output = getattr(args, "json_output", None)
        write_json(report, output)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
