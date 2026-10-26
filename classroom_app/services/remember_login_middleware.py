"""HTTP glue for 保持登录: silently re-login a remembered device.

Runs before routing. When the request carries ``ls_remember`` but no usable
access session, it asks ``remember_login_service`` for a new access token,
rewrites the request's Cookie header so every downstream dependency sees the
fresh session, and sets the rotated cookies on the response. Outcomes that need
the user's attention (another device in use, reused credential) are exposed as
``request.state.remember_notice`` for the login page.
"""
from __future__ import annotations

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request

from . import remember_login_service as remember

_SKIP_PREFIXES = ("/static/", "/assets/", "/api/internal/", "/favicon", "/logout", "/ws/", "/healthz")
_LOGIN_POSTS = frozenset({
    "/student/login", "/teacher/login", "/api/student/login/password",
    "/student/password/setup", "/api/student/password/setup",
})


def _should_try(request: Request) -> bool:
    if remember.REMEMBER_COOKIE not in request.cookies or not remember.remember_enabled():
        return False
    path = request.url.path or "/"
    if path.startswith(_SKIP_PREFIXES):
        return False
    return not (request.method == "POST" and path in _LOGIN_POSTS)


def _has_live_session(request: Request) -> bool:
    from ..dependencies import get_client_ip, verify_token
    token = request.cookies.get("access_token")
    return bool(token) and verify_token(token, get_client_ip(request)) is not None


def _replace_access_cookie(request: Request, access_token: str) -> None:
    """Rewrite the Cookie header in the ASGI scope so routing sees the new session."""
    kept: list[str] = []
    for name, value in request.scope.get("headers", []):
        if name != b"cookie":
            continue
        for part in value.decode("latin-1").split(";"):
            if part.strip() and not part.strip().startswith("access_token="):
                kept.append(part.strip())
    kept.append(f"access_token={access_token}")
    headers = [(n, v) for n, v in request.scope.get("headers", []) if n != b"cookie"]
    headers.append((b"cookie", "; ".join(kept).encode("latin-1")))
    request.scope["headers"] = headers


async def remember_login_middleware(request: Request, call_next):
    if not _should_try(request):
        return await call_next(request)
    if await run_in_threadpool(_has_live_session, request):
        return await call_next(request)

    from ..dependencies import apply_access_token_cookie, get_client_ip
    outcome = await run_in_threadpool(
        remember.refresh_from_remember_cookie,
        request.cookies.get(remember.REMEMBER_COOKIE),
        get_client_ip(request),
        str(request.headers.get("user-agent") or ""),
    )
    if outcome.kind == "ok":
        _replace_access_cookie(request, outcome.access_token)
    elif outcome.kind in (remember.NOTICE_CONFLICT, remember.NOTICE_SECURITY):
        request.state.remember_notice = outcome.kind

    response = await call_next(request)
    if outcome.kind == "ok":
        apply_access_token_cookie(response, outcome.access_token)
        remember.apply_remember_cookie(response, outcome.cookie_value, secure=remember.is_https_request(request))
    elif outcome.kind in ("clear", remember.NOTICE_SECURITY):
        remember.clear_remember_cookie(response)
        if outcome.kind == remember.NOTICE_SECURITY:
            remember.set_notice_cookie(response, remember.NOTICE_SECURITY)
    return response
