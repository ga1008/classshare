from __future__ import annotations

import html
import json
import re
import time
from typing import Any
from urllib.parse import parse_qsl, urlparse

import httpx

from ..database import get_db_connection
from .academic_integration_service import (
    load_teacher_academic_access_method,
    open_authenticated_academic_client,
)
from .smart_classroom_integration_service import (
    load_teacher_smart_classroom_access_method,
    open_authenticated_smart_classroom_client,
)


MAX_PROBE_RESPONSE_BYTES = 96 * 1024
MAX_TEXT_PREVIEW_CHARS = 14000
ALLOWED_METHODS = {"GET", "POST"}
BLOCKED_HEADER_NAMES = {
    "authorization",
    "cookie",
    "host",
    "content-length",
    "connection",
    "transfer-encoding",
}
ACADEMIC_ALLOWED_PATH_PREFIXES = (
    "/kbcx/",
    "/xsxkjk/",
    "/kwgl/jkcx_",
    "/pkgl/jxcdjbxxgl_",
    "/cdjy/",
    "/ksglcommon/",
    "/cjlrgl/jscjlr_",
    "/xtgl/index_",
)
SMART_ALLOWED_PATH_PREFIXES = (
    "/teaching/checkinCourse/",
    "/user/getUserInfo",
)


def _clean_preview_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _load_mapping(raw_value: Any, *, field_name: str) -> dict[str, Any]:
    if raw_value in (None, ""):
        return {}
    if isinstance(raw_value, dict):
        return {str(key): value for key, value in raw_value.items()}
    if isinstance(raw_value, str):
        stripped = raw_value.strip()
        if not stripped:
            return {}
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed_pairs = parse_qsl(stripped, keep_blank_values=True)
            if parsed_pairs:
                return {str(key): value for key, value in parsed_pairs}
            raise ValueError(f"{field_name} 需要是 JSON 对象或 URL 编码键值。") from None
        if not isinstance(parsed, dict):
            raise ValueError(f"{field_name} 需要是 JSON 对象。")
        return {str(key): value for key, value in parsed.items()}
    raise ValueError(f"{field_name} 需要是 JSON 对象。")


def _sanitize_headers(raw_headers: Any) -> dict[str, str]:
    headers = _load_mapping(raw_headers, field_name="请求头")
    sanitized: dict[str, str] = {}
    for key, value in headers.items():
        normalized = str(key or "").strip()
        if not normalized or normalized.lower() in BLOCKED_HEADER_NAMES:
            continue
        sanitized[normalized] = str(value)
    return sanitized


def _normalize_method(raw_method: Any) -> str:
    method = str(raw_method or "POST").strip().upper()
    if method not in ALLOWED_METHODS:
        raise ValueError("验证请求仅支持 GET 或 POST。")
    return method


def _normalize_path_for_allowed_base(path: str, *, allowed_base_url: str) -> str:
    base_path = urlparse(allowed_base_url).path.rstrip("/")
    if base_path and path == base_path:
        return "/"
    if base_path and path.startswith(f"{base_path}/"):
        stripped = path[len(base_path):]
        return stripped if stripped.startswith("/") else f"/{stripped}"
    return path


def _resolve_probe_path(raw_url: Any, *, allowed_base_url: str, allowed_prefixes: tuple[str, ...]) -> str:
    url_text = str(raw_url or "").strip()
    if not url_text:
        raise ValueError("请填写请求地址。")
    base = urlparse(allowed_base_url)
    parsed = urlparse(url_text)
    if parsed.scheme or parsed.netloc:
        if parsed.scheme not in {"http", "https"} or parsed.netloc != base.netloc:
            raise ValueError("验证请求只能访问当前适配器允许的官方域名。")
        path = _normalize_path_for_allowed_base(parsed.path or "/", allowed_base_url=allowed_base_url)
        if parsed.query:
            path = f"{path}?{parsed.query}"
    else:
        path = url_text if url_text.startswith("/") else f"/{url_text}"
        parsed_relative = urlparse(path)
        normalized_relative_path = _normalize_path_for_allowed_base(
            parsed_relative.path or "/",
            allowed_base_url=allowed_base_url,
        )
        path = normalized_relative_path + (f"?{parsed_relative.query}" if parsed_relative.query else "")
    parsed_path = urlparse(path).path
    if not any(parsed_path.startswith(prefix) for prefix in allowed_prefixes):
        raise ValueError("该地址不在本平台已确认的只读探查范围内。")
    return path


def _body_for_preview(raw_body: Any, *, body_mode: str) -> Any:
    if body_mode == "raw":
        return raw_body if isinstance(raw_body, str) else json.dumps(raw_body or {}, ensure_ascii=False, indent=2)
    return _load_mapping(raw_body, field_name="请求载荷")


def _summarize_response(response: httpx.Response, *, elapsed_ms: int) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "")
    raw_bytes = response.content[:MAX_PROBE_RESPONSE_BYTES]
    charset = response.encoding or "utf-8"
    text = raw_bytes.decode(charset, errors="replace")
    truncated = len(response.content) > MAX_PROBE_RESPONSE_BYTES
    summary: dict[str, Any] = {
        "ok": response.status_code < 400,
        "status_code": response.status_code,
        "reason": response.reason_phrase,
        "elapsed_ms": elapsed_ms,
        "url": str(response.url),
        "content_type": content_type,
        "truncated": truncated,
        "size_bytes": len(response.content),
        "preview_kind": "text",
        "preview": text[:MAX_TEXT_PREVIEW_CHARS],
    }
    parsed_json: Any = None
    try:
        parsed_json = response.json()
    except (ValueError, json.JSONDecodeError):
        parsed_json = None
    if parsed_json is not None:
        summary["preview_kind"] = "json"
        summary["json"] = parsed_json
        if isinstance(parsed_json, dict):
            row_count = 0
            for key in ("items", "rows", "list", "kbList", "data"):
                if isinstance(parsed_json.get(key), list):
                    row_count = len(parsed_json[key])
                    break
            summary["result_hint"] = {
                "top_level_keys": list(parsed_json.keys())[:20],
                "row_count": row_count,
            }
        elif isinstance(parsed_json, list):
            summary["result_hint"] = {"row_count": len(parsed_json)}
        return summary

    title_match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)
    clean_text = _clean_preview_text(text)
    summary["preview_kind"] = "html" if "<html" in text.lower() or "<table" in text.lower() else "text"
    summary["html_title"] = _clean_preview_text(title_match.group(1)) if title_match else ""
    summary["text_preview"] = clean_text[:MAX_TEXT_PREVIEW_CHARS]
    if "login_slogin" in text or "请输入用户名" in clean_text:
        summary["ok"] = False
        summary["warning"] = "返回内容像登录页，可能是会话失效或请求参数不完整。"
    return summary


async def _execute_probe(
    client: httpx.AsyncClient,
    *,
    method: str,
    path: str,
    params: dict[str, Any],
    headers: dict[str, str],
    body_mode: str,
    body: Any,
) -> dict[str, Any]:
    started_at = time.monotonic()
    request_kwargs: dict[str, Any] = {
        "params": params,
        "headers": headers,
        "follow_redirects": False,
    }
    if method == "POST":
        if body_mode == "json":
            request_kwargs["json"] = _load_mapping(body, field_name="请求载荷")
        elif body_mode == "raw":
            request_kwargs["content"] = _body_for_preview(body, body_mode=body_mode)
        else:
            request_kwargs["data"] = _load_mapping(body, field_name="请求载荷")
    response = await client.request(method, path, **request_kwargs)
    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    return _summarize_response(response, elapsed_ms=elapsed_ms)


async def probe_integration_request(teacher_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    provider = str(payload.get("provider") or "").strip().lower()
    method = _normalize_method(payload.get("method"))
    params = _load_mapping(payload.get("params"), field_name="查询参数")
    headers = _sanitize_headers(payload.get("headers"))
    body_mode = str(payload.get("body_mode") or "form").strip().lower()
    if body_mode not in {"form", "json", "raw"}:
        raise ValueError("请求载荷格式仅支持 form、json 或 raw。")
    body = _body_for_preview(payload.get("body"), body_mode=body_mode)

    with get_db_connection() as conn:
        if provider == "academic":
            access_payload = load_teacher_academic_access_method(conn, int(teacher_id), school_code="gxufl")
        elif provider == "smart_classroom":
            access_payload = load_teacher_smart_classroom_access_method(conn, int(teacher_id))
        else:
            raise ValueError("未知的对接类型。")

    if not access_payload:
        raise ValueError("请先在账号管理中保存并验证账号。")

    if provider == "academic":
        async with open_authenticated_academic_client(access_payload) as (client, profile, login_result):
            path = _resolve_probe_path(
                payload.get("url"),
                allowed_base_url=profile.base_url,
                allowed_prefixes=ACADEMIC_ALLOWED_PATH_PREFIXES,
            )
            headers.setdefault("Origin", profile.base_url)
            return {
                "status": "success",
                "provider": provider,
                "login": {
                    "status": login_result.get("status"),
                    "message": login_result.get("message"),
                    "checked_at": login_result.get("checked_at"),
                },
                "request": {
                    "method": method,
                    "url": str(client.base_url).rstrip("/") + path,
                    "params": params,
                    "headers": {key: value for key, value in headers.items() if key.lower() not in BLOCKED_HEADER_NAMES},
                    "body_mode": body_mode,
                    "body": body,
                },
                "response": await _execute_probe(
                    client,
                    method=method,
                    path=path,
                    params=params,
                    headers=headers,
                    body_mode=body_mode,
                    body=body,
                ),
            }

    async with open_authenticated_smart_classroom_client(access_payload) as (client, profile, login_result):
        path = _resolve_probe_path(
            payload.get("url"),
            allowed_base_url=profile.api_base_url,
            allowed_prefixes=SMART_ALLOWED_PATH_PREFIXES,
        )
        return {
            "status": "success",
            "provider": provider,
            "login": {
                "status": login_result.get("status"),
                "message": login_result.get("message"),
                "checked_at": login_result.get("checked_at"),
            },
            "request": {
                "method": method,
                "url": str(client.base_url).rstrip("/") + path,
                "params": params,
                "headers": {key: value for key, value in headers.items() if key.lower() not in BLOCKED_HEADER_NAMES},
                "body_mode": body_mode,
                "body": body,
            },
            "response": await _execute_probe(
                client,
                method=method,
                path=path,
                params=params,
                headers=headers,
                body_mode=body_mode,
                body=body,
            ),
        }
