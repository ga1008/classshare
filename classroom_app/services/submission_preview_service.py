from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException

from .file_preview_service import infer_file_preview_profile, load_text_content


def _coerce_user_id(user: dict | None) -> int:
    if not user:
        raise HTTPException(401, "Not authenticated")
    try:
        return int(user.get("id"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(403, "Invalid user") from exc


def _can_teacher_access_submission(row, teacher_id: int) -> bool:
    creator_teacher_id = int(row["created_by_teacher_id"] or 0)
    offering_teacher_id = int(row["offering_teacher_id"] or 0)
    return teacher_id in {creator_teacher_id, offering_teacher_id}


def ensure_submission_access(conn, submission_id: int, user: dict | None) -> dict:
    user_id = _coerce_user_id(user)
    row = conn.execute(
        """
        SELECT
            s.*,
            a.course_id,
            a.class_offering_id,
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
    if not row:
        raise HTTPException(404, "Submission not found")

    role = str(user.get("role") or "").lower()
    if role == "student":
        if int(row["student_pk_id"]) != user_id:
            raise HTTPException(403, "Permission denied")
        return dict(row)

    if role == "teacher":
        if not _can_teacher_access_submission(row, user_id):
            raise HTTPException(403, "Permission denied")
        return dict(row)

    raise HTTPException(403, "Permission denied")


def ensure_submission_file_access(conn, file_id: int, user: dict | None) -> dict:
    user_id = _coerce_user_id(user)
    row = conn.execute(
        """
        SELECT
            sf.*,
            s.assignment_id,
            s.student_pk_id,
            a.course_id,
            a.class_offering_id,
            c.created_by_teacher_id,
            o.teacher_id AS offering_teacher_id
        FROM submission_files sf
        JOIN submissions s ON s.id = sf.submission_id
        JOIN assignments a ON a.id = s.assignment_id
        JOIN courses c ON c.id = a.course_id
        LEFT JOIN class_offerings o ON o.id = a.class_offering_id
        WHERE sf.id = ?
        LIMIT 1
        """,
        (file_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "File not found")

    role = str(user.get("role") or "").lower()
    if role == "student":
        if int(row["student_pk_id"]) != user_id:
            raise HTTPException(403, "Permission denied")
    elif role == "teacher":
        if not _can_teacher_access_submission(row, user_id):
            raise HTTPException(403, "Permission denied")
    else:
        raise HTTPException(403, "Permission denied")

    return dict(row)


def serialize_submission_file_row(row, extra: dict | None = None) -> dict:
    item = dict(row)
    display_name = item.get("relative_path") or item.get("original_filename") or "file"
    profile = infer_file_preview_profile(display_name, item.get("mime_type"))
    item["display_name"] = display_name
    item.update(profile)
    item["preview_url"] = f"/api/submission-files/{item['id']}/preview"
    item["raw_url"] = f"/submission-files/raw/{item['id']}" if item["is_image"] else ""
    item["download_url"] = f"/submissions/download/{item['id']}"
    if extra:
        item.update(extra)
    return item


async def build_submission_file_preview_payload(file_row) -> dict:
    payload = serialize_submission_file_row(file_row)
    if payload["preview_type"] not in {"markdown", "text"}:
        payload["content"] = None
        payload["content_encoding"] = None
        return payload

    file_path = Path(str(payload["stored_path"]))
    if not file_path.exists():
        raise HTTPException(404, "File not found on disk")

    content, encoding = await load_text_content(
        file_path,
        binary_error_message="当前文件不是可预览的文本文件",
        encoding_error_message="当前文本文件编码暂不支持在线预览",
    )
    payload["content"] = content
    payload["content_encoding"] = encoding
    return payload
