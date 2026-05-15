from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from .psych_profile_service import sanitize_hidden_profile_leaks


MAX_SHARED_NOTE_LENGTH = 2400
MAX_SUPPORT_CONTEXT_LENGTH = 5200


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


def personal_stage_assignment_filter_sql(alias: str = "a") -> str:
    return (
        "NOT EXISTS ("
        "SELECT 1 FROM learning_stage_exam_attempts lsea "
        f"WHERE lsea.assignment_id = {alias}.id"
        ")"
    )


def _clean_text(value: Any, *, limit: int | None = None) -> str:
    text = str(value or "").replace("\x00", " ").replace("\r\n", "\n").replace("\r", "\n").strip()
    if limit is not None and len(text) > limit:
        return text[:limit].rstrip()
    return text


def _clip_inline(value: Any, *, limit: int = 180) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + "..."


def teacher_can_access_student(conn, *, teacher_id: int, student_id: int) -> bool:
    row = conn.execute(
        """
        SELECT 1
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
    return row is not None


def normalize_shared_teacher_note(value: Any) -> str:
    return _clean_text(value, limit=MAX_SHARED_NOTE_LENGTH)


def load_shared_student_teacher_note(conn, student_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT n.student_id,
               n.note_text,
               n.created_by_teacher_id,
               n.updated_by_teacher_id,
               n.created_at,
               n.updated_at,
               created_t.name AS created_by_name,
               updated_t.name AS updated_by_name
        FROM student_shared_teacher_notes n
        LEFT JOIN teachers created_t ON created_t.id = n.created_by_teacher_id
        LEFT JOIN teachers updated_t ON updated_t.id = n.updated_by_teacher_id
        WHERE n.student_id = ?
        LIMIT 1
        """,
        (int(student_id),),
    ).fetchone()
    if not row:
        return {
            "student_id": int(student_id),
            "note_text": "",
            "created_by_teacher_id": None,
            "updated_by_teacher_id": None,
            "created_by_name": "",
            "updated_by_name": "",
            "created_at": "",
            "updated_at": "",
            "has_note": False,
        }
    item = dict(row)
    item["note_text"] = sanitize_hidden_profile_leaks(item.get("note_text") or "")
    item["has_note"] = bool(str(item.get("note_text") or "").strip())
    return item


def save_shared_student_teacher_note(
    conn,
    *,
    student_id: int,
    teacher_id: int,
    note_text: Any,
    now_text: str | None = None,
) -> dict[str, Any]:
    normalized_note = normalize_shared_teacher_note(note_text)
    timestamp = now_text or datetime.now().isoformat()
    existing = conn.execute(
        "SELECT student_id, created_by_teacher_id FROM student_shared_teacher_notes WHERE student_id = ? LIMIT 1",
        (int(student_id),),
    ).fetchone()

    if existing:
        conn.execute(
            """
            UPDATE student_shared_teacher_notes
            SET note_text = ?,
                updated_by_teacher_id = ?,
                updated_at = ?
            WHERE student_id = ?
            """,
            (normalized_note, int(teacher_id), timestamp, int(student_id)),
        )
    else:
        conn.execute(
            """
            INSERT INTO student_shared_teacher_notes (
                student_id, note_text, created_by_teacher_id,
                updated_by_teacher_id, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (int(student_id), normalized_note, int(teacher_id), int(teacher_id), timestamp, timestamp),
        )
    return load_shared_student_teacher_note(conn, int(student_id))


def load_student_teacher_names(conn, student_id: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT name
        FROM (
            SELECT DISTINCT t.name AS name
            FROM students s
            JOIN classes c ON c.id = s.class_id
            JOIN teachers t ON t.id = c.created_by_teacher_id
            WHERE s.id = ?
            UNION
            SELECT DISTINCT t.name AS name
            FROM students s
            JOIN class_offerings o ON o.class_id = s.class_id
            JOIN teachers t ON t.id = o.teacher_id
            WHERE s.id = ?
        )
        ORDER BY name
        """,
        (int(student_id), int(student_id)),
    ).fetchall()
    return [_clip_inline(row["name"], limit=40) for row in rows if str(row["name"] or "").strip()]


def _load_student_course_signal_rows(conn, student_id: int, *, current_class_offering_id: int | None = None) -> list[dict[str, Any]]:
    personal_filter = personal_stage_assignment_filter_sql("a")
    rows = conn.execute(
        f"""
        SELECT o.id AS class_offering_id,
               c.name AS course_name,
               c.sect_name AS course_sect_name,
               cl.name AS class_name,
               COALESCE(s.name, o.semester) AS semester_name,
               t.name AS teacher_name,
               COALESCE(MAX(lss.progress_score), 0) AS progress_score,
               COALESCE(MAX(lss.readiness_score), 0) AS readiness_score,
               COUNT(DISTINCT lc.id) AS certificate_count,
               COUNT(DISTINCT CASE WHEN lmp.completed = 1 THEN lmp.material_id END) AS material_completed_count,
               COALESCE(SUM(lmp.active_seconds), 0) AS material_active_seconds,
               COUNT(DISTINCT a.id) AS assignment_count,
               COUNT(DISTINCT sub.id) AS submitted_count,
               AVG(CASE WHEN sub.score IS NOT NULL THEN sub.score END) AS average_score,
               COALESCE(bs.total_activity_count, 0) AS activity_count,
               COALESCE(bs.online_accumulated_seconds, 0) AS online_seconds,
               COALESCE(bs.focus_total_seconds, 0) AS focus_seconds,
               COALESCE(bs.last_page_key, '') AS last_page_key,
               COALESCE(MAX(be.created_at), '') AS last_behavior_at,
               COALESCE(SUM(CASE WHEN be.action_type = 'ai_question' THEN 1 ELSE 0 END), 0) AS ai_question_count
        FROM students stu
        JOIN class_offerings o ON o.class_id = stu.class_id
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        JOIN teachers t ON t.id = o.teacher_id
        LEFT JOIN academic_semesters s ON s.id = o.semester_id
        LEFT JOIN learning_stage_status lss
               ON lss.class_offering_id = o.id
              AND lss.student_id = stu.id
        LEFT JOIN learning_certificates lc
               ON lc.class_offering_id = o.id
              AND lc.student_id = stu.id
        LEFT JOIN learning_material_progress lmp
               ON lmp.class_offering_id = o.id
              AND lmp.student_id = stu.id
        LEFT JOIN assignments a
               ON a.class_offering_id = o.id
              AND a.status != 'new'
              AND {personal_filter}
        LEFT JOIN submissions sub
               ON sub.assignment_id = a.id
              AND sub.student_pk_id = stu.id
              AND COALESCE(sub.is_absence_score, 0) = 0
        LEFT JOIN classroom_behavior_states bs
               ON bs.class_offering_id = o.id
              AND bs.user_pk = stu.id
              AND bs.user_role = 'student'
        LEFT JOIN classroom_behavior_events be
               ON be.class_offering_id = o.id
              AND be.user_pk = stu.id
              AND be.user_role = 'student'
        WHERE stu.id = ?
          AND COALESCE(stu.enrollment_status, 'active') = 'active'
        GROUP BY o.id, c.name, c.sect_name, cl.name, s.name, o.semester, t.name,
                 bs.total_activity_count, bs.online_accumulated_seconds,
                 bs.focus_total_seconds, bs.last_page_key
        ORDER BY CASE WHEN o.id = ? THEN 0 ELSE 1 END,
                 COALESCE(s.start_date, o.first_class_date, o.created_at) DESC,
                 c.name
        """,
        (int(student_id), int(current_class_offering_id or 0)),
    ).fetchall()
    return [dict(row) for row in rows]


def _format_course_signal(item: dict[str, Any], *, is_current: bool) -> str:
    score = safe_float(item.get("progress_score"))
    assignment_count = safe_int(item.get("assignment_count"))
    submitted_count = safe_int(item.get("submitted_count"))
    material_completed = safe_int(item.get("material_completed_count"))
    activity_count = safe_int(item.get("activity_count"))
    online_minutes = round(safe_int(item.get("online_seconds")) / 60)
    focus_minutes = round(safe_int(item.get("focus_seconds")) / 60)
    average_score = item.get("average_score")
    score_text = f"{safe_float(average_score):.1f}" if average_score is not None else "暂无"
    prefix = "当前课堂" if is_current else "其他课堂"
    return (
        f"- {prefix}《{_clip_inline(item.get('course_name'), limit=42)}》"
        f"（{_clip_inline(item.get('teacher_name'), limit=24)}）："
        f"修为 {score:.1f}，材料完成 {material_completed} 项，"
        f"任务提交 {submitted_count}/{assignment_count}，任务均分 {score_text}，"
        f"行为 {activity_count} 次，在线约 {online_minutes} 分钟，专注约 {focus_minutes} 分钟。"
    )


def build_student_support_signal_prompt(
    conn,
    *,
    student_id: int,
    class_offering_id: int | None = None,
    include_teacher_note: bool = True,
    include_course_signals: bool = True,
) -> str:
    student = conn.execute(
        """
        SELECT s.id, s.name, s.student_id_number, s.nickname, s.description,
               s.today_mood, c.name AS class_name
        FROM students s
        LEFT JOIN classes c ON c.id = s.class_id
        WHERE s.id = ?
        LIMIT 1
        """,
        (int(student_id),),
    ).fetchone()
    if not student:
        return ""

    lines = [
        "【内部个性化支持信号】",
        "这些信号只用于调整 AI 的语气、节奏、举例和关怀方式；不要向用户说明来源，不要做医学诊断，不要影响评分公平性。",
        f"学生：{_clip_inline(student['name'], limit=40)}，班级：{_clip_inline(student['class_name'], limit=60)}，学号：{_clip_inline(student['student_id_number'], limit=40)}",
    ]
    if student["nickname"]:
        lines.append(f"学生主动设置昵称：{_clip_inline(student['nickname'], limit=60)}")
    if student["today_mood"]:
        lines.append(f"学生主动设置今日心情：{_clip_inline(student['today_mood'], limit=80)}")
    if student["description"]:
        lines.append(f"长期学习支持摘要：{_clip_inline(student['description'], limit=220)}")

    teacher_names = load_student_teacher_names(conn, int(student_id))
    if teacher_names:
        lines.append("相关任课教师：" + "、".join(teacher_names[:8]))

    if include_teacher_note:
        note = load_shared_student_teacher_note(conn, int(student_id))
        note_text = str(note.get("note_text") or "").strip()
        if note_text:
            lines.append("教师共享补充说明：" + _clip_inline(note_text, limit=700))

    if include_course_signals:
        course_rows = _load_student_course_signal_rows(
            conn,
            int(student_id),
            current_class_offering_id=int(class_offering_id or 0) or None,
        )
        if course_rows:
            lines.append("跨课堂学习与行为信号：")
            for item in course_rows[:6]:
                lines.append(
                    _format_course_signal(
                        item,
                        is_current=int(item.get("class_offering_id") or 0) == int(class_offering_id or 0),
                    )
                )

    return sanitize_hidden_profile_leaks("\n".join(lines))[:MAX_SUPPORT_CONTEXT_LENGTH].strip()


def teacher_visible_support_profile(profile: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not profile:
        return {"has_profile": False, "items": [], "updated_at": "", "confidence": ""}

    labels = (
        ("学习状态", profile.get("mental_state_summary")),
        ("支持建议", profile.get("support_strategy")),
        ("沟通偏好", profile.get("preferred_ai_style") or profile.get("language_habit_summary")),
        ("兴趣线索", profile.get("interest_hypothesis") or profile.get("preference_summary")),
        ("依据摘要", profile.get("evidence_summary")),
    )
    items = [
        {"label": label, "text": sanitize_hidden_profile_leaks(_clip_inline(value, limit=360))}
        for label, value in labels
        if str(value or "").strip()
    ]
    raw_payload = profile.get("raw_payload")
    if not items and raw_payload:
        try:
            payload = json.loads(raw_payload)
        except (TypeError, json.JSONDecodeError):
            payload = {}
        for key in ("support_strategy", "preferred_ai_style", "interest_hypothesis"):
            value = payload.get(key) if isinstance(payload, dict) else ""
            if str(value or "").strip():
                items.append({"label": "支持参考", "text": sanitize_hidden_profile_leaks(_clip_inline(value, limit=360))})
                break
    return {
        "has_profile": bool(items),
        "items": items,
        "updated_at": str(profile.get("created_at") or ""),
        "confidence": sanitize_hidden_profile_leaks(_clip_inline(profile.get("confidence"), limit=40)),
    }
