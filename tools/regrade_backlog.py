"""List and re-enqueue submissions whose AI grading failed or stalled (e.g. after a vendor outage).

Run inside the app container (it needs the database and the AI service):

    python tools/regrade_backlog.py --since 2026-10-14           # dry run: list candidates
    python tools/regrade_backlog.py --since 2026-10-14 --apply   # re-enqueue them
    python tools/regrade_backlog.py --ids 4543 4549 --apply      # explicit submissions

Candidates are real student submissions (not absence placeholders, not returned
for resubmission) on AI-graded assignments that are
  * ``grading_failed`` / ``grading_review`` (retries exhausted, no score written), or
  * ``grading`` while their durable job is no longer active (stale), or
  * ``submitted`` with no score (never picked up).
Drafts (``submission_drafts``) are never graded and never touched. A submission
held by a pending "错误提交" appeal is skipped and reported.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ACTIVE_JOB_STATUSES = ("queued", "retry_wait", "running", "result_ready")
AI_GRADING_MODES = ("ai", "auto", "mixed")


def _latest_job(conn, submission_id: int):
    return conn.execute(
        "SELECT id, status, attempt_count, max_attempts, last_error_code, last_error, updated_at FROM ai_jobs "
        "WHERE task_type = 'ai_grading' AND source_ref = ? ORDER BY id DESC LIMIT 1",
        (f"submission:{int(submission_id)}",),
    ).fetchone()


def find_candidates(conn, *, since: str | None, ids: list[int] | None) -> list[dict]:
    from classroom_app.services.assessment_appeal_service import submission_grading_hold

    params: list = []
    where = ["COALESCE(s.resubmission_allowed, 0) = 0", "COALESCE(s.is_absence_score, 0) = 0"]
    if ids:
        where.append("s.id IN (" + ",".join("?" for _ in ids) + ")")
        params.extend(int(item) for item in ids)
    else:
        where.append("(s.status IN ('grading_failed', 'grading_review', 'grading') OR (s.status = 'submitted' AND s.score IS NULL))")
        if since:
            where.append("s.submitted_at >= ?")
            params.append(since)
    rows = conn.execute(
        "SELECT s.id, s.assignment_id, s.student_pk_id, s.student_name, s.status, s.score, s.submitted_at, s.grading_job_id, "
        "a.title, a.grading_mode FROM submissions s JOIN assignments a ON a.id = s.assignment_id "
        f"WHERE {' AND '.join(where)} ORDER BY s.submitted_at, s.id",
        tuple(params),
    ).fetchall()
    candidates = []
    for row in rows:
        item = dict(row)
        mode = str(item.get("grading_mode") or "").strip().lower()
        if not ids and mode not in AI_GRADING_MODES:
            continue
        job = _latest_job(conn, int(item["id"]))
        item["job"] = dict(job) if job else None
        status = str(item["status"] or "")
        if status == "grading" and job and str(job["status"]) in ACTIVE_JOB_STATUSES:
            item["action"] = "skip: job still active"
        elif status == "graded" and not ids:
            item["action"] = "skip: already graded"
        else:
            hold = submission_grading_hold(conn, int(item["id"]))
            if hold:
                item["action"] = f"skip: held by pending appeal ({hold})"
            else:
                item["action"] = "force-regrade" if status == "grading" else "regrade"
        candidates.append(item)
    return candidates


def describe(item: dict) -> str:
    job = item.get("job") or {}
    job_text = (f"job#{job.get('id')} {job.get('status')} att={job.get('attempt_count')}/{job.get('max_attempts')} "
                f"err={job.get('last_error_code') or '-'}") if job else "no job"
    return (f"submission#{item['id']} assignment#{item['assignment_id']} {item.get('student_name') or ''} "
            f"status={item['status']} score={item.get('score')} submitted={item.get('submitted_at')} | {job_text} -> {item['action']}")


async def apply(candidates: list[dict]) -> None:
    from classroom_app.services.ai_grading_service import (
        AIGradingQueueError, force_submit_submission_for_ai_grading, submit_submission_for_ai_grading,
    )

    for item in candidates:
        if item["action"] not in {"regrade", "force-regrade"}:
            continue
        try:
            if item["action"] == "force-regrade":
                result = await force_submit_submission_for_ai_grading(int(item["id"]))
            else:
                result = await submit_submission_for_ai_grading(int(item["id"]), allow_graded=False)
            print(f"  queued submission#{item['id']}: {result.get('status') if isinstance(result, dict) else result}")
        except AIGradingQueueError as exc:
            print(f"  FAILED submission#{item['id']}: HTTP {exc.status_code} {exc.detail}")
        except Exception as exc:  # noqa: BLE001 - keep going, report at the end
            print(f"  FAILED submission#{item['id']}: {exc!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", help="only submissions submitted at/after this ISO date, e.g. 2026-10-14")
    parser.add_argument("--ids", nargs="*", type=int, help="explicit submission ids (bypasses the status filter)")
    parser.add_argument("--apply", action="store_true", help="actually re-enqueue; default is a dry run")
    args = parser.parse_args()
    if not args.since and not args.ids:
        parser.error("give --since DATE or --ids ...")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    from classroom_app.database import get_db_connection

    with get_db_connection() as conn:
        candidates = find_candidates(conn, since=args.since, ids=args.ids)
    if not candidates:
        print("no candidates")
        return 0
    for item in candidates:
        print(describe(item))
    actionable = [item for item in candidates if item["action"] in {"regrade", "force-regrade"}]
    print(f"{len(candidates)} candidate(s), {len(actionable)} to re-enqueue")
    if not args.apply:
        print("dry run; add --apply to re-enqueue")
        return 0
    asyncio.run(apply(actionable))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
