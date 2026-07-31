"""Low-frequency GXUFL teaching-evaluation synchronization.

The browser investigation for this adapter established the real ZFSoft V9
contract behind ``教学质量评价查询``.  Runtime code uses the existing encrypted
academic credential/session boundary, serializes every source request, mirrors
only teacher-visible read data, and serves the dashboard exclusively from the
local database.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import unicodedata
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Iterable

import httpx

from ..core import ai_client
from ..database import get_db_connection
from ..db.schema_academic_evaluations import ensure_academic_evaluation_schema
from .academic_integration_service import (
    load_teacher_academic_access_method,
    open_authenticated_academic_client,
)
from .academic_service import china_now
from .ai_gateway_service import ai_gateway_post
from .semester_identity_service import current_identity, infer_identity_from_dates


SCHOOL_CODE = "gxufl"
EVALUATION_TARGET_TEACHER = "01"
AUTO_REFRESH_SECONDS = 24 * 60 * 60
MANUAL_REFRESH_SECONDS = 6 * 60 * 60
LEASE_SECONDS = 12 * 60
REQUEST_DELAY_MIN_SECONDS = 0.75
REQUEST_DELAY_MAX_SECONDS = 1.25
MAX_SOURCE_COURSES = 24
MAX_HOUR_TYPES_PER_COURSE = 4
MAX_METRICS_PER_EVALUATION = 80
MAX_COMMENTS_PER_EVALUATION = 500

ZF_EVALUATION_INDEX_PATH = (
    "/jxpjtj/jxpjtj_cxXspjjstjIndex.html?gnmkdm=N305025&layout=default"
)
ZF_EVALUATION_COURSE_LIST_PATH = "/jxpjtj/jxpjtj_cxKcxxList.html"
ZF_EVALUATION_HOUR_LIST_PATH = "/jxpjtj/jxpjtj_cxKcxsxxList.html"
ZF_EVALUATION_OPEN_PATH = "/jxpjtj/jxpjtj_cxXysfkf.html?gnmkdm=N305025"
ZF_EVALUATION_SUMMARY_PATH = "/jxpjtj/jxpjtj_cxXspjjsxxMap.html?gnmkdm=N305025"
ZF_EVALUATION_GRADE_ITEMS_PATH = "/jxpjtj/jxpjtj_cxXspjjsDjXmList.html?gnmkdm=N305025"
ZF_EVALUATION_METRICS_PATH = "/jxpjtj/jxpjtj_cxXspjjsxxList.html?gnmkdm=N305025"
ZF_EVALUATION_COMMENTS_PATH = "/jxpjtj/jxpjtj_cxXspy.html?gnmkdm=N305025"


def _mapping(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any) -> float | None:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _normalize_course_name(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    return re.sub(r"[\s·•,，。:：;；()（）\[\]【】_-]+", "", text)


def _clean_text(value: Any, *, limit: int = 2000) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _now_iso() -> str:
    return china_now().isoformat()


def _iso_after(seconds: int) -> str:
    return (china_now() + timedelta(seconds=max(0, int(seconds)))).isoformat()


def _parse_iso(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=china_now().tzinfo)
    return parsed


def _payload_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("items", "rows", "data", "list"):
            value = payload.get(key)
            if isinstance(value, list):
                return [dict(item) for item in value if isinstance(item, dict)]
    return []


def _looks_like_login_response(response: httpx.Response) -> bool:
    location = str(response.headers.get("location") or "").lower()
    text = str(response.text or "").lower()
    return (
        response.status_code in {301, 302, 303, 307, 308}
        and "login" in location
    ) or ("login_slogin.html" in text and 'name="yhm"' in text)


class _SerialAcademicReader:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.last_request_at = 0.0
        self.source_summary: list[dict[str, Any]] = []

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        expect_json: bool = True,
    ) -> Any:
        loop = asyncio.get_running_loop()
        if self.last_request_at:
            minimum_delay = random.uniform(
                REQUEST_DELAY_MIN_SECONDS,
                REQUEST_DELAY_MAX_SECONDS,
            )
            remaining = minimum_delay - (loop.time() - self.last_request_at)
            if remaining > 0:
                await asyncio.sleep(remaining)

        headers = {
            "Accept": (
                "application/json,text/javascript,*/*;q=0.8"
                if expect_json
                else "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            ),
            "X-Requested-With": "XMLHttpRequest",
            "Referer": str(self.client.base_url).rstrip("/") + ZF_EVALUATION_INDEX_PATH,
        }
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded;charset=UTF-8"
            headers["Origin"] = str(self.client.base_url).rstrip("/")

        response = await self.client.request(
            method.upper(),
            path,
            params=params,
            data=data,
            headers=headers,
        )
        self.last_request_at = loop.time()
        if _looks_like_login_response(response):
            raise ValueError("教务系统登录会话已失效，请重新验证教务账号。")
        response.raise_for_status()

        parser = "html"
        result: Any = response.text
        if expect_json:
            parser = "json"
            try:
                result = response.json()
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValueError(f"教务评价接口未返回有效 JSON：{path}") from exc

        item_count = len(_payload_items(result)) if expect_json else 0
        self.source_summary.append(
            {
                "path": path.split("?", 1)[0],
                "method": method.upper(),
                "status_code": int(response.status_code),
                "parser": parser,
                "item_count": item_count,
            }
        )
        return result


def _current_semester_row(conn: Any, teacher_id: int, today: date) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT *
        FROM academic_semesters
        WHERE teacher_id = ?
          AND date(start_date) <= date(?)
          AND date(end_date) >= date(?)
        ORDER BY date(start_date) DESC, id DESC
        LIMIT 1
        """,
        (int(teacher_id), today.isoformat(), today.isoformat()),
    ).fetchone()
    if row is not None:
        return _mapping(row)
    row = conn.execute(
        """
        SELECT *
        FROM academic_semesters
        WHERE teacher_id = ?
        ORDER BY date(start_date) DESC, id DESC
        LIMIT 1
        """,
        (int(teacher_id),),
    ).fetchone()
    return _mapping(row) if row is not None else None


def _semester_contract(semester: dict[str, Any]) -> dict[str, str]:
    identity = infer_identity_from_dates(
        semester.get("start_date"),
        name=semester.get("name"),
    ) or current_identity(china_now().date())
    return {
        "xnm": str(identity.start_year),
        "xqm": "12" if int(identity.term) == 2 else "3",
        "academic_year": f"{identity.start_year}-{identity.start_year + 1}",
        "academic_term": str(identity.term),
    }


def _sync_state_row(
    conn: Any,
    *,
    teacher_id: int,
    academic_year: str,
    academic_term: str,
) -> dict[str, Any]:
    ensure_academic_evaluation_schema(conn)
    row = conn.execute(
        """
        SELECT *
        FROM teacher_academic_evaluation_sync_state
        WHERE teacher_id = ? AND school_code = ?
          AND academic_year = ? AND academic_term = ?
        LIMIT 1
        """,
        (int(teacher_id), SCHOOL_CODE, academic_year, academic_term),
    ).fetchone()
    return _mapping(row)


def _acquire_sync_lease(
    conn: Any,
    *,
    teacher_id: int,
    semester_id: int | None,
    academic_year: str,
    academic_term: str,
    force: bool,
) -> tuple[str, dict[str, Any]]:
    ensure_academic_evaluation_schema(conn)
    now = china_now()
    now_iso = now.isoformat()
    token = uuid.uuid4().hex
    conn.execute(
        """
        INSERT INTO teacher_academic_evaluation_sync_state (
            teacher_id, semester_id, school_code, academic_year, academic_term,
            status, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, 'idle', ?, ?)
        ON CONFLICT(teacher_id, school_code, academic_year, academic_term) DO NOTHING
        """,
        (
            int(teacher_id),
            semester_id,
            SCHOOL_CODE,
            academic_year,
            academic_term,
            now_iso,
            now_iso,
        ),
    )

    eligibility_sql = "(next_allowed_at IS NULL OR next_allowed_at <= ?)"
    eligibility_value = now_iso
    if force:
        eligibility_sql = "(completed_at IS NULL OR completed_at <= ?)"
        eligibility_value = (now - timedelta(seconds=MANUAL_REFRESH_SECONDS)).isoformat()

    cursor = conn.execute(
        f"""
        UPDATE teacher_academic_evaluation_sync_state
        SET semester_id = ?, status = 'running', last_error = '',
            attempt_started_at = ?, lease_token = ?, lease_expires_at = ?,
            updated_at = ?
        WHERE teacher_id = ? AND school_code = ?
          AND academic_year = ? AND academic_term = ?
          AND (status <> 'running' OR lease_expires_at IS NULL OR lease_expires_at <= ?)
          AND {eligibility_sql}
        """,
        (
            semester_id,
            now_iso,
            token,
            (now + timedelta(seconds=LEASE_SECONDS)).isoformat(),
            now_iso,
            int(teacher_id),
            SCHOOL_CODE,
            academic_year,
            academic_term,
            now_iso,
            eligibility_value,
        ),
    )
    state = _sync_state_row(
        conn,
        teacher_id=teacher_id,
        academic_year=academic_year,
        academic_term=academic_term,
    )
    if int(cursor.rowcount or 0) == 1:
        state["lease_token"] = token
        return "acquired", state
    if str(state.get("status") or "") == "running" and (
        _parse_iso(state.get("lease_expires_at")) or now
    ) > now:
        return "running", state
    return "cooldown", state


def _finish_sync_lease(
    conn: Any,
    *,
    teacher_id: int,
    academic_year: str,
    academic_term: str,
    lease_token: str,
    status: str,
    source_course_count: int,
    synced_evaluation_count: int,
    error: str = "",
) -> None:
    now_iso = _now_iso()
    retry_seconds = AUTO_REFRESH_SECONDS if status in {"success", "partial_success", "no_data"} else MANUAL_REFRESH_SECONDS
    conn.execute(
        """
        UPDATE teacher_academic_evaluation_sync_state
        SET status = ?, source_course_count = ?, synced_evaluation_count = ?,
            last_error = ?, completed_at = ?, next_allowed_at = ?,
            lease_token = '', lease_expires_at = NULL, updated_at = ?
        WHERE teacher_id = ? AND school_code = ?
          AND academic_year = ? AND academic_term = ? AND lease_token = ?
        """,
        (
            status,
            max(0, int(source_course_count)),
            max(0, int(synced_evaluation_count)),
            _clean_text(error, limit=800),
            now_iso,
            _iso_after(retry_seconds),
            now_iso,
            int(teacher_id),
            SCHOOL_CODE,
            academic_year,
            academic_term,
            lease_token,
        ),
    )


def _course_from_payload(item: dict[str, Any]) -> dict[str, str]:
    return {
        "source_course_key": _clean_text(
            item.get("KCH_ID") or item.get("kch_id") or item.get("id"),
            limit=160,
        ),
        "course_name": _clean_text(
            item.get("KCMC") or item.get("kcmc") or item.get("course_name"),
            limit=240,
        ),
    }


def _hour_from_payload(item: dict[str, Any]) -> dict[str, str]:
    return {
        "hour_type_code": _clean_text(item.get("XSDM") or item.get("xsdm"), limit=60),
        "hour_type_name": _clean_text(item.get("XSMC") or item.get("xsmc"), limit=120),
    }


def _grade_definitions(payload: Any) -> list[dict[str, str]]:
    definitions: list[dict[str, str]] = []
    for item in _payload_items(payload)[:12]:
        key = _clean_text(item.get("PFDJDMXMB_ID") or item.get("id"), limit=160)
        label = _clean_text(item.get("XMMC") or item.get("xmmc"), limit=40)
        if key:
            definitions.append({"key": key, "label": label or f"等级{len(definitions) + 1}"})
    return definitions


def _metric_from_payload(
    item: dict[str, Any],
    *,
    grade_definitions: list[dict[str, str]],
    fallback_sequence: int,
) -> dict[str, Any]:
    sequence_no = _safe_int(item.get("hh"), fallback_sequence)
    grade_counts: dict[str, int] = {}
    for index, definition in enumerate(grade_definitions):
        count = _safe_int(item.get(f"rs_{index}"), 0)
        if count or item.get(f"rs_{index}") not in (None, ""):
            grade_counts[definition["label"]] = count
    return {
        "source_metric_key": _clean_text(
            item.get("pjzbxm_id") or item.get("id") or str(sequence_no),
            limit=160,
        ),
        "sequence_no": sequence_no,
        "metric_name": _clean_text(item.get("zbxmmc"), limit=500),
        "mean_score": _safe_float(item.get("dxjz")),
        "satisfaction_score": _safe_float(item.get("myd")),
        "weight_value": _safe_float(item.get("qzz")),
        "hour_type_name": _clean_text(item.get("xsmc"), limit=120),
        "grade_counts": grade_counts,
    }


def _comment_from_payload(item: dict[str, Any], fallback_sequence: int) -> dict[str, Any] | None:
    text = _clean_text(item.get("py") or item.get("comment"), limit=2000)
    if not text:
        return None
    sequence_no = _safe_int(item.get("xh"), fallback_sequence)
    source_key = _clean_text(item.get("row_id") or item.get("id") or str(sequence_no), limit=120)
    return {
        "source_comment_key": source_key or str(sequence_no),
        "sequence_no": sequence_no,
        "comment_text": text,
        "comment_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


async def _fetch_evaluations(
    client: httpx.AsyncClient,
    *,
    xnm: str,
    xqm: str,
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]], int]:
    reader = _SerialAcademicReader(client)
    warnings: list[str] = []
    await reader.request("GET", ZF_EVALUATION_INDEX_PATH, expect_json=False)
    open_payload = await reader.request(
        "POST",
        ZF_EVALUATION_OPEN_PATH,
        data={"xnm": xnm, "xqm": xqm},
    )
    if str(open_payload).strip().lower() not in {"1", "true", '"1"'}:
        return [], ["当前学年学期的教学评价尚未开放查询。"], reader.source_summary, 0

    course_payload = await reader.request(
        "GET",
        ZF_EVALUATION_COURSE_LIST_PATH,
        params={"xnm": xnm, "xqm": xqm},
    )
    courses = [
        course
        for course in (_course_from_payload(item) for item in _payload_items(course_payload))
        if course["source_course_key"] and course["course_name"]
    ][:MAX_SOURCE_COURSES]
    if not courses:
        return [], ["教务系统当前学期没有返回可查询的课程评价。"], reader.source_summary, 0

    evaluations: list[dict[str, Any]] = []
    grade_definitions: list[dict[str, str]] | None = None
    for course in courses:
        try:
            hour_payload = await reader.request(
                "GET",
                ZF_EVALUATION_HOUR_LIST_PATH,
                params={"xnm": xnm, "xqm": xqm, "kch_id": course["source_course_key"]},
            )
            hours = [
                hour
                for hour in (_hour_from_payload(item) for item in _payload_items(hour_payload))
                if hour["hour_type_code"] or hour["hour_type_name"]
            ][:MAX_HOUR_TYPES_PER_COURSE]
            if not hours:
                hours = [{"hour_type_code": "", "hour_type_name": ""}]

            if grade_definitions is None:
                grade_payload = await reader.request(
                    "POST",
                    ZF_EVALUATION_GRADE_ITEMS_PATH,
                    data={
                        "xnm": xnm,
                        "xqm": xqm,
                        "kch_id": course["source_course_key"],
                        "pjdxdm": EVALUATION_TARGET_TEACHER,
                    },
                )
                grade_definitions = _grade_definitions(grade_payload)

            for hour in hours:
                base_form = {
                    "xnm": xnm,
                    "xqm": xqm,
                    "kch_id": course["source_course_key"],
                    "xsdm": hour["hour_type_code"],
                    "pjdxdm": EVALUATION_TARGET_TEACHER,
                    "fxbj": "",
                }
                col_ids = ",".join(item["key"] for item in (grade_definitions or []))
                summary_payload = await reader.request(
                    "POST",
                    ZF_EVALUATION_SUMMARY_PATH,
                    data={**base_form, "col_ids": col_ids, "doType": "query", "flag": "1"},
                )
                metric_payload = await reader.request(
                    "POST",
                    ZF_EVALUATION_METRICS_PATH,
                    data={
                        **base_form,
                        "col_ids": col_ids,
                        "doType": "query",
                        "flag": "1",
                        "_search": "false",
                        "nd": str(int(china_now().timestamp() * 1000)),
                        "queryModel.showCount": "1000",
                        "queryModel.currentPage": "1",
                        "queryModel.sortName": " ",
                        "queryModel.sortOrder": "asc",
                        "time": "0",
                    },
                )
                comment_payload = await reader.request(
                    "POST",
                    ZF_EVALUATION_COMMENTS_PATH,
                    data={
                        "doType": "query",
                        "xnm": xnm,
                        "xqm": xqm,
                        "kch_id": course["source_course_key"],
                        "xsdm": hour["hour_type_code"],
                        "fxbj": "",
                        "_search": "false",
                        "nd": str(int(china_now().timestamp() * 1000)),
                        "queryModel.showCount": str(MAX_COMMENTS_PER_EVALUATION),
                        "queryModel.currentPage": "1",
                        "queryModel.sortName": " ",
                        "queryModel.sortOrder": "asc",
                        "time": "0",
                    },
                )

                summary = dict(summary_payload) if isinstance(summary_payload, dict) else {}
                metrics = [
                    _metric_from_payload(
                        item,
                        grade_definitions=grade_definitions or [],
                        fallback_sequence=index,
                    )
                    for index, item in enumerate(
                        _payload_items(metric_payload)[:MAX_METRICS_PER_EVALUATION],
                        start=1,
                    )
                ]
                comments = []
                for index, item in enumerate(
                    _payload_items(comment_payload)[:MAX_COMMENTS_PER_EVALUATION],
                    start=1,
                ):
                    parsed = _comment_from_payload(item, index)
                    if parsed is not None:
                        comments.append(parsed)

                if not summary and not metrics and not comments:
                    continue
                evaluations.append(
                    {
                        **course,
                        **hour,
                        "evaluation_target_code": EVALUATION_TARGET_TEACHER,
                        "campus_name": _clean_text(summary.get("xqumc"), limit=160),
                        "course_score": _safe_float(summary.get("kcpjf")),
                        "teacher_weighted_score": _safe_float(summary.get("jqpjf")),
                        "institution_percentile_score": _safe_float(summary.get("bfzpf")),
                        "academic_year_course_score": _safe_float(summary.get("xnxqkcpjf")),
                        # The source labels cprs as participating students, not
                        # total course enrolment. Keep the two concepts separate.
                        "enrolled_count": 0,
                        "response_count": _safe_int(summary.get("cprs")),
                        "valid_response_count": _safe_int(summary.get("jfrs")),
                        "institution_rank": _safe_int(summary.get("jgpm"), 0) or None,
                        "course_unit_rank": _safe_int(summary.get("kckkdwpm"), 0) or None,
                        "metrics": metrics,
                        "comments": comments,
                    }
                )
        except (httpx.HTTPError, ValueError) as exc:
            warnings.append(f"{course['course_name']}评价读取未完成：{str(exc)[:160]}")

    return evaluations, warnings, reader.source_summary, len(courses)


def _upsert_evaluation(
    conn: Any,
    *,
    teacher_id: int,
    semester_id: int | None,
    academic_year: str,
    academic_term: str,
    item: dict[str, Any],
    source_summary: list[dict[str, Any]],
    synced_at: str,
) -> int:
    ensure_academic_evaluation_schema(conn)
    conn.execute(
        """
        INSERT INTO teacher_academic_course_evaluations (
            teacher_id, semester_id, school_code, academic_year, academic_term,
            source_course_key, course_name, course_name_key,
            hour_type_code, hour_type_name, evaluation_target_code,
            campus_name, course_score, teacher_weighted_score,
            institution_percentile_score, academic_year_course_score,
            enrolled_count, response_count, valid_response_count,
            institution_rank, course_unit_rank, comment_count,
            source_summary_json, sync_status, synced_at, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
        ON CONFLICT(
            teacher_id, school_code, academic_year, academic_term,
            source_course_key, hour_type_code, evaluation_target_code
        ) DO UPDATE SET
            semester_id = excluded.semester_id,
            course_name = excluded.course_name,
            course_name_key = excluded.course_name_key,
            hour_type_name = excluded.hour_type_name,
            campus_name = excluded.campus_name,
            course_score = excluded.course_score,
            teacher_weighted_score = excluded.teacher_weighted_score,
            institution_percentile_score = excluded.institution_percentile_score,
            academic_year_course_score = excluded.academic_year_course_score,
            enrolled_count = excluded.enrolled_count,
            response_count = excluded.response_count,
            valid_response_count = excluded.valid_response_count,
            institution_rank = excluded.institution_rank,
            course_unit_rank = excluded.course_unit_rank,
            comment_count = excluded.comment_count,
            source_summary_json = excluded.source_summary_json,
            sync_status = 'active',
            synced_at = excluded.synced_at,
            updated_at = excluded.updated_at
        """,
        (
            int(teacher_id),
            semester_id,
            SCHOOL_CODE,
            academic_year,
            academic_term,
            item["source_course_key"],
            item["course_name"],
            _normalize_course_name(item["course_name"]),
            item.get("hour_type_code") or "",
            item.get("hour_type_name") or "",
            item.get("evaluation_target_code") or EVALUATION_TARGET_TEACHER,
            item.get("campus_name") or "",
            item.get("course_score"),
            item.get("teacher_weighted_score"),
            item.get("institution_percentile_score"),
            item.get("academic_year_course_score"),
            max(0, _safe_int(item.get("enrolled_count"))),
            max(0, _safe_int(item.get("response_count"))),
            max(0, _safe_int(item.get("valid_response_count"))),
            item.get("institution_rank"),
            item.get("course_unit_rank"),
            len(item.get("comments") or []),
            json.dumps(source_summary, ensure_ascii=False, separators=(",", ":")),
            synced_at,
            synced_at,
            synced_at,
        ),
    )
    row = conn.execute(
        """
        SELECT id
        FROM teacher_academic_course_evaluations
        WHERE teacher_id = ? AND school_code = ?
          AND academic_year = ? AND academic_term = ?
          AND source_course_key = ? AND hour_type_code = ?
          AND evaluation_target_code = ?
        LIMIT 1
        """,
        (
            int(teacher_id),
            SCHOOL_CODE,
            academic_year,
            academic_term,
            item["source_course_key"],
            item.get("hour_type_code") or "",
            item.get("evaluation_target_code") or EVALUATION_TARGET_TEACHER,
        ),
    ).fetchone()
    evaluation_id = int(row["id"])
    conn.execute(
        "DELETE FROM teacher_academic_course_evaluation_metrics WHERE evaluation_id = ?",
        (evaluation_id,),
    )
    conn.execute(
        "DELETE FROM teacher_academic_course_evaluation_comments WHERE evaluation_id = ?",
        (evaluation_id,),
    )
    for metric in item.get("metrics") or []:
        conn.execute(
            """
            INSERT INTO teacher_academic_course_evaluation_metrics (
                evaluation_id, source_metric_key, sequence_no, metric_name,
                mean_score, satisfaction_score, weight_value, hour_type_name,
                grade_counts_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evaluation_id,
                metric.get("source_metric_key") or str(metric.get("sequence_no") or ""),
                _safe_int(metric.get("sequence_no")),
                metric.get("metric_name") or "",
                metric.get("mean_score"),
                metric.get("satisfaction_score"),
                metric.get("weight_value"),
                metric.get("hour_type_name") or "",
                json.dumps(metric.get("grade_counts") or {}, ensure_ascii=False, separators=(",", ":")),
                synced_at,
                synced_at,
            ),
        )
    for comment in item.get("comments") or []:
        conn.execute(
            """
            INSERT INTO teacher_academic_course_evaluation_comments (
                evaluation_id, source_comment_key, sequence_no,
                comment_text, comment_hash, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                evaluation_id,
                comment.get("source_comment_key") or str(comment.get("sequence_no") or ""),
                _safe_int(comment.get("sequence_no")),
                comment.get("comment_text") or "",
                comment.get("comment_hash") or "",
                synced_at,
            ),
        )
    return evaluation_id


def _extract_ai_json(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    for key in ("response_json", "json", "data"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                return parsed
    text = str(payload.get("response_text") or "").strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _normalize_ai_keywords(payload: dict[str, Any] | None) -> tuple[str, list[dict[str, Any]]]:
    data = payload or {}
    summary = _clean_text(data.get("summary"), limit=100)
    raw_keywords = data.get("keywords") if isinstance(data.get("keywords"), list) else []
    keywords: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_keywords:
        item = raw if isinstance(raw, dict) else {"label": raw}
        label = _clean_text(item.get("label") or item.get("keyword"), limit=16)
        key = label.casefold()
        if not label or key in seen:
            continue
        seen.add(key)
        sentiment = str(item.get("sentiment") or "neutral").strip().lower()
        if sentiment not in {"positive", "improvement", "neutral"}:
            sentiment = "neutral"
        confidence = _safe_float(item.get("confidence"))
        keywords.append(
            {
                "label": label,
                "sentiment": sentiment,
                "count": max(1, _safe_int(item.get("count"), 1)),
                "confidence": round(max(0.0, min(1.0, confidence if confidence is not None else 0.7)), 2),
            }
        )
        if len(keywords) >= 8:
            break
    return summary, keywords


async def _ai_keyword_summary(
    *,
    teacher_id: int,
    course_name: str,
    comments: list[str],
    source_hash: str,
) -> tuple[str, list[dict[str, Any]], str]:
    useful_comments = [_clean_text(item, limit=500) for item in comments if _clean_text(item, limit=500)]
    prompt_comments = useful_comments[:160]
    payload = {
        "system_prompt": (
            "你是教学评价文本分析助手。只分析匿名学生评语，不推断学生身份。"
            "过滤‘好’‘无’等信息量极低文本，合并同义表达；既保留优势，也如实保留改进建议。"
            "仅输出JSON：summary为不超过60字的教师可读结论；keywords为3到8项，"
            "每项包含label、sentiment(positive/improvement/neutral)、count、confidence(0到1)。"
        ),
        "messages": [],
        "new_message": json.dumps(
            {"course_name": course_name, "comment_count": len(useful_comments), "comments": prompt_comments},
            ensure_ascii=False,
        ),
        "file_texts": [],
        "model_capability": "standard",
        "task_type": "fast_text_response",
        "response_format": "json",
        "web_search_enabled": False,
        "task_priority": "background",
        "task_label": "academic-evaluation:keyword-summary",
    }
    response = await ai_gateway_post(
        ai_client,
        "/api/ai/chat",
        json_payload=payload,
        timeout=90.0,
        task_type="academic_evaluation_keyword_summary",
        priority="P2",
        teacher_id=int(teacher_id),
        source_ref=f"academic-evaluation:{source_hash}",
        metadata={"course_name": course_name, "comment_count": len(useful_comments)},
    )
    response.raise_for_status()
    response_payload = response.json()
    summary, keywords = _normalize_ai_keywords(_extract_ai_json(response_payload))
    if not keywords:
        raise ValueError("AI未返回有效评价关键词。")
    model = _clean_text(
        response_payload.get("model")
        or response_payload.get("model_used")
        or response_payload.get("model_name"),
        limit=120,
    )
    return summary, keywords, model


async def _refresh_ai_keywords(
    *,
    teacher_id: int,
    evaluations: list[dict[str, Any]],
    evaluation_ids: list[int],
) -> list[str]:
    warnings: list[str] = []
    grouped: dict[str, dict[str, Any]] = {}
    for item, evaluation_id in zip(evaluations, evaluation_ids):
        key = _normalize_course_name(item.get("course_name"))
        entry = grouped.setdefault(
            key,
            {"course_name": item.get("course_name") or "课程", "comments": [], "ids": []},
        )
        entry["ids"].append(int(evaluation_id))
        entry["comments"].extend(
            str(comment.get("comment_text") or "") for comment in (item.get("comments") or [])
        )

    for entry in grouped.values():
        comments = sorted({_clean_text(item, limit=2000) for item in entry["comments"] if _clean_text(item, limit=2000)})
        source_hash = hashlib.sha256("\n".join(comments).encode("utf-8")).hexdigest()
        placeholders = ",".join("?" for _ in entry["ids"])
        with get_db_connection() as conn:
            ensure_academic_evaluation_schema(conn)
            cached = conn.execute(
                f"""
                SELECT ai_summary, ai_keywords_json, ai_keyword_model
                FROM teacher_academic_course_evaluations
                WHERE id IN ({placeholders})
                  AND ai_keyword_status = 'completed'
                  AND ai_keyword_source_hash = ?
                LIMIT 1
                """,
                (*entry["ids"], source_hash),
            ).fetchone()
            if not comments:
                conn.execute(
                    f"""
                    UPDATE teacher_academic_course_evaluations
                    SET ai_summary = '', ai_keywords_json = '[]',
                        ai_keyword_status = 'no_comments', ai_keyword_error = '',
                        ai_keyword_source_hash = ?, ai_keyword_updated_at = ?, updated_at = ?
                    WHERE id IN ({placeholders})
                    """,
                    (source_hash, _now_iso(), _now_iso(), *entry["ids"]),
                )
                conn.commit()
                continue
            if cached is not None:
                conn.execute(
                    f"""
                    UPDATE teacher_academic_course_evaluations
                    SET ai_summary = ?, ai_keywords_json = ?, ai_keyword_status = 'completed',
                        ai_keyword_model = ?, ai_keyword_error = '',
                        ai_keyword_source_hash = ?, ai_keyword_updated_at = ?, updated_at = ?
                    WHERE id IN ({placeholders})
                    """,
                    (
                        cached["ai_summary"],
                        cached["ai_keywords_json"],
                        cached["ai_keyword_model"],
                        source_hash,
                        _now_iso(),
                        _now_iso(),
                        *entry["ids"],
                    ),
                )
                conn.commit()
                continue
            conn.execute(
                f"""
                UPDATE teacher_academic_course_evaluations
                SET ai_keyword_status = 'running', ai_keyword_error = '',
                    ai_keyword_source_hash = ?, updated_at = ?
                WHERE id IN ({placeholders})
                """,
                (source_hash, _now_iso(), *entry["ids"]),
            )
            conn.commit()

        try:
            summary, keywords, model = await _ai_keyword_summary(
                teacher_id=teacher_id,
                course_name=str(entry["course_name"]),
                comments=comments,
                source_hash=source_hash,
            )
            with get_db_connection() as conn:
                conn.execute(
                    f"""
                    UPDATE teacher_academic_course_evaluations
                    SET ai_summary = ?, ai_keywords_json = ?, ai_keyword_status = 'completed',
                        ai_keyword_model = ?, ai_keyword_error = '',
                        ai_keyword_source_hash = ?, ai_keyword_updated_at = ?, updated_at = ?
                    WHERE id IN ({placeholders})
                    """,
                    (
                        summary,
                        json.dumps(keywords, ensure_ascii=False, separators=(",", ":")),
                        model,
                        source_hash,
                        _now_iso(),
                        _now_iso(),
                        *entry["ids"],
                    ),
                )
                conn.commit()
        except Exception as exc:  # noqa: BLE001 - score data remains useful without AI.
            error = f"{type(exc).__name__}: {str(exc)[:240]}"
            warnings.append(f"{entry['course_name']}评语关键词暂未生成：{str(exc)[:120]}")
            with get_db_connection() as conn:
                conn.execute(
                    f"""
                    UPDATE teacher_academic_course_evaluations
                    SET ai_keyword_status = 'failed', ai_keyword_error = ?,
                        ai_keyword_source_hash = ?, ai_keyword_updated_at = ?, updated_at = ?
                    WHERE id IN ({placeholders})
                    """,
                    (error, source_hash, _now_iso(), _now_iso(), *entry["ids"]),
                )
                conn.commit()
    return warnings


async def sync_current_teacher_academic_evaluations(
    teacher_id: int,
    *,
    force: bool = False,
) -> dict[str, Any]:
    teacher_id = int(teacher_id)
    with get_db_connection() as conn:
        ensure_academic_evaluation_schema(conn)
        access_payload = load_teacher_academic_access_method(conn, teacher_id, school_code=SCHOOL_CODE)
        semester = _current_semester_row(conn, teacher_id, china_now().date())

    if not access_payload:
        return {
            "status": "missing_credential",
            "message": "请先在系统设置中配置并验证教务系统账号，再同步教学评价。",
        }
    if not semester:
        return {
            "status": "no_current_semester",
            "message": "未找到当前学期设置，暂不能对齐课堂评价。",
        }

    term = _semester_contract(semester)
    with get_db_connection() as conn:
        lease_status, state = _acquire_sync_lease(
            conn,
            teacher_id=teacher_id,
            semester_id=int(semester["id"]) if semester.get("id") else None,
            academic_year=term["academic_year"],
            academic_term=term["academic_term"],
            force=bool(force),
        )
        conn.commit()

    if lease_status == "running":
        return {
            "status": "already_running",
            "message": "教学评价正在后台同步，本次未重复访问教务系统。",
            "sync": _public_sync_state(state),
        }
    if lease_status == "cooldown":
        return {
            "status": "fresh",
            "message": "教学评价仍在低频保护期内，已直接使用最近一次同步结果。",
            "sync": _public_sync_state(state),
        }

    lease_token = str(state.get("lease_token") or "")
    evaluations: list[dict[str, Any]] = []
    warnings: list[str] = []
    source_summary: list[dict[str, Any]] = []
    source_course_count = 0
    try:
        async with open_authenticated_academic_client(access_payload) as (client, profile, _login_result):
            evaluations, warnings, source_summary, source_course_count = await _fetch_evaluations(
                client,
                xnm=term["xnm"],
                xqm=term["xqm"],
            )

        synced_at = _now_iso()
        evaluation_ids: list[int] = []
        with get_db_connection() as conn:
            ensure_academic_evaluation_schema(conn)
            for item in evaluations:
                evaluation_ids.append(
                    _upsert_evaluation(
                        conn,
                        teacher_id=teacher_id,
                        semester_id=int(semester["id"]) if semester.get("id") else None,
                        academic_year=term["academic_year"],
                        academic_term=term["academic_term"],
                        item=item,
                        source_summary=source_summary,
                        synced_at=synced_at,
                    )
                )
            conn.commit()

        ai_warnings = await _refresh_ai_keywords(
            teacher_id=teacher_id,
            evaluations=evaluations,
            evaluation_ids=evaluation_ids,
        ) if evaluations else []
        warnings.extend(ai_warnings)
        status = "partial_success" if warnings and evaluations else ("success" if evaluations else "no_data")
        message = (
            f"已低频同步 {source_course_count} 门教务课程中的 {len(evaluations)} 组教学评价，"
            "课堂卡片与评价详情已更新。"
            if evaluations
            else "教务系统当前未返回已发布的教学评价，保留原有本地结果并等待下次低频同步。"
        )
        with get_db_connection() as conn:
            _finish_sync_lease(
                conn,
                teacher_id=teacher_id,
                academic_year=term["academic_year"],
                academic_term=term["academic_term"],
                lease_token=lease_token,
                status=status,
                source_course_count=source_course_count,
                synced_evaluation_count=len(evaluations),
                error="；".join(warnings[:5]),
            )
            conn.commit()
        return {
            "status": status,
            "message": message,
            "semester_id": int(semester["id"]),
            "semester_name": str(semester.get("name") or ""),
            "academic_year": term["academic_year"],
            "academic_term": term["academic_term"],
            "course_count": source_course_count,
            "evaluation_count": len(evaluations),
            "comment_count": sum(len(item.get("comments") or []) for item in evaluations),
            "warnings": warnings[:12],
            "source_summary": source_summary,
            "school_name": profile.school_name,
        }
    except Exception as exc:  # noqa: BLE001 - preserve cached data and release the lease.
        error = f"{type(exc).__name__}: {str(exc)[:400]}"
        with get_db_connection() as conn:
            _finish_sync_lease(
                conn,
                teacher_id=teacher_id,
                academic_year=term["academic_year"],
                academic_term=term["academic_term"],
                lease_token=lease_token,
                status="failed",
                source_course_count=source_course_count,
                synced_evaluation_count=len(evaluations),
                error=error,
            )
            conn.commit()
        return {
            "status": "failed",
            "message": f"教学评价同步未完成，已继续使用最近一次本地结果：{str(exc)[:180]}",
            "warnings": warnings[:12],
        }


def _weighted_average(rows: Iterable[dict[str, Any]], field: str) -> float | None:
    numerator = 0.0
    denominator = 0.0
    fallback: list[float] = []
    for row in rows:
        value = _safe_float(row.get(field))
        if value is None:
            continue
        fallback.append(value)
        weight = max(0, _safe_int(row.get("valid_response_count")))
        if weight:
            numerator += value * weight
            denominator += weight
    if denominator > 0:
        return numerator / denominator
    return sum(fallback) / len(fallback) if fallback else None


def _keyword_union(rows: Iterable[dict[str, Any]], *, limit: int = 8) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        for raw in _json_list(row.get("ai_keywords_json")):
            if not isinstance(raw, dict):
                continue
            label = _clean_text(raw.get("label"), limit=16)
            if not label:
                continue
            key = label.casefold()
            item = merged.setdefault(
                key,
                {
                    "label": label,
                    "sentiment": str(raw.get("sentiment") or "neutral"),
                    "count": 0,
                    "confidence": 0.0,
                },
            )
            item["count"] += max(1, _safe_int(raw.get("count"), 1))
            item["confidence"] = max(item["confidence"], _safe_float(raw.get("confidence")) or 0.0)
            if item["sentiment"] == "neutral" and raw.get("sentiment") in {"positive", "improvement"}:
                item["sentiment"] = raw.get("sentiment")
    return sorted(
        merged.values(),
        key=lambda item: (-int(item["count"]), -float(item["confidence"]), item["label"]),
    )[:limit]


def _freshness_label(value: Any) -> str:
    parsed = _parse_iso(value)
    if parsed is None:
        return "尚未同步"
    delta = china_now() - parsed.astimezone(china_now().tzinfo)
    if delta.total_seconds() < 3600:
        return "刚刚更新"
    if delta.total_seconds() < 86400:
        return f"{max(1, int(delta.total_seconds() // 3600))}小时前更新"
    return f"{max(1, int(delta.total_seconds() // 86400))}天前更新"


def _latest_term_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    latest = max(
        rows,
        key=lambda row: (
            str(row.get("academic_year") or ""),
            _safe_int(row.get("academic_term")),
            str(row.get("synced_at") or ""),
        ),
    )
    latest_key = (
        str(latest.get("academic_year") or ""),
        str(latest.get("academic_term") or ""),
    )
    return [
        row
        for row in rows
        if (
            str(row.get("academic_year") or ""),
            str(row.get("academic_term") or ""),
        ) == latest_key
    ]


def _evaluation_overview(rows: list[dict[str, Any]]) -> dict[str, Any]:
    score = _weighted_average(rows, "course_score")
    weighted_score = _weighted_average(rows, "teacher_weighted_score")
    enrolled_count = sum(max(0, _safe_int(row.get("enrolled_count"))) for row in rows)
    response_count = sum(max(0, _safe_int(row.get("response_count"))) for row in rows)
    valid_count = sum(max(0, _safe_int(row.get("valid_response_count"))) for row in rows)
    comment_count = sum(max(0, _safe_int(row.get("comment_count"))) for row in rows)
    last_synced_at = max((str(row.get("synced_at") or "") for row in rows), default="")
    summaries = [_clean_text(row.get("ai_summary"), limit=100) for row in rows if _clean_text(row.get("ai_summary"), limit=100)]
    return {
        "available": True,
        "score": round(score, 2) if score is not None else None,
        "score_display": f"{score:.1f}" if score is not None else "--",
        "weighted_score": round(weighted_score, 2) if weighted_score is not None else None,
        "enrolled_count": enrolled_count,
        "response_count": response_count,
        "valid_response_count": valid_count,
        "response_rate": round(valid_count * 100 / response_count, 1) if response_count else None,
        "comment_count": comment_count,
        "keywords": _keyword_union(rows, limit=4),
        "ai_summary": summaries[0] if summaries else "",
        "ai_keyword_status": (
            "completed"
            if any(str(row.get("ai_keyword_status") or "") == "completed" for row in rows)
            else str(rows[0].get("ai_keyword_status") or "pending")
        ),
        "source_count": len(rows),
        "last_synced_at": last_synced_at,
        "freshness_label": _freshness_label(last_synced_at),
    }


def _public_sync_state(state: dict[str, Any] | None) -> dict[str, Any]:
    row = state or {}
    return {
        "status": str(row.get("status") or "idle"),
        "source_course_count": _safe_int(row.get("source_course_count")),
        "synced_evaluation_count": _safe_int(row.get("synced_evaluation_count")),
        "last_error": _clean_text(row.get("last_error"), limit=400),
        "attempt_started_at": str(row.get("attempt_started_at") or ""),
        "completed_at": str(row.get("completed_at") or ""),
        "next_allowed_at": str(row.get("next_allowed_at") or ""),
        "freshness_label": _freshness_label(row.get("completed_at")),
    }


def build_teacher_academic_evaluation_dashboard_context(
    conn: Any,
    *,
    teacher_id: int,
    offerings: list[dict[str, Any]],
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    semester_ids = sorted(
        {int(item["semester_id"]) for item in offerings if item.get("semester_id")}
    )
    params: list[Any] = [int(teacher_id)]
    where = "teacher_id = ?"
    if semester_ids:
        where += f" AND semester_id IN ({','.join('?' for _ in semester_ids)})"
        params.extend(semester_ids)
    rows = [
        _mapping(row)
        for row in conn.execute(
            f"""
            SELECT *
            FROM teacher_academic_course_evaluations
            WHERE {where} AND sync_status IN ('active', 'stale')
            ORDER BY synced_at DESC, id DESC
            """,
            tuple(params),
        ).fetchall()
    ]
    grouped: dict[tuple[int | None, str], list[dict[str, Any]]] = defaultdict(list)
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        semester_id = int(row["semester_id"]) if row.get("semester_id") else None
        key = str(row.get("course_name_key") or _normalize_course_name(row.get("course_name")))
        grouped[(semester_id, key)].append(row)
        by_name[key].append(row)

    overview_by_offering: dict[int, dict[str, Any]] = {}
    for offering in offerings:
        course_key = _normalize_course_name(offering.get("course_name"))
        semester_id = int(offering["semester_id"]) if offering.get("semester_id") else None
        matched = grouped.get((semester_id, course_key), [])
        if not matched and semester_id is None:
            matched = _latest_term_rows(by_name.get(course_key, []))
        if matched:
            overview_by_offering[int(offering["id"])] = _evaluation_overview(matched)

    credential_row = conn.execute(
        """
        SELECT 1
        FROM teacher_academic_system_credentials
        WHERE teacher_id = ? AND school_code = ? AND enabled = 1
        LIMIT 1
        """,
        (int(teacher_id), SCHOOL_CODE),
    ).fetchone()
    state_row = conn.execute(
        """
        SELECT *
        FROM teacher_academic_evaluation_sync_state
        WHERE teacher_id = ? AND school_code = ?
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (int(teacher_id), SCHOOL_CODE),
    ).fetchone()
    state = _mapping(state_row)
    now = china_now()
    next_allowed = _parse_iso(state.get("next_allowed_at"))
    lease_expires = _parse_iso(state.get("lease_expires_at"))
    is_running = str(state.get("status") or "") == "running" and bool(lease_expires and lease_expires > now)
    should_auto_sync = bool(credential_row) and not is_running and (next_allowed is None or next_allowed <= now)
    sync_payload = {
        **_public_sync_state(state),
        "has_credential": bool(credential_row),
        "has_data": bool(rows),
        "should_auto_sync": should_auto_sync,
        "is_running": is_running,
        "auto_refresh_hours": AUTO_REFRESH_SECONDS // 3600,
        "manual_refresh_hours": MANUAL_REFRESH_SECONDS // 3600,
        "endpoint": "/api/academic-evaluations/sync-current",
    }
    return overview_by_offering, sync_payload


def get_teacher_classroom_academic_evaluation_detail(
    conn: Any,
    *,
    teacher_id: int,
    class_offering_id: int,
) -> dict[str, Any] | None:
    offering_row = conn.execute(
        """
        SELECT o.id, o.semester_id, o.class_id, o.course_id,
               c.name AS course_name, cl.name AS class_name,
               COALESCE(s.name, o.semester, '') AS semester_name
        FROM class_offerings o
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        LEFT JOIN academic_semesters s ON s.id = o.semester_id
        WHERE o.id = ? AND o.teacher_id = ?
        LIMIT 1
        """,
        (int(class_offering_id), int(teacher_id)),
    ).fetchone()
    if offering_row is None:
        return None
    offering = _mapping(offering_row)
    course_key = _normalize_course_name(offering.get("course_name"))
    params: list[Any] = [int(teacher_id), course_key]
    semester_sql = ""
    if offering.get("semester_id"):
        semester_sql = " AND semester_id = ?"
        params.append(int(offering["semester_id"]))
    rows = [
        _mapping(row)
        for row in conn.execute(
            f"""
            SELECT *
            FROM teacher_academic_course_evaluations
            WHERE teacher_id = ? AND course_name_key = ?{semester_sql}
              AND sync_status IN ('active', 'stale')
            ORDER BY hour_type_name, source_course_key, id
            """,
            tuple(params),
        ).fetchall()
    ]
    if not offering.get("semester_id"):
        rows = _latest_term_rows(rows)
    state_row = conn.execute(
        """
        SELECT *
        FROM teacher_academic_evaluation_sync_state
        WHERE teacher_id = ? AND school_code = ?
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (int(teacher_id), SCHOOL_CODE),
    ).fetchone()
    if not rows:
        return {
            "available": False,
            "offering": offering,
            "sync": _public_sync_state(_mapping(state_row)),
            "message": "当前课堂尚未匹配到已发布的教务评价。",
        }

    sources: list[dict[str, Any]] = []
    for row in rows:
        evaluation_id = int(row["id"])
        metrics = [
            {
                **_mapping(metric),
                "grade_counts": _json_object(metric["grade_counts_json"]),
            }
            for metric in conn.execute(
                """
                SELECT sequence_no, metric_name, mean_score, satisfaction_score,
                       weight_value, hour_type_name, grade_counts_json
                FROM teacher_academic_course_evaluation_metrics
                WHERE evaluation_id = ?
                ORDER BY sequence_no, id
                """,
                (evaluation_id,),
            ).fetchall()
        ]
        comments = [
            {
                "sequence_no": _safe_int(comment["sequence_no"]),
                "text": str(comment["comment_text"] or ""),
            }
            for comment in conn.execute(
                """
                SELECT sequence_no, comment_text
                FROM teacher_academic_course_evaluation_comments
                WHERE evaluation_id = ?
                ORDER BY sequence_no, id
                """,
                (evaluation_id,),
            ).fetchall()
        ]
        sources.append(
            {
                "id": evaluation_id,
                "course_name": str(row.get("course_name") or ""),
                "hour_type_name": str(row.get("hour_type_name") or "") or "综合",
                "campus_name": str(row.get("campus_name") or ""),
                "course_score": row.get("course_score"),
                "teacher_weighted_score": row.get("teacher_weighted_score"),
                "enrolled_count": _safe_int(row.get("enrolled_count")),
                "response_count": _safe_int(row.get("response_count")),
                "valid_response_count": _safe_int(row.get("valid_response_count")),
                "institution_rank": row.get("institution_rank"),
                "course_unit_rank": row.get("course_unit_rank"),
                "metrics": metrics,
                "comments": comments,
                "comment_count": len(comments),
                "synced_at": str(row.get("synced_at") or ""),
            }
        )
    return {
        "available": True,
        "offering": offering,
        "overall": _evaluation_overview(rows),
        "keywords": _keyword_union(rows, limit=8),
        "ai_summaries": [
            _clean_text(row.get("ai_summary"), limit=100)
            for row in rows
            if _clean_text(row.get("ai_summary"), limit=100)
        ],
        "sources": sources,
        "sync": _public_sync_state(_mapping(state_row)),
        "frequency_note": (
            f"自动同步每 {AUTO_REFRESH_SECONDS // 3600} 小时最多一次；"
            f"手动刷新至少间隔 {MANUAL_REFRESH_SECONDS // 3600} 小时。"
        ),
    }


__all__ = [
    "AUTO_REFRESH_SECONDS",
    "MANUAL_REFRESH_SECONDS",
    "build_teacher_academic_evaluation_dashboard_context",
    "get_teacher_classroom_academic_evaluation_detail",
    "sync_current_teacher_academic_evaluations",
]
