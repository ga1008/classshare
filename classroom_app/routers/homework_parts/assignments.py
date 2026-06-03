from .common import *


router = APIRouter()


@router.post("/courses/{course_id}/assignments", response_class=JSONResponse)
async def create_assignment(course_id: int, request: Request, user: dict = Depends(get_current_teacher)):
    """V4.0: 在指定课程下创建新作业"""
    data = await request.json()
    created_at = datetime.now().isoformat()
    class_offering_id = data.get('class_offering_id')
    allowed_file_types_json = encode_allowed_file_types_json(_get_allowed_file_types(data))
    learning_stage_key = _get_learning_stage_key(data, class_offering_id=class_offering_id)
    try:
        schedule_fields = build_assignment_schedule_fields(
            data,
            default_status="new",
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    with get_db_connection() as conn:
        close_overdue_assignments(conn)
        actual_course_id = course_id
        if class_offering_id:
            offering = conn.execute(
                "SELECT id, course_id FROM class_offerings WHERE id = ? AND teacher_id = ?",
                (int(class_offering_id), user['id'])
            ).fetchone()
            if not offering:
                raise HTTPException(404, "当前课堂不存在或您无权操作")
            actual_course_id = int(offering['course_id'])
        else:
            owned_course = conn.execute(
                "SELECT id FROM courses WHERE id = ? AND created_by_teacher_id = ?",
                (course_id, user['id'])
            ).fetchone()
            if not owned_course:
                raise HTTPException(404, "课程不存在或您无权操作")

        cursor = conn.execute(
            """
            INSERT INTO assignments (
                course_id, title, status, requirements_md, rubric_md, grading_mode,
                class_offering_id, created_at, allowed_file_types_json,
                availability_mode, starts_at, due_at, duration_minutes, auto_close, closed_at,
                late_submission_enabled, late_submission_until, late_penalty_strategy,
                late_penalty_interval_hours, late_penalty_points, late_penalty_min_score, late_score_cap,
                learning_stage_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                actual_course_id,
                data['title'],
                schedule_fields["status"],
                data.get('requirements_md', ''),
                data.get('rubric_md', ''),
                data.get('grading_mode', 'manual'),
                int(class_offering_id) if class_offering_id else None,
                created_at,
                allowed_file_types_json,
                schedule_fields["availability_mode"],
                schedule_fields["starts_at"],
                schedule_fields["due_at"],
                schedule_fields["duration_minutes"],
                schedule_fields["auto_close"],
                schedule_fields["closed_at"],
                schedule_fields["late_submission_enabled"],
                schedule_fields["late_submission_until"],
                schedule_fields["late_penalty_strategy"],
                schedule_fields["late_penalty_interval_hours"],
                schedule_fields["late_penalty_points"],
                schedule_fields["late_penalty_min_score"],
                schedule_fields["late_score_cap"],
                learning_stage_key,
            )
        )
        new_id = cursor.lastrowid
        if schedule_fields["status"] == "published":
            try:
                create_assignment_published_notifications(
                    conn,
                    new_id,
                    send_email_notification=_wants_assignment_email_notification(data),
                )
            except Exception as exc:
                print(f"[MESSAGE_CENTER] assignment publish notify failed: {exc}")
        conn.commit()
    # 作业文件夹现在按 Course / Assignment 组织
    assignment_dir = _build_assignment_storage_dir(actual_course_id, new_id)
    assignment_dir.mkdir(parents=True, exist_ok=True)
    return {
        "status": "success",
        "new_assignment_id": new_id,
        "assignment_status": schedule_fields["status"],
        "due_at": schedule_fields["due_at"],
    }


@router.put("/assignments/{assignment_id}", response_class=JSONResponse)
async def update_assignment(assignment_id: str, request: Request, user: dict = Depends(get_current_teacher)):
    data = await request.json()
    with get_db_connection() as conn:
        close_overdue_assignments(conn)
        assignment = conn.execute(
            """SELECT a.*,
                      c.created_by_teacher_id,
                      o.teacher_id AS offering_teacher_id
               FROM assignments a
               JOIN courses c ON a.course_id = c.id
               LEFT JOIN class_offerings o ON o.id = a.class_offering_id
               WHERE a.id = ?""",
            (assignment_id,)
        ).fetchone()
        if not assignment:
            raise HTTPException(404, "作业不存在")
        if not _teacher_can_access_assignment(conn, dict(assignment), int(user["id"])):
            raise HTTPException(403, "无权修改该作业")
        if is_personal_stage_exam_assignment(conn, assignment_id):
            _hide_personal_stage_asset()
        assignment_dict = dict(assignment)
        assignment_dict = refresh_assignment_runtime_status(conn, assignment_dict)

        previous_status = str(assignment_dict['status'] or '')
        allowed_file_types_json = encode_allowed_file_types_json(_get_allowed_file_types(data, assignment_dict))
        if "learning_stage_key" in data or "stage_key" in data:
            learning_stage_key = _get_learning_stage_key(
                data,
                class_offering_id=assignment_dict.get("class_offering_id"),
            )
        else:
            learning_stage_key = assignment_dict.get("learning_stage_key")
        try:
            schedule_fields = build_assignment_schedule_fields(
                data,
                existing=assignment_dict,
                default_status=assignment_dict["status"],
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        conn.execute(
            """
            UPDATE assignments
            SET title = ?, requirements_md = ?, rubric_md = ?, grading_mode = ?,
                status = ?, allowed_file_types_json = ?,
                availability_mode = ?, starts_at = ?, due_at = ?, duration_minutes = ?, auto_close = ?, closed_at = ?,
                late_submission_enabled = ?, late_submission_until = ?, late_penalty_strategy = ?,
                late_penalty_interval_hours = ?, late_penalty_points = ?, late_penalty_min_score = ?, late_score_cap = ?,
                learning_stage_key = ?
            WHERE id = ?
            """,
            (
                data['title'],
                data.get('requirements_md', ''),
                data.get('rubric_md', ''),
                data.get('grading_mode', assignment_dict['grading_mode']),
                schedule_fields["status"],
                allowed_file_types_json,
                schedule_fields["availability_mode"],
                schedule_fields["starts_at"],
                schedule_fields["due_at"],
                schedule_fields["duration_minutes"],
                schedule_fields["auto_close"],
                schedule_fields["closed_at"],
                schedule_fields["late_submission_enabled"],
                schedule_fields["late_submission_until"],
                schedule_fields["late_penalty_strategy"],
                schedule_fields["late_penalty_interval_hours"],
                schedule_fields["late_penalty_points"],
                schedule_fields["late_penalty_min_score"],
                schedule_fields["late_score_cap"],
                learning_stage_key,
                assignment_id,
            )
        )
        if previous_status != 'published' and schedule_fields["status"] == 'published':
            try:
                create_assignment_published_notifications(
                    conn,
                    assignment_id,
                    send_email_notification=_wants_assignment_email_notification(data),
                )
            except Exception as exc:
                print(f"[MESSAGE_CENTER] assignment publish notify failed: {exc}")
        conn.commit()
    return {
        "status": "success",
        "updated_assignment_id": assignment_id,
        "assignment_status": schedule_fields["status"],
        "due_at": schedule_fields["due_at"],
    }


@router.delete("/assignments/{assignment_id}", response_class=JSONResponse)
async def delete_assignment(assignment_id: str, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        assignment = conn.execute(
            """SELECT a.id,
                      a.course_id,
                      a.class_offering_id,
                      c.created_by_teacher_id,
                      o.teacher_id AS offering_teacher_id
               FROM assignments a
               JOIN courses c ON a.course_id = c.id
               LEFT JOIN class_offerings o ON o.id = a.class_offering_id
               WHERE a.id = ?""",
            (assignment_id,)
        ).fetchone()
        if not assignment:
            raise HTTPException(404, "作业不存在")
        if not _teacher_can_access_assignment(conn, dict(assignment), int(user["id"])):
            raise HTTPException(403, "无权删除该作业")
        if is_personal_stage_exam_assignment(conn, assignment_id):
            _hide_personal_stage_asset()

        conn.execute("DELETE FROM assignments WHERE id = ?", (assignment_id,))
        conn.commit()
    delete_storage_tree(_build_assignment_storage_dir(assignment['course_id'], assignment_id))
    return {"status": "success", "deleted_assignment_id": assignment_id}


@router.get("/assignments/time-state", response_class=JSONResponse)
async def get_assignment_time_state(request: Request, user: dict = Depends(get_current_user)):
    raw_ids = request.query_params.get("ids") or request.query_params.get("assignment_ids") or ""
    assignment_ids = []
    for part in str(raw_ids).split(","):
        text = part.strip()
        if not text:
            continue
        try:
            assignment_ids.append(int(text))
        except ValueError as exc:
            raise HTTPException(400, "作业 ID 格式无效") from exc
    assignment_ids = list(dict.fromkeys(assignment_ids))[:50]
    now_dt = utc_like_now()
    if not assignment_ids:
        return {"status": "success", "server_now": now_dt.isoformat(), "assignments": []}

    with get_db_connection() as conn:
        close_overdue_assignments(conn, now_dt=now_dt)
        placeholders = ",".join("?" for _ in assignment_ids)
        rows = conn.execute(
            f"""
            SELECT a.*,
                   c.created_by_teacher_id,
                   o.teacher_id AS offering_teacher_id
            FROM assignments a
            JOIN courses c ON c.id = a.course_id
            LEFT JOIN class_offerings o ON o.id = a.class_offering_id
            WHERE a.id IN ({placeholders})
            """,
            tuple(assignment_ids),
        ).fetchall()
        assignments = []
        for row in rows:
            item = dict(row)
            if user.get("role") == "teacher":
                if not _teacher_can_access_assignment(conn, item, int(user["id"])):
                    continue
            elif user.get("role") == "student":
                if str(item.get("status") or "").strip().lower() == "new":
                    continue
                if not student_can_access_assignment(conn, str(item["id"]), int(user["id"])):
                    continue
            else:
                continue
            item = enrich_assignment_runtime_view(item, now_dt=now_dt)
            assignments.append(serialize_assignment_time_state(item, now_dt=now_dt))
        conn.commit()

    return {"status": "success", "server_now": now_dt.isoformat(), "assignments": assignments}


@router.get("/courses/{course_id}/assignment-stats", response_class=JSONResponse)
async def get_course_assignment_stats(course_id: int, user: dict = Depends(get_current_teacher)):
    """课程维度统计：汇总某课程下所有作业的提交率、批改进度和平均分。"""
    with get_db_connection() as conn:
        owned = conn.execute(
            "SELECT id FROM courses WHERE id = ? AND created_by_teacher_id = ?",
            (course_id, user["id"]),
        ).fetchone()
        if not owned:
            raise HTTPException(404, "课程不存在或无权访问")

        assignments = [
            dict(row)
            for row in conn.execute(
                """
                SELECT a.id, a.title, a.status, a.grading_mode, a.class_offering_id,
                       a.due_at, a.availability_mode
                FROM assignments a
                WHERE a.course_id = ?
                  AND NOT EXISTS (
                      SELECT 1 FROM learning_stage_exam_attempts lsea
                      WHERE lsea.assignment_id = a.id
                  )
                ORDER BY a.created_at DESC
                """,
                (course_id,),
            )
        ]

        stats_list = []
        for a in assignments:
            sub_stats = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status = 'graded' THEN 1 ELSE 0 END) AS graded,
                    SUM(CASE WHEN status = 'submitted' THEN 1 ELSE 0 END) AS submitted,
                    SUM(CASE WHEN status = 'grading' THEN 1 ELSE 0 END) AS grading,
                    SUM(CASE WHEN is_absence_score = 1 THEN 1 ELSE 0 END) AS absence,
                    ROUND(AVG(CASE WHEN status = 'graded' THEN score END), 1) AS avg_score,
                    MAX(CASE WHEN status = 'graded' THEN score END) AS max_score,
                    MIN(CASE WHEN status = 'graded' THEN score END) AS min_score
                FROM submissions
                WHERE assignment_id = ?
                """,
                (a["id"],),
            ).fetchone()
            row = dict(sub_stats)
            stats_list.append({
                "assignment_id": a["id"],
                "title": a["title"],
                "status": a.get("effective_status") or a["status"],
                **row,
            })
        conn.commit()

    return {"status": "success", "course_id": course_id, "assignments": stats_list}
