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
from datetime import date, datetime, timedelta
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

# 历史学期回溯数量：从当前学期往前逐学期请求（远端不支持参数时会因
# 严格的学期过滤而自动落空，不会污染本地数据）。
HISTORY_TERM_LOOKBACK = 6

_teacher_schedule_sync_locks: dict[int, asyncio.Lock] = {}


# --------------------------------------------------------------------------- #
# 学年学期与教学周锚定工具
#
# 全平台口径（与 academic_service.compute_semester_week_count 一致）：
# 第 1 教学周 = 含学期开始日的自然周，周一为一周之始。
# --------------------------------------------------------------------------- #


def _monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _parse_iso_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _today_local() -> date:
    """Asia/Shanghai 的今天（与平台学期日历同一时区口径）。"""
    try:
        from .academic_service import china_today

        return china_today()
    except Exception:  # noqa: BLE001 — 兜底本机日期
        return datetime.now().date()


def _derive_week1_monday(synced_at_iso: str, cur_week: int) -> str:
    """由同步时刻与当时的教学周反推第 1 教学周周一（ISO 日期）。"""
    if cur_week < 1:
        return ""
    synced_day = _parse_iso_date(synced_at_iso)
    if synced_day is None:
        return ""
    return (_monday_of(synced_day) - timedelta(weeks=cur_week - 1)).isoformat()


def _previous_term_key(year: str, term: str) -> tuple[str, str] | None:
    """('2025-2026','2') → ('2025-2026','1')；('2025-2026','1') → ('2024-2025','2')。"""
    matched = re.fullmatch(r"(\d{4})-(\d{4})", str(year or "").strip())
    term_text = str(term or "").strip()
    if not matched:
        return None
    if term_text == "2":
        return (str(year).strip(), "1")
    if term_text == "1":
        start_year = int(matched.group(1)) - 1
        return (f"{start_year}-{start_year + 1}", "2")
    return None


def _history_term_keys(year: str, term: str, count: int = HISTORY_TERM_LOOKBACK) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    cursor: tuple[str, str] | None = (year, term)
    for _ in range(count):
        cursor = _previous_term_key(*cursor) if cursor else None
        if cursor is None:
            break
        keys.append(cursor)
    return keys


# 平台学期名（academic_semesters.name，如 "2025-2026第二学期" / "2025-2026学年第2学期"）
# → 智慧课堂 (year, term)。
_SEMESTER_NAME_RE = re.compile(r"(\d{4})\s*[-–—~]\s*(\d{4}).*?([一二12])\s*学期")
_TERM_DIGITS = {"一": "1", "1": "1", "二": "2", "2": "2"}


def _parse_platform_semester_name(name: Any) -> tuple[str, str] | None:
    matched = _SEMESTER_NAME_RE.search(str(name or ""))
    if not matched:
        return None
    term = _TERM_DIGITS.get(matched.group(3), "")
    if not term:
        return None
    return (f"{matched.group(1)}-{matched.group(2)}", term)


def _load_platform_semester_anchors(conn, teacher_id: int) -> dict[tuple[str, str], dict[str, Any]]:
    """教师可见的平台学期设置（academic_semesters）→ 教学周锚点。

    平台学期设置是全平台教学活动的权威时间基准；本人创建的学期优先于
    组织共享的学期。解析失败/无学期时返回空 dict，调用方退回同步锚点。
    """
    try:
        from .academic_service import load_teacher_semester_rows

        rows = load_teacher_semester_rows(conn, int(teacher_id))
    except Exception:  # noqa: BLE001 — 学期模块不可用时静默降级
        return {}
    anchors: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = _parse_platform_semester_name(row.get("name"))
        if key is None:
            continue
        start_day = _parse_iso_date(row.get("start_date"))
        if start_day is None:
            continue
        entry = {
            "semester_id": _coerce_int(row.get("id")),
            "name": _clean_text(row.get("name")),
            "week1_monday": _monday_of(start_day),
            "week_count": _coerce_int(row.get("week_count")),
            "is_owned": bool(row.get("is_owned")),
        }
        existing = anchors.get(key)
        if existing is None or (entry["is_owned"] and not existing["is_owned"]):
            anchors[key] = entry
    return anchors


def _platform_week_for_date(conn, semester_id: int, on_date: date) -> int:
    """平台学期日历的逐日 week_index（含调休等人工修正），查不到返回 0。"""
    if semester_id <= 0:
        return 0
    try:
        row = conn.execute(
            "SELECT week_index FROM academic_semester_calendar_days WHERE semester_id = ? AND date = ?",
            (int(semester_id), on_date.isoformat()),
        ).fetchone()
    except Exception:  # noqa: BLE001 — 日历表不存在时退回算术推算
        return 0
    return _coerce_int(row["week_index"]) if row else 0


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


def _short_classroom(text: Any) -> str:
    """'（知新楼B414）网络渗透实验室' → '知新楼B414'（无括号则原文）。"""
    matched = re.search(r"[（(]([^（）()]+)[）)]", str(text or ""))
    if matched:
        return matched.group(1).strip()
    return _clean_text(text)


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


async def _fetch_teacher_schedule(
    client: httpx.AsyncClient,
    *,
    year: str = "",
    semester: str = "",
) -> dict[str, Any]:
    """拉取课表：不带参数 = 当前学期；带 year/semester = 指定（历史）学期。"""
    data: dict[str, str] = {}
    if year and semester:
        data = {"year": year, "semester": semester}
    response = await client.post(TEACHER_SCHEDULE_LIST_PATH, data=data)
    response.raise_for_status()
    return _parse_schedule_payload(response.json())


# --------------------------------------------------------------------------- #
# Teaching-class enrichment (best-effort, from the check-in schedule sync)
# --------------------------------------------------------------------------- #


def _load_academic_class_mappings(conn, teacher_id: int) -> dict[Any, str]:
    """教务系统"教学班代号 → 真实行政班名"的精确对照。

    权威来源是"班级与学生名单"同步落地的
    ``teacher_academic_roster_memberships``：该同步按教学班逐个拉取学生
    名单，每条记录把学生的真实行政班（``class_id``，直接外键到本平台
    ``classes`` 表）与其所在教学班（``teaching_class_name``，即 jxbmc/
    "计算机网络实验-0002" 这类代号）关联起来——写入 ``classes.name`` 用的
    正是同一个字符串，因此按 class_id 取名保证与本平台班级表完全同名，
    不需要模糊匹配。按 (course_code, teaching_class_name) 分组，仅当组内
    学生的 class_id 全部一致（教学班未被拆分/合并多个行政班）才采信，
    宁缺勿错。

    注：之前一度使用教务"课程与课次"同步
    （teacher_academic_course_sync_items.class_composition）作对照源，
    但该字段在部分接口响应缺少"教学班组成"时会被兜底填充成 jxbmc 本身
    （等价于教学班代号无变化），导致班级名一直显示代号、真实课堂也匹配
    不上——因此改用本函数这个更可靠的名单关系表。
    """
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT m.course_code, m.teaching_class_name, m.class_id, c.name AS class_name
            FROM teacher_academic_roster_memberships m
            JOIN classes c ON c.id = m.class_id
            WHERE m.teacher_id = ?
              AND COALESCE(m.teaching_class_name, '') <> ''
            """,
            (int(teacher_id),),
        ).fetchall()
    except Exception:  # noqa: BLE001 — 教务名单同步表不存在时跳过
        return {}
    groups: dict[Any, set[tuple[int, str]]] = {}
    for row in rows:
        row_dict = dict(row)
        tcn = _clean_text(row_dict.get("teaching_class_name"))
        code = _clean_text(row_dict.get("course_code"))
        class_name = _clean_text(row_dict.get("class_name"))
        raw_class_id = row_dict.get("class_id")
        if not tcn or not class_name or not raw_class_id:
            continue
        pair = (int(raw_class_id), class_name)
        groups.setdefault((code, tcn), set()).add(pair)
        groups.setdefault(tcn, set()).add(pair)
    mappings: dict[Any, str] = {}
    for key, pairs in groups.items():
        distinct_class_ids = {class_id for class_id, _name in pairs}
        if len(distinct_class_ids) == 1:
            mappings[key] = next(iter(pairs))[1]
    return mappings


def _load_teaching_class_candidates(conn, teacher_id: int) -> list[dict[str, Any]]:
    """Load check-in schedule rows joined with the local classroom mapping.

    ``smart_classroom_schedule_items.class_offering_id`` is filled by the
    check-in sync's offering matcher, so joining through ``class_offerings``
    to ``classes`` yields the real class name (软件工程2303班) instead of the
    remote teaching-class code (计算机网络实验-0002).
    """
    try:
        rows = conn.execute(
            """
            SELECT ssi.remote_course_id, ssi.remote_course_name,
                   ssi.remote_teaching_class_name, ssi.weekday, ssi.sections_text,
                   ssi.student_count, ssi.class_offering_id, ssi.match_status,
                   COALESCE(cls.name, '') AS local_class_name
            FROM smart_classroom_schedule_items ssi
            LEFT JOIN class_offerings o ON o.id = ssi.class_offering_id
            LEFT JOIN classes cls ON cls.id = o.class_id
            WHERE ssi.teacher_id = ?
              AND (COALESCE(ssi.remote_teaching_class_name, '') <> ''
                   OR ssi.class_offering_id IS NOT NULL)
            """,
            (int(teacher_id),),
        ).fetchall()
    except Exception:  # noqa: BLE001 — 点名同步表不存在时跳过教学班标注
        return []
    return [dict(row) for row in rows]


# 显示班级名至少需要课程一致 + 一个时段/人数信号；写入课堂跳转 (offering)
# 需要课程一致 + 至少两个信号，并且最高分候选必须唯一映射到一个本地课堂。
_MIN_CLASS_NAME_SCORE = 5
_MIN_OFFERING_SCORE = 7

_EMPTY_CLASS_MATCH: dict[str, Any] = {
    "teaching_class_name": "",
    "local_class_name": "",
    "class_offering_id": None,
}


def _score_class_candidate(item: dict[str, Any], candidate: dict[str, Any], item_sections: set[int]) -> int:
    """Score one check-in schedule row against one course-schedule slot.

    The check-in sync stores weekday as 0-6 (local) while the schedule payload
    uses 1-7; both sides carry course code/name, section list and student
    count, which together identify the teaching class reliably.
    """
    code = _clean_text(candidate.get("remote_course_id"))
    name = _clean_text(candidate.get("remote_course_name"))
    code_ok = bool(code) and code == item["course_code"]
    name_ok = bool(name) and name == item["course_name"]
    if not code_ok and not name_ok:
        return 0
    score = 3 if code_ok else 2
    if _coerce_int(candidate.get("weekday"), -1) == item["weekday"] - 1:
        score += 2
    candidate_sections = set(_int_list(candidate.get("sections_text")))
    if candidate_sections and candidate_sections & item_sections:
        score += 2
    if item["student_count"] > 0 and _coerce_int(candidate.get("student_count")) == item["student_count"]:
        score += 2
    return score


def _match_teaching_class(item: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Resolve teaching-class labels and (if unambiguous) the local classroom.

    Returns ``{"teaching_class_name", "local_class_name", "class_offering_id"}``.
    The offering id is only set when every top-scoring candidate points at the
    same matched local classroom — a wrong deep link is worse than none.
    """
    item_sections = set(item["sections"])
    scored: list[tuple[int, dict[str, Any]]] = []
    for candidate in candidates:
        score = _score_class_candidate(item, candidate, item_sections)
        if score > 0:
            scored.append((score, candidate))
    if not scored:
        return dict(_EMPTY_CLASS_MATCH)
    best_score = max(score for score, _candidate in scored)
    if best_score < _MIN_CLASS_NAME_SCORE:
        return dict(_EMPTY_CLASS_MATCH)
    top = [candidate for score, candidate in scored if score == best_score]

    remote_names = {
        _clean_text(candidate.get("remote_teaching_class_name"))
        for candidate in top
        if _clean_text(candidate.get("remote_teaching_class_name"))
    }
    local_names = {
        _clean_text(candidate.get("local_class_name"))
        for candidate in top
        if _clean_text(candidate.get("local_class_name"))
    }
    offering_ids = {
        int(candidate["class_offering_id"])
        for candidate in top
        if candidate.get("class_offering_id")
        and str(candidate.get("match_status") or "") == "matched"
    }
    result = dict(_EMPTY_CLASS_MATCH)
    if len(remote_names) == 1:
        result["teaching_class_name"] = next(iter(remote_names))
    if len(local_names) == 1:
        result["local_class_name"] = next(iter(local_names))
    if best_score >= _MIN_OFFERING_SCORE and len(offering_ids) == 1:
        result["class_offering_id"] = offering_ids.pop()
    return result


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

        history_synced: list[tuple[str, str]] = []
        history_warnings: list[str] = []
        history_items: list[dict[str, Any]] = []
        history_meta: dict[tuple[str, str], dict[str, int]] = {}
        try:
            async with open_authenticated_smart_classroom_client(access_payload) as (client, _profile, _login):
                parsed = await _fetch_teacher_schedule(client)
                current_key = (parsed["year"], parsed["semester"])
                # 历史学期：从当前学期逐学期回溯请求。严格过滤——只保留
                # academic_year/term 与请求学期完全一致的条目；远端不支持
                # 参数时会原样返回当前学期，过滤后为空 → 跳过且不动本地。
                if current_key[0] and current_key[1]:
                    for hist_year, hist_term in _history_term_keys(*current_key):
                        try:
                            hist = await _fetch_teacher_schedule(
                                client, year=hist_year, semester=hist_term
                            )
                        except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
                            history_warnings.append(
                                f"{hist_year} 第{hist_term}学期历史课表拉取失败：{str(exc)[:80]}"
                            )
                            continue
                        hist_items = [
                            item
                            for item in hist["items"]
                            if (item["academic_year"], item["academic_term"]) == (hist_year, hist_term)
                        ]
                        if not hist_items:
                            continue
                        history_synced.append((hist_year, hist_term))
                        history_items.extend(hist_items)
                        highest_week = max(max(item["weeks"]) for item in hist_items)
                        history_meta[(hist_year, hist_term)] = {
                            "max_week": max(hist["max_week"], highest_week),
                        }
        except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
            return {
                "status": "failed",
                "message": f"智慧课堂课程表同步失败：{str(exc)[:180]}",
                "counts": {},
                "warnings": [str(exc)[:180]],
            }

        synced_at = _now_iso()
        current_items = parsed["items"]
        items = current_items + history_items
        # Terms present in the payloads — replacement is scoped to these so an
        # empty remote answer never wipes previously synced history.
        term_keys = {(item["academic_year"], item["academic_term"]) for item in items}
        if parsed["year"] or parsed["semester"]:
            term_keys.add((parsed["year"], parsed["semester"]))
        term_keys = {key for key in term_keys if key[0] or key[1]}

        matched_class_count = 0
        linked_offering_count = 0
        with get_db_connection() as conn:
            ensure_course_schedule_schema(conn)
            candidates = _load_teaching_class_candidates(conn, int(teacher_id))
            academic_map = _load_academic_class_mappings(conn, int(teacher_id))
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
                class_match = _match_teaching_class(item, candidates)
                # 教务系统的"教学班组成"是行政班名的权威对照，优先于
                # 点名同步推导的本地班级名。
                teaching_class_name = class_match["teaching_class_name"]
                if teaching_class_name:
                    composition = academic_map.get(
                        (item["course_code"], teaching_class_name)
                    ) or academic_map.get(teaching_class_name)
                    if composition:
                        class_match["local_class_name"] = composition
                if class_match["teaching_class_name"] or class_match["local_class_name"]:
                    matched_class_count += 1
                if class_match["class_offering_id"]:
                    linked_offering_count += 1
                conn.execute(
                    """
                    INSERT INTO smart_classroom_course_schedule_items (
                        teacher_id, platform_code, remote_id, course_name, course_code,
                        classroom, teaching_class_name, local_class_name,
                        class_offering_id, teacher_name, teacher_no,
                        academic_year, academic_term, weekday, sections_json, weeks_json,
                        week_text, single_or_double, student_count, metadata_json,
                        synced_at, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(teacher_id, platform_code, remote_id) DO UPDATE SET
                        course_name = excluded.course_name,
                        course_code = excluded.course_code,
                        classroom = excluded.classroom,
                        teaching_class_name = excluded.teaching_class_name,
                        local_class_name = excluded.local_class_name,
                        class_offering_id = excluded.class_offering_id,
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
                        class_match["teaching_class_name"],
                        class_match["local_class_name"],
                        class_match["class_offering_id"],
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
            # cur_week / 第 1 周锚点只属于远端标记的"当前学期"；历史学期
            # cur_week=0（没有"本周"概念），锚点交给平台学期设置在查询时解析。
            current_week1_monday = _derive_week1_monday(synced_at, parsed["cur_week"])
            for year, term in term_keys:
                is_current_term = (year, term) == (parsed["year"], parsed["semester"])
                term_items = [
                    item for item in items if (item["academic_year"], item["academic_term"]) == (year, term)
                ]
                highest_week = max((max(item["weeks"]) for item in term_items if item["weeks"]), default=0)
                if is_current_term:
                    cur_week_value = parsed["cur_week"]
                    cur_xq_value = parsed["cur_xq"]
                    max_week_value = max(parsed["max_week"], highest_week)
                    week1_value = current_week1_monday
                else:
                    cur_week_value = 0
                    cur_xq_value = 0
                    max_week_value = max(
                        history_meta.get((year, term), {}).get("max_week", 0), highest_week
                    )
                    week1_value = ""
                conn.execute(
                    """
                    INSERT INTO smart_classroom_course_schedule_meta (
                        teacher_id, platform_code, academic_year, academic_term,
                        cur_week, max_week, cur_xq, item_count, week1_monday_date,
                        synced_at, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(teacher_id, platform_code, academic_year, academic_term) DO UPDATE SET
                        cur_week = excluded.cur_week,
                        max_week = excluded.max_week,
                        cur_xq = excluded.cur_xq,
                        item_count = excluded.item_count,
                        week1_monday_date = excluded.week1_monday_date,
                        synced_at = excluded.synced_at,
                        updated_at = excluded.updated_at
                    """,
                    (
                        int(teacher_id),
                        SMART_PLATFORM_CODE,
                        year,
                        term,
                        cur_week_value,
                        max_week_value,
                        cur_xq_value,
                        len(term_items),
                        week1_value,
                        synced_at,
                        synced_at,
                        synced_at,
                    ),
                )
            conn.commit()

        total_hours = sum(len(item["sections"]) * len(item["weeks"]) for item in current_items)
        course_count = len({(item["course_name"], item["course_code"]) for item in current_items})
        counts = {
            "schedule_count": len(current_items),
            "course_count": course_count,
            "total_hours": total_hours,
            "matched_class_count": matched_class_count,
            "linked_offering_count": linked_offering_count,
            "history_term_count": len(history_synced),
            "history_schedule_count": len(history_items),
        }
        if not items:
            return {
                "status": "empty",
                "message": "智慧课堂本学期暂未返回任何排课记录。",
                "counts": counts,
                "warnings": history_warnings,
                "synced_at": synced_at,
            }
        history_text = (
            f"；另同步 {len(history_synced)} 个历史学期（{len(history_items)} 条排课）"
            if history_synced
            else ""
        )
        return {
            "status": "success",
            "message": (
                f"已同步 {len(current_items)} 条排课、{course_count} 门课程，"
                f"本学期共 {total_hours} 课时"
                + (f"；{matched_class_count} 条已标注班级" if matched_class_count else "")
                + (f"；{linked_offering_count} 条已关联本地课堂" if linked_offering_count else "")
                + history_text
                + "。"
            ),
            "counts": counts,
            "warnings": history_warnings,
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
    local_class_name = _clean_text(row_dict.get("local_class_name"))
    raw_offering_id = row_dict.get("class_offering_id")
    class_offering_id = int(raw_offering_id) if raw_offering_id else None
    item = {
        "id": int(row_dict["id"]),
        "remote_id": str(row_dict.get("remote_id") or ""),
        "course_name": str(row_dict.get("course_name") or ""),
        "course_code": str(row_dict.get("course_code") or ""),
        "classroom": str(row_dict.get("classroom") or ""),
        "classroom_short": _short_classroom(row_dict.get("classroom")),
        "teaching_class_name": class_name,
        "local_class_name": local_class_name,
        "class_offering_id": class_offering_id,
        "classroom_url": f"/classroom/{class_offering_id}" if class_offering_id else "",
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
    _apply_class_label(item)
    return item


def _apply_class_label(item: dict[str, Any]) -> None:
    """按优先级设置 class_label / class_is_fallback：
    本地真实班级名（软工2303班）> 智慧课堂教学班名 > 人数兜底。
    """
    if item.get("local_class_name"):
        item["class_label"] = item["local_class_name"]
        item["class_is_fallback"] = False
    elif item.get("teaching_class_name"):
        item["class_label"] = item["teaching_class_name"]
        item["class_is_fallback"] = False
    else:
        item["class_label"] = _fallback_class_label({"student_count": item["student_count"]})
        item["class_is_fallback"] = True


def _resolve_item_class_name(item: dict[str, Any], academic_map: dict[Any, str]) -> None:
    """读取时用教务名单关系把教学班代号解析成真实行政班名（就地修改 item）。

    班级名解析放在读取时（而非仅同步时）：存量课表可能在映射逻辑上线前
    就已同步、local_class_name 为空只能显示教学班代号；每次打开课表都按
    最新名单关系重算，存量数据自动自愈，无需重新同步或重置数据库。
    键优先 (course_code, teaching_class_name)，回退教学班名单键。
    """
    if not academic_map:
        return
    tcn = item.get("teaching_class_name") or ""
    if not tcn:
        return
    resolved = academic_map.get((item.get("course_code") or "", tcn)) or academic_map.get(tcn)
    if resolved:
        item["local_class_name"] = resolved
        _apply_class_label(item)


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
        # 人数兜底标签（"教学班 · N人"）与"最多 N 人"信息重复，不进班级 chips。
        if not item["class_is_fallback"]:
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


def _build_week_deck(
    items: list[dict[str, Any]],
    *,
    max_week: int,
    cur_week: int,
    week1_monday: date | None = None,
    session_no_map: dict[tuple[int, int], tuple[int, int]] | None = None,
) -> list[dict[str, Any]]:
    session_no_map = session_no_map or {}
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
                "classroom_short": item["classroom_short"],
                "class_label": item["class_label"],
                "class_is_fallback": item["class_is_fallback"],
                "class_offering_id": item["class_offering_id"],
                "classroom_url": item["classroom_url"],
                "create_url": item.get("create_url", ""),
                "session_no": session_no_map.get((item["id"], week_index), (0, 0))[0],
                "session_total": session_no_map.get((item["id"], week_index), (0, 0))[1],
                "single_or_double": item["single_or_double"],
                "single_or_double_label": item["single_or_double_label"],
                "student_count": item["student_count"],
                "hours": item["hours_per_meeting"],
            }
            for item in items
            if week_index in item["weeks"]
        ]
        lessons.sort(key=lambda lesson: (lesson["weekday"], lesson["sections"][0] if lesson["sections"] else 0))
        monday = week1_monday + timedelta(weeks=week_index - 1) if week1_monday else None
        sunday = monday + timedelta(days=6) if monday else None
        weeks.append(
            {
                "week_index": week_index,
                "label": f"第{week_index}周",
                "is_current": week_index == cur_week,
                "monday_date": monday.isoformat() if monday else "",
                "date_range_label": (
                    f"{monday.month}月{monday.day}日 – {sunday.month}月{sunday.day}日"
                    if monday and sunday
                    else ""
                ),
                "lesson_count": len(lessons),
                "total_hours": sum(lesson["hours"] for lesson in lessons),
                "lessons": lessons,
            }
        )
    return weeks


def _build_session_no_map(items: list[dict[str, Any]]) -> dict[tuple[int, int], tuple[int, int]]:
    """(item_id, week_index) → (本学期第 N 次课, 总次数)。

    分组键 = 课程 + 教学班：同一门课同一个班的所有上课时点（周、星期、
    起始节）按时间排序后编号，由周次推算出"第几次课"。
    """
    groups: dict[tuple[str, str], list[tuple[int, int, int, int]]] = {}
    for item in items:
        group_key = (
            item["course_code"] or item["course_name"],
            item["teaching_class_name"] or item["class_label"],
        )
        first_section = item["sections"][0] if item["sections"] else 0
        for week in item["weeks"]:
            groups.setdefault(group_key, []).append(
                (week, item["weekday"], first_section, item["id"])
            )
    session_map: dict[tuple[int, int], tuple[int, int]] = {}
    for occurrences in groups.values():
        occurrences.sort()
        total = len(occurrences)
        for index, (week, _weekday, _section, item_id) in enumerate(occurrences, start=1):
            session_map[(item_id, week)] = (index, total)
    return session_map


def _load_local_offerings(conn, teacher_id: int) -> list[dict[str, Any]]:
    """本教师的平台课堂（含课程名/班级名/学期），用于课表→课堂宽松匹配。"""
    try:
        rows = conn.execute(
            """
            SELECT o.id, o.semester, o.semester_id,
                   c.name AS course_name, cl.name AS class_name,
                   COALESCE(sem.name, '') AS semester_name
            FROM class_offerings o
            JOIN courses c ON c.id = o.course_id
            JOIN classes cl ON cl.id = o.class_id
            LEFT JOIN academic_semesters sem ON sem.id = o.semester_id
            WHERE o.teacher_id = ?
            """,
            (int(teacher_id),),
        ).fetchall()
    except Exception:  # noqa: BLE001
        return []
    return [dict(row) for row in rows]


def _offering_term_key(offering: dict[str, Any]) -> tuple[str, str] | None:
    for source in (offering.get("semester_name"), offering.get("semester")):
        key = _parse_platform_semester_name(source)
        if key is not None:
            return key
    return None


def _text_match_score(left: Any, right: Any) -> int:
    """名称匹配强度：2=完全相等（忽略大小写），1=互为子串，0=无关。

    大小写不敏感：课表课程名"动态Web程序设计"与平台课堂"动态web程序设计"
    应视为同一门课。精确匹配（2）优先于子串（1），用于区分"计算机网络"
    与"计算机网络实验"这类互为子串但不同的课程。
    """
    left_text = _clean_text(left).casefold()
    right_text = _clean_text(right).casefold()
    if not left_text or not right_text:
        return 0
    if left_text == right_text:
        return 2
    if left_text in right_text or right_text in left_text:
        return 1
    return 0


def _match_local_offering(
    item: dict[str, Any],
    offerings: list[dict[str, Any]],
    term_key: tuple[str, str] | None,
) -> int | None:
    """匹配平台课堂：课程名 + 班级名都要有交集，学期兼容，取唯一最优。

    - 学期兼容 = 双方都能解析出 (学年, 学期) 时必须一致；任一方解析不出
      则不作为否决条件（避免硬匹配漏掉信息）。
    - 打分 = 课程名匹配强度 + 班级名匹配强度（精确 2 / 子串 1）。只有唯一
      拿到最高分的候选才链接——保证"计算机网络"命中"计算机网络"课堂而非
      同时命中被它子串串味的"计算机网络实验"课堂；并列最高分则判为歧义、
      不链接（宁缺勿错）。
    """
    scored: list[tuple[int, int]] = []  # (score, offering_id)
    for offering in offerings:
        course_score = _text_match_score(offering.get("course_name"), item["course_name"])
        if course_score == 0:
            continue
        class_name = offering.get("class_name")
        class_score = max(
            _text_match_score(class_name, item["local_class_name"]),
            _text_match_score(class_name, item["teaching_class_name"]),
        )
        if class_score == 0:
            continue
        offering_key = _offering_term_key(offering)
        if offering_key is not None and term_key is not None and offering_key != term_key:
            continue
        scored.append((course_score + class_score, int(offering["id"])))
    if not scored:
        return None
    best_score = max(score for score, _oid in scored)
    best_ids = {oid for score, oid in scored if score == best_score}
    return best_ids.pop() if len(best_ids) == 1 else None


def _offering_create_url(item: dict[str, Any], year: str, term: str) -> str:
    """无对应课堂时的"新建课堂"深链，自动带入课表信息供开课页预填。"""
    from urllib.parse import urlencode

    params = {
        "prefill": "smart_schedule",
        "course": item["course_name"],
        "class_name": item["local_class_name"] or item["teaching_class_name"],
        "year": year,
        "term": term,
    }
    return "/manage/teaching/offerings?" + urlencode(
        {key: value for key, value in params.items() if value}
    )


def _resolve_term_anchor(
    entry: dict[str, Any], platform_anchors: dict[tuple[str, str], dict[str, Any]]
) -> dict[str, Any]:
    """教学周锚点解析：平台学期设置 > 同步时推算 > 无锚点。

    平台学期（academic_semesters）是全平台教学活动的权威时间基准，教师改
    了学期设置后无需重新同步即可生效；同步锚点由 synced_at+curWeek 反推。
    """
    platform = platform_anchors.get((entry["year"], entry["term"]))
    if platform is not None:
        return {
            "week1_monday": platform["week1_monday"],
            "week_count_hint": max(platform["week_count"], entry["max_week"]),
            "anchor_source": "platform",
            "anchor_label": f"平台学期设置（{platform['name']}）",
            "semester_id": platform["semester_id"],
        }
    sync_monday = _parse_iso_date(entry.get("week1_monday_date"))
    if sync_monday is not None:
        return {
            "week1_monday": sync_monday,
            "week_count_hint": entry["max_week"],
            "anchor_source": "sync",
            "anchor_label": "按智慧课堂同步时的教学周推算",
            "semester_id": 0,
        }
    return {
        "week1_monday": None,
        "week_count_hint": entry["max_week"],
        "anchor_source": "",
        "anchor_label": "",
        "semester_id": 0,
    }


def _term_status(anchor: dict[str, Any], today: date) -> str:
    """current / ended / future / unknown（无锚点无法判断日期归属）。"""
    week1 = anchor["week1_monday"]
    weeks_total = max(_coerce_int(anchor["week_count_hint"]), 1)
    if week1 is None:
        return "unknown"
    if today < week1:
        return "future"
    if today > week1 + timedelta(days=weeks_total * 7 - 1):
        return "ended"
    return "current"


def build_teacher_course_schedule_overview(
    conn,
    teacher_id: int,
    *,
    year: str = "",
    term: str = "",
    course: str = "",
    class_label: str = "",
) -> dict[str, Any]:
    """Build the filtered overview: filters + aggregation + week deck.

    学年学期与教学周对齐规则（教学正确性关键，勿随意改动）：
    - 教学周锚点优先用平台学期设置（academic_semesters + 逐日学期日历的
      week_index，权威且含人工调整），其次用同步时推算的第 1 周周一；
    - "本周"由锚点 + 今天动态计算，不使用同步时落库的 cur_week 快照，
      避免长时间未同步导致周次漂移；
    - 默认选中学期：日期落在其教学周范围内的"进行中"学期；假期（无进行
      中学期）选最近结束的学期并定位其最后一个教学周（focus_week）。
    """
    ensure_course_schedule_schema(conn)
    today = _today_local()
    meta_rows = _load_meta_rows(conn, int(teacher_id))
    platform_anchors = _load_platform_semester_anchors(conn, int(teacher_id))
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
            "week1_monday_date": str(meta.get("week1_monday_date") or ""),
            "synced_at": _display_datetime(meta.get("synced_at")),
        }
        for meta in meta_rows
    ]
    for entry in terms:
        anchor = _resolve_term_anchor(entry, platform_anchors)
        entry["anchor_source"] = anchor["anchor_source"]
        entry["week1_monday"] = (
            anchor["week1_monday"].isoformat() if anchor["week1_monday"] else ""
        )
        entry["status"] = _term_status(anchor, today)
        entry["_anchor"] = anchor
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

    # 学期选择：显式请求 > 进行中的学期 > 最近结束的学期（假期场景）> 最新学期。
    selected = next(
        (entry for entry in terms if entry["year"] == year and entry["term"] == term),
        None,
    )
    if selected is None:
        selected = next((entry for entry in terms if entry["status"] == "current"), None)
    if selected is None:
        ended_terms = [entry for entry in terms if entry["status"] == "ended"]
        if ended_terms:
            selected = max(ended_terms, key=lambda entry: entry["week1_monday"])
    if selected is None:
        selected = terms[0]
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

    # 读取时用教务名单关系把教学班代号解析成真实行政班名（存量课表自愈，
    # 必须在下面 offering 匹配之前完成，好让宽松匹配用上真实班级名）。
    academic_map = _load_academic_class_mappings(conn, int(teacher_id))
    for item in all_items:
        _resolve_item_class_name(item, academic_map)

    # 课表 → 平台课堂：同步时的严格匹配优先；未命中的再按
    # 课程名+班级名+学期宽松匹配；仍无对应 → 提供预填的新建课堂链接。
    local_offerings = _load_local_offerings(conn, int(teacher_id))
    selected_term_key = (
        (selected["year"], selected["term"]) if selected["year"] and selected["term"] else None
    )
    for item in all_items:
        if not item["class_offering_id"]:
            matched_offering_id = _match_local_offering(item, local_offerings, selected_term_key)
            if matched_offering_id:
                item["class_offering_id"] = matched_offering_id
                item["classroom_url"] = f"/classroom/{matched_offering_id}"
        item["create_url"] = (
            ""
            if item["class_offering_id"]
            else _offering_create_url(item, selected["year"], selected["term"])
        )
    # 第 N 次课编号基于全量条目（不受课程/班级筛选影响，编号稳定）。
    session_no_map = _build_session_no_map(all_items)

    course_options = sorted({item["course_name"] for item in all_items if item["course_name"]})
    class_options = sorted({item["class_label"] for item in all_items if item["class_label"]})

    items = all_items
    if course:
        items = [item for item in items if item["course_name"] == course]
    if class_label:
        items = [item for item in items if item["class_label"] == class_label]

    # 动态"本周"与打开定位（focus_week）：
    # - 进行中：本周 = 平台学期日历逐日 week_index（权威）或按锚点周一推算；
    # - 已结束（假期）：无"本周"，定位最后一个教学周；
    # - 未开始：定位第 1 周；
    # - 无锚点：退回同步时落库的 cur_week 快照。
    anchor = selected["_anchor"]
    status = selected["status"]
    live_cur_week = 0
    if status == "current":
        live_cur_week = _platform_week_for_date(conn, anchor["semester_id"], today)
        if live_cur_week <= 0:
            live_cur_week = ((today - anchor["week1_monday"]).days // 7) + 1
    elif status == "unknown":
        live_cur_week = selected["cur_week"]

    course_stats = _build_course_stats(items)
    weeks = _build_week_deck(
        items,
        max_week=selected["max_week"],
        cur_week=live_cur_week,
        week1_monday=anchor["week1_monday"],
        session_no_map=session_no_map,
    )
    if status == "ended":
        focus_week = len(weeks)
    elif status == "future":
        focus_week = 1 if weeks else 0
    else:
        focus_week = min(max(live_cur_week, 1), len(weeks)) if weeks else 0
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
        "cur_week": live_cur_week,
        "max_week": selected["max_week"],
        "term_status": status,
        "week1_monday": selected["week1_monday"],
        "anchor_source": anchor["anchor_source"],
        "anchor_label": anchor["anchor_label"],
        "weekly_average_hours": (
            round(total_hours / len([week for week in weeks if week["lesson_count"]]), 1)
            if any(week["lesson_count"] for week in weeks)
            else 0
        ),
    }

    selected_payload = {
        key: value for key, value in selected.items() if key != "_anchor"
    }
    selected_payload["focus_week"] = focus_week
    selected_payload["live_cur_week"] = live_cur_week
    terms_payload = [
        {key: value for key, value in entry.items() if key != "_anchor"} for entry in terms
    ]

    return {
        "status": "success",
        "has_data": bool(all_items),
        "message": "",
        "terms": terms_payload,
        "selected_term": selected_payload,
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
