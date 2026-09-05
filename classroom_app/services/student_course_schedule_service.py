"""Read-only platform session adapter for the student's shared 3D week deck.

Membership and semester discovery are shared with the homepage. This adapter
never reads a teacher's imported schedule: every lesson has an authorized
platform offering and an actual session date. Its four batch SELECTs do not
grow with the number of offerings; reading a deck cannot synchronize schedules.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

from .academic_course_sync_service import _parse_section_range
from .academic_service import china_now
from .dashboard_calendar_service import load_web_calendar_base
from .dashboard_service import _load_student_offerings, _match_semester_for_offering
from .semester_identity_service import parse_semester_identity
from .smart_classroom_schedule_sync_service import (
    _build_course_stats,
    _build_week_deck,
    _section_label,
    _short_classroom,
    _weekday_label,
)


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _term_key(semester: dict[str, Any]) -> tuple[str, str]:
    identity = parse_semester_identity(semester.get("name"))
    # Custom semester names remain selectable without inventing an academic year.
    return identity.as_year_term() if identity else (f"semester-{semester['id']}", "0")


def _empty(terms: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "status": "empty", "has_data": False, "message": "本平台暂无可显示的课程安排。",
        "terms": terms or [], "selected_term": None,
        "filters": {"course": "", "class_label": "", "course_options": [], "class_options": []},
        "summary": {}, "courses": [], "weeks": [], "section_range": {"min": 1, "max": 11},
    }


def build_student_course_schedule_overview(
    conn, student_id: int, *, year: str = "", term: str = "", now: datetime | None = None,
) -> dict[str, Any]:
    now = now or china_now()
    today = now.date()
    offerings = _load_student_offerings(conn, int(student_id))
    if not offerings:
        return _empty()
    calendar = load_web_calendar_base(
        conn, user={"id": int(student_id), "role": "student"}, offerings=offerings, now=now,
    )
    semesters = calendar.get("semesters") or []
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for semester in semesters:
        by_key.setdefault(_term_key(semester), []).append(semester)
    terms = []
    for (academic_year, academic_term), entries in by_key.items():
        start = min((_date(s.get("start_date")) for s in entries if _date(s.get("start_date"))), default=None)
        end = max((_date(s.get("end_date")) for s in entries if _date(s.get("end_date"))), default=None)
        monday = start - timedelta(days=start.weekday()) if start else None
        status = "current" if start and end and start <= today <= end else (
            "ended" if end and today > end else ("future" if start and today < start else "unknown")
        )
        terms.append({
            "year": academic_year, "term": academic_term, "label": entries[0]["name"],
            "status": status, "week1_monday": monday.isoformat() if monday else "",
            "max_week": max((int(s.get("week_count") or 0) for s in entries), default=0),
            "anchor_source": "platform", "schedule_source": "platform_offerings",
        })
    if not terms:
        return _empty()
    selected = next((entry for entry in terms if (entry["year"], entry["term"]) == (year, term)), None)
    if (year or term) and selected is None:
        # An inaccessible/stale term must not silently substitute a different one.
        return _empty(terms)
    selected = selected or next((entry for entry in terms if entry["status"] == "current"), None)
    if selected is None:
        ended = [entry for entry in terms if entry["status"] == "ended"]
        selected = max(ended, key=lambda entry: entry["week1_monday"]) if ended else terms[0]
    selected = dict(selected)
    selected_key = (selected["year"], selected["term"])
    selected_offerings = {
        int(offering["id"]): offering for offering in offerings
        if (semester := _match_semester_for_offering(semesters, offering)) and _term_key(semester) == selected_key
    }
    ids = sorted(selected_offerings)
    rows = conn.execute(
        f"""SELECT s.id, s.class_offering_id, s.session_date, s.order_index, s.academic_section_text,
            s.academic_location, s.schedule_metadata_json, o.combined_class_names
            FROM class_offering_sessions s JOIN class_offerings o ON o.id = s.class_offering_id
            WHERE s.class_offering_id IN ({','.join('?' for _ in ids)})
            AND COALESCE(s.schedule_status, 'scheduled') NOT IN ('cancelled', 'canceled')
            ORDER BY s.session_date, s.order_index, s.id""", tuple(ids),
    ).fetchall() if ids else []
    # The real date takes precedence over stale week_index/weekday after a move.
    monday = _date(selected["week1_monday"])
    if monday is None:
        first_date = min((_date(row["session_date"]) for row in rows if _date(row["session_date"])), default=None)
        monday = first_date - timedelta(days=first_date.weekday()) if first_date else None
    items = []
    session_map = {}
    totals: dict[int, int] = {}
    for row in rows:
        oid = int(row["class_offering_id"])
        totals[oid] = max(totals.get(oid, 0), int(row["order_index"] or 0))
    unpositioned_count = 0
    for row in rows:
        on_date = _date(row["session_date"])
        try:
            metadata = json.loads(row["schedule_metadata_json"] or "{}")
        except (TypeError, ValueError):
            metadata = {}
        section_text = row["academic_section_text"] or (metadata.get("section_text") if isinstance(metadata, dict) else "")
        section_start, section_end, _count = _parse_section_range(section_text)
        week = ((on_date - monday).days // 7 + 1) if on_date and monday else 0
        if not 1 <= week <= 104 or not 1 <= section_start <= section_end <= 24:
            # Do not assign an unknown time to a fabricated first period.
            unpositioned_count += 1
            continue
        sections = list(range(section_start, section_end + 1))
        offering_id = int(row["class_offering_id"])
        offering = selected_offerings[offering_id]
        class_label = str(row["combined_class_names"] or offering.get("class_name") or "")
        room = str(row["academic_location"] or "")
        item = {
            "id": int(row["id"]), "weekday": on_date.weekday() + 1,
            "weekday_label": _weekday_label(on_date.weekday() + 1),
            "sections": sections, "section_label": _section_label(sections), "weeks": [week],
            "course_name": str(offering.get("course_name") or ""), "course_code": "",
            "classroom": room, "classroom_short": _short_classroom(room),
            "class_label": class_label, "class_is_fallback": False, "class_offering_id": offering_id,
            "classroom_url": f"/classroom/{offering_id}", "single_or_double": "NONE",
            "single_or_double_label": "", "student_count": 0,
            "hours_per_meeting": len(sections), "total_hours": len(sections),
        }
        items.append(item)
        session_map[(item["id"], week)] = (int(row["order_index"] or 0), totals[offering_id])
    max_week = min(104, max(selected["max_week"], max((item["weeks"][0] for item in items), default=0)))
    live_week = ((today - monday).days // 7 + 1) if monday and selected["status"] == "current" else 0
    weeks = _build_week_deck(items, max_week=max_week, cur_week=live_week, week1_monday=monday, session_no_map=session_map)
    selected.update({
        "focus_week": len(weeks) if selected["status"] == "ended" else (min(max(live_week, 1), len(weeks)) if weeks else 0),
        "live_cur_week": live_week, "max_week": max_week, "week1_monday": monday.isoformat() if monday else "",
    })
    return {
        "status": "success", "has_data": bool(items),
        "message": f"有 {unpositioned_count} 次课尚未设置完整的日期或节次，请进入课堂查看。" if unpositioned_count else "",
        "terms": terms, "selected_term": selected,
        "filters": {"course": "", "class_label": "", "course_options": sorted({i["course_name"] for i in items}),
                    "class_options": sorted({i["class_label"] for i in items})},
        "summary": {"course_count": len({i["course_name"] for i in items}), "slot_count": len(items),
                    "total_hours": sum(i["total_hours"] for i in items), "cur_week": live_week,
                    "max_week": max_week, "term_status": selected["status"], "unpositioned_count": unpositioned_count},
        "courses": _build_course_stats(items), "weeks": weeks,
        "section_range": {"min": 1, "max": max(11, max((max(i["sections"]) for i in items), default=11))},
    }
