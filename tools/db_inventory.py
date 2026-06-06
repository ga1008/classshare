from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMP_ROOT = REPO_ROOT / ".codex-temp"
DEFAULT_RUNTIME_ROOT = TEMP_ROOT / "db-inventory"
SCAN_DIRS = ("classroom_app", "tools", "deployment", "tests")
CALL_PATTERNS = (
    "get_db_connection(",
    "sqlite3.",
    "PRAGMA",
    "sqlite_master",
    "BEGIN IMMEDIATE",
    "SAVEPOINT",
    "lastrowid",
    "INSERT OR",
    "ON CONFLICT",
    "AUTOINCREMENT",
    "OperationalError",
    "IntegrityError",
    "DatabaseError",
)

DOMAIN_HINTS = (
    ("assignment", "homework"),
    ("submission", "homework"),
    ("homework", "homework"),
    ("teacher", "identity"),
    ("student", "identity"),
    ("organization", "identity"),
    ("classroom", "classroom"),
    ("class_", "classroom"),
    ("course", "materials"),
    ("material", "materials"),
    ("textbook", "materials"),
    ("ai_", "ai"),
    ("agent", "agent"),
    ("email", "email"),
    ("message", "message"),
    ("notification", "message"),
    ("behavior", "behavior"),
    ("learning", "learning"),
    ("smart_", "smart-classroom"),
    ("blog", "blog"),
    ("signature", "files"),
    ("file", "files"),
    ("config", "system"),
    ("setting", "system"),
)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def resolve_runtime_root(raw: str | None) -> Path:
    runtime = Path(raw) if raw else DEFAULT_RUNTIME_ROOT
    if not runtime.is_absolute():
        runtime = REPO_ROOT / runtime
    runtime = runtime.resolve()
    temp_root = TEMP_ROOT.resolve()
    if runtime != temp_root and temp_root not in runtime.parents:
        raise ValueError(f"database inventory root must stay under {temp_root}; got {runtime}")
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


def copy_sqlite_database(runtime_root: Path, source_db: Path) -> Path:
    runtime_root = resolve_runtime_root(str(runtime_root))
    if runtime_root.exists():
        shutil.rmtree(runtime_root)
    db_dir = runtime_root / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    copied_db = db_dir / "classroom.db"
    source_uri = source_db.resolve().as_uri() + "?mode=ro"
    source_conn = sqlite3.connect(source_uri, uri=True)
    target_conn = sqlite3.connect(copied_db)
    try:
        source_conn.backup(target_conn)
    finally:
        target_conn.close()
        source_conn.close()
    return copied_db


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _sqlite_rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _table_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {_quote_identifier(table)}").fetchone()[0])


def _index_columns(conn: sqlite3.Connection, table: str) -> list[list[str]]:
    indexes = conn.execute(f"PRAGMA index_list({_quote_identifier(table)})").fetchall()
    columns: list[list[str]] = []
    for index_row in indexes:
        index_name = str(index_row["name"])
        info_rows = conn.execute(f"PRAGMA index_info({_quote_identifier(index_name)})").fetchall()
        ordered = [str(info["name"]) for info in sorted(info_rows, key=lambda row: int(row["seqno"]))]
        if ordered:
            columns.append(ordered)
    return columns


def _foreign_key_index_candidates(
    conn: sqlite3.Connection,
    table_names: list[str],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for table in table_names:
        fk_rows = conn.execute(f"PRAGMA foreign_key_list({_quote_identifier(table)})").fetchall()
        if not fk_rows:
            continue
        index_sets = _index_columns(conn, table)
        grouped: dict[int, list[str]] = {}
        parents: dict[int, str] = {}
        for row in fk_rows:
            fk_id = int(row["id"])
            grouped.setdefault(fk_id, []).append(str(row["from"]))
            parents[fk_id] = str(row["table"])
        for fk_id, columns in grouped.items():
            has_prefix_index = any(index_cols[: len(columns)] == columns for index_cols in index_sets)
            if not has_prefix_index:
                candidates.append(
                    {
                        "table": table,
                        "columns": columns,
                        "parent_table": parents.get(fk_id, ""),
                    }
                )
    return candidates


def sqlite_snapshot(db_path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        quick_check = str(conn.execute("PRAGMA quick_check").fetchone()[0])
        integrity_rows = _sqlite_rows(conn, "PRAGMA foreign_key_check")
        table_rows = _sqlite_rows(
            conn,
            """
            SELECT name, sql
            FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """,
        )
        index_rows = _sqlite_rows(
            conn,
            """
            SELECT name, tbl_name, sql
            FROM sqlite_master
            WHERE type = 'index'
            ORDER BY tbl_name, name
            """,
        )
        table_names = [str(row["name"]) for row in table_rows]
        table_counts = {table: _table_count(conn, table) for table in table_names}
        top_tables = sorted(
            ({"table": table, "rows": count} for table, count in table_counts.items()),
            key=lambda item: int(item["rows"]),
            reverse=True,
        )[:20]
        fk_index_candidates = _foreign_key_index_candidates(conn, table_names)
    finally:
        conn.close()

    return {
        "path": str(db_path),
        "size_bytes": db_path.stat().st_size,
        "quick_check": quick_check,
        "foreign_key_violations": len(integrity_rows),
        "foreign_key_violation_samples": integrity_rows[:20],
        "table_count": len(table_rows),
        "index_count": len(index_rows),
        "tables": [
            {
                "name": str(row["name"]),
                "rows": table_counts[str(row["name"])],
                "sql": row.get("sql"),
            }
            for row in table_rows
        ],
        "indexes": index_rows,
        "top_tables": top_tables,
        "foreign_key_missing_index_candidates": fk_index_candidates,
    }


def scan_database_call_sites() -> dict[str, Any]:
    pattern_counts = {pattern: 0 for pattern in CALL_PATTERNS}
    file_hits: dict[str, dict[str, Any]] = {}
    python_files: list[Path] = []
    for scan_dir in SCAN_DIRS:
        root = REPO_ROOT / scan_dir
        if not root.exists():
            continue
        python_files.extend(path for path in root.rglob("*.py") if path.is_file())

    for path in sorted(python_files):
        relative = path.relative_to(REPO_ROOT).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="replace")
        line_hits: list[dict[str, Any]] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            matched = [pattern for pattern in CALL_PATTERNS if pattern in line]
            if not matched:
                continue
            for pattern in matched:
                pattern_counts[pattern] += 1
            line_hits.append(
                {
                    "line": line_number,
                    "patterns": matched,
                    "text": line.strip()[:240],
                }
            )
        if line_hits:
            file_hits[relative] = {
                "hit_count": len(line_hits),
                "lines": line_hits,
            }

    dir_counts: dict[str, int] = {}
    for relative in file_hits:
        top = relative.split("/", 1)[0]
        dir_counts[top] = dir_counts.get(top, 0) + 1

    return {
        "scanned_python_files": len(python_files),
        "files_with_hits": len(file_hits),
        "directory_counts": dir_counts,
        "pattern_counts": pattern_counts,
        "files": file_hits,
    }


def build_inventory(runtime_root: Path, source_db: Path) -> dict[str, Any]:
    runtime_root = resolve_runtime_root(str(runtime_root))
    copied_db = copy_sqlite_database(runtime_root, source_db)
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
        "code_inventory": scan_database_call_sites(),
        "sqlite_snapshot": sqlite_snapshot(copied_db),
    }


def markdown_report(report: dict[str, Any]) -> str:
    code = report["code_inventory"]
    snapshot = report["sqlite_snapshot"]
    lines = [
        "# LanShare Database Inventory",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Source DB: `{report['source_db']}`",
        f"- Copied DB: `{report['copied_db']}`",
        f"- Production data modified: `{report['safety']['production_data_modified']}`",
        "",
        "## Code Coupling Summary",
        "",
        f"- Scanned Python files: `{code['scanned_python_files']}`",
        f"- Files with database coupling hits: `{code['files_with_hits']}`",
        "",
        "| Pattern | Count |",
        "| --- | ---: |",
    ]
    for pattern, count in code["pattern_counts"].items():
        lines.append(f"| `{pattern}` | {count} |")

    lines.extend(
        [
            "",
            "## SQLite Snapshot",
            "",
            f"- Quick check: `{snapshot['quick_check']}`",
            f"- Size bytes: `{snapshot['size_bytes']}`",
            f"- Tables: `{snapshot['table_count']}`",
            f"- Indexes: `{snapshot['index_count']}`",
            f"- Foreign key violations: `{snapshot['foreign_key_violations']}`",
            f"- FK missing-index candidates: `{len(snapshot['foreign_key_missing_index_candidates'])}`",
            "",
            "## Largest Tables",
            "",
            "| Table | Rows |",
            "| --- | ---: |",
        ]
    )
    for item in snapshot["top_tables"]:
        lines.append(f"| `{item['table']}` | {item['rows']} |")

    lines.extend(["", "## Files With Database Coupling", "", "| File | Hits |", "| --- | ---: |"])
    for file_name, info in sorted(code["files"].items()):
        lines.append(f"| `{file_name}` | {info['hit_count']} |")

    if snapshot["foreign_key_violation_samples"]:
        lines.extend(["", "## Foreign Key Violation Samples", "", "```json"])
        lines.append(json.dumps(snapshot["foreign_key_violation_samples"], ensure_ascii=False, indent=2))
        lines.append("```")

    return "\n".join(lines) + "\n"


def _table_domain(table_name: str) -> str:
    normalized = table_name.lower()
    for needle, domain in DOMAIN_HINTS:
        if needle in normalized:
            return domain
    return "uncategorized"


def table_map_report(report: dict[str, Any]) -> str:
    snapshot = report["sqlite_snapshot"]
    fk_candidates = {
        item["table"]
        for item in snapshot["foreign_key_missing_index_candidates"]
    }
    lines = [
        "# LanShare Database Table Map",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Copied DB: `{report['copied_db']}`",
        f"- Production data modified: `{report['safety']['production_data_modified']}`",
        "",
        "| Table | Domain | Rows | Has create SQL | FK index candidate |",
        "| --- | --- | ---: | --- | --- |",
    ]
    for table in snapshot["tables"]:
        table_name = str(table["name"])
        lines.append(
            "| `{}` | {} | {} | {} | {} |".format(
                table_name,
                _table_domain(table_name),
                int(table["rows"]),
                "yes" if table.get("sql") else "no",
                "yes" if table_name in fk_candidates else "no",
            )
        )
    return "\n".join(lines) + "\n"


def _risk_row(
    risk_id: str,
    title: str,
    severity: str,
    evidence: str,
    target: str,
    action: str,
) -> str:
    return f"| {risk_id} | {severity} | {title} | {evidence} | {target} | {action} |"


def risk_register_report(report: dict[str, Any]) -> str:
    code = report["code_inventory"]
    snapshot = report["sqlite_snapshot"]
    pattern_counts = code["pattern_counts"]
    rows = [
        _risk_row(
            "DB-R001",
            "Foreign key violations exist in the current SQLite snapshot",
            "high",
            str(snapshot["foreign_key_violations"]),
            "T06",
            "Repair, explicitly exempt, or block cutover before data migration.",
        ),
        _risk_row(
            "DB-R002",
            "Foreign key columns may be missing PostgreSQL indexes",
            "high",
            str(len(snapshot["foreign_key_missing_index_candidates"])),
            "T11",
            "Confirm with query plans and add only justified indexes.",
        ),
        _risk_row(
            "DB-R003",
            "SQLite write-lock transactions must be redesigned",
            "high",
            str(pattern_counts.get("BEGIN IMMEDIATE", 0)),
            "T05",
            "Replace queue and hot writes with short transactions and row-level locking.",
        ),
        _risk_row(
            "DB-R004",
            "Insert id retrieval depends on SQLite lastrowid",
            "medium",
            str(pattern_counts.get("lastrowid", 0)),
            "T04",
            "Route inserts through an insert-returning helper.",
        ),
        _risk_row(
            "DB-R005",
            "SQLite insert conflict syntax remains in use",
            "medium",
            str(pattern_counts.get("INSERT OR", 0)),
            "T04",
            "Replace with explicit upsert or ignore helpers.",
        ),
        _risk_row(
            "DB-R006",
            "SQLite introspection and PRAGMA calls exist outside the target adapter",
            "medium",
            str(pattern_counts.get("PRAGMA", 0) + pattern_counts.get("sqlite_master", 0)),
            "T03/T04",
            "Move introspection to migration and diagnostic tools.",
        ),
        _risk_row(
            "DB-R007",
            "Direct sqlite3 coupling is broad",
            "medium",
            str(pattern_counts.get("sqlite3.", 0)),
            "T02/T04",
            "Keep shrinking direct imports behind the adapter and compatibility helpers.",
        ),
        _risk_row(
            "DB-R008",
            "Large behavior event table is the main write/read hotspot",
            "medium",
            str(next((item["rows"] for item in snapshot["top_tables"] if item["table"] == "classroom_behavior_events"), 0)),
            "T05/T11",
            "Validate batch writes, indexes, retention, and query plans under PostgreSQL.",
        ),
    ]
    lines = [
        "# LanShare Database Risk Register",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Copied DB: `{report['copied_db']}`",
        f"- Production data modified: `{report['safety']['production_data_modified']}`",
        "",
        "| ID | Severity | Risk | Evidence | Target | Required action |",
        "| --- | --- | --- | ---: | --- | --- |",
        *rows,
    ]
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


def _write_text(text: str, output: Path | None) -> None:
    if output is None:
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a safe LanShare database inventory report.")
    parser.add_argument("--runtime-root", type=str)
    parser.add_argument("--source-db", type=str)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    parser.add_argument("--table-map-output", type=Path)
    parser.add_argument("--risk-register-output", type=Path)
    args = parser.parse_args(argv)

    try:
        report = build_inventory(
            resolve_runtime_root(args.runtime_root),
            source_db_path(args.source_db),
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
    _write_markdown(report, args.markdown_output)
    if report.get("status") == "ok":
        _write_text(table_map_report(report), args.table_map_output)
        _write_text(risk_register_report(report), args.risk_register_output)
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
