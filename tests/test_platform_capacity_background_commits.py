"""Only successful PostgreSQL owner commits may confirm projection metrics."""
from types import SimpleNamespace
from contextlib import redirect_stdout
import io
import unittest

from tools.platform_capacity_background import _committed_projection_recorder


class FakeConnection:
    def __init__(self, *, before=2, unknown_commit=False, calculated_version=10):
        self.raw_connection = SimpleNamespace(info=SimpleNamespace(transaction_status=before))
        self.unknown_commit = unknown_commit
        self.physically_committed = False
        self.closed = False
        self.calculated_version = calculated_version
        self.executions = []
        self.observer_rollbacks = 0

    def commit(self):
        self.physically_committed = self.raw_connection.info.transaction_status == 2
        self.raw_connection.info.transaction_status = 0
        if self.unknown_commit:
            raise ConnectionError("synthetic connection loss after possible server commit")

    def rollback(self):
        self.observer_rollbacks += 1
        self.raw_connection.info.transaction_status = 0

    def close(self):
        self.closed = True
        self.raw_connection.info.transaction_status = 0

    def execute(self, statement, params=()):
        self.executions.append((statement, params))
        self.raw_connection.info.transaction_status = 2
        return SimpleNamespace(fetchone=lambda: {"calculated_version": self.calculated_version})


class ProjectionCommitMetricTests(unittest.TestCase):
    def test_staged_event_waits_until_owner_commit_and_emits_exactly_once(self):
        events = []
        conn = FakeConnection()
        with _committed_projection_recorder(FakeConnection, lambda kind, **values: events.append((kind, values))) as staged:
            staged(conn, student_id=1, class_offering_id=2, version=7)
            self.assertEqual([], events)
            conn.commit()
            conn.commit()
        self.assertEqual([("projection_published", {"student_id": 1, "class_offering_id": 2, "version": 7})], events)

    def test_rollback_or_close_discards_staged_measurement(self):
        events = []
        with _committed_projection_recorder(FakeConnection, lambda kind, **values: events.append((kind, values))) as staged:
            for cleanup in ("rollback", "close"):
                conn = FakeConnection()
                staged(conn, student_id=1, class_offering_id=2, version=7)
                getattr(conn, cleanup)()
                conn.commit()
        self.assertEqual([], events)

    def test_unknown_commit_does_not_claim_confirmation_even_if_server_may_have_committed(self):
        events = []
        conn = FakeConnection(unknown_commit=True)
        with _committed_projection_recorder(FakeConnection, lambda kind, **values: events.append((kind, values))) as staged:
            staged(conn, student_id=1, class_offering_id=2, version=7)
            with self.assertRaises(ConnectionError):
                conn.commit()
            self.assertTrue(conn.physically_committed)
            conn.unknown_commit = False
            conn.commit()
        self.assertEqual([], events)

    def test_aborted_transaction_successful_rollback_is_not_a_publication(self):
        events = []
        conn = FakeConnection(before=3)
        with _committed_projection_recorder(FakeConnection, lambda kind, **values: events.append((kind, values))) as staged:
            staged(conn, student_id=1, class_offering_id=2, version=7)
            conn.commit()
        self.assertFalse(conn.physically_committed)
        self.assertEqual([], events)

    def test_connections_and_rollback_generations_do_not_mix(self):
        events = []
        first, second = FakeConnection(), FakeConnection()
        with _committed_projection_recorder(FakeConnection, lambda kind, **values: events.append((kind, values))) as staged:
            staged(first, student_id=1, class_offering_id=2, version=7)
            staged(second, student_id=2, class_offering_id=2, version=8)
            first.rollback()
            second.commit()
        self.assertEqual([("projection_published", {"student_id": 2, "class_offering_id": 2, "version": 8})], events)

    def test_metric_io_failure_cannot_turn_a_committed_handler_into_a_retry(self):
        conn = FakeConnection()
        def failed_record(*_args, **_kwargs):
            raise OSError("synthetic metric disk failure")
        output = io.StringIO()
        with redirect_stdout(output), _committed_projection_recorder(FakeConnection, failed_record) as staged:
            staged(conn, student_id=1, class_offering_id=2, version=7)
            conn.commit()
            self.assertEqual("event_write", staged.measurement_errors[0]["phase"])
        self.assertTrue(conn.physically_committed)
        self.assertIn("measurement failed (event_write): OSError", output.getvalue())

    def test_outer_savepoint_rollback_then_successful_commit_cannot_false_confirm(self):
        events = []
        # The staged version 7 was rolled back to a business savepoint, while
        # other outer work still commits successfully. Durable projection is 6.
        conn = FakeConnection(calculated_version=6)
        with _committed_projection_recorder(FakeConnection, lambda kind, **values: events.append((kind, values))) as staged:
            staged(conn, student_id=1, class_offering_id=2, version=7)
            conn.commit()
            conn.close()
        self.assertTrue(conn.physically_committed)
        self.assertEqual([], events)
        self.assertEqual(1, len(conn.executions))
        self.assertEqual(0, conn.observer_rollbacks)
        self.assertEqual(0, conn.raw_connection.info.transaction_status)

    def test_postcommit_verification_error_is_separate_from_successful_business_commit(self):
        events = []
        conn = FakeConnection(calculated_version="malformed verification result")
        with redirect_stdout(io.StringIO()), _committed_projection_recorder(FakeConnection, lambda kind, **values: events.append((kind, values))) as staged:
            staged(conn, student_id=1, class_offering_id=2, version=7)
            conn.commit()
            conn.close()
            self.assertEqual("durable_generation_read", staged.measurement_errors[0]["phase"])
        self.assertTrue(conn.physically_committed)
        self.assertFalse(any(kind == "projection_published" for kind, _values in events))
        self.assertEqual("projection_measurement_error", events[0][0])
