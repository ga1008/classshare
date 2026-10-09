"""Save timetable edit drafts into the 教务系统 (正方) 调停课申请 *draft* list.

Scope, deliberately narrow — this module talks to exactly five 教务 endpoints:

* ``ttksq_cxTtksqView.html``  open the 调停课申请 form for one teaching class
  (教务 allocates / reuses a draft ``ttk_id`` and returns the class's official
  slots plus any details already saved in the 待提交 list);
* ``ttksq_cxConflictCtzt.html`` the same conflict check the browser runs
  before 保存草稿;
* ``ttksq_cxSaveTtksj.html``  保存草稿 — append one detail row to the draft;
* ``ttksq_scTtksqsj.html``    remove one saved detail (撤回);
* ``ttksq_cxUpdateTkyy.html`` save the application-level 调动原因/备注/附件. 教务
  keeps these on the application header, not on the detail rows — the detail
  save silently ignores ``tkyy`` — so without this call the draft shows an
  empty 原因 and 无附件 (verified 2026-10-09 against index_ttksq.js
  ``saveTkyy``, which the 提交申请 button runs *before* its separate submit).

It never calls the 提交 endpoint. Submitting stays a human action inside 教务:
the teacher signs in, reviews the saved drafts and presses 提交申请 there
(see docs/course-schedule-editor-2026-09.md).
"""

from __future__ import annotations

import html
import io
import json
import logging
import re
import zipfile
from pathlib import Path
from html.parser import HTMLParser
from typing import Any

import httpx
from datetime import datetime, timezone

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
UPDATE_REASON_PATH = f"/tkgl/ttksq_cxUpdateTkyy.html?gnmkdm={GNMKDM}"
ZF_REASON_MAX = 180                       # #tkyy validate stringMaxLength:180
ZF_NOTE_MAX = 500
ZF_ATTACHMENT_SUFFIXES = {".jpg", ".jpeg", ".png", ".doc", ".docx", ".pdf", ".zip", ".rar"}
ZF_ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024  # filehandle maxSize/maxTotal 10MB, maxCount 1
ZF_ATTACHMENT_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".pdf": "application/pdf",
                      ".doc": "application/msword", ".zip": "application/zip", ".rar": "application/octet-stream",
                      ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
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
MAX_CONFLICT_DETAILS = 80        # ctxxList can enumerate every clashing student; keep a bounded copy


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
        # 待提交 rows of the open draft application; they carry the header fields (tkyy/tksm/tksmfjm/fjm).
        "draft_details": _inline_list(page_html, "tjModelList"),
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


def _reject_unverified_existing_detail(page: dict[str, Any], draft: dict[str, Any]) -> None:
    # The save form's tkxxList contains the original slot, not the complete
    # proposed slot. Matching it cannot prove that our intended move was saved.
    # The separate published snapshot can be stale, so it cannot establish that
    # fact either. Preserve the local draft and require a remote review instead
    # of linking an unrelated change or creating a duplicate detail.
    if find_existing_detail(page["existing_details"], draft["original"]) is not None:
        raise DraftPushError(
            "教务已有该原课次的调整记录，但当前表单无法核对完整拟安排；"
            "未自动关联或重复保存。请先在教务核对并处理已有调整，再重试。"
        )


def _verify_withdrawable_detail(page: dict[str, Any], draft: dict[str, Any]) -> None:
    if page["ttk_id"] != draft["remote_ttk_id"]:
        raise DraftPushError("教务当前草稿申请已变化，原申请可能已经提交；未撤回，请到教务核对。")
    details = [row for row in page["existing_details"]
               if str(row.get("ttkxx_id") or "") == draft["remote_detail_id"]]
    if len(details) != 1:
        raise DraftPushError("教务当前申请中未找到唯一的原草稿明细；未撤回，请到教务核对。")
    detail, original = details[0], draft["original"]
    # One locally saved occurrence must not delete a remote detail that was
    # subsequently changed to cover several weeks or a different lesson.
    if (find_existing_detail(details, original) is None
            or _int_csv(detail.get("zcarr")) != [int(original["week"])]
            or (detail.get("jxb_id") and str(detail["jxb_id"]) != draft["teaching_class_id"])
            or (original.get("room_id") and str(detail.get("cd_id") or "") != original["room_id"])):
        raise DraftPushError("教务草稿明细的原安排与本地记录不一致；未撤回，请到教务核对。")


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
    _reject_unverified_existing_detail(page, draft)
    fields = build_detail_form(page, slot, draft)
    check = await _request(client, "POST", CONFLICT_CHECK_PATH, label="教务冲突检测", files=_multipart(fields), headers=_headers())
    conflict = conflict_outcome(_json_or_none(check))
    if conflict["conflict_num"]:
        message = conflict["message"]
        if conflict["hard"] or not force:
            return {"status": "conflict", "ttk_id": page["ttk_id"], "message": message, "conflict": conflict}
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
            "message": "已保存到教务调停课草稿（待提交）。" + (f"教务冲突提示：{conflict['message']}" if conflict["conflict_num"] else "")}


def conflict_outcome(payload: Any) -> dict[str, Any]:
    """Normalise a ``ttksq_cxConflictCtzt`` answer.

    Verified 2026-09-27: ``ctxxList`` items are UPPERCASE-keyed (CTLX 冲突类型 / MC 对象 / JXBMC / KCMC /
    XQJ / JC / ZCD, student rows add XH / BJ / XB); ``conflictXs`` is the student subset. One clash can list
    every affected student, so keep a bounded copy plus the counts.
    """
    conflict_num = 0
    if isinstance(payload, dict) and str(payload.get("conflictNum") or "").strip():
        try:
            conflict_num = int(str(payload.get("conflictNum")))
        except ValueError:
            conflict_num = 0
    details = payload.get("ctxxList") if isinstance(payload, dict) and isinstance(payload.get("ctxxList"), list) else []
    students = payload.get("conflictXs") if isinstance(payload, dict) and isinstance(payload.get("conflictXs"), list) else []
    hard = any(conflict_num & bit for bit in HARD_CONFLICT_BITS)
    return {"conflict_num": conflict_num, "hard": hard, "message": describe_conflict(conflict_num) if conflict_num else "",
            "details": details[:MAX_CONFLICT_DETAILS], "detail_count": len(details), "student_count": len(students)}


# ---------------------------------------------------------------------------
# Application header: 调动原因 / 备注 / 附件 (one per 教务 application = teaching class)
# ---------------------------------------------------------------------------

def _unique_texts(values: list[str]) -> list[str]:
    seen: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.append(text)
    return seen


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(text or ""))).strip()


def build_application_reason(drafts: list[dict[str, Any]], existing: str = "") -> tuple[str, str]:
    """One 教务 application holds every saved detail of a class, so the per-change
    reasons are merged into its single 调动原因 (≤180 字); a reason the teacher already
    typed in 教务 is kept first. Notes go to 备注说明."""
    reason = "；".join(_unique_texts([*str(existing or "").split("；"), *[d.get("reason", "") for d in drafts]]))
    note = "；".join(_unique_texts([d.get("note", "") for d in drafts]))
    return reason[:ZF_REASON_MAX], note[:ZF_NOTE_MAX]


def build_application_attachment(teacher_id: int, drafts: list[dict[str, Any]]) -> tuple[tuple[str, bytes] | None, str]:
    """教务 accepts exactly one attachment (jpg/png/pdf/doc/docx/zip/rar, ≤10MB).

    A single compatible proof is sent as-is; several proofs (or a type 教务 refuses,
    e.g. webp/txt) are bundled into one zip. Returns (file, warning)."""
    from .schedule_editor_service import ScheduleEditError, draft_proof_path

    files: list[tuple[str, Path]] = []
    for draft in drafts:
        for proof in draft.get("proofs") or []:
            try:
                path = draft_proof_path(teacher_id, draft["id"], str(proof.get("stored") or ""))
            except ScheduleEditError:
                continue
            if path.is_file():
                files.append((str(proof.get("name") or path.name), path))
    if not files:
        return None, ""
    # Refuse before reading anything into memory; zipping barely shrinks PDFs/images.
    if sum(path.stat().st_size for _name, path in files) > ZF_ATTACHMENT_MAX_BYTES:
        return None, "证明材料合计超过教务 10MB 上限，未上传附件，请在教务手动上传。"
    if len(files) == 1 and Path(files[0][0]).suffix.lower() in ZF_ATTACHMENT_SUFFIXES:
        name, path = files[0]
        content = path.read_bytes()
    else:
        buffer = io.BytesIO()
        used: set[str] = set()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for index, (name, path) in enumerate(files, start=1):
                entry = name if name not in used else f"{index}-{name}"
                used.add(entry)
                archive.write(path, entry)
        name, content = "调课证明材料.zip", buffer.getvalue()
    if len(content) > ZF_ATTACHMENT_MAX_BYTES:
        return None, "证明材料合计超过教务 10MB 上限，未上传附件，请在教务手动上传。"
    return (name, content), ""


def _application_form(page: dict[str, Any], reason: str, note: str, attachment: tuple[str, bytes] | None) -> list[Any]:
    """The #ajaxForm submission ``saveTkyy`` posts (header fields + one ``myFile`` part)."""
    fields: list[tuple[str, str]] = [
        ("ttk_id", page["ttk_id"]), ("spl_id", TK_FLOW_ID), ("sfqxtj", "0"), ("bdlb", ""),
        ("yylb", ""), ("tkyy", reason), ("tksm", note), ("sfyxsgt", "1"), ("jxnr", ""), ("xskcd", ""),
    ]
    parts: list[Any] = _multipart(fields)
    if attachment:
        name, content = attachment
        parts.append(("myFile", (name, content, ZF_ATTACHMENT_MIME.get(Path(name).suffix.lower(), "application/octet-stream"))))
    else:
        parts.append(("myFile", ("", b"", "application/octet-stream")))
    return parts


def _application_header_state(page: dict[str, Any]) -> dict[str, Any] | None:
    rows = page.get("draft_details") or []
    if not rows:
        return None
    first = rows[0]
    return {"reason": str(first.get("tkyy") or "").strip(), "has_attachment": str(first.get("tksmfjm") or "") == "1"}


async def _sync_application(client: httpx.AsyncClient, page: dict[str, Any], teacher_id: int,
                            class_drafts: list[dict[str, Any]]) -> dict[str, Any]:
    """Write 调动原因/备注/附件 onto the open draft application, then read it back.

    Returns ``{"status": synced|unverified|skipped|failed, "message": ...}`` and never raises, so a
    header hiccup cannot undo details that 教务 already saved."""
    previous = _application_header_state(page) or {}
    reason, note = build_application_reason(class_drafts, previous.get("reason", ""))
    if not reason:
        return {"status": "skipped", "message": "未填写调动原因，请在教务补填后再提交。"}
    try:
        attachment, warning = build_application_attachment(teacher_id, class_drafts)
        await _request(client, "POST", UPDATE_REASON_PATH, label="保存教务调动原因",
                       files=_application_form(page, reason, note, attachment), headers=_headers())
        fresh = await open_form_page(client, jxb_id=page["jxb_id"], xnm=page["xnm"], xqm=page["xqm"])
    except (DraftPushError, OSError, zipfile.BadZipFile) as exc:
        return {"status": "failed", "message": f"调动原因/附件未能写入教务（{exc}），请在教务补填。"}
    except Exception:
        logger.exception("Application header sync failed for teacher %s", teacher_id)
        return {"status": "failed", "message": "调动原因/附件写入时发生异常，请在教务补填。"}
    state = _application_header_state(fresh)
    sent = "调动原因" + ("和附件" if attachment else "")
    if fresh["ttk_id"] != page["ttk_id"] or state is None:
        return {"status": "unverified", "message": f"已发送{sent}，但教务未回显，提交前请在教务核对。{warning}"}
    if not state["reason"] or (attachment and not state["has_attachment"]):
        missing = "附件" if state["reason"] else "调动原因"
        return {"status": "failed", "message": f"教务未保存{missing}，请在教务补填后再提交。"}
    if _normalized(state["reason"]) != _normalized(reason):
        return {"status": "unverified", "message": f"已写入{sent}，但教务回显的原因与发送内容不完全一致，提交前请在教务核对。{warning}"}
    return {"status": "synced", "message": f"{sent}已写入教务草稿。{warning}"}


async def _check_one(client: httpx.AsyncClient, page: dict[str, Any], draft: dict[str, Any]) -> dict[str, Any]:
    """Read-only dry run of ``_save_one``: the same conflict check 教务 performs before saving, nothing saved."""
    slot = find_original_slot(page["slots"], draft["original"])
    if slot is None:
        return {"status": "failed", "message": "教务正式课表中未找到该原课次（可能已被调整或本地课表过期），请先同步教务课表。"}
    _reject_unverified_existing_detail(page, draft)
    check = await _request(client, "POST", CONFLICT_CHECK_PATH, label="教务冲突检测", files=_multipart(build_detail_form(page, slot, draft)), headers=_headers())
    conflict = conflict_outcome(_json_or_none(check))
    if not conflict["conflict_num"]:
        return {"status": "ok", "message": "教务未检测到冲突，可以保存。", "conflict": conflict}
    return {"status": "hard" if conflict["hard"] else "conflict", "message": conflict["message"], "conflict": conflict}


async def check_drafts_conflicts(teacher_id: int, *, year: str, term: str, draft_ids: list[int] | None = None) -> dict[str, Any]:
    """提前预测：run 教务's own conflict check for pending drafts without saving anything.

    Results are recorded on each draft (``availability_json.zf_precheck``) so the editor can show
    the verdict on cards and in the drawer before the teacher decides to save.
    """
    teacher_id = int(teacher_id)
    identity = identity_from_year_term(year, term)
    if identity is None:
        return {"status": "invalid_semester", "message": "学年学期无效。", "results": []}
    xnm, xqm = identity.as_xnm_xqm()
    with get_db_connection() as conn:
        credential = load_teacher_academic_access_method(conn, teacher_id, school_code="gxufl")
        drafts = list_drafts(conn, teacher_id, year, term)
    if not credential:
        return {"status": "missing_credential", "message": "尚未配置教务账号，无法提前检测冲突；保存前请先在教务系统对接设置中验证账号。", "results": []}
    wanted = {int(d) for d in draft_ids or []}
    targets = [d for d in drafts if d["status"] in ("draft", "conflict", "failed") and (not wanted or d["id"] in wanted)]
    if not targets:
        return {"status": "nothing", "message": "没有待检测的变更。", "results": []}
    results: list[dict[str, Any]] = []
    try:
        async with open_authenticated_academic_client(credential) as (client, profile, _login):
            if profile.school_code != "gxufl":
                raise DraftPushError("当前学校尚未启用调停课草稿保存。")
            await _request(client, "GET", ENTRY_PATH, label="打开教务调停课页面", headers=_headers(html=True))
            pages: dict[str, dict[str, Any]] = {}
            for draft in targets:
                jxb_id = draft["teaching_class_id"]
                try:
                    if jxb_id not in pages:
                        pages[jxb_id] = await open_form_page(client, jxb_id=jxb_id, xnm=xnm, xqm=xqm)
                    outcome = await _check_one(client, pages[jxb_id], draft)
                except DraftPushError as exc:
                    outcome = {"status": "failed", "message": str(exc)}
                results.append({"draft_id": draft["id"], "teaching_class_id": jxb_id, "course_name": draft["course_name"],
                                "original_label": describe_slot(draft["original"]), "proposed_label": describe_slot(draft["proposed"]), **outcome})
    except (ValueError, httpx.HTTPError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else "教务系统访问失败，暂时无法提前检测冲突。"
        return {"status": "failed", "message": message, "results": results}
    except Exception:
        logger.exception("Schedule draft pre-check failed for teacher %s", teacher_id)
        return {"status": "failed", "message": "检测冲突时发生未知错误，请稍后重试。", "results": results}
    from .schedule_editor_service import record_draft_precheck

    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    try:
        with get_db_connection() as conn:
            for item in results:
                record_draft_precheck(conn, teacher_id, item["draft_id"], {"checked_at": stamp, "status": item["status"], "message": item.get("message", ""),
                                                                          **{k: v for k, v in (item.get("conflict") or {}).items()}})
            conn.commit()
    except Exception:
        # The 教务 round-trips already succeeded; a persistence hiccup must not turn them into a 500.
        logger.exception("Schedule draft pre-check could not be recorded for teacher %s", teacher_id)
    ok = sum(1 for r in results if r["status"] in ("ok", "already"))
    soft = sum(1 for r in results if r["status"] == "conflict")
    hard = sum(1 for r in results if r["status"] == "hard")
    failed = len(results) - ok - soft - hard
    parts = [f"{ok} 项无冲突", f"{soft} 项有可强制保存的冲突" if soft else "", f"{hard} 项不能保存" if hard else "", f"{failed} 项检测失败" if failed else ""]
    return {"status": "success", "results": results, "ok": ok, "conflicts": soft, "hard": hard, "failed": failed,
            "message": "，".join(part for part in parts if part) + "。"}


async def _sync_pushed_applications(client: httpx.AsyncClient, teacher_id: int, pages: dict[str, dict[str, Any]],
                                    drafts: list[dict[str, Any]], results: list[dict[str, Any]]) -> None:
    """After details are saved, write each touched application's 原因/附件 (covering earlier saved
    details of the same application too) and annotate the per-draft results."""
    pushed_now = {item["draft_id"] for item in results if item["status"] == "pushed"}
    for jxb_id, page in pages.items():
        class_results = [item for item in results if item["teaching_class_id"] == jxb_id and item["draft_id"] in pushed_now]
        if not class_results:
            continue
        members = [d for d in drafts if d["teaching_class_id"] == jxb_id and (
            d["id"] in pushed_now or (d["status"] == "pushed" and d.get("remote_ttk_id") == page["ttk_id"]))]
        header = await _sync_application(client, page, teacher_id, members)
        for item in class_results:
            item["application"] = header
            item["message"] = f"{item['message']}{header['message']}"


async def sync_application_reasons(teacher_id: int, *, year: str, term: str) -> dict[str, Any]:
    """Re-send 原因/附件 for drafts already saved to 教务 (e.g. saved before this was supported,
    or edited afterwards). Only touches applications that are still unsubmitted drafts."""
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
    groups: dict[str, list[dict[str, Any]]] = {}
    for draft in drafts:
        if draft["status"] == "pushed" and draft.get("remote_ttk_id"):
            groups.setdefault(draft["teaching_class_id"], []).append(draft)
    if not groups:
        return {"status": "nothing", "message": "没有已保存到教务的草稿。", "results": []}
    results: list[dict[str, Any]] = []
    try:
        async with open_authenticated_academic_client(credential) as (client, profile, _login):
            if profile.school_code != "gxufl":
                raise DraftPushError("当前学校尚未启用调停课草稿保存。")
            await _request(client, "GET", ENTRY_PATH, label="打开教务调停课页面", headers=_headers(html=True))
            for jxb_id, members in groups.items():
                course = members[0]["course_name"]
                page = await open_form_page(client, jxb_id=jxb_id, xnm=xnm, xqm=xqm)
                members = [d for d in members if d["remote_ttk_id"] == page["ttk_id"]]
                if not members:
                    results.append({"teaching_class_id": jxb_id, "course_name": course, "status": "skipped",
                                    "message": "该申请已在教务提交或变化，原因/附件请在教务查看。"})
                    continue
                header = await _sync_application(client, page, teacher_id, members)
                results.append({"teaching_class_id": jxb_id, "course_name": course, **header})
    except (ValueError, httpx.HTTPError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else "教务系统访问失败。"
        return {"status": "failed", "message": message, "results": results}
    except Exception:
        logger.exception("Application reason re-sync failed for teacher %s", teacher_id)
        return {"status": "failed", "message": "写入调动原因时发生未知错误，请稍后重试。", "results": results}
    synced = sum(1 for r in results if r["status"] in ("synced", "unverified"))
    return {"status": "success" if synced == len(results) else ("partial" if synced else "failed"),
            "message": f"已为 {synced}/{len(results)} 个教务申请写入调动原因和材料。", "results": results}


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
    batch_error = ""
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
                results.append({"draft_id": draft["id"], "teaching_class_id": jxb_id, "course_name": draft["course_name"],
                                "original_label": describe_slot(draft["original"]), "proposed_label": describe_slot(draft["proposed"]), **outcome})
            await _sync_pushed_applications(client, teacher_id, pages, drafts, results)
    except (ValueError, httpx.HTTPError) as exc:
        batch_error = str(exc) if isinstance(exc, ValueError) else "教务系统访问中断，请核对尚未确认的保存结果。"
    except Exception:
        logger.exception("Schedule draft push failed for teacher %s", teacher_id)
        batch_error = "保存教务草稿时发生异常，请核对尚未确认的保存结果。"

    # A transport/context-manager failure can occur after earlier details were
    # saved. Persist their confirmed IDs before reporting the batch interruption;
    # never tell the user that nothing was saved or silently drop remaining rows.
    completed_ids = {item["draft_id"] for item in results}
    for draft in targets:
        if draft["id"] not in completed_ids:
            results.append({"draft_id": draft["id"], "teaching_class_id": draft["teaching_class_id"],
                            "course_name": draft["course_name"], "original_label": describe_slot(draft["original"]),
                            "proposed_label": describe_slot(draft["proposed"]), "status": "failed",
                            "message": batch_error + " 此项尚未确认，未标记为已保存。"})
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
    if batch_error:
        parts.append(batch_error)
    return {"status": "success" if pushed and not failed and not conflicts and not batch_error else ("partial" if pushed else "failed"),
            "message": "，".join(parts) + "。请登录教务系统核对后点击「提交申请」。", "results": results,
            "pushed": pushed, "conflicts": conflicts, "failed": failed, "batch_error": batch_error}


async def withdraw_draft_from_academic_system(teacher_id: int, draft_id: int) -> dict[str, Any]:
    """Delete one saved detail from the 教务 draft (撤回), then unlock locally."""
    teacher_id = int(teacher_id)
    with get_db_connection() as conn:
        credential = load_teacher_academic_access_method(conn, teacher_id, school_code="gxufl")
        draft = get_draft(conn, teacher_id, int(draft_id))
    if draft is None:
        return {"status": "not_found", "message": "草稿不存在。"}
    if draft["status"] != "pushed":
        return {"status": "nothing", "message": "该变更尚未保存到教务，无需撤回。"}
    if not draft["remote_ttk_id"] or not draft["remote_detail_id"]:
        return {"status": "failed", "message": "缺少原教务申请或明细标识，无法安全撤回，请到教务核对。"}
    if not credential:
        return {"status": "missing_credential", "message": "请先在教务系统对接设置中验证并保存账号。"}
    identity = identity_from_year_term(draft["year"], draft["term"])
    if identity is None:
        return {"status": "failed", "message": "草稿学年学期无效，无法撤回。"}
    xnm, xqm = identity.as_xnm_xqm()
    try:
        async with open_authenticated_academic_client(credential) as (client, profile, _login):
            if profile.school_code != "gxufl":
                raise DraftPushError("当前学校尚未启用调停课草稿撤回。")
            await _request(client, "GET", ENTRY_PATH, label="打开教务调停课页面", headers=_headers(html=True))
            page = await open_form_page(client, jxb_id=draft["teaching_class_id"], xnm=xnm, xqm=xqm)
            _verify_withdrawable_detail(page, draft)
            await _request(client, "POST", DELETE_DETAIL_PATH, label="撤回教务草稿",
                           data={"ttkxx_id": draft["remote_detail_id"]}, headers=_headers())
            page = await open_form_page(client, jxb_id=draft["teaching_class_id"], xnm=xnm, xqm=xqm)
            if page["ttk_id"] != draft["remote_ttk_id"]:
                raise DraftPushError("撤回后教务申请标识发生变化，未确认删除结果；本地继续保留已保存状态，请到教务核对。")
            if any(str(d.get("ttkxx_id")) == draft["remote_detail_id"] for d in page["existing_details"]):
                raise DraftPushError("教务未删除该草稿明细，可能该申请已提交，请登录教务系统处理。")
    except (ValueError, httpx.HTTPError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else "教务系统访问失败，未撤回草稿。"
        return {"status": "failed", "message": message}
    with get_db_connection() as conn:
        update_draft_remote_state(conn, draft["id"], status="draft", remote_message="已从教务草稿撤回，可继续修改。")
        conn.commit()
    return {"status": "success", "message": "已从教务草稿撤回，可继续修改后再次保存。"}
