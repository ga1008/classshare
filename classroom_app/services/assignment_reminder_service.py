"""作业/考试截止临期提醒（学生默认开启）。

发布或修改带截止时间的作业时，同步两个一次性 scheduled task（T-24h、T-2h）。
到点后 scheduler worker 给该课堂尚未提交的在读学生发消息中心站内通知。
同步是 best-effort：调度失败绝不阻塞作业本身的写入。

与 ``exam_reminder_service``（教师监考/考试日历的邮件提醒）互不重叠：
这里面向学生、按作业截止时间触发、走站内通知。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .academic_service import china_now
from .scheduled_task_service import cancel_tasks_by_dedupe, schedule_task

ASSIGNMENT_DUE_REMINDER_TASK_KIND = "assignment_due_reminder"

# (window_label, 提前量, 标题里的时间表述)
REMINDER_WINDOWS: tuple[tuple[str, timedelta, str], ...] = (
    ("24h", timedelta(hours=24), "将在 24 小时后"),
    ("2h", timedelta(hours=2), "将在 2 小时后"),
)


def reminder_window_display(window_label: str) -> str:
    for label, _delta, display in REMINDER_WINDOWS:
        if label == window_label:
            return display
    return "即将"


def _dedupe_key(assignment_id: int | str, window_label: str) -> str:
    return f"assignment-due-reminder:{assignment_id}:{window_label}"


def parse_due_at(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "").replace("T", " ").strip())
    except ValueError:
        return None


def sync_assignment_due_reminders(
    conn,
    assignment_id: int | str,
    *,
    status: Any,
    due_at: Any,
    class_offering_id: Any,
    title: str = "",
) -> None:
    """Arm or cancel the due reminders so they always mirror the latest
    (status, due_at). Idempotent via dedupe_key + replace=True."""
    now = china_now().replace(tzinfo=None)
    due = parse_due_at(due_at)
    is_active = (
        str(status or "").strip().lower() == "published"
        and due is not None
        and due > now
        and bool(class_offering_id)
    )
    try:
        for window_label, delta, _display in REMINDER_WINDOWS:
            key = _dedupe_key(assignment_id, window_label)
            run_at = (due - delta) if due is not None else None
            if is_active and run_at is not None and run_at > now:
                schedule_task(
                    conn,
                    task_kind=ASSIGNMENT_DUE_REMINDER_TASK_KIND,
                    run_at=run_at,
                    payload={
                        "assignment_id": int(assignment_id),
                        "window": window_label,
                        "due_at": due.isoformat(timespec="seconds"),
                    },
                    dedupe_key=key,
                    owner_role="teacher",
                    title=f"作业临期提醒（{window_label}）：{title}"[:120],
                    replace=True,
                )
            else:
                cancel_tasks_by_dedupe(conn, key)
    except Exception:
        # 提醒是增强项，调度异常不允许影响作业保存。
        pass


def cancel_assignment_due_reminders(conn, assignment_id: int | str) -> None:
    try:
        for window_label, _delta, _display in REMINDER_WINDOWS:
            cancel_tasks_by_dedupe(conn, _dedupe_key(assignment_id, window_label))
    except Exception:
        pass
