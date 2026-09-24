"""Pull the data the timetable editor needs to judge "可调时段" from 教务 (正方).

Designed from 正方 v9 conventions while GXUFL's 教务 is offline, so every
endpoint is a *candidate list* that is probed in order; the first one that
answers with the expected JSON contract wins and the choice is recorded in the
sync summary. Nothing here writes to 教务.

Sources
-------
1. 行政班课表 (students' timetables). The lesson's teaching class is mapped
   to admin classes through the synced roster memberships (``bj_id`` =
   ``admin_class_code``). The class timetable query follows the same
   ``kbList`` contract as the teacher timetable (``xqj``/``jcs``/``zcd``/
   ``cdmc``/``kcmc``), so the existing parser is reused.
2. 教室课表 (room timetable) for each room the teacher's lessons use.
   Optional: when no candidate answers, the editor falls back to targeted
   空闲教室 checks (``search_free_rooms``) which reuse the existing free-room
   query and cache one verdict per (week, weekday, sections).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx

from ..database import get_db_connection
from .academic_classroom_sync_service import query_free_classrooms_from_academic_system
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


# 班级课表查询 (bj_id) — probed in order; override with LANSHARE_ZF_CLASS_TIMETABLE_PATHS.
ZF_CLASS_TIMETABLE_QUERY_PATHS = _env_paths("LANSHARE_ZF_CLASS_TIMETABLE_PATHS", [
    "/kbcx/bjkbcx_cxBjKb.html?gnmkdm=N2153",
    "/kbcx/bjkbcx_cxBjKb.html?gnmkdm=N2150",
    "/kbdy/bjkbdy_cxBjKb.html?gnmkdm=N214505",
])
ZF_CLASS_TIMETABLE_INDEX_PATH = os.getenv("LANSHARE_ZF_CLASS_TIMETABLE_INDEX", "/kbcx/bjkbcx_cxBjkbcxIndex.html?gnmkdm=N2153&layout=default")
# 教室课表查询 (cd_id) — optional; override with LANSHARE_ZF_ROOM_TIMETABLE_PATHS.
ZF_ROOM_TIMETABLE_QUERY_PATHS = _env_paths("LANSHARE_ZF_ROOM_TIMETABLE_PATHS", [
    "/kbcx/cdkbcx_cxCdKb.html?gnmkdm=N2154",
    "/kbcx/jscdkbcx_cxJscdKb.html?gnmkdm=N2151",
])
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
            try:
                await client.get(ZF_CLASS_TIMETABLE_INDEX_PATH, headers={"Accept": "text/html,*/*;q=0.8"}, timeout=HTTP_TIMEOUT_SECONDS)
            except httpx.HTTPError:
                pass
            field_keys = await _fetch_timetable_field_keys(client, sources) or ZF_TIMETABLE_FIELD_KEYS
            for code, name in list(admin_classes.items())[:MAX_CLASSES_PER_SYNC]:
                form = {**_build_timetable_form(term_params, field_keys), "bj_id": code, "bjmc": name}
                slots, path = await probe_timetable(client, ZF_CLASS_TIMETABLE_QUERY_PATHS, form,
                                                    referer=ZF_CLASS_TIMETABLE_INDEX_PATH, sources=sources, label=f"班级课表 {name}")
                if path:
                    class_ok += 1
                    with get_db_connection() as conn:
                        class_slot_count += replace_class_slots(conn, year=year, term=term, scope_kind="admin_class", scope_key=code,
                                                                scope_name=name, slots=slots, source_path=path)
                        conn.commit()
            for rid, rname in list(room_ids.items())[:MAX_ROOMS_PER_SYNC]:
                form = {**_build_timetable_form(term_params, field_keys), "cd_id": rid, "cdmc": rname}
                slots, path = await probe_timetable(client, ZF_ROOM_TIMETABLE_QUERY_PATHS, form,
                                                    referer=ZF_CLASS_TIMETABLE_INDEX_PATH, sources=sources, label=f"教室课表 {rname}")
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
        status, message = "endpoint_unverified", "教务未返回班级课表（接口待联调）；学生课表暂不可用，教室占用可按时段实时查询。"
    else:
        status = "success"
        message = f"已同步 {class_ok}/{len(admin_classes)} 个行政班课表（{class_slot_count} 段）"
        message += f"，{room_ok}/{len(room_ids)} 间教室课表（{room_slot_count} 段）。" if room_ids else "。"
        if room_ids and room_ok == 0:
            message += " 教室课表接口未响应，教室占用改为按时段实时查询。"
    with get_db_connection() as conn:
        save_sync_state(conn, teacher_id, year=year, term=term, status=status, message=message,
                        class_scope_count=class_ok, class_slot_count=class_slot_count, room_count=room_ok,
                        room_slot_count=room_slot_count, source_summary=sources, synced=status == "success")
        conn.commit()
    return {"status": status, "message": message, "class_scope_count": class_ok, "class_slot_count": class_slot_count,
            "room_count": room_ok, "room_slot_count": room_slot_count, "sources": sources}


def _room_matches(item: dict[str, Any], room_id: str, room_name: str) -> bool:
    candidates = {str(item.get(key) or "").strip() for key in ("place_id", "room_code", "room_name", "room_full_name", "display_name")}
    return bool(candidates & {room_id, room_name}) if (room_id or room_name) else False


async def search_free_rooms(teacher_id: int, *, year: str, term: str, week: int, weekday: int, sections: list[int],
                            keyword: str = "", room_id: str = "", room_name: str = "", building: str = "",
                            room_type: str = "") -> dict[str, Any]:
    """二次搜索：free rooms for one slot via the existing 空闲教室 query; also records the
    verdict for ``room_id`` (the lesson's current room) in the slot-check cache."""
    identity = identity_from_year_term(year, term)
    if identity is None:
        return {"status": "invalid", "message": "学年学期无效。", "items": []}
    xnm, xqm = identity.as_xnm_xqm()
    filters = {"xnm": xnm, "xqm": xqm, "weeks": [int(week)], "weekday": [int(weekday)], "sections": [int(s) for s in sections],
               "cdmc": keyword.strip(), "lh": building.strip(), "cdlb_id": room_type.strip() or "05", "page_size": 200}
    try:
        result = await query_free_classrooms_from_academic_system(int(teacher_id), filters)
    except Exception as exc:  # 教务 offline / unexpected payload must not 500 the editor
        logger.warning("Free-room search failed for teacher %s: %s", teacher_id, exc)
        result = {"status": "academic_unavailable", "message": f"教务系统暂时无法查询空闲教室：{str(exc)[:120]}", "items": []}
    items = result.get("items") or []
    room_status = "unknown"
    if result.get("status") == "success" and (room_id or room_name):
        # Only a keyword-free query enumerates every free room, so the absence of the
        # current room is conclusive only then.
        matched = any(_room_matches(item, room_id, room_name) for item in items)
        if matched:
            room_status = "free"
        elif not keyword.strip() and not building.strip():
            room_status = "busy"
        if room_status != "unknown" and room_id:
            with get_db_connection() as conn:
                record_room_slot_check(conn, year=year, term=term, room_id=room_id, room_name=room_name, week=int(week),
                                       weekday=int(weekday), sections=[int(s) for s in sections], status=room_status,
                                       detail="实时查空：教室未在空闲列表" if room_status == "busy" else "实时查空：空闲")
                conn.commit()
    return {**result, "items": items, "room_status": room_status,
            "slot": {"week": int(week), "weekday": int(weekday), "sections": [int(s) for s in sections]}}
