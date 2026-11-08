"""Receipt-backed material retries retain real rollback and public error boundaries."""
from contextlib import contextmanager
import json
import sqlite3
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from classroom_app.db.errors import DatabaseBusyError, DatabaseConnectionError
from classroom_app.routers import learning
from classroom_app.services import behavior_inbox_service as inbox, material_progress_collection_service as collection
from tests import test_material_progress_collection as support
from tests import test_behavior_heartbeat_durability as heartbeat_support


class ConfirmedLockConflict(RuntimeError):
    sqlstate = "55P03"


class IdempotentBehaviorTransactionTests(unittest.TestCase):
    def test_closed_rolled_back_attempt_precedes_backoff_and_tuple_result_is_unchanged(self):
        events = []
        @contextmanager
        def connect():
            events.append("open")
            try:
                yield object()
                events.append("context_commit")
            except BaseException:
                events.append("rollback")
                raise
            finally:
                events.append("close")
        class Connection:
            def commit(self): events.append("commit")
        @contextmanager
        def factory():
            with connect(): yield Connection()
        calls = []
        def operation(conn):
            calls.append(conn)
            if len(calls) == 1: raise ConfirmedLockConflict()
            return ("kept", 2)
        def wait(_delay):
            self.assertEqual(["open", "rollback", "close"], events)
        self.assertEqual(("kept", 2), inbox.run_idempotent_behavior_transaction(operation,
            connection_factory=factory, retry_wait=wait))
        self.assertEqual(2, len(calls))
        self.assertEqual(["open", "rollback", "close", "open", "commit", "context_commit", "close"], events)

    def test_permission_validation_and_programming_failures_are_never_retried(self):
        @contextmanager
        def factory(): yield object()
        for error in (HTTPException(403, "synthetic denied"), ValueError("synthetic invalid"), RuntimeError("synthetic SQL defect")):
            with self.subTest(error=type(error).__name__), patch.object(inbox.time, "sleep") as wait:
                calls = []
                def operation(conn):
                    calls.append(conn)
                    raise error
                with self.assertRaises(type(error)):
                    inbox.run_idempotent_behavior_transaction(operation, connection_factory=factory)
                self.assertEqual(1, len(calls))
                wait.assert_not_called()

    def test_attempt_count_original_budget_and_stop_remain_bounded(self):
        @contextmanager
        def factory(): yield object()
        calls = []
        def operation(conn):
            calls.append(conn)
            raise ConfirmedLockConflict()
        with self.assertRaises(DatabaseBusyError):
            inbox.run_idempotent_behavior_transaction(operation, connection_factory=factory, retry_wait=lambda _: None)
        self.assertEqual(3, len(calls))
        calls.clear()
        with patch.object(inbox.time, "monotonic", side_effect=(0, 2)), self.assertRaises(DatabaseBusyError):
            inbox.run_idempotent_behavior_transaction(operation, connection_factory=factory, retry_wait=lambda _: self.fail("Expired budget must not sleep"))
        self.assertEqual(1, len(calls))
        calls.clear()
        with self.assertRaises(DatabaseBusyError):
            inbox.run_idempotent_behavior_transaction(operation, connection_factory=factory, retry_wait=lambda _: True)
        self.assertEqual(1, len(calls))

    def test_rollback_or_close_failure_is_not_hidden_as_a_clean_retry(self):
        for close_after_success in (False, True):
            calls = []
            class Connection:
                def commit(self): pass
            @contextmanager
            def factory():
                try:
                    yield Connection()
                finally:
                    raise DatabaseConnectionError("synthetic cleanup failed")
            def operation(conn):
                calls.append(conn)
                if not close_after_success: raise ConfirmedLockConflict()
            with self.subTest(close_after_success=close_after_success), patch.object(inbox.time, "sleep") as wait:
                with self.assertRaisesRegex(DatabaseConnectionError, "cleanup"):
                    inbox.run_idempotent_behavior_transaction(operation, connection_factory=factory)
                self.assertEqual(1, len(calls))
                wait.assert_not_called()


class MaterialProgressRetryTests(unittest.TestCase):
    def setUp(self):
        # Compose the established synthetic fixture instead of inheriting its
        # test methods, which would silently duplicate unittest discovery.
        self.f = support.MaterialProgressCollectionTests(methodName="runTest")
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.received = self.f.at(65)
        self.body = self.f.body(65, self.f.heartbeat(60, start=0, end=60))

    def call(self, body=None):
        return learning.post_material_progress(self.f.offering, learning.MaterialProgressPayload(**(body or self.body)), user=self.f.actor)

    def test_full_material_attempt_rolls_back_before_retry_and_first_server_time_is_retained(self):
        real = collection.record_behavior_batch
        connections, seen_times = [], []
        body_before = json.dumps(self.body, sort_keys=True)
        def admission(**kwargs):
            connections.append(kwargs["conn"])
            seen_times.append(kwargs["received_at"])
            if len(connections) == 1: raise ConfirmedLockConflict()
            return real(**kwargs)
        def wait(_delay):
            self.assertIsNone(self.f.material_state())
            self.assertIsNone(heartbeat_support.BehaviorHeartbeatDurabilityTests.dirty_version(self.f))
            with self.assertRaises(sqlite3.ProgrammingError): connections[0].execute("SELECT 1")
            self.f.received = self.f.at(2000)
        with patch.object(collection, "record_behavior_batch", side_effect=admission), patch.object(inbox.time, "sleep", side_effect=wait):
            result = self.call()
        self.assertEqual("durable_queued", result["write_mode"])
        self.assertEqual([self.f.local(65)] * 2, seen_times)
        self.assertEqual(self.f.local(65), heartbeat_support.BehaviorHeartbeatDurabilityTests.receipt(self.f, self.body)["received_at"])
        self.assertEqual((60, 60, 1), tuple(self.f.material_state()[key] for key in ("accumulated_seconds", "active_seconds", "view_count")))
        self.assertEqual(body_before, json.dumps(self.body, sort_keys=True))

    def test_real_committed_material_unknown_ack_replays_without_changing_any_row(self):
        real_connection, real_record = learning.get_db_connection, collection.record_behavior_batch
        before, commits = {}, []
        class CommitOnceLost:
            def __init__(self, conn): self.conn = conn
            def __getattr__(self, name): return getattr(self.conn, name)
            def commit(self):
                self.conn.commit()
                commits.append(True)
                if len(commits) == 1: raise ConnectionError("synthetic committed ACK lost")
        @contextmanager
        def factory():
            with real_connection() as conn: yield CommitOnceLost(conn)
        def admission(**kwargs):
            result = real_record(**kwargs)
            if not before:
                conn = kwargs["conn"]
                for table in ("learning_material_progress", "learning_progress_snapshots", "behavior_write_inbox"):
                    before[table] = [dict(row) for row in conn.execute("SELECT * FROM " + table + " ORDER BY id" if table != "behavior_write_inbox" else "SELECT * FROM behavior_write_inbox ORDER BY batch_key")]
            return result
        with patch.object(learning, "get_db_connection", side_effect=factory), \
                patch.object(collection, "record_behavior_batch", side_effect=admission), patch.object(inbox.time, "sleep"):
            result = self.call()
        self.assertTrue(result["duplicate"])
        self.assertEqual(1, self.f.pipeline.stats()["unknown_commit_count"])
        with real_connection() as conn:
            for table, rows in before.items():
                actual = [dict(row) for row in conn.execute("SELECT * FROM " + table + " ORDER BY id" if table != "behavior_write_inbox" else "SELECT * FROM behavior_write_inbox ORDER BY batch_key")]
                self.assertEqual(rows, actual, table)

    def test_revoked_authorization_on_retry_is_returned_without_another_write(self):
        attempts = []
        def access(*_args):
            attempts.append(True)
            if len(attempts) > 1: raise HTTPException(403, "synthetic permission revoked")
        with patch.object(learning, "_ensure_classroom_access", side_effect=access), \
                patch.object(collection, "record_behavior_batch", side_effect=ConfirmedLockConflict()), patch.object(inbox.time, "sleep"):
            with self.assertRaises(HTTPException) as raised: self.call()
        self.assertEqual(403, raised.exception.status_code)
        self.assertEqual(2, len(attempts))
        self.assertIsNone(self.f.material_state())

    def test_conflicts_invalid_identity_and_legacy_failures_are_not_blindly_retried(self):
        self.call()
        changed = {**self.body, "scroll_ratio": .9}
        with patch.object(inbox.time, "sleep") as wait:
            with self.assertRaises(HTTPException) as raised: self.call(changed)
            self.assertEqual(409, raised.exception.status_code)
            with self.assertRaises(HTTPException) as raised: self.call({**self.body, "user_key": "student:other"})
            self.assertEqual(409, raised.exception.status_code)
            with patch.object(learning, "record_material_learning_progress", side_effect=ConfirmedLockConflict()) as legacy:
                with self.assertRaises(ConfirmedLockConflict): self.call({**self.body, "progress_version": 1})
                self.assertEqual(1, legacy.call_count)
            wait.assert_not_called()


if __name__ == "__main__":
    unittest.main()
