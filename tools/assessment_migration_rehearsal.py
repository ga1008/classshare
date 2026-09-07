"""Rehearse the assessment release migrations on a fresh offline SQLite clone.

No configured database lookup, application startup, default backup location,
network connection, model request, or cleanup is performed. PostgreSQL must be
rehearsed natively on a restored, isolated PostgreSQL database before deployment;
a SQLite pass does not satisfy that gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def encode_value(value: Any) -> list[Any]:
    # Preserve storage types, exact strings and blobs; JSON content is not
    # reserialized or normalized into a different historical representation.
    if value is None:
        return ["null"]
    if isinstance(value, bytes):
        return ["blob", value.hex()]
    if isinstance(value, int):
        return ["integer", str(value)]
    if isinstance(value, float):
        return ["real", value.hex()]
    return ["text", str(value)]


def row_bytes(row) -> bytes:
    return json.dumps([encode_value(value) for value in row], ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def update_digest(digest, data: bytes) -> None:
    digest.update(len(data).to_bytes(8, "big"))
    digest.update(data)


def snapshot(conn: sqlite3.Connection, baseline: dict[str, Any] | None = None) -> dict[str, Any]:
    """Hash rows in deterministic order, streaming rather than retaining data."""
    schema = conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
    present_tables = {str(row[1]) for row in schema if row[0] == "table"}
    tables = {}
    for table in sorted(baseline["tables"] if baseline else present_tables):
        if table not in present_tables:
            tables[table] = {"missing_table": True}
            continue
        infos = {row[1]: tuple(row)[1:] for row in conn.execute(f"PRAGMA table_info({quote_identifier(table)})")}
        names = baseline["tables"][table]["columns"] if baseline else list(infos)
        if missing := set(names) - set(infos):
            tables[table] = {"missing_columns": sorted(missing)}
            continue
        primary = sorted((name for name in names if infos[name][-1]), key=lambda name: infos[name][-1])
        foreign_keys = [tuple(row) for row in conn.execute(f"PRAGMA foreign_key_list({quote_identifier(table)})") if row[3] in names]
        foreign_key_columns = {row[3] for row in foreign_keys}
        relations = [name for name in names if name in foreign_key_columns or name.endswith(("_id", "_pk", "_ids", "_ids_json"))]
        selected = ",".join(quote_identifier(name) for name in names)
        ordered = ",".join(quote_identifier(name) for name in (primary + [name for name in names if name not in primary]))
        data_hash, key_hash, relation_hash = (hashlib.sha256() for _ in range(3))
        key_indices = [names.index(name) for name in primary]
        relation_indices = [names.index(name) for name in relations]
        row_count = 0
        for row in conn.execute(f"SELECT {selected} FROM {quote_identifier(table)} ORDER BY {ordered}"):
            row_count += 1
            update_digest(data_hash, row_bytes(row))
            update_digest(key_hash, row_bytes([row[index] for index in key_indices]))
            update_digest(relation_hash, row_bytes([row[index] for index in relation_indices]))
        tables[table] = {
            "columns": names, "column_definitions_sha256": hashlib.sha256(row_bytes([json.dumps([infos[name] for name in names])])).hexdigest(), "row_count": row_count,
            "data_sha256": data_hash.hexdigest(), "primary_key_columns": primary,
            "primary_key_sha256": key_hash.hexdigest() if primary else None,
            "association_columns": relations, "association_sha256": relation_hash.hexdigest(),
            "foreign_key_definitions": foreign_keys,
        }
    return {"tables": tables, "schema_sha256": hashlib.sha256(row_bytes([json.dumps([tuple(row) for row in schema], ensure_ascii=False)])).hexdigest(),
            "quick_check": str(conn.execute("PRAGMA quick_check").fetchone()[0])}


def differences(before: dict[str, Any], after: dict[str, Any], *, include_schema: bool = False) -> list[str]:
    changes = [f"table:{table}" for table in sorted(set(before["tables"]) | set(after["tables"]))
               if before["tables"].get(table) != after["tables"].get(table)]
    if include_schema and before["schema_sha256"] != after["schema_sha256"]:
        changes.append("schema")
    if after["quick_check"] != "ok":
        changes.append("quick_check")
    return changes


def attachment_snapshot(conn: sqlite3.Connection, root: Path | None) -> dict[str, Any]:
    if root is None:
        return {"status": "not_requested", "note": "An explicit offline attachment root is required for file verification."}
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Offline attachment root must be a directory")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(submission_files)")}
    if "relative_path" not in columns:
        return {"status": "not_available", "note": "No submission_files.relative_path mapping exists in this backup."}
    entries, seen, missing, unsafe = [], set(), 0, 0
    for (raw,) in conn.execute("SELECT relative_path FROM submission_files ORDER BY relative_path"):
        relative = str(raw or "")
        if relative in seen:
            continue
        seen.add(relative)
        path_key = hashlib.sha256(relative.encode("utf-8")).hexdigest()
        if not relative:
            missing += 1
            entries.append((path_key, "missing_path"))
            continue
        path = (root / relative).resolve()
        if path != root and root not in path.parents:
            unsafe += 1
            entries.append((path_key, "outside_offline_root"))
        elif not path.is_file():
            missing += 1
            entries.append((path_key, "missing_file"))
        else:
            entries.append((path_key, file_digest(path)))
    return {"status": "ok" if not missing and not unsafe else "failed", "mapped_file_count": len(entries),
            "missing": missing, "outside_root": unsafe,
            "files_sha256": hashlib.sha256(row_bytes([json.dumps(entries)])).hexdigest()}


def apply_assessment_migrations(conn: sqlite3.Connection) -> None:
    """Reuse this release's runtime migrations, with no application startup."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from classroom_app.db.schema_assignments import ensure_assessment_classification_schema
    from classroom_app.db import schema_ai_jobs
    from classroom_app.db.schema_grade_publications import ensure_grade_publication_schema
    ensure_assessment_classification_schema(conn)
    previous_ready = set(schema_ai_jobs._SCHEMA_READY_ENGINES)
    try:
        # Force a real second execution instead of passing via process cache.
        schema_ai_jobs._SCHEMA_READY_ENGINES.discard("sqlite")
        schema_ai_jobs.ensure_ai_job_schema(conn, engine="sqlite")
    finally:
        schema_ai_jobs._SCHEMA_READY_ENGINES = previous_ready
    ensure_grade_publication_schema(conn, engine="sqlite")


def rehearse_sqlite(*, backup: Path, working_copy: Path, attachment_root: Path | None = None,
                    migrate: Callable[[sqlite3.Connection], None] = apply_assessment_migrations) -> dict[str, Any]:
    backup = backup.resolve(strict=True)
    working_copy = working_copy.resolve()
    if not backup.is_file() or backup == working_copy or working_copy.exists():
        raise ValueError("Backup must exist and the distinct working-copy path must not exist")
    if not working_copy.parent.is_dir():
        raise ValueError("Create the explicit offline working directory first")
    source_hash = file_digest(backup)
    # Exclusive creation forbids overwriting any existing file. SQLite's backup
    # API copies a consistent source transaction, including committed WAL data.
    working_copy.touch(exist_ok=False)
    source = sqlite3.connect(backup.as_uri() + "?mode=ro", uri=True)
    conn = sqlite3.connect(working_copy)
    conn.row_factory = sqlite3.Row
    try:
        source.backup(conn)
    finally:
        source.close()
    try:
        before = snapshot(conn)
        if not {"assignments", "submissions"}.issubset(before["tables"]):
            raise ValueError("Backup does not contain the required assignment/submission tables")
        files_before = attachment_snapshot(conn, attachment_root)
        stages, first_full = [], None
        for number in (1, 2):
            try:
                with conn:
                    migrate(conn)
                preserved = snapshot(conn, baseline=before)
                full = snapshot(conn)
                stages.append({"migration": number, "old_field_differences": differences(before, preserved),
                               "old_projection": preserved, "schema_sha256": full["schema_sha256"]})
                if number == 1:
                    first_full = full
                else:
                    idempotency = differences(first_full, full, include_schema=True)
            except Exception as exc:
                # Never dump DB values, SQL parameters, credentials or full
                # database exception messages into a shareable report.
                return {"status": "failed", "engine": "sqlite", "failed_migration": number,
                        "error_type": type(exc).__name__, "stages": stages, "before": before,
                        "source_unchanged": source_hash == file_digest(backup)}
        files_after = attachment_snapshot(conn, attachment_root)
        source_unchanged = source_hash == file_digest(backup)
        database_passed = (before["quick_check"] == "ok" and source_unchanged and not idempotency
                           and all(not stage["old_field_differences"] for stage in stages))
        file_passed = files_before == files_after and files_after["status"] == "ok"
        return {
            "status": "ok" if database_passed and files_after["status"] != "failed" and files_before == files_after else "failed",
            "engine": "sqlite", "migration_scope": "assessment classification, durable AI/review ledger, grade publication runtime schema",
            "database_preservation_passed": database_passed, "source_unchanged": source_unchanged,
            "before": before, "stages": stages, "idempotency_differences": idempotency,
            "added_tables": sorted(set(first_full["tables"]) - set(before["tables"])),
            "added_columns": {table: [column for column in first_full["tables"][table]["columns"] if column not in meta["columns"]]
                              for table, meta in before["tables"].items() if table in first_full["tables"]},
            "attachments": {"before": files_before, "after": files_after, "verified": file_passed},
            "deployment_gate_complete": False,
            "remaining_gates": ["Run native PostgreSQL rehearsal if the deployment uses PostgreSQL.",
                                "Verify a real backup restore and open representative historical attachments/documents.",
                                "Full application startup migrations outside this release's additive schema are not covered."],
        }
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite-backup", required=True, type=Path, help="Explicit, already-created offline backup; read-only input.")
    parser.add_argument("--working-copy", required=True, type=Path, help="New offline destination; existing paths are refused.")
    parser.add_argument("--offline-attachment-root", type=Path)
    parser.add_argument("--report", required=True, type=Path, help="New JSON report file; existing paths are refused.")
    args = parser.parse_args(argv)
    if args.report.exists() or args.report.resolve() in {args.sqlite_backup.resolve(), args.working_copy.resolve()}:
        parser.error("Report must be a new path distinct from both databases")
    try:
        report = rehearse_sqlite(backup=args.sqlite_backup, working_copy=args.working_copy,
                                attachment_root=args.offline_attachment_root)
    except Exception as exc:
        report = {"status": "failed", "engine": "sqlite", "error_type": type(exc).__name__}
    with args.report.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(f"Offline SQLite rehearsal: {report['status']}; report: {args.report}")
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
