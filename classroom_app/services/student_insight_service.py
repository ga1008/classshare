from __future__ import annotations

import math
from typing import Any

from .learning_progress_service import (
    build_student_public_cultivation_badge,
    clamp,
    get_student_learning_state,
    json_loads,
    normalize_course_sect_name,
    personal_stage_assignment_filter_sql,
    public_level_payload,
    safe_float,
    safe_int,
)
from .portfolio_service import build_teacher_portfolio_snapshot
from .student_support_service import (
    load_shared_student_teacher_note,
    load_student_teacher_names,
    teacher_visible_support_profile,
)


RADAR_SIZE = 168
RADAR_CENTER = RADAR_SIZE / 2
RADAR_RADIUS = 66
RADAR_LABEL_RADIUS = 78
COMPONENT_MAX_SCORES = {"material": 45, "task": 35, "interaction": 15, "consistency": 5}
ALERT_SEVERITY_LABELS = {"L1": "提示", "L2": "关注", "L3": "干预"}
ALERT_SEVERITY_RANK = {"L1": 1, "L2": 2, "L3": 3}
ALERT_TONES = {"L1": "notice", "L2": "warning", "L3": "danger"}
COMPONENT_LABELS = {
    "material": "材料研读",
    "task": "任务试炼",
    "interaction": "互动求助",
    "consistency": "稳定投入",
}


def _percent(value: float) -> int:
    return int(round(clamp(float(value or 0)) * 100))


def _score_percent(value: Any) -> int:
    return int(round(clamp(safe_float(value) / 100) * 100))


def _severity_rank(value: Any) -> int:
    return ALERT_SEVERITY_RANK.get(str(value or "").strip().upper(), 1)


def _severity_label(value: Any) -> str:
    return ALERT_SEVERITY_LABELS.get(str(value or "").strip().upper(), "提示")


def _severity_tone(value: Any) -> str:
    return ALERT_TONES.get(str(value or "").strip().upper(), "notice")


def _timeline_sort_key(value: Any) -> str:
    return str(value or "").strip().replace(" ", "T")


def _short_text(value: Any, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)]}…"


def _evidence_items(evidence: Any) -> list[dict[str, str]]:
    if not isinstance(evidence, dict):
        return []
    labels = {
        "assignment_count": "任务总数",
        "pending_count": "待完成",
        "due_soon_count": "临近截止",
        "submitted_count": "已提交",
        "completion_ratio": "完成率",
        "nearest_due_at": "最近截止",
        "last_activity_at": "最后活动",
        "failed_count": "失败次数",
        "last_failed_at": "最近失败",
        "current_delta": "本周增长",
        "previous_delta": "上周增长",
        "stage_key": "试炼阶段",
    }
    items: list[dict[str, str]] = []
    for key, value in evidence.items():
        if value in (None, "", [], {}):
            continue
        label = labels.get(str(key), str(key))
        if str(key) == "completion_ratio":
            value_text = f"{round(safe_float(value) * 100)}%"
        elif isinstance(value, list):
            value_text = " / ".join(str(item) for item in value[:4])
        else:
            value_text = str(value)
        items.append({"label": label, "value": _short_text(value_text, 48)})
        if len(items) >= 4:
            break
    return items


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


def _radar_label_position(index: int, count: int = 6) -> dict[str, float]:
    angle = -math.pi / 2 + index * 2 * math.pi / count
    x = RADAR_CENTER + math.cos(angle) * RADAR_LABEL_RADIUS
    y = RADAR_CENTER + math.sin(angle) * RADAR_LABEL_RADIUS
    return {"label_x": round(x, 1), "label_y": round(y, 1)}


def _radar_axis_points(axes: list[dict[str, Any]], *, score_key: str = "score") -> str:
    return _radar_points([{"score": axis.get(score_key, 0)} for axis in axes])


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


def _serialize_student_alert(row: dict[str, Any]) -> dict[str, Any]:
    severity = str(row.get("severity") or "L1").upper()
    evidence = json_loads(row.get("evidence_json"), {})
    course_name = row.get("course_name") or ""
    sect_name = normalize_course_sect_name(row.get("course_sect_name"), course_name=course_name)
    return {
        "id": safe_int(row.get("id")),
        "class_offering_id": safe_int(row.get("class_offering_id")),
        "student_id": safe_int(row.get("student_id")),
        "rule_key": row.get("rule_key") or "",
        "severity": severity,
        "severity_label": _severity_label(severity),
        "severity_rank": _severity_rank(severity),
        "tone": _severity_tone(severity),
        "status": row.get("status") or "active",
        "title": row.get("title") or "",
        "body": row.get("body") or "",
        "evidence": evidence if isinstance(evidence, dict) else {},
        "evidence_items": _evidence_items(evidence),
        "first_seen_at": row.get("first_seen_at") or "",
        "last_seen_at": row.get("last_seen_at") or "",
        "snoozed_until": row.get("snoozed_until") or "",
        "course_name": course_name,
        "sect_name": sect_name,
    }


def _load_student_active_alerts(
    conn,
    *,
    offering_ids: list[int],
    student_id: int,
    limit: int = 8,
) -> list[dict[str, Any]]:
    if not offering_ids:
        return []
    placeholders = ",".join("?" for _ in offering_ids)
    rows = conn.execute(
        f"""
        SELECT ca.*,
               c.name AS course_name,
               c.sect_name AS course_sect_name
        FROM cultivation_alerts ca
        LEFT JOIN class_offerings o ON o.id = ca.class_offering_id
        LEFT JOIN courses c ON c.id = o.course_id
        WHERE ca.student_id = ?
          AND ca.class_offering_id IN ({placeholders})
          AND ca.status = 'active'
        ORDER BY
          CASE ca.severity WHEN 'L3' THEN 3 WHEN 'L2' THEN 2 ELSE 1 END DESC,
          ca.last_seen_at DESC,
          ca.id DESC
        LIMIT ?
        """,
        [int(student_id), *offering_ids, max(1, min(int(limit or 8), 20))],
    ).fetchall()
    return [_serialize_student_alert(dict(row)) for row in rows]


def _merge_timeline_items(*groups: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
    items = [item for group in groups for item in group if item.get("occurred_at")]
    items.sort(
        key=lambda item: (
            _timeline_sort_key(item.get("occurred_at")),
            safe_int(item.get("sort_id")),
        ),
        reverse=True,
    )
    return items[: max(1, min(int(limit or 20), 40))]


def _load_score_event_timeline_items(
    conn,
    *,
    offering_ids: list[int],
    student_id: int,
    limit: int = 18,
) -> list[dict[str, Any]]:
    if not offering_ids:
        return []
    placeholders = ",".join("?" for _ in offering_ids)
    rows = conn.execute(
        f"""
        SELECT cse.id,
               cse.class_offering_id,
               cse.event_type,
               cse.delta,
               cse.component,
               cse.source_ref,
               cse.created_at,
               c.name AS course_name,
               c.sect_name AS course_sect_name
        FROM cultivation_score_events cse
        LEFT JOIN class_offerings o ON o.id = cse.class_offering_id
        LEFT JOIN courses c ON c.id = o.course_id
        WHERE cse.student_id = ?
          AND cse.class_offering_id IN ({placeholders})
        ORDER BY cse.created_at DESC, cse.id DESC
        LIMIT ?
        """,
        [int(student_id), *offering_ids, max(1, min(int(limit or 18), 40))],
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        delta = round(safe_float(item.get("delta")), 1)
        component = str(item.get("component") or "total")
        component_label = COMPONENT_LABELS.get(component, "修为")
        course_name = item.get("course_name") or ""
        sect_name = normalize_course_sect_name(item.get("course_sect_name"), course_name=course_name)
        direction = "增长" if delta >= 0 else "回落"
        items.append({
            "type": "score_event",
            "tone": "up" if delta >= 0 else "down",
            "sort_id": safe_int(item.get("id")),
            "occurred_at": item.get("created_at") or "",
            "title": f"{component_label}{direction} {delta:+.1f}",
            "body": f"{course_name or sect_name} · {item.get('source_ref') or '修为重新校准'}",
            "meta": f"{sect_name} · {item.get('event_type') or 'recalibration'}",
            "class_offering_id": safe_int(item.get("class_offering_id")),
        })
    return items


def _load_alert_timeline_items(
    conn,
    *,
    offering_ids: list[int],
    student_id: int,
    limit: int = 10,
) -> list[dict[str, Any]]:
    if not offering_ids:
        return []
    placeholders = ",".join("?" for _ in offering_ids)
    rows = conn.execute(
        f"""
        SELECT ca.*,
               c.name AS course_name,
               c.sect_name AS course_sect_name
        FROM cultivation_alerts ca
        LEFT JOIN class_offerings o ON o.id = ca.class_offering_id
        LEFT JOIN courses c ON c.id = o.course_id
        WHERE ca.student_id = ?
          AND ca.class_offering_id IN ({placeholders})
          AND ca.status IN ('active', 'handled', 'snoozed', 'resolved')
        ORDER BY ca.last_seen_at DESC, ca.id DESC
        LIMIT ?
        """,
        [int(student_id), *offering_ids, max(1, min(int(limit or 10), 30))],
    ).fetchall()
    items: list[dict[str, Any]] = []
    status_labels = {"active": "当前预警", "handled": "已处理", "snoozed": "已静音", "resolved": "已恢复"}
    for row in rows:
        alert = _serialize_student_alert(dict(row))
        status = str(alert.get("status") or "active")
        items.append({
            "type": "alert",
            "tone": alert["tone"],
            "sort_id": alert["id"],
            "occurred_at": alert.get("last_seen_at") or alert.get("first_seen_at") or "",
            "title": f"{alert['severity_label']}预警：{alert['title']}",
            "body": alert.get("body") or "",
            "meta": f"{status_labels.get(status, status)} · {alert.get('sect_name') or alert.get('course_name')}",
            "class_offering_id": alert.get("class_offering_id"),
            "alert_id": alert.get("id"),
        })
    return items


def _load_stage_exam_timeline_items(
    conn,
    *,
    offering_ids: list[int],
    student_id: int,
    limit: int = 8,
) -> list[dict[str, Any]]:
    if not offering_ids:
        return []
    placeholders = ",".join("?" for _ in offering_ids)
    rows = conn.execute(
        f"""
        SELECT lsea.id,
               lsea.class_offering_id,
               lsea.stage_key,
               lsea.status,
               lsea.score,
               lsea.generated_at,
               lsea.submitted_at,
               lsea.graded_at,
               lsea.passed_at,
               c.name AS course_name,
               c.sect_name AS course_sect_name
        FROM learning_stage_exam_attempts lsea
        LEFT JOIN class_offerings o ON o.id = lsea.class_offering_id
        LEFT JOIN courses c ON c.id = o.course_id
        WHERE lsea.student_id = ?
          AND lsea.class_offering_id IN ({placeholders})
        ORDER BY COALESCE(lsea.passed_at, lsea.graded_at, lsea.submitted_at, lsea.generated_at) DESC,
                 lsea.id DESC
        LIMIT ?
        """,
        [int(student_id), *offering_ids, max(1, min(int(limit or 8), 20))],
    ).fetchall()
    status_labels = {
        "generating": ("试炼生成中", "neutral"),
        "generated": ("试炼已布置", "neutral"),
        "passed": ("破境试炼通过", "up"),
        "failed": ("破境试炼受阻", "danger"),
    }
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        status = str(item.get("status") or "generated")
        title, tone = status_labels.get(status, ("破境试炼更新", "neutral"))
        course_name = item.get("course_name") or ""
        sect_name = normalize_course_sect_name(item.get("course_sect_name"), course_name=course_name)
        occurred_at = item.get("passed_at") or item.get("graded_at") or item.get("submitted_at") or item.get("generated_at") or ""
        score = item.get("score")
        score_text = f" · 得分 {round(safe_float(score), 1)}" if score is not None else ""
        items.append({
            "type": "stage_exam",
            "tone": tone,
            "sort_id": safe_int(item.get("id")),
            "occurred_at": occurred_at,
            "title": title,
            "body": f"{item.get('stage_key') or '当前境界'}{score_text}",
            "meta": f"{sect_name} · {course_name or '课程'}",
            "class_offering_id": safe_int(item.get("class_offering_id")),
        })
    return items


def _teacher_note_timeline_items(teacher_note: dict[str, Any]) -> list[dict[str, Any]]:
    if not teacher_note or not teacher_note.get("has_note"):
        return []
    return [{
        "type": "teacher_note",
        "tone": "note",
        "sort_id": 0,
        "occurred_at": teacher_note.get("updated_at") or teacher_note.get("created_at") or "",
        "title": "教师共享说明更新",
        "body": _short_text(teacher_note.get("note_text"), 150),
        "meta": teacher_note.get("updated_by_name") or teacher_note.get("created_by_name") or "教师补充",
    }]


def _build_student_cultivation_timeline(
    conn,
    *,
    offering_ids: list[int],
    student_id: int,
    teacher_note: dict[str, Any],
    limit: int = 20,
) -> list[dict[str, Any]]:
    return _merge_timeline_items(
        _load_alert_timeline_items(conn, offering_ids=offering_ids, student_id=student_id),
        _load_stage_exam_timeline_items(conn, offering_ids=offering_ids, student_id=student_id),
        _load_score_event_timeline_items(conn, offering_ids=offering_ids, student_id=student_id),
        _teacher_note_timeline_items(teacher_note),
        limit=limit,
    )


def _component_axis_percent(component_value: Any, component: str) -> float:
    max_score = COMPONENT_MAX_SCORES.get(component) or 1
    return clamp(safe_float(component_value) / max_score) * 100


def _load_class_radar_average(
    conn,
    *,
    offerings: list[dict[str, Any]],
    class_id: int,
) -> dict[str, Any]:
    offering_ids = [int(item["class_offering_id"]) for item in offerings]
    if not offering_ids:
        return {"available": False, "student_count": 0, "axes": []}
    student_rows = conn.execute(
        """
        SELECT id
        FROM students
        WHERE class_id = ?
          AND COALESCE(enrollment_status, 'active') = 'active'
        """,
        (int(class_id),),
    ).fetchall()
    student_ids = [int(row["id"]) for row in student_rows]
    if len(student_ids) <= 1:
        return {"available": False, "student_count": len(student_ids), "axes": []}
    placeholders = ",".join("?" for _ in offering_ids)
    rows = conn.execute(
        f"""
        SELECT student_id, score, components_json
        FROM learning_progress_snapshots
        WHERE class_offering_id IN ({placeholders})
          AND student_id IN ({",".join("?" for _ in student_ids)})
        """,
        [*offering_ids, *student_ids],
    ).fetchall()
    per_student: dict[int, dict[str, list[float]]] = {}
    for row in rows:
        item = dict(row)
        student_id = int(item["student_id"])
        bucket = per_student.setdefault(
            student_id,
            {"material": [], "task": [], "interaction": [], "consistency": [], "cultivation": []},
        )
        components = json_loads(item.get("components_json"), {})
        if isinstance(components, dict):
            for component in ("material", "task", "interaction", "consistency"):
                bucket[component].append(_component_axis_percent(components.get(component), component))
        bucket["cultivation"].append(clamp(safe_float(item.get("score")) / 100) * 100)
    if len(per_student) <= 1:
        return {"available": False, "student_count": len(student_ids), "axes": []}
    axis_totals = {"material": 0.0, "task": 0.0, "interaction": 0.0, "consistency": 0.0, "cultivation": 0.0}
    axis_counts = {key: 0 for key in axis_totals}
    for values in per_student.values():
        for key, samples in values.items():
            if not samples:
                continue
            axis_totals[key] += sum(samples) / len(samples)
            axis_counts[key] += 1
    average_task = axis_totals["task"] / axis_counts["task"] if axis_counts["task"] else 0.0
    axes = {
        "material": axis_totals["material"] / axis_counts["material"] if axis_counts["material"] else 0.0,
        "task": average_task,
        "quality": average_task,
        "interaction": axis_totals["interaction"] / axis_counts["interaction"] if axis_counts["interaction"] else 0.0,
        "consistency": axis_totals["consistency"] / axis_counts["consistency"] if axis_counts["consistency"] else 0.0,
        "cultivation": axis_totals["cultivation"] / axis_counts["cultivation"] if axis_counts["cultivation"] else 0.0,
    }
    return {
        "available": True,
        "student_count": len(student_ids),
        "sample_count": len(per_student),
        "axes": {key: round(value) for key, value in axes.items()},
    }


def _build_course_progress(
    conn,
    *,
    offerings: list[dict[str, Any]],
    student_id: int,
) -> list[dict[str, Any]]:
    courses: list[dict[str, Any]] = []
    for offering in offerings:
        state = get_student_learning_state(conn, int(offering["class_offering_id"]), int(student_id))
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
    class_average: dict[str, Any] | None = None,
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
        {"key": "material", "label": "材料吸收", "short_label": "材", "score": round(material_score)},
        {"key": "task", "label": "任务执行", "short_label": "任", "score": round(task_score)},
        {"key": "quality", "label": "成绩质量", "short_label": "绩", "score": round(quality_score)},
        {"key": "interaction", "label": "互动表达", "short_label": "互", "score": round(interaction_score)},
        {"key": "consistency", "label": "学习稳定", "short_label": "稳", "score": round(consistency_score)},
        {"key": "cultivation", "label": "修为推进", "short_label": "修", "score": round(cultivation_score)},
    ]
    class_axes = (class_average or {}).get("axes") or {}
    has_class_average = bool((class_average or {}).get("available") and class_axes)
    for index, axis in enumerate(axes):
        axis.update(_radar_label_position(index, len(axes)))
        axis["class_average_score"] = safe_int(class_axes.get(axis["key"])) if has_class_average else None
    return {
        "axes": axes,
        "points": _radar_points(axes),
        "class_average_points": _radar_axis_points(axes, score_key="class_average_score") if has_class_average else "",
        "has_class_average": has_class_average,
        "class_average": class_average or {"available": False, "student_count": 0, "axes": []},
        "grid_points": [_radar_grid_points(0.33), _radar_grid_points(0.66), _radar_grid_points(1.0)],
    }


def _load_latest_student_support_profile(conn, *, student_id: int, class_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT cbp.id,
               cbp.class_offering_id,
               cbp.profile_summary,
               cbp.mental_state_summary,
               cbp.support_strategy,
               cbp.personality_traits,
               cbp.preference_summary,
               cbp.language_habit_summary,
               cbp.preferred_ai_style,
               cbp.interest_hypothesis,
               cbp.evidence_summary,
               cbp.confidence,
               cbp.raw_payload,
               cbp.created_at,
               c.name AS course_name,
               t.name AS teacher_name
        FROM classroom_behavior_profiles cbp
        JOIN class_offerings o ON o.id = cbp.class_offering_id
        JOIN courses c ON c.id = o.course_id
        JOIN teachers t ON t.id = o.teacher_id
        WHERE o.class_id = ?
          AND cbp.user_pk = ?
          AND cbp.user_role = 'student'
        ORDER BY cbp.created_at DESC, cbp.id DESC
        LIMIT 1
        """,
        (int(class_id), int(student_id)),
    ).fetchone()
    return dict(row) if row else None


def build_teacher_student_insight(conn, teacher_id: int, student_id: int) -> dict[str, Any] | None:
    student = _load_teacher_student_row(conn, teacher_id=int(teacher_id), student_id=int(student_id))
    if not student:
        return None

    offerings = _load_teacher_student_offerings(
        conn,
        teacher_id=int(teacher_id),
        class_id=int(student["class_id"]),
    )
    offering_ids = [int(item["class_offering_id"]) for item in offerings]
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
        offering_ids=offering_ids,
        student_id=int(student_id),
    )
    class_radar_average = _load_class_radar_average(
        conn,
        offerings=offerings,
        class_id=int(student["class_id"]),
    )
    radar = _build_radar_axes(
        courses=courses,
        task_summary=task_summary,
        activity_summary=activity_summary,
        class_average=class_radar_average,
    )
    public_badge = build_student_public_cultivation_badge(conn, int(student_id))
    raw_support_profile = _load_latest_student_support_profile(
        conn,
        student_id=int(student_id),
        class_id=int(student["class_id"]),
    )
    support_profile = teacher_visible_support_profile(raw_support_profile)
    if raw_support_profile:
        support_profile["source_course_name"] = raw_support_profile.get("course_name") or ""
        support_profile["source_teacher_name"] = raw_support_profile.get("teacher_name") or ""
    portfolio = build_teacher_portfolio_snapshot(
        conn,
        int(student_id),
        class_offering_ids=offering_ids,
    )
    teacher_note = load_shared_student_teacher_note(conn, int(student_id))
    current_alerts = _load_student_active_alerts(
        conn,
        offering_ids=offering_ids,
        student_id=int(student_id),
    )
    cultivation_timeline = _build_student_cultivation_timeline(
        conn,
        offering_ids=offering_ids,
        student_id=int(student_id),
        teacher_note=teacher_note,
    )
    related_teacher_names = load_student_teacher_names(conn, int(student_id))
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
        "support_profile": support_profile,
        "portfolio": portfolio,
        "teacher_note": teacher_note,
        "current_alerts": current_alerts,
        "cultivation_timeline": cultivation_timeline,
        "related_teacher_names": related_teacher_names,
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
