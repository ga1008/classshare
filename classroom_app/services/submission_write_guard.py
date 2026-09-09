"""Optimistic submission versions and short database locks shared by clients."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from fastapi import HTTPException

from ..db.connection import get_configured_db_engine
from .assignment_lifecycle_service import submission_resubmission_accepts


def submission_write_version(submission: Any) -> str:
    if not submission:
        return "unsubmitted"
    row = dict(submission)
    fields = [row.get(key) for key in (
        "id", "submitted_at", "returned_at", "resubmission_due_at",
        "resubmission_allowed", "is_absence_score",
    )]
    return hashlib.sha256(json.dumps(fields, default=str).encode()).hexdigest()[:32]


def draft_matches_submission_round(draft: Any, submission: Any) -> bool:
    row = dict(submission or {})
    if not row.get("resubmission_allowed"):
        return True
    try:
        def local_time(raw: Any) -> datetime:
            value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            return value.astimezone().replace(tzinfo=None) if value.tzinfo else value
        updated = local_time(dict(draft or {}).get("server_updated_at"))
        returned = local_time(row.get("returned_at"))
        # Historical return timestamps had second precision. Do not allow an
        # older draft from that same second to masquerade as a fresh round.
        return (updated if returned.microsecond else updated.replace(microsecond=0)) > returned
    except (TypeError, ValueError):
        return False


def lock_submission_writer(conn: Any, assignment_id: Any, student_id: int) -> None:
    # A per-student task key covers the no-row-yet case too. SQLite callers
    # already hold BEGIN IMMEDIATE; the PostgreSQL lock lasts only to commit.
    if get_configured_db_engine() == "postgres":
        from .group_assignment_service import lock_group_grading_for_student

        # Group finalization updates several members. Serialize the group before
        # any task/submission row, matching manual grades and AI callbacks.
        lock_group_grading_for_student(conn, assignment_id, student_id)
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (
            f"assignment-submission:{assignment_id}:{student_id}",
        ))
        # Grade writers use this row lock. Coordinate replacement with them too.
        conn.execute("SELECT id FROM submissions WHERE assignment_id = ? AND student_pk_id = ? FOR UPDATE",
                     (assignment_id, student_id)).fetchone()


def verify_submission_write(current: Any, expected: Any, *, actor_role: str,
                            client_version: str = "") -> None:
    version = submission_write_version(current)
    if version != submission_write_version(expected) or (client_version and client_version != version):
        raise HTTPException(409, "提交状态已变化，请刷新后确认本次提交结果")
    if current and actor_role == "student":
        row = dict(current)
        if not int(row.get("is_absence_score") or 0) and not submission_resubmission_accepts(row):
            raise HTTPException(409, "该答卷已提交或重交窗口已关闭，请刷新确认")
