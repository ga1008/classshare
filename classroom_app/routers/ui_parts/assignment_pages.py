import re
from urllib.parse import urlsplit

from .common import *


router = APIRouter()


def _submission_return_url(request: Request, fallback: str) -> str:
    raw_return = request.query_params.get("return_to")
    safe_base = sanitize_next_path(raw_return, fallback=fallback)
    if not raw_return or safe_base == fallback:
        return safe_base
    try:
        fragment = urlsplit(str(raw_return).strip()).fragment
    except Exception:
        fragment = ""
    if fragment and re.fullmatch(r"[A-Za-z0-9_.:\-]+", fragment):
        return f"{safe_base}#{fragment}"
    return safe_base


@router.get("/assignment/{assignment_id}", response_class=HTMLResponse)
def assignment_detail_page(request: Request, assignment_id: str, user: dict = Depends(get_current_user)):
    """V4.0: 作业详情页 (学生/教师均可访问)"""
    with get_db_connection() as conn:
        close_overdue_assignments(conn)
        assignment_row = conn.execute("SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
        if not assignment_row:
            raise HTTPException(404, "Assignment not found")
        assignment_row = refresh_assignment_runtime_status(conn, assignment_row)
        assignment = _enrich_assignment_upload_config(dict(assignment_row))
        assignment_back_url = _assignment_back_url(assignment)
        if user["role"] == "student" and not student_can_access_assignment(conn, assignment_id, int(user["id"])):
            raise HTTPException(403, "该破境试炼只对指定学生开放")
        if user["role"] == "teacher" and is_personal_stage_exam_assignment(conn, assignment_id):
            return templates.TemplateResponse(
                request,
                "status.html",
                {
                    "request": request,
                    "success": False,
                    "message": "学生个人试炼不进入教师作业与考试明细，请在班级修行统计中查看汇总情况。",
                    "back_url": assignment_back_url,
                },
                status_code=404,
            )

        # 如果是试卷型作业且用户是学生 → 重定向到考试页面
        if assignment.get('exam_paper_id') and user['role'] == 'student':
            return RedirectResponse(url=f"/exam/take/{assignment_id}")

        if user['role'] == 'teacher':
            access_row = conn.execute(
                """
                SELECT a.*,
                       c.created_by_teacher_id,
                       o.teacher_id AS offering_teacher_id
                FROM assignments a
                JOIN courses c ON c.id = a.course_id
                LEFT JOIN class_offerings o ON o.id = a.class_offering_id
                WHERE a.id = ?
                LIMIT 1
                """,
                (assignment_id,),
            ).fetchone()
            if not access_row or not teacher_can_manage_assignment(conn, int(user["id"]), access_row):
                raise HTTPException(403, "无权查看该作业")
            _expire_stale_ai_grading_for_assignments(conn, [assignment_id])
            exam_questions = None
            exam_paper_preview = None
            if assignment.get("exam_paper_id"):
                paper_row = conn.execute(
                    "SELECT title, description, questions_json FROM exam_papers WHERE id = ?",
                    (assignment["exam_paper_id"],),
                ).fetchone()
                if paper_row:
                    exam_paper_preview = {
                        "title": paper_row["title"],
                        "description": paper_row["description"] or "",
                    }
                    try:
                        exam_questions = json.loads(paper_row["questions_json"] or "{}")
                    except json.JSONDecodeError:
                        exam_questions = {"pages": []}
            if assignment.get("class_offering_id"):
                try:
                    record_behavior_event(
                        class_offering_id=int(assignment["class_offering_id"]),
                        user_pk=int(user["id"]),
                        user_role=str(user["role"]),
                        display_name=str(user.get("name") or user.get("username") or user["id"]),
                        action_type="page_view",
                        session_started_at=str(user.get("login_time") or "").strip() or None,
                        summary_text=f"查看作业详情：{assignment.get('title') or assignment_id}",
                        payload={"page": "assignment_detail", "assignment_id": assignment_id},
                        page_key="assignment_detail",
                    )
                except Exception as exc:
                    print(f"[BEHAVIOR] 记录教师作业页访问失败: {exc}")
            return templates.TemplateResponse(request, "assignment_detail_teacher.html", {
                "request": request,
                "user_info": user,
                "assignment": assignment,
                "assignment_back_url": assignment_back_url,
                "exam_questions": exam_questions,
                "exam_paper_preview": exam_paper_preview,
                "learning_stage_options": get_learning_stage_options(),
                "max_upload_mb": MAX_UPLOAD_SIZE_MB,
                "max_submission_file_count": MAX_SUBMISSION_FILE_COUNT,
                "max_per_file_mb": MAX_SUBMISSION_PER_FILE_MB,
                "max_total_mb": MAX_SUBMISSION_TOTAL_MB,
            })

        if assignment['status'] == 'new':
            return templates.TemplateResponse(
                request,
                "status.html",
                {
                    "request": request,
                    "success": False,
                    "message": "该作业尚未发布",
                    "back_url": assignment_back_url,
                },
            )

        submission_row = conn.execute(
            "SELECT * FROM submissions WHERE assignment_id = ? AND student_pk_id = ?",
            (assignment_id, user['id'])
        ).fetchone()
        submission = dict(submission_row) if submission_row else None
        if submission and int(submission.get("is_absence_score") or 0):
            submission = None
        submission_files = []
        if submission:
            files_cursor = conn.execute(
                "SELECT * FROM submission_files WHERE submission_id = ? ORDER BY COALESCE(relative_path, original_filename), id",
                (submission['id'],)
            )
            submission_files = _serialize_submission_file_rows(files_cursor)

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
                summary_text=f"查看作业详情：{assignment.get('title') or assignment_id}",
                payload={
                    "page": "assignment_detail",
                    "assignment_id": assignment_id,
                    "has_submission": bool(submission),
                },
                page_key="assignment_detail",
            )
        except Exception as exc:
            print(f"[BEHAVIOR] 记录学生作业页访问失败: {exc}")

    return templates.TemplateResponse(request, "assignment_detail_student.html", {
        "request": request, "user_info": user, "assignment": assignment,
        "assignment_back_url": assignment_back_url,
        "submission": submission, "submission_files": submission_files,
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


@router.get("/assignment/{assignment_id}/wrong-summary", response_class=HTMLResponse)
async def assignment_wrong_summary_page(
    request: Request,
    assignment_id: str,
    user: dict = Depends(get_current_teacher),
):
    summary = await build_assignment_wrong_question_summary(
        assignment_id,
        int(user["id"]),
        ai_mode="cached",
        schedule_ai=False,
    )
    assignment = summary.get("assignment") or {"id": assignment_id, "title": "错题归集"}
    return templates.TemplateResponse(
        request,
        "assignment_wrong_summary.html",
        {
            "request": request,
            "user_info": user,
            "summary": summary,
            "assignment": assignment,
            "paper": summary.get("paper"),
            "assignment_back_url": f"/assignment/{assignment_id}",
            "classroom_back_url": _assignment_back_url(assignment),
        },
    )


@router.get("/api/assignments/{assignment_id}/wrong-summary/status", response_class=JSONResponse)
async def assignment_wrong_summary_status(
    assignment_id: str,
    user: dict = Depends(get_current_teacher),
):
    summary = await build_assignment_wrong_question_summary(
        assignment_id,
        int(user["id"]),
        ai_mode="cached",
        schedule_ai=False,
    )
    return {
        "status": "success",
        "assignment_id": str(assignment_id),
        "ai_status": summary.get("ai_status") or {},
        "stats": summary.get("stats") or {},
    }


@router.post("/api/assignments/{assignment_id}/wrong-summary/reorganize", response_class=JSONResponse)
async def assignment_wrong_summary_reorganize(
    assignment_id: str,
    user: dict = Depends(get_current_teacher),
):
    summary = await reorganize_assignment_wrong_summary_ai(
        assignment_id,
        int(user["id"]),
    )
    return {
        "status": "success",
        "assignment_id": str(assignment_id),
        "ai_status": summary.get("ai_status") or {},
        "stats": summary.get("stats") or {},
        "reset_result": summary.get("reset_result") or {},
    }


@router.get("/submission/{submission_id}", response_class=HTMLResponse)
async def submission_detail_page(request: Request, submission_id: int, user: dict = Depends(get_current_user)):
    """查看/批改提交详情页（教师+学生均可访问）"""
    with get_db_connection() as conn:
        close_overdue_assignments(conn)
        submission = ensure_submission_access(conn, submission_id, user)
        if submission is None:
            raise HTTPException(404, "提交记录不存在")
        submission = dict(submission)

        assignment = conn.execute("SELECT * FROM assignments WHERE id = ?", (submission['assignment_id'],)).fetchone()
        if not assignment:
            raise HTTPException(404, "作业不存在")
        assignment = refresh_assignment_runtime_status(conn, assignment)
        assignment = _enrich_assignment_upload_config(dict(assignment))
        submission_back_url = _submission_return_url(request, f"/assignment/{assignment['id']}")
        submission_back_label = "返回错题归集" if "/wrong-summary" in submission_back_url else "返回作业"

        # 获取提交的附件
        files_cursor = conn.execute(
            "SELECT * FROM submission_files WHERE submission_id = ? ORDER BY COALESCE(relative_path, original_filename), id",
            (submission_id,)
        )
        submission_files = _serialize_submission_file_rows(files_cursor)

        # 如果是试卷型作业，获取题目信息
        exam_questions = None
        if assignment.get('exam_paper_id'):
            paper = conn.execute("SELECT questions_json FROM exam_papers WHERE id = ?",
                                 (assignment['exam_paper_id'],)).fetchone()
            if paper:
                exam_questions = json.loads(paper['questions_json'])
        conn.commit()

    can_manage_submission_files = bool(
        user.get("role") == "teacher"
        and submission.get("status") != "grading"
        and (
            submission.get("status") != "graded"
            or int(submission.get("resubmission_allowed") or 0)
        )
    )
    attachment_locked_reason = ""
    if user.get("role") == "teacher" and submission.get("status") == "grading":
        attachment_locked_reason = "AI 正在批改中，附件暂不可修改。"
    elif (
        user.get("role") == "teacher"
        and submission.get("status") == "graded"
        and not int(submission.get("resubmission_allowed") or 0)
    ):
        attachment_locked_reason = "已批改成功的提交需要先撤回，才能修改附件。"

    return templates.TemplateResponse(request, "submission_detail.html", {
        "request": request,
        "user_info": user,
        "assignment": assignment,
        "submission": submission,
        "submission_files": submission_files,
        "exam_questions": exam_questions,
        "can_manage_submission_files": can_manage_submission_files,
        "attachment_locked_reason": attachment_locked_reason,
        "ai_grading_upload_extensions": AI_GRADING_UPLOAD_EXTENSIONS,
        "ai_grading_supported_types_label": AI_GRADING_SUPPORTED_TYPES_LABEL,
        "submission_back_url": submission_back_url,
        "submission_back_label": submission_back_label,
        "max_upload_mb": MAX_UPLOAD_SIZE_MB,
        "max_submission_file_count": MAX_SUBMISSION_FILE_COUNT,
        "max_per_file_mb": MAX_SUBMISSION_PER_FILE_MB,
        "max_total_mb": MAX_SUBMISSION_TOTAL_MB,
    })
