"""Trusted content types and response headers for student-uploaded files.

Student uploads are untrusted: the client-declared MIME type (upload header or
manifest) must never decide how the browser renders a file on our origin.
The effective type is derived from the file extension, and every inline
response is served with ``nosniff`` plus a sandboxing CSP so scripts inside an
uploaded document (SVG, HTML disguised as an image, ...) can never execute
with a teacher's session.
"""
from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import quote

from .file_preview_service import infer_file_preview_profile

SAFE_INLINE_IMAGE_MIME_TYPES = frozenset({
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/bmp",
    "image/avif",
})
SANDBOX_CSP = "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox"
OCTET_STREAM = "application/octet-stream"
# Pinned so results do not depend on the host's mimetypes database (the
# production image runs Python 3.12 on slim Debian without /etc/mime.types).
EXTENSION_MIME_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "jfif": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
    "avif": "image/avif",
    "heic": "image/heic",
    "heif": "image/heif",
    "tif": "image/tiff",
    "tiff": "image/tiff",
    "ico": "image/x-icon",
    "svg": "image/svg+xml",
    "pdf": "application/pdf",
    "zip": "application/zip",
}


def trusted_submission_mime_type(file_name: str) -> str:
    """Return the MIME type implied by ``file_name``'s extension only."""
    name = str(file_name or "")
    if "." not in name:
        return OCTET_STREAM
    extension = name.rsplit(".", 1)[-1].lower()
    if extension in EXTENSION_MIME_TYPES:
        return EXTENSION_MIME_TYPES[extension]
    return str(infer_file_preview_profile(name).get("mime_type") or OCTET_STREAM).lower()


def _display_name(file_info: Mapping[str, Any]) -> str:
    return str(file_info.get("relative_path") or file_info.get("original_filename") or "file")


def untrusted_file_headers() -> dict[str, str]:
    return {
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": SANDBOX_CSP,
    }


def submission_inline_response_params(file_info: Mapping[str, Any]) -> tuple[str, dict[str, str]]:
    """Media type and headers for previewing an uploaded file in the browser.

    Raster images render inline. Everything else (including SVG) is marked as
    an attachment: browsers still display it inside ``<img>``, but a direct
    navigation downloads it instead of rendering an active document.
    """
    name = _display_name(file_info)
    media_type = trusted_submission_mime_type(name)
    headers = untrusted_file_headers()
    if media_type not in SAFE_INLINE_IMAGE_MIME_TYPES:
        filename = quote(str(file_info.get("original_filename") or name.rsplit("/", 1)[-1]))
        headers["Content-Disposition"] = f"attachment; filename*=utf-8''{filename}"
    return media_type, headers


def submission_download_media_type(file_info: Mapping[str, Any]) -> str:
    return trusted_submission_mime_type(_display_name(file_info))
