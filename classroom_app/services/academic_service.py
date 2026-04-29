from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo


try:
    CHINA_TZ = ZoneInfo("Asia/Shanghai")
except Exception:
    # Windows environments may not ship IANA tzdata by default.
    CHINA_TZ = timezone(timedelta(hours=8))

MAX_TEXTBOOK_SECTION_LENGTH = 4000
MAX_CLASSROOM_SECTION_LENGTH = 2400


# Official references:
# - 国务院办公厅关于 2025 年部分节假日安排的通知（gov.cn, 2024-11）
# - 国务院办公厅关于 2026 年部分节假日安排的通知（gov.cn, 2025-11）
# - 广西壮族自治区人民政府办公厅关于 2025 / 2026 年广西三月三放假的通知
HOLIDAY_DATA: dict[int, dict[str, dict[str, dict[str, str]]]] = {
    2025: {
        "holidays": {
            "2025-01-01": {"label": "元旦", "scope": "national", "kind": "holiday"},
            "2025-01-28": {"label": "春节", "scope": "national", "kind": "holiday"},
            "2025-01-29": {"label": "春节", "scope": "national", "kind": "holiday"},
            "2025-01-30": {"label": "春节", "scope": "national", "kind": "holiday"},
            "2025-01-31": {"label": "春节", "scope": "national", "kind": "holiday"},
            "2025-02-01": {"label": "春节", "scope": "national", "kind": "holiday"},
            "2025-02-02": {"label": "春节", "scope": "national", "kind": "holiday"},
            "2025-02-03": {"label": "春节", "scope": "national", "kind": "holiday"},
            "2025-02-04": {"label": "春节", "scope": "national", "kind": "holiday"},
            "2025-03-31": {"label": "广西三月三", "scope": "guangxi", "kind": "holiday"},
            "2025-04-01": {"label": "广西三月三", "scope": "guangxi", "kind": "holiday"},
            "2025-04-04": {"label": "清明节", "scope": "national", "kind": "holiday"},
            "2025-04-05": {"label": "清明节", "scope": "national", "kind": "holiday"},
            "2025-04-06": {"label": "清明节", "scope": "national", "kind": "holiday"},
            "2025-05-01": {"label": "劳动节", "scope": "national", "kind": "holiday"},
            "2025-05-02": {"label": "劳动节", "scope": "national", "kind": "holiday"},
            "2025-05-03": {"label": "劳动节", "scope": "national", "kind": "holiday"},
            "2025-05-04": {"label": "劳动节", "scope": "national", "kind": "holiday"},
            "2025-05-05": {"label": "劳动节", "scope": "national", "kind": "holiday"},
            "2025-05-31": {"label": "端午节", "scope": "national", "kind": "holiday"},
            "2025-06-01": {"label": "端午节", "scope": "national", "kind": "holiday"},
            "2025-06-02": {"label": "端午节", "scope": "national", "kind": "holiday"},
            "2025-10-01": {"label": "国庆节 / 中秋节", "scope": "national", "kind": "holiday"},
            "2025-10-02": {"label": "国庆节 / 中秋节", "scope": "national", "kind": "holiday"},
            "2025-10-03": {"label": "国庆节 / 中秋节", "scope": "national", "kind": "holiday"},
            "2025-10-04": {"label": "国庆节 / 中秋节", "scope": "national", "kind": "holiday"},
            "2025-10-05": {"label": "国庆节 / 中秋节", "scope": "national", "kind": "holiday"},
            "2025-10-06": {"label": "国庆节 / 中秋节", "scope": "national", "kind": "holiday"},
            "2025-10-07": {"label": "国庆节 / 中秋节", "scope": "national", "kind": "holiday"},
            "2025-10-08": {"label": "国庆节 / 中秋节", "scope": "national", "kind": "holiday"},
        },
        "workdays": {
            "2025-01-26": {"label": "春节调休上班", "scope": "national", "kind": "workday"},
            "2025-02-08": {"label": "春节调休上班", "scope": "national", "kind": "workday"},
            "2025-04-27": {"label": "劳动节调休上班", "scope": "national", "kind": "workday"},
            "2025-09-28": {"label": "国庆节调休上班", "scope": "national", "kind": "workday"},
            "2025-10-11": {"label": "国庆节调休上班", "scope": "national", "kind": "workday"},
        },
    },
    2026: {
        "holidays": {
            "2026-01-01": {"label": "元旦", "scope": "national", "kind": "holiday"},
            "2026-01-02": {"label": "元旦", "scope": "national", "kind": "holiday"},
            "2026-01-03": {"label": "元旦", "scope": "national", "kind": "holiday"},
            "2026-02-15": {"label": "春节", "scope": "national", "kind": "holiday"},
            "2026-02-16": {"label": "春节", "scope": "national", "kind": "holiday"},
            "2026-02-17": {"label": "春节", "scope": "national", "kind": "holiday"},
            "2026-02-18": {"label": "春节", "scope": "national", "kind": "holiday"},
            "2026-02-19": {"label": "春节", "scope": "national", "kind": "holiday"},
            "2026-02-20": {"label": "春节", "scope": "national", "kind": "holiday"},
            "2026-02-21": {"label": "春节", "scope": "national", "kind": "holiday"},
            "2026-02-22": {"label": "春节", "scope": "national", "kind": "holiday"},
            "2026-02-23": {"label": "春节", "scope": "national", "kind": "holiday"},
            "2026-04-04": {"label": "清明节", "scope": "national", "kind": "holiday"},
            "2026-04-05": {"label": "清明节", "scope": "national", "kind": "holiday"},
            "2026-04-06": {"label": "清明节", "scope": "national", "kind": "holiday"},
            "2026-04-17": {"label": "广西三月三补休", "scope": "guangxi", "kind": "holiday"},
            "2026-04-18": {"label": "广西三月三", "scope": "guangxi", "kind": "holiday"},
            "2026-04-19": {"label": "广西三月三", "scope": "guangxi", "kind": "holiday"},
            "2026-04-20": {"label": "广西三月三补休", "scope": "guangxi", "kind": "holiday"},
            "2026-05-01": {"label": "劳动节", "scope": "national", "kind": "holiday"},
            "2026-05-02": {"label": "劳动节", "scope": "national", "kind": "holiday"},
            "2026-05-03": {"label": "劳动节", "scope": "national", "kind": "holiday"},
            "2026-05-04": {"label": "劳动节", "scope": "national", "kind": "holiday"},
            "2026-05-05": {"label": "劳动节", "scope": "national", "kind": "holiday"},
            "2026-06-19": {"label": "端午节", "scope": "national", "kind": "holiday"},
            "2026-06-20": {"label": "端午节", "scope": "national", "kind": "holiday"},
            "2026-06-21": {"label": "端午节", "scope": "national", "kind": "holiday"},
            "2026-09-25": {"label": "中秋节", "scope": "national", "kind": "holiday"},
            "2026-09-26": {"label": "中秋节", "scope": "national", "kind": "holiday"},
            "2026-09-27": {"label": "中秋节", "scope": "national", "kind": "holiday"},
            "2026-10-01": {"label": "国庆节", "scope": "national", "kind": "holiday"},
            "2026-10-02": {"label": "国庆节", "scope": "national", "kind": "holiday"},
            "2026-10-03": {"label": "国庆节", "scope": "national", "kind": "holiday"},
            "2026-10-04": {"label": "国庆节", "scope": "national", "kind": "holiday"},
            "2026-10-05": {"label": "国庆节", "scope": "national", "kind": "holiday"},
            "2026-10-06": {"label": "国庆节", "scope": "national", "kind": "holiday"},
            "2026-10-07": {"label": "国庆节", "scope": "national", "kind": "holiday"},
        },
        "workdays": {
            "2026-01-04": {"label": "元旦调休上班", "scope": "national", "kind": "workday"},
            "2026-02-14": {"label": "春节调休上班", "scope": "national", "kind": "workday"},
            "2026-02-28": {"label": "春节调休上班", "scope": "national", "kind": "workday"},
            "2026-05-09": {"label": "劳动节调休上班", "scope": "national", "kind": "workday"},
            "2026-09-20": {"label": "国庆节调休上班", "scope": "national", "kind": "workday"},
            "2026-10-10": {"label": "国庆节调休上班", "scope": "national", "kind": "workday"},
        },
    },
}


def china_now() -> datetime:
    return datetime.now(CHINA_TZ)


def china_today() -> date:
    return china_now().date()


def parse_date_input(value: str | date | datetime | None, field_name: str = "日期") -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()

    normalized = str(value).strip()
    if not normalized:
        return None

    try:
        return date.fromisoformat(normalized[:10])
    except ValueError as exc:
        raise ValueError(f"{field_name}格式无效") from exc


def infer_semester_name(reference_date: date | datetime | None = None) -> str:
    current_date = parse_date_input(reference_date) or china_today()
    month = current_date.month

    if month >= 8:
        start_year = current_date.year
        term_label = "第一学期"
    elif month <= 1:
        start_year = current_date.year - 1
        term_label = "第一学期"
    else:
        start_year = current_date.year - 1
        term_label = "第二学期"

    return f"{start_year}-{start_year + 1}{term_label}"


def build_semester_defaults(reference_date: date | datetime | None = None) -> dict[str, str]:
    current_date = parse_date_input(reference_date) or china_today()
    month = current_date.month

    if month >= 8 or month <= 1:
        start_year = current_date.year if month >= 8 else current_date.year - 1
        start_date_value = date(start_year, 9, 1)
        end_date_value = date(start_year + 1, 1, 31)
    else:
        start_year = current_date.year - 1
        start_date_value = date(start_year + 1, 2, 1)
        end_date_value = date(start_year + 1, 7, 31)

    return {
        "name": infer_semester_name(current_date),
        "start_date": start_date_value.isoformat(),
        "end_date": end_date_value.isoformat(),
    }


def compute_semester_week_count(start_date: date, end_date: date) -> int:
    if end_date < start_date:
        raise ValueError("学期结束时间不能早于开始时间")

    calendar_start = start_date - timedelta(days=start_date.weekday())
    calendar_end = end_date + timedelta(days=(6 - end_date.weekday()))
    return ((calendar_end - calendar_start).days // 7) + 1


def normalize_string_list(
    values: Iterable[Any],
    *,
    max_items: int,
    max_length: int,
) -> list[str]:
    normalized_values: list[str] = []
    seen: set[str] = set()

    for raw_value in values:
        value = " ".join(str(raw_value or "").replace("\u3000", " ").split()).strip()
        if not value:
            continue
        if len(value) > max_length:
            raise ValueError(f"单项内容不能超过 {max_length} 个字符")

        dedupe_key = value.casefold()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        normalized_values.append(value)

        if len(normalized_values) > max_items:
            raise ValueError(f"最多只能填写 {max_items} 项")

    return normalized_values


def _split_text_list(raw_value: str) -> list[str]:
    return [part for part in re.split(r"[\r\n,，;；、]+", raw_value) if part.strip()]


def parse_json_list_field(
    raw_value: str | list[Any] | tuple[Any, ...] | None,
    *,
    field_name: str,
    max_items: int,
    max_length: int,
) -> list[str]:
    if raw_value is None or raw_value == "":
        return []

    parsed: Any
    if isinstance(raw_value, (list, tuple, set)):
        parsed = list(raw_value)
    else:
        raw_text = str(raw_value).strip()
        if not raw_text:
            return []
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError:
            parsed = _split_text_list(raw_text)

    if not isinstance(parsed, list):
        raise ValueError(f"{field_name}必须是列表")

    try:
        return normalize_string_list(parsed, max_items=max_items, max_length=max_length)
    except ValueError as exc:
        raise ValueError(f"{field_name}{exc}") from exc


def _parse_json_text_list(raw_value: Any) -> list[str]:
    if raw_value is None or raw_value == "":
        return []

    if isinstance(raw_value, (list, tuple, set)):
        try:
            return normalize_string_list(raw_value, max_items=50, max_length=200)
        except ValueError:
            return []

    raw_text = str(raw_value).strip()
    if not raw_text:
        return []

    try:
        parsed = json.loads(raw_text)
    except (TypeError, json.JSONDecodeError):
        parsed = _split_text_list(raw_text)

    if not isinstance(parsed, list):
        return []

    try:
        return normalize_string_list(parsed, max_items=50, max_length=200)
    except ValueError:
        return []


def truncate_text(value: Any, limit: int = 140) -> str:
    normalized = " ".join(str(value or "").split()).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(limit - 3, 0)].rstrip() + "..."


def build_holiday_lookup(years: Iterable[int]) -> dict[str, dict[str, str]]:
    lookup: dict[str, dict[str, str]] = {}
    normalized_years: set[int] = set()

    for raw_year in years:
        try:
            year = int(raw_year)
        except (TypeError, ValueError):
            continue
        normalized_years.add(year)

    for year in normalized_years:
        payload = HOLIDAY_DATA.get(year) or {}
        for bucket in ("holidays", "workdays"):
            lookup.update(payload.get(bucket) or {})

    return lookup


def load_teacher_semester_rows(conn, teacher_id: int):
    return conn.execute(
        """
        SELECT id, name, start_date, end_date, week_count, created_at, updated_at
        FROM academic_semesters
        WHERE teacher_id = ?
        ORDER BY start_date DESC, updated_at DESC, id DESC
        """,
        (teacher_id,),
    ).fetchall()


def load_student_semester_rows(conn, student_id: int):
    student_row = conn.execute(
        """
        SELECT class_id
        FROM students
        WHERE id = ?
        LIMIT 1
        """,
        (student_id,),
    ).fetchone()
    if not student_row or not student_row["class_id"]:
        return []

    class_id = int(student_row["class_id"])
    offering_rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT id, teacher_id, semester_id, semester
            FROM class_offerings
            WHERE class_id = ?
            ORDER BY id DESC
            """,
            (class_id,),
        ).fetchall()
    ]
    if not offering_rows:
        return []

    teacher_ids = sorted(
        {
            int(row["teacher_id"])
            for row in offering_rows
            if row.get("teacher_id") not in (None, "")
        }
    )
    if not teacher_ids:
        return []

    placeholders = ",".join("?" for _ in teacher_ids)
    candidate_rows = [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT id, teacher_id, name, start_date, end_date, week_count, created_at, updated_at
            FROM academic_semesters
            WHERE teacher_id IN ({placeholders})
            ORDER BY teacher_id ASC, start_date DESC, updated_at DESC, id DESC
            """,
            tuple(teacher_ids),
        ).fetchall()
    ]
    if not candidate_rows:
        return []

    direct_semester_ids = {
        int(row["semester_id"])
        for row in offering_rows
        if row.get("semester_id") not in (None, "")
    }
    legacy_semester_pairs = {
        (int(row["teacher_id"]), str(row.get("semester") or "").strip())
        for row in offering_rows
        if row.get("teacher_id") not in (None, "")
        and row.get("semester_id") in (None, "")
        and str(row.get("semester") or "").strip()
    }

    matched_items: list[dict[str, Any]] = []
    matched_teacher_ids: set[int] = set()
    seen_semester_ids: set[int] = set()

    def append_unique(item: dict[str, Any]) -> None:
        semester_id = int(item.get("id") or 0)
        if semester_id and semester_id in seen_semester_ids:
            return
        if semester_id:
            seen_semester_ids.add(semester_id)
        teacher_id = int(item.get("teacher_id") or 0)
        if teacher_id:
            matched_teacher_ids.add(teacher_id)
        matched_items.append(item)

    for item in candidate_rows:
        if int(item.get("id") or 0) in direct_semester_ids:
            append_unique(item)

    for item in candidate_rows:
        key = (int(item.get("teacher_id") or 0), str(item.get("name") or "").strip())
        if key[0] and key in legacy_semester_pairs:
            append_unique(item)

    teacher_candidate_map: dict[int, list[dict[str, Any]]] = {}
    for item in candidate_rows:
        teacher_id = int(item.get("teacher_id") or 0)
        if teacher_id:
            teacher_candidate_map.setdefault(teacher_id, []).append(item)

    current_date = china_today()
    for teacher_id in teacher_ids:
        if teacher_id in matched_teacher_ids:
            continue
        options = teacher_candidate_map.get(teacher_id) or []
        if not options:
            continue

        current_item = None
        for item in options:
            start_date_value = parse_date_input(item.get("start_date"))
            end_date_value = parse_date_input(item.get("end_date"))
            if start_date_value and end_date_value and start_date_value <= current_date <= end_date_value:
                current_item = item
                break

        append_unique(current_item or options[0])

    matched_items.sort(
        key=lambda item: (
            parse_date_input(item.get("start_date")) or date.min,
            str(item.get("updated_at") or item.get("created_at") or ""),
            int(item.get("id") or 0),
        ),
        reverse=True,
    )
    return matched_items


def build_semester_calendar_payload(
    semesters: Iterable[dict[str, Any] | sqlite3.Row | Any],
    *,
    reference_date: date | None = None,
) -> dict[str, Any]:
    current_date = reference_date or china_today()
    serialized_items: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, str, int]] = set()

    for raw_item in semesters:
        try:
            item = serialize_semester_row(raw_item, reference_date=current_date)
        except Exception:
            continue

        dedupe_key = (
            str(item.get("name") or "").casefold(),
            str(item.get("start_date") or ""),
            str(item.get("end_date") or ""),
            int(item.get("week_count") or 0),
        )
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        serialized_items.append(item)

    covered_years = {current_date.year, current_date.year + 1}
    for item in serialized_items:
        for key in ("start_date", "end_date"):
            value = str(item.get(key) or "").strip()
            if len(value) >= 4 and value[:4].isdigit():
                covered_years.add(int(value[:4]))

    return {
        "semesters": serialized_items,
        "holiday_lookup": build_holiday_lookup(covered_years),
        "today_iso": current_date.isoformat(),
        "default_semester_id": choose_default_semester_id(serialized_items, reference_date=current_date),
    }


def serialize_semester_row(row: Any, *, reference_date: date | None = None) -> dict[str, Any]:
    item = dict(row)
    today = reference_date or china_today()
    start_date_value = parse_date_input(item.get("start_date"), "学期开始时间")
    end_date_value = parse_date_input(item.get("end_date"), "学期结束时间")
    week_count = int(item.get("week_count") or 0)

    if start_date_value and end_date_value and week_count <= 0:
        week_count = compute_semester_week_count(start_date_value, end_date_value)

    item["name"] = str(item.get("name") or "").strip()
    item["start_date"] = start_date_value.isoformat() if start_date_value else ""
    item["end_date"] = end_date_value.isoformat() if end_date_value else ""
    item["week_count"] = week_count
    item["is_current"] = bool(
        start_date_value and end_date_value and start_date_value <= today <= end_date_value
    )
    item["display_range"] = (
        f"{start_date_value.isoformat()} 至 {end_date_value.isoformat()}"
        if start_date_value and end_date_value
        else ""
    )
    return item


def serialize_textbook_row(row: Any) -> dict[str, Any]:
    item = dict(row)
    authors = _parse_json_text_list(item.get("authors_json", item.get("authors")))
    tags = _parse_json_text_list(item.get("tags_json", item.get("tags")))
    publication_date_value = parse_date_input(item.get("publication_date"), "出版日期")
    attachment_size = int(item.get("attachment_size") or 0)
    title = str(item.get("title") or "").strip()
    publisher = str(item.get("publisher") or "").strip()
    introduction = str(item.get("introduction") or "").strip()
    catalog_text = str(item.get("catalog_text") or "").strip()

    author_display = str(item.get("author_display") or "").strip()
    if not author_display:
        author_display = "、".join(authors) if authors else "未填写作者"

    item["title"] = title
    item["authors"] = authors
    item["tags"] = tags
    item["publisher"] = publisher
    item["introduction"] = introduction
    item["catalog_text"] = catalog_text
    item["publication_date"] = publication_date_value.isoformat() if publication_date_value else ""
    item["publication_year"] = (
        publication_date_value.year if publication_date_value else item.get("publication_year")
    )
    item["has_attachment"] = bool(str(item.get("attachment_path") or "").strip())
    item["attachment_size"] = attachment_size
    item["introduction_preview"] = truncate_text(introduction, 180)
    item["catalog_preview"] = truncate_text(catalog_text, 220)
    item["author_display"] = author_display
    item["tag_display"] = " ".join(tags)
    item["search_blob"] = " ".join(
        filter(
            None,
            [
                title,
                publisher,
                " ".join(authors),
                " ".join(tags),
                introduction,
                catalog_text,
            ],
        )
    ).lower()
    return item


def _build_textbook_payload_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    textbook_id = snapshot.get("textbook_id")
    if not textbook_id:
        return None

    return serialize_textbook_row(
        {
            "id": textbook_id,
            "title": snapshot.get("textbook_title") or "",
            "authors_json": snapshot.get("textbook_authors_json") or "[]",
            "publisher": snapshot.get("textbook_publisher") or "",
            "publication_date": snapshot.get("textbook_publication_date") or "",
            "introduction": snapshot.get("textbook_introduction") or "",
            "catalog_text": snapshot.get("textbook_catalog_text") or "",
            "tags_json": snapshot.get("textbook_tags_json") or "[]",
            "attachment_name": snapshot.get("textbook_attachment_name") or "",
            "attachment_path": snapshot.get("textbook_attachment_path") or "",
            "attachment_size": snapshot.get("textbook_attachment_size") or 0,
            "attachment_mime_type": snapshot.get("textbook_attachment_mime_type") or "",
        }
    )


def build_textbook_prompt_context(textbook: dict[str, Any] | None) -> str:
    if not textbook:
        return "当前课堂未绑定教材。"

    lines = [
        f"教材名称：{textbook.get('title') or '未命名教材'}",
        f"作者：{textbook.get('author_display') or '未填写作者'}",
    ]
    if textbook.get("publisher"):
        lines.append(f"出版社：{textbook['publisher']}")
    if textbook.get("publication_year"):
        lines.append(f"出版年份：{textbook['publication_year']}")
    if textbook.get("tags"):
        lines.append(f"教材标签：{'、'.join(textbook['tags'])}")
    if textbook.get("introduction"):
        lines.append(
            f"教材简介：{str(textbook['introduction']).strip()[:MAX_TEXTBOOK_SECTION_LENGTH]}"
        )
    if textbook.get("catalog_text"):
        lines.append(
            f"教材目录：{str(textbook['catalog_text']).strip()[:MAX_TEXTBOOK_SECTION_LENGTH]}"
        )
    return "\n".join(lines)


def _safe_fetch_recent_material_names(conn, class_offering_id: int) -> list[str]:
    try:
        rows = conn.execute(
            """
            SELECT m.name
            FROM course_material_assignments a
            JOIN course_materials m ON m.id = a.material_id
            WHERE a.class_offering_id = ?
            ORDER BY a.created_at DESC, m.updated_at DESC, m.id DESC
            LIMIT 8
            """,
            (class_offering_id,),
        ).fetchall()
    except sqlite3.Error:
        return []

    return [
        str(row["name"] or "").strip()
        for row in rows
        if str(row["name"] or "").strip()
    ]


def _safe_fetch_recent_assignment_titles(conn, class_offering_id: int) -> list[str]:
    try:
        rows = conn.execute(
            """
            SELECT title
            FROM assignments
            WHERE class_offering_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 6
            """,
            (class_offering_id,),
        ).fetchall()
    except sqlite3.Error:
        return []

    return [
        str(row["title"] or "").strip()
        for row in rows
        if str(row["title"] or "").strip()
    ]


def build_classroom_ai_context(conn, class_offering_id: int) -> dict[str, Any]:
    snapshot_row = conn.execute(
        """
        SELECT o.id,
               o.class_id,
               o.course_id,
               o.teacher_id,
               o.schedule_info,
               o.semester,
               o.semester_id,
               o.textbook_id,
               COALESCE(s.name, o.semester) AS semester_name,
               s.start_date AS semester_start_date,
               s.end_date AS semester_end_date,
               s.week_count AS semester_week_count,
               c.name AS course_name,
               c.description AS course_description,
               c.credits AS course_credits,
               cl.name AS class_name,
               cl.description AS class_description,
               t.name AS teacher_name,
               (
                   SELECT COUNT(*)
                   FROM students st
                   WHERE st.class_id = o.class_id
               ) AS class_student_count,
               tb.title AS textbook_title,
               tb.authors_json AS textbook_authors_json,
               tb.publisher AS textbook_publisher,
               tb.publication_date AS textbook_publication_date,
               tb.introduction AS textbook_introduction,
               tb.catalog_text AS textbook_catalog_text,
               tb.tags_json AS textbook_tags_json,
               tb.attachment_name AS textbook_attachment_name,
               tb.attachment_path AS textbook_attachment_path,
               tb.attachment_size AS textbook_attachment_size,
               tb.attachment_mime_type AS textbook_attachment_mime_type
        FROM class_offerings o
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        JOIN teachers t ON t.id = o.teacher_id
        LEFT JOIN academic_semesters s ON s.id = o.semester_id
        LEFT JOIN textbooks tb ON tb.id = o.textbook_id
        WHERE o.id = ?
        LIMIT 1
        """,
        (class_offering_id,),
    ).fetchone()

    if not snapshot_row:
        return {}

    snapshot = dict(snapshot_row)
    textbook = _build_textbook_payload_from_snapshot(snapshot)
    recent_material_names = _safe_fetch_recent_material_names(conn, class_offering_id)
    recent_assignment_titles = _safe_fetch_recent_assignment_titles(conn, class_offering_id)

    classroom_lines = [
        f"课堂编号：{snapshot.get('id')}",
        f"课程名称：{snapshot.get('course_name') or '未命名课程'}",
        f"授课班级：{snapshot.get('class_name') or '未命名班级'}",
        f"任课教师：{snapshot.get('teacher_name') or '未命名教师'}",
    ]

    if snapshot.get("semester_name"):
        classroom_lines.append(f"所属学期：{snapshot['semester_name']}")
    if snapshot.get("semester_start_date") and snapshot.get("semester_end_date"):
        classroom_lines.append(
            f"学期时间：{snapshot['semester_start_date']} 至 {snapshot['semester_end_date']}"
        )
    if snapshot.get("semester_week_count"):
        classroom_lines.append(f"学期周数：第 1 周至第 {int(snapshot['semester_week_count'])} 周")
    if snapshot.get("course_credits") is not None:
        classroom_lines.append(f"课程学分：{snapshot.get('course_credits')}")
    if snapshot.get("class_student_count"):
        classroom_lines.append(f"班级人数：{int(snapshot['class_student_count'])} 人")
    if snapshot.get("schedule_info"):
        classroom_lines.append(f"排课说明：{snapshot['schedule_info']}")
    if snapshot.get("course_description"):
        classroom_lines.append(
            f"课程简介：{str(snapshot['course_description']).strip()[:MAX_CLASSROOM_SECTION_LENGTH]}"
        )
    if snapshot.get("class_description"):
        classroom_lines.append(
            f"班级说明：{str(snapshot['class_description']).strip()[:1200]}"
        )
    if textbook:
        classroom_lines.append(f"当前绑定教材：{textbook.get('title') or '未命名教材'}")
    if recent_material_names:
        classroom_lines.append(f"最近课堂材料：{'、'.join(recent_material_names)}")
    if recent_assignment_titles:
        classroom_lines.append(f"最近课堂任务：{'、'.join(recent_assignment_titles)}")

    return {
        **snapshot,
        "textbook": textbook,
        "recent_material_names": recent_material_names,
        "recent_assignment_titles": recent_assignment_titles,
        "classroom_summary": "\n".join(classroom_lines),
        "textbook_summary": build_textbook_prompt_context(textbook),
    }


def choose_default_semester_id(
    semesters: Iterable[dict[str, Any]],
    reference_date: date | None = None,
) -> int | None:
    today = reference_date or china_today()
    serialized_items: list[dict[str, Any]] = []

    for item in semesters:
        try:
            serialized_items.append(serialize_semester_row(item, reference_date=today))
        except Exception:
            continue

    for item in serialized_items:
        if item.get("is_current") and item.get("id") is not None:
            return int(item["id"])

    future_items: list[tuple[date, int]] = []
    past_items: list[tuple[date, int]] = []

    for item in serialized_items:
        if item.get("id") is None:
            continue
        start_date_value = parse_date_input(item.get("start_date"))
        end_date_value = parse_date_input(item.get("end_date"))
        item_id = int(item["id"])

        if start_date_value and start_date_value > today:
            future_items.append((start_date_value, item_id))
        elif end_date_value and end_date_value < today:
            past_items.append((end_date_value, item_id))

    if future_items:
        return min(future_items, key=lambda pair: pair[0])[1]
    if past_items:
        return max(past_items, key=lambda pair: pair[0])[1]
    return None
