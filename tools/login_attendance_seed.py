"""Add synthetic attendance lessons only inside the physically owned Linux PG.

The standard controller first creates its empty, nonce-owned database and 200
synthetic students. This extension preserves those identities and class IDs.
There is no DSN, arbitrary host, import/export backup, or production attachment.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.platform_capacity_linux_lab import validate_container_boundary, _checked_pg_connection
from tools.login_attendance_lab import FIXTURE_KIND


def seed():
    identity = validate_container_boundary()
    marker, fixture = identity["marker"], identity["fixture"]
    artifacts = Path(identity["artifact_root"])
    output = artifacts / "attendance-fixture.json"
    if output.exists():
        raise ValueError("Preserve an existing owned attendance seed")
    conn, _ = _checked_pg_connection(marker)
    try:
        with conn.transaction():
            required = {"login_attendance_rounds", "login_attendance_records", "login_attendance_access"}
            tables = {row[0] for row in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()}
            if not required.issubset(tables):
                raise ValueError("New attendance runtime/schema must be built before this seed")
            if conn.execute("SELECT COUNT(*) FROM login_attendance_rounds").fetchone()[0]:
                raise ValueError("Attendance seed requires a fresh unused attendance domain")
            note = "Owned attendance capacity " + identity["lab_nonce"]
            if conn.execute("SELECT COUNT(*) FROM class_offering_sessions WHERE schedule_note=%s", (note,)).fetchone()[0]:
                raise ValueError("Preserve an interrupted seed instead of creating duplicate lessons")
            row = conn.execute("SELECT class_id,teacher_id FROM class_offerings WHERE id=%s",
                               (fixture["classOfferingId"],)).fetchone()
            if tuple(row) != (fixture["classId"], fixture["teacher"]["id"]):
                raise ValueError("Owner differs from the physically guarded fixture")
            last = conn.execute("SELECT COALESCE(MAX(order_index),0) FROM class_offering_sessions WHERE class_offering_id=%s",
                                (fixture["classOfferingId"],)).fetchone()[0]
            today = datetime.now().astimezone().date()
            sessions = {}
            for offset, role in enumerate(("main", "lifecycle"), 1):
                sessions[role] = conn.execute("""INSERT INTO class_offering_sessions
                    (class_offering_id,order_index,title,content,section_count,slot_section_count,
                     session_date,weekday,week_index,schedule_source,academic_time_text,schedule_note)
                    VALUES(%s,%s,%s,'Synthetic only',2,2,%s,%s,0,'fixed_cycle','00:00—01:30',%s)
                    RETURNING id""", (fixture["classOfferingId"], last+offset,
                    "Synthetic attendance " + role, today.isoformat(), today.isoweekday(),
                    note)).fetchone()[0]
            # Real independent teacher identities read their own 200-record
            # historical projections. Eight-day-old windows cannot create
            # overlapping active check-in contexts for the live 200 learners.
            encoded = conn.execute("SELECT hashed_password FROM teachers WHERE id=%s", (fixture["teacher"]["id"],)).fetchone()[0]
            students = conn.execute("SELECT s.id,s.student_id_number,s.name,c.name FROM students s JOIN classes c ON c.id=s.class_id "
                                    "WHERE s.id=ANY(%s) ORDER BY s.id", ([u["student_id"] for u in fixture["users"]],)).fetchall()
            viewers = []
            previous = today-timedelta(days=8)
            start = datetime.combine(previous, datetime.min.time(), tzinfo=timezone(timedelta(hours=8)))+timedelta(hours=8, minutes=30)
            start_ms = int(start.timestamp()*1000)
            end_ms = start_ms+90*60000
            for index in range(3):
                email = f"capacity-viewer-{identity['lab_nonce'][:8]}-{index}@example.invalid"
                teacher = conn.execute("""INSERT INTO teachers(name,email,hashed_password,password_updated_at,is_active,school_code,school_name)
                    VALUES(%s,%s,%s,%s,1,'capacity.synthetic','Synthetic capacity school') RETURNING id""",
                    (f"Synthetic history teacher {index}", email, encoded, datetime.now().isoformat())).fetchone()[0]
                course = conn.execute("INSERT INTO courses(name,sect_name,created_by_teacher_id,school_code,school_name) "
                    "VALUES(%s,'Synthetic',%s,'capacity.synthetic','Synthetic capacity school') RETURNING id",
                    (f"Synthetic history course {index}", teacher)).fetchone()[0]
                offering = conn.execute("INSERT INTO class_offerings(class_id,course_id,teacher_id,semester) "
                    "VALUES(%s,%s,%s,'capacity-synthetic') RETURNING id", (fixture["classId"], course, teacher)).fetchone()[0]
                session = conn.execute("""INSERT INTO class_offering_sessions(class_offering_id,order_index,title,session_date,
                    weekday,section_count,slot_section_count,academic_time_text,schedule_note)
                    VALUES(%s,1,'Synthetic archived lesson',%s,%s,2,2,'08:30—10:00',%s) RETURNING id""",
                    (offering, previous.isoformat(), previous.isoweekday(), note)).fetchone()[0]
                policy = json.dumps({"networks": [marker["network"]["gateway"]+"/32"], "before_minutes": 10,
                                     "after_minutes": 10, "appeal_days": 7})
                context = json.dumps({"session_title": "Synthetic archived lesson", "session_date": previous.isoformat(),
                                      "class_offering_id": offering, "session_id": session})
                round_id = conn.execute("""INSERT INTO login_attendance_rounds(class_offering_id,session_id,teacher_id,
                    request_key,state,starts_at_ms,ends_at_ms,window_start_ms,window_end_ms,settle_at_ms,created_at_ms,
                    ended_at_ms,evaluated_at_ms,next_evaluation_ms,policy_json,context_json)
                    VALUES(%s,%s,%s,%s,'published',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (offering, session, teacher, "synthetic-history-"+identity["lab_nonce"]+str(index), start_ms, end_ms,
                     start_ms-600000, end_ms+600000, end_ms+720000, start_ms-60000, end_ms, end_ms+720000,
                     int(datetime.now().timestamp()*1000)+86400000, policy, context)).fetchone()[0]
                with conn.cursor() as cursor:
                    cursor.executemany("""INSERT INTO login_attendance_records(round_id,student_id,student_number,name,class_name,
                        status,suggestion,quality,manual_confirmed,reason,decided_by,decided_at_ms,updated_at_ms)
                        VALUES(%s,%s,%s,%s,%s,'PRESENT','PRESENT','synthetic_archive',1,'Synthetic archived fixture',%s,%s,%s)""",
                        [(round_id, sid, number, name, class_name, teacher, end_ms, end_ms) for sid,number,name,class_name in students])
                viewers.append({"id": teacher, "email": email, "offering_id": offering, "session_id": session, "round_id": round_id})
        result = {"kind": FIXTURE_KIND, "schema_version": 1, "synthetic": True,
            "lab_nonce": identity["lab_nonce"], "release_id": marker["release_id"],
            "offering_id": fixture["classOfferingId"], "students": fixture["users"],
            "teacher": {"id": fixture["teacher"]["id"], "email": fixture["teacher"]["email"]},
            "viewers": viewers,
            "sessions": sessions, "networks": [marker["network"]["gateway"] + "/32"],
            "private_canary": "ATTENDANCE_PRIVATE_CANARY_" + identity["lab_nonce"],
            "created_at_ms": int(datetime.now().timestamp()*1000)}
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"status": "seeded_synthetic_attendance", "lab_nonce": identity["lab_nonce"],
                          "sessions": sessions, "fixture_sha256": hashlib.sha256(output.read_bytes()).hexdigest()}))
        return result
    finally:
        conn.close()


if __name__ == "__main__":
    seed()
