from __future__ import annotations

import json
import math
import re
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

import httpx

from ..config import DATA_DIR, HOMEWORK_SUBMISSIONS_DIR
from ..core import ai_client
from ..database import get_db_connection
from ..db.connection import begin_immediate_transaction, execute_insert_returning_id, get_configured_db_engine
from .exam_json_service import EXAM_JSON_TEMPLATE, normalize_exam_json_payload, normalize_exam_scoring_payload
from .ai_gateway_service import ai_gateway_post
from .material_mastery_check_service import (
    grade_material_mastery_check,
    normalize_material_mastery_check_payload,
    public_material_mastery_check_payload,
)
from .message_center_service import (
    AI_ASSISTANT_LABEL,
    AI_ASSISTANT_ROLE,
    create_learning_progress_notification,
)
from .psych_profile_service import (
    build_explicit_user_profile_prompt,
    load_explicit_user_profile,
    load_latest_hidden_profile,
)
from .submission_file_alignment import resolve_submission_file_path
from .submission_assets import delete_storage_tree
from .ai_grading_service import submit_submission_for_ai_grading
from .ai_usage_budget_service import ensure_stage_exam_generation_quota
from .cultivation_weight_service import (
    CULTIVATION_WEIGHT_COOLDOWN_DAYS,
    CULTIVATION_WEIGHT_VERSION_DEFAULT,
    DEFAULT_CULTIVATION_WEIGHTS,
    CultivationWeightValidationError,
    build_weight_settings_payload,
    load_cultivation_weight_config,
    normalize_cultivation_weights,
    save_cultivation_weight_config,
    weight_rules_from_weights,
)
from .resource_access_service import student_can_read_assignment


PASSING_STAGE_SCORE = 80
MATERIAL_COMPLETE_SCROLL = 0.86
MATERIAL_COMPLETE_ACTIVE_SECONDS = 180
MATERIAL_COMPLETE_TOTAL_SECONDS = 300
MATERIAL_READING_CREDIT_RATIO = 0.7
INTERACTION_QUALITY_DEFAULT = 0.7
INTERACTION_QUALITY_WEIGHT_FLOOR = 0.5
PEER_HELP_MENTOR_TIER = 6
PEER_HELP_UNIT_MULTIPLIER = 1
PEER_HELP_MENTOR_UNIT_MULTIPLIER = 2
AI_EXAM_SOURCE_TYPE_STAGE = "manual"
MAX_COURSE_SECT_NAME_LENGTH = 18
SCORE_EVENT_DELTA_THRESHOLD = 0.1
CULTIVATION_SNAPSHOT_REFRESH_TASK_KIND = "cultivation_snapshot_refresh"
CULTIVATION_SNAPSHOT_REFRESH_INTERVAL_SECONDS = 180
CULTIVATION_WEEKLY_SNAPSHOT_TASK_KIND = "cultivation_weekly_snapshot"
CULTIVATION_WEEKLY_SNAPSHOT_INTERVAL_SECONDS = 24 * 60 * 60
CULTIVATION_WEEKLY_REPORT_TASK_KIND = "cultivation_weekly_report"
CULTIVATION_WEEKLY_REPORT_INTERVAL_SECONDS = 24 * 60 * 60
CULTIVATION_SCORE_EVENT_ARCHIVE_TASK_KIND = "cultivation_score_event_archive"
CULTIVATION_SCORE_EVENT_ARCHIVE_INTERVAL_SECONDS = 24 * 60 * 60
CULTIVATION_SCORE_EVENT_RETENTION_DAYS = 90
STAGE_EXAM_GENERATION_TASK_KIND = "stage_exam_generation"
STAGE_EXAM_GENERATION_MAX_ATTEMPTS = 3
STAGE_EXAM_RETREAT_PLAN_KEY = "retreat_plan"
STAGE_EXAM_RETREAT_MIN_ITEMS = 3
STAGE_EXAM_RETREAT_MAX_ITEMS = 5
STAGE_EXAM_TEMPLATE_PATH = DATA_DIR / "exam_templates" / "learning_stage_exam_template.json"
STAGE_EXAM_TEMPLATE: dict[str, Any] = {
    "title": "课程名称 · 境界名称破境试炼",
    "description": "本试炼满分 100 分，建议作答时间 60-90 分钟。试卷只覆盖当前境界及之前已学习的课堂知识点。",
    "grading": {
        "total_score": 100,
        "description": "Grade by the reference answer, reasoning quality, course terminology, and required evidence for each question.",
        "style": "medium",
    },
    "pages": [
        {
            "name": "第一部分：单项选择题",
            "questions": [
                {
                    "id": "p1_q1",
                    "type": "radio",
                    "text": "1. 围绕当前境界范围内的基础概念设计一道单项选择题。",
                    "options": ["A. 选项一", "B. 选项二", "C. 选项三", "D. 选项四"],
                    "answer": "A",
                    "explanation": "说明正确答案对应的课程知识点。",
                }
            ],
        },
        {
            "name": "第二部分：多项选择题",
            "questions": [
                {
                    "id": "p2_q1",
                    "type": "checkbox",
                    "text": "2. 围绕当前境界范围内容易混淆的知识点设计一道多项选择题。",
                    "options": ["A. 选项一", "B. 选项二", "C. 选项三", "D. 选项四"],
                    "answer": ["A", "C"],
                    "explanation": "说明每个正确选项为什么成立，并指出干扰项的误区。",
                }
            ],
        },
        {
            "name": "第三部分：填空与判断迁移题",
            "questions": [
                {
                    "id": "p3_q1",
                    "type": "text",
                    "text": "3. 根据课堂材料中的关键术语或过程设计一道短答案题。",
                    "placeholder": "请输入简短答案",
                    "answer": "参考答案",
                    "explanation": "说明答案所在的知识点和常见错误。",
                }
            ],
        },
        {
            "name": "第四部分：简答与综合应用题",
            "questions": [
                {
                    "id": "p4_q1",
                    "type": "textarea",
                    "text": "4. 结合学生学习记录和当前境界范围，设计一道需要解释、推理或应用的综合题。",
                    "placeholder": "请写出完整作答过程",
                    "answer": "参考答案",
                    "explanation": "给出评分关注点、关键步骤和可接受的表述。",
                }
            ],
        },
    ],
}

STARTER_LEVEL: dict[str, Any] = {
    "key": "mortal",
    "level_key": "mortal",
    "name": "未入道",
    "level_name": "未入道",
    "short_name": "未入道",
    "tier": 0,
    "unlock_score": 0,
    "theme": "mortal",
    "title": "正在凝聚第一缕灵力",
    "certificate_title": "未入道",
    "address_title": "初入修士",
    "aura_label": "灵根初醒",
    "description": "刚踏入课堂修行，先从稳定阅读和完成第一项任务开始。",
}

LEARNING_LEVELS: tuple[dict[str, Any], ...] = (
    {
        "key": "qi_awakening",
        "name": "启蒙入门",
        "short_name": "启蒙",
        "tier": 1,
        "unlock_score": 8,
        "theme": "qi_awakening",
        "certificate_title": "启蒙入门灵契",
        "address_title": "启蒙修士",
        "aura_label": "入门有光",
        "description": "开始进入课程语境，能完成基础阅读、首轮课堂活动和第一次任务。",
    },
    {
        "key": "qi_refining",
        "name": "基础成形",
        "short_name": "基础",
        "tier": 2,
        "unlock_score": 18,
        "theme": "qi_refining",
        "certificate_title": "基础成形道印",
        "address_title": "基础修士",
        "aura_label": "根基初稳",
        "description": "能稳定阅读材料、完成任务，并开始把课堂概念和操作步骤对应起来。",
    },
    {
        "key": "foundation",
        "name": "核心筑基",
        "short_name": "筑基",
        "tier": 3,
        "unlock_score": 32,
        "theme": "foundation",
        "certificate_title": "核心筑基玉牒",
        "address_title": "筑基修士",
        "aura_label": "主干成脉",
        "description": "对课程主干知识形成结构化认知，能解释关键术语和基础机制。",
    },
    {
        "key": "application_seed",
        "name": "迁移初通",
        "short_name": "迁移",
        "tier": 4,
        "unlock_score": 46,
        "theme": "application_seed",
        "certificate_title": "迁移初通玉简",
        "address_title": "迁移修士",
        "aura_label": "举一反三",
        "description": "能把概念用于作业、实验、案例或小型综合题，不再只停留在记忆层面。",
    },
    {
        "key": "golden_core",
        "name": "融会小成",
        "short_name": "融会",
        "tier": 5,
        "unlock_score": 58,
        "theme": "golden_core",
        "certificate_title": "融会小成宝箓",
        "address_title": "融会修士",
        "aura_label": "知识成网",
        "description": "能把不同章节、材料和反馈串起来，开始形成自己的解题框架。",
    },
    {
        "key": "practical_mastery",
        "name": "基本掌握",
        "short_name": "掌握",
        "tier": 6,
        "unlock_score": 70,
        "theme": "practical_mastery",
        "certificate_title": "基本掌握真箓",
        "address_title": "掌握修士",
        "aura_label": "稳态输出",
        "description": "达到本课程的基本掌握线，能独立完成大多数核心任务并解释自己的过程。",
    },
    {
        "key": "systems_thinking",
        "name": "系统贯通",
        "short_name": "贯通",
        "tier": 7,
        "unlock_score": 80,
        "theme": "systems_thinking",
        "certificate_title": "系统贯通玄章",
        "address_title": "贯通修士",
        "aura_label": "体系自洽",
        "description": "能跨章节建模，定位薄弱点，并用复盘结果反向优化学习策略。",
    },
    {
        "key": "nascent_soul",
        "name": "完全掌握",
        "short_name": "完全",
        "tier": 8,
        "unlock_score": 88,
        "theme": "nascent_soul",
        "certificate_title": "完全掌握天书",
        "address_title": "圆满修士",
        "aura_label": "知行合一",
        "description": "对课程核心与延展内容均能稳定迁移，达到完整掌握状态。",
    },
    {
        "key": "independent_path",
        "name": "自主精进",
        "short_name": "自主",
        "tier": 9,
        "unlock_score": 95,
        "theme": "independent_path",
        "certificate_title": "自主精进星图",
        "address_title": "自驱修士",
        "aura_label": "自驱生长",
        "description": "具备明确的自主学习方法，能主动发现问题、查找资料并完成高质量复盘。",
    },
    {
        "key": "mentor_heart",
        "name": "可为人师",
        "short_name": "师者",
        "tier": 10,
        "unlock_score": 100,
        "theme": "mentor_heart",
        "certificate_title": "可为人师终章印",
        "address_title": "领航修士",
        "aura_label": "传道解惑",
        "description": "知识掌握、自主学习、表达讲解和助人能力都达到课程理想状态，可承担同伴导师角色。",
    },
)

LEGACY_LEVEL_KEY_MAP = {
    "starter": "mortal",
    "bronze": "qi_awakening",
    "silver": "qi_refining",
    "gold": "foundation",
    "diamond": "golden_core",
}


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def derive_course_sect_name(course_name: Any) -> str:
    clean_name = "".join(str(course_name or "").strip().split())
    if not clean_name:
        return "课堂宗"
    if "计算机网络" in clean_name:
        return "计网宗"
    replacements = ("课程", "实验", "基础", "概论", "导论", "实践", "设计", "专题")
    compact = clean_name
    for word in replacements:
        compact = compact.replace(word, "")
    compact = compact or clean_name
    if len(compact) <= 4:
        stem = compact
    else:
        cjk_chars = [char for char in compact if "\u4e00" <= char <= "\u9fff"]
        stem = "".join(cjk_chars[:2]) if cjk_chars else compact[:4]
    return f"{stem}宗"


def normalize_course_sect_name(value: Any, *, course_name: Any = "") -> str:
    normalized = " ".join(str(value or "").strip().split())
    if not normalized:
        normalized = derive_course_sect_name(course_name)
    if len(normalized) > MAX_COURSE_SECT_NAME_LENGTH:
        normalized = normalized[:MAX_COURSE_SECT_NAME_LENGTH]
    return normalized


def truncate_text(value: Any, limit: int = 500) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + "…"


def json_loads(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def normalize_level_key(stage_key: Any) -> str:
    normalized = str(stage_key or "").strip().lower()
    return LEGACY_LEVEL_KEY_MAP.get(normalized, normalized)


def get_learning_level(stage_key: str) -> Optional[dict[str, Any]]:
    normalized = normalize_level_key(stage_key)
    return next((item for item in LEARNING_LEVELS if item["key"] == normalized), None)


def get_learning_stage_options() -> list[dict[str, Any]]:
    return [public_level_payload(level) for level in LEARNING_LEVELS]


def normalize_assignment_stage_key(raw_stage_key: Any) -> Optional[str]:
    stage_key = normalize_level_key(raw_stage_key)
    if not stage_key:
        return None
    level = get_learning_level(stage_key)
    if not level:
        raise ValueError("试炼阶段不存在")
    return level["key"]


def get_starter_level() -> dict[str, Any]:
    return dict(STARTER_LEVEL)


def public_level_payload(level: dict[str, Any] | None) -> dict[str, Any]:
    source = level or STARTER_LEVEL
    key = str(source.get("key") or source.get("level_key") or "mortal")
    level_key = normalize_level_key(key)
    if level_key == "mortal":
        source = STARTER_LEVEL
    else:
        source = get_learning_level(level_key) or source
    return {
        "key": level_key,
        "level_key": level_key,
        "name": source.get("name") or source.get("level_name") or STARTER_LEVEL["name"],
        "level_name": source.get("name") or source.get("level_name") or STARTER_LEVEL["name"],
        "short_name": source.get("short_name") or source.get("name") or STARTER_LEVEL["short_name"],
        "tier": safe_int(source.get("tier")),
        "unlock_score": safe_float(source.get("unlock_score")),
        "theme": source.get("theme") or level_key,
        "title": source.get("title") or source.get("certificate_title") or STARTER_LEVEL["title"],
        "certificate_title": source.get("certificate_title") or STARTER_LEVEL["certificate_title"],
        "address_title": source.get("address_title") or STARTER_LEVEL["address_title"],
        "aura_label": source.get("aura_label") or STARTER_LEVEL["aura_label"],
        "description": source.get("description") or "",
    }


def format_cultivation_address(name: Any, level_or_key: Any = None) -> str:
    if isinstance(level_or_key, dict):
        level = public_level_payload(level_or_key)
    else:
        level = public_level_payload(get_learning_level(str(level_or_key or "")) if level_or_key else STARTER_LEVEL)
    clean_name = str(name or "").strip()
    if not clean_name:
        return str(level["address_title"])
    return f"{level['address_title']}{clean_name}"


def get_stage_exam_target(conn, assignment_id: int | str) -> Optional[dict[str, Any]]:
    row = conn.execute(
        """
        SELECT *
        FROM learning_stage_exam_attempts
        WHERE assignment_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (assignment_id,),
    ).fetchone()
    return dict(row) if row else None


def student_can_access_assignment(conn, assignment_id: int | str, student_id: int | str) -> bool:
    return student_can_read_assignment(conn, assignment_id, student_id)


def is_personal_stage_exam_assignment(conn, assignment_id: int | str) -> bool:
    return get_stage_exam_target(conn, assignment_id) is not None


def is_learning_stage_assignment(conn, assignment_id: int | str) -> bool:
    return is_personal_stage_exam_assignment(conn, assignment_id)


def build_personal_stage_exam_stats(conn, class_offering_id: int) -> dict[str, int]:
    row = conn.execute(
        """
        SELECT COUNT(*) AS total_count,
               COUNT(DISTINCT student_id) AS student_count,
               SUM(CASE WHEN status IN ('generating', 'generated', 'submitted', 'grading') THEN 1 ELSE 0 END) AS active_count,
               SUM(CASE WHEN status IN ('submitted', 'grading') THEN 1 ELSE 0 END) AS submitted_count,
               SUM(CASE WHEN status = 'passed' THEN 1 ELSE 0 END) AS passed_count,
               SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_count
        FROM learning_stage_exam_attempts
        WHERE class_offering_id = ?
        """,
        (int(class_offering_id),),
    ).fetchone()
    if not row:
        return {
            "total_count": 0,
            "student_count": 0,
            "active_count": 0,
            "submitted_count": 0,
            "passed_count": 0,
            "failed_count": 0,
        }
    return {
        "total_count": int(row["total_count"] or 0),
        "student_count": int(row["student_count"] or 0),
        "active_count": int(row["active_count"] or 0),
        "submitted_count": int(row["submitted_count"] or 0),
        "passed_count": int(row["passed_count"] or 0),
        "failed_count": int(row["failed_count"] or 0),
    }


def is_personal_stage_exam_paper(conn, paper_id: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM learning_stage_exam_attempts
        WHERE exam_paper_id = ?
        LIMIT 1
        """,
        (paper_id,),
    ).fetchone()
    return row is not None


def personal_stage_assignment_filter_sql(alias: str = "a") -> str:
    return (
        "NOT EXISTS ("
        "SELECT 1 FROM learning_stage_exam_attempts lsea "
        f"WHERE lsea.assignment_id = {alias}.id"
        ")"
    )


def visible_stage_assignment_filter_sql(alias: str = "a") -> str:
    return (
        "NOT EXISTS ("
        "SELECT 1 FROM learning_stage_exam_attempts lsea "
        f"WHERE lsea.assignment_id = {alias}.id "
        "AND lsea.student_id != ?"
        ")"
    )


def _load_offering(conn, class_offering_id: int) -> Optional[dict[str, Any]]:
    row = conn.execute(
        """
        SELECT o.*,
               c.name AS course_name,
               c.sect_name AS course_sect_name,
               c.description AS course_description,
               c.credits AS course_credits,
               cl.name AS class_name,
               t.name AS teacher_name
        FROM class_offerings o
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        JOIN teachers t ON t.id = o.teacher_id
        WHERE o.id = ?
        LIMIT 1
        """,
        (class_offering_id,),
    ).fetchone()
    return dict(row) if row else None


def _load_student(conn, student_id: int) -> Optional[dict[str, Any]]:
    row = conn.execute(
        """
        SELECT s.*, c.name AS class_name
        FROM students s
        LEFT JOIN classes c ON c.id = s.class_id
        WHERE s.id = ?
        LIMIT 1
        """,
        (student_id,),
    ).fetchone()
    return dict(row) if row else None


def _load_required_materials(conn, class_offering_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT DISTINCT m.id,
               m.name,
               m.material_path,
               m.preview_type,
               m.check_questions_json,
               m.check_questions_status,
               MIN(s.order_index) AS order_index,
               MIN(s.id) AS session_id
        FROM course_materials m
        JOIN (
            SELECT learning_material_id AS material_id, order_index, id
            FROM class_offering_sessions
            WHERE class_offering_id = ? AND learning_material_id IS NOT NULL
            UNION ALL
            SELECT o.home_learning_material_id AS material_id, 99999 AS order_index, NULL AS id
            FROM class_offerings o
            WHERE o.id = ? AND o.home_learning_material_id IS NOT NULL
            UNION ALL
            SELECT cma.material_id, 90000 AS order_index, NULL AS id
            FROM course_material_assignments cma
            WHERE cma.class_offering_id = ?
        ) s ON s.material_id = m.id
        WHERE m.node_type = 'file'
        GROUP BY m.id, m.name, m.material_path, m.preview_type, m.check_questions_json, m.check_questions_status
        ORDER BY order_index, m.name COLLATE NOCASE
        """,
        (class_offering_id, class_offering_id, class_offering_id),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_progress_rows(conn, class_offering_id: int, student_id: int) -> dict[int, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM learning_material_progress
        WHERE class_offering_id = ? AND student_id = ?
        """,
        (class_offering_id, student_id),
    ).fetchall()
    return {int(row["material_id"]): dict(row) for row in rows}


def _material_has_ready_mastery_check(material: dict[str, Any] | None) -> bool:
    if not material:
        return False
    status = str(material.get("check_questions_status") or "").strip().lower()
    payload = normalize_material_mastery_check_payload(material.get("check_questions_json"))
    return status == "ready" and payload.get("status") == "ready" and len(payload.get("questions") or []) >= 2


def _public_mastery_check_context(
    material: dict[str, Any] | None,
    progress: dict[str, Any] | None = None,
) -> dict[str, Any]:
    public_payload = public_material_mastery_check_payload((material or {}).get("check_questions_json"))
    completed = bool(progress and safe_int(progress.get("completed")))
    mastered = bool(progress and safe_int(progress.get("mastered")))
    return {
        **public_payload,
        "available": _material_has_ready_mastery_check(material),
        "completed": completed,
        "mastered": mastered,
        "attempts": safe_int((progress or {}).get("mastery_attempts")),
        "source": str((progress or {}).get("mastery_source") or ""),
    }


def _material_unit_ratio(progress: Optional[dict[str, Any]]) -> float:
    if not progress:
        return 0.0
    if safe_int(progress.get("completed")):
        if "mastered" not in progress:
            return 1.0
        return 1.0 if safe_int(progress.get("mastered")) else MATERIAL_READING_CREDIT_RATIO
    scroll_ratio = clamp(safe_float(progress.get("max_scroll_ratio")))
    active_ratio = clamp(safe_int(progress.get("active_seconds")) / MATERIAL_COMPLETE_ACTIVE_SECONDS)
    total_ratio = clamp(safe_int(progress.get("accumulated_seconds")) / MATERIAL_COMPLETE_TOTAL_SECONDS)
    return min(MATERIAL_READING_CREDIT_RATIO, clamp(scroll_ratio * 0.58 + active_ratio * 0.28 + total_ratio * 0.14))


def _load_assignment_metrics(conn, class_offering_id: int, student_id: int) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT a.id,
               a.title,
               a.exam_paper_id,
               a.status,
               s.id AS submission_id,
               s.status AS submission_status,
               s.score AS submission_score
        FROM assignments a
        LEFT JOIN submissions s
               ON s.assignment_id = a.id
              AND s.student_pk_id = ?
              AND COALESCE(s.is_absence_score, 0) = 0
        LEFT JOIN learning_stage_exam_attempts lsea
               ON lsea.assignment_id = a.id
        WHERE a.class_offering_id = ?
          AND a.status != 'new'
          AND lsea.id IS NULL
        ORDER BY a.created_at ASC, a.id ASC
        """,
        (student_id, class_offering_id),
    ).fetchall()
    items = [dict(row) for row in rows]
    total = len(items)
    submitted_items = [item for item in items if item.get("submission_id")]
    graded_items = [item for item in submitted_items if item.get("submission_score") is not None]
    submitted_count = len(submitted_items)
    score_ratios = [clamp(safe_float(item.get("submission_score")) / 100) for item in graded_items]
    score_ratio = sum(score_ratios) / len(score_ratios) if score_ratios else (0.68 if submitted_count else 0.0)
    completion_ratio = submitted_count / total if total else 0.0
    task_ratio = clamp(completion_ratio * 0.72 + score_ratio * 0.28)
    return {
        "assignment_count": total,
        "submitted_count": submitted_count,
        "graded_count": len(graded_items),
        "exam_count": sum(1 for item in items if item.get("exam_paper_id")),
        "completion_ratio": completion_ratio,
        "score_ratio": score_ratio,
        "task_ratio": task_ratio,
        "items": [
            {
                "id": safe_int(item.get("id")),
                "title": item.get("title") or "课堂任务",
                "submitted": bool(item.get("submission_id")),
                "graded": item.get("submission_score") is not None,
                "score": item.get("submission_score"),
            }
            for item in items[:12]
        ],
    }


def _load_interaction_quality_signal(conn, class_offering_id: int, student_id: int) -> dict[str, Any]:
    try:
        row = conn.execute(
            """
            SELECT interaction_quality, interaction_quality_label, interaction_quality_reason, created_at
            FROM classroom_behavior_profiles
            WHERE class_offering_id = ?
              AND user_pk = ?
              AND user_role = 'student'
              AND interaction_quality IS NOT NULL
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (class_offering_id, student_id),
        ).fetchone()
    except Exception:
        row = None
    if not row:
        quality = INTERACTION_QUALITY_DEFAULT
        return {
            "quality": quality,
            "quality_factor": INTERACTION_QUALITY_WEIGHT_FLOOR + (1 - INTERACTION_QUALITY_WEIGHT_FLOOR) * quality,
            "quality_source": "default",
            "quality_label": "default",
            "quality_reason": "",
        }
    quality = clamp(safe_float(row["interaction_quality"], INTERACTION_QUALITY_DEFAULT))
    label = str(row["interaction_quality_label"] or "").strip().lower()
    if label not in {"low", "medium", "high"}:
        if quality >= 0.78:
            label = "high"
        elif quality >= 0.52:
            label = "medium"
        else:
            label = "low"
    return {
        "quality": quality,
        "quality_factor": INTERACTION_QUALITY_WEIGHT_FLOOR + (1 - INTERACTION_QUALITY_WEIGHT_FLOOR) * quality,
        "quality_source": "profile",
        "quality_label": label,
        "quality_reason": str(row["interaction_quality_reason"] or "").strip(),
    }


def _load_student_certificate_tier(conn, class_offering_id: int, student_id: int) -> int:
    try:
        row = conn.execute(
            """
            SELECT COALESCE(MAX(tier), 0) AS tier
            FROM learning_certificates
            WHERE class_offering_id = ?
              AND student_id = ?
            """,
            (int(class_offering_id), int(student_id)),
        ).fetchone()
    except Exception:
        row = None
    return safe_int(row["tier"] if row else 0)


def _load_peer_help_count(conn, class_offering_id: int, student_id: int) -> int:
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS peer_help_count
            FROM classroom_behavior_events
            WHERE class_offering_id = ?
              AND user_pk = ?
              AND user_role = 'student'
              AND action_type = 'peer_help'
            """,
            (int(class_offering_id), int(student_id)),
        ).fetchone()
    except Exception:
        row = None
    return safe_int(row["peer_help_count"] if row else 0)


def _load_interaction_metrics(conn, class_offering_id: int, student_id: int) -> dict[str, Any]:
    chat_row = conn.execute(
        """
        SELECT COUNT(*) AS message_count,
               SUM(CASE WHEN message LIKE '%@助教%' OR message LIKE '%@AI%' OR message LIKE '%@ai%' OR message LIKE '%AI助教%' THEN 1 ELSE 0 END) AS mention_count
        FROM chat_logs
        WHERE class_offering_id = ?
          AND user_role = 'student'
          AND user_id = ?
        """,
        (class_offering_id, str(student_id)),
    ).fetchone()
    behavior_row = conn.execute(
        """
        SELECT
            SUM(CASE WHEN action_type = 'ai_question' THEN 1 ELSE 0 END) AS ai_question_count,
            COUNT(DISTINCT substr(created_at, 1, 10)) AS active_days
        FROM classroom_behavior_events
        WHERE class_offering_id = ?
          AND user_pk = ?
          AND user_role = 'student'
        """,
        (class_offering_id, student_id),
    ).fetchone()
    state_row = conn.execute(
        """
        SELECT total_activity_count,
               online_accumulated_seconds,
               focus_total_seconds,
               visible_total_seconds,
               discussion_lurk_total_seconds,
               ai_panel_open_total_seconds
        FROM classroom_behavior_states
        WHERE class_offering_id = ?
          AND user_pk = ?
          AND user_role = 'student'
        LIMIT 1
        """,
        (class_offering_id, student_id),
    ).fetchone()
    sender_identity = f"student:{student_id}"
    private_row = conn.execute(
        """
        SELECT COUNT(*) AS private_teacher_count
        FROM private_messages
        WHERE class_offering_id = ?
          AND sender_identity = ?
          AND recipient_role IN ('teacher', 'assistant')
        """,
        (class_offering_id, sender_identity),
    ).fetchone()

    message_count = safe_int(chat_row["message_count"] if chat_row else 0)
    mention_count = safe_int(chat_row["mention_count"] if chat_row else 0)
    ai_question_count = safe_int(behavior_row["ai_question_count"] if behavior_row else 0)
    active_days = safe_int(behavior_row["active_days"] if behavior_row else 0)
    private_teacher_count = safe_int(private_row["private_teacher_count"] if private_row else 0)
    online_seconds = safe_int(state_row["online_accumulated_seconds"] if state_row else 0)
    focus_seconds = safe_int(state_row["focus_total_seconds"] if state_row else 0)
    activity_count = safe_int(state_row["total_activity_count"] if state_row else 0)
    peer_help_count = _load_peer_help_count(conn, class_offering_id, student_id)
    certificate_tier = _load_student_certificate_tier(conn, class_offering_id, student_id)
    peer_help_multiplier = (
        PEER_HELP_MENTOR_UNIT_MULTIPLIER
        if certificate_tier >= PEER_HELP_MENTOR_TIER
        else PEER_HELP_UNIT_MULTIPLIER
    )
    peer_help_units = peer_help_count * peer_help_multiplier
    question_help_units = min(ai_question_count + peer_help_units, 8)

    base_interaction_ratio = clamp(
        question_help_units / 8 * 0.34
        + min(message_count, 12) / 12 * 0.24
        + min(mention_count, 4) / 4 * 0.22
        + min(private_teacher_count, 3) / 3 * 0.20
    )
    quality_signal = _load_interaction_quality_signal(conn, class_offering_id, student_id)
    interaction_quality = clamp(safe_float(quality_signal.get("quality"), INTERACTION_QUALITY_DEFAULT))
    interaction_quality_factor = clamp(
        safe_float(quality_signal.get("quality_factor"), 0.85),
        INTERACTION_QUALITY_WEIGHT_FLOOR,
        1.0,
    )
    interaction_ratio = clamp(base_interaction_ratio * interaction_quality_factor)
    consistency_ratio = clamp(
        min(online_seconds, 7200) / 7200 * 0.42
        + min(focus_seconds, 5400) / 5400 * 0.28
        + min(active_days, 5) / 5 * 0.20
        + min(activity_count, 80) / 80 * 0.10
    )
    return {
        "chat_message_count": message_count,
        "assistant_mention_count": mention_count,
        "ai_question_count": ai_question_count,
        "peer_help_count": peer_help_count,
        "peer_help_units": peer_help_units,
        "peer_help_multiplier": peer_help_multiplier,
        "private_teacher_count": private_teacher_count,
        "active_days": active_days,
        "online_seconds": online_seconds,
        "focus_seconds": focus_seconds,
        "activity_count": activity_count,
        "base_interaction_ratio": base_interaction_ratio,
        "interaction_quality": round(interaction_quality, 4),
        "interaction_quality_factor": round(interaction_quality_factor, 4),
        "interaction_quality_source": quality_signal.get("quality_source") or "default",
        "interaction_quality_label": quality_signal.get("quality_label") or "",
        "interaction_quality_reason": quality_signal.get("quality_reason") or "",
        "interaction_ratio": interaction_ratio,
        "consistency_ratio": consistency_ratio,
    }


def _load_certificates(conn, class_offering_id: int, student_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM learning_certificates
        WHERE class_offering_id = ? AND student_id = ?
        ORDER BY tier ASC, issued_at ASC, id ASC
        """,
        (class_offering_id, student_id),
    ).fetchall()
    items = [dict(row) for row in rows]
    for item in items:
        item["metadata"] = json_loads(item.get("metadata_json"), {})
        level = public_level_payload(get_learning_level(item.get("level_key") or item.get("stage_key")))
        item["stage_key"] = normalize_level_key(item.get("stage_key") or level["key"])
        item["key"] = level["key"]
        item["level_key"] = level["key"]
        item["level_name"] = level["level_name"]
        item["short_name"] = level["short_name"]
        item["tier"] = level["tier"]
        item["title"] = level["certificate_title"]
        item["theme"] = level["theme"]
        item["address_title"] = level["address_title"]
        item["aura_label"] = level["aura_label"]
        item["revealed_at"] = str(item.get("revealed_at") or "")
        item["needs_reveal"] = not bool(item["revealed_at"])
    return items


def mark_learning_certificate_revealed(
    conn,
    certificate_id: int | str,
    student_id: int | str,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT *
        FROM learning_certificates
        WHERE id = ? AND student_id = ?
        LIMIT 1
        """,
        (safe_int(certificate_id), safe_int(student_id)),
    ).fetchone()
    if not row:
        return None
    item = dict(row)
    revealed_at = str(item.get("revealed_at") or "").strip()
    if not revealed_at:
        revealed_at = now_iso()
        conn.execute(
            """
            UPDATE learning_certificates
            SET revealed_at = ?
            WHERE id = ? AND student_id = ? AND (revealed_at IS NULL OR TRIM(revealed_at) = '')
            """,
            (revealed_at, safe_int(certificate_id), safe_int(student_id)),
        )
        item["revealed_at"] = revealed_at
    item["metadata"] = json_loads(item.get("metadata_json"), {})
    return item


def _load_latest_attempts(conn, class_offering_id: int, student_id: int) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM learning_stage_exam_attempts
        WHERE class_offering_id = ? AND student_id = ?
        ORDER BY generated_at DESC, id DESC
        """,
        (class_offering_id, student_id),
    ).fetchall()
    attempts: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        attempts.setdefault(normalize_level_key(item["stage_key"]), item)
    return attempts


def _weights_from_metrics(metrics: dict[str, Any] | None) -> dict[str, int]:
    if not isinstance(metrics, dict):
        return dict(DEFAULT_CULTIVATION_WEIGHTS)
    weights = metrics.get("weights")
    if isinstance(weights, dict):
        try:
            return normalize_cultivation_weights(weights)
        except CultivationWeightValidationError:
            pass
    return dict(DEFAULT_CULTIVATION_WEIGHTS)


def _metric_weight(metrics: dict[str, Any] | None, key: str) -> float:
    return safe_float(_weights_from_metrics(metrics).get(key), safe_float(DEFAULT_CULTIVATION_WEIGHTS.get(key)))


def _build_learning_metrics(
    conn,
    class_offering_id: int,
    student_id: int,
    *,
    weight_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    weight_config = weight_config or load_cultivation_weight_config(conn, int(class_offering_id))
    weights = dict(weight_config.get("weights") or DEFAULT_CULTIVATION_WEIGHTS)
    materials = _load_required_materials(conn, class_offering_id)
    progress_rows = _load_progress_rows(conn, class_offering_id, student_id)
    material_items: list[dict[str, Any]] = []
    material_ratios: list[float] = []
    for material in materials:
        progress = progress_rows.get(int(material["id"]))
        unit_ratio = _material_unit_ratio(progress)
        completed = bool(progress and safe_int(progress.get("completed")))
        mastered = bool(progress and safe_int(progress.get("mastered")))
        check_ready = _material_has_ready_mastery_check(material)
        material_ratios.append(unit_ratio)
        material_items.append({
            **material,
            "progress": progress,
            "unit_ratio": round(unit_ratio, 4),
            "percent": int(round(unit_ratio * 100)),
            "completed": completed,
            "mastered": mastered,
            "mastery_source": str((progress or {}).get("mastery_source") or ""),
            "needs_mastery_check": bool(completed and check_ready and not mastered),
            "mastery_check": _public_mastery_check_context(material, progress),
        })
    material_ratio = sum(material_ratios) / len(material_ratios) if material_ratios else 0.0
    completed_material_count = sum(1 for item in material_items if item.get("completed"))
    mastered_material_count = sum(1 for value in material_ratios if value >= 0.94)

    assignment_metrics = _load_assignment_metrics(conn, class_offering_id, student_id)
    interaction_metrics = _load_interaction_metrics(conn, class_offering_id, student_id)

    material_points = material_ratio * safe_float(weights.get("material"), DEFAULT_CULTIVATION_WEIGHTS["material"])
    task_points = assignment_metrics["task_ratio"] * safe_float(weights.get("task"), DEFAULT_CULTIVATION_WEIGHTS["task"])
    interaction_points = interaction_metrics["interaction_ratio"] * safe_float(weights.get("interaction"), DEFAULT_CULTIVATION_WEIGHTS["interaction"])
    consistency_points = interaction_metrics["consistency_ratio"] * safe_float(weights.get("consistency"), DEFAULT_CULTIVATION_WEIGHTS["consistency"])
    total_score = clamp((material_points + task_points + interaction_points + consistency_points) / 100, 0, 1) * 100

    return {
        "score": round(total_score, 1),
        "weights": weights,
        "weight_version": str(weight_config.get("version") or CULTIVATION_WEIGHT_VERSION_DEFAULT),
        "weight_source": str(weight_config.get("source") or "default"),
        "components": {
            "material": round(material_points, 1),
            "task": round(task_points, 1),
            "interaction": round(interaction_points, 1),
            "consistency": round(consistency_points, 1),
        },
        "material": {
            "required_count": len(materials),
            "completed_count": completed_material_count,
            "mastered_count": mastered_material_count,
            "ratio": round(material_ratio, 4),
            "items": material_items[:12],
        },
        "assignments": assignment_metrics,
        "interactions": interaction_metrics,
    }


def build_score_opportunities(
    metrics: dict[str, Any],
    next_stage: dict[str, Any] | None,
    *,
    class_offering_id: int | None = None,
    limit: int = 4,
) -> list[dict[str, Any]]:
    if not next_stage or str(next_stage.get("status") or "") in {"challenge_ready", "generating", "in_exam"}:
        return []
    score = safe_float(metrics.get("score"))
    remaining = round(max(safe_float(next_stage.get("unlock_score")) - score, 0.0), 1)
    if remaining <= 0:
        return []

    classroom_url = f"/classroom/{int(class_offering_id)}" if class_offering_id else ""
    opportunities: list[dict[str, Any]] = []
    material = metrics.get("material") or {}
    required_count = safe_int(material.get("required_count"))
    material_weight = _metric_weight(metrics, "material")
    task_weight = _metric_weight(metrics, "task")
    interaction_weight = _metric_weight(metrics, "interaction")
    consistency_weight = _metric_weight(metrics, "consistency")
    material_unit_points = material_weight / required_count if required_count else 0
    for item in material.get("items") or []:
        unit_ratio = clamp(safe_float(item.get("unit_ratio")))
        if unit_ratio >= 0.94:
            continue
        estimated_delta = round(max(0.0, 1 - unit_ratio) * material_unit_points, 1)
        if estimated_delta <= 0:
            continue
        material_id = safe_int(item.get("id"))
        needs_mastery_check = bool(item.get("needs_mastery_check"))
        opportunities.append({
            "type": "material",
            "component": "material",
            "title": f"{'掌握' if needs_mastery_check else '研读'}《{item.get('name') or '课堂材料'}》",
            "summary": "完成心法检验可拿满本材料修为。" if needs_mastery_check else "材料研读是当前最直接的修为来源。",
            "estimated_delta": estimated_delta,
            "action_label": "去掌握" if needs_mastery_check else "去研读",
            "action_url": (
                f"/materials/view/{material_id}?class_offering_id={int(class_offering_id)}#mastery-check"
                if needs_mastery_check and class_offering_id and material_id
                else f"{classroom_url}?material_id={material_id}" if classroom_url and material_id else classroom_url
            ),
        })

    assignments = metrics.get("assignments") or {}
    assignment_count = safe_int(assignments.get("assignment_count"))
    task_unit_points = task_weight * 0.72 / assignment_count if assignment_count else 0
    for item in assignments.get("items") or []:
        if item.get("submitted"):
            continue
        assignment_id = safe_int(item.get("id"))
        estimated_delta = round(task_unit_points, 1)
        if estimated_delta <= 0:
            continue
        opportunities.append({
            "type": "assignment",
            "component": "task",
            "title": f"提交《{item.get('title') or '课堂任务'}》",
            "summary": "先完成未交任务，再等待批改带来下一段提升。",
            "estimated_delta": estimated_delta,
            "action_label": "去完成",
            "action_url": f"/assignment/{assignment_id}" if assignment_id else classroom_url,
        })

    interactions = metrics.get("interactions") or {}
    interaction_gap = round(max(0.0, 1 - clamp(safe_float(interactions.get("interaction_ratio")))) * interaction_weight, 1)
    if interaction_gap >= SCORE_EVENT_DELTA_THRESHOLD:
        opportunities.append({
            "type": "interaction",
            "component": "interaction",
            "title": "提出一个具体问题或回应同伴",
            "summary": "高质量互动会让修为更稳，也让老师更容易看见你的困惑。",
            "estimated_delta": min(interaction_gap, 3.0),
            "action_label": "去互动",
            "action_url": f"{classroom_url}#chat-messages" if classroom_url else "",
        })

    consistency_gap = round(max(0.0, 1 - clamp(safe_float(interactions.get("consistency_ratio")))) * consistency_weight, 1)
    if consistency_gap >= SCORE_EVENT_DELTA_THRESHOLD:
        opportunities.append({
            "type": "consistency",
            "component": "consistency",
            "title": "保持一次完整学习停留",
            "summary": "持续阅读、查看任务和参与课堂会补足稳定投入分。",
            "estimated_delta": min(consistency_gap, 1.5),
            "action_label": "继续学习",
            "action_url": classroom_url,
        })

    opportunities.sort(key=lambda item: (-safe_float(item.get("estimated_delta")), str(item.get("title") or "")))
    return opportunities[: max(1, int(limit or 4))]


def _component_event_type(component: str, delta: float, source_ref: str) -> str:
    if delta < 0:
        return "recalibration"
    if component == "material":
        return "material_progress"
    if component == "task":
        return "submission_graded" if source_ref.startswith(("submission:", "grading:")) else "task_progress"
    if component == "interaction":
        return "interaction"
    if component == "consistency":
        return "consistency"
    return "recalibration"


def _load_learning_progress_snapshot(conn, class_offering_id: int, student_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT *
        FROM learning_progress_snapshots
        WHERE class_offering_id = ? AND student_id = ?
        LIMIT 1
        """,
        (int(class_offering_id), int(student_id)),
    ).fetchone()
    return dict(row) if row else None


def _snapshot_components(row: dict[str, Any] | None) -> dict[str, float]:
    if not row:
        return {}
    components = json_loads(row.get("components_json"), {})
    if not isinstance(components, dict):
        return {}
    return {str(key): safe_float(value) for key, value in components.items()}


def _record_cultivation_score_events(
    conn,
    *,
    class_offering_id: int,
    student_id: int,
    previous_components: dict[str, float],
    current_components: dict[str, Any],
    source_ref: str,
    created_at: str,
    metrics: dict[str, Any] | None = None,
) -> None:
    weight_version = str((metrics or {}).get("weight_version") or CULTIVATION_WEIGHT_VERSION_DEFAULT)
    weights = _weights_from_metrics(metrics)
    for component in ("material", "task", "interaction", "consistency"):
        previous_value = safe_float(previous_components.get(component))
        current_value = safe_float(current_components.get(component))
        delta = round(current_value - previous_value, 1)
        if abs(delta) < SCORE_EVENT_DELTA_THRESHOLD:
            continue
        conn.execute(
            """
            INSERT INTO cultivation_score_events (
                class_offering_id, student_id, event_type, delta,
                component, source_ref, created_at, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(class_offering_id),
                int(student_id),
                _component_event_type(component, delta, source_ref),
                delta,
                component,
                source_ref,
                created_at,
                json.dumps(
                    {
                        "previous": round(previous_value, 1),
                        "current": round(current_value, 1),
                        "weight_version": weight_version,
                        "weights": weights,
                    },
                    ensure_ascii=False,
                ),
            ),
        )


def _build_learning_state_from_metrics(
    conn,
    class_offering_id: int,
    student_id: int,
    metrics: dict[str, Any],
    *,
    persist_stage_status: bool,
    timestamp: str | None = None,
) -> dict[str, Any]:
    certificates = _load_certificates(conn, class_offering_id, student_id)
    cert_by_stage = {normalize_level_key(item["stage_key"]): item for item in certificates}
    latest_attempts = _load_latest_attempts(conn, class_offering_id, student_id)
    timestamp = timestamp or now_iso()
    score = safe_float(metrics["score"])
    previous_passed = True
    stages: list[dict[str, Any]] = []
    eligible_stage: Optional[dict[str, Any]] = None

    for level in LEARNING_LEVELS:
        key = level["key"]
        certificate = cert_by_stage.get(key)
        attempt = latest_attempts.get(key)
        active_attempt = attempt if attempt and str(attempt.get("status")) in {"generating", "generated", "submitted", "grading"} else None
        status = "locked"
        unlocked_at = None
        passed_at = None
        certificate_id = None
        last_exam_assignment_id = None
        if certificate:
            status = "passed"
            passed_at = certificate.get("issued_at")
            certificate_id = safe_int(certificate.get("id"))
            previous_passed = True
        elif not previous_passed:
            status = "locked"
        elif active_attempt:
            status = "generating" if str(active_attempt.get("status")) == "generating" else "in_exam"
            unlocked_at = active_attempt.get("generated_at")
            last_exam_assignment_id = safe_int(active_attempt.get("assignment_id")) or None
        elif score >= safe_float(level["unlock_score"]):
            status = "challenge_ready"
            unlocked_at = timestamp
        else:
            status = "available"

        progress_to_stage = clamp(score / max(safe_float(level["unlock_score"], 1), 1))
        stage_payload = {
            **level,
            "status": status,
            "progress_score": score,
            "progress_percent": int(round(progress_to_stage * 100)),
            "pass_score": PASSING_STAGE_SCORE,
            "certificate": certificate,
            "latest_attempt": attempt,
            "last_exam_assignment_id": last_exam_assignment_id,
        }
        if status == "challenge_ready" and eligible_stage is None:
            eligible_stage = stage_payload
        stages.append(stage_payload)

        metadata = {
            "level_name": level["name"],
            "unlock_score": level["unlock_score"],
            "pass_score": PASSING_STAGE_SCORE,
            "latest_attempt_id": attempt.get("id") if attempt else None,
            "component_scores": metrics["components"],
        }
        if persist_stage_status:
            conn.execute(
                """
                INSERT INTO learning_stage_status (
                    class_offering_id, student_id, stage_key, status,
                    progress_score, readiness_score, unlocked_at, passed_at,
                    last_exam_assignment_id, certificate_id, last_calculated_at, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(class_offering_id, student_id, stage_key)
                DO UPDATE SET
                    status = excluded.status,
                    progress_score = excluded.progress_score,
                    readiness_score = excluded.readiness_score,
                    unlocked_at = COALESCE(learning_stage_status.unlocked_at, excluded.unlocked_at),
                    passed_at = COALESCE(learning_stage_status.passed_at, excluded.passed_at),
                    last_exam_assignment_id = excluded.last_exam_assignment_id,
                    certificate_id = excluded.certificate_id,
                    last_calculated_at = excluded.last_calculated_at,
                    metadata_json = excluded.metadata_json
                """,
                (
                    class_offering_id,
                    student_id,
                    key,
                    status,
                    score,
                    progress_to_stage * 100,
                    unlocked_at,
                    passed_at,
                    last_exam_assignment_id,
                    certificate_id,
                    timestamp,
                    json.dumps(metadata, ensure_ascii=False),
                ),
            )
        if not certificate:
            previous_passed = False

    highest_certificate = certificates[-1] if certificates else None
    latest_unrevealed_certificate = next(
        (item for item in reversed(certificates) if item.get("needs_reveal")),
        None,
    )
    next_stage = next((item for item in stages if item["status"] != "passed"), None)
    if next_stage:
        progress_percent = next_stage["progress_percent"]
    else:
        progress_percent = 100
    state = {
        "score": score,
        "progress_percent": progress_percent,
        "current_level": highest_certificate or get_starter_level(),
        "next_stage": next_stage,
        "eligible_stage": eligible_stage,
        "stages": stages,
        "certificates": certificates,
        "latest_certificate": certificates[-1] if certificates else None,
        "latest_unrevealed_certificate": latest_unrevealed_certificate,
        "metrics": metrics,
        "rules": {
            "score_weights": weight_rules_from_weights(_weights_from_metrics(metrics)),
            "weight_version": str(metrics.get("weight_version") or CULTIVATION_WEIGHT_VERSION_DEFAULT),
            "weight_source": str(metrics.get("weight_source") or "default"),
            "pass_score": PASSING_STAGE_SCORE,
            "fairness_note": "修为由材料研读、任务通关、课堂互动和稳定投入共同凝聚",
        },
    }
    state["score_opportunities"] = build_score_opportunities(
        metrics,
        next_stage,
        class_offering_id=int(class_offering_id),
    )
    return state


def _upsert_learning_progress_snapshot(
    conn,
    *,
    class_offering_id: int,
    student_id: int,
    state: dict[str, Any],
    calculated_at: str,
) -> None:
    metrics = state.get("metrics") or {}
    current_level = public_level_payload(state.get("current_level"))
    next_stage = state.get("next_stage") or {}
    conn.execute(
        """
        INSERT INTO learning_progress_snapshots (
            class_offering_id, student_id, score, progress_percent,
            components_json, metrics_json, level_key, next_stage_key,
            calculated_at, dirty, dirty_at, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?)
        ON CONFLICT(class_offering_id, student_id)
        DO UPDATE SET
            score = excluded.score,
            progress_percent = excluded.progress_percent,
            components_json = excluded.components_json,
            metrics_json = excluded.metrics_json,
            level_key = excluded.level_key,
            next_stage_key = excluded.next_stage_key,
            calculated_at = excluded.calculated_at,
            dirty = 0,
            dirty_at = NULL,
            metadata_json = excluded.metadata_json
        """,
        (
            int(class_offering_id),
            int(student_id),
            safe_float(state.get("score")),
            safe_int(state.get("progress_percent")),
            json.dumps(metrics.get("components") or {}, ensure_ascii=False),
            json.dumps(metrics, ensure_ascii=False),
            current_level.get("key") or current_level.get("level_key") or "mortal",
            next_stage.get("key"),
            calculated_at,
            json.dumps(
                {
                    "snapshot_version": 2,
                    "weight_version": metrics.get("weight_version") or CULTIVATION_WEIGHT_VERSION_DEFAULT,
                    "weights": _weights_from_metrics(metrics),
                },
                ensure_ascii=False,
            ),
        ),
    )


def refresh_student_learning_state(
    conn,
    class_offering_id: int,
    student_id: int,
    *,
    event_source_ref: str = "recalculation",
) -> dict[str, Any]:
    previous_snapshot = _load_learning_progress_snapshot(conn, int(class_offering_id), int(student_id))
    previous_components = _snapshot_components(previous_snapshot)
    timestamp = now_iso()
    metrics = _build_learning_metrics(conn, class_offering_id, student_id)
    state = _build_learning_state_from_metrics(
        conn,
        int(class_offering_id),
        int(student_id),
        metrics,
        persist_stage_status=True,
        timestamp=timestamp,
    )
    _record_cultivation_score_events(
        conn,
        class_offering_id=int(class_offering_id),
        student_id=int(student_id),
        previous_components=previous_components,
            current_components=metrics.get("components") or {},
            source_ref=event_source_ref,
            created_at=timestamp,
            metrics=metrics,
        )
    _upsert_learning_progress_snapshot(
        conn,
        class_offering_id=int(class_offering_id),
        student_id=int(student_id),
        state=state,
        calculated_at=timestamp,
    )
    return state


def mark_student_learning_progress_dirty(
    conn,
    class_offering_id: int,
    student_id: int,
    *,
    source_ref: str = "dirty",
) -> None:
    timestamp = now_iso()
    conn.execute(
        """
        INSERT INTO learning_progress_snapshots (
            class_offering_id, student_id, score, progress_percent,
            components_json, metrics_json, level_key, calculated_at,
            dirty, dirty_at, metadata_json
        )
        VALUES (?, ?, 0, 0, '{}', '{}', 'mortal', ?, 1, ?, ?)
        ON CONFLICT(class_offering_id, student_id)
        DO UPDATE SET
            dirty = 1,
            dirty_at = excluded.dirty_at,
            metadata_json = excluded.metadata_json
        """,
        (
            int(class_offering_id),
            int(student_id),
            timestamp,
            timestamp,
            json.dumps({"dirty_source_ref": source_ref}, ensure_ascii=False),
        ),
    )


def _active_student_ids_for_offering(conn, class_offering_id: int) -> list[int]:
    offering = _load_offering(conn, int(class_offering_id))
    if not offering:
        return []
    rows = conn.execute(
        """
        SELECT id
        FROM students
        WHERE class_id = ?
          AND COALESCE(enrollment_status, 'active') = 'active'
        ORDER BY student_id_number, id
        """,
        (offering["class_id"],),
    ).fetchall()
    return [int(row["id"]) for row in rows]


def get_class_cultivation_weight_settings(conn, class_offering_id: int) -> dict[str, Any]:
    config = load_cultivation_weight_config(conn, int(class_offering_id))
    return build_weight_settings_payload(config)


def preview_class_cultivation_weights(
    conn,
    class_offering_id: int,
    weights_payload: Any,
    *,
    limit: int = 8,
) -> dict[str, Any]:
    weights = normalize_cultivation_weights(weights_payload)
    current_config = load_cultivation_weight_config(conn, int(class_offering_id))
    preview_config = {
        **current_config,
        "weights": weights,
        "rules": weight_rules_from_weights(weights),
        "version": f"preview:{current_config.get('version') or CULTIVATION_WEIGHT_VERSION_DEFAULT}",
        "source": "preview",
    }
    rows: list[dict[str, Any]] = []
    old_total = 0.0
    new_total = 0.0
    affected_count = 0
    student_ids = _active_student_ids_for_offering(conn, int(class_offering_id))
    for student_id in student_ids:
        old_metrics = _build_learning_metrics(conn, int(class_offering_id), student_id, weight_config=current_config)
        new_metrics = _build_learning_metrics(conn, int(class_offering_id), student_id, weight_config=preview_config)
        old_score = safe_float(old_metrics.get("score"))
        new_score = safe_float(new_metrics.get("score"))
        delta = round(new_score - old_score, 1)
        old_total += old_score
        new_total += new_score
        if abs(delta) >= SCORE_EVENT_DELTA_THRESHOLD:
            affected_count += 1
        student = _load_student(conn, student_id) or {}
        rows.append({
            "student_id": student_id,
            "name": student.get("name") or f"学生 {student_id}",
            "old_score": round(old_score, 1),
            "new_score": round(new_score, 1),
            "delta": delta,
            "delta_label": f"{delta:+.1f}",
        })
    rows.sort(key=lambda item: (-abs(safe_float(item.get("delta"))), str(item.get("name") or "")))
    student_count = len(student_ids)
    old_average = round(old_total / student_count, 1) if student_count else 0.0
    new_average = round(new_total / student_count, 1) if student_count else 0.0
    average_delta = round(new_average - old_average, 1)
    return {
        "status": "success",
        "weights": weights,
        "rules": weight_rules_from_weights(weights),
        "student_count": student_count,
        "affected_count": affected_count,
        "old_average": old_average,
        "new_average": new_average,
        "average_delta": average_delta,
        "average_delta_label": f"{average_delta:+.1f}",
        "students_preview": rows[: max(1, min(int(limit or 8), 20))],
    }


def update_class_cultivation_weights(
    conn,
    class_offering_id: int,
    *,
    teacher_id: int,
    weights_payload: Any,
) -> dict[str, Any]:
    weights = normalize_cultivation_weights(weights_payload)
    current_config = load_cultivation_weight_config(conn, int(class_offering_id))
    timestamp = now_iso()
    settings = build_weight_settings_payload(current_config, now=timestamp)
    if weights == current_config.get("weights"):
        return {
            "status": "success",
            "updated": False,
            "message": "修为权重未变化",
            "weight_settings": settings,
            "dirty_count": 0,
            "recalibration_event_count": 0,
        }
    if not settings.get("can_update"):
        raise CultivationWeightValidationError(
            f"修为权重每 {CULTIVATION_WEIGHT_COOLDOWN_DAYS} 天最多调整一次，请在冷却期后再修改"
        )
    student_ids = _active_student_ids_for_offering(conn, int(class_offering_id))
    new_config = save_cultivation_weight_config(
        conn,
        int(class_offering_id),
        teacher_id=int(teacher_id),
        weights=weights,
        previous_config=current_config,
        timestamp=timestamp,
    )
    source_ref = f"weights:{new_config['version']}"
    recalibration_events = 0
    for student_id in student_ids:
        mark_student_learning_progress_dirty(
            conn,
            int(class_offering_id),
            student_id,
            source_ref=source_ref,
        )
        conn.execute(
            """
            INSERT INTO cultivation_score_events (
                class_offering_id, student_id, event_type, delta,
                component, source_ref, created_at, metadata_json
            )
            VALUES (?, ?, 'recalibration', 0, 'total', ?, ?, ?)
            """,
            (
                int(class_offering_id),
                student_id,
                source_ref,
                timestamp,
                json.dumps(
                    {
                        "reason": "cultivation_weight_update",
                        "teacher_id": int(teacher_id),
                        "previous_weight_version": current_config.get("version") or CULTIVATION_WEIGHT_VERSION_DEFAULT,
                        "weight_version": new_config.get("version"),
                        "previous_weights": current_config.get("weights") or DEFAULT_CULTIVATION_WEIGHTS,
                        "weights": weights,
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        recalibration_events += 1
    return {
        "status": "success",
        "updated": True,
        "message": "修为权重已更新，班级修为快照将按新权重重新计算",
        "weight_settings": build_weight_settings_payload(new_config, now=timestamp),
        "dirty_count": len(student_ids),
        "recalibration_event_count": recalibration_events,
    }


def get_student_learning_state(
    conn,
    class_offering_id: int,
    student_id: int,
    *,
    allow_recalculate: bool = True,
) -> dict[str, Any]:
    snapshot = _load_learning_progress_snapshot(conn, int(class_offering_id), int(student_id))
    if not snapshot or safe_int(snapshot.get("dirty")):
        if allow_recalculate:
            source_ref = "snapshot:initial" if not snapshot else str(
                (json_loads(snapshot.get("metadata_json"), {}) or {}).get("dirty_source_ref") or "snapshot:dirty"
            )
            return refresh_student_learning_state(
                conn,
                int(class_offering_id),
                int(student_id),
                event_source_ref=source_ref,
            )
        metrics = {"score": 0, "components": {}, "material": {}, "assignments": {}, "interactions": {}}
    else:
        metrics = json_loads(snapshot.get("metrics_json"), {})
        if not isinstance(metrics, dict) or not isinstance(metrics.get("components"), dict):
            if allow_recalculate:
                return refresh_student_learning_state(
                    conn,
                    int(class_offering_id),
                    int(student_id),
                    event_source_ref="snapshot:repair",
                )
            metrics = {"score": safe_float(snapshot.get("score")), "components": {}, "material": {}, "assignments": {}, "interactions": {}}

    return _build_learning_state_from_metrics(
        conn,
        int(class_offering_id),
        int(student_id),
        metrics,
        persist_stage_status=False,
    )


def recalculate_dirty_learning_progress_snapshots(conn, *, limit: int = 100) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT class_offering_id, student_id
        FROM learning_progress_snapshots
        WHERE dirty = 1
        ORDER BY dirty_at ASC, id ASC
        LIMIT ?
        """,
        (max(1, int(limit or 100)),),
    ).fetchall()
    refreshed = 0
    for row in rows:
        refresh_student_learning_state(
            conn,
            int(row["class_offering_id"]),
            int(row["student_id"]),
            event_source_ref="snapshot:dirty-batch",
        )
        refreshed += 1
    return {"checked": len(rows), "refreshed": refreshed}


def ensure_cultivation_snapshot_refresh_task(conn) -> int:
    from .scheduled_task_service import schedule_task

    return schedule_task(
        conn,
        task_kind=CULTIVATION_SNAPSHOT_REFRESH_TASK_KIND,
        run_at=datetime.now() + timedelta(seconds=60),
        payload={"limit": 100},
        dedupe_key="cultivation:snapshot-refresh",
        recurrence_seconds=CULTIVATION_SNAPSHOT_REFRESH_INTERVAL_SECONDS,
        title="修为快照脏行刷新",
        priority=70,
        max_attempts=3,
        replace=False,
    )


def _coerce_date(value: Any | None = None) -> date:
    if value is None:
        return datetime.now().date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return datetime.now().date()
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return datetime.now().date()


def _coerce_datetime(value: Any | None = None) -> datetime:
    if value is None:
        return datetime.now()
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    text = str(value or "").strip()
    if not text:
        return datetime.now()
    normalized = text[:-1] if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        try:
            return datetime.combine(datetime.fromisoformat(normalized[:10]).date(), datetime.min.time())
        except ValueError:
            return datetime.now()


def _week_start_date(value: Any | None = None) -> date:
    anchor = _coerce_date(value)
    return anchor - timedelta(days=anchor.weekday())


def _week_window(value: Any | None = None) -> tuple[str, str]:
    week_start = _week_start_date(value)
    week_end = week_start + timedelta(days=6)
    return week_start.isoformat(), week_end.isoformat()


def _sparkline_payload(points: list[dict[str, Any]], *, value_key: str = "score") -> dict[str, Any]:
    width = 220
    height = 64
    pad_x = 8
    pad_y = 8
    values = [safe_float(point.get(value_key)) for point in points]
    if not values:
        return {"view_box": f"0 0 {width} {height}", "points": "", "area_points": "", "min": 0, "max": 0}
    min_value = min(values)
    max_value = max(values)
    value_range = max(max_value - min_value, 1.0)
    usable_width = width - pad_x * 2
    usable_height = height - pad_y * 2
    coords: list[str] = []
    for index, value in enumerate(values):
        if len(values) == 1:
            x = width / 2
        else:
            x = pad_x + usable_width * index / max(len(values) - 1, 1)
        y = pad_y + (max_value - value) / value_range * usable_height
        coords.append(f"{x:.1f},{y:.1f}")
    area_points = ""
    if coords:
        first_x = coords[0].split(",", 1)[0]
        last_x = coords[-1].split(",", 1)[0]
        area_points = f"{first_x},{height - pad_y:.1f} {' '.join(coords)} {last_x},{height - pad_y:.1f}"
    return {
        "view_box": f"0 0 {width} {height}",
        "points": " ".join(coords),
        "area_points": area_points,
        "min": round(min_value, 1),
        "max": round(max_value, 1),
    }


def capture_cultivation_weekly_snapshots(
    conn,
    *,
    class_offering_id: int | None = None,
    week_start: Any | None = None,
    refresh_current: bool = True,
) -> dict[str, Any]:
    week_start_text, week_end_text = _week_window(week_start)
    if class_offering_id:
        offering_rows = [{"id": int(class_offering_id)}]
    else:
        offering_rows = [
            dict(row)
            for row in conn.execute("SELECT id FROM class_offerings ORDER BY id").fetchall()
        ]

    result = {
        "week_start": week_start_text,
        "week_end": week_end_text,
        "offerings": len(offering_rows),
        "checked": 0,
        "refreshed": 0,
        "captured": 0,
        "skipped": 0,
    }
    timestamp = now_iso()
    for offering_row in offering_rows:
        offering_id = int(offering_row["id"])
        students = conn.execute(
            """
            SELECT s.id
            FROM students s
            JOIN class_offerings o ON o.class_id = s.class_id
            WHERE o.id = ?
              AND COALESCE(s.enrollment_status, 'active') = 'active'
            ORDER BY s.id
            """,
            (offering_id,),
        ).fetchall()
        for student_row in students:
            student_id = int(student_row["id"])
            result["checked"] += 1
            snapshot = _load_learning_progress_snapshot(conn, offering_id, student_id)
            if refresh_current and (not snapshot or safe_int(snapshot.get("dirty"))):
                refresh_student_learning_state(
                    conn,
                    offering_id,
                    student_id,
                    event_source_ref="snapshot:weekly",
                )
                result["refreshed"] += 1
                snapshot = _load_learning_progress_snapshot(conn, offering_id, student_id)
            if not snapshot:
                result["skipped"] += 1
                continue
            snapshot_metadata = json_loads(snapshot.get("metadata_json"), {})
            if not isinstance(snapshot_metadata, dict):
                snapshot_metadata = {}
            conn.execute(
                """
                INSERT INTO cultivation_weekly_snapshots (
                    class_offering_id, student_id, week_start, week_end,
                    score, progress_percent, components_json, level_key,
                    snapshot_source, created_at, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(class_offering_id, student_id, week_start)
                DO UPDATE SET
                    week_end = excluded.week_end,
                    score = excluded.score,
                    progress_percent = excluded.progress_percent,
                    components_json = excluded.components_json,
                    level_key = excluded.level_key,
                    snapshot_source = excluded.snapshot_source,
                    created_at = excluded.created_at,
                    metadata_json = excluded.metadata_json
                """,
                (
                    offering_id,
                    student_id,
                    week_start_text,
                    week_end_text,
                    safe_float(snapshot.get("score")),
                    safe_int(snapshot.get("progress_percent")),
                    snapshot.get("components_json") or "{}",
                    normalize_level_key(snapshot.get("level_key") or "mortal"),
                    "scheduled" if refresh_current else "snapshot",
                    timestamp,
                    json.dumps(
                        {
                            "snapshot_calculated_at": snapshot.get("calculated_at"),
                            "snapshot_dirty": safe_int(snapshot.get("dirty")),
                            "weight_version": snapshot_metadata.get("weight_version") or CULTIVATION_WEIGHT_VERSION_DEFAULT,
                            "weights": snapshot_metadata.get("weights") or DEFAULT_CULTIVATION_WEIGHTS,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            result["captured"] += 1
    return result


def ensure_cultivation_weekly_snapshot_task(conn) -> int:
    from .scheduled_task_service import schedule_task

    return schedule_task(
        conn,
        task_kind=CULTIVATION_WEEKLY_SNAPSHOT_TASK_KIND,
        run_at=datetime.now() + timedelta(seconds=120),
        payload={"refresh_current": True},
        dedupe_key="cultivation:weekly-snapshot",
        recurrence_seconds=CULTIVATION_WEEKLY_SNAPSHOT_INTERVAL_SECONDS,
        title="修为周快照沉淀",
        priority=68,
        max_attempts=3,
        replace=False,
    )


def build_student_cultivation_growth_trend(
    conn,
    class_offering_id: int,
    student_id: int,
    *,
    weeks: int = 8,
) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT week_start, week_end, score, progress_percent, components_json, level_key
        FROM cultivation_weekly_snapshots
        WHERE class_offering_id = ?
          AND student_id = ?
        ORDER BY week_start DESC
        LIMIT ?
        """,
        (int(class_offering_id), int(student_id), max(1, min(int(weeks or 8), 16))),
    ).fetchall()
    points: list[dict[str, Any]] = []
    for row in reversed(rows):
        item = dict(row)
        components = json_loads(item.get("components_json"), {})
        points.append({
            "week_start": item.get("week_start"),
            "week_end": item.get("week_end"),
            "score": round(safe_float(item.get("score")), 1),
            "progress_percent": safe_int(item.get("progress_percent")),
            "level_key": normalize_level_key(item.get("level_key") or "mortal"),
            "components": components if isinstance(components, dict) else {},
        })
    first_score = safe_float(points[0].get("score")) if points else 0.0
    last_score = safe_float(points[-1].get("score")) if points else 0.0
    previous_score = safe_float(points[-2].get("score")) if len(points) >= 2 else last_score
    total_delta = round(last_score - first_score, 1) if len(points) >= 2 else 0.0
    weekly_delta = round(last_score - previous_score, 1) if len(points) >= 2 else 0.0
    return {
        "weeks": len(points),
        "has_enough_data": len(points) >= 2,
        "points": points,
        "sparkline": _sparkline_payload(points),
        "total_delta": total_delta,
        "total_delta_label": f"{total_delta:+.1f}",
        "weekly_delta": weekly_delta,
        "weekly_delta_label": f"{weekly_delta:+.1f}",
    }


def build_class_cultivation_trend_summary(
    conn,
    class_offering_id: int,
    *,
    weeks: int = 8,
) -> dict[str, Any]:
    week_rows = conn.execute(
        """
        SELECT DISTINCT week_start
        FROM cultivation_weekly_snapshots
        WHERE class_offering_id = ?
        ORDER BY week_start DESC
        LIMIT ?
        """,
        (int(class_offering_id), max(1, min(int(weeks or 8), 16))),
    ).fetchall()
    week_starts = [str(row["week_start"]) for row in reversed(week_rows)]
    if not week_starts:
        return {
            "weeks": 0,
            "has_enough_data": False,
            "points": [],
            "sparkline": _sparkline_payload([]),
            "distribution_compare": [],
            "component_deltas": [],
            "stalled_students": [],
        }

    placeholders = ",".join("?" for _ in week_starts)
    rows = conn.execute(
        f"""
        SELECT student_id, week_start, score, progress_percent, components_json, level_key
        FROM cultivation_weekly_snapshots
        WHERE class_offering_id = ?
          AND week_start IN ({placeholders})
        ORDER BY week_start ASC, student_id ASC
        """,
        [int(class_offering_id), *week_starts],
    ).fetchall()
    by_week: dict[str, list[dict[str, Any]]] = {week: [] for week in week_starts}
    by_student_week: dict[int, dict[str, float]] = {}
    for row in rows:
        item = dict(row)
        week = str(item.get("week_start"))
        by_week.setdefault(week, []).append(item)
        by_student_week.setdefault(int(item["student_id"]), {})[week] = safe_float(item.get("score"))

    level_label_by_key = {"mortal": STARTER_LEVEL["short_name"]}
    for level in LEARNING_LEVELS:
        level_label_by_key[level["key"]] = level["short_name"]

    points: list[dict[str, Any]] = []
    for week in week_starts:
        items = by_week.get(week) or []
        count = len(items)
        score_total = sum(safe_float(item.get("score")) for item in items)
        component_totals = {"material": 0.0, "task": 0.0, "interaction": 0.0, "consistency": 0.0}
        distribution: dict[str, int] = {key: 0 for key in level_label_by_key}
        for item in items:
            components = json_loads(item.get("components_json"), {})
            if isinstance(components, dict):
                for key in component_totals:
                    component_totals[key] += safe_float(components.get(key))
            level_key = normalize_level_key(item.get("level_key") or "mortal")
            distribution[level_key] = distribution.get(level_key, 0) + 1
        average_components = {
            key: round(value / count, 1) if count else 0.0
            for key, value in component_totals.items()
        }
        points.append({
            "week_start": week,
            "student_count": count,
            "average_score": round(score_total / count, 1) if count else 0,
            "components": average_components,
            "distribution": [
                {
                    "key": key,
                    "name": level_label_by_key.get(key, key),
                    "count": value,
                    "percent": int(round(value / count * 100)) if count else 0,
                }
                for key, value in distribution.items()
            ],
        })

    current = points[-1]
    previous = points[-2] if len(points) >= 2 else None
    average_delta = round(safe_float(current.get("average_score")) - safe_float(previous.get("average_score")), 1) if previous else 0.0
    component_labels = {
        "material": "材料",
        "task": "任务",
        "interaction": "互动",
        "consistency": "稳定",
    }
    component_deltas: list[dict[str, Any]] = []
    for key, label in component_labels.items():
        current_value = safe_float((current.get("components") or {}).get(key))
        previous_value = safe_float((previous.get("components") or {}).get(key)) if previous else current_value
        delta = round(current_value - previous_value, 1) if previous else 0.0
        component_deltas.append({
            "key": key,
            "label": label,
            "value": round(current_value, 1),
            "delta": delta,
            "delta_label": f"{delta:+.1f}",
            "direction": "up" if delta > 0 else ("down" if delta < 0 else "flat"),
        })

    distribution_compare: list[dict[str, Any]] = []
    previous_distribution = {item["key"]: item for item in (previous or {}).get("distribution", [])} if previous else {}
    current_distribution = {item["key"]: item for item in current.get("distribution", [])}
    for key in level_label_by_key:
        cur_item = current_distribution.get(key, {"count": 0, "percent": 0})
        prev_item = previous_distribution.get(key, {"count": 0, "percent": 0})
        distribution_compare.append({
            "key": key,
            "name": level_label_by_key.get(key, key),
            "current_count": safe_int(cur_item.get("count")),
            "current_percent": safe_int(cur_item.get("percent")),
            "previous_count": safe_int(prev_item.get("count")),
            "previous_percent": safe_int(prev_item.get("percent")),
        })

    stalled_students: list[dict[str, Any]] = []
    if len(week_starts) >= 3:
        older_week, previous_week, current_week = week_starts[-3], week_starts[-2], week_starts[-1]
        student_rows = conn.execute(
            """
            SELECT s.id, s.name, s.student_id_number
            FROM students s
            JOIN class_offerings o ON o.class_id = s.class_id
            WHERE o.id = ?
              AND COALESCE(s.enrollment_status, 'active') = 'active'
            """,
            (int(class_offering_id),),
        ).fetchall()
        student_names = {int(row["id"]): dict(row) for row in student_rows}
        for student_id, score_by_week in by_student_week.items():
            if not all(week in score_by_week for week in (older_week, previous_week, current_week)):
                continue
            previous_delta = round(score_by_week[previous_week] - score_by_week[older_week], 1)
            current_delta = round(score_by_week[current_week] - score_by_week[previous_week], 1)
            if previous_delta > 0.05 and current_delta <= 0.05:
                student = student_names.get(student_id, {"name": "", "student_id_number": ""})
                stalled_students.append({
                    "student_id": student_id,
                    "name": student.get("name") or f"学生 {student_id}",
                    "student_id_number": student.get("student_id_number") or "",
                    "previous_delta": previous_delta,
                    "current_delta": current_delta,
                    "current_score": round(score_by_week[current_week], 1),
                })
    stalled_students.sort(key=lambda item: (safe_float(item.get("current_delta")), -safe_float(item.get("previous_delta"))))
    return {
        "weeks": len(points),
        "has_enough_data": len(points) >= 2,
        "points": points,
        "sparkline": _sparkline_payload(
            [{"week_start": item["week_start"], "score": item["average_score"]} for item in points]
        ),
        "average_delta": average_delta,
        "average_delta_label": f"{average_delta:+.1f}",
        "component_deltas": component_deltas,
        "distribution_compare": distribution_compare,
        "stalled_students": stalled_students[:8],
    }


def _level_short_name_by_key() -> dict[str, str]:
    names = {"mortal": STARTER_LEVEL["short_name"]}
    for level in LEARNING_LEVELS:
        names[level["key"]] = level["short_name"]
    return names


def _weekly_report_target_start(value: Any | None = None) -> date:
    if value is not None:
        return _week_start_date(value)
    return _week_start_date(datetime.now().date() - timedelta(days=7))


def create_cultivation_weekly_reports(
    conn,
    *,
    class_offering_id: int | None = None,
    week_start: Any | None = None,
) -> dict[str, Any]:
    target_start = _weekly_report_target_start(week_start)
    previous_start = target_start - timedelta(days=7)
    target_week_start = target_start.isoformat()
    target_week_end = (target_start + timedelta(days=6)).isoformat()
    previous_week_start = previous_start.isoformat()
    event_start = f"{target_week_start}T00:00:00"
    event_end = f"{(target_start + timedelta(days=7)).isoformat()}T00:00:00"
    level_names = _level_short_name_by_key()

    if class_offering_id:
        offering_rows = [{"id": int(class_offering_id)}]
    else:
        offering_rows = [
            dict(row)
            for row in conn.execute("SELECT id FROM class_offerings ORDER BY id").fetchall()
        ]

    result = {
        "week_start": target_week_start,
        "week_end": target_week_end,
        "offerings": len(offering_rows),
        "checked": 0,
        "created": 0,
        "duplicates": 0,
        "skipped": 0,
    }
    for offering_row in offering_rows:
        offering_id = int(offering_row["id"])
        rows = conn.execute(
            """
            SELECT s.id AS student_id,
                   s.name AS student_name,
                   cur.score AS current_score,
                   cur.components_json AS current_components_json,
                   cur.level_key AS current_level_key,
                   prev.score AS previous_score,
                   prev.components_json AS previous_components_json,
                   prev.level_key AS previous_level_key
            FROM students s
            JOIN class_offerings o ON o.class_id = s.class_id
            JOIN cultivation_weekly_snapshots cur
              ON cur.class_offering_id = o.id
             AND cur.student_id = s.id
             AND cur.week_start = ?
            LEFT JOIN cultivation_weekly_snapshots prev
              ON prev.class_offering_id = o.id
             AND prev.student_id = s.id
             AND prev.week_start = ?
            WHERE o.id = ?
              AND COALESCE(s.enrollment_status, 'active') = 'active'
            ORDER BY s.id
            """,
            (target_week_start, previous_week_start, offering_id),
        ).fetchall()
        for row in rows:
            item = dict(row)
            student_id = int(item["student_id"])
            result["checked"] += 1
            if item.get("previous_score") is None:
                result["skipped"] += 1
                continue
            event_row = conn.execute(
                """
                SELECT COUNT(*) AS event_count,
                       COALESCE(SUM(delta), 0) AS event_delta
                FROM cultivation_score_events
                WHERE class_offering_id = ?
                  AND student_id = ?
                  AND created_at >= ?
                  AND created_at < ?
                """,
                (offering_id, student_id, event_start, event_end),
            ).fetchone()
            event_count = safe_int(event_row["event_count"] if event_row else 0)
            current_score = safe_float(item.get("current_score"))
            previous_score = safe_float(item.get("previous_score"))
            score_delta = round(current_score - previous_score, 1)
            if score_delta <= 0.05 and event_count == 0:
                result["skipped"] += 1
                continue

            current_components = json_loads(item.get("current_components_json"), {})
            previous_components = json_loads(item.get("previous_components_json"), {})
            component_labels = {
                "material": "材料",
                "task": "任务",
                "interaction": "互动",
                "consistency": "稳定投入",
            }
            component_deltas = []
            if isinstance(current_components, dict) and isinstance(previous_components, dict):
                for key, label in component_labels.items():
                    delta = round(safe_float(current_components.get(key)) - safe_float(previous_components.get(key)), 1)
                    if delta > 0:
                        component_deltas.append((delta, label))
            component_deltas.sort(reverse=True)
            strongest_component = component_deltas[0][1] if component_deltas else "稳定推进"

            current_level_key = normalize_level_key(item.get("current_level_key") or "mortal")
            previous_level_key = normalize_level_key(item.get("previous_level_key") or "mortal")
            level_line = ""
            if current_level_key != previous_level_key:
                level_line = (
                    f"境界从 {level_names.get(previous_level_key, previous_level_key)} "
                    f"到 {level_names.get(current_level_key, current_level_key)}。"
                )

            advice = "保持材料、任务和互动的连续投入。"
            try:
                state = get_student_learning_state(conn, offering_id, student_id, allow_recalculate=False)
                opportunities = state.get("score_opportunities") or []
                if opportunities:
                    advice = str(opportunities[0].get("title") or advice)
            except Exception:
                advice = "保持材料、任务和互动的连续投入。"

            body = (
                f"本周修为 {score_delta:+.1f}，当前 {current_score:.1f}。"
                f"{level_line}"
                f"{event_count} 条修为流水，主要增长来自{strongest_component}。"
                f"下周优先：{advice}"
            )
            ref_id = f"cultivation-weekly-report:{offering_id}:{student_id}:{target_week_start}"
            inserted = create_learning_progress_notification(
                conn,
                recipient_role="student",
                recipient_user_pk=student_id,
                title="修行周报",
                body_preview=body,
                link_url=f"/classroom/{offering_id}",
                class_offering_id=offering_id,
                ref_id=ref_id,
                actor_role=AI_ASSISTANT_ROLE,
                actor_display_name=AI_ASSISTANT_LABEL,
                metadata={
                    "week_start": target_week_start,
                    "week_end": target_week_end,
                    "previous_week_start": previous_week_start,
                    "score_delta": score_delta,
                    "current_score": round(current_score, 1),
                    "event_count": event_count,
                    "rule_version": 1,
                },
            )
            if inserted:
                result["created"] += 1
            else:
                result["duplicates"] += 1
    return result


def ensure_cultivation_weekly_report_task(conn) -> int:
    from .scheduled_task_service import schedule_task

    return schedule_task(
        conn,
        task_kind=CULTIVATION_WEEKLY_REPORT_TASK_KIND,
        run_at=datetime.now() + timedelta(seconds=180),
        payload={},
        dedupe_key="cultivation:weekly-report",
        recurrence_seconds=CULTIVATION_WEEKLY_REPORT_INTERVAL_SECONDS,
        title="修行周报生成",
        priority=72,
        max_attempts=3,
        replace=False,
    )


def archive_cultivation_score_events(
    conn,
    *,
    retention_days: int = CULTIVATION_SCORE_EVENT_RETENTION_DAYS,
    as_of: Any | None = None,
    batch_limit: int = 500,
) -> dict[str, Any]:
    days = max(1, int(retention_days or CULTIVATION_SCORE_EVENT_RETENTION_DAYS))
    limit = max(1, min(int(batch_limit or 500), 5000))
    cutoff_dt = _coerce_datetime(as_of) - timedelta(days=days)
    cutoff_iso = cutoff_dt.isoformat(timespec="seconds")
    archived_at = datetime.now().isoformat(timespec="seconds")
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT class_offering_id,
                   student_id,
                   substr(created_at, 1, 7) AS archive_month,
                   event_type,
                   component,
                   COUNT(*) AS event_count,
                   COALESCE(SUM(delta), 0) AS total_delta,
                   MIN(created_at) AS first_event_at,
                   MAX(created_at) AS last_event_at
            FROM cultivation_score_events
            WHERE created_at < ?
            GROUP BY class_offering_id, student_id, archive_month, event_type, component
            ORDER BY archive_month, class_offering_id, student_id
            LIMIT ?
            """,
            (cutoff_iso, limit),
        ).fetchall()
    ]
    result = {
        "cutoff": cutoff_iso,
        "retention_days": days,
        "archive_rows": 0,
        "archived_events": 0,
        "deleted_events": 0,
    }
    for row in rows:
        offering_id = safe_int(row.get("class_offering_id"))
        student_id = safe_int(row.get("student_id"))
        archive_month = str(row.get("archive_month") or "")[:7]
        event_type = str(row.get("event_type") or "unknown")
        component = str(row.get("component") or "total")
        event_count = safe_int(row.get("event_count"))
        total_delta = round(safe_float(row.get("total_delta")), 3)
        first_event_at = str(row.get("first_event_at") or cutoff_iso)
        last_event_at = str(row.get("last_event_at") or cutoff_iso)
        if not offering_id or not student_id or not archive_month:
            continue
        metadata_json = json.dumps(
            {
                "source": "cultivation_score_events",
                "retention_days": days,
                "cutoff": cutoff_iso,
                "last_archived_at": archived_at,
            },
            ensure_ascii=False,
        )
        existing = conn.execute(
            """
            SELECT id, event_count, total_delta, first_event_at, last_event_at
            FROM cultivation_score_event_archives
            WHERE class_offering_id = ?
              AND student_id = ?
              AND archive_month = ?
              AND event_type = ?
              AND component = ?
            LIMIT 1
            """,
            (offering_id, student_id, archive_month, event_type, component),
        ).fetchone()
        if existing:
            existing_item = dict(existing)
            combined_count = safe_int(existing_item.get("event_count")) + event_count
            combined_delta = round(safe_float(existing_item.get("total_delta")) + total_delta, 3)
            combined_first = min(str(existing_item.get("first_event_at") or first_event_at), first_event_at)
            combined_last = max(str(existing_item.get("last_event_at") or last_event_at), last_event_at)
            conn.execute(
                """
                UPDATE cultivation_score_event_archives
                SET event_count = ?,
                    total_delta = ?,
                    first_event_at = ?,
                    last_event_at = ?,
                    archived_at = ?,
                    metadata_json = ?
                WHERE id = ?
                """,
                (
                    combined_count,
                    combined_delta,
                    combined_first,
                    combined_last,
                    archived_at,
                    metadata_json,
                    existing_item["id"],
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO cultivation_score_event_archives (
                    class_offering_id, student_id, archive_month, event_type, component,
                    event_count, total_delta, first_event_at, last_event_at, archived_at, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    offering_id,
                    student_id,
                    archive_month,
                    event_type,
                    component,
                    event_count,
                    total_delta,
                    first_event_at,
                    last_event_at,
                    archived_at,
                    metadata_json,
                ),
            )

        cursor = conn.execute(
            """
            DELETE FROM cultivation_score_events
            WHERE class_offering_id = ?
              AND student_id = ?
              AND substr(created_at, 1, 7) = ?
              AND event_type = ?
              AND component = ?
              AND created_at < ?
            """,
            (offering_id, student_id, archive_month, event_type, component, cutoff_iso),
        )
        deleted = max(0, int(getattr(cursor, "rowcount", 0) or 0))
        result["archive_rows"] += 1
        result["archived_events"] += event_count
        result["deleted_events"] += deleted
    return result


def ensure_cultivation_score_event_archive_task(conn) -> int:
    from .scheduled_task_service import schedule_task

    return schedule_task(
        conn,
        task_kind=CULTIVATION_SCORE_EVENT_ARCHIVE_TASK_KIND,
        run_at=datetime.now() + timedelta(seconds=240),
        payload={"retention_days": CULTIVATION_SCORE_EVENT_RETENTION_DAYS, "batch_limit": 500},
        dedupe_key="cultivation:score-event-archive",
        recurrence_seconds=CULTIVATION_SCORE_EVENT_ARCHIVE_INTERVAL_SECONDS,
        title="Cultivation score-event archive",
        priority=74,
        max_attempts=3,
        replace=False,
    )


def list_cultivation_score_events(
    conn,
    class_offering_id: int,
    student_id: int,
    *,
    limit: int = 30,
    days: int = 30,
) -> list[dict[str, Any]]:
    cutoff = (datetime.now() - timedelta(days=max(1, int(days or 30)))).isoformat(timespec="seconds")
    rows = conn.execute(
        """
        SELECT id, event_type, delta, component, source_ref, created_at, metadata_json
        FROM cultivation_score_events
        WHERE class_offering_id = ?
          AND student_id = ?
          AND created_at >= ?
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (int(class_offering_id), int(student_id), cutoff, max(1, min(int(limit or 30), 100))),
    ).fetchall()
    labels = {
        "material": "材料研读",
        "task": "作业考试",
        "interaction": "互动求助",
        "consistency": "稳定投入",
    }
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        delta = safe_float(item.get("delta"))
        component = str(item.get("component") or "total")
        metadata = json_loads(item.get("metadata_json"), {})
        items.append({
            "id": safe_int(item.get("id")),
            "event_type": item.get("event_type") or "recalibration",
            "delta": round(delta, 1),
            "delta_label": f"{delta:+.1f}",
            "component": component,
            "component_label": labels.get(component, "修为"),
            "source_ref": item.get("source_ref") or "",
            "created_at": item.get("created_at"),
            "metadata": metadata if isinstance(metadata, dict) else {},
        })
    return items


def _class_learning_score_rows(
    conn,
    class_offering_id: int,
    student_id: int,
    *,
    current_score: float | None = None,
) -> list[dict[str, Any]]:
    offering = _load_offering(conn, int(class_offering_id))
    if not offering:
        return []
    rows = conn.execute(
        """
        SELECT s.id,
               s.name,
               s.student_id_number,
               lps.score AS snapshot_score,
               lps.dirty AS snapshot_dirty
        FROM students s
        LEFT JOIN learning_progress_snapshots lps
               ON lps.class_offering_id = ?
              AND lps.student_id = s.id
        WHERE s.class_id = ?
          AND COALESCE(s.enrollment_status, 'active') = 'active'
        ORDER BY s.student_id_number, s.id
        """,
        (int(class_offering_id), offering["class_id"]),
    ).fetchall()
    score_rows: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item_student_id = int(item["id"])
        if item_student_id == int(student_id) and current_score is not None:
            score = safe_float(current_score)
        elif item.get("snapshot_score") is None or safe_int(item.get("snapshot_dirty")):
            state = get_student_learning_state(conn, int(class_offering_id), item_student_id)
            score = safe_float(state.get("score"))
        else:
            score = safe_float(item.get("snapshot_score"))
        score_rows.append({
            "id": item_student_id,
            "name": item.get("name") or "同学",
            "student_id_number": item.get("student_id_number"),
            "score": round(score, 1),
            "is_self": item_student_id == int(student_id),
        })
    return score_rows


def build_student_class_position(
    conn,
    class_offering_id: int,
    student_id: int,
    *,
    current_score: float | None = None,
) -> dict[str, Any] | None:
    score_rows = _class_learning_score_rows(
        conn,
        int(class_offering_id),
        int(student_id),
        current_score=current_score,
    )
    if not score_rows:
        return None

    ranked = sorted(
        score_rows,
        key=lambda item: (-safe_float(item.get("score")), str(item.get("student_id_number") or ""), int(item["id"])),
    )
    total = len(ranked)
    for index, item in enumerate(ranked, start=1):
        item["rank"] = index
    current = next((item for item in ranked if item.get("is_self")), None)
    if current is None:
        return None

    leader = ranked[0]
    ascending = sorted(
        ranked,
        key=lambda item: (safe_float(item.get("score")), int(item["rank"]), int(item["id"])),
    )
    min_score = safe_float(ascending[0].get("score")) if ascending else 0.0
    max_score = safe_float(ascending[-1].get("score")) if ascending else 0.0
    x_by_id: dict[int, float] = {}
    points: list[dict[str, Any]] = []
    denominator = max(len(ascending) - 1, 1)
    for index, item in enumerate(ascending):
        x_percent = 50.0 if len(ascending) == 1 else round(index / denominator * 100, 2)
        x_by_id[int(item["id"])] = x_percent
        points.append({
            "x": x_percent,
            "score": safe_float(item.get("score")),
            "rank": int(item["rank"]),
            "is_self": bool(item.get("is_self")),
            "is_top": int(item["id"]) == int(leader["id"]),
        })

    rank = int(current["rank"])
    top_percent = max(1, int(math.ceil(rank / total * 100))) if total else 0
    surpass_percent = int(round((total - rank) / max(total - 1, 1) * 100)) if total > 1 else 100
    return {
        "total": total,
        "current": {
            "name": current.get("name") or "您",
            "rank": rank,
            "score": safe_float(current.get("score")),
            "top_percent": top_percent,
            "surpass_percent": surpass_percent,
            "x": x_by_id.get(int(current["id"]), 50.0),
        },
        "leader": {
            "name": leader.get("name") or "同学",
            "score": safe_float(leader.get("score")),
            "x": x_by_id.get(int(leader["id"]), 50.0),
            "is_self": bool(leader.get("is_self")),
        },
        "mountain": {
            "min_score": min_score,
            "max_score": max_score,
            "points": points,
        },
    }


def build_cultivation_rank_notice(
    class_position: dict[str, Any] | None,
    *,
    sect_name: Any = "",
) -> dict[str, Any] | None:
    if not class_position:
        return None
    current = class_position.get("current") or {}
    total = safe_int(class_position.get("total"))
    rank = safe_int(current.get("rank"))
    if total <= 0 or rank <= 0:
        return None

    top_percent = safe_int(current.get("top_percent"), 100)
    surpass_percent = safe_int(current.get("surpass_percent"))
    sect_label = normalize_course_sect_name(sect_name)
    if total == 1:
        tier = "summit"
        title = "登顶"
        message = f"你的修为已登顶{sect_label}，暂居首席！"
    elif rank == 1:
        tier = "summit"
        title = "登顶"
        message = f"你的修为已登顶{sect_label}，稳坐宗门榜首！"
    elif top_percent <= 25:
        tier = "front"
        title = "位列前茅"
        message = f"你的修为在{sect_label}位列前茅，已入前 {top_percent}%！"
    elif top_percent <= 60:
        tier = "middle"
        title = "仍有进步空间"
        message = f"你的修为在{sect_label}仍有进步空间，继续精进可入前列。"
    else:
        tier = "training"
        title = "尚需努力"
        message = f"你的修为在{sect_label}尚需努力，稳住每日修行即可破局。"

    return {
        "tier": tier,
        "title": title,
        "message": message,
        "rank": rank,
        "total": total,
        "top_percent": top_percent,
        "surpass_percent": surpass_percent,
        "scope_label": sect_label,
    }


def serialize_student_learning_progress(conn, class_offering_id: int, student_id: int) -> dict[str, Any]:
    state = get_student_learning_state(conn, int(class_offering_id), int(student_id))
    state["class_position"] = build_student_class_position(
        conn,
        int(class_offering_id),
        int(student_id),
        current_score=safe_float(state.get("score")),
    )
    state["recent_score_events"] = list_cultivation_score_events(
        conn,
        int(class_offering_id),
        int(student_id),
        limit=6,
    )
    state["growth_trend"] = build_student_cultivation_growth_trend(
        conn,
        int(class_offering_id),
        int(student_id),
        weeks=8,
    )
    return state


def build_student_global_cultivation_profile(conn, student_id: int) -> dict[str, Any]:
    student = _load_student(conn, int(student_id)) or {"id": int(student_id), "name": ""}
    rows = conn.execute(
        """
        SELECT o.id AS class_offering_id,
               c.id AS course_id,
               c.name AS course_name,
               c.sect_name AS course_sect_name,
               cl.name AS class_name,
               t.name AS teacher_name
        FROM class_offerings o
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        JOIN teachers t ON t.id = o.teacher_id
        WHERE o.class_id = (
            SELECT class_id
            FROM students
            WHERE id = ?
              AND COALESCE(enrollment_status, 'active') = 'active'
        )
        ORDER BY o.id DESC
        """,
        (int(student_id),),
    ).fetchall()

    course_items: list[dict[str, Any]] = []
    selected: dict[str, Any] | None = None
    latest_unrevealed_certificate: dict[str, Any] | None = None
    for row in rows:
        offering = dict(row)
        progress = get_student_learning_state(conn, int(offering["class_offering_id"]), int(student_id))
        current_level = public_level_payload(progress.get("current_level"))
        next_stage = progress.get("next_stage")
        candidate_certificate = progress.get("latest_unrevealed_certificate")
        if candidate_certificate:
            candidate = {
                **candidate_certificate,
                "class_offering_id": int(offering["class_offering_id"]),
                "course_id": safe_int(offering.get("course_id")),
                "course_name": offering.get("course_name") or "课堂",
                "class_name": offering.get("class_name") or "",
                "student_name": student.get("name") or "",
            }
            candidate_sort = str(candidate.get("issued_at") or "")
            current_sort = str((latest_unrevealed_certificate or {}).get("issued_at") or "")
            if latest_unrevealed_certificate is None or candidate_sort >= current_sort:
                latest_unrevealed_certificate = candidate
        item = {
            "class_offering_id": int(offering["class_offering_id"]),
            "course_id": safe_int(offering.get("course_id")),
            "course_name": offering.get("course_name") or "课堂",
            "sect_name": normalize_course_sect_name(
                offering.get("course_sect_name"),
                course_name=offering.get("course_name"),
            ),
            "class_name": offering.get("class_name") or "",
            "teacher_name": offering.get("teacher_name") or "",
            "score": progress["score"],
            "progress_percent": progress["progress_percent"],
            "current_level": current_level,
            "next_stage": next_stage,
            "eligible_stage": progress.get("eligible_stage"),
            "certificate_count": len(progress.get("certificates") or []),
            "stages": [
                {
                    "key": stage["key"],
                    "short_name": stage["short_name"],
                    "status": stage["status"],
                    "theme": stage.get("theme") or stage["key"],
                    "progress_percent": stage["progress_percent"],
                }
                for stage in progress.get("stages") or []
            ],
        }
        item["sect_level_label"] = f"{item['sect_name']} · {current_level['short_name']}"
        course_items.append(item)
        if selected is None:
            selected = item
            continue
        item_rank = (safe_int(current_level.get("tier")), safe_float(item.get("score")))
        selected_rank = (
            safe_int(selected["current_level"].get("tier")),
            safe_float(selected.get("score")),
        )
        if item_rank > selected_rank:
            selected = item

    if selected is None:
        current_level = get_starter_level()
        selected = {
            "class_offering_id": None,
            "course_id": None,
            "course_name": "尚未加入课堂",
            "class_name": student.get("class_name") or "",
            "teacher_name": "",
            "score": 0,
            "progress_percent": 0,
            "current_level": current_level,
            "sect_name": "课堂宗",
            "sect_level_label": "课堂宗 · 未入道",
            "next_stage": LEARNING_LEVELS[0] if LEARNING_LEVELS else None,
            "eligible_stage": None,
            "certificate_count": 0,
        }
    else:
        current_level = public_level_payload(selected.get("current_level"))

    sorted_courses = sorted(
        course_items,
        key=lambda item: (
            safe_int(item["current_level"].get("tier")),
            safe_float(item.get("score")),
        ),
        reverse=True,
    )
    student_name = student.get("name") or ""
    progress_percent = int(round(clamp(safe_float(selected.get("progress_percent")) / 100) * 100))
    score = round(safe_float(selected.get("score")), 1)
    sect_name = normalize_course_sect_name(
        selected.get("sect_name"),
        course_name=selected.get("course_name"),
    )
    sect_level_label = f"{sect_name} · {current_level['short_name']}"
    class_position = (
        build_student_class_position(
            conn,
            int(selected["class_offering_id"]),
            int(student_id),
            current_score=score,
        )
        if selected.get("class_offering_id")
        else None
    )
    rank_notice = build_cultivation_rank_notice(class_position, sect_name=sect_name)
    next_stage = selected.get("next_stage")
    next_name = str(next_stage.get("name") if isinstance(next_stage, dict) else "") if next_stage else ""
    selected_status = str(next_stage.get("status") if isinstance(next_stage, dict) else "")
    breakthrough_ready = selected_status == "challenge_ready" or bool(selected.get("eligible_stage"))
    generating_stage_exam = selected_status == "generating"
    progress_label = "破境进度" if breakthrough_ready else ("试炼生成中" if generating_stage_exam else "修为进度")
    if breakthrough_ready and next_name:
        reveal_title = f"可破境 · {next_name}"
        reveal_subtitle = (
            f"{format_cultivation_address(student_name, current_level)}，"
            f"{selected.get('course_name') or '课堂'} 已可挑战 {next_name}"
        )
    elif generating_stage_exam and next_name:
        reveal_title = f"试炼生成中 · {next_name}"
        reveal_subtitle = (
            f"{format_cultivation_address(student_name, current_level)}，"
            f"{selected.get('course_name') or '课堂'} 正在准备破境试炼"
        )
    else:
        reveal_title = f"{current_level['aura_label']} · {current_level['level_name']}"
        reveal_subtitle = (
            f"{format_cultivation_address(student_name, current_level)}，"
            f"{selected.get('course_name') or '课堂'} 修为 {score:g} / 100"
        )
    return {
        "student_id": int(student_id),
        "student_name": student_name,
        "address_name": format_cultivation_address(student_name, current_level),
        "highest_level": current_level,
        "avatar_theme": current_level["theme"],
        "score": score,
        "progress_percent": progress_percent,
        "course_count": len(course_items),
        "certificate_count": sum(safe_int(item.get("certificate_count")) for item in course_items),
        "breakthrough_course_count": sum(1 for item in course_items if item.get("eligible_stage")),
        "best_course": {
            "class_offering_id": selected.get("class_offering_id"),
            "course_id": selected.get("course_id"),
            "course_name": selected.get("course_name") or "课堂",
            "sect_name": sect_name,
            "sect_level_label": sect_level_label,
            "class_name": selected.get("class_name") or "",
            "score": score,
            "progress_percent": progress_percent,
            "next_stage_name": next_name,
            "breakthrough_ready": breakthrough_ready,
            "generating_stage_exam": generating_stage_exam,
            "rank_notice": rank_notice,
        },
        "courses": sorted_courses,
        "sect_name": sect_name,
        "sect_level_label": sect_level_label,
        "rank_notice": rank_notice,
        "next_stage_name": next_name,
        "progress_label": progress_label,
        "breakthrough_ready": breakthrough_ready,
        "generating_stage_exam": generating_stage_exam,
        "reveal_title": reveal_title,
        "reveal_subtitle": reveal_subtitle,
        "latest_unrevealed_certificate": latest_unrevealed_certificate,
    }


def build_student_public_cultivation_badge(conn, student_id: int) -> dict[str, Any] | None:
    profile = build_student_global_cultivation_profile(conn, int(student_id))
    best_course = profile.get("best_course") or {}
    highest_level = public_level_payload(profile.get("highest_level"))
    if not best_course.get("class_offering_id"):
        return None

    sect_name = normalize_course_sect_name(
        best_course.get("sect_name"),
        course_name=best_course.get("course_name"),
    )
    label = f"{sect_name} · {highest_level['short_name']}"
    return {
        "label": label,
        "sect_name": sect_name,
        "level_name": highest_level["level_name"],
        "short_name": highest_level["short_name"],
        "tier": highest_level["tier"],
        "theme": highest_level["theme"],
        "score": profile.get("score", 0),
        "progress_percent": profile.get("progress_percent", 0),
        "course_name": best_course.get("course_name") or "",
        "class_offering_id": best_course.get("class_offering_id"),
    }


def build_class_learning_overview(conn, class_offering_id: int) -> dict[str, Any]:
    offering = _load_offering(conn, int(class_offering_id))
    if not offering:
        return {"student_count": 0, "average_score": 0, "distribution": [], "students": []}
    weight_settings = get_class_cultivation_weight_settings(conn, int(class_offering_id))
    rows = conn.execute(
        """
        SELECT id, name, student_id_number
        FROM students
        WHERE class_id = ?
          AND COALESCE(enrollment_status, 'active') = 'active'
        ORDER BY student_id_number, id
        """,
        (offering["class_id"],),
    ).fetchall()
    student_ids = [int(row["id"]) for row in rows]
    behavior_state_by_student: dict[int, dict[str, Any]] = {}
    behavior_event_by_student: dict[int, dict[str, Any]] = {}
    chat_count_by_student: dict[int, int] = {}
    shared_note_student_ids: set[int] = set()
    alert_context: dict[str, Any] = {"alerts_by_student": {}, "total_count": 0, "student_count": 0, "counts": {}}
    if student_ids:
        from .cultivation_alert_service import build_class_cultivation_alert_context

        alert_context = build_class_cultivation_alert_context(conn, int(class_offering_id))
        state_rows = conn.execute(
            """
            SELECT user_pk,
                   total_activity_count,
                   online_accumulated_seconds,
                   focus_total_seconds,
                   last_page_key,
                   last_event_at
            FROM classroom_behavior_states
            WHERE class_offering_id = ?
              AND user_role = 'student'
            """,
            (int(class_offering_id),),
        ).fetchall()
        behavior_state_by_student = {int(row["user_pk"]): dict(row) for row in state_rows}

        event_rows = conn.execute(
            """
            SELECT user_pk,
                   COUNT(*) AS event_count,
                   SUM(CASE WHEN action_type = 'ai_question' THEN 1 ELSE 0 END) AS ai_question_count,
                   SUM(CASE WHEN action_type = 'peer_help' THEN 1 ELSE 0 END) AS peer_help_count,
                   MAX(created_at) AS last_event_at
            FROM classroom_behavior_events
            WHERE class_offering_id = ?
              AND user_role = 'student'
            GROUP BY user_pk
            """,
            (int(class_offering_id),),
        ).fetchall()
        behavior_event_by_student = {int(row["user_pk"]): dict(row) for row in event_rows}

        placeholders = ",".join("?" for _ in student_ids)
        chat_rows = conn.execute(
            f"""
            SELECT user_id, COUNT(*) AS chat_message_count
            FROM chat_logs
            WHERE class_offering_id = ?
              AND user_role = 'student'
              AND user_id IN ({placeholders})
            GROUP BY user_id
            """,
            [int(class_offering_id), *[str(student_id) for student_id in student_ids]],
        ).fetchall()
        for row in chat_rows:
            try:
                chat_count_by_student[int(row["user_id"])] = safe_int(row["chat_message_count"])
            except (TypeError, ValueError):
                continue

        note_rows = conn.execute(
            f"""
            SELECT student_id
            FROM student_shared_teacher_notes
            WHERE student_id IN ({placeholders})
              AND TRIM(COALESCE(note_text, '')) != ''
            """,
            student_ids,
        ).fetchall()
        shared_note_student_ids = {int(row["student_id"]) for row in note_rows}

    students: list[dict[str, Any]] = []
    distribution = {level["key"]: 0 for level in LEARNING_LEVELS}
    distribution["mortal"] = 0
    challenge_ready_count = 0
    certificate_count = 0
    score_total = 0.0
    material_percent_total = 0
    material_mastery_percent_total = 0
    task_percent_total = 0
    interaction_percent_total = 0
    active_student_count = 0
    quiet_student_count = 0
    need_attention_count = 0
    online_minutes_total = 0
    for row in rows:
        student = dict(row)
        progress = get_student_learning_state(conn, int(class_offering_id), int(student["id"]))
        metrics = progress.get("metrics") or {}
        material_metrics = metrics.get("material") or {}
        assignment_metrics = metrics.get("assignments") or {}
        interaction_metrics = metrics.get("interactions") or {}
        score_total += safe_float(progress.get("score"))
        current_key = normalize_level_key(progress["current_level"].get("level_key") or "mortal")
        distribution[current_key] = distribution.get(current_key, 0) + 1
        challenge_ready_count += 1 if progress.get("eligible_stage") else 0
        certificate_count += len(progress.get("certificates") or [])
        student_id = int(student["id"])
        state_item = behavior_state_by_student.get(student_id, {})
        event_item = behavior_event_by_student.get(student_id, {})
        material_percent = int(round(clamp(safe_float(material_metrics.get("ratio"))) * 100))
        material_required_count = safe_int(material_metrics.get("required_count"))
        material_mastery_percent = (
            int(round(safe_int(material_metrics.get("mastered_count")) / material_required_count * 100))
            if material_required_count
            else 0
        )
        task_completion_percent = int(round(clamp(safe_float(assignment_metrics.get("completion_ratio"))) * 100))
        interaction_percent = int(round(clamp(safe_float(interaction_metrics.get("interaction_ratio"))) * 100))
        assignment_count = safe_int(assignment_metrics.get("assignment_count"))
        submitted_count = safe_int(assignment_metrics.get("submitted_count"))
        pending_task_count = max(assignment_count - submitted_count, 0)
        activity_count = safe_int(state_item.get("total_activity_count")) or safe_int(event_item.get("event_count"))
        ai_question_count = safe_int(event_item.get("ai_question_count"))
        peer_help_count = safe_int(event_item.get("peer_help_count"))
        chat_message_count = safe_int(chat_count_by_student.get(student_id))
        online_minutes = round(safe_int(state_item.get("online_accumulated_seconds")) / 60)
        focus_minutes = round(safe_int(state_item.get("focus_total_seconds")) / 60)
        has_teacher_note = student_id in shared_note_student_ids
        student_alerts = (alert_context.get("alerts_by_student") or {}).get(student_id, [])
        highest_alert_severity = ""
        if student_alerts:
            highest_alert_severity = max(
                (str(item.get("severity") or "L1") for item in student_alerts),
                key=lambda value: {"L1": 1, "L2": 2, "L3": 3}.get(value, 0),
            )
        has_activity = bool(activity_count or ai_question_count or chat_message_count or online_minutes)
        needs_attention = bool(
            pending_task_count > 0
            or (assignment_count > 0 and task_completion_percent < 50)
            or (assignment_count > 0 and not has_activity)
            or student_alerts
        )
        material_percent_total += material_percent
        material_mastery_percent_total += material_mastery_percent
        task_percent_total += task_completion_percent
        interaction_percent_total += interaction_percent
        online_minutes_total += online_minutes
        active_student_count += 1 if has_activity else 0
        quiet_student_count += 0 if has_activity else 1
        need_attention_count += 1 if needs_attention else 0
        status_tags = []
        if has_teacher_note:
            status_tags.append("有备注")
        if pending_task_count:
            status_tags.append(f"待完成 {pending_task_count}")
        if not has_activity:
            status_tags.append("低活跃")
        if progress.get("eligible_stage"):
            status_tags.append("可破境")
        if highest_alert_severity:
            status_tags.append(f"{highest_alert_severity}预警")
        if peer_help_count:
            status_tags.append(f"助人{peer_help_count}")
        students.append({
            "id": student_id,
            "name": student["name"],
            "student_id_number": student.get("student_id_number"),
            "score": progress["score"],
            "progress_percent": progress["progress_percent"],
            "current_level": progress["current_level"],
            "next_stage": progress["next_stage"],
            "certificate_count": len(progress.get("certificates") or []),
            "eligible_stage": progress.get("eligible_stage"),
            "material_percent": material_percent,
            "material_mastery_percent": material_mastery_percent,
            "task_completion_percent": task_completion_percent,
            "interaction_percent": interaction_percent,
            "submitted_count": submitted_count,
            "assignment_count": assignment_count,
            "pending_task_count": pending_task_count,
            "activity_count": activity_count,
            "ai_question_count": ai_question_count,
            "peer_help_count": peer_help_count,
            "chat_message_count": chat_message_count,
            "online_minutes": online_minutes,
            "focus_minutes": focus_minutes,
            "has_teacher_note": has_teacher_note,
            "needs_attention": needs_attention,
            "alert_count": len(student_alerts),
            "highest_alert_severity": highest_alert_severity,
            "alerts": student_alerts[:3],
            "status_tags": status_tags,
            "metrics": {
                "materials": material_metrics,
                "assignments": assignment_metrics,
                "interactions": interaction_metrics,
            },
        })
    student_count = len(students)
    distribution_items = [
        {
            "key": "mortal",
            "name": STARTER_LEVEL["short_name"],
            "count": distribution.get("mortal", 0),
            "percent": int(round(distribution.get("mortal", 0) / student_count * 100)) if student_count else 0,
        }
    ]
    for level in LEARNING_LEVELS:
        count = distribution.get(level["key"], 0)
        distribution_items.append({
            "key": level["key"],
            "name": level["short_name"],
            "count": count,
            "percent": int(round(count / student_count * 100)) if student_count else 0,
        })
    roster_students = sorted(
        students,
        key=lambda item: (str(item.get("student_id_number") or ""), str(item.get("name") or ""), int(item["id"])),
    )
    students.sort(key=lambda item: (item["score"], item["certificate_count"]), reverse=True)
    average_material_percent = round(material_percent_total / student_count) if student_count else 0
    average_material_mastery_percent = round(material_mastery_percent_total / student_count) if student_count else 0
    average_task_percent = round(task_percent_total / student_count) if student_count else 0
    average_interaction_percent = round(interaction_percent_total / student_count) if student_count else 0
    average_online_minutes = round(online_minutes_total / student_count) if student_count else 0
    teacher_note_count = len(shared_note_student_ids)
    high_progress_count = sum(1 for item in students if safe_float(item.get("score")) >= 80)
    total_peer_help_count = sum(safe_int(item.get("peer_help_count")) for item in students)
    peer_help_leaders = [
        {
            "id": int(item["id"]),
            "name": item.get("name"),
            "peer_help_count": safe_int(item.get("peer_help_count")),
            "current_level": item.get("current_level"),
            "score": item.get("score"),
        }
        for item in sorted(
            (item for item in students if safe_int(item.get("peer_help_count")) > 0),
            key=lambda value: (
                -safe_int(value.get("peer_help_count")),
                -safe_float(value.get("score")),
                str(value.get("name") or ""),
            ),
        )[:3]
    ]
    summary_cards = [
        {"label": "学生总数", "value": student_count, "suffix": "人", "note": f"活跃 {active_student_count} 人"},
        {"label": "平均修为", "value": round(score_total / student_count, 1) if student_count else 0, "suffix": "", "note": f"高阶 {high_progress_count} 人"},
        {"label": "材料完成", "value": average_material_percent, "suffix": "%", "note": "班级均值"},
        {"label": "材料掌握", "value": average_material_mastery_percent, "suffix": "%", "note": "检验通过率"},
        {"label": "任务完成", "value": average_task_percent, "suffix": "%", "note": f"待关注 {need_attention_count} 人"},
        {"label": "互动热度", "value": average_interaction_percent, "suffix": "%", "note": f"低活跃 {quiet_student_count} 人"},
        {"label": "教师备注", "value": teacher_note_count, "suffix": "人", "note": f"平均在线 {average_online_minutes} 分钟"},
    ]
    if total_peer_help_count:
        summary_cards.append({"label": "传功助人", "value": total_peer_help_count, "suffix": "次", "note": f"上榜 {len(peer_help_leaders)} 人"})
    personal_stats = build_personal_stage_exam_stats(conn, int(class_offering_id))
    if not personal_stats.get("total_count"):
        personal_stats = {"total_count": 0, "student_count": 0, "active_count": 0, "passed_count": 0}
    trend_summary = build_class_cultivation_trend_summary(conn, int(class_offering_id), weeks=8)
    if trend_summary.get("has_enough_data") and len(summary_cards) > 1:
        summary_cards[1]["note"] = f"较上周 {trend_summary.get('average_delta_label')}"
    return {
        "student_count": student_count,
        "average_score": round(score_total / student_count, 1) if student_count else 0,
        "sect_name": normalize_course_sect_name(offering.get("course_sect_name"), course_name=offering.get("course_name")),
        "challenge_ready_count": challenge_ready_count,
        "certificate_count": certificate_count,
        "active_student_count": active_student_count,
        "quiet_student_count": quiet_student_count,
        "need_attention_count": need_attention_count,
        "teacher_note_count": teacher_note_count,
        "total_peer_help_count": total_peer_help_count,
        "peer_help_leaders": peer_help_leaders,
        "average_material_percent": average_material_percent,
        "average_material_mastery_percent": average_material_mastery_percent,
        "average_task_percent": average_task_percent,
        "average_interaction_percent": average_interaction_percent,
        "average_online_minutes": average_online_minutes,
        "summary_cards": summary_cards,
        "weight_settings": weight_settings,
        "rules": {
            "score_weights": weight_settings.get("rules") or weight_rules_from_weights(DEFAULT_CULTIVATION_WEIGHTS),
            "weight_version": weight_settings.get("version") or CULTIVATION_WEIGHT_VERSION_DEFAULT,
        },
        "alert_summary": alert_context,
        "trend_summary": trend_summary,
        "personal_stage_exam_stats": personal_stats,
        "distribution": distribution_items,
        "students": students[:12],
        "roster_students": roster_students,
        "levels": [dict(level, pass_score=PASSING_STAGE_SCORE) for level in LEARNING_LEVELS],
    }


def record_material_learning_progress(
    conn,
    *,
    class_offering_id: int,
    student_id: int,
    material_id: int,
    session_id: Optional[int] = None,
    duration_seconds: int = 0,
    active_seconds: int = 0,
    scroll_ratio: float = 0.0,
    completed: bool = False,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    timestamp = now_iso()
    duration_seconds = max(0, min(int(duration_seconds or 0), 600))
    active_seconds = max(0, min(int(active_seconds or 0), duration_seconds or 600))
    scroll_ratio = clamp(float(scroll_ratio or 0))
    completed = bool(
        completed
        or scroll_ratio >= MATERIAL_COMPLETE_SCROLL
        or active_seconds >= MATERIAL_COMPLETE_ACTIVE_SECONDS
        or duration_seconds >= MATERIAL_COMPLETE_TOTAL_SECONDS
    )
    material_row = conn.execute(
        """
        SELECT id, check_questions_json, check_questions_status
        FROM course_materials
        WHERE id = ?
        LIMIT 1
        """,
        (int(material_id),),
    ).fetchone()
    material = dict(material_row) if material_row else {}
    check_ready = _material_has_ready_mastery_check(material)
    auto_mastered = bool(completed and not check_ready)
    mastery_source = "single_tier_fallback" if auto_mastered else ""
    progress_rule_version = "material_mastery_v2" if check_ready else "material_single_tier_fallback_v2"
    if get_configured_db_engine() == "postgres":
        view_count_increment_sql = """
                WHEN (excluded.last_viewed_at::timestamp - learning_material_progress.last_viewed_at::timestamp) > INTERVAL '1800 seconds' THEN 1
        """
    else:
        view_count_increment_sql = """
                WHEN (julianday(excluded.last_viewed_at) - julianday(learning_material_progress.last_viewed_at)) * 86400 > 1800 THEN 1
        """
    conn.execute(
        f"""
        INSERT INTO learning_material_progress (
            class_offering_id, student_id, material_id, session_id,
            view_count, accumulated_seconds, active_seconds, max_scroll_ratio,
            completed, mastered, mastered_at, mastery_source, mastery_attempts,
            mastery_last_attempt_json, progress_rule_version,
            first_viewed_at, last_viewed_at, updated_at, metadata_json
        )
        VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, 0, '{{}}', ?, ?, ?, ?, ?)
        ON CONFLICT(class_offering_id, student_id, material_id)
        DO UPDATE SET
            session_id = COALESCE(excluded.session_id, learning_material_progress.session_id),
            view_count = learning_material_progress.view_count + CASE
                WHEN learning_material_progress.last_viewed_at IS NULL THEN 1
{view_count_increment_sql.rstrip()}
                ELSE 0
            END,
            accumulated_seconds = learning_material_progress.accumulated_seconds + excluded.accumulated_seconds,
            active_seconds = learning_material_progress.active_seconds + excluded.active_seconds,
            max_scroll_ratio = CASE
                WHEN COALESCE(learning_material_progress.max_scroll_ratio, 0) >= COALESCE(excluded.max_scroll_ratio, 0)
                    THEN learning_material_progress.max_scroll_ratio
                ELSE excluded.max_scroll_ratio
            END,
            completed = CASE WHEN learning_material_progress.completed = 1 OR excluded.completed = 1 THEN 1 ELSE 0 END,
            mastered = CASE WHEN learning_material_progress.mastered = 1 OR excluded.mastered = 1 THEN 1 ELSE 0 END,
            mastered_at = CASE
                WHEN learning_material_progress.mastered = 1 THEN learning_material_progress.mastered_at
                WHEN excluded.mastered = 1 THEN excluded.mastered_at
                ELSE learning_material_progress.mastered_at
            END,
            mastery_source = CASE
                WHEN learning_material_progress.mastered = 1 AND COALESCE(TRIM(learning_material_progress.mastery_source), '') != ''
                    THEN learning_material_progress.mastery_source
                WHEN excluded.mastered = 1 THEN excluded.mastery_source
                ELSE learning_material_progress.mastery_source
            END,
            progress_rule_version = CASE
                WHEN learning_material_progress.progress_rule_version = 'legacy_completed_full_credit'
                    THEN learning_material_progress.progress_rule_version
                WHEN learning_material_progress.completed = 0 AND excluded.completed = 1
                    THEN excluded.progress_rule_version
                ELSE learning_material_progress.progress_rule_version
            END,
            last_viewed_at = excluded.last_viewed_at,
            updated_at = excluded.updated_at,
            metadata_json = excluded.metadata_json
        """,
        (
            int(class_offering_id),
            int(student_id),
            int(material_id),
            int(session_id) if session_id else None,
            duration_seconds,
            active_seconds,
            scroll_ratio,
            1 if completed else 0,
            1 if auto_mastered else 0,
            timestamp if auto_mastered else None,
            mastery_source,
            progress_rule_version,
            timestamp,
            timestamp,
            timestamp,
            json.dumps(
                {
                    **(metadata or {}),
                    "progress_rule_version": progress_rule_version,
                    "mastery_check_ready": check_ready,
                },
                ensure_ascii=False,
            ),
        ),
    )
    progress_row = conn.execute(
        """
        SELECT *
        FROM learning_material_progress
        WHERE class_offering_id = ? AND student_id = ? AND material_id = ?
        LIMIT 1
        """,
        (int(class_offering_id), int(student_id), int(material_id)),
    ).fetchone()
    progress = dict(progress_row) if progress_row else {}
    event_source = f"material:{int(material_id)}"
    if completed:
        event_source = (
            f"material:{int(material_id)}:single-tier-fallback-v2"
            if auto_mastered
            else f"material:{int(material_id)}:read-v2"
        )
    state = refresh_student_learning_state(
        conn,
        int(class_offering_id),
        int(student_id),
        event_source_ref=event_source,
    )
    return {
        "status": "success",
        "completed": completed,
        "mastered": bool(progress and safe_int(progress.get("mastered"))),
        "mastery_check": _public_mastery_check_context(material, progress),
        "progress": {
            "score": state["score"],
            "progress_percent": state["progress_percent"],
            "eligible_stage": state.get("eligible_stage"),
        },
    }


def get_material_mastery_check_context(
    conn,
    *,
    class_offering_id: int,
    student_id: int,
    material_id: int,
) -> dict[str, Any]:
    material_row = conn.execute(
        """
        SELECT id, check_questions_json, check_questions_status
        FROM course_materials
        WHERE id = ?
        LIMIT 1
        """,
        (int(material_id),),
    ).fetchone()
    progress_row = conn.execute(
        """
        SELECT *
        FROM learning_material_progress
        WHERE class_offering_id = ? AND student_id = ? AND material_id = ?
        LIMIT 1
        """,
        (int(class_offering_id), int(student_id), int(material_id)),
    ).fetchone()
    return _public_mastery_check_context(
        dict(material_row) if material_row else {},
        dict(progress_row) if progress_row else {},
    )


def submit_material_mastery_check(
    conn,
    *,
    class_offering_id: int,
    student_id: int,
    material_id: int,
    answers: dict[str, Any],
) -> dict[str, Any]:
    material_row = conn.execute(
        """
        SELECT id, check_questions_json, check_questions_status
        FROM course_materials
        WHERE id = ?
        LIMIT 1
        """,
        (int(material_id),),
    ).fetchone()
    if not material_row:
        raise LookupError("未找到材料")
    material = dict(material_row)
    if not _material_has_ready_mastery_check(material):
        raise ValueError("当前材料没有可用的心法检验")

    progress_row = conn.execute(
        """
        SELECT *
        FROM learning_material_progress
        WHERE class_offering_id = ? AND student_id = ? AND material_id = ?
        LIMIT 1
        """,
        (int(class_offering_id), int(student_id), int(material_id)),
    ).fetchone()
    if not progress_row or not safe_int(dict(progress_row).get("completed")):
        raise ValueError("先完成材料研读后再进行心法检验")

    grading = grade_material_mastery_check(material.get("check_questions_json"), answers or {})
    timestamp = now_iso()
    attempt_payload = {
        "version": "material_mastery_v2",
        "submitted_at": timestamp,
        "answers": answers or {},
        "grading": grading,
    }
    if grading["passed"]:
        conn.execute(
            """
            UPDATE learning_material_progress
            SET mastered = 1,
                mastered_at = COALESCE(mastered_at, ?),
                mastery_source = 'micro_check',
                mastery_attempts = mastery_attempts + 1,
                mastery_last_attempt_json = ?,
                progress_rule_version = 'material_mastery_v2',
                updated_at = ?
            WHERE class_offering_id = ? AND student_id = ? AND material_id = ?
            """,
            (
                timestamp,
                json.dumps(attempt_payload, ensure_ascii=False),
                timestamp,
                int(class_offering_id),
                int(student_id),
                int(material_id),
            ),
        )
    else:
        conn.execute(
            """
            UPDATE learning_material_progress
            SET mastery_attempts = mastery_attempts + 1,
                mastery_last_attempt_json = ?,
                progress_rule_version = 'material_mastery_v2',
                updated_at = ?
            WHERE class_offering_id = ? AND student_id = ? AND material_id = ?
            """,
            (
                json.dumps(attempt_payload, ensure_ascii=False),
                timestamp,
                int(class_offering_id),
                int(student_id),
                int(material_id),
            ),
        )

    progress_row = conn.execute(
        """
        SELECT *
        FROM learning_material_progress
        WHERE class_offering_id = ? AND student_id = ? AND material_id = ?
        LIMIT 1
        """,
        (int(class_offering_id), int(student_id), int(material_id)),
    ).fetchone()
    progress = dict(progress_row) if progress_row else {}
    state = refresh_student_learning_state(
        conn,
        int(class_offering_id),
        int(student_id),
        event_source_ref=(
            f"material:{int(material_id)}:mastery-check-v2"
            if grading["passed"]
            else f"material:{int(material_id)}:mastery-check-retry-v2"
        ),
    )
    return {
        "status": "success",
        "passed": bool(grading["passed"]),
        "completed": bool(progress and safe_int(progress.get("completed"))),
        "mastered": bool(progress and safe_int(progress.get("mastered"))),
        "attempts": safe_int(progress.get("mastery_attempts")),
        "grading": grading,
        "mastery_check": _public_mastery_check_context(material, progress),
        "progress": {
            "score": state["score"],
            "progress_percent": state["progress_percent"],
            "eligible_stage": state.get("eligible_stage"),
        },
    }


def _ensure_stage_exam_template_file() -> dict[str, Any]:
    STAGE_EXAM_TEMPLATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if STAGE_EXAM_TEMPLATE_PATH.exists():
        template = json_loads(STAGE_EXAM_TEMPLATE_PATH.read_text(encoding="utf-8"), {})
        if isinstance(template, dict):
            try:
                normalize_exam_json_payload(template)
                return template
            except ValueError:
                pass
    STAGE_EXAM_TEMPLATE_PATH.write_text(
        json.dumps(STAGE_EXAM_TEMPLATE, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return STAGE_EXAM_TEMPLATE


def _load_stage_exam_template_text() -> str:
    template = _ensure_stage_exam_template_file()
    if not template:
        template = EXAM_JSON_TEMPLATE
    return json.dumps(template, ensure_ascii=False, indent=2)


def _stage_answer_has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(_stage_answer_has_value(item) for item in value)
    if isinstance(value, dict):
        return any(_stage_answer_has_value(item) for item in value.values())
    return True


def _flatten_stage_exam_questions(exam_payload: dict[str, Any]) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    for page in exam_payload.get("pages", []) or []:
        if not isinstance(page, dict):
            continue
        for question in page.get("questions", []) or []:
            if isinstance(question, dict):
                questions.append(question)
    return questions


def _score_value(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        score = float(value)
    except (TypeError, ValueError):
        return None
    return score if score > 0 else None


def _format_stage_points(value: float) -> int | float:
    rounded = round(float(value), 2)
    return int(rounded) if rounded.is_integer() else rounded


def _ensure_stage_exam_scoring_payload(exam_payload: dict[str, Any]) -> dict[str, Any]:
    """Make AI-generated personal stage exams safe for automatic grading."""
    normalized = normalize_exam_scoring_payload(exam_payload, require_complete=False)
    questions = _flatten_stage_exam_questions(normalized)
    if not questions:
        return normalize_exam_scoring_payload(normalized, require_complete=True)

    existing_points = [_score_value(question.get("points")) for question in questions]
    total_points = sum(point or 0 for point in existing_points)
    has_missing_points = any(point is None for point in existing_points) or total_points <= 0
    if has_missing_points:
        raw_share = 100 / len(questions)
        assigned_total = 0.0
        for index, question in enumerate(questions, start=1):
            if index == len(questions):
                points = max(1.0, 100 - assigned_total)
            else:
                points = round(raw_share, 2)
                assigned_total += points
            question["points"] = _format_stage_points(points)
    elif abs(total_points - 100) > 0.01:
        assigned_total = 0.0
        for index, (question, point) in enumerate(zip(questions, existing_points), start=1):
            if index == len(questions):
                points = max(1.0, 100 - assigned_total)
            else:
                points = round(float(point or 0) * 100 / total_points, 2)
                assigned_total += points
            question["points"] = _format_stage_points(points)

    default_guidance = (
        "Grade by the reference answer, explanation, prompt requirements, course terminology, "
        "reasoning process, and supporting evidence."
    )
    default_deductions = (
        "Deduct for incorrect core concepts, missing key steps, unsupported conclusions, "
        "insufficient evidence, or answers that drift away from the prompt."
    )
    for question in questions:
        if not _stage_answer_has_value(question.get("answer")):
            question["answer"] = question.get("explanation") or "Use the question requirements and scoring guidance as the reference answer."
        if not str(question.get("grading_guidance") or "").strip():
            question["grading_guidance"] = default_guidance
        if not str(question.get("deduction_points") or "").strip():
            question["deduction_points"] = default_deductions
        question["grading"] = {
            "points": question["points"],
            "guidance": question["grading_guidance"],
            "deduction_points": question["deduction_points"],
        }

    grading = normalized.get("grading") if isinstance(normalized.get("grading"), dict) else {}
    grading = dict(grading)
    grading["total_score"] = 100
    grading["description"] = str(grading.get("description") or default_guidance)
    grading["style"] = str(grading.get("style") or "medium")
    normalized["grading"] = grading
    return normalize_exam_scoring_payload(normalized, require_complete=True)


def _normalize_exam_payload(payload: Any) -> dict[str, Any]:
    data = payload
    if isinstance(data, dict) and isinstance(data.get("exam_data"), dict):
        data = data["exam_data"]
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        data = data["data"]
    try:
        imported = normalize_exam_json_payload(data)
    except ValueError as exc:
        raise ValueError(f"AI 返回的试卷结构不符合原生 JSON 模板：{exc}") from exc
    questions = _ensure_stage_exam_scoring_payload(dict(imported["questions"]))
    questions["meta"] = {
        **(questions.get("meta") if isinstance(questions.get("meta"), dict) else {}),
        "generated_for": "learning_stage",
        "template": "native_exam_json",
        "title": imported.get("title"),
        "description": imported.get("description"),
        "stats": imported.get("stats"),
    }
    return questions


def _stage_exam_question_texts(exam_payload: dict[str, Any], *, limit: int = 50) -> list[str]:
    texts: list[str] = []
    for question in _flatten_stage_exam_questions(exam_payload):
        text = " ".join(str(question.get(key) or "").strip() for key in ("text", "prompt", "title"))
        text = " ".join(text.split())
        if len(text) >= 8:
            texts.append(text[:500])
        if len(texts) >= limit:
            break
    return texts


def _stage_exam_option_tokens(option: Any) -> set[str]:
    values: list[Any] = []
    if isinstance(option, dict):
        for key in ("value", "label", "text", "title", "content"):
            values.append(option.get(key))
    else:
        values.append(option)
    tokens = set()
    for value in values:
        text = " ".join(str(value or "").strip().lower().split())
        if text:
            tokens.add(text)
    return tokens


def _validate_stage_exam_answer_options(exam_payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for index, question in enumerate(_flatten_stage_exam_questions(exam_payload), start=1):
        question_type = str(question.get("type") or "").strip().lower()
        if question_type not in {"radio", "checkbox"}:
            continue
        option_tokens: set[str] = set()
        for option in question.get("options") or []:
            option_tokens.update(_stage_exam_option_tokens(option))
        if not option_tokens:
            errors.append(f"question {index} has no selectable options")
            continue
        answer = question.get("answer")
        if question_type == "radio":
            answer_tokens = {" ".join(str(answer or "").strip().lower().split())}
        elif isinstance(answer, list):
            answer_tokens = {" ".join(str(item or "").strip().lower().split()) for item in answer}
        else:
            errors.append(f"question {index} checkbox answer must be a list")
            continue
        missing = sorted(token for token in answer_tokens if token and token not in option_tokens)
        if missing:
            errors.append(f"question {index} answer not in options: {', '.join(missing[:3])}")
    return errors


def _stage_exam_similarity_key(text: Any) -> str:
    return "".join(ch.lower() for ch in str(text or "") if ch.isalnum())


def _stage_exam_ngrams(text: Any, *, size: int = 3) -> set[str]:
    compact = _stage_exam_similarity_key(text)
    if len(compact) <= size:
        return {compact} if compact else set()
    return {compact[index : index + size] for index in range(0, len(compact) - size + 1)}


def _stage_exam_text_similarity(left: Any, right: Any) -> float:
    left_grams = _stage_exam_ngrams(left)
    right_grams = _stage_exam_ngrams(right)
    if not left_grams or not right_grams:
        return 0.0
    return len(left_grams & right_grams) / max(1, min(len(left_grams), len(right_grams)))


def _stage_exam_duplicate_report(
    exam_payload: dict[str, Any],
    historical_question_texts: list[str],
    *,
    question_similarity_threshold: float = 0.82,
    duplicate_ratio_threshold: float = 0.4,
) -> dict[str, Any]:
    current_texts = _stage_exam_question_texts(exam_payload)
    historical_texts = [text for text in historical_question_texts if str(text or "").strip()]
    if not current_texts or not historical_texts:
        return {"duplicate": False, "duplicate_count": 0, "question_count": len(current_texts), "examples": []}
    duplicate_count = 0
    examples: list[dict[str, Any]] = []
    for current in current_texts:
        best_text = ""
        best_score = 0.0
        for historical in historical_texts:
            score = _stage_exam_text_similarity(current, historical)
            if score > best_score:
                best_score = score
                best_text = historical
        if best_score >= question_similarity_threshold:
            duplicate_count += 1
            if len(examples) < 3:
                examples.append({
                    "text": truncate_text(current, 120),
                    "matched": truncate_text(best_text, 120),
                    "similarity": round(best_score, 3),
                })
    ratio = duplicate_count / max(1, len(current_texts))
    return {
        "duplicate": ratio >= duplicate_ratio_threshold,
        "duplicate_count": duplicate_count,
        "question_count": len(current_texts),
        "duplicate_ratio": round(ratio, 3),
        "examples": examples,
    }


def _load_historical_stage_exam_question_texts(
    conn,
    class_offering_id: int,
    student_id: int,
    stage_key: str,
    *,
    limit: int = 40,
) -> list[str]:
    try:
        rows = conn.execute(
            """
            SELECT ep.questions_json
            FROM learning_stage_exam_attempts lsea
            JOIN exam_papers ep ON ep.id = lsea.exam_paper_id
            WHERE lsea.class_offering_id = ?
              AND lsea.student_id = ?
              AND lsea.stage_key = ?
              AND lsea.exam_paper_id IS NOT NULL
              AND lsea.exam_paper_id != ''
            ORDER BY COALESCE(lsea.generated_at, lsea.submitted_at, lsea.graded_at, '') DESC, lsea.id DESC
            LIMIT 10
            """,
            (int(class_offering_id), int(student_id), normalize_level_key(stage_key)),
        ).fetchall()
    except Exception:
        return []
    texts: list[str] = []
    for row in rows:
        payload = json_loads(row["questions_json"], {})
        if isinstance(payload, dict):
            texts.extend(_stage_exam_question_texts(payload, limit=max(1, limit - len(texts))))
        if len(texts) >= limit:
            break
    return texts[:limit]


def _validate_stage_exam_quality(exam_payload: dict[str, Any], historical_question_texts: list[str] | None = None) -> dict[str, Any]:
    option_errors = _validate_stage_exam_answer_options(exam_payload)
    if option_errors:
        raise ValueError("Stage exam answer option validation failed: " + "; ".join(option_errors[:5]))
    duplicate_report = _stage_exam_duplicate_report(exam_payload, historical_question_texts or [])
    if duplicate_report.get("duplicate"):
        raise ValueError(
            "Stage exam duplicates historical questions: "
            f"{duplicate_report.get('duplicate_count')}/{duplicate_report.get('question_count')} similar"
        )
    return {"option_errors": [], "duplicate_report": duplicate_report}


def _stage_scope_count(total_count: int, level: Optional[dict[str, Any]]) -> int:
    if total_count <= 0:
        return 0
    if not level:
        return total_count
    max_tier = max(safe_int(item["tier"]) for item in LEARNING_LEVELS) or 1
    tier = max(1, safe_int(level.get("tier"), 1))
    return max(1, min(total_count, math.ceil(total_count * tier / max_tier)))


def _material_summary_text(item: dict[str, Any]) -> str:
    parse_result = json_loads(item.get("ai_parse_result_json"), {})
    summary = parse_result.get("summary") if isinstance(parse_result, dict) else ""
    return summary or item.get("ai_optimized_markdown") or item.get("content") or ""


def _build_course_knowledge_snapshot(
    conn,
    class_offering_id: int,
    *,
    level: Optional[dict[str, Any]] = None,
    limit: int = 5200,
) -> str:
    offering = _load_offering(conn, class_offering_id) or {}
    sessions = conn.execute(
        """
        SELECT s.order_index, s.title, s.content, s.learning_material_id,
               m.name AS material_name, m.ai_parse_result_json, m.ai_optimized_markdown
        FROM class_offering_sessions s
        LEFT JOIN course_materials m ON m.id = s.learning_material_id
        WHERE s.class_offering_id = ?
        ORDER BY s.order_index ASC
        """,
        (class_offering_id,),
    ).fetchall()
    lines = [
        f"课程名称：{offering.get('course_name') or ''}",
        f"课程简介：{truncate_text(offering.get('course_description'), 600)}",
    ]
    scoped_count = _stage_scope_count(len(sessions), level)
    scoped_sessions = sessions[:scoped_count] if scoped_count else sessions
    if level and sessions:
        lines.append(
            f"当前破境范围：{level['name']}，按境界累计覆盖前 {scoped_count} / {len(sessions)} 个课堂节点。"
        )
    lines.append("范围内课堂章节与学习文档摘要：")
    scoped_material_ids: set[int] = set()
    for row in scoped_sessions:
        item = dict(row)
        material_id = safe_int(item.get("learning_material_id"))
        if material_id:
            scoped_material_ids.add(material_id)
        summary = _material_summary_text(item)
        lines.append(
            f"{item.get('order_index')}. {item.get('title') or '未命名章节'}"
            f"；材料：{item.get('material_name') or '无'}；要点：{truncate_text(summary, 420)}"
        )
    supplemental_rows = conn.execute(
        """
        SELECT DISTINCT m.id, m.name, m.material_path, m.ai_parse_result_json, m.ai_optimized_markdown, '' AS content
        FROM course_materials m
        JOIN (
            SELECT home_learning_material_id AS material_id
            FROM class_offerings
            WHERE id = ? AND home_learning_material_id IS NOT NULL
            UNION
            SELECT material_id
            FROM course_material_assignments
            WHERE class_offering_id = ?
        ) scoped ON scoped.material_id = m.id
        WHERE m.node_type = 'file'
        ORDER BY m.name COLLATE NOCASE
        """,
        (class_offering_id, class_offering_id),
    ).fetchall()
    supplemental_added = 0
    for row in supplemental_rows:
        item = dict(row)
        material_id = safe_int(item.get("id"))
        if material_id in scoped_material_ids:
            continue
        if supplemental_added == 0:
            lines.append("范围内补充学习文档：")
        supplemental_added += 1
        if supplemental_added > 8:
            break
        lines.append(
            f"- {item.get('name') or item.get('material_path') or '未命名材料'}："
            f"{truncate_text(_material_summary_text(item), 360)}"
        )
    return truncate_text("\n".join(lines), limit)


def _strip_markdown_marker(value: Any) -> str:
    text = str(value or "").replace("\x00", " ")
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text)
    text = re.sub(r"^\s*[-*+]\s*", "", text)
    text = re.sub(r"^\s*\d+[.)、]\s*", "", text)
    text = text.replace("**", "").replace("__", "").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _is_retreat_signal_line(text: str) -> bool:
    if not text:
        return False
    weak_keywords = (
        "薄弱",
        "不足",
        "错误",
        "错因",
        "失分",
        "扣分",
        "混淆",
        "遗漏",
        "未能",
        "没有",
        "需要",
        "建议",
        "改进",
        "概念不清",
        "步骤不完整",
    )
    if any(keyword in text for keyword in weak_keywords):
        return True
    match = re.search(r"(?:得分|分数)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", text)
    if match:
        earned = safe_float(match.group(1))
        total = safe_float(match.group(2), 1)
        return total > 0 and earned / total < 0.8
    return False


def _extract_stage_retreat_weak_points(
    feedback_md: Any,
    *,
    score: float,
    level: dict[str, Any],
) -> list[str]:
    raw = str(feedback_md or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [_strip_markdown_marker(line) for line in raw.split("\n")]
    current_question = ""
    weak_points: list[str] = []
    seen: set[str] = set()

    for line in lines:
        if not line:
            continue
        if re.match(r"^(第?\s*\d+\s*[题問问]|[A-Za-z]?\d+[\s:：.-])", line):
            current_question = truncate_text(line, 48)
            continue
        if not _is_retreat_signal_line(line):
            continue
        if len(line) < 6 and "得分" not in line:
            continue
        text = line
        if current_question and current_question not in text and not text.startswith("总览"):
            text = f"{current_question}：{text}"
        text = truncate_text(text, 120)
        key = re.sub(r"\W+", "", text).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        weak_points.append(text)
        if len(weak_points) >= STAGE_EXAM_RETREAT_MAX_ITEMS:
            break

    if len(weak_points) < STAGE_EXAM_RETREAT_MIN_ITEMS and raw:
        sentences = [
            _strip_markdown_marker(part)
            for part in re.split(r"[。！？!?；;\n]", raw)
            if _strip_markdown_marker(part)
        ]
        for sentence in sentences:
            if sentence in weak_points:
                continue
            if len(sentence) < 8:
                continue
            if not _is_retreat_signal_line(sentence):
                continue
            text = truncate_text(sentence, 120)
            key = re.sub(r"\W+", "", text).lower()
            if key in seen:
                continue
            seen.add(key)
            weak_points.append(text)
            if len(weak_points) >= STAGE_EXAM_RETREAT_MAX_ITEMS:
                break

    fallback_templates = [
        f"{level.get('name') or '当前境界'}试炼得分 {score:g}，先复盘低分题的关键概念与答题依据。",
        "整理本次试炼中没有写清楚的术语、步骤或例证，补回课堂材料里的原始表述。",
        "挑一题最不确定的题目重新口述解题过程，确认下一次能独立判断。",
    ]
    for fallback in fallback_templates:
        if len(weak_points) >= STAGE_EXAM_RETREAT_MIN_ITEMS:
            break
        key = re.sub(r"\W+", "", fallback).lower()
        if key in seen:
            continue
        seen.add(key)
        weak_points.append(fallback)

    return weak_points[:STAGE_EXAM_RETREAT_MAX_ITEMS]


def _cjk_bigrams(text: str) -> set[str]:
    chars = [char for char in text if "\u4e00" <= char <= "\u9fff"]
    return {f"{chars[index]}{chars[index + 1]}" for index in range(max(0, len(chars) - 1))}


def _match_tokens(text: Any) -> set[str]:
    raw = str(text or "").lower()
    tokens = {
        token
        for token in re.findall(r"[a-z0-9_]{2,}", raw)
        if token not in {"http", "https", "www"}
    }
    tokens.update(_cjk_bigrams(raw))
    return tokens


def _retreat_material_href(class_offering_id: int, material_id: int, session_id: int = 0) -> str:
    href = f"/materials/view/{int(material_id)}?class_offering_id={int(class_offering_id)}"
    if session_id:
        href += f"&session_id={int(session_id)}"
    return href


def _load_stage_retreat_material_candidates(
    conn,
    class_offering_id: int,
    *,
    level: dict[str, Any],
) -> list[dict[str, Any]]:
    sessions = [
        dict(row)
        for row in conn.execute(
            """
            SELECT s.id AS session_id,
                   s.order_index,
                   s.title AS session_title,
                   s.content AS session_content,
                   s.learning_material_id,
                   m.id AS material_id,
                   m.name AS material_name,
                   m.material_path,
                   m.ai_parse_result_json,
                   m.ai_optimized_markdown
            FROM class_offering_sessions s
            LEFT JOIN course_materials m ON m.id = s.learning_material_id
            WHERE s.class_offering_id = ?
            ORDER BY s.order_index ASC
            """,
            (int(class_offering_id),),
        ).fetchall()
    ]
    scoped_count = _stage_scope_count(len(sessions), level)
    scoped_sessions = sessions[:scoped_count] if scoped_count else sessions
    candidates: list[dict[str, Any]] = []
    seen_material_ids: set[int] = set()
    for item in scoped_sessions:
        material_id = safe_int(item.get("material_id") or item.get("learning_material_id"))
        title = str(item.get("material_name") or item.get("session_title") or "").strip()
        if not title:
            continue
        summary = _material_summary_text(item)
        href = _retreat_material_href(class_offering_id, material_id, safe_int(item.get("session_id"))) if material_id else f"/classroom/{int(class_offering_id)}"
        candidates.append({
            "session_id": safe_int(item.get("session_id")),
            "material_id": material_id,
            "title": title,
            "session_title": item.get("session_title") or "",
            "material_name": item.get("material_name") or "",
            "href": href,
            "haystack": " ".join([
                str(item.get("session_title") or ""),
                str(item.get("material_name") or ""),
                str(item.get("material_path") or ""),
                truncate_text(summary or item.get("session_content"), 900),
            ]).lower(),
        })
        if material_id:
            seen_material_ids.add(material_id)

    supplemental_rows = conn.execute(
        """
        SELECT DISTINCT m.id AS material_id,
               m.name AS material_name,
               m.material_path,
               m.ai_parse_result_json,
               m.ai_optimized_markdown,
               '' AS session_title,
               '' AS session_content
        FROM course_materials m
        JOIN (
            SELECT home_learning_material_id AS material_id
            FROM class_offerings
            WHERE id = ? AND home_learning_material_id IS NOT NULL
            UNION
            SELECT material_id
            FROM course_material_assignments
            WHERE class_offering_id = ?
        ) scoped ON scoped.material_id = m.id
        WHERE m.node_type = 'file'
        ORDER BY m.name COLLATE NOCASE
        LIMIT 12
        """,
        (int(class_offering_id), int(class_offering_id)),
    ).fetchall()
    for row in supplemental_rows:
        item = dict(row)
        material_id = safe_int(item.get("material_id"))
        if material_id in seen_material_ids:
            continue
        title = str(item.get("material_name") or item.get("material_path") or "").strip()
        if not title:
            continue
        summary = _material_summary_text(item)
        candidates.append({
            "session_id": 0,
            "material_id": material_id,
            "title": title,
            "session_title": "",
            "material_name": title,
            "href": _retreat_material_href(class_offering_id, material_id),
            "haystack": " ".join([title, str(item.get("material_path") or ""), truncate_text(summary, 900)]).lower(),
        })
        seen_material_ids.add(material_id)
    return candidates


def _best_retreat_material_match(weak_point: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not candidates:
        return None
    weak_lower = str(weak_point or "").lower()
    weak_tokens = _match_tokens(weak_lower)
    best: tuple[int, dict[str, Any] | None] = (0, None)
    for candidate in candidates:
        title = str(candidate.get("title") or "").lower()
        haystack = str(candidate.get("haystack") or "").lower()
        score = 0
        if title and title in weak_lower:
            score += 12
        if title and weak_lower in title:
            score += 8
        material_name = str(candidate.get("material_name") or "").lower()
        session_title = str(candidate.get("session_title") or "").lower()
        for label in (material_name, session_title):
            if label and label in weak_lower:
                score += 8
        score += sum(1 for token in weak_tokens if len(token) >= 2 and token in haystack)
        if score > best[0]:
            best = (score, candidate)
    return best[1] if best[0] >= 2 else None


def build_stage_exam_retreat_plan(
    conn,
    attempt: dict[str, Any],
    level: dict[str, Any],
    *,
    feedback_md: Any = "",
) -> dict[str, Any]:
    class_offering_id = safe_int(attempt.get("class_offering_id"))
    attempt_id = safe_int(attempt.get("id"))
    submission_id = safe_int(attempt.get("submission_id"))
    score = safe_float(attempt.get("submission_score", attempt.get("score")))
    weak_points = _extract_stage_retreat_weak_points(feedback_md, score=score, level=level)
    candidates = _load_stage_retreat_material_candidates(conn, class_offering_id, level=level) if class_offering_id else []
    items: list[dict[str, Any]] = []
    for index, weak_point in enumerate(weak_points[:STAGE_EXAM_RETREAT_MAX_ITEMS], start=1):
        match = _best_retreat_material_match(weak_point, candidates)
        has_material = bool(match and safe_int(match.get("material_id")))
        href = str((match or {}).get("href") or f"/classroom/{class_offering_id}")
        target_type = "material" if has_material else "stage_retreat"
        target_id = str((match or {}).get("material_id") or attempt_id or f"{class_offering_id}:{index}")
        material_label = str((match or {}).get("title") or "课堂材料").strip()
        reflection_prompt = (
            "用自己的话写下：这个薄弱点原来卡在哪里、材料里怎么解释、下一次答题如何避免。"
        )
        items.append({
            "key": f"stage-retreat:{attempt_id}:{index}",
            "title": f"闭关 {index}：{truncate_text(weak_point, 28)}",
            "weak_point": weak_point,
            "description": (
                f"{weak_point}。"
                f"{'建议回到《' + material_label + '》核对原文。' if has_material else '暂未匹配到具体材料，先按反馈做文本复盘。'}"
            ),
            "href": href,
            "action_label": "研读材料" if has_material else "回到课堂",
            "target_type": target_type,
            "target_id": target_id,
            "material_id": safe_int((match or {}).get("material_id")),
            "session_id": safe_int((match or {}).get("session_id")),
            "material_title": material_label if has_material else "",
            "reflection_prompt": reflection_prompt,
            "estimated_minutes": 12 if has_material else 8,
        })

    summary_points = [truncate_text(item["weak_point"], 34) for item in items[:3]]
    summary = "；".join(summary_points) or f"{level.get('name') or '当前境界'}试炼需要先复盘薄弱点"
    return {
        "version": 1,
        "generated_at": now_iso(),
        "attempt_id": attempt_id,
        "submission_id": submission_id,
        "stage_key": normalize_level_key(level.get("key") or attempt.get("stage_key")),
        "stage_name": level.get("name") or "",
        "score": score,
        "summary": truncate_text(summary, 180),
        "items": items,
    }


def _load_stage_exam_retreat_prompt_block(
    conn,
    class_offering_id: int,
    student_id: int,
    stage_key: str,
    *,
    limit: int = 2,
) -> str:
    rows = conn.execute(
        """
        SELECT id, score, graded_at, metadata_json
        FROM learning_stage_exam_attempts
        WHERE class_offering_id = ?
          AND student_id = ?
          AND stage_key = ?
          AND status = 'failed'
        ORDER BY COALESCE(graded_at, generated_at) DESC, id DESC
        LIMIT ?
        """,
        (int(class_offering_id), int(student_id), normalize_level_key(stage_key), max(1, int(limit))),
    ).fetchall()
    lines: list[str] = []
    for row in rows:
        metadata = json_loads(row["metadata_json"], {})
        if not isinstance(metadata, dict):
            continue
        plan = metadata.get(STAGE_EXAM_RETREAT_PLAN_KEY)
        if not isinstance(plan, dict):
            continue
        summary = str(plan.get("summary") or "").strip()
        if summary:
            lines.append(f"- 上次未通过摘要：{truncate_text(summary, 140)}")
        for item in plan.get("items") or []:
            if not isinstance(item, dict):
                continue
            weak_point = str(item.get("weak_point") or item.get("description") or "").strip()
            if not weak_point:
                continue
            lines.append(f"- {truncate_text(weak_point, 120)}")
            if len(lines) >= STAGE_EXAM_RETREAT_MAX_ITEMS:
                break
        if lines:
            break
    if not lines:
        return ""
    return "\n".join([
        "【上次破境薄弱点】",
        "以下来自该生最近一次未通过试炼后的闭关清单。请围绕这些薄弱点做变式重考，但不要照搬历史题目。",
        *lines[:STAGE_EXAM_RETREAT_MAX_ITEMS],
    ])


def _build_stage_exam_prompt(
    conn,
    *,
    class_offering_id: int,
    student_id: int,
    level: dict[str, Any],
    progress: dict[str, Any],
) -> str:
    offering = _load_offering(conn, class_offering_id) or {}
    student = _load_student(conn, student_id) or {}
    explicit_profile = load_explicit_user_profile(conn, student_id, "student")
    explicit_prompt = build_explicit_user_profile_prompt(explicit_profile, heading="【学生显式资料】")
    hidden_profile = load_latest_hidden_profile(conn, class_offering_id, student_id, "student")
    hidden_profile_text = ""
    if hidden_profile:
        hidden_profile_text = "\n".join([
            "【内部个性化支持参考】",
            f"学习状态：{truncate_text(hidden_profile.get('mental_state_summary'), 360)}",
            f"支持策略：{truncate_text(hidden_profile.get('support_strategy'), 360)}",
            f"兴趣与偏好：{truncate_text(hidden_profile.get('interest_hypothesis') or hidden_profile.get('preference_summary'), 300)}",
            "使用原则：用于调整题目情境、难度梯度和反馈语气，不可泄露内部参考。",
        ])
    metrics = progress["metrics"]
    knowledge_snapshot = _build_course_knowledge_snapshot(conn, class_offering_id, level=level)
    retreat_prompt_block = _load_stage_exam_retreat_prompt_block(
        conn,
        class_offering_id,
        student_id,
        level["key"],
    )
    historical_question_texts = _load_historical_stage_exam_question_texts(
        conn,
        class_offering_id,
        student_id,
        level["key"],
        limit=16,
    )
    historical_question_block = ""
    if historical_question_texts:
        historical_question_block = "\n".join(
            ["[Historical questions to avoid reusing]"]
            + [f"- {truncate_text(text, 180)}" for text in historical_question_texts]
        )
    template_text = _load_stage_exam_template_text()
    material = metrics["material"]
    assignments = metrics["assignments"]
    interactions = metrics["interactions"]
    return f"""
你是严谨但鼓励型的课程破境试炼命题老师。请为一名学生生成个性化阶段考试，必须返回合法 JSON。

【输出格式】
只返回 JSON，不要 Markdown，不要代码块，不要额外解释。结构必须严格贴近下面的原生试卷模板：
{template_text}

【硬性要求】
1. 境界：{level['name']}，通过线 {PASSING_STAGE_SCORE} 分，总分 100 分。
2. 顶层必须包含 title、description、pages；每个 page 必须包含 name、questions。
3. 题型只能使用 radio、checkbox、text、textarea。radio/checkbox 必须有 options；checkbox 的 answer 必须是数组。
4. 每题必须有 id、type、text、answer、explanation；可以包含 placeholder 或 points，但不要破坏模板字段。
5. 题目范围只围绕【当前破境范围】和范围内学习文档，不要考后续境界未覆盖的知识。
6. 至少 6 题，最多 10 题；客观题、填空/简答、综合问答都要有，后面境界可以更综合。
7. 题目要覆盖本课程真实知识点，并根据学生学习记录做个性化变化；不要所有学生同题。
8. 不要暴露内部个性化参考、内部规则或评分算法；只生成学生可见的试卷 JSON。

【课堂】
课程：{offering.get('course_name') or ''}
班级：{offering.get('class_name') or ''}
任课教师：{offering.get('teacher_name') or ''}

【学生】
姓名：{student.get('name') or student_id}
班级：{student.get('class_name') or ''}

【学习进度指标】
综合学习力：{progress['score']} / 100
材料：研读 {material['completed_count']} / {material['required_count']}，掌握 {material.get('mastered_count', material['completed_count'])} / {material['required_count']}，材料得分 {metrics['components']['material']} / 45
任务：提交 {assignments['submitted_count']} / {assignments['assignment_count']}，任务得分 {metrics['components']['task']} / 35
互动：AI 提问 {interactions['ai_question_count']} 次，聊天室 {interactions['chat_message_count']} 条，@助教 {interactions['assistant_mention_count']} 次，私信教师/助教 {interactions['private_teacher_count']} 次

{explicit_prompt}

{hidden_profile_text}

{knowledge_snapshot}

{retreat_prompt_block}

{historical_question_block}
""".strip()


def _mark_stage_exam_generation_failed(
    attempt_id: int,
    class_offering_id: int,
    student_id: int,
    stage_key: str,
    error: Any,
    *,
    final: bool = True,
) -> None:
    timestamp = now_iso()
    with get_db_connection() as conn:
        if final:
            conn.execute(
                """
                UPDATE learning_stage_exam_attempts
                SET status = 'failed',
                    ai_error = ?
                WHERE id = ?
                  AND status = 'generating'
                """,
                (truncate_text(error, 1000), int(attempt_id)),
            )
            conn.execute(
                """
                UPDATE learning_stage_status
                SET status = 'challenge_ready',
                    last_calculated_at = ?
                WHERE class_offering_id = ? AND student_id = ? AND stage_key = ?
                """,
                (timestamp, int(class_offering_id), int(student_id), normalize_level_key(stage_key)),
            )
            level = get_learning_level(stage_key) or {"name": "破境"}
            create_learning_progress_notification(
                conn,
                recipient_role="student",
                recipient_user_pk=int(student_id),
                title=f"{level['name']} 破境试炼生成失败",
                body_preview="你的破境资格已保留，可以稍后重新发起试炼。",
                link_url=f"/classroom/{int(class_offering_id)}",
                class_offering_id=int(class_offering_id),
                ref_id=f"stage-exam:{int(attempt_id)}:failed",
                actor_role=AI_ASSISTANT_ROLE,
                actor_display_name=AI_ASSISTANT_LABEL,
                metadata={"attempt_id": int(attempt_id), "stage_key": normalize_level_key(stage_key)},
            )
        else:
            conn.execute(
                """
                UPDATE learning_stage_exam_attempts
                SET ai_error = ?
                WHERE id = ?
                  AND status = 'generating'
                """,
                (truncate_text(error, 1000), int(attempt_id)),
            )
        conn.commit()


def _stage_exam_generation_dedupe_key(class_offering_id: int, student_id: int, stage_key: str) -> str:
    return f"stage-exam-generation:{int(class_offering_id)}:{int(student_id)}:{normalize_level_key(stage_key)}"


def _schedule_stage_exam_generation_task(
    conn,
    *,
    attempt_id: int,
    class_offering_id: int,
    student_id: int,
    stage_key: str,
) -> int:
    from .scheduled_task_service import schedule_task

    return schedule_task(
        conn,
        task_kind=STAGE_EXAM_GENERATION_TASK_KIND,
        run_at=datetime.now(),
        payload={
            "attempt_id": int(attempt_id),
            "class_offering_id": int(class_offering_id),
            "student_id": int(student_id),
            "stage_key": normalize_level_key(stage_key),
        },
        dedupe_key=_stage_exam_generation_dedupe_key(class_offering_id, student_id, stage_key),
        owner_role="student",
        owner_user_pk=int(student_id),
        title="生成个人破境试炼",
        priority=20,
        max_attempts=STAGE_EXAM_GENERATION_MAX_ATTEMPTS,
        replace=True,
    )


def _publish_generated_stage_exam(
    *,
    attempt_id: int,
    class_offering_id: int,
    student_id: int,
    stage_key: str,
    level: dict[str, Any],
    exam_payload: dict[str, Any],
    quality_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with get_db_connection() as conn:
        begin_immediate_transaction(conn)
        attempt = conn.execute(
            """
            SELECT *
            FROM learning_stage_exam_attempts
            WHERE id = ?
            LIMIT 1
            """,
            (int(attempt_id),),
        ).fetchone()
        if not attempt:
            conn.commit()
            return {"status": "skipped", "message": "attempt missing"}
        if str(attempt["status"] or "") != "generating":
            conn.commit()
            assignment_id = safe_int(attempt["assignment_id"])
            return {
                "status": "skipped",
                "message": f"attempt already {attempt['status']}",
                "assignment_id": assignment_id,
            }

        offering = _load_offering(conn, int(class_offering_id))
        student = _load_student(conn, int(student_id))
        if not offering or not student:
            raise ValueError("课堂或学生不存在")
        teacher_id = int(offering["teacher_id"])
        paper_id = f"stage-{class_offering_id}-{student_id}-{stage_key}-{uuid.uuid4().hex[:10]}"
        assignment_title = f"破境试炼 · {level['name']} · {student.get('name') or student_id}"
        timestamp = now_iso()
        exam_config = {
            "source": "learning_stage",
            "stage_key": stage_key,
            "pass_score": PASSING_STAGE_SCORE,
            "interaction_quality_note": "互动看质量：具体的问题、对讨论的贡献比条数更重要。",
            "personalized_for_student_id": int(student_id),
            "generated_at": timestamp,
        }
        conn.execute(
            """
            INSERT INTO exam_papers (
                id, teacher_id, title, description, questions_json, exam_config_json,
                status, ai_gen_status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'published', 'completed', ?, ?)
            """,
            (
                paper_id,
                teacher_id,
                assignment_title,
                f"{level['name']} 个性化破境试炼，80 分通过后发放晋级道印。",
                json.dumps(exam_payload, ensure_ascii=False),
                json.dumps(exam_config, ensure_ascii=False),
                timestamp,
                timestamp,
            ),
        )
        assignment_id = execute_insert_returning_id(
            conn,
            """
            INSERT INTO assignments (
                course_id, title, status, requirements_md, rubric_md, grading_mode,
                exam_paper_id, class_offering_id, allowed_file_types_json,
                availability_mode, starts_at, due_at, duration_minutes, auto_close
            )
            VALUES (?, ?, 'published', ?, ?, 'ai', ?, ?, '[]', 'permanent', NULL, NULL, 90, 0)
            """,
            (
                int(offering["course_id"]),
                assignment_title,
                f"这是 {level['name']} 个性化破境试炼。完成后系统会自动提交 AI 批改，达到 {PASSING_STAGE_SCORE} 分即可获得 {level['certificate_title']}。",
                f"总分 100 分。请依据题目参考答案和 rubric 批改，达到 {PASSING_STAGE_SCORE} 分视为通过 {level['name']} 境界。对主观题关注知识准确性、推理过程、课程术语使用和应用迁移能力。",
                paper_id,
                int(class_offering_id),
            ),
        )
        conn.execute(
            """
            UPDATE learning_stage_exam_attempts
            SET assignment_id = ?,
                exam_paper_id = ?,
                status = 'generated',
                generated_at = COALESCE(generated_at, ?),
                metadata_json = ?
            WHERE id = ?
            """,
            (
                assignment_id,
                paper_id,
                timestamp,
                json.dumps(
                    {
                        "level_name": level["name"],
                        "pass_score": PASSING_STAGE_SCORE,
                        "quality_guard": quality_report or {},
                    },
                    ensure_ascii=False,
                ),
                int(attempt_id),
            ),
        )
        conn.execute(
            """
            UPDATE learning_stage_status
            SET status = 'in_exam',
                last_exam_assignment_id = ?,
                unlocked_at = COALESCE(unlocked_at, ?),
                last_calculated_at = ?
            WHERE class_offering_id = ? AND student_id = ? AND stage_key = ?
            """,
            (assignment_id, timestamp, timestamp, int(class_offering_id), int(student_id), stage_key),
        )
        create_learning_progress_notification(
            conn,
            recipient_role="student",
            recipient_user_pk=int(student_id),
            title=f"{level['name']} 破境试炼已生成",
            body_preview=f"完成并达到 {PASSING_STAGE_SCORE} 分即可获得 {level['certificate_title']}。",
            link_url=f"/exam/take/{assignment_id}",
            class_offering_id=int(class_offering_id),
            ref_id=f"stage-exam:{assignment_id}:student",
            actor_role=AI_ASSISTANT_ROLE,
            actor_display_name=AI_ASSISTANT_LABEL,
            metadata={"assignment_id": assignment_id, "stage_key": stage_key},
        )
        refresh_student_learning_state(
            conn,
            int(class_offering_id),
            int(student_id),
            event_source_ref=f"stage-exam:{assignment_id}:generated",
        )
        conn.commit()
        return {
            "status": "success",
            "assignment_id": assignment_id,
            "exam_url": f"/exam/take/{assignment_id}",
            "stage": level,
        }


async def generate_personal_stage_exam_from_attempt(
    attempt_id: int,
    *,
    task_attempt_count: int = 0,
    max_attempts: int = STAGE_EXAM_GENERATION_MAX_ATTEMPTS,
) -> dict[str, Any]:
    with get_db_connection() as conn:
        attempt = conn.execute(
            """
            SELECT *
            FROM learning_stage_exam_attempts
            WHERE id = ?
            LIMIT 1
            """,
            (int(attempt_id),),
        ).fetchone()
        if not attempt:
            return {"status": "skipped", "message": "attempt missing"}
        if str(attempt["status"] or "") != "generating":
            return {"status": "skipped", "message": f"attempt already {attempt['status']}"}
        class_offering_id = int(attempt["class_offering_id"])
        student_id = int(attempt["student_id"])
        stage_key = normalize_level_key(attempt["stage_key"])
        level = get_learning_level(stage_key)
        if not level:
            _mark_stage_exam_generation_failed(
                int(attempt_id),
                class_offering_id,
                student_id,
                stage_key,
                "unknown stage",
                final=True,
            )
            raise ValueError("未知的修行境界")
        offering = _load_offering(conn, class_offering_id)
        if not offering:
            _mark_stage_exam_generation_failed(
                int(attempt_id),
                class_offering_id,
                student_id,
                stage_key,
                "class offering missing",
                final=True,
            )
            raise ValueError("课堂不存在")
        teacher_id = int(offering["teacher_id"])
        progress = refresh_student_learning_state(
            conn,
            class_offering_id,
            student_id,
            event_source_ref=f"stage-exam:{int(attempt_id)}:generation",
        )
        prompt = _build_stage_exam_prompt(
            conn,
            class_offering_id=class_offering_id,
            student_id=student_id,
            level=level,
            progress=progress,
        )
        conn.commit()

    payload = {
        "prompt": prompt,
        "model_type": "thinking",
        "task_type": "stage_exam_generation",
        "teacher_id": teacher_id,
        "class_offering_id": class_offering_id,
        # /api/ai/generate-exam 的 source_type 是 AI 服务协议字段；
        # 阶段试炼的业务语义保留在 task_type、prompt 和 exam_config 中。
        "source_type": AI_EXAM_SOURCE_TYPE_STAGE,
    }
    try:
        response = await ai_gateway_post(
            ai_client,
            "/api/ai/generate-exam",
            json_payload=payload,
            timeout=300.0,
            task_type="stage_exam_generation",
            priority="P0",
            class_offering_id=class_offering_id,
            student_id=student_id,
            teacher_id=teacher_id,
            source_ref=f"stage-exam:{int(attempt_id)}",
            metadata={"stage_key": stage_key},
        )
        response.raise_for_status()
        exam_payload = _normalize_exam_payload(response.json())
        with get_db_connection() as quality_conn:
            historical_question_texts = _load_historical_stage_exam_question_texts(
                quality_conn,
                class_offering_id,
                student_id,
                stage_key,
            )
        quality_report = _validate_stage_exam_quality(exam_payload, historical_question_texts)
    except httpx.ConnectError as exc:
        final = int(task_attempt_count or 0) + 1 >= int(max_attempts or STAGE_EXAM_GENERATION_MAX_ATTEMPTS)
        _mark_stage_exam_generation_failed(attempt_id, class_offering_id, student_id, stage_key, exc, final=final)
        raise ConnectionError("AI 助手服务未运行，请先启动 ai_assistant.py。") from exc
    except httpx.HTTPStatusError as exc:
        final = int(task_attempt_count or 0) + 1 >= int(max_attempts or STAGE_EXAM_GENERATION_MAX_ATTEMPTS)
        detail = exc.response.text[:1000] if exc.response is not None else str(exc)
        _mark_stage_exam_generation_failed(attempt_id, class_offering_id, student_id, stage_key, detail, final=final)
        raise RuntimeError(f"AI 出卷失败：{detail}") from exc
    except Exception as exc:
        final = int(task_attempt_count or 0) + 1 >= int(max_attempts or STAGE_EXAM_GENERATION_MAX_ATTEMPTS)
        _mark_stage_exam_generation_failed(attempt_id, class_offering_id, student_id, stage_key, exc, final=final)
        raise

    return _publish_generated_stage_exam(
        attempt_id=int(attempt_id),
        class_offering_id=class_offering_id,
        student_id=student_id,
        stage_key=stage_key,
        level=level,
        exam_payload=exam_payload,
        quality_report=quality_report,
    )


async def create_personal_stage_exam(class_offering_id: int, student_id: int, stage_key: str) -> dict[str, Any]:
    stage_key = normalize_level_key(stage_key)
    level = get_learning_level(stage_key)
    if not level:
        raise ValueError("未知的修行境界")

    generation_attempt_id = 0
    with get_db_connection() as conn:
        begin_immediate_transaction(conn)
        offering = _load_offering(conn, int(class_offering_id))
        if not offering:
            raise ValueError("课堂不存在")
        progress = refresh_student_learning_state(conn, int(class_offering_id), int(student_id))
        active = conn.execute(
            """
            SELECT *
            FROM learning_stage_exam_attempts
            WHERE class_offering_id = ?
              AND student_id = ?
              AND stage_key = ?
              AND status IN ('generating', 'generated', 'submitted', 'grading')
            ORDER BY generated_at DESC, id DESC
            LIMIT 1
            """,
            (class_offering_id, student_id, stage_key),
        ).fetchone()
        if active:
            conn.commit()
            assignment_id = safe_int(active["assignment_id"])
            if not assignment_id:
                with get_db_connection() as task_conn:
                    task_id = _schedule_stage_exam_generation_task(
                        task_conn,
                        attempt_id=int(active["id"]),
                        class_offering_id=int(class_offering_id),
                        student_id=int(student_id),
                        stage_key=stage_key,
                    )
                    task_conn.commit()
                return {
                    "status": "generating",
                    "task_id": task_id,
                    "message": "AI 正在生成破境试炼，请稍后刷新。",
                    "stage": level,
                }
            return {
                "status": "exists",
                "assignment_id": assignment_id,
                "exam_url": f"/exam/take/{active['assignment_id']}",
                "stage": level,
            }
        eligible = progress.get("eligible_stage")
        if not eligible or eligible.get("key") != stage_key:
            raise PermissionError("当前还未达到该境界破境条件")
        ensure_stage_exam_generation_quota(
            conn,
            class_offering_id=int(class_offering_id),
            student_id=int(student_id),
            stage_key=stage_key,
        )
        timestamp = now_iso()
        generation_attempt_id = execute_insert_returning_id(
            conn,
            """
            INSERT INTO learning_stage_exam_attempts (
                class_offering_id, student_id, stage_key, status, generated_at, metadata_json
            )
            VALUES (?, ?, ?, 'generating', ?, ?)
            """,
            (
                int(class_offering_id),
                int(student_id),
                stage_key,
                timestamp,
                json.dumps({"level_name": level["name"], "pass_score": PASSING_STAGE_SCORE}, ensure_ascii=False),
            ),
        )
        conn.execute(
            """
            UPDATE learning_stage_status
            SET status = 'generating',
                unlocked_at = COALESCE(unlocked_at, ?),
                last_calculated_at = ?
            WHERE class_offering_id = ? AND student_id = ? AND stage_key = ?
            """,
            (timestamp, timestamp, int(class_offering_id), int(student_id), stage_key),
        )
        task_id = _schedule_stage_exam_generation_task(
            conn,
            attempt_id=generation_attempt_id,
            class_offering_id=int(class_offering_id),
            student_id=int(student_id),
            stage_key=stage_key,
        )
        conn.commit()
        return {
            "status": "generating",
            "attempt_id": generation_attempt_id,
            "task_id": task_id,
            "message": "破境试炼已进入生成队列，可以先去做别的，生成后会在消息中心提醒你。",
            "stage": level,
        }


def delete_personal_stage_exam(class_offering_id: int, student_id: int, stage_key: str) -> dict[str, Any]:
    stage_key = normalize_level_key(stage_key)
    level = get_learning_level(stage_key)
    if not level:
        raise ValueError("未知的修行境界")

    storage_roots: list[Path] = []
    with get_db_connection() as conn:
        begin_immediate_transaction(conn)
        row = conn.execute(
            """
            SELECT lsea.*,
                   a.course_id,
                   a.exam_paper_id AS assignment_exam_paper_id,
                   ep.exam_config_json,
                   (
                     SELECT COUNT(*)
                     FROM submissions s
                     WHERE s.assignment_id = lsea.assignment_id
                       AND s.student_pk_id = lsea.student_id
                       AND s.status IN ('submitted', 'grading')
                   ) AS pending_submission_count
            FROM learning_stage_exam_attempts lsea
            LEFT JOIN assignments a ON a.id = lsea.assignment_id
            LEFT JOIN exam_papers ep ON ep.id = lsea.exam_paper_id
            WHERE lsea.class_offering_id = ?
              AND lsea.student_id = ?
              AND lsea.stage_key = ?
              AND lsea.status IN ('generated', 'failed')
            ORDER BY lsea.generated_at DESC, lsea.id DESC
            LIMIT 1
            """,
            (int(class_offering_id), int(student_id), stage_key),
        ).fetchone()
        if not row:
            conn.rollback()
            raise ValueError("当前没有可删除的个人破境试炼")

        attempt = dict(row)
        if safe_int(attempt.get("pending_submission_count")):
            conn.rollback()
            raise PermissionError("试炼正在提交或批改中，暂时不能删除")
        config = json_loads(attempt.get("exam_config_json"), {})
        if config.get("source") != "learning_stage":
            conn.rollback()
            raise PermissionError("教师发布的试炼不能由学生删除")

        assignment_id = safe_int(attempt.get("assignment_id"))
        paper_id = str(attempt.get("exam_paper_id") or attempt.get("assignment_exam_paper_id") or "")
        course_id = safe_int(attempt.get("course_id"))
        if assignment_id and course_id:
            storage_roots.append(HOMEWORK_SUBMISSIONS_DIR / str(course_id) / str(assignment_id))

        submission_rows = conn.execute(
            "SELECT id FROM submissions WHERE assignment_id = ? AND student_pk_id = ?",
            (assignment_id, int(student_id)),
        ).fetchall() if assignment_id else []
        submission_ids = [int(item["id"]) for item in submission_rows]
        for submission_id in submission_ids:
            conn.execute("DELETE FROM submission_files WHERE submission_id = ?", (submission_id,))
        if submission_ids:
            placeholders = ",".join("?" for _ in submission_ids)
            conn.execute(f"DELETE FROM submissions WHERE id IN ({placeholders})", submission_ids)

        conn.execute(
            """
            UPDATE learning_stage_status
            SET status = 'challenge_ready',
                last_exam_assignment_id = NULL,
                last_calculated_at = ?
            WHERE class_offering_id = ? AND student_id = ? AND stage_key = ?
            """,
            (now_iso(), int(class_offering_id), int(student_id), stage_key),
        )
        conn.execute("DELETE FROM learning_stage_exam_attempts WHERE id = ?", (int(attempt["id"]),))
        if assignment_id:
            conn.execute("DELETE FROM assignments WHERE id = ?", (assignment_id,))
        if paper_id:
            conn.execute("DELETE FROM exam_papers WHERE id = ?", (paper_id,))
        conn.commit()

    for storage_root in storage_roots:
        delete_storage_tree(storage_root)
    return {
        "status": "success",
        "deleted_assignment_id": assignment_id,
        "stage": level,
        "message": "个人破境试炼已删除，可以重新生成。",
    }


def mark_stage_submission_saved(conn, submission_id: int | str) -> Optional[dict[str, Any]]:
    row = conn.execute(
        """
        SELECT lsea.*, s.status AS submission_status
        FROM learning_stage_exam_attempts lsea
        JOIN submissions s ON s.assignment_id = lsea.assignment_id
                         AND s.student_pk_id = lsea.student_id
        WHERE s.id = ?
        ORDER BY lsea.id DESC
        LIMIT 1
        """,
        (submission_id,),
    ).fetchone()
    if not row:
        return None
    attempt = dict(row)
    timestamp = now_iso()
    next_status = "grading"
    conn.execute(
        """
        UPDATE learning_stage_exam_attempts
        SET status = ?,
            submitted_at = COALESCE(submitted_at, ?),
            metadata_json = metadata_json
        WHERE id = ?
        """,
        (next_status, timestamp, int(attempt["id"])),
    )
    conn.execute(
        """
        UPDATE learning_stage_status
        SET status = 'in_exam',
            last_exam_assignment_id = ?,
            last_calculated_at = ?
        WHERE class_offering_id = ? AND student_id = ? AND stage_key = ?
        """,
        (
            attempt["assignment_id"],
            timestamp,
            attempt["class_offering_id"],
            attempt["student_id"],
            attempt["stage_key"],
        ),
    )
    return attempt


def _extract_answer_attachment_context(answers_json: str | None) -> dict[str, dict[str, str]]:
    payload = json_loads(answers_json, {})
    answers = payload.get("answers", payload) if isinstance(payload, dict) else payload
    items: list[dict[str, Any]]
    if isinstance(answers, dict):
        items = [{"question_id": key, **value} if isinstance(value, dict) else {"question_id": key, "answer": value} for key, value in answers.items()]
    elif isinstance(answers, list):
        items = [item for item in answers if isinstance(item, dict)]
    else:
        return {}
    result: dict[str, dict[str, str]] = {}
    for index, item in enumerate(items, start=1):
        question_id = str(item.get("question_id") or index)
        question_text = str(item.get("question") or item.get("title") or f"第{index}题")
        for attachment in item.get("attachments") or []:
            if not isinstance(attachment, dict):
                continue
            relative_path = str(attachment.get("relative_path") or attachment.get("stored_relative_path") or "").strip()
            file_name = str(attachment.get("file_name") or attachment.get("filename") or "").strip()
            if not relative_path.startswith("exam_drawings/"):
                continue
            context = {
                "label": f"第{question_id}题附图 - {question_text[:80]}",
                "question_id": question_id,
                "relative_path": relative_path,
                "file_name": file_name,
            }
            for key in (relative_path, file_name, Path(relative_path).name):
                if key:
                    result[str(key).lower()] = context
    return result


def _apply_attachment_context(item: dict[str, Any], context_by_file: dict[str, dict[str, str]]) -> dict[str, Any]:
    keys = {
        str(item.get("relative_path") or "").lower(),
        str(item.get("original_filename") or "").lower(),
    }
    context = next((context_by_file[key] for key in keys if key in context_by_file), None)
    if context:
        original = item.get("relative_path") or item.get("original_filename") or ""
        item["relative_path"] = f"{context['label']} | {original}"
    return item


async def submit_stage_exam_for_ai_grading(submission_id: int) -> None:
    try:
        with get_db_connection() as conn:
            submission = conn.execute(
                """
                SELECT s.*,
                       lsea.id AS attempt_id
                FROM submissions s
                JOIN assignments a ON a.id = s.assignment_id
                JOIN learning_stage_exam_attempts lsea
                     ON lsea.assignment_id = a.id
                    AND lsea.student_id = s.student_pk_id
                WHERE s.id = ?
                LIMIT 1
                """,
                (submission_id,),
            ).fetchone()
            if not submission or int(submission["resubmission_allowed"] or 0):
                return
        await submit_submission_for_ai_grading(int(submission_id), allow_graded=False)
    except Exception as exc:
        with get_db_connection() as conn:
            conn.execute(
                """
                UPDATE submissions
                SET status = 'submitted',
                    grading_started_at = NULL,
                    grading_attempt_fingerprint = NULL
                WHERE id = ? AND status = 'grading'
                """,
                (int(submission_id),),
            )
            conn.execute(
                """
                UPDATE learning_stage_exam_attempts
                SET status = 'submitted',
                    ai_error = ?
                WHERE assignment_id = (
                    SELECT assignment_id FROM submissions WHERE id = ?
                )
                """,
                (str(exc)[:1000], int(submission_id)),
            )
            conn.commit()
        print(f"[LEARNING_PROGRESS] 破境试炼自动批改提交失败: {exc}")


def _ensure_stage_certificate(
    conn,
    *,
    class_offering_id: int,
    student_id: int,
    level: dict[str, Any],
    score: float,
    assignment_id: int | str,
    submission_id: int | str,
    course_name: str,
    source: str,
) -> dict[str, Any]:
    existing = conn.execute(
        """
        SELECT *
        FROM learning_certificates
        WHERE class_offering_id = ? AND student_id = ? AND stage_key = ?
        LIMIT 1
        """,
        (class_offering_id, student_id, level["key"]),
    ).fetchone()
    if existing:
        return dict(existing)

    timestamp = now_iso()
    cert_code = (
        f"LS-{int(class_offering_id):04d}-"
        f"{int(student_id):05d}-{level['key'].upper()}-"
        f"{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
    )
    certificate_id = execute_insert_returning_id(
        conn,
        """
        INSERT INTO learning_certificates (
            class_offering_id, student_id, stage_key, level_key, level_name,
            tier, title, certificate_code, issued_at, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            class_offering_id,
            student_id,
            level["key"],
            level["key"],
            level["name"],
            level["tier"],
            level["certificate_title"],
            cert_code,
            timestamp,
            json.dumps(
                {
                    "score": score,
                    "assignment_id": assignment_id,
                    "submission_id": int(submission_id),
                    "course_name": course_name,
                    "source": source,
                },
                ensure_ascii=False,
            ),
        ),
    )
    return {
        "id": certificate_id,
        "certificate_code": cert_code,
        "title": level["certificate_title"],
        "level_name": level["name"],
        "issued_at": timestamp,
    }


def _mark_stage_passed_by_certificate(
    conn,
    *,
    class_offering_id: int,
    student_id: int,
    level: dict[str, Any],
    certificate_id: int,
    assignment_id: int | str,
    score: float,
    timestamp: str,
) -> None:
    conn.execute(
        """
        INSERT INTO learning_stage_status (
            class_offering_id, student_id, stage_key, status,
            progress_score, readiness_score, unlocked_at, passed_at,
            last_exam_assignment_id, certificate_id, last_calculated_at, metadata_json
        )
        VALUES (?, ?, ?, 'passed', ?, 100, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(class_offering_id, student_id, stage_key)
        DO UPDATE SET
            status = 'passed',
            progress_score = CASE
                WHEN COALESCE(learning_stage_status.progress_score, 0) >= COALESCE(excluded.progress_score, 0)
                    THEN learning_stage_status.progress_score
                ELSE excluded.progress_score
            END,
            readiness_score = 100,
            unlocked_at = COALESCE(learning_stage_status.unlocked_at, excluded.unlocked_at),
            passed_at = COALESCE(learning_stage_status.passed_at, excluded.passed_at),
            last_exam_assignment_id = excluded.last_exam_assignment_id,
            certificate_id = excluded.certificate_id,
            last_calculated_at = excluded.last_calculated_at,
            metadata_json = excluded.metadata_json
        """,
        (
            class_offering_id,
            student_id,
            level["key"],
            max(float(score or 0), 100.0),
            timestamp,
            timestamp,
            assignment_id,
            certificate_id,
            timestamp,
            json.dumps(
                {
                    "level_name": level["name"],
                    "unlock_score": level["unlock_score"],
                    "pass_score": PASSING_STAGE_SCORE,
                    "source": "teacher_stage_assignment",
                },
                ensure_ascii=False,
            ),
        ),
    )


def handle_assignment_stage_grading_complete(conn, submission_id: int | str) -> Optional[dict[str, Any]]:
    row = conn.execute(
        """
        SELECT s.id AS submission_id,
               s.student_pk_id,
               s.student_name,
               s.status AS submission_status,
               s.score AS submission_score,
               a.id AS assignment_id,
               a.title AS assignment_title,
               a.learning_stage_key,
               a.class_offering_id,
               o.teacher_id,
               c.name AS course_name
        FROM submissions s
        JOIN assignments a ON a.id = s.assignment_id
        JOIN class_offerings o ON o.id = a.class_offering_id
        JOIN courses c ON c.id = o.course_id
        LEFT JOIN learning_stage_exam_attempts lsea ON lsea.assignment_id = a.id
        WHERE s.id = ?
          AND lsea.id IS NULL
        LIMIT 1
        """,
        (submission_id,),
    ).fetchone()
    if not row:
        return None
    payload = dict(row)
    stage_key = normalize_level_key(payload.get("learning_stage_key"))
    if not stage_key:
        return None
    target_level = get_learning_level(stage_key)
    if not target_level:
        return None
    score = safe_float(payload.get("submission_score"))
    if str(payload.get("submission_status")) != "graded" or score < PASSING_STAGE_SCORE:
        refresh_student_learning_state(
            conn,
            int(payload["class_offering_id"]),
            int(payload["student_pk_id"]),
            event_source_ref=f"grading:{submission_id}",
        )
        return {"status": "failed", "stage": target_level, "score": score}

    class_offering_id = int(payload["class_offering_id"])
    student_id = int(payload["student_pk_id"])
    timestamp = now_iso()
    target_tier = safe_int(target_level["tier"])
    awarded: list[dict[str, Any]] = []
    for level in LEARNING_LEVELS:
        if safe_int(level["tier"]) > target_tier:
            continue
        certificate = _ensure_stage_certificate(
            conn,
            class_offering_id=class_offering_id,
            student_id=student_id,
            level=level,
            score=score,
            assignment_id=payload["assignment_id"],
            submission_id=payload["submission_id"],
            course_name=str(payload.get("course_name") or ""),
            source="teacher_stage_assignment",
        )
        _mark_stage_passed_by_certificate(
            conn,
            class_offering_id=class_offering_id,
            student_id=student_id,
            level=level,
            certificate_id=int(certificate["id"]),
            assignment_id=payload["assignment_id"],
            score=score,
            timestamp=timestamp,
        )
        awarded.append({**public_level_payload(level), "certificate_id": int(certificate["id"])})

    classroom_link = f"/classroom/{class_offering_id}"
    level_names = "、".join(item["level_name"] for item in awarded)
    create_learning_progress_notification(
        conn,
        recipient_role="student",
        recipient_user_pk=student_id,
        title=f"通过教师试炼，点亮至 {target_level['name']}",
        body_preview=f"{payload.get('assignment_title') or '课堂试炼'} 得分 {score:g}，已点亮 {level_names}。",
        link_url=classroom_link,
        class_offering_id=class_offering_id,
        ref_id=f"teacher-stage-assignment:{submission_id}:student",
        actor_role=AI_ASSISTANT_ROLE,
        actor_display_name=AI_ASSISTANT_LABEL,
        metadata={"assignment_id": payload["assignment_id"], "stage_key": target_level["key"], "score": score},
    )
    refresh_student_learning_state(
        conn,
        class_offering_id,
        student_id,
        event_source_ref=f"stage:{submission_id}:passed",
    )
    return {"status": "passed", "stage": target_level, "score": score, "awarded": awarded}


def handle_stage_exam_grading_complete(conn, submission_id: int | str) -> Optional[dict[str, Any]]:
    row = conn.execute(
        """
        SELECT lsea.*,
               s.id AS submission_id,
               s.score AS submission_score,
               s.status AS submission_status,
               s.feedback_md AS submission_feedback_md,
               s.student_name,
               a.title AS assignment_title,
               o.teacher_id,
               c.name AS course_name
        FROM learning_stage_exam_attempts lsea
        JOIN submissions s ON s.assignment_id = lsea.assignment_id
                         AND s.student_pk_id = lsea.student_id
        JOIN assignments a ON a.id = s.assignment_id
        JOIN class_offerings o ON o.id = lsea.class_offering_id
        JOIN courses c ON c.id = o.course_id
        WHERE s.id = ?
        ORDER BY lsea.id DESC
        LIMIT 1
        """,
        (submission_id,),
    ).fetchone()
    if not row:
        return None
    attempt = dict(row)
    level = get_learning_level(str(attempt["stage_key"]))
    if not level:
        return None
    timestamp = now_iso()
    score = safe_float(attempt.get("submission_score"))
    passed = score >= PASSING_STAGE_SCORE and str(attempt.get("submission_status")) == "graded"
    if not passed:
        metadata = json_loads(attempt.get("metadata_json"), {})
        if not isinstance(metadata, dict):
            metadata = {}
        retreat_plan = build_stage_exam_retreat_plan(
            conn,
            attempt,
            level,
            feedback_md=attempt.get("submission_feedback_md"),
        )
        metadata[STAGE_EXAM_RETREAT_PLAN_KEY] = retreat_plan
        conn.execute(
            """
            UPDATE learning_stage_exam_attempts
            SET status = 'failed',
                score = ?,
                graded_at = ?,
                metadata_json = ?
            WHERE id = ?
            """,
            (score, timestamp, json.dumps(metadata, ensure_ascii=False), int(attempt["id"])),
        )
        conn.execute(
            """
            UPDATE learning_stage_status
            SET status = 'challenge_ready',
                progress_score = CASE
                    WHEN COALESCE(progress_score, 0) >= ? THEN progress_score
                    ELSE ?
                END,
                last_calculated_at = ?
            WHERE class_offering_id = ? AND student_id = ? AND stage_key = ?
            """,
            (score, score, timestamp, attempt["class_offering_id"], attempt["student_id"], attempt["stage_key"]),
        )
        refresh_student_learning_state(
            conn,
            int(attempt["class_offering_id"]),
            int(attempt["student_id"]),
            event_source_ref=f"stage:{submission_id}:failed",
        )
        retreat_count = len(retreat_plan.get("items") or [])
        create_learning_progress_notification(
            conn,
            recipient_role="student",
            recipient_user_pk=int(attempt["student_id"]),
            title=f"{level['name']} 试炼闭关清单已生成",
            body_preview=f"试炼显示 {retreat_count} 处薄弱点，闭关清单已生成。",
            link_url=f"/learning-path?status=active&q={quote('闭关')}",
            class_offering_id=int(attempt["class_offering_id"]),
            ref_id=f"stage-exam:{int(attempt['id'])}:retreat",
            actor_role=AI_ASSISTANT_ROLE,
            actor_display_name=AI_ASSISTANT_LABEL,
            metadata={
                "attempt_id": int(attempt["id"]),
                "submission_id": int(submission_id),
                "stage_key": level["key"],
                "score": score,
                "retreat_count": retreat_count,
            },
        )
        return {"status": "failed", "stage": level, "score": score, "retreat_plan": retreat_plan}

    existing = conn.execute(
        """
        SELECT *
        FROM learning_certificates
        WHERE class_offering_id = ? AND student_id = ? AND stage_key = ?
        LIMIT 1
        """,
        (attempt["class_offering_id"], attempt["student_id"], attempt["stage_key"]),
    ).fetchone()
    if existing:
        certificate = dict(existing)
    else:
        cert_code = (
            f"LS-{int(attempt['class_offering_id']):04d}-"
            f"{int(attempt['student_id']):05d}-{level['key'].upper()}-"
            f"{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
        )
        certificate_id = execute_insert_returning_id(
            conn,
            """
            INSERT INTO learning_certificates (
                class_offering_id, student_id, stage_key, level_key, level_name,
                tier, title, certificate_code, issued_at, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attempt["class_offering_id"],
                attempt["student_id"],
                attempt["stage_key"],
                level["key"],
                level["name"],
                level["tier"],
                level["certificate_title"],
                cert_code,
                timestamp,
                json.dumps(
                    {
                        "score": score,
                        "assignment_id": attempt["assignment_id"],
                        "submission_id": int(submission_id),
                        "course_name": attempt.get("course_name"),
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        certificate = {
            "id": certificate_id,
            "certificate_code": cert_code,
            "title": level["certificate_title"],
            "level_name": level["name"],
            "issued_at": timestamp,
        }
    conn.execute(
        """
        UPDATE learning_stage_exam_attempts
        SET status = 'passed',
            score = ?,
            graded_at = ?,
            passed_at = ?
        WHERE id = ?
        """,
        (score, timestamp, timestamp, int(attempt["id"])),
    )
    conn.execute(
        """
        UPDATE learning_stage_status
        SET status = 'passed',
            progress_score = CASE
                WHEN COALESCE(progress_score, 0) >= ? THEN progress_score
                ELSE ?
            END,
            readiness_score = 100,
            passed_at = ?,
            certificate_id = ?,
            last_exam_assignment_id = ?,
            last_calculated_at = ?
        WHERE class_offering_id = ? AND student_id = ? AND stage_key = ?
        """,
        (
            score,
            score,
            timestamp,
            certificate["id"],
            attempt["assignment_id"],
            timestamp,
            attempt["class_offering_id"],
            attempt["student_id"],
            attempt["stage_key"],
        ),
    )
    classroom_link = f"/classroom/{attempt['class_offering_id']}"
    create_learning_progress_notification(
        conn,
        recipient_role="student",
        recipient_user_pk=int(attempt["student_id"]),
        title=f"恭喜获得 {level['certificate_title']}",
        body_preview=f"{attempt.get('course_name') or '课堂'} {level['name']} 破境成功，得分 {score:g}。",
        link_url=classroom_link,
        class_offering_id=int(attempt["class_offering_id"]),
        ref_id=f"certificate:{certificate['id']}:student",
        actor_role=AI_ASSISTANT_ROLE,
        actor_display_name=AI_ASSISTANT_LABEL,
        metadata={"certificate_id": certificate["id"], "stage_key": level["key"], "score": score},
    )
    refresh_student_learning_state(
        conn,
        int(attempt["class_offering_id"]),
        int(attempt["student_id"]),
        event_source_ref=f"stage:{submission_id}:passed",
    )
    return {"status": "passed", "stage": level, "score": score, "certificate": certificate}
