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

WEEKDAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
MIN_SECTION = 2          # 第 1 节是早读，不允许放置课次
DEFAULT_MAX_SECTION = 11
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


def _term_context(overview: dict[str, Any]) -> dict[str, Any]:
    selected = overview.get("selected_term") or {}
    max_week = int(selected.get("max_week") or len(overview.get("weeks") or []) or 0)
    section_range = overview.get("section_range") or {}
    max_section = max(DEFAULT_MAX_SECTION, int(section_range.get("max") or DEFAULT_MAX_SECTION))
    return {
        "year": _clean(selected.get("year")), "term": _clean(selected.get("term")),
        "max_week": max_week, "max_section": max_section,
        "week1_monday": _clean(selected.get("week1_monday")),
        "editable": overview.get("schedule_source") == "academic" and bool(overview.get("has_data")),
    }


def _normalize_proposed(payload: dict[str, Any], original: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
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
    room = _clean(payload.get("room") or payload.get("room_name"))
    room_id = _clean(payload.get("room_id"))
    if not room and not room_id:
        room, room_id = original.get("room", ""), original.get("room_id", "")
    return {
        "week": week, "weekday": weekday, "sections": sections,
        "date": slot_date(context["week1_monday"], week, weekday),
        "room": room, "room_id": room_id,
    }


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
    proposed = _normalize_proposed(payload, original, context)
    same_time = (proposed["week"], proposed["weekday"], proposed["sections"]) == (original["week"], original["weekday"], original["sections"])
    same_room = (not proposed["room_id"] or proposed["room_id"] == original["room_id"]) and (not proposed["room"] or proposed["room"] == original["room"])
    if same_time and same_room:
        raise ScheduleEditError("目标时间与教室都没有变化，无需保存。")
    drafts = list_drafts(conn, teacher_id, context["year"], context["term"])
    conflicts = _find_conflicts(overview, drafts, event_key=event_key, proposed=proposed)
    if conflicts:
        raise ScheduleEditError("目标时段与您的其他课程重叠：" + "；".join(conflicts), status_code=409)
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
        json.dumps(proposed, ensure_ascii=False), reason, note,
    )
    if existing:
        conn.execute(
            """
            UPDATE teacher_schedule_edit_drafts
            SET teaching_class_id = ?, teaching_class_name = ?, course_name = ?, class_label = ?, change_kind = ?,
                original_json = ?, proposed_json = ?, reason = ?, note = ?, status = 'draft',
                remote_message = '', remote_conflict_json = '{}', updated_at = ?
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
                original_json, proposed_json, reason, note, status, created_at, updated_at
            ) VALUES (?, 'gxufl', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)
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
    return {
        "overview": decorated,
        "drafts": drafts,
        "editable": context["editable"],
        "rules": {"min_section": MIN_SECTION, "max_section": context["max_section"], "max_week": context["max_week"]},
        "zf_entry_url": ZF_ENTRY_URL,
        "status_labels": STATUS_LABELS,
    }


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
