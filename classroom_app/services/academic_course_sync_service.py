from __future__ import annotations

import html
import json
import re
import sqlite3
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import httpx

from ..database import get_db_connection
from ..db.connection import begin_immediate_transaction, execute_insert_returning_id, get_configured_db_engine
from .academic_calendar_sync_service import prepare_current_semester_from_academic_system
from .academic_integration_service import (
    load_teacher_academic_access_method,
    open_authenticated_academic_client,
)
from .academic_service import china_now, parse_date_input
from .course_planning_service import (
    SCHEDULE_SOURCE_ACADEMIC_SYNC,
    build_academic_offering_session_plan,
    load_course_lessons_by_course_id,
    replace_offering_sessions,
    select_academic_teaching_class_for_offering,
)
from .department_service import infer_department_from_text, normalize_department
from .learning_progress_service import normalize_course_sect_name
from .organization_scope_service import apply_teacher_scope_to_org, load_teacher_org_scope


ACADEMIC_COURSE_SOURCE = "gxufl_jwxt"
ZF_TEACHER_TIMETABLE_INDEX_PATH = (
    "/kbcx/jskbcx_cxJskbcxIndex.html?doType=details&gnmkdm=N2150&layout=default"
)
ZF_TIMETABLE_FIELD_PATH = "/kbdy/bjkbdy_cxKbzdxsxx.html?gnmkdm=N2150"
ZF_TEACHER_TIMETABLE_QUERY_PATH = "/kbcx/jskbcx_cxJsKb1.html?gnmkdm=N2150"
ZF_LAB_TIMETABLE_LIST_PATH = "/jssygl/sykbcx_cxSykbcxList.html?doType=query&gnmkdm=N2150"
ZF_LAB_TIMETABLE_QUERY_PATH = "/jssygl/sykbcx_cxKfxSykbcxIndex.html?doType=query&gnmkdm=N2150"
ZF_TIMETABLE_WEEK_SLOTS_PATH = "/kbcx/jskbcx_cxRsd.html?gnmkdm=N2150"
ZF_TIMETABLE_SECTION_SLOTS_PATH = "/kbcx/jskbcx_cxRjc.html?gnmkdm=N2150"

FOLLOW_UP_ITEMS = [
    "补充课程简介、教学目标和平台内使用说明",
    "选择或导入教材，并绑定到课堂设置",
    "确认本平台班级与学生名单，避免只按教务教学班误开课堂",
    "生成或完善课堂设置，保证总学时与每次课内容对齐",
    "复核教务周次、地点、教学班组成是否需要在本平台拆分",
]

WEEKDAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
WEEKDAY_ALIASES = {
    "星期一": 0,
    "周一": 0,
    "一": 0,
    "星期二": 1,
    "周二": 1,
    "二": 1,
    "星期三": 2,
    "周三": 2,
    "三": 2,
    "星期四": 3,
    "周四": 3,
    "四": 3,
    "星期五": 4,
    "周五": 4,
    "五": 4,
    "星期六": 5,
    "周六": 5,
    "六": 5,
    "星期日": 6,
    "星期天": 6,
    "周日": 6,
    "周天": 6,
    "日": 6,
    "天": 6,
}

HTML_FIELD_LABELS = [
    "课程学时组成",
    "课程性质简称",
    "教学班组成",
    "考试方式",
    "考核方式",
    "选课人数",
    "上课地点",
    "课程号",
    "周数",
    "校区",
    "学分",
]
HTML_FIELD_PATTERN = "|".join(re.escape(item) for item in HTML_FIELD_LABELS)

ZF_TIMETABLE_FIELD_KEYS = [
    "kch",
    "sj",
    "cd",
    "jsxm",
    "jxb",
    "ktmc",
    "jxbzc",
    "kcxzjc",
    "jxbrs",
    "xkrs",
    "khfs",
    "ksfs",
    "xkbz",
    "kcxszc",
    "zhxs",
    "zxs",
    "kczxs",
    "bklxdjmc",
    "cdlbmc",
    "fx",
    "xf",
    "xq",
]

ZF_OPTIONAL_FALSE_FIELD_KEYS = ["zxxx"]


@dataclass
class AcademicCourseScheduleItem:
    academic_year: str = ""
    academic_year_name: str = ""
    academic_term: str = ""
    academic_term_name: str = ""
    teacher_name: str = ""
    teacher_org_id: str = ""
    teacher_org_name: str = ""
    course_name: str = ""
    course_code: str = ""
    teaching_class_name: str = ""
    time_text: str = ""
    weeks_text: str = ""
    weekday: int | None = None
    weekday_label: str = ""
    section_text: str = ""
    campus: str = ""
    campus_id: str = ""
    location: str = ""
    classroom_id: str = ""
    classroom_code: str = ""
    classroom_type: str = ""
    class_composition: str = ""
    course_nature: str = ""
    exam_method: str = ""
    exam_mode: str = ""
    course_hour_text: str = ""
    weekly_hours_text: str = ""
    total_hours_text: str = ""
    course_total_hours_text: str = ""
    major_direction: str = ""
    course_note: str = ""
    online_info: str = ""
    course_topic_name: str = ""
    block_level: str = ""
    teaching_class_student_count: int = 0
    credits: float = 0.0
    student_count: int = 0
    raw_text: str = ""
    raw_json: dict[str, Any] = field(default_factory=dict)
    source_url: str = ""


def _now_iso() -> str:
    return china_now().replace(tzinfo=None).isoformat(timespec="seconds")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _safe_json_loads(raw_value: Any, fallback: Any) -> Any:
    if raw_value in (None, ""):
        return fallback
    if isinstance(raw_value, type(fallback)):
        return raw_value
    try:
        parsed = json.loads(str(raw_value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def _normalize_space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u3000", " ")).strip()


def _strip_html(value: Any) -> str:
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", str(value or ""), flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    return _normalize_space(html.unescape(text))


def _weekday_label(weekday: int | None) -> str:
    if weekday is None:
        return ""
    if 0 <= int(weekday) < len(WEEKDAY_LABELS):
        return WEEKDAY_LABELS[int(weekday)]
    return f"周{int(weekday) + 1}"


def _parse_weekday(value: Any) -> int | None:
    normalized = _normalize_space(value)
    if not normalized:
        return None
    if normalized.isdigit():
        numeric = int(normalized)
        if 1 <= numeric <= 7:
            return numeric - 1
        if 0 <= numeric <= 6:
            return numeric
    for label, index in WEEKDAY_ALIASES.items():
        if label in normalized:
            return index
    return None


def _parse_section_text(value: Any) -> str:
    normalized = _normalize_space(value)
    if not normalized:
        return ""
    match = re.search(r"(\d{1,2})\s*[-~－—]\s*(\d{1,2})", normalized)
    if match:
        return f"{int(match.group(1))}-{int(match.group(2))}"
    match = re.search(r"第?\s*(\d{1,2})\s*节", normalized)
    if match:
        return str(int(match.group(1)))
    return normalized.replace("节", "").strip()


def _parse_float(value: Any) -> float:
    match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
    if not match:
        return 0.0
    try:
        return float(match.group(0))
    except ValueError:
        return 0.0


def _parse_int(value: Any) -> int:
    match = re.search(r"\d+", str(value or ""))
    if not match:
        return 0
    try:
        return int(match.group(0))
    except ValueError:
        return 0


def _parse_total_hours(value: Any) -> int:
    numbers = [int(float(item)) for item in re.findall(r"\d+(?:\.\d+)?", str(value or ""))]
    if not numbers:
        return 0
    return max(0, sum(numbers))


def _parse_week_numbers(value: Any, *, max_week_count: int = 40) -> list[int]:
    text = _normalize_space(value)
    if not text:
        return []
    weeks: set[int] = set()
    segments = [segment for segment in re.split(r"[,，、;；]\s*", text) if segment.strip()]
    for segment in segments or [text]:
        normalized_segment = _normalize_space(segment)
        parity = None
        if "单" in normalized_segment:
            parity = 1
        elif "双" in normalized_segment:
            parity = 0
        ranges = re.findall(r"(\d{1,2})\s*[-~－—]\s*(\d{1,2})", normalized_segment)
        consumed = set()
        for start_text, end_text in ranges:
            start = int(start_text)
            end = int(end_text)
            if end < start:
                start, end = end, start
            for week in range(start, min(end, max_week_count) + 1):
                if parity is None or week % 2 == parity:
                    weeks.add(week)
            consumed.update([start_text, end_text])
        single_numbers = [
            int(number)
            for number in re.findall(r"\d{1,2}", normalized_segment)
            if number not in consumed
        ]
        for week in single_numbers:
            if 1 <= week <= max_week_count and (parity is None or week % 2 == parity):
                weeks.add(week)
    return sorted(weeks)


def _is_non_periodic_weeks(weeks_text: Any, week_numbers: list[int]) -> bool:
    text = _normalize_space(weeks_text)
    if any(marker in text for marker in ("单", "双", ",", "，", "、", ";", "；")):
        return True
    if len(week_numbers) <= 1:
        return False
    return week_numbers != list(range(week_numbers[0], week_numbers[-1] + 1))


def _semester_monday(semester: dict[str, Any]) -> date | None:
    start_date = parse_date_input(semester.get("start_date"))
    if not start_date:
        return None
    return start_date - timedelta(days=start_date.weekday())


def _date_for_academic_week(
    semester: dict[str, Any],
    *,
    week_index: int,
    weekday: int,
) -> str:
    start_monday = _semester_monday(semester)
    if not start_monday or week_index <= 0 or not 0 <= weekday <= 6:
        return ""
    return (start_monday + timedelta(days=(week_index - 1) * 7 + weekday)).isoformat()


def _parse_section_range(section_text: Any) -> tuple[int, int, int]:
    text = _normalize_space(section_text)
    match = re.search(r"(\d{1,2})\s*[-~－—]\s*(\d{1,2})", text)
    if match:
        start = int(match.group(1))
        end = int(match.group(2))
        if end < start:
            start, end = end, start
        return start, end, max(1, end - start + 1)
    match = re.search(r"\d{1,2}", text)
    if match:
        start = int(match.group(0))
        return start, start, 1
    return 0, 0, 1


def _extract_cells(row_html: str) -> list[str]:
    cells = re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row_html, flags=re.IGNORECASE | re.DOTALL)
    return [_strip_html(cell) for cell in cells if _strip_html(cell)]


def _extract_labeled_value(text: str, label: str) -> str:
    pattern = rf"{re.escape(label)}\s*[：:]\s*(.*?)(?=\s*(?:{HTML_FIELD_PATTERN})\s*[：:]|$)"
    match = re.search(pattern, text)
    if not match:
        return ""
    return _normalize_space(match.group(1)).strip("；;，,")


def _remove_known_prefixes(value: str) -> str:
    text = _normalize_space(value)
    text = re.sub(r"^(?:星期[一二三四五六日天]|周[一二三四五六日天])\s*", "", text)
    text = re.sub(r"^\d{1,2}\s*[-~－—]\s*\d{1,2}\s*", "", text)
    return _normalize_space(text)


def _extract_course_name(info_text: str) -> str:
    text = _remove_known_prefixes(info_text)
    label_positions = [text.find(label) for label in HTML_FIELD_LABELS if text.find(label) >= 0]
    if label_positions:
        text = text[: min(label_positions)]
    return _normalize_space(text).strip("：:；;，,")


def _parse_schedule_items_from_html(page_html: str, source_url: str) -> list[AcademicCourseScheduleItem]:
    items: list[AcademicCourseScheduleItem] = []
    current_weekday: int | None = None

    rows = re.findall(r"<tr\b[^>]*>(.*?)</tr>", page_html, flags=re.IGNORECASE | re.DOTALL)
    for row_html in rows:
        row_text = _strip_html(row_html)
        if not row_text or ("课表信息" in row_text and "课程号" not in row_text):
            continue

        cells = _extract_cells(row_html)
        if not cells:
            continue

        local_weekday = None
        section_text = ""
        content_cells: list[str] = []
        for cell in cells:
            parsed_weekday = _parse_weekday(cell)
            parsed_section = _parse_section_text(cell)
            if parsed_weekday is not None and not re.search(r"课程号|周数|上课地点", cell):
                local_weekday = parsed_weekday
                current_weekday = parsed_weekday
                continue
            if (
                not section_text
                and not re.search(r"课程号|周数|上课地点|教学班组成|课程学时", cell)
                and re.fullmatch(r"\d{1,2}(?:\s*[-~－—]\s*\d{1,2})?", cell)
            ):
                section_text = parsed_section
                continue
            content_cells.append(cell)

        weekday = local_weekday if local_weekday is not None else current_weekday
        info_text = _normalize_space(" ".join(content_cells) or row_text)
        course_name = _extract_course_name(info_text)
        course_code = _extract_labeled_value(info_text, "课程号")
        weeks_text = _extract_labeled_value(info_text, "周数")
        location = _extract_labeled_value(info_text, "上课地点")
        class_composition = _extract_labeled_value(info_text, "教学班组成")

        if not course_name or not (course_code or weeks_text or location or class_composition):
            continue

        item = AcademicCourseScheduleItem(
            course_name=course_name[:160],
            course_code=course_code[:80],
            teaching_class_name=class_composition[:180],
            weeks_text=weeks_text[:180],
            weekday=weekday,
            weekday_label=_weekday_label(weekday),
            section_text=section_text[:40],
            campus=_extract_labeled_value(info_text, "校区")[:120],
            location=location[:220],
            class_composition=class_composition[:260],
            course_nature=_extract_labeled_value(info_text, "课程性质简称")[:80],
            exam_method=_extract_labeled_value(info_text, "考核方式")[:80],
            exam_mode=_extract_labeled_value(info_text, "考试方式")[:80],
            course_hour_text=_extract_labeled_value(info_text, "课程学时组成")[:160],
            credits=_parse_float(_extract_labeled_value(info_text, "学分")),
            student_count=_parse_int(_extract_labeled_value(info_text, "选课人数")),
            raw_text=info_text[:1600],
            raw_json={"parser": "html_table"},
            source_url=source_url,
        )
        items.append(item)

    return _dedupe_schedule_items(items)


def _walk_json_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json_dicts(child)


def _first_text(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return _normalize_space(data[key])
    return ""


def _payload_context(payload: Any) -> dict[str, str]:
    if not isinstance(payload, dict):
        return {}
    xsxx = payload.get("xsxx") if isinstance(payload.get("xsxx"), dict) else {}
    jsxx = payload.get("jsxx") if isinstance(payload.get("jsxx"), dict) else {}
    context = {**xsxx, **jsxx}
    return {
        "academic_year": _first_text(context, "XNM", "xnm"),
        "academic_year_name": _first_text(context, "XNMC", "xnmc"),
        "academic_term": _first_text(context, "XQM", "xqm"),
        "academic_term_name": _first_text(context, "XQMMC", "xqmmc"),
        "teacher_name": _first_text(context, "XM", "xm"),
        "teacher_org_id": _first_text(context, "JG_ID", "jg_id"),
        "teacher_org_name": _first_text(context, "JGMC", "jgmc"),
    }


def _field_key_from_definition(raw: dict[str, Any]) -> str:
    key = _first_text(raw, "ZDM", "zdm", "field", "name", "key").strip()
    if key:
        return key
    return ""


def _field_keys_from_response(payload: Any) -> list[str]:
    keys: list[str] = []
    for raw in _walk_json_dicts(payload):
        key = _field_key_from_definition(raw)
        if key and key not in keys:
            keys.append(key)
    return keys


def _candidate_course_dicts(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        kb_list = payload.get("kbList")
        if isinstance(kb_list, list) and kb_list:
            return [item for item in kb_list if isinstance(item, dict)]
    return [
        raw
        for raw in _walk_json_dicts(payload)
        if any(key in raw for key in ("kcmc", "kch", "kch_id", "jxbmc", "jxb", "zcd", "cdmc"))
    ]


def _parse_schedule_items_from_json(payload: Any, source_url: str) -> list[AcademicCourseScheduleItem]:
    items: list[AcademicCourseScheduleItem] = []
    context = _payload_context(payload)
    for raw in _candidate_course_dicts(payload):
        course_name = _first_text(raw, "kcmc", "kcmc_zw", "courseName", "course_name", "name")
        course_code = _first_text(raw, "kch", "kch_id", "kcdm", "kcbh", "courseCode")
        if not course_name:
            course_name = _first_text(raw, "ktmc", "jxbmc", "jxb")
        if not course_name or not any(
            key in raw
            for key in ("kch", "kch_id", "jxbmc", "jxb", "zcd", "xqj", "jc", "cdmc", "sj", "kcxszc")
        ):
            continue

        time_text = _first_text(raw, "sj", "time_text", "time")
        weekday = _parse_weekday(_first_text(raw, "xqj", "xqjmc", "weekday", "weekDay") or time_text)
        section_text = _parse_section_text(_first_text(raw, "jc", "jcs", "jcdm", "sections", "section_text") or time_text)
        course_hour_text = _first_text(raw, "kcxszz", "kcxszc", "xs", "xszc", "hourComposition", "course_hour_text")
        weekly_hours_text = _first_text(raw, "zhxs", "weeklyHours", "weekly_hours_text")
        total_hours_text = _first_text(raw, "zxs", "totalHours", "total_hours_text")
        course_total_hours_text = _first_text(raw, "kczxs", "courseTotalHours", "course_total_hours_text")
        raw_text = _normalize_space(
            " ".join(
                filter(
                    None,
                    [
                        course_name,
                        course_code,
                        time_text,
                        _first_text(raw, "zcd", "skzcmc", "zc"),
                        _first_text(raw, "cdmc", "jxdd", "classroom", "room"),
                        _first_text(raw, "jxbzc", "jxbmc", "jxb"),
                        course_hour_text,
                    ],
                )
            )
        )

        item = AcademicCourseScheduleItem(
            academic_year=context.get("academic_year", "")[:24],
            academic_year_name=context.get("academic_year_name", "")[:40],
            academic_term=context.get("academic_term", "")[:24],
            academic_term_name=context.get("academic_term_name", "")[:40],
            teacher_name=context.get("teacher_name", "")[:80],
            teacher_org_id=context.get("teacher_org_id", "")[:80],
            teacher_org_name=context.get("teacher_org_name", "")[:160],
            course_name=course_name[:160],
            course_code=course_code[:80],
            teaching_class_name=_first_text(raw, "jxbmc", "jxb", "jxb_id", "teachingClassName")[:180],
            time_text=time_text[:180],
            weeks_text=_first_text(raw, "zcd", "skzcmc", "zc", "weeks", "weeks_text")[:180],
            weekday=weekday,
            weekday_label=_weekday_label(weekday),
            section_text=section_text[:40],
            campus=_first_text(raw, "xqmc", "xq", "campus", "campusName")[:120],
            campus_id=_first_text(raw, "xqdm", "xq_id", "xqid", "xqh_id")[:80],
            location=_first_text(raw, "cdmc", "jxdd", "classroom", "room", "location")[:220],
            classroom_id=_first_text(raw, "cd_id", "cdid", "classroomId")[:120],
            classroom_code=_first_text(raw, "cdbh", "cdh", "classroomCode")[:80],
            classroom_type=_first_text(raw, "cdlbmc", "cdlb", "classroomType")[:120],
            class_composition=_first_text(raw, "jxbzc", "jxbmc", "bj", "classComposition", "class_composition")[:260],
            course_nature=_first_text(raw, "kcxzjc", "kcxzmc", "kcxz", "courseNature")[:80],
            exam_method=_first_text(raw, "khfs", "khfsmc", "examMethod")[:80],
            exam_mode=_first_text(raw, "ksfs", "ksfsmc", "examMode")[:80],
            course_hour_text=course_hour_text[:160],
            weekly_hours_text=weekly_hours_text[:80],
            total_hours_text=total_hours_text[:80],
            course_total_hours_text=course_total_hours_text[:80],
            major_direction=_first_text(raw, "fx", "zyfx", "majorDirection")[:120],
            course_note=_first_text(raw, "xkbz", "note", "remark")[:180],
            online_info=_first_text(raw, "zxxx", "onlineInfo")[:180],
            course_topic_name=_first_text(raw, "ktmc", "topicName")[:160],
            block_level=_first_text(raw, "bklxdjmc", "bklx", "blockLevel")[:120],
            teaching_class_student_count=_parse_int(_first_text(raw, "jxbrs", "teachingClassStudentCount")),
            credits=_parse_float(_first_text(raw, "xf", "credits", "credit")),
            student_count=_parse_int(_first_text(raw, "xkrs", "studentCount", "student_count")),
            raw_text=raw_text[:1600],
            raw_json={"row": dict(raw), "context": context},
            source_url=source_url,
        )
        items.append(item)
    return _dedupe_schedule_items(items)


def _dedupe_schedule_items(items: list[AcademicCourseScheduleItem]) -> list[AcademicCourseScheduleItem]:
    seen: set[tuple[Any, ...]] = set()
    unique_items: list[AcademicCourseScheduleItem] = []
    for item in items:
        key = (
            item.course_code.casefold(),
            item.course_name.casefold(),
            item.teaching_class_name.casefold(),
            item.time_text.casefold(),
            item.weeks_text.casefold(),
            item.weekday,
            item.section_text.casefold(),
            item.location.casefold(),
        )
        if key in seen:
            continue
        seen.add(key)
        unique_items.append(item)
    return unique_items


def _parse_schedule_response(
    response: httpx.Response,
    *,
    source_url: str,
) -> tuple[list[AcademicCourseScheduleItem], str]:
    content_type = response.headers.get("content-type", "").lower()
    text = response.text or ""
    if "application/json" in content_type or text.lstrip().startswith(("{", "[")):
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError):
            payload = None
        if payload is not None:
            items = _parse_schedule_items_from_json(payload, source_url)
            if items:
                return items, "json"

    items = _parse_schedule_items_from_html(text, source_url)
    if items:
        return items, "html"
    return [], "empty"


def _semester_year_start(semester: dict[str, Any]) -> int:
    name = str(semester.get("name") or "")
    match = re.search(r"(20\d{2})\s*[-—]\s*(20\d{2})", name)
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
    return {
        "Accept": accept,
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": str(client.base_url).rstrip("/"),
        "Referer": str(client.base_url).rstrip("/") + ZF_TEACHER_TIMETABLE_INDEX_PATH,
    }


def _build_timetable_form(term_params: dict[str, str], field_keys: list[str]) -> dict[str, Any]:
    form: dict[str, Any] = {
        **term_params,
        "kzlx": "ck",
        "djsktkb": "0",
        "xsdm": "",
        "ccdm": "",
        "xsewkbnr": "0",
    }
    keys = field_keys or ZF_TIMETABLE_FIELD_KEYS
    for key in keys:
        if key:
            form[f"xszd[{key}]"] = "true"
    for key in ZF_OPTIONAL_FALSE_FIELD_KEYS:
        form[f"xszd[{key}]"] = "false"
    return form


async def _fetch_timetable_field_keys(
    client: httpx.AsyncClient,
    sources: list[dict[str, Any]],
) -> list[str]:
    try:
        response = await client.post(
            ZF_TIMETABLE_FIELD_PATH,
            data={"kbzl": "jsgr", "doType": "query"},
            headers=_ajax_headers(client, accept="*/*"),
        )
        payload: Any = None
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError):
            payload = None
        field_keys = _field_keys_from_response(payload)
        sources.append(
            {
                "path": ZF_TIMETABLE_FIELD_PATH,
                "method": "POST",
                "status_code": response.status_code,
                "parser": "field_definitions" if field_keys else "empty",
                "field_keys": field_keys[:32],
                "field_count": len(field_keys),
                "url": str(response.url),
            }
        )
        selected_keys: list[str] = []
        for key in [*field_keys, *ZF_TIMETABLE_FIELD_KEYS]:
            if key and key not in ZF_OPTIONAL_FALSE_FIELD_KEYS and key not in selected_keys:
                selected_keys.append(key)
        return selected_keys
    except httpx.HTTPError as exc:
        sources.append(
            {
                "path": ZF_TIMETABLE_FIELD_PATH,
                "method": "POST",
                "status": "failed",
                "message": str(exc)[:180],
            }
        )
        return ZF_TIMETABLE_FIELD_KEYS


async def _fetch_supplemental_timetable_sources(
    client: httpx.AsyncClient,
    *,
    term_params: dict[str, str],
    field_keys: list[str],
    sources: list[dict[str, Any]],
) -> None:
    supplemental_requests = [
        (
            ZF_TIMETABLE_WEEK_SLOTS_PATH,
            {**term_params, "xqh_id": "1", "xsewkbnr": "0"},
            "week_slots",
        ),
        (
            ZF_TIMETABLE_SECTION_SLOTS_PATH,
            {**term_params, "xqh_id": "1"},
            "section_slots",
        ),
        (
            ZF_LAB_TIMETABLE_LIST_PATH,
            {
                **term_params,
                "kzlx": "ck",
                "djsktkb": "0",
                "xsewkbnr": "0",
            },
            "lab_list",
        ),
        (
            ZF_LAB_TIMETABLE_QUERY_PATH,
            {
                **_build_timetable_form(term_params, field_keys),
                "_search": "false",
                "nd": str(int(china_now().timestamp() * 1000)),
                "queryModel.showCount": "1000",
                "queryModel.currentPage": "1",
                "queryModel.sortName": "",
                "queryModel.sortOrder": "asc",
                "time": "5",
            },
            "lab_timetable",
        ),
    ]
    for path, form, parser_name in supplemental_requests:
        try:
            response = await client.post(path, data=form, headers=_ajax_headers(client))
            payload: Any = None
            try:
                payload = response.json()
            except (json.JSONDecodeError, ValueError):
                payload = None
            rows = []
            if isinstance(payload, dict):
                for key in ("kbList", "items", "rows"):
                    if isinstance(payload.get(key), list):
                        rows = payload[key]
                        break
            elif isinstance(payload, list):
                rows = payload
            sources.append(
                {
                    "path": path,
                    "method": "POST",
                    "params": term_params,
                    "status_code": response.status_code,
                    "parser": parser_name,
                    "item_count": len(rows),
                    "url": str(response.url),
                }
            )
        except httpx.HTTPError as exc:
            sources.append(
                {
                    "path": path,
                    "method": "POST",
                    "params": term_params,
                    "status": "failed",
                    "parser": parser_name,
                    "message": str(exc)[:180],
                }
            )


async def _fetch_teacher_timetable(
    client: httpx.AsyncClient,
    semester: dict[str, Any],
) -> tuple[list[AcademicCourseScheduleItem], list[dict[str, Any]]]:
    sources: list[dict[str, Any]] = []

    try:
        response = await client.get(
            ZF_TEACHER_TIMETABLE_INDEX_PATH,
            headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
        )
        source_url = str(response.url)
        items, parser = _parse_schedule_response(response, source_url=source_url)
        sources.append(
            {
                "path": ZF_TEACHER_TIMETABLE_INDEX_PATH,
                "method": "GET",
                "status_code": response.status_code,
                "parser": parser,
                "item_count": len(items),
                "url": source_url,
            }
        )
        if items:
            return items, sources
    except httpx.HTTPError as exc:
        sources.append(
            {
                "path": ZF_TEACHER_TIMETABLE_INDEX_PATH,
                "method": "GET",
                "status": "failed",
                "message": str(exc)[:180],
            }
        )

    field_keys = await _fetch_timetable_field_keys(client, sources)
    for term_params in _term_param_candidates(semester):
        form = _build_timetable_form(term_params, field_keys)
        try:
            response = await client.post(
                ZF_TEACHER_TIMETABLE_QUERY_PATH,
                data=form,
                headers=_ajax_headers(client),
            )
            source_url = str(response.url)
            items, parser = _parse_schedule_response(response, source_url=source_url)
            sources.append(
                {
                    "path": ZF_TEACHER_TIMETABLE_QUERY_PATH,
                    "method": "POST",
                    "params": term_params,
                    "status_code": response.status_code,
                    "parser": parser,
                    "field_count": len(field_keys),
                    "item_count": len(items),
                    "url": source_url,
                }
            )
            if items:
                await _fetch_supplemental_timetable_sources(
                    client,
                    term_params=term_params,
                    field_keys=field_keys,
                    sources=sources,
                )
                return items, sources
        except httpx.HTTPError as exc:
            sources.append(
                {
                    "path": ZF_TEACHER_TIMETABLE_QUERY_PATH,
                    "method": "POST",
                    "params": term_params,
                    "status": "failed",
                    "message": str(exc)[:180],
                }
            )

    return [], sources


def _load_current_semester(conn, teacher_id: int, today: date) -> dict[str, Any] | None:
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


def _course_group_key(item: AcademicCourseScheduleItem) -> str:
    if item.course_code:
        return f"code:{item.course_code.casefold()}"
    return f"name:{item.course_name.casefold()}"


def _normalize_course_match_text(value: Any) -> str:
    normalized = _normalize_space(value).casefold()
    return re.sub(r"[\s\-_—–·•:：,，;；/／\\（）()【】\[\]《》<>]+", "", normalized)


def _course_description(item: AcademicCourseScheduleItem, schedule_count: int) -> str:
    pieces = [
        f"从教务系统同步：{item.course_name}",
        f"课程号 {item.course_code}" if item.course_code else "",
        f"共同步 {schedule_count} 条上课安排",
        "请继续补充课程目标、教材、课堂设置和本平台班级绑定后再用于正式开课。",
    ]
    return "；".join(part for part in pieces if part)


def _course_row_with_match(row: Any, match_mode: str) -> dict[str, Any]:
    row_dict = dict(row)
    row_dict["_academic_match_mode"] = match_mode
    return row_dict


def _find_existing_course(
    conn,
    teacher_id: int,
    item: AcademicCourseScheduleItem,
) -> tuple[dict[str, Any] | None, str, int]:
    if item.course_code:
        row = conn.execute(
            """
            SELECT *
            FROM courses
            WHERE created_by_teacher_id = ?
              AND academic_source = ?
              AND academic_course_code = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (int(teacher_id), ACADEMIC_COURSE_SOURCE, item.course_code),
        ).fetchone()
        if row:
            return _course_row_with_match(row, "academic_code"), "academic_code", 0
    exact_rows = conn.execute(
        """
        SELECT *
        FROM courses
        WHERE created_by_teacher_id = ?
          AND name = ? COLLATE NOCASE
        ORDER BY id DESC
        """,
        (int(teacher_id), item.course_name),
    ).fetchall()
    if len(exact_rows) == 1:
        return _course_row_with_match(exact_rows[0], "exact_name"), "exact_name", 0
    if len(exact_rows) > 1:
        return None, "ambiguous_name", len(exact_rows)

    target_name = _normalize_course_match_text(item.course_name)
    if target_name:
        rows = conn.execute(
            """
            SELECT *
            FROM courses
            WHERE created_by_teacher_id = ?
            ORDER BY id DESC
            """,
            (int(teacher_id),),
        ).fetchall()
        normalized_matches = [
            row
            for row in rows
            if _normalize_course_match_text(row["name"]) == target_name
        ]
        if len(normalized_matches) == 1:
            return _course_row_with_match(normalized_matches[0], "normalized_name"), "normalized_name", 0
        if len(normalized_matches) > 1:
            return None, "ambiguous_name", len(normalized_matches)

    return None, "new", 0


def _course_metadata(
    *,
    semester: dict[str, Any],
    items: list[AcademicCourseScheduleItem],
    source_summary: list[dict[str, Any]],
) -> dict[str, Any]:
    locations = sorted({item.location for item in items if item.location})
    teaching_classes = sorted({item.teaching_class_name for item in items if item.teaching_class_name})
    weeks = sorted({item.weeks_text for item in items if item.weeks_text})
    classroom_types = sorted({item.classroom_type for item in items if item.classroom_type})
    teacher_names = sorted({item.teacher_name for item in items if item.teacher_name})
    return {
        "source": ACADEMIC_COURSE_SOURCE,
        "semester_id": int(semester["id"]),
        "semester_name": str(semester.get("name") or ""),
        "schedule_item_count": len(items),
        "locations": locations[:24],
        "teaching_classes": teaching_classes[:24],
        "classroom_types": classroom_types[:12],
        "teacher_names": teacher_names[:8],
        "weeks": weeks[:24],
        "source_summary": source_summary[-8:],
        "follow_up_items": FOLLOW_UP_ITEMS,
        "synced_at": _now_iso(),
    }


def _find_sync_item_id(
    conn,
    *,
    teacher_id: int,
    semester_id: int,
    course_id: int,
    item: AcademicCourseScheduleItem,
) -> int | None:
    row = conn.execute(
        """
        SELECT id
        FROM teacher_academic_course_sync_items
        WHERE teacher_id = ?
          AND semester_id = ?
          AND course_id = ?
          AND course_name = ?
          AND course_code = ?
          AND teaching_class_name = ?
          AND weeks_text = ?
          AND COALESCE(weekday, -1) = COALESCE(?, -1)
          AND section_text = ?
          AND location = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (
            int(teacher_id),
            int(semester_id),
            int(course_id),
            item.course_name,
            item.course_code,
            item.teaching_class_name,
            item.weeks_text,
            item.weekday,
            item.section_text,
            item.location,
        ),
    ).fetchone()
    return int(row["id"]) if row else None


def _insert_academic_occurrences(
    conn,
    *,
    teacher_id: int,
    semester: dict[str, Any],
    course_id: int,
    sync_item_id: int | None,
    item: AcademicCourseScheduleItem,
    synced_at: str,
) -> int:
    if item.weekday is None:
        return 0
    week_numbers = _parse_week_numbers(item.weeks_text)
    if not week_numbers:
        return 0
    section_start, section_end, section_count = _parse_section_range(item.section_text)
    is_non_periodic = _is_non_periodic_weeks(item.weeks_text, week_numbers)
    count = 0
    for week_index in week_numbers:
        session_date = _date_for_academic_week(
            semester,
            week_index=week_index,
            weekday=int(item.weekday),
        )
        if not session_date:
            continue
        note_parts = []
        if is_non_periodic:
            note_parts.append("教务周次不是完整连续周循环")
        if item.course_note:
            note_parts.append(item.course_note)
        if get_configured_db_engine() == "postgres":
            insert_verb = "INSERT INTO"
            conflict_clause = """
            ON CONFLICT (
                teacher_id, semester_id, course_id, teaching_class_name,
                session_date, section_text, location
            ) DO NOTHING
            """
        else:
            insert_verb = "INSERT OR IGNORE INTO"
            conflict_clause = ""
        cursor = conn.execute(
            f"""
            {insert_verb} teacher_academic_course_session_occurrences (
                teacher_id, semester_id, course_id, sync_item_id,
                academic_year, academic_term, course_name, course_code,
                teaching_class_name, class_composition, session_date,
                week_index, weekday, weekday_label, section_text,
                section_start, section_end, section_count, time_text,
                weeks_text, campus, campus_id, location, classroom_id,
                classroom_code, classroom_type, schedule_source,
                schedule_status, is_non_periodic, schedule_note,
                raw_json, synced_at, updated_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP
            )
            {conflict_clause}
            """,
            (
                int(teacher_id),
                int(semester["id"]),
                int(course_id),
                sync_item_id,
                item.academic_year or str(semester.get("name") or ""),
                item.academic_term or str(semester.get("term_number") or ""),
                item.course_name,
                item.course_code,
                item.teaching_class_name,
                item.class_composition,
                session_date,
                int(week_index),
                int(item.weekday),
                item.weekday_label or _weekday_label(item.weekday),
                item.section_text,
                section_start,
                section_end,
                section_count,
                item.time_text,
                item.weeks_text,
                item.campus,
                item.campus_id,
                item.location,
                item.classroom_id,
                item.classroom_code,
                item.classroom_type,
                SCHEDULE_SOURCE_ACADEMIC_SYNC,
                "scheduled",
                1 if is_non_periodic else 0,
                "；".join(part for part in note_parts if part),
                _json_dumps(
                    {
                        "source_url": item.source_url,
                        "sync_item_id": sync_item_id,
                        "raw": item.raw_json or {},
                    }
                ),
                synced_at,
            ),
        )
        count += int(cursor.rowcount or 0)
    return count


def _sync_existing_offering_academic_sessions(
    conn,
    *,
    teacher_id: int,
    semester: dict[str, Any],
    course_ids: list[int],
    synced_at: str,
) -> tuple[int, list[str]]:
    normalized_course_ids = sorted({int(course_id) for course_id in course_ids if int(course_id) > 0})
    if not normalized_course_ids:
        return 0, []
    placeholders = ",".join("?" for _ in normalized_course_ids)
    rows = conn.execute(
        f"""
        SELECT o.*,
               c.name AS course_name,
               cl.name AS class_name,
               cl.department AS class_department,
               cl.description AS class_description
        FROM class_offerings o
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        WHERE o.teacher_id = ?
          AND o.semester_id = ?
          AND o.course_id IN ({placeholders})
        ORDER BY o.id
        """,
        (int(teacher_id), int(semester["id"]), *normalized_course_ids),
    ).fetchall()
    if not rows:
        return 0, []

    lesson_map = load_course_lessons_by_course_id(conn, normalized_course_ids)
    updated_count = 0
    warnings: list[str] = []
    semester_start_date = parse_date_input(semester.get("start_date"))

    for row in rows:
        offering = dict(row)
        class_row = {
            "name": offering.get("class_name") or "",
            "department": offering.get("class_department") or "",
            "description": offering.get("class_description") or "",
        }
        selected_class, occurrences, selection_warnings, _ = select_academic_teaching_class_for_offering(
            conn,
            teacher_id=teacher_id,
            semester_id=int(semester["id"]),
            course_id=int(offering["course_id"]),
            class_row=class_row,
            preferred_teaching_class_name=str(offering.get("academic_teaching_class_name") or ""),
        )
        if selection_warnings:
            warnings.extend(
                f"{offering.get('course_name') or '课程'} / {offering.get('class_name') or '班级'}：{message}"
                for message in selection_warnings
            )
            continue
        if not occurrences:
            continue

        plan = build_academic_offering_session_plan(
            course_lessons=lesson_map.get(int(offering["course_id"]), []),
            academic_occurrences=occurrences,
            semester_start_date=semester_start_date,
            course_name=str(offering.get("course_name") or ""),
            teaching_class_name=selected_class,
        )
        replace_offering_sessions(
            conn,
            offering_id=int(offering["id"]),
            sessions=plan["sessions"],
        )
        conn.execute(
            """
            UPDATE class_offerings
            SET schedule_source = ?,
                academic_teaching_class_name = ?,
                academic_schedule_sync_at = ?,
                academic_schedule_sync_message = ?,
                schedule_info = ?,
                first_class_date = ?,
                weekly_schedule_json = ?
            WHERE id = ? AND teacher_id = ?
            """,
            (
                SCHEDULE_SOURCE_ACADEMIC_SYNC,
                selected_class,
                synced_at,
                f"已同步教务实际排课 {plan.get('session_count') or 0} 次。",
                plan.get("schedule_info") or "",
                plan.get("first_class_date") or "",
                "[]",
                int(offering["id"]),
                int(teacher_id),
            ),
        )
        warnings.extend(
            f"{offering.get('course_name') or '课程'} / {offering.get('class_name') or '班级'}：{message}"
            for message in plan.get("warnings", [])
        )
        updated_count += 1

    return updated_count, warnings


def _upsert_courses_and_schedule_items(
    conn,
    *,
    teacher_id: int,
    semester: dict[str, Any],
    items: list[AcademicCourseScheduleItem],
    source_summary: list[dict[str, Any]],
) -> dict[str, Any]:
    grouped: "OrderedDict[str, list[AcademicCourseScheduleItem]]" = OrderedDict()
    for item in items:
        grouped.setdefault(_course_group_key(item), []).append(item)

    created_count = 0
    updated_count = 0
    occurrence_count = 0
    affected_course_ids: list[int] = []
    course_results: list[dict[str, Any]] = []
    warnings: list[str] = []
    synced_at = _now_iso()
    sync_message = "已同步本学期教务课表；请继续补充教材、课堂设置和本平台班级绑定。"

    begin_immediate_transaction(conn)
    conn.execute(
        "DELETE FROM teacher_academic_course_sync_items WHERE teacher_id = ? AND semester_id = ?",
        (int(teacher_id), int(semester["id"])),
    )
    conn.execute(
        "DELETE FROM teacher_academic_course_session_occurrences WHERE teacher_id = ? AND semester_id = ?",
        (int(teacher_id), int(semester["id"])),
    )

    for group_items in grouped.values():
        first_item = group_items[0]
        existing, match_mode, ambiguous_count = _find_existing_course(conn, teacher_id, first_item)
        if ambiguous_count > 0:
            warnings.append(
                f"课程“{first_item.course_name}”在本系统已有 {ambiguous_count} 个相似课程，未自动绑定其中任意一个，已创建独立教务同步课程。"
            )
        credits = next((item.credits for item in group_items if item.credits > 0), 0.0)
        total_hours = max(
            (
                _parse_total_hours(
                    item.course_total_hours_text
                    or item.total_hours_text
                    or item.course_hour_text
                )
                for item in group_items
            ),
            default=0,
        )
        department = normalize_department(infer_department_from_text(first_item.course_name, first_item.raw_text))
        org_scope = apply_teacher_scope_to_org(conn, teacher_id, department=department)
        metadata = _course_metadata(semester=semester, items=group_items, source_summary=source_summary)

        if existing:
            course_id = int(existing["id"])
            updates: dict[str, Any] = {
                "academic_source": ACADEMIC_COURSE_SOURCE,
                "academic_course_code": first_item.course_code,
                "academic_sync_at": synced_at,
                "academic_sync_message": sync_message,
                "academic_metadata_json": _json_dumps(metadata),
            }
            if not str(existing.get("department") or "").strip() and department:
                updates["department"] = department
            if not str(existing.get("school_code") or "").strip():
                updates["school_code"] = org_scope["school_code"]
            if not str(existing.get("school_name") or "").strip():
                updates["school_name"] = org_scope["school_name"]
            if not str(existing.get("college") or "").strip():
                updates["college"] = org_scope["college"]
            if not str(existing.get("description") or "").strip():
                updates["description"] = _course_description(first_item, len(group_items))
            if not float(existing.get("credits") or 0) and credits > 0:
                updates["credits"] = credits
            if not int(existing.get("total_hours") or 0) and total_hours > 0:
                updates["total_hours"] = total_hours

            assignments = ", ".join(f"{column} = ?" for column in updates)
            conn.execute(
                f"UPDATE courses SET {assignments} WHERE id = ? AND created_by_teacher_id = ?",
                [*updates.values(), course_id, int(teacher_id)],
            )
            updated_count += 1
            action = "updated"
        else:
            course_id = execute_insert_returning_id(
                conn,
                """
                INSERT INTO courses (
                    name, description, sect_name, department, credits, total_hours, created_by_teacher_id,
                    school_code, school_name, college,
                    academic_source, academic_course_code, academic_sync_at, academic_sync_message,
                    academic_metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    first_item.course_name,
                    _course_description(first_item, len(group_items)),
                    normalize_course_sect_name("", course_name=first_item.course_name),
                    department,
                    credits,
                    total_hours,
                    int(teacher_id),
                    org_scope["school_code"],
                    org_scope["school_name"],
                    org_scope["college"],
                    ACADEMIC_COURSE_SOURCE,
                    first_item.course_code,
                    synced_at,
                    sync_message,
                    _json_dumps(metadata),
                ),
            )
            created_count += 1
            action = "created_after_ambiguous_name" if match_mode == "ambiguous_name" else "created"

        if course_id not in affected_course_ids:
            affected_course_ids.append(course_id)
        group_occurrence_count = 0
        for item in group_items:
            if get_configured_db_engine() == "postgres":
                insert_verb = "INSERT INTO"
                conflict_clause = """
                ON CONFLICT (
                    teacher_id, semester_id, course_code, teaching_class_name,
                    weeks_text, weekday, section_text, location
                ) DO NOTHING
                RETURNING id
                """
            else:
                insert_verb = "INSERT OR IGNORE INTO"
                conflict_clause = ""
            cursor = conn.execute(
                f"""
                {insert_verb} teacher_academic_course_sync_items (
                    teacher_id, semester_id, course_id,
                    academic_year, academic_year_name, academic_term, academic_term_name,
                    teacher_name, teacher_org_id, teacher_org_name,
                    course_name, course_code, teaching_class_name, time_text,
                    weeks_text, weekday, weekday_label, section_text,
                    campus, campus_id, location, classroom_id, classroom_code, classroom_type, class_composition,
                    course_nature, exam_method, exam_mode, course_hour_text, credits, student_count,
                    weekly_hours_text, total_hours_text, course_total_hours_text,
                    major_direction, course_note, online_info, course_topic_name, block_level,
                    teaching_class_student_count,
                    raw_text, raw_json, source_url, synced_at, updated_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP
                )
                {conflict_clause}
                """,
                (
                    int(teacher_id),
                    int(semester["id"]),
                    course_id,
                    item.academic_year,
                    item.academic_year_name,
                    item.academic_term,
                    item.academic_term_name,
                    item.teacher_name,
                    item.teacher_org_id,
                    item.teacher_org_name,
                    item.course_name,
                    item.course_code,
                    item.teaching_class_name,
                    item.time_text,
                    item.weeks_text,
                    item.weekday,
                    item.weekday_label,
                    item.section_text,
                    item.campus,
                    item.campus_id,
                    item.location,
                    item.classroom_id,
                    item.classroom_code,
                    item.classroom_type,
                    item.class_composition,
                    item.course_nature,
                    item.exam_method,
                    item.exam_mode,
                    item.course_hour_text,
                    float(item.credits or 0),
                    int(item.student_count or 0),
                    item.weekly_hours_text,
                    item.total_hours_text,
                    item.course_total_hours_text,
                    item.major_direction,
                    item.course_note,
                    item.online_info,
                    item.course_topic_name,
                    item.block_level,
                    int(item.teaching_class_student_count or 0),
                    item.raw_text,
                    _json_dumps(item.raw_json or {}),
                    item.source_url,
                    synced_at,
                ),
            )
            if get_configured_db_engine() == "postgres":
                inserted_row = cursor.fetchone()
                sync_item_id = int(inserted_row["id"]) if inserted_row else _find_sync_item_id(
                    conn,
                    teacher_id=teacher_id,
                    semester_id=int(semester["id"]),
                    course_id=course_id,
                    item=item,
                )
            else:
                sync_item_id = _find_sync_item_id(
                    conn,
                    teacher_id=teacher_id,
                    semester_id=int(semester["id"]),
                    course_id=course_id,
                    item=item,
                )
            group_occurrence_count += _insert_academic_occurrences(
                conn,
                teacher_id=teacher_id,
                semester=semester,
                course_id=course_id,
                sync_item_id=sync_item_id,
                item=item,
                synced_at=synced_at,
            )

        occurrence_count += group_occurrence_count
        course_results.append(
            {
                "course_id": course_id,
                "course_name": first_item.course_name,
                "course_code": first_item.course_code,
                "schedule_item_count": len(group_items),
                "occurrence_count": group_occurrence_count,
                "action": action,
                "match_mode": match_mode,
                "ambiguous_existing_count": ambiguous_count,
            }
        )

    offering_update_count, offering_warnings = _sync_existing_offering_academic_sessions(
        conn,
        teacher_id=teacher_id,
        semester=semester,
        course_ids=affected_course_ids,
        synced_at=synced_at,
    )
    warnings.extend(offering_warnings)

    return {
        "created_count": created_count,
        "updated_count": updated_count,
        "course_count": len(grouped),
        "schedule_item_count": len(items),
        "occurrence_count": occurrence_count,
        "offering_update_count": offering_update_count,
        "courses": course_results,
        "warnings": warnings,
    }


async def sync_current_teacher_courses_from_academic_system(teacher_id: int) -> dict[str, Any]:
    with get_db_connection() as conn:
        access_payload = load_teacher_academic_access_method(conn, teacher_id, school_code="gxufl")
        semester = _load_current_semester(conn, teacher_id, china_now().date())

    if not access_payload:
        return {
            "status": "missing_credential",
            "message": "请先在系统设置中配置并验证教务系统账号，再同步教务课程。",
        }

    if not semester:
        semester_result = await prepare_current_semester_from_academic_system(teacher_id)
        if semester_result.get("status") != "success":
            return {
                "status": "no_current_semester",
                "message": semester_result.get("message") or "未能从教务系统识别当前学期，暂不能同步课程。",
                "source_summary": semester_result.get("source_summary") or [],
            }
        with get_db_connection() as conn:
            semester = _load_semester_by_id(conn, teacher_id, int(semester_result["semester_id"]))

    if not semester:
        return {
            "status": "no_current_semester",
            "message": "请先新建或从教务系统同步当前学期，再同步课程课表。",
        }

    try:
        async with open_authenticated_academic_client(access_payload) as (client, profile, login_result):
            items, source_summary = await _fetch_teacher_timetable(client, semester)
    except (ValueError, httpx.HTTPError) as exc:
        return {
            "status": "academic_login_failed",
            "message": f"教务系统登录或课表访问失败：{str(exc)[:180]}",
        }

    if not items:
        return {
            "status": "no_courses",
            "message": "已登录教务系统，但没有解析到当前学期课表。请确认教务系统课表页面已能查询到本学期课程。",
            "semester_id": int(semester["id"]),
            "semester_name": str(semester.get("name") or ""),
            "source_summary": source_summary,
        }

    with get_db_connection() as conn:
        try:
            result = _upsert_courses_and_schedule_items(
                conn,
                teacher_id=teacher_id,
                semester=semester,
                items=items,
                source_summary=source_summary,
            )
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
            raise

    warnings = result.get("warnings") or []

    return {
        "status": "success",
        "message": (
            f"已从教务系统同步 {result['course_count']} 门课程、{result['schedule_item_count']} 条课表安排，"
            f"展开为 {result.get('occurrence_count') or 0} 次真实课次。"
            f"已自动更新 {result.get('offering_update_count') or 0} 个已开设课堂的时间轴。"
            "请继续补充教材、课堂设置和本平台班级绑定。"
        ),
        "semester_id": int(semester["id"]),
        "semester_name": str(semester.get("name") or ""),
        "created_count": result["created_count"],
        "updated_count": result["updated_count"],
        "course_count": result["course_count"],
        "schedule_item_count": result["schedule_item_count"],
        "occurrence_count": result.get("occurrence_count") or 0,
        "offering_update_count": result.get("offering_update_count") or 0,
        "courses": result["courses"],
        "warnings": warnings,
        "follow_up_items": [*warnings[:3], *FOLLOW_UP_ITEMS],
        "source_summary": source_summary,
        "login_display_name": login_result.get("display_name") if isinstance(login_result, dict) else "",
        "school_name": profile.school_name,
    }


def summarize_academic_course_sync_item(row: Any) -> dict[str, Any]:
    item = dict(row)
    weekday_label = str(item.get("weekday_label") or "").strip()
    if not weekday_label and item.get("weekday") is not None:
        weekday_label = _weekday_label(int(item["weekday"]))
    return {
        "id": int(item["id"]),
        "semester_id": int(item["semester_id"]) if item.get("semester_id") else None,
        "course_id": int(item["course_id"]) if item.get("course_id") else None,
        "academic_year": str(item.get("academic_year") or ""),
        "academic_year_name": str(item.get("academic_year_name") or ""),
        "academic_term": str(item.get("academic_term") or ""),
        "academic_term_name": str(item.get("academic_term_name") or ""),
        "teacher_name": str(item.get("teacher_name") or ""),
        "teacher_org_id": str(item.get("teacher_org_id") or ""),
        "teacher_org_name": str(item.get("teacher_org_name") or ""),
        "course_name": str(item.get("course_name") or ""),
        "course_code": str(item.get("course_code") or ""),
        "teaching_class_name": str(item.get("teaching_class_name") or ""),
        "time_text": str(item.get("time_text") or ""),
        "weeks_text": str(item.get("weeks_text") or ""),
        "weekday": int(item["weekday"]) if item.get("weekday") is not None else None,
        "weekday_label": weekday_label,
        "section_text": str(item.get("section_text") or ""),
        "campus": str(item.get("campus") or ""),
        "campus_id": str(item.get("campus_id") or ""),
        "location": str(item.get("location") or ""),
        "classroom_id": str(item.get("classroom_id") or ""),
        "classroom_code": str(item.get("classroom_code") or ""),
        "classroom_type": str(item.get("classroom_type") or ""),
        "class_composition": str(item.get("class_composition") or ""),
        "course_nature": str(item.get("course_nature") or ""),
        "exam_method": str(item.get("exam_method") or ""),
        "exam_mode": str(item.get("exam_mode") or ""),
        "course_hour_text": str(item.get("course_hour_text") or ""),
        "weekly_hours_text": str(item.get("weekly_hours_text") or ""),
        "total_hours_text": str(item.get("total_hours_text") or ""),
        "course_total_hours_text": str(item.get("course_total_hours_text") or ""),
        "major_direction": str(item.get("major_direction") or ""),
        "course_note": str(item.get("course_note") or ""),
        "online_info": str(item.get("online_info") or ""),
        "course_topic_name": str(item.get("course_topic_name") or ""),
        "block_level": str(item.get("block_level") or ""),
        "teaching_class_student_count": int(item.get("teaching_class_student_count") or 0),
        "credits": float(item.get("credits") or 0),
        "student_count": int(item.get("student_count") or 0),
        "source_url": str(item.get("source_url") or ""),
        "synced_at": str(item.get("synced_at") or ""),
    }


def build_academic_course_metadata(raw_value: Any) -> dict[str, Any]:
    metadata = _safe_json_loads(raw_value, {})
    return metadata if isinstance(metadata, dict) else {}
