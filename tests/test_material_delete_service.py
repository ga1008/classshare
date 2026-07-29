import asyncio
import sqlite3
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from classroom_app.routers.materials_parts import library as material_library_router
from classroom_app.services.base_resource_modes_service import build_material_delete_blockers
from classroom_app.services.material_delete_service import (
    build_material_delete_impact,
    unlink_material_delete_references,
)


class MaterialDeleteServiceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE course_materials (
                id INTEGER PRIMARY KEY,
                teacher_id INTEGER NOT NULL,
                parent_id INTEGER,
                root_id INTEGER NOT NULL,
                material_path TEXT NOT NULL,
                name TEXT NOT NULL,
                node_type TEXT NOT NULL,
                file_hash TEXT,
                updated_at TEXT
            );
            CREATE TABLE courses (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE classes (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE class_offerings (
                id INTEGER PRIMARY KEY,
                class_id INTEGER,
                course_id INTEGER,
                semester TEXT,
                home_learning_material_id INTEGER
            );
            CREATE TABLE course_material_assignments (
                id INTEGER PRIMARY KEY,
                material_id INTEGER,
                class_offering_id INTEGER,
                assigned_by_teacher_id INTEGER,
                created_at TEXT
            );
            CREATE TABLE course_lessons (
                id INTEGER PRIMARY KEY,
                course_id INTEGER,
                order_index INTEGER,
                title TEXT,
                learning_material_id INTEGER,
                updated_at TEXT
            );
            CREATE TABLE class_offering_sessions (
                id INTEGER PRIMARY KEY,
                class_offering_id INTEGER,
                order_index INTEGER,
                title TEXT,
                learning_material_id INTEGER,
                updated_at TEXT
            );
            CREATE TABLE class_offering_learning_materials (
                id INTEGER PRIMARY KEY,
                class_offering_id INTEGER,
                session_id INTEGER,
                material_id INTEGER,
                sort_order INTEGER
            );
            CREATE TABLE material_ai_import_records (
                id INTEGER PRIMARY KEY,
                package_material_id INTEGER,
                source_material_id INTEGER,
                parsed_material_id INTEGER,
                parent_material_id INTEGER,
                document_type_label TEXT,
                document_type TEXT,
                parse_status TEXT,
                source_file_name TEXT,
                updated_at TEXT
            );
            CREATE TABLE session_material_generation_tasks (
                id INTEGER PRIMARY KEY,
                class_offering_id INTEGER,
                session_id INTEGER,
                generated_material_id INTEGER,
                status TEXT,
                document_type TEXT,
                updated_at TEXT
            );
            CREATE TABLE learning_material_progress (
                id INTEGER PRIMARY KEY,
                class_offering_id INTEGER,
                student_id INTEGER,
                material_id INTEGER,
                completed INTEGER,
                mastered INTEGER,
                last_viewed_at TEXT
            );

            INSERT INTO courses VALUES (20, '动态 Web 程序设计');
            INSERT INTO classes VALUES (30, '软工 2401 班');
            INSERT INTO course_materials VALUES (10, 1, NULL, 10, 'pkg', '材料包', 'folder', NULL, '2026-07-29T01:00:00');
            INSERT INTO course_materials VALUES (11, 1, 10, 10, 'pkg/readme.md', 'readme.md', 'file', NULL, '2026-07-29T01:00:00');
            INSERT INTO course_materials VALUES (12, 1, 10, 10, 'pkg/other.md', 'other.md', 'file', NULL, '2026-07-29T01:00:00');
            INSERT INTO class_offerings VALUES (40, 30, 20, '第二学期', 11);
            INSERT INTO course_material_assignments VALUES (50, 11, 40, 1, '2026-07-29T01:00:00');
            INSERT INTO course_lessons VALUES (60, 20, 2, '成绩记录', 11, '2026-07-29T01:00:00');
            INSERT INTO class_offering_sessions VALUES (70, 40, 2, '成绩记录', 11, '2026-07-29T01:00:00');
            INSERT INTO class_offering_learning_materials VALUES (80, 40, 70, 11, 0);
            INSERT INTO class_offering_learning_materials VALUES (81, 40, 70, 12, 1);
            INSERT INTO class_offering_learning_materials VALUES (82, 40, 0, 11, 0);
            INSERT INTO class_offering_learning_materials VALUES (83, 40, 0, 12, 1);
            INSERT INTO material_ai_import_records VALUES (
                90, NULL, 11, 12, NULL, '学生平时成绩记录表', 'ordinary_grade_record',
                'completed', '成绩记录表.xlsx', '2026-07-29T01:00:00'
            );
            INSERT INTO session_material_generation_tasks VALUES (
                100, 40, 70, 11, 'completed', 'lesson_material', '2026-07-29T01:00:00'
            );
            INSERT INTO learning_material_progress VALUES (110, 40, 1, 11, 1, 1, '2026-07-29T01:00:00');
            INSERT INTO learning_material_progress VALUES (111, 40, 2, 11, 0, 0, '2026-07-29T01:00:00');
            """
        )
        self.material = self.conn.execute("SELECT * FROM course_materials WHERE id = 11").fetchone()

    def tearDown(self):
        self.conn.close()

    def test_impact_reverse_traces_real_bindings_without_double_counting_primary_mirrors(self):
        impact = build_material_delete_impact(self.conn, self.material)

        self.assertEqual(8, impact["total_reference_count"])
        self.assertEqual(2, impact["destructive_reference_count"])
        self.assertEqual(1, impact["blockers"]["课堂课次引用"])
        self.assertEqual(1, impact["blockers"]["课堂首页材料"])
        self.assertEqual(2, impact["blockers"]["学生学习进度"])
        self.assertFalse(impact["can_delete_directly"])
        self.assertEqual(64, len(impact["impact_token"]))

        groups = {group["key"]: group for group in impact["groups"]}
        self.assertEqual("动态 Web 程序设计 · 软工 2401 班", groups["classroom_sessions"]["items"][0]["primary"])
        self.assertEqual("delete", groups["learning_progress"]["risk"])
        self.assertIn("不可恢复", groups["learning_progress"]["effect"])

        blockers = build_material_delete_blockers(self.conn, self.material)
        self.assertEqual(impact["blockers"], blockers)

    def test_one_step_unlink_preserves_history_and_promotes_remaining_material(self):
        before = unlink_material_delete_references(self.conn, self.material)

        self.assertEqual(8, before["total_reference_count"])
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM course_material_assignments").fetchone()[0])
        self.assertIsNone(self.conn.execute("SELECT learning_material_id FROM course_lessons WHERE id = 60").fetchone()[0])
        self.assertEqual(12, self.conn.execute("SELECT learning_material_id FROM class_offering_sessions WHERE id = 70").fetchone()[0])
        self.assertEqual(12, self.conn.execute("SELECT home_learning_material_id FROM class_offerings WHERE id = 40").fetchone()[0])
        self.assertEqual(
            [12],
            [row[0] for row in self.conn.execute(
                "SELECT material_id FROM class_offering_learning_materials WHERE session_id = 70 ORDER BY sort_order"
            ).fetchall()],
        )

        ai_record = self.conn.execute(
            "SELECT source_material_id, parsed_material_id FROM material_ai_import_records WHERE id = 90"
        ).fetchone()
        self.assertIsNone(ai_record["source_material_id"])
        self.assertEqual(12, ai_record["parsed_material_id"])
        self.assertIsNone(
            self.conn.execute(
                "SELECT generated_material_id FROM session_material_generation_tasks WHERE id = 100"
            ).fetchone()[0]
        )
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM learning_material_progress").fetchone()[0])
        self.assertIsNotNone(self.conn.execute("SELECT id FROM course_materials WHERE id = 11").fetchone())
        self.assertEqual(0, build_material_delete_impact(self.conn, self.material)["total_reference_count"])

    def test_delete_endpoint_requires_current_impact_token_and_closes_the_transaction(self):
        impact = build_material_delete_impact(self.conn, self.material)
        with patch.object(material_library_router, "get_db_connection", return_value=self.conn):
            result = asyncio.run(
                material_library_router.delete_material(
                    material_id=11,
                    unlink_references=True,
                    impact_token=impact["impact_token"],
                    user={"id": 1, "role": "teacher"},
                )
            )

        self.assertEqual("success", result["status"])
        self.assertEqual(8, result["unlinked_reference_count"])
        self.assertEqual(2, result["deleted_learning_progress_count"])
        self.assertIsNone(self.conn.execute("SELECT id FROM course_materials WHERE id = 11").fetchone())
        self.assertEqual(12, self.conn.execute("SELECT learning_material_id FROM class_offering_sessions WHERE id = 70").fetchone()[0])

    def test_delete_endpoint_rejects_unreviewed_reference_changes(self):
        with patch.object(material_library_router, "get_db_connection", return_value=self.conn):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(
                    material_library_router.delete_material(
                        material_id=11,
                        unlink_references=True,
                        impact_token="stale-token",
                        user={"id": 1, "role": "teacher"},
                    )
                )

        self.assertEqual(409, raised.exception.status_code)
        self.assertEqual("material_delete_impact_changed", raised.exception.detail["code"])
        self.assertIsNotNone(self.conn.execute("SELECT id FROM course_materials WHERE id = 11").fetchone())
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM course_material_assignments").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
