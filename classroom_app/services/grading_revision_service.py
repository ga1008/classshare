from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from ..db.connection import execute_insert_returning_id, get_configured_db_engine


def activate_submission_grade_revision(
    conn,
    *,
    submission: dict[str, Any],
    data: dict[str, Any],
    score: Any,
    feedback_md: Any,
) -> int:
    """Append a grade revision and atomically make it the active version."""

    submission_id = int(submission["id"])
    revision_hash = str(
        data.get("grading_revision_hash")
        or data.get("submission_fingerprint")
        or f"manual:{submission_id}:{uuid.uuid4().hex}"
    ).strip()
    existing = conn.execute(
        "SELECT id, status, score, feedback_md FROM submission_grade_revisions WHERE submission_id = ? AND revision_hash = ?",
        (submission_id, revision_hash),
    ).fetchone()
    if (existing and existing["status"] == "active" and existing["score"] == score
            and str(existing["feedback_md"] or "") == str(feedback_md or "")
            and not any(data.get(key) for key in ("quality_audit", "review_reason_codes", "execution_metadata", "execution_state"))):
        conn.execute("UPDATE submissions SET active_grade_revision_id = ? WHERE id = ?", (existing["id"], submission_id))
        return int(existing["id"])
    ai_job_id = data.get("ai_job_id") or submission.get("grading_job_id")
    ai_result_id = None
    if ai_job_id:
        job_row = conn.execute("SELECT result_id FROM ai_jobs WHERE id = ?", (int(ai_job_id),)).fetchone()
        if job_row:
            ai_result_id = job_row["result_id"]
    revision_no_row = conn.execute(
        "SELECT COALESCE(MAX(revision_no), 0) + 1 AS next_revision FROM submission_grade_revisions WHERE submission_id = ?",
        (submission_id,),
    ).fetchone()
    revision_no = int(revision_no_row["next_revision"] if revision_no_row else 1)
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        UPDATE submission_grade_revisions
        SET status = 'superseded', superseded_at = ?
        WHERE submission_id = ? AND status = 'active'
        """,
        (now, submission_id),
    )
    provenance = {
        "source": data.get("source") or ("ai" if ai_job_id or any(data.get(key) for key in (
            "execution_metadata", "execution_plan", "execution_state", "ai_policy_version", "grading_contract_version"
        )) else "manual"),
        "actor_role": data.get("actor_role") or "",
        "actor_user_pk": data.get("actor_user_pk"),
        "ai_job_id": ai_job_id,
        "requested_provider": data.get("requested_provider") or "",
        "requested_model": data.get("requested_model") or "",
        "grading_contract_version": data.get("grading_contract_version") or "",
        "ai_confidence": data.get("ai_confidence"),
        "review_reason_codes": data.get("review_reason_codes") or [],
        "group_work_score": data.get("group_work_score"),
        "group_peer_average": data.get("group_peer_average"),
    }
    quality_audit = dict(data.get("quality_audit") or {})
    for key in ("execution_plan", "execution_metadata", "adjudication_execution_metadata", "execution_state", "ai_policy_version", "business_context"):
        if data.get(key) is not None:
            quality_audit[key] = data[key]
    revision_id = execute_insert_returning_id(
        conn,
        """
        INSERT INTO submission_grade_revisions (
            submission_id, ai_job_id, ai_result_id, revision_hash, revision_no,
            status, score, feedback_md, quality_audit_json, provenance_json,
            created_at, activated_at
        )
        VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)
        ON CONFLICT (submission_id, revision_hash) DO UPDATE SET
            ai_job_id = excluded.ai_job_id,
            ai_result_id = excluded.ai_result_id,
            status = 'active',
            score = excluded.score,
            feedback_md = excluded.feedback_md,
            quality_audit_json = excluded.quality_audit_json,
            provenance_json = excluded.provenance_json,
            activated_at = excluded.activated_at,
            superseded_at = NULL
        """,
        (
            submission_id,
            int(ai_job_id) if ai_job_id else None,
            int(ai_result_id) if ai_result_id else None,
            revision_hash,
            revision_no,
            score,
            str(feedback_md or ""),
            json.dumps(quality_audit, ensure_ascii=False, sort_keys=True),
            json.dumps(provenance, ensure_ascii=False, sort_keys=True),
            now,
            now,
        ),
        engine=get_configured_db_engine(),
    )
    conn.execute(
        "UPDATE submissions SET active_grade_revision_id = ? WHERE id = ?",
        (int(revision_id), submission_id),
    )
    return int(revision_id)
