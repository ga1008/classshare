"""Loopback-only HTTP QA app using real routes/templates and an isolated SQLite DB.

Run: python tools/career_frontend_http_probe.py --port 8768 --output-dir .codex-temp/career-http
The output directory must be new (the script never opens the application's DB).
All model handlers are replaced with deterministic candidates; real apply/lease CAS
and the pure resume HTML renderer run unchanged. POST /__qa__/drain executes jobs.
This is a test tool, never mount it in the production application.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import runpy
import sqlite3
import sys
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DB_ENGINE"] = "sqlite"
os.environ["CAREER_JOBS_ENABLED"] = "1"
os.environ["CAREER_JOB_POLL_SECONDS"] = "1"


def source_fingerprints():
    paths = {ROOT / "tools/career_frontend_http_probe.py", ROOT / "tests/frontend/career_resume_http_probe.cjs",
             ROOT / "classroom_app/config.py", ROOT / "classroom_app/storage_paths.py", ROOT / "classroom_app/dependencies.py"}
    for folder, pattern in (("classroom_app/services", "*.py"), ("classroom_app/db", "*.py"),
                            ("classroom_app/routers", "*.py"), ("templates", "*.html"),
                            ("static/js", "*.js"), ("static/css", "*.css")):
        paths.update((ROOT / folder).rglob(pattern))
    values = {str(path.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(paths)}
    return values, hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def make_app(output_dir: Path):
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    db_path = output_dir / "synthetic.sqlite3"
    if db_path.exists():
        raise RuntimeError(f"Refusing to reuse an existing database: {db_path}")
    os.environ["DB_PATH"] = str(db_path)
    os.environ["LANSHARE_DATA_ROOT"] = str(output_dir / "data")
    os.environ["MAIN_GLOBAL_FILES_DIR"] = str(output_dir / "data/media/blobs/sha256")
    os.environ["RESUME_EXPORT_CACHE_DIR"] = str(output_dir / "exports")
    startup_files, startup_fingerprint = source_fingerprints()
    manifest_path = output_dir / "startup-source-hashes.json"
    manifest_path.write_text(json.dumps(startup_files, indent=2), encoding="utf-8")
    from fastapi import FastAPI, Request, HTTPException
    from fastapi.staticfiles import StaticFiles
    from classroom_app.dependencies import get_current_user
    from classroom_app.routers import career_path, resume_console
    from classroom_app.services import career_recommendation_service as recommendation
    from classroom_app.services import career_lifecycle_service as lifecycle
    from classroom_app.services import student_career_job_service as registry
    from classroom_app.services import student_career_job_worker as worker
    from classroom_app.services import ai_durable_job_service as durable
    from classroom_app.services.resume import resume_document_service as docs
    from classroom_app.services.resume import resume_profile_service as profile
    from classroom_app.services.resume import resume_generation_service as generation
    from classroom_app.services.resume import resume_import_service  # register domain
    from classroom_app.services import file_service

    # Never fall back to a real student's legacy blob, even if a synthetic hash
    # happens to match one. Both upload and resolution stay in this fixture.
    file_service.GLOBAL_FILES_LEGACY_DIRS = ()

    seed = runpy.run_path(str(ROOT / "tests/test_career_lifecycle.py"))["fixture"]()
    seed.execute("CREATE TABLE IF NOT EXISTS submissions(id INTEGER PRIMARY KEY)")
    for student_id in (1, 2):
        profile.update_personal_info(seed, student_id, {"name": f"合成学生{student_id}", "email": f"student{student_id}@example.test", "phone": "13800000000", "expected_position": "英语教师"})
        profile.create_section_item(seed, student_id, "experience", {"kind": "internship", "title": f"学生{student_id}的教学实习", "start_date": "2025-01", "end_date": "2025-06", "role": "实习教师", "content": "设计英语课堂活动", "contribution": "编写教案", "achievement": "完成三次公开课"})
        profile.create_section_item(seed, student_id, "education", {"kind": "university", "school": "合成测试学校", "degree": "本科", "major": "英语", "start_date": "2024-09", "end_date": "2028-06"})
    seed.commit()
    target = sqlite3.connect(str(db_path)); seed.backup(target); target.close(); seed.close()

    @contextmanager
    def connection():
        conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=15000")
        try:
            yield conn
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    # Patch imported connection references before the application handles a request.
    for name, module in list(sys.modules.items()):
        if name.startswith("classroom_app") and hasattr(module, "get_db_connection"):
            module.get_db_connection = connection
    with connection() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.commit()
    lifecycle._cached_recommend.cache_clear()
    lifecycle._validated_graph.cache_clear()

    async def stub(job, payload):
        kind = job["task_type"]
        if kind == "career_major_network_generate":
            return {"network": recommendation.baseline_network(payload["major_name"]), "sources": {"verified": False, "test_fixture": True}}
        if kind == "career_personalize_generate":
            return {"summary": "根据已填写资料探索职业方向，请用真实经历进一步核对。", "timeline_advice": "从一段真实课程或实习成果开始。", "node_tips": {}}
        if kind == "resume_optimize":
            return {"summary_md": "具有英语教学实习与课程活动设计经验。", "tech_stack": [{"group": "教学沟通", "items": ["英语教学", "课堂沟通"]}], "notes": ["请核对实习职责与时间，合成建议不会直接覆盖简历。"]}
        if kind == "resume_intro":
            return {"content_md": "具有英语教学实习经验，参与课堂活动设计与教案编写。"}
        if kind == "resume_suggestion":
            return {"ok": True, "suggestions": {"expected_industry": "教育服务"}} if payload["kind"] == "personal" else {"ok": True, "content": "具有教学实习经验，参与英语课堂活动设计。"}
        if kind == "resume_import":
            return {"parsed": {"personal": {"name": "待核对的导入姓名"}, "experience": []}, "baseline_revision": 1}
        raise RuntimeError(f"No model stub permitted for {kind}")

    for kind, handler in registry.registered_student_career_handlers().items():
        if kind != "resume_render":
            registry.register_student_career_handler(kind, execute=stub, apply=handler.apply,
                fail=handler.fail, timeout_seconds=10, lane=handler.lane)

    app = FastAPI(title="Isolated student career frontend QA")
    app.include_router(career_path.router)
    app.include_router(resume_console.router)
    app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")

    def synthetic_user(request: Request):
        value = request.cookies.get("qa_student", "1")
        if value not in {"1", "2"}:
            raise HTTPException(401, "请选择合成测试学生")
        return {"id": int(value), "role": "student", "name": f"合成学生{value}"}

    # Annotation is assigned explicitly because the dependency is nested and
    # FastAPI otherwise cannot resolve the postponed Request annotation.
    synthetic_user.__annotations__["request"] = Request
    app.dependency_overrides[get_current_user] = synthetic_user

    @app.get("/__qa__/health")
    def health():
        with connection() as conn:
            jobs = [dict(row) for row in conn.execute("SELECT id,task_type,status,last_error_code FROM ai_jobs ORDER BY id")]
        current_files, current_fingerprint = source_fingerprints()
        changed = sorted(name for name in set(startup_files) | set(current_files) if startup_files.get(name) != current_files.get(name))
        return {"ok": True, "isolated": True, "database": str(db_path), "data_root": os.environ["LANSHARE_DATA_ROOT"], "jobs": jobs,
                "source_fingerprint": startup_fingerprint, "current_fingerprint": current_fingerprint,
                "fixed_code": not changed, "changed_files": changed, "startup_manifest": str(manifest_path)}

    @app.post("/__qa__/drain")
    async def drain():
        types = tuple(registry.registered_student_career_handlers())
        completed = 0
        for _ in range(8):
            jobs = await asyncio.to_thread(durable.claim_due_ai_jobs, limit=20, worker_id="qa-http", task_types=types)
            for job in jobs:
                await worker._execute(job)
            deliveries = await asyncio.to_thread(durable.claim_result_ready_ai_jobs, limit=20, worker_id="qa-http-delivery", task_types=types)
            for job in deliveries:
                completed += bool(await asyncio.to_thread(worker._apply_result, job))
            if not jobs and not deliveries:
                break
        return {"ok": True, "applied": completed}

    @app.post("/__qa__/conflict/{resume_id}")
    def create_conflict(resume_id: int):
        with connection() as conn:
            current = docs.get_resume(conn, 1, resume_id)
            docs.update_resume(conn, 1, resume_id, title="另一个窗口的新标题", target_position=current["target_position"],
                template_key=current["template_key"], layout=current["layout"], expected_revision=current["revision"], draft=True)
            conn.commit()
        return {"ok": True}

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(make_app(args.output_dir), host="127.0.0.1", port=args.port, log_level="warning")
