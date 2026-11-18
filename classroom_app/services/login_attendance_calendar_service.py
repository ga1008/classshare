"""Teacher's local attendance register, scoped to one offering and its semester."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
import hashlib
from uuid import uuid4

from fastapi import HTTPException

from ..db.connection import execute_insert_returning_id, get_configured_db_engine
from . import attendance_dashboard_service as dashboard
from . import login_attendance_service as local
from .academic_session_identity_service import _sections
from .classroom_activity_evidence_service import _roster
from .schedule_editor_service import build_term_calendar
from .semester_identity_service import infer_identity_from_dates, parse_semester_identity


def _iso_day(value):
    try:
        return date.fromisoformat(str(value or "")[:10]).isoformat()
    except ValueError:
        return ""


def semester_sessions(conn, offering, session_id=None, *, lock=False):
    semester = None
    if offering.get("semester_id"):
        semester = conn.execute("SELECT id,name,start_date,end_date FROM academic_semesters WHERE id=?",
                                (offering["semester_id"],)).fetchone()
    semester = dict(semester) if semester else {"name": offering.get("semester") or ""}
    start, end = (_iso_day(semester.get(key)) for key in ("start_date", "end_date"))
    if not start or not end or start > end:
        semester["start_date"] = semester["end_date"] = ""
    legacy_term = None
    if not semester.get("start_date"):
        legacy_term = parse_semester_identity(semester["name"]) or infer_identity_from_dates(
            local.rules.display_time(local.now_ms())[:10])
        semester["scope_note"] = f"未绑定完整校历，按课次日期筛选{legacy_term.canonical_name}。"
    sql = """SELECT id,title,session_date,weekday,week_index,academic_section_text,academic_time_text,
                    academic_course_code,schedule_status,schedule_note FROM class_offering_sessions
             WHERE class_offering_id=?"""
    args = [offering["id"]]
    for bound, operator in (("start_date", ">="), ("end_date", "<=")):
        if semester.get(bound):
            sql += f" AND session_date{operator}?"
            args.append(str(semester[bound])[:10])
    if session_id is not None:
        sql += " AND id=?"
        args.append(session_id)
    rows = []
    sql += " ORDER BY session_date,order_index,id"
    if lock and get_configured_db_engine() == "postgres":
        sql += " FOR SHARE"
    for item in conn.execute(sql, tuple(args)):
        lesson = dict(item)
        iso = _iso_day(lesson["session_date"])
        if not iso or (legacy_term and infer_identity_from_dates(iso) != legacy_term):
            continue
        rows.append({**lesson, "session_date": iso})
    if session_id is not None and not rows:
        raise HTTPException(404, "该课次不在本课程本学期内。")
    return semester, rows


def _calendar(conn, semester, lessons, today):
    start = str(semester.get("start_date") or (lessons[0]["session_date"] if lessons else today))[:10]
    end = str(semester.get("end_date") or (lessons[-1]["session_date"] if lessons else today))[:10]
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    monday = first - timedelta(days=first.weekday())
    term = build_term_calendar(conn, {"semester_id": semester.get("id"), "week1_monday": monday.isoformat(),
                                      "max_week": (last - monday).days // 7 + 1})
    return {"start": start, "end": end, "today": today, "scope_note": semester.get("scope_note") or "",
            "days": [day for day in term["days"] if start <= day["date"] <= end]}


def _current_roster(conn, offering_id):
    return {person["id"]: {"student_id": person["id"], "student_number": person["student_id_number"],
                           "name": person["name"], "class_name": person["class_name"],
                           "expected": not person["confirmed_retake"]} for person in _roster(conn, offering_id)}


def _edit_note(lesson, round_id, now):
    if lesson["schedule_status"] in {"cancelled", "canceled", "suspended"}:
        return "此课次已停课。"
    if not round_id:
        if str(lesson["session_date"])[:10] > local.rules.display_time(now)[:10]:
            return "尚未到上课日期。"
        try:
            if now < local.rules.resolve_session_window(lesson)["starts_at_ms"]:
                return "尚未到本节课的开始时间。"
        except (TypeError, ValueError):
            pass  # Manually dated lessons can lack period or clock information.
    return ""


def _slot_revision(lesson):
    # Manual plan edits and official timetable reconciliation can retain a
    # session id while changing its date, periods, cancellation or identity.
    values = [lesson[key] for key in ("id", "session_date", "academic_section_text", "academic_time_text",
                                      "schedule_status", "week_index", "weekday", "title", "academic_course_code")]
    return hashlib.sha256(local._json(values).encode()).hexdigest()


def _schedule_label(lesson, meta):
    if lesson["schedule_status"] in {"cancelled", "canceled", "suspended"}:
        return "已停课"
    original = local._load(meta["original_json"])
    current = local._load(meta["current_json"])
    if not isinstance(original, dict) or not isinstance(current, dict):
        return ""
    day = _iso_day(original.get("date"))
    sections = current.get("sections") or []
    if (day and current.get("date") == lesson["session_date"] and sections == list(_sections(lesson["academic_section_text"]))
            and (day != current["date"] or original.get("sections") != sections)):
        before = "、".join(str(value) for value in original.get("sections") or [])
        return f"调自 {day}" + (f" 第{before}节" if before else "")
    return ""


def _projection(conn, offering, lessons, student_id, *, detail=False):
    roster = _current_roster(conn, offering["id"])
    if not lessons:
        if student_id is not None and student_id not in roster:
            raise HTTPException(404, "该学生不在本课程的名单中。")
        return [], roster, []
    # Bound the input before fetching any student facts. A calendar never loads
    # evidence JSON, audit logs, learning traces, or network observations.
    dates = (lessons[0]["session_date"], lessons[-1]["session_date"])
    args = [offering["id"], *dates]
    scope = "l.class_offering_id=? AND l.session_date BETWEEN ? AND ?"
    if detail:
        scope += " AND l.id=?"
        args.append(lessons[0]["id"])
    meta = {row["id"]: row for row in conn.execute("""SELECT l.id,r.id AS round_id,r.state,b.original_json,b.current_json,
        EXISTS(SELECT 1 FROM smart_classroom_checkin_sessions c WHERE c.session_id=l.id
          AND c.class_offering_id=l.class_offering_id AND c.teacher_id=? AND c.match_status='matched'
          AND c.platform_code='gxufl_smart_classroom' AND COALESCE(c.remote_checkin_id,'')<>''
          AND COALESCE(c.synced_at,'')<>'') AS school_cached
        FROM class_offering_sessions l LEFT JOIN login_attendance_rounds r
          ON r.session_id=l.id AND r.class_offering_id=l.class_offering_id AND r.is_current=1 AND r.state<>'void'
        LEFT JOIN academic_schedule_session_bindings b ON b.session_id=l.id AND b.class_offering_id=l.class_offering_id
          AND b.teacher_id=? AND b.semester_id=?
        WHERE """ + scope, (offering["teacher_id"], offering["teacher_id"], offering.get("semester_id"), *args))}
    _, candidates = dashboard._fact_rows(conn, offering, student_id, lessons[0]["id"] if detail else None,
                                         lessons=lessons, dates=dates)
    appeals = dashboard._remote_appeals(conn, candidates, {"role": "teacher", "id": offering["teacher_id"]})
    people = defaultdict(dict)
    for (sid, person_id), options in candidates.items():
        fact = dashboard._effective(options, appeals)
        if not fact or sid not in meta:
            continue
        person = roster.setdefault(person_id, {"student_id": person_id, "student_number": fact["student_number"],
                    "name": fact["name"], "class_name": fact.get("class_name") or ""})
        local_fact = options.get("local")
        record = dict(person)
        # A student added after the original local round cannot be inserted
        # into that sealed roster. School/PDF facts remain visible to teachers.
        expected = bool(local_fact["expected"]) if local_fact else bool(person.get("expected", fact["status"] != "EXEMPT"))
        record.update(expected=expected, revision=local_fact["revision"] if local_fact else 0,
                      status=local_fact["status"] if local_fact else ("PENDING" if expected else "EXEMPT"),
                      manual_confirmed=bool(local_fact and local_fact["manual_confirmed"]),
                      school_status=local.school_status(local_fact) if local_fact else None,
                      effective_status=appeals.get(fact["source_key"], {}).get("effective_status", fact["status"]),
                      source=fact["source"], editable=bool(local_fact or not meta[sid]["round_id"]))
        people[sid][person_id] = record
    if student_id is not None and student_id not in roster and not detail:
        raise HTTPException(404, "该学生不在本课程本学期的名单或点名记录中。")
    sessions, records = [], []
    now = local.now_ms()
    current_expected = {key for key, p in roster.items() if p.get("expected") and (student_id is None or key == student_id)}
    for lesson in lessons:
        sid = lesson["id"]
        if sid not in meta:  # Concurrent deletion after reading the semester timetable.
            continue
        round_id, state = meta[sid]["round_id"], meta[sid]["state"]
        people_in_lesson = people[sid]
        selected = people_in_lesson.get(student_id)
        note = _edit_note(lesson, round_id, now)
        if student_id is not None and ((selected and not selected["editable"]) or
                (not selected and (round_id or "expected" not in roster.get(student_id, {})))):
            note = "该学生不在本节课的名单中。"
        records = list(people_in_lesson.values())
        sources = {r["source"] for r in records}
        local_counts = Counter(r["status"] for r in records if r["revision"])
        effective_counts = Counter(r["effective_status"] for r in records)
        sessions.append({"session_id": sid, "title": lesson["title"], "date": str(lesson["session_date"])[:10],
                         "slot_revision": _slot_revision(lesson),
                         "week_index": lesson["week_index"], "section_text": lesson["academic_section_text"],
                         "schedule_status": lesson["schedule_status"], "schedule_note": lesson["schedule_note"],
                         "schedule_status_label": _schedule_label(lesson, meta[sid]),
                         "round_id": round_id, "local_state": state, "school_cached": bool(meta[sid]["school_cached"]),
                         "can_edit": not note, "edit_note": note, "status": selected["effective_status"] if selected else None,
                         "local_status": selected["status"] if selected and selected["revision"] else None,
                         "source": "mixed" if len(sources) > 1 else next(iter(sources), None), "recorded": bool(records),
                         "unrecorded_count": 0 if round_id else len(current_expected - people_in_lesson.keys()),
                         "counts": {key: effective_counts[key] for key in dashboard.STATUS_LABELS},
                         "local_counts": {key: local_counts[key] for key in dashboard.STATUS_LABELS}})
    if detail:
        if not sessions:
            raise HTTPException(404, "该课次已移除，请刷新。")
        if not sessions[0]["round_id"]:
            for person in roster.values():
                person_id = person["student_id"]
                if "expected" in person and (student_id is None or person_id == student_id):
                    people_in_lesson.setdefault(person_id, {**person, "status": "PENDING" if person["expected"] else "EXEMPT",
                        "manual_confirmed": False, "revision": 0, "effective_status": None, "source": None, "editable": True})
        records = sorted(people_in_lesson.values(), key=lambda p: (p["student_number"] or "", p["student_id"]))
    return sessions, roster, records


def calendar(conn, offering_id, user, student_id=None):
    offering = local.require_teacher(conn, offering_id, user)
    semester, lessons = semester_sessions(conn, offering)
    sessions, roster, _ = _projection(conn, offering, lessons, student_id)
    course = conn.execute("SELECT name FROM courses WHERE id=?", (offering["course_id"],)).fetchone()
    return {"offering": {"id": offering_id, "course_name": course["name"] if course else "", "semester": semester["name"]},
            "calendar": _calendar(conn, semester, lessons, local.rules.display_time(local.now_ms())[:10]),
            "students": sorted(roster.values(), key=lambda p: (p["class_name"] or "", p["student_number"] or "", p["student_id"])),
            "sessions": sessions}


def session_detail(conn, offering_id, session_id, user, student_id=None):
    offering = local.require_teacher(conn, offering_id, user)
    _, lessons = semester_sessions(conn, offering, session_id)
    sessions, _, records = _projection(conn, offering, lessons, student_id, detail=True)
    return {"session": sessions[0], "records": records}


def _manual_round(conn, offering, lesson, user):
    roster = _roster(conn, offering["id"])
    # An exact, matched school/PDF record can retain a historical participant
    # who has since left the current roster. Freeze that person in this lesson
    # only, without adopting the school's status as a local decision.
    candidates = dashboard._source_rows(conn, offering, None, lesson["id"])
    existing = {p["id"] for p in roster}
    for (_, person_id), options in candidates.items():
        if person_id in existing:
            continue
        fact = options.get("confirmed_pdf") or options.get("smart_synced")
        if fact:
            roster.append({"id": person_id, "name": fact["name"], "student_id_number": fact["student_number"],
                           "class_name": fact.get("class_name") or "", "confirmed_retake": False,
                           "expected": fact["status"] != "EXEMPT"})
    return create_register(conn, offering, lesson, user, roster, time_source="manual_register")


def create_register(conn, offering, lesson, user, roster, *, time_source):
    if not roster:
        raise HTTPException(409, "本课程当前没有可补记的学生。")
    now = local.now_ms()
    # There was no collection window. Use a zero-width window and preserve
    # the actual decision time separately from the historical lesson date.
    try:
        cutoff = min(now, local.rules.resolve_session_window(lesson)["ends_at_ms"])
    except (TypeError, ValueError):
        cutoff = min(now, local.rules.to_ms(str(lesson["session_date"])[:10] + "T23:59:59"))
    context = {**local._context(conn, offering, lesson), "time_source": time_source}
    policy = {**local.load_policy(conn, offering["id"]), "time_source": time_source}
    round_id = execute_insert_returning_id(conn, """INSERT INTO login_attendance_rounds
        (class_offering_id,session_id,teacher_id,request_key,state,starts_at_ms,ends_at_ms,window_start_ms,
         window_end_ms,ended_at_ms,settle_at_ms,created_at_ms,evaluated_at_ms,next_evaluation_ms,policy_json,context_json)
        VALUES(?,?,?,?,'published',?,?,?,?,?,?,?,?,?,?,?)""",
        (offering["id"], lesson["id"], user["id"], time_source + ":" + uuid4().hex, cutoff, cutoff, cutoff, cutoff,
         cutoff, cutoff, now, now, now + 8 * 86400_000, local._json(policy), local._json(context)))
    local.seed_records(conn, round_id, roster, now)
    local._audit(conn, round_id, time_source + "_created", user, after={"students": len(roster), "session_id": lesson["id"]})
    return round_id


def save(conn, offering_id, session_id, user, payload):
    offering = local.require_teacher(conn, offering_id, user, lock=True)
    _, lessons = semester_sessions(conn, offering, session_id, lock=True)
    key = payload.get("idempotency_key")
    fingerprint_input = dict(payload)
    if fingerprint_input.get("expected_slot_revision") is None:
        fingerprint_input.pop("expected_slot_revision", None)
    fingerprint = hashlib.sha256(local._json(fingerprint_input).encode()).hexdigest()
    if key:
        receipt = conn.execute("""SELECT a.after_json FROM login_attendance_audit a JOIN login_attendance_rounds r ON r.id=a.round_id
            WHERE r.class_offering_id=? AND r.session_id=? AND a.actor_id=? AND a.action='calendar_saved' AND a.reason=?
            ORDER BY a.id DESC LIMIT 1""", (offering_id, session_id, user["id"], key)).fetchone()
        if receipt:
            saved = local._load(receipt["after_json"])
            if saved["hash"] != fingerprint:
                raise HTTPException(409, "此操作标识已用于另一份更正。")
            return saved["result"]
    if payload.get("expected_slot_revision") and payload["expected_slot_revision"] != _slot_revision(lessons[0]):
        raise HTTPException(409, "本课日期、节次或排课信息已变化，请核对新课表后再更正。")
    current = conn.execute("""SELECT id FROM login_attendance_rounds WHERE class_offering_id=? AND session_id=?
                              AND is_current=1 AND state<>'void'""", (offering_id, session_id)).fetchone()
    round_id = current["id"] if current else None
    if round_id != payload["round_id"]:
        raise HTTPException(409, "本课次的有效点名已变化，请重新核对名单。")
    note = _edit_note(lessons[0], round_id, local.now_ms())
    if note:
        raise HTTPException(409, note)
    changes = payload["records"]
    if round_id is None:
        if any(item["revision"] != 0 for item in changes):
            raise HTTPException(409, "本次补记需要核对最新名单。")
        round_id = _manual_round(conn, offering, lessons[0], user)
        changes = [{**item, "revision": 1} for item in changes]
    from .smart_classroom_student_push_service import revoke_for_local_changes
    revoke_for_local_changes(conn, round_id, [item["student_id"] for item in changes])
    result = local.change_records(conn, round_id, user, {"records": changes, "reason": payload["reason"]}, include_round=False)
    if any(item["result"] != "updated" for item in result["results"]):
        raise HTTPException(409, "有学生的记录已更新，请重新核对；本次更正尚未保存。")
    result = {**result, "saved": len(changes), "round_id": round_id}
    if key:
        local._audit(conn, round_id, "calendar_saved", user, reason=key, after={"hash": fingerprint, "result": result})
    return result
