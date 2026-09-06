"""Real resume HTTP and durable worker flow in an ephemeral local PG schema.

Uses synthetic students and deterministic suggestion stubs. No production
application tables, model providers, external files or Office processes run.
The reusable fixture supports mixed-load and browser probes.
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from contextlib import ExitStack, contextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg
from psycopg import sql
from fastapi import FastAPI
from fastapi.testclient import TestClient
from classroom_app import config
from classroom_app.db import schema_resume, schema_ai_jobs
from classroom_app.db.postgres import LanSharePostgresConnection, sqlite_compatible_dict_row
from classroom_app.dependencies import get_current_user
from classroom_app.routers import resume_console as routes
from classroom_app.services import ai_durable_job_service as durable
from classroom_app.services import student_career_job_service as service
from classroom_app.services import student_career_job_worker as worker
from classroom_app.services.resume import resume_profile_service as profile
from classroom_app.services.resume import resume_document_service as documents
from classroom_app.services.resume import resume_generation_service as generation
from classroom_app.services.resume import resume_import_service as imports


@contextmanager
def isolated_resume_postgres():
    if urlsplit(config.DATABASE_URL).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Probe only permits local PostgreSQL")
    schema = "resume_probe_" + uuid.uuid4().hex
    def connect():
        raw = psycopg.connect(config.DATABASE_URL, connect_timeout=5,
            row_factory=sqlite_compatible_dict_row, options=f"-c search_path={schema}")
        return LanSharePostgresConnection(raw)
    created = False
    try:
        with psycopg.connect(config.DATABASE_URL, connect_timeout=5, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        created = True
        schema_resume._SCHEMA_READY = False
        schema_ai_jobs.reset_ai_job_schema_guard_for_tests()
        with ExitStack() as stack:
            for module in (schema_resume, schema_ai_jobs, durable, service, worker):
                stack.enter_context(patch.object(module, "get_configured_db_engine", return_value="postgres"))
            stack.enter_context(patch("classroom_app.db.connection.get_configured_db_engine", return_value="postgres"))
            for module in (routes, generation, imports, durable, worker):
                stack.enter_context(patch.object(module, "get_db_connection", connect))
            # Platform context/events are outside this isolated resume schema.
            stack.enter_context(patch.object(generation, "_student_context", return_value={"major_name": "英语", "school_name": "合成大学"}))
            stack.enter_context(patch.object(routes, "record_student_career_event_safely", return_value=None))
            stack.enter_context(patch.object(config, "CAREER_JOBS_ENABLED", True))
            stack.enter_context(patch.object(service, "CAREER_JOBS_ENABLED", True))
            with connect() as conn:
                conn.execute("CREATE TABLE submissions (id BIGINT PRIMARY KEY)")
                schema_resume.ensure_resume_schema(conn)
                schema_ai_jobs.ensure_ai_job_schema(conn, engine="postgres")
                for sid in (1, 2):
                    profile.update_personal_info(conn, sid, {"name": f"合成学生{sid}", "email": f"student{sid}@example.invalid", "expected_position": "英语教师"})
                exp_id = profile.create_section_item(conn, 1, "experience", {"kind": "internship", "title": "教学实习", "start_date": "2025-01", "end_date": "2025-06", "content": "设计课堂活动", "achievement": "完成公开课"})
                conn.commit()
            app = FastAPI()
            app.include_router(routes.router)
            app.dependency_overrides[get_current_user] = lambda: {"id": 1, "role": "student"}
            yield {"app": app, "connect": connect, "schema": schema, "experience_id": exp_id}
    finally:
        schema_resume._SCHEMA_READY = False
        schema_ai_jobs.reset_ai_job_schema_guard_for_tests()
        if created:
            with psycopg.connect(config.DATABASE_URL, connect_timeout=5, autocommit=True) as admin:
                admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def drain_resume_jobs():
    kinds = ("resume_render", "resume_optimize", "resume_import", "resume_intro", "resume_suggestion")
    for _ in range(30):
        delivery = durable.claim_result_ready_ai_jobs(limit=1, worker_id="resume-probe-delivery", task_types=kinds)
        if delivery:
            worker._apply_result(delivery[0])
            continue
        jobs = durable.claim_due_ai_jobs(limit=1, worker_id="resume-probe-execute", task_types=kinds)
        if not jobs:
            return
        asyncio.run(worker._execute(jobs[0]))
    raise AssertionError("Resume probe jobs failed to drain")


def run() -> dict:
    checks = []
    with isolated_resume_postgres() as fixture, TestClient(fixture["app"]) as client:
        def request(method, path, payload=None, status=200):
            response = client.request(method, "/api/resume/" + path, json=payload)
            assert response.status_code == status, (path, response.status_code, response.text)
            return response
        layout = {"blocks": [{"type": "experience", "ids": [fixture["experience_id"]]}]}
        body = {"client_id": "probe-client", "title": "合成简历", "target_position": "英语教师", "template_key": "classic", "layout": layout, "draft": True}
        rid = request("POST", "resumes", body).json()["id"]
        assert request("POST", "resumes", body).json()["id"] == rid
        request("PUT", f"resumes/{rid}", {**body, "revision": 1, "title": "版本二"})
        request("PUT", f"resumes/{rid}", {**body, "revision": 1}, 409)
        publish = request("POST", f"resumes/{rid}/publish", {"revision": 2}).json()
        repeated = request("POST", f"resumes/{rid}/publish", {"revision": 2}).json()
        assert publish["job"]["id"] == repeated["job"]["id"]
        drain_resume_jobs()
        preview = request("GET", f"resumes/{rid}/preview?revision=2")
        assert preview.headers["x-resume-revision"] == "2" and "教学实习" in preview.text
        checks += ["draft_save_cas", "create_publish_idempotency", "worker_render_frozen_preview"]
        async def suggestion(*args, **kwargs):
            return {"summary_md": "教学实习与课堂活动设计经历。", "tech_stack": [], "notes": ["请核对摘要"]}
        with patch.object(generation.ai, "optimize_resume_for_target", side_effect=suggestion):
            request("POST", f"resumes/{rid}/optimize", {"revision": 2})
            drain_resume_jobs()
        detail = request("GET", f"resumes/{rid}").json()
        with fixture["connect"]() as conn:
            assert not documents.get_resume(conn, 1, rid)["optimized_summary_md"]
        candidates = request("GET", f"resumes/{rid}/candidates").json()["items"]
        cid = candidates[0]["id"]
        accepted = request("POST", f"resumes/{rid}/candidates/{cid}/accept", {"revision": 2}).json()
        assert accepted["revision"] == 3
        drain_resume_jobs()
        assert "课堂活动设计" in request("GET", f"resumes/{rid}/preview?revision=3").text
        restored = request("POST", f"resumes/{rid}/versions/2/restore", {"revision": 3}).json()
        assert restored["revision"] == 4
        drain_resume_jobs()
        assert "课堂活动设计经历" not in request("GET", f"resumes/{rid}/preview?revision=4").text
        checks += ["suggestion_requires_acceptance", "accept_creates_version", "restore_creates_version"]
        request("PUT", f"resumes/{rid}", {**body, "revision": 4, "title": "未渲染草稿"})
        request("GET", f"resumes/{rid}/preview?revision=5", status=409)
        assert request("GET", f"resumes/{rid}/preview?revision=2").text == preview.text
        async def personal_suggestion(*args, **kwargs):
            return {"ok": True, "suggestions": {"expected_position": "英语教师", "student_id": 999}}
        first = request("POST", "personal/suggest", status=202).json()
        same = request("POST", "personal/suggest", status=202).json()
        assert first["job"]["id"] == same["job"]["id"]
        jid = first["job"]["id"]
        request("POST", f"suggestions/jobs/{jid}/cancel", status=202)
        retried = request("POST", f"suggestions/jobs/{jid}/retry", status=202).json()
        assert retried["job"]["id"] != jid
        assert request("POST", "personal/suggest", status=202).json()["job"]["id"] == retried["job"]["id"]
        with patch.object(generation.ai, "build_personal_info_suggestions", side_effect=personal_suggestion):
            drain_resume_jobs()
        suggestion_result = request("GET", f"suggestions/jobs/{retried['job']['id']}").json()
        assert suggestion_result["result"]["suggestions"] == {"expected_position": "英语教师"}
        assert "result" not in request("GET", f"suggestions/jobs/{jid}").json()
        checks += ["short_suggestions_durable_dedupe_cancel_retry", "suggestion_result_whitelist"]
        def simultaneous_save(title):
            with TestClient(fixture["app"]) as window:
                return window.put(f"/api/resume/resumes/{rid}", json={"revision": 5, "draft": True, "title": title}).status_code
        with ThreadPoolExecutor(max_workers=2) as windows:
            outcomes = list(windows.map(simultaneous_save, ("窗口甲", "窗口乙")))
        assert sorted(outcomes) == [200, 409], outcomes
        checks.append("simultaneous_editor_cas")
        fixture["app"].dependency_overrides[get_current_user] = lambda: {"id": 2, "role": "student"}
        request("GET", f"resumes/{rid}", status=404)
        request("GET", f"suggestions/jobs/{retried['job']['id']}", status=404)
        request("POST", f"suggestions/jobs/{retried['job']['id']}/retry", status=404)
        checks += ["no_stale_export_substitution", "owner_isolation"]
        with fixture["connect"]() as conn:
            statuses = {row["status"]: int(row["n"]) for row in conn.execute("SELECT status,COUNT(*) AS n FROM ai_jobs GROUP BY status")}
            assert set(statuses) == {"succeeded", "cancelled"}, statuses
    return {"ok": True, "engine": "postgres", "isolated_schema_removed": True, "checks": checks, "jobs": statuses}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
