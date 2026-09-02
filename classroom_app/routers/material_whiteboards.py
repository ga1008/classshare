"""Material whiteboard API (材料白板): per-teacher boards on top of a material.

Prefix ``/api/materials/{material_id}/whiteboards``. Error mapping:
400 validation, 404 missing board, 409 version conflict (with server copy),
413 oversized payload. Role / material access failures raise 403 from the
service (``app.py`` rewrites 403 to 401 for unauthenticated API calls).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ..database import get_db_connection
from ..dependencies import get_current_user
from ..services import material_whiteboard_service as svc


router = APIRouter(prefix="/api/materials/{material_id}/whiteboards")


async def _json_payload(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(400, "请求 JSON 格式不正确") from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, "请求体必须是 JSON 对象")
    return payload


def _conflict_response(exc: svc.WhiteboardConflict) -> JSONResponse:
    return JSONResponse(
        {"status": "conflict", "detail": "白板已在其他地方更新，请刷新后再保存", "board": exc.board},
        status_code=409,
    )


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, svc.WhiteboardTooLarge):
        return HTTPException(413, str(exc))
    if isinstance(exc, svc.WhiteboardValidationError):
        return HTTPException(400, str(exc))
    if isinstance(exc, svc.WhiteboardNotFound):
        return HTTPException(404, str(exc))
    raise exc


_SERVICE_ERRORS = (svc.WhiteboardValidationError, svc.WhiteboardNotFound)


@router.get("", response_class=JSONResponse)
async def list_material_whiteboards(material_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        boards = svc.list_boards(conn, user, material_id)
    return {"status": "ok", "boards": boards}


@router.get("/{board_key}", response_class=JSONResponse)
async def get_material_whiteboard(material_id: int, board_key: str, user: dict = Depends(get_current_user)):
    try:
        with get_db_connection() as conn:
            board = svc.get_board(conn, user, material_id, board_key)
    except _SERVICE_ERRORS as exc:
        raise _translate(exc) from exc
    return {"status": "ok", "board": board}


@router.put("/{board_key}", response_class=JSONResponse)
async def save_material_whiteboard(
    material_id: int,
    board_key: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    payload = await _json_payload(request)
    try:
        with get_db_connection() as conn:
            board = svc.upsert_board(
                conn, user, material_id, board_key, payload, payload.get("base_version")
            )
    except svc.WhiteboardConflict as exc:
        return _conflict_response(exc)
    except _SERVICE_ERRORS as exc:
        raise _translate(exc) from exc
    return {"status": "ok", "board": board}


@router.patch("/{board_key}", response_class=JSONResponse)
async def rename_material_whiteboard(
    material_id: int,
    board_key: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    payload = await _json_payload(request)
    try:
        with get_db_connection() as conn:
            board = svc.rename_board(conn, user, material_id, board_key, payload.get("name"))
    except _SERVICE_ERRORS as exc:
        raise _translate(exc) from exc
    return {"status": "ok", "board": board}


@router.delete("/{board_key}", response_class=JSONResponse)
async def delete_material_whiteboard(material_id: int, board_key: str, user: dict = Depends(get_current_user)):
    try:
        with get_db_connection() as conn:
            result = svc.delete_board(conn, user, material_id, board_key)
    except _SERVICE_ERRORS as exc:
        raise _translate(exc) from exc
    return {"status": "ok", **result}
