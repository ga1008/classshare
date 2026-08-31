import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from classroom_app import config, database
from classroom_app.db import schema_offering_class_links
from classroom_app.db.connection import execute_insert_returning_id
from classroom_app.db.schema import init_database
from classroom_app.services.academic_service import china_today
from classroom_app.services import offering_hub_service as hub


class OfferingHubServiceTests(unittest.TestCase):
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
        self.today = china_today()
        with database.get_db_connection() as conn:
            self.teacher_id = execute_insert_returning_id(
                conn,
                "INSERT INTO teachers (name, email, hashed_password) VALUES (?, ?, ?)",
                ("测试教师", "hub@example.com", "x"),
            )
            self.semester_id = execute_insert_returning_id(
                conn,
                "INSERT INTO academic_semesters (teacher_id, name, start_date, end_date) VALUES (?, ?, ?, ?)",
                (
                    self.teacher_id,
                    "2026-2027学年第1学期",
                    (self.today - timedelta(days=14)).isoformat(),
                    (self.today + timedelta(days=120)).isoformat(),
                ),
            )
            self.course_id = execute_insert_returning_id(
                conn,
                "INSERT INTO courses (name, created_by_teacher_id) VALUES (?, ?)",
                ("Python程序设计", self.teacher_id),
            )
            self.class_a = self._create_class(conn, "网工2401")
            self.class_b = self._create_class(conn, "网工2402")
            self.offering_id = execute_insert_returning_id(
                conn,
                """
                INSERT INTO class_offerings (class_id, course_id, teacher_id, semester, semester_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (self.class_a, self.course_id, self.teacher_id, "2026-2027学年第1学期", self.semester_id),
            )
            for class_id, is_primary in ((self.class_a, 1), (self.class_b, 0)):
                conn.execute(
                    """
                    INSERT INTO class_offering_class_links (offering_id, class_id, teacher_id, is_primary, source)
                    VALUES (?, ?, ?, ?, 'manual')
                    """,
                    (self.offering_id, class_id, self.teacher_id, is_primary),
                )
            self._create_student(conn, "2400000001", "学生甲", self.class_a)
            self._create_student(conn, "2400000002", "学生乙", self.class_b)
            self._create_student(conn, "2400000003", "学生丙", self.class_b, enrollment_status="withdrawn")
            self._create_session(conn, 1, self.today - timedelta(days=7))
            self._create_session(conn, 2, self.today + timedelta(days=1), section="3-4节", location="6-101")
            self._create_session(conn, 3, self.today + timedelta(days=8), status="cancelled")
            conn.execute(
                "INSERT INTO ai_class_configs (class_offering_id, system_prompt, syllabus) VALUES (?, ?, ?)",
                (self.offering_id, "小助手提示词", "大纲"),
            )
            conn.commit()

    def tearDown(self):
        schema_offering_class_links._READY_KEYS.clear()
        config.DB_ENGINE = self.original_engine
        config.DB_PATH = self.original_path
        database.DB_PATH = self.original_database_path
        self.temp_dir.cleanup()

    def _create_class(self, conn, name):
        return execute_insert_returning_id(
            conn,
            "INSERT INTO classes (name, created_by_teacher_id) VALUES (?, ?)",
            (name, self.teacher_id),
        )

    def _create_student(self, conn, number, name, class_id, enrollment_status="active"):
        return execute_insert_returning_id(
            conn,
            """
            INSERT INTO students (student_id_number, name, class_id, enrollment_status)
            VALUES (?, ?, ?, ?)
            """,
            (number, name, class_id, enrollment_status),
        )

    def _create_session(self, conn, order_index, session_date, section="1-2节", location="", status="scheduled"):
        conn.execute(
            """
            INSERT INTO class_offering_sessions
                (class_offering_id, order_index, title, session_date, weekday,
                 academic_section_text, academic_location, schedule_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self.offering_id,
                order_index,
                f"第{order_index}次课",
                session_date.isoformat(),
                session_date.isoweekday() % 7,
                section,
                location,
                status,
            ),
        )

    def _base_offering(self):
        return {
            "id": self.offering_id,
            "class_id": self.class_a,
            "class_ids": [self.class_a, self.class_b],
            "class_name": "网工2401、网工2402",
            "course_name": "Python程序设计",
            "semester": "2026-2027学年第1学期",
            "semester_id": self.semester_id,
            "textbook_id": None,
            "is_combined": 1,
        }

    def test_enrich_covers_membership_progress_and_config(self):
        with database.get_db_connection() as conn:
            enriched = hub.enrich_offerings_for_hub(conn, self.teacher_id, [self._base_offering()])

        self.assertEqual(len(enriched), 1)
        item = enriched[0]
        # 合班学生数经 membership links 统计，且排除非在读学生。
        self.assertEqual(item["student_count"], 2)
        self.assertEqual(item["class_count"], 2)
        self.assertEqual(sorted(item["linked_class_names"]), ["网工2401", "网工2402"])
        # 取消的课次不计入总数；过去 1 次 + 未来 1 次。
        self.assertEqual(item["session_total"], 2)
        self.assertEqual(item["session_done"], 1)
        self.assertEqual(item["run_status"], "active")
        self.assertIsNotNone(item["next_session"])
        self.assertEqual(item["next_session"]["date"], (self.today + timedelta(days=1)).isoformat())
        self.assertEqual(item["next_session"]["relative_label"], "明天")
        self.assertEqual(item["next_session"]["section_text"], "3-4节")
        self.assertTrue(item["has_ai_config"])
        self.assertFalse(item["has_textbook"])
        self.assertEqual(item["config_missing"], ["textbook"])

    def test_build_context_stats_and_todo(self):
        semesters = [
            {
                "id": self.semester_id,
                "name": "2026-2027学年第1学期",
                "start_date": (self.today - timedelta(days=14)).isoformat(),
                "end_date": (self.today + timedelta(days=120)).isoformat(),
            }
        ]
        with database.get_db_connection() as conn:
            context = hub.build_offering_hub_context(
                conn, self.teacher_id, [self._base_offering()], semesters, self.semester_id
            )

        stats = context["hub_stats"]
        self.assertEqual(stats["current_offering_count"], 1)
        self.assertEqual(stats["current_class_count"], 2)
        self.assertEqual(stats["current_student_count"], 2)
        self.assertEqual(stats["session_total"], 2)
        self.assertEqual(stats["session_done"], 1)
        self.assertEqual(context["hub_todo"]["missing_textbook"], 1)
        self.assertEqual(context["hub_todo"]["missing_ai"], 0)
        self.assertEqual(context["hub_course_distribution"], [{"label": "Python程序设计", "value": 1}])
        self.assertEqual(len(context["hub_semester_options"]), 1)
        self.assertTrue(context["hub_semester_options"][0]["is_default"])

    def test_run_status_derivation(self):
        self.assertEqual(hub._run_status({"total": 0, "done": 0}), "unscheduled")
        self.assertEqual(hub._run_status({"total": 3, "done": 0}), "upcoming")
        self.assertEqual(hub._run_status({"total": 3, "done": 1}), "active")
        self.assertEqual(hub._run_status({"total": 3, "done": 3}), "finished")


if __name__ == "__main__":
    unittest.main()
