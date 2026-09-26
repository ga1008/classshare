"""Policy-governed access to every mounted JSON API route, as the current user.

This is the digital-twin layer. A reviewed adapter is no longer required for the
Agent to reach a business route: any mounted JSON route that is not on the hard
exclusion list can be invoked in-process with the task owner's live identity,
so the route's own dependencies enforce exactly the user's permissions.

Three properties keep this safe without per-route review:

1. Hard exclusions are decided by route metadata, never by model text:
   authentication/session transitions, credential inputs, the Agent control
   plane, HTML pages, file/form transports and routes outside the OpenAPI schema.
2. Reviewed adapters keep precedence. A route already covered by a reviewed
   read/request capability is reachable only through that key and its contract.
3. Destructive routes are never executable by the model. They may only be
   proposed as a ``platform_route_request`` action which the user confirms in
   the platform, after which the server executes it with the user's session.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import inspect
import json
import re
from typing import Any
from urllib.parse import urlencode

from fastapi import HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.routing import APIRoute
from starlette.requests import Request
from starlette.responses import FileResponse, PlainTextResponse, RedirectResponse, StreamingResponse

from .agent_platform_registry import (
    READ_OPERATIONS,
    SIDE_EFFECT_GET_PATHS,
    _domain,
    _exclusion,
    _special_review_reason,
    mounted_routes,
)

ROUTE_KEY_PREFIX = "route."
ALLOWED_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
MAX_BODY_BYTES = 64 * 1024
MAX_STRING_LENGTH = 4000
MAX_ARRAY_ITEMS = 200
# Paths whose business meaning is irreversible or broad. They require the user
# to confirm in the platform; the model can only propose them.
DESTRUCTIVE_PATTERN = re.compile(
    r"(delete|remove|purge|reset|clear|revoke|wipe|close[-_]?out|merge|publish|unpublish|archive|"
    r"disable|deactivate|retire|force|bulk|batch|reassign|transfer|regenerate|rotate|import|sync|"
    r"cutover|migrate|restore|rollback|destroy|truncate|approve|reject|grant|promote|demote)",
    re.IGNORECASE,
)
CONTROL_PLANE_PREFIXES = ("/api/agent-tasks", "/api/agent-model", "/api/agent-bridge", "/api/manage/system/agent")
NON_JSON_RESPONSES = (HTMLResponse, FileResponse, RedirectResponse, StreamingResponse, PlainTextResponse)
FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"
BODY_METHODS = ("POST", "PUT", "PATCH", "DELETE")
FIELD_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
# Handlers that read the body by hand (``await request.json()``) declare
# nothing in OpenAPI. Their field names are recovered from the source so the
# model is told what to send instead of being refused every body.
_UNDECLARED_JSON = re.compile(r"(?:(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*)?await\s+request\.json\(\)")
_UNDECLARED_FORM = re.compile(r"(?:(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*)?await\s+request\.form\(\)")
_BODY_FIELD_TEMPLATE = r"\b{var}\s*(?:\.get\(|\[)\s*[\"']([A-Za-z_][A-Za-z0-9_]{{0,63}})[\"']"


def route_key(method: str, path: str) -> str:
    return ROUTE_KEY_PREFIX + hashlib.sha256((method + "\n" + path).encode()).hexdigest()[:20]


def source_digest(endpoint) -> str:
    return hashlib.sha256(inspect.getsource(endpoint).replace("\r\n", "\n").encode()).hexdigest()


@dataclass(frozen=True)
class RouteCapability:
    """Duck-types the reviewed RequestCapability fields the request service uses."""

    key: str
    label: str
    method: str
    path: str
    module: str
    handler: str
    source_sha256: str
    domain: str
    risk: str
    mutates: bool
    requires_user_confirmation: bool
    parameters: dict[str, Any] = field(default_factory=dict)
    body_schema: dict[str, Any] | None = None
    body_required: bool = False
    transport: str = "json"
    allows_files: bool = False
    max_body_bytes: int = MAX_BODY_BYTES
    response_contract: str = "generic_json"
    server_operation_id_field: str | None = None
    # json | form (urlencoded). Multipart stays behind a reviewed adapter.
    content_type: str = "application/json"
    # False when the handler reads the body by hand; then body_hints carries
    # the field names recovered from its source and any JSON object is passed.
    body_declared: bool = True
    body_fields: tuple[str, ...] = ()
    body_hints: tuple[str, ...] = ()

    @property
    def accepts_body(self) -> bool:
        return self.body_schema is not None or not self.body_declared

    def usage(self) -> str:
        """One line telling the model exactly how to call this route."""
        parts = [f"{self.method} {self.path}"]
        for location in ("path", "query"):
            names = [f"{name}{'*' if spec.get('required') else ''}" for name, spec in self.parameters.items() if spec["in"] == location]
            if names:
                parts.append(f"{location}参数: {', '.join(names)}")
        kind = "表单字段" if self.transport == "form" else "JSON body 字段"
        if self.body_fields:
            parts.append(f"{kind}: {', '.join(self.body_fields)}")
        elif self.body_hints:
            parts.append(f"{kind}（从接口源码推断，未在文档声明）: {', '.join(self.body_hints)}")
        elif not self.body_declared:
            parts.append(f"{kind}: 接口自行解析，按业务字段名发送 JSON 对象")
        if not any(spec for spec in self.parameters.values()) and not self.accepts_body:
            parts.append("无参数")
        return "；".join(parts) + "（* 为必填）"

    def public(self, *, include_parameters: bool) -> dict[str, Any]:
        # requires_user_confirmation now means "destructive": it executes directly
        # once platform_request carries a server-verified safety_check.
        item = {"key": self.key, "method": self.method, "path": self.path, "label": self.label,
                "domain": self.domain, "risk": self.risk, "mutates": self.mutates,
                "executable": True,
                "status": "route_destructive_self_check" if self.requires_user_confirmation else "route_ready",
                "tool": "platform_request + safety_check" if self.requires_user_confirmation else "platform_request",
                "authorization": "normal_platform_resource_policy",
                "guarantee": "observed_http_result_not_verified_business",
                "usage": self.usage()}
        if include_parameters:
            item["parameters"] = self.parameters
            item["transport"] = self.transport
            item["body_schema"] = self.body_schema
            item["body_required"] = self.body_required
            item["body_fields"] = list(self.body_fields)
            item["body_hints"] = list(self.body_hints)
            item["body_declared"] = self.body_declared
            item["required_scope"] = "platform:write" if self.mutates else "platform:read"
        return item


@dataclass(frozen=True)
class RouteClassification:
    status: str  # blocked | reviewed | route_ready | route_confirmation_required
    reason: str
    mutates: bool = False
    risk: str = "read"
    superseded_by: tuple[str, ...] = ()


def _reviewed_keys() -> dict[tuple[str, str], list[str]]:
    from .agent_platform_request_registry import CAPABILITIES

    reviewed: dict[tuple[str, str], list[str]] = {}
    for item in READ_OPERATIONS:
        reviewed.setdefault(("GET", item.path), []).append(item.key)
    for item in CAPABILITIES:
        reviewed.setdefault((item.method, item.path), []).append(item.key)
    return reviewed


def _openapi_operation(app, method: str, path: str) -> dict[str, Any] | None:
    try:
        spec = app.openapi()
    except Exception:
        return None
    return (spec.get("paths") or {}).get(path, {}).get(method.lower())


def _non_json_response(route) -> bool:
    response_class = getattr(route, "response_class", None)
    if isinstance(response_class, type):
        return issubclass(response_class, NON_JSON_RESPONSES) and not issubclass(response_class, JSONResponse)
    return False


def _explicit_json_response(route) -> bool:
    response_class = getattr(route, "response_class", None)
    return isinstance(response_class, type) and issubclass(response_class, JSONResponse)


def _body_kind(operation: dict[str, Any]) -> str | None:
    """json | form | multipart | None (no declared request body)."""
    body = operation.get("requestBody")
    if not body:
        return None
    content = body.get("content") or {}
    if "application/json" in content:
        return "json"
    if FORM_CONTENT_TYPE in content:
        return "form"
    return "multipart"


def _resolve_schema(spec: dict[str, Any], schema: Any, depth: int = 0) -> Any:
    """Inline ``$ref`` so the model sees field names, not component pointers."""
    if not isinstance(schema, dict) or depth > 4:
        return schema
    if "$ref" in schema:
        name = str(schema["$ref"]).rsplit("/", 1)[-1]
        target = ((spec.get("components") or {}).get("schemas") or {}).get(name)
        return _resolve_schema(spec, target, depth + 1) if isinstance(target, dict) else {"type": "object"}
    resolved = dict(schema)
    if isinstance(resolved.get("properties"), dict):
        resolved["properties"] = {key: _resolve_schema(spec, value, depth + 1) for key, value in resolved["properties"].items()}
    for key in ("items", "additionalProperties"):
        if isinstance(resolved.get(key), dict):
            resolved[key] = _resolve_schema(spec, resolved[key], depth + 1)
    for key in ("anyOf", "oneOf", "allOf"):
        if isinstance(resolved.get(key), list):
            resolved[key] = [_resolve_schema(spec, item, depth + 1) for item in resolved[key]]
    return resolved


def _body_field_names(schema: Any) -> tuple[str, ...]:
    if not isinstance(schema, dict):
        return ()
    required = set(schema.get("required") or [])
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return ()
    return tuple(f"{name}*" if name in required else name for name in properties)


def _undeclared_body(endpoint) -> tuple[str | None, tuple[str, ...]]:
    """(transport, field hints) for handlers that consume ``request`` by hand."""
    try:
        signature = inspect.signature(endpoint)
        source = inspect.getsource(endpoint)
    except (TypeError, OSError, ValueError):
        return None, ()
    takes_request = any(param.annotation is Request or getattr(param.annotation, "__name__", "") == "Request"
                        for param in signature.parameters.values())
    if not takes_request:
        return None, ()
    json_calls = list(_UNDECLARED_JSON.finditer(source))
    form_calls = list(_UNDECLARED_FORM.finditer(source))
    # A handler that branches on content type (JSON vs multipart) or parses
    # the body more than once has no single shape to infer: stay blocked.
    if len(json_calls) + len(form_calls) != 1 or "multipart" in source:
        return None, ()
    match, transport = (json_calls[0], "json") if json_calls else (form_calls[0], "form")
    # Only fields read from the variable that holds the parsed body count as
    # hints; other dict lookups in the handler (AI responses, rows) do not.
    variable = match.group("var") or "data"
    # Stop at the next rebinding of that name (handlers reuse `data` for the
    # AI response later on), so only the request's own fields are reported.
    segment = source[match.end():]
    rebound = re.search(r"\b" + re.escape(variable) + r"\s*=[^=]", segment)
    if rebound:
        segment = segment[:rebound.start()]
    field_pattern = re.compile(_BODY_FIELD_TEMPLATE.format(var=re.escape(variable)))
    hints: list[str] = []
    for hit in field_pattern.finditer(segment):
        if hit.group(1) not in hints:
            hints.append(hit.group(1))
    return transport, tuple(hints[:24])


def classify_route(app, row: dict[str, Any], reviewed: dict | None = None) -> RouteClassification:
    method, path, route = row["method"], row["path"], row.get("route")
    text = (path + " " + row["handler"]).lower()
    exclusion = _exclusion(row)
    if exclusion:
        return RouteClassification("blocked", exclusion)
    if _special_review_reason(row) == "special_secure_input_required":
        return RouteClassification("blocked", "special_secure_input_required")
    if path.startswith(CONTROL_PLANE_PREFIXES) or "system_monitor" in text or "/agent" in path:
        return RouteClassification("blocked", "agent_control_plane")
    if method not in ALLOWED_METHODS or not isinstance(route, APIRoute):
        return RouteClassification("blocked", "unsupported_transport")
    if ":path}" in path:
        return RouteClassification("blocked", "open_path_segment")
    if _non_json_response(route):
        return RouteClassification("blocked", "non_json_response")
    if not path.startswith("/api/") and not _explicit_json_response(route):
        # Template pages and redirects under /manage, /classroom, ... declare no
        # response class. They are UI surfaces, not machine-readable operations.
        return RouteClassification("blocked", "page_route_not_machine_readable")
    operation = _openapi_operation(app, method, path)
    if operation is None:
        return RouteClassification("blocked", "outside_openapi_schema")
    if _body_kind(operation) == "multipart":
        # File uploads stay behind reviewed adapters (attachment brokering);
        # plain urlencoded forms are ordinary field maps and run as `form`.
        return RouteClassification("blocked", "multipart_requires_reviewed_adapter")
    from .agent_danger_guard import hard_block_reason

    if hard_block_reason(method, path, row["handler"]):
        return RouteClassification("blocked", "agent_hard_blocked")
    superseded = (reviewed if reviewed is not None else _reviewed_keys()).get((method, path))
    if superseded:
        return RouteClassification("reviewed", "reviewed_adapter_takes_precedence", superseded_by=tuple(superseded))
    mutates = method != "GET" or path in SIDE_EFFECT_GET_PATHS
    destructive = method == "DELETE" or (mutates and bool(DESTRUCTIVE_PATTERN.search(text)))
    if destructive:
        return RouteClassification("route_confirmation_required", "destructive_route_requires_user_confirmation", True, "destructive")
    return RouteClassification("route_ready", "normal_platform_resource_policy", mutates, "write" if mutates else "read")


def _parameter_specs(operation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    specs: dict[str, dict[str, Any]] = {}
    for item in operation.get("parameters") or []:
        if item.get("in") not in {"path", "query"}:
            continue
        schema = item.get("schema") or {}
        if "anyOf" in schema:
            options = [option for option in schema["anyOf"] if option.get("type") != "null"]
            schema = options[0] if len(options) == 1 else {"type": "string"}
        specs[item["name"]] = {"in": item["in"], "type": schema.get("type") or "string", "required": bool(item.get("required")),
                               **{key: schema[key] for key in ("minimum", "maximum", "maxLength", "enum", "items") if key in schema}}
    return specs


def _label(route, operation: dict[str, Any]) -> str:
    description = str(operation.get("description") or "").strip()
    first_line = description.splitlines()[0] if description else ""
    for candidate in (operation.get("summary"), first_line, getattr(route, "name", "")):
        text = str(candidate or "").strip()
        if text:
            return text[:120]
    return route.path


def _capability(app, row: dict[str, Any], classification: RouteClassification, *, with_source: bool = True) -> RouteCapability:
    route = row["route"]
    operation = _openapi_operation(app, row["method"], row["path"]) or {}
    body = operation.get("requestBody") or {}
    kind = _body_kind(operation)
    transport = "form" if kind == "form" else "json"
    content = (body.get("content") or {}).get(FORM_CONTENT_TYPE if kind == "form" else "application/json") or {}
    try:
        spec = app.openapi()
    except Exception:
        spec = {}
    schema = _resolve_schema(spec, content.get("schema")) if kind else None
    endpoint = route.endpoint
    body_declared, hints = True, ()
    if kind is None and row["method"] in BODY_METHODS:
        undeclared, hints = _undeclared_body(endpoint)
        if undeclared:
            body_declared, transport = False, undeclared
    return RouteCapability(
        key=route_key(row["method"], row["path"]), label=_label(route, operation), method=row["method"], path=row["path"],
        module=getattr(endpoint, "__module__", ""), handler=getattr(endpoint, "__qualname__", ""),
        # The index lists hundreds of routes; the live handler digest is only
        # bound at resolve time, where it enters the durable request receipt.
        source_sha256=source_digest(endpoint) if with_source else "", domain=_domain(row), risk=classification.risk, mutates=classification.mutates,
        requires_user_confirmation=classification.status == "route_confirmation_required",
        parameters=_parameter_specs(operation), body_schema=schema, body_required=bool(body.get("required")),
        transport=transport, content_type=FORM_CONTENT_TYPE if transport == "form" else "application/json",
        body_declared=body_declared, body_fields=_body_field_names(schema), body_hints=hints,
    )


def _rows(app) -> list[dict[str, Any]]:
    return [row for row in mounted_routes(app) if row["method"] in ALLOWED_METHODS]


def resolve_route_capability(app, key: str) -> tuple[RouteCapability, Any]:
    if not isinstance(key, str) or not key.startswith(ROUTE_KEY_PREFIX) or not 10 <= len(key) <= 40:
        raise HTTPException(404, "平台路由能力名称无效。")
    matches = [row for row in _rows(app) if route_key(row["method"], row["path"]) == key]
    if len(matches) != 1:
        raise HTTPException(503, "平台路由缺失或重复挂载，需重新核对。")
    row = matches[0]
    classification = classify_route(app, row)
    if classification.status == "blocked":
        raise HTTPException(403, f"该平台路由不对 Agent 开放（{classification.reason}）。")
    if classification.status == "reviewed":
        raise HTTPException(409, {"message": "该路由已有审核适配能力，请改用对应能力名称。",
                                  "use_capability_keys": list(classification.superseded_by)})
    return _capability(app, row, classification), row["route"]


def route_capability_inventory(app, *, actor_role: str, is_super_admin: bool = False) -> list[dict[str, Any]]:
    if actor_role not in {"teacher", "student"}:
        raise HTTPException(403, "Agent 平台身份无效。")
    reviewed = _reviewed_keys()
    result = []
    for row in _rows(app):
        classification = classify_route(app, row, reviewed)
        if classification.status in {"blocked", "reviewed"}:
            continue
        result.append(_capability(app, row, classification, with_source=False).public(include_parameters=False))
    return sorted(result, key=lambda item: (item["domain"], item["path"], item["method"]))


def route_capability_details(app, keys: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    found, unavailable = [], []
    for key in keys:
        try:
            capability, _route = resolve_route_capability(app, key)
        except HTTPException:
            unavailable.append(key)
            continue
        found.append(capability.public(include_parameters=True))
    return found, unavailable


def _scalar(value: Any, spec: dict[str, Any], name: str) -> Any:
    kind = spec.get("type") or "string"
    if kind == "integer":
        valid = type(value) is int and spec.get("minimum", -(2**63)) <= value <= spec.get("maximum", 2**63 - 1)
    elif kind == "number":
        valid = type(value) in (int, float) and value == value and abs(value) < 1e300
    elif kind == "boolean":
        valid = type(value) is bool
    else:
        limit = min(int(spec.get("maxLength") or MAX_STRING_LENGTH), MAX_STRING_LENGTH)
        valid = (isinstance(value, str) and len(value) <= limit
                 and not any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in value))
    if not valid or ("enum" in spec and value not in spec["enum"]):
        raise HTTPException(400, f"参数 {name} 类型或范围无效。")
    return value


def _query_text(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


def route_arguments(capability: RouteCapability, *, path_params=None, query_params=None, body=None):
    values = {"path": {} if path_params is None else path_params, "query": {} if query_params is None else query_params}
    normalized: dict[str, Any] = {"path": {}, "query": {}, "body": {}}
    for location, items in values.items():
        allowed = {name: spec for name, spec in capability.parameters.items() if spec["in"] == location}
        if not isinstance(items, dict) or items.keys() - allowed.keys():
            raise HTTPException(400, f"平台请求包含未注册的{location}参数。")
        for name, spec in allowed.items():
            if name not in items:
                if spec.get("required"):
                    raise HTTPException(400, f"缺少参数 {name}。")
                continue
            value = items[name]
            if spec.get("type") == "array":
                item_spec = spec.get("items") or {"type": "string"}
                if not isinstance(value, list) or len(value) > MAX_ARRAY_ITEMS:
                    raise HTTPException(400, f"参数 {name} 必须是有限数组。")
                normalized[location][name] = [_scalar(item, item_spec, name) for item in value]
            else:
                normalized[location][name] = _scalar(value, spec, name)
    raw = _body_bytes(capability, body)
    if raw:
        normalized["body"] = body
    path = capability.path
    for name, value in normalized["path"].items():
        path = re.sub(r"\{" + re.escape(name) + r"(?::int)?\}", lambda _match, text=str(value): text, path)
    if "{" in path:
        raise HTTPException(400, "平台请求路径参数不完整。")
    query_items = [(name, _query_text(item)) for name in sorted(normalized["query"])
                   for item in (normalized["query"][name] if isinstance(normalized["query"][name], list) else [normalized["query"][name]])]
    return path, urlencode(query_items).encode("ascii"), raw, normalized


def _body_bytes(capability: RouteCapability, body: Any) -> bytes:
    if not capability.accepts_body:
        if body not in (None, {}):
            raise HTTPException(400, "该平台路由不接受请求体。")
        return b""
    if body is None:
        if capability.body_required:
            raise HTTPException(400, "该平台路由需要请求体。")
        return b""
    if capability.transport == "form":
        return _form_bytes(capability, body)
    if not isinstance(body, (dict, list)):
        raise HTTPException(400, "请求体必须是 JSON 对象或数组。")
    if not capability.body_declared:
        # The handler parses this itself; only the shape is bounded here.
        if not isinstance(body, dict) or any(not isinstance(key, str) or not FIELD_NAME.fullmatch(key) for key in body):
            raise HTTPException(400, "请求体必须是字段名合法的 JSON 对象。")
    try:
        raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError):
        raise HTTPException(400, "请求体不是有效 JSON。") from None
    if len(raw) > capability.max_body_bytes:
        raise HTTPException(400, "平台请求内容过长。")
    return raw


def _form_bytes(capability: RouteCapability, body: Any) -> bytes:
    """urlencoded field map; scalars and lists of scalars only, like a browser form."""
    if not isinstance(body, dict):
        raise HTTPException(400, "表单请求体必须是字段名到值的 JSON 对象。")
    items: list[tuple[str, str]] = []
    for name, value in body.items():
        if not isinstance(name, str) or not FIELD_NAME.fullmatch(name):
            raise HTTPException(400, "表单字段名无效。")
        values = value if isinstance(value, list) else [value]
        if len(values) > MAX_ARRAY_ITEMS:
            raise HTTPException(400, f"字段 {name} 项数过多。")
        for item in values:
            if item is None or isinstance(item, (dict, list)):
                raise HTTPException(400, f"字段 {name} 必须是文本、数字或布尔值。")
            text = _query_text(item)
            if len(text) > MAX_STRING_LENGTH or any(ord(char) < 32 and char not in "\n\r\t" for char in text):
                raise HTTPException(400, f"字段 {name} 内容无效。")
            items.append((name, text))
    raw = urlencode(items).encode("ascii")
    if len(raw) > capability.max_body_bytes:
        raise HTTPException(400, "平台请求内容过长。")
    return raw
