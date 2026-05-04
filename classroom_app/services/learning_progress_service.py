from __future__ import annotations

import json
import math
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx

from ..core import ai_client
from ..database import get_db_connection
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


PASSING_STAGE_SCORE = 80
MATERIAL_COMPLETE_SCROLL = 0.86
MATERIAL_COMPLETE_ACTIVE_SECONDS = 180
MATERIAL_COMPLETE_TOTAL_SECONDS = 300

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
    "address_title": "初入道友",
    "aura_label": "灵根初醒",
    "description": "刚踏入课堂修行，先从稳定阅读和完成第一项任务开始。",
}

LEARNING_LEVELS: tuple[dict[str, Any], ...] = (
    {
        "key": "qi_awakening",
        "name": "引气入体",
        "short_name": "引气",
        "tier": 1,
        "unlock_score": 22,
        "theme": "qi_awakening",
        "certificate_title": "引气入体灵契",
        "address_title": "引气道友",
        "aura_label": "灵气入脉",
        "description": "完成基础接触，形成第一轮课堂学习节奏。",
    },
    {
        "key": "qi_refining",
        "name": "炼气小成",
        "short_name": "炼气",
        "tier": 2,
        "unlock_score": 42,
        "theme": "qi_refining",
        "certificate_title": "炼气小成道印",
        "address_title": "炼气道友",
        "aura_label": "灵力成环",
        "description": "能稳定阅读材料、完成任务，并开始主动提问。",
    },
    {
        "key": "foundation",
        "name": "筑基定境",
        "short_name": "筑基",
        "tier": 3,
        "unlock_score": 62,
        "theme": "foundation",
        "certificate_title": "筑基定境玉牒",
        "address_title": "筑基修士",
        "aura_label": "道基初稳",
        "description": "对课程核心知识有较完整的掌握和迁移能力。",
    },
    {
        "key": "golden_core",
        "name": "金丹凝元",
        "short_name": "金丹",
        "tier": 4,
        "unlock_score": 80,
        "theme": "golden_core",
        "certificate_title": "金丹凝元宝箓",
        "address_title": "金丹真人",
        "aura_label": "丹光流转",
        "description": "能综合运用课堂知识解决高阶问题。",
    },
    {
        "key": "nascent_soul",
        "name": "元婴问道",
        "short_name": "元婴",
        "tier": 5,
        "unlock_score": 92,
        "theme": "nascent_soul",
        "certificate_title": "元婴问道天书",
        "address_title": "元婴真君",
        "aura_label": "神识化形",
        "description": "能跨章节综合建模，解释、迁移并创造性应用课程知识。",
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
    target = get_stage_exam_target(conn, assignment_id)
    if not target:
        return True
    return int(target["student_id"]) == int(student_id)


def is_learning_stage_assignment(conn, assignment_id: int | str) -> bool:
    return get_stage_exam_target(conn, assignment_id) is not None


def _load_offering(conn, class_offering_id: int) -> Optional[dict[str, Any]]:
    row = conn.execute(
        """
        SELECT o.*,
               c.name AS course_name,
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
        GROUP BY m.id, m.name, m.material_path, m.preview_type
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


def _material_unit_ratio(progress: Optional[dict[str, Any]]) -> float:
    if not progress:
        return 0.0
    if safe_int(progress.get("completed")):
        return 1.0
    scroll_ratio = clamp(safe_float(progress.get("max_scroll_ratio")))
    active_ratio = clamp(safe_int(progress.get("active_seconds")) / MATERIAL_COMPLETE_ACTIVE_SECONDS)
    total_ratio = clamp(safe_int(progress.get("accumulated_seconds")) / MATERIAL_COMPLETE_TOTAL_SECONDS)
    return clamp(scroll_ratio * 0.58 + active_ratio * 0.28 + total_ratio * 0.14)


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
    }


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

    interaction_ratio = clamp(
        min(ai_question_count, 8) / 8 * 0.34
        + min(message_count, 12) / 12 * 0.24
        + min(mention_count, 4) / 4 * 0.22
        + min(private_teacher_count, 3) / 3 * 0.20
    )
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
        "private_teacher_count": private_teacher_count,
        "active_days": active_days,
        "online_seconds": online_seconds,
        "focus_seconds": focus_seconds,
        "activity_count": activity_count,
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
    return items


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


def _build_learning_metrics(conn, class_offering_id: int, student_id: int) -> dict[str, Any]:
    materials = _load_required_materials(conn, class_offering_id)
    progress_rows = _load_progress_rows(conn, class_offering_id, student_id)
    material_items: list[dict[str, Any]] = []
    material_ratios: list[float] = []
    for material in materials:
        progress = progress_rows.get(int(material["id"]))
        unit_ratio = _material_unit_ratio(progress)
        material_ratios.append(unit_ratio)
        material_items.append({
            **material,
            "progress": progress,
            "unit_ratio": round(unit_ratio, 4),
            "percent": int(round(unit_ratio * 100)),
            "completed": bool(progress and safe_int(progress.get("completed"))),
        })
    material_ratio = sum(material_ratios) / len(material_ratios) if material_ratios else 0.0
    completed_material_count = sum(1 for value in material_ratios if value >= 0.94)

    assignment_metrics = _load_assignment_metrics(conn, class_offering_id, student_id)
    interaction_metrics = _load_interaction_metrics(conn, class_offering_id, student_id)

    material_points = material_ratio * 45
    task_points = assignment_metrics["task_ratio"] * 35
    interaction_points = interaction_metrics["interaction_ratio"] * 15
    consistency_points = interaction_metrics["consistency_ratio"] * 5
    total_score = clamp((material_points + task_points + interaction_points + consistency_points) / 100, 0, 1) * 100

    return {
        "score": round(total_score, 1),
        "components": {
            "material": round(material_points, 1),
            "task": round(task_points, 1),
            "interaction": round(interaction_points, 1),
            "consistency": round(consistency_points, 1),
        },
        "material": {
            "required_count": len(materials),
            "completed_count": completed_material_count,
            "ratio": round(material_ratio, 4),
            "items": material_items[:12],
        },
        "assignments": assignment_metrics,
        "interactions": interaction_metrics,
    }


def refresh_student_learning_state(conn, class_offering_id: int, student_id: int) -> dict[str, Any]:
    metrics = _build_learning_metrics(conn, class_offering_id, student_id)
    certificates = _load_certificates(conn, class_offering_id, student_id)
    cert_by_stage = {normalize_level_key(item["stage_key"]): item for item in certificates}
    latest_attempts = _load_latest_attempts(conn, class_offering_id, student_id)
    timestamp = now_iso()
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
    next_stage = next((item for item in stages if item["status"] != "passed"), None)
    if next_stage:
        progress_percent = next_stage["progress_percent"]
    else:
        progress_percent = 100
    return {
        "score": score,
        "progress_percent": progress_percent,
        "current_level": highest_certificate or get_starter_level(),
        "next_stage": next_stage,
        "eligible_stage": eligible_stage,
        "stages": stages,
        "certificates": certificates,
        "latest_certificate": certificates[-1] if certificates else None,
        "metrics": metrics,
        "rules": {
            "score_weights": [
                {"label": "学习材料", "weight": 45},
                {"label": "作业考试", "weight": 35},
                {"label": "互动求助", "weight": 15},
                {"label": "稳定投入", "weight": 5},
            ],
            "pass_score": PASSING_STAGE_SCORE,
            "fairness_note": "修为由材料研读、任务通关、课堂互动和稳定投入共同凝聚；互动有上限，前期容易入门，后期逐步加大突破难度。",
        },
    }


def serialize_student_learning_progress(conn, class_offering_id: int, student_id: int) -> dict[str, Any]:
    state = refresh_student_learning_state(conn, int(class_offering_id), int(student_id))
    return state


def build_student_global_cultivation_profile(conn, student_id: int) -> dict[str, Any]:
    student = _load_student(conn, int(student_id)) or {"id": int(student_id), "name": ""}
    rows = conn.execute(
        """
        SELECT o.id AS class_offering_id,
               c.name AS course_name,
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
        )
        ORDER BY o.id DESC
        """,
        (int(student_id),),
    ).fetchall()

    course_items: list[dict[str, Any]] = []
    selected: dict[str, Any] | None = None
    for row in rows:
        offering = dict(row)
        progress = refresh_student_learning_state(conn, int(offering["class_offering_id"]), int(student_id))
        current_level = public_level_payload(progress.get("current_level"))
        next_stage = progress.get("next_stage")
        item = {
            "class_offering_id": int(offering["class_offering_id"]),
            "course_name": offering.get("course_name") or "课堂",
            "class_name": offering.get("class_name") or "",
            "teacher_name": offering.get("teacher_name") or "",
            "score": progress["score"],
            "progress_percent": progress["progress_percent"],
            "current_level": current_level,
            "next_stage": next_stage,
            "eligible_stage": progress.get("eligible_stage"),
            "certificate_count": len(progress.get("certificates") or []),
        }
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
            "course_name": "尚未加入课堂",
            "class_name": student.get("class_name") or "",
            "teacher_name": "",
            "score": 0,
            "progress_percent": 0,
            "current_level": current_level,
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
        "best_course": {
            "class_offering_id": selected.get("class_offering_id"),
            "course_name": selected.get("course_name") or "课堂",
            "class_name": selected.get("class_name") or "",
            "score": score,
            "progress_percent": progress_percent,
            "next_stage_name": next_name,
            "breakthrough_ready": breakthrough_ready,
            "generating_stage_exam": generating_stage_exam,
        },
        "courses": sorted_courses[:6],
        "next_stage_name": next_name,
        "progress_label": progress_label,
        "breakthrough_ready": breakthrough_ready,
        "generating_stage_exam": generating_stage_exam,
        "reveal_title": reveal_title,
        "reveal_subtitle": reveal_subtitle,
    }


def build_class_learning_overview(conn, class_offering_id: int) -> dict[str, Any]:
    offering = _load_offering(conn, int(class_offering_id))
    if not offering:
        return {"student_count": 0, "average_score": 0, "distribution": [], "students": []}
    rows = conn.execute(
        """
        SELECT id, name, student_id_number
        FROM students
        WHERE class_id = ?
        ORDER BY student_id_number, id
        """,
        (offering["class_id"],),
    ).fetchall()
    students: list[dict[str, Any]] = []
    distribution = {level["key"]: 0 for level in LEARNING_LEVELS}
    distribution["mortal"] = 0
    challenge_ready_count = 0
    certificate_count = 0
    score_total = 0.0
    for row in rows:
        student = dict(row)
        progress = refresh_student_learning_state(conn, int(class_offering_id), int(student["id"]))
        score_total += safe_float(progress.get("score"))
        current_key = normalize_level_key(progress["current_level"].get("level_key") or "mortal")
        distribution[current_key] = distribution.get(current_key, 0) + 1
        challenge_ready_count += 1 if progress.get("eligible_stage") else 0
        certificate_count += len(progress.get("certificates") or [])
        students.append({
            "id": int(student["id"]),
            "name": student["name"],
            "student_id_number": student.get("student_id_number"),
            "score": progress["score"],
            "progress_percent": progress["progress_percent"],
            "current_level": progress["current_level"],
            "next_stage": progress["next_stage"],
            "certificate_count": len(progress.get("certificates") or []),
            "eligible_stage": progress.get("eligible_stage"),
            "metrics": {
                "materials": progress["metrics"]["material"],
                "assignments": progress["metrics"]["assignments"],
                "interactions": progress["metrics"]["interactions"],
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
    students.sort(key=lambda item: (item["score"], item["certificate_count"]), reverse=True)
    return {
        "student_count": student_count,
        "average_score": round(score_total / student_count, 1) if student_count else 0,
        "challenge_ready_count": challenge_ready_count,
        "certificate_count": certificate_count,
        "distribution": distribution_items,
        "students": students[:12],
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
    conn.execute(
        """
        INSERT INTO learning_material_progress (
            class_offering_id, student_id, material_id, session_id,
            view_count, accumulated_seconds, active_seconds, max_scroll_ratio,
            completed, first_viewed_at, last_viewed_at, updated_at, metadata_json
        )
        VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(class_offering_id, student_id, material_id)
        DO UPDATE SET
            session_id = COALESCE(excluded.session_id, learning_material_progress.session_id),
            view_count = learning_material_progress.view_count + CASE
                WHEN learning_material_progress.last_viewed_at IS NULL THEN 1
                WHEN (julianday(excluded.last_viewed_at) - julianday(learning_material_progress.last_viewed_at)) * 86400 > 1800 THEN 1
                ELSE 0
            END,
            accumulated_seconds = learning_material_progress.accumulated_seconds + excluded.accumulated_seconds,
            active_seconds = learning_material_progress.active_seconds + excluded.active_seconds,
            max_scroll_ratio = MAX(learning_material_progress.max_scroll_ratio, excluded.max_scroll_ratio),
            completed = CASE WHEN learning_material_progress.completed = 1 OR excluded.completed = 1 THEN 1 ELSE 0 END,
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
            timestamp,
            timestamp,
            timestamp,
            json.dumps(metadata or {}, ensure_ascii=False),
        ),
    )
    state = refresh_student_learning_state(conn, int(class_offering_id), int(student_id))
    return {
        "status": "success",
        "completed": completed,
        "progress": {
            "score": state["score"],
            "progress_percent": state["progress_percent"],
            "eligible_stage": state.get("eligible_stage"),
        },
    }


def _normalize_exam_payload(payload: Any) -> dict[str, Any]:
    data = payload
    if isinstance(data, dict) and isinstance(data.get("exam_data"), dict):
        data = data["exam_data"]
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        data = data["data"]
    if not isinstance(data, dict):
        raise ValueError("AI 返回的试卷不是 JSON 对象")
    if isinstance(data.get("pages"), list):
        pages = data["pages"]
    elif isinstance(data.get("questions"), list):
        pages = [{"id": "stage-page-1", "title": "破境试炼", "questions": data["questions"]}]
    else:
        raise ValueError("AI 返回的试卷缺少 pages/questions")

    normalized_pages = []
    question_counter = 1
    for page_index, page in enumerate(pages, start=1):
        if not isinstance(page, dict):
            continue
        questions = []
        for question in page.get("questions") or []:
            if not isinstance(question, dict):
                continue
            item = dict(question)
            item["id"] = str(item.get("id") or f"Q{question_counter}")
            item["type"] = str(item.get("type") or "short_answer")
            item["text"] = str(item.get("text") or item.get("question") or item.get("title") or f"第{question_counter}题")
            questions.append(item)
            question_counter += 1
        if questions:
            normalized_pages.append({
                "id": str(page.get("id") or f"P{page_index}"),
                "title": str(page.get("title") or f"第{page_index}部分"),
                "questions": questions,
            })
    if not normalized_pages:
        raise ValueError("AI 返回的试卷没有可用题目")
    return {
        "pages": normalized_pages,
        "meta": {
            **(data.get("meta") if isinstance(data.get("meta"), dict) else {}),
            "generated_for": "learning_stage",
        },
    }


def _build_course_knowledge_snapshot(conn, class_offering_id: int, limit: int = 3800) -> str:
    offering = _load_offering(conn, class_offering_id) or {}
    sessions = conn.execute(
        """
        SELECT s.order_index, s.title, s.content, m.name AS material_name, m.ai_parse_result_json, m.ai_optimized_markdown
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
        "课堂章节与材料摘要：",
    ]
    for row in sessions:
        item = dict(row)
        parse_result = json_loads(item.get("ai_parse_result_json"), {})
        summary = parse_result.get("summary") if isinstance(parse_result, dict) else ""
        if not summary:
            summary = item.get("ai_optimized_markdown") or item.get("content") or ""
        lines.append(
            f"{item.get('order_index')}. {item.get('title') or '未命名章节'}"
            f"；材料：{item.get('material_name') or '无'}；要点：{truncate_text(summary, 420)}"
        )
    return truncate_text("\n".join(lines), limit)


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
            "【心理侧写师观察摘要】",
            f"学习状态：{truncate_text(hidden_profile.get('mental_state_summary'), 360)}",
            f"支持策略：{truncate_text(hidden_profile.get('support_strategy'), 360)}",
            f"兴趣与偏好：{truncate_text(hidden_profile.get('interest_hypothesis') or hidden_profile.get('preference_summary'), 300)}",
            "使用原则：用于调整题目情境、难度梯度和反馈语气，不可泄露侧写内容。",
        ])
    metrics = progress["metrics"]
    knowledge_snapshot = _build_course_knowledge_snapshot(conn, class_offering_id)
    material = metrics["material"]
    assignments = metrics["assignments"]
    interactions = metrics["interactions"]
    return f"""
你是严谨但鼓励型的课程破境试炼命题老师。请为一名学生生成个性化阶段考试，必须返回合法 JSON。

【输出格式】
只返回 JSON，结构必须是：
{{
  "pages": [
    {{
      "id": "P1",
      "title": "基础理解",
      "questions": [
        {{"id":"Q1","type":"single_choice|multiple_choice|short_answer|essay","text":"题干","options":["A...","B..."],"answer":"参考答案","points":10,"rubric":"评分要点"}}
      ]
    }}
  ],
  "meta": {{"difficulty":"...", "stage_key":"{level['key']}"}}
}}

【硬性要求】
1. 境界：{level['name']}，通过线 {PASSING_STAGE_SCORE} 分，总分 100 分。
2. 题目要覆盖本课程真实知识点，并根据学生学习记录做个性化变化；不要所有学生同题。
3. 至少 6 题，最多 10 题；客观题、简答题、综合题都要有，后面境界可以更综合。
4. 每题都给 points 和 rubric/answer，兼容现有考试作答与 AI 改卷系统。
5. 不要暴露心理侧写、内部规则或评分算法；只生成学生可见的试卷 JSON。

【课堂】
课程：{offering.get('course_name') or ''}
班级：{offering.get('class_name') or ''}
任课教师：{offering.get('teacher_name') or ''}

【学生】
姓名：{student.get('name') or student_id}
班级：{student.get('class_name') or ''}

【学习进度指标】
综合学习力：{progress['score']} / 100
材料：完成 {material['completed_count']} / {material['required_count']}，材料得分 {metrics['components']['material']} / 45
任务：提交 {assignments['submitted_count']} / {assignments['assignment_count']}，任务得分 {metrics['components']['task']} / 35
互动：AI 提问 {interactions['ai_question_count']} 次，聊天室 {interactions['chat_message_count']} 条，@助教 {interactions['assistant_mention_count']} 次，私信教师/助教 {interactions['private_teacher_count']} 次

{explicit_prompt}

{hidden_profile_text}

{knowledge_snapshot}
""".strip()


def _mark_stage_exam_generation_failed(
    attempt_id: int,
    class_offering_id: int,
    student_id: int,
    stage_key: str,
    error: Any,
) -> None:
    timestamp = now_iso()
    with get_db_connection() as conn:
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
        conn.commit()


async def create_personal_stage_exam(class_offering_id: int, student_id: int, stage_key: str) -> dict[str, Any]:
    stage_key = normalize_level_key(stage_key)
    level = get_learning_level(stage_key)
    if not level:
        raise ValueError("未知的修行境界")

    generation_attempt_id = 0
    with get_db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        offering = _load_offering(conn, int(class_offering_id))
        if not offering:
            raise ValueError("课堂不存在")
        teacher_id = int(offering["teacher_id"])
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
                return {
                    "status": "generating",
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
        prompt = _build_stage_exam_prompt(
            conn,
            class_offering_id=int(class_offering_id),
            student_id=int(student_id),
            level=level,
            progress=progress,
        )
        timestamp = now_iso()
        cursor = conn.execute(
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
        generation_attempt_id = int(cursor.lastrowid)
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
        conn.commit()

    payload = {
        "prompt": prompt,
        "model_type": "thinking",
        "task_type": "stage_exam_generation",
        "teacher_id": teacher_id,
        "class_offering_id": int(class_offering_id),
        "source_type": "learning_stage",
    }
    try:
        response = await ai_client.post("/api/ai/generate-exam", json=payload, timeout=300.0)
        response.raise_for_status()
        exam_payload = _normalize_exam_payload(response.json())
    except httpx.ConnectError as exc:
        _mark_stage_exam_generation_failed(generation_attempt_id, class_offering_id, student_id, stage_key, exc)
        raise ConnectionError("AI 助手服务未运行，请先启动 ai_assistant.py。") from exc
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:1000] if exc.response is not None else str(exc)
        _mark_stage_exam_generation_failed(generation_attempt_id, class_offering_id, student_id, stage_key, detail)
        raise RuntimeError(f"AI 出卷失败：{detail}") from exc
    except Exception as exc:
        _mark_stage_exam_generation_failed(generation_attempt_id, class_offering_id, student_id, stage_key, exc)
        raise

    with get_db_connection() as conn:
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
        cursor = conn.execute(
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
        assignment_id = int(cursor.lastrowid)
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
                json.dumps({"level_name": level["name"], "pass_score": PASSING_STAGE_SCORE}, ensure_ascii=False),
                generation_attempt_id,
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
        conn.commit()
        return {
            "status": "success",
            "assignment_id": assignment_id,
            "exam_url": f"/exam/take/{assignment_id}",
            "stage": level,
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
                       a.requirements_md,
                       a.rubric_md,
                       a.allowed_file_types_json,
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
            files = conn.execute(
                """
                SELECT stored_path, original_filename, relative_path, mime_type, file_size, file_ext, file_hash
                FROM submission_files
                WHERE submission_id = ?
                ORDER BY COALESCE(relative_path, original_filename), id
                """,
                (submission_id,),
            ).fetchall()
            conn.execute(
                "UPDATE submissions SET status = 'grading' WHERE id = ? AND COALESCE(resubmission_allowed, 0) = 0",
                (submission_id,),
            )
            conn.commit()

        resolved_files = []
        for row in files:
            item = dict(row)
            resolved = resolve_submission_file_path(str(item.get("stored_path") or ""))
            if not resolved:
                continue
            item["resolved_path"] = str(Path(resolved).resolve())
            resolved_files.append(item)
        has_answers = bool(submission["answers_json"])
        context_by_file = _extract_answer_attachment_context(submission["answers_json"] if has_answers else None)
        resolved_files = [_apply_attachment_context(item, context_by_file) for item in resolved_files]
        job_data = {
            "submission_id": int(submission_id),
            "rubric_md": submission["rubric_md"],
            "requirements_md": submission["requirements_md"] or "",
            "allowed_file_types_json": submission["allowed_file_types_json"],
            "files": [
                {
                    "stored_path": item["resolved_path"],
                    "original_filename": item.get("original_filename"),
                    "relative_path": item.get("relative_path") or item.get("original_filename"),
                    "mime_type": item.get("mime_type"),
                    "file_size": item.get("file_size"),
                    "file_ext": item.get("file_ext"),
                    "file_hash": item.get("file_hash"),
                }
                for item in resolved_files
            ],
            "file_paths": [item["resolved_path"] for item in resolved_files],
            "answers_json": submission["answers_json"] if has_answers else None,
        }
        response = await ai_client.post("/api/ai/submit-grading-job", json=job_data, timeout=60.0)
        response.raise_for_status()
    except Exception as exc:
        with get_db_connection() as conn:
            conn.execute(
                """
                UPDATE submissions
                SET status = 'submitted'
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


def handle_stage_exam_grading_complete(conn, submission_id: int | str) -> Optional[dict[str, Any]]:
    row = conn.execute(
        """
        SELECT lsea.*,
               s.score AS submission_score,
               s.status AS submission_status,
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
        conn.execute(
            """
            UPDATE learning_stage_exam_attempts
            SET status = 'failed',
                score = ?,
                graded_at = ?
            WHERE id = ?
            """,
            (score, timestamp, int(attempt["id"])),
        )
        conn.execute(
            """
            UPDATE learning_stage_status
            SET status = 'challenge_ready',
                progress_score = MAX(progress_score, ?),
                last_calculated_at = ?
            WHERE class_offering_id = ? AND student_id = ? AND stage_key = ?
            """,
            (score, timestamp, attempt["class_offering_id"], attempt["student_id"], attempt["stage_key"]),
        )
        return {"status": "failed", "stage": level, "score": score}

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
        cursor = conn.execute(
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
            "id": int(cursor.lastrowid),
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
            progress_score = MAX(progress_score, ?),
            readiness_score = 100,
            passed_at = ?,
            certificate_id = ?,
            last_exam_assignment_id = ?,
            last_calculated_at = ?
        WHERE class_offering_id = ? AND student_id = ? AND stage_key = ?
        """,
        (
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
    create_learning_progress_notification(
        conn,
        recipient_role="teacher",
        recipient_user_pk=int(attempt["teacher_id"]),
        title=f"{attempt.get('student_name') or '学生'} 晋级 {level['name']}",
        body_preview=f"{attempt.get('student_name') or '学生'} 通过 {attempt.get('course_name') or '课堂'} 破境试炼，得分 {score:g}。",
        link_url=classroom_link,
        class_offering_id=int(attempt["class_offering_id"]),
        ref_id=f"certificate:{certificate['id']}:teacher",
        actor_role="student",
        actor_user_pk=int(attempt["student_id"]),
        actor_display_name=str(attempt.get("student_name") or ""),
        metadata={"certificate_id": certificate["id"], "stage_key": level["key"], "score": score},
    )
    return {"status": "passed", "stage": level, "score": score, "certificate": certificate}
