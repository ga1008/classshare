from .common import *


router = APIRouter()


@router.get("/manage/exams", response_class=HTMLResponse)
async def manage_exams_page(request: Request, user: dict = Depends(get_current_teacher)):
    """试卷库管理页面"""
    def _extract_exam_metrics(question_data: Any) -> dict[str, Any]:
        pages = question_data.get("pages", []) if isinstance(question_data, dict) else []
        if not isinstance(pages, list):
            pages = []

        type_counts: dict[str, int] = {}
        question_count = 0
        total_points = 0.0

        for page in pages:
            questions = page.get("questions", []) if isinstance(page, dict) else []
            if not isinstance(questions, list):
                continue
            for question in questions:
                if not isinstance(question, dict):
                    continue
                question_count += 1
                qtype = str(question.get("type") or "").strip()
                if qtype:
                    type_counts[qtype] = type_counts.get(qtype, 0) + 1

                point_value = question.get("points") if question.get("points") is not None else question.get("score")
                if point_value is None:
                    point_value = question.get("max_score")
                if point_value is None and isinstance(question.get("grading"), dict):
                    point_value = question["grading"].get("points")
                try:
                    total_points += float(point_value or 0)
                except (TypeError, ValueError):
                    pass

        question_types = set(type_counts)
        objective_types = {"radio", "checkbox"}
        subjective_types = {"text", "textarea"}
        if question_count == 0:
            profile = "empty"
        elif question_types and question_types <= objective_types:
            profile = "objective"
        elif question_types and question_types <= subjective_types:
            profile = "subjective"
        else:
            profile = "mixed"

        return {
            "page_count": len(pages),
            "question_count": question_count,
            "total_points": round(total_points, 1),
            "question_type_counts": type_counts,
            "question_profile": profile,
            "question_types": sorted(question_types),
        }

    def _resolve_exam_source(paper: dict[str, Any]) -> str:
        if paper.get("ai_gen_task_id") or paper.get("ai_gen_status"):
            return "ai"
        return "manual"

    with get_db_connection() as conn:
        # 兼容旧版本：已完成但仍停留在 generating 的试卷应进入可用状态。
        conn.execute(
            """UPDATE exam_papers SET status = 'ready', updated_at = ?
               WHERE teacher_id = ? AND status = 'generating' AND ai_gen_status = 'completed'""",
            (datetime.now().isoformat(), user['id'])
        )
        conn.commit()

        current_teacher_is_super_admin = is_super_admin_teacher(conn, user.get("id"))
        papers_cursor = conn.execute(
            """SELECT ep.*,
                      t.name AS owner_teacher_name,
                      (SELECT COUNT(*)
                       FROM assignments a
                       WHERE a.exam_paper_id = ep.id
                         AND NOT EXISTS (
                             SELECT 1 FROM learning_stage_exam_attempts lsea
                             WHERE lsea.assignment_id = a.id
                         )) as assigned_count
               FROM exam_papers ep
               LEFT JOIN teachers t ON t.id = ep.teacher_id
               WHERE (? = 1 OR ep.teacher_id = ? OR COALESCE(ep.scope_level, 'private') != 'private')
                 AND NOT EXISTS (
                     SELECT 1 FROM learning_stage_exam_attempts lsea
                     WHERE lsea.exam_paper_id = ep.id
                 )
               ORDER BY ep.updated_at DESC""",
            (1 if current_teacher_is_super_admin else 0, user['id'])
        )
        papers = []
        for row in papers_cursor:
            paper = dict(row)
            if not teacher_can_use_exam_paper(conn, int(user["id"]), paper):
                continue
            paper["is_owned"] = int(paper.get("teacher_id") or 0) == int(user["id"])
            paper["can_manage"] = teacher_can_manage_exam_paper(conn, int(user["id"]), paper)
            paper["is_shared_paper"] = not paper["is_owned"]
            paper["scope_level"] = _normalize_exam_open_scope(paper.get("scope_level"), default=SCOPE_PRIVATE)
            paper["scope_label"] = _exam_scope_label(paper["scope_level"])
            # 解析 questions_json
            if paper.get('questions_json'):
                try:
                    paper['questions_json'] = json.loads(paper['questions_json'])
                except (json.JSONDecodeError, TypeError):
                    paper['questions_json'] = None
            # 解析 tags_json
            if paper.get('tags_json'):
                try:
                    paper['tags_json'] = json.loads(paper['tags_json'])
                except (json.JSONDecodeError, TypeError):
                    paper['tags_json'] = []
            else:
                paper['tags_json'] = []
            metrics = _extract_exam_metrics(paper.get('questions_json'))
            paper.update(metrics)
            paper['source_type'] = _resolve_exam_source(paper)
            papers.append(paper)

    return templates.TemplateResponse(
        request,
        "manage/exams.html",
        _build_manage_template_context(
            request,
            user,
            page_title="试卷管理",
            active_page="exams",
            extra={
                "papers": papers,
                "learning_stage_options": get_learning_stage_options(),
            },
        ),
    )


@router.get("/exam/{exam_id}/edit", response_class=HTMLResponse)
async def exam_editor_page(request: Request, exam_id: str, user: dict = Depends(get_current_teacher)):
    """试卷编辑器页面"""
    with get_db_connection() as conn:
        paper = conn.execute(
            "SELECT * FROM exam_papers WHERE id = ? AND teacher_id = ?",
            (exam_id, user['id'])
        ).fetchone()
        if not paper:
            raise HTTPException(404, "试卷不存在")
        if is_personal_stage_exam_paper(conn, exam_id):
            raise HTTPException(404, "学生个人试炼不进入教师试卷库")

        # 获取教师所有课堂（用于分配）
        offerings = conn.execute(
            """SELECT o.id, c.name as class_name, co.name as course_name
               FROM class_offerings o
               JOIN classes c ON o.class_id = c.id
               JOIN courses co ON o.course_id = co.id
               WHERE o.teacher_id = ?
               ORDER BY co.name""",
            (user['id'],)
        ).fetchall()

    return templates.TemplateResponse(request, "exam_editor.html", {
        "request": request,
        "user_info": user,
        "paper": dict(paper),
        "offerings": [dict(row) for row in offerings]
    })


@router.get("/exam/new", response_class=HTMLResponse)
async def exam_new_page(request: Request, user: dict = Depends(get_current_teacher)):
    """新建试卷页面"""
    with get_db_connection() as conn:
        offerings = conn.execute(
            """SELECT o.id, c.name as class_name, co.name as course_name
               FROM class_offerings o
               JOIN classes c ON o.class_id = c.id
               JOIN courses co ON o.course_id = co.id
               WHERE o.teacher_id = ?
               ORDER BY co.name""",
            (user['id'],)
        ).fetchall()

    return templates.TemplateResponse(request, "exam_editor.html", {
        "request": request,
        "user_info": user,
        "paper": None,
        "offerings": [dict(row) for row in offerings]
    })


@router.get("/exam/take/{assignment_id}", response_class=HTMLResponse)
def exam_take_page(request: Request, assignment_id: str, user: dict = Depends(get_current_user)):
    """学生考试界面"""
    with get_db_connection() as conn:
        close_overdue_assignments(conn)
        assignment = conn.execute("SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
        if not assignment:
            raise HTTPException(404, "作业不存在")
        assignment = refresh_assignment_runtime_status(conn, assignment)
        assignment = _enrich_assignment_upload_config(dict(assignment))
        assignment_back_url = _assignment_back_url(assignment)
        if user["role"] == "student" and not student_can_access_assignment(conn, assignment_id, int(user["id"])):
            raise HTTPException(403, "该破境试炼只对指定学生开放")

        if not assignment.get('exam_paper_id'):
            # 不是试卷型作业，跳转到普通作业页
            return RedirectResponse(url=f"/assignment/{assignment_id}")

        if user['role'] == 'student' and assignment['status'] == 'new':
            return templates.TemplateResponse(request, "status.html",
                {"request": request, "success": False, "message": "该考试尚未发布", "back_url": assignment_back_url})

        paper = conn.execute("SELECT * FROM exam_papers WHERE id = ?", (assignment['exam_paper_id'],)).fetchone()
        if not paper:
            raise HTTPException(404, "试卷不存在")
        paper_dict = dict(paper)
        paper_data = _load_json_object(paper_dict.get("questions_json"))
        exam_config = _load_json_object(paper_dict.get("exam_config_json"))
        exam_ai_allowed = (
            user["role"] == "student"
            and bool(assignment.get("class_offering_id"))
            and _exam_allows_student_ai(paper_data, exam_config)
        )
        exam_ai_context = _build_exam_ai_context(assignment, paper_dict, paper_data) if exam_ai_allowed else ""
        if user["role"] == "student":
            paper_dict["questions_json"] = json.dumps(strip_exam_scoring_for_student(paper_data), ensure_ascii=False)

        # 检查学生是否已提交
        submission = None
        submission_files = []
        if user['role'] == 'student':
            submission_row = conn.execute(
                "SELECT * FROM submissions WHERE assignment_id = ? AND student_pk_id = ?",
                (assignment_id, user['id'])
            ).fetchone()
            submission = dict(submission_row) if submission_row else None
            if submission and int(submission.get("is_absence_score") or 0):
                submission = None
            if submission:
                files_cursor = conn.execute(
                    "SELECT * FROM submission_files WHERE submission_id = ? ORDER BY COALESCE(relative_path, original_filename), id",
                    (submission['id'],)
                )
                submission_files = _serialize_submission_file_rows(files_cursor)
        conn.commit()

    submission_returned = bool(submission and submission_is_returned(submission))
    resubmission_state = submission_resubmission_state(submission) if submission else "none"
    can_resubmit_submission = bool(
        submission
        and submission.get("status") == "submitted"
        and resubmission_state == "open"
    )
    can_withdraw_submission = bool(
        submission
        and submission.get("status") == "submitted"
        and assignment_accepts_submissions(assignment)
        and not submission_returned
    )

    if assignment.get("class_offering_id"):
        try:
            record_behavior_event(
                class_offering_id=int(assignment["class_offering_id"]),
                user_pk=int(user["id"]),
                user_role=str(user["role"]),
                display_name=str(user.get("name") or user.get("username") or user["id"]),
                action_type="page_view",
                session_started_at=str(user.get("login_time") or "").strip() or None,
                summary_text=f"进入考试页面：{assignment.get('title') or assignment_id}",
                payload={
                    "page": "exam_take",
                    "assignment_id": assignment_id,
                    "has_submission": bool(submission),
                },
                page_key="exam_take",
            )
        except Exception as exc:
            print(f"[BEHAVIOR] 记录考试页访问失败: {exc}")

    return templates.TemplateResponse(request, "exam_take.html", {
        "request": request,
        "user_info": user,
        "assignment": assignment,
        "assignment_back_url": assignment_back_url,
        "paper": paper_dict,
        "submission": submission,
        "submission_files": submission_files,
        "exam_ai_allowed": exam_ai_allowed,
        "exam_ai_context": exam_ai_context,
        "can_withdraw_submission": can_withdraw_submission,
        "can_resubmit_submission": can_resubmit_submission,
        "submission_returned": submission_returned,
        "resubmission_state": resubmission_state,
        "resubmission_due_at": submission.get("resubmission_due_at") if submission else None,
        "max_upload_mb": MAX_UPLOAD_SIZE_MB,
        "max_submission_file_count": MAX_SUBMISSION_FILE_COUNT,
        "max_per_file_mb": MAX_SUBMISSION_PER_FILE_MB,
        "max_total_mb": MAX_SUBMISSION_TOTAL_MB,
    })
