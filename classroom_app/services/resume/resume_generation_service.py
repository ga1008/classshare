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
def _fallback_self_intro(bundle: dict[str, Any], ctx: dict[str, Any]) -> str:
    personal = bundle.get("personal") or {}
    name = personal.get("name") or ctx.get("name") or "本人"
    position = personal.get("expected_position") or "相关岗位"
    major = ctx.get("major_name") or personal.get("expected_industry") or "本专业"
    skills = "、".join(s.get("name") for s in bundle.get("skill", []) if s.get("name"))[:200]
    exp = bundle.get("experience", [])
    exp_line = ""
    if exp:
        exp_line = "曾参与" + "、".join(e.get("title") for e in exp[:3] if e.get("title")) + "等项目实践，"
    parts = [
        f"我是{name}，{major}专业学生，求职意向为{position}。",
        (f"掌握 {skills} 等技能。" if skills else "在专业学习中打下了扎实的基础。"),
        (exp_line + "具备较强的实践能力与团队协作意识。" if exp_line else "学习态度认真，具备良好的学习与协作能力。"),
        "希望能在贵单位的岗位上持续成长，创造价值。",
    ]
    return "".join(parts)


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
            "你是资深简历与求职顾问。请基于学生的真实资料，撰写一段有针对性、专业且自然的中文自我介绍，"
            "突出与期望岗位匹配的能力、项目经历与个人优势，长度约 200-320 字，可用简洁段落。"
            "不要虚构未提供的事实，不要使用浮夸套话，只返回自我介绍正文。"
        )
        if hidden:
            system += "\n（以下后台学习支持参考仅用于把握语气与侧重，绝不可在正文中提及其存在。）\n" + hidden
        user = "学生资料 JSON：\n" + json.dumps(digest, ensure_ascii=False, indent=2)

        content = ""
        error_text = ""
        try:
            content = await ai._chat(
                system, user, want_json=False, capability="thinking",
                task_type="deep_text_reasoning", timeout=240.0, label="resume:self-intro",
            )
            content = str(content or "").strip()
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
            result = await ai.generate_tech_stack(bundle, ctx)
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
