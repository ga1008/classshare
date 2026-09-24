"""Save timetable edit drafts into the 教务系统 (正方) 调停课申请 *draft* list.

Scope, deliberately narrow — this module talks to exactly four 教务 endpoints:

* ``ttksq_cxTtksqView.html``  open the 调停课申请 form for one teaching class
  (教务 allocates / reuses a draft ``ttk_id`` and returns the class's official
  slots plus any details already saved in the 待提交 list);
* ``ttksq_cxConflictCtzt.html`` the same conflict check the browser runs
  before 保存草稿;
* ``ttksq_cxSaveTtksj.html``  保存草稿 — append one detail row to the draft;
* ``ttksq_scTtksqsj.html``    remove one saved detail (撤回).

It never calls the 提交 endpoint. Submitting stays a human action inside 教务:
the teacher signs in, reviews the saved drafts, fills 调动原因/附件 and presses
提交申请 there (see docs/course-schedule-editor-2026-09.md).
"""

from __future__ import annotations

import json
import logging
import re
from html.parser import HTMLParser
from typing import Any

import httpx

from ..database import get_db_connection
from .academic_integration_service import load_teacher_academic_access_method, open_authenticated_academic_client
from .schedule_editor_service import (
    describe_slot, get_draft, list_drafts, update_draft_remote_state,
)
from .semester_identity_service import identity_from_year_term

logger = logging.getLogger(__name__)

GNMKDM = "N2122"
ENTRY_PATH = f"/tkgl/ttksq_cxTtksqIndex.html?information=1&doType=details&gnmkdm={GNMKDM}&layout=default"
FORM_VIEW_PATH = "/tkgl/ttksq_cxTtksqView.html"
CONFLICT_CHECK_PATH = f"/tkgl/ttksq_cxConflictCtzt.html?gnmkdm={GNMKDM}"
SAVE_DETAIL_PATH = f"/tkgl/ttksq_cxSaveTtksj.html?gnmkdm={GNMKDM}"
DELETE_DETAIL_PATH = f"/tkgl/ttksq_scTtksqsj.html?gnmkdm={GNMKDM}"
HTTP_TIMEOUT_SECONDS = 25.0
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
TK_TYPE_CODE = "01"          # 调课类别：调课
TK_FLOW_ID = "TKGL_TK"       # spl_id for 调课

# conflictNum bit meanings taken from the 教务 page script (index_ttksq.js).
CONFLICT_BITS = {
    1: "学生冲突（超出允许范围）", 2: "教师冲突", 4: "场地冲突", 8: "该时间段已申请过",
    16: "课表冲突", 32: "学生冲突", 64: "该停课信息已补课", 128: "实践课冲突",
}
HARD_CONFLICT_BITS = (8, 64)     # 教务 itself refuses these outright


class DraftPushError(ValueError):
    pass


def week_bitmask(weeks: list[int] | int) -> int:
    values = [weeks] if isinstance(weeks, int) else list(weeks)
    return sum(1 << (int(week) - 1) for week in values if int(week) >= 1)


def section_bitmask(sections: list[int]) -> int:
    return sum(1 << (int(section) - 1) for section in sections if int(section) >= 1)


def describe_conflict(conflict_num: int) -> str:
    labels = [label for bit, label in CONFLICT_BITS.items() if conflict_num & bit]
    return "，".join(labels) or f"冲突代码 {conflict_num}"


# ---------------------------------------------------------------------------
# Form page parsing
# ---------------------------------------------------------------------------

class _FormHTML(HTMLParser):
    """Collect hidden/text input values by id and the ``<option>`` rows of #tdlb."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.inputs: dict[str, str] = {}
        self.tdlb_options: list[dict[str, str]] = []
        self._in_tdlb = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: (value or "") for key, value in attrs}
        tag = tag.lower()
        if tag == "input":
            key = values.get("id") or values.get("name")
            if key and key not in self.inputs:
                self.inputs[key] = values.get("value", "")
        elif tag == "select" and values.get("id") == "tdlb":
            self._in_tdlb = True
        elif tag == "option" and self._in_tdlb:
            self.tdlb_options.append({"value": values.get("value", ""), "spl_id": values.get("spl_id", "")})

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "select":
            self._in_tdlb = False


def _inline_list(page_html: str, name: str) -> list[dict[str, Any]]:
    match = re.search(rf"\b{re.escape(name)}\s*=\s*eval\(", page_html)
    if not match:
        return []
    start = match.end()
    if start >= len(page_html) or page_html[start] != "[":
        return []
    try:
        value, _ = json.JSONDecoder().raw_decode(page_html, start)
    except ValueError as exc:
        raise DraftPushError(f"教务表单中的 {name} 数据无法解析。") from exc
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def parse_form_page(page_html: str) -> dict[str, Any]:
    """Structured view of the 调停课申请 form page for one teaching class."""
    if len(page_html) > MAX_RESPONSE_BYTES:
        raise DraftPushError("教务表单响应过大，停止解析。")
    document = _FormHTML()
    document.feed(page_html)
    inputs = document.inputs
    required = ("ttk_id", "jxb_id", "xnm", "xqm")
    missing = [key for key in required if not inputs.get(key)]
    if missing:
        raise DraftPushError("教务调停课表单缺少字段：" + "、".join(missing) + "（可能登录失效或页面协议变化）。")
    option = next((o for o in document.tdlb_options if o["value"] == TK_TYPE_CODE), None)
    if option is None or option.get("spl_id") != TK_FLOW_ID:
        raise DraftPushError("教务当前不允许发起「调课」类别申请，请登录教务系统核对。")
    return {
        "ttk_id": inputs["ttk_id"], "jxb_id": inputs["jxb_id"], "xnm": inputs["xnm"], "xqm": inputs["xqm"],
        "xqh_id": inputs.get("xqh_id", ""), "xsxy2": inputs.get("xsxy2", ""), "kkbm_id": inputs.get("kkbm_id", ""),
        "sfzf": inputs.get("sfzf", ""), "max_week": int(inputs.get("skzdzc") or 0) or None,
        "max_section": int(inputs.get("dqjc") or 0) or None,
        "slots": _inline_list(page_html, "modelList"),
        "existing_details": _inline_list(page_html, "tkxxList"),
    }


def _int_csv(value: Any) -> list[int]:
    return sorted(int(part) for part in re.split(r"[,，]", str(value or "")) if part.strip().isdigit())


def find_original_slot(slots: list[dict[str, Any]], original: dict[str, Any]) -> dict[str, Any] | None:
    """Match a lesson occurrence to the class's official slot: same weekday,
    week bit set in ``zc`` and identical section list."""
    week = int(original.get("week") or 0)
    weekday = int(original.get("weekday") or 0)
    sections = sorted(int(s) for s in original.get("sections") or [])
    for slot in slots:
        try:
            zc = int(str(slot.get("zc") or "0"))
        except ValueError:
            continue
        if int(str(slot.get("xqj") or 0) or 0) != weekday or not zc & (1 << (week - 1)):
            continue
        if _int_csv(slot.get("jcarr")) == sections:
            return slot
    return None


def find_existing_detail(details: list[dict[str, Any]], original: dict[str, Any]) -> dict[str, Any] | None:
    week = int(original.get("week") or 0)
    weekday = int(original.get("weekday") or 0)
    sections = sorted(int(s) for s in original.get("sections") or [])
    for detail in details:
        if int(str(detail.get("xqj") or 0) or 0) != weekday:
            continue
        if week not in _int_csv(detail.get("zcarr")) or _int_csv(detail.get("jcarr")) != sections:
            continue
        return detail
    return None


def build_detail_form(page: dict[str, Any], slot: dict[str, Any], draft: dict[str, Any]) -> list[tuple[str, str]]:
    """The exact field set the browser posts for 保存草稿 (``getDatas()`` map +
    the ``#ajaxForm`` inputs), as an ordered multipart list."""
    original, proposed = draft["original"], draft["proposed"]
    y_week, y_day, y_sections = int(original["week"]), int(original["weekday"]), [int(s) for s in original["sections"]]
    x_week, x_day, x_sections = int(proposed["week"]), int(proposed["weekday"]), [int(s) for s in proposed["sections"]]
    teacher_id = str(slot.get("jgh_id") or "")
    teacher_name = str(slot.get("jsxm") or "")
    y_room_id = str(slot.get("cd_id") or "")
    y_room_name = str(slot.get("jxdd") or slot.get("cdmc") or original.get("room") or "")
    x_room_id = str(proposed.get("room_id") or "") or y_room_id
    x_room_name = str(proposed.get("room") or "") or y_room_name
    if not teacher_id or not y_room_id:
        raise DraftPushError("教务原课次缺少教师或场地标识，无法保存草稿。")
    fields: list[tuple[str, str]] = [
        ("ttk_id", page["ttk_id"]), ("xnm", page["xnm"]), ("xqm", page["xqm"]), ("jxb_id", page["jxb_id"]),
        ("xqh_id", page.get("xqh_id", "")), ("xsxy2", page.get("xsxy2", "")), ("kkbm_id", page.get("kkbm_id", "")),
        ("sfzj", page.get("sfzf", "")), ("tklxdm", TK_TYPE_CODE), ("bdlb", ""), ("spl_id", TK_FLOW_ID),
        ("yzcd", str(week_bitmask(y_week))), ("yxqj", str(y_day)), ("yjc", str(section_bitmask(y_sections))),
        ("xzcd", str(week_bitmask(x_week))), ("xxqj", str(x_day)), ("xjc", str(section_bitmask(x_sections))),
        ("zcd", str(week_bitmask(x_week))), ("xqj", str(x_day)), ("jc", str(section_bitmask(x_sections))),
        # #ajaxForm inputs (the browser serialises the whole form alongside the map)
        ("qymc", ""), ("tkrq", ""), ("yjgh_id", teacher_id), ("yjsxm", teacher_name), ("ycd_id", y_room_id), ("ycdmc", y_room_name),
        ("tkrq", ""), ("xjsxm", teacher_name), ("xjgh_id", teacher_id), ("xcdmc", x_room_name), ("xcd_id", x_room_id),
        ("xskcd", ""), ("jxnr", ""), ("sfyxsgt", "1"), ("yylb", ""), ("tkyy", str(draft.get("reason") or "")),
        ("tksm", str(draft.get("note") or "")),
    ]
    return fields


def _multipart(fields: list[tuple[str, str]]) -> list[tuple[str, tuple[None, str]]]:
    return [(name, (None, value)) for name, value in fields]


def _headers(*, html: bool = False) -> dict[str, str]:
    return {
        "Accept": "text/html,*/*;q=0.8" if html else "application/json,text/javascript,*/*;q=0.8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://jwxt.gxufl.com" + ENTRY_PATH,
    }


async def _request(client: httpx.AsyncClient, method: str, path: str, *, label: str, **kwargs: Any) -> httpx.Response:
    try:
        response = await client.request(method, path, timeout=HTTP_TIMEOUT_SECONDS, **kwargs)
    except httpx.HTTPError as exc:
        raise DraftPushError(f"{label}请求失败（{type(exc).__name__}）。") from exc
    if 300 <= response.status_code < 400 or "login_" in response.url.path.lower():
        raise DraftPushError(f"{label}：教务登录会话已失效，请重新验证教务账号。")
    if not 200 <= response.status_code < 300:
        raise DraftPushError(f"{label}失败（HTTP {response.status_code}）。")
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise DraftPushError(f"{label}响应过大。")
    return response


def _json_or_none(response: httpx.Response) -> Any:
    text = response.text.strip()
    if not text or text == "null":
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text


async def open_form_page(client: httpx.AsyncClient, *, jxb_id: str, xnm: str, xqm: str) -> dict[str, Any]:
    response = await _request(
        client, "POST", FORM_VIEW_PATH, label="打开教务调停课表单",
        params={"jxb_id": jxb_id, "xnm": xnm, "xqm": xqm, "time": "0", "gnmkdm": GNMKDM}, headers=_headers(html=True),
    )
    page = parse_form_page(response.text)
    if page["jxb_id"] != jxb_id or (page["xnm"], page["xqm"]) != (xnm, xqm):
        raise DraftPushError("教务返回的表单教学班或学期与请求不一致。")
    return page


async def _save_one(client: httpx.AsyncClient, page: dict[str, Any], draft: dict[str, Any], *, force: bool,
                    force_note: str) -> dict[str, Any]:
    slot = find_original_slot(page["slots"], draft["original"])
    if slot is None:
        raise DraftPushError("教务正式课表中未找到该原课次（可能已被调整或本地课表过期），请先同步教务课表。")
    existing = find_existing_detail(page["existing_details"], draft["original"])
    if existing and existing.get("ttkxx_id"):
        return {"status": "pushed", "ttk_id": page["ttk_id"], "detail_id": str(existing["ttkxx_id"]),
                "label": str(existing.get("select_name") or ""), "message": "教务草稿中已存在该原课次的调整记录，已直接关联。"}
    fields = build_detail_form(page, slot, draft)
    check = await _request(client, "POST", CONFLICT_CHECK_PATH, label="教务冲突检测", files=_multipart(fields), headers=_headers())
    payload = _json_or_none(check)
    conflict_num = 0
    if isinstance(payload, dict) and str(payload.get("conflictNum") or "").strip():
        try:
            conflict_num = int(str(payload.get("conflictNum")))
        except ValueError:
            conflict_num = 0
    if conflict_num:
        message = describe_conflict(conflict_num)
        hard = any(conflict_num & bit for bit in HARD_CONFLICT_BITS)
        if hard or not force:
            return {"status": "conflict", "ttk_id": page["ttk_id"], "message": message,
                    "conflict": {"conflict_num": conflict_num, "hard": hard,
                                 "details": payload.get("ctxxList") if isinstance(payload, dict) else None}}
        fields = fields + [("sfctttk", "1"), ("ctskapqk", force_note or "已与相关方沟通，按新安排上课"), ("ttkctlx", message)]
    saved = await _request(client, "POST", SAVE_DETAIL_PATH, label="保存教务草稿", files=_multipart(fields), headers=_headers())
    result = _json_or_none(saved)
    detail_id, label = "", ""
    if isinstance(result, dict):
        detail_id = str(result.get("ttkxx_id") or "").split(",")[0]
        label = str(result.get("bcName") or "")
    if not detail_id:
        raise DraftPushError(f"教务未返回草稿明细标识：{str(result)[:200]}")
    return {"status": "pushed", "ttk_id": page["ttk_id"], "detail_id": detail_id, "label": label,
            "message": "已保存到教务调停课草稿（待提交）。" + (f"教务冲突提示：{describe_conflict(conflict_num)}" if conflict_num else "")}


async def push_drafts_to_academic_system(teacher_id: int, *, year: str, term: str, draft_ids: list[int] | None = None,
                                         force: bool = False, force_note: str = "") -> dict[str, Any]:
    """Save every pending local draft of the term into 教务 as 待提交 草稿."""
    teacher_id = int(teacher_id)
    identity = identity_from_year_term(year, term)
    if identity is None:
        return {"status": "invalid_semester", "message": "学年学期无效。", "results": []}
    xnm, xqm = identity.as_xnm_xqm()
    with get_db_connection() as conn:
        credential = load_teacher_academic_access_method(conn, teacher_id, school_code="gxufl")
        drafts = list_drafts(conn, teacher_id, year, term)
    if not credential:
        return {"status": "missing_credential", "message": "请先在教务系统对接设置中验证并保存账号。", "results": []}
    wanted = {int(d) for d in draft_ids or []}
    targets = [d for d in drafts if d["status"] in ("draft", "conflict", "failed") and (not wanted or d["id"] in wanted)]
    if not targets:
        return {"status": "nothing", "message": "没有待保存到教务的变更。", "results": []}
    results: list[dict[str, Any]] = []
    try:
        async with open_authenticated_academic_client(credential) as (client, profile, _login):
            if profile.school_code != "gxufl":
                raise DraftPushError("当前学校尚未启用调停课草稿保存。")
            await _request(client, "GET", ENTRY_PATH, label="打开教务调停课页面", headers=_headers(html=True))
            pages: dict[str, dict[str, Any]] = {}
            for draft in targets:
                jxb_id = draft["teaching_class_id"]
                outcome: dict[str, Any]
                try:
                    if jxb_id not in pages:
                        pages[jxb_id] = await open_form_page(client, jxb_id=jxb_id, xnm=xnm, xqm=xqm)
                    outcome = await _save_one(client, pages[jxb_id], draft, force=force, force_note=force_note)
                    if outcome["status"] == "pushed":
                        # keep the in-memory page in sync so a second draft of the
                        # same class sees the detail we just added
                        pages[jxb_id]["existing_details"].append({
                            "ttkxx_id": outcome["detail_id"], "xqj": draft["original"]["weekday"],
                            "zcarr": str(draft["original"]["week"]), "jcarr": ",".join(str(s) for s in draft["original"]["sections"]),
                            "select_name": outcome.get("label", ""),
                        })
                except DraftPushError as exc:
                    outcome = {"status": "failed", "message": str(exc)}
                results.append({"draft_id": draft["id"], "course_name": draft["course_name"],
                                "original_label": describe_slot(draft["original"]), "proposed_label": describe_slot(draft["proposed"]), **outcome})
    except (ValueError, httpx.HTTPError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else "教务系统访问失败，未保存任何草稿。"
        return {"status": "failed", "message": message, "results": results}
    except Exception:
        logger.exception("Schedule draft push failed for teacher %s", teacher_id)
        return {"status": "failed", "message": "保存教务草稿时发生未知错误，请稍后重试。", "results": results}

    with get_db_connection() as conn:
        for item in results:
            status = item["status"]
            update_draft_remote_state(
                conn, item["draft_id"], status=status if status in ("pushed", "conflict", "failed") else "failed",
                remote_ttk_id=item.get("ttk_id", ""), remote_detail_id=item.get("detail_id", ""),
                remote_label=item.get("label", ""), remote_message=item.get("message", ""),
                conflict=item.get("conflict"), pushed=status == "pushed",
            )
        conn.commit()
    pushed = sum(1 for item in results if item["status"] == "pushed")
    conflicts = sum(1 for item in results if item["status"] == "conflict")
    failed = len(results) - pushed - conflicts
    parts = [f"已保存 {pushed} 项到教务草稿"]
    if conflicts:
        parts.append(f"{conflicts} 项存在教务冲突")
    if failed:
        parts.append(f"{failed} 项失败")
    return {"status": "success" if pushed and not failed and not conflicts else ("partial" if pushed else "failed"),
            "message": "，".join(parts) + "。请登录教务系统核对后点击「提交申请」。", "results": results,
            "pushed": pushed, "conflicts": conflicts, "failed": failed}


async def withdraw_draft_from_academic_system(teacher_id: int, draft_id: int) -> dict[str, Any]:
    """Delete one saved detail from the 教务 draft (撤回), then unlock locally."""
    teacher_id = int(teacher_id)
    with get_db_connection() as conn:
        credential = load_teacher_academic_access_method(conn, teacher_id, school_code="gxufl")
        draft = get_draft(conn, teacher_id, int(draft_id))
    if draft is None:
        return {"status": "not_found", "message": "草稿不存在。"}
    if draft["status"] != "pushed" or not draft["remote_detail_id"]:
        return {"status": "nothing", "message": "该变更尚未保存到教务，无需撤回。"}
    if not credential:
        return {"status": "missing_credential", "message": "请先在教务系统对接设置中验证并保存账号。"}
    identity = identity_from_year_term(draft["year"], draft["term"])
    xnm, xqm = identity.as_xnm_xqm() if identity else ("", "")
    try:
        async with open_authenticated_academic_client(credential) as (client, _profile, _login):
            await _request(client, "GET", ENTRY_PATH, label="打开教务调停课页面", headers=_headers(html=True))
            await _request(client, "POST", DELETE_DETAIL_PATH, label="撤回教务草稿",
                           data={"ttkxx_id": draft["remote_detail_id"]}, headers=_headers())
            page = await open_form_page(client, jxb_id=draft["teaching_class_id"], xnm=xnm, xqm=xqm)
            if any(str(d.get("ttkxx_id")) == draft["remote_detail_id"] for d in page["existing_details"]):
                raise DraftPushError("教务未删除该草稿明细，可能该申请已提交，请登录教务系统处理。")
    except (ValueError, httpx.HTTPError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else "教务系统访问失败，未撤回草稿。"
        return {"status": "failed", "message": message}
    with get_db_connection() as conn:
        update_draft_remote_state(conn, draft["id"], status="draft", remote_message="已从教务草稿撤回，可继续修改。")
        conn.commit()
    return {"status": "success", "message": "已从教务草稿撤回，可继续修改后再次保存。"}
