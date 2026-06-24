"""Functional tests for the group-assignment peer-evaluation + blended scoring
lifecycle. Uses a real in-memory SQLite database built from the actual schema
modules so the SQL is exercised end to end."""

import os
import sqlite3
import unittest

os.environ.setdefault("DB_ENGINE", "sqlite")

from classroom_app.db import schema_study_group_scheme as scheme_schema
from classroom_app.db.schema_assignments import ensure_assignment_schema
from classroom_app.db.schema_classroom_activity import ensure_classroom_activity_schema
from classroom_app.services import group_assignment_service as ga


ASSIGNMENT_ID = "1"
CLASS_OFFERING_ID = 100
SCHEME_ID = 500


class GroupAssignmentLifecycleTests(unittest.TestCase):
    def setUp(self):
        # Reset the module-level schema-ready guard so the engine-aware DDL runs
        # against this fresh in-memory connection.
        scheme_schema._SCHEMA_READY = False
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        ensure_assignment_schema(self.conn)
        ensure_classroom_activity_schema(self.conn)
        scheme_schema.ensure_study_group_scheme_schema(self.conn)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY,
                name TEXT,
                student_id_number TEXT,
                avatar_file_hash TEXT,
                enrollment_status TEXT DEFAULT 'active'
            )
            """
        )
        self.conn.execute(
            "INSERT INTO assignments (id, course_id, title) VALUES (1, 1, '小组项目作业')"
        )
        self.conn.execute(
            """
            INSERT INTO group_schemes (id, class_offering_id, name, status, created_by_teacher_id)
            VALUES (?, ?, '随机分组', 'active', 7)
            """,
            (SCHEME_ID, CLASS_OFFERING_ID),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    # -- helpers -----------------------------------------------------------
    def _add_student(self, sid, name):
        self.conn.execute(
            "INSERT INTO students (id, name, student_id_number) VALUES (?, ?, ?)",
            (sid, name, f"S{sid}"),
        )

    def _make_group(self, group_id, index, member_ids):
        self.conn.execute(
            """
            INSERT INTO study_groups (id, class_offering_id, name, status, join_policy,
                                      max_members, created_by_role, created_by_user_pk,
                                      scheme_id, group_index)
            VALUES (?, ?, ?, 'active', 'scheme_random', 6, 'teacher', 7, ?, ?)
            """,
            (group_id, CLASS_OFFERING_ID, f"第 {index} 组", SCHEME_ID, index),
        )
        for sid in member_ids:
            self.conn.execute(
                """
                INSERT INTO study_group_members (group_id, student_id, member_role, status)
                VALUES (?, ?, 'member', 'active')
                """,
                (group_id, sid),
            )
        self.conn.commit()

    def _submit_and_grade(self, sid, score, status="graded"):
        cur = self.conn.execute(
            """
            INSERT INTO submissions (assignment_id, student_pk_id, student_name, status, score, submitted_at)
            VALUES (?, ?, ?, ?, ?, '2026-06-24T10:00:00')
            """,
            (ASSIGNMENT_ID, sid, f"name{sid}", status, score),
        )
        self.conn.commit()
        return cur.lastrowid

    def _bind(self):
        ga.bind_assignment_to_scheme(
            self.conn,
            assignment_id=ASSIGNMENT_ID,
            class_offering_id=CLASS_OFFERING_ID,
            scheme_id=SCHEME_ID,
            teacher_id=7,
        )
        self.conn.commit()

    def _member_result(self, sid):
        return ga._load_member_result(self.conn, ASSIGNMENT_ID, sid)

    # -- tests -------------------------------------------------------------
    def test_binding_roundtrip(self):
        self._bind()
        self.assertTrue(ga.is_group_assignment(self.conn, ASSIGNMENT_ID))
        binding = ga.get_assignment_group_binding(self.conn, ASSIGNMENT_ID)
        self.assertEqual(int(binding["scheme_id"]), SCHEME_ID)
        ga.unbind_assignment(self.conn, assignment_id=ASSIGNMENT_ID)
        self.conn.commit()
        self.assertFalse(ga.is_group_assignment(self.conn, ASSIGNMENT_ID))

    def test_binding_rejects_foreign_scheme(self):
        with self.assertRaises(ValueError):
            ga.bind_assignment_to_scheme(
                self.conn,
                assignment_id=ASSIGNMENT_ID,
                class_offering_id=CLASS_OFFERING_ID + 1,  # mismatched offering
                scheme_id=SCHEME_ID,
                teacher_id=7,
            )

    def test_blended_score_and_gating(self):
        for sid, name in [(1, "甲"), (2, "乙"), (3, "丙")]:
            self._add_student(sid, name)
        self._make_group(900, 1, [1, 2, 3])
        self._bind()

        # Student 1 submits + graded (90). Group not finalized yet -> pending.
        sub1 = self._submit_and_grade(1, 90)
        ga.record_member_work_score(self.conn, sub1)
        self.conn.commit()
        state1 = ga.get_student_display_state(self.conn, ASSIGNMENT_ID, 1)
        self.assertTrue(state1["is_group"])
        self.assertTrue(state1["pending"])
        self.assertFalse(state1["revealed"])
        self.assertIsNone(state1["final_score"])

        # Student 1 rates teammates explicitly; 2 & 3 are filled by defaults.
        ga.submit_peer_contributions(
            self.conn,
            assignment_id=ASSIGNMENT_ID,
            reviewer_id=1,
            ratings={2: 20, 3: 10},
        )
        self.conn.commit()

        # Students 2 and 3 submit + graded.
        sub2 = self._submit_and_grade(2, 80)
        ga.record_member_work_score(self.conn, sub2)
        self.conn.commit()
        # Still pending (3 not graded).
        self.assertFalse(self._member_result(1)["revealed"])

        sub3 = self._submit_and_grade(3, 70)
        ga.record_member_work_score(self.conn, sub3)
        self.conn.commit()

        # Now finalized: every member revealed.
        for sid in (1, 2, 3):
            res = self._member_result(sid)
            self.assertEqual(int(res["revealed"]), 1)

        # Student 1: work 90, received from 2 and 3. 2 never rated -> default 16,
        # 3 never rated -> default 16. peer_avg = 16. final = 90*0.8 + 16 = 88.0
        res1 = self._member_result(1)
        self.assertAlmostEqual(res1["peer_avg"], 16.0, places=2)
        self.assertAlmostEqual(res1["final_score"], 88.0, places=2)

        # Student 2 received 20 from student 1, 16 default from 3 -> avg 18.
        # final = 80*0.8 + 18 = 82.0
        res2 = self._member_result(2)
        self.assertAlmostEqual(res2["peer_avg"], 18.0, places=2)
        self.assertAlmostEqual(res2["final_score"], 82.0, places=2)

        # Student 3 received 10 from student 1, 16 default from 2 -> avg 13.
        # final = 70*0.8 + 13 = 69.0
        res3 = self._member_result(3)
        self.assertAlmostEqual(res3["peer_avg"], 13.0, places=2)
        self.assertAlmostEqual(res3["final_score"], 69.0, places=2)

        # submissions.score overwritten with the blended final.
        score1 = self.conn.execute(
            "SELECT score FROM submissions WHERE id = ?", (sub1,)
        ).fetchone()["score"]
        self.assertAlmostEqual(score1, 88.0, places=2)

        # Reveal state for student 1 now shows the final, no peer detail leaked.
        final_state = ga.get_student_display_state(self.conn, ASSIGNMENT_ID, 1)
        self.assertTrue(final_state["revealed"])
        self.assertAlmostEqual(final_state["final_score"], 88.0, places=2)
        self.assertFalse(final_state["pending"])

    def test_solo_group_uses_default_peer_points(self):
        self._add_student(1, "独行")
        self._make_group(901, 1, [1])
        self._bind()
        sub1 = self._submit_and_grade(1, 90)
        result = ga.record_member_work_score(self.conn, sub1)
        self.conn.commit()
        self.assertTrue(result["finalized"])
        res1 = self._member_result(1)
        # No peers -> peer_avg = default 16. final = 90*0.8 + 16 = 88.0
        self.assertAlmostEqual(res1["peer_avg"], 16.0, places=2)
        self.assertAlmostEqual(res1["final_score"], 88.0, places=2)

    def test_score_is_clamped_to_100(self):
        self.assertEqual(ga.compute_final_score(100, 20), 100.0)
        self.assertEqual(ga.compute_final_score(100, 25), 100.0)  # clamp
        self.assertEqual(ga.compute_final_score(50, 16), 56.0)

    def test_explicit_rating_not_overwritten_by_default(self):
        for sid, name in [(1, "甲"), (2, "乙")]:
            self._add_student(sid, name)
        self._make_group(902, 1, [1, 2])
        self._bind()
        ga.submit_peer_contributions(
            self.conn, assignment_id=ASSIGNMENT_ID, reviewer_id=1, ratings={2: 5}
        )
        self.conn.commit()
        # Defaulting again must NOT overwrite the explicit 5.
        ga.ensure_default_peer_contributions(
            self.conn, assignment_id=ASSIGNMENT_ID, group_id=902, reviewer_id=1
        )
        self.conn.commit()
        row = self.conn.execute(
            """
            SELECT contribution_points, is_auto_default FROM peer_reviews
            WHERE group_id = 902 AND reviewer_student_id = 1 AND reviewee_student_id = 2
            """
        ).fetchone()
        self.assertEqual(int(row["contribution_points"]), 5)
        self.assertEqual(int(row["is_auto_default"]), 0)


if __name__ == "__main__":
    unittest.main()
