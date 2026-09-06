"""AI-rollout correctness through real routes in one disposable local PG schema.

This is a correctness probe, not a performance measurement. Only model execute
is stubbed; admission, revisions, real HTML render and durable apply stay real.
"""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient
from tools.career_postgres_workflow_probe import isolated_career_postgres, _answers
from classroom_app import config
from classroom_app.services import career_path_service as career, career_lifecycle_service as lifecycle
from classroom_app.services import career_rollout_service as rollout, student_career_job_service as registry
from classroom_app.services import student_career_job_worker as worker, ai_durable_job_service as durable
from classroom_app.services.career_recommendation_service import baseline_network
from classroom_app.services.resume import resume_profile_service as profile


def hashes():
    paths = [Path(__file__)]
    for folder, pattern in (("classroom_app", "*.py"), ("templates", "*.html"), ("static", "*.js"), ("static", "*.css")):
        paths.extend((ROOT/folder).rglob(pattern))
    return {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(paths)}


async def drain():
    kinds = ("career_major_network_generate", "resume_suggestion", "resume_render")
    for _ in range(20):
        pending = durable.claim_due_ai_jobs(limit=10, worker_id="rollout-qa", task_types=kinds)
        for job in pending:
            await worker._execute(job)
        ready = durable.claim_result_ready_ai_jobs(limit=10, worker_id="rollout-qa-apply", task_types=kinds)
        for job in ready:
            worker._apply_result(job)
        if not pending and not ready:
            return
    raise AssertionError("Synthetic admitted jobs did not drain")


def run():
    before = hashes(); checks = []
    with isolated_career_postgres(students=2) as fixture, ExitStack() as stack, TestClient(fixture["app"]) as client:
        connect = fixture["connect"]
        stack.enter_context(patch.multiple(config, CAREER_AI_ROLLOUT_MODE="allowlist", CAREER_AI_ROLLOUT_STUDENT_IDS="1", CAREER_AI_ROLLOUT_MAJORS="[]"))
        originals = dict(registry._HANDLERS)
        original_policies = dict(durable.TASK_POLICIES)
        stack.callback(lambda: (registry._HANDLERS.clear(), registry._HANDLERS.update(originals)))
        stack.callback(lambda: (durable.TASK_POLICIES.clear(), durable.TASK_POLICIES.update(original_policies)))
        async def execute(job, payload):
            if job["task_type"] == "career_major_network_generate":
                return {"network": baseline_network(payload["major_name"]), "sources": {"synthetic_only": True}}
            return {"ok": True, "suggestions": {"expected_industry": "教育服务"}}
        for kind in ("career_major_network_generate", "resume_suggestion"):
            handler = originals[kind]
            registry.register_student_career_handler(kind, execute=execute, apply=handler.apply, fail=handler.fail, timeout_seconds=10, lane=handler.lane)
        def request(sid, method, path, body=None, status=200):
            response = client.request(method, path, headers={"x-probe-student": str(sid)}, json=body)
            assert response.status_code == status, (path, response.status_code, response.text[:500])
            return response
        with connect() as conn:
            exp_id = profile.create_section_item(conn, 2, "experience", {"kind": "internship", "title": "合成实习", "start_date": "2025-01", "end_date": "2025-06", "content": "整理教学资料"})
            conn.commit()
        excluded = request(2, "POST", "/api/career-path/initialize").json()
        assert not excluded["ai_availability"]["allowed"] and excluded["network"]["nodes"]
        with connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0] == 0
            ctx = career.resolve_student_context(conn, 2)
        submitted = request(2, "POST", "/api/career-path/answers", {"answers": _answers(ctx), "mode": "quick", "quiz_version": career.QUIZ_VERSION, "revision": excluded["revision"], "enhance": True}).json()["state"]
        assert submitted["session_status"] == "ready" and submitted["rankings"]
        checks.append("excluded_student_keeps_baseline_and_submitted_quiz_without_ai")
        allowed = request(1, "POST", "/api/career-path/initialize").json()
        with connect() as conn:
            shared = dict(conn.execute("SELECT * FROM ai_jobs WHERE task_type='career_major_network_generate'").fetchone())
            assert shared["owner_role"] == "system" and shared["owner_user_pk"] is None
            assert json.loads(shared["payload_json"])["requested_by_student_id"] == 1
            shared_before = dict(conn.execute("SELECT * FROM career_major_networks").fetchone())
        denied = request(2, "POST", "/api/career-path/retry", {"target": "network", "revision": submitted["revision"]}, status=403)
        assert denied.json()["detail"]["code"] == "rollout_limited" and "retry-after" not in denied.headers
        with connect() as conn:
            assert shared_before == dict(conn.execute("SELECT * FROM career_major_networks").fetchone())
        checks.append("shared_system_job_binds_requester_and_rejects_excluded_retry_without_mutation")
        request(1, "POST", "/api/resume/personal/suggest", status=202)
        request(2, "POST", "/api/resume/personal/suggest", status=403)
        request(2, "POST", "/api/resume/self-intro/optimize", {"text": "保留原文"}, status=403)
        other_school = request(100001, "POST", "/api/career-path/initialize").json()
        assert not other_school["ai_availability"]["allowed"]
        checks.append("personal_ai_and_other_school_are_authoritatively_gated")
        document = request(2, "POST", "/api/resume/resumes", {"title": "名单外手工草稿", "draft": True, "target_position": "英语教师", "template_key": "classic", "layout": {"blocks": [{"type": "experience", "ids": [exp_id]}]}}).json()
        rid = document["id"]
        saved = request(2, "PUT", f"/api/resume/resumes/{rid}", {"revision": document["revision"], "draft": True, "title": "名单外手工修改"}).json()
        revision = saved["revision"]
        request(2, "POST", f"/api/resume/resumes/{rid}/publish", {"revision": revision})
        asyncio.run(drain())
        preview = request(2, "GET", f"/api/resume/resumes/{rid}/preview?revision={revision}")
        assert "合成学生2" in preview.text
        assert request(2, "GET", f"/api/resume/resumes/{rid}").json()["resume"]["render_revision"] == revision
        checks.append("excluded_student_manual_save_publish_real_html_and_pinned_preview_succeed")
        with connect() as conn:
            conn.execute("INSERT INTO career_major_networks(school_code,major_key,major_name,status) VALUES('career-probe','护理学','护理学','generating')")
            row = dict(conn.execute("SELECT * FROM career_major_networks WHERE major_key='护理学'").fetchone()); conn.commit()
            with __import__('contextlib').suppress(rollout.CareerRolloutLimited):
                registry.enqueue_student_career_job(conn, task_type="career_major_network_generate", dedupe_key="forged-system", payload={"network_id": row["id"], "school_code": "career-probe", "major_key": "护理学", "requested_by_student_id": 1})
            assert conn.execute("SELECT COUNT(*) FROM ai_jobs WHERE dedupe_key='forged-system'").fetchone()[0] == 0
            lifecycle.recover_career_jobs(conn)
            assert row == dict(conn.execute("SELECT * FROM career_major_networks WHERE id=?", (row["id"],)).fetchone())
            conn.commit()
        with patch.object(config, "CAREER_AI_ROLLOUT_MAJORS", '[{"school_code":"career-probe","major_key":"护理学"}]'):
            with connect() as conn:
                lifecycle.recover_career_jobs(conn); conn.commit()
            asyncio.run(drain())
        checks.append("system_payload_cannot_forge_requester_and_maintenance_uses_explicit_major_scope")
        with connect() as conn:
            statuses = [dict(row) for row in conn.execute("SELECT task_type,status,COUNT(*) AS count FROM ai_jobs GROUP BY task_type,status")]
            assert all(row["status"] == "succeeded" for row in statuses), statuses
        schema = fixture["schema"]
    import psycopg
    with psycopg.connect(config.DATABASE_URL, connect_timeout=5) as conn:
        removed = conn.execute("SELECT COUNT(*) FROM pg_namespace WHERE nspname=%s", (schema,)).fetchone()[0] == 0
    after = hashes(); changed = sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))
    return {"ok": removed and not changed, "checks": checks, "schema": schema, "schema_removed": removed,
            "scope": "Real ASGI routes + local isolated PostgreSQL + real durable claim/apply. Model execute stub only; no TCP/browser/Office performance claim.",
            "jobs": statuses, "fixed_code": not changed, "changed_files": changed, "source_hashes": before,
            "source_fingerprint": hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Use a new output path")
    report = run(); args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("ok", "checks", "schema_removed", "fixed_code", "source_fingerprint")}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["ok"] else 1)
