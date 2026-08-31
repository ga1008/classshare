import json
import tempfile
import unittest
from pathlib import Path

from classroom_app import config, database
from classroom_app.db import schema_offering_class_links, schema_offering_merge
from classroom_app.db.connection import execute_insert_returning_id
from classroom_app.db.schema import init_database
from classroom_app.services import offering_merge_service as merge


class OfferingMergeServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_engine = config.DB_ENGINE
        self.original_path = config.DB_PATH
        self.original_database_path = database.DB_PATH
        config.DB_ENGINE = "sqlite"
        config.DB_PATH = Path(self.temp_dir.name) / "classroom.db"
        database.DB_PATH = config.DB_PATH
        schema_offering_class_links._SCHEMA_READY = False
        schema_offering_merge._SCHEMA_READY = False
        init_database()
        with database.get_db_connection() as conn:
            from classroom_app.db import schema_session_learning_materials as slm

            if hasattr(slm, "_SCHEMA_READY"):
                slm._SCHEMA_READY = False
            slm.ensure_session_learning_materials_schema(conn)
            self.teacher_id = execute_insert_returning_id(
                conn,
                "INSERT INTO teachers (name, email, hashed_password) VALUES (?, ?, ?)",
                ("测试教师", "merge@example.com", "x"),
            )
            self.course_id = execute_insert_returning_id(
                conn,
                "INSERT INTO courses (name, created_by_teacher_id) VALUES (?, ?)",
                ("动态web程序设计", self.teacher_id),
            )
            self.class_a = self._insert_class(conn, "软工2401班")
            self.class_b = self._insert_class(conn, "软工2402班")
            self.target_id = self._insert_offering(conn, self.class_a)
            self.source_id = self._insert_offering(conn, self.class_b)
            self.student_a = self._insert_student(conn, "2400000001", "学生甲", self.class_a)
            self.student_b = self._insert_student(conn, "2400000002", "学生乙", self.class_b)
            # 双方课次（同 order），source 课次带材料绑定 → 验证 session 重映射
            self.target_session = self._insert_session(conn, self.target_id, 1, "第1次课")
            self.source_session = self._insert_session(conn, self.source_id, 1, "第1次课")
            self.material_id = execute_insert_returning_id(
                conn,
                """
                INSERT INTO course_materials (teacher_id, name, material_path)
                VALUES (?, '讲义', 'docs/讲义.md')
                """,
                (self.teacher_id,),
            )
            conn.execute(
                """
                INSERT INTO class_offering_learning_materials (class_offering_id, session_id, material_id)
                VALUES (?, ?, ?)
                """,
                (self.source_id, self.source_session, self.material_id),
            )
            # 双方各一份同名作业 + source 学生提交
            self.target_assignment = self._insert_assignment(conn, self.target_id, "课堂练习1")
            self.source_assignment = self._insert_assignment(conn, self.source_id, "课堂练习1")
            conn.execute(
                """
                INSERT INTO submissions (assignment_id, student_pk_id, student_name, status, submitted_at)
                VALUES (?, ?, '学生乙', 'submitted', '2026-03-11T10:00:00')
                """,
                (self.source_assignment, self.student_b),
            )
            # 学生维度唯一表：两边各自学生的快照（互斥 → 应放行）
            for offering_id, student_id in (
                (self.target_id, self.student_a),
                (self.source_id, self.student_b),
            ):
                conn.execute(
                    "INSERT INTO learning_progress_snapshots (class_offering_id, student_id, score) VALUES (?, ?, 10)",
                    (offering_id, student_id),
                )
            conn.commit()
        schema_offering_class_links._SCHEMA_READY = False
        schema_offering_merge._SCHEMA_READY = False

    def tearDown(self):
        schema_offering_class_links._SCHEMA_READY = False
        schema_offering_merge._SCHEMA_READY = False
        config.DB_ENGINE = self.original_engine
        config.DB_PATH = self.original_path
        database.DB_PATH = self.original_database_path
        self.temp_dir.cleanup()

    def _insert_class(self, conn, name):
        return execute_insert_returning_id(
            conn,
            "INSERT INTO classes (name, created_by_teacher_id) VALUES (?, ?)",
            (name, self.teacher_id),
        )

    def _insert_offering(self, conn, class_id):
        return execute_insert_returning_id(
            conn,
            """
            INSERT INTO class_offerings (class_id, course_id, teacher_id, semester)
            VALUES (?, ?, ?, '2025-2026第二学期')
            """,
            (class_id, self.course_id, self.teacher_id),
        )

    def _insert_student(self, conn, number, name, class_id):
        return execute_insert_returning_id(
            conn,
            "INSERT INTO students (student_id_number, name, class_id) VALUES (?, ?, ?)",
            (number, name, class_id),
        )

    def _insert_session(self, conn, offering_id, order_index, title):
        return execute_insert_returning_id(
            conn,
            """
            INSERT INTO class_offering_sessions (
                class_offering_id, order_index, title, content, section_count,
                slot_section_count, session_date, weekday, week_index
            ) VALUES (?, ?, ?, '内容', 2, 2, '2026-03-09', 0, 1)
            """,
            (offering_id, order_index, title),
        )

    def _insert_assignment(self, conn, offering_id, title):
        return execute_insert_returning_id(
            conn,
            """
            INSERT INTO assignments (course_id, title, status, class_offering_id, created_at)
            VALUES (?, ?, 'published', ?, '2026-03-10T08:00:00')
            """,
            (self.course_id, title, offering_id),
        )

    def test_registry_covers_every_offering_table(self):
        with database.get_db_connection() as conn:
            self.assertEqual([], merge.find_unregistered_offering_tables(conn))

    def test_candidates_detect_double_opened_group(self):
        with database.get_db_connection() as conn:
            candidates = merge.find_merge_candidates(conn, self.teacher_id)
        self.assertEqual(len(candidates), 1)
        group = candidates[0]
        self.assertEqual(group["course_name"], "动态web程序设计")
        self.assertEqual(
            {o["offering_id"] for o in group["offerings"]},
            {self.target_id, self.source_id},
        )
        self.assertIn(group["recommended_target_id"], (self.target_id, self.source_id))

    def test_preview_reports_rows_and_allows_execution(self):
        with database.get_db_connection() as conn:
            preview = merge.build_merge_preview(
                conn,
                teacher_id=self.teacher_id,
                target_offering_id=self.target_id,
                source_offering_ids=[self.source_id],
            )
        self.assertTrue(preview["can_execute"])
        tables = {item["table"]: item for item in preview["tables"]}
        self.assertEqual(tables["assignments"]["source_rows"], 1)
        self.assertEqual(tables["learning_progress_snapshots"]["source_rows"], 1)

    def test_execute_merges_everything_atomically(self):
        with database.get_db_connection() as conn:
            result = merge.execute_offering_merge(
                conn,
                teacher_id=self.teacher_id,
                target_offering_id=self.target_id,
                source_offering_ids=[self.source_id],
                confirm_class_name="软工2401班",
            )
            conn.commit()

        self.assertEqual(result["status"], "success")
        with database.get_db_connection() as conn:
            self.assertIsNone(
                conn.execute(
                    "SELECT id FROM class_offerings WHERE id = ?", (self.source_id,)
                ).fetchone()
            )
            offering = dict(
                conn.execute(
                    "SELECT * FROM class_offerings WHERE id = ?", (self.target_id,)
                ).fetchone()
            )
            self.assertEqual(offering["is_combined"], 1)
            self.assertEqual(offering["combined_class_names"], "软工2401班·软工2402班")

            assignments = [
                dict(row)
                for row in conn.execute(
                    "SELECT id, title FROM assignments WHERE class_offering_id = ? ORDER BY id",
                    (self.target_id,),
                ).fetchall()
            ]
            titles = {a["title"] for a in assignments}
            self.assertIn("课堂练习1", titles)
            self.assertIn("课堂练习1（原软工2402班）", titles)
            submission = conn.execute(
                "SELECT student_pk_id FROM submissions WHERE assignment_id = ?",
                (self.source_assignment,),
            ).fetchone()
            self.assertEqual(int(submission["student_pk_id"]), self.student_b)

            snapshot_count = conn.execute(
                "SELECT COUNT(*) AS n FROM learning_progress_snapshots WHERE class_offering_id = ?",
                (self.target_id,),
            ).fetchone()["n"]
            self.assertEqual(int(snapshot_count), 2)

            material_binding = conn.execute(
                "SELECT session_id, class_offering_id FROM class_offering_learning_materials WHERE material_id = ?",
                (self.material_id,),
            ).fetchone()
            self.assertEqual(int(material_binding["session_id"]), self.target_session)
            self.assertEqual(int(material_binding["class_offering_id"]), self.target_id)

            sessions = conn.execute(
                "SELECT COUNT(*) AS n FROM class_offering_sessions WHERE class_offering_id IN (?, ?)",
                (self.target_id, self.source_id),
            ).fetchone()["n"]
            self.assertEqual(int(sessions), 1)

            archive = conn.execute(
                "SELECT * FROM offering_merge_archives WHERE id = ?",
                (result["archive_id"],),
            ).fetchone()
            payload = json.loads(archive["payload_json"])
            self.assertIn("assignments", payload["tables"])
            log = conn.execute(
                "SELECT * FROM offering_merge_logs WHERE merge_token = ?",
                (result["merge_token"],),
            ).fetchone()
            self.assertIsNotNone(log)

    def test_wrong_confirmation_text_changes_nothing(self):
        with database.get_db_connection() as conn:
            with self.assertRaises(merge.OfferingMergeError):
                merge.execute_offering_merge(
                    conn,
                    teacher_id=self.teacher_id,
                    target_offering_id=self.target_id,
                    source_offering_ids=[self.source_id],
                    confirm_class_name="错误名字",
                )
            conn.rollback()
        with database.get_db_connection() as conn:
            self.assertIsNotNone(
                conn.execute(
                    "SELECT id FROM class_offerings WHERE id = ?", (self.source_id,)
                ).fetchone()
            )
            archives = conn.execute(
                "SELECT COUNT(*) AS n FROM offering_merge_archives"
            ).fetchone()["n"]
            self.assertEqual(int(archives), 0)

    def test_guarded_conflict_blocks_merge(self):
        with database.get_db_connection() as conn:
            # 人为制造转班残留：同一学生在两个课堂都有进度快照
            conn.execute(
                "INSERT INTO learning_progress_snapshots (class_offering_id, student_id, score) VALUES (?, ?, 5)",
                (self.source_id, self.student_a),
            )
            conn.commit()
            preview = merge.build_merge_preview(
                conn,
                teacher_id=self.teacher_id,
                target_offering_id=self.target_id,
                source_offering_ids=[self.source_id],
            )
            self.assertFalse(preview["can_execute"])
            self.assertTrue(any("learning_progress_snapshots" in b for b in preview["blockers"]))
            with self.assertRaises(merge.OfferingMergeError):
                merge.execute_offering_merge(
                    conn,
                    teacher_id=self.teacher_id,
                    target_offering_id=self.target_id,
                    source_offering_ids=[self.source_id],
                    confirm_class_name="软工2401班",
                )
            conn.rollback()

    def test_overlapping_classes_rejected(self):
        with database.get_db_connection() as conn:
            other_course = execute_insert_returning_id(
                conn,
                "INSERT INTO courses (name, created_by_teacher_id) VALUES (?, ?)",
                ("另一门课", self.teacher_id),
            )
            with self.assertRaises(merge.OfferingMergeError):
                merge._load_merge_offerings(
                    conn,
                    teacher_id=self.teacher_id,
                    target_offering_id=self.target_id,
                    source_offering_ids=[self.target_id],
                )
            other_offering = execute_insert_returning_id(
                conn,
                """
                INSERT INTO class_offerings (class_id, course_id, teacher_id, semester)
                VALUES (?, ?, ?, '2025-2026第二学期')
                """,
                (self.class_a, other_course, self.teacher_id),
            )
            with self.assertRaises(merge.OfferingMergeError):
                merge._load_merge_offerings(
                    conn,
                    teacher_id=self.teacher_id,
                    target_offering_id=self.target_id,
                    source_offering_ids=[other_offering],
                )


if __name__ == "__main__":
    unittest.main()
