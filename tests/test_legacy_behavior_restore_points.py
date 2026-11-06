from contextlib import contextmanager
from copy import deepcopy
from datetime import date
import hashlib
import json
from types import SimpleNamespace
import unittest

from tools import legacy_behavior_restore_points as points


ARCHIVE = "a" * 64
SOURCE = "b" * 64
DAY = date(2026, 11, 6)
IDENTITY = points.DatabaseIdentity("12345", "offline", "tester", "127.0.0.1")
PAGE = {"action_type": "page_view", "summary_text": ""}
HEARTBEAT = {"action_type": "presence-heartbeat", "summary_text": " \t ", "payload": {"idle_seconds": 2}}


def make_proof(index=0, student=101, day=DAY, kind="same_process_fifo_bounds"):
    return {"queue_index": index, "student_id": student, "active_date": day.isoformat(), "evidence_kind": kind}


def raw_plan(*records):
    return json.dumps({"kind": points.PLAN_KIND, "archive_sha256": ARCHIVE, "records": list(records)}).encode("ascii")


def plan(*records):
    raw = raw_plan(*records)
    return points.load_plan(raw, expected_sha256=hashlib.sha256(raw).hexdigest())


def archive(index=0, student=101, *, role="student", events=None, session=None):
    events = [PAGE] if events is None else events
    data = {"queue_index": index, "class_offering_id": 7, "user_pk": student, "user_role": role,
            "display_name": "private synthetic value", "page_key": "classroom", "events": events,
            "session_started_at": session, "is_barrier": False, "future_present": False}
    raw = (json.dumps(data) + "\n").encode("ascii")
    return index, hashlib.sha256(raw).hexdigest(), len(events), False, raw


class Result:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class RecordingPG:
    """Model committed facts and check ordering. Native tests exercise the SQL."""

    autocommit = True
    info = SimpleNamespace(transaction_status=0)

    def __init__(self, *archived):
        self.archived = list(archived or [archive()])
        self.students = {101: "active", 102: "active"}
        self.ledger, self.audit = {}, {}
        self.audit_exists = False
        self.private = True
        self.identity = IDENTITY
        self.after_lock = None
        self.failed_write = False
        self.commands = []
        self.locked = False

    @contextmanager
    def transaction(self):
        before = deepcopy((self.ledger, self.audit, self.audit_exists))
        self.locked = False
        try:
            yield
        except BaseException:
            self.ledger, self.audit, self.audit_exists = before
            raise

    def execute(self, sql, params=()):
        statement = " ".join(sql.split())
        self.commands.append(statement)
        if statement.startswith("SET ") or statement.startswith("REVOKE "):
            return Result()
        if "pg_control_system()" in statement:
            db = self.identity
            return Result([(db.system_identifier, db.database, db.user, db.server_address, False, 160005)])
        if "n.nspowner = current_user" in statement:
            return Result([(True, True)])
        if "c.relname IN ('archive_manifest', 'archived_batches')" in statement:
            return Result([(name, "r", "p", False, False, False, True, True)
                           for name in ("archive_manifest", "archived_batches")])
        if "FROM ONLY legacy_behavior_recovery.archive_manifest" in statement:
            return Result([(len(self.archived), json.dumps({"container_id": SOURCE}))])
        if "FROM ONLY legacy_behavior_recovery.archived_batches" in statement:
            return Result([row for row in self.archived if row[0] in params[1]])
        if "FROM ONLY public.students" in statement:
            return Result([(student, self.students[student]) for student in params[0] if student in self.students])
        if "pg_advisory_xact_lock" in statement:
            assert params == points.BEHAVIOR_APPLY_LOCK
            self.locked = True
            if self.after_lock is not None:
                self.after_lock(self.ledger)
            return Result()
        if "LEFT JOIN LATERAL" in statement:
            rows = []
            for student, day in zip(params[::2], params[1::2]):
                rows.extend((student, day, *row) for row in self.ledger.get((student, day), [(None, None)])[:2])
            return Result(rows)
        if "c.relname = 'point_credits'" in statement:
            return Result([(self.private,)] if self.audit_exists else [])
        if "FROM ONLY legacy_behavior_recovery.point_credits" in statement:
            return Result([row for key, row in self.audit.items() if key in set(zip(params[::2], params[1::2]))])
        if statement.startswith("CREATE TABLE legacy_behavior_recovery.point_credits"):
            assert self.locked
            self.audit_exists = True
            return Result()
        if "'public.student_point_ledger'::pg_catalog.regclass" in statement:
            return Result([(True,)])
        if statement.startswith("WITH proven") and "INSERT INTO public.student_point_ledger" in statement:
            assert self.locked
            fields = params[:-5]
            amount, archive_sha, plan_sha, _, checked_amount = params[-5:]
            assert amount == checked_amount == 10
            result = []
            for student, day, ordinal, digest, kind in zip(*(fields[index::5] for index in range(5))):
                key = student, day
                ledger_id = 100 + len(self.audit)
                assert key not in self.ledger and key not in self.audit
                self.ledger[key] = [(ledger_id, amount)]
                self.audit[key] = (student, day, ledger_id, archive_sha, ordinal, digest, plan_sha, kind)
                result.append(key)
            return Result([] if self.failed_write else result)
        raise AssertionError("Unexpected recovery SQL")


class LegacyPointsTests(unittest.TestCase):
    def run_points(self, db, selected=None, *, apply=False):
        return points.reconcile_points(db, selected or plan(make_proof()), IDENTITY,
                                       expected_source_container=SOURCE, apply=apply, operator="offline")

    def test_plan_requires_exact_hash_date_and_unique_economic_key(self):
        raw = raw_plan(make_proof())
        with self.assertRaisesRegex(points.RestoreError, "point_proof_digest_mismatch"):
            points.load_plan(raw, expected_sha256="0" * 64)
        with self.assertRaisesRegex(points.RestoreError, "duplicate_point_proof"):
            plan(make_proof(), make_proof(1))
        with self.assertRaisesRegex(points.RestoreError, "invalid_point_proof_date"):
            plan({**make_proof(), "active_date": "2026-02-31"})
        with self.assertRaisesRegex(points.RestoreError, "invalid_point_proof_record"):
            plan({**make_proof(), "student_id": True})

    def test_guard_rejects_foreign_database_before_loading_archive(self):
        db = RecordingPG()
        db.identity = points.DatabaseIdentity("999", "offline", "tester", "127.0.0.1")
        with self.assertRaisesRegex(points.RestoreError, "unexpected_database_identity"):
            self.run_points(db, apply=True)
        self.assertFalse(any("archive_manifest" in sql for sql in db.commands))

    def test_digest_actor_and_source_must_match_database_archive(self):
        db = RecordingPG()
        db.archived[0] = (0, "0" * 64, *db.archived[0][2:])
        with self.assertRaisesRegex(points.RestoreError, "point_archive_row_mismatch"):
            self.run_points(db)
        with self.assertRaisesRegex(points.RestoreError, "point_archive_event_not_eligible"):
            self.run_points(RecordingPG(archive(student=102)))
        with self.assertRaisesRegex(points.RestoreError, "point_archive_source_mismatch"):
            points.reconcile_points(RecordingPG(), plan(make_proof()), IDENTITY, expected_source_container="e" * 64)

    def test_only_loggable_valid_student_batches_qualify(self):
        malformed = {**HEARTBEAT, "payload": {"idle_seconds": "bad"}}
        for row in (archive(role="teacher"), archive(events=[HEARTBEAT]), archive(events=[PAGE, malformed])):
            with self.subTest(row=row[:4]), self.assertRaisesRegex(points.RestoreError, "point_archive_event_not_eligible"):
                self.run_points(RecordingPG(row))
        summary = {**HEARTBEAT, "summary_text": "有摘要的记录"}
        self.assertEqual(1, self.run_points(RecordingPG(archive(events=[summary])))['missing_before'])

    def test_server_session_bound_matches_the_china_date(self):
        db = RecordingPG(archive(session="2026-11-05T18:03:00Z"))
        self.assertEqual(1, self.run_points(db, plan(make_proof(kind="server_session_bounds")))['missing_before'])
        with self.assertRaisesRegex(points.RestoreError, "point_session_date_mismatch"):
            self.run_points(db, plan(make_proof(day=date(2026, 11, 5), kind="server_session_bounds")))

    def test_read_only_existing_credits_and_conflicts_never_create_tables(self):
        db = RecordingPG(archive(), archive(1, 102))
        db.ledger[(101, DAY)] = [(20, 10), (21, 10)]
        selected = plan(make_proof(), make_proof(1, 102))
        result = self.run_points(db, selected)
        self.assertEqual((result['conflicting_keys'], result['missing_before'], result['points_credited']), (1, 1, 0))
        self.assertTrue(db.commands[0].endswith("READ ONLY"))
        with self.assertRaisesRegex(points.RestoreError, "point_ledger_conflict"):
            self.run_points(db, selected, apply=True)
        self.assertFalse(db.audit_exists)
        self.assertEqual({(101, DAY): [(20, 10), (21, 10)]}, db.ledger)

    def test_only_missing_day_credited_and_same_plan_rerun_checks_audit(self):
        db = RecordingPG(archive(), archive(1, 102))
        db.ledger[(102, DAY)] = [(21, 10)]
        selected = plan(make_proof(), make_proof(1, 102))
        result = self.run_points(db, selected, apply=True)
        self.assertEqual((result['new_credits'], result['points_credited']), (1, 10))
        self.assertEqual({(101, DAY): [(100, 10)], (102, DAY): [(21, 10)]}, db.ledger)
        self.assertEqual(db.audit[(101, DAY)][2], 100)
        result = self.run_points(db, selected, apply=True)
        self.assertEqual((result['new_credits'], result['existing_recovery_audits']), (0, 1))
        db.ledger[(101, DAY)] = [(100, 11)]
        with self.assertRaisesRegex(points.RestoreError, "point_audit_ledger_mismatch"):
            self.run_points(db, selected, apply=True)

    def test_active_students_and_worker_lock_are_checked_before_ledger_recheck(self):
        db = RecordingPG()
        db.students[101] = "suspended"
        with self.assertRaisesRegex(points.RestoreError, "point_student_missing_or_inactive"):
            self.run_points(db, apply=True)
        self.assertFalse(any("LEFT JOIN LATERAL" in sql for sql in db.commands))
        db.students[101] = "active"
        db.after_lock = lambda ledger: ledger.update({(101, DAY): [(22, 10)]})
        self.assertEqual(0, self.run_points(db, apply=True)['new_credits'])
        self.assertFalse(db.audit_exists)
        from classroom_app.services.behavior_inbox_service import APPLY_LOCK
        self.assertEqual(APPLY_LOCK, points.BEHAVIOR_APPLY_LOCK)

    def test_incomplete_atomic_write_rolls_back_both_tables(self):
        db = RecordingPG()
        db.failed_write = True
        with self.assertRaisesRegex(points.RestoreError, "point_ledger_insert_mismatch"):
            self.run_points(db, apply=True)
        self.assertEqual({}, db.ledger)
        self.assertEqual({}, db.audit)
        self.assertFalse(db.audit_exists)
