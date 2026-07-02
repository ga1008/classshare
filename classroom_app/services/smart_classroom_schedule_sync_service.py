"""Smart Classroom teacher course-schedule sync + 课时 aggregation (课时统计).

Pulls the teacher's full-term teaching schedule from the Smart Classroom
endpoint ``POST /teaching/teacherSchedule/list`` (same authenticated client as
the check-in sync), stores it replace-style in the runtime tables created by
``schema_smart_schedule``, and builds the aggregated overview consumed by the
管理端 · 教学 · 课堂工具 · 课时统计 page:

- term options (学年学期) discovered from synced payloads;
- per-course teaching-hour totals (每节 = 1 课时, 一条排课 = 节数 × 周数);
- per-week lesson grids (纵轴节次 × 横轴星期) for the 3D week deck;
- best-effort teaching-class labels joined from the check-in schedule sync
  (``smart_classroom_schedule_items``), since the schedule endpoint itself
  does not return class names.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any

import httpx

from ..database import get_db_connection
from ..db.schema_smart_schedule import ensure_course_schedule_schema
from ..time_utils import format_display_datetime, local_iso
from .smart_classroom_integration_service import (
    load_teacher_smart_classroom_access_method,
    open_authenticated_smart_classroom_client,
)

SMART_PLATFORM_CODE = "gxufl_smart_classroom"
TEACHER_SCHEDULE_LIST_PATH = "/teaching/teacherSchedule/list"

WEEKDAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
SINGLE_OR_DOUBLE_LABELS = {"NONE": "", "SINGLE": "单周", "DOUBLE": "双周"}

_teacher_schedule_sync_locks: dict[int, asyncio.Lock] = {}


def _now_iso() -> str:
    return local_iso(timespec="seconds")


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("　", " ")).strip()


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _int_list(value: Any) -> list[int]:
    """Normalize a remote sections/weeks payload into a sorted unique int list."""
    if isinstance(value, str):
        value = [part for part in re.split(r"[,，\s]+", value) if part]
    if not isinstance(value, (list, tuple)):
        return []
    numbers = {_coerce_int(item) for item in value}
    return sorted(number for number in numbers if number > 0)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _safe_json_list(raw_value: Any) -> list[int]:
    try:
        parsed = json.loads(str(raw_value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return _int_list(parsed) if isinstance(parsed, list) else []


def _section_label(sections: list[int]) -> str:
    if not sections:
        return ""
    if len(sections) == 1:
        return f"第{sections[0]}节"
    contiguous = all(b - a == 1 for a, b in zip(sections, sections[1:]))
    if contiguous:
        return f"第{sections[0]}-{sections[-1]}节"
    return "第" + ",".join(str(section) for section in sections) + "节"


def _weekday_label(weekday: int) -> str:
    return WEEKDAY_LABELS[weekday - 1] if 1 <= weekday <= 7 else "未知"


def _term_label(year: str, term: str) -> str:
    year_text = year or "未知学年"
    term_text = f"第{term}学期" if term else "未知学期"
    return f"{year_text}学年 {term_text}"


def _display_datetime(value: Any) -> str:
    return format_display_datetime(value, fallback=str(value or "").replace("T", " "))


# --------------------------------------------------------------------------- #
# Remote fetch + parse
# --------------------------------------------------------------------------- #


def _parse_schedule_payload(payload: Any) -> dict[str, Any]:
    """Validate the raw teacherSchedule/list response into a normalized dict."""
    if not isinstance(payload, dict):
        raise ValueError("智慧课堂课程表接口返回格式异常。")
    raw_list = payload.get("list")
    items: list[dict[str, Any]] = []
    for raw in raw_list if isinstance(raw_list, list) else []:
        if not isinstance(raw, dict):
            continue
        sections = _int_list(raw.get("sections"))
        weeks = _int_list(raw.get("weeks"))
        course_name = _clean_text(raw.get("course"))
        if not course_name or not sections or not weeks:
            # A slot without course/sections/weeks carries no teaching hours.
            continue
        remote_id = _clean_text(raw.get("id"))
        if not remote_id:
            fingerprint = _json_dumps(
                [course_name, raw.get("courseCode"), raw.get("xqj"), sections, weeks, raw.get("classroom")]
            )
            remote_id = "derived-" + hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:24]
        items.append(
            {
                "remote_id": remote_id,
                "course_name": course_name,
                "course_code": _clean_text(raw.get("courseCode")),
                "classroom": _clean_text(raw.get("classroom")),
                "teacher_name": _clean_text(raw.get("teacher")),
                "teacher_no": _clean_text(raw.get("no")),
                "academic_year": _clean_text(raw.get("year")) or _clean_text(payload.get("year")),
                "academic_term": _clean_text(raw.get("semester")) or _clean_text(payload.get("semester")),
                "weekday": _coerce_int(raw.get("xqj")),
                "sections": sections,
                "weeks": weeks,
                "week_text": _clean_text(raw.get("week")),
                "single_or_double": _clean_text(raw.get("singleOrDoubleWeek")).upper() or "NONE",
                "student_count": _coerce_int(raw.get("stuNo")),
                "raw": raw,
            }
        )
    return {
        "year": _clean_text(payload.get("year")),
        "semester": _clean_text(payload.get("semester")),
        "cur_week": _coerce_int(payload.get("curWeek")),
        "max_week": _coerce_int(payload.get("maxWeek")),
        "cur_xq": _coerce_int(payload.get("curXq")),
        "items": items,
    }


async def _fetch_teacher_schedule(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.post(TEACHER_SCHEDULE_LIST_PATH, data={})
    response.raise_for_status()
    return _parse_schedule_payload(response.json())


# --------------------------------------------------------------------------- #
# Teaching-class enrichment (best-effort, from the check-in schedule sync)
# --------------------------------------------------------------------------- #


def _load_teaching_class_candidates(conn, teacher_id: int) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(
            """
            SELECT remote_course_id, remote_course_name, remote_teaching_class_name,
                   weekday, sections_text, student_count
            FROM smart_classroom_schedule_items
            WHERE teacher_id = ?
              AND COALESCE(remote_teaching_class_name, '') <> ''
            """,
            (int(teacher_id),),
        ).fetchall()
    except Exception:  # noqa: BLE001 — 点名同步表不存在时跳过教学班标注
        return []
    return [dict(row) for row in rows]


def _match_teaching_class_name(item: dict[str, Any], candidates: list[dict[str, Any]]) -> str:
    """Score the check-in schedule rows against one course-schedule slot.

    The check-in sync stores weekday as 0-6 (local) while the schedule payload
    uses 1-7; both sides carry course code/name, section list and student
    count, which together identify the teaching class reliably.
    """
    best_score = 0
    best_name = ""
    item_sections = set(item["sections"])
    for candidate in candidates:
        code = _clean_text(candidate.get("remote_course_id"))
        name = _clean_text(candidate.get("remote_course_name"))
        code_ok = bool(code) and code == item["course_code"]
        name_ok = bool(name) and name == item["course_name"]
        if not code_ok and not name_ok:
            continue
        score = 3 if code_ok else 2
        candidate_weekday = _coerce_int(candidate.get("weekday"), -1)
        if candidate_weekday == item["weekday"] - 1:
            score += 2
        candidate_sections = set(_int_list(candidate.get("sections_text")))
        if candidate_sections and candidate_sections & item_sections:
            score += 2
        if _coerce_int(candidate.get("student_count")) == item["student_count"] and item["student_count"] > 0:
            score += 2
        if score > best_score:
            best_score = score
            best_name = _clean_text(candidate.get("remote_teaching_class_name"))
    return best_name if best_score >= 5 else ""


def _fallback_class_label(item: dict[str, Any]) -> str:
    if item["student_count"] > 0:
        return f"教学班 · {item['student_count']}人"
    return "教学班"


# --------------------------------------------------------------------------- #
# Sync (replace-based)
# --------------------------------------------------------------------------- #


async def sync_teacher_course_schedule(teacher_id: int) -> dict[str, Any]:
    """Pull the teacher's schedule and replace the local copy for that term."""
    lock = _teacher_schedule_sync_locks.setdefault(int(teacher_id), asyncio.Lock())
    async with lock:
        with get_db_connection() as conn:
            access_payload = load_teacher_smart_classroom_access_method(conn, int(teacher_id))

        if not access_payload:
            return {
                "status": "missing_credential",
                "message": "请先在智慧课堂对接页面配置并验证智慧课堂账号。",
                "counts": {},
                "warnings": [],
            }

        try:
            async with open_authenticated_smart_classroom_client(access_payload) as (client, _profile, _login):
                parsed = await _fetch_teacher_schedule(client)
        except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
            return {
                "status": "failed",
                "message": f"智慧课堂课程表同步失败：{str(exc)[:180]}",
                "counts": {},
                "warnings": [str(exc)[:180]],
            }

        synced_at = _now_iso()
        items = parsed["items"]
        # Terms present in the payload — replacement is scoped to these so an
        # empty remote answer never wipes previously synced history.
        term_keys = {(item["academic_year"], item["academic_term"]) for item in items}
        if parsed["year"] or parsed["semester"]:
            term_keys.add((parsed["year"], parsed["semester"]))
        term_keys = {key for key in term_keys if key[0] or key[1]}

        matched_class_count = 0
        with get_db_connection() as conn:
            ensure_course_schedule_schema(conn)
            candidates = _load_teaching_class_candidates(conn, int(teacher_id))
            for year, term in term_keys:
                conn.execute(
                    """
                    DELETE FROM smart_classroom_course_schedule_items
                    WHERE teacher_id = ? AND platform_code = ?
                      AND academic_year = ? AND academic_term = ?
                    """,
                    (int(teacher_id), SMART_PLATFORM_CODE, year, term),
                )
            for item in items:
                teaching_class_name = _match_teaching_class_name(item, candidates)
                if teaching_class_name:
                    matched_class_count += 1
                conn.execute(
                    """
                    INSERT INTO smart_classroom_course_schedule_items (
                        teacher_id, platform_code, remote_id, course_name, course_code,
                        classroom, teaching_class_name, teacher_name, teacher_no,
                        academic_year, academic_term, weekday, sections_json, weeks_json,
                        week_text, single_or_double, student_count, metadata_json,
                        synced_at, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(teacher_id, platform_code, remote_id) DO UPDATE SET
                        course_name = excluded.course_name,
                        course_code = excluded.course_code,
                        classroom = excluded.classroom,
                        teaching_class_name = excluded.teaching_class_name,
                        teacher_name = excluded.teacher_name,
                        teacher_no = excluded.teacher_no,
                        academic_year = excluded.academic_year,
                        academic_term = excluded.academic_term,
                        weekday = excluded.weekday,
                        sections_json = excluded.sections_json,
                        weeks_json = excluded.weeks_json,
                        week_text = excluded.week_text,
                        single_or_double = excluded.single_or_double,
                        student_count = excluded.student_count,
                        metadata_json = excluded.metadata_json,
                        synced_at = excluded.synced_at
                    """,
                    (
                        int(teacher_id),
                        SMART_PLATFORM_CODE,
                        item["remote_id"],
                        item["course_name"],
                        item["course_code"],
                        item["classroom"],
                        teaching_class_name,
                        item["teacher_name"],
                        item["teacher_no"],
                        item["academic_year"],
                        item["academic_term"],
                        item["weekday"],
                        _json_dumps(item["sections"]),
                        _json_dumps(item["weeks"]),
                        item["week_text"],
                        item["single_or_double"],
                        item["student_count"],
                        _json_dumps(item["raw"]),
                        synced_at,
                        synced_at,
                    ),
                )
            for year, term in term_keys:
                item_count = sum(
                    1 for item in items if (item["academic_year"], item["academic_term"]) == (year, term)
                )
                conn.execute(
                    """
                    INSERT INTO smart_classroom_course_schedule_meta (
                        teacher_id, platform_code, academic_year, academic_term,
                        cur_week, max_week, cur_xq, item_count, synced_at,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(teacher_id, platform_code, academic_year, academic_term) DO UPDATE SET
                        cur_week = excluded.cur_week,
                        max_week = excluded.max_week,
                        cur_xq = excluded.cur_xq,
                        item_count = excluded.item_count,
                        synced_at = excluded.synced_at,
                        updated_at = excluded.updated_at
                    """,
                    (
                        int(teacher_id),
                        SMART_PLATFORM_CODE,
                        year,
                        term,
                        parsed["cur_week"],
                        parsed["max_week"],
                        parsed["cur_xq"],
                        item_count,
                        synced_at,
                        synced_at,
                        synced_at,
                    ),
                )
            conn.commit()

        total_hours = sum(len(item["sections"]) * len(item["weeks"]) for item in items)
        course_count = len({(item["course_name"], item["course_code"]) for item in items})
        counts = {
            "schedule_count": len(items),
            "course_count": course_count,
            "total_hours": total_hours,
            "matched_class_count": matched_class_count,
        }
        if not items:
            return {
                "status": "empty",
                "message": "智慧课堂本学期暂未返回任何排课记录。",
                "counts": counts,
                "warnings": [],
                "synced_at": synced_at,
            }
        return {
            "status": "success",
            "message": (
                f"已同步 {len(items)} 条排课、{course_count} 门课程，"
                f"本学期共 {total_hours} 课时。"
            ),
            "counts": counts,
            "warnings": [],
            "synced_at": synced_at,
        }


# --------------------------------------------------------------------------- #
# Aggregated overview for the 课时统计 page
# --------------------------------------------------------------------------- #


def _load_meta_rows(conn, teacher_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM smart_classroom_course_schedule_meta
        WHERE teacher_id = ? AND platform_code = ?
        ORDER BY academic_year DESC, academic_term DESC
        """,
        (int(teacher_id), SMART_PLATFORM_CODE),
    ).fetchall()
    return [dict(row) for row in rows]


def _serialize_item(row: Any) -> dict[str, Any]:
    row_dict = dict(row)
    sections = _safe_json_list(row_dict.get("sections_json"))
    weeks = _safe_json_list(row_dict.get("weeks_json"))
    weekday = _coerce_int(row_dict.get("weekday"))
    single_or_double = str(row_dict.get("single_or_double") or "NONE").upper()
    class_name = _clean_text(row_dict.get("teaching_class_name"))
    item = {
        "id": int(row_dict["id"]),
        "remote_id": str(row_dict.get("remote_id") or ""),
        "course_name": str(row_dict.get("course_name") or ""),
        "course_code": str(row_dict.get("course_code") or ""),
        "classroom": str(row_dict.get("classroom") or ""),
        "teaching_class_name": class_name,
        "academic_year": str(row_dict.get("academic_year") or ""),
        "academic_term": str(row_dict.get("academic_term") or ""),
        "weekday": weekday,
        "weekday_label": _weekday_label(weekday),
        "sections": sections,
        "section_label": _section_label(sections),
        "weeks": weeks,
        "week_text": str(row_dict.get("week_text") or ""),
        "single_or_double": single_or_double,
        "single_or_double_label": SINGLE_OR_DOUBLE_LABELS.get(single_or_double, ""),
        "student_count": _coerce_int(row_dict.get("student_count")),
        "hours_per_meeting": len(sections),
        "total_hours": len(sections) * len(weeks),
        "synced_at": _display_datetime(row_dict.get("synced_at")),
    }
    if not item["teaching_class_name"]:
        item["class_label"] = _fallback_class_label({"student_count": item["student_count"]})
        item["class_is_fallback"] = True
    else:
        item["class_label"] = item["teaching_class_name"]
        item["class_is_fallback"] = False
    return item


def _build_course_stats(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        key = (item["course_name"], item["course_code"])
        stat = grouped.setdefault(
            key,
            {
                "course_name": item["course_name"],
                "course_code": item["course_code"],
                "total_hours": 0,
                "slot_count": 0,
                "weeks": set(),
                "classrooms": set(),
                "classes": set(),
                "max_student_count": 0,
            },
        )
        stat["total_hours"] += item["total_hours"]
        stat["slot_count"] += 1
        stat["weeks"].update(item["weeks"])
        if item["classroom"]:
            stat["classrooms"].add(item["classroom"])
        stat["classes"].add(item["class_label"])
        stat["max_student_count"] = max(stat["max_student_count"], item["student_count"])
    stats = []
    for stat in grouped.values():
        stats.append(
            {
                "course_name": stat["course_name"],
                "course_code": stat["course_code"],
                "total_hours": stat["total_hours"],
                "slot_count": stat["slot_count"],
                "week_count": len(stat["weeks"]),
                "week_span": (
                    f"第{min(stat['weeks'])}-{max(stat['weeks'])}周" if stat["weeks"] else ""
                ),
                "classrooms": sorted(stat["classrooms"]),
                "classes": sorted(stat["classes"]),
                "max_student_count": stat["max_student_count"],
            }
        )
    stats.sort(key=lambda entry: (-entry["total_hours"], entry["course_name"]))
    return stats


def _build_week_deck(items: list[dict[str, Any]], *, max_week: int, cur_week: int) -> list[dict[str, Any]]:
    highest_item_week = max((max(item["weeks"]) for item in items if item["weeks"]), default=0)
    deck_max = max(max_week, highest_item_week)
    if deck_max <= 0:
        return []
    weeks: list[dict[str, Any]] = []
    for week_index in range(1, deck_max + 1):
        lessons = [
            {
                "id": item["id"],
                "weekday": item["weekday"],
                "weekday_label": item["weekday_label"],
                "sections": item["sections"],
                "section_label": item["section_label"],
                "course_name": item["course_name"],
                "course_code": item["course_code"],
                "classroom": item["classroom"],
                "class_label": item["class_label"],
                "class_is_fallback": item["class_is_fallback"],
                "student_count": item["student_count"],
                "hours": item["hours_per_meeting"],
            }
            for item in items
            if week_index in item["weeks"]
        ]
        lessons.sort(key=lambda lesson: (lesson["weekday"], lesson["sections"][0] if lesson["sections"] else 0))
        weeks.append(
            {
                "week_index": week_index,
                "label": f"第{week_index}周",
                "is_current": week_index == cur_week,
                "lesson_count": len(lessons),
                "total_hours": sum(lesson["hours"] for lesson in lessons),
                "lessons": lessons,
            }
        )
    return weeks


def build_teacher_course_schedule_overview(
    conn,
    teacher_id: int,
    *,
    year: str = "",
    term: str = "",
    course: str = "",
    class_label: str = "",
) -> dict[str, Any]:
    """Build the filtered overview: filters + aggregation + week deck."""
    ensure_course_schedule_schema(conn)
    meta_rows = _load_meta_rows(conn, int(teacher_id))
    terms = [
        {
            "year": str(meta.get("academic_year") or ""),
            "term": str(meta.get("academic_term") or ""),
            "label": _term_label(
                str(meta.get("academic_year") or ""), str(meta.get("academic_term") or "")
            ),
            "cur_week": _coerce_int(meta.get("cur_week")),
            "max_week": _coerce_int(meta.get("max_week")),
            "item_count": _coerce_int(meta.get("item_count")),
            "synced_at": _display_datetime(meta.get("synced_at")),
        }
        for meta in meta_rows
    ]
    if not terms:
        return {
            "status": "empty",
            "has_data": False,
            "message": "还没有同步过智慧课堂课程表，点击「同步智慧课堂」拉取本学期排课。",
            "terms": [],
            "selected_term": None,
            "filters": {"course": "", "class_label": "", "course_options": [], "class_options": []},
            "summary": {},
            "courses": [],
            "weeks": [],
            "section_range": {"min": 1, "max": 12},
        }

    selected = next(
        (entry for entry in terms if entry["year"] == year and entry["term"] == term),
        terms[0],
    )
    rows = conn.execute(
        """
        SELECT *
        FROM smart_classroom_course_schedule_items
        WHERE teacher_id = ? AND platform_code = ?
          AND academic_year = ? AND academic_term = ?
        ORDER BY weekday, sections_json, course_name
        """,
        (int(teacher_id), SMART_PLATFORM_CODE, selected["year"], selected["term"]),
    ).fetchall()
    all_items = [_serialize_item(row) for row in rows]

    course_options = sorted({item["course_name"] for item in all_items if item["course_name"]})
    class_options = sorted({item["class_label"] for item in all_items if item["class_label"]})

    items = all_items
    if course:
        items = [item for item in items if item["course_name"] == course]
    if class_label:
        items = [item for item in items if item["class_label"] == class_label]

    course_stats = _build_course_stats(items)
    weeks = _build_week_deck(items, max_week=selected["max_week"], cur_week=selected["cur_week"])
    total_hours = sum(item["total_hours"] for item in items)
    cur_week_entry = next((week for week in weeks if week["is_current"]), None)
    all_sections = [section for item in items for section in item["sections"]]
    section_max = max(all_sections, default=11)

    summary = {
        "course_count": len(course_stats),
        "class_count": len({item["class_label"] for item in items if item["class_label"]}),
        "classroom_count": len({item["classroom"] for item in items if item["classroom"]}),
        "slot_count": len(items),
        "total_hours": total_hours,
        "current_week_hours": cur_week_entry["total_hours"] if cur_week_entry else 0,
        "cur_week": selected["cur_week"],
        "max_week": selected["max_week"],
        "weekly_average_hours": (
            round(total_hours / len([week for week in weeks if week["lesson_count"]]), 1)
            if any(week["lesson_count"] for week in weeks)
            else 0
        ),
    }

    return {
        "status": "success",
        "has_data": bool(all_items),
        "message": "",
        "terms": terms,
        "selected_term": selected,
        "filters": {
            "course": course if course in course_options else "",
            "class_label": class_label if class_label in class_options else "",
            "course_options": course_options,
            "class_options": class_options,
        },
        "summary": summary,
        "courses": course_stats,
        "weeks": weeks,
        "section_range": {"min": 1, "max": max(11, section_max)},
    }


def build_course_schedule_capability(conn, teacher_id: int) -> dict[str, Any]:
    """Capability card shown on the 智慧课堂对接 page for this sync feature."""
    ensure_course_schedule_schema(conn)
    row = conn.execute(
        """
        SELECT COUNT(*) AS count, MAX(synced_at) AS last_synced_at
        FROM smart_classroom_course_schedule_items
        WHERE teacher_id = ? AND platform_code = ?
        """,
        (int(teacher_id), SMART_PLATFORM_CODE),
    ).fetchone()
    meta_row = conn.execute(
        """
        SELECT COUNT(*) AS term_count
        FROM smart_classroom_course_schedule_meta
        WHERE teacher_id = ? AND platform_code = ?
        """,
        (int(teacher_id), SMART_PLATFORM_CODE),
    ).fetchone()
    item_count = int((row["count"] if row else 0) or 0)
    term_count = int((meta_row["term_count"] if meta_row else 0) or 0)
    return {
        "key": "course_schedule",
        "label": "教师课程表与课时统计",
        "description": "从智慧课堂读取教师本人本学期全部排课（课程、教室、节次、周次），按周展示课表并统计课程课时和学期课时。",
        "scope": "教师已保存账号下本学期的全部排课",
        "endpoint": "/api/manage/teaching/course-schedule/sync",
        "method": "POST",
        "parameters": [
            {"name": "credential", "value": "使用当前教师已验证的智慧课堂账号"},
            {"name": "请求体", "value": "空表单（接口按登录教师返回本学期课表）"},
        ],
        "last_synced_at": _display_datetime(row["last_synced_at"] if row else ""),
        "has_synced": item_count > 0,
        "status_text": f"已同步 {term_count} 个学期、{item_count} 条排课",
        "counts": {"schedule_count": item_count, "term_count": term_count},
        "stats": [
            {"label": "学期", "value": term_count},
            {"label": "排课记录", "value": item_count},
        ],
        "request_template": {
            "provider": "smart_classroom",
            "method": "POST",
            "url": "https://edu_api.gxufl.com/api/teaching/teacherSchedule/list",
            "params": {},
            "headers": {
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                "Referer": "https://edu.gxufl.edu.cn/teaching/schedule",
            },
            "body_mode": "form",
            "body": {},
        },
        "safe_note": "只读取课表数据，不向智慧课堂写入任何内容；每次同步会替换本地对应学期的课表副本。",
    }
