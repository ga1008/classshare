"""Shared six-axis ability radar: per-classroom comparison, privacy floor, cross-course pairing, rendering."""
import json
import sqlite3
import unittest
from unittest.mock import patch

from jinja2 import Environment, FileSystemLoader

from classroom_app.services import ability_radar_service as radar


def _metrics(material, completion=0.5, score_ratio=0.5, graded=1, interaction=0.3, consistency=0.3):
    return json.dumps({
        "material": {"required_count": 4, "ratio": material},
        "assignments": {"assignment_count": 3, "completion_ratio": completion, "score_ratio": score_ratio, "graded_count": graded},
        "interactions": {"interaction_ratio": interaction, "consistency_ratio": consistency},
    })


class AbilityRadarTests(unittest.TestCase):
    def setUp(self):
        radar.clear_peer_cache()
        self.addCleanup(radar.clear_peer_cache)
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.execute("CREATE TABLE students (id INTEGER PRIMARY KEY, class_id INTEGER, enrollment_status TEXT DEFAULT 'active')")
        self.conn.execute("CREATE TABLE learning_progress_snapshots (class_offering_id INTEGER, student_id INTEGER, score REAL, metrics_json TEXT)")
        # Class 10: students 1-4 (offering 1). Offering 2 is taken by student 1 plus students 5 and 6 only.
        self.conn.executemany("INSERT INTO students (id, class_id) VALUES (?, ?)", [(1, 10), (2, 10), (3, 10), (4, 10), (5, 20), (6, 20)])
        self.conn.executemany("INSERT INTO learning_progress_snapshots VALUES (?, ?, ?, ?)", [
            (1, 1, 60, _metrics(0.8, completion=1.0, score_ratio=0.9, graded=2)),
            (1, 2, 40, _metrics(0.4, score_ratio=0.7)),
            (1, 3, 20, _metrics(0.2, score_ratio=0.5)),
            (1, 4, 30, _metrics(0.6, graded=0)),
            (2, 1, 30, _metrics(0.6)),
            (2, 5, 10, _metrics(0.2)),
            (2, 6, 50, _metrics(0.4)),
        ])
        classes = {1: [10], 2: [10, 20]}
        patcher = patch.object(radar, "offering_class_ids", lambda conn, offering_id: classes.get(int(offering_id), []))
        patcher.start()
        self.addCleanup(patcher.stop)
        # Offering 2 really only has students 1, 5, 6 with snapshots; 2-4 have none there.

    def _axis(self, result, key):
        return next(axis for axis in result["axes"] if axis["key"] == key)

    def test_peer_mean_excludes_the_student(self):
        result = radar.build_ability_radar(self.conn, student_id=1, offering_ids=[1])
        material = self._axis(result, "material")
        self.assertEqual(80, material["score"])
        self.assertEqual(40, material["average"])  # (40 + 20 + 60) / 3, student 1 excluded
        self.assertEqual(40, material["delta"])

    def test_small_peer_groups_never_reveal_a_classmate(self):
        # Offering 2 has only two other students with snapshots: no peer value at all.
        peers = radar.peer_average(self.conn, 2, exclude_student_id=1)
        self.assertFalse(peers["available"])
        # 成绩质量 in offering 1: only two other students are graded -> hidden too.
        result = radar.build_ability_radar(self.conn, student_id=1, offering_ids=[1])
        self.assertIsNone(self._axis(result, "quality")["average"])
        self.assertEqual(90, self._axis(result, "quality")["score"])

    def test_cross_course_gap_uses_only_courses_with_both_values(self):
        result = radar.build_ability_radar(self.conn, student_id=1, offering_ids=[1, 2])
        material = self._axis(result, "material")
        self.assertEqual(70, material["score"])     # (80 + 60) / 2 over both courses
        self.assertEqual(40, material["delta"])     # only offering 1 can be compared
        self.assertEqual(30, material["average"])   # drawn at score - gap
        self.assertEqual(2, result["course_count"])

    def test_no_snapshot_means_not_available(self):
        self.assertFalse(radar.build_ability_radar(self.conn, student_id=99, offering_ids=[1, 2])["available"])

    def test_fresh_self_state_overrides_snapshot(self):
        fresh = {1: {"metrics": json.loads(_metrics(1.0, 1.0, 1.0, 3, 1.0, 1.0)), "score": 90}}
        result = radar.build_ability_radar(self.conn, student_id=1, offering_ids=[1], self_states=fresh)
        self.assertEqual(100, self._axis(result, "material")["score"])
        self.assertEqual(90, self._axis(result, "cultivation")["score"])

    def test_missing_axes_never_draw_as_zero(self):
        result = radar.build_ability_radar(self.conn, student_id=1, offering_ids=[1])
        quality = self._axis(result, "quality")
        self.assertIsNone(quality["average"])
        # The peer outline borrows the student's own value on that axis instead of collapsing to 0.
        peer_points = result["average_points"].split()
        self.assertNotEqual(f"{radar.CENTER:.1f},{radar.CENTER:.1f}", peer_points[2])

    def test_strength_and_focus_use_peer_gaps(self):
        result = radar.build_ability_radar(self.conn, student_id=1, offering_ids=[1])
        self.assertIsNotNone(result["strength"])
        self.assertIsNotNone(result["focus"])
        self.assertGreaterEqual(result["strength"]["delta"], result["focus"]["delta"])

    def test_partial_renders_through_page_style_include(self):
        result = radar.build_ability_radar(self.conn, student_id=1, offering_ids=[1, 2])
        env = Environment(loader=FileSystemLoader("templates"))
        page = env.from_string(
            "{% set ability_radar = data %}{% set radar_self_label = '本人' %}{% include 'partials/ability_radar.html' %}"
        )
        html = page.render(data=result)
        self.assertIn('class="lq-radar__self"', html)
        self.assertIn('class="lq-radar__average"', html)
        self.assertEqual(6, html.count('<li class="lq-radar__axis'))
        self.assertIn("综合 2 门课堂", html)
        self.assertNotIn("None", html)
        empty = env.get_template("partials/ability_radar.html").render(ability_radar={"available": False})
        self.assertIn("lq-radar__empty", empty)


if __name__ == "__main__":
    unittest.main()
