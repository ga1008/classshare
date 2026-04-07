from datetime import datetime
import sqlite3
import json

from fastapi import APIRouter, Request, Form, HTTPException, Depends, status, UploadFile, File
from fastapi.responses import RedirectResponse, HTMLResponse
from typing import Optional, List
from pathlib import Path
import pandas as pd

from ..core import templates, COURSE_INFO
# 修复：移除不再需要的 TEACHER_PASS, SHARE_DIR, ROSTER_DIR
from ..config import TEACHER_USER, MAX_UPLOAD_SIZE_MB
from ..dependencies import (
    get_current_user, get_current_user_optional, get_current_teacher,
    create_access_token, get_password_hash, verify_password,
    human_readable_size  # human_readable_size 仍被 classroom_main 使用
)
# 修复：移除，V4.0 roster_handler 不再有 parse_excel_to_students
# from ..services.roster_handler import parse_excel_to_students
from ..database import get_db_connection
from ..dependencies import build_login_url, sanitize_next_path
from ..dependencies import infer_required_role_from_path, get_role_label
from ..dependencies import apply_access_token_cookie, clear_access_token_cookie, invalidate_session_for_user

router = APIRouter()


def _build_login_page_context(request: Request, next_url: Optional[str]) -> dict:
    safe_next = sanitize_next_path(next_url, fallback="/dashboard")
    return {
        "request": request,
        "next_url": safe_next,
        "teacher_entry_url": build_login_url("/teacher/login", safe_next),
        "student_entry_url": build_login_url("/student/login", safe_next),
    }


# ============================
# 1. 根目录和登录/注册
# ============================

@router.get("/", response_class=HTMLResponse)
async def root(request: Request, user: Optional[dict] = Depends(get_current_user_optional)):
    """根目录，根据登录状态重定向到仪表盘或学生登录页"""
    if user:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(url="/student/login", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/student/login", response_class=HTMLResponse)
async def student_login_page(request: Request, next: Optional[str] = None):
    # V4.0 不再需要 class_name 和 course_name
    return templates.TemplateResponse(
        request,
        "student_login_v4.html",
        _build_login_page_context(request, next),
    )


@router.get("/teacher/login", response_class=HTMLResponse)
async def teacher_login_page(request: Request, next: Optional[str] = None):
    return templates.TemplateResponse(
        request,
        "teacher_login_v4.html",
        _build_login_page_context(request, next),
    )


@router.get("/teacher/register", response_class=HTMLResponse)
async def teacher_register_page(request: Request):
    return templates.TemplateResponse(request, "teacher_register_v4.html", {"request": request})


@router.get("/auth/forbidden", response_class=HTMLResponse)
async def permission_warning_page(
    request: Request,
    next: Optional[str] = None,
    required_role: Optional[str] = None,
    user: Optional[dict] = Depends(get_current_user_optional),
):
    safe_next = sanitize_next_path(next, fallback="/dashboard")
    effective_required_role = required_role or infer_required_role_from_path(safe_next.split("?", 1)[0])
    if not user:
        login_path = "/teacher/login" if effective_required_role == "teacher" else "/student/login"
        response = RedirectResponse(
            url=build_login_url(login_path, safe_next),
            status_code=status.HTTP_303_SEE_OTHER,
        )
        clear_access_token_cookie(response)
        return response

    user_hint = user
    current_role = user_hint.get("role")

    return templates.TemplateResponse(request, "permission_denied.html", {
        "request": request,
        "next_url": safe_next,
        "current_user": user_hint,
        "current_role_label": get_role_label(current_role),
        "required_role": effective_required_role,
        "required_role_label": get_role_label(effective_required_role) if effective_required_role else "",
        "teacher_login_url": build_login_url("/teacher/login", safe_next),
        "student_login_url": build_login_url("/student/login", safe_next),
        "dashboard_url": "/dashboard" if user_hint else "/",
        "show_teacher_login": True,
        "permission_message": "当前账号已登录，但没有访问该页面或资源的权限。",
    })


@router.post("/student/login")
async def handle_student_login(
    request: Request,
    name: str = Form(),
    student_id_number: str = Form(),
    next: Optional[str] = Form(default=None),
):
    """V4.0: 学生登录 - 验证数据库"""
    sid_c = student_id_number.strip()
    name_c = name.strip()
    safe_next = sanitize_next_path(next, fallback="/dashboard")

    # 获取客户端IP
    from ..dependencies import get_client_ip
    client_ip = get_client_ip(request)

    with get_db_connection() as conn:
        student = conn.execute(
            "SELECT * FROM students WHERE student_id_number = ? AND name = ?",
            (sid_c, name_c)
        ).fetchone()

    if not student:
        return templates.TemplateResponse(request, "status.html",
                                          {"request": request, "success": False, "message": "登录失败：姓名或学号错误。",
                                           "back_url": build_login_url("/student/login", safe_next)})

    student_data = dict(student)

    # 使该用户之前的会话失效
    invalidate_session_for_user(str(student_data['id']), "student")

    token_data = {
        "id": student_data['id'],  # 使用数据库主键 PK
        "student_id_number": student_data['student_id_number'],
        "name": student_data['name'],
        "role": "student",
        "login_time": datetime.now().isoformat()
    }

    access_token = create_access_token(token_data, client_ip)

    response = RedirectResponse(
        url=safe_next,
        status_code=status.HTTP_303_SEE_OTHER,
    )
    apply_access_token_cookie(response, access_token)
    return response


@router.post("/teacher/register")
async def handle_teacher_register(request: Request, name: str = Form(), email: str = Form(), password: str = Form()):
    """V4.0: 教师注册"""
    hashed_password = get_password_hash(password)
    try:
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO teachers (name, email, hashed_password) VALUES (?, ?, ?)",
                (name.strip(), email.strip(), hashed_password)
            )
            conn.commit()
    except sqlite3.IntegrityError:  # 邮箱已存在
        return templates.TemplateResponse(request, "status.html",
                                          {"request": request, "success": False, "message": "注册失败：该邮箱已被使用。",
                                           "back_url": "/teacher/register"})

    return templates.TemplateResponse(request, "status.html",
                                      {"request": request, "success": True, "message": "注册成功！请登录。",
                                       "back_url": "/teacher/login"})


@router.post("/teacher/login")
async def handle_teacher_login(
    request: Request,
    email: str = Form(),
    password: str = Form(),
    next: Optional[str] = Form(default=None),
):
    """V4.0: 教师登录 - 验证数据库"""
    from ..dependencies import get_client_ip
    client_ip = get_client_ip(request)
    safe_next = sanitize_next_path(next, fallback="/dashboard")

    with get_db_connection() as conn:
        teacher = conn.execute("SELECT * FROM teachers WHERE email = ?", (email,)).fetchone()

    # 修复：使用 verify_password 验证
    if not teacher or not verify_password(password, teacher['hashed_password']):
        return templates.TemplateResponse(request, "status.html",
                                          {"request": request, "success": False, "message": "登录失败：邮箱或密码错误。",
                                           "back_url": build_login_url("/teacher/login", safe_next)})

    teacher_data = dict(teacher)

    # 使该用户之前的会话失效
    invalidate_session_for_user(str(teacher_data['id']), "teacher")

    token_data = {
        "id": teacher_data['id'],  # 数据库主键 PK
        "name": teacher_data['name'],
        "email": teacher_data['email'],
        "role": "teacher",
        "login_time": datetime.now().isoformat()
    }

    access_token = create_access_token(token_data, client_ip)

    response = RedirectResponse(
        url=safe_next,
        status_code=status.HTTP_303_SEE_OTHER,
    )
    apply_access_token_cookie(response, access_token)
    return response


@router.get("/logout")
async def logout(request: Request):
    from ..dependencies import get_current_user_optional

    # 获取当前用户并使其会话失效
    user = await get_current_user_optional(request)
    if user and user.get('id'):
        invalidate_session_for_user(str(user['id']), user.get('role'))
        print(f"[SESSION] 用户 {user.get('name')} 主动注销")

    response = RedirectResponse(url="/student/login", status_code=status.HTTP_303_SEE_OTHER)
    clear_access_token_cookie(response)
    return response


# ============================
# 2. 仪表盘 (V4.0 新)
# ============================

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, user: dict = Depends(get_current_user)):
    """V4.0: 仪表盘，显示用户所有相关的 "班级课堂" """
    offerings = []
    with get_db_connection() as conn:
        if user['role'] == 'teacher':
            cursor = conn.execute(
                """SELECT o.*, c.name as course_name, cl.name as class_name
                   FROM class_offerings o
                            JOIN courses c ON o.course_id = c.id
                            JOIN classes cl ON o.class_id = cl.id
                   WHERE o.teacher_id = ?
                   ORDER BY o.id DESC""",
                (user['id'],)
            )
            offerings = [dict(row) for row in cursor]

        elif user['role'] == 'student':
            cursor = conn.execute(
                """SELECT o.*, c.name as course_name, cl.name as class_name, t.name as teacher_name
                   FROM class_offerings o
                            JOIN courses c ON o.course_id = c.id
                            JOIN classes cl ON o.class_id = cl.id
                            JOIN teachers t ON o.teacher_id = t.id
                   WHERE o.class_id = (SELECT class_id FROM students WHERE id = ?)
                   ORDER BY o.id DESC""",
                (user['id'],)
            )
            offerings = [dict(row) for row in cursor]

    return templates.TemplateResponse(request, "dashboard.html", {
        "request": request,
        "user_info": user,
        "class_offerings": offerings
    })


# ============================
# 3. 课堂主界面 (V4.0 新)
# ============================

@router.get("/classroom/{class_offering_id}", response_class=HTMLResponse)
async def classroom_main(request: Request, class_offering_id: int, user: dict = Depends(get_current_user)):
    """V4.0: 替换旧的 /app，这是特定班级课堂的主界面"""
    with get_db_connection() as conn:
        offering = conn.execute(
            """SELECT o.*,
                      c.name as course_name,
                      c.description as course_description,
                      c.credits as course_credits,
                      cl.name as class_name,
                      cl.description as class_description,
                      t.name as teacher_name,
                      (SELECT COUNT(*) FROM students s WHERE s.class_id = o.class_id) as class_student_count
               FROM class_offerings o
                        JOIN courses c ON o.course_id = c.id
                        JOIN classes cl ON o.class_id = cl.id
                        JOIN teachers t ON o.teacher_id = t.id
               WHERE o.id = ?""",
            (class_offering_id,)
        ).fetchone()

        if not offering: raise HTTPException(404, "未找到此课堂")

        offering_data = dict(offering)
        course_id = offering_data['course_id']

        if user['role'] == 'student':
            student_class = conn.execute("SELECT class_id FROM students WHERE id = ?", (user['id'],)).fetchone()
            if not student_class or student_class['class_id'] != offering_data['class_id']:
                raise HTTPException(403, "您未加入此课堂")
        elif user['role'] == 'teacher':
            if offering_data['teacher_id'] != user['id']:
                raise HTTPException(403, "您不是此课堂的教师")

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

        assignments_cursor = conn.execute("SELECT * FROM assignments WHERE course_id = ? AND (class_offering_id = ? OR class_offering_id IS NULL) ORDER BY created_at DESC",
                                          (course_id, class_offering_id))
        assignments = []
        for row in assignments_cursor:
            assignment = dict(row)
            if user['role'] == 'student':
                if assignment['status'] == 'new': continue
                submission = conn.execute(
                    "SELECT status, score FROM submissions WHERE assignment_id = ? AND student_pk_id = ?",
                    (assignment['id'], user['id'])
                ).fetchone()
                if submission:
                    assignment['submission_status'] = submission['status']
                    assignment['submission_score'] = submission['score']
                else:
                    assignment['submission_status'] = 'unsubmitted'
            assignments.append(assignment)

    return templates.TemplateResponse(request, "classroom_main_v4.html", {
        "request": request,
        "user_info": user,
        "classroom": offering_data,
        "shared_files": files_info,
        "assignments": assignments
    })

# ============================
# 5. 作业详情页 (V4.0)
# ============================

@router.get("/assignment/{assignment_id}", response_class=HTMLResponse)
async def assignment_detail_page(request: Request, assignment_id: str, user: dict = Depends(get_current_user)):
    """V4.0: 作业详情页 (学生/教师均可访问)"""
    with get_db_connection() as conn:
        assignment_row = conn.execute("SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
    if not assignment_row:
        raise HTTPException(404, "Assignment not found")
    assignment = dict(assignment_row)

    # 如果是试卷型作业且用户是学生 → 重定向到考试页面
    if assignment.get('exam_paper_id') and user['role'] == 'student':
        return RedirectResponse(url=f"/exam/take/{assignment_id}")

    if user['role'] == 'teacher':
        return templates.TemplateResponse(request, "assignment_detail_teacher.html", {
            "request": request, "user_info": user, "assignment": assignment
        })
    else:
        if assignment['status'] == 'new':
            return templates.TemplateResponse(request, "status.html",
                                              {"request": request, "success": False, "message": "该作业尚未发布",
                                               "back_url": "/dashboard"})

        with get_db_connection() as conn:
            submission_row = conn.execute(
                "SELECT * FROM submissions WHERE assignment_id = ? AND student_pk_id = ?", (assignment_id, user['id'])
            ).fetchone()
            submission = dict(submission_row) if submission_row else None
            submission_files = []
            if submission:
                files_cursor = conn.execute("SELECT * FROM submission_files WHERE submission_id = ?",
                                            (submission['id'],))
                submission_files = [dict(row) for row in files_cursor]

        return templates.TemplateResponse(request, "assignment_detail_student.html", {
            "request": request, "user_info": user, "assignment": assignment,
            "submission": submission, "submission_files": submission_files,
            "max_upload_mb": MAX_UPLOAD_SIZE_MB
        })


# ============================
# V4.1: 新的管理中心路由
# ============================

@router.get("/manage", response_class=RedirectResponse)
async def redirect_to_manage_classes(user: dict = Depends(get_current_teacher)):
    """管理中心根目录，重定向到班级管理页"""
    return RedirectResponse(url="/manage/classes", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/manage/classes", response_class=HTMLResponse)
async def get_manage_classes_page(request: Request, user: dict = Depends(get_current_teacher)):
    """显示班级管理页面 (列表和新建)"""
    with get_db_connection() as conn:
        my_classes_cursor = conn.execute(
            """
            SELECT c.id, c.name, COUNT(s.id) as student_count
            FROM classes c
            LEFT JOIN students s ON c.id = s.class_id
            WHERE c.created_by_teacher_id = ?
            GROUP BY c.id, c.name
            ORDER BY c.name
            """,
            (user['id'],)
        )
        my_classes = my_classes_cursor.fetchall()

    return templates.TemplateResponse(request, "manage/classes.html", {
        "request": request,
        "user_info": user,
        "my_classes": my_classes,
        "page_title": "班级管理",
        "active_page": "classes"  # 用于侧边栏高亮
    })


@router.get("/manage/courses", response_class=HTMLResponse)
async def get_manage_courses_page(request: Request, user: dict = Depends(get_current_teacher)):
    """显示课程管理页面 (列表和新建)"""
    with get_db_connection() as conn:
        my_courses_cursor = conn.execute(
            "SELECT id, name, credits FROM courses WHERE created_by_teacher_id = ? ORDER BY name", (user['id'],)
        )
        my_courses = my_courses_cursor.fetchall()

    return templates.TemplateResponse(request, "manage/courses.html", {
        "request": request,
        "user_info": user,
        "my_courses": my_courses,
        "page_title": "课程管理",
        "active_page": "courses"
    })


@router.get("/manage/offerings", response_class=HTMLResponse)
async def get_manage_offerings_page(request: Request, user: dict = Depends(get_current_teacher)):
    """显示开设课堂页面 (列表和新建)"""
    conn = get_db_connection()
    try:
        my_classes = conn.execute(
            "SELECT id, name FROM classes WHERE created_by_teacher_id = ?", (user['id'],)
        ).fetchall()
        my_courses = conn.execute(
            "SELECT id, name FROM courses WHERE created_by_teacher_id = ?", (user['id'],)
        ).fetchall()
        my_offerings = conn.execute(
            """
            SELECT o.id, c.name as class_name, co.name as course_name, o.semester
            FROM class_offerings o
            JOIN classes c ON o.class_id = c.id
            JOIN courses co ON o.course_id = co.id
            WHERE o.teacher_id = ?
            ORDER BY co.name, c.name
            """, (user['id'],)
        ).fetchall()
    finally:
        conn.close()

    return templates.TemplateResponse(request, "manage/offerings.html", {
        "request": request,
        "user_info": user,
        "my_classes": my_classes,
        "my_courses": my_courses,
        "my_offerings": my_offerings,
        "page_title": "开设课堂",
        "active_page": "offerings"
    })


@router.get("/manage/ai", response_class=HTMLResponse)
async def get_manage_ai_page(request: Request, user: dict = Depends(get_current_teacher)):
    """显示课堂AI配置页面"""
    with get_db_connection() as conn:
        # 优化：查询时额外获取课程的 description 和 credits，用于前端预填充
        my_offerings = conn.execute(
            """
            SELECT o.id, c.name as class_name, 
                   co.name as course_name, co.description, co.credits
            FROM class_offerings o
            JOIN classes c ON o.class_id = c.id
            JOIN courses co ON o.course_id = co.id
            WHERE o.teacher_id = ?
            ORDER BY co.name, c.name
            """, (user['id'],)
        ).fetchall()

    return templates.TemplateResponse(request, "manage/ai.html", {
        "request": request,
        "user_info": user,
        "my_offerings": my_offerings,
        "page_title": "课堂AI配置",
        "active_page": "ai"
    })


# ============================
# V4.5: 试卷库管理路由
# ============================

@router.get("/manage/exams", response_class=HTMLResponse)
async def manage_exams_page(request: Request, user: dict = Depends(get_current_teacher)):
    """试卷库管理页面"""
    with get_db_connection() as conn:
        # 自动将已完成的AI生成试卷从 generating 转为 draft
        conn.execute(
            """UPDATE exam_papers SET status = 'draft', ai_gen_status = NULL, updated_at = ?
               WHERE teacher_id = ? AND status = 'generating' AND ai_gen_status = 'completed'""",
            (datetime.now().isoformat(), user['id'])
        )
        conn.commit()

        papers_cursor = conn.execute(
            """SELECT ep.*,
                      (SELECT COUNT(*) FROM assignments WHERE exam_paper_id = ep.id) as assigned_count
               FROM exam_papers ep
               WHERE ep.teacher_id = ?
               ORDER BY ep.updated_at DESC""",
            (user['id'],)
        )
        papers = []
        for row in papers_cursor:
            paper = dict(row)
            # 解析 questions_json
            if paper.get('questions_json'):
                try:
                    paper['questions_json'] = json.loads(paper['questions_json'])
                except (json.JSONDecodeError, TypeError):
                    paper['questions_json'] = None
            papers.append(paper)

    return templates.TemplateResponse(request, "manage/exams.html", {
        "request": request,
        "user_info": user,
        "papers": papers,
        "page_title": "试卷库",
        "active_page": "exams"
    })


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


@router.get("/submission/{submission_id}", response_class=HTMLResponse)
async def submission_detail_page(request: Request, submission_id: int, user: dict = Depends(get_current_user)):
    """查看/批改提交详情页（教师+学生均可访问）"""
    with get_db_connection() as conn:
        submission = conn.execute("SELECT * FROM submissions WHERE id = ?", (submission_id,)).fetchone()
        if not submission:
            raise HTTPException(404, "提交记录不存在")
        submission = dict(submission)

        assignment = conn.execute("SELECT * FROM assignments WHERE id = ?", (submission['assignment_id'],)).fetchone()
        if not assignment:
            raise HTTPException(404, "作业不存在")
        assignment = dict(assignment)

        # 获取提交的附件
        files_cursor = conn.execute("SELECT * FROM submission_files WHERE submission_id = ?", (submission_id,))
        submission_files = [dict(row) for row in files_cursor]

        # 如果是试卷型作业，获取题目信息
        exam_questions = None
        if assignment.get('exam_paper_id'):
            paper = conn.execute("SELECT questions_json FROM exam_papers WHERE id = ?",
                                 (assignment['exam_paper_id'],)).fetchone()
            if paper:
                exam_questions = json.loads(paper['questions_json'])

    return templates.TemplateResponse(request, "submission_detail.html", {
        "request": request,
        "user_info": user,
        "assignment": assignment,
        "submission": submission,
        "submission_files": submission_files,
        "exam_questions": exam_questions,
    })


@router.get("/exam/take/{assignment_id}", response_class=HTMLResponse)
async def exam_take_page(request: Request, assignment_id: str, user: dict = Depends(get_current_user)):
    """学生考试界面"""
    with get_db_connection() as conn:
        assignment = conn.execute("SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
        if not assignment:
            raise HTTPException(404, "作业不存在")
        assignment = dict(assignment)

        if not assignment.get('exam_paper_id'):
            # 不是试卷型作业，跳转到普通作业页
            return RedirectResponse(url=f"/assignment/{assignment_id}")

        if user['role'] == 'student' and assignment['status'] == 'new':
            return templates.TemplateResponse(request, "status.html",
                {"request": request, "success": False, "message": "该考试尚未发布", "back_url": "/dashboard"})

        paper = conn.execute("SELECT * FROM exam_papers WHERE id = ?", (assignment['exam_paper_id'],)).fetchone()
        if not paper:
            raise HTTPException(404, "试卷不存在")

        # 检查学生是否已提交
        submission = None
        if user['role'] == 'student':
            submission_row = conn.execute(
                "SELECT * FROM submissions WHERE assignment_id = ? AND student_pk_id = ?",
                (assignment_id, user['id'])
            ).fetchone()
            submission = dict(submission_row) if submission_row else None

    return templates.TemplateResponse(request, "exam_take.html", {
        "request": request,
        "user_info": user,
        "assignment": assignment,
        "paper": dict(paper),
        "submission": submission,
        "max_upload_mb": MAX_UPLOAD_SIZE_MB
    })

