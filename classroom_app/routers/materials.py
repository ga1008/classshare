import asyncio
import json
import hashlib
import os
import re
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

import aiofiles
import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from ..config import GLOBAL_FILES_DIR
from ..core import ai_client, templates
from ..database import get_db_connection
from ..dependencies import get_current_teacher, get_current_user
from ..services.file_handler import delete_file_safely
from ..services.file_service import save_file_globally
from ..services.download_policy import apply_download_policy, ensure_download_allowed
from ..services.file_preview_service import TEXT_CONTENT_ENCODINGS
from ..services.materials_service import (
    MATERIAL_TYPE_REGISTRY,
    attach_home_learning_material_briefs,
    attach_learning_material_briefs,
    attach_learning_document_metadata,
    ensure_classroom_access,
    ensure_teacher_learning_material_owner,
    ensure_teacher_material_owner,
    ensure_user_material_access,
    get_effective_assignment_nodes,
    get_material_breadcrumbs,
    get_nearest_assignment_anchor,
    infer_material_profile,
    is_descendant_path,
    is_editable_material,
    is_git_internal_material_path,
    make_unique_material_name,
    normalize_material_path,
    serialize_material_row,
    sync_classroom_learning_material_assignments,
)
from ..services.course_planning_service import build_timeline_home_entry
from ..services.materials_git_service import (
    attach_git_repository_metadata,
    execute_material_repository_action,
    get_material_repository_detail,
    refresh_root_git_metadata,
    save_material_repository_credential,
)
from ..services.session_material_generation_service import (
    create_generation_task,
    extract_example_documents,
    get_teacher_session_with_material_state,
    normalize_document_type,
    normalize_requirement_text,
    run_generation_task,
)

router = APIRouter()


class MaterialAssignRequest(BaseModel):
    class_offering_ids: list[int] = []


class MaterialBatchDownloadRequest(BaseModel):
    material_ids: list[int]


class MaterialContentUpdateRequest(BaseModel):
    content: str = ""
    encoding: str | None = None


class MaterialRepositoryCommandRequest(BaseModel):
    action: str = "update"
    command: str = ""


class MaterialRepositoryCredentialRequest(BaseModel):
    username: str = ""
    secret: str = ""
    auth_mode: str = "password"


class ClassroomLearningMaterialUpdateRequest(BaseModel):
    learning_material_id: int | None = None


class ClassroomHomeLearningMaterialUpdateRequest(BaseModel):
    learning_material_id: int | None = None


MATERIAL_LIBRARY_SORT_LABELS = {
    "name": "名称",
    "created_at": "创建时间",
    "updated_at": "更新时间",
}
MATERIAL_LIBRARY_DEFAULT_SORT_BY = "name"
MATERIAL_LIBRARY_DEFAULT_SORT_ORDER = "asc"
MATERIAL_LIBRARY_ALLOWED_SORT_ORDERS = {"asc", "desc"}


def _row_value(row, key: str, default=None):
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _cleanup_temp_file(path_value: str):
    try:
        Path(path_value).unlink(missing_ok=True)
    except Exception as exc:
        print(f"[WARN] 临时文件清理失败: {exc}")


def _load_material_storage_path(material_row) -> Path:
    file_hash = _row_value(material_row, "file_hash")
    if not file_hash:
        raise HTTPException(400, "当前节点不是文件材料")
    file_path = Path(GLOBAL_FILES_DIR) / file_hash
    if not file_path.exists():
        raise HTTPException(404, "材料文件不存在")
    return file_path


async def _load_material_markdown(material_row, prefer_optimized: bool = False) -> str:
    content, _encoding = await _load_material_text_content(material_row, prefer_optimized=prefer_optimized)
    return content


def _decode_text_bytes(raw_bytes: bytes) -> tuple[str, str]:
    if b"\x00" in raw_bytes:
        raise HTTPException(400, "当前材料不是可编辑的文本文件")

    for encoding in TEXT_CONTENT_ENCODINGS:
        try:
            return raw_bytes.decode(encoding), encoding
        except UnicodeDecodeError:
            continue

    raise HTTPException(400, "当前文本材料编码暂不支持在线编辑")


async def _load_material_text_content(material_row, prefer_optimized: bool = False) -> tuple[str, str]:
    optimized_content = _row_value(material_row, "ai_optimized_markdown")
    if prefer_optimized and optimized_content:
        return optimized_content, "utf-8"

    file_path = _load_material_storage_path(material_row)
    async with aiofiles.open(file_path, "rb") as handle:
        raw_bytes = await handle.read()
    return _decode_text_bytes(raw_bytes)


async def _write_material_file(file_hash: str, payload_bytes: bytes):
    GLOBAL_FILES_DIR.mkdir(parents=True, exist_ok=True)
    target_path = Path(GLOBAL_FILES_DIR) / file_hash
    if target_path.exists():
        return target_path

    async with aiofiles.open(target_path, "wb") as handle:
        await handle.write(payload_bytes)
    return target_path


def _serialize_material_items(conn, rows) -> list[dict]:
    items = [serialize_material_row(row) for row in rows]
    items = attach_learning_document_metadata(conn, items)
    items = attach_git_repository_metadata(conn, items)
    return [_decorate_material_download_policy(item) for item in items]


def _decorate_learning_document_item(item: dict) -> dict:
    if item.get("document_readme_id"):
        item["document_viewer_url"] = f"/materials/view/{item['document_readme_id']}"
    else:
        item["document_viewer_url"] = ""
    return item


def _decorate_material_download_policy(item: dict) -> dict:
    resource_label = "课堂材料" if item.get("node_type") == "file" else "材料压缩包"
    return apply_download_policy(item, resource_label=resource_label)


def _strip_code_fence(raw_text: str) -> str:
    text = (raw_text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text


async def _call_ai_chat(system_prompt: str, new_message: str, capability: str = "thinking") -> str:
    payload = {
        "system_prompt": system_prompt,
        "messages": [],
        "new_message": new_message,
        "base64_urls": [],
        "model_capability": capability,
        "web_search_enabled": False,
    }
    try:
        response = await ai_client.post("/api/ai/chat", json=payload, timeout=180.0)
        response.raise_for_status()
        data = response.json()
        return str(data.get("response_text") or "").strip()
    except httpx.ConnectError:
        raise HTTPException(503, "AI 助手服务未运行，请先启动 ai_assistant.py。")
    except httpx.TimeoutException:
        raise HTTPException(504, "AI 服务响应超时，请稍后重试。")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, f"AI 服务错误: {exc.response.text}")
    except Exception as exc:
        raise HTTPException(500, f"AI 请求失败: {exc}")


def _parse_ai_json(raw_text: str) -> dict:
    cleaned = _strip_code_fence(raw_text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start:end + 1])
        raise


def _normalize_material_keyword(raw_keyword: str | None) -> str:
    return " ".join(str(raw_keyword or "").split())[:100]


def _normalize_material_sort(sort_by: str | None, sort_order: str | None) -> tuple[str, str]:
    normalized_sort_by = str(sort_by or MATERIAL_LIBRARY_DEFAULT_SORT_BY).strip().lower()
    if normalized_sort_by not in MATERIAL_LIBRARY_SORT_LABELS:
        normalized_sort_by = MATERIAL_LIBRARY_DEFAULT_SORT_BY

    default_sort_order = "asc" if normalized_sort_by == "name" else "desc"
    normalized_sort_order = str(sort_order or default_sort_order).strip().lower()
    if normalized_sort_order not in MATERIAL_LIBRARY_ALLOWED_SORT_ORDERS:
        normalized_sort_order = default_sort_order

    return normalized_sort_by, normalized_sort_order


def _build_material_order_clause(sort_by: str, sort_order: str) -> str:
    direction = "ASC" if sort_order == "asc" else "DESC"
    name_fallback = "m.name COLLATE NOCASE ASC, m.id DESC"

    if sort_by == "created_at":
        return (
            "CASE WHEN m.node_type = 'folder' THEN 0 ELSE 1 END, "
            f"m.created_at {direction}, {name_fallback}"
        )
    if sort_by == "updated_at":
        return (
            "CASE WHEN m.node_type = 'folder' THEN 0 ELSE 1 END, "
            f"m.updated_at {direction}, {name_fallback}"
        )
    return (
        "CASE WHEN m.node_type = 'folder' THEN 0 ELSE 1 END, "
        f"m.name COLLATE NOCASE {direction}, m.updated_at DESC, m.id DESC"
    )


def _list_material_rows_for_parent(
    conn,
    teacher_id: int,
    parent_row,
    keyword: str = "",
    sort_by: str = MATERIAL_LIBRARY_DEFAULT_SORT_BY,
    sort_order: str = MATERIAL_LIBRARY_DEFAULT_SORT_ORDER,
):
    keyword = _normalize_material_keyword(keyword)
    sort_by, sort_order = _normalize_material_sort(sort_by, sort_order)

    conditions = ["m.teacher_id = ?"]
    params: list[object] = [teacher_id]

    if parent_row is None:
        if keyword:
            keyword_pattern = f"%{keyword}%"
            conditions.append("(m.name LIKE ? COLLATE NOCASE OR m.material_path LIKE ? COLLATE NOCASE)")
            params.extend([keyword_pattern, keyword_pattern])
        else:
            conditions.append("m.parent_id IS NULL")
    else:
        if keyword:
            subtree_pattern = f"{parent_row['material_path']}/%"
            keyword_pattern = f"%{keyword}%"
            conditions.append("m.material_path LIKE ?")
            params.append(subtree_pattern)
            conditions.append("m.id != ?")
            params.append(parent_row["id"])
            conditions.append("(m.name LIKE ? COLLATE NOCASE OR m.material_path LIKE ? COLLATE NOCASE)")
            params.extend([keyword_pattern, keyword_pattern])
        else:
            conditions.append("m.parent_id = ?")
            params.append(parent_row["id"])

    order_clause = _build_material_order_clause(sort_by, sort_order)
    query = f"""
        SELECT m.*,
               (SELECT COUNT(*) FROM course_materials child WHERE child.parent_id = m.id AND child.name != '.git') AS child_count,
               (SELECT COUNT(*) FROM course_material_assignments a WHERE a.material_id = m.id) AS assignment_count
        FROM course_materials m
        WHERE {' AND '.join(conditions)}
        ORDER BY {order_clause}
    """
    rows = conn.execute(query, params).fetchall()
    return [row for row in rows if not is_git_internal_material_path(row["material_path"])]


def _get_teacher_material_stats(conn, teacher_id: int) -> dict:
    material_rows = conn.execute(
        """
        SELECT id, parent_id, material_path, node_type, file_size, updated_at
        FROM course_materials
        WHERE teacher_id = ?
        """,
        (teacher_id,),
    ).fetchall()
    visible_rows = [dict(row) for row in material_rows if not is_git_internal_material_path(row["material_path"])]

    assignment_rows = conn.execute(
        """
        SELECT a.material_id, a.class_offering_id, m.material_path
        FROM course_material_assignments a
        JOIN course_materials m ON m.id = a.material_id
        WHERE m.teacher_id = ?
        """,
        (teacher_id,),
    ).fetchall()
    visible_assignments = [dict(row) for row in assignment_rows if not is_git_internal_material_path(row["material_path"])]

    return {
        "root_count": sum(1 for row in visible_rows if row["parent_id"] is None),
        "total_count": len(visible_rows),
        "folder_count": sum(1 for row in visible_rows if row["node_type"] == "folder"),
        "file_count": sum(1 for row in visible_rows if row["node_type"] == "file"),
        "total_size": sum(int(row["file_size"] or 0) for row in visible_rows if row["node_type"] == "file"),
        "latest_updated_at": max((row["updated_at"] for row in visible_rows if row["updated_at"]), default=None),
        "assigned_material_count": len({int(row["material_id"]) for row in visible_assignments}),
        "classroom_count": len({int(row["class_offering_id"]) for row in visible_assignments}),
        "assignment_count": len(visible_assignments),
    }


def _build_teacher_library_overview(parent_row, keyword: str, sort_by: str, sort_order: str, result_count: int) -> dict:
    normalized_keyword = _normalize_material_keyword(keyword)
    normalized_sort_by, normalized_sort_order = _normalize_material_sort(sort_by, sort_order)
    scope_name = parent_row["name"] if parent_row else "材料库根目录"
    scope_path = parent_row["material_path"] if parent_row else ""
    search_scope_label = f"{scope_name}及其子级" if parent_row else "全部材料"
    if normalized_keyword:
        description = f"在{search_scope_label}中匹配到 {result_count} 项"
    else:
        description = f"当前目录显示 {result_count} 项"

    return {
        "scope_name": scope_name,
        "scope_path": scope_path,
        "description": description,
        "result_count": int(result_count),
        "search_active": bool(normalized_keyword),
        "search_keyword": normalized_keyword,
        "search_scope_label": search_scope_label,
        "sort_by": normalized_sort_by,
        "sort_order": normalized_sort_order,
        "sort_label": MATERIAL_LIBRARY_SORT_LABELS[normalized_sort_by],
    }


def _count_global_file_references(conn, file_hash: str) -> int:
    material_refs = conn.execute(
        "SELECT COUNT(*) FROM course_materials WHERE file_hash = ?",
        (file_hash,),
    ).fetchone()[0]
    course_refs = conn.execute(
        "SELECT COUNT(*) FROM course_files WHERE file_hash = ?",
        (file_hash,),
    ).fetchone()[0]
    return int(material_refs) + int(course_refs)


def _estimate_material_archive_size(conn, material_rows: list[dict]) -> int:
    selected_row_ids: set[int] = set()
    total_size = 0

    for material_row in material_rows:
        subtree_rows = _collect_subtree_rows(conn, material_row, include_internal=False)
        for row in subtree_rows:
            row_dict = dict(row)
            row_id = int(row_dict["id"])
            if row_id in selected_row_ids:
                continue
            selected_row_ids.add(row_id)
            if row_dict["node_type"] != "file":
                continue
            total_size += int(row_dict.get("file_size") or 0)

    return total_size


def _collect_subtree_rows(conn, material_row, include_internal: bool = True):
    rows = conn.execute(
        """
        SELECT *
        FROM course_materials
        WHERE root_id = ?
          AND (material_path = ? OR material_path LIKE ?)
        ORDER BY material_path
        """,
        (material_row["root_id"], material_row["material_path"], f"{material_row['material_path']}/%"),
    ).fetchall()
    if include_internal:
        return rows
    return [row for row in rows if not is_git_internal_material_path(row["material_path"])]


HOME_DOCUMENT_NAME_SCORES = {
    "readme.md": 110,
    "index.md": 105,
    "home.md": 100,
    "首页.md": 115,
    "目录.md": 104,
    "课程目录.md": 108,
    "课程简介.md": 104,
    "introduction.md": 96,
    "overview.md": 92,
    "getting-started.md": 88,
}


def _material_path_parts(path_value: str | None) -> list[str]:
    return [part for part in str(path_value or "").replace("\\", "/").split("/") if part and part != "."]


def _infer_home_material_row(file_rows: list[dict], root_material_path: str | None) -> dict | None:
    root_parts = _material_path_parts(root_material_path)
    best_row = None
    best_score = 0
    homepage_keywords = ("首页", "目录", "简介", "导读", "home", "index", "readme", "intro", "overview", "getting-started")
    lesson_markers = ("lesson", "chapter", "lecture", "unit", "第1课", "第01课", "第1次课", "第01次课")

    for row in file_rows:
        if str(row.get("preview_type") or "") != "markdown":
            continue
        name = str(row.get("name") or "").strip()
        path_value = str(row.get("material_path") or "").strip()
        lower_name = name.lower()
        lower_path = path_value.lower()
        path_parts = _material_path_parts(path_value)
        relative_depth = max(1, len(path_parts) - len(root_parts))
        stem = lower_name.rsplit(".", 1)[0]
        lesson_path_match = (
            any(marker in lower_path for marker in lesson_markers)
            or re.search(r"(?:^|/)(?:lesson|chapter|lecture|unit|l)[\s_-]*0*\d+(?:[/_.-]|$)", lower_path)
            or re.search(r"第\s*\d+\s*(?:课|次课|讲)", path_value)
        )

        score = HOME_DOCUMENT_NAME_SCORES.get(lower_name, 0)
        if any(keyword in lower_name or keyword in lower_path for keyword in homepage_keywords):
            score += 42
        if relative_depth == 1:
            score += 40
        elif relative_depth == 2:
            score += 14
        else:
            score -= min(36, relative_depth * 6)
        if re.search(r"(?:^|[/_-])(?:0|00|intro|start)(?:[/_.-]|$)", lower_path):
            score += 18
        if lesson_path_match:
            score -= 105
        if stem.isdigit():
            score -= 30

        if score > best_score:
            best_score = score
            best_row = row

    return best_row if best_score >= 70 else None


def _coerce_positive_int(value) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _collect_ai_home_assignments(
    parsed_result: dict,
    *,
    desired_offering_ids: list[int],
    file_id_map: dict[int, dict],
    fallback_home_row: dict | None,
) -> dict[int, dict]:
    raw_items: list[dict] = []
    for key in ("home_assignments", "homepage_assignments"):
        value = parsed_result.get(key)
        if isinstance(value, list):
            raw_items.extend(item for item in value if isinstance(item, dict))

    for key in ("home_material", "homepage_material", "home_assignment"):
        value = parsed_result.get(key)
        if isinstance(value, dict):
            raw_items.append(value)

    home_by_offering: dict[int, dict] = {}
    for item in raw_items:
        material_id = _coerce_positive_int(item.get("material_id") or item.get("id"))
        if material_id not in file_id_map:
            continue
        offering_id = _coerce_positive_int(item.get("class_offering_id"))
        target_offering_ids = [offering_id] if offering_id in desired_offering_ids else desired_offering_ids
        for target_offering_id in target_offering_ids:
            home_by_offering.setdefault(
                target_offering_id,
                {
                    "class_offering_id": target_offering_id,
                    "material_id": material_id,
                    "material_path": file_id_map[material_id]["material_path"],
                    "confidence": item.get("confidence", "medium"),
                    "source": "ai",
                },
            )

    if not home_by_offering and fallback_home_row:
        material_id = int(fallback_home_row["id"])
        for offering_id in desired_offering_ids:
            home_by_offering[offering_id] = {
                "class_offering_id": offering_id,
                "material_id": material_id,
                "material_path": fallback_home_row["material_path"],
                "confidence": "medium",
                "source": "heuristic",
            }

    return home_by_offering


def _normalize_archive_name(used_names: set[str], desired_name: str) -> str:
    if desired_name not in used_names:
        used_names.add(desired_name)
        return desired_name

    suffix = 2
    base = desired_name
    extension = ""
    if "." in desired_name and not desired_name.startswith("."):
        base, extension = desired_name.rsplit(".", 1)
        extension = f".{extension}"

    while True:
        next_name = f"{base} ({suffix}){extension}"
        if next_name not in used_names:
            used_names.add(next_name)
            return next_name
        suffix += 1


def _create_material_zip(conn, material_rows: list[dict]) -> str:
    fd, temp_path = tempfile.mkstemp(prefix="course-materials-", suffix=".zip")
    os.close(fd)
    Path(temp_path).unlink(missing_ok=True)
    used_names: set[str] = set()

    with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for material_row in material_rows:
            base_name = _normalize_archive_name(used_names, material_row["name"])
            subtree_rows = _collect_subtree_rows(conn, material_row, include_internal=False)

            if material_row["node_type"] == "folder" and not any(row["node_type"] == "file" for row in subtree_rows):
                archive.writestr(f"{base_name}/", "")
                continue

            for row in subtree_rows:
                row_dict = dict(row)
                if row_dict["node_type"] == "folder":
                    relative_path = PurePosixPath(row_dict["material_path"]).relative_to(material_row["material_path"])
                    folder_path = PurePosixPath(base_name) / relative_path
                    archive.writestr(f"{folder_path.as_posix()}/", "")
                    continue

                file_path = _load_material_storage_path(row_dict)
                relative_path = PurePosixPath(row_dict["material_path"]).relative_to(material_row["material_path"])
                if relative_path.as_posix() == ".":
                    archive_name = base_name
                else:
                    archive_name = str(PurePosixPath(base_name) / relative_path)
                archive.write(file_path, archive_name)

    return temp_path


def _resolve_allowed_scope_rows(conn, material_row, user: dict) -> list[dict]:
    if user["role"] == "teacher":
        rows = conn.execute(
            """
            SELECT id, material_path, name, node_type, preview_type, mime_type
            FROM course_materials
            WHERE root_id = ?
            ORDER BY material_path
            """,
            (material_row["root_id"],),
        ).fetchall()
        return [dict(row) for row in rows if not is_git_internal_material_path(row["material_path"])]

    offering_rows = conn.execute(
        """
        SELECT o.id
        FROM class_offerings o
        JOIN students s ON s.class_id = o.class_id
        WHERE s.id = ?
        """,
        (user["id"],),
    ).fetchall()
    offering_ids = [int(row["id"]) for row in offering_rows]
    if not offering_ids:
        return []

    placeholders = ",".join("?" for _ in offering_ids)
    assignment_rows = conn.execute(
        f"""
        SELECT m.material_path
        FROM course_material_assignments a
        JOIN course_materials m ON m.id = a.material_id
        WHERE a.class_offering_id IN ({placeholders})
          AND m.root_id = ?
        ORDER BY LENGTH(m.material_path) DESC
        """,
        offering_ids + [material_row["root_id"]],
    ).fetchall()
    allowed_paths = [row["material_path"] for row in assignment_rows]
    if not allowed_paths:
        return []

    rows = conn.execute(
        """
        SELECT id, material_path, name, node_type, preview_type, mime_type
        FROM course_materials
        WHERE root_id = ?
        ORDER BY material_path
        """,
        (material_row["root_id"],),
    ).fetchall()

    result = []
    for row in rows:
        row_dict = dict(row)
        if is_git_internal_material_path(row_dict["material_path"]):
            continue
        if any(is_descendant_path(row_dict["material_path"], allowed_path) for allowed_path in allowed_paths):
            result.append(row_dict)
    return result


def _slice_breadcrumbs_from_anchor(breadcrumbs: list[dict], anchor_id: int | None) -> list[dict]:
    if anchor_id is None:
        return breadcrumbs
    for index, crumb in enumerate(breadcrumbs):
        if int(crumb["id"]) == int(anchor_id):
            return breadcrumbs[index:]
    return breadcrumbs


@router.get("/manage/materials", response_class=HTMLResponse)
async def manage_materials_page(request: Request, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        offerings = conn.execute(
            """
            SELECT o.id,
                   COALESCE(s.name, o.semester) AS semester,
                   c.name AS class_name,
                   co.name AS course_name
            FROM class_offerings o
            JOIN classes c ON o.class_id = c.id
            JOIN courses co ON o.course_id = co.id
            LEFT JOIN academic_semesters s ON s.id = o.semester_id
            WHERE o.teacher_id = ?
            ORDER BY co.name, c.name
            """,
            (user["id"],),
        ).fetchall()
        stats = _get_teacher_material_stats(conn, user["id"])

    type_registry = []
    seen_labels = set()
    for extension, meta in MATERIAL_TYPE_REGISTRY.items():
        type_key = (meta["type_label"], meta["preview_type"])
        if type_key in seen_labels:
            continue
        seen_labels.add(type_key)
        type_registry.append(
            {
                "extension": extension,
                "type_label": meta["type_label"],
                "preview_type": meta["preview_type"],
                "ai_capability": meta["ai_capability"],
            }
        )

    return templates.TemplateResponse(
        request,
        "manage/materials.html",
        {
            "request": request,
            "user_info": user,
            "page_title": "课程材料",
            "active_page": "materials",
            "embedded_mode": str(request.query_params.get("embed") or "").strip().lower() in {"1", "true", "yes", "on"},
            "offerings": [dict(row) for row in offerings],
            "material_stats": stats,
            "type_registry": type_registry,
        },
    )


@router.get("/api/materials/library", response_class=JSONResponse)
async def get_teacher_material_library(
    parent_id: int | None = Query(default=None),
    keyword: str = Query(default=""),
    sort_by: str = Query(default=MATERIAL_LIBRARY_DEFAULT_SORT_BY),
    sort_order: str = Query(default=MATERIAL_LIBRARY_DEFAULT_SORT_ORDER),
    user: dict = Depends(get_current_teacher),
):
    normalized_keyword = _normalize_material_keyword(keyword)
    normalized_sort_by, normalized_sort_order = _normalize_material_sort(sort_by, sort_order)

    with get_db_connection() as conn:
        current_folder = None
        breadcrumbs = []
        if parent_id is not None:
            current_folder = ensure_teacher_material_owner(conn, parent_id, user["id"])
            if current_folder["node_type"] != "folder":
                raise HTTPException(400, "只能打开文件夹")
            breadcrumbs = get_material_breadcrumbs(conn, parent_id)

        rows = _list_material_rows_for_parent(
            conn,
            user["id"],
            current_folder,
            keyword=normalized_keyword,
            sort_by=normalized_sort_by,
            sort_order=normalized_sort_order,
        )
        items = [_decorate_learning_document_item(item) for item in _serialize_material_items(conn, rows)]
        current_folder_item = None
        if current_folder:
            current_folder_item = attach_git_repository_metadata(conn, [serialize_material_row(current_folder)])[0]
        stats = _get_teacher_material_stats(conn, user["id"])
        overview = _build_teacher_library_overview(
            current_folder,
            normalized_keyword,
            normalized_sort_by,
            normalized_sort_order,
            len(items),
        )

    return {
        "status": "success",
        "current_folder": current_folder_item,
        "breadcrumbs": breadcrumbs,
        "items": items,
        "stats": stats,
        "filters": {
            "keyword": normalized_keyword,
            "sort_by": normalized_sort_by,
            "sort_order": normalized_sort_order,
        },
        "overview": overview,
    }


@router.get("/api/materials/{material_id}", response_class=JSONResponse)
async def get_material_detail(material_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        material = ensure_teacher_material_owner(conn, material_id, user["id"])
        child_count = conn.execute(
            "SELECT COUNT(*) FROM course_materials WHERE parent_id = ? AND name != '.git'",
            (material_id,),
        ).fetchone()[0]
        assignments = conn.execute(
            """
            SELECT a.class_offering_id, a.created_at, c.name AS class_name, co.name AS course_name, o.semester
            FROM course_material_assignments a
            JOIN class_offerings o ON o.id = a.class_offering_id
            JOIN classes c ON c.id = o.class_id
            JOIN courses co ON co.id = o.course_id
            WHERE a.material_id = ?
            ORDER BY co.name, c.name
            """,
            (material_id,),
        ).fetchall()
        detail = serialize_material_row(
            material,
            {
                "child_count": int(child_count),
                "breadcrumbs": get_material_breadcrumbs(conn, material_id),
                "assignments": [dict(row) for row in assignments],
                "ai_parse_result": json.loads(material["ai_parse_result_json"]) if material["ai_parse_result_json"] else None,
                "has_optimized_version": bool(material["ai_optimized_markdown"]),
            },
        )
        detail = attach_git_repository_metadata(conn, [detail])[0]
        if material["node_type"] == "folder":
            detail = attach_learning_document_metadata(conn, [detail])[0]
            detail = _decorate_learning_document_item(detail)
        detail = _decorate_material_download_policy(detail)

    return {"status": "success", "material": detail}


@router.get("/api/materials/{material_id}/repository", response_class=JSONResponse)
async def get_material_repository(material_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        repository = get_material_repository_detail(conn, material_id, user["id"])
    return {"status": "success", "repository": repository}


@router.post("/api/materials/{material_id}/repository/command", response_class=JSONResponse)
async def run_material_repository_command(
    material_id: int,
    payload: MaterialRepositoryCommandRequest,
    user: dict = Depends(get_current_teacher),
):
    return await execute_material_repository_action(
        get_db_connection,
        material_id,
        user,
        payload.action,
        payload.command,
    )


@router.post("/api/materials/{material_id}/repository/credentials", response_class=JSONResponse)
async def save_material_repository_credentials(
    material_id: int,
    payload: MaterialRepositoryCredentialRequest,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        credential = save_material_repository_credential(
            conn,
            material_id,
            user["id"],
            payload.username,
            payload.secret,
            payload.auth_mode,
        )
    return {
        "status": "success",
        "message": "仓库凭据已保存",
        "credential": credential,
    }


@router.post("/api/materials/upload", response_class=JSONResponse)
async def upload_materials(
    files: list[UploadFile] = File(...),
    manifest: str = Form(default=""),
    parent_id: int | None = Form(default=None),
    user: dict = Depends(get_current_teacher),
):
    if not files:
        raise HTTPException(400, "请选择要上传的材料")

    try:
        manifest_items = json.loads(manifest) if manifest else []
    except json.JSONDecodeError:
        raise HTTPException(400, "上传清单格式错误")

    if manifest_items and len(manifest_items) != len(files):
        raise HTTPException(400, "上传文件与清单数量不匹配")

    prepared_entries = []
    for index, file in enumerate(files):
        manifest_item = manifest_items[index] if index < len(manifest_items) else {}
        raw_path = manifest_item.get("relative_path") or file.filename
        normalized_path = normalize_material_path(raw_path, fallback_name=file.filename or f"file-{index + 1}")
        prepared_entries.append(
            {
                "file": file,
                "relative_path": normalized_path,
                "content_type": manifest_item.get("content_type") or file.content_type,
            }
        )

    with get_db_connection() as conn:
        base_parent = None
        base_prefix = ""
        base_root_id = None
        if parent_id is not None:
            base_parent = ensure_teacher_material_owner(conn, parent_id, user["id"])
            if base_parent["node_type"] != "folder":
                raise HTTPException(400, "只能上传到文件夹中")
            base_prefix = str(base_parent["material_path"])
            base_root_id = int(base_parent["root_id"])

        top_level_name_map: dict[str, str] = {}
        for entry in prepared_entries:
            top_name = entry["relative_path"].split("/", 1)[0]
            if top_name in top_level_name_map:
                continue
            top_level_name_map[top_name] = make_unique_material_name(conn, user["id"], parent_id, top_name)

        created_paths: dict[str, int] = {}
        created_roots: dict[str, int] = {}
        top_level_created_ids: list[int] = []
        uploaded_file_count = 0
        uploaded_folder_count = 0
        now = datetime.now().isoformat()

        for entry in prepared_entries:
            file = entry["file"]
            raw_segments = entry["relative_path"].split("/")
            raw_segments[0] = top_level_name_map[raw_segments[0]]
            adjusted_relative_path = "/".join(raw_segments)
            full_path = f"{base_prefix}/{adjusted_relative_path}" if base_prefix else adjusted_relative_path
            full_path = normalize_material_path(full_path)
            full_segments = full_path.split("/")

            for depth in range(1, len(full_segments)):
                folder_path = "/".join(full_segments[:depth])
                if folder_path in created_paths:
                    continue

                folder_name = full_segments[depth - 1]
                parent_path = "/".join(full_segments[:depth - 1]) if depth > 1 else base_prefix
                if parent_path:
                    folder_parent_id = created_paths.get(parent_path, base_parent["id"] if base_parent and parent_path == base_prefix else None)
                else:
                    folder_parent_id = base_parent["id"] if base_parent else None

                inherited_root_id = None
                if folder_parent_id:
                    if parent_path == base_prefix and base_parent:
                        inherited_root_id = base_root_id
                    else:
                        inherited_root_id = created_roots[parent_path]

                cursor = conn.execute(
                    """
                    INSERT INTO course_materials
                    (teacher_id, parent_id, root_id, material_path, name, node_type, mime_type,
                     preview_type, ai_capability, file_ext, file_hash, file_size,
                     ai_parse_status, ai_optimize_status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 'folder', 'inode/directory', 'folder', 'none', '', NULL, 0, 'idle', 'idle', ?, ?)
                    """,
                    (
                        user["id"],
                        folder_parent_id,
                        inherited_root_id,
                        folder_path,
                        folder_name,
                        now,
                        now,
                    ),
                )
                folder_id = cursor.lastrowid
                actual_root_id = inherited_root_id or folder_id
                if inherited_root_id is None:
                    conn.execute("UPDATE course_materials SET root_id = ? WHERE id = ?", (actual_root_id, folder_id))

                created_paths[folder_path] = folder_id
                created_roots[folder_path] = actual_root_id
                if depth == 1 and parent_path == base_prefix:
                    top_level_created_ids.append(folder_id)
                    uploaded_folder_count += 1

            parent_path = "/".join(full_segments[:-1]) if len(full_segments) > 1 else base_prefix
            if parent_path:
                file_parent_id = created_paths.get(parent_path, base_parent["id"] if base_parent and parent_path == base_prefix else None)
            else:
                file_parent_id = base_parent["id"] if base_parent else None

            inherited_root_id = None
            if file_parent_id:
                if parent_path == base_prefix and base_parent:
                    inherited_root_id = base_root_id
                else:
                    inherited_root_id = created_roots[parent_path]

            file_profile = infer_material_profile(file.filename or full_segments[-1], entry["content_type"])
            file_info = await save_file_globally(file)
            if not file_info:
                raise HTTPException(500, f"保存材料失败: {file.filename}")

            cursor = conn.execute(
                """
                INSERT INTO course_materials
                (teacher_id, parent_id, root_id, material_path, name, node_type, mime_type,
                 preview_type, ai_capability, file_ext, file_hash, file_size,
                 ai_parse_status, ai_optimize_status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'file', ?, ?, ?, ?, ?, ?, 'idle', 'idle', ?, ?)
                """,
                (
                    user["id"],
                    file_parent_id,
                    inherited_root_id,
                    full_path,
                    full_segments[-1],
                    file_profile["mime_type"],
                    file_profile["preview_type"],
                    file_profile["ai_capability"],
                    file_profile["file_ext"],
                    file_info["hash"],
                    file_info["size"],
                    now,
                    now,
                ),
            )
            file_id = cursor.lastrowid
            actual_root_id = inherited_root_id or file_id
            if inherited_root_id is None:
                conn.execute("UPDATE course_materials SET root_id = ? WHERE id = ?", (actual_root_id, file_id))

            created_paths[full_path] = file_id
            created_roots[full_path] = actual_root_id
            uploaded_file_count += 1
            if parent_path == base_prefix:
                top_level_created_ids.append(file_id)

        affected_root_ids = {int(base_root_id)} if base_root_id else set()
        affected_root_ids.update(int(root_id) for root_id in created_roots.values() if root_id)
        for affected_root_id in sorted(affected_root_ids):
            refresh_root_git_metadata(conn, affected_root_id)

        conn.commit()

        created_items = []
        if top_level_created_ids:
            placeholders = ",".join("?" for _ in top_level_created_ids)
            created_rows = conn.execute(
                f"""
                SELECT m.*,
                       (SELECT COUNT(*) FROM course_materials child WHERE child.parent_id = m.id AND child.name != '.git') AS child_count,
                       0 AS assignment_count
                FROM course_materials m
                WHERE m.id IN ({placeholders})
                ORDER BY CASE WHEN m.node_type = 'folder' THEN 0 ELSE 1 END, m.name COLLATE NOCASE
                """,
                top_level_created_ids,
            ).fetchall()
            created_items = [_decorate_learning_document_item(item) for item in _serialize_material_items(conn, created_rows)]

    return {
        "status": "success",
        "message": f"已导入 {uploaded_file_count} 个文件",
        "uploaded_file_count": uploaded_file_count,
        "uploaded_folder_count": uploaded_folder_count,
        "created_items": created_items,
    }


@router.post("/api/materials/{material_id}/assign", response_class=JSONResponse)
async def assign_material_to_classrooms(
    material_id: int,
    payload: MaterialAssignRequest,
    user: dict = Depends(get_current_teacher),
):
    desired_ids = {int(item) for item in payload.class_offering_ids if item}

    with get_db_connection() as conn:
        material = ensure_teacher_material_owner(conn, material_id, user["id"])
        offering_rows = conn.execute(
            "SELECT id FROM class_offerings WHERE teacher_id = ?",
            (user["id"],),
        ).fetchall()
        allowed_ids = {int(row["id"]) for row in offering_rows}
        invalid_ids = desired_ids - allowed_ids
        if invalid_ids:
            raise HTTPException(403, "包含无权分配的课堂")

        existing_rows = conn.execute(
            """
            SELECT a.class_offering_id
            FROM course_material_assignments a
            JOIN class_offerings o ON o.id = a.class_offering_id
            WHERE a.material_id = ? AND o.teacher_id = ?
            """,
            (material_id, user["id"]),
        ).fetchall()
        existing_ids = {int(row["class_offering_id"]) for row in existing_rows}

        remove_ids = existing_ids - desired_ids
        add_ids = desired_ids - existing_ids
        now = datetime.now().isoformat()

        for class_offering_id in remove_ids:
            conn.execute(
                "DELETE FROM course_material_assignments WHERE material_id = ? AND class_offering_id = ?",
                (material_id, class_offering_id),
            )

        for class_offering_id in add_ids:
            conn.execute(
                """
                INSERT INTO course_material_assignments (material_id, class_offering_id, assigned_by_teacher_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (material_id, class_offering_id, user["id"], now),
            )

        conn.commit()

        assignment_rows = conn.execute(
            """
            SELECT a.class_offering_id, a.created_at, c.name AS class_name, co.name AS course_name, o.semester
            FROM course_material_assignments a
            JOIN class_offerings o ON o.id = a.class_offering_id
            JOIN classes c ON c.id = o.class_id
            JOIN courses co ON co.id = o.course_id
            WHERE a.material_id = ?
            ORDER BY co.name, c.name
            """,
            (material_id,),
        ).fetchall()

    return {
        "status": "success",
        "message": f"《{material['name']}》的课堂分配已更新",
        "assignments": [dict(row) for row in assignment_rows],
        "added_count": len(add_ids),
        "removed_count": len(remove_ids),
    }


@router.post("/api/materials/{material_id}/ai-assign-sessions", response_class=JSONResponse)
async def ai_assign_material_to_sessions(
    material_id: int,
    payload: MaterialAssignRequest,
    user: dict = Depends(get_current_teacher),
):
    """AI 分析文档结构并自动将文档文件绑定到对应课堂的课次（session）上。"""
    desired_ids = [int(item) for item in payload.class_offering_ids if item]
    if not desired_ids:
        raise HTTPException(400, "请至少选择一个课堂")

    with get_db_connection() as conn:
        material = ensure_teacher_material_owner(conn, material_id, user["id"])

        # 获取该材料下的所有文件（含子目录递归）
        subtree_rows = _collect_subtree_rows(conn, material, include_internal=False)
        file_rows = [
            dict(row) for row in subtree_rows
            if row["node_type"] == "file"
            and row["preview_type"] == "markdown"
            and not is_git_internal_material_path(row["material_path"])
        ]
        if not file_rows:
            raise HTTPException(400, "当前材料下没有可分配的 Markdown 文档")
        fallback_home_row = _infer_home_material_row(file_rows, material["material_path"])

        # 收集所有选中课堂的课次信息
        offering_rows = conn.execute(
            "SELECT id FROM class_offerings WHERE teacher_id = ?",
            (user["id"],),
        ).fetchall()
        allowed_ids = {int(row["id"]) for row in offering_rows}
        invalid_ids = set(desired_ids) - allowed_ids
        if invalid_ids:
            raise HTTPException(403, "包含无权分配的课堂")

        all_sessions_by_offering: dict[int, list[dict]] = {}
        for offering_id in desired_ids:
            sessions = conn.execute(
                """
                SELECT s.id, s.order_index, s.title, s.content, s.learning_material_id
                FROM class_offering_sessions s
                WHERE s.class_offering_id = ?
                ORDER BY s.order_index
                """,
                (offering_id,),
            ).fetchall()
            all_sessions_by_offering[offering_id] = [dict(s) for s in sessions]

    # 构建发送给 AI 的内容
    file_list_text = "\n".join(
        f"  - ID={row['id']}, path=\"{row['material_path']}\""
        for row in file_rows
    )

    sessions_context_parts: list[str] = []
    for offering_id in desired_ids:
        sessions = all_sessions_by_offering.get(offering_id, [])
        if not sessions:
            continue
        sessions_text = "\n".join(
            f"    - order_index={s['order_index']}, session_id={s['id']}, title=\"{s['title']}\""
            for s in sessions
        )
        sessions_context_parts.append(
            f"  课堂 ID={offering_id}（共 {len(sessions)} 次课）:\n{sessions_text}"
        )
    sessions_context_text = "\n".join(sessions_context_parts)

    if not sessions_context_text:
        raise HTTPException(400, "所选课堂暂无课次安排，请先配置课堂的课次拆分")

    system_prompt = (
        "你是一名教学材料匹配助手。你的任务是根据文档文件的完整路径和课堂课次的标题、顺序，"
        "将文档文件智能匹配到课程首页或对应的课次上。\n\n"
        "匹配规则：\n"
        "1. 先识别课程首页文档。根目录或课程目录下的 README.md、index.md、home.md、首页.md、目录.md、课程简介.md、overview.md 通常是首页；首页用于目录、课程简介和后续文档跳转，不绑定到第1次课。\n"
        "2. 再匹配课次文档。优先按路径中的序号（如 lesson01、L1、第1课、01 等）与课次的 order_index 对应。\n"
        "3. 如果 README.md、index.md 位于某个 lesson01/L1/第1课 目录内，它才属于该课次；如果位于根目录或课程总目录，它属于首页。\n"
        "4. 文档数量与课次数可能不完全对应，多余的说明文档不强行分配。\n"
        "5. 必须使用文档的完整路径进行识别和匹配。\n"
        "6. 每个课堂最多一个首页文档；每个课次只能匹配一个课次文档。\n\n"
        "输出格式：严格的 JSON 对象，包含 \"assignments\" 数组，每个元素包含：\n"
        "  - class_offering_id: 课堂 ID\n"
        "  - session_id: 课次 ID\n"
        "  - material_id: 文档文件 ID\n"
        "  - material_path: 文档完整路径\n"
        "  - confidence: 匹配置信度（high/medium/low）\n\n"
        "如识别到首页，还必须包含 \"home_assignments\" 数组，每个元素包含：\n"
        "  - class_offering_id: 课堂 ID\n"
        "  - material_id: 首页文档文件 ID\n"
        "  - material_path: 首页文档完整路径\n"
        "  - confidence: 匹配置信度（high/medium/low）\n\n"
        "只输出 JSON，不要输出任何其他解释文字或 Markdown 代码块。"
    )

    user_message = (
        f"请将以下文档文件匹配到对应课堂的课次上。\n\n"
        f"【文档文件列表】\n{file_list_text}\n\n"
        f"【课堂课次列表】\n{sessions_context_text}\n\n"
        f"请返回匹配结果 JSON。"
    )

    response_text = await _call_ai_chat(system_prompt, user_message, capability="thinking")
    parsed_result = _parse_ai_json(response_text)

    assignments_raw = parsed_result.get("assignments", [])
    if not isinstance(assignments_raw, list):
        raise HTTPException(500, "AI 未返回有效的匹配结果，请重试或手动分配")

    # 构建校验映射
    file_id_map = {int(row["id"]): row for row in file_rows}
    home_assignments_by_offering = _collect_ai_home_assignments(
        parsed_result,
        desired_offering_ids=desired_ids,
        file_id_map=file_id_map,
        fallback_home_row=fallback_home_row,
    )
    session_id_map: dict[int, dict] = {}
    for offering_id, sessions in all_sessions_by_offering.items():
        for s in sessions:
            session_id_map[int(s["id"])] = {**s, "class_offering_id": offering_id}

    # 过滤有效匹配并执行绑定
    valid_assignments: list[dict] = []
    valid_home_assignments: list[dict] = []
    bound_material_keys: set[tuple[int, int]] = set()
    bound_session_ids: set[int] = set()
    now = datetime.now().isoformat()

    with get_db_connection() as conn:
        for offering_id, home_item in home_assignments_by_offering.items():
            mat_id = int(home_item.get("material_id") or 0)
            if offering_id not in allowed_ids or mat_id not in file_id_map:
                continue
            conn.execute(
                """
                UPDATE class_offerings
                SET home_learning_material_id = ?
                WHERE id = ? AND teacher_id = ?
                """,
                (mat_id, offering_id, user["id"]),
            )
            sync_classroom_learning_material_assignments(
                conn,
                class_offering_id=offering_id,
                teacher_id=int(user["id"]),
                material_ids=[mat_id],
            )
            bound_material_keys.add((offering_id, mat_id))
            valid_home_assignments.append({
                "target_type": "home",
                "class_offering_id": offering_id,
                "session_id": None,
                "session_title": "目录与简介",
                "order_index": 0,
                "material_id": mat_id,
                "material_path": file_id_map[mat_id]["material_path"],
                "confidence": home_item.get("confidence", "medium"),
                "source": home_item.get("source", "ai"),
            })

        for item in assignments_raw:
            target_type = str(item.get("target_type") or item.get("target") or "").strip().lower()
            if target_type in {"home", "homepage", "index", "intro", "introduction"}:
                continue
            offering_id = int(item.get("class_offering_id") or 0)
            session_id = int(item.get("session_id") or 0)
            mat_id = int(item.get("material_id") or 0)

            if offering_id not in allowed_ids:
                continue
            if session_id not in session_id_map:
                continue
            if mat_id not in file_id_map:
                continue
            session_info = session_id_map[session_id]
            if int(session_info["class_offering_id"]) != offering_id:
                continue
            if session_id in bound_session_ids:
                continue
            if (offering_id, mat_id) in bound_material_keys:
                continue

            bound_session_ids.add(session_id)
            bound_material_keys.add((offering_id, mat_id))


            # 绑定 learning_material_id 到 session
            conn.execute(
                """
                UPDATE class_offering_sessions
                SET learning_material_id = ?,
                    updated_at = ?
                WHERE id = ? AND class_offering_id = ?
                """,
                (mat_id, now, session_id, offering_id),
            )

            # 同步课堂材料访问权限
            sync_classroom_learning_material_assignments(
                conn,
                class_offering_id=offering_id,
                teacher_id=int(user["id"]),
                material_ids=[mat_id],
            )

            valid_assignments.append({
                "target_type": "lesson",
                "class_offering_id": offering_id,
                "session_id": session_id,
                "session_title": session_info.get("title", ""),
                "order_index": session_info.get("order_index", 0),
                "material_id": mat_id,
                "material_path": file_id_map[mat_id]["material_path"],
                "confidence": item.get("confidence", "medium"),
            })

        conn.commit()

    return {
        "status": "success",
        "message": (
            f"AI 已完成匹配，成功绑定 {len(valid_assignments)} 个课次文档"
            + (f"，并识别 {len(valid_home_assignments)} 个首页文档" if valid_home_assignments else "")
        ),
        "total_assignments": len(valid_assignments),
        "total_home_assignments": len(valid_home_assignments),
        "assignments": valid_home_assignments + valid_assignments,
        "lesson_assignments": valid_assignments,
        "home_assignments": valid_home_assignments,
    }


@router.put("/api/classrooms/{class_offering_id}/learning-home-material", response_class=JSONResponse)
async def update_classroom_home_learning_material(
    class_offering_id: int,
    payload: ClassroomHomeLearningMaterialUpdateRequest,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        offering_row = conn.execute(
            """
            SELECT id, teacher_id, home_learning_material_id
            FROM class_offerings
            WHERE id = ? AND teacher_id = ?
            LIMIT 1
            """,
            (class_offering_id, user["id"]),
        ).fetchone()
        if not offering_row:
            raise HTTPException(404, "课堂不存在或无权操作")

        learning_material_id = payload.learning_material_id
        if learning_material_id is not None:
            learning_material_id = int(learning_material_id)
            if learning_material_id <= 0:
                learning_material_id = None
            else:
                ensure_teacher_learning_material_owner(conn, learning_material_id, user["id"])

        conn.execute(
            """
            UPDATE class_offerings
            SET home_learning_material_id = ?
            WHERE id = ? AND teacher_id = ?
            """,
            (learning_material_id, class_offering_id, user["id"]),
        )

        if learning_material_id:
            sync_classroom_learning_material_assignments(
                conn,
                class_offering_id=class_offering_id,
                teacher_id=int(user["id"]),
                material_ids=[learning_material_id],
            )

        home_payload = attach_home_learning_material_briefs(
            conn,
            [{"home_learning_material_id": learning_material_id}],
            teacher_id=int(user["id"]),
            markdown_only=True,
        )[0]
        conn.commit()

    home_material = home_payload.get("home_learning_material")
    return {
        "status": "success",
        "message": "课程首页已更新" if home_material else "课程首页已移除",
        "home_material": home_material,
        "has_home_material": bool(home_material),
        "home_entry": build_timeline_home_entry(home_material, include_placeholder=True),
    }


@router.put("/api/classrooms/{class_offering_id}/sessions/{session_id}/learning-material", response_class=JSONResponse)
async def update_classroom_session_learning_material(
    class_offering_id: int,
    session_id: int,
    payload: ClassroomLearningMaterialUpdateRequest,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        session_row = conn.execute(
            """
            SELECT s.id,
                   s.class_offering_id,
                   s.course_lesson_id,
                   s.order_index,
                   s.title,
                   s.content,
                   s.section_count,
                   s.slot_section_count,
                   s.session_date,
                   s.weekday,
                   s.week_index,
                   s.learning_material_id
            FROM class_offering_sessions s
            JOIN class_offerings o ON o.id = s.class_offering_id
            WHERE s.id = ? AND s.class_offering_id = ? AND o.teacher_id = ?
            LIMIT 1
            """,
            (session_id, class_offering_id, user["id"]),
        ).fetchone()
        if not session_row:
            raise HTTPException(404, "课堂节点不存在或无权操作")

        learning_material_id = payload.learning_material_id
        if learning_material_id is not None:
            learning_material_id = int(learning_material_id)
            if learning_material_id <= 0:
                learning_material_id = None
            else:
                ensure_teacher_learning_material_owner(conn, learning_material_id, user["id"])

        conn.execute(
            """
            UPDATE class_offering_sessions
            SET learning_material_id = ?
            WHERE id = ? AND class_offering_id = ?
            """,
            (learning_material_id, session_id, class_offering_id),
        )

        if learning_material_id:
            sync_classroom_learning_material_assignments(
                conn,
                class_offering_id=class_offering_id,
                teacher_id=int(user["id"]),
                material_ids=[learning_material_id],
            )

        updated_row = conn.execute(
            """
            SELECT id,
                   class_offering_id,
                   course_lesson_id,
                   order_index,
                   title,
                   content,
                   section_count,
                   slot_section_count,
                   session_date,
                   weekday,
                   week_index,
                   learning_material_id
            FROM class_offering_sessions
            WHERE id = ? AND class_offering_id = ?
            LIMIT 1
            """,
            (session_id, class_offering_id),
        ).fetchone()
        session_item = attach_learning_material_briefs(
            conn,
            [dict(updated_row)],
            teacher_id=int(user["id"]),
            markdown_only=True,
        )[0]
        conn.commit()

    return {
        "status": "success",
        "message": "课堂材料已更新",
        "session": session_item,
    }


@router.get("/api/classrooms/{class_offering_id}/sessions/{session_id}/ai-material-task", response_class=JSONResponse)
async def get_classroom_session_ai_material_task(
    class_offering_id: int,
    session_id: int,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        session_item = get_teacher_session_with_material_state(
            conn,
            class_offering_id=class_offering_id,
            session_id=session_id,
            teacher_id=int(user["id"]),
        )
        if not session_item:
            raise HTTPException(404, "Session not found or access denied")
        conn.commit()

    return {
        "status": "success",
        "task": session_item.get("material_generation_task"),
        "session": session_item,
    }


@router.post("/api/classrooms/{class_offering_id}/sessions/{session_id}/ai-material-task", response_class=JSONResponse)
async def create_classroom_session_ai_material_task(
    class_offering_id: int,
    session_id: int,
    mode: str = Form(default="guided"),
    document_type: str = Form(default=""),
    requirement_text: str = Form(default=""),
    guided_document_type: str = Form(default=""),
    guided_requirement_text: str = Form(default=""),
    auto_document_type: str = Form(default=""),
    auto_requirement_text: str = Form(default=""),
    example_files: list[UploadFile] | None = File(default=None),
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        session_item = get_teacher_session_with_material_state(
            conn,
            class_offering_id=class_offering_id,
            session_id=session_id,
            teacher_id=int(user["id"]),
        )
        if not session_item:
            raise HTTPException(404, "Session not found or access denied")

        existing_task = session_item.get("material_generation_task")
        if existing_task and existing_task.get("is_active"):
            conn.commit()
            return {
                "status": "accepted",
                "message": "AI assistant is already generating material for this session.",
                "task": existing_task,
                "session": session_item,
            }

        normalized_mode = str(mode or "guided").strip().lower()
        if normalized_mode not in {"guided", "auto"}:
            normalized_mode = "guided"
        requested_document_type = (
            auto_document_type if normalized_mode == "auto" else guided_document_type
        ) or document_type
        requested_requirement_text = (
            auto_requirement_text if normalized_mode == "auto" else guided_requirement_text
        ) or requirement_text
        normalized_document_type = normalize_document_type(
            requested_document_type,
            session_title=session_item.get("title") or "",
            session_content=session_item.get("content") or "",
        )
        normalized_requirement_text = normalize_requirement_text(requested_requirement_text)
        conn.commit()

    example_documents = await extract_example_documents(
        example_files if normalized_mode == "guided" else None,
    )

    with get_db_connection() as conn:
        task = create_generation_task(
            conn,
            class_offering_id=class_offering_id,
            session_id=session_id,
            teacher_id=int(user["id"]),
            trigger_mode=normalized_mode,
            document_type=normalized_document_type,
            requirement_text=normalized_requirement_text,
            example_documents=example_documents,
        )
        session_item = get_teacher_session_with_material_state(
            conn,
            class_offering_id=class_offering_id,
            session_id=session_id,
            teacher_id=int(user["id"]),
        )
        conn.commit()

    if task and not task.get("already_running"):
        asyncio.create_task(run_generation_task(int(task["id"])))

    return {
        "status": "accepted",
        "message": "AI assistant started generating session material.",
        "task": task,
        "session": session_item,
    }


@router.delete("/api/materials/{material_id}", response_class=JSONResponse)
async def delete_material(material_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        material = ensure_teacher_material_owner(conn, material_id, user["id"])
        subtree_rows = _collect_subtree_rows(conn, material)
        file_hashes = {row["file_hash"] for row in subtree_rows if row["node_type"] == "file" and row["file_hash"]}

        conn.execute("DELETE FROM course_materials WHERE id = ?", (material_id,))
        conn.commit()

        removed_files = 0
        for file_hash in file_hashes:
            if _count_global_file_references(conn, file_hash) <= 0:
                if await delete_file_safely(Path(GLOBAL_FILES_DIR) / file_hash):
                    removed_files += 1

    return {
        "status": "success",
        "message": f"《{material['name']}》已删除",
        "removed_file_count": removed_files,
    }


@router.get("/api/classrooms/{class_offering_id}/materials", response_class=JSONResponse)
async def get_classroom_materials(
    class_offering_id: int,
    parent_id: int | None = Query(default=None),
    user: dict = Depends(get_current_user),
):
    with get_db_connection() as conn:
        ensure_classroom_access(conn, class_offering_id, user)

        if parent_id is None:
            rows = get_effective_assignment_nodes(conn, class_offering_id)
            items = []
            for row in rows:
                child_count = conn.execute(
                    "SELECT COUNT(*) FROM course_materials WHERE parent_id = ? AND name != '.git'",
                    (row["id"],),
                ).fetchone()[0]
                row_dict = dict(row)
                row_dict["child_count"] = int(child_count)
                items.append(_decorate_material_download_policy(serialize_material_row(row_dict)))
            items = attach_git_repository_metadata(conn, items)
            items = [_decorate_learning_document_item(item) for item in attach_learning_document_metadata(conn, items)]
            return {
                "status": "success",
                "current_folder": None,
                "breadcrumbs": [],
                "items": items,
            }

        folder = ensure_user_material_access(conn, parent_id, user)
        if folder["node_type"] != "folder":
            raise HTTPException(400, "只能打开文件夹")

        anchor = get_nearest_assignment_anchor(conn, class_offering_id, folder)
        if not anchor:
            raise HTTPException(403, "当前课堂无权访问该文件夹")

        child_rows = conn.execute(
            """
            SELECT m.*,
                   (SELECT COUNT(*) FROM course_materials child WHERE child.parent_id = m.id AND child.name != '.git') AS child_count,
                   0 AS assignment_count
            FROM course_materials m
            WHERE m.parent_id = ?
            ORDER BY CASE WHEN m.node_type = 'folder' THEN 0 ELSE 1 END, m.name COLLATE NOCASE
            """,
            (parent_id,),
        ).fetchall()

        items = []
        for row in child_rows:
            row_dict = dict(row)
            if is_git_internal_material_path(row_dict["material_path"]):
                continue
            if is_descendant_path(row_dict["material_path"], anchor["material_path"]):
                items.append(_decorate_material_download_policy(serialize_material_row(row_dict)))
        items = attach_git_repository_metadata(conn, items)
        items = [_decorate_learning_document_item(item) for item in attach_learning_document_metadata(conn, items)]

        breadcrumbs = _slice_breadcrumbs_from_anchor(get_material_breadcrumbs(conn, parent_id), anchor["id"])
        return {
            "status": "success",
            "current_folder": _decorate_material_download_policy(
                attach_git_repository_metadata(conn, [serialize_material_row(folder)])[0]
            ),
            "breadcrumbs": breadcrumbs,
            "items": items,
        }


@router.get("/materials/view/{material_id}", response_class=HTMLResponse)
async def material_viewer_page(
    request: Request,
    material_id: int,
    variant: str = Query(default="original"),
    user: dict = Depends(get_current_user),
):
    with get_db_connection() as conn:
        material = ensure_user_material_access(conn, material_id, user)
        allowed_rows = _resolve_allowed_scope_rows(conn, material, user)
        preview_variant = "optimized" if variant == "optimized" and material["ai_optimized_markdown"] else "original"
        can_edit_source = user["role"] == "teacher" and is_editable_material(material)

        preview_payload = serialize_material_row(
            material,
            {
                "download_url": f"/materials/download/{material_id}",
                "raw_url": f"/materials/raw/{material_id}",
                "viewer_url": f"/materials/view/{material_id}",
                "content_url": f"/api/materials/{material_id}/content" if can_edit_source else "",
                "preview_variant": preview_variant,
                "path_index": allowed_rows,
                "is_image": material["preview_type"] == "image",
                "is_markdown": material["preview_type"] == "markdown",
                "is_text": material["preview_type"] in {"markdown", "text"},
                "can_edit_source": can_edit_source,
                "optimized_available": bool(material["ai_optimized_markdown"]),
                "ai_parse_result": json.loads(material["ai_parse_result_json"]) if material["ai_parse_result_json"] else None,
            },
        )
        preview_payload = _decorate_material_download_policy(preview_payload)

    if material["preview_type"] in {"markdown", "text"}:
        preview_payload["content"], preview_payload["content_encoding"] = await _load_material_text_content(
            material,
            prefer_optimized=preview_variant == "optimized",
        )
    else:
        preview_payload["content"] = None
        preview_payload["content_encoding"] = None

    return templates.TemplateResponse(
        request,
        "material_viewer.html",
        {
            "request": request,
            "user_info": user,
            "material": preview_payload,
        },
    )


@router.get("/materials/raw/{material_id}", response_class=FileResponse)
async def get_material_raw(material_id: int, user: dict = Depends(get_current_user)):
    raw_preview_only = False
    with get_db_connection() as conn:
        material = ensure_user_material_access(conn, material_id, user)
    raw_preview_only = material["preview_type"] == "image"
    if material["node_type"] != "file":
        raise HTTPException(400, "文件夹不能直接预览")
    if not raw_preview_only:
        raise HTTPException(400, "仅图片材料支持原始内容访问")
    file_path = _load_material_storage_path(material)
    return FileResponse(file_path, media_type=material["mime_type"] or "application/octet-stream")


@router.get("/materials/download/{material_id}", response_class=FileResponse)
async def download_material(material_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        material = ensure_user_material_access(conn, material_id, user)
    ensure_download_allowed(material["file_size"], resource_label="课堂材料")
    if material["node_type"] != "file":
        raise HTTPException(400, "文件夹请使用批量下载")
    file_path = _load_material_storage_path(material)
    return FileResponse(
        file_path,
        media_type=material["mime_type"] or "application/octet-stream",
        filename=material["name"],
    )


@router.post("/api/materials/download", response_class=FileResponse)
async def batch_download_materials(payload: MaterialBatchDownloadRequest, user: dict = Depends(get_current_user)):
    if not payload.material_ids:
        raise HTTPException(400, "请选择要下载的材料")

    with get_db_connection() as conn:
        unique_ids = []
        seen_ids = set()
        for material_id in payload.material_ids:
            if material_id in seen_ids:
                continue
            seen_ids.add(material_id)
            unique_ids.append(material_id)

        selected_rows = []
        for material_id in unique_ids:
            selected_rows.append(dict(ensure_user_material_access(conn, int(material_id), user)))

        archive_source_size = _estimate_material_archive_size(conn, selected_rows)
        ensure_download_allowed(archive_source_size, resource_label="所选课堂材料压缩包")
        temp_path = _create_material_zip(conn, selected_rows)

    archive_title = f"course-materials-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    return FileResponse(
        temp_path,
        media_type="application/zip",
        filename=archive_title,
        background=BackgroundTask(_cleanup_temp_file, temp_path),
    )


@router.get("/api/materials/{material_id}/content", response_class=JSONResponse)
async def get_material_content(material_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        material = ensure_teacher_material_owner(conn, material_id, user["id"])
        if not is_editable_material(material):
            raise HTTPException(400, "当前仅支持编辑文本类材料")

    content, encoding = await _load_material_text_content(material, prefer_optimized=False)
    return {
        "status": "success",
        "material": {
            "id": material["id"],
            "name": material["name"],
            "preview_type": material["preview_type"],
            "updated_at": material["updated_at"],
        },
        "content": content,
        "encoding": encoding,
    }


@router.put("/api/materials/{material_id}/content", response_class=JSONResponse)
async def update_material_content(
    material_id: int,
    payload: MaterialContentUpdateRequest,
    user: dict = Depends(get_current_teacher),
):
    normalized_encoding = str(payload.encoding or "utf-8").strip().lower()
    if normalized_encoding not in TEXT_CONTENT_ENCODINGS:
        raise HTTPException(400, "当前文本编码暂不支持保存")

    with get_db_connection() as conn:
        material = ensure_teacher_material_owner(conn, material_id, user["id"])
        if not is_editable_material(material):
            raise HTTPException(400, "当前仅支持编辑文本类材料")

        payload_bytes = payload.content.encode(normalized_encoding)
        old_hash = material["file_hash"]
        new_hash = hashlib.sha256(payload_bytes).hexdigest()
        if old_hash == new_hash and int(material["file_size"] or 0) == len(payload_bytes):
            return {
                "status": "success",
                "message": "源码没有变化",
                "unchanged": True,
                "material": {
                    "id": material["id"],
                    "name": material["name"],
                    "updated_at": material["updated_at"],
                },
            }

        await _write_material_file(new_hash, payload_bytes)

        updated_at = datetime.now().isoformat()
        conn.execute(
            """
            UPDATE course_materials
            SET file_hash = ?,
                file_size = ?,
                ai_parse_status = 'idle',
                ai_parse_result_json = NULL,
                ai_optimize_status = 'idle',
                ai_optimized_markdown = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (new_hash, len(payload_bytes), updated_at, material_id),
        )
        conn.commit()

        should_remove_old_file = bool(old_hash and old_hash != new_hash and _count_global_file_references(conn, old_hash) <= 0)

    if should_remove_old_file:
        await delete_file_safely(Path(GLOBAL_FILES_DIR) / old_hash)

    return {
        "status": "success",
        "message": "材料源码已保存",
        "unchanged": False,
        "material": {
            "id": material_id,
            "name": material["name"],
            "updated_at": updated_at,
            "viewer_url": f"/materials/view/{material_id}",
        },
    }


@router.post("/api/materials/{material_id}/ai-parse", response_class=JSONResponse)
async def ai_parse_material(material_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        material = ensure_teacher_material_owner(conn, material_id, user["id"])
        if material["node_type"] != "file" or material["ai_capability"] != "markdown":
            raise HTTPException(400, "当前仅支持对 Markdown 材料执行 AI 解析")
        conn.execute(
            "UPDATE course_materials SET ai_parse_status = 'running', updated_at = ? WHERE id = ?",
            (datetime.now().isoformat(), material_id),
        )
        conn.commit()

    try:
        markdown_content = await _load_material_markdown(material, prefer_optimized=False)
        system_prompt = (
            "你是一名教学材料分析助手。"
            "请严格输出 JSON，不要输出 Markdown 代码块。"
            "JSON 结构必须包含 summary, outline, keywords, teaching_value, cautions 字段。"
            "其中 outline 为数组，元素包含 level 和 title。"
        )
        user_prompt = (
            f"请解析下面这份 Markdown 课程材料《{material['name']}》，输出结构化教学摘要。\n\n"
            f"{markdown_content}"
        )
        response_text = await _call_ai_chat(system_prompt, user_prompt, capability="thinking")
        parsed_result = _parse_ai_json(response_text)

        with get_db_connection() as conn:
            conn.execute(
                """
                UPDATE course_materials
                SET ai_parse_status = 'completed',
                    ai_parse_result_json = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (json.dumps(parsed_result, ensure_ascii=False), datetime.now().isoformat(), material_id),
            )
            conn.commit()

        return {
            "status": "success",
            "message": "AI 解析完成",
            "result": parsed_result,
        }
    except Exception as exc:
        error_message = exc.detail if isinstance(exc, HTTPException) else str(exc)
        with get_db_connection() as conn:
            conn.execute(
                "UPDATE course_materials SET ai_parse_status = 'failed', updated_at = ? WHERE id = ?",
                (datetime.now().isoformat(), material_id),
            )
            conn.commit()
        if isinstance(exc, HTTPException):
            raise exc
        raise HTTPException(500, f"AI 解析失败: {error_message}")


@router.post("/api/materials/{material_id}/ai-optimize", response_class=JSONResponse)
async def ai_optimize_material(material_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        material = ensure_teacher_material_owner(conn, material_id, user["id"])
        if material["node_type"] != "file" or material["ai_capability"] != "markdown":
            raise HTTPException(400, "当前仅支持对 Markdown 材料执行 AI 优化")
        conn.execute(
            "UPDATE course_materials SET ai_optimize_status = 'running', updated_at = ? WHERE id = ?",
            (datetime.now().isoformat(), material_id),
        )
        conn.commit()

    try:
        markdown_content = await _load_material_markdown(material, prefer_optimized=False)
        system_prompt = (
            "你是一名教学材料润色助手。"
            "请保留 Markdown 结构，优化措辞、层次和课堂可读性。"
            "不要省略原始关键信息，不要输出解释，只返回优化后的 Markdown 正文。"
        )
        user_prompt = (
            f"请优化下面这份课程材料《{material['name']}》的 Markdown 内容：\n\n"
            f"{markdown_content}"
        )
        response_text = await _call_ai_chat(system_prompt, user_prompt, capability="thinking")
        optimized_markdown = _strip_code_fence(response_text)
        if not optimized_markdown.strip():
            raise HTTPException(500, "AI 未返回有效的优化内容")

        with get_db_connection() as conn:
            conn.execute(
                """
                UPDATE course_materials
                SET ai_optimize_status = 'completed',
                    ai_optimized_markdown = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (optimized_markdown, datetime.now().isoformat(), material_id),
            )
            conn.commit()

        return {
            "status": "success",
            "message": "AI 优化完成",
            "optimized_markdown": optimized_markdown,
            "viewer_url": f"/materials/view/{material_id}?variant=optimized",
        }
    except Exception as exc:
        error_message = exc.detail if isinstance(exc, HTTPException) else str(exc)
        with get_db_connection() as conn:
            conn.execute(
                "UPDATE course_materials SET ai_optimize_status = 'failed', updated_at = ? WHERE id = ?",
                (datetime.now().isoformat(), material_id),
            )
            conn.commit()
        if isinstance(exc, HTTPException):
            raise exc
        raise HTTPException(500, f"AI 优化失败: {error_message}")
