from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from classroom_app.db.sql import placeholder, postgres_claim_jobs_sql, postgres_singleton_status_index_sql
from tools import db_inventory


DEFAULT_RUNTIME_ROOT = db_inventory.TEMP_ROOT / "db-concurrency-plan"
SCAN_DIRS = ("classroom_app",)
TRANSACTION_PATTERNS = (
    "BEGIN IMMEDIATE",
    "SAVEPOINT",
    "database is locked",
    "OperationalError",
    "claim_",
    "_claim",
    "queued",
    "running",
    "sending",
    "locked_at",
    "worker_id",
)
QUEUE_TABLE_NAME_RE = re.compile(r"(job|task|queue|outbox)", re.IGNORECASE)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row["name"]) for row in conn.execute(f"PRAGMA table_info({_quote_identifier(table)})").fetchall()]


def _status_counts(conn: sqlite3.Connection, table: str, status_column: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"""
        SELECT { _quote_identifier(status_column) } AS value, COUNT(*) AS count
        FROM { _quote_identifier(table) }
        GROUP BY { _quote_identifier(status_column) }
        ORDER BY count DESC, value ASC
        LIMIT 20
        """
    ).fetchall()
    return [{"value": str(row["value"] or ""), "count": int(row["count"] or 0)} for row in rows]


def scan_transaction_hotspots() -> dict[str, Any]:
    files: dict[str, dict[str, Any]] = {}
    counts = {pattern: 0 for pattern in TRANSACTION_PATTERNS}
    python_files: list[Path] = []
    for scan_dir in SCAN_DIRS:
        root = REPO_ROOT / scan_dir
        if root.exists():
            python_files.extend(path for path in root.rglob("*.py") if path.is_file())

    for path in sorted(python_files):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="replace")
        lines: list[dict[str, Any]] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            matched = [pattern for pattern in TRANSACTION_PATTERNS if pattern in line]
            if not matched:
                continue
            for pattern in matched:
                counts[pattern] += 1
            lines.append({"line": line_number, "patterns": matched, "text": line.strip()[:240]})
        if lines:
            files[path.relative_to(REPO_ROOT).as_posix()] = {"hit_count": len(lines), "lines": lines}
    return {
        "scanned_python_files": len(python_files),
        "files_with_hits": len(files),
        "pattern_counts": counts,
        "files": files,
    }


def _postgres_claim_example(table: str, columns: list[str]) -> str:
    status_column = "status" if "status" in columns else "state"
    if table == "email_outbox":
        return postgres_claim_jobs_sql(
            table,
            claim_status="sending",
            eligible_where_sql=(
                f"({ _quote_identifier(status_column) } = {placeholder('postgres', 1)} "
                f"AND (next_attempt_at IS NULL OR next_attempt_at <= {placeholder('postgres', 2)}))"
            ),
            locked_at_column="locked_at" if "locked_at" in columns else None,
            updated_at_column="updated_at" if "updated_at" in columns else None,
            order_columns=(("created_at", "ASC"), ("id", "ASC")),
            limit_placeholder_index=3,
        ).sql
    worker_column = "worker_id" if "worker_id" in columns else None
    return postgres_claim_jobs_sql(
        table,
        claim_status="running",
        eligible_where_sql=f"{_quote_identifier(status_column)} = {placeholder('postgres', 1)}",
        worker_column=worker_column,
        worker_placeholder_index=2 if worker_column else None,
        started_at_column="started_at" if "started_at" in columns else None,
        updated_at_column="updated_at" if "updated_at" in columns else None,
        order_columns=(("priority", "DESC"), ("created_at", "ASC"), ("id", "ASC"))
        if "priority" in columns
        else (("created_at", "ASC"), ("id", "ASC")),
        limit_placeholder_index=3 if worker_column else 2,
    ).sql


def queue_candidates(copied_db: Path) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    conn = sqlite3.connect(copied_db)
    try:
        conn.row_factory = sqlite3.Row
        table_rows = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()
        for row in table_rows:
            table = str(row["name"])
            columns = _columns(conn, table)
            status_column = "status" if "status" in columns else "state" if "state" in columns else ""
            is_queue_like = bool(QUEUE_TABLE_NAME_RE.search(table))
            if not status_column and not is_queue_like:
                continue
            if not is_queue_like and status_column not in columns:
                continue
            table_count = int(conn.execute(f"SELECT COUNT(*) FROM {_quote_identifier(table)}").fetchone()[0])
            candidate = {
                "table": table,
                "rows": table_count,
                "status_column": status_column,
                "status_counts": _status_counts(conn, table, status_column) if status_column else [],
                "has_id": "id" in columns,
                "has_worker_id": "worker_id" in columns,
                "has_locked_at": "locked_at" in columns,
                "has_next_attempt_at": "next_attempt_at" in columns,
                "has_attempt_count": "attempt_count" in columns,
                "has_priority": "priority" in columns,
                "has_created_at": "created_at" in columns,
                "claim_recommended": is_queue_like and bool(status_column),
                "postgres_claim_sql": "",
                "postgres_singleton_guard_sql": "",
            }
            if candidate["claim_recommended"] and candidate["has_id"] and "created_at" in columns:
                candidate["postgres_claim_sql"] = _postgres_claim_example(table, columns)
            if table == "agent_tasks" and status_column:
                candidate["postgres_singleton_guard_sql"] = postgres_singleton_status_index_sql(table).sql
            candidates.append(candidate)
    finally:
        conn.close()
    return candidates


def build_concurrency_plan(runtime_root: Path, source_db: Path) -> dict[str, Any]:
    runtime_root = db_inventory.resolve_runtime_root(str(runtime_root))
    copied_db = db_inventory.copy_sqlite_database(runtime_root, source_db)
    return {
        "status": "ok",
        "generated_at": _now(),
        "source_db": str(source_db),
        "runtime_root": str(runtime_root),
        "copied_db": str(copied_db),
        "safety": {
            "runtime_root_under_codex_temp": True,
            "source_db_was_copied_with_sqlite_backup_api": True,
            "production_data_modified": False,
        },
        "transaction_hotspots": scan_transaction_hotspots(),
        "queue_candidates": queue_candidates(copied_db),
    }


def markdown_report(report: dict[str, Any]) -> str:
    hotspots = report["transaction_hotspots"]
    queues = report["queue_candidates"]
    lines = [
        "# LanShare Transaction And Queue Concurrency Plan",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Source DB: `{report['source_db']}`",
        f"- Copied DB: `{report['copied_db']}`",
        f"- Production data modified: `{report['safety']['production_data_modified']}`",
        "",
        "## Transaction Hotspots",
        "",
        f"- Scanned Python files: `{hotspots['scanned_python_files']}`",
        f"- Files with transaction/queue hits: `{hotspots['files_with_hits']}`",
        "",
        "| Pattern | Count |",
        "| --- | ---: |",
    ]
    for pattern, count in hotspots["pattern_counts"].items():
        lines.append(f"| `{pattern}` | {count} |")
    lines.extend(
        [
            "",
            "## Queue And Status Table Candidates",
            "",
            "| Table | Rows | Status Column | Claim Recommended | Queue Safety Fields |",
            "| --- | ---: | --- | --- | --- |",
        ]
    )
    for item in queues:
        fields = []
        for label in ("has_worker_id", "has_locked_at", "has_next_attempt_at", "has_attempt_count", "has_priority"):
            if item.get(label):
                fields.append(label.replace("has_", ""))
        lines.append(
            f"| `{item['table']}` | {item['rows']} | `{item['status_column']}` | "
            f"{'yes' if item.get('claim_recommended') else 'no'} | {', '.join(fields) or '-'} |"
        )
    lines.extend(["", "## PostgreSQL Claim SQL Examples", ""])
    for item in queues:
        if not item.get("postgres_claim_sql"):
            continue
        lines.extend([f"### {item['table']}", "", "```sql", item["postgres_claim_sql"], "```", ""])
        if item.get("postgres_singleton_guard_sql"):
            lines.extend(["Singleton running guard:", "", "```sql", item["postgres_singleton_guard_sql"], "```", ""])
    return "\n".join(lines) + "\n"


def _write_json(report: dict[str, Any], output: Path | None) -> None:
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if output is None:
        print(text)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text + "\n", encoding="utf-8")


def _write_markdown(report: dict[str, Any], output: Path | None) -> None:
    if output is None:
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(markdown_report(report), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a safe LanShare transaction and queue concurrency plan.")
    parser.add_argument("--runtime-root", type=str)
    parser.add_argument("--source-db", type=str)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args(argv)

    try:
        report = build_concurrency_plan(
            db_inventory.resolve_runtime_root(args.runtime_root or str(DEFAULT_RUNTIME_ROOT)),
            db_inventory.source_db_path(args.source_db),
        )
    except Exception as exc:
        report = {
            "status": "failed",
            "generated_at": _now(),
            "error": str(exc),
            "runtime_root": str(args.runtime_root or DEFAULT_RUNTIME_ROOT),
            "source_db": str(args.source_db or ""),
            "production_data_modified": False,
        }

    _write_json(report, args.json_output)
    if report.get("status") == "ok":
        _write_markdown(report, args.markdown_output)
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
