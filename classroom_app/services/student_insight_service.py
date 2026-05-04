from __future__ import annotations

import math
from typing import Any

from .learning_progress_service import (
    build_student_public_cultivation_badge,
    clamp,
    normalize_course_sect_name,
    personal_stage_assignment_filter_sql,
    public_level_payload,
    refresh_student_learning_state,
    safe_float,
    safe_int,
)


RADAR_SIZE = 168
RADAR_CENTER = RADAR_SIZE / 2
RADAR_RADIUS = 66


def _percent(value: float) -> int:
    return int(round(clamp(float(value or 0)) * 100))


def _score_percent(value: Any) -> int:
    return int(round(clamp(safe_float(value) / 100) * 100))


def _task_status_label(row: dict[str, Any]) -> str:
    if row.get("submission_id"):
        if str(row.get("submission_status") or "") == "graded" or row.get("score") is not None:
            return "已批改"
        return "已提交"
    if str(row.get("status") or "") == "closed":
        return "已截止"
    return "待完成"


def _radar_points(axes: list[dict[str, Any]]) -> str:
    points: list[str] = []
    count = max(len(axes), 1)
    for index, axis in enumerate(axes):
        ratio = clamp(safe_float(axis.get("score")) / 100)
        angle = -math.pi / 2 + index * 2 * math.pi / count
        x = RADAR_CENTER + math.cos(angle) * RADAR_RADIUS * ratio
        y = RADAR_CENTER + math.sin(angle) * RADAR_RADIUS * ratio
        points.append(f"{x:.1f},{y:.1f}")
    return " ".join(points)


def _radar_grid_points(ratio: float, count: int = 6) -> str:
    points: list[str] = []
    for index in range(count):
        angle = -math.pi / 2 + index * 2 * math.pi / count
        x = RADAR_CENTER + math.cos(angle) * RADAR_RADIUS * ratio
        y = RADAR_CENTER + math.sin(angle) * RADAR_RADIUS * ratio
        points.append(f"{x:.1f},{y:.1f}")
    return " ".join(points)


def _load_teacher_student_row(conn, *, teacher_id: int, student_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT s.id,
               s.student_id_number,
               s.name,
               s.nickname,
               s.gender,
               s.email,
               s.phone,
               s.homepage_url,
               s.class_id,
               s.created_at,
               c.name AS class_name
        FROM students s
        JOIN classes c ON c.id = s.class_id
        WHERE s.id = ?
          AND (
              c.created_by_teacher_id = ?
              OR EXISTS (
                  SELECT 1
                  FROM class_offerings o
                  WHERE o.class_id = s.class_id
                    AND o.teacher_id = ?
              )
          )
        LIMIT 1
        """,
        (int(student_id), int(teacher_id), int(teacher_id)),
    ).fetchone()
    return dict(row) if row else None


def _load_teacher_student_offerings(conn, *, teacher_id: int, class_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT o.id AS class_offering_id,
               o.class_id,
               o.course_id,
               o.first_class_date,
               COALESCE(s.name, o.semester) AS semester_name,
               c.name AS course_name,
               c.sect_name AS course_sect_name,
               cl.name AS class_name,
               t.name AS teacher_name
        FROM class_offerings o
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        JOIN teachers t ON t.id = o.teacher_id
        LEFT JOIN academic_semesters s ON s.id = o.semester_id
        WHERE o.class_id = ?
          AND o.teacher_id = ?
        ORDER BY COALESCE(s.start_date, o.first_class_date, o.created_at) DESC, c.name
        """,
        (int(class_id), int(teacher_id)),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_student_task_rows(
    conn,
    *,
    teacher_id: int,
    class_id: int,
    student_id: int,
) -> list[dict[str, Any]]:
    personal_filter = personal_stage_assignment_filter_sql("a")
    rows = conn.execute(
        f"""
        SELECT a.id,
               a.title,
               a.status,
               a.exam_paper_id,
               a.starts_at,
               a.due_at,
               a.created_at,
               o.id AS class_offering_id,
               c.name AS course_name,
               c.sect_name AS course_sect_name,
               s.id AS submission_id,
               s.status AS submission_status,
               s.score,
               s.submitted_at
        FROM assignments a
        JOIN class_offerings o ON o.id = a.class_offering_id
        JOIN courses c ON c.id = o.course_id
        LEFT JOIN submissions s
               ON s.assignment_id = a.id
              AND s.student_pk_id = ?
              AND COALESCE(s.is_absence_score, 0) = 0
        WHERE o.class_id = ?
          AND o.teacher_id = ?
          AND a.status != 'new'
          AND {personal_filter}
        ORDER BY COALESCE(a.due_at, a.created_at) DESC, a.id DESC
        """,
        (int(student_id), int(class_id), int(teacher_id)),
    ).fetchall()

    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        sect_name = normalize_course_sect_name(item.get("course_sect_name"), course_name=item.get("course_name"))
        item.update(
            {
                "sect_name": sect_name,
                "type_label": "考试" if item.get("exam_paper_id") else "练习",
                "status_label": _task_status_label(item),
                "is_completed": bool(item.get("submission_id")),
                "score_percent": _score_percent(item.get("score")),
            }
        )
        items.append(item)
    return items


def _load_activity_summary(
    conn,
    *,
    offering_ids: list[int],
    student_id: int,
) -> dict[str, Any]:
    if not offering_ids:
        return {
            "activity_count": 0,
            "event_count": 0,
            "active_days": 0,
            "chat_message_count": 0,
            "ai_question_count": 0,
            "online_minutes": 0,
            "focus_minutes": 0,
        }

    placeholders = ",".join("?" for _ in offering_ids)
    state_row = conn.execute(
        f"""
        SELECT SUM(total_activity_count) AS activity_count,
               SUM(online_accumulated_seconds) AS online_seconds,
               SUM(focus_total_seconds) AS focus_seconds,
               SUM(visible_total_seconds) AS visible_seconds
        FROM classroom_behavior_states
        WHERE class_offering_id IN ({placeholders})
          AND user_pk = ?
          AND user_role = 'student'
        """,
        [*offering_ids, int(student_id)],
    ).fetchone()
    event_row = conn.execute(
        f"""
        SELECT COUNT(*) AS event_count,
               COUNT(DISTINCT substr(created_at, 1, 10)) AS active_days,
               SUM(CASE WHEN action_type = 'ai_question' THEN 1 ELSE 0 END) AS ai_question_count
        FROM classroom_behavior_events
        WHERE class_offering_id IN ({placeholders})
          AND user_pk = ?
          AND user_role = 'student'
        """,
        [*offering_ids, int(student_id)],
    ).fetchone()
    chat_row = conn.execute(
        f"""
        SELECT COUNT(*) AS chat_message_count
        FROM chat_logs
        WHERE class_offering_id IN ({placeholders})
          AND user_role = 'student'
          AND user_id = ?
        """,
        [*offering_ids, str(student_id)],
    ).fetchone()
    return {
        "activity_count": safe_int(state_row["activity_count"] if state_row else 0),
        "event_count": safe_int(event_row["event_count"] if event_row else 0),
        "active_days": safe_int(event_row["active_days"] if event_row else 0),
        "chat_message_count": safe_int(chat_row["chat_message_count"] if chat_row else 0),
        "ai_question_count": safe_int(event_row["ai_question_count"] if event_row else 0),
        "online_minutes": round(safe_int(state_row["online_seconds"] if state_row else 0) / 60),
        "focus_minutes": round(safe_int(state_row["focus_seconds"] if state_row else 0) / 60),
    }


def _build_course_progress(
    conn,
    *,
    offerings: list[dict[str, Any]],
    student_id: int,
) -> list[dict[str, Any]]:
    courses: list[dict[str, Any]] = []
    for offering in offerings:
        state = refresh_student_learning_state(conn, int(offering["class_offering_id"]), int(student_id))
        level = public_level_payload(state.get("current_level"))
        metrics = state.get("metrics") or {}
        assignments = metrics.get("assignments") or {}
        material = metrics.get("material") or {}
        interactions = metrics.get("interactions") or {}
        sect_name = normalize_course_sect_name(
            offering.get("course_sect_name"),
            course_name=offering.get("course_name"),
        )
        courses.append(
            {
                "class_offering_id": int(offering["class_offering_id"]),
                "course_id": int(offering["course_id"]),
                "course_name": offering.get("course_name") or "课堂",
                "sect_name": sect_name,
                "sect_level_label": f"{sect_name} · {level['short_name']}",
                "class_name": offering.get("class_name") or "",
                "teacher_name": offering.get("teacher_name") or "",
                "semester_name": offering.get("semester_name") or "",
                "score": round(safe_float(state.get("score")), 1),
                "progress_percent": safe_int(state.get("progress_percent")),
                "current_level": level,
                "eligible_stage": state.get("eligible_stage"),
                "next_stage": state.get("next_stage"),
                "certificate_count": len(state.get("certificates") or []),
                "material_completed_count": safe_int(material.get("completed_count")),
                "material_required_count": safe_int(material.get("required_count")),
                "assignment_count": safe_int(assignments.get("assignment_count")),
                "submitted_count": safe_int(assignments.get("submitted_count")),
                "graded_count": safe_int(assignments.get("graded_count")),
                "task_completion_percent": _percent(assignments.get("completion_ratio", 0)),
                "interaction_percent": _percent(interactions.get("interaction_ratio", 0)),
                "consistency_percent": _percent(interactions.get("consistency_ratio", 0)),
                "metrics": {
                    "material": material,
                    "assignments": assignments,
                    "interactions": interactions,
                },
            }
        )
    courses.sort(
        key=lambda item: (
            safe_int(item["current_level"].get("tier")),
            safe_float(item.get("score")),
        ),
        reverse=True,
    )
    return courses


def _build_task_summary(task_rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(task_rows)
    completed = [item for item in task_rows if item.get("is_completed")]
    unfinished = [item for item in task_rows if not item.get("is_completed")]
    graded_scores = [safe_float(item.get("score")) for item in completed if item.get("score") is not None]
    average_score = round(sum(graded_scores) / len(graded_scores), 1) if graded_scores else None
    completion_percent = int(round(len(completed) / total * 100)) if total else 0
    return {
        "total": total,
        "completed": len(completed),
        "unfinished": len(unfinished),
        "graded": len(graded_scores),
        "average_score": average_score,
        "completion_percent": completion_percent,
        "completed_items": completed[:8],
        "unfinished_items": unfinished[:8],
    }


def _build_radar_axes(
    *,
    courses: list[dict[str, Any]],
    task_summary: dict[str, Any],
    activity_summary: dict[str, Any],
) -> dict[str, Any]:
    if courses:
        material_score = sum(_percent((item["metrics"].get("material") or {}).get("ratio", 0)) for item in courses) / len(courses)
        task_score = sum(safe_float(item.get("task_completion_percent")) for item in courses) / len(courses)
        interaction_score = sum(safe_float(item.get("interaction_percent")) for item in courses) / len(courses)
        consistency_score = sum(safe_float(item.get("consistency_percent")) for item in courses) / len(courses)
        cultivation_score = sum(safe_float(item.get("score")) for item in courses) / len(courses)
    else:
        material_score = task_score = interaction_score = consistency_score = cultivation_score = 0
    quality_score = safe_float(task_summary.get("average_score")) if task_summary.get("average_score") is not None else task_score
    activity_boost = min(safe_int(activity_summary.get("active_days")), 8) / 8 * 18
    consistency_score = clamp((consistency_score + activity_boost) / 100) * 100
    axes = [
        {"label": "材料吸收", "score": round(material_score)},
        {"label": "任务执行", "score": round(task_score)},
        {"label": "成绩质量", "score": round(quality_score)},
        {"label": "互动表达", "score": round(interaction_score)},
        {"label": "学习稳定", "score": round(consistency_score)},
        {"label": "修为推进", "score": round(cultivation_score)},
    ]
    return {
        "axes": axes,
        "points": _radar_points(axes),
        "grid_points": [_radar_grid_points(0.33), _radar_grid_points(0.66), _radar_grid_points(1.0)],
    }


def build_teacher_student_insight(conn, teacher_id: int, student_id: int) -> dict[str, Any] | None:
    student = _load_teacher_student_row(conn, teacher_id=int(teacher_id), student_id=int(student_id))
    if not student:
        return None

    offerings = _load_teacher_student_offerings(
        conn,
        teacher_id=int(teacher_id),
        class_id=int(student["class_id"]),
    )
    courses = _build_course_progress(conn, offerings=offerings, student_id=int(student_id))
    task_rows = _load_student_task_rows(
        conn,
        teacher_id=int(teacher_id),
        class_id=int(student["class_id"]),
        student_id=int(student_id),
    )
    task_summary = _build_task_summary(task_rows)
    activity_summary = _load_activity_summary(
        conn,
        offering_ids=[int(item["class_offering_id"]) for item in offerings],
        student_id=int(student_id),
    )
    radar = _build_radar_axes(
        courses=courses,
        task_summary=task_summary,
        activity_summary=activity_summary,
    )
    public_badge = build_student_public_cultivation_badge(conn, int(student_id))
    best_course = courses[0] if courses else None
    average_cultivation_score = (
        round(sum(safe_float(item.get("score")) for item in courses) / len(courses), 1)
        if courses
        else 0
    )

    return {
        "student": {
            **student,
            "avatar_url": f"/api/profile/avatar?role=student&user_id={int(student_id)}",
            "display_name": student.get("nickname") or student.get("name") or "学生",
        },
        "public_badge": public_badge,
        "best_course": best_course,
        "courses": courses,
        "task_summary": task_summary,
        "activity_summary": activity_summary,
        "radar": radar,
        "hero_stats": [
            {"label": "参与课堂", "value": len(courses), "suffix": "门", "note": "当前教师名下课堂"},
            {"label": "任务完成", "value": task_summary["completion_percent"], "suffix": "%", "note": f"{task_summary['completed']} / {task_summary['total']}"},
            {"label": "平均修为", "value": average_cultivation_score, "suffix": "", "note": "按课程修为均值"},
            {
                "label": "活跃天数",
                "value": safe_int(activity_summary.get("active_days")),
                "suffix": "天",
                "note": f"互动 {safe_int(activity_summary.get('event_count'))} 次",
            },
        ],
    }
