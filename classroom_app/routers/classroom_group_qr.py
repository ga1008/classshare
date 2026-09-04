"""Authorized group QR display and teacher-only editing for a classroom."""
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from ..database import get_db_connection
from ..dependencies import get_current_user
from ..services.classroom_group_qr_service import (
    IMAGE_EXTENSIONS,
    load_group_qr_offering,
    serialize_group_qr,
    update_group_qr,
)
from ..services.file_service import resolve_global_file_path

router = APIRouter(prefix="/api/classrooms/{class_offering_id}/group-qr")


@router.get("")
def get_group_qr(class_offering_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        payload = serialize_group_qr(load_group_qr_offering(conn, class_offering_id, user))
    return JSONResponse(payload, headers={"Cache-Control": "private, no-store"})


@router.post("")
def save_group_qr(
    class_offering_id: int,
    description: str = Form(""),
    revision: str = Form(""),
    file: UploadFile | None = File(None),
    remove_image: bool = Form(False),
    user: dict = Depends(get_current_user),
):
    # A sync route keeps image decoding / disk and DB I/O off the event loop.
    with get_db_connection() as conn:
        payload = update_group_qr(
            conn, class_offering_id, user, description=description, revision=revision, file=file,
            remove_image=remove_image,
        )
        conn.commit()
    return JSONResponse(payload, headers={"Cache-Control": "private, no-store"})


@router.get("/image")
def get_group_qr_image(
    class_offering_id: int,
    download: bool = False,
    user: dict = Depends(get_current_user),
):
    with get_db_connection() as conn:
        offering = load_group_qr_offering(conn, class_offering_id, user)
    file_hash = str(offering.get("group_qr_file_hash") or "")
    mime_type = offering.get("group_qr_mime_type") or ""
    # Never resolve malformed legacy metadata into an arbitrary storage path.
    if len(file_hash) != 64 or any(char not in "0123456789abcdef" for char in file_hash):
        raise HTTPException(404, "该课堂尚未设置二维码，或图片已不可用。")
    path = resolve_global_file_path(file_hash)
    if path is None or mime_type not in IMAGE_EXTENSIONS:
        raise HTTPException(404, "该课堂尚未设置二维码，或图片已不可用。")
    filename = f"classroom-{class_offering_id}-group-qr.{IMAGE_EXTENSIONS[mime_type]}" if download else None
    return FileResponse(
        path, media_type=mime_type, filename=filename,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )
