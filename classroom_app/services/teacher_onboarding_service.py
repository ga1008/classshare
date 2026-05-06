from __future__ import annotations

import sqlite3
from typing import Any


def _count(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> int:
    row = conn.execute(sql, params).fetchone()
    return int((row[0] if row else 0) or 0)


def build_teacher_onboarding_progress(conn: sqlite3.Connection, teacher_id: int) -> dict:
    counts = {
        "classes": _count(
            conn,
            "SELECT COUNT(*) FROM classes WHERE created_by_teacher_id = ?",
            (teacher_id,),
        ),
        "courses": _count(
            conn,
            "SELECT COUNT(*) FROM courses WHERE created_by_teacher_id = ?",
            (teacher_id,),
        ),
        "semesters": _count(
            conn,
            "SELECT COUNT(*) FROM academic_semesters WHERE teacher_id = ?",
            (teacher_id,),
        ),
        "offerings": _count(
            conn,
            "SELECT COUNT(*) FROM class_offerings WHERE teacher_id = ?",
            (teacher_id,),
        ),
        "ai_configs": _count(
            conn,
            """
            SELECT COUNT(*)
            FROM ai_class_configs cfg
            JOIN class_offerings o ON o.id = cfg.class_offering_id
            WHERE o.teacher_id = ?
              AND TRIM(COALESCE(cfg.system_prompt, '')) != ''
              AND TRIM(COALESCE(cfg.syllabus, '')) != ''
            """,
            (teacher_id,),
        ),
    }

    step_defs = [
        {
            "id": "classes",
            "title": "创建自己的班级",
            "description": "先建立授课班级，后续导入学生、布置作业和课堂互动都会依赖它。",
            "href": "/manage/classes",
            "action_label": "去创建班级",
            "count_key": "classes",
        },
        {
            "id": "courses",
            "title": "准备课程与基础资料",
            "description": "课程模板承载课时、章节和教学目标。后续开设课堂时会把它绑定到具体班级。",
            "href": "/manage/courses",
            "action_label": "去准备课程",
            "count_key": "courses",
        },
        {
            "id": "semesters",
            "title": "确认本学期",
            "description": "学期决定课堂的起止日期、周次和教学日历，是开设课堂前的时间基准。",
            "href": "/manage/semesters",
            "action_label": "去创建学期",
            "count_key": "semesters",
        },
        {
            "id": "offerings",
            "title": "开设第一门课堂",
            "description": "把班级、课程和学期组合成真实课堂，学生登录后会从这里进入学习空间。",
            "href": "/manage/offerings",
            "action_label": "去开设课堂",
            "count_key": "offerings",
        },
        {
            "id": "ai",
            "title": "配置课堂 AI 助教",
            "description": "为课堂绑定系统提示词、课程大纲和教材依据，让 AI 助教能围绕这门课回答。",
            "href": "/manage/ai",
            "action_label": "去配置 AI 助教",
            "count_key": "ai_configs",
        },
    ]

    steps = []
    for index, item in enumerate(step_defs):
        count = counts[item["count_key"]]
        ready = count > 0
        steps.append(
            {
                "index": index,
                **item,
                "count": count,
                "ready": ready,
                "status_label": "已完成" if ready else "待完成",
            }
        )

    completed_count = sum(1 for step in steps if step["ready"])
    next_step = next((step for step in steps if not step["ready"]), steps[0])

    return {
        "counts": counts,
        "steps": steps,
        "completed_count": completed_count,
        "total_count": len(steps),
        "all_core_ready": completed_count == len(steps),
        "next_step_id": next_step["id"],
    }


def build_teacher_onboarding_payload(conn: sqlite3.Connection, teacher_id: int) -> dict:
    state_row = conn.execute(
        """
        SELECT teacher_id, dismissed_at, completed_at, dismiss_reason, updated_at
        FROM teacher_onboarding_state
        WHERE teacher_id = ?
        LIMIT 1
        """,
        (teacher_id,),
    ).fetchone()
    state = dict(state_row) if state_row else {
        "teacher_id": teacher_id,
        "dismissed_at": None,
        "completed_at": None,
        "dismiss_reason": "",
        "updated_at": None,
    }
    progress = build_teacher_onboarding_progress(conn, teacher_id)
    has_dismissed = bool(state.get("dismissed_at") or state.get("completed_at"))

    return {
        "status": "success",
        "state": state,
        "progress": progress,
        "should_auto_open": (not has_dismissed) and (not progress["all_core_ready"]),
    }


def mark_teacher_onboarding_dismissed(
    conn: sqlite3.Connection,
    teacher_id: int,
    reason: str,
) -> None:
    normalized_reason = str(reason or "").strip().lower()
    if normalized_reason not in {"completed", "manual_exit", "used"}:
        normalized_reason = "manual_exit"

    if normalized_reason == "completed":
        conn.execute(
            """
            INSERT INTO teacher_onboarding_state (
                teacher_id, dismissed_at, completed_at, dismiss_reason, updated_at
            )
            VALUES (?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(teacher_id) DO UPDATE SET
                dismissed_at = CURRENT_TIMESTAMP,
                completed_at = CURRENT_TIMESTAMP,
                dismiss_reason = excluded.dismiss_reason,
                updated_at = CURRENT_TIMESTAMP
            """,
            (teacher_id, normalized_reason),
        )
        return

    conn.execute(
        """
        INSERT INTO teacher_onboarding_state (
            teacher_id, dismissed_at, completed_at, dismiss_reason, updated_at
        )
        VALUES (?, CURRENT_TIMESTAMP, NULL, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(teacher_id) DO UPDATE SET
            dismissed_at = CURRENT_TIMESTAMP,
            dismiss_reason = excluded.dismiss_reason,
            updated_at = CURRENT_TIMESTAMP
        """,
        (teacher_id, normalized_reason),
    )
