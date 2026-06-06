from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import db_inventory


DEFAULT_RUNTIME_ROOT = db_inventory.TEMP_ROOT / "db-migration-readiness"
KEY_TABLES = (
    "teachers",
    "students",
    "classes",
    "courses",
    "class_offerings",
    "assignments",
    "submissions",
    "submission_files",
    "course_materials",
    "email_outbox",
    "agent_tasks",
    "classroom_behavior_events",
)
STATUS_COLUMN_NAMES = {"status", "state", "email_status", "ai_parse_status", "runtime_status"}
JSON_NAME_HINTS = ("json", "payload", "metadata", "context", "snapshot", "details", "config", "settings", "result")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _table_names(conn: sqlite3.Connection) -> list[str]:
    return [
        str(row["name"])
        for row in conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()
    ]


def _columns(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(f"PRAGMA table_info({_quote_identifier(table)})").fetchall()]


def _single_integer_primary_key(columns: list[dict[str, Any]]) -> str | None:
    pk_columns = [column for column in columns if int(column["pk"] or 0) > 0]
    if len(pk_columns) != 1:
        return None
    column = pk_columns[0]
    declared_type = str(column["type"] or "").upper()
    return str(column["name"]) if "INT" in declared_type else None


def _key_table_counts(conn: sqlite3.Connection, table_names: set[str]) -> dict[str, int | None]:
    counts: dict[str, int | None] = {}
    for table in KEY_TABLES:
        counts[table] = (
            int(conn.execute(f"SELECT COUNT(*) FROM {_quote_identifier(table)}").fetchone()[0])
            if table in table_names
            else None
        )
    return counts


def _primary_key_maxima(conn: sqlite3.Connection, table_names: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for table in table_names:
        columns = _columns(conn, table)
        pk = _single_integer_primary_key(columns)
        if not pk:
            continue
        max_value = conn.execute(
            f"SELECT MAX({_quote_identifier(pk)}) FROM {_quote_identifier(table)}"
        ).fetchone()[0]
        max_int = int(max_value or 0)
        rows.append(
            {
                "table": table,
                "primary_key": pk,
                "max_value": max_int,
                "postgres_sequence_sql": (
                    f"SELECT setval(pg_get_serial_sequence('{table}', '{pk}'), "
                    f"GREATEST({max_int}, 1), true);"
                ),
            }
        )
    return rows


def _status_values(conn: sqlite3.Connection, table_names: list[str]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for table in table_names:
        columns = _columns(conn, table)
        for column in columns:
            column_name = str(column["name"])
            if column_name not in STATUS_COLUMN_NAMES and not column_name.endswith("_status"):
                continue
            rows = conn.execute(
                f"""
                SELECT {_quote_identifier(column_name)} AS value, COUNT(*) AS count
                FROM {_quote_identifier(table)}
                GROUP BY {_quote_identifier(column_name)}
                ORDER BY count DESC, value ASC
                LIMIT 30
                """
            ).fetchall()
            values.append(
                {
                    "table": table,
                    "column": column_name,
                    "values": [
                        {"value": str(row["value"] or ""), "count": int(row["count"] or 0)}
                        for row in rows
                    ],
                }
            )
    return values


def _json_candidate_columns(columns: list[dict[str, Any]]) -> list[str]:
    result: list[str] = []
    for column in columns:
        name = str(column["name"])
        declared = str(column["type"] or "").upper()
        if "TEXT" not in declared and declared:
            continue
        if any(hint in name.lower() for hint in JSON_NAME_HINTS):
            result.append(name)
    return result


def _json_samples(conn: sqlite3.Connection, table_names: list[str], *, sample_limit: int = 200) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for table in table_names:
        columns = _json_candidate_columns(_columns(conn, table))
        for column in columns:
            rows = conn.execute(
                f"""
                SELECT {_quote_identifier(column)} AS value
                FROM {_quote_identifier(table)}
                WHERE {_quote_identifier(column)} IS NOT NULL
                  AND TRIM({_quote_identifier(column)}) != ''
                LIMIT ?
                """,
                (sample_limit,),
            ).fetchall()
            checked = 0
            invalid = 0
            invalid_samples: list[str] = []
            for row in rows:
                raw = str(row["value"] or "")
                checked += 1
                try:
                    json.loads(raw)
                except json.JSONDecodeError:
                    invalid += 1
                    if len(invalid_samples) < 5:
                        invalid_samples.append(raw[:160])
            if checked or column.endswith("_json"):
                reports.append(
                    {
                        "table": table,
                        "column": column,
                        "sample_checked": checked,
                        "sample_invalid": invalid,
                        "invalid_samples": invalid_samples,
                    }
                )
    return reports


def _foreign_key_violations(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute("PRAGMA foreign_key_check").fetchall()]


def build_readiness_report(runtime_root: Path, source_db: Path) -> dict[str, Any]:
    runtime_root = db_inventory.resolve_runtime_root(str(runtime_root))
    copied_db = db_inventory.copy_sqlite_database(runtime_root, source_db)
    conn = sqlite3.connect(copied_db)
    try:
        conn.row_factory = sqlite3.Row
        table_names = _table_names(conn)
        table_name_set = set(table_names)
        quick_check = str(conn.execute("PRAGMA quick_check").fetchone()[0])
        index_count = int(conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'index'").fetchone()[0])
        fk_violations = _foreign_key_violations(conn)
        key_counts = _key_table_counts(conn, table_name_set)
        pk_maxima = _primary_key_maxima(conn, table_names)
        status_values = _status_values(conn, table_names)
        json_samples = _json_samples(conn, table_names)
    finally:
        conn.close()

    blocking_issues: list[dict[str, Any]] = []
    if quick_check != "ok":
        blocking_issues.append({"id": "MR-R001", "severity": "blocker", "message": "SQLite quick_check failed"})
    if fk_violations:
        blocking_issues.append(
            {
                "id": "MR-R002",
                "severity": "blocker",
                "message": "Foreign key violations must be repaired or explicitly exempted before migration",
                "count": len(fk_violations),
            }
        )
    invalid_json_columns = [
        item for item in json_samples if int(item["sample_invalid"] or 0) > 0
    ]
    if invalid_json_columns:
        blocking_issues.append(
            {
                "id": "MR-R003",
                "severity": "review",
                "message": "JSON candidate columns contain invalid JSON samples",
                "count": len(invalid_json_columns),
            }
        )

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
        "quick_check": quick_check,
        "table_count": len(table_names),
        "index_count": index_count,
        "key_table_counts": key_counts,
        "foreign_key_violations": len(fk_violations),
        "foreign_key_violation_samples": fk_violations[:30],
        "primary_key_maxima": pk_maxima,
        "status_values": status_values,
        "json_samples": json_samples,
        "blocking_issues": blocking_issues,
    }


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# LanShare Data Migration Readiness Report",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Source DB: `{report['source_db']}`",
        f"- Copied DB: `{report['copied_db']}`",
        f"- Production data modified: `{report['safety']['production_data_modified']}`",
        f"- Quick check: `{report['quick_check']}`",
        f"- Tables: `{report['table_count']}`",
        f"- Indexes: `{report['index_count']}`",
        f"- Foreign key violations: `{report['foreign_key_violations']}`",
        "",
        "## Key Table Counts",
        "",
        "| Table | Rows |",
        "| --- | ---: |",
    ]
    for table, count in report["key_table_counts"].items():
        lines.append(f"| `{table}` | {'' if count is None else count} |")
    lines.extend(["", "## Blocking Or Review Issues", "", "| ID | Severity | Message | Count |", "| --- | --- | --- | ---: |"])
    for issue in report["blocking_issues"]:
        lines.append(f"| {issue['id']} | {issue['severity']} | {issue['message']} | {issue.get('count', '')} |")
    lines.extend(["", "## Primary Key Sequence Alignment Plan", "", "| Table | PK | Max | PostgreSQL setval SQL |", "| --- | --- | ---: | --- |"])
    for item in report["primary_key_maxima"][:80]:
        lines.append(
            f"| `{item['table']}` | `{item['primary_key']}` | {item['max_value']} | `{item['postgres_sequence_sql']}` |"
        )
    lines.extend(["", "## Status Value Samples", "", "| Table | Column | Values |", "| --- | --- | --- |"])
    for item in report["status_values"][:80]:
        values = ", ".join(f"{entry['value']}={entry['count']}" for entry in item["values"][:10])
        lines.append(f"| `{item['table']}` | `{item['column']}` | {values} |")
    lines.extend(["", "## JSON Parse Samples", "", "| Table | Column | Checked | Invalid |", "| --- | --- | ---: | ---: |"])
    for item in report["json_samples"][:120]:
        lines.append(f"| `{item['table']}` | `{item['column']}` | {item['sample_checked']} | {item['sample_invalid']} |")
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
    parser = argparse.ArgumentParser(description="Build a safe LanShare data migration readiness report.")
    parser.add_argument("--runtime-root", type=str)
    parser.add_argument("--source-db", type=str)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args(argv)

    try:
        report = build_readiness_report(
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
