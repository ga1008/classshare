"""Reviewed local corrections over imported attendance; school snapshots stay intact."""
from __future__ import annotations

import json
import re
import time

from fastapi import HTTPException

from ..db.connection import get_configured_db_engine
from .approval_workflow_service import ApprovalWorkflowError

KEY = "smart_attendance_appeal"
SUBJECT_TYPE = "smart_attendance_source"
STATUSES = {"PRESENT", "ABSENT", "SICK_LEAVE", "PERSONAL_LEAVE", "LATE", "EARLY", "LATE_OR_EARLY"}
LABELS = {"PRESENT": "出勤", "ABSENT": "缺课", "SICK_LEAVE": "病假", "PERSONAL_LEAVE": "事假",
          "LATE": "迟到", "EARLY": "早退", "LATE_OR_EARLY": "迟到或早退", "PENDING": "待核对"}
SNAPSHOT_FIELDS = ("source", "source_key", "source_fingerprint", "source_id", "source_run_id",
                   "class_offering_id", "session_id", "student_id", "teacher_id", "status", "source_status",
                   "name", "student_number", "status_label", "ended_at_ms", "appeal_days", "course_name")


def _load(value):
    try:
        result = json.loads(value or "{}")
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        return {}


def now_ms():
    return int(time.time() * 1000)


def _locator(conn, source_key):
    match = re.fullmatch(r"(api|report):([1-9][0-9]*)", str(source_key))
    if not match:
        raise ApprovalWorkflowError(422, "智慧课堂记录编号无效")
    kind, record_id = match[1], int(match[2])
    if kind == "api":
        row = conn.execute("SELECT s.student_id,c.class_offering_id,c.session_id,c.id AS parent_id "
            "FROM smart_classroom_checkin_students s JOIN smart_classroom_checkin_sessions c ON c.id=s.checkin_session_id "
            "WHERE s.id=? AND s.local_match_status='matched' AND c.match_status='matched' "
            "AND s.class_offering_id=c.class_offering_id AND s.session_id=c.session_id", (record_id,)).fetchone()
    else:
        row = conn.execute("SELECT s.local_student_id AS student_id,p.mapped_offering_id AS class_offering_id,"
            "se.local_session_id AS session_id,r.id AS parent_id FROM attendance_report_cells c "
            "JOIN attendance_report_students s ON s.id=c.student_row_id AND s.parse_run_id=c.parse_run_id "
            "JOIN attendance_report_sessions se ON se.id=c.session_column_id AND se.parse_run_id=c.parse_run_id "
            "JOIN attendance_parse_runs p ON p.id=c.parse_run_id "
            "JOIN attendance_report_versions v ON v.id=p.source_version_id "
            "JOIN attendance_reports r ON r.id=v.report_id AND r.confirmed_parse_run_id=p.id "
            "WHERE c.id=? AND r.deleted_at IS NULL AND p.state='confirmed' AND s.identity_state='matched' "
            "AND se.mapping_state='matched' AND c.quality_state IN ('verified','resolved_historical_difference')",
            (record_id,)).fetchone()
    if not row or not row["student_id"] or not row["class_offering_id"] or not row["session_id"]:
        raise ApprovalWorkflowError(409, "该同步记录尚未确认映射或已被替代，请刷新后核对")
    return {**dict(row), "kind": kind, "record_id": record_id}


def lock_offering(conn, offering_id, reviewer=None):
    suffix = " FOR NO KEY UPDATE" if get_configured_db_engine() == "postgres" else ""
    row = conn.execute("SELECT id,teacher_id FROM class_offerings WHERE id=?" + suffix, (offering_id,)).fetchone()
    if not row:
        raise ApprovalWorkflowError(409, "课堂已归档，请查看申诉处理记录")
    if reviewer and (reviewer.get("role") != "teacher" or int(reviewer["id"]) != int(row["teacher_id"])):
        raise ApprovalWorkflowError(403, "只有当前任课教师可以处理此申诉")
    return dict(row)


def current_fact(conn, source_key, user, *, lock=False, expected_offering=None):
    locator = _locator(conn, source_key)
    offering = int(locator["class_offering_id"])
    if expected_offering is not None and offering != int(expected_offering):
        raise ApprovalWorkflowError(404, "该记录不属于当前课堂")
    if user.get("role") == "student":
        if int(user["id"]) != int(locator["student_id"]):
            raise ApprovalWorkflowError(403, "只能查看或申诉自己的签到")
    elif user.get("role") == "teacher":
        owner = conn.execute("SELECT teacher_id FROM class_offerings WHERE id=?", (offering,)).fetchone()
        if not owner or int(owner["teacher_id"]) != int(user["id"]):
            raise ApprovalWorkflowError(403, "无权读取此课堂签到")
    else:
        raise ApprovalWorkflowError(403, "无权读取此课堂签到")
    if lock:
        lock_offering(conn, offering, user if user.get("role") == "teacher" else None)
        if get_configured_db_engine() == "postgres":
            parent = "smart_classroom_checkin_sessions" if locator["kind"] == "api" else "attendance_reports"
            conn.execute(f"SELECT id FROM {parent} WHERE id=? FOR SHARE", (locator["parent_id"],)).fetchone()
            table = "smart_classroom_checkin_students" if locator["kind"] == "api" else "attendance_report_cells"
            conn.execute(f"SELECT id FROM {table} WHERE id=? FOR SHARE", (locator["record_id"],)).fetchone()
        locator = _locator(conn, source_key)
    from .attendance_dashboard_service import load_student_session_fact
    try:
        options = {"student_id": int(locator["student_id"])} if user.get("role") == "teacher" else {}
        fact = load_student_session_fact(conn, offering, int(locator["session_id"]), user, **options)
    except HTTPException as exc:
        raise ApprovalWorkflowError(exc.status_code, str(exc.detail)) from None
    if not fact or fact.get("source_key") != source_key or not fact.get("source_fingerprint"):
        raise ApprovalWorkflowError(409, "该记录已被较新的有效签到替代，请在统计页面核对当前记录")
    if int(fact.get("student_id") or 0) != int(locator["student_id"]) or int(fact.get("class_offering_id") or 0) != offering:
        raise ApprovalWorkflowError(409, "签到身份关联已变化，请刷新")
    student = conn.execute("SELECT name,student_id_number FROM students WHERE id=?", (locator["student_id"],)).fetchone()
    result = dict(fact)
    if student:
        result.update(name=student["name"], student_number=student["student_id_number"])
    result["status_label"] = LABELS.get(result.get("status"), "待核对")
    return result


def appeal_deadline(fact):
    ended = int(fact.get("ended_at_ms") or 0)
    days = max(1, min(30, int(fact.get("appeal_days") or 7)))
    return ended + days * 86400_000 if ended else None


def _public_appeal(row):
    return {key: row.get(key) for key in ("id", "status", "reason", "decision_note", "created_at", "decided_at")}


def load_smart_appeal_states(conn, facts, *, viewer=None):
    """Batch overlay for already-authorized descriptors; never changes a source row."""
    facts = [dict(f) for f in facts if str(f.get("source_key") or "").startswith(("api:", "report:"))]
    if not facts:
        return {}
    keys = list(dict.fromkeys(f["source_key"] for f in facts))
    rows = []
    for offset in range(0, len(keys), 300):
        chunk = keys[offset:offset + 300]
        rows.extend(dict(r) for r in conn.execute("WITH ranked AS (SELECT *, "
            "ROW_NUMBER() OVER(PARTITION BY subject_id,applicant_user_pk ORDER BY CASE WHEN status='pending' THEN 0 ELSE 1 END,id DESC) AS latest_rank, "
            "ROW_NUMBER() OVER(PARTITION BY subject_id,applicant_user_pk,status ORDER BY id DESC) AS status_rank "
            "FROM approval_requests WHERE request_type=? AND subject_type=? AND subject_id IN (" + ",".join("?" for _ in chunk) + ")) "
            "SELECT * FROM ranked WHERE latest_rank=1 OR (status='approved' AND status_rank=1)", (KEY, SUBJECT_TYPE, *chunk)).fetchall())
    by_subject = {}
    for row in rows:
        by_subject.setdefault((row["subject_id"], int(row["applicant_user_pk"])), []).append(row)
    result = {}
    now = now_ms()
    for fact in facts:
        history = by_subject.get((fact["source_key"], int(fact["student_id"])), [])
        latest = next((r for r in history if r["latest_rank"] == 1), None)
        approved = next((r for r in history if r["status"] == "approved"), None)
        correction = None
        status = fact.get("status", "PENDING")
        if approved:
            decision = _load(approved["decision_payload_json"])
            payload = _load(approved["payload_json"])
            same_scope = all(payload.get(k) == fact.get(k) for k in ("class_offering_id", "session_id", "student_id"))
            if same_scope and not payload.get("history_only") and not payload.get("deleted_archive") and decision.get("status") in STATUSES:
                stale = decision.get("source_fingerprint") != fact["source_fingerprint"]
                correction = {"request_id": approved["id"], "status": decision["status"], "stale": stale,
                    "applied": not stale, "local_only": True, "remote_write_status": "not_written",
                    "note": approved["decision_note"], "decided_at": approved["decided_at"]}
                status = "PENDING" if stale else decision["status"]
        deadline = appeal_deadline(fact)
        eligible = (bool(deadline) and int(fact.get("ended_at_ms") or 0) <= now <= deadline
                    and (fact.get("status") in STATUSES or
                         fact.get("status") == "PENDING" and fact.get("state") == "synced"))
        if viewer is not None:
            eligible = eligible and viewer.get("role") == "student" and int(viewer.get("id") or 0) == int(fact["student_id"])
        if latest and latest["status"] == "pending":
            eligible = False
        result[fact["source_key"]] = {"appeal": _public_appeal(latest) if latest else None,
            "can_appeal": bool(eligible), "appeal_deadline": deadline, "correction": correction,
            "effective_status": status, "needs_review": bool(correction and correction["stale"])}
    return result


def record_detail(conn, offering_id, source_key, user):
    fact = current_fact(conn, source_key, user, expected_offering=offering_id)
    overlay = load_smart_appeal_states(conn, [fact], viewer=user)[source_key]
    return {"record": fact, "source_fingerprint": fact["source_fingerprint"], "current_source_available": True,
            "source_changed": False, **overlay}


def request_detail(conn, request, user):
    payload = _load(request["payload_json"])
    snapshot = dict(payload.get("source_snapshot") or {})
    try:
        fact = current_fact(conn, request["subject_id"], user, expected_offering=request.get("class_offering_id"))
    except ApprovalWorkflowError as exc:
        if exc.status_code not in {404, 409}:
            raise
        fact = None
    overlay = load_smart_appeal_states(conn, [fact], viewer=user).get(request["subject_id"], {}) if fact else {}
    return {"record": fact or snapshot, "source_snapshot": snapshot,
            "current_source_available": bool(fact), "source_changed": not fact or fact["source_fingerprint"] != payload.get("source_fingerprint"),
            "source_fingerprint": fact["source_fingerprint"] if fact else None, **overlay}
