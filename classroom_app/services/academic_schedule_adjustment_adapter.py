"""Read-only GXUFL adjustment-page adapter.

The caller supplies an already authenticated client. This module never logs in,
persists credentials, updates the school system, or applies a schedule change.
An incomplete response raises ValueError so callers retain their last snapshot.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from html.parser import HTMLParser
from types import SimpleNamespace
from typing import Any

import httpx

from .academic_course_sync_service import (
    _parse_week_numbers,
    build_schedule_items_from_teaching_class_rosters,
)
from .semester_identity_service import identity_from_xnm_xqm, parse_semester_identity, zf_term_params_from_semester


ENTRY_PATH = "/tkgl/ttksq_cxTtksqIndex.html?information=1&doType=details&gnmkdm=N2122&layout=default"
CLASS_LIST_PATH = "/tkgl/ttksq_cxTtksqList.html?doType=query&jxb_id=&gnmkdm=N2122"
REQUEST_LIST_PATH = "/tkgl/ttksq_cxTtksqjgList.html?doType=jscx&pkey=&gnmkdm=N2122"
DETAIL_PATH = "/tkgl/tksqsh_cxShxxView.html"
PAGE_SIZE = 50
MAX_PAGES = 40
MAX_LIST_ROWS = 1000
MAX_DETAIL_ROWS = 256
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
HTTP_TIMEOUT_SECONDS = 20.0
MAX_PERIOD = 30

CLASS_FIELDS = (
    "jxb_id", "jxbmc", "jxbzc", "kch", "kch_id", "kcmc", "sksj", "jxdd",
    "xnm", "xqm", "xnmmc", "xqmmc",
)
REQUEST_FIELDS = CLASS_FIELDS + (
    "ttk_id", "ttk_lsh", "shzt", "tklxdm", "tklxmc", "sqtjsj", "tkyy",
)
STATUS_MAP = {"0": "draft", "1": "pending", "2": "pending", "3": "approved", "4": "returned", "5": "rejected"}
KIND_MAP = {"01": "move", "03": "cancel"}
_DAY_MAP = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}
_TARGET_FIELDS = ("tkhrq", "xzcd", "xxqj", "xjc", "xcd_id", "xcdmc", "xjgh")


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, bool)):
        raise ValueError("教务字段类型无效，预期文本或数字。")
    return str(value).strip()


def _integer(value: Any, label: str) -> int:
    text = _text(value)
    if not re.fullmatch(r"\d+", text):
        raise ValueError(f"{label}缺失或不是非负整数。")
    return int(text)


def _term_params(semester: dict[str, Any]) -> dict[str, str]:
    # Legacy identity helpers may infer a term from dates. A remote snapshot
    # requires an explicit target identity rather than that compatibility guess.
    if parse_semester_identity(semester.get("name") or semester.get("semester_name")) is None:
        raise ValueError("所选学期缺少明确学年学期名称，未按日期猜测教务参数。")
    params = zf_term_params_from_semester(semester)
    if not params:
        raise ValueError("无法确定所选学年学期，未查询教务系统。")
    return params


def _same_term(row: dict[str, Any], params: dict[str, str], label: str) -> None:
    if _text(row.get("xnm")) != params["xnm"] or _text(row.get("xqm")) != params["xqm"]:
        raise ValueError(f"{label}的学年学期缺失或与所选学期不一致。")


def _iso_date(value: Any, label: str) -> date:
    text = _text(value)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise ValueError(f"{label}不是明确的 ISO 日期。")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{label}日期无效。") from exc


def _semester_dates(semester: dict[str, Any]) -> tuple[date, date, date]:
    start = _iso_date(semester.get("start_date"), "学期开始日期")
    end = _iso_date(semester.get("end_date"), "学期结束日期")
    if end < start or (end - start).days > 366:
        raise ValueError("学期日期范围无效。")
    return start, end, start - timedelta(days=start.weekday())


def _weekday(value: Any, label: str) -> int:
    text = re.sub(r"\s+", "", _text(value))
    text = re.sub(r"^(?:星期|周)", "", text)
    day = _DAY_MAP.get(text)
    if day is None and text in {str(i) for i in range(1, 8)}:
        day = int(text)
    if day is None:
        raise ValueError(f"{label}星期无法精确识别。")
    return day


def _sections(value: Any, label: str) -> list[int]:
    text = re.sub(r"\s+", "", _text(value))
    text = re.sub(r"^第", "", text)
    text = re.sub(r"节$", "", text)
    if not re.fullmatch(r"\d{1,2}(?:[-~～－—]\d{1,2})?(?:[,，、]\d{1,2}(?:[-~～－—]\d{1,2})?)*", text):
        raise ValueError(f"{label}节次缺失或格式不明确。")
    sections: list[int] = []
    for part in re.split(r"[,，、]", text):
        ends = re.split(r"[-~～－—]", part)
        first, last = int(ends[0]), int(ends[-1])
        if not 1 <= first <= last <= MAX_PERIOD:
            raise ValueError(f"{label}节次超出范围或顺序倒置。")
        expanded = list(range(first, last + 1))
        if set(expanded).intersection(sections):
            raise ValueError(f"{label}节次重复。")
        sections.extend(expanded)
    return sorted(sections)


def _weeks(value: Any, max_week: int, label: str) -> list[int]:
    """Validate first, then reuse the established odd/even week parser."""
    text = re.sub(r"\s+", "", _text(value))
    if not text:
        raise ValueError(f"{label}周次缺失。")
    for segment in re.split(r"[,，、;；]", text):
        match = re.fullmatch(
            r"(?:第)?(\d{1,2})(?:[-~～－—](\d{1,2}))?(?:周)?(?:[（(]([单双])[）)]|([单双])(?:周)?)?",
            segment,
        )
        if not match:
            raise ValueError(f"{label}周次格式无法精确解析。")
        first, last = int(match.group(1)), int(match.group(2) or match.group(1))
        if not 1 <= first <= last <= max_week:
            raise ValueError(f"{label}周次超出学期范围或顺序倒置。")
    # The existing parser understands '~' rather than the full-width version.
    weeks = _parse_week_numbers(text.replace("～", "~"), max_week_count=max_week)
    if not weeks:
        raise ValueError(f"{label}周次没有有效课位。")
    return weeks


def _point(row: dict[str, Any], semester: dict[str, Any], *, proposed: bool, label: str) -> dict[str, Any]:
    fields = ("tkhrq", "xzcd", "xxqj", "xjc", "xcdmc", "xcd_id", "xjgh") if proposed else (
        "tkqrq", "yzcd", "yxqj", "yjc", "ycdmc", "ycd_id", "yjgh"
    )
    date_key, week_key, day_key, period_key, room_key, room_id_key, teacher_key = fields
    day = _iso_date(row.get(date_key), label)
    start, end, monday = _semester_dates(semester)
    if not start <= day <= end:
        raise ValueError(f"{label}日期超出所选学期。")
    week_match = re.fullmatch(r"(?:第)?(\d{1,2})(?:周)?", _text(row.get(week_key)))
    if not week_match:
        raise ValueError(f"{label}必须包含一个明确周次。")
    week = int(week_match.group(1))
    weekday = _weekday(row.get(day_key), label)
    if week != (day - monday).days // 7 + 1 or weekday != day.isoweekday():
        raise ValueError(f"{label}的日期、周次、星期不一致。")
    room = _text(row.get(room_key))
    room_id = _text(row.get(room_id_key))
    if not room and not room_id:
        raise ValueError(f"{label}教室信息缺失。")
    return {
        "date": day.isoformat(), "week": week, "weekday": weekday,
        "sections": _sections(row.get(period_key), label),
        "room": room, "room_id": room_id, "teacher_code": _text(row.get(teacher_key)),
    }


def _allow_row(row: dict[str, Any], fields: tuple[str, ...]) -> dict[str, str]:
    result = {key: _text(row.get(key)) for key in fields}
    if any(len(value) > 16000 for value in result.values()):
        raise ValueError("教务响应字段超过安全解析长度。")
    return result


def _headers(*, html: bool = False) -> dict[str, str]:
    return {
        "Accept": "text/html,*/*;q=0.8" if html else "application/json,text/javascript,*/*;q=0.8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://jwxt.gxufl.com" + ENTRY_PATH,
    }


async def _read(client: httpx.AsyncClient, method: str, path: str, *, label: str, **kwargs: Any) -> httpx.Response:
    try:
        response = await client.request(method, path, timeout=HTTP_TIMEOUT_SECONDS, **kwargs)
    except httpx.HTTPError as exc:
        raise ValueError(f"{label}读取失败（{type(exc).__name__}），保留上次有效课表。") from exc
    if 300 <= response.status_code < 400 or "login_" in response.url.path.lower():
        raise ValueError(f"{label}登录会话已失效，请重新验证教务账号。")
    if not 200 <= response.status_code < 300:
        raise ValueError(f"{label}读取失败（HTTP {response.status_code}）。")
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise ValueError(f"{label}响应过大，停止解析。")
    return response


async def _fetch_pages(client: httpx.AsyncClient, *, path: str, params: dict[str, str],
                       extra: dict[str, str], fields: tuple[str, ...], identity_key: str,
                       label: str, sources: list[dict[str, Any]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    expected: tuple[int, int] | None = None
    for page in range(1, MAX_PAGES + 1):
        form = {
            **params, **extra, "_search": "false", "queryModel.showCount": str(PAGE_SIZE),
            "queryModel.currentPage": str(page),
        }
        response = await _read(client, "POST", path, label=label, data=form, headers=_headers())
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"{label}返回非 JSON（可能是登录页），未发布快照。") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise ValueError(f"{label}缺少完整的分页数据。")
        rows = payload["items"]
        total = _integer(payload.get("totalResult"), f"{label}总记录数")
        pages = _integer(payload.get("totalPage"), f"{label}总页数")
        current = _integer(payload.get("currentPage"), f"{label}当前页")
        if current != page:
            raise ValueError(f"{label}响应页码不符，可能重复返回旧页。")
        if total > MAX_LIST_ROWS or pages > MAX_PAGES or len(rows) > PAGE_SIZE:
            raise ValueError(f"{label}超过有界查询上限，未发布不完整快照。")
        if expected is None:
            expected = total, pages
        elif expected != (total, pages):
            raise ValueError(f"{label}分页总数发生变化，请重新同步。")
        if total == 0:
            if rows or pages not in (0, 1) or page != 1:
                raise ValueError(f"{label}空列表与分页总数不一致。")
        elif pages < 1 or pages > total or not rows:
            raise ValueError(f"{label}缺页或分页信息无效。")
        for raw in rows:
            if not isinstance(raw, dict):
                raise ValueError(f"{label}包含无效数据行。")
            row = _allow_row(raw, fields)
            _same_term(row, params, label)
            identity = row[identity_key]
            if not identity:
                raise ValueError(f"{label}缺少稳定标识。")
            if identity in seen_ids:
                raise ValueError(f"{label}出现重复记录或重复分页。")
            seen_ids.add(identity)
            result.append(row)
        sources.append({
            "source": "gxufl_jwxt", "path": path, "method": "POST", "page": page,
            "total_pages": pages, "total_count": total, "item_count": len(rows),
            "xnm": params["xnm"], "xqm": params["xqm"], "status": "success",
        })
        if len(result) > total:
            raise ValueError(f"{label}记录数超过声明总数。")
        if page >= pages:
            if len(result) != total:
                raise ValueError(f"{label}记录数不足，分页不完整。")
            return result
    raise ValueError(f"{label}分页超过上限。")


class _DetailHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.inputs: dict[str, str] = {}
        self.scripts: list[str] = []
        self._script: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag.lower() == "input":
            for key in {values.get("name"), values.get("id")} - {None, ""}:
                value = values.get("value") or ""
                if key in self.inputs and self.inputs[key] != value:
                    raise ValueError("详情页面包含互相冲突的标识字段。")
                self.inputs[key] = value
        elif tag.lower() == "script":
            self._script = []

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._script.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._script is not None:
            self.scripts.append("".join(self._script))
            self._script = None


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("详情 JSON 含重复字段，不能确定真实安排。")
        result[key] = value
    return result


def _detail_rows(page_html: str, request: dict[str, str], params: dict[str, str]) -> list[dict[str, Any]]:
    document = _DetailHTML()
    document.feed(page_html)
    if document.inputs.get("ttk_id") != request["ttk_id"]:
        raise ValueError("调停课详情申请标识不匹配或登录会话失效。")
    if document.inputs.get("tklxdm_sub") != request["tklxdm"]:
        raise ValueError("调停课详情类型与申请列表不一致。")
    assignments: list[str] = []
    for script in document.scripts:
        for match in re.finditer(r"\bvar\s+modelList\s*=\s*", script):
            assignments.append(script[match.end():])
    if len(assignments) != 1:
        raise ValueError("调停课详情缺少唯一的 modelList 数据。")
    try:
        value, offset = json.JSONDecoder(object_pairs_hook=_unique_object).raw_decode(assignments[0])
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("调停课详情 modelList 不是有效 JSON，未执行页面脚本。") from exc
    trailing = assignments[0][offset:].lstrip()
    if trailing and not trailing.startswith(";"):
        raise ValueError("调停课详情包含非 JSON 表达式，未执行页面脚本。")
    if not isinstance(value, list) or not value or len(value) > MAX_DETAIL_ROWS:
        raise ValueError("调停课详情列表为空或超过解析上限。")
    seen: set[str] = set()
    for row in value:
        if not isinstance(row, dict):
            raise ValueError("调停课详情含无效明细行。")
        _same_term(row, params, "调停课详情")
        if _text(row.get("jxb_id")) != request["jxb_id"]:
            raise ValueError("调停课详情教学班与申请列表不一致。")
        if _text(row.get("ttk_lsh")) != request["ttk_lsh"]:
            raise ValueError("调停课详情流水号与申请列表不一致。")
        if _text(row.get("tklxdm")) != request["tklxdm"]:
            raise ValueError("调停课明细类型与申请列表不一致。")
        identity = _text(row.get("ttkxx_id"))
        if not identity or identity in seen:
            raise ValueError("调停课详情标识缺失或重复。")
        seen.add(identity)
    return value


async def fetch_adjustment_snapshot(client: httpx.AsyncClient, semester: dict[str, Any]) -> dict[str, Any]:
    """Fetch one complete, explicitly scoped term snapshot, serially and read-only."""
    params = _term_params(semester)
    _semester_dates(semester)
    sources: list[dict[str, Any]] = []
    classes = await _fetch_pages(
        client, path=CLASS_LIST_PATH, params=params,
        extra={"pkxnm": params["xnm"], "pkxqm": params["xqm"], "kg": "1", "kcmc": "", "jgh": "",
               "kkxb_id": "", "kkbm_id": "", "queryModel.sortName": "xnmmc,xqmmc,jxbmc ", "queryModel.sortOrder": "asc"},
        fields=CLASS_FIELDS, identity_key="jxb_id", label="教务教学班课表", sources=sources,
    )
    request_rows = await _fetch_pages(
        client, path=REQUEST_LIST_PATH, params=params,
        extra={"jg_id": "", "queryModel.sortName": "sqtjsj ", "queryModel.sortOrder": "desc"},
        fields=REQUEST_FIELDS, identity_key="ttk_id", label="调停课申请列表", sources=sources,
    )
    requests = []
    for row in request_rows:
        if not row["jxb_id"] or not row["ttk_lsh"]:
            raise ValueError("调停课申请缺少教学班或流水号。")
        status = STATUS_MAP.get(row["shzt"], "unknown")
        kind = KIND_MAP.get(row["tklxdm"], "unknown")
        detail_params = {**params, "ymly": "sqym", "ttk_id": row["ttk_id"], "jxb_id": row["jxb_id"], "gnmkdm": "N2122"}
        response = await _read(client, "POST", DETAIL_PATH, label="调停课申请详情", params=detail_params, headers=_headers(html=True))
        rows = _detail_rows(response.text, row, params)
        details = []
        for raw in rows:
            original = _point(raw, semester, proposed=False, label="原安排")
            has_target = any(_text(raw.get(key)) for key in _TARGET_FIELDS)
            if kind == "cancel" and has_target:
                raise ValueError("停课申请却包含目标安排，请核对教务数据。")
            if kind == "move" and not has_target:
                raise ValueError("调课申请缺少完整目标安排。")
            proposed = _point(raw, semester, proposed=True, label="拟安排") if has_target else None
            details.append({"detail_id": _text(raw["ttkxx_id"]), "original": original, "proposed": proposed})
        requests.append({
            "request_id": row["ttk_id"], "serial": row["ttk_lsh"], "status": status,
            "raw_status": row["shzt"], "kind": kind, "teaching_class_id": row["jxb_id"],
            "course_code": row["kch"], "course_name": row["kcmc"], "teaching_class_name": row["jxbmc"],
            "class_label": row["jxbzc"], "reason": row["tkyy"], "applied_at": row["sqtjsj"], "details": details,
        })
        summary: dict[str, Any] = {
            "source": "gxufl_jwxt", "path": DETAIL_PATH, "method": "POST", "request_id": row["ttk_id"],
            "xnm": params["xnm"], "xqm": params["xqm"], "detail_count": len(details), "status": "success",
        }
        warnings = []
        if status == "unknown":
            warnings.append("审批状态代码未识别，不参与待审预测。")
        if kind == "unknown":
            warnings.append("申请类型未识别，保留结构化原文事实，不推断调课或停课。")
        if warnings:
            summary.update(status="warning", warnings=warnings, raw_status=row["shzt"], raw_kind=row["tklxdm"])
        sources.append(summary)
    return {"teaching_classes": classes, "requests": requests, "source_summary": sources}


def build_official_occurrences(teaching_classes: list[dict[str, Any]], semester: dict[str, Any]) -> list[dict[str, Any]]:
    """Expand the official SKSJ/JXDD facts without applying any application."""
    params = _term_params(semester)
    start, end, monday = _semester_dates(semester)
    max_week = (end - monday).days // 7 + 1
    occurrences: list[dict[str, Any]] = []
    seen_rows: set[str] = set()
    seen_slots: set[tuple[Any, ...]] = set()
    for raw in teaching_classes:
        row = _allow_row(raw, CLASS_FIELDS)
        _same_term(row, params, "正式课表")
        if not row["jxb_id"] or row["jxb_id"] in seen_rows:
            raise ValueError("正式课表教学班标识缺失或重复。")
        seen_rows.add(row["jxb_id"])
        if not row["kcmc"] or not row["jxbmc"]:
            raise ValueError("正式课表课程或教学班名称缺失。")
        schedules = [part.strip() for part in re.split(r"[;；]", row["sksj"]) if part.strip()]
        rooms = [part.strip() for part in re.split(r"[;；]", row["jxdd"]) if part.strip()]
        if not schedules or not rooms or len(rooms) not in (1, len(schedules)):
            raise ValueError("正式课表的上课时间与教室不能精确配对。")
        expected_periods: set[tuple[int, int, int, str]] = set()
        for schedule_index, schedule in enumerate(schedules):
            match = re.fullmatch(r"\s*(?:星期|周)([一二三四五六日天1-7])\s*第\s*([^节{}｛｝]+)\s*节\s*[\{｛]([^{}｛｝]+)[\}｝]\s*", schedule)
            if not match:
                raise ValueError("正式课表时间格式无法完整解析，未忽略未知片段。")
            day = _weekday(match.group(1), "正式课表")
            sections = _sections(match.group(2), "正式课表")
            weeks = _weeks(match.group(3), max_week, "正式课表")
            room = rooms[schedule_index] if len(rooms) > 1 else rooms[0]
            for week in weeks:
                for section in sections:
                    coverage = (week, day, section, room)
                    if coverage in expected_periods:
                        raise ValueError("正式课表存在重复课位，不能发布。")
                    expected_periods.add(coverage)
        roster = SimpleNamespace(
            teaching_class_id=row["jxb_id"], teaching_class_name=row["jxbmc"],
            academic_year=row["xnm"], academic_year_name=row["xnmmc"],
            academic_term=row["xqm"], academic_term_name=row["xqmmc"],
            course_code=row["kch"], course_internal_id=row["kch_id"], course_name=row["kcmc"],
            class_composition=row["jxbzc"], schedule_text=row["sksj"], location_text=row["jxdd"], raw_json={},
        )
        actual_periods: set[tuple[int, int, int, str]] = set()
        for item in build_schedule_items_from_teaching_class_rosters([roster], source_url=CLASS_LIST_PATH):
            if item.weekday is None or not 0 <= item.weekday <= 6:
                raise ValueError("正式课表星期解析失败。")
            sections = _sections(item.section_text, "正式课表")
            for week in _weeks(item.weeks_text, max_week, "正式课表"):
                actual_date = monday + timedelta(days=(week - 1) * 7 + item.weekday)
                if not start <= actual_date <= end:
                    raise ValueError("正式课表日期超出所选学期。")
                key = (row["jxb_id"], actual_date.isoformat(), tuple(sections), item.location)
                if key in seen_slots:
                    raise ValueError("正式课表存在重复课位，不能发布。")
                seen_slots.add(key)
                actual_periods.update((week, item.weekday + 1, section, item.location) for section in sections)
                occurrences.append({
                    "teaching_class_id": row["jxb_id"], "course_code": row["kch"], "course_name": row["kcmc"],
                    "teaching_class_name": row["jxbmc"], "class_label": row["jxbzc"],
                    "date": actual_date.isoformat(), "week": week, "weekday": item.weekday + 1,
                    "sections": sections, "room": item.location,
                })
        if actual_periods != expected_periods:
            raise ValueError("正式课表解析结果与原始周次节次不一致，未发布遗漏课位。")
    return sorted(occurrences, key=lambda row: (row["date"], row["sections"], row["teaching_class_id"]))


class _CurrentTermHTML(HTMLParser):
    """Only read verified hidden fields inside the entry page's searchForm div."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fields: dict[str, str] = {}
        self.container_count = 0
        self._div_depth = 0
        self._scope_depth: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag.lower() == "div":
            self._div_depth += 1
            if values.get("id") == "searchForm":
                self.container_count += 1
                self._scope_depth = self._div_depth
        elif tag.lower() == "input" and self._scope_depth is not None:
            name = values.get("name")
            if name not in {"xnm", "xqm", "pkxnm", "pkxqm"}:
                return
            if values.get("type", "").lower() != "hidden" or values.get("id") != name or name in self.fields:
                raise ValueError("教务当前学期字段形态改变或重复，请重新核对页面协议。")
            self.fields[name] = values.get("value") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "div":
            if self._div_depth == self._scope_depth:
                self._scope_depth = None
            self._div_depth -= 1


async def discover_current_term(client: httpx.AsyncClient) -> dict[str, Any]:
    """Discover remote defaults from verified page fields, never today's date."""
    response = await _read(client, "GET", ENTRY_PATH, label="教务当前学期", headers=_headers(html=True))
    document = _CurrentTermHTML()
    document.feed(response.text)
    if document.container_count != 1 or document._scope_depth is not None:
        raise ValueError("教务页面缺少唯一 searchForm 学期容器，可能登录失效。")
    fields = document.fields
    xnm, xqm = fields.get("xnm", ""), fields.get("xqm", "")
    if not re.fullmatch(r"20\d{2}", xnm) or xqm not in {"3", "12", "16"}:
        raise ValueError("教务页面未提供可核验的当前学年学期代码。")
    if fields.get("pkxnm") != xnm or fields.get("pkxqm") != xqm:
        raise ValueError("教务查询学期与排课学期不一致。")
    identity = identity_from_xnm_xqm(xnm, xqm)
    if identity is None:
        raise ValueError("教务当前学期代码无法规范化。")
    return {
        "xnm": xnm, "xqm": xqm, "academic_year": f"{identity.start_year}-{identity.end_year}",
        "academic_term": identity.term, "name": identity.canonical_name,
    }
