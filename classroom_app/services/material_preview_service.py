"""Canonical preview targets for one material item.

The platform has several ways to show a material (markdown runtime, image,
HTML render shell, page-image document renderer, native media players). Each
consumer used to pick one by hand. This module writes a single ``preview``
contract on a serialized item so the shared front-end renderer registry
(``static/js/material_renderers.js``) can dispatch without guessing:

    preview_mode          markdown | text | image | frame | document | media | folder | download
    preview_url           page to open in a new tab (always the richest option)
    preview_frame_url     URL that can be embedded in an iframe ("" when none)
    preview_image_url     raw image URL for ``image``
    preview_media_url     inline media URL for ``media`` (and the PDF itself)
    preview_content_url   JSON source endpoint for markdown / text (teacher only)
"""
from __future__ import annotations

from typing import Any, Iterable

from .material_document_kind_service import classify_material_kind, describe_material_kind

DOCUMENT_PREVIEW_FORMATS = {"pdf", "doc", "docx", "xls", "xlsx"}


def _ext(item: dict[str, Any]) -> str:
    ext = str(item.get("file_ext") or "").strip().lower().lstrip(".")
    if ext:
        return ext
    name = str(item.get("name") or "")
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def supports_document_preview(item: dict[str, Any]) -> bool:
    return str(item.get("node_type") or "") == "file" and _ext(item) in DOCUMENT_PREVIEW_FORMATS


def build_material_preview(item: dict[str, Any], *, can_edit: bool = False) -> dict[str, Any]:
    material_id = int(item.get("id") or 0)
    kind = str(item.get("document_kind") or classify_material_kind(item))
    mode = describe_material_kind(kind)["preview_mode"]
    node_type = str(item.get("node_type") or "")
    render_url = str(item.get("render_shell_url") or item.get("render_url") or "") if item.get("is_renderable") else ""
    payload = {
        "preview_mode": mode,
        "preview_url": "",
        "preview_frame_url": "",
        "preview_image_url": "",
        "preview_media_url": "",
        "preview_content_url": "",
    }
    if node_type == "folder":
        # A renderable folder (HTML package, git repository with an index page,
        # LessonDoc pack) opens in the render shell; README-only folders fall
        # back to the markdown viewer; plain folders have no preview.
        if render_url:
            payload["preview_mode"] = "frame"
            payload["preview_url"] = render_url
            payload["preview_frame_url"] = str(item.get("render_url") or "")
        elif item.get("document_readme_id"):
            payload["preview_mode"] = "markdown"
            payload["preview_url"] = f"/materials/view/{int(item['document_readme_id'])}"
        else:
            payload["preview_mode"] = "folder"
        return payload

    if mode in {"markdown", "text"}:
        payload["preview_url"] = f"/materials/view/{material_id}"
        if can_edit or item.get("editable"):
            payload["preview_content_url"] = f"/api/materials/{material_id}/content"
    elif mode == "image":
        payload["preview_image_url"] = f"/materials/raw/{material_id}"
        # The viewer page only knows registered image types; older rows fall back to the raw image.
        payload["preview_url"] = f"/materials/view/{material_id}" if str(item.get("preview_type") or "") == "image" else payload["preview_image_url"]
    elif mode == "frame":
        if render_url:
            payload["preview_url"] = render_url
            payload["preview_frame_url"] = str(item.get("render_url") or "")
        else:
            payload["preview_mode"] = "text"
            payload["preview_url"] = f"/materials/view/{material_id}"
            if can_edit or item.get("editable"):
                payload["preview_content_url"] = f"/api/materials/{material_id}/content"
    elif mode == "document":
        if supports_document_preview(item):
            payload["preview_url"] = f"/materials/document-preview/{material_id}"
            payload["preview_frame_url"] = f"/materials/document-preview/{material_id}"
            if _ext(item) == "pdf":
                payload["preview_media_url"] = f"/materials/inline/{material_id}"
        else:
            payload["preview_mode"] = "download"
    elif mode == "media":
        payload["preview_url"] = f"/materials/inline/{material_id}"
        payload["preview_media_url"] = f"/materials/inline/{material_id}"
    return payload


def attach_material_preview_metadata(items: Iterable[dict[str, Any]], *, can_edit: bool = False) -> list[dict[str, Any]]:
    result = []
    for item in items:
        item.update(build_material_preview(item, can_edit=can_edit))
        result.append(item)
    return result
