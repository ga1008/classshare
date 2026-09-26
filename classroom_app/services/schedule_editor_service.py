"""课表编辑模式 · 本地调课草稿 (teacher timetable edit drafts).

Local, platform-side half of the timetable editor:

* validate a proposed slot against the published term timetable (week range,
  section range, 第一节/早读 forbidden, contiguous sections, no overlap with
  the teacher's own lessons or other drafts);
* persist drafts in ``teacher_schedule_edit_drafts``;
* decorate the shared week deck so the editor can render the original lesson
  as "moved" and the proposed slot as a ghost card;
* look up 教务 classroom ids from the synced teaching-place table.

Pushing drafts to 教务 lives in ``academic_schedule_draft_push_service`` so
this module never opens a network connection.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ..db.schema_schedule_editor import ensure_schedule_editor_schema
from ..db.connection import execute_insert_returning_id
from .academic_service import _serialize_calendar_day_row, build_holiday_lookup, china_today
from .national_holiday_service import calendar_swaps
from .offering_session_resequence_service import plan_offering_resequence
from .schedule_availability_service import build_lesson_availability, check_slot, load_sync_state

WEEKDAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
MIN_SECTION = 2          # 第 1 节是早读，不允许放置课次
DEFAULT_MAX_SECTION = 11
# 课次以两小节为单位放置：2-3 / 4-5 / 6-7 / 8-9 / 10-11；四小节课=两个连续单元，可跨上午/下午/晚上。
PAIR_UNIT = 2
MAX_REASON_LENGTH = 400
ZF_ENTRY_URL = "https://jwxt.gxufl.com/tkgl/ttksq_cxTtksqIndex.html?information=1&doType=details&gnmkdm=N2122&layout=default"

STATUS_LABELS = {
    "draft": "待保存到教务",
    "pushed": "已保存到教务草稿",
    "conflict": "教务冲突待处理",
    "failed": "保存失败",
}


class ScheduleEditError(ValueError):
    """User-facing validation failure (HTTP 400/409 by the router)."""

    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _loads(raw: Any, fallback: Any) -> Any:
    try:
        value = json.loads(raw) if isinstance(raw, str) and raw else raw
    except (TypeError, ValueError):
        return fallback
    return value if value is not None else fallback


def _int_list(value: Any) -> list[int]:
    result: list[int] = []
    for item in value or []:
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if number > 0 and number not in result:
            result.append(number)
    return sorted(result)


def section_label(sections: list[int]) -> str:
    if not sections:
        return ""
    return f"第{sections[0]}节" if len(sections) == 1 else f"第{sections[0]}-{sections[-1]}节"


def weekday_label(weekday: int) -> str:
    return WEEKDAY_LABELS[weekday - 1] if 1 <= weekday <= 7 else ""


def slot_date(week1_monday: str, week: int, weekday: int) -> str:
    try:
        monday = date.fromisoformat(str(week1_monday)[:10])
    except (TypeError, ValueError):
        return ""
    return (monday + timedelta(weeks=week - 1, days=weekday - 1)).isoformat()


def describe_slot(slot: dict[str, Any]) -> str:
    """Human label such as ``第5周 周四 第4-5节 · 知新楼B310``."""
    parts = [f"第{slot.get('week')}周", weekday_label(int(slot.get("weekday") or 0)), section_label(_int_list(slot.get("sections")))]
    text = " ".join(part for part in parts if part)
    room = _clean(slot.get("room"))
    return f"{text} · {room}" if room else text


# ---------------------------------------------------------------------------
# Drafts persistence
# ---------------------------------------------------------------------------

def serialize_draft(row: Any) -> dict[str, Any]:
    data = dict(row)
    original = _loads(data.get("original_json"), {}) or {}
    proposed = _loads(data.get("proposed_json"), {}) or {}
    status = _clean(data.get("status")) or "draft"
    return {
        "id": int(data["id"]),
        "teacher_id": int(data["teacher_id"]),
        "year": data.get("academic_year", ""),
        "term": data.get("academic_term", ""),
        "event_key": data.get("event_key", ""),
        "teaching_class_id": data.get("teaching_class_id", ""),
        "teaching_class_name": data.get("teaching_class_name", ""),
        "course_name": data.get("course_name", ""),
        "class_label": data.get("class_label", ""),
        "change_kind": data.get("change_kind", "move"),
        "original": original,
        "proposed": proposed,
        "original_label": describe_slot(original),
        "proposed_label": describe_slot(proposed),
        "reason": data.get("reason", ""),
        "note": data.get("note", ""),
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "remote_ttk_id": data.get("remote_ttk_id", ""),
        "remote_detail_id": data.get("remote_detail_id", ""),
        "remote_label": data.get("remote_label", ""),
        "remote_message": data.get("remote_message", ""),
        "remote_conflict": _loads(data.get("remote_conflict_json"), {}) or {},
        "room_status": _clean(data.get("room_status")) or "unknown",
        "availability": _loads(data.get("availability_json"), {}) or {},
        "pushed_at": data.get("pushed_at", ""),
        "created_at": data.get("created_at", ""),
        "updated_at": data.get("updated_at", ""),
    }


def list_drafts(conn, teacher_id: int, year: str, term: str) -> list[dict[str, Any]]:
    ensure_schedule_editor_schema(conn)
    rows = conn.execute(
        """
        SELECT * FROM teacher_schedule_edit_drafts
        WHERE teacher_id = ? AND academic_year = ? AND academic_term = ?
        ORDER BY id ASC
        """,
        (int(teacher_id), _clean(year), _clean(term)),
    ).fetchall()
    return [serialize_draft(row) for row in rows]


def get_draft(conn, teacher_id: int, draft_id: int) -> dict[str, Any] | None:
    ensure_schedule_editor_schema(conn)
    row = conn.execute(
        "SELECT * FROM teacher_schedule_edit_drafts WHERE id = ? AND teacher_id = ?",
        (int(draft_id), int(teacher_id)),
    ).fetchone()
    return serialize_draft(row) if row else None


def delete_draft(conn, teacher_id: int, draft_id: int) -> dict[str, Any]:
    draft = get_draft(conn, teacher_id, draft_id)
    if draft is None:
        raise ScheduleEditError("草稿不存在或不属于当前教师。", status_code=404)
    if draft["status"] == "pushed":
        raise ScheduleEditError("该变更已保存到教务草稿，请先「从教务撤回」再删除。", status_code=409)
    conn.execute("DELETE FROM teacher_schedule_edit_drafts WHERE id = ? AND teacher_id = ?", (int(draft_id), int(teacher_id)))
    return draft


def update_draft_remote_state(conn, draft_id: int, *, status: str, remote_ttk_id: str = "", remote_detail_id: str = "",
                              remote_label: str = "", remote_message: str = "", conflict: dict | None = None,
                              pushed: bool = False) -> None:
    ensure_schedule_editor_schema(conn)
    now = _now_iso()
    conn.execute(
        """
        UPDATE teacher_schedule_edit_drafts
        SET status = ?, remote_ttk_id = ?, remote_detail_id = ?, remote_label = ?, remote_message = ?,
            remote_conflict_json = ?, pushed_at = CASE WHEN ? = 1 THEN ? ELSE pushed_at END, updated_at = ?
        WHERE id = ?
        """,
        (status, _clean(remote_ttk_id), _clean(remote_detail_id), _clean(remote_label)[:400], _clean(remote_message)[:2000],
         json.dumps(conflict or {}, ensure_ascii=False), 1 if pushed else 0, now, now, int(draft_id)),
    )


# ---------------------------------------------------------------------------
# Term context + validation
# ---------------------------------------------------------------------------

def _lesson_index(overview: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for week in overview.get("weeks") or []:
        for lesson in week.get("lessons") or []:
            key = _clean(lesson.get("event_key"))
            if key and not lesson.get("edit_ghost"):
                index.setdefault(key, {**lesson, "week_index": int(week.get("week_index") or lesson.get("week_index") or 0)})
    return index


def pair_starts(max_section: int) -> list[int]:
    """Legal start sections for a lesson: 2, 4, 6, 8, 10 (… up to the term's last pair)."""
    return list(range(MIN_SECTION, max(MIN_SECTION, int(max_section)) , PAIR_UNIT))


def _term_context(overview: dict[str, Any], *, today: date | None = None) -> dict[str, Any]:
    selected = overview.get("selected_term") or {}
    max_week = int(selected.get("max_week") or len(overview.get("weeks") or []) or 0)
    section_range = overview.get("section_range") or {}
    max_section = max(DEFAULT_MAX_SECTION, int(section_range.get("max") or DEFAULT_MAX_SECTION))
    week1_monday = _clean(selected.get("week1_monday"))
    try:
        semester_id = int(selected.get("semester_id") or 0)
    except (TypeError, ValueError):
        semester_id = 0
    return {
        "year": _clean(selected.get("year")), "term": _clean(selected.get("term")),
        "max_week": max_week, "max_section": max_section, "pair_starts": pair_starts(max_section),
        "week1_monday": week1_monday, "semester_id": semester_id,
        "today": (today or china_today()).isoformat(),
        "editable": overview.get("schedule_source") == "academic" and bool(overview.get("has_data")),
    }


# ---------------------------------------------------------------------------
# Term calendar: holidays + make-up workdays (调休) inside the term
# ---------------------------------------------------------------------------

def _term_bounds(context: dict[str, Any]) -> tuple[date | None, date | None]:
    try:
        start = date.fromisoformat(context["week1_monday"])
    except (KeyError, TypeError, ValueError):
        return None, None
    return start, start + timedelta(days=max(1, int(context.get("max_week") or 1)) * 7 - 1)


def _date_week_weekday(iso_date: str, week1_monday: str) -> tuple[int, int]:
    try:
        day, monday = date.fromisoformat(iso_date), date.fromisoformat(week1_monday)
    except (TypeError, ValueError):
        return 0, 0
    return (day - monday).days // 7 + 1, day.isoweekday()


def build_term_calendar(conn, context: dict[str, Any]) -> dict[str, Any]:
    """Holiday / make-up workday days for the term plus swap pairs (workday → replaced weekday).

    School calendar rows (``academic_semester_calendar_days``, synced from 教务/AI)
    override the shared holiday lookup; the lookup (curated + national feed) fills
    in the make-up target when the school row does not say which weekday is followed.
    """
    start, end = _term_bounds(context)
    if start is None or end is None:
        return {"days": [], "swaps": [], "start": "", "end": ""}
    lookup = {k: dict(v) for k, v in build_holiday_lookup({start.year, end.year}).items()}
    if context.get("semester_id"):
        try:
            rows = conn.execute(
                "SELECT semester_id, date, week_index, weekday, day_type, label, source, source_url, confidence, metadata_json "
                "FROM academic_semester_calendar_days WHERE semester_id = ? AND day_type IN ('holiday', 'workday') ORDER BY date",
                (int(context["semester_id"]),),
            ).fetchall()
        except Exception:  # table missing in minimal fixtures → lookup only
            rows = []
        for row in rows:
            item = _serialize_calendar_day_row(row)
            base = lookup.get(item["date"], {})
            merged = {**base, "kind": item["kind"], "label": item["label"] or base.get("label", ""),
                      "source": item["source"] or base.get("source", ""), "source_url": item["source_url"] or base.get("source_url", "")}
            if item["makeup_for_date"]:
                merged["makeup_for_date"], merged["makeup_for_weekday"] = item["makeup_for_date"], item["makeup_for_weekday"]
                merged["inferred"] = bool(item["metadata"].get("inferred"))
            lookup[item["date"]] = merged
    days = []
    for iso_date, info in sorted(lookup.items()):
        try:
            day = date.fromisoformat(iso_date)
        except ValueError:
            continue
        if not start <= day <= end or info.get("kind") not in ("holiday", "workday"):
            continue
        week, weekday = _date_week_weekday(iso_date, context["week1_monday"])
        entry = {"date": iso_date, "kind": info["kind"], "label": str(info.get("label") or ""), "week": week, "weekday": weekday,
                 "source": str(info.get("source") or "built_in"), "makeup_for_date": str(info.get("makeup_for_date") or ""),
                 "makeup_for_weekday": str(info.get("makeup_for_weekday") or ""), "inferred": bool(info.get("inferred"))}
        if entry["makeup_for_date"]:
            entry["makeup_week"], entry["makeup_weekday"] = _date_week_weekday(entry["makeup_for_date"], context["week1_monday"])
        days.append(entry)
    swaps = calendar_swaps({d["date"]: d for d in days}, start, end)
    for swap in swaps:
        swap["week"], swap["weekday"] = _date_week_weekday(swap["workday_date"], context["week1_monday"])
        swap["makeup_week"], swap["makeup_weekday"] = _date_week_weekday(swap["makeup_for_date"], context["week1_monday"])
    return {"days": days, "swaps": swaps, "start": start.isoformat(), "end": end.isoformat()}


def calendar_day_info(calendar: dict[str, Any], iso_date: str) -> dict[str, Any] | None:
    return next((d for d in calendar.get("days") or [] if d["date"] == iso_date), None)


def effective_slot(calendar: dict[str, Any], week: int, weekday: int, iso_date: str) -> tuple[int, int]:
    """(week, weekday) whose timetable is followed on ``iso_date`` (调休上课日走被补那天的课表)."""
    info = calendar_day_info(calendar, iso_date)
    if info and info["kind"] == "workday" and info.get("makeup_for_date") and info.get("makeup_week"):
        return int(info["makeup_week"]), int(info["makeup_weekday"])
    return week, weekday


def _normalize_proposed(payload: dict[str, Any], original: dict[str, Any], context: dict[str, Any],
                        calendar: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        week = int(payload.get("week") or 0)
        weekday = int(payload.get("weekday") or 0)
    except (TypeError, ValueError) as exc:
        raise ScheduleEditError("目标周次/星期必须是数字。") from exc
    sections = _int_list(payload.get("sections"))
    if not sections and payload.get("start_section"):
        try:
            start = int(payload["start_section"])
        except (TypeError, ValueError) as exc:
            raise ScheduleEditError("起始节次必须是数字。") from exc
        sections = list(range(start, start + len(original["sections"])))
    if not 1 <= week <= max(1, context["max_week"]):
        raise ScheduleEditError(f"目标周次须在第 1–{context['max_week']} 周之间。")
    if not 1 <= weekday <= 7:
        raise ScheduleEditError("目标星期须在周一到周日之间。")
    if not sections:
        raise ScheduleEditError("请选择目标节次。")
    if sections != list(range(sections[0], sections[0] + len(sections))):
        raise ScheduleEditError("目标节次必须连续。")
    if len(sections) != len(original["sections"]):
        raise ScheduleEditError(f"该课次占用 {len(original['sections'])} 节，目标节次数量必须一致。")
    if sections[0] < MIN_SECTION:
        raise ScheduleEditError("第 1 节为早读时段，不允许放置课次。")
    if sections[-1] > context["max_section"]:
        raise ScheduleEditError(f"目标节次超出本学期节次范围（最多第 {context['max_section']} 节）。")
    if sections[0] not in context.get("pair_starts", pair_starts(context["max_section"])):
        raise ScheduleEditError("课次须以两小节为单位放置：只能从第 2、4、6、8、10 节开始（如 2-3、4-5、6-7、8-9、10-11）。")
    target_date = slot_date(context["week1_monday"], week, weekday)
    today = _clean(context.get("today"))
    if today and target_date and target_date < today:
        raise ScheduleEditError("目标时间已经过去，不能把课次放到过去的日期。")
    day_info = calendar_day_info(calendar or {}, target_date) if target_date else None
    if day_info and day_info["kind"] == "holiday":
        raise ScheduleEditError(f"目标日期为节假日（{day_info.get('label') or '放假'}），不能安排课程。")
    room = _clean(payload.get("room") or payload.get("room_name"))
    room_id = _clean(payload.get("room_id"))
    if not room and not room_id:
        room, room_id = original.get("room", ""), original.get("room_id", "")
    proposed = {
        "week": week, "weekday": weekday, "sections": sections,
        "date": target_date,
        "room": room, "room_id": room_id,
    }
    if day_info and day_info["kind"] == "workday" and day_info.get("makeup_for_date"):
        # 调休上课日：当天按被补那天的课表上课，冲突/可调时段都按那一天判断。
        proposed.update({"calendar_kind": "workday", "follows_date": day_info["makeup_for_date"],
                         "follows_weekday": day_info.get("makeup_for_weekday", ""), "calendar_label": day_info.get("label", "")})
    return proposed


def _overlaps(a_sections: list[int], b_sections: list[int]) -> bool:
    return bool(set(a_sections) & set(b_sections))


def _find_conflicts(overview: dict[str, Any], drafts: list[dict[str, Any]], *, event_key: str,
                    proposed: dict[str, Any]) -> list[str]:
    """Own-timetable overlap check: lessons in the target week (excluding lessons
    this teacher is moving away) plus other drafts' proposed slots."""
    moving_away = {d["event_key"] for d in drafts if d["event_key"] != event_key} | {event_key}
    conflicts: list[str] = []
    for week in overview.get("weeks") or []:
        if int(week.get("week_index") or 0) != proposed["week"]:
            continue
        for lesson in week.get("lessons") or []:
            if lesson.get("edit_ghost") or lesson.get("counts_towards_total") is False:
                continue
            if _clean(lesson.get("event_key")) in moving_away:
                continue
            if int(lesson.get("weekday") or 0) == proposed["weekday"] and _overlaps(_int_list(lesson.get("sections")), proposed["sections"]):
                conflicts.append(f"{lesson.get('course_name', '')} {section_label(_int_list(lesson.get('sections')))}")
    for draft in drafts:
        if draft["event_key"] == event_key:
            continue
        other = draft["proposed"]
        if int(other.get("week") or 0) == proposed["week"] and int(other.get("weekday") or 0) == proposed["weekday"] \
                and _overlaps(_int_list(other.get("sections")), proposed["sections"]):
            conflicts.append(f"{draft['course_name']}（已计划调至此处）")
    return conflicts


def save_draft(conn, teacher_id: int, overview: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Create or replace the draft for one lesson occurrence.

    ``overview`` must be the *undecorated* term overview (from
    ``build_teacher_course_schedule_overview``) so ghost cards never count as
    conflicts against themselves.
    """
    ensure_schedule_editor_schema(conn)
    context = _term_context(overview)
    if not context["editable"]:
        raise ScheduleEditError("当前学期尚无教务正式课表，请先「同步教务课表」后再编辑。", status_code=409)
    event_key = _clean(payload.get("event_key"))
    lesson = _lesson_index(overview).get(event_key)
    if lesson is None:
        raise ScheduleEditError("未找到要调整的课次，请刷新后重试。", status_code=404)
    if lesson.get("counts_towards_total") is False:
        raise ScheduleEditError("待审核的拟安排不能再次调整，请等待教务审批结果。", status_code=409)
    teaching_class_id = _clean(lesson.get("teaching_class_id"))
    if not teaching_class_id:
        raise ScheduleEditError("该课次缺少教务教学班标识，无法生成调课草稿；请重新同步教务课表。", status_code=409)
    original = {
        "week": int(lesson.get("week_index") or 0), "weekday": int(lesson.get("weekday") or 0),
        "sections": _int_list(lesson.get("sections")), "date": _clean(lesson.get("actual_date")),
        "room": _clean(lesson.get("classroom")), "room_id": _clean(lesson.get("classroom_id")),
    }
    if original["date"] and context.get("today") and original["date"] < context["today"]:
        raise ScheduleEditError("该课次已经上过，不能再调整。", status_code=409)
    calendar = build_term_calendar(conn, context)
    proposed = _normalize_proposed(payload, original, context, calendar)
    same_time = (proposed["week"], proposed["weekday"], proposed["sections"]) == (original["week"], original["weekday"], original["sections"])
    same_room = (not proposed["room_id"] or proposed["room_id"] == original["room_id"]) and (not proposed["room"] or proposed["room"] == original["room"])
    if same_time and same_room:
        raise ScheduleEditError("目标时间与教室都没有变化，无需保存。")
    drafts = list_drafts(conn, teacher_id, context["year"], context["term"])
    # 调休上课日按被补那天的周次/星期判断本人课表与学生课表。
    check_week, check_weekday = effective_slot(calendar, proposed["week"], proposed["weekday"], proposed["date"])
    conflicts = _find_conflicts(overview, drafts, event_key=event_key,
                                proposed={**proposed, "week": check_week, "weekday": check_weekday})
    if conflicts:
        raise ScheduleEditError("目标时段与您的其他课程重叠：" + "；".join(conflicts), status_code=409)
    # 可调时段：学生有课 → 硬阻止；教室被占 → 允许但标记，需要换教室或二次搜索
    availability = build_lesson_availability(conn, teacher_id, overview, event_key,
                                             room_id=proposed["room_id"], room_name=proposed["room"])
    verdict = check_slot(availability, week=check_week, weekday=check_weekday, sections=proposed["sections"])
    if verdict["level"] == "block" and any("学生有课" in r for r in verdict["reasons"]):
        raise ScheduleEditError("该时段学生有其他课程，不能调整到此：" + "；".join(verdict["reasons"]), status_code=409)
    room_status = {"ok": "free", "room": "busy"}.get(verdict["level"], "unknown")
    availability_snapshot = {"level": verdict["level"], "reasons": verdict["reasons"], "room": availability.get("room"),
                             "coverage": availability.get("coverage"), "checked_at": _now_iso()}
    reason = _clean(payload.get("reason"))[:MAX_REASON_LENGTH]
    note = _clean(payload.get("note"))[:MAX_REASON_LENGTH]
    change_kind = "room" if same_time else "move"
    existing = next((d for d in drafts if d["event_key"] == event_key), None)
    now = _now_iso()
    if existing and existing["status"] == "pushed":
        raise ScheduleEditError("该课次的变更已保存到教务草稿；如需修改请先「从教务撤回」。", status_code=409)
    values = (
        teaching_class_id, _clean(lesson.get("teaching_class_name")), _clean(lesson.get("course_name")),
        _clean(lesson.get("class_label")), change_kind, json.dumps(original, ensure_ascii=False),
        json.dumps(proposed, ensure_ascii=False), reason, note, room_status,
        json.dumps(availability_snapshot, ensure_ascii=False),
    )
    if existing:
        conn.execute(
            """
            UPDATE teacher_schedule_edit_drafts
            SET teaching_class_id = ?, teaching_class_name = ?, course_name = ?, class_label = ?, change_kind = ?,
                original_json = ?, proposed_json = ?, reason = ?, note = ?, room_status = ?, availability_json = ?,
                status = 'draft', remote_message = '', remote_conflict_json = '{}', updated_at = ?
            WHERE id = ?
            """,
            (*values, now, existing["id"]),
        )
        draft_id = existing["id"]
    else:
        draft_id = execute_insert_returning_id(
            conn,
            """
            INSERT INTO teacher_schedule_edit_drafts (
                teacher_id, school_code, academic_year, academic_term, event_key,
                teaching_class_id, teaching_class_name, course_name, class_label, change_kind,
                original_json, proposed_json, reason, note, room_status, availability_json, status, created_at, updated_at
            ) VALUES (?, 'gxufl', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)
            """,
            (int(teacher_id), context["year"], context["term"], event_key, *values, now, now),
        )
    return get_draft(conn, teacher_id, int(draft_id))


# ---------------------------------------------------------------------------
# Payload for the editor page
# ---------------------------------------------------------------------------

def decorate_overview_with_drafts(overview: dict[str, Any], drafts: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a copy of ``overview`` with draft markers and ghost lessons."""
    by_key = {d["event_key"]: d for d in drafts}
    weeks_out = []
    ghosts_by_week: dict[int, list[dict[str, Any]]] = {}
    for week in overview.get("weeks") or []:
        for lesson in week.get("lessons") or []:
            draft = by_key.get(_clean(lesson.get("event_key")))
            if not draft:
                continue
            proposed = draft["proposed"]
            ghost = {
                **lesson,
                "id": f"draft-{draft['id']}", "event_key": f"draft:{draft['id']}", "edit_ghost": True,
                "edit_draft_id": draft["id"], "edit_status": draft["status"], "source_event_key": draft["event_key"],
                "edit_room_status": draft.get("room_status", "unknown"),
                "weekday": proposed["weekday"], "weekday_label": weekday_label(int(proposed["weekday"])),
                "sections": proposed["sections"], "section_label": section_label(proposed["sections"]),
                "classroom": proposed.get("room") or lesson.get("classroom", ""),
                "classroom_short": proposed.get("room") or lesson.get("classroom_short", ""),
                "actual_date": proposed.get("date", ""), "counts_towards_total": False,
                "adjustment": None, "classroom_url": "", "create_url": "", "start_time": "", "end_time": "", "time_label": "",
            }
            ghosts_by_week.setdefault(int(proposed["week"]), []).append(ghost)
    for week in overview.get("weeks") or []:
        week_index = int(week.get("week_index") or 0)
        lessons = []
        for lesson in week.get("lessons") or []:
            draft = by_key.get(_clean(lesson.get("event_key")))
            lessons.append({**lesson, "edit_draft": {
                "id": draft["id"], "status": draft["status"], "status_label": draft["status_label"],
                "change_kind": draft["change_kind"], "proposed": draft["proposed"], "proposed_label": draft["proposed_label"],
                "room_status": draft.get("room_status", "unknown"),
            }} if draft else dict(lesson))
        lessons.extend(ghosts_by_week.get(week_index, []))
        lessons.sort(key=lambda item: (int(item.get("weekday") or 0), (item.get("sections") or [0])[0]))
        draft_ids = {(l.get("edit_draft") or {}).get("id") or l.get("edit_draft_id") for l in lessons if l.get("edit_draft") or l.get("edit_ghost")}
        weeks_out.append({**week, "lessons": lessons, "draft_count": len(draft_ids)})
    return {**overview, "weeks": weeks_out}


def build_editor_payload(conn, teacher_id: int, overview: dict[str, Any]) -> dict[str, Any]:
    context = _term_context(overview)
    drafts = list_drafts(conn, teacher_id, context["year"], context["term"]) if context["year"] else []
    decorated = decorate_overview_with_drafts(overview, drafts)
    calendar = build_term_calendar(conn, context) if context["week1_monday"] else {"days": [], "swaps": []}
    return {
        "overview": decorated,
        "drafts": drafts,
        "editable": context["editable"],
        "today": context["today"],
        "availability_sync": load_sync_state(conn, teacher_id, context["year"], context["term"]) if context["year"] else None,
        "rules": {"min_section": MIN_SECTION, "max_section": context["max_section"], "max_week": context["max_week"],
                  "pair_starts": context["pair_starts"], "pair_unit": PAIR_UNIT},
        "calendar": calendar,
        "zf_entry_url": ZF_ENTRY_URL,
        "status_labels": STATUS_LABELS,
    }


# ---------------------------------------------------------------------------
# Resequence preview: what the lesson order becomes once the drafts take effect
# ---------------------------------------------------------------------------

def plan_resequence_for_drafts(conn, teacher_id: int, overview: dict[str, Any], *, draft_ids: list[int] | None = None) -> dict[str, Any]:
    """Simulate the drafts as approved moves and report the resulting lesson order per course.

    The real re-ordering happens automatically when the approved adjustment is
    reflected by 教务 and synced; this preview lets the teacher see the outcome
    (which week becomes which lesson number) before saving to 教务.
    """
    context = _term_context(overview)
    drafts = list_drafts(conn, teacher_id, context["year"], context["term"]) if context["year"] else []
    if draft_ids:
        wanted = {int(i) for i in draft_ids}
        drafts = [d for d in drafts if int(d["id"]) in wanted]
    index = _lesson_index(overview)
    moves_by_offering: dict[int, dict[int, dict[str, Any]]] = {}
    names: dict[int, str] = {}
    for draft in drafts:
        lesson = index.get(draft["event_key"])
        if not lesson or not lesson.get("session_id") or not lesson.get("class_offering_id"):
            continue
        offering_id, session_id = int(lesson["class_offering_id"]), int(lesson["session_id"])
        proposed = draft["proposed"]
        moves_by_offering.setdefault(offering_id, {})[session_id] = {
            "date": proposed.get("date", ""), "sections": proposed.get("sections") or [],
            "room": proposed.get("room") or "", "week": proposed.get("week") or 0,
        }
        names[offering_id] = f"{lesson.get('course_name', '')} {lesson.get('class_label') or lesson.get('teaching_class_name') or ''}".strip()
    plans = []
    for offering_id, moves in moves_by_offering.items():
        plan = plan_offering_resequence(conn, offering_id, moves=moves, today=context["today"], week1_monday=context["week1_monday"])
        plans.append({**plan, "course_label": names.get(offering_id, ""),
                      "change_count": len(plan["changes"]), "moved_count": sum(1 for c in plan["changes"] if c["moved_directly"])})
    return {"plans": plans, "today": context["today"], "draft_count": len(drafts),
            "note": "调课经教务审批并同步后，剩余课次将按新日期自动重排；课次材料跟随课次序号，不需要重新绑定。"}


def apply_resequence_by_dates(conn, teacher_id: int, overview: dict[str, Any], *, offering_id: int | None = None,
                              note: str = "教师手动按日期重排") -> dict[str, Any]:
    """Re-order the remaining lessons of the teacher's courses in this term by their *current* dates.

    Used when dates were changed outside the 教务 flow (platform-only courses,
    manual session edits) or to repair order after an adjustment landed before
    this rule existed. Past lessons are frozen; ids, numbers and materials stay.
    """
    from .offering_session_resequence_service import apply_offering_resequence

    context = _term_context(overview)
    if not context["semester_id"]:
        raise ScheduleEditError("当前学期未关联课堂，无法重排课次。", status_code=409)
    if offering_id:
        offering_ids = [int(offering_id)]
    else:
        offering_ids = [int(r["id"]) for r in conn.execute(
            "SELECT id FROM class_offerings WHERE teacher_id = ? AND semester_id = ? ORDER BY id",
            (int(teacher_id), int(context["semester_id"])),
        ).fetchall()]
    reports = []
    for oid in offering_ids:
        owner = conn.execute("SELECT teacher_id FROM class_offerings WHERE id = ?", (oid,)).fetchone()
        if not owner or int(owner["teacher_id"]) != int(teacher_id):
            raise ScheduleEditError("只能重排本人课堂的课次。", status_code=403)
        plan = plan_offering_resequence(conn, oid, today=context["today"], week1_monday=context["week1_monday"])
        if not plan["changes"]:
            continue
        reports.append(apply_offering_resequence(conn, plan, teacher_id=teacher_id, semester_id=context["semester_id"], note=note))
    return {"reports": reports, "applied_count": sum(r["applied_count"] for r in reports), "offering_count": len(offering_ids)}


# ---------------------------------------------------------------------------
# Classroom lookup (教务场地 id)
# ---------------------------------------------------------------------------

def search_rooms(conn, keyword: str = "", *, limit: int = 30, school_code: str = "gxufl") -> list[dict[str, Any]]:
    """Rooms are school-wide facts; any teacher's synced copy is authoritative."""
    keyword = _clean(keyword)
    try:
        conn.execute("SELECT 1 FROM teacher_academic_teaching_places LIMIT 1")
    except Exception:  # table absent on a fresh install
        return []
    pattern = f"%{keyword}%"
    rows = conn.execute(
        """
        SELECT place_id, room_code, room_name, room_full_name, building_name, campus_name,
               seat_count, is_schedulable, room_type_name
        FROM teacher_academic_teaching_places
        WHERE school_code = ? AND place_id <> ''
          AND (? = '' OR room_full_name LIKE ? OR room_name LIKE ? OR room_code LIKE ? OR building_name LIKE ?)
        ORDER BY is_schedulable DESC, building_name, room_code
        LIMIT ?
        """,
        (school_code, keyword, pattern, pattern, pattern, pattern, max(1, min(int(limit), 200)) * 4),
    ).fetchall()
    seen: set[str] = set()
    result = []
    for row in rows:
        data = dict(row)
        place_id = _clean(data.get("place_id"))
        if place_id in seen:
            continue
        seen.add(place_id)
        result.append({
            "room_id": place_id,
            "name": _clean(data.get("room_name")) or _clean(data.get("room_full_name")) or place_id,
            "full_name": _clean(data.get("room_full_name")) or _clean(data.get("room_name")),
            "building": _clean(data.get("building_name")), "campus": _clean(data.get("campus_name")),
            "seat_count": int(data.get("seat_count") or 0), "schedulable": bool(data.get("is_schedulable")),
            "type": _clean(data.get("room_type_name")),
        })
        if len(result) >= limit:
            break
    return result
