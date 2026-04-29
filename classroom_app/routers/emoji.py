from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from ..database import get_db_connection
from ..dependencies import get_current_user
from ..services.emoji_service import (
    MAX_CUSTOM_EMOJIS_PER_USER,
    MAX_CUSTOM_EMOJI_BYTES,
    build_custom_emoji_url,
    get_custom_emoji_path,
    load_custom_emojis_for_user,
    load_frequent_emojis,
    make_unique_custom_emoji_name,
    sanitize_custom_emoji_name,
    serialize_custom_emoji_row,
    validate_and_store_custom_emoji,
)
from ..services.materials_service import ensure_classroom_access

router = APIRouter()


@router.get("/api/classrooms/{class_offering_id}/emoji-panel")
async def get_emoji_panel_data(class_offering_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        ensure_classroom_access(conn, class_offering_id, user)
        return {
            "emoji_set": {
                "name": "Twemoji",
                "license": "CC-BY 4.0",
                "attribution": "Twemoji by Twitter/X, graphics licensed under CC-BY 4.0.",
            },
            "frequent": load_frequent_emojis(conn, class_offering_id, user),
            "custom_emojis": load_custom_emojis_for_user(conn, class_offering_id, user),
            "limits": {
                "max_upload_bytes": MAX_CUSTOM_EMOJI_BYTES,
                "max_upload_mb": MAX_CUSTOM_EMOJI_BYTES // (1024 * 1024),
                "max_custom_emoji_count": MAX_CUSTOM_EMOJIS_PER_USER,
            },
        }


@router.post("/api/classrooms/{class_offering_id}/custom-emojis")
async def upload_custom_emoji(
    class_offering_id: int,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    with get_db_connection() as conn:
        ensure_classroom_access(conn, class_offering_id, user)

    stored_file = await validate_and_store_custom_emoji(file)

    with get_db_connection() as conn:
        ensure_classroom_access(conn, class_offering_id, user)
        existing = conn.execute(
            """
            SELECT *
            FROM custom_emojis
            WHERE class_offering_id = ?
              AND owner_user_id = ?
              AND owner_user_role = ?
              AND file_hash = ?
            LIMIT 1
            """,
            (class_offering_id, int(user["id"]), user["role"], stored_file["hash"]),
        ).fetchone()
        if existing:
            return {
                "created": False,
                "deduplicated": True,
                "message": "这张表情已经在你的表情库中了。",
                "emoji": serialize_custom_emoji_row(class_offering_id, existing),
            }

        display_name = make_unique_custom_emoji_name(
            conn,
            class_offering_id,
            int(user["id"]),
            user["role"],
            sanitize_custom_emoji_name(file.filename or "emoji"),
        )

        cursor = conn.execute(
            """
            INSERT INTO custom_emojis
            (
                class_offering_id,
                owner_user_id,
                owner_user_role,
                display_name,
                original_filename,
                file_hash,
                mime_type,
                file_size,
                image_width,
                image_height
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                class_offering_id,
                int(user["id"]),
                user["role"],
                display_name,
                file.filename or display_name,
                stored_file["hash"],
                stored_file["mime_type"],
                stored_file["size"],
                stored_file["width"],
                stored_file["height"],
            ),
        )
        conn.commit()

        created = conn.execute(
            "SELECT * FROM custom_emojis WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()

    return {
        "created": True,
        "deduplicated": False,
        "message": "自定义表情上传成功。",
        "emoji": serialize_custom_emoji_row(class_offering_id, created),
    }


@router.get(
    "/api/classrooms/{class_offering_id}/custom-emojis/{emoji_id}/file",
    response_class=FileResponse,
)
async def get_custom_emoji_file(
    class_offering_id: int,
    emoji_id: int,
    user: dict = Depends(get_current_user),
):
    with get_db_connection() as conn:
        ensure_classroom_access(conn, class_offering_id, user)
        emoji_row = conn.execute(
            """
            SELECT *
            FROM custom_emojis
            WHERE class_offering_id = ? AND id = ?
            LIMIT 1
            """,
            (class_offering_id, emoji_id),
        ).fetchone()

    if not emoji_row:
        raise HTTPException(404, "表情不存在。")

    file_path = get_custom_emoji_path(emoji_row["file_hash"])
    return FileResponse(
        file_path,
        media_type=emoji_row["mime_type"],
        filename=emoji_row["original_filename"],
        headers={"Cache-Control": "private, max-age=86400"},
    )
