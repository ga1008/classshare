"""Classroom retake/transfer-student management (重修插班生).

The academic office only requires retake students to sit the final exam, so
regular homework, quizzes and attendance are exempt. The pipeline is:

1. AI detection (:func:`detect_retake_candidates`) — student numbers encode
   the enrollment year in their prefix; students whose prefix/length differs
   from the class majority are *suggested* as retake candidates. Suggestion
   alone changes nothing.
2. Teacher confirmation (:func:`confirm_retake_student`) — activates the
   special handling with a per-student default ordinary score (70 unless
   set). Historical closed assignments the student skipped get an absence
   placeholder submission at the default score; real submitted scores are
   never touched.
3. Downstream consumers read :func:`get_confirmed_retake_students`:
   grade-record generation fills missing components with the default score,
   assignment auto-close and classroom closeout stamp the default score,
   and group random-join / poll voting politely exclude these students.
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import datetime
from typing import Any

from fastapi import HTTPException

from ..db.schema_retake import ensure_retake_schema
from .classroom_closeout_service import apply_absence_scores, refresh_learning_state
from .offering_membership_service import offering_class_ids

DEFAULT_RETAKE_ORDINARY_SCORE = 70.0
RETAKE_FEEDBACK_TEMPLATE = "重修/免修学生：未参加本次任务，按教师确认的默认平时分 {score} 分记录。"
_DETECTION_MIN_CLASS_SIZE = 5
_DETECTION_MAJORITY_RATIO = 0.6


def _now_iso() -> str:
    return datetime.now().isoformat()


def normalize_retake_default_score(value: Any) -> float:
    if value in (None, ""):
        return DEFAULT_RETAKE_ORDINARY_SCORE
    try:
        score = float(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "默认平时分必须是 0 到 100 之间的数字。") from exc
    if not math.isfinite(score) or score < 0 or score > 100:
        raise HTTPException(400, "默认平时分必须在 0 到 100 之间。")
    return round(score, 2)


def _format_score(value: float) -> Any:
    return int(value) if float(value).is_integer() else float(value)


def _offering_row(conn, class_offering_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT o.id, o.class_id, o.course_id, o.teacher_id
        FROM class_offerings o
        WHERE o.id = ?
        LIMIT 1
        """,
        (int(class_offering_id),),
    ).fetchone()
    if not row:
        raise HTTPException(404, "课堂不存在。")
    return dict(row)


def _roster_rows(conn, offering: Any) -> list[dict[str, Any]]:
    offering_row = dict(offering)
    class_ids = offering_class_ids(conn, int(offering_row["id"])) or [
        int(offering_row["class_id"])
    ]
    placeholders = ",".join("?" for _ in class_ids)
    return [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT s.id, s.student_id_number, s.name
            FROM students s
            WHERE s.class_id IN ({placeholders})
              AND COALESCE(s.enrollment_status, 'active') = 'active'
            ORDER BY s.student_id_number, s.id
            """,
            tuple(class_ids),
        ).fetchall()
    ]


def _cohort_key(student_number: str) -> tuple[int, str]:
    number = str(student_number or "").strip()
    return (len(number), number[:2])


def _upsert_retake_row(
    conn,
    *,
    class_offering_id: int,
    student_id: int,
    student_number: str,
    student_name: str,
    status: str,
    suggested_reason: str = "",
    default_ordinary_score: float = DEFAULT_RETAKE_ORDINARY_SCORE,
    confirmed_by_teacher_id: int | None = None,
    confirmed_at: str | None = None,
    now: str = "",
) -> None:
    now = now or _now_iso()
    conn.execute(
        """
        INSERT INTO classroom_retake_students
            (class_offering_id, student_id, student_number, student_name, status,
             default_ordinary_score, suggested_reason, confirmed_by_teacher_id,
             confirmed_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (class_offering_id, student_id) DO UPDATE SET
            student_number = excluded.student_number,
            student_name = excluded.student_name,
            status = excluded.status,
            default_ordinary_score = excluded.default_ordinary_score,
            suggested_reason = excluded.suggested_reason,
            confirmed_by_teacher_id = excluded.confirmed_by_teacher_id,
            confirmed_at = excluded.confirmed_at,
            updated_at = excluded.updated_at
        """,
        (
            int(class_offering_id),
            int(student_id),
            student_number,
            student_name,
            status,
            float(default_ordinary_score),
            suggested_reason,
            confirmed_by_teacher_id,
            confirmed_at,
            now,
            now,
        ),
    )


def detect_retake_candidates(conn, *, class_offering_id: int) -> dict[str, Any]:
    """学号前缀启发式识别：入学年份编码在学号前缀里，与班级多数
    （前两位 + 位数）不同的学生被列为重修插班生候选。仅建议，不生效。"""
    ensure_retake_schema(conn)
    offering = _offering_row(conn, class_offering_id)
    roster = _roster_rows(conn, offering)
    existing = {
        int(row["student_id"]): dict(row)
        for row in conn.execute(
            "SELECT * FROM classroom_retake_students WHERE class_offering_id = ?",
            (int(class_offering_id),),
        ).fetchall()
    }

    counts = Counter(
        _cohort_key(student["student_id_number"])
        for student in roster
        if str(student["student_id_number"] or "").strip()
    )
    majority_key, majority_count = (counts.most_common(1)[0] if counts else ((0, ""), 0))
    total = sum(counts.values())
    detectable = (
        len(roster) >= _DETECTION_MIN_CLASS_SIZE
        and total > 0
        and majority_count / total >= _DETECTION_MAJORITY_RATIO
    )

    now = _now_iso()
    suggestions: list[dict[str, Any]] = []
    candidate_ids: set[int] = set()
    if detectable:
        majority_len, majority_prefix = majority_key
        for student in roster:
            number = str(student["student_id_number"] or "").strip()
            if not number or _cohort_key(number) == majority_key:
                continue
            student_id = int(student["id"])
            candidate_ids.add(student_id)
            reason = (
                f"学号前缀 {number[:2]}（{len(number)} 位），"
                f"与全班多数 {majority_prefix}（{majority_len} 位）不同，疑似非本届入学的插班/重修学生。"
            )
            current = existing.get(student_id)
            if current is None:
                _upsert_retake_row(
                    conn,
                    class_offering_id=int(class_offering_id),
                    student_id=student_id,
                    student_number=number,
                    student_name=str(student["name"] or ""),
                    status="suggested",
                    suggested_reason=reason,
                    now=now,
                )
            elif current.get("status") == "suggested" and current.get("suggested_reason") != reason:
                conn.execute(
                    "UPDATE classroom_retake_students SET suggested_reason = ?, updated_at = ? WHERE id = ?",
                    (reason, now, int(current["id"])),
                )
            suggestions.append(
                {
                    "student_id": student_id,
                    "student_number": number,
                    "student_name": str(student["name"] or ""),
                    "reason": reason,
                    "status": (existing.get(student_id) or {}).get("status") or "suggested",
                }
            )
    # AI 判定只影响"建议"状态：撤下不再命中的旧建议，绝不动教师确认/驳回的记录。
    for student_id, row in existing.items():
        if row.get("status") == "suggested" and student_id not in candidate_ids:
            conn.execute(
                "DELETE FROM classroom_retake_students WHERE id = ?",
                (int(row["id"]),),
            )
    conn.commit()
    return {
        "detectable": detectable,
        "roster_count": len(roster),
        "majority_prefix": majority_key[1] if counts else "",
        "majority_length": majority_key[0] if counts else 0,
        "majority_count": majority_count,
        "suggestions": suggestions,
    }


def list_retake_students(conn, *, class_offering_id: int) -> list[dict[str, Any]]:
    ensure_retake_schema(conn)
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM classroom_retake_students
            WHERE class_offering_id = ?
            ORDER BY CASE status WHEN 'confirmed' THEN 0 WHEN 'suggested' THEN 1 ELSE 2 END,
                     student_number, id
            """,
            (int(class_offering_id),),
        ).fetchall()
    ]


def get_confirmed_retake_students(conn, *, class_offering_id: int) -> list[dict[str, Any]]:
    """确认生效的插班生名单，供生成/结课/活动等下游消费。"""
    ensure_retake_schema(conn)
    return [
        {
            "student_id": int(row["student_id"]),
            "student_number": str(row["student_number"] or ""),
            "student_name": str(row["student_name"] or ""),
            "default_ordinary_score": float(row["default_ordinary_score"] or DEFAULT_RETAKE_ORDINARY_SCORE),
        }
        for row in conn.execute(
            """
            SELECT student_id, student_number, student_name, default_ordinary_score
            FROM classroom_retake_students
            WHERE class_offering_id = ? AND status = 'confirmed'
            ORDER BY student_number, id
            """,
            (int(class_offering_id),),
        ).fetchall()
    ]


def is_confirmed_retake_student(conn, *, class_offering_id: int, student_id: int) -> bool:
    ensure_retake_schema(conn)
    row = conn.execute(
        """
        SELECT 1 FROM classroom_retake_students
        WHERE class_offering_id = ? AND student_id = ? AND status = 'confirmed'
        LIMIT 1
        """,
        (int(class_offering_id), int(student_id)),
    ).fetchone()
    return bool(row)


def confirm_retake_student(
    conn,
    *,
    class_offering_id: int,
    student_id: int,
    teacher_id: int,
    default_score: Any = None,
) -> dict[str, Any]:
    """教师敲定插班生身份：写入默认平时分，并把已截止、未参加的
    作业/测验补上默认分占位（已参加的真实分数保持不变）。"""
    ensure_retake_schema(conn)
    offering = _offering_row(conn, class_offering_id)
    roster = {int(student["id"]): student for student in _roster_rows(conn, offering)}
    student = roster.get(int(student_id))
    if not student:
        raise HTTPException(404, "该学生不在本课堂的在读名单中，无法设置为重修/插班生。")
    score = normalize_retake_default_score(default_score)
    now = _now_iso()
    _upsert_retake_row(
        conn,
        class_offering_id=int(class_offering_id),
        student_id=int(student_id),
        student_number=str(student["student_id_number"] or ""),
        student_name=str(student["name"] or ""),
        status="confirmed",
        default_ordinary_score=score,
        confirmed_by_teacher_id=int(teacher_id),
        confirmed_at=now,
        now=now,
    )
    backfill = backfill_retake_absences_for_offering(
        conn,
        class_offering_id=int(class_offering_id),
        teacher_id=int(teacher_id),
        only_student_ids={int(student_id)},
    )
    conn.commit()
    return {
        "student_id": int(student_id),
        "student_number": str(student["student_id_number"] or ""),
        "student_name": str(student["name"] or ""),
        "default_ordinary_score": score,
        "confirmed_at": now,
        "backfill": backfill,
    }


def revoke_retake_student(
    conn,
    *,
    class_offering_id: int,
    student_id: int,
    teacher_id: int,
) -> dict[str, Any]:
    """撤销插班生身份：之后按普通学生处理。已写入的默认分占位不自动
    回收（避免破坏已生成材料），教师可在作业页逐条撤回。"""
    ensure_retake_schema(conn)
    now = _now_iso()
    cursor = conn.execute(
        """
        UPDATE classroom_retake_students
        SET status = 'dismissed', confirmed_by_teacher_id = ?, updated_at = ?
        WHERE class_offering_id = ? AND student_id = ?
        """,
        (int(teacher_id), now, int(class_offering_id), int(student_id)),
    )
    conn.commit()
    return {"student_id": int(student_id), "revoked": bool(cursor.rowcount)}


def backfill_retake_absences_for_offering(
    conn,
    *,
    class_offering_id: int,
    teacher_id: int,
    only_student_ids: set[int] | None = None,
) -> dict[str, Any]:
    """幂等清扫：给确认插班生补齐所有已截止作业/测验的默认分占位。

    只补"没有提交记录"的空位；真实提交（含线下代交）的分数绝不改动。
    学生个人试炼（learning stage exam）不属于班级统一任务，跳过。
    """
    confirmed = get_confirmed_retake_students(conn, class_offering_id=int(class_offering_id))
    if only_student_ids is not None:
        confirmed = [item for item in confirmed if item["student_id"] in only_student_ids]
    result = {"assignment_count": 0, "created_count": 0, "student_count": len(confirmed)}
    if not confirmed:
        return result

    from .learning_progress_service import is_personal_stage_exam_assignment

    assignments = [
        dict(row)
        for row in conn.execute(
            """
            SELECT a.id, a.title, a.status, o.class_id AS offering_class_id
            FROM assignments a
            JOIN class_offerings o ON o.id = a.class_offering_id
            WHERE a.class_offering_id = ?
              AND a.status = 'closed'
            ORDER BY a.id
            """,
            (int(class_offering_id),),
        ).fetchall()
    ]
    affected: set[int] = set()
    for assignment in assignments:
        if is_personal_stage_exam_assignment(conn, assignment["id"]):
            continue
        result["assignment_count"] += 1
        for item in confirmed:
            outcome = apply_absence_scores(
                conn,
                assignment,
                teacher_id=int(teacher_id),
                score=item["default_ordinary_score"],
                only_student_ids={item["student_id"]},
                score_overrides={item["student_id"]: item["default_ordinary_score"]},
                feedback_override=RETAKE_FEEDBACK_TEMPLATE.format(
                    score=_format_score(item["default_ordinary_score"])
                ),
            )
            # 占位重写（updated_count）是幂等的常态，只统计真正新建的占位。
            created = int(outcome.get("created_count") or 0)
            if created:
                result["created_count"] += created
                affected.update(outcome.get("affected_student_ids") or [])
    if affected:
        refresh_learning_state(conn, class_offering_id, affected, "retake_backfill")
    return result


def backfill_retake_absences_everywhere(conn) -> int:
    """自动截止后的全局幂等清扫：为所有有确认插班生的课堂补齐默认分占位。

    仅在真的有作业被关闭时调用（低频）；无插班生的部署零开销。"""
    ensure_retake_schema(conn)
    offerings = conn.execute(
        """
        SELECT DISTINCT r.class_offering_id, o.teacher_id
        FROM classroom_retake_students r
        JOIN class_offerings o ON o.id = r.class_offering_id
        WHERE r.status = 'confirmed'
        """
    ).fetchall()
    total = 0
    for row in offerings:
        outcome = backfill_retake_absences_for_offering(
            conn,
            class_offering_id=int(row["class_offering_id"]),
            teacher_id=int(row["teacher_id"] or 0),
        )
        total += int(outcome.get("created_count") or 0)
    return total
