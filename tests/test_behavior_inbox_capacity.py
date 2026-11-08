"""Bounded recorder work and atomic receipt groups on owned synthetic SQLite."""
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

from classroom_app.services import behavior_inbox_service as inbox
from tests import test_behavior_inbox as support


class BehaviorInboxCapacityTests(unittest.TestCase):
    setUp = support.BehaviorInboxTests.setUp
    tearDown = support.BehaviorInboxTests.tearDown
    apply = support.BehaviorInboxTests.apply
    count = support.BehaviorInboxTests.count

    def enqueue(self, count):
        requests = [replace(self.request, batch_id=inbox.new_batch_id(), events=[{"action_type": f"event_{i}"}])
                    for i in range(count)]
        for request in requests:
            self.pipeline.submit(request)
        return requests

    def test_bounded_group_amortizes_commit_without_changing_individual_receipts(self):
        requests = self.enqueue(11)
        with patch.object(inbox.time, "monotonic", return_value=0), patch.object(
            self.pipeline, "_commit", wraps=self.pipeline._commit,
        ) as commit:
            self.assertEqual(8, self.pipeline._drain_batch(max_batches=8))
        commit.assert_called_once()
        self.assertEqual(8, self.count("effects"))
        for request in requests[:8]:
            self.assertEqual("synced", self.pipeline.submit(request)["write_mode"])
        self.assertEqual("durable_queued", self.pipeline.submit(requests[8])["write_mode"])
        self.assertEqual(8, self.pipeline.stats()["max_drain_batch_count"])
        self.assertEqual(1, self.pipeline.stats()["drain_transactions"])

    def test_time_budget_is_checked_before_starting_another_receipt(self):
        self.enqueue(3)
        with patch.object(inbox.time, "monotonic", side_effect=[0, 1, 1]):
            self.assertEqual(1, self.pipeline._drain_batch(max_batches=8))
        self.assertEqual(1, self.count("effects"))

    def test_one_actor_failure_rolls_back_only_it_and_preserves_its_following_order(self):
        with self.connect() as conn:
            conn.execute("INSERT INTO students VALUES(20)")
        first = self.enqueue(1)[0]
        other = replace(self.request, user_pk=20, batch_id=inbox.new_batch_id(), events=[{"action_type": "other_actor"}])
        following = replace(self.request, batch_id=inbox.new_batch_id(), events=[{"action_type": "following"}])
        self.pipeline.submit(other)
        self.pipeline.submit(following)

        def fail_first(conn, **kwargs):
            result = self.apply(conn, **kwargs)
            if kwargs["events"][0]["action_type"] == "event_0":
                conn.execute("SELECT missing_synthetic_column")
            return result

        self.pipeline._apply_batch = fail_first
        with patch.object(inbox.time, "monotonic", return_value=0):
            self.assertEqual(2, self.pipeline._drain_batch())
        self.assertEqual(1, self.count("effects"))
        with self.connect() as conn:
            self.assertEqual(["other_actor"], [row[0] for row in conn.execute("SELECT kind FROM effects")])
            row = conn.execute("SELECT events_json, state FROM behavior_write_inbox WHERE batch_id=?", (first.batch_id,)).fetchone()
            self.assertEqual(first.events, json.loads(row["events_json"]))
            self.assertEqual("pending", row["state"])
            conn.execute("UPDATE behavior_write_inbox SET retry_at=NULL")
        self.pipeline._apply_batch = self.apply
        with patch.object(inbox.time, "monotonic", return_value=0):
            self.assertEqual(2, self.pipeline._drain_batch())
        with self.connect() as conn:
            self.assertEqual(["other_actor", "event_0", "following"],
                             [row[0] for row in conn.execute("SELECT kind FROM effects ORDER BY rowid")])

    def test_unknown_group_commit_reconciles_every_receipt_without_repeating_effects(self):
        requests = self.enqueue(3)
        self.fail_commit = True
        with patch.object(inbox.time, "monotonic", return_value=0), self.assertRaises(ConnectionError):
            self.pipeline._drain_batch()
        self.assertEqual(3, self.count("effects"))
        self.assertEqual(0, self.pipeline._drain_batch())
        self.assertEqual(1, self.pipeline.stats()["unknown_commit_count"])
        for request in requests:
            self.assertEqual("synced", self.pipeline.submit(request)["write_mode"])
        self.assertEqual(3, self.count("effects"))

    def test_failure_before_group_commit_retains_all_receipts_and_replays_once(self):
        self.enqueue(3)
        select = self.pipeline._select_next_receipt
        calls = 0

        def interrupted(conn, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise ConnectionError("synthetic connection lost before commit")
            return select(conn, **kwargs)

        with patch.object(self.pipeline, "_select_next_receipt", side_effect=interrupted), patch.object(
            inbox.time, "monotonic", return_value=0,
        ), self.assertRaises(ConnectionError):
            self.pipeline._drain_batch()
        self.assertEqual(0, self.count("effects"))
        with self.connect() as conn:
            self.assertEqual(3, conn.execute("SELECT COUNT(*) FROM behavior_write_inbox WHERE state='pending'").fetchone()[0])
        with patch.object(inbox.time, "monotonic", return_value=0):
            self.assertEqual(3, self.pipeline._drain_batch())
        self.assertEqual(3, self.count("effects"))


class RecorderDatabaseTimeoutTests(unittest.TestCase):
    def test_bounded_settings_preserve_stricter_callers_and_restore_original_values(self):
        class Cursor:
            def fetchone(self):
                return {"lock_timeout": "25ms", "statement_timeout": "30s"}
        class Connection:
            calls = []
            def execute(self, sql, params=()):
                self.calls.append((sql, params))
                return Cursor()
        connection = Connection()
        original = inbox._bounded_timeouts(connection, lock_ms=250, statement_ms=1500)
        self.assertEqual(("25ms", "30s"), original)
        self.assertEqual(("25ms", "1500ms"), connection.calls[-1][1])
        inbox._restore_timeouts(connection, original)
        self.assertEqual(original, connection.calls[-1][1])


if __name__ == "__main__":
    unittest.main()
