import mimetypes

from pathlib import PurePosixPath

from fastapi import HTTPException

from .file_preview_service import (
    TEXT_PREVIEW_TYPES,
    TEXTUAL_BASENAME_HINTS,
    TEXTUAL_EXTENSIONS,
    TEXTUAL_MIME_PREFIXES,
    TEXTUAL_MIME_TYPES,
    is_editable_preview_type as _is_editable_preview_type,
    is_preview_supported as _is_preview_supported,
    is_text_preview_type as _is_text_preview_type,
)


LEARNING_DOCUMENT_NAME = "readme.md"

MATERIAL_TYPE_REGISTRY = {
    "md": {
        "mime_type": "text/markdown",
        "preview_type": "markdown",
        "type_label": "Markdown",
        "ai_capability": "markdown",
    },
    "markdown": {
        "mime_type": "text/markdown",
        "preview_type": "markdown",
        "type_label": "Markdown",
        "ai_capability": "markdown",
    },
    "pdf": {
        "mime_type": "application/pdf",
        "preview_type": "pdf",
        "type_label": "PDF",
        "ai_capability": "document",
    },
    "doc": {
        "mime_type": "application/msword",
        "preview_type": "document",
        "type_label": "Word",
        "ai_capability": "document",
    },
    "docx": {
        "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "preview_type": "document",
        "type_label": "Word",
        "ai_capability": "document",
    },
    "xls": {
        "mime_type": "application/vnd.ms-excel",
        "preview_type": "spreadsheet",
        "type_label": "Excel",
        "ai_capability": "spreadsheet",
    },
    "xlsx": {
        "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "preview_type": "spreadsheet",
        "type_label": "Excel",
        "ai_capability": "spreadsheet",
    },
    "ppt": {
        "mime_type": "application/vnd.ms-powerpoint",
        "preview_type": "presentation",
        "type_label": "PPT",
        "ai_capability": "presentation",
    },
    "pptx": {
        "mime_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "preview_type": "presentation",
        "type_label": "PPT",
        "ai_capability": "presentation",
    },
    "txt": {
        "mime_type": "text/plain",
        "preview_type": "text",
        "type_label": "文本",
        "ai_capability": "text",
    },
    "png": {
        "mime_type": "image/png",
        "preview_type": "image",
        "type_label": "图片",
        "ai_capability": "image",
    },
    "jpg": {
        "mime_type": "image/jpeg",
        "preview_type": "image",
        "type_label": "图片",
        "ai_capability": "image",
    },
    "jpeg": {
        "mime_type": "image/jpeg",
        "preview_type": "image",
        "type_label": "图片",
        "ai_capability": "image",
    },
    "gif": {
        "mime_type": "image/gif",
        "preview_type": "image",
        "type_label": "图片",
        "ai_capability": "image",
    },
    "svg": {
        "mime_type": "image/svg+xml",
        "preview_type": "image",
        "type_label": "图片",
        "ai_capability": "image",
    },
}


def normalize_material_path(raw_path: str, fallback_name: str = "untitled") -> str:
    normalized = (raw_path or "").replace("\\", "/").strip().strip("/")
    if not normalized:
        normalized = fallback_name

    parts = []
    for part in PurePosixPath(normalized).parts:
        if part in ("", "."):
            continue
        if part == "..":
            raise HTTPException(400, "材料路径不能包含上级目录跳转")
        parts.append(part.strip())

    if not parts:
        raise HTTPException(400, "材料路径不能为空")

    return "/".join(parts)


def is_descendant_path(path_value: str, ancestor_path: str) -> bool:
    return path_value == ancestor_path or path_value.startswith(f"{ancestor_path}/")


def is_git_internal_material_path(path_value: str | None) -> bool:
    parts = [segment for segment in str(path_value or "").split("/") if segment and segment != "."]
    return any(segment == ".git" for segment in parts[1:])


def is_learning_document_name(name: str | None) -> bool:
    return str(name or "").strip().lower() == LEARNING_DOCUMENT_NAME


def is_text_preview_type(preview_type: str | None) -> bool:
    return _is_text_preview_type(preview_type)


def is_preview_supported(preview_type: str | None) -> bool:
    return _is_preview_supported(preview_type)


def is_editable_material(item) -> bool:
    node_type = ""
    preview_type = ""
    if isinstance(item, dict):
        node_type = str(item.get("node_type") or "")
        preview_type = str(item.get("preview_type") or "")
    else:
        try:
            node_type = str(item["node_type"] or "")
            preview_type = str(item["preview_type"] or "")
        except (KeyError, TypeError):
            return False
    return node_type == "file" and _is_editable_preview_type(preview_type)


def _is_textual_material(file_name: str, mime_type: str | None = None) -> bool:
    normalized_name = str(file_name or "").strip()
    lower_name = normalized_name.lower()
    extension = ""
    if "." in normalized_name:
        extension = normalized_name.rsplit(".", 1)[-1].lower()

    normalized_mime = str(mime_type or "").strip().lower()
    if normalized_mime.startswith(TEXTUAL_MIME_PREFIXES) or normalized_mime in TEXTUAL_MIME_TYPES:
        return True
    if extension in TEXTUAL_EXTENSIONS:
        return True
    return lower_name in TEXTUAL_BASENAME_HINTS


def _infer_text_type_label(file_name: str, extension: str) -> str:
    if extension in {"md", "markdown"}:
        return "Markdown"
    if extension == "txt":
        return "文本"
    if extension:
        return extension.upper()

    normalized_name = str(file_name or "").strip()
    return normalized_name or "文本"


def infer_material_profile(file_name: str, content_type: str | None = None) -> dict:
    extension = ""
    if "." in file_name:
        extension = file_name.rsplit(".", 1)[-1].lower()

    profile = MATERIAL_TYPE_REGISTRY.get(extension, {}).copy()
    guessed_mime = content_type or profile.get("mime_type") or mimetypes.guess_type(file_name)[0] or "application/octet-stream"
    if not profile and _is_textual_material(file_name, guessed_mime):
        profile = {
            "mime_type": guessed_mime if guessed_mime != "application/octet-stream" else "text/plain",
            "preview_type": "text",
            "type_label": _infer_text_type_label(file_name, extension),
            "ai_capability": "text",
        }
        guessed_mime = profile["mime_type"]

    return {
        "file_ext": extension,
        "mime_type": guessed_mime,
        "preview_type": profile.get("preview_type", "binary"),
        "type_label": profile.get("type_label", extension.upper() if extension else "文件"),
        "ai_capability": profile.get("ai_capability", "none"),
        "preview_supported": is_preview_supported(profile.get("preview_type")),
        "is_markdown": profile.get("preview_type") == "markdown",
        "is_text": is_text_preview_type(profile.get("preview_type")),
        "editable": profile.get("preview_type") in TEXT_PREVIEW_TYPES,
    }


def serialize_material_row(row, extra: dict | None = None) -> dict:
    item = dict(row)
    preview_type = str(item.get("preview_type") or "")
    item["is_folder"] = item.get("node_type") == "folder"
    item["preview_supported"] = is_preview_supported(preview_type)
    item["can_ai_parse"] = item.get("ai_capability") == "markdown"
    item["can_ai_optimize"] = item.get("ai_capability") == "markdown"
    item["is_markdown"] = preview_type == "markdown"
    item["is_text"] = is_text_preview_type(preview_type)
    item["is_image"] = preview_type == "image"
    item["editable"] = is_editable_material(item)
    item["can_edit_source"] = item["editable"]
    item["path_depth"] = len([segment for segment in str(item.get("material_path", "")).split("/") if segment])
    item["git_repo_status"] = str(item.get("git_repo_status") or "plain")
    item["git_provider"] = str(item.get("git_provider") or "")
    item["git_remote_name"] = str(item.get("git_remote_name") or "")
    item["git_remote_url"] = str(item.get("git_remote_url") or "")
    item["git_remote_host"] = str(item.get("git_remote_host") or "")
    item["git_remote_protocol"] = str(item.get("git_remote_protocol") or "")
    item["git_default_branch"] = str(item.get("git_default_branch") or "")
    item["git_head_branch"] = str(item.get("git_head_branch") or "")
    item["git_detect_error"] = str(item.get("git_detect_error") or "")
    item["git_detected_at"] = str(item.get("git_detected_at") or "")
    item["is_git_repository"] = (
        item.get("node_type") == "folder"
        and int(item.get("id") or 0) == int(item.get("root_id") or 0)
        and item["git_repo_status"] == "repository"
    )
    if extra:
        item.update(extra)
    return item


def get_learning_document_map(conn, folder_ids: list[int]) -> dict[int, dict]:
    normalized_ids = [int(folder_id) for folder_id in folder_ids if folder_id]
    if not normalized_ids:
        return {}

    placeholders = ",".join("?" for _ in normalized_ids)
    rows = conn.execute(
        f"""
        SELECT id, parent_id, name, material_path, preview_type, node_type
        FROM course_materials
        WHERE node_type = 'file'
          AND parent_id IN ({placeholders})
          AND LOWER(name) = ?
        ORDER BY parent_id, name COLLATE NOCASE
        """,
        normalized_ids + [LEARNING_DOCUMENT_NAME],
    ).fetchall()

    result: dict[int, dict] = {}
    for row in rows:
        parent_id = row["parent_id"]
        if parent_id is None:
            continue
        result[int(parent_id)] = dict(row)
    return result


def attach_learning_document_metadata(conn, items: list[dict]) -> list[dict]:
    folder_ids = [int(item["id"]) for item in items if item.get("node_type") == "folder" and item.get("id")]
    learning_doc_map = get_learning_document_map(conn, folder_ids)

    for item in items:
        learning_doc = learning_doc_map.get(int(item["id"])) if item.get("node_type") == "folder" and item.get("id") else None
        item["document_readme_id"] = int(learning_doc["id"]) if learning_doc else None
        item["document_readme_name"] = learning_doc["name"] if learning_doc else None
        item["is_learning_document_dir"] = bool(learning_doc)
        item["has_document_view"] = bool(learning_doc)
    return items


def _query_sibling(conn, teacher_id: int, parent_id: int | None, name: str):
    if parent_id is None:
        return conn.execute(
            "SELECT id FROM course_materials WHERE teacher_id = ? AND parent_id IS NULL AND name = ? LIMIT 1",
            (teacher_id, name),
        ).fetchone()

    return conn.execute(
        "SELECT id FROM course_materials WHERE teacher_id = ? AND parent_id = ? AND name = ? LIMIT 1",
        (teacher_id, parent_id, name),
    ).fetchone()


def make_unique_material_name(conn, teacher_id: int, parent_id: int | None, desired_name: str) -> str:
    candidate = desired_name.strip() or "untitled"
    if not _query_sibling(conn, teacher_id, parent_id, candidate):
        return candidate

    base_name = candidate
    extension = ""
    if "." in candidate and not candidate.startswith("."):
        base_name, extension = candidate.rsplit(".", 1)
        extension = f".{extension}"

    counter = 2
    while True:
        next_name = f"{base_name} ({counter}){extension}"
        if not _query_sibling(conn, teacher_id, parent_id, next_name):
            return next_name
        counter += 1


def ensure_classroom_access(conn, class_offering_id: int, user: dict):
    offering = conn.execute(
        """
        SELECT o.*, c.name AS course_name, cl.name AS class_name
        FROM class_offerings o
        JOIN courses c ON o.course_id = c.id
        JOIN classes cl ON o.class_id = cl.id
        WHERE o.id = ?
        """,
        (class_offering_id,),
    ).fetchone()
    if not offering:
        raise HTTPException(404, "课堂不存在")

    if user["role"] == "teacher":
        if int(offering["teacher_id"]) != int(user["id"]):
            raise HTTPException(403, "无权访问当前课堂")
        return offering

    student = conn.execute(
        "SELECT class_id FROM students WHERE id = ?",
        (user["id"],),
    ).fetchone()
    if not student or int(student["class_id"]) != int(offering["class_id"]):
        raise HTTPException(403, "无权访问当前课堂")
    return offering


def ensure_teacher_material_owner(conn, material_id: int, teacher_id: int):
    row = conn.execute(
        "SELECT * FROM course_materials WHERE id = ? AND teacher_id = ?",
        (material_id, teacher_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "材料不存在或无权操作")
    if is_git_internal_material_path(row["material_path"]):
        raise HTTPException(404, "材料不存在或无权操作")
    return row


def _get_student_offering_ids(conn, student_id: int) -> list[int]:
    rows = conn.execute(
        """
        SELECT o.id
        FROM class_offerings o
        JOIN students s ON s.class_id = o.class_id
        WHERE s.id = ?
        """,
        (student_id,),
    ).fetchall()
    return [int(row["id"]) for row in rows]


def ensure_user_material_access(conn, material_id: int, user: dict):
    material = conn.execute(
        "SELECT * FROM course_materials WHERE id = ?",
        (material_id,),
    ).fetchone()
    if not material:
        raise HTTPException(404, "材料不存在")
    if is_git_internal_material_path(material["material_path"]):
        raise HTTPException(404, "材料不存在")

    if user["role"] == "teacher":
        if int(material["teacher_id"]) != int(user["id"]):
            raise HTTPException(403, "无权访问该材料")
        return material

    offering_ids = _get_student_offering_ids(conn, int(user["id"]))
    if not offering_ids:
        raise HTTPException(403, "当前学生没有可访问的课堂材料")

    placeholders = ",".join("?" for _ in offering_ids)
    params = offering_ids + [material["root_id"], material["material_path"], material["material_path"]]
    allowed = conn.execute(
        f"""
        SELECT assigned.*
        FROM course_material_assignments a
        JOIN course_materials assigned ON assigned.id = a.material_id
        WHERE a.class_offering_id IN ({placeholders})
          AND assigned.root_id = ?
          AND (? = assigned.material_path OR ? LIKE assigned.material_path || '/%')
        ORDER BY LENGTH(assigned.material_path) DESC
        LIMIT 1
        """,
        params,
    ).fetchone()
    if not allowed:
        raise HTTPException(403, "无权访问该材料")
    return material


def get_material_breadcrumbs(conn, material_id: int) -> list[dict]:
    breadcrumbs = []
    current = conn.execute(
        "SELECT id, parent_id, name, node_type, material_path FROM course_materials WHERE id = ?",
        (material_id,),
    ).fetchone()
    while current:
        breadcrumbs.append(
            {
                "id": current["id"],
                "parent_id": current["parent_id"],
                "name": current["name"],
                "node_type": current["node_type"],
                "material_path": current["material_path"],
            }
        )
        if current["parent_id"] is None:
            break
        current = conn.execute(
            "SELECT id, parent_id, name, node_type, material_path FROM course_materials WHERE id = ?",
            (current["parent_id"],),
        ).fetchone()
    breadcrumbs.reverse()
    return breadcrumbs


def get_effective_assignment_nodes(conn, class_offering_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT m.*, a.created_at AS assigned_at
        FROM course_material_assignments a
        JOIN course_materials m ON m.id = a.material_id
        WHERE a.class_offering_id = ?
        ORDER BY m.root_id, LENGTH(m.material_path), m.material_path
        """,
        (class_offering_id,),
    ).fetchall()

    effective = []
    for row in rows:
        row_dict = dict(row)
        if is_git_internal_material_path(row_dict["material_path"]):
            continue
        if any(
            existing["root_id"] == row_dict["root_id"]
            and is_descendant_path(row_dict["material_path"], existing["material_path"])
            for existing in effective
        ):
            continue
        effective.append(row_dict)
    return effective


def get_nearest_assignment_anchor(conn, class_offering_id: int, material_row) -> dict | None:
    rows = conn.execute(
        """
        SELECT m.*
        FROM course_material_assignments a
        JOIN course_materials m ON m.id = a.material_id
        WHERE a.class_offering_id = ?
          AND m.root_id = ?
        ORDER BY LENGTH(m.material_path) DESC
        """,
        (class_offering_id, material_row["root_id"]),
    ).fetchall()

    current_path = material_row["material_path"]
    for row in rows:
        row_dict = dict(row)
        if is_git_internal_material_path(row_dict["material_path"]):
            continue
        if is_descendant_path(current_path, row_dict["material_path"]):
            return row_dict
    return None
