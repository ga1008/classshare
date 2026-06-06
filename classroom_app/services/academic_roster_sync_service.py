from __future__ import annotations

import html
import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx

from ..database import get_db_connection
from ..db.connection import execute_insert_returning_id, get_configured_db_engine
from .academic_calendar_sync_service import prepare_current_semester_from_academic_system
from .academic_integration_service import (
    load_teacher_academic_access_method,
    open_authenticated_academic_client,
)
from .academic_service import china_now, parse_date_input
from .department_service import infer_department_from_text, normalize_department
from .organization_scope_service import apply_teacher_scope_to_org, load_teacher_org_scope
from .student_lifecycle_service import STUDENT_STATUS_ACTIVE, STUDENT_STATUS_SUSPENDED


ACADEMIC_ROSTER_SOURCE = "gxufl_jwxt"
ZF_STUDENT_ROSTER_INDEX_PATH = "/xsxkjk/xsxkcx_cxXsxkIndex.html?gnmkdm=N255005&layout=default"
ZF_TEACHING_CLASS_LIST_PATH = "/xsxkjk/xsxkcx_cxJxbxxList.html?doType=query&gnmkdm=N255005"
ZF_TEACHING_CLASS_STUDENT_LIST_PATH = "/xsxkjk/xsxkcx_cxJxbxsList.html?doType=query&gnmkdm=N255005"
ROSTER_PAGE_SIZE = 500


FOLLOW_UP_ITEMS = [
    "检查教务教学班与本平台行政班级是否拆分正确",
    "复核学生手机号和邮箱，系统会保留本地已人工维护的联系方式",
    "对教务名单未覆盖但仍保留在本平台的学生进行人工确认",
    "到开设课堂页面确认课程、班级和教务教学班的绑定关系",
]


@dataclass
class AcademicRosterStudent:
    student_number: str
    name: str
    class_name: str
    class_code: str = ""
    gender: str = ""
    email: str = ""
    phone: str = ""
    college: str = ""
    grade: str = ""
    major: str = ""
    school_status: str = ""
    flags: dict[str, Any] = field(default_factory=dict)
    raw_json: dict[str, Any] = field(default_factory=dict)


@dataclass
class AcademicTeachingClassRoster:
    teaching_class_id: str
    teaching_class_name: str
    academic_year: str = ""
    academic_year_name: str = ""
    academic_term: str = ""
    academic_term_name: str = ""
    course_code: str = ""
    course_name: str = ""
    class_composition: str = ""
    college: str = ""
    teacher_name: str = ""
    schedule_text: str = ""
    location_text: str = ""
    declared_student_count: int = 0
    selected_student_count: int = 0
    raw_json: dict[str, Any] = field(default_factory=dict)
    students: list[AcademicRosterStudent] = field(default_factory=list)


def _now_iso() -> str:
    return china_now().replace(tzinfo=None).isoformat(timespec="seconds")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _normalize_space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u3000", " ")).strip()


def _strip_html(value: Any) -> str:
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", str(value or ""), flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    return _normalize_space(html.unescape(text))


def _parse_int(value: Any) -> int:
    match = re.search(r"\d+", str(value or ""))
    if not match:
        return 0
    try:
        return int(match.group(0))
    except ValueError:
        return 0


def _semester_year_start(semester: dict[str, Any]) -> int:
    name = str(semester.get("name") or "")
    match = re.search(r"(20\d{2})\s*[-—至]\s*(20\d{2})", name)
    if match:
        return int(match.group(1))
    start_date = parse_date_input(semester.get("start_date"))
    if start_date:
        return start_date.year if start_date.month >= 8 else start_date.year - 1
    today = china_now().date()
    return today.year if today.month >= 8 else today.year - 1


def _semester_term_number(semester: dict[str, Any]) -> int:
    name = str(semester.get("name") or "")
    if re.search(r"(第?\s*2|第二|二)\s*学期", name):
        return 2
    if re.search(r"(第?\s*1|第一|一)\s*学期", name):
        return 1
    start_date = parse_date_input(semester.get("start_date"))
    if start_date and 2 <= start_date.month <= 7:
        return 2
    return 1


def _term_param_candidates(semester: dict[str, Any]) -> list[dict[str, str]]:
    year_start = _semester_year_start(semester)
    term_number = _semester_term_number(semester)
    year_values = [str(year_start), f"{year_start}-{year_start + 1}"]
    term_values = ["12", "2"] if term_number == 2 else ["3", "1"]
    candidates: list[dict[str, str]] = []
    for xnm in year_values:
        for xqm in term_values:
            candidates.append({"xnm": xnm, "xqm": xqm})
    return candidates


def _ajax_headers(client: httpx.AsyncClient, *, accept: str = "application/json,text/javascript,*/*;q=0.8") -> dict[str, str]:
    base_url = str(client.base_url).rstrip("/")
    return {
        "Accept": accept,
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": base_url,
        "Referer": base_url + ZF_STUDENT_ROSTER_INDEX_PATH,
    }


def _jqgrid_form(
    *,
    page: int,
    show_count: int,
    extra: dict[str, Any],
) -> dict[str, Any]:
    return {
        **extra,
        "_search": "false",
        "nd": str(int(time.time() * 1000)),
        "queryModel.showCount": str(show_count),
        "queryModel.currentPage": str(max(1, int(page or 1))),
        "queryModel.sortName": " ",
        "queryModel.sortOrder": "asc",
        "time": "0",
    }


def _extract_items(payload: Any) -> tuple[list[dict[str, Any]], int, int]:
    if not isinstance(payload, dict):
        return [], 0, 0
    items = payload.get("items")
    if not isinstance(items, list):
        return [], _parse_int(payload.get("totalCount")), _parse_int(payload.get("totalPage"))
    rows = [dict(item) for item in items if isinstance(item, dict)]
    total_count = _parse_int(payload.get("totalCount") or payload.get("totalResult") or len(rows))
    total_page = max(1, _parse_int(payload.get("totalPage") or 1))
    return rows, total_count, total_page


def _clean_teaching_class_name(value: Any) -> str:
    return _strip_html(value).strip()


def _student_status_from_academic(value: Any) -> str:
    status = _normalize_space(value)
    if not status or "在读" in status:
        return STUDENT_STATUS_ACTIVE
    return STUDENT_STATUS_SUSPENDED


def _student_flags_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "is_retake": _normalize_space(row.get("CXBJ")),
        "is_second_major": _normalize_space(row.get("FXBJ")),
        "is_minor": _normalize_space(row.get("SFBX")),
        "is_self_study": _normalize_space(row.get("ZXBJ")),
        "student_tag": _normalize_space(row.get("YWXXK")),
        "selection_time": _normalize_space(row.get("XSXKSJ")),
        "external_selection_flag": _normalize_space(row.get("XXKWXBJ")),
    }


def _student_from_row(row: dict[str, Any], roster: AcademicTeachingClassRoster) -> AcademicRosterStudent | None:
    student_number = _normalize_space(row.get("XH") or row.get("XH_ID"))
    name = _normalize_space(row.get("XM"))
    if not student_number or not name:
        return None
    class_name = _normalize_space(row.get("BJ") or roster.class_composition)
    if not class_name or class_name in {"无", "未分班"}:
        class_name = f"教务未分班-{roster.teaching_class_name or roster.course_name or '学生'}"
    return AcademicRosterStudent(
        student_number=student_number,
        name=name,
        class_name=class_name,
        class_code=_normalize_space(row.get("BH_ID")),
        gender=_normalize_space(row.get("XB")),
        email=_normalize_space(row.get("DZYX")),
        phone=_normalize_space(row.get("SJHM")),
        college=_normalize_space(row.get("JGMC") or roster.college),
        grade=_normalize_space(row.get("NJMC") or row.get("NJDM_ID")),
        major=_normalize_space(row.get("ZYMC")),
        school_status=_normalize_space(row.get("XJZTMC")),
        flags=_student_flags_from_row(row),
        raw_json=dict(row),
    )


def _teaching_class_from_row(row: dict[str, Any]) -> AcademicTeachingClassRoster:
    return AcademicTeachingClassRoster(
        teaching_class_id=_normalize_space(row.get("JXB_ID")),
        teaching_class_name=_clean_teaching_class_name(row.get("JXBMC")),
        academic_year=_normalize_space(row.get("XNM")),
        academic_year_name=_normalize_space(row.get("XNMMC")),
        academic_term=_normalize_space(row.get("XQM")),
        academic_term_name=_normalize_space(row.get("XQMMC")),
        course_code=_normalize_space(row.get("KCH_ID")),
        course_name=_normalize_space(row.get("KCMC")),
        class_composition=_normalize_space(row.get("JXBZC")),
        college=_normalize_space(row.get("XBXX")),
        teacher_name=_normalize_space(row.get("JSXM")),
        schedule_text=_normalize_space(row.get("SKSJ")),
        location_text=_normalize_space(row.get("JXDD")),
        declared_student_count=_parse_int(row.get("RS")),
        selected_student_count=_parse_int(row.get("YSKXS")),
        raw_json=dict(row),
    )


async def _fetch_json(client: httpx.AsyncClient, path: str, data: dict[str, Any]) -> Any:
    response = await client.post(path, data=data, headers=_ajax_headers(client))
    response.raise_for_status()
    try:
        return response.json()
    except (json.JSONDecodeError, ValueError):
        return None


async def _fetch_teaching_classes(
    client: httpx.AsyncClient,
    semester: dict[str, Any],
    sources: list[dict[str, Any]],
) -> tuple[list[AcademicTeachingClassRoster], dict[str, str] | None]:
    try:
        response = await client.get(
            ZF_STUDENT_ROSTER_INDEX_PATH,
            headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
        )
        sources.append(
            {
                "path": ZF_STUDENT_ROSTER_INDEX_PATH,
                "method": "GET",
                "status_code": response.status_code,
                "url": str(response.url),
            }
        )
    except httpx.HTTPError as exc:
        sources.append(
            {
                "path": ZF_STUDENT_ROSTER_INDEX_PATH,
                "method": "GET",
                "status": "failed",
                "message": str(exc)[:180],
            }
        )

    for term_params in _term_param_candidates(semester):
        all_rows: list[dict[str, Any]] = []
        total_page = 1
        for page in range(1, 50):
            form = _jqgrid_form(page=page, show_count=ROSTER_PAGE_SIZE, extra=term_params)
            payload = await _fetch_json(client, ZF_TEACHING_CLASS_LIST_PATH, form)
            rows, total_count, total_page = _extract_items(payload)
            sources.append(
                {
                    "path": ZF_TEACHING_CLASS_LIST_PATH,
                    "method": "POST",
                    "params": {**term_params, "page": page, "showCount": ROSTER_PAGE_SIZE},
                    "parser": "teaching_class_list",
                    "item_count": len(rows),
                    "total_count": total_count,
                    "total_page": total_page,
                }
            )
            all_rows.extend(rows)
            if page >= total_page:
                break
        if all_rows:
            return [_teaching_class_from_row(row) for row in all_rows], term_params
    return [], None


async def _fetch_roster_students(
    client: httpx.AsyncClient,
    roster: AcademicTeachingClassRoster,
    sources: list[dict[str, Any]],
) -> list[AcademicRosterStudent]:
    if not roster.teaching_class_id:
        return []
    students: list[AcademicRosterStudent] = []
    total_page = 1
    for page in range(1, 200):
        form = _jqgrid_form(
            page=page,
            show_count=ROSTER_PAGE_SIZE,
            extra={
                "jxb_id": roster.teaching_class_id,
                "xnm": roster.academic_year,
                "xqm": roster.academic_term,
            },
        )
        payload = await _fetch_json(client, ZF_TEACHING_CLASS_STUDENT_LIST_PATH, form)
        rows, total_count, total_page = _extract_items(payload)
        sources.append(
            {
                "path": ZF_TEACHING_CLASS_STUDENT_LIST_PATH,
                "method": "POST",
                "params": {
                    "teaching_class_id": roster.teaching_class_id,
                    "academic_year": roster.academic_year,
                    "academic_term": roster.academic_term,
                    "page": page,
                    "showCount": ROSTER_PAGE_SIZE,
                },
                "parser": "student_roster",
                "item_count": len(rows),
                "total_count": total_count,
                "total_page": total_page,
            }
        )
        for row in rows:
            student = _student_from_row(row, roster)
            if student:
                students.append(student)
        if page >= total_page:
            break
    return students


def _load_current_semester(conn, teacher_id: int, today: date) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT *
        FROM academic_semesters
        WHERE teacher_id = ?
          AND date(start_date) <= date(?)
          AND date(end_date) >= date(?)
        ORDER BY start_date DESC, id DESC
        LIMIT 1
        """,
        (int(teacher_id), today.isoformat(), today.isoformat()),
    ).fetchone()
    return dict(row) if row else None


def _load_semester_by_id(conn, teacher_id: int, semester_id: int) -> dict[str, Any] | None:
    teacher_scope = load_teacher_org_scope(conn, teacher_id)
    row = conn.execute(
        """
        SELECT *
        FROM academic_semesters
        WHERE id = ?
          AND lower(TRIM(COALESCE(school_code, ?))) = lower(TRIM(?))
        LIMIT 1
        """,
        (int(semester_id), teacher_scope["school_code"], teacher_scope["school_code"]),
    ).fetchone()
    return dict(row) if row else None


def _find_course_id(conn, teacher_id: int, roster: AcademicTeachingClassRoster) -> int | None:
    teacher_scope = load_teacher_org_scope(conn, teacher_id)
    teacher_department = normalize_department(teacher_scope.get("department"))
    if roster.course_code:
        row = conn.execute(
            """
            SELECT id
            FROM courses
            WHERE (
                    created_by_teacher_id = ?
                    OR (
                        lower(TRIM(COALESCE(school_code, ?))) = lower(TRIM(?))
                        AND ? != ''
                        AND lower(TRIM(COALESCE(department, ''))) = lower(TRIM(?))
                    )
                  )
              AND academic_source = ?
              AND academic_course_code = ?
            ORDER BY CASE WHEN created_by_teacher_id = ? THEN 0 ELSE 1 END, id DESC
            LIMIT 1
            """,
            (
                int(teacher_id),
                teacher_scope["school_code"],
                teacher_scope["school_code"],
                teacher_department,
                teacher_department,
                ACADEMIC_ROSTER_SOURCE,
                roster.course_code,
                int(teacher_id),
            ),
        ).fetchone()
        if row:
            return int(row["id"])
    if roster.course_name:
        row = conn.execute(
            """
            SELECT id
            FROM courses
            WHERE (
                    created_by_teacher_id = ?
                    OR (
                        lower(TRIM(COALESCE(school_code, ?))) = lower(TRIM(?))
                        AND ? != ''
                        AND lower(TRIM(COALESCE(department, ''))) = lower(TRIM(?))
                    )
                  )
              AND name = ?
            ORDER BY CASE WHEN created_by_teacher_id = ? THEN 0 ELSE 1 END, id ASC
            LIMIT 1
            """,
            (
                int(teacher_id),
                teacher_scope["school_code"],
                teacher_scope["school_code"],
                teacher_department,
                teacher_department,
                roster.course_name,
                int(teacher_id),
            ),
        ).fetchone()
        if row:
            return int(row["id"])
    return None


def _class_metadata(student: AcademicRosterStudent, rosters: list[AcademicTeachingClassRoster]) -> dict[str, Any]:
    return {
        "school_code": "gxufl",
        "source": ACADEMIC_ROSTER_SOURCE,
        "class_code": student.class_code,
        "class_name": student.class_name,
        "college": student.college,
        "grade": student.grade,
        "major": student.major,
        "teaching_classes": [
            {
                "teaching_class_id": roster.teaching_class_id,
                "teaching_class_name": roster.teaching_class_name,
                "course_code": roster.course_code,
                "course_name": roster.course_name,
                "academic_year": roster.academic_year,
                "academic_term": roster.academic_term,
            }
            for roster in rosters[:20]
        ],
    }


def _upsert_class(
    conn,
    *,
    teacher_id: int,
    student: AcademicRosterStudent,
    rosters: list[AcademicTeachingClassRoster],
    synced_at: str,
    stats: dict[str, int],
    warnings: list[str],
) -> int | None:
    class_name = student.class_name.strip()
    row = conn.execute("SELECT * FROM classes WHERE name = ? LIMIT 1", (class_name,)).fetchone()
    department = normalize_department(student.college) or normalize_department(student.major) or infer_department_from_text(class_name)
    org_scope = apply_teacher_scope_to_org(
        conn,
        teacher_id,
        college=student.college,
        department=department,
    )
    message = f"由教务系统同步：{student.college or student.major or class_name}"
    metadata = _json_dumps(_class_metadata(student, rosters))
    if row is None:
        class_id = execute_insert_returning_id(
            conn,
            """
            INSERT INTO classes (
                name, department, created_by_teacher_id,
                school_code, school_name, college,
                academic_source, academic_class_code, academic_class_name,
                academic_college, academic_grade, academic_major,
                academic_sync_at, academic_sync_message, academic_metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                class_name,
                department,
                int(teacher_id),
                org_scope["school_code"],
                org_scope["school_name"],
                org_scope["college"],
                ACADEMIC_ROSTER_SOURCE,
                student.class_code,
                class_name,
                student.college,
                student.grade,
                student.major,
                synced_at,
                message,
                metadata,
            ),
        )
        stats["classes_created"] += 1
        return class_id

    if int(row["created_by_teacher_id"]) != int(teacher_id):
        warnings.append(f"班级“{class_name}”已属于其他教师，已跳过以避免误改。")
        stats["class_conflicts"] += 1
        return None

    conn.execute(
        """
        UPDATE classes
        SET department = CASE WHEN TRIM(COALESCE(department, '')) = '' THEN ? ELSE department END,
            school_code = CASE WHEN TRIM(COALESCE(school_code, '')) = '' THEN ? ELSE school_code END,
            school_name = CASE WHEN TRIM(COALESCE(school_name, '')) = '' THEN ? ELSE school_name END,
            college = CASE WHEN TRIM(COALESCE(college, '')) = '' THEN ? ELSE college END,
            academic_source = ?,
            academic_class_code = ?,
            academic_class_name = ?,
            academic_college = ?,
            academic_grade = ?,
            academic_major = ?,
            academic_sync_at = ?,
            academic_sync_message = ?,
            academic_metadata_json = ?
        WHERE id = ?
        """,
        (
            department,
            org_scope["school_code"],
            org_scope["school_name"],
            org_scope["college"],
            ACADEMIC_ROSTER_SOURCE,
            student.class_code,
            class_name,
            student.college,
            student.grade,
            student.major,
            synced_at,
            message,
            metadata,
            int(row["id"]),
        ),
    )
    stats["classes_updated"] += 1
    return int(row["id"])


def _student_metadata(student: AcademicRosterStudent, roster: AcademicTeachingClassRoster) -> dict[str, Any]:
    return {
        "school_code": "gxufl",
        "source": ACADEMIC_ROSTER_SOURCE,
        "academic_student_id": student.raw_json.get("XH_ID"),
        "academic_student_code": student.raw_json.get("XH_ID_CODE"),
        "class_code": student.class_code,
        "class_name": student.class_name,
        "college": student.college,
        "grade": student.grade,
        "major": student.major,
        "school_status": student.school_status,
        "flags": student.flags,
        "latest_teaching_class": {
            "teaching_class_id": roster.teaching_class_id,
            "teaching_class_name": roster.teaching_class_name,
            "course_code": roster.course_code,
            "course_name": roster.course_name,
            "academic_year": roster.academic_year,
            "academic_term": roster.academic_term,
        },
        "raw": student.raw_json,
    }


def _contact_value_for_update(existing: Any, incoming: str, *, existing_source: str, stats: dict[str, int]) -> str:
    current = _normalize_space(existing)
    if not incoming:
        return current
    if not current or existing_source == ACADEMIC_ROSTER_SOURCE:
        return incoming
    if current != incoming:
        stats["contact_conflicts"] += 1
    return current


def _upsert_student(
    conn,
    *,
    teacher_id: int,
    class_id: int,
    student: AcademicRosterStudent,
    roster: AcademicTeachingClassRoster,
    synced_at: str,
    stats: dict[str, int],
    warnings: list[str],
) -> int | None:
    existing = conn.execute(
        """
        SELECT s.*, c.created_by_teacher_id AS current_teacher_id, c.name AS current_class_name
        FROM students s
        JOIN classes c ON c.id = s.class_id
        WHERE s.student_id_number = ?
        LIMIT 1
        """,
        (student.student_number,),
    ).fetchone()
    academic_status = _student_status_from_academic(student.school_status)
    department = normalize_department(student.college) or normalize_department(student.major)
    org_scope = apply_teacher_scope_to_org(
        conn,
        teacher_id,
        college=student.college,
        department=department,
    )
    student_flags = _json_dumps(student.flags)
    metadata = _json_dumps(_student_metadata(student, roster))
    message = f"由教务系统同步：{roster.course_name or roster.teaching_class_name}"
    if existing is None:
        student_id = execute_insert_returning_id(
            conn,
            """
            INSERT INTO students (
                student_id_number, name, class_id, gender, email, phone,
                school_code, school_name, college, department,
                enrollment_status, enrollment_status_updated_at, enrollment_note,
                academic_source, academic_student_id, academic_class_code, academic_class_name,
                academic_college, academic_grade, academic_major, academic_school_status,
                academic_student_flags, academic_sync_at, academic_sync_message, academic_metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                student.student_number,
                student.name,
                int(class_id),
                student.gender,
                student.email,
                student.phone,
                org_scope["school_code"],
                org_scope["school_name"],
                org_scope["college"],
                org_scope["department"],
                academic_status,
                synced_at if academic_status != STUDENT_STATUS_ACTIVE else None,
                student.school_status if academic_status != STUDENT_STATUS_ACTIVE else "",
                ACADEMIC_ROSTER_SOURCE,
                _normalize_space(student.raw_json.get("XH_ID") or student.student_number),
                student.class_code,
                student.class_name,
                student.college,
                student.grade,
                student.major,
                student.school_status,
                student_flags,
                synced_at,
                message,
                metadata,
            ),
        )
        stats["students_created"] += 1
        return student_id

    if int(existing["current_teacher_id"]) != int(teacher_id):
        warnings.append(f"学生“{student.name} / {student.student_number}”已属于其他教师的班级，已跳过。")
        stats["student_conflicts"] += 1
        return None

    next_email = _contact_value_for_update(
        existing["email"],
        student.email,
        existing_source=str(existing["academic_source"] or ""),
        stats=stats,
    )
    next_phone = _contact_value_for_update(
        existing["phone"],
        student.phone,
        existing_source=str(existing["academic_source"] or ""),
        stats=stats,
    )
    moved = int(existing["class_id"]) != int(class_id)
    if moved:
        stats["students_moved"] += 1
    else:
        stats["students_updated"] += 1

    conn.execute(
        """
        UPDATE students
        SET class_id = ?,
            name = ?,
            gender = CASE WHEN ? != '' THEN ? ELSE gender END,
            email = ?,
            phone = ?,
            school_code = CASE WHEN TRIM(COALESCE(school_code, '')) = '' THEN ? ELSE school_code END,
            school_name = CASE WHEN TRIM(COALESCE(school_name, '')) = '' THEN ? ELSE school_name END,
            college = CASE WHEN TRIM(COALESCE(college, '')) = '' THEN ? ELSE college END,
            department = CASE WHEN TRIM(COALESCE(department, '')) = '' THEN ? ELSE department END,
            enrollment_status = ?,
            enrollment_status_updated_at = CASE
                WHEN COALESCE(enrollment_status, 'active') != ? THEN ?
                ELSE enrollment_status_updated_at
            END,
            enrollment_note = CASE WHEN ? != 'active' THEN ? ELSE enrollment_note END,
            academic_source = ?,
            academic_student_id = ?,
            academic_class_code = ?,
            academic_class_name = ?,
            academic_college = ?,
            academic_grade = ?,
            academic_major = ?,
            academic_school_status = ?,
            academic_student_flags = ?,
            academic_sync_at = ?,
            academic_sync_message = ?,
            academic_metadata_json = ?
        WHERE id = ?
        """,
        (
            int(class_id),
            student.name,
            student.gender,
            student.gender,
            next_email,
            next_phone,
            org_scope["school_code"],
            org_scope["school_name"],
            org_scope["college"],
            org_scope["department"],
            academic_status,
            academic_status,
            synced_at,
            academic_status,
            student.school_status,
            ACADEMIC_ROSTER_SOURCE,
            _normalize_space(student.raw_json.get("XH_ID") or student.student_number),
            student.class_code,
            student.class_name,
            student.college,
            student.grade,
            student.major,
            student.school_status,
            student_flags,
            synced_at,
            message,
            metadata,
            int(existing["id"]),
        ),
    )
    return int(existing["id"])


def _upsert_roster_item(
    conn,
    *,
    teacher_id: int,
    semester: dict[str, Any],
    roster: AcademicTeachingClassRoster,
    course_id: int | None,
    synced_at: str,
    source_url: str,
) -> int:
    sql = """
        INSERT INTO teacher_academic_roster_sync_items (
            teacher_id, semester_id, course_id, school_code,
            academic_year, academic_year_name, academic_term, academic_term_name,
            course_code, course_name, teaching_class_id, teaching_class_name,
            class_composition, college, teacher_name, schedule_text, location_text,
            declared_student_count, selected_student_count, raw_json, source_url,
            synced_at, updated_at
        )
        VALUES (?, ?, ?, 'gxufl', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (teacher_id, school_code, academic_year, academic_term, teaching_class_id)
        DO UPDATE SET
            semester_id = excluded.semester_id,
            course_id = excluded.course_id,
            academic_year_name = excluded.academic_year_name,
            academic_term_name = excluded.academic_term_name,
            course_code = excluded.course_code,
            course_name = excluded.course_name,
            teaching_class_name = excluded.teaching_class_name,
            class_composition = excluded.class_composition,
            college = excluded.college,
            teacher_name = excluded.teacher_name,
            schedule_text = excluded.schedule_text,
            location_text = excluded.location_text,
            declared_student_count = excluded.declared_student_count,
            selected_student_count = excluded.selected_student_count,
            raw_json = excluded.raw_json,
            source_url = excluded.source_url,
            synced_at = excluded.synced_at,
            updated_at = excluded.updated_at
        """
    params = (
        int(teacher_id),
        int(semester["id"]),
        course_id,
        roster.academic_year,
        roster.academic_year_name,
        roster.academic_term,
        roster.academic_term_name,
        roster.course_code,
        roster.course_name,
        roster.teaching_class_id,
        roster.teaching_class_name,
        roster.class_composition,
        roster.college,
        roster.teacher_name,
        roster.schedule_text,
        roster.location_text,
        roster.declared_student_count,
        roster.selected_student_count,
        _json_dumps(roster.raw_json),
        source_url,
        synced_at,
        synced_at,
    )
    if get_configured_db_engine() == "postgres":
        row = conn.execute(f"{sql} RETURNING id", params).fetchone()
        return int(row["id"]) if row else 0

    conn.execute(sql, params)
    row = conn.execute(
        """
        SELECT id
        FROM teacher_academic_roster_sync_items
        WHERE teacher_id = ?
          AND school_code = 'gxufl'
          AND academic_year = ?
          AND academic_term = ?
          AND teaching_class_id = ?
        LIMIT 1
        """,
        (int(teacher_id), roster.academic_year, roster.academic_term, roster.teaching_class_id),
    ).fetchone()
    return int(row["id"]) if row else 0


def _upsert_membership(
    conn,
    *,
    teacher_id: int,
    semester: dict[str, Any],
    sync_item_id: int,
    class_id: int,
    student_id: int,
    roster: AcademicTeachingClassRoster,
    student: AcademicRosterStudent,
    synced_at: str,
    source_url: str,
) -> None:
    conn.execute(
        """
        INSERT INTO teacher_academic_roster_memberships (
            teacher_id, semester_id, sync_item_id, class_id, student_id, school_code,
            academic_year, academic_term, course_code, course_name,
            teaching_class_id, teaching_class_name, admin_class_code, admin_class_name,
            student_number, student_name, school_status, raw_json, source_url,
            synced_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, 'gxufl', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (teacher_id, school_code, academic_year, academic_term, teaching_class_id, student_number)
        DO UPDATE SET
            semester_id = excluded.semester_id,
            sync_item_id = excluded.sync_item_id,
            class_id = excluded.class_id,
            student_id = excluded.student_id,
            course_code = excluded.course_code,
            course_name = excluded.course_name,
            teaching_class_name = excluded.teaching_class_name,
            admin_class_code = excluded.admin_class_code,
            admin_class_name = excluded.admin_class_name,
            student_name = excluded.student_name,
            school_status = excluded.school_status,
            raw_json = excluded.raw_json,
            source_url = excluded.source_url,
            synced_at = excluded.synced_at,
            updated_at = excluded.updated_at
        """,
        (
            int(teacher_id),
            int(semester["id"]),
            int(sync_item_id),
            int(class_id),
            int(student_id),
            roster.academic_year,
            roster.academic_term,
            roster.course_code,
            roster.course_name,
            roster.teaching_class_id,
            roster.teaching_class_name,
            student.class_code,
            student.class_name,
            student.student_number,
            student.name,
            student.school_status,
            _json_dumps(student.raw_json),
            source_url,
            synced_at,
            synced_at,
        ),
    )


def _mark_stale_students(
    conn,
    *,
    class_ids: set[int],
    synced_at: str,
) -> int:
    if not class_ids:
        return 0
    placeholders = ",".join("?" for _ in class_ids)
    cursor = conn.execute(
        f"""
        UPDATE students
        SET academic_sync_message = '本次教务名单同步未再次出现，请人工复核是否退选、转班或不属于当前任课名单。'
        WHERE class_id IN ({placeholders})
          AND academic_source = ?
          AND (academic_sync_at IS NULL OR academic_sync_at < ?)
        """,
        [*sorted(class_ids), ACADEMIC_ROSTER_SOURCE, synced_at],
    )
    return int(cursor.rowcount or 0)


def _persist_rosters(
    conn,
    *,
    teacher_id: int,
    semester: dict[str, Any],
    rosters: list[AcademicTeachingClassRoster],
    source_summary: list[dict[str, Any]],
    synced_at: str,
) -> dict[str, Any]:
    stats = {
        "classes_created": 0,
        "classes_updated": 0,
        "class_conflicts": 0,
        "students_created": 0,
        "students_updated": 0,
        "students_moved": 0,
        "student_conflicts": 0,
        "contact_conflicts": 0,
        "memberships_upserted": 0,
        "stale_students": 0,
    }
    warnings: list[str] = []
    touched_class_ids: set[int] = set()
    class_cache: dict[str, int | None] = {}
    student_cache: dict[str, int | None] = {}
    student_class_cache: dict[str, int] = {}
    course_count = len({roster.course_code or roster.course_name for roster in rosters if roster.course_code or roster.course_name})
    roster_results: list[dict[str, Any]] = []

    rosters_by_class_name: dict[str, list[AcademicTeachingClassRoster]] = {}
    for roster in rosters:
        for student in roster.students:
            rosters_by_class_name.setdefault(student.class_name, []).append(roster)

    for roster in rosters:
        course_id = _find_course_id(conn, teacher_id, roster)
        source_url = str(source_summary[-1].get("path") if source_summary else ZF_TEACHING_CLASS_STUDENT_LIST_PATH)
        sync_item_id = _upsert_roster_item(
            conn,
            teacher_id=teacher_id,
            semester=semester,
            roster=roster,
            course_id=course_id,
            synced_at=synced_at,
            source_url=source_url,
        )
        roster_class_ids: set[int] = set()
        imported_count = 0
        for student in roster.students:
            if student.class_name in class_cache:
                class_id = class_cache[student.class_name]
            else:
                class_id = _upsert_class(
                    conn,
                    teacher_id=teacher_id,
                    student=student,
                    rosters=rosters_by_class_name.get(student.class_name, [roster]),
                    synced_at=synced_at,
                    stats=stats,
                    warnings=warnings,
                )
                class_cache[student.class_name] = class_id
            if not class_id:
                continue
            touched_class_ids.add(int(class_id))
            roster_class_ids.add(int(class_id))
            cached_student_id = student_cache.get(student.student_number)
            cached_class_id = student_class_cache.get(student.student_number)
            if cached_student_id is not None and cached_class_id == int(class_id):
                student_id = cached_student_id
            else:
                student_id = _upsert_student(
                    conn,
                    teacher_id=teacher_id,
                    class_id=int(class_id),
                    student=student,
                    roster=roster,
                    synced_at=synced_at,
                    stats=stats,
                    warnings=warnings,
                )
                student_cache[student.student_number] = student_id
                if student_id:
                    student_class_cache[student.student_number] = int(class_id)
            if not student_id:
                continue
            _upsert_membership(
                conn,
                teacher_id=teacher_id,
                semester=semester,
                sync_item_id=sync_item_id,
                class_id=int(class_id),
                student_id=int(student_id),
                roster=roster,
                student=student,
                synced_at=synced_at,
                source_url=source_url,
            )
            stats["memberships_upserted"] += 1
            imported_count += 1

        unique_class_id = next(iter(roster_class_ids)) if len(roster_class_ids) == 1 else None
        conn.execute(
            """
            UPDATE teacher_academic_roster_sync_items
            SET class_id = ?,
                imported_student_count = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (unique_class_id, imported_count, synced_at, int(sync_item_id)),
        )
        roster_results.append(
            {
                "course_name": roster.course_name,
                "teaching_class_name": roster.teaching_class_name,
                "class_composition": roster.class_composition,
                "declared_student_count": roster.declared_student_count,
                "imported_student_count": imported_count,
                "class_count": len(roster_class_ids),
            }
        )

    stats["stale_students"] = _mark_stale_students(conn, class_ids=touched_class_ids, synced_at=synced_at)
    if stats["contact_conflicts"]:
        warnings.append("部分学生联系方式与本地人工维护值不同，系统保留了本地值，并把教务原始值写入元数据以便复核。")
    if stats["stale_students"]:
        warnings.append(f"{stats['stale_students']} 名既有教务同步学生本次未在名单中出现，系统已标记复核但未自动删除。")

    return {
        **stats,
        "course_count": course_count,
        "teaching_class_count": len(rosters),
        "roster_student_count": sum(len(roster.students) for roster in rosters),
        "touched_class_count": len(touched_class_ids),
        "rosters": roster_results,
        "warnings": warnings,
    }


async def _fetch_all_rosters(
    client: httpx.AsyncClient,
    semester: dict[str, Any],
) -> tuple[list[AcademicTeachingClassRoster], list[dict[str, Any]]]:
    sources: list[dict[str, Any]] = []
    rosters, term_params = await _fetch_teaching_classes(client, semester, sources)
    if not rosters:
        return [], sources
    for roster in rosters:
        if term_params:
            roster.academic_year = roster.academic_year or term_params.get("xnm", "")
            roster.academic_term = roster.academic_term or term_params.get("xqm", "")
        roster.students = await _fetch_roster_students(client, roster, sources)
    return rosters, sources


async def sync_current_teacher_rosters_from_academic_system(teacher_id: int) -> dict[str, Any]:
    with get_db_connection() as conn:
        access_payload = load_teacher_academic_access_method(conn, teacher_id, school_code="gxufl")
        semester = _load_current_semester(conn, teacher_id, china_now().date())

    if not access_payload:
        return {
            "status": "missing_credential",
            "message": "请先在系统设置中配置并验证教务系统账号，再同步班级与学生名单。",
        }

    if not semester:
        semester_result = await prepare_current_semester_from_academic_system(teacher_id)
        if semester_result.get("status") != "success":
            return {
                "status": "no_current_semester",
                "message": semester_result.get("message") or "未能从教务系统识别当前学期，暂不能同步班级名单。",
                "source_summary": semester_result.get("source_summary") or [],
            }
        with get_db_connection() as conn:
            semester = _load_semester_by_id(conn, teacher_id, int(semester_result["semester_id"]))

    if not semester:
        return {
            "status": "no_current_semester",
            "message": "请先新建或从教务系统同步当前学期，再同步班级与学生名单。",
        }

    try:
        async with open_authenticated_academic_client(access_payload) as (client, profile, login_result):
            rosters, source_summary = await _fetch_all_rosters(client, semester)
    except (ValueError, httpx.HTTPError) as exc:
        return {
            "status": "academic_login_failed",
            "message": f"教务系统登录或学生名单访问失败：{str(exc)[:180]}",
        }

    if not rosters:
        return {
            "status": "no_rosters",
            "message": "已登录教务系统，但没有查询到当前学期的教学班与学生名单。",
            "semester_id": int(semester["id"]),
            "semester_name": str(semester.get("name") or ""),
            "source_summary": source_summary,
        }

    synced_at = _now_iso()
    with get_db_connection() as conn:
        try:
            result = _persist_rosters(
                conn,
                teacher_id=teacher_id,
                semester=semester,
                rosters=rosters,
                source_summary=source_summary,
                synced_at=synced_at,
            )
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
            raise

    return {
        "status": "success",
        "message": (
            f"已从教务系统同步 {result['teaching_class_count']} 个教学班、"
            f"{result['touched_class_count']} 个本平台班级、{result['roster_student_count']} 条学生名单关系。"
            "系统已自动创建或更新班级与学生，未自动删除本地名单。"
        ),
        "semester_id": int(semester["id"]),
        "semester_name": str(semester.get("name") or ""),
        "synced_at": synced_at,
        "classes_created": result["classes_created"],
        "classes_updated": result["classes_updated"],
        "students_created": result["students_created"],
        "students_updated": result["students_updated"],
        "students_moved": result["students_moved"],
        "memberships_upserted": result["memberships_upserted"],
        "teaching_class_count": result["teaching_class_count"],
        "course_count": result["course_count"],
        "roster_student_count": result["roster_student_count"],
        "touched_class_count": result["touched_class_count"],
        "class_conflicts": result["class_conflicts"],
        "student_conflicts": result["student_conflicts"],
        "contact_conflicts": result["contact_conflicts"],
        "stale_students": result["stale_students"],
        "rosters": result["rosters"],
        "warnings": result["warnings"],
        "follow_up_items": [*result["warnings"][:3], *FOLLOW_UP_ITEMS],
        "source_summary": source_summary,
    }
