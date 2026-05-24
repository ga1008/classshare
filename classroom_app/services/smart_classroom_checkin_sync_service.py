from __future__ import annotations

import asyncio
import json
import random
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from ..database import get_db_connection
from .smart_classroom_integration_service import (
    load_teacher_smart_classroom_access_method,
    open_authenticated_smart_classroom_client,
)


SMART_PLATFORM_CODE = "gxufl_smart_classroom"
CHECKIN_SCHEDULE_LIST_PATH = "/teaching/checkinCourse/teacherScheduleList"
CHECKIN_PAGE_PATH = "/teaching/checkinCourse/page"
CHECKIN_RECORD_PATH = "/teaching/checkinCourse/checkinRecord"

STATUS_LABELS = {
    "CHECKED": "出勤",
    "UNCHECKED": "缺勤",
    "SICK_LEAVE": "病假",
    "PERSONAL_LEAVE": "事假",
    "LATE_OR_EARLY": "迟到或早退",
}

_teacher_sync_locks: dict[int, asyncio.Lock] = {}


@dataclass
class OfferingCandidate:
    id: int
    class_id: int
    class_name: str
    course_name: str
    course_code: str
    academic_teaching_class_name: str
    semester_name: str
    semester_text: str
    semester_start_date: str
    semester_end_date: str
    session_course_codes: set[str]


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _safe_json_loads(raw_value: Any, fallback: Any) -> Any:
    if raw_value in (None, ""):
        return fallback
    try:
        parsed = json.loads(str(raw_value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u3000", " ")).strip()


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _remote_weekday_to_local(value: Any) -> int | None:
    numeric = _coerce_int(value, -1)
    if 1 <= numeric <= 7:
        return numeric - 1
    if 0 <= numeric <= 6:
        return numeric
    return None


def _parse_remote_datetime(value: Any) -> tuple[str, str]:
    raw = _clean_text(value)
    if not raw:
        return "", ""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.date().isoformat(), dt.isoformat(timespec="seconds")
        except ValueError:
            continue
    match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", raw)
    if not match:
        return "", raw
    date_text = f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    return date_text, raw


def _extract_section_index(record: dict[str, Any]) -> int:
    for key in ("section", "sections", "sectionIndex", "djj"):
        value = record.get(key)
        if isinstance(value, str) and "," in value:
            return _coerce_int(value.split(",", 1)[0])
        numeric = _coerce_int(value)
        if numeric > 0:
            return numeric
    return 0


def _section_text_contains(section_text: Any, section_index: int) -> bool:
    if section_index <= 0:
        return False
    text = str(section_text or "")
    if not text:
        return False
    numbers = [int(item) for item in re.findall(r"\d+", text)]
    if not numbers:
        return False
    if len(numbers) >= 2:
        start, end = min(numbers[0], numbers[1]), max(numbers[0], numbers[1])
        if start <= section_index <= end:
            return True
    return section_index in numbers


def _term_matches(candidate: OfferingCandidate, year: str, term: str) -> bool:
    haystack = " ".join(
        item
        for item in [candidate.semester_name, candidate.semester_text]
        if item
    )
    normalized_haystack = _normalize_text(haystack)
    year_ok = not year or _normalize_text(year) in normalized_haystack
    term_text = str(term or "").strip()
    if not term_text:
        return year_ok
    term_markers = {
        "1": ("1", "第一", "一"),
        "2": ("2", "第二", "二"),
        "12": ("2", "第二", "二"),
    }.get(term_text, (term_text,))
    term_ok = any(_normalize_text(marker) in normalized_haystack for marker in term_markers)
    return year_ok and term_ok


def _load_offering_candidates(conn, teacher_id: int) -> list[OfferingCandidate]:
    rows = conn.execute(
        """
        SELECT o.id,
               o.class_id,
               cl.name AS class_name,
               co.name AS course_name,
               COALESCE(co.academic_course_code, '') AS course_code,
               COALESCE(o.academic_teaching_class_name, '') AS academic_teaching_class_name,
               COALESCE(s.name, '') AS semester_name,
               COALESCE(o.semester, '') AS semester_text,
               COALESCE(s.start_date, '') AS semester_start_date,
               COALESCE(s.end_date, '') AS semester_end_date,
               (
                   SELECT GROUP_CONCAT(DISTINCT COALESCE(ss.academic_course_code, ''))
                   FROM class_offering_sessions ss
                   WHERE ss.class_offering_id = o.id
                     AND COALESCE(ss.academic_course_code, '') <> ''
               ) AS session_course_codes
        FROM class_offerings o
        JOIN courses co ON co.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        LEFT JOIN academic_semesters s ON s.id = o.semester_id
        WHERE o.teacher_id = ?
        ORDER BY COALESCE(s.start_date, o.created_at) DESC, o.id DESC
        """,
        (teacher_id,),
    ).fetchall()
    candidates: list[OfferingCandidate] = []
    for row in rows:
        session_codes = {
            item.strip()
            for item in str(row["session_course_codes"] or "").split(",")
            if item and item.strip()
        }
        candidates.append(
            OfferingCandidate(
                id=int(row["id"]),
                class_id=int(row["class_id"]),
                class_name=str(row["class_name"] or ""),
                course_name=str(row["course_name"] or ""),
                course_code=str(row["course_code"] or ""),
                academic_teaching_class_name=str(row["academic_teaching_class_name"] or ""),
                semester_name=str(row["semester_name"] or ""),
                semester_text=str(row["semester_text"] or ""),
                semester_start_date=str(row["semester_start_date"] or ""),
                semester_end_date=str(row["semester_end_date"] or ""),
                session_course_codes=session_codes,
            )
        )
    return candidates


def _match_offering(schedule: dict[str, Any], candidates: list[OfferingCandidate]) -> tuple[OfferingCandidate | None, str]:
    remote_course_id = _clean_text(schedule.get("courseId") or schedule.get("courseNo") or schedule.get("no"))
    remote_course_name = _clean_text(schedule.get("course") or schedule.get("name"))
    remote_teaching_class = _clean_text(
        schedule.get("claName")
        or schedule.get("chooseCourseNo")
        or schedule.get("fullTitle")
        or schedule.get("fullTitle2")
    )
    remote_year = _clean_text(schedule.get("year"))
    remote_term = _clean_text(schedule.get("semester"))

    scored: list[tuple[int, OfferingCandidate, list[str]]] = []
    for candidate in candidates:
        score = 0
        reasons: list[str] = []
        code_pool = {candidate.course_code, *candidate.session_course_codes}
        if remote_course_id and remote_course_id in code_pool:
            score += 5
            reasons.append("课程编号一致")
        if remote_course_name and _normalize_text(remote_course_name) == _normalize_text(candidate.course_name):
            score += 3
            reasons.append("课程名称一致")
        elif remote_course_name and _normalize_text(remote_course_name) in _normalize_text(candidate.course_name):
            score += 2
            reasons.append("课程名称相近")

        candidate_teaching_class = _clean_text(candidate.academic_teaching_class_name)
        if remote_teaching_class and candidate_teaching_class:
            remote_norm = _normalize_text(remote_teaching_class)
            candidate_norm = _normalize_text(candidate_teaching_class)
            if remote_norm == candidate_norm:
                score += 5
                reasons.append("教学班一致")
            elif remote_norm in candidate_norm or candidate_norm in remote_norm:
                score += 4
                reasons.append("教学班相近")
        if remote_teaching_class and _normalize_text(candidate.class_name) in _normalize_text(remote_teaching_class):
            score += 1
            reasons.append("行政班可关联")
        if _term_matches(candidate, remote_year, remote_term):
            score += 2
            reasons.append("学期一致")
        scored.append((score, candidate, reasons))

    scored.sort(key=lambda item: (item[0], item[1].id), reverse=True)
    if not scored or scored[0][0] < 6:
        return None, "未能按课程编号、教学班和学期匹配到本系统课堂。"
    best_score, best_candidate, reasons = scored[0]
    return best_candidate, f"匹配分 {best_score}：{'、'.join(reasons)}"


async def _gentle_pause() -> None:
    await asyncio.sleep(random.uniform(0.22, 0.62))


async def _post_json(client: httpx.AsyncClient, path: str, data: dict[str, Any] | None = None) -> Any:
    response = await client.post(path, data=data or {})
    response.raise_for_status()
    return response.json()


async def _fetch_schedule_list(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    payload = await _post_json(client, CHECKIN_SCHEDULE_LIST_PATH)
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("list", "data", "rows"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


async def _fetch_checkin_pages(client: httpx.AsyncClient, schedule_id: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    page = 1
    total_page = 1
    while page <= total_page and page <= 30:
        await _gentle_pause()
        payload = await _post_json(
            client,
            CHECKIN_PAGE_PATH,
            {
                "page": page,
                "pageSize": 100,
                "teacherScheduleId": schedule_id,
                "field": "id",
                "order": "descend",
            },
        )
        if isinstance(payload, dict):
            page_records = payload.get("list") if isinstance(payload.get("list"), list) else []
            total_page = max(1, _coerce_int(payload.get("totalPage"), 1))
        elif isinstance(payload, list):
            page_records = payload
            total_page = 1
        else:
            page_records = []
            total_page = 1
        records.extend(item for item in page_records if isinstance(item, dict))
        page += 1
    return records


async def _fetch_checkin_detail(client: httpx.AsyncClient, checkin_id: Any) -> dict[str, Any]:
    await _gentle_pause()
    payload = await _post_json(client, CHECKIN_RECORD_PATH, {"id": checkin_id})
    return payload if isinstance(payload, dict) else {}


def _upsert_schedule_item(
    conn,
    *,
    teacher_id: int,
    schedule: dict[str, Any],
    offering: OfferingCandidate | None,
    match_message: str,
    synced_at: str,
) -> int:
    remote_schedule_id = str(schedule.get("id") or schedule.get("kbId") or "").strip()
    remote_teaching_class = _clean_text(
        schedule.get("claName")
        or schedule.get("chooseCourseNo")
        or schedule.get("fullTitle")
        or schedule.get("fullTitle2")
    )
    conn.execute(
        """
        INSERT INTO smart_classroom_schedule_items (
            teacher_id, class_offering_id, platform_code, remote_schedule_id,
            remote_course_id, remote_course_name, remote_teaching_class_id,
            remote_teaching_class_name, academic_year, academic_term, weeks_text,
            sections_text, weekday, classroom_name, student_count, match_status,
            match_message, metadata_json, synced_at, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(teacher_id, platform_code, remote_schedule_id) DO UPDATE SET
            class_offering_id = excluded.class_offering_id,
            remote_course_id = excluded.remote_course_id,
            remote_course_name = excluded.remote_course_name,
            remote_teaching_class_id = excluded.remote_teaching_class_id,
            remote_teaching_class_name = excluded.remote_teaching_class_name,
            academic_year = excluded.academic_year,
            academic_term = excluded.academic_term,
            weeks_text = excluded.weeks_text,
            sections_text = excluded.sections_text,
            weekday = excluded.weekday,
            classroom_name = excluded.classroom_name,
            student_count = excluded.student_count,
            match_status = excluded.match_status,
            match_message = excluded.match_message,
            metadata_json = excluded.metadata_json,
            synced_at = excluded.synced_at,
            updated_at = excluded.updated_at
        """,
        (
            teacher_id,
            offering.id if offering else None,
            SMART_PLATFORM_CODE,
            remote_schedule_id,
            _clean_text(schedule.get("courseId") or schedule.get("courseNo") or schedule.get("no")),
            _clean_text(schedule.get("course") or schedule.get("name")),
            _clean_text(schedule.get("claId")),
            remote_teaching_class,
            _clean_text(schedule.get("year")),
            _clean_text(schedule.get("semester")),
            _clean_text(schedule.get("week") or schedule.get("week2") or schedule.get("period")),
            _clean_text(schedule.get("sections")),
            _remote_weekday_to_local(schedule.get("xqj") or schedule.get("dayOfWeek")),
            _clean_text(schedule.get("classRoomName")),
            _coerce_int(schedule.get("stuNo")),
            "matched" if offering else "unmatched",
            match_message,
            _json_dumps(schedule),
            synced_at,
            synced_at,
            synced_at,
        ),
    )
    row = conn.execute(
        """
        SELECT id
        FROM smart_classroom_schedule_items
        WHERE teacher_id = ? AND platform_code = ? AND remote_schedule_id = ?
        LIMIT 1
        """,
        (teacher_id, SMART_PLATFORM_CODE, remote_schedule_id),
    ).fetchone()
    return int(row["id"]) if row else 0


def _load_sessions_for_offering(conn, class_offering_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM class_offering_sessions
        WHERE class_offering_id = ?
        ORDER BY session_date, order_index
        """,
        (class_offering_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _match_session(
    sessions: list[dict[str, Any]],
    record: dict[str, Any],
    detail: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    checkin_course = detail.get("checkinCourse") if isinstance(detail.get("checkinCourse"), dict) else {}
    merged = {**record, **checkin_course}
    date_text, _dt = _parse_remote_datetime(
        merged.get("createTime")
        or merged.get("checkinTime")
        or merged.get("stopTime")
        or record.get("createTime")
    )
    if date_text:
        same_date = [session for session in sessions if str(session.get("session_date") or "") == date_text]
        if len(same_date) == 1:
            return same_date[0], "按点名日期对齐课次。"
        if same_date:
            section_index = _extract_section_index(merged)
            for session in same_date:
                if _section_text_contains(session.get("academic_section_text"), section_index):
                    return session, "按点名日期和节次对齐课次。"
            return same_date[0], "按点名日期对齐课次，存在同日多课请复核。"

    week_index = _coerce_int(merged.get("week"))
    weekday = _remote_weekday_to_local(merged.get("dayOfWeek") or merged.get("xqj"))
    section_index = _extract_section_index(merged)
    candidates = []
    for session in sessions:
        if week_index and _coerce_int(session.get("week_index")) != week_index:
            continue
        if weekday is not None and _coerce_int(session.get("weekday"), -1) != weekday:
            continue
        if section_index and not _section_text_contains(session.get("academic_section_text"), section_index):
            continue
        candidates.append(session)
    if len(candidates) == 1:
        return candidates[0], "按周次、星期和节次对齐课次。"
    if candidates:
        return candidates[0], "按周次和星期对齐课次，存在多个候选请复核。"
    return None, "未能对齐到本系统课次，请检查该周是否调课或课次尚未生成。"


def _status_label(status: Any) -> str:
    normalized = str(status or "").strip().upper()
    return STATUS_LABELS.get(normalized, str(status or "").strip() or "未知")


def _remote_checkin_payload(record: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any]:
    checkin_course = detail.get("checkinCourse") if isinstance(detail.get("checkinCourse"), dict) else {}
    merged = {**record, **checkin_course}
    date_text, dt_text = _parse_remote_datetime(
        merged.get("createTime")
        or merged.get("checkinTime")
        or merged.get("stopTime")
        or record.get("createTime")
    )
    _stop_date, stop_dt = _parse_remote_datetime(merged.get("stopTime"))
    return {
        "merged": merged,
        "date_text": date_text,
        "checkin_time": dt_text,
        "stop_time": stop_dt,
        "section_index": _extract_section_index(merged),
        "weekday": _remote_weekday_to_local(merged.get("dayOfWeek") or merged.get("xqj")),
        "week_index": _coerce_int(merged.get("week")),
    }


def _upsert_checkin_session(
    conn,
    *,
    teacher_id: int,
    offering: OfferingCandidate | None,
    session: dict[str, Any] | None,
    schedule_item_id: int,
    schedule: dict[str, Any],
    record: dict[str, Any],
    detail: dict[str, Any],
    match_message: str,
    synced_at: str,
) -> int:
    counts = detail.get("statusCounts") if isinstance(detail.get("statusCounts"), dict) else {}
    stu_list = detail.get("stuList") if isinstance(detail.get("stuList"), list) else []
    payload = _remote_checkin_payload(record, detail)
    merged = payload["merged"]
    remote_checkin_id = str(record.get("id") or merged.get("id") or "").strip()
    remote_schedule_id = str(schedule.get("id") or merged.get("teacherScheduleId") or "").strip()
    total_count = len(stu_list) or sum(_coerce_int(value) for value in counts.values())
    conn.execute(
        """
        INSERT INTO smart_classroom_checkin_sessions (
            teacher_id, class_offering_id, session_id, schedule_item_id, platform_code,
            remote_checkin_id, remote_schedule_id, course_code, course_name,
            teaching_class_name, academic_year, academic_term, week_index, weekday,
            section_index, checkin_time, stop_time, method, checked_rate,
            checked_count, unchecked_count, sick_leave_count, personal_leave_count,
            late_or_early_count, total_count, match_status, match_message,
            metadata_json, synced_at, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(teacher_id, platform_code, remote_checkin_id) DO UPDATE SET
            class_offering_id = excluded.class_offering_id,
            session_id = excluded.session_id,
            schedule_item_id = excluded.schedule_item_id,
            remote_schedule_id = excluded.remote_schedule_id,
            course_code = excluded.course_code,
            course_name = excluded.course_name,
            teaching_class_name = excluded.teaching_class_name,
            academic_year = excluded.academic_year,
            academic_term = excluded.academic_term,
            week_index = excluded.week_index,
            weekday = excluded.weekday,
            section_index = excluded.section_index,
            checkin_time = excluded.checkin_time,
            stop_time = excluded.stop_time,
            method = excluded.method,
            checked_rate = excluded.checked_rate,
            checked_count = excluded.checked_count,
            unchecked_count = excluded.unchecked_count,
            sick_leave_count = excluded.sick_leave_count,
            personal_leave_count = excluded.personal_leave_count,
            late_or_early_count = excluded.late_or_early_count,
            total_count = excluded.total_count,
            match_status = excluded.match_status,
            match_message = excluded.match_message,
            metadata_json = excluded.metadata_json,
            synced_at = excluded.synced_at,
            updated_at = excluded.updated_at
        """,
        (
            teacher_id,
            offering.id if offering else None,
            int(session["id"]) if session else None,
            schedule_item_id or None,
            SMART_PLATFORM_CODE,
            remote_checkin_id,
            remote_schedule_id,
            _clean_text(merged.get("courseId") or schedule.get("courseId")),
            _clean_text(merged.get("course") or schedule.get("course")),
            _clean_text(merged.get("claName") or schedule.get("claName") or schedule.get("chooseCourseNo")),
            _clean_text(merged.get("year") or schedule.get("year")),
            _clean_text(merged.get("semester") or schedule.get("semester")),
            payload["week_index"],
            payload["weekday"],
            payload["section_index"],
            payload["checkin_time"],
            payload["stop_time"],
            _clean_text(merged.get("method")),
            _clean_text(merged.get("checkedRate")),
            _coerce_int(counts.get("checked")),
            _coerce_int(counts.get("unchecked")),
            _coerce_int(counts.get("sickLeave")),
            _coerce_int(counts.get("personalLeave")),
            _coerce_int(counts.get("lateOrEarly")),
            total_count,
            "matched" if session else ("offering_matched" if offering else "unmatched"),
            match_message,
            _json_dumps({"record": record, "detail": detail}),
            synced_at,
            synced_at,
            synced_at,
        ),
    )
    row = conn.execute(
        """
        SELECT id
        FROM smart_classroom_checkin_sessions
        WHERE teacher_id = ? AND platform_code = ? AND remote_checkin_id = ?
        LIMIT 1
        """,
        (teacher_id, SMART_PLATFORM_CODE, remote_checkin_id),
    ).fetchone()
    return int(row["id"]) if row else 0


def _student_match_map(conn, class_offering_id: int | None) -> dict[str, dict[str, Any]]:
    if not class_offering_id:
        return {}
    rows = conn.execute(
        """
        SELECT s.id, s.student_id_number, s.name
        FROM students s
        JOIN class_offerings o ON o.class_id = s.class_id
        WHERE o.id = ?
          AND COALESCE(s.enrollment_status, 'active') = 'active'
        """,
        (int(class_offering_id),),
    ).fetchall()
    return {str(row["student_id_number"] or "").strip(): dict(row) for row in rows}


def _upsert_checkin_students(
    conn,
    *,
    checkin_session_id: int,
    teacher_id: int,
    class_offering_id: int | None,
    session_id: int | None,
    student_rows: list[Any],
    synced_at: str,
) -> int:
    local_students = _student_match_map(conn, class_offering_id)
    seen_numbers: set[str] = set()
    saved_count = 0
    for index, raw_item in enumerate(student_rows, start=1):
        if not isinstance(raw_item, dict):
            continue
        student_number = str(
            raw_item.get("no")
            or raw_item.get("studentNo")
            or raw_item.get("studentNumber")
            or raw_item.get("username")
            or ""
        ).strip()
        if not student_number:
            student_number = f"unknown-{index}"
        student_name = _clean_text(raw_item.get("name") or raw_item.get("realName") or "")
        status = str(raw_item.get("status") or "").strip().upper()
        local_student = local_students.get(student_number)
        seen_numbers.add(student_number)
        conn.execute(
            """
            INSERT INTO smart_classroom_checkin_students (
                checkin_session_id, teacher_id, class_offering_id, session_id,
                student_id, student_number, student_name, status, status_label,
                local_match_status, metadata_json, synced_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(checkin_session_id, student_number) DO UPDATE SET
                class_offering_id = excluded.class_offering_id,
                session_id = excluded.session_id,
                student_id = excluded.student_id,
                student_name = excluded.student_name,
                status = excluded.status,
                status_label = excluded.status_label,
                local_match_status = excluded.local_match_status,
                metadata_json = excluded.metadata_json,
                synced_at = excluded.synced_at,
                updated_at = excluded.updated_at
            """,
            (
                checkin_session_id,
                teacher_id,
                class_offering_id,
                session_id,
                int(local_student["id"]) if local_student else None,
                student_number,
                student_name,
                status,
                _status_label(status),
                "matched" if local_student else "remote_only",
                _json_dumps(raw_item),
                synced_at,
                synced_at,
                synced_at,
            ),
        )
        saved_count += 1
    if seen_numbers:
        placeholders = ",".join("?" for _ in seen_numbers)
        conn.execute(
            f"""
            DELETE FROM smart_classroom_checkin_students
            WHERE checkin_session_id = ?
              AND student_number NOT IN ({placeholders})
            """,
            (checkin_session_id, *sorted(seen_numbers)),
        )
    return saved_count


async def sync_teacher_smart_classroom_checkins(
    teacher_id: int,
    *,
    class_offering_id: int | None = None,
    session_id: int | None = None,
) -> dict[str, Any]:
    lock = _teacher_sync_locks.setdefault(int(teacher_id), asyncio.Lock())
    async with lock:
        with get_db_connection() as conn:
            access_payload = load_teacher_smart_classroom_access_method(conn, int(teacher_id))
            candidates = _load_offering_candidates(conn, int(teacher_id))
            target_session_row = None
            if session_id and class_offering_id:
                target_session_row = conn.execute(
                    """
                    SELECT *
                    FROM class_offering_sessions
                    WHERE id = ? AND class_offering_id = ?
                    LIMIT 1
                    """,
                    (int(session_id), int(class_offering_id)),
                ).fetchone()

        if not access_payload:
            return {
                "status": "missing_credential",
                "message": "请先在系统设置中配置并验证智慧课堂账号。",
                "counts": {},
                "warnings": [],
            }

        if session_id and class_offering_id and target_session_row is None:
            return {
                "status": "failed",
                "message": "目标课次不存在，无法同步智慧课堂点名。",
                "counts": {},
                "warnings": [],
            }

        schedules: list[dict[str, Any]] = []
        schedule_records: list[dict[str, Any]] = []
        warnings: list[str] = []
        try:
            async with open_authenticated_smart_classroom_client(access_payload) as (client, _profile, _login_result):
                schedules = await _fetch_schedule_list(client)
                for schedule in schedules:
                    remote_schedule_id = str(schedule.get("id") or schedule.get("kbId") or "").strip()
                    if not remote_schedule_id:
                        continue
                    offering, match_message = _match_offering(schedule, candidates)
                    if class_offering_id and (not offering or offering.id != int(class_offering_id)):
                        continue
                    records = await _fetch_checkin_pages(client, remote_schedule_id)
                    for record in records:
                        remote_checkin_id = record.get("id")
                        if not remote_checkin_id:
                            continue
                        detail = await _fetch_checkin_detail(client, remote_checkin_id)
                        schedule_records.append(
                            {
                                "schedule": schedule,
                                "offering": offering,
                                "offering_match_message": match_message,
                                "record": record,
                                "detail": detail,
                            }
                        )
        except (httpx.HTTPError, ValueError) as exc:
            return {
                "status": "failed",
                "message": f"智慧课堂点名同步失败：{str(exc)[:180]}",
                "counts": {},
                "warnings": [str(exc)[:180]],
            }

        synced_at = _now_iso()
        counts = {
            "schedule_count": len(schedules),
            "matched_schedule_count": 0,
            "checkin_count": 0,
            "student_count": 0,
            "matched_session_count": 0,
            "unmatched_session_count": 0,
        }

        with get_db_connection() as conn:
            session_cache: dict[int, list[dict[str, Any]]] = {}
            for schedule in schedules:
                offering, match_message = _match_offering(schedule, candidates)
                if class_offering_id and (not offering or offering.id != int(class_offering_id)):
                    continue
                if offering:
                    counts["matched_schedule_count"] += 1
                _upsert_schedule_item(
                    conn,
                    teacher_id=int(teacher_id),
                    schedule=schedule,
                    offering=offering,
                    match_message=match_message,
                    synced_at=synced_at,
                )

            for item in schedule_records:
                schedule = item["schedule"]
                offering: OfferingCandidate | None = item["offering"]
                record = item["record"]
                detail = item["detail"]
                schedule_item_id = _upsert_schedule_item(
                    conn,
                    teacher_id=int(teacher_id),
                    schedule=schedule,
                    offering=offering,
                    match_message=item["offering_match_message"],
                    synced_at=synced_at,
                )
                matched_session = None
                session_match_message = item["offering_match_message"]
                if offering:
                    if offering.id not in session_cache:
                        session_cache[offering.id] = _load_sessions_for_offering(conn, offering.id)
                    matched_session, session_match_message = _match_session(
                        session_cache[offering.id],
                        record,
                        detail,
                    )
                    if session_id and (not matched_session or int(matched_session["id"]) != int(session_id)):
                        continue
                elif class_offering_id:
                    continue

                if matched_session:
                    counts["matched_session_count"] += 1
                else:
                    counts["unmatched_session_count"] += 1
                    warnings.append(session_match_message)

                checkin_session_id = _upsert_checkin_session(
                    conn,
                    teacher_id=int(teacher_id),
                    offering=offering,
                    session=matched_session,
                    schedule_item_id=schedule_item_id,
                    schedule=schedule,
                    record=record,
                    detail=detail,
                    match_message=session_match_message,
                    synced_at=synced_at,
                )
                student_rows = detail.get("stuList") if isinstance(detail.get("stuList"), list) else []
                counts["student_count"] += _upsert_checkin_students(
                    conn,
                    checkin_session_id=checkin_session_id,
                    teacher_id=int(teacher_id),
                    class_offering_id=offering.id if offering else None,
                    session_id=int(matched_session["id"]) if matched_session else None,
                    student_rows=student_rows,
                    synced_at=synced_at,
                )
                counts["checkin_count"] += 1
            conn.commit()

        message = (
            f"已从智慧课堂同步 {counts['checkin_count']} 条点名记录、{counts['student_count']} 条学生签到状态。"
            if counts["checkin_count"]
            else "智慧课堂暂未返回可对齐的点名记录。"
        )
        status = "success" if counts["checkin_count"] else "empty"
        if counts["unmatched_session_count"]:
            status = "partial_success" if counts["checkin_count"] else "empty"
        return {
            "status": status,
            "message": message,
            "counts": counts,
            "warnings": list(dict.fromkeys(warnings))[:8],
            "synced_at": synced_at,
        }


def _serialize_checkin_row(row: Any) -> dict[str, Any]:
    row_dict = dict(row)
    return {
        "id": int(row_dict["id"]),
        "remote_checkin_id": str(row_dict.get("remote_checkin_id") or ""),
        "course_code": str(row_dict.get("course_code") or ""),
        "course_name": str(row_dict.get("course_name") or ""),
        "teaching_class_name": str(row_dict.get("teaching_class_name") or ""),
        "academic_year": str(row_dict.get("academic_year") or ""),
        "academic_term": str(row_dict.get("academic_term") or ""),
        "week_index": int(row_dict.get("week_index") or 0),
        "weekday": row_dict.get("weekday"),
        "section_index": int(row_dict.get("section_index") or 0),
        "checkin_time": str(row_dict.get("checkin_time") or ""),
        "stop_time": str(row_dict.get("stop_time") or ""),
        "method": str(row_dict.get("method") or ""),
        "checked_rate": str(row_dict.get("checked_rate") or ""),
        "checked_count": int(row_dict.get("checked_count") or 0),
        "unchecked_count": int(row_dict.get("unchecked_count") or 0),
        "sick_leave_count": int(row_dict.get("sick_leave_count") or 0),
        "personal_leave_count": int(row_dict.get("personal_leave_count") or 0),
        "late_or_early_count": int(row_dict.get("late_or_early_count") or 0),
        "total_count": int(row_dict.get("total_count") or 0),
        "match_status": str(row_dict.get("match_status") or ""),
        "match_message": str(row_dict.get("match_message") or ""),
        "synced_at": str(row_dict.get("synced_at") or ""),
    }


def _serialize_student_row(row: Any) -> dict[str, Any]:
    row_dict = dict(row)
    return {
        "id": int(row_dict["id"]),
        "student_id": row_dict.get("student_id"),
        "student_number": str(row_dict.get("student_number") or ""),
        "student_name": str(row_dict.get("student_name") or ""),
        "status": str(row_dict.get("status") or ""),
        "status_label": str(row_dict.get("status_label") or ""),
        "local_match_status": str(row_dict.get("local_match_status") or ""),
        "synced_at": str(row_dict.get("synced_at") or ""),
    }


def load_session_smart_checkin_summary(
    conn,
    *,
    teacher_id: int,
    class_offering_id: int,
    session_id: int,
) -> dict[str, Any]:
    records = conn.execute(
        """
        SELECT *
        FROM smart_classroom_checkin_sessions
        WHERE teacher_id = ?
          AND class_offering_id = ?
          AND session_id = ?
        ORDER BY checkin_time DESC, synced_at DESC, id DESC
        """,
        (int(teacher_id), int(class_offering_id), int(session_id)),
    ).fetchall()
    serialized_records = [_serialize_checkin_row(row) for row in records]
    if not serialized_records:
        return {
            "status": "empty",
            "message": "本次课还没有从智慧课堂导入点名记录。",
            "record": None,
            "records": [],
            "students": [],
            "summary": {
                "checked": 0,
                "unchecked": 0,
                "sick_leave": 0,
                "personal_leave": 0,
                "late_or_early": 0,
                "total": 0,
            },
        }

    selected = serialized_records[0]
    student_rows = conn.execute(
        """
        SELECT *
        FROM smart_classroom_checkin_students
        WHERE checkin_session_id = ?
        ORDER BY
            CASE status
                WHEN 'UNCHECKED' THEN 0
                WHEN 'LATE_OR_EARLY' THEN 1
                WHEN 'PERSONAL_LEAVE' THEN 2
                WHEN 'SICK_LEAVE' THEN 3
                ELSE 4
            END,
            student_number,
            id
        """,
        (selected["id"],),
    ).fetchall()
    students = [_serialize_student_row(row) for row in student_rows]
    return {
        "status": "success",
        "message": "已读取本次课智慧课堂点名记录。",
        "record": selected,
        "records": serialized_records,
        "students": students,
        "summary": {
            "checked": selected["checked_count"],
            "unchecked": selected["unchecked_count"],
            "sick_leave": selected["sick_leave_count"],
            "personal_leave": selected["personal_leave_count"],
            "late_or_early": selected["late_or_early_count"],
            "total": selected["total_count"],
        },
    }


async def sync_teacher_smart_classroom_data_after_credential_verified(teacher_id: int) -> dict[str, Any]:
    result = await sync_teacher_smart_classroom_checkins(int(teacher_id))
    stage = {
        "key": "checkins",
        "label": "点名记录",
        "status": result.get("status") or "unknown",
        "message": result.get("message") or "",
        "counts": result.get("counts") or {},
        "warnings": result.get("warnings") or [],
    }
    if result.get("status") in {"success", "partial_success", "empty"}:
        return {
            "status": result.get("status"),
            "message": result.get("message") or "智慧课堂账号已验证并保存，系统已自动同步点名记录。",
            "stages": [stage],
        }
    return {
        "status": "failed",
        "message": "智慧课堂账号已验证并保存，但点名记录自动同步未完成；可以稍后在本页面手动同步。",
        "stages": [stage],
    }
