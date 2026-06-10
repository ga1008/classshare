"""
Agent 桥接 API —— 独立运行时里的 Agent 通过本接口把平台当工具用（全部只读）。

鉴权：Authorization: Bearer <task 专属 HMAC token>（由 agent_task_worker 在任务
启动时写入 workspace）。token 自带过期时间，任务结束后自然失效。
"""
from __future__ import annotations

from typing import Any, Optional

import httpx
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from ..database import get_db_connection
from ..db.connection import get_configured_db_engine
from ..services.agent_bridge_service import (
    MAX_WEB_BYTES,
    assert_public_http_url,
    describe_schema,
    read_platform_file,
    run_readonly_query,
    strip_html_to_text,
    verify_bridge_token,
)
from ..services.platform_knowledge_service import (
    build_platform_overview_block,
    build_user_knowledge_block,
)

router = APIRouter(prefix="/api/agent-bridge", tags=["agent-bridge"])


def _require_task_id(authorization: str) -> int:
    token = str(authorization or "")
    if token.lower().startswith("bearer "):
        token = token[7:]
    task_id = verify_bridge_token(token.strip())
    if not task_id:
        raise HTTPException(status_code=401, detail="桥接 token 无效或已过期。")
    return task_id


def _load_task_owner(task_id: int) -> dict[str, Any]:
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT id, teacher_id, status FROM agent_tasks WHERE id = ? LIMIT 1",
            (int(task_id),),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="任务不存在。")
    return dict(row)


class BridgeQueryPayload(BaseModel):
    sql: str = Field(..., max_length=8000)
    limit: int = Field(default=200, ge=1, le=200)


class BridgeFilePayload(BaseModel):
    path: str = Field(..., max_length=2000)


class BridgeWebPayload(BaseModel):
    url: str = Field(..., max_length=2000)
    mode: str = Field(default="text")  # text=去标签正文, raw=原始响应体


@router.get("/meta")
async def bridge_meta(authorization: Optional[str] = Header(default="")):
    task_id = _require_task_id(authorization)
    task = _load_task_owner(task_id)
    with get_db_connection() as conn:
        user_block = build_user_knowledge_block(conn, int(task["teacher_id"]), "teacher")
    return {
        "status": "success",
        "task_id": task_id,
        "platform_overview": build_platform_overview_block("teacher"),
        "task_owner_profile": user_block,
    }


@router.get("/schema")
async def bridge_schema(authorization: Optional[str] = Header(default="")):
    _require_task_id(authorization)
    engine = get_configured_db_engine()
    with get_db_connection() as conn:
        tables = describe_schema(conn, engine)
    return {"status": "success", "engine": engine, "tables": tables}


@router.post("/query")
async def bridge_query(payload: BridgeQueryPayload, authorization: Optional[str] = Header(default="")):
    task_id = _require_task_id(authorization)
    _load_task_owner(task_id)
    try:
        with get_db_connection() as conn:
            result = run_readonly_query(conn, payload.sql, payload.limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"查询执行失败：{exc}")
    return {"status": "success", **result}


@router.post("/file")
async def bridge_file(payload: BridgeFilePayload, authorization: Optional[str] = Header(default="")):
    _require_task_id(authorization)
    try:
        result = read_platform_file(payload.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "success", **result}


@router.post("/web")
async def bridge_web(payload: BridgeWebPayload, authorization: Optional[str] = Header(default="")):
    _require_task_id(authorization)
    try:
        url = assert_public_http_url(payload.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=25.0,
            headers={"User-Agent": "LanShare-AgentBridge/1.0"},
        ) as client:
            response = await client.get(url)
            # 手动跟随重定向，每一跳都重新做 SSRF 校验。
            hops = 0
            while response.is_redirect and hops < 4:
                next_url = str(response.next_request.url) if response.next_request else ""
                next_url = assert_public_http_url(next_url)
                response = await client.get(next_url)
                hops += 1
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"抓取失败：{exc}")
    body = response.content[:MAX_WEB_BYTES]
    text = body.decode(response.encoding or "utf-8", errors="replace")
    content_type = str(response.headers.get("content-type") or "")
    if payload.mode != "raw" and "html" in content_type.lower():
        text = strip_html_to_text(text)
    return {
        "status": "success",
        "url": str(response.url),
        "status_code": response.status_code,
        "content_type": content_type,
        "truncated": len(response.content) > MAX_WEB_BYTES,
        "content": text,
    }
