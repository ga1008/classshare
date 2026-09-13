"""Verified GXUFL check-in contract. No browser tokens or arbitrary remote URLs.

The source identity is obtained from teacherScheduleList for an explicit term.
exportPdf takes that object's opaque id and exports the entire teaching group.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import fitz
import httpx

from ..config import DATA_DIR
from ..database import get_db_connection
from .smart_classroom_integration_service import (
    load_teacher_smart_classroom_access_method,
    open_authenticated_smart_classroom_client,
)

PLATFORM_CODE = "gxufl_smart_classroom"
SOURCE_PREFIX = "/teaching/checkinCourse"
MAX_PDF_BYTES = max(1024, int(os.getenv("ATTENDANCE_MAX_PDF_BYTES", str(50 * 1024 * 1024))))
MAX_PDF_PAGES = max(1, int(os.getenv("ATTENDANCE_MAX_PDF_PAGES", "100")))
MAX_CHECKINS = max(1, int(os.getenv("ATTENDANCE_MAX_CHECKINS", "500")))
REQUEST_INTERVAL = max(0.0, float(os.getenv("ATTENDANCE_REQUEST_INTERVAL", "0.35")))


class AttendanceSourceError(ValueError):
    def __init__(self, code: str, message: str, *, retry_after: int | None = None):
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after


def validate_source_term(year: Any, term: Any) -> tuple[str, str]:
    year, term = str(year or "").strip(), str(term or "").strip()
    match = re.fullmatch(r"(20\d{2})-(20\d{2})", year)
    if not match or int(match[2]) != int(match[1]) + 1 or term not in {"1", "2"}:
        raise AttendanceSourceError("invalid_term", "请选择完整且有效的智慧课堂学年、第一或第二学期。")
    return year, term


def external_account_key(access: dict[str, Any]) -> str:
    # Credential rows are reused on account change. Their numeric id is not identity.
    platform = str(access.get("platform_code") or PLATFORM_CODE).strip().lower()
    username = re.sub(r"\s+", "", str(access.get("username") or ""))
    if not username:
        raise AttendanceSourceError("missing_credential", "请先配置并验证智慧课堂教师账号。")
    return hashlib.sha256(f"{platform}\0{username}".encode("utf-8")).hexdigest()


def load_source_access(teacher_id: int, expected_account_key: str = "") -> dict[str, Any]:
    with get_db_connection() as conn:
        access = load_teacher_smart_classroom_access_method(conn, int(teacher_id))
    if not access:
        raise AttendanceSourceError("missing_credential", "请先配置并验证智慧课堂教师账号。")
    if str(access.get("platform_code") or PLATFORM_CODE) != PLATFORM_CODE:
        raise AttendanceSourceError("unsupported_platform", "该智慧课堂平台尚未验证原件导出协议。")
    if expected_account_key and external_account_key(access) != expected_account_key:
        raise AttendanceSourceError("account_changed", "智慧课堂账号已变更，请重新选择来源；已有原件仍可使用。")
    return access


def _check_response(response: httpx.Response) -> None:
    if response.status_code in {401, 403}:
        raise AttendanceSourceError("authentication_required", "智慧课堂登录已失效或没有该来源权限，请重新验证账号。")
    if response.status_code == 429:
        raw = response.headers.get("Retry-After", "")
        delay = int(raw) if raw.isdigit() else 30
        raise AttendanceSourceError("source_rate_limited", "智慧课堂请求频繁，请稍后重试。", retry_after=min(delay, 3600))
    if not 200 <= response.status_code < 300:
        raise AttendanceSourceError("source_unavailable", "智慧课堂暂时无法完成请求，请稍后重试。")


async def _post_json(client: httpx.AsyncClient, suffix: str, data: dict[str, Any]) -> Any:
    if suffix not in {"teacherScheduleList", "page", "checkinRecord"}:
        raise AttendanceSourceError("invalid_operation", "不支持的签到查询。")
    await asyncio.sleep(REQUEST_INTERVAL)
    try:
        response = await client.post(f"{SOURCE_PREFIX}/{suffix}", data=data)
        _check_response(response)
        payload = response.json()
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        raise AttendanceSourceError("source_invalid_response", "智慧课堂返回异常，原有归档未受影响。") from exc
    if isinstance(payload, dict) and payload.get("errNo") not in (None, 0, "0"):
        raise AttendanceSourceError("source_business_error", "智慧课堂未接受该查询，请检查来源账号与学年学期。")
    return payload


async def fetch_source_schedules(client: httpx.AsyncClient, year: Any, term: Any) -> list[dict[str, Any]]:
    year, term = validate_source_term(year, term)
    payload = await _post_json(client, "teacherScheduleList", {"year": year, "semester": term})
    if not isinstance(payload, list):
        raise AttendanceSourceError("invalid_schedule_list", "智慧课堂授课班列表结构异常，请稍后重新获取。")
    items, seen = [], set()
    for row in payload:
        if not isinstance(row, dict) or not str(row.get("id") or "").strip():
            raise AttendanceSourceError("invalid_schedule", "智慧课堂授课班缺少来源标识。")
        if (str(row.get("year") or ""), str(row.get("semester") or "")) != (year, term):
            raise AttendanceSourceError("source_term_mismatch", "智慧课堂返回了其他学期的授课班，已阻止误导出。")
        key = str(row["id"])
        if key in seen:
            raise AttendanceSourceError("duplicate_schedule", "智慧课堂返回重复授课班，请重新核对来源。")
        seen.add(key)
        items.append({key: row.get(key) for key in (
            "id", "year", "semester", "course", "courseId", "claId", "claName",
            "chooseCourseNo", "kbId", "fullTitle", "fullTitle2", "sections", "week", "xqj", "stuNo",
        )})
    return items


async def list_attendance_source_options(teacher_id: int, year: Any, term: Any) -> dict[str, Any]:
    year, term = validate_source_term(year, term)
    access = await asyncio.to_thread(load_source_access, teacher_id)
    try:
        async with open_authenticated_smart_classroom_client(access) as (client, profile, _login):
            items = await fetch_source_schedules(client, year, term)
    except AttendanceSourceError:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        raise AttendanceSourceError("authentication_required", "无法使用智慧课堂账号，请前往设置重新验证。") from exc
    return {
        "external_account_key": external_account_key(access), "credential_id": access.get("credential_id"),
        "platform_code": profile.platform_code, "school_code": "gxufl", "year": year, "term": term,
        "items": items, "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }


async def fetch_source_checkins(client: httpx.AsyncClient, schedule_id: str, *, expected_schedule: dict | None = None) -> list[dict[str, Any]]:
    records, seen = [], set()
    expected_total = None
    page = 1
    while True:
        payload = await _post_json(client, "page", {
            "page": page, "pageSize": 100, "teacherScheduleId": schedule_id, "field": "id", "order": "descend",
        })
        if not isinstance(payload, dict) or not isinstance(payload.get("list"), list):
            raise AttendanceSourceError("invalid_checkin_page", "点名分页响应异常，未把不完整数据作为成功结果。")
        try:
            total, pages = int(payload["totalRow"]), int(payload["totalPage"])
            current = int(payload.get("pageNumber", payload.get("pageNum", page)))
            if "pageNumber" in payload and "pageNum" in payload and int(payload["pageNumber"]) != int(payload["pageNum"]):
                raise ValueError("Conflicting page identities")
        except (ValueError, TypeError, KeyError) as exc:
            raise AttendanceSourceError("invalid_checkin_page", "点名分页缺少完整计数。") from exc
        if total < 0 or total > MAX_CHECKINS or pages > MAX_CHECKINS or pages < 0 or current != page:
            raise AttendanceSourceError("checkin_limit", "点名记录数量或分页超出限制，未截断归档。")
        if expected_total is not None and total != expected_total:
            raise AttendanceSourceError("source_changed", "读取期间点名记录发生变化，请重新导出。")
        expected_total = total
        for row in payload["list"]:
            if not isinstance(row, dict) or row.get("id") in (None, ""):
                raise AttendanceSourceError("invalid_checkin", "点名记录缺少标识。")
            key = str(row["id"])
            if key in seen:
                raise AttendanceSourceError("duplicate_checkin", "点名分页包含重复记录，无法确认完整范围。")
            # Verified source semantics: query/export uses one representative
            # schedule id for the teaching group; each event may name a different
            # timetable row. Never compare those ids or reconstruct their prefix.
            seen.add(key)
            records.append({key: row.get(key) for key in (
                "id", "teacherScheduleId", "year", "semester", "course", "courseId",
                "claId", "createTime", "updateTime", "week", "dayOfWeek", "section", "checkedRate",
            )})
        if page >= max(1, pages):
            break
        if not payload["list"]:
            raise AttendanceSourceError("incomplete_checkins", "点名分页提前结束，无法确认完整范围。")
        page += 1
    if len(records) != expected_total:
        raise AttendanceSourceError("incomplete_checkins", "点名清单与来源总数不一致，请重新导出。")
    if expected_schedule:
        _validate_record_scope(expected_schedule, records)
    return records


def _validate_record_scope(schedule: dict, records: list[dict]) -> None:
    for record in records:
        for key in ("year", "semester", "courseId", "claId"):
            value, expected = record.get(key), schedule.get(key)
            if expected not in (None, "") and str(value or "") != str(expected):
                raise AttendanceSourceError("source_scope_mismatch", "点名记录与所选学年学期或教学班不一致，已阻止归档。")


async def fetch_source_detail(client: httpx.AsyncClient, checkin_id: Any, *, expected_schedule: dict | None = None) -> dict[str, Any]:
    data = await _post_json(client, "checkinRecord", {"id": checkin_id})
    if not isinstance(data, dict) or not isinstance(data.get("stuList"), list) or not isinstance(data.get("checkinCourse"), dict):
        raise AttendanceSourceError("invalid_checkin_detail", "签到明细不完整，未覆盖已有数据。")
    if str(data["checkinCourse"].get("id")) != str(checkin_id):
        raise AttendanceSourceError("checkin_identity_mismatch", "签到明细与点名标识不符。")
    if expected_schedule:
        _validate_record_scope(expected_schedule, [data["checkinCourse"]])
    students, seen = [], set()
    for row in data["stuList"]:
        if not isinstance(row, dict) or not str(row.get("no") or "").strip():
            raise AttendanceSourceError("invalid_student_identity", "来源签到名单缺少学号，需要核对。")
        no = str(row["no"]).strip()
        if no in seen:
            raise AttendanceSourceError("duplicate_student_identity", "来源签到名单有重复学号，需要核对。")
        seen.add(no)
        students.append({"no": no, "name": str(row.get("name") or ""), "status": str(row.get("status") or "")})
    return {
        "id": str(checkin_id), "stuList": students,
        "statusCounts": data.get("statusCounts") if isinstance(data.get("statusCounts"), dict) else {},
        "computedStatusCounts": dict(Counter(row["status"] for row in students)),
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }


def checkin_manifest_fingerprint(records: list[dict[str, Any]]) -> str:
    normalized = sorted(records, key=lambda row: str(row.get("id")))
    return hashlib.sha256(json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def validate_pdf_file(path: Path) -> dict[str, Any]:
    size = path.stat().st_size
    if not 0 < size <= MAX_PDF_BYTES:
        raise AttendanceSourceError("pdf_size_limit", "PDF为空或超过文件限制，未保存不完整原件。")
    with path.open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise AttendanceSourceError("not_pdf", "智慧课堂返回的不是PDF，可能需要重新登录。")
    try:
        with fitz.open(path) as pdf:
            if pdf.is_repaired:
                raise AttendanceSourceError("incomplete_pdf", "PDF需要修复才能读取，可能下载不完整；请重新导出原件。")
            if pdf.needs_pass or not 0 < len(pdf) <= MAX_PDF_PAGES:
                raise AttendanceSourceError("pdf_page_limit", "PDF加密、为空或超过页面限制，无法完整解析。")
            page_count = len(pdf)
            for page in pdf:
                if page.rect.is_empty or page.rect.width * page.rect.height > 50_000_000:
                    raise AttendanceSourceError("pdf_invalid_page", "PDF页面尺寸异常。")
    except AttendanceSourceError:
        raise
    except Exception as exc:
        raise AttendanceSourceError("invalid_pdf", "PDF无法完整打开，请重新导出。") from exc
    return {"byte_size": size, "page_count": page_count}


async def _download_pdf(client: httpx.AsyncClient, schedule_id: str) -> tuple[Path, str]:
    root = Path(DATA_DIR) / "attendance_tmp"
    root.mkdir(parents=True, exist_ok=True)
    temp = tempfile.NamedTemporaryFile(dir=root, suffix=".pdf", prefix="export-", delete=False)
    path = Path(temp.name)
    try:
        async with client.stream("POST", f"{SOURCE_PREFIX}/exportPdf", data={"teacherScheduleId": schedule_id}, timeout=180.0) as response:
            _check_response(response)
            if response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/pdf":
                raise AttendanceSourceError("not_pdf", "智慧课堂没有返回PDF，请重新验证账号或来源。")
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > MAX_PDF_BYTES:
                    raise AttendanceSourceError("pdf_size_limit", "PDF超过文件限制，已停止下载。")
                temp.write(chunk)
            disposition = response.headers.get("Content-Disposition", "")
            filename_match = re.search(r'filename="?([^";]+)', disposition, re.I)
            filename = Path(filename_match[1].replace("\\", "/")).name if filename_match else "点名记录.pdf"
        temp.close()
        await asyncio.to_thread(validate_pdf_file, path)
        return path, filename[:200]
    except httpx.HTTPError as exc:
        temp.close()
        path.unlink(missing_ok=True)
        raise AttendanceSourceError("source_invalid_response", "智慧课堂下载连接中断，未保存不完整原件。") from exc
    except BaseException:
        temp.close()
        path.unlink(missing_ok=True)
        raise


async def fetch_attendance_source_snapshot(
    teacher_id: int, external_account_key: str, year: Any, term: Any, remote_schedule_id: str,
) -> dict[str, Any]:
    year, term = validate_source_term(year, term)
    access = await asyncio.to_thread(load_source_access, teacher_id, external_account_key)
    pdf_path = None
    try:
        async with open_authenticated_smart_classroom_client(access) as (client, _profile, _login):
            options = await fetch_source_schedules(client, year, term)
            matches = [row for row in options if str(row["id"]) == str(remote_schedule_id)]
            if len(matches) != 1:
                raise AttendanceSourceError("source_not_found", "当前账号及学期下找不到该教学班，请重新选择来源。")
            schedule = matches[0]
            records = await fetch_source_checkins(client, str(remote_schedule_id), expected_schedule=schedule)
            _validate_record_scope(schedule, records)
            if not records:
                raise AttendanceSourceError("no_checkins", "该教学班暂无点名记录，无需导出空档案。")
            details = [await fetch_source_detail(client, row["id"], expected_schedule=schedule) for row in records]
            pdf_path, filename = await _download_pdf(client, str(remote_schedule_id))
            after = await fetch_source_checkins(client, str(remote_schedule_id), expected_schedule=schedule)
            if checkin_manifest_fingerprint(records) != checkin_manifest_fingerprint(after):
                raise AttendanceSourceError("source_changed", "导出期间点名清单发生变化，请重试以核对完整范围。")
        await asyncio.to_thread(load_source_access, teacher_id, external_account_key)
        metadata = await asyncio.to_thread(validate_pdf_file, pdf_path)
        return {
            **metadata, "pdf_file": pdf_path, "filename": filename,
            "request_manifest": {"year": year, "semester": term, "teacherScheduleId": str(remote_schedule_id), "scope": "all_schedule"},
            "checkin_manifest": {
                "schedule": schedule, "checkins": records, "details": details,
                "record_count": len(records), "fingerprint": checkin_manifest_fingerprint(records),
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
                "api_detail_coverage": len(details),
            },
        }
    except BaseException:
        if pdf_path:
            pdf_path.unlink(missing_ok=True)
        raise
