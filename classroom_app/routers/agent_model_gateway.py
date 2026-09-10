"""Task-scoped model ingress. This router is reachable only through the broker network."""
from __future__ import annotations

import asyncio
import contextlib
import json
import time

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from ..database import get_db_connection
from ..services.agent_model_gateway_service import (
    ENDPOINT_SCOPES, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, UsageCollector,
    finish_model_request, reserve_model_request,
)

router = APIRouter(prefix="/api/agent-model", tags=["agent-model"])


def _token(request: Request) -> str:
    value = request.headers.get("authorization", "")
    # The official search plugin uses x-api-key (Anthropic protocol). Neither
    # header is ever forwarded upstream; upstream headers are created afresh.
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    if request.headers.get("x-api-key"):
        return request.headers["x-api-key"].strip()
    raise HTTPException(401, "需要任务模型凭据。")


def _reserve(token, endpoint, payload):
    with get_db_connection() as conn:
        result = reserve_model_request(conn, token, endpoint=endpoint, payload=payload)
        conn.commit()
        return result


def _check_authority(token, endpoint):
    from ..services.agent_delegation_service import verify_task_delegation
    with get_db_connection() as conn:
        verify_task_delegation(conn, token, purpose="model", required_scope=ENDPOINT_SCOPES[endpoint])


def _finish(request_id, status, upstream_status, usage):
    with get_db_connection() as conn:
        finish_model_request(conn, request_id, status=status, upstream_status=upstream_status, usage=usage)
        conn.commit()


async def _read_payload(request: Request):
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_REQUEST_BYTES:
            raise HTTPException(413, "模型请求超过大小限制。")
        body.extend(chunk)
    try:
        return json.loads(body)
    except (ValueError, UnicodeError):
        raise HTTPException(422, "模型请求必须是有效 JSON。") from None


async def _watch_authority(token, endpoint, expires_at):
    while True:
        await asyncio.sleep(1)
        if time.time() >= expires_at:
            raise HTTPException(504, "单次模型请求已超过时间预算。")
        await run_in_threadpool(_check_authority, token, endpoint)


async def _authorized_chunks(response, token, endpoint, expires_at):
    watcher = asyncio.create_task(_watch_authority(token, endpoint, expires_at))
    pending = None
    size = 0
    try:
        iterator = response.aiter_bytes().__aiter__()
        while True:
            pending = asyncio.create_task(anext(iterator))
            done, _ = await asyncio.wait({pending, watcher}, return_when=asyncio.FIRST_COMPLETED)
            if watcher in done:
                await watcher  # Propagate revoked authorization, even on a stalled stream.
            try:
                chunk = await pending
            except StopAsyncIteration:
                break
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise ValueError("Model response exceeds limit")
            yield chunk
    finally:
        for job in (pending, watcher):
            if job and not job.done():
                job.cancel()
            if job:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await job


@router.post("/chat/completions")
@router.post("/messages")
async def proxy_model(request: Request):
    endpoint = "messages" if request.url.path.endswith("/messages") else "chat/completions"
    token = _token(request)
    payload = await _read_payload(request)
    grant = await run_in_threadpool(_reserve, token, endpoint, payload)
    headers = {"Authorization": f"Bearer {grant.secret}", "Content-Type": "application/json"}
    if endpoint == "messages":
        headers = {"x-api-key": grant.secret, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}
    client = httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=10.0), follow_redirects=False, trust_env=False)
    response = None
    try:
        response = await client.send(client.build_request("POST", grant.url, headers=headers, json=grant.payload), stream=True)
        await run_in_threadpool(_check_authority, token, endpoint)
    except BaseException as exc:
        if response is not None:
            await response.aclose()
        await client.aclose()
        await asyncio.shield(run_in_threadpool(_finish, grant.id, "canceled" if isinstance(exc, asyncio.CancelledError) else "failed", None, {}))
        if isinstance(exc, (HTTPException, asyncio.CancelledError)):
            raise
        raise HTTPException(502, "模型上游暂不可用。") from None

    if response.status_code != 200:
        status = response.status_code
        await response.aclose()
        await client.aclose()
        await run_in_threadpool(_finish, grant.id, "failed", status, {})
        return JSONResponse({"error": {"type": "upstream_error", "message": "模型请求未成功，请稍后重试。"}},
                            status_code=status if status in {400, 408, 429, 503} else 502,
                            headers={"X-Agent-Request-Id": grant.id})

    if not grant.payload.get("stream"):
        status = "failed"
        usage = {}
        try:
            body = bytearray()
            async for chunk in _authorized_chunks(response, token, endpoint, grant.expires_at):
                body.extend(chunk)
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError("Invalid model response")
            usage = data.get("usage") or {}
            status = "completed"
            return Response(bytes(body), media_type="application/json", headers={"X-Agent-Request-Id": grant.id})
        except HTTPException:
            raise
        except (ValueError, httpx.HTTPError):
            raise HTTPException(502, "模型返回了无效响应。") from None
        finally:
            await response.aclose()
            await client.aclose()
            await asyncio.shield(run_in_threadpool(_finish, grant.id, status, response.status_code, usage))

    async def relay():
        status = "failed"
        collector = UsageCollector()
        try:
            async for chunk in _authorized_chunks(response, token, endpoint, grant.expires_at):
                collector.feed(chunk)
                yield chunk
            status = "completed"
        except asyncio.CancelledError:
            status = "canceled"
            raise
        except (HTTPException, ValueError, httpx.HTTPError):
            # No upstream body, exception URL, key or model prompt in errors.
            yield b'event: error\ndata: {"error":{"type":"gateway_interrupted","message":"Model stream interrupted"}}\n\n'
        finally:
            await response.aclose()
            await client.aclose()
            await asyncio.shield(run_in_threadpool(_finish, grant.id, status, response.status_code, collector.usage))

    return StreamingResponse(relay(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no", "X-Agent-Request-Id": grant.id})
