"""Private student job-application pipeline CRUD."""

from __future__ import annotations

import re
import json
from datetime import datetime, timezone
from typing import Any

from ...db.connection import execute_insert_returning_id
from ...db.schema_resume import ensure_resume_schema

APPLICATION_STATUSES = (
    "wishlist", "preparing", "applied", "written_test", "interview", "offer", "rejected", "closed",
)
STATUS_LABELS = {
    "wishlist": "想投",
    "preparing": "准备中",
    "applied": "已投递",
    "written_test": "笔试",
    "interview": "面试",
    "offer": "Offer",
    "rejected": "未通过",
    "closed": "已结束",
}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2})?$")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _clean(value: Any, limit: int) -> str:
    return str(value if value is not None else "").strip()[:limit]


def _optional_owned_id(conn: Any, student_id: int, value: Any, table: str) -> int | None:
    raw = str(value if value is not None else "").strip()
    if not raw:
        return None
    if not raw.isdigit() or int(raw) <= 0:
        raise ValueError("关联记录格式不正确")
    record_id = int(raw)
    row = conn.execute(
        f"SELECT id FROM {table} WHERE id = ? AND student_id = ? LIMIT 1",
        (record_id, int(student_id)),
    ).fetchone()
    if row is None:
        raise ValueError("关联记录不存在或无权访问")
    return record_id


def _normalize_payload(conn: Any, student_id: int, payload: Any) -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    status = _clean(payload.get("status") or "wishlist", 30).lower()
    if status not in APPLICATION_STATUSES:
        raise ValueError("投递状态不正确")
    applied_on = _clean(payload.get("applied_on"), 10)
    if applied_on and not _DATE_RE.fullmatch(applied_on):
        raise ValueError("投递日期格式不正确")
    if applied_on:
        try:
            datetime.strptime(applied_on, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("投递日期不是有效日期") from exc
    next_action_at = _clean(payload.get("next_action_at"), 16)
    if next_action_at and not _DATETIME_RE.fullmatch(next_action_at):
        raise ValueError("下一步时间格式不正确")
    if next_action_at:
        try:
            datetime.fromisoformat(next_action_at)
        except ValueError as exc:
            raise ValueError("下一步时间不是有效日期") from exc
    job_target_id = _optional_owned_id(conn, student_id, payload.get("job_target_id"), "resume_job_targets")
    resume_id = _optional_owned_id(conn, student_id, payload.get("resume_id"), "resumes")
    company = _clean(payload.get("company_name"), 100)
    position = _clean(payload.get("target_position"), 100)
    if job_target_id and (not company or not position):
        target = conn.execute(
            "SELECT company_name, target_position FROM resume_job_targets WHERE id = ? AND student_id = ?",
            (job_target_id, int(student_id)),
        ).fetchone()
        if target:
            target = dict(target)
            company = company or _clean(target.get("company_name"), 100)
            position = position or _clean(target.get("target_position"), 100)
    if not company:
        raise ValueError("请填写公司或组织名称")
    if not position:
        raise ValueError("请填写目标岗位")
    return {
        "job_target_id": job_target_id,
        "resume_id": resume_id,
        "company_name": company,
        "target_position": position,
        "channel": _clean(payload.get("channel"), 100),
        "status": status,
        "applied_on": applied_on,
        "next_action": _clean(payload.get("next_action"), 300),
        "next_action_at": next_action_at,
        "note": _clean(payload.get("note"), 2_000),
    }


def _get_row(conn: Any, student_id: int, application_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT a.*, r.title AS resume_title, j.target_position AS linked_job_position
        FROM resume_applications a
        LEFT JOIN resumes r ON r.id = a.resume_id AND r.student_id = a.student_id
        LEFT JOIN resume_job_targets j ON j.id = a.job_target_id AND j.student_id = a.student_id
        WHERE a.id = ? AND a.student_id = ? LIMIT 1
        """,
        (int(application_id), int(student_id)),
    ).fetchone()
    if row is None:
        raise LookupError("投递记录不存在或无权访问")
    item = dict(row)
    _hydrate_snapshot(item)
    item["status_label"] = STATUS_LABELS.get(item.get("status"), item.get("status"))
    return item


def _hydrate_snapshot(item: dict[str, Any]) -> None:
    for key in ("resume_snapshot", "job_snapshot"):
        try:
            item[key] = json.loads(item.pop(key + "_json", "{}") or "{}")
        except (TypeError, ValueError):
            item[key] = {}
    item["resume_title"] = item["resume_snapshot"].get("title") or item.get("resume_title")
    item["linked_job_position"] = item["job_snapshot"].get("target_position") or item.get("linked_job_position")


def _snapshots(conn: Any, student_id: int, data: dict[str, Any]) -> tuple[int | None, dict[str, Any], dict[str, Any]]:
    from . import resume_document_service as documents
    from . import resume_job_target_service as targets
    version_number = None
    resume_snapshot: dict[str, Any] = {}
    job_snapshot: dict[str, Any] = {}
    if data.get("resume_id"):
        resume = documents.get_resume(conn, student_id, data["resume_id"], include_archived=True)
        version_number = int(resume.get("render_revision") or resume.get("revision") or 1)
        resume_snapshot = {"id": int(data["resume_id"]), "revision": version_number,
                           "title": resume["title"], "target_position": resume["target_position"]}
        try:
            version = documents.get_version(conn, student_id, data["resume_id"], version_number)
            resume_snapshot.update(title=version["snapshot"].get("title"), content_hash=version["content_hash"], snapshot=version["snapshot"])
        except LookupError:
            pass
    if data.get("job_target_id"):
        job_snapshot = targets.get_job_target(conn, student_id, data["job_target_id"])
    return version_number, resume_snapshot, job_snapshot


def list_applications(conn: Any, student_id: int) -> list[dict[str, Any]]:
    ensure_resume_schema(conn)
    rows = conn.execute(
        """
        SELECT a.*, r.title AS resume_title, j.target_position AS linked_job_position
        FROM resume_applications a
        LEFT JOIN resumes r ON r.id = a.resume_id AND r.student_id = a.student_id
        LEFT JOIN resume_job_targets j ON j.id = a.job_target_id AND j.student_id = a.student_id
        WHERE a.student_id = ?
        ORDER BY CASE WHEN COALESCE(a.next_action_at, '') = '' THEN 1 ELSE 0 END,
                 a.next_action_at ASC, a.updated_at DESC, a.id DESC
        LIMIT 300
        """,
        (int(student_id),),
    ).fetchall()
    items = [dict(row) for row in rows]
    for item in items:
        _hydrate_snapshot(item)
        item["status_label"] = STATUS_LABELS.get(item.get("status"), item.get("status"))
    return items


def create_application(conn: Any, student_id: int, payload: Any) -> dict[str, Any]:
    ensure_resume_schema(conn)
    data = _normalize_payload(conn, student_id, payload)
    resume_revision, resume_snapshot, job_snapshot = _snapshots(conn, student_id, data)
    now = _now()
    application_id = execute_insert_returning_id(
        conn,
        """
        INSERT INTO resume_applications
            (student_id, job_target_id, resume_id, company_name, target_position, channel,
             status, applied_on, next_action, next_action_at, note, created_at, updated_at,
             resume_revision, resume_snapshot_json, job_snapshot_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(student_id), data["job_target_id"], data["resume_id"], data["company_name"],
            data["target_position"], data["channel"], data["status"], data["applied_on"],
            data["next_action"], data["next_action_at"], data["note"], now, now, resume_revision,
            json.dumps(resume_snapshot, ensure_ascii=False), json.dumps(job_snapshot, ensure_ascii=False),
        ),
    )
    return _get_row(conn, student_id, application_id)


def update_application(conn: Any, student_id: int, application_id: int, payload: Any) -> dict[str, Any]:
    ensure_resume_schema(conn)
    existing = _get_row(conn, student_id, application_id)
    from .resume_document_service import require_revision, ResumeConflict
    revision = require_revision(existing, payload["revision"]) if "revision" in payload else int(existing.get("revision") or 1)
    merged = {**existing, **payload}
    for field, table in (("job_target_id", "resume_job_targets"), ("resume_id", "resumes")):
        if merged.get(field) and merged[field] == existing.get(field):
            found = conn.execute(f"SELECT 1 FROM {table} WHERE id = ? AND student_id = ?", (merged[field], int(student_id))).fetchone()
            if not found:
                merged[field] = None
    data = _normalize_payload(conn, student_id, merged)
    resume_revision, resume_snapshot, job_snapshot = _snapshots(conn, student_id, data)
    if (data["resume_id"] == existing.get("resume_id") or not data["resume_id"] and "resume_id" not in payload) and existing.get("resume_snapshot"):
        resume_revision, resume_snapshot = existing.get("resume_revision"), existing["resume_snapshot"]
    if (data["job_target_id"] == existing.get("job_target_id") or not data["job_target_id"] and "job_target_id" not in payload) and existing.get("job_snapshot"):
        job_snapshot = existing["job_snapshot"]
    result = conn.execute(
        """
        UPDATE resume_applications
        SET job_target_id = ?, resume_id = ?, company_name = ?, target_position = ?, channel = ?,
            status = ?, applied_on = ?, next_action = ?, next_action_at = ?, note = ?, updated_at = ?,
            resume_revision = ?, resume_snapshot_json = ?, job_snapshot_json = ?, revision = revision + 1
        WHERE id = ? AND student_id = ? AND revision = ?
        """,
        (
            data["job_target_id"], data["resume_id"], data["company_name"], data["target_position"],
            data["channel"], data["status"], data["applied_on"], data["next_action"],
            data["next_action_at"], data["note"], _now(), resume_revision,
            json.dumps(resume_snapshot, ensure_ascii=False), json.dumps(job_snapshot, ensure_ascii=False),
            int(application_id), int(student_id), revision,
        ),
    )
    if result.rowcount != 1:
        raise ResumeConflict("投递记录已更新，请保留输入并重新载入。")
    item = _get_row(conn, student_id, application_id)
    item["_status_changed"] = existing.get("status") != item.get("status")
    return item


def delete_application(conn: Any, student_id: int, application_id: int) -> None:
    ensure_resume_schema(conn)
    _get_row(conn, student_id, application_id)
    conn.execute(
        "DELETE FROM resume_applications WHERE id = ? AND student_id = ?",
        (int(application_id), int(student_id)),
    )


def backfill_application_snapshots(conn: Any, *, limit: int = 100) -> int:
    """Preserve known legacy links; do not claim reconstructed facts were sent."""
    rows = conn.execute("SELECT * FROM resume_applications WHERE (resume_id IS NOT NULL AND resume_snapshot_json = '{}') OR (job_target_id IS NOT NULL AND job_snapshot_json = '{}') ORDER BY id LIMIT ?", (max(1, min(500, int(limit))),)).fetchall()
    for raw in rows:
        item = dict(raw)
        try:
            revision, resume, job = _snapshots(conn, int(item["student_id"]), item)
        except (LookupError, ValueError):
            revision, resume, job = None, {}, {}
        if item.get("resume_id") and not resume:
            resume = {"id": item["resume_id"], "title": "历史关联简历", "availability": "missing"}
        if item.get("job_target_id") and not job:
            job = {"id": item["job_target_id"], "company_name": item.get("company_name"), "target_position": item.get("target_position"), "availability": "missing"}
        if resume:
            resume["historical_content_verified"] = False
        if job:
            job["historical_content_verified"] = False
        conn.execute("UPDATE resume_applications SET resume_revision = COALESCE(resume_revision,?), resume_snapshot_json = CASE WHEN resume_snapshot_json = '{}' THEN ? ELSE resume_snapshot_json END, job_snapshot_json = CASE WHEN job_snapshot_json = '{}' THEN ? ELSE job_snapshot_json END WHERE id = ? AND student_id = ? AND revision = ?", (revision, json.dumps(resume, ensure_ascii=False), json.dumps(job, ensure_ascii=False), int(item["id"]), int(item["student_id"]), int(item.get("revision") or 1)))
    return len(rows)
