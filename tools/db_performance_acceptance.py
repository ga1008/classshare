from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMP_ROOT = REPO_ROOT / ".codex-temp"
DEFAULT_RUNTIME_ROOT = TEMP_ROOT / "db-performance-acceptance"

RECOMMENDED_INDEXES = (
    {
        "table": "submissions",
        "columns": ("assignment_id", "student_pk_id", "status"),
        "purpose": "Teacher submission list and per-student submission lookup.",
    },
    {
        "table": "submission_files",
        "columns": ("submission_id",),
        "purpose": "Load attachments for a submission without scanning all uploaded files.",
    },
    {
        "table": "submission_drafts",
        "columns": ("assignment_id", "student_pk_id"),
        "purpose": "Draft autosave lookup and conflict checks.",
    },
    {
        "table": "submission_draft_files",
        "columns": ("draft_id",),
        "purpose": "Load draft attachments during autosave and submit flows.",
    },
    {
        "table": "classroom_behavior_events",
        "columns": ("class_offering_id", "student_id", "created_at"),
        "purpose": "Classroom behavior timeline and batch analytics.",
    },
    {
        "table": "email_outbox",
        "columns": ("status", "next_attempt_at", "created_at"),
        "purpose": "Email worker queue claiming and retry scheduling.",
    },
    {
        "table": "agent_tasks",
        "columns": ("status", "updated_at"),
        "purpose": "Agent worker polling and teacher task-center status refresh.",
    },
    {
        "table": "assignment_wrong_summary_jobs",
        "columns": ("status", "updated_at"),
        "purpose": "Wrong-question summary worker recovery and polling.",
    },
    {
        "table": "private_message_ai_jobs",
        "columns": ("status", "updated_at"),
        "purpose": "Private-message AI reply worker polling.",
    },
    {
        "table": "course_materials",
        "columns": ("folder_id", "created_by_teacher_id", "updated_at"),
        "purpose": "Teacher material library folder listing.",
    },
    {
        "table": "message_center_items",
        "columns": ("recipient_role", "recipient_id", "read_at", "created_at"),
        "purpose": "Notification list and unread-count queries.",
    },
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
        raise ValueError(f"performance runtime root must stay under {temp_root}; got {runtime_root}")
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


def _load_optional_json(path: Path | str | None) -> dict[str, Any]:
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


def _copy_sqlite_database(source_db: Path, target_db: Path) -> None:
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


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row["name"]) for row in conn.execute(f"PRAGMA table_info({_quote_identifier(table)})").fetchall()]


def _index_columns(conn: sqlite3.Connection, table: str) -> dict[str, tuple[str, ...]]:
    indexes: dict[str, tuple[str, ...]] = {}
    for row in conn.execute(f"PRAGMA index_list({_quote_identifier(table)})").fetchall():
        index_name = str(row["name"])
        columns = tuple(
            str(index_row["name"])
            for index_row in conn.execute(f"PRAGMA index_info({_quote_identifier(index_name)})").fetchall()
            if index_row["name"] is not None
        )
        if columns:
            indexes[index_name] = columns
    return indexes


def _has_covering_prefix(indexes: dict[str, tuple[str, ...]], columns: tuple[str, ...]) -> tuple[bool, str]:
    for index_name, index_columns in indexes.items():
        if tuple(index_columns[: len(columns)]) == columns:
            return True, index_name
    return False, ""


def _sample_value(column: str) -> Any:
    if column.endswith("_id") or column == "id":
        return 1
    if column.endswith("_at"):
        return "2026-01-01T00:00:00"
    if column == "status":
        return "queued"
    if column == "read_at":
        return None
    if column == "recipient_role":
        return "student"
    return "sample"


def _build_explain_sql(table: str, columns: tuple[str, ...]) -> tuple[str, tuple[Any, ...]]:
    predicates: list[str] = []
    params: list[Any] = []
    order_column = ""
    for column in columns:
        if column.endswith("_at") and not order_column:
            order_column = column
            continue
        if column == "read_at":
            predicates.append(f"{_quote_identifier(column)} IS NULL")
            continue
        predicates.append(f"{_quote_identifier(column)} = ?")
        params.append(_sample_value(column))
    where_sql = " AND ".join(predicates) if predicates else "1=1"
    order_sql = f" ORDER BY {_quote_identifier(order_column)} DESC" if order_column else ""
    sql = f"SELECT id FROM {_quote_identifier(table)} WHERE {where_sql}{order_sql} LIMIT 20"
    return sql, tuple(params)


def _explain_query_plan(conn: sqlite3.Connection, table: str, columns: tuple[str, ...]) -> dict[str, Any]:
    sql, params = _build_explain_sql(table, columns)
    plan_rows = conn.execute(f"EXPLAIN QUERY PLAN {sql}", params).fetchall()
    details = [str(row["detail"]) for row in plan_rows]
    full_scan = any(f"SCAN {table}" in detail and "USING" not in detail for detail in details)
    return {
        "table": table,
        "sql": sql,
        "params": list(params),
        "plan": details,
        "uses_full_scan": full_scan,
    }


def build_performance_acceptance_report(
    runtime_root: Path | str | None = None,
    *,
    source_db: Path | str | None = None,
    postgres_performance_report: Path | str | None = None,
) -> dict[str, Any]:
    runtime_root = resolve_runtime_root(runtime_root)
    if runtime_root.exists():
        shutil.rmtree(runtime_root)
    copied_db = runtime_root / "db" / "classroom.db"
    reports_dir = runtime_root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    source = source_db_path(source_db)
    _copy_sqlite_database(source, copied_db)

    conn = sqlite3.connect(copied_db)
    conn.row_factory = sqlite3.Row
    try:
        quick_check = str(conn.execute("PRAGMA quick_check").fetchone()[0])
        recommendations: list[dict[str, Any]] = []
        explain_plans: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for item in RECOMMENDED_INDEXES:
            table = str(item["table"])
            columns = tuple(str(column) for column in item["columns"])
            if not _table_exists(conn, table):
                skipped.append({"table": table, "reason": "table_missing", "columns": list(columns)})
                continue
            existing_columns = set(_columns(conn, table))
            missing_columns = [column for column in columns if column not in existing_columns]
            if missing_columns:
                skipped.append(
                    {
                        "table": table,
                        "reason": "column_missing",
                        "columns": list(columns),
                        "missing_columns": missing_columns,
                    }
                )
                continue
            indexes = _index_columns(conn, table)
            covered, index_name = _has_covering_prefix(indexes, columns)
            explain = _explain_query_plan(conn, table, columns)
            explain_plans.append(explain)
            if not covered:
                recommendations.append(
                    {
                        "table": table,
                        "columns": list(columns),
                        "purpose": item["purpose"],
                        "sqlite_plan_uses_full_scan": explain["uses_full_scan"],
                        "postgres_index_sql": (
                            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                            f"idx_{table}_{'_'.join(columns)} ON {table} ({', '.join(columns)});"
                        ),
                    }
                )
            else:
                explain["covered_by_index"] = index_name
        table_names = [
            str(row["name"])
            for row in conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            ).fetchall()
        ]
        top_tables = sorted(
            (
                {
                    "table": table,
                    "rows": int(conn.execute(f"SELECT COUNT(*) FROM {_quote_identifier(table)}").fetchone()[0]),
                }
                for table in table_names
            ),
            key=lambda item: (-int(item["rows"]), str(item["table"])),
        )[:20]
    finally:
        conn.close()

    postgres_report = _load_optional_json(postgres_performance_report)
    postgres_baseline_recorded = bool(
        postgres_report.get("status") == "ok"
        and postgres_report.get("postgres_baseline_recorded") is True
        and postgres_report.get("query_results")
    )
    remote_load_test_recorded = bool(postgres_report.get("remote_docker_load_test_recorded") is True)
    acceptance_gates = {
        "sqlite_baseline_recorded": True,
        "postgres_baseline_recorded": postgres_baseline_recorded,
        "remote_docker_load_test_recorded": remote_load_test_recorded,
        "missing_index_recommendations": len(recommendations),
        "full_scan_plan_count": sum(1 for item in explain_plans if item.get("uses_full_scan")),
    }
    return {
        "status": "ok",
        "generated_at": _now(),
        "runtime_root": str(runtime_root),
        "source_db": str(source),
        "copied_db": str(copied_db),
        "reports_dir": str(reports_dir),
        "quick_check": quick_check,
        "recommended_index_count": len(recommendations),
        "missing_index_recommendations": recommendations,
        "explain_plans": explain_plans,
        "skipped_recommendations": skipped,
        "top_tables": top_tables,
        "acceptance_gates": acceptance_gates,
        "postgres_performance_report": postgres_report,
        "performance_thresholds": {
            "postgres_read_p95_vs_sqlite_max_ratio": 1.2,
            "concurrent_write_error_rate_max": 0.01,
            "queue_duplicate_claims_allowed": 0,
            "behavior_queue_long_term_full_allowed": False,
        },
        "safety": {
            "source_db_was_copied": True,
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
    print(f"performance acceptance report written: {output}")


def write_markdown(report: dict[str, Any], output: Path | None) -> None:
    if output is None:
        return
    lines = [
        "# Database Performance Acceptance",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Quick check: `{report.get('quick_check')}`",
        f"- Copied DB: `{report.get('copied_db')}`",
        f"- Missing index recommendations: `{report.get('recommended_index_count')}`",
        f"- Production data modified: `{report.get('safety', {}).get('production_data_modified')}`",
        "",
        "## Missing Index Recommendations",
        "",
    ]
    for item in report.get("missing_index_recommendations", []):
        lines.extend(
            [
                f"### {item['table']} ({', '.join(item['columns'])})",
                "",
                f"- Purpose: {item['purpose']}",
                f"- SQLite full scan: `{item['sqlite_plan_uses_full_scan']}`",
                "",
                "```sql",
                item["postgres_index_sql"],
                "```",
                "",
            ]
        )
    lines.extend(["## Acceptance Gates", ""])
    for key, value in report.get("acceptance_gates", {}).items():
        lines.append(f"- `{key}`: `{value}`")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"performance acceptance markdown written: {output}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build database performance and index acceptance report.")
    parser.add_argument("--runtime-root", type=str)
    parser.add_argument("--source-db", type=str)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    parser.add_argument("--postgres-performance-report", type=Path)
    args = parser.parse_args(argv)

    try:
        report = build_performance_acceptance_report(
            args.runtime_root,
            source_db=args.source_db,
            postgres_performance_report=args.postgres_performance_report,
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
