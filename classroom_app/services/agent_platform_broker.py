"""Bounded, in-process ASGI calls to explicitly reviewed read operations."""
from __future__ import annotations

import asyncio
import threading
from typing import Any

import httpx
from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from ..database import get_db_connection
from .agent_delegation_service import verify_task_delegation
from .agent_platform_registry import ReadOperation, resolve_read_operation
from .agent_request_context import _BrokerIdentity, _SCOPE_KEY, _broker_identity


MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_READ_SECONDS = 15
_READ_CAPACITY = threading.BoundedSemaphore(4)


def _read_arguments(operation: ReadOperation, path_params: dict | None, query_params: dict | None) -> tuple[str, dict]:
    path_values = {} if path_params is None else path_params
    query_values = {} if query_params is None else query_params
    if not isinstance(path_values, dict) or not isinstance(query_values, dict):
        raise HTTPException(400, "读取参数必须是对象。")
    result = {"path": {}, "query": {}}
    for location, values in (("path", path_values), ("query", query_values)):
        allowed = {key: spec for key, spec in operation.parameters.items() if spec["in"] == location}
        if values.keys() - allowed.keys():
            raise HTTPException(400, "请求包含未注册的读取参数。")
        for key, spec in allowed.items():
            if key not in values:
                if spec.get("required"):
                    raise HTTPException(400, f"缺少参数 {key}。")
                continue
            value = values[key]
            kind = spec["type"]
            if kind == "integer":
                valid = type(value) is int and spec.get("minimum", 0) <= value <= spec.get("maximum", 2**63 - 1)
            elif kind == "boolean":
                valid = type(value) is bool
            else:
                valid = isinstance(value, str) and len(value) <= spec.get("maxLength", 200) and not any(ord(char) < 32 for char in value)
            if not valid or ("enum" in spec and value not in spec["enum"]):
                raise HTTPException(400, f"参数 {key} 类型或范围无效。")
            result[location][key] = value
    path = operation.path
    for key, value in result["path"].items():
        # All currently reviewed path parameters are bounded positive IDs.
        path = path.replace("{" + key + "}", str(value))
    if "{" in path or not path.startswith("/") or path.startswith("//"):
        raise HTTPException(400, "平台读取路径无效。")
    return path, {**result["query"], **operation.fixed_query}


def _verify_read_token(token: str):
    with get_db_connection() as conn:
        return verify_task_delegation(conn, token, purpose="tools", required_scope="platform:read")


async def dispatch_read(app, token: str, operation_key: str, *, path_params: dict | None = None, query_params: dict | None = None) -> dict[str, Any]:
    if _broker_identity.get() is not None:
        raise HTTPException(403, "不允许递归调用 Agent 平台能力。")
    if not isinstance(operation_key, str) or not 1 <= len(operation_key) <= 160:
        raise HTTPException(400, "平台读取能力名称无效。")
    if not _READ_CAPACITY.acquire(blocking=False):
        raise HTTPException(429, "Agent 平台读取繁忙，请稍后重试。")
    async def execute():
        verified = await run_in_threadpool(_verify_read_token, token)
        result = await _dispatch_verified_read(app, verified, operation_key, path_params=path_params, query_params=query_params)
        await run_in_threadpool(_verify_read_token, token)
        return result

    # Keep a strong reference and do not cancel an ASGI worker thread. Native
    # DB drivers cannot stop merely because their awaiting task was cancelled.
    # Capacity and the outer tool lease remain occupied until actual completion.
    work = asyncio.create_task(execute())
    try:
        try:
            return await asyncio.wait_for(asyncio.shield(work), timeout=MAX_READ_SECONDS)
        except TimeoutError:
            await _drain_read_work(work)
            raise HTTPException(504, "平台读取超时，请缩小范围后重试。") from None
        except asyncio.CancelledError:
            await _drain_read_work(work)
            raise
    finally:
        _READ_CAPACITY.release()


async def _drain_read_work(work: asyncio.Task) -> None:
    while not work.done():
        try:
            await asyncio.shield(work)
        except asyncio.CancelledError:
            continue
        except Exception:
            break
    if not work.cancelled():
        work.exception()  # Retrieve a late failure after timeout/caller disconnect.


async def _dispatch_verified_read(app, verified, operation_key: str, *, path_params: dict | None, query_params: dict | None) -> dict[str, Any]:
    # Verification happens for every call, against live session, task, lease,
    # actor and scope. A previously returned VerifiedDelegation is insufficient.
    operation = resolve_read_operation(app, operation_key, verified.actor.role)
    if operation.requires_super_admin and not verified.actor.is_super_admin:
        raise HTTPException(403, "当前账号没有此管理员能力。")
    path, query = _read_arguments(operation, path_params, query_params)
    identity = _BrokerIdentity(verified.actor.role, verified.actor.id, verified.actor.authority_fingerprint,
                               int(verified.task["id"]), str(verified.attempt["id"]), int(verified.attempt["fencing_token"]), path, object())
    context_token = _broker_identity.set(identity)
    received = 0

    async def bounded_app(scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") != "GET" or scope.get("path") != path:
            raise HTTPException(403, "Agent 请求不匹配已授权的平台操作。")
        scope[_SCOPE_KEY] = identity.nonce

        async def bounded_send(message):
            nonlocal received
            if message["type"] == "http.response.body":
                received += len(message.get("body", b""))
                if received > MAX_RESPONSE_BYTES:
                    raise HTTPException(502, "平台读取结果过大，请缩小范围。")
            await send(message)

        await app(scope, receive, bounded_send)

    try:
        transport = httpx.ASGITransport(app=bounded_app, raise_app_exceptions=True)
        async with httpx.AsyncClient(transport=transport, base_url="http://lanshare-agent.internal", follow_redirects=False) as client:
            response = await client.get(path, params=query, headers={"accept": "application/json", "accept-encoding": "identity"})
        if 300 <= response.status_code < 400 or "application/json" not in response.headers.get("content-type", "").lower():
            raise HTTPException(502, "该平台读取未返回受支持的 JSON 结果。")
        try:
            payload = response.json()
        except ValueError:
            raise HTTPException(502, "平台读取返回的 JSON 无法解析。") from None
        if response.status_code >= 400:
            raise HTTPException(response.status_code, payload.get("detail", payload) if isinstance(payload, dict) else payload)
        return {"status": "success", "operation_key": operation.key, "status_code": response.status_code, "data": payload}
    except TimeoutError:
        raise HTTPException(504, "平台读取超时，请缩小范围后重试。") from None
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, "平台读取失败，请稍后重试。") from None
    finally:
        # ContextVar copies retain the same mutable identity. Any task spawned
        # during the request loses this authority when the broker call ends.
        identity.active = False
        _broker_identity.reset(context_token)
