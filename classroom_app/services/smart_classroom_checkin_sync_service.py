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
CHECKED_STATUS = "CHECKED"
ABSENT_STATUS = "UNCHECKED"
LEAVE_STATUSES = {"SICK_LEAVE", "PERSONAL_LEAVE"}
ATTENDANCE_ABNORMAL_STATUSES = {ABSENT_STATUS, "LATE_OR_EARLY", *LEAVE_STATUSES}

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


def _rate_percent(numerator: int | float, denominator: int | float, *, digits: int = 1) -> float:
    try:
        denominator_value = float(denominator)
        if denominator_value <= 0:
            return 0.0
        return round(float(numerator) * 100 / denominator_value, digits)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def _status_bucket(status: Any) -> str:
    normalized = str(status or "").strip().upper()
    if normalized == CHECKED_STATUS:
        return "checked"
    if normalized == ABSENT_STATUS:
        return "absent"
    if normalized == "LATE_OR_EARLY":
        return "late_or_early"
    if normalized == "SICK_LEAVE":
        return "sick_leave"
    if normalized == "PERSONAL_LEAVE":
        return "personal_leave"
    return "unknown"


def _empty_student_attendance(
    *,
    student_id: int | None,
    student_number: str,
    student_name: str,
) -> dict[str, Any]:
    return {
        "student_id": student_id,
        "student_number": str(student_number or ""),
        "student_name": str(student_name or ""),
        "checked": 0,
        "absent": 0,
        "late_or_early": 0,
        "sick_leave": 0,
        "personal_leave": 0,
        "unknown": 0,
        "total": 0,
        "attendance_rate": 0.0,
        "abnormal_count": 0,
        "risk_score": 0.0,
        "risk_level": "none",
        "latest_status": "",
        "latest_status_label": "",
        "latest_checkin_time": "",
        "local_match_status": "matched" if student_id else "remote_only",
    }


def _finalize_student_attendance(item: dict[str, Any]) -> dict[str, Any]:
    total = int(item.get("total") or 0)
    abnormal_count = (
        int(item.get("absent") or 0)
        + int(item.get("late_or_early") or 0)
        + int(item.get("sick_leave") or 0)
        + int(item.get("personal_leave") or 0)
    )
    risk_score = (
        int(item.get("absent") or 0) * 2.2
        + int(item.get("late_or_early") or 0) * 1.2
        + (int(item.get("sick_leave") or 0) + int(item.get("personal_leave") or 0)) * 0.7
    )
    attendance_rate = _rate_percent(int(item.get("checked") or 0), total)
    if total <= 0:
        risk_level = "none"
    elif attendance_rate < 70 or int(item.get("absent") or 0) >= 3:
        risk_level = "high"
    elif attendance_rate < 85 or int(item.get("absent") or 0) >= 2 or int(item.get("late_or_early") or 0) >= 2:
        risk_level = "medium"
    elif abnormal_count:
        risk_level = "watch"
    else:
        risk_level = "healthy"

    item["attendance_rate"] = attendance_rate
    item["abnormal_count"] = abnormal_count
    item["risk_score"] = round(risk_score, 2)
    item["risk_level"] = risk_level
    return item


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


def _load_latest_matched_checkin_sessions(
    conn,
    *,
    class_offering_id: int,
    teacher_id: int | None = None,
) -> tuple[list[dict[str, Any]], int]:
    params: list[Any] = [int(class_offering_id)]
    teacher_filter = ""
    if teacher_id is not None:
        teacher_filter = "AND teacher_id = ?"
        params.append(int(teacher_id))
    rows = conn.execute(
        f"""
        SELECT *
        FROM smart_classroom_checkin_sessions
        WHERE class_offering_id = ?
          AND session_id IS NOT NULL
          {teacher_filter}
        ORDER BY session_id,
                 COALESCE(checkin_time, '') DESC,
                 COALESCE(synced_at, '') DESC,
                 id DESC
        """,
        params,
    ).fetchall()
    latest_by_session: dict[int, dict[str, Any]] = {}
    for row in rows:
        session_id = _coerce_int(row["session_id"])
        if session_id <= 0 or session_id in latest_by_session:
            continue
        latest_by_session[session_id] = dict(row)

    unmatched_row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM smart_classroom_checkin_sessions
        WHERE class_offering_id = ?
          AND session_id IS NULL
        """,
        (int(class_offering_id),),
    ).fetchone()
    return list(latest_by_session.values()), int((unmatched_row["count"] if unmatched_row else 0) or 0)


def _load_offering_summary_row(conn, class_offering_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT o.id,
               o.teacher_id,
               o.class_id,
               COALESCE(s.name, o.semester, '') AS semester_name,
               c.name AS course_name,
               COALESCE(c.academic_course_code, '') AS course_code,
               cl.name AS class_name,
               t.name AS teacher_name
        FROM class_offerings o
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        JOIN teachers t ON t.id = o.teacher_id
        LEFT JOIN academic_semesters s ON s.id = o.semester_id
        WHERE o.id = ?
        LIMIT 1
        """,
        (int(class_offering_id),),
    ).fetchone()
    return dict(row) if row else {}


def _build_session_chart_items(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in sorted(
        rows,
        key=lambda item: (
            _coerce_int(item.get("week_index")),
            _coerce_int(item.get("weekday"), -1),
            _coerce_int(item.get("section_index")),
            str(item.get("checkin_time") or ""),
        ),
    ):
        total = _coerce_int(row.get("total_count"))
        checked = _coerce_int(row.get("checked_count"))
        abnormal = (
            _coerce_int(row.get("unchecked_count"))
            + _coerce_int(row.get("sick_leave_count"))
            + _coerce_int(row.get("personal_leave_count"))
            + _coerce_int(row.get("late_or_early_count"))
        )
        items.append(
            {
                "id": int(row.get("id") or 0),
                "session_id": row.get("session_id"),
                "week_index": _coerce_int(row.get("week_index")),
                "weekday": row.get("weekday"),
                "section_index": _coerce_int(row.get("section_index")),
                "label": f"第{_coerce_int(row.get('week_index')) or '?'}周",
                "checkin_time": str(row.get("checkin_time") or ""),
                "rate": _rate_percent(checked, total),
                "checked": checked,
                "abnormal": abnormal,
                "total": total,
            }
        )
    return items


def _build_weekly_trend(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[int, dict[str, int]] = {}
    for row in rows:
        week_index = _coerce_int(row.get("week_index"))
        if week_index <= 0:
            week_index = 0
        item = grouped.setdefault(
            week_index,
            {"week_index": week_index, "checked": 0, "total": 0, "session_count": 0},
        )
        item["checked"] += _coerce_int(row.get("checked_count"))
        item["total"] += _coerce_int(row.get("total_count"))
        item["session_count"] += 1
    return [
        {
            **item,
            "label": "未标周次" if item["week_index"] == 0 else f"第{item['week_index']}周",
            "rate": _rate_percent(item["checked"], item["total"]),
        }
        for item in sorted(grouped.values(), key=lambda value: value["week_index"])
    ]


def _build_course_comparisons(
    conn,
    *,
    teacher_id: int,
    current_class_offering_id: int,
    current_rate: float,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT scs.*,
               c.name AS course_name,
               cl.name AS class_name
        FROM smart_classroom_checkin_sessions scs
        JOIN class_offerings o ON o.id = scs.class_offering_id
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        WHERE scs.teacher_id = ?
          AND scs.class_offering_id IS NOT NULL
          AND scs.session_id IS NOT NULL
        ORDER BY scs.class_offering_id,
                 scs.session_id,
                 COALESCE(scs.checkin_time, '') DESC,
                 COALESCE(scs.synced_at, '') DESC,
                 scs.id DESC
        """,
        (int(teacher_id),),
    ).fetchall()
    latest_keys: set[tuple[int, int]] = set()
    grouped: dict[int, dict[str, Any]] = {}
    for row in rows:
        offering_id = _coerce_int(row["class_offering_id"])
        session_id = _coerce_int(row["session_id"])
        if offering_id <= 0 or session_id <= 0:
            continue
        key = (offering_id, session_id)
        if key in latest_keys:
            continue
        latest_keys.add(key)
        item = grouped.setdefault(
            offering_id,
            {
                "class_offering_id": offering_id,
                "course_name": str(row["course_name"] or ""),
                "class_name": str(row["class_name"] or ""),
                "checked": 0,
                "total": 0,
                "session_count": 0,
            },
        )
        item["checked"] += _coerce_int(row["checked_count"])
        item["total"] += _coerce_int(row["total_count"])
        item["session_count"] += 1

    comparisons = []
    for item in grouped.values():
        total = int(item["total"] or 0)
        rate = _rate_percent(item["checked"], total)
        comparisons.append(
            {
                **item,
                "rate": rate,
                "delta_from_current": round(current_rate - rate, 1),
                "is_current": int(item["class_offering_id"]) == int(current_class_offering_id),
            }
        )
    comparisons.sort(key=lambda item: (not item["is_current"], -item["session_count"], item["course_name"]))
    return comparisons[:8]


def _load_local_student_attendance_base(conn, class_offering_id: int) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT s.id, s.student_id_number, s.name
        FROM students s
        JOIN class_offerings o ON o.class_id = s.class_id
        WHERE o.id = ?
          AND COALESCE(s.enrollment_status, 'active') = 'active'
        ORDER BY s.student_id_number, s.id
        """,
        (int(class_offering_id),),
    ).fetchall()
    return {
        f"id:{int(row['id'])}": _empty_student_attendance(
            student_id=int(row["id"]),
            student_number=str(row["student_id_number"] or ""),
            student_name=str(row["name"] or ""),
        )
        for row in rows
    }


def _build_student_attendance_rows(
    conn,
    *,
    class_offering_id: int,
    selected_sessions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    students = _load_local_student_attendance_base(conn, int(class_offering_id))
    if not selected_sessions:
        return [_finalize_student_attendance(item) for item in students.values()]

    session_ids = [int(row["id"]) for row in selected_sessions if int(row.get("id") or 0) > 0]
    if not session_ids:
        return [_finalize_student_attendance(item) for item in students.values()]
    placeholders = ",".join("?" for _ in session_ids)
    rows = conn.execute(
        f"""
        SELECT scstu.*,
               scs.checkin_time,
               scs.week_index
        FROM smart_classroom_checkin_students scstu
        JOIN smart_classroom_checkin_sessions scs ON scs.id = scstu.checkin_session_id
        WHERE scstu.checkin_session_id IN ({placeholders})
        ORDER BY COALESCE(scs.checkin_time, '') DESC,
                 scstu.student_number
        """,
        session_ids,
    ).fetchall()
    for row in rows:
        student_id = row["student_id"]
        key = f"id:{int(student_id)}" if student_id else f"number:{str(row['student_number'] or '').strip()}"
        item = students.get(key)
        if not item:
            item = _empty_student_attendance(
                student_id=int(student_id) if student_id else None,
                student_number=str(row["student_number"] or ""),
                student_name=str(row["student_name"] or ""),
            )
            item["local_match_status"] = str(row["local_match_status"] or "remote_only")
            students[key] = item
        bucket = _status_bucket(row["status"])
        item[bucket] = int(item.get(bucket) or 0) + 1
        item["total"] = int(item.get("total") or 0) + 1
        if not item.get("latest_status"):
            item["latest_status"] = str(row["status"] or "")
            item["latest_status_label"] = str(row["status_label"] or "")
            item["latest_checkin_time"] = str(row["checkin_time"] or "")

    result = [_finalize_student_attendance(item) for item in students.values()]
    result.sort(
        key=lambda item: (
            -float(item.get("risk_score") or 0),
            float(item.get("attendance_rate") or 0),
            str(item.get("student_number") or ""),
        )
    )
    return result


def _build_personal_course_comparisons(
    conn,
    *,
    student_id: int,
    student_number: str,
    current_class_offering_id: int,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT scstu.status,
               scs.id AS checkin_session_id,
               scs.session_id,
               scs.class_offering_id,
               scs.checkin_time,
               c.name AS course_name,
               cl.name AS class_name
        FROM smart_classroom_checkin_students scstu
        JOIN smart_classroom_checkin_sessions scs ON scs.id = scstu.checkin_session_id
        JOIN class_offerings o ON o.id = scs.class_offering_id
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        WHERE scs.class_offering_id IS NOT NULL
          AND scs.session_id IS NOT NULL
          AND (
              (scstu.student_id IS NOT NULL AND scstu.student_id = ?)
              OR (scstu.student_number = ?)
          )
        ORDER BY scs.class_offering_id,
                 scs.session_id,
                 COALESCE(scs.checkin_time, '') DESC,
                 scs.id DESC
        """,
        (int(student_id), str(student_number or "")),
    ).fetchall()
    seen: set[tuple[int, int]] = set()
    grouped: dict[int, dict[str, Any]] = {}
    for row in rows:
        offering_id = _coerce_int(row["class_offering_id"])
        session_id = _coerce_int(row["session_id"])
        if offering_id <= 0 or session_id <= 0:
            continue
        key = (offering_id, session_id)
        if key in seen:
            continue
        seen.add(key)
        item = grouped.setdefault(
            offering_id,
            {
                "class_offering_id": offering_id,
                "course_name": str(row["course_name"] or ""),
                "class_name": str(row["class_name"] or ""),
                "checked": 0,
                "absent": 0,
                "late_or_early": 0,
                "leave": 0,
                "total": 0,
                "is_current": offering_id == int(current_class_offering_id),
            },
        )
        bucket = _status_bucket(row["status"])
        if bucket == "checked":
            item["checked"] += 1
        elif bucket == "absent":
            item["absent"] += 1
        elif bucket == "late_or_early":
            item["late_or_early"] += 1
        elif bucket in {"sick_leave", "personal_leave"}:
            item["leave"] += 1
        item["total"] += 1
    result = []
    for item in grouped.values():
        item["rate"] = _rate_percent(item["checked"], item["total"])
        item["abnormal_count"] = item["absent"] + item["late_or_early"] + item["leave"]
        result.append(item)
    result.sort(key=lambda item: (not item["is_current"], -item["total"], item["course_name"]))
    return result[:8]


def _build_attendance_insights(
    *,
    summary: dict[str, Any],
    weekly_trend: list[dict[str, Any]],
    students: list[dict[str, Any]],
    comparisons: list[dict[str, Any]],
) -> list[dict[str, str]]:
    insights: list[dict[str, str]] = []
    coverage_rate = float(summary.get("coverage_rate") or 0)
    class_rate = float(summary.get("attendance_rate") or 0)
    if coverage_rate and coverage_rate < 75:
        insights.append(
            {
                "tone": "warning",
                "title": "点名覆盖不足",
                "text": f"本课堂已对齐 {coverage_rate:.1f}% 的课次点名，统计可作为趋势参考，但还不适合作为最终考勤结论。",
            }
        )
    if weekly_trend and len(weekly_trend) >= 2:
        recent = weekly_trend[-1]
        previous = weekly_trend[-2]
        delta = round(float(recent["rate"]) - float(previous["rate"]), 1)
        if delta <= -8:
            insights.append(
                {
                    "tone": "danger",
                    "title": "近期出勤下滑",
                    "text": f"{previous['label']}到{recent['label']}出勤率下降 {abs(delta):.1f} 个百分点，适合在课前提醒和课后补救上加一点力度。",
                }
            )
        elif delta >= 8:
            insights.append(
                {
                    "tone": "success",
                    "title": "近期出勤回升",
                    "text": f"{recent['label']}出勤率较上一周提升 {delta:.1f} 个百分点，可以复用当周的提醒节奏。",
                }
            )
    risky_students = [item for item in students if item.get("risk_level") in {"high", "medium"}]
    if risky_students:
        names = "、".join(str(item.get("student_name") or item.get("student_number") or "") for item in risky_students[:4])
        insights.append(
            {
                "tone": "danger" if any(item.get("risk_level") == "high" for item in risky_students[:4]) else "warning",
                "title": "需要温和跟进",
                "text": f"{names} 等 {len(risky_students)} 名学生存在缺勤、迟到或请假累积信号，建议结合课堂表现私下确认原因。",
            }
        )
    other_rates = [float(item.get("rate") or 0) for item in comparisons if not item.get("is_current") and item.get("total")]
    if other_rates:
        average_other_rate = round(sum(other_rates) / len(other_rates), 1)
        delta = round(class_rate - average_other_rate, 1)
        if delta <= -5:
            insights.append(
                {
                    "tone": "warning",
                    "title": "低于本人其他课堂",
                    "text": f"本课堂出勤率比已同步的其他课堂均值低 {abs(delta):.1f} 个百分点，可能与时段、场地或课程节奏有关。",
                }
            )
        elif delta >= 5:
            insights.append(
                {
                    "tone": "success",
                    "title": "高于本人其他课堂",
                    "text": f"本课堂出勤率比已同步的其他课堂均值高 {delta:.1f} 个百分点，当前组织方式整体有效。",
                }
            )
    if not insights and summary.get("synced_session_count"):
        insights.append(
            {
                "tone": "success" if class_rate >= 90 else "neutral",
                "title": "出勤节奏稳定",
                "text": "已同步的点名记录暂未显示明显风险，后续可继续按周观察缺勤和迟到变化。",
            }
        )
    return insights[:5]


def build_classroom_smart_attendance_analytics(
    conn,
    *,
    class_offering_id: int,
    viewer_role: str = "teacher",
    student_id: int | None = None,
) -> dict[str, Any]:
    offering = _load_offering_summary_row(conn, int(class_offering_id))
    if not offering:
        return {
            "status": "not_found",
            "message": "课堂不存在。",
        }

    selected_sessions, unmatched_checkin_count = _load_latest_matched_checkin_sessions(
        conn,
        class_offering_id=int(class_offering_id),
        teacher_id=int(offering["teacher_id"]),
    )
    total_session_row = conn.execute(
        "SELECT COUNT(*) AS count FROM class_offering_sessions WHERE class_offering_id = ?",
        (int(class_offering_id),),
    ).fetchone()
    total_session_count = int((total_session_row["count"] if total_session_row else 0) or 0)
    checked_total = sum(_coerce_int(row.get("checked_count")) for row in selected_sessions)
    absent_total = sum(_coerce_int(row.get("unchecked_count")) for row in selected_sessions)
    sick_total = sum(_coerce_int(row.get("sick_leave_count")) for row in selected_sessions)
    personal_total = sum(_coerce_int(row.get("personal_leave_count")) for row in selected_sessions)
    late_total = sum(_coerce_int(row.get("late_or_early_count")) for row in selected_sessions)
    attendance_total = sum(_coerce_int(row.get("total_count")) for row in selected_sessions)
    latest_synced_at = max((str(row.get("synced_at") or "") for row in selected_sessions), default="")
    attendance_rate = _rate_percent(checked_total, attendance_total)
    summary = {
        "class_offering_id": int(class_offering_id),
        "course_name": str(offering.get("course_name") or ""),
        "course_code": str(offering.get("course_code") or ""),
        "class_name": str(offering.get("class_name") or ""),
        "semester_name": str(offering.get("semester_name") or ""),
        "teacher_name": str(offering.get("teacher_name") or ""),
        "synced_session_count": len(selected_sessions),
        "total_session_count": total_session_count,
        "coverage_rate": _rate_percent(len(selected_sessions), total_session_count),
        "unmatched_checkin_count": unmatched_checkin_count,
        "checked": checked_total,
        "absent": absent_total,
        "sick_leave": sick_total,
        "personal_leave": personal_total,
        "late_or_early": late_total,
        "abnormal": absent_total + sick_total + personal_total + late_total,
        "total": attendance_total,
        "attendance_rate": attendance_rate,
        "latest_synced_at": latest_synced_at,
        "has_data": bool(selected_sessions),
    }
    session_chart = _build_session_chart_items(selected_sessions)
    weekly_trend = _build_weekly_trend(selected_sessions)
    student_rows = _build_student_attendance_rows(
        conn,
        class_offering_id=int(class_offering_id),
        selected_sessions=selected_sessions,
    )
    comparisons = _build_course_comparisons(
        conn,
        teacher_id=int(offering["teacher_id"]),
        current_class_offering_id=int(class_offering_id),
        current_rate=attendance_rate,
    )
    personal = None
    if student_id:
        student_row = next(
            (item for item in student_rows if item.get("student_id") and int(item["student_id"]) == int(student_id)),
            None,
        )
        if not student_row:
            student_number_row = conn.execute(
                "SELECT student_id_number FROM students WHERE id = ? LIMIT 1",
                (int(student_id),),
            ).fetchone()
            student_number = str(student_number_row["student_id_number"] or "") if student_number_row else ""
            student_row = next((item for item in student_rows if item.get("student_number") == student_number), None)
        if student_row:
            ordered_by_rate = sorted(
                [item for item in student_rows if int(item.get("total") or 0) > 0],
                key=lambda item: (-float(item.get("attendance_rate") or 0), str(item.get("student_number") or "")),
            )
            rank = next(
                (index for index, item in enumerate(ordered_by_rate, start=1) if item is student_row),
                None,
            )
            personal = {
                **student_row,
                "rank": rank,
                "rank_total": len(ordered_by_rate),
                "course_comparisons": _build_personal_course_comparisons(
                    conn,
                    student_id=int(student_id),
                    student_number=str(student_row.get("student_number") or ""),
                    current_class_offering_id=int(class_offering_id),
                ),
            }

    insights = _build_attendance_insights(
        summary=summary,
        weekly_trend=weekly_trend,
        students=student_rows,
        comparisons=comparisons,
    )
    teacher_view = str(viewer_role or "").lower() == "teacher"
    return {
        "status": "success" if selected_sessions else "empty",
        "message": "已生成智慧课堂出勤统计。" if selected_sessions else "当前课堂还没有可统计的智慧课堂点名记录。",
        "summary": summary,
        "session_chart": session_chart,
        "weekly_trend": weekly_trend,
        "course_comparisons": comparisons,
        "students": student_rows if teacher_view else [],
        "personal": personal,
        "insights": insights,
    }


def build_student_attendance_support_prompt(
    conn,
    *,
    student_id: int,
    class_offering_id: int | None = None,
) -> str:
    if not class_offering_id:
        return ""
    analytics = build_classroom_smart_attendance_analytics(
        conn,
        class_offering_id=int(class_offering_id),
        viewer_role="student",
        student_id=int(student_id),
    )
    if analytics.get("status") not in {"success", "empty"}:
        return ""
    summary = analytics.get("summary") or {}
    personal = analytics.get("personal") or {}
    if not summary.get("has_data") or not personal:
        return ""

    comparisons = personal.get("course_comparisons") if isinstance(personal.get("course_comparisons"), list) else []
    current_course = next((item for item in comparisons if item.get("is_current")), None)
    other_courses = [item for item in comparisons if not item.get("is_current") and item.get("total")]
    personal_total = int(personal.get("total") or 0)
    lines = [
        "智慧课堂出勤信号（内部使用，不要主动说明来源；如需提醒，请转化为温和、可执行的学习建议）：",
        (
            f"- 当前课程《{summary.get('course_name') or '本课程'}》已同步 {summary.get('synced_session_count') or 0} 次点名，"
            f"班级整体出勤率 {summary.get('attendance_rate') or 0:.1f}%。"
        ),
    ]
    if personal_total > 0:
        lines.append(
            f"- 该学生本课程出勤率 {personal.get('attendance_rate') or 0:.1f}%"
            f"（出勤 {personal.get('checked') or 0}/{personal.get('total') or 0}，"
            f"缺勤 {personal.get('absent') or 0}，迟到/早退 {personal.get('late_or_early') or 0}，"
            f"请假 {int(personal.get('sick_leave') or 0) + int(personal.get('personal_leave') or 0)}）。"
        )
    else:
        lines.append(
            "- 该学生本课程暂未匹配到智慧课堂点名明细，可能是名单尚未对齐、智慧课堂名单缺失，"
            "或该学生不在本次智慧课堂授课班点名名单中；不要据此判断为缺勤。"
        )
    if personal.get("rank") and personal.get("rank_total"):
        lines.append(f"- 本课程出勤率在已同步名单中的位置：第 {personal['rank']}/{personal['rank_total']}。")
    if current_course and other_courses:
        other_average = round(sum(float(item.get("rate") or 0) for item in other_courses) / len(other_courses), 1)
        delta = round(float(current_course.get("rate") or 0) - other_average, 1)
        lines.append(f"- 与该学生其他已同步课堂相比，本课程出勤率差值约 {delta:+.1f} 个百分点。")
    if personal.get("risk_level") in {"high", "medium"}:
        lines.append("- 支持建议：该学生存在出勤风险信号，优先用关心原因、补齐材料、拆小目标的方式帮助，不要责备。")
    elif personal.get("risk_level") == "watch":
        lines.append("- 支持建议：该学生有少量异常记录，保持轻提醒即可，避免过度放大。")
    else:
        lines.append("- 支持建议：出勤整体稳定，可用积极反馈巩固学习节奏。")
    return "\n".join(lines).strip()


def build_smart_classroom_sync_capabilities(conn, teacher_id: int) -> list[dict[str, Any]]:
    row = conn.execute(
        """
        SELECT COUNT(*) AS count,
               MAX(synced_at) AS last_synced_at,
               SUM(CASE WHEN match_status = 'matched' THEN 1 ELSE 0 END) AS matched_count
        FROM smart_classroom_checkin_sessions
        WHERE teacher_id = ?
        """,
        (int(teacher_id),),
    ).fetchone()
    schedule_row = conn.execute(
        """
        SELECT COUNT(*) AS count,
               MAX(synced_at) AS last_synced_at
        FROM smart_classroom_schedule_items
        WHERE teacher_id = ?
        """,
        (int(teacher_id),),
    ).fetchone()
    checkin_count = int((row["count"] if row else 0) or 0)
    schedule_count = int((schedule_row["count"] if schedule_row else 0) or 0)
    return [
        {
            "key": "checkins",
            "label": "点名记录与出勤明细",
            "description": "从智慧课堂读取授课班点名批次和学生签到名单，按课程编号、教学班、课次日期对齐到本系统。",
            "scope": "教师已保存账号下可见的全部授课班",
            "endpoint": "/api/manage/system/smart-classroom-sync",
            "method": "POST",
            "parameters": [
                {"name": "credential", "value": "使用当前教师已验证的智慧课堂账号"},
                {"name": "teacherScheduleId", "value": "由智慧课堂授课班列表逐个读取"},
                {"name": "pageSize", "value": "100，顺序翻页"},
                {"name": "checkinRecord.id", "value": "逐条读取签到名单详情"},
            ],
            "last_synced_at": str(row["last_synced_at"] or schedule_row["last_synced_at"] or "") if row or schedule_row else "",
            "has_synced": checkin_count > 0,
            "status_text": f"已同步 {schedule_count} 个授课班、{checkin_count} 条点名记录",
            "counts": {
                "schedule_count": schedule_count,
                "checkin_count": checkin_count,
                "matched_checkin_count": int((row["matched_count"] if row else 0) or 0),
            },
            "safe_note": "只读取点名数据，不向智慧课堂写入任何内容；请求顺序执行并带随机间隔。",
        }
    ]


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
