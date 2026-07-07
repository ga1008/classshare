"""Shared prompt pool API for reusable non-chat AI prompt inputs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from ..database import get_db_connection
from ..dependencies import get_current_user
from ..services import prompt_pool_service as pool

router = APIRouter(prefix="/api/prompt-pool")


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(400, "请求 JSON 格式不正确") from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, "请求体必须是 JSON 对象")
    return payload


@router.get("", response_class=JSONResponse)
async def list_prompt_pool(
    feature_key: str = Query(..., min_length=2, max_length=100),
    q: str = Query("", max_length=200),
    limit: int = Query(20, ge=1, le=20),
    user: dict = Depends(get_current_user),
):
    del user
    try:
        with get_db_connection() as conn:
            prompts = pool.search_prompts(conn, feature_key, q, limit=limit)
    except ValueError as exc:
        raise HTTPException(400, "提示词功能范围不正确") from exc
    return {"prompts": prompts}


@router.post("/record", response_class=JSONResponse)
async def record_prompt(request: Request, user: dict = Depends(get_current_user)):
    del user
    body = await _json_body(request)
    try:
        with get_db_connection() as conn:
            prompt = pool.record_prompt(conn, body.get("feature_key"), body.get("prompt"))
            conn.commit()
    except ValueError as exc:
        raise HTTPException(400, "提示词功能范围不正确") from exc
    return {"ok": True, "prompt": prompt}
