from .common import *


router = APIRouter()


@router.get(
    "/assignments/{assignment_id}/submissions",
    response_class=JSONResponse,
    response_model=AssignmentSubmissionsResponse,
    response_model_exclude_unset=True,
)
async def get_submissions_for_assignment(assignment_id: str, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        close_overdue_assignments(conn)
        assignment = _get_assignment_for_teacher(conn, assignment_id, int(user["id"]))
        _expire_stale_ai_grading_for_assignment(conn, assignment_id)

        submissions_cursor = conn.execute(
            """
            SELECT s.*, COUNT(sf.id) AS file_count
            FROM submissions s
            LEFT JOIN submission_files sf ON sf.submission_id = s.id
            WHERE s.assignment_id = ?
            GROUP BY s.id
            ORDER BY s.submitted_at DESC
            """,
            (assignment_id,)
        )
        submissions = [dict(row) for row in submissions_cursor]
        submission_file_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT sf.submission_id,
                       sf.original_filename,
                       sf.relative_path,
                       sf.mime_type,
                       sf.file_size,
                       sf.file_ext,
                       sf.file_hash,
                       sf.stored_path
                FROM submission_files sf
                JOIN submissions s ON s.id = sf.submission_id
                WHERE s.assignment_id = ?
                ORDER BY sf.submission_id, COALESCE(sf.relative_path, sf.original_filename), sf.id
                """,
                (assignment_id,),
            )
        ]

        # 获取班级花名册以包含未提交学生
        total_students = 0
        roster = []
        stage_target = get_stage_exam_target(conn, assignment_id)
        if assignment['class_offering_id']:
            offering = conn.execute("SELECT class_id FROM class_offerings WHERE id = ?",
                                    (assignment['class_offering_id'],)).fetchone()
            if offering:
                if stage_target:
                    students_cursor = conn.execute(
                        """
                        SELECT id, student_id_number, name
                        FROM students
                        WHERE id = ?
                          AND COALESCE(enrollment_status, 'active') = 'active'
                        """,
                        (int(stage_target["student_id"]),),
                    )
                else:
                    students_cursor = conn.execute(
                        """
                        SELECT id, student_id_number, name
                        FROM students
                        WHERE class_id = ?
                          AND COALESCE(enrollment_status, 'active') = 'active'
                        ORDER BY student_id_number
                        """,
                        (offering['class_id'],),
                    )
                roster = [dict(row) for row in students_cursor]
                total_students = len(roster)
        conn.commit()

    files_by_submission: dict[int, list[dict[str, Any]]] = {}
    for row in submission_file_rows:
        try:
            key = int(row.get("submission_id"))
        except (TypeError, ValueError):
            continue
        files_by_submission.setdefault(key, []).append(row)

    for submission in submissions:
        file_rows = files_by_submission.get(int(submission["id"]), [])
        type_summary = build_attachment_type_summary(file_rows)
        submission["attachment_type_summary"] = type_summary
        submission["has_unsupported_ai_attachments"] = any(not item.get("supported", True) for item in type_summary)

    # 构建提交映射
    submission_map = {s['student_pk_id']: s for s in submissions}

    # 合并花名册和提交数据（包含未提交学生）
    all_entries = []
    for student in roster:
        if student['id'] in submission_map:
            entry = submission_map[student['id']]
            entry['student_id_number'] = student['student_id_number']
            entry['student_name'] = student['name'] or entry.get('student_name')
            all_entries.append(entry)
        else:
            all_entries.append({
                'id': None,
                'student_pk_id': student['id'],
                'student_name': student['name'],
                'student_id_number': student['student_id_number'],
                'assignment_id': assignment_id,
                'status': 'unsubmitted',
                'score': None,
                'feedback_md': None,
                'submitted_at': None,
                'answers_json': None,
                'file_count': 0,
                'submitted_by_role': None,
                'submitted_by_teacher_id': None,
                'submission_channel': None,
                'resubmission_allowed': 0,
                'resubmission_due_at': None,
                'returned_at': None,
                'returned_by_teacher_id': None,
                'returned_reason': None,
                'is_absence_score': 0,
                'absence_scored_at': None,
                'absence_scored_by_teacher_id': None,
                'is_late_submission': 0,
                'late_by_seconds': 0,
                'score_before_late_penalty': None,
                'late_penalty_points': 0,
                'late_score_cap_applied': 0,
                'attachment_type_summary': [],
                'has_unsupported_ai_attachments': False,
            })

    # 如果没有花名册信息，退回只显示已提交学生
    if not roster:
        all_entries = submissions
        total_students = len(submissions)

    # 计算统计数据
    submitted_entries = [e for e in all_entries if e['status'] != 'unsubmitted']
    absence_zero_entries = [
        e for e in all_entries
        if e.get('status') == 'unsubmitted'
        and int(e.get('is_absence_score') or 0)
        and e.get('score') is not None
    ]
    returned_entries = [s for s in submitted_entries if int(s.get('resubmission_allowed') or 0)]
    graded_entries = [s for s in submitted_entries if s['status'] == 'graded' and s['score'] is not None]
    score_entries = graded_entries + absence_zero_entries
    scores = [s['score'] for s in score_entries]
    none_count = max(0, total_students - len(submitted_entries) - len(absence_zero_entries))

    stats = {
        "total_students": total_students,
        "total_submissions": len(submitted_entries),
        "unsubmitted_count": none_count,
        "graded_count": len(graded_entries),
        "absence_zero_count": len(absence_zero_entries),
        "submitted_count": len([s for s in submitted_entries if s['status'] == 'submitted']),
        "pending_grade_count": len([
            s for s in submitted_entries
            if s["status"] == "submitted"
            and not int(s.get("resubmission_allowed") or 0)
            and not int(s.get("is_absence_score") or 0)
        ]),
        "grading_count": len([s for s in submitted_entries if s['status'] == 'grading']),
        "returned_count": len(returned_entries),
        "late_submission_count": len([s for s in submitted_entries if int(s.get("is_late_submission") or 0)]),
        "average_score": round(sum(scores) / len(scores), 1) if scores else 0,
        "max_score": max(scores) if scores else 0,
        "min_score": min(scores) if scores else 0,
        "pass_rate": round(len([s for s in scores if s >= 60]) / len(scores) * 100, 1) if scores else 0,
        "score_distribution": {
            "none": none_count,
            "fail": len([s for s in scores if s < 60]),
            "pass": len([s for s in scores if 60 <= s < 70]),
            "medium": len([s for s in scores if 70 <= s < 80]),
            "good": len([s for s in scores if 80 <= s < 90]),
            "excellent": len([s for s in scores if s >= 90]),
        }
    }

    return {
        "status": "success",
        "stats": stats,
        "submissions": all_entries,
        "assignment": enrich_assignment_runtime_view(assignment),
    }


@router.delete(
    "/submissions/{submission_id}",
    response_class=JSONResponse,
    response_model=SubmissionMutationResponse,
    response_model_exclude_unset=True,
)
async def return_submission(submission_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        submission = _get_submission_for_teacher(conn, submission_id, int(user["id"]))
        conn.execute("DELETE FROM submissions WHERE id = ?", (submission_id,))
        conn.commit()
    delete_storage_tree(
        _build_submission_storage_dir(submission['course_id'], submission['assignment_id'], submission['student_pk_id'])
    )
    return {"status": "success", "deleted_submission_id": submission_id}


@router.post("/submissions/{submission_id}/files", response_class=JSONResponse)
async def add_submission_files(
    submission_id: int,
    manifest: str = Form(""),
    queue_ai: str = Form("0"),
    files: List[UploadFile] = File(default=[]),
    user: dict = Depends(get_current_teacher),
):
    """教师为已提交记录补充附件；已批改记录必须先撤回。"""
    prepared_entries = _validate_upload_entries(files, manifest)
    if not prepared_entries:
        raise HTTPException(400, "请选择要添加的附件")

    queue_ai_requested = _form_bool(queue_ai)
    moved_paths: list[Path] = []
    staging_dir: Path | None = None

    with get_db_connection() as conn:
        submission = _get_submission_for_teacher(conn, int(submission_id), int(user["id"]))
        _ensure_submission_files_manageable(submission)
        allowed_file_types = decode_allowed_file_types_json(submission.get("allowed_file_types_json"))
        existing_files = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id, original_filename, relative_path, stored_path, mime_type, file_size, file_ext, file_hash
                FROM submission_files
                WHERE submission_id = ?
                ORDER BY COALESCE(relative_path, original_filename), id
                """,
                (submission_id,),
            )
        ]

    _deduplicate_upload_entries_against_existing(prepared_entries, existing_files)
    existing_count = len(existing_files)
    existing_total_bytes = sum(int(row.get("file_size") or 0) for row in existing_files)
    new_total_bytes = sum(int(entry.size_bytes) for entry in prepared_entries)
    if existing_count + len(prepared_entries) > MAX_SUBMISSION_FILE_COUNT:
        raise HTTPException(413, f"附件总数不能超过 {MAX_SUBMISSION_FILE_COUNT} 个")
    if existing_total_bytes + new_total_bytes > MAX_SUBMISSION_TOTAL_BYTES:
        raise HTTPException(
            413,
            f"附件总大小超过限制 {MAX_SUBMISSION_TOTAL_MB:.0f}MB"
            f"（当前 {(existing_total_bytes + new_total_bytes) / 1024 / 1024:.1f}MB）",
        )

    submission_dir = _build_submission_storage_dir(
        int(submission["course_id"]),
        submission["assignment_id"],
        int(submission["student_pk_id"]),
    )
    staging_dir = submission_dir.with_name(f"{submission_dir.name}.__teacher_add__{uuid.uuid4().hex}")

    try:
        storage_result = await store_submission_files(staging_dir, prepared_entries, allowed_file_types)
        storage_result.dropped_files = _enrich_dropped_file_details(
            storage_result.dropped_files,
            allowed_file_types,
            None,
        )
        if storage_result.dropped_files:
            raise HTTPException(
                status_code=400,
                detail=_dropped_files_error_detail(storage_result.dropped_files, action_label="添加附件"),
            )
        if not storage_result.stored_files:
            expected_types = summarize_allowed_file_types(allowed_file_types)
            raise HTTPException(400, f"没有符合要求的文件可添加，允许类型: {expected_types}")
        try:
            ensure_ai_grading_attachments_supported([_stored_file_to_dict(item) for item in storage_result.stored_files])
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

        with get_db_connection() as conn:
            try:
                begin_immediate_transaction(conn)
                current_submission = _get_submission_for_teacher(conn, int(submission_id), int(user["id"]))
                _ensure_submission_files_manageable(current_submission)
                current_files = [
                    dict(row)
                    for row in conn.execute(
                        """
                        SELECT id, original_filename, relative_path, stored_path, mime_type, file_size, file_ext, file_hash
                        FROM submission_files
                        WHERE submission_id = ?
                        ORDER BY COALESCE(relative_path, original_filename), id
                        """,
                        (submission_id,),
                    )
                ]
                if len(current_files) + len(storage_result.stored_files) > MAX_SUBMISSION_FILE_COUNT:
                    raise HTTPException(413, f"附件总数不能超过 {MAX_SUBMISSION_FILE_COUNT} 个")
                current_total = sum(int(row.get("file_size") or 0) for row in current_files)
                stored_total = sum(int(item.file_size or 0) for item in storage_result.stored_files)
                if current_total + stored_total > MAX_SUBMISSION_TOTAL_BYTES:
                    raise HTTPException(413, f"附件总大小超过限制 {MAX_SUBMISSION_TOTAL_MB:.0f}MB")

                seen_paths = {
                    str(row.get("relative_path") or row.get("original_filename") or "").replace("\\", "/").strip().lower()
                    for row in current_files
                    if str(row.get("relative_path") or row.get("original_filename") or "").strip()
                }
                submission_dir.mkdir(parents=True, exist_ok=True)
                for file_info in storage_result.stored_files:
                    source_path = Path(file_info.stored_path)
                    relative_path = _deduplicate_relative_path_against_seen(file_info.relative_path, seen_paths)
                    final_path = _build_submission_file_path(submission_dir, relative_path)
                    final_path.parent.mkdir(parents=True, exist_ok=True)
                    source_path.replace(final_path)
                    moved_paths.append(final_path)
                    file_info.relative_path = relative_path
                    file_info.original_filename = PurePosixPath(relative_path).name
                    file_info.stored_path = str(final_path)
                    file_info.file_ext = Path(relative_path).suffix.lower()

                for file_info in storage_result.stored_files:
                    conn.execute(
                        """
                        INSERT INTO submission_files (
                            submission_id, original_filename, relative_path, stored_path,
                            mime_type, file_size, file_ext, file_hash
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            submission_id,
                            file_info.original_filename,
                            file_info.relative_path,
                            file_info.stored_path,
                            file_info.mime_type,
                            file_info.file_size,
                            file_info.file_ext,
                            file_info.file_hash,
                        ),
                    )
                _reset_submission_after_attachment_edit(conn, int(submission_id), int(user["id"]))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
    except Exception:
        for path in moved_paths:
            try:
                if path.exists() and path.is_file():
                    path.unlink()
            except Exception as exc:
                print(f"[SUBMISSION_FILES] failed to remove moved file after rollback: {exc}")
        raise
    finally:
        if staging_dir:
            delete_storage_tree(staging_dir)

    ai_queue_result = None
    if queue_ai_requested:
        try:
            ai_queue_result = await submit_submission_for_ai_grading(
                int(submission_id),
                teacher_id=int(user["id"]),
                allow_graded=False,
            )
        except AIGradingQueueError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    return {
        "status": "success",
        "submission_id": int(submission_id),
        "added_count": len(storage_result.stored_files),
        **_dropped_files_response_fields(storage_result.dropped_files, action_label="添加附件"),
        "ai_queue_result": ai_queue_result,
    }


@router.delete("/submission-files/{file_id}", response_class=JSONResponse)
async def delete_submission_file(file_id: int, user: dict = Depends(get_current_teacher)):
    """教师删除单个学生提交附件；已批改记录必须先撤回。"""
    physical_path: Path | None = None
    with get_db_connection() as conn:
        file_row = conn.execute(
            """
            SELECT id, submission_id, original_filename, relative_path, stored_path, mime_type, file_size, file_ext, file_hash
            FROM submission_files
            WHERE id = ?
            LIMIT 1
            """,
            (int(file_id),),
        ).fetchone()
        if not file_row:
            raise HTTPException(404, "附件不存在")
        file_dict = dict(file_row)
        submission = _get_submission_for_teacher(conn, int(file_dict["submission_id"]), int(user["id"]))
        _ensure_submission_files_manageable(submission)
        resolved = resolve_submission_file_path(str(file_dict.get("stored_path") or ""))
        if resolved:
            physical_path = Path(resolved)

        answers_json = submission.get("answers_json")
        cleaned_answers_json = None
        if answers_json:
            try:
                answers_payload = json.loads(answers_json) if isinstance(answers_json, str) else answers_json
                cleaned_payload = remove_answer_attachment_references(answers_payload, file_dict)
                cleaned_answers_json = json.dumps(cleaned_payload, ensure_ascii=False)
            except (TypeError, json.JSONDecodeError):
                cleaned_answers_json = None

        try:
            begin_immediate_transaction(conn)
            current_submission = _get_submission_for_teacher(conn, int(file_dict["submission_id"]), int(user["id"]))
            _ensure_submission_files_manageable(current_submission)
            conn.execute("DELETE FROM submission_files WHERE id = ?", (int(file_id),))
            if cleaned_answers_json is not None:
                conn.execute(
                    "UPDATE submissions SET answers_json = ? WHERE id = ?",
                    (cleaned_answers_json, int(file_dict["submission_id"])),
                )
            _reset_submission_after_attachment_edit(conn, int(file_dict["submission_id"]), int(user["id"]))
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    if physical_path and physical_path.exists() and physical_path.is_file():
        try:
            physical_path.unlink()
        except Exception as exc:
            print(f"[SUBMISSION_FILES] failed to delete physical file {physical_path}: {exc}")

    return {
        "status": "success",
        "deleted_file_id": int(file_id),
        "submission_id": int(file_dict["submission_id"]),
    }


@router.post("/assignments/{assignment_id}/submit", response_class=JSONResponse)
async def submit_assignment(assignment_id: str,
                            answers_json: str = Form(""),
                            manifest: str = Form(""),
                            started_at: str = Form(""),
                            use_server_draft: bool = Form(False),
                            files: List[UploadFile] = File(default=[]),
                            user: dict = Depends(get_current_student)):
    """
    V4.4: 学生提交作业 — 支持 JSON 格式的答案 + 可选文件附件
    answers_json: 包含所有答题内容的 JSON 字符串
    files: 可选的附件文件列表
    """
    stage_attempt = None
    with get_db_connection() as conn:
        close_overdue_assignments(conn)
        conn.commit()
        assignment = conn.execute("SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
        if not assignment:
            raise HTTPException(404, "Assignment not found")
        assignment = enrich_assignment_runtime_view(assignment)
        if not student_can_access_assignment(conn, assignment_id, int(user["id"])):
            raise HTTPException(403, "该破境试炼只对指定学生开放")
        personal_stage_target = get_stage_exam_target(conn, assignment_id)

        submission = conn.execute(
            "SELECT * FROM submissions WHERE assignment_id = ? AND student_pk_id = ?",
            (assignment_id, user['id']),
        ).fetchone()
        existing_submission = dict(submission) if submission else None
        if existing_submission:
            is_absence_score = int(existing_submission.get("is_absence_score") or 0) == 1
            if is_absence_score:
                _ensure_accepting_submission(assignment)
            elif not submission_resubmission_accepts(existing_submission):
                if int(existing_submission.get("resubmission_allowed") or 0):
                    raise HTTPException(400, "重交时间已截止，请联系教师重新开放")
                raise HTTPException(400, "您已经提交过此作业")
        else:
            _ensure_accepting_submission(assignment)

        result = await _save_submission_payload(
            conn,
            assignment=assignment,
            student=dict(user),
            answers_json=answers_json,
            manifest=manifest,
            files=files,
            actor_role="student",
            actor_user_pk=int(user["id"]),
            channel="online",
            started_at=started_at,
            existing_submission=existing_submission,
            notify_teacher=personal_stage_target is None,
            use_server_draft_files=_form_bool(use_server_draft),
        )
        try:
            stage_attempt = mark_stage_submission_saved(conn, result["submission_id"])
        except Exception as exc:
            print(f"[LEARNING_PROGRESS] 破境试炼提交状态更新失败: {exc}")

    if assignment["class_offering_id"]:
        try:
            user_dict = dict(user)
            record_behavior_event(
                class_offering_id=int(assignment["class_offering_id"]),
                user_pk=int(user_dict["id"]),
                user_role="student",
                display_name=str(user_dict.get("name") or user_dict["id"]),
                action_type="assignment_submit",
                session_started_at=str(user_dict.get("login_time") or "").strip() or None,
                summary_text=f"提交作业：{assignment.get('title') or assignment_id}",
                payload={
                    "assignment_id": assignment_id,
                    "submission_id": result["submission_id"],
                    "stored_file_count": result["stored_file_count"],
                    "dropped_file_count": result["dropped_file_count"],
                    "has_text_answers": result["has_text_answers"],
                    "is_resubmission": result["is_replacement"],
                },
                page_key="assignment_detail",
            )
        except Exception as exc:
            print(f"[BEHAVIOR] 记录作业提交失败: {exc}")

    auto_ai_grading_scheduled = False
    if stage_attempt:
        stage_ai_task = submit_stage_exam_for_ai_grading(int(result["submission_id"]))
        try:
            asyncio.create_task(stage_ai_task)
            auto_ai_grading_scheduled = True
        except RuntimeError:
            await stage_ai_task
            auto_ai_grading_scheduled = True
    elif _assignment_uses_ai_grading(assignment):
        auto_ai_grading_scheduled = _schedule_ai_grading(int(result["submission_id"]), reason="assignment_auto")
        if not auto_ai_grading_scheduled:
            await _submit_ai_grading_background(int(result["submission_id"]), reason="assignment_auto")
            auto_ai_grading_scheduled = True

    grading_status = _resolve_grading_status(assignment, auto_ai_grading_scheduled)
    return {
        "status": "success",
        "submission_id": result["submission_id"],
        "stored_file_count": result["stored_file_count"],
        "dropped_file_count": result["dropped_file_count"],
        "dropped_files": result.get("dropped_files", []),
        "dropped_file_message": result.get("dropped_file_message", ""),
        "is_resubmission": result["is_replacement"],
        "auto_ai_grading_scheduled": auto_ai_grading_scheduled,
        "grading_status": grading_status,
        "is_late_submission": result.get("is_late_submission", False),
        "late_by_seconds": result.get("late_by_seconds", 0),
    }


@router.post("/assignments/{assignment_id}/submissions/withdraw", response_class=JSONResponse)
async def teacher_withdraw_submissions(
    assignment_id: str,
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    """教师撤回一个、多个或全部已提交记录，保留原提交内容并开放重交窗口。"""
    data = await request.json()
    scope = str(data.get("scope") or "").strip().lower()
    student_pk_ids = _parse_int_set(data.get("student_pk_ids"), "student_pk_ids")
    submission_ids = _parse_int_set(data.get("submission_ids"), "submission_ids")
    if scope != "all" and not student_pk_ids and not submission_ids:
        raise HTTPException(400, "请选择要撤回的学生或提交记录")

    try:
        resubmission_due_at = build_resubmission_due_at(data, default_minutes=120)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    with get_db_connection() as conn:
        close_overdue_assignments(conn)
        assignment = _get_assignment_for_teacher(conn, assignment_id, int(user["id"]))

        where_parts = ["assignment_id = ?"]
        params: list[Any] = [assignment_id]
        if scope != "all":
            id_clauses = []
            if student_pk_ids:
                placeholders = ",".join("?" for _ in student_pk_ids)
                id_clauses.append(f"student_pk_id IN ({placeholders})")
                params.extend(sorted(student_pk_ids))
            if submission_ids:
                placeholders = ",".join("?" for _ in submission_ids)
                id_clauses.append(f"id IN ({placeholders})")
                params.extend(sorted(submission_ids))
            where_parts.append("(" + " OR ".join(id_clauses) + ")")

        targets = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT id, student_pk_id, status
                FROM submissions
                WHERE {' AND '.join(where_parts)}
                """,
                tuple(params),
            )
        ]
        if not targets:
            return {
                "status": "success",
                "updated_count": 0,
                "resubmission_due_at": resubmission_due_at,
            }

        now_iso = datetime.now().replace(microsecond=0).isoformat()
        reason = str(data.get("reason") or "").strip() or None
        target_ids = [int(row["id"]) for row in targets]
        placeholders = ",".join("?" for _ in target_ids)
        conn.execute(
            f"""
            UPDATE submissions
            SET status = 'submitted',
                score = NULL,
                feedback_md = NULL,
                grading_started_at = NULL,
                grading_attempt_fingerprint = NULL,
                resubmission_allowed = 1,
                resubmission_due_at = ?,
                returned_at = ?,
                returned_by_teacher_id = ?,
                returned_reason = ?
            WHERE assignment_id = ?
              AND id IN ({placeholders})
            """,
            (resubmission_due_at, now_iso, int(user["id"]), reason, assignment_id, *target_ids),
        )
        conn.commit()

    if assignment.get("class_offering_id"):
        try:
            record_behavior_event(
                class_offering_id=int(assignment["class_offering_id"]),
                user_pk=int(user["id"]),
                user_role="teacher",
                display_name=str(user.get("name") or user["id"]),
                action_type="assignment_teacher_withdraw",
                session_started_at=str(user.get("login_time") or "").strip() or None,
                summary_text=f"撤回作业提交：{assignment.get('title') or assignment_id}",
                payload={
                    "assignment_id": assignment_id,
                    "scope": scope or "selected",
                    "updated_count": len(target_ids),
                    "resubmission_due_at": resubmission_due_at,
                },
                page_key="assignment_detail",
            )
        except Exception as exc:
            print(f"[BEHAVIOR] 记录教师撤回作业失败: {exc}")

    return {
        "status": "success",
        "updated_count": len(target_ids),
        "resubmission_due_at": resubmission_due_at,
    }


@router.post("/assignments/{assignment_id}/submissions/offline", response_class=JSONResponse)
async def teacher_offline_submit_assignment(
    assignment_id: str,
    student_pk_id: int = Form(...),
    answers_json: str = Form(""),
    manifest: str = Form(""),
    files: List[UploadFile] = File(default=[]),
    user: dict = Depends(get_current_teacher),
):
    """教师代学生线下提交作业或考试。已有提交必须先撤回，避免覆盖正式提交。"""
    with get_db_connection() as conn:
        close_overdue_assignments(conn)
        conn.commit()
        assignment = _get_assignment_for_teacher(conn, assignment_id, int(user["id"]))
        student = _get_student_for_assignment(conn, assignment, int(student_pk_id))
        existing = conn.execute(
            "SELECT * FROM submissions WHERE assignment_id = ? AND student_pk_id = ?",
            (assignment_id, student_pk_id),
        ).fetchone()
        existing_submission = dict(existing) if existing else None
        existing_is_absence_score = existing_submission and int(existing_submission.get("is_absence_score") or 0) == 1
        if existing_submission and not existing_is_absence_score and not int(existing_submission.get("resubmission_allowed") or 0):
            raise HTTPException(409, "该学生已有提交，请先撤回后再线下重交")

        result = await _save_submission_payload(
            conn,
            assignment=assignment,
            student=student,
            answers_json=answers_json,
            manifest=manifest,
            files=files,
            actor_role="teacher",
            actor_user_pk=int(user["id"]),
            channel="offline",
            existing_submission=existing_submission,
            notify_teacher=False,
        )

    if assignment.get("class_offering_id"):
        try:
            record_behavior_event(
                class_offering_id=int(assignment["class_offering_id"]),
                user_pk=int(user["id"]),
                user_role="teacher",
                display_name=str(user.get("name") or user["id"]),
                action_type="assignment_offline_submit",
                session_started_at=str(user.get("login_time") or "").strip() or None,
                summary_text=f"线下代交作业：{assignment.get('title') or assignment_id}",
                payload={
                    "assignment_id": assignment_id,
                    "student_pk_id": student_pk_id,
                    "submission_id": result["submission_id"],
                    "stored_file_count": result["stored_file_count"],
                    "dropped_file_count": result["dropped_file_count"],
                    "is_replacement": result["is_replacement"],
                },
                page_key="assignment_detail",
            )
        except Exception as exc:
            print(f"[BEHAVIOR] 记录教师线下代交失败: {exc}")

    auto_ai_grading_scheduled = False
    if _assignment_uses_ai_grading(assignment):
        auto_ai_grading_scheduled = _schedule_ai_grading(int(result["submission_id"]), reason="teacher_offline_auto")
        if not auto_ai_grading_scheduled:
            await _submit_ai_grading_background(int(result["submission_id"]), reason="teacher_offline_auto")
            auto_ai_grading_scheduled = True

    return {
        "status": "success",
        "submission_id": result["submission_id"],
        "stored_file_count": result["stored_file_count"],
        "dropped_file_count": result["dropped_file_count"],
        "dropped_files": result.get("dropped_files", []),
        "dropped_file_message": result.get("dropped_file_message", ""),
        "is_replacement": result["is_replacement"],
        "auto_ai_grading_scheduled": auto_ai_grading_scheduled,
    }


@router.delete("/assignments/{assignment_id}/withdraw", response_class=JSONResponse)
async def withdraw_submission(assignment_id: str, user: dict = Depends(get_current_student)):
    """学生撤回已提交的作业（仅限未批改的提交）"""
    with get_db_connection() as conn:
        close_overdue_assignments(conn)
        conn.commit()
        submission = conn.execute(
            """
            SELECT s.*, a.course_id, a.class_offering_id, a.title,
                   a.status AS assignment_status,
                   a.availability_mode, a.starts_at, a.due_at, a.duration_minutes, a.auto_close,
                   a.late_submission_enabled, a.late_submission_until,
                   a.late_penalty_strategy, a.late_penalty_interval_hours,
                   a.late_penalty_points, a.late_penalty_min_score, a.late_score_cap
            FROM submissions s
            JOIN assignments a ON a.id = s.assignment_id
            WHERE s.assignment_id = ? AND s.student_pk_id = ?
            """,
            (assignment_id, user['id'])
        ).fetchone()
        if not submission:
            raise HTTPException(404, "未找到提交记录")
        submission = dict(submission)
        assignment_snapshot = {
            "status": submission.get("assignment_status"),
            "availability_mode": submission.get("availability_mode"),
            "starts_at": submission.get("starts_at"),
            "due_at": submission.get("due_at"),
            "duration_minutes": submission.get("duration_minutes"),
            "auto_close": submission.get("auto_close"),
            "late_submission_enabled": submission.get("late_submission_enabled"),
            "late_submission_until": submission.get("late_submission_until"),
            "late_penalty_strategy": submission.get("late_penalty_strategy"),
            "late_penalty_interval_hours": submission.get("late_penalty_interval_hours"),
            "late_penalty_points": submission.get("late_penalty_points"),
            "late_penalty_min_score": submission.get("late_penalty_min_score"),
            "late_score_cap": submission.get("late_score_cap"),
        }
        if not assignment_accepts_submissions(assignment_snapshot):
            raise HTTPException(400, "作业已截止，当前只能查看，不能撤回提交")
        if int(submission.get("resubmission_allowed") or 0):
            raise HTTPException(400, "教师已撤回该提交，请直接重新提交，旧提交将保留到新版本提交成功")
        if submission['status'] == 'graded':
            raise HTTPException(400, "已批改的作业无法撤回")
        if submission['status'] == 'grading':
            raise HTTPException(400, "正在批改中的作业无法撤回")

        conn.execute("DELETE FROM submission_files WHERE submission_id = ?", (submission['id'],))
        conn.execute("DELETE FROM submissions WHERE id = ?", (submission['id'],))
        conn.commit()

    user_dict = dict(user)  # 转换
    delete_storage_tree(_build_submission_storage_dir(submission['course_id'], assignment_id, user_dict.get('id')))
    if submission["class_offering_id"]:
        try:
            record_behavior_event(
                class_offering_id=int(submission["class_offering_id"]),
                user_pk=int(user_dict["id"]),
                user_role="student",
                display_name=str(user_dict.get("name") or user_dict["id"]),
                action_type="assignment_withdraw",
                session_started_at=str(user_dict.get("login_time") or "").strip() or None,
                summary_text=f"撤回作业：{submission.get('title') or assignment_id}",
                payload={
                    "assignment_id": assignment_id,
                    "submission_id": submission["id"],
                },
                page_key="assignment_detail",
            )
        except Exception as exc:
            print(f"[BEHAVIOR] 记录作业撤回失败: {exc}")
    return {"status": "success", "message": "作业已撤回"}
