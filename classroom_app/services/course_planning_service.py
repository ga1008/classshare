from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from heapq import heappop, heappush
from typing import Any, Iterable

from .academic_service import china_today, parse_date_input, truncate_text
from .learning_progress_service import normalize_course_sect_name


MAX_COURSE_LESSON_COUNT = 120
MAX_WEEKLY_SLOT_COUNT = 7
MAX_LESSON_TITLE_LENGTH = 120
MAX_LESSON_CONTENT_LENGTH = 4000
MAX_TOTAL_HOURS = 512
MAX_SECTION_COUNT = 12

WEEKDAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
HOME_TIMELINE_ENTRY_ORDER = "home"


class CoursePlanningError(ValueError):
    """Raised when course or offering planning data is invalid."""


def weekday_label(weekday: int) -> str:
    if 0 <= int(weekday) < len(WEEKDAY_LABELS):
        return WEEKDAY_LABELS[int(weekday)]
    return f"周{int(weekday) + 1}"


def _loads_json_value(raw_value: Any) -> Any:
    if raw_value is None or raw_value == "":
        return []
    if isinstance(raw_value, (list, tuple)):
        return list(raw_value)
    if isinstance(raw_value, dict):
        return raw_value
    try:
        return json.loads(str(raw_value))
    except (TypeError, json.JSONDecodeError) as exc:
        raise CoursePlanningError("JSON 数据格式不正确") from exc


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\u3000", " ").split()).strip()


def _normalize_multiline_text(value: Any) -> str:
    lines = [line.strip() for line in str(value or "").replace("\r\n", "\n").split("\n")]
    return "\n".join(line for line in lines if line)


def _parse_int(
    value: Any,
    *,
    field_name: str,
    minimum: int = 0,
    maximum: int | None = None,
    default: int = 0,
) -> int:
    if value in (None, ""):
        parsed = default
    else:
        try:
            parsed = int(str(value).strip())
        except (TypeError, ValueError) as exc:
            raise CoursePlanningError(f"{field_name}必须是整数") from exc

    if parsed < minimum:
        raise CoursePlanningError(f"{field_name}不能小于 {minimum}")
    if maximum is not None and parsed > maximum:
        raise CoursePlanningError(f"{field_name}不能大于 {maximum}")
    return parsed


def _parse_optional_positive_int(value: Any, *, field_name: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise CoursePlanningError(f"{field_name} value is invalid") from exc
    if parsed <= 0:
        raise CoursePlanningError(f"{field_name} must be greater than 0")
    return parsed


def normalize_total_hours(value: Any) -> int:
    return _parse_int(
        value,
        field_name="学时",
        minimum=0,
        maximum=MAX_TOTAL_HOURS,
        default=0,
    )


def normalize_course_lessons(
    raw_lessons: Any,
    *,
    require_items: bool = True,
) -> list[dict[str, Any]]:
    parsed = _loads_json_value(raw_lessons)
    if parsed in (None, ""):
        parsed = []
    if not isinstance(parsed, list):
        raise CoursePlanningError("课堂设置必须是数组")

    normalized_lessons: list[dict[str, Any]] = []
    for index, raw_item in enumerate(parsed, start=1):
        if not isinstance(raw_item, dict):
            raise CoursePlanningError(f"第 {index} 条课堂设置格式不正确")

        title = _normalize_text(raw_item.get("title") or raw_item.get("name"))
        content = _normalize_multiline_text(raw_item.get("content"))
        section_count = _parse_int(
            raw_item.get("section_count", raw_item.get("sections", 0)),
            field_name=f"第 {index} 条课堂设置的小节数",
            minimum=1,
            maximum=MAX_SECTION_COUNT,
            default=1,
        )
        learning_material_id = _parse_optional_positive_int(
            raw_item.get("learning_material_id"),
            field_name=f"lesson {index} learning material",
        )

        if not title and not content:
            continue
        if not title:
            raise CoursePlanningError(f"第 {index} 条课堂设置缺少课堂名称")
        if not content:
            raise CoursePlanningError(f"第 {index} 条课堂设置缺少上课内容")
        if len(title) > MAX_LESSON_TITLE_LENGTH:
            raise CoursePlanningError(
                f"第 {index} 条课堂名称不能超过 {MAX_LESSON_TITLE_LENGTH} 个字符"
            )
        if len(content) > MAX_LESSON_CONTENT_LENGTH:
            raise CoursePlanningError(
                f"第 {index} 条上课内容不能超过 {MAX_LESSON_CONTENT_LENGTH} 个字符"
            )

        normalized_lessons.append(
            {
                "order_index": len(normalized_lessons) + 1,
                "title": title,
                "content": content,
                "section_count": section_count,
                "source_type": _normalize_text(raw_item.get("source_type")) or "manual",
                "learning_material_id": learning_material_id,
            }
        )

    if len(normalized_lessons) > MAX_COURSE_LESSON_COUNT:
        raise CoursePlanningError(f"课堂设置最多只能保留 {MAX_COURSE_LESSON_COUNT} 条")
    if require_items and not normalized_lessons:
        raise CoursePlanningError("请至少保留一条课堂设置")
    return normalized_lessons


def normalize_weekly_schedule(
    raw_schedule: Any,
    *,
    first_class_date: date | None = None,
    require_items: bool = True,
) -> list[dict[str, Any]]:
    parsed = _loads_json_value(raw_schedule)
    if not isinstance(parsed, list):
        raise CoursePlanningError("每周上课安排必须是数组")

    normalized_slots: list[dict[str, Any]] = []
    seen_weekdays: set[int] = set()

    for index, raw_item in enumerate(parsed, start=1):
        if not isinstance(raw_item, dict):
            raise CoursePlanningError(f"第 {index} 条每周安排格式不正确")

        weekday = _parse_int(
            raw_item.get("weekday"),
            field_name=f"第 {index} 条每周安排的上课日",
            minimum=0,
            maximum=6,
        )
        if weekday in seen_weekdays:
            raise CoursePlanningError("每周安排中的上课日不能重复")

        section_count = _parse_int(
            raw_item.get("section_count", raw_item.get("sections", 0)),
            field_name=f"第 {index} 条每周安排的节数",
            minimum=1,
            maximum=MAX_SECTION_COUNT,
            default=1,
        )

        seen_weekdays.add(weekday)
        normalized_slots.append(
            {
                "weekday": weekday,
                "weekday_label": weekday_label(weekday),
                "section_count": section_count,
            }
        )

    if len(normalized_slots) > MAX_WEEKLY_SLOT_COUNT:
        raise CoursePlanningError(f"每周安排最多只能保留 {MAX_WEEKLY_SLOT_COUNT} 条")
    if require_items and not normalized_slots:
        raise CoursePlanningError("请至少配置一条每周上课安排")

    normalized_slots.sort(key=lambda item: (item["weekday"], item["section_count"]))

    if first_class_date and normalized_slots:
        first_weekday = int(first_class_date.weekday())
        if first_weekday not in {item["weekday"] for item in normalized_slots}:
            raise CoursePlanningError("第一次上课日期的星期必须包含在每周上课安排中")

    return normalized_slots


def summarize_weekly_schedule(slots: Iterable[dict[str, Any]]) -> str:
    parts = []
    for slot in slots:
        parts.append(f"{weekday_label(int(slot['weekday']))} {int(slot['section_count'])} 节")
    return " / ".join(parts)


def build_schedule_info_text(
    *,
    first_class_date: date | None,
    weekly_schedule: Iterable[dict[str, Any]],
    session_count: int = 0,
    end_date: date | None = None,
) -> str:
    parts: list[str] = []
    if first_class_date:
        parts.append(
            f"首次上课 {first_class_date.isoformat()} {weekday_label(first_class_date.weekday())}"
        )
    schedule_summary = summarize_weekly_schedule(weekly_schedule)
    if schedule_summary:
        parts.append(f"每周 {schedule_summary}")
    if session_count > 0:
        parts.append(f"共 {session_count} 次课")
    if end_date:
        parts.append(f"预计至 {end_date.isoformat()} 完成")
    return "；".join(parts)


def replace_course_lessons(
    conn: sqlite3.Connection,
    *,
    course_id: int,
    lessons: list[dict[str, Any]],
) -> None:
    conn.execute("DELETE FROM course_lessons WHERE course_id = ?", (course_id,))
    if not lessons:
        return

    conn.executemany(
        """
        INSERT INTO course_lessons (
            course_id,
            order_index,
            title,
            content,
            section_count,
            source_type,
            learning_material_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                course_id,
                int(item["order_index"]),
                item["title"],
                item["content"],
                int(item["section_count"]),
                item.get("source_type") or "manual",
                item.get("learning_material_id"),
            )
            for item in lessons
        ],
    )


def replace_offering_sessions(
    conn: sqlite3.Connection,
    *,
    offering_id: int,
    sessions: list[dict[str, Any]],
) -> None:
    conn.execute("DELETE FROM class_offering_sessions WHERE class_offering_id = ?", (offering_id,))
    if not sessions:
        return

    conn.executemany(
        """
        INSERT INTO class_offering_sessions (
            class_offering_id,
            course_lesson_id,
            order_index,
            title,
            content,
            section_count,
            slot_section_count,
            session_date,
            weekday,
            week_index,
            learning_material_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                offering_id,
                item.get("course_lesson_id"),
                int(item["order_index"]),
                item["title"],
                item["content"],
                int(item["section_count"]),
                int(item.get("slot_section_count") or item["section_count"]),
                item["session_date"],
                int(item["weekday"]),
                int(item.get("week_index") or 0),
                item.get("learning_material_id"),
            )
            for item in sessions
        ],
    )


def load_course_lessons_by_course_id(
    conn: sqlite3.Connection,
    course_ids: Iterable[int],
) -> dict[int, list[dict[str, Any]]]:
    normalized_course_ids = sorted({int(course_id) for course_id in course_ids if int(course_id) > 0})
    if not normalized_course_ids:
        return {}

    placeholders = ",".join("?" for _ in normalized_course_ids)
    rows = conn.execute(
        f"""
        SELECT id, course_id, order_index, title, content, section_count, source_type, learning_material_id
        FROM course_lessons
        WHERE course_id IN ({placeholders})
        ORDER BY course_id, order_index, id
        """,
        tuple(normalized_course_ids),
    ).fetchall()

    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        item = dict(row)
        item["section_count"] = int(item.get("section_count") or 0)
        item["order_index"] = int(item.get("order_index") or len(grouped[int(item["course_id"])]) + 1)
        item["learning_material_id"] = int(item["learning_material_id"]) if item.get("learning_material_id") else None
        grouped[int(item["course_id"])].append(item)
    return dict(grouped)


def serialize_course_row(
    row: Any,
    *,
    lessons: list[dict[str, Any]] | None = None,
    offering_count: int = 0,
) -> dict[str, Any]:
    item = dict(row)
    lesson_list = lessons or []
    total_hours = normalize_total_hours(item.get("total_hours"))
    planned_section_count = sum(int(lesson.get("section_count") or 0) for lesson in lesson_list)
    lesson_titles = [lesson["title"] for lesson in lesson_list if lesson.get("title")]
    description = str(item.get("description") or "").strip()

    if lesson_list and total_hours > 0 and planned_section_count == total_hours:
        coverage_status = "complete"
        coverage_label = "内容完整"
    elif lesson_list:
        coverage_status = "partial"
        coverage_label = "待校准"
    else:
        coverage_status = "empty"
        coverage_label = "待完善"

    item["description"] = description
    item["sect_name"] = normalize_course_sect_name(item.get("sect_name"), course_name=item.get("name"))
    item["credits"] = float(item.get("credits") or 0)
    item["total_hours"] = total_hours
    item["lesson_count"] = len(lesson_list)
    item["planned_section_count"] = planned_section_count
    item["offering_count"] = int(offering_count or 0)
    item["is_in_use"] = item["offering_count"] > 0
    item["coverage_status"] = coverage_status
    item["coverage_label"] = coverage_label
    item["lessons"] = lesson_list
    item["material_lesson_count"] = sum(1 for lesson in lesson_list if lesson.get("learning_material_id"))
    item["lesson_preview_titles"] = lesson_titles[:4]
    item["lesson_preview"] = [
        {
            "title": lesson["title"],
            "content_preview": truncate_text(lesson.get("content"), 88),
            "section_count": int(lesson.get("section_count") or 0),
            "learning_material_name": str(lesson.get("learning_material_name") or "").strip(),
        }
        for lesson in lesson_list[:3]
    ]
    item["description_preview"] = truncate_text(description, 150)
    item["hour_gap"] = total_hours - planned_section_count if total_hours > 0 else 0
    item["search_blob"] = " ".join(
        filter(
            None,
            [
                str(item.get("name") or "").strip(),
                str(item.get("sect_name") or "").strip(),
                description,
                " ".join(lesson_titles),
                " ".join(
                    str(lesson.get("learning_material_name") or "").strip()
                    for lesson in lesson_list
                    if lesson.get("learning_material_name")
                ),
                " ".join(
                    truncate_text(lesson.get("content"), 120) for lesson in lesson_list if lesson.get("content")
                ),
            ],
        )
    ).lower()
    return item


def _build_occurrence_slots(
    *,
    first_class_date: date,
    weekly_schedule: list[dict[str, Any]],
    required_count: int,
    semester_end_date: date | None,
) -> list[dict[str, Any]]:
    week_start = first_class_date - timedelta(days=first_class_date.weekday())
    heap: list[tuple[date, int, dict[str, Any]]] = []

    for order_index, slot in enumerate(weekly_schedule):
        candidate = week_start + timedelta(days=int(slot["weekday"]))
        while candidate < first_class_date:
            candidate += timedelta(days=7)
        heappush(heap, (candidate, order_index, slot))

    occurrences: list[dict[str, Any]] = []
    while heap and len(occurrences) < required_count:
        session_date, slot_order, slot = heappop(heap)
        if semester_end_date and session_date > semester_end_date:
            continue

        occurrences.append(
            {
                "session_date": session_date,
                "weekday": int(slot["weekday"]),
                "weekday_label": slot.get("weekday_label") or weekday_label(slot["weekday"]),
                "slot_section_count": int(slot["section_count"]),
            }
        )

        next_date = session_date + timedelta(days=7)
        if not semester_end_date or next_date <= semester_end_date:
            heappush(heap, (next_date, slot_order, slot))

    return occurrences


def _compute_week_index(session_date: date, semester_start_date: date | None) -> int:
    if not semester_start_date:
        return 0
    semester_monday = semester_start_date - timedelta(days=semester_start_date.weekday())
    return ((session_date - semester_monday).days // 7) + 1


def _build_relative_day_label(session_date: date | None, today: date) -> str:
    if not session_date:
        return "待定"

    delta_days = (session_date - today).days
    if delta_days == 0:
        return "今天"
    if delta_days == 1:
        return "明天"
    if delta_days == 2:
        return "后天"
    if delta_days == -1:
        return "昨天"
    if delta_days == -2:
        return "前天"
    if delta_days > 0:
        return f"{delta_days} 天后"
    return f"{abs(delta_days)} 天前"


def _build_month_day_label(session_date: date | None) -> str:
    if not session_date:
        return ""
    return f"{session_date.month}月{session_date.day}日"


def _build_timeline_weekday_label(*, week_index: int, weekday_text: str) -> str:
    weekday_text = str(weekday_text or "").strip()
    if week_index > 0 and weekday_text:
        return f"第{week_index}周{weekday_text}"
    if week_index > 0:
        return f"第{week_index}周"
    return weekday_text or "待排时间"


def _build_timeline_relative_date_label(*, relative_day_label: str, session_date: date | None) -> str:
    relative_text = str(relative_day_label or "").strip()
    month_day_label = _build_month_day_label(session_date)
    if relative_text and month_day_label:
        return f"{relative_text}（{month_day_label}）"
    return relative_text or month_day_label


def _build_session_status_label(
    *,
    progress_state: str,
    is_anchor: bool,
    timeline_status: str,
) -> str:
    if progress_state == "current":
        return "今天课程"
    if progress_state == "next":
        return "下一次课"
    if progress_state == "completed" and is_anchor and timeline_status == "completed":
        return "最近一次课"
    if progress_state == "completed":
        return "已完成"
    if is_anchor:
        return "焦点课程"
    return "待开始"


def _build_timeline_status_note(
    *,
    timeline_status: str,
    anchor_session: dict[str, Any] | None,
) -> str:
    if not anchor_session:
        return "暂未生成排课内容。"

    anchor_date = anchor_session.get("date_label") or anchor_session.get("session_date") or ""
    anchor_title = anchor_session.get("title") or "当前课程"
    if timeline_status == "current":
        return f"系统已自动定位到今天的课程：{anchor_title}。"
    if timeline_status == "next":
        return f"当前不是上课日，已吸附到下一次课：{anchor_date}。"
    return f"当前日期已晚于最后一次课，默认定位到最近一次课：{anchor_title}。"


def build_timeline_home_entry(
    home_material: dict[str, Any] | None = None,
    *,
    include_placeholder: bool = False,
) -> dict[str, Any] | None:
    material = dict(home_material or {})
    try:
        material_id = int(material.get("id") or 0)
    except (TypeError, ValueError):
        material_id = 0
    viewer_url = str(material.get("viewer_url") or "").strip()
    material_name = str(material.get("name") or "").strip()
    material_path = str(material.get("material_path") or "").strip()
    has_material = material_id > 0 and bool(viewer_url)

    if not has_material and not include_placeholder:
        return None

    title = "目录与课程简介" if has_material else "首页未配置"
    detail_title = "课程学习首页" if has_material else "课程学习首页未配置"
    detail_summary = (
        "从这里进入课程目录、课程简介与后续学习文档导航。"
        if has_material
        else "教师可为本课堂绑定一份 Markdown 首页文档，用于放置课程目录、学习说明和后续文档入口。"
    )

    return {
        "id": HOME_TIMELINE_ENTRY_ORDER,
        "course_lesson_id": None,
        "order_index": HOME_TIMELINE_ENTRY_ORDER,
        "entry_type": "home",
        "is_home_entry": True,
        "is_lesson_entry": False,
        "is_anchor": False,
        "progress_state": "home",
        "session_number_label": "首页",
        "relative_day_label": "开始学习",
        "month_day_label": "",
        "timeline_weekday_label": "课程入口",
        "timeline_relative_date_label": "目录与简介",
        "segment_title": title,
        "session_status_label": "课程入口" if has_material else "待配置",
        "task_status_label": "课程入口" if has_material else "待配置",
        "title": detail_title,
        "detail_title": detail_title,
        "content": detail_summary,
        "detail_content": detail_summary,
        "detail_lines": [detail_summary] if detail_summary else [],
        "detail_summary": detail_summary,
        "content_preview": detail_summary,
        "detail_meta": material_path or "尚未绑定首页文档",
        "detail_hint": "" if has_material else "首页文档不存在时，学生端不会显示首页入口按钮。",
        "date_label": "",
        "week_label": "",
        "section_count": 0,
        "slot_section_count": 0,
        "is_section_match": True,
        "learning_material_id": material_id if has_material else None,
        "learning_material": material if has_material else None,
        "learning_material_name": material_name if has_material else "",
        "learning_material_path": material_path if has_material else "",
        "learning_material_parent_id": material.get("parent_id") if has_material else None,
        "learning_material_viewer_url": viewer_url if has_material else "",
        "has_learning_material": has_material,
        "home_learning_material_id": material_id if has_material else None,
        "home_learning_material": material if has_material else None,
        "home_learning_material_name": material_name if has_material else "",
        "home_learning_material_path": material_path if has_material else "",
        "home_learning_material_viewer_url": viewer_url if has_material else "",
        "has_home_learning_material": has_material,
        "material_generation_task": None,
        "material_generation_status": "idle",
        "has_material_generation_in_progress": False,
    }


def _decorate_session_progress(
    sessions: list[dict[str, Any]],
    *,
    reference_date: date | None = None,
) -> dict[str, Any]:
    decorated_sessions = list(sessions)
    if not decorated_sessions:
        return {
            "sessions": [],
            "anchor_index": None,
            "anchor_session": None,
            "timeline_status": "empty",
            "timeline_status_label": "暂无排课",
        }

    today = reference_date or china_today()
    anchor_index = len(decorated_sessions) - 1
    exact_match_found = False
    future_match_found = False

    for index, item in enumerate(decorated_sessions):
        session_date = parse_date_input(item.get("session_date"), "上课日期")
        if not session_date:
            continue
        if session_date == today and not exact_match_found:
            exact_match_found = True
            anchor_index = index
            break
        if session_date > today and not future_match_found:
            future_match_found = True
            anchor_index = index
            break

    if exact_match_found:
        timeline_status = "current"
        timeline_status_label = "当前上课进度"
    elif future_match_found:
        timeline_status = "next"
        timeline_status_label = "已定位到下一次课"
    else:
        timeline_status = "completed"
        timeline_status_label = "本轮课程已完成"

    for index, item in enumerate(decorated_sessions):
        session_date = parse_date_input(item.get("session_date"), "上课日期")
        state = "upcoming"
        if session_date:
            if session_date < today:
                state = "completed"
            elif session_date == today:
                state = "current"
            elif index == anchor_index and timeline_status == "next":
                state = "next"
        item["progress_state"] = state
        item["is_anchor"] = index == anchor_index

    return {
        "sessions": decorated_sessions,
        "anchor_index": anchor_index,
        "anchor_session": decorated_sessions[anchor_index],
        "timeline_status": timeline_status,
        "timeline_status_label": timeline_status_label,
        "reference_date": today.isoformat(),
    }


def decorate_offering_sessions(
    session_rows: Iterable[Any],
    *,
    reference_date: date | None = None,
    home_material: dict[str, Any] | None = None,
    include_home_placeholder: bool = False,
) -> dict[str, Any]:
    normalized_sessions: list[dict[str, Any]] = []
    today = reference_date or china_today()

    for raw_row in session_rows:
        item = dict(raw_row)
        session_date = parse_date_input(item.get("session_date"), "上课日期")
        if not session_date:
            continue

        weekday = int(item.get("weekday") if item.get("weekday") is not None else session_date.weekday())
        week_index = int(item.get("week_index") or 0)
        section_count = int(item.get("section_count") or 0)
        slot_section_count = int(item.get("slot_section_count") or section_count or 0)

        normalized_sessions.append(
            {
                **item,
                "session_date": session_date.isoformat(),
                "weekday": weekday,
                "weekday_label": weekday_label(weekday),
                "week_index": week_index,
                "week_label": f"第 {week_index} 周" if week_index > 0 else "",
                "section_count": section_count,
                "slot_section_count": slot_section_count,
                "is_section_match": slot_section_count in (0, section_count),
                "date_label": f"{session_date.isoformat()} {weekday_label(weekday)}",
                "content_preview": truncate_text(item.get("content"), 120),
                "learning_material_id": int(item["learning_material_id"]) if item.get("learning_material_id") else None,
            }
        )

    normalized_sessions.sort(key=lambda item: (item["session_date"], int(item.get("order_index") or 0)))
    progress = _decorate_session_progress(normalized_sessions, reference_date=today)
    sessions = progress["sessions"]
    anchor_session = progress.get("anchor_session")
    timeline_status = str(progress.get("timeline_status") or "empty")
    completed_count = 0
    current_count = 0
    upcoming_count = 0

    for index, item in enumerate(sessions):
        session_date = parse_date_input(item.get("session_date"), "上课日期")
        progress_state = str(item.get("progress_state") or "upcoming")
        is_anchor = bool(item.get("is_anchor"))
        detail_content = str(item.get("content") or "").strip()
        content_lines = [
            line.strip()
            for line in detail_content.replace("\r\n", "\n").split("\n")
            if line.strip()
        ]
        order_index = int(item.get("order_index") or index + 1)

        if progress_state == "completed":
            completed_count += 1
        elif progress_state == "current":
            current_count += 1
        else:
            upcoming_count += 1

        item["session_number_label"] = f"第 {order_index} 次课"
        item["relative_day_label"] = _build_relative_day_label(session_date, today)
        item["month_day_label"] = _build_month_day_label(session_date)
        item["timeline_weekday_label"] = _build_timeline_weekday_label(
            week_index=week_index,
            weekday_text=item.get("weekday_label") or "",
        )
        item["timeline_relative_date_label"] = _build_timeline_relative_date_label(
            relative_day_label=item["relative_day_label"],
            session_date=session_date,
        )
        item["segment_title"] = truncate_text(item.get("title"), 18)
        item["session_status_label"] = _build_session_status_label(
            progress_state=progress_state,
            is_anchor=is_anchor,
            timeline_status=timeline_status,
        )
        item["task_status_label"] = item["session_status_label"]
        item["detail_title"] = item.get("title") or ""
        item["detail_content"] = detail_content
        item["detail_lines"] = content_lines
        item["detail_summary"] = detail_content or item.get("content_preview") or ""
        item["has_learning_material"] = bool(item.get("learning_material_id"))
        item["detail_meta"] = " · ".join(
            part
            for part in [
                item.get("date_label"),
                item.get("week_label"),
                f"{int(item.get('section_count') or 0)} 节" if int(item.get("section_count") or 0) > 0 else "",
            ]
            if part
        )
        item["detail_hint"] = (
            f"排课节数为 {int(item.get('slot_section_count') or 0)} 节，与教学内容配置不一致。"
            if not item.get("is_section_match")
            else ""
        )

    home_entry = build_timeline_home_entry(
        home_material,
        include_placeholder=include_home_placeholder,
    )
    timeline_entries = ([home_entry] if home_entry else []) + sessions
    home_material_payload = dict(home_material or {}) if home_entry and home_entry.get("has_home_learning_material") else None

    return {
        **progress,
        "session_count": len(sessions),
        "timeline_entries": timeline_entries,
        "timeline_entry_count": len(timeline_entries),
        "home_entry": home_entry,
        "home_material": home_material_payload,
        "has_home_material": bool(home_material_payload),
        "first_session_date": sessions[0]["session_date"] if sessions else "",
        "last_session_date": sessions[-1]["session_date"] if sessions else "",
        "focus_title": anchor_session["title"] if anchor_session else "",
        "focus_summary": anchor_session["detail_summary"] if anchor_session else "",
        "focus_meta": anchor_session["detail_meta"] if anchor_session else "",
        "focus_status_label": anchor_session["session_status_label"] if anchor_session else "",
        "schedule_summary": (
            f"{sessions[0]['date_label']} 至 {sessions[-1]['date_label']}"
            if len(sessions) >= 2
            else (sessions[0]["date_label"] if sessions else "")
        ),
        "status_note": _build_timeline_status_note(
            timeline_status=timeline_status,
            anchor_session=anchor_session,
        ),
        "completed_count": completed_count,
        "current_count": current_count,
        "upcoming_count": upcoming_count,
        "remaining_count": len(sessions) - completed_count,
        "task_list": sessions,
    }


def build_offering_session_plan(
    *,
    course_lessons: list[dict[str, Any]],
    first_class_date: date,
    weekly_schedule: list[dict[str, Any]],
    semester_start_date: date | None = None,
    semester_end_date: date | None = None,
    reference_date: date | None = None,
) -> dict[str, Any]:
    warnings: list[str] = []

    if semester_start_date and first_class_date < semester_start_date:
        raise CoursePlanningError("第一次上课日期不能早于学期开始日期")
    if semester_end_date and first_class_date > semester_end_date:
        raise CoursePlanningError("第一次上课日期不能晚于学期结束日期")

    occurrences = _build_occurrence_slots(
        first_class_date=first_class_date,
        weekly_schedule=weekly_schedule,
        required_count=len(course_lessons),
        semester_end_date=semester_end_date,
    )

    if len(occurrences) < len(course_lessons):
        warnings.append(
            f"按当前学期范围只能排入 {len(occurrences)} 次课，仍有 {len(course_lessons) - len(occurrences)} 次课未能落到具体日期。"
        )

    generated_sessions: list[dict[str, Any]] = []
    for lesson, occurrence in zip(course_lessons, occurrences):
        session_date = occurrence["session_date"]
        lesson_section_count = int(lesson.get("section_count") or 0)
        slot_section_count = int(occurrence["slot_section_count"])
        if slot_section_count != lesson_section_count:
            warnings.append(
                f"{session_date.isoformat()} {occurrence['weekday_label']} 预设 {slot_section_count} 节，但对应课堂内容为 {lesson_section_count} 节。"
            )

        generated_sessions.append(
            {
                "course_lesson_id": lesson.get("id"),
                "order_index": int(lesson.get("order_index") or len(generated_sessions) + 1),
                "title": lesson["title"],
                "content": lesson["content"],
                "content_preview": truncate_text(lesson.get("content"), 120),
                "section_count": lesson_section_count,
                "slot_section_count": slot_section_count,
                "session_date": session_date.isoformat(),
                "weekday": int(occurrence["weekday"]),
                "weekday_label": occurrence["weekday_label"],
                "week_index": _compute_week_index(session_date, semester_start_date),
                "learning_material_id": lesson.get("learning_material_id"),
                "learning_material": lesson.get("learning_material"),
                "learning_material_name": lesson.get("learning_material_name"),
                "learning_material_path": lesson.get("learning_material_path"),
                "learning_material_viewer_url": lesson.get("learning_material_viewer_url"),
            }
        )

    decorated = decorate_offering_sessions(generated_sessions, reference_date=reference_date)
    last_session_date = parse_date_input(decorated.get("last_session_date"))

    return {
        **decorated,
        "warnings": list(dict.fromkeys(warnings)),
        "schedule_info": build_schedule_info_text(
            first_class_date=first_class_date,
            weekly_schedule=weekly_schedule,
            session_count=decorated["session_count"],
            end_date=last_session_date,
        ),
        "weekly_schedule_summary": summarize_weekly_schedule(weekly_schedule),
        "first_class_date": first_class_date.isoformat(),
    }
