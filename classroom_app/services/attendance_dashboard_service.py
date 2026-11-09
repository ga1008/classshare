"""Read-only semester attendance, with one effective fact per student and lesson."""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib

from fastapi import HTTPException

from . import login_attendance_rules as rules, login_attendance_service as local
from .attendance_fact_service import VERIFIED_QUALITIES
from .classroom_activity_evidence_service import _roster
from .resource_access_service import ensure_classroom_access


SMART_STATUS = {"CHECKED": "PRESENT", "UNCHECKED": "ABSENT", "SICK_LEAVE": "SICK_LEAVE",
                "PERSONAL_LEAVE": "PERSONAL_LEAVE", "LATE_OR_EARLY": "LATE_OR_EARLY",
                "NOT_APPLICABLE": "EXEMPT"}
STATUS_LABELS = {**rules.STATUS_LABELS, "LATE_OR_EARLY": "迟到或早退", "EXEMPT": "免考勤"}
FINAL_STATUSES = frozenset(STATUS_LABELS) - {"PENDING", "EXEMPT"}
RATE_NOTE = "出勤率为出勤人次除以已确认应考勤人次。待确认、免考勤和未开启课次不计入分母。"


def _scope(conn, offering_id, user, student_id=None):
    if user.get("role") == "teacher":
        return local.require_teacher(conn, offering_id, user), student_id
    if user.get("role") != "student" or (student_id is not None and int(student_id) != int(user["id"])):
        raise HTTPException(403, "只能读取本人或任课课堂的签到统计。")
    student_id = int(user["id"])
    try:
        return dict(ensure_classroom_access(conn, offering_id, user)), student_id
    except HTTPException as exc:
        if exc.status_code != 403:
            raise
        # Confirmed retakes are included in the existing local attendance roster.
        row = conn.execute("""SELECT o.* FROM class_offerings o JOIN classroom_retake_students r
            ON r.class_offering_id=o.id JOIN students s ON s.id=r.student_id
            WHERE o.id=? AND r.student_id=? AND r.status='confirmed'
              AND COALESCE(s.enrollment_status,'active')='active'""", (offering_id, student_id)).fetchone()
        if row is None:
            raise
        return dict(row), student_id


def _date_ms(value):
    if not value:
        return None
    try:
        return rules.to_ms(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _scheduled_end(lesson):
    try:
        return rules.resolve_session_window(lesson)["ends_at_ms"]
    except (ValueError, TypeError):
        return None


def _fingerprint(*parts):
    return hashlib.sha256(local._json(parts).encode()).hexdigest()


def _local_rows(conn, offering_id, student_id, session_id):
    sql = """SELECT s.id AS source_id,s.student_id,s.name,s.student_number,s.class_name,s.status,s.expected,
        s.manual_confirmed,s.quality,s.revision,r.id AS round_id,r.session_id,r.state,r.policy_json,
        r.ended_at_ms,r.ends_at_ms FROM login_attendance_rounds r
        JOIN class_offering_sessions lesson ON lesson.id=r.session_id AND lesson.class_offering_id=r.class_offering_id
        JOIN login_attendance_records s ON s.round_id=r.id
        WHERE r.class_offering_id=? AND r.is_current=1 AND r.state<>'void'"""
    params = [offering_id]
    if student_id is not None:
        sql += " AND s.student_id=?"
        params.append(student_id)
    if session_id is not None:
        sql += " AND r.session_id=?"
        params.append(session_id)
    return conn.execute(sql, params).fetchall()


def _smart_rows(conn, offering_id, teacher_id, student_id, session_id):
    # Select the latest whole remote round before filtering to one student.
    # Missing students in that round do not inherit older same-lesson results.
    filters = " AND c.session_id=?" if session_id is not None else ""
    params = [offering_id, teacher_id, *([session_id] if session_id is not None else [])]
    query = f"""WITH selected AS (
        SELECT c.id,c.session_id,c.remote_checkin_id,c.stop_time,c.synced_at,
            lesson.session_date,lesson.academic_section_text,lesson.academic_time_text,
            ROW_NUMBER() OVER(PARTITION BY c.session_id ORDER BY COALESCE(c.checkin_time,'') DESC,
                               COALESCE(c.synced_at,'') DESC,c.id DESC) AS rank
        FROM smart_classroom_checkin_sessions c JOIN class_offering_sessions lesson
          ON lesson.id=c.session_id AND lesson.class_offering_id=c.class_offering_id
        WHERE c.class_offering_id=? AND c.teacher_id=? AND c.platform_code='gxufl_smart_classroom'
          AND c.match_status='matched' AND COALESCE(c.remote_checkin_id,'')<>''
          AND COALESCE(c.synced_at,'')<>'' AND c.total_count>0{filters}
    ) SELECT s.id AS source_id,c.id AS source_run_id,c.session_id,c.remote_checkin_id,c.stop_time,c.synced_at,
        c.session_date,c.academic_section_text,c.academic_time_text,
        s.student_id,s.student_number,s.student_name AS name,s.status AS source_status
      FROM selected c JOIN smart_classroom_checkin_students s ON s.checkin_session_id=c.id
      WHERE c.rank=1 AND s.teacher_id=? AND s.class_offering_id=? AND s.session_id=c.session_id
        AND s.student_id IS NOT NULL AND s.local_match_status='matched' AND s.synced_at=c.synced_at"""
    params.extend((teacher_id, offering_id))
    if student_id is not None:
        query += " AND s.student_id=?"
        params.append(student_id)
    return conn.execute(query, params).fetchall()


def _pdf_rows(conn, offering_id, teacher_id, student_id, session_id):
    filters = " AND c.local_session_id=?" if session_id is not None else ""
    params = [offering_id, teacher_id, *([session_id] if session_id is not None else [])]
    query = f"""WITH selected AS (
        SELECT c.id,c.local_session_id,c.parse_run_id,c.remote_checkin_id,
            RANK() OVER(PARTITION BY c.local_session_id ORDER BY l.is_grade_source DESC,
                        COALESCE(c.source_datetime,'') DESC) AS rank,
            COUNT(*) OVER(PARTITION BY c.local_session_id,l.is_grade_source,
                          COALESCE(c.source_datetime,'')) AS tied
        FROM smart_attendance_source_offerings l
        JOIN smart_attendance_source_bindings b ON b.id=l.binding_id
        JOIN attendance_reports r ON r.binding_id=b.id AND r.deleted_at IS NULL
        JOIN attendance_report_versions v ON v.report_id=r.id
        JOIN attendance_parse_runs p ON p.id=r.confirmed_parse_run_id AND p.source_version_id=v.id
        JOIN attendance_report_sessions c ON c.parse_run_id=p.id
        JOIN class_offering_sessions lesson ON lesson.id=c.local_session_id AND lesson.class_offering_id=l.class_offering_id
        WHERE l.class_offering_id=? AND b.owner_teacher_id=? AND l.link_state='active'
          AND p.state='confirmed' AND p.mapped_offering_id=l.class_offering_id
          AND p.binding_link_revision=l.revision AND c.mapping_state='matched'{filters}
    ) SELECT cell.id AS source_id,c.parse_run_id AS source_run_id,c.local_session_id AS session_id,
        c.remote_checkin_id,cell.normalized_status AS source_status,cell.quality_state,cell.revision,
        s.local_student_id AS student_id,s.student_number,s.source_name AS name,s.source_class_name AS class_name
      FROM selected c JOIN attendance_report_cells cell ON cell.session_column_id=c.id AND cell.parse_run_id=c.parse_run_id
      JOIN attendance_report_students s ON s.id=cell.student_row_id AND s.parse_run_id=c.parse_run_id
      WHERE c.rank=1 AND c.tied=1 AND s.identity_state='matched' AND s.local_student_id IS NOT NULL"""
    if student_id is not None:
        query += " AND s.local_student_id=?"
        params.append(student_id)
    return conn.execute(query, params).fetchall()


def _source_rows(conn, offering, student_id, session_id=None):
    offering_id, teacher_id = offering["id"], offering["teacher_id"]
    candidates = defaultdict(dict)
    for raw in _local_rows(conn, offering_id, student_id, session_id):
        row = dict(raw)
        status = row["status"]
        if not row["expected"] and not row["manual_confirmed"]:
            status = "EXEMPT"
        elif not row["manual_confirmed"] and (row["state"] != "published" or status != "PRESENT"):
            status = "PENDING"
        candidates[(row["session_id"], row["student_id"])]["local"] = {**row, "source": "local", "status": status}
    for source, rows in (("confirmed_pdf", _pdf_rows(conn, offering_id, teacher_id, student_id, session_id)),
                         ("smart_synced", _smart_rows(conn, offering_id, teacher_id, student_id, session_id))):
        for raw in rows:
            row = dict(raw)
            status = SMART_STATUS.get(row["source_status"], "PENDING")
            if source == "confirmed_pdf" and row["quality_state"] not in VERIFIED_QUALITIES:
                continue
            state = "synced"
            if source == "smart_synced":
                ended = _date_ms(row["stop_time"]) or _scheduled_end(row)
                row["ended_at_ms"] = ended
                synced = _date_ms(row["synced_at"])
                now = local.now_ms()
                if not ended or not synced or ended > min(synced, now):
                    # A snapshot taken during remote roll call is provisional
                    # until a fresh sync actually observes the ended round.
                    status = "PENDING"
                    state = "running" if ended and ended > now else "settling"
            options = candidates[(row["session_id"], row["student_id"])]
            # Multiple source rows mapped to the same person are ambiguous.
            options[source] = None if source in options else {**row, "source": source, "status": status, "state": state}
    return candidates


def _fact_rows(conn, offering, student_id, session_id=None):
    offering_id, teacher_id = offering["id"], offering["teacher_id"]
    lessons = conn.execute("SELECT id,title,session_date,week_index,academic_section_text,academic_time_text "
        "FROM class_offering_sessions WHERE class_offering_id=?" + (" AND id=?" if session_id is not None else "") +
        " ORDER BY session_date,id", (offering_id, session_id) if session_id is not None else (offering_id,)).fetchall()
    lessons = {row["id"]: dict(row) for row in lessons}
    candidates = _source_rows(conn, offering, student_id, session_id)
    remote_appeal_days = None
    scheduled_ends = {}
    for (lesson_id, person_id), options in candidates.items():
        # Concurrent lesson deletion can make an otherwise scoped query find a
        # row which was missing from the earlier lesson metadata snapshot.
        if lesson_id not in lessons:
            options.clear()
            continue
        for source, fact in options.items():
            if fact is None:
                continue
            if source == "local":
                key = f"local:{fact['source_id']}"
                fingerprint = _fingerprint(key, fact["revision"], fact["status"])
                ended = fact["ended_at_ms"] or fact["ends_at_ms"]
                appeal_days = local._load(fact["policy_json"]).get("appeal_days", 7)
            else:
                prefix = "report" if source == "confirmed_pdf" else "api"
                key = f"{prefix}:{fact['source_id']}"
                fingerprint = _fingerprint(key, offering_id, lesson_id, person_id, fact["source_run_id"],
                                           fact["remote_checkin_id"], fact["source_status"], fact["status"], fact.get("revision"))
                ended = fact.get("ended_at_ms")
                if source == "confirmed_pdf":
                    if lesson_id not in scheduled_ends:
                        scheduled_ends[lesson_id] = _scheduled_end(lessons[lesson_id])
                    ended = scheduled_ends[lesson_id]
                if remote_appeal_days is None:
                    remote_appeal_days = local.load_policy(conn, offering_id)["appeal_days"]
                appeal_days = remote_appeal_days
            fact.update(source_key=key, source_fingerprint=fingerprint, class_offering_id=offering_id,
                        teacher_id=teacher_id, ended_at_ms=ended, appeal_days=appeal_days)
    return lessons, candidates


def _remote_appeals(conn, candidates, user):
    facts = [fact for options in candidates.values() for name, fact in options.items() if fact and name != "local"]
    if not facts:
        return {}
    from .smart_attendance_appeal_service import load_smart_appeal_states
    return load_smart_appeal_states(conn, facts, viewer=user)


def _effective(candidates, remote_appeals=None):
    attendance = candidates.get("local")
    if attendance and (attendance["manual_confirmed"] or attendance["status"] == "EXEMPT"):
        return attendance
    approved = []
    for source in ("confirmed_pdf", "smart_synced"):
        fact = candidates.get(source)
        correction = (remote_appeals or {}).get(fact["source_key"], {}).get("correction") if fact else None
        if correction:
            approved.append((correction.get("decided_at") or "", correction["request_id"], fact))
    if approved:
        # Preserve a teacher's correction over automatic attendance. If the
        # underlying import changed, the stale correction stays pending review.
        return max(approved, key=lambda item: item[:2])[2]
    if attendance and attendance["status"] != "PENDING":
        return attendance
    confirmed_pdf, smart = candidates.get("confirmed_pdf"), candidates.get("smart_synced")
    if smart and smart["status"] != "PENDING":
        return confirmed_pdf or smart
    return confirmed_pdf or attendance or smart


def load_student_session_fact(conn, offering_id, session_id, user, *, student_id=None):
    """Select the same source as the dashboard and return its original fact."""
    offering, student_id = _scope(conn, offering_id, user, student_id)
    if student_id is None:
        raise HTTPException(422, "请选择本次签到的学生。")
    lessons, candidates = _fact_rows(conn, offering, student_id, session_id)
    if session_id not in lessons:
        raise HTTPException(404, "课次不存在。")
    fact = _effective(candidates.get((session_id, student_id), {}), _remote_appeals(conn, candidates, user))
    if not fact:
        return None
    course = conn.execute("SELECT name FROM courses WHERE id=?", (offering["course_id"],)).fetchone()
    fields = ("source", "source_key", "source_fingerprint", "source_id", "source_run_id", "class_offering_id", "teacher_id",
              "session_id", "student_id", "status", "source_status", "name", "student_number", "ended_at_ms", "appeal_days")
    return {**{key: fact[key] for key in fields if key in fact}, "status_label": STATUS_LABELS[fact["status"]],
            "course_name": course["name"] if course else ""}


def visibility(conn, offering_id, user):
    offering, student_id = _scope(conn, offering_id, user)
    if student_id is None:
        return {"visible": True, "session_ids": [], "local_session_ids": []}
    # Three narrow, own-student queries, without summaries or approval reads.
    candidates = _source_rows(conn, offering, student_id)
    visible_sessions = sorted(lesson_id for (lesson_id, _), options in candidates.items() if any(options.values()))
    local_sessions = sorted(lesson_id for (lesson_id, _), options in candidates.items()
                            if options.get("local") and not (options.get("confirmed_pdf") or options.get("smart_synced")))
    return {"visible": bool(visible_sessions), "session_ids": visible_sessions, "local_session_ids": local_sessions}


def _summary(statuses, *, session_count, recorded_count, active_count):
    counts = Counter(statuses)
    confirmed = sum(counts[status] for status in FINAL_STATUSES)
    return {"counts": {status: counts[status] for status in STATUS_LABELS},
            "expected": confirmed + counts["PENDING"], "confirmed": confirmed,
            "pending": counts["PENDING"], "exempt": counts["EXEMPT"],
            "attendance_rate": round(100 * counts["PRESENT"] / confirmed, 2) if confirmed else None,
            "session_count": session_count, "recorded_session_count": recorded_count, "active_session_count": active_count}


def _local_appeals(conn, facts, user):
    if not facts:
        return {}
    identifiers = {str(fact["source_id"]) for fact in facts}
    params = [facts[0]["class_offering_id"]]
    query = """SELECT a.id,a.subject_id,a.status FROM approval_requests a JOIN login_attendance_records s
        ON a.subject_id=CAST(s.id AS TEXT) AND a.applicant_user_pk=s.student_id
        JOIN login_attendance_rounds r ON r.id=s.round_id
        WHERE r.class_offering_id=? AND r.is_current=1 AND r.state<>'void'
          AND a.request_type='login_attendance_appeal' AND a.applicant_role='student'"""
    if user["role"] == "student":
        query += " AND s.student_id=?"
        params.append(user["id"])
    rows = conn.execute(query + " ORDER BY CASE WHEN a.status='pending' THEN 0 ELSE 1 END,a.id DESC", params).fetchall()
    result = {}
    for row in rows:
        if row["subject_id"] in identifiers:
            result.setdefault(row["subject_id"], {"id": row["id"], "status": row["status"]})
    return result


def _personal_row(fact, options, lesson, *, appeal, can_appeal, appeal_deadline, correction=None):
    current = options.get("local")
    return {"session_id": lesson["id"], "title": lesson["title"], "date": lesson["session_date"],
            "week_index": lesson["week_index"], "source": fact["source"], "state": fact["state"],
            "status": fact["status"], "status_label": STATUS_LABELS[fact["status"]],
            "source_key": fact["source_key"], "source_fingerprint": fact["source_fingerprint"],
            "round_id": current["round_id"] if current else None, "local_round_id": current["round_id"] if current else None,
            "local_state": current["state"] if current else None,
            "other_sources": [name for name, value in options.items() if value and name != fact["source"]],
            "conflict_count": sum(other["status"] not in ("PENDING", fact["status"]) for other in options.values()
                                  if other and other["source"] != fact["source"] and fact["status"] != "PENDING"),
            "appeal": appeal, "can_appeal": can_appeal, "appeal_deadline": appeal_deadline, "correction": correction}


def dashboard(conn, offering_id, user, *, student_id=None, session_id=None):
    offering, selected_student = _scope(conn, offering_id, user, student_id)
    teacher_view = user["role"] == "teacher"
    lessons, candidates = _fact_rows(conn, offering, selected_student, session_id)
    if session_id is not None and session_id not in lessons:
        raise HTTPException(404, "课次不存在。")
    remote_appeals = _remote_appeals(conn, candidates, user)
    effective = {identity: fact for identity, options in candidates.items() if (fact := _effective(options, remote_appeals))}
    if not teacher_view and not effective:
        return {"visible": False}
    local_appeals = _local_appeals(conn, [fact for fact in effective.values() if fact["source"] == "local"], user)
    by_session, by_student = defaultdict(list), defaultdict(list)
    roster = {row["id"]: {"student_id": row["id"], "name": row["name"],
                          "student_number": row["student_id_number"], "class_name": row["class_name"]}
              for row in _roster(conn, offering_id)} if teacher_view else {}
    now = local.now_ms()
    for (session_id, person_id), fact in effective.items():
        appeal, can_appeal, deadline, correction = None, False, None, None
        if fact["source"] == "local":
            appeal = local_appeals.get(str(fact["source_id"]))
            deadline = fact["ended_at_ms"] + fact["appeal_days"] * 86400_000
            can_appeal = not teacher_view and fact["state"] in {"settling", "published"} and (
                now <= deadline) and not (appeal and appeal["status"] == "pending")
        else:
            remote = remote_appeals.get(fact["source_key"], {})
            appeal, can_appeal, correction = remote.get("appeal"), bool(remote.get("can_appeal")), remote.get("correction")
            deadline = remote.get("appeal_deadline")
            if remote.get("effective_status") in STATUS_LABELS:
                fact = {**fact, "status": remote["effective_status"]}
        entry = _personal_row(fact, candidates[(session_id, person_id)], lessons[session_id],
                              appeal=appeal, can_appeal=can_appeal, appeal_deadline=deadline, correction=correction)
        by_session[session_id].append(entry)
        by_student[person_id].append(entry)
        if teacher_view:
            roster.setdefault(person_id, {"student_id": person_id, "name": fact["name"],
                                          "student_number": fact["student_number"], "class_name": fact.get("class_name", "")})

    def summarize(entries):
        result = _summary((entry["status"] for entry in entries), session_count=len(lessons),
            recorded_count=len({entry["session_id"] for entry in entries}),
            active_count=len({entry["session_id"] for entry in entries if "running" in (entry["state"], entry["local_state"])}))
        result["pending_appeals"] = sum((entry["appeal"] or {}).get("status") == "pending" for entry in entries)
        return result

    if selected_student is not None and teacher_view and selected_student not in roster:
        raise HTTPException(404, "该学生不在此课程的名单或签到记录中。")
    course = conn.execute("SELECT name FROM courses WHERE id=?", (offering["course_id"],)).fetchone()
    semester = conn.execute("SELECT name FROM academic_semesters WHERE id=?", (offering["semester_id"],)).fetchone() if offering.get("semester_id") else None
    result = {"visible": True, "offering": {"id": offering_id, "course_name": course["name"] if course else "",
              "semester": semester["name"] if semester else offering.get("semester", "")}, "rate_note": RATE_NOTE,
              "summary": summarize([entry for entries in by_session.values() for entry in entries])}
    if selected_student is not None:
        result["sessions"] = [by_session[lesson_id][0] for lesson_id in lessons if lesson_id in by_session]
        if teacher_view:
            result["student"] = roster[selected_student]
        return result

    sessions = []
    for lesson_id, lesson in lessons.items():
        entries = by_session.get(lesson_id, [])
        sources = sorted({entry["source"] for entry in entries})
        current = next((entry for entry in entries if entry["local_round_id"]), None)
        state = current["local_state"] if current else next((label for label in ("running", "settling", "synced")
                                                           if any(entry["state"] == label for entry in entries)), "not_started")
        sessions.append({"session_id": lesson_id, "title": lesson["title"], "date": lesson["session_date"],
            "week_index": lesson["week_index"], "state": state,
            "local_state": current["local_state"] if current else None, "round_id": current["local_round_id"] if current else None,
            "local_round_id": current["local_round_id"] if current else None, "source": "mixed" if len(sources) > 1 else next(iter(sources), None),
            "other_sources": sorted({name for entry in entries for name in entry["other_sources"]}),
            "conflict_count": sum(bool(entry["conflict_count"]) for entry in entries),
            "pending_appeals": sum((entry["appeal"] or {}).get("status") == "pending" for entry in entries),
            **_summary((entry["status"] for entry in entries), session_count=1, recorded_count=int(bool(entries)),
                       active_count=int(state == "running"))})
    result["sessions"] = sessions
    result["students"] = [{**person, "summary": summarize(by_student.get(person_id, []))}
                          for person_id, person in sorted(roster.items(), key=lambda item: (item[1]["class_name"], item[1]["student_number"], item[0]))]
    return result
