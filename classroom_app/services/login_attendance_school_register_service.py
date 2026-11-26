"""Apply verified school roll calls to an editable local register in the caller's transaction."""
from __future__ import annotations

from fastapi import HTTPException

from . import login_attendance_service as local
from . import smart_classroom_miniapp_adapter as mini
from .smart_classroom_write_service import PROTECTED_STATUSES
from .login_attendance_calendar_service import create_register, semester_sessions
from .login_attendance_settlement_service import has_unpublished_settlement


def school_decision_ms(snapshot, number):
    individual = snapshot.get("student_times", {}).get(number)
    return individual or mini.school_timestamp_ms((snapshot.get("record") or {}).get("updateTime"))


def _same_observation(baseline, snapshot, number):
    return (baseline.get("checkin_id") == str(snapshot["record"]["id"])
            and baseline.get("status") == snapshot["students"].get(number))


def decision_direction(row, ctx, snapshot):
    """Compare decision clocks. A new fetch or another pupil's edit is not a pupil decision."""
    number = row["student_number"]
    local_time = row.get("decided_at_ms") if row.get("manual_confirmed") or row.get("school_status") else ctx.get("ended_at_ms")
    baseline = row.get("school_baseline") or {}
    remote_time = school_decision_ms(snapshot, number)
    if _same_observation(baseline, snapshot, number) and not snapshot.get("student_times", {}).get(number):
        # The last mutually agreed individual status is unchanged. A later
        # teacher correction follows that agreement even on schools without
        # an individual edit clock.
        if row.get("manual_confirmed") and local.rules.SCHOOL_STATUS[baseline["status"]] != row["status"]:
            return "local_newer"
        remote_time = baseline.get("at_ms")
    if remote_time and local_time:
        if local_time == remote_time:
            return "equal"
        if remote_time % 1000 == 0 and remote_time < local_time < remote_time + 1000:
            # Some source clocks contain only whole seconds. The update order
            # within that second cannot be inferred from a local millisecond.
            return "unknown"
        return "local_newer" if local_time > remote_time else "school_newer"
    # Starting the school roll establishes a lower bound, never the last
    # edit. A local decision strictly before the start can safely be updated.
    school = snapshot["record"]
    began = mini.school_timestamp_ms(school.get("createTime")) or mini.school_timestamp_ms(school.get("checkinTime"))
    if began and local_time and local_time < began:
        return "school_newer"
    return "unknown"


def _matched(conn, teacher_id, offering_id, session_id, checkin_id):
    rows = conn.execute("""SELECT s.id,s.name,s.student_id_number,cl.name AS class_name
        FROM smart_classroom_checkin_sessions c JOIN smart_classroom_checkin_students st
          ON st.checkin_session_id=c.id AND st.teacher_id=c.teacher_id
          AND st.session_id=c.session_id AND st.class_offering_id=c.class_offering_id
        JOIN students s ON st.student_id=s.id AND st.student_number=TRIM(s.student_id_number)
        LEFT JOIN classes cl ON cl.id=s.class_id
        WHERE c.teacher_id=? AND c.class_offering_id=? AND c.session_id=? AND c.remote_checkin_id=?
          AND c.platform_code='gxufl_smart_classroom' AND c.match_status='matched'
          AND st.local_match_status='matched'""", (teacher_id, offering_id, session_id, checkin_id))
    return {r["id"]: dict(r) for r in rows}


def reconcile_snapshot(conn, user, offering_id, session_id, snapshot, student_ids=None, *, observe_only=False):
    """Caller verifies and persists the exact school snapshot in this transaction."""
    offering = local.require_teacher(conn, offering_id, user, lock=True)
    _, lessons = semester_sessions(conn, offering, session_id, lock=True)
    lesson = lessons[0]
    school = snapshot.get("record") or {}
    if not school.get("id") or lesson.get("schedule_status") in {"cancelled", "canceled", "suspended"}:
        raise HTTPException(409, "需要有效课次及已核对的学校点名记录。")
    current = conn.execute("SELECT id FROM login_attendance_rounds WHERE class_offering_id=? AND session_id=? "
                           "AND is_current=1 AND state<>'void'", (offering_id, session_id)).fetchone()
    rid = current["id"] if current else None
    result = {"round_id": rid, "created": False, "imported_ids": []}
    if observe_only and not current:
        return result
    if local.load_policy(conn, offering_id).get("closed_at_ms"):
        return result
    if current:
        current = local.load_round(conn, rid, lock=True)
        if current["state"] != "published" or has_unpublished_settlement(conn, rid):
            return result
    checkin_id = str(school["id"])
    matched = _matched(conn, user["id"], offering_id, session_id, checkin_id)
    scope = set(matched) if student_ids is None else set(student_ids).intersection(matched)
    if not scope:
        return result
    if rid is None:
        roster = local._roster(conn, offering_id)
        current_ids = {p["id"] for p in roster}
        roster.extend({**person, "class_name": person["class_name"] or "", "confirmed_retake": False}
                      for sid, person in matched.items() if sid not in current_ids)
        rid = create_register(conn, offering, lesson, user, roster, time_source="school_register")
        result.update(round_id=rid, created=True)
    ctx = {"ended_at_ms": current["ended_at_ms"] if current else None}
    changed, observations = [], []
    for row in local._records(conn, rid):
        sid, number = row["student_id"], row["student_number"]
        if sid not in scope or (not row["expected"] and not row["manual_confirmed"] and not local.school_status(row)):
            continue
        if number != str(matched[sid]["student_id_number"]).strip():
            continue
        actual = snapshot["students"].get(number)
        status = local.rules.SCHOOL_STATUS.get(actual)
        if status is None or (actual == "UNCHECKED" and school.get("isStop") is not True):
            continue
        local_status = local.confirmed_record_status(row, "published")
        baseline = local._load(row["school_decision_json"])
        source_ms = school_decision_ms(snapshot, number)
        decision = {"checkin_id": checkin_id, "status": actual, "at_ms": source_ms}
        # The school cannot distinguish a local late arrival from an early
        # departure. Keep the more detailed, already equivalent local fact.
        if status == local_status or (status == "LATE_OR_EARLY" and local_status in {"LATE", "EARLY"}):
            if not _same_observation(baseline, snapshot, number):
                decision["applied"] = bool(local.school_status(row) and not row["manual_confirmed"])
                observations.append((local._json(decision), row["id"]))
            continue
        if observe_only:
            continue
        row.update(school_status=local.school_status(row), school_baseline=baseline)
        protected_backfill = local.backfill_inferred(row) and (actual in PROTECTED_STATUSES or actual == "NOT_APPLICABLE")
        if local_status != "PENDING" and not protected_backfill and decision_direction(row, ctx, snapshot) != "school_newer":
            continue
        decision["applied"] = True
        changed.append((row, decision, status, local_status))
    if observations:
        conn.executemany("UPDATE login_attendance_records SET school_decision_json=? WHERE id=?", observations)
    if changed:
        from .smart_classroom_student_push_service import revoke_for_local_changes
        ids = [row["student_id"] for row, *_ in changed]
        if not result["created"]:
            revoke_for_local_changes(conn, rid, ids)
        now = local.now_ms()
        updates, audit = [], []
        for row, decision, status, previous in changed:
            # The narrow legacy status constraint remains valid. The school
            # source preserves additional official statuses without guessing
            # whether "late or early" meant an arrival or a departure.
            stored = status if status in local.rules.STATUS_LABELS else "PENDING"
            updates.append((stored, local._json(decision), decision["at_ms"], now, row["id"]))
            before = {"status": previous, "manual_confirmed": bool(row["manual_confirmed"]),
                      "decided_at_ms": row["decided_at_ms"], "revision": row["revision"]}
            after = {**decision, "local_status": status, "revision": row["revision"]+1}
            audit.append((rid, row["id"], user["id"], local._json(before), local._json(after), now))
        conn.executemany("""UPDATE login_attendance_records SET status=?,school_decision_json=?,manual_confirmed=0,
            reason='已按智慧课堂点名同步',decided_by=NULL,decided_at_ms=?,updated_at_ms=?,revision=revision+1 WHERE id=?""", updates)
        conn.executemany("""INSERT INTO login_attendance_audit(round_id,record_id,actor_id,action,before_json,after_json,created_at_ms)
            VALUES(?,?,?,'school_decision_imported',?,?,?)""", audit)
        conn.execute("UPDATE login_attendance_rounds SET revision=revision+1 WHERE id=?", (rid,))
        present = {row["student_id"] for row, _, status, _ in changed if status == "PRESENT"}
        if present and not result["created"]:
            local._cancel_appeals(conn, rid, present, user, "智慧课堂已确认出勤，请查看同步后的记录。")
        result["imported_ids"] = ids
    return result
