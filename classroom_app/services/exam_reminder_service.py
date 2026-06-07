"""Exam / invigilation email-reminder orchestration.

Bridges a teacher calendar event (synced invigilation or course exam) to the
unified scheduler: it builds a clean, structured reminder detail and schedules a
one-shot ``exam_email_reminder`` task that fires ``lead`` before the start time.

``build_event_reminder_detail`` is also reused by the dashboard agenda builder so
the popover and the email speak the same structured language (科目/日期/时间/
教室/校区/监考分工).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any

from ..database import get_db_connection
from .scheduled_task_handlers import TASK_KIND_EXAM_EMAIL_REMINDER
from .scheduled_task_service import (
    cancel_tasks_by_dedupe,
    get_owner_task_by_dedupe,
    schedule_task,
)

_WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

LEAD_UNIT_SECONDS = {"minute": 60, "hour": 3600, "day": 86400}
LEAD_UNIT_LABELS = {"minute": "分钟", "hour": "小时", "day": "天"}
MAX_LEAD_SECONDS = 30 * 86400  # 30 days


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _safe_metadata(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_dt(value: Any) -> datetime | None:
    text = _text(value).replace(" ", "T")
    if not text:
        return None
    try:
        return datetime.fromisoformat(text[:19])
    except ValueError:
        return None


def _strip_subject_prefix(title: Any) -> str:
    return re.sub(r"^教务(?:考试|监考)[：:]\s*|^监考[：:]\s*", "", _text(title)).strip()


def _compose_invigilators(metadata: dict[str, Any]) -> str:
    invigilators = _text(metadata.get("invigilators"))
    if invigilators:
        return invigilators
    chief = _text(metadata.get("chief_invigilator"))
    assistant = _text(metadata.get("assistant_invigilator"))
    parts = []
    if chief:
        parts.append(f"主监考：{chief}")
    if assistant:
        parts.append(f"副监考：{assistant}")
    return "；".join(parts)


def build_event_reminder_detail(event: dict[str, Any]) -> dict[str, Any]:
    """Normalise a teacher_calendar_events row into structured reminder fields."""
    metadata = _safe_metadata(event.get("metadata_json") or event.get("metadata"))
    source_type = _text(event.get("source_type"))
    kind = "invigilation" if source_type == "academic_invigilation" else "exam"

    subject = (
        _strip_subject_prefix(event.get("title"))
        or _text(metadata.get("course_name"))
        or _text(event.get("subtitle"))
        or ("监考安排" if kind == "invigilation" else "考试安排")
    )

    start_dt = _parse_dt(event.get("starts_at")) or _parse_dt(event.get("due_at"))
    end_dt = _parse_dt(event.get("ends_at"))
    date_label = ""
    time_label = ""
    when_text = ""
    if start_dt:
        date_label = f"{start_dt.month}月{start_dt.day}日 {_WEEKDAYS[start_dt.weekday()]}"
        start_hm = f"{start_dt.hour:02d}:{start_dt.minute:02d}"
        if end_dt and end_dt.date() == start_dt.date() and end_dt.time() != start_dt.time():
            time_label = f"{start_hm}-{end_dt.hour:02d}:{end_dt.minute:02d}"
        else:
            time_label = start_hm
        when_text = f"{start_dt.month}月{start_dt.day}日 {start_hm}"

    campus = _text(metadata.get("campus"))
    classroom = (
        _text(metadata.get("room"))
        or _text(metadata.get("location_full"))
        or _text(event.get("location"))
    )
    # If the room text already embeds the campus, do not duplicate it.
    if campus and classroom.startswith(campus):
        classroom = classroom[len(campus):].strip(" ·-")

    return {
        "kind": kind,
        "subject": subject,
        "date_label": date_label,
        "time_label": time_label,
        "when_text": when_text,
        "campus": campus,
        "classroom": classroom,
        "teaching_class": _text(metadata.get("teaching_class_name")),
        "invigilators": _compose_invigilators(metadata),
        "role": _text(metadata.get("role")),
        "start_at": start_dt.isoformat(timespec="minutes") if start_dt else "",
        "link_url": _text(event.get("link_url")) or "/dashboard#dashboard-semester",
    }


def _load_owned_event(conn, *, teacher_id: int, calendar_event_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT *
        FROM teacher_calendar_events
        WHERE id = ? AND teacher_id = ?
          AND status = 'active' AND deleted_at IS NULL
        LIMIT 1
        """,
        (int(calendar_event_id), int(teacher_id)),
    ).fetchone()
    return dict(row) if row else None


def _reminder_dedupe_key(teacher_id: int, calendar_event_id: int) -> str:
    return f"exam-reminder:{int(teacher_id)}:{int(calendar_event_id)}"


def schedule_exam_email_reminder(
    *,
    teacher_id: int,
    calendar_event_id: int,
    lead_value: int,
    lead_unit: str,
) -> dict[str, Any]:
    unit = str(lead_unit or "").strip().lower()
    if unit not in LEAD_UNIT_SECONDS:
        return {"status": "invalid", "message": "提醒单位只支持分钟、小时或天。"}
    try:
        lead_value_int = int(lead_value)
    except (TypeError, ValueError):
        return {"status": "invalid", "message": "请输入有效的提前时间。"}
    if lead_value_int <= 0:
        return {"status": "invalid", "message": "提前时间需要大于 0。"}
    lead_seconds = lead_value_int * LEAD_UNIT_SECONDS[unit]
    if lead_seconds > MAX_LEAD_SECONDS:
        return {"status": "invalid", "message": "提前时间最多支持 30 天。"}

    with get_db_connection() as conn:
        event = _load_owned_event(conn, teacher_id=int(teacher_id), calendar_event_id=int(calendar_event_id))
        if not event:
            return {"status": "not_found", "message": "未找到对应的监考/考试安排，可能已被更新，请刷新后重试。"}
        detail = build_event_reminder_detail(event)
        start_dt = _parse_dt(event.get("starts_at")) or _parse_dt(event.get("due_at"))
        if not start_dt:
            return {"status": "no_start_time", "message": "该安排暂无明确开始时间，无法设置定时提醒。"}
        now = datetime.now()
        if start_dt <= now:
            return {"status": "past", "message": "该安排已经开始或结束，无法再设置提醒。"}

        run_at = start_dt - timedelta(seconds=lead_seconds)
        # If the chosen lead already passed but the exam is still upcoming, fire soon.
        if run_at <= now:
            run_at = now + timedelta(seconds=30)

        lead_label = f"{lead_value_int}{LEAD_UNIT_LABELS[unit]}"
        payload = {
            "teacher_id": int(teacher_id),
            "calendar_event_id": int(calendar_event_id),
            "lead_label": lead_label,
            "lead_seconds": lead_seconds,
            **detail,
        }
        task_id = schedule_task(
            conn,
            task_kind=TASK_KIND_EXAM_EMAIL_REMINDER,
            run_at=run_at,
            payload=payload,
            dedupe_key=_reminder_dedupe_key(teacher_id, calendar_event_id),
            owner_role="teacher",
            owner_user_pk=int(teacher_id),
            title=f"{detail.get('kind') == 'invigilation' and '监考' or '考试'}邮件提醒：{detail.get('subject')}"[:120],
            max_attempts=4,
        )
        conn.commit()

    return {
        "status": "success",
        "message": f"已设置邮件提醒：将在开始前 {lead_label} 发送到你的邮箱。",
        "task_id": task_id,
        "run_at": run_at.isoformat(timespec="minutes"),
        "lead_label": lead_label,
        "detail": detail,
    }


def cancel_exam_email_reminder(*, teacher_id: int, calendar_event_id: int) -> dict[str, Any]:
    with get_db_connection() as conn:
        cancelled = cancel_tasks_by_dedupe(conn, _reminder_dedupe_key(teacher_id, calendar_event_id))
        conn.commit()
    if cancelled:
        return {"status": "success", "message": "已取消该安排的邮件提醒。", "cancelled_count": cancelled}
    return {"status": "noop", "message": "当前没有可取消的邮件提醒。", "cancelled_count": 0}


def get_exam_email_reminder_state(*, teacher_id: int, calendar_event_id: int) -> dict[str, Any]:
    with get_db_connection() as conn:
        task = get_owner_task_by_dedupe(conn, _reminder_dedupe_key(teacher_id, calendar_event_id))
    if not task or str(task.get("status")) not in {"pending", "running"}:
        return {"has_reminder": False}
    return {
        "has_reminder": True,
        "run_at": str(task.get("run_at") or ""),
        "status": str(task.get("status") or ""),
    }
