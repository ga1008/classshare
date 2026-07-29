"""Generate final-material documents by reversing a concrete exam paper.

This service owns the exam -> assessment-plan / grading-rubric contract:
permission checks, exam scoring extraction, deterministic fallback seeds, AI JSON
constraints, placeholder task persistence, and final material package creation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import sqlite3
import traceback
from datetime import datetime
from typing import Any

import httpx
from fastapi import HTTPException

from ..core import ai_client
from ..db.connection import execute_insert_returning_id, get_configured_db_engine, get_db_connection
from ..db.schema_materials_integrations import ensure_materials_integrations_schema
from . import assessment_plan_service as ap
from .assessment_plan_generation_service import find_teacher_own_signature_id
from .academic_class_mapping_service import resolve_teaching_class_display_name_from_candidates
from .exam_json_service import normalize_exam_scoring_payload
from .file_service import global_file_write_path
from .material_identity_service import build_final_material_package_name
from .material_ai_import_service import (
    MaterialExtraction,
    build_import_readme,
    normalize_ai_parse_result,
    resolve_material_ai_import_type,
)
from .material_final_document_service import (
    ASSESSMENT_PLAN_NOTES,
    SCORING_RUBRIC_NOTES,
    normalize_final_material_payload,
)
from .material_mastery_check_service import build_material_mastery_check_payload
from .materials_git_service import refresh_root_git_metadata
from .materials_service import (
    ensure_teacher_material_owner,
    infer_material_profile,
    make_unique_material_name,
    normalize_material_path,
)
from .organization_scope_service import load_teacher_org_scope
from .resource_access_service import teacher_can_use_exam_paper


_AI_TIMEOUT = 300.0
_AI_RETRY_TIMEOUT = 150.0
_MAX_AI_CONTEXT_CHARS = 48000
_PROCESS_ASSESSMENT_TERMS = (
    "平时",
    "考勤",
    "课堂表现",
    "课堂互动",
    "课堂参与",
    "课后",
    "书面作业",
    "编程作业",
    "作业",
    "阶段性",
    "过程性",
    "出勤",
    "学习态度",
)


def _uses_postgres_metadata(conn: Any) -> bool:
    if isinstance(conn, sqlite3.Connection):
        return False
    try:
        return get_configured_db_engine() == "postgres"
    except Exception:
        return False


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    try:
        if key in row.keys():
            return row[key]
    except Exception:
        pass
    try:
        return row[index]
    except Exception:
        return None


def _run_optional_db(conn: Any, callback: Any, default: Any = None) -> Any:
    """Run schema-drift-prone optional reads without poisoning the caller transaction."""
    savepoint_name = "exam_material_reverse_optional"
    savepoint_active = False
    try:
        conn.execute(f"SAVEPOINT {savepoint_name}")
        savepoint_active = True
    except Exception:
        savepoint_active = False
    try:
        result = callback()
    except Exception:
        if savepoint_active:
            try:
                conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name}")
            finally:
                try:
                    conn.execute(f"RELEASE SAVEPOINT {savepoint_name}")
                except Exception:
                    pass
        return default
    if savepoint_active:
        try:
            conn.execute(f"RELEASE SAVEPOINT {savepoint_name}")
        except Exception:
            return default
    return result


def _optional_fetchone(conn: Any, sql: str, params: tuple[Any, ...]) -> Any | None:
    return _run_optional_db(conn, lambda: conn.execute(sql, params).fetchone(), None)


def _table_columns(conn: Any, table_name: str) -> set[str]:
    def load() -> set[str]:
        if _uses_postgres_metadata(conn):
            rows = conn.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = ? AND table_name = ?
                """,
                ("public", table_name),
            ).fetchall()
            return {
                str(_row_value(row, "column_name", 0) or "")
                for row in rows
                if _row_value(row, "column_name", 0)
            }
        rows = conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
        return {str(_row_value(row, "name", 1) or "") for row in rows if _row_value(row, "name", 1)}

    return _run_optional_db(conn, load, set())


def _select_if_present(
    table_alias: str,
    columns: set[str],
    column_name: str,
    output_alias: str,
    *,
    join_enabled: bool = True,
    fallback: str = "NULL",
) -> str:
    if join_enabled and column_name in columns:
        return f"{table_alias}.{column_name} AS {output_alias}"
    return f"{fallback} AS {output_alias}"


def create_assessment_plan_reverse_placeholder(
    conn: Any,
    *,
    teacher: dict[str, Any],
    paper_id: str,
    prompt: str = "",
) -> dict[str, Any]:
    context = build_exam_reverse_context(conn, paper_id=paper_id, teacher=teacher, require_complete=False)
    fields = dict(context["fields"])
    title_course = fields.get("course_name") or context["paper"]["title"] or "课程"
    plan_id = ap.create_assessment_plan(
        conn,
        teacher=teacher,
        title=f"{title_course}（试卷反推）",
        fields=fields,
        items=context["assessment_items"],
        notes=list(ASSESSMENT_PLAN_NOTES),
        course_id=context.get("course_id"),
        class_offering_id=context.get("class_offering_id"),
        source_type="exam_reverse",
        status="generating",
        tags=["试卷反推"],
        ai_gen_status="pending",
        ai_gen_progress={
            "done": 0,
            "total": 1,
            "current_label": "正在读取试卷并反推考核计划…",
            "source_exam_paper_id": str(paper_id),
        },
    )
    ap.set_generation_status(conn, plan_id, task_id=plan_id)
    plan = ap.get_assessment_plan(conn, plan_id)
    return {
        "plan_id": plan_id,
        "card": ap.serialize_card(plan) if plan else None,
        "redirect_url": "/manage/teaching/assessment-plans",
    }


def create_grading_rubric_reverse_placeholder(
    conn: Any,
    *,
    teacher: dict[str, Any],
    paper_id: str,
    prompt: str = "",
) -> dict[str, Any]:
    if get_configured_db_engine() == "sqlite":
        ensure_materials_integrations_schema(conn)
    type_meta = resolve_material_ai_import_type("final_material", "grading_rubric")
    context = build_exam_reverse_context(conn, paper_id=paper_id, teacher=teacher, require_complete=True)
    now = datetime.now().isoformat()
    metadata = {
        **context["fields"],
        "source_filename": f"{type_meta['label']}-{context['paper']['title']}.json",
        "document_group": type_meta["group_label"],
        "document_type": type_meta["label"],
        "source_exam_paper_id": str(context["paper"]["id"]),
        "source_exam_paper_title": context["paper"]["title"],
        "source_exam_paper_updated_at": context["paper"].get("updated_at") or "",
        "generation_mode": "exam_reverse",
        "teacher_prompt": str(prompt or "").strip(),
    }
    record_id = _insert_running_material_generation_record(
        conn,
        teacher_id=int(teacher["id"]),
        document_group=type_meta["group_key"],
        document_type=type_meta["key"],
        document_type_label=type_meta["label"],
        source_file_name=f"{type_meta['label']}-{context['paper']['title']}.json",
        metadata_json=json.dumps(metadata, ensure_ascii=False),
        now=now,
    )
    return {
        "record_id": record_id,
        "redirect_url": "/manage/teaching/grading-rubrics",
        "source_exam_paper_id": str(paper_id),
    }


async def run_assessment_plan_reverse_job(
    plan_id: str,
    paper_id: str,
    teacher_id: int,
    prompt: str = "",
) -> None:
    try:
        _set_plan_status(
            plan_id,
            status="generating",
            ai_gen_status="running",
            ai_gen_error="",
            progress={"done": 0, "total": 1, "current_label": "正在整理试卷题目与分值…"},
        )
        with get_db_connection() as conn:
            teacher = _load_teacher(conn, teacher_id)
            context = build_exam_reverse_context(conn, paper_id=paper_id, teacher=teacher, require_complete=False)
            own_signature_id = find_teacher_own_signature_id(conn, int(teacher_id))

        fields = dict(context["fields"])
        items = list(context["assessment_items"])
        warnings = list(context.get("warnings") or [])
        ai_used = True
        try:
            _set_plan_status(
                plan_id,
                progress={"done": 0, "total": 1, "current_label": "AI 正在校验考核计划表结构…"},
            )
            raw = await _chat_json(
                _assessment_plan_system_prompt(),
                _assessment_plan_user_prompt(context, prompt),
                task_label="exam-material-reverse:assessment-plan",
            )
            if not raw:
                raise ValueError("AI 未返回有效 JSON")
            ai_fields = raw.get("fields") if isinstance(raw.get("fields"), dict) else {}
            ai_items = raw.get("assessment_items")
            if not isinstance(ai_items, list):
                structured = raw.get("structured") if isinstance(raw.get("structured"), dict) else {}
                ai_items = structured.get("assessment_items") if isinstance(structured.get("assessment_items"), list) else []
            fields = _merge_identity_fields(fields, ai_fields)
            items = _valid_assessment_items_or_seed(ai_items, seed_items=context["assessment_items"], warnings=warnings)
        except Exception as exc:  # noqa: BLE001 - background task must fall back cleanly.
            ai_used = False
            warnings.append(
                f"AI 生成不可用，已使用试卷分值本地反推草稿：{type(exc).__name__}: {str(exc)[:160]}。"
            )

        normalized = ap.normalize_plan_payload(fields, items)
        if not normalized["score_balanced"]:
            warnings.append(f"考核项分值合计为 {normalized['score_total']}，请教师复核。")

        with get_db_connection() as conn:
            ap.update_content(
                conn,
                plan_id,
                fields=normalized["fields"],
                items=normalized["items"],
                notes=list(ASSESSMENT_PLAN_NOTES),
                status="ready",
            )
            course_name = normalized["fields"].get("course_name") or context["paper"]["title"] or "课程"
            ap.update_attributes(conn, plan_id, title=f"{course_name}（试卷反推）")
            if own_signature_id:
                ap.set_signature(conn, plan_id, role="examiner", signature_id=own_signature_id)
            ap.set_generation_status(
                conn,
                plan_id,
                ai_gen_status="completed" if ai_used and not warnings else "completed_with_fallback",
                ai_gen_error="；".join(warnings)[:800],
                progress={"done": 1, "total": 1, "current_label": "完成", "warnings": warnings[-3:]},
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        _set_plan_status(
            plan_id,
            status="failed",
            ai_gen_status="failed",
            ai_gen_error=f"生成失败：{type(exc).__name__}: {str(exc)[:400]}",
        )


async def run_grading_rubric_reverse_job(
    record_id: int,
    paper_id: str,
    teacher_id: int,
    prompt: str = "",
) -> None:
    try:
        _set_material_record_running(record_id, "正在整理试卷评分标准…")
        with get_db_connection() as conn:
            teacher = _load_teacher(conn, teacher_id)
            context = build_exam_reverse_context(conn, paper_id=paper_id, teacher=teacher, require_complete=True)

        ai_used = True
        warnings = list(context.get("warnings") or [])
        try:
            raw = await _chat_json(
                _grading_rubric_system_prompt(),
                _grading_rubric_user_prompt(context, prompt),
                task_label="exam-material-reverse:grading-rubric",
            )
            if not raw:
                raise ValueError("AI 未返回有效 JSON")
            parse_result = _normalize_grading_rubric_result(
                raw,
                context=context,
                source_name=f"课程考核评分细则-{context['paper']['title']}.json",
                ai_used=True,
                warnings=warnings,
                prompt=prompt,
            )
            if not _rubric_result_covers_exam(parse_result.export_payload, context):
                raise ValueError("AI 返回的评分细则未覆盖全部题目或分值不为 100")
        except Exception as exc:  # noqa: BLE001 - local rubric is complete and source-bound.
            ai_used = False
            warnings.append(
                f"AI 生成不可用或未通过结构校验，已使用试卷评分标准本地反推：{type(exc).__name__}: {str(exc)[:160]}。"
            )
            parse_result = _normalize_grading_rubric_result(
                _grading_rubric_seed_result(context, prompt=prompt, warnings=warnings),
                context=context,
                source_name=f"课程考核评分细则-{context['paper']['title']}.json",
                ai_used=False,
                warnings=warnings,
                prompt=prompt,
            )
        parse_result.ai_used = ai_used
        parse_result.parsed_payload["ai_used"] = ai_used
        await _persist_generated_rubric_record(record_id, parse_result, teacher_id=int(teacher_id))
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        _mark_material_record_failed(record_id, f"评分细则表生成失败：{type(exc).__name__}: {str(exc)[:400]}")


def build_exam_reverse_context(
    conn: Any,
    *,
    paper_id: str,
    teacher: dict[str, Any],
    require_complete: bool,
) -> dict[str, Any]:
    paper = _get_exam_paper_for_teacher(conn, paper_id, int(teacher["id"]))
    try:
        raw_questions = json.loads(paper.get("questions_json") or "{}")
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(400, "试卷题目 JSON 不合法，无法反推材料。") from exc
    try:
        normalized_questions = normalize_exam_scoring_payload(raw_questions, require_complete=require_complete)
    except ValueError as exc:
        message = "评分细则表要求试卷已补齐每题标准答案、分值、评分指导和扣分点。"
        if require_complete:
            raise HTTPException(400, f"{message}{exc}") from exc
        raise HTTPException(400, str(exc)) from exc

    questions = _extract_exam_questions(normalized_questions)
    if not questions:
        raise HTTPException(400, "试卷中没有可用于反推的题目。")
    original_total = _exam_total_score(normalized_questions, questions)
    if original_total <= 0:
        raise HTTPException(400, "试卷缺少有效分值，无法反推出规范表单。")
    scaled_scores = _allocate_scores([item["raw_points"] for item in questions], total=100)
    for question, scaled_score in zip(questions, scaled_scores):
        question["score"] = scaled_score
        question["score_text"] = _score_text(scaled_score)

    assignment_context = _latest_assignment_context(conn, str(paper["id"]), int(teacher["id"]))
    fields = _build_reverse_fields(
        conn,
        paper=paper,
        teacher=teacher,
        questions=questions,
        assignment_context=assignment_context,
    )
    warnings: list[str] = []
    if not math.isclose(float(original_total), 100.0, abs_tol=0.01):
        warnings.append(f"来源试卷总分为 {_score_text(original_total)}，已按比例换算为 100 分表单。")

    paper_sections = _paper_sections_from_questions(questions)
    rubric_items = _rubric_items_from_questions(questions)
    assessment_items = _assessment_items_from_exam_groups(questions, fields)
    source_exam = {
        "id": str(paper["id"]),
        "record_id": str(paper["id"]),
        "title": paper.get("title") or "",
        "updated_at": paper.get("updated_at") or "",
        "total_score": _score_text(original_total),
        "scaled_total_score": "100",
        "question_count": len(questions),
        "structured": {
            "paper_sections": paper_sections,
            "rubric_items": rubric_items,
            "assessment_items": assessment_items,
        },
        "paper_sections": paper_sections,
        "rubric_items": rubric_items,
        "assessment_items": assessment_items,
    }
    final_context = {
        **fields,
        "source_exam_paper": source_exam,
        "generation_warnings": warnings,
    }
    if assignment_context:
        final_context.update(
            {
                "class_offering_id": assignment_context.get("class_offering_id"),
                "course_id": assignment_context.get("course_id"),
                "assignment_id": assignment_context.get("assignment_id"),
            }
        )

    return {
        "paper": paper,
        "questions": questions,
        "fields": fields,
        "assessment_items": assessment_items,
        "rubric_items": rubric_items,
        "paper_sections": paper_sections,
        "source_exam_paper": source_exam,
        "classroom_context": final_context,
        "warnings": warnings,
        "original_total_score": original_total,
        "course_id": _optional_int(assignment_context.get("course_id") if assignment_context else None),
        "class_offering_id": _optional_int(assignment_context.get("class_offering_id") if assignment_context else None),
    }


def _get_exam_paper_for_teacher(conn: Any, paper_id: str, teacher_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM exam_papers WHERE id = ?", (str(paper_id),)).fetchone()
    if not row:
        raise HTTPException(404, "试卷不存在或无权访问")
    paper = dict(row)
    blocked = _optional_fetchone(
        conn,
        "SELECT 1 FROM learning_stage_exam_attempts WHERE exam_paper_id = ? LIMIT 1",
        (str(paper_id),),
    )
    if blocked:
        raise HTTPException(404, "阶段考试试卷不能用于材料反推")
    if not teacher_can_use_exam_paper(conn, int(teacher_id), paper):
        raise HTTPException(404, "试卷不存在或无权访问")
    if str(paper.get("ai_gen_status") or "").strip().lower() in {"pending", "running"}:
        raise HTTPException(409, "试卷仍在生成中，请生成完成后再反推材料。")
    return paper


def _extract_exam_questions(exam_data: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    ordinal = 1
    for page_index, page in enumerate(exam_data.get("pages") or [], start=1):
        if not isinstance(page, dict):
            continue
        page_name = _text(page.get("name") or f"第{page_index}部分")
        for question_index, question in enumerate(page.get("questions") or [], start=1):
            if not isinstance(question, dict):
                continue
            raw_points = _score_number(
                _first_non_blank(
                    _as_dict(question.get("grading")).get("points"),
                    question.get("points"),
                    question.get("score"),
                    question.get("max_score"),
                )
            )
            if raw_points <= 0:
                raw_points = 1.0
            answer_text = _answer_to_text(question.get("answer"))
            attachment = _as_dict(question.get("attachment_requirements"))
            result.append(
                {
                    "ordinal": ordinal,
                    "page_index": page_index,
                    "page_name": page_name,
                    "question_index": question_index,
                    "id": _text(question.get("id") or f"p{page_index}_q{question_index}"),
                    "type": _text(question.get("type")),
                    "type_label": _question_type_label(question.get("type")),
                    "text": _text(question.get("text") or question.get("question") or question.get("title")),
                    "options": [str(item).strip() for item in (question.get("options") or []) if str(item).strip()]
                    if isinstance(question.get("options"), list)
                    else [],
                    "answer": answer_text,
                    "guidance": _first_text_multi(
                        _as_dict(question.get("grading")),
                        question,
                        keys=("guidance", "grading_guidance", "scoring_guidance", "criteria", "score_points"),
                    ),
                    "deduction_points": _first_text_multi(
                        _as_dict(question.get("grading")),
                        question,
                        keys=("deduction_points", "deductions", "loss_points", "mistakes"),
                    ),
                    "attachment_requirements": attachment,
                    "raw_points": raw_points,
                }
            )
            ordinal += 1
    return result


def _exam_total_score(exam_data: dict[str, Any], questions: list[dict[str, Any]]) -> float:
    grading = exam_data.get("grading") if isinstance(exam_data.get("grading"), dict) else {}
    total = _score_number(
        _first_non_blank(
            grading.get("total_score"),
            exam_data.get("total_score"),
            exam_data.get("total_points"),
            exam_data.get("score"),
        )
    )
    if total > 0:
        return total
    return sum(_score_number(item.get("raw_points")) for item in questions)


def _latest_assignment_context(conn: Any, paper_id: str, teacher_id: int) -> dict[str, Any]:
    assignment_cols = _table_columns(conn, "assignments")
    if "exam_paper_id" not in assignment_cols:
        return {}
    offering_cols = _table_columns(conn, "class_offerings")
    course_cols = _table_columns(conn, "courses")
    class_cols = _table_columns(conn, "classes")

    join_offering = "class_offering_id" in assignment_cols and "id" in offering_cols
    offering_course_expr = "o.course_id" if join_offering and "course_id" in offering_cols else ""
    assignment_course_expr = "a.course_id" if "course_id" in assignment_cols else ""
    course_link_exprs = [expr for expr in (offering_course_expr, assignment_course_expr) if expr]
    if len(course_link_exprs) > 1:
        course_link_sql = f"COALESCE({', '.join(course_link_exprs)})"
    else:
        course_link_sql = course_link_exprs[0] if course_link_exprs else ""
    join_course = "id" in course_cols and bool(course_link_sql)
    join_class = join_offering and "class_id" in offering_cols and "id" in class_cols
    updated_expr = (
        "a.updated_at"
        if "updated_at" in assignment_cols
        else ("a.created_at" if "created_at" in assignment_cols else "NULL")
    )

    select_parts = [
        _select_if_present("a", assignment_cols, "id", "assignment_id"),
        _select_if_present("a", assignment_cols, "course_id", "assignment_course_id"),
        _select_if_present("a", assignment_cols, "class_offering_id", "class_offering_id"),
        _select_if_present("a", assignment_cols, "title", "assignment_title"),
        f"{updated_expr} AS assignment_updated_at",
        _select_if_present("o", offering_cols, "course_id", "offering_course_id", join_enabled=join_offering),
        _select_if_present("o", offering_cols, "semester", "offering_semester", join_enabled=join_offering),
        _select_if_present(
            "o",
            offering_cols,
            "academic_teaching_class_name",
            "academic_teaching_class_name",
            join_enabled=join_offering,
        ),
        _select_if_present("c", course_cols, "id", "course_id", join_enabled=join_course),
        _select_if_present("c", course_cols, "name", "course_name", join_enabled=join_course),
        _select_if_present("c", course_cols, "academic_course_code", "academic_course_code", join_enabled=join_course),
        _select_if_present("c", course_cols, "school_name", "course_school_name", join_enabled=join_course),
        _select_if_present("c", course_cols, "college", "course_college", join_enabled=join_course),
        _select_if_present("c", course_cols, "department", "course_department", join_enabled=join_course),
        _select_if_present("cl", class_cols, "name", "class_name", join_enabled=join_class),
        _select_if_present("cl", class_cols, "academic_class_name", "academic_class_name", join_enabled=join_class),
        _select_if_present("cl", class_cols, "academic_major", "academic_major", join_enabled=join_class),
        _select_if_present("cl", class_cols, "major", "major", join_enabled=join_class),
        _select_if_present("cl", class_cols, "department", "class_department", join_enabled=join_class),
    ]
    joins: list[str] = []
    if join_offering:
        joins.append("LEFT JOIN class_offerings o ON o.id = a.class_offering_id")
    if join_course:
        joins.append(f"LEFT JOIN courses c ON c.id = {course_link_sql}")
    if join_class:
        joins.append("LEFT JOIN classes cl ON cl.id = o.class_id")

    where_parts = ["a.exam_paper_id = ?"]
    params: list[Any] = [str(paper_id)]
    if join_offering and "teacher_id" in offering_cols:
        where_parts.append("(o.teacher_id = ? OR o.teacher_id IS NULL)")
        params.append(int(teacher_id))

    order_parts = [f"a.{column} DESC" for column in ("updated_at", "created_at", "id") if column in assignment_cols]
    order_sql = f"ORDER BY {', '.join(order_parts)}" if order_parts else ""
    row = _optional_fetchone(
        conn,
        f"""
        SELECT {', '.join(select_parts)}
        FROM assignments a
        {' '.join(joins)}
        WHERE {' AND '.join(where_parts)}
        {order_sql}
        LIMIT 1
        """,
        tuple(params),
    )
    if not row:
        return {}
    data = dict(row)
    data["course_id"] = data.get("course_id") or data.get("offering_course_id") or data.get("assignment_course_id")
    return data


def _build_reverse_fields(
    conn: Any,
    *,
    paper: dict[str, Any],
    teacher: dict[str, Any],
    questions: list[dict[str, Any]],
    assignment_context: dict[str, Any],
) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    offering_id = _optional_int(assignment_context.get("class_offering_id"))
    if offering_id:
        offering_fields = _run_optional_db(
            conn,
            lambda: ap.build_fields_from_offering(conn, offering_id, teacher=teacher),
            {},
        )
        if isinstance(offering_fields, dict):
            fields.update(offering_fields)
    org = load_teacher_org_scope(conn, int(teacher["id"]))
    teacher_name = _text(teacher.get("name") or teacher.get("username") or teacher.get("email"))
    course_name = _text(fields.get("course_name") or assignment_context.get("course_name"))
    if not course_name:
        course_name = _infer_course_name(paper.get("title") or "")
    method = _infer_assessment_method(paper, questions)
    fields.update(
        {
            "school": _text(fields.get("school") or assignment_context.get("course_school_name") or org.get("school_name"))
            or "广西外国语学院",
            "college": _text(fields.get("college") or assignment_context.get("course_college") or org.get("college")),
            "department": _text(fields.get("department") or assignment_context.get("course_department") or org.get("department")),
            "course_name": course_name or _text(paper.get("title")) or "课程",
            "class_name": _text(
                fields.get("class_name")
                or _class_label_from_assignment(conn, assignment_context, teacher_id=int(teacher["id"]))
            ),
            "teacher_name": _text(fields.get("teacher_name") or teacher_name),
            "examiner_name": _text(fields.get("examiner_name") or teacher_name),
            "reviewer_name": _text(fields.get("reviewer_name")),
            "date": _text(fields.get("date")) or datetime.now().strftime("%Y年%m月%d日"),
            "assessment_type": _normalize_assessment_type(fields.get("assessment_type")),
            "assessment_method": method,
            "assessment_mode": "written" if "笔试" in method else "non_written",
            "assessment_mode_label": "笔试考核" if "笔试" in method else "非笔试考核",
            "total_score": "100",
            "source_exam_paper_record_id": str(paper.get("id") or ""),
            "source_exam_paper_title": _text(paper.get("title")),
            "source_exam_paper_updated_at": _text(paper.get("updated_at")),
        }
    )
    if not fields.get("academic_year") or not fields.get("semester"):
        academic_year, semester = _academic_period_from_semester(assignment_context.get("offering_semester"))
        if not fields.get("academic_year"):
            fields["academic_year"] = academic_year
        if not fields.get("semester"):
            fields["semester"] = semester
    return fields


def _class_label_from_assignment(conn: Any, row: dict[str, Any], *, teacher_id: int) -> str:
    return resolve_teaching_class_display_name_from_candidates(
        conn,
        teacher_id=int(teacher_id),
        teaching_class_names=[
            row.get("academic_teaching_class_name"),
            row.get("academic_class_name"),
            row.get("class_name"),
        ],
        course_code=_text(row.get("academic_course_code")),
        default=_text(row.get("academic_class_name") or row.get("class_name") or row.get("academic_teaching_class_name")),
    )


def _academic_period_from_semester(value: Any) -> tuple[str, str]:
    text = _text(value)
    year = ""
    semester = ""
    match = re.search(r"(20\d{2})\s*[-—－]\s*(20\d{2})", text)
    if match:
        year = f"{match.group(1)}-{match.group(2)}"
    if re.search(r"第一|(?:^|[-_\s])1(?:$|[-_\s])", text):
        semester = "第一学期"
    elif re.search(r"第二|(?:^|[-_\s])2(?:$|[-_\s])", text):
        semester = "第二学期"
    elif text:
        semester = text
    return year, semester


def _infer_course_name(title: str) -> str:
    text = _text(title)
    if not text:
        return ""
    head = re.split(r"[-—－:：]", text, maxsplit=1)[0].strip()
    if len(head) >= 2:
        text = head
    text = re.sub(r"(课程)?(期末)?(综合)?(考核)?(试卷|实验报告|复习)\d*$", "", text).strip(" -—－_")
    return text or _text(title)


def _infer_assessment_method(paper: dict[str, Any], questions: list[dict[str, Any]]) -> str:
    source = " ".join(
        [
            _text(paper.get("title")),
            _text(paper.get("description")),
            " ".join(_text(item.get("text")) for item in questions[:30]),
        ]
    )
    if re.search(r"机试|实验|实训|配置|命令|部署|截图|拓扑|网络|附件|提交|报告|项目|操作", source, re.IGNORECASE):
        return "机试"
    if any(str(item.get("type") or "") == "textarea" for item in questions):
        return "机试"
    return "闭卷笔试"


def _normalize_assessment_type(value: Any) -> str:
    text = _text(value)
    if "考查" in text:
        return "考查"
    return "考试"


def _paper_sections_from_questions(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for question in questions:
        title = f"第{question['ordinal']}题：{_clip_inline(question.get('text'), 36)}"
        content_lines = [
            f"题型：{question.get('type_label') or '题目'}",
            f"题干：{_text(question.get('text'))}",
        ]
        if question.get("options"):
            content_lines.append("选项：" + "；".join(question["options"]))
        if question.get("answer"):
            content_lines.append(f"标准答案：{question['answer']}")
        if question.get("guidance"):
            content_lines.append(f"得分点：{question['guidance']}")
        if question.get("deduction_points"):
            content_lines.append(f"扣分点：{question['deduction_points']}")
        attachment = _attachment_text(question.get("attachment_requirements"))
        if attachment:
            content_lines.append(f"附件/提交要求：{attachment}")
        sections.append(
            {
                "title": title,
                "score": _score_text(question["score"]),
                "content": "\n".join(line for line in content_lines if line.strip()),
                "tasks": [_text(question.get("text"))],
                "screenshot_requirements": _extract_keywords(content_lines, ("截图", ".png", ".jpg", ".jpeg", ".webp")),
                "submission_requirements": _extract_keywords(content_lines, ("提交", "附件", "压缩", "命名", "上传")),
            }
        )
    return sections


def _rubric_items_from_questions(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for question in questions:
        title = f"第{question['ordinal']}题：{_clip_inline(question.get('text'), 34)}"
        score = _score_text(question["score"])
        criteria_text = "；".join(
            part
            for part in (
                f"题型为{question.get('type_label') or '题目'}，满分 {score} 分",
                f"标准答案/参考结果：{question.get('answer') or '以题目要求和教师标准答案为准'}",
                f"得分点：{question.get('guidance') or '答案与参考结论一致，过程或证据满足题目要求'}",
                f"扣分点：{question.get('deduction_points') or '结论错误、关键步骤遗漏、证据不足或未按要求提交时按比例扣分'}",
                f"附件/提交要求：{_attachment_text(question.get('attachment_requirements'))}"
                if _attachment_text(question.get("attachment_requirements"))
                else "",
            )
            if part
        )
        items.append(
            {
                "title": title,
                "score": score,
                "criteria": [{"score": score, "text": criteria_text}],
            }
        )
    return items


def _assessment_items_from_exam_groups(questions: list[dict[str, Any]], fields: dict[str, Any]) -> list[dict[str, str]]:
    method = _text(fields.get("assessment_method")) or "机试"
    groups: list[dict[str, Any]] = []
    pages: dict[int, dict[str, Any]] = {}
    for question in questions:
        page = pages.setdefault(
            int(question.get("page_index") or 0),
            {"title": question.get("page_name") or "试卷题目", "questions": [], "score": 0.0},
        )
        page["questions"].append(question)
        page["score"] += float(question.get("score") or 0)
    page_groups = [page for page in pages.values() if page["questions"]]
    if 3 <= len(page_groups) <= 6:
        groups = page_groups
    elif 3 <= len(questions) <= 6:
        groups = [
            {"title": f"第{item['ordinal']}题", "questions": [item], "score": float(item.get("score") or 0)}
            for item in questions
        ]
    elif len(questions) >= 3:
        target_count = min(6, max(3, round(len(questions) / 4) or 3))
        groups = _chunk_question_groups(questions, target_count)
    else:
        base_text = _summarize_question_topics(questions)
        return [
            {"assessment_form": method, "content": f"{base_text}：题意理解、方案设计与关键知识点判断", "score": "25"},
            {"assessment_form": method, "content": f"{base_text}：核心操作、计算、配置或论述任务完成", "score": "55"},
            {"assessment_form": method, "content": f"{base_text}：结果验证、证据截图、提交规范与异常分析", "score": "20"},
        ]

    scores = _allocate_scores([group["score"] for group in groups], total=100)
    items: list[dict[str, str]] = []
    for group, score in zip(groups, scores):
        topics = _summarize_question_topics(group["questions"])
        title = _text(group.get("title")) or "综合考核"
        content = f"{title}：{topics}"
        items.append({"assessment_form": method, "content": content, "score": _score_text(score)})
    return items


def _chunk_question_groups(questions: list[dict[str, Any]], target_count: int) -> list[dict[str, Any]]:
    chunks: list[list[dict[str, Any]]] = [[] for _ in range(target_count)]
    for index, question in enumerate(questions):
        chunks[index * target_count // max(1, len(questions))].append(question)
    groups: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks, start=1):
        if not chunk:
            continue
        groups.append(
            {
                "title": f"第{index}部分",
                "questions": chunk,
                "score": sum(float(item.get("score") or 0) for item in chunk),
            }
        )
    return groups


def _summarize_question_topics(questions: list[dict[str, Any]]) -> str:
    snippets = [_clip_inline(item.get("text"), 34) for item in questions if _text(item.get("text"))]
    if not snippets:
        return "围绕来源试卷的综合任务"
    return "；".join(snippets[:5]) + ("等" if len(snippets) > 5 else "")


def _assessment_plan_system_prompt() -> str:
    return (
        "你是广西外国语学院《课程考核计划表》模板助手。必须严格返回 JSON 对象，不要 Markdown。"
        "JSON 只能包含 fields、assessment_items、warnings。"
        "fields 使用给定身份字段，不能改写课程名称、班级、教师、学年学期和来源试卷。"
        "assessment_items 必须为 3-6 项，每项包含 assessment_form、content、score，score 合计严格等于 100。"
        "考核项只描述期末考试/期末考核本身的考核形式、技能/内容和分值；严禁出现平时成绩、考勤、课堂表现、作业、阶段性实验、过程性成绩。"
        "content 必须来自来源试卷题目范围，不能生成与试卷无关的课程内容。"
    )


def _assessment_plan_user_prompt(context: dict[str, Any], prompt: str) -> str:
    ai_context = {
        "fields": context.get("fields"),
        "local_seed_assessment_items": context.get("assessment_items"),
        "source_exam_paper": _compact_source_exam_for_ai(context),
        "warnings": context.get("warnings") or [],
    }
    return "\n\n".join(
        [
            "请根据来源试卷反推出《课程考核计划表》的考核信息。固定 notes 不需要返回。",
            "本地已按题目分值生成一个可用草稿；你可以在不改变总分和期末考试语义的前提下优化考核项目表述。",
            f"试卷上下文 JSON：\n{_limited_json(ai_context)}",
            f"教师额外提示：\n{_text(prompt) or '无'}",
        ]
    )


def _grading_rubric_system_prompt() -> str:
    return (
        "你是广西外国语学院《课程考核评分细则》模板助手。必须严格返回 JSON 对象，不要 Markdown 代码块。"
        "JSON 必须包含 metadata、content_markdown、export_payload、warnings。"
        "export_payload.document_group='final_material'，document_type='grading_rubric'，template_key='grading_rubric'。"
        "export_payload.fields 必须保留给定课程、班级、教师、学年学期、总分和来源试卷字段。"
        "export_payload.structured.rubric_items 必须逐题覆盖来源试卷全部题目，分值合计严格等于 100。"
        "每个 rubric_item 包含 title、score、criteria；criteria 必须写清标准答案/参考结果、得分点、扣分点、附件或截图要求。"
        "不得生成与来源试卷无关的评分项目，不得遗漏题目。"
    )


def _grading_rubric_user_prompt(context: dict[str, Any], prompt: str) -> str:
    seed = _grading_rubric_seed_result(context, prompt=prompt, warnings=[])
    ai_context = {
        "fields": context.get("fields"),
        "source_exam_paper": _compact_source_exam_for_ai(context),
        "local_seed": seed,
    }
    return "\n\n".join(
        [
            "请根据来源试卷生成《课程考核评分细则》结构化 JSON。必须逐题覆盖，不得合并漏项。",
            "请优先沿用 local_seed 的结构和分值；可根据教师提示优化 criteria 表述。",
            f"上下文 JSON：\n{_limited_json(ai_context)}",
            f"教师额外提示：\n{_text(prompt) or '无'}",
        ]
    )


async def _chat_json(system_prompt: str, user_message: str, *, task_label: str) -> dict[str, Any] | None:
    payload = {
        "system_prompt": system_prompt,
        "messages": [],
        "new_message": user_message,
        "file_texts": [],
        "model_capability": "thinking",
        "task_type": "deep_text_reasoning",
        "response_format": "json",
        "web_search_enabled": False,
        "task_priority": "background",
        "task_label": task_label,
    }
    try:
        response = await ai_client.post("/api/ai/chat", json=payload, timeout=_AI_TIMEOUT)
        response.raise_for_status()
    except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.TransportError):
        retry = {
            **payload,
            "model_capability": "standard",
            "task_type": "fast_text_response",
            "task_label": f"{task_label}:standard-retry",
        }
        response = await ai_client.post("/api/ai/chat", json=retry, timeout=_AI_RETRY_TIMEOUT)
        response.raise_for_status()
    return _json_from_payload(response.json())


def _json_from_payload(data: Any) -> dict[str, Any] | None:
    if isinstance(data, dict):
        for key in ("response_json", "json", "data"):
            parsed = _loads_json_object(data.get(key))
            if parsed:
                return parsed
        return _loads_json_object(data.get("response_text")) or data
    return _loads_json_object(data)


def _loads_json_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    raw = str(value or "").strip()
    if not raw:
        return None
    fence = re.search(r"```(?:json)?\s*(.+?)```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1).strip()
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except (TypeError, json.JSONDecodeError):
        pass
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", raw):
        try:
            parsed, _ = decoder.raw_decode(raw[match.start():])
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _valid_assessment_items_or_seed(
    raw_items: Any,
    *,
    seed_items: list[dict[str, Any]],
    warnings: list[str],
) -> list[dict[str, Any]]:
    items = [dict(item) for item in raw_items if isinstance(item, dict)] if isinstance(raw_items, list) else []
    filtered = [item for item in items if not _looks_like_process_assessment(item)]
    if len(filtered) != len(items):
        warnings.append("已移除 AI 误写入的平时/过程性成绩项。")
    if 3 <= len(filtered) <= 6 and math.isclose(_score_total(filtered), 100.0, abs_tol=0.01):
        return filtered
    if items:
        warnings.append("AI 生成的考核项目数量或分值不符合模板要求，已改用本地试卷反推草稿。")
    return seed_items


def _looks_like_process_assessment(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    text = " ".join(_text(item.get(key)) for key in ("assessment_form", "form", "content", "assessment_content"))
    return any(term in text for term in _PROCESS_ASSESSMENT_TERMS)


def _score_total(items: list[Any]) -> float:
    total = 0.0
    for item in items:
        if isinstance(item, dict):
            total += _score_number(item.get("score"))
    return total


def _merge_identity_fields(base: dict[str, Any], ai_fields: dict[str, Any]) -> dict[str, Any]:
    merged = dict(ai_fields or {})
    for key, value in (base or {}).items():
        if _text(value):
            merged[key] = value
    merged["total_score"] = "100"
    return merged


def _grading_rubric_seed_result(context: dict[str, Any], *, prompt: str, warnings: list[str]) -> dict[str, Any]:
    fields = dict(context.get("fields") or {})
    fields["total_score"] = "100"
    if prompt.strip():
        warnings = [*warnings, f"教师补充要求：{prompt.strip()}"]
    content_markdown = _grading_rubric_markdown(fields, context.get("rubric_items") or [], context.get("paper") or {}, prompt)
    return {
        "metadata": fields,
        "content_markdown": content_markdown,
        "tables": [],
        "warnings": warnings,
        "export_payload": {
            "document_group": "final_material",
            "document_type": "grading_rubric",
            "document_type_label": "课程考核评分细则",
            "template_key": "grading_rubric",
            "fields": fields,
            "structured": {
                "rubric_items": context.get("rubric_items") or [],
                "notes": list(SCORING_RUBRIC_NOTES),
                "source_exam_paper": context.get("source_exam_paper") or {},
            },
        },
    }


def _normalize_grading_rubric_result(
    raw_result: dict[str, Any],
    *,
    context: dict[str, Any],
    source_name: str,
    ai_used: bool,
    warnings: list[str],
    prompt: str,
):
    type_meta = resolve_material_ai_import_type("final_material", "grading_rubric")
    extraction = MaterialExtraction(
        text=str(raw_result.get("content_markdown") or raw_result.get("content") or ""),
        method="exam_reverse_ai_generate" if ai_used else "exam_reverse_local_seed",
        source_kind="ai_generated" if ai_used else "local_generated",
        warnings=[],
        quality={"usable": True},
    )
    parse_result = normalize_ai_parse_result(
        raw_result,
        original_name=source_name,
        type_meta=type_meta,
        extraction=extraction,
        extra_warnings=warnings,
        ai_used=ai_used,
    )
    fields = _merge_identity_fields(context.get("fields") or {}, parse_result.metadata)
    parse_result.export_payload = normalize_final_material_payload(
        document_type="grading_rubric",
        metadata=fields,
        content_markdown=parse_result.content_markdown,
        tables=parse_result.tables,
        export_payload={
            **(parse_result.export_payload or {}),
            "fields": fields,
            "structured": {
                **_as_dict((parse_result.export_payload or {}).get("structured")),
                "source_exam_paper": context.get("source_exam_paper") or {},
            },
        },
        classroom_context=context.get("classroom_context") or {},
    )
    parse_result.metadata.update(parse_result.export_payload.get("fields") or {})
    structured = _as_dict(parse_result.export_payload.get("structured"))
    rubric_items = structured.get("rubric_items") if isinstance(structured.get("rubric_items"), list) else []
    if not rubric_items:
        rubric_items = context.get("rubric_items") or []
    parse_result.content_markdown = _grading_rubric_markdown(
        parse_result.metadata,
        rubric_items,
        context.get("paper") or {},
        prompt,
    )
    parse_result.content_quality = {
        **(parse_result.content_quality or {}),
        "status": "ok",
        "usable": True,
        "method": "exam_reverse_structured_markdown",
        "length": len(parse_result.content_markdown),
    }
    parse_result.parsed_payload["metadata"] = parse_result.metadata
    parse_result.parsed_payload["content_markdown"] = parse_result.content_markdown
    parse_result.parsed_payload["export_payload"] = parse_result.export_payload
    parse_result.parsed_payload["warnings"] = parse_result.warnings
    parse_result.parsed_payload["content_quality"] = parse_result.content_quality
    return parse_result


def _rubric_result_covers_exam(export_payload: dict[str, Any], context: dict[str, Any]) -> bool:
    structured = _as_dict(export_payload.get("structured"))
    items = structured.get("rubric_items") if isinstance(structured.get("rubric_items"), list) else []
    question_count = len(context.get("questions") or [])
    if len(items) < question_count:
        return False
    if not math.isclose(_score_total(items), 100.0, abs_tol=0.01):
        return False
    for item in items[:question_count]:
        if not _text(item.get("title")) or not isinstance(item.get("criteria"), list) or not item["criteria"]:
            return False
    return True


def _grading_rubric_markdown(
    fields: dict[str, Any],
    rubric_items: list[dict[str, Any]],
    paper: dict[str, Any],
    prompt: str,
) -> str:
    lines = [
        "## 基础信息",
        f"- 课程名称：{fields.get('course_name') or ''}",
        f"- 专业年级班级：{fields.get('class_name') or ''}",
        f"- 命题教师：{fields.get('examiner_name') or fields.get('teacher_name') or ''}",
        f"- 来源试卷：{paper.get('title') or fields.get('source_exam_paper_title') or ''}",
        "- 总分：100",
    ]
    if prompt.strip():
        lines.append(f"- 教师补充要求：{prompt.strip()}")
    lines.extend(["", "## 评分细则"])
    for item in rubric_items:
        lines.extend(["", f"### {item.get('title') or '评分项目'}（{item.get('score') or ''}分）"])
        for criterion in item.get("criteria") or []:
            if not isinstance(criterion, dict):
                continue
            score = _text(criterion.get("score"))
            text = _text(criterion.get("text"))
            lines.append(f"- 【{score or item.get('score') or ''}分】{text}")
    lines.extend(["", "## 注", *SCORING_RUBRIC_NOTES])
    return "\n".join(lines).strip()


def _insert_running_material_generation_record(
    conn: Any,
    *,
    teacher_id: int,
    document_group: str,
    document_type: str,
    document_type_label: str,
    source_file_name: str,
    metadata_json: str,
    now: str,
) -> int:
    db_engine = get_configured_db_engine()
    insert_sql = """
        INSERT INTO material_ai_import_records
        (teacher_id, package_material_id, source_material_id, parsed_material_id,
         parent_material_id, document_group, document_type, document_type_label,
         parse_status, parse_mode, extraction_method, source_file_name,
         source_file_hash, source_file_size, source_mime_type, metadata_json, content_markdown,
         parsed_payload_json, export_payload_json, warnings_json, content_quality_status,
         content_quality_json, error_message, created_at, started_at, updated_at, completed_at, failed_at)
        VALUES (?, NULL, NULL, NULL, NULL, ?, ?, ?, 'running', 'ai_generated', 'exam_reverse',
                ?, '', 0, 'application/json', ?, '',
                NULL, NULL, '[]', 'unchecked', '{}', '', ?, ?, ?, NULL, NULL)
    """
    return execute_insert_returning_id(
        conn,
        insert_sql,
        (
            int(teacher_id),
            document_group,
            document_type,
            document_type_label,
            source_file_name,
            metadata_json,
            now,
            now,
            now,
        ),
        engine=db_engine,
    )


def _reverse_package_base_name(parse_result, course_name: str) -> str:
    """由试卷反推生成的材料同样要带上班级与学年学期，否则平行教学班无法区分。"""
    export_payload = parse_result.export_payload if isinstance(parse_result.export_payload, dict) else {}
    fields = export_payload.get("fields") if isinstance(export_payload.get("fields"), dict) else {}
    metadata = parse_result.metadata if isinstance(parse_result.metadata, dict) else {}
    merged: dict[str, Any] = {}
    for key in ("academic_year", "semester", "period", "course_name", "class_name"):
        value = fields.get(key) or metadata.get(key)
        if _text(value):
            merged[key] = value
    if course_name and not _text(merged.get("course_name")):
        merged["course_name"] = course_name
    return build_final_material_package_name(
        document_type_label=parse_result.document_type_label,
        fields=merged,
    )


async def _persist_generated_rubric_record(record_id: int, parse_result, *, teacher_id: int) -> None:
    readme_content = build_import_readme(result=parse_result, original_name=f"{parse_result.document_type_label}.md")
    readme_bytes = readme_content.encode("utf-8")
    readme_hash = hashlib.sha256(readme_bytes).hexdigest()
    await _write_material_file(readme_hash, readme_bytes)

    readme_profile = infer_material_profile("readme.md", "text/markdown")
    parse_payload_json = json.dumps(parse_result.parsed_payload, ensure_ascii=False)
    metadata_json = json.dumps(parse_result.metadata, ensure_ascii=False)
    export_payload_json = json.dumps(parse_result.export_payload, ensure_ascii=False)
    warnings_json = json.dumps(parse_result.warnings, ensure_ascii=False)
    content_quality_json = json.dumps(parse_result.content_quality, ensure_ascii=False)

    with get_db_connection() as conn:
        if get_configured_db_engine() == "sqlite":
            ensure_materials_integrations_schema(conn)
        record = conn.execute(
            "SELECT * FROM material_ai_import_records WHERE id = ? AND teacher_id = ?",
            (int(record_id), int(teacher_id)),
        ).fetchone()
        if not record:
            return
        status = str(record["parse_status"] or "").lower()
        if status not in {"queued", "running"}:
            return
        user = {"id": int(teacher_id), "role": "teacher"}
        owner_scope = load_teacher_org_scope(conn, int(teacher_id))
        now = datetime.now().isoformat()
        course_name = _text(parse_result.metadata.get("course_name"))
        package_base_name = _reverse_package_base_name(parse_result, course_name)
        package_name = make_unique_material_name(conn, int(teacher_id), None, package_base_name)
        package_path = normalize_material_path(package_name)
        package_id, package_root_id = _insert_material_folder_row(
            conn,
            user=user,
            name=package_name,
            material_path=package_path,
            parent_id=None,
            inherited_root_id=None,
            owner_scope=owner_scope,
            now=now,
        )
        parsed_path = normalize_material_path(f"{package_path}/readme.md")
        parsed_id = _insert_material_file_row(
            conn,
            user=user,
            name="readme.md",
            material_path=parsed_path,
            parent_id=package_id,
            root_id=package_root_id,
            file_profile=readme_profile,
            file_hash=readme_hash,
            file_size=len(readme_bytes),
            owner_scope=owner_scope,
            now=now,
            ai_parse_status="completed",
            ai_parse_result_json=parse_payload_json,
        )
        conn.execute(
            """
            UPDATE material_ai_import_records
            SET package_material_id = ?,
                parsed_material_id = ?,
                document_group = ?,
                document_type = ?,
                document_type_label = ?,
                parse_status = 'completed',
                parse_mode = ?,
                extraction_method = ?,
                metadata_json = ?,
                content_markdown = ?,
                parsed_payload_json = ?,
                export_payload_json = ?,
                warnings_json = ?,
                content_quality_status = ?,
                content_quality_json = ?,
                error_message = '',
                updated_at = ?,
                completed_at = ?,
                failed_at = NULL
            WHERE id = ?
            """,
            (
                package_id,
                parsed_id,
                parse_result.document_group,
                parse_result.document_type,
                parse_result.document_type_label,
                "ai_generated" if parse_result.ai_used else "local_fallback",
                parse_result.extraction_method,
                metadata_json,
                parse_result.content_markdown,
                parse_payload_json,
                export_payload_json,
                warnings_json,
                parse_result.content_quality.get("status", "ok"),
                content_quality_json,
                now,
                now,
                int(record_id),
            ),
        )
        refresh_root_git_metadata(conn, int(package_root_id))
        conn.commit()


async def _write_material_file(file_hash: str, payload_bytes: bytes) -> None:
    target_path = global_file_write_path(file_hash)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        return
    await asyncio.to_thread(target_path.write_bytes, payload_bytes)


def _insert_material_folder_row(
    conn: Any,
    *,
    user: dict[str, Any],
    name: str,
    material_path: str,
    parent_id: int | None,
    inherited_root_id: int | None,
    owner_scope: dict[str, Any],
    now: str,
) -> tuple[int, int]:
    db_engine = get_configured_db_engine()
    insert_sql = """
        INSERT INTO course_materials
        (teacher_id, parent_id, root_id, material_path, name, node_type, mime_type,
         preview_type, ai_capability, file_ext, file_hash, file_size,
         ai_parse_status, ai_optimize_status, owner_role, owner_user_pk, scope_level,
         school_code, school_name, college, department, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'folder', 'inode/directory', 'folder', 'none', '', NULL, 0,
                'idle', 'idle', 'teacher', ?, 'private', ?, ?, ?, ?, ?, ?)
    """
    folder_id = execute_insert_returning_id(
        conn,
        insert_sql,
        (
            int(user["id"]),
            parent_id,
            inherited_root_id,
            material_path,
            name,
            int(user["id"]),
            owner_scope.get("school_code") or "",
            owner_scope.get("school_name") or "",
            owner_scope.get("college") or "",
            owner_scope.get("department") or "",
            now,
            now,
        ),
        engine=db_engine,
    )
    actual_root_id = int(inherited_root_id or folder_id)
    if inherited_root_id is None:
        conn.execute("UPDATE course_materials SET root_id = ? WHERE id = ?", (actual_root_id, folder_id))
    return folder_id, actual_root_id


def _insert_material_file_row(
    conn: Any,
    *,
    user: dict[str, Any],
    name: str,
    material_path: str,
    parent_id: int,
    root_id: int | None,
    file_profile: dict[str, Any],
    file_hash: str,
    file_size: int,
    owner_scope: dict[str, Any],
    now: str,
    ai_parse_status: str = "idle",
    ai_parse_result_json: str | None = None,
) -> int:
    db_engine = get_configured_db_engine()
    check_questions_json = ""
    check_questions_status = "idle"
    check_questions_error = ""
    check_questions_generated_at = None
    if str(ai_parse_status or "").lower() == "completed" and ai_parse_result_json:
        check_payload = build_material_mastery_check_payload(
            ai_parse_result_json,
            material_name=name,
            generated_at=now,
        )
        check_questions_json = json.dumps(check_payload, ensure_ascii=False)
        check_questions_status = "ready" if check_payload.get("status") == "ready" else "fallback"
        check_questions_error = "" if check_questions_status == "ready" else str(check_payload.get("reason") or "")
        check_questions_generated_at = now
    insert_sql = """
        INSERT INTO course_materials
        (teacher_id, parent_id, root_id, material_path, name, node_type, mime_type,
         preview_type, ai_capability, file_ext, file_hash, file_size,
         ai_parse_status, ai_parse_result_json, check_questions_json, check_questions_status,
         check_questions_error, check_questions_generated_at, ai_optimize_status, owner_role, owner_user_pk, scope_level,
         school_code, school_name, college, department, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'file', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'idle',
                'teacher', ?, 'private', ?, ?, ?, ?, ?, ?)
    """
    file_id = execute_insert_returning_id(
        conn,
        insert_sql,
        (
            int(user["id"]),
            parent_id,
            root_id,
            material_path,
            name,
            file_profile["mime_type"],
            file_profile["preview_type"],
            file_profile["ai_capability"],
            file_profile["file_ext"],
            file_hash,
            int(file_size),
            ai_parse_status,
            ai_parse_result_json,
            check_questions_json,
            check_questions_status,
            check_questions_error,
            check_questions_generated_at,
            int(user["id"]),
            owner_scope.get("school_code") or "",
            owner_scope.get("school_name") or "",
            owner_scope.get("college") or "",
            owner_scope.get("department") or "",
            now,
            now,
        ),
        engine=db_engine,
    )
    if root_id is None:
        conn.execute("UPDATE course_materials SET root_id = ? WHERE id = ?", (file_id, file_id))
    return file_id


def _set_plan_status(plan_id: str, **kwargs: Any) -> None:
    with get_db_connection() as conn:
        ap.set_generation_status(conn, plan_id, **kwargs)
        conn.commit()


def _set_material_record_running(record_id: int, message: str) -> None:
    now = datetime.now().isoformat()
    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE material_ai_import_records
            SET parse_status = 'running',
                error_message = ?,
                updated_at = ?,
                started_at = COALESCE(started_at, ?)
            WHERE id = ?
            """,
            (message, now, now, int(record_id)),
        )
        conn.commit()


def _mark_material_record_failed(record_id: int, message: str) -> None:
    now = datetime.now().isoformat()
    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE material_ai_import_records
            SET parse_status = 'failed',
                error_message = ?,
                updated_at = ?,
                failed_at = ?
            WHERE id = ?
            """,
            (message, now, now, int(record_id)),
        )
        conn.commit()


def _load_teacher(conn: Any, teacher_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT id, name, email AS username, email FROM teachers WHERE id = ? LIMIT 1",
        (int(teacher_id),),
    ).fetchone()
    return dict(row) if row else {"id": int(teacher_id), "name": "", "username": "", "email": ""}


def _allocate_scores(raw_scores: list[Any], *, total: int) -> list[int]:
    numbers = [_score_number(value) for value in raw_scores]
    score_sum = sum(numbers)
    if score_sum <= 0:
        base = total // max(1, len(numbers))
        result = [base for _ in numbers]
        for index in range(total - sum(result)):
            result[index % max(1, len(result))] += 1
        return result
    exact = [(value / score_sum) * total for value in numbers]
    floors = [int(math.floor(value)) for value in exact]
    remainder = total - sum(floors)
    order = sorted(range(len(exact)), key=lambda index: exact[index] - floors[index], reverse=True)
    for index in order[:remainder]:
        floors[index] += 1
    return floors


def _score_number(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    if not match:
        return 0.0
    try:
        return float(match.group(0))
    except ValueError:
        return 0.0


def _score_text(value: Any) -> str:
    number = _score_number(value)
    if float(number).is_integer():
        return str(int(number))
    return f"{number:.1f}".rstrip("0").rstrip(".")


def _question_type_label(value: Any) -> str:
    return {
        "radio": "单选题",
        "checkbox": "多选题",
        "text": "填空题",
        "textarea": "问答/实操题",
    }.get(_text(value), _text(value) or "题目")


def _answer_to_text(value: Any) -> str:
    if isinstance(value, list):
        return "、".join(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, dict):
        return "；".join(f"{key}: {val}" for key, val in value.items() if str(val).strip())
    return _text(value)


def _attachment_text(value: Any) -> str:
    attachment = _as_dict(value)
    if not attachment:
        return ""
    parts: list[str] = []
    if attachment.get("description"):
        parts.append(_text(attachment.get("description")))
    if attachment.get("required") or _optional_int(attachment.get("min_count")):
        parts.append(f"至少提交 {_optional_int(attachment.get('min_count')) or 1} 个附件")
    if _optional_int(attachment.get("max_count")):
        parts.append(f"最多 {_optional_int(attachment.get('max_count'))} 个附件")
    allowed = attachment.get("allowed_file_types")
    if isinstance(allowed, list) and allowed:
        parts.append("允许格式：" + "、".join(str(item) for item in allowed[:12]))
    if attachment.get("allow_drawing"):
        parts.append("允许在线绘图")
    return "；".join(part for part in parts if part)


def _extract_keywords(lines: list[Any], keywords: tuple[str, ...]) -> list[str]:
    text_lines = []
    for item in lines:
        for line in str(item or "").splitlines():
            if any(keyword.lower() in line.lower() for keyword in keywords):
                text_lines.append(line.strip())
    seen: list[str] = []
    for line in text_lines:
        if line and line not in seen:
            seen.append(line)
    return seen[:12]


def _compact_source_exam_for_ai(context: dict[str, Any]) -> dict[str, Any]:
    questions = []
    for item in context.get("questions") or []:
        questions.append(
            {
                "ordinal": item.get("ordinal"),
                "page_name": item.get("page_name"),
                "type_label": item.get("type_label"),
                "score": item.get("score"),
                "text": _clip_text(item.get("text"), 520),
                "options": item.get("options") or [],
                "answer": _clip_text(item.get("answer"), 380),
                "grading_guidance": _clip_text(item.get("guidance"), 520),
                "deduction_points": _clip_text(item.get("deduction_points"), 420),
                "attachment_requirements": item.get("attachment_requirements") or {},
            }
        )
    return {
        "id": context.get("paper", {}).get("id"),
        "title": context.get("paper", {}).get("title"),
        "original_total_score": context.get("original_total_score"),
        "scaled_total_score": 100,
        "questions": questions,
    }


def _limited_json(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, indent=2)
    if len(text) <= _MAX_AI_CONTEXT_CHARS:
        return text
    return text[:_MAX_AI_CONTEXT_CHARS] + "\n...（已截断，仅供生成参考）"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _optional_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _first_non_blank(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _first_text_multi(*dicts: dict[str, Any], keys: tuple[str, ...]) -> str:
    for source in dicts:
        if not isinstance(source, dict):
            continue
        for key in keys:
            value = source.get(key)
            if _text(value):
                return _text(value)
    return ""


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _clip_text(value: Any, limit: int) -> str:
    text = _text(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _clip_inline(value: Any, limit: int) -> str:
    return _clip_text(re.sub(r"\s+", " ", _text(value)), limit)
