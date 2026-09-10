"""Verify release migrations on an explicitly restored, loopback-only PG clone.

This tool never discovers a DSN, starts an app/worker, copies production data,
creates a database, or cleans up files. Backup/restore are separate operations.
Only anonymized counts, object names, and digests enter the report.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.assessment_migration_rehearsal import file_digest, quote_identifier, row_bytes, update_digest
from tools.signature_visibility_rehearsal import capture as capture_signature_visibility, prove as prove_signature_visibility
from tools.agent_authority_migration_rehearsal import capture as capture_agent_authority, prove as prove_agent_authority


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def validate_target(*, cluster_dir: Path, port: int, database: str) -> Path:
    cluster_dir = cluster_dir.resolve(strict=True)
    if not cluster_dir.is_dir() or not (cluster_dir / "PG_VERSION").is_file():
        raise ValueError("Explicit dedicated cluster directory is required")
    if not 1024 <= port <= 65535 or port == 5432:
        raise ValueError("Use an explicit non-default isolated port")
    if not re.fullmatch(r"lanshare_assessment_rehearsal(?:_[a-z0-9_]+)?", database):
        raise ValueError("Database must have the dedicated rehearsal prefix")
    return cluster_dir


def connect_offline(*, cluster_dir: Path, port: int, database: str):
    import psycopg
    expected = validate_target(cluster_dir=cluster_dir, port=port, database=database)
    # No host/password/DSN input or configured application database fallback.
    conn = psycopg.connect(host="127.0.0.1", port=port, dbname=database,
                          user="rehearsal_admin", connect_timeout=5,
                          application_name="assessment_offline_rehearsal")
    try:
        data_dir, listener, server_port = conn.execute(
            "SELECT current_setting('data_directory'), current_setting('listen_addresses'), current_setting('port')"
        ).fetchone()
        if Path(data_dir).resolve() != expected or listener != "127.0.0.1" or int(server_port) != port:
            raise ValueError("Server identity does not match the explicit loopback-only cluster")
        conn.execute("SET statement_timeout = '120s'")
        conn.execute("SET lock_timeout = '5s'")
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute("SET DateStyle = 'ISO, YMD'")
        conn.execute("SET IntervalStyle = 'postgres'")
        conn.execute("SET bytea_output = 'hex'")
        conn.execute("SET extra_float_digits = 3")
        conn.commit()
        return conn
    except Exception:
        conn.close()
        raise


def schema_objects(conn) -> dict[str, str]:
    """Hash stable public-schema definitions; never expose defaults or bodies."""
    objects = {}
    queries = (
        ("relation", """SELECT c.relname, c.relkind, c.relpersistence, c.reloptions, c.relrowsecurity,
            c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f') ORDER BY c.relname"""),
        ("column", """SELECT c.relname || '.' || a.attname, a.attnum, format_type(a.atttypid,a.atttypmod),
            a.attnotnull, pg_get_expr(d.adbin,d.adrelid), a.attidentity, a.attgenerated, a.attcollation::regcollation::text
            FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
            WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f') AND a.attnum>0 AND NOT a.attisdropped
            ORDER BY c.relname,a.attnum"""),
        ("constraint", """SELECT c.relname || '.' || k.conname, k.contype, pg_get_constraintdef(k.oid,true),
            k.convalidated, k.condeferrable, k.condeferred FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' ORDER BY c.relname,k.conname"""),
        ("index", "SELECT indexname, tablename, indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY indexname"),
        ("sequence", """SELECT sequencename, data_type, start_value, min_value, max_value, increment_by, cycle, cache_size
            FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename"""),
        ("view", "SELECT viewname, definition FROM pg_views WHERE schemaname='public' ORDER BY viewname"),
        ("trigger", """SELECT c.relname || '.' || t.tgname, pg_get_triggerdef(t.oid,true), t.tgenabled
            FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND NOT t.tgisinternal ORDER BY c.relname,t.tgname"""),
        ("routine", """SELECT p.oid::regprocedure::text, pg_get_functiondef(p.oid)
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' AND p.prokind IN ('f','p') ORDER BY p.oid::regprocedure::text"""),
        ("policy", """SELECT tablename || '.' || policyname, permissive, roles, cmd, qual, with_check
            FROM pg_policies WHERE schemaname='public' ORDER BY tablename,policyname"""),
    )
    for kind, sql in queries:
        for row in conn.execute(sql):
            objects[f"{kind}:{row[0]}"] = digest(row[1:])
    return objects


def snapshot(conn, baseline: dict[str, Any] | None = None) -> dict[str, Any]:
    tables = {}
    names = [row[0] for row in conn.execute("""SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind IN ('r','p') ORDER BY c.relname""")]
    objects = schema_objects(conn)
    for number, table in enumerate(sorted(baseline["tables"] if baseline else names)):
        if table not in names:
            tables[table] = {"missing_table": True}
            continue
        actual_columns = [row[0] for row in conn.execute("""SELECT a.attname FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=%s
            AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum""", (table,))]
        columns = baseline["tables"][table]["columns"] if baseline else actual_columns
        if missing := set(columns) - set(actual_columns):
            tables[table] = {"missing_columns": sorted(missing)}
            continue
        actual_keys = [row[0] for row in conn.execute("""SELECT a.attname FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace CROSS JOIN LATERAL unnest(k.conkey) WITH ORDINALITY AS x(attnum,ord)
            JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=x.attnum
            WHERE n.nspname='public' AND c.relname=%s AND k.contype='p' ORDER BY x.ord""", (table,))]
        # Old-row projections retain their original identity/relationship
        # columns. Changed PK semantics are proved separately by schema hashes;
        # a new actor PK must never index columns absent from the old projection.
        keys = baseline["tables"][table]["primary_key_columns"] if baseline else actual_keys
        foreign_keys = conn.execute("""SELECT k.conname, pg_get_constraintdef(k.oid,true), k.convalidated FROM pg_constraint k
            JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relname=%s AND k.contype='f' ORDER BY k.conname""", (table,)).fetchall()
        fk_columns = {row[0] for row in conn.execute("""SELECT a.attname FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace CROSS JOIN LATERAL unnest(k.conkey) AS x(attnum)
            JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=x.attnum
            WHERE n.nspname='public' AND c.relname=%s AND k.contype='f'""", (table,))}
        relations = (baseline["tables"][table]["association_columns"] if baseline else
                     [name for name in columns if name in fk_columns or name.endswith(("_id", "_pk", "_ids", "_ids_json"))])
        key_indices = [columns.index(name) for name in keys]
        relation_indices = [columns.index(name) for name in dict.fromkeys(keys + relations)]
        row_hashes, key_hashes, relation_hashes = [], [], []
        # PostgreSQL's lossless text representation under fixed session settings
        # preserves JSON text, bytea, NULL, and float round-trip precision. Type
        # definitions are independently protected by schema object hashes.
        selected = ",".join(f"{quote_identifier(column)}::text" for column in columns)
        with conn.cursor(name=f"assessment_hash_{number}") as cursor:
            cursor.itersize = 256
            cursor.execute(f"SELECT {selected} FROM public.{quote_identifier(table)}")
            for row in cursor:
                row_hashes.append(hashlib.sha256(row_bytes(row)).digest())
                if keys:
                    key_hashes.append(hashlib.sha256(row_bytes([row[i] for i in key_indices])).digest())
                relation_hashes.append(hashlib.sha256(row_bytes([row[i] for i in relation_indices])).digest())
        # Sort fixed-size hashes, not answer bodies; order-independent multiset
        # comparison also handles tables without PKs and duplicate equal rows.
        def aggregate(values):
            result = hashlib.sha256()
            for value in sorted(values):
                update_digest(result, value)
            return result.hexdigest()
        tables[table] = {
            "columns": columns, "row_count": len(row_hashes), "data_sha256": aggregate(row_hashes),
            "primary_key_columns": keys, "primary_key_sha256": aggregate(key_hashes) if keys else None,
            "association_columns": relations, "association_sha256": aggregate(relation_hashes),
            "foreign_key_count": len(foreign_keys), "foreign_keys_sha256": digest(foreign_keys),
            "unvalidated_foreign_key_count": sum(not row[2] for row in foreign_keys),
        }
    sequences = {}
    sequence_names = [row[0] for row in conn.execute("SELECT sequencename FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename")]
    for name in sorted(baseline["sequences"] if baseline else sequence_names):
        sequences[name] = (digest(conn.execute(f"SELECT last_value,is_called FROM public.{quote_identifier(name)}").fetchone())
                           if name in sequence_names else "missing_sequence")
    if baseline:
        objects = {key: objects.get(key, "missing_schema_object") for key in baseline["schema_objects"]}
    return {"tables": tables, "schema_objects": objects, "schema_sha256": digest(objects), "sequences": sequences}


def differences(before, after):
    changes = [f"table:{name}" for name in sorted(set(before["tables"]) | set(after["tables"]))
               if before["tables"].get(name) != after["tables"].get(name)]
    changes.extend(f"schema:{name}" for name in sorted(set(before["schema_objects"]) | set(after["schema_objects"]))
                   if before["schema_objects"].get(name) != after["schema_objects"].get(name))
    changes.extend(f"sequence:{name}" for name in sorted(set(before["sequences"]) | set(after["sequences"]))
                   if before["sequences"].get(name) != after["sequences"].get(name))
    return changes


def apply_assessment_migrations(conn) -> None:
    """Use this release's actual PG DDL, excluding unrelated old startup repairs."""
    from classroom_app.db.postgres_schema import POSTGRES_RUNTIME_COLUMN_DEFINITIONS, POSTGRES_RUNTIME_TABLE_DEFINITIONS
    from classroom_app.db import schema_ai_jobs
    from classroom_app.db.schema_grade_publications import ensure_grade_publication_schema
    for column, definition in POSTGRES_RUNTIME_COLUMN_DEFINITIONS["assignments"].items():
        if column.startswith("assessment_kind"):
            conn.execute(f"ALTER TABLE assignments ADD COLUMN IF NOT EXISTS {quote_identifier(column)} {definition}")
    conn.execute(POSTGRES_RUNTIME_TABLE_DEFINITIONS["assignment_classification_revisions"])
    previous_ready = set(schema_ai_jobs._SCHEMA_READY_ENGINES)
    try:
        schema_ai_jobs._SCHEMA_READY_ENGINES.discard("postgres")
        schema_ai_jobs.ensure_ai_job_schema(conn, engine="postgres")
    finally:
        schema_ai_jobs._SCHEMA_READY_ENGINES = previous_ready
    ensure_grade_publication_schema(conn, engine="postgres")
    if conn.execute("SELECT to_regclass('public.electronic_signatures')").fetchone()[0]:
        from classroom_app.db.postgres import LanSharePostgresConnection
        from classroom_app.db.schema_signature_workflow import migrate_signature_visibility_levels
        migrate_signature_visibility_levels(LanSharePostgresConnection(conn), engine="postgres")
    if conn.execute("SELECT to_regclass('public.agent_tasks')").fetchone()[0]:
        from classroom_app.db.postgres import LanSharePostgresConnection
        from classroom_app.db.schema_agent_ext import ensure_agent_task_extension_schema
        from classroom_app.db.schema_agent_authority import ensure_agent_authority_schema
        from classroom_app.db.schema_agent_model import ensure_agent_model_schema
        from classroom_app.db.schema_agent_request_budget import ensure_agent_request_budget_schema
        from classroom_app.db.schema_agent_interactions import ensure_agent_interactions_schema
        from classroom_app.db.schema_agent_platform_requests import ensure_agent_platform_requests_schema
        from classroom_app.db.schema_agent_children import ensure_agent_children_schema
        adapter = LanSharePostgresConnection(conn)
        ensure_agent_task_extension_schema(adapter, force=True, engine="postgres")
        ensure_agent_authority_schema(adapter)
        ensure_agent_model_schema(adapter)
        ensure_agent_request_budget_schema(adapter)
        ensure_agent_interactions_schema(adapter)
        ensure_agent_platform_requests_schema(adapter)
        ensure_agent_children_schema(adapter)


def rehearse(conn, *, backup: Path, migrate: Callable = apply_assessment_migrations, progress: Callable = print):
    source_hash = file_digest(backup)
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        before = snapshot(conn)
        signature_before = capture_signature_visibility(conn, before["tables"])
        agent_before = capture_agent_authority(conn, before["tables"])
    if not {"assignments", "submissions"}.issubset(before["tables"]):
        raise ValueError("Required restored application tables are missing")
    progress(f"Baseline: {len(before['tables'])} tables, {sum(t['row_count'] for t in before['tables'].values())} rows")
    stages, first_full, idempotency = [], None, []
    for number in (1, 2):
        try:
            with conn.transaction():
                migrate(conn)
            with conn.transaction():
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                preserved = snapshot(conn, baseline=before)
                full = snapshot(conn)
                signature_after = capture_signature_visibility(conn, full["tables"])
                agent_after = capture_agent_authority(conn, full["tables"])
            changes = differences(before, preserved)
            signature_proof = prove_signature_visibility(signature_before, signature_after)
            agent_proof = prove_agent_authority(agent_before, agent_after)
            allowed = set(signature_proof["allowed_differences"]) | set(agent_proof["allowed_differences"])
            unexpected = [change for change in changes if change not in allowed]
            stages.append({"migration": number, "old_field_differences": changes,
                           "unexpected_old_field_differences": unexpected,
                           "signature_visibility_migration": signature_proof,
                           "agent_authority_migration": agent_proof,
                           "old_projection": preserved, "full_schema_sha256": full["schema_sha256"]})
            progress(f"Migration {number}: old-data/schema differences={len(changes)}")
            if number == 1:
                first_full = full
            else:
                idempotency = differences(first_full, full)
        except Exception as exc:
            return {"status": "failed", "engine": "postgres", "failed_migration": number,
                    "error_type": type(exc).__name__, "stages": stages, "before": before,
                    "source_unchanged": file_digest(backup) == source_hash, "deployment_gate_complete": False}
    source_unchanged = file_digest(backup) == source_hash
    passed = source_unchanged and not idempotency and all(
        not item["unexpected_old_field_differences"] and item["signature_visibility_migration"]["status"] == "ok"
        and item["agent_authority_migration"]["status"] == "ok"
        for item in stages)
    return {
        "status": "ok" if passed else "failed", "engine": "postgres", "source_dump_sha256": source_hash,
        "source_unchanged": source_unchanged, "database_preservation_passed": passed,
        "migration_scope": "assessment classification, durable AI/review ledger, grade publication, signature visibility, Agent actor/delegation/key/budget schema",
        "before": before, "stages": stages, "idempotency_differences": idempotency,
        "added_tables": sorted(set(first_full["tables"]) - set(before["tables"])),
        "added_columns": {table: added for table, meta in before["tables"].items()
                          if (added := [name for name in first_full["tables"][table]["columns"] if name not in meta["columns"]])},
        "added_schema_objects": sorted(set(first_full["schema_objects"]) - set(before["schema_objects"])),
        "attachments": {"verified": False, "note": "Database mappings verified; attachment bytes are outside this database dump."},
        "deployment_gate_complete": False,
        "remaining_gates": ["Full application startup migrations outside this release scope are not covered.",
                            "Back up/open representative historical attachment and material files.",
                            "Preserve new production writes since the snapshot before any deployment."],
    }


def run_startup_child(*, cluster_dir: Path, port: int, database: str) -> dict[str, Any]:
    # Called in a fresh Python process for every pass: no runtime ready cache
    # can skip a second migration. Disable dotenv before importing app config.
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["DB_ENGINE"] = "postgres"
    os.environ["DATABASE_URL"] = f"postgresql://rehearsal_admin@127.0.0.1:{port}/{database}"
    os.environ["POSTGRES_POOL_ENABLED"] = "false"
    os.environ["POSTGRES_BACKEND_READY"] = "true"
    os.environ["POSTGRES_STATEMENT_TIMEOUT_MS"] = "120000"
    os.environ["POSTGRES_LOCK_TIMEOUT_MS"] = "5000"
    with connect_offline(cluster_dir=cluster_dir, port=port, database=database):
        pass
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
        from classroom_app.db.schema import init_database
        result = init_database()
    lines = captured.getvalue().splitlines()
    skipped_steps = sum(" step skipped:" in line or " schema step skipped:" in line for line in lines)
    indexes = result.get("performance_indexes", {})
    return {
        "status": "ok" if not skipped_steps and not indexes.get("failed", 0) else "failed",
        "required_table_count": result.get("required_table_count"),
        "present_required_table_count": result.get("present_required_table_count"),
        "runtime_tables": result.get("runtime_tables"),
        "runtime_columns": result.get("runtime_columns"),
        "runtime_constraints": result.get("runtime_constraints"),
        "index_created_or_already_present": indexes.get("created", 0), "index_failed": indexes.get("failed", 0),
        "skipped_step_count": skipped_steps, "captured_log_sha256": digest(lines),
    }


def full_startup_migrator(*, cluster_dir: Path, port: int, database: str, runs: list):
    def migrate(_conn):
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--cluster-dir", str(cluster_dir),
             "--port", str(port), "--database", database, "--run-startup-only"],
            cwd=cluster_dir.parent, capture_output=True, text=True, encoding="utf-8", timeout=240,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        # Child output is a sanitized JSON summary, never the initialization log.
        summary = json.loads(result.stdout)
        runs.append(summary)
        if result.returncode or summary.get("status") != "ok":
            raise RuntimeError("Full initialization did not complete every schema step")
    return migrate


def main(argv=None):
    # Runtime DDL imports config for dialect helpers; this offline CLI has no
    # reason to read the workspace's model keys or production connection file.
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cluster-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--backup-file", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--scope", choices=("incremental", "full-startup"), default="incremental")
    parser.add_argument("--run-startup-only", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.run_startup_only:
        try:
            result = run_startup_child(cluster_dir=args.cluster_dir, port=args.port, database=args.database)
        except Exception as exc:
            result = {"status": "failed", "error_type": type(exc).__name__}
        print(json.dumps(result, ensure_ascii=True, default=str))
        return 0 if result["status"] == "ok" else 1
    if args.backup_file is None or args.report is None:
        parser.error("--backup-file and --report are required")
    if args.report.exists() or args.report.resolve() == args.backup_file.resolve():
        parser.error("Report must be a new path distinct from the backup")
    # Validate input files before connecting; errors are deliberately sanitized.
    try:
        backup = args.backup_file.resolve(strict=True)
        if not backup.is_file():
            raise ValueError("Backup file is required")
        runs = []
        migrate = (full_startup_migrator(cluster_dir=args.cluster_dir, port=args.port, database=args.database, runs=runs)
                   if args.scope == "full-startup" else apply_assessment_migrations)
        with connect_offline(cluster_dir=args.cluster_dir, port=args.port, database=args.database) as conn:
            report = rehearse(conn, backup=backup, migrate=migrate)
        if args.scope == "full-startup":
            report["migration_scope"] = "complete init_database(), fresh isolated Python process for each pass; no app lifespan or workers"
            report["startup_runs"] = runs
            report["remaining_gates"] = ["Back up/open historical attachment and material files.",
                                         "Preserve production writes since this snapshot before any deployment."]
    except Exception as exc:
        report = {"status": "failed", "engine": "postgres", "error_type": type(exc).__name__, "deployment_gate_complete": False}
    with args.report.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(f"Offline PostgreSQL rehearsal: {report['status']}; report: {args.report}")
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
