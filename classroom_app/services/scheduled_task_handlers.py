"""Handlers for the unified scheduler.

Importing this module registers every built-in task kind. Each handler is small
and fast: it performs the side effect for a due task and returns a short result
string. New timed features register their handler here (or via
``register_task_handler`` from their own module).
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any

from ..config import PUBLIC_SITE_BASE_URL, SITE_DISPLAY_NAME
from ..database import get_db_connection
from .email_notification_service import queue_custom_teacher_email
from .scheduled_task_service import register_task_handler

TASK_KIND_EXAM_EMAIL_REMINDER = "exam_email_reminder"


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _absolute_link(link_url: Any) -> str:
    raw = _text(link_url) or "/dashboard"
    if raw.startswith(("http://", "https://")):
        return raw
    base = str(PUBLIC_SITE_BASE_URL or "").rstrip("/")
    return f"{base}{raw if raw.startswith('/') else '/' + raw}" if base else raw


def _calendar_event_still_active(teacher_id: int, calendar_event_id: int) -> bool:
    """A reminder must not fire for an exam that was cancelled/removed."""
    if not calendar_event_id:
        return True
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT status, deleted_at
            FROM teacher_calendar_events
            WHERE id = ? AND teacher_id = ?
            LIMIT 1
            """,
            (int(calendar_event_id), int(teacher_id)),
        ).fetchone()
    if row is None:
        return False
    return str(row["status"] or "") == "active" and not row["deleted_at"]


def _build_reminder_email(payload: dict[str, Any]) -> tuple[str, str, str]:
    kind = _text(payload.get("kind")) or "invigilation"
    kind_label = "监考" if kind == "invigilation" else "考试"
    subject_name = _text(payload.get("subject")) or f"{kind_label}安排"
    lead_label = _text(payload.get("lead_label"))

    detail_pairs: list[tuple[str, str]] = []
    for label, key in (
        ("科目", "subject"),
        ("日期", "date_label"),
        ("时间", "time_label"),
        ("校区", "campus"),
        ("教室", "classroom"),
        ("教学班", "teaching_class"),
        ("监考分工", "invigilators"),
        ("角色", "role"),
    ):
        value = _text(payload.get(key))
        if value:
            detail_pairs.append((label, value))

    action_url = _absolute_link(payload.get("link_url"))
    subject_line = f"【{kind_label}提醒】{subject_name}"
    when_text = _text(payload.get("when_text")) or _text(payload.get("date_label"))
    if when_text:
        subject_line = f"{subject_line} · {when_text}"

    lines = [
        f"老师，您好：",
        "",
        f"您有一项{kind_label}安排即将开始" + (f"（还有 {lead_label}）。" if lead_label else "。"),
        "",
    ]
    lines.extend(f"{label}：{value}" for label, value in detail_pairs)
    lines.extend([
        "",
        f"查看详情：{action_url}",
        "",
        f"这封邮件由 {SITE_DISPLAY_NAME} 的定时提醒自动发送。",
    ])
    text_body = "\n".join(lines)

    safe_rows = "".join(
        "<tr>"
        f"<td style=\"padding:7px 12px;color:#64748b;font-size:13px;border-bottom:1px solid #e2e8f0;width:84px;white-space:nowrap;\">{html.escape(label)}</td>"
        f"<td style=\"padding:7px 12px;color:#0f172a;font-size:13px;border-bottom:1px solid #e2e8f0;\">{html.escape(value)}</td>"
        "</tr>"
        for label, value in detail_pairs
    )
    safe_site = html.escape(str(SITE_DISPLAY_NAME or "Lanshare"))
    safe_subject = html.escape(subject_name)
    safe_kind = html.escape(kind_label)
    safe_lead = html.escape(lead_label)
    safe_url = html.escape(action_url, quote=True)
    html_body = f"""<!doctype html>
<html lang="zh-CN">
<body style="margin:0;padding:0;background:#edf2fb;color:#0f172a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif;">
  <div style="max-width:600px;margin:0 auto;padding:30px 16px;">
    <div style="border:1px solid #dbe3ef;border-radius:18px;background:#ffffff;padding:26px;box-shadow:0 18px 40px -28px rgba(15,23,42,0.4);">
      <div style="font-size:13px;font-weight:800;color:#4f46e5;margin-bottom:12px;">{safe_site}</div>
      <div style="display:inline-block;padding:5px 12px;border-radius:999px;background:#fff7ed;color:#b45309;font-size:12px;font-weight:800;border:1px solid #fed7aa;">{safe_kind}提醒{(' · 还有 ' + safe_lead) if safe_lead else ''}</div>
      <h1 style="margin:14px 0 16px;font-size:21px;line-height:1.35;color:#0f172a;">{safe_subject}</h1>
      <table role="presentation" style="width:100%;border-collapse:collapse;margin:0 0 22px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;">{safe_rows}</table>
      <a href="{safe_url}" style="display:inline-block;padding:11px 18px;border-radius:10px;background:#4f46e5;color:#ffffff;text-decoration:none;font-size:14px;font-weight:800;">查看详情</a>
      <p style="margin:20px 0 0;font-size:12px;line-height:1.7;color:#64748b;">这封邮件由定时提醒自动发送。</p>
    </div>
  </div>
</body>
</html>"""
    return subject_line, text_body, html_body


def handle_exam_email_reminder(task: dict[str, Any]) -> str:
    """Send the teacher a reminder email for a synced invigilation/exam."""
    payload = task.get("payload") or {}
    teacher_id = int(payload.get("teacher_id") or 0)
    calendar_event_id = int(payload.get("calendar_event_id") or 0)
    if not teacher_id:
        return "skipped: missing teacher"

    if not _calendar_event_still_active(teacher_id, calendar_event_id):
        return "skipped: calendar event no longer active"

    subject_line, text_body, html_body = _build_reminder_email(payload)
    start_at = _text(payload.get("start_at")) or _text(payload.get("when_text"))
    dedupe_key = f"exam-reminder-email:{teacher_id}:{calendar_event_id}:{start_at}"

    with get_db_connection() as conn:
        queued = queue_custom_teacher_email(
            conn,
            teacher_id=teacher_id,
            subject=subject_line,
            body_text=text_body,
            body_html=html_body,
            dedupe_key=dedupe_key,
            category="academic_exam",
            recipient_role="teacher",
            recipient_user_pk=teacher_id,
        )
        conn.commit()
    return f"queued email job {queued.get('job_id')} -> {queued.get('recipient_email')}"


register_task_handler(TASK_KIND_EXAM_EMAIL_REMINDER, handle_exam_email_reminder)


# 校园公文通 recurring incremental sync (keeps the local copy fresh for stats /
# reminders). The handler is async; the dispatcher awaits coroutine results.
from .gongwen_document_sync_service import (  # noqa: E402
    GONGWEN_SYNC_TASK_KIND,
    handle_gongwen_sync_task,
)

register_task_handler(GONGWEN_SYNC_TASK_KIND, handle_gongwen_sync_task)

# 校园公文通 parse queue worker — drains the pending parse backlog in paced batches.
from .gongwen_parse_service import (  # noqa: E402
    GONGWEN_PARSE_TASK_KIND,
    handle_gongwen_parse_task,
)

register_task_handler(GONGWEN_PARSE_TASK_KIND, handle_gongwen_parse_task)
