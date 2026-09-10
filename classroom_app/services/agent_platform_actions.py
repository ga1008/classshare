"""Compatibility helpers used by reviewed operation adapters; no task executor."""
from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException


def _safe_text(value: Any, *, max_chars: int = 0) -> str:
    text = str(value or "").replace("\r\n", "\n").strip()
    if max_chars > 0 and len(text) > max_chars:
        return text[:max_chars].rstrip()
    return text


def _load_teacher_blog_profile(conn, teacher_id: int) -> dict[str, Any]:
    teacher = conn.execute(
        """
        SELECT id, name, nickname, avatar_file_hash, avatar_mime_type, is_active
        FROM teachers
        WHERE id = ?
        LIMIT 1
        """,
        (int(teacher_id),),
    ).fetchone()
    if not teacher:
        raise HTTPException(404, "教师账户不存在，无法创建博客内容。")
    user = dict(teacher)
    if not user.get("is_active", 1):
        raise HTTPException(status_code=403, detail="教师账户已停用，无法操作博客内容。")
    user["role"] = "teacher"
    return user


def _create_teacher_blog_post(
    conn,
    *,
    teacher_id: int,
    title: str,
    content_md: str,
    tags: list[str],
    status: str = "draft",
    visibility: Any = "public",
    visible_class_id: Any = None,
) -> dict[str, Any]:
    from .blog_service import create_post

    user = _load_teacher_blog_profile(conn, teacher_id)
    normalized_status = "published" if str(status or "").lower() == "published" else "draft"
    try:
        return create_post(
            conn, user, title=title, content_md=content_md, tags=tags,
            status=normalized_status, visibility=visibility, visible_class_id=visible_class_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _create_teacher_blog_draft(conn, *, teacher_id: int, title: str, content_md: str, tags: list[str]) -> dict[str, Any]:
    return _create_teacher_blog_post(
        conn,
        teacher_id=int(teacher_id),
        title=title,
        content_md=content_md,
        tags=tags,
        status="draft",
        visibility="public",
    )


def _create_teacher_blog_comment(
    conn,
    *,
    teacher_id: int,
    post_id: int,
    content_md: str,
    parent_comment_id: Any = None,
) -> dict[str, Any]:
    from .blog_notifications import notify_new_comment, notify_post_hot
    from .blog_service import add_comment

    user = _load_teacher_blog_profile(conn, teacher_id)
    try:
        parent_id = int(parent_comment_id) if parent_comment_id not in (None, "", 0) else None
        return add_comment(
            conn, user, int(post_id), content_md=content_md, parent_comment_id=parent_id,
            notify_callback=notify_new_comment, hot_notify_callback=notify_post_hot,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _instruction_is_exam_like(text: str) -> bool:
    return bool(re.search(r"(考试|试卷|测验|随堂测|quiz|exam)", _safe_text(text), flags=re.IGNORECASE))


def _create_assignment_draft(
    conn,
    *,
    course_id: int,
    class_offering_id: int,
    title: str,
    requirements_md: str,
    rubric_md: str,
) -> dict[str, Any]:
    from .assignment_creation_service import create_assignment_record

    offering = conn.execute("SELECT teacher_id FROM class_offerings WHERE id=? AND course_id=?", (int(class_offering_id), int(course_id))).fetchone()
    if not offering:
        raise HTTPException(404, "课堂不存在")
    created = create_assignment_record(conn, teacher_id=int(offering["teacher_id"]), course_id=int(course_id), data={
        "class_offering_id": int(class_offering_id), "title": _safe_text(title, max_chars=120) or "Agent 作业草稿",
        "requirements_md": requirements_md, "rubric_md": rubric_md, "status": "new", "grading_mode": "manual",
    })
    # Actual submission writes create their storage directory lazily. A draft
    # and its Agent receipt therefore have no filesystem side effect to undo.
    return {"id": created["id"], "status": "new", "created_at": created["created_at"], "url": f"/assignment/{created['id']}",
            "classification": created["classification"]}
