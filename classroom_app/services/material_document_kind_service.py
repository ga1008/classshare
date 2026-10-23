"""Shared learning-document kind taxonomy.

One registry decides how a material is *presented* everywhere (library cards,
explorer tree, classroom material lists): the kind key, its label, the colour
tone and which preview mode the platform renderer should use. The serialized
``course_materials`` row already carries ``preview_type`` / render / lessondoc
metadata; this module only folds those facts into a single ``document_kind``
so no view has to reimplement the decision.

Kinds (ordered by how specific they are):

    git_repository   root folder with a parsed ``.git``
    lessondoc_pack   registered LessonDoc 2.0 pack root
    html_package     ``main.html`` + ``lesson_N/`` folder package
    html_page        single HTML / folder whose entry is an HTML page
    markdown         ``.md`` document
    pdf              PDF file
    office_document  Word
    spreadsheet      Excel
    presentation     PowerPoint
    image            raster / svg
    code             textual source file (py / js / css ...)
    text             plain text
    archive          zip / rar / 7z
    media            audio / video
    folder           plain folder
    file             anything else
"""
from __future__ import annotations

from typing import Any, Iterable

MATERIAL_KIND_REGISTRY: dict[str, dict[str, str]] = {
    "git_repository": {"label": "Git 仓库", "short": "GIT", "tone": "orange", "color": "#ea580c", "preview_mode": "folder"},
    "lessondoc_pack": {"label": "学习文档包", "short": "LD", "tone": "violet", "color": "#7c3aed", "preview_mode": "frame"},
    "html_package": {"label": "HTML 课件包", "short": "PKG", "tone": "indigo", "color": "#4f46e5", "preview_mode": "frame"},
    "html_page": {"label": "网页文档", "short": "HTML", "tone": "amber", "color": "#d97706", "preview_mode": "frame"},
    "markdown": {"label": "Markdown", "short": "MD", "tone": "teal", "color": "#0f766e", "preview_mode": "markdown"},
    "pdf": {"label": "PDF", "short": "PDF", "tone": "rose", "color": "#e11d48", "preview_mode": "document"},
    "office_document": {"label": "Word 文档", "short": "DOC", "tone": "blue", "color": "#2563eb", "preview_mode": "document"},
    "spreadsheet": {"label": "Excel 表格", "short": "XLS", "tone": "green", "color": "#059669", "preview_mode": "document"},
    "presentation": {"label": "演示文稿", "short": "PPT", "tone": "amber", "color": "#f59e0b", "preview_mode": "download"},
    "image": {"label": "图片", "short": "IMG", "tone": "purple", "color": "#8b5cf6", "preview_mode": "image"},
    "code": {"label": "源码", "short": "SRC", "tone": "sky", "color": "#0284c7", "preview_mode": "text"},
    "text": {"label": "文本", "short": "TXT", "tone": "slate", "color": "#64748b", "preview_mode": "text"},
    "archive": {"label": "压缩包", "short": "ZIP", "tone": "slate", "color": "#475569", "preview_mode": "download"},
    "media": {"label": "音视频", "short": "AV", "tone": "pink", "color": "#db2777", "preview_mode": "media"},
    "folder": {"label": "文件夹", "short": "DIR", "tone": "sky", "color": "#0ea5e9", "preview_mode": "folder"},
    "file": {"label": "文件", "short": "FILE", "tone": "slate", "color": "#94a3b8", "preview_mode": "download"},
}

# Chip groups shown by the library filter bar. ``kinds`` lists the registry
# keys each chip matches so the server-side filter and the UI agree.
MATERIAL_KIND_FILTER_GROUPS: list[dict[str, Any]] = [
    {"key": "git_repository", "label": "Git 仓库", "kinds": ["git_repository"]},
    {"key": "lessondoc_pack", "label": "学习文档包", "kinds": ["lessondoc_pack"]},
    {"key": "html", "label": "HTML 课件", "kinds": ["html_package", "html_page"]},
    {"key": "markdown", "label": "Markdown", "kinds": ["markdown"]},
    {"key": "document", "label": "办公文档", "kinds": ["pdf", "office_document", "spreadsheet", "presentation"]},
    {"key": "media", "label": "图片 / 媒体", "kinds": ["image", "media"]},
    {"key": "folder", "label": "文件夹", "kinds": ["folder"]},
    {"key": "other", "label": "其他", "kinds": ["code", "text", "archive", "file"]},
]

CODE_EXTENSIONS = {
    "py", "js", "mjs", "cjs", "ts", "tsx", "jsx", "json", "yaml", "yml", "toml", "ini", "cfg", "conf",
    "css", "scss", "less", "sh", "bat", "ps1", "sql", "java", "c", "cc", "cpp", "h", "hpp", "cs", "go",
    "rs", "rb", "php", "kt", "swift", "xml", "csv", "tsv", "ipynb", "r", "m", "lua", "pl", "env",
    "gitignore", "dockerfile", "makefile",
}
ARCHIVE_EXTENSIONS = {"zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz"}
MEDIA_EXTENSIONS = {"mp4", "webm", "mov", "avi", "mkv", "mp3", "wav", "ogg", "m4a", "flac", "aac"}
HTML_EXTENSIONS = {"html", "htm", "xhtml"}


def _ext(item: dict[str, Any]) -> str:
    ext = str(item.get("file_ext") or "").strip().lower().lstrip(".")
    if ext:
        return ext
    name = str(item.get("name") or "")
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def classify_material_kind(item: dict[str, Any]) -> str:
    """Return the registry key for one serialized material item."""
    if not item:
        return "file"
    if item.get("is_git_repository"):
        return "git_repository"
    if item.get("lessondoc_pack"):
        return "lessondoc_pack"
    node_type = str(item.get("node_type") or "")
    if node_type == "folder":
        if item.get("html_package") or item.get("is_html_package"):
            return "html_package"
        if item.get("is_renderable") and str(item.get("render_kind") or "") == "html":
            return "html_page"
        return "folder"
    ext = _ext(item)
    preview_type = str(item.get("preview_type") or "")
    if ext in HTML_EXTENSIONS:
        return "html_page"
    if preview_type == "markdown" or ext in {"md", "markdown"}:
        return "markdown"
    if preview_type == "pdf" or ext == "pdf":
        return "pdf"
    if preview_type == "document" or ext in {"doc", "docx", "odt", "rtf"}:
        return "office_document"
    if preview_type == "spreadsheet" or ext in {"xls", "xlsx", "ods"}:
        return "spreadsheet"
    if preview_type == "presentation" or ext in {"ppt", "pptx", "odp"}:
        return "presentation"
    if preview_type == "image" or ext in {"png", "jpg", "jpeg", "gif", "svg", "webp", "bmp"}:
        return "image"
    if ext in ARCHIVE_EXTENSIONS:
        return "archive"
    if ext in MEDIA_EXTENSIONS:
        return "media"
    if ext in CODE_EXTENSIONS:
        return "code"
    if preview_type == "text" or ext in {"txt", "log"}:
        return "text"
    return "file"


def describe_material_kind(kind: str) -> dict[str, str]:
    meta = MATERIAL_KIND_REGISTRY.get(kind) or MATERIAL_KIND_REGISTRY["file"]
    return {"kind": kind if kind in MATERIAL_KIND_REGISTRY else "file", **meta}


def attach_material_document_kinds(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Write ``document_kind`` / ``kind_label`` / ``kind_tone`` / ``kind_color`` on every item."""
    result = []
    for item in items:
        kind = classify_material_kind(item)
        meta = describe_material_kind(kind)
        item["document_kind"] = meta["kind"]
        item["kind_label"] = meta["label"]
        item["kind_short"] = meta["short"]
        item["kind_tone"] = meta["tone"]
        item["kind_color"] = meta["color"]
        item["kind_preview_mode"] = meta["preview_mode"]
        result.append(item)
    return result


def normalize_kind_filter(value: str | None) -> str:
    key = str(value or "").strip().lower()
    return key if any(group["key"] == key for group in MATERIAL_KIND_FILTER_GROUPS) else ""


def kinds_for_filter(filter_key: str) -> set[str]:
    for group in MATERIAL_KIND_FILTER_GROUPS:
        if group["key"] == filter_key:
            return set(group["kinds"])
    return set()


def build_kind_facets(items: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Count items per filter group so chips can show live totals."""
    counts: dict[str, int] = {group["key"]: 0 for group in MATERIAL_KIND_FILTER_GROUPS}
    for item in items:
        kind = str(item.get("document_kind") or classify_material_kind(item))
        for group in MATERIAL_KIND_FILTER_GROUPS:
            if kind in group["kinds"]:
                counts[group["key"]] += 1
                break
    return counts
