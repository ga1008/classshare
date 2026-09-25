"""Async client for the platform MCP bridge (``/api/agent-bridge/mcp``).

Every call carries the task's revocable ``lsagt_`` credential; the app verifies
it against the running attempt and the user's live session, then executes the
platform operation as that user (AI/Agent standard R1).
"""
from __future__ import annotations

import asyncio
import itertools
import json
from typing import Any

import httpx

BRIDGE_TIMEOUT_SECONDS = 60.0
MAX_RATE_LIMIT_RETRIES = 3


class BridgeAuthorityLost(RuntimeError):
    """The task credential was revoked (cancel, logout, lease loss)."""


class BridgeToolError(RuntimeError):
    def __init__(self, message: str, *, code: str = "", payload: Any = None):
        super().__init__(message)
        self.code = code
        self.payload = payload


def _message_and_code(detail: Any) -> tuple[str, str]:
    if isinstance(detail, dict):
        inner = detail.get("detail") if isinstance(detail.get("detail"), dict) else None
        if inner:
            return _message_and_code(inner)
        message = detail.get("message") or detail.get("detail") or json.dumps(detail, ensure_ascii=False)
        return str(message)[:1500], str(detail.get("code") or "")
    return str(detail or "")[:1500], ""


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        value = response.json()
    except ValueError:
        return {"detail": response.text[:500]}
    return value if isinstance(value, dict) else {"detail": value}


class BridgeClient:
    def __init__(self, base_url: str, token: str, *, transport: httpx.AsyncBaseTransport | None = None):
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=BRIDGE_TIMEOUT_SECONDS, transport=transport,
                                         headers={"Authorization": f"Bearer {token}"})
        self._ids = itertools.count(1)

    async def close(self) -> None:
        await self._client.aclose()

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        body = {"jsonrpc": "2.0", "id": next(self._ids), "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}}}
        response = None
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            response = await self._client.post("/api/agent-bridge/mcp", json=body)
            if response.status_code != 429 or attempt == MAX_RATE_LIMIT_RETRIES:
                break
            try:
                delay = float(response.headers.get("Retry-After") or 2)
            except ValueError:
                delay = 2.0
            await asyncio.sleep(min(max(delay, 1.0), 10.0))
        payload = _safe_json(response)
        if response.status_code == 401:
            # Only the credential check itself answers 401 at the transport level.
            raise BridgeAuthorityLost(_message_and_code(payload)[0] or "任务授权已失效。")
        if response.status_code >= 400:
            message, code = _message_and_code(payload)
            raise BridgeToolError(message or f"平台桥接返回 HTTP {response.status_code}", code=code or f"http_{response.status_code}")
        if payload.get("error"):
            raise BridgeToolError(str((payload["error"] or {}).get("message") or "工具参数无效"), code="invalid_arguments")
        result = payload.get("result") or {}
        text = "".join(part.get("text", "") for part in result.get("content") or [] if isinstance(part, dict))
        try:
            value = json.loads(text) if text else {}
        except ValueError:
            value = {"text": text}
        if result.get("isError"):
            message, code = _message_and_code(value)
            raise BridgeToolError(message or "平台操作失败", code=code, payload=value)
        return value
