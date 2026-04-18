import json
import os
import sqlite3
import tempfile
import traceback
import uuid
from datetime import datetime
from pathlib import Path

import aiofiles
import httpx
from fastapi import APIRouter, Request, Form, HTTPException, Depends, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse

from ..config import ROSTER_DIR, SHARE_DIR, TEXTBOOK_ATTACHMENT_DIR
from ..core import ai_client
from ..database import get_db_connection
from ..dependencies import get_current_teacher, invalidate_session_for_user
from ..services.academic_service import (
    build_classroom_ai_context,
    compute_semester_week_count,
    infer_semester_name,
    parse_date_input,
    parse_json_list_field,
)
from ..services.file_handler import save_upload_file
from ..services.roster_handler import parse_excel_to_students
from ..services.student_auth_service import build_student_security_summary, list_student_login_history
from ..services.submission_file_alignment import run_full_alignment

router = APIRouter(prefix="/api/manage", dependencies=[Depends(get_current_teacher)])


def _ensure_teacher_owned_record(
    conn,
    *,
    table: str,
    record_id: int,
    teacher_id: int,
    owner_column: str,
):
    row = conn.execute(
        f"SELECT * FROM {table} WHERE id = ? AND {owner_column} = ?",
        (record_id, teacher_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "目标记录不存在或无权操作")
    return row


def _ensure_teacher_owned_offering(conn, offering_id: int, teacher_id: int):
    offering = conn.execute(
        """
        SELECT o.*,
               COALESCE(s.name, o.semester) AS semester_name,
               tb.title AS textbook_title
        FROM class_offerings o
        LEFT JOIN academic_semesters s ON s.id = o.semester_id
        LEFT JOIN textbooks tb ON tb.id = o.textbook_id
        WHERE o.id = ? AND o.teacher_id = ?
        LIMIT 1
        """,
        (offering_id, teacher_id),
    ).fetchone()
    if not offering:
        raise HTTPException(404, "课堂不存在或无权操作")
    return offering


def _validate_teacher_owned_selection(
    conn,
    *,
    teacher_id: int,
    class_id: int,
    course_id: int,
    semester_id: int,
    textbook_id: int,
) -> tuple[sqlite3.Row, sqlite3.Row, sqlite3.Row, sqlite3.Row]:
    class_row = _ensure_teacher_owned_record(
        conn,
        table="classes",
        record_id=class_id,
        teacher_id=teacher_id,
        owner_column="created_by_teacher_id",
    )
    course_row = _ensure_teacher_owned_record(
        conn,
        table="courses",
        record_id=course_id,
        teacher_id=teacher_id,
        owner_column="created_by_teacher_id",
    )
    semester_row = _ensure_teacher_owned_record(
        conn,
        table="academic_semesters",
        record_id=semester_id,
        teacher_id=teacher_id,
        owner_column="teacher_id",
    )
    textbook_row = _ensure_teacher_owned_record(
        conn,
        table="textbooks",
        record_id=textbook_id,
        teacher_id=teacher_id,
        owner_column="teacher_id",
    )
    return class_row, course_row, semester_row, textbook_row


def _remove_file_if_exists(path_value: str | None) -> None:
    normalized_path = str(path_value or "").strip()
    if not normalized_path:
        return

    try:
        file_path = Path(normalized_path)
        if file_path.exists():
            file_path.unlink()
    except Exception:
        pass


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
        if temp_excel_path.exists():
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
        if temp_excel_path.exists():
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


# --- 学期与教材管理 ---
@router.post("/semesters/save", response_class=JSONResponse)
async def api_save_semester(
    semester_id: str = Form(default=""),
    name: str = Form(default=""),
    start_date: str = Form(...),
    end_date: str = Form(...),
    user: dict = Depends(get_current_teacher),
):
    semester_id_value = int(str(semester_id).strip()) if str(semester_id).strip() else None
    try:
        start_date_value = parse_date_input(start_date, "学期开始时间")
        end_date_value = parse_date_input(end_date, "学期结束时间")
        if not start_date_value or not end_date_value:
            raise HTTPException(400, "请完整填写学期起止日期")
        week_count = compute_semester_week_count(start_date_value, end_date_value)
        semester_name = str(name or "").strip() or infer_semester_name(start_date_value)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    with get_db_connection() as conn:
        try:
            if semester_id_value:
                _ensure_teacher_owned_record(
                    conn,
                    table="academic_semesters",
                    record_id=semester_id_value,
                    teacher_id=user["id"],
                    owner_column="teacher_id",
                )
                conn.execute(
                    """
                    UPDATE academic_semesters
                    SET name = ?, start_date = ?, end_date = ?, week_count = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND teacher_id = ?
                    """,
                    (
                        semester_name,
                        start_date_value.isoformat(),
                        end_date_value.isoformat(),
                        week_count,
                        semester_id_value,
                        user["id"],
                    ),
                )
                action_text = "更新"
            else:
                conn.execute(
                    """
                    INSERT INTO academic_semesters (teacher_id, name, start_date, end_date, week_count)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        user["id"],
                        semester_name,
                        start_date_value.isoformat(),
                        end_date_value.isoformat(),
                        week_count,
                    ),
                )
                action_text = "创建"
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise HTTPException(400, f"保存失败，学期名称“{semester_name}”已存在") from exc

    return {
        "status": "success",
        "message": f"学期已{action_text}：{semester_name}",
    }


@router.delete("/semesters/{semester_id}", response_class=JSONResponse)
async def api_delete_semester(semester_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        semester_row = _ensure_teacher_owned_record(
            conn,
            table="academic_semesters",
            record_id=semester_id,
            teacher_id=user["id"],
            owner_column="teacher_id",
        )
        offering_count = conn.execute(
            "SELECT COUNT(*) AS count FROM class_offerings WHERE semester_id = ? AND teacher_id = ?",
            (semester_id, user["id"]),
        ).fetchone()
        linked_count = int((offering_count["count"] if offering_count else 0) or 0)
        if linked_count > 0:
            raise HTTPException(
                400,
                f"该学期已被 {linked_count} 个课堂使用，请先调整课堂的学期绑定后再删除",
            )

        conn.execute(
            "DELETE FROM academic_semesters WHERE id = ? AND teacher_id = ?",
            (semester_id, user["id"]),
        )
        conn.commit()

    return {
        "status": "success",
        "message": f"学期“{semester_row['name']}”已删除",
    }


@router.post("/textbooks/save", response_class=JSONResponse)
async def api_save_textbook(
    textbook_id: str = Form(default=""),
    title: str = Form(...),
    authors_json: str = Form(default="[]"),
    publisher: str = Form(default=""),
    publication_date: str = Form(default=""),
    introduction: str = Form(default=""),
    catalog_text: str = Form(default=""),
    tags_json: str = Form(default="[]"),
    remove_attachment: bool = Form(default=False),
    attachment: UploadFile | None = File(default=None),
    user: dict = Depends(get_current_teacher),
):
    normalized_title = str(title or "").strip()
    textbook_id_value = int(str(textbook_id).strip()) if str(textbook_id).strip() else None
    if not normalized_title:
        raise HTTPException(400, "教材名称不能为空")

    try:
        authors = parse_json_list_field(
            authors_json,
            field_name="作者",
            max_items=12,
            max_length=30,
        )
        tags = parse_json_list_field(
            tags_json,
            field_name="标签",
            max_items=20,
            max_length=12,
        )
        publication_date_value = parse_date_input(publication_date, "出版日期")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    attachment_info = None
    old_attachment_path = ""
    old_attachment_name = ""
    old_attachment_size = 0
    old_attachment_mime_type = ""

    if attachment and str(attachment.filename or "").strip():
        upload_dir = TEXTBOOK_ATTACHMENT_DIR / str(user["id"])
        attachment_info = await save_upload_file(upload_dir, attachment)
        if not attachment_info:
            raise HTTPException(500, "教材附件保存失败")

    with get_db_connection() as conn:
        try:
            if textbook_id_value:
                existing_row = _ensure_teacher_owned_record(
                    conn,
                    table="textbooks",
                    record_id=textbook_id_value,
                    teacher_id=user["id"],
                    owner_column="teacher_id",
                )
                old_attachment_path = str(existing_row["attachment_path"] or "")
                old_attachment_name = str(existing_row["attachment_name"] or "")
                old_attachment_size = int(existing_row["attachment_size"] or 0)
                old_attachment_mime_type = str(existing_row["attachment_mime_type"] or "")

                attachment_name = old_attachment_name
                attachment_path = old_attachment_path
                attachment_size = old_attachment_size
                attachment_mime_type = old_attachment_mime_type

                if remove_attachment and not attachment_info:
                    attachment_name = ""
                    attachment_path = ""
                    attachment_size = 0
                    attachment_mime_type = ""

                if attachment_info:
                    attachment_name = str(attachment_info["original_filename"] or "")
                    attachment_path = str(attachment_info["stored_path"] or "")
                    attachment_size = int(Path(attachment_path).stat().st_size) if attachment_path else 0
                    attachment_mime_type = str(attachment.content_type or "")

                conn.execute(
                    """
                    UPDATE textbooks
                    SET title = ?,
                        authors_json = ?,
                        publisher = ?,
                        publication_date = ?,
                        introduction = ?,
                        catalog_text = ?,
                        attachment_name = ?,
                        attachment_path = ?,
                        attachment_size = ?,
                        attachment_mime_type = ?,
                        tags_json = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND teacher_id = ?
                    """,
                    (
                        normalized_title,
                        json.dumps(authors, ensure_ascii=False),
                        str(publisher or "").strip(),
                        publication_date_value.isoformat() if publication_date_value else "",
                        str(introduction or "").strip(),
                        str(catalog_text or "").strip(),
                        attachment_name,
                        attachment_path,
                        attachment_size,
                        attachment_mime_type,
                        json.dumps(tags, ensure_ascii=False),
                        textbook_id_value,
                        user["id"],
                    ),
                )
                persisted_textbook_id = textbook_id_value
                action_text = "更新"
            else:
                attachment_name = str(attachment_info["original_filename"] or "") if attachment_info else ""
                attachment_path = str(attachment_info["stored_path"] or "") if attachment_info else ""
                attachment_size = int(Path(attachment_path).stat().st_size) if attachment_path else 0
                attachment_mime_type = str(attachment.content_type or "") if attachment_info else ""
                cursor = conn.execute(
                    """
                    INSERT INTO textbooks (
                        teacher_id,
                        title,
                        authors_json,
                        publisher,
                        publication_date,
                        introduction,
                        catalog_text,
                        attachment_name,
                        attachment_path,
                        attachment_size,
                        attachment_mime_type,
                        tags_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user["id"],
                        normalized_title,
                        json.dumps(authors, ensure_ascii=False),
                        str(publisher or "").strip(),
                        publication_date_value.isoformat() if publication_date_value else "",
                        str(introduction or "").strip(),
                        str(catalog_text or "").strip(),
                        attachment_name,
                        attachment_path,
                        attachment_size,
                        attachment_mime_type,
                        json.dumps(tags, ensure_ascii=False),
                    ),
                )
                persisted_textbook_id = int(cursor.lastrowid)
                action_text = "创建"

            conn.commit()
        except Exception:
            if attachment_info:
                _remove_file_if_exists(attachment_info.get("stored_path"))
            raise

    if attachment_info and old_attachment_path and old_attachment_path != attachment_info.get("stored_path"):
        _remove_file_if_exists(old_attachment_path)
    elif remove_attachment and old_attachment_path and not attachment_info:
        _remove_file_if_exists(old_attachment_path)

    return {
        "status": "success",
        "message": f"教材已{action_text}：{normalized_title}",
        "textbook_id": persisted_textbook_id,
    }


@router.delete("/textbooks/{textbook_id}", response_class=JSONResponse)
async def api_delete_textbook(textbook_id: int, user: dict = Depends(get_current_teacher)):
    attachment_path = ""
    with get_db_connection() as conn:
        textbook_row = _ensure_teacher_owned_record(
            conn,
            table="textbooks",
            record_id=textbook_id,
            teacher_id=user["id"],
            owner_column="teacher_id",
        )
        offering_count = conn.execute(
            "SELECT COUNT(*) AS count FROM class_offerings WHERE textbook_id = ? AND teacher_id = ?",
            (textbook_id, user["id"]),
        ).fetchone()
        linked_count = int((offering_count["count"] if offering_count else 0) or 0)
        if linked_count > 0:
            raise HTTPException(
                400,
                f"该教材已被 {linked_count} 个课堂绑定，请先调整课堂教材后再删除",
            )

        attachment_path = str(textbook_row["attachment_path"] or "")
        conn.execute(
            "DELETE FROM textbooks WHERE id = ? AND teacher_id = ?",
            (textbook_id, user["id"]),
        )
        conn.commit()

    _remove_file_if_exists(attachment_path)
    return {
        "status": "success",
        "message": f"教材“{textbook_row['title']}”已删除",
    }


@router.get("/textbooks/{textbook_id}/attachment")
async def api_download_textbook_attachment(textbook_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        textbook_row = _ensure_teacher_owned_record(
            conn,
            table="textbooks",
            record_id=textbook_id,
            teacher_id=user["id"],
            owner_column="teacher_id",
        )

    attachment_path = str(textbook_row["attachment_path"] or "").strip()
    if not attachment_path:
        raise HTTPException(404, "该教材没有附件")

    file_path = Path(attachment_path)
    if not file_path.exists():
        raise HTTPException(404, "教材附件不存在或已丢失")

    media_type = str(textbook_row["attachment_mime_type"] or "").strip() or None
    return FileResponse(
        path=file_path,
        filename=str(textbook_row["attachment_name"] or file_path.name),
        media_type=media_type,
    )


def _strip_code_fence(raw_text: str) -> str:
    text = (raw_text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text


def _parse_ai_json(raw_text: str) -> dict:
    cleaned = _strip_code_fence(raw_text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                pass
        raise


@router.post("/textbooks/ai-format-intro-catalog", response_class=JSONResponse)
async def api_ai_format_textbook_intro_catalog(
    title: str = Form(default=""),
    publisher: str = Form(default=""),
    authors_json: str = Form(default="[]"),
    publication_date: str = Form(default=""),
    tags_json: str = Form(default="[]"),
    raw_introduction: str = Form(default=""),
    raw_catalog: str = Form(default=""),
    custom_requirements: str = Form(default=""),
    attachment: UploadFile | None = File(default=None),
    user: dict = Depends(get_current_teacher),
):
    """Call AI (thinking model) to format and organize textbook introduction and catalog."""
    has_intro = bool(str(raw_introduction or "").strip())
    has_catalog = bool(str(raw_catalog or "").strip())
    if not has_intro and not has_catalog:
        raise HTTPException(400, "请至少填写教材简介或教材目录")

    # Parse basic info for context
    normalized_title = str(title or "").strip() or "未命名教材"
    try:
        authors = parse_json_list_field(
            authors_json, field_name="作者", max_items=12, max_length=30,
        )
        tags = parse_json_list_field(
            tags_json, field_name="标签", max_items=20, max_length=12,
        )
    except ValueError:
        authors = []
        tags = []

    publication_year = ""
    if publication_date:
        try:
            publication_year = str(parse_date_input(publication_date).year)
        except Exception:
            pass

    # Extract attachment text if provided
    attachment_text = ""
    if attachment and str(attachment.filename or "").strip():
        try:
            contents = await attachment.read()
            if contents:
                filename = str(attachment.filename or "").strip()
                ext = Path(filename).suffix.lower()
                text_exts = {
                    ".txt", ".md", ".py", ".js", ".ts", ".html", ".htm", ".css",
                    ".json", ".xml", ".yaml", ".yml", ".csv", ".log",
                }
                if ext in text_exts:
                    for enc in ("utf-8", "gbk", "gb2312", "latin-1"):
                        try:
                            attachment_text = contents.decode(enc)
                            break
                        except (UnicodeDecodeError, LookupError):
                            continue
                    if not attachment_text:
                        attachment_text = contents.decode("utf-8", errors="replace")
                elif ext in {".docx", ".pptx", ".xlsx", ".xls", ".doc", ".ppt", ".pdf"}:
                    try:
                        from ai_assistant_doc_extract import extract_document_text
                        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                            tmp.write(contents)
                            tmp_path = tmp.name
                        try:
                            result = extract_document_text(Path(tmp_path), ext)
                            attachment_text = str(result.text or "") if result else ""
                        finally:
                            try:
                                os.unlink(tmp_path)
                            except OSError:
                                pass
                    except Exception as exc:
                        print(f"[TEXTBOOK_AI] 附件文本提取失败 ({filename}): {exc}")
        except Exception as exc:
            print(f"[TEXTBOOK_AI] 附件读取失败: {exc}")

    # Build context block
    context_parts = [f"教材名称：{normalized_title}"]
    if publisher:
        context_parts.append(f"出版社：{publisher}")
    if authors:
        context_parts.append(f"作者：{'、'.join(authors)}")
    if publication_year:
        context_parts.append(f"出版年份：{publication_year}")
    if tags:
        context_parts.append(f"标签：{'、'.join(tags)}")
    context_block = "\n".join(context_parts)

    # Build content block
    content_parts = []
    if has_intro:
        content_parts.append(f"【原始简介】\n{raw_introduction.strip()}")
    if has_catalog:
        content_parts.append(f"【原始目录】\n{raw_catalog.strip()}")
    if attachment_text:
        content_parts.append(f"【附件内容】\n{attachment_text}")
    content_block = "\n\n".join(content_parts)

    system_prompt = (
        "你是一名高校教材内容整理助手，负责将教师提供的教材简介和目录重新规整化。"
        "你的输出必须是合法的 JSON 对象，包含两个键：\n"
        "- \"introduction\"：教材简介文本（字符串）\n"
        "- \"catalog_text\"：教材目录文本（字符串）\n\n"
        "工作要求：\n"
        "1. 目录整理：\n"
        "   - 必须完整保留原始目录中的所有章节和小节，不得遗漏任何一个条目。\n"
        "   - 如果原始目录格式混乱（如编号不一致、层级不清），请统一为「第X章」+「X.X 小节」的清晰层级格式。\n"
        "   - 保持原始缩进或用编号体现层级关系，使目录结构一目了然。\n"
        "   - 如果原始目录有重复或明显错误的编号，请自动修正。\n"
        "2. 简介改写：\n"
        "   - 将简介改写为适合学生阅读的课程导引式文本，语气亲切自然。\n"
        "   - 概括教材的核心内容、适用对象和学习目标。\n"
        "   - 突出本教材的关键知识点和教学特色，方便后续课堂AI助手理解本门课的基本要点。\n"
        "   - 如果原始简介信息不足，可以结合目录内容进行合理的补充概括，但不要编造不存在的内容。\n"
        "3. 如果提供了附件内容，请结合附件中的信息来完善简介和目录。\n"
        "4. 只输出 JSON 对象，不要输出任何额外的解释或 Markdown 代码块标记。"
    )

    user_message = f"以下是教材的基本信息及需要整理的内容：\n\n{context_block}\n\n{content_block}"
    if custom_requirements and custom_requirements.strip():
        user_message += f"\n\n【教师的自定义要求】\n{custom_requirements.strip()}"

    try:
        response = await ai_client.post(
            "/api/ai/chat",
            json={
                "system_prompt": system_prompt,
                "messages": [],
                "new_message": user_message,
                "base64_urls": [],
                "model_capability": "thinking",
                "web_search_enabled": False,
            },
            timeout=180.0,
        )
        response.raise_for_status()
        data = response.json()
    except httpx.ConnectError:
        raise HTTPException(503, "AI 助手服务未运行，请先启动 ai_assistant.py。")
    except httpx.TimeoutException:
        raise HTTPException(504, "AI 服务响应超时，请稍后重试。")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, f"AI 服务错误: {exc.response.text}")
    except Exception as exc:
        raise HTTPException(500, f"AI 请求失败: {exc}")

    if data.get("status") != "success":
        raise HTTPException(500, f"AI 返回异常: {data.get('detail', '未知错误')}")

    response_text = str(data.get("response_text") or "").strip()
    if not response_text:
        raise HTTPException(500, "AI 未返回有效内容")

    try:
        parsed = _parse_ai_json(response_text)
    except json.JSONDecodeError:
        raise HTTPException(500, "AI 返回的内容格式不正确，请重试")

    formatted_intro = str(parsed.get("introduction") or "").strip()
    formatted_catalog = str(parsed.get("catalog_text") or "").strip()

    if not formatted_intro and not formatted_catalog:
        raise HTTPException(500, "AI 返回的内容为空，请重试")

    return {
        "status": "success",
        "introduction": formatted_intro,
        "catalog_text": formatted_catalog,
    }


# --- 班级课堂 (关联) ---
@router.post("/class_offerings/create", response_class=JSONResponse)
async def api_create_class_offering(
        request: Request,
        class_id: int = Form(...),
        course_id: int = Form(...),
        semester_id: int = Form(...),
        textbook_id: int = Form(...),
        user: dict = Depends(get_current_teacher)
):
    try:
        with get_db_connection() as conn:
            _, _, semester_row, textbook_row = _validate_teacher_owned_selection(
                conn,
                teacher_id=user["id"],
                class_id=class_id,
                course_id=course_id,
                semester_id=semester_id,
                textbook_id=textbook_id,
            )
            conn.execute(
                """
                INSERT INTO class_offerings (
                    class_id,
                    course_id,
                    teacher_id,
                    semester,
                    semester_id,
                    textbook_id
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    class_id,
                    course_id,
                    user["id"],
                    str(semester_row["name"] or "").strip(),
                    semester_id,
                    textbook_id,
                ),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(400, "创建失败，该班级课程在当前学期可能已存在。")
    except Exception as e:
        raise HTTPException(500, f"数据库错误: {e}")

    return {
        "status": "success",
        "message": f"课堂已开设，并绑定学期“{semester_row['name']}”和教材“{textbook_row['title']}”",
    }


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
        class_offering_id: int = Form(...),
        system_prompt: str = Form(""),
        syllabus: str = Form(""),
        textbook_id: str = Form(default=""),
        user: dict = Depends(get_current_teacher)
):
    """
    创建或更新一个特定课堂的 AI 配置，并同步更新教材绑定
    """
    conn = get_db_connection()
    try:
        _ensure_teacher_owned_offering(conn, class_offering_id, user["id"])

        textbook_id_value = int(str(textbook_id).strip()) if str(textbook_id).strip() else None
        bound_textbook_id = None
        if textbook_id_value:
            textbook_row = _ensure_teacher_owned_record(
                conn,
                table="textbooks",
                record_id=textbook_id_value,
                teacher_id=user["id"],
                owner_column="teacher_id",
            )
            bound_textbook_id = int(textbook_row["id"])

        conn.execute(
            """
            UPDATE class_offerings
            SET textbook_id = ?
            WHERE id = ? AND teacher_id = ?
            """,
            (bound_textbook_id, class_offering_id, user["id"]),
        )

        conn.execute(
            """
            INSERT INTO ai_class_configs (class_offering_id, system_prompt, syllabus)
            VALUES (?, ?, ?)
            ON CONFLICT(class_offering_id) DO UPDATE SET
                system_prompt = excluded.system_prompt,
                syllabus = excluded.syllabus,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                class_offering_id,
                str(system_prompt or "").strip(),
                str(syllabus or "").strip(),
            ),
        )

        conn.commit()
    except sqlite3.IntegrityError as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=f"配置保存失败: {e}")
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"服务器内部错误: {e}")
    finally:
        conn.close()

    return {
        "status": "success",
        "message": "AI 配置保存成功！",
        "class_offering_id": class_offering_id,
        "textbook_id": bound_textbook_id,
    }


# (新增) 获取 AI 配置 (用于前端加载)
@router.get("/ai/config/{class_offering_id}", response_class=JSONResponse)
async def api_get_ai_config(class_offering_id: int, user: dict = Depends(get_current_teacher)):
    """获取一个特定课堂的 AI 配置"""
    conn = get_db_connection()
    try:
        offering = _ensure_teacher_owned_offering(conn, class_offering_id, user["id"])
        config_row = conn.execute(
            "SELECT system_prompt, syllabus FROM ai_class_configs WHERE class_offering_id = ?",
            (class_offering_id,),
        ).fetchone()
        classroom_context = build_classroom_ai_context(conn, class_offering_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"服务器内部错误: {e}")
    finally:
        conn.close()

    config = dict(config_row) if config_row else {"system_prompt": "", "syllabus": ""}
    return {
        **config,
        "textbook_id": int(offering["textbook_id"]) if offering["textbook_id"] else None,
        "semester_name": str(offering["semester_name"] or ""),
        "textbook": classroom_context.get("textbook") or None,
        "classroom_summary": classroom_context.get("classroom_summary") or "",
        "textbook_summary": classroom_context.get("textbook_summary") or "",
        "recent_material_names": classroom_context.get("recent_material_names") or [],
        "recent_assignment_titles": classroom_context.get("recent_assignment_titles") or [],
    }


# --- 课堂 AI 智能生成 ---
@router.post("/ai/ai-generate", response_class=JSONResponse)
async def api_ai_generate_config(
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    """调用思考模型 AI，根据课堂和教材信息生成系统提示词和课程大纲。"""
    try:
        data = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(400, "请求数据格式错误")

    class_offering_id = data.get("class_offering_id")
    textbook_id = data.get("textbook_id")

    if not class_offering_id:
        raise HTTPException(400, "请先选择一个课堂")
    try:
        class_offering_id = int(class_offering_id)
    except (ValueError, TypeError):
        raise HTTPException(400, "无效的课堂 ID")

    if not textbook_id:
        raise HTTPException(400, "请先选择一本教材，AI 生成需要教材信息作为知识依据")
    try:
        textbook_id = int(textbook_id)
    except (ValueError, TypeError):
        raise HTTPException(400, "无效的教材 ID")

    # 获取课堂和教材上下文
    with get_db_connection() as conn:
        _ensure_teacher_owned_offering(conn, class_offering_id, user["id"])
        _ensure_teacher_owned_record(
            conn,
            table="textbooks",
            record_id=textbook_id,
            teacher_id=user["id"],
            owner_column="teacher_id",
        )
        classroom_context = build_classroom_ai_context(conn, class_offering_id)

    if not classroom_context:
        raise HTTPException(404, "课堂信息不存在")

    classroom_summary = classroom_context.get("classroom_summary") or ""
    textbook_summary = classroom_context.get("textbook_summary") or ""
    textbook = classroom_context.get("textbook") or {}
    recent_materials = classroom_context.get("recent_material_names") or []
    recent_assignments = classroom_context.get("recent_assignment_titles") or []

    teacher_name = classroom_context.get("teacher_name") or "任课教师"
    course_name = classroom_context.get("course_name") or "课程"
    class_name = classroom_context.get("class_name") or "班级"
    semester_name = classroom_context.get("semester_name") or ""
    class_student_count = classroom_context.get("class_student_count") or 0
    course_credits = classroom_context.get("course_credits")
    course_description = classroom_context.get("course_description") or ""

    # 构建发送给 AI 的提示词
    system_prompt_for_ai = (
        "你是一名高校课堂 AI 助教配置专家。根据教师提供的课堂信息和教材信息，"
        "为其生成课堂 AI 助教的「系统提示词」和「课程大纲 / 知识依据」。\n\n"
        "你的输出必须是合法的 JSON 对象，包含两个键：\n"
        "- \"system_prompt\"：课堂 AI 助教的系统提示词（字符串）\n"
        "- \"syllabus\"：课程大纲 / 知识依据（字符串）\n\n"
        "只输出 JSON 对象，不要输出任何额外的解释或 Markdown 代码块标记。"
    )

    user_message_parts = [
        f"请为以下课堂生成 AI 助教配置：\n",
        f"--- 课堂基本信息 ---",
        f"课程名称：{course_name}",
        f"授课班级：{class_name}",
        f"任课教师：{teacher_name}",
    ]
    if semester_name:
        user_message_parts.append(f"所属学期：{semester_name}")
    if class_student_count:
        user_message_parts.append(f"班级人数：{int(class_student_count)} 人")
    if course_credits is not None:
        user_message_parts.append(f"课程学分：{course_credits}")
    if course_description:
        user_message_parts.append(f"课程简介：{course_description.strip()[:800]}")

    user_message_parts.append(f"\n--- 教材信息 ---\n{textbook_summary}")

    if recent_materials:
        user_message_parts.append(f"\n--- 最近课堂材料 ---\n{'、'.join(recent_materials)}")
    if recent_assignments:
        user_message_parts.append(f"\n--- 最近课堂任务 ---\n{'、'.join(recent_assignments)}")

    user_message_parts.append(f"""
--- 生成要求 ---

一、system_prompt（系统提示词）要求：
这是给课堂 AI 助教看的提示词，让助教 AI 在回复学生时表现得活泼、可爱、热情且专业。
具体要求：
1. 赋予 AI 助教一个亲和力十足的角色设定，名字可以用"小X助手"之类的可爱称呼，语气自然轻松。
2. 使用简体中文回复，表达风格要生动活泼，适当使用鼓励性语言和表情符号（如"太棒了！""加油~""没问题，我来帮你~"等）。
3. 回答专业问题时必须严谨准确，不能为了活泼而牺牲专业性。
4. 面向学生时：优先讲思路、举例子、拆步骤，不直接代写作业或泄露考试答案；当学生遇到困难时先共情鼓励，再引导解决。
5. 面向教师时：帮助备课、设计活动、梳理知识点、优化教学表达，语气可以更专业但依然亲和。
6. 明确使用边界：超出课程范围的问题要温和说明边界并给出查证方向；教材/材料/大纲不一致时先指出差异。
7. 引用教材章节、知识点名称使建议可落地，让回答有根有据。
8. 学生焦虑或挫败时，先用短句共情，再给可执行的小步建议。
9. 任课教师在生成提示词时，请在提示词中体现教师姓名：{teacher_name}。
10. 提示词要详细完整，确保 AI 助教能够理解自己的角色定位、行为准则和教学目标。建议 300-600 字。

二、syllabus（课程大纲 / 知识依据）要求：
这是给助教 AI 看的知识参考，让 AI 全面了解课堂信息以便更好辅助教学。
具体要求：
1. 侧重点在课堂知识范围和核心知识点梳理上，这是最重要的部分。
2. 基于教材目录和教材简介，梳理出课程的章节结构、核心知识点和学习要点。
3. 包含课堂的基本信息：课程名称、班级、学期、教师、学生人数等。
4. 如果教材有目录信息，请按章节结构化整理出知识点概要。
5. 包含课程目标、考核方式的建议模板（供教师后续修改）。
6. 包含 AI 回答约束：哪些可以直接回答、哪些需引导回教材、哪些必须提醒教师确认。
7. 内容要全面详实，建议 500-1000 字。
""")

    user_message = "\n".join(user_message_parts)

    try:
        response = await ai_client.post(
            "/api/ai/chat",
            json={
                "system_prompt": system_prompt_for_ai,
                "messages": [],
                "new_message": user_message,
                "base64_urls": [],
                "model_capability": "thinking",
                "web_search_enabled": False,
            },
            timeout=180.0,
        )
        response.raise_for_status()
        data = response.json()
    except httpx.ConnectError:
        raise HTTPException(503, "AI 助手服务未运行，请先启动 ai_assistant.py。")
    except httpx.TimeoutException:
        raise HTTPException(504, "AI 服务响应超时，请稍后重试。")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, f"AI 服务错误: {exc.response.text}")
    except Exception as exc:
        raise HTTPException(500, f"AI 请求失败: {exc}")

    if data.get("status") != "success":
        raise HTTPException(500, f"AI 返回异常: {data.get('detail', '未知错误')}")

    response_text = str(data.get("response_text") or "").strip()
    if not response_text:
        raise HTTPException(500, "AI 未返回有效内容")

    try:
        parsed = _parse_ai_json(response_text)
    except json.JSONDecodeError:
        raise HTTPException(500, "AI 返回的内容格式不正确，请重试")

    generated_system_prompt = str(parsed.get("system_prompt") or "").strip()
    generated_syllabus = str(parsed.get("syllabus") or "").strip()

    if not generated_system_prompt and not generated_syllabus:
        raise HTTPException(500, "AI 生成的内容为空，请重试")

    return {
        "status": "success",
        "system_prompt": generated_system_prompt,
        "syllabus": generated_syllabus,
    }


# --- 课堂列表 API (试卷分配用) ---
@router.get("/offerings/list", response_class=JSONResponse)
async def api_list_offerings(user: dict = Depends(get_current_teacher)):
    """获取当前教师的课堂列表（用于试卷分配）"""
    try:
        conn = get_db_connection()
        cursor = conn.execute(
            """
               SELECT o.id,
                      COALESCE(s.name, o.semester) AS semester,
                      c.name AS class_name,
                      co.name AS course_name,
                      tb.title AS textbook_title
               FROM class_offerings o
               JOIN classes c ON o.class_id = c.id
               JOIN courses co ON o.course_id = co.id
               LEFT JOIN academic_semesters s ON s.id = o.semester_id
               LEFT JOIN textbooks tb ON tb.id = o.textbook_id
               WHERE o.teacher_id = ?
               ORDER BY COALESCE(s.start_date, o.created_at) DESC, co.name, c.name
            """,
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


@router.post("/system/repair-submission-files", response_class=JSONResponse)
async def api_repair_submission_files(user: dict = Depends(get_current_teacher)):
    """Repair stale stored_path entries and recover orphaned submission files.

    This is an administrative action that:
    1. Fixes stored_path values that point to wrong drives / directories
    2. Discovers files on disk with no DB record and reconstructs entries
    """
    try:
        with get_db_connection() as conn:
            report = run_full_alignment(conn)
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(500, f"修复失败: {exc}")

    return {
        "status": "success",
        "message": (
            f"路径修复: {report['stale_path_repair']['paths_repaired']} 条已修复, "
            f"{report['stale_path_repair']['paths_still_missing']} 条仍缺失; "
            f"孤立文件恢复: {report['orphan_recovery']['orphan_files_recovered']} 个文件已恢复, "
            f"{report['orphan_recovery']['orphan_submissions_created']} 条提交记录已重建"
        ),
        "report": report,
    }
