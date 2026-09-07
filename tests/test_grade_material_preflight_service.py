import asyncio
import copy
from contextlib import closing, contextmanager, ExitStack
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from classroom_app.services.grade_material_preflight_service import build_grade_material_preflight, confirm_grade_material_preflight
from classroom_app.services.grade_source_preflight_service import build_grade_source_preflight
from classroom_app.services.grading_revision_service import activate_submission_grade_revision
from classroom_app.routers.materials_parts import final_materials as routes
from classroom_app.routers.materials_parts import final_material_helpers as helpers
from classroom_app.routers.manage_parts import classes_courses_offerings as offerings
from tests import test_exam_grade_record_service as exam_fixture
from tests import test_grade_publication_service as publication_fixture
from tests import test_score_projection_service as score_fixture


class GradeMaterialPreflightTests(unittest.TestCase):
    def setUp(self):
        self.fixture = exam_fixture.ExamGradeRecordServiceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.conn = self.fixture.conn
        self.selection = {"document_type": "exam_grade_record", "exam_assignment_id": 301}

    def build(self):
        return routes._build_local_grade_export(self.conn, selection=self.selection,
            class_offering_id=30, teacher_id=1, classroom_context={})

    @contextmanager
    def db(self):
        yield self.conn

    def confirmation(self, preview):
        return {"preflight_confirmed": True, "expected_preflight_hash": preview["source_hash"],
                "accepted_preflight_warning_codes": [item["code"] for item in preview["warnings"]]}

    def test_real_projection_hash_is_stable_and_missing_is_distinct_from_zero(self):
        export = self.build()
        first = build_grade_material_preflight(export)
        export["structured"]["attendance_sync"] = {"checked_at": "another-time"}
        self.assertEqual(first["source_hash"], build_grade_material_preflight(export)["source_hash"])
        self.assertTrue(first["source_snapshots"])
        self.assertEqual(first["student_count"], len(export["structured"]["students"]))
        self.assertEqual(first["full_score"], export["fields"]["total_score"])
        export["structured"]["students"][0]["total_score"] = 0
        zero = build_grade_material_preflight(export)
        self.assertEqual(zero["students"][0]["score"], 0)
        export["structured"]["students"][0]["total_score"] = None
        missing = build_grade_material_preflight(export)
        self.assertIsNone(missing["students"][0]["score"])
        self.assertNotEqual(zero["source_hash"], missing["source_hash"])

    def test_confirmation_checks_flags_warnings_and_changed_source(self):
        preview = build_grade_material_preflight(self.build())
        good = self.confirmation(preview)
        saved = confirm_grade_material_preflight(preview, good, teacher_id=1)
        self.assertEqual(saved["teacher_id"], 1)
        for bad in ({}, {**good, "preflight_confirmed": False}, {**good, "expected_preflight_hash": "a" * 64},
                    {**good, "accepted_preflight_warning_codes": []}):
            with self.subTest(bad=bad), self.assertRaises(HTTPException):
                confirm_grade_material_preflight(preview, bad, teacher_id=1)
        changed = self.build()
        changed["structured"]["source_preflight"]["source_snapshots"][0]["review_required"] = True
        with self.assertRaises(HTTPException):
            confirm_grade_material_preflight(build_grade_material_preflight(changed), good, teacher_id=1)

    def test_refresh_previews_manual_overwrite_and_scale_change_without_mutating_old(self):
        export = self.build()
        old_payload = copy.deepcopy(export)
        old_payload["structured"]["manual_edit_log"] = [{"teacher": "教师", "score": 0}]
        old_payload["structured"]["score_adjustment_policy"] = {}
        old_payload["structured"]["students"][0]["total_score"] = 0
        record = {"id": 1, "updated_at": "v1", "export_payload_json": json.dumps(old_payload)}
        original = dict(record)
        preview = build_grade_material_preflight(export, existing_record=record)
        self.assertEqual(record, original)
        self.assertEqual(preview["students"][0]["previous_score"], 0)
        self.assertTrue({"refresh_replaces_manual_scores", "refresh_score_scale_conversion", "replace_saved_material"}
                        .issubset({item["code"] for item in preview["warnings"]}))
        record["updated_at"] = "v2"
        self.assertNotEqual(preview["source_hash"], build_grade_material_preflight(export, existing_record=record)["source_hash"])

    def test_legacy_generate_and_refresh_require_preflight_before_any_work(self):
        with patch.object(routes, "_sync_fresh_attendance_for_ordinary_generation", AsyncMock()) as sync, patch.object(
            routes, "_create_generated_final_material_package", AsyncMock()
        ) as create:
            for kind in ("ordinary_grade_record", "exam_grade_record"):
                with self.assertRaises(HTTPException) as error:
                    asyncio.run(routes.generate_classroom_final_material(30, routes.ClassroomFinalMaterialGenerateRequest(document_type=kind), {"id": 1}))
                self.assertEqual(error.exception.status_code, 409)
            with self.assertRaises(HTTPException) as error:
                asyncio.run(routes.refresh_generated_grade_record_material(1, None, {"id": 1}))
            self.assertEqual(error.exception.status_code, 409)
            sync.assert_not_called()
            create.assert_not_called()

    def test_real_preflight_and_confirmed_generation_use_same_projection_and_save_confirmation(self):
        with ExitStack() as stack:
            stack.enter_context(patch.object(routes, "get_db_connection", self.db))
            stack.enter_context(patch.object(routes, "ensure_classroom_access"))
            stack.enter_context(patch.object(routes, "_load_final_material_classroom_context", return_value={}))
            stack.enter_context(patch.object(routes, "_local_grade_record_parse_result", side_effect=lambda **kw: SimpleNamespace(export_payload=kw["export_payload"])))
            create = stack.enter_context(patch.object(routes, "_create_generated_final_material_package", AsyncMock(return_value={"id": 1})))
            body = routes.ClassroomFinalMaterialGenerateRequest(**self.selection)
            response = asyncio.run(routes.preflight_classroom_grade_material(30, body, {"id": 1}))
            create.assert_not_called()
            confirmed = routes.ClassroomFinalMaterialGenerateRequest(**self.selection, **self.confirmation(response["preflight"]))
            result = asyncio.run(routes.generate_classroom_final_material(30, confirmed, {"id": 1}))
        self.assertEqual(result["status"], "success")
        saved = create.call_args.kwargs["parse_result"].export_payload["structured"]["generation_confirmation"]
        self.assertEqual(saved["source_hash"], response["preflight"]["source_hash"])

    def test_teacher_floor_setting_is_forwarded_without_changing_weights(self):
        with patch.object(routes, "build_ordinary_grade_record_payload", return_value={}) as build:
            routes._build_local_grade_export(self.conn, selection={"document_type": "ordinary_grade_record",
                "homework_assignment_ids": [1, 2, 3], "assessment_assignment_id": 4,
                "minimum_ordinary_score_enabled": False, "minimum_ordinary_score": 72},
                class_offering_id=30, teacher_id=1, classroom_context={})
        self.assertFalse(build.call_args.kwargs["minimum_ordinary_score_enabled"])
        self.assertEqual(build.call_args.kwargs["minimum_ordinary_score"], 72)


class EffectiveScoreReviewTests(unittest.TestCase):
    def test_graded_primary_result_with_failed_review_is_not_a_clean_or_manual_source(self):
        fixture = score_fixture.ScoreProjectionTests()
        fixture.setUp()
        try:
            sid = fixture.add(1, 80, kind="final")
            activate_submission_grade_revision(fixture.conn, submission={"id": sid}, score=80, feedback_md="有效主分",
                data={"execution_plan": {"capability": "vision"}, "execution_metadata": {"profile_id": "vision_assessment_high", "reasoning_effort": "high"},
                      "review_reason_codes": ["adjudication_budget_exhausted"], "quality_audit": {"review_required": True}})
            row = fixture.conn.execute("SELECT provenance_json FROM submission_grade_revisions WHERE submission_id=?", (sid,)).fetchone()
            self.assertEqual(json.loads(row["provenance_json"])["source"], "ai")
            preview = build_grade_source_preflight(fixture.conn, assignment_ids=[1], student_ids=[1])
            self.assertEqual(preview["counts"]["review_required"], 1)
            self.assertFalse(preview["ready_for_publication"])
            snapshot = preview["source_snapshots"][0]
            self.assertEqual(snapshot["effective_score"], 80)
            self.assertEqual(snapshot["status"], "graded")
            self.assertTrue(snapshot["review_required"])
            self.assertEqual(snapshot["review_reason_codes"], ["adjudication_budget_exhausted"])
        finally:
            fixture.tearDown()

    def test_same_grade_result_does_not_discard_a_later_review_warning(self):
        fixture = score_fixture.ScoreProjectionTests()
        fixture.setUp()
        try:
            sid = fixture.add(1, 80)
            data = {"grading_revision_hash": "same-answer", "source": "ai"}
            activate_submission_grade_revision(fixture.conn, submission={"id": sid}, score=80, feedback_md="相同评语", data=data)
            activate_submission_grade_revision(fixture.conn, submission={"id": sid}, score=80, feedback_md="相同评语",
                data={**data, "review_reason_codes": ["high_review_failed"], "quality_audit": {"review_required": True}})
            preflight = build_grade_source_preflight(fixture.conn, assignment_ids=[1], student_ids=[1])
            self.assertEqual(preflight["counts"]["review_required"], 1)
            self.assertEqual(preflight["source_snapshots"][0]["effective_score"], 80)
        finally:
            fixture.tearDown()


class PublishedMaterialRetentionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = publication_fixture.GradePublicationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.conn = self.fixture.conn

    @contextmanager
    def db(self):
        yield self.conn

    def test_source_delete_preserves_self_snapshot_but_teacher_can_still_withdraw(self):
        publication = self.fixture.publish()
        self.conn.execute("DELETE FROM material_ai_import_records WHERE id=1")
        self.assertEqual(publication_fixture.student_published_grades(self.conn, student_id=101)[0]["overall_score"], 81.6)
        self.assertTrue(publication_fixture.teacher_grade_publication_status(self.conn, class_offering_id=1, teacher_id=1)["current"]["source_stale"])
        publication_fixture.withdraw_grade_publication(self.conn, class_offering_id=1, teacher_id=1,
            publication_id=publication["publication_id"], reason="撤回")
        self.assertEqual(publication_fixture.student_published_grades(self.conn, student_id=101), [])

    def test_active_publication_prevents_classroom_hard_delete_until_withdrawn(self):
        publication = self.fixture.publish()
        with patch.object(offerings, "get_db_connection", self.db):
            with self.assertRaises(HTTPException) as error:
                asyncio.run(offerings.api_delete_class_offering(1, {"id": 1}))
            self.assertEqual(error.exception.status_code, 409)
            self.assertIsNotNone(self.conn.execute("SELECT 1 FROM class_offerings WHERE id=1").fetchone())
            publication_fixture.withdraw_grade_publication(self.conn, class_offering_id=1, teacher_id=1,
                publication_id=publication["publication_id"], reason="撤回后删除")
            result = asyncio.run(offerings.api_delete_class_offering(1, {"id": 1}))
        self.assertEqual(result["status"], "success")
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM grade_publications WHERE id=?", (publication["publication_id"],)).fetchone())

    def test_retake_related_material_list_does_not_write_or_refresh_saved_scores(self):
        with patch.object(routes, "get_db_connection", self.db), patch.object(routes, "build_grade_record_refresh_plan", return_value={"class_offering_id": 1, "document_type": "ordinary_grade_record"}), patch.object(
            routes, "_execute_grade_record_refresh", AsyncMock()
        ) as refresh:
            self.conn.execute("UPDATE material_ai_import_records SET document_type='ordinary_grade_record'")
            before = self.conn.total_changes
            result = asyncio.run(routes.refresh_offering_grade_record_materials(1, {"id": 1}))
        self.assertEqual(self.conn.total_changes, before)
        self.assertEqual(result[0]["status"], "needs_confirmation")
        refresh.assert_not_called()

    def test_refresh_compare_and_swap_preserves_a_concurrently_edited_material(self):
        record = dict(self.conn.execute("SELECT * FROM material_ai_import_records WHERE id=1").fetchone())
        record["source_file_name"] = "旧材料"
        self.conn.execute("UPDATE material_ai_import_records SET updated_at='another-edit' WHERE id=1")
        parse = SimpleNamespace(document_type_label="成绩表", metadata={}, export_payload={}, warnings=[], content_quality={})
        with ExitStack() as stack:
            stack.enter_context(patch.object(helpers, "get_db_connection", self.db))
            stack.enter_context(patch.object(helpers, "build_import_readme", return_value="preview"))
            stack.enter_context(patch.object(helpers, "_write_material_file", AsyncMock()))
            stack.enter_context(patch.object(helpers, "_build_material_ai_parse_payload", return_value={}))
            stack.enter_context(patch.object(helpers, "build_material_mastery_check_payload", return_value={"status": "ready"}))
            with self.assertRaises(HTTPException) as error:
                asyncio.run(helpers._persist_final_material_record_update(1, record, parse, {"id": 1}, require_unchanged_record=True))
            self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.conn.execute("SELECT updated_at FROM material_ai_import_records WHERE id=1").fetchone()[0], "another-edit")

    def test_two_publishers_confirming_same_version_produce_one_active_snapshot(self):
        review = self.fixture.preview()
        self.conn.commit()
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "publication.sqlite3"
            with closing(sqlite3.connect(db_path)) as target:
                self.conn.backup(target)
            barrier = threading.Barrier(2)
            def publish():
                conn = sqlite3.connect(db_path, timeout=10)
                conn.row_factory = sqlite3.Row
                try:
                    barrier.wait()
                    publication_fixture.publish_grade_snapshot(conn, class_offering_id=1, teacher_id=1,
                        material_id=1, expected_source_hash=review["source_hash"], expected_version=0, confirmed=True,
                        accepted_warning_codes=[item["code"] for item in review["warnings"]], confirmation_note="已核对")
                    conn.commit()
                    return "published"
                except HTTPException as exc:
                    conn.rollback()
                    return exc.status_code
                finally:
                    conn.close()
            with ThreadPoolExecutor(max_workers=2) as workers:
                outcomes = list(workers.map(lambda _: publish(), range(2)))
            self.assertCountEqual(outcomes, ["published", 409])
            with closing(sqlite3.connect(db_path)) as check:
                self.assertEqual(check.execute("SELECT COUNT(*) FROM grade_publications WHERE status='active'").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
