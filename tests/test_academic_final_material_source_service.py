from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import unittest
from contextlib import ExitStack, nullcontext
from dataclasses import FrozenInstanceError
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from classroom_app.routers.materials_parts import ai_import_helpers, exports
from classroom_app.services.academic_final_material_service import (
    ACADEMIC_EXAM_ANALYSIS_TYPE,
    build_exam_analysis_export_payload,
    normalize_academic_final_material_payload,
)
from classroom_app.services.academic_final_material_source_service import (
    ACADEMIC_NATIVE_SOURCE_KEY,
    NativeAcademicSource,
    NativeAcademicSourceError,
    hydrate_academic_final_material_source,
    strip_academic_native_source,
)
from classroom_app.services.material_export_template_service import MaterialExportArtifact


class AcademicFinalMaterialSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.content = br"{\rtf1\ansi Native table original}"
        self.digest = hashlib.sha256(self.content).hexdigest()
        self.source = Path(self.temp.name) / "source.doc"
        self.source.write_bytes(self.content)
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.execute("CREATE TABLE course_materials (id INTEGER, file_hash TEXT)")
        self.conn.execute("INSERT INTO course_materials VALUES (12, ?)", (self.digest,))
        self.payload = {
            "document_type": "academic_exam_analysis",
            "export_payload": {
                "template_key": "academic_exam_analysis",
                "fields": {"course_name": "测试课程"},
                "structured": {"analysis_text": "课程分析"},
            },
        }
        self.record = {
            "id": 21,
            "document_type": "academic_exam_analysis",
            "document_type_label": "试卷分析表",
            "source_material_id": 12,
            "source_file_hash": self.digest,
            "source_file_name": "教务原件-试卷分析表.doc",
            "package_material_id": 11,
            "parsed_material_id": 13,
            "parent_material_id": None,
            "parsed_payload_json": json.dumps(self.payload, ensure_ascii=False),
        }
        self.resolve = self.enterContext(
            patch(
                "classroom_app.services.academic_final_material_source_service.resolve_global_file_path",
                return_value=self.source,
            )
        )

    def test_export_uses_exact_archived_bytes_without_changing_json_payload(self) -> None:
        hydrated = hydrate_academic_final_material_source(self.conn, self.record, self.payload)
        source = hydrated[ACADEMIC_NATIVE_SOURCE_KEY]
        self.assertIsInstance(source, NativeAcademicSource)
        self.assertEqual(self.content, source.content)
        self.assertEqual(self.digest, source.sha256)
        self.assertEqual("rtf", source.source_format)
        self.assertNotIn(ACADEMIC_NATIVE_SOURCE_KEY, self.payload)
        self.assertEqual(self.payload["export_payload"], hydrated["export_payload"])
        self.resolve.assert_called_once_with(self.digest)
        with self.assertRaises(FrozenInstanceError):
            source.content = b"changed"

    def test_archived_hash_survives_deleted_material_reference(self) -> None:
        self.conn.execute("DELETE FROM course_materials")
        hydrated = hydrate_academic_final_material_source(self.conn, self.record, self.payload)
        self.assertEqual(self.content, hydrated[ACADEMIC_NATIVE_SOURCE_KEY].content)

    def test_legacy_record_can_resolve_its_source_material_hash(self) -> None:
        self.record.pop("source_file_hash")
        hydrated = hydrate_academic_final_material_source(self.conn, self.record, self.payload)
        self.assertEqual(self.digest, hydrated[ACADEMIC_NATIVE_SOURCE_KEY].sha256)

    def test_conflicting_source_reference_is_rejected_before_file_access(self) -> None:
        self.conn.execute("UPDATE course_materials SET file_hash = ?", ("f" * 64,))
        with self.assertRaisesRegex(NativeAcademicSourceError, "归档记录不一致"):
            hydrate_academic_final_material_source(self.conn, self.record, self.payload)
        self.resolve.assert_not_called()

    def test_corrupted_original_never_falls_back_to_another_form(self) -> None:
        self.source.write_bytes(br"{\rtf1 changed content}")
        with self.assertRaisesRegex(NativeAcademicSourceError, "校验失败"):
            hydrate_academic_final_material_source(self.conn, self.record, self.payload)

    def test_missing_original_has_actionable_error(self) -> None:
        self.resolve.return_value = None
        with self.assertRaisesRegex(NativeAcademicSourceError, "重新同步"):
            hydrate_academic_final_material_source(self.conn, self.record, self.payload)

    def test_unsupported_original_is_rejected_even_when_hash_matches(self) -> None:
        content = b"<html>sign-in page instead of report</html>"
        digest = hashlib.sha256(content).hexdigest()
        self.source.write_bytes(content)
        self.record["source_file_hash"] = digest
        self.conn.execute("UPDATE course_materials SET file_hash = ?", (digest,))
        with self.assertRaisesRegex(NativeAcademicSourceError, "格式不受支持"):
            hydrate_academic_final_material_source(self.conn, self.record, self.payload)

    def test_path_like_source_identifier_cannot_reach_file_resolver(self) -> None:
        self.record["source_file_hash"] = str(self.source)
        with self.assertRaisesRegex(NativeAcademicSourceError, "文件标识无效"):
            hydrate_academic_final_material_source(self.conn, self.record, self.payload)
        self.resolve.assert_not_called()

    def test_oversized_original_is_rejected(self) -> None:
        with patch("classroom_app.services.academic_final_material_source_service._MAX_SOURCE_BYTES", 8):
            with self.assertRaisesRegex(NativeAcademicSourceError, "原件过大"):
                hydrate_academic_final_material_source(self.conn, self.record, self.payload)

    def test_grade_register_does_not_require_native_source(self) -> None:
        self.record.update(document_type="academic_grade_register", source_file_hash="", source_material_id=None)
        self.assertEqual(self.payload, hydrate_academic_final_material_source(self.conn, self.record, self.payload))
        self.resolve.assert_not_called()

    def test_serialized_runtime_values_are_removed_without_mutating_input(self) -> None:
        self.payload[ACADEMIC_NATIVE_SOURCE_KEY] = {"path": "untrusted.doc"}
        self.payload["export_payload"][ACADEMIC_NATIVE_SOURCE_KEY] = {"content": "untrusted"}
        cleaned = strip_academic_native_source(self.payload)
        self.assertNotIn(ACADEMIC_NATIVE_SOURCE_KEY, cleaned)
        self.assertNotIn(ACADEMIC_NATIVE_SOURCE_KEY, cleaned["export_payload"])
        self.assertIn(ACADEMIC_NATIVE_SOURCE_KEY, self.payload)
        self.assertIn(ACADEMIC_NATIVE_SOURCE_KEY, self.payload["export_payload"])

    def _mock_signature_resolution(self, stack: ExitStack) -> None:
        stack.enter_context(patch(
            "classroom_app.services.academic_final_material_service.repair_legacy_grade_register_roster_order",
            side_effect=lambda conn, row, payload: payload,
        ))
        stack.enter_context(patch(
            "classroom_app.services.academic_final_material_service.hydrate_academic_final_material_signature_paths",
            side_effect=lambda conn, payload, **kwargs: payload,
        ))

    def test_record_details_remain_json_serializable_and_do_not_read_source(self) -> None:
        with ExitStack() as stack:
            self._mock_signature_resolution(stack)
            detail = ai_import_helpers._build_ai_import_payload_from_record(self.record, self.conn)
        self.assertEqual(self.payload, json.loads(json.dumps(detail, ensure_ascii=False)))
        self.resolve.assert_not_called()

    def test_export_returns_conflict_when_archived_original_is_missing(self) -> None:
        self.resolve.return_value = None
        with ExitStack() as stack:
            self._mock_signature_resolution(stack)
            with self.assertRaises(HTTPException) as raised:
                ai_import_helpers._build_ai_import_payload_from_record(self.record, self.conn, for_export=True)
        self.assertEqual(409, raised.exception.status_code)
        self.assertIn("重新同步", raised.exception.detail)

    def test_preview_and_download_resolve_the_same_original(self) -> None:
        export_conn = MagicMock()

        def execute(query, values):
            if "material_ai_import_records" in query:
                result = MagicMock()
                result.fetchone.return_value = self.record
                return result
            return self.conn.execute(query, values)

        export_conn.execute.side_effect = execute
        with ExitStack() as stack:
            self._mock_signature_resolution(stack)
            stack.enter_context(patch.object(exports, "ensure_user_material_access", return_value={"id": 11}))
            stack.enter_context(patch.object(exports, "get_db_connection", return_value=nullcontext(export_conn)))
            builder = stack.enter_context(patch.object(
                exports, "build_material_export_artifact",
                return_value=MaterialExportArtifact(content=b"DOCX", filename="analysis.docx", media_type="application/octet-stream"),
            ))
            _, preview_payload, _ = exports._load_ai_import_record_preview_payload(export_conn, 21, {"id": 7})
            response = asyncio.run(exports.export_ai_import_record(record_id=21, format="docx", user={"id": 7}))
            try:
                exported_payload = builder.call_args.args[0]
                self.assertEqual(preview_payload[ACADEMIC_NATIVE_SOURCE_KEY], exported_payload[ACADEMIC_NATIVE_SOURCE_KEY])
                self.assertEqual(self.content, exported_payload[ACADEMIC_NATIVE_SOURCE_KEY].content)
                self.assertEqual(b"DOCX", Path(response.path).read_bytes())
            finally:
                asyncio.run(response.background())

    def test_native_layout_rejection_is_visible_in_preview_and_download(self) -> None:
        export_conn = MagicMock()
        export_conn.execute.return_value.fetchone.return_value = self.record
        message = "教务原件版式尚未支持，无法保证格式一致。"
        with ExitStack() as stack:
            stack.enter_context(patch.object(exports, "ensure_user_material_access", return_value={"id": 11}))
            stack.enter_context(patch.object(exports, "get_db_connection", side_effect=lambda: nullcontext(export_conn)))
            stack.enter_context(patch.object(exports, "_build_ai_import_payload_from_record", return_value=self.payload))
            stack.enter_context(patch.object(exports, "build_material_export_artifact", side_effect=NativeAcademicSourceError(message)))
            for route in (exports.export_ai_import_record, exports.preview_ai_import_record_export):
                with self.subTest(route=route.__name__):
                    with self.assertRaises(HTTPException) as raised:
                        asyncio.run(route(record_id=21, format="docx", user={"id": 7}))
                    self.assertEqual(409, raised.exception.status_code)
                    self.assertEqual(message, raised.exception.detail)

    def test_native_content_rejection_is_a_bad_request(self) -> None:
        with patch.object(exports, "build_material_export_artifact", side_effect=ValueError("分析正文超出原始单元格容量。")):
            with self.assertRaises(HTTPException) as raised:
                exports._build_material_export_for_response(self.payload, fallback_filename="analysis.docx", requested_format="docx")
        self.assertEqual(400, raised.exception.status_code)
        self.assertIn("单元格", raised.exception.detail)

    def test_other_document_errors_keep_their_existing_behavior(self) -> None:
        with patch.object(exports, "build_material_export_artifact", side_effect=ValueError("original error")):
            with self.assertRaisesRegex(ValueError, "original error"):
                exports._build_material_export_for_response({"document_type": "academic_grade_register"}, fallback_filename="grade.docx", requested_format="docx")


class AcademicExamAnalysisNativeSchemaTests(unittest.TestCase):
    def test_new_payload_declares_preserved_native_document_contract(self) -> None:
        payload = build_exam_analysis_export_payload({}, {})
        self.assertEqual("gxufl-academic-exam-analysis-v4", payload["schema_version"])
        layout = payload["layout_profile"]
        self.assertEqual("native_template_in_place", layout["render_mode"])
        self.assertEqual("native_layout_fingerprint", layout["source_validation"])
        self.assertEqual("native_source_chart", layout["chart_mode"])
        self.assertEqual(23, layout["table_rows"])
        self.assertEqual("note_in_original_last_row", layout["note_placement"])

    def test_old_reconstruction_settings_are_removed_without_altering_edits(self) -> None:
        legacy = {
            "schema_version": "gxufl-academic-exam-analysis-v3",
            "layout_profile": {"table_rows": 19, "font_size": 42, "note_placement": "paragraph_after_table"},
            "fields": {"department_review_opinion": "", "exam_form": "开卷"},
            "structured": {"analysis_text": "已经编辑的分析正文", "department_signature_id": 12},
        }
        before = json.loads(json.dumps(legacy, ensure_ascii=False))
        normalized = normalize_academic_final_material_payload(
            document_type=ACADEMIC_EXAM_ANALYSIS_TYPE,
            metadata={"department_review_opinion": "旧意见"},
            export_payload=legacy,
        )
        self.assertEqual(before, legacy)
        self.assertEqual(legacy["fields"], normalized["fields"])
        self.assertEqual(legacy["structured"], normalized["structured"])
        self.assertEqual("gxufl-academic-exam-analysis-v4", normalized["schema_version"])
        self.assertNotIn("font_size", normalized["layout_profile"])
        self.assertEqual(build_exam_analysis_export_payload({}, {})["layout_profile"], normalized["layout_profile"])


if __name__ == "__main__":
    unittest.main()
