import sqlite3
import unittest

from classroom_app.routers.materials_parts.common import (
    _apply_material_library_filters,
    _attach_material_assignment_facets,
    _build_material_filter_facets,
    _get_teacher_material_stats,
)


class MaterialLibraryFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE classes (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            );
            CREATE TABLE courses (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            );
            CREATE TABLE academic_semesters (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            );
            CREATE TABLE class_offerings (
                id INTEGER PRIMARY KEY,
                class_id INTEGER NOT NULL,
                course_id INTEGER NOT NULL,
                teacher_id INTEGER NOT NULL,
                semester TEXT,
                semester_id INTEGER
            );
            CREATE TABLE course_material_assignments (
                material_id INTEGER NOT NULL,
                class_offering_id INTEGER NOT NULL
            );
            CREATE TABLE course_materials (
                id INTEGER PRIMARY KEY,
                parent_id INTEGER,
                teacher_id INTEGER NOT NULL,
                material_path TEXT NOT NULL,
                node_type TEXT NOT NULL,
                file_size INTEGER,
                updated_at TEXT
            );
            CREATE TABLE material_ai_import_records (
                id INTEGER PRIMARY KEY,
                document_type TEXT,
                parse_status TEXT,
                package_material_id INTEGER,
                source_material_id INTEGER,
                parsed_material_id INTEGER
            );
            """
        )
        self.conn.executemany(
            "INSERT INTO classes (id, name) VALUES (?, ?)",
            [(10, "软工 2301"), (20, "数媒 2302")],
        )
        self.conn.executemany(
            "INSERT INTO courses (id, name) VALUES (?, ?)",
            [(101, "Python 程序设计"), (102, "数据结构")],
        )
        self.conn.execute("INSERT INTO academic_semesters (id, name) VALUES (?, ?)", (1, "2025-2026-1"))
        self.conn.executemany(
            """
            INSERT INTO class_offerings (id, class_id, course_id, teacher_id, semester_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            [(1001, 10, 101, 1, 1), (1002, 20, 102, 1, 1)],
        )
        self.conn.executemany(
            "INSERT INTO course_material_assignments (material_id, class_offering_id) VALUES (?, ?)",
            [(1, 1001), (2, 1002)],
        )

    def tearDown(self) -> None:
        self.conn.close()

    def test_assignment_facets_drive_course_and_class_filters(self) -> None:
        rows = [
            {
                "id": 1,
                "material_path": "/评学表/Python.docx",
                "scope_level": "private",
                "teacher_id": 1,
                "school_name": "GXUFL",
                "school_code": "gxufl",
                "college": "信息工程学院",
                "department": "软件工程系",
            },
            {
                "id": 2,
                "material_path": "/考核计划/数据结构.docx",
                "scope_level": "private",
                "teacher_id": 1,
                "school_name": "GXUFL",
                "school_code": "gxufl",
                "college": "信息工程学院",
                "department": "软件工程系",
            },
        ]

        attached = _attach_material_assignment_facets(self.conn, rows)
        facets = _build_material_filter_facets(attached, teacher_id=1)

        self.assertIn("Python 程序设计", facets["courses"])
        self.assertIn("软工 2301", facets["classes"])

        filtered = _apply_material_library_filters(
            attached,
            teacher_id=1,
            scope_filter="all",
            school="",
            department="",
            college="信息工程学院",
            course="Python 程序设计",
            class_name="软工 2301",
        )

        self.assertEqual([1], [item["id"] for item in filtered])

    def test_final_material_stats_count_only_the_user_facing_package(self) -> None:
        self.conn.executemany(
            """
            INSERT INTO course_materials
                (id, parent_id, teacher_id, material_path, node_type, file_size, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (11, None, 1, "AI生成-考核登分表", "folder", 0, "2026-07-29T15:00:00"),
                (12, 11, 1, "AI生成-考核登分表/readme.md", "file", 128, "2026-07-29T15:00:00"),
                (13, None, 1, "其他材料.pdf", "file", 256, "2026-07-29T14:00:00"),
            ],
        )
        self.conn.execute(
            """
            INSERT INTO material_ai_import_records
                (id, document_type, parse_status, package_material_id, source_material_id, parsed_material_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (1, "exam_grade_record", "completed", 11, 12, 12),
        )
        self.conn.execute(
            "INSERT INTO course_material_assignments (material_id, class_offering_id) VALUES (?, ?)",
            (11, 1001),
        )

        stats = _get_teacher_material_stats(self.conn, 1, document_type="exam_grade_record")

        self.assertEqual(1, stats["total_count"])
        self.assertEqual(1, stats["folder_count"])
        self.assertEqual(0, stats["file_count"])
        self.assertEqual(1, stats["assigned_material_count"])
        self.assertEqual(1, stats["classroom_count"])


if __name__ == "__main__":
    unittest.main()
