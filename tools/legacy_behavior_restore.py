"""Recover a sealed behavior queue as a private append-only evidence ledger."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from itertools import zip_longest
import ipaddress
import json
import os
from pathlib import Path
import re
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.legacy_behavior_restore_archive import HEX_SHA, RestoreError, SealedArchive, open_sealed_archive, require


NAMESPACE = "legacy_behavior_recovery"
LOCK_ID = 742819037413


@dataclass(frozen=True, slots=True)
class DatabaseIdentity:
    system_identifier: str
    database: str
    user: str
    server_address: str


def _database_identity(conn) -> DatabaseIdentity:
    row = conn.execute("""
        SELECT system_identifier::text, pg_catalog.current_database(), current_user::text,
               pg_catalog.host(pg_catalog.inet_server_addr()), pg_catalog.pg_is_in_recovery(),
               pg_catalog.current_setting('server_version_num')::integer
        FROM pg_catalog.pg_control_system()
        """).fetchone()
    require(row is not None and row[4] is False and 160000 <= row[5] < 170000, "database_identity_unavailable")
    return DatabaseIdentity(*row[:4])


def _schema_exists(conn) -> bool:
    return conn.execute(
        "SELECT pg_catalog.to_regnamespace('legacy_behavior_recovery') IS NOT NULL"
    ).fetchone()[0]


def _create_evidence_tables(conn) -> None:
    conn.execute("CREATE SCHEMA legacy_behavior_recovery")
    conn.execute("REVOKE ALL ON SCHEMA legacy_behavior_recovery FROM PUBLIC")
    conn.execute("""
        CREATE TABLE legacy_behavior_recovery.archive_manifest (
            archive_sha256 TEXT PRIMARY KEY CHECK (length(archive_sha256) = 64),
            byte_count BIGINT NOT NULL CHECK (byte_count > 0),
            record_count INTEGER NOT NULL CHECK (record_count BETWEEN 1 AND 20000),
            event_count INTEGER NOT NULL CHECK (event_count >= 0),
            barrier_count INTEGER NOT NULL CHECK (barrier_count BETWEEN 0 AND record_count),
            source_identity_json TEXT NOT NULL,
            proof_sha256 TEXT NOT NULL CHECK (length(proof_sha256) = 64),
            operator_name TEXT NOT NULL,
            reason TEXT NOT NULL,
            imported_at TIMESTAMPTZ NOT NULL DEFAULT pg_catalog.clock_timestamp()
        )
        """)
    conn.execute("""
        CREATE TABLE legacy_behavior_recovery.archived_batches (
            archive_sha256 TEXT NOT NULL REFERENCES legacy_behavior_recovery.archive_manifest (archive_sha256),
            queue_ordinal INTEGER NOT NULL CHECK (queue_ordinal BETWEEN 0 AND 19999),
            record_sha256 TEXT NOT NULL CHECK (length(record_sha256) = 64),
            event_count INTEGER NOT NULL CHECK (event_count >= 0),
            is_barrier BOOLEAN NOT NULL,
            raw_record BYTEA NOT NULL CHECK (octet_length(raw_record) > 0),
            PRIMARY KEY (archive_sha256, queue_ordinal)
        )
        """)
    conn.execute("REVOKE ALL ON ALL TABLES IN SCHEMA legacy_behavior_recovery FROM PUBLIC")


def _verify_private_tables(conn) -> None:
    namespace = conn.execute("""
        SELECT n.nspowner = current_user::pg_catalog.regrole::oid,
               NOT EXISTS (
                   SELECT 1 FROM pg_catalog.aclexplode(COALESCE(n.nspacl, pg_catalog.acldefault('n', n.nspowner))) a
                   WHERE a.grantee <> n.nspowner
               )
        FROM pg_catalog.pg_namespace n WHERE n.nspname = %s
        """, (NAMESPACE,)).fetchone()
    require(namespace == (True, True), "recovery_namespace_not_private")
    relations = conn.execute("""
        SELECT c.relname, c.relkind, c.relpersistence, c.relrowsecurity, c.relhasrules, c.relispartition,
               c.relowner = current_user::pg_catalog.regrole::oid,
               NOT EXISTS (
                   SELECT 1 FROM pg_catalog.aclexplode(COALESCE(c.relacl, pg_catalog.acldefault('r', c.relowner))) a
                   WHERE a.grantee <> c.relowner
               )
        FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relname IN ('archive_manifest', 'archived_batches')
        ORDER BY c.relname
        """, (NAMESPACE,)).fetchall()
    require(relations == [
        ("archive_manifest", "r", "p", False, False, False, True, True),
        ("archived_batches", "r", "p", False, False, False, True, True),
    ], "unexpected_recovery_relations")


def _manifest_values(archive: SealedArchive) -> tuple:
    return (archive.sha256, archive.byte_count, len(archive.rows), archive.event_count, archive.barrier_count,
            archive.source_identity_json, archive.proof_sha256)


def _verified_rows(conn, archive: SealedArchive) -> None:
    manifest = conn.execute("""
        SELECT archive_sha256, byte_count, record_count, event_count, barrier_count, source_identity_json, proof_sha256
        FROM ONLY legacy_behavior_recovery.archive_manifest LIMIT 2
        """).fetchall()
    require(manifest == [_manifest_values(archive)], "existing_recovery_manifest_mismatch")
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT archive_sha256, queue_ordinal, record_sha256, event_count, is_barrier, raw_record
            FROM ONLY legacy_behavior_recovery.archived_batches ORDER BY queue_ordinal
            LIMIT %s
            """, (len(archive.rows) + 1,))
        for stored, original in zip_longest(cursor, archive.rows):
            require(stored is not None and original is not None
                    and stored == (archive.sha256, original.ordinal, original.record_sha256,
                                   original.event_count, original.is_barrier, original.raw_record),
                    "stored_archive_record_mismatch")


def _append_rows(conn, archive: SealedArchive, operator: str, reason: str) -> None:
    conn.execute("""
        INSERT INTO legacy_behavior_recovery.archive_manifest (
            archive_sha256, byte_count, record_count, event_count, barrier_count, source_identity_json,
            proof_sha256, operator_name, reason
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (*_manifest_values(archive), operator, reason))
    with conn.cursor() as cursor, cursor.copy("""
        COPY legacy_behavior_recovery.archived_batches (
            archive_sha256, queue_ordinal, record_sha256, event_count, is_barrier, raw_record
        ) FROM STDIN
        """) as copy:
        for row in archive.rows:
            copy.write_row((archive.sha256, row.ordinal, row.record_sha256, row.event_count,
                            row.is_barrier, row.raw_record))


def restore_archive(conn, archive: SealedArchive, expected_database: DatabaseIdentity, *,
                    operator: str, reason: str) -> dict:
    require(0 < len(operator.strip()) <= 100 and 0 < len(reason.strip()) <= 300, "operator_and_reason_required")
    require(conn.autocommit and int(conn.info.transaction_status) == 0, "clean_owned_connection_required")
    with conn.transaction():
        conn.execute("SET LOCAL search_path = pg_catalog")
        conn.execute("SET LOCAL lock_timeout = '2s'")
        conn.execute("SET LOCAL statement_timeout = '25s'")
        require(_database_identity(conn) == expected_database, "unexpected_database_identity")
        conn.execute("SELECT pg_catalog.pg_advisory_xact_lock(%s::bigint)", (LOCK_ID,))
        existing = _schema_exists(conn)
        if not existing:
            _create_evidence_tables(conn)
        _verify_private_tables(conn)
        if not existing:
            _append_rows(conn, archive, operator, reason)
        _verified_rows(conn, archive)
    return {"status": "already_verified" if existing else "archived", **archive.aggregate(),
            "inserted_batches": 0 if existing else len(archive.rows), "proof_sha256": archive.proof_sha256}


def _connect_configured_database(expected: DatabaseIdentity):
    import psycopg
    from psycopg.conninfo import conninfo_to_dict

    require(not any(key.startswith("PG") for key in os.environ), "ambient_postgres_parameters_forbidden")
    dsn = os.environ.get("DATABASE_URL", "")
    require(dsn.startswith(("postgresql://", "postgres://")) and len(dsn) <= 4096, "explicit_container_dsn_required")
    fields = conninfo_to_dict(dsn)
    require(set(fields) <= {"host", "port", "dbname", "user", "password", "sslmode"}
            and fields.get("host") == "postgres" and fields.get("port", "5432") == "5432"
            and fields.get("dbname") == expected.database and fields.get("user") == expected.user,
            "container_dsn_binding_mismatch")
    return psycopg.connect(dsn, autocommit=True, connect_timeout=4, application_name="lanshare-legacy-archive-restore")


def arguments(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("inspect", "apply"))
    p.add_argument("--archive", type=Path, required=True)
    p.add_argument("--expected-sha256", required=True)
    p.add_argument("--expected-records", type=int, required=True)
    p.add_argument("--expected-events", type=int, required=True)
    p.add_argument("--expected-proof-sha256")
    p.add_argument("--expected-source-container")
    p.add_argument("--expected-system-identifier")
    p.add_argument("--expected-database")
    p.add_argument("--expected-user")
    p.add_argument("--expected-server-address")
    p.add_argument("--acknowledge-write", action="store_true")
    p.add_argument("--operator")
    p.add_argument("--reason")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = arguments(argv)
    try:
        archive = open_sealed_archive(args.archive, expected_sha256=args.expected_sha256,
                                      expected_records=args.expected_records, expected_events=args.expected_events)
        if args.action == "inspect":
            result = {"status": "verified_offline", **archive.aggregate(), "proof_sha256": archive.proof_sha256}
        else:
            require(os.geteuid() == 0 and args.acknowledge_write and args.operator and args.reason
                    and args.expected_source_container and HEX_SHA.fullmatch(args.expected_source_container)
                    and archive.proof_sha256 == args.expected_proof_sha256
                    and json.loads(archive.source_identity_json)["container_id"] == args.expected_source_container,
                    "explicit_archive_write_binding_required")
            require(args.expected_system_identifier and re.fullmatch(r"[1-9][0-9]{0,19}", args.expected_system_identifier)
                    and args.expected_database and args.expected_user and args.expected_server_address,
                    "explicit_database_identity_required")
            address = str(ipaddress.ip_address(args.expected_server_address))
            identity = DatabaseIdentity(args.expected_system_identifier, args.expected_database, args.expected_user, address)
            with _connect_configured_database(identity) as conn:
                result = restore_archive(conn, archive, identity, operator=args.operator, reason=args.reason)
    except Exception as error:
        diagnostic = str(error) if type(error) is RestoreError else type(error).__name__
        print(json.dumps({"status": "failed", "diagnostic": diagnostic}), flush=True)
        return 1
    print(json.dumps(result), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
