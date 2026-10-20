"""GXUFL N214510 read adapter, verified against the page's own AJAX flow.

This module never imports teachers/courses or stores login credentials. A query
is complete only after every page and every matched teacher timetable validates.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from html.parser import HTMLParser
import re
from typing import Any

import httpx

from .academic_classroom_sync_service import _ajax_headers, _is_login_redirect, _jqgrid_form
from .academic_course_sync_service import _parse_schedule_items_from_json, _parse_week_numbers
from .academic_integration_service import _looks_like_login_page

INDEX = "/kbdy/jskbdy_cxJskbdyIndex.html?gnmkdm=N214510&layout=default"
LIST = "/kbdy/jskbdy_cxJskbdyList.html?gnmkdm=N214510"
CHECK = "/kbdy/bjkbdy_cxXnxqsfkz.html?gnmkdm=N214510"
TIMETABLE = "/kbdy/jskbdy_cxJsKb.html?gnmkdm=N214510"
MAX_TEACHERS = 20
PAGE_SIZE = 20
REQUEST_INTERVAL = 0.25
OPTION_FIELDS = {"xqh_id": "campuses", "jg_id": "departments", "zcm": "titles",
                 "kkbm_id": "colleges", "sfwp": "external"}


class TeacherTimetableSourceError(ValueError):
    """A failed response cannot establish a complete cache, including empties."""


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


class _OptionsParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.options: dict[str, list[dict]] = {}
        self.select = ""
        self.option: dict | None = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "select":
            self.select = attrs.get("id", "")
        elif tag == "option" and self.select in (*OPTION_FIELDS, "xnm", "xqm"):
            self.option = {"id": attrs.get("value", ""), "name": ""}

    def handle_data(self, data):
        if self.option is not None:
            self.option["name"] += data

    def handle_endtag(self, tag):
        if tag == "option" and self.option is not None:
            if self.option["id"]:
                self.option["name"] = _text(self.option["name"])
                self.options.setdefault(self.select, []).append(self.option)
            self.option = None
        elif tag == "select":
            self.select = ""


def parse_query_options(page_html: str) -> dict:
    parser = _OptionsParser()
    parser.feed(page_html)
    if not parser.options.get("xnm") or not parser.options.get("xqm"):
        raise TeacherTimetableSourceError("教务教师查询页面不可用，请确认账号有教师课表查询权限。")
    return parser.options


def _check_response(response: httpx.Response):
    if _is_login_redirect(response) or response.is_redirect or _looks_like_login_page(response.text):
        raise TeacherTimetableSourceError("教务系统登录已失效，请检查教务账号后重试。")
    response.raise_for_status()
    if len(response.content) > 8 * 1024 * 1024:
        raise TeacherTimetableSourceError("教务系统响应过大，请缩小教师查询范围。")


async def _post(client, path, data):
    await asyncio.sleep(REQUEST_INTERVAL)
    response = await client.post(path, data=data, headers=_ajax_headers(client, referer_path=INDEX))
    _check_response(response)
    try:
        payload = response.json()
    except ValueError:
        raise TeacherTimetableSourceError("教务系统返回了无效查询结果，原有缓存已保留。") from None
    if isinstance(payload, dict) and (payload.get("success") is False or payload.get("error")):
        raise TeacherTimetableSourceError("教务系统未完成查询，原有缓存已保留。")
    return payload


def _count(value):
    if isinstance(value, bool) or not re.fullmatch(r"\d+", str(value)):
        raise TeacherTimetableSourceError("教务系统分页信息无效，未更新缓存。")
    return int(value)


def _validate_list(payload, *, page, total_expected=None):
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise TeacherTimetableSourceError("教务系统未返回有效教师列表。")
    total = _count(payload.get("totalCount", payload.get("totalResult")))
    pages = _count(payload.get("totalPage"))
    if total > MAX_TEACHERS:
        raise TeacherTimetableSourceError(f"匹配教师超过{MAX_TEACHERS}人，请填写更完整的姓名、工号或增加筛选条件。")
    if total_expected is not None and total != total_expected:
        raise TeacherTimetableSourceError("教师列表在查询过程中发生变化，请重试。")
    if _count(payload.get("currentPage")) != page:
        raise TeacherTimetableSourceError("教务系统返回了重复分页，未更新缓存。")
    rows = payload["items"]
    if pages > MAX_TEACHERS or (total and (not rows or not pages)) or len(rows) > total:
        raise TeacherTimetableSourceError("教务系统教师列表不完整，未更新缓存。")
    if not total and (rows or pages > 1):
        raise TeacherTimetableSourceError("教务系统空列表分页不一致。")
    return rows, total, pages


def _sections(value: str) -> list[list[int]]:
    text = re.sub(r"[第节\s]", "", value).replace("，", ",").replace("、", ",")
    values = set()
    for part in text.split(","):
        match = re.fullmatch(r"(\d{1,2})(?:[-~－—](\d{1,2}))?", part)
        if not match:
            raise TeacherTimetableSourceError("课表节次无法识别，未更新缓存。")
        start, end = int(match[1]), int(match[2] or match[1])
        if not 1 <= start <= end <= 30:
            raise TeacherTimetableSourceError("课表节次超出有效范围。")
        values.update(range(start, end + 1))
    groups: list[list[int]] = []
    for value in sorted(values):
        if not groups or groups[-1][-1] + 1 != value:
            groups.append([])
        groups[-1].append(value)
    return groups


def normalize_timetable(payload, *, teacher_no, xnm, xqm) -> tuple[list[dict], list[dict]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("kbList"), list):
        raise TeacherTimetableSourceError("教务系统未返回完整课表，未更新缓存。")
    for key in ("sjkList", "bjsjkList", "jxhjkcList"):
        if not isinstance(payload.get(key), list):
            raise TeacherTimetableSourceError("教务系统其他课程数据无效，未更新缓存。")
    for key, expected in (("jgh", teacher_no), ("xnm", xnm), ("xqm", xqm)):
        if key in payload and _text(payload[key]) != expected:
            raise TeacherTimetableSourceError("课表教师或学期与查询不一致，未更新缓存。")
    lessons = []
    for raw in payload["kbList"]:
        if not isinstance(raw, dict):
            raise TeacherTimetableSourceError("教务系统课表课程格式无效。")
        if _text(raw.get("jgh")) != teacher_no or _text(raw.get("xnm")) != xnm or _text(raw.get("xqm")) != xqm:
            raise TeacherTimetableSourceError("课表教师或学期与查询不一致，未更新缓存。")
        parsed = _parse_schedule_items_from_json({"kbList": [raw]}, TIMETABLE)
        if len(parsed) != 1:
            raise TeacherTimetableSourceError("课表存在无法解析的课程，未更新缓存。")
        item = parsed[0]
        numbers = [int(n) for n in re.findall(r"\d+", item.weeks_text)]
        if (not numbers or any(n < 1 or n > 60 for n in numbers)
                or re.search(r"[^\d\s,，、;；周第单双()（）\-~－—]", item.weeks_text)
                or any(int(a) > int(b) for a, b in re.findall(r"(\d+)\s*[-~－—]\s*(\d+)", item.weeks_text))):
            raise TeacherTimetableSourceError("课程周次规则无法完整识别，未更新缓存。")
        weeks = _parse_week_numbers(item.weeks_text, max_week_count=60)
        # Do not silently turn malformed/unsupported rules into missing lessons.
        if item.weekday is None or not weeks or any(n < 1 for n in weeks):
            raise TeacherTimetableSourceError("课程周次或星期无法识别，未更新缓存。")
        base = asdict(item)
        # Keep the complete source in the private snapshot; public details contain
        # academic fields only, never unrelated source userModel/personnel fields.
        base.pop("raw_json", None)
        for sections in _sections(_text(raw.get("jcs") or raw.get("jc"))):
            lessons.append({**base, "weekday": item.weekday + 1, "weeks": weeks, "sections": sections,
                            "class_label": item.class_composition, "classroom": item.location,
                            "teacher_no": teacher_no, "teacher_name": _text(raw.get("jsxm") or raw.get("xm")),
                            "course_category": _text(raw.get("kclbmc")), "teacher_role": _text(raw.get("zfjmc"))})
    others, seen = [], set()
    for key in ("sjkList", "bjsjkList", "jxhjkcList"):
        for raw in payload.get(key, []):
            if not isinstance(raw, dict):
                raise TeacherTimetableSourceError("教务系统其他课程格式无效。")
            description = _text(raw.get("qtkcgs") or raw.get("sjkcgs") or raw.get("kcmc") or raw.get("jxhjmc"))
            if not description:
                # Preserve unfamiliar academic fields without pretending a blank
                # scheduled course is a complete parsed result.
                description = "；".join(f"{k}：{v}" for k, v in raw.items()
                                       if k in ("jxbmc", "jxbzc", "kch", "zcd", "qsjsz", "bz", "jxnr") and _text(v))
            if not description:
                raise TeacherTimetableSourceError("其他课程存在无法识别的数据，未更新缓存。")
            if description in seen:
                continue
            seen.add(description)
            # ZF's compact "other course" format contains course/teacher, weeks,
            # teaching-class and class composition; retain its full description.
            parts = description.split("/")
            teaching_class = _text(raw.get("jxbmc")) or (parts[2] if len(parts) >= 4 else "")
            course_name = _text(raw.get("kcmc")) or re.sub(r"-\d+$", "", teaching_class) or parts[0]
            others.append({"course_name": course_name,
                           "description": description, "class_label": _text(raw.get("jxbzc")) or (parts[3] if len(parts) >= 4 else ""),
                           "teaching_class_name": teaching_class, "course_code": _text(raw.get("kch")),
                           "weeks_text": _text(raw.get("zcd") or raw.get("qsjsz")) or (parts[1] if len(parts) >= 4 else ""),
                           "source_list": key})
    return lessons, others


async def fetch_teacher_timetables(client, *, xnm, xqm, filters, exact_teacher_no=None):
    """Fetch bounded, complete results, using the caller's existing auth client."""
    xnm, xqm = str(xnm), str(xqm)
    response = await client.get(INDEX)
    _check_response(response)
    options = parse_query_options(response.text)
    for field, value in (("xnm", xnm), ("xqm", xqm)):
        if value not in {o["id"] for o in options[field]}:
            raise TeacherTimetableSourceError("所选学年学期不在教务系统可查询范围。")
    query = {key: "" for key in ("jsxb_id", "kkxb_id", "xzzw", "tsxxkcxbj", "xsdm", "kclxdm",
                                     "kclbdm", "zs", "yf", "jsdm_query", "kbsjlyqz", "xb_id", "xsxy",
                                     "ccdm", "rkxy_id", "drcx_id", "jzglbm")}
    query.update({"xnm": xnm, "xqm": xqm, "jgh": exact_teacher_no or filters["q"], "zxsypkjs": "false"})
    for local, remote in (("campus_id", "xqh_id"), ("department_id", "jg_id"), ("title_id", "zcm"),
                          ("college_id", "kkbm_id"), ("external", "sfwp")):
        value = "" if exact_teacher_no is not None else _text(filters.get(local))
        if value and value not in {o["id"] for o in options.get(remote, [])}:
            raise TeacherTimetableSourceError("筛选选项已变更，请重新查询后选择。")
        query[remote] = value
    rows, total, pages = _validate_list(await _post(client, LIST, _jqgrid_form(
        page=1, show_count=PAGE_SIZE, extra=query, sort_name=" ")), page=1)
    for page in range(2, pages + 1):
        more, _, new_pages = _validate_list(await _post(client, LIST, _jqgrid_form(
            page=page, show_count=PAGE_SIZE, extra=query, sort_name=" ")), page=page, total_expected=total)
        if new_pages != pages:
            raise TeacherTimetableSourceError("教师分页范围发生变化，请重试。")
        rows.extend(more)
    if len(rows) != total:
        raise TeacherTimetableSourceError("教师列表未完整返回，未更新缓存。")
    teachers, seen, matched_rows = [], set(), []
    for row in rows:
        if not isinstance(row, dict):
            raise TeacherTimetableSourceError("教务教师资料格式无效。")
        no, name = _text(row.get("jgh")), _text(row.get("xm"))
        if not no or not name or not _text(row.get("jgh_id")) or no in seen:
            raise TeacherTimetableSourceError("教务教师工号缺失或重复，未更新缓存。")
        if _text(row.get("xnm")) != xnm or _text(row.get("xqm")) != xqm:
            raise TeacherTimetableSourceError("教师列表的学期与查询不一致。")
        seen.add(no)
        if exact_teacher_no is None or no == exact_teacher_no:
            matched_rows.append(row)
    if exact_teacher_no is not None and len(matched_rows) != 1:
        raise TeacherTimetableSourceError("教务系统未找到该精确工号，已有课表缓存保持不变。")
    for row in matched_rows:
        no, name = _text(row.get("jgh")), _text(row.get("xm"))
        # Match filters select teachers only. Every snapshot always stores their
        # full term, never a college/week/primary-only subset.
        detail = {k: _text(row.get(k)) for k in ("jgh", "jgh_id", "xm", "jgmc", "rkxy", "xqh_id")}
        detail.update({"xnm": xnm, "xqm": xqm, "xnmc": f"{xnm}-{int(xnm)+1}",
                       "xqmmc": {"3": "1", "12": "2", "16": "3"}[xqm], "kzlx": "ck",
                       "zs": "", "xsdm": "", "kclxdm": "", "kclbdm": "", "kbsjlyqz": "",
                       "kkbm_id": "", "zxszjjs": "false", "djsktkb": "0", "bxscxkc": "0",
                       "xsewkbnr": "0", "sfwp": "", "zxsypkjs": "false", "yf": "", "ccdm": "", "rkxy_id": ""})
        locked = await _post(client, CHECK, detail)
        if locked not in (False, "false"):
            raise TeacherTimetableSourceError("教务系统尚未开放该学期教师课表的查看权限。")
        fields = ("kch", "sj", "cd", "fx", "jxb", "jxbzc", "kcxzjc", "jxbrs", "xkrs", "khfs",
                  "xkbz", "kcxszc", "zxxx", "zxs", "cxbj", "zzrl", "bkmc", "xq", "kczxs", "ksfs")
        payload = await _post(client, TIMETABLE, {**detail, **{f"xszd[{k}]": "true" for k in fields}})
        lessons, unscheduled = normalize_timetable(payload, teacher_no=no, xnm=xnm, xqm=xqm)
        campus_id = _text(row.get("xqh_id"))
        teachers.append({"teacher_no": no, "name": name, "college_id": _text(row.get("jg_id")),
                         "college_name": _text(row.get("jgmc")), "department_id": _text(row.get("jg_id")),
                         "department_name": _text(row.get("jgmc")), "title": _text(row.get("zcmc")),
                         "title_id": _text(row.get("zcm")), "campus_id": campus_id,
                         "campus_name": next((o["name"] for o in options.get("xqh_id", []) if o["id"] == campus_id), ""),
                         "external": _text(row.get("sfwp")), "schedule_payload": payload,
                         "lessons": lessons, "unscheduled_courses": unscheduled})
    return {"teachers": teachers, "options": {name: options.get(field, []) for field, name in OPTION_FIELDS.items()}}
