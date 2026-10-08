"""Physical identity, ordinal projection and offline repair on synthetic SQLite."""
from __future__ import annotations

import copy
import json
import sqlite3
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from classroom_app.db.schema_academic_schedule_predictions import ensure_academic_schedule_prediction_schema
from classroom_app.services import offering_session_resequence_service as reseq
from classroom_app.services.academic_schedule_prediction_service import (
    claim_schedule_sync, release_schedule_sync, reconcile_and_publish_snapshot, load_teacher_prediction_snapshot,
)
from classroom_app.services.schedule_learning_repair_service import repair_stored_schedule_snapshot
from test_academic_schedule_predictions import SCHEMA, base_snapshot, official, request, slot


class ChronologicalResequenceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.execute("ALTER TABLE class_offering_sessions ADD COLUMN schedule_metadata_json TEXT DEFAULT '{}'")
        self.conn.execute("CREATE UNIQUE INDEX session_ordinal ON class_offering_sessions(class_offering_id,order_index)")
        ensure_academic_schedule_prediction_schema(self.conn)
        self.conn.commit()
        self.now = datetime(2026, 9, 19, 2, tzinfo=timezone.utc)
        self.addCleanup(self.conn.close)

    def publish(self, snapshot):
        lease = claim_schedule_sync(self.conn, 1, now=self.now)
        self.conn.commit()
        result = reconcile_and_publish_snapshot(self.conn, 1, 1, snapshot, lease["token"], now=self.now)
        release_schedule_sync(self.conn, 1, lease["token"])
        self.conn.commit()
        return result

    def snapshot(self):
        return load_teacher_prediction_snapshot(self.conn, 1, 1)

    def rows(self):
        return [dict(row) for row in self.conn.execute("SELECT * FROM class_offering_sessions ORDER BY id")]

    def approved(self):
        snapshot = base_snapshot([request(status="approved")])
        snapshot["official"][0] = official("2026-10-11")
        return snapshot

    def legacy_exchange(self):
        self.publish(base_snapshot([request()]))
        previous = {101: slot("2026-10-11"), 102: slot("2026-09-26", (8, 9)), 103: slot("2026-09-27")}
        exchanged = {101: previous[102], 102: previous[103], 103: previous[101]}
        for sid in previous:
            target = exchanged[sid]
            meta = {"resequence_history": [{"at": "old", "from": previous[sid], "to": target, "note": "legacy"}],
                    "section_text": "-".join(map(str, target["sections"]))}
            self.conn.execute("UPDATE class_offering_sessions SET session_date=?,academic_section_text=?,week_index=?,weekday=?,schedule_metadata_json=? WHERE id=?",
                              (target["date"], meta["section_text"], target["week"], target["weekday"] - 1, json.dumps(meta), sid))
            self.conn.execute("UPDATE academic_schedule_session_bindings SET current_json=?,evidence='resequenced_after_adjustment' WHERE session_id=?",
                              (json.dumps({**target, "schedule_status": "scheduled"}), sid))
        self.conn.commit()

    def test_historical_dates_reindex_and_second_approval_replay_is_idempotent(self):
        self.publish(base_snapshot([request()]))
        self.now = datetime(2027, 1, 1, tzinfo=timezone.utc)
        self.publish(self.approved())
        after = self.rows()
        self.assertEqual([(101, 3, "2026-10-11", 53), (102, 1, "2026-09-26", 51), (103, 2, "2026-09-27", 52)],
                         [(row["id"], row["order_index"], row["session_date"], row["learning_material_id"]) for row in after if row["class_offering_id"] == 10])
        self.publish(self.approved())
        self.assertEqual(after, self.rows())
        self.assertEqual([(102, 1, 3), (103, 2, 3), (101, 3, 3)],
                         [(row["session_id"], row["session_no"], row["session_total"]) for row in self.snapshot()["lessons"]])

    def test_same_day_full_period_tuple_and_ties_are_deterministic(self):
        rows = reseq.load_offering_sessions(self.conn, 10)
        for row, periods in zip(rows, ("8-9", "2,4", "2-3")):
            row.update(session_date="2026-09-20", academic_section_text=periods)
        plan = reseq.plan_offering_resequence(None, 10, sessions=rows)
        self.assertEqual([103, 102, 101], [row["session_id"] for row in plan["assignments"]])
        for row in rows:
            row["academic_section_text"] = "2-3"
        self.assertEqual([101, 102, 103], [row["session_id"] for row in reseq.plan_offering_resequence(None, 10, sessions=list(reversed(rows)))["assignments"]])

    def test_manual_dated_sessions_without_periods_remain_numbered(self):
        self.conn.execute("UPDATE class_offering_sessions SET academic_section_text='',session_date='2026-09-20' WHERE class_offering_id=10")
        plan = reseq.plan_offering_resequence(self.conn, 10)
        self.assertEqual(3, plan["active_count"])
        self.assertEqual([101, 102, 103], [row["session_id"] for row in plan["assignments"]])

    def test_cancelled_and_undated_keep_bindings_outside_active_number_range(self):
        self.conn.execute("UPDATE class_offering_sessions SET schedule_status='cancelled' WHERE id=101")
        self.conn.execute("UPDATE class_offering_sessions SET session_date='' WHERE id=102")
        plan = reseq.plan_offering_resequence(self.conn, 10)
        reseq.apply_offering_resequence(self.conn, plan)
        self.assertEqual([(103, 1, True), (101, 2, False), (102, 3, False)],
                         [(row["session_id"], row["order_index"], row["active"]) for row in plan["assignments"]])
        self.assertEqual([51, 52, 53], [row["learning_material_id"] for row in self.rows() if row["class_offering_id"] == 10])

    def test_stale_plan_rejects_without_partial_ordinal_or_material_changes(self):
        self.conn.execute("UPDATE class_offering_sessions SET session_date='2026-10-11' WHERE id=101")
        plan = reseq.plan_offering_resequence(self.conn, 10)
        self.conn.execute("UPDATE class_offering_sessions SET academic_location='manual' WHERE id=102")
        before = self.rows()
        with self.assertRaisesRegex(ValueError, "已变化"):
            reseq.apply_offering_resequence(self.conn, plan)
        self.assertEqual(before, self.rows())

    def test_material_failure_rolls_back_dates_ordinals_and_snapshot_together(self):
        self.publish(base_snapshot())
        before, snapshot = self.rows(), self.snapshot()
        lease = claim_schedule_sync(self.conn, 1, now=self.now)
        self.conn.commit()
        with patch("classroom_app.services.session_learning_materials_service.rebind_offering_materials_for_resequence", side_effect=RuntimeError("material failure")):
            with self.assertRaisesRegex(RuntimeError, "material failure"):
                reconcile_and_publish_snapshot(self.conn, 1, 1, self.approved(), lease["token"], now=self.now)
        self.conn.commit()
        self.assertEqual(before, self.rows())
        self.assertEqual(snapshot["lessons"], self.snapshot()["lessons"])
        self.assertEqual(snapshot["sync_state"]["revision"], self.snapshot()["sync_state"]["revision"])

    def test_verified_legacy_date_exchange_recovers_physical_ids_then_numbers(self):
        self.legacy_exchange()
        self.publish(self.approved())
        self.assertEqual([(101, 3, "2026-10-11"), (102, 1, "2026-09-26"), (103, 2, "2026-09-27")],
                         [(row["id"], row["order_index"], row["session_date"]) for row in self.rows() if row["class_offering_id"] == 10])
        self.assertIn("legacy_resequence_repaired", {row["code"] for row in self.snapshot()["warnings"]})
        after = self.rows()
        self.publish(self.approved())
        self.assertEqual(after, self.rows())

    def test_legacy_migration_does_not_overwrite_subsequent_manual_date(self):
        self.legacy_exchange()
        self.conn.execute("UPDATE class_offering_sessions SET session_date='2026-09-28' WHERE id=102")
        self.conn.commit()
        self.publish(self.approved())
        self.assertEqual("2026-09-28", self.conn.execute("SELECT session_date FROM class_offering_sessions WHERE id=102").fetchone()[0])
        self.assertIn("legacy_resequence_unresolved", {row["code"] for row in self.snapshot()["warnings"]})
        self.assertIn("local_schedule_conflict", {row["code"] for row in self.snapshot()["warnings"]})

    def test_any_apply_caller_refreshes_persisted_snapshot_ordinals(self):
        self.publish(base_snapshot())
        self.conn.execute("UPDATE class_offering_sessions SET session_date='2026-10-11' WHERE id=101")
        reseq.apply_offering_resequence(self.conn, reseq.plan_offering_resequence(self.conn, 10))
        self.assertEqual({101: 3, 102: 1, 103: 2}, {row["session_id"]: row["session_no"] for row in self.snapshot()["lessons"]})

    def test_offline_repair_is_rollbackable_and_preserves_network_state(self):
        self.publish(base_snapshot([request()]))
        before, snapshot = self.rows(), copy.deepcopy(self.snapshot())
        state = dict(self.conn.execute("SELECT * FROM teacher_academic_schedule_sync_state WHERE teacher_id=1").fetchone())
        report = repair_stored_schedule_snapshot(self.conn, 1, 1, now=self.now)
        self.assertTrue(self.conn.in_transaction)
        self.assertEqual((1, 1), (report["teacher_id"], report["source_revision"]))
        self.assertEqual(state, dict(self.conn.execute("SELECT * FROM teacher_academic_schedule_sync_state WHERE teacher_id=1").fetchone()))
        self.conn.rollback()
        self.assertEqual(before, self.rows())
        self.assertEqual(snapshot, self.snapshot())

    def test_offline_tool_dry_run_reports_only_ids_counts_and_rolls_back(self):
        from tools.repair_schedule_learning_bindings import run_repair
        self.publish(base_snapshot())
        before = self.rows()
        report = run_repair(self.conn, teacher_id=1, include_repositories=False, now=self.now)
        self.assertEqual((1, 1, 0), (report["teacher_count"], report["semester_count"], report["repository_count"]))
        self.assertNotIn("不可覆盖", json.dumps(report, ensure_ascii=False))
        self.conn.rollback()
        self.assertEqual(before, self.rows())

    def test_manual_repair_rejects_wrong_teacher_and_selected_term(self):
        from classroom_app.services.schedule_editor_service import ScheduleEditError, apply_resequence_by_dates
        before = self.rows()
        for teacher, semester in ((2, 1), (1, 3)):
            with self.assertRaises(ScheduleEditError):
                apply_resequence_by_dates(self.conn, teacher, {"selected_term": {"semester_id": semester}}, offering_id=10)
        self.assertEqual(before, self.rows())

    def test_offline_repair_retains_existing_warning_evidence(self):
        self.publish(base_snapshot())
        self.conn.execute("UPDATE teacher_academic_schedule_snapshots SET warnings_json=? WHERE teacher_id=1 AND semester_id=1",
                          (json.dumps([{"code": "previous_anomaly", "message": "需核对"}]),))
        self.conn.commit()
        result = repair_stored_schedule_snapshot(self.conn, 1, 1, now=self.now)
        self.assertIn("previous_anomaly", {item["code"] for item in result["warnings"]})
        self.assertIn("previous_anomaly", {item["code"] for item in self.snapshot()["warnings"]})


class OfflineRepositoryRepairTests(unittest.TestCase):
    def test_existing_assigned_tree_is_projected_without_network_and_rollbackable(self):
        from test_git_learning_bindings_service import GitLearningBindingsTests
        from tools.repair_schedule_learning_bindings import run_repair
        fixture = GitLearningBindingsTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        conn = fixture.conn
        conn.execute("ALTER TABLE course_materials ADD COLUMN git_repo_status TEXT DEFAULT 'plain'")
        conn.execute("UPDATE course_materials SET git_repo_status='repository' WHERE id=200")
        conn.commit()
        before = fixture.mapping()
        with patch("subprocess.run", side_effect=AssertionError("no subprocess allowed")):
            report = run_repair(conn, teacher_id=1)
        self.assertEqual((1, 4), (report["repository_count"], report["repositories"][0]["total_assignments"]))
        self.assertEqual([(10, 1, 301), (20, 2, 302), (30, 3, 303), (40, 4, 304)], fixture.mapping())
        conn.rollback()
        self.assertEqual(before, fixture.mapping())


if __name__ == "__main__":
    unittest.main()
