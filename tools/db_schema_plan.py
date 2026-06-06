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

from classroom_app.db.migration_registry import (
    baseline_migration_for_schema,
    schema_migrations_table_sql,
)
from tools import db_inventory


DEFAULT_RUNTIME_ROOT = db_inventory.TEMP_ROOT / "db-schema-plan"
TIME_NAME_RE = re.compile(r"(^|_)(created|updated|deleted|started|ended|finished|submitted|due|deadline|expires|time|date)(_at|_on|$)")
BOOL_NAME_RE = re.compile(r"^(is|has|can|allow|enable|enabled|disabled|published|active|visible)_|_(enabled|disabled|flag|active)$")
JSON_NAME_RE = re.compile(r"(json|payload|metadata|context|snapshot|details|config|settings|result|response)")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _schema_sql(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        """
        SELECT type, name, tbl_name, sql
        FROM sqlite_master
        WHERE sql IS NOT NULL
          AND name NOT LIKE 'sqlite_%'
        ORDER BY type, name
        """
    ).fetchall()
    return "\n\n".join(str(row["sql"]).strip() for row in rows)


def _columns(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    return [
        {
            "name": str(row["name"]),
            "declared_type": str(row["type"] or ""),
            "not_null": bool(row["notnull"]),
            "default": row["dflt_value"],
            "primary_key_position": int(row["pk"]),
        }
        for row in conn.execute(f"PRAGMA table_info({_quote_identifier(table)})").fetchall()
    ]


def _conversion_risks(table: str, columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    risks: list[dict[str, Any]] = []
    for column in columns:
        name = column["name"].lower()
        declared = column["declared_type"].upper()
        if "INT" in declared and BOOL_NAME_RE.search(name):
            risks.append(
                {
                    "table": table,
                    "column": column["name"],
                    "declared_type": column["declared_type"],
                    "risk": "boolean-candidate",
                    "target": "Decide whether to map 0/1 to PostgreSQL boolean.",
                }
            )
        if ("TEXT" in declared or declared == "") and TIME_NAME_RE.search(name):
            risks.append(
                {
                    "table": table,
                    "column": column["name"],
                    "declared_type": column["declared_type"],
                    "risk": "timestamp-candidate",
                    "target": "Decide whether to map text to timestamptz or preserve text.",
                }
            )
        if ("TEXT" in declared or declared == "") and JSON_NAME_RE.search(name):
            risks.append(
                {
                    "table": table,
                    "column": column["name"],
                    "declared_type": column["declared_type"],
                    "risk": "json-candidate",
                    "target": "Validate JSON parse rate before mapping to jsonb.",
                }
            )
    return risks


def build_schema_plan(runtime_root: Path, source_db: Path) -> dict[str, Any]:
    runtime_root = db_inventory.resolve_runtime_root(str(runtime_root))
    copied_db = db_inventory.copy_sqlite_database(runtime_root, source_db)
    conn = sqlite3.connect(copied_db)
    try:
        conn.row_factory = sqlite3.Row
        schema_sql = _schema_sql(conn)
        baseline = baseline_migration_for_schema(schema_sql)
        table_rows = conn.execute(
            """
            SELECT name, sql
            FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()
        tables: list[dict[str, Any]] = []
        conversion_risks: list[dict[str, Any]] = []
        autoincrement_tables: list[str] = []
        sqlite_specific_sql: list[dict[str, str]] = []
        for row in table_rows:
            table_name = str(row["name"])
            create_sql = str(row["sql"] or "")
            columns = _columns(conn, table_name)
            tables.append(
                {
                    "name": table_name,
                    "columns": columns,
                    "foreign_keys": [
                        dict(fk_row)
                        for fk_row in conn.execute(f"PRAGMA foreign_key_list({_quote_identifier(table_name)})")
                    ],
                    "create_sql": create_sql,
                }
            )
            conversion_risks.extend(_conversion_risks(table_name, columns))
            if "AUTOINCREMENT" in create_sql.upper():
                autoincrement_tables.append(table_name)
            if any(token in create_sql.upper() for token in ("AUTOINCREMENT", " WITHOUT ROWID", "COLLATE NOCASE")):
                sqlite_specific_sql.append({"table": table_name, "sql": create_sql})
    finally:
        conn.close()

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
        "baseline_migration": baseline.__dict__,
        "schema_migrations_sql": {
            "sqlite": schema_migrations_table_sql("sqlite"),
            "postgres": schema_migrations_table_sql("postgres"),
        },
        "table_count": len(tables),
        "tables": tables,
        "postgres_conversion_risks": conversion_risks,
        "sqlite_specific_sql": sqlite_specific_sql,
        "autoincrement_tables": autoincrement_tables,
    }


def markdown_report(report: dict[str, Any]) -> str:
    baseline = report["baseline_migration"]
    lines = [
        "# LanShare Schema Baseline Plan",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Source DB: `{report['source_db']}`",
        f"- Copied DB: `{report['copied_db']}`",
        f"- Production data modified: `{report['safety']['production_data_modified']}`",
        f"- Baseline version: `{baseline['version']}`",
        f"- Baseline checksum: `{baseline['checksum']}`",
        f"- Tables: `{report['table_count']}`",
        f"- SQLite-specific create SQL entries: `{len(report['sqlite_specific_sql'])}`",
        f"- PostgreSQL conversion risk candidates: `{len(report['postgres_conversion_risks'])}`",
        "",
        "## Schema Migrations Table",
        "",
        "### SQLite",
        "",
        "```sql",
        report["schema_migrations_sql"]["sqlite"],
        "```",
        "",
        "### PostgreSQL",
        "",
        "```sql",
        report["schema_migrations_sql"]["postgres"],
        "```",
        "",
        "## Conversion Risk Samples",
        "",
        "| Table | Column | Declared Type | Risk | Target |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in report["postgres_conversion_risks"][:80]:
        lines.append(
            f"| `{item['table']}` | `{item['column']}` | `{item['declared_type']}` | {item['risk']} | {item['target']} |"
        )
    lines.extend(["", "## AUTOINCREMENT Tables", ""])
    for table in report["autoincrement_tables"]:
        lines.append(f"- `{table}`")
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
    parser = argparse.ArgumentParser(description="Build a safe LanShare schema baseline plan.")
    parser.add_argument("--runtime-root", type=str)
    parser.add_argument("--source-db", type=str)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args(argv)

    try:
        report = build_schema_plan(
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
