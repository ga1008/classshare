from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..database import get_db_connection
from .academic_calendar_sync_service import prepare_current_semester_from_academic_system
from .academic_integration_service import (
    load_teacher_academic_access_method,
    open_authenticated_academic_client,
)
from .academic_service import china_now, parse_date_input


ACADEMIC_CLASSROOM_SOURCE = "gxufl_jwxt"
ZF_TEACHING_PLACE_INDEX_PATH = "/pkgl/jxcdjbxxgl_cxJxcdjbxxIndex.html?gnmkdm=N211015&layout=default"
ZF_TEACHING_PLACE_QUERY_PATH = "/pkgl/jxcdjbxxgl_cxJxcdjbxxIndex.html?doType=query&gnmkdm=N211015"
ZF_FREE_ROOM_INDEX_PATH = "/cdjy/cdjy_cxKxcdlb.html?gnmkdm=N2155&layout=default"
ZF_FREE_ROOM_QUERY_PATH = "/cdjy/cdjy_cxKxcdlb.html?doType=query&gnmkdm=N2155"
ZF_FREE_ROOM_WEEK_SECTION_PATH = "/cdjy/cdjy_cxXqjc.html?gnmkdm=N2155"

TEACHING_PLACE_PAGE_SIZE = 500
FREE_ROOM_PAGE_SIZE = 100

DEFAULT_CAMPUSES = [
    {"id": "1", "name": "五合校区"},
    {"id": "3", "name": "空港校区"},
    {"id": "2", "name": "本部（虚拟）"},
]

DEFAULT_ROOM_TYPES = [
    {"id": "", "name": "全部类别"},
    {"id": "05", "name": "多媒体教室"},
    {"id": "03", "name": "实验室"},
    {"id": "13", "name": "画室"},
    {"id": "14", "name": "校内实训基地"},
    {"id": "20", "name": "体育教学场地"},
    {"id": "4505E6FE1B39775FE0630100007FF674", "name": "会议室"},
    {"id": "15", "name": "校外实训基地"},
]


class AcademicSessionRedirectError(RuntimeError):
    """Raised when JWXT redirects a read-only AJAX request back to login."""


@dataclass
class AcademicTeachingPlace:
    place_key: str
    place_id: str = ""
    room_code: str = ""
    room_name: str = ""
    room_full_name: str = ""
    campus_id: str = ""
    campus_name: str = ""
    building_id: str = ""
    building_name: str = ""
    floor_name: str = ""
    room_type_id: str = ""
    room_type_name: str = ""
    room_subtype_id: str = ""
    room_subtype_name: str = ""
    organization_id: str = ""
    organization_name: str = ""
    manager_name: str = ""
    usage_department: str = ""
    usage_class: str = ""
    borrow_type: str = ""
    seat_count: int = 0
    scheduling_seat_count: int = 0
    exam_seat_count: int = 0
    building_area: str = ""
    is_schedulable: bool = False
    is_borrowable: bool = False
    is_exam_schedulable: bool = False
    conflict_ignored: bool = False
    status_text: str = ""
    note: str = ""
    raw_json: dict[str, Any] = field(default_factory=dict)


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


def _truthy_text(value: Any) -> bool:
    text = _normalize_space(value).lower()
    return text in {"1", "true", "yes", "y", "on", "是", "可用"}


def _ajax_headers(
    client: httpx.AsyncClient,
    *,
    referer_path: str,
    accept: str = "application/json,text/javascript,*/*;q=0.8",
) -> dict[str, str]:
    base_url = str(client.base_url).rstrip("/")
    return {
        "Accept": accept,
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": base_url,
        "Referer": base_url + referer_path,
    }


def _is_login_redirect(response: httpx.Response) -> bool:
    if response.status_code not in {301, 302, 303, 307, 308}:
        return False
    location = str(response.headers.get("location") or "").lower()
    return "login_slogin" in location or "login_init" in location or "kickout" in location


def _jqgrid_form(
    *,
    page: int,
    show_count: int,
    extra: dict[str, Any],
    sort_name: str = "cdbh",
    time_flag: int = 1,
) -> dict[str, Any]:
    return {
        **extra,
        "_search": "false",
        "nd": str(int(time.time() * 1000)),
        "queryModel.showCount": str(max(1, int(show_count or 1))),
        "queryModel.currentPage": str(max(1, int(page or 1))),
        "queryModel.sortName": sort_name,
        "queryModel.sortOrder": "asc",
        "time": str(time_flag),
    }


def _extract_items(payload: Any) -> tuple[list[dict[str, Any]], int, int]:
    if not isinstance(payload, dict):
        return [], 0, 0
    items = payload.get("items")
    rows = [dict(item) for item in items if isinstance(item, dict)] if isinstance(items, list) else []
    total_count = _parse_int(payload.get("totalCount") or payload.get("totalResult") or len(rows))
    total_page = _parse_int(payload.get("totalPage"))
    if rows and total_page <= 0:
        total_page = 1
    return rows, total_count, max(total_page, 0)


async def _fetch_json(
    client: httpx.AsyncClient,
    path: str,
    data: dict[str, Any],
    *,
    referer_path: str,
) -> Any:
    response = await client.post(path, data=data, headers=_ajax_headers(client, referer_path=referer_path))
    if _is_login_redirect(response):
        raise AcademicSessionRedirectError("教务系统会话被重置，请稍后重试。")
    response.raise_for_status()
    try:
        return response.json()
    except (ValueError, json.JSONDecodeError):
        return None


async def _get_json(
    client: httpx.AsyncClient,
    path: str,
    params: dict[str, Any],
    *,
    referer_path: str,
) -> Any:
    response = await client.get(
        path,
        params=params,
        headers=_ajax_headers(client, referer_path=referer_path),
    )
    if _is_login_redirect(response):
        raise AcademicSessionRedirectError("教务系统会话被重置，请稍后重试。")
    response.raise_for_status()
    try:
        return response.json()
    except (ValueError, json.JSONDecodeError):
        return None


def _teaching_place_form(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "xqh_id": "",
        "cdlb_id": "",
        "cdejlb_id": "",
        "cdkyzt": "",
        "cdkyzt_yjs": "",
        "sfkjy": "",
        "sfkpk": "",
        "cdmc": "",
        "lh": "",
        "jg_id": "",
        "sfxncd": "0",
        "sydx_id_cx": "",
        "zysx": "",
        "sflb": "",
        "hbsl": "",
        "bbsl": "",
        "sfyzz": "",
        "sfjtjs": "",
        "tymbsl": "",
        "hlctpk": "",
        "yqdm": "",
        "sfbhkc": "",
        "sfykt": "",
        "cd_id": "",
        "zwzt": "",
        "minzws": "",
        "maxzws": "",
    }
    if extra:
        data.update(extra)
    return data


def _place_from_row(row: dict[str, Any]) -> AcademicTeachingPlace | None:
    place_id = _normalize_space(row.get("cd_id"))
    room_code = _normalize_space(row.get("cdbh"))
    room_name = _normalize_space(row.get("cdmc"))
    if not (place_id or room_code or room_name):
        return None
    building_name = _normalize_space(row.get("jxlmc"))
    if building_name and room_name and building_name not in room_name:
        room_full_name = f"{building_name} {room_name}"
    else:
        room_full_name = room_name or room_code
    seat_count = _parse_int(row.get("zws") or row.get("sjzws"))
    scheduling_seat_count = _parse_int(row.get("sjzws") or row.get("zws"))
    return AcademicTeachingPlace(
        place_key=place_id or room_code or room_name,
        place_id=place_id,
        room_code=room_code,
        room_name=room_name,
        room_full_name=room_full_name,
        campus_id=_normalize_space(row.get("xqh_id")),
        campus_name=_normalize_space(row.get("xqmc")),
        building_id=_normalize_space(row.get("lh")),
        building_name=building_name,
        floor_name=_normalize_space(row.get("lch")),
        room_type_id=_normalize_space(row.get("cdlb_id")),
        room_type_name=_normalize_space(row.get("cdlbmc")),
        room_subtype_id=_normalize_space(row.get("cdejlb_id")),
        room_subtype_name=_normalize_space(row.get("cdejlbmc")),
        organization_id=_normalize_space(row.get("jg_id")),
        organization_name=_normalize_space(row.get("jgmc")),
        manager_name=_normalize_space(row.get("cdgly")),
        usage_department=_normalize_space(row.get("sydxmc")),
        usage_class=_normalize_space(row.get("sybj")),
        borrow_type=_normalize_space(row.get("cdjylx")),
        seat_count=seat_count,
        scheduling_seat_count=scheduling_seat_count,
        exam_seat_count=_parse_int(row.get("kszws1") or row.get("kszws")),
        building_area=_normalize_space(row.get("jzmj")),
        is_schedulable=_truthy_text(row.get("sfkpke") or row.get("sfkpk")),
        is_borrowable=_truthy_text(row.get("sfkjy")),
        is_exam_schedulable=_truthy_text(row.get("sfkpk")),
        conflict_ignored=_truthy_text(row.get("hlctpk")),
        status_text=_normalize_space(row.get("zwzt")),
        note=_normalize_space(row.get("bz")),
        raw_json=dict(row),
    )


def _serialize_place_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    for key in (
        "seat_count",
        "scheduling_seat_count",
        "exam_seat_count",
        "is_schedulable",
        "is_borrowable",
        "is_exam_schedulable",
        "conflict_ignored",
    ):
        item[key] = int(item.get(key) or 0)
    item["is_schedulable"] = bool(item["is_schedulable"])
    item["is_borrowable"] = bool(item["is_borrowable"])
    item["is_exam_schedulable"] = bool(item["is_exam_schedulable"])
    item["conflict_ignored"] = bool(item["conflict_ignored"])
    item["display_name"] = item.get("room_full_name") or item.get("room_name") or item.get("room_code") or ""
    return item


def _load_current_semester(conn: sqlite3.Connection, teacher_id: int) -> dict[str, Any] | None:
    today = china_now().date().isoformat()
    row = conn.execute(
        """
        SELECT *
        FROM academic_semesters
        WHERE teacher_id = ?
          AND start_date <= ?
          AND end_date >= ?
        ORDER BY start_date DESC, id DESC
        LIMIT 1
        """,
        (int(teacher_id), today, today),
    ).fetchone()
    if row is None:
        row = conn.execute(
            """
            SELECT *
            FROM academic_semesters
            WHERE teacher_id = ?
            ORDER BY end_date DESC, start_date DESC, id DESC
            LIMIT 1
            """,
            (int(teacher_id),),
        ).fetchone()
    return dict(row) if row is not None else None


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
    if re.search(r"(第\s*2|第二|二)\s*学期", name):
        return 2
    if re.search(r"(第\s*1|第一|一)\s*学期", name):
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
    return [{"xnm": xnm, "xqm": xqm} for xnm in year_values for xqm in term_values]


async def _resolve_term_params(teacher_id: int, requested: dict[str, Any]) -> tuple[dict[str, str], dict[str, Any] | None]:
    xnm = _normalize_space(requested.get("xnm"))
    xqm = _normalize_space(requested.get("xqm"))
    if xnm and xqm:
        return {"xnm": xnm, "xqm": xqm}, None

    semester_id = _parse_int(requested.get("semester_id"))
    with get_db_connection() as conn:
        semester = None
        if semester_id:
            row = conn.execute(
                "SELECT * FROM academic_semesters WHERE id = ? AND teacher_id = ? LIMIT 1",
                (semester_id, int(teacher_id)),
            ).fetchone()
            semester = dict(row) if row is not None else None
        if semester is None:
            semester = _load_current_semester(conn, int(teacher_id))

    if semester is None:
        prepared = await prepare_current_semester_from_academic_system(int(teacher_id))
        if prepared.get("status") == "success":
            with get_db_connection() as conn:
                semester = _load_current_semester(conn, int(teacher_id))

    if semester:
        return _term_param_candidates(semester)[0], semester

    today = china_now().date()
    year_start = today.year if today.month >= 8 else today.year - 1
    return {"xnm": str(year_start), "xqm": "12" if 2 <= today.month <= 7 else "3"}, None


def _bitmap(values: Any, *, min_value: int, max_value: int) -> int:
    if isinstance(values, str):
        raw_values = re.findall(r"\d+", values)
    elif isinstance(values, (list, tuple, set)):
        raw_values = list(values)
    else:
        raw_values = [values]
    bitmap = 0
    for raw in raw_values:
        try:
            number = int(raw)
        except (TypeError, ValueError):
            continue
        if min_value <= number <= max_value:
            bitmap += 1 << (number - 1)
    return bitmap


def _normalize_weekdays(value: Any) -> str:
    if isinstance(value, str):
        raw_values = re.findall(r"\d+", value)
    elif isinstance(value, (list, tuple, set)):
        raw_values = list(value)
    else:
        raw_values = [value]
    weekdays: list[str] = []
    for raw in raw_values:
        try:
            number = int(raw)
        except (TypeError, ValueError):
            continue
        if 1 <= number <= 7 and str(number) not in weekdays:
            weekdays.append(str(number))
    return ",".join(weekdays)


def _free_room_form(filters: dict[str, Any], term_params: dict[str, str]) -> dict[str, Any]:
    weeks_value = filters.get("weeks")
    if weeks_value in (None, ""):
        weeks_value = filters.get("week")
    sections_value = filters.get("sections")
    if sections_value in (None, ""):
        sections_value = filters.get("section")
    zcd = _parse_int(filters.get("zcd")) or _bitmap(weeks_value, min_value=1, max_value=30)
    jcd = _parse_int(filters.get("jcd")) or _bitmap(sections_value, min_value=1, max_value=20)
    xqj = _normalize_weekdays(filters.get("xqj") or filters.get("weekday"))
    return {
        "xqh_id": _normalize_space(filters.get("xqh_id")) or "1",
        "xnm": term_params["xnm"],
        "xqm": term_params["xqm"],
        "cdlb_id": _normalize_space(filters.get("cdlb_id")) or "05",
        "cdejlb_id": _normalize_space(filters.get("cdejlb_id")),
        "qszws": _normalize_space(filters.get("qszws")),
        "jszws": _normalize_space(filters.get("jszws")),
        "cdmc": _normalize_space(filters.get("cdmc")),
        "cd_id": _normalize_space(filters.get("cd_id")),
        "lh": _normalize_space(filters.get("lh")),
        "jyfs": "0",
        "cdjylx": _normalize_space(filters.get("cdjylx")),
        "zysx": "",
        "sflb": "",
        "hbsl": "",
        "bbsl": "",
        "sfyzz": "",
        "sfjtjs": "",
        "tjsl": "",
        "tymbsl": "",
        "yczb": "",
        "zws": "",
        "sfbhkc": "",
        "kszws1": "",
        "zcd": str(zcd),
        "xqj": xqj,
        "jcd": str(jcd),
    }


def _normalize_option(value: Any, label: Any) -> dict[str, str] | None:
    option_id = _normalize_space(value)
    name = _normalize_space(label)
    if not (option_id or name):
        return None
    return {"id": option_id, "name": name or option_id}


def _query_options_from_places(conn: sqlite3.Connection, teacher_id: int) -> dict[str, list[dict[str, str]]]:
    rows = conn.execute(
        """
        SELECT campus_id, campus_name, building_id, building_name, room_type_id, room_type_name
        FROM teacher_academic_teaching_places
        WHERE teacher_id = ?
          AND source = ?
          AND sync_status = 'active'
        """,
        (int(teacher_id), ACADEMIC_CLASSROOM_SOURCE),
    ).fetchall()
    campuses: dict[str, dict[str, str]] = {item["id"]: dict(item) for item in DEFAULT_CAMPUSES}
    buildings: dict[str, dict[str, str]] = {"": {"id": "", "name": "全部楼号"}}
    room_types: dict[str, dict[str, str]] = {item["id"]: dict(item) for item in DEFAULT_ROOM_TYPES}
    for row in rows:
        campus = _normalize_option(row["campus_id"], row["campus_name"])
        building = _normalize_option(row["building_id"], row["building_name"])
        room_type = _normalize_option(row["room_type_id"], row["room_type_name"])
        if campus:
            campuses[campus["id"]] = campus
        if building:
            buildings[building["id"]] = building
        if room_type:
            room_types[room_type["id"]] = room_type
    return {
        "campuses": list(campuses.values()),
        "buildings": list(buildings.values()),
        "room_types": list(room_types.values()),
    }


async def sync_teaching_places_from_academic_system(teacher_id: int) -> dict[str, Any]:
    with get_db_connection() as conn:
        access_payload = load_teacher_academic_access_method(conn, int(teacher_id))
    if not access_payload:
        return {
            "status": "missing_credential",
            "message": "请先在系统设置中配置并验证教务系统账号。",
        }

    batch_id = uuid.uuid4().hex
    synced_at = _now_iso()
    source_summary: list[dict[str, Any]] = []
    places: list[AcademicTeachingPlace] = []

    async with open_authenticated_academic_client(access_payload) as (client, profile, _login_result):
        await client.get(
            ZF_TEACHING_PLACE_INDEX_PATH,
            headers=_ajax_headers(client, referer_path=ZF_TEACHING_PLACE_INDEX_PATH, accept="text/html,*/*;q=0.8"),
        )
        page = 1
        total_page = 1
        while page <= max(1, total_page):
            payload = await _fetch_json(
                client,
                ZF_TEACHING_PLACE_QUERY_PATH,
                _jqgrid_form(
                    page=page,
                    show_count=TEACHING_PLACE_PAGE_SIZE,
                    extra=_teaching_place_form(),
                ),
                referer_path=ZF_TEACHING_PLACE_INDEX_PATH,
            )
            rows, total_count, payload_total_page = _extract_items(payload)
            source_summary.append(
                {
                    "endpoint": ZF_TEACHING_PLACE_QUERY_PATH,
                    "page": page,
                    "rows": len(rows),
                    "total_count": total_count,
                    "total_page": payload_total_page,
                }
            )
            for row in rows:
                place = _place_from_row(row)
                if place:
                    places.append(place)
            if payload_total_page <= 0 or not rows:
                break
            total_page = min(payload_total_page, 50)
            page += 1

    existing_keys: set[str] = set()
    with get_db_connection() as conn:
        existing_keys = {
            str(row["place_key"])
            for row in conn.execute(
                """
                SELECT place_key
                FROM teacher_academic_teaching_places
                WHERE teacher_id = ? AND school_code = ? AND source = ?
                """,
                (int(teacher_id), access_payload.get("school_code") or "gxufl", ACADEMIC_CLASSROOM_SOURCE),
            ).fetchall()
        }
        conn.execute(
            """
            UPDATE teacher_academic_teaching_places
               SET sync_status = 'stale',
                   updated_at = ?
             WHERE teacher_id = ?
               AND school_code = ?
               AND source = ?
            """,
            (synced_at, int(teacher_id), access_payload.get("school_code") or "gxufl", ACADEMIC_CLASSROOM_SOURCE),
        )
        for place in places:
            conn.execute(
                """
                INSERT INTO teacher_academic_teaching_places (
                    teacher_id, school_code, source, place_key, place_id, room_code,
                    room_name, room_full_name, campus_id, campus_name, building_id,
                    building_name, floor_name, room_type_id, room_type_name,
                    room_subtype_id, room_subtype_name, organization_id, organization_name,
                    manager_name, usage_department, usage_class, borrow_type, seat_count,
                    scheduling_seat_count, exam_seat_count, building_area, is_schedulable,
                    is_borrowable, is_exam_schedulable, conflict_ignored, status_text,
                    note, raw_json, source_url, sync_status, sync_batch_id, synced_at,
                    updated_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, 'active', ?, ?,
                    ?
                )
                ON CONFLICT (teacher_id, school_code, source, place_key) DO UPDATE SET
                    place_id = excluded.place_id,
                    room_code = excluded.room_code,
                    room_name = excluded.room_name,
                    room_full_name = excluded.room_full_name,
                    campus_id = excluded.campus_id,
                    campus_name = excluded.campus_name,
                    building_id = excluded.building_id,
                    building_name = excluded.building_name,
                    floor_name = excluded.floor_name,
                    room_type_id = excluded.room_type_id,
                    room_type_name = excluded.room_type_name,
                    room_subtype_id = excluded.room_subtype_id,
                    room_subtype_name = excluded.room_subtype_name,
                    organization_id = excluded.organization_id,
                    organization_name = excluded.organization_name,
                    manager_name = excluded.manager_name,
                    usage_department = excluded.usage_department,
                    usage_class = excluded.usage_class,
                    borrow_type = excluded.borrow_type,
                    seat_count = excluded.seat_count,
                    scheduling_seat_count = excluded.scheduling_seat_count,
                    exam_seat_count = excluded.exam_seat_count,
                    building_area = excluded.building_area,
                    is_schedulable = excluded.is_schedulable,
                    is_borrowable = excluded.is_borrowable,
                    is_exam_schedulable = excluded.is_exam_schedulable,
                    conflict_ignored = excluded.conflict_ignored,
                    status_text = excluded.status_text,
                    note = excluded.note,
                    raw_json = excluded.raw_json,
                    source_url = excluded.source_url,
                    sync_status = 'active',
                    sync_batch_id = excluded.sync_batch_id,
                    synced_at = excluded.synced_at,
                    updated_at = excluded.updated_at
                """,
                (
                    int(teacher_id),
                    access_payload.get("school_code") or "gxufl",
                    ACADEMIC_CLASSROOM_SOURCE,
                    place.place_key,
                    place.place_id,
                    place.room_code,
                    place.room_name,
                    place.room_full_name,
                    place.campus_id,
                    place.campus_name,
                    place.building_id,
                    place.building_name,
                    place.floor_name,
                    place.room_type_id,
                    place.room_type_name,
                    place.room_subtype_id,
                    place.room_subtype_name,
                    place.organization_id,
                    place.organization_name,
                    place.manager_name,
                    place.usage_department,
                    place.usage_class,
                    place.borrow_type,
                    int(place.seat_count),
                    int(place.scheduling_seat_count),
                    int(place.exam_seat_count),
                    place.building_area,
                    1 if place.is_schedulable else 0,
                    1 if place.is_borrowable else 0,
                    1 if place.is_exam_schedulable else 0,
                    1 if place.conflict_ignored else 0,
                    place.status_text,
                    place.note,
                    _json_dumps(place.raw_json),
                    str(profile.base_url).rstrip("/") + ZF_TEACHING_PLACE_INDEX_PATH,
                    batch_id,
                    synced_at,
                    synced_at,
                ),
            )
        stale_count = _parse_int(
            conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM teacher_academic_teaching_places
                WHERE teacher_id = ?
                  AND school_code = ?
                  AND source = ?
                  AND sync_status = 'stale'
                """,
                (int(teacher_id), access_payload.get("school_code") or "gxufl", ACADEMIC_CLASSROOM_SOURCE),
            ).fetchone()["count"]
        )
        conn.commit()

    created_count = sum(1 for place in places if place.place_key not in existing_keys)
    updated_count = max(0, len(places) - created_count)
    return {
        "status": "success",
        "message": f"已从教务系统同步 {len(places)} 个教学场地。",
        "place_count": len(places),
        "created_count": created_count,
        "updated_count": updated_count,
        "stale_count": stale_count,
        "synced_at": synced_at,
        "source_summary": source_summary,
    }


def load_teacher_teaching_places(
    conn: sqlite3.Connection,
    teacher_id: int,
    *,
    search: str = "",
    campus_id: str = "",
    building_id: str = "",
    room_type_id: str = "",
    availability: str = "",
    include_stale: bool = False,
    limit: int = 600,
) -> list[dict[str, Any]]:
    where = ["teacher_id = ?", "source = ?"]
    params: list[Any] = [int(teacher_id), ACADEMIC_CLASSROOM_SOURCE]
    if not include_stale:
        where.append("sync_status = 'active'")
    if campus_id:
        where.append("campus_id = ?")
        params.append(campus_id)
    if building_id:
        where.append("building_id = ?")
        params.append(building_id)
    if room_type_id:
        where.append("room_type_id = ?")
        params.append(room_type_id)
    if availability == "schedulable":
        where.append("is_schedulable = 1")
    elif availability == "borrowable":
        where.append("is_borrowable = 1")
    elif availability == "exam":
        where.append("is_exam_schedulable = 1")
    if search:
        like = f"%{search}%"
        where.append(
            """
            (
                room_code LIKE ?
                OR room_name LIKE ?
                OR room_full_name LIKE ?
                OR campus_name LIKE ?
                OR building_name LIKE ?
                OR room_type_name LIKE ?
                OR organization_name LIKE ?
            )
            """
        )
        params.extend([like] * 7)
    params.append(max(1, min(int(limit or 600), 1200)))
    rows = conn.execute(
        f"""
        SELECT *
        FROM teacher_academic_teaching_places
        WHERE {' AND '.join(where)}
        ORDER BY campus_name, building_name, room_code, room_name
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [_serialize_place_row(row) for row in rows]


def load_teacher_teaching_place_dashboard(conn: sqlite3.Connection, teacher_id: int) -> dict[str, Any]:
    stats = conn.execute(
        """
        SELECT COUNT(*) AS total_count,
               SUM(CASE WHEN sync_status = 'active' THEN 1 ELSE 0 END) AS active_count,
               SUM(CASE WHEN sync_status = 'stale' THEN 1 ELSE 0 END) AS stale_count,
               SUM(CASE WHEN is_schedulable = 1 AND sync_status = 'active' THEN 1 ELSE 0 END) AS schedulable_count,
               SUM(CASE WHEN is_borrowable = 1 AND sync_status = 'active' THEN 1 ELSE 0 END) AS borrowable_count,
               SUM(CASE WHEN is_exam_schedulable = 1 AND sync_status = 'active' THEN 1 ELSE 0 END) AS exam_count,
               MAX(synced_at) AS last_synced_at
        FROM teacher_academic_teaching_places
        WHERE teacher_id = ?
          AND source = ?
        """,
        (int(teacher_id), ACADEMIC_CLASSROOM_SOURCE),
    ).fetchone()
    options = _query_options_from_places(conn, teacher_id)
    return {
        "total_count": int(stats["total_count"] or 0) if stats else 0,
        "active_count": int(stats["active_count"] or 0) if stats else 0,
        "stale_count": int(stats["stale_count"] or 0) if stats else 0,
        "schedulable_count": int(stats["schedulable_count"] or 0) if stats else 0,
        "borrowable_count": int(stats["borrowable_count"] or 0) if stats else 0,
        "exam_count": int(stats["exam_count"] or 0) if stats else 0,
        "last_synced_at": str(stats["last_synced_at"] or "") if stats else "",
        "options": options,
    }


async def load_free_classroom_options_from_academic_system(
    teacher_id: int,
    *,
    xnm: str = "",
    xqm: str = "",
    semester_id: Any = None,
    xqh_id: str = "1",
) -> dict[str, Any]:
    with get_db_connection() as conn:
        access_payload = load_teacher_academic_access_method(conn, int(teacher_id))
        local_options = _query_options_from_places(conn, int(teacher_id))
    if not access_payload:
        return {
            "status": "missing_credential",
            "message": "请先在系统设置中配置并验证教务系统账号。",
            "options": local_options,
        }

    term_params, semester = await _resolve_term_params(
        int(teacher_id),
        {"xnm": xnm, "xqm": xqm, "semester_id": semester_id},
    )
    campus_id = _normalize_space(xqh_id) or "1"
    try:
        async with open_authenticated_academic_client(access_payload) as (client, _profile, _login_result):
            await client.get(
                ZF_FREE_ROOM_INDEX_PATH,
                headers=_ajax_headers(client, referer_path=ZF_FREE_ROOM_INDEX_PATH, accept="text/html,*/*;q=0.8"),
            )
            payload = await _get_json(
                client,
                ZF_FREE_ROOM_WEEK_SECTION_PATH,
                {"xqh_id": campus_id, "xnm": term_params["xnm"], "xqm": term_params["xqm"]},
                referer_path=ZF_FREE_ROOM_INDEX_PATH,
            )
    except AcademicSessionRedirectError:
        return {
            "status": "academic_session_expired",
            "message": "教务系统会话被重置，请稍后重新加载教室选项。",
            "term": term_params,
            "semester_id": semester.get("id") if semester else None,
            "semester_name": str(semester.get("name") or "") if semester else "",
            "options": {
                "campuses": local_options.get("campuses") or DEFAULT_CAMPUSES,
                "buildings": local_options.get("buildings") or [{"id": "", "name": "全部楼号"}],
                "room_types": local_options.get("room_types") or DEFAULT_ROOM_TYPES,
                "sections": [],
            },
        }
    except httpx.HTTPError as exc:
        return {
            "status": "academic_unavailable",
            "message": f"教务系统教室选项读取失败：{str(exc)[:160]}",
            "term": term_params,
            "semester_id": semester.get("id") if semester else None,
            "semester_name": str(semester.get("name") or "") if semester else "",
            "options": {
                "campuses": local_options.get("campuses") or DEFAULT_CAMPUSES,
                "buildings": local_options.get("buildings") or [{"id": "", "name": "全部楼号"}],
                "room_types": local_options.get("room_types") or DEFAULT_ROOM_TYPES,
                "sections": [],
            },
        }
    buildings = [{"id": "", "name": "全部楼号"}]
    sections = []
    if isinstance(payload, dict):
        for item in payload.get("lhList") or []:
            if isinstance(item, dict):
                option = _normalize_option(item.get("JXLDM"), item.get("JXLMC"))
                if option:
                    buildings.append(option)
        for item in payload.get("jcList") or []:
            if isinstance(item, dict):
                section_number = _parse_int(item.get("JCMC"))
                if section_number:
                    sections.append(
                        {
                            "id": str(section_number),
                            "name": f"第 {section_number} 节",
                            "period": _normalize_space(item.get("RSDMC")),
                            "time": _normalize_space(item.get("SJD")),
                        }
                    )
    if len(buildings) == 1:
        buildings = local_options.get("buildings") or buildings
    return {
        "status": "success",
        "term": term_params,
        "semester_id": semester.get("id") if semester else None,
        "semester_name": str(semester.get("name") or "") if semester else "",
        "options": {
            "campuses": local_options.get("campuses") or DEFAULT_CAMPUSES,
            "buildings": buildings,
            "room_types": local_options.get("room_types") or DEFAULT_ROOM_TYPES,
            "sections": sections,
        },
    }


async def query_free_classrooms_from_academic_system(
    teacher_id: int,
    filters: dict[str, Any],
) -> dict[str, Any]:
    with get_db_connection() as conn:
        access_payload = load_teacher_academic_access_method(conn, int(teacher_id))
    if not access_payload:
        return {
            "status": "missing_credential",
            "message": "请先在系统设置中配置并验证教务系统账号。",
        }

    term_params, semester = await _resolve_term_params(int(teacher_id), filters)
    base_form = _free_room_form(filters, term_params)
    if _parse_int(base_form.get("zcd")) <= 0:
        return {"status": "invalid", "message": "请选择要查询的周次。"}
    if not base_form.get("xqj"):
        return {"status": "invalid", "message": "请选择星期。"}
    if _parse_int(base_form.get("jcd")) <= 0:
        return {"status": "invalid", "message": "请选择节次。"}
    page = max(1, _parse_int(filters.get("page")) or 1)
    page_size = max(1, min(_parse_int(filters.get("page_size")) or FREE_ROOM_PAGE_SIZE, 200))
    profile = None
    payload = None
    last_session_error: AcademicSessionRedirectError | None = None
    for attempt in range(2):
        try:
            async with open_authenticated_academic_client(access_payload) as (client, profile, _login_result):
                await client.get(
                    ZF_FREE_ROOM_INDEX_PATH,
                    headers=_ajax_headers(client, referer_path=ZF_FREE_ROOM_INDEX_PATH, accept="text/html,*/*;q=0.8"),
                )
                payload = await _fetch_json(
                    client,
                    ZF_FREE_ROOM_QUERY_PATH,
                    _jqgrid_form(page=page, show_count=page_size, extra=base_form),
                    referer_path=ZF_FREE_ROOM_INDEX_PATH,
                )
            break
        except AcademicSessionRedirectError as exc:
            last_session_error = exc
            if attempt == 0:
                await asyncio.sleep(0.45)
                continue
            return {
                "status": "academic_session_expired",
                "message": str(last_session_error),
                "items": [],
                "total_count": 0,
                "total_page": 0,
                "page": page,
                "page_size": page_size,
                "term": term_params,
                "semester_id": semester.get("id") if semester else None,
                "semester_name": str(semester.get("name") or "") if semester else "",
            }
        except httpx.HTTPError as exc:
            return {
                "status": "academic_unavailable",
                "message": f"教务系统空闲教室查询失败：{str(exc)[:160]}",
                "items": [],
                "total_count": 0,
                "total_page": 0,
                "page": page,
                "page_size": page_size,
                "term": term_params,
                "semester_id": semester.get("id") if semester else None,
                "semester_name": str(semester.get("name") or "") if semester else "",
            }
    rows, total_count, total_page = _extract_items(payload)
    items = []
    for row in rows:
        place = _place_from_row(row)
        if place:
            item = place.__dict__.copy()
            item["available"] = True
            item["raw_json"] = place.raw_json
            items.append(item)
    return {
        "status": "success",
        "message": f"实时查询到 {total_count} 个空闲场地。",
        "items": items,
        "total_count": total_count,
        "total_page": total_page,
        "page": page,
        "page_size": page_size,
        "term": term_params,
        "semester_id": semester.get("id") if semester else None,
        "semester_name": str(semester.get("name") or "") if semester else "",
        "query": {
            "campus_id": base_form["xqh_id"],
            "building_id": base_form["lh"],
            "room_type_id": base_form["cdlb_id"],
            "room_name": base_form["cdmc"],
            "week_bitmap": base_form["zcd"],
            "weekday": base_form["xqj"],
            "section_bitmap": base_form["jcd"],
        },
        "source_summary": [
            {
                "endpoint": str(profile.base_url).rstrip("/") + ZF_FREE_ROOM_QUERY_PATH,
                "method": "POST",
                "readonly": True,
                "contract": "ZFSoft jqGrid free-room query",
            }
        ],
    }
