import unittest
from types import SimpleNamespace
from unittest.mock import patch

from classroom_app.routers.materials_parts import final_material_helpers
from classroom_app.routers.materials_parts import generation_helpers


class _FakeCursor:
    def __init__(self, row=None, rowcount=0):
        self._row = row
        self.rowcount = rowcount
        self.lastrowid = int((row or {}).get("id") or 0)

    def fetchone(self):
        return self._row


class _FakeMaterialConnection:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        self.calls.append((normalized, tuple(params)))
        if normalized.startswith("INSERT INTO course_materials"):
            name = str(params[4])
            return _FakeCursor({"id": 17 if name == "Folder" else 18}, rowcount=1)
        if normalized.startswith("UPDATE course_materials SET root_id"):
            return _FakeCursor(rowcount=1)
        if normalized.startswith("INSERT INTO material_ai_import_records"):
            return _FakeCursor({"id": 19}, rowcount=1)
        raise AssertionError(f"Unexpected SQL: {normalized}")


class MaterialsPostgresWriteTests(unittest.TestCase):
    def _owner_scope(self):
        return {
            "school_code": "S",
            "school_name": "School",
            "college": "College",
            "department": "Department",
        }

    def test_postgres_material_folder_insert_uses_returning_and_root_backfill(self):
        conn = _FakeMaterialConnection()

        with patch.object(generation_helpers, "get_configured_db_engine", return_value="postgres"):
            folder_id, root_id = generation_helpers._insert_material_folder_row(
                conn,
                user={"id": 3},
                name="Folder",
                material_path="Folder",
                parent_id=None,
                inherited_root_id=None,
                owner_scope=self._owner_scope(),
                now="2026-01-01T00:00:00",
            )

        self.assertEqual((17, 17), (folder_id, root_id))
        self.assertIn("RETURNING id", conn.calls[0][0])
        self.assertTrue(conn.calls[1][0].startswith("UPDATE course_materials SET root_id"))

    def test_postgres_material_file_insert_uses_returning_and_root_backfill(self):
        conn = _FakeMaterialConnection()

        with patch.object(generation_helpers, "get_configured_db_engine", return_value="postgres"):
            file_id = generation_helpers._insert_material_file_row(
                conn,
                user={"id": 3},
                name="File",
                material_path="File.md",
                parent_id=None,
                root_id=None,
                file_profile={
                    "mime_type": "text/markdown",
                    "preview_type": "markdown",
                    "ai_capability": "markdown",
                    "file_ext": ".md",
                },
                file_hash="hash-1",
                file_size=10,
                owner_scope=self._owner_scope(),
                now="2026-01-01T00:00:00",
            )

        self.assertEqual(18, file_id)
        self.assertIn("RETURNING id", conn.calls[0][0])
        self.assertTrue(conn.calls[1][0].startswith("UPDATE course_materials SET root_id"))

    def test_postgres_completed_material_import_record_uses_returning(self):
        conn = _FakeMaterialConnection()
        parse_result = SimpleNamespace(
            document_group="final",
            document_type="final_report",
            document_type_label="Final report",
            ai_used=True,
            extraction_method="ai",
            content_markdown="# Final",
            content_quality={"status": "ok"},
        )

        with patch.object(final_material_helpers, "get_configured_db_engine", return_value="postgres"):
            record_id = final_material_helpers._insert_completed_material_ai_import_record(
                conn,
                user_id=3,
                package_id=17,
                parsed_id=18,
                parent_id=None,
                parse_result=parse_result,
                source_file_name="final.json",
                metadata_json="{}",
                parse_payload_json="{}",
                export_payload_json="{}",
                warnings_json="[]",
                content_quality_json="{}",
                now="2026-01-01T00:00:00",
            )

        self.assertEqual(19, record_id)
        self.assertIn("RETURNING id", conn.calls[0][0])
        self.assertEqual(3, conn.calls[0][1][0])


if __name__ == "__main__":
    unittest.main()
