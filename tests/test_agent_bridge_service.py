import asyncio
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import UploadFile

from classroom_app.db import schema_gongwen
from classroom_app.db.schema_assignments import ensure_assignment_schema
from classroom_app.db.schema_classroom_activity import ensure_classroom_activity_schema
from classroom_app.db.schema_foundation import ensure_foundation_schema
from classroom_app.db.schema_materials_integrations import ensure_materials_integrations_schema
from classroom_app.routers.ai import _process_chat_file
from classroom_app.services.agent_bridge_service import (
    example_queries_payload,
    mask_sensitive_cell,
    read_platform_file,
    run_readonly_query,
    unified_search,
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
        conn.execute(
            """
            INSERT INTO teachers (id, name, email, hashed_password, school_code, school_name, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (7, "Teacher", "teacher7@example.test", "hashed", "gxufl", "广西外国语学院", 1),
        )
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
            INSERT INTO assignments (id, course_id, class_offering_id, title, requirements_md, status, due_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                1,
                1,
                "Policy reflection homework",
                "Write about policy summary",
                "published",
                "2026-01-08T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
            ),
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
            (1, 7, "/materials/course-1/policy-intro.md", "Policy intro", "file", "markdown", "2026-01-03T00:00:00+00:00"),
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
            INSERT INTO gongwen_documents (
                id, remote_id, attr_school_code, attr_level, openness,
                title, sn, author, parsed_summary, parsed_text, publish_time, parsed_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                "remote-1",
                "gxufl",
                "school",
                "school",
                "Policy Notice",
                "GW-1",
                "Office",
                "policy summary",
                "policy body",
                "2026-01-05",
                "done",
            ),
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

    def test_unified_search_scopes_return_clickable_teacher_scoped_results(self):
        conn = self._open_example_schema_conn()
        try:
            gongwen = unified_search(conn, teacher_id=7, scope="gongwen", keyword="policy", limit=5)
            materials = unified_search(conn, teacher_id=7, scope="materials", keyword="policy", limit=5)
            assignments = unified_search(conn, teacher_id=7, scope="assignments", keyword="policy", limit=5)
            combined = unified_search(conn, teacher_id=7, scope="all", keyword="policy", limit=5)

            self.assertEqual(["gongwen"], [item["type"] for item in gongwen])
            self.assertIn("/manage/gongwen?", gongwen[0]["url"])
            self.assertIn("Policy Notice", gongwen[0]["title"])

            self.assertEqual(["material"], [item["type"] for item in materials])
            self.assertEqual("/materials/view/1", materials[0]["url"])
            self.assertIn("Policy intro", materials[0]["title"])

            self.assertEqual(["assignment"], [item["type"] for item in assignments])
            self.assertEqual("/assignment/1", assignments[0]["url"])
            self.assertIn("Policy reflection homework", assignments[0]["title"])

            self.assertEqual({"gongwen", "material", "assignment"}, {item["type"] for item in combined})

            for result in (gongwen + materials + assignments):
                self.assertGreaterEqual(set(result), {"type", "title", "snippet", "url", "date"})
                self.assertTrue(result["url"].startswith("/"))
        finally:
            conn.close()
            schema_gongwen._SCHEMA_READY = False

    def test_unified_search_does_not_cross_teacher_material_or_assignment_scope(self):
        conn = self._open_example_schema_conn()
        try:
            conn.execute(
                """
                INSERT INTO courses (id, name, created_by_teacher_id)
                VALUES (?, ?, ?)
                """,
                (2, "Other Course", 99),
            )
            conn.execute(
                """
                INSERT INTO assignments (id, course_id, class_offering_id, title, requirements_md, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (2, 2, None, "Policy homework from another teacher", "policy", "published", "2026-01-06T00:00:00+00:00"),
            )
            conn.execute(
                """
                INSERT INTO course_materials (id, teacher_id, material_path, name, node_type, preview_type, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (2, 99, "/materials/other/policy.md", "Other policy material", "file", "markdown", "2026-01-06T00:00:00+00:00"),
            )
            conn.commit()

            materials = unified_search(conn, teacher_id=7, scope="materials", keyword="policy", limit=10)
            assignments = unified_search(conn, teacher_id=7, scope="assignments", keyword="policy", limit=10)

            self.assertEqual([1], [int(item["url"].rsplit("/", 1)[-1]) for item in materials])
            self.assertEqual(["/assignment/1"], [item["url"] for item in assignments])
        finally:
            conn.close()
            schema_gongwen._SCHEMA_READY = False

    def test_file_bridge_extracts_docx_like_chat_attachment_parser(self):
        try:
            from docx import Document
        except ImportError as exc:
            self.skipTest(f"python-docx unavailable: {exc}")

        expected_lines = ["Agent bridge DOCX consistency", "Second paragraph from platform material."]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "agent-docx-fixture.docx"
            document = Document()
            for line in expected_lines:
                document.add_paragraph(line)
            document.save(path)

            with patch(
                "classroom_app.services.agent_bridge_service.allowed_file_roots",
                return_value=[root],
            ):
                bridge_result = read_platform_file(str(path))

            chat_result = asyncio.run(self._process_chat_attachment(path, "agent-docx-fixture.docx"))

        self.assertTrue(bridge_result["extracted"])
        self.assertFalse(bridge_result["truncated"])
        self.assertEqual(path.name, Path(bridge_result["path"]).name)
        self.assertEqual(chat_result["type"], "text")
        self.assertEqual(chat_result["content"], bridge_result["content"])
        for line in expected_lines:
            self.assertIn(line, bridge_result["content"])

    def test_file_bridge_extracts_pdf_like_chat_attachment_parser(self):
        try:
            import fitz
        except ImportError as exc:
            self.skipTest(f"PyMuPDF unavailable: {exc}")

        expected_text = "Agent bridge PDF consistency"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "agent-pdf-fixture.pdf"
            document = fitz.open()
            page = document.new_page()
            page.insert_text((72, 72), expected_text)
            document.save(path)
            document.close()

            with patch(
                "classroom_app.services.agent_bridge_service.allowed_file_roots",
                return_value=[root],
            ):
                bridge_result = read_platform_file(str(path))

            chat_result = asyncio.run(self._process_chat_attachment(path, "agent-pdf-fixture.pdf"))

        self.assertTrue(bridge_result["extracted"])
        self.assertFalse(bridge_result["truncated"])
        self.assertEqual(path.name, Path(bridge_result["path"]).name)
        self.assertEqual(chat_result["type"], "text")
        self.assertEqual(chat_result["content"], bridge_result["content"])
        self.assertIn(expected_text, bridge_result["content"])

    async def _process_chat_attachment(self, path: Path, filename: str) -> dict:
        upload = UploadFile(file=io.BytesIO(path.read_bytes()), filename=filename)
        return await _process_chat_file(upload)


if __name__ == "__main__":
    unittest.main()
