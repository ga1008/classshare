"""定时 / 订阅型 Agent 任务（G6）。

预置 3 个订阅模板（不做自由编排，降低理解成本），底层复用统一 scheduler：
到点由 ``agent_task_dispatch`` handler 把任务模板写入 agent_tasks 队列，
复用全部现有执行链路；产出经任务终态通知送达消息中心。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import HTTPException

DISPATCH_TASK_KIND = "agent_task_dispatch"
SUBSCRIPTION_PRIORITY = -1  # 低于手动任务，互不阻塞

DAY_SECONDS = 24 * 3600
WEEK_SECONDS = 7 * DAY_SECONDS
SKIP_NOTIFY_RESULTS = {
    "skipped: no upcoming exams",
    "skipped: subscription backlog",
}

AGENT_SUBSCRIPTION_TEMPLATES: dict[str, dict[str, Any]] = {
    "weekly_report": {
        "label": "每周教学周报",
        "description": "每周一早上汇总上周各课堂作业提交率、低分预警和课堂活跃情况。",
        "cadence": "weekly",
        "weekday": 0,  # Monday
        "default_hour": 8,
    },
    "gongwen_sentinel": {
        "label": "公文哨兵",
        "description": "每天检查新增校园公文中与我相关的文件并生成解读。",
        "cadence": "daily",
        "default_hour": 9,
    },
    "exam_briefing": {
        "label": "考前提醒包",
        "description": "考试/监考前 3 天自动生成监考核对清单和学生注意事项草稿。",
        "cadence": "daily",
        "default_hour": 7,
    },
}


def _dedupe_key(template_key: str, teacher_id: int) -> str:
    return f"agent-sub:{template_key}:{int(teacher_id)}"


def _next_run_at(template: dict[str, Any], hour: int, *, now: datetime | None = None) -> datetime:
    current = now or datetime.now()
    candidate = current.replace(hour=int(hour), minute=0, second=0, microsecond=0)
    if template["cadence"] == "weekly":
        days_ahead = (int(template.get("weekday") or 0) - candidate.weekday()) % 7
        candidate = candidate + timedelta(days=days_ahead)
        if candidate <= current:
            candidate += timedelta(days=7)
        return candidate
    if candidate <= current:
        candidate += timedelta(days=1)
    return candidate


def _recurrence_seconds(template: dict[str, Any]) -> int:
    return WEEK_SECONDS if template["cadence"] == "weekly" else DAY_SECONDS


def _subscription_last_run_message(last_result: Any, last_error: Any = "") -> str:
    result = str(last_result or "").strip()
    error = str(last_error or "").strip()
    if error:
        return f"上次运行失败：{error[:120]}"
    if not result:
        return ""
    if result.startswith("queued agent task"):
        task_id = result.rsplit(" ", 1)[-1]
        return f"已生成 Agent 任务 {task_id}" if task_id.isdigit() else "已生成 Agent 任务"
    if result == "skipped: no upcoming exams":
        return "未来 3 天暂无考试/监考安排，已跳过本次提醒"
    if result == "skipped: subscription backlog":
        return "已有订阅任务仍在排队，已跳过本次运行"
    if result == "skipped: teacher not found":
        return "教师账号不可用，已跳过本次运行"
    if result == "skipped: invalid payload":
        return "订阅配置异常，已跳过本次运行"
    if result == "skipped: unknown template":
        return "订阅模板已不存在，已跳过本次运行"
    if result.startswith("skipped:"):
        return f"已跳过本次运行：{result[8:].strip() or '无可处理内容'}"
    return result[:120]


def _maybe_notify_subscription_skip(
    conn,
    *,
    teacher_id: int,
    template_key: str,
    template: dict[str, Any],
    result: str,
    previous_result: Any = "",
) -> None:
    result_text = str(result or "").strip()
    if result_text not in SKIP_NOTIFY_RESULTS:
        return
    if str(previous_result or "").strip() != result_text:
        return
    message = _subscription_last_run_message(result_text)
    if not message:
        return
    try:
        from .message_center_service import create_todo_notification

        label = str(template.get("label") or template_key or "Agent 订阅")
        create_todo_notification(
            conn,
            recipient_role="teacher",
            recipient_user_pk=int(teacher_id),
            title=f"Agent 订阅提醒：{label}",
            body_preview=f"{message}。最近连续两次没有新产出，可打开定时任务查看或调整订阅。",
            link_url="/?agent_subscriptions=1",
            ref_id=f"agent-subscription-skip:{int(teacher_id)}:{template_key}:{result_text}",
            actor_display_name="LanShare Agent",
            metadata={
                "agent_subscription": template_key,
                "result": result_text,
                "consecutive": 2,
            },
            allow_duplicates=False,
        )
    except Exception as exc:  # noqa: BLE001 - notification is best-effort.
        print(f"[AGENT_SUBSCRIPTION] skip notification failed for teacher {teacher_id}: {exc}")


def list_agent_subscriptions(conn, *, teacher_id: int) -> dict[str, Any]:
    from .scheduled_task_service import ensure_scheduler_schema

    ensure_scheduler_schema(conn)
    subscriptions = []
    for key, template in AGENT_SUBSCRIPTION_TEMPLATES.items():
        row = conn.execute(
            """
            SELECT id, status, run_at, payload_json, last_result, last_error, finished_at, updated_at
            FROM scheduled_tasks
            WHERE dedupe_key = ?
            LIMIT 1
            """,
            (_dedupe_key(key, teacher_id),),
        ).fetchone()
        enabled = bool(row and str(row["status"] or "") in ("pending", "running"))
        hour = template["default_hour"]
        if row:
            import json

            try:
                payload = json.loads(str(row["payload_json"] or "{}"))
                hour = int(payload.get("hour") or hour)
            except (TypeError, ValueError):
                pass
        subscriptions.append(
            {
                "key": key,
                "label": template["label"],
                "description": template["description"],
                "cadence": template["cadence"],
                "enabled": enabled,
                "hour": hour,
                "next_run_at": (row["run_at"] if enabled and row else "") or "",
                "scheduler_status": str(row["status"] or "") if row else "",
                "last_run_message": _subscription_last_run_message(
                    row["last_result"] if row else "",
                    row["last_error"] if row else "",
                ),
                "last_finished_at": (row["finished_at"] if row else "") or "",
            }
        )
    try:
        recent = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id, title, status, created_at
                FROM agent_tasks
                WHERE teacher_id = ? AND origin = 'subscription'
                ORDER BY id DESC
                LIMIT 6
                """,
                (int(teacher_id),),
            ).fetchall()
        ]
    except Exception as exc:
        if "agent_tasks" not in str(exc):
            raise
        recent = []
    return {"subscriptions": subscriptions, "recent_tasks": recent}


def set_agent_subscription(
    conn,
    user: dict[str, Any],
    *,
    template_key: str,
    enabled: bool,
    hour: int | None = None,
) -> dict[str, Any]:
    template = AGENT_SUBSCRIPTION_TEMPLATES.get(str(template_key or ""))
    if not template:
        raise HTTPException(status_code=404, detail="未知的订阅模板。")
    teacher_id = int(user["id"])
    from .scheduled_task_service import cancel_tasks_by_dedupe, schedule_task

    if not enabled:
        cancel_tasks_by_dedupe(conn, _dedupe_key(template_key, teacher_id))
        conn.commit()
        return list_agent_subscriptions(conn, teacher_id=teacher_id)

    safe_hour = max(0, min(int(hour if hour is not None else template["default_hour"]), 23))
    schedule_task(
        conn,
        task_kind=DISPATCH_TASK_KIND,
        run_at=_next_run_at(template, safe_hour),
        payload={"teacher_id": teacher_id, "template_key": template_key, "hour": safe_hour},
        dedupe_key=_dedupe_key(template_key, teacher_id),
        recurrence_seconds=_recurrence_seconds(template),
        owner_role="teacher",
        owner_user_pk=teacher_id,
        title=f"Agent 订阅：{template['label']}",
        replace=True,
    )
    conn.commit()
    return list_agent_subscriptions(conn, teacher_id=teacher_id)


def _weekly_report_instruction(teacher_name: str) -> str:
    today = datetime.now().date()
    last_monday = today - timedelta(days=today.weekday() + 7)
    last_sunday = last_monday + timedelta(days=6)
    return (
        f"生成上周（{last_monday} 至 {last_sunday}）的教学周报。要求：\n"
        "1. 用平台桥接 /query 接口逐项统计：我名下各课堂上周作业的布置数、提交率、60 分以下低分人数与名单（仅统计上周截止或上周有提交的作业）。\n"
        "2. 数字必须全部来自 SQL 查询结果，不允许估算或编造；查询不到就写「无数据」。\n"
        "3. 输出结构：本周概览（3 句话内）→ 各课堂明细表 → 需要关注的学生（连续未交/低分）→ 本周建议。\n"
        "4. 末尾列出使用过的查询和数据时间范围，便于核对。"
    )


def _gongwen_sentinel_instruction(teacher_name: str) -> str:
    return (
        "检查最近 24 小时平台公文库（gongwen_documents 表）新增的公文，"
        f"找出与我相关的：正文或标题提到我的姓名「{teacher_name}」、或涉及教学安排、考试监考、教学检查、材料提交截止等教师必须响应的事项。\n"
        "对每篇命中的公文输出：标题、文号、发文单位、关键要求、建议动作和站内链接（/manage/gongwen）。\n"
        "如果没有新增或没有相关公文，直接简短说明「今日无相关新公文」，不要硬凑内容。"
    )


def _exam_briefing_instruction(events_summary: str) -> str:
    return (
        f"未来 3 天我有以下考试/监考安排：\n{events_summary}\n"
        "请生成：1) 监考安排核对清单（时间、地点、角色、需要提前确认的事项）；"
        "2) 一份面向学生的考前注意事项草稿（可直接作为通知文案）。"
        "信息不足的部分明确标注「需教师补充」。"
    )


def _upcoming_exam_events(conn, teacher_id: int) -> str:
    rows = conn.execute(
        """
        SELECT title, subtitle, starts_at, location, source_type
        FROM teacher_calendar_events
        WHERE teacher_id = ?
          AND status = 'active'
          AND deleted_at IS NULL
          AND starts_at >= ?
          AND starts_at <= ?
        ORDER BY starts_at ASC
        LIMIT 10
        """,
        (
            int(teacher_id),
            datetime.now().isoformat(timespec="seconds"),
            (datetime.now() + timedelta(days=3)).isoformat(timespec="seconds"),
        ),
    ).fetchall()
    lines = []
    for row in rows:
        kind = str(row["source_type"] or "")
        title = str(row["title"] or "")
        is_exam_like = (
            "exam" in kind
            or "invigilation" in kind
            or "考" in title
            or "监考" in str(row["subtitle"] or "")
        )
        if not is_exam_like:
            continue
        lines.append(f"- {row['starts_at']} {title} {row['location'] or ''}".rstrip())
    return "\n".join(lines)


def handle_agent_task_dispatch(task_row: dict[str, Any]) -> str:
    """scheduler handler：到点把订阅模板写入 agent_tasks 队列（小而快）。"""
    import json

    from ..database import get_db_connection
    from .agent_task_service import TASK_ORIGIN_SUBSCRIPTION, create_agent_task

    try:
        payload = json.loads(str(task_row.get("payload_json") or "{}"))
    except (TypeError, ValueError):
        payload = {}
    teacher_id = int(payload.get("teacher_id") or 0)
    template_key = str(payload.get("template_key") or "")
    template = AGENT_SUBSCRIPTION_TEMPLATES.get(template_key)
    if not teacher_id or not template:
        return "skipped: invalid payload"

    with get_db_connection() as conn:
        teacher = conn.execute(
            "SELECT id, name, nickname FROM teachers WHERE id = ? AND COALESCE(is_active, 1) = 1 LIMIT 1",
            (teacher_id,),
        ).fetchone()
        if not teacher:
            return "skipped: teacher not found"
        teacher_name = str(teacher["name"] or teacher["nickname"] or f"教师{teacher_id}")

        if template_key == "weekly_report":
            instruction = _weekly_report_instruction(teacher_name)
            task_type = "general_teaching_task"
        elif template_key == "gongwen_sentinel":
            instruction = _gongwen_sentinel_instruction(teacher_name)
            task_type = "gongwen_lookup"
        elif template_key == "exam_briefing":
            events_summary = _upcoming_exam_events(conn, teacher_id)
            if not events_summary:
                result = "skipped: no upcoming exams"
                _maybe_notify_subscription_skip(
                    conn,
                    teacher_id=teacher_id,
                    template_key=template_key,
                    template=template,
                    result=result,
                    previous_result=task_row.get("last_result"),
                )
                conn.commit()
                return result
            instruction = _exam_briefing_instruction(events_summary)
            task_type = "general_teaching_task"
        else:
            return "skipped: unknown template"

        active = conn.execute(
            """
            SELECT COUNT(*) FROM agent_tasks
            WHERE teacher_id = ? AND origin = 'subscription' AND status IN ('queued', 'running')
            """,
            (teacher_id,),
        ).fetchone()
        if int(active[0] or 0) >= 2:
            result = "skipped: subscription backlog"
            _maybe_notify_subscription_skip(
                conn,
                teacher_id=teacher_id,
                template_key=template_key,
                template=template,
                result=result,
                previous_result=task_row.get("last_result"),
            )
            conn.commit()
            return result

        task = create_agent_task(
            conn,
            {"id": teacher_id, "name": teacher_name},
            {
                "task_type": task_type,
                "instruction": instruction,
                "page_context": {},
                "origin": TASK_ORIGIN_SUBSCRIPTION,
                "priority": SUBSCRIPTION_PRIORITY,
                "title_override": template["label"],
                "extra_context": {"subscription": {"template": template_key}},
            },
        )
        conn.commit()
        return f"queued agent task {task['id']}"
