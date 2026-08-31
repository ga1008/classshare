import tempfile
import unittest
from pathlib import Path

from classroom_app import config, database
from classroom_app.db import schema_offering_class_links
from classroom_app.db.connection import execute_insert_returning_id
from classroom_app.db.schema import init_database
from classroom_app.routers.manage_parts.classes_courses_offerings import _bootstrap_create_offering
from classroom_app.services.offering_bootstrap_service import build_offering_bootstrap_candidates


class OfferingBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_engine = config.DB_ENGINE
        self.original_path = config.DB_PATH
        self.original_database_path = database.DB_PATH
        config.DB_ENGINE = "sqlite"
        config.DB_PATH = Path(self.temp_dir.name) / "classroom.db"
        database.DB_PATH = config.DB_PATH
        schema_offering_class_links._READY_KEYS.clear()
        init_database()
        with database.get_db_connection() as conn:
            self.teacher_id = execute_insert_returning_id(
                conn,
                "INSERT INTO teachers (name, email, hashed_password) VALUES (?, ?, ?)",
                ("测试教师", "bootstrap@example.com", "x"),
            )
            self.semester_id = execute_insert_returning_id(
                conn,
                "INSERT INTO academic_semesters (teacher_id, name, start_date, end_date) VALUES (?, ?, ?, ?)",
                (self.teacher_id, "2026-2027学年第1学期", "2026-08-31", "2027-01-10"),
            )
            self.course_a = self._course(conn, "计算机网络原理", "E030054B1")
            self.course_b = self._course(conn, "计算机网络原理", "E040016B1")
            self.class_a = self._klass(conn, "网工2401班")
            self.class_b = self._klass(conn, "网工2402班")
            self.class_c = self._klass(conn, "计科2601班")
            for class_id, number in ((self.class_a, "01"), (self.class_b, "02"), (self.class_c, "03")):
                execute_insert_returning_id(
                    conn,
                    "INSERT INTO students (student_id_number, name, class_id) VALUES (?, ?, ?)",
                    (f"24000000{number}", f"学生{number}", class_id),
                )
            self.textbook_id = execute_insert_returning_id(
                conn,
                "INSERT INTO textbooks (title, teacher_id) VALUES (?, ?)",
                ("计算机网络（第8版）", self.teacher_id),
            )
            # 课程A：一个合班教学班（网工2401+2402），3 周排课
            self._occurrences(conn, self.course_a, "JXB-A1", "网工2401班,网工2402班", weeks=3)
            # 课程B（同名不同号）：单班教学班 计科2601
            self._occurrences(conn, self.course_b, "JXB-B1", "计科2601班", weeks=2)
            # 课程A 的另一个教学班：组成班级本地缺失 → blocked
            self._occurrences(conn, self.course_a, "JXB-A2", "信工2701班", weeks=2)
            conn.commit()
        schema_offering_class_links._READY_KEYS.clear()

    def tearDown(self):
        schema_offering_class_links._READY_KEYS.clear()
        config.DB_ENGINE = self.original_engine
        config.DB_PATH = self.original_path
        database.DB_PATH = self.original_database_path
        self.temp_dir.cleanup()

    def _course(self, conn, name, code):
        return execute_insert_returning_id(
            conn,
            """
            INSERT INTO courses (name, created_by_teacher_id, academic_source, academic_course_code, total_hours)
            VALUES (?, ?, 'gxufl_jwxt', ?, 64)
            """,
            (name, self.teacher_id, code),
        )

    def _klass(self, conn, name):
        return execute_insert_returning_id(
            conn,
            "INSERT INTO classes (name, created_by_teacher_id) VALUES (?, ?)",
            (name, self.teacher_id),
        )

    def _occurrences(self, conn, course_id, teaching_class_id, composition, *, weeks):
        for week in range(1, weeks + 1):
            conn.execute(
                """
                INSERT INTO teacher_academic_course_session_occurrences (
                    teacher_id, semester_id, course_id, course_name,
                    teaching_class_id, teaching_class_name, class_composition,
                    session_date, week_index, weekday, section_text,
                    section_start, section_end, section_count, schedule_source, synced_at
                ) VALUES (?, ?, ?, '计算机网络原理', ?, ?, ?, ?, ?, 0, '1-2', 1, 2, 2, 'academic_sync', '2026-08-31T08:00:00')
                """,
                (
                    self.teacher_id,
                    self.semester_id,
                    course_id,
                    teaching_class_id,
                    teaching_class_id,
                    composition,
                    f"2026-09-{6 + week:02d}",
                    week,
                ),
            )

    def _candidates(self, conn):
        return build_offering_bootstrap_candidates(
            conn, teacher_id=self.teacher_id, semester_id=self.semester_id
        )

    def test_candidates_cover_combined_and_same_name_courses(self):
        with database.get_db_connection() as conn:
            payload = self._candidates(conn)
        by_key = {(c["course_id"], c["teaching_class_id"]): c for c in payload["candidates"]}
        self.assertIn((self.course_a, "JXB-A1"), by_key)
        self.assertIn((self.course_b, "JXB-B1"), by_key)
        combined = by_key[(self.course_a, "JXB-A1")]
        self.assertTrue(combined["is_combined"])
        self.assertEqual(set(combined["class_ids"]), {self.class_a, self.class_b})
        self.assertEqual(combined["student_count"], 2)
        self.assertEqual(combined["session_count"], 3)
        single = by_key[(self.course_b, "JXB-B1")]
        self.assertEqual(single["class_ids"], [self.class_c])
        self.assertEqual(single["course_code"], "E040016B1")
        self.assertEqual(len(payload["blocked"]), 1)
        self.assertIn("信工2701班", payload["blocked"][0]["reason"])
        self.assertEqual(payload["summary"]["candidate_count"], 2)

    def test_create_offering_lands_links_and_sessions(self):
        with database.get_db_connection() as conn:
            payload = self._candidates(conn)
            combined = next(c for c in payload["candidates"] if c["is_combined"])
            created = _bootstrap_create_offering(
                conn,
                teacher_id=self.teacher_id,
                semester_id=self.semester_id,
                candidate=combined,
                textbook_id=None,
            )
            conn.commit()
            offering = dict(
                conn.execute(
                    "SELECT * FROM class_offerings WHERE id = ?", (created["offering_id"],)
                ).fetchone()
            )
            links = conn.execute(
                "SELECT class_id FROM class_offering_class_links WHERE offering_id = ? ORDER BY id",
                (created["offering_id"],),
            ).fetchall()
            sessions = conn.execute(
                "SELECT COUNT(*) AS n FROM class_offering_sessions WHERE class_offering_id = ?",
                (created["offering_id"],),
            ).fetchone()["n"]
        self.assertIsNone(offering["textbook_id"])
        self.assertEqual(offering["is_combined"], 1)
        self.assertEqual(offering["schedule_source"], "academic_sync")
        self.assertEqual(offering["academic_teaching_class_id"], "JXB-A1")
        self.assertEqual({int(row["class_id"]) for row in links}, {self.class_a, self.class_b})
        self.assertEqual(int(sessions), 3)
        self.assertEqual(created["session_count"], 3)

    def test_created_teaching_class_leaves_candidate_pool(self):
        with database.get_db_connection() as conn:
            payload = self._candidates(conn)
            combined = next(c for c in payload["candidates"] if c["is_combined"])
            _bootstrap_create_offering(
                conn,
                teacher_id=self.teacher_id,
                semester_id=self.semester_id,
                candidate=combined,
                textbook_id=self.textbook_id,
            )
            conn.commit()
            refreshed = self._candidates(conn)
        keys = {(c["course_id"], c["teaching_class_id"]) for c in refreshed["candidates"]}
        self.assertNotIn((self.course_a, "JXB-A1"), keys)
        self.assertIn((self.course_b, "JXB-B1"), keys)
        single = next(c for c in refreshed["candidates"] if c["course_id"] == self.course_b)
        self.assertIsNone(single["suggested_textbook"])


if __name__ == "__main__":
    unittest.main()
