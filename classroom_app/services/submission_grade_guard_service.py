"""Concurrency and input contract for the shared manual grading endpoint."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

from fastapi import HTTPException

from ..db.connection import begin_immediate_transaction, get_configured_db_engine


def submission_review_revision(submission: dict[str, Any]) -> str:
    """Also detects return/resubmit and legacy writers without a grade ledger row."""
    state = {
        key: str(submission.get(key) or "")
        for key in ("status", "submitted_at", "answers_json", "feedback_md")
    }
    for key in ("id", "grading_job_id", "active_grade_revision_id", "resubmission_allowed",
                "is_absence_score", "is_late_submission", "late_score_cap_applied"):
        state[key] = int(submission.get(key) or 0)
    for key in ("score", "score_before_late_penalty", "late_penalty_points"):
        value = submission.get(key)
        state[key] = float(value) if value is not None else None
    return hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def validate_manual_grade(payload: Any) -> float:
    raw = payload.get("score") if isinstance(payload, dict) else None
    if isinstance(raw, bool) or raw is None or (isinstance(raw, str) and not raw.strip()):
        raise HTTPException(400, "请输入 0-100 的原始分数，空分数不能记为零分")
    try:
        score = float(raw)
    except (TypeError, ValueError, OverflowError) as exc:
        raise HTTPException(400, "请输入 0-100 的原始分数") from exc
    if not math.isfinite(score) or not 0 <= score <= 100:
        raise HTTPException(400, "请输入 0-100 的原始分数")
    return score


def lock_submission_for_manual_grade(conn: Any, submission_id: int) -> None:
    """Keep the reread, version check, grade and ledger writes in one transaction.

    SQLite reserves the writer before reading; PostgreSQL serializes only this
    submission row. Call before loading the permission-checked submission.
    """
    engine = get_configured_db_engine()
    if engine != "sqlite" or not bool(getattr(conn, "in_transaction", False)):
        begin_immediate_transaction(conn, engine=engine)
    if engine == "postgres":
        conn.execute("SELECT id FROM submissions WHERE id = ? FOR UPDATE", (submission_id,)).fetchone()


def ensure_manual_grade_revision(submission: dict[str, Any], payload: dict[str, Any]) -> None:
    # Optional for existing Web clients. New miniapp clients must send the
    # revision returned with the answer they actually reviewed.
    if "expected_review_revision" not in payload:
        return
    expected = payload["expected_review_revision"]
    if not isinstance(expected, str) or expected != submission_review_revision(submission):
        raise HTTPException(409, "该答卷或成绩已更新，请重新加载后核对评分；本次修改尚未保存")


_GENERATED_LATE_SUFFIX = re.compile(
    r"(?:\s*## 补交扣分\r?\n补交扣分：原始分 [^\r\n]+，最终分 [^\r\n]+。\s*)+$"
)


def editable_manual_feedback(feedback: Any) -> str:
    """The late-policy footer is regenerated on save, never edited or doubled."""
    return _GENERATED_LATE_SUFFIX.sub("", str(feedback or "")).strip()
