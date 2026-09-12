"""One denominator for raw attendance and confirmed classroom projections."""
from __future__ import annotations

from collections import Counter
from typing import Any

KNOWN_STATUSES = {"CHECKED", "UNCHECKED", "SICK_LEAVE", "PERSONAL_LEAVE", "LATE_OR_EARLY"}
VERIFIED_QUALITIES = {"verified", "resolved_historical_difference"}


class ReviewedAttendanceScores(dict):
    """Missing keys are unknown, never the legacy implied zero default."""
    strict = True


def summarize_attendance(cells, *, expected_count: int | None = None) -> dict[str, Any]:
    counts = Counter()
    for cell in cells:
        status = str(cell.get("normalized_status", cell.get("status", "UNKNOWN")) or "UNKNOWN")
        quality = str(cell.get("quality_state") or "verified")
        n = int(cell.get("count", cell.get("n", 1)))
        if quality not in VERIFIED_QUALITIES or status not in KNOWN_STATUSES | {"NOT_APPLICABLE"}:
            counts["UNKNOWN"] += n
        else:
            counts[status] += n
    if expected_count is not None:
        counts["UNKNOWN"] += max(0, int(expected_count) - sum(counts.values()))
    known, unknown, na = sum(counts[s] for s in KNOWN_STATUSES), counts["UNKNOWN"], counts["NOT_APPLICABLE"]
    applicable = known + unknown
    return {"known": known, "unknown": unknown, "not_applicable": na, "applicable": applicable,
            "checked": counts["CHECKED"], "absent": counts["UNCHECKED"], "sick_leave": counts["SICK_LEAVE"],
            "personal_leave": counts["PERSONAL_LEAVE"], "late_or_early": counts["LATE_OR_EARLY"],
            "attendance_rate": round(100 * counts["CHECKED"] / applicable, 2) if applicable and not unknown else None,
            "known_attendance_rate": round(100 * counts["CHECKED"] / known, 2) if known else None,
            "completeness_rate": round(100 * known / applicable, 2) if applicable else None}


def student_run_summaries(conn, run_id: int, student_ids: list[int] | None = None, session_ids: list[int] | None = None) -> dict[int, dict]:
    where, params = ["parse_run_id=?"], [run_id]
    if student_ids is not None:
        if not student_ids:
            return {}
        where.append("student_row_id IN (" + ",".join("?" for _ in student_ids) + ")"); params.extend(student_ids)
    if session_ids is not None:
        if not session_ids:
            return {i: summarize_attendance([]) for i in student_ids or []}
        where.append("session_column_id IN (" + ",".join("?" for _ in session_ids) + ")"); params.extend(session_ids)
    rows = conn.execute("SELECT student_row_id,normalized_status,quality_state,COUNT(*) AS n FROM attendance_report_cells WHERE " + " AND ".join(where) + " GROUP BY student_row_id,normalized_status,quality_state", params).fetchall()
    expected = len(session_ids) if session_ids is not None else int(conn.execute("SELECT COUNT(*) FROM attendance_report_sessions WHERE parse_run_id=?", (run_id,)).fetchone()[0])
    grouped = {i: [] for i in student_ids or []}
    for row in rows:
        grouped.setdefault(int(row["student_row_id"]), []).append(dict(row))
    return {i: summarize_attendance(cells, expected_count=expected) for i, cells in grouped.items()}


def load_confirmed_attendance_facts(conn, *, class_offering_id: int, teacher_id: int, report_id: int | None = None, require_feature=True) -> dict | None:
    """None means no new archive: callers may retain their labelled legacy path.

    A configured but ambiguous/incomplete archive is an explicit unavailable
    result. It must not become a zero mark or silently fall back to older APIs.
    """
    from ..db.connection import get_configured_db_engine
    from .. import config
    if require_feature and not config.ATTENDANCE_CONFIRMED_FACTS_ENABLED:
        return None
    if get_configured_db_engine() == "postgres":
        exists = conn.execute("SELECT 1 FROM information_schema.tables WHERE table_schema=current_schema() AND table_name='attendance_reports'").fetchone()
    else:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='attendance_reports'").fetchone()
    if not exists:
        return None
    params = [teacher_id, class_offering_id]
    extra = ""
    if report_id is not None:
        extra = " AND r.id=?"; params.append(report_id)
    rows = conn.execute("SELECT r.id AS report_id,r.confirmed_parse_run_id,p.*,l.revision AS current_link_revision,l.is_grade_source "
                        "FROM attendance_reports r JOIN smart_attendance_source_bindings b ON b.id=r.binding_id "
                        "JOIN smart_attendance_source_offerings l ON l.binding_id=b.id AND l.link_state='active' "
                        "LEFT JOIN attendance_parse_runs p ON p.id=r.confirmed_parse_run_id "
                        "WHERE b.owner_teacher_id=? AND l.class_offering_id=? AND r.deleted_at IS NULL" + extra, params).fetchall()
    if not rows:
        return None
    if len(rows) > 1 and report_id is None:
        selected = [r for r in rows if r["is_grade_source"]]
        if len(selected) == 1:
            rows = selected
    if len(rows) != 1:
        return {"available": False, "reason": "multiple_sources", "message": "课堂关联多个签到来源，请明确选择计分来源。"}
    run = dict(rows[0])
    if not run.get("confirmed_parse_run_id") or run.get("state") != "confirmed":
        return {"available": False, "reason": "not_confirmed", "message": "签到归档尚未确认，不能用于成绩。"}
    if run.get("mapped_offering_id") != class_offering_id or run.get("binding_link_revision") != run.get("current_link_revision"):
        return {"available": False, "reason": "mapping_changed", "message": "来源课堂关联已改变，请重新核验映射。"}
    sessions = [dict(r) for r in conn.execute("SELECT * FROM attendance_report_sessions WHERE parse_run_id=? ORDER BY source_datetime DESC,remote_checkin_id DESC,id DESC", (run["id"],)).fetchall()]
    if any(not s["local_session_id"] or s["mapping_state"] != "matched" for s in sessions):
        return {"available": False, "reason": "unmapped_sessions", "message": "仍有未映射课次，不能用于课堂成绩。"}
    selected = {}
    for session in sessions:
        key = int(session["local_session_id"])
        if key in selected and selected[key]["source_datetime"] == session["source_datetime"]:
            return {"available": False, "reason": "ambiguous_latest", "message": "同课次点名时间相同，请先核实计分场次。"}
        selected.setdefault(key, session)
    students = [dict(r) for r in conn.execute("SELECT * FROM attendance_report_students WHERE parse_run_id=? ORDER BY row_index", (run["id"],)).fetchall()]
    summaries = student_run_summaries(conn, run["id"], [s["id"] for s in students], [s["id"] for s in selected.values()])
    for student in students:
        student["summary"] = summaries.get(student["id"], summarize_attendance([]))
    return {"available": True, "source": "confirmed_pdf", "report_id": run["report_id"], "parse_run_id": run["id"],
            "source_version_id": run["source_version_id"], "students": students, "sessions": list(selected.values()),
            "all_session_count": len(sessions), "aggregation": "latest_per_local_session"}


def load_confirmed_attendance_scores(conn, *, class_offering_id: int, teacher_id: int, report_id: int | None = None) -> dict[int, float] | None:
    facts = load_confirmed_attendance_facts(conn, class_offering_id=class_offering_id, teacher_id=teacher_id, report_id=report_id)
    if facts is None:
        return None
    if not facts["available"]:
        raise ValueError(facts["message"])
    scores = ReviewedAttendanceScores()
    for student in facts["students"]:
        if not student["local_student_id"] or student["identity_state"] != "matched":
            continue
        rate = student["summary"]["attendance_rate"]
        if rate is None:
            raise ValueError("签到记录尚不完整或没有适用点名，不能作为出勤成绩。")
        if int(student["local_student_id"]) in scores:
            raise ValueError("归档多行映射到同一学生，请先复核身份映射。")
        scores[int(student["local_student_id"])] = rate
    return scores
