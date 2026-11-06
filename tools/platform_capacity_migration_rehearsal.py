"""Dump/restore and replay startup migrations using an owned synthetic PG clone.

The source is read under one exported snapshot. Restore never targets the
source: a fresh, ownership-marked database is created and removed afterwards.
No production connection discovery, external integration or worker is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SOURCE_DB = "lanshare_assessment_rehearsal_capacity"
ADDITIVE_COLUMNS = {
    "learning_progress_snapshots": {"dirty_version", "calculated_version"},
    "behavior_write_inbox": {"resolved_at"},
}


def projection_extras(actual_columns, legacy_columns):
    """Only known additive release fields may be excluded from legacy hashes."""
    if set(actual_columns) != set(legacy_columns):
        raise AssertionError("Migration added or removed an unreviewed business table")
    result = {}
    for table, columns in legacy_columns.items():
        missing = set(columns)-set(actual_columns[table])
        extras = set(actual_columns[table])-set(columns)
        if missing or extras-ADDITIVE_COLUMNS.get(table, set()):
            raise AssertionError(f"Unreviewed column change in {table}: missing={sorted(missing)}, added={sorted(extras)}")
        result[table] = sorted(extras)
    return result


def snapshot(conn, *, legacy_columns=None):
    from psycopg import sql
    tables = [row[0] for row in conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
    ).fetchall()]
    column_metadata = conn.execute("""SELECT table_name,column_name,data_type,udt_name,is_nullable,
        column_default,is_identity,identity_generation FROM information_schema.columns
        WHERE table_schema='public' ORDER BY table_name,ordinal_position""").fetchall()
    columns = {table: [] for table in tables}
    for metadata in column_metadata:
        if metadata[0] in columns:
            columns[metadata[0]].append(metadata[1])
    extras = projection_extras(columns, legacy_columns) if legacy_columns is not None else {table: [] for table in tables}
    counts, digests, business_digests = {}, {}, {}
    for table in tables:
        table_sql = sql.Identifier("public", table)
        counts[table] = conn.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(table_sql)).fetchone()[0]
        full = sql.SQL("to_jsonb(t)")
        projected = sql.SQL("to_jsonb(t) - {}::text[]").format(sql.Literal(extras[table])) if extras[table] else full
        for target, row_expr in ((digests, full), (business_digests, projected)):
            target[table] = conn.execute(sql.SQL(
                "SELECT md5(COALESCE(string_agg(md5(({row})::text), '' ORDER BY md5(({row})::text)),'')) FROM {table} t"
            ).format(row=row_expr, table=table_sql)).fetchone()[0]
    constraints = conn.execute("""SELECT t.relname,c.conname,c.contype,pg_get_constraintdef(c.oid,true),c.convalidated
        FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace
        WHERE n.nspname='public' ORDER BY t.relname,c.conname""").fetchall()
    sequences = [row[0] for row in conn.execute(
        "SELECT sequencename FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename"
    ).fetchall()]
    sequence_state = {}
    for sequence in sequences:
        sequence_state[sequence] = list(conn.execute(sql.SQL("SELECT last_value,is_called FROM {}").format(sql.Identifier("public", sequence))).fetchone())
    return {"table_counts": counts, "key_digests": digests, "business_digests": business_digests,
            "table_columns": columns, "column_metadata": list(column_metadata), "constraints": list(constraints),
            "sequence_state": sequence_state}


def emulate_legacy_inbox_and_progress(conn):
    """Only the owned clone is allowed to lose the reviewed additive fields."""
    if conn.execute("SELECT COUNT(*) FROM public.behavior_write_inbox WHERE state NOT IN ('pending','applied')").fetchone()[0]:
        raise AssertionError("The clone contains newer-state receipts; do not rewrite or drop them to imitate old data")
    conn.execute("ALTER TABLE public.learning_progress_snapshots DROP COLUMN IF EXISTS dirty_version")
    conn.execute("ALTER TABLE public.learning_progress_snapshots DROP COLUMN IF EXISTS calculated_version")
    conn.execute("ALTER TABLE public.behavior_write_inbox DROP COLUMN IF EXISTS resolved_at")
    conn.execute("ALTER TABLE public.behavior_write_inbox DROP CONSTRAINT IF EXISTS behavior_write_inbox_state_check")
    conn.execute("ALTER TABLE public.behavior_write_inbox ADD CONSTRAINT behavior_write_inbox_state_check CHECK(state IN ('pending','applied'))")


def verify_inbox_state_contract(conn):
    """Exercise the new CHECK without retaining a synthetic receipt or change."""
    import psycopg
    key = "migration-contract-" + uuid.uuid4().hex
    conn.execute("SAVEPOINT inbox_migration_contract")
    rejected = False
    try:
        conn.execute("""INSERT INTO public.behavior_write_inbox
            (batch_key,batch_id,payload_hash,class_offering_id,user_pk,user_role,display_name,events_json,received_at)
            VALUES(%s,%s,'owned-synthetic-contract',1,1,'student','Synthetic','[]','2026-11-05')""", (key, key))
        for state in ("pending", "applied", "quarantined", "discarded"):
            conn.execute("UPDATE public.behavior_write_inbox SET state=%s WHERE batch_key=%s", (state, key))
        try:
            conn.execute("UPDATE public.behavior_write_inbox SET state='invalid-state-must-fail' WHERE batch_key=%s", (key,))
        except psycopg.errors.CheckViolation:
            rejected = True
    finally:
        conn.execute("ROLLBACK TO SAVEPOINT inbox_migration_contract")
        conn.execute("RELEASE SAVEPOINT inbox_migration_contract")
    if not rejected:
        raise AssertionError("New inbox schema did not reject an unknown state")


def assert_clone_identity(conn, name, token):
    if not re.fullmatch(r"lanshare_capacity_restore_[a-f0-9]{16}", name) or name == SOURCE_DB:
        raise ValueError("Not an owned restore-clone name")
    row = conn.execute("SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=%s", (name,)).fetchone()
    if not row or row[0] != f"synthetic-capacity-migration:{token}":
        raise ValueError("Restore-clone ownership marker mismatch")


def native_environment():
    environment = dict(os.environ)
    for name in list(environment):
        if name.startswith("PG"):
            environment.pop(name, None)
    environment["PGPASSFILE"] = str(ROOT / ".codex-temp/nonexistent-capacity-pgpass")
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


def startup_child(args, output, dsn):
    import psycopg
    from tools.isolated_environment import isolate_sqlite_environment, guard_dotenv_loading
    marker = json.loads((output / "clone-owner.json").read_text(encoding="utf-8"))
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        assert_clone_identity(conn, args.clone_db, marker["token"])
    private_runtime = output / "startup-runtime"
    private_runtime.mkdir(exist_ok=True)
    isolate_sqlite_environment(private_runtime)
    guard_dotenv_loading()
    os.environ.update(DB_ENGINE="postgres", DATABASE_URL=dsn, POSTGRES_BACKEND_READY="true", POSTGRES_POOL_ENABLED="false")
    from classroom_app.database import init_database
    init_database()


def run(args):
    from tools.platform_capacity_probe import establish_boundary
    # Also verifies cluster directory/listener/port and blocks external sockets.
    if args.action != "startup-child" and args.clone_db:
        raise ValueError("The source database is fixed; clone-db is only for the owned child action")
    runtime, fixture = establish_boundary(args)
    cluster = args.cluster_dir.resolve(strict=True)
    if cluster != (ROOT / ".codex-temp/platform-capacity-20261105-pg").resolve():
        raise ValueError("Only this task's disposable PostgreSQL cluster is permitted")
    output = args.output.resolve()
    temporary = (ROOT / ".codex-temp").resolve()
    if output == temporary or not output.is_relative_to(temporary):
        raise ValueError("Rehearsal artifacts must be an owned .codex-temp child")
    output.mkdir(parents=True, exist_ok=True)
    dsn = f"postgresql://rehearsal_admin@127.0.0.1:{args.pg_port}/{args.clone_db or SOURCE_DB}"
    if args.action == "startup-child":
        startup_child(args, output, dsn)
        return
    from tools.platform_capacity_background import validate_fixture
    validate_fixture(args, runtime, fixture)
    import psycopg
    from psycopg import sql
    from classroom_app.db.postgres import close_connection_pool
    # Release validation's app pool; the rehearsal uses explicit native DSNs.
    close_connection_pool()
    suffix, token = uuid.uuid4().hex[:16], uuid.uuid4().hex
    clone = "lanshare_capacity_restore_" + suffix
    clone_dsn = f"postgresql://rehearsal_admin@127.0.0.1:{args.pg_port}/{clone}"
    dump = output / f"synthetic-{suffix}.dump"
    owner_file = output / "clone-owner.json"
    owner_file.write_text(json.dumps({"database": clone, "token": token}), encoding="utf-8")
    report = {"source_database": SOURCE_DB, "clone_database": clone, "source_read_only": True,
              "production_data_accessed": False, "background_workers_started": False,
              "scope": "Native synthetic whole-database restore plus progress/inbox old-schema upgrade; excludes uploaded-file recovery and production-image release acceptance"}
    created = False
    try:
        with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as source:
            source.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            snapshot_id = source.execute("SELECT pg_export_snapshot()").fetchone()[0]
            before = snapshot(source)
            binary = shutil.which("pg_dump")
            if not binary:
                raise RuntimeError("Native pg_dump is unavailable")
            started = time.perf_counter()
            # A schema-only filter omits extension definitions such as pg_trgm
            # while retaining indexes that need them. Dump the whole explicitly
            # synthetic database, matching the native release backup contract.
            subprocess.run([binary, "--dbname", dsn, "--format=custom", "--no-owner", "--no-acl",
                            "--no-password", "--snapshot", snapshot_id, "--file", str(dump)],
                           env=native_environment(), capture_output=True, text=True, encoding="utf-8", check=True, timeout=120)
            report["dump_seconds"] = round(time.perf_counter()-started, 3)
            source.execute("ROLLBACK")
        report.update(dump_file=str(dump), dump_size=dump.stat().st_size, dump_sha256=hashlib.sha256(dump.read_bytes()).hexdigest())
        with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(clone)))
            created = True
            admin.execute(sql.SQL("COMMENT ON DATABASE {} IS {}").format(sql.Identifier(clone), sql.Literal(f"synthetic-capacity-migration:{token}")))
        with psycopg.connect(clone_dsn, connect_timeout=5) as conn:
            assert_clone_identity(conn, clone, token)
            # Whole-database dumps expect template0's standard public schema;
            # extension creation may depend on it before any table is restored.
        binary = shutil.which("pg_restore")
        if not binary:
            raise RuntimeError("Native pg_restore is unavailable")
        started = time.perf_counter()
        restored_process = subprocess.run([binary, "--dbname", clone_dsn, "--no-owner", "--no-acl", "--no-password", "--exit-on-error", str(dump)],
                       env=native_environment(), capture_output=True, text=True, encoding="utf-8", timeout=120)
        (output / "pg-restore.log").write_text(restored_process.stdout+restored_process.stderr, encoding="utf-8")
        restored_process.check_returncode()
        report["restore_seconds"] = round(time.perf_counter()-started, 3)
        with psycopg.connect(clone_dsn, connect_timeout=5) as conn:
            assert_clone_identity(conn, clone, token)
            restored = snapshot(conn)
            assert before == restored, "Restored rows, key content digests or sequence state differ from the exported snapshot"
            # Full restore is checked before changing the clone. Capture a new
            # complete legacy baseline only after removing the reviewed fields
            # and restoring the original two-state CHECK; every old value stays.
            emulate_legacy_inbox_and_progress(conn)
            legacy = snapshot(conn)
            assert legacy["table_counts"] == before["table_counts"] and legacy["sequence_state"] == before["sequence_state"]
            assert "resolved_at" not in legacy["table_columns"]["behavior_write_inbox"]
            old_check = [item[3] for item in legacy["constraints"] if item[0] == "behavior_write_inbox" and item[1] == "behavior_write_inbox_state_check"]
            assert len(old_check) == 1 and "quarantined" not in old_check[0]
        (output / "legacy-business-baseline.json").write_text(json.dumps(legacy, ensure_ascii=False, indent=2), encoding="utf-8")
        report.update(restore_matches_snapshot=True, restored_table_count=len(before["table_counts"]),
                      retained_inbox_receipts=before["table_counts"].get("behavior_write_inbox", 0))
        startup_snapshots = []
        for index in range(2):
            command = [sys.executable, str(Path(__file__).resolve()), "startup-child", "--runtime", str(runtime),
                       "--cluster-dir", str(cluster), "--pg-port", str(args.pg_port), "--users", str(args.users),
                       "--http-port", str(args.http_port), "--output", str(output), "--clone-db", clone]
            started = time.perf_counter()
            result = subprocess.run(command, env=native_environment(), capture_output=True, text=True, encoding="utf-8", timeout=120)
            (output / f"startup-{index+1}.log").write_text(result.stdout+result.stderr, encoding="utf-8")
            if result.returncode:
                raise RuntimeError(f"Startup replay {index+1} failed; inspect its owned rehearsal log")
            with psycopg.connect(clone_dsn, connect_timeout=5) as conn:
                current = snapshot(conn, legacy_columns=legacy["table_columns"])
                versions = conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' "
                                        "AND table_name='learning_progress_snapshots' AND column_name IN ('dirty_version','calculated_version')").fetchall()
                assert len(versions) == 2
                assert "resolved_at" in current["table_columns"]["behavior_write_inbox"]
                assert legacy["table_counts"] == current["table_counts"] and legacy["key_digests"] == current["business_digests"], "Migration changed existing business fields or rows"
                assert legacy["sequence_state"] == current["sequence_state"], "Migration changed existing sequence state"
                new_check = [item[3] for item in current["constraints"] if item[0] == "behavior_write_inbox" and item[1] == "behavior_write_inbox_state_check"]
                assert len(new_check) == 1 and all(state in new_check[0] for state in ("pending", "applied", "quarantined", "discarded"))
                verify_inbox_state_contract(conn)
                startup_snapshots.append(current)
            report[f"startup_{index+1}_seconds"] = round(time.perf_counter()-started, 3)
        assert startup_snapshots[0] == startup_snapshots[1], "Second fresh-process startup was not idempotent"
        report.update(progress_version_upgrade=True, inbox_resolved_at_upgrade=True, inbox_four_state_check_upgrade=True,
                      inbox_unknown_state_rejected=True, all_legacy_fields_preserved=True, business_rows_preserved=True, fresh_process_startup_idempotent=True,
                      business_tables_verified=len(before["key_digests"]), sequence_count=len(before["sequence_state"]), status="ok")
    except BaseException as exc:
        report.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        if created:
            with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as admin:
                assert_clone_identity(admin, clone, token)
                admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(clone)))
            report["owned_clone_removed"] = True
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("rehearse", "startup-child"))
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--cluster-dir", type=Path, required=True)
    parser.add_argument("--pg-port", type=int, required=True)
    parser.add_argument("--http-port", type=int, default=18767)
    parser.add_argument("--users", type=int, default=200)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--clone-db", default="", help=argparse.SUPPRESS)
    run(parser.parse_args())
