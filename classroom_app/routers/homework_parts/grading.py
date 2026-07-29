from .common import *
import uuid

from ...services.assignment_lifecycle_service import ASSIGNMENT_STATUS_CLOSED
from ...services.classroom_closeout_service import (
    apply_absence_scores,
    close_assignment,
    normalize_absence_score,
    refresh_learning_state,
)
from ...services.grading_revision_service import activate_submission_grade_revision


router = APIRouter()


async def _optional_json_body(request: Request) -> dict[str, Any]:
    """这些教师操作既支持无 body 的快捷点击，也支持带参数的弹窗提交。"""
    try:
        payload = await request.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


@router.post(
    "/assignments/{assignment_id}/submissions/zero-unsubmitted",
    response_class=JSONResponse,
    response_model=SubmissionMutationResponse,
    response_model_exclude_unset=True,
)
async def zero_unsubmitted_scores(
    assignment_id: str,
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    """为仍未提交的学生写“缺交”占位成绩，占位记录不视为正式提交。

    默认 0 分；教师可在弹窗里传 ``{"score": N}`` 改成别的默认分。不改变作业
    本身的状态——要连同截止请用 ``/assignments/{id}/close``。
    """
    payload = await _optional_json_body(request)
    score = normalize_absence_score(payload.get("score", payload.get("default_score")))

    with get_db_connection() as conn:
        close_overdue_assignments(conn)
        assignment = _get_assignment_for_teacher(conn, assignment_id, int(user["id"]))
        result = apply_absence_scores(
            conn,
            assignment,
            teacher_id=int(user["id"]),
            score=score,
        )
        if result.get("message"):
            return {
                "status": "success",
                "updated_count": 0,
                "created_count": 0,
                "skipped_count": 0,
                "message": result["message"],
            }
        refresh_learning_state(
            conn,
            assignment.get("class_offering_id"),
            result.get("affected_student_ids") or [],
            f"grading:{assignment_id}:zero",
        )
        conn.commit()

    created_count = int(result.get("created_count") or 0)
    updated_count = int(result.get("updated_count") or 0)
    skipped_count = int(result.get("skipped_count") or 0)

    if assignment.get("class_offering_id") and created_count + updated_count > 0:
        try:
            record_behavior_event(
                class_offering_id=int(assignment["class_offering_id"]),
                user_pk=int(user["id"]),
                user_role="teacher",
                display_name=str(user.get("name") or user["id"]),
                action_type="assignment_zero_unsubmitted",
                session_started_at=str(user.get("login_time") or "").strip() or None,
                summary_text=f"未提交作业记 {score:g} 分：{assignment.get('title') or assignment_id}",
                payload={
                    "assignment_id": assignment_id,
                    "score": score,
                    "created_count": created_count,
                    "updated_count": updated_count,
                    "skipped_count": skipped_count,
                },
                page_key="assignment_detail",
            )
        except Exception as exc:
            print(f"[BEHAVIOR] 记录未提交记 0 失败: {exc}")

    return {
        "status": "success",
        "updated_count": updated_count + created_count,
        "created_count": created_count,
        "refreshed_count": updated_count,
        "skipped_count": skipped_count,
        "score": score,
    }


@router.post(
    "/assignments/{assignment_id}/close",
    response_class=JSONResponse,
    response_model=AssignmentMutationResponse,
    response_model_exclude_unset=True,
)
async def close_assignment_now(
    assignment_id: str,
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    """立即截止一份作业/测验，并给未提交者写默认分。

    Body 全部可选::

        {
          "default_score": 0,        # 未提交者的默认分，0..100，缺省 0
          "apply_absence": true,     # 关掉就只截止、不补分
          "include_ungraded": false  # 是否把“已提交未批改”也按默认分记（破坏性）
        }

    全员已提交且已批改时补分环节自然什么都不做，效果就是“直接截止”。
    """
    payload = await _optional_json_body(request)
    score = normalize_absence_score(payload.get("default_score", payload.get("score")))
    apply_absence = payload.get("apply_absence", True) is not False
    include_ungraded = bool(payload.get("include_ungraded"))

    with get_db_connection() as conn:
        close_overdue_assignments(conn)
        assignment = _get_assignment_for_teacher(conn, assignment_id, int(user["id"]))
        result = close_assignment(
            conn,
            assignment,
            teacher_id=int(user["id"]),
            score=score,
            apply_absence=apply_absence,
            include_ungraded=include_ungraded,
        )
        refresh_learning_state(
            conn,
            assignment.get("class_offering_id"),
            result.get("affected_student_ids") or [],
            f"grading:{assignment_id}:close",
        )
        conn.commit()

    if assignment.get("class_offering_id"):
        try:
            record_behavior_event(
                class_offering_id=int(assignment["class_offering_id"]),
                user_pk=int(user["id"]),
                user_role="teacher",
                display_name=str(user.get("name") or user["id"]),
                action_type="assignment_closed",
                session_started_at=str(user.get("login_time") or "").strip() or None,
                summary_text=f"截止作业：{assignment.get('title') or assignment_id}",
                payload={
                    "assignment_id": assignment_id,
                    "default_score": result.get("default_score"),
                    "created_count": result.get("created_count"),
                    "updated_count": result.get("updated_count"),
                    "graded_count": result.get("graded_count"),
                },
                page_key="assignment_detail",
            )
        except Exception as exc:
            print(f"[BEHAVIOR] 记录作业截止失败: {exc}")

    return {
        "status": "success",
        "updated_assignment_id": assignment_id,
        "assignment_status": ASSIGNMENT_STATUS_CLOSED,
        "closed": result.get("closed"),
        "closed_at": result.get("closed_at"),
        "default_score": result.get("default_score"),
        "created_count": result.get("created_count"),
        "updated_count": result.get("updated_count"),
        "graded_count": result.get("graded_count"),
        "skipped_count": result.get("skipped_count"),
    }


@router.post(
    "/submissions/{submission_id}/grade",
    response_class=JSONResponse,
    response_model=SubmissionMutationResponse,
    response_model_exclude_unset=True,
)
async def grade_submission(submission_id: int, request: Request, user: dict = Depends(get_current_teacher)):
    data = await request.json()
    with get_db_connection() as conn:
        submission = _get_submission_for_teacher(conn, submission_id, int(user["id"]))
        if int(submission.get("resubmission_allowed") or 0):
            raise HTTPException(400, "该提交已撤回并等待重交，不能批改旧版本")
        assignment_for_late_policy = {
            "id": submission.get("assignment_id"),
            "due_at": submission.get("assignment_due_at"),
            "late_submission_enabled": submission.get("assignment_late_submission_enabled"),
            "late_submission_until": submission.get("assignment_late_submission_until"),
            "late_penalty_strategy": submission.get("assignment_late_penalty_strategy"),
            "late_penalty_interval_hours": submission.get("assignment_late_penalty_interval_hours"),
            "late_penalty_points": submission.get("assignment_late_penalty_points"),
            "late_penalty_min_score": submission.get("assignment_late_penalty_min_score"),
            "late_score_cap": submission.get("assignment_late_score_cap"),
        }
        adjustment = apply_late_policy_to_score(
            data.get("score"),
            submission=submission,
            assignment=assignment_for_late_policy,
        )
        final_score = adjustment.get("final_score")
        feedback_md = append_late_policy_feedback(data.get("feedback_md"), adjustment)
        active_ai_job_id = submission.get("grading_job_id")
        if active_ai_job_id:
            conn.execute(
                """
                UPDATE ai_jobs
                SET status = 'superseded', lease_token = '', lease_expires_at = NULL,
                    locked_at = NULL, locked_by = '', updated_at = ?, finished_at = ?
                WHERE id = ? AND status IN ('queued', 'retry_wait', 'running', 'result_ready')
                """,
                (datetime.now().isoformat(timespec="seconds"), datetime.now().isoformat(timespec="seconds"), int(active_ai_job_id)),
            )
        conn.execute(
            """
            UPDATE submissions
            SET status = 'graded',
                score = ?,
                feedback_md = ?,
                score_before_late_penalty = ?,
                late_penalty_points = ?,
                late_score_cap_applied = ?,
                grading_started_at = NULL,
                grading_attempt_fingerprint = NULL,
                grading_revision_hash = NULL,
                grading_job_id = NULL,
                resubmission_allowed = 0,
                resubmission_due_at = NULL,
                returned_at = NULL,
                returned_by_teacher_id = NULL,
                returned_reason = NULL
            WHERE id = ?
            """,
            (
                final_score,
                feedback_md,
                adjustment.get("original_score") if adjustment.get("applied") else None,
                adjustment.get("penalty_points") or 0,
                1 if adjustment.get("score_cap_applied") else 0,
                submission_id,
            ),
        )
        activate_submission_grade_revision(
            conn,
            submission={**submission, "grading_job_id": None},
            data={
                "grading_revision_hash": f"manual:{submission_id}:{uuid.uuid4().hex}",
                "source": "manual",
                "actor_role": "teacher",
                "actor_user_pk": int(user["id"]),
                "quality_audit": {"manual_grade": True},
            },
            score=final_score,
            feedback_md=feedback_md,
        )
        try:
            create_student_grading_notification(
                conn,
                submission_id,
                actor_role="teacher",
                actor_user_pk=int(user["id"]),
                actor_display_name=str(user.get("name") or ""),
            )
        except Exception as exc:
            print(f"[MESSAGE_CENTER] manual grading notify failed: {exc}")
        try:
            handle_stage_exam_grading_complete(conn, submission_id)
        except Exception as exc:
            print(f"[LEARNING_PROGRESS] manual grading stage handling failed: {exc}")
        try:
            handle_assignment_stage_grading_complete(conn, submission_id)
        except Exception as exc:
            print(f"[LEARNING_PROGRESS] manual grading teacher-stage handling failed: {exc}")
        try:
            from ...services.group_assignment_service import record_member_work_score

            record_member_work_score(conn, submission_id)
        except Exception as exc:
            print(f"[GROUP_ASSIGNMENT] manual grading group finalize failed: {exc}")
        if submission.get("class_offering_id") and submission.get("student_pk_id"):
            try:
                refresh_student_learning_state(
                    conn,
                    int(submission["class_offering_id"]),
                    int(submission["student_pk_id"]),
                    event_source_ref=f"grading:{submission_id}",
                )
            except Exception as exc:
                print(f"[LEARNING_PROGRESS] manual grading snapshot refresh failed: {exc}")
        conn.commit()
    return {"status": "success", "graded_submission_id": submission_id}


@router.post(
    "/assignments/{assignment_id}/submissions/batch-grade",
    response_class=JSONResponse,
    response_model=SubmissionMutationResponse,
    response_model_exclude_unset=True,
)
async def batch_grade_submissions(assignment_id: str, request: Request, user: dict = Depends(get_current_teacher)):
    """教师批量发起 AI 批改：可指定 submission_ids 或自动处理所有待批改提交。"""
    data = await request.json()
    submission_ids_input = _parse_int_set(data.get("submission_ids", []), "submission_ids")

    with get_db_connection() as conn:
        close_overdue_assignments(conn)
        assignment = _get_assignment_for_teacher(conn, assignment_id, int(user["id"]))

        if submission_ids_input:
            placeholders = ",".join("?" for _ in submission_ids_input)
            rows = conn.execute(
                f"""
                SELECT id, status FROM submissions
                WHERE assignment_id = ? AND id IN ({placeholders})
                ORDER BY id
                """,
                (assignment_id, *sorted(submission_ids_input)),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, status FROM submissions
                WHERE assignment_id = ?
                  AND status NOT IN ('graded', 'grading')
                  AND COALESCE(resubmission_allowed, 0) = 0
                  AND COALESCE(is_absence_score, 0) = 0
                ORDER BY id
                LIMIT 50
                """,
                (assignment_id,),
            ).fetchall()
        conn.commit()

    targets = [dict(row) for row in rows]
    if not targets:
        return {
            "status": "success",
            "queued_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "message": "没有可批改的提交（可能已全部批改完毕或正在批改中）。",
        }

    # 最多同时提交 5 个，避免压垮 AI 服务
    sem = asyncio.Semaphore(5)

    async def _grade_one(sub_id: int) -> str:
        async with sem:
            try:
                result = await submit_submission_for_ai_grading(sub_id, teacher_id=int(user["id"]), allow_graded=False)
                status = str(result.get("status") or "")
                if status in ("already_grading", "already_graded"):
                    return "skipped"
                return "queued"
            except AIGradingQueueError as exc:
                print(f"[BATCH_GRADE] submission {sub_id} failed: {exc.detail}")
                return "failed"
            except Exception as exc:
                print(f"[BATCH_GRADE] submission {sub_id} unexpected error: {exc}")
                return "failed"

    tasks = [_grade_one(int(t["id"])) for t in targets]
    results = await asyncio.gather(*tasks)
    queued = 0
    skipped = 0
    failed = 0
    for r in (results or []):
        if r == "queued":
            queued += 1
        elif r == "skipped":
            skipped += 1
        else:
            failed += 1

    return {
        "status": "success",
        "total_targets": len(targets),
        "queued_count": queued,
        "skipped_count": skipped,
        "failed_count": failed,
    }
