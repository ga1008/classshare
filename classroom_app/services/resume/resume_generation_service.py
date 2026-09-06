"""Durable resume handlers: compute outside transactions, apply with revision guards."""

from __future__ import annotations

import json
import asyncio
import hashlib
import re
from typing import Any


from ...database import get_db_connection
from . import resume_ai_service as ai
from . import resume_document_service as docs
from . import resume_profile_service as profile
from . import resume_render_service as render
from ..student_career_job_service import (
    enqueue_student_career_job, register_student_career_handler, public_job_state,
    supersede_student_career_jobs, SupersededCareerJob,
)


def _student_context(conn, student_id: int) -> dict[str, Any]:
    try:
        from ..career_path_service import resolve_student_context

        return resolve_student_context(conn, int(student_id)) or {}
    except Exception:
        return {}




# ---------------------------------------------------------------------------
# 1) Self-introduction deep generation
# ---------------------------------------------------------------------------
_INTRO_SENTENCE_RE = re.compile(r"(?<=[。！？!?])\s*")


def _compact_resume_intro(text: Any, *, limit: int = 180) -> str:
    """Keep AI output shaped like a resume summary, not a chatty essay."""
    raw = str(text or "").strip()
    if not raw:
        return ""
    raw = re.sub(r"```(?:markdown|md|text)?\s*|\s*```", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", raw)
    raw = re.sub(r"(?m)^\s*[-*]\s+", "", raw)
    raw = re.sub(r"^\s*(个人介绍|自我介绍|职业摘要|简历摘要)\s*\n+", "", raw)
    raw = re.sub(r"^\s*(个人介绍|自我介绍|职业摘要|简历摘要)\s*[:：]\s*", "", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    if not raw:
        return ""

    sentences = [s.strip() for s in _INTRO_SENTENCE_RE.split(raw) if s.strip()]
    compact = ""
    for sentence in sentences:
        candidate = (compact + sentence).strip()
        if compact and len(candidate) > limit:
            break
        compact = candidate
        if compact.count("。") + compact.count("！") + compact.count("？") >= 3:
            break
    compact = compact or raw[:limit]
    if len(compact) > limit:
        compact = compact[:limit].rstrip("，、；; ")
    if compact and compact[-1] not in "。！？!?":
        compact += "。"
    return compact


def _clean_intro_background_label(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    lowered = raw.lower()
    if any(term in lowered for term in ("regression", "fixture", "mock", "qa-", "qa ", "test", "p03")):
        return ""
    if raw in {"待完善", "未知", "无", "暂无"}:
        return ""
    return raw[:24]


def _first_education_major(bundle: dict[str, Any]) -> str:
    for edu in bundle.get("education", []):
        if isinstance(edu, dict):
            major = _clean_intro_background_label(edu.get("major"))
            if major:
                return major
    return ""


def _fallback_self_intro(bundle: dict[str, Any], ctx: dict[str, Any]) -> str:
    personal = bundle.get("personal") or {}
    position = personal.get("expected_position") or "相关岗位"
    major = _clean_intro_background_label(ctx.get("major_name")) or _first_education_major(bundle)
    skills = [name for group in ai._fallback_tech_stack(bundle) for name in group["items"]]
    experiences = [
        str(e.get("title") or "").strip()
        for e in bundle.get("experience", [])
        if str(e.get("title") or "").strip()
    ]

    opening = f"具备{major}相关学习背景，求职意向为{position}" if major else f"求职意向为{position}"
    if skills:
        opening += f"，掌握{'、'.join(skills[:4])}等技能"
    opening += "。"

    if experiences:
        practice = f"具有{'、'.join(experiences[:2])}等实践经历。"
    else:
        practice = ""
    return _compact_resume_intro(opening + practice)




# ---------------------------------------------------------------------------
# 2) Résumé render (tech stack + HTML assembly)
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# 3) First-visit education auto-seed
# ---------------------------------------------------------------------------
def _fallback_education(ctx: dict[str, Any]) -> dict[str, Any]:
    timeline = ctx.get("timeline") or {}
    start = str(timeline.get("enrollment_year") or "").strip()
    end = str(timeline.get("graduation_year") or "").strip()
    return {
        "kind": "university",
        "school": ctx.get("school_name") or "",
        "college": ctx.get("college") or ctx.get("department") or "",
        "major": ctx.get("major_name") or "",
        "start_date": (start + "-09") if start else "",
        "end_date": (end + "-06") if end else "",
        "content": "",
    }


def _normalize_seed_education(
    edu: dict[str, Any] | None,
    *,
    fallback: dict[str, Any],
    ctx: dict[str, Any],
) -> dict[str, str]:
    source = edu if isinstance(edu, dict) else {}
    merged = {**fallback, **{key: value for key, value in source.items() if value}}
    return {
        "kind": str(merged.get("kind") or "university")[:40],
        "school": str(merged.get("school") or fallback.get("school") or ctx.get("school_name") or "")[:120],
        "college": str(merged.get("college") or fallback.get("college") or "")[:120],
        "major": str(merged.get("major") or fallback.get("major") or ctx.get("major_name") or "")[:120],
        "start_date": str(merged.get("start_date") or fallback.get("start_date") or "")[:20],
        "end_date": str(merged.get("end_date") or fallback.get("end_date") or "")[:20],
        "content": str(merged.get("content") or fallback.get("content") or "")[:1000],
        "source": "ai_auto",
    }


def _current_resume(conn, job: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any] | None:
    try:
        resume = docs.get_resume(conn, int(payload["student_id"]), int(payload["resume_id"]))
    except ValueError:
        return None
    if int(resume.get("revision") or 1) != int(payload["revision"]) or str(resume.get("active_job_id") or "") != str(job["id"]):
        return None
    return resume



def _lock_current_resume(conn, job, payload) -> bool:
    # Establish the business row lock before touching candidates or versions.
    return conn.execute("UPDATE resumes SET revision = revision WHERE id = ? AND student_id = ? AND revision = ? AND active_job_id = ? AND archived = 0",
                        (int(payload["resume_id"]), int(payload["student_id"]), int(payload["revision"]), str(job["id"]))).rowcount == 1

def queue_resume_job(conn, student_id: int, resume_id: int, kind: str, *, retry: bool = False) -> dict[str, Any]:
    if kind not in {"render", "optimize", "import"}:
        raise ValueError("不支持的简历任务")
    from ..ai_durable_job_service import ensure_ai_job_schema
    from ..career_rollout_service import require_student_ai
    if kind != "render":
        require_student_ai(conn, student_id)
    ensure_ai_job_schema(conn)
    resume = docs.get_resume(conn, student_id, resume_id)
    revision = int(resume.get("revision") or 1)
    locked = conn.execute("UPDATE resumes SET revision = revision WHERE id = ? AND student_id = ? AND revision = ? AND archived = 0", (int(resume_id), int(student_id), revision))
    if locked.rowcount != 1:
        raise docs.ResumeConflict("简历已更新，请重试当前操作。")
    current_job = public_job_state(conn, resume.get("active_job_id"), student_id=student_id)
    if current_job.get("cancellable") and current_job.get("task_type") == "resume_" + kind:
        return current_job
    supersede_student_career_jobs(conn, scope_type="resume", scope_id=str(resume_id), student_id=student_id)
    if kind != "import":
        try:
            docs.get_version(conn, student_id, resume_id, revision)
        except LookupError:
            docs.capture_version(conn, student_id, resume_id)
    # An explicit retry gets a new logical attempt id; automatic retries stay
    # under the durable job's bounded budget and never reset on a polling GET.
    import uuid
    key = f"resume:{student_id}:{resume_id}:{revision}:{kind}"
    existing = conn.execute("SELECT id,status FROM ai_jobs WHERE dedupe_key = ?", (key,)).fetchone()
    if existing and not retry:
        if existing["status"] == "succeeded":
            if kind == "render" and docs.get_version(conn, student_id, resume_id, revision).get("render_html"):
                conn.execute("UPDATE resumes SET status = 'ready' WHERE id = ? AND student_id = ? AND revision = ?", (int(resume_id), int(student_id), revision))
                return public_job_state(conn, existing["id"], student_id=student_id)
            pending = conn.execute("SELECT 1 FROM resume_candidates WHERE resume_id = ? AND student_id = ? AND base_revision = ? AND status = 'pending' AND kind = ?", (int(resume_id), int(student_id), revision, "import" if kind == "import" else "optimization")).fetchone()
            if pending:
                conn.execute("UPDATE resumes SET status = 'review_ready' WHERE id = ? AND student_id = ? AND revision = ?", (int(resume_id), int(student_id), revision))
                return public_job_state(conn, existing["id"], student_id=student_id)
        retry = True  # A new explicit command may replace a terminal attempt.
    if retry:
        key += ":retry:" + uuid.uuid4().hex
    job = enqueue_student_career_job(
        conn, task_type="resume_" + kind, dedupe_key=key,
        payload={"student_id": int(student_id), "resume_id": int(resume_id), "revision": revision},
        student_id=int(student_id), scope_type="resume", scope_id=str(resume_id), source_ref=str(resume_id))
    status = {"render": "rendering", "optimize": "optimizing", "import": "parsing"}[kind]
    conn.execute("UPDATE resumes SET status = ?, active_job_id = ?, error_text = '' WHERE id = ? AND student_id = ? AND revision = ? AND archived = 0",
                 (status, str(job["id"]), int(resume_id), int(student_id), revision))
    return public_job_state(conn, job["id"], student_id=student_id)


def _load_task_version(job, payload, *, context=False):
    with get_db_connection() as conn:
        if not _current_resume(conn, job, payload):
            raise SupersededCareerJob()
        version = docs.get_version(conn, int(payload["student_id"]), int(payload["resume_id"]), int(payload["revision"]))
        return (version, _student_context(conn, int(payload["student_id"]))) if context else version


async def execute_resume_render(job: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    version = await asyncio.to_thread(_load_task_version, job, payload)
    # Rendering uses a frozen bundle and does not open a DB connection or call
    # a model. AI enrichment is a separate suggestion task the student accepts.
    html = await asyncio.to_thread(render.assemble_resume_html, None, int(payload["student_id"]), docs.snapshot_resume(version))
    return {"render_html": html, "content_hash": version["content_hash"]}


def apply_resume_render(conn, job, payload, result) -> bool:
    if not _lock_current_resume(conn, job, payload):
        return False
    docs.save_version_render(conn, int(payload["student_id"]), int(payload["resume_id"]), int(payload["revision"]), str(result["render_html"]))
    return True


async def execute_resume_optimization(job, payload) -> dict[str, Any]:
    version, ctx = await asyncio.to_thread(_load_task_version, job, payload, context=True)
    resume = docs.snapshot_resume(version)
    # The model receives only the selected evidence and the exact private JD.
    return await ai.optimize_resume_for_target(resume, resume["content_snapshot"], ctx)


def apply_resume_candidate(conn, job, payload, result) -> bool:
    if not _lock_current_resume(conn, job, payload):
        return False
    kind = "import" if job["task_type"] == "resume_import" else "optimization"
    docs.create_candidate(conn, int(payload["student_id"]), int(payload["resume_id"]), int(payload["revision"]), kind, result, job_id=str(job["id"]))
    conn.execute("UPDATE resumes SET status = 'review_ready', error_text = '', active_job_id = '' WHERE id = ? AND student_id = ? AND revision = ?",
                 (int(payload["resume_id"]), int(payload["student_id"]), int(payload["revision"])))
    return True


async def execute_resume_import(job, payload) -> dict[str, Any]:
    from . import resume_import_service
    return await resume_import_service.execute_import_candidate(job, payload)


def fail_resume_job(conn, job, payload, code, message) -> None:
    if _lock_current_resume(conn, job, payload):
        user_message = {"ResumeImportResourceLimit": "文件页数、图片像素或解压后内容过大，请精简后重新上传。", "ResumeImportInvalidDocument": "文件无法解析或带密码，请上传可正常打开的原件。"}.get(code, "处理失败，可重试。" + str(code)[:80])
        conn.execute("UPDATE resumes SET status = 'failed', error_text = ? WHERE id = ? AND student_id = ? AND revision = ? AND active_job_id = ?",
                     (user_message, int(payload["resume_id"]), int(payload["student_id"]), int(payload["revision"]), str(job["id"])))


def _intro_input(conn, student_id: int) -> dict[str, Any]:
    data = profile.collect_profile_bundle(conn, student_id)
    return {"personal": {"expected_position": (data.get("personal") or {}).get("expected_position", "")},
            **{key: data.get(key) or [] for key in ("skill", "certificate", "education", "experience")}}


def queue_intro_job(conn, student_id: int, intro_id: int) -> dict[str, Any]:
    from ..career_rollout_service import require_student_ai
    require_student_ai(conn, student_id)
    intro = profile.get_section_item(conn, student_id, "self_intro", intro_id)
    bundle = _intro_input(conn, student_id)
    ctx = _student_context(conn, student_id)
    job = enqueue_student_career_job(conn, task_type="resume_intro", dedupe_key=f"resume-intro:{student_id}:{intro_id}:{intro['revision']}",
        payload={"student_id": int(student_id), "intro_id": int(intro_id), "revision": int(intro["revision"]), "bundle": bundle,
                 "evidence_hash": hashlib.sha256(docs._json(bundle).encode()).hexdigest(),
                 "context": {key: ctx.get(key) for key in ("major_name", "college")}},
        student_id=int(student_id), scope_type="resume_intro", scope_id=str(intro_id))
    conn.execute("UPDATE resume_self_intros SET active_job_id = ? WHERE id = ? AND student_id = ?", (str(job["id"]), int(intro_id), int(student_id)))
    return public_job_state(conn, job["id"], student_id=student_id)


def begin_intro_job(conn, student_id: int) -> tuple[int, dict[str, Any]]:
    from ..ai_durable_job_service import ensure_ai_job_schema, load_ai_job_payload
    ensure_ai_job_schema(conn)
    profile._ensure_personal_row(conn, student_id)
    conn.execute("UPDATE resume_personal_info SET student_id = student_id WHERE student_id = ?", (int(student_id),))
    fingerprint = hashlib.sha256(docs._json(_intro_input(conn, student_id)).encode()).hexdigest()
    pending = conn.execute("SELECT id,active_job_id FROM resume_self_intros WHERE student_id = ? AND status = 'generating' ORDER BY id LIMIT 10", (int(student_id),)).fetchall()
    for intro in pending:
        state = public_job_state(conn, intro["active_job_id"], student_id=student_id)
        if state.get("cancellable"):
            raw = conn.execute("SELECT * FROM ai_jobs WHERE id = ?", (int(intro["active_job_id"]),)).fetchone()
            if raw and load_ai_job_payload(dict(raw)).get("evidence_hash") == fingerprint:
                return int(intro["id"]), state
        conn.execute("UPDATE resume_self_intros SET status = 'failed', revision = revision + 1, active_job_id = '', error_text = '资料已更新或原任务中断，请使用新的建议。' WHERE id = ? AND student_id = ?", (int(intro["id"]), int(student_id)))
        supersede_student_career_jobs(conn, scope_type="resume_intro", scope_id=str(intro["id"]), student_id=student_id)
    intro_id = profile.create_self_intro_placeholder(conn, student_id)
    return intro_id, queue_intro_job(conn, student_id, intro_id)


async def execute_intro(job, payload) -> dict[str, Any]:
    bundle, ctx = payload["bundle"], payload["context"]
    digest = {"professional_background": ctx, "skills": bundle.get("skill"), "experience": bundle.get("experience"), "education": bundle.get("education")}
    text = await ai._chat("请根据真实材料写80-140字职业摘要，适用学生的专业，不编造能力、成果或工作方式，不写求职愿望。只返回正文。",
                          json.dumps(digest, ensure_ascii=False), want_json=False, capability="thinking", timeout=180, label="resume:self-intro")
    return {"content_md": _compact_resume_intro(text) or _fallback_self_intro(bundle, ctx)}


def apply_intro(conn, job, payload, result) -> bool:
    if payload.get("evidence_hash") != hashlib.sha256(docs._json(_intro_input(conn, int(payload["student_id"]))).encode()).hexdigest():
        return False
    cursor = conn.execute("UPDATE resume_self_intros SET content_md = ?, title = '职业摘要建议（请核实）', status = 'ready', "
                          "active_job_id = '', updated_at = ?, revision = revision + 1 WHERE id = ? AND student_id = ? AND revision = ? AND active_job_id = ?",
                          (str(result["content_md"]), docs._now(), int(payload["intro_id"]), int(payload["student_id"]), int(payload["revision"]), str(job["id"])))
    return cursor.rowcount == 1


def fail_intro(conn, job, payload, code, message) -> None:
    conn.execute("UPDATE resume_self_intros SET status = 'failed', error_text = ? WHERE id = ? AND student_id = ? AND revision = ? AND active_job_id = ?",
                 ("生成失败，可重试或手动编辑。" + str(code)[:80], int(payload["intro_id"]), int(payload["student_id"]), int(payload["revision"]), str(job["id"])))


def seed_education_from_context(conn, student_id: int) -> int | None:
    # Deterministic source facts need no AI task. A per-student row lock makes
    # repeated first-use requests idempotent on PostgreSQL and SQLite.
    profile._ensure_personal_row(conn, student_id)
    conn.execute("UPDATE resume_personal_info SET student_id = student_id WHERE student_id = ?", (int(student_id),))
    if profile.has_any_education(conn, student_id):
        return None
    ctx = _student_context(conn, student_id)
    if not ctx:
        return None
    fields = _fallback_education(ctx)
    if not fields["school"] or not fields["start_date"] or not fields["end_date"]:
        return None
    fields["content"] = ""  # Do not infer unverified coursework from a major.
    return profile.create_section_item(conn, student_id, "education", {**fields, "source": "platform"})


def recover_resume_jobs(conn) -> int:
    from ...db.schema_resume import ensure_resume_schema
    ensure_resume_schema(conn)
    docs.backfill_resume_versions(conn, limit=100)
    from .resume_application_service import backfill_application_snapshots
    backfill_application_snapshots(conn, limit=100)
    render.cleanup_export_cache()
    count = 0
    for table, statuses in (("resumes", "'rendering','optimizing','parsing'"), ("resume_self_intros", "'generating'")):
        rows = conn.execute(f"SELECT id,student_id,active_job_id FROM {table} WHERE status IN ({statuses}) LIMIT 100").fetchall()
        for row in rows:
            state = public_job_state(conn, row["active_job_id"], student_id=int(row["student_id"]))
            if not state or state.get("status") in {"dead_letter", "review_required", "cancelled", "superseded", "succeeded"}:
                conn.execute(f"UPDATE {table} SET status = 'failed', error_text = '后台任务已中断，请重试；已保存资料仍保留。' WHERE id = ? AND student_id = ? AND active_job_id = ?",
                             (int(row["id"]), int(row["student_id"]), str(row["active_job_id"] or "")))
                count += 1
    return count


register_student_career_handler("resume_render", execute=execute_resume_render, apply=apply_resume_render, fail=fail_resume_job, timeout_seconds=120, lane="render")
register_student_career_handler("resume_optimize", execute=execute_resume_optimization, apply=apply_resume_candidate, fail=fail_resume_job, timeout_seconds=300)
register_student_career_handler("resume_import", execute=execute_resume_import, apply=apply_resume_candidate, fail=fail_resume_job, timeout_seconds=360)
register_student_career_handler("resume_intro", execute=execute_intro, apply=apply_intro, fail=fail_intro, timeout_seconds=240)



# Register short suggestions on the same shared worker and concurrency lane.
from . import resume_suggestion_service as _suggestion_handlers  # noqa: E402,F401
