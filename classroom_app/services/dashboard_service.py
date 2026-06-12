from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from typing import Any

from ..db.connection import get_configured_db_engine
from .message_center_service import CATEGORY_LABELS, get_message_center_summary
from .academic_service import (
    build_semester_calendar_payload,
    china_now,
    china_today,
    load_student_semester_rows,
    load_teacher_semester_rows,
    parse_date_input,
)
from .exam_reminder_service import build_event_reminder_detail
from .student_auth_service import build_student_security_summary
from .ui_copy_service import get_ui_copy_block, render_ui_copy_block
from .prompt_utils import polite_address
from .learning_progress_service import (
    build_student_global_cultivation_profile,
    serialize_student_learning_progress,
)
from .todo_service import build_classroom_todo_overview
from .feedback_review_service import build_feedback_review_summary
from .manage_nav_service import build_dashboard_domain_cards, canonical_manage_href

RECENT_ACTIVITY_DAYS = 14
DEFAULT_TIMELINE_HOUR = "08:00"
DASHBOARD_WEEKDAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
DASHBOARD_COURSE_TONES = ("indigo", "teal", "sky", "amber", "rose", "violet", "emerald", "slate")
DASHBOARD_COURSE_PATTERNS = ("grid", "dots", "diagonal", "rings")

ACTIVITY_TONE_BY_CATEGORY = {
    "private_message": "neutral",
    "assignment": "primary",
    "discussion_mention": "warning",
    "submission": "success",
    "grading_result": "success",
    "ai_feedback": "primary",
}

DASHBOARD_FILTER_VALUES = {
    "teacher": ("all", "attention", "recent"),
    "student": ("all", "attention", "progress", "recent"),
}


def _dashboard_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _stable_dashboard_bucket(value: Any, *, modulo: int) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    total = 0
    for char in text:
        total = (total * 131 + ord(char)) % 1000003
    return total % modulo if modulo > 0 else 0


def _dashboard_course_visual(course_id: Any) -> dict[str, str]:
    tone_index = _stable_dashboard_bucket(course_id, modulo=len(DASHBOARD_COURSE_TONES))
    pattern_index = _stable_dashboard_bucket(f"{course_id}:pattern", modulo=len(DASHBOARD_COURSE_PATTERNS))
    return {
        "tone": DASHBOARD_COURSE_TONES[tone_index],
        "pattern": DASHBOARD_COURSE_PATTERNS[pattern_index],
    }


def _dashboard_todo_sort_key(item: dict[str, Any]) -> tuple[int, str, str, int]:
    return (
        1 if item.get("is_completed") else 0,
        str(item.get("effective_end_at") or item.get("effective_start_at") or "9999-12-31"),
        str(item.get("offering_label") or ""),
        _dashboard_int(item.get("source_id")),
    )


def _dashboard_parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) == 10:
        parsed_date = parse_date_input(text)
        return datetime.combine(parsed_date, time.min) if parsed_date else None
    try:
        return datetime.fromisoformat(text.replace("Z", "")).replace(tzinfo=None)
    except (TypeError, ValueError):
        parsed_date = parse_date_input(text[:10])
        return datetime.combine(parsed_date, time.min) if parsed_date else None


def _dashboard_week_start(value: date) -> date:
    return value - timedelta(days=value.weekday())


def _dashboard_month_day_label(value: date | datetime | None) -> str:
    if value is None:
        return ""
    day_value = value.date() if isinstance(value, datetime) else value
    return f"{day_value.month}月{day_value.day}日"


def _dashboard_datetime_label(value: datetime | None, *, with_time: bool = True) -> str:
    if value is None:
        return ""
    base = f"{_dashboard_month_day_label(value)} {DASHBOARD_WEEKDAY_LABELS[value.weekday()]}"
    if with_time:
        return f"{base} {value.hour:02d}:{value.minute:02d}"
    return base


def _dashboard_relative_event_label(starts_at: datetime | None, now: datetime, *, label: str = "监考") -> str:
    if starts_at is None:
        return "时间待确认"
    delta_days = (starts_at.date() - now.date()).days
    if delta_days < 0:
        return "已结束"
    if delta_days == 0:
        return f"今天{label}"
    if delta_days == 1:
        return f"明天{label}"
    return f"{delta_days} 天后{label}"


def _dashboard_bar_position(start_date: date, end_date: date, week_start: date) -> dict[str, float]:
    week_end = week_start + timedelta(days=6)
    start_offset = max(0, min(6, (start_date - week_start).days))
    end_offset = max(0, min(6, (end_date - week_start).days))
    if end_date < week_start:
        start_offset = end_offset = 0
    elif start_date > week_end:
        start_offset = end_offset = 6
    if end_offset < start_offset:
        end_offset = start_offset
    return {
        "bar_left": round(start_offset / 7 * 100, 4),
        "bar_width": round(((end_offset - start_offset + 1) / 7) * 100, 4),
        "start_offset": start_offset,
        "end_offset": end_offset,
    }


def _dashboard_safe_json(raw_value: Any) -> dict[str, Any]:
    if not raw_value:
        return {}
    try:
        payload = json.loads(str(raw_value))
    except (TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _teacher_calendar_event_todo(row: Any, *, now: datetime) -> dict[str, Any] | None:
    item = dict(row)
    source_type = str(item.get("source_type") or "academic_invigilation")
    is_course_exam = source_type == "academic_course_exam"
    starts_at = _dashboard_parse_datetime(item.get("starts_at") or item.get("due_at"))
    ends_at = _dashboard_parse_datetime(item.get("ends_at") or item.get("due_at")) or starts_at
    created_at = _dashboard_parse_datetime(item.get("created_at")) or starts_at or now
    if starts_at is None:
        return None
    if ends_at is None or ends_at < starts_at:
        ends_at = starts_at
    start_date = starts_at.date()
    end_date = ends_at.date()
    metadata = _dashboard_safe_json(item.get("metadata_json"))
    source_id = _dashboard_int(item.get("id"))
    event_label = "考试" if is_course_exam else "监考"
    status_label = _dashboard_relative_event_label(starts_at, now, label=event_label)
    is_completed = bool(ends_at < now)
    duration_label = (
        f"{_dashboard_datetime_label(starts_at)} - {ends_at.hour:02d}:{ends_at.minute:02d}"
        if starts_at.date() == ends_at.date() and starts_at.time() != ends_at.time()
        else _dashboard_datetime_label(starts_at)
    )
    return {
        "id": f"{source_type}:{source_id}",
        "source_type": source_type,
        "source_id": source_id,
        "title": str(item.get("title") or f"{event_label}安排"),
        "subtitle": str(item.get("subtitle") or f"教务系统{event_label}"),
        "notes": str(item.get("notes") or ""),
        "link_url": str(item.get("link_url") or "/dashboard#dashboard-semester"),
        "status": "completed" if is_completed else "upcoming",
        "status_label": status_label,
        "tone": str(item.get("tone") or "invigilation"),
        "is_manual": False,
        "is_completed": is_completed,
        "can_complete": False,
        "no_deadline": False,
        "start_at": starts_at.isoformat(timespec="minutes"),
        "due_at": starts_at.isoformat(timespec="minutes"),
        "created_at": created_at.isoformat(timespec="minutes"),
        "effective_start_at": starts_at.isoformat(timespec="minutes"),
        "effective_end_at": ends_at.isoformat(timespec="minutes"),
        "effective_start_date": start_date.isoformat(),
        "effective_end_date": end_date.isoformat(),
        "start_label": _dashboard_datetime_label(starts_at),
        "deadline_label": _dashboard_datetime_label(starts_at),
        "relative_due_label": status_label,
        "duration_label": duration_label,
        "due_time_label": f"{starts_at.hour:02d}:{starts_at.minute:02d}",
        "offering_label": "教务考试" if is_course_exam else "教务监考",
        "course_name": str(metadata.get("course_name") or ""),
        "class_name": str(metadata.get("teaching_class_name") or ""),
        "location": str(item.get("location") or ""),
        "metadata": metadata,
    }


ACADEMIC_IMPORTANT_SOURCES = {"academic_exam", "academic_course_exam", "academic_invigilation"}


def _dashboard_academic_event_label(item: dict[str, Any]) -> str:
    source_type = str(item.get("source_type") or "")
    if source_type in {"academic_exam", "academic_course_exam"}:
        return "考试"
    if source_type == "academic_invigilation":
        return "监考"
    return "教务"


def _dashboard_academic_focus_tone(item: dict[str, Any], *, now: datetime) -> str:
    starts_at = _dashboard_parse_datetime(item.get("effective_start_at") or item.get("start_at") or item.get("due_at"))
    if starts_at and starts_at.date() <= now.date() + timedelta(days=1):
        return "danger"
    return "warning"


def _dashboard_academic_focus_items(
    items: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    now_dt = now or china_now().replace(tzinfo=None)
    candidates = []
    for item in items:
        if str(item.get("source_type") or "") not in ACADEMIC_IMPORTANT_SOURCES:
            continue
        if item.get("is_completed"):
            continue
        starts_at = _dashboard_parse_datetime(item.get("effective_start_at") or item.get("start_at") or item.get("due_at"))
        if starts_at and starts_at < now_dt - timedelta(hours=2):
            continue
        candidates.append(item)

    candidates.sort(
        key=lambda value: (
            str(value.get("effective_start_at") or value.get("start_at") or value.get("due_at") or "9999-12-31"),
            str(value.get("course_name") or value.get("title") or ""),
        )
    )
    focus_items = []
    for item in candidates[: max(0, int(limit or 0))]:
        label = _dashboard_academic_event_label(item)
        title_text = re.sub(r"^教务(?:考试|监考)[：:]\s*", "", str(item.get("title") or "")).strip()
        course_name = str(item.get("course_name") or title_text or label).strip()
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        location = str(item.get("location") or metadata.get("location") or "").strip()
        fragments = [
            str(item.get("duration_label") or item.get("deadline_label") or "").strip(),
            location,
            str(item.get("class_name") or "").strip(),
            str(item.get("status_label") or item.get("relative_due_label") or "").strip(),
        ]
        focus_items.append(
            {
                "title": f"{label}提醒：{course_name}",
                "description": " · ".join(part for part in fragments if part) or "教务系统已同步，请及时查看安排。",
                "href": str(item.get("link_url") or item.get("href") or "/dashboard#dashboard-semester"),
                "tone": _dashboard_academic_focus_tone(item, now=now_dt),
            }
        )
    return focus_items


def _load_teacher_academic_focus_items(
    conn: sqlite3.Connection,
    *,
    teacher_id: int,
    limit: int = 3,
) -> list[dict[str, Any]]:
    now_dt = china_now().replace(tzinfo=None)
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM teacher_calendar_events
            WHERE teacher_id = ?
              AND source_type IN ('academic_invigilation', 'academic_course_exam')
              AND status = 'active'
              AND deleted_at IS NULL
              AND COALESCE(starts_at, due_at, created_at) >= ?
            ORDER BY COALESCE(starts_at, due_at, created_at), id
            LIMIT ?
            """,
            (int(teacher_id), (now_dt - timedelta(hours=2)).isoformat(timespec="minutes"), max(1, int(limit or 1)) * 2),
        ).fetchall()
    except sqlite3.Error:
        return []
    todos = [
        todo
        for row in rows
        if (todo := _teacher_calendar_event_todo(row, now=now_dt))
    ]
    return _dashboard_academic_focus_items(todos, now=now_dt, limit=limit)


def _attach_teacher_calendar_events_to_buckets(
    conn: sqlite3.Connection,
    *,
    buckets: dict[int, dict[str, Any]],
    semesters: list[dict[str, Any]],
    user: dict[str, Any],
) -> None:
    if str(user.get("role") or "").strip().lower() != "teacher":
        return
    teacher_id = _dashboard_int(user.get("id"))
    semester_ids = [_dashboard_int(item.get("id")) for item in semesters if _dashboard_int(item.get("id"))]
    if not teacher_id or not semester_ids:
        return
    placeholders = ",".join("?" for _ in semester_ids)
    rows = conn.execute(
        f"""
        SELECT *
        FROM teacher_calendar_events
        WHERE teacher_id = ?
          AND semester_id IN ({placeholders})
          AND source_type IN ('academic_invigilation', 'academic_course_exam')
          AND status = 'active'
          AND deleted_at IS NULL
        ORDER BY COALESCE(starts_at, due_at, created_at), id
        """,
        (teacher_id, *semester_ids),
    ).fetchall()
    if not rows:
        return

    semesters_by_id = {_dashboard_int(item.get("id")): item for item in semesters}
    now_dt = china_now().replace(tzinfo=None)
    for row in rows:
        semester_id = _dashboard_int(row["semester_id"])
        bucket = buckets.get(semester_id)
        semester = semesters_by_id.get(semester_id)
        if not bucket or not semester:
            continue
        todo = _teacher_calendar_event_todo(row, now=now_dt)
        if not todo:
            continue
        bucket["items"].append(todo)
        event_date = parse_date_input(todo["effective_start_date"])
        if not event_date:
            continue
        week_start = _dashboard_week_start(event_date)
        week_key = week_start.isoformat()
        semester_start = parse_date_input(semester.get("start_date"))
        semester_calendar_start = _dashboard_week_start(semester_start) if semester_start else week_start
        week_index = max(1, int(((week_start - semester_calendar_start).days // 7) + 1))
        target_week = bucket["weeks"].setdefault(
            week_key,
            {
                "key": week_key,
                "week_index": week_index,
                "label": f"第 {week_index} 周",
                "range_label": f"{_dashboard_month_day_label(week_start)} - {_dashboard_month_day_label(week_start + timedelta(days=6))}",
                "todos": [],
                "is_current": week_start <= now_dt.date() <= week_start + timedelta(days=6),
            },
        )
        positioned = {
            **todo,
            **_dashboard_bar_position(
                parse_date_input(todo["effective_start_date"]) or event_date,
                parse_date_input(todo["effective_end_date"]) or event_date,
                week_start,
            ),
        }
        target_week["todos"].append(positioned)
        target_week["is_current"] = bool(target_week.get("is_current") or week_start <= now_dt.date() <= week_start + timedelta(days=6))


def _match_semester_for_offering(
    semesters: list[dict[str, Any]],
    offering: dict[str, Any],
) -> dict[str, Any] | None:
    semester_id = _dashboard_int(offering.get("semester_id"))
    if semester_id:
        for semester in semesters:
            if _dashboard_int(semester.get("id")) == semester_id:
                return semester

    semester_name = str(offering.get("semester") or "").strip()
    if semester_name:
        for semester in semesters:
            if str(semester.get("name") or "").strip() == semester_name:
                return semester
        return None

    teacher_id = _dashboard_int(offering.get("teacher_id"))
    candidates = [
        semester
        for semester in semesters
        if not teacher_id or _dashboard_int(semester.get("teacher_id")) in {0, teacher_id}
    ]
    if len(candidates) == 1:
        return candidates[0]

    for semester in candidates:
        if semester.get("is_current"):
            return semester

    if len(semesters) == 1:
        return semesters[0]

    return None


def _dashboard_todo_option(offering: dict[str, Any]) -> dict[str, Any]:
    course_name = str(offering.get("course_name") or "未命名课程").strip()
    class_name = str(offering.get("class_name") or "未命名班级").strip()
    return {
        "class_offering_id": _dashboard_int(offering.get("id")),
        "course_name": course_name,
        "class_name": class_name,
        "label": f"{course_name} · {class_name}",
    }


def _enrich_dashboard_todo(item: dict[str, Any], offering: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(item)
    option = _dashboard_todo_option(offering)
    class_offering_id = option["class_offering_id"]
    enriched["class_offering_id"] = class_offering_id
    enriched["course_name"] = option["course_name"]
    enriched["class_name"] = option["class_name"]
    enriched["offering_label"] = option["label"]
    if not str(enriched.get("link_url") or "").strip() and class_offering_id:
        enriched["link_url"] = f"/classroom/{class_offering_id}#timeline-panel"
    return enriched


def _attach_dashboard_todos_to_semester_calendar(
    conn: sqlite3.Connection,
    semester_calendar: dict[str, Any],
    offerings: list[dict[str, Any]],
    user: dict[str, Any],
    *,
    preloaded_todo_overviews: dict[int, dict[str, Any]] | None = None,
) -> None:
    semesters = [
        item
        for item in semester_calendar.get("semesters", [])
        if isinstance(item, dict)
    ]
    if not semesters:
        return

    can_create_manual = str(user.get("role") or "").strip().lower() == "student"
    buckets: dict[int, dict[str, Any]] = {}
    for semester in semesters:
        semester_id = _dashboard_int(semester.get("id"))
        if not semester_id:
            continue
        role_policy = {
            "can_create_manual": can_create_manual,
            "show_student_stage_exams": can_create_manual,
            "description": (
                "学生端显示课程安排、待提交任务、个人试炼和自定义待办。"
                if can_create_manual
                else "教师端显示课程安排和课堂任务截止，不展示学生个人试炼与学生自定义待办。"
            ),
        }
        bucket = {
            "items": [],
            "weeks": {},
            "todo_create_options": [],
            "role_policy": role_policy,
        }
        buckets[semester_id] = bucket
        semester["todo_overview"] = {
            "items": [],
            "weeks": [],
            "summary": {
                "total_count": 0,
                "open_count": 0,
                "manual_count": 0,
                "due_soon_count": 0,
                "no_deadline_count": 0,
            },
            "role_policy": role_policy,
            "active_week_key": "",
        }
        semester["todo_create_options"] = bucket["todo_create_options"]

    for offering in offerings:
        class_offering_id = _dashboard_int(offering.get("id"))
        if not class_offering_id:
            continue
        semester = _match_semester_for_offering(semesters, offering)
        if not semester:
            continue
        semester_id = _dashboard_int(semester.get("id"))
        bucket = buckets.get(semester_id)
        if not bucket:
            continue

        option = _dashboard_todo_option(offering)
        if option["class_offering_id"] and not any(
            existing.get("class_offering_id") == option["class_offering_id"]
            for existing in bucket["todo_create_options"]
        ):
            bucket["todo_create_options"].append(option)

        overview = (preloaded_todo_overviews or {}).get(class_offering_id)
        if overview is None:
            try:
                overview = build_classroom_todo_overview(
                    conn,
                    class_offering_id=class_offering_id,
                    user=user,
                )
            except Exception:
                continue

        bucket["items"].extend(
            _enrich_dashboard_todo(item, offering)
            for item in overview.get("items", [])
            if isinstance(item, dict)
        )
        for week in overview.get("weeks", []):
            if not isinstance(week, dict):
                continue
            week_key = str(week.get("key") or "").strip()
            if not week_key:
                continue
            target_week = bucket["weeks"].setdefault(
                week_key,
                {
                    "key": week_key,
                    "week_index": week.get("week_index"),
                    "label": week.get("label") or "",
                    "range_label": week.get("range_label") or "",
                    "todos": [],
                    "is_current": bool(week.get("is_current")),
                },
            )
            target_week["todos"].extend(
                _enrich_dashboard_todo(todo, offering)
                for todo in week.get("todos", [])
                if isinstance(todo, dict)
            )
            target_week["is_current"] = bool(target_week.get("is_current") or week.get("is_current"))

    _attach_teacher_calendar_events_to_buckets(
        conn,
        buckets=buckets,
        semesters=semesters,
        user=user,
    )

    for semester in semesters:
        semester_id = _dashboard_int(semester.get("id"))
        bucket = buckets.get(semester_id)
        if not bucket:
            continue

        items = sorted(bucket["items"], key=_dashboard_todo_sort_key)
        weeks = []
        for week in bucket["weeks"].values():
            todos = sorted(week["todos"], key=_dashboard_todo_sort_key)
            weeks.append({
                **week,
                "todos": todos,
                "todo_count": len(todos),
                "open_count": sum(1 for item in todos if not item.get("is_completed")),
            })
        weeks.sort(key=lambda item: str(item.get("key") or ""))

        active_week_key = ""
        for week in weeks:
            if week.get("is_current"):
                active_week_key = str(week.get("key") or "")
                break
        if not active_week_key and weeks:
            active_week_key = str(weeks[0].get("key") or "")

        semester["todo_overview"] = {
            "items": items,
            "weeks": weeks,
            "summary": {
                "total_count": len(items),
                "open_count": sum(1 for item in items if not item.get("is_completed")),
                "manual_count": sum(1 for item in items if item.get("source_type") == "manual"),
                "due_soon_count": sum(
                    1
                    for item in items
                    if "后截止" in str(item.get("relative_due_label") or "")
                ),
                "no_deadline_count": sum(1 for item in items if item.get("no_deadline")),
            },
            "role_policy": bucket["role_policy"],
            "active_week_key": active_week_key,
        }
        semester["todo_create_options"] = sorted(
            bucket["todo_create_options"],
            key=lambda item: str(item.get("label") or ""),
        )


def _normalize_dashboard_group_label(value: Any, fallback: str = "未分类") -> str:
    normalized = re.sub(r"\s+", " ", str(value or "").strip())
    return normalized or fallback


def _dashboard_sort_text(value: Any) -> str:
    return str(value or "").strip().casefold()


def _dashboard_datetime_sort_value(value: Any) -> int:
    parsed = _parse_datetime(value)
    if parsed is None:
        return 0
    return int(parsed.timestamp())


def _build_teacher_activity_score(
    *,
    recent_active_student_count: int,
    recent_login_count: int,
    pending_review_count: int,
    draft_count: int,
    last_activity_at: Any,
) -> int:
    recency = _dashboard_datetime_sort_value(last_activity_at)
    # Keep real classroom usage dominant, then fall back to content/task activity and recency.
    return (
        int(recent_active_student_count or 0) * 1_000_000
        + int(recent_login_count or 0) * 1_000
        + int(pending_review_count or 0) * 100
        + int(draft_count or 0) * 20
        + min(recency // 86_400, 999)
    )


def _dashboard_weekday_label(value: int) -> str:
    if 0 <= int(value) < len(DASHBOARD_WEEKDAY_LABELS):
        return DASHBOARD_WEEKDAY_LABELS[int(value)]
    return ""


def _safe_dashboard_date(value: Any) -> date | None:
    try:
        return parse_date_input(value)
    except (TypeError, ValueError):
        return None


def _dashboard_relative_day_label(session_date: date, today: date) -> str:
    delta_days = (session_date - today).days
    if delta_days == 0:
        return "今天"
    if delta_days == 1:
        return "明天"
    if delta_days == 2:
        return "后天"
    if delta_days == -1:
        return "昨天"
    if delta_days == -2:
        return "前天"
    if delta_days > 0:
        return f"{delta_days} 天后"
    return f"{abs(delta_days)} 天前"


def _extract_dashboard_time_label(*values: Any) -> tuple[str, bool]:
    for value in values:
        raw = str(value or "")
        if not raw:
            continue
        match = re.search(r"(?<!\d)([01]?\d|2[0-3])[:：]([0-5]\d)(?!\d)", raw)
        if match:
            return f"{int(match.group(1)):02d}:{match.group(2)}", True
        match = re.search(r"(?<!\d)([01]?\d|2[0-3])\s*(?:点|时)(?!\d)", raw)
        if match:
            return f"{int(match.group(1)):02d}:00", True
    return DEFAULT_TIMELINE_HOUR, False


def _truncate_dashboard_text(value: Any, max_length: int = 88) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if len(text) <= max_length:
        return text
    return text[: max_length - 1].rstrip() + "…"


def _dashboard_notice_text(value: Any, *, fallback: str = "") -> str:
    if isinstance(value, dict):
        for key in ("message", "description", "title", "label"):
            text = re.sub(r"\s+", " ", str(value.get(key) or "").strip())
            if text:
                return text
        return fallback
    if isinstance(value, (list, tuple, set)):
        for item in value:
            text = _dashboard_notice_text(item)
            if text:
                return text
        return fallback
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text or fallback


def _build_teacher_timeline_item(
    *,
    offering: dict[str, Any],
    session: dict[str, Any],
    session_date: date,
    today: date,
    time_label: str,
    is_time_explicit: bool,
    is_fallback: bool = False,
) -> dict[str, Any]:
    status = "upcoming"
    if session_date < today:
        status = "completed"
    elif session_date == today:
        status = "current"

    title = str(session.get("title") or "").strip()
    if not title:
        title = "首次上课" if is_fallback else "课堂安排"
    section_count = _dashboard_int(session.get("section_count"))
    starts_at = f"{session_date.isoformat()}T{time_label}:00"
    date_key = f"{session_date.isoformat()} {time_label}"

    return {
        "id": str(session.get("session_id") or session.get("id") or f"fallback-{offering.get('id')}"),
        "kind": "class",
        "offering_id": _dashboard_int(offering.get("id")),
        "course_name": str(offering.get("course_name") or "未命名课程"),
        "class_name": str(offering.get("class_name") or "未命名班级"),
        "department": str(offering.get("department_label") or "未分类"),
        "title": title,
        "summary": _truncate_dashboard_text(session.get("content") or offering.get("summary") or ""),
        "href": f"/classroom/{_dashboard_int(offering.get('id'))}#timeline-panel",
        "starts_at": starts_at,
        "timeline_key": date_key,
        "date_label": f"{session_date.month}月{session_date.day}日",
        "date_full_label": session_date.isoformat(),
        "year_label": f"{session_date.year}年",
        "hour_label": time_label,
        "weekday_label": _dashboard_weekday_label(session_date.weekday()),
        "relative_label": _dashboard_relative_day_label(session_date, today),
        "status": status,
        "section_label": f"{section_count} 节" if section_count > 0 else "",
        "week_label": f"第 {int(session.get('week_index') or 0)} 周" if int(session.get("week_index") or 0) > 0 else "",
        "time_hint": "" if is_time_explicit else "未设置具体上课小时，按默认 08:00 归纳。",
    }


def _attach_teacher_timeline_items(
    conn: sqlite3.Connection,
    offerings: list[dict[str, Any]],
) -> None:
    if not offerings:
        return

    today = china_today()
    offering_by_id = {
        _dashboard_int(item.get("id")): item
        for item in offerings
        if _dashboard_int(item.get("id")) > 0
    }
    for offering in offerings:
        offering["timeline_items"] = []

    offering_ids = sorted(offering_by_id)
    if not offering_ids:
        return
    placeholders = ",".join("?" for _ in offering_ids)
    rows = conn.execute(
        f"""
        SELECT id AS session_id,
               class_offering_id,
               order_index,
               title,
               content,
               section_count,
               slot_section_count,
               session_date,
               weekday,
               week_index
        FROM class_offering_sessions
        WHERE class_offering_id IN ({placeholders})
        ORDER BY date(session_date), order_index, id
        """,
        tuple(offering_ids),
    ).fetchall()

    seen_offering_ids: set[int] = set()
    for row in rows:
        item = dict(row)
        offering_id = _dashboard_int(item.get("class_offering_id"))
        offering = offering_by_id.get(offering_id)
        if not offering:
            continue
        session_date = _safe_dashboard_date(item.get("session_date"))
        if not session_date:
            continue
        time_label, is_time_explicit = _extract_dashboard_time_label(
            item.get("starts_at"),
            item.get("start_time"),
            offering.get("schedule_info"),
        )
        offering["timeline_items"].append(
            _build_teacher_timeline_item(
                offering=offering,
                session=item,
                session_date=session_date,
                today=today,
                time_label=time_label,
                is_time_explicit=is_time_explicit,
            )
        )
        seen_offering_ids.add(offering_id)

    for offering_id, offering in offering_by_id.items():
        if offering_id in seen_offering_ids:
            continue
        first_class_date = _safe_dashboard_date(offering.get("first_class_date"))
        if not first_class_date:
            continue
        time_label, is_time_explicit = _extract_dashboard_time_label(offering.get("schedule_info"))
        offering["timeline_items"].append(
            _build_teacher_timeline_item(
                offering=offering,
                session={
                    "id": f"first-{offering_id}",
                    "title": "首次上课",
                    "content": offering.get("summary"),
                    "section_count": 0,
                    "week_index": 0,
                },
                session_date=first_class_date,
                today=today,
                time_label=time_label,
                is_time_explicit=is_time_explicit,
                is_fallback=True,
            )
        )

    for offering in offerings:
        offering["timeline_items"].sort(
            key=lambda item: (
                str(item.get("starts_at") or ""),
                _dashboard_sort_text(item.get("course_name")),
                _dashboard_sort_text(item.get("class_name")),
            )
        )


_AGENDA_TODO_KIND_BY_SOURCE = {
    "lesson": "class",
    "assignment": "assignment",
    "academic_course_exam": "exam",
    "academic_exam": "exam",
    "exam": "exam",
    "stage": "todo",
    "manual": "todo",
}

_AGENDA_FALLBACK_TITLE = {
    "invigilation": "监考安排",
    "exam": "考试安排",
    "assignment": "作业",
    "todo": "待办事项",
    "class": "课堂安排",
}


def _dashboard_agenda_event(
    *,
    kind: str,
    when: datetime,
    title: Any,
    subtitle: Any,
    href: Any,
    today: date,
) -> dict[str, Any]:
    """Normalise any dated item into the shape the agenda renderer expects."""
    event_date = when.date()
    has_time = (when.hour, when.minute) != (0, 0)
    if event_date < today:
        status = "completed"
    elif event_date == today:
        status = "current"
    else:
        status = "upcoming"
    clean_title = str(title or "").strip() or _AGENDA_FALLBACK_TITLE.get(kind, "日程安排")
    return {
        "kind": kind,
        "title": clean_title,
        "subtitle": str(subtitle or "").strip(),
        "href": str(href or "#").strip() or "#",
        "status": status,
        "starts_at": when.isoformat(timespec="minutes"),
        "timeline_key": f"{event_date.isoformat()} {when.hour:02d}:{when.minute:02d}",
        "date_full_label": event_date.isoformat(),
        "date_label": f"{event_date.month}月{event_date.day}日",
        "year_label": f"{event_date.year}年",
        "hour_label": f"{when.hour:02d}:{when.minute:02d}" if has_time else "全天",
        "weekday_label": _dashboard_weekday_label(event_date.weekday()),
        "relative_label": _dashboard_relative_day_label(event_date, today),
    }


def _build_teacher_calendar_agenda_events(
    conn: sqlite3.Connection,
    *,
    teacher_id: int,
    today: date,
    now: datetime,
) -> list[dict[str, Any]]:
    """Invigilation / exam / personal-todo events from the teacher calendar."""
    if not teacher_id:
        return []
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM teacher_calendar_events
            WHERE teacher_id = ?
              AND status = 'active'
              AND deleted_at IS NULL
              AND COALESCE(starts_at, due_at, created_at) >= ?
            ORDER BY COALESCE(starts_at, due_at, created_at), id
            LIMIT 200
            """,
            (int(teacher_id), (now - timedelta(days=14)).isoformat(timespec="minutes")),
        ).fetchall()
    except sqlite3.Error:
        return []

    try:
        teacher_name_row = conn.execute(
            "SELECT name FROM teachers WHERE id = ? LIMIT 1", (int(teacher_id),)
        ).fetchone()
        teacher_name = str(teacher_name_row["name"] or "") if teacher_name_row else ""
    except sqlite3.Error:
        teacher_name = ""

    events: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        when = _dashboard_parse_datetime(
            item.get("starts_at") or item.get("due_at") or item.get("created_at")
        )
        if when is None:
            continue
        source_type = str(item.get("source_type") or "")
        if source_type == "academic_invigilation":
            kind = "invigilation"
        elif source_type in {"academic_course_exam", "academic_exam"}:
            kind = "exam"
        else:
            kind = "todo"

        if kind in {"invigilation", "exam"}:
            # Structured, de-cluttered detail (科目/日期/时间/教室/校区/监考分工)
            # reused by the popover and the email reminder. Pass conn + teacher
            # name so the two invigilators and "我的角色" resolve even for events
            # synced before the metadata carried them.
            detail = build_event_reminder_detail(item, conn=conn, teacher_name=teacher_name)
            title = detail["subject"]
            # Concise list line: place + time (subject already in the title).
            place = " ".join(part for part in (detail["campus"], detail["classroom"]) if part)
            subtitle = " · ".join(part for part in (place, detail["time_label"]) if part)
            event = _dashboard_agenda_event(
                kind=kind,
                when=when,
                title=title,
                subtitle=subtitle,
                href=item.get("link_url") or "/dashboard#dashboard-semester",
                today=today,
            )
            event["event_id"] = _dashboard_int(item.get("id"))
            event["can_email_reminder"] = True
            event["detail"] = {
                "subject": detail["subject"],
                "date_label": detail["date_label"],
                "time_label": detail["time_label"],
                "campus": detail["campus"],
                "classroom": detail["classroom"],
                "teaching_class": detail["teaching_class"],
                "invigilators": detail["invigilators"],
                "role": detail["role"],
            }
            events.append(event)
            continue

        metadata = _dashboard_safe_json(item.get("metadata_json"))
        subtitle = " · ".join(
            part
            for part in (
                str(metadata.get("course_name") or "").strip(),
                str(metadata.get("teaching_class_name") or "").strip(),
                str(item.get("location") or "").strip(),
            )
            if part
        )
        title = re.sub(r"^教务(?:考试|监考)[：:]\s*", "", str(item.get("title") or "")).strip()
        events.append(
            _dashboard_agenda_event(
                kind=kind,
                when=when,
                title=title,
                subtitle=subtitle,
                href=item.get("link_url") or "/dashboard#dashboard-semester",
                today=today,
            )
        )
    return events


def _build_agenda_events_from_todos(
    todo_items: list[dict[str, Any]],
    *,
    today: date,
    now: datetime,
) -> list[dict[str, Any]]:
    """Exam / assignment / lesson / manual-todo events from a todo overview."""
    del now
    events: list[dict[str, Any]] = []
    for item in todo_items or []:
        if item.get("is_completed"):
            continue
        source_type = str(item.get("source_type") or "")
        kind = _AGENDA_TODO_KIND_BY_SOURCE.get(source_type)
        if kind is None:
            kind = "todo" if item.get("is_manual") else None
        if kind is None:
            continue
        if kind in {"class", "exam"}:
            raw = item.get("start_at") or item.get("effective_start_at") or item.get("due_at")
        else:
            raw = item.get("due_at") or item.get("effective_end_at") or item.get("effective_start_at")
        when = _dashboard_parse_datetime(raw)
        if when is None:
            continue
        if item.get("no_deadline") and kind not in {"class", "exam"}:
            continue
        subtitle = " · ".join(
            part
            for part in (
                str(item.get("offering_label") or "").strip(),
                str(item.get("subtitle") or "").strip(),
            )
            if part
        )
        events.append(
            _dashboard_agenda_event(
                kind=kind,
                when=when,
                title=item.get("title"),
                subtitle=subtitle,
                href=item.get("link_url") or "#",
                today=today,
            )
        )
    return events


def build_dashboard_context(
    conn,
    user: dict,
    *,
    initial_filter: Any = None,
    initial_search: Any = None,
) -> dict[str, Any]:
    role = str(user.get("role") or "").strip().lower()
    if role == "teacher":
        return _build_teacher_dashboard_context(
            conn,
            user,
            initial_filter=initial_filter,
            initial_search=initial_search,
        )
    return _build_student_dashboard_context(
        conn,
        user,
        initial_filter=initial_filter,
        initial_search=initial_search,
    )


def _build_teacher_dashboard_context(
    conn,
    user: dict,
    *,
    initial_filter: Any = None,
    initial_search: Any = None,
) -> dict[str, Any]:
    teacher_id = int(user["id"])
    offerings = _load_teacher_offerings(conn, teacher_id)
    offering_ids = [int(item["id"]) for item in offerings]
    course_ids = sorted({int(item["course_id"]) for item in offerings})

    assignment_stats = _load_teacher_assignment_stats(conn, offering_ids)
    pending_submission_stats = _load_teacher_pending_submission_stats(conn, offering_ids)
    resource_stats = _load_course_resource_stats(conn, course_ids, include_teacher_resources=True)
    material_stats = _load_offering_material_stats(conn, offering_ids)
    recent_login_stats = _load_teacher_recent_login_stats(conn, [int(item["class_id"]) for item in offerings])
    recent_activity = _load_recent_activity(conn, user)
    message_summary = get_message_center_summary(conn, user)
    unread_total = int(message_summary.get("unread_total") or 0)
    unique_student_count = _query_scalar(
        conn,
        """
        SELECT COUNT(DISTINCT s.id)
        FROM students s
        JOIN (
            SELECT DISTINCT class_id
            FROM class_offerings
            WHERE teacher_id = ?
        ) active_classes ON active_classes.class_id = s.class_id
        WHERE COALESCE(s.enrollment_status, 'active') = 'active'
        """,
        (teacher_id,),
    )
    today_login_count = _query_scalar(conn, _teacher_today_login_count_sql(), (teacher_id,))
    pending_reset_count = _query_scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM student_password_reset_requests r
        JOIN classes c ON c.id = r.class_id
        WHERE r.teacher_id = ?
          AND c.created_by_teacher_id = ?
          AND r.status = 'pending'
        """,
        (teacher_id, teacher_id),
    )

    enriched_offerings: list[dict[str, Any]] = []
    pending_review_total = 0
    draft_total = 0
    attention_count = 0
    recent_count = 0

    for offering in offerings:
        offering_id = int(offering["id"])
        course_id = int(offering["course_id"])
        assignment_item = assignment_stats.get(offering_id, {})
        pending_item = pending_submission_stats.get(offering_id, {})
        resource_item = resource_stats.get(course_id, {})
        material_item = material_stats.get(offering_id, {})
        login_item = recent_login_stats.get(int(offering["class_id"]), {})

        student_count = int(offering.get("student_count") or 0)
        assignment_count = int(assignment_item.get("assignment_count") or 0)
        draft_count = int(assignment_item.get("draft_count") or 0)
        published_count = int(assignment_item.get("published_count") or 0)
        exam_count = int(assignment_item.get("exam_count") or 0)
        pending_review_count = int(pending_item.get("pending_review_count") or 0)
        grading_count = int(pending_item.get("grading_count") or 0)
        recent_active_student_count = int(login_item.get("recent_active_student_count") or 0)
        recent_login_count = int(login_item.get("recent_login_count") or 0)
        resource_count = int(resource_item.get("resource_count") or 0)
        material_count = int(material_item.get("material_count") or 0)
        resource_total = resource_count + material_count
        class_department = _normalize_dashboard_group_label(offering.get("class_department"))
        course_department = _normalize_dashboard_group_label(offering.get("course_department"))
        department_label = class_department if class_department != "未分类" else course_department
        last_activity_at = _pick_latest_datetime(
            offering.get("created_at"),
            assignment_item.get("latest_assignment_at"),
            pending_item.get("latest_submission_at"),
            resource_item.get("latest_resource_at"),
            material_item.get("latest_material_at"),
            login_item.get("latest_login_at"),
        )
        needs_attention = pending_review_count > 0 or draft_count > 0
        has_recent_activity = _is_recent(last_activity_at)
        activity_score = _build_teacher_activity_score(
            recent_active_student_count=recent_active_student_count,
            recent_login_count=recent_login_count,
            pending_review_count=pending_review_count,
            draft_count=draft_count,
            last_activity_at=last_activity_at,
        )

        pending_review_total += pending_review_count
        draft_total += draft_count
        attention_count += 1 if needs_attention else 0
        recent_count += 1 if has_recent_activity else 0

        badges = []
        if pending_review_count > 0:
            badges.append({"label": f"待批改 {pending_review_count}", "tone": "danger"})
        if grading_count > 0:
            badges.append({"label": f"批改中 {grading_count}", "tone": "warning"})
        if draft_count > 0:
            badges.append({"label": f"草稿 {draft_count}", "tone": "warning"})
        if published_count > 0:
            badges.append({"label": f"已发布 {published_count}", "tone": "success"})
        if exam_count > 0:
            badges.append({"label": f"考试 {exam_count}", "tone": "neutral"})

        meta = [
            item
            for item in [
                offering.get("semester"),
                offering.get("schedule_info"),
                f"{student_count} 名学生" if student_count else "待导入学生",
            ]
            if item
        ]

        description = (
            str(offering.get("course_description") or "").strip()
            or str(offering.get("class_description") or "").strip()
            or "从这里继续管理作业、考试、课程资料与课堂互动。"
        )

        if pending_review_count > 0:
            summary = f"当前有 {pending_review_count} 份学生提交等待处理。"
        elif grading_count > 0:
            summary = f"当前有 {grading_count} 份学生提交正在批改中。"
        elif draft_count > 0:
            summary = f"还有 {draft_count} 项草稿未发布，课堂内容可以继续补齐。"
        elif assignment_count > 0:
            summary = f"当前共配置 {assignment_count} 项课堂任务，课堂结构已经成型。"
        else:
            summary = "建议优先补充任务与资料，让学生进入课堂后立即可用。"

        offering["summary"] = summary
        offering["description"] = description
        offering["meta"] = meta
        offering["badges"] = badges
        offering["class_department_label"] = class_department
        offering["course_department_label"] = course_department
        offering["department_label"] = department_label
        offering["resource_total"] = resource_total
        offering["resource_count"] = resource_count
        offering["material_count"] = material_count
        offering["assignment_count"] = assignment_count
        offering["draft_count"] = draft_count
        offering["exam_count"] = exam_count
        offering["pending_review_count"] = pending_review_count
        offering["grading_count"] = grading_count
        offering["recent_active_student_count"] = recent_active_student_count
        offering["recent_login_count"] = recent_login_count
        offering["activity_score"] = activity_score
        offering["last_activity_sort"] = _dashboard_datetime_sort_value(last_activity_at)
        offering["last_activity_at"] = last_activity_at or ""
        offering["needs_attention"] = needs_attention
        offering["has_recent_activity"] = has_recent_activity
        offering["has_progress"] = assignment_count > 0 or resource_total > 0
        offering["metrics"] = [
            {"label": "学生", "value": student_count, "note": "班级规模"},
            {"label": "任务", "value": assignment_count, "note": f"考试 {exam_count}"},
            {"label": "待批改", "value": pending_review_count, "note": f"批改中 {grading_count}"},
            {"label": "资料", "value": resource_total, "note": f"文件 {resource_count} · 材料 {material_count}"},
        ]
        offering["search_text"] = _build_dashboard_search_text(
            offering.get("course_name"),
            offering.get("class_name"),
            department_label,
            class_department,
            course_department,
            offering.get("semester"),
            offering.get("schedule_info"),
            description,
            summary,
            *meta,
            *(badge.get("label") for badge in badges),
            *(f"{metric['label']} {metric['value']} {metric['note']}" for metric in offering["metrics"]),
            f"近{RECENT_ACTIVITY_DAYS}天活跃学生 {recent_active_student_count}",
            f"近{RECENT_ACTIVITY_DAYS}天登录 {recent_login_count}",
        )
        enriched_offerings.append(offering)

    _attach_teacher_timeline_items(conn, enriched_offerings)
    enriched_offerings.sort(
        key=lambda item: (
            -_dashboard_int(item.get("activity_score")),
            -_dashboard_int(item.get("recent_active_student_count")),
            -_dashboard_int(item.get("recent_login_count")),
            -_dashboard_int(item.get("last_activity_sort")),
            _dashboard_sort_text(item.get("department_label")),
            _dashboard_sort_text(item.get("class_name")),
            _dashboard_sort_text(item.get("course_name")),
            -_dashboard_int(item.get("pending_review_count")),
            -_dashboard_int(item.get("draft_count")),
            -_dashboard_int(item.get("id")),
        )
    )

    distinct_class_count = len({int(item["class_id"]) for item in offerings})
    distinct_course_count = len({int(item["course_id"]) for item in offerings})
    ui_copy = render_ui_copy_block(
        get_ui_copy_block(conn, scene="dashboard", role="teacher"),
        {
            "name": polite_address(user.get("name") or "", "teacher"),
            "unread_total": unread_total,
            "pending_reset_count": pending_reset_count,
            "today_login_count": today_login_count,
        },
    )

    spotlight = {
        "label": ui_copy["spotlight_pending_label"],
        "value": pending_review_total,
        "suffix": "份",
        "note": ui_copy["spotlight_pending_note"],
    }
    if pending_review_total <= 0 and pending_reset_count > 0:
        spotlight = {
            "label": ui_copy["spotlight_reset_label"],
            "value": pending_reset_count,
            "suffix": "条",
            "note": ui_copy["spotlight_reset_note"],
        }
    elif pending_review_total <= 0 and unread_total > 0:
        spotlight = {
            "label": ui_copy["spotlight_unread_label"],
            "value": unread_total,
            "suffix": "条",
            "note": ui_copy["spotlight_unread_note"],
        }
    elif pending_review_total <= 0:
        spotlight = {
            "label": ui_copy["spotlight_login_label"],
            "value": today_login_count,
            "suffix": "次",
            "note": ui_copy["spotlight_login_note"],
        }

    quick_actions = [
        {
            "mode": "link",
            "label": ui_copy["action_offering_label"],
            "description": ui_copy["action_offering_description"],
            "href": canonical_manage_href("offerings"),
            "badge": None,
        },
        {
            "mode": "link",
            "label": ui_copy["action_materials_label"],
            "description": ui_copy["action_materials_description"],
            "href": canonical_manage_href("materials"),
            "badge": None,
        },
        {
            "mode": "link",
            "label": ui_copy["action_exams_label"],
            "description": ui_copy["action_exams_description"],
            "href": canonical_manage_href("exams"),
            "badge": None,
        },
        {
            "mode": "link",
            "label": ui_copy["action_system_label"],
            "description": ui_copy["action_system_description"],
            "href": canonical_manage_href("system_password_resets"),
            "badge": pending_reset_count or None,
        },
    ]

    focus_items = _load_teacher_academic_focus_items(conn, teacher_id=teacher_id, limit=3)
    try:
        from .gongwen_follow_service import count_unseen_follow_hits

        follow_unseen_count = count_unseen_follow_hits(conn, teacher_id)
    except Exception:  # noqa: BLE001 — 关注模块异常不能拖垮首页
        follow_unseen_count = 0
    if follow_unseen_count > 0:
        focus_items.insert(0, {
            "title": "您的关注：公文命中提醒",
            "description": f"有 {follow_unseen_count} 篇新公文命中了你的关注项目或关键字，点击查看。",
            "href": f"{canonical_manage_href('gongwen')}?follow=1",
            "tone": "primary",
        })
    if pending_reset_count > 0:
        focus_items.append({
            "title": "学生找回密码审核",
            "description": f"当前有 {pending_reset_count} 条申请待处理。",
            "href": canonical_manage_href("system_password_resets"),
            "tone": "danger",
        })
    if unread_total > 0:
        focus_items.append({
            "title": "消息中心未读提醒",
            "description": f"还有 {unread_total} 条通知未读，建议及时回看课堂互动。",
            "href": "/message-center",
            "tone": "primary",
        })

    for offering in sorted(
        enriched_offerings,
        key=lambda item: (
            -int(item.get("pending_review_count") or 0),
            -int(item.get("draft_count") or 0),
            -int(bool(item.get("has_recent_activity"))),
            -int(item.get("id") or 0),
        ),
    ):
        if not offering["needs_attention"]:
            continue
        fragments = []
        if offering["pending_review_count"] > 0:
            fragments.append(f"{offering['pending_review_count']} 份待批改")
        if offering["draft_count"] > 0:
            fragments.append(f"{offering['draft_count']} 项草稿")
        focus_items.append({
            "title": f"{offering['class_name']} · {offering['course_name']}",
            "description": "，".join(fragments) or "课堂内容仍可继续完善。",
            "href": f"/classroom/{offering['id']}",
            "tone": "warning",
        })
        if len(focus_items) >= 5:
            break

    if not focus_items:
        focus_items.append({
            "title": ui_copy["focus_empty_title"],
            "description": ui_copy["focus_empty_description"],
            "href": canonical_manage_href("materials") if offerings else canonical_manage_href("offerings"),
            "tone": "neutral",
        })

    dashboard_filters = [
        {"value": "all", "label": "全部", "count": len(offerings)},
        {"value": "attention", "label": "待处理", "count": attention_count},
        {"value": "recent", "label": "近期活跃", "count": recent_count},
    ]
    selected_filter = _normalize_dashboard_filter("teacher", initial_filter)
    search_query = _normalize_dashboard_search(initial_search)
    initial_visible_count = _apply_dashboard_view_state(
        enriched_offerings,
        filter_value=selected_filter,
        search_query=search_query,
    )
    initial_results_summary = _build_dashboard_results_summary(
        dashboard_filters,
        filter_value=selected_filter,
        search_query=search_query,
    )
    semester_calendar = build_semester_calendar_payload(
        load_teacher_semester_rows(conn, teacher_id),
    )
    _attach_dashboard_todos_to_semester_calendar(
        conn,
        semester_calendar,
        offerings,
        user,
    )

    return {
        "dashboard_theme": "teacher",
        "dashboard_hero": {
            "eyebrow": ui_copy["hero_eyebrow"],
            "title": ui_copy["hero_title"],
            "subtitle": ui_copy["hero_subtitle"],
            "chips": [
                f"{distinct_course_count} 门课程模板",
                f"{distinct_class_count} 个班级",
                f"今日登录 {today_login_count} 次",
            ],
            "spotlight": spotlight,
        },
        "dashboard_stats": [
            {"label": "活跃课堂", "value": len(offerings), "note": "可直接进入的教学空间"},
            {"label": "覆盖学生", "value": unique_student_count, "note": f"{distinct_class_count} 个班级"},
            {"label": "待批改", "value": pending_review_total, "note": f"草稿 {draft_total} 项"},
            {"label": "未读提醒", "value": unread_total, "note": f"待审核 {pending_reset_count} 条"},
        ],
        "dashboard_quick_actions": quick_actions,
        "dashboard_domain_cards": build_dashboard_domain_cards(),
        "dashboard_sections": {
            "quick_actions": {
                "title": ui_copy["quick_actions_title"],
                "subtitle": ui_copy["quick_actions_subtitle"],
            },
        },
        "dashboard_focus": {
            "title": ui_copy["focus_title"],
            "subtitle": ui_copy["focus_subtitle"],
            "items": focus_items[:5],
        },
        "dashboard_activity": {
            "title": ui_copy["activity_title"],
            "subtitle": ui_copy["activity_subtitle"],
            "items": recent_activity,
        },
        "dashboard_filters": dashboard_filters,
        "dashboard_search_placeholder": "搜索课程、班级或学期",
        "dashboard_initial_filter": selected_filter,
        "dashboard_initial_search": search_query,
        "dashboard_initial_visible_count": initial_visible_count,
        "dashboard_initial_results_summary": initial_results_summary,
        "dashboard_default_group_mode": "department",
        "dashboard_recent_activity_days": RECENT_ACTIVITY_DAYS,
        "dashboard_empty_state": {
            "title": ui_copy["empty_title"],
            "description": ui_copy["empty_description"],
            "action_label": ui_copy["empty_action_label"],
            "action_href": canonical_manage_href("offerings"),
        },
        "class_offerings": enriched_offerings,
        "dashboard_semester_calendar": semester_calendar,
        "dashboard_agenda_events": _build_teacher_calendar_agenda_events(
            conn,
            teacher_id=teacher_id,
            today=china_today(),
            now=china_now().replace(tzinfo=None),
        ),
        "dashboard_student_cockpit": None,
        "student_security_summary": None,
    }


def _format_dashboard_metric_value(value: Any, *, suffix: str = "") -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return f"0{suffix}"
    if number.is_integer():
        return f"{int(number)}{suffix}"
    return f"{number:.1f}{suffix}"


def _clamp_dashboard_percent(value: Any) -> int:
    try:
        percent = float(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, int(round(percent))))


def _student_material_progress_percent(item: dict[str, Any]) -> int:
    if int(item.get("completed") or 0):
        return 100
    try:
        scroll_percent = float(item.get("max_scroll_ratio") or 0) * 100
    except (TypeError, ValueError):
        scroll_percent = 0
    active_percent = int(item.get("active_seconds") or 0) / 180 * 100
    total_percent = int(item.get("accumulated_seconds") or 0) / 300 * 100
    return _clamp_dashboard_percent(max(scroll_percent, active_percent, total_percent))


def _student_material_viewer_href(item: dict[str, Any]) -> str:
    material_id = _dashboard_int(item.get("material_id"))
    class_offering_id = _dashboard_int(item.get("class_offering_id"))
    session_id = _dashboard_int(item.get("session_id"))
    if not material_id:
        return "/dashboard"
    query = []
    if class_offering_id:
        query.append(f"class_offering_id={class_offering_id}")
    if session_id:
        query.append(f"session_id={session_id}")
    suffix = f"?{'&'.join(query)}" if query else ""
    return f"/materials/view/{material_id}{suffix}"


def _load_student_continue_material(
    conn: sqlite3.Connection,
    *,
    student_id: int,
    offering_ids: list[int],
) -> dict[str, Any] | None:
    if not offering_ids:
        return None
    placeholders = ",".join("?" for _ in offering_ids)
    rows = conn.execute(
        f"""
        WITH assigned_materials AS (
            SELECT o.id AS class_offering_id,
                   c.name AS course_name,
                   cl.name AS class_name,
                   m.id AS material_id,
                   m.name AS material_name,
                   s.id AS session_id,
                   COALESCE(s.order_index, 0) AS order_index,
                   1 AS source_rank
            FROM class_offerings o
            JOIN courses c ON c.id = o.course_id
            JOIN classes cl ON cl.id = o.class_id
            JOIN class_offering_sessions s ON s.class_offering_id = o.id
            JOIN course_materials m ON m.id = s.learning_material_id
            WHERE o.id IN ({placeholders})
              AND m.node_type = 'file'
            UNION ALL
            SELECT o.id AS class_offering_id,
                   c.name AS course_name,
                   cl.name AS class_name,
                   m.id AS material_id,
                   m.name AS material_name,
                   NULL AS session_id,
                   90000 AS order_index,
                   2 AS source_rank
            FROM class_offerings o
            JOIN courses c ON c.id = o.course_id
            JOIN classes cl ON cl.id = o.class_id
            JOIN course_material_assignments cma ON cma.class_offering_id = o.id
            JOIN course_materials m ON m.id = cma.material_id
            WHERE o.id IN ({placeholders})
              AND m.node_type = 'file'
            UNION ALL
            SELECT o.id AS class_offering_id,
                   c.name AS course_name,
                   cl.name AS class_name,
                   m.id AS material_id,
                   m.name AS material_name,
                   NULL AS session_id,
                   99999 AS order_index,
                   3 AS source_rank
            FROM class_offerings o
            JOIN courses c ON c.id = o.course_id
            JOIN classes cl ON cl.id = o.class_id
            JOIN course_materials m ON m.id = o.home_learning_material_id
            WHERE o.id IN ({placeholders})
              AND m.node_type = 'file'
        )
        SELECT assigned_materials.*,
               lmp.completed,
               lmp.max_scroll_ratio,
               lmp.active_seconds,
               lmp.accumulated_seconds,
               lmp.last_viewed_at,
               lmp.updated_at
        FROM assigned_materials
        LEFT JOIN learning_material_progress lmp
               ON lmp.class_offering_id = assigned_materials.class_offering_id
              AND lmp.material_id = assigned_materials.material_id
              AND lmp.student_id = ?
        WHERE COALESCE(lmp.completed, 0) = 0
        ORDER BY
            CASE WHEN lmp.last_viewed_at IS NOT NULL AND lmp.last_viewed_at != '' THEN 0 ELSE 1 END,
            COALESCE(lmp.last_viewed_at, lmp.updated_at, '') DESC,
            assigned_materials.source_rank ASC,
            assigned_materials.order_index ASC,
            assigned_materials.material_name COLLATE NOCASE
        LIMIT 1
        """,
        (*offering_ids, *offering_ids, *offering_ids, int(student_id)),
    ).fetchall()
    if not rows:
        return None
    item = dict(rows[0])
    item["progress_percent"] = _student_material_progress_percent(item)
    item["href"] = _student_material_viewer_href(item)
    return item


def _load_student_dashboard_todo_overviews(
    conn: sqlite3.Connection,
    offerings: list[dict[str, Any]],
    user: dict[str, Any],
) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
    overviews: dict[int, dict[str, Any]] = {}
    items: list[dict[str, Any]] = []
    for offering in offerings:
        class_offering_id = _dashboard_int(offering.get("id"))
        if not class_offering_id:
            continue
        try:
            overview = build_classroom_todo_overview(
                conn,
                class_offering_id=class_offering_id,
                user=user,
            )
        except Exception:
            continue
        overviews[class_offering_id] = overview
        items.extend(
            _enrich_dashboard_todo(item, offering)
            for item in overview.get("items", [])
            if isinstance(item, dict)
        )
    items.sort(key=_dashboard_todo_sort_key)
    return overviews, items


def _student_cockpit_step_kind(item: dict[str, Any]) -> str:
    explicit_kind = str(item.get("kind") or "").strip()
    if explicit_kind in {"exam", "assignment", "stage", "lesson", "manual", "material", "review", "message", "learning"}:
        return explicit_kind
    source_type = str(item.get("source_type") or "").strip()
    if source_type == "stage_exam":
        return "stage"
    if source_type == "lesson":
        return "lesson"
    if source_type == "manual":
        return "manual"
    if source_type == "material":
        return "material"
    if source_type == "review":
        return "review"
    if bool((item.get("metadata") or {}).get("is_exam")):
        return "exam"
    text = f"{item.get('title') or ''} {item.get('description') or ''} {item.get('subtitle') or ''} {item.get('href') or item.get('link_url') or ''}"
    if "考试" in text or "exam" in text:
        return "exam"
    if "作业" in text or "/assignment/" in text:
        return "assignment"
    if "消息" in text or "提醒" in text or "message-center" in text:
        return "message"
    return "learning"


def _student_cockpit_action_label(kind: str, tone: str) -> str:
    if kind == "exam":
        return "进入考试"
    if kind == "assignment":
        return "开始处理"
    if kind == "stage":
        return "挑战试炼"
    if kind == "material":
        return "继续阅读"
    if kind == "review":
        return "去复盘"
    if kind == "message":
        return "查看消息"
    if kind == "lesson":
        return "进入课堂"
    if tone in {"danger", "warning"}:
        return "马上处理"
    return "继续学习"


def _student_cockpit_plan_label(kind: str) -> str:
    return {
        "exam": "考试",
        "assignment": "作业",
        "stage": "破境",
        "lesson": "上课",
        "manual": "待办",
        "material": "阅读",
        "review": "复盘",
        "message": "消息",
    }.get(kind, "学习")


def _student_cockpit_todo_plan_item(item: dict[str, Any], *, now: datetime) -> dict[str, Any] | None:
    is_completed = bool(item.get("is_completed"))
    due_at = _dashboard_parse_datetime(item.get("due_at") or item.get("effective_end_at"))
    start_at = _dashboard_parse_datetime(item.get("start_at") or item.get("effective_start_at"))
    due_date = due_at.date() if due_at else None
    start_date = start_at.date() if start_at else None
    today = now.date()
    kind = _student_cockpit_step_kind(item)
    if is_completed and due_date != today and start_date != today:
        return None

    if not is_completed and kind == "lesson" and (start_date == today or due_date == today):
        priority = 2
        tone = "primary"
        label = "今天上课"
    elif not is_completed and due_at and due_at < now:
        priority = 0
        tone = "danger"
        label = "已超时"
    elif not is_completed and due_date == today:
        priority = 1
        tone = "warning" if kind in {"assignment", "exam"} else "primary"
        label = "今天截止" if kind in {"assignment", "exam", "manual"} else "今天"
    elif not is_completed and start_date == today:
        priority = 2
        tone = "primary"
        label = "今天开始"
    elif not is_completed and kind == "stage":
        priority = 3
        tone = "success"
        label = "个人试炼"
    elif not is_completed and due_at and due_at <= now + timedelta(days=7):
        priority = 4
        tone = "warning"
        label = "本周截止"
    elif is_completed:
        priority = 8
        tone = "success"
        label = "已完成"
    else:
        return None

    href = str(item.get("link_url") or item.get("href") or "").strip()
    if not href and _dashboard_int(item.get("class_offering_id")):
        href = f"/classroom/{_dashboard_int(item.get('class_offering_id'))}#timeline-panel"
    due_label = str(item.get("relative_due_label") or item.get("deadline_label") or "").strip()
    course_name = str(item.get("course_name") or "").strip()
    class_name = str(item.get("class_name") or "").strip()
    context = " · ".join(part for part in [course_name, class_name] if part)
    return {
        "kind": kind,
        "label": label,
        "title": str(item.get("title") or "待处理事项"),
        "description": context or str(item.get("subtitle") or "进入后查看完整内容。"),
        "href": href or "/dashboard#dashboard-semester",
        "tone": tone,
        "due_label": due_label,
        "priority": priority,
        "is_completed": is_completed,
    }


def _student_cockpit_material_plan_item(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not item:
        return None
    progress_percent = _clamp_dashboard_percent(item.get("progress_percent"))
    return {
        "kind": "material",
        "label": "继续阅读" if progress_percent > 0 else "开始阅读",
        "title": str(item.get("material_name") or "继续学习材料"),
        "description": " · ".join(
            part
            for part in [str(item.get("course_name") or ""), f"已读 {progress_percent}%"]
            if part
        ),
        "href": str(item.get("href") or _student_material_viewer_href(item)),
        "tone": "primary",
        "due_label": "上次进度" if progress_percent > 0 else "推荐起点",
        "priority": 5 if progress_percent > 0 else 6,
        "is_completed": False,
    }


def _student_cockpit_message_plan_item(unread_total: int) -> dict[str, Any] | None:
    if unread_total <= 0:
        return None
    return {
        "kind": "message",
        "label": "未读提醒",
        "title": f"有 {unread_total} 条消息需要查看",
        "description": "通知、私信和教师提醒都在消息中心。",
        "href": "/message-center",
        "tone": "primary",
        "due_label": "消息中心",
        "priority": 7,
        "is_completed": False,
    }


def _student_cockpit_review_plan_item(review_summary: dict[str, Any] | None) -> dict[str, Any] | None:
    if not review_summary:
        return None
    open_count = _dashboard_int(review_summary.get("open_count"))
    if open_count <= 0:
        return None
    high_count = _dashboard_int(review_summary.get("high_count"))
    return {
        "kind": "review",
        "label": "反馈复盘",
        "title": f"复盘 {open_count} 个反馈点",
        "description": str(
            review_summary.get("description")
            or "把最近批改里的扣分点转成自己的检查动作。"
        ),
        "href": str(review_summary.get("href") or "/feedback-review"),
        "tone": "danger" if high_count else "warning",
        "due_label": "优先错题" if high_count else "错题本",
        "priority": 6,
        "is_completed": False,
    }


def _build_student_today_plan(
    *,
    todo_items: list[dict[str, Any]],
    continue_material: dict[str, Any] | None,
    review_summary: dict[str, Any] | None,
    unread_total: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or china_now().replace(tzinfo=None)
    plan_items = [
        item
        for item in (
            _student_cockpit_todo_plan_item(todo, now=now)
            for todo in todo_items
        )
        if item
    ]
    material_item = _student_cockpit_material_plan_item(continue_material)
    if material_item:
        plan_items.append(material_item)
    review_item = _student_cockpit_review_plan_item(review_summary)
    if review_item:
        plan_items.append(review_item)
    message_item = _student_cockpit_message_plan_item(unread_total)
    if message_item:
        plan_items.append(message_item)

    plan_items.sort(
        key=lambda item: (
            1 if item.get("is_completed") else 0,
            int(item["priority"]) if item.get("priority") is not None else 99,
            str(item.get("title") or ""),
        )
    )
    total_count = len(plan_items)
    completed_count = sum(1 for item in plan_items if item.get("is_completed"))
    open_count = max(0, total_count - completed_count)
    overdue_count = sum(1 for item in plan_items if item.get("label") == "已超时")
    due_soon_count = sum(
        1
        for item in plan_items
        if item.get("label") in {"今天截止", "本周截止", "已超时"}
        and not item.get("is_completed")
    )
    completion_percent = 100 if total_count == 0 else _clamp_dashboard_percent(completed_count / total_count * 100)
    if total_count == 0:
        title = "今天没有硬性任务"
        description = "适合翻一段材料、看看讨论，或者提前处理下一周的任务。"
        label = "轻松日"
    elif open_count == 0:
        title = "今天已经收尾"
        description = "当前计划项都已处理，可以进入课堂复盘或预习下一节。"
        label = "已完成"
    elif overdue_count:
        title = f"先补 {overdue_count} 项超时任务"
        description = "把最紧急的事项先清掉，再回到材料和讨论。"
        label = "需要处理"
    else:
        title = f"今天还有 {open_count} 项可推进"
        description = "按下面的优先级走，先截止任务，再继续材料和消息。"
        label = "进行中"
    return {
        "items": plan_items,
        "actionable_items": [item for item in plan_items if not item.get("is_completed")],
        "total_count": total_count,
        "completed_count": completed_count,
        "open_count": open_count,
        "overdue_count": overdue_count,
        "due_soon_count": due_soon_count,
        "completion_percent": completion_percent,
        "label": label,
        "title": title,
        "description": description,
    }


def _student_cockpit_greeting(now: datetime, student_name: str) -> str:
    hour = now.hour
    if 5 <= hour < 11:
        prefix = "早上好"
    elif 11 <= hour < 14:
        prefix = "中午好"
    elif 14 <= hour < 18:
        prefix = "下午好"
    else:
        prefix = "晚上好"
    name = str(student_name or "").strip()
    return f"{prefix}，{name}" if name else f"{prefix}，今天从这里开始"


def _student_cockpit_day_shape(todo_items: list[dict[str, Any]], *, now: datetime, open_count: int) -> str:
    today = now.date()
    class_count = 0
    due_count = 0
    for item in todo_items:
        if item.get("is_completed"):
            continue
        kind = _student_cockpit_step_kind(item)
        starts_at = _dashboard_parse_datetime(item.get("start_at") or item.get("effective_start_at"))
        due_at = _dashboard_parse_datetime(item.get("due_at") or item.get("effective_end_at"))
        if kind == "lesson" and (
            (starts_at and starts_at.date() == today)
            or (due_at and due_at.date() == today)
        ):
            class_count += 1
        elif due_at and due_at.date() == today:
            due_count += 1
    fragments = [f"今天还有 {class_count} 节课", f"{due_count} 项截止"]
    summary = " · ".join(fragments)
    if now.hour >= 22 and open_count > 0:
        summary += " · 夜深了，剩余事项已为你保留到明天清单"
    return summary


def _build_student_continue_action(offerings: list[dict[str, Any]]) -> dict[str, str]:
    recent_candidates = [
        item
        for item in offerings
        if _dashboard_int(item.get("last_activity_sort")) > 0
    ]
    selected: dict[str, Any] | None = None
    if recent_candidates:
        selected = max(
            recent_candidates,
            key=lambda item: (
                _dashboard_int(item.get("last_activity_sort")),
                _dashboard_int(item.get("id")),
            ),
        )
    else:
        pending_candidates = [
            item
            for item in offerings
            if _dashboard_int(item.get("pending_count")) > 0
        ]
        if pending_candidates:
            selected = sorted(
                pending_candidates,
                key=lambda item: (
                    -_dashboard_int(item.get("pending_count")),
                    _dashboard_sort_text(item.get("course_name")),
                    -_dashboard_int(item.get("id")),
                ),
            )[0]

    if not selected:
        return {
            "href": "#dashboard-class-list",
            "title": "查看课堂列表",
            "label": "查看课堂",
            "subtitle": "课堂列表",
            "course_name": "",
        }

    course_name = str(selected.get("course_name") or "课堂").strip() or "课堂"
    return {
        "href": f"/classroom/{_dashboard_int(selected.get('id'))}",
        "title": f"继续学习：{course_name}",
        "label": "继续学习",
        "subtitle": f"继续 · {course_name}",
        "course_name": course_name,
    }


def _build_student_cockpit(
    *,
    offerings: list[dict[str, Any]],
    priority_items: list[dict[str, Any]],
    cultivation_profile: dict[str, Any],
    todo_items: list[dict[str, Any]],
    continue_material: dict[str, Any] | None,
    review_summary: dict[str, Any] | None,
    pending_total: int,
    submitted_total: int,
    unread_total: int,
    student_name: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or china_now().replace(tzinfo=None)
    best_course = cultivation_profile.get("best_course") or {}
    highest_level = cultivation_profile.get("highest_level") or {}
    today_plan = _build_student_today_plan(
        todo_items=todo_items,
        continue_material=continue_material,
        review_summary=review_summary,
        unread_total=unread_total,
        now=now,
    )
    primary_source = (
        today_plan["actionable_items"][0]
        if today_plan["actionable_items"]
        else (priority_items[0] if priority_items else None)
    )
    primary_kind = _student_cockpit_step_kind(primary_source or {})
    primary_tone = str((primary_source or {}).get("tone") or "neutral")
    fallback_href = (
        f"/classroom/{best_course['class_offering_id']}"
        if best_course.get("class_offering_id")
        else (f"/classroom/{offerings[0]['id']}" if offerings else "/message-center")
    )
    primary = {
        "eyebrow": "先做这件事" if today_plan["open_count"] else "保持节奏",
        "title": str((primary_source or {}).get("title") or "回到课堂继续学习"),
        "description": str(
            (primary_source or {}).get("description")
            or _dashboard_notice_text(best_course.get("rank_notice"), fallback="从最近的课堂进入，补齐资料、作业与讨论。")
        ),
        "href": str((primary_source or {}).get("href") or (primary_source or {}).get("link_url") or fallback_href),
        "label": _student_cockpit_action_label(primary_kind, primary_tone),
        "tone": primary_tone,
        "meta": str((primary_source or {}).get("due_label") or "系统已按截止、进度和提醒排序"),
        "kind": primary_kind,
    }

    stats = [
        {
            "label": "今日计划",
            "value": str(today_plan["total_count"]),
            "hint": f"待推进 {today_plan['open_count']}",
            "tone": "danger" if today_plan["overdue_count"] else ("warning" if today_plan["open_count"] else "success"),
        },
        {
            "label": "已完成",
            "value": str(today_plan["completed_count"]),
            "hint": f"完成度 {today_plan['completion_percent']}%",
            "tone": "success" if today_plan["completed_count"] else "neutral",
        },
        {
            "label": "临近截止",
            "value": str(today_plan["due_soon_count"]),
            "hint": "含超时与本周",
            "tone": "warning" if today_plan["due_soon_count"] else "success",
        },
        {
            "label": "修为进度",
            "value": _format_dashboard_metric_value(cultivation_profile.get("progress_percent"), suffix="%"),
            "hint": str(highest_level.get("short_name") or highest_level.get("level_name") or "未入道"),
            "tone": "primary",
        },
        {
            "label": "未读提醒",
            "value": str(unread_total),
            "hint": f"已提交 {submitted_total}",
            "tone": "warning" if unread_total else "neutral",
        },
    ]

    next_steps: list[dict[str, Any]] = []
    seen_hrefs: set[str] = set()
    for item in today_plan["actionable_items"][:4]:
        href = str(item.get("href") or "")
        if not href or href in seen_hrefs:
            continue
        seen_hrefs.add(href)
        next_steps.append({
            "kind": str(item.get("kind") or "learning"),
            "label": str(item.get("label") or _student_cockpit_plan_label(str(item.get("kind") or ""))),
            "title": str(item.get("title") or "待处理事项"),
            "description": str(item.get("description") or "进入后查看完整内容。"),
            "href": href,
            "tone": str(item.get("tone") or "neutral"),
            "due_label": str(item.get("due_label") or ""),
        })

    if best_course.get("class_offering_id"):
        best_href = f"/classroom/{best_course['class_offering_id']}"
        if best_href not in seen_hrefs and len(next_steps) < 3:
            next_steps.append({
                "kind": "learning",
                "label": "修为推进",
                "title": str(best_course.get("course_name") or "继续学习"),
                "description": _dashboard_notice_text(
                    best_course.get("rank_notice"),
                    fallback="进入当前进度最高的课堂，保持学习连续性。",
                ),
                "href": best_href,
                "tone": "success",
                "due_label": str(best_course.get("next_stage_name") or "学习进度"),
            })

    sorted_offerings = sorted(
        offerings,
        key=lambda item: (
            0 if int(item.get("pending_count") or 0) > 0 else 1,
            0 if item.get("has_recent_activity") else 1,
            -int(item.get("pending_count") or 0),
            -float((item.get("cultivation") or {}).get("score") or 0),
            str(item.get("course_name") or ""),
        ),
    )
    course_pulse = []
    for item in sorted_offerings[:4]:
        cultivation = item.get("cultivation") or {}
        pending_count = int(item.get("pending_count") or 0)
        grading_count = int(item.get("grading_count") or 0)
        resource_total = int(item.get("resource_total") or 0)
        progress_percent = _clamp_dashboard_percent(cultivation.get("progress_percent"))
        if pending_count > 0:
            note = f"{pending_count} 项待完成"
            tone = "danger"
        elif grading_count > 0:
            note = f"{grading_count} 项批改中"
            tone = "warning"
        elif resource_total > 0:
            note = f"{resource_total} 个资料资源"
            tone = "primary"
        else:
            note = "进入课堂查看动态"
            tone = "neutral"
        course_pulse.append({
            "course_name": str(item.get("course_name") or "课堂"),
            "class_name": str(item.get("class_name") or ""),
            "href": f"/classroom/{item['id']}",
            "score": round(float(cultivation.get("score") or 0), 1),
            "progress_percent": progress_percent,
            "level_name": str(cultivation.get("short_name") or cultivation.get("level_name") or "未入道"),
            "level_theme": str(cultivation.get("theme") or cultivation.get("level_key") or "mortal"),
            "note": note,
            "tone": tone,
            "course_tone": str(item.get("course_tone") or "indigo"),
        })

    empty_state = {
        "title": "今天先建立一个学习起点",
        "description": "加入课堂后，这里会自动把作业、考试、提醒与修为进度汇总成每日行动卡。",
        "href": "/message-center",
        "label": "查看消息",
    }

    return {
        "title": _student_cockpit_greeting(now, student_name),
        "subtitle": _student_cockpit_day_shape(todo_items, now=now, open_count=today_plan["open_count"]),
        "path": {
            "href": "/learning-path",
            "label": "查看学习路径",
        },
        "today": today_plan,
        "primary": primary,
        "stats": stats,
        "next_steps": next_steps,
        "course_pulse": course_pulse,
        "show_course_pulse": bool(course_pulse),
        "continue_learning": continue_material,
        "empty": empty_state,
    }


def _build_student_dashboard_context(
    conn,
    user: dict,
    *,
    initial_filter: Any = None,
    initial_search: Any = None,
) -> dict[str, Any]:
    student_id = int(user["id"])
    student_security_summary = build_student_security_summary(conn, student_id)
    student_profile = conn.execute(
        """
        SELECT s.id, s.class_id, c.name AS class_name,
               (
                   SELECT COUNT(*)
                   FROM students peers
                   WHERE peers.class_id = s.class_id
                     AND COALESCE(peers.enrollment_status, 'active') = 'active'
               ) AS classmate_count
        FROM students s
        JOIN classes c ON c.id = s.class_id
        WHERE s.id = ?
          AND COALESCE(s.enrollment_status, 'active') = 'active'
        LIMIT 1
        """,
        (student_id,),
    ).fetchone()
    class_name = str(student_profile["class_name"] or "") if student_profile else ""
    classmate_count = int(student_profile["classmate_count"] or 0) if student_profile else 0

    offerings = _load_student_offerings(conn, student_id)
    offering_ids = [int(item["id"]) for item in offerings]
    course_ids = sorted({int(item["course_id"]) for item in offerings})
    assignment_stats = _load_student_assignment_stats(conn, offering_ids, student_id)
    resource_stats = _load_course_resource_stats(conn, course_ids, include_teacher_resources=False)
    material_stats = _load_offering_material_stats(conn, offering_ids)
    recent_activity = _load_recent_activity(conn, user)
    message_summary = get_message_center_summary(conn, user)
    unread_total = int(message_summary.get("unread_total") or 0)
    cultivation_profile = build_student_global_cultivation_profile(conn, student_id)
    ui_copy = render_ui_copy_block(
        get_ui_copy_block(conn, scene="dashboard", role="student"),
        {
            "name": cultivation_profile.get("address_name") or polite_address(user.get("name") or "", "student"),
            "class_name": class_name or "当前班级",
            "unread_total": unread_total,
        },
    )

    enriched_offerings: list[dict[str, Any]] = []
    pending_total = 0
    submitted_total = 0
    attention_count = 0
    progress_count = 0

    for offering in offerings:
        offering_id = int(offering["id"])
        course_id = int(offering["course_id"])
        course_visual = _dashboard_course_visual(course_id)
        assignment_item = assignment_stats.get(offering_id, {})
        resource_item = resource_stats.get(course_id, {})
        material_item = material_stats.get(offering_id, {})
        learning_progress = serialize_student_learning_progress(conn, offering_id, student_id)
        cultivation_level = learning_progress.get("current_level") or {}

        assignment_count = int(assignment_item.get("assignment_count") or 0)
        pending_count = int(assignment_item.get("pending_count") or 0)
        submitted_count = int(assignment_item.get("submitted_count") or 0)
        graded_count = int(assignment_item.get("graded_count") or 0)
        grading_count = int(assignment_item.get("grading_count") or 0)
        exam_count = int(assignment_item.get("exam_count") or 0)
        resource_count = int(resource_item.get("resource_count") or 0)
        material_count = int(material_item.get("material_count") or 0)
        resource_total = resource_count + material_count
        last_activity_at = _pick_latest_datetime(
            offering.get("created_at"),
            assignment_item.get("last_activity_at"),
            resource_item.get("latest_resource_at"),
            material_item.get("latest_material_at"),
        )

        pending_total += pending_count
        submitted_total += submitted_count
        attention_count += 1 if pending_count > 0 else 0
        progress_count += 1 if submitted_count > 0 or grading_count > 0 or graded_count > 0 else 0

        badges = []
        if pending_count > 0:
            badges.append({"label": f"待完成 {pending_count}", "tone": "danger"})
        if grading_count > 0:
            badges.append({"label": f"批改中 {grading_count}", "tone": "warning"})
        if graded_count > 0:
            badges.append({"label": f"已批改 {graded_count}", "tone": "success"})
        if exam_count > 0:
            badges.append({"label": f"考试 {exam_count}", "tone": "primary"})
        if cultivation_level.get("tier"):
            badges.append({"label": str(cultivation_level.get("short_name") or cultivation_level.get("level_name")), "tone": "success"})

        description = (
            str(offering.get("course_description") or "").strip()
            or "进入课堂继续查看资料、作业、考试与讨论内容。"
        )
        if pending_count > 0:
            summary = f"还有 {pending_count} 项已发布任务等待完成。"
        elif grading_count > 0:
            summary = f"有 {grading_count} 项任务正在批改，可以稍后回来查看结果。"
        elif submitted_count > 0:
            summary = f"你已经完成 {submitted_count} 项任务，继续保持。"
        elif assignment_count > 0:
            summary = f"当前共有 {assignment_count} 项可查看任务，建议先浏览要求。"
        else:
            summary = "当前以资料和课堂互动为主，进入课堂即可查看完整内容。"

        meta = [
            item
            for item in [
                f"授课教师 {offering['teacher_name']}" if offering.get("teacher_name") else "",
                offering.get("semester"),
                offering.get("schedule_info"),
            ]
            if item
        ]

        offering["summary"] = summary
        offering["description"] = description
        offering["meta"] = meta
        offering["badges"] = badges
        offering["assignment_count"] = assignment_count
        offering["pending_count"] = pending_count
        offering["submitted_count"] = submitted_count
        offering["graded_count"] = graded_count
        offering["grading_count"] = grading_count
        offering["exam_count"] = exam_count
        offering["resource_total"] = resource_total
        offering["resource_count"] = resource_count
        offering["material_count"] = material_count
        offering["last_activity_at"] = last_activity_at or ""
        offering["last_activity_sort"] = _dashboard_datetime_sort_value(last_activity_at)
        offering["course_tone"] = course_visual["tone"]
        offering["course_pattern"] = course_visual["pattern"]
        offering["needs_attention"] = pending_count > 0
        offering["has_recent_activity"] = _is_recent(last_activity_at)
        offering["has_progress"] = (
            submitted_count > 0
            or graded_count > 0
            or grading_count > 0
            or float(learning_progress.get("score") or 0) > 0
        )
        offering["cultivation"] = {
            "score": learning_progress.get("score", 0),
            "progress_percent": learning_progress.get("progress_percent", 0),
            "level_name": cultivation_level.get("level_name") or "未入道",
            "short_name": cultivation_level.get("short_name") or "未入道",
            "theme": cultivation_level.get("theme") or "mortal",
            "next_stage_name": (learning_progress.get("next_stage") or {}).get("name"),
        }
        offering["metrics"] = [
            {"label": "修为", "value": learning_progress.get("score", 0), "note": offering["cultivation"]["level_name"]},
            {"label": "待完成", "value": pending_count, "note": "仅统计已发布任务"},
            {"label": "已提交", "value": submitted_count, "note": f"已批改 {graded_count}"},
            {"label": "资料", "value": resource_total, "note": f"文件 {resource_count} · 材料 {material_count}"},
        ]
        offering["search_text"] = _build_dashboard_search_text(
            offering.get("course_name"),
            offering.get("class_name"),
            offering.get("teacher_name"),
            offering.get("semester"),
            offering.get("schedule_info"),
            description,
            summary,
            *meta,
            *(badge.get("label") for badge in badges),
            *(f"{metric['label']} {metric['value']} {metric['note']}" for metric in offering["metrics"]),
        )
        enriched_offerings.append(offering)

    priority_items = _load_student_priority_items(conn, student_id)
    if unread_total > 0:
        priority_items.append({
            "title": ui_copy["priority_unread_title"],
            "description": ui_copy["priority_unread_description"],
            "href": "/message-center",
            "tone": "primary",
        })

    todo_overviews, dashboard_todo_items = _load_student_dashboard_todo_overviews(
        conn,
        enriched_offerings,
        user,
    )
    academic_priority_items = _dashboard_academic_focus_items(dashboard_todo_items, limit=3)
    if academic_priority_items:
        seen_focus_keys = {
            (str(item.get("title") or ""), str(item.get("href") or ""))
            for item in academic_priority_items
        }
        priority_items = academic_priority_items + [
            item
            for item in priority_items
            if (str(item.get("title") or ""), str(item.get("href") or "")) not in seen_focus_keys
        ]
    if not priority_items:
        fallback_href = f"/classroom/{offerings[0]['id']}" if offerings else "/message-center"
        priority_items.append({
            "title": ui_copy["priority_empty_title"],
            "description": ui_copy["priority_empty_description"],
            "href": fallback_href,
            "tone": "neutral",
        })
    continue_material = _load_student_continue_material(
        conn,
        student_id=student_id,
        offering_ids=offering_ids,
    )
    feedback_review_summary = build_feedback_review_summary(conn, student_id)
    student_display_name = cultivation_profile.get("address_name") or polite_address(user.get("name") or "", "student")
    student_cockpit = _build_student_cockpit(
        offerings=enriched_offerings,
        priority_items=priority_items,
        cultivation_profile=cultivation_profile,
        todo_items=dashboard_todo_items,
        continue_material=continue_material,
        review_summary=feedback_review_summary,
        pending_total=pending_total,
        submitted_total=submitted_total,
        unread_total=unread_total,
        student_name=student_display_name,
    )
    continue_action = _build_student_continue_action(enriched_offerings)

    first_pending_href = str(
        (student_cockpit.get("primary") or {}).get("href")
        or (priority_items[0]["href"] if priority_items else "")
        or (f"/classroom/{offerings[0]['id']}" if offerings else "/message-center")
    )

    total_logins = int(student_security_summary.get("total_logins") or 0) if student_security_summary else 0
    spotlight = {
        "label": ui_copy["spotlight_pending_label"],
        "value": pending_total,
        "suffix": "项",
        "note": ui_copy["spotlight_pending_note"],
    }
    if pending_total <= 0 and unread_total > 0:
        spotlight = {
            "label": ui_copy["spotlight_unread_label"],
            "value": unread_total,
            "suffix": "条",
            "note": ui_copy["spotlight_unread_note"],
        }
    elif pending_total <= 0:
        last_device = ""
        if student_security_summary and student_security_summary.get("last_login"):
            last_device = str(student_security_summary["last_login"].get("device_label") or "")
        spotlight = {
            "label": ui_copy["spotlight_login_label"],
            "value": total_logins,
            "suffix": "次",
            "note": last_device or ui_copy["spotlight_login_note"],
        }

    quick_actions = [
        {
            "mode": "link",
            "label": ui_copy["action_priority_label"],
            "description": ui_copy["action_priority_description"],
            "href": first_pending_href,
            "badge": pending_total or None,
        },
        {
            "mode": "link",
            "label": "成长档案",
            "description": "整理作品、证书与复盘证据",
            "href": "/profile?section=portfolio",
            "badge": None,
        },
        {
            "mode": "link",
            "label": ui_copy["action_message_label"],
            "description": ui_copy["action_message_description"],
            "href": "/message-center",
            "badge": unread_total or None,
        },
        {
            "mode": "button",
            "label": ui_copy["action_security_label"],
            "description": ui_copy["action_security_description"],
            "button_attrs": {"data-open-student-security": "true"},
            "badge": None,
        },
    ]
    recent_count = sum(1 for item in enriched_offerings if item["has_recent_activity"])
    dashboard_filters = [
        {"value": "all", "label": "全部", "count": len(offerings)},
        {"value": "attention", "label": "待完成", "count": attention_count},
        {"value": "progress", "label": "有进展", "count": progress_count},
        {"value": "recent", "label": "近期活跃", "count": recent_count},
    ]
    selected_filter = _normalize_dashboard_filter("student", initial_filter)
    search_query = _normalize_dashboard_search(initial_search)
    initial_visible_count = _apply_dashboard_view_state(
        enriched_offerings,
        filter_value=selected_filter,
        search_query=search_query,
    )
    initial_results_summary = _build_dashboard_results_summary(
        dashboard_filters,
        filter_value=selected_filter,
        search_query=search_query,
    )
    semester_calendar = build_semester_calendar_payload(
        load_student_semester_rows(conn, student_id),
    )
    _attach_dashboard_todos_to_semester_calendar(
        conn,
        semester_calendar,
        offerings,
        user,
        preloaded_todo_overviews=todo_overviews,
    )

    return {
        "dashboard_theme": "student",
        "dashboard_hero": {
            "eyebrow": ui_copy["hero_eyebrow"],
            "title": ui_copy["hero_title"],
            "subtitle": ui_copy["hero_subtitle"],
            "chips": [
                class_name or "当前班级",
                f"{cultivation_profile['highest_level']['level_name']} · 修为 {cultivation_profile['score']:g}",
                f"累计登录 {total_logins} 次",
                f"同班 {classmate_count} 人",
            ],
            "spotlight": spotlight,
        },
        "dashboard_stats": [
            {"label": "最高境界", "value": cultivation_profile["highest_level"]["short_name"], "note": cultivation_profile.get("best_course", {}).get("course_name") or class_name or "当前班级"},
            {"label": "待完成", "value": pending_total, "note": "仅统计已发布任务"},
            {"label": "已提交", "value": submitted_total, "note": "含待批改与已批改"},
            {"label": "未读提醒", "value": unread_total, "note": f"累计登录 {total_logins} 次"},
        ],
        "dashboard_quick_actions": quick_actions,
        "dashboard_sections": {
            "quick_actions": {
                "title": ui_copy["quick_actions_title"],
                "subtitle": ui_copy["quick_actions_subtitle"],
            },
        },
        "dashboard_focus": {
            "title": ui_copy["focus_title"],
            "subtitle": ui_copy["focus_subtitle"],
            "items": priority_items[:4],
        },
        "dashboard_activity": {
            "title": ui_copy["activity_title"],
            "subtitle": ui_copy["activity_subtitle"],
            "items": recent_activity,
        },
        "dashboard_filters": dashboard_filters,
        "dashboard_search_placeholder": "搜索课程、教师或学期",
        "dashboard_initial_filter": selected_filter,
        "dashboard_initial_search": search_query,
        "dashboard_initial_visible_count": initial_visible_count,
        "dashboard_initial_results_summary": initial_results_summary,
        "dashboard_default_group_mode": "flat",
        "dashboard_recent_activity_days": RECENT_ACTIVITY_DAYS,
        "dashboard_empty_state": {
            "title": ui_copy["empty_title"],
            "description": ui_copy["empty_description"],
            "action_label": ui_copy["empty_action_label"],
            "action_href": "/message-center",
            "steps": [
                {
                    "title": ui_copy["empty_step_profile_title"],
                    "description": ui_copy["empty_step_profile_description"],
                    "href": "/profile",
                    "label": ui_copy["empty_step_profile_label"],
                },
                {
                    "title": ui_copy["empty_step_classroom_title"],
                    "description": ui_copy["empty_step_classroom_description"],
                    "href": "",
                    "label": "",
                },
                {
                    "title": ui_copy["empty_step_message_title"],
                    "description": ui_copy["empty_step_message_description"],
                    "href": "/message-center",
                    "label": ui_copy["empty_step_message_label"],
                },
            ],
        },
        "dashboard_continue_action": continue_action,
        "class_offerings": enriched_offerings,
        "dashboard_semester_calendar": semester_calendar,
        "dashboard_agenda_events": _build_agenda_events_from_todos(
            dashboard_todo_items,
            today=china_today(),
            now=china_now().replace(tzinfo=None),
        ),
        "dashboard_student_cockpit": student_cockpit,
        "dashboard_feedback_review_summary": feedback_review_summary,
        "student_security_summary": student_security_summary,
        "cultivation_profile": cultivation_profile,
    }


def _load_teacher_offerings(conn, teacher_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT o.id, o.class_id, o.course_id, o.teacher_id, o.semester, o.semester_id, o.schedule_info,
               o.first_class_date, o.weekly_schedule_json, o.created_at,
               c.name AS course_name, c.description AS course_description, c.credits AS course_credits,
               c.department AS course_department,
               cl.name AS class_name, cl.description AS class_description, cl.department AS class_department,
               COUNT(CASE
                   WHEN COALESCE(s.enrollment_status, 'active') = 'active'
                   THEN s.id END
               ) AS student_count
        FROM class_offerings o
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        LEFT JOIN students s
               ON s.class_id = o.class_id
              AND COALESCE(s.enrollment_status, 'active') = 'active'
        WHERE o.teacher_id = ?
        GROUP BY o.id, o.class_id, o.course_id, o.teacher_id, o.semester, o.semester_id, o.schedule_info,
                 o.first_class_date, o.weekly_schedule_json, o.created_at,
                 c.name, c.description, c.credits, c.department, cl.name, cl.description, cl.department
        ORDER BY COALESCE(cl.department, ''), cl.name, c.name, o.id DESC
        """,
        (teacher_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_student_offerings(conn, student_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT o.id, o.class_id, o.course_id, o.teacher_id, o.semester, o.semester_id, o.schedule_info, o.created_at,
               c.name AS course_name, c.description AS course_description, c.credits AS course_credits,
               cl.name AS class_name, cl.description AS class_description,
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
        (student_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_teacher_assignment_stats(conn, offering_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not offering_ids:
        return {}
    placeholders = ",".join("?" for _ in offering_ids)
    rows = conn.execute(
        f"""
        SELECT o.id AS offering_id,
               COUNT(DISTINCT a.id) AS assignment_count,
               COUNT(DISTINCT CASE WHEN a.status = 'new' THEN a.id END) AS draft_count,
               COUNT(DISTINCT CASE WHEN a.status = 'published' THEN a.id END) AS published_count,
               COUNT(DISTINCT CASE WHEN a.exam_paper_id IS NOT NULL THEN a.id END) AS exam_count,
               MAX(a.created_at) AS latest_assignment_at
        FROM class_offerings o
        LEFT JOIN assignments a
            ON a.course_id = o.course_id
           AND (a.class_offering_id = o.id OR a.class_offering_id IS NULL)
           AND NOT EXISTS (
               SELECT 1 FROM learning_stage_exam_attempts lsea
               WHERE lsea.assignment_id = a.id
           )
        WHERE o.id IN ({placeholders})
        GROUP BY o.id
        """,
        tuple(offering_ids),
    ).fetchall()
    return {int(row["offering_id"]): dict(row) for row in rows}


def _load_student_assignment_stats(conn, offering_ids: list[int], student_id: int) -> dict[int, dict[str, Any]]:
    if not offering_ids:
        return {}
    placeholders = ",".join("?" for _ in offering_ids)
    params = [student_id, student_id, *offering_ids]
    rows = conn.execute(
        f"""
        SELECT o.id AS offering_id,
               COUNT(DISTINCT CASE WHEN a.status != 'new' THEN a.id END) AS assignment_count,
               COUNT(DISTINCT CASE WHEN a.status != 'new' AND a.exam_paper_id IS NOT NULL THEN a.id END) AS exam_count,
               COUNT(DISTINCT CASE
                   WHEN a.status = 'published'
                    AND (s.id IS NULL OR COALESCE(s.resubmission_allowed, 0) = 1)
                   THEN a.id END) AS pending_count,
               COUNT(DISTINCT CASE
                   WHEN s.id IS NOT NULL
                    AND COALESCE(s.resubmission_allowed, 0) = 0
                   THEN a.id END) AS submitted_count,
               COUNT(DISTINCT CASE WHEN s.status = 'graded' THEN a.id END) AS graded_count,
               COUNT(DISTINCT CASE WHEN s.status = 'grading' THEN a.id END) AS grading_count,
               MAX(COALESCE(s.submitted_at, a.created_at)) AS last_activity_at
        FROM class_offerings o
        LEFT JOIN assignments a
            ON a.course_id = o.course_id
           AND (a.class_offering_id = o.id OR a.class_offering_id IS NULL)
           AND NOT EXISTS (
               SELECT 1 FROM learning_stage_exam_attempts lsea
               WHERE lsea.assignment_id = a.id
                 AND lsea.student_id != ?
           )
        LEFT JOIN submissions s
            ON s.assignment_id = a.id
           AND s.student_pk_id = ?
        WHERE o.id IN ({placeholders})
        GROUP BY o.id
        """,
        tuple(params),
    ).fetchall()
    return {int(row["offering_id"]): dict(row) for row in rows}


def _load_teacher_pending_submission_stats(conn, offering_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not offering_ids:
        return {}
    placeholders = ",".join("?" for _ in offering_ids)
    rows = conn.execute(
        f"""
        SELECT a.class_offering_id AS offering_id,
               COUNT(DISTINCT CASE
                   WHEN COALESCE(s.is_absence_score, 0) = 0
                    AND COALESCE(s.resubmission_allowed, 0) = 0
                    AND s.status = 'submitted'
                   THEN s.student_pk_id END) AS pending_review_count,
               COUNT(DISTINCT CASE
                   WHEN COALESCE(s.is_absence_score, 0) = 0
                    AND COALESCE(s.resubmission_allowed, 0) = 0
                    AND s.status = 'grading'
                   THEN s.student_pk_id END) AS grading_count,
               MAX(CASE
                   WHEN COALESCE(s.is_absence_score, 0) = 0
                    AND COALESCE(s.resubmission_allowed, 0) = 0
                    AND s.status IN ('submitted', 'grading')
                   THEN s.submitted_at END) AS latest_submission_at
        FROM assignments a
        JOIN submissions s ON s.assignment_id = a.id
        WHERE a.class_offering_id IN ({placeholders})
          AND s.status IN ('submitted', 'grading')
          AND NOT EXISTS (
              SELECT 1 FROM learning_stage_exam_attempts lsea
              WHERE lsea.assignment_id = a.id
          )
        GROUP BY a.class_offering_id
        """,
        tuple(offering_ids),
    ).fetchall()
    return {int(row["offering_id"]): dict(row) for row in rows}


def _load_teacher_recent_login_stats(conn, class_ids: list[int]) -> dict[int, dict[str, Any]]:
    normalized_class_ids = sorted({int(class_id) for class_id in class_ids if int(class_id) > 0})
    if not normalized_class_ids:
        return {}
    placeholders = ",".join("?" for _ in normalized_class_ids)
    cutoff = (datetime.now() - timedelta(days=RECENT_ACTIVITY_DAYS)).isoformat()
    rows = conn.execute(
        f"""
        SELECT class_id,
               COUNT(DISTINCT student_id) AS recent_active_student_count,
               COUNT(*) AS recent_login_count,
               MAX(logged_at) AS latest_login_at
        FROM student_login_audit_logs
        WHERE class_id IN ({placeholders})
          AND logged_at >= ?
          AND student_id IN (
              SELECT id
              FROM students
              WHERE COALESCE(enrollment_status, 'active') = 'active'
          )
        GROUP BY class_id
        """,
        (*normalized_class_ids, cutoff),
    ).fetchall()
    return {int(row["class_id"]): dict(row) for row in rows}


def _load_course_resource_stats(
    conn,
    course_ids: list[int],
    *,
    include_teacher_resources: bool,
) -> dict[int, dict[str, Any]]:
    if not course_ids:
        return {}
    placeholders = ",".join("?" for _ in course_ids)
    conditions = [f"course_id IN ({placeholders})"]
    if not include_teacher_resources:
        conditions.append("LOWER(COALESCE(is_public, '0')) IN ('1', 'true', 't', 'yes')")
        conditions.append("LOWER(COALESCE(is_teacher_resource, '0')) NOT IN ('1', 'true', 't', 'yes')")
    rows = conn.execute(
        f"""
        SELECT course_id,
               COUNT(*) AS resource_count,
               MAX(uploaded_at) AS latest_resource_at
        FROM course_files
        WHERE {' AND '.join(conditions)}
        GROUP BY course_id
        """,
        tuple(course_ids),
    ).fetchall()
    return {int(row["course_id"]): dict(row) for row in rows}


def _load_offering_material_stats(conn, offering_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not offering_ids:
        return {}
    placeholders = ",".join("?" for _ in offering_ids)
    rows = conn.execute(
        f"""
        SELECT class_offering_id AS offering_id,
               COUNT(*) AS material_count,
               MAX(created_at) AS latest_material_at
        FROM course_material_assignments
        WHERE class_offering_id IN ({placeholders})
        GROUP BY class_offering_id
        """,
        tuple(offering_ids),
    ).fetchall()
    return {int(row["offering_id"]): dict(row) for row in rows}


def _load_recent_activity(conn, user: dict, limit: int = 6) -> list[dict[str, Any]]:
    role = str(user.get("role") or "").strip().lower()
    user_pk = int(user["id"])
    primary_sql = """
        SELECT id, category, title, body_preview, link_url, read_at, created_at
        FROM message_center_notifications
        WHERE recipient_role = ? AND recipient_user_pk = ?
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """
    fallback_sql = """
        SELECT id, category, title, body_preview, link_url, read_at, created_at
        FROM message_center_notifications NOT INDEXED
        WHERE recipient_role = ? AND recipient_user_pk = ?
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """
    try:
        rows = conn.execute(
            primary_sql,
            (role, user_pk, limit),
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        print(f"[DB WARN] Failed to load recent activity with index: {exc}")
        try:
            rows = conn.execute(
                fallback_sql,
                (role, user_pk, limit),
            ).fetchall()
        except sqlite3.DatabaseError as fallback_exc:
            print(f"[DB WARN] Failed to load recent activity without index: {fallback_exc}")
            return []
    items = []
    for row in rows:
        category = str(row["category"] or "")
        items.append({
            "title": str(row["title"] or "新提醒"),
            "description": str(row["body_preview"] or "点击查看详情"),
            "href": str(row["link_url"] or "/message-center"),
            "label": CATEGORY_LABELS.get(category, category or "提醒"),
            "tone": ACTIVITY_TONE_BY_CATEGORY.get(category, "neutral"),
            "is_unread": not row["read_at"],
            "created_at": str(row["created_at"] or ""),
        })
    return items


def _load_student_priority_items(conn, student_id: int, limit: int = 4) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT a.id AS assignment_id,
               a.title,
               a.exam_paper_id,
               a.created_at,
               o.id AS offering_id,
               c.name AS course_name,
               cl.name AS class_name
        FROM class_offerings o
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        JOIN assignments a
            ON a.course_id = o.course_id
           AND (a.class_offering_id = o.id OR a.class_offering_id IS NULL)
           AND NOT EXISTS (
               SELECT 1 FROM learning_stage_exam_attempts lsea
               WHERE lsea.assignment_id = a.id
                 AND lsea.student_id != ?
           )
        LEFT JOIN submissions s
            ON s.assignment_id = a.id
           AND s.student_pk_id = ?
        WHERE o.class_id = (
            SELECT class_id
            FROM students
            WHERE id = ?
              AND COALESCE(enrollment_status, 'active') = 'active'
        )
          AND a.status = 'published'
          AND s.id IS NULL
        ORDER BY a.created_at DESC, a.id DESC
        LIMIT ?
        """,
        (student_id, student_id, student_id, limit),
    ).fetchall()

    items = []
    for row in rows:
        items.append({
            "title": str(row["title"] or "待完成任务"),
            "description": f"{row['course_name']} · {row['class_name']}"
            + (" · 考试" if row["exam_paper_id"] else " · 作业"),
            "href": f"/assignment/{row['assignment_id']}",
            "tone": "danger" if not row["exam_paper_id"] else "warning",
        })
    return items


def _query_scalar(conn, sql: str, params: tuple[Any, ...]) -> int:
    row = conn.execute(sql, params).fetchone()
    if not row:
        return 0
    if isinstance(row, Mapping):
        for key in ("row_count", "count", "total", "cnt"):
            if key in row:
                return int(row[key] or 0)
        return int(next(iter(row.values()), 0) or 0)
    return int(row[0] or 0)


def _teacher_today_login_count_sql() -> str:
    if get_configured_db_engine() == "postgres":
        return """
        SELECT COUNT(*)
        FROM student_login_audit_logs logs
        JOIN (
            SELECT DISTINCT class_id
            FROM class_offerings
            WHERE teacher_id = ?
        ) active_classes ON active_classes.class_id = logs.class_id
        WHERE logged_at::date = CURRENT_DATE
        """
    return """
    SELECT COUNT(*)
    FROM student_login_audit_logs logs
    JOIN (
        SELECT DISTINCT class_id
        FROM class_offerings
        WHERE teacher_id = ?
    ) active_classes ON active_classes.class_id = logs.class_id
    WHERE date(logged_at) = date('now', 'localtime')
    """


def _normalize_dashboard_filter(role: str, value: Any) -> str:
    allowed_values = DASHBOARD_FILTER_VALUES.get(role, ("all",))
    normalized = str(value or "").strip().lower()
    return normalized if normalized in allowed_values else "all"


def _normalize_dashboard_search(value: Any, *, max_length: int = 80) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())[:max_length]


def _normalize_dashboard_search_token(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _build_dashboard_search_text(*parts: Any) -> str:
    tokens: list[str] = []
    seen: set[str] = set()
    for part in parts:
        token = _normalize_dashboard_search_token(part)
        if not token:
            continue
        for candidate in (token, token.replace(" ", "")):
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            tokens.append(candidate)
    return " ".join(tokens)


def _matches_dashboard_filter(offering: dict[str, Any], filter_value: str) -> bool:
    if filter_value == "attention":
        return bool(offering.get("needs_attention"))
    if filter_value == "recent":
        return bool(offering.get("has_recent_activity"))
    if filter_value == "progress":
        return bool(offering.get("has_progress"))
    return True


def _matches_dashboard_search(offering: dict[str, Any], search_query: str) -> bool:
    if not search_query:
        return True
    normalized_query = _normalize_dashboard_search_token(search_query)
    if not normalized_query:
        return True
    haystack = str(offering.get("search_text") or "")
    if normalized_query in haystack:
        return True
    compact_query = normalized_query.replace(" ", "")
    return bool(compact_query) and compact_query in haystack.replace(" ", "")


def _apply_dashboard_view_state(
    offerings: list[dict[str, Any]],
    *,
    filter_value: str,
    search_query: str,
) -> int:
    visible_count = 0
    for offering in offerings:
        is_visible = _matches_dashboard_filter(offering, filter_value) and _matches_dashboard_search(offering, search_query)
        offering["initially_visible"] = is_visible
        if is_visible:
            visible_count += 1
    return visible_count


def _build_dashboard_results_summary(
    filters: list[dict[str, Any]],
    *,
    filter_value: str,
    search_query: str,
) -> str:
    filter_labels = {str(item.get("value") or ""): str(item.get("label") or "") for item in filters}
    fragments: list[str] = []
    if filter_value != "all":
        fragments.append(f"筛选：{filter_labels.get(filter_value, filter_value)}")
    if search_query:
        fragments.append(f"关键词：{search_query}")
    return " · ".join(fragments) if fragments else "显示全部课堂"


def _pick_latest_datetime(*values: Any) -> str:
    latest_value = ""
    latest_dt: datetime | None = None
    for value in values:
        parsed = _parse_datetime(value)
        if parsed is None:
            continue
        if latest_dt is None or parsed > latest_dt:
            latest_dt = parsed
            latest_value = str(value or "")
    return latest_value


def _parse_datetime(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    candidates = [raw]
    if raw.endswith("Z"):
        candidates.append(raw[:-1] + "+00:00")
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, pattern)
        except ValueError:
            continue
    return None


def _is_recent(value: Any, days: int = RECENT_ACTIVITY_DAYS) -> bool:
    parsed = _parse_datetime(value)
    if parsed is None:
        return False
    return parsed >= datetime.now() - timedelta(days=days)
