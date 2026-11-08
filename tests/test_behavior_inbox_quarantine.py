"""Permanent-data isolation, replay safety and explicit recovery on owned SQLite."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
import json
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from classroom_app.db.schema_behavior_inbox import BEHAVIOR_INBOX_TABLES, ensure_behavior_inbox_schema
from classroom_app.services import behavior_inbox_service as inbox
from classroom_app.services.behavior_event_validation import BehaviorValidationError
from classroom_app.services.behavior_inbox_recovery_service import recover_behavior_receipt, BehaviorRecoveryOrderError
from tests import test_behavior_inbox as support
from tools import behavior_inbox_recovery as cli


class DataFailure(Exception):
    def __init__(self, sqlstate="22P02"):
        self.sqlstate = sqlstate
        super().__init__("synthetic driver error; not retained as user data")


class QuarantineTests(unittest.TestCase):
    def setUp(self):
        support.BehaviorInboxTests.setUp(self)
        with self.connect() as conn:
            conn.execute("""CREATE TABLE classroom_behavior_states(user_role TEXT,user_pk INTEGER,
                last_event_at TEXT,last_presence_at TEXT,current_session_started_at TEXT)""")
            conn.execute("CREATE TABLE student_activity_streaks(student_id INTEGER,last_active_date TEXT)")
    tearDown = support.BehaviorInboxTests.tearDown
    apply = support.BehaviorInboxTests.apply
    count = support.BehaviorInboxTests.count

    def row(self):
        with self.connect() as conn:
            return dict(conn.execute("SELECT * FROM behavior_write_inbox ORDER BY received_at,batch_key LIMIT 1").fetchone())

    def make_quarantined(self, *, malformed=False):
        request = replace(self.request, events=[{"action_type": "presence_heartbeat", "payload": {"idle_seconds": "bad"}}]) if malformed else self.request
        with patch.object(inbox, "validate_behavior_payload"):
            self.pipeline.submit(request)
        if malformed:
            self.pipeline._drain_once()
        else:
            def fail(conn, **kwargs):
                self.apply(conn, **kwargs)
                raise DataFailure()
            self.pipeline._apply_batch = fail
            for _ in range(3):
                self.pipeline._drain_once()
                with self.connect() as conn:
                    conn.execute("UPDATE behavior_write_inbox SET retry_at=NULL")
            self.pipeline._apply_batch = self.apply
        self.assertEqual(self.row()["state"], "quarantined")
        return request, self.row()

    def recover(self, action, row, **overrides):
        values = dict(action=action, batch_key=row["batch_key"], payload_hash=row["payload_hash"],
                      class_offering_id=1, user_role="student", user_pk=10,
                      operator="teacher:1", reason="synthetic reviewed recovery")
        values.update(overrides)
        with self.connect() as conn:
            return recover_behavior_receipt(conn, **values)

    def test_new_malformed_data_is_rejected_without_any_durable_receipt(self):
        events = [
            {"action_type": "presence_heartbeat", "payload": {"idle_seconds": value}}
            for value in ("not-an-integer", [1], {"bad": 1}, float("inf"), float("nan"))
        ] + [{"action_type": "click", "summary_text": "nul\x00text"},
             {"action_type": "cli\x00ck"}, {"action_type": "click", "page_key": "bad\x00page"},
             {"action_type": "click", "payload": {"nested": [float("inf")]}}]
        for event in events:
            with self.subTest(event=repr(event)), self.assertRaises(BehaviorValidationError):
                self.pipeline.submit(replace(self.request, batch_id=inbox.new_batch_id(), events=[event]))
        for field, value in (("page_key", "bad\x00page"), ("display_name", "bad\x00name"),
                             ("session_started_at", "bad\x00session"), ("page_key", "bad\ud800")):
            with self.subTest(field=field), self.assertRaises(BehaviorValidationError):
                self.pipeline.submit(replace(self.request, **{field: value}))
        self.assertEqual(self.count("behavior_write_inbox"), 0)
        self.assertEqual(self.count("effects"), 0)

    def test_unreadable_metadata_is_quarantined_once_without_blocking_other_actors(self):
        with self.connect() as conn:
            conn.execute("INSERT INTO students VALUES(20)")
        cases = ("{broken", "[]", "null", '{"recovery_actions":[1]}',
                 '{"permanent_error_streak":"broken"}', json.dumps(["legacy_" * 12000]))
        for number, raw in enumerate(cases):
            request = replace(self.request, batch_id=inbox.new_batch_id())
            self.pipeline.submit(request)
            with self.connect() as conn:
                conn.execute("UPDATE behavior_write_inbox SET result_json=? WHERE batch_id=?", (raw, request.batch_id))
                original = dict(conn.execute("SELECT * FROM behavior_write_inbox WHERE batch_id=?", (request.batch_id,)).fetchone())
            self.pipeline.submit(replace(self.request, user_pk=20, batch_id=inbox.new_batch_id(),
                                         events=[{"action_type": f"good_{number}"}]))
            with patch.object(inbox.time, "monotonic", return_value=0):
                self.assertEqual(2, self.pipeline._drain_batch())
            with self.connect() as conn:
                isolated = dict(conn.execute("SELECT * FROM behavior_write_inbox WHERE batch_id=?", (request.batch_id,)).fetchone())
            evidence = json.loads(isolated["result_json"])
            self.assertEqual(("quarantined", 1), (isolated["state"], isolated["attempts"]))
            self.assertEqual(original["events_json"], isolated["events_json"])
            self.assertEqual(original["payload_hash"], isolated["payload_hash"])
            self.assertEqual(raw, evidence["unreadable_result_json"])
            self.assertEqual(hashlib.sha256(raw.encode("utf-8")).hexdigest(), evidence["unreadable_result_sha256"])
            self.assertEqual(len(raw.encode("utf-8")), evidence["unreadable_result_bytes"])
            self.assertLessEqual(len(isolated["result_json"].encode("utf-8")), 2 * len(raw.encode("utf-8")) + 4096)
            self.assertEqual(number + 1, self.count("effects"))
            self.assertEqual(0, self.pipeline._drain_batch())
            with self.connect() as conn:
                repeated = dict(conn.execute("SELECT * FROM behavior_write_inbox WHERE batch_id=?", (request.batch_id,)).fetchone())
            self.assertEqual(isolated, repeated)  # No repeated parse/wrap/retry loop.

    def test_admin_retry_and_discard_preserve_unreadable_metadata_without_exposing_it_to_clients(self):
        requests = [replace(self.request, batch_id=inbox.new_batch_id()) for _ in range(2)]
        raw_values = ("{original legacy metadata", "[]")
        for request, raw in zip(requests, raw_values):
            self.pipeline.submit(request)
            with self.connect() as conn:
                conn.execute("UPDATE behavior_write_inbox SET result_json=? WHERE batch_id=?", (raw, request.batch_id))
        with patch.object(inbox.time, "monotonic", return_value=0):
            self.assertEqual(2, self.pipeline._drain_batch())
        with self.connect() as conn:
            rows = [dict(conn.execute("SELECT * FROM behavior_write_inbox WHERE batch_id=?", (request.batch_id,)).fetchone())
                    for request in requests]
        self.assertEqual("pending", self.recover("retry", rows[0])["state"])
        self.assertTrue(self.pipeline._drain_once())
        receipt = self.pipeline.submit(requests[0])
        self.assertEqual("synced", receipt["write_mode"])
        self.assertTrue(all(field not in receipt for field in inbox._UNREADABLE_RESULT_FIELDS))
        self.assertEqual("discarded", self.recover("discard", rows[1])["state"])
        self.assertEqual("discarded", self.pipeline.submit(requests[1])["write_mode"])
        with self.connect() as conn:
            for request, raw, action in zip(requests, raw_values, ("retry", "discard")):
                row = dict(conn.execute("SELECT * FROM behavior_write_inbox WHERE batch_id=?", (request.batch_id,)).fetchone())
                metadata = json.loads(row["result_json"])
                self.assertEqual(raw, metadata["unreadable_result_json"])
                self.assertEqual(action, metadata["recovery_actions"][-1]["action"])
                self.assertEqual(rows[0]["payload_hash"], row["payload_hash"])
        self.assertEqual(1, self.count("effects"))

    def test_legacy_server_metadata_and_heartbeat_coercion_are_preserved(self):
        for value in ("12", 1.5, None, False, [], {}):
            request = replace(self.request, batch_id=inbox.new_batch_id(), events=[{
                "action_type": "presence_heartbeat", "payload": {"idle_seconds": value,
                    "at": datetime(2026, 11, 5), "path": Path("synthetic/path"), "amount": Decimal("1.25"),
                    "unknown_zero": "valid\x00json", "tuple": (1, 2)}}])
            self.assertEqual(self.pipeline.submit(request)["write_mode"], "durable_queued")
        with self.connect() as conn:
            payload = json.loads(conn.execute("SELECT events_json FROM behavior_write_inbox LIMIT 1").fetchone()[0])[0]["payload"]
        self.assertEqual(payload["amount"], "1.25")
        self.assertEqual(payload["path"], "synthetic\\path" if __import__("os").name == "nt" else "synthetic/path")
        self.assertEqual(payload["tuple"], [1, 2])
        with self.assertRaises(BehaviorValidationError):
            self.pipeline.submit(replace(self.request, events=[{"action_type": "heartbeat", "payload": {"idle_seconds": Decimal("1.5")}}]))

    def test_previously_accepted_poison_is_retained_and_does_not_block_later_actor_work(self):
        request, original = self.make_quarantined(malformed=True)
        duplicate = self.pipeline.submit(request)
        self.assertEqual((duplicate["write_mode"], duplicate["accepted_event_count"]), ("quarantined", 0))
        self.assertEqual(self.row()["events_json"], original["events_json"])
        self.assertIsNone(self.row()["applied_at"])
        later = replace(self.request, class_offering_id=2, batch_id=inbox.new_batch_id())
        self.pipeline.submit(later)
        self.assertTrue(self.pipeline._drain_once())
        self.assertFalse(self.pipeline._drain_once())
        self.assertEqual(self.count("effects"), 1)
        self.pipeline._refresh_depth()
        self.assertEqual((self.pipeline.stats()["queue_depth"], self.pipeline.stats()["quarantined_count"]), (0, 1))
        with patch.object(inbox, "BEHAVIOR_WRITE_QUEUE_SIZE", 1), self.assertRaises(inbox.DatabaseBusyError):
            self.pipeline.submit(replace(later, batch_id=inbox.new_batch_id()))
        with self.assertRaises(inbox.DatabaseBusyError):
            self.pipeline.flush(.1)

    def test_oversized_legacy_receipt_is_quarantined_without_erasing_raw_data(self):
        legacy = replace(self.request, events=[{"action_type": "click", "payload": {"old_data": "x" * (70 * 1024)}}])
        # Model a receipt admitted under an older, larger limit. Replay must
        # reconcile that exact immutable payload, not re-run new admission.
        with patch.object(inbox, "validate_behavior_payload"):
            self.pipeline.submit(legacy)
        payload = self.row()["events_json"]
        self.pipeline._drain_once()
        self.assertEqual((self.row()["state"], self.row()["events_json"]), ("quarantined", payload))
        self.assertEqual(self.pipeline.submit(legacy)["write_mode"], "quarantined")
        self.assertEqual(self.count("effects"), 0)

    def test_only_consecutive_permanent_data_errors_quarantine_and_all_effects_roll_back(self):
        self.pipeline.submit(self.request)
        codes = iter(["22P02", "22P02", "42P01", "22P02", "22P02", "22P02"])
        def fail(conn, **kwargs):
            self.apply(conn, **kwargs)
            raise DataFailure(next(codes))
        self.pipeline._apply_batch = fail
        for index in range(6):
            self.pipeline._drain_once()
            self.assertEqual(self.row()["state"], "quarantined" if index == 5 else "pending")
            self.assertEqual(self.count("effects"), 0)
            with self.connect() as conn:
                conn.execute("UPDATE behavior_write_inbox SET retry_at=NULL")

    def test_connection_conflict_schema_and_generic_programming_failures_never_auto_quarantine(self):
        self.pipeline.submit(self.request)
        for error in [DataFailure("08006"), DataFailure("40P01"), DataFailure("42P01"), ValueError("programming bug")] * 4:
            def fail(conn, **kwargs):
                raise error
            self.pipeline._apply_batch = fail
            self.pipeline._drain_once()
            self.assertEqual(self.row()["state"], "pending")
            with self.connect() as conn:
                conn.execute("UPDATE behavior_write_inbox SET retry_at=NULL")

    def test_unknown_quarantine_commit_is_reconciled_and_commit_errors_are_not_classified_as_payload(self):
        request = replace(self.request, events=[{"action_type": "heartbeat", "payload": {"idle_seconds": "bad"}}])
        with patch.object(inbox, "validate_behavior_payload"):
            self.pipeline.submit(request)
        self.fail_commit = True
        with self.assertRaises(ConnectionError):
            self.pipeline._drain_once()
        self.assertEqual(self.row()["state"], "quarantined")
        self.assertFalse(self.pipeline._drain_once())
        self.assertEqual(self.pipeline.stats()["unknown_commit_count"], 1)
        self.pipeline.submit(replace(self.request, batch_id=inbox.new_batch_id()))
        @contextmanager
        def bad_commit():
            with self.connect() as conn:
                class Proxy:
                    def __getattr__(self, key): return getattr(conn, key)
                    def commit(self): raise DataFailure("22000")
                yield Proxy()
        with patch.object(inbox, "get_db_connection", bad_commit):
            for _ in range(3):
                with self.assertRaises(DataFailure):
                    self.pipeline._drain_once()
        with self.connect() as conn:
            states = [row[0] for row in conn.execute("SELECT state FROM behavior_write_inbox ORDER BY received_at")]
        self.assertEqual(states, ["quarantined", "pending"])
        self.assertEqual(self.count("effects"), 0)

    def test_explicit_retry_keeps_hash_payload_audit_and_applies_once(self):
        request, original = self.make_quarantined()
        result = self.recover("retry", original)
        self.assertEqual((result["state"], result["applied"]), ("pending", False))
        self.pipeline._drain_once()
        self.assertEqual(self.count("effects"), 1)
        receipt = self.pipeline.submit(request)
        self.assertEqual(receipt["write_mode"], "synced")
        self.assertNotIn("recovery_actions", receipt)
        row = self.row()
        self.assertEqual(row["payload_hash"], original["payload_hash"])
        self.assertEqual(json.loads(row["result_json"])["recovery_actions"][0]["reason"], "synthetic reviewed recovery")
        self.assertIsNotNone(row["resolved_at"])

    def test_late_or_expired_recovery_is_rejected_without_any_change(self):
        _request, original = self.make_quarantined()
        later = replace(self.request, batch_id=inbox.new_batch_id())
        self.pipeline.submit(later)
        self.pipeline._drain_once()
        before = self.row()
        with self.assertRaises(BehaviorRecoveryOrderError):
            self.recover("retry", original)
        self.assertEqual(self.row(), before)
        self.assertEqual(self.count("effects"), 1)
        with self.connect() as conn:
            conn.execute("DELETE FROM behavior_write_inbox WHERE state='applied'")
            conn.execute("UPDATE behavior_write_inbox SET received_at=?", ((datetime.now() - timedelta(days=8)).isoformat(),))
        before = self.row()
        with self.assertRaises(BehaviorRecoveryOrderError):
            self.recover("retry", original)
        self.assertEqual(self.row(), before)

    def test_direct_behavior_or_repair_card_facts_prevent_stale_recovery(self):
        _request, original = self.make_quarantined()
        future = (datetime.now() + timedelta(hours=1)).isoformat()
        with self.connect() as conn:
            conn.execute("INSERT INTO classroom_behavior_states VALUES('student',10,?,NULL,NULL)", (future,))
        before = self.row()
        with self.assertRaises(BehaviorRecoveryOrderError):
            self.recover("retry", original)
        self.assertEqual(self.row(), before)
        with self.connect() as conn:
            conn.execute("DELETE FROM classroom_behavior_states")
            conn.execute("INSERT INTO student_activity_streaks VALUES(10,?)",
                         ((datetime.now() + timedelta(days=1)).date().isoformat(),))
        with self.assertRaises(BehaviorRecoveryOrderError):
            self.recover("retry", original)
        self.assertEqual(self.row(), before)

    def test_replay_revalidates_new_repair_facts_after_admin_retry(self):
        _request, original = self.make_quarantined()
        self.recover("retry", original)
        with self.connect() as conn:
            conn.execute("INSERT INTO student_activity_streaks VALUES(10,?)",
                         ((datetime.now() + timedelta(days=1)).date().isoformat(),))
        self.pipeline._drain_once()
        self.assertEqual(self.row()["state"], "quarantined")
        self.assertIn("BehaviorRecoveryOrderError", self.row()["last_error"])
        self.assertEqual(self.count("effects"), 0)

    def test_discard_requires_scope_hash_operator_reason_and_unblocks_delete_without_scoring(self):
        request, original = self.make_quarantined(malformed=True)
        for override in ({"payload_hash": "0" * 64}, {"user_pk": 11}, {"class_offering_id": 2},
                         {"operator": ""}, {"reason": ""}):
            with self.subTest(override=override), self.assertRaises((ValueError, inbox.BehaviorBatchConflict)):
                self.recover("discard", original, **override)
        with self.assertRaises(inbox.BehaviorDeletionPending), self.connect() as conn:
            inbox.prepare_behavior_deletion(conn, user_role="student", user_pk=10)
        with self.assertRaises(BehaviorValidationError):
            self.recover("retry", original)
        result = self.recover("discard", original)
        self.assertFalse(result["applied"])
        self.assertEqual(self.row()["events_json"], original["events_json"])
        self.assertEqual(self.pipeline.submit(request)["write_mode"], "discarded")
        self.assertFalse(self.pipeline._drain_once())
        self.assertEqual(self.count("effects"), 0)
        with self.connect() as conn:
            inbox.prepare_behavior_deletion(conn, user_role="student", user_pk=10)
            conn.execute("DELETE FROM students WHERE id=10")
        self.assertEqual(self.row()["events_json"], "[]")
        self.assertEqual(self.row()["payload_hash"], original["payload_hash"])
        audit = json.loads(self.row()["result_json"])["recovery_actions"][0]
        self.assertEqual((audit["action"], audit["operator"], audit["reason"]), ("discard", "teacher:1", "retired"))

    def test_quarantines_never_expire_and_discard_audit_retains_ten_days_after_resolution(self):
        _request, original = self.make_quarantined()
        old = (datetime.now() - timedelta(days=20)).isoformat()
        with self.connect() as conn:
            conn.execute("UPDATE behavior_write_inbox SET received_at=?", (old,))
        self.pipeline._cleanup_receipts()
        self.assertEqual(self.count("behavior_write_inbox"), 1)
        self.recover("discard", original)
        self.pipeline._cleanup_receipts()
        self.assertEqual(self.count("behavior_write_inbox"), 1)

    def test_cli_defaults_read_only_and_writes_require_full_explicit_confirmation(self):
        _request, original = self.make_quarantined()
        args = cli.arguments([])
        listed = cli.run(args, self.connect)
        self.assertEqual(listed[0]["state"], "quarantined")
        self.assertNotIn("events_json", listed[0])
        with patch("sys.stderr"), self.assertRaises(SystemExit):
            cli.arguments(["discard", "--batch-key", original["batch_key"]])
        self.assertEqual(self.row()["state"], "quarantined")


class SchemaUpgradeTests(unittest.TestCase):
    def old_table(self, conn):
        old_ddl = BEHAVIOR_INBOX_TABLES["behavior_write_inbox"].replace(
            ", 'quarantined', 'discarded'", "").replace("        resolved_at TEXT,\n", "")
        conn.execute(old_ddl)
        conn.execute("""INSERT INTO behavior_write_inbox(batch_key,batch_id,payload_hash,class_offering_id,user_pk,
            user_role,display_name,events_json,received_at) VALUES('key','id','hash',1,10,'student','synthetic','[]','old')""")
        conn.commit()

    def test_old_two_state_check_and_all_receipts_survive_the_sqlite_startup_upgrade(self):
        with sqlite3.connect(":memory:") as conn:
            conn.row_factory = sqlite3.Row
            self.old_table(conn)
            ensure_behavior_inbox_schema(conn, engine="sqlite")
            row = conn.execute("SELECT * FROM behavior_write_inbox").fetchone()
            self.assertEqual((row["batch_key"], row["received_at"], row["resolved_at"]), ("key", "old", None))
            conn.execute("UPDATE behavior_write_inbox SET state='quarantined'")
            ensure_behavior_inbox_schema(conn, engine="sqlite")
            self.assertEqual(conn.execute("SELECT state FROM behavior_write_inbox").fetchone()[0], "quarantined")

    def test_mid_upgrade_failure_rolls_back_ddl_and_restart_can_upgrade_same_receipt(self):
        failure = {"enabled": False}
        class Connection(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                result = super().execute(sql, parameters)
                if failure["enabled"] and sql == "DROP TABLE behavior_write_inbox":
                    failure["enabled"] = False
                    raise sqlite3.OperationalError("synthetic crash after source table drop")
                return result
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "owned-upgrade.db"
            conn = sqlite3.connect(path, factory=Connection)
            self.old_table(conn)
            failure["enabled"] = True
            with self.assertRaisesRegex(sqlite3.OperationalError, "synthetic crash"):
                ensure_behavior_inbox_schema(conn, engine="sqlite")
            self.assertEqual(conn.execute("SELECT batch_key FROM behavior_write_inbox").fetchone()[0], "key")
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='behavior_write_inbox_upgrade'").fetchone())
            conn.close()
            conn = sqlite3.connect(path)
            try:
                ensure_behavior_inbox_schema(conn, engine="sqlite")
                conn.execute("UPDATE behavior_write_inbox SET state='quarantined'")
                self.assertEqual(conn.execute("SELECT batch_key FROM behavior_write_inbox").fetchone()[0], "key")
            finally:
                conn.close()

    def test_upgrade_never_commits_an_outer_business_transaction(self):
        conn = sqlite3.connect(":memory:")
        try:
            self.old_table(conn)
            conn.execute("UPDATE behavior_write_inbox SET display_name='outer uncommitted change'")
            ensure_behavior_inbox_schema(conn, engine="sqlite")
            self.assertTrue(conn.in_transaction)
            conn.rollback()
            self.assertEqual(conn.execute("SELECT display_name FROM behavior_write_inbox").fetchone()[0], "synthetic")
            self.assertNotIn("resolved_at", {row[1] for row in conn.execute("PRAGMA table_info(behavior_write_inbox)")})
        finally:
            conn.close()
