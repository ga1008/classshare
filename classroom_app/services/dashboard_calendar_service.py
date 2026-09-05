"""Web calendar adapter for the complete, already-authorized workspace stream.

The legacy mini-app calendar keeps its existing adapter. This adapter never
queries business sources or invents a submission deadline for a schedule.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .dashboard_workspace_service import SCHEDULE_KINDS, _integer, _iso, local_datetime


def calendar_item(item: dict[str, Any], *, source: dict[str, Any]) -> dict[str, Any]:
    schedule = item["kind"] in SCHEDULE_KINDS
    start = local_datetime(item["starts_at"])
    end = local_datetime(item["ends_at"] if schedule else item["effective_due_at"])
    historical_due = not schedule and not end and item["status"] in {"closed", "completed"}
    if historical_due:
        end = local_datetime(item["due_at"])
    # An undated action stays undated; it must not acquire today's date merely
    # to fit the calendar. It remains reachable in the complete items panel.
    position_start, position_end = start or end, end or start
    if position_start and position_end and position_end < position_start:
        position_start = position_end
    source_type = source.get("calendar_source_type") or item["source_type"]
    if item["kind"] == "class":
        source_type = "lesson"
    elif item["kind"] == "invigilation":
        source_type = "academic_invigilation"
    elif item["kind"] == "exam":
        source_type = "academic_exam"
    deadline = ""
    if not schedule and end:
        prefix = "原截止" if historical_due else ("重交截止" if item["status"] == "returned" else ("补交截止" if item["status"] == "late" else "截止"))
        deadline = f"{prefix} {end.month}月{end.day}日 {end:%H:%M}"
    return {
        "canonical_workspace": True, "workspace_key": item["key"],
        "id": f"manual:{item['source_id']}" if item["is_manual"] else item["key"],
        "source_id": item["source_id"], "source_type": source_type, "type_label": item["type_label"],
        "class_offering_id": item["offering_id"], "semester_id": _integer(source.get("semester_id")),
        "title": item["title"], "subtitle": item["subtitle"], "notes": str(source.get("notes") or ""),
        "location": str(source.get("location") or " · ".join(str((source.get("detail") or {}).get(k) or "") for k in ("campus", "classroom") if (source.get("detail") or {}).get(k))),
        "link_url": item["href"], "is_manual": item["is_manual"], "can_complete": item["is_manual"],
        "status": item["status"], "status_label": item["status_label"],
        "is_completed": item["is_completed"], "is_actionable": item["is_actionable"],
        "is_schedule": schedule, "date_only": item["date_only"],
        "time_label": item["time_label"], "date_label": item["date_label"],
        "start_at": item["starts_at"], "ends_at": item["ends_at"],
        "due_at": "" if schedule else item["effective_due_at"],
        "effective_due_at": item["effective_due_at"], "original_due_at": item["due_at"],
        "effective_start_at": _iso(position_start) if not item["date_only"] else "",
        "effective_end_at": _iso(position_end) if not item["date_only"] else "",
        "effective_start_date": position_start.date().isoformat() if position_start else "",
        "effective_end_date": position_end.date().isoformat() if position_end else "",
        "duration_label": item["date_label"] if position_start else "未安排日期",
        "deadline_label": deadline, "due_time_label": end.strftime("%H:%M") if end and not schedule else "",
        "relative_due_label": item["status_label"], "no_deadline": not schedule and end is None,
        "priority": item["priority"], "is_high_priority": item["priority"] == "high",
    }


def attach_workspace_calendar(calendar, *, offerings, user, items, now: datetime) -> None:
    from .dashboard_service import _dashboard_todo_option, _match_semester_for_offering

    semesters = calendar.get("semesters") or []
    offering_map = {_integer(o["id"]): o for o in offerings}
    offering_semesters = {key: _match_semester_for_offering(semesters, value) for key, value in offering_map.items()}
    current_week = (now.date() - timedelta(days=now.weekday())).isoformat()
    calendar["canonical_workspace"] = True
    calendar["today_iso"] = now.date().isoformat()
    buckets = {}
    for semester in semesters:
        sid = _integer(semester["id"])
        buckets[sid] = {"items": [], "weeks": {}}
        semester["todo_create_options"] = sorted(
            [_dashboard_todo_option(o) for key, o in offering_map.items() if offering_semesters[key] is semester],
            key=lambda option: option["label"],
        )

    for item in items:
        offering = offering_map.get(item["class_offering_id"])
        semester = offering_semesters.get(item["class_offering_id"])
        if semester is None and not offering:
            semester = next((s for s in semesters if item["semester_id"] and _integer(s["id"]) == item["semester_id"]), None)
            reference = item["effective_start_date"] or item["effective_end_date"]
            if semester is None and reference:
                semester = next((s for s in semesters if s.get("start_date", "") <= reference <= s.get("end_date", "")), None)
            if semester is None and not reference:
                semester = next((s for s in semesters if _integer(s["id"]) == _integer(calendar.get("default_semester_id"))), None)
        if semester is None:
            continue
        option = _dashboard_todo_option(offering) if offering else {}
        item.update({"offering_label": option.get("label", "私人事项"), "course_name": option.get("course_name", ""), "class_name": option.get("class_name", "")})
        bucket = buckets[_integer(semester["id"])]
        bucket["items"].append(item)
        start, end = local_datetime(item["effective_start_date"]), local_datetime(item["effective_end_date"])
        sem_start, sem_end = local_datetime(semester.get("start_date")), local_datetime(semester.get("end_date"))
        if not start or not end or not sem_start or not sem_end:
            continue
        calendar_start = sem_start.date() - timedelta(days=sem_start.weekday())
        calendar_end = sem_end.date() + timedelta(days=6 - sem_end.weekday())
        first, last = max(start.date(), calendar_start), min(end.date(), calendar_end)
        week_start = first - timedelta(days=first.weekday())
        while week_start <= last:
            key = week_start.isoformat()
            week = bucket["weeks"].setdefault(key, {"key": key, "week_index": (week_start - calendar_start).days // 7 + 1,
                "label": "", "range_label": f"{week_start:%m.%d} - {week_start + timedelta(days=6):%m.%d}",
                "is_current": key == current_week, "todos": []})
            week["todos"].append(item)
            week_start += timedelta(days=7)

    for semester in semesters:
        bucket = buckets[_integer(semester["id"])]
        sort_key = lambda item: (item["effective_start_date"] or "9999", item["effective_end_date"] or "9999", item["workspace_key"])
        ordered = sorted(bucket["items"], key=sort_key)
        weeks = []
        for key, week in sorted(bucket["weeks"].items()):
            week["todos"].sort(key=sort_key)
            week.update(todo_count=len(week["todos"]), open_count=sum(bool(i["is_actionable"]) for i in week["todos"]))
            weeks.append(week)
        semester["todo_overview"] = {
            "canonical_workspace": True, "items": ordered, "weeks": weeks, "active_week_key": current_week,
            "summary": {"total_count": len(ordered), "open_count": sum(bool(i["is_actionable"]) for i in ordered),
                "manual_count": sum(bool(i["is_manual"]) for i in ordered), "no_deadline_count": sum(bool(i["no_deadline"]) for i in ordered),
                "due_soon_count": sum(bool(i["is_actionable"] and (due := local_datetime(i["effective_due_at"])) and now <= due <= now + timedelta(days=7)) for i in ordered)},
            "role_policy": {"can_create_manual": user.get("role") in {"student", "teacher"}, "show_student_stage_exams": user.get("role") == "student",
                "description": "日程显示实际安排；待处理依据当前有效提交与补交权限。"},
        }


def load_web_calendar_base(conn, *, user, offerings, now: datetime):
    """Read semester metadata once; merged students use authorized offerings."""
    from .academic_service import _attach_semester_calendar_days, build_semester_calendar_payload, load_teacher_semester_rows
    from .dashboard_service import _match_semester_for_offering

    if user["role"] == "teacher":
        rows = load_teacher_semester_rows(conn, int(user["id"]))
    elif offerings:
        ids = sorted({_integer(o.get("semester_id")) for o in offerings if o.get("semester_id")})
        teachers = sorted({_integer(o.get("teacher_id")) for o in offerings if o.get("teacher_id")})
        clauses = []
        params = []
        for column, values in (("id", ids), ("teacher_id", teachers)):
            if values:
                clauses.append(f"{column} IN ({','.join('?' for _ in values)})")
                params.extend(values)
        rows = [dict(r) for r in conn.execute(f"""SELECT id, teacher_id, school_code, school_name, name, start_date, end_date, week_count,
            calendar_sync_status, calendar_sync_at, calendar_sync_message, calendar_source_summary_json, created_at, updated_at
            FROM academic_semesters WHERE {' OR '.join(clauses) if clauses else '1 = 0'} ORDER BY start_date DESC, id DESC""", tuple(params)).fetchall()]
        for row in rows:
            row["is_current"] = str(row.get("start_date") or "") <= now.date().isoformat() <= str(row.get("end_date") or "")
            row["is_owned"], row["can_manage"] = False, False
        matched = {_integer(m["id"]) for o in offerings if (m := _match_semester_for_offering(rows, o))}
        rows = _attach_semester_calendar_days(conn, [r for r in rows if _integer(r["id"]) in matched])
    else:
        rows = []
    return build_semester_calendar_payload(rows, reference_date=now.date())


def load_dashboard_calendar(conn, *, user, now: datetime | None = None):
    from .academic_service import china_now
    from .dashboard_service import _load_student_offerings, _load_teacher_offerings
    from .dashboard_workspace_service import load_dashboard_workspace

    now = local_datetime(now or china_now())
    offerings = _load_teacher_offerings(conn, int(user["id"])) if user["role"] == "teacher" else _load_student_offerings(conn, int(user["id"]))
    calendar = load_web_calendar_base(conn, user=user, offerings=offerings, now=now)
    load_dashboard_workspace(conn, user=user, offerings=offerings, now=now, limit=1, calendar_target=calendar)
    return calendar
