import uuid
import json
import io
import zipfile
import pandas as pd
import sqlite3
from urllib.parse import quote
from datetime import datetime
from pathlib import Path
from typing import Any, List
from fastapi import APIRouter, Request, Form, HTTPException, Depends, UploadFile, File
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse

# 修复：移除这个错误的导入，COURSE_INFO 不再是 V4.0 的依赖
# from ..core import COURSE_INFO
from ..config import (
    HOMEWORK_SUBMISSIONS_DIR,
    MAX_SUBMISSION_FILE_COUNT,
    MAX_SUBMISSION_PER_FILE_BYTES,
    MAX_SUBMISSION_PER_FILE_MB,
    MAX_SUBMISSION_TOTAL_BYTES,
    MAX_SUBMISSION_TOTAL_MB,
    MAX_UPLOAD_SIZE_BYTES,
    MAX_UPLOAD_SIZE_MB,
)
from ..database import get_db_connection
from ..dependencies import get_current_user, get_current_student, get_current_teacher
from ..services.behavior_tracking_service import record_behavior_event
from ..services.message_center_service import (
    create_assignment_published_notifications,
    create_student_grading_notification,
    create_submission_notification,
)
from ..services.assignment_lifecycle_service import (
    assignment_accepts_submissions,
    build_resubmission_due_at,
    build_assignment_schedule_fields,
    close_overdue_assignments,
    enrich_assignment_runtime_view,
    refresh_assignment_runtime_status,
    submission_resubmission_accepts,
)
from ..services.submission_assets import (
    answers_have_content,
    decode_allowed_file_types_json,
    delete_storage_tree,
    encode_allowed_file_types_json,
    is_allowed_submission_file,
    normalize_allowed_file_types,
    parse_submission_manifest,
    store_submission_files,
    summarize_allowed_file_types,
)
from ..services.submission_file_alignment import resolve_submission_file_path

router = APIRouter(prefix="/api")


def _build_assignment_storage_dir(course_id: int, assignment_id: int | str):
    return HOMEWORK_SUBMISSIONS_DIR / str(course_id) / str(assignment_id)


def _build_submission_storage_dir(course_id: int, assignment_id: int | str, student_pk_id: int | str):
    return _build_assignment_storage_dir(course_id, assignment_id) / str(student_pk_id)


def _get_allowed_file_types(data: dict, assignment_row=None) -> list[str]:
    if "allowed_file_types" in data:
        return normalize_allowed_file_types(data.get("allowed_file_types"))
    if "allowed_file_types_json" in data:
        return decode_allowed_file_types_json(data.get("allowed_file_types_json"))
    if assignment_row is not None:
        return decode_allowed_file_types_json(assignment_row["allowed_file_types_json"])
    return []


def _ensure_accepting_submission(assignment: dict[str, Any]) -> None:
    if assignment_accepts_submissions(assignment):
        return
    status = str(assignment.get("status") or "").strip().lower()
    if status == "new":
        raise HTTPException(400, "作业尚未开始，当前不可作答或提交")
    raise HTTPException(400, "作业已截止，当前只能查看，不能作答或提交")


def _teacher_can_access_assignment(assignment: dict[str, Any], teacher_id: int) -> bool:
    teacher_id = int(teacher_id)
    owner_id = int(assignment.get("created_by_teacher_id") or 0)
    offering_teacher_id = int(assignment.get("offering_teacher_id") or 0)
    return teacher_id in {owner_id, offering_teacher_id}


def _get_assignment_for_teacher(conn, assignment_id: str, teacher_id: int) -> dict[str, Any]:
    assignment = conn.execute(
        """
        SELECT a.*,
               c.created_by_teacher_id,
               o.teacher_id AS offering_teacher_id,
               o.class_id AS offering_class_id
        FROM assignments a
        JOIN courses c ON c.id = a.course_id
        LEFT JOIN class_offerings o ON o.id = a.class_offering_id
        WHERE a.id = ?
        LIMIT 1
        """,
        (assignment_id,),
    ).fetchone()
    if not assignment:
        raise HTTPException(404, "作业不存在")
    assignment_dict = refresh_assignment_runtime_status(conn, assignment)
    if not _teacher_can_access_assignment(assignment_dict, int(teacher_id)):
        raise HTTPException(403, "无权操作该作业")
    return assignment_dict


def _get_submission_for_teacher(conn, submission_id: int, teacher_id: int) -> dict[str, Any]:
    submission = conn.execute(
        """
        SELECT s.*,
               a.course_id,
               a.class_offering_id,
               a.title AS assignment_title,
               c.created_by_teacher_id,
               o.teacher_id AS offering_teacher_id
        FROM submissions s
        JOIN assignments a ON a.id = s.assignment_id
        JOIN courses c ON c.id = a.course_id
        LEFT JOIN class_offerings o ON o.id = a.class_offering_id
        WHERE s.id = ?
        LIMIT 1
        """,
        (submission_id,),
    ).fetchone()
    if not submission:
        raise HTTPException(404, "提交记录不存在")
    submission_dict = dict(submission)
    if not _teacher_can_access_assignment(submission_dict, int(teacher_id)):
        raise HTTPException(403, "无权操作该提交")
    return submission_dict


def _parse_int_set(raw_values: Any, field_name: str) -> set[int]:
    values = raw_values or []
    if not isinstance(values, list):
        raise HTTPException(400, f"{field_name} 必须是数组")
    parsed: set[int] = set()
    for value in values:
        if str(value).strip() == "":
            continue
        try:
            parsed.add(int(value))
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, f"{field_name} 包含无效 ID") from exc
    return parsed


def _get_student_for_assignment(conn, assignment: dict[str, Any], student_pk_id: int) -> dict[str, Any]:
    student = conn.execute(
        "SELECT id, student_id_number, name, class_id FROM students WHERE id = ? LIMIT 1",
        (student_pk_id,),
    ).fetchone()
    if not student:
        raise HTTPException(404, "学生不存在")
    student_dict = dict(student)
    offering_class_id = assignment.get("offering_class_id")
    if offering_class_id and int(student_dict.get("class_id") or 0) != int(offering_class_id):
        raise HTTPException(400, "该学生不属于当前作业对应班级")
    return student_dict


def _validate_upload_entries(files: List[UploadFile], manifest: str):
    prepared_entries = parse_submission_manifest(files, manifest)
    if len(prepared_entries) > MAX_SUBMISSION_FILE_COUNT:
        raise HTTPException(413, f"文件数量不能超过 {MAX_SUBMISSION_FILE_COUNT} 个")

    total_size = 0
    for entry in prepared_entries:
        if entry.size_bytes > MAX_SUBMISSION_PER_FILE_BYTES:
            raise HTTPException(
                413,
                f"文件 '{entry.relative_path}' 超过单文件大小限制 {MAX_SUBMISSION_PER_FILE_MB:.0f}MB"
                f"（当前 {entry.size_bytes / 1024 / 1024:.1f}MB）",
            )
        total_size += entry.size_bytes
    if total_size > MAX_SUBMISSION_TOTAL_BYTES:
        raise HTTPException(
            413,
            f"总文件大小超过限制 {MAX_SUBMISSION_TOTAL_MB:.0f}MB"
            f"（当前 {total_size / 1024 / 1024:.1f}MB）",
        )
    return prepared_entries


def _parse_answers_payload(answers_json: str) -> Any:
    try:
        answers_data = json.loads(answers_json) if answers_json else {}
    except json.JSONDecodeError:
        answers_data = {"raw_text": answers_json}
    return answers_data.get("answers", answers_data) if isinstance(answers_data, dict) else answers_data


def _restore_submission_dir(submission_dir, backup_dir) -> None:
    delete_storage_tree(submission_dir)
    if backup_dir and backup_dir.exists():
        backup_dir.rename(submission_dir)


async def _save_submission_payload(
    conn,
    *,
    assignment: dict[str, Any],
    student: dict[str, Any],
    answers_json: str,
    manifest: str,
    files: List[UploadFile],
    actor_role: str,
    actor_user_pk: int | None,
    channel: str,
    existing_submission: dict[str, Any] | None = None,
    notify_teacher: bool = False,
) -> dict[str, Any]:
    prepared_entries = _validate_upload_entries(files, manifest)
    submitted_at = datetime.now().isoformat()
    answers_payload = _parse_answers_payload(answers_json)
    has_text_answers = answers_have_content(answers_payload)
    allowed_file_types = decode_allowed_file_types_json(assignment.get("allowed_file_types_json"))
    has_allowed_uploads = any(
        is_allowed_submission_file(entry.relative_path, entry.content_type, allowed_file_types)
        for entry in prepared_entries
    )

    if not has_text_answers and not prepared_entries:
        raise HTTPException(400, "请至少填写答案或上传一个文件")
    if not has_text_answers and not has_allowed_uploads:
        expected_types = summarize_allowed_file_types(allowed_file_types)
        raise HTTPException(400, f"没有符合要求的文件可提交，允许类型: {expected_types}")

    student_pk_id = int(student["id"])
    full_submission = {
        "student_id": student.get("student_id_number", ""),
        "student_name": student.get("name", ""),
        "student_pk_id": student_pk_id,
        "submitted_at": submitted_at,
        "assignment_id": assignment["id"],
        "course_id": assignment["course_id"],
        "answers": answers_payload,
        "submitted_by_role": actor_role,
        "submitted_by_teacher_id": actor_user_pk if actor_role == "teacher" else None,
        "submission_channel": channel,
    }
    full_submission_json = json.dumps(full_submission, ensure_ascii=False)

    submission_dir = _build_submission_storage_dir(assignment["course_id"], assignment["id"], student_pk_id)
    backup_dir = None
    is_replacement = bool(existing_submission)
    if is_replacement and submission_dir.exists():
        backup_dir = submission_dir.with_name(f"{submission_dir.name}.__backup__{uuid.uuid4().hex}")
        submission_dir.rename(backup_dir)

    try:
        cursor = conn.cursor()
        if existing_submission:
            submission_id = int(existing_submission["id"])
            cursor.execute("DELETE FROM submission_files WHERE submission_id = ?", (submission_id,))
            cursor.execute(
                """
                UPDATE submissions
                SET student_name = ?,
                    status = 'submitted',
                    score = NULL,
                    feedback_md = NULL,
                    answers_json = ?,
                    submitted_at = ?,
                    submitted_by_role = ?,
                    submitted_by_teacher_id = ?,
                    submission_channel = ?,
                    resubmission_allowed = 0,
                    resubmission_due_at = NULL,
                    returned_at = NULL,
                    returned_by_teacher_id = NULL,
                    returned_reason = NULL,
                    is_absence_score = 0,
                    absence_scored_at = NULL,
                    absence_scored_by_teacher_id = NULL
                WHERE id = ?
                """,
                (
                    student.get("name", ""),
                    full_submission_json,
                    submitted_at,
                    actor_role,
                    actor_user_pk if actor_role == "teacher" else None,
                    channel,
                    submission_id,
                ),
            )
        else:
            cursor.execute(
                """
                INSERT INTO submissions (
                    assignment_id, student_pk_id, student_name, status, submitted_at, answers_json,
                    submitted_by_role, submitted_by_teacher_id, submission_channel,
                    resubmission_allowed, resubmission_due_at, returned_at, returned_by_teacher_id, returned_reason,
                    is_absence_score, absence_scored_at, absence_scored_by_teacher_id
                ) VALUES (?, ?, ?, 'submitted', ?, ?, ?, ?, ?, 0, NULL, NULL, NULL, NULL, 0, NULL, NULL)
                """,
                (
                    assignment["id"],
                    student_pk_id,
                    student.get("name", ""),
                    submitted_at,
                    full_submission_json,
                    actor_role,
                    actor_user_pk if actor_role == "teacher" else None,
                    channel,
                ),
            )
            submission_id = cursor.lastrowid

        storage_result = await store_submission_files(submission_dir, prepared_entries, allowed_file_types)
        if not storage_result.stored_files and not has_text_answers:
            expected_types = summarize_allowed_file_types(allowed_file_types)
            raise HTTPException(400, f"没有符合要求的文件可提交，允许类型: {expected_types}")

        for file_info in storage_result.stored_files:
            cursor.execute(
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

        if notify_teacher:
            try:
                create_submission_notification(conn, submission_id)
            except Exception as exc:
                print(f"[MESSAGE_CENTER] submission notify failed: {exc}")
        conn.commit()
        if backup_dir:
            delete_storage_tree(backup_dir)
    except sqlite3.IntegrityError:
        conn.rollback()
        _restore_submission_dir(submission_dir, backup_dir)
        raise HTTPException(400, "该学生已经提交过此作业")
    except HTTPException:
        conn.rollback()
        _restore_submission_dir(submission_dir, backup_dir)
        raise
    except Exception as e:
        conn.rollback()
        _restore_submission_dir(submission_dir, backup_dir)
        print(f"[ERROR] Submission failed: {e}")
        raise HTTPException(500, f"数据库错误: {e}")

    return {
        "submission_id": int(submission_id),
        "stored_file_count": len(storage_result.stored_files),
        "dropped_file_count": len(storage_result.dropped_files),
        "has_text_answers": bool(has_text_answers),
        "is_replacement": is_replacement,
    }


# --- 教师作业 API ---
@router.post("/courses/{course_id}/assignments", response_class=JSONResponse)
async def create_assignment(course_id: int, request: Request, user: dict = Depends(get_current_teacher)):
    """V4.0: 在指定课程下创建新作业"""
    data = await request.json()
    created_at = datetime.now().isoformat()
    class_offering_id = data.get('class_offering_id')
    allowed_file_types_json = encode_allowed_file_types_json(_get_allowed_file_types(data))
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
                availability_mode, starts_at, due_at, duration_minutes, auto_close, closed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            )
        )
        new_id = cursor.lastrowid
        if schedule_fields["status"] == "published":
            try:
                create_assignment_published_notifications(conn, new_id)
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
            """SELECT a.*, c.created_by_teacher_id
               FROM assignments a
               JOIN courses c ON a.course_id = c.id
               WHERE a.id = ?""",
            (assignment_id,)
        ).fetchone()
        if not assignment:
            raise HTTPException(404, "作业不存在")
        if assignment['created_by_teacher_id'] != user['id']:
            raise HTTPException(403, "无权修改该作业")
        assignment_dict = dict(assignment)
        assignment_dict = refresh_assignment_runtime_status(conn, assignment_dict)

        previous_status = str(assignment_dict['status'] or '')
        allowed_file_types_json = encode_allowed_file_types_json(_get_allowed_file_types(data, assignment_dict))
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
                availability_mode = ?, starts_at = ?, due_at = ?, duration_minutes = ?, auto_close = ?, closed_at = ?
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
                assignment_id,
            )
        )
        if previous_status != 'published' and schedule_fields["status"] == 'published':
            try:
                create_assignment_published_notifications(conn, assignment_id)
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
            """SELECT a.id, a.course_id, c.created_by_teacher_id
               FROM assignments a
               JOIN courses c ON a.course_id = c.id
               WHERE a.id = ?""",
            (assignment_id,)
        ).fetchone()
        if not assignment:
            raise HTTPException(404, "作业不存在")
        if assignment['created_by_teacher_id'] != user['id']:
            raise HTTPException(403, "无权删除该作业")

        conn.execute("DELETE FROM assignments WHERE id = ?", (assignment_id,))
        conn.commit()
    delete_storage_tree(_build_assignment_storage_dir(assignment['course_id'], assignment_id))
    return {"status": "success", "deleted_assignment_id": assignment_id}


@router.get("/assignments/{assignment_id}/submissions", response_class=JSONResponse)
async def get_submissions_for_assignment(assignment_id: str, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        close_overdue_assignments(conn)
        assignment = _get_assignment_for_teacher(conn, assignment_id, int(user["id"]))

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

        # 获取班级花名册以包含未提交学生
        total_students = 0
        roster = []
        if assignment['class_offering_id']:
            offering = conn.execute("SELECT class_id FROM class_offerings WHERE id = ?",
                                    (assignment['class_offering_id'],)).fetchone()
            if offering:
                students_cursor = conn.execute(
                    "SELECT id, student_id_number, name FROM students WHERE class_id = ? ORDER BY student_id_number",
                    (offering['class_id'],))
                roster = [dict(row) for row in students_cursor]
                total_students = len(roster)

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
        "unsubmitted_count": total_students - len(submitted_entries),
        "graded_count": len(graded_entries),
        "absence_zero_count": len(absence_zero_entries),
        "submitted_count": len([s for s in submitted_entries if s['status'] == 'submitted']),
        "grading_count": len([s for s in submitted_entries if s['status'] == 'grading']),
        "returned_count": len(returned_entries),
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


@router.post("/assignments/{assignment_id}/submissions/zero-unsubmitted", response_class=JSONResponse)
async def zero_unsubmitted_scores(assignment_id: str, user: dict = Depends(get_current_teacher)):
    """为仍未提交的学生创建“缺交记 0”成绩，占位记录不视为正式提交。"""
    with get_db_connection() as conn:
        close_overdue_assignments(conn)
        assignment = _get_assignment_for_teacher(conn, assignment_id, int(user["id"]))
        offering_class_id = assignment.get("offering_class_id")
        if not offering_class_id:
            return {
                "status": "success",
                "updated_count": 0,
                "created_count": 0,
                "skipped_count": 0,
                "message": "当前作业未绑定班级，无法识别未提交学生",
            }

        students = [
            dict(row)
            for row in conn.execute(
                """
                SELECT s.id, s.student_id_number, s.name
                FROM students s
                WHERE s.class_id = ?
                ORDER BY s.student_id_number, s.name
                """,
                (int(offering_class_id),),
            )
        ]
        existing_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id, student_pk_id, status, is_absence_score
                FROM submissions
                WHERE assignment_id = ?
                """,
                (assignment_id,),
            )
        ]
        existing_by_student: dict[int, dict[str, Any]] = {}
        for row in existing_rows:
            student_pk_id = int(row["student_pk_id"])
            current = existing_by_student.get(student_pk_id)
            row_is_absence = int(row.get("is_absence_score") or 0) == 1
            current_is_absence = current is not None and int(current.get("is_absence_score") or 0) == 1
            if current is None or (current_is_absence and not row_is_absence):
                existing_by_student[student_pk_id] = row

        now_iso = datetime.now().replace(microsecond=0).isoformat()
        feedback = "未提交，按缺交记 0 分。"
        created_count = 0
        updated_count = 0
        skipped_count = 0

        for student in students:
            student_pk_id = int(student["id"])
            existing = existing_by_student.get(student_pk_id)
            if existing and str(existing.get("status") or "") != "unsubmitted":
                skipped_count += 1
                continue

            if existing:
                conn.execute(
                    """
                    UPDATE submissions
                    SET student_name = ?,
                        status = 'unsubmitted',
                        score = 0,
                        feedback_md = ?,
                        submitted_by_role = 'teacher',
                        submitted_by_teacher_id = ?,
                        submission_channel = 'absence_zero',
                        resubmission_allowed = 0,
                        resubmission_due_at = NULL,
                        returned_at = NULL,
                        returned_by_teacher_id = NULL,
                        returned_reason = NULL,
                        is_absence_score = 1,
                        absence_scored_at = ?,
                        absence_scored_by_teacher_id = ?
                    WHERE id = ?
                    """,
                    (
                        student.get("name") or "",
                        feedback,
                        int(user["id"]),
                        now_iso,
                        int(user["id"]),
                        int(existing["id"]),
                    ),
                )
                updated_count += 1
                continue

            conn.execute(
                """
                INSERT INTO submissions (
                    assignment_id, student_pk_id, student_name, status, score, feedback_md,
                    answers_json, submitted_by_role, submitted_by_teacher_id, submission_channel,
                    resubmission_allowed, resubmission_due_at, returned_at, returned_by_teacher_id,
                    returned_reason, is_absence_score, absence_scored_at, absence_scored_by_teacher_id,
                    submitted_at
                ) VALUES (?, ?, ?, 'unsubmitted', 0, ?, NULL, 'teacher', ?, 'absence_zero',
                          0, NULL, NULL, NULL, NULL, 1, ?, ?, ?)
                """,
                (
                    assignment_id,
                    student_pk_id,
                    student.get("name") or "",
                    feedback,
                    int(user["id"]),
                    now_iso,
                    int(user["id"]),
                    now_iso,
                ),
            )
            created_count += 1

        conn.commit()

    if assignment.get("class_offering_id") and created_count + updated_count > 0:
        try:
            record_behavior_event(
                class_offering_id=int(assignment["class_offering_id"]),
                user_pk=int(user["id"]),
                user_role="teacher",
                display_name=str(user.get("name") or user["id"]),
                action_type="assignment_zero_unsubmitted",
                session_started_at=str(user.get("login_time") or "").strip() or None,
                summary_text=f"未提交作业记 0：{assignment.get('title') or assignment_id}",
                payload={
                    "assignment_id": assignment_id,
                    "created_count": created_count,
                    "updated_count": updated_count,
                    "skipped_count": skipped_count,
                },
                page_key="assignment_detail",
            )
        except Exception as exc:
            print(f"[BEHAVIOR] 记录未提交记 0 失败: {exc}")

    return {
        "status": "success",
        "updated_count": updated_count + created_count,
        "created_count": created_count,
        "refreshed_count": updated_count,
        "skipped_count": skipped_count,
    }


@router.post("/submissions/{submission_id}/grade", response_class=JSONResponse)
async def grade_submission(submission_id: int, request: Request, user: dict = Depends(get_current_teacher)):
    data = await request.json()
    with get_db_connection() as conn:
        submission = _get_submission_for_teacher(conn, submission_id, int(user["id"]))
        if int(submission.get("resubmission_allowed") or 0):
            raise HTTPException(400, "该提交已撤回并等待重交，不能批改旧版本")
        conn.execute(
            """
            UPDATE submissions
            SET status = 'graded',
                score = ?,
                feedback_md = ?,
                resubmission_allowed = 0,
                resubmission_due_at = NULL,
                returned_at = NULL,
                returned_by_teacher_id = NULL,
                returned_reason = NULL
            WHERE id = ?
            """,
            (data['score'], data['feedback_md'], submission_id),
        )
        try:
            create_student_grading_notification(
                conn,
                submission_id,
                actor_role="teacher",
                actor_user_pk=int(user["id"]),
                actor_display_name=str(user.get("name") or ""),
            )
        except Exception as exc:
            print(f"[MESSAGE_CENTER] manual grading notify failed: {exc}")
        conn.commit()
    return {"status": "success", "graded_submission_id": submission_id}


@router.delete("/submissions/{submission_id}", response_class=JSONResponse)
async def return_submission(submission_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        submission = _get_submission_for_teacher(conn, submission_id, int(user["id"]))
        conn.execute("DELETE FROM submissions WHERE id = ?", (submission_id,))
        conn.commit()
    delete_storage_tree(
        _build_submission_storage_dir(submission['course_id'], submission['assignment_id'], submission['student_pk_id'])
    )
    return {"status": "success", "deleted_submission_id": submission_id}


@router.get("/assignments/{assignment_id}/export-attachments/{class_offering_id}")
async def export_submission_attachments(
    assignment_id: str,
    class_offering_id: int,
    user: dict = Depends(get_current_teacher),
):
    """将指定作业所有已提交学生的附件打包为 zip 下载。
    目录结构: 班级名-作业名/学生姓名-学号/原始附件文件
    """
    with get_db_connection() as conn:
        assignment = _get_assignment_for_teacher(conn, assignment_id, int(user["id"]))

        offering = conn.execute(
            "SELECT * FROM class_offerings WHERE id = ?", (class_offering_id,)
        ).fetchone()
        if not offering:
            raise HTTPException(404, "未找到班级课堂")
        if int(assignment.get("class_offering_id") or 0) != int(class_offering_id):
            raise HTTPException(400, "作业与当前班级课堂不匹配")

        class_id = offering["class_id"]
        class_info = conn.execute(
            "SELECT name FROM classes WHERE id = ?", (class_id,)
        ).fetchone()
        if not class_info:
            raise HTTPException(404, "未找到班级")
        class_name = str(class_info["name"] or "").strip()

        # 获取所有已提交学生的附件记录
        rows = conn.execute(
            """
            SELECT sf.stored_path, sf.relative_path, sf.original_filename,
                   s.student_pk_id, stu.name AS student_name,
                   stu.student_id_number
            FROM submission_files sf
            JOIN submissions s ON s.id = sf.submission_id
            JOIN students stu ON stu.id = s.student_pk_id
            WHERE s.assignment_id = ?
              AND s.student_pk_id IN (SELECT id FROM students WHERE class_id = ?)
              AND s.status != 'unsubmitted'
            ORDER BY stu.student_id_number, sf.relative_path
            """,
            (assignment_id, class_id),
        ).fetchall()

    if not rows:
        raise HTTPException(404, "当前没有已提交附件可供导出")

    # Build zip in memory
    assignment_title = str(assignment.get("title") or "").strip()
    # Sanitize folder names for cross-platform compatibility
    root_folder = _sanitize_zip_path(f"{class_name}-{assignment_title}")
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        # Map student_pk_id -> unique folder name (handles duplicate names)
        student_folder_map: dict[int, str] = {}
        used_folder_names: set[str] = set()

        for row in rows:
            resolved_path = resolve_submission_file_path(str(row["stored_path"] or ""))
            if not resolved_path:
                continue
            stored_path = Path(resolved_path)

            student_pk_id = int(row["student_pk_id"])

            # Resolve folder name once per student
            if student_pk_id not in student_folder_map:
                student_name = str(row["student_name"] or "").strip() or "未知"
                student_id_number = str(row["student_id_number"] or "").strip() or "无学号"
                folder = _sanitize_zip_path(f"{student_name}-{student_id_number}")

                if folder in used_folder_names:
                    base = folder
                    idx = 2
                    while f"{base}_{idx}" in used_folder_names:
                        idx += 1
                    folder = f"{base}_{idx}"
                used_folder_names.add(folder)
                student_folder_map[student_pk_id] = folder

            student_folder = student_folder_map[student_pk_id]

            # Use relative_path to preserve sub-directory structure if any
            relative_path = str(row["relative_path"] or row["original_filename"] or "file")
            arc_path = f"{root_folder}/{student_folder}/{relative_path}"

            zf.write(stored_path, arc_path)

    zip_buffer.seek(0)
    zip_filename = f"{root_folder}.zip"
    encoded_filename = quote(zip_filename)
    return StreamingResponse(
        zip_buffer,
        media_type="application/x-zip-compressed",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"},
    )


def _sanitize_zip_path(name: str) -> str:
    """Remove or replace characters that are unsafe in zip paths / filenames."""
    import re
    # Replace characters unsafe for filenames (Windows + cross-platform)
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    # Collapse consecutive underscores and strip
    sanitized = re.sub(r'_+', '_', sanitized).strip('_ ')
    return sanitized or "export"


@router.get("/assignments/{assignment_id}/export/{class_offering_id}", response_class=FileResponse)
async def export_grades_for_class(assignment_id: str, class_offering_id: int, user: dict = Depends(get_current_teacher)):
    """V4.0: 导出此作业在指定班级课堂的成绩"""
    with get_db_connection() as conn:
        assignment = _get_assignment_for_teacher(conn, assignment_id, int(user["id"]))

        # 通过 class_offering_id 解析出实际的 class_id
        offering = conn.execute("SELECT * FROM class_offerings WHERE id = ?", (class_offering_id,)).fetchone()
        if not offering:
            raise HTTPException(404, "未找到班级课堂")
        if int(assignment.get("class_offering_id") or 0) != int(class_offering_id):
            raise HTTPException(400, "作业与当前班级课堂不匹配")
        class_id = offering['class_id']

        class_info = conn.execute("SELECT * FROM classes WHERE id = ?", (class_id,)).fetchone()
        if not class_info:
            raise HTTPException(404, "未找到班级")

        # 1. 获取班级所有学生
        roster_cursor = conn.execute("SELECT id, student_id_number, name FROM students WHERE class_id = ?", (class_id,))
        roster_df = pd.DataFrame(roster_cursor, columns=['student_pk_id', '学号', '姓名'])

        if roster_df.empty:
            raise HTTPException(404, "此班级没有学生，无法导出。")

        # 2. 获取这个班级学生的此项作业成绩
        grades_cursor = conn.execute(
            """SELECT student_pk_id,
                      student_name,
                      score,
                      CASE
                          WHEN COALESCE(is_absence_score, 0) = 1 THEN '未提交（缺交记0）'
                          ELSE status
                      END AS status,
                      feedback_md
               FROM submissions
               WHERE assignment_id = ?
                 AND student_pk_id IN (SELECT id FROM students WHERE class_id = ?)""",
            (assignment_id, class_id)
        )
        grades_df = pd.DataFrame(grades_cursor, columns=['student_pk_id', '提交姓名', '分数', '状态', '评语'])

    final_df = roster_df.merge(grades_df, on='student_pk_id', how='left')

    export_filename = f"成绩_{class_info['name']}_{assignment['title']}.xlsx"
    # 确保作业目录存在
    assignment_dir = _build_assignment_storage_dir(assignment['course_id'], assignment['id'])
    assignment_dir.mkdir(parents=True, exist_ok=True)
    export_path = assignment_dir / export_filename

    final_df.to_excel(export_path, index=False)
    return FileResponse(export_path, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        filename=export_filename)


# --- 学生作业 API ---
@router.post("/assignments/{assignment_id}/submit", response_class=JSONResponse)
async def submit_assignment(assignment_id: str,
                            answers_json: str = Form(""),
                            manifest: str = Form(""),
                            files: List[UploadFile] = File(default=[]),
                            user: dict = Depends(get_current_student)):
    """
    V4.4: 学生提交作业 — 支持 JSON 格式的答案 + 可选文件附件
    answers_json: 包含所有答题内容的 JSON 字符串
    files: 可选的附件文件列表
    """
    with get_db_connection() as conn:
        close_overdue_assignments(conn)
        conn.commit()
        assignment = conn.execute("SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
        if not assignment:
            raise HTTPException(404, "Assignment not found")
        assignment = enrich_assignment_runtime_view(assignment)

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
            existing_submission=existing_submission,
            notify_teacher=True,
        )

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

    return {
        "status": "success",
        "submission_id": result["submission_id"],
        "stored_file_count": result["stored_file_count"],
        "dropped_file_count": result["dropped_file_count"],
        "is_resubmission": result["is_replacement"],
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

    return {
        "status": "success",
        "submission_id": result["submission_id"],
        "stored_file_count": result["stored_file_count"],
        "dropped_file_count": result["dropped_file_count"],
        "is_replacement": result["is_replacement"],
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
                   a.availability_mode, a.starts_at, a.due_at, a.duration_minutes, a.auto_close
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


# --- 试卷库 API ---
@router.get("/exam-papers", response_class=JSONResponse)
async def list_exam_papers(user: dict = Depends(get_current_teacher)):
    """获取当前教师的所有试卷"""
    with get_db_connection() as conn:
        cursor = conn.execute(
            """SELECT ep.*,
                      (SELECT COUNT(*) FROM assignments WHERE exam_paper_id = ep.id) as assigned_count
               FROM exam_papers ep
               WHERE ep.teacher_id = ?
               ORDER BY ep.updated_at DESC""",
            (user['id'],)
        )
        papers = [dict(row) for row in cursor]
    return {"status": "success", "papers": papers}


@router.put("/exam-papers/{paper_id}/tags", response_class=JSONResponse)
async def update_exam_paper_tags(paper_id: str, request: Request, user: dict = Depends(get_current_teacher)):
    """更新试卷标签"""
    data = await request.json()
    tags = data.get('tags', [])
    if not isinstance(tags, list) or len(tags) > 20:
        raise HTTPException(400, "标签格式不正确")
    for t in tags:
        if not isinstance(t, str) or len(t) == 0 or len(t) > 10:
            raise HTTPException(400, "每个标签长度应为1-10个字符")

    now = datetime.now().isoformat()
    with get_db_connection() as conn:
        existing = conn.execute("SELECT teacher_id FROM exam_papers WHERE id = ?", (paper_id,)).fetchone()
        if not existing:
            raise HTTPException(404, "试卷不存在")
        if existing['teacher_id'] != user['id']:
            raise HTTPException(403, "无权修改此试卷")
        conn.execute(
            "UPDATE exam_papers SET tags_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(tags, ensure_ascii=False), now, paper_id)
        )
        conn.commit()
    return {"status": "success", "tags": tags}


@router.post("/exam-papers", response_class=JSONResponse)
async def create_exam_paper(request: Request, user: dict = Depends(get_current_teacher)):
    """创建新试卷"""
    data = await request.json()
    paper_id = data.get('id') or str(uuid.uuid4())
    now = datetime.now().isoformat()

    with get_db_connection() as conn:
        conn.execute(
            """INSERT INTO exam_papers (id, teacher_id, title, description, questions_json, exam_config_json, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (paper_id, user['id'], data['title'], data.get('description', ''),
             json.dumps(data.get('questions', {"pages": []}), ensure_ascii=False),
             json.dumps(data.get('config', {}), ensure_ascii=False),
             data.get('status', 'draft'), now, now)
        )
        conn.commit()
    return {"status": "success", "paper_id": paper_id}


@router.get("/exam-papers/{paper_id}", response_class=JSONResponse)
async def get_exam_paper(paper_id: str, user: dict = Depends(get_current_user)):
    """获取试卷详情"""
    with get_db_connection() as conn:
        paper = conn.execute(
            "SELECT * FROM exam_papers WHERE id = ? AND teacher_id = ?",
            (paper_id, user['id'])
        ).fetchone()
        if not paper:
            raise HTTPException(404, "试卷不存在")
        result = dict(paper)
        # 获取已分配的课堂列表
        assignments = conn.execute(
            """SELECT a.id, a.status, a.title, o.id as offering_id, c.name as course_name, cl.name as class_name
               FROM assignments a
               LEFT JOIN class_offerings o ON a.class_offering_id = o.id
               LEFT JOIN courses c ON o.course_id = c.id
               LEFT JOIN classes cl ON o.class_id = cl.id
               WHERE a.exam_paper_id = ?""",
            (paper_id,)
        ).fetchall()
        result['assignments'] = [dict(row) for row in assignments]
    return {"status": "success", "paper": result}


@router.put("/exam-papers/{paper_id}", response_class=JSONResponse)
async def update_exam_paper(paper_id: str, request: Request, user: dict = Depends(get_current_teacher)):
    """更新试卷"""
    data = await request.json()
    now = datetime.now().isoformat()

    with get_db_connection() as conn:
        existing = conn.execute("SELECT teacher_id FROM exam_papers WHERE id = ?", (paper_id,)).fetchone()
        if not existing:
            raise HTTPException(404, "试卷不存在")
        if existing['teacher_id'] != user['id']:
            raise HTTPException(403, "无权修改此试卷")

        conn.execute(
            """UPDATE exam_papers
               SET title = ?, description = ?, questions_json = ?, exam_config_json = ?, status = ?, updated_at = ?
               WHERE id = ?""",
            (data['title'], data.get('description', ''),
             json.dumps(data.get('questions', {"pages": []}), ensure_ascii=False),
             json.dumps(data.get('config', {}), ensure_ascii=False),
             data.get('status', 'draft'), now, paper_id)
        )
        conn.commit()
    return {"status": "success", "paper_id": paper_id}


@router.delete("/exam-papers/{paper_id}", response_class=JSONResponse)
async def delete_exam_paper(paper_id: str, user: dict = Depends(get_current_teacher)):
    """删除试卷"""
    with get_db_connection() as conn:
        existing = conn.execute("SELECT teacher_id FROM exam_papers WHERE id = ?", (paper_id,)).fetchone()
        if not existing:
            raise HTTPException(404, "试卷不存在")
        if existing['teacher_id'] != user['id']:
            raise HTTPException(403, "无权删除此试卷")
        # 检查是否已被分配
        assigned = conn.execute("SELECT COUNT(*) FROM assignments WHERE exam_paper_id = ?", (paper_id,)).fetchone()[0]
        if assigned > 0:
            raise HTTPException(400, f"该试卷已被分配给 {assigned} 个作业，请先删除相关作业")
        conn.execute("DELETE FROM exam_papers WHERE id = ?", (paper_id,))
        conn.commit()
    return {"status": "success"}


def _auto_add_class_name_tag(conn, paper_row: sqlite3.Row, class_id: int) -> None:
    """自动将课堂名称添加为试卷标签（去重）。"""
    class_row = conn.execute("SELECT name FROM classes WHERE id = ?", (class_id,)).fetchone()
    if not class_row:
        return
    class_name = class_row["name"].strip()
    if not class_name or len(class_name) > 10:
        return

    try:
        existing_tags = json.loads(paper_row["tags_json"]) if paper_row["tags_json"] else []
    except (json.JSONDecodeError, TypeError):
        existing_tags = []

    if class_name not in existing_tags:
        existing_tags.append(class_name)
        conn.execute(
            "UPDATE exam_papers SET tags_json = ? WHERE id = ?",
            (json.dumps(existing_tags, ensure_ascii=False), paper_row["id"]),
        )


@router.post("/exam-papers/{paper_id}/assign", response_class=JSONResponse)
async def assign_exam_paper(paper_id: str, request: Request, user: dict = Depends(get_current_teacher)):
    """将试卷分配给指定课堂（创建 assignment）"""
    data = await request.json()
    class_offering_id = data.get('class_offering_id')
    if not class_offering_id:
        raise HTTPException(400, "请指定课堂")
    try:
        schedule_fields = build_assignment_schedule_fields(
            data,
            default_status="published",
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    with get_db_connection() as conn:
        close_overdue_assignments(conn)
        paper = conn.execute("SELECT * FROM exam_papers WHERE id = ?", (paper_id,)).fetchone()
        if not paper:
            raise HTTPException(404, "试卷不存在")

        # 获取课堂信息
        offering = conn.execute(
            "SELECT * FROM class_offerings WHERE id = ? AND teacher_id = ?",
            (class_offering_id, user['id'])
        ).fetchone()
        if not offering:
            raise HTTPException(404, "课堂不存在或无权操作")

        # 创建作业记录
        existing_assignment = conn.execute(
            "SELECT id FROM assignments WHERE exam_paper_id = ? AND class_offering_id = ?",
            (paper_id, int(class_offering_id))
        ).fetchone()
        if existing_assignment:
            raise HTTPException(409, "该试卷已添加到当前课堂，请勿重复发布")

        created_at = datetime.now().isoformat()
        allowed_file_types_json = encode_allowed_file_types_json(_get_allowed_file_types(data))
        cursor = conn.execute(
            """
            INSERT INTO assignments (
                course_id, title, status, requirements_md, rubric_md, grading_mode,
                exam_paper_id, class_offering_id, created_at, allowed_file_types_json,
                availability_mode, starts_at, due_at, duration_minutes, auto_close, closed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(offering['course_id']),
                data.get('title', paper['title']),
                schedule_fields["status"],
                f"**试卷**: {paper['title']}\n\n{paper['description'] or ''}",
                "按试卷各题评分",
                'ai',
                paper_id,
                int(class_offering_id),
                created_at,
                allowed_file_types_json,
                schedule_fields["availability_mode"],
                schedule_fields["starts_at"],
                schedule_fields["due_at"],
                schedule_fields["duration_minutes"],
                schedule_fields["auto_close"],
                schedule_fields["closed_at"],
            )
        )
        new_assignment_id = cursor.lastrowid
        if schedule_fields["status"] == "published":
            try:
                create_assignment_published_notifications(conn, new_assignment_id)
            except Exception as exc:
                print(f"[MESSAGE_CENTER] exam assignment publish notify failed: {exc}")

        # 自动将课堂名称添加为试卷标签
        _auto_add_class_name_tag(conn, paper, offering['class_id'])

        conn.commit()
        assignment_dir = _build_assignment_storage_dir(offering['course_id'], new_assignment_id)
        assignment_dir.mkdir(parents=True, exist_ok=True)
    return {
        "status": "success",
        "assignment_id": new_assignment_id,
        "assignment_status": schedule_fields["status"],
        "due_at": schedule_fields["due_at"],
        "message": "试卷已成功发布到当前课堂"
    }
