"""课次重排 (offering session resequencing after a timetable adjustment).

Business rule (教师端调课): a course's lessons are numbered 1..N and teaching
materials are bound to the *lesson number* (``class_offering_sessions.order_index``,
which the materials tables reference through ``session_id``). When a lesson is
moved in time, lessons that already happened keep their number and date; every
remaining lesson is re-ordered by date so the k-th upcoming slot always carries
the k-th upcoming lesson. Example: 第 3 周第 3 次课调到第 17 周 → 第 4 周变第 3 次,
…, 第 17 周变最后一次. Only dates move — ids, ``order_index``, titles, content
and material bindings never change, so materials automatically follow the new
order.

``plan_offering_resequence`` is pure (no writes) and can simulate pending moves
for a preview; ``apply_offering_resequence`` writes the plan and keeps the
教务 comparison baseline (``academic_schedule_session_bindings.current_json``)
in step so the next sync does not report a local conflict.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from typing import Any

WEEKDAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
RESEQUENCE_EVIDENCE = "resequenced_after_adjustment"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sections(text: Any) -> list[int]:
    if isinstance(text, (list, tuple)):
        return sorted({int(value) for value in text})
    text = str(text or "").strip()
    match = re.fullmatch(r"(?:第)?(\d+)\s*[-—－~～]\s*(\d+)(?:节)?", text)
    if match:
        return list(range(int(match[1]), int(match[2]) + 1))
    if re.fullmatch(r"\d+", text):
        return [int(text)]
    return []


def _section_text(sections: list[int]) -> str:
    if not sections:
        return ""
    return str(sections[0]) if len(sections) == 1 else f"{sections[0]}-{sections[-1]}"


def _as_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _week_index(day: date, week1_monday: date | None, fallback: int) -> int:
    if week1_monday is None:
        return fallback
    return (day - week1_monday).days // 7 + 1


def _slot_from_session(row: dict[str, Any]) -> dict[str, Any]:
    try:
        metadata = json.loads(row.get("schedule_metadata_json") or "{}")
    except (TypeError, ValueError):
        metadata = {}
    metadata = metadata if isinstance(metadata, dict) else {}
    sections = _sections(row.get("academic_section_text") or metadata.get("section_text"))
    return {
        "date": str(row.get("session_date") or ""),
        "sections": sections,
        "room": str(row.get("academic_location") or ""),
        "week": int(row.get("week_index") or 0),
    }


def _normalize_move(raw: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    sections = _sections(raw.get("sections")) or list(fallback.get("sections") or [])
    return {
        "date": str(raw.get("date") or fallback.get("date") or ""),
        "sections": sections,
        "room": str(raw.get("room") if raw.get("room") is not None else fallback.get("room") or ""),
        "week": int(raw.get("week") or fallback.get("week") or 0),
    }


def _slot_sort_key(slot: dict[str, Any]) -> tuple[str, int]:
    sections = slot.get("sections") or [0]
    return str(slot.get("date") or ""), int(sections[0])


def _same_slot(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return (str(a.get("date") or ""), list(a.get("sections") or []), str(a.get("room") or "")) == (
        str(b.get("date") or ""), list(b.get("sections") or []), str(b.get("room") or ""))


def describe_slot(slot: dict[str, Any]) -> str:
    day = _as_date(slot.get("date"))
    if day is None:
        return "未排期"
    week = int(slot.get("week") or 0)
    sections = slot.get("sections") or []
    section_text = f"第 {_section_text(sections)} 节" if sections else ""
    week_text = f"第 {week} 周 " if week else ""
    return f"{week_text}{day.month}/{day.day} {WEEKDAY_LABELS[day.weekday()]} {section_text}".strip()


def describe_change(change: dict[str, Any]) -> str:
    return f"第 {change['order_index']} 次课「{change.get('title') or ''}」：{describe_slot(change['old'])} → {describe_slot(change['new'])}"


def load_offering_sessions(conn, offering_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT id, class_offering_id, order_index, title, session_date, weekday, week_index, academic_section_text,
                  academic_location, slot_section_count, schedule_status, schedule_metadata_json
           FROM class_offering_sessions WHERE class_offering_id = ? ORDER BY order_index ASC, id ASC""",
        (int(offering_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def plan_offering_resequence(conn, offering_id: int, *, moves: dict[int, dict[str, Any]] | None = None,
                             today: date | str | None = None, week1_monday: date | str | None = None,
                             sessions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Compute the date re-assignment for one offering without writing.

    ``moves`` (session_id → slot) simulates pending adjustments (editor drafts)
    on top of the stored dates. Lessons dated before ``today`` that are not
    themselves moved are frozen. Cancelled lessons keep their row untouched.
    """
    today_value = _as_date(today) or date.today()
    monday = _as_date(week1_monday)
    rows = sessions if sessions is not None else load_offering_sessions(conn, offering_id)
    moves = {int(k): v for k, v in (moves or {}).items()}
    frozen: list[dict[str, Any]] = []
    movable: list[dict[str, Any]] = []
    for row in rows:
        slot = _slot_from_session(row)
        entry = {"session_id": int(row["id"]), "order_index": int(row["order_index"]), "title": str(row.get("title") or ""),
                 "old": slot, "moved_directly": int(row["id"]) in moves}
        if str(row.get("schedule_status") or "") == "cancelled":
            continue
        if entry["moved_directly"]:
            entry["effective"] = _normalize_move(moves[int(row["id"])], slot)
            if monday and entry["effective"]["date"]:
                day = _as_date(entry["effective"]["date"])
                if day:
                    entry["effective"]["week"] = _week_index(day, monday, entry["effective"]["week"])
            movable.append(entry)
            continue
        day = _as_date(slot["date"])
        if day is not None and day < today_value:
            frozen.append(entry)
        else:
            entry["effective"] = slot
            movable.append(entry)
    pool = sorted((entry["effective"] for entry in movable), key=_slot_sort_key)
    movable.sort(key=lambda entry: entry["order_index"])
    changes: list[dict[str, Any]] = []
    assignments: list[dict[str, Any]] = []
    for entry, slot in zip(movable, pool):
        new_slot = dict(slot)
        day = _as_date(new_slot["date"])
        if day is not None:
            new_slot["week"] = _week_index(day, monday, int(new_slot.get("week") or 0))
        assignments.append({"session_id": entry["session_id"], "order_index": entry["order_index"], "slot": new_slot})
        if not _same_slot(entry["old"], new_slot):
            changes.append({"session_id": entry["session_id"], "order_index": entry["order_index"], "title": entry["title"],
                            "old": entry["old"], "new": new_slot, "moved_directly": entry["moved_directly"]})
    return {
        "offering_id": int(offering_id),
        "today": today_value.isoformat(),
        "frozen_count": len(frozen),
        "movable_count": len(movable),
        "changes": changes,
        "assignments": assignments,
        "summary": [describe_change(change) for change in changes],
    }


def _patch_binding(conn, teacher_id: int, semester_id: int, session_id: int, slot: dict[str, Any], stamp: str) -> None:
    row = conn.execute(
        "SELECT current_json FROM academic_schedule_session_bindings WHERE teacher_id=? AND semester_id=? AND session_id=?",
        (int(teacher_id), int(semester_id), int(session_id)),
    ).fetchone()
    if not row:
        return
    try:
        current = json.loads(row["current_json"] or "{}")
    except (TypeError, ValueError):
        current = {}
    current = current if isinstance(current, dict) else {}
    day = _as_date(slot.get("date"))
    current.update({
        "date": slot.get("date", ""), "sections": list(slot.get("sections") or []), "room": slot.get("room", ""),
        "week": int(slot.get("week") or current.get("week") or 0),
        "weekday": day.isoweekday() if day else current.get("weekday"),
    })
    conn.execute(
        "UPDATE academic_schedule_session_bindings SET current_json=?, evidence=?, updated_at=? "
        "WHERE teacher_id=? AND semester_id=? AND session_id=?",
        (json.dumps(current, ensure_ascii=False, separators=(",", ":")), RESEQUENCE_EVIDENCE, stamp,
         int(teacher_id), int(semester_id), int(session_id)),
    )


def apply_offering_resequence(conn, plan: dict[str, Any], *, teacher_id: int | None = None, semester_id: int | None = None,
                              note: str = "", stamp: str | None = None) -> dict[str, Any]:
    """Write the plan's changes. Only dates/slots move; ids, order and materials stay."""
    stamp = stamp or _now_iso()
    applied: list[dict[str, Any]] = []
    for change in plan.get("changes") or []:
        slot = change["new"]
        day = _as_date(slot.get("date"))
        if day is None:
            continue
        sections = list(slot.get("sections") or [])
        row = conn.execute(
            "SELECT schedule_metadata_json FROM class_offering_sessions WHERE id=? AND class_offering_id=?",
            (int(change["session_id"]), int(plan["offering_id"])),
        ).fetchone()
        if not row:
            continue
        try:
            metadata = json.loads(row["schedule_metadata_json"] or "{}")
        except (TypeError, ValueError):
            metadata = {}
        metadata = metadata if isinstance(metadata, dict) else {}
        history = list(metadata.get("resequence_history") or [])[-9:]
        history.append({"at": stamp, "from": change["old"], "to": slot, "note": note[:120]})
        metadata.update({"resequence_history": history, "section_text": _section_text(sections)})
        conn.execute(
            """UPDATE class_offering_sessions
               SET session_date=?, weekday=?, week_index=?, academic_section_text=?, academic_location=?,
                   slot_section_count=?, schedule_metadata_json=?, updated_at=?
               WHERE id=? AND class_offering_id=?""",
            (day.isoformat(), day.weekday(), int(slot.get("week") or 0), _section_text(sections), str(slot.get("room") or ""),
             max(1, len(sections)), json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
             stamp, int(change["session_id"]), int(plan["offering_id"])),
        )
        if teacher_id is not None and semester_id is not None:
            _patch_binding(conn, int(teacher_id), int(semester_id), int(change["session_id"]), slot, stamp)
        applied.append(change)
    return {"offering_id": int(plan["offering_id"]), "applied": applied, "applied_count": len(applied),
            "frozen_count": int(plan.get("frozen_count") or 0), "summary": [describe_change(c) for c in applied]}


def resequence_offerings_after_publish(conn, teacher_id: int, semester_id: int, sessions: list[dict[str, Any]],
                                       offering_ids: set[int] | list[int], *, week1_monday: date | str | None,
                                       today: date | str | None, stamp: str) -> list[dict[str, Any]]:
    """Hook for the 教务 sync publisher: after approved adjustments are applied to
    session rows, re-order the remaining lessons of each touched offering.

    ``sessions`` is the publisher's in-memory list (rows carry a ``slot`` dict);
    it is updated in place so the rest of the publication matches official
    lessons against the re-ordered dates. Returns per-offering summaries.
    """
    reports: list[dict[str, Any]] = []
    by_id = {int(row["id"]): row for row in sessions}
    for offering_id in sorted({int(value) for value in offering_ids}):
        scoped = [row for row in sessions if int(row["class_offering_id"]) == offering_id]
        # Feed the publisher's already-updated rows so the plan sees post-approval dates.
        rows = [{**row, "session_date": (row.get("slot") or {}).get("date", row.get("session_date")),
                 "academic_section_text": _section_text((row.get("slot") or {}).get("sections") or _sections(row.get("academic_section_text"))),
                 "academic_location": (row.get("slot") or {}).get("room", row.get("academic_location"))} for row in scoped]
        plan = plan_offering_resequence(conn, offering_id, today=today, week1_monday=week1_monday, sessions=rows)
        if not plan["changes"]:
            continue
        report = apply_offering_resequence(conn, plan, teacher_id=teacher_id, semester_id=semester_id,
                                           note="教务调课审批通过后自动重排", stamp=stamp)
        for change in report["applied"]:
            row = by_id.get(int(change["session_id"]))
            if row is not None:
                new_slot = change["new"]
                row["slot"] = {**(row.get("slot") or {}), "date": new_slot["date"], "sections": list(new_slot["sections"]),
                               "room": new_slot.get("room", "")}
                row["session_date"], row["week_index"] = new_slot["date"], int(new_slot.get("week") or 0)
        reports.append(report)
    return reports
