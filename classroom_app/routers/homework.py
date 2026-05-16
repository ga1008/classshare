import uuid
import json
import io
import asyncio
import zipfile
import shutil
import pandas as pd
import sqlite3
from urllib.parse import quote
from datetime import datetime
from pathlib import Path, PurePosixPath
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
from ..services.late_submission_policy import (
    append_late_policy_feedback,
    apply_late_policy_to_score,
    build_late_submission_snapshot,
    serialize_assignment_time_state,
    utc_like_now,
)
from ..services.exam_json_service import (
    build_exam_rubric_md,
    EXAM_JSON_MAX_BYTES,
    get_exam_json_template_text,
    normalize_exam_scoring_payload,
    parse_exam_json_text,
)
from ..services.submission_assets import (
    answers_have_content,
    decode_allowed_file_types_json,
    delete_storage_tree,
    encode_allowed_file_types_json,
    is_allowed_submission_file,
    normalize_submission_relative_path,
    normalize_allowed_file_types,
    parse_submission_manifest,
    remove_answer_attachment_references,
    reconcile_answer_attachment_references,
    StoredSubmissionFile,
    store_submission_files,
    summarize_allowed_file_types,
)
from ..services.submission_file_alignment import resolve_submission_file_path
from ..services.ai_grading_attachments import (
    build_attachment_type_summary,
    ensure_ai_grading_attachments_supported,
)
from ..services.ai_grading_service import AIGradingQueueError, submit_submission_for_ai_grading
from ..services.learning_progress_service import (
    get_stage_exam_target,
    handle_assignment_stage_grading_complete,
    handle_stage_exam_grading_complete,
    is_personal_stage_exam_assignment,
    is_personal_stage_exam_paper,
    mark_stage_submission_saved,
    normalize_assignment_stage_key,
    personal_stage_assignment_filter_sql,
    student_can_access_assignment,
    submit_stage_exam_for_ai_grading,
)

router = APIRouter(prefix="/api")

# 并发控制：限制同时进行的 AI 批改提交数量，避免大量学生同时提交时
# 创建过多 SQLite 连接导致 WAL 锁争用。
_ai_grading_submit_semaphore = asyncio.Semaphore(10)

PERSONAL_STAGE_TEACHER_HIDDEN_MESSAGE = "学生个人试炼属于学生资产，不在教师作业与考试中展示；请查看班级修行统计。"


def _build_assignment_storage_dir(course_id: int, assignment_id: int | str):
    return HOMEWORK_SUBMISSIONS_DIR / str(course_id) / str(assignment_id)


def _build_submission_storage_dir(course_id: int, assignment_id: int | str, student_pk_id: int | str):
    return _build_assignment_storage_dir(course_id, assignment_id) / str(student_pk_id)


def _build_submission_draft_storage_dir(course_id: int, assignment_id: int | str, student_pk_id: int | str):
    return _build_assignment_storage_dir(course_id, assignment_id) / "__drafts__" / str(student_pk_id)


def _build_submission_file_path(submission_dir: Path, relative_path: str) -> Path:
    return submission_dir.joinpath(*PurePosixPath(relative_path).parts)


def _get_allowed_file_types(data: dict, assignment_row=None) -> list[str]:
    if "allowed_file_types" in data:
        return normalize_allowed_file_types(data.get("allowed_file_types"))
    if "allowed_file_types_json" in data:
        return decode_allowed_file_types_json(data.get("allowed_file_types_json"))
    if assignment_row is not None:
        return decode_allowed_file_types_json(assignment_row["allowed_file_types_json"])
    return []


def _get_learning_stage_key(data: dict, *, class_offering_id: Any = None) -> str | None:
    raw_stage_key = data.get("learning_stage_key", data.get("stage_key"))
    try:
        stage_key = normalize_assignment_stage_key(raw_stage_key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if stage_key and not class_offering_id:
        raise HTTPException(400, "仅课堂内作业或考试可以设定为阶段试炼")
    return stage_key


def _ensure_accepting_submission(assignment: dict[str, Any]) -> None:
    if assignment_accepts_submissions(assignment):
        return
    status = str(assignment.get("status") or "").strip().lower()
    # 倒计时模式：实时检查 due_at 是否已过期，避免前端倒计时结束后仍可 API 提交
    if status == "published":
        due_at_raw = assignment.get("due_at")
        if due_at_raw:
            try:
                from datetime import datetime as _dt
                try:
                    due_dt = _dt.fromisoformat(str(due_at_raw).replace(" ", "T"))
                except (TypeError, ValueError):
                    due_dt = None
                if due_dt is not None and due_dt <= _dt.now().replace(microsecond=0):
                    raise HTTPException(400, "作业已截止，当前只能查看，不能作答或提交")
            except HTTPException:
                raise
            except Exception:
                pass
    if status == "new":
        raise HTTPException(400, "作业尚未开始，当前不可作答或提交")
    raise HTTPException(400, "作业已截止，当前只能查看，不能作答或提交")


def _teacher_can_access_assignment(assignment: dict[str, Any], teacher_id: int) -> bool:
    teacher_id = int(teacher_id)
    owner_id = int(assignment.get("created_by_teacher_id") or 0)
    offering_teacher_id = int(assignment.get("offering_teacher_id") or 0)
    return teacher_id in {owner_id, offering_teacher_id}


def _hide_personal_stage_asset() -> None:
    raise HTTPException(404, PERSONAL_STAGE_TEACHER_HIDDEN_MESSAGE)


def _get_exam_paper_for_teacher(conn, paper_id: str, teacher_id: int) -> dict[str, Any]:
    paper = conn.execute("SELECT * FROM exam_papers WHERE id = ?", (paper_id,)).fetchone()
    if not paper:
        raise HTTPException(404, "试卷不存在")
    paper_dict = dict(paper)
    if int(paper_dict.get("teacher_id") or 0) != int(teacher_id):
        raise HTTPException(403, "无权操作此试卷")
    if is_personal_stage_exam_paper(conn, paper_id):
        _hide_personal_stage_asset()
    return paper_dict


def _get_assignment_for_teacher(conn, assignment_id: str, teacher_id: int) -> dict[str, Any]:
    assignment = conn.execute(
        """
        SELECT a.*,
               c.created_by_teacher_id,
               o.teacher_id AS offering_teacher_id,
               o.class_id AS offering_class_id,
               lsea.id AS personal_stage_attempt_id
        FROM assignments a
        JOIN courses c ON c.id = a.course_id
        LEFT JOIN class_offerings o ON o.id = a.class_offering_id
        LEFT JOIN learning_stage_exam_attempts lsea ON lsea.assignment_id = a.id
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
    if assignment_dict.get("personal_stage_attempt_id") is not None:
        _hide_personal_stage_asset()
    return assignment_dict


def _get_submission_for_teacher(conn, submission_id: int, teacher_id: int) -> dict[str, Any]:
    submission = conn.execute(
        """
        SELECT s.*,
               a.course_id,
               a.class_offering_id,
               a.allowed_file_types_json,
               a.due_at AS assignment_due_at,
               a.late_submission_enabled AS assignment_late_submission_enabled,
               a.late_submission_until AS assignment_late_submission_until,
               a.late_penalty_strategy AS assignment_late_penalty_strategy,
               a.late_penalty_interval_hours AS assignment_late_penalty_interval_hours,
               a.late_penalty_points AS assignment_late_penalty_points,
               a.late_penalty_min_score AS assignment_late_penalty_min_score,
               a.late_score_cap AS assignment_late_score_cap,
               a.title AS assignment_title,
               c.created_by_teacher_id,
               o.teacher_id AS offering_teacher_id,
               lsea.id AS personal_stage_attempt_id
        FROM submissions s
        JOIN assignments a ON a.id = s.assignment_id
        JOIN courses c ON c.id = a.course_id
        LEFT JOIN class_offerings o ON o.id = a.class_offering_id
        LEFT JOIN learning_stage_exam_attempts lsea ON lsea.assignment_id = a.id
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
    if submission_dict.get("personal_stage_attempt_id") is not None:
        _hide_personal_stage_asset()
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
        """
        SELECT id, student_id_number, name, class_id,
               COALESCE(enrollment_status, 'active') AS enrollment_status
        FROM students
        WHERE id = ?
        LIMIT 1
        """,
        (student_pk_id,),
    ).fetchone()
    if not student:
        raise HTTPException(404, "学生不存在")
    student_dict = dict(student)
    if str(student_dict.get("enrollment_status") or "active").strip().lower() != "active":
        raise HTTPException(400, "该学生已休学，不需要完成当前课堂任务")
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


def _deduplicate_relative_path_against_seen(relative_path: str, seen_paths: set[str]) -> str:
    normalized_key = relative_path.lower()
    if normalized_key not in seen_paths:
        seen_paths.add(normalized_key)
        return relative_path

    path_obj = PurePosixPath(relative_path)
    parent = "" if str(path_obj.parent) == "." else str(path_obj.parent)
    suffix = "".join(path_obj.suffixes)
    stem = path_obj.name[: -len(suffix)] if suffix else path_obj.name
    for index in range(2, 10000):
        candidate_name = f"{stem} ({index}){suffix}"
        candidate_path = f"{parent}/{candidate_name}" if parent else candidate_name
        candidate_key = candidate_path.lower()
        if candidate_key in seen_paths:
            continue
        seen_paths.add(candidate_key)
        return candidate_path
    raise HTTPException(400, "Too many duplicated upload paths")


def _deduplicate_upload_entries_against_existing(prepared_entries, existing_files: list[dict[str, Any]]) -> None:
    seen_paths = {
        str(row.get("relative_path") or row.get("original_filename") or "").replace("\\", "/").strip().lower()
        for row in existing_files
        if str(row.get("relative_path") or row.get("original_filename") or "").strip()
    }
    for entry in prepared_entries:
        entry.relative_path = _deduplicate_relative_path_against_seen(entry.relative_path, seen_paths)


def _stored_file_to_dict(file_info: StoredSubmissionFile) -> dict[str, Any]:
    return {
        "original_filename": file_info.original_filename,
        "relative_path": file_info.relative_path,
        "stored_path": file_info.stored_path,
        "mime_type": file_info.mime_type,
        "file_size": file_info.file_size,
        "file_ext": file_info.file_ext,
        "file_hash": file_info.file_hash,
    }


def _parse_json_list(raw_value: str, *, field_name: str) -> list[Any]:
    if not raw_value:
        return []
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"{field_name} 不是有效 JSON") from exc
    if not isinstance(parsed, list):
        raise HTTPException(400, f"{field_name} 必须是数组")
    return parsed


def _load_submission_draft(conn, assignment_id: str, student_pk_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT *
        FROM submission_drafts
        WHERE assignment_id = ? AND student_pk_id = ?
        LIMIT 1
        """,
        (assignment_id, int(student_pk_id)),
    ).fetchone()
    return dict(row) if row else None


def _ensure_submission_draft(
    conn,
    *,
    assignment_id: str,
    student_pk_id: int,
    answers_json: str,
    current_page: int,
    client_updated_at: str,
) -> dict[str, Any]:
    now = datetime.now().isoformat()
    current_page = max(0, int(current_page or 0))
    existing = _load_submission_draft(conn, assignment_id, student_pk_id)
    if existing:
        conn.execute(
            """
            UPDATE submission_drafts
            SET answers_json = ?,
                current_page = ?,
                client_updated_at = ?,
                server_updated_at = ?,
                server_version = COALESCE(server_version, 0) + 1,
                status = 'active'
            WHERE id = ?
            """,
            (answers_json, current_page, client_updated_at, now, int(existing["id"])),
        )
        return {
            **existing,
            "answers_json": answers_json,
            "current_page": current_page,
            "client_updated_at": client_updated_at,
            "server_updated_at": now,
            "server_version": int(existing.get("server_version") or 0) + 1,
        }

    cursor = conn.execute(
        """
        INSERT INTO submission_drafts (
            assignment_id, student_pk_id, answers_json, current_page,
            client_updated_at, server_updated_at, server_version, status
        ) VALUES (?, ?, ?, ?, ?, ?, 1, 'active')
        """,
        (assignment_id, int(student_pk_id), answers_json, current_page, client_updated_at, now),
    )
    return {
        "id": int(cursor.lastrowid),
        "assignment_id": assignment_id,
        "student_pk_id": int(student_pk_id),
        "answers_json": answers_json,
        "current_page": current_page,
        "client_updated_at": client_updated_at,
        "server_updated_at": now,
        "server_version": 1,
        "status": "active",
    }


def _load_submission_draft_files(conn, draft_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM submission_draft_files
        WHERE draft_id = ?
        ORDER BY question_id, relative_path, id
        """,
        (int(draft_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def _serialize_draft_file(row: dict[str, Any], assignment_id: str) -> dict[str, Any]:
    file_id = int(row["id"])
    mime_type = str(row.get("mime_type") or "")
    download_url = f"/api/assignments/{assignment_id}/draft-files/{file_id}"
    return {
        "id": file_id,
        "question_id": str(row.get("question_id") or ""),
        "kind": str(row.get("kind") or "file"),
        "file_name": row.get("original_filename") or PurePosixPath(str(row.get("relative_path") or "")).name,
        "original_filename": row.get("original_filename") or "",
        "relative_path": row.get("relative_path") or "",
        "mime_type": mime_type,
        "file_size": row.get("file_size"),
        "file_ext": row.get("file_ext") or "",
        "file_hash": row.get("file_hash") or "",
        "download_url": download_url,
        "raw_url": download_url if mime_type.startswith("image/") else "",
        "is_image": mime_type.startswith("image/"),
        "server_draft": True,
    }


def _serialize_submission_draft(conn, draft: dict[str, Any] | None, assignment_id: str) -> dict[str, Any]:
    if not draft:
        return {
            "exists": False,
            "answers_json": "",
            "current_page": 0,
            "client_updated_at": "",
            "server_updated_at": "",
            "server_version": 0,
            "files": [],
            "files_by_question": {},
        }
    files = [_serialize_draft_file(row, assignment_id) for row in _load_submission_draft_files(conn, int(draft["id"]))]
    files_by_question: dict[str, list[dict[str, Any]]] = {}
    for item in files:
        qid = str(item.get("question_id") or "")
        files_by_question.setdefault(qid, []).append(item)
    return {
        "exists": True,
        "answers_json": draft.get("answers_json") or "",
        "current_page": int(draft.get("current_page") or 0),
        "client_updated_at": draft.get("client_updated_at") or "",
        "server_updated_at": draft.get("server_updated_at") or "",
        "server_version": int(draft.get("server_version") or 0),
        "files": files,
        "files_by_question": files_by_question,
    }


def _delete_draft_file_rows_for_questions(
    conn,
    *,
    draft_id: int,
    question_ids: set[str],
) -> list[str]:
    if not question_ids:
        return []
    placeholders = ",".join("?" for _ in question_ids)
    params = [int(draft_id), *sorted(question_ids)]
    rows = conn.execute(
        f"""
        SELECT stored_path
        FROM submission_draft_files
        WHERE draft_id = ? AND question_id IN ({placeholders})
        """,
        params,
    ).fetchall()
    conn.execute(
        f"""
        DELETE FROM submission_draft_files
        WHERE draft_id = ? AND question_id IN ({placeholders})
        """,
        params,
    )
    return [str(row["stored_path"] or "") for row in rows if str(row["stored_path"] or "").strip()]


def _move_stored_files_to_final_dir(
    stored_files: list[StoredSubmissionFile],
    *,
    staging_dir: Path,
    final_dir: Path,
    backup_dir: Path | None = None,
) -> list[tuple[Path, Path | None]]:
    moved_files: list[tuple[Path, Path | None]] = []
    for file_info in stored_files:
        source = Path(file_info.stored_path)
        if not source.exists():
            source = _build_submission_file_path(staging_dir, file_info.relative_path)
        target = _build_submission_file_path(final_dir, file_info.relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        backup_path = None
        if target.exists():
            if backup_dir:
                backup_path = _build_submission_file_path(backup_dir, file_info.relative_path)
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                target.replace(backup_path)
            else:
                target.unlink()
        source.replace(target)
        file_info.stored_path = str(target)
        moved_files.append((target, backup_path))
    return moved_files


def _restore_moved_draft_files(moved_files: list[tuple[Path, Path | None]]) -> None:
    for target, backup_path in reversed(moved_files):
        try:
            if target.exists() and target.is_file():
                target.unlink()
            if backup_path and backup_path.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                backup_path.replace(target)
        except Exception as exc:
            print(f"[SUBMISSION_DRAFT] failed to restore draft file {target}: {exc}")


def _validate_combined_stored_file_limits(stored_files: list[StoredSubmissionFile]) -> None:
    if len(stored_files) > MAX_SUBMISSION_FILE_COUNT:
        raise HTTPException(413, f"文件数量不能超过 {MAX_SUBMISSION_FILE_COUNT} 个")
    total_size = sum(int(file_info.file_size or 0) for file_info in stored_files)
    if total_size > MAX_SUBMISSION_TOTAL_BYTES:
        raise HTTPException(
            413,
            f"总文件大小超过限制 {MAX_SUBMISSION_TOTAL_MB:.0f}MB"
            f"（当前 {total_size / 1024 / 1024:.1f}MB）",
        )


def _copy_submission_draft_files_to_staging(
    conn,
    *,
    assignment: dict[str, Any],
    student_pk_id: int,
    staging_dir: Path,
    submission_dir: Path,
    existing_relative_paths: set[str],
    allowed_file_types: list[str],
) -> tuple[list[StoredSubmissionFile], int]:
    draft = _load_submission_draft(conn, str(assignment["id"]), int(student_pk_id))
    if not draft:
        return [], 0
    copied_files: list[StoredSubmissionFile] = []
    dropped_count = 0
    for row in _load_submission_draft_files(conn, int(draft["id"])):
        relative_path = str(row.get("relative_path") or "").replace("\\", "/").strip()
        if not relative_path:
            dropped_count += 1
            continue
        path_key = relative_path.lower()
        if path_key in existing_relative_paths:
            continue
        mime_type = str(row.get("mime_type") or "")
        if not is_allowed_submission_file(relative_path, mime_type, allowed_file_types):
            dropped_count += 1
            continue
        source_path = resolve_submission_file_path(str(row.get("stored_path") or "")) or Path(str(row.get("stored_path") or ""))
        source_path = Path(source_path)
        if not source_path.exists() or not source_path.is_file():
            dropped_count += 1
            continue
        target = _build_submission_file_path(staging_dir, relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target)
        existing_relative_paths.add(path_key)
        copied_files.append(
            StoredSubmissionFile(
                original_filename=str(row.get("original_filename") or PurePosixPath(relative_path).name),
                relative_path=relative_path,
                stored_path=str(_build_submission_file_path(submission_dir, relative_path)),
                mime_type=mime_type,
                file_size=int(row.get("file_size") or target.stat().st_size),
                file_ext=str(row.get("file_ext") or Path(relative_path).suffix.lower()),
                file_hash=str(row.get("file_hash") or ""),
            )
        )
    return copied_files, dropped_count


def _ensure_student_can_save_assignment_draft(
    conn,
    *,
    assignment: dict[str, Any],
    student_id: int,
) -> dict[str, Any] | None:
    if not student_can_access_assignment(conn, str(assignment["id"]), int(student_id)):
        raise HTTPException(403, "该破境试炼只对指定学生开放")
    submission = conn.execute(
        "SELECT * FROM submissions WHERE assignment_id = ? AND student_pk_id = ? LIMIT 1",
        (assignment["id"], int(student_id)),
    ).fetchone()
    existing_submission = dict(submission) if submission else None
    if existing_submission:
        if int(existing_submission.get("is_absence_score") or 0):
            _ensure_accepting_submission(assignment)
            return existing_submission
        if submission_resubmission_accepts(existing_submission):
            return existing_submission
        raise HTTPException(409, "您已经提交过此作业，当前不能继续保存草稿")
    _ensure_accepting_submission(assignment)
    return None


def _assignment_uses_ai_grading(assignment: dict[str, Any]) -> bool:
    return str(assignment.get("grading_mode") or "").strip().lower() in {"ai", "auto", "mixed"}


def _resolve_grading_status(assignment: dict[str, Any], auto_scheduled: bool) -> str:
    """推断批改状态摘要，用于提交响应中通知学生批改进度。"""
    if auto_scheduled:
        return "queued"
    if _assignment_uses_ai_grading(assignment):
        return "failed"
    return "manual"


def _ensure_submission_files_manageable(submission: dict[str, Any]) -> None:
    if int(submission.get("is_absence_score") or 0):
        raise HTTPException(400, "缺交记 0 记录没有可管理的学生附件")
    if str(submission.get("status") or "").lower() == "grading":
        raise HTTPException(409, "该提交正在 AI 批改中，不能修改附件")
    if str(submission.get("status") or "").lower() == "graded" and not int(submission.get("resubmission_allowed") or 0):
        raise HTTPException(409, "该提交已批改成功，请先撤回后再修改附件")


def _reset_submission_after_attachment_edit(conn, submission_id: int, teacher_id: int) -> None:
    conn.execute(
        """
        UPDATE submissions
        SET status = 'submitted',
            score = NULL,
            feedback_md = NULL,
            grading_started_at = NULL,
            grading_attempt_fingerprint = NULL,
            resubmission_allowed = 0,
            resubmission_due_at = NULL,
            returned_at = NULL,
            returned_by_teacher_id = NULL,
            returned_reason = NULL,
            submitted_by_role = COALESCE(submitted_by_role, 'teacher'),
            submitted_by_teacher_id = COALESCE(submitted_by_teacher_id, ?),
            submission_channel = COALESCE(submission_channel, 'offline'),
            is_absence_score = 0,
            absence_scored_at = NULL,
            absence_scored_by_teacher_id = NULL
        WHERE id = ?
        """,
        (int(teacher_id), int(submission_id)),
    )


async def _submit_ai_grading_background(submission_id: int, *, reason: str = "auto") -> None:
    try:
        async with _ai_grading_submit_semaphore:
            await submit_submission_for_ai_grading(int(submission_id), allow_graded=False)
    except AIGradingQueueError as exc:
        print(f"[AI_GRADING] {reason} submit skipped for submission {submission_id}: {exc.detail}")
    except Exception as exc:
        print(f"[AI_GRADING] {reason} submit failed for submission {submission_id}: {exc}")


def _schedule_ai_grading(submission_id: int, *, reason: str = "auto") -> bool:
    task_coro = _submit_ai_grading_background(int(submission_id), reason=reason)
    try:
        asyncio.create_task(task_coro)
        return True
    except RuntimeError:
        task_coro.close()
        return False


def _form_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_client_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed.replace(microsecond=0)


def _normalize_submission_started_at(value: Any, submitted_at: str) -> str:
    submitted_dt = _parse_client_datetime(submitted_at) or datetime.now().replace(microsecond=0)
    started_dt = _parse_client_datetime(value)
    if started_dt is None:
        return submitted_dt.isoformat()
    if started_dt > submitted_dt:
        return submitted_dt.isoformat()
    if (submitted_dt - started_dt).total_seconds() > 7 * 24 * 60 * 60:
        return submitted_dt.isoformat()
    return started_dt.isoformat()


def _parse_answers_payload(answers_json: str) -> Any:
    try:
        answers_data = json.loads(answers_json) if answers_json else {}
    except json.JSONDecodeError:
        answers_data = {"raw_text": answers_json}
    return answers_data.get("answers", answers_data) if isinstance(answers_data, dict) else answers_data


def _load_exam_attachment_policies(conn, assignment: dict[str, Any]) -> dict[str, dict[str, Any]]:
    paper_id = assignment.get("exam_paper_id")
    if not paper_id:
        return {}
    paper = conn.execute("SELECT questions_json FROM exam_papers WHERE id = ?", (paper_id,)).fetchone()
    if not paper or not paper["questions_json"]:
        return {}
    try:
        paper_data = json.loads(paper["questions_json"])
    except (TypeError, json.JSONDecodeError):
        return {}

    policies: dict[str, dict[str, Any]] = {}
    for page in (paper_data.get("pages", []) if isinstance(paper_data, dict) else []):
        if not isinstance(page, dict):
            continue
        for question in page.get("questions", []) or []:
            if not isinstance(question, dict):
                continue
            qid = str(question.get("id") or "").strip()
            raw_policy = question.get("attachment_requirements")
            if not qid or not isinstance(raw_policy, dict):
                continue
            enabled = raw_policy.get("enabled")
            raw_required = bool(raw_policy.get("required") or raw_policy.get("requires_attachment"))
            try:
                min_count = int(raw_policy.get("min_count") or raw_policy.get("min") or (1 if raw_required else 0))
            except (TypeError, ValueError):
                min_count = 1 if raw_required else 0
            enabled_false = enabled is not None and not _form_bool(enabled)
            required = bool(raw_required or (min_count > 0 and not enabled_false))
            if enabled_false and not raw_required:
                continue
            if required and min_count < 1:
                min_count = 1
            try:
                max_count_raw = raw_policy.get("max_count", raw_policy.get("max"))
                max_count = int(max_count_raw) if max_count_raw not in (None, "") else None
            except (TypeError, ValueError):
                max_count = None
            if max_count is not None and max_count < min_count:
                max_count = min_count
            allowed_file_types = normalize_allowed_file_types(raw_policy.get("allowed_file_types") or raw_policy.get("file_types") or [])
            policies[qid] = {
                "required": required,
                "min_count": max(0, min_count),
                "max_count": max_count if max_count and max_count > 0 else None,
                "allowed_file_types": allowed_file_types,
                "title": str(question.get("text") or question.get("title") or "").strip(),
            }
    return policies


def _iter_answer_items(answers_payload: Any) -> list[dict[str, Any]]:
    if isinstance(answers_payload, list):
        return [item for item in answers_payload if isinstance(item, dict)]
    if isinstance(answers_payload, dict):
        items = []
        for key, value in answers_payload.items():
            if isinstance(value, dict):
                items.append({"question_id": key, **value})
            else:
                items.append({"question_id": key, "answer": value})
        return items
    return []


def _dedupe_answer_attachments(attachments: Any) -> list[dict[str, Any]]:
    if not isinstance(attachments, list):
        return []
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        identity = str(
            attachment.get("relative_path")
            or attachment.get("stored_relative_path")
            or attachment.get("file_name")
            or attachment.get("filename")
            or ""
        ).replace("\\", "/").strip().lower()
        kind = str(attachment.get("kind") or attachment.get("type") or "file").strip().lower()
        if identity:
            key = (kind, identity)
        else:
            key = ("json", json.dumps(attachment, ensure_ascii=False, sort_keys=True, default=str))
        if key in seen:
            continue
        seen.add(key)
        unique.append(attachment)
    return unique


def _validate_exam_answer_attachment_policies(answers_payload: Any, policies: dict[str, dict[str, Any]]) -> None:
    if not policies:
        return
    answer_items = _iter_answer_items(answers_payload)
    attachments_by_question: dict[str, list[dict[str, Any]]] = {}
    for index, item in enumerate(answer_items, start=1):
        qid = str(item.get("question_id") or item.get("question_no") or index)
        attachments_by_question[qid] = _dedupe_answer_attachments(item.get("attachments"))

    for qid, policy in policies.items():
        attachments = attachments_by_question.get(qid, [])
        min_count = int(policy.get("min_count") or (1 if policy.get("required") else 0))
        if policy.get("required") and min_count < 1:
            min_count = 1
        if min_count and len(attachments) < min_count:
            title = str(policy.get("title") or "").strip()
            title_hint = f"（{title[:24]}）" if title else ""
            raise HTTPException(400, f"第 {qid} 题{title_hint}需要至少上传 {min_count} 个附件")
        max_count = policy.get("max_count")
        if max_count and len(attachments) > int(max_count):
            raise HTTPException(400, f"第 {qid} 题附件不能超过 {max_count} 个")
        allowed_file_types = policy.get("allowed_file_types") or []
        if allowed_file_types:
            for attachment in attachments:
                relative_path = str(attachment.get("relative_path") or attachment.get("stored_relative_path") or attachment.get("file_name") or "")
                content_type = str(attachment.get("mime_type") or attachment.get("content_type") or "")
                if not is_allowed_submission_file(relative_path, content_type, allowed_file_types):
                    expected = summarize_allowed_file_types(allowed_file_types)
                    raise HTTPException(400, f"第 {qid} 题附件类型不符合要求，允许类型: {expected}")


def _restore_submission_dir(submission_dir, backup_dir, *, remove_current: bool = True) -> None:
    if remove_current:
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
    started_at: str = "",
    existing_submission: dict[str, Any] | None = None,
    notify_teacher: bool = False,
    use_server_draft_files: bool = False,
) -> dict[str, Any]:
    prepared_entries = _validate_upload_entries(files, manifest)
    submitted_at = datetime.now().isoformat()
    late_snapshot = build_late_submission_snapshot(assignment, submitted_at)
    is_late_submission = 1 if late_snapshot.get("is_late_submission") else 0
    late_by_seconds = int(late_snapshot.get("late_by_seconds") or 0)
    late_snapshot_json = json.dumps(late_snapshot, ensure_ascii=False) if is_late_submission else None
    started_at_normalized = _normalize_submission_started_at(started_at, submitted_at)
    answers_payload = _parse_answers_payload(answers_json)
    has_answer_content_before_storage = answers_have_content(answers_payload)
    allowed_file_types = decode_allowed_file_types_json(assignment.get("allowed_file_types_json"))
    student_pk_id = int(student["id"])
    draft = _load_submission_draft(conn, str(assignment["id"]), student_pk_id) if use_server_draft_files else None
    has_server_draft_files = bool(draft and _load_submission_draft_files(conn, int(draft["id"])))
    has_allowed_uploads = any(
        is_allowed_submission_file(entry.relative_path, entry.content_type, allowed_file_types)
        for entry in prepared_entries
    )

    if not has_answer_content_before_storage and not prepared_entries and not has_server_draft_files:
        raise HTTPException(400, "请至少填写答案或上传一个文件")
    if not has_answer_content_before_storage and not has_allowed_uploads and not has_server_draft_files:
        expected_types = summarize_allowed_file_types(allowed_file_types)
        raise HTTPException(400, f"没有符合要求的文件可提交，允许类型: {expected_types}")

    submission_dir = _build_submission_storage_dir(assignment["course_id"], assignment["id"], student_pk_id)
    staging_dir = submission_dir.with_name(f"{submission_dir.name}.__staging__{uuid.uuid4().hex}")
    backup_dir = None
    staging_moved_to_final = False
    is_replacement = bool(existing_submission)

    try:
        storage_result = await store_submission_files(staging_dir, prepared_entries, allowed_file_types)
        if not storage_result.stored_files and not has_answer_content_before_storage:
            if not has_server_draft_files:
                expected_types = summarize_allowed_file_types(allowed_file_types)
                raise HTTPException(400, f"没有符合要求的文件可提交，允许类型: {expected_types}")
        for file_info in storage_result.stored_files:
            file_info.stored_path = str(_build_submission_file_path(submission_dir, file_info.relative_path))
        if use_server_draft_files:
            existing_paths = {file_info.relative_path.lower() for file_info in storage_result.stored_files}
            draft_files, draft_dropped_count = _copy_submission_draft_files_to_staging(
                conn,
                assignment=assignment,
                student_pk_id=student_pk_id,
                staging_dir=staging_dir,
                submission_dir=submission_dir,
                existing_relative_paths=existing_paths,
                allowed_file_types=allowed_file_types,
            )
            storage_result.stored_files.extend(draft_files)
            storage_result.dropped_files.extend(
                {"relative_path": "server_draft", "reason": "draft_file_unavailable_or_filtered"}
                for _ in range(draft_dropped_count)
            )
        _validate_combined_stored_file_limits(storage_result.stored_files)
    except Exception:
        delete_storage_tree(staging_dir)
        raise

    answers_payload = reconcile_answer_attachment_references(answers_payload, storage_result.stored_files)
    try:
        _validate_exam_answer_attachment_policies(
            answers_payload,
            _load_exam_attachment_policies(conn, assignment),
        )
    except Exception:
        delete_storage_tree(staging_dir)
        raise
    has_answer_content = answers_have_content(answers_payload)
    if not has_answer_content and not storage_result.stored_files:
        expected_types = summarize_allowed_file_types(allowed_file_types)
        delete_storage_tree(staging_dir)
        raise HTTPException(400, f"没有符合要求的作答内容可提交，允许文件类型: {expected_types}")

    full_submission = {
        "student_id": student.get("student_id_number", ""),
        "student_name": student.get("name", ""),
        "student_pk_id": student_pk_id,
        "started_at": started_at_normalized,
        "submitted_at": submitted_at,
        "assignment_id": assignment["id"],
        "course_id": assignment["course_id"],
        "answers": answers_payload,
        "submitted_by_role": actor_role,
        "submitted_by_teacher_id": actor_user_pk if actor_role == "teacher" else None,
        "submission_channel": channel,
    }
    full_submission_json = json.dumps(full_submission, ensure_ascii=False)

    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.cursor()
        current_submission = cursor.execute(
            "SELECT id FROM submissions WHERE assignment_id = ? AND student_pk_id = ? LIMIT 1",
            (assignment["id"], student_pk_id),
        ).fetchone()
        if not existing_submission and current_submission:
            raise sqlite3.IntegrityError("duplicate submission")
        if submission_dir.exists():
            backup_dir = submission_dir.with_name(f"{submission_dir.name}.__backup__{uuid.uuid4().hex}")
            submission_dir.rename(backup_dir)
        if storage_result.stored_files:
            staging_dir.rename(submission_dir)
            staging_moved_to_final = True
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
                    grading_started_at = NULL,
                    grading_attempt_fingerprint = NULL,
                    answers_json = ?,
                    submitted_at = ?,
                    started_at = ?,
                    is_late_submission = ?,
                    late_by_seconds = ?,
                    late_policy_snapshot_json = ?,
                    score_before_late_penalty = NULL,
                    late_penalty_points = 0,
                    late_score_cap_applied = 0,
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
                    started_at_normalized,
                    is_late_submission,
                    late_by_seconds,
                    late_snapshot_json,
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
                    assignment_id, student_pk_id, student_name, status, submitted_at, started_at, answers_json,
                    is_late_submission, late_by_seconds, late_policy_snapshot_json,
                    submitted_by_role, submitted_by_teacher_id, submission_channel,
                    resubmission_allowed, resubmission_due_at, returned_at, returned_by_teacher_id, returned_reason,
                    is_absence_score, absence_scored_at, absence_scored_by_teacher_id
                ) VALUES (?, ?, ?, 'submitted', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, NULL, NULL, NULL, 0, NULL, NULL)
                """,
                (
                    assignment["id"],
                    student_pk_id,
                    student.get("name", ""),
                    submitted_at,
                    started_at_normalized,
                    full_submission_json,
                    is_late_submission,
                    late_by_seconds,
                    late_snapshot_json,
                    actor_role,
                    actor_user_pk if actor_role == "teacher" else None,
                    channel,
                ),
            )
            submission_id = cursor.lastrowid

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
        if use_server_draft_files:
            cursor.execute(
                "DELETE FROM submission_drafts WHERE assignment_id = ? AND student_pk_id = ?",
                (assignment["id"], student_pk_id),
            )
        conn.commit()
        if backup_dir:
            delete_storage_tree(backup_dir)
        if use_server_draft_files:
            delete_storage_tree(_build_submission_draft_storage_dir(assignment["course_id"], assignment["id"], student_pk_id))
    except sqlite3.IntegrityError:
        conn.rollback()
        _restore_submission_dir(
            submission_dir,
            backup_dir,
            remove_current=staging_moved_to_final or backup_dir is not None,
        )
        delete_storage_tree(staging_dir)
        raise HTTPException(400, "该学生已经提交过此作业")
    except HTTPException:
        conn.rollback()
        _restore_submission_dir(
            submission_dir,
            backup_dir,
            remove_current=staging_moved_to_final or backup_dir is not None,
        )
        delete_storage_tree(staging_dir)
        raise
    except Exception as e:
        conn.rollback()
        _restore_submission_dir(
            submission_dir,
            backup_dir,
            remove_current=staging_moved_to_final or backup_dir is not None,
        )
        delete_storage_tree(staging_dir)
        print(f"[ERROR] Submission failed: {e}")
        raise HTTPException(500, f"数据库错误: {e}")

    return {
        "submission_id": int(submission_id),
        "stored_file_count": len(storage_result.stored_files),
        "dropped_file_count": len(storage_result.dropped_files),
        "has_text_answers": bool(has_answer_content),
        "is_replacement": is_replacement,
        "is_late_submission": bool(is_late_submission),
        "late_by_seconds": late_by_seconds,
    }


# --- 教师作业 API ---
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
                if not _teacher_can_access_assignment(item, int(user["id"])):
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
        submission_file_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT sf.submission_id,
                       sf.original_filename,
                       sf.relative_path,
                       sf.mime_type,
                       sf.file_size,
                       sf.file_ext,
                       sf.file_hash,
                       sf.stored_path
                FROM submission_files sf
                JOIN submissions s ON s.id = sf.submission_id
                WHERE s.assignment_id = ?
                ORDER BY sf.submission_id, COALESCE(sf.relative_path, sf.original_filename), sf.id
                """,
                (assignment_id,),
            )
        ]

        # 获取班级花名册以包含未提交学生
        total_students = 0
        roster = []
        stage_target = get_stage_exam_target(conn, assignment_id)
        if assignment['class_offering_id']:
            offering = conn.execute("SELECT class_id FROM class_offerings WHERE id = ?",
                                    (assignment['class_offering_id'],)).fetchone()
            if offering:
                if stage_target:
                    students_cursor = conn.execute(
                        """
                        SELECT id, student_id_number, name
                        FROM students
                        WHERE id = ?
                          AND COALESCE(enrollment_status, 'active') = 'active'
                        """,
                        (int(stage_target["student_id"]),),
                    )
                else:
                    students_cursor = conn.execute(
                        """
                        SELECT id, student_id_number, name
                        FROM students
                        WHERE class_id = ?
                          AND COALESCE(enrollment_status, 'active') = 'active'
                        ORDER BY student_id_number
                        """,
                        (offering['class_id'],),
                    )
                roster = [dict(row) for row in students_cursor]
                total_students = len(roster)
        conn.commit()

    files_by_submission: dict[int, list[dict[str, Any]]] = {}
    for row in submission_file_rows:
        try:
            key = int(row.get("submission_id"))
        except (TypeError, ValueError):
            continue
        files_by_submission.setdefault(key, []).append(row)

    for submission in submissions:
        file_rows = files_by_submission.get(int(submission["id"]), [])
        type_summary = build_attachment_type_summary(file_rows)
        submission["attachment_type_summary"] = type_summary
        submission["has_unsupported_ai_attachments"] = any(not item.get("supported", True) for item in type_summary)

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
                'is_late_submission': 0,
                'late_by_seconds': 0,
                'score_before_late_penalty': None,
                'late_penalty_points': 0,
                'late_score_cap_applied': 0,
                'attachment_type_summary': [],
                'has_unsupported_ai_attachments': False,
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
        "unsubmitted_count": none_count,
        "graded_count": len(graded_entries),
        "absence_zero_count": len(absence_zero_entries),
        "submitted_count": len([s for s in submitted_entries if s['status'] == 'submitted']),
        "pending_grade_count": len([
            s for s in submitted_entries
            if s["status"] == "submitted"
            and not int(s.get("resubmission_allowed") or 0)
            and not int(s.get("is_absence_score") or 0)
        ]),
        "grading_count": len([s for s in submitted_entries if s['status'] == 'grading']),
        "returned_count": len(returned_entries),
        "late_submission_count": len([s for s in submitted_entries if int(s.get("is_late_submission") or 0)]),
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
                  AND COALESCE(s.enrollment_status, 'active') = 'active'
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
        assignment_for_late_policy = {
            "id": submission.get("assignment_id"),
            "due_at": submission.get("assignment_due_at"),
            "late_submission_enabled": submission.get("assignment_late_submission_enabled"),
            "late_submission_until": submission.get("assignment_late_submission_until"),
            "late_penalty_strategy": submission.get("assignment_late_penalty_strategy"),
            "late_penalty_interval_hours": submission.get("assignment_late_penalty_interval_hours"),
            "late_penalty_points": submission.get("assignment_late_penalty_points"),
            "late_penalty_min_score": submission.get("assignment_late_penalty_min_score"),
            "late_score_cap": submission.get("assignment_late_score_cap"),
        }
        adjustment = apply_late_policy_to_score(
            data.get("score"),
            submission=submission,
            assignment=assignment_for_late_policy,
        )
        final_score = adjustment.get("final_score")
        feedback_md = append_late_policy_feedback(data.get("feedback_md"), adjustment)
        conn.execute(
            """
            UPDATE submissions
            SET status = 'graded',
                score = ?,
                feedback_md = ?,
                score_before_late_penalty = ?,
                late_penalty_points = ?,
                late_score_cap_applied = ?,
                grading_started_at = NULL,
                grading_attempt_fingerprint = NULL,
                resubmission_allowed = 0,
                resubmission_due_at = NULL,
                returned_at = NULL,
                returned_by_teacher_id = NULL,
                returned_reason = NULL
            WHERE id = ?
            """,
            (
                final_score,
                feedback_md,
                adjustment.get("original_score") if adjustment.get("applied") else None,
                adjustment.get("penalty_points") or 0,
                1 if adjustment.get("score_cap_applied") else 0,
                submission_id,
            ),
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
        try:
            handle_stage_exam_grading_complete(conn, submission_id)
        except Exception as exc:
            print(f"[LEARNING_PROGRESS] manual grading stage handling failed: {exc}")
        try:
            handle_assignment_stage_grading_complete(conn, submission_id)
        except Exception as exc:
            print(f"[LEARNING_PROGRESS] manual grading teacher-stage handling failed: {exc}")
        conn.commit()
    return {"status": "success", "graded_submission_id": submission_id}


@router.post("/assignments/{assignment_id}/submissions/batch-grade", response_class=JSONResponse)
async def batch_grade_submissions(assignment_id: str, request: Request, user: dict = Depends(get_current_teacher)):
    """教师批量发起 AI 批改：可指定 submission_ids 或自动处理所有待批改提交。"""
    data = await request.json()
    submission_ids_input = _parse_int_set(data.get("submission_ids", []), "submission_ids")

    with get_db_connection() as conn:
        close_overdue_assignments(conn)
        assignment = _get_assignment_for_teacher(conn, assignment_id, int(user["id"]))

        if submission_ids_input:
            placeholders = ",".join("?" for _ in submission_ids_input)
            rows = conn.execute(
                f"""
                SELECT id, status FROM submissions
                WHERE assignment_id = ? AND id IN ({placeholders})
                ORDER BY id
                """,
                (assignment_id, *sorted(submission_ids_input)),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, status FROM submissions
                WHERE assignment_id = ?
                  AND status NOT IN ('graded', 'grading')
                  AND COALESCE(resubmission_allowed, 0) = 0
                  AND COALESCE(is_absence_score, 0) = 0
                ORDER BY id
                LIMIT 50
                """,
                (assignment_id,),
            ).fetchall()
        conn.commit()

    targets = [dict(row) for row in rows]
    if not targets:
        return {
            "status": "success",
            "queued_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "message": "没有可批改的提交（可能已全部批改完毕或正在批改中）。",
        }

    # 最多同时提交 5 个，避免压垮 AI 服务
    sem = asyncio.Semaphore(5)

    async def _grade_one(sub_id: int) -> str:
        async with sem:
            try:
                result = await submit_submission_for_ai_grading(sub_id, teacher_id=int(user["id"]), allow_graded=False)
                status = str(result.get("status") or "")
                if status in ("already_grading", "already_graded"):
                    return "skipped"
                return "queued"
            except AIGradingQueueError as exc:
                print(f"[BATCH_GRADE] submission {sub_id} failed: {exc.detail}")
                return "failed"
            except Exception as exc:
                print(f"[BATCH_GRADE] submission {sub_id} unexpected error: {exc}")
                return "failed"

    tasks = [_grade_one(int(t["id"])) for t in targets]
    results = await asyncio.gather(*tasks)
    queued = 0
    skipped = 0
    failed = 0
    for r in (results or []):
        if r == "queued":
            queued += 1
        elif r == "skipped":
            skipped += 1
        else:
            failed += 1

    return {
        "status": "success",
        "total_targets": len(targets),
        "queued_count": queued,
        "skipped_count": skipped,
        "failed_count": failed,
    }


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


@router.post("/submissions/{submission_id}/files", response_class=JSONResponse)
async def add_submission_files(
    submission_id: int,
    manifest: str = Form(""),
    queue_ai: str = Form("0"),
    files: List[UploadFile] = File(default=[]),
    user: dict = Depends(get_current_teacher),
):
    """教师为已提交记录补充附件；已批改记录必须先撤回。"""
    prepared_entries = _validate_upload_entries(files, manifest)
    if not prepared_entries:
        raise HTTPException(400, "请选择要添加的附件")

    queue_ai_requested = _form_bool(queue_ai)
    moved_paths: list[Path] = []
    staging_dir: Path | None = None

    with get_db_connection() as conn:
        submission = _get_submission_for_teacher(conn, int(submission_id), int(user["id"]))
        _ensure_submission_files_manageable(submission)
        allowed_file_types = decode_allowed_file_types_json(submission.get("allowed_file_types_json"))
        existing_files = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id, original_filename, relative_path, stored_path, mime_type, file_size, file_ext, file_hash
                FROM submission_files
                WHERE submission_id = ?
                ORDER BY COALESCE(relative_path, original_filename), id
                """,
                (submission_id,),
            )
        ]

    _deduplicate_upload_entries_against_existing(prepared_entries, existing_files)
    existing_count = len(existing_files)
    existing_total_bytes = sum(int(row.get("file_size") or 0) for row in existing_files)
    new_total_bytes = sum(int(entry.size_bytes) for entry in prepared_entries)
    if existing_count + len(prepared_entries) > MAX_SUBMISSION_FILE_COUNT:
        raise HTTPException(413, f"附件总数不能超过 {MAX_SUBMISSION_FILE_COUNT} 个")
    if existing_total_bytes + new_total_bytes > MAX_SUBMISSION_TOTAL_BYTES:
        raise HTTPException(
            413,
            f"附件总大小超过限制 {MAX_SUBMISSION_TOTAL_MB:.0f}MB"
            f"（当前 {(existing_total_bytes + new_total_bytes) / 1024 / 1024:.1f}MB）",
        )

    submission_dir = _build_submission_storage_dir(
        int(submission["course_id"]),
        submission["assignment_id"],
        int(submission["student_pk_id"]),
    )
    staging_dir = submission_dir.with_name(f"{submission_dir.name}.__teacher_add__{uuid.uuid4().hex}")

    try:
        storage_result = await store_submission_files(staging_dir, prepared_entries, allowed_file_types)
        if not storage_result.stored_files:
            expected_types = summarize_allowed_file_types(allowed_file_types)
            raise HTTPException(400, f"没有符合要求的文件可添加，允许类型: {expected_types}")
        try:
            ensure_ai_grading_attachments_supported([_stored_file_to_dict(item) for item in storage_result.stored_files])
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

        with get_db_connection() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                current_submission = _get_submission_for_teacher(conn, int(submission_id), int(user["id"]))
                _ensure_submission_files_manageable(current_submission)
                current_files = [
                    dict(row)
                    for row in conn.execute(
                        """
                        SELECT id, original_filename, relative_path, stored_path, mime_type, file_size, file_ext, file_hash
                        FROM submission_files
                        WHERE submission_id = ?
                        ORDER BY COALESCE(relative_path, original_filename), id
                        """,
                        (submission_id,),
                    )
                ]
                if len(current_files) + len(storage_result.stored_files) > MAX_SUBMISSION_FILE_COUNT:
                    raise HTTPException(413, f"附件总数不能超过 {MAX_SUBMISSION_FILE_COUNT} 个")
                current_total = sum(int(row.get("file_size") or 0) for row in current_files)
                stored_total = sum(int(item.file_size or 0) for item in storage_result.stored_files)
                if current_total + stored_total > MAX_SUBMISSION_TOTAL_BYTES:
                    raise HTTPException(413, f"附件总大小超过限制 {MAX_SUBMISSION_TOTAL_MB:.0f}MB")

                seen_paths = {
                    str(row.get("relative_path") or row.get("original_filename") or "").replace("\\", "/").strip().lower()
                    for row in current_files
                    if str(row.get("relative_path") or row.get("original_filename") or "").strip()
                }
                submission_dir.mkdir(parents=True, exist_ok=True)
                for file_info in storage_result.stored_files:
                    source_path = Path(file_info.stored_path)
                    relative_path = _deduplicate_relative_path_against_seen(file_info.relative_path, seen_paths)
                    final_path = _build_submission_file_path(submission_dir, relative_path)
                    final_path.parent.mkdir(parents=True, exist_ok=True)
                    source_path.replace(final_path)
                    moved_paths.append(final_path)
                    file_info.relative_path = relative_path
                    file_info.original_filename = PurePosixPath(relative_path).name
                    file_info.stored_path = str(final_path)
                    file_info.file_ext = Path(relative_path).suffix.lower()

                for file_info in storage_result.stored_files:
                    conn.execute(
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
                _reset_submission_after_attachment_edit(conn, int(submission_id), int(user["id"]))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
    except Exception:
        for path in moved_paths:
            try:
                if path.exists() and path.is_file():
                    path.unlink()
            except Exception as exc:
                print(f"[SUBMISSION_FILES] failed to remove moved file after rollback: {exc}")
        raise
    finally:
        if staging_dir:
            delete_storage_tree(staging_dir)

    ai_queue_result = None
    if queue_ai_requested:
        try:
            ai_queue_result = await submit_submission_for_ai_grading(
                int(submission_id),
                teacher_id=int(user["id"]),
                allow_graded=False,
            )
        except AIGradingQueueError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    return {
        "status": "success",
        "submission_id": int(submission_id),
        "added_count": len(storage_result.stored_files),
        "dropped_file_count": len(storage_result.dropped_files),
        "ai_queue_result": ai_queue_result,
    }


@router.delete("/submission-files/{file_id}", response_class=JSONResponse)
async def delete_submission_file(file_id: int, user: dict = Depends(get_current_teacher)):
    """教师删除单个学生提交附件；已批改记录必须先撤回。"""
    physical_path: Path | None = None
    with get_db_connection() as conn:
        file_row = conn.execute(
            """
            SELECT id, submission_id, original_filename, relative_path, stored_path, mime_type, file_size, file_ext, file_hash
            FROM submission_files
            WHERE id = ?
            LIMIT 1
            """,
            (int(file_id),),
        ).fetchone()
        if not file_row:
            raise HTTPException(404, "附件不存在")
        file_dict = dict(file_row)
        submission = _get_submission_for_teacher(conn, int(file_dict["submission_id"]), int(user["id"]))
        _ensure_submission_files_manageable(submission)
        resolved = resolve_submission_file_path(str(file_dict.get("stored_path") or ""))
        if resolved:
            physical_path = Path(resolved)

        answers_json = submission.get("answers_json")
        cleaned_answers_json = None
        if answers_json:
            try:
                answers_payload = json.loads(answers_json) if isinstance(answers_json, str) else answers_json
                cleaned_payload = remove_answer_attachment_references(answers_payload, file_dict)
                cleaned_answers_json = json.dumps(cleaned_payload, ensure_ascii=False)
            except (TypeError, json.JSONDecodeError):
                cleaned_answers_json = None

        try:
            conn.execute("BEGIN IMMEDIATE")
            current_submission = _get_submission_for_teacher(conn, int(file_dict["submission_id"]), int(user["id"]))
            _ensure_submission_files_manageable(current_submission)
            conn.execute("DELETE FROM submission_files WHERE id = ?", (int(file_id),))
            if cleaned_answers_json is not None:
                conn.execute(
                    "UPDATE submissions SET answers_json = ? WHERE id = ?",
                    (cleaned_answers_json, int(file_dict["submission_id"])),
                )
            _reset_submission_after_attachment_edit(conn, int(file_dict["submission_id"]), int(user["id"]))
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    if physical_path and physical_path.exists() and physical_path.is_file():
        try:
            physical_path.unlink()
        except Exception as exc:
            print(f"[SUBMISSION_FILES] failed to delete physical file {physical_path}: {exc}")

    return {
        "status": "success",
        "deleted_file_id": int(file_id),
        "submission_id": int(file_dict["submission_id"]),
    }


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
              AND s.student_pk_id IN (
                  SELECT id
                  FROM students
                  WHERE class_id = ?
                    AND COALESCE(enrollment_status, 'active') = 'active'
              )
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
        roster_cursor = conn.execute(
            """
            SELECT id, student_id_number, name
            FROM students
            WHERE class_id = ?
              AND COALESCE(enrollment_status, 'active') = 'active'
            """,
            (class_id,),
        )
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
                  AND student_pk_id IN (
                      SELECT id
                      FROM students
                      WHERE class_id = ?
                        AND COALESCE(enrollment_status, 'active') = 'active'
                  )""",
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
@router.get("/assignments/{assignment_id}/draft", response_class=JSONResponse)
async def get_assignment_draft(assignment_id: str, user: dict = Depends(get_current_student)):
    with get_db_connection() as conn:
        close_overdue_assignments(conn)
        conn.commit()
        assignment = conn.execute("SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
        if not assignment:
            raise HTTPException(404, "Assignment not found")
        assignment = enrich_assignment_runtime_view(assignment)
        _ensure_student_can_save_assignment_draft(
            conn,
            assignment=assignment,
            student_id=int(user["id"]),
        )
        draft = _load_submission_draft(conn, assignment_id, int(user["id"]))
        return _serialize_submission_draft(conn, draft, assignment_id)


@router.post("/assignments/{assignment_id}/draft", response_class=JSONResponse)
async def save_assignment_draft(
    assignment_id: str,
    answers_json: str = Form(""),
    current_page: int = Form(0),
    client_updated_at: str = Form(""),
    replace_question_ids: str = Form("[]"),
    manifest: str = Form(""),
    files: List[UploadFile] = File(default=[]),
    user: dict = Depends(get_current_student),
):
    staging_dir: Path | None = None
    move_backup_dir: Path | None = None
    moved_draft_files: list[tuple[Path, Path | None]] = []
    old_file_paths: list[str] = []
    with get_db_connection() as conn:
        close_overdue_assignments(conn)
        conn.commit()
        assignment = conn.execute("SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
        if not assignment:
            raise HTTPException(404, "Assignment not found")
        assignment = enrich_assignment_runtime_view(assignment)
        _ensure_student_can_save_assignment_draft(
            conn,
            assignment=assignment,
            student_id=int(user["id"]),
        )

        replace_ids = {
            str(item or "").strip()
            for item in _parse_json_list(replace_question_ids or "[]", field_name="replace_question_ids")
            if str(item or "").strip()
        }
        manifest_items = _parse_json_list(manifest or "[]", field_name="manifest") if manifest else []
        manifest_by_path: dict[str, dict[str, Any]] = {}
        for item in manifest_items:
            if not isinstance(item, dict):
                continue
            try:
                relative_path = normalize_submission_relative_path(
                    str(item.get("relative_path") or ""),
                    fallback_name=str(item.get("file_name") or item.get("filename") or "upload.bin"),
                )
            except HTTPException:
                continue
            manifest_by_path[relative_path.lower()] = item

        prepared_entries = _validate_upload_entries(files, manifest)
        allowed_file_types = decode_allowed_file_types_json(assignment.get("allowed_file_types_json"))
        draft_dir = _build_submission_draft_storage_dir(assignment["course_id"], assignment["id"], int(user["id"]))
        staging_dir = draft_dir.with_name(f"{draft_dir.name}.__staging__{uuid.uuid4().hex}")
        try:
            storage_result = await store_submission_files(staging_dir, prepared_entries, allowed_file_types)

            if storage_result.stored_files:
                remaining_rows = conn.execute(
                    """
                    SELECT question_id, file_size
                    FROM submission_draft_files sdf
                    JOIN submission_drafts sd ON sd.id = sdf.draft_id
                    WHERE sd.assignment_id = ? AND sd.student_pk_id = ?
                    """,
                    (assignment_id, int(user["id"])),
                ).fetchall()
                remaining_count = 0
                remaining_size = 0
                for row in remaining_rows:
                    if str(row["question_id"] or "") in replace_ids:
                        continue
                    remaining_count += 1
                    remaining_size += int(row["file_size"] or 0)
                next_count = remaining_count + len(storage_result.stored_files)
                next_size = remaining_size + sum(int(file_info.file_size or 0) for file_info in storage_result.stored_files)
                if next_count > MAX_SUBMISSION_FILE_COUNT:
                    raise HTTPException(413, f"草稿附件数量不能超过 {MAX_SUBMISSION_FILE_COUNT} 个")
                if next_size > MAX_SUBMISSION_TOTAL_BYTES:
                    raise HTTPException(
                        413,
                        f"草稿附件总大小超过限制 {MAX_SUBMISSION_TOTAL_MB:.0f}MB"
                        f"（当前 {next_size / 1024 / 1024:.1f}MB）",
                    )
        except Exception:
            if staging_dir:
                delete_storage_tree(staging_dir)
            raise

        try:
            if storage_result.stored_files:
                move_backup_dir = draft_dir.with_name(f"{draft_dir.name}.__replace_backup__{uuid.uuid4().hex}")
                moved_draft_files = _move_stored_files_to_final_dir(
                    storage_result.stored_files,
                    staging_dir=staging_dir,
                    final_dir=draft_dir,
                    backup_dir=move_backup_dir,
                )
            conn.execute("BEGIN IMMEDIATE")
            draft = _ensure_submission_draft(
                conn,
                assignment_id=assignment_id,
                student_pk_id=int(user["id"]),
                answers_json=answers_json,
                current_page=current_page,
                client_updated_at=client_updated_at,
            )
            old_file_paths = _delete_draft_file_rows_for_questions(
                conn,
                draft_id=int(draft["id"]),
                question_ids=replace_ids,
            )
            for file_info in storage_result.stored_files:
                manifest_item = manifest_by_path.get(file_info.relative_path.lower(), {})
                question_id = str(manifest_item.get("question_id") or "").strip()
                kind = str(manifest_item.get("kind") or "file").strip() or "file"
                duplicate_rows = conn.execute(
                    """
                    SELECT stored_path
                    FROM submission_draft_files
                    WHERE draft_id = ? AND LOWER(relative_path) = LOWER(?)
                    """,
                    (int(draft["id"]), file_info.relative_path),
                ).fetchall()
                old_file_paths.extend(
                    str(row["stored_path"] or "")
                    for row in duplicate_rows
                    if str(row["stored_path"] or "").strip()
                )
                conn.execute(
                    """
                    DELETE FROM submission_draft_files
                    WHERE draft_id = ? AND LOWER(relative_path) = LOWER(?)
                    """,
                    (int(draft["id"]), file_info.relative_path),
                )
                conn.execute(
                    """
                    INSERT INTO submission_draft_files (
                        draft_id, question_id, kind, original_filename, relative_path,
                        stored_path, mime_type, file_size, file_ext, file_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(draft["id"]),
                        question_id,
                        kind,
                        file_info.original_filename,
                        file_info.relative_path,
                        file_info.stored_path,
                        file_info.mime_type,
                        file_info.file_size,
                        file_info.file_ext,
                        file_info.file_hash,
                        datetime.now().isoformat(),
                    ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            _restore_moved_draft_files(moved_draft_files)
            raise
        finally:
            if staging_dir:
                delete_storage_tree(staging_dir)
            if move_backup_dir:
                delete_storage_tree(move_backup_dir)

        new_file_paths = {str(file_info.stored_path) for file_info in storage_result.stored_files}
        for old_path in old_file_paths:
            if str(old_path) in new_file_paths:
                continue
            physical_path = resolve_submission_file_path(old_path) or Path(old_path)
            try:
                physical_path = Path(physical_path)
                if physical_path.exists() and physical_path.is_file():
                    physical_path.unlink()
            except Exception as exc:
                print(f"[SUBMISSION_DRAFT] failed to delete old draft file {old_path}: {exc}")

        draft = _load_submission_draft(conn, assignment_id, int(user["id"]))
        payload = _serialize_submission_draft(conn, draft, assignment_id)
        payload.update(
            {
                "status": "success",
                "stored_file_count": len(storage_result.stored_files),
                "dropped_file_count": len(storage_result.dropped_files),
            }
        )
        return payload


@router.get("/assignments/{assignment_id}/draft-files/{file_id}")
async def download_assignment_draft_file(
    assignment_id: str,
    file_id: int,
    user: dict = Depends(get_current_student),
):
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT sdf.*, sd.assignment_id, sd.student_pk_id
            FROM submission_draft_files sdf
            JOIN submission_drafts sd ON sd.id = sdf.draft_id
            WHERE sdf.id = ? AND sd.assignment_id = ? AND sd.student_pk_id = ?
            LIMIT 1
            """,
            (int(file_id), assignment_id, int(user["id"])),
        ).fetchone()
        if not row:
            raise HTTPException(404, "草稿附件不存在")
        file_dict = dict(row)
    physical_path = resolve_submission_file_path(str(file_dict.get("stored_path") or "")) or Path(str(file_dict.get("stored_path") or ""))
    physical_path = Path(physical_path)
    if not physical_path.exists() or not physical_path.is_file():
        raise HTTPException(404, "草稿附件文件不存在")
    return FileResponse(
        physical_path,
        media_type=file_dict.get("mime_type") or "application/octet-stream",
        filename=file_dict.get("original_filename") or physical_path.name,
    )


@router.post("/assignments/{assignment_id}/submit", response_class=JSONResponse)
async def submit_assignment(assignment_id: str,
                            answers_json: str = Form(""),
                            manifest: str = Form(""),
                            started_at: str = Form(""),
                            use_server_draft: bool = Form(False),
                            files: List[UploadFile] = File(default=[]),
                            user: dict = Depends(get_current_student)):
    """
    V4.4: 学生提交作业 — 支持 JSON 格式的答案 + 可选文件附件
    answers_json: 包含所有答题内容的 JSON 字符串
    files: 可选的附件文件列表
    """
    stage_attempt = None
    with get_db_connection() as conn:
        close_overdue_assignments(conn)
        conn.commit()
        assignment = conn.execute("SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
        if not assignment:
            raise HTTPException(404, "Assignment not found")
        assignment = enrich_assignment_runtime_view(assignment)
        if not student_can_access_assignment(conn, assignment_id, int(user["id"])):
            raise HTTPException(403, "该破境试炼只对指定学生开放")
        personal_stage_target = get_stage_exam_target(conn, assignment_id)

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
            started_at=started_at,
            existing_submission=existing_submission,
            notify_teacher=personal_stage_target is None,
            use_server_draft_files=_form_bool(use_server_draft),
        )
        try:
            stage_attempt = mark_stage_submission_saved(conn, result["submission_id"])
        except Exception as exc:
            print(f"[LEARNING_PROGRESS] 破境试炼提交状态更新失败: {exc}")

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

    auto_ai_grading_scheduled = False
    if stage_attempt:
        stage_ai_task = submit_stage_exam_for_ai_grading(int(result["submission_id"]))
        try:
            asyncio.create_task(stage_ai_task)
            auto_ai_grading_scheduled = True
        except RuntimeError:
            await stage_ai_task
            auto_ai_grading_scheduled = True
    elif _assignment_uses_ai_grading(assignment):
        auto_ai_grading_scheduled = _schedule_ai_grading(int(result["submission_id"]), reason="assignment_auto")
        if not auto_ai_grading_scheduled:
            await _submit_ai_grading_background(int(result["submission_id"]), reason="assignment_auto")
            auto_ai_grading_scheduled = True

    grading_status = _resolve_grading_status(assignment, auto_ai_grading_scheduled)
    return {
        "status": "success",
        "submission_id": result["submission_id"],
        "stored_file_count": result["stored_file_count"],
        "dropped_file_count": result["dropped_file_count"],
        "is_resubmission": result["is_replacement"],
        "auto_ai_grading_scheduled": auto_ai_grading_scheduled,
        "grading_status": grading_status,
        "is_late_submission": result.get("is_late_submission", False),
        "late_by_seconds": result.get("late_by_seconds", 0),
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
                grading_started_at = NULL,
                grading_attempt_fingerprint = NULL,
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

    auto_ai_grading_scheduled = False
    if _assignment_uses_ai_grading(assignment):
        auto_ai_grading_scheduled = _schedule_ai_grading(int(result["submission_id"]), reason="teacher_offline_auto")
        if not auto_ai_grading_scheduled:
            await _submit_ai_grading_background(int(result["submission_id"]), reason="teacher_offline_auto")
            auto_ai_grading_scheduled = True

    return {
        "status": "success",
        "submission_id": result["submission_id"],
        "stored_file_count": result["stored_file_count"],
        "dropped_file_count": result["dropped_file_count"],
        "is_replacement": result["is_replacement"],
        "auto_ai_grading_scheduled": auto_ai_grading_scheduled,
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
                   a.availability_mode, a.starts_at, a.due_at, a.duration_minutes, a.auto_close,
                   a.late_submission_enabled, a.late_submission_until,
                   a.late_penalty_strategy, a.late_penalty_interval_hours,
                   a.late_penalty_points, a.late_penalty_min_score, a.late_score_cap
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
            "late_submission_enabled": submission.get("late_submission_enabled"),
            "late_submission_until": submission.get("late_submission_until"),
            "late_penalty_strategy": submission.get("late_penalty_strategy"),
            "late_penalty_interval_hours": submission.get("late_penalty_interval_hours"),
            "late_penalty_points": submission.get("late_penalty_points"),
            "late_penalty_min_score": submission.get("late_penalty_min_score"),
            "late_score_cap": submission.get("late_score_cap"),
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
                      (SELECT COUNT(*)
                       FROM assignments a
                       WHERE a.exam_paper_id = ep.id
                         AND NOT EXISTS (
                             SELECT 1 FROM learning_stage_exam_attempts lsea
                             WHERE lsea.assignment_id = a.id
                         )) as assigned_count
               FROM exam_papers ep
               WHERE ep.teacher_id = ?
                 AND NOT EXISTS (
                     SELECT 1 FROM learning_stage_exam_attempts lsea
                     WHERE lsea.exam_paper_id = ep.id
                 )
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
        _get_exam_paper_for_teacher(conn, paper_id, int(user["id"]))
        conn.execute(
            "UPDATE exam_papers SET tags_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(tags, ensure_ascii=False), now, paper_id)
        )
        conn.commit()
    return {"status": "success", "tags": tags}


@router.get("/exam-papers/json-template")
async def download_exam_json_template(user: dict = Depends(get_current_teacher)):
    """下载原生 JSON 试卷模板。"""
    content = get_exam_json_template_text().encode("utf-8")
    filename = quote("试卷原生JSON导入模板.json")
    return StreamingResponse(
        io.BytesIO(content),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@router.post("/exam-papers/import-json", response_class=JSONResponse)
async def import_exam_paper_json(file: UploadFile = File(...), user: dict = Depends(get_current_teacher)):
    """解析原生 JSON 试卷文件，不调用内置 AI。"""
    filename = Path(str(file.filename or "exam.json")).name
    if Path(filename).suffix.lower() != ".json":
        raise HTTPException(400, "请上传 .json 文件")

    raw = await file.read()
    if not raw:
        raise HTTPException(400, "JSON 文件为空")
    if len(raw) > EXAM_JSON_MAX_BYTES:
        raise HTTPException(413, "JSON 文件不能超过 2MB")

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(400, "JSON 文件必须使用 UTF-8 编码") from exc

    try:
        imported = parse_exam_json_text(text)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    imported["source_filename"] = filename
    return {"status": "success", "imported": imported}


@router.post("/exam-papers", response_class=JSONResponse)
async def create_exam_paper(request: Request, user: dict = Depends(get_current_teacher)):
    """创建新试卷"""
    data = await request.json()
    paper_id = data.get('id') or str(uuid.uuid4())
    now = datetime.now().isoformat()
    try:
        questions_payload = normalize_exam_scoring_payload(data.get('questions', {"pages": []}))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    with get_db_connection() as conn:
        conn.execute(
            """INSERT INTO exam_papers (id, teacher_id, title, description, questions_json, exam_config_json, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (paper_id, user['id'], data['title'], data.get('description', ''),
             json.dumps(questions_payload, ensure_ascii=False),
             json.dumps(data.get('config', {}), ensure_ascii=False),
             data.get('status', 'draft'), now, now)
        )
        conn.commit()
    return {"status": "success", "paper_id": paper_id}


@router.get("/exam-papers/{paper_id}", response_class=JSONResponse)
async def get_exam_paper(paper_id: str, user: dict = Depends(get_current_user)):
    """获取试卷详情"""
    if str(user.get("role") or "").lower() != "teacher":
        raise HTTPException(403, "无权查看此试卷")
    with get_db_connection() as conn:
        result = _get_exam_paper_for_teacher(conn, paper_id, int(user["id"]))
        # 获取已分配的课堂列表
        assignments = conn.execute(
            """SELECT a.id, a.status, a.title, o.id as offering_id, c.name as course_name, cl.name as class_name
               FROM assignments a
               LEFT JOIN class_offerings o ON a.class_offering_id = o.id
               LEFT JOIN courses c ON o.course_id = c.id
               LEFT JOIN classes cl ON o.class_id = cl.id
               WHERE a.exam_paper_id = ?
                 AND NOT EXISTS (
                     SELECT 1 FROM learning_stage_exam_attempts lsea
                     WHERE lsea.assignment_id = a.id
                 )""",
            (paper_id,)
        ).fetchall()
        result['assignments'] = [dict(row) for row in assignments]
    return {"status": "success", "paper": result}


@router.put("/exam-papers/{paper_id}", response_class=JSONResponse)
async def update_exam_paper(paper_id: str, request: Request, user: dict = Depends(get_current_teacher)):
    """更新试卷"""
    data = await request.json()
    now = datetime.now().isoformat()
    try:
        questions_payload = normalize_exam_scoring_payload(data.get('questions', {"pages": []}))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    with get_db_connection() as conn:
        _get_exam_paper_for_teacher(conn, paper_id, int(user["id"]))

        conn.execute(
            """UPDATE exam_papers
               SET title = ?, description = ?, questions_json = ?, exam_config_json = ?, status = ?, updated_at = ?
               WHERE id = ?""",
            (data['title'], data.get('description', ''),
             json.dumps(questions_payload, ensure_ascii=False),
             json.dumps(data.get('config', {}), ensure_ascii=False),
             data.get('status', 'draft'), now, paper_id)
        )
        conn.commit()
    return {"status": "success", "paper_id": paper_id}


@router.delete("/exam-papers/{paper_id}", response_class=JSONResponse)
async def delete_exam_paper(paper_id: str, user: dict = Depends(get_current_teacher)):
    """删除试卷"""
    with get_db_connection() as conn:
        _get_exam_paper_for_teacher(conn, paper_id, int(user["id"]))
        # 检查是否已被分配
        assigned = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM assignments a
            WHERE a.exam_paper_id = ?
              AND {personal_stage_assignment_filter_sql('a')}
            """,
            (paper_id,),
        ).fetchone()[0]
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
    learning_stage_key = _get_learning_stage_key(data, class_offering_id=class_offering_id)
    try:
        schedule_fields = build_assignment_schedule_fields(
            data,
            default_status="published",
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    with get_db_connection() as conn:
        close_overdue_assignments(conn)
        paper = _get_exam_paper_for_teacher(conn, paper_id, int(user["id"]))

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
        try:
            paper_questions = json.loads(paper["questions_json"] or "{}")
            if not isinstance(paper_questions, dict):
                paper_questions = {"pages": []}
            paper_questions = normalize_exam_scoring_payload(paper_questions, require_complete=True)
            exam_rubric_md = build_exam_rubric_md(
                title=str(paper["title"] or ""),
                description=str(paper["description"] or ""),
                exam_data=paper_questions,
                require_complete=True,
            )
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            raise HTTPException(
                400,
                f"试卷评分标准不完整，请先回到试卷编辑器补齐标准答案、分值、评分指导和扣分点：{exc}",
            ) from exc

        conn.execute(
            "UPDATE exam_papers SET questions_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(paper_questions, ensure_ascii=False), created_at, paper_id),
        )

        allowed_file_types_json = encode_allowed_file_types_json(_get_allowed_file_types(data))
        cursor = conn.execute(
            """
            INSERT INTO assignments (
                course_id, title, status, requirements_md, rubric_md, grading_mode,
                exam_paper_id, class_offering_id, created_at, allowed_file_types_json,
                availability_mode, starts_at, due_at, duration_minutes, auto_close, closed_at,
                late_submission_enabled, late_submission_until, late_penalty_strategy,
                late_penalty_interval_hours, late_penalty_points, late_penalty_min_score, late_score_cap,
                learning_stage_key
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(offering['course_id']),
                data.get('title', paper['title']),
                schedule_fields["status"],
                f"**试卷**: {paper['title']}\n\n{paper['description'] or ''}",
                exam_rubric_md,
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
