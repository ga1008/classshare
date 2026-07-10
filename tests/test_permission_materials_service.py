import sqlite3
import unittest

from fastapi import HTTPException

from classroom_app.routers.materials_parts.common import _list_material_rows_for_parent
from classroom_app.services.materials_service import ensure_user_material_access, sync_classroom_learning_material_assignments


class MaterialPermissionServiceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE teachers (
                id INTEGER PRIMARY KEY,
                is_active INTEGER DEFAULT 1,
                is_super_admin INTEGER DEFAULT 0,
                school_code TEXT,
                school_name TEXT,
                college TEXT,
                department TEXT
            );
            CREATE TABLE teacher_organization_memberships (
                id INTEGER PRIMARY KEY,
                teacher_id INTEGER NOT NULL,
                school_code TEXT NOT NULL,
                school_name TEXT,
                college TEXT,
                department TEXT,
                is_primary INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                updated_at TEXT
            );
            CREATE TABLE students (
                id INTEGER PRIMARY KEY,
                class_id INTEGER NOT NULL,
                school_code TEXT,
                school_name TEXT,
                college TEXT,
                department TEXT,
                enrollment_status TEXT DEFAULT 'active'
            );
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
                name TEXT
            );
            CREATE TABLE class_offerings (
                id INTEGER PRIMARY KEY,
                class_id INTEGER NOT NULL,
                course_id INTEGER NOT NULL,
                teacher_id INTEGER NOT NULL,
                semester_id INTEGER,
                semester TEXT
            );
            CREATE TABLE course_materials (
                id INTEGER PRIMARY KEY,
                teacher_id INTEGER NOT NULL,
                parent_id INTEGER,
                root_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                material_path TEXT NOT NULL,
                node_type TEXT NOT NULL,
                preview_type TEXT DEFAULT '',
                scope_level TEXT DEFAULT 'private',
                owner_role TEXT DEFAULT 'teacher',
                owner_user_pk INTEGER,
                school_code TEXT,
                school_name TEXT,
                college TEXT,
                department TEXT,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE course_material_assignments (
                material_id INTEGER NOT NULL,
                class_offering_id INTEGER NOT NULL,
                assigned_by_teacher_id INTEGER NOT NULL,
                created_at TEXT
            );
            CREATE TABLE material_ai_import_records (
                id INTEGER PRIMARY KEY,
                teacher_id INTEGER NOT NULL,
                package_material_id INTEGER,
                source_material_id INTEGER,
                parsed_material_id INTEGER,
                parent_material_id INTEGER,
                document_type TEXT NOT NULL,
                parse_status TEXT NOT NULL DEFAULT 'completed'
            );
            """
        )
        self.conn.executemany(
            """
            INSERT INTO teachers (id, school_code, school_name, college, department)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (1, "gxufl", "GXUFL", "info", "network"),
                (2, "gxufl", "GXUFL", "info", "network"),
                (3, "gxufl", "GXUFL", "business", "finance"),
            ],
        )
        self.conn.executemany(
            """
            INSERT INTO teacher_organization_memberships (
                id, teacher_id, school_code, school_name, college, department, is_primary, is_active, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, 2, "gxufl", "GXUFL", "info", "network", 1, 1, "2026-01-01"),
                (2, 3, "gxufl", "GXUFL", "business", "finance", 1, 1, "2026-01-01"),
            ],
        )
        self.conn.executemany(
            """
            INSERT INTO students (id, class_id, school_code, school_name, college, department)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (100, 10, "gxufl", "GXUFL", "info", "network"),
                (200, 20, "gxufl", "GXUFL", "info", "network"),
            ],
        )
        self.conn.executemany(
            "INSERT INTO class_offerings (id, class_id, course_id, teacher_id) VALUES (?, ?, ?, ?)",
            [
                (1001, 10, 501, 1),
                (2001, 20, 501, 1),
                (3001, 10, 501, 2),
            ],
        )
        self.conn.executemany("INSERT INTO classes (id, name) VALUES (?, ?)", [(10, "A班"), (20, "B班")])
        self.conn.executemany("INSERT INTO courses (id, name) VALUES (?, ?)", [(501, "过程材料测试课")])
        self.conn.executemany(
            """
            INSERT INTO course_materials (
                id, teacher_id, parent_id, root_id, name, material_path, node_type,
                preview_type, scope_level, owner_user_pk, school_code, school_name,
                college, department, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, 1, None, 1, "shared", "shared", "folder", "folder", "school", 1, "gxufl", "GXUFL", "info", "network", "2026-01-01", "2026-01-01"),
                (2, 1, 1, 1, "readme.md", "shared/readme.md", "file", "markdown", "school", 1, "gxufl", "GXUFL", "info", "network", "2026-01-01", "2026-01-01"),
                (3, 1, None, 3, "private.md", "private.md", "file", "markdown", "private", 1, "gxufl", "GXUFL", "info", "network", "2026-01-01", "2026-01-01"),
                (4, 1, None, 4, "college.xlsx", "college.xlsx", "file", "binary", "college", 1, "gxufl", "GXUFL", "info", "network", "2026-01-01", "2026-01-01"),
            ],
        )
        self.conn.executemany(
            """
            INSERT INTO material_ai_import_records (
                id, teacher_id, package_material_id, source_material_id,
                parsed_material_id, parent_material_id, document_type, parse_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, 1, 1, None, None, None, "exam_grade_record", "completed"),
                (2, 1, 3, None, None, None, "ordinary_grade_record", "completed"),
                (3, 1, 4, None, None, None, "ordinary_grade_record", "completed"),
                (4, 1, 2, None, None, None, "exam_grade_record", "running"),
            ],
        )

    def tearDown(self):
        self.conn.close()

    def test_teacher_can_use_same_school_scoped_material(self):
        material = ensure_user_material_access(self.conn, 2, {"role": "teacher", "id": 2})

        self.assertEqual(2, int(material["id"]))

    def test_teacher_can_use_same_college_scoped_material(self):
        material = ensure_user_material_access(self.conn, 4, {"role": "teacher", "id": 2})

        self.assertEqual(4, int(material["id"]))

        with self.assertRaises(HTTPException) as ctx:
            ensure_user_material_access(self.conn, 4, {"role": "teacher", "id": 3})

        self.assertEqual(403, ctx.exception.status_code)

    def test_material_library_filters_completed_records_by_document_type(self):
        rows = _list_material_rows_for_parent(self.conn, 2, None, document_type="exam_grade_record")
        self.assertEqual([1], [int(row["id"]) for row in rows])

        rows = _list_material_rows_for_parent(self.conn, 2, None, document_type="ordinary_grade_record")
        self.assertEqual([4], [int(row["id"]) for row in rows])

        rows = _list_material_rows_for_parent(self.conn, 1, None, document_type="ordinary_grade_record")
        self.assertEqual([4, 3], [int(row["id"]) for row in rows])

    def test_student_cannot_directly_read_school_scoped_material_without_assignment(self):
        with self.assertRaises(HTTPException) as ctx:
            ensure_user_material_access(self.conn, 2, {"role": "student", "id": 100})

        self.assertEqual(403, ctx.exception.status_code)

    def test_student_can_read_child_material_when_ancestor_is_assigned_to_own_classroom(self):
        self.conn.execute(
            """
            INSERT INTO course_material_assignments (material_id, class_offering_id, assigned_by_teacher_id, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (1, 1001, 1, "2026-06-03T00:00:00"),
        )

        material = ensure_user_material_access(self.conn, 2, {"role": "student", "id": 100})

        self.assertEqual(2, int(material["id"]))

    def test_student_cannot_use_other_classroom_assignment(self):
        self.conn.execute(
            """
            INSERT INTO course_material_assignments (material_id, class_offering_id, assigned_by_teacher_id, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (1, 2001, 1, "2026-06-03T00:00:00"),
        )

        with self.assertRaises(HTTPException) as ctx:
            ensure_user_material_access(self.conn, 2, {"role": "student", "id": 100})

        self.assertEqual(403, ctx.exception.status_code)

    def test_sync_assignments_rejects_unowned_classroom_before_insert(self):
        with self.assertRaises(HTTPException) as ctx:
            sync_classroom_learning_material_assignments(
                self.conn,
                class_offering_id=1001,
                teacher_id=2,
                material_ids=[2],
            )

        self.assertEqual(404, ctx.exception.status_code)
        row = self.conn.execute("SELECT COUNT(*) AS count FROM course_material_assignments").fetchone()
        self.assertEqual(0, int(row["count"]))

    def test_sync_assignments_rejects_private_material_before_insert(self):
        with self.assertRaises(HTTPException) as ctx:
            sync_classroom_learning_material_assignments(
                self.conn,
                class_offering_id=3001,
                teacher_id=2,
                material_ids=[3],
            )

        self.assertEqual(400, ctx.exception.status_code)
        row = self.conn.execute("SELECT COUNT(*) AS count FROM course_material_assignments").fetchone()
        self.assertEqual(0, int(row["count"]))

    def test_sync_assignments_inserts_nearest_folder_anchor_for_owned_markdown(self):
        inserted = sync_classroom_learning_material_assignments(
            self.conn,
            class_offering_id=1001,
            teacher_id=1,
            material_ids=[2],
        )

        self.assertEqual([1], [int(item["id"]) for item in inserted])
        row = self.conn.execute(
            """
            SELECT material_id, class_offering_id
            FROM course_material_assignments
            WHERE material_id = ? AND class_offering_id = ?
            """,
            (1, 1001),
        ).fetchone()
        self.assertIsNotNone(row)


if __name__ == "__main__":
    unittest.main()
