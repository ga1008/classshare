from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from ..database import get_db_connection
from ..dependencies import get_current_user
from ..services.file_service import global_file_write_path, resolve_global_file_path
from ..services.message_center_service import create_app_feedback_notifications, is_super_admin_teacher

router = APIRouter()

MAX_FEEDBACK_ATTACHMENTS = 5
MAX_ATTACHMENT_SIZE_MB = 10
MAX_ATTACHMENT_SIZE_BYTES = MAX_ATTACHMENT_SIZE_MB * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp"}
MAX_TITLE_LENGTH = 200
MAX_DESCRIPTION_LENGTH = 5000
MAX_SECTION_LENGTH = 120
MAX_PAGE_URL_LENGTH = 1000
MAX_FILENAME_LENGTH = 240


def _clean_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_upload_filename(filename: str | None) -> str:
    cleaned = _clean_text(filename).replace("\\", "/").rsplit("/", 1)[-1]
    if not cleaned:
        return "feedback-image"
    return cleaned[:MAX_FILENAME_LENGTH]


@router.post("/api/feedback")
async def submit_feedback(request: Request, user: dict = Depends(get_current_user)):
    """Submit a new bug report or feature request."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "无法解析请求体。")

    if not isinstance(body, dict):
        raise HTTPException(400, "请求体格式无效。")

    feedback_type = _clean_text(body.get("feedback_type")).lower()
    if feedback_type not in ("bug", "feature", "report"):
        raise HTTPException(400, '反馈类型无效，请选择“有bug”“新功能”或“举报”。')

    title = _clean_text(body.get("title"))
    if not title:
        raise HTTPException(400, "请填写标题。")
    if len(title) > MAX_TITLE_LENGTH:
        raise HTTPException(400, f"标题过长，请控制在{MAX_TITLE_LENGTH}字以内。")

    description = _clean_text(body.get("description"))
    if not description:
        raise HTTPException(400, "请填写描述。")
    if len(description) > MAX_DESCRIPTION_LENGTH:
        raise HTTPException(400, f"描述过长，请控制在{MAX_DESCRIPTION_LENGTH}字以内。")

    section = _clean_text(body.get("section"))[:MAX_SECTION_LENGTH]
    page_url = _clean_text(body.get("page_url"))[:MAX_PAGE_URL_LENGTH]

    with get_db_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO app_feedback (user_id, user_role, user_name, feedback_type, section, title, description, page_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(user["id"]),
                _clean_text(user.get("role")),
                _clean_text(user.get("name")),
                feedback_type,
                section,
                title,
                description,
                page_url,
            ),
        )
        feedback_id = cursor.lastrowid
        notification_count = create_app_feedback_notifications(conn, feedback_id)
        conn.commit()

    return JSONResponse(
        {
            "success": True,
            "feedback_id": feedback_id,
            "notification_count": notification_count,
            "message": "反馈提交成功，感谢您的宝贵意见！",
        },
        status_code=201,
    )


@router.post("/api/feedback/{feedback_id}/upload")
async def upload_feedback_attachment(
    feedback_id: int,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    """Upload an attachment (screenshot/image) for a feedback submission."""
    with get_db_connection() as conn:
        feedback = conn.execute(
            "SELECT id, user_id FROM app_feedback WHERE id = ?",
            (feedback_id,),
        ).fetchone()

    if not feedback:
        raise HTTPException(404, "反馈记录不存在。")
    if str(feedback["user_id"]) != str(user["id"]):
        raise HTTPException(403, "无权为此反馈上传附件。")

    if not file.filename:
        raise HTTPException(400, "请选择文件。")

    content_type = (file.content_type or "").split(";", 1)[0].strip().lower()
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(400, "仅支持 PNG、JPEG、GIF、WebP、BMP 格式的图片。")

    with get_db_connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS cnt FROM app_feedback_attachments WHERE feedback_id = ?",
            (feedback_id,),
        ).fetchone()
        if count and count["cnt"] >= MAX_FEEDBACK_ATTACHMENTS:
            raise HTTPException(400, f"每个反馈最多上传 {MAX_FEEDBACK_ATTACHMENTS} 个附件。")

    content = await file.read(MAX_ATTACHMENT_SIZE_BYTES + 1)
    if not content:
        raise HTTPException(400, "文件内容为空。")
    if len(content) > MAX_ATTACHMENT_SIZE_BYTES:
        raise HTTPException(400, f"文件大小不能超过 {MAX_ATTACHMENT_SIZE_MB}MB。")

    import hashlib

    file_hash = hashlib.sha256(content).hexdigest()
    file_path = global_file_write_path(file_hash)

    file_size = len(content)
    original_filename = _normalize_upload_filename(file.filename)

    file_path.parent.mkdir(parents=True, exist_ok=True)
    if not file_path.exists():
        file_path.write_bytes(content)

    with get_db_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO app_feedback_attachments (feedback_id, file_hash, original_filename, file_size, mime_type)
            VALUES (?, ?, ?, ?, ?)
            """,
            (feedback_id, file_hash, original_filename, file_size, content_type),
        )
        conn.commit()
        attachment_id = cursor.lastrowid

    return JSONResponse(
        {
            "success": True,
            "attachment_id": attachment_id,
            "file_hash": file_hash,
            "original_filename": original_filename,
            "file_size": file_size,
            "mime_type": content_type,
            "message": "附件上传成功。",
        },
        status_code=201,
    )


@router.get(
    "/api/feedback/{feedback_id}/attachment/{file_hash}",
    response_class=FileResponse,
)
async def serve_feedback_attachment(
    feedback_id: int,
    file_hash: str,
    user: dict = Depends(get_current_user),
):
    """Serve a feedback attachment image."""
    with get_db_connection() as conn:
        attachment = conn.execute(
            """
            SELECT a.original_filename, a.mime_type, a.file_hash,
                   f.user_id, f.user_role
            FROM app_feedback_attachments a
            JOIN app_feedback f ON f.id = a.feedback_id
            WHERE a.feedback_id = ? AND a.file_hash = ?
            LIMIT 1
            """,
            (feedback_id, file_hash),
        ).fetchone()
        can_view = bool(attachment) and (
            str(attachment["user_id"]) == str(user["id"])
            or (
                user.get("role") == "teacher"
                and is_super_admin_teacher(conn, user.get("id"))
            )
        )

    if not attachment or not can_view:
        raise HTTPException(404, "附件不存在。")

    file_path = resolve_global_file_path(attachment["file_hash"])
    if not file_path:
        raise HTTPException(404, "附件文件不存在。")

    return FileResponse(
        file_path,
        media_type=attachment["mime_type"] or "application/octet-stream",
        filename=attachment["original_filename"],
        headers={"Cache-Control": "private, max-age=86400"},
    )
