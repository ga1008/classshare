from .common import *


router = APIRouter()


@router.get("/classroom/{class_offering_id}", response_class=HTMLResponse)
def classroom_main(
    request: Request,
    class_offering_id: int,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
):
    """V4.0: 替换旧的 /app，这是特定班级课堂的主界面"""
    student_security_summary = None
    classroom_page = None
    teacher_daily_sync_task_id = None
    with get_db_connection() as conn:
        offering = conn.execute(
            """SELECT o.*,
                      COALESCE(s.name, o.semester) as semester_display,
                      c.name as course_name,
                      c.description as course_description,
                      c.credits as course_credits,
                      cl.name as class_name,
                      cl.description as class_description,
                      t.name as teacher_name,
                      tb.title as textbook_title,
                      (
                          SELECT COUNT(*)
                          FROM students s
                          WHERE s.class_id = o.class_id
                            AND COALESCE(s.enrollment_status, 'active') = 'active'
                      ) as class_student_count
               FROM class_offerings o
                        JOIN courses c ON o.course_id = c.id
                        JOIN classes cl ON o.class_id = cl.id
                        JOIN teachers t ON o.teacher_id = t.id
                        LEFT JOIN academic_semesters s ON s.id = o.semester_id
                        LEFT JOIN textbooks tb ON tb.id = o.textbook_id
               WHERE o.id = ?""",
            (class_offering_id,)
        ).fetchone()

        if not offering: raise HTTPException(404, "未找到此课堂")

        offering_data = dict(offering)
        offering_data["semester"] = offering_data.get("semester_display") or offering_data.get("semester")
        offering_data = attach_home_learning_material_briefs(
            conn,
            [offering_data],
            teacher_id=int(offering_data["teacher_id"]),
            markdown_only=True,
        )[0]
        course_id = offering_data['course_id']

        if user['role'] == 'student':
            student_class = conn.execute(
                """
                SELECT class_id, COALESCE(enrollment_status, 'active') AS enrollment_status
                FROM students
                WHERE id = ?
                """,
                (user['id'],),
            ).fetchone()
            if (
                not student_class
                or student_class['class_id'] != offering_data['class_id']
                or normalize_student_enrollment_status(student_class["enrollment_status"]) != STUDENT_STATUS_ACTIVE
            ):
                raise HTTPException(403, "您未加入此课堂")
            student_security_summary = build_student_security_summary(conn, int(user['id']))
            try:
                maybe_send_student_attendance_alert(
                    conn,
                    class_offering_id=int(class_offering_id),
                    student_id=int(user["id"]),
                )
            except Exception as exc:
                print(f"[SMART_ATTENDANCE] 学生考勤提醒创建失败: {exc}")
                try:
                    conn.rollback()
                except Exception:
                    pass
        elif user['role'] == 'teacher':
            if offering_data['teacher_id'] != user['id']:
                raise HTTPException(403, "您不是此课堂的教师")
            try:
                teacher_daily_sync_task_id = maybe_enqueue_teacher_daily_checkin_sync(
                    conn,
                    class_offering_id=int(class_offering_id),
                    teacher_id=int(user["id"]),
                )
            except Exception as exc:
                print(f"[SMART_ATTENDANCE] 教师每日后台同步任务入队失败: {exc}")
                try:
                    conn.rollback()
                except Exception:
                    pass

        if user['role'] == 'teacher':
            files_cursor = conn.execute(
                "SELECT * FROM course_files WHERE course_id = ?",
                (course_id,)
            )
        else:
            files_cursor = conn.execute(
                "SELECT * FROM course_files WHERE course_id = ? AND is_public = TRUE AND is_teacher_resource = FALSE",
                (course_id,)
            )

        def format_size(size_bytes: int) -> str:
            """辅助函数：将字节大小转换为人类可读格式"""
            for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
                if size_bytes < 1024:
                    return f"{size_bytes:.2f} {unit}"
                size_bytes /= 1024
            return f"{size_bytes:.2f} PB"

        # 修复：从 V3.2 复制，但 V4.0 还不支持显示大小
        files_info = [{"id": row['id'], "name": row['file_name'], "size": format_size(row['file_size'])} for row in files_cursor]

        close_overdue_assignments(conn)
        teacher_assignment_filter = (
            f"AND {personal_stage_assignment_filter_sql('assignments')}"
            if user["role"] == "teacher"
            else ""
        )
        assignments_cursor = conn.execute(
            f"""
            SELECT *
            FROM assignments
            WHERE course_id = ? AND class_offering_id = ?
            {teacher_assignment_filter}
            ORDER BY created_at DESC
            """,
            (course_id, class_offering_id)
        )
        assignments = []
        for row in assignments_cursor:
            assignment = _enrich_assignment_upload_config(dict(row))
            if user['role'] == 'student':
                if not student_can_access_assignment(conn, assignment["id"], int(user["id"])):
                    continue
                if assignment['status'] == 'new': continue
                submission = conn.execute(
                    """
                    SELECT id, status, score, feedback_md, resubmission_allowed, resubmission_due_at
                    FROM submissions
                    WHERE assignment_id = ? AND student_pk_id = ?
                    """,
                    (assignment['id'], user['id'])
                ).fetchone()
                if submission:
                    submission_dict = dict(submission)
                    can_resubmit = submission_resubmission_accepts(submission_dict)
                    assignment['can_resubmit_submission'] = can_resubmit
                    assignment['resubmission_state'] = submission_resubmission_state(submission_dict)
                    assignment['resubmission_due_at'] = submission_dict.get('resubmission_due_at')
                    assignment['submission_status'] = submission_effective_status(submission_dict)
                    assignment['submission_score'] = submission['score']
                    assignment['submission_id'] = submission['id']
                    assignment['submission_feedback_md'] = submission['feedback_md']
                    assignment['submission_feedback_preview'] = _plain_feedback_preview(submission['feedback_md'])
                else:
                    assignment['submission_status'] = 'unsubmitted'
                    assignment['can_resubmit_submission'] = False
                    assignment['resubmission_state'] = 'none'
                    assignment['submission_id'] = None
                    assignment['submission_feedback_md'] = None
                    assignment['submission_feedback_preview'] = ""
            assignments.append(assignment)

        if user['role'] == 'teacher':
            _attach_teacher_assignment_card_metrics(conn, assignments, offering_data)

        session_rows = conn.execute(
            """
            SELECT id,
                   course_lesson_id,
                   order_index,
                   title,
                   content,
                   section_count,
                   slot_section_count,
                   session_date,
                   weekday,
                   week_index,
                   learning_material_id,
                   schedule_source,
                   academic_occurrence_id,
                   academic_sync_item_id,
                   academic_course_code,
                   academic_teaching_class_name,
                   academic_weeks_text,
                   academic_section_text,
                   academic_time_text,
                   academic_campus,
                   academic_location,
                   academic_classroom_id,
                   academic_classroom_code,
                   academic_classroom_type,
                   schedule_status,
                   is_non_periodic,
                   schedule_note,
                   schedule_metadata_json
            FROM class_offering_sessions
            WHERE class_offering_id = ?
            ORDER BY order_index, session_date
            """,
            (class_offering_id,),
        ).fetchall()
        session_items = attach_learning_material_briefs(
            conn,
            [dict(row) for row in session_rows],
            teacher_id=int(offering_data["teacher_id"]),
            markdown_only=True,
        )
        attach_generation_tasks(
            conn,
            session_items,
            teacher_id=int(offering_data["teacher_id"]),
        )
        teaching_plan = decorate_offering_sessions(
            session_items,
            home_material=offering_data.get("home_learning_material"),
            include_home_placeholder=user["role"] == "teacher",
        )
        academic_course_exams = load_classroom_course_exam_status_for_user(
            conn,
            class_offering_id=class_offering_id,
            user=user,
        )
        teaching_plan = merge_course_exams_into_teaching_plan(
            teaching_plan,
            academic_course_exams,
        )
        if teaching_plan.get("schedule_summary") and not offering_data.get("schedule_info"):
            offering_data["schedule_info"] = teaching_plan["schedule_summary"]

        classroom_page = build_classroom_page_context(
            conn=conn,
            user=user,
            classroom=offering_data,
            assignments=assignments,
            shared_files=files_info,
        )
        classroom_page["teaching_plan"] = teaching_plan
        classroom_page["academic_course_exams"] = academic_course_exams
        if user["role"] == "student":
            try:
                classroom_page["learning_progress"] = serialize_student_learning_progress(
                    conn,
                    class_offering_id,
                    int(user["id"]),
                )
            except Exception as exc:
                print(f"[LEARNING_PROGRESS] 学生修为信息加载失败: {exc}")
                try:
                    conn.rollback()
                except Exception:
                    pass
                classroom_page["learning_progress"] = None
        else:
            try:
                classroom_page["learning_overview"] = build_class_learning_overview(
                    conn,
                    class_offering_id,
                )
            except Exception as exc:
                print(f"[LEARNING_PROGRESS] 课堂修为概览加载失败: {exc}")
                try:
                    conn.rollback()
                except Exception:
                    pass
                classroom_page["learning_overview"] = None

    try:
        record_behavior_event(
            class_offering_id=class_offering_id,
            user_pk=int(user["id"]),
            user_role=str(user["role"]),
            display_name=str(user.get("name") or user.get("username") or user["id"]),
            action_type="page_view",
            session_started_at=str(user.get("login_time") or "").strip() or None,
            summary_text=f"进入课堂页面：{offering_data.get('course_name') or class_offering_id}",
            payload={
                "page": "classroom_main",
                "class_name": offering_data.get("class_name"),
                "course_name": offering_data.get("course_name"),
            },
            page_key="classroom_discussion",
        )
    except Exception as exc:
        print(f"[BEHAVIOR] 记录课堂页面访问失败: {exc}")

    try:
        schedule_discussion_mood_refresh_soon(
            class_offering_id,
            reason="page_view",
        )
    except Exception as exc:
        print(f"[DISCUSSION_MOOD] 课堂页面预热失败: {exc}")

    if teacher_daily_sync_task_id:
        background_tasks.add_task(
            run_teacher_daily_checkin_sync_task,
            int(teacher_daily_sync_task_id),
            teacher_id=int(user["id"]),
            class_offering_id=int(class_offering_id),
        )

    return templates.TemplateResponse(request, "classroom_main_v4.html", {
        "request": request,
        "user_info": user,
        "classroom": offering_data,
        "classroom_page": classroom_page,
        "shared_files": files_info,
        "assignments": assignments,
        "student_security_summary": student_security_summary,
        "learning_stage_options": get_learning_stage_options(),
    })
