"""Group-based assignment / exam completion + peer evaluation + blended scoring.

A teacher can bind an assignment (or exam) to a *group scheme* (from the
collaboration area). When bound:

* Each group member still submits their **own** work and gets their **own** AI
  score (the existing per-student submission + grading pipeline is unchanged).
* At submit time the student rates every teammate's contribution on a 20-point
  scale. If they close the page without rating, a fair default of 16 is filled
  in automatically (see :data:`DEFAULT_PEER_POINTS`).
* AI grading runs as soon as each student submits ("先提交先评分"), but the
  resulting score is **withheld** from the student until the *whole group* has
  submitted and been graded.
* Once every active member is graded, each member's final score is computed as::

      final = round(work_score * 0.8 + peer_avg, 2)

  where ``peer_avg`` is the average of the 20-point ratings the member received
  from teammates (a solo member, with no teammates, receives the default). All
  members' final scores are then revealed at once.
* A student only ever sees their own blended **综合表现分** — never the
  individual ratings teammates gave them.

This module is the single source of truth for that lifecycle. It is engine
agnostic (plain SQL that runs on both SQLite and PostgreSQL) and uses
``group_assignment_member_results`` to persist the *raw* work score so that
re-grading / re-finalization never double-blends an already-blended score.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from ..db.connection import execute_insert_returning_id
from ..db.schema_study_group_scheme import ensure_study_group_scheme_schema

# --- Scoring constants -------------------------------------------------------
DEFAULT_PEER_POINTS = 16          # fair default when a rater never rates a teammate
PEER_POINTS_MIN = 1
PEER_POINTS_MAX = 20
WORK_WEIGHT = 0.8                 # 作业得分 weight; peer (0-20) fills the remaining 20%
SCORE_MIN = 0.0
SCORE_MAX = 100.0

BINDING_STATUS_ACTIVE = "active"
BINDING_STATUS_REMOVED = "removed"

# Submission statuses that count as "this member's work is graded".
_GRADED_STATUSES = {"graded"}


def _now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _safe_int(value: Any) -> Optional[int]:
    try:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_assignment_id(value: Any) -> str:
    return str(value or "").strip()


def clamp_peer_points(value: Any) -> int:
    """Coerce an arbitrary value into the valid 1..20 contribution range."""
    points = _safe_int(value)
    if points is None:
        return DEFAULT_PEER_POINTS
    return max(PEER_POINTS_MIN, min(PEER_POINTS_MAX, points))


# =============================================================================
# Binding (assignment <-> scheme)
# =============================================================================
def get_assignment_group_binding(conn, assignment_id: Any) -> Optional[dict[str, Any]]:
    """Return the active group binding for an assignment, or ``None``."""
    assignment_id = _normalize_assignment_id(assignment_id)
    if not assignment_id:
        return None
    ensure_study_group_scheme_schema(conn)
    row = conn.execute(
        """
        SELECT b.*, s.name AS scheme_name, s.status AS scheme_status,
               s.group_count AS scheme_group_count
        FROM assignment_group_bindings b
        LEFT JOIN group_schemes s ON s.id = b.scheme_id
        WHERE b.assignment_id = ?
          AND b.status = ?
        LIMIT 1
        """,
        (assignment_id, BINDING_STATUS_ACTIVE),
    ).fetchone()
    return dict(row) if row else None


def is_group_assignment(conn, assignment_id: Any) -> bool:
    return get_assignment_group_binding(conn, assignment_id) is not None


def bind_assignment_to_scheme(
    conn,
    *,
    assignment_id: Any,
    class_offering_id: int,
    scheme_id: int,
    teacher_id: int,
) -> dict[str, Any]:
    """Bind (or rebind) an assignment to a group scheme.

    Validates that the scheme belongs to the same class offering as the
    assignment. Idempotent: an existing binding is updated in place.
    """
    ensure_study_group_scheme_schema(conn)
    assignment_id = _normalize_assignment_id(assignment_id)
    scheme_id = int(scheme_id)
    class_offering_id = int(class_offering_id)

    scheme = conn.execute(
        "SELECT id, class_offering_id, status FROM group_schemes WHERE id = ? LIMIT 1",
        (scheme_id,),
    ).fetchone()
    if scheme is None:
        raise ValueError("分组方案不存在")
    if int(scheme["class_offering_id"]) != class_offering_id:
        raise ValueError("分组方案不属于当前课堂")

    now = _now_iso()
    existing = conn.execute(
        "SELECT id FROM assignment_group_bindings WHERE assignment_id = ? LIMIT 1",
        (assignment_id,),
    ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE assignment_group_bindings
            SET scheme_id = ?, class_offering_id = ?, status = ?,
                created_by_teacher_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (scheme_id, class_offering_id, BINDING_STATUS_ACTIVE, int(teacher_id), now, int(existing["id"])),
        )
    else:
        execute_insert_returning_id(
            conn,
            """
            INSERT INTO assignment_group_bindings (
                assignment_id, class_offering_id, scheme_id, status,
                created_by_teacher_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (assignment_id, class_offering_id, scheme_id, BINDING_STATUS_ACTIVE, int(teacher_id), now, now),
        )
    binding = get_assignment_group_binding(conn, assignment_id)
    return binding or {}


def unbind_assignment(conn, *, assignment_id: Any) -> bool:
    """Remove the group binding for an assignment (keeps history rows)."""
    ensure_study_group_scheme_schema(conn)
    assignment_id = _normalize_assignment_id(assignment_id)
    cursor = conn.execute(
        "UPDATE assignment_group_bindings SET status = ?, updated_at = ? WHERE assignment_id = ? AND status = ?",
        (BINDING_STATUS_REMOVED, _now_iso(), assignment_id, BINDING_STATUS_ACTIVE),
    )
    return bool(cursor.rowcount)


# =============================================================================
# Group membership lookups
# =============================================================================
def _scheme_group_for_student(conn, scheme_id: int, student_pk_id: int) -> Optional[dict[str, Any]]:
    row = conn.execute(
        """
        SELECT g.id, g.name, g.group_index
        FROM study_group_members m
        JOIN study_groups g ON g.id = m.group_id
        WHERE g.scheme_id = ?
          AND m.student_id = ?
          AND m.status = 'active'
        LIMIT 1
        """,
        (int(scheme_id), int(student_pk_id)),
    ).fetchone()
    return dict(row) if row else None


def _active_group_members(conn, group_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT m.student_id, s.name AS student_name, s.avatar_file_hash,
               m.member_role
        FROM study_group_members m
        JOIN students s ON s.id = m.student_id
        WHERE m.group_id = ?
          AND m.status = 'active'
        ORDER BY CASE m.member_role WHEN 'leader' THEN 0 ELSE 1 END, s.student_id_number, s.id
        """,
        (int(group_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def _avatar_url(student_pk_id: int, avatar_hash: Any) -> str:
    revision = str(avatar_hash or "default")
    return f"/api/profile/avatar?role=student&user_id={int(student_pk_id)}&v={revision}"


def get_student_group_context(conn, assignment_id: Any, student_pk_id: int) -> Optional[dict[str, Any]]:
    """For a group assignment, return the student's group + teammates.

    Returns ``None`` when the assignment is not group-bound or the student is
    not assigned to any group of the bound scheme (the teacher must assign /
    the student must self-join first).
    """
    binding = get_assignment_group_binding(conn, assignment_id)
    if not binding:
        return None
    group = _scheme_group_for_student(conn, int(binding["scheme_id"]), int(student_pk_id))
    if not group:
        return {
            "binding": binding,
            "group": None,
            "members": [],
            "peers": [],
            "in_group": False,
        }
    members = _active_group_members(conn, int(group["id"]))
    peers = [
        {
            "student_id": int(m["student_id"]),
            "name": str(m["student_name"] or "同学"),
            "avatar_url": _avatar_url(int(m["student_id"]), m.get("avatar_file_hash")),
        }
        for m in members
        if int(m["student_id"]) != int(student_pk_id)
    ]
    return {
        "binding": binding,
        "group": {"id": int(group["id"]), "name": str(group["name"] or ""), "group_index": group.get("group_index")},
        "members": members,
        "peers": peers,
        "in_group": True,
    }


# =============================================================================
# Peer contribution ratings (stored in peer_reviews.contribution_points)
# =============================================================================
def _upsert_contribution(
    conn,
    *,
    class_offering_id: int,
    group_id: int,
    assignment_id: str,
    reviewer_id: int,
    reviewee_id: int,
    points: int,
    is_auto: bool,
    only_if_missing: bool = False,
) -> None:
    now = _now_iso()
    existing = conn.execute(
        """
        SELECT id, contribution_points FROM peer_reviews
        WHERE group_id = ? AND assignment_id = ?
          AND reviewer_student_id = ? AND reviewee_student_id = ?
        LIMIT 1
        """,
        (int(group_id), assignment_id, int(reviewer_id), int(reviewee_id)),
    ).fetchone()
    if existing:
        # Never overwrite an existing rating when we're only filling defaults.
        if only_if_missing and existing["contribution_points"] is not None:
            return
        conn.execute(
            """
            UPDATE peer_reviews
            SET contribution_points = ?, is_auto_default = ?, status = 'submitted', updated_at = ?
            WHERE id = ?
            """,
            (int(points), 1 if is_auto else 0, now, int(existing["id"])),
        )
        return
    execute_insert_returning_id(
        conn,
        """
        INSERT INTO peer_reviews (
            class_offering_id, group_id, assignment_id,
            reviewer_student_id, reviewee_student_id,
            responsibility_score, collaboration_score, quality_score,
            contribution_points, is_auto_default, comment, share_with_reviewee,
            status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 0, 0, 0, ?, ?, '', 0, 'submitted', ?, ?)
        """,
        (
            int(class_offering_id), int(group_id), assignment_id,
            int(reviewer_id), int(reviewee_id),
            int(points), 1 if is_auto else 0, now, now,
        ),
    )


def ensure_default_peer_contributions(
    conn,
    *,
    assignment_id: Any,
    group_id: int,
    reviewer_id: int,
) -> int:
    """Safety net: fill DEFAULT_PEER_POINTS for any teammate this reviewer has
    not yet rated (e.g. they submitted then closed the page). Never overwrites
    a real rating. Returns the number of defaults written."""
    assignment_id = _normalize_assignment_id(assignment_id)
    members = _active_group_members(conn, int(group_id))
    if not members:
        return 0
    class_offering_id = _group_class_offering_id(conn, int(group_id))
    written = 0
    for member in members:
        reviewee_id = int(member["student_id"])
        if reviewee_id == int(reviewer_id):
            continue
        before = conn.execute(
            """
            SELECT contribution_points FROM peer_reviews
            WHERE group_id = ? AND assignment_id = ?
              AND reviewer_student_id = ? AND reviewee_student_id = ?
            LIMIT 1
            """,
            (int(group_id), assignment_id, int(reviewer_id), reviewee_id),
        ).fetchone()
        if before and before["contribution_points"] is not None:
            continue
        _upsert_contribution(
            conn,
            class_offering_id=class_offering_id,
            group_id=int(group_id),
            assignment_id=assignment_id,
            reviewer_id=int(reviewer_id),
            reviewee_id=reviewee_id,
            points=DEFAULT_PEER_POINTS,
            is_auto=True,
            only_if_missing=True,
        )
        written += 1
    return written


def submit_peer_contributions(
    conn,
    *,
    assignment_id: Any,
    reviewer_id: int,
    ratings: dict,
) -> dict[str, Any]:
    """Persist a student's 20-point ratings of their teammates for a group
    assignment. Validates group membership server-side. Any teammate omitted
    from ``ratings`` is filled with the fair default."""
    assignment_id = _normalize_assignment_id(assignment_id)
    context = get_student_group_context(conn, assignment_id, int(reviewer_id))
    if not context or not context.get("in_group"):
        raise ValueError("当前作业未分组，或你尚未加入小组")
    group_id = int(context["group"]["id"])
    class_offering_id = _group_class_offering_id(conn, group_id)
    peer_ids = {int(p["student_id"]) for p in context["peers"]}
    saved = 0
    for reviewee_id, points in (ratings or {}).items():
        rid = _safe_int(reviewee_id)
        if rid is None or rid not in peer_ids:
            continue
        _upsert_contribution(
            conn,
            class_offering_id=class_offering_id,
            group_id=group_id,
            assignment_id=assignment_id,
            reviewer_id=int(reviewer_id),
            reviewee_id=rid,
            points=clamp_peer_points(points),
            is_auto=False,
        )
        saved += 1
    # Fill defaults for any teammate the student skipped.
    ensure_default_peer_contributions(
        conn, assignment_id=assignment_id, group_id=group_id, reviewer_id=int(reviewer_id)
    )
    return {"saved": saved, "group_id": group_id, "peer_count": len(peer_ids)}


def _group_class_offering_id(conn, group_id: int) -> int:
    row = conn.execute(
        "SELECT class_offering_id FROM study_groups WHERE id = ? LIMIT 1",
        (int(group_id),),
    ).fetchone()
    if not row:
        raise ValueError("小组不存在")
    return int(row["class_offering_id"])


# =============================================================================
# Member-result ledger + finalization
# =============================================================================
def _load_member_result(conn, assignment_id: str, student_pk_id: int) -> Optional[dict[str, Any]]:
    row = conn.execute(
        """
        SELECT * FROM group_assignment_member_results
        WHERE assignment_id = ? AND student_pk_id = ?
        LIMIT 1
        """,
        (assignment_id, int(student_pk_id)),
    ).fetchone()
    return dict(row) if row else None


def _upsert_member_result(
    conn,
    *,
    assignment_id: str,
    class_offering_id: int,
    group_id: int,
    student_pk_id: int,
    submission_id: Optional[int],
    work_score: Optional[float],
    peer_avg: Optional[float] = None,
    peer_review_count: Optional[int] = None,
    final_score: Optional[float] = None,
    revealed: Optional[int] = None,
    finalized_at: Optional[str] = None,
) -> None:
    now = _now_iso()
    existing = _load_member_result(conn, assignment_id, int(student_pk_id))
    if existing:
        conn.execute(
            """
            UPDATE group_assignment_member_results
            SET class_offering_id = ?, group_id = ?,
                submission_id = COALESCE(?, submission_id),
                work_score = COALESCE(?, work_score),
                peer_avg = COALESCE(?, peer_avg),
                peer_review_count = COALESCE(?, peer_review_count),
                final_score = COALESCE(?, final_score),
                revealed = COALESCE(?, revealed),
                finalized_at = COALESCE(?, finalized_at),
                updated_at = ?
            WHERE id = ?
            """,
            (
                int(class_offering_id), int(group_id),
                submission_id, work_score, peer_avg, peer_review_count,
                final_score, revealed, finalized_at, now, int(existing["id"]),
            ),
        )
        return
    execute_insert_returning_id(
        conn,
        """
        INSERT INTO group_assignment_member_results (
            assignment_id, class_offering_id, group_id, student_pk_id, submission_id,
            work_score, peer_avg, peer_review_count, final_score, revealed,
            finalized_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            assignment_id, int(class_offering_id), int(group_id), int(student_pk_id), submission_id,
            work_score, peer_avg, peer_review_count or 0, final_score,
            revealed if revealed is not None else 0, finalized_at, now, now,
        ),
    )


def _load_submission_for_member(conn, assignment_id: str, student_pk_id: int) -> Optional[dict[str, Any]]:
    row = conn.execute(
        """
        SELECT id, status, score, is_absence_score, resubmission_allowed
        FROM submissions
        WHERE assignment_id = ? AND student_pk_id = ?
        LIMIT 1
        """,
        (assignment_id, int(student_pk_id)),
    ).fetchone()
    return dict(row) if row else None


def record_member_work_score(conn, submission_id: int) -> dict[str, Any]:
    """Called after a submission is graded. If the submission belongs to a
    group assignment, persist its *raw* work score into the member-result
    ledger and attempt to finalize the group. Safe no-op for non-group work."""
    submission = conn.execute(
        "SELECT id, assignment_id, student_pk_id, status, score, is_absence_score FROM submissions WHERE id = ? LIMIT 1",
        (int(submission_id),),
    ).fetchone()
    if not submission:
        return {"handled": False}
    submission = dict(submission)
    assignment_id = _normalize_assignment_id(submission["assignment_id"])
    binding = get_assignment_group_binding(conn, assignment_id)
    if not binding:
        return {"handled": False}
    student_pk_id = int(submission["student_pk_id"])
    group = _scheme_group_for_student(conn, int(binding["scheme_id"]), student_pk_id)
    if not group:
        # Student graded but not assigned to any group of the scheme — record
        # nothing; finalization for their (absent) group cannot include them.
        return {"handled": True, "in_group": False}
    if submission.get("score") is None:
        return {"handled": True, "in_group": True, "recorded": False}
    _upsert_member_result(
        conn,
        assignment_id=assignment_id,
        class_offering_id=int(binding["class_offering_id"]),
        group_id=int(group["id"]),
        student_pk_id=student_pk_id,
        submission_id=int(submission["id"]),
        work_score=float(submission["score"]),
        revealed=0,
    )
    return try_finalize_group(conn, assignment_id=assignment_id, group_id=int(group["id"]))


def _resolve_member_work_score(conn, assignment_id: str, group_id: int, class_offering_id: int, student_pk_id: int) -> Optional[float]:
    """Return the stable raw work score for a member, or ``None`` if their work
    is not yet graded. Reads the persisted ledger first (immune to the
    finalization overwrite of ``submissions.score``); falls back to an
    absence score recorded by the teacher."""
    result = _load_member_result(conn, assignment_id, student_pk_id)
    if result and result.get("work_score") is not None:
        return float(result["work_score"])
    submission = _load_submission_for_member(conn, assignment_id, student_pk_id)
    if not submission:
        return None
    status = str(submission.get("status") or "").strip().lower()
    score = submission.get("score")
    is_absence = bool(submission.get("is_absence_score"))
    if score is not None and (status in _GRADED_STATUSES or is_absence):
        # Persist so future reads are stable even after the score is overwritten.
        _upsert_member_result(
            conn,
            assignment_id=assignment_id,
            class_offering_id=int(class_offering_id),
            group_id=int(group_id),
            student_pk_id=int(student_pk_id),
            submission_id=_safe_int(submission.get("id")),
            work_score=float(score),
            revealed=0,
        )
        return float(score)
    return None


def _received_peer_avg(conn, assignment_id: str, group_id: int, reviewee_id: int, peer_ids: list) -> tuple:
    """Average of the 20-point ratings ``reviewee_id`` received from teammates.
    Missing ratings are treated as the fair default (defensive — the safety net
    should already have filled them)."""
    if not peer_ids:
        return float(DEFAULT_PEER_POINTS), 0
    total = 0.0
    count = 0
    for reviewer_id in peer_ids:
        row = conn.execute(
            """
            SELECT contribution_points FROM peer_reviews
            WHERE group_id = ? AND assignment_id = ?
              AND reviewer_student_id = ? AND reviewee_student_id = ?
            LIMIT 1
            """,
            (int(group_id), assignment_id, int(reviewer_id), int(reviewee_id)),
        ).fetchone()
        points = row["contribution_points"] if row else None
        total += float(points) if points is not None else float(DEFAULT_PEER_POINTS)
        count += 1
    return (total / count if count else float(DEFAULT_PEER_POINTS)), count


def compute_final_score(work_score: float, peer_avg: float) -> float:
    blended = float(work_score) * WORK_WEIGHT + float(peer_avg)
    blended = max(SCORE_MIN, min(SCORE_MAX, blended))
    return round(blended, 2)


def try_finalize_group(conn, *, assignment_id: Any, group_id: int) -> dict[str, Any]:
    """Finalize a group's scores **iff** every active member has been graded.

    Idempotent and re-entrant: always recomputes from the persisted raw work
    scores, so re-grading a member and re-running this safely updates everyone.
    """
    assignment_id = _normalize_assignment_id(assignment_id)
    binding = get_assignment_group_binding(conn, assignment_id)
    if not binding:
        return {"handled": False}
    class_offering_id = int(binding["class_offering_id"])
    members = _active_group_members(conn, int(group_id))
    if not members:
        return {"handled": True, "finalized": False, "reason": "no_members"}

    member_ids = [int(m["student_id"]) for m in members]
    work_scores: dict = {}
    for student_pk_id in member_ids:
        score = _resolve_member_work_score(conn, assignment_id, int(group_id), class_offering_id, student_pk_id)
        if score is None:
            return {
                "handled": True,
                "finalized": False,
                "reason": "pending_members",
                "graded_count": len(work_scores),
                "member_count": len(member_ids),
            }
        work_scores[student_pk_id] = score

    # Everyone is graded — guarantee a complete peer-rating matrix, then blend.
    for reviewer_id in member_ids:
        ensure_default_peer_contributions(
            conn, assignment_id=assignment_id, group_id=int(group_id), reviewer_id=reviewer_id
        )

    now = _now_iso()
    finalized = []
    for member in members:
        student_pk_id = int(member["student_id"])
        peers = [mid for mid in member_ids if mid != student_pk_id]
        peer_avg, peer_count = _received_peer_avg(conn, assignment_id, int(group_id), student_pk_id, peers)
        work_score = work_scores[student_pk_id]
        final_score = compute_final_score(work_score, peer_avg)
        submission = _load_submission_for_member(conn, assignment_id, student_pk_id)
        submission_id = _safe_int(submission.get("id")) if submission else None
        _upsert_member_result(
            conn,
            assignment_id=assignment_id,
            class_offering_id=class_offering_id,
            group_id=int(group_id),
            student_pk_id=student_pk_id,
            submission_id=submission_id,
            work_score=work_score,
            peer_avg=round(peer_avg, 2),
            peer_review_count=peer_count,
            final_score=final_score,
            revealed=1,
            finalized_at=now,
        )
        # Overwrite the visible submission score with the blended final so every
        # existing read path shows the 综合表现分. The raw work score is safe in
        # the ledger above.
        if submission_id is not None:
            _apply_final_to_submission(conn, submission_id, final_score, work_score, peer_avg)
        finalized.append({"student_pk_id": student_pk_id, "final_score": final_score})

    _notify_group_finalized(conn, assignment_id, class_offering_id, members)
    return {
        "handled": True,
        "finalized": True,
        "group_id": int(group_id),
        "results": finalized,
    }


_FINAL_FEEDBACK_MARKER = "<!-- group-final -->"


def _apply_final_to_submission(conn, submission_id: int, final_score: float, work_score: float, peer_avg: float) -> None:
    row = conn.execute(
        "SELECT feedback_md FROM submissions WHERE id = ? LIMIT 1",
        (int(submission_id),),
    ).fetchone()
    feedback = str(row["feedback_md"] or "") if row else ""
    # Strip any previous group-final block so re-finalization stays clean.
    if _FINAL_FEEDBACK_MARKER in feedback:
        feedback = feedback.split(_FINAL_FEEDBACK_MARKER, 1)[0].rstrip()
    summary = (
        f"\n\n{_FINAL_FEEDBACK_MARKER}\n"
        f"**综合表现分：{final_score}**\n\n"
        f"（综合表现分 = 作业得分 × {WORK_WEIGHT:g} + 组员评分均分，已结合小组协作表现。）"
    )
    conn.execute(
        "UPDATE submissions SET score = ?, status = 'graded', feedback_md = ? WHERE id = ?",
        (final_score, (feedback + summary).strip(), int(submission_id)),
    )


def _notify_group_finalized(conn, assignment_id: str, class_offering_id: int, members: list) -> None:
    try:
        from .message_center_service import create_collaboration_notification

        title = conn.execute(
            "SELECT title FROM assignments WHERE id = ? LIMIT 1",
            (assignment_id,),
        ).fetchone()
        assignment_title = str(title["title"]) if title else "小组作业"
        for member in members:
            create_collaboration_notification(
                conn,
                recipient_role="student",
                recipient_user_pk=int(member["student_id"]),
                title="小组作业成绩已揭晓",
                body_preview=f"《{assignment_title}》全组已完成，你的综合表现分已生成。",
                link_url=f"/assignment/{assignment_id}",
                class_offering_id=int(class_offering_id),
                ref_id=f"group-final:{assignment_id}:{int(member['student_id'])}",
                allow_duplicates=False,
            )
    except Exception as exc:  # best-effort; never block finalization
        print(f"[GROUP_ASSIGNMENT] finalize notification failed: {exc}")


# =============================================================================
# Student-facing display helpers
# =============================================================================
def get_student_display_state(conn, assignment_id: Any, student_pk_id: int) -> Optional[dict[str, Any]]:
    """Return how a group assignment's score should be presented to a student.

    ``None`` => not a group assignment (normal display).
    Otherwise a dict with:
      * ``is_group``: True
      * ``in_group``: whether the student is assigned to a group
      * ``revealed``: whether final scores are released
      * ``final_score``: blended 综合表现分 (only when revealed)
      * ``pending``: graded but waiting for teammates
    """
    binding = get_assignment_group_binding(conn, assignment_id)
    if not binding:
        return None
    assignment_id = _normalize_assignment_id(assignment_id)
    result = _load_member_result(conn, assignment_id, int(student_pk_id))
    submission = _load_submission_for_member(conn, assignment_id, int(student_pk_id))
    group = _scheme_group_for_student(conn, int(binding["scheme_id"]), int(student_pk_id))
    revealed = bool(result and int(result.get("revealed") or 0))
    work_graded = bool(result and result.get("work_score") is not None) or (
        submission is not None
        and submission.get("score") is not None
        and str(submission.get("status") or "").lower() in _GRADED_STATUSES
    )
    return {
        "is_group": True,
        "in_group": group is not None,
        "group_name": str(group["name"]) if group else "",
        "revealed": revealed,
        "final_score": float(result["final_score"]) if (revealed and result and result.get("final_score") is not None) else None,
        # "pending" = the student's own work is graded but the group reveal is
        # still waiting on teammates.
        "pending": bool(work_graded and not revealed),
    }
