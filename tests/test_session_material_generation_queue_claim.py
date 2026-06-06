import sqlite3
import unittest
from unittest.mock import patch

from classroom_app.services import session_material_generation_service as service


class _FakeCursor:
    def __init__(self, row=None, rowcount=0):
        self._row = row
        self.rowcount = rowcount
        self.lastrowid = int((row or {}).get("id") or 0)

    def fetchone(self):
        return self._row


def _task_row(task_id=7, *, status=service.TASK_STATUS_QUEUED):
    return {
        "id": task_id,
        "class_offering_id": 10,
        "session_id": 20,
        "teacher_id": 30,
        "trigger_mode": "guided",
        "status": status,
        "document_type": "lesson",
        "requirement_text": "make a lesson note",
        "request_payload_json": '{"example_documents":[]}',
        "result_payload_json": "",
        "generated_material_id": None,
        "generated_material_path": "",
        "error_message": "",
        "created_at": "2026-01-01T00:00:00",
        "started_at": "",
        "completed_at": "",
        "updated_at": "2026-01-01T00:00:00",
        "order_index": 1,
        "session_title": "Session 1",
        "session_content": "Intro",
        "section_count": 1,
        "session_date": "2026-01-01",
        "schedule_info": "",
        "course_name": "Course",
        "course_description": "",
        "class_name": "Class",
        "class_description": "",
        "semester_name": "Semester",
        "teacher_name": "Teacher",
    }


class _FakePostgresGenerationConnection:
    def __init__(self, *, claim_row=None, context_row=None, insert_row=None):
        self.calls = []
        self.commits = 0
        self.claim_row = claim_row if claim_row is not None else {"id": 7}
        self.context_row = context_row if context_row is not None else _task_row(7, status=service.TASK_STATUS_RUNNING)
        self.insert_row = insert_row if insert_row is not None else _task_row(8)

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        self.calls.append((normalized, tuple(params)))
        if normalized.startswith("UPDATE session_material_generation_tasks") and "FOR UPDATE SKIP LOCKED" in normalized:
            return _FakeCursor(self.claim_row, rowcount=1 if self.claim_row else 0)
        if normalized.startswith("UPDATE session_material_generation_tasks") and "WHERE status IN" in normalized:
            return _FakeCursor(rowcount=0)
        if normalized.startswith("SELECT * FROM session_material_generation_tasks"):
            if "WHERE id = ?" in normalized:
                return _FakeCursor(self.insert_row)
            return _FakeCursor(None)
        if normalized.startswith("INSERT INTO session_material_generation_tasks"):
            return _FakeCursor(self.insert_row, rowcount=1)
        if normalized.startswith("SELECT t.*,"):
            return _FakeCursor(self.context_row)
        raise AssertionError(f"Unexpected SQL: {normalized}")

    def commit(self):
        self.commits += 1


class _FakePostgresMaterialConnection:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        self.calls.append((normalized, tuple(params)))
        if normalized.startswith("INSERT INTO course_materials"):
            name = str(params[4])
            return _FakeCursor({"id": 88 if name == "Folder" else 89}, rowcount=1)
        if normalized.startswith("UPDATE course_materials SET root_id"):
            return _FakeCursor(rowcount=1)
        if normalized.startswith("SELECT * FROM course_materials WHERE id"):
            row_id = int(params[0])
            return _FakeCursor({"id": row_id, "root_id": row_id, "name": "Folder" if row_id == 88 else "File"})
        raise AssertionError(f"Unexpected SQL: {normalized}")


class SessionMaterialGenerationQueueClaimTests(unittest.TestCase):
    def _sqlite_conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE teachers (
                id INTEGER PRIMARY KEY,
                name TEXT
            );
            CREATE TABLE classes (
                id INTEGER PRIMARY KEY,
                name TEXT,
                description TEXT
            );
            CREATE TABLE courses (
                id INTEGER PRIMARY KEY,
                name TEXT,
                description TEXT
            );
            CREATE TABLE academic_semesters (
                id INTEGER PRIMARY KEY,
                name TEXT
            );
            CREATE TABLE class_offerings (
                id INTEGER PRIMARY KEY,
                class_id INTEGER,
                course_id INTEGER,
                teacher_id INTEGER,
                semester_id INTEGER,
                schedule_info TEXT
            );
            CREATE TABLE class_offering_sessions (
                id INTEGER PRIMARY KEY,
                class_offering_id INTEGER,
                order_index INTEGER,
                title TEXT,
                content TEXT,
                section_count INTEGER,
                session_date TEXT,
                learning_material_id INTEGER
            );
            CREATE TABLE session_material_generation_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                class_offering_id INTEGER NOT NULL,
                session_id INTEGER NOT NULL,
                teacher_id INTEGER NOT NULL,
                trigger_mode TEXT NOT NULL DEFAULT 'guided',
                status TEXT NOT NULL DEFAULT 'queued',
                document_type TEXT DEFAULT '',
                requirement_text TEXT DEFAULT '',
                request_payload_json TEXT,
                result_payload_json TEXT,
                generated_material_id INTEGER,
                generated_material_path TEXT DEFAULT '',
                error_message TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                started_at TEXT,
                completed_at TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO teachers (id, name) VALUES (30, 'Teacher');
            INSERT INTO classes (id, name, description) VALUES (40, 'Class', '');
            INSERT INTO courses (id, name, description) VALUES (50, 'Course', '');
            INSERT INTO academic_semesters (id, name) VALUES (60, 'Semester');
            INSERT INTO class_offerings
                (id, class_id, course_id, teacher_id, semester_id, schedule_info)
            VALUES
                (10, 40, 50, 30, 60, '');
            INSERT INTO class_offering_sessions
                (id, class_offering_id, order_index, title, content, section_count, session_date, learning_material_id)
            VALUES
                (20, 10, 1, 'Session 1', 'Intro', 1, '2026-01-01', NULL);
            INSERT INTO session_material_generation_tasks
                (id, class_offering_id, session_id, teacher_id, status, request_payload_json, updated_at)
            VALUES
                (1, 10, 20, 30, 'queued', '{}', '2026-01-01T00:00:00'),
                (2, 10, 20, 30, 'running', '{}', '2026-01-01T00:00:00'),
                (3, 10, 20, 30, 'completed', '{}', '2026-01-01T00:00:00');
            """
        )
        return conn

    def test_sqlite_generation_claim_requires_queued_status(self):
        conn = self._sqlite_conn()
        try:
            claimed = service._claim_generation_task_for_run(conn, 1, engine="sqlite")
            running_claim = service._claim_generation_task_for_run(conn, 2, engine="sqlite")
            completed_claim = service._claim_generation_task_for_run(conn, 3, engine="sqlite")
            rows = conn.execute(
                "SELECT id, status FROM session_material_generation_tasks ORDER BY id"
            ).fetchall()

            self.assertEqual(1, claimed["id"])
            self.assertEqual(service.TASK_STATUS_RUNNING, claimed["status"])
            self.assertIsNone(running_claim)
            self.assertIsNone(completed_claim)
            self.assertEqual(
                [service.TASK_STATUS_RUNNING, service.TASK_STATUS_RUNNING, service.TASK_STATUS_COMPLETED],
                [row["status"] for row in rows],
            )
        finally:
            conn.close()

    def test_postgres_generation_claim_uses_skip_locked_returning(self):
        conn = _FakePostgresGenerationConnection()

        claimed = service._claim_generation_task_for_run(conn, 7, engine="postgres")

        self.assertEqual(7, claimed["id"])
        self.assertEqual(service.TASK_STATUS_RUNNING, claimed["status"])
        self.assertEqual(1, conn.commits)
        claim_sql, claim_params = conn.calls[0]
        self.assertIn("FOR UPDATE SKIP LOCKED", claim_sql)
        self.assertIn("RETURNING id", claim_sql)
        self.assertIn("AND status = ?", claim_sql)
        self.assertEqual(service.TASK_STATUS_RUNNING, claim_params[0])
        self.assertEqual(7, claim_params[3])
        self.assertEqual(service.TASK_STATUS_QUEUED, claim_params[4])

    def test_postgres_create_generation_task_uses_returning(self):
        conn = _FakePostgresGenerationConnection(insert_row=_task_row(8))

        with patch.object(service, "get_configured_db_engine", return_value="postgres"):
            created = service.create_generation_task(
                conn,
                class_offering_id=10,
                session_id=20,
                teacher_id=30,
                trigger_mode="guided",
                document_type="lesson",
                requirement_text="make a lesson note",
                example_documents=[],
            )

        insert_sql, insert_params = conn.calls[2]
        self.assertEqual(8, created["id"])
        self.assertIn("RETURNING id", insert_sql)
        self.assertEqual(10, insert_params[0])
        self.assertEqual(20, insert_params[1])
        self.assertEqual(30, insert_params[2])
        self.assertEqual(service.TASK_STATUS_QUEUED, insert_params[4])

    def test_postgres_create_generated_folder_row_uses_insert_returning_helper(self):
        conn = _FakePostgresMaterialConnection()

        with patch.object(service, "get_configured_db_engine", return_value="postgres"):
            row = service._create_folder_row(
                conn,
                teacher_id=30,
                parent_id=None,
                root_id=None,
                material_path="Folder",
                name="Folder",
                now="2026-01-01T00:00:00",
            )

        self.assertEqual(88, row["id"])
        self.assertEqual(88, row["root_id"])
        self.assertIn("RETURNING id", conn.calls[0][0])
        self.assertTrue(conn.calls[1][0].startswith("UPDATE course_materials SET root_id"))

    def test_postgres_create_generated_file_row_uses_insert_returning_helper(self):
        conn = _FakePostgresMaterialConnection()

        with patch.object(service, "get_configured_db_engine", return_value="postgres"), patch.object(
            service, "_store_markdown_bytes", return_value=("hash-1", 12)
        ):
            row = service._create_file_row(
                conn,
                teacher_id=30,
                parent_id=None,
                root_id=None,
                material_path="File.md",
                name="File.md",
                content="# File",
                now="2026-01-01T00:00:00",
            )

        self.assertEqual(89, row["id"])
        self.assertEqual(89, row["root_id"])
        self.assertIn("RETURNING id", conn.calls[0][0])
        self.assertTrue(conn.calls[1][0].startswith("UPDATE course_materials SET root_id"))

    def test_generation_claim_unknown_engine_fails_fast(self):
        conn = self._sqlite_conn()
        try:
            with self.assertRaises(ValueError):
                service._claim_generation_task_for_run(conn, 1, engine="mysql")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
