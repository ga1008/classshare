from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote_plus, urljoin, urlparse
from xml.etree import ElementTree

import httpx

from ..config import AI_ASSISTANT_URL
from ..database import get_db_connection
from ..db.connection import execute_insert_returning_id
from .academic_integration_service import (
    load_teacher_academic_access_method,
    open_authenticated_academic_client,
)
from .academic_service import (
    build_holiday_lookup,
    china_now,
    compute_semester_week_count,
    infer_semester_name,
    parse_date_input,
)
from .organization_scope_service import load_teacher_org_scope


SYNC_STATUS_PENDING = "pending"
SYNC_STATUS_RUNNING = "running"
SYNC_STATUS_SYNCED = "synced"
SYNC_STATUS_GENERATED = "generated"
SYNC_STATUS_PARTIAL = "partial"
SYNC_STATUS_FAILED = "failed"

DAY_TYPE_TEACHING = "teaching_day"
DAY_TYPE_WEEKEND = "weekend"
DAY_TYPE_HOLIDAY = "holiday"
DAY_TYPE_WORKDAY = "workday"

ACADEMIC_CALENDAR_TIMEOUT_SECONDS = 14.0
HOLIDAY_CRAWLER_TIMEOUT_SECONDS = 12.0
HOLIDAY_AI_TIMEOUT_SECONDS = 90.0
MAX_CRAWLER_ITEMS = 36

ZF_HOME_CALENDAR_PATHS: tuple[str, ...] = (
    "/xtgl/index_initMenu.html",
    "/xtgl/index_cxAreaSix.html",
    "/xtgl/index_cxAreaSix.html?localeKey=zh_CN",
    "/xtgl/index_cxAreaSix.html?gnmkdm=index",
    "/xtgl/index_cxDbsy.html",
    "/xtgl/index_cxYhxxIndex.html",
)


@dataclass
class CalendarEvent:
    date: str
    day_type: str
    label: str
    source: str
    source_url: str = ""
    confidence: float = 0.7
    metadata: dict[str, Any] | None = None


@dataclass
class CalendarAlignment:
    name: str = ""
    start_date: str = ""
    end_date: str = ""
    week_count: int = 0
    source: str = ""
    source_url: str = ""
    confidence: float = 0.0


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
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _strip_html(value: Any) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return _normalize_space(text)


def _domain(url: str) -> str:
    try:
        return urlparse(str(url or "")).netloc.lower()
    except Exception:
        return ""


def _date_key(value: Any) -> str:
    parsed = parse_date_input(value)
    return parsed.isoformat() if parsed else ""


def _event_kind(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"workday", "makeup", "makeup_workday", "adjusted_workday", "补班", "补课", "调休上班"}:
        return DAY_TYPE_WORKDAY
    if normalized in {"holiday", "vacation", "off", "放假", "节假日"}:
        return DAY_TYPE_HOLIDAY
    return ""


def _term_number(value: str) -> str:
    normalized = str(value or "").strip()
    if normalized in {"1", "一", "第一"}:
        return "1"
    if normalized in {"2", "二", "第二"}:
        return "2"
    return normalized


def _candidate_is_plausible(candidate: CalendarAlignment, requested_start: date, requested_end: date) -> bool:
    start = parse_date_input(candidate.start_date)
    end = parse_date_input(candidate.end_date)
    if not start or not end or end < start:
        return False
    overlap_start = max(start, requested_start)
    overlap_end = min(end, requested_end)
    if overlap_end < overlap_start:
        return False
    overlap_days = (overlap_end - overlap_start).days + 1
    candidate_days = max(1, (end - start).days + 1)
    requested_days = max(1, (requested_end - requested_start).days + 1)
    return overlap_days >= 14 or overlap_days / min(candidate_days, requested_days) >= 0.35


def _pick_best_alignment(candidates: list[CalendarAlignment], requested_start: date, requested_end: date) -> CalendarAlignment | None:
    plausible = [item for item in candidates if _candidate_is_plausible(item, requested_start, requested_end)]
    if not plausible:
        return None

    def score(item: CalendarAlignment) -> tuple[float, int]:
        start = parse_date_input(item.start_date) or requested_start
        end = parse_date_input(item.end_date) or requested_end
        overlap_start = max(start, requested_start)
        overlap_end = min(end, requested_end)
        overlap_days = max(0, (overlap_end - overlap_start).days + 1)
        boundary_distance = abs((start - requested_start).days) + abs((end - requested_end).days)
        return (overlap_days * float(item.confidence or 0.0), -boundary_distance)

    return max(plausible, key=score)


def _pick_current_alignment(candidates: list[CalendarAlignment], reference_date: date) -> CalendarAlignment | None:
    valid: list[tuple[CalendarAlignment, date, date]] = []
    for item in candidates:
        start = parse_date_input(item.start_date)
        end = parse_date_input(item.end_date)
        if not start or not end or end < start:
            continue
        valid.append((item, start, end))
    if not valid:
        return None

    def score(entry: tuple[CalendarAlignment, date, date]) -> tuple[int, int, float, int]:
        item, start, end = entry
        contains_today = start <= reference_date <= end
        if contains_today:
            distance = 0
        elif reference_date < start:
            distance = (start - reference_date).days
        else:
            distance = (reference_date - end).days
        duration = max(1, (end - start).days + 1)
        return (1 if contains_today else 0, -distance, float(item.confidence or 0.0), -duration)

    best, start, end = max(valid, key=score)
    if start <= reference_date <= end:
        return best
    nearest_days = min(abs((start - reference_date).days), abs((end - reference_date).days))
    return best if nearest_days <= 45 else None


def _parse_academic_calendar_alignment(page_html: str, *, source_url: str) -> list[CalendarAlignment]:
    text = _strip_html(page_html)
    candidates: list[CalendarAlignment] = []
    patterns = (
        re.compile(
            r"(?P<name>(?P<start_year>20\d{2})\s*[-－—]\s*(?P<end_year>20\d{2})\s*学年\s*第?\s*(?P<term>[12一二])\s*学期)"
            r"\s*[（(]\s*(?P<start>20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}日?)\s*(?:至|到|[-—~～])\s*"
            r"(?P<end>20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}日?)\s*[）)]"
        ),
        re.compile(
            r"(?P<name>(?P<start_year>20\d{2})\s*[-－—]\s*(?P<end_year>20\d{2})\s*第?\s*(?P<term>[12一二])\s*学期)"
            r".{0,20}?(?P<start>20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}日?)\s*(?:至|到|[-—~～])\s*"
            r"(?P<end>20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}日?)"
        ),
    )
    for pattern in patterns:
        for match in pattern.finditer(text):
            start = _date_key(match.group("start").replace("年", "-").replace("月", "-").replace("日", ""))
            end = _date_key(match.group("end").replace("年", "-").replace("月", "-").replace("日", ""))
            if not start or not end:
                continue
            start_date = parse_date_input(start)
            end_date = parse_date_input(end)
            if not start_date or not end_date or end_date < start_date:
                continue
            name = _normalize_space(match.group("name"))
            if "第" not in name and "学期" in name:
                term = _term_number(match.groupdict().get("term") or "")
                if term in {"1", "2"}:
                    name = f"{match.group('start_year')}-{match.group('end_year')}学年第{term}学期"
            candidates.append(
                CalendarAlignment(
                    name=name,
                    start_date=start,
                    end_date=end,
                    week_count=compute_semester_week_count(start_date, end_date),
                    source="academic_system",
                    source_url=source_url,
                    confidence=0.92,
                )
            )
    return candidates


async def _fetch_academic_alignment_candidates(
    access_payload: dict[str, Any] | None,
) -> tuple[list[CalendarAlignment], list[dict[str, Any]]]:
    if not access_payload:
        return [], [{"source": "academic_system", "status": "skipped", "message": "未配置可用教务系统账号"}]

    candidates: list[CalendarAlignment] = []
    source_summaries: list[dict[str, Any]] = []
    try:
        async with open_authenticated_academic_client(access_payload) as (client, profile, _login_result):
            for path in ZF_HOME_CALENDAR_PATHS:
                url = urljoin(profile.base_url, path)
                try:
                    response = await client.get(path, follow_redirects=True, timeout=ACADEMIC_CALENDAR_TIMEOUT_SECONDS)
                    if response.status_code >= 400:
                        continue
                except httpx.HTTPError:
                    continue
                parsed = _parse_academic_calendar_alignment(response.text, source_url=url)
                if parsed:
                    candidates.extend(parsed)
                    source_summaries.append(
                        {
                            "source": "academic_system",
                            "status": "success",
                            "url": url,
                            "found": len(parsed),
                        }
                    )
                    break
            if not source_summaries:
                source_summaries.append(
                    {
                        "source": "academic_system",
                        "status": "partial",
                        "message": "已登录教务系统，但首页未解析到教学日历范围",
                    }
                )
    except Exception as exc:
        source_summaries.append(
            {
                "source": "academic_system",
                "status": "failed",
                "message": str(exc)[:220],
            }
        )

    return candidates, source_summaries


async def _fetch_academic_alignment(
    access_payload: dict[str, Any] | None,
    *,
    requested_start: date,
    requested_end: date,
) -> tuple[CalendarAlignment | None, list[dict[str, Any]]]:
    candidates, source_summaries = await _fetch_academic_alignment_candidates(access_payload)
    return _pick_best_alignment(candidates, requested_start, requested_end), source_summaries


def _rss_templates(keyword: str, recent_days: int) -> list[str]:
    encoded = quote_plus(keyword)
    return [
        f"https://www.bing.com/news/search?q={encoded}&format=RSS&setlang=zh-CN&cc=CN&freshness={'Week' if recent_days <= 14 else 'Month'}",
        f"https://news.google.com/rss/search?q={encoded}+when:{max(1, min(recent_days, 365))}d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
    ]


def _local_name(tag: str) -> str:
    return str(tag or "").rsplit("}", 1)[-1].lower()


def _xml_text(item: ElementTree.Element, name: str) -> str:
    for child in item.iter():
        if _local_name(child.tag) == name.lower() and child.text:
            return _normalize_space(child.text)
    return ""


def _xml_link(item: ElementTree.Element) -> str:
    link = _xml_text(item, "link")
    if link:
        return link
    for child in item.iter():
        if _local_name(child.tag) == "link":
            href = child.attrib.get("href")
            if href:
                return href
    return ""


def _parse_rss_items(feed_text: str, *, source_name: str) -> list[dict[str, Any]]:
    try:
        root = ElementTree.fromstring(feed_text.encode("utf-8"))
    except ElementTree.ParseError:
        return []
    parsed: list[dict[str, Any]] = []
    for item in root.iter():
        if _local_name(item.tag) not in {"item", "entry"}:
            continue
        title = _xml_text(item, "title")
        url = _xml_link(item)
        if not title or not url:
            continue
        parsed.append(
            {
                "source": source_name,
                "title": title[:260],
                "url": url,
                "summary": _strip_html(_xml_text(item, "description") or _xml_text(item, "summary"))[:700],
                "published_at": _xml_text(item, "pubDate") or _xml_text(item, "published") or _xml_text(item, "updated"),
            }
        )
    return parsed


def _holiday_keywords(years: set[int]) -> list[str]:
    keywords: list[str] = []
    for year in sorted(years):
        keywords.extend(
            [
                f"{year} 年 国务院 放假安排 调休 上班 通知",
                f"{year} 年 广西 壮族三月三 放假 通知 调休 补班",
                f"{year} 年 广西外国语学院 校历 放假 补课",
            ]
        )
    return keywords


async def _crawl_holiday_sources(start_date: date, end_date: date) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    years = set(range(start_date.year, end_date.year + 1))
    recent_days = max(30, min(365, (china_now().date() - start_date).days + 365))
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; LanShareAcademicCalendarCrawler/1.0)",
        "Accept": "application/rss+xml, application/xml, text/xml, text/html;q=0.8, */*;q=0.5",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.4",
    }
    items: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    async with httpx.AsyncClient(headers=headers, timeout=HOLIDAY_CRAWLER_TIMEOUT_SECONDS, follow_redirects=True) as client:
        for keyword in _holiday_keywords(years):
            for url in _rss_templates(keyword, recent_days):
                if len(items) >= MAX_CRAWLER_ITEMS:
                    break
                try:
                    response = await client.get(url)
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    summaries.append(
                        {
                            "source": "crawler",
                            "status": "failed",
                            "domain": _domain(url),
                            "keyword": keyword,
                            "message": str(exc)[:160],
                        }
                    )
                    continue
                parsed_items = _parse_rss_items(response.text, source_name=_domain(url) or "RSS")
                added = 0
                for item in parsed_items:
                    canonical = str(item.get("url") or "").strip()
                    if not canonical or canonical in seen_urls:
                        continue
                    seen_urls.add(canonical)
                    haystack = f"{item.get('title') or ''} {item.get('summary') or ''}"
                    if not any(token in haystack for token in ("放假", "调休", "补班", "补课", "三月三", "校历")):
                        continue
                    item["keyword"] = keyword
                    items.append(item)
                    added += 1
                    if len(items) >= MAX_CRAWLER_ITEMS:
                        break
                summaries.append(
                    {
                        "source": "crawler",
                        "status": "success",
                        "domain": _domain(url),
                        "keyword": keyword,
                        "found": added,
                    }
                )
            if len(items) >= MAX_CRAWLER_ITEMS:
                break

    return items[:MAX_CRAWLER_ITEMS], summaries


def _parse_feed_date(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = parsedate_to_datetime(raw)
        return parsed.date().isoformat()
    except Exception:
        return raw[:10]


async def _call_holiday_ai(
    *,
    start_date: date,
    end_date: date,
    crawler_items: list[dict[str, Any]],
    built_in_events: list[CalendarEvent],
) -> tuple[list[CalendarEvent], list[dict[str, Any]]]:
    if not crawler_items and not built_in_events:
        return [], [{"source": "ai", "status": "skipped", "message": "没有可供 AI 交叉核对的候选材料"}]

    candidate_lines = []
    for index, item in enumerate(crawler_items[:MAX_CRAWLER_ITEMS], start=1):
        candidate_lines.append(
            "\n".join(
                [
                    f"ID: {index}",
                    f"标题: {item.get('title') or ''}",
                    f"来源: {item.get('source') or _domain(item.get('url') or '')}",
                    f"发布时间: {_parse_feed_date(item.get('published_at')) or '未知'}",
                    f"摘要: {item.get('summary') or ''}",
                    f"链接: {item.get('url') or ''}",
                ]
            )
        )
    built_in_lines = [
        f"{event.date} {event.day_type} {event.label} {event.source_url or ''}"
        for event in built_in_events
    ]
    system_prompt = (
        "你是高校教务校历核对助手，只输出合法 JSON。"
        "任务是从候选通知中提取指定日期范围内的中国法定节假日、广西三月三等广西适配假期、调休上班和补课日期。"
        "只保留有明确日期依据的事件，不要编造；不确定的内容降低 confidence 或忽略。"
    )
    user_message = f"""
日期范围：{start_date.isoformat()} 至 {end_date.isoformat()}

请输出：
{{
  "events": [
    {{"date": "YYYY-MM-DD", "kind": "holiday 或 workday", "label": "节日/补课说明", "scope": "national/guangxi/school", "source_url": "来源链接", "confidence": 0.0}}
  ],
  "notes": ["简短说明"]
}}

已有本地基线事件：
{chr(10).join(built_in_lines) if built_in_lines else "无"}

爬虫候选：
{"\n\n---\n\n".join(candidate_lines) if candidate_lines else "无"}
""".strip()
    try:
        async with httpx.AsyncClient(base_url=AI_ASSISTANT_URL, timeout=HOLIDAY_AI_TIMEOUT_SECONDS) as client:
            response = await client.post(
                "/api/ai/chat",
                json={
                    "system_prompt": system_prompt,
                    "messages": [],
                    "new_message": user_message,
                    "model_capability": "thinking",
                    "task_type": "deep_text_reasoning",
                    "response_format": "json",
                    "task_priority": "background",
                    "task_label": "academic_calendar_holiday_extract",
                },
            )
            response.raise_for_status()
            payload = response.json()
        response_json = payload.get("response_json") if isinstance(payload, dict) else None
        if not isinstance(response_json, dict):
            raise RuntimeError("AI 未返回 JSON 对象")
    except Exception as exc:
        return [], [{"source": "ai", "status": "failed", "message": str(exc)[:220]}]

    events: list[CalendarEvent] = []
    for item in response_json.get("events") if isinstance(response_json.get("events"), list) else []:
        if not isinstance(item, dict):
            continue
        event_date = _date_key(item.get("date"))
        parsed_date = parse_date_input(event_date)
        if not event_date or not parsed_date or parsed_date < start_date or parsed_date > end_date:
            continue
        day_type = _event_kind(item.get("kind"))
        if day_type not in {DAY_TYPE_HOLIDAY, DAY_TYPE_WORKDAY}:
            continue
        try:
            confidence = float(item.get("confidence") or 0.72)
        except (TypeError, ValueError):
            confidence = 0.72
        events.append(
            CalendarEvent(
                date=event_date,
                day_type=day_type,
                label=_normalize_space(item.get("label") or ("调休上课" if day_type == DAY_TYPE_WORKDAY else "节假日"))[:80],
                source="ai",
                source_url=str(item.get("source_url") or "")[:500],
                confidence=max(0.0, min(1.0, confidence)),
                metadata={"scope": item.get("scope") or "", "notes": response_json.get("notes") or []},
            )
        )
    return events, [{"source": "ai", "status": "success", "found": len(events)}]


def _built_in_events(start_date: date, end_date: date) -> list[CalendarEvent]:
    lookup = build_holiday_lookup(range(start_date.year, end_date.year + 1))
    events: list[CalendarEvent] = []
    for iso_date, info in sorted(lookup.items()):
        parsed = parse_date_input(iso_date)
        if not parsed or parsed < start_date or parsed > end_date:
            continue
        day_type = _event_kind(info.get("kind"))
        if not day_type:
            continue
        events.append(
            CalendarEvent(
                date=iso_date,
                day_type=day_type,
                label=str(info.get("label") or ("调休上课" if day_type == DAY_TYPE_WORKDAY else "节假日")),
                source="built_in",
                confidence=0.78,
                metadata={"scope": info.get("scope") or ""},
            )
        )
    return events


def _merge_events(events: list[CalendarEvent]) -> dict[str, CalendarEvent]:
    merged: dict[str, CalendarEvent] = {}
    source_rank = {"academic_system": 4, "ai": 3, "crawler": 2, "built_in": 1, "generated": 0}
    for event in events:
        parsed = parse_date_input(event.date)
        if not parsed:
            continue
        current = merged.get(event.date)
        if current is None:
            merged[event.date] = event
            continue
        current_score = source_rank.get(current.source, 0) + float(current.confidence or 0.0)
        next_score = source_rank.get(event.source, 0) + float(event.confidence or 0.0)
        if next_score >= current_score:
            merged[event.date] = event
    return merged


def _generate_calendar_days(
    *,
    semester_id: int,
    teacher_id: int,
    start_date: date,
    end_date: date,
    events: list[CalendarEvent],
) -> list[dict[str, Any]]:
    event_map = _merge_events(events)
    rows: list[dict[str, Any]] = []
    semester_monday = start_date - timedelta(days=start_date.weekday())
    cursor = start_date
    while cursor <= end_date:
        week_index = ((cursor - semester_monday).days // 7) + 1
        weekday = cursor.weekday()
        event = event_map.get(cursor.isoformat())
        day_type = DAY_TYPE_TEACHING if weekday < 5 else DAY_TYPE_WEEKEND
        label = ""
        source = "generated"
        source_url = ""
        confidence = 0.5
        metadata: dict[str, Any] = {}
        if event:
            day_type = event.day_type
            label = event.label
            source = event.source
            source_url = event.source_url
            confidence = event.confidence
            metadata = event.metadata or {}
        elif day_type == DAY_TYPE_WEEKEND:
            label = "周末"
        rows.append(
            {
                "semester_id": int(semester_id),
                "teacher_id": int(teacher_id),
                "date": cursor.isoformat(),
                "week_index": week_index,
                "weekday": weekday,
                "day_type": day_type,
                "label": label,
                "source": source,
                "source_url": source_url,
                "confidence": float(confidence or 0.0),
                "metadata_json": _json_dumps(metadata),
            }
        )
        cursor += timedelta(days=1)
    return rows


def _load_semester_for_sync(conn, teacher_id: int, semester_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT *
        FROM academic_semesters
        WHERE id = ? AND teacher_id = ?
        LIMIT 1
        """,
        (int(semester_id), int(teacher_id)),
    ).fetchone()
    return dict(row) if row else None


def mark_semester_calendar_sync_queued(conn, *, teacher_id: int, semester_id: int) -> None:
    conn.execute(
        """
        UPDATE academic_semesters
        SET calendar_sync_status = ?,
            calendar_sync_message = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND teacher_id = ?
        """,
        (SYNC_STATUS_PENDING, "校历同步已排队，系统会自动拉取教务日历并核对广西节假日。", int(semester_id), int(teacher_id)),
    )


def _find_existing_semester_for_alignment(
    conn,
    *,
    teacher_id: int,
    name: str,
    start_date: str,
    end_date: str,
):
    teacher_scope = load_teacher_org_scope(conn, teacher_id)
    row = conn.execute(
        """
        SELECT *
        FROM academic_semesters
        WHERE lower(TRIM(COALESCE(school_code, ?))) = lower(TRIM(?))
          AND (
              name = ?
              OR (start_date = ? AND end_date = ?)
          )
        ORDER BY
          CASE
            WHEN name = ? THEN 0
            WHEN start_date = ? AND end_date = ? THEN 1
            ELSE 2
          END,
          updated_at DESC,
          id DESC
        LIMIT 1
        """,
        (
            teacher_scope["school_code"],
            teacher_scope["school_code"],
            name,
            start_date,
            end_date,
            name,
            start_date,
            end_date,
        ),
    ).fetchone()
    return dict(row) if row else None


async def prepare_current_semester_from_academic_system(teacher_id: int) -> dict[str, Any]:
    """Create or reuse the teacher's current semester from the academic system.

    This does the login and term-range discovery synchronously so the UI knows
    which semester was created. The heavier holiday crawler and AI verification
    still runs through the normal background calendar sync.
    """
    with get_db_connection() as conn:
        access_payload = load_teacher_academic_access_method(conn, teacher_id, school_code="gxufl")
    if not access_payload:
        return {
            "status": "missing_credential",
            "message": "请先在系统设置中配置并通过校验的教务系统账号，再从教务系统同步学期。",
        }

    candidates, academic_sources = await _fetch_academic_alignment_candidates(access_payload)
    alignment = _pick_current_alignment(candidates, china_now().date())
    if not alignment:
        source_message = ""
        failed_source = next((item for item in academic_sources if item.get("status") == "failed"), None)
        if failed_source:
            source_message = str(failed_source.get("message") or "")[:160]
        return {
            "status": "no_current_semester",
            "message": "已尝试登录教务系统，但未解析到当前学期教学日历。请确认教务系统首页已显示本学期校历后再试。",
            "source_message": source_message,
            "source_summary": academic_sources,
        }

    start_date = parse_date_input(alignment.start_date)
    end_date = parse_date_input(alignment.end_date)
    if not start_date or not end_date or end_date < start_date:
        return {
            "status": "invalid_alignment",
            "message": "教务系统返回的学期日期无效，未写入本系统。",
            "source_summary": academic_sources,
        }

    semester_name = _normalize_space(alignment.name) or infer_semester_name(start_date)
    start_iso = start_date.isoformat()
    end_iso = end_date.isoformat()
    week_count = compute_semester_week_count(start_date, end_date)
    sync_message = "已从教务系统识别本学期，节假日和补课处理已排队。"

    with get_db_connection() as conn:
        teacher_scope = load_teacher_org_scope(conn, teacher_id)
        existing = _find_existing_semester_for_alignment(
            conn,
            teacher_id=teacher_id,
            name=semester_name,
            start_date=start_iso,
            end_date=end_iso,
        )
        try:
            if existing:
                semester_id = int(existing["id"])
                if int(existing.get("teacher_id") or 0) == int(teacher_id):
                    conn.execute(
                        """
                        UPDATE academic_semesters
                        SET name = ?,
                            start_date = ?,
                            end_date = ?,
                            week_count = ?,
                            school_code = ?,
                            school_name = ?,
                            calendar_sync_status = ?,
                            calendar_sync_message = ?,
                            calendar_source_summary_json = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ? AND teacher_id = ?
                        """,
                        (
                            semester_name,
                            start_iso,
                            end_iso,
                            week_count,
                            teacher_scope["school_code"],
                            teacher_scope["school_name"],
                            SYNC_STATUS_PENDING,
                            sync_message,
                            _json_dumps(academic_sources),
                            semester_id,
                            int(teacher_id),
                        ),
                    )
                    action = "reused"
                    should_sync_calendar = True
                else:
                    action = "shared_reused"
                    should_sync_calendar = False
            else:
                semester_id = execute_insert_returning_id(
                    conn,
                    """
                    INSERT INTO academic_semesters (
                        teacher_id, school_code, school_name, name, start_date, end_date, week_count,
                        calendar_sync_status, calendar_sync_message, calendar_source_summary_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(teacher_id),
                        teacher_scope["school_code"],
                        teacher_scope["school_name"],
                        semester_name,
                        start_iso,
                        end_iso,
                        week_count,
                        SYNC_STATUS_PENDING,
                        sync_message,
                        _json_dumps(academic_sources),
                    ),
                )
                action = "created"
                should_sync_calendar = True
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return {
        "status": "success",
        "action": action,
        "semester_id": semester_id,
        "semester_name": semester_name,
        "start_date": start_iso,
        "end_date": end_iso,
        "week_count": week_count,
        "should_sync_calendar": should_sync_calendar,
        "message": (
            f"已从教务系统识别并复用同校本学期：{semester_name}。"
            if action == "shared_reused"
            else f"已从教务系统{'复用' if action == 'reused' else '创建'}本学期：{semester_name}，"
            "系统会继续核对广西节假日和补课日期。"
        ),
        "source_summary": academic_sources,
    }


def _mark_sync_running(conn, *, teacher_id: int, semester_id: int) -> None:
    conn.execute(
        """
        UPDATE academic_semesters
        SET calendar_sync_status = ?,
            calendar_sync_message = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND teacher_id = ?
        """,
        (SYNC_STATUS_RUNNING, "正在同步教务系统校历并核对广西节假日。", int(semester_id), int(teacher_id)),
    )


def _persist_sync_result(
    conn,
    *,
    teacher_id: int,
    semester_id: int,
    alignment: CalendarAlignment | None,
    days: list[dict[str, Any]],
    source_summary: list[dict[str, Any]],
    status: str,
    message: str,
) -> None:
    start_date_value = None
    end_date_value = None
    week_count_value = None
    if alignment and alignment.start_date and alignment.end_date:
        start_date_value = alignment.start_date
        end_date_value = alignment.end_date
        week_count_value = int(alignment.week_count or 0)

    conn.execute(
        "DELETE FROM academic_semester_calendar_days WHERE semester_id = ? AND teacher_id = ?",
        (int(semester_id), int(teacher_id)),
    )
    conn.executemany(
        """
        INSERT INTO academic_semester_calendar_days (
            semester_id, teacher_id, date, week_index, weekday, day_type, label,
            source, source_url, confidence, metadata_json, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        [
            (
                row["semester_id"],
                row["teacher_id"],
                row["date"],
                row["week_index"],
                row["weekday"],
                row["day_type"],
                row["label"],
                row["source"],
                row["source_url"],
                row["confidence"],
                row["metadata_json"],
            )
            for row in days
        ],
    )

    if start_date_value and end_date_value and week_count_value:
        conn.execute(
            """
            UPDATE academic_semesters
            SET start_date = ?,
                end_date = ?,
                week_count = ?,
                calendar_sync_status = ?,
                calendar_sync_at = ?,
                calendar_sync_message = ?,
                calendar_source_summary_json = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND teacher_id = ?
            """,
            (
                start_date_value,
                end_date_value,
                week_count_value,
                status,
                _now_iso(),
                message,
                _json_dumps(source_summary),
                int(semester_id),
                int(teacher_id),
            ),
        )
    else:
        conn.execute(
            """
            UPDATE academic_semesters
            SET calendar_sync_status = ?,
                calendar_sync_at = ?,
                calendar_sync_message = ?,
                calendar_source_summary_json = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND teacher_id = ?
            """,
            (
                status,
                _now_iso(),
                message,
                _json_dumps(source_summary),
                int(semester_id),
                int(teacher_id),
            ),
        )


async def sync_semester_calendar_for_teacher(teacher_id: int, semester_id: int) -> dict[str, Any]:
    started = time.monotonic()
    with get_db_connection() as conn:
        semester = _load_semester_for_sync(conn, teacher_id, semester_id)
        if not semester:
            return {"status": SYNC_STATUS_FAILED, "message": "学期不存在或无权同步。"}
        _mark_sync_running(conn, teacher_id=teacher_id, semester_id=semester_id)
        access_payload = load_teacher_academic_access_method(conn, teacher_id, school_code="gxufl")
        conn.commit()

    requested_start = parse_date_input(semester.get("start_date"))
    requested_end = parse_date_input(semester.get("end_date"))
    if not requested_start or not requested_end or requested_end < requested_start:
        with get_db_connection() as conn:
            _persist_sync_result(
                conn,
                teacher_id=teacher_id,
                semester_id=semester_id,
                alignment=None,
                days=[],
                source_summary=[{"source": "system", "status": "failed", "message": "学期日期无效"}],
                status=SYNC_STATUS_FAILED,
                message="学期日期无效，无法生成校历。",
            )
            conn.commit()
        return {"status": SYNC_STATUS_FAILED, "message": "学期日期无效，无法生成校历。"}

    alignment, academic_sources = await _fetch_academic_alignment(
        access_payload,
        requested_start=requested_start,
        requested_end=requested_end,
    )
    effective_start = parse_date_input(alignment.start_date) if alignment else None
    effective_end = parse_date_input(alignment.end_date) if alignment else None
    if not effective_start or not effective_end:
        effective_start = requested_start
        effective_end = requested_end

    built_in_events = _built_in_events(effective_start, effective_end)
    crawler_items, crawler_sources = await _crawl_holiday_sources(effective_start, effective_end)
    ai_events, ai_sources = await _call_holiday_ai(
        start_date=effective_start,
        end_date=effective_end,
        crawler_items=crawler_items,
        built_in_events=built_in_events,
    )
    all_events = [*built_in_events, *ai_events]
    source_summary = [
        *academic_sources,
        *crawler_sources,
        *ai_sources,
        {
            "source": "built_in",
            "status": "success",
            "found": len(built_in_events),
        },
    ]
    days = _generate_calendar_days(
        semester_id=int(semester_id),
        teacher_id=int(teacher_id),
        start_date=effective_start,
        end_date=effective_end,
        events=all_events,
    )
    has_academic_alignment = bool(alignment and alignment.start_date and alignment.end_date)
    has_ai_success = any(item.get("source") == "ai" and item.get("status") == "success" for item in ai_sources)
    has_crawler_success = any(item.get("source") == "crawler" and item.get("status") == "success" for item in crawler_sources)
    status = SYNC_STATUS_SYNCED if has_academic_alignment and has_ai_success else SYNC_STATUS_PARTIAL
    if not has_academic_alignment and not has_ai_success and not has_crawler_success:
        status = SYNC_STATUS_GENERATED
    event_count = sum(1 for row in days if row.get("day_type") in {DAY_TYPE_HOLIDAY, DAY_TYPE_WORKDAY})
    message_parts = []
    if has_academic_alignment:
        message_parts.append("已对齐教务系统教学日历")
    else:
        message_parts.append("未能从教务系统解析到校历范围，已按当前学期日期生成")
    message_parts.append(f"已写入 {len(days)} 个校历日")
    if event_count:
        message_parts.append(f"标注 {event_count} 个节假日/调休补课日")
    elapsed_ms = int((time.monotonic() - started) * 1000)
    source_summary.append({"source": "system", "status": "success", "elapsed_ms": elapsed_ms})
    message = "，".join(message_parts) + "。"

    persisted_alignment = alignment or CalendarAlignment(
        start_date=effective_start.isoformat(),
        end_date=effective_end.isoformat(),
        week_count=compute_semester_week_count(effective_start, effective_end),
        source="generated",
        confidence=0.5,
    )
    with get_db_connection() as conn:
        _persist_sync_result(
            conn,
            teacher_id=teacher_id,
            semester_id=semester_id,
            alignment=persisted_alignment,
            days=days,
            source_summary=source_summary,
            status=status,
            message=message,
        )
        conn.commit()
    return {"status": status, "message": message, "days": len(days), "event_count": event_count}


async def sync_semester_calendar_background(teacher_id: int, semester_id: int) -> None:
    try:
        await sync_semester_calendar_for_teacher(int(teacher_id), int(semester_id))
    except Exception as exc:
        with get_db_connection() as conn:
            conn.execute(
                """
                UPDATE academic_semesters
                SET calendar_sync_status = ?,
                    calendar_sync_at = ?,
                    calendar_sync_message = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND teacher_id = ?
                """,
                (
                    SYNC_STATUS_FAILED,
                    _now_iso(),
                    f"校历同步失败：{str(exc)[:260]}",
                    int(semester_id),
                    int(teacher_id),
                ),
            )
            conn.commit()
