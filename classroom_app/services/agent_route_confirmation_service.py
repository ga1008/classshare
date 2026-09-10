"""Human confirmation for destructive platform routes proposed by the Agent.

The model may only propose ``platform_route_request``. The task owner reviews
the exact method, path and parameters in the platform, accepts the warning, and
the server then performs that one bounded request with the owner's live session
through the normal route dependencies. A change to the route source or the
parameters between review and confirmation invalidates the review hash.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

from fastapi import HTTPException
import httpx

from .agent_platform_route_capability import resolve_route_capability, route_arguments
from .agent_user_route_request_context import _SCOPE_KEY, _UserRouteIdentity, _user_route_identity

MAX_RESPONSE_BYTES = 128 * 1024
WARNING_CODE = "destructive_route"
ACTION_DEFINITIONS: dict[str, dict[str, Any]] = {
    "platform_route_request": {
        "label": "以本人身份执行平台操作",
        "done_label": "已执行平台操作",
        "description": "对具有破坏性或不可逆影响的平台接口，由用户本人在平台核对方法、路径与参数后确认，以本人身份执行。",
        "risk": "high",
        "execution_mode": "user_confirmation",
        "roles": ["teacher", "student"],
        "confirmation_note": "模型不能直接执行该接口；用户本人核对后确认，权限以本人实时权限为准。",
        "fields": {
            "capability_key": {"type": "str", "required": True, "max_chars": 160},
            "path_params": {"type": "json_object", "max_bytes": 4096},
            "query_params": {"type": "json_object", "max_bytes": 4096},
            "body": {"type": "json_object", "max_bytes": 65536},
            "expected_review_hash": {"type": "str", "max_chars": 64},
        },
    },
}


def _hash(value: bytes | str) -> str:
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _default_app():
    from ..app import app

    return app


def _prepare(app, params: dict[str, Any], user: dict[str, Any]):
    capability, route = resolve_route_capability(app, params.get("capability_key"))
    if user.get("role") not in ACTION_DEFINITIONS["platform_route_request"]["roles"]:
        raise HTTPException(403, "当前身份不能确认平台操作。")
    path, query, raw, normalized = route_arguments(capability, path_params=params.get("path_params"),
                                                   query_params=params.get("query_params"), body=params.get("body"))
    review_hash = _hash(_canonical([capability.key, capability.method, path, query.decode(), _hash(raw),
                                    capability.source_sha256, str(user.get("role")), int(user.get("id") or 0)]))
    return capability, route, path, query, raw, normalized, review_hash


def prepare_user_confirmation(conn, *, action: str, params: dict[str, Any], user: dict[str, Any], app=None) -> dict[str, Any]:
    app = app or _default_app()
    capability, _route, path, _query, _raw, normalized, review_hash = _prepare(app, params, user)
    warnings = []
    if capability.requires_user_confirmation:
        warnings.append({"code": WARNING_CODE, "message": "该接口具有破坏性或不可逆影响（删除、发布、合并、重置等）。确认后将以你的身份立即执行，平台不会自动撤销。"})
    review = {"capability_key": capability.key, "method": capability.method, "path": path, "label": capability.label,
              "domain": capability.domain, "risk": capability.risk, "mutates": capability.mutates,
              "path_params": normalized["path"], "query_params": normalized["query"], "body": normalized["body"] or None,
              "warnings": warnings, "blocking_reasons": [], "can_execute": True,
              "expected_review_hash": review_hash, "route_source_sha256": capability.source_sha256}
    clean = {key: value for key, value in params.items() if key != "expected_review_hash"}
    return {"params": {**clean, "expected_review_hash": review_hash}, "review": review}


def _validate_inputs(confirmation_inputs: Any, *, destructive: bool) -> dict[str, Any]:
    if not isinstance(confirmation_inputs, dict) or set(confirmation_inputs) != {"accepted_warning_codes", "confirmation_note"}:
        raise HTTPException(400, "请提交本人核对说明及提示选择。")
    codes, note = confirmation_inputs["accepted_warning_codes"], confirmation_inputs["confirmation_note"]
    if (not isinstance(codes, list) or len(codes) > 30 or any(not isinstance(code, str) or not 1 <= len(code) <= 100 for code in codes)
            or len(set(codes)) != len(codes) or not isinstance(note, str) or not note.strip() or len(note) > 2000
            or any(0xD800 <= ord(char) <= 0xDFFF for text in [note, *codes] for char in text)):
        raise HTTPException(400, "核对说明或提示选择格式不正确。")
    if destructive and WARNING_CODE not in codes:
        raise HTTPException(400, "请先确认该操作的破坏性提示。")
    return {"accepted_warning_codes": sorted(codes), "confirmation_note": note.strip()}


def _execute_as_user(app, *, user: dict[str, Any], source_session_id: str, capability, route, path: str, query: bytes, raw: bytes) -> dict[str, Any]:
    from ..database import get_db_connection
    from .agent_actor_service import resolve_agent_actor
    from .agent_platform_request_registry import matched_route

    with get_db_connection() as conn:
        actor = resolve_agent_actor(conn, user["role"], user["id"])
    identity = _UserRouteIdentity(actor.role, actor.id, source_session_id, _hash(source_session_id), actor.authority_fingerprint,
                                  capability.method, path, query, _hash(raw), route, object())

    async def run():
        marker = _user_route_identity.set(identity)
        total = 0
        try:
            async def bounded_app(scope, receive, send):
                if (scope.get("type") != "http" or scope.get("method") != capability.method or scope.get("path") != path
                        or scope.get("query_string", b"") != query or matched_route(app, scope) is not route):
                    raise HTTPException(403, "实际路由与已核对的平台操作不一致。")
                scope[_SCOPE_KEY] = (identity.nonce, identity.body_hash)

                async def bound_send(message):
                    nonlocal total
                    if message["type"] == "http.response.body":
                        total += len(message.get("body", b""))
                        if total > MAX_RESPONSE_BYTES:
                            raise ValueError("HTTP observation exceeds bound")
                    await send(message)

                await app(scope, receive, bound_send)

            transport = httpx.ASGITransport(app=bounded_app, raise_app_exceptions=True)
            async with httpx.AsyncClient(transport=transport, base_url="http://lanshare-agent.internal", follow_redirects=False) as client:
                return await client.request(capability.method, path + ("?" + query.decode() if query else ""), content=raw,
                                            headers={"accept": "application/json", "accept-encoding": "identity",
                                                     **({"content-type": "application/json"} if raw else {})})
        finally:
            identity.active = False
            _user_route_identity.reset(marker)

    try:
        response = asyncio.run(run())
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, "平台操作结果未知：请先在平台页面核对是否已生效，系统不会自动重试。") from None
    observation = {"http_status": response.status_code, "body_sha256": _hash(response.content)}
    if "application/json" in response.headers.get("content-type", "").lower():
        try:
            observation["data"] = response.json()
        except ValueError:
            observation["data"] = None
    return observation


def dispatch_user_confirmation(conn, *, user: dict[str, Any], source_session_id: str, task_id: int, operation_id: str,
                               action: str, params: dict[str, Any], confirmation_inputs: Any, app=None) -> dict[str, Any]:
    from .agent_action_registry import validate_action_params
    from .agent_business_confirmation_service import claim_business_confirmation
    from .agent_operation_service import complete_user_agent_operation

    app = app or _default_app()
    clean, errors = validate_action_params(action, params, reject_unknown=True)
    if errors or not clean.get("expected_review_hash"):
        raise HTTPException(400, "请先读取本次平台操作的核对快照。")
    capability, route, path, query, raw, normalized, review_hash = _prepare(app, clean, user)
    if review_hash != clean["expected_review_hash"]:
        raise HTTPException(409, "平台接口或参数已变化，请重新核对后确认。")
    declaration = _validate_inputs(confirmation_inputs, destructive=capability.requires_user_confirmation)
    claim = claim_business_confirmation(conn, user=user, source_session_id=source_session_id, task_id=task_id,
                                        operation_id=operation_id, action=action, params={**clean, "user_confirmation": declaration})
    if not claim["claimed"]:
        if claim["operation"]["status"] != "completed":
            raise HTTPException(409, "本次平台操作尚无确定回执：请先在平台页面核对是否已生效，不能自动重试。")
        return {"operation_id": operation_id, "result": claim["operation"]["result"], "replayed": True}
    # The normal router commits on its own connection. Make the claim durable
    # first so a crash leaves an explicit executing row instead of a silent redo.
    conn.commit()
    observation = _execute_as_user(app, user=user, source_session_id=source_session_id, capability=capability,
                                   route=route, path=path, query=query, raw=raw)
    result = {"capability_key": capability.key, "method": capability.method, "path": path, "label": capability.label,
              "domain": capability.domain, "risk": capability.risk, "parameters": normalized,
              "route_source_sha256": capability.source_sha256, "observation": observation,
              "verified_business": False, "url": "", "ref_id": None}
    if not 200 <= observation["http_status"] < 300:
        result["label"] = f"平台返回 {observation['http_status']}，请核对"
    complete_user_agent_operation(conn, user=user, source_session_id=source_session_id, task_id=task_id,
                                  operation_id=operation_id, result=result)
    return {"operation_id": operation_id, "result": result, "replayed": False}
