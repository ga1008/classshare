"""Background AI jobs for the resume console (asyncio tasks).

Triggered via ``asyncio.create_task`` from the router (same pattern as
``assessment_plan_generation_service``). Each job opens its own DB connection,
commits, and is fully graceful — on AI failure it falls back to a deterministic
draft so the closed loop never strands the student with an empty placeholder.

Jobs:

* ``run_self_intro_generation_job``  — deep self-introduction from all profile data.
* ``run_resume_render_job``          — tech-stack gen (if requested) + HTML assembly.
* ``run_education_seed_job``         — first-visit auto education entry.
"""

from __future__ import annotations

import json
import re
import traceback
from typing import Any

import httpx

from ...database import get_db_connection
from . import resume_ai_service as ai
from . import resume_document_service as docs
from . import resume_profile_service as profile
from . import resume_render_service as render


def _student_context(conn, student_id: int) -> dict[str, Any]:
    try:
        from ..career_path_service import resolve_student_context

        return resolve_student_context(conn, int(student_id)) or {}
    except Exception:
        return {}


def _hidden_block(conn, student_id: int) -> str:
    try:
        from ..career_path_service import _build_hidden_profile_block

        return _build_hidden_profile_block(conn, int(student_id))
    except Exception:
        return ""


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
    skills = [str(s.get("name") or "").strip() for s in bundle.get("skill", []) if str(s.get("name") or "").strip()]
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
        practice = f"具备{'、'.join(experiences[:2])}等项目实践经验，能够围绕需求拆解、功能实现与问题定位推进交付。"
    else:
        practice = "具备扎实的专业学习基础，重视需求理解、代码质量与协作交付。"
    return _compact_resume_intro(opening + practice)


async def run_self_intro_generation_job(intro_id: int, student_id: int) -> None:
    try:
        with get_db_connection() as conn:
            bundle = profile.collect_profile_bundle(conn, student_id)
            ctx = _student_context(conn, student_id)
            hidden = _hidden_block(conn, student_id)
            conn.commit()

        digest = {
            "个人信息": {k: (bundle.get("personal") or {}).get(k)
                       for k in ("name", "expected_position", "expected_industry")},
            "专业": ctx.get("major_name"),
            "班级": ctx.get("class_name"),
            "学习经历": [{"学校": e.get("school"), "专业": e.get("major"), "内容": (e.get("content") or "")[:160]}
                       for e in bundle.get("education", [])][:6],
            "项目比赛": [{"标题": e.get("title"), "角色": e.get("role"), "内容": (e.get("content") or "")[:200],
                       "成果": e.get("achievement")} for e in bundle.get("experience", [])][:8],
            "技能": [s.get("name") for s in bundle.get("skill", [])][:30],
            "证书": [c.get("name") for c in bundle.get("certificate", [])][:20],
        }
        system = (
            "你是资深简历顾问。请基于学生的真实资料，写一段可直接放入简历“个人介绍/职业摘要”栏位的中文正文。"
            "要求：80-140 个中文字符，最多 3 句；专业、严谨、简洁，突出与期望岗位匹配的技能、项目/学习成果和工作方式。"
            "禁止聊天式自述、流水账、课堂任务过程、弱项说明、求职愿望、空泛表态；不要出现“希望”“贵单位”“未来我会”等套话。"
            "不要虚构未提供的事实，不要 Markdown、标题、称呼或解释，只返回正文。"
        )
        if hidden:
            system += "\n（以下后台学习支持参考仅用于推断能力侧重，绝不可在正文中提及课堂、班级、作业过程或其存在。）\n" + hidden
        user = "学生资料 JSON：\n" + json.dumps(digest, ensure_ascii=False, indent=2)

        content = ""
        error_text = ""
        try:
            content = await ai._chat(
                system, user, want_json=False, capability="thinking",
                task_type="deep_text_reasoning", timeout=240.0, label="resume:self-intro",
            )
            content = _compact_resume_intro(content)
            if not ai._resume_summary_is_useful(
                content,
                str((bundle.get("personal") or {}).get("expected_position") or ""),
            ):
                content = ""
        except (httpx.HTTPError, ValueError):
            content = ""
        if not content:
            content = _fallback_self_intro(bundle, ctx)
            error_text = "AI 生成暂不可用，已根据你的资料生成基础版本，可继续编辑优化。"

        with get_db_connection() as conn:
            profile.finish_self_intro(
                conn, intro_id, content_md=content,
                title="AI 定制自我介绍", status="ready", error_text=error_text,
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        try:
            with get_db_connection() as conn:
                profile.finish_self_intro(
                    conn, intro_id, content_md="", status="failed",
                    error_text=f"生成失败：{type(exc).__name__}: {str(exc)[:200]}",
                )
                conn.commit()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 2) Résumé render (tech stack + HTML assembly)
# ---------------------------------------------------------------------------
async def run_resume_render_job(resume_id: int, student_id: int) -> None:
    try:
        with get_db_connection() as conn:
            resume = docs.get_resume(conn, student_id, resume_id)
            bundle = profile.collect_profile_bundle(conn, student_id)
            ctx = _student_context(conn, student_id)
            conn.commit()

        wants_tech = any(b.get("type") == "tech_stack" for b in resume.get("layout", {}).get("blocks", []))
        tech_stack = resume.get("tech_stack") or []
        if wants_tech and not tech_stack:
            result = await ai.generate_tech_stack(
                bundle, ctx,
                target_position=str(resume.get("target_position") or (bundle.get("personal") or {}).get("expected_position") or ""),
            )
            tech_stack = result.get("groups") or []
        resume["tech_stack"] = tech_stack

        with get_db_connection() as conn:
            html = render.assemble_resume_html(conn, student_id, resume)
            docs.save_render(conn, resume_id, render_html=html, tech_stack=tech_stack, status="ready")
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        try:
            with get_db_connection() as conn:
                docs.set_status(conn, resume_id, "failed",
                                f"渲染失败：{type(exc).__name__}: {str(exc)[:200]}")
                conn.commit()
        except Exception:
            pass


async def run_resume_optimization_job(resume_id: int, student_id: int) -> None:
    try:
        with get_db_connection() as conn:
            resume = docs.get_resume(conn, student_id, resume_id)
            bundle = profile.collect_profile_bundle(conn, student_id)
            ctx = _student_context(conn, student_id)
            conn.commit()

        result = await ai.optimize_resume_for_target(resume, bundle, ctx)
        resume["target_position"] = result.get("target_position") or resume.get("target_position") or ""
        resume["optimized_summary_md"] = result.get("summary_md") or ""
        resume["tech_stack"] = result.get("tech_stack") or []
        resume["optimization_notes"] = {"items": result.get("notes") or []}

        with get_db_connection() as conn:
            html = render.assemble_resume_html(conn, student_id, resume)
            docs.save_optimization(
                conn, resume_id,
                target_position=resume["target_position"],
                optimized_summary_md=resume["optimized_summary_md"],
                optimization_notes=resume["optimization_notes"],
                render_html=html,
                tech_stack=resume["tech_stack"],
                status="ready",
                error_text=str(result.get("error") or ""),
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        try:
            with get_db_connection() as conn:
                docs.set_status(conn, resume_id, "failed",
                                f"AI 优化失败：{type(exc).__name__}: {str(exc)[:200]}")
                conn.commit()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 3) First-visit education auto-seed
# ---------------------------------------------------------------------------
def _fallback_education(ctx: dict[str, Any]) -> dict[str, Any]:
    timeline = ctx.get("timeline") or {}
    start = str(timeline.get("enrollment_year") or "").strip()
    end = str(timeline.get("graduation_year") or "").strip()
    return {
        "kind": "university",
        "school": "广西外国语学院",
        "college": ctx.get("college") or ctx.get("department") or "",
        "major": ctx.get("major_name") or "",
        "start_date": (start + "-09") if start else "",
        "end_date": (end + "-06") if end else "",
        "content": f"主修{ctx.get('major_name') or '专业'}相关核心课程，系统学习专业基础知识与实践技能。",
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
        "school": str(merged.get("school") or fallback.get("school") or "广西外国语学院")[:120],
        "college": str(merged.get("college") or fallback.get("college") or "")[:120],
        "major": str(merged.get("major") or fallback.get("major") or ctx.get("major_name") or "")[:120],
        "start_date": str(merged.get("start_date") or fallback.get("start_date") or "")[:20],
        "end_date": str(merged.get("end_date") or fallback.get("end_date") or "")[:20],
        "content": str(merged.get("content") or fallback.get("content") or "")[:1000],
        "source": "ai_auto",
    }


async def run_education_seed_job(student_id: int) -> None:
    try:
        with get_db_connection() as conn:
            if profile.has_any_education(conn, student_id):
                return
            ctx = _student_context(conn, student_id)
            conn.commit()
        if not ctx:
            return

        fallback = _fallback_education(ctx)
        seed_payload = _normalize_seed_education(None, fallback=fallback, ctx=ctx)
        if not seed_payload["school"] or not seed_payload["start_date"] or not seed_payload["end_date"]:
            return

        with get_db_connection() as conn:
            if profile.has_any_education(conn, student_id):  # race guard
                conn.commit()
                return
            edu_id = profile.create_education_auto(
                conn, student_id,
                school=seed_payload["school"],
                college=seed_payload["college"],
                major=seed_payload["major"],
                start_date=seed_payload["start_date"],
                end_date=seed_payload["end_date"],
                content=seed_payload["content"],
                kind=seed_payload["kind"],
            )
            conn.commit()

        system = (
            "你是简历填写助手。请根据学生的学校、专业、入学与毕业年份，整理一条规范的大学学习经历，"
            "必须返回 JSON 对象，键：school、college、major、start_date(YYYY-MM)、end_date(YYYY-MM)、content。"
            "content 用一句话概述主修方向与学习重点。只返回 JSON，不要编造不存在的信息。"
        )
        digest = {
            "学校": "广西外国语学院",
            "学院系部": ctx.get("college") or ctx.get("department"),
            "专业": ctx.get("major_name"),
            "班级": ctx.get("class_name"),
            "时间线": ctx.get("timeline"),
        }
        user = "学生学籍信息：\n" + json.dumps(digest, ensure_ascii=False, indent=2)
        data: Any = None
        try:
            data = await ai._chat(
                system, user, want_json=True, capability="thinking",
                task_type="deep_text_reasoning", timeout=30.0, label="resume:edu-seed",
            )
        except (httpx.HTTPError, ValueError):
            data = None
        if not isinstance(data, dict):
            return
        edu = _normalize_seed_education(data, fallback=fallback, ctx=ctx)
        if not edu["school"] or not edu["start_date"] or not edu["end_date"]:
            return

        with get_db_connection() as conn:
            current = profile.get_section_item(conn, student_id, "education", int(edu_id))
            if current.get("updated_at") != current.get("created_at"):
                # The student already edited the seed while AI was thinking.
                conn.commit()
                return
            profile.update_section_item(conn, student_id, "education", int(edu_id), edu)
            conn.commit()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
