"""Web homepage projection. Business facts remain in the existing services.

This module deliberately does not change the legacy dashboard/mini-app payload.
All dates exported to the browser carry the school timezone; date-only lessons
remain date-only events and never become midnight submission deadlines.
"""
from __future__ import annotations

import hashlib
import heapq
from bisect import insort
import base64
import hmac
import json
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from typing import Any

from .academic_service import CHINA_TZ, china_now
from .assignment_lifecycle_service import enrich_assignment_runtime_view, submission_resubmission_state

KIND_LABELS = {
    "class": "上课", "exam": "考试安排", "invigilation": "监考",
    "assignment": "作业", "exam_task": "考试", "stage": "个人试炼",
    "manual": "个人待办", "material": "继续阅读", "review": "复盘",
    "teacher_work": "教学工作", "poll": "投票",
}
SCHEDULE_KINDS = {"class", "exam", "invigilation"}
TASK_KINDS = {"assignment", "exam_task", "stage"}
ATTENTION_KINDS = TASK_KINDS | {"manual", "poll", "teacher_work"}


def local_datetime(value: Any) -> datetime | None:
    """Interpret legacy naive fields as Shanghai time, convert aware input."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, date):
            parsed = datetime.combine(value, time.min)
        else:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(CHINA_TZ)
        return parsed.replace(tzinfo=None)
    except (ValueError, TypeError, OverflowError):
        return None


def _iso(value: datetime | None) -> str:
    return value.replace(tzinfo=CHINA_TZ).isoformat(timespec="seconds") if value else ""


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _kind(source: dict[str, Any]) -> str:
    kind = str(source.get("kind") or source.get("source_type") or "manual")
    if kind in {"lesson", "class"}:
        return "class"
    if kind in {"academic_exam", "academic_course_exam"}:
        return "exam"
    if kind == "academic_invigilation":
        return "invigilation"
    if kind in {"stage_exam", "stage"}:
        return "stage"
    if kind == "assignment" and (source.get("metadata") or {}).get("is_exam"):
        return "exam_task"
    return "manual" if kind == "todo" else kind


def _stable_key(source: dict[str, Any], kind: str, offering_id: int) -> str:
    source_type = str(source.get("source_type") or kind)
    source_id = source.get("source_id") or source.get("todo_id") or source.get("event_id") or source.get("id")
    if source_id in (None, ""):
        # Deterministic fallback only for legacy synthetic work summaries.
        raw = "|".join(str(source.get(field) or "") for field in ("href", "link_url", "title", "starts_at", "due_at"))
        source_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{source_type}:{source_id}:{offering_id}"


def normalize_workspace_item(source: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    now = local_datetime(now) or china_now().replace(tzinfo=None)
    kind = _kind(source)
    schedule = kind in SCHEDULE_KINDS
    offering_id = _integer(source.get("offering_id") or source.get("class_offering_id"))
    starts = local_datetime(source.get("starts_at") or source.get("start_at"))
    raw_due = local_datetime(source.get("due_at"))
    ends = local_datetime(source.get("ends_at")) or (raw_due if schedule else None)
    due = None if schedule else raw_due
    effective_due = local_datetime(source.get("effective_due_at")) if "effective_due_at" in source else due
    if schedule:
        effective_due = None
    # Legacy lesson rows use 00:00 and teacher timelines invent 08:00. Neither
    # is evidence of a currently-running class.
    date_only = bool(source.get("date_only")) or (kind == "class" and not source.get("has_exact_time"))
    completed = bool(source.get("is_completed")) if not schedule else False
    status = str(source.get("status") or "open")
    cancelled = status in {"cancelled", "canceled", "deleted"}
    temporal = "undated"
    reference = starts if schedule else (effective_due or due or starts)
    if reference:
        temporal = "past" if reference.date() < now.date() else ("today" if reference.date() == now.date() else "upcoming")
    if schedule:
        ended = bool(starts and (starts.date() < now.date() if date_only else (ends or starts) <= now))
        running = bool(not date_only and starts and ends and starts <= now < ends)
        status = "cancelled" if cancelled else ("past" if ended else ("in_progress" if running else ("today" if temporal == "today" else "upcoming")))
        actionable = not cancelled and not ended
        status_label = {"cancelled": "已取消", "past": "已结束", "in_progress": "进行中", "today": "今天", "upcoming": "待开始"}[status]
    else:
        actionable = bool(source.get("is_actionable", not completed and status not in {"closed", "cancelled", "failed", "generating", "grading", "submitted"}))
        if completed:
            status = "completed"
        elif starts and starts > now and kind in TASK_KINDS and not actionable and status not in {"closed", "cancelled", "failed", "generating"}:
            status, actionable = "not_started", False
        elif status not in {"closed", "generating", "failed", "grading", "returned", "late", "not_started"}:
            status = "overdue" if due and due < now else "open"
        status_label = str(source.get("status_label") or {
            "completed": "已完成", "closed": "已关闭", "not_started": "未开始", "overdue": "已逾期",
            "returned": "待重交", "late": "可补交", "generating": "生成中", "failed": "生成失败",
            "grading": "批改中", "open": "待处理",
        }.get(status, "待处理"))
    history = completed or status in {"past", "closed", "cancelled"}
    date_bucket = "history" if history else ("overdue" if due and due < now and not schedule else temporal)
    if date_bucket == "past":
        date_bucket = "undated" if effective_due is None else "overdue"
    weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    date_label = f"{reference.month}月{reference.day}日 {weekday[reference.weekday()]}" if reference else "无日期"
    clock_label = "" if date_only or not reference else reference.strftime("%H:%M")
    if schedule:
        range_label = f"{starts:%H:%M}–{ends:%H:%M}" if starts and ends and starts.date() == ends.date() else clock_label
        if starts and ends and starts.date() != ends.date():
            range_label = f"{starts:%H:%M}–{ends.month}月{ends.day}日 {ends:%H:%M}"
        time_label = str(source.get("section_label") or source.get("time_label") or ("全天" if date_only else range_label))
    elif status == "returned":
        time_label = f"重交截止 {clock_label}" if effective_due else "重交已结束"
    elif status == "late":
        time_label = f"补交截止 {clock_label}" if effective_due else "补交未设结束时间"
    else:
        time_label = f"截止 {clock_label}" if effective_due else "无截止"
    week_start = now.date() - timedelta(days=now.weekday())
    source_id = source.get("source_id") or source.get("todo_id") or source.get("event_id") or source.get("id")
    href = str(source.get("href") or source.get("link_url") or (f"/classroom/{offering_id}#timeline-panel" if offering_id else "/dashboard#dashboard-semester"))
    item = {
        "key": _stable_key(source, kind, offering_id), "source_type": str(source.get("source_type") or kind), "source_id": source_id,
        "kind": kind, "type_label": KIND_LABELS.get(kind, "事项"), "offering_id": offering_id, "class_offering_id": offering_id,
        "title": str(source.get("title") or "待办事项"), "subtitle": str(source.get("offering_label") or source.get("subtitle") or source.get("description") or ""),
        "href": href, "starts_at": _iso(starts), "ends_at": _iso(ends), "due_at": _iso(due), "effective_due_at": _iso(effective_due),
        "status": status, "status_label": status_label, "is_completed": completed, "is_actionable": actionable,
        "temporal_state": temporal, "date_bucket": date_bucket, "date_only": date_only,
        "date_label": date_label, "time_label": time_label, "date_key": reference.date().isoformat() if reference else "",
        "is_this_week": bool(reference and week_start <= reference.date() < week_start + timedelta(days=7)),
        "is_next_seven_days": bool(reference and now <= reference <= now + timedelta(days=7)),
        "priority": str(source.get("priority") or "normal"), "has_hard_deadline": not schedule and kind != "poll" and effective_due is not None,
        "action_label": str(source.get("action_label") or ("查看待办" if kind == "manual" else ("进入课堂" if kind == "class" else "查看详情"))),
        "is_manual": kind == "manual" and bool(source.get("is_manual")),
        "is_in_progress": status == "in_progress" or bool(source.get("is_in_progress")),
    }
    # Preserve the established lifecycle controller contract, without copying
    # assignment SQL rows, grades or teacher-only metadata into browser JSON.
    agenda_data = {name: source.get(name) for name in (
        "todo_id", "notes", "reminder_enabled", "email_reminder_enabled", "reminder_lead_minutes", "event_id", "can_email_reminder", "detail",
    ) if name in source}
    agenda_data.update({
        "kind": "todo" if kind == "manual" else kind, "title": item["title"], "subtitle": item["subtitle"], "href": href,
        "status": status, "date_label": date_label, "hour_label": time_label, "relative_label": status_label,
        "is_manual": item["is_manual"], "todo_id": source.get("todo_id") or (source_id if item["is_manual"] else None),
        "class_offering_id": offering_id, "priority": item["priority"], "is_high_priority": item["priority"] == "high",
        "due_at_raw": str(source.get("due_at_raw") or source.get("due_at") or ""), "start_at_raw": str(source.get("start_at_raw") or source.get("start_at") or ""),
    })
    item["agenda_data"] = agenda_data
    return item


def workspace_sort_key(item: dict[str, Any], *, now: datetime) -> tuple:
    due = local_datetime(item.get("effective_due_at"))
    start = local_datetime(item.get("starts_at"))
    kind = item["kind"]
    actionable = item["is_actionable"] and not item["is_completed"]
    priority = {"high": 0, "normal": 1, "low": 2}.get(item.get("priority"), 1)
    rank = 9
    if actionable:
        if item["has_hard_deadline"] and due and now <= due <= now + timedelta(minutes=30):
            rank = 0
        elif item["is_in_progress"] and kind in {"exam_task", "stage", "class", "exam", "invigilation"}:
            rank = 1
        elif item["has_hard_deadline"] and due and due.date() == now.date() and due > now:
            rank = 2
        elif (kind in SCHEDULE_KINDS and start and start.date() == now.date()) or (kind == "manual" and (start or due) and (start or due).date() == now.date()) or kind == "teacher_work":
            rank = 3
        elif (kind == "poll" and item["is_in_progress"]) or (priority == 0 and due is None) or item["status"] in {"overdue", "late", "returned"}:
            rank = 4
        elif due and now < due <= now + timedelta(days=7):
            rank = 5
        elif kind in {"material", "review"} or kind in SCHEDULE_KINDS or (kind in TASK_KINDS and due is None):
            rank = 6
        else:
            rank = 7
    # Rank 3 places today's actual schedule ahead of undated teacher work.
    subgroup = 0 if rank == 3 and kind in SCHEDULE_KINDS else 1
    return (rank, subgroup, due or datetime.max, start or datetime.max, priority, item["key"])


class WorkspaceCursorError(ValueError):
    def __init__(self, message: str, *, expired: bool = False):
        super().__init__(message)
        self.expired = expired


def _cursor_signature(encoded: str) -> str:
    from ..config import SECRET_KEY
    return hmac.new(str(SECRET_KEY).encode(), f"dashboard-workspace-v1:{encoded}".encode(), hashlib.sha256).hexdigest()


def _encode_cursor(*, after: tuple, snapshot: datetime, expires: datetime, fingerprint: str, offset: int) -> str:
    payload = {"v": 1, "after": [after[0], after[1], after[2].isoformat(), after[3].isoformat(), after[4], after[5]],
               "as_of": _iso(snapshot), "expires": _iso(expires), "filters": fingerprint, "offset": offset}
    encoded = base64.urlsafe_b64encode(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()).decode().rstrip("=")
    return f"{encoded}.{_cursor_signature(encoded)}"


def _decode_cursor(token: str, *, fingerprint: str, now: datetime) -> tuple[tuple, datetime, int]:
    try:
        if len(token) > 4096:
            raise ValueError()
        encoded, signature = token.split(".")
        if not hmac.compare_digest(signature, _cursor_signature(encoded)):
            raise ValueError()
        payload = json.loads(base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True))
        after = payload["after"]
        snapshot, expires = local_datetime(payload["as_of"]), local_datetime(payload["expires"])
        offset = payload["offset"]
        if payload["v"] != 1 or payload["filters"] != fingerprint or type(offset) is not int or offset < 0:
            raise ValueError()
        if not snapshot or not expires or snapshot > now + timedelta(seconds=5) or not snapshot < expires <= snapshot + timedelta(days=1):
            raise ValueError()
        if not isinstance(after, list) or len(after) != 6 or type(after[0]) is not int or not 0 <= after[0] <= 9 or after[1] not in {0, 1} or after[4] not in {0, 1, 2}:
            raise ValueError()
        due, start = local_datetime(after[2]), local_datetime(after[3])
        if not due or not start or not isinstance(after[5], str) or len(after[5]) > 512:
            raise ValueError()
        if now >= expires:
            raise WorkspaceCursorError("事项时间状态已更新，请从第一页重新读取", expired=True)
        return (after[0], after[1], due, start, after[4], after[5]), snapshot, offset
    except WorkspaceCursorError:
        raise
    except (ValueError, TypeError, KeyError, OverflowError):
        raise WorkspaceCursorError("分页游标无效或不属于当前筛选，请从第一页重新读取") from None


def build_dashboard_workspace(*, user: dict[str, Any], offerings: list[dict[str, Any]], sources: Iterable[dict[str, Any]], continue_material: dict[str, Any] | None = None, now: datetime | None = None, offering_id: int = 0, offering_ids: set[int] | None = None, kind: str = "all", date_scope: str = "all", status: str = "all", keyword: str = "", item_key: str = "", offset: int = 0, limit: int = 100, cursor: str = "", calendar_target: dict[str, Any] | None = None) -> dict[str, Any]:
    now = local_datetime(now or china_now()) or china_now().replace(tzinfo=None)
    offset, limit = max(0, offset), max(1, min(100, limit))
    fingerprint = hashlib.sha256(json.dumps([user.get("role"), user.get("id"), offering_id, sorted(offering_ids) if offering_ids is not None else None,
                                             kind, date_scope, status, keyword.strip().casefold(), item_key], ensure_ascii=False).encode()).hexdigest()
    after = None
    if cursor:
        after, now, offset = _decode_cursor(cursor, fingerprint=fingerprint, now=now)
    continuation: list[dict[str, Any]] = []
    if continue_material and continue_material.get("last_viewed_at"):
        continuation.append({
            "source_type": "material", "source_id": continue_material.get("material_id"), "kind": "material",
            "class_offering_id": continue_material.get("class_offering_id"), "title": continue_material.get("material_name"),
            "subtitle": continue_material.get("course_name"), "href": continue_material.get("href"),
            "is_actionable": True, "action_label": "继续阅读",
        })
    from itertools import chain
    seen: set[str] = set()
    counts = {"total": 0, "filtered_total": 0, "pending_total": 0, "urgent_total": 0, "today_class_count": 0, "today_due_count": 0}
    focus_pool: list[tuple[tuple, dict[str, Any]]] = []
    attention_pool: list[tuple[tuple, dict[str, Any]]] = []
    action_summary = {"total": 0, "today": 0, "overdue": 0, "undated": 0}
    next_transition: datetime | None = None
    offering_summaries = {str(_integer(o["id"])): {"pending_task_count": 0, "pending_review_count": 0} for o in offerings}
    calendar_items = [] if calendar_target is not None else None

    def needs_attention(item):
        # Schedule and reading facts retain their original calendar semantics.
        return item["kind"] in ATTENTION_KINDS and item["is_actionable"] and not item["is_completed"]

    def matches(item):
        if item_key and item["key"] != item_key:
            return False
        if offering_id and item["offering_id"] != offering_id:
            return False
        if offering_ids is not None and item["offering_id"] not in offering_ids:
            return False
        if kind != "all" and item["kind"] != kind:
            return False
        if status == "actionable" and not item["is_actionable"]:
            return False
        if status == "completed" and not item["is_completed"]:
            return False
        if status == "attention" and not needs_attention(item):
            return False
        if status == "attention" and date_scope == "today":
            due = local_datetime(item["effective_due_at"])
            if not due or due.date() != now.date():
                return False
        elif date_scope == "this_week":
            if not item["is_this_week"]:
                return False
        elif date_scope == "next_seven_days":
            if not item["is_next_seven_days"]:
                return False
        elif date_scope != "all" and item["date_bucket"] != date_scope:
            return False
        return not keyword or keyword.strip().casefold() in f"{item['title']} {item['subtitle']}".casefold()

    def normalized_matches():
        nonlocal focus_pool, next_transition
        for source in chain(sources, continuation):
            item = normalize_workspace_item(source, now=now)
            if item["key"] in seen:
                continue
            seen.add(item["key"])
            if calendar_items is not None:
                from .dashboard_calendar_service import calendar_item
                calendar_items.append(calendar_item(item, source=source))
            summary = offering_summaries.get(str(item["offering_id"]))
            if summary is not None:
                summary["pending_task_count"] += bool(item["kind"] in TASK_KINDS and item["is_actionable"] and not item["is_completed"])
                if item["source_type"] == "grading":
                    summary["pending_review_count"] += _integer(source.get("work_count"))
            counts["total"] += 1
            counts["pending_total"] += bool(item["is_actionable"] and not item["is_completed"])
            sort_key = workspace_sort_key(item, now=now)
            rank = sort_key[0]
            counts["urgent_total"] += rank <= 2
            counts["today_class_count"] += item["kind"] == "class" and item["date_bucket"] == "today"
            due = local_datetime(item["effective_due_at"])
            counts["today_due_count"] += bool(due and item["has_hard_deadline"] and item["is_actionable"] and due.date() == now.date())
            if needs_attention(item):
                action_summary["total"] += 1
                action_summary["today"] += bool(due and due.date() == now.date())
                action_summary["overdue"] += bool(due and due < now)
                action_summary["undated"] += not bool(due or item["starts_at"])
                if len(attention_pool) < 3 or sort_key < attention_pool[-1][0]:
                    insort(attention_pool, (sort_key, item))
                    del attention_pool[3:]
            if rank <= 6:
                # The key contains the stable source identity, so equal keys
                # cannot reach this point after deduplication. Retain only
                # eight candidates without parsing their dates again per row.
                if len(focus_pool) < 8 or sort_key < focus_pool[-1][0]:
                    insort(focus_pool, (sort_key, item))
                    del focus_pool[8:]
            boundaries = [local_datetime(item.get(field)) for field in ("starts_at", "ends_at", "due_at", "effective_due_at")]
            if due:
                boundaries.append(due - timedelta(minutes=30))
            for boundary in boundaries:
                if boundary == now:
                    boundary += timedelta(seconds=1)
                if boundary and boundary > now and (next_transition is None or boundary < next_transition):
                    next_transition = boundary
            if matches(item):
                counts["filtered_total"] += 1
                if after is None or sort_key > after:
                    yield sort_key, item

    # Stream DB facts and retain only the requested page prefix, never an
    # entire hidden DOM/payload. Query iteration itself uses batches of 100.
    page_prefix = heapq.nsmallest(limit if after is not None else offset + limit, normalized_matches(), key=lambda entry: entry[0])
    all_items = [item for _, item in (page_prefix if after is not None else page_prefix[offset:])]
    if calendar_target is not None:
        from .dashboard_calendar_service import attach_workspace_calendar
        attach_workspace_calendar(calendar_target, offerings=offerings, user=user, items=calendar_items, now=now)
    focus: list[dict[str, Any]] = []
    fallback_added = False
    focused_ranks: set[int] = set()
    for sort_key, item in focus_pool:
        rank = sort_key[0]
        if rank == 6:
            if fallback_added or focused_ranks.intersection({0, 1, 2, 5}):
                continue
            fallback_added = True
        focus.append(item)
        focused_ranks.add(rank)
        if len(focus) == 3:
            break
    name = str(user.get("name") or "").strip()
    greeting = "早上好" if 5 <= now.hour < 11 else ("中午好" if now.hour < 14 and now.hour >= 11 else ("下午好" if 14 <= now.hour < 18 else "晚上好"))
    expires = min(next_transition or datetime.max, datetime.combine(now.date() + timedelta(days=1), time.min))
    has_more = offset + len(all_items) < counts["filtered_total"]
    next_cursor = _encode_cursor(after=workspace_sort_key(all_items[-1], now=now), snapshot=now, expires=expires, fingerprint=fingerprint,
                                 offset=offset + len(all_items)) if has_more and all_items else None
    return {
        "greeting": f"{name}，{greeting}" if name else greeting,
        "date_label": f"{now.month}月{now.day}日 {['周一','周二','周三','周四','周五','周六','周日'][now.weekday()]}",
        "generated_at": _iso(now), "timezone": "Asia/Shanghai", "focus_items": focus, "all_items": all_items,
        "attention_items": [item for _, item in attention_pool], "action_summary": action_summary,
        "offering_summaries": offering_summaries,
        **counts, "actionable_total": counts["pending_total"], "offset": offset, "limit": limit,
        "has_more": has_more, "next_cursor": next_cursor,
        "next_transition_at": _iso(expires),
        "offering_options": [{"id": _integer(o.get("id")), "label": " · ".join(str(o.get(k) or "") for k in ("course_name", "class_name") if o.get(k)), "semester": str(o.get("semester") or "")} for o in offerings],
    }


def assignment_workspace_source(row: dict[str, Any], *, now: datetime, role: str) -> dict[str, Any]:
    """Use existing submission policy on normalized local timestamps."""
    clean = dict(row)
    for field in ("starts_at", "due_at", "late_submission_until", "resubmission_due_at"):
        parsed = local_datetime(clean.get(field))
        clean[field] = parsed.isoformat() if parsed else None
    runtime = enrich_assignment_runtime_view(clean, now_dt=now)
    returned = submission_resubmission_state(clean, now_dt=now)
    has_submission = bool(clean.get("submission_id"))
    completed = has_submission and returned == "none"
    accepting = bool(runtime.get("is_accepting_submissions"))
    effective_due = runtime.get("countdown_at")
    status = runtime.get("effective_status") or "closed"
    label, action = "待提交", "开始作答" if clean.get("exam_paper_id") else "提交作业"
    if role == "teacher":
        # Closing a published assignment is a lifecycle state, not evidence
        # that the teacher completed their own work.
        completed, accepting = False, status == "new"
        label, action = ("草稿", "编辑任务") if status == "new" else ("已发布" if status == "published" else "已关闭", "查看任务")
    elif returned != "none":
        accepting = returned == "open"
        effective_due = clean.get("resubmission_due_at")
        status, label, action = ("returned", "待重交", "重新提交") if accepting else ("closed", "重交已结束", "查看详情")
    elif completed:
        status, label, action = "completed", {"grading": "批改中", "grading_review": "待教师复核", "graded": "已提交"}.get(clean.get("submission_status"), "已提交"), "查看提交"
    elif runtime.get("is_late_submission_open"):
        status, label, action = "late", "可补交", "补交任务"
    elif not accepting:
        status, label, action = "closed", "已关闭", "查看详情"
        if clean.get("starts_at") and local_datetime(clean["starts_at"]) > now and runtime.get("effective_status") == "published":
            status, label = "not_started", "未开始"
    source = {
        "source_type": "assignment", "source_id": clean["id"], "kind": "exam_task" if clean.get("exam_paper_id") else "assignment",
        "class_offering_id": clean["offering_id"], "title": clean.get("title") or "课堂任务", "subtitle": clean.get("offering_label") or "",
        "href": f"/assignment/{clean['id']}", "starts_at": clean.get("starts_at"), "due_at": clean.get("due_at"),
        "effective_due_at": effective_due, "status": status, "status_label": label, "is_actionable": accepting and not completed,
        "is_completed": completed, "action_label": action,
    }
    return source


def _iter_rows(conn, sql: str, params=()):
    cursor = conn.execute(sql, params)
    while True:
        rows = cursor.fetchmany(100)
        if not rows:
            return
        for row in rows:
            yield dict(row)


def load_assignment_workspace_sources(conn, *, offerings: list[dict[str, Any]], user: dict[str, Any], now: datetime):
    if not offerings:
        return
    ids = [_integer(o["id"]) for o in offerings]
    placeholders = ",".join("?" for _ in ids)
    student = user.get("role") == "student"
    submissions = "LEFT JOIN submissions s ON s.assignment_id = a.id AND s.student_pk_id = ? AND COALESCE(s.is_absence_score, 0) = 0" if student else ""
    submission_fields = ", s.id AS submission_id, s.status AS submission_status, s.resubmission_allowed, s.resubmission_due_at" if student else ""
    rows = _iter_rows(conn, f"""
        SELECT a.*, o.id AS offering_id{submission_fields}
        FROM class_offerings o
        JOIN assignments a ON a.course_id = o.course_id AND (a.class_offering_id = o.id OR a.class_offering_id IS NULL)
        {submissions}
        WHERE o.id IN ({placeholders})
          {"AND a.status != 'new'" if student else ""}
          AND NOT EXISTS (SELECT 1 FROM learning_stage_exam_attempts lsea WHERE lsea.assignment_id = a.id)
        ORDER BY o.id, a.id
    """, tuple(([int(user["id"])] if student else []) + ids))
    labels = {_integer(o["id"]): " · ".join(str(o.get(k) or "") for k in ("course_name", "class_name") if o.get(k)) for o in offerings}
    for row in rows:
        item = dict(row)
        item["offering_label"] = labels.get(_integer(item["offering_id"]), "")
        yield assignment_workspace_source(item, now=now, role=str(user.get("role")))


def _json(value: Any) -> dict[str, Any]:
    try:
        result = json.loads(str(value or "{}"))
        return result if isinstance(result, dict) else {}
    except (TypeError, ValueError):
        return {}


def _has_optional_table(conn, name: str) -> bool:
    from ..db.connection import get_configured_db_engine
    if get_configured_db_engine() == "postgres":
        sql = "SELECT 1 FROM information_schema.tables WHERE table_schema = current_schema() AND table_name = ?"
    else:
        sql = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?"
    return bool(conn.execute(sql, (name,)).fetchone())


def _poll_sources(conn, *, ids: list[int], labels: dict[int, str], user: dict[str, Any], now: datetime):
    # Poll schema is lazily installed by its write path. This read path never
    # creates it, and does not request vote totals or participant identities.
    if not ids or not _has_optional_table(conn, "polls"):
        return
    placeholders = ",".join("?" for _ in ids)
    teacher, user_id = user["role"] == "teacher", int(user["id"])
    for row in _iter_rows(conn, f"""SELECT p.id, p.title, p.status, p.deadline_at, p.owner_role, p.owner_user_pk,
            p.audience_scope, pa.class_offering_id, b.id AS ballot_id,
            CASE WHEN pp.student_id IS NOT NULL THEN 1 ELSE 0 END AS is_participant
        FROM polls p JOIN poll_assignments pa ON pa.poll_id = p.id
        LEFT JOIN poll_ballots b ON b.poll_id = p.id AND b.voter_id = ?
        LEFT JOIN poll_participants pp ON pp.poll_id = p.id AND pp.student_id = ?
        WHERE pa.class_offering_id IN ({placeholders}) ORDER BY p.id, pa.class_offering_id""", (user_id, user_id, *ids)):
        owned = row["owner_role"] == user["role"] and _integer(row["owner_user_pk"]) == user_id
        if row["status"] == "draft" and not owned:
            continue
        participant = not teacher and (bool(row["is_participant"]) if row["audience_scope"] == "custom" else str(user.get("enrollment_status") or "active") == "active")
        if not (owned or teacher or participant):
            continue
        due = local_datetime(row["deadline_at"])
        active = row["status"] == "active" and (due is None or now < due)
        voted = not teacher and bool(row["ballot_id"])
        actionable = active and participant and not voted
        yield {"source_type": "poll", "source_id": row["id"], "kind": "poll", "class_offering_id": row["class_offering_id"],
               "title": row["title"], "subtitle": labels.get(_integer(row["class_offering_id"]), ""), "due_at": row["deadline_at"],
               "is_completed": voted, "is_actionable": actionable, "is_in_progress": actionable,
               "status": "completed" if voted else ("open" if active else "closed"),
               "status_label": "已投票" if voted else ("进行中" if active else ("草稿" if row["status"] == "draft" else "已结束")),
               "href": f"/classroom/{row['class_offering_id']}#poll-panel", "action_label": "参与投票" if actionable else "查看投票"}


def iter_workspace_sources(conn, *, offerings: list[dict[str, Any]], user: dict[str, Any], now: datetime):
    """Only scoped SELECTs. No schemas, schedules, AI or progress recalculation."""
    ids = [_integer(o["id"]) for o in offerings]
    labels = {_integer(o["id"]): " · ".join(str(o.get(k) or "") for k in ("course_name", "class_name") if o.get(k)) for o in offerings}
    placeholders = ",".join("?" for _ in ids)
    teacher = user.get("role") == "teacher"
    yield from load_assignment_workspace_sources(conn, offerings=offerings, user=user, now=now)
    yield from _poll_sources(conn, ids=ids, labels=labels, user=user, now=now)
    if ids:
        for row in _iter_rows(conn, f"SELECT * FROM class_offering_sessions WHERE class_offering_id IN ({placeholders}) ORDER BY id", tuple(ids)):
            if not row.get("session_date"):
                continue
            meta = _json(row.get("schedule_metadata_json"))
            section = str(row.get("academic_section_text") or meta.get("section_text") or "")
            if not section and row.get("section_start"):
                section = f"第 {row['section_start']}-{row.get('section_end') or row['section_start']} 节"
            yield {
                "source_type": "lesson", "source_id": row["id"], "kind": "class", "class_offering_id": row["class_offering_id"],
                "title": row.get("title") or "课堂安排", "subtitle": labels.get(_integer(row["class_offering_id"]), ""),
                "starts_at": str(row["session_date"])[:10], "date_only": True, "section_label": section or "未设置具体节次",
                "status": "cancelled" if row.get("schedule_status") in {"cancelled", "canceled"} else "upcoming",
                "href": f"/classroom/{row['class_offering_id']}#timeline-panel",
            }
        # Student academic examinations are separate from site assignments.
        if not teacher:
            for row in _iter_rows(conn, f"SELECT * FROM teacher_academic_course_exam_items WHERE class_offering_id IN ({placeholders}) AND sync_status = 'active' ORDER BY id", tuple(ids)):
                yield {
                    "source_type": "academic_exam", "source_id": row["id"], "kind": "exam", "class_offering_id": row["class_offering_id"],
                    "title": row.get("course_name") or row.get("exam_name") or "教务考试", "subtitle": row.get("location") or labels.get(_integer(row["class_offering_id"]), ""),
                    "location": row.get("location") or "",
                    "starts_at": row.get("starts_at") or row.get("exam_date"), "ends_at": row.get("ends_at"),
                    "date_only": not row.get("starts_at"), "href": f"/classroom/{row['class_offering_id']}#timeline-panel",
                }
            from .learning_progress_service import get_learning_level, public_level_payload
            for row in _iter_rows(conn, f"""SELECT a.*, lsea.id AS attempt_id, lsea.assignment_id AS linked_assignment_id,
                    lsea.class_offering_id AS offering_id, lsea.status AS attempt_status, lsea.stage_key
                FROM learning_stage_exam_attempts lsea LEFT JOIN assignments a ON a.id = lsea.assignment_id
                WHERE lsea.class_offering_id IN ({placeholders}) AND lsea.student_id = ? ORDER BY lsea.id""", (*ids, int(user["id"]))):
                attempt_status = str(row.get("attempt_status") or "")
                level = public_level_payload(get_learning_level(str(row.get("stage_key") or "")))
                task = assignment_workspace_source(row, now=now, role="student") if row.get("id") else {}
                actionable = attempt_status == "generated" and bool(task.get("is_actionable"))
                yield {
                    **task, "source_type": "stage_exam", "source_id": row["attempt_id"], "kind": "stage", "class_offering_id": row["offering_id"],
                    "title": f"{level['name']}破境试炼", "subtitle": labels.get(_integer(row["offering_id"]), ""),
                    "status": task.get("status", "closed") if attempt_status == "generated" else attempt_status,
                    "is_completed": attempt_status in {"submitted", "grading", "graded", "passed"}, "is_actionable": actionable,
                    "href": f"/exam/take/{row['linked_assignment_id']}" if actionable else f"/classroom/{row['offering_id']}#learning-progress-panel",
                    "status_label": ("待作答" if actionable else task.get("status_label", "无法作答")) if attempt_status == "generated" else {"generating": "生成中", "failed": "生成失败", "submitted": "已提交", "grading": "批改中", "graded": "已批改"}.get(attempt_status, "查看试炼"),
                }
    manual_scope = "" if teacher else (f"AND class_offering_id IN ({placeholders})" if ids else "AND 1 = 0")
    for row in _iter_rows(conn, f"SELECT * FROM classroom_todos WHERE owner_role = ? AND owner_user_pk = ? AND deleted_at IS NULL {manual_scope} ORDER BY id", (str(user["role"]), int(user["id"]), *([] if teacher else ids))):
        meta = _json(row.get("metadata_json"))
        reminder = meta.get("reminder") or {}
        accessible_id = _integer(row.get("class_offering_id"))
        # Keep teacher-owned orphan todos, but do not disclose an inaccessible
        # classroom association or route to that classroom.
        if accessible_id not in labels:
            accessible_id = 0
        yield {
            "source_type": "manual", "source_id": row["id"], "todo_id": row["id"], "kind": "manual", "is_manual": True,
            "class_offering_id": accessible_id, "title": row.get("title"), "subtitle": labels.get(accessible_id, "私人待办"),
            "notes": row.get("notes") or "", "start_at": row.get("start_at"), "due_at": row.get("due_at"),
            "is_completed": bool(row.get("completed_at")), "is_actionable": not row.get("completed_at"), "priority": meta.get("priority") or "normal",
            "reminder_enabled": bool(reminder.get("enabled")), "email_reminder_enabled": bool(reminder.get("email_enabled")),
            "reminder_lead_minutes": _integer(reminder.get("lead_minutes")) or 1440,
        }
    if teacher:
        from .exam_reminder_service import build_event_reminder_detail
        for row in _iter_rows(conn, "SELECT * FROM teacher_calendar_events WHERE teacher_id = ? AND status = 'active' AND deleted_at IS NULL ORDER BY id", (int(user["id"]),)):
            academic = row.get("source_type") in {"academic_invigilation", "academic_course_exam", "academic_exam"}
            kind = "invigilation" if row.get("source_type") == "academic_invigilation" else ("exam" if academic else "teacher_work")
            detail = build_event_reminder_detail(row, teacher_name=str(user.get("name") or "")) if academic else {}
            calendar_offering_id = _integer(_json(row.get("metadata_json")).get("class_offering_id"))
            if calendar_offering_id not in labels:
                calendar_offering_id = 0
            yield {
                "source_type": "teacher_calendar", "source_id": row["id"], "kind": kind, "event_id": row["id"],
                "class_offering_id": calendar_offering_id, "semester_id": row.get("semester_id"), "calendar_source_type": row.get("source_type"),
                "title": detail.get("subject") or row.get("title"), "subtitle": " · ".join(str(detail.get(k) or "") for k in ("campus", "classroom") if detail.get(k)),
                "starts_at": row.get("starts_at") or (row.get("due_at") if academic else None), "ends_at": row.get("ends_at"),
                "due_at": None if academic else row.get("due_at"), "href": row.get("link_url") or "/dashboard#dashboard-semester",
                "date_only": not row.get("starts_at"), "can_email_reminder": academic, "detail": detail,
            }
        if ids:
            from .offering_membership_service import offering_student_where
            for row in _iter_rows(conn, f"""SELECT o.id AS offering_id, COUNT(s.id) AS pending_count FROM class_offerings o
                JOIN assignments a ON a.course_id = o.course_id AND (a.class_offering_id = o.id OR a.class_offering_id IS NULL)
                JOIN submissions s ON s.assignment_id = a.id AND s.status = 'submitted'
                  AND COALESCE(s.resubmission_allowed, 0) = 0 AND COALESCE(s.is_absence_score, 0) = 0
                JOIN students learner ON learner.id = s.student_pk_id
                  AND {offering_student_where(offering_alias='o', student_alias='learner')}
                WHERE o.id IN ({placeholders}) AND NOT EXISTS (SELECT 1 FROM learning_stage_exam_attempts e WHERE e.assignment_id = a.id)
                GROUP BY o.id ORDER BY o.id""", tuple(ids)):
                if row["pending_count"]:
                    yield {"source_type": "grading", "source_id": row["offering_id"], "kind": "teacher_work", "class_offering_id": row["offering_id"],
                           "work_count": _integer(row["pending_count"]),
                           "title": f"{row['pending_count']} 份作业待批改", "subtitle": labels.get(_integer(row["offering_id"]), ""),
                           "is_actionable": True, "href": f"/classroom/{row['offering_id']}#assignment-panel", "action_label": "查看待批改"}
        for row in _iter_rows(conn, """SELECT COUNT(*) AS pending_count FROM student_password_reset_requests r JOIN classes c ON c.id = r.class_id
            WHERE r.teacher_id = ? AND c.created_by_teacher_id = ? AND r.status = 'pending'""", (int(user["id"]), int(user["id"]))):
            if row["pending_count"]:
                from .manage_nav_service import canonical_manage_href
                yield {"source_type": "password_reset", "source_id": "pending", "kind": "teacher_work", "title": f"{row['pending_count']} 项密码申请待审核", "is_actionable": True,
                       "href": canonical_manage_href("system_password_resets"), "action_label": "审核申请"}


def load_dashboard_workspace(conn, *, user: dict[str, Any], offerings: list[dict[str, Any]] | None = None, continue_material: dict[str, Any] | None = None, now: datetime | None = None, **filters) -> dict[str, Any]:
    from .dashboard_service import _load_student_offerings, _load_teacher_offerings, _load_student_continue_material
    now = local_datetime(now or china_now()) or china_now().replace(tzinfo=None)
    if user.get("role") not in {"student", "teacher"}:
        raise PermissionError("当前账号不能读取首页事项")
    if offerings is None:
        offerings = _load_teacher_offerings(conn, int(user["id"])) if user["role"] == "teacher" else _load_student_offerings(conn, int(user["id"]))
    if continue_material is None and user["role"] == "student":
        continue_material = _load_student_continue_material(conn, student_id=int(user["id"]), offering_ids=[int(o["id"]) for o in offerings], include_multiple=True, require_read=True)
    return build_dashboard_workspace(user=user, offerings=offerings, sources=iter_workspace_sources(conn, offerings=offerings, user=user, now=now), continue_material=continue_material, now=now, **filters)
