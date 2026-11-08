"""Collection evidence must never create presence or rewrite an accepted fact."""
from datetime import datetime, timedelta, timezone
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from classroom_app.db.schema_behavior_collection_health import (
    BEHAVIOR_COLLECTION_HEALTH_REQUIRED_COLUMNS,
    ensure_behavior_collection_health_schema,
)
from classroom_app.services.behavior_collection_health_service import record_collection_observation, record_collection_observations


class BehaviorCollectionHealthServiceTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(TemporaryDirectory(prefix="behavior-collection-health-"))
        self.conn = self.enterContext(closing(sqlite3.connect(Path(folder) / "evidence.sqlite")))
        self.conn.row_factory = sqlite3.Row
        ensure_behavior_collection_health_schema(self.conn, engine="sqlite")
        self.conn.executescript("""
            CREATE TABLE classroom_behavior_states (learning_online_seconds INTEGER, last_presence_at TEXT);
            INSERT INTO classroom_behavior_states VALUES (17, '2026-11-08T08:00:00');
            CREATE TABLE classroom_behavior_events (id INTEGER);
        """)
        self.args = dict(class_offering_id=410, user_pk=501, user_role="student", batch_id="b1_1794100000000_test",
                         received_at="2026-11-08T09:01:00", source="browser", valid_sample_count=1,
                         rejected_sample_count=0, learning_delta_seconds=0,
                         observed_start="2026-11-08T09:00:00", observed_end="2026-11-08T09:01:00")

    def record(self, **updates):
        return record_collection_observation(self.conn, **{**self.args, **updates})

    def row(self):
        return self.conn.execute("SELECT * FROM behavior_collection_observations").fetchone()

    def test_valid_observed_zero_preserves_business_state_and_trusted_ip(self):
        self.assertTrue(self.record(reason_codes=["idle", "idle"], ip_address="2001:db8::1"))
        row = self.row()
        self.assertEqual((1, 0, 0), (row["valid_sample_count"], row["rejected_sample_count"], row["learning_delta_seconds"]))
        self.assertEqual(["idle"], json.loads(row["reason_codes_json"]))
        self.assertEqual("2001:db8::1", row["ip_address"])
        self.assertEqual((17, "2026-11-08T08:00:00"), tuple(self.conn.execute("SELECT * FROM classroom_behavior_states").fetchone()))
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM classroom_behavior_events").fetchone()[0])

    def test_same_batch_is_immutable_and_not_incremented_on_retry(self):
        self.assertTrue(self.record(learning_delta_seconds=25))
        before = tuple(self.row())
        self.assertFalse(self.record(learning_delta_seconds=60, source="server", reason_codes=["late_retry"]))
        self.assertEqual(before, tuple(self.row()))
        for changed in ({"user_pk": 502}, {"user_role": "teacher"}, {"class_offering_id": 411}):
            self.assertTrue(self.record(**changed))
        self.assertEqual(4, self.conn.execute("SELECT COUNT(*) FROM behavior_collection_observations").fetchone()[0])

    def test_client_diagnostic_is_separate_failure_evidence_and_cannot_claim_presence(self):
        self.assertTrue(self.record(source="client_diagnostic", batch_id="diagnostic_01", valid_sample_count=0,
                                    observed_start=None, observed_end=None, reason_codes=["auth_required"],
                                    diagnostics={"original_batch_id": self.args["batch_id"], "status": 401}))
        row = self.row()
        self.assertEqual((0, 0, 0, 1), (row["valid_sample_count"], row["rejected_sample_count"],
                                      row["learning_delta_seconds"], row["client_diagnostic_count"]))
        with self.assertRaises(ValueError):
            self.record(source="client_diagnostic", batch_id="diagnostic_02")

    def test_caller_transaction_rolls_back_observation(self):
        self.conn.execute("BEGIN")
        self.assertTrue(self.record())
        self.conn.rollback()
        self.assertIsNone(self.row())

    def test_bulk_diagnostics_are_one_write_with_first_fact_retained_and_atomic_validation(self):
        items = [{**self.args, "batch_id": f"d1_bulk_{n}", "source": "client_diagnostic", "valid_sample_count": 0,
                  "observed_start": None, "observed_end": None,
                  "reason_codes": ["client_request_timeout"], "diagnostics": {"occurrence_count": n + 1}}
                 for n in range(20)]
        statements = []
        self.conn.set_trace_callback(statements.append)
        self.assertEqual(20, record_collection_observations(self.conn, items))
        self.assertEqual(1, sum(sql.startswith("INSERT INTO behavior_collection_observations") for sql in statements))
        self.assertEqual(0, record_collection_observations(self.conn, items))
        self.assertEqual(20, self.conn.execute("SELECT COUNT(*) FROM behavior_collection_observations").fetchone()[0])
        with self.assertRaises(ValueError):
            record_collection_observations(self.conn, [{**items[0], "batch_id": "d1_other"}, {**items[1], "valid_sample_count": 1}])
        self.assertEqual(20, self.conn.execute("SELECT COUNT(*) FROM behavior_collection_observations").fetchone()[0])
        self.conn.rollback()
        self.assertIsNone(self.row())

    def test_schema_is_idempotent_preserves_rows_and_has_window_indexes(self):
        self.record()
        before = tuple(self.row())
        ensure_behavior_collection_health_schema(self.conn, engine="sqlite")
        self.assertEqual(before, tuple(self.row()))
        indices = {row[1] for row in self.conn.execute("PRAGMA index_list(behavior_collection_observations)")}
        self.assertTrue({"idx_behavior_collection_class_received", "idx_behavior_collection_actor_received",
                         "idx_behavior_collection_class_observed"}.issubset(indices))

    def test_postgres_required_table_and_column_contract_registered(self):
        from classroom_app.db.postgres_schema import POSTGRES_RUNTIME_TABLE_DEFINITIONS, REQUIRED_POSTGRES_TABLES
        from classroom_app.db.postgres_required_columns import REQUIRED_POSTGRES_COLUMNS
        table = "behavior_collection_observations"
        self.assertIn(table, POSTGRES_RUNTIME_TABLE_DEFINITIONS)
        self.assertIn(table, REQUIRED_POSTGRES_TABLES)
        self.assertEqual(BEHAVIOR_COLLECTION_HEALTH_REQUIRED_COLUMNS[table], REQUIRED_POSTGRES_COLUMNS[table])

    def test_aware_timestamps_are_normalized_to_server_local_iso(self):
        stamp = datetime(2026, 11, 8, 1, 1, tzinfo=timezone.utc)
        self.record(received_at=stamp, observed_start=stamp - timedelta(seconds=60), observed_end=stamp)
        self.assertEqual(stamp.astimezone().replace(tzinfo=None).isoformat(), self.row()["received_at"])

    def test_invalid_counts_identity_time_and_ip_fail_before_insert(self):
        cases = (
            {"user_pk": True}, {"class_offering_id": 0}, {"user_role": "admin"}, {"source": "guessed"},
            {"batch_id": "a\nunsafe"}, {"batch_id": "x" * 129}, {"valid_sample_count": True},
            {"rejected_sample_count": -1}, {"valid_sample_count": 0, "learning_delta_seconds": 1},
            {"learning_delta_seconds": 91}, {"received_at": "2026-11-08"},
            {"observed_start": None}, {"observed_start": "2026-10-01T00:00:00"},
            {"observed_end": "2026-11-08T09:02:00"}, {"ip_address": "127.0.0.1, 192.0.2.1"},
        )
        for updates in cases:
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                self.record(**updates)
        self.assertIsNone(self.row())

    def test_private_unbounded_or_malformed_diagnostics_and_reason_codes_rejected(self):
        cases = (
            {"diagnostics": {"nested": {"session_id": "private"}}},
            {"diagnostics": {"ip_address": "192.0.2.1"}}, {"diagnostics": {"User-Agent": "private"}},
            {"diagnostics": {"text": "x" * 513}}, {"diagnostics": {"number": float("nan")}},
            {"diagnostics": {"text": "\ud800"}}, {"diagnostics": {str(i): "字" * 128 for i in range(32)}},
            {"diagnostics": {"a": {"b": {"c": {"d": {"e": 1}}}}}},
            {"reason_codes": ["raw message"]}, {"reason_codes": ["x" * 81]}, {"reason_codes": "bad"},
        )
        for updates in cases:
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                self.record(**updates)
        self.assertIsNone(self.row())


if __name__ == "__main__":
    unittest.main()
