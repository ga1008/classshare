"""AI-only rollout, using synthetic in-memory SQLite and no worker/provider."""
import asyncio
import copy
import json
import os
import unittest
from contextlib import contextmanager
from unittest.mock import patch

os.environ["DB_ENGINE"] = "sqlite"

from fastapi import FastAPI
from fastapi.testclient import TestClient
from classroom_app import config
from classroom_app.dependencies import get_current_user
from classroom_app.routers import career_path as career_routes, resume_console as resume_routes
from classroom_app.services import career_path_service as career, career_lifecycle_service as lifecycle
from classroom_app.services import career_rollout_service as rollout, student_career_job_service as jobs
from classroom_app.services.resume import resume_document_service as documents, resume_profile_service as profile
from classroom_app.services.resume import resume_generation_service as generation, resume_suggestion_service as suggestions
from tests.test_career_lifecycle import fixture, answers_for


class RolloutPolicyTests(unittest.TestCase):
    def test_default_all_and_bounded_allowlist(self):
        self.assertTrue(rollout.parse_policy("all", "", "[]").allows(None))
        policy = rollout.parse_policy("allowlist", "1, 2", '[{"school_code":"audit","major_key":"英语"}]')
        self.assertTrue(policy.valid)
        self.assertTrue(policy.allows({"student_id": 1}))
        self.assertTrue(policy.allows({"school_code": "audit", "major_key": "英语"}, system=True))
        self.assertFalse(policy.allows({"school_code": "other", "major_key": "英语"}, system=True))
        self.assertFalse(policy.allows({"student_id": 1}, system=True))

    def test_invalid_mode_or_allowlist_never_falls_back_to_all(self):
        for mode, students, majors in [
            ("al", "", "[]"), ("allowlist", "1,", "[]"), ("allowlist", "0", "[]"),
            ("allowlist", "True", "[]"), ("allowlist", "9223372036854775808", "[]"),
            ("allowlist", ",".join(map(str, range(1, 502))), "[]"),
            ("allowlist", "1", "{"), ("allowlist", "1", "{}"),
            ("allowlist", "1", '[{"school_code":"audit","major_key":"unknown"}]'),
            ("allowlist", "1", '[{"school_code":"audit","major_key":"英语","extra":true}]'),
            ("allowlist", "1", '[{"school_code":"audit","school_code":"other","major_key":"英语"}]'),
            ("allowlist", "1", "["*1500 + "]"*1500),
            ("allowlist", "1", " " * 32769),
        ]:
            with self.subTest(mode=mode, students=students[:20], majors=majors[:40]):
                policy = rollout.parse_policy(mode, students, majors)
                self.assertFalse(policy.valid)
                self.assertFalse(policy.allows({"student_id": 1, "school_code": "audit", "major_key": "英语"}))
        self.assertFalse(rollout.parse_policy("allowlist", "", "[]").allows({"student_id": 1}))


class RolloutWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.conn = fixture()
        self.ctx = career.resolve_student_context(self.conn, 1)
        self.patches = [patch.multiple(config, CAREER_AI_ROLLOUT_MODE="allowlist", CAREER_AI_ROLLOUT_STUDENT_IDS="", CAREER_AI_ROLLOUT_MAJORS="[]"),
                        patch.object(jobs, "CAREER_JOBS_ENABLED", True),
                        patch.object(career_routes, "get_db_connection", self.connection),
                        patch.object(resume_routes, "get_db_connection", self.connection),
                        patch.object(generation, "get_db_connection", self.connection)]
        for item in self.patches:
            item.start()
        app = FastAPI()
        app.include_router(career_routes.router); app.include_router(resume_routes.router)
        app.dependency_overrides[get_current_user] = lambda: {"id": 1, "role": "student"}
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        for item in reversed(self.patches):
            item.stop()
        self.conn.close()
        lifecycle._cached_recommend.cache_clear(); lifecycle._validated_graph.cache_clear()

    @contextmanager
    def connection(self):
        try:
            yield self.conn
        except BaseException:
            self.conn.rollback()
            raise

    def count_jobs(self):
        return self.conn.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0]

    def network_row(self, school="audit", major="英语"):
        self.conn.execute("INSERT INTO career_major_networks(school_code,major_key,major_name) VALUES(?,?,?) ON CONFLICT(school_code,major_key) DO NOTHING", (school, major, major))
        return dict(self.conn.execute("SELECT * FROM career_major_networks WHERE school_code=? AND major_key=?", (school, major)).fetchone())

    def enqueue_network(self, row, requester=None, **extra):
        payload = {"network_id": row["id"], "school_code": row["school_code"], "major_key": row["major_key"], **extra}
        return jobs.enqueue_student_career_job(self.conn, task_type="career_major_network_generate", dedupe_key=f"probe-network:{row['id']}",
            payload=payload, requester_student_id=requester)

    def test_excluded_student_keeps_cold_baseline_quiz_and_light_state_without_shared_writes(self):
        state = career.initialize_career(self.conn, 1)
        self.assertTrue(state["network"]["nodes"])
        self.assertFalse(state["ai_availability"]["allowed"])
        self.assertEqual(state["tasks"]["network"]["status"], "rollout_limited")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM career_major_networks").fetchone()[0], 0)
        saved = career.save_test_and_generate(self.conn, self.ctx, answers_for(self.ctx), revision=state["revision"], enhance=True)["state"]
        self.assertEqual(saved["session_status"], "ready")
        self.assertTrue(saved["rankings"])
        light = career.build_state(self.conn, 1, known_result_version=saved["result_version"])
        self.assertTrue(light["network_unchanged"])
        self.assertFalse(light["tasks"]["personalization"]["can_retry"])
        self.assertEqual(self.count_jobs(), 0)

    def test_student_allowlist_network_has_system_owner_and_trusted_requester(self):
        with patch.object(config, "CAREER_AI_ROLLOUT_STUDENT_IDS", "1"):
            first = career.initialize_career(self.conn, 1)
            row = dict(self.conn.execute("SELECT * FROM ai_jobs").fetchone())
            self.assertEqual(row["owner_role"], "system")
            self.assertIsNone(row["owner_user_pk"])
            self.assertEqual(json.loads(row["payload_json"])["requested_by_student_id"], 1)
            before = dict(self.conn.execute("SELECT * FROM career_major_networks").fetchone())
            excluded = career.initialize_career(self.conn, 2)
            self.assertFalse(excluded["ai_availability"]["allowed"])
            self.assertFalse(excluded["tasks"]["network"]["can_retry"])
            self.assertEqual(self.count_jobs(), 1)
            self.assertEqual(before, dict(self.conn.execute("SELECT * FROM career_major_networks").fetchone()))
            self.assertEqual(first["tasks"]["network"]["id"], excluded["tasks"]["network"]["id"])

    def test_system_owner_and_payload_requester_do_not_bypass_student_allowlist(self):
        row = self.network_row()
        with patch.object(config, "CAREER_AI_ROLLOUT_STUDENT_IDS", "1"):
            with self.assertRaises(rollout.CareerRolloutLimited):
                self.enqueue_network(row, requested_by_student_id=1)
            with self.assertRaises(rollout.CareerRolloutLimited):
                self.enqueue_network(row, requester=2)
            self.enqueue_network(row, requester=1)
        self.assertEqual(self.count_jobs(), 1)

    def test_shared_network_rejects_forged_school_major_and_foreign_requester(self):
        row = self.network_row(major="护理学")
        with patch.object(config, "CAREER_AI_ROLLOUT_STUDENT_IDS", "1"):
            with self.assertRaises(rollout.CareerRolloutLimited):
                self.enqueue_network(row, requester=1)
            with self.assertRaises(rollout.CareerRolloutLimited):
                self.enqueue_network(row, requester=1, major_key="英语")
        self.assertEqual(self.count_jobs(), 0)

    def test_school_scoped_canonical_alias_allowlist(self):
        from classroom_app.services.career_major_mapping_service import set_career_major_alias
        set_career_major_alias(self.conn, school_code="audit", alias_name="英语教育", canonical_name="英语", reason="test")
        self.conn.execute("UPDATE classes SET major='英语教育' WHERE id=1")
        self.conn.execute("UPDATE students SET school_code='other' WHERE id=2")
        scopes = '[{"school_code":"audit","major_key":"英语"}]'
        with patch.object(config, "CAREER_AI_ROLLOUT_MAJORS", scopes):
            self.assertTrue(rollout.ai_availability(self.conn, 1)["allowed"])
            self.assertFalse(rollout.ai_availability(self.conn, 2)["allowed"])
            career.initialize_career(self.conn, 1)
        self.assertEqual(self.count_jobs(), 1)

    def test_maintenance_filters_scopes_before_limit_and_does_not_mutate_excluded_network(self):
        blocked = self.network_row(major="护理学")
        allowed = self.network_row()
        self.conn.execute("UPDATE career_major_networks SET status='generating'")
        before = dict(self.conn.execute("SELECT * FROM career_major_networks WHERE id=?", (blocked["id"],)).fetchone())
        with patch.object(config, "CAREER_AI_ROLLOUT_MAJORS", '[{"school_code":"audit","major_key":"英语"}]'):
            career.recover_career_jobs(self.conn, limit=1)
        self.assertEqual(self.count_jobs(), 1)
        row = self.conn.execute("SELECT payload_json FROM ai_jobs").fetchone()
        self.assertEqual(json.loads(row[0])["network_id"], allowed["id"])
        self.assertEqual(before, dict(self.conn.execute("SELECT * FROM career_major_networks WHERE id=?", (blocked["id"],)).fetchone()))

    def test_student_only_maintenance_stays_closed_and_orphan_does_not_poll_forever(self):
        row = self.network_row()
        self.conn.execute("UPDATE career_major_networks SET status='queued',job_id=999")
        with patch.object(config, "CAREER_AI_ROLLOUT_STUDENT_IDS", "2"):
            career.recover_career_jobs(self.conn)
            state = career.build_state(self.conn, 1)
        self.assertEqual(self.count_jobs(), 0)
        self.assertEqual(state["network_status"], "rollout_limited")
        self.assertEqual(state["poll_after_ms"], 0)
        self.assertEqual(self.conn.execute("SELECT status FROM career_major_networks WHERE id=?", (row["id"],)).fetchone()[0], "queued")

    def test_http_rejection_is_structured_nonretryable_and_does_not_change_quiz(self):
        state = self.client.post("/api/career-path/initialize").json()
        response = self.client.post("/api/career-path/retry", json={"target": "network", "revision": state["revision"]})
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["detail"]["code"], "rollout_limited")
        self.assertFalse(response.json()["detail"]["retryable"])
        self.assertNotIn("retry-after", response.headers)
        for path, body in (("/api/resume/personal/suggest", None), ("/api/resume/self-intro/optimize", {"text": "保留原文"})):
            denied = self.client.post(path, json=body)
            self.assertEqual(denied.status_code, 403, denied.text)
            self.assertEqual(denied.json()["detail"]["code"], "rollout_limited")
        self.assertEqual(career.build_state(self.conn, 1)["revision"], state["revision"])
        self.assertEqual(self.count_jobs(), 0)

    def test_all_ai_handlers_are_gated_but_manual_render_is_exempt_and_emergency_switch_still_wins(self):
        for task_type, handler in jobs.registered_student_career_handlers().items():
            if handler.lane != "ai" or task_type == "career_major_network_generate":
                continue
            with self.subTest(task_type=task_type), self.assertRaises(rollout.CareerRolloutLimited):
                jobs.enqueue_student_career_job(self.conn, task_type=task_type, dedupe_key="blocked:"+task_type, student_id=1, payload={})
        rendered = jobs.enqueue_student_career_job(self.conn, task_type="resume_render", dedupe_key="manual:1", student_id=1, payload={})
        self.assertEqual(rendered["task_type"], "resume_render")
        with patch.object(jobs, "CAREER_JOBS_ENABLED", False), self.assertRaises(jobs.CareerJobCapacityError):
            jobs.enqueue_student_career_job(self.conn, task_type="resume_render", dedupe_key="manual:2", student_id=1, payload={})

    def test_manual_draft_publish_and_html_render_remain_available(self):
        profile.update_personal_info(self.conn, 1, {"name": "合成学生", "email": "student@example.invalid", "expected_position": "英语教师"})
        exp_id = profile.create_section_item(self.conn, 1, "experience", {"kind": "internship", "title": "教学实习", "start_date": "2025-01", "end_date": "2025-06", "content": "整理教学资料"})
        rid = documents.create_resume(self.conn, 1, title="手工草稿", target_position="英语教师", template_key="classic", layout={"blocks": [{"type": "experience", "ids": [exp_id]}]}, draft=True)
        self.conn.commit()
        saved = self.client.put(f"/api/resume/resumes/{rid}", json={"revision": 1, "draft": True, "title": "手工保存继续可用"})
        self.assertEqual(saved.status_code, 200, saved.text)
        revision = saved.json()["revision"]
        published = self.client.post(f"/api/resume/resumes/{rid}/publish", json={"revision": revision})
        self.assertEqual(published.status_code, 200, published.text)
        job = dict(self.conn.execute("SELECT * FROM ai_jobs WHERE task_type='resume_render'").fetchone())
        payload = json.loads(job["payload_json"])
        result = asyncio.run(generation.execute_resume_render(job, payload))
        self.assertTrue(generation.apply_resume_render(self.conn, job, payload, result))
        self.conn.commit()
        preview = self.client.get(f"/api/resume/resumes/{rid}/preview?revision={revision}")
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertIn("合成学生", preview.text)


if __name__ == "__main__":
    unittest.main()
