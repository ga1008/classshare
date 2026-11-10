"""AI account adapter over the ordinary platform capability and HTTP executor.

There are no alternate blog/social CRUD implementations here. Stable aliases
resolve a mounted route or its reviewed capability; every call still traverses
that route's normal dependencies, ownership checks and transaction semantics.
"""
from __future__ import annotations

from dataclasses import replace
from functools import lru_cache
from typing import Any

from fastapi import HTTPException

from .agent_platform_registry import READ_OPERATIONS, mounted_routes, resolve_read_operation
from .agent_platform_request_registry import CAPABILITIES, resolve_capability
from .agent_platform_route_capability import (
    ROUTE_KEY_PREFIX, RouteClassification, _capability, classify_route,
    resolve_route_capability, route_key,
)
from .platform_execution_authority import WandererServiceAuthority
from .wanderer_capability_roles import student_role_exclusion
from .wanderer_platform_audience import content_audience

ALIASES = {
    "legacy.send_message": ("POST", "/api/message-center/private/messages"),
    "legacy.conversation": ("GET", "/api/message-center/private/conversation"),
    "social.send_message": ("POST", "/api/social/conversations/{key}/messages"),
    "social.messages": ("GET", "/api/social/conversations/{key}/messages"),
    "social.nudge": ("POST", "/api/social/conversations/{key}/nudge"),
    "social.mark_read": ("POST", "/api/social/conversations/{key}/read"),
    "social.create_group": ("POST", "/api/social/groups"),
    "social.invite_group": ("POST", "/api/social/groups/{group_id}/members"),
    "social.leave_group": ("POST", "/api/social/groups/{group_id}/leave"),
    "social.update_group": ("PATCH", "/api/social/groups/{group_id}"),
    "social.dissolve_group": ("POST", "/api/social/groups/{group_id}/dissolve"),
    "social.transfer_group": ("POST", "/api/social/groups/{group_id}/transfer"),
    "social.remove_group_member": ("DELETE", "/api/social/groups/{group_id}/members/{identity}"),
    "social.friends": ("GET", "/api/social/friends"),
    "social.request_friend": ("POST", "/api/social/friends/requests"),
    "social.accept_friend": ("POST", "/api/social/friends/requests/{request_id}/accept"),
    "social.decline_friend": ("POST", "/api/social/friends/requests/{request_id}/decline"),
    "social.remove_friend": ("DELETE", "/api/social/friends/{identity}"),
    "social.block": ("POST", "/api/social/blocks"),
    "social.unblock": ("DELETE", "/api/social/blocks/{identity}"),
    "social.recall_message": ("POST", "/api/social/messages/{message_id}/recall"),
    "blog.create_post": ("POST", "/api/blog/posts"),
    "blog.update_post": ("PUT", "/api/blog/posts/{post_id}"),
    "blog.delete_post": ("DELETE", "/api/blog/posts/{post_id}"),
}
_BUDGET_CONTROL_PATHS = ("/api/agent", "/api/manage", "/api/mp/", "/api/internal/")


def _app(app=None):
    if app is not None:
        return app
    from ..app import app as platform_app
    return platform_app


def _service_exclusion(path: str) -> str | None:
    if path.startswith(_BUDGET_CONTROL_PATHS):
        return "account_role_or_runtime_control_plane"
    return None


def _key_for_route(app, method, path):
    if _service_exclusion(path):
        raise HTTPException(403, "此入口属于管理或模型运行控制面，不属于散修普通业务能力。")
    reviewed = next((item for item in CAPABILITIES if item.method == method and item.path == path), None)
    if reviewed is not None:
        return reviewed.key
    read = next((item for item in READ_OPERATIONS if method == "GET" and item.path == path), None)
    if read is not None:
        return read.key
    return route_key(method, path)


def _multipart_capability(app, row, *, with_source=True):
    """Ordinary UploadFile endpoints use the same safe referenced-byte transport."""
    from .agent_platform_route_capability import _resolve_schema, _openapi_operation
    from .agent_danger_guard import hard_block_reason
    if hard_block_reason(row["method"], row["path"], row["handler"]):
        raise HTTPException(403, "此上传入口属于受限控制面。")
    spec = _openapi_operation(app, row["method"], row["path"]) or {}
    schema = _resolve_schema(app.openapi(), ((spec.get("requestBody") or {}).get("content") or {}).get("multipart/form-data", {}).get("schema")) or {}
    props = schema.get("properties") or {}
    def binary(value):
        if value.get("format") == "binary" or (value.get("type") == "string" and
                value.get("contentMediaType") == "application/octet-stream"):
            return "one"
        if value.get("type") == "array" and binary(value.get("items") or {}): return "many"
        return next((binary(option) for option in value.get("anyOf", []) if binary(option)), None)
    file_fields = [(key, binary(value)) for key, value in props.items() if binary(value)]
    if len(file_fields) != 1:
        raise HTTPException(503, "上传接口需要一个明确的文件字段。")
    field_name, cardinality = file_fields[0]
    if not field_name.isidentifier():
        raise HTTPException(503, "上传文件字段不合法。")
    plain = {key: value for key, value in props.items() if key != field_name}
    body_schema = {**schema, "properties": plain, "required": [name for name in schema.get("required", []) if name != field_name]}
    return replace(_capability(app, row, RouteClassification("route_ready", "authorized_file_transport", True, "write"), with_source=with_source),
                   transport="form", content_type="application/x-www-form-urlencoded", allows_files=True,
                   file_field_name=field_name, max_files=16 if cardinality == "many" else 1,
                   body_schema=body_schema, body_fields=tuple(plain), body_required=bool(body_schema["required"]))


def _native_transport_metadata(operation):
    if operation.method == "POST" and operation.path == "/api/social/conversations/{key}/messages":
        # This ordinary endpoint deliberately accepts a free-form dict. Describe
        # its service contract here; validation and delivery remain in that service.
        from .social_message_service import BODY_MAX, BODY_FORMATS
        from .social_media_service import MAX_ATTACHMENTS
        props = {
            "body": {"type": "string", "maxLength": BODY_MAX, "description": "消息正文；无附件时必填"},
            "body_format": {"type": "string", "enum": list(BODY_FORMATS), "default": "md"},
            "reply_to_id": {"type": ["integer", "null"], "description": "同会话内实际消息编号"},
            "mentions": {"type": "array", "items": {"type": "string"}, "description": "会话内成员identity"},
            "media_tokens": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_ATTACHMENTS,
                             "description": "原上传接口返回的授权附件token"},
        }
        return replace(operation, body_schema={"type": "object", "properties": props, "additionalProperties": True},
                       body_fields=tuple(props), body_required=True)
    if operation.method == "POST" and operation.path == "/api/message-center/private/messages":
        return replace(operation, body_schema={"type":"object", "properties": {
            "contact_identity":{"type":"string"}, "class_offering_id":{"type":["integer","null"]},
            "content":{"type":"string","maxLength":4000}}, "required":["contact_identity"]},
            body_fields=("contact_identity","class_offering_id","content"), body_required=True,
            allows_files=True, file_field_name="attachments", max_files=8)
    return operation


def _discovery_metadata(operation):
    """Describe shared route semantics without changing authorization or dispatch."""
    path = operation.path
    metadata = {}
    if path.startswith("/api/social/"):
        terms = ["social 社交"]
        if "/conversations" in path:
            # A single ordinary conversation route serves both DM and groups.
            terms.append("conversation group dm chat 私信 私聊 群聊 聊天")
        if "/groups" in path:
            terms.append("group 群聊 群组 聊天")
        if operation.method == "GET" and path == "/api/social/groups/{group_id}":
            terms.append("member 成员")
        metadata["search_terms"] = " ".join(terms)
    if path == "/api/social/conversations/{key}/messages":
        metadata["description"] = "key取已有会话列表或建群回执的key；同一入口支持私信与群聊，仍校验好友/成员与拉黑权限。"
        if operation.method == "POST":
            metadata["args_example"] = {"path_params": {"key": "<已读取的会话key>"}, "body": {"body": "<你决定发送的正文>"}}
    return metadata


def resolve_wanderer_capability(app, key: str):
    if key in ALIASES:
        key = _key_for_route(app, *ALIASES[key])
    read = next((item for item in READ_OPERATIONS if item.key == key), None)
    if read is not None:
        read = resolve_read_operation(app, key, "student")
        rows = [row for row in mounted_routes(app) if row["method"] == "GET" and row["path"] == read.path]
        if len(rows) != 1:
            raise HTTPException(503, "平台读取路由缺失或重复。")
        operation = _capability(app, rows[0], RouteClassification("route_ready", "reviewed_read", False, "read"))
        allowed = {**read.parameters, **{name: operation.parameters[name] for name in read.fixed_query if name in operation.parameters}}
        operation = replace(operation, key=read.key, parameters=allowed)
        route = rows[0]["route"]
    elif key.startswith(ROUTE_KEY_PREFIX):
        matches = [row for row in mounted_routes(app) if route_key(row["method"], row["path"]) == key]
        if len(matches) == 1 and classify_route(app, matches[0]).reason == "multipart_requires_reviewed_adapter":
            operation, route = _multipart_capability(app, matches[0]), matches[0]["route"]
        else:
            operation, route = resolve_route_capability(app, key)
    else:
        operation, route = resolve_capability(app, key)
    if _service_exclusion(operation.path):
        raise HTTPException(403, "该能力不属于散修普通账号权限或需要统一模型预算。")
    if student_role_exclusion(route):
        raise HTTPException(403, "该平台功能要求教师身份，散修保持普通学生权限。")
    if operation.method == "POST" and operation.path == "/api/social/conversations/{key}/messages":
        operation = replace(operation, server_operation_id_field="client_nonce")
    return _native_transport_metadata(operation), route


def list_wanderer_capabilities(conn, wanderer, *, app=None) -> list[dict[str, Any]]:
    app = _app(app)
    from .agent_actor_service import resolve_agent_actor
    resolve_agent_actor(conn, "student", int(wanderer["student_id"]), service_wanderer=wanderer)
    signature = tuple((id(route), id(getattr(route, "endpoint", None))) for route in app.router.routes)
    return [*_catalog(app, signature), {
        "key": "platform.attachment.read", "capability": "platform.attachment.read", "label": "按普通聊天权限读取附件引用与文档正文",
        "mutates": False, "parameters": {"social_media_id": "integer", "private_attachment_id": "integer", "revision": "string", "read_text": "boolean"},
        "description": "两个附件编号恰选其一；文本/文档可提取正文，图片音视频返回授权引用和真实未识别状态；结果继承原会话私密范围。",
        "authorization": "normal_platform_resource_policy", "executable": True,
    }, {
        "key": "platform.file.read", "capability": "platform.file.read", "label": "按原下载权限读取文件文本与版本",
        "mutates": False, "parameters": {"material_id": "integer", "submission_file_id": "integer",
            "collaboration_file_id": "integer", "course_file_id": "integer", "revision": "string"},
        "description": "四种来源恰选一种，不能使用服务器路径；返回文本/文档抽取、文件版本及原授权下载地址。",
        "authorization": "normal_platform_resource_policy", "executable": True,
    }]


@lru_cache(maxsize=8)
def _catalog(app, signature):
    from .agent_danger_guard import assess_route
    result, seen = [], set()
    rows = list(mounted_routes(app))
    for row in rows:
        if _service_exclusion(row["path"]) or student_role_exclusion(row["route"]):
            continue
        key = _key_for_route(app, row["method"], row["path"])
        if key in seen:
            continue
        reviewed = next((item for item in CAPABILITIES if item.key == key), None)
        read = next((item for item in READ_OPERATIONS if item.key == key), None)
        if reviewed is not None:
            try:
                operation, _route = resolve_capability(app, key)
            except HTTPException:
                continue
        elif read is not None:
            if "student" not in read.roles or read.requires_super_admin or row["handler"] != read.endpoint:
                continue
            operation = replace(_capability(app, row, RouteClassification("route_ready", "reviewed_read", False, "read"), with_source=False),
                                key=read.key, parameters=read.parameters)
        else:
            classification = classify_route(app, row)
            if classification.reason == "multipart_requires_reviewed_adapter":
                try:
                    operation = _multipart_capability(app, row, with_source=False)
                except HTTPException:
                    continue
            elif classification.status not in {"route_ready", "route_confirmation_required"}:
                continue
            else:
                operation = _capability(app, row, classification, with_source=False)
        operation = _native_transport_metadata(operation)
        seen.add(key)
        params = getattr(operation, "parameters", {})
        aliases = [name for name, pair in ALIASES.items() if pair == (operation.method, operation.path)]
        result.append({"key": key, "capability": key, "aliases": aliases,
            "label": operation.label, "method": operation.method, "path": operation.path,
            "mutates": operation.mutates, "parameters": params,
            "content_audience": content_audience(operation),
            "body_schema": getattr(operation, "body_schema", None),
            "body_fields": list(getattr(operation, "body_fields", ())),
            "body_hints": list(getattr(operation, "body_hints", ())),
            "transport": operation.transport, "allows_files": operation.allows_files,
            "requires_self_confirmation": assess_route(operation.method,operation.path,getattr(operation,"handler","")).destructive,
            "authorization": "normal_platform_resource_policy",
            "confirmation_source": "wanderer_service", "executable": True,
            **_discovery_metadata(operation)})
    return tuple(sorted(result, key=lambda item: (item["path"], item["method"])))


def platform_capability_matrix(app=None) -> list[dict[str, Any]]:
    """Every mounted route gets an explicit coverage reason, including exclusions."""
    app = _app(app)
    result = []
    for row in mounted_routes(app):
        item = {"method": row["method"], "path": row["path"], "handler": row["handler"]}
        reason = _service_exclusion(row["path"]) or student_role_exclusion(row["route"])
        if reason:
            item.update(status="unavailable_by_role_or_runtime_policy", reason=reason)
        else:
            try:
                key = _key_for_route(app, row["method"], row["path"])
                resolve_wanderer_capability(app, key)
                item.update(status="shared_business_route", capability=key,
                            reason="ordinary_resource_authorization_at_execution")
            except HTTPException as exc:
                classification = classify_route(app, row)
                item.update(status="requires_adapter_or_unavailable", reason=classification.reason,
                            detail=str(exc.detail))
        result.append(item)
    return result


async def execute_platform_command(*, wanderer_id: int, turn_id: str, life_epoch: int,
        fencing_token: int, command: dict, operation_id: str, authority_version: int | None = None,
        conn_factory=None, app=None) -> dict:
    from .agent_platform_request_service import dispatch_platform_request
    if not isinstance(command, dict) or not isinstance(command.get("capability"), str):
        raise HTTPException(400, "指令缺少合法 capability。")
    args = command.get("args") or {}
    source = WandererServiceAuthority(int(wanderer_id), str(turn_id), int(life_epoch), int(fencing_token),
                                      authority_version, conn_factory)
    if command["capability"] == "platform.attachment.read":
        from starlette.concurrency import run_in_threadpool
        from .platform_execution_authority import authority_connection, verify_platform_authority
        from .wanderer_platform_attachments import read_attachment
        def attachment():
            with authority_connection(source) as conn:
                authority = verify_platform_authority(conn, source, required_scope="platform:read")
                data = read_attachment(conn, authority.actor, args)
                verify_platform_authority(conn, source, required_scope="platform:read")
                return data
        data = await run_in_threadpool(attachment)
        return {"operation_id": operation_id, "command_id": command.get("id"), "capability": command["capability"],
                "status": "succeeded", "mutates": False, "verified_business": True, "scope": data["scope"], "result": {"data": data}}
    if command["capability"] == "platform.file.read":
        from starlette.concurrency import run_in_threadpool
        from .platform_execution_authority import authority_connection, verify_platform_authority
        from .agent_scoped_read_service import read_scoped_file, assert_scoped_file_current
        from .wanderer_platform_files import SELECTORS
        if not isinstance(args, dict) or set(args) - (SELECTORS | {"revision"}):
            raise HTTPException(400, "文件读取仅接受平台文件编号和可选 revision。")
        def read():
            with authority_connection(source) as conn:
                authority = verify_platform_authority(conn, source, required_scope="platform:read")
                data = read_scoped_file(conn, authority.actor, 0, **args)
                assert_scoped_file_current(conn, authority.actor, data, **args)
                verify_platform_authority(conn, source, required_scope="platform:read")
                return data
        data = await run_in_threadpool(read)
        return {"operation_id": operation_id, "command_id": command.get("id"), "capability": command["capability"],
                "status": "succeeded", "mutates": False, "verified_business": True, "result": {"data": data}}
    if not isinstance(args, dict) or set(args) - {"path_params", "query_params", "body", "files", "safety_check"}:
        raise HTTPException(400, "能力参数必须为 path_params/query_params/body/files/safety_check 字段。")
    app = _app(app)
    operation, _route = resolve_wanderer_capability(app, command["capability"])
    values = dict(args)
    read = next((item for item in READ_OPERATIONS if item.key == operation.key), None)
    if read is not None and read.fixed_query:
        values["query_params"] = {**(values.get("query_params") or {}), **read.fixed_query}
    if operation.method == "POST" and operation.path == "/api/social/conversations/{key}/messages":
        values["body"] = {**(values.get("body") or {}), "client_nonce": operation_id}
    if command.get("expected_revision") is not None:
        parameters = getattr(operation, "parameters", {})
        fields = set(parameters) | set(getattr(operation, "body_fields", ())) | set(getattr(operation, "body_hints", ()))
        for group in ("body", "query"):
            if isinstance(parameters.get(group), dict):
                fields.update(parameters[group])
        schema = getattr(operation, "body_schema", None) or {}
        fields.update(schema.get("properties", {}))
        key = next((name for name in ("expected_revision", "expected_version", "expected_updated_at") if name in fields), None)
        if key is None:
            raise HTTPException(400, "此能力没有声明版本字段，请使用其普通业务参数。")
        target = "query_params" if operation.method == "DELETE" else "body"
        values[target] = {**(values.get(target) or {}), key: command["expected_revision"]}
    receipt = await dispatch_platform_request(app, source, operation.key, operation_id, **values)
    from .platform_execution_authority import authority_connection
    from .wanderer_platform_results import normalize_platform_result
    with authority_connection(source) as conn:
        receipt = normalize_platform_result(conn, source, operation, values, receipt)
        from .wanderer_platform_resources import settle_action_resources
        settle_action_resources(conn, source, operation, operation_id, receipt.get("status"))
        conn.commit()
    return {**receipt, "command_id": command.get("id"), "capability": command["capability"]}
