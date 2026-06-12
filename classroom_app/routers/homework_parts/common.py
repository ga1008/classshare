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
# from ...core import COURSE_INFO
from ...config import (
    AI_GRADING_STALE_MINUTES,
    HOMEWORK_SUBMISSIONS_DIR,
    MAX_SUBMISSION_FILE_COUNT,
    MAX_SUBMISSION_PER_FILE_BYTES,
    MAX_SUBMISSION_PER_FILE_MB,
    MAX_SUBMISSION_TOTAL_BYTES,
    MAX_SUBMISSION_TOTAL_MB,
    MAX_UPLOAD_SIZE_BYTES,
    MAX_UPLOAD_SIZE_MB,
)
from ...database import get_db_connection
from ...db.connection import begin_immediate_transaction, execute_insert_returning_id
from ...dependencies import get_current_user, get_current_student, get_current_teacher
from ...schemas.homework_contracts import (
    AssignmentDraftResponse,
    AssignmentDraftSaveResponse,
    AssignmentMutationResponse,
    AssignmentSubmissionsResponse,
    AssignmentTimeStateResponse,
    CourseAssignmentStatsResponse,
    ExamPaperDetailResponse,
    ExamPapersResponse,
    SubmissionMutationResponse,
)
from ...services.behavior_tracking_service import record_behavior_event
from ...services.message_center_service import (
    create_assignment_published_notifications,
    create_student_grading_notification,
    create_submission_notification,
)
from ...services.assignment_lifecycle_service import (
    assignment_accepts_submissions,
    build_resubmission_due_at,
    build_assignment_schedule_fields,
    close_overdue_assignments,
    enrich_assignment_runtime_view,
    refresh_assignment_runtime_status,
    submission_resubmission_accepts,
)
from ...services.late_submission_policy import (
    append_late_policy_feedback,
    apply_late_policy_to_score,
    build_late_submission_snapshot,
    serialize_assignment_time_state,
    utc_like_now,
)
from ...services.exam_json_service import (
    build_exam_rubric_md,
    EXAM_JSON_MAX_BYTES,
    get_exam_json_template_text,
    normalize_exam_scoring_payload,
    parse_exam_json_text,
)
from ...services.submission_assets import (
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
from ...services.submission_file_alignment import resolve_submission_file_path
from ...services.submission_export_docx_service import (
    DOCX_MEDIA_TYPE,
    build_student_submission_export_docx,
)
from ...services.ai_grading_attachments import (
    build_attachment_type_summary,
    ensure_ai_grading_attachments_supported,
)
from ...services.ai_grading_service import (
    AIGradingQueueError,
    expire_stale_ai_grading_submissions,
    submit_submission_for_ai_grading,
)
from ...services.learning_progress_service import (
    get_stage_exam_target,
    handle_assignment_stage_grading_complete,
    handle_stage_exam_grading_complete,
    is_personal_stage_exam_assignment,
    is_personal_stage_exam_paper,
    mark_stage_submission_saved,
    normalize_assignment_stage_key,
    personal_stage_assignment_filter_sql,
    refresh_student_learning_state,
    student_can_access_assignment,
    submit_stage_exam_for_ai_grading,
)
from ...services.organization_scope_service import load_teacher_org_scope
from ...services.file_service import resolve_global_file_path
from ...services.materials_service import ensure_user_material_access
from ...services.resource_access_service import (
    SCOPE_DEPARTMENT,
    SCOPE_PRIVATE,
    SCOPE_SCHOOL,
    normalize_scope_level,
    teacher_can_manage_assignment,
    teacher_can_manage_exam_paper,
    teacher_can_use_exam_paper,
)


def insert_and_get_id(conn, sql: str, params: tuple | list, *, id_column: str = "id") -> int:
    return execute_insert_returning_id(conn, sql, params, id_column=id_column)


# 并发控制：限制同时进行的 AI 批改提交数量，避免大量学生同时提交时
# 创建过多 SQLite 连接导致 WAL 锁争用。
_ai_grading_submit_semaphore = asyncio.Semaphore(10)

PERSONAL_STAGE_TEACHER_HIDDEN_MESSAGE = "学生个人试炼属于学生资产，不在教师作业与考试中展示；请查看班级修行统计。"


def _truthy_request_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "checked"}


def _wants_assignment_email_notification(data: dict[str, Any]) -> bool:
    for key in (
        "send_email_notification",
        "send_email_notifications",
        "notify_students_by_email",
        "email_notification_enabled",
    ):
        if key in data:
            return _truthy_request_flag(data.get(key))
    return False


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


def _question_id_from_submission_relative_path(relative_path: str) -> str | None:
    normalized = str(relative_path or "").replace("\\", "/").strip().strip("/")
    if not normalized:
        return None
    parts = PurePosixPath(normalized).parts
    if len(parts) >= 3 and parts[0] == "exam_question_files":
        return str(parts[1] or "").strip() or None
    return None


def _allowed_file_types_for_submission_path(
    relative_path: str,
    assignment_allowed_file_types: list[str],
    attachment_policies: dict[str, dict[str, Any]] | None,
) -> list[str]:
    question_id = _question_id_from_submission_relative_path(relative_path)
    if question_id and attachment_policies:
        policy = attachment_policies.get(question_id) or {}
        policy_allowed = policy.get("allowed_file_types") or []
        if policy_allowed:
            return normalize_allowed_file_types(policy_allowed)
    return normalize_allowed_file_types(assignment_allowed_file_types)


def _is_allowed_assignment_submission_file(
    relative_path: str,
    content_type: str | None,
    assignment_allowed_file_types: list[str],
    attachment_policies: dict[str, dict[str, Any]] | None,
) -> bool:
    return is_allowed_submission_file(
        relative_path,
        content_type,
        _allowed_file_types_for_submission_path(
            relative_path,
            assignment_allowed_file_types,
            attachment_policies,
        ),
    )


def _display_submission_file_name(relative_path: str) -> str:
    normalized = str(relative_path or "").replace("\\", "/").strip().strip("/")
    if not normalized:
        return "附件"
    return PurePosixPath(normalized).name or normalized or "附件"


def _allowed_file_types_label_for_submission_path(
    relative_path: str,
    assignment_allowed_file_types: list[str],
    attachment_policies: dict[str, dict[str, Any]] | None,
) -> str:
    allowed_types = _allowed_file_types_for_submission_path(
        relative_path,
        assignment_allowed_file_types,
        attachment_policies,
    )
    label = summarize_allowed_file_types(allowed_types)
    return "任意类型" if label == "all" else label


def _dropped_file_user_message(detail: dict[str, Any]) -> str:
    file_name = str(detail.get("file_name") or "附件")
    reason = str(detail.get("reason") or "")
    allowed_label = str(detail.get("allowed_file_types_label") or "任意类型")
    if reason == "type_not_allowed":
        return f"文件“{file_name}”类型不符合要求。当前允许类型：{allowed_label}。请转换为允许格式后重新上传。"
    if reason == "draft_file_unavailable":
        return f"服务器草稿中的文件“{file_name}”已丢失或无法读取。请重新选择该文件，等待同步成功后再提交。"
    if reason == "missing_relative_path":
        return f"文件“{file_name}”路径信息无效。请重新选择文件后再提交。"
    return f"文件“{file_name}”未能保存。请重新上传，或联系老师确认作业附件设置。"


def _enrich_dropped_file_details(
    dropped_files: list[dict[str, Any]],
    assignment_allowed_file_types: list[str],
    attachment_policies: dict[str, dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for item in dropped_files or []:
        relative_path = str(
            item.get("relative_path")
            or item.get("original_filename")
            or item.get("file_name")
            or ""
        ).replace("\\", "/").strip()
        detail = {
            "relative_path": relative_path,
            "file_name": _display_submission_file_name(relative_path),
            "reason": str(item.get("reason") or "unknown"),
            "content_type": str(item.get("content_type") or item.get("mime_type") or ""),
            "allowed_file_types_label": _allowed_file_types_label_for_submission_path(
                relative_path,
                assignment_allowed_file_types,
                attachment_policies,
            ),
        }
        detail["message"] = _dropped_file_user_message(detail)
        enriched.append(detail)
    return enriched


def _format_dropped_files_error(dropped_files: list[dict[str, Any]], *, action_label: str) -> str:
    messages = [str(item.get("message") or "").strip() for item in dropped_files or []]
    messages = [message for message in messages if message]
    if not messages:
        return f"{action_label}失败：有文件不符合要求，请检查文件类型、大小和作业附件设置。"
    preview = "；".join(messages[:3])
    remaining = len(messages) - 3
    if remaining > 0:
        preview = f"{preview}；还有 {remaining} 个文件也不符合要求。"
    return f"{action_label}失败：{preview}"


def _dropped_files_error_detail(dropped_files: list[dict[str, Any]], *, action_label: str) -> dict[str, Any]:
    return {
        "message": _format_dropped_files_error(dropped_files, action_label=action_label),
        "dropped_file_count": len(dropped_files or []),
        "dropped_files": dropped_files or [],
    }


def _dropped_files_response_fields(dropped_files: list[dict[str, Any]], *, action_label: str) -> dict[str, Any]:
    return {
        "dropped_file_count": len(dropped_files or []),
        "dropped_files": dropped_files or [],
        "dropped_file_message": (
            _format_dropped_files_error(dropped_files, action_label=action_label)
            if dropped_files
            else ""
        ),
    }


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


def _teacher_can_access_assignment(conn, assignment: dict[str, Any], teacher_id: int) -> bool:
    return teacher_can_manage_assignment(conn, int(teacher_id), assignment)


def _hide_personal_stage_asset() -> None:
    raise HTTPException(404, PERSONAL_STAGE_TEACHER_HIDDEN_MESSAGE)


EXAM_OPEN_SCOPES = {SCOPE_PRIVATE, SCOPE_DEPARTMENT, SCOPE_SCHOOL}
EXAM_SCOPE_LABELS = {
    SCOPE_PRIVATE: "私有",
    SCOPE_DEPARTMENT: "本系部开放",
    SCOPE_SCHOOL: "全校开放",
}


def _normalize_exam_open_scope(value: Any, default: str = SCOPE_DEPARTMENT) -> str:
    scope = normalize_scope_level(value, default=default)
    return scope if scope in EXAM_OPEN_SCOPES else default


def _exam_scope_label(scope_level: Any) -> str:
    return EXAM_SCOPE_LABELS.get(_normalize_exam_open_scope(scope_level, default=SCOPE_PRIVATE), "私有")


def _get_exam_paper_for_teacher(conn, paper_id: str, teacher_id: int, *, manage: bool = False) -> dict[str, Any]:
    paper = conn.execute("SELECT * FROM exam_papers WHERE id = ?", (paper_id,)).fetchone()
    if not paper:
        raise HTTPException(404, "试卷不存在")
    paper_dict = dict(paper)
    allowed = (
        teacher_can_manage_exam_paper(conn, teacher_id, paper_dict)
        if manage
        else teacher_can_use_exam_paper(conn, teacher_id, paper_dict)
    )
    if not allowed:
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
    if not _teacher_can_access_assignment(conn, assignment_dict, int(teacher_id)):
        raise HTTPException(403, "无权操作该作业")
    if assignment_dict.get("personal_stage_attempt_id") is not None:
        _hide_personal_stage_asset()
    return assignment_dict


def _expire_stale_ai_grading_for_assignment(conn, assignment_id: str) -> int:
    try:
        reclaimed_count = expire_stale_ai_grading_submissions(
            conn,
            stale_minutes=AI_GRADING_STALE_MINUTES,
            assignment_ids=[assignment_id],
        )
        if reclaimed_count:
            conn.commit()
            print(
                f"[AI_GRADING] reclaimed {reclaimed_count} stale grading submission(s) "
                f"for assignment {assignment_id}"
            )
        return int(reclaimed_count or 0)
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"[AI_GRADING] stale grading reclaim for assignment {assignment_id} failed: {exc}")
        return 0


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
    if not _teacher_can_access_assignment(conn, submission_dict, int(teacher_id)):
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

    draft_id = insert_and_get_id(
        conn,
        """
        INSERT INTO submission_drafts (
            assignment_id, student_pk_id, answers_json, current_page,
            client_updated_at, server_updated_at, server_version, status
        ) VALUES (?, ?, ?, ?, ?, ?, 1, 'active')
        """,
        (assignment_id, int(student_pk_id), answers_json, current_page, client_updated_at, now),
    )
    return {
        "id": draft_id,
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


def _delete_old_draft_physical_files(old_file_paths: list[str], *, keep_paths: set[str] | None = None) -> None:
    keep_paths = keep_paths or set()
    for old_path in old_file_paths:
        if str(old_path) in keep_paths:
            continue
        physical_path = resolve_submission_file_path(old_path) or Path(old_path)
        try:
            physical_path = Path(physical_path)
            if physical_path.exists() and physical_path.is_file():
                physical_path.unlink()
        except Exception as exc:
            print(f"[SUBMISSION_DRAFT] failed to delete old draft file {old_path}: {exc}")


def _save_assignment_draft_without_files_sync(
    *,
    assignment_id: str,
    student_pk_id: int,
    answers_json: str,
    current_page: int,
    client_updated_at: str,
    replace_question_ids: str,
) -> dict[str, Any]:
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
            student_id=int(student_pk_id),
        )

        replace_ids = {
            str(item or "").strip()
            for item in _parse_json_list(replace_question_ids or "[]", field_name="replace_question_ids")
            if str(item or "").strip()
        }

        try:
            begin_immediate_transaction(conn)
            draft = _ensure_submission_draft(
                conn,
                assignment_id=assignment_id,
                student_pk_id=int(student_pk_id),
                answers_json=answers_json,
                current_page=current_page,
                client_updated_at=client_updated_at,
            )
            old_file_paths = _delete_draft_file_rows_for_questions(
                conn,
                draft_id=int(draft["id"]),
                question_ids=replace_ids,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        draft = _load_submission_draft(conn, assignment_id, int(student_pk_id))
        payload = _serialize_submission_draft(conn, draft, assignment_id)

    _delete_old_draft_physical_files(old_file_paths)
    payload.update(
        {
            "status": "success",
            "stored_file_count": 0,
            **_dropped_files_response_fields([], action_label="保存到服务器草稿"),
        }
    )
    return payload


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
    attachment_policies: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[StoredSubmissionFile], list[dict[str, Any]]]:
    draft = _load_submission_draft(conn, str(assignment["id"]), int(student_pk_id))
    if not draft:
        return [], []
    copied_files: list[StoredSubmissionFile] = []
    dropped_files: list[dict[str, Any]] = []
    for row in _load_submission_draft_files(conn, int(draft["id"])):
        relative_path = str(row.get("relative_path") or "").replace("\\", "/").strip()
        mime_type = str(row.get("mime_type") or "")
        if not relative_path:
            dropped_files.append(
                {
                    "relative_path": str(row.get("original_filename") or "服务器草稿附件"),
                    "reason": "missing_relative_path",
                    "content_type": mime_type,
                }
            )
            continue
        path_key = relative_path.lower()
        if path_key in existing_relative_paths:
            continue
        if not _is_allowed_assignment_submission_file(
            relative_path,
            mime_type,
            allowed_file_types,
            attachment_policies,
        ):
            dropped_files.append(
                {
                    "relative_path": relative_path,
                    "reason": "type_not_allowed",
                    "content_type": mime_type,
                }
            )
            continue
        source_path = resolve_submission_file_path(str(row.get("stored_path") or "")) or Path(str(row.get("stored_path") or ""))
        source_path = Path(source_path)
        if not source_path.exists() or not source_path.is_file():
            dropped_files.append(
                {
                    "relative_path": relative_path,
                    "reason": "draft_file_unavailable",
                    "content_type": mime_type,
                }
            )
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
    return copied_files, dropped_files


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
    attachment_policies = _load_exam_attachment_policies(conn, assignment)
    student_pk_id = int(student["id"])
    draft = _load_submission_draft(conn, str(assignment["id"]), student_pk_id) if use_server_draft_files else None
    has_server_draft_files = bool(draft and _load_submission_draft_files(conn, int(draft["id"])))
    has_allowed_uploads = any(
        _is_allowed_assignment_submission_file(
            entry.relative_path,
            entry.content_type,
            allowed_file_types,
            attachment_policies,
        )
        for entry in prepared_entries
    )

    if not has_answer_content_before_storage and not prepared_entries and not has_server_draft_files:
        raise HTTPException(400, "请至少填写答案或上传一个文件")
    if (
        not has_answer_content_before_storage
        and prepared_entries
        and not has_allowed_uploads
        and not has_server_draft_files
    ):
        dropped_files = _enrich_dropped_file_details(
            [
                {
                    "relative_path": entry.relative_path,
                    "reason": "type_not_allowed",
                    "content_type": entry.content_type,
                }
                for entry in prepared_entries
            ],
            allowed_file_types,
            attachment_policies,
        )
        if dropped_files:
            for entry in prepared_entries:
                await entry.file.close()
            raise HTTPException(
                status_code=400,
                detail=_dropped_files_error_detail(dropped_files, action_label="提交"),
            )
    if not has_answer_content_before_storage and not has_allowed_uploads and not has_server_draft_files:
        expected_types = summarize_allowed_file_types(allowed_file_types)
        raise HTTPException(400, f"没有符合要求的文件可提交，允许类型: {expected_types}")

    submission_dir = _build_submission_storage_dir(assignment["course_id"], assignment["id"], student_pk_id)
    staging_dir = submission_dir.with_name(f"{submission_dir.name}.__staging__{uuid.uuid4().hex}")
    backup_dir = None
    staging_moved_to_final = False
    is_replacement = bool(existing_submission)

    try:
        storage_result = await store_submission_files(
            staging_dir,
            prepared_entries,
            allowed_file_types,
            is_allowed_file=lambda entry: _is_allowed_assignment_submission_file(
                entry.relative_path,
                entry.content_type,
                allowed_file_types,
                attachment_policies,
            ),
        )
        storage_result.dropped_files = _enrich_dropped_file_details(
            storage_result.dropped_files,
            allowed_file_types,
            attachment_policies,
        )
        if storage_result.dropped_files:
            raise HTTPException(
                status_code=400,
                detail=_dropped_files_error_detail(storage_result.dropped_files, action_label="提交"),
            )
        if not storage_result.stored_files and not has_answer_content_before_storage:
            if not has_server_draft_files:
                expected_types = summarize_allowed_file_types(allowed_file_types)
                raise HTTPException(400, f"没有符合要求的文件可提交，允许类型: {expected_types}")
        for file_info in storage_result.stored_files:
            file_info.stored_path = str(_build_submission_file_path(submission_dir, file_info.relative_path))
        if use_server_draft_files:
            existing_paths = {file_info.relative_path.lower() for file_info in storage_result.stored_files}
            draft_files, draft_dropped_files = _copy_submission_draft_files_to_staging(
                conn,
                assignment=assignment,
                student_pk_id=student_pk_id,
                staging_dir=staging_dir,
                submission_dir=submission_dir,
                existing_relative_paths=existing_paths,
                allowed_file_types=allowed_file_types,
                attachment_policies=attachment_policies,
            )
            storage_result.stored_files.extend(draft_files)
            storage_result.dropped_files.extend(
                _enrich_dropped_file_details(
                    draft_dropped_files,
                    allowed_file_types,
                    attachment_policies,
                )
            )
            if storage_result.dropped_files:
                raise HTTPException(
                    status_code=400,
                    detail=_dropped_files_error_detail(storage_result.dropped_files, action_label="提交"),
                )
        _validate_combined_stored_file_limits(storage_result.stored_files)
    except Exception:
        delete_storage_tree(staging_dir)
        raise

    answers_payload = reconcile_answer_attachment_references(answers_payload, storage_result.stored_files)
    try:
        _validate_exam_answer_attachment_policies(
            answers_payload,
            attachment_policies,
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
        begin_immediate_transaction(conn)
        cursor = conn
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
            submission_id = insert_and_get_id(
                cursor,
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
        if assignment.get("class_offering_id"):
            try:
                refresh_student_learning_state(
                    conn,
                    int(assignment["class_offering_id"]),
                    int(student_pk_id),
                    event_source_ref=f"submission:{submission_id}",
                )
            except Exception as exc:
                print(f"[LEARNING_PROGRESS] submission snapshot refresh failed: {exc}")
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
        **_dropped_files_response_fields(storage_result.dropped_files, action_label="提交"),
        "has_text_answers": bool(has_answer_content),
        "is_replacement": is_replacement,
        "is_late_submission": bool(is_late_submission),
        "late_by_seconds": late_by_seconds,
    }


# --- 教师作业 API ---


























def _sanitize_zip_path(name: str) -> str:
    """Remove or replace characters that are unsafe in zip paths / filenames."""
    import re
    # Replace characters unsafe for filenames (Windows + cross-platform)
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    # Collapse consecutive underscores and strip
    sanitized = re.sub(r'_+', '_', sanitized).strip('_ ')
    return sanitized or "export"






# --- 学生作业 API ---














# --- 试卷库 API ---
















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


__all__ = [name for name in globals() if not name.startswith("__")]
