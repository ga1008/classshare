"""Reconcile reviewed daily-point credits against a sealed legacy archive."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.legacy_behavior_restore import DatabaseIdentity, _connect_configured_database, _database_identity, _verify_private_tables
from tools.legacy_behavior_restore_archive import HEX_SHA, MAX_RECORD_BYTES, MAX_RECORDS, RestoreError, _archive_row, _parse_json, require


PLAN_KIND = "lanshare-legacy-behavior-daily-points-v1"
EVIDENCE_KINDS = {"server_session_bounds", "same_process_fifo_bounds"}
MAX_PLAN_BYTES = 64 << 10
MAX_CREDITS = 100
# Match the currently deployed daily-active writer. Its complete check/insert
# runs inside behavior_inbox_service._drain_once while this lock is held.
BEHAVIOR_APPLY_LOCK = (0x42485652, 2)
DAILY_POINTS = 10
HEARTBEATS = {"presence_heartbeat", "page_presence", "heartbeat"}
CHINA_TIME = timezone(timedelta(hours=8))


@dataclass(frozen=True, slots=True)
class CreditProof:
    student_id: int
    active_date: date
    queue_index: int
    evidence_kind: str

    @property
    def key(self):
        return self.student_id, self.active_date


@dataclass(frozen=True, slots=True)
class CreditPlan:
    archive_sha256: str
    sha256: str
    records: tuple[CreditProof, ...]


def load_plan(raw: bytes, *, expected_sha256: str) -> CreditPlan:
    require(type(expected_sha256) is str and HEX_SHA.fullmatch(expected_sha256)
            and 0 < len(raw) <= MAX_PLAN_BYTES
            and hashlib.sha256(raw).hexdigest() == expected_sha256, "point_proof_digest_mismatch")
    document = _parse_json(raw)
    require(type(document) is dict and set(document) == {"kind", "archive_sha256", "records"}
            and document["kind"] == PLAN_KIND and type(document["archive_sha256"]) is str
            and HEX_SHA.fullmatch(document["archive_sha256"]) and type(document["records"]) is list
            and 0 < len(document["records"]) <= MAX_CREDITS, "invalid_point_proof_plan")
    proofs, keys, ordinals = [], set(), set()
    for row in document["records"]:
        require(type(row) is dict and set(row) == {"student_id", "active_date", "queue_index", "evidence_kind"}
                and type(row["student_id"]) is int and 0 < row["student_id"] <= 2**63 - 1
                and type(row["queue_index"]) is int and 0 <= row["queue_index"] < MAX_RECORDS
                and type(row["active_date"]) is str and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", row["active_date"])
                and type(row["evidence_kind"]) is str and row["evidence_kind"] in EVIDENCE_KINDS,
                "invalid_point_proof_record")
        try:
            day = date.fromisoformat(row["active_date"])
        except ValueError:
            raise RestoreError("invalid_point_proof_date") from None
        proof = CreditProof(row["student_id"], day, row["queue_index"], row["evidence_kind"])
        require(proof.key not in keys and proof.queue_index not in ordinals, "duplicate_point_proof")
        keys.add(proof.key)
        ordinals.add(proof.queue_index)
        proofs.append(proof)
    return CreditPlan(document["archive_sha256"], expected_sha256, tuple(sorted(proofs, key=lambda p: p.key)))


def _qualifies(events: list[dict]) -> bool:
    # Preserve the old rule: a student needs at least one event the legacy
    # batch would have logged. A malformed heartbeat would abort the batch.
    loggable = False
    for event in events:
        action = str(event.get("action_type") or "page_action").strip().lower().replace("-", "_").replace(" ", "_")[:64]
        if action not in HEARTBEATS:
            loggable = True
            continue
        payload = event.get("payload")
        if isinstance(payload, dict):
            try:
                int(payload.get("idle_seconds") or 0)
            except (TypeError, ValueError, OverflowError):
                return False
        loggable |= bool(str(event.get("summary_text") or "").strip())
    return loggable


def _session_day(value) -> date | None:
    if not isinstance(value, str) or "T" not in value:
        return None
    try:
        stamp = datetime.fromisoformat(value)
        return (stamp.astimezone(CHINA_TIME) if stamp.tzinfo is not None else stamp).date()
    except (ValueError, OverflowError):
        return None


def _verified_archive_rows(conn, plan: CreditPlan, *, expected_source_container: str, apply: bool) -> dict[int, str]:
    lock = " FOR SHARE" if apply else ""
    manifest = conn.execute("""
        SELECT record_count, source_identity_json FROM ONLY legacy_behavior_recovery.archive_manifest
        WHERE archive_sha256 = %s
        """ + lock, (plan.archive_sha256,)).fetchone()
    require(manifest is not None and 0 < manifest[0] <= MAX_RECORDS
            and max(row.queue_index for row in plan.records) < manifest[0], "point_archive_manifest_mismatch")
    identity = _parse_json(manifest[1].encode("ascii"))
    require(type(identity) is dict and identity.get("container_id") == expected_source_container,
            "point_archive_source_mismatch")
    rows = conn.execute("""
        SELECT queue_ordinal, record_sha256, event_count, is_barrier, raw_record
        FROM ONLY legacy_behavior_recovery.archived_batches
        WHERE archive_sha256 = %s AND queue_ordinal = ANY(%s::integer[])
          AND pg_catalog.octet_length(raw_record) BETWEEN 1 AND %s
        ORDER BY queue_ordinal
        """ + lock, (plan.archive_sha256, [proof.queue_index for proof in plan.records], MAX_RECORD_BYTES)).fetchall()
    stored = {row[0]: row[1:] for row in rows}
    require(len(stored) == len(rows) and set(stored) == {proof.queue_index for proof in plan.records}, "point_archive_row_missing")
    digests = {}
    for proof in plan.records:
        digest, event_count, is_barrier, raw = stored[proof.queue_index]
        require(type(raw) is bytes and 0 < len(raw) <= MAX_RECORD_BYTES and raw.endswith(b"\n"), "invalid_point_archive_row")
        archived = _archive_row(raw, proof.queue_index)
        require((digest, event_count, is_barrier) == (archived.record_sha256, archived.event_count, archived.is_barrier)
                and not is_barrier, "point_archive_row_mismatch")
        item = _parse_json(raw)
        require(item["user_role"] == "student" and item["user_pk"] == proof.student_id and item["class_offering_id"] > 0
                and _qualifies(item["events"]), "point_archive_event_not_eligible")
        if proof.evidence_kind == "server_session_bounds":
            require(_session_day(item["session_started_at"]) == proof.active_date, "point_session_date_mismatch")
        digests[proof.queue_index] = digest
    return digests


def _verify_active_students(conn, plan: CreditPlan, *, apply: bool) -> None:
    student_ids = sorted({proof.student_id for proof in plan.records})
    # SHARE holds the validated active status through commit. It stays
    # compatible with the worker's foreign-key KEY SHARE requests.
    lock = " FOR SHARE" if apply else ""
    rows = conn.execute("""
        SELECT id, enrollment_status FROM ONLY public.students
        WHERE id = ANY(%s::bigint[]) ORDER BY id
        """ + lock, (student_ids,)).fetchall()
    require(rows == [(student_id, "active") for student_id in student_ids], "point_student_missing_or_inactive")


def _read_ledger(conn, plan: CreditPlan) -> dict:
    values = ",".join(["(%s::bigint,%s::date)"] * len(plan.records))
    rows = conn.execute(f"""
        WITH candidates(student_id, day) AS (VALUES {values})
        SELECT p.student_id, p.day, l.id, l.delta FROM candidates p
        LEFT JOIN LATERAL (
            SELECT id, delta FROM ONLY public.student_point_ledger
            WHERE student_id = p.student_id AND reason_kind = 'daily_active' AND reason_ref = p.day::text
            ORDER BY id LIMIT 2
        ) l ON true ORDER BY p.student_id, p.day, l.id
        """, tuple(value for proof in plan.records for value in proof.key)).fetchall()
    grouped = {proof.key: [] for proof in plan.records}
    for student, day, ledger_id, delta in rows:
        if ledger_id is not None:
            grouped[(student, day)].append((ledger_id, delta))
    return grouped


def _audit_exists(conn) -> bool:
    row = conn.execute("""
        SELECT c.relkind = 'r' AND c.relpersistence = 'p' AND NOT c.relrowsecurity
               AND NOT c.relhasrules AND NOT c.relispartition
               AND c.relowner = current_user::pg_catalog.regrole::oid
               AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_trigger t WHERE t.tgrelid = c.oid AND NOT t.tgisinternal)
               AND NOT EXISTS (
                   SELECT 1 FROM pg_catalog.aclexplode(COALESCE(c.relacl, pg_catalog.acldefault('r', c.relowner))) a
                   WHERE a.grantee <> c.relowner
               )
        FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'legacy_behavior_recovery' AND c.relname = 'point_credits'
        """).fetchone()
    require(row in (None, (True,)), "point_audit_not_private")
    return row is not None


def _verify_audit(conn, plan: CreditPlan, digests: dict[int, str], ledger: dict) -> int:
    values = ",".join(["(%s::bigint,%s::date)"] * len(plan.records))
    rows = conn.execute(f"""
        WITH candidates(student_id, day) AS (VALUES {values})
        SELECT a.student_id, a.active_date, a.ledger_id, a.archive_sha256, a.queue_ordinal,
               a.record_sha256, a.plan_sha256, a.evidence_kind
        FROM ONLY legacy_behavior_recovery.point_credits a JOIN candidates p
          ON a.student_id = p.student_id AND a.active_date = p.day
        """, tuple(value for proof in plan.records for value in proof.key)).fetchall()
    proofs = {proof.key: proof for proof in plan.records}
    require(len({row[:2] for row in rows}) == len(rows), "duplicate_point_audit")
    for student, day, ledger_id, archive, ordinal, digest, plan_sha, kind in rows:
        require(ledger[(student, day)] == [(ledger_id, DAILY_POINTS)], "point_audit_ledger_mismatch")
        if plan_sha == plan.sha256:
            proof = proofs[(student, day)]
            require((archive, ordinal, digest, kind) == (plan.archive_sha256, proof.queue_index,
                    digests[proof.queue_index], proof.evidence_kind), "point_audit_proof_mismatch")
    return len(rows)


def _create_audit(conn) -> None:
    conn.execute("""
        CREATE TABLE legacy_behavior_recovery.point_credits (
            student_id BIGINT NOT NULL,
            active_date DATE NOT NULL,
            ledger_id BIGINT NOT NULL UNIQUE,
            archive_sha256 TEXT NOT NULL,
            queue_ordinal INTEGER NOT NULL,
            record_sha256 TEXT NOT NULL CHECK (length(record_sha256) = 64),
            plan_sha256 TEXT NOT NULL CHECK (length(plan_sha256) = 64),
            evidence_kind TEXT NOT NULL CHECK (evidence_kind IN ('server_session_bounds','same_process_fifo_bounds')),
            operator_name TEXT NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT pg_catalog.clock_timestamp(),
            PRIMARY KEY (student_id, active_date),
            FOREIGN KEY (archive_sha256, queue_ordinal)
                REFERENCES legacy_behavior_recovery.archived_batches (archive_sha256, queue_ordinal)
        )
        """)
    conn.execute("REVOKE ALL ON TABLE legacy_behavior_recovery.point_credits FROM PUBLIC")
    require(_audit_exists(conn), "point_audit_not_private")


def _verify_ledger_relation(conn) -> None:
    safe_ledger = conn.execute("""
        SELECT c.relkind = 'r' AND c.relpersistence = 'p' AND NOT c.relrowsecurity AND NOT c.relhasrules
               AND NOT c.relispartition
               AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_trigger t WHERE t.tgrelid = c.oid AND NOT t.tgisinternal)
        FROM pg_catalog.pg_class c WHERE c.oid = 'public.student_point_ledger'::pg_catalog.regclass
        """).fetchone()
    require(safe_ledger == (True,), "unexpected_point_ledger_relation")


def _credit_missing(conn, plan: CreditPlan, missing: list[CreditProof], digests: dict[int, str], operator: str) -> None:
    # A single bounded statement owns every ledger and matching audit row.
    # An unexpected RETURNING row causes the outer transaction to roll back.
    values = ",".join(["(%s::bigint,%s::date,%s::integer,%s::text,%s::text)"] * len(missing))
    rows = conn.execute(f"""
        WITH proven(student_id, day, queue_ordinal, record_sha256, evidence_kind) AS (VALUES {values}),
        credited AS (
            INSERT INTO public.student_point_ledger (student_id, delta, reason_kind, reason_ref, note, created_at)
            SELECT student_id, %s, 'daily_active', day::text, '历史行为核验补记 ' || day::text,
                   pg_catalog.to_char(pg_catalog.clock_timestamp() AT TIME ZONE 'Asia/Shanghai', 'YYYY-MM-DD"T"HH24:MI:SS')
            FROM proven
            RETURNING id, student_id, delta, reason_kind, reason_ref
        )
        INSERT INTO legacy_behavior_recovery.point_credits (
            student_id, active_date, ledger_id, archive_sha256, queue_ordinal, record_sha256,
            plan_sha256, evidence_kind, operator_name
        ) SELECT p.student_id, p.day, c.id, %s, p.queue_ordinal, p.record_sha256, %s, p.evidence_kind, %s
        FROM proven p JOIN credited c ON c.student_id = p.student_id AND c.reason_ref = p.day::text
        WHERE c.delta = %s AND c.reason_kind = 'daily_active'
        RETURNING student_id, active_date
        """, tuple(value for proof in missing for value in (*proof.key, proof.queue_index,
              digests[proof.queue_index], proof.evidence_kind)) + (DAILY_POINTS, plan.archive_sha256, plan.sha256,
              operator, DAILY_POINTS)).fetchall()
    require(len(rows) == len(missing) and set(rows) == {proof.key for proof in missing}, "point_ledger_insert_mismatch")


def reconcile_points(conn, plan: CreditPlan, expected_database: DatabaseIdentity, *, expected_source_container: str,
                     apply: bool = False, operator: str = "") -> dict:
    require(isinstance(plan, CreditPlan) and 0 < len(plan.records) <= MAX_CREDITS, "invalid_point_proof_plan")
    require(type(expected_source_container) is str and HEX_SHA.fullmatch(expected_source_container), "point_source_binding_required")
    require(conn.autocommit and int(conn.info.transaction_status) == 0, "clean_owned_connection_required")
    require(not apply or (0 < len(operator.strip()) <= 100 and "\0" not in operator), "point_operator_required")
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED" if apply else
                     "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL search_path = pg_catalog")
        conn.execute("SET LOCAL DateStyle = 'ISO, YMD'")
        conn.execute("SET LOCAL lock_timeout = '2s'")
        conn.execute("SET LOCAL statement_timeout = '10s'")
        require(_database_identity(conn) == expected_database, "unexpected_database_identity")
        _verify_private_tables(conn)
        digests = _verified_archive_rows(conn, plan, expected_source_container=expected_source_container, apply=apply)
        _verify_active_students(conn, plan, apply=apply)
        if apply:
            conn.execute("SELECT pg_catalog.pg_advisory_xact_lock(%s::integer, %s::integer)", BEHAVIOR_APPLY_LOCK)
        _verify_ledger_relation(conn)
        ledger = _read_ledger(conn, plan)
        audit_exists = _audit_exists(conn)
        audited = _verify_audit(conn, plan, digests, ledger) if audit_exists else 0
        missing = [proof for proof in plan.records if not ledger[proof.key]]
        already = sum(len(rows) == 1 and rows[0][1] == DAILY_POINTS for rows in ledger.values())
        conflicts = len(plan.records) - len(missing) - already
        credited = 0
        if apply:
            require(not conflicts, "point_ledger_conflict")
            if missing and not audit_exists:
                _create_audit(conn)
            if missing:
                _credit_missing(conn, plan, missing, digests, operator.strip())
            credited = len(missing)
    return {"status": "applied" if credited else "already_credited" if apply else "verified_read_only",
            "archive_sha256": plan.archive_sha256, "plan_sha256": plan.sha256, "eligible_keys": len(plan.records),
            "missing_before": len(missing), "already_credited_before": already, "existing_recovery_audits": audited,
            "conflicting_keys": conflicts, "new_credits": credited, "points_credited": DAILY_POINTS * credited}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "apply"))
    parser.add_argument("--proof-plan", type=Path, required=True)
    parser.add_argument("--expected-plan-sha256", required=True)
    for name in ("expected-source-container", "expected-system-identifier", "expected-database",
                 "expected-user", "expected-server-address"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--acknowledge-write", action="store_true")
    parser.add_argument("--operator", default="")
    args = parser.parse_args(argv)
    try:
        apply = args.action == "apply"
        require(os.name == "posix" and os.geteuid() == 0 and (not apply or args.acknowledge_write), "point_operator_boundary_required")
        require(not apply or (0 < len(args.operator.strip()) <= 100 and "\0" not in args.operator), "point_operator_required")
        require(re.fullmatch(r"[1-9][0-9]{0,19}", args.expected_system_identifier)
                and HEX_SHA.fullmatch(args.expected_source_container), "explicit_database_identity_required")
        identity = DatabaseIdentity(args.expected_system_identifier, args.expected_database, args.expected_user,
                                    str(ipaddress.ip_address(args.expected_server_address)))
        with args.proof_plan.open("rb") as stream:
            plan = load_plan(stream.read(MAX_PLAN_BYTES + 1), expected_sha256=args.expected_plan_sha256)
        with _connect_configured_database(identity) as conn:
            result = reconcile_points(conn, plan, identity, expected_source_container=args.expected_source_container,
                                      apply=apply, operator=args.operator)
    except Exception as error:
        diagnostic = str(error) if type(error) is RestoreError else type(error).__name__
        print(json.dumps({"status": "failed", "diagnostic": diagnostic}), flush=True)
        return 1
    print(json.dumps(result), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
