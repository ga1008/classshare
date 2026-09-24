"""可调时段 (availability) for the timetable editor — local computation only.

Combines three facts for one lesson occurrence the teacher wants to move:

* the teacher's own timetable (from the published overview);
* the students' other courses — 行政班 timetables of every admin class that
  feeds the lesson's teaching class (``academic_class_timetable_slots``);
* the classroom's occupancy — a synced room timetable when 教务 exposes one,
  otherwise targeted per-slot verdicts (``academic_room_slot_checks``).

Verdict levels per target slot:

* ``block``   students (or the teacher) have another course → never allowed;
* ``room``    students are free but the room is taken → allowed, needs another room;
* ``ok``      everything free;
* ``unknown`` room occupancy not cached yet (students free).

Network access lives in ``academic_availability_sync_service``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ..db.schema_schedule_availability import ensure_schedule_availability_schema


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _int_list(value: Any) -> list[int]:
    if isinstance(value, str):
        try:
            value = json.loads(value or "[]")
        except ValueError:
            value = []
    result: list[int] = []
    for item in value or []:
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if number > 0 and number not in result:
            result.append(number)
    return sorted(result)


def _norm(text: Any) -> str:
    return _clean(text).replace(" ", "").lower()


# ---------------------------------------------------------------------------
# Scope resolution: which admin classes feed a teaching class
# ---------------------------------------------------------------------------

def admin_classes_for_teaching_class(conn, teacher_id: int, teaching_class_id: str) -> list[dict[str, Any]]:
    """Admin classes (行政班 bj_id + name) whose students sit in the teaching class."""
    try:
        rows = conn.execute(
            """
            SELECT admin_class_code, admin_class_name, COUNT(*) AS n
            FROM teacher_academic_roster_memberships
            WHERE teacher_id = ? AND teaching_class_id = ? AND admin_class_code <> ''
            GROUP BY admin_class_code, admin_class_name ORDER BY n DESC
            """,
            (int(teacher_id), _clean(teaching_class_id)),
        ).fetchall()
    except Exception:
        return []
    return [{"code": _clean(r["admin_class_code"]), "name": _clean(r["admin_class_name"]), "count": int(r["n"])} for r in rows]


# ---------------------------------------------------------------------------
# Slot storage
# ---------------------------------------------------------------------------

def replace_class_slots(conn, *, year: str, term: str, scope_kind: str, scope_key: str, scope_name: str,
                        slots: list[dict[str, Any]], source_path: str, school_code: str = "gxufl") -> int:
    ensure_schedule_availability_schema(conn)
    conn.execute(
        "DELETE FROM academic_class_timetable_slots WHERE school_code = ? AND academic_year = ? AND academic_term = ? AND scope_kind = ? AND scope_key = ?",
        (school_code, year, term, scope_kind, scope_key),
    )
    now = _now_iso()
    for slot in slots:
        conn.execute(
            """
            INSERT INTO academic_class_timetable_slots (school_code, academic_year, academic_term, scope_kind, scope_key, scope_name,
                weekday, sections_json, weeks_json, course_name, teaching_class_name, room, teacher_name, source_path, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (school_code, year, term, scope_kind, scope_key, scope_name, int(slot.get("weekday") or 0),
             json.dumps(_int_list(slot.get("sections"))), json.dumps(_int_list(slot.get("weeks"))),
             _clean(slot.get("course_name")), _clean(slot.get("teaching_class_name")), _clean(slot.get("room")),
             _clean(slot.get("teacher_name")), source_path, now),
        )
    return len(slots)


def replace_room_slots(conn, *, year: str, term: str, room_id: str, room_name: str, slots: list[dict[str, Any]],
                       source_path: str, school_code: str = "gxufl") -> int:
    ensure_schedule_availability_schema(conn)
    conn.execute(
        "DELETE FROM academic_room_timetable_slots WHERE school_code = ? AND academic_year = ? AND academic_term = ? AND room_id = ?",
        (school_code, year, term, room_id),
    )
    now = _now_iso()
    for slot in slots:
        conn.execute(
            """
            INSERT INTO academic_room_timetable_slots (school_code, academic_year, academic_term, room_id, room_name,
                weekday, sections_json, weeks_json, course_name, teaching_class_name, teacher_name, source_path, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (school_code, year, term, room_id, room_name, int(slot.get("weekday") or 0),
             json.dumps(_int_list(slot.get("sections"))), json.dumps(_int_list(slot.get("weeks"))),
             _clean(slot.get("course_name")), _clean(slot.get("teaching_class_name")), _clean(slot.get("teacher_name")),
             source_path, now),
        )
    return len(slots)


def record_room_slot_check(conn, *, year: str, term: str, room_id: str, room_name: str, week: int, weekday: int,
                           sections: list[int], status: str, detail: str = "", school_code: str = "gxufl") -> None:
    ensure_schedule_availability_schema(conn)
    key = json.dumps(_int_list(sections))
    conn.execute(
        "DELETE FROM academic_room_slot_checks WHERE school_code = ? AND academic_year = ? AND academic_term = ? AND room_id = ? AND week = ? AND weekday = ? AND sections_json = ?",
        (school_code, year, term, room_id, int(week), int(weekday), key),
    )
    conn.execute(
        """
        INSERT INTO academic_room_slot_checks (school_code, academic_year, academic_term, room_id, room_name, week, weekday,
            sections_json, status, detail, checked_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (school_code, year, term, room_id, room_name, int(week), int(weekday), key, status, _clean(detail)[:400], _now_iso()),
    )


def save_sync_state(conn, teacher_id: int, *, year: str, term: str, status: str, message: str, class_scope_count: int = 0,
                    class_slot_count: int = 0, room_count: int = 0, room_slot_count: int = 0,
                    source_summary: list | None = None, synced: bool = False) -> None:
    ensure_schedule_availability_schema(conn)
    now = _now_iso()
    conn.execute(
        "DELETE FROM academic_availability_sync_state WHERE teacher_id = ? AND academic_year = ? AND academic_term = ?",
        (int(teacher_id), year, term),
    )
    conn.execute(
        """
        INSERT INTO academic_availability_sync_state (teacher_id, academic_year, academic_term, status, message, class_scope_count,
            class_slot_count, room_count, room_slot_count, source_summary_json, synced_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (int(teacher_id), year, term, status, _clean(message)[:600], int(class_scope_count), int(class_slot_count),
         int(room_count), int(room_slot_count), json.dumps(source_summary or [], ensure_ascii=False), now if synced else "", now),
    )


def load_sync_state(conn, teacher_id: int, year: str, term: str) -> dict[str, Any]:
    ensure_schedule_availability_schema(conn)
    row = conn.execute(
        "SELECT * FROM academic_availability_sync_state WHERE teacher_id = ? AND academic_year = ? AND academic_term = ?",
        (int(teacher_id), year, term),
    ).fetchone()
    if row is None:
        return {"status": "never", "message": "尚未同步学生课表与教室占用。", "synced_at": "", "updated_at": "",
                "class_scope_count": 0, "class_slot_count": 0, "room_count": 0, "room_slot_count": 0}
    data = dict(row)
    return {key: data.get(key) for key in ("status", "message", "synced_at", "class_scope_count", "class_slot_count",
                                           "room_count", "room_slot_count", "updated_at")}


# ---------------------------------------------------------------------------
# Availability matrix for one lesson
# ---------------------------------------------------------------------------

def _lesson_by_key(overview: dict[str, Any], event_key: str) -> tuple[dict[str, Any] | None, int]:
    for week in overview.get("weeks") or []:
        for lesson in week.get("lessons") or []:
            if _clean(lesson.get("event_key")) == event_key and not lesson.get("edit_ghost"):
                return lesson, int(week.get("week_index") or 0)
    return None, 0


def _class_slots(conn, *, year: str, term: str, scope_keys: list[tuple[str, str]], school_code: str = "gxufl") -> list[dict[str, Any]]:
    if not scope_keys:
        return []
    ensure_schedule_availability_schema(conn)
    result = []
    for kind, key in scope_keys:
        rows = conn.execute(
            "SELECT * FROM academic_class_timetable_slots WHERE school_code = ? AND academic_year = ? AND academic_term = ? AND scope_kind = ? AND scope_key = ?",
            (school_code, year, term, kind, key),
        ).fetchall()
        result.extend(dict(r) for r in rows)
    return result


def _room_slots(conn, *, year: str, term: str, room_id: str, school_code: str = "gxufl") -> list[dict[str, Any]]:
    if not room_id:
        return []
    rows = conn.execute(
        "SELECT * FROM academic_room_timetable_slots WHERE school_code = ? AND academic_year = ? AND academic_term = ? AND room_id = ?",
        (school_code, year, term, room_id),
    ).fetchall()
    return [dict(r) for r in rows]


def _room_checks(conn, *, year: str, term: str, room_id: str, school_code: str = "gxufl") -> list[dict[str, Any]]:
    if not room_id:
        return []
    rows = conn.execute(
        "SELECT * FROM academic_room_slot_checks WHERE school_code = ? AND academic_year = ? AND academic_term = ? AND room_id = ?",
        (school_code, year, term, room_id),
    ).fetchall()
    return [dict(r) for r in rows]


def _same_lesson(slot: dict[str, Any], lesson: dict[str, Any]) -> bool:
    """A class timetable naturally contains the lesson being moved; do not count it as a clash."""
    tcn = _norm(lesson.get("teaching_class_name"))
    if tcn and _norm(slot.get("teaching_class_name")) == tcn:
        return True
    return bool(_norm(slot.get("course_name"))) and _norm(slot.get("course_name")) == _norm(lesson.get("course_name")) \
        and _norm(slot.get("teacher_name")) in ("", _norm(lesson.get("teacher_name")))


def room_identity(lesson: dict[str, Any]) -> tuple[str, str]:
    """(room_id, room_name) for the lesson's current room, best effort."""
    return _clean(lesson.get("classroom_id") or lesson.get("room_id")), _clean(lesson.get("classroom"))


def resolve_room_id(conn, room_name: str, *, school_code: str = "gxufl") -> str:
    """Map a 教务 room display name to its place id via the synced teaching places."""
    name = _clean(room_name)
    if not name:
        return ""
    try:
        row = conn.execute(
            "SELECT place_id FROM teacher_academic_teaching_places WHERE school_code = ? AND (room_full_name = ? OR room_name = ?) AND place_id <> '' LIMIT 1",
            (school_code, name, name),
        ).fetchone()
    except Exception:
        return ""
    return _clean(row["place_id"]) if row else ""


def build_lesson_availability(conn, teacher_id: int, overview: dict[str, Any], event_key: str, *,
                              room_id: str = "", room_name: str = "") -> dict[str, Any]:
    """Compact busy map for the editor overlay.

    Returns ``{"event_key", "found", "room", "coverage", "students", "teacher", "room_busy", "room_checked"}``
    where the four maps are ``{week: {weekday: {section: reason_or_status}}}`` with string keys so
    the payload survives JSON round trips unchanged.
    """
    selected = overview.get("selected_term") or {}
    year, term = _clean(selected.get("year")), _clean(selected.get("term"))
    max_week = int(selected.get("max_week") or len(overview.get("weeks") or []) or 0)
    lesson, source_week = _lesson_by_key(overview, event_key)
    if lesson is None:
        return {"event_key": event_key, "found": False}
    teaching_class_id = _clean(lesson.get("teaching_class_id"))
    admin_classes = admin_classes_for_teaching_class(conn, teacher_id, teaching_class_id)
    scope_keys = [("admin_class", c["code"]) for c in admin_classes]
    if teaching_class_id:
        scope_keys.append(("teaching_class", teaching_class_id))
    class_slots = _class_slots(conn, year=year, term=term, scope_keys=scope_keys)

    def mark(target: dict, week: int, weekday: int, section: int, reason: str) -> None:
        target.setdefault(str(week), {}).setdefault(str(weekday), {})
        target[str(week)][str(weekday)].setdefault(str(section), reason)

    students: dict[str, dict] = {}
    for slot in class_slots:
        if _same_lesson(slot, lesson):
            continue
        label = f"{_clean(slot.get('scope_name'))} {_clean(slot.get('course_name'))}".strip() or "其他课程"
        for week in _int_list(slot.get("weeks_json")):
            if week > max(max_week, 1) + 5:
                continue
            for section in _int_list(slot.get("sections_json")):
                mark(students, week, int(slot.get("weekday") or 0), section, label)

    teacher: dict[str, dict] = {}
    for week in overview.get("weeks") or []:
        for other in week.get("lessons") or []:
            key = _clean(other.get("event_key"))
            if key == event_key or other.get("edit_ghost") or other.get("edit_draft"):
                continue
            if other.get("counts_towards_total") is False:
                continue
            for section in _int_list(other.get("sections")):
                mark(teacher, int(week.get("week_index") or 0), int(other.get("weekday") or 0), section, f"本人 {_clean(other.get('course_name'))}")

    rid, rname = room_id, room_name
    if not rid and not rname:
        rid, rname = room_identity(lesson)
    if not rid and rname:
        rid = resolve_room_id(conn, rname)
    room_busy: dict[str, dict] = {}
    room_slots = _room_slots(conn, year=year, term=term, room_id=rid) if rid else []
    for slot in room_slots:
        if _same_lesson(slot, lesson):
            continue
        label = f"{_clean(slot.get('course_name'))} {_clean(slot.get('teaching_class_name'))}".strip() or "已被占用"
        for week in _int_list(slot.get("weeks_json")):
            for section in _int_list(slot.get("sections_json")):
                mark(room_busy, week, int(slot.get("weekday") or 0), section, label)
    room_checked: dict[str, dict] = {}
    for check in _room_checks(conn, year=year, term=term, room_id=rid) if rid else []:
        for section in _int_list(check.get("sections_json")):
            mark(room_checked, int(check.get("week") or 0), int(check.get("weekday") or 0), section, _clean(check.get("status")))
            if _clean(check.get("status")) == "busy":
                mark(room_busy, int(check.get("week") or 0), int(check.get("weekday") or 0), section, _clean(check.get("detail")) or "教室已被占用")

    return {
        "event_key": event_key, "found": True, "source_week": source_week,
        "room": {"id": rid, "name": rname, "timetable_synced": bool(room_slots),
                 "checked_slots": sum(len(d) for w in room_checked.values() for d in w.values())},
        "coverage": {
            "students": "synced" if class_slots else ("no_scope" if not scope_keys else "none"),
            "admin_classes": admin_classes,
            "room": "timetable" if room_slots else ("checks" if room_checked else "none"),
        },
        "students": students, "teacher": teacher, "room_busy": room_busy, "room_checked": room_checked,
    }


def check_slot(availability: dict[str, Any], *, week: int, weekday: int, sections: list[int]) -> dict[str, Any]:
    """Verdict for one candidate slot from a ``build_lesson_availability`` result."""
    w, d = str(int(week)), str(int(weekday))
    reasons: list[str] = []
    for section in sections:
        reason = ((availability.get("students") or {}).get(w) or {}).get(d, {}).get(str(int(section)))
        if reason:
            reasons.append(f"第{section}节：学生有课（{reason}）")
    if reasons:
        return {"level": "block", "reasons": reasons}
    for section in sections:
        reason = ((availability.get("teacher") or {}).get(w) or {}).get(d, {}).get(str(int(section)))
        if reason:
            reasons.append(f"第{section}节：{reason}")
    if reasons:
        return {"level": "block", "reasons": reasons}
    room_reasons = []
    unknown = 0
    for section in sections:
        s = str(int(section))
        busy = ((availability.get("room_busy") or {}).get(w) or {}).get(d, {}).get(s)
        if busy:
            room_reasons.append(f"第{section}节：教室已占用（{busy}）")
            continue
        known = ((availability.get("room_checked") or {}).get(w) or {}).get(d, {}).get(s)
        if not known and not (availability.get("room") or {}).get("timetable_synced"):
            unknown += 1
    if room_reasons:
        return {"level": "room", "reasons": room_reasons}
    if unknown:
        return {"level": "unknown", "reasons": ["教室在该时段的占用情况尚未查询"]}
    return {"level": "ok", "reasons": []}
