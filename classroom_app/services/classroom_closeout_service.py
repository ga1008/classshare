"""结课（end-of-term closeout）汇总与批量收尾。

学期结束时教师需要把一个课堂里所有"过程性"任务一次性收口：还没截止的作业/
测验、还在进行的投票、还没归档的分组方案、课堂互动、举手求助与提问。

本模块只做两件事：

1. :func:`build_closeout_summary` —— 只读地扫描课堂，产出可直接渲染成卡片的
   未结束任务清单（含未提交/未批改人数等决策所需的计数）。
2. :func:`execute_closeout` —— 按教师在弹窗里的选择，把这些任务逐个扭转到
   截止/停止状态；作业与测验可同时给未提交者写"缺交"默认分。

设计上的两条硬约束：

* **绝不静默给已提交的作业打分。** 未提交者记默认分是安全的（本来就没有成绩），
  但"已提交未批改"的分数属于真实学业评价，只有教师显式打开
  ``include_ungraded`` 才会被写入，否则只在卡片上作为风险提示呈现。
* **单个任务失败不拖垮整场结课。** 每个条目独立 try/except，失败进 ``failures``
  列表返回给前端，其余照常收尾。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Optional

from .assignment_lifecycle_service import (
    ASSIGNMENT_STATUS_CLOSED,
    ASSIGNMENT_STATUS_PUBLISHED,
    close_overdue_assignments,
)
from .assignment_reminder_service import cancel_assignment_due_reminders
from .offering_membership_service import offering_class_ids

# 结课卡片类别。作业与测验共用 assignments 表，靠 exam_paper_id 区分展示。
KIND_ASSIGNMENT = "assignment"
KIND_EXAM = "exam"
KIND_POLL = "poll"
KIND_GROUP_SCHEME = "group_scheme"
KIND_LIVE_ACTIVITY = "live_activity"
KIND_HELP_SIGNAL = "help_signal"
KIND_QUESTION = "question"

KIND_LABELS = {
    KIND_ASSIGNMENT: "作业",
    KIND_EXAM: "测验",
    KIND_POLL: "投票",
    KIND_GROUP_SCHEME: "分组方案",
    KIND_LIVE_ACTIVITY: "课堂互动",
    KIND_HELP_SIGNAL: "举手求助",
    KIND_QUESTION: "课堂提问",
}

# 只有作业/测验带默认分，其余类别是纯粹的状态扭转。
SCORABLE_KINDS = {KIND_ASSIGNMENT, KIND_EXAM}

DEFAULT_ABSENCE_SCORE = 0.0
MAX_ABSENCE_SCORE = 100.0

ABSENCE_FEEDBACK_ZERO = "未提交，按缺交记 0 分。"
ABSENCE_FEEDBACK_TEMPLATE = "未提交，按缺交记 {score} 分。"
UNGRADED_FEEDBACK_TEMPLATE = "结课收尾：已提交但未批改，按默认分 {score} 分记录。"


def _now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_absence_score(raw: Any, *, default: float = DEFAULT_ABSENCE_SCORE) -> float:
    """把教师输入（滑块或输入框）收敛成 0..100 的分数。

    非法输入退回 ``default`` 而不是报错——结课是批量操作，为一个格式问题整场
    失败对教师毫无价值。整数值以 int 存储，与既有"缺交记 0"写入保持一致。
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        value = float(default)
    else:
        try:
            value = float(str(raw).strip())
        except (TypeError, ValueError):
            value = float(default)
    if value != value or value in (float("inf"), float("-inf")):  # NaN / inf
        value = float(default)
    return max(0.0, min(MAX_ABSENCE_SCORE, value))


def _score_for_storage(score: float) -> Any:
    """整数分存 int，避免在 score 为整型列的后端上出现 0.0 这类写入。"""
    if float(score).is_integer():
        return int(score)
    return float(score)


def _absence_feedback(score: float) -> str:
    if float(score) == 0.0:
        return ABSENCE_FEEDBACK_ZERO
    return ABSENCE_FEEDBACK_TEMPLATE.format(score=_score_for_storage(score))


# --------------------------------------------------------------------------
# 只读扫描
# --------------------------------------------------------------------------


def _offering_row(conn, class_offering_id: int) -> Optional[dict[str, Any]]:
    row = conn.execute(
        """
        SELECT o.id, o.course_id, o.class_id, o.teacher_id,
               c.name AS course_name, cl.name AS class_name
        FROM class_offerings o
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        WHERE o.id = ?
        """,
        (int(class_offering_id),),
    ).fetchone()
    return dict(row) if row else None


def _active_student_rows(conn, class_ids: Any) -> list[dict[str, Any]]:
    normalized_ids = [int(value) for value in (class_ids or []) if value]
    if not normalized_ids:
        return []
    placeholders = ",".join("?" for _ in normalized_ids)
    return [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT s.id, s.student_id_number, s.name
            FROM students s
            WHERE s.class_id IN ({placeholders})
              AND COALESCE(s.enrollment_status, 'active') = 'active'
            ORDER BY s.student_id_number, s.name
            """,
            tuple(normalized_ids),
        )
    ]


def _submission_rows(conn, assignment_id: Any) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT id, student_pk_id, status, score, is_absence_score, resubmission_allowed
            FROM submissions
            WHERE assignment_id = ?
            """,
            (assignment_id,),
        )
    ]


def _pick_primary_submission(rows: Iterable[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """一个学生可能有多行提交记录（缺交占位 + 真实提交）。真实提交优先。"""
    picked: dict[int, dict[str, Any]] = {}
    for row in rows:
        student_pk_id = _safe_int(row.get("student_pk_id"))
        if student_pk_id is None:
            continue
        current = picked.get(student_pk_id)
        row_is_absence = int(row.get("is_absence_score") or 0) == 1
        current_is_absence = current is not None and int(current.get("is_absence_score") or 0) == 1
        if current is None or (current_is_absence and not row_is_absence):
            picked[student_pk_id] = row
    return picked


def _assignment_progress(
    conn, assignment: dict[str, Any], student_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """统计一份作业的提交/批改分布，供卡片决策使用。"""
    by_student = _pick_primary_submission(_submission_rows(conn, assignment.get("id")))
    total = len(student_rows)

    unsubmitted = 0
    absence_scored = 0
    ungraded = 0
    graded = 0
    for student in student_rows:
        student_pk_id = _safe_int(student.get("id"))
        row = by_student.get(student_pk_id) if student_pk_id is not None else None
        if row is None:
            unsubmitted += 1
            continue
        if int(row.get("is_absence_score") or 0) == 1:
            absence_scored += 1
            continue
        status = str(row.get("status") or "").strip().lower()
        if status == "unsubmitted":
            unsubmitted += 1
        elif status == "graded":
            graded += 1
        else:
            ungraded += 1

    return {
        "total_students": total,
        "unsubmitted_count": unsubmitted,
        "absence_scored_count": absence_scored,
        "ungraded_count": ungraded,
        "graded_count": graded,
        "submitted_count": ungraded + graded,
    }


def _open_assignment_cards(conn, offering: dict[str, Any]) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, title, status, exam_paper_id, availability_mode, due_at, closed_at
        FROM assignments
        WHERE course_id = ? AND class_offering_id = ? AND status = ?
        ORDER BY COALESCE(due_at, created_at) ASC, id ASC
        """,
        (offering["course_id"], offering["id"], ASSIGNMENT_STATUS_PUBLISHED),
    ).fetchall()
    if not rows:
        return []

    student_rows = _active_student_rows(
        conn,
        offering_class_ids(conn, int(offering["id"])) or [offering.get("class_id")],
    )
    cards: list[dict[str, Any]] = []
    for row in rows:
        assignment = dict(row)
        is_exam = bool(str(assignment.get("exam_paper_id") or "").strip())
        kind = KIND_EXAM if is_exam else KIND_ASSIGNMENT
        progress = _assignment_progress(conn, assignment, student_rows)
        cards.append(
            {
                "kind": kind,
                "kind_label": KIND_LABELS[kind],
                "id": str(assignment["id"]),
                "title": assignment.get("title") or "未命名作业",
                "due_at": assignment.get("due_at"),
                "availability_mode": assignment.get("availability_mode"),
                "scorable": True,
                "default_score": DEFAULT_ABSENCE_SCORE,
                "detail_url": f"/assignments/{assignment['id']}/teacher",
                **progress,
            }
        )
    return cards


def _open_poll_cards(conn, offering: dict[str, Any]) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT p.id, p.title, p.status, p.deadline_at
        FROM polls p
        JOIN poll_assignments pa ON pa.poll_id = p.id
        WHERE pa.class_offering_id = ? AND p.status IN ('draft', 'active')
        GROUP BY p.id, p.title, p.status, p.deadline_at
        ORDER BY p.id ASC
        """,
        (offering["id"],),
    ).fetchall()
    cards = []
    for row in rows:
        poll = dict(row)
        voted_row = conn.execute(
            "SELECT COUNT(*) AS c FROM poll_ballots WHERE poll_id = ?",
            (int(poll["id"]),),
        ).fetchone()
        cards.append(
            {
                "kind": KIND_POLL,
                "kind_label": KIND_LABELS[KIND_POLL],
                "id": str(poll["id"]),
                "title": poll.get("title") or "投票",
                "status": poll.get("status"),
                "due_at": poll.get("deadline_at"),
                "scorable": False,
                "voted_count": int(dict(voted_row).get("c") or 0) if voted_row else 0,
            }
        )
    return cards


def _open_group_scheme_cards(conn, offering: dict[str, Any]) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, name, status, group_count, expires_at
        FROM group_schemes
        WHERE class_offering_id = ? AND status = 'active'
        ORDER BY id ASC
        """,
        (offering["id"],),
    ).fetchall()
    cards = []
    for row in rows:
        scheme = dict(row)
        cards.append(
            {
                "kind": KIND_GROUP_SCHEME,
                "kind_label": KIND_LABELS[KIND_GROUP_SCHEME],
                "id": str(scheme["id"]),
                "title": scheme.get("name") or "随机分组",
                "group_count": int(scheme.get("group_count") or 0),
                "due_at": scheme.get("expires_at"),
                "scorable": False,
            }
        )
    return cards


def _open_live_activity_cards(conn, offering: dict[str, Any]) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, kind, title, status
        FROM classroom_live_activities
        WHERE class_offering_id = ? AND status = 'active'
        ORDER BY id ASC
        """,
        (offering["id"],),
    ).fetchall()
    cards = []
    for row in rows:
        activity = dict(row)
        cards.append(
            {
                "kind": KIND_LIVE_ACTIVITY,
                "kind_label": KIND_LABELS[KIND_LIVE_ACTIVITY],
                "id": str(activity["id"]),
                "title": activity.get("title") or "课堂互动",
                "activity_kind": activity.get("kind"),
                "scorable": False,
            }
        )
    return cards


def _open_help_signal_cards(conn, offering: dict[str, Any]) -> list[dict[str, Any]]:
    row = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM classroom_live_help_signals
        WHERE class_offering_id = ? AND status = 'active'
        """,
        (offering["id"],),
    ).fetchone()
    count = int(dict(row).get("c") or 0) if row else 0
    if count <= 0:
        return []
    return [
        {
            "kind": KIND_HELP_SIGNAL,
            "kind_label": KIND_LABELS[KIND_HELP_SIGNAL],
            "id": "all",
            "title": f"{count} 条未处理的举手求助",
            "pending_count": count,
            "scorable": False,
        }
    ]


def _open_question_cards(conn, offering: dict[str, Any]) -> list[dict[str, Any]]:
    row = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM classroom_live_questions q
        JOIN classroom_live_activities a ON a.id = q.activity_id
        WHERE a.class_offering_id = ? AND q.status = 'open'
        """,
        (offering["id"],),
    ).fetchone()
    count = int(dict(row).get("c") or 0) if row else 0
    if count <= 0:
        return []
    return [
        {
            "kind": KIND_QUESTION,
            "kind_label": KIND_LABELS[KIND_QUESTION],
            "id": "all",
            "title": f"{count} 条未回应的课堂提问",
            "pending_count": count,
            "scorable": False,
        }
    ]


def build_closeout_summary(conn, class_offering_id: int, teacher_id: int) -> dict[str, Any]:
    """扫描课堂里所有仍未结束的过程性任务，返回可直接渲染的卡片清单。

    调用方负责鉴权（教师必须是本课堂授课教师）。这里只读，不写任何业务状态，
    唯一的例外是 :func:`close_overdue_assignments`——它把已过期的作业顺手落到
    closed，否则统计出来的"未截止"会包含实际早已过期的条目。
    """
    offering = _offering_row(conn, int(class_offering_id))
    if offering is None:
        return {"exists": False, "cards": [], "counts": {}, "total": 0}

    close_overdue_assignments(conn)

    cards: list[dict[str, Any]] = []
    cards.extend(_open_assignment_cards(conn, offering))
    cards.extend(_open_poll_cards(conn, offering))
    cards.extend(_open_group_scheme_cards(conn, offering))
    cards.extend(_open_live_activity_cards(conn, offering))
    cards.extend(_open_help_signal_cards(conn, offering))
    cards.extend(_open_question_cards(conn, offering))

    counts: dict[str, int] = {}
    for card in cards:
        counts[card["kind"]] = counts.get(card["kind"], 0) + 1

    pending_scores = sum(
        int(card.get("unsubmitted_count") or 0)
        for card in cards
        if card["kind"] in SCORABLE_KINDS
    )
    pending_grading = sum(
        int(card.get("ungraded_count") or 0)
        for card in cards
        if card["kind"] in SCORABLE_KINDS
    )

    return {
        "exists": True,
        "class_offering_id": int(class_offering_id),
        "course_name": offering.get("course_name"),
        "class_name": offering.get("class_name"),
        "cards": cards,
        "counts": counts,
        "total": len(cards),
        "pending_absence_score_count": pending_scores,
        "pending_grading_count": pending_grading,
        "default_absence_score": DEFAULT_ABSENCE_SCORE,
        "kind_labels": KIND_LABELS,
        "generated_at": _now_iso(),
    }


# --------------------------------------------------------------------------
# 状态扭转
# --------------------------------------------------------------------------


def apply_absence_scores(
    conn,
    assignment: dict[str, Any],
    *,
    teacher_id: int,
    score: float = DEFAULT_ABSENCE_SCORE,
    include_ungraded: bool = False,
    only_student_ids: set[int] | None = None,
    score_overrides: dict[int, float] | None = None,
    feedback_override: str = "",
) -> dict[str, Any]:
    """给未提交学生写"缺交"占位成绩。

    占位记录保持 ``status = 'unsubmitted'`` 且 ``is_absence_score = 1``，这样
    "缺交记分"与"真的提交了但得 0 分"在成绩单与导出里始终可区分。

    ``include_ungraded=True`` 时，已提交但未批改的提交也会被打上默认分——这是
    破坏性的（会顶掉真实批改的机会），只在教师显式勾选时才传入。
    """
    assignment_id = assignment.get("id")
    class_id = assignment.get("offering_class_id") or assignment.get("class_id")
    result: dict[str, Any] = {
        "created_count": 0,
        "updated_count": 0,
        "graded_count": 0,
        "skipped_count": 0,
        "affected_student_ids": [],
    }
    if not class_id:
        result["message"] = "当前作业未绑定班级，无法识别未提交学生"
        return result

    now_iso = _now_iso()

    offering_id_for_roster = int(assignment.get("class_offering_id") or 0)
    roster_class_ids = (
        offering_class_ids(conn, offering_id_for_roster) if offering_id_for_roster else []
    ) or [class_id]
    students = _active_student_rows(conn, roster_class_ids)
    by_student = _pick_primary_submission(_submission_rows(conn, assignment_id))
    affected: set[int] = set()

    for student in students:
        student_pk_id = _safe_int(student.get("id"))
        if student_pk_id is None:
            continue
        if only_student_ids is not None and student_pk_id not in only_student_ids:
            continue
        student_score = (score_overrides or {}).get(student_pk_id, score)
        normalized = normalize_absence_score(student_score)
        stored_score = _score_for_storage(normalized)
        if student_pk_id in (score_overrides or {}) and feedback_override:
            feedback = (
                feedback_override.format(score=stored_score)
                if "{score}" in feedback_override
                else feedback_override
            )
        else:
            feedback = _absence_feedback(normalized)
        ungraded_feedback = UNGRADED_FEEDBACK_TEMPLATE.format(score=stored_score)
        existing = by_student.get(student_pk_id)
        student_name = student.get("name") or ""

        if existing is None:
            conn.execute(
                """
                INSERT INTO submissions (
                    assignment_id, student_pk_id, student_name, status, score, feedback_md,
                    answers_json, submitted_by_role, submitted_by_teacher_id, submission_channel,
                    resubmission_allowed, resubmission_due_at, returned_at, returned_by_teacher_id,
                    returned_reason, is_absence_score, absence_scored_at, absence_scored_by_teacher_id,
                    submitted_at
                ) VALUES (?, ?, ?, 'unsubmitted', ?, ?, NULL, 'teacher', ?, 'absence_zero',
                          0, NULL, NULL, NULL, NULL, 1, ?, ?, ?)
                """,
                (
                    assignment_id,
                    student_pk_id,
                    student_name,
                    stored_score,
                    feedback,
                    int(teacher_id),
                    now_iso,
                    int(teacher_id),
                    now_iso,
                ),
            )
            result["created_count"] += 1
            affected.add(student_pk_id)
            continue

        status = str(existing.get("status") or "").strip().lower()
        is_absence = int(existing.get("is_absence_score") or 0) == 1

        if status == "unsubmitted":
            # 已有缺交占位也重写一遍：教师可能改了默认分再次执行。
            conn.execute(
                """
                UPDATE submissions
                SET student_name = ?,
                    status = 'unsubmitted',
                    score = ?,
                    feedback_md = ?,
                    submitted_by_role = 'teacher',
                    submitted_by_teacher_id = ?,
                    submission_channel = 'absence_zero',
                    resubmission_allowed = 0,
                    resubmission_due_at = NULL,
                    returned_at = NULL,
                    returned_by_teacher_id = NULL,
                    returned_reason = NULL,
                    is_absence_score = 1,
                    absence_scored_at = ?,
                    absence_scored_by_teacher_id = ?
                WHERE id = ?
                """,
                (
                    student_name,
                    stored_score,
                    feedback,
                    int(teacher_id),
                    now_iso,
                    int(teacher_id),
                    int(existing["id"]),
                ),
            )
            result["updated_count"] += 1
            affected.add(student_pk_id)
            continue

        if include_ungraded and not is_absence and status not in {"graded", "grading"}:
            conn.execute(
                """
                UPDATE submissions
                SET status = 'graded',
                    score = ?,
                    feedback_md = COALESCE(NULLIF(feedback_md, ''), ?),
                    grading_started_at = NULL,
                    grading_attempt_fingerprint = NULL,
                    resubmission_allowed = 0,
                    resubmission_due_at = NULL
                WHERE id = ?
                """,
                (stored_score, ungraded_feedback, int(existing["id"])),
            )
            result["graded_count"] += 1
            affected.add(student_pk_id)
            continue

        result["skipped_count"] += 1

    result["affected_student_ids"] = sorted(affected)
    return result


def close_assignment(
    conn,
    assignment: dict[str, Any],
    *,
    teacher_id: int,
    score: float = DEFAULT_ABSENCE_SCORE,
    apply_absence: bool = True,
    include_ungraded: bool = False,
    score_overrides: dict[int, float] | None = None,
    feedback_override: str = "",
) -> dict[str, Any]:
    """截止一份作业/测验，并（可选）给未提交者写默认分。

    幂等：已经是 closed 的作业只跳过状态更新，缺交补分仍会执行，方便教师改了
    默认分之后重跑。
    """
    assignment_id = assignment.get("id")
    now_iso = _now_iso()
    was_open = str(assignment.get("status") or "").strip().lower() != ASSIGNMENT_STATUS_CLOSED

    if was_open:
        conn.execute(
            """
            UPDATE assignments
            SET status = ?, closed_at = COALESCE(NULLIF(closed_at, ''), ?)
            WHERE id = ?
            """,
            (ASSIGNMENT_STATUS_CLOSED, now_iso, assignment_id),
        )
        cancel_assignment_due_reminders(conn, assignment_id)

    if apply_absence:
        effective_overrides = dict(score_overrides or {})
        effective_feedback = feedback_override
        if not effective_overrides:
            # 已确认的重修/插班学生用各自的默认平时分，而不是教师本次
            # 选择的兜底分。best-effort：查询失败按普通流程处理。
            try:
                from .classroom_retake_service import (
                    RETAKE_FEEDBACK_TEMPLATE,
                    get_confirmed_retake_students,
                )

                offering_id = assignment.get("class_offering_id")
                if offering_id:
                    confirmed = get_confirmed_retake_students(
                        conn, class_offering_id=int(offering_id)
                    )
                    if confirmed:
                        effective_overrides = {
                            item["student_id"]: item["default_ordinary_score"]
                            for item in confirmed
                        }
                        effective_feedback = feedback_override or RETAKE_FEEDBACK_TEMPLATE
            except Exception as exc:
                print(f"[RETAKE] 截止时读取插班生默认分失败: {exc}")
        scoring = apply_absence_scores(
            conn,
            assignment,
            teacher_id=teacher_id,
            score=score,
            include_ungraded=include_ungraded,
            score_overrides=effective_overrides or None,
            feedback_override=effective_feedback,
        )
    else:
        scoring = {
            "created_count": 0,
            "updated_count": 0,
            "graded_count": 0,
            "skipped_count": 0,
            "affected_student_ids": [],
        }

    return {
        "assignment_id": str(assignment_id),
        "closed": bool(was_open),
        "closed_at": now_iso if was_open else assignment.get("closed_at"),
        "default_score": _score_for_storage(normalize_absence_score(score)),
        **scoring,
    }


def refresh_learning_state(conn, class_offering_id: Any, student_ids: Iterable[int], ref: str) -> None:
    """成绩变化后刷新修为快照。best-effort：失败不影响结课本身。"""
    offering_id = _safe_int(class_offering_id)
    if offering_id is None:
        return
    try:
        from .learning_progress_service import refresh_student_learning_state
    except Exception as exc:  # pragma: no cover - import guard
        print(f"[CLOSEOUT] learning progress import failed: {exc}")
        return
    for student_pk_id in student_ids:
        try:
            refresh_student_learning_state(
                conn,
                offering_id,
                int(student_pk_id),
                event_source_ref=ref,
            )
        except Exception as exc:
            print(f"[CLOSEOUT] learning state refresh failed for {student_pk_id}: {exc}")


def _plan_for(payload: dict[str, Any], kind: str, card_id: str) -> dict[str, Any]:
    """取出教师在弹窗里为某张卡片设置的动作；未提及的卡片默认执行收尾。"""
    per_kind = payload.get(kind)
    if isinstance(per_kind, dict):
        entry = per_kind.get(str(card_id))
        if isinstance(entry, dict):
            return entry
        if entry is False:
            return {"action": "skip"}
    return {}


def execute_closeout(
    conn,
    class_offering_id: int,
    user: dict[str, Any],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把课堂内所有未结束的过程性任务批量收尾。

    ``payload`` 结构（全部可选，缺省即"全部收尾、默认分 0"）::

        {
          "default_score": 0,              # 作业/测验的兜底默认分
          "include_ungraded": false,       # 是否给"已提交未批改"也打默认分
          "assignment": {"<id>": {"action": "skip"}},          # 逐卡覆盖
          "exam":       {"<id>": {"default_score": 60}},
          "poll":       {"<id>": {"action": "skip"}},
          ...
        }

    返回逐类别的处理计数与 ``failures`` 明细；单条失败不会中断整场结课。
    """
    payload = dict(payload or {})
    teacher_id = int(user.get("id") or 0)
    fallback_score = normalize_absence_score(payload.get("default_score"))
    include_ungraded = bool(payload.get("include_ungraded"))

    summary = build_closeout_summary(conn, int(class_offering_id), teacher_id)
    if not summary.get("exists"):
        raise ValueError("未找到此课堂")

    processed: dict[str, int] = {}
    skipped: dict[str, int] = {}
    failures: list[dict[str, Any]] = []
    assignment_results: list[dict[str, Any]] = []
    affected_students: set[int] = set()

    def _mark(bucket: dict[str, int], kind: str) -> None:
        bucket[kind] = bucket.get(kind, 0) + 1

    for card in summary["cards"]:
        kind = card["kind"]
        card_id = card["id"]
        plan = _plan_for(payload, kind, card_id)
        if str(plan.get("action") or "").strip().lower() == "skip":
            _mark(skipped, kind)
            continue

        try:
            if kind in SCORABLE_KINDS:
                row = conn.execute(
                    """
                    SELECT a.*, o.class_id AS offering_class_id
                    FROM assignments a
                    LEFT JOIN class_offerings o ON o.id = a.class_offering_id
                    WHERE a.id = ?
                    """,
                    (card_id,),
                ).fetchone()
                if row is None:
                    raise ValueError("作业不存在")
                card_score = (
                    normalize_absence_score(plan.get("default_score"), default=fallback_score)
                    if "default_score" in plan
                    else fallback_score
                )
                outcome = close_assignment(
                    conn,
                    dict(row),
                    teacher_id=teacher_id,
                    score=card_score,
                    apply_absence=plan.get("apply_absence", True) is not False,
                    include_ungraded=bool(plan.get("include_ungraded", include_ungraded)),
                )
                outcome["title"] = card.get("title")
                assignment_results.append(outcome)
                affected_students.update(outcome.get("affected_student_ids") or [])

            elif kind == KIND_POLL:
                from . import poll_service

                poll_service.set_poll_status(
                    conn, int(card_id), user, poll_service.POLL_STATUS_CLOSED
                )

            elif kind == KIND_GROUP_SCHEME:
                from . import collaboration_service

                collaboration_service.close_group_scheme(conn, int(card_id), user)

            elif kind == KIND_LIVE_ACTIVITY:
                from . import classroom_interaction_service

                classroom_interaction_service.close_activity(conn, int(card_id), user)

            elif kind == KIND_HELP_SIGNAL:
                conn.execute(
                    """
                    UPDATE classroom_live_help_signals
                    SET status = 'resolved', updated_at = ?
                    WHERE class_offering_id = ? AND status = 'active'
                    """,
                    (_now_iso(), int(class_offering_id)),
                )

            elif kind == KIND_QUESTION:
                conn.execute(
                    """
                    UPDATE classroom_live_questions
                    SET status = 'addressed', updated_at = ?
                    WHERE status = 'open'
                      AND activity_id IN (
                          SELECT id FROM classroom_live_activities WHERE class_offering_id = ?
                      )
                    """,
                    (_now_iso(), int(class_offering_id)),
                )

            else:
                _mark(skipped, kind)
                continue

            _mark(processed, kind)
        except Exception as exc:
            failures.append(
                {
                    "kind": kind,
                    "kind_label": KIND_LABELS.get(kind, kind),
                    "id": str(card_id),
                    "title": card.get("title"),
                    "error": str(exc) or exc.__class__.__name__,
                }
            )

    if affected_students:
        refresh_learning_state(
            conn,
            class_offering_id,
            affected_students,
            f"closeout:{class_offering_id}",
        )

    return {
        "class_offering_id": int(class_offering_id),
        "processed": processed,
        "processed_total": sum(processed.values()),
        "skipped": skipped,
        "skipped_total": sum(skipped.values()),
        "failures": failures,
        "assignments": assignment_results,
        "default_score": _score_for_storage(fallback_score),
        "include_ungraded": include_ungraded,
        "completed_at": _now_iso(),
    }
