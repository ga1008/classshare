"""Private student job-application pipeline CRUD."""

from __future__ import annotations

import re
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
    next_action_at = _clean(payload.get("next_action_at"), 16)
    if next_action_at and not _DATETIME_RE.fullmatch(next_action_at):
        raise ValueError("下一步时间格式不正确")
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
    item["status_label"] = STATUS_LABELS.get(item.get("status"), item.get("status"))
    return item


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
        item["status_label"] = STATUS_LABELS.get(item.get("status"), item.get("status"))
    return items


def create_application(conn: Any, student_id: int, payload: Any) -> dict[str, Any]:
    ensure_resume_schema(conn)
    data = _normalize_payload(conn, student_id, payload)
    now = _now()
    application_id = execute_insert_returning_id(
        conn,
        """
        INSERT INTO resume_applications
            (student_id, job_target_id, resume_id, company_name, target_position, channel,
             status, applied_on, next_action, next_action_at, note, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(student_id), data["job_target_id"], data["resume_id"], data["company_name"],
            data["target_position"], data["channel"], data["status"], data["applied_on"],
            data["next_action"], data["next_action_at"], data["note"], now, now,
        ),
    )
    return _get_row(conn, student_id, application_id)


def update_application(conn: Any, student_id: int, application_id: int, payload: Any) -> dict[str, Any]:
    ensure_resume_schema(conn)
    existing = _get_row(conn, student_id, application_id)
    data = _normalize_payload(conn, student_id, payload)
    conn.execute(
        """
        UPDATE resume_applications
        SET job_target_id = ?, resume_id = ?, company_name = ?, target_position = ?, channel = ?,
            status = ?, applied_on = ?, next_action = ?, next_action_at = ?, note = ?, updated_at = ?
        WHERE id = ? AND student_id = ?
        """,
        (
            data["job_target_id"], data["resume_id"], data["company_name"], data["target_position"],
            data["channel"], data["status"], data["applied_on"], data["next_action"],
            data["next_action_at"], data["note"], _now(), int(application_id), int(student_id),
        ),
    )
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
