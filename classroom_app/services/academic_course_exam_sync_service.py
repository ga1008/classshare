from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time as time_module
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from ..database import get_db_connection
from ..db.connection import execute_insert_returning_id, get_configured_db_engine
from ..db.errors import DatabaseProgrammingError
from .academic_calendar_sync_service import prepare_current_semester_from_academic_system
from .academic_integration_service import (
    load_teacher_academic_access_method,
    open_authenticated_academic_client,
)
from .academic_service import china_now, parse_date_input
from .message_center_service import create_academic_exam_notification
from .organization_scope_service import load_teacher_org_scope


ACADEMIC_COURSE_EXAM_SOURCE = "gxufl_jwxt"
SCHOOL_CODE = "gxufl"
TEACHER_CALENDAR_SOURCE_COURSE_EXAM = "academic_course_exam"

ZF_COURSE_EXAM_INDEX_PATH = "/kwgl/rkjskscx_cxRkjsksIndex.html?gnmkdm=N358126&layout=default"
ZF_COURSE_EXAM_QUERY_PATH = "/kwgl/rkjskscx_cxRkjsksIndex.html?doType=query&gnmkdm=N358126"
ZF_EXAM_NAME_OPTIONS_PATH = "/ksglcommon/common_cxKsmcByXnxq.html"
COURSE_EXAM_PAGE_SIZE = 150

FOLLOW_UP_ITEMS = [
    "已识别的任课考试会写入课堂日程与教师日历，学生端会收到重要通知和邮件队列。",
    "课堂详情浮窗会显示本课程匹配到的考试安排；若教务后续调整，重新同步会更新同一条考试记录。",
    "如果教务系统返回了考试但本地未能唯一匹配课堂，记录仍会保留在教师端教务对接卡片中，便于后续修正课程/班级映射。",
]


def _fetch_postgres_column_names(conn, table_name: str) -> set[str]:
    rows = conn.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = ? AND table_name = ?
        """,
        ("public", table_name),
    ).fetchall()
    return {str(row["column_name"] if isinstance(row, dict) else row[0]) for row in rows}


def ensure_course_exam_schema(conn: sqlite3.Connection) -> None:
    if get_configured_db_engine() == "postgres":
        required_columns = {
            "id",
            "teacher_id",
            "semester_id",
            "class_offering_id",
            "course_id",
            "class_id",
            "school_code",
            "academic_year",
            "academic_term",
            "exam_key",
            "course_code",
            "course_name",
            "teaching_class_name",
            "starts_at",
            "ends_at",
            "sync_status",
            "synced_at",
        }
        actual_columns = _fetch_postgres_column_names(conn, "teacher_academic_course_exam_items")
        missing = sorted(required_columns - actual_columns)
        if missing:
            raise DatabaseProgrammingError(
                "PostgreSQL schema validation failed for teacher_academic_course_exam_items: "
                + ", ".join(missing)
            )
        return

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS teacher_academic_course_exam_items
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER NOT NULL,
            semester_id INTEGER,
            class_offering_id INTEGER,
            course_id INTEGER,
            class_id INTEGER,
            school_code TEXT NOT NULL DEFAULT 'gxufl',
            academic_year TEXT NOT NULL DEFAULT '',
            academic_year_name TEXT NOT NULL DEFAULT '',
            academic_term TEXT NOT NULL DEFAULT '',
            academic_term_name TEXT NOT NULL DEFAULT '',
            exam_key TEXT NOT NULL,
            exam_batch_id TEXT NOT NULL DEFAULT '',
            exam_name TEXT NOT NULL DEFAULT '',
            exam_paper_id TEXT NOT NULL DEFAULT '',
            exam_paper_code TEXT NOT NULL DEFAULT '',
            course_code TEXT NOT NULL DEFAULT '',
            course_name TEXT NOT NULL DEFAULT '',
            course_display_name TEXT NOT NULL DEFAULT '',
            teaching_class_name TEXT NOT NULL DEFAULT '',
            class_composition TEXT NOT NULL DEFAULT '',
            teacher_name TEXT NOT NULL DEFAULT '',
            chief_invigilator TEXT NOT NULL DEFAULT '',
            assistant_invigilator TEXT NOT NULL DEFAULT '',
            course_college TEXT NOT NULL DEFAULT '',
            campus TEXT NOT NULL DEFAULT '',
            campus_id TEXT NOT NULL DEFAULT '',
            building TEXT NOT NULL DEFAULT '',
            location TEXT NOT NULL DEFAULT '',
            location_type TEXT NOT NULL DEFAULT '',
            location_type_id TEXT NOT NULL DEFAULT '',
            exam_student_count INTEGER NOT NULL DEFAULT 0,
            seat_count INTEGER NOT NULL DEFAULT 0,
            credits REAL,
            course_nature TEXT NOT NULL DEFAULT '',
            exam_time_text TEXT NOT NULL DEFAULT '',
            exam_date TEXT NOT NULL DEFAULT '',
            starts_at TEXT,
            ends_at TEXT,
            note TEXT NOT NULL DEFAULT '',
            raw_json TEXT NOT NULL DEFAULT '{}',
            source_url TEXT NOT NULL DEFAULT '',
            sync_status TEXT NOT NULL DEFAULT 'active',
            synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (teacher_id) REFERENCES teachers (id) ON DELETE CASCADE,
            FOREIGN KEY (semester_id) REFERENCES academic_semesters (id) ON DELETE SET NULL,
            FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE SET NULL,
            FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE SET NULL,
            FOREIGN KEY (class_id) REFERENCES classes (id) ON DELETE SET NULL,
            UNIQUE (teacher_id, school_code, academic_year, academic_term, exam_key)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teacher_academic_course_exam_items_teacher_semester "
        "ON teacher_academic_course_exam_items (teacher_id, semester_id, starts_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teacher_academic_course_exam_items_offering "
        "ON teacher_academic_course_exam_items (teacher_id, class_offering_id, starts_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teacher_academic_course_exam_items_term "
        "ON teacher_academic_course_exam_items (teacher_id, academic_year, academic_term, sync_status)"
    )


@dataclass
class AcademicCourseExamItem:
    exam_key: str
    academic_year: str = ""
    academic_year_name: str = ""
    academic_term: str = ""
    academic_term_name: str = ""
    exam_batch_id: str = ""
    exam_name: str = ""
    exam_paper_id: str = ""
    exam_paper_code: str = ""
    course_code: str = ""
    course_name: str = ""
    course_display_name: str = ""
    teaching_class_name: str = ""
    class_composition: str = ""
    teacher_name: str = ""
    chief_invigilator: str = ""
    assistant_invigilator: str = ""
    course_college: str = ""
    campus: str = ""
    campus_id: str = ""
    building: str = ""
    location: str = ""
    location_type: str = ""
    location_type_id: str = ""
    exam_student_count: int = 0
    seat_count: int = 0
    credits: float | None = None
    course_nature: str = ""
    exam_time_text: str = ""
    exam_date: str = ""
    starts_at: str = ""
    ends_at: str = ""
    note: str = ""
    raw_json: dict[str, Any] = field(default_factory=dict)
    source_url: str = ""
    class_offering_id: int | None = None
    course_id: int | None = None
    class_id: int | None = None


def _now_iso() -> str:
    return china_now().replace(tzinfo=None).isoformat(timespec="seconds")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_loads(value: Any, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return fallback


def _normalize_space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u3000", " ")).strip()


def _normalize_key(value: Any) -> str:
    return re.sub(r"\s+", "", _normalize_space(value)).lower()


def _field(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        for candidate in (key, key.lower(), key.upper()):
            if candidate in row and row.get(candidate) not in (None, ""):
                return _normalize_space(row.get(candidate))
    return ""


def _parse_int(value: Any) -> int:
    match = re.search(r"\d+", str(value or ""))
    if not match:
        return 0
    try:
        return int(match.group(0))
    except ValueError:
        return 0


def _parse_float(value: Any) -> float | None:
    match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _optional_int(value: Any) -> int | None:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return None
    return parsed or None


def _semester_year_start(semester: dict[str, Any]) -> int:
    name = str(semester.get("name") or semester.get("semester_name") or "")
    match = re.search(r"(20\d{2})\s*[-—至]\s*(20\d{2})", name)
    if match:
        return int(match.group(1))
    start_date = parse_date_input(semester.get("start_date"))
    if start_date:
        return start_date.year if start_date.month >= 8 else start_date.year - 1
    today = china_now().date()
    return today.year if today.month >= 8 else today.year - 1


def _semester_term_number(semester: dict[str, Any]) -> int:
    name = str(semester.get("name") or semester.get("semester_name") or "")
    if re.search(r"(第\s*)?(二|2)\s*学期", name):
        return 2
    if re.search(r"(第\s*)?(一|1)\s*学期", name):
        return 1
    start_date = parse_date_input(semester.get("start_date"))
    if start_date and 1 <= start_date.month <= 7:
        return 2
    return 1


def _term_param_candidates(semester: dict[str, Any]) -> list[dict[str, str]]:
    year_start = _semester_year_start(semester)
    term_number = _semester_term_number(semester)
    year_values = [str(year_start), f"{year_start}-{year_start + 1}"]
    term_values = ["12", "2"] if term_number == 2 else ["3", "1"]
    return [{"xnm": xnm, "xqm": xqm} for xnm in year_values for xqm in term_values]


def _ajax_headers(client: httpx.AsyncClient, *, referer: str = ZF_COURSE_EXAM_INDEX_PATH) -> dict[str, str]:
    base_url = str(client.base_url).rstrip("/")
    return {
        "Accept": "application/json,text/javascript,*/*;q=0.8",
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": base_url,
        "Referer": base_url + referer,
    }


def _decode_json_response(response: httpx.Response) -> Any:
    text = response.content.decode("utf-8", errors="replace")
    return json.loads(text)


def _build_query_form(term_params: dict[str, str], *, page: int = 1) -> dict[str, Any]:
    return {
        **term_params,
        "ksmcdmb_id": "",
        "ksrq": "",
        "sjbh": "",
        "kc": "",
        "jkjs": "",
        "kch": "",
        "_search": "false",
        "nd": str(int(time_module.time() * 1000)),
        "queryModel.showCount": str(COURSE_EXAM_PAGE_SIZE),
        "queryModel.currentPage": str(max(1, int(page or 1))),
        "queryModel.sortName": "kssj ",
        "queryModel.sortOrder": "asc",
        "time": str(max(0, int(page or 1) - 1)),
    }


def _split_course(value: Any) -> tuple[str, str, str]:
    display = _normalize_space(value)
    if "/" in display:
        code, name = display.split("/", 1)
        return _normalize_space(code), _normalize_space(name), display
    match = re.match(r"^([A-Za-z0-9_-]{4,})\s+(.+)$", display)
    if match:
        return _normalize_space(match.group(1)), _normalize_space(match.group(2)), display
    return "", display, display


def _parse_exam_time(value: Any) -> tuple[str, str, str, str]:
    text = _normalize_space(value)
    match = re.search(
        r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\s*[（(]?\s*(\d{1,2}:\d{2})\s*[-~—至]\s*(\d{1,2}:\d{2})",
        text,
    )
    if not match:
        date_match = re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", text)
        if not date_match:
            return "", "", "", text
        exam_date = f"{int(date_match.group(1)):04d}-{int(date_match.group(2)):02d}-{int(date_match.group(3)):02d}"
        return exam_date, "", "", text

    exam_date = f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    start_text = match.group(4)
    end_text = match.group(5)
    try:
        starts_dt = datetime.fromisoformat(f"{exam_date}T{start_text}")
        ends_dt = datetime.fromisoformat(f"{exam_date}T{end_text}")
        if ends_dt < starts_dt:
            ends_dt += timedelta(days=1)
        return (
            exam_date,
            starts_dt.isoformat(timespec="minutes"),
            ends_dt.isoformat(timespec="minutes"),
            text,
        )
    except ValueError:
        return exam_date, "", "", text


def _fallback_key(row: dict[str, Any], term_params: dict[str, str]) -> str:
    pieces = [
        term_params.get("xnm", ""),
        term_params.get("xqm", ""),
        _field(row, "pkvalue"),
        _field(row, "ksmcdmb_id"),
        _field(row, "sjbh_id"),
        _field(row, "kcmc"),
        _field(row, "jxbmc"),
        _field(row, "jxbzc"),
        _field(row, "cdmc"),
        _field(row, "kssj"),
    ]
    digest = hashlib.sha1("|".join(pieces).encode("utf-8", errors="ignore")).hexdigest()
    return f"fallback:{digest}"


def _item_from_row(
    row: dict[str, Any],
    *,
    term_params: dict[str, str],
    source_url: str,
) -> AcademicCourseExamItem:
    course_code, course_name, course_display_name = _split_course(_field(row, "kcmc", "kcmc_id"))
    exam_date, starts_at, ends_at, exam_time_text = _parse_exam_time(_field(row, "kssj"))
    exam_key = _field(row, "pkvalue") or _fallback_key(row, term_params)
    return AcademicCourseExamItem(
        exam_key=exam_key,
        academic_year=_field(row, "xnm") or term_params.get("xnm", ""),
        academic_year_name=_field(row, "xnmmc"),
        academic_term=_field(row, "xqm") or term_params.get("xqm", ""),
        academic_term_name=_field(row, "xqmmc"),
        exam_batch_id=_field(row, "ksmcdmb_id"),
        exam_name=_field(row, "ksmc"),
        exam_paper_id=_field(row, "sjbh_id"),
        exam_paper_code=_field(row, "sjbh"),
        course_code=course_code,
        course_name=course_name,
        course_display_name=course_display_name,
        teaching_class_name=_field(row, "jxbmc"),
        class_composition=_field(row, "jxbzc"),
        teacher_name=_field(row, "jsxm"),
        chief_invigilator=_field(row, "zjkjs"),
        assistant_invigilator=_field(row, "fjkjs"),
        course_college=_field(row, "kkxy"),
        campus=_field(row, "xqmc"),
        campus_id=_field(row, "xqh_id"),
        building=_field(row, "jxlmc", "lh"),
        location=_field(row, "cdmc"),
        location_type=_field(row, "cdlbmc"),
        location_type_id=_field(row, "cdlb_id"),
        exam_student_count=_parse_int(_field(row, "ksrs")),
        seat_count=_parse_int(_field(row, "kszws1")),
        credits=_parse_float(_field(row, "xf")),
        course_nature=_field(row, "kcxzmc"),
        exam_time_text=exam_time_text,
        exam_date=exam_date,
        starts_at=starts_at,
        ends_at=ends_at,
        note=_field(row, "ksbz", "biaoji", "bz"),
        raw_json=dict(row),
        source_url=source_url,
    )


def _items_from_payload(
    payload: Any,
    *,
    term_params: dict[str, str],
    source_url: str,
) -> tuple[list[AcademicCourseExamItem], int, int]:
    raw_rows: list[Any] = []
    total_page = 1
    total_result = 0
    if isinstance(payload, dict):
        for key in ("items", "rows", "data"):
            if isinstance(payload.get(key), list):
                raw_rows = payload[key]
                break
        total_page = max(1, _parse_int(payload.get("totalPage") or payload.get("total_page") or 1))
        total_result = _parse_int(
            payload.get("totalResult")
            or payload.get("totalCount")
            or payload.get("records")
            or len(raw_rows)
        )
    elif isinstance(payload, list):
        raw_rows = payload
        total_result = len(raw_rows)

    items = [
        _item_from_row(row, term_params=term_params, source_url=source_url)
        for row in raw_rows
        if isinstance(row, dict)
    ]
    return items, total_page, total_result


async def _fetch_exam_name_options(
    client: httpx.AsyncClient,
    *,
    term_params: dict[str, str],
    sources: list[dict[str, Any]],
) -> None:
    try:
        response = await client.post(
            ZF_EXAM_NAME_OPTIONS_PATH,
            data=term_params,
            headers=_ajax_headers(client),
        )
        try:
            payload = _decode_json_response(response)
        except (json.JSONDecodeError, ValueError):
            payload = None
        sources.append(
            {
                "path": ZF_EXAM_NAME_OPTIONS_PATH,
                "method": "POST",
                "params": dict(term_params),
                "status_code": response.status_code,
                "parser": "exam_name_options",
                "item_count": len(payload) if isinstance(payload, list) else 0,
                "url": str(response.url),
            }
        )
    except httpx.HTTPError as exc:
        sources.append(
            {
                "path": ZF_EXAM_NAME_OPTIONS_PATH,
                "method": "POST",
                "params": dict(term_params),
                "status": "failed",
                "parser": "exam_name_options",
                "message": str(exc)[:180],
            }
        )


async def _fetch_course_exams_for_term(
    client: httpx.AsyncClient,
    *,
    term_params: dict[str, str],
    sources: list[dict[str, Any]],
) -> list[AcademicCourseExamItem]:
    items: list[AcademicCourseExamItem] = []
    total_page = 1
    total_result = 0
    for page in range(1, 100):
        form = _build_query_form(term_params, page=page)
        response = await client.post(
            ZF_COURSE_EXAM_QUERY_PATH,
            data=form,
            headers=_ajax_headers(client),
        )
        payload = _decode_json_response(response)
        page_items, payload_total_page, payload_total_result = _items_from_payload(
            payload,
            term_params=term_params,
            source_url=str(response.url),
        )
        total_page = max(total_page, payload_total_page)
        total_result = max(total_result, payload_total_result, len(items) + len(page_items))
        items.extend(page_items)
        sources.append(
            {
                "path": ZF_COURSE_EXAM_QUERY_PATH,
                "method": "POST",
                "params": dict(term_params),
                "status_code": response.status_code,
                "parser": "course_exam_query",
                "page": page,
                "total_page": total_page,
                "item_count": len(page_items),
                "total_result": total_result,
                "url": str(response.url),
            }
        )
        if page >= total_page or not page_items:
            break
    return items


async def _fetch_teacher_course_exams(
    client: httpx.AsyncClient,
    semester: dict[str, Any],
) -> tuple[list[AcademicCourseExamItem], list[dict[str, Any]], list[dict[str, str]]]:
    sources: list[dict[str, Any]] = []
    try:
        response = await client.get(
            ZF_COURSE_EXAM_INDEX_PATH,
            headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
        )
        sources.append(
            {
                "path": ZF_COURSE_EXAM_INDEX_PATH,
                "method": "GET",
                "status_code": response.status_code,
                "parser": "index_page",
                "url": str(response.url),
            }
        )
    except httpx.HTTPError as exc:
        sources.append(
            {
                "path": ZF_COURSE_EXAM_INDEX_PATH,
                "method": "GET",
                "status": "failed",
                "message": str(exc)[:180],
            }
        )

    candidates = _term_param_candidates(semester)
    last_empty_items: list[AcademicCourseExamItem] = []
    for term_params in candidates:
        await _fetch_exam_name_options(client, term_params=term_params, sources=sources)
        try:
            items = await _fetch_course_exams_for_term(client, term_params=term_params, sources=sources)
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            sources.append(
                {
                    "path": ZF_COURSE_EXAM_QUERY_PATH,
                    "method": "POST",
                    "params": dict(term_params),
                    "status": "failed",
                    "parser": "course_exam_query",
                    "message": str(exc)[:180],
                }
            )
            continue
        if items:
            return items, sources, [term_params]
        last_empty_items = items
    return last_empty_items, sources, candidates


def _load_current_semester(conn: sqlite3.Connection, teacher_id: int, today: date) -> dict[str, Any] | None:
    teacher_scope = load_teacher_org_scope(conn, teacher_id)
    row = conn.execute(
        """
        SELECT *
        FROM academic_semesters
        WHERE lower(TRIM(COALESCE(school_code, ?))) = lower(TRIM(?))
          AND date(start_date) <= date(?)
          AND date(end_date) >= date(?)
        ORDER BY CASE WHEN teacher_id = ? THEN 0 ELSE 1 END, updated_at DESC, id DESC
        LIMIT 1
        """,
        (
            teacher_scope["school_code"],
            teacher_scope["school_code"],
            today.isoformat(),
            today.isoformat(),
            int(teacher_id),
        ),
    ).fetchone()
    return dict(row) if row else None


def _load_semester_by_id(conn: sqlite3.Connection, teacher_id: int, semester_id: int) -> dict[str, Any] | None:
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


def _load_teacher_offering_contexts(
    conn: sqlite3.Connection,
    *,
    teacher_id: int,
    semester_id: int | None,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT o.id AS class_offering_id,
               o.class_id,
               o.course_id,
               o.teacher_id,
               o.semester_id,
               o.academic_teaching_class_name,
               c.name AS class_name,
               c.academic_class_code,
               c.academic_class_name,
               c.academic_college,
               c.academic_grade,
               c.academic_major,
               cr.name AS course_name,
               cr.academic_course_code,
               cr.credits,
               s.name AS semester_name,
               t.name AS teacher_name
        FROM class_offerings o
        JOIN classes c ON c.id = o.class_id
        JOIN courses cr ON cr.id = o.course_id
        JOIN teachers t ON t.id = o.teacher_id
        LEFT JOIN academic_semesters s ON s.id = o.semester_id
        WHERE o.teacher_id = ?
          AND (? IS NULL OR o.semester_id = ? OR o.semester_id IS NULL)
        ORDER BY o.semester_id DESC, o.id DESC
        """,
        (int(teacher_id), semester_id, semester_id),
    ).fetchall()
    return [dict(row) for row in rows]


def _score_exam_for_offering(item: AcademicCourseExamItem, context: dict[str, Any]) -> int:
    score = 0
    local_course_code = _normalize_key(context.get("academic_course_code"))
    remote_course_code = _normalize_key(item.course_code)
    if local_course_code and remote_course_code and local_course_code == remote_course_code:
        score += 60
    elif local_course_code and remote_course_code and (
        local_course_code in remote_course_code or remote_course_code in local_course_code
    ):
        score += 30

    local_course_name = _normalize_key(context.get("course_name"))
    remote_course_name = _normalize_key(item.course_name)
    if local_course_name and remote_course_name and local_course_name == remote_course_name:
        score += 36
    elif local_course_name and remote_course_name and (
        local_course_name in remote_course_name or remote_course_name in local_course_name
    ):
        score += 20

    teaching_class_name = _normalize_key(context.get("academic_teaching_class_name"))
    remote_teaching_class = _normalize_key(item.teaching_class_name)
    if teaching_class_name and remote_teaching_class and teaching_class_name == remote_teaching_class:
        score += 48
    elif teaching_class_name and remote_teaching_class and (
        teaching_class_name in remote_teaching_class or remote_teaching_class in teaching_class_name
    ):
        score += 20

    remote_class_composition = _normalize_key(item.class_composition)
    class_candidates = [
        context.get("academic_class_name"),
        context.get("academic_class_code"),
        context.get("class_name"),
    ]
    for class_candidate in class_candidates:
        normalized = _normalize_key(class_candidate)
        if normalized and remote_class_composition and (
            normalized in remote_class_composition or remote_class_composition in normalized
        ):
            score += 36
            break
    return score


def _attach_best_offering(item: AcademicCourseExamItem, contexts: list[dict[str, Any]]) -> int:
    scored = [(context, _score_exam_for_offering(item, context)) for context in contexts]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    if not scored or scored[0][1] < 45:
        return 0
    if len(scored) > 1 and scored[1][1] >= scored[0][1] - 8:
        return 0
    context, score = scored[0]
    item.class_offering_id = _optional_int(context.get("class_offering_id"))
    item.course_id = _optional_int(context.get("course_id"))
    item.class_id = _optional_int(context.get("class_id"))
    return score


def _source_key(item: AcademicCourseExamItem) -> str:
    return f"gxufl:{item.academic_year}:{item.academic_term}:{item.exam_key}"


def _event_title(item: AcademicCourseExamItem) -> str:
    target = item.course_name or item.course_display_name or item.exam_name or "未命名考试"
    return f"教务考试：{target}"


def _event_notes(item: AcademicCourseExamItem) -> str:
    parts = [
        item.exam_time_text,
        item.location,
        item.teaching_class_name,
        item.class_composition,
        f"{item.exam_student_count} 人" if item.exam_student_count else "",
        item.exam_name,
    ]
    return " | ".join(part for part in parts if part)


def _signature(item: AcademicCourseExamItem) -> str:
    pieces = [
        item.starts_at,
        item.ends_at,
        item.location,
        item.exam_time_text,
        item.exam_name,
        item.teaching_class_name,
        item.class_composition,
    ]
    return hashlib.sha1("|".join(pieces).encode("utf-8", errors="ignore")).hexdigest()[:16]


def _upsert_calendar_event(
    conn: sqlite3.Connection,
    *,
    teacher_id: int,
    semester: dict[str, Any],
    item_id: int,
    item: AcademicCourseExamItem,
    synced_at: str,
) -> tuple[bool, bool, int]:
    source_key = _source_key(item)
    existing = conn.execute(
        """
        SELECT id, starts_at, ends_at, location, title, status
        FROM teacher_calendar_events
        WHERE teacher_id = ?
          AND source_type = ?
          AND source_key = ?
        LIMIT 1
        """,
        (int(teacher_id), TEACHER_CALENDAR_SOURCE_COURSE_EXAM, source_key),
    ).fetchone()
    metadata = {
        "academic_source": ACADEMIC_COURSE_EXAM_SOURCE,
        "course_exam_item_id": item_id,
        "exam_name": item.exam_name,
        "exam_batch_id": item.exam_batch_id,
        "exam_paper_id": item.exam_paper_id,
        "exam_paper_code": item.exam_paper_code,
        "course_code": item.course_code,
        "course_name": item.course_name,
        "teaching_class_name": item.teaching_class_name,
        "student_count": item.exam_student_count,
        "exam_time_text": item.exam_time_text,
        "class_offering_id": item.class_offering_id,
        "signature": _signature(item),
    }
    title = _event_title(item)
    subtitle = item.exam_name or "教务系统任课考试"
    notes = _event_notes(item)
    starts_at = item.starts_at or None
    ends_at = item.ends_at or item.starts_at or None
    due_at = item.starts_at or item.exam_date or None
    link_url = f"/classroom/{item.class_offering_id}#timeline-panel" if item.class_offering_id else "/dashboard#dashboard-semester"
    params = (
        int(teacher_id),
        int(semester["id"]),
        TEACHER_CALENDAR_SOURCE_COURSE_EXAM,
        int(item_id),
        source_key,
        title,
        subtitle,
        notes,
        starts_at,
        ends_at,
        due_at,
        item.location,
        "active",
        "academic_exam",
        link_url,
        _json_dumps(metadata),
        synced_at,
        synced_at,
    )
    if existing is None:
        event_id = execute_insert_returning_id(
            conn,
            """
            INSERT INTO teacher_calendar_events (
                teacher_id, semester_id, source_type, source_id, source_key,
                title, subtitle, notes, starts_at, ends_at, due_at, location,
                status, tone, link_url, metadata_json, synced_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (*params, synced_at),
        )
        return True, False, event_id

    changed = any(
        str(existing[key] or "") != str(value or "")
        for key, value in {
            "starts_at": starts_at,
            "ends_at": ends_at,
            "location": item.location,
            "title": title,
            "status": "active",
        }.items()
    )
    conn.execute(
        """
        UPDATE teacher_calendar_events
        SET semester_id = ?,
            source_id = ?,
            title = ?,
            subtitle = ?,
            notes = ?,
            starts_at = ?,
            ends_at = ?,
            due_at = ?,
            location = ?,
            status = 'active',
            tone = 'academic_exam',
            link_url = ?,
            metadata_json = ?,
            synced_at = ?,
            updated_at = ?,
            deleted_at = NULL
        WHERE id = ?
        """,
        (
            int(semester["id"]),
            int(item_id),
            title,
            subtitle,
            notes,
            starts_at,
            ends_at,
            due_at,
            item.location,
            link_url,
            _json_dumps(metadata),
            synced_at,
            synced_at,
            int(existing["id"]),
        ),
    )
    return False, changed, int(existing["id"])


def _student_rows_for_offering(conn: sqlite3.Connection, class_offering_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT s.id,
               s.name,
               s.student_id_number
        FROM class_offerings o
        JOIN students s ON s.class_id = o.class_id
        WHERE o.id = ?
          AND COALESCE(s.enrollment_status, 'active') = 'active'
        ORDER BY s.student_id_number, s.id
        """,
        (int(class_offering_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def _maybe_notify_students(
    conn: sqlite3.Connection,
    *,
    item: AcademicCourseExamItem,
    is_created: bool,
    is_changed: bool,
) -> int:
    if not item.class_offering_id or not item.starts_at or not (is_created or is_changed):
        return 0
    try:
        starts_at = datetime.fromisoformat(item.starts_at)
    except ValueError:
        return 0
    if starts_at < china_now().replace(tzinfo=None):
        return 0

    change_label = "新增" if is_created else "更新"
    ref_id = f"academic-course-exam:{_source_key(item)}:{_signature(item)}"
    title = f"教务系统{change_label}考试：{item.course_name or item.exam_name or '课程考试'}"
    body_preview = _event_notes(item)
    count = 0
    for student in _student_rows_for_offering(conn, int(item.class_offering_id)):
        count += create_academic_exam_notification(
            conn,
            recipient_role="student",
            recipient_user_pk=int(student["id"]),
            title=title,
            body_preview=body_preview,
            link_url=f"/classroom/{item.class_offering_id}#timeline-panel",
            class_offering_id=int(item.class_offering_id),
            ref_id=ref_id,
            actor_display_name="教务系统",
            metadata={
                "source": ACADEMIC_COURSE_EXAM_SOURCE,
                "source_key": _source_key(item),
                "starts_at": item.starts_at,
                "ends_at": item.ends_at,
                "location": item.location,
                "course_name": item.course_name,
                "exam_name": item.exam_name,
            },
        )
    return count


def _mark_stale_events(
    conn: sqlite3.Connection,
    *,
    teacher_id: int,
    semester_id: int,
    source_keys: list[str],
    synced_at: str,
) -> int:
    if not source_keys:
        return 0
    placeholders = ",".join("?" for _ in source_keys)
    cursor = conn.execute(
        f"""
        UPDATE teacher_calendar_events
        SET status = 'stale',
            synced_at = ?,
            updated_at = ?
        WHERE teacher_id = ?
          AND semester_id = ?
          AND source_type = ?
          AND status = 'active'
          AND source_key IN ({placeholders})
        """,
        (synced_at, synced_at, int(teacher_id), int(semester_id), TEACHER_CALENDAR_SOURCE_COURSE_EXAM, *source_keys),
    )
    return int(cursor.rowcount or 0)


def _mark_stale_items_for_terms(
    conn: sqlite3.Connection,
    *,
    teacher_id: int,
    semester_id: int,
    term_params_list: list[dict[str, str]],
    seen_keys: set[str],
    synced_at: str,
    class_offering_id: int | None = None,
) -> int:
    stale_keys: list[str] = []
    stale_count = 0
    for term_params in term_params_list:
        params: list[Any] = [
            int(teacher_id),
            int(semester_id),
            term_params.get("xnm", ""),
            term_params.get("xqm", ""),
        ]
        offering_clause = ""
        if class_offering_id:
            offering_clause = " AND class_offering_id = ?"
            params.append(int(class_offering_id))
        rows = conn.execute(
            f"""
            SELECT id, academic_year, academic_term, exam_key
            FROM teacher_academic_course_exam_items
            WHERE teacher_id = ?
              AND semester_id = ?
              AND school_code = 'gxufl'
              AND academic_year = ?
              AND academic_term = ?
              AND sync_status = 'active'
              {offering_clause}
            """,
            tuple(params),
        ).fetchall()
        for row in rows:
            key = str(row["exam_key"] or "")
            if key in seen_keys:
                continue
            stale_count += 1
            source_key = f"gxufl:{row['academic_year']}:{row['academic_term']}:{key}"
            stale_keys.append(source_key)
            conn.execute(
                """
                UPDATE teacher_academic_course_exam_items
                SET sync_status = 'stale',
                    synced_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (synced_at, synced_at, int(row["id"])),
            )
    _mark_stale_events(
        conn,
        teacher_id=teacher_id,
        semester_id=semester_id,
        source_keys=stale_keys,
        synced_at=synced_at,
    )
    return stale_count


def _persist_course_exams(
    conn: sqlite3.Connection,
    *,
    teacher_id: int,
    semester: dict[str, Any],
    items: list[AcademicCourseExamItem],
    term_params_list: list[dict[str, str]],
    synced_at: str,
    class_offering_id: int | None = None,
) -> dict[str, int]:
    ensure_course_exam_schema(conn)
    contexts = _load_teacher_offering_contexts(
        conn,
        teacher_id=int(teacher_id),
        semester_id=_optional_int(semester.get("id")),
    )
    created_count = 0
    updated_count = 0
    matched_offering_count = 0
    event_created_count = 0
    event_updated_count = 0
    student_notification_count = 0
    seen_keys: set[str] = set()

    for item in items:
        score = _attach_best_offering(item, contexts)
        if score:
            matched_offering_count += 1
        if class_offering_id and item.class_offering_id != int(class_offering_id):
            continue

        seen_keys.add(item.exam_key)
        existing = conn.execute(
            """
            SELECT id, starts_at, ends_at, location, exam_name, class_offering_id, sync_status
            FROM teacher_academic_course_exam_items
            WHERE teacher_id = ?
              AND school_code = 'gxufl'
              AND academic_year = ?
              AND academic_term = ?
              AND exam_key = ?
            LIMIT 1
            """,
            (int(teacher_id), item.academic_year, item.academic_term, item.exam_key),
        ).fetchone()
        is_created = existing is None
        is_changed = False
        if is_created:
            created_count += 1
        else:
            updated_count += 1
            is_changed = any(
                str(existing[key] or "") != str(value or "")
                for key, value in {
                    "starts_at": item.starts_at or None,
                    "ends_at": item.ends_at or None,
                    "location": item.location,
                    "exam_name": item.exam_name,
                    "class_offering_id": item.class_offering_id,
                    "sync_status": "active",
                }.items()
            )

        conn.execute(
            """
            INSERT INTO teacher_academic_course_exam_items (
                teacher_id, semester_id, class_offering_id, course_id, class_id, school_code,
                academic_year, academic_year_name, academic_term, academic_term_name,
                exam_key, exam_batch_id, exam_name, exam_paper_id, exam_paper_code,
                course_code, course_name, course_display_name,
                teaching_class_name, class_composition, teacher_name,
                chief_invigilator, assistant_invigilator, course_college,
                campus, campus_id, building, location, location_type, location_type_id,
                exam_student_count, seat_count, credits, course_nature,
                exam_time_text, exam_date, starts_at, ends_at,
                note, raw_json, source_url, sync_status, synced_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'gxufl', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            ON CONFLICT(teacher_id, school_code, academic_year, academic_term, exam_key)
            DO UPDATE SET
                semester_id = excluded.semester_id,
                class_offering_id = excluded.class_offering_id,
                course_id = excluded.course_id,
                class_id = excluded.class_id,
                academic_year_name = excluded.academic_year_name,
                academic_term_name = excluded.academic_term_name,
                exam_batch_id = excluded.exam_batch_id,
                exam_name = excluded.exam_name,
                exam_paper_id = excluded.exam_paper_id,
                exam_paper_code = excluded.exam_paper_code,
                course_code = excluded.course_code,
                course_name = excluded.course_name,
                course_display_name = excluded.course_display_name,
                teaching_class_name = excluded.teaching_class_name,
                class_composition = excluded.class_composition,
                teacher_name = excluded.teacher_name,
                chief_invigilator = excluded.chief_invigilator,
                assistant_invigilator = excluded.assistant_invigilator,
                course_college = excluded.course_college,
                campus = excluded.campus,
                campus_id = excluded.campus_id,
                building = excluded.building,
                location = excluded.location,
                location_type = excluded.location_type,
                location_type_id = excluded.location_type_id,
                exam_student_count = excluded.exam_student_count,
                seat_count = excluded.seat_count,
                credits = excluded.credits,
                course_nature = excluded.course_nature,
                exam_time_text = excluded.exam_time_text,
                exam_date = excluded.exam_date,
                starts_at = excluded.starts_at,
                ends_at = excluded.ends_at,
                note = excluded.note,
                raw_json = excluded.raw_json,
                source_url = excluded.source_url,
                sync_status = 'active',
                synced_at = excluded.synced_at,
                updated_at = excluded.updated_at
            """,
            (
                int(teacher_id),
                int(semester["id"]),
                item.class_offering_id,
                item.course_id,
                item.class_id,
                item.academic_year,
                item.academic_year_name,
                item.academic_term,
                item.academic_term_name,
                item.exam_key,
                item.exam_batch_id,
                item.exam_name,
                item.exam_paper_id,
                item.exam_paper_code,
                item.course_code,
                item.course_name,
                item.course_display_name,
                item.teaching_class_name,
                item.class_composition,
                item.teacher_name,
                item.chief_invigilator,
                item.assistant_invigilator,
                item.course_college,
                item.campus,
                item.campus_id,
                item.building,
                item.location,
                item.location_type,
                item.location_type_id,
                int(item.exam_student_count or 0),
                int(item.seat_count or 0),
                item.credits,
                item.course_nature,
                item.exam_time_text,
                item.exam_date,
                item.starts_at or None,
                item.ends_at or None,
                item.note,
                _json_dumps(item.raw_json),
                item.source_url,
                synced_at,
                synced_at,
            ),
        )
        item_row = conn.execute(
            """
            SELECT id
            FROM teacher_academic_course_exam_items
            WHERE teacher_id = ?
              AND school_code = 'gxufl'
              AND academic_year = ?
              AND academic_term = ?
              AND exam_key = ?
            LIMIT 1
            """,
            (int(teacher_id), item.academic_year, item.academic_term, item.exam_key),
        ).fetchone()
        if item_row is None:
            continue
        event_created, event_changed, _ = _upsert_calendar_event(
            conn,
            teacher_id=int(teacher_id),
            semester=semester,
            item_id=int(item_row["id"]),
            item=item,
            synced_at=synced_at,
        )
        event_created_count += 1 if event_created else 0
        event_updated_count += 1 if event_changed else 0
        student_notification_count += _maybe_notify_students(
            conn,
            item=item,
            is_created=is_created or event_created,
            is_changed=is_changed or event_changed,
        )

    stale_count = _mark_stale_items_for_terms(
        conn,
        teacher_id=int(teacher_id),
        semester_id=int(semester["id"]),
        term_params_list=term_params_list,
        seen_keys=seen_keys,
        synced_at=synced_at,
        class_offering_id=class_offering_id,
    )
    return {
        "course_exam_count": created_count + updated_count,
        "created_count": created_count,
        "updated_count": updated_count,
        "matched_offering_count": matched_offering_count,
        "event_created_count": event_created_count,
        "event_updated_count": event_updated_count,
        "student_notification_count": student_notification_count,
        "stale_count": stale_count,
    }


def _serialize_course_exam_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    metadata = _json_loads(item.get("raw_json"), {})
    starts_at = str(item.get("starts_at") or "")
    ends_at = str(item.get("ends_at") or "")
    now = china_now().replace(tzinfo=None)
    status = "upcoming"
    status_label = "待考试"
    try:
        start_dt = datetime.fromisoformat(starts_at) if starts_at else None
        end_dt = datetime.fromisoformat(ends_at) if ends_at else start_dt
    except ValueError:
        start_dt = None
        end_dt = None
    if end_dt and end_dt < now:
        status = "completed"
        status_label = "已结束"
    elif start_dt and start_dt.date() == now.date():
        status = "current"
        status_label = "今天考试"

    return {
        "id": int(item.get("id") or 0),
        "semester_id": _optional_int(item.get("semester_id")),
        "class_offering_id": _optional_int(item.get("class_offering_id")),
        "course_id": _optional_int(item.get("course_id")),
        "class_id": _optional_int(item.get("class_id")),
        "exam_key": str(item.get("exam_key") or ""),
        "source_key": f"gxufl:{item.get('academic_year') or ''}:{item.get('academic_term') or ''}:{item.get('exam_key') or ''}",
        "academic_year": str(item.get("academic_year") or ""),
        "academic_year_name": str(item.get("academic_year_name") or ""),
        "academic_term": str(item.get("academic_term") or ""),
        "academic_term_name": str(item.get("academic_term_name") or ""),
        "exam_name": str(item.get("exam_name") or ""),
        "exam_paper_code": str(item.get("exam_paper_code") or ""),
        "course_code": str(item.get("course_code") or ""),
        "course_name": str(item.get("course_name") or ""),
        "course_display_name": str(item.get("course_display_name") or ""),
        "teaching_class_name": str(item.get("teaching_class_name") or ""),
        "class_composition": str(item.get("class_composition") or ""),
        "teacher_name": str(item.get("teacher_name") or ""),
        "course_college": str(item.get("course_college") or ""),
        "campus": str(item.get("campus") or ""),
        "building": str(item.get("building") or ""),
        "location": str(item.get("location") or ""),
        "location_type": str(item.get("location_type") or ""),
        "exam_student_count": int(item.get("exam_student_count") or 0),
        "seat_count": int(item.get("seat_count") or 0),
        "credits": item.get("credits"),
        "course_nature": str(item.get("course_nature") or ""),
        "exam_time_text": str(item.get("exam_time_text") or ""),
        "exam_date": str(item.get("exam_date") or ""),
        "starts_at": starts_at,
        "ends_at": ends_at,
        "note": str(item.get("note") or ""),
        "sync_status": str(item.get("sync_status") or "active"),
        "synced_at": str(item.get("synced_at") or ""),
        "created_at": str(item.get("created_at") or ""),
        "updated_at": str(item.get("updated_at") or ""),
        "status": status,
        "status_label": status_label,
        "raw_summary": {
            "pkvalue": metadata.get("pkvalue") if isinstance(metadata, dict) else "",
            "row_id": metadata.get("row_id") if isinstance(metadata, dict) else "",
        },
    }


def _same_course_identity(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_code = _normalize_key(left.get("course_code"))
    right_code = _normalize_key(right.get("course_code"))
    if left_code and right_code and left_code == right_code:
        return True

    left_names = {
        _normalize_key(left.get("course_name")),
        _normalize_key(left.get("course_display_name")),
    } - {""}
    right_names = {
        _normalize_key(right.get("course_name")),
        _normalize_key(right.get("course_display_name")),
    } - {""}
    if left_names & right_names:
        return True
    for left_name in left_names:
        for right_name in right_names:
            if len(left_name) >= 4 and len(right_name) >= 4 and (left_name in right_name or right_name in left_name):
                return True
    return False


def _serialize_related_invigilation(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    starts_at = str(item.get("starts_at") or "")
    ends_at = str(item.get("ends_at") or "")
    now = china_now().replace(tzinfo=None)
    status_label = "待监考"
    try:
        start_dt = datetime.fromisoformat(starts_at) if starts_at else None
        end_dt = datetime.fromisoformat(ends_at) if ends_at else start_dt
    except ValueError:
        start_dt = None
        end_dt = None
    if end_dt and end_dt < now:
        status_label = "已结束"
    elif start_dt and start_dt.date() == now.date():
        status_label = "今天监考"
    return {
        "id": int(item.get("id") or 0),
        "source_key": f"gxufl:{item.get('academic_year') or ''}:{item.get('academic_term') or ''}:{item.get('invigilation_key') or ''}",
        "academic_year": str(item.get("academic_year") or ""),
        "academic_term": str(item.get("academic_term") or ""),
        "exam_name": str(item.get("exam_name") or ""),
        "course_code": str(item.get("course_code") or ""),
        "course_name": str(item.get("course_name") or ""),
        "course_display_name": str(item.get("course_display_name") or ""),
        "teaching_class_name": str(item.get("teaching_class_name") or ""),
        "class_composition": str(item.get("class_composition") or ""),
        "invigilation_role": str(item.get("invigilation_role") or "监考"),
        "invigilation_teachers": str(item.get("invigilation_teachers") or ""),
        "campus": str(item.get("campus") or ""),
        "building": str(item.get("building") or ""),
        "location": str(item.get("location") or item.get("location_short_name") or ""),
        "location_short_name": str(item.get("location_short_name") or ""),
        "exam_time_text": str(item.get("exam_time_text") or ""),
        "exam_date": str(item.get("exam_date") or ""),
        "starts_at": starts_at,
        "ends_at": ends_at,
        "note": str(item.get("note") or ""),
        "status_label": status_label,
    }


def _attach_related_invigilations(
    conn: sqlite3.Connection,
    *,
    teacher_id: int,
    items: list[dict[str, Any]],
) -> None:
    if not items:
        return
    semester_ids = sorted({int(item.get("semester_id") or 0) for item in items if item.get("semester_id")})
    term_pairs = sorted({
        (str(item.get("academic_year") or ""), str(item.get("academic_term") or ""))
        for item in items
        if item.get("academic_year") or item.get("academic_term")
    })
    params: list[Any] = [int(teacher_id)]
    clauses = ["teacher_id = ?", "sync_status = 'active'"]
    semester_term_clauses: list[str] = []
    if semester_ids:
        placeholders = ",".join("?" for _ in semester_ids)
        semester_term_clauses.append(f"semester_id IN ({placeholders})")
        params.extend(semester_ids)
    for academic_year, academic_term in term_pairs:
        semester_term_clauses.append("(academic_year = ? AND academic_term = ?)")
        params.extend([academic_year, academic_term])
    if semester_term_clauses:
        clauses.append("(" + " OR ".join(semester_term_clauses) + ")")
    try:
        rows = conn.execute(
            f"""
            SELECT *
            FROM teacher_academic_invigilation_items
            WHERE {' AND '.join(clauses)}
            ORDER BY COALESCE(starts_at, exam_date, synced_at), id
            """,
            tuple(params),
        ).fetchall()
    except sqlite3.Error:
        return

    invigilations = [_serialize_related_invigilation(row) for row in rows]
    for item in items:
        related = [
            invigilation
            for invigilation in invigilations
            if _same_course_identity(item, invigilation)
        ]
        related.sort(key=lambda value: (str(value.get("starts_at") or value.get("exam_date") or ""), int(value.get("id") or 0)))
        item["related_invigilations"] = related
        item["related_invigilation_count"] = len(related)


def load_classroom_course_exam_status(
    teacher_id: int,
    class_offering_id: int,
) -> dict[str, Any]:
    with get_db_connection() as conn:
        ensure_course_exam_schema(conn)
        rows = conn.execute(
            """
            SELECT *
            FROM teacher_academic_course_exam_items
            WHERE teacher_id = ?
              AND class_offering_id = ?
              AND sync_status = 'active'
            ORDER BY COALESCE(starts_at, exam_date, created_at), id
            """,
            (int(teacher_id), int(class_offering_id)),
        ).fetchall()
        items = [_serialize_course_exam_row(row) for row in rows]
        _attach_related_invigilations(conn, teacher_id=int(teacher_id), items=items)
    return {
        "status": "success",
        "class_offering_id": int(class_offering_id),
        "items": items,
        "exam_count": len(items),
        "last_synced_at": max((str(item.get("synced_at") or "") for item in items), default=""),
        "has_synced": bool(items),
    }


def load_classroom_course_exam_status_for_user(
    conn: sqlite3.Connection,
    *,
    class_offering_id: int,
    user: dict[str, Any],
) -> dict[str, Any]:
    role = str(user.get("role") or "").strip().lower()
    ensure_course_exam_schema(conn)
    params: tuple[Any, ...]
    teacher_clause = ""
    if role == "teacher":
        teacher_clause = " AND teacher_id = ?"
        params = (int(class_offering_id), int(user["id"]))
    else:
        params = (int(class_offering_id),)
    rows = conn.execute(
        f"""
        SELECT *
        FROM teacher_academic_course_exam_items
        WHERE class_offering_id = ?
          AND sync_status = 'active'
          {teacher_clause}
        ORDER BY COALESCE(starts_at, exam_date, created_at), id
        """,
        params,
    ).fetchall()
    items = [_serialize_course_exam_row(row) for row in rows]
    if role == "teacher":
        _attach_related_invigilations(conn, teacher_id=int(user["id"]), items=items)
    return {
        "status": "success",
        "class_offering_id": int(class_offering_id),
        "items": items,
        "exam_count": len(items),
        "last_synced_at": max((str(item.get("synced_at") or "") for item in items), default=""),
        "has_synced": bool(items),
    }


def _timeline_date_label(value: str) -> str:
    parsed = parse_date_input(value)
    if not parsed:
        return ""
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    return f"{parsed.isoformat()} {weekdays[parsed.weekday()]}"


def _timeline_relative_label(value: str) -> str:
    parsed = parse_date_input(value)
    if not parsed:
        return ""
    today = china_now().date()
    delta = (parsed - today).days
    if delta == 0:
        return "今天考试"
    if delta == 1:
        return "明天考试"
    if delta > 1:
        return f"{delta} 天后考试"
    if delta == -1:
        return "昨天已考"
    return f"已结束 {abs(delta)} 天"


def build_course_exam_timeline_entry(item: dict[str, Any]) -> dict[str, Any]:
    exam_date = str(item.get("exam_date") or str(item.get("starts_at") or "")[:10])
    now = china_now().replace(tzinfo=None)
    starts_at = str(item.get("starts_at") or "")
    ends_at = str(item.get("ends_at") or "")
    progress_state = "upcoming"
    status_label = "待考试"
    try:
        starts_dt = datetime.fromisoformat(starts_at) if starts_at else None
        ends_dt = datetime.fromisoformat(ends_at) if ends_at else starts_dt
    except ValueError:
        starts_dt = None
        ends_dt = None
    if ends_dt and ends_dt < now:
        progress_state = "completed"
        status_label = "已结束"
    elif starts_dt and starts_dt.date() == now.date():
        progress_state = "current"
        status_label = "今天考试"
    title = item.get("course_name") or item.get("course_display_name") or item.get("exam_name") or "课程考试"
    detail_lines = [
        item.get("exam_name") or "",
        item.get("exam_time_text") or "",
        item.get("location") or "",
        item.get("teaching_class_name") or item.get("class_composition") or "",
        f"座位 {item.get('seat_count')} / 考生 {item.get('exam_student_count')}" if item.get("seat_count") or item.get("exam_student_count") else "",
    ]
    related_invigilations = [
        value for value in (item.get("related_invigilations") or []) if isinstance(value, dict)
    ]
    if related_invigilations:
        detail_lines.append("监考安排")
        for invigilation in related_invigilations[:3]:
            detail_lines.append(
                " · ".join(
                    part
                    for part in [
                        invigilation.get("invigilation_role") or "监考",
                        invigilation.get("exam_time_text") or "",
                        invigilation.get("location") or "",
                        invigilation.get("teaching_class_name") or invigilation.get("class_composition") or "",
                    ]
                    if part
                )
            )
    detail_content = "\n".join(part for part in detail_lines if part)
    return {
        "id": f"academic-exam-{item.get('id')}",
        "order_index": f"academic-exam-{item.get('id')}",
        "entry_type": "academic_exam",
        "is_academic_exam": True,
        "is_academic_schedule": False,
        "is_home_entry": False,
        "is_non_periodic": False,
        "is_anchor": False,
        "session_date": exam_date,
        "weekday": parse_date_input(exam_date).weekday() if parse_date_input(exam_date) else 0,
        "weekday_label": _timeline_date_label(exam_date).split(" ", 1)[-1] if exam_date else "",
        "week_index": 0,
        "week_label": "教务考试",
        "date_label": _timeline_date_label(exam_date),
        "relative_day_label": _timeline_relative_label(exam_date),
        "timeline_weekday_label": "教务考试",
        "timeline_relative_date_label": _timeline_relative_label(exam_date),
        "month_day_label": exam_date[5:] if len(exam_date) >= 10 else exam_date,
        "session_number_label": "考试",
        "segment_title": str(title)[:18],
        "session_status_label": status_label,
        "task_status_label": status_label,
        "progress_state": progress_state,
        "title": f"教务考试：{title}",
        "detail_title": f"教务考试：{title}",
        "detail_content": detail_content,
        "detail_summary": detail_content,
        "content_preview": detail_content,
        "detail_meta": " · ".join(part for part in [item.get("exam_time_text"), item.get("location"), item.get("exam_name")] if part),
        "detail_hint": "该考试来自教务系统任课教师考试查询，重新同步会更新本卡片。",
        "section_count": 0,
        "slot_section_count": 0,
        "is_section_match": True,
        "has_learning_material": False,
        "learning_material_id": None,
        "learning_material_name": "",
        "learning_material_path": "",
        "learning_material_viewer_url": "",
        "exam_item": item,
        "exam_name": item.get("exam_name") or "",
        "exam_time_text": item.get("exam_time_text") or "",
        "exam_location": item.get("location") or "",
        "exam_status_label": status_label,
        "related_invigilations": related_invigilations,
        "related_invigilation_count": len(related_invigilations),
        "starts_at": starts_at,
        "ends_at": ends_at,
    }


def merge_course_exams_into_teaching_plan(
    teaching_plan: dict[str, Any],
    course_exam_status: dict[str, Any] | None,
) -> dict[str, Any]:
    if not teaching_plan or not course_exam_status:
        return teaching_plan
    exam_entries = [
        build_course_exam_timeline_entry(item)
        for item in course_exam_status.get("items") or []
        if item.get("exam_date") or item.get("starts_at")
    ]
    if not exam_entries:
        teaching_plan["academic_course_exams"] = []
        return teaching_plan

    home_entry = teaching_plan.get("home_entry")
    lesson_entries = [
        dict(item)
        for item in (teaching_plan.get("sessions") or [])
        if not item.get("is_home_entry")
    ]
    for item in lesson_entries:
        item["is_anchor"] = False
    core_entries = lesson_entries + exam_entries
    core_entries.sort(
        key=lambda item: (
            str(item.get("session_date") or "9999-12-31"),
            1 if item.get("is_academic_exam") else 0,
            str(item.get("order_index") or ""),
        )
    )

    today = china_now().date()
    anchor_index = len(core_entries) - 1
    for index, entry in enumerate(core_entries):
        entry_date = parse_date_input(entry.get("session_date"))
        if entry_date and entry_date >= today:
            anchor_index = index
            break
    if core_entries:
        core_entries[anchor_index]["is_anchor"] = True
        teaching_plan["anchor_session"] = core_entries[anchor_index]
        teaching_plan["focus_title"] = core_entries[anchor_index].get("detail_title") or core_entries[anchor_index].get("title") or ""
        teaching_plan["focus_summary"] = core_entries[anchor_index].get("detail_summary") or ""
        teaching_plan["focus_meta"] = core_entries[anchor_index].get("detail_meta") or ""
        teaching_plan["focus_status_label"] = core_entries[anchor_index].get("session_status_label") or ""

    teaching_plan["timeline_entries"] = ([home_entry] if home_entry else []) + core_entries
    teaching_plan["timeline_entry_count"] = len(teaching_plan["timeline_entries"])
    teaching_plan["academic_course_exams"] = course_exam_status.get("items") or []
    teaching_plan["academic_course_exam_count"] = len(exam_entries)
    return teaching_plan


async def sync_current_teacher_course_exams_from_academic_system(teacher_id: int) -> dict[str, Any]:
    with get_db_connection() as conn:
        access_payload = load_teacher_academic_access_method(conn, teacher_id, school_code=SCHOOL_CODE)
        semester = _load_current_semester(conn, teacher_id, china_now().date())

    if not access_payload:
        return {
            "status": "missing_credential",
            "message": "请先在系统设置中配置并验证教务系统账号，再同步任课考试。",
        }

    if not semester:
        semester_result = await prepare_current_semester_from_academic_system(teacher_id)
        if semester_result.get("status") != "success":
            return {
                "status": "no_current_semester",
                "message": semester_result.get("message") or "未能从教务系统识别当前学期，暂不能同步任课考试。",
                "source_summary": semester_result.get("source_summary") or [],
            }
        with get_db_connection() as conn:
            semester = _load_semester_by_id(conn, teacher_id, int(semester_result["semester_id"]))

    if not semester:
        return {
            "status": "no_current_semester",
            "message": "请先新建或从教务系统同步当前学期，再同步任课考试。",
        }

    try:
        async with open_authenticated_academic_client(access_payload) as (client, profile, login_result):
            items, source_summary, term_params_list = await _fetch_teacher_course_exams(client, semester)
    except (ValueError, httpx.HTTPError) as exc:
        return {
            "status": "academic_login_failed",
            "message": f"教务系统登录或任课考试访问失败：{str(exc)[:180]}",
        }

    synced_at = _now_iso()
    with get_db_connection() as conn:
        try:
            result = _persist_course_exams(
                conn,
                teacher_id=int(teacher_id),
                semester=semester,
                items=items,
                term_params_list=term_params_list,
                synced_at=synced_at,
            )
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
            raise

    message = (
        f"已从教务系统同步 {result['course_exam_count']} 条任课考试，"
        f"新增 {result['created_count']} 条、更新 {result['updated_count']} 条，"
        f"匹配课堂 {result['matched_offering_count']} 条，"
        f"写入日历 {result['event_created_count'] + result['event_updated_count']} 条，"
        f"学生重要通知 {result['student_notification_count']} 条，"
        f"标记待复核 {result['stale_count']} 条。"
    )
    if not items:
        message = "已连接教务系统并完成任课考试同步检查，当前学期暂未查询到考试安排。"

    return {
        "status": "success",
        "message": message,
        "semester_id": int(semester["id"]),
        "semester_name": str(semester.get("name") or ""),
        "synced_at": synced_at,
        **result,
        "follow_up_items": FOLLOW_UP_ITEMS,
        "source_summary": source_summary,
        "login_display_name": login_result.get("display_name") if isinstance(login_result, dict) else "",
        "school_name": profile.school_name,
    }


async def sync_classroom_course_exams_from_academic_system(
    teacher_id: int,
    class_offering_id: int,
) -> dict[str, Any]:
    with get_db_connection() as conn:
        ownership = conn.execute(
            """
            SELECT id
            FROM class_offerings
            WHERE id = ? AND teacher_id = ?
            LIMIT 1
            """,
            (int(class_offering_id), int(teacher_id)),
        ).fetchone()
    if not ownership:
        raise PermissionError("当前教师无权同步该课堂的教务考试。")

    result = await sync_current_teacher_course_exams_from_academic_system(int(teacher_id))
    related_invigilation_sync: dict[str, Any] = {"status": "skipped"}
    if result.get("status") == "success":
        try:
            from .academic_invigilation_sync_service import sync_current_teacher_invigilations_from_academic_system

            related_invigilation_sync = await sync_current_teacher_invigilations_from_academic_system(int(teacher_id))
        except Exception as exc:
            related_invigilation_sync = {
                "status": "failed",
                "message": f"任课考试已同步，监考安排刷新失败：{str(exc)[:120]}",
            }
    status = load_classroom_course_exam_status(int(teacher_id), int(class_offering_id))
    return {
        **result,
        "class_offering_id": int(class_offering_id),
        "classroom_exam_status": status,
        "related_invigilation_sync": related_invigilation_sync,
    }
