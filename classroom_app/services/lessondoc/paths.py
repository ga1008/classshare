"""Shared package-relative resource policy for media, HTML and backgrounds."""

import re
from urllib.parse import unquote, urlsplit

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def local_src_ok(value: str, *, anchor: bool = False) -> bool:
    if not isinstance(value, str) or not value:
        return False
    decoded = value
    # Reject nested escaping that would change meaning at another serving layer.
    for _ in range(4):
        new = unquote(decoded)
        if new == decoded:
            break
        decoded = new
    else:
        return False
    if decoded != decoded.strip() or _CONTROL.search(decoded) or "\\" in decoded:
        return False
    if decoded.startswith("#"):
        return anchor and len(decoded) > 1
    try:
        url = urlsplit(decoded)
    except ValueError:
        return False
    if url.scheme or url.netloc or not url.path or url.path.startswith("/"):
        return False
    parts = url.path.split("/")
    if parts[:2] == ["..", "assets"]:
        parts = parts[2:]
    return bool(parts) and all(part not in ("", "..") for part in parts)
