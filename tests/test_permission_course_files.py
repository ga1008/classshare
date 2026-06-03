import sqlite3
import unittest

from fastapi import HTTPException

from classroom_app.routers.files import _update_course_file_metadata
from classroom_app.services.resource_access_service import can_read_scoped_resource


class CourseFilePermissionTests(unittest.TestCase):
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
            CREATE TABLE courses (
                id INTEGER PRIMARY KEY,
                created_by_teacher_id INTEGER,
                school_code TEXT,
                school_name TEXT,
                college TEXT,
                department TEXT
            );
            CREATE TABLE course_files (
                id INTEGER PRIMARY KEY,
                course_id INTEGER NOT NULL,
                file_name TEXT NOT NULL,
                file_hash TEXT,
                file_size INTEGER DEFAULT 0,
                description TEXT DEFAULT '',
                original_link TEXT DEFAULT '',
                uploaded_by_teacher_id INTEGER,
                owner_role TEXT DEFAULT 'teacher',
                owner_user_pk INTEGER,
                scope_level TEXT DEFAULT 'private',
                class_offering_id INTEGER,
                class_id INTEGER,
                school_code TEXT,
                school_name TEXT,
                college TEXT,
                department TEXT
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
            INSERT INTO teacher_organization_memberships (
                id, teacher_id, school_code, school_name, college, department,
                is_primary, is_active, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, 1, "gxufl", "GXUFL", "info", "network", 1, 1, "2026-06-03"),
                (2, 2, "gxufl", "GXUFL", "info", "network", 1, 1, "2026-06-03"),
            ],
        )
        self.conn.execute(
            """
            INSERT INTO courses (id, created_by_teacher_id, school_code, school_name, college, department)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (10, 1, "gxufl", "GXUFL", "info", "network"),
        )
        self.conn.executemany(
            """
            INSERT INTO course_files (
                id, course_id, file_name, file_hash, description, original_link,
                uploaded_by_teacher_id, owner_user_pk, scope_level, school_code,
                school_name, college, department
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, 10, "owner.md", "h1", "old", "", 1, 1, "school", "gxufl", "GXUFL", "info", "network"),
                (2, 10, "classroom.md", "h2", "old", "", 2, 2, "classroom", "gxufl", "GXUFL", "info", "network"),
            ],
        )

    def tearDown(self):
        self.conn.close()

    def _file(self, file_id: int):
        return self.conn.execute(
            """
            SELECT cf.*, c.created_by_teacher_id
            FROM course_files cf
            JOIN courses c ON c.id = cf.course_id
            WHERE cf.id = ?
            """,
            (file_id,),
        ).fetchone()

    def _description(self, file_id: int) -> str:
        row = self.conn.execute("SELECT description FROM course_files WHERE id = ?", (file_id,)).fetchone()
        return str(row["description"] or "")

    def test_owner_can_update_course_file_metadata(self):
        result = _update_course_file_metadata(
            self.conn,
            file_id=1,
            user={"role": "teacher", "id": 1},
            description="new",
            original_link="https://example.com/file",
        )

        self.assertEqual("new", result["description"])
        self.assertEqual("new", self._description(1))

    def test_same_school_reader_cannot_update_course_file_metadata(self):
        readable_file = self._file(1)
        self.assertTrue(can_read_scoped_resource(self.conn, readable_file, {"role": "teacher", "id": 2}))

        with self.assertRaises(HTTPException) as ctx:
            _update_course_file_metadata(
                self.conn,
                file_id=1,
                user={"role": "teacher", "id": 2},
                description="stolen",
            )

        self.assertEqual(403, ctx.exception.status_code)
        self.assertEqual("old", self._description(1))

    def test_classroom_file_owner_can_update_even_when_course_creator_differs(self):
        result = _update_course_file_metadata(
            self.conn,
            file_id=2,
            user={"role": "teacher", "id": 2},
            description="owned by uploader",
        )

        self.assertEqual("owned by uploader", result["description"])
        self.assertEqual("owned by uploader", self._description(2))


if __name__ == "__main__":
    unittest.main()
