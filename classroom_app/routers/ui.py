from datetime import datetime
import sqlite3

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

router = APIRouter()


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
async def student_login_page(request: Request):
    # V4.0 不再需要 class_name 和 course_name
    return templates.TemplateResponse("student_login_v4.html", {"request": request})


@router.get("/teacher/login", response_class=HTMLResponse)
async def teacher_login_page(request: Request):
    return templates.TemplateResponse("teacher_login_v4.html", {"request": request})


@router.get("/teacher/register", response_class=HTMLResponse)
async def teacher_register_page(request: Request):
    return templates.TemplateResponse("teacher_register_v4.html", {"request": request})


@router.post("/student/login")
async def handle_student_login(request: Request, name: str = Form(), student_id_number: str = Form()):
    """V4.0: 学生登录 - 验证数据库"""
    sid_c = student_id_number.strip()
    name_c = name.strip()

    # 获取客户端IP
    from ..dependencies import get_client_ip, invalidate_user_session
    client_ip = get_client_ip(request)

    with get_db_connection() as conn:
        student = conn.execute(
            "SELECT * FROM students WHERE student_id_number = ? AND name = ?",
            (sid_c, name_c)
        ).fetchone()

    if not student:
        return templates.TemplateResponse("status.html",
                                          {"request": request, "success": False, "message": "登录失败：姓名或学号错误。",
                                           "back_url": "/student/login"})

    student_data = dict(student)

    # 使该用户之前的会话失效
    invalidate_user_session(str(student_data['id']))

    token_data = {
        "id": student_data['id'],  # 使用数据库主键 PK
        "student_id_number": student_data['student_id_number'],
        "name": student_data['name'],
        "role": "student",
        "login_time": datetime.now().isoformat()
    }

    from ..dependencies import create_access_token
    access_token = create_access_token(token_data, client_ip)

    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(key="access_token", value=access_token, httponly=True)
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
        return templates.TemplateResponse("status.html",
                                          {"request": request, "success": False, "message": "注册失败：该邮箱已被使用。",
                                           "back_url": "/teacher/register"})

    return templates.TemplateResponse("status.html",
                                      {"request": request, "success": True, "message": "注册成功！请登录。",
                                       "back_url": "/teacher/login"})


@router.post("/teacher/login")
async def handle_teacher_login(request: Request, email: str = Form(), password: str = Form()):
    """V4.0: 教师登录 - 验证数据库"""
    from ..dependencies import get_client_ip, invalidate_user_session
    client_ip = get_client_ip(request)

    with get_db_connection() as conn:
        teacher = conn.execute("SELECT * FROM teachers WHERE email = ?", (email,)).fetchone()

    # 修复：使用 verify_password 验证
    if not teacher or not verify_password(password, teacher['hashed_password']):
        return templates.TemplateResponse("status.html",
                                          {"request": request, "success": False, "message": "登录失败：邮箱或密码错误。",
                                           "back_url": "/teacher/login"})

    teacher_data = dict(teacher)

    # 使该用户之前的会话失效
    invalidate_user_session(str(teacher_data['id']))

    token_data = {
        "id": teacher_data['id'],  # 数据库主键 PK
        "name": teacher_data['name'],
        "email": teacher_data['email'],
        "role": "teacher",
        "login_time": datetime.now().isoformat()
    }

    from ..dependencies import create_access_token
    access_token = create_access_token(token_data, client_ip)

    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(key="access_token", value=access_token, httponly=True)
    return response


@router.get("/logout")
async def logout(request: Request):
    from ..dependencies import get_current_user_optional, invalidate_user_session

    # 获取当前用户并使其会话失效
    user = await get_current_user_optional(request)
    if user and user.get('id'):
        invalidate_user_session(str(user['id']))
        print(f"[SESSION] 用户 {user.get('name')} 主动注销")

    response = RedirectResponse(url="/student/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie("access_token")
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

    return templates.TemplateResponse("dashboard.html", {
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
            """SELECT o.*, c.name as course_name, cl.name as class_name, t.name as teacher_name
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

        assignments_cursor = conn.execute("SELECT * FROM assignments WHERE course_id = ? ORDER BY created_at DESC",
                                          (course_id,))
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

    return templates.TemplateResponse("classroom_main_v4.html", {
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

    # 模板名称已在 V3.1 中更正
    if user['role'] == 'teacher':
        return templates.TemplateResponse("assignment_detail_teacher.html", {
            "request": request, "user_info": user, "assignment": assignment
        })
    else:
        if assignment['status'] == 'new':
            return templates.TemplateResponse("status.html",
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

        return templates.TemplateResponse("assignment_detail_student.html", {
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

    return templates.TemplateResponse("manage/classes.html", {
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

    return templates.TemplateResponse("manage/courses.html", {
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

    return templates.TemplateResponse("manage/offerings.html", {
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

    return templates.TemplateResponse("manage/ai.html", {
        "request": request,
        "user_info": user,
        "my_offerings": my_offerings,
        "page_title": "课堂AI配置",
        "active_page": "ai"
    })

