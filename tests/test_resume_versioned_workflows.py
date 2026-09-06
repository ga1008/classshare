"""Student HTTP contracts, immutable facts and delayed-result safety.

All persistence uses in-memory SQLite. AI, Office and production storage are
never called; the real command and apply functions exercise business guards.
"""
import asyncio
import io
import os
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import patch

os.environ.setdefault("DB_ENGINE", "sqlite")

from fastapi import FastAPI, UploadFile, HTTPException
from fastapi.testclient import TestClient
from classroom_app.db import schema_resume
from classroom_app.dependencies import get_current_user
from classroom_app.routers import resume_console as routes
from classroom_app.services.resume import resume_document_service as docs
from classroom_app.services.resume import resume_generation_service as generation
from classroom_app.services.resume import resume_import_service as imports
from classroom_app.services.resume import resume_job_target_service as jobs
from classroom_app.services.resume import resume_application_service as applications
from classroom_app.services.resume import resume_profile_service as profile
from classroom_app.services.resume import resume_readiness_service as readiness
from classroom_app.services.resume import resume_render_service as render


class ResumeWorkflowTests(unittest.TestCase):
    def setUp(self):
        schema_resume._SCHEMA_READY = False
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        schema_resume.ensure_resume_schema(self.conn)
        profile.update_personal_info(self.conn, 1, {"name": "学生甲", "email": "a@example.com", "expected_position": "英语教师"})
        self.exp_id = profile.create_section_item(self.conn, 1, "experience", {
            "kind": "internship", "title": "教学实习", "start_date": "2025-01", "end_date": "2025-06",
            "content": "设计英语课堂活动", "contribution": "编写教案", "achievement": "完成三次公开课", "role": "实习教师",
        })
        self.conn.commit()
        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[get_current_user] = lambda: {"id": 1, "role": "student"}
        self.client = TestClient(app)
        self.database = patch.object(routes, "get_db_connection", self.connection)
        self.database.start()

    def tearDown(self):
        self.database.stop()
        self.client.close()
        self.conn.close()
        schema_resume._SCHEMA_READY = False

    @contextmanager
    def connection(self):
        try:
            yield self.conn
        except BaseException:
            self.conn.rollback()
            raise

    def create(self, **kwargs):
        return docs.create_resume(self.conn, 1, title="教学岗位简历", target_position="英语教师", template_key="classic",
            layout={"blocks": [{"type": "experience", "ids": [self.exp_id]}]}, draft=True, **kwargs)

    def test_real_put_contract_and_conflict_preserve_input(self):
        rid = self.create()
        self.conn.commit()
        payload = {"title": "修改后的简历", "draft": True, "revision": 1, "source_context": {"source": "builder"}}
        with patch.object(routes, "supersede_student_career_jobs", return_value=0):
            result = self.client.put(f"/api/resume/resumes/{rid}", json=payload)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()["revision"], 2)
            stale = self.client.put(f"/api/resume/resumes/{rid}", json={**payload, "title": "旧窗口覆盖"})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(docs.get_resume(self.conn, 1, rid)["title"], "修改后的简历")
        missing = self.client.put(f"/api/resume/resumes/{rid}", json={"draft": True})
        self.assertEqual(missing.status_code, 428)

    def test_create_idempotency_and_incomplete_draft(self):
        payload = {"client_id": "stable-local-draft", "title": "空草稿", "draft": True, "layout": {"blocks": []}}
        first = self.client.post("/api/resume/resumes", json=payload)
        second = self.client.post("/api/resume/resumes", json=payload)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM resumes").fetchone()[0], 1)
        publish = self.client.post(f"/api/resume/resumes/{first.json()['id']}/publish", json={"revision": 1})
        self.assertEqual(publish.status_code, 400)

    def test_snapshot_survives_source_update_and_delete(self):
        rid = self.create(content_overrides=[{"section": "experience", "id": self.exp_id, "fields": {"achievement": "本份简历的具体表达"}}])
        old = docs.get_version(self.conn, 1, rid, 1)
        original = profile.get_section_item(self.conn, 1, "experience", self.exp_id)
        self.assertEqual(original["achievement"], "完成三次公开课")
        profile.delete_section_item(self.conn, 1, "experience", self.exp_id)
        docs.update_resume(self.conn, 1, rid, title="改标题", target_position="英语教师", template_key="classic",
                           layout=old["snapshot"]["layout"], expected_revision=1, draft=True)
        new = docs.get_version(self.conn, 1, rid, 2)
        self.assertEqual(new["snapshot"]["bundle"]["experience"][0]["achievement"], "本份简历的具体表达")
        self.assertTrue(readiness.validate_frozen_resume(new["snapshot"])["ok"])
        self.assertEqual(docs.get_version(self.conn, 1, rid, 1)["content_hash"], old["content_hash"])

    def test_late_ai_result_cannot_overwrite_new_revision(self):
        rid = self.create()
        self.conn.execute("UPDATE resumes SET active_job_id='23',status='optimizing' WHERE id=?", (rid,))
        docs.update_resume(self.conn, 1, rid, title="最新编辑", target_position="翻译", template_key="classic",
                           layout={"blocks": [{"type": "experience", "ids": [self.exp_id]}]}, expected_revision=1, draft=True)
        applied = generation.apply_resume_candidate(self.conn, {"id": 23, "task_type": "resume_optimize"},
            {"student_id": 1, "resume_id": rid, "revision": 1}, {"summary_md": "旧结果"})
        self.assertFalse(applied)
        self.assertEqual(docs.get_resume(self.conn, 1, rid)["title"], "最新编辑")
        self.assertEqual(docs.list_candidates(self.conn, 1, rid), [])

    def test_optimization_is_suggestion_until_accepted(self):
        rid = self.create()
        self.conn.execute("UPDATE resumes SET active_job_id='24',status='optimizing' WHERE id=?", (rid,))
        payload = {"student_id": 1, "resume_id": rid, "revision": 1}
        generation.apply_resume_candidate(self.conn, {"id": 24, "task_type": "resume_optimize"}, payload,
            {"summary_md": "英语教学实习与课程设计经验。", "tech_stack": [], "notes": ["突出教学实践"]})
        self.assertEqual(docs.get_resume(self.conn, 1, rid)["optimized_summary_md"], "")
        candidate = docs.list_candidates(self.conn, 1, rid)[0]
        revision = docs.accept_optimization(self.conn, 1, rid, candidate["id"], 1)
        self.assertEqual(revision, 2)
        self.assertIn("英语教学", docs.get_version(self.conn, 1, rid, 2)["snapshot"]["optimized_summary_md"])
        with self.assertRaises(docs.ResumeConflict):
            docs.accept_optimization(self.conn, 1, rid, candidate["id"], 2)

    def test_preview_never_substitutes_old_version_for_requested_new(self):
        rid = self.create()
        docs.save_version_render(self.conn, 1, rid, 1, "<html>version-one</html>")
        docs.update_resume(self.conn, 1, rid, title="新版", target_position="教师", template_key="classic", layout={}, expected_revision=1, draft=True)
        self.conn.commit()
        explicit = self.client.get(f"/api/resume/resumes/{rid}/preview?revision=2")
        self.assertEqual(explicit.status_code, 409)
        old = self.client.get(f"/api/resume/resumes/{rid}/preview?revision=1")
        self.assertEqual(old.status_code, 200)
        self.assertEqual(old.headers["x-resume-revision"], "1")
        self.assertIn("version-one", old.text)

    def test_import_candidate_has_no_profile_side_effect_before_accept(self):
        rid = self.create()
        before = profile.collect_profile_bundle(self.conn, 1)
        parsed = imports.normalize_resume_import_payload({"personal": {"phone": "13900000000"},
            "education": [{"school": "新增学校", "start_date": "2022-09", "end_date": "2026-06"}],
            "skill": [{"name": "法语", "acquired_date": "2024-01"}]})
        cid = docs.create_candidate(self.conn, 1, rid, 1, "import", {"parsed": parsed}, job_id="51")
        self.assertEqual(profile.collect_profile_bundle(self.conn, 1), before)
        revision = imports.accept_import_candidate(self.conn, 1, rid, cid, 1, selections={"selected_sections": ["skill"]})
        self.assertEqual(revision, 2)
        self.assertEqual(len(profile.list_section(self.conn, 1, "education")), 0)
        self.assertEqual(profile.get_personal_info(self.conn, 1)["phone"], "")
        self.assertEqual(profile.list_section(self.conn, 1, "skill")[0]["name"], "法语")
        with self.assertRaises(docs.ResumeConflict):
            imports.accept_import_candidate(self.conn, 1, rid, cid, revision)

    def test_import_conflict_does_not_overwrite_newer_field(self):
        rid = self.create()
        docs.save_import_summary(self.conn, rid, {"conflicts": [{"section": "personal", "field": "email", "existing": "a@example.com", "incoming": "import@example.com"}]})
        personal = profile.get_personal_info(self.conn, 1)
        profile.update_personal_info(self.conn, 1, {**personal, "email": "manual@example.com"})
        with self.assertRaises(docs.ResumeConflict):
            imports.accept_import_conflict(self.conn, 1, rid, 0)
        self.assertEqual(profile.get_personal_info(self.conn, 1)["email"], "manual@example.com")

    def test_application_history_survives_archiving_and_editing(self):
        rid = self.create()
        target = jobs.create_job_target(self.conn, 1, target_position="英语教师", company_name="学校", job_description="岗位要求：具备英语教学设计能力，负责课堂组织与学生学习活动设计。")
        record = applications.create_application(self.conn, 1, {"job_target_id": target["id"], "resume_id": rid, "status": "preparing"})
        jobs.delete_job_target(self.conn, 1, target["id"])
        docs.delete_resume(self.conn, 1, rid)
        updated = applications.update_application(self.conn, 1, record["id"], {"status": "applied", "revision": record["revision"]})
        self.assertEqual(updated["status"], "applied")
        self.assertEqual(updated["resume_snapshot"]["title"], "教学岗位简历")
        self.assertEqual(updated["job_snapshot"]["job_description"], target["job_description"])

    def test_job_target_deduplication_and_archive_retains_source(self):
        body = dict(target_position="教师", company_name="学校", job_description="岗位要求：负责英语教学，具备教学设计和课堂组织能力，能开展教学活动。")
        first = jobs.create_job_target(self.conn, 1, **body)
        for index in range(31):
            jobs.create_job_target(self.conn, 1, **{**body, "company_name": f"学校{index}"})
        self.assertTrue(jobs.get_job_target(self.conn, 1, first["id"])["archived"])
        repeated = jobs.create_job_target(self.conn, 1, **body)
        self.assertEqual(repeated["id"], first["id"])
        self.assertFalse(repeated["archived"])

    def test_intent_negation_and_expired_certificate_are_not_evidence(self):
        description = "任职要求：熟悉 Python 开发，持有教师资格证，可以独立组织相关教学实践活动。"
        result = jobs.analyze_job_description({"personal": {"expected_position": "Python教师"},
            "skill": [{"name": "Python", "description": "不熟悉 Python，计划学习"}],
            "certificate": [{"name": "教师资格证", "expiry_date": "2020-01"}]}, description)
        self.assertEqual(result["coverage_score"], 0)
        self.assertTrue(all(not capability["matched"] for capability in result["capabilities"]))
        self.assertTrue(result["hard_requirements"])

    def test_exact_jd_is_frozen_and_passed_to_optimization(self):
        target = jobs.create_job_target(self.conn, 1, target_position="教师", job_description="岗位要求：负责法语教学设计和法语口语训练，具备课程组织与评估能力。")
        rid = self.create(source_context={"source": "job_analysis", "job_id": target["id"]})
        version = docs.get_version(self.conn, 1, rid)
        self.assertEqual(version["snapshot"]["job_target"]["job_description"], target["job_description"])
        from classroom_app.services.resume import resume_ai_service as ai
        captured = []
        async def chat(system, message, **kwargs):
            captured.append(message)
            return {"summary_md": "具备法语教学背景与教学实践。", "tech_stack": [], "notes": []}
        with patch.object(ai, "_chat", side_effect=chat):
            asyncio.run(ai.optimize_resume_for_target(docs.snapshot_resume(version), version["snapshot"]["bundle"], {}))
        self.assertIn("法语口语训练", captured[0])

    def test_eight_experience_types_render_and_escape_once(self):
        rid = self.create()
        snapshot = docs.get_version(self.conn, 1, rid)["snapshot"]
        for kind, label in render._EXPERIENCE_KIND_LABEL.items():
            snapshot["bundle"]["experience"][0].update(kind=kind, title="A&B")
            html = render.assemble_resume_html(None, 1, {**snapshot, "content_snapshot": snapshot["bundle"]})
            self.assertIn(label, html)
            self.assertIn("A&amp;B", html)
            self.assertNotIn("A&amp;amp;B", html)

    def test_upload_limit_precedes_storage(self):
        upload = UploadFile(file=io.BytesIO(b"%PDF" + b"x" * 64), filename="resume.pdf")
        with self.assertRaises(HTTPException) as error:
            asyncio.run(imports.validate_upload_stream(upload, max_bytes=32))
        self.assertEqual(error.exception.status_code, 413)
        self.assertEqual(upload.file.tell(), 0)
        invalid = UploadFile(file=io.BytesIO(b"not-a-pdf"), filename="resume.pdf")
        with self.assertRaises(HTTPException) as error:
            asyncio.run(imports.validate_upload_stream(invalid))
        self.assertEqual(error.exception.status_code, 415)

    def test_export_cache_avoids_second_conversion(self):
        with tempfile.TemporaryDirectory(prefix="resume-test-") as root:
            with patch.dict(os.environ, {"RESUME_EXPORT_CACHE_DIR": root}), patch.object(render, "export_resume_bytes", return_value=b"%PDF-test") as convert:
                first = render.export_resume_cached("<html>same</html>", "pdf")
                second = render.export_resume_cached("<html>same</html>", "pdf")
                self.assertEqual(first, second)
                self.assertEqual(convert.call_count, 1)

    def test_export_shared_office_capacity_returns_retryable_http_response(self):
        from classroom_app.services.libreoffice_service import LibreOfficeBusy
        rid = self.create()
        docs.save_version_render(self.conn, 1, rid, 1, "<p>Frozen version</p>")
        self.conn.commit()
        with patch.object(render, "export_resume_cached", side_effect=LibreOfficeBusy()):
            response = self.client.get(f"/api/resume/resumes/{rid}/export?fmt=docx&revision=1")
        self.assertEqual(429, response.status_code)
        self.assertEqual("10", response.headers["Retry-After"])
        self.assertNotIn("content-disposition", response.headers)
        self.assertIn("请稍后", response.json()["detail"])

    def test_save_preserves_displayed_facts_and_personal_overrides(self):
        rid = self.create()
        old = docs.get_version(self.conn, 1, rid)["snapshot"]
        material = profile.get_section_item(self.conn, 1, "experience", self.exp_id)
        profile.update_section_item(self.conn, 1, "experience", self.exp_id, {**material, "content": "素材库后来修改"})
        personal = profile.get_personal_info(self.conn, 1)
        profile.update_personal_info(self.conn, 1, {**personal, "name": "资料库新名字"})
        docs.update_resume(self.conn, 1, rid, title="仅改名称", target_position="教师", template_key="classic", layout=old["layout"], expected_revision=1, draft=True,
            content_overrides=[{"section": "personal", "id": 0, "fields": {"phone": "13900000001", "student_id": 999}},
                               {"section": "experience", "id": self.exp_id, "fields": {"end_date": "2025-07"}}])
        saved = docs.get_version(self.conn, 1, rid)["snapshot"]["bundle"]
        self.assertEqual(saved["personal"]["name"], "学生甲")
        self.assertEqual(saved["personal"]["student_id"], 1)
        self.assertEqual(saved["personal"]["phone"], "13900000001")
        self.assertEqual(saved["experience"][0]["content"], "设计英语课堂活动")
        self.assertEqual(saved["experience"][0]["end_date"], "2025-07")
        self.assertEqual(profile.get_personal_info(self.conn, 1)["phone"], "")

    def test_personal_and_material_http_require_revision(self):
        personal = profile.get_personal_info(self.conn, 1)
        self.assertEqual(self.client.post("/api/resume/personal", json={"name": "不覆盖"}).status_code, 428)
        self.assertEqual(self.client.post("/api/resume/personal", json={**personal, "name": "新名字"}).status_code, 200)
        self.assertEqual(self.client.post("/api/resume/personal", json=personal).status_code, 409)
        material = profile.get_section_item(self.conn, 1, "experience", self.exp_id)
        path = f"/api/resume/sections/experience/{self.exp_id}"
        self.assertEqual(self.client.put(path, json={"title": "不覆盖"}).status_code, 428)
        self.assertEqual(self.client.put(path, json=material).status_code, 200)
        self.assertEqual(self.client.put(path, json=material).status_code, 409)

    def test_intro_generation_reuses_same_evidence_and_rejects_changed_evidence(self):
        from classroom_app.db.schema_ai_jobs import ensure_ai_job_schema, reset_ai_job_schema_guard_for_tests
        from classroom_app.services.ai_durable_job_service import load_ai_job_payload
        self.conn.execute("CREATE TABLE submissions (id INTEGER PRIMARY KEY)")
        reset_ai_job_schema_guard_for_tests()
        ensure_ai_job_schema(self.conn, engine="sqlite")
        try:
            with patch.object(generation, "_student_context", return_value={}):
                first, job = generation.begin_intro_job(self.conn, 1)
                repeated, same = generation.begin_intro_job(self.conn, 1)
            self.assertEqual((first, job["id"]), (repeated, same["id"]))
            raw = dict(self.conn.execute("SELECT * FROM ai_jobs WHERE id=?", (job["id"],)).fetchone())
            payload = load_ai_job_payload(raw)
            profile.create_section_item(self.conn, 1, "skill", {"name": "新增口译技能", "acquired_date": "2024-01"})
            self.assertFalse(generation.apply_intro(self.conn, raw, payload, {"content_md": "过期摘要"}))
            self.assertNotIn("过期摘要", profile.get_section_item(self.conn, 1, "self_intro", first)["content_md"])
        finally:
            reset_ai_job_schema_guard_for_tests()

    def test_hard_requirements_keep_unknowns_and_do_not_double_count_work(self):
        from datetime import date
        from classroom_app.services.resume.resume_requirement_service import evaluate_hard_requirements
        bundle = {"education": [{"id": 1, "school": "某大学", "degree": "大专", "end_date": "2025-06"}],
                  "certificate": [{"id": 2, "name": "小学教师资格证", "acquired_date": "2024-01"}],
                  "experience": [{"id": 3, "kind": "employment", "start_date": "2024-01", "end_date": "2025-01"},
                                 {"id": 4, "kind": "employment", "start_date": "2024-06", "end_date": "2025-06"},
                                 {"id": 5, "kind": "internship", "start_date": "2020-01", "end_date": "2024-01"}]}
        checks = evaluate_hard_requirements(bundle, "本科以上学历，具备高中教师资格证；2年以上工作经验；工作地点上海", today=date(2026, 9, 6))
        by_type = {item["type"]: item for item in checks}
        self.assertEqual(by_type["education"]["state"], "failed")
        self.assertEqual(by_type["qualification"]["state"], "unknown")
        self.assertEqual(by_type["experience"]["state"], "unknown")
        self.assertEqual(by_type["location"]["state"], "unknown")
        bundle["education"][0].update(degree="本科", end_date="2027-06")
        self.assertEqual(evaluate_hard_requirements(bundle, "本科以上学历", today=date(2026, 9, 6))[0]["state"], "unknown")
        bundle["education"][0].update(end_date="2025-06")
        self.assertEqual(evaluate_hard_requirements(bundle, "本科以上学历", today=date(2026, 9, 6))[0]["state"], "met")

    def test_readiness_counts_beyond_section_page_limit(self):
        self.conn.executemany("INSERT INTO resume_skills(student_id,name,acquired_date) VALUES (1,?,'2024-01')", [(f"技能{n}",) for n in range(205)])
        result = readiness.build_resume_readiness(self.conn, 1)
        self.assertEqual(result["counts"]["sections"]["skill"], 205)

    def test_partial_import_accepts_into_draft_with_actionable_validation(self):
        rid = self.create()
        parsed = imports.normalize_resume_import_payload({"skill": [{"name": "口译"}]})
        cid = docs.create_candidate(self.conn, 1, rid, 1, "import", {"parsed": parsed}, job_id="partial-import")
        self.conn.commit()
        result = self.client.post(f"/api/resume/resumes/{rid}/candidates/{cid}/accept", json={"revision": 1, "selected_sections": ["skill"]})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertFalse(result.json()["validation"]["ok"])
        self.assertEqual(result.json()["job"], {})
        self.assertEqual(docs.get_resume(self.conn, 1, rid)["status"], "draft")
        self.assertEqual(profile.list_section(self.conn, 1, "skill")[0]["name"], "口译")

    def test_short_suggestions_share_durable_queue_and_profile_guard(self):
        from classroom_app.db.schema_ai_jobs import ensure_ai_job_schema, reset_ai_job_schema_guard_for_tests
        from classroom_app.services.ai_durable_job_service import load_ai_job_payload
        from classroom_app.services.resume import resume_suggestion_service as suggestions
        self.conn.execute("CREATE TABLE submissions (id INTEGER PRIMARY KEY)")
        reset_ai_job_schema_guard_for_tests()
        ensure_ai_job_schema(self.conn, engine="sqlite")
        try:
            with patch.object(generation, "_student_context", return_value={}):
                first = self.client.post("/api/resume/personal/suggest")
                same = self.client.post("/api/resume/personal/suggest")
                self.assertEqual(first.status_code, 202, first.text)
                jid = first.json()["job"]["id"]
                self.assertEqual(jid, same.json()["job"]["id"])
                self.assertEqual(self.client.post(f"/api/resume/suggestions/jobs/{jid}/cancel").status_code, 202)
                new = self.client.post(f"/api/resume/suggestions/jobs/{jid}/retry")
                next_id = new.json()["job"]["id"]
                self.assertNotEqual(jid, next_id)
                self.assertEqual(self.client.post("/api/resume/personal/suggest").json()["job"]["id"], next_id)
            with self.assertRaises(LookupError):
                suggestions.suggestion_state(self.conn, 2, next_id)
            raw = dict(self.conn.execute("SELECT * FROM ai_jobs WHERE id=?", (next_id,)).fetchone())
            payload = load_ai_job_payload(raw)
            personal = profile.get_personal_info(self.conn, 1)
            profile.update_personal_info(self.conn, 1, {**personal, "expected_position": "翻译"})
            self.assertFalse(suggestions.apply_suggestion(self.conn, raw, payload, {"ok": True, "suggestions": {}}))
            self.assertTrue(suggestions.suggestion_state(self.conn, 1, next_id)["stale"])
            self.assertNotIn("result", suggestions.suggestion_state(self.conn, 1, jid))
        finally:
            reset_ai_job_schema_guard_for_tests()

    def test_compact_list_omits_document_contents(self):
        self.create()
        self.conn.commit()
        response = self.client.get("/api/resume/resumes?compact=true")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()["items"][0]), {"id", "status", "revision", "render_revision", "active_job_id", "error_text", "updated_at"})

    def test_accepted_summary_survives_save_and_can_be_edited_explicitly(self):
        pending = patch.object(routes, "supersede_student_career_jobs", return_value=0)
        pending.start()
        self.addCleanup(pending.stop)
        rid = self.create()
        cid = docs.create_candidate(self.conn, 1, rid, 1, "optimization", {"summary_md": "已采用的摘要", "tech_stack": [{"group": "专业能力", "items": ["教学"]}]}, job_id="editable-summary")
        docs.accept_optimization(self.conn, 1, rid, cid, 1)
        self.conn.commit()
        saved = self.client.put(f"/api/resume/resumes/{rid}", json={"revision": 2, "title": "只改标题", "draft": True})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(docs.get_resume(self.conn, 1, rid)["optimized_summary_md"], "已采用的摘要")
        edited = self.client.put(f"/api/resume/resumes/{rid}", json={"revision": 3, "draft": True, "optimized_summary_md": "手工核对后的摘要", "tech_stack": []})
        self.assertEqual(edited.status_code, 200, edited.text)
        current = docs.get_version(self.conn, 1, rid)["snapshot"]
        self.assertEqual(current["optimized_summary_md"], "手工核对后的摘要")
        self.assertEqual(current["tech_stack"], [])
        self.assertEqual(docs.get_version(self.conn, 1, rid, 2)["snapshot"]["optimized_summary_md"], "已采用的摘要")

    def test_import_resource_budgets_precede_parser(self):
        from pathlib import Path
        import zipfile
        import fitz
        with tempfile.TemporaryDirectory(prefix="resume-resource-test-") as directory:
            archive_path = Path(directory) / "many-parts.docx"
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for index in range(2001):
                    archive.writestr(f"part{index}.xml", b"x")
            with patch.object(imports, "extract_material_content") as parse:
                with self.assertRaises(imports.ResumeImportResourceLimit):
                    imports._extract_resume_file(archive_path, archive_path.name)
                parse.assert_not_called()
            pdf_path = Path(directory) / "too-many-pages.pdf"
            with fitz.open() as document:
                for _ in range(41):
                    document.new_page()
                document.save(pdf_path)
            with self.assertRaises(imports.ResumeImportResourceLimit):
                imports._check_input_resource_budget(pdf_path, pdf_path.name)

    def test_invalid_dates_do_not_pass_publish_or_material_save(self):
        material = profile.get_section_item(self.conn, 1, "experience", self.exp_id)
        with self.assertRaises(ValueError):
            profile.update_section_item(self.conn, 1, "experience", self.exp_id, {**material, "end_date": "2025-13"})
        rid = self.create(content_overrides=[{"section": "experience", "id": self.exp_id, "fields": {"title": "", "end_date": "2025-13"}}])
        self.assertFalse(readiness.validate_frozen_resume(docs.get_version(self.conn, 1, rid)["snapshot"])["ok"])

    def test_avatar_revision_can_advance_form_without_overwriting_a_stale_window(self):
        from unittest.mock import AsyncMock
        current = profile.get_personal_info(self.conn, 1)["revision"]
        from PIL import Image
        pixels = io.BytesIO()
        Image.new("RGB", (1, 1), "white").save(pixels, format="PNG")
        upload = {"file": ("avatar.png", pixels.getvalue(), "image/png") }
        stored = {"hash": "a" * 64, "size": 17}
        with patch.object(routes, "save_file_globally", new=AsyncMock(return_value=stored)), patch("classroom_app.services.file_service.lock_global_file_references"):
            response = self.client.post("/api/resume/personal/avatar", data={"revision": str(current)}, files=upload)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["revision"], current + 1)
            stale = self.client.post("/api/resume/personal/avatar", data={"revision": str(current)}, files=upload)
            self.assertEqual(stale.status_code, 409)
            self.assertEqual(self.client.post("/api/resume/personal/avatar", files=upload).status_code, 428)


if __name__ == "__main__":
    unittest.main()
