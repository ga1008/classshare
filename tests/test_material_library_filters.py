import sqlite3
import unittest

from classroom_app.routers.materials_parts.common import (
    _apply_material_library_filters,
    _attach_material_assignment_facets,
    _build_material_filter_facets,
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


if __name__ == "__main__":
    unittest.main()
