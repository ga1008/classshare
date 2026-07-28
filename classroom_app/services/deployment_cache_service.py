from __future__ import annotations

import os
import re
from typing import Any


DEPLOYMENT_RELEASE_ENV = "LANSHARE_RELEASE_ID"
DEPLOYMENT_RELEASE_COOKIE = "lanshare_cache_release"
DEPLOYMENT_RELEASE_HEADER = "X-LanShare-Release"
DEPLOYMENT_CACHE_CLEAR_HEADER = '"cache"'
DEPLOYMENT_RELEASE_COOKIE_MAX_AGE = 365 * 24 * 60 * 60

_RELEASE_ID_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")
_HASHED_VITE_ASSET_PATTERN = re.compile(r"-[A-Za-z0-9_-]{8,}\.[A-Za-z0-9]+$")


def normalize_deployment_release_id(value: Any) -> str:
    normalized = _RELEASE_ID_PATTERN.sub("-", str(value or "").strip()).strip(".-_")
    return normalized[:96] or "dev"


def get_deployment_release_id() -> str:
    return normalize_deployment_release_id(os.getenv(DEPLOYMENT_RELEASE_ENV, "dev"))


def static_asset_cache_control(path: str) -> str:
    """Keep content-hashed bundles immutable and revalidate legacy static URLs.

    A large part of the legacy UI still uses manually versioned ``?v=...`` URLs.
    Treating every such URL as immutable made an unchanged version label serve
    stale JS/CSS after a deploy. Vite assets are safe to cache for a year because
    the content hash is part of the filename; all other static paths revalidate
    with their ETag.
    """

    normalized_path = str(path or "").replace("\\", "/").lstrip("/")
    if normalized_path.startswith("dist/assets/") and _HASHED_VITE_ASSET_PATTERN.search(normalized_path):
        return "public, max-age=31536000, immutable"
    return "public, no-cache, max-age=0, must-revalidate"


def apply_deployment_cache_headers(
    request: Any,
    response: Any,
    *,
    release_id: str | None = None,
) -> bool:
    """Apply release-aware browser cache invalidation without touching drafts.

    The first HTML response seen by a browser after a deployment clears only
    the HTTP cache via ``Clear-Site-Data: "cache"``. It deliberately does not
    clear cookies, local/session storage, IndexedDB, server-side draft rows, or
    uploaded files. A small HttpOnly cookie records which release that browser
    has acknowledged so the cache clear happens once per deployment.
    """

    current_release = normalize_deployment_release_id(release_id or get_deployment_release_id())
    response.headers[DEPLOYMENT_RELEASE_HEADER] = current_release

    if str(getattr(request, "method", "") or "").upper() not in {"GET", "HEAD"}:
        return False

    content_type = str(response.headers.get("content-type") or "").lower()
    if not content_type.startswith("text/html"):
        return False

    response.headers["Cache-Control"] = "private, no-store, max-age=0, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"

    request_cookies = getattr(request, "cookies", {}) or {}
    if request_cookies.get(DEPLOYMENT_RELEASE_COOKIE) == current_release:
        return False

    forwarded_proto = str(getattr(request, "headers", {}).get("x-forwarded-proto") or "").lower()
    request_url = getattr(request, "url", None)
    request_scheme = str(getattr(request_url, "scheme", "") or "").lower()
    response.headers["Clear-Site-Data"] = DEPLOYMENT_CACHE_CLEAR_HEADER
    response.set_cookie(
        key=DEPLOYMENT_RELEASE_COOKIE,
        value=current_release,
        max_age=DEPLOYMENT_RELEASE_COOKIE_MAX_AGE,
        path="/",
        secure=forwarded_proto == "https" or request_scheme == "https",
        httponly=True,
        samesite="lax",
    )
    return True
