import os
import uuid
import pandas as pd
import aiofiles
import sqlite3
from datetime import datetime
from typing import List
from pathlib import Path
from fastapi import APIRouter, Request, Form, HTTPException, Depends, UploadFile, File
from fastapi.responses import JSONResponse, FileResponse

# 修复：移除这个错误的导入，COURSE_INFO 不再是 V4.0 的依赖
# from ..core import COURSE_INFO
from ..config import HOMEWORK_SUBMISSIONS_DIR, MAX_UPLOAD_SIZE_BYTES, MAX_UPLOAD_SIZE_MB
from ..database import get_db_connection
from ..dependencies import get_current_user, get_current_student, get_current_teacher
from ..services.file_handler import save_upload_file

router = APIRouter(prefix="/api")


# --- 教师作业 API ---
@router.post("/courses/{course_id}/assignments", response_class=JSONResponse)
async def create_assignment(course_id: int, request: Request, user: dict = Depends(get_current_teacher)):
    """V4.0: 在指定课程下创建新作业"""
    data = await request.json()
    new_id = str(uuid.uuid4())
    created_at = datetime.now().isoformat()

    # 作业文件夹现在按 Course / Assignment 组织
    assignment_dir = HOMEWORK_SUBMISSIONS_DIR / str(course_id) / new_id
    assignment_dir.mkdir(parents=True, exist_ok=True)

    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO assignments (id, course_id, title, status, requirements_md, rubric_md, grading_mode, created_at) VALUES (?, ?, ?, 'new', ?, ?, ?, ?)",
            (new_id, course_id, data['title'], data['requirements_md'], data['rubric_md'], data['grading_mode'],
             created_at)
        )
        conn.commit()
    return {"status": "success", "new_assignment_id": new_id}


@router.put("/assignments/{assignment_id}", response_class=JSONResponse)
async def update_assignment(assignment_id: str, request: Request, user: dict = Depends(get_current_teacher)):
    data = await request.json()
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE assignments SET title = ?, requirements_md = ?, rubric_md = ?, grading_mode = ?, status = ? WHERE id = ?",
            (data['title'], data['requirements_md'], data['rubric_md'], data['grading_mode'], data['status'],
             assignment_id))
        conn.commit()
    return {"status": "success", "updated_assignment_id": assignment_id}


@router.delete("/assignments/{assignment_id}", response_class=JSONResponse)
async def delete_assignment(assignment_id: str, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        conn.execute("DELETE FROM assignments WHERE id = ?", (assignment_id,))
        conn.commit()
    # TODO: 删除磁盘上的文件夹
    return {"status": "success", "deleted_assignment_id": assignment_id}


@router.get("/assignments/{assignment_id}/submissions", response_class=JSONResponse)
async def get_submissions_for_assignment(assignment_id: str, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        submissions_cursor = conn.execute(
            "SELECT * FROM submissions WHERE assignment_id = ? ORDER BY submitted_at DESC", (assignment_id,))
        submissions = [dict(row) for row in submissions_cursor]
    total_submissions = len(submissions)
    graded_submissions = [s for s in submissions if s['status'] == 'graded' and s['score'] is not None]
    average_score = sum(s['score'] for s in graded_submissions) / len(graded_submissions) if graded_submissions else 0
    return {"status": "success",
            "stats": {"total_submissions": total_submissions, "average_score": round(average_score, 1),
                      "common_mistakes": "N/A"}, "submissions": submissions}


@router.post("/submissions/{submission_id}/grade", response_class=JSONResponse)
async def grade_submission(submission_id: int, request: Request, user: dict = Depends(get_current_teacher)):
    data = await request.json()
    with get_db_connection() as conn:
        conn.execute("UPDATE submissions SET status = 'graded', score = ?, feedback_md = ? WHERE id = ?",
                     (data['score'], data['feedback_md'], submission_id))
        conn.commit()
    return {"status": "success", "graded_submission_id": submission_id}


@router.delete("/submissions/{submission_id}", response_class=JSONResponse)
async def return_submission(submission_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        conn.execute("DELETE FROM submissions WHERE id = ?", (submission_id,))
        conn.commit()
    # TODO: 删除磁盘上的文件
    return {"status": "success", "deleted_submission_id": submission_id}


@router.get("/assignments/{assignment_id}/export/{class_id}", response_class=FileResponse)
async def export_grades_for_class(assignment_id: str, class_id: int, user: dict = Depends(get_current_teacher)):
    """V4.0: 导出此作业在指定班级的成绩"""
    with get_db_connection() as conn:
        assignment = conn.execute("SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
        class_info = conn.execute("SELECT * FROM classes WHERE id = ?", (class_id,)).fetchone()
        if not assignment or not class_info: raise HTTPException(404, "未找到作业或班级")

        # 修复：此函数不依赖 COURSE_INFO，而是直接从数据库查询
        # 1. 获取班级所有学生
        roster_cursor = conn.execute("SELECT id, student_id_number, name FROM students WHERE class_id = ?", (class_id,))
        roster_df = pd.DataFrame(roster_cursor, columns=['student_pk_id', '学号', '姓名'])

        if roster_df.empty:
            raise HTTPException(404, "此班级没有学生，无法导出。")

        # 2. 获取这个班级学生的此项作业成绩
        grades_cursor = conn.execute(
            """SELECT student_pk_id, student_name, score, status, feedback_md
               FROM submissions
               WHERE assignment_id = ?
                 AND student_pk_id IN (SELECT id FROM students WHERE class_id = ?)""",
            (assignment_id, class_id)
        )
        grades_df = pd.DataFrame(grades_cursor, columns=['student_pk_id', '提交姓名', '分数', '状态', '评语'])

    final_df = roster_df.merge(grades_df, on='student_pk_id', how='left')

    export_filename = f"成绩_{class_info['name']}_{assignment['title']}.xlsx"
    # 确保作业目录存在
    assignment_dir = HOMEWORK_SUBMISSIONS_DIR / str(assignment['course_id']) / assignment['id']
    assignment_dir.mkdir(parents=True, exist_ok=True)
    export_path = assignment_dir / export_filename

    final_df.to_excel(export_path, index=False)
    return FileResponse(export_path, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        filename=export_filename)


# --- 学生作业 API ---
@router.post("/assignments/{assignment_id}/submit", response_class=JSONResponse)
async def submit_assignment(assignment_id: str, files: List[UploadFile] = File(...),
                            user: dict = Depends(get_current_student)):
    with get_db_connection() as conn:
        assignment = conn.execute("SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
        if not assignment: raise HTTPException(404, "Assignment not found")
        if assignment['status'] != 'published': raise HTTPException(400, "作业已截止或未发布")

        submission = conn.execute(
            "SELECT id FROM submissions WHERE assignment_id = ? AND student_pk_id = ?", (assignment_id, user['id'])
        ).fetchone()
        if submission: raise HTTPException(400, "您已经提交过此作业")

        total_size = 0
        for f in files:
            # 修正：f.file.seek(0, 2) 可能在内存中不可用，使用更安全的方式
            f.file.seek(0, os.SEEK_END)
            file_size = f.file.tell()
            f.file.seek(0, os.SEEK_SET)
            if file_size > MAX_UPLOAD_SIZE_BYTES: raise HTTPException(413,
                                                                      f"文件 '{f.filename}' 超过 {MAX_UPLOAD_SIZE_MB}MB")
            total_size += file_size
        if total_size > MAX_UPLOAD_SIZE_BYTES: raise HTTPException(413, f"总文件大小超过 {MAX_UPLOAD_SIZE_MB}MB")

        try:
            cursor = conn.cursor()
            submitted_at = datetime.now().isoformat()
            cursor.execute(
                "INSERT INTO submissions (assignment_id, student_pk_id, student_name, status, submitted_at) VALUES (?, ?, ?, 'submitted', ?)",
                (assignment_id, user['id'], user['name'], submitted_at)
            )
            submission_id = cursor.lastrowid

            # V4.0: 路径按 课程ID / 作业ID / 学生PK_ID 存储
            submission_dir = HOMEWORK_SUBMISSIONS_DIR / str(assignment['course_id']) / assignment['id'] / str(
                user['id'])

            for f in files:
                file_info = await save_upload_file(submission_dir, f)
                if not file_info: raise HTTPException(500, "文件保存失败")

                cursor.execute(
                    "INSERT INTO submission_files (submission_id, original_filename, stored_path) VALUES (?, ?, ?)",
                    (submission_id, file_info['original_filename'], file_info['stored_path'])
                )

            conn.commit()
        except sqlite3.IntegrityError:
            conn.rollback()
            raise HTTPException(400, "您已经提交过此作业。")
        except Exception as e:
            conn.rollback()
            print(f"[ERROR] Submission failed: {e}")
            raise HTTPException(500, f"数据库错误: {e}")

    return {"status": "success", "submission_id": submission_id}

