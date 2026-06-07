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
from ..db.connection import execute_insert_returning_id
from .academic_calendar_sync_service import prepare_current_semester_from_academic_system
from .academic_location_service import (
    compose_exam_location,
    enrich_campus_building,
    load_teaching_place_location_index,
)
from .academic_integration_service import (
    load_teacher_academic_access_method,
    open_authenticated_academic_client,
)
from .academic_service import china_now, parse_date_input
from .message_center_service import create_todo_notification
from .organization_scope_service import load_teacher_org_scope


ACADEMIC_INVIGILATION_SOURCE = "gxufl_jwxt"
TEACHER_CALENDAR_SOURCE_INVIGILATION = "academic_invigilation"

ZF_INVIGILATION_INDEX_PATH = "/kwgl/jkcx_cxJsjkxxIndex.html?gnmkdm=N358125&layout=default"
ZF_INVIGILATION_QUERY_PATH = "/kwgl/jkcx_cxJsjkxxIndex.html?doType=query&gnmkdm=N358125"
ZF_EXAM_NAME_OPTIONS_PATH = "/ksglcommon/common_cxKsmcByXnxq.html"
INVIGILATION_PAGE_SIZE = 500

FOLLOW_UP_ITEMS = [
    "请在教师日历里复核监考时间与地点，若教务系统后续调整，重新同步会自动更新同一条监考安排。",
    "如教务系统当前学期暂未排监考，系统会保留同步状态，不会把账号校验误判为失败。",
    "若发现本地日历里已有但本次教务系统不再返回的监考，系统会标记为待复核并从活跃待办中移出。",
]

PHONE_OR_CONTACT_KEYS = {
    "zjkjssjh",
    "fjkjssjh",
    "sjh",
    "lxdh",
    "dh",
    "phone",
    "mobile",
    "tel",
}


@dataclass
class AcademicInvigilationItem:
    invigilation_key: str
    academic_year: str = ""
    academic_year_name: str = ""
    academic_term: str = ""
    academic_term_name: str = ""
    exam_batch_id: str = ""
    exam_name: str = ""
    exam_paper_id: str = ""
    exam_paper_code: str = ""
    invigilation_role: str = ""
    invigilation_teachers: str = ""
    course_code: str = ""
    course_name: str = ""
    course_display_name: str = ""
    teaching_class_name: str = ""
    class_composition: str = ""
    student_college: str = ""
    course_college: str = ""
    campus: str = ""
    building: str = ""
    location: str = ""
    location_short_name: str = ""
    location_type: str = ""
    location_type_id: str = ""
    exam_student_count: int = 0
    seat_count: int = 0
    exam_time_text: str = ""
    exam_date: str = ""
    starts_at: str = ""
    ends_at: str = ""
    note: str = ""
    raw_json: dict[str, Any] = field(default_factory=dict)
    source_url: str = ""


def _now_iso() -> str:
    return china_now().replace(tzinfo=None).isoformat(timespec="seconds")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _normalize_space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u3000", " ")).strip()


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
    if re.search(r"(第\s*)?2\s*(学期|期|semester)", name, flags=re.IGNORECASE):
        return 2
    if re.search(r"(第\s*)?1\s*(学期|期|semester)", name, flags=re.IGNORECASE):
        return 1
    start_date = parse_date_input(semester.get("start_date"))
    if start_date and start_date.month in {1, 2, 3, 4, 5, 6, 7}:
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


def _ajax_headers(client: httpx.AsyncClient, *, referer: str = ZF_INVIGILATION_INDEX_PATH) -> dict[str, str]:
    return {
        "Accept": "application/json,text/javascript,*/*;q=0.8",
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": str(client.base_url).rstrip("/"),
        "Referer": str(client.base_url).rstrip("/") + referer,
    }


def _build_query_form(term_params: dict[str, str], *, page: int = 1) -> dict[str, Any]:
    return {
        **term_params,
        "ksmcdmb_id": "",
        "ksrq": "",
        "sjbh": "",
        "kc": "",
        "kch": "",
        "kkbm_id": "",
        "jg_id": "",
        "pjkxy": "",
        "ksfsdm": "",
        "njdm_id": "",
        "zyh_id": "",
        "bh_id": "",
        "jxbzc": "",
        "_search": "false",
        "nd": str(int(time_module.time() * 1000)),
        "queryModel.showCount": str(INVIGILATION_PAGE_SIZE),
        "queryModel.currentPage": str(max(1, int(page or 1))),
        "queryModel.sortName": "kssj",
        "queryModel.sortOrder": "asc",
        "time": str(max(0, int(page or 1) - 1)),
    }


def _sanitize_raw_row(row: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in row.items():
        normalized_key = str(key or "").strip().lower()
        if normalized_key in PHONE_OR_CONTACT_KEYS or "sjh" in normalized_key or "phone" in normalized_key:
            continue
        sanitized[str(key)] = value
    return sanitized


def _field(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        if key in row and row.get(key) not in (None, ""):
            return _normalize_space(row.get(key))
    return ""


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
        starts_at = datetime.fromisoformat(f"{exam_date}T{start_text}").isoformat(timespec="minutes")
        ends_at_dt = datetime.fromisoformat(f"{exam_date}T{end_text}")
        if ends_at_dt < datetime.fromisoformat(f"{exam_date}T{start_text}"):
            ends_at_dt += timedelta(days=1)
        ends_at = ends_at_dt.isoformat(timespec="minutes")
        return exam_date, starts_at, ends_at, text
    except ValueError:
        return exam_date, "", "", text


def _fallback_key(row: dict[str, Any], term_params: dict[str, str]) -> str:
    pieces = [
        term_params.get("xnm", ""),
        term_params.get("xqm", ""),
        _field(row, "ksmcdmb_id"),
        _field(row, "sjbh_id"),
        _field(row, "kcmc"),
        _field(row, "jxbmc"),
        _field(row, "cdmc"),
        _field(row, "kssj"),
    ]
    digest = hashlib.sha1("|".join(pieces).encode("utf-8", errors="ignore")).hexdigest()
    return f"fallback:{digest}"


def _role_from_row(row: dict[str, Any]) -> tuple[str, str]:
    current_teacher = _field(row, "jsxm")
    chief = _field(row, "zjkjs")
    assistant = _field(row, "fjkjs")
    teachers = []
    if chief:
        teachers.append(f"主监考：{chief}")
    if assistant:
        teachers.append(f"副监考：{assistant}")
    if current_teacher and not teachers:
        teachers.append(current_teacher)
    role = "监考"
    if current_teacher and chief and current_teacher in chief:
        role = "主监考"
    elif current_teacher and assistant and current_teacher in assistant:
        role = "副监考"
    return role, "；".join(dict.fromkeys(teachers))


def _item_from_row(
    row: dict[str, Any],
    *,
    term_params: dict[str, str],
    source_url: str,
) -> AcademicInvigilationItem:
    course_code, course_name, course_display_name = _split_course(_field(row, "kcmc"))
    exam_date, starts_at, ends_at, exam_time_text = _parse_exam_time(_field(row, "kssj"))
    role, teachers = _role_from_row(row)
    invigilation_key = _field(row, "pkvalue", "row_id") or _fallback_key(row, term_params)
    return AcademicInvigilationItem(
        invigilation_key=invigilation_key,
        academic_year=_field(row, "xnm") or term_params.get("xnm", ""),
        academic_year_name=_field(row, "xnmmc"),
        academic_term=_field(row, "xqm") or term_params.get("xqm", ""),
        academic_term_name=_field(row, "xqmmc"),
        exam_batch_id=_field(row, "ksmcdmb_id"),
        exam_name=_field(row, "ksmc"),
        exam_paper_id=_field(row, "sjbh_id"),
        exam_paper_code=_field(row, "sjbh"),
        invigilation_role=role,
        invigilation_teachers=teachers,
        course_code=course_code,
        course_name=course_name,
        course_display_name=course_display_name,
        teaching_class_name=_field(row, "jxbmc"),
        class_composition=_field(row, "jxbzc"),
        student_college=_field(row, "xsxy"),
        course_college=_field(row, "kkxy"),
        campus=_field(row, "xqmc"),
        building=_field(row, "jxlmc", "lh"),
        location=_field(row, "cdmc"),
        location_short_name=_field(row, "cdjc"),
        location_type=_field(row, "cdlbmc"),
        location_type_id=_field(row, "cdlb_id"),
        exam_student_count=_parse_int(_field(row, "ksrs")),
        seat_count=_parse_int(_field(row, "kszws1")),
        exam_time_text=exam_time_text,
        exam_date=exam_date,
        starts_at=starts_at,
        ends_at=ends_at,
        note=_field(row, "biaoji"),
        raw_json=_sanitize_raw_row(row),
        source_url=source_url,
    )


def _items_from_payload(
    payload: Any,
    *,
    term_params: dict[str, str],
    source_url: str,
) -> tuple[list[AcademicInvigilationItem], int, int]:
    raw_rows: list[Any] = []
    total_page = 1
    total_result = 0
    if isinstance(payload, dict):
        for key in ("items", "rows", "data"):
            if isinstance(payload.get(key), list):
                raw_rows = payload[key]
                break
        total_page = max(1, _parse_int(payload.get("totalPage") or payload.get("total_page") or 1))
        total_result = _parse_int(payload.get("totalResult") or payload.get("records") or len(raw_rows))
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
        payload: Any = None
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError):
            payload = None
        option_count = len(payload) if isinstance(payload, list) else 0
        sources.append(
            {
                "path": ZF_EXAM_NAME_OPTIONS_PATH,
                "method": "POST",
                "params": dict(term_params),
                "status_code": response.status_code,
                "parser": "exam_name_options",
                "item_count": option_count,
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


async def _fetch_invigilations_for_term(
    client: httpx.AsyncClient,
    *,
    term_params: dict[str, str],
    sources: list[dict[str, Any]],
) -> list[AcademicInvigilationItem]:
    items: list[AcademicInvigilationItem] = []
    total_page = 1
    total_result = 0
    for page in range(1, 100):
        form = _build_query_form(term_params, page=page)
        response = await client.post(
            ZF_INVIGILATION_QUERY_PATH,
            data=form,
            headers=_ajax_headers(client),
        )
        payload = response.json()
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
                "path": ZF_INVIGILATION_QUERY_PATH,
                "method": "POST",
                "params": dict(term_params),
                "status_code": response.status_code,
                "parser": "invigilation_query",
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


async def _fetch_teacher_invigilations(
    client: httpx.AsyncClient,
    semester: dict[str, Any],
) -> tuple[list[AcademicInvigilationItem], list[dict[str, Any]], list[dict[str, str]]]:
    sources: list[dict[str, Any]] = []
    try:
        response = await client.get(
            ZF_INVIGILATION_INDEX_PATH,
            headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
        )
        sources.append(
            {
                "path": ZF_INVIGILATION_INDEX_PATH,
                "method": "GET",
                "status_code": response.status_code,
                "parser": "index_page",
                "url": str(response.url),
            }
        )
    except httpx.HTTPError as exc:
        sources.append(
            {
                "path": ZF_INVIGILATION_INDEX_PATH,
                "method": "GET",
                "status": "failed",
                "message": str(exc)[:180],
            }
        )

    candidates = _term_param_candidates(semester)
    last_empty_items: list[AcademicInvigilationItem] = []
    for term_params in candidates:
        await _fetch_exam_name_options(client, term_params=term_params, sources=sources)
        try:
            items = await _fetch_invigilations_for_term(client, term_params=term_params, sources=sources)
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            sources.append(
                {
                    "path": ZF_INVIGILATION_QUERY_PATH,
                    "method": "POST",
                    "params": dict(term_params),
                    "status": "failed",
                    "parser": "invigilation_query",
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


def _source_key(item: AcademicInvigilationItem) -> str:
    return f"gxufl:{item.academic_year}:{item.academic_term}:{item.invigilation_key}"


def _event_title(item: AcademicInvigilationItem) -> str:
    target = item.course_name or item.course_display_name or item.exam_name or "未命名考试"
    return f"监考：{target}"


def _full_location(item: AcademicInvigilationItem) -> str:
    return compose_exam_location(item.campus, item.building, item.location)


def _event_notes(item: AcademicInvigilationItem) -> str:
    parts = [
        item.exam_time_text,
        _full_location(item),
        item.teaching_class_name,
        item.class_composition,
        f"{item.exam_student_count} 人" if item.exam_student_count else "",
        item.invigilation_role,
    ]
    return " | ".join(part for part in parts if part)


def _upsert_calendar_event(
    conn: sqlite3.Connection,
    *,
    teacher_id: int,
    semester: dict[str, Any],
    item_id: int,
    item: AcademicInvigilationItem,
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
        (int(teacher_id), TEACHER_CALENDAR_SOURCE_INVIGILATION, source_key),
    ).fetchone()
    metadata = {
        "academic_source": ACADEMIC_INVIGILATION_SOURCE,
        "invigilation_item_id": item_id,
        "exam_name": item.exam_name,
        "exam_batch_id": item.exam_batch_id,
        "exam_paper_id": item.exam_paper_id,
        "exam_paper_code": item.exam_paper_code,
        "course_code": item.course_code,
        "course_name": item.course_name,
        "teaching_class_name": item.teaching_class_name,
        "student_count": item.exam_student_count,
        "exam_time_text": item.exam_time_text,
        "role": item.invigilation_role,
        "campus": item.campus,
        "building": item.building,
        "room": item.location,
        "location_full": _full_location(item),
    }
    title = _event_title(item)
    subtitle = item.exam_name or "教务系统监考"
    notes = _event_notes(item)
    full_location = _full_location(item)
    starts_at = item.starts_at or None
    ends_at = item.ends_at or item.starts_at or None
    due_at = item.starts_at or item.exam_date or None
    params = (
        int(teacher_id),
        int(semester["id"]),
        TEACHER_CALENDAR_SOURCE_INVIGILATION,
        int(item_id),
        source_key,
        title,
        subtitle,
        notes,
        starts_at,
        ends_at,
        due_at,
        full_location,
        "active",
        "invigilation",
        "/dashboard#dashboard-semester",
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
            "location": full_location,
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
            tone = 'invigilation',
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
            full_location,
            "/dashboard#dashboard-semester",
            _json_dumps(metadata),
            synced_at,
            synced_at,
            int(existing["id"]),
        ),
    )
    return False, changed, int(existing["id"])


def _maybe_create_invigilation_notification(
    conn: sqlite3.Connection,
    *,
    teacher_id: int,
    item: AcademicInvigilationItem,
    is_created: bool,
    is_changed: bool,
) -> int:
    if not item.starts_at or not (is_created or is_changed):
        return 0
    try:
        starts_at = datetime.fromisoformat(item.starts_at)
    except ValueError:
        return 0
    if starts_at < china_now().replace(tzinfo=None):
        return 0
    change_label = "新增" if is_created else "更新"
    ref_id = f"academic-invigilation:{_source_key(item)}:{item.starts_at}"
    return create_todo_notification(
        conn,
        recipient_role="teacher",
        recipient_user_pk=int(teacher_id),
        title=f"教务系统{change_label}监考：{item.course_name or item.exam_name or '考试'}",
        body_preview=_event_notes(item),
        link_url="/dashboard#dashboard-semester",
        ref_id=ref_id,
        actor_role="",
        actor_user_pk=None,
        actor_display_name="教务系统",
        metadata={
            "source": ACADEMIC_INVIGILATION_SOURCE,
            "source_key": _source_key(item),
            "starts_at": item.starts_at,
            "campus": item.campus,
            "building": item.building,
            "location": _full_location(item),
        },
    )


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
        (synced_at, synced_at, int(teacher_id), int(semester_id), TEACHER_CALENDAR_SOURCE_INVIGILATION, *source_keys),
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
) -> int:
    stale_keys: list[str] = []
    stale_count = 0
    for term_params in term_params_list:
        rows = conn.execute(
            """
            SELECT id, academic_year, academic_term, invigilation_key
            FROM teacher_academic_invigilation_items
            WHERE teacher_id = ?
              AND semester_id = ?
              AND school_code = 'gxufl'
              AND academic_year = ?
              AND academic_term = ?
              AND sync_status = 'active'
            """,
            (
                int(teacher_id),
                int(semester_id),
                term_params.get("xnm", ""),
                term_params.get("xqm", ""),
            ),
        ).fetchall()
        for row in rows:
            key = str(row["invigilation_key"] or "")
            if key in seen_keys:
                continue
            stale_count += 1
            source_key = f"gxufl:{row['academic_year']}:{row['academic_term']}:{key}"
            stale_keys.append(source_key)
            conn.execute(
                """
                UPDATE teacher_academic_invigilation_items
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


def _persist_invigilations(
    conn: sqlite3.Connection,
    *,
    teacher_id: int,
    semester: dict[str, Any],
    items: list[AcademicInvigilationItem],
    term_params_list: list[dict[str, str]],
    synced_at: str,
) -> dict[str, int]:
    created_count = 0
    updated_count = 0
    event_created_count = 0
    event_updated_count = 0
    notification_count = 0
    seen_keys: set[str] = set()
    place_index = load_teaching_place_location_index(conn, int(teacher_id))
    for item in items:
        seen_keys.add(item.invigilation_key)
        # Backfill campus/building when the invigilation feed omits them, so a
        # bare room name stays unambiguous across multiple campuses.
        item.campus, item.building = enrich_campus_building(
            place_index,
            campus=item.campus,
            building=item.building,
            location=item.location,
            location_short_name=item.location_short_name,
        )
        existing = conn.execute(
            """
            SELECT id
            FROM teacher_academic_invigilation_items
            WHERE teacher_id = ?
              AND school_code = 'gxufl'
              AND academic_year = ?
              AND academic_term = ?
              AND invigilation_key = ?
            LIMIT 1
            """,
            (int(teacher_id), item.academic_year, item.academic_term, item.invigilation_key),
        ).fetchone()
        if existing is None:
            created_count += 1
        else:
            updated_count += 1

        conn.execute(
            """
            INSERT INTO teacher_academic_invigilation_items (
                teacher_id, semester_id, school_code,
                academic_year, academic_year_name, academic_term, academic_term_name,
                exam_batch_id, exam_name, exam_paper_id, exam_paper_code,
                invigilation_key, invigilation_role, invigilation_teachers,
                course_code, course_name, course_display_name,
                teaching_class_name, class_composition,
                student_college, course_college,
                campus, building, location, location_short_name, location_type, location_type_id,
                exam_student_count, seat_count, exam_time_text, exam_date, starts_at, ends_at,
                note, raw_json, source_url, sync_status, synced_at, updated_at
            )
            VALUES (?, ?, 'gxufl', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            ON CONFLICT(teacher_id, school_code, academic_year, academic_term, invigilation_key)
            DO UPDATE SET
                semester_id = excluded.semester_id,
                academic_year_name = excluded.academic_year_name,
                academic_term_name = excluded.academic_term_name,
                exam_batch_id = excluded.exam_batch_id,
                exam_name = excluded.exam_name,
                exam_paper_id = excluded.exam_paper_id,
                exam_paper_code = excluded.exam_paper_code,
                invigilation_role = excluded.invigilation_role,
                invigilation_teachers = excluded.invigilation_teachers,
                course_code = excluded.course_code,
                course_name = excluded.course_name,
                course_display_name = excluded.course_display_name,
                teaching_class_name = excluded.teaching_class_name,
                class_composition = excluded.class_composition,
                student_college = excluded.student_college,
                course_college = excluded.course_college,
                campus = excluded.campus,
                building = excluded.building,
                location = excluded.location,
                location_short_name = excluded.location_short_name,
                location_type = excluded.location_type,
                location_type_id = excluded.location_type_id,
                exam_student_count = excluded.exam_student_count,
                seat_count = excluded.seat_count,
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
                item.academic_year,
                item.academic_year_name,
                item.academic_term,
                item.academic_term_name,
                item.exam_batch_id,
                item.exam_name,
                item.exam_paper_id,
                item.exam_paper_code,
                item.invigilation_key,
                item.invigilation_role,
                item.invigilation_teachers,
                item.course_code,
                item.course_name,
                item.course_display_name,
                item.teaching_class_name,
                item.class_composition,
                item.student_college,
                item.course_college,
                item.campus,
                item.building,
                item.location,
                item.location_short_name,
                item.location_type,
                item.location_type_id,
                int(item.exam_student_count or 0),
                int(item.seat_count or 0),
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
            FROM teacher_academic_invigilation_items
            WHERE teacher_id = ?
              AND school_code = 'gxufl'
              AND academic_year = ?
              AND academic_term = ?
              AND invigilation_key = ?
            LIMIT 1
            """,
            (int(teacher_id), item.academic_year, item.academic_term, item.invigilation_key),
        ).fetchone()
        if item_row is None:
            continue
        event_created, event_changed, _ = _upsert_calendar_event(
            conn,
            teacher_id=teacher_id,
            semester=semester,
            item_id=int(item_row["id"]),
            item=item,
            synced_at=synced_at,
        )
        event_created_count += 1 if event_created else 0
        event_updated_count += 1 if event_changed else 0
        notification_count += _maybe_create_invigilation_notification(
            conn,
            teacher_id=teacher_id,
            item=item,
            is_created=event_created,
            is_changed=event_changed,
        )

    stale_count = _mark_stale_items_for_terms(
        conn,
        teacher_id=teacher_id,
        semester_id=int(semester["id"]),
        term_params_list=term_params_list,
        seen_keys=seen_keys,
        synced_at=synced_at,
    )
    return {
        "created_count": created_count,
        "updated_count": updated_count,
        "invigilation_count": len(items),
        "event_created_count": event_created_count,
        "event_updated_count": event_updated_count,
        "notification_count": notification_count,
        "stale_count": stale_count,
    }


async def sync_current_teacher_invigilations_from_academic_system(teacher_id: int) -> dict[str, Any]:
    with get_db_connection() as conn:
        access_payload = load_teacher_academic_access_method(conn, teacher_id, school_code="gxufl")
        semester = _load_current_semester(conn, teacher_id, china_now().date())

    if not access_payload:
        return {
            "status": "missing_credential",
            "message": "请先在系统设置中配置并验证教务系统账号，再同步监考安排。",
        }

    if not semester:
        semester_result = await prepare_current_semester_from_academic_system(teacher_id)
        if semester_result.get("status") != "success":
            return {
                "status": "no_current_semester",
                "message": semester_result.get("message") or "未能从教务系统识别当前学期，暂不能同步监考安排。",
                "source_summary": semester_result.get("source_summary") or [],
            }
        with get_db_connection() as conn:
            semester = _load_semester_by_id(conn, teacher_id, int(semester_result["semester_id"]))

    if not semester:
        return {
            "status": "no_current_semester",
            "message": "请先新建或从教务系统同步当前学期，再同步监考安排。",
        }

    try:
        async with open_authenticated_academic_client(access_payload) as (client, profile, login_result):
            items, source_summary, term_params_list = await _fetch_teacher_invigilations(client, semester)
    except (ValueError, httpx.HTTPError) as exc:
        return {
            "status": "academic_login_failed",
            "message": f"教务系统登录或监考信息访问失败：{str(exc)[:180]}",
        }

    synced_at = _now_iso()
    with get_db_connection() as conn:
        try:
            result = _persist_invigilations(
                conn,
                teacher_id=teacher_id,
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
        f"已从教务系统同步 {result['invigilation_count']} 条监考安排，"
        f"新增 {result['created_count']} 条、更新 {result['updated_count']} 条，"
        f"写入教师日历 {result['event_created_count'] + result['event_updated_count']} 条，"
        f"标记待复核 {result['stale_count']} 条。"
    )
    if not items:
        message = "已连接教务系统并完成监考同步检查，当前学期暂未查询到监考安排。"

    return {
        "status": "success",
        "message": message,
        "semester_id": int(semester["id"]),
        "semester_name": str(semester.get("name") or ""),
        "synced_at": synced_at,
        "invigilation_count": result["invigilation_count"],
        "created_count": result["created_count"],
        "updated_count": result["updated_count"],
        "event_created_count": result["event_created_count"],
        "event_updated_count": result["event_updated_count"],
        "notification_count": result["notification_count"],
        "stale_count": result["stale_count"],
        "follow_up_items": FOLLOW_UP_ITEMS,
        "source_summary": source_summary,
        "login_display_name": login_result.get("display_name") if isinstance(login_result, dict) else "",
        "school_name": profile.school_name,
    }
