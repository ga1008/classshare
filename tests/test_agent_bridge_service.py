import sqlite3
import unittest
from unittest.mock import patch

from classroom_app.db import schema_gongwen
from classroom_app.db.schema_assignments import ensure_assignment_schema
from classroom_app.db.schema_classroom_activity import ensure_classroom_activity_schema
from classroom_app.db.schema_foundation import ensure_foundation_schema
from classroom_app.db.schema_materials_integrations import ensure_materials_integrations_schema
from classroom_app.services.agent_bridge_service import (
    example_queries_payload,
    mask_sensitive_cell,
    run_readonly_query,
)


class AgentBridgeServiceTests(unittest.TestCase):
    def _open_conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE sample_items (id INTEGER PRIMARY KEY, name TEXT, api_token TEXT)")
        conn.executemany(
            "INSERT INTO sample_items (name, api_token) VALUES (?, ?)",
            [(f"item-{idx}", f"token-{idx}") for idx in range(5)],
        )
        return conn

    def _open_example_schema_conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        schema_gongwen._SCHEMA_READY = False
        ensure_foundation_schema(conn)
        ensure_assignment_schema(conn)
        ensure_classroom_activity_schema(conn)
        ensure_materials_integrations_schema(conn)
        with patch.object(schema_gongwen, "get_configured_db_engine", return_value="sqlite"):
            schema_gongwen.ensure_gongwen_schema(conn)
        conn.execute("INSERT INTO classes (id, name, created_by_teacher_id) VALUES (?, ?, ?)", (1, "Class 1", 7))
        conn.execute("INSERT INTO courses (id, name, created_by_teacher_id) VALUES (?, ?, ?)", (1, "Course 1", 7))
        conn.execute(
            "INSERT INTO class_offerings (id, class_id, course_id, teacher_id) VALUES (?, ?, ?, ?)",
            (1, 1, 1, 7),
        )
        conn.execute(
            "INSERT INTO students (id, student_id_number, name, class_id) VALUES (?, ?, ?, ?)",
            (1, "S001", "Alice", 1),
        )
        conn.execute(
            """
            INSERT INTO assignments (id, course_id, class_offering_id, title, status, due_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (1, 1, 1, "Homework 1", "published", "2026-01-08T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )
        conn.execute(
            """
            INSERT INTO submissions (id, assignment_id, student_pk_id, student_name, submitted_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (1, "1", 1, "Alice", "2026-01-02T00:00:00+00:00"),
        )
        conn.execute(
            """
            INSERT INTO course_materials (id, teacher_id, material_path, name, node_type, preview_type, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (1, 7, "/materials/course-1/intro.md", "Intro", "file", "markdown", "2026-01-03T00:00:00+00:00"),
        )
        conn.execute(
            """
            INSERT INTO class_offering_sessions (
                id, class_offering_id, order_index, title, session_date, weekday, learning_material_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (1, 1, 1, "Session 1", "2026-01-04", 1, 1),
        )
        conn.execute(
            """
            INSERT INTO gongwen_documents (id, remote_id, title, sn, author, parsed_summary, parsed_text, publish_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (1, "remote-1", "Policy Notice", "GW-1", "Office", "policy summary", "policy body", "2026-01-05"),
        )
        conn.commit()
        return conn

    def test_readonly_query_applies_outer_limit_and_truncation(self):
        conn = self._open_conn()
        try:
            result = run_readonly_query(conn, "SELECT id, name FROM sample_items ORDER BY id", limit=2)

            self.assertEqual(["id", "name"], result["columns"])
            self.assertEqual(2, result["row_count"])
            self.assertTrue(result["truncated"])
            self.assertEqual([1, 2], [row["id"] for row in result["rows"]])
        finally:
            conn.close()

    def test_readonly_query_keeps_named_params_and_masks_sensitive_columns(self):
        conn = self._open_conn()
        try:
            result = run_readonly_query(
                conn,
                "SELECT name, api_token FROM sample_items WHERE name LIKE :keyword ORDER BY id",
                limit=10,
                params={"keyword": "item-%"},
            )

            self.assertEqual(5, result["row_count"])
            self.assertFalse(result["truncated"])
            self.assertEqual("item-0", result["rows"][0]["name"])
            self.assertEqual(mask_sensitive_cell("api_token", "token-0"), result["rows"][0]["api_token"])
        finally:
            conn.close()

    def test_example_queries_execute_against_current_schema(self):
        conn = self._open_example_schema_conn()
        params = {
            "teacher_id": 7,
            "class_offering_id": 1,
            "assignment_id": 1,
            "pattern": "%policy%",
        }
        try:
            for item in example_queries_payload():
                with self.subTest(purpose=item["purpose"]):
                    result = run_readonly_query(conn, item["sql"], limit=5, params=params)
                    self.assertGreater(len(result["columns"]), 0)
                    self.assertLessEqual(result["row_count"], 5)
        finally:
            conn.close()
            schema_gongwen._SCHEMA_READY = False


if __name__ == "__main__":
    unittest.main()
