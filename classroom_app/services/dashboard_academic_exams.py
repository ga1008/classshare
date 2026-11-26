"""Personal, current-semester countdown. Reading never contacts the school."""
from __future__ import annotations

from .academic_exam_audience import student_exam_rows
from .academic_exam_jobs import eligible_teachers, enqueue, sync_state
from .academic_location_service import compose_exam_location
from .academic_service import china_now
from .offering_membership_service import offering_student_where


def _feed(conn, user, *, duty, today, now):
    cols = """e.course_name,e.course_display_name,e.exam_name,e.exam_date,e.exam_time_text,
              e.starts_at,e.ends_at,e.campus,e.building,e.location,e.teaching_class_name,
              e.class_composition,e.note,e.course_code,sem.name AS semester"""
    params = [today, today, today, now]
    if duty:
        source = "teacher_academic_invigilation_items e"
        cols += ",e.invigilation_role,e.invigilation_teachers"
    else:
        source = "teacher_academic_course_exam_items e"
        cols += ",e.teacher_name,e.class_offering_id"
    if user["role"] == "student":
        cols += ",s.class_id AS student_class_id"
        source += (" JOIN class_offerings o ON o.id=e.class_offering_id AND o.teacher_id=e.teacher_id"
                   f" JOIN students s ON {offering_student_where()}")
        scope = "s.id=? AND COALESCE(s.enrollment_status,'active')='active'"
    else:
        scope = "e.teacher_id=?"
    params.append(user["id"])
    rows = conn.execute(
        f"""SELECT {cols} FROM {source} JOIN academic_semesters sem ON sem.id=e.semester_id
            WHERE e.sync_status='active' AND sem.start_date<=? AND sem.end_date>=?
              AND (e.exam_date='' OR e.exam_date>=? OR e.ends_at>=?)
              AND {scope} ORDER BY e.id""", tuple(params),
    ).fetchall()
    rows = [dict(row) for row in rows]
    if user["role"] == "student" and rows:
        rows = student_exam_rows(conn, rows, int(rows[0]["student_class_id"]))
    items = []
    for r in rows:
        if r["ends_at"] and str(r["ends_at"]).replace(" ", "T") <= now:
            continue
        options = r.get("student_room_options", [])
        location = compose_exam_location(r["campus"], r["building"], r["location"])
        subject = r["course_name"] or r["course_display_name"] or r["exam_name"] or "考试"
        details = (("考试名称", r["exam_name"] if r["exam_name"] != subject else ""),
                   ("教务时间说明", r["exam_time_text"] if not r["starts_at"] else ""),
                   ("学期", r["semester"]),
                   ("课程代码", r["course_code"]), ("教学班", r["teaching_class_name"]),
                   ("班级组成", r["class_composition"]), ("任课教师", r.get("teacher_name")),
                   ("监考职责", r.get("invigilation_role")), ("监考教师", r.get("invigilation_teachers")),
                   ("候选考场", "、".join(options)), ("各考场时段", r.get("student_room_times")), ("备注", r["note"]))
        items.append({
            "kind": "invigilation" if duty else "exam", "subject": subject,
            "date": r["exam_date"] or "", "starts_at": r["starts_at"] or "", "ends_at": r["ends_at"] or "",
            "location": "多个考场，个人考场请核对教务座位名单" if options else location,
            "room_time_varies": bool(r.get("student_room_times")),
            "detail_rows": [{"label": k, "value": v} for k, v in details if v],
        })
    return items


def load_exam_dashboard(conn, user, *, refresh=False):
    clock = china_now().replace(tzinfo=None)
    date = clock.date().isoformat()
    now = clock.isoformat(timespec="seconds")
    items = _feed(conn, user, duty=False, today=date, now=now)
    if user["role"] == "teacher":
        items.extend(_feed(conn, user, duty=True, today=date, now=now))
    items.sort(key=lambda item: (item["date"] or "9999", item["starts_at"], item["subject"], item["kind"]))
    ids = eligible_teachers(conn, user)
    if refresh:
        enqueue(conn, ids)
    return {"items": items, "can_refresh": bool(ids), "sync": sync_state(conn, ids, user["role"])}
