import sqlite3
import unittest

from fastapi import HTTPException

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
            CREATE TABLE students (
                id INTEGER PRIMARY KEY,
                class_id INTEGER NOT NULL,
                school_code TEXT,
                school_name TEXT,
                college TEXT,
                department TEXT,
                enrollment_status TEXT DEFAULT 'active'
            );
            CREATE TABLE class_offerings (
                id INTEGER PRIMARY KEY,
                class_id INTEGER NOT NULL,
                course_id INTEGER NOT NULL,
                teacher_id INTEGER NOT NULL
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
                department TEXT
            );
            CREATE TABLE course_material_assignments (
                material_id INTEGER NOT NULL,
                class_offering_id INTEGER NOT NULL,
                assigned_by_teacher_id INTEGER NOT NULL,
                created_at TEXT
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
        self.conn.executemany(
            """
            INSERT INTO course_materials (
                id, teacher_id, parent_id, root_id, name, material_path, node_type,
                preview_type, scope_level, owner_user_pk, school_code, school_name,
                college, department
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, 1, None, 1, "shared", "shared", "folder", "folder", "school", 1, "gxufl", "GXUFL", "info", "network"),
                (2, 1, 1, 1, "readme.md", "shared/readme.md", "file", "markdown", "school", 1, "gxufl", "GXUFL", "info", "network"),
                (3, 1, None, 3, "private.md", "private.md", "file", "markdown", "private", 1, "gxufl", "GXUFL", "info", "network"),
            ],
        )

    def tearDown(self):
        self.conn.close()

    def test_teacher_can_use_same_school_scoped_material(self):
        material = ensure_user_material_access(self.conn, 2, {"role": "teacher", "id": 2})

        self.assertEqual(2, int(material["id"]))

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
