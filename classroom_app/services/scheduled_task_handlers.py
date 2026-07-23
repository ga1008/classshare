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


TASK_KIND_LIFE_TIP_GONGWEN_REFRESH = "life_tip_gongwen_refresh"


async def handle_life_tip_gongwen_refresh(task: dict[str, Any]) -> str:
    """公文 → 登录"人生一言"提示语挖掘（重逻辑惰性导入，保持 worker 启动轻量）。"""
    from .life_tip_generation_service import handle_life_tip_generation_task

    return await handle_life_tip_generation_task(task)


register_task_handler(TASK_KIND_LIFE_TIP_GONGWEN_REFRESH, handle_life_tip_gongwen_refresh)


# Value must match todo_service.TODO_DUE_REMINDER_TASK_KIND (kept as a literal
# here to avoid importing the heavier todo_service into the worker boot path).
TASK_KIND_TODO_DUE_REMINDER = "todo_due_reminder"


def _todo_due_labels(due_at_text: str) -> tuple[str, str]:
    """Return ("M月D日 HH:MM", "还有 N 天/小时/分钟" | "已超时")."""
    from .academic_service import china_now

    raw = _text(due_at_text)
    if not raw:
        return "", ""
    try:
        due = datetime.fromisoformat(raw.replace("Z", "").replace("T", " ").strip())
    except (TypeError, ValueError):
        return raw, ""
    when = f"{due.month}月{due.day}日 {due.hour:02d}:{due.minute:02d}"
    now = china_now().replace(tzinfo=None)
    minutes = int((due - now).total_seconds() // 60)
    if minutes < 0:
        relative = "已超时"
    elif minutes < 60:
        relative = f"还有 {minutes} 分钟"
    elif minutes < 60 * 24:
        relative = f"还有 {minutes // 60} 小时"
    else:
        relative = f"还有 {minutes // (60 * 24)} 天"
    return when, relative


def handle_todo_due_reminder(task: dict[str, Any]) -> str:
    """Deliver the selected in-app/email channels for a manual to-do."""
    payload = task.get("payload") or {}
    todo_id = int(payload.get("todo_id") or 0)
    raw_class_offering_id = payload.get("class_offering_id")
    class_offering_id = (
        int(raw_class_offering_id)
        if raw_class_offering_id not in (None, "", 0, "0")
        else None
    )
    role = _text(payload.get("owner_role")).lower()
    user_pk = int(payload.get("owner_user_pk") or 0)
    if not (todo_id and user_pk and role):
        return "skipped: missing ids"

    from .message_center_service import create_todo_notification

    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT title, notes, due_at, completed_at, deleted_at, metadata_json
            FROM classroom_todos
            WHERE id = ?
              AND (class_offering_id = ? OR (class_offering_id IS NULL AND ? IS NULL))
              AND owner_role = ?
              AND owner_user_pk = ?
            LIMIT 1
            """,
            (todo_id, class_offering_id, class_offering_id, role, user_pk),
        ).fetchone()
        if row is None:
            return "skipped: todo missing"
        if row["deleted_at"]:
            return "skipped: deleted"
        if row["completed_at"]:
            return "skipped: completed"
        due_at_text = _text(row["due_at"])
        if not due_at_text:
            return "skipped: no deadline"

        row_data = dict(row)
        title = _text(row_data.get("title")) or "待办"
        notes = _text(row_data.get("notes"))
        try:
            import json

            metadata = json.loads(str(row_data.get("metadata_json") or "{}"))
        except (KeyError, TypeError, ValueError):
            metadata = {}
        reminder = metadata.get("reminder") if isinstance(metadata, dict) else {}
        reminder = reminder if isinstance(reminder, dict) else {}
        # Older scheduled tasks predate channel metadata and were always
        # in-app reminders, so keep that behaviour for compatibility.
        in_app_enabled = bool(reminder.get("enabled", True))
        email_enabled = bool(reminder.get("email_enabled", False)) and role == "teacher"
        when_label, relative = _todo_due_labels(due_at_text)
        body = f"{relative}（{when_label} 截止）" if relative else f"{when_label} 截止"
        created = 0
        if in_app_enabled:
            created = create_todo_notification(
                conn,
                recipient_role=role,
                recipient_user_pk=user_pk,
                title=f"待办即将到期：{title}",
                body_preview=body,
                link_url="/dashboard#dashboard-class-list",
                class_offering_id=class_offering_id or None,
                ref_id=f"todo-due:{todo_id}:{due_at_text}",
                actor_role="system",
                actor_display_name="到期提醒",
            )
        email_job_id = None
        email_error: Exception | None = None
        if email_enabled:
            action_url = _absolute_link("/dashboard")
            safe_title = html.escape(title)
            safe_when = html.escape(when_label)
            safe_notes = html.escape(notes)
            safe_action_url = html.escape(action_url, quote=True)
            note_html = (
                f'<div style="margin:16px 0;padding:12px 14px;border-radius:10px;background:#f8fafc;'
                f'color:#475569;line-height:1.7;">{safe_notes}</div>'
                if safe_notes
                else ""
            )
            try:
                email_result = queue_custom_teacher_email(
                    conn,
                    teacher_id=user_pk,
                    subject=f"【{SITE_DISPLAY_NAME}】待办提醒：{title}",
                    body_text=(
                        f"老师，你好：\n\n待办“{title}”{body}。"
                        + (f"\n\n备注：{notes}" if notes else "")
                        + f"\n\n打开教师工作台：{action_url}"
                    ),
                    body_html=f"""<!doctype html>
<html lang="zh-CN"><body style="margin:0;background:#eef2f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif;color:#0f172a;">
<div style="max-width:620px;margin:0 auto;padding:32px 18px;"><div style="padding:28px;border:1px solid #dbe3ef;border-radius:20px;background:#fff;box-shadow:0 18px 45px -32px rgba(15,23,42,.5);">
<div style="font-size:12px;font-weight:800;color:#0f766e;letter-spacing:.08em;">{html.escape(str(SITE_DISPLAY_NAME))} · 待办提醒</div>
<h1 style="margin:14px 0 8px;font-size:23px;line-height:1.4;">{safe_title}</h1>
<p style="margin:0;color:#64748b;font-size:15px;line-height:1.8;">截止时间：{safe_when}</p>{note_html}
<a href="{safe_action_url}" style="display:inline-block;margin-top:18px;padding:12px 18px;border-radius:11px;background:#0f766e;color:#fff;text-decoration:none;font-size:14px;font-weight:800;">打开教师工作台</a>
</div></div></body></html>""",
                    dedupe_key=f"todo-reminder-email:{user_pk}:{todo_id}:{due_at_text}",
                    category="todo",
                    recipient_role="teacher",
                    recipient_user_pk=user_pk,
                )
                email_job_id = email_result.get("job_id")
            except Exception as exc:  # Scheduler will retry; keep an in-app fallback visible.
                email_error = exc
                create_todo_notification(
                    conn,
                    recipient_role="teacher",
                    recipient_user_pk=user_pk,
                    title=f"待办邮件提醒发送失败：{title}",
                    body_preview="系统会自动重试；请检查账号邮箱与默认发信配置。",
                    link_url="/profile?section=email",
                    class_offering_id=class_offering_id or None,
                    ref_id=f"todo-email-retry:{todo_id}:{due_at_text}",
                    actor_role="system",
                    actor_display_name="邮件提醒",
                )
        conn.commit()
    if email_error is not None:
        raise RuntimeError("todo email reminder queue failed; retry scheduled") from email_error
    return f"todo due reminder notified={created} email_job={email_job_id or 0}"


register_task_handler(TASK_KIND_TODO_DUE_REMINDER, handle_todo_due_reminder)


from .cultivation_alert_service import (  # noqa: E402
    CULTIVATION_ALERT_TASK_KIND,
    generate_cultivation_alerts,
)


def handle_cultivation_alert_scan(task: dict[str, Any]) -> str:
    payload = task.get("payload") or {}
    class_offering_id = payload.get("class_offering_id")
    with get_db_connection() as conn:
        result = generate_cultivation_alerts(
            conn,
            class_offering_id=int(class_offering_id) if class_offering_id else None,
            now=payload.get("now"),
        )
        conn.commit()
    return (
        f"generated {result.get('created_or_updated', 0)} cultivation alert(s), "
        f"{result.get('suppressed', 0)} suppressed, {result.get('resolved', 0)} resolved"
    )


register_task_handler(CULTIVATION_ALERT_TASK_KIND, handle_cultivation_alert_scan)


from .learning_progress_service import (  # noqa: E402
    CULTIVATION_SCORE_EVENT_ARCHIVE_TASK_KIND,
    CULTIVATION_SNAPSHOT_REFRESH_TASK_KIND,
    CULTIVATION_WEEKLY_REPORT_TASK_KIND,
    CULTIVATION_WEEKLY_SNAPSHOT_TASK_KIND,
    STAGE_EXAM_GENERATION_TASK_KIND,
    archive_cultivation_score_events,
    capture_cultivation_weekly_snapshots,
    create_cultivation_weekly_reports,
    generate_personal_stage_exam_from_attempt,
    recalculate_dirty_learning_progress_snapshots,
)


def handle_cultivation_snapshot_refresh(task: dict[str, Any]) -> str:
    payload = task.get("payload") or {}
    limit = max(1, min(int(payload.get("limit") or 100), 500))
    with get_db_connection() as conn:
        result = recalculate_dirty_learning_progress_snapshots(conn, limit=limit)
        conn.commit()
    return f"refreshed {result.get('refreshed', 0)} of {result.get('checked', 0)} dirty snapshot(s)"


register_task_handler(CULTIVATION_SNAPSHOT_REFRESH_TASK_KIND, handle_cultivation_snapshot_refresh)


def handle_cultivation_weekly_snapshot(task: dict[str, Any]) -> str:
    payload = task.get("payload") or {}
    class_offering_id = payload.get("class_offering_id")
    with get_db_connection() as conn:
        result = capture_cultivation_weekly_snapshots(
            conn,
            class_offering_id=int(class_offering_id) if class_offering_id else None,
            week_start=payload.get("week_start"),
            refresh_current=bool(payload.get("refresh_current", True)),
        )
        conn.commit()
    return (
        f"captured {result.get('captured', 0)} of {result.get('checked', 0)} "
        f"weekly snapshot(s) for {result.get('week_start')}"
    )


register_task_handler(CULTIVATION_WEEKLY_SNAPSHOT_TASK_KIND, handle_cultivation_weekly_snapshot)


def handle_cultivation_weekly_report(task: dict[str, Any]) -> str:
    payload = task.get("payload") or {}
    class_offering_id = payload.get("class_offering_id")
    with get_db_connection() as conn:
        result = create_cultivation_weekly_reports(
            conn,
            class_offering_id=int(class_offering_id) if class_offering_id else None,
            week_start=payload.get("week_start"),
        )
        conn.commit()
    return (
        f"created {result.get('created', 0)} weekly report(s), "
        f"{result.get('duplicates', 0)} duplicate(s), {result.get('skipped', 0)} skipped"
    )


register_task_handler(CULTIVATION_WEEKLY_REPORT_TASK_KIND, handle_cultivation_weekly_report)


def handle_cultivation_score_event_archive(task: dict[str, Any]) -> str:
    payload = task.get("payload") or {}
    with get_db_connection() as conn:
        result = archive_cultivation_score_events(
            conn,
            retention_days=int(payload.get("retention_days") or 90),
            as_of=payload.get("as_of"),
            batch_limit=int(payload.get("batch_limit") or 500),
        )
        conn.commit()
    return (
        f"archived {result.get('archived_events', 0)} score event(s) "
        f"into {result.get('archive_rows', 0)} bucket(s); "
        f"deleted {result.get('deleted_events', 0)} raw row(s)"
    )


register_task_handler(CULTIVATION_SCORE_EVENT_ARCHIVE_TASK_KIND, handle_cultivation_score_event_archive)


async def handle_stage_exam_generation(task: dict[str, Any]) -> str:
    payload = task.get("payload") or {}
    attempt_id = int(payload.get("attempt_id") or 0)
    if not attempt_id:
        return "skipped: missing attempt_id"
    result = await generate_personal_stage_exam_from_attempt(
        attempt_id,
        task_attempt_count=int(task.get("attempt_count") or 0),
        max_attempts=int(task.get("max_attempts") or 3),
    )
    if result.get("status") == "success":
        return f"generated stage exam assignment {result.get('assignment_id')}"
    return f"{result.get('status')}: {result.get('message') or 'no-op'}"


register_task_handler(STAGE_EXAM_GENERATION_TASK_KIND, handle_stage_exam_generation)


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

# 公文关注匹配 worker — matches freshly parsed documents against teacher follow
# settings (AI 语义项 + 硬关键字) and sends notifications/emails on hits.
from .gongwen_follow_service import (  # noqa: E402
    GONGWEN_FOLLOW_RESCAN_TASK_KIND,
    GONGWEN_FOLLOW_TASK_KIND,
    handle_gongwen_follow_rescan_task,
    handle_gongwen_follow_task,
)

register_task_handler(GONGWEN_FOLLOW_TASK_KIND, handle_gongwen_follow_task)
# 重新发现：教师在关注设置浮窗里手动触发的一次性全量回扫。
register_task_handler(GONGWEN_FOLLOW_RESCAN_TASK_KIND, handle_gongwen_follow_rescan_task)

# 定时/订阅型 Agent 任务 — 到点把订阅模板（周报/公文哨兵/考前提醒包）写入
# agent_tasks 队列，复用全部现有 Agent 执行链路。
from .agent_subscription_service import (  # noqa: E402
    DISPATCH_TASK_KIND as AGENT_TASK_DISPATCH_KIND,
    handle_agent_task_dispatch,
)

register_task_handler(AGENT_TASK_DISPATCH_KIND, handle_agent_task_dispatch)

# 职业发展网络 — 按专业生成网络 + 按学生定制推荐/必备知识（深度思考型 AI，异步）。
# 导入即注册两个 handler（career_major_network_generate / career_personalize_generate）。
from . import career_path_service  # noqa: E402,F401

# 作业/考试截止临期提醒 — T-24h/T-2h 给该课堂尚未提交的学生发站内通知。
from .assignment_reminder_service import (  # noqa: E402
    ASSIGNMENT_DUE_REMINDER_TASK_KIND,
    parse_due_at as _parse_assignment_due_at,
    reminder_window_display,
)


def handle_assignment_due_reminder(task: dict[str, Any]) -> str:
    payload = task.get("payload") or {}
    assignment_id = int(payload.get("assignment_id") or 0)
    window_label = _text(payload.get("window")) or "24h"
    if not assignment_id:
        return "skipped: missing assignment id"

    from .message_center_service import create_assignment_due_reminder_notifications

    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT status, due_at, closed_at, class_offering_id FROM assignments WHERE id = ? LIMIT 1",
            (assignment_id,),
        ).fetchone()
        if row is None:
            return "skipped: assignment missing"
        if _text(row["status"]).lower() != "published" or row["closed_at"]:
            return "skipped: not published"
        due = _parse_assignment_due_at(row["due_at"])
        if due is None:
            return "skipped: no deadline"
        # 截止时间被改过时 sync 会重新布防；万一存在竞态旧任务，这里兜底跳过。
        payload_due = _parse_assignment_due_at(payload.get("due_at"))
        if payload_due is not None and payload_due != due:
            return "skipped: deadline changed"
        from .academic_service import china_now

        if due <= china_now().replace(tzinfo=None):
            return "skipped: past due"
        created = create_assignment_due_reminder_notifications(
            conn,
            assignment_id,
            window_label=window_label,
            window_display=reminder_window_display(window_label),
        )
        conn.commit()
    return f"assignment due reminder notified={created}"


register_task_handler(ASSIGNMENT_DUE_REMINDER_TASK_KIND, handle_assignment_due_reminder)
