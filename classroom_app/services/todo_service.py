from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta
from typing import Any

from .academic_service import china_now, parse_date_input
from .course_planning_service import weekday_label
from .learning_progress_service import get_learning_level, personal_stage_assignment_filter_sql, public_level_payload
from .message_center_service import create_todo_notification


TODO_SOURCE_LESSON = "lesson"
TODO_SOURCE_ASSIGNMENT = "assignment"
TODO_SOURCE_STAGE = "stage_exam"
TODO_SOURCE_MANUAL = "manual"

TODO_MAX_TITLE_LENGTH = 120
TODO_MAX_NOTES_LENGTH = 1200


class TodoValidationError(ValueError):
    """Raised when manual todo data is invalid."""


def _now_iso() -> str:
    return china_now().replace(tzinfo=None).isoformat(timespec="seconds")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _clean_text(value: Any, *, max_length: int, field_name: str, required: bool = False) -> str:
    normalized = " ".join(str(value or "").replace("\u3000", " ").split()).strip()
    if required and not normalized:
        raise TodoValidationError(f"{field_name}不能为空")
    if len(normalized) > max_length:
        raise TodoValidationError(f"{field_name}不能超过 {max_length} 个字符")
    return normalized


def parse_datetime_input(value: Any, field_name: str = "时间") -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime.combine(value, time.min)

    normalized = str(value).strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = normalized[:-1]
    if len(normalized) == 10:
        try:
            return datetime.combine(date.fromisoformat(normalized), time.min)
        except ValueError as exc:
            raise TodoValidationError(f"{field_name}格式无效") from exc

    normalized = normalized.replace("T", " ")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise TodoValidationError(f"{field_name}格式无效") from exc
    return parsed.replace(tzinfo=None)


def _date_key(value: datetime | date | None) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    return value.isoformat()


def _week_start(value: date) -> date:
    return value - timedelta(days=value.weekday())


def _month_day_label(value: date | datetime | None) -> str:
    if value is None:
        return ""
    day_value = value.date() if isinstance(value, datetime) else value
    return f"{day_value.month}月{day_value.day}日"


def _minute_label(value: datetime | None) -> str:
    if value is None:
        return ""
    return f"{value.hour:02d}:{value.minute:02d}"


def _datetime_label(value: datetime | None, *, with_time: bool = True) -> str:
    if value is None:
        return ""
    base = f"{_month_day_label(value)} {weekday_label(value.weekday())}"
    if with_time:
        return f"{base} {_minute_label(value)}"
    return base


def _relative_due_label(due_at: datetime | None, now: datetime) -> str:
    if due_at is None:
        return "无截止日期"
    delta = due_at - now
    minutes = int(delta.total_seconds() // 60)
    if minutes < 0:
        overdue_minutes = abs(minutes)
        if overdue_minutes < 60:
            return f"已超时 {overdue_minutes} 分钟"
        if overdue_minutes < 60 * 24:
            return f"已超时 {overdue_minutes // 60} 小时"
        return f"已超时 {overdue_minutes // (60 * 24)} 天"
    if minutes < 60:
        return f"{minutes} 分钟后截止"
    if minutes < 60 * 24:
        return f"{minutes // 60} 小时后截止"
    return f"{minutes // (60 * 24)} 天后截止"


def _duration_label(start_at: datetime | None, due_at: datetime | None) -> str:
    if due_at is None:
        return "无截止日期"
    if start_at and start_at.date() != due_at.date():
        return f"{_datetime_label(start_at, with_time=False)} - {_datetime_label(due_at)}"
    return f"{_datetime_label(due_at)} 截止"


def _build_calendar_days(week_start: date, today: date) -> list[dict[str, Any]]:
    days = []
    for offset in range(7):
        current = week_start + timedelta(days=offset)
        days.append(
            {
                "date": current.isoformat(),
                "day_number": current.day,
                "month_day_label": _month_day_label(current),
                "weekday_label": weekday_label(current.weekday()),
                "is_today": current == today,
                "is_weekend": current.weekday() >= 5,
            }
        )
    return days


def _bar_position_for_week(item: dict[str, Any], week_start: date) -> dict[str, float]:
    week_end = week_start + timedelta(days=6)
    start_date = parse_date_input(item.get("effective_start_date")) or week_start
    end_date = parse_date_input(item.get("effective_end_date")) or start_date
    if item.get("no_deadline"):
        end_date = start_date
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


def _todo_overlaps_week(item: dict[str, Any], week_start: date) -> bool:
    week_end = week_start + timedelta(days=6)
    start_date = parse_date_input(item.get("effective_start_date")) or week_start
    end_date = parse_date_input(item.get("effective_end_date")) or start_date
    if item.get("no_deadline"):
        end_date = start_date
    return start_date <= week_end and end_date >= week_start


def _sort_key(item: dict[str, Any]) -> tuple[int, str, str]:
    completed_rank = 1 if item.get("is_completed") else 0
    no_deadline_rank = 1 if item.get("no_deadline") else 0
    end_at = str(item.get("effective_end_at") or item.get("effective_start_at") or "9999-12-31")
    return (completed_rank, no_deadline_rank, end_at, str(item.get("title") or ""))


def _normalize_item(
    *,
    source_type: str,
    source_id: Any,
    title: str,
    start_at: datetime | None,
    due_at: datetime | None,
    created_at: datetime | None,
    subtitle: str = "",
    notes: str = "",
    link_url: str = "",
    status: str = "",
    status_label: str = "",
    tone: str = "neutral",
    is_manual: bool = False,
    is_completed: bool = False,
    can_complete: bool = False,
    metadata: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current_now = now or china_now().replace(tzinfo=None)
    effective_start = start_at or created_at or due_at or current_now
    effective_end = due_at or effective_start
    start_date = effective_start.date()
    end_date = effective_end.date()
    todo_id = f"{source_type}:{source_id}"
    no_deadline = due_at is None
    return {
        "id": todo_id,
        "source_type": source_type,
        "source_id": source_id,
        "title": title,
        "subtitle": subtitle,
        "notes": notes,
        "link_url": link_url,
        "status": status,
        "status_label": status_label,
        "tone": tone,
        "is_manual": is_manual,
        "is_completed": is_completed,
        "can_complete": can_complete,
        "no_deadline": no_deadline,
        "start_at": start_at.isoformat(timespec="minutes") if start_at else "",
        "due_at": due_at.isoformat(timespec="minutes") if due_at else "",
        "created_at": created_at.isoformat(timespec="minutes") if created_at else "",
        "effective_start_at": effective_start.isoformat(timespec="minutes"),
        "effective_end_at": effective_end.isoformat(timespec="minutes"),
        "effective_start_date": start_date.isoformat(),
        "effective_end_date": end_date.isoformat(),
        "start_label": _datetime_label(effective_start, with_time=bool(start_at)),
        "deadline_label": _datetime_label(due_at) if due_at else "无截止",
        "relative_due_label": _relative_due_label(due_at, current_now),
        "duration_label": _duration_label(effective_start, due_at),
        "due_time_label": _minute_label(due_at),
        "metadata": metadata or {},
    }


def _load_offering_calendar_bounds(conn: sqlite3.Connection, class_offering_id: int) -> tuple[date | None, date | None]:
    row = conn.execute(
        """
        SELECT sem.start_date, sem.end_date
        FROM class_offerings o
        LEFT JOIN academic_semesters sem ON sem.id = o.semester_id
        WHERE o.id = ?
        LIMIT 1
        """,
        (int(class_offering_id),),
    ).fetchone()
    if not row:
        return None, None
    return parse_date_input(row["start_date"]), parse_date_input(row["end_date"])


def _load_teaching_plan(conn: sqlite3.Connection, class_offering_id: int) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT id,
               order_index,
               title,
               session_date,
               weekday,
               week_index
        FROM class_offering_sessions
        WHERE class_offering_id = ?
        ORDER BY session_date, order_index, id
        """,
        (int(class_offering_id),),
    ).fetchall()
    sessions = []
    for index, row in enumerate(rows, start=1):
        item = dict(row)
        session_date = parse_date_input(item.get("session_date"), "上课日期")
        if not session_date:
            continue
        order_index = _safe_int(item.get("order_index"), index) or index
        sessions.append(
            {
                "id": item.get("id"),
                "order_index": order_index,
                "title": item.get("title") or "课堂",
                "session_date": session_date.isoformat(),
                "weekday": _safe_int(item.get("weekday"), session_date.weekday()),
                "week_index": _safe_int(item.get("week_index")),
                "session_number_label": f"第 {order_index} 次课",
            }
        )
    return {"sessions": sessions}


def _load_manual_todos(conn: sqlite3.Connection, *, class_offering_id: int, user: dict[str, Any]) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM classroom_todos
        WHERE class_offering_id = ?
          AND owner_role = ?
          AND owner_user_pk = ?
          AND deleted_at IS NULL
        ORDER BY COALESCE(due_at, start_at, created_at), id
        """,
        (int(class_offering_id), str(user.get("role") or ""), int(user["id"])),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_assignment_rows(conn: sqlite3.Connection, *, class_offering_id: int, user: dict[str, Any]) -> list[dict[str, Any]]:
    role = str(user.get("role") or "").strip().lower()
    if role == "teacher":
        rows = conn.execute(
            f"""
            SELECT a.*,
                   ep.title AS exam_paper_title
            FROM assignments a
            LEFT JOIN exam_papers ep ON ep.id = a.exam_paper_id
            WHERE a.class_offering_id = ?
              AND {personal_stage_assignment_filter_sql('a')}
            ORDER BY COALESCE(a.due_at, a.starts_at, a.created_at), a.id
            """,
            (int(class_offering_id),),
        ).fetchall()
        return [dict(row) for row in rows]

    rows = conn.execute(
        """
        SELECT a.*,
               ep.title AS exam_paper_title,
               s.id AS submission_id,
               s.status AS submission_status,
               s.score AS submission_score,
               s.resubmission_allowed,
               s.resubmission_due_at
        FROM assignments a
        LEFT JOIN exam_papers ep ON ep.id = a.exam_paper_id
        LEFT JOIN submissions s
               ON s.assignment_id = a.id
              AND s.student_pk_id = ?
        WHERE a.class_offering_id = ?
          AND a.status != 'new'
          AND NOT EXISTS (
              SELECT 1
              FROM learning_stage_exam_attempts lsea
              WHERE lsea.assignment_id = a.id
          )
        ORDER BY COALESCE(a.due_at, a.starts_at, a.created_at), a.id
        """,
        (int(user["id"]), int(class_offering_id)),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_stage_attempts(conn: sqlite3.Connection, *, class_offering_id: int, student_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT lsea.*,
               a.title AS assignment_title,
               ep.title AS exam_paper_title
        FROM learning_stage_exam_attempts lsea
        LEFT JOIN assignments a ON a.id = lsea.assignment_id
        LEFT JOIN exam_papers ep ON ep.id = lsea.exam_paper_id
        WHERE lsea.class_offering_id = ?
          AND lsea.student_id = ?
          AND lsea.status IN ('generating', 'generated', 'submitted', 'grading', 'failed')
        ORDER BY lsea.generated_at DESC, lsea.id DESC
        """,
        (int(class_offering_id), int(student_id)),
    ).fetchall()
    return [dict(row) for row in rows]


def _lesson_items(teaching_plan: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    items = []
    today = now.date()
    for session in teaching_plan.get("sessions") or []:
        session_date = parse_date_input(session.get("session_date"), "上课日期")
        if not session_date:
            continue
        title = str(session.get("title") or session.get("detail_title") or "课堂").strip()
        session_number = str(session.get("session_number_label") or "").strip()
        status_label = "待上课"
        status = "upcoming"
        if session_date < today:
            status_label = "已上课"
            status = "completed"
        elif session_date == today:
            status_label = "今天上课"
            status = "current"
        start_at = datetime.combine(session_date, time.min)
        display_title = f"{_month_day_label(session_date)} {weekday_label(session_date.weekday())} 上 {title}"
        lesson_item = _normalize_item(
            source_type=TODO_SOURCE_LESSON,
            source_id=session.get("id") or session.get("order_index"),
            title=display_title,
            subtitle=session_number or "教学安排",
            start_at=start_at,
            due_at=start_at,
            created_at=start_at,
            status=status,
            status_label=status_label,
            tone="lesson",
            is_completed=session_date < today,
            metadata={"session_order": session.get("order_index"), "session_date": session_date.isoformat()},
            now=now,
        )
        lesson_item["deadline_label"] = _datetime_label(start_at, with_time=False)
        lesson_item["relative_due_label"] = status_label
        lesson_item["duration_label"] = f"{_datetime_label(start_at, with_time=False)} 上课"
        items.append(lesson_item)
    return items


def _assignment_items(conn: sqlite3.Connection, *, class_offering_id: int, user: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    rows = _load_assignment_rows(conn, class_offering_id=class_offering_id, user=user)
    role = str(user.get("role") or "").strip().lower()
    items = []
    for row in rows:
        assignment_id = row["id"]
        is_exam = bool(row.get("exam_paper_id"))
        title = str(row.get("title") or row.get("exam_paper_title") or ("考试" if is_exam else "作业")).strip()
        start_at = parse_datetime_input(row.get("starts_at") or row.get("created_at"), "开始时间")
        due_at = parse_datetime_input(row.get("due_at"), "截止时间")
        created_at = parse_datetime_input(row.get("created_at"), "创建时间")
        if role == "student":
            submission_status = str(row.get("submission_status") or "unsubmitted")
            can_resubmit = bool(_safe_int(row.get("resubmission_allowed")) and row.get("resubmission_due_at"))
            status_label = {
                "submitted": "已提交",
                "grading": "批改中",
                "graded": "已评分",
                "returned": "待重交",
            }.get("returned" if can_resubmit else submission_status, "待提交")
            is_completed = submission_status in {"submitted", "grading", "graded"} and not can_resubmit
        else:
            status = str(row.get("status") or "")
            status_label = {
                "new": "草稿",
                "published": "已发布",
                "closed": "已截止",
            }.get(status, status or "任务")
            is_completed = status == "closed"

        items.append(
            _normalize_item(
                source_type=TODO_SOURCE_ASSIGNMENT,
                source_id=assignment_id,
                title=title,
                subtitle="考试截止" if is_exam else "作业截止",
                start_at=start_at,
                due_at=due_at,
                created_at=created_at,
                link_url=f"/assignment/{assignment_id}",
                status=str(row.get("status") or ""),
                status_label=status_label,
                tone="exam" if is_exam else "assignment",
                is_completed=is_completed,
                metadata={
                    "assignment_id": assignment_id,
                    "is_exam": is_exam,
                    "availability_mode": row.get("availability_mode") or "",
                },
                now=now,
            )
        )
    return items


def _stage_items(conn: sqlite3.Connection, *, class_offering_id: int, student_id: int, now: datetime) -> list[dict[str, Any]]:
    attempts = _load_stage_attempts(conn, class_offering_id=class_offering_id, student_id=student_id)
    items = []
    for attempt in attempts:
        stage_key = str(attempt.get("stage_key") or "")
        level = public_level_payload(get_learning_level(stage_key))
        generated_at = parse_datetime_input(attempt.get("generated_at"), "生成时间")
        status = str(attempt.get("status") or "")
        status_label = {
            "generating": "生成中",
            "generated": "待作答",
            "submitted": "已提交",
            "grading": "批改中",
            "failed": "待重试",
        }.get(status, status or "试炼")
        assignment_id = _safe_int(attempt.get("assignment_id"))
        items.append(
            _normalize_item(
                source_type=TODO_SOURCE_STAGE,
                source_id=attempt["id"],
                title=f"{level['name']}破境试炼",
                subtitle="个人等级试炼",
                start_at=generated_at,
                due_at=None,
                created_at=generated_at,
                link_url=f"/exam/take/{assignment_id}" if assignment_id else "",
                status=status,
                status_label=status_label,
                tone="stage",
                is_completed=status in {"submitted", "grading"},
                metadata={
                    "attempt_id": attempt["id"],
                    "assignment_id": assignment_id,
                    "stage_key": level["key"],
                },
                now=now,
            )
        )
    return items


def _manual_items(rows: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    items = []
    for row in rows:
        start_at = parse_datetime_input(row.get("start_at"), "开始时间")
        due_at = parse_datetime_input(row.get("due_at"), "截止时间")
        created_at = parse_datetime_input(row.get("created_at"), "创建时间")
        completed_at = parse_datetime_input(row.get("completed_at"), "完成时间")
        status_label = "已完成" if completed_at else ("临近截止" if due_at and due_at >= now else "自定义")
        items.append(
            _normalize_item(
                source_type=TODO_SOURCE_MANUAL,
                source_id=row["id"],
                title=str(row.get("title") or "自定义待办"),
                subtitle="我的待办",
                notes=str(row.get("notes") or ""),
                start_at=start_at,
                due_at=due_at,
                created_at=created_at,
                status="completed" if completed_at else "open",
                status_label=status_label,
                tone="manual",
                is_manual=True,
                is_completed=completed_at is not None,
                can_complete=True,
                metadata={"todo_id": row["id"]},
                now=now,
            )
        )
    return items


def _build_week_rows(
    *,
    items: list[dict[str, Any]],
    semester_start: date | None,
    semester_end: date | None,
    today: date,
) -> list[dict[str, Any]]:
    date_candidates: list[date] = [today]
    if semester_start:
        date_candidates.append(semester_start)
    if semester_end:
        date_candidates.append(semester_end)
    for item in items:
        for key in ("effective_start_date", "effective_end_date"):
            parsed = parse_date_input(item.get(key))
            if parsed:
                date_candidates.append(parsed)

    min_date = min(date_candidates)
    max_date = max(date_candidates)
    calendar_start = _week_start(semester_start or min_date)
    calendar_end = _week_start(semester_end or max_date) + timedelta(days=6)
    if calendar_end < calendar_start:
        calendar_end = calendar_start + timedelta(days=6)

    weeks = []
    current = calendar_start
    index = 1
    while current <= calendar_end and index <= 36:
        week_items = []
        for item in items:
            if not _todo_overlaps_week(item, current):
                continue
            positioned = {**item, **_bar_position_for_week(item, current)}
            week_items.append(positioned)
        week_items.sort(key=_sort_key)
        weeks.append(
            {
                "key": current.isoformat(),
                "week_index": index,
                "label": f"第 {index} 周",
                "range_label": f"{_month_day_label(current)} - {_month_day_label(current + timedelta(days=6))}",
                "days": _build_calendar_days(current, today),
                "todos": week_items,
                "todo_count": len(week_items),
                "open_count": sum(1 for item in week_items if not item.get("is_completed")),
                "is_current": current <= today <= current + timedelta(days=6),
            }
        )
        current += timedelta(days=7)
        index += 1
    return weeks


def _pick_active_week(weeks: list[dict[str, Any]], items: list[dict[str, Any]], now: datetime) -> str:
    today_key = now.date().isoformat()
    current_week_key = ""
    for week in weeks:
        if week["is_current"]:
            current_week_key = str(week["key"])
            if int(week.get("open_count") or 0) > 0:
                return current_week_key
            break
    upcoming = [
        item
        for item in items
        if not item.get("is_completed")
        and str(item.get("effective_end_at") or item.get("effective_start_at") or "") >= now.isoformat(timespec="minutes")
    ]
    upcoming.sort(key=_sort_key)
    if upcoming:
        target_date = parse_date_input(upcoming[0].get("effective_start_date")) or now.date()
        target_week = _week_start(target_date).isoformat()
        if any(week["key"] == target_week for week in weeks):
            return target_week
    if current_week_key:
        return current_week_key
    for week in weeks:
        if any(day["date"] == today_key for day in week["days"]):
            return str(week["key"])
    return str(weeks[0]["key"]) if weeks else ""


def build_classroom_todo_overview(
    conn: sqlite3.Connection,
    *,
    class_offering_id: int,
    user: dict[str, Any],
    teaching_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = china_now().replace(tzinfo=None)
    today = now.date()
    plan = teaching_plan or _load_teaching_plan(conn, int(class_offering_id))
    semester_start, semester_end = _load_offering_calendar_bounds(conn, int(class_offering_id))

    items: list[dict[str, Any]] = []
    items.extend(_lesson_items(plan, now))
    items.extend(_assignment_items(conn, class_offering_id=int(class_offering_id), user=user, now=now))
    if str(user.get("role") or "").strip().lower() == "student":
        items.extend(_stage_items(conn, class_offering_id=int(class_offering_id), student_id=int(user["id"]), now=now))
    items.extend(_manual_items(_load_manual_todos(conn, class_offering_id=int(class_offering_id), user=user), now))

    items.sort(key=_sort_key)
    weeks = _build_week_rows(items=items, semester_start=semester_start, semester_end=semester_end, today=today)
    active_week_key = _pick_active_week(weeks, items, now)
    due_soon_cutoff = now + timedelta(days=7)
    due_soon_count = sum(
        1
        for item in items
        if not item.get("is_completed")
        and item.get("due_at")
        and now <= (parse_datetime_input(item.get("due_at")) or now) <= due_soon_cutoff
    )

    return {
        "generated_at": now.isoformat(timespec="minutes"),
        "active_week_key": active_week_key,
        "items": items,
        "weeks": weeks,
        "summary": {
            "total_count": len(items),
            "open_count": sum(1 for item in items if not item.get("is_completed")),
            "manual_count": sum(1 for item in items if item.get("source_type") == TODO_SOURCE_MANUAL),
            "due_soon_count": due_soon_count,
            "no_deadline_count": sum(1 for item in items if item.get("no_deadline")),
        },
        "role_policy": {
            "can_create_manual": str(user.get("role") or "").strip().lower() == "student",
            "show_student_stage_exams": str(user.get("role") or "").strip().lower() == "student",
            "description": (
                "学生端显示课程安排、待提交任务、个人试炼和自定义待办。"
                if str(user.get("role") or "").strip().lower() == "student"
                else "教师端显示课程安排和课堂任务截止，不展示学生个人试炼与学生自定义待办。"
            ),
        },
    }


def create_manual_todo(
    conn: sqlite3.Connection,
    *,
    class_offering_id: int,
    user: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    role = str(user.get("role") or "").strip().lower()
    if role != "student":
        raise PermissionError("当前仅学生可以添加自己的待办事项")

    title = _clean_text(payload.get("title"), max_length=TODO_MAX_TITLE_LENGTH, field_name="待办名称", required=True)
    notes = _clean_text(payload.get("notes"), max_length=TODO_MAX_NOTES_LENGTH, field_name="备注")
    start_at = parse_datetime_input(payload.get("start_at"), "开始时间")
    due_at = parse_datetime_input(payload.get("due_at"), "截止时间")
    if start_at and due_at and due_at < start_at:
        raise TodoValidationError("截止时间不能早于开始时间")

    timestamp = _now_iso()
    cursor = conn.execute(
        """
        INSERT INTO classroom_todos (
            class_offering_id, owner_role, owner_user_pk,
            title, notes, start_at, due_at, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(class_offering_id),
            role,
            int(user["id"]),
            title,
            notes,
            start_at.isoformat(timespec="minutes") if start_at else None,
            due_at.isoformat(timespec="minutes") if due_at else None,
            timestamp,
            timestamp,
        ),
    )
    todo_id = int(cursor.lastrowid)
    if due_at:
        create_todo_notification(
            conn,
            recipient_role=role,
            recipient_user_pk=int(user["id"]),
            title=f"已加入待办：{title}",
            body_preview=f"{_datetime_label(due_at)} 截止",
            link_url=f"/classroom/{int(class_offering_id)}#timeline-panel",
            class_offering_id=int(class_offering_id),
            ref_id=f"manual-todo:{todo_id}:created",
            actor_role=role,
            actor_user_pk=int(user["id"]),
            actor_display_name=str(user.get("name") or "学生"),
            metadata={"todo_id": todo_id, "due_at": due_at.isoformat(timespec="minutes")},
        )
    return {"id": todo_id, "message": "待办已添加。"}


def update_manual_todo(
    conn: sqlite3.Connection,
    *,
    class_offering_id: int,
    todo_id: int,
    user: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT *
        FROM classroom_todos
        WHERE id = ?
          AND class_offering_id = ?
          AND owner_role = ?
          AND owner_user_pk = ?
          AND deleted_at IS NULL
        LIMIT 1
        """,
        (int(todo_id), int(class_offering_id), str(user.get("role") or ""), int(user["id"])),
    ).fetchone()
    if not row:
        raise LookupError("待办不存在或无权操作")

    current = dict(row)
    title = (
        _clean_text(payload.get("title"), max_length=TODO_MAX_TITLE_LENGTH, field_name="待办名称", required=True)
        if "title" in payload
        else str(current.get("title") or "")
    )
    notes = (
        _clean_text(payload.get("notes"), max_length=TODO_MAX_NOTES_LENGTH, field_name="备注")
        if "notes" in payload
        else str(current.get("notes") or "")
    )
    start_at = parse_datetime_input(payload.get("start_at"), "开始时间") if "start_at" in payload else parse_datetime_input(current.get("start_at"))
    due_at = parse_datetime_input(payload.get("due_at"), "截止时间") if "due_at" in payload else parse_datetime_input(current.get("due_at"))
    if start_at and due_at and due_at < start_at:
        raise TodoValidationError("截止时间不能早于开始时间")

    completed_at = current.get("completed_at")
    if "completed" in payload:
        completed_at = _now_iso() if bool(payload.get("completed")) else None

    timestamp = _now_iso()
    conn.execute(
        """
        UPDATE classroom_todos
        SET title = ?,
            notes = ?,
            start_at = ?,
            due_at = ?,
            completed_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            title,
            notes,
            start_at.isoformat(timespec="minutes") if start_at else None,
            due_at.isoformat(timespec="minutes") if due_at else None,
            completed_at,
            timestamp,
            int(todo_id),
        ),
    )
    return {"id": int(todo_id), "message": "待办已更新。"}


def delete_manual_todo(
    conn: sqlite3.Connection,
    *,
    class_offering_id: int,
    todo_id: int,
    user: dict[str, Any],
) -> dict[str, Any]:
    timestamp = _now_iso()
    cursor = conn.execute(
        """
        UPDATE classroom_todos
        SET deleted_at = ?,
            updated_at = ?
        WHERE id = ?
          AND class_offering_id = ?
          AND owner_role = ?
          AND owner_user_pk = ?
          AND deleted_at IS NULL
        """,
        (timestamp, timestamp, int(todo_id), int(class_offering_id), str(user.get("role") or ""), int(user["id"])),
    )
    if cursor.rowcount <= 0:
        raise LookupError("待办不存在或无权操作")
    return {"id": int(todo_id), "message": "待办已删除。"}
