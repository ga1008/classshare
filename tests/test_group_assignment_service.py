"""Functional tests for the group-assignment peer-evaluation + blended scoring
lifecycle. Uses a real in-memory SQLite database built from the actual schema
modules so the SQL is exercised end to end."""

import os
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event

os.environ.setdefault("DB_ENGINE", "sqlite")

from classroom_app.db import schema_study_group_scheme as scheme_schema
from classroom_app.db import schema_ai_jobs
from classroom_app.db.schema_assignments import ensure_assignment_schema
from classroom_app.db.schema_classroom_activity import ensure_classroom_activity_schema
from classroom_app.services import group_assignment_service as ga


ASSIGNMENT_ID = "1"
CLASS_OFFERING_ID = 100
SCHEME_ID = 500


class GroupAssignmentLifecycleTests(unittest.TestCase):
    def setUp(self):
        self._previous_ai_ready = set(schema_ai_jobs._SCHEMA_READY_ENGINES)
        self._previous_group_ready = scheme_schema._SCHEMA_READY
        # Reset the module-level schema-ready guard so the engine-aware DDL runs
        # against this fresh in-memory connection.
        scheme_schema._SCHEMA_READY = False
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        ensure_assignment_schema(self.conn)
        schema_ai_jobs._SCHEMA_READY_ENGINES.clear()
        schema_ai_jobs.ensure_ai_job_schema(self.conn)
        ensure_classroom_activity_schema(self.conn)
        scheme_schema.ensure_study_group_scheme_schema(self.conn)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY,
                name TEXT,
                student_id_number TEXT,
                email TEXT,
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
        schema_ai_jobs._SCHEMA_READY_ENGINES = self._previous_ai_ready
        scheme_schema._SCHEMA_READY = self._previous_group_ready

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

    def test_finished_scheme_can_be_reused_by_a_later_assignment(self):
        from classroom_app.routers.homework_parts.group_assignments import _scheme_options

        for sid in (1, 2):
            self._add_student(sid, f"成员{sid}")
        self._make_group(900, 1, [1, 2])
        self.conn.execute("UPDATE group_schemes SET status = 'closed' WHERE id = ?", (SCHEME_ID,))
        self.conn.executemany(
            """INSERT INTO group_schemes (id, class_offering_id, name, status, created_by_teacher_id)
               VALUES (?, ?, '另一方案', 'closed', 7)""",
            [(501, CLASS_OFFERING_ID), (502, CLASS_OFFERING_ID + 1)],
        )
        self.assertEqual(
            [(s["id"], s["is_history"], s["assigned_count"]) for s in _scheme_options(self.conn, CLASS_OFFERING_ID)],
            [(SCHEME_ID, True, 2)],
        )
        with self.assertRaisesRegex(ValueError, "没有成员"):
            ga.bind_assignment_to_scheme(self.conn, assignment_id=ASSIGNMENT_ID,
                class_offering_id=CLASS_OFFERING_ID, scheme_id=501, teacher_id=7)
        self._bind()
        self.assertEqual(ga.get_student_group_context(self.conn, ASSIGNMENT_ID, 1)["peers"][0]["student_id"], 2)

    def test_configuration_changes_cannot_reinterpret_existing_work(self):
        for sid in (1, 2, 3):
            self._add_student(sid, f"成员{sid}")
        self._make_group(900, 1, [1, 2])
        self._make_group(901, 2, [3])
        self.conn.execute(
            """INSERT INTO group_schemes (id, class_offering_id, name, status, created_by_teacher_id)
               VALUES (501, ?, '另一方案', 'active', 7)""", (CLASS_OFFERING_ID,),
        )
        self._bind()
        self.assertEqual(ga.scheme_assignment_activity_groups(self.conn, SCHEME_ID), set())
        ga.submit_peer_contributions(self.conn, assignment_id=ASSIGNMENT_ID, reviewer_id=1, ratings={2: 18})
        self.assertEqual(ga.scheme_assignment_activity_groups(self.conn, SCHEME_ID), {900})
        with self.assertRaisesRegex(ValueError, "锁定"):
            ga.unbind_assignment(self.conn, assignment_id=ASSIGNMENT_ID)
        self._submit_and_grade(3, 70)
        self.assertEqual(ga.scheme_assignment_activity_groups(self.conn, SCHEME_ID), {900, 901})
        with self.assertRaisesRegex(ValueError, "锁定"):
            ga.bind_assignment_to_scheme(self.conn, assignment_id=ASSIGNMENT_ID,
                class_offering_id=CLASS_OFFERING_ID, scheme_id=501, teacher_id=7)
        self._bind()  # Retrying a successful save is harmless even after work starts.

    def test_cannot_convert_existing_individual_submission_to_group(self):
        self._submit_and_grade(1, 70)
        with self.assertRaisesRegex(ValueError, "锁定"):
            self._bind()
        self.assertFalse(ga.is_group_assignment(self.conn, ASSIGNMENT_ID))

    def test_last_submitters_peer_rating_matches_published_grade_and_revision(self):
        for sid in (1, 2):
            self._add_student(sid, f"成员{sid}")
        self._make_group(900, 1, [1, 2])
        self._bind()
        for sid in (1, 2):
            ga.record_member_work_score(self.conn, self._submit_and_grade(sid, 80))
        self.assertEqual(self._member_result(1)["final_score"], 80)
        for _ in range(2):
            ga.submit_peer_contributions(self.conn, assignment_id=ASSIGNMENT_ID, reviewer_id=2, ratings={1: 19})
        self.assertEqual(ga.get_reviewer_ratings(self.conn, ASSIGNMENT_ID, 900, 2), {"1": 19})
        self.assertEqual(ga.get_reviewer_ratings(self.conn, ASSIGNMENT_ID, 900, 1), {"2": 16})
        self.assertEqual(self._member_result(1)["final_score"], 83)
        row = self.conn.execute(
            """SELECT s.score, r.score FROM submissions s JOIN submission_grade_revisions r
               ON r.id = s.active_grade_revision_id WHERE s.student_pk_id = 1""",
        ).fetchone()
        self.assertEqual(tuple(row), (83, 83))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM submission_grade_revisions").fetchone()[0], 3)

    def test_peer_evaluation_waits_for_last_grade_transaction(self):
        for sid in (1, 2):
            self._add_student(sid, f"成员{sid}")
        self._make_group(900, 1, [1, 2])
        self._bind()
        ga.record_member_work_score(self.conn, self._submit_and_grade(1, 80))
        last = self._submit_and_grade(2, None, status="submitted")
        db_path = Path(self.enterContext(TemporaryDirectory())) / "assessment.sqlite3"
        waiting = Event()

        def rate_from_student_page():
            with closing(sqlite3.connect(db_path)) as peer:
                peer.row_factory = sqlite3.Row
                peer.set_trace_callback(lambda sql: waiting.set() if sql == "BEGIN IMMEDIATE" else None)
                ga.submit_peer_contributions(peer, assignment_id=ASSIGNMENT_ID, reviewer_id=2, ratings={1: 19})
                peer.commit()

        with closing(sqlite3.connect(db_path)) as grader:
            grader.row_factory = sqlite3.Row
            self.conn.backup(grader)
            grader.execute("UPDATE submissions SET score=80, status='graded' WHERE id=?", (last,))
            self.assertTrue(ga.record_member_work_score(grader, last)["finalized"])
            with ThreadPoolExecutor(max_workers=1) as pool:
                student = pool.submit(rate_from_student_page)
                try:
                    self.assertTrue(waiting.wait(3))
                    self.assertFalse(student.done())
                finally:
                    grader.commit()
                student.result(timeout=5)
            self.assertEqual(ga.get_student_display_state(grader, ASSIGNMENT_ID, 1)["final_score"], 83)

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

    def test_group_revision_matches_settlement_and_repeated_callback_is_idempotent(self):
        self._add_student(1, "独行")
        self._make_group(901, 1, [1])
        self._bind()
        sub_id = self._submit_and_grade(1, 90)
        ga.record_member_work_score(self.conn, sub_id)
        ga.record_member_work_score(self.conn, sub_id)
        row = self.conn.execute("SELECT s.score, r.score AS revision_score, r.provenance_json FROM submissions s JOIN submission_grade_revisions r ON r.id = s.active_grade_revision_id WHERE s.id = ?", (sub_id,)).fetchone()
        self.assertEqual(row["score"], 88)
        self.assertEqual(row["revision_score"], 88)
        self.assertIn('"source": "group_final"', row["provenance_json"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM submission_grade_revisions").fetchone()[0], 1)
        self.assertEqual(self._member_result(1)["work_score"], 90)

    def test_manual_regrade_creates_new_consistent_group_revision(self):
        self._add_student(1, "独行")
        self._make_group(901, 1, [1])
        self._bind()
        sub_id = self._submit_and_grade(1, 90)
        ga.record_member_work_score(self.conn, sub_id)
        self.conn.execute("UPDATE submissions SET score = 80, feedback_md = '教师调整' WHERE id = ?", (sub_id,))
        ga.activate_submission_grade_revision(self.conn, submission={"id": sub_id},
            data={"source": "manual"}, score=80, feedback_md="教师调整")
        ga.record_member_work_score(self.conn, sub_id)
        self.assertEqual(self._member_result(1)["work_score"], 80)
        self.assertEqual(self._member_result(1)["final_score"], 80)
        row = self.conn.execute("SELECT s.score, r.score FROM submissions s JOIN submission_grade_revisions r ON r.id = s.active_grade_revision_id WHERE s.id = ?", (sub_id,)).fetchone()
        self.assertEqual(tuple(row), (80, 80))

    def test_absence_zero_does_not_receive_default_peer_points(self):
        self._add_student(1, "缺交")
        self._make_group(901, 1, [1])
        self._bind()
        sub_id = self._submit_and_grade(1, 0)
        self.conn.execute("UPDATE submissions SET is_absence_score = 1 WHERE id = ?", (sub_id,))
        ga.record_member_work_score(self.conn, sub_id)
        self.assertEqual(self._member_result(1)["final_score"], 0)

    def test_refinalization_preserves_in_progress_regrading_status(self):
        self._add_student(1, "独行")
        self._make_group(901, 1, [1])
        self._bind()
        sub_id = self._submit_and_grade(1, 90)
        ga.record_member_work_score(self.conn, sub_id)
        self.conn.execute("UPDATE submissions SET status = 'grading' WHERE id = ?", (sub_id,))
        ga.try_finalize_group(self.conn, assignment_id=ASSIGNMENT_ID, group_id=901)
        self.assertEqual(self.conn.execute("SELECT status FROM submissions WHERE id = ?", (sub_id,)).fetchone()[0], "grading")

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
