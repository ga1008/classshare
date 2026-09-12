"""教务域首页 = 我的教务日程（docs/manage-center-improvement-plan-2026-09-11.md §5.7）。

周视图时间线复用首页 agenda 同一数据源（dashboard_service 的教师日历事件），
右栏三张同步状态卡（教务对接 / 智慧课堂 / 公文通）三态：none / ok / error。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

WEEKDAY_LABELS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
KIND_LABELS = {"invigilation": "监考", "exam": "考试", "todo": "待办", "class": "上课"}
STATE_LABELS = {"none": "未连接", "ok": "正常", "error": "同步失败"}


def _parse_day(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def resolve_week_start(anchor: Any, today: date) -> date:
    day = _parse_day(anchor) or today
    return day - timedelta(days=day.weekday())


def credential_state(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"state": "none", "state_label": STATE_LABELS["none"], "detail": "连接后自动同步", "at": ""}
    enabled = [row for row in rows if row.get("enabled", 1) not in (0, False, "0")] or rows
    errors = [row for row in enabled if str(row.get("last_status") or "").lower() in {"error", "failed", "fail", "invalid"}]
    latest = max(enabled, key=lambda row: str(row.get("last_status_at") or row.get("last_verified_at") or ""))
    at = str(latest.get("last_status_at") or latest.get("last_verified_at") or "")[:16].replace("T", " ")
    if errors:
        return {"state": "error", "state_label": STATE_LABELS["error"], "detail": str(errors[0].get("last_error") or "凭据或网络异常")[:80], "at": at}
    return {"state": "ok", "state_label": STATE_LABELS["ok"], "detail": f"{len(enabled)} 个账号" + (f" · {at}" if at else ""), "at": at}


def group_events_by_week(events: list[dict[str, Any]], *, week_start: date, today: date) -> list[dict[str, Any]]:
    days: list[dict[str, Any]] = []
    for offset in range(7):
        day = week_start + timedelta(days=offset)
        day_events = [event for event in events if str(event.get("date_full_label") or "") == day.isoformat()]
        for event in day_events:
            event["kind_label"] = KIND_LABELS.get(str(event.get("kind") or ""), "教务")
        days.append({
            "date": day.isoformat(),
            "label": f"{day.month}月{day.day}日",
            "weekday": WEEKDAY_LABELS[day.weekday()],
            "is_today": day == today,
            "is_past": day < today,
            "events": day_events,
        })
    return days


_CREDENTIAL_CARDS = (
    ("academic", "教务对接", "/manage/academic/integrations", "academic_integration_service", "list_teacher_academic_credentials"),
    ("smart_classroom", "智慧课堂", "/manage/academic/smart-classroom", "smart_classroom_integration_service", "list_teacher_smart_classroom_credentials"),
    ("gongwen", "公文通", "/manage/academic/gongwen-sync", "gongwen_integration_service", "list_teacher_gongwen_credentials"),
)


def build_academic_home(
    conn,
    user: dict[str, Any],
    *,
    week_anchor: Any = None,
    today: date | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    from importlib import import_module

    from .academic_service import china_now, china_today
    from .dashboard_service import _build_teacher_calendar_agenda_events

    teacher_id = int(user["id"])
    today = today or china_today()
    now = now or china_now().replace(tzinfo=None)
    week_start = resolve_week_start(week_anchor, today)
    week_end = week_start + timedelta(days=6)

    events = _build_teacher_calendar_agenda_events(conn, teacher_id=teacher_id, today=today, now=now)
    days = group_events_by_week(events, week_start=week_start, today=today)
    week_total = sum(len(day["events"]) for day in days)
    kind_counts: dict[str, int] = {}
    for day in days:
        for event in day["events"]:
            kind = str(event.get("kind") or "")
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
    upcoming = [event for event in events if str(event.get("date_full_label") or "") > week_end.isoformat()][:5]

    sync_cards = []
    for key, label, href, module_name, func_name in _CREDENTIAL_CARDS:
        try:
            module = import_module(f"classroom_app.services.{module_name}")
            rows = [dict(row) for row in getattr(module, func_name)(conn, teacher_id)]
        except Exception as exc:  # pragma: no cover - defensive per-card degradation
            print(f"[ACADEMIC_HOME] credential card {key} failed: {exc}")
            rows = []
        sync_cards.append({"key": key, "label": label, "href": href, **credential_state(rows)})

    return {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "week_label": f"{week_start.month}月{week_start.day}日 – {week_end.month}月{week_end.day}日",
        "prev_week": (week_start - timedelta(days=7)).isoformat(),
        "next_week": (week_start + timedelta(days=7)).isoformat(),
        "is_current_week": week_start <= today <= week_end,
        "days": days,
        "week_total": week_total,
        "kind_counts": [{"kind": kind, "label": KIND_LABELS.get(kind, "教务"), "count": count} for kind, count in kind_counts.items()],
        "upcoming": upcoming,
        "sync_cards": sync_cards,
    }
