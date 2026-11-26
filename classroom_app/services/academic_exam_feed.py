"""Read a complete current-term ZF exam grid before reconciling local records."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Protocol, TypeVar

import httpx

from .academic_classroom_sync_service import _ajax_headers
from .organization_scope_service import load_teacher_org_scope
from .semester_identity_service import zf_term_params_from_semester


class ExamRow(Protocol):
    academic_year: str
    academic_term: str
    course_name: str
    exam_name: str
    exam_time_text: str


Item = TypeVar("Item", bound=ExamRow)
MAX_PAGES = 100
EXAM_NAME_OPTIONS = "/ksglcommon/common_cxKsmcByXnxq.html"


def exam_snapshot_start() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def superseding_exam_snapshot(conn, *, teacher_id: int, semester: dict, term: dict,
                             source: str, started_at: str) -> dict | None:
    """Claim the validated read inside the same transaction as publication.

    A more recent successful empty grid also establishes a watermark. Failed
    reads never call this function and rolled-back writes roll back the claim.
    """
    tables = {"course_exam": "teacher_academic_course_exam_items", "invigilation": "teacher_academic_invigilation_items"}
    table = tables[source]
    watermark = f"academic_exam_feed:gxufl:{teacher_id}:{term['xnm']}:{term['xqm']}:{source}"
    claim = conn.execute(
        """INSERT INTO system_settings(key,value,updated_at) VALUES (?,?,CURRENT_TIMESTAMP)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
           WHERE system_settings.value < excluded.value""",
        (watermark, started_at),
    )
    if claim.rowcount:
        return None
    row = conn.execute(
        f"""SELECT COUNT(*) AS total FROM {table}
            WHERE teacher_id=? AND school_code='gxufl' AND academic_year=? AND academic_term=?
            AND sync_status='active'""", (teacher_id, term["xnm"], term["xqm"]),
    ).fetchone()
    return {
        "status": "success", "message": "已采用同时完成的较新教务同步结果。",
        "semester_id": int(semester["id"]), "semester_name": semester.get("name") or "",
        "snapshot_superseded": True, f"{source}_count": int(row["total"]),
        "created_count": 0, "updated_count": 0, "stale_count": 0, "event_created_count": 0,
        "event_updated_count": 0, "matched_offering_count": 0, "notification_count": 0,
        "student_notification_count": 0, "teacher_notification_count": 0,
    }


def load_current_exam_semester(conn, teacher_id: int, today: date) -> dict | None:
    school = load_teacher_org_scope(conn, teacher_id)["school_code"]
    row = conn.execute(
        """
        SELECT * FROM academic_semesters
        WHERE lower(TRIM(COALESCE(school_code, ?))) = lower(TRIM(?))
          AND date(start_date) <= date(?) AND date(end_date) >= date(?)
        ORDER BY CASE WHEN teacher_id = ? THEN 0 ELSE 1 END, updated_at DESC, id DESC
        LIMIT 1
        """,
        (school, school, today.isoformat(), today.isoformat(), int(teacher_id)),
    ).fetchone()
    return dict(row) if row else None


def parse_exam_time(value: Any) -> tuple[str, str, str, str]:
    text = " ".join(str(value or "").split())
    match = re.search(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})日?", text)
    if not match:
        return "", "", "", text
    try:
        day = date(*map(int, match.groups()))
    except ValueError:
        return "", "", "", text
    clock = re.search(r"(\d{1,2})[:：](\d{2})\s*[-~～—–－至]\s*(\d{1,2})[:：](\d{2})", text[match.end():])
    if not clock:
        return day.isoformat(), "", "", text
    try:
        start_hour, start_minute, end_hour, end_minute = map(int, clock.groups())
        start = datetime(day.year, day.month, day.day, start_hour, start_minute)
        end = start.replace(hour=end_hour, minute=end_minute)
    except ValueError:
        return day.isoformat(), "", "", text
    if end < start:
        end += timedelta(days=1)
    return day.isoformat(), start.isoformat(timespec="minutes"), end.isoformat(timespec="minutes"), text


def _page_integer(value: Any) -> int:
    if not re.fullmatch(r"[0-9]{1,10}", str(value)):
        raise ValueError("教务系统返回的考试分页信息无效。")
    return int(value)


def _read_page(payload: Any, requested_page: int, page_size: int) -> tuple[list, int, int]:
    if not isinstance(payload, dict):
        raise ValueError("教务系统未返回有效考试列表。")
    if str(payload.get("success")).lower() == "false" or str(payload.get("status")).lower() in {"error", "failed", "fail"}:
        raise ValueError("教务系统未返回有效考试列表，请检查教务账号后重试。")
    rows = next((payload[key] for key in ("items", "rows", "data") if isinstance(payload.get(key), list)), None)
    if rows is None or len(rows) > page_size or any(not isinstance(row, dict) for row in rows):
        raise ValueError("教务系统返回的考试列表格式无效。")
    total = _page_integer(next((payload[key] for key in ("totalResult", "totalCount", "records") if key in payload), None))
    pages = _page_integer(payload.get("totalPage", payload.get("total_page", (total + page_size - 1) // page_size)))
    current = _page_integer(payload.get("currentPage", requested_page))
    if current != requested_page or pages > MAX_PAGES or (not total and (rows or pages > 1)):
        raise ValueError("教务系统返回的考试页码与查询不符。")
    if total and (not rows or pages < requested_page or pages > total or total > pages * page_size):
        raise ValueError("教务系统返回的考试分页不完整。")
    return rows, pages, total


async def fetch_exam_feed(
    client: httpx.AsyncClient,
    semester: dict,
    *,
    index_path: str,
    query_path: str,
    parser: str,
    build_form: Callable[..., dict],
    parse_row: Callable[..., Item],
    item_key: Callable[[Item], str],
) -> tuple[list[Item], list[dict], list[dict[str, str]]]:
    params = zf_term_params_from_semester(semester)
    if not params:
        raise ValueError("当前学期缺少有效的教务学年学期编码。")
    headers = _ajax_headers(client, referer_path=index_path)
    sources: list[dict] = []
    # Keep the menu and option requests used by the ZF page. Only the grid
    # establishes an authoritative snapshot; a warmup failure alone does not.
    for path, method, kind in ((index_path, "GET", "index_page"), (EXAM_NAME_OPTIONS, "POST", "exam_name_options")):
        try:
            if method == "GET":
                response = await client.get(path, headers={"Accept": "text/html,*/*;q=0.8"})
            else:
                response = await client.post(path, data=params, headers=headers)
            sources.append({"path": path, "method": method, "parser": kind, "status_code": response.status_code})
        except httpx.HTTPError:
            sources.append({"path": path, "method": method, "parser": kind, "status": "failed"})

    items: list[Item] = []
    seen: set[str] = set()
    expected: tuple[int, int] | None = None
    for page in range(1, MAX_PAGES + 1):
        form = build_form(params, page=page)
        response = await client.post(query_path, data=form, headers=headers)
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise ValueError("教务系统未返回考试查询数据，请检查登录状态。") from exc
        rows, pages, total = _read_page(payload, page, int(form["queryModel.showCount"]))
        if expected is not None and expected != (pages, total):
            raise ValueError("教务考试列表在翻页期间已变动，请重新同步。")
        expected = pages, total
        for row in rows:
            item = parse_row(row, term_params=params, source_url=str(response.url))
            if (item.academic_year, item.academic_term) != (params["xnm"], params["xqm"]):
                raise ValueError("教务系统返回了其他学期的考试记录。")
            if not (item.course_name or item.exam_name or item.exam_time_text):
                raise ValueError("教务系统返回了无法识别的考试记录。")
            key = item_key(item)
            if key in seen:
                raise ValueError("教务系统返回了重复考试记录或重复分页。")
            seen.add(key)
            items.append(item)
        sources.append({
            "path": query_path, "method": "POST", "parser": parser, "params": dict(params),
            "status_code": response.status_code, "url": str(response.url), "page": page,
            "total_page": pages, "total_result": total, "item_count": len(rows),
        })
        if page >= pages:
            if len(items) != total:
                raise ValueError("教务考试明细数量与总记录数不符，请重新同步。")
            return items, sources, [params]
    raise ValueError("教务考试分页超过读取上限。")
