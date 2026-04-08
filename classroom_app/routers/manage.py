import sqlite3
import traceback
import uuid
from datetime import datetime

import aiofiles
from fastapi import APIRouter, Request, Form, HTTPException, Depends, UploadFile, File
from fastapi.responses import JSONResponse

from ..config import ROSTER_DIR, SHARE_DIR
from ..database import get_db_connection
from ..dependencies import get_current_teacher, invalidate_session_for_user
from ..services.file_handler import save_upload_file
from ..services.roster_handler import parse_excel_to_students
from ..services.student_auth_service import build_student_security_summary, list_student_login_history

router = APIRouter(prefix="/api/manage", dependencies=[Depends(get_current_teacher)])


# --- 班级管理 ---
@router.post("/classes/create", response_class=JSONResponse)
async def api_create_class(request: Request, class_name: str = Form(), file: UploadFile = File(...),
                           user: dict = Depends(get_current_teacher)):
    """从Excel文件创建班级和学生"""

    # 1. 保存 Excel 文件
    temp_excel_path = ROSTER_DIR / f"temp_{uuid.uuid4()}_{file.filename}"
    try:
        async with aiofiles.open(temp_excel_path, 'wb') as out_file:
            while content := await file.read(1024 * 1024): await out_file.write(content)
    except Exception as e:
        raise HTTPException(500, f"保存文件失败: {e}")

    # 2. 解析 Excel
    students_data = parse_excel_to_students(temp_excel_path)
    if students_data is None:
        temp_excel_path.unlink()  # 清理临时文件
        raise HTTPException(400, "解析Excel失败，请检查文件格式和列名（需包含'姓名'和'学号'）。")

    # 3. 存入数据库 (使用事务)
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # 创建班级
        cursor.execute("INSERT INTO classes (name, created_by_teacher_id) VALUES (?, ?)", (class_name, user['id']))
        class_id = cursor.lastrowid

        # 批量插入学生
        students_to_insert = [
            (s['student_id_number'], s['name'], class_id, s.get('gender'), s.get('email'), s.get('phone'))
            for s in students_data
        ]
        cursor.executemany(
            "INSERT INTO students (student_id_number, name, class_id, gender, email, phone) VALUES (?, ?, ?, ?, ?, ?)",
            students_to_insert
        )
        conn.commit()

    except sqlite3.IntegrityError as e:
        # 显示更详细的错误信息并打印堆栈跟踪
        traceback.print_exc()
        conn.rollback()
        raise HTTPException(400, f"创建失败：{e}。可能是班级名称或学号已存在。")
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"数据库操作失败: {e}")
    finally:
        conn.close()
        temp_excel_path.unlink()  # 清理临时文件

    return {"status": "success", "message": f"成功创建班级 '{class_name}' 并导入 {len(students_data)} 名学生。"}


# (新增) 删除班级
@router.delete("/classes/{class_id}", response_class=JSONResponse)
async def api_delete_class(class_id: int, user: dict = Depends(get_current_teacher)):
    """删除一个班级 (及其所有学生和课堂关联)"""
    try:
        with get_db_connection() as conn:
            # 权限检查
            cursor = conn.execute(
                "SELECT id FROM classes WHERE id = ? AND created_by_teacher_id = ?",
                (class_id, user['id'])
            )
            if not cursor.fetchone():
                raise HTTPException(403, "无权删除该班级或班级不存在")

            # 删除 (依赖于 database.py 中设置的 PRAGMA foreign_keys = ON 和 ON DELETE CASCADE)
            # 1. 删除 students (通过外键)
            # 2. 删除 class_offerings (通过外键)
            # 3. 删除 class
            conn.execute("DELETE FROM classes WHERE id = ?", (class_id,))
            conn.commit()

    except sqlite3.IntegrityError as e:
        raise HTTPException(400, f"删除失败: {e}")
    except Exception as e:
        raise HTTPException(500, f"服务器错误: {e}")

    return {"status": "success", "message": "班级删除成功。"}


# --- 课程管理 ---
@router.post("/courses/create", response_class=JSONResponse)
async def api_create_course(
        request: Request,
        name: str = Form(...),  # 改为必填
        description: str = Form(default=""),  # 明确指定默认值
        credits: float = Form(default=0.0),  # 明确指定默认值
        user: dict = Depends(get_current_teacher)
):
    try:
        # 添加参数验证
        if not name or len(name.strip()) == 0:
            raise HTTPException(400, "课程名称不能为空")

        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO courses (name, description, credits, created_by_teacher_id) VALUES (?, ?, ?, ?)",
                (name.strip(), description, credits, user['id'])
            )
            conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(400, "创建课程失败，可能名称已存在。")
    except Exception as e:
        print(f"创建课程错误: {str(e)}")  # 添加错误日志
        raise HTTPException(500, f"创建课程失败: {str(e)}")

    return {"status": "success", "message": f"课程 '{name}' 创建成功。"}


# (新增) 删除课程
@router.delete("/courses/{course_id}", response_class=JSONResponse)
async def api_delete_course(course_id: int, user: dict = Depends(get_current_teacher)):
    """删除一个课程 (及其所有文件和课堂关联)"""
    try:
        with get_db_connection() as conn:
            # 权限检查
            cursor = conn.execute(
                "SELECT id FROM courses WHERE id = ? AND created_by_teacher_id = ?",
                (course_id, user['id'])
            )
            if not cursor.fetchone():
                raise HTTPException(403, "无权删除该课程或课程不存在")

            # 删除 (依赖于 ON DELETE CASCADE)
            # 1. 删除 course_files (通过外键)
            # 2. 删除 class_offerings (通过外键)
            # 3. 删除 assignments (通过外键)
            # 4. 删除 course
            conn.execute("DELETE FROM courses WHERE id = ?", (course_id,))

            # TODO: 还应删除服务器上的相关文件 (例如 SHARE_DIR / str(course_id) 目录)

            conn.commit()

    except sqlite3.IntegrityError as e:
        raise HTTPException(400, f"删除失败: {e}")
    except Exception as e:
        raise HTTPException(500, f"服务器错误: {e}")

    return {"status": "success", "message": "课程删除成功。"}


@router.post("/courses/{course_id}/files/upload", response_class=JSONResponse)
async def api_upload_course_file(
        course_id: int,
        file: UploadFile = File(...),
        is_public: bool = Form(True),
        is_teacher_resource: bool = Form(False),
        user: dict = Depends(get_current_teacher)
):
    """上传课程资源文件"""
    # 检查教师是否拥有此课程
    with get_db_connection() as conn:
        course = conn.execute("SELECT id FROM courses WHERE id = ? AND created_by_teacher_id = ?",
                              (course_id, user['id'])).fetchone()
    if not course:
        raise HTTPException(403, "无权操作此课程")

    upload_dir = SHARE_DIR / str(course_id)
    file_info = await save_upload_file(upload_dir, file)
    if not file_info:
        raise HTTPException(500, "保存文件到服务器失败")

    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO course_files (course_id, file_name, stored_path, is_public, is_teacher_resource) VALUES (?, ?, ?, ?, ?)",
            (course_id, file_info['original_filename'], file_info['stored_path'], is_public, is_teacher_resource)
        )
        conn.commit()

    return {"status": "success", "message": f"文件 '{file_info['original_filename']}' 上传成功。"}


# --- 班级课堂 (关联) ---
@router.post("/class_offerings/create", response_class=JSONResponse)
async def api_create_class_offering(
        request: Request,
        class_id: int = Form(...),
        course_id: int = Form(...),
        semester: str = Form(""),
        user: dict = Depends(get_current_teacher)
):
    try:
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO class_offerings (class_id, course_id, teacher_id, semester) VALUES (?, ?, ?, ?)",
                (class_id, course_id, user['id'], semester)
            )
            conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(400, "创建失败，该班级课堂可能已存在。")
    except Exception as e:
        raise HTTPException(500, f"数据库错误: {e}")

    return {"status": "success", "message": "班级课堂关联成功！"}


# (新增) 删除课堂
@router.delete("/class_offerings/{offering_id}", response_class=JSONResponse)
async def api_delete_class_offering(offering_id: int, user: dict = Depends(get_current_teacher)):
    """删除一个课堂 (及其AI配置和聊天记录)"""
    try:
        with get_db_connection() as conn:
            # 权限检查
            cursor = conn.execute(
                "SELECT id FROM class_offerings WHERE id = ? AND teacher_id = ?",
                (offering_id, user['id'])
            )
            if not cursor.fetchone():
                raise HTTPException(403, "无权删除该课堂或课堂不存在")

            # 删除 (依赖于 ON DELETE CASCADE)
            # 1. 删除 chat_logs (通过外键)
            # 2. 删除 ai_class_configs (通过外键)
            # 3. 删除 class_offering
            conn.execute("DELETE FROM class_offerings WHERE id = ?", (offering_id,))
            conn.commit()

    except sqlite3.IntegrityError as e:
        raise HTTPException(400, f"删除失败: {e}")
    except Exception as e:
        raise HTTPException(500, f"服务器错误: {e}")

    return {"status": "success", "message": "课堂关联删除成功。"}


# --- 课堂 AI 配置 ---
@router.post("/ai/configure", response_class=JSONResponse)
async def api_configure_ai_offering(
        request: Request,
        class_offering_id: int = Form(...),
        system_prompt: str = Form(""),
        syllabus: str = Form(""),
        user: dict = Depends(get_current_teacher)
):
    """
    创建或更新一个特定课堂的 AI 配置
    """
    conn = get_db_connection()
    try:
        # 安全检查：确保该教师有权配置这个课堂
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM class_offerings WHERE id = ? AND teacher_id = ?",
            (class_offering_id, user['id'])
        )
        offering = cursor.fetchone()

        if not offering:
            raise HTTPException(status_code=403, detail="无权配置该课堂或课堂不存在")

        # 使用 UPSERT (Update or Insert) 逻辑
        # 如果 class_offering_id 已存在, 则更新；否则, 插入新行
        cursor.execute(
            """
            INSERT INTO ai_class_configs (class_offering_id, system_prompt, syllabus)
            VALUES (?, ?, ?) ON CONFLICT(class_offering_id) DO
            UPDATE SET
                system_prompt = excluded.system_prompt,
                syllabus = excluded.syllabus,
                updated_at = CURRENT_TIMESTAMP
            """,
            (class_offering_id, system_prompt, syllabus)
        )

        conn.commit()

    except sqlite3.IntegrityError as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=f"配置保存失败: {e}")
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"服务器内部错误: {e}")
    finally:
        conn.close()

    return {"status": "success", "message": "AI配置保存成功！"}


# (新增) 获取 AI 配置 (用于前端加载)
@router.get("/ai/config/{class_offering_id}", response_class=JSONResponse)
async def api_get_ai_config(class_offering_id: int, user: dict = Depends(get_current_teacher)):
    """获取一个特定课堂的 AI 配置"""
    conn = get_db_connection()
    try:
        # 权限检查
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT ac.system_prompt, ac.syllabus
            FROM ai_class_configs ac
                     JOIN class_offerings co ON ac.class_offering_id = co.id
            WHERE ac.class_offering_id = ?
              AND co.teacher_id = ?
            """,
            (class_offering_id, user['id'])
        )
        config = cursor.fetchone()

        if config:
            return dict(config)
        else:
            # 即使没有配置，也要检查教师是否有权访问这个课堂
            cursor.execute(
                "SELECT id FROM class_offerings WHERE id = ? AND teacher_id = ?",
                (class_offering_id, user['id'])
            )
            if not cursor.fetchone():
                raise HTTPException(status_code=403, detail="无权访问该课堂")

            # 课堂存在，但没有 AI 配置
            return {"system_prompt": "", "syllabus": ""}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"服务器内部错误: {e}")
    finally:
        conn.close()


# --- 课堂列表 API (试卷分配用) ---
@router.get("/offerings/list", response_class=JSONResponse)
async def api_list_offerings(user: dict = Depends(get_current_teacher)):
    """获取当前教师的课堂列表（用于试卷分配）"""
    try:
        conn = get_db_connection()
        cursor = conn.execute(
            """SELECT o.id, o.semester,
                      c.name as class_name,
                      co.name as course_name
               FROM class_offerings o
               JOIN classes c ON o.class_id = c.id
               JOIN courses co ON o.course_id = co.id
               WHERE o.teacher_id = ?
               ORDER BY co.name, c.name""",
            (user['id'],)
        )
        offerings = [dict(row) for row in cursor]
        conn.close()
        return {"status": "success", "offerings": offerings}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/system/password-resets/{request_id}", response_class=JSONResponse)
async def api_get_password_reset_request_detail(
    request_id: int,
    user: dict = Depends(get_current_teacher),
):
    """查看单个找回密码申请详情及学生历史登录信息。"""
    with get_db_connection() as conn:
        request_row = conn.execute(
            """
            SELECT r.*,
                   s.name AS student_name,
                   s.student_id_number,
                   s.password_reset_required,
                   s.password_updated_at,
                   CASE WHEN s.hashed_password IS NULL OR s.hashed_password = '' THEN 0 ELSE 1 END AS has_password,
                   c.name AS current_class_name,
                   reviewer.name AS reviewer_name
            FROM student_password_reset_requests r
            JOIN students s ON s.id = r.student_id
            JOIN classes c ON c.id = r.class_id
            LEFT JOIN teachers reviewer ON reviewer.id = r.reviewed_by_teacher_id
            WHERE r.id = ? AND r.teacher_id = ?
            """,
            (request_id, user["id"]),
        ).fetchone()

        if not request_row:
            raise HTTPException(status_code=404, detail="找回密码申请不存在。")

        login_history = list_student_login_history(conn, request_row["student_id"], limit=20)
        security_summary = build_student_security_summary(conn, request_row["student_id"])

    return {
        "status": "success",
        "request": dict(request_row),
        "login_history": login_history,
        "security_summary": security_summary,
    }


@router.post("/system/password-resets/{request_id}/approve", response_class=JSONResponse)
async def api_approve_password_reset_request(
    request_id: int,
    review_note: str = Form(default=""),
    user: dict = Depends(get_current_teacher),
):
    """教师通过学生找回密码申请。"""
    with get_db_connection() as conn:
        request_row = conn.execute(
            """
            SELECT id, student_id, teacher_id, status
            FROM student_password_reset_requests
            WHERE id = ? AND teacher_id = ?
            """,
            (request_id, user["id"]),
        ).fetchone()
        if not request_row:
            raise HTTPException(status_code=404, detail="找回密码申请不存在。")
        if request_row["status"] != "pending":
            raise HTTPException(status_code=400, detail="该申请当前不能再执行通过操作。")

        reviewed_at = datetime.now().isoformat()
        conn.execute(
            """
            UPDATE student_password_reset_requests
            SET status = 'approved', reviewed_at = ?, reviewed_by_teacher_id = ?, review_note = ?
            WHERE id = ?
            """,
            (reviewed_at, user["id"], review_note.strip(), request_id),
        )
        conn.execute(
            """
            UPDATE students
            SET password_reset_required = 1
            WHERE id = ?
            """,
            (request_row["student_id"],),
        )
        conn.commit()

    invalidate_session_for_user(str(request_row["student_id"]), "student")
    return {
        "status": "success",
        "message": "已通过该申请，学生可重新使用姓名和学号登录并设置新密码。",
    }


@router.post("/system/password-resets/{request_id}/reject", response_class=JSONResponse)
async def api_reject_password_reset_request(
    request_id: int,
    review_note: str = Form(default=""),
    user: dict = Depends(get_current_teacher),
):
    """教师拒绝学生找回密码申请。"""
    with get_db_connection() as conn:
        request_row = conn.execute(
            """
            SELECT id, status
            FROM student_password_reset_requests
            WHERE id = ? AND teacher_id = ?
            """,
            (request_id, user["id"]),
        ).fetchone()
        if not request_row:
            raise HTTPException(status_code=404, detail="找回密码申请不存在。")
        if request_row["status"] != "pending":
            raise HTTPException(status_code=400, detail="该申请当前不能再执行拒绝操作。")

        conn.execute(
            """
            UPDATE student_password_reset_requests
            SET status = 'rejected', reviewed_at = ?, reviewed_by_teacher_id = ?, review_note = ?
            WHERE id = ?
            """,
            (datetime.now().isoformat(), user["id"], review_note.strip(), request_id),
        )
        conn.commit()

    return {"status": "success", "message": "已拒绝该找回密码申请。"}
