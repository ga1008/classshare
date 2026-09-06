"""Small private suggestions on the same durable and bounded career lane."""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from typing import Any

from . import resume_ai_service as ai
from . import resume_document_service as documents
from . import resume_profile_service as profile
from ..student_career_job_service import (
    enqueue_student_career_job, register_student_career_handler,
    public_job_state, supersede_student_career_jobs,
)

TASK_TYPE = "resume_suggestion"


def _owned_job(conn, student_id: int, job_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM ai_jobs WHERE id = ? AND owner_role = 'student' AND owner_user_pk = ? AND task_type = ?", (int(job_id), int(student_id), TASK_TYPE)).fetchone()
    if not row:
        raise LookupError("建议任务不存在或无权访问")
    return dict(row)


def _safe_result(result: Any, kind: str) -> dict[str, Any]:
    result = result if isinstance(result, dict) else {}
    if not result.get("ok"):
        return {"ok": False, "error": "建议暂时无法生成，请重试或继续手工编辑。"}
    if kind == "self_intro":
        return {"ok": True, "content": str(result.get("content") or "")[:2000]}
    suggestions = result.get("suggestions") if isinstance(result.get("suggestions"), dict) else {}
    return {"ok": True, "suggestions": {key: str(suggestions[key] or "")[:200] for key in ("expected_position", "expected_industry", "email") if key in suggestions}}


def suggestion_state(conn, student_id: int, job_id: int) -> dict[str, Any]:
    job = _owned_job(conn, student_id, job_id)
    payload = json.loads(job.get("payload_json") or "{}")
    state = {"ok": True, "job": public_job_state(conn, job_id, student_id=student_id),
             "kind": payload.get("kind"), "profile_revision": payload.get("profile_revision")}
    if payload.get("kind") == "self_intro":
        state["input_text"] = str(payload.get("text") or "")[:8000]
    current = conn.execute("SELECT revision FROM resume_personal_info WHERE student_id = ?", (int(student_id),)).fetchone()
    state["stale"] = not current or int(current["revision"]) != int(payload.get("profile_revision") or 0)
    # Failed, cancelled and superseded tasks never reveal a stale suggestion.
    if job["status"] == "succeeded" and job.get("result_id"):
        row = conn.execute("SELECT result_json FROM ai_job_results WHERE id = ? AND job_id = ? AND status = 'succeeded'", (job["result_id"], int(job_id))).fetchone()
        if row:
            state["result"] = _safe_result(json.loads(row["result_json"] or "{}"), str(payload.get("kind")))
    return state


def queue_suggestion(conn, student_id: int, kind: str, *, text: str = "", retry: bool = False) -> dict[str, Any]:
    from ..career_rollout_service import require_student_ai
    require_student_ai(conn, student_id)
    if kind not in {"personal", "self_intro"}:
        raise ValueError("不支持的建议类型")
    text = str(text or "").strip()[:8000]
    if kind == "self_intro" and not text:
        raise ValueError("请先输入自我介绍内容")
    from .resume_generation_service import _student_context
    from ..ai_durable_job_service import ensure_ai_job_schema
    ensure_ai_job_schema(conn)
    profile._ensure_personal_row(conn, student_id)
    conn.execute("UPDATE resume_personal_info SET student_id = student_id WHERE student_id = ?", (int(student_id),))
    personal = profile.get_personal_info(conn, student_id)
    context = _student_context(conn, student_id)
    payload = {"kind": kind, "student_id": int(student_id), "profile_revision": personal["revision"], "text": text,
               "personal": {key: personal.get(key) for key in ("expected_position", "expected_industry", "email")},
               "context": {key: context.get(key) for key in ("major_name", "college")}}
    digest = hashlib.sha256(documents._json(payload).encode()).hexdigest()
    key = f"resume-suggestion:{student_id}:{kind}:{digest}"
    active = conn.execute("SELECT id,status FROM ai_jobs WHERE task_type = ? AND owner_user_pk = ? AND source_ref = ? AND status IN ('queued','running','retry_wait','result_ready') ORDER BY id DESC LIMIT 1", (TASK_TYPE, int(student_id), digest)).fetchone()
    if active:
        return suggestion_state(conn, student_id, int(active["id"]))
    existing = conn.execute("SELECT id,status FROM ai_jobs WHERE dedupe_key = ? AND owner_user_pk = ?", (key, int(student_id))).fetchone()
    if existing and not retry:
        if existing["status"] in {"queued", "running", "retry_wait", "result_ready", "succeeded"}:
            return suggestion_state(conn, student_id, int(existing["id"]))
        retry = True
    supersede_student_career_jobs(conn, scope_type="resume_suggestion", scope_id=f"{student_id}:{kind}", student_id=student_id)
    if retry:
        key += ":retry:" + uuid.uuid4().hex
    job = enqueue_student_career_job(conn, task_type=TASK_TYPE, dedupe_key=key, payload=payload,
        student_id=int(student_id), scope_type="resume_suggestion", scope_id=f"{student_id}:{kind}", source_ref=digest)
    return suggestion_state(conn, student_id, int(job["id"]))


def retry_suggestion(conn, student_id: int, job_id: int) -> dict[str, Any]:
    job = _owned_job(conn, student_id, job_id)
    payload = json.loads(job.get("payload_json") or "{}")
    state = public_job_state(conn, job_id, student_id=student_id)
    if state.get("cancellable"):
        return suggestion_state(conn, student_id, job_id)
    return queue_suggestion(conn, student_id, payload["kind"], text=payload.get("text", ""), retry=True)


async def execute_suggestion(job, payload) -> dict[str, Any]:
    from . import resume_generation_service as generation
    from ..student_career_job_service import SupersededCareerJob
    def current_revision():
        with generation.get_db_connection() as conn:
            row = conn.execute("SELECT revision FROM resume_personal_info WHERE student_id = ?", (int(payload["student_id"]),)).fetchone()
            return int(row["revision"]) if row else None
    if await asyncio.to_thread(current_revision) != int(payload["profile_revision"]):
        raise SupersededCareerJob()
    if payload["kind"] == "personal":
        result = await ai.build_personal_info_suggestions(payload["personal"], payload["context"])
    else:
        result = await ai.optimize_self_intro(payload["text"], payload["personal"])
    if not result.get("ok"):
        raise RuntimeError("suggestion_unavailable")
    return _safe_result(result, payload["kind"])


def apply_suggestion(conn, job, payload, result) -> bool:
    # Only the ledger stores the suggestion. This lock makes a concurrent
    # personal-profile save win cleanly; the student still chooses and saves.
    return conn.execute("UPDATE resume_personal_info SET revision = revision WHERE student_id = ? AND revision = ?", (int(payload["student_id"]), int(payload["profile_revision"]))).rowcount == 1


register_student_career_handler(TASK_TYPE, execute=execute_suggestion, apply=apply_suggestion, timeout_seconds=60)
