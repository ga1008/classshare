"""Durable attendance bridge: network/model work outside short fenced transactions."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from ..database import get_db_connection
from . import attendance_report_service as reports
from .attendance_report_parser_service import analyze_attendance_pdf, _gateway_json, MAX_AI_CALLS, AttendanceProcessingHalted
from .ai_durable_job_service import _iso
from ..db.connection import get_configured_db_engine
from .file_service import resolve_global_file_path, store_file_object_globally
from .smart_classroom_attendance_adapter import (
    AttendanceSourceError, fetch_attendance_source_snapshot, load_source_access,
)


def _context(job: dict) -> dict:
    with get_db_connection() as conn:
        result = reports.load_attendance_job_context(conn, int(job["id"]), str(job["lease_token"]))
        conn.commit()
        return result


def _completed(job: dict) -> dict | None:
    with get_db_connection() as conn:
        result = reports.completed_attendance_job_result(conn, int(job["id"]), str(job["lease_token"]))
        conn.commit()
        return result


def _publish(job: dict, method, value: dict) -> dict:
    with get_db_connection() as conn:
        result = method(conn, int(job["id"]), str(job["lease_token"]), value)
        conn.commit()
        return result


def _store(path: Path) -> dict:
    with path.open("rb") as stream:
        return store_file_object_globally(stream)


def _verified_cached_path(version: dict) -> Path:
    path = resolve_global_file_path(version["source_file_hash"])
    if not path:
        raise AttendanceSourceError("source_file_missing", "归档原件暂不可用，请恢复原件后重新解析。")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    if digest.hexdigest() != version["source_file_hash"] or path.stat().st_size != int(version["source_byte_size"]):
        raise AttendanceSourceError("source_file_integrity", "归档原件完整性校验失败，已停止解析。")
    return path


def _ai_checkpoint(job: dict, batch_key: str, result: tuple | None = None) -> dict:
    """Reserve before send. An interrupted, uncertain call is never auto-rebilled."""
    with get_db_connection() as conn:
        engine = get_configured_db_engine()
        if engine == "sqlite":
            conn.execute("BEGIN IMMEDIATE")
        lock = " FOR UPDATE" if engine == "postgres" else ""
        row = conn.execute("SELECT payload_json FROM ai_jobs WHERE id=? AND status='running' AND lease_token=? AND lease_expires_at>?" + lock,
                           (int(job["id"]), job["lease_token"], _iso())).fetchone()
        if not row:
            raise RuntimeError("Attendance AI reservation lost its job lease")
        payload = json.loads(row["payload_json"])
        batches = payload.setdefault("attendance_ai_batches", {})
        prior = batches.get(batch_key)
        if prior and result is None:
            conn.commit()
            return prior
        if result is not None:
            if not prior or prior["state"] != "pending":
                raise RuntimeError("Attendance AI result reservation changed")
            batches[batch_key] = {"state": "completed", "response": result[0], "model": result[1]}
        else:
            if len(batches) >= MAX_AI_CALLS:
                raise RuntimeError("Attendance AI call budget exhausted")
            batches[batch_key] = {"state": "pending"}
        encoded = json.dumps(payload, ensure_ascii=False)
        if len(encoded.encode("utf-8")) > 4 * 1024 * 1024:
            raise RuntimeError("Attendance AI checkpoint is too large")
        cursor = conn.execute("UPDATE ai_jobs SET payload_json=?,updated_at=? WHERE id=? AND status='running' AND lease_token=? AND lease_expires_at>?",
                              (encoded, _iso(), int(job["id"]), job["lease_token"], _iso()))
        if cursor.rowcount != 1:
            raise RuntimeError("Attendance AI reservation expired")
        conn.commit()
        return {"state": "reserved"} if result is None else batches[batch_key]


def _durable_ai_gateway(job: dict):
    async def call(system: str, prompt: str, **kwargs):
        batch_key = hashlib.sha256(json.dumps([system, prompt, kwargs], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        try:
            prior = await asyncio.to_thread(_ai_checkpoint, job, batch_key)
        except RuntimeError as exc:
            raise AttendanceProcessingHalted("AI任务租约或调用预算已失效，已停止后续请求。") from exc
        if prior["state"] == "completed":
            return prior["response"], prior["model"]
        if prior["state"] == "pending":
            raise AttendanceProcessingHalted("上次AI请求结果不确定，已停止自动重发；请检查后创建新解析。")
        try:
            result = await _gateway_json(system, prompt, **kwargs)
        except Exception as exc:
            raise AttendanceProcessingHalted("AI请求未返回可保存结果，已停止后续请求；不自动重发。") from exc
        try:
            await asyncio.to_thread(_ai_checkpoint, job, batch_key, result)
        except RuntimeError as exc:
            raise AttendanceProcessingHalted("AI结果未取得有效任务租约，已停止后续请求。") from exc
        return result
    return call


async def dispatch_attendance_job(job: dict) -> dict:
    # Recovery after business publication and before durable result publication.
    completed = await asyncio.to_thread(_completed, job)
    if completed is not None:
        return completed
    context = await asyncio.to_thread(_context, job)
    binding, version = context["binding"], context["source_version"]
    teacher_id = int(context["payload"]["teacher_id"])
    if job["task_type"] == "attendance_export":
        snapshot = await fetch_attendance_source_snapshot(
            teacher_id=teacher_id, external_account_key=binding["external_account_key"],
            year=binding["academic_year"], term=binding["academic_term"],
            remote_schedule_id=binding["remote_schedule_id"],
        )
        path = Path(snapshot.pop("pdf_file"))
        try:
            stored = await asyncio.to_thread(_store, path)
            # Credential rows may be updated during download. Recheck before binding.
            await asyncio.to_thread(load_source_access, teacher_id, binding["external_account_key"])
            snapshot.update(file_hash=stored["hash"], byte_size=stored["size"])
            published = await asyncio.to_thread(_publish, job, reports.cache_attendance_source, snapshot)
        finally:
            path.unlink(missing_ok=True)
    elif job["task_type"] == "attendance_parse":
        # No source credential lookup: the immutable cached PDF is sufficient.
        path = await asyncio.to_thread(_verified_cached_path, version)
        manifest = json.loads(version.get("checkin_manifest_json") or "{}")
        result = await analyze_attendance_pdf(path, manifest, teacher_id=teacher_id, ai_chat=_durable_ai_gateway(job))
        published = await asyncio.to_thread(_publish, job, reports.save_attendance_parse_result, result)
    else:
        raise ValueError("Unsupported attendance job")
    return {"completed": True, "report_id": context["report"]["id"], "source_version_id": version["id"], **published}


def mark_attendance_failure(job: dict, error_code: str) -> None:
    with get_db_connection() as conn:
        reports.mark_attendance_job_failure(conn, int(job["id"]), str(job["lease_token"]), "签到处理未完成", error_code=error_code)
        conn.commit()
