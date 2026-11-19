"""Scheduled material scope and durable, normalized material credit."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, datetime, time, timedelta

from .login_attendance_rules import SCHOOL_PERIODS, SHANGHAI
from .material_publication_service import _has_publication_mode, get_classroom_publication_context
from .materials_service import get_nearest_assignment_anchor
from .session_learning_materials_service import has_material_bindings_table


MATERIAL_SCORING_POLICY = "material-earned-v1"


def material_scope_key(materials):
    ids = sorted({int(row["id"]) for row in materials})
    return hashlib.sha256(",".join(map(str, ids)).encode("ascii")).hexdigest()


def session_credit_start(row):
    """Use the effective local occurrence. With only a date, wait until it has passed."""
    from .academic_course_sync_service import _parse_section_range
    from .schedule_lesson_metadata import explicit_lesson_time

    if str(row.get("schedule_status") or "").lower() in {"cancelled", "canceled", "suspended"}:
        return None
    try:
        day = date.fromisoformat(str(row.get("session_date") or "")[:10])
    except ValueError:
        return None
    try:
        metadata = json.loads(row.get("schedule_metadata_json") or "{}")
    except (TypeError, ValueError):
        metadata = {}
    section_text = row.get("academic_section_text") or (metadata.get("section_text") if isinstance(metadata, dict) else "")
    first, last, _ = _parse_section_range(section_text)
    sections = list(range(first, last + 1)) if 1 <= first <= last <= 30 else []
    clock = explicit_lesson_time({**row, "sections": sections})["start_time"]
    if not clock and first in SCHOOL_PERIODS:
        clock = SCHOOL_PERIODS[first][0]
    if clock:
        return datetime.combine(day, time.fromisoformat(clock), SHANGHAI)
    return datetime.combine(day + timedelta(days=1), time.min, SHANGHAI)


def load_material_scope(conn, offering_id, *, as_of=None):
    """Return currently eligible files and the next known change from the timetable."""
    now = as_of or datetime.now(SHANGHAI)
    now = now.astimezone(SHANGHAI) if now.tzinfo else now.replace(tzinfo=SHANGHAI)
    # The session body can contain a full lesson plan; load just its timetable.
    columns = ["id", "order_index", "learning_material_id", "session_date", "academic_section_text",
               "academic_time_text", "schedule_metadata_json", "schedule_status"]
    if isinstance(conn, sqlite3.Connection):
        available = {row[1] for row in conn.execute("PRAGMA table_info(class_offering_sessions)")}
        columns = [name for name in columns if name in available]
    sessions = {int(row["id"]): dict(row) for row in conn.execute(
        "SELECT " + ",".join(columns) + " FROM class_offering_sessions WHERE class_offering_id=?", (offering_id,),
    ).fetchall()}
    # The same file can also appear as a homepage/publication link. Any
    # dated lesson binding takes precedence over these independent access grants.
    bindings = {}
    for sid, session in sessions.items():
        if session.get("learning_material_id"):
            bindings.setdefault(int(session["learning_material_id"]), set()).add(sid)
    if has_material_bindings_table(conn):
        # Supplemental bindings constrain timing; they do not by themselves
        # turn an optional document into a required one.
        for row in conn.execute(
            "SELECT material_id, session_id FROM class_offering_learning_materials WHERE class_offering_id=? AND session_id>0",
            (offering_id,),
        ).fetchall():
            bindings.setdefault(int(row["material_id"]), set()).add(int(row["session_id"]))
    rows = conn.execute(
        """SELECT m.id,m.name,m.root_id,m.material_path,m.preview_type,m.check_questions_json,m.check_questions_status
            FROM course_materials m WHERE m.node_type='file' AND m.id IN (
                SELECT learning_material_id FROM class_offering_sessions WHERE class_offering_id=?
                UNION SELECT home_learning_material_id FROM class_offerings WHERE id=?
                UNION SELECT material_id FROM course_material_assignments WHERE class_offering_id=?
            )""", (offering_id, offering_id, offering_id),
    ).fetchall()
    if not rows:
        return [], None
    publications, restricted = get_classroom_publication_context(conn, offering_id)
    starts = {sid: session_credit_start(row) for sid, row in sessions.items()}
    ready = []
    next_start = None
    for row in rows:
        if not get_nearest_assignment_anchor(conn, offering_id, row, publication_nodes=publications, restricted_paths=restricted):
            continue
        lesson_ids = bindings.get(int(row["id"]))
        session_id = None
        if lesson_ids:
            valid = [(starts[sid], sid) for sid in lesson_ids if starts.get(sid) is not None]
            if not valid:
                continue
            earliest, session_id = min(valid)
            if earliest > now:
                next_start = min(next_start, earliest) if next_start else earliest
                continue
        ready.append({**dict(row), "session_id": session_id,
                      "order_index": sessions[session_id]["order_index"] if session_id is not None else 90000})
    ready.sort(key=lambda item: (item["order_index"], str(item["name"] or "").casefold(), item["id"]))
    return ready, next_start


def _ratio(value):
    return min(1.0, max(0.0, float(value or 0)))


def legacy_scored_material_ids(conn, offering_id, candidates, cutoff):
    """Find dated evidence of an old-style requirement, independently of a learner's browsing."""
    if not candidates or not cutoff:
        return set()
    params = [offering_id]
    primary_dates = ""
    if has_material_bindings_table(conn):
        primary_dates = """
            UNION ALL SELECT b.material_id,b.created_at
            FROM class_offering_learning_materials b
            JOIN class_offerings o ON o.id=b.class_offering_id
            LEFT JOIN class_offering_sessions s ON s.class_offering_id=b.class_offering_id AND s.id=b.session_id
            WHERE b.class_offering_id=? AND ((b.session_id=0 AND o.home_learning_material_id=b.material_id)
                OR (b.session_id>0 AND s.learning_material_id=b.material_id))"""
        params.append(offering_id)
    # Only the effective primary and directly distributed files were eligible
    # in the old formula. Archive rows cannot establish historical distribution.
    mode = "a.publication_mode" if _has_publication_mode(conn) else "NULL"
    rows = conn.execute(f"""
        WITH dated(material_id, granted_at) AS (
            SELECT a.material_id,a.created_at FROM course_material_assignments a
            JOIN course_materials m ON m.id=a.material_id
            WHERE a.class_offering_id=? AND ({mode} IN ('explicit','explicit_archive')
                  OR ({mode} IS NULL AND a.assigned_by_teacher_id=m.teacher_id))
            {primary_dates}
        )
        SELECT m.id,m.root_id,m.material_path FROM course_materials m WHERE m.node_type='file' AND m.id IN
            (SELECT material_id FROM dated WHERE granted_at IS NOT NULL
             AND REPLACE(SUBSTR(granted_at,1,19),' ','T')<=?)
    """, (*params, cutoff)).fetchall()
    candidates = set(candidates)
    matches = [row for row in rows if int(row["id"]) in candidates]
    if not matches:
        return set()
    publications, restricted = get_classroom_publication_context(conn, offering_id)
    return {int(row["id"]) for row in matches if get_nearest_assignment_anchor(
        conn, offering_id, row, publication_nodes=publications, restricted_paths=restricted,
    )}


def material_credit(units, previous, *, legacy_credit=0.0, legacy_consumed=None):
    """Allocate only the remaining credit across remaining eligible learning units.

    Keep watermarks for retired material as well, so removing and rebinding a
    previously studied file cannot award the same effort a second time.
    """
    coverage = sum(units.values()) / len(units) if units else 0.0
    upgrading = "credit_watermarks" not in previous
    watermarks = dict(previous.get("credit_watermarks", legacy_consumed or {}))
    legacy_credit = _ratio(previous.get("legacy_credit_ratio", legacy_credit))
    earned = _ratio(previous.get("earned_ratio", legacy_credit))
    remaining_units = sum(1.0 - watermarks.get(key, 0.0) for key in units)
    gained = sum(max(0.0, unit - watermarks.get(key, 0.0)) for key, unit in units.items())
    if remaining_units > 0:
        earned = _ratio(earned + (1.0 - earned) * gained / remaining_units)
    if upgrading:
        earned = max(coverage, earned)
    for key, unit in units.items():
        if unit > watermarks.get(key, 0.0):
            watermarks[key] = unit
    remaining_units = sum(1.0 - watermarks.get(key, 0.0) for key in units)
    return {
        "earned_ratio": earned, "coverage_ratio": coverage, "legacy_credit_ratio": legacy_credit,
        "credit_watermarks": watermarks,
        "next_unit_credit_ratio": (1.0 - earned) / remaining_units if remaining_units > 0 else 0.0,
    }
