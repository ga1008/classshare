from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import secrets
import shutil
import sqlite3
import string
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
TEMP_ROOT = REPO_ROOT / ".codex-temp"
DEFAULT_RUNTIME_ROOT = TEMP_ROOT / "p03-runtime"
SCHOOL_CODE = "p03-school"
SCHOOL_NAME = "P03 QA School"
COLLEGE = "P03 QA College"
DEPARTMENT = "P03 QA Department"
SEMESTER = "P03-2026"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _resolve_runtime_root(raw: str | None) -> Path:
    runtime = Path(raw) if raw else DEFAULT_RUNTIME_ROOT
    if not runtime.is_absolute():
        runtime = REPO_ROOT / runtime
    runtime = runtime.resolve()
    temp_root = TEMP_ROOT.resolve()
    if runtime != temp_root and temp_root not in runtime.parents:
        raise SystemExit(f"P03 runtime root must stay under {temp_root}; got {runtime}")
    return runtime


def _source_db_path() -> Path:
    candidates = [
        REPO_ROOT / "data" / "db" / "classroom.db",
        REPO_ROOT / "data" / "classroom.db",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise SystemExit("Cannot find data/classroom.db or data/db/classroom.db")


def _copy_runtime_db(runtime_root: Path) -> Path:
    if runtime_root.exists():
        shutil.rmtree(runtime_root)
    (runtime_root / "db").mkdir(parents=True, exist_ok=True)
    (runtime_root / "uploads").mkdir(parents=True, exist_ok=True)
    db_path = runtime_root / "db" / "classroom.db"
    shutil.copy2(_source_db_path(), db_path)
    return db_path


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _filter_values(conn: sqlite3.Connection, table: str, values: dict[str, Any]) -> dict[str, Any]:
    cols = _columns(conn, table)
    return {key: value for key, value in values.items() if key in cols}


def _insert(conn: sqlite3.Connection, table: str, values: dict[str, Any]) -> int:
    filtered = _filter_values(conn, table, values)
    keys = list(filtered.keys())
    placeholders = ", ".join("?" for _ in keys)
    conn.execute(
        f"INSERT INTO {table} ({', '.join(keys)}) VALUES ({placeholders})",
        [filtered[key] for key in keys],
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def _update(conn: sqlite3.Connection, table: str, row_id: int, values: dict[str, Any]) -> None:
    filtered = _filter_values(conn, table, {k: v for k, v in values.items() if k != "id"})
    if not filtered:
        return
    assignments = ", ".join(f"{key} = ?" for key in filtered)
    conn.execute(
        f"UPDATE {table} SET {assignments} WHERE id = ?",
        [*filtered.values(), row_id],
    )


def _upsert_by_column(
    conn: sqlite3.Connection,
    table: str,
    lookup_column: str,
    lookup_value: Any,
    values: dict[str, Any],
) -> int:
    row = conn.execute(
        f"SELECT id FROM {table} WHERE {lookup_column} = ? LIMIT 1",
        (lookup_value,),
    ).fetchone()
    payload = dict(values)
    payload[lookup_column] = lookup_value
    if row:
        row_id = int(row["id"])
        _update(conn, table, row_id, payload)
        return row_id
    return _insert(conn, table, payload)


def _teacher_identity(teacher_id: int) -> str:
    return f"teacher:{teacher_id}"


def _student_identity(student_id: int) -> str:
    return f"student:{student_id}"


def _random_password() -> str:
    alphabet = string.ascii_letters + string.digits
    return "P03-" + "".join(secrets.choice(alphabet) for _ in range(24))


def _ensure_teacher(conn: sqlite3.Connection, *, email: str, name: str, password_hash: str, super_admin: bool) -> int:
    teacher_id = _upsert_by_column(
        conn,
        "teachers",
        "email",
        email,
        {
            "name": name,
            "hashed_password": password_hash,
            "is_super_admin": 1 if super_admin else 0,
            "is_active": 1,
            "school_code": SCHOOL_CODE,
            "school_name": SCHOOL_NAME,
            "college": COLLEGE,
            "department": DEPARTMENT,
            "updated_at": _now(),
            "created_at": _now(),
        },
    )
    _ensure_teacher_membership(conn, teacher_id)
    return teacher_id


def _ensure_teacher_membership(conn: sqlite3.Connection, teacher_id: int) -> None:
    row = conn.execute(
        """
        SELECT id
        FROM teacher_organization_memberships
        WHERE teacher_id = ? AND school_code = ?
        LIMIT 1
        """,
        (teacher_id, SCHOOL_CODE),
    ).fetchone()
    values = {
        "teacher_id": teacher_id,
        "school_code": SCHOOL_CODE,
        "school_name": SCHOOL_NAME,
        "college": COLLEGE,
        "department": DEPARTMENT,
        "is_primary": 1,
        "is_active": 1,
        "source": "p03-fixture",
        "created_by_teacher_id": teacher_id,
        "updated_by_teacher_id": teacher_id,
        "updated_at": _now(),
        "deactivated_at": None,
    }
    if row:
        _update(conn, "teacher_organization_memberships", int(row["id"]), values)
        return
    values["created_at"] = _now()
    _insert(conn, "teacher_organization_memberships", values)


def _ensure_class(conn: sqlite3.Connection, *, name: str, teacher_id: int) -> int:
    return _upsert_by_column(
        conn,
        "classes",
        "name",
        name,
        {
            "created_by_teacher_id": teacher_id,
            "description": "P03 Playwright fixture class",
            "department": DEPARTMENT,
            "academic_source": "p03",
            "academic_class_code": name.replace(" ", "-").upper(),
            "academic_class_name": name,
            "academic_college": COLLEGE,
            "academic_grade": "2026",
            "academic_major": "Regression",
            "academic_metadata_json": "{}",
            "school_code": SCHOOL_CODE,
            "school_name": SCHOOL_NAME,
            "college": COLLEGE,
            "created_at": _now(),
        },
    )


def _ensure_student(
    conn: sqlite3.Connection,
    *,
    student_number: str,
    name: str,
    class_id: int,
    password_hash: str,
) -> int:
    return _upsert_by_column(
        conn,
        "students",
        "student_id_number",
        student_number,
        {
            "name": name,
            "class_id": class_id,
            "email": f"{student_number.lower()}@example.invalid",
            "hashed_password": password_hash,
            "password_reset_required": 0,
            "password_updated_at": _now(),
            "enrollment_status": "active",
            "academic_source": "p03",
            "academic_student_id": student_number,
            "academic_class_code": f"CLS-{class_id}",
            "academic_class_name": "P03 QA Class",
            "academic_college": COLLEGE,
            "academic_grade": "2026",
            "academic_major": "Regression",
            "academic_school_status": "active",
            "academic_student_flags": "",
            "academic_metadata_json": "{}",
            "school_code": SCHOOL_CODE,
            "school_name": SCHOOL_NAME,
            "college": COLLEGE,
            "department": DEPARTMENT,
            "created_at": _now(),
        },
    )


def _ensure_course(conn: sqlite3.Connection, *, name: str, teacher_id: int) -> int:
    row = conn.execute(
        "SELECT id FROM courses WHERE name = ? AND created_by_teacher_id = ? LIMIT 1",
        (name, teacher_id),
    ).fetchone()
    values = {
        "name": name,
        "description": "P03 Playwright fixture course",
        "credits": 1,
        "created_by_teacher_id": teacher_id,
        "created_at": _now(),
        "total_hours": 16,
        "sect_name": DEPARTMENT,
        "department": DEPARTMENT,
        "academic_source": "p03",
        "academic_course_code": "P03-COURSE",
        "academic_metadata_json": "{}",
        "school_code": SCHOOL_CODE,
        "school_name": SCHOOL_NAME,
        "college": COLLEGE,
    }
    if row:
        course_id = int(row["id"])
        _update(conn, "courses", course_id, values)
        return course_id
    return _insert(conn, "courses", values)


def _ensure_offering(conn: sqlite3.Connection, *, class_id: int, course_id: int, teacher_id: int) -> int:
    row = conn.execute(
        """
        SELECT id
        FROM class_offerings
        WHERE class_id = ? AND course_id = ? AND semester = ?
        LIMIT 1
        """,
        (class_id, course_id, SEMESTER),
    ).fetchone()
    values = {
        "class_id": class_id,
        "course_id": course_id,
        "teacher_id": teacher_id,
        "semester": SEMESTER,
        "schedule_info": "P03 regression fixture",
        "weekly_schedule_json": "[]",
        "schedule_source": "fixed_cycle",
        "academic_teaching_class_name": f"P03 Teaching Class {class_id}",
        "created_at": _now(),
    }
    if row:
        offering_id = int(row["id"])
        _update(conn, "class_offerings", offering_id, values)
        return offering_id
    return _insert(conn, "class_offerings", values)


def _cleanup_qa_assignments(conn: sqlite3.Connection) -> None:
    assignment_ids = [
        str(row["id"])
        for row in conn.execute("SELECT id FROM assignments WHERE title LIKE 'P03 QA %'")
    ]
    if assignment_ids:
        placeholders = ", ".join("?" for _ in assignment_ids)
        conn.execute(f"DELETE FROM submissions WHERE assignment_id IN ({placeholders})", assignment_ids)
        conn.execute(f"DELETE FROM assignments WHERE id IN ({placeholders})", assignment_ids)
    conn.execute("DELETE FROM message_center_notifications WHERE ref_type = 'p03-fixture'")


def _create_assignment(
    conn: sqlite3.Connection,
    *,
    course_id: int,
    class_offering_id: int,
    title: str,
) -> int:
    return _insert(
        conn,
        "assignments",
        {
            "course_id": course_id,
            "title": title,
            "status": "published",
            "requirements_md": (
                "# P03 regression task\n\n"
                "1. Explain one risk of changing permission or frontend entry code.\n"
                "2. Describe how to verify the workflow in a real browser.\n"
            ),
            "rubric_md": "Clarity 40; correctness 40; operational safety 20.",
            "grading_mode": "manual",
            "created_at": _now(),
            "class_offering_id": class_offering_id,
            "allowed_file_types_json": "[]",
            "availability_mode": "permanent",
            "auto_close": 0,
            "late_submission_enabled": 0,
            "late_penalty_strategy": "fixed",
            "late_penalty_interval_hours": 1,
            "late_penalty_points": 0,
            "late_penalty_min_score": 0,
        },
    )


def _create_submission(
    conn: sqlite3.Connection,
    *,
    assignment_id: int,
    student_id: int,
    student_name: str,
    status: str = "submitted",
    score: int | None = None,
    feedback_md: str = "",
) -> int:
    return _insert(
        conn,
        "submissions",
        {
            "assignment_id": str(assignment_id),
            "student_pk_id": student_id,
            "student_name": student_name,
            "status": status,
            "score": score,
            "feedback_md": feedback_md,
            "answers_json": json.dumps(
                {
                    "answers": [
                        {
                            "question": "P03 fixture answer",
                            "answer": "This answer belongs only to the copied P03 runtime database.",
                        }
                    ]
                },
                ensure_ascii=True,
            ),
            "submitted_at": _now(),
            "submitted_by_role": "student",
            "submission_channel": "online",
            "resubmission_allowed": 0,
            "started_at": _now(),
            "is_absence_score": 0,
            "is_late_submission": 0,
            "late_by_seconds": 0,
            "late_penalty_points": 0,
            "late_score_cap_applied": 0,
        },
    )


def _create_notification(
    conn: sqlite3.Connection,
    *,
    role: str,
    user_id: int,
    title: str,
    link_url: str,
    class_offering_id: int | None = None,
) -> int:
    identity = _teacher_identity(user_id) if role == "teacher" else _student_identity(user_id)
    return _insert(
        conn,
        "message_center_notifications",
        {
            "recipient_identity": identity,
            "recipient_role": role,
            "recipient_user_pk": user_id,
            "category": "system",
            "actor_identity": "system:p03",
            "actor_role": "system",
            "actor_user_pk": None,
            "actor_display_name": "P03 Fixture",
            "title": title,
            "body_preview": "P03 regression message in the copied runtime database only.",
            "link_url": link_url,
            "class_offering_id": class_offering_id,
            "ref_type": "p03-fixture",
            "ref_id": f"p03-{role}-{user_id}",
            "metadata_json": json.dumps({"source": "p03"}, ensure_ascii=True),
            "read_at": None,
            "created_at": _now(),
            "severity": "normal",
            "email_status": "not_required",
        },
    )


def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = [
        "teachers",
        "students",
        "classes",
        "courses",
        "class_offerings",
        "assignments",
        "submissions",
        "course_materials",
        "course_material_assignments",
        "message_center_notifications",
    ]
    result: dict[str, int] = {}
    for table in tables:
        try:
            result[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        except sqlite3.Error:
            result[table] = -1
    return result


def prepare(runtime_root: Path) -> dict[str, Any]:
    db_path = _copy_runtime_db(runtime_root)
    os.environ["LANSHARE_DATA_ROOT"] = str(runtime_root)
    os.environ["MAIN_DATA_DIR"] = str(runtime_root)

    sys.path.insert(0, str(REPO_ROOT))
    from classroom_app.database import init_database
    from classroom_app.dependencies import get_password_hash

    init_database()
    password = _random_password()
    password_hash = get_password_hash(password)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        before_check = conn.execute("PRAGMA quick_check").fetchone()[0]
        if before_check != "ok":
            raise SystemExit(f"Copied database failed PRAGMA quick_check before fixture: {before_check}")

        teacher_id = _ensure_teacher(
            conn,
            email="qa_p03_teacher@example.invalid",
            name="QA P03 Teacher",
            password_hash=password_hash,
            super_admin=False,
        )
        super_teacher_id = _ensure_teacher(
            conn,
            email="qa_p03_super@example.invalid",
            name="QA P03 Super",
            password_hash=password_hash,
            super_admin=True,
        )
        other_teacher_id = _ensure_teacher(
            conn,
            email="qa_p03_other@example.invalid",
            name="QA P03 Other Teacher",
            password_hash=password_hash,
            super_admin=False,
        )

        class_id = _ensure_class(conn, name="P03 QA Class", teacher_id=teacher_id)
        other_class_id = _ensure_class(conn, name="P03 QA Other Class", teacher_id=other_teacher_id)
        student_id = _ensure_student(
            conn,
            student_number="QA-P03-STUDENT",
            name="QA P03 Student",
            class_id=class_id,
            password_hash=password_hash,
        )
        other_student_id = _ensure_student(
            conn,
            student_number="QA-P03-OTHER-STUDENT",
            name="QA P03 Other Student",
            class_id=other_class_id,
            password_hash=password_hash,
        )

        course_id = _ensure_course(conn, name="P03 QA Course", teacher_id=teacher_id)
        other_course_id = _ensure_course(conn, name="P03 QA Other Course", teacher_id=other_teacher_id)
        class_offering_id = _ensure_offering(
            conn,
            class_id=class_id,
            course_id=course_id,
            teacher_id=teacher_id,
        )
        other_class_offering_id = _ensure_offering(
            conn,
            class_id=other_class_id,
            course_id=other_course_id,
            teacher_id=other_teacher_id,
        )

        _cleanup_qa_assignments(conn)
        student_assignment_id = _create_assignment(
            conn,
            course_id=course_id,
            class_offering_id=class_offering_id,
            title="P03 QA Student Submission",
        )
        teacher_review_assignment_id = _create_assignment(
            conn,
            course_id=course_id,
            class_offering_id=class_offering_id,
            title="P03 QA Teacher Review",
        )
        teacher_review_submission_id = _create_submission(
            conn,
            assignment_id=teacher_review_assignment_id,
            student_id=student_id,
            student_name="QA P03 Student",
        )
        ai_success_assignment_id = _create_assignment(
            conn,
            course_id=course_id,
            class_offering_id=class_offering_id,
            title="P03 QA AI Success",
        )
        ai_success_submission_id = _create_submission(
            conn,
            assignment_id=ai_success_assignment_id,
            student_id=student_id,
            student_name="QA P03 Student",
        )
        ai_stop_assignment_id = _create_assignment(
            conn,
            course_id=course_id,
            class_offering_id=class_offering_id,
            title="P03 QA AI Stop",
        )
        ai_stop_submission_id = _create_submission(
            conn,
            assignment_id=ai_stop_assignment_id,
            student_id=student_id,
            student_name="QA P03 Student",
        )

        teacher_notification_id = _create_notification(
            conn,
            role="teacher",
            user_id=teacher_id,
            title="P03 QA teacher notification",
            link_url="/profile?section=notifications#profile-message-center",
            class_offering_id=class_offering_id,
        )
        student_notification_id = _create_notification(
            conn,
            role="student",
            user_id=student_id,
            title="P03 QA student notification",
            link_url="/profile?section=notifications#profile-message-center",
            class_offering_id=class_offering_id,
        )

        baseline_counts = _counts(conn)
        after_check = conn.execute("PRAGMA quick_check").fetchone()[0]
        if after_check != "ok":
            raise SystemExit(f"Copied database failed PRAGMA quick_check after fixture: {after_check}")
        conn.commit()

    fixture = {
        "runtimeRoot": str(runtime_root),
        "databasePath": str(db_path),
        "password": password,
        "teacher": {
            "id": teacher_id,
            "email": "qa_p03_teacher@example.invalid",
            "name": "QA P03 Teacher",
        },
        "superTeacher": {
            "id": super_teacher_id,
            "email": "qa_p03_super@example.invalid",
            "name": "QA P03 Super",
        },
        "otherTeacher": {
            "id": other_teacher_id,
            "email": "qa_p03_other@example.invalid",
            "name": "QA P03 Other Teacher",
        },
        "student": {
            "id": student_id,
            "studentNumber": "QA-P03-STUDENT",
            "name": "QA P03 Student",
        },
        "otherStudent": {
            "id": other_student_id,
            "studentNumber": "QA-P03-OTHER-STUDENT",
            "name": "QA P03 Other Student",
        },
        "classId": class_id,
        "otherClassId": other_class_id,
        "courseId": course_id,
        "classOfferingId": class_offering_id,
        "otherClassOfferingId": other_class_offering_id,
        "studentSubmissionAssignmentId": student_assignment_id,
        "teacherReviewAssignmentId": teacher_review_assignment_id,
        "teacherReviewSubmissionId": teacher_review_submission_id,
        "aiSuccessAssignmentId": ai_success_assignment_id,
        "aiSuccessSubmissionId": ai_success_submission_id,
        "aiStopAssignmentId": ai_stop_assignment_id,
        "aiStopSubmissionId": ai_stop_submission_id,
        "teacherNotificationId": teacher_notification_id,
        "studentNotificationId": student_notification_id,
        "baselineCounts": baseline_counts,
        "preparedAt": _now(),
    }
    fixture_path = runtime_root / "fixture.json"
    fixture_path.write_text(json.dumps(fixture, ensure_ascii=True, indent=2), encoding="utf-8")
    (runtime_root / "baseline_counts.json").write_text(
        json.dumps(baseline_counts, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    return fixture


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", default=os.getenv("P03_RUNTIME_ROOT"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    runtime_root = _resolve_runtime_root(args.runtime_root)
    fixture = prepare(runtime_root)
    if args.json:
        redacted = dict(fixture)
        redacted["password"] = "<redacted>"
        print(json.dumps(redacted, ensure_ascii=True, indent=2))
    else:
        print(f"P03 runtime prepared at {runtime_root}")
        print(f"P03 copied database: {fixture['databasePath']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
