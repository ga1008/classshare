from .common import *


router = APIRouter()


@router.post(
    "/assignments/{assignment_id}/submissions/zero-unsubmitted",
    response_class=JSONResponse,
    response_model=SubmissionMutationResponse,
    response_model_exclude_unset=True,
)
async def zero_unsubmitted_scores(assignment_id: str, user: dict = Depends(get_current_teacher)):
    """为仍未提交的学生创建“缺交记 0”成绩，占位记录不视为正式提交。"""
    with get_db_connection() as conn:
        close_overdue_assignments(conn)
        assignment = _get_assignment_for_teacher(conn, assignment_id, int(user["id"]))
        offering_class_id = assignment.get("offering_class_id")
        if not offering_class_id:
            return {
                "status": "success",
                "updated_count": 0,
                "created_count": 0,
                "skipped_count": 0,
                "message": "当前作业未绑定班级，无法识别未提交学生",
            }

        students = [
            dict(row)
            for row in conn.execute(
                """
                SELECT s.id, s.student_id_number, s.name
                FROM students s
                WHERE s.class_id = ?
                  AND COALESCE(s.enrollment_status, 'active') = 'active'
                ORDER BY s.student_id_number, s.name
                """,
                (int(offering_class_id),),
            )
        ]
        existing_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id, student_pk_id, status, is_absence_score
                FROM submissions
                WHERE assignment_id = ?
                """,
                (assignment_id,),
            )
        ]
        existing_by_student: dict[int, dict[str, Any]] = {}
        for row in existing_rows:
            student_pk_id = int(row["student_pk_id"])
            current = existing_by_student.get(student_pk_id)
            row_is_absence = int(row.get("is_absence_score") or 0) == 1
            current_is_absence = current is not None and int(current.get("is_absence_score") or 0) == 1
            if current is None or (current_is_absence and not row_is_absence):
                existing_by_student[student_pk_id] = row

        now_iso = datetime.now().replace(microsecond=0).isoformat()
        feedback = "未提交，按缺交记 0 分。"
        created_count = 0
        updated_count = 0
        skipped_count = 0
        affected_student_ids: set[int] = set()

        for student in students:
            student_pk_id = int(student["id"])
            existing = existing_by_student.get(student_pk_id)
            if existing and str(existing.get("status") or "") != "unsubmitted":
                skipped_count += 1
                continue

            if existing:
                conn.execute(
                    """
                    UPDATE submissions
                    SET student_name = ?,
                        status = 'unsubmitted',
                        score = 0,
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
                        student.get("name") or "",
                        feedback,
                        int(user["id"]),
                        now_iso,
                        int(user["id"]),
                        int(existing["id"]),
                    ),
                )
                updated_count += 1
                affected_student_ids.add(student_pk_id)
                continue

            conn.execute(
                """
                INSERT INTO submissions (
                    assignment_id, student_pk_id, student_name, status, score, feedback_md,
                    answers_json, submitted_by_role, submitted_by_teacher_id, submission_channel,
                    resubmission_allowed, resubmission_due_at, returned_at, returned_by_teacher_id,
                    returned_reason, is_absence_score, absence_scored_at, absence_scored_by_teacher_id,
                    submitted_at
                ) VALUES (?, ?, ?, 'unsubmitted', 0, ?, NULL, 'teacher', ?, 'absence_zero',
                          0, NULL, NULL, NULL, NULL, 1, ?, ?, ?)
                """,
                (
                    assignment_id,
                    student_pk_id,
                    student.get("name") or "",
                    feedback,
                    int(user["id"]),
                    now_iso,
                    int(user["id"]),
                    now_iso,
                ),
            )
            created_count += 1
            affected_student_ids.add(student_pk_id)

        if assignment.get("class_offering_id"):
            for student_pk_id in affected_student_ids:
                try:
                    refresh_student_learning_state(
                        conn,
                        int(assignment["class_offering_id"]),
                        int(student_pk_id),
                        event_source_ref=f"grading:{assignment_id}:zero",
                    )
                except Exception as exc:
                    print(f"[LEARNING_PROGRESS] zero-unsubmitted snapshot refresh failed: {exc}")
        conn.commit()

    if assignment.get("class_offering_id") and created_count + updated_count > 0:
        try:
            record_behavior_event(
                class_offering_id=int(assignment["class_offering_id"]),
                user_pk=int(user["id"]),
                user_role="teacher",
                display_name=str(user.get("name") or user["id"]),
                action_type="assignment_zero_unsubmitted",
                session_started_at=str(user.get("login_time") or "").strip() or None,
                summary_text=f"未提交作业记 0：{assignment.get('title') or assignment_id}",
                payload={
                    "assignment_id": assignment_id,
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
