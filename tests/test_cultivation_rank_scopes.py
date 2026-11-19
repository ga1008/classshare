"""A material rollout must never rank students from different scoring bases."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from classroom_app.services import learning_progress_service as progress
from tests import test_learning_progress_snapshots as support


class CultivationRankScopeTests(unittest.TestCase):
    def setUp(self):
        fixture = support.LearningProgressSnapshotTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        self.conn = fixture.conn
        self.conn.executescript("""
            CREATE TABLE classes (id INTEGER PRIMARY KEY, name TEXT);
            INSERT INTO classes VALUES(1,'First'),(2,'Second');
            INSERT INTO class_offerings(id,class_id,course_id,teacher_id) VALUES(10,1,1,1);
            INSERT INTO class_offering_class_links(offering_id,class_id,teacher_id) VALUES(10,2,1);
            INSERT INTO students(id,class_id,name,student_id_number,enrollment_status) VALUES
                (1,1,'Alpha','002','active'), (2,2,'Beta','001','active'), (3,2,'Old','003','inactive');
        """)
        self.materials = [{"id": 7}]
        self.scores = {1: 28, 2: 28}
        self.enterContext(patch.object(progress, "get_db_connection", self.connection))
        self.enterContext(patch.object(progress, "load_material_scope", side_effect=lambda *a, **kw: (self.materials, None)))
        self.enterContext(patch.object(progress, "_build_learning_metrics", side_effect=self.metrics))

    @contextmanager
    def connection(self):
        with self.conn:
            yield self.conn

    def metrics(self, _conn, _offering, student):
        metrics = support._metrics(self.scores[student], task=self.scores[student])
        metrics.update(material_scope_key=progress.material_scope_key(self.materials),
                       growth_exponent=progress.growth_curve_exponent())
        return metrics

    def refresh(self, limit):
        return progress.refresh_pending_learning_snapshots(limit=limit, discovery_limit=limit,
            time_budget_seconds=5, discovery_time_budget_seconds=1)

    def test_rollout_waits_for_inactive_viewers_and_ignores_previous_request_score(self):
        self.assertIsNone(progress.build_student_class_position(self.conn, 10, 1))
        self.refresh(2)
        position = progress.build_student_class_position(self.conn, 10, 1, current_score=99)
        self.assertEqual((2, 2, 28), (position["total"], position["current"]["rank"], position["current"]["score"]))
        self.materials.append({"id": 8})
        self.scores[1] = 32
        self.conn.execute("UPDATE cultivation_rank_scopes SET next_check_at='2000'")
        # Neither student visits any page or queues an activity.
        self.assertEqual(1, self.refresh(1)["refreshed"])
        self.assertIsNone(progress.build_student_class_position(self.conn, 10, 1))
        response = progress.serialize_student_learning_progress(self.conn, 10, 2)
        self.assertEqual(("updating", None), (response["class_position_status"], response["class_position"]))
        self.assertEqual(1, self.refresh(1)["refreshed"])
        response = progress.serialize_student_learning_progress(self.conn, 10, 2)
        self.assertEqual(("ready", 2, "Alpha"), (response["class_position_status"],
            response["class_position"]["current"]["rank"], response["class_position"]["leader"]["name"]))
        self.assertEqual(0, self.refresh(2)["seeded"])

    def test_time_boundary_checks_due_lesson_without_login_and_legacy_rows_wait(self):
        now = datetime.now(timezone.utc)
        starts = now + timedelta(seconds=12)
        with patch.object(progress, "load_material_scope", return_value=(self.materials, starts)):
            self.refresh(2)
        row = self.conn.execute("SELECT next_check_at FROM cultivation_rank_scopes").fetchone()
        self.assertEqual(starts.isoformat(timespec="seconds"), row["next_check_at"])
        self.conn.execute("UPDATE learning_progress_snapshots SET rank_basis='' WHERE student_id=2")
        self.assertIsNone(progress.build_student_class_position(self.conn, 10, 1))
        self.assertEqual(1, self.refresh(2)["refreshed"])
        self.assertIsNotNone(progress.build_student_class_position(self.conn, 10, 1))

    def test_scope_weights_and_curve_versions_are_incompatible(self):
        metrics = self.metrics(self.conn, 10, 1)
        expected = progress._snapshot_rank_basis(metrics)
        for changed in ({"material_scope_key": "other"}, {"growth_exponent": 1.01},
                        {"scoring_policy_version": "old"}, {"weight_version": "other"},
                        {"weights": {"material": 40, "task": 40, "interaction": 15, "consistency": 5}}):
            self.assertNotEqual(expected, progress._snapshot_rank_basis({**metrics, **changed}))

    def test_concurrent_explicit_refresh_same_version_and_second_preserves_new_watermarks(self):
        self.refresh(2)
        old = self.metrics(self.conn, 10, 1)
        self.materials.append({"id": 8})
        newer = self.metrics(self.conn, 10, 1)
        # Scores can even be identical: the newly-consumed units must survive.
        newer["material"]["credit_watermarks"] = {"7": 1, "8": 0.5}
        def finish_newer_first(*args):
            with patch.object(progress, "_build_learning_metrics", return_value=newer):
                progress.refresh_student_learning_state(*args)
            return old
        with patch.object(progress, "now_iso", return_value="2030-02-02T02:02:02"), \
             patch.object(progress, "_build_learning_metrics", side_effect=finish_newer_first):
            result = progress.refresh_student_learning_state(self.conn, 10, 1)
        self.assertEqual("stale", result["snapshot_status"])
        snapshot = progress._load_learning_progress_snapshot(self.conn, 10, 1)
        self.assertEqual(progress._snapshot_rank_basis(newer), snapshot["rank_basis"])
        self.assertEqual(newer["material"]["credit_watermarks"], result["metrics"]["material"]["credit_watermarks"])
        self.assertEqual(1, snapshot["dirty"])
