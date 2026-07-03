import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from classroom_app.routers.materials_parts import final_material_helpers
from classroom_app.routers.materials_parts import generation_helpers
from classroom_app.services import exam_material_reverse_service


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
            self.assert_sql_placeholders_match_params(normalized, params)
            name = str(params[4])
            return _FakeCursor({"id": 17 if name == "Folder" else 18}, rowcount=1)
        if normalized.startswith("UPDATE course_materials SET root_id"):
            return _FakeCursor(rowcount=1)
        if normalized.startswith("INSERT INTO material_ai_import_records"):
            return _FakeCursor({"id": 19}, rowcount=1)
        raise AssertionError(f"Unexpected SQL: {normalized}")

    @staticmethod
    def assert_sql_placeholders_match_params(sql, params):
        placeholder_count = str(sql).count("?")
        if placeholder_count != len(tuple(params)):
            raise AssertionError(f"SQL placeholder count {placeholder_count} did not match params {len(tuple(params))}")


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

    def test_postgres_assessment_plan_import_record_preserves_export_payload(self):
        conn = _FakeMaterialConnection()
        export_payload = {
            "template_key": "assessment_plan",
            "document_group": "final_material",
            "document_type": "assessment_plan",
            "fields": {"course_name": "\u52a8\u6001web\u7a0b\u5e8f\u8bbe\u8ba1"},
            "structured": {
                "assessment_items": [
                    {
                        "assessment_form": "\u673a\u8bd5",
                        "content": "Spring Boot project delivery",
                        "score": "100",
                    }
                ],
                "total_score": 100,
            },
        }
        parse_result = SimpleNamespace(
            document_group="final_material",
            document_type="assessment_plan",
            document_type_label="\u8bfe\u7a0b\u8003\u6838\u8ba1\u5212\u8868",
            ai_used=True,
            extraction_method="ai",
            content_markdown="# assessment plan",
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
                source_file_name="assessment-plan.json",
                metadata_json="{}",
                parse_payload_json="{}",
                export_payload_json=json.dumps(export_payload, ensure_ascii=False),
                warnings_json="[]",
                content_quality_json="{}",
                now="2026-01-01T00:00:00",
            )

        self.assertEqual(19, record_id)
        params = conn.calls[0][1]
        self.assertEqual("final_material", params[4])
        self.assertEqual("assessment_plan", params[5])
        persisted_payload = json.loads(params[13])
        self.assertEqual(persisted_payload["template_key"], "assessment_plan")
        self.assertEqual(persisted_payload["structured"]["assessment_items"][0]["score"], "100")

    def test_postgres_exam_reverse_running_import_record_uses_returning(self):
        conn = _FakeMaterialConnection()

        with patch.object(exam_material_reverse_service, "get_configured_db_engine", return_value="postgres"):
            record_id = exam_material_reverse_service._insert_running_material_generation_record(
                conn,
                teacher_id=3,
                document_group="final_material",
                document_type="grading_rubric",
                document_type_label="Grading rubric",
                source_file_name="rubric.json",
                metadata_json="{}",
                now="2026-01-01T00:00:00",
            )

        self.assertEqual(19, record_id)
        self.assertIn("RETURNING id", conn.calls[0][0])
        self.assertEqual(9, len(conn.calls[0][1]))
        self.assertIn("'exam_reverse'", conn.calls[0][0])


if __name__ == "__main__":
    unittest.main()
