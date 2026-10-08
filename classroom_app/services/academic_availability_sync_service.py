"""Pull the data the timetable editor needs to judge "可调时段" from 教务 (正方).

Endpoints were verified against the live GXUFL 教务 on 2026-09-27 (teacher
role). Nothing here writes to 教务.

Sources
-------
1. 行政班课表 (students' timetables) via the 班级课表打印 module (N214505).
   The teacher role has no 班级课表查询 module, but the print module's data
   calls answer for any class: first ``bjkbdy_cxBjkbdyTjkbList`` lists the
   class rows (``bh_id`` = 正方 班级 id, the roster's ``BH_ID``/
   ``admin_class_code``), then ``bjkbdy_cxBjKb`` returns ``kbList`` **only
   when posted with the whole class row** (``xqh_id``/``njdm_id``/``zyh_id``/
   ``tjkbzdm``…) — a bare ``bh_id`` yields an empty list. Items follow the
   teacher-timetable contract (``xqj``/``jcs``/``zcd``/``cdmc``/``kcmc``), so
   the existing parser is reused.
2. 教室课表 (room timetable) via the 场地课表打印 module (N214515):
   ``cdkbdy_cxCdKb`` with ``cd_id`` (the ``teacher_academic_teaching_places``
   place id, e.g. ``131416X``) returns the room's whole-term ``kbList``.
   Targeted 空闲教室 checks (``search_free_rooms``) remain the fallback and
   cache one verdict per (week, weekday, sections).
3. 冲突检测 (``ttksq_cxConflictCtzt``, in the draft-push service) returns
   ``conflictNum`` + ``ctxxList`` items keyed in UPPERCASE (``CTLX`` 冲突类型,
   ``MC`` 对象, ``JXBMC``, ``KCMC``, ``XQJ``, ``JC``, ``ZCD``; student rows add
   ``XH``/``BJ``/``XB``) and ``conflictXs`` = the student subset.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx

from ..database import get_db_connection
from .academic_classroom_sync_service import (
    load_teacher_teaching_place_by_key, load_teacher_teaching_places,
    query_free_classrooms_from_academic_system,
)
from .academic_course_sync_service import (
    ZF_TIMETABLE_FIELD_KEYS, _build_timetable_form, _fetch_timetable_field_keys, _parse_schedule_response,
    _parse_week_numbers,
)
from .academic_integration_service import load_teacher_academic_access_method, open_authenticated_academic_client
from .schedule_availability_service import (
    admin_classes_for_teaching_class, record_room_slot_check, replace_class_slots, replace_room_slots,
    resolve_room_id, save_sync_state,
)
from .semester_identity_service import identity_from_year_term

logger = logging.getLogger(__name__)


def _env_paths(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name, "").strip()
    return [item.strip() for item in raw.split(",") if item.strip()] or default


# 班级课表打印 (N214505) — verified 2026-09-27; override with LANSHARE_ZF_CLASS_TIMETABLE_PATHS (first entry wins).
ZF_CLASS_PRINT_GNMKDM = "N214505"
ZF_CLASS_TIMETABLE_INDEX_PATH = os.getenv("LANSHARE_ZF_CLASS_TIMETABLE_INDEX", f"/kbdy/bjkbdy_cxBjkbdyIndex.html?gnmkdm={ZF_CLASS_PRINT_GNMKDM}&layout=default")
ZF_CLASS_ROWS_PATH = os.getenv("LANSHARE_ZF_CLASS_ROWS_PATH", f"/kbdy/bjkbdy_cxBjkbdyTjkbList.html?gnmkdm={ZF_CLASS_PRINT_GNMKDM}")
ZF_CLASS_TIMETABLE_QUERY_PATHS = _env_paths("LANSHARE_ZF_CLASS_TIMETABLE_PATHS", [f"/kbdy/bjkbdy_cxBjKb.html?gnmkdm={ZF_CLASS_PRINT_GNMKDM}"])
# 场地课表打印 (N214515) — verified 2026-09-27; override with LANSHARE_ZF_ROOM_TIMETABLE_PATHS.
ZF_ROOM_PRINT_GNMKDM = "N214515"
ZF_ROOM_TIMETABLE_INDEX_PATH = os.getenv("LANSHARE_ZF_ROOM_TIMETABLE_INDEX", f"/kbdy/cdkbdy_cxCdkbdyIndex.html?gnmkdm={ZF_ROOM_PRINT_GNMKDM}&layout=default")
ZF_ROOM_TIMETABLE_QUERY_PATHS = _env_paths("LANSHARE_ZF_ROOM_TIMETABLE_PATHS", [f"/kbdy/cdkbdy_cxCdKb.html?gnmkdm={ZF_ROOM_PRINT_GNMKDM}"])
# The class-row fields the 班级课表打印 page forwards to the timetable query (ylKbdy() in bjkbdy.js).
CLASS_ROW_FORWARDED_FIELDS = ("xnm", "xqm", "xnmc", "xqmmc", "xqh_id", "njdm_id", "zyh_id", "bh_id", "tjkbzdm", "tjkbzxsdm",
                              "zymc", "jgmc", "njmc", "bj", "xkrs", "jsxm", "lxdh", "bh")
CLASS_ROWS_PAGE_SIZE = 500
HTTP_TIMEOUT_SECONDS = 25.0
MAX_ROOMS_PER_SYNC = 6
MAX_CLASSES_PER_SYNC = 24


class AvailabilitySyncError(ValueError):
    pass


def _ajax_headers(client: httpx.AsyncClient, referer: str) -> dict[str, str]:
    base = str(client.base_url).rstrip("/")
    return {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": base, "Referer": base + referer,
    }


def slot_from_item(item: Any) -> dict[str, Any] | None:
    """Normalise a parsed timetable item (weekday 0-6, '2-3', '1-3周,6-16周') into a slot."""
    if item.weekday is None:
        return None
    numbers = [int(n) for n in re.findall(r"\d+", str(item.section_text or ""))]
    if not numbers:
        return None
    sections = list(range(min(numbers), max(numbers) + 1)) if len(numbers) >= 2 else [numbers[0]]
    weeks = _parse_week_numbers(item.weeks_text or "", max_week_count=40)
    if not weeks:
        return None
    return {
        "weekday": int(item.weekday) + 1, "sections": sections, "weeks": weeks,
        "course_name": item.course_name, "teaching_class_name": item.teaching_class_name,
        "room": item.location, "teacher_name": item.teacher_name,
    }


async def probe_timetable(client: httpx.AsyncClient, paths: list[str], form: dict[str, Any], *, referer: str,
                          sources: list[dict[str, Any]], label: str) -> tuple[list[dict[str, Any]], str]:
    """POST the same form to each candidate path; return slots from the first JSON ``kbList`` answer."""
    for path in paths:
        try:
            response = await client.post(path, data=form, headers=_ajax_headers(client, referer), timeout=HTTP_TIMEOUT_SECONDS)
        except httpx.HTTPError as exc:
            sources.append({"path": path, "label": label, "status": "failed", "message": str(exc)[:160]})
            continue
        if response.status_code >= 400 or "login_" in response.url.path.lower():
            sources.append({"path": path, "label": label, "status": "rejected", "status_code": response.status_code})
            continue
        items, parser = _parse_schedule_response(response, source_url=str(response.url))
        content_type = response.headers.get("content-type", "").lower()
        if parser != "json" and "json" not in content_type:
            sources.append({"path": path, "label": label, "status": "unrecognised", "parser": parser})
            continue
        slots = [slot for slot in (slot_from_item(item) for item in items) if slot]
        sources.append({"path": path, "label": label, "status": "success", "item_count": len(items), "slot_count": len(slots)})
        return slots, path
    return [], ""


def _class_rows_form(term_params: dict[str, str], *, bh_id: str = "") -> dict[str, str]:
    """``paramMap()`` of bjkbdy.js plus the grid paging fields."""
    return {
        **term_params, "xqh_id": "", "njdm_id": "", "xb_id": "", "jg_id": "", "zyh_id": "", "zyfx_id": "", "bh_id": bh_id,
        "xsdm": "", "pyccdm": "", "kclxdm": "", "kclbdm": "", "sfzhsjk": "", "kbsjlyqz": "", "zs": "", "yf": "", "sfcxxqh": "0",
        "_search": "false", "nd": "0", "queryModel.showCount": str(CLASS_ROWS_PAGE_SIZE), "queryModel.currentPage": "1",
        "queryModel.sortName": "", "queryModel.sortOrder": "asc", "time": "0",
    }


async def fetch_class_rows(client: httpx.AsyncClient, term_params: dict[str, str], *, bh_id: str = "") -> list[dict[str, Any]]:
    """Class rows of the 班级课表打印 grid (``items``); ``bh_id`` narrows to one class."""
    response = await client.post(ZF_CLASS_ROWS_PATH, data=_class_rows_form(term_params, bh_id=bh_id),
                                 headers=_ajax_headers(client, ZF_CLASS_TIMETABLE_INDEX_PATH), timeout=HTTP_TIMEOUT_SECONDS)
    if response.status_code >= 400:
        return []
    try:
        payload = response.json()
    except ValueError:
        return []
    items = payload.get("items") if isinstance(payload, dict) else None
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def class_timetable_form(row: dict[str, Any], term_params: dict[str, str], field_keys: list[str]) -> dict[str, Any]:
    """The map ``ylKbdy()`` posts: the whole class row + display switches + ``xszd[...]`` flags."""
    form: dict[str, Any] = {key: str(row.get(key) or "") for key in CLASS_ROW_FORWARDED_FIELDS}
    form.update({**term_params, "zs": "", "zxszjjs": "false", "akcxqjchb": "false", "xsdm": "", "kclxdm": "", "kclbdm": "",
                 "kbsjlyqz": "", "yf": "", "kzlx": "ck", "sfcxxqh": "0"})
    for key in (field_keys or ZF_TIMETABLE_FIELD_KEYS):
        if key:
            form[f"xszd[{key}]"] = "true"
    return form


async def fetch_class_timetable(client: httpx.AsyncClient, term_params: dict[str, str], field_keys: list[str], *,
                                code: str, name: str, sources: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    """Slots of one admin class: locate its row (by ``bh_id``, else by name), then query with the whole row."""
    label = f"班级课表 {name}"
    rows = await fetch_class_rows(client, term_params, bh_id=code) if code else []
    row = next((r for r in rows if str(r.get("bh_id") or "") == code), rows[0] if rows else None)
    if row is None and name:
        # Older rosters carry a different class key; fall back to matching the class name in the full list.
        row = next((r for r in await fetch_class_rows(client, term_params) if str(r.get("bj") or "").strip() == name.strip()), None)
    if row is None:
        sources.append({"path": ZF_CLASS_ROWS_PATH, "label": label, "status": "rejected", "message": "班级课表打印列表中没有该班级"})
        return [], ""
    return await probe_timetable(client, ZF_CLASS_TIMETABLE_QUERY_PATHS, class_timetable_form(row, term_params, field_keys),
                                 referer=ZF_CLASS_TIMETABLE_INDEX_PATH, sources=sources, label=label)


async def fetch_room_timetable(client: httpx.AsyncClient, term_params: dict[str, str], field_keys: list[str], *,
                               room_id: str, room_name: str, sources: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    """Whole-term slots of one room via 场地课表打印 (``cd_id`` = teaching-place id)."""
    form = {**_build_timetable_form(term_params, field_keys), "cd_id": room_id, "cdmc": room_name}
    return await probe_timetable(client, ZF_ROOM_TIMETABLE_QUERY_PATHS, form, referer=ZF_ROOM_TIMETABLE_INDEX_PATH,
                                 sources=sources, label=f"教室课表 {room_name}")


def teaching_scopes(overview: dict[str, Any]) -> list[dict[str, str]]:
    seen: dict[str, dict[str, str]] = {}
    for week in overview.get("weeks") or []:
        for lesson in week.get("lessons") or []:
            tcid = str(lesson.get("teaching_class_id") or "").strip()
            if tcid and tcid not in seen:
                seen[tcid] = {"teaching_class_id": tcid, "teaching_class_name": str(lesson.get("teaching_class_name") or ""),
                              "course_name": str(lesson.get("course_name") or ""), "room": str(lesson.get("classroom") or "")}
    return list(seen.values())


async def sync_availability_for_term(teacher_id: int, *, year: str, term: str, overview: dict[str, Any]) -> dict[str, Any]:
    """Refresh students' and rooms' timetables for every teaching class in the term overview."""
    teacher_id = int(teacher_id)
    identity = identity_from_year_term(year, term)
    if identity is None:
        return {"status": "invalid_semester", "message": "学年学期无效。"}
    xnm, xqm = identity.as_xnm_xqm()
    term_params = {"xnm": xnm, "xqm": xqm}
    scopes = teaching_scopes(overview)
    with get_db_connection() as conn:
        credential = load_teacher_academic_access_method(conn, teacher_id, school_code="gxufl")
        admin_map = {s["teaching_class_id"]: admin_classes_for_teaching_class(conn, teacher_id, s["teaching_class_id"]) for s in scopes}
        room_ids: dict[str, str] = {}
        for scope in scopes:
            rid = resolve_room_id(conn, scope["room"])
            if rid:
                room_ids[rid] = scope["room"]
    if not credential:
        return {"status": "missing_credential", "message": "请先在教务系统对接设置中验证并保存账号。"}
    if not scopes:
        return {"status": "nothing", "message": "本学期没有可分析的教务课次。"}
    admin_classes: dict[str, str] = {}
    for items in admin_map.values():
        for item in items:
            admin_classes.setdefault(item["code"], item["name"])
    sources: list[dict[str, Any]] = []
    class_slot_count = room_slot_count = class_ok = room_ok = 0
    try:
        async with open_authenticated_academic_client(credential) as (client, profile, _login):
            if profile.school_code != "gxufl":
                raise AvailabilitySyncError("当前学校尚未启用可调时段同步。")
            # Open the print modules once so 教务 registers the module context for the data calls.
            for index_path in (ZF_CLASS_TIMETABLE_INDEX_PATH, ZF_ROOM_TIMETABLE_INDEX_PATH):
                try:
                    await client.get(index_path, headers={"Accept": "text/html,*/*;q=0.8"}, timeout=HTTP_TIMEOUT_SECONDS)
                except httpx.HTTPError:
                    pass
            field_keys = await _fetch_timetable_field_keys(client, sources) or ZF_TIMETABLE_FIELD_KEYS
            for code, name in list(admin_classes.items())[:MAX_CLASSES_PER_SYNC]:
                slots, path = await fetch_class_timetable(client, term_params, field_keys, code=code, name=name, sources=sources)
                if path:
                    class_ok += 1
                    with get_db_connection() as conn:
                        class_slot_count += replace_class_slots(conn, year=year, term=term, scope_kind="admin_class", scope_key=code,
                                                                scope_name=name, slots=slots, source_path=path)
                        conn.commit()
            for rid, rname in list(room_ids.items())[:MAX_ROOMS_PER_SYNC]:
                slots, path = await fetch_room_timetable(client, term_params, field_keys, room_id=rid, room_name=rname, sources=sources)
                if path:
                    room_ok += 1
                    with get_db_connection() as conn:
                        room_slot_count += replace_room_slots(conn, year=year, term=term, room_id=rid, room_name=rname, slots=slots, source_path=path)
                        conn.commit()
    except (ValueError, httpx.HTTPError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else "教务系统访问失败，可调时段数据未更新。"
        with get_db_connection() as conn:
            save_sync_state(conn, teacher_id, year=year, term=term, status="failed", message=message, source_summary=sources)
            conn.commit()
        return {"status": "failed", "message": message, "sources": sources}
    except Exception:
        logger.exception("Availability sync failed for teacher %s", teacher_id)
        message = "可调时段同步发生未知错误，请稍后重试。"
        with get_db_connection() as conn:
            save_sync_state(conn, teacher_id, year=year, term=term, status="failed", message=message, source_summary=sources)
            conn.commit()
        return {"status": "failed", "message": message, "sources": sources}

    if not admin_classes:
        status, message = "no_scope", "本学期教学班尚未同步学生名单，无法推导行政班课表；请先同步「班级与学生名单」。"
    elif class_ok == 0:
        status, message = "class_unavailable", "教务班级课表打印列表中未找到这些行政班（或课表为空）；学生课表暂不可用，教室占用可按时段实时查询。"
    else:
        status = "success"
        message = f"已同步 {class_ok}/{len(admin_classes)} 个行政班课表（{class_slot_count} 段）"
        message += f"，{room_ok}/{len(room_ids)} 间教室课表（{room_slot_count} 段）。" if room_ids else "。"
        if room_ids and room_ok == 0:
            message += " 教室课表未返回，教室占用改为按时段实时查询。"
    with get_db_connection() as conn:
        save_sync_state(conn, teacher_id, year=year, term=term, status=status, message=message,
                        class_scope_count=class_ok, class_slot_count=class_slot_count, room_count=room_ok,
                        room_slot_count=room_slot_count, source_summary=sources, synced=status == "success")
        conn.commit()
    return {"status": status, "message": message, "class_scope_count": class_ok, "class_slot_count": class_slot_count,
            "room_count": room_ok, "room_slot_count": room_slot_count, "sources": sources}


def _room_matches(item: dict[str, Any], room_id: str, room_name: str) -> bool:
    ids = {str(item.get(key) or "").strip() for key in ("place_id", "room_code")} - {""}
    if room_id and ids:
        return room_id in ids
    names = {str(item.get(key) or "").strip() for key in ("room_name", "room_full_name", "display_name")} - {""}
    return bool(room_name and room_name in names)


def _free_room_target(conn, teacher_id: int, room_id: str, room_name: str) -> dict | None:
    if room_id:
        return load_teacher_teaching_place_by_key(conn, teacher_id, place_id=room_id)
    if room_name:
        matches = load_teacher_teaching_places(conn, teacher_id, search=room_name, limit=2)
        if len(matches) == 1 and _room_matches(matches[0], "", room_name):
            return matches[0]
    return None


async def search_free_rooms(teacher_id: int, *, year: str, term: str, week: int, weekday: int, sections: list[int],
                            keyword: str = "", room_id: str = "", room_name: str = "", building: str = "",
                            room_type: str = "", campus: str = "", page: int = 1, page_size: int = 40) -> dict[str, Any]:
    """二次搜索：free rooms for one slot via the existing 空闲教室 query; also records the
    verdict for ``room_id`` (the lesson's current room) in the slot-check cache."""
    identity = identity_from_year_term(year, term)
    if identity is None:
        return {"status": "invalid", "message": "学年学期无效。", "items": []}
    xnm, xqm = identity.as_xnm_xqm()
    year, term = identity.as_year_term()
    with get_db_connection() as conn:
        target = _free_room_target(conn, teacher_id, room_id.strip(), room_name.strip())
    campus_id = campus.strip() or str((target or {}).get("campus_id") or "1")
    filters = {"xnm": xnm, "xqm": xqm, "weeks": [int(week)], "weekday": [int(weekday)], "sections": [int(s) for s in sections],
               "cdmc": keyword.strip(), "lh": building.strip(), "cdlb_id": room_type.strip(), "xqh_id": campus_id,
               "page": page, "page_size": page_size, "recommendations": False}
    try:
        result = await query_free_classrooms_from_academic_system(int(teacher_id), filters)
    except Exception as exc:  # 教务 offline / unexpected payload must not 500 the editor
        logger.warning("Free-room search failed for teacher %s (%s)", teacher_id, type(exc).__name__)
        result = {"status": "academic_unavailable", "message": "教务系统暂时无法查询空闲教室，请稍后重试。", "items": []}
    items = result.get("items") or []
    room_status = "unknown"
    room_status_message = ""
    target_id = str((target or {}).get("place_id") or "")
    target_name = str((target or {}).get("room_full_name") or (target or {}).get("room_name") or room_name)
    target_campus = str((target or {}).get("campus_id") or "")
    school_code = str(result.get("school_code") or "")
    scoped_target = bool(target_id and target_campus and school_code and school_code == str((target or {}).get("school_code") or ""))
    if result.get("status") == "success" and scoped_target:
        if any(_room_matches(item, target_id, target_name) for item in items):
            room_status = "free"
        else:
            # Candidate filters/pages cannot prove absence: independently query
            # the scoped room, with its actual campus and all room types.
            target_filters = {**filters, "cd_id": target_id, "cdmc": "", "lh": "", "cdlb_id": "",
                              "xqh_id": target_campus, "page": 1, "page_size": 200}
            try:
                checked = await query_free_classrooms_from_academic_system(int(teacher_id), target_filters)
            except Exception as exc:
                logger.warning("Target room check failed (%s)", type(exc).__name__)
                checked = {"status": "academic_unavailable"}
            if checked.get("status") == "success" and checked.get("school_code") == school_code:
                if any(_room_matches(item, target_id, target_name) for item in checked.get("items") or []):
                    room_status = "free"
                elif checked.get("total_count") == 0:
                    room_status = "busy"
            if room_status == "unknown":
                room_status_message = "候选教室已查询；原教室的实时占用未能确认，请重试。"
        if room_status != "unknown":
            with get_db_connection() as conn:
                record_room_slot_check(conn, year=year, term=term, room_id=target_id, room_name=target_name, week=int(week),
                                       weekday=int(weekday), sections=[int(s) for s in sections], status=room_status,
                                       school_code=school_code,
                                       detail="实时查空：目标教室在所选完整时段不可用" if room_status == "busy" else "实时查空：空闲")
                conn.commit()
    elif result.get("status") == "success" and (room_id or room_name):
        room_status_message = "原教室尚无可确认的校区和场地标识，占用状态保持待核实。"
    return {**result, "items": items, "room_status": room_status, "room_status_message": room_status_message,
            "slot": {"week": int(week), "weekday": int(weekday), "sections": [int(s) for s in sections]}}
