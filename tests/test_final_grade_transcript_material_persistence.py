import asyncio
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from classroom_app.routers.materials_parts import ai_import_helpers, final_material_helpers


class _Cursor:
    def __init__(self, row=None):
        self._row = row
        self.rowcount = 1

    def fetchone(self):
        return self._row


class _RecordingConnection:
    def __init__(self, *, current_record=None):
        self.current_record = current_record
        self.calls = []
        self.committed = False

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        self.calls.append((normalized, tuple(params)))
        if normalized.startswith("SELECT * FROM material_ai_import_records WHERE id ="):
            return _Cursor(self.current_record or {"id": 77, "parse_status": "completed"})
        if normalized.startswith("SELECT id FROM class_offerings"):
            return _Cursor({"id": 30})
        return _Cursor()

    def commit(self):
        self.committed = True


def _parse_result():
    export_payload = {
        "fields": {
            "course_name": "Server Administration",
            "class_name": "SE2406",
            "teacher_name": "Teacher Zhang",
        },
        "structured": {"students": []},
    }
    return SimpleNamespace(
        document_group="final_material",
        document_type="final_grade_transcript",
        document_type_label="Final Grade Transcript",
        extraction_method="final_grade_transcript_local_generation",
        ai_used=False,
        metadata={"class_offering_id": 30, **export_payload["fields"]},
        content_markdown="# Final Grade Transcript",
        export_payload=export_payload,
        warnings=[],
        content_quality={"status": "ok"},
        parsed_payload={"export_payload": export_payload},
    )


class FinalGradeTranscriptMaterialPersistenceTests(unittest.TestCase):
    def test_generated_transcript_persists_one_real_xlsx_material_without_readme_folder(self):
        conn = _RecordingConnection()

        @contextmanager
        def fake_connection():
            yield conn

        insert_file_calls = []
        insert_record_calls = []

        def insert_file(_conn, **kwargs):
            insert_file_calls.append(kwargs)
            return 55

        def insert_record(_conn, **kwargs):
            insert_record_calls.append(kwargs)
            return 77

        with (
            patch.object(final_material_helpers, "build_final_grade_transcript_xlsx", return_value=b"xlsx-bytes"),
            patch.object(final_material_helpers, "_write_material_file", new=AsyncMock()),
            patch.object(final_material_helpers, "get_db_connection", side_effect=fake_connection),
            patch.object(
                final_material_helpers,
                "_load_final_material_classroom_context",
                return_value={"course_name": "Server Administration"},
            ),
            patch.object(
                final_material_helpers,
                "load_teacher_org_scope",
                return_value={
                    "school_code": "gxufl",
                    "school_name": "GXUFL",
                    "college": "School of IT",
                    "department": "Software Engineering",
                },
            ),
            patch.object(
                final_material_helpers,
                "make_unique_material_name",
                side_effect=lambda _conn, _teacher, _parent, name: name,
            ),
            patch.object(final_material_helpers, "_insert_material_file_row", side_effect=insert_file),
            patch.object(
                final_material_helpers,
                "_insert_completed_material_ai_import_record",
                side_effect=insert_record,
            ),
            patch.object(final_material_helpers, "refresh_root_git_metadata"),
            patch.object(
                final_material_helpers,
                "_serialize_material_ai_import_task",
                return_value={"package_material_id": 55},
            ),
            patch.object(final_material_helpers, "get_configured_db_engine", return_value="sqlite"),
        ):
            result = asyncio.run(
                final_material_helpers._create_generated_final_grade_transcript_material(
                    class_offering_id=30,
                    parent_id=None,
                    parse_result=_parse_result(),
                    user={"id": 1, "role": "teacher"},
                )
            )

        self.assertEqual({"package_material_id": 55}, result)
        self.assertEqual(1, len(insert_file_calls))
        self.assertTrue(insert_file_calls[0]["name"].endswith(".xlsx"))
        self.assertIsNone(insert_file_calls[0]["parent_id"])
        self.assertEqual("spreadsheet", insert_file_calls[0]["file_profile"]["preview_type"])
        self.assertEqual("Excel", insert_file_calls[0]["file_profile"]["type_label"])
        self.assertEqual(55, insert_record_calls[0]["package_id"])
        self.assertEqual(55, insert_record_calls[0]["parsed_id"])
        self.assertTrue(
            any(
                "SET source_material_id = ?" in sql and params[0] == 55
                for sql, params in conn.calls
            )
        )
        self.assertTrue(
            any("INSERT OR IGNORE INTO course_material_assignments" in sql for sql, _ in conn.calls)
        )
        self.assertTrue(conn.committed)

    def test_imported_transcript_reuses_uploaded_xlsx_as_the_only_visible_material(self):
        conn = _RecordingConnection(current_record={"id": 88, "parse_status": "running"})

        @contextmanager
        def fake_connection():
            yield conn

        insert_file_calls = []
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "final-grade-template.xlsx"
            source_path.write_bytes(b"uploaded-xlsx")
            record = {
                "teacher_id": 1,
                "parent_material_id": None,
                "source_file_name": "final-grade-template.xlsx",
                "source_file_hash": "source-hash",
                "source_file_size": len(b"uploaded-xlsx"),
                "source_mime_type": (
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                ),
            }

            with (
                patch.object(ai_import_helpers, "resolve_global_file_path", return_value=source_path),
                patch.object(ai_import_helpers, "get_db_connection", side_effect=fake_connection),
                patch.object(
                    ai_import_helpers,
                    "load_teacher_org_scope",
                    return_value={
                        "school_code": "gxufl",
                        "school_name": "GXUFL",
                        "college": "School of IT",
                        "department": "Software Engineering",
                    },
                ),
                patch.object(
                    ai_import_helpers,
                    "make_unique_material_name",
                    side_effect=lambda _conn, _teacher, _parent, name: name,
                ),
                patch.object(
                    ai_import_helpers,
                    "_insert_material_file_row",
                    side_effect=lambda _conn, **kwargs: insert_file_calls.append(kwargs) or 66,
                ),
                patch.object(ai_import_helpers, "refresh_root_git_metadata"),
                patch.object(ai_import_helpers, "get_configured_db_engine", return_value="sqlite"),
            ):
                asyncio.run(
                    ai_import_helpers._persist_final_grade_transcript_import_success(
                        record_id=88,
                        record=record,
                        parse_result=_parse_result(),
                    )
                )

        self.assertEqual(1, len(insert_file_calls))
        self.assertEqual("final-grade-template.xlsx", insert_file_calls[0]["name"])
        self.assertTrue(
            any(
                "SET package_material_id = ?" in sql and params[:3] == (66, 66, 66)
                for sql, params in conn.calls
            )
        )
        self.assertFalse(any("readme.md" in str(params) for _, params in conn.calls))
        self.assertTrue(conn.committed)


if __name__ == "__main__":
    unittest.main()
