"""隐藏修为, avatar emblems and the 宗门实力排行 天骄 rule (in-memory sqlite, synthetic data)."""
import sqlite3
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from classroom_app.services import cultivation_display_service as display
from classroom_app.services import learning_progress_service as service


def _conn():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE learning_certificates (id INTEGER PRIMARY KEY AUTOINCREMENT, class_offering_id INTEGER, student_id INTEGER, tier INTEGER)"
    )
    conn.execute(
        "CREATE TABLE learning_progress_snapshots (class_offering_id INTEGER, student_id INTEGER, score REAL, metrics_json TEXT)"
    )
    return conn


class CultivationDisplayTests(unittest.TestCase):
    def setUp(self):
        display.invalidate_emblem_cache()
        self.conn = _conn()
        self.addCleanup(self.conn.close)
        self.addCleanup(display.invalidate_emblem_cache)
        rows = [
            # student 1: tier 3 in offering 10, tier 5 in offering 11 -> highest is tier 5 / offering 11
            (10, 1, 1), (10, 1, 2), (10, 1, 3), (11, 1, 5),
            (10, 2, 1),
            (10, 3, 2),
        ]
        self.conn.executemany("INSERT INTO learning_certificates (class_offering_id, student_id, tier) VALUES (?, ?, ?)", rows)
        self.conn.executemany(
            "INSERT INTO learning_progress_snapshots VALUES (?, ?, ?, ?)",
            [
                (10, 1, 40.0, '{"growth_exponent": 1.45}'),
                (11, 1, 61.2, '{"growth_exponent": 1.45}'),
                (10, 2, 20.0, '{"score": 20}'),  # legacy linear snapshot
            ],
        )

    def test_highest_level_across_classrooms_with_its_score(self):
        emblems = display.load_student_emblems(self.conn, [1, 2, 4])
        self.assertEqual("golden_core", emblems[1]["key"])
        self.assertEqual(5, emblems[1]["tier"])
        self.assertEqual(61.2, emblems[1]["score"])
        self.assertEqual("/static/img/cultivation/emblems/golden_core.svg", emblems[1]["emblem_url"])
        from classroom_app.services.cultivation_growth_service import apply_growth_curve
        self.assertEqual(apply_growth_curve(20.0), emblems[2]["score"])  # legacy score shown on the curve
        self.assertNotIn(4, emblems)  # no certificate -> no emblem

    def test_hidden_student_has_no_emblem_and_cache_invalidates(self):
        self.assertIn(1, display.load_student_emblems(self.conn, [1]))
        display.set_cultivation_hidden(self.conn, 1, True)
        self.assertTrue(display.is_cultivation_hidden(self.conn, 1))
        self.assertNotIn(1, display.load_student_emblems(self.conn, [1]))
        display.set_cultivation_hidden(self.conn, 1, False)
        self.assertIn(1, display.load_student_emblems(self.conn, [1]))

    def _position(self, viewer_id):
        rows = [
            {"id": 1, "name": "甲", "student_id_number": "001", "score": 80.0, "is_self": viewer_id == 1},
            {"id": 2, "name": "乙", "student_id_number": "002", "score": 60.0, "is_self": viewer_id == 2},
            {"id": 3, "name": "丙", "student_id_number": "003", "score": 40.0, "is_self": viewer_id == 3},
        ]
        with patch.object(service, "_class_learning_score_rows", return_value=rows):
            return service.build_student_class_position(self.conn, 10, viewer_id)

    def test_top_student_is_tianjiao_with_course_level(self):
        position = self._position(3)
        leader = position["leader"]
        self.assertEqual("甲", leader["name"])
        self.assertTrue(leader["is_tianjiao"])
        self.assertEqual("foundation", leader["level"]["key"])  # tier 3 in this classroom, not global tier 5
        self.assertEqual([1, 2, 3], [row["rank"] for row in position["ranking"]])
        self.assertEqual(0, position["hidden_count"])

    def test_hidden_tianjiao_yields_second_place_without_title(self):
        display.set_cultivation_hidden(self.conn, 1, True)
        position = self._position(3)
        leader = position["leader"]
        self.assertEqual("乙", leader["name"])
        self.assertEqual(2, leader["rank"])
        self.assertFalse(leader["is_tianjiao"])
        self.assertNotIn("甲", [row["name"] for row in position["ranking"]])
        self.assertEqual(1, position["hidden_count"])
        self.assertFalse(position["self_hidden"])

    def test_hidden_scores_never_reach_the_mountain(self):
        display.set_cultivation_hidden(self.conn, 1, True)
        position = self._position(3)
        scores = [point["score"] for point in position["mountain"]["points"]]
        self.assertNotIn(80.0, scores)
        self.assertEqual(60.0, position["mountain"]["max_score"])
        self.assertEqual(2, len(scores))

    def test_everyone_else_hidden_exposes_nobody(self):
        display.set_cultivation_hidden(self.conn, 1, True)
        display.set_cultivation_hidden(self.conn, 2, True)
        position = self._position(3)
        self.assertEqual("丙", position["leader"]["name"])
        self.assertEqual(40.0, position["leader"]["score"])
        self.assertEqual([40.0], [point["score"] for point in position["mountain"]["points"]])

    def test_hide_applies_even_with_a_warm_cache(self):
        self.assertIn(1, display.load_student_emblems(self.conn, [1]))  # cache now warm
        self.conn.execute("INSERT OR REPLACE INTO cultivation_display_settings VALUES (1, 1, 'x')")
        self.assertNotIn(1, display.load_student_emblems(self.conn, [1]))

    def test_malformed_ids_are_ignored(self):
        self.assertEqual({}, display.load_student_emblems(self.conn, ["²", "1" * 30, -1, None, "abc"]))

    def test_viewer_who_hides_still_sees_own_rank(self):
        display.set_cultivation_hidden(self.conn, 3, True)
        position = self._position(3)
        self.assertTrue(position["self_hidden"])
        self.assertEqual(3, position["current"]["rank"])
        self.assertNotIn("丙", [row["name"] for row in position["ranking"]])


class _ConnContext:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, *exc):
        return False


class CultivationDisplayRouteTests(unittest.TestCase):
    def setUp(self):
        from classroom_app.dependencies import get_current_user
        from classroom_app.routers import cultivation_display

        display.invalidate_emblem_cache()
        self.conn = _conn()
        self.conn.execute("INSERT INTO learning_certificates (class_offering_id, student_id, tier) VALUES (10, 7, 2)")
        self.user = {"role": "student", "id": 7}
        app = FastAPI()
        app.include_router(cultivation_display.router)
        app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(app)
        patcher = patch.object(cultivation_display, "get_db_connection", lambda: _ConnContext(self.conn))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(display.invalidate_emblem_cache)

    def test_self_token_resolves_and_toggle_hides(self):
        data = self.client.get("/api/cultivation/emblems?students=self,abc,999").json()
        self.assertEqual(7, data["self_id"])
        self.assertEqual("qi_refining", data["items"]["7"]["key"])
        self.assertNotIn("999", data["items"])
        response = self.client.put("/api/profile/cultivation-visibility", json={"hidden": True})
        self.assertEqual(200, response.status_code)
        self.assertTrue(self.client.get("/api/profile/cultivation-visibility").json()["hidden"])
        self.assertEqual({}, self.client.get("/api/cultivation/emblems?students=7").json()["items"])

    def test_bad_payload_and_teacher_are_rejected(self):
        self.assertEqual(400, self.client.put("/api/profile/cultivation-visibility", json={"hidden": "yes"}).status_code)
        self.user = {"role": "teacher", "id": 3}
        self.assertEqual(403, self.client.put("/api/profile/cultivation-visibility", json={"hidden": True}).status_code)
        self.assertFalse(self.client.get("/api/profile/cultivation-visibility").json()["applicable"])


if __name__ == "__main__":
    unittest.main()
