from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image, UnidentifiedImageError

from ..core import templates
from ..database import get_db_connection
from ..dependencies import get_current_user
from ..services.blog_ai_service import maybe_reply_to_comment_mention, maybe_reply_to_post_mention
from ..services.blog_notifications import notify_new_comment, notify_post_featured, notify_post_hot
from ..services.blog_service import (
    POST_STATUS_DRAFT,
    POST_STATUS_PUBLISHED,
    VISIBILITY_PUBLIC,
    add_comment,
    create_post,
    delete_comment,
    delete_post,
    feature_post,
    get_bookmarked_posts,
    get_media_asset_for_user,
    get_my_posts,
    get_post_detail,
    hide_post,
    list_attachments,
    list_available_custom_emojis,
    list_comments,
    list_posts,
    pin_post,
    register_media_asset,
    toggle_bookmark,
    toggle_comments,
    toggle_like,
    update_post,
)
from ..services.file_service import resolve_global_file_path, save_file_globally

router = APIRouter()

ALLOWED_BLOG_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
ALLOWED_BLOG_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
MAX_BLOG_IMAGE_BYTES = 10 * 1024 * 1024


def _build_background_user(user: dict) -> dict:
    return {
        "id": user.get("id"),
        "role": user.get("role", ""),
        "name": user.get("name", ""),
        "nickname": user.get("nickname", ""),
    }


def _build_blog_user_info(conn, user: dict) -> dict:
    role = str(user.get("role") or "").strip().lower()
    user_pk = user.get("id")
    profile = None
    if role == "student":
        profile = conn.execute(
            "SELECT name, nickname FROM students WHERE id = ? LIMIT 1",
            (user_pk,),
        ).fetchone()
    elif role == "teacher":
        profile = conn.execute(
            "SELECT name, nickname FROM teachers WHERE id = ? LIMIT 1",
            (user_pk,),
        ).fetchone()

    name = str(profile["name"] or "") if profile else str(user.get("name") or "")
    nickname = str(profile["nickname"] or "") if profile else str(user.get("nickname") or "")
    return {
        "id": user_pk,
        "name": name,
        "role": role,
        "nickname": nickname,
        "identity": f"{role}:{user_pk}",
    }


@router.get("/blog")
async def blog_page(request: Request, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        user_info = _build_blog_user_info(conn, user)
    return templates.TemplateResponse(
        request,
        "blog.html",
        {
            "request": request,
            "user_info": user_info,
        },
    )


@router.get("/api/blog/posts", response_class=JSONResponse)
def api_list_posts(
    sort: str = Query(default="latest"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=50),
    author: Optional[str] = Query(default=None),
    tag: Optional[str] = Query(default=None),
    user: dict = Depends(get_current_user),
):
    with get_db_connection() as conn:
        result = list_posts(conn, user, sort=sort, page=page, limit=limit, author_identity=author, tag=tag)
        return {"status": "success", **result}


@router.get("/api/blog/posts/{post_id}", response_class=JSONResponse)
def api_get_post(post_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        try:
            post = get_post_detail(conn, user, post_id)
            post["attachments"] = list_posts_attachments(conn, post_id)
            comments_data = list_comments(conn, user, post_id, page=1, limit=100)
            post["_comments"] = comments_data.get("comments", [])
            conn.commit()
            return {"status": "success", "post": post}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/api/blog/posts", response_class=JSONResponse)
async def api_create_post(
    request: Request,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
):
    data = await request.json()
    title = str(data.get("title") or "").strip()
    content_md = str(data.get("content_md") or "").strip()
    if not title or not content_md:
        raise HTTPException(status_code=400, detail="标题和内容不能为空")

    with get_db_connection() as conn:
        try:
            result = create_post(
                conn,
                user,
                title=title,
                content_md=content_md,
                author_display_mode=str(data.get("author_display_mode") or "real_name"),
                visibility=str(data.get("visibility") or VISIBILITY_PUBLIC),
                visible_class_id=data.get("visible_class_id"),
                visible_user_identities=data.get("visible_user_identities"),
                allow_comments=bool(data.get("allow_comments", True)),
                tags=data.get("tags"),
                status=str(data.get("status") or POST_STATUS_PUBLISHED),
            )
            conn.commit()
            background_tasks.add_task(maybe_reply_to_post_mention, int(result["id"]), _build_background_user(user))
            return {"status": "success", **result}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.put("/api/blog/posts/{post_id}", response_class=JSONResponse)
async def api_update_post(
    post_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
):
    data = await request.json()
    with get_db_connection() as conn:
        try:
            result = update_post(
                conn,
                user,
                post_id,
                title=data.get("title"),
                content_md=data.get("content_md"),
                author_display_mode=data.get("author_display_mode"),
                visibility=data.get("visibility"),
                visible_class_id=data.get("visible_class_id"),
                visible_user_identities=data.get("visible_user_identities"),
                allow_comments=data.get("allow_comments"),
                tags=data.get("tags"),
                status=data.get("status"),
            )
            conn.commit()
            background_tasks.add_task(maybe_reply_to_post_mention, post_id, _build_background_user(user))
            return {"status": "success", **result}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.delete("/api/blog/posts/{post_id}", response_class=JSONResponse)
def api_delete_post(post_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        try:
            result = delete_post(conn, user, post_id)
            conn.commit()
            return {"status": "success", **result}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/api/blog/posts/{post_id}/pin", response_class=JSONResponse)
def api_pin_post(post_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        try:
            result = pin_post(conn, user, post_id)
            conn.commit()
            return {"status": "success", **result}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/api/blog/posts/{post_id}/feature", response_class=JSONResponse)
def api_feature_post(post_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        try:
            result = feature_post(conn, user, post_id, notify_callback=notify_post_featured)
            conn.commit()
            return {"status": "success", **result}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/api/blog/posts/{post_id}/hide", response_class=JSONResponse)
async def api_hide_post(post_id: int, request: Request, user: dict = Depends(get_current_user)):
    data = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    with get_db_connection() as conn:
        try:
            result = hide_post(conn, user, post_id, reason=str(data.get("reason") or ""))
            conn.commit()
            return {"status": "success", **result}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/api/blog/posts/{post_id}/comments-toggle", response_class=JSONResponse)
def api_toggle_comments(post_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        try:
            result = toggle_comments(conn, user, post_id)
            conn.commit()
            return {"status": "success", **result}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/api/blog/posts/{post_id}/comments", response_class=JSONResponse)
def api_list_comments(
    post_id: int,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    with get_db_connection() as conn:
        try:
            result = list_comments(conn, user, post_id, page=page, limit=limit)
            return {"status": "success", **result}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/api/blog/posts/{post_id}/comments", response_class=JSONResponse)
async def api_add_comment(
    post_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
):
    data = await request.json()
    with get_db_connection() as conn:
        try:
            result = add_comment(
                conn,
                user,
                post_id,
                content_md=str(data.get("content_md") or ""),
                parent_comment_id=data.get("parent_comment_id"),
                emoji_payload_json=str(data.get("emoji_payload_json") or ""),
                attachments_json=str(data.get("attachments_json") or "[]"),
                notify_callback=notify_new_comment,
                hot_notify_callback=notify_post_hot,
            )
            conn.commit()
            background_tasks.add_task(maybe_reply_to_comment_mention, int(result["id"]), _build_background_user(user))
            return {"status": "success", **result}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.delete("/api/blog/comments/{comment_id}", response_class=JSONResponse)
def api_delete_comment(comment_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        try:
            result = delete_comment(conn, user, comment_id)
            conn.commit()
            return {"status": "success", **result}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/api/blog/posts/{post_id}/like", response_class=JSONResponse)
def api_like_post(post_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        try:
            result = toggle_like(conn, user, "post", post_id, hot_notify_callback=notify_post_hot)
            conn.commit()
            return {"status": "success", **result}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/api/blog/comments/{comment_id}/like", response_class=JSONResponse)
def api_like_comment(comment_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        try:
            result = toggle_like(conn, user, "comment", comment_id)
            conn.commit()
            return {"status": "success", **result}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/api/blog/posts/{post_id}/bookmark", response_class=JSONResponse)
def api_bookmark_post(post_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        try:
            result = toggle_bookmark(conn, user, post_id)
            conn.commit()
            return {"status": "success", **result}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/api/blog/upload-image", response_class=JSONResponse)
async def api_upload_image(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    content_type = str(file.content_type or "").lower()
    suffix = Path(file.filename or "").suffix.lower()
    if content_type not in ALLOWED_BLOG_IMAGE_TYPES and suffix not in ALLOWED_BLOG_IMAGE_EXTS:
        raise HTTPException(status_code=400, detail="仅支持 PNG、JPG、GIF 或 WebP 图片")

    await file.seek(0)
    file.file.seek(0, 2)
    file_size = int(file.file.tell())
    await file.seek(0)
    if file_size <= 0:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if file_size > MAX_BLOG_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="图片大小不能超过 10MB")

    save_result = await save_file_globally(file)
    if not save_result:
        raise HTTPException(status_code=500, detail="图片保存失败")

    saved_path = Path(save_result["path"])
    try:
        with Image.open(saved_path) as image:
            image.load()
            width, height = image.size
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(status_code=400, detail="上传文件不是有效图片") from exc

    with get_db_connection() as conn:
        asset = register_media_asset(
            conn,
            user,
            file_hash=str(save_result["hash"]),
            filename=str(file.filename or save_result["hash"]),
            mime_type=content_type if content_type in ALLOWED_BLOG_IMAGE_TYPES else "application/octet-stream",
            file_size=int(save_result["size"] or 0),
            image_width=int(width or 0),
            image_height=int(height or 0),
        )
        conn.commit()

    return {
        "status": "success",
        "file": {
            "hash": asset["file_hash"],
            "file_hash": asset["file_hash"],
            "filename": asset["original_filename"],
            "name": asset["original_filename"],
            "mime_type": asset["mime_type"],
            "size": int(asset["file_size"] or 0),
            "file_size": int(asset["file_size"] or 0),
            "width": int(asset["image_width"] or 0),
            "height": int(asset["image_height"] or 0),
            "url": f"/api/blog/image/{asset['file_hash']}",
        },
    }


@router.get("/api/blog/image/{file_hash}")
async def api_get_blog_image(file_hash: str, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        asset = get_media_asset_for_user(conn, user, file_hash)
    if asset is None:
        raise HTTPException(status_code=404, detail="图片不存在或不可访问")

    file_path = resolve_global_file_path(str(file_hash or "").strip().lower())
    if not file_path:
        raise HTTPException(status_code=404, detail="图片不存在")

    return FileResponse(
        str(file_path),
        media_type=str(asset.get("mime_type") or "application/octet-stream"),
        filename=str(asset.get("original_filename") or file_hash),
    )


@router.get("/api/blog/custom-emojis", response_class=JSONResponse)
def api_custom_emojis(
    limit: int = Query(default=60, ge=1, le=120),
    user: dict = Depends(get_current_user),
):
    with get_db_connection() as conn:
        emojis = list_available_custom_emojis(conn, user, limit=limit)
        conn.commit()
    return {"status": "success", "emojis": emojis}


@router.get("/api/blog/my-posts", response_class=JSONResponse)
def api_my_posts(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=50),
    status: Optional[str] = Query(default=None),
    user: dict = Depends(get_current_user),
):
    with get_db_connection() as conn:
        result = get_my_posts(conn, user, page=page, limit=limit, status_filter=status)
        return {"status": "success", **result}


@router.get("/api/blog/bookmarks", response_class=JSONResponse)
def api_bookmarks(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=50),
    user: dict = Depends(get_current_user),
):
    with get_db_connection() as conn:
        result = get_bookmarked_posts(conn, user, page=page, limit=limit)
        return {"status": "success", **result}


@router.get("/api/blog/user-classes", response_class=JSONResponse)
def api_user_classes(user: dict = Depends(get_current_user)):
    role = str(user.get("role") or "").strip().lower()
    user_pk = user.get("id")
    with get_db_connection() as conn:
        if role == "student":
            rows = conn.execute(
                """
                SELECT DISTINCT c.id, c.name
                FROM students s
                JOIN classes c ON c.id = s.class_id
                WHERE s.id = ?
                ORDER BY c.name
                """,
                (user_pk,),
            ).fetchall()
        elif role == "teacher":
            rows = conn.execute(
                """
                SELECT DISTINCT c.id, c.name
                FROM classes c
                WHERE c.created_by_teacher_id = ?
                ORDER BY c.name
                """,
                (user_pk,),
            ).fetchall()
        else:
            rows = []
    return {"status": "success", "classes": [{"id": int(row["id"]), "name": str(row["name"] or "")} for row in rows]}


@router.get("/api/blog/users-search", response_class=JSONResponse)
def api_users_search(
    q: str = Query(default="", min_length=1),
    class_id: Optional[int] = Query(default=None),
    user: dict = Depends(get_current_user),
):
    like_q = f"%{q.strip()}%"
    with get_db_connection() as conn:
        students = conn.execute(
            """
            SELECT s.id, s.name, s.nickname, 'student' AS role, c.name AS class_name
            FROM students s
            JOIN classes c ON c.id = s.class_id
            WHERE (s.name LIKE ? OR s.nickname LIKE ? OR s.student_id_number LIKE ?)
              AND (? IS NULL OR s.class_id = ?)
            ORDER BY s.name
            LIMIT 20
            """,
            (like_q, like_q, like_q, class_id, class_id),
        ).fetchall()
        teachers = conn.execute(
            """
            SELECT t.id, t.name, t.nickname, 'teacher' AS role, '' AS class_name
            FROM teachers t
            WHERE (t.name LIKE ? OR t.nickname LIKE ?)
            ORDER BY t.name
            LIMIT 10
            """,
            (like_q, like_q),
        ).fetchall()

    users = []
    for row in [*students, *teachers]:
        identity = f"{row['role']}:{row['id']}"
        if identity == f"{user.get('role')}:{user.get('id')}":
            continue
        users.append(
            {
                "identity": identity,
                "name": str(row["name"] or ""),
                "nickname": str(row["nickname"] or ""),
                "role": str(row["role"] or ""),
                "role_label": "教师" if row["role"] == "teacher" else "学生",
                "class_name": str(row["class_name"] or ""),
            }
        )
    return {"status": "success", "users": users}


def list_posts_attachments(conn, post_id: int) -> list[dict]:
    raw_items = list_attachments(conn, post_id)
    return [
        {
            "id": int(item["id"]),
            "url": f"/api/blog/image/{item['file_hash']}",
            "filename": str(item["original_filename"] or ""),
            "mime_type": str(item["mime_type"] or "application/octet-stream"),
            "size": int(item["file_size"] or 0),
            "width": int(item["image_width"] or 0),
            "height": int(item["image_height"] or 0),
        }
        for item in raw_items
    ]
