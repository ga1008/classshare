"""Chronological lesson numbering over stable physical session identities.

Dates and attendance/assignment identities never swap. Only active, positioned
sessions receive teaching ordinals 1..N; cancelled/unpositioned rows retain their
identity and materials in a stable tail. The material service moves complete
teaching-material collections between active ordinals in the same transaction.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime, timezone
from typing import Any

WEEKDAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
RESEQUENCE_EVIDENCE = "chronological_ordinals_v2"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sections(value: Any) -> list[int]:
    if isinstance(value, (list, tuple)):
        try:
            values = {int(item) for item in value}
        except (ValueError, TypeError):
            return []
        return sorted(values) if all(1 <= item <= 30 for item in values) else []
    text = re.sub(r"[第节\s]", "", str(value or ""))
    values = set()
    for part in re.split(r"[,，、]", text):
        match = re.fullmatch(r"(\d+)(?:[-—－~～](\d+))?", part)
        if not match:
            return []
        first, last = int(match[1]), int(match[2] or match[1])
        if not 1 <= first <= last <= 30:
            return []
        values.update(range(first, last + 1))
    return sorted(values)


def _section_text(sections: list[int]) -> str:
    if not sections:
        return ""
    if sections == list(range(sections[0], sections[-1] + 1)):
        return str(sections[0]) if len(sections) == 1 else f"{sections[0]}-{sections[-1]}"
    return ",".join(str(value) for value in sections)


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _slot_from_session(row: dict[str, Any]) -> dict[str, Any]:
    try:
        metadata = json.loads(row.get("schedule_metadata_json") or "{}")
    except (TypeError, ValueError):
        metadata = {}
    metadata = metadata if isinstance(metadata, dict) else {}
    return {"date": str(row.get("session_date") or ""),
            "sections": _sections(row.get("academic_section_text") or metadata.get("section_text")),
            "room": str(row.get("academic_location") or ""), "week": int(row.get("week_index") or 0)}


def _same_slot(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return (str(a.get("date") or ""), list(a.get("sections") or []), str(a.get("room") or "")) == (
        str(b.get("date") or ""), list(b.get("sections") or []), str(b.get("room") or ""))


def is_numbered_session(row: dict[str, Any]) -> bool:
    slot = row.get("slot") or _slot_from_session(row)
    return (str(row.get("schedule_status") or "scheduled") not in {"cancelled", "canceled"}
            and _as_date(slot.get("date")) is not None)


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
    new_label = f"第 {change['order_index']} 次课" if change.get("active", True) else "保留记录（取消或未排期）"
    return f"原第 {change['old_order_index']} 次课「{change.get('title') or ''}」 → {new_label}：{describe_slot(change['new'])}"


def load_offering_sessions(conn, offering_id: int) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(
        "SELECT * FROM class_offering_sessions WHERE class_offering_id=? ORDER BY order_index,id",
        (int(offering_id),)).fetchall()]


def lock_offering_sessions(conn, offering_id: int) -> None:
    """Use the plan editor's course -> offering -> session lock order."""
    from .offering_plan_edit_service import lock_plan_row
    offering = conn.execute("SELECT * FROM class_offerings WHERE id=?", (int(offering_id),)).fetchone()
    if not offering:
        raise ValueError("课堂不存在。")
    course_id = dict(offering).get("course_id")
    if course_id:
        lock_plan_row(conn, "courses", int(course_id))
    lock_plan_row(conn, "class_offerings", int(offering_id))
    for row in conn.execute("SELECT id FROM class_offering_sessions WHERE class_offering_id=? ORDER BY id", (int(offering_id),)).fetchall():
        lock_plan_row(conn, "class_offering_sessions", int(row["id"]))


def plan_offering_resequence(conn, offering_id: int, *, moves: dict[int, dict[str, Any]] | None = None,
                             today: date | str | None = None, week1_monday: date | str | None = None,
                             sessions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Pure preview of ordinal changes. Pending moves are never executable plans.

    Historical dates participate in numbering; their physical facts stay intact.
    Exact simultaneous slots use ID only for deterministic display, never identity
    reconciliation. Unpositioned/cancelled rows receive non-teaching tail indexes.
    """
    rows = sessions if sessions is not None else load_offering_sessions(conn, offering_id)
    moves = {int(key): value for key, value in (moves or {}).items()}
    monday = _as_date(week1_monday)
    entries = []
    for row in rows:
        sid = int(row["id"])
        old = _slot_from_session(row)
        effective = dict(old)
        if sid in moves:
            move = moves[sid]
            effective.update(date=str(move.get("date") or old["date"]),
                             sections=_sections(move.get("sections")) or old["sections"],
                             room=str(move.get("room") if move.get("room") is not None else old["room"]))
        day = _as_date(effective["date"])
        if day and monday:
            effective["week"] = (day - monday).days // 7 + 1
        active = is_numbered_session({**row, "slot": effective})
        entries.append({"session_id": sid, "old_order_index": int(row["order_index"]),
                        "title": str(row.get("title") or ""), "old": old, "new": effective,
                        "slot": effective, "active": active, "sortable": bool(day),
                        "schedule_status": str(row.get("schedule_status") or "scheduled"),
                        "moved_directly": sid in moves})
    active = sorted((entry for entry in entries if entry["active"]),
                    key=lambda entry: (entry["slot"]["date"], tuple(entry["slot"]["sections"]) or (99,),
                                       0 if entry["slot"]["sections"] else entry["old_order_index"], entry["session_id"]))
    tail = sorted((entry for entry in entries if not entry["active"]), key=lambda entry: (entry["old_order_index"], entry["session_id"]))
    assignments = [{**entry, "order_index": index} for index, entry in enumerate(active + tail, 1)]
    changes = [entry for entry in assignments if entry["old_order_index"] != entry["order_index"] or not _same_slot(entry["old"], entry["new"])]
    return {"offering_id": int(offering_id), "today": (_as_date(today) or date.today()).isoformat(),
            "frozen_count": 0, "movable_count": len(active), "active_count": len(active),
            "unpositioned_count": sum(not entry["sortable"] and entry["schedule_status"] not in {"cancelled", "canceled"} for entry in tail),
            "cancelled_count": sum(entry["schedule_status"] in {"cancelled", "canceled"} for entry in tail),
            "preview_only": bool(moves), "changes": changes, "assignments": assignments,
            "summary": [describe_change(change) for change in changes]}


def refresh_snapshot_ordinals(conn, teacher_id: int, semester_id: int, sessions: list[dict[str, Any]], *, stamp: str) -> None:
    """Refresh numbering of stored projections after an explicit local repair."""
    from .academic_schedule_prediction_service import _table_exists
    if not _table_exists(conn, "teacher_academic_schedule_snapshots"):
        return
    row = conn.execute("SELECT lessons_json FROM teacher_academic_schedule_snapshots WHERE teacher_id=? AND semester_id=?",
                       (int(teacher_id), int(semester_id))).fetchone()
    if not row:
        return
    by_id = {int(item["id"]): item for item in sessions}
    totals: dict[int, int] = {}
    for item in sessions:
        oid = int(item["class_offering_id"])
        totals[oid] = totals.get(oid, 0) + int(is_numbered_session(item))
    lessons = json.loads(row["lessons_json"])
    changed = False
    for lesson in lessons:
        session = by_id.get(int(lesson.get("session_id") or 0))
        if session:
            value = int(session["order_index"]) if is_numbered_session(session) else None
            total = totals[int(session["class_offering_id"])]
            changed |= lesson.get("session_no") != value or lesson.get("session_total") != total
            lesson.update(session_no=value, session_total=total)
    if changed:
        conn.execute("UPDATE teacher_academic_schedule_snapshots SET lessons_json=?,revision=revision+1,published_at=? WHERE teacher_id=? AND semester_id=?",
                     (json.dumps(lessons, ensure_ascii=False, separators=(",", ":")), stamp, int(teacher_id), int(semester_id)))


def apply_offering_resequence(conn, plan: dict[str, Any], *, teacher_id: int | None = None, semester_id: int | None = None,
                              note: str = "", stamp: str | None = None) -> dict[str, Any]:
    """Atomically swap ordinals/materials under locks; never exchange real slots."""
    from .session_learning_materials_service import rebind_offering_materials_for_resequence
    if plan.get("preview_only"):
        raise ValueError("待审批调课仅供预览，必须先同步已生效的正式课表。")
    stamp = stamp or _now_iso()
    oid = int(plan["offering_id"])
    if isinstance(conn, sqlite3.Connection) and not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    conn.execute("SAVEPOINT lq_session_resequence")
    try:
        lock_offering_sessions(conn, oid)
        current = load_offering_sessions(conn, oid)
        assignments = list(plan.get("assignments") or [])
        expected = {int(item["session_id"]): item for item in assignments}
        if set(expected) != {int(row["id"]) for row in current} or any(
                int(row["order_index"]) != int(expected[int(row["id"])]["old_order_index"])
                or not _same_slot(_slot_from_session(row), expected[int(row["id"])]["old"])
                or str(row.get("schedule_status") or "scheduled") != expected[int(row["id"])]["schedule_status"]
                for row in current):
            raise ValueError("课次在预览后已变化，请刷新后重试。")
        applied = [item for item in assignments if item["old_order_index"] != item["order_index"]]
        if applied:
            # Immediate UNIQUE(offering, order_index) on both engines requires
            # staging every changed row above the complete current/target range.
            offset = max([int(row["order_index"]) for row in current] + [len(assignments), 0]) + len(assignments) + 1
            for index, item in enumerate(applied):
                conn.execute("UPDATE class_offering_sessions SET order_index=? WHERE id=? AND class_offering_id=?",
                             (offset + index, item["session_id"], oid))
            for item in applied:
                conn.execute("UPDATE class_offering_sessions SET order_index=?,updated_at=? WHERE id=? AND class_offering_id=?",
                             (item["order_index"], stamp, item["session_id"], oid))
            rebind_offering_materials_for_resequence(conn, oid, assignments, stamp=stamp)
            owner = dict(conn.execute("SELECT * FROM class_offerings WHERE id=?", (oid,)).fetchone())
            if owner.get("teacher_id") and owner.get("semester_id"):
                refresh_snapshot_ordinals(conn, int(owner["teacher_id"]), int(owner["semester_id"]),
                                          load_offering_sessions(conn, oid), stamp=stamp)
        conn.execute("RELEASE SAVEPOINT lq_session_resequence")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT lq_session_resequence")
        conn.execute("RELEASE SAVEPOINT lq_session_resequence")
        raise
    return {"offering_id": oid, "applied": applied, "applied_count": len(applied),
            "assignments": assignments, "active_count": int(plan.get("active_count") or 0),
            "frozen_count": 0, "summary": [describe_change(item) for item in applied]}


def resequence_offerings_after_publish(conn, teacher_id: int, semester_id: int, sessions: list[dict[str, Any]],
                                       offering_ids: set[int] | list[int], *, week1_monday: date | str | None,
                                       today: date | str | None, stamp: str) -> list[dict[str, Any]]:
    """Repair every covered offering on every complete sync, including replays."""
    reports = []
    by_id = {int(row["id"]): row for row in sessions}
    for offering_id in sorted({int(value) for value in offering_ids}):
        scoped = [row for row in sessions if int(row["class_offering_id"]) == offering_id]
        rows = [{**row, "session_date": (row.get("slot") or {}).get("date", row.get("session_date")),
                 "academic_section_text": _section_text((row.get("slot") or {}).get("sections") or _sections(row.get("academic_section_text"))),
                 "academic_location": (row.get("slot") or {}).get("room", row.get("academic_location"))} for row in scoped]
        plan = plan_offering_resequence(conn, offering_id, today=today, week1_monday=week1_monday, sessions=rows)
        if not plan["changes"]:
            continue
        report = apply_offering_resequence(conn, plan, teacher_id=teacher_id, semester_id=semester_id,
                                           note="按教务实际日期节次重编序号", stamp=stamp)
        for assignment in report["assignments"]:
            by_id[assignment["session_id"]]["order_index"] = assignment["order_index"]
        reports.append(report)
    return reports
