import asyncio
import json
import hashlib
import os
import re
import tempfile
import zipfile
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

import aiofiles
import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from ..core import ai_client, templates
from ..database import get_db_connection
from ..dependencies import get_current_teacher, get_current_user
from ..services.file_service import delete_global_file, global_file_write_path, resolve_global_file_path, save_file_globally
from ..services.download_policy import apply_download_policy, ensure_download_allowed
from ..services.file_preview_service import TEXT_CONTENT_ENCODINGS
from ..services.material_ai_import_service import (
    MaterialExtraction,
    build_import_readme,
    extract_material_content,
    get_material_ai_import_registry,
    normalize_ai_parse_result,
    parse_material_document,
    resolve_material_ai_import_type,
)
from ..services.material_export_template_service import build_material_export_artifact
from ..services.material_final_document_service import (
    FINAL_MATERIAL_TYPES,
    build_final_material_generation_seed,
    final_material_label,
    normalize_final_material_payload,
)
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
from ..services.message_center_service import is_super_admin_teacher
from ..services.organization_scope_service import load_teacher_org_memberships, load_teacher_org_scope
from ..services.session_material_generation_service import (
    create_generation_task,
    extract_example_documents,
    get_teacher_session_with_material_state,
    normalize_document_type,
    normalize_requirement_text,
    run_generation_task,
)

router = APIRouter()


def _read_material_ai_import_int_env(name: str, default: int, minimum: int = 1) -> int:
    raw_value = os.getenv(name)
    if raw_value in (None, ""):
        return max(minimum, int(default))
    try:
        return max(minimum, int(raw_value))
    except (TypeError, ValueError):
        return max(minimum, int(default))


MATERIAL_AI_IMPORT_STATUS_LABELS = {
    "queued": "排队中",
    "running": "正在解析",
    "completed": "解析完成",
    "failed": "解析失败",
    "ai_failed": "AI 识别失败",
    "quality_failed": "疑似乱码",
    "unsupported": "格式不支持",
}
MATERIAL_AI_IMPORT_ACTIVE_STATUSES = {"queued", "running"}
MATERIAL_AI_IMPORT_FINAL_STATUSES = {"completed", "failed", "ai_failed", "quality_failed", "unsupported"}
MATERIAL_AI_IMPORT_WORKER_COUNT = _read_material_ai_import_int_env("MATERIAL_AI_IMPORT_WORKER_COUNT", 1)
MATERIAL_AI_IMPORT_QUEUE_MAX_PENDING = _read_material_ai_import_int_env("MATERIAL_AI_IMPORT_QUEUE_MAX_PENDING", 30)
MATERIAL_AI_IMPORT_STALE_MINUTES = _read_material_ai_import_int_env("MATERIAL_AI_IMPORT_STALE_MINUTES", 45, minimum=5)
MATERIAL_AI_IMPORT_RECENT_MINUTES = _read_material_ai_import_int_env("MATERIAL_AI_IMPORT_RECENT_MINUTES", 30, minimum=5)

_material_ai_import_queue: asyncio.Queue[int] | None = None
_material_ai_import_worker_tasks: list[asyncio.Task] = []
_material_ai_import_enqueued_ids: set[int] = set()


class MaterialAssignRequest(BaseModel):
    class_offering_ids: list[int] = []
    candidate_material_ids: list[int] = []


class MaterialBatchDownloadRequest(BaseModel):
    material_ids: list[int]


class MaterialContentUpdateRequest(BaseModel):
    content: str = ""
    encoding: str | None = None


class MaterialScopeUpdateRequest(BaseModel):
    scope_level: str = "private"


class MaterialRepositoryCommandRequest(BaseModel):
    action: str = "update"
    command: str = ""


class MaterialRepositoryCredentialRequest(BaseModel):
    username: str = ""
    secret: str = ""
    auth_mode: str = "password"


class MaterialRepositoryAutoBindRequest(BaseModel):
    candidate_material_ids: list[int] = []
    class_offering_ids: list[int] = []


class ClassroomLearningMaterialUpdateRequest(BaseModel):
    learning_material_id: int | None = None


class ClassroomHomeLearningMaterialUpdateRequest(BaseModel):
    learning_material_id: int | None = None


class MaterialAiImportOptimizeRequest(BaseModel):
    prompt: str = ""
    class_offering_id: int | None = None


class ClassroomFinalMaterialGenerateRequest(BaseModel):
    document_type: str = "exam_paper"
    prompt: str = ""
    parent_id: int | None = None
    assessment_mode: str = ""
    assessment_method: str = ""


class MaterialAiRewriteRequest(BaseModel):
    mode: str = "optimize"
    prompt: str = ""


MATERIAL_LIBRARY_SORT_LABELS = {
    "name": "名称",
    "created_at": "创建时间",
    "updated_at": "更新时间",
}
MATERIAL_LIBRARY_DEFAULT_SORT_BY = "name"
MATERIAL_LIBRARY_DEFAULT_SORT_ORDER = "asc"
MATERIAL_LIBRARY_ALLOWED_SORT_ORDERS = {"asc", "desc"}
README_SNIPPET_LINE_LIMIT = 10
MATERIAL_AI_CONTEXT_MAX_ATTACHMENTS = 10
MATERIAL_AI_CONTEXT_MAX_CHARS = 42000
MATERIAL_AI_CONTEXT_SINGLE_CHARS = 9000
MATERIAL_AI_CONTEXT_UPLOAD_MAX_BYTES = 18 * 1024 * 1024


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
    file_path = resolve_global_file_path(file_hash)
    if not file_path:
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


@lru_cache(maxsize=128)
def _load_cached_text_content(file_path_value: str, mtime_ns: int, file_size: int) -> tuple[str, str]:
    del mtime_ns, file_size
    return _decode_text_bytes(Path(file_path_value).read_bytes())


async def _load_material_text_content(material_row, prefer_optimized: bool = False) -> tuple[str, str]:
    optimized_content = _row_value(material_row, "ai_optimized_markdown")
    if prefer_optimized and optimized_content:
        return optimized_content, "utf-8"

    file_path = _load_material_storage_path(material_row)
    stat = await asyncio.to_thread(file_path.stat)
    return await asyncio.to_thread(
        _load_cached_text_content,
        str(file_path),
        int(stat.st_mtime_ns),
        int(stat.st_size),
    )


async def _write_material_file(file_hash: str, payload_bytes: bytes):
    target_path = global_file_write_path(file_hash)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        return target_path

    async with aiofiles.open(target_path, "wb") as handle:
        await handle.write(payload_bytes)
    return target_path


def _decorate_material_ownership(conn, item: dict, user: dict | None) -> dict:
    if not user or str(user.get("role") or "") != "teacher":
        return item
    teacher_id = int(user.get("id") or 0)
    is_owned = int(item.get("teacher_id") or 0) == teacher_id
    item["is_owned"] = is_owned
    item["can_manage"] = is_owned or is_super_admin_teacher(conn, teacher_id)
    item["scope_level"] = str(item.get("scope_level") or "private")
    item["scope_label"] = {
        "private": "私有",
        "school": "本校可见",
        "department": "本系部可见",
        "classroom": "课堂可见",
        "public": "全网公开",
    }.get(item["scope_level"], "私有")
    return item


def _serialize_material_items(conn, rows, user: dict | None = None) -> list[dict]:
    items = [serialize_material_row(row) for row in rows]
    items = attach_learning_document_metadata(conn, items)
    items = attach_git_repository_metadata(conn, items)
    return [
        _decorate_material_download_policy(_decorate_material_ownership(conn, item, user))
        for item in items
    ]


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


async def _call_ai_chat(
    system_prompt: str,
    new_message: str,
    capability: str = "thinking",
    *,
    response_format: str = "text",
    base64_urls: list[str] | None = None,
    image_inputs: list[dict[str, Any]] | None = None,
    file_texts: list[dict[str, str]] | None = None,
    task_type: str | None = None,
    task_priority: str = "default",
    task_label: str | None = None,
    timeout: float = 180.0,
):
    payload = {
        "system_prompt": system_prompt,
        "messages": [],
        "new_message": new_message,
        "base64_urls": base64_urls or [],
        "image_inputs": image_inputs or [],
        "file_texts": file_texts or [],
        "model_capability": capability,
        "task_type": task_type or ("deep_text_reasoning" if capability == "thinking" else "fast_text_response"),
        "web_search_enabled": False,
        "response_format": response_format,
        "task_priority": task_priority,
        "task_label": task_label or f"materials:{task_type or capability}",
    }
    try:
        response = await ai_client.post("/api/ai/chat", json=payload, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        if response_format == "json":
            if data.get("response_json") is not None:
                return data.get("response_json")
            return _parse_ai_json(str(data.get("response_text") or ""))
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


def _material_visibility_condition(conn, teacher_id: int) -> tuple[str, list[object]]:
    if is_super_admin_teacher(conn, teacher_id):
        return "1 = 1", []
    memberships = load_teacher_org_memberships(conn, int(teacher_id))
    raw_membership_rows = conn.execute(
        """
        SELECT school_code, department
        FROM teacher_organization_memberships
        WHERE teacher_id = ?
          AND COALESCE(is_active, 1) = 1
        UNION ALL
        SELECT school_code, department
        FROM teachers
        WHERE id = ?
        """,
        (int(teacher_id), int(teacher_id)),
    ).fetchall()
    school_codes = sorted(
        {
            str(scope.get("school_code") or "").strip().lower()
            for scope in memberships
            if str(scope.get("school_code") or "").strip()
        }
        | {
            str(row["school_code"] or "").strip().lower()
            for row in raw_membership_rows
            if str(row["school_code"] or "").strip()
        }
    )
    department_pairs = sorted(
        {
            (
                str(scope.get("school_code") or "").strip().lower(),
                str(scope.get("department") or "").strip().lower(),
            )
            for scope in memberships
            if str(scope.get("school_code") or "").strip()
            and str(scope.get("department") or "").strip()
        }
        | {
            (
                str(row["school_code"] or "").strip().lower(),
                str(row["department"] or "").strip().lower(),
            )
            for row in raw_membership_rows
            if str(row["school_code"] or "").strip()
            and str(row["department"] or "").strip()
        }
    )

    conditions = ["m.teacher_id = ?"]
    params: list[object] = [int(teacher_id)]

    if school_codes:
        placeholders = ", ".join("?" for _ in school_codes)
        conditions.append(
            f"""
            (
                m.scope_level = 'school'
                AND lower(TRIM(COALESCE(m.school_code, ''))) IN ({placeholders})
            )
            """
        )
        params.extend(school_codes)

    if department_pairs:
        pair_conditions = []
        for school_code, department in department_pairs:
            pair_conditions.append(
                """
                (
                    lower(TRIM(COALESCE(m.school_code, ''))) = ?
                    AND lower(TRIM(COALESCE(m.department, ''))) = ?
                )
                """
            )
            params.extend([school_code, department])
        conditions.append(
            """
            (
                m.scope_level = 'department'
                AND (
            """
            + " OR ".join(pair_conditions)
            + """
                )
            )
            """
        )

    return "(" + " OR ".join(f"({condition})" for condition in conditions) + ")", params


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

    visible_sql, visible_params = _material_visibility_condition(conn, int(teacher_id))
    conditions = [visible_sql]
    params: list[object] = [*visible_params]

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
        relative_parts = path_parts[len(root_parts):] if path_parts[: len(root_parts)] == root_parts else path_parts
        relative_parent_parts = relative_parts[:-1]
        relative_depth = max(1, len(path_parts) - len(root_parts))
        stem = lower_name.rsplit(".", 1)[0]
        numeric_lesson_dir_match = any(
            re.search(r"^(?:0*\d{1,3})(?:[\s_.-]|$)", part)
            for part in relative_parent_parts
        )
        lesson_path_match = (
            any(marker in lower_path for marker in lesson_markers)
            or numeric_lesson_dir_match
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

    assignments = parsed_result.get("assignments")
    if isinstance(assignments, list):
        for item in assignments:
            if not isinstance(item, dict):
                continue
            target_type = str(item.get("target_type") or item.get("target") or "").strip().lower()
            if target_type in {"home", "homepage", "index", "intro", "introduction"}:
                raw_items.append(item)

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
          AND COALESCE(s.enrollment_status, 'active') = 'active'
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


def _normalize_positive_id_list(values: list[int] | tuple[int, ...] | None) -> list[int]:
    seen: set[int] = set()
    result: list[int] = []
    for value in values or []:
        normalized = _coerce_positive_int(value)
        if normalized <= 0 or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _is_readme_material_row(row: dict) -> bool:
    return (
        str(row.get("node_type") or "") == "file"
        and str(row.get("preview_type") or "") == "markdown"
        and str(row.get("name") or "").strip().lower() == "readme.md"
        and not is_git_internal_material_path(row.get("material_path"))
    )


def _relative_material_path(root_path: str | None, material_path: str | None) -> str:
    root_parts = [part for part in str(root_path or "").replace("\\", "/").split("/") if part and part != "."]
    material_parts = [part for part in str(material_path or "").replace("\\", "/").split("/") if part and part != "."]
    if material_parts[: len(root_parts)] == root_parts:
        return "/".join(material_parts[len(root_parts):]) or "."
    return "/".join(material_parts) or "."


def _build_directory_tree_text(rows: list[dict], root_path: str | None) -> str:
    visible_rows = [row for row in rows if not is_git_internal_material_path(row.get("material_path"))]
    if not visible_rows:
        return "(空目录)"

    lines: list[str] = []
    for row in sorted(
        visible_rows,
        key=lambda item: (str(item.get("material_path") or "").count("/"), str(item.get("material_path") or "")),
    ):
        relative_path = _relative_material_path(root_path, row.get("material_path"))
        if relative_path == ".":
            lines.append(f"- [root] {row.get('name') or root_path or 'materials'}")
            continue
        marker = "dir" if row.get("node_type") == "folder" else "file"
        material_id = _coerce_positive_int(row.get("id"))
        id_text = f" id={material_id}" if material_id > 0 and marker == "file" else ""
        lines.append(f"- [{marker}{id_text}] {relative_path}")
    return "\n".join(lines)


def _load_readme_snippet(row: dict, *, line_limit: int = README_SNIPPET_LINE_LIMIT) -> str:
    if not row.get("file_hash"):
        return ""
    try:
        file_path = _load_material_storage_path(row)
        content, _encoding = _decode_text_bytes(file_path.read_bytes())
    except Exception:
        return ""
    lines = content.splitlines()[: max(1, min(line_limit, README_SNIPPET_LINE_LIMIT))]
    return "\n".join(lines).strip()


def _build_readme_snippets_text(readme_rows: list[dict]) -> str:
    if not readme_rows:
        return "(没有 README.md 文档)"

    blocks: list[str] = []
    for row in sorted(readme_rows, key=lambda item: str(item.get("material_path") or "")):
        snippet = _load_readme_snippet(row)
        blocks.append(
            "\n".join(
                [
                    f"- ID={row.get('id')}, path=\"{row.get('material_path')}\"",
                    "  前 10 行：",
                    "```markdown",
                    snippet or "(空文档或无法读取)",
                    "```",
                ]
            )
        )
    return "\n\n".join(blocks)


def _load_assigned_offering_ids_for_material_scope(conn, material_row: dict, teacher_id: int) -> list[int]:
    rows = conn.execute(
        """
        SELECT DISTINCT a.class_offering_id, m.material_path
        FROM course_material_assignments a
        JOIN course_materials m ON m.id = a.material_id
        JOIN class_offerings o ON o.id = a.class_offering_id
        WHERE m.root_id = ?
          AND o.teacher_id = ?
        ORDER BY a.class_offering_id
        """,
        (material_row["root_id"], teacher_id),
    ).fetchall()
    result: list[int] = []
    seen: set[int] = set()
    for row in rows:
        if is_git_internal_material_path(row["material_path"]):
            continue
        offering_id = int(row["class_offering_id"])
        if offering_id in seen:
            continue
        seen.add(offering_id)
        result.append(offering_id)
    return result


def _load_teacher_offering_map(conn, teacher_id: int, desired_ids: list[int]) -> dict[int, dict]:
    if not desired_ids:
        return {}
    placeholders = ",".join("?" for _ in desired_ids)
    rows = conn.execute(
        f"""
        SELECT o.id, o.semester, c.name AS class_name, co.name AS course_name
        FROM class_offerings o
        JOIN classes c ON c.id = o.class_id
        JOIN courses co ON co.id = o.course_id
        WHERE o.teacher_id = ?
          AND o.id IN ({placeholders})
        ORDER BY co.name, c.name
        """,
        [teacher_id] + desired_ids,
    ).fetchall()
    return {int(row["id"]): dict(row) for row in rows}


def _normalize_ai_confidence(value: Any) -> str:
    normalized = str(value or "medium").strip().lower()
    return normalized if normalized in {"high", "medium", "low"} else "medium"


def _expand_ai_lesson_targets(
    item: dict,
    *,
    desired_ids: list[int],
    session_id_map: dict[int, dict],
    session_by_offering_order: dict[tuple[int, int], dict],
) -> list[tuple[int, int, dict]]:
    session_id = _coerce_positive_int(item.get("session_id"))
    order_index = _coerce_positive_int(
        item.get("order_index")
        or item.get("lesson_order")
        or item.get("lesson_index")
        or item.get("lesson_number")
        or item.get("class_session_order")
    )

    if order_index > 0:
        result: list[tuple[int, int, dict]] = []
        for target_offering_id in desired_ids:
            session_info = session_by_offering_order.get((target_offering_id, order_index))
            if not session_info:
                continue
            result.append((target_offering_id, int(session_info["id"]), session_info))
        return result

    if session_id in session_id_map:
        session_info = session_id_map[session_id]
        return [(int(session_info["class_offering_id"]), session_id, session_info)]

    return []


async def _run_ai_material_session_assignment(
    *,
    material_id: int,
    desired_ids: list[int],
    candidate_material_ids: list[int] | None,
    user: dict,
    auto_discover_classrooms: bool = False,
) -> dict:
    desired_ids = _normalize_positive_id_list(desired_ids)
    candidate_ids = _normalize_positive_id_list(candidate_material_ids)

    with get_db_connection() as conn:
        material = ensure_teacher_material_owner(conn, material_id, user["id"])

        if auto_discover_classrooms and not desired_ids:
            desired_ids = _load_assigned_offering_ids_for_material_scope(conn, dict(material), int(user["id"]))
        if not desired_ids:
            raise HTTPException(400, "请先把仓库或材料分配到至少一个课堂，再执行自动绑定。")

        offering_map = _load_teacher_offering_map(conn, int(user["id"]), desired_ids)
        invalid_ids = set(desired_ids) - set(offering_map)
        if invalid_ids:
            raise HTTPException(403, "包含无权分配的课堂")

        subtree_rows = [dict(row) for row in _collect_subtree_rows(conn, material, include_internal=False)]
        if candidate_ids:
            candidate_set = set(candidate_ids)
            file_rows = [row for row in subtree_rows if int(row.get("id") or 0) in candidate_set and _is_readme_material_row(row)]
        else:
            file_rows = [row for row in subtree_rows if _is_readme_material_row(row)]
        if not file_rows:
            raise HTTPException(400, "没有可自动绑定的 README.md 文档。")

        readme_rows = [row for row in subtree_rows if _is_readme_material_row(row)]
        fallback_home_row = _infer_home_material_row(file_rows, material["material_path"])

        all_sessions_by_offering: dict[int, list[dict]] = {}
        for offering_id in desired_ids:
            sessions = conn.execute(
                """
                SELECT s.id, s.order_index, s.title, s.content, s.session_date, s.learning_material_id
                FROM class_offering_sessions s
                WHERE s.class_offering_id = ?
                ORDER BY s.order_index
                """,
                (offering_id,),
            ).fetchall()
            all_sessions_by_offering[offering_id] = [dict(s) for s in sessions]

    file_list_text = "\n".join(
        f"  - ID={row['id']}, path=\"{row['material_path']}\""
        for row in file_rows
    )
    directory_tree_text = _build_directory_tree_text(subtree_rows, material["material_path"])
    readme_snippets_text = _build_readme_snippets_text(readme_rows)

    sessions_context_parts: list[str] = []
    for offering_id in desired_ids:
        sessions = all_sessions_by_offering.get(offering_id, [])
        if not sessions:
            continue
        offering = offering_map.get(offering_id, {})
        sessions_text = "\n".join(
            (
                f"    - 第 {s['order_index']} 次课 | session_id={s['id']} | "
                f"title=\"{s['title'] or ''}\" | date=\"{s.get('session_date') or ''}\" | "
                f"content=\"{str(s.get('content') or '').replace(chr(10), ' ')[:220]}\""
            )
            for s in sessions
        )
        sessions_context_parts.append(
            "\n".join(
                [
                    f"  课堂 ID={offering_id} | 课程=\"{offering.get('course_name') or ''}\" | 班级=\"{offering.get('class_name') or ''}\" | 共 {len(sessions)} 次课",
                    sessions_text,
                ]
            )
        )
    sessions_context_text = "\n".join(sessions_context_parts)

    if not sessions_context_text:
        raise HTTPException(400, "所选课堂暂无课次安排，请先配置课堂的课次拆分。")

    system_prompt = (
        "你是教学材料自动绑定助手。你的任务是根据完整目录结构、README.md 前 10 行内容、课堂课次顺序与标题，"
        "判断每个候选 README.md 是课程首页还是第几次课的学习文档，并返回严格 JSON。\n\n"
        "绑定规则：\n"
        "1. 根目录或课程总目录下的 README.md 通常是首页，首页用于课程目录、简介、导航，不绑定到第 1 次课。\n"
        "2. lesson01、L01、01、第一课、第1次课等目录内的 README.md 才属于对应课次。\n"
        "3. 若候选 README 属于第 N 次课，请绑定到每个目标课堂的第 N 次课；如果某个课堂没有第 N 次课则跳过。\n"
        "4. 一个课堂最多一个首页文档；一个课次最多一个学习文档。\n"
        "5. 只为【候选 README】中的 material_id 输出绑定，不要绑定非候选文档。\n\n"
        "输出 JSON 对象：\n"
        "{\n"
        "  \"assignments\": [\n"
        "    {\"class_offering_id\": 1, \"order_index\": 2, \"session_id\": 10, \"material_id\": 99, \"material_path\": \"...\", \"confidence\": \"high|medium|low\"}\n"
        "  ],\n"
        "  \"home_assignments\": [\n"
        "    {\"class_offering_id\": 1, \"material_id\": 88, \"material_path\": \"...\", \"confidence\": \"high|medium|low\"}\n"
        "  ]\n"
        "}\n"
        "只输出 JSON，不输出 Markdown。"
    )

    user_message = (
        "请识别并绑定这次 Git 更新发现的 README.md。\n\n"
        f"【候选 README】\n{file_list_text}\n\n"
        f"【完整目录结构】\n{directory_tree_text}\n\n"
        f"【README 前 10 行内容】\n{readme_snippets_text}\n\n"
        f"【目标课堂与课次】\n{sessions_context_text}\n\n"
        "请返回绑定结果 JSON。"
    )

    parsed_result = await _call_ai_chat(system_prompt, user_message, capability="thinking", response_format="json")
    if not isinstance(parsed_result, dict):
        raise HTTPException(500, "AI 未返回有效的 JSON 对象，请稍后重试或手动绑定。")

    assignments_raw = parsed_result.get("assignments", [])
    if not isinstance(assignments_raw, list):
        raise HTTPException(500, "AI 未返回有效的课次匹配结果，请重试或手动绑定。")

    file_id_map = {int(row["id"]): row for row in file_rows}
    home_assignments_by_offering = _collect_ai_home_assignments(
        parsed_result,
        desired_offering_ids=desired_ids,
        file_id_map=file_id_map,
        fallback_home_row=fallback_home_row,
    )
    session_id_map: dict[int, dict] = {}
    session_by_offering_order: dict[tuple[int, int], dict] = {}
    for offering_id, sessions in all_sessions_by_offering.items():
        for session in sessions:
            session_info = {**session, "class_offering_id": offering_id}
            session_id_map[int(session["id"])] = session_info
            session_by_offering_order[(offering_id, int(session["order_index"] or 0))] = session_info

    valid_assignments: list[dict] = []
    valid_home_assignments: list[dict] = []
    skipped_assignments: list[dict] = []
    bound_material_keys: set[tuple[int, int]] = set()
    bound_session_ids: set[int] = set()
    now = datetime.now().isoformat()

    with get_db_connection() as conn:
        for offering_id, home_item in home_assignments_by_offering.items():
            mat_id = int(home_item.get("material_id") or 0)
            if offering_id not in desired_ids or mat_id not in file_id_map:
                skipped_assignments.append({"target_type": "home", "material_id": mat_id, "reason": "目标课堂或文档无效"})
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
                "class_name": offering_map.get(offering_id, {}).get("class_name", ""),
                "course_name": offering_map.get(offering_id, {}).get("course_name", ""),
                "session_id": None,
                "session_title": "目录与简介",
                "order_index": 0,
                "material_id": mat_id,
                "material_path": file_id_map[mat_id]["material_path"],
                "confidence": _normalize_ai_confidence(home_item.get("confidence")),
                "source": home_item.get("source", "ai"),
            })

        for item in assignments_raw:
            if not isinstance(item, dict):
                continue
            target_type = str(item.get("target_type") or item.get("target") or "").strip().lower()
            if target_type in {"home", "homepage", "index", "intro", "introduction"}:
                continue

            mat_id = _coerce_positive_int(item.get("material_id") or item.get("id"))
            if mat_id not in file_id_map:
                skipped_assignments.append({"target_type": "lesson", "material_id": mat_id, "reason": "文档不在候选 README 范围内"})
                continue

            targets = _expand_ai_lesson_targets(
                item,
                desired_ids=desired_ids,
                session_id_map=session_id_map,
                session_by_offering_order=session_by_offering_order,
            )
            if not targets:
                skipped_assignments.append({"target_type": "lesson", "material_id": mat_id, "reason": "未找到对应课次"})
                continue

            for offering_id, session_id, session_info in targets:
                if session_id in bound_session_ids:
                    skipped_assignments.append({"target_type": "lesson", "material_id": mat_id, "reason": "同一课次已有候选文档绑定"})
                    continue
                if (offering_id, mat_id) in bound_material_keys:
                    skipped_assignments.append({"target_type": "lesson", "material_id": mat_id, "reason": "该文档已作为首页绑定"})
                    continue

                bound_session_ids.add(session_id)
                bound_material_keys.add((offering_id, mat_id))

                conn.execute(
                    """
                    UPDATE class_offering_sessions
                    SET learning_material_id = ?,
                        updated_at = ?
                    WHERE id = ? AND class_offering_id = ?
                    """,
                    (mat_id, now, session_id, offering_id),
                )

                sync_classroom_learning_material_assignments(
                    conn,
                    class_offering_id=offering_id,
                    teacher_id=int(user["id"]),
                    material_ids=[mat_id],
                )

                valid_assignments.append({
                    "target_type": "lesson",
                    "class_offering_id": offering_id,
                    "class_name": offering_map.get(offering_id, {}).get("class_name", ""),
                    "course_name": offering_map.get(offering_id, {}).get("course_name", ""),
                    "session_id": session_id,
                    "session_title": session_info.get("title", ""),
                    "order_index": session_info.get("order_index", 0),
                    "material_id": mat_id,
                    "material_path": file_id_map[mat_id]["material_path"],
                    "confidence": _normalize_ai_confidence(item.get("confidence")),
                })

        conn.commit()

    total_bound = len(valid_home_assignments) + len(valid_assignments)
    message = (
        f"AI 自动绑定完成：{len(valid_home_assignments)} 个首页文档、{len(valid_assignments)} 个课次文档。"
        if total_bound
        else "AI 没有找到可安全绑定的 README，请查看结果后手动绑定。"
    )
    if skipped_assignments:
        message += f" 已跳过 {len(skipped_assignments)} 项无法匹配的候选。"

    return {
        "status": "success",
        "message": message,
        "target_classroom_count": len(desired_ids),
        "candidate_count": len(file_rows),
        "total_assignments": len(valid_assignments),
        "total_home_assignments": len(valid_home_assignments),
        "assignments": valid_home_assignments + valid_assignments,
        "lesson_assignments": valid_assignments,
        "home_assignments": valid_home_assignments,
        "skipped_assignments": skipped_assignments,
    }


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
        current_teacher_is_super_admin = is_super_admin_teacher(conn, user.get("id"))

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
            "current_teacher_is_super_admin": current_teacher_is_super_admin,
            "offerings": [dict(row) for row in offerings],
            "material_stats": stats,
            "type_registry": type_registry,
            "material_ai_import_registry": get_material_ai_import_registry(),
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
            current_folder = ensure_user_material_access(conn, parent_id, user)
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
        items = [_decorate_learning_document_item(item) for item in _serialize_material_items(conn, rows, user=user)]
        current_folder_item = None
        if current_folder:
            current_folder_item = attach_git_repository_metadata(
                conn,
                [_decorate_material_ownership(conn, serialize_material_row(current_folder), user)],
            )[0]
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


@router.get("/api/materials/ai-generation/candidates", response_class=JSONResponse)
async def list_material_ai_generation_candidates(
    query: str = Query(default=""),
    limit: int = Query(default=30, ge=1, le=80),
    user: dict = Depends(get_current_teacher),
):
    normalized_query = _normalize_material_keyword(query)
    with get_db_connection() as conn:
        rows = _list_material_rows_for_parent(
            conn,
            int(user["id"]),
            None,
            keyword=normalized_query,
            sort_by="updated_at",
            sort_order="desc",
        )[: int(limit)]
        items = [
            _decorate_learning_document_item(item)
            for item in _serialize_material_items(conn, rows, user=user)
        ]
    return {"status": "success", "items": items}


@router.get("/api/materials/ai-generation/assignments", response_class=JSONResponse)
async def list_material_ai_generation_assignment_candidates(
    query: str = Query(default=""),
    limit: int = Query(default=30, ge=1, le=80),
    user: dict = Depends(get_current_teacher),
):
    normalized_query = _normalize_material_keyword(query)
    conditions = ["(o.teacher_id = ? OR co.created_by_teacher_id = ?)"]
    params: list[object] = [int(user["id"]), int(user["id"])]
    if normalized_query:
        keyword_pattern = f"%{normalized_query}%"
        conditions.append(
            """
            (
                a.title LIKE ? COLLATE NOCASE
                OR a.requirements_md LIKE ? COLLATE NOCASE
                OR co.name LIKE ? COLLATE NOCASE
                OR cl.name LIKE ? COLLATE NOCASE
            )
            """
        )
        params.extend([keyword_pattern, keyword_pattern, keyword_pattern, keyword_pattern])
    with get_db_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT a.id, a.title, a.status, a.created_at, a.requirements_md,
                   a.exam_paper_id, a.class_offering_id,
                   co.name AS course_name,
                   cl.name AS class_name,
                   o.semester,
                   ep.questions_json AS exam_questions_json
            FROM assignments a
            JOIN courses co ON co.id = a.course_id
            LEFT JOIN class_offerings o ON o.id = a.class_offering_id
            LEFT JOIN classes cl ON cl.id = o.class_id
            LEFT JOIN exam_papers ep ON ep.id = a.exam_paper_id
            WHERE {' AND '.join(conditions)}
            ORDER BY a.created_at DESC, a.id DESC
            LIMIT ?
            """,
            params + [int(limit)],
        ).fetchall()
    items = []
    for row in rows:
        row_dict = dict(row)
        questions = _iter_exam_question_texts(row_dict.get("exam_questions_json")) if row_dict.get("exam_questions_json") else []
        if not questions:
            questions = _extract_assignment_question_lines(row_dict.get("requirements_md") or "")
        excerpt = "；".join(questions[:2])
        items.append(
            {
                "id": int(row_dict["id"]),
                "title": row_dict.get("title") or "",
                "status": row_dict.get("status") or "",
                "course_name": row_dict.get("course_name") or "",
                "class_name": row_dict.get("class_name") or "",
                "semester": row_dict.get("semester") or "",
                "created_at": row_dict.get("created_at") or "",
                "question_count": len(questions),
                "question_excerpt": excerpt[:220],
            }
        )
    return {"status": "success", "items": items}


@router.post("/api/materials/ai-generate", response_class=JSONResponse)
async def ai_generate_material_from_context(
    prompt: str = Form(default=""),
    document_group: str = Form(default="teaching_material"),
    document_type: str = Form(default="teaching_document"),
    parent_id: int | None = Form(default=None),
    existing_material_ids: str = Form(default="[]"),
    assignment_ids: str = Form(default="[]"),
    new_files: list[UploadFile] | None = File(default=None),
    user: dict = Depends(get_current_teacher),
):
    material_ids = _parse_material_ai_id_list(existing_material_ids)
    selected_assignment_ids = _parse_material_ai_id_list(assignment_ids)
    upload_files = list(new_files or [])
    total_attachments = len(material_ids) + len(selected_assignment_ids) + len(upload_files)
    if total_attachments > MATERIAL_AI_CONTEXT_MAX_ATTACHMENTS:
        raise HTTPException(400, f"关联附件最多支持 {MATERIAL_AI_CONTEXT_MAX_ATTACHMENTS} 份")
    if not prompt.strip() and total_attachments <= 0:
        raise HTTPException(400, "请填写提示语，或至少选择一份关联附件")

    material_context_specs: list[tuple[dict, list[dict]]] = []
    assignment_rows: list[dict] = []
    parent_context: dict[str, Any] | None = None
    with get_db_connection() as conn:
        if parent_id is not None:
            parent = ensure_teacher_material_owner(conn, parent_id, user["id"])
            if parent["node_type"] != "folder":
                raise HTTPException(400, "只能生成到文件夹中")
            parent_context = {
                "id": int(parent["id"]),
                "name": parent["name"],
                "material_path": parent["material_path"],
            }
        for material_id in material_ids:
            material = dict(ensure_user_material_access(conn, material_id, user))
            material_context_specs.append((material, _collect_material_context_rows(conn, material)))

        if selected_assignment_ids:
            placeholders = ",".join("?" for _ in selected_assignment_ids)
            rows = conn.execute(
                f"""
                SELECT a.id, a.title, a.status, a.created_at, a.requirements_md,
                       a.exam_paper_id, a.class_offering_id,
                       co.name AS course_name,
                       cl.name AS class_name,
                       o.semester,
                       ep.questions_json AS exam_questions_json
                FROM assignments a
                JOIN courses co ON co.id = a.course_id
                LEFT JOIN class_offerings o ON o.id = a.class_offering_id
                LEFT JOIN classes cl ON cl.id = o.class_id
                LEFT JOIN exam_papers ep ON ep.id = a.exam_paper_id
                WHERE a.id IN ({placeholders})
                  AND (o.teacher_id = ? OR co.created_by_teacher_id = ?)
                ORDER BY a.created_at DESC, a.id DESC
                """,
                selected_assignment_ids + [int(user["id"]), int(user["id"])],
            ).fetchall()
            found_ids = {int(row["id"]) for row in rows}
            missing_ids = [item_id for item_id in selected_assignment_ids if item_id not in found_ids]
            if missing_ids:
                raise HTTPException(403, "存在无权使用的作业题目")
            assignment_rows = [dict(row) for row in rows]

    attachments: list[dict[str, Any]] = []
    for file in upload_files:
        attachments.append(await _build_uploaded_context_attachment(file))
    for material, context_rows in material_context_specs:
        attachments.append(await _build_material_context_attachment(material, context_rows))
    for assignment_row in assignment_rows:
        attachments.append(_build_assignment_context_attachment(assignment_row))
    attachments = _limit_ai_context_attachments(attachments)

    if str(document_group or "").strip() == "final_material" and str(document_type or "").strip() in FINAL_MATERIAL_TYPES:
        return await _generate_final_material_from_manage_context(
            document_type=str(document_type or "").strip(),
            prompt=prompt,
            parent_id=parent_id,
            parent_context=parent_context,
            attachments=attachments,
            user=user,
        )

    file_texts = [
        {"name": item.get("title") or f"attachment-{index + 1}", "content": item.get("content") or ""}
        for index, item in enumerate(attachments)
        if str(item.get("content") or "").strip()
    ]
    raw_result = await _call_ai_chat(
        _build_ai_material_generation_system_prompt(),
        _build_ai_material_generation_user_prompt(
            prompt=prompt,
            parent_context=parent_context,
            attachments=attachments,
        ),
        capability="thinking",
        response_format="json",
        file_texts=file_texts,
        task_type="material_ai_generate",
        task_label="materials:ai-generate",
        timeout=300.0,
    )
    fallback_title = prompt.strip().splitlines()[0][:42] if prompt.strip() else "AI生成材料"
    parse_result = _build_generic_material_parse_result(
        raw_result=raw_result,
        fallback_title=fallback_title,
        attachments=attachments,
        ai_used=True,
    )
    markdown_content = build_import_readme(
        result=parse_result,
        original_name=f"{parse_result.metadata.get('title') or fallback_title}.md",
    )
    parse_payload_json = json.dumps(_build_material_ai_parse_payload(parse_result), ensure_ascii=False)
    material = await _create_generated_markdown_material(
        user=user,
        parent_id=parent_id,
        title=str(parse_result.metadata.get("title") or fallback_title),
        markdown_content=markdown_content,
        parse_payload_json=parse_payload_json,
        name_prefix="AI生成",
    )
    return {
        "status": "success",
        "message": "AI 已生成材料并保存到材料库",
        "material": material,
        "viewer_url": f"/materials/view/{material['id']}",
        "attachment_count": len(attachments),
    }


@router.get("/api/materials/{material_id}", response_class=JSONResponse)
async def get_material_detail(material_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        material = ensure_user_material_access(conn, material_id, user)
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
        detail = _decorate_material_ownership(conn, detail, user)
        detail = attach_git_repository_metadata(conn, [detail])[0]
        if material["node_type"] == "folder":
            detail = attach_learning_document_metadata(conn, [detail])[0]
            detail = _decorate_learning_document_item(detail)
        detail = _decorate_material_download_policy(detail)
        ai_import_record = _find_material_ai_import_record(conn, material_id, user["id"])
        if ai_import_record:
            task = _serialize_material_ai_import_task(conn, ai_import_record, user)
            detail["ai_import_record"] = {
                "id": task["id"],
                "document_group": task["document_group"],
                "document_type": task["document_type"],
                "document_type_label": task["document_type_label"],
                "parse_status": task["parse_status"],
                "parse_mode": task["parse_mode"],
                "updated_at": task["updated_at"],
                "completed_at": task["completed_at"],
                "export_url": f"/api/materials/ai-import-records/{task['id']}/export?format=docx",
                "export_pdf_url": f"/api/materials/ai-import-records/{task['id']}/export?format=pdf" if task["document_type"] == "exam_paper" else "",
                "preview_url": f"/api/materials/{material_id}/ai-import/preview",
            }
        else:
            detail["ai_import_record"] = None

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


@router.post("/api/materials/{material_id}/repository/auto-bind-readmes", response_class=JSONResponse)
async def auto_bind_repository_readmes(
    material_id: int,
    payload: MaterialRepositoryAutoBindRequest,
    user: dict = Depends(get_current_teacher),
):
    return await _run_ai_material_session_assignment(
        material_id=material_id,
        desired_ids=payload.class_offering_ids,
        candidate_material_ids=payload.candidate_material_ids,
        user=user,
        auto_discover_classrooms=True,
    )


def _normalize_uploaded_filename(filename: str | None, fallback: str = "material") -> str:
    raw_name = str(filename or "").replace("\\", "/").strip()
    name = raw_name.rsplit("/", 1)[-1].strip()
    return name or fallback


def _insert_material_folder_row(
    conn,
    *,
    user: dict,
    name: str,
    material_path: str,
    parent_id: int | None,
    inherited_root_id: int | None,
    owner_scope: dict,
    now: str,
) -> tuple[int, int]:
    cursor = conn.execute(
        """
        INSERT INTO course_materials
        (teacher_id, parent_id, root_id, material_path, name, node_type, mime_type,
         preview_type, ai_capability, file_ext, file_hash, file_size,
         ai_parse_status, ai_optimize_status, owner_role, owner_user_pk, scope_level,
         school_code, school_name, college, department, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'folder', 'inode/directory', 'folder', 'none', '', NULL, 0,
                'idle', 'idle', 'teacher', ?, 'private', ?, ?, ?, ?, ?, ?)
        """,
        (
            user["id"],
            parent_id,
            inherited_root_id,
            material_path,
            name,
            user["id"],
            owner_scope["school_code"],
            owner_scope["school_name"],
            owner_scope["college"],
            owner_scope["department"],
            now,
            now,
        ),
    )
    folder_id = int(cursor.lastrowid)
    actual_root_id = int(inherited_root_id or folder_id)
    if inherited_root_id is None:
        conn.execute("UPDATE course_materials SET root_id = ? WHERE id = ?", (actual_root_id, folder_id))
    return folder_id, actual_root_id


def _insert_material_file_row(
    conn,
    *,
    user: dict,
    name: str,
    material_path: str,
    parent_id: int,
    root_id: int,
    file_profile: dict,
    file_hash: str,
    file_size: int,
    owner_scope: dict,
    now: str,
    ai_parse_status: str = "idle",
    ai_parse_result_json: str | None = None,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO course_materials
        (teacher_id, parent_id, root_id, material_path, name, node_type, mime_type,
         preview_type, ai_capability, file_ext, file_hash, file_size,
         ai_parse_status, ai_parse_result_json, ai_optimize_status, owner_role, owner_user_pk, scope_level,
         school_code, school_name, college, department, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'file', ?, ?, ?, ?, ?, ?, ?, ?, 'idle',
                'teacher', ?, 'private', ?, ?, ?, ?, ?, ?)
        """,
        (
            user["id"],
            parent_id,
            root_id,
            material_path,
            name,
            file_profile["mime_type"],
            file_profile["preview_type"],
            file_profile["ai_capability"],
            file_profile["file_ext"],
            file_hash,
            file_size,
            ai_parse_status,
            ai_parse_result_json,
            user["id"],
            owner_scope["school_code"],
            owner_scope["school_name"],
            owner_scope["college"],
            owner_scope["department"],
            now,
            now,
        ),
    )
    return int(cursor.lastrowid)


def _fetch_material_response_item(conn, material_id: int, user: dict) -> dict | None:
    row = conn.execute(
        """
        SELECT m.*,
               (SELECT COUNT(*) FROM course_materials child WHERE child.parent_id = m.id AND child.name != '.git') AS child_count,
               (SELECT COUNT(*) FROM course_material_assignments a WHERE a.material_id = m.id) AS assignment_count
        FROM course_materials m
        WHERE m.id = ?
        """,
        (material_id,),
    ).fetchone()
    if not row:
        return None
    item = _serialize_material_items(conn, [row], user=user)[0]
    return _decorate_learning_document_item(item)


def _parse_material_ai_id_list(raw_value: Any) -> list[int]:
    if raw_value in (None, ""):
        return []
    value = raw_value
    if isinstance(raw_value, str):
        try:
            value = json.loads(raw_value)
        except json.JSONDecodeError:
            value = [item.strip() for item in raw_value.split(",") if item.strip()]
    if not isinstance(value, list):
        return []
    return _normalize_positive_id_list(value)


def _safe_generated_material_base_name(raw_title: str | None, fallback: str = "AI生成材料") -> str:
    text = str(raw_title or "").strip() or fallback
    text = re.sub(r"[\\/:*?\"<>|]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return (text or fallback)[:80]


def _truncate_ai_context_text(text: str, limit: int = MATERIAL_AI_CONTEXT_SINGLE_CHARS) -> tuple[str, bool]:
    content = str(text or "").strip()
    if len(content) <= limit:
        return content, False
    return content[:limit].rstrip() + "\n\n[内容过长，已截断]", True


def _make_ai_context_attachment(
    *,
    source_type: str,
    title: str,
    content: str,
    metadata: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    truncated_content, truncated = _truncate_ai_context_text(content)
    return {
        "source_type": source_type,
        "title": str(title or source_type).strip()[:160],
        "content": truncated_content,
        "metadata": metadata or {},
        "warnings": warnings or [],
        "truncated": truncated,
    }


def _limit_ai_context_attachments(attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    limited: list[dict[str, Any]] = []
    used_chars = 0
    for attachment in attachments[:MATERIAL_AI_CONTEXT_MAX_ATTACHMENTS]:
        content = str(attachment.get("content") or "")
        remaining = MATERIAL_AI_CONTEXT_MAX_CHARS - used_chars
        if remaining <= 0:
            break
        if len(content) > remaining:
            attachment = {
                **attachment,
                "content": content[:remaining].rstrip() + "\n\n[总上下文过长，已截断]",
                "truncated": True,
            }
            content = str(attachment.get("content") or "")
        used_chars += len(content)
        limited.append(attachment)
    return limited


async def _extract_file_row_for_ai_context(file_row: dict) -> tuple[str, list[str], str]:
    warnings: list[str] = []
    if not file_row.get("file_hash"):
        return "", ["材料文件缺少存储内容。"], "missing"
    try:
        file_path = _load_material_storage_path(file_row)
        extraction = await asyncio.to_thread(extract_material_content, file_path, str(file_row.get("name") or "material"))
        warnings.extend(str(item) for item in extraction.warnings if item)
        return str(extraction.text or "").strip(), warnings, extraction.method or "extract"
    except Exception as exc:
        message = exc.detail if isinstance(exc, HTTPException) else str(exc)
        return "", [f"材料正文抽取失败：{message}"], "failed"


def _collect_material_context_rows(conn, material_row: dict) -> list[dict]:
    if material_row["node_type"] == "file":
        return [dict(material_row)]
    rows = conn.execute(
        """
        SELECT *
        FROM course_materials
        WHERE root_id = ?
          AND (material_path = ? OR material_path LIKE ?)
        ORDER BY material_path
        """,
        (
            int(material_row["root_id"]),
            material_row["material_path"],
            f"{material_row['material_path']}/%",
        ),
    ).fetchall()
    return [dict(row) for row in rows if not is_git_internal_material_path(row["material_path"])]


def _is_good_ai_context_file(row: dict) -> bool:
    if row.get("node_type") != "file":
        return False
    if not row.get("file_hash"):
        return False
    capability = str(row.get("ai_capability") or "").strip().lower()
    preview = str(row.get("preview_type") or "").strip().lower()
    return capability not in {"", "none"} or preview in {"markdown", "text"}


async def _build_material_context_attachment(material_row: dict, context_rows: list[dict]) -> dict[str, Any]:
    if material_row["node_type"] == "file":
        text, warnings, method = await _extract_file_row_for_ai_context(dict(material_row))
        content = text or f"材料名称：{material_row['name']}\n材料路径：{material_row['material_path']}\n（无法抽取正文）"
        return _make_ai_context_attachment(
            source_type="material",
            title=f"站内材料：{material_row['name']}",
            content=content,
            metadata={
                "material_id": int(material_row["id"]),
                "material_path": material_row["material_path"],
                "node_type": "file",
                "extract_method": method,
            },
            warnings=warnings,
        )

    tree_text = _build_directory_tree_text(context_rows, material_row["material_path"])
    file_rows = [row for row in context_rows if _is_good_ai_context_file(row)]
    file_rows.sort(
        key=lambda row: (
            0 if str(row.get("name") or "").strip().lower() == "readme.md" else 1,
            0 if str(row.get("preview_type") or "") in {"markdown", "text"} else 1,
            str(row.get("material_path") or ""),
        )
    )
    parts = [f"目录结构：\n{tree_text}"]
    warnings: list[str] = []
    for row in file_rows[:8]:
        text, row_warnings, method = await _extract_file_row_for_ai_context(row)
        warnings.extend(f"{row.get('material_path')}: {item}" for item in row_warnings if item)
        if not text:
            continue
        snippet, _truncated = _truncate_ai_context_text(text, 3600)
        parts.append(
            "\n".join(
                [
                    f"文件：{row.get('material_path')}",
                    f"抽取方式：{method}",
                    snippet,
                ]
            )
        )
    return _make_ai_context_attachment(
        source_type="material",
        title=f"站内材料目录：{material_row['name']}",
        content="\n\n---\n\n".join(parts),
        metadata={
            "material_id": int(material_row["id"]),
            "material_path": material_row["material_path"],
            "node_type": "folder",
            "file_count": len(file_rows),
        },
        warnings=warnings[:8],
    )


async def _build_uploaded_context_attachment(file: UploadFile) -> dict[str, Any]:
    original_name = _normalize_uploaded_filename(file.filename, fallback="attachment")
    payload_bytes = await file.read()
    if not payload_bytes:
        raise HTTPException(400, f"关联附件《{original_name}》为空")
    if len(payload_bytes) > MATERIAL_AI_CONTEXT_UPLOAD_MAX_BYTES:
        raise HTTPException(413, f"关联附件《{original_name}》超过 18MB 限制")

    suffix = Path(original_name).suffix
    fd, temp_path_value = tempfile.mkstemp(prefix="material-ai-context-", suffix=suffix)
    os.close(fd)
    temp_path = Path(temp_path_value)
    try:
        await asyncio.to_thread(temp_path.write_bytes, payload_bytes)
        extraction = await asyncio.to_thread(extract_material_content, temp_path, original_name)
        content = str(extraction.text or "").strip()
        warnings = [str(item) for item in extraction.warnings if item]
        if not content:
            content = f"附件名称：{original_name}\n（无法抽取正文，请仅作为材料线索参考。）"
        return _make_ai_context_attachment(
            source_type="upload",
            title=f"上传附件：{original_name}",
            content=content,
            metadata={
                "filename": original_name,
                "file_size": len(payload_bytes),
                "content_type": file.content_type or "",
                "extract_method": extraction.method or "extract",
            },
            warnings=warnings,
        )
    finally:
        _cleanup_temp_file(str(temp_path))


def _iter_exam_question_texts(payload: Any) -> list[str]:
    data = _parse_json_object(payload)
    pages = data.get("pages")
    if not isinstance(pages, list):
        pages = [data] if data else []
    questions: list[str] = []
    for page_index, page in enumerate(pages, start=1):
        if not isinstance(page, dict):
            continue
        page_title = str(page.get("title") or page.get("name") or f"第 {page_index} 页").strip()
        raw_questions = page.get("questions")
        if not isinstance(raw_questions, list):
            raw_questions = []
        for question_index, question in enumerate(raw_questions, start=1):
            if not isinstance(question, dict):
                continue
            text = str(
                question.get("text")
                or question.get("question")
                or question.get("title")
                or question.get("stem")
                or ""
            ).strip()
            if not text:
                continue
            score = question.get("score") or question.get("points") or question.get("point")
            prefix = f"{page_title} / 第 {question_index} 题"
            if score not in (None, ""):
                prefix += f"（{score} 分）"
            options = question.get("options")
            option_text = ""
            if isinstance(options, list) and options:
                option_lines = []
                for option in options:
                    if isinstance(option, dict):
                        label = str(option.get("label") or option.get("key") or "").strip()
                        value = str(option.get("text") or option.get("value") or "").strip()
                        option_lines.append(f"{label}. {value}".strip(". "))
                    else:
                        option_lines.append(str(option).strip())
                option_text = "\n选项：" + "；".join(item for item in option_lines if item)
            questions.append(f"{prefix}：{text}{option_text}")
    return questions


def _extract_assignment_question_lines(requirements_md: str) -> list[str]:
    lines = str(requirements_md or "").splitlines()
    results: list[str] = []
    stop_section = False
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        heading = re.sub(r"^#+\s*", "", line).strip()
        if re.search(r"(评分|批改|提交|截止|附件|命名|格式|说明|要求)", heading) and not re.search(r"(题|任务|实验|练习|问题)", heading):
            if results and raw_line.lstrip().startswith("#"):
                stop_section = True
            if stop_section:
                continue
        if re.match(r"^(?:#{1,4}\s*)?(?:第\s*\d+\s*[题问]|题目\s*\d+|任务\s*\d+|实验\s*\d+|问题\s*\d+)", line):
            results.append(re.sub(r"^#+\s*", "", line))
            continue
        if re.match(r"^(?:[-*]\s*)?\d+[\.、)]\s*\S+", line) and len(line) >= 8:
            results.append(re.sub(r"^[-*]\s*", "", line))
            continue
        if re.search(r"[？?]$", line) and len(line) >= 6:
            results.append(re.sub(r"^[-*]\s*", "", line))
    if results:
        return results[:60]
    fallback, _truncated = _truncate_ai_context_text(requirements_md, 7000)
    return [fallback] if fallback else []


def _build_assignment_context_attachment(row: dict) -> dict[str, Any]:
    exam_questions = _iter_exam_question_texts(row.get("exam_questions_json")) if row.get("exam_questions_json") else []
    question_lines = exam_questions or _extract_assignment_question_lines(row.get("requirements_md") or "")
    classroom = " / ".join(
        item
        for item in [
            str(row.get("course_name") or "").strip(),
            str(row.get("class_name") or "").strip(),
        ]
        if item
    )
    content = "\n".join(
        [
            f"作业标题：{row.get('title') or ''}",
            f"关联课堂：{classroom or '未绑定课堂'}",
            "题目：",
            "\n".join(f"{index + 1}. {text}" for index, text in enumerate(question_lines)) or "（未识别到题目正文）",
        ]
    )
    return _make_ai_context_attachment(
        source_type="assignment",
        title=f"已生成作业：{row.get('title') or row.get('id')}",
        content=content,
        metadata={
            "assignment_id": int(row["id"]),
            "course_name": row.get("course_name") or "",
            "class_name": row.get("class_name") or "",
            "status": row.get("status") or "",
            "question_count": len(question_lines),
        },
    )


def _normalize_generated_ai_payload(raw_result: Any, *, fallback_title: str, fallback_content: str = "") -> dict[str, Any]:
    payload = raw_result if isinstance(raw_result, dict) else {}
    payload = dict(payload)
    content = str(
        payload.get("content_markdown")
        or payload.get("markdown")
        or payload.get("content")
        or payload.get("text")
        or fallback_content
        or ""
    ).strip()
    if not content:
        raise HTTPException(422, "AI 未返回可保存的材料正文")
    title = str(payload.get("title") or payload.get("name") or fallback_title or "AI生成材料").strip()
    metadata = _parse_json_object(payload.get("metadata"))
    metadata.setdefault("title", title)
    if payload.get("summary"):
        metadata.setdefault("summary", str(payload.get("summary") or "").strip())
    payload["metadata"] = metadata
    payload["content_markdown"] = content
    payload.setdefault("tables", [])
    payload.setdefault("warnings", [])
    payload.setdefault(
        "export_payload",
        {
            "document_group": "teaching_material",
            "document_type": "teaching_document",
            "document_type_label": "教学文档",
            "template_key": "teaching_document",
            "fields": metadata,
        },
    )
    return payload


def _build_generic_material_parse_result(
    *,
    raw_result: Any,
    fallback_title: str,
    fallback_content: str = "",
    attachments: list[dict[str, Any]] | None = None,
    ai_used: bool = True,
):
    normalized = _normalize_generated_ai_payload(
        raw_result,
        fallback_title=fallback_title,
        fallback_content=fallback_content,
    )
    type_meta = resolve_material_ai_import_type("teaching_material", "teaching_document")
    attachment_text = "\n\n".join(str(item.get("content") or "") for item in attachments or [])
    extraction = MaterialExtraction(
        text=attachment_text or normalized["content_markdown"],
        method="ai_material_context",
        source_kind="mixed_context",
        warnings=[],
        quality={"usable": True, "status": "ok"},
    )
    parse_result = normalize_ai_parse_result(
        normalized,
        original_name=f"{normalized['metadata'].get('title') or fallback_title}.json",
        type_meta=type_meta,
        extraction=extraction,
        extra_warnings=[],
        ai_used=ai_used,
    )
    parse_result.parsed_payload["context_sources"] = [
        {
            "source_type": item.get("source_type"),
            "title": item.get("title"),
            "metadata": item.get("metadata") or {},
            "truncated": bool(item.get("truncated")),
        }
        for item in attachments or []
    ]
    return parse_result


def _build_manage_final_material_context(
    *,
    document_type: str,
    prompt: str,
    parent_context: dict[str, Any] | None,
    attachments: list[dict[str, Any]],
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "course_name": "",
        "class_name": "",
        "teacher_name": "",
        "academic_year": "",
        "semester": "",
        "parent_context": parent_context or {},
        "manage_generation": True,
    }
    for attachment in attachments:
        metadata = _parse_json_object(attachment.get("metadata"))
        for key in ("course_name", "class_name", "semester"):
            if not context.get(key) and metadata.get(key):
                context[key] = metadata.get(key)

    attachment_text = "\n\n".join(
        f"【{item.get('title') or '关联附件'}】\n{item.get('content') or ''}"
        for item in attachments
        if str(item.get("content") or "").strip()
    )
    if document_type == "exam_paper" and _has_assessment_plan_data(attachments, attachment_text):
        plan_payload = normalize_final_material_payload(
            document_type="assessment_plan",
            metadata={},
            content_markdown=attachment_text,
            tables=[],
            export_payload={},
        )
        structured = _parse_json_object(plan_payload.get("structured"))
        context["source_assessment_plan"] = {
            "record_id": "",
            "title": "管理中心关联附件中的考核计划表",
            "updated_at": "",
            "fields": _parse_json_object(plan_payload.get("fields")),
            "structured": structured,
            "assessment_items": structured.get("assessment_items") if isinstance(structured.get("assessment_items"), list) else [],
            "content_markdown": attachment_text[:18000],
            "source": "manage_ai_generation_attachments",
        }
    if document_type == "grading_rubric" and _has_concrete_exam_questions(attachments, attachment_text):
        exam_payload = normalize_final_material_payload(
            document_type="exam_paper",
            metadata={},
            content_markdown=attachment_text,
            tables=[],
            export_payload={},
        )
        structured = _parse_json_object(exam_payload.get("structured"))
        context["source_exam_paper"] = {
            "record_id": "",
            "title": "管理中心关联附件中的试卷/题目",
            "updated_at": "",
            "fields": _parse_json_object(exam_payload.get("fields")),
            "structured": structured,
            "paper_sections": structured.get("paper_sections") if isinstance(structured.get("paper_sections"), list) else [],
            "content_markdown": attachment_text[:18000],
            "source": "manage_ai_generation_attachments",
        }
    if attachment_text:
        context["attachment_context"] = attachment_text[:18000]
        if document_type == "exam_paper" and not context.get("source_assessment_plan"):
            context["requires_assessment_plan_confirmation"] = True
            context["generation_warnings"] = ["未在关联附件中识别到考核计划表，生成后需要教师确认分值分布、考试时长、开闭卷和命题信息。"]
    if str(prompt or "").strip():
        context["teacher_prompt"] = str(prompt or "").strip()
    return context


def _has_concrete_exam_questions(attachments: list[dict[str, Any]], attachment_text: str) -> bool:
    for attachment in attachments:
        if str(attachment.get("source_type") or "") == "assignment" and int(_parse_json_object(attachment.get("metadata")).get("question_count") or 0) > 0:
            return True
    text = str(attachment_text or "")
    return bool(
        re.search(
            r"课程考核试卷|paper_sections|"
            r"第\s*[一二三四五六七八九十\d]+\s*[大小]?[题问]|"
            r"[一二三四五六七八九十]+[、.．].{0,48}(?:共\s*\d+(?:\.\d+)?\s*分)|"
            r"题目\s*\d+|任务\s*\d+|截图\s*[A-Za-z0-9_-]*\.(?:png|jpg|jpeg|webp)",
            text,
            re.IGNORECASE,
        )
    )


def _has_assessment_plan_data(attachments: list[dict[str, Any]], attachment_text: str) -> bool:
    text = str(attachment_text or "")
    for attachment in attachments:
        metadata = _parse_json_object(attachment.get("metadata"))
        if str(metadata.get("document_type") or "").strip() in {"考核计划表", "assessment_plan"}:
            return True
    return bool(
        re.search(
            r"课程考核计划表|assessment_items|考核技能/内容|考核技能|考核内容|命题日期|命题教师|考核形式.{0,20}分值",
            text,
            re.IGNORECASE,
        )
    )


async def _generate_final_material_from_manage_context(
    *,
    document_type: str,
    prompt: str,
    parent_id: int | None,
    parent_context: dict[str, Any] | None,
    attachments: list[dict[str, Any]],
    user: dict,
) -> dict[str, Any]:
    type_meta = resolve_material_ai_import_type("final_material", document_type)
    classroom_context = _build_manage_final_material_context(
        document_type=document_type,
        prompt=prompt,
        parent_context=parent_context,
        attachments=attachments,
    )
    if document_type == "grading_rubric" and not classroom_context.get("source_exam_paper"):
        raise HTTPException(409, "生成评分细则前，请先关联具体试卷或题目附件，例如上传课程考核试卷、选择已解析试卷材料，或选择已生成作业题目。")
    if document_type == "exam_paper" and not attachments and not classroom_context.get("source_assessment_plan"):
        raise HTTPException(409, "生成课程考核试卷前，请先关联考核计划表或课程材料附件；试卷需要明确分值分布、考试形式和题目依据。")

    file_texts = [
        {"name": item.get("title") or f"attachment-{index + 1}", "content": item.get("content") or ""}
        for index, item in enumerate(attachments)
        if str(item.get("content") or "").strip()
    ]
    ai_used = True
    try:
        raw_response = await _call_ai_chat(
            _build_final_material_ai_system_prompt(document_type),
            _build_final_material_ai_user_prompt(
                document_type=document_type,
                classroom_context=classroom_context,
                prompt=prompt,
                examples=[],
            ),
            capability="thinking",
            response_format="json",
            file_texts=file_texts,
            task_type="material_final_manage_generate",
            task_label="materials:final-manage-generate",
            timeout=300.0,
        )
        raw_result = raw_response if isinstance(raw_response, dict) else {}
        if not raw_result:
            raise HTTPException(500, "AI 未返回有效 JSON")
    except Exception as exc:
        ai_used = False
        raw_result = build_final_material_generation_seed(
            document_type=document_type,
            classroom_context=classroom_context,
            prompt=prompt,
        )
        warning = exc.detail if isinstance(exc, HTTPException) else str(exc)
        raw_result.setdefault("warnings", [])
        if isinstance(raw_result["warnings"], list):
            raw_result["warnings"].append(f"AI 生成不可用，已使用本地草稿模板：{warning}")
    if isinstance(classroom_context.get("generation_warnings"), list):
        raw_result.setdefault("warnings", [])
        if isinstance(raw_result["warnings"], list):
            raw_result["warnings"].extend(str(item) for item in classroom_context["generation_warnings"] if str(item).strip())

    extraction = MaterialExtraction(
        text=str(raw_result.get("content_markdown") or ""),
        method="ai_manage_generate" if ai_used else "local_generation_seed",
        source_kind="ai_generated" if ai_used else "local_generated",
        warnings=[],
        quality={"usable": True},
    )
    parse_result = normalize_ai_parse_result(
        raw_result,
        original_name=f"{type_meta['label']}-管理中心生成.json",
        type_meta=type_meta,
        extraction=extraction,
        extra_warnings=[],
        ai_used=ai_used,
    )
    parse_result.export_payload = normalize_final_material_payload(
        document_type=document_type,
        metadata=parse_result.metadata,
        content_markdown=parse_result.content_markdown,
        tables=parse_result.tables,
        export_payload=parse_result.export_payload,
        classroom_context=classroom_context,
    )
    parse_result.metadata.update(parse_result.export_payload.get("fields") or {})
    parse_result.parsed_payload["metadata"] = parse_result.metadata
    parse_result.parsed_payload["export_payload"] = parse_result.export_payload
    task = await _create_generated_final_material_library_package(
        parent_id=parent_id,
        parse_result=parse_result,
        user=user,
    )
    material = task.get("package_item") or task.get("parsed_item")
    viewer_item = task.get("parsed_item") or material
    return {
        "status": "success",
        "message": f"{'AI' if ai_used else '本地草稿'}已按模板生成{type_meta['label']}，并保存到材料库。",
        "material": material,
        "task": task,
        "viewer_url": f"/materials/view/{viewer_item['id']}" if viewer_item else "",
        "attachment_count": len(attachments),
        "ai_used": ai_used,
    }


async def _create_generated_markdown_material(
    *,
    user: dict,
    parent_id: int | None,
    title: str,
    markdown_content: str,
    parse_payload_json: str | None,
    name_prefix: str = "AI生成",
) -> dict:
    base_name = _safe_generated_material_base_name(title)
    if not base_name.lower().endswith((".md", ".markdown")):
        desired_name = f"{name_prefix}-{base_name}.md" if name_prefix else f"{base_name}.md"
    else:
        desired_name = f"{name_prefix}-{base_name}" if name_prefix else base_name
    payload_bytes = markdown_content.encode("utf-8")
    file_hash = hashlib.sha256(payload_bytes).hexdigest()
    await _write_material_file(file_hash, payload_bytes)

    with get_db_connection() as conn:
        base_parent = None
        base_prefix = ""
        inherited_root_id = None
        parent_key = None
        if parent_id is not None:
            base_parent = ensure_teacher_material_owner(conn, parent_id, user["id"])
            if base_parent["node_type"] != "folder":
                raise HTTPException(400, "只能生成到文件夹中")
            base_prefix = str(base_parent["material_path"])
            inherited_root_id = int(base_parent["root_id"])
            parent_key = int(base_parent["id"])
        owner_scope = load_teacher_org_scope(conn, int(user["id"]))
        now = datetime.now().isoformat()
        material_name = make_unique_material_name(conn, int(user["id"]), parent_key, desired_name)
        material_path = normalize_material_path(f"{base_prefix}/{material_name}" if base_prefix else material_name)
        file_profile = infer_material_profile(material_name, "text/markdown")
        material_id = _insert_material_file_row(
            conn,
            user=user,
            name=material_name,
            material_path=material_path,
            parent_id=parent_key,
            root_id=inherited_root_id or 0,
            file_profile=file_profile,
            file_hash=file_hash,
            file_size=len(payload_bytes),
            owner_scope=owner_scope,
            now=now,
            ai_parse_status="completed",
            ai_parse_result_json=parse_payload_json,
        )
        actual_root_id = inherited_root_id or material_id
        if inherited_root_id is None:
            conn.execute("UPDATE course_materials SET root_id = ? WHERE id = ?", (actual_root_id, material_id))
        refresh_root_git_metadata(conn, int(actual_root_id))
        conn.commit()
        item = _fetch_material_response_item(conn, material_id, user)
    if not item:
        raise HTTPException(500, "AI 材料已保存，但无法读取详情")
    return item


def _build_ai_material_generation_system_prompt() -> str:
    return (
        "你是一名深度思考型教学材料生成助手。"
        "请严格返回 JSON 对象，不要输出 Markdown 代码块或解释。"
        "JSON 必须包含 title、summary、content_markdown、metadata、outline、keywords、teaching_value、cautions、warnings。"
        "content_markdown 要是可直接保存为课程材料的完整 Markdown 正文，标题、层级、表格、清单要规整。"
        "如果有关联附件，只提炼与教师目标相关的题目、知识点、流程和约束，避免抄入无关内容。"
        "不得编造真实成绩、学生隐私或未提供的事实；不确定处写成待确认字段。"
    )


def _build_ai_material_generation_user_prompt(
    *,
    prompt: str,
    parent_context: dict[str, Any] | None,
    attachments: list[dict[str, Any]],
) -> str:
    source_manifest = [
        {
            "index": index + 1,
            "source_type": item.get("source_type"),
            "title": item.get("title"),
            "metadata": item.get("metadata") or {},
            "truncated": bool(item.get("truncated")),
            "warnings": item.get("warnings") or [],
        }
        for index, item in enumerate(attachments)
    ]
    return "\n\n".join(
        [
            "请生成一份新的课程材料，并让后端可以直接解析保存。",
            f"教师提示语：\n{prompt.strip() or '请根据关联附件生成一份结构清晰、可直接用于教学的课程材料。'}",
            f"目标目录：\n{json.dumps(parent_context or {'name': '材料库根目录', 'material_path': '/'}, ensure_ascii=False, indent=2)}",
            f"关联附件清单：\n{json.dumps(source_manifest, ensure_ascii=False, indent=2)}",
            "输出要求：title 简洁明确；summary 1-3 句；content_markdown 内容完整，适合直接在材料库阅读或继续编辑。",
        ]
    )


def _build_ai_material_rewrite_system_prompt(mode: str) -> str:
    if mode == "regenerate":
        return (
            "你是一名深度思考型教学材料重生成助手。"
            "请严格返回 JSON 对象，不要 Markdown 代码块。"
            "JSON 必须包含 title、summary、content_markdown、metadata、outline、keywords、teaching_value、cautions、warnings。"
            "请基于原材料重新组织内容，回应教师调整提示；可以重写结构，但不得丢失原材料的关键事实。"
        )
    return (
        "你是一名深度思考型教学材料优化助手。"
        "请严格返回 JSON 对象，不要 Markdown 代码块。"
        "JSON 必须包含 title、summary、content_markdown、metadata、outline、keywords、teaching_value、cautions、warnings。"
        "请优化原材料表达、层次、标题和课堂可读性，保留关键事实与操作步骤，修正明显乱码、重复和格式混乱。"
    )


def _build_ai_material_rewrite_user_prompt(
    *,
    mode: str,
    material: dict,
    prompt: str,
    attachment: dict[str, Any],
) -> str:
    return "\n\n".join(
        [
            "请处理下面这份课程材料。",
            f"处理模式：{'重新生成' if mode == 'regenerate' else '优化'}",
            f"材料信息：\n{json.dumps({'id': material.get('id'), 'name': material.get('name'), 'material_path': material.get('material_path'), 'node_type': material.get('node_type'), 'preview_type': material.get('preview_type')}, ensure_ascii=False, indent=2)}",
            f"教师调整提示：\n{prompt.strip() or '无补充要求，请按教学材料质量标准处理。'}",
            f"可用原始内容来源：{attachment.get('title')}",
            "输出 content_markdown 时请给出完整正文，不要只给建议。",
        ]
    )


def _build_material_ai_parse_payload(parse_result) -> dict:
    return dict(parse_result.parsed_payload)


def _parse_json_object(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _parse_json_array(value: Any) -> list:
    if isinstance(value, list):
        return value
    text = str(value or "").strip()
    if not text:
        return []
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        return []
    return loaded if isinstance(loaded, list) else []


def _material_ai_import_status_message(row: dict, *, queue_position: int | None = None) -> str:
    status = str(row.get("parse_status") or "queued").strip().lower()
    source_name = row.get("source_file_name") or "材料文件"
    if status == "queued":
        if queue_position and queue_position > 1:
            return f"《{source_name}》已进入 AI 解析队列，当前约第 {queue_position} 位。"
        return f"《{source_name}》已进入 AI 解析队列，系统会按顺序处理。"
    if status == "running":
        return f"AI 正在解析《{source_name}》，会先校验乱码和结构，再生成可保存的材料内容。"
    if status == "completed":
        return f"《{source_name}》解析完成，已生成材料包和结构化内容。"

    error_message = str(row.get("error_message") or "").strip()
    if error_message:
        return error_message
    if status == "ai_failed":
        return "AI 服务未能返回有效识别结果，请稍后重试或换用更清晰的 PDF/Word 文件。"
    if status == "quality_failed":
        return "解析内容疑似乱码或质量不足，系统已阻止保存无效正文。"
    if status == "unsupported":
        return "当前文档格式暂不支持自动解析，请先转换为 docx、xlsx 或 PDF 后重试。"
    return "解析未完成，请稍后重试。"


def _material_ai_import_queue_position(conn, record_id: int) -> int | None:
    row = conn.execute(
        """
        SELECT COUNT(*) AS queue_position
        FROM material_ai_import_records
        WHERE parse_status = 'queued'
          AND id <= ?
        """,
        (int(record_id),),
    ).fetchone()
    if not row:
        return None
    return int(row["queue_position"] or 0) or None


def _serialize_material_ai_import_task(conn, row, user: dict) -> dict:
    item = dict(row)
    status = str(item.get("parse_status") or "queued").strip().lower()
    record_id = int(item.get("id") or 0)
    queue_position = _material_ai_import_queue_position(conn, record_id) if status == "queued" else None

    package_id = int(item.get("package_material_id") or 0) or None
    source_id = int(item.get("source_material_id") or 0) or None
    parsed_id = int(item.get("parsed_material_id") or 0) or None

    package_item = _fetch_material_response_item(conn, package_id, user) if package_id else None
    source_item = _fetch_material_response_item(conn, source_id, user) if source_id else None
    parsed_item = _fetch_material_response_item(conn, parsed_id, user) if parsed_id else None

    return {
        "id": record_id,
        "teacher_id": int(item.get("teacher_id") or 0),
        "parent_material_id": int(item.get("parent_material_id") or 0) or None,
        "package_material_id": package_id,
        "source_material_id": source_id,
        "parsed_material_id": parsed_id,
        "document_group": item.get("document_group") or "",
        "document_type": item.get("document_type") or "",
        "document_type_label": item.get("document_type_label") or "",
        "parse_status": status,
        "status": status,
        "status_label": MATERIAL_AI_IMPORT_STATUS_LABELS.get(status, "处理中"),
        "is_active": status in MATERIAL_AI_IMPORT_ACTIVE_STATUSES,
        "is_terminal": status in MATERIAL_AI_IMPORT_FINAL_STATUSES,
        "parse_mode": item.get("parse_mode") or "ai",
        "extraction_method": item.get("extraction_method") or "",
        "source_file_name": item.get("source_file_name") or "",
        "source_file_size": int(item.get("source_file_size") or 0),
        "source_mime_type": item.get("source_mime_type") or "",
        "content_quality_status": item.get("content_quality_status") or "unchecked",
        "error_message": item.get("error_message") or "",
        "message": _material_ai_import_status_message(item, queue_position=queue_position),
        "queue_position": queue_position,
        "created_at": item.get("created_at") or "",
        "started_at": item.get("started_at") or "",
        "updated_at": item.get("updated_at") or "",
        "completed_at": item.get("completed_at") or "",
        "failed_at": item.get("failed_at") or "",
        "package_item": package_item,
        "source_item": source_item,
        "parsed_item": parsed_item,
    }


def _ensure_material_ai_import_workers() -> asyncio.Queue[int]:
    global _material_ai_import_queue, _material_ai_import_worker_tasks
    if _material_ai_import_queue is None:
        _material_ai_import_queue = asyncio.Queue(maxsize=MATERIAL_AI_IMPORT_QUEUE_MAX_PENDING)

    live_tasks = [task for task in _material_ai_import_worker_tasks if not task.done()]
    _material_ai_import_worker_tasks = live_tasks
    while len(_material_ai_import_worker_tasks) < MATERIAL_AI_IMPORT_WORKER_COUNT:
        worker_no = len(_material_ai_import_worker_tasks) + 1
        _material_ai_import_worker_tasks.append(asyncio.create_task(_material_ai_import_worker_loop(worker_no)))
    return _material_ai_import_queue


def _enqueue_material_ai_import_task(record_id: int) -> bool:
    record_id = int(record_id)
    if record_id <= 0:
        return False
    if record_id in _material_ai_import_enqueued_ids:
        return True

    queue = _ensure_material_ai_import_workers()
    try:
        queue.put_nowait(record_id)
    except asyncio.QueueFull:
        return False
    _material_ai_import_enqueued_ids.add(record_id)
    return True


def _recover_stale_material_ai_import_tasks(conn) -> int:
    cutoff = (datetime.now() - timedelta(minutes=MATERIAL_AI_IMPORT_STALE_MINUTES)).isoformat()
    now = datetime.now().isoformat()
    cursor = conn.execute(
        """
        UPDATE material_ai_import_records
        SET parse_status = 'queued',
            started_at = NULL,
            error_message = CASE
                WHEN TRIM(COALESCE(error_message, '')) = '' THEN '上次解析进程中断，系统已重新排队。'
                ELSE error_message
            END,
            updated_at = ?
        WHERE parse_status = 'running'
          AND COALESCE(started_at, updated_at, created_at) < ?
        """,
        (now, cutoff),
    )
    return int(cursor.rowcount or 0)


def _classify_material_ai_import_error(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, HTTPException):
        status_code = int(exc.status_code or 500)
        message = str(exc.detail or "").strip() or "解析失败"
    else:
        status_code = 500
        message = str(exc).strip() or "解析失败"

    lowered = message.lower()
    if status_code in {400, 415} and ("不受支持" in message or "格式" in message or "unsupported" in lowered):
        return "unsupported", message
    if "乱码" in message or "质量校验" in message or "质量不足" in message or "quality" in lowered or "garbled" in lowered:
        return "quality_failed", message
    if "可解析内容" in message or "无法从该材料中抽取" in message:
        return "quality_failed", message
    if "AI 未返回" in message or "AI 服务" in message or "AI 助手" in message:
        return "ai_failed", message
    if status_code in {429, 502, 503, 504}:
        return "ai_failed", message
    return "failed", message


def _mark_material_ai_import_failed(record_id: int, status: str, message: str) -> None:
    now = datetime.now().isoformat()
    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE material_ai_import_records
            SET parse_status = ?,
                error_message = ?,
                updated_at = ?,
                completed_at = COALESCE(completed_at, ?),
                failed_at = COALESCE(failed_at, ?)
            WHERE id = ?
            """,
            (status, message[:500], now, now, now, int(record_id)),
        )
        conn.commit()


async def _material_ai_import_worker_loop(worker_no: int) -> None:
    while True:
        queue = _ensure_material_ai_import_workers()
        record_id = await queue.get()
        _material_ai_import_enqueued_ids.discard(int(record_id))
        try:
            await _run_material_ai_import_record(int(record_id))
        except Exception as exc:  # pragma: no cover - worker must not die on one bad record
            status, message = _classify_material_ai_import_error(exc)
            _mark_material_ai_import_failed(int(record_id), status, message)
            print(f"[MATERIAL_AI_IMPORT] worker {worker_no} failed record {record_id}: {message}")
        finally:
            queue.task_done()


async def _run_material_ai_import_record(record_id: int) -> None:
    record_id = int(record_id)
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT * FROM material_ai_import_records WHERE id = ?",
            (record_id,),
        ).fetchone()
        if not row:
            return
        if str(row["parse_status"] or "").lower() not in MATERIAL_AI_IMPORT_ACTIVE_STATUSES:
            return
        now = datetime.now().isoformat()
        conn.execute(
            """
            UPDATE material_ai_import_records
            SET parse_status = 'running',
                started_at = COALESCE(started_at, ?),
                updated_at = ?,
                error_message = ''
            WHERE id = ?
            """,
            (now, now, record_id),
        )
        conn.commit()
        record = dict(row)
        record["parse_status"] = "running"
        record["started_at"] = record.get("started_at") or now
        record["updated_at"] = now

    try:
        file_hash = str(record.get("source_file_hash") or "").strip()
        if not file_hash:
            metadata = _parse_json_object(record.get("metadata_json"))
            file_hash = str(metadata.get("source_file_hash") or metadata.get("file_hash") or "").strip()
        stored_path = resolve_global_file_path(file_hash)
        if not stored_path:
            raise HTTPException(410, "源文件缓存已不存在，无法继续解析，请重新上传。")

        parse_result = await parse_material_document(
            file_path=stored_path,
            original_name=record.get("source_file_name") or stored_path.name,
            document_group=record.get("document_group") or "",
            document_type=record.get("document_type") or "",
            ai_chat=_call_ai_chat,
        )
        await _persist_material_ai_import_success(record_id, record, parse_result)
    except Exception as exc:
        status, message = _classify_material_ai_import_error(exc)
        _mark_material_ai_import_failed(record_id, status, message)
        print(f"[MATERIAL_AI_IMPORT] failed record {record_id}: {message}")


async def _persist_material_ai_import_success(record_id: int, record: dict, parse_result) -> None:
    teacher_id = int(record.get("teacher_id") or 0)
    parent_id = int(record.get("parent_material_id") or 0) or None
    user = {"id": teacher_id, "role": "teacher"}
    original_name = record.get("source_file_name") or "material"
    source_file_hash = str(record.get("source_file_hash") or "").strip()
    source_file_size = int(record.get("source_file_size") or 0)
    source_mime_type = str(record.get("source_mime_type") or "").strip()

    readme_content = build_import_readme(result=parse_result, original_name=original_name)
    readme_bytes = readme_content.encode("utf-8")
    readme_hash = hashlib.sha256(readme_bytes).hexdigest()
    await _write_material_file(readme_hash, readme_bytes)

    source_path = resolve_global_file_path(source_file_hash)
    if source_path and source_file_size <= 0:
        source_file_size = source_path.stat().st_size

    file_profile = infer_material_profile(original_name, source_mime_type or None)
    readme_profile = infer_material_profile("readme.md", "text/markdown")
    parse_payload = _build_material_ai_parse_payload(parse_result)
    parse_payload_json = json.dumps(parse_payload, ensure_ascii=False)
    metadata_json = json.dumps(parse_result.metadata, ensure_ascii=False)
    export_payload_json = json.dumps(parse_result.export_payload, ensure_ascii=False)
    warnings_json = json.dumps(parse_result.warnings, ensure_ascii=False)
    content_quality_json = json.dumps(parse_result.content_quality, ensure_ascii=False)

    with get_db_connection() as conn:
        current = conn.execute(
            "SELECT * FROM material_ai_import_records WHERE id = ?",
            (int(record_id),),
        ).fetchone()
        if not current:
            return
        if str(current["parse_status"] or "").lower() not in MATERIAL_AI_IMPORT_ACTIVE_STATUSES:
            return

        base_parent = None
        base_prefix = ""
        inherited_root_id = None
        if parent_id is not None:
            base_parent = ensure_teacher_material_owner(conn, parent_id, teacher_id)
            if base_parent["node_type"] != "folder":
                raise HTTPException(400, "只能导入到文件夹中")
            base_prefix = str(base_parent["material_path"])
            inherited_root_id = int(base_parent["root_id"])

        owner_scope = load_teacher_org_scope(conn, teacher_id)
        now = datetime.now().isoformat()
        package_base_name = f"AI解析-{Path(original_name).stem or parse_result.document_type_label}"
        package_name = make_unique_material_name(conn, teacher_id, parent_id, package_base_name)
        package_path = normalize_material_path(f"{base_prefix}/{package_name}" if base_prefix else package_name)

        package_id, package_root_id = _insert_material_folder_row(
            conn,
            user=user,
            name=package_name,
            material_path=package_path,
            parent_id=base_parent["id"] if base_parent else None,
            inherited_root_id=inherited_root_id,
            owner_scope=owner_scope,
            now=now,
        )

        source_name = original_name
        if source_name.strip().lower() == "readme.md":
            source_name = "source-readme.md"
        material_source_path = normalize_material_path(f"{package_path}/{source_name}")
        source_id = _insert_material_file_row(
            conn,
            user=user,
            name=source_name,
            material_path=material_source_path,
            parent_id=package_id,
            root_id=package_root_id,
            file_profile=file_profile,
            file_hash=source_file_hash,
            file_size=source_file_size,
            owner_scope=owner_scope,
            now=now,
        )

        parsed_name = "readme.md"
        parsed_path = normalize_material_path(f"{package_path}/{parsed_name}")
        parsed_id = _insert_material_file_row(
            conn,
            user=user,
            name=parsed_name,
            material_path=parsed_path,
            parent_id=package_id,
            root_id=package_root_id,
            file_profile=readme_profile,
            file_hash=readme_hash,
            file_size=len(readme_bytes),
            owner_scope=owner_scope,
            now=now,
            ai_parse_status="completed",
            ai_parse_result_json=parse_payload_json,
        )

        conn.execute(
            """
            UPDATE material_ai_import_records
            SET package_material_id = ?,
                source_material_id = ?,
                parsed_material_id = ?,
                document_group = ?,
                document_type = ?,
                document_type_label = ?,
                parse_status = 'completed',
                parse_mode = ?,
                extraction_method = ?,
                metadata_json = ?,
                content_markdown = ?,
                parsed_payload_json = ?,
                export_payload_json = ?,
                warnings_json = ?,
                content_quality_status = ?,
                content_quality_json = ?,
                error_message = '',
                updated_at = ?,
                completed_at = ?
            WHERE id = ?
            """,
            (
                package_id,
                source_id,
                parsed_id,
                parse_result.document_group,
                parse_result.document_type,
                parse_result.document_type_label,
                "ai" if parse_result.ai_used else "local_fallback",
                parse_result.extraction_method,
                metadata_json,
                parse_result.content_markdown,
                parse_payload_json,
                export_payload_json,
                warnings_json,
                parse_result.content_quality.get("status", "ok"),
                content_quality_json,
                now,
                now,
                int(record_id),
            ),
        )
        refresh_root_git_metadata(conn, package_root_id)
        conn.commit()


def _build_ai_import_payload_from_record(row) -> dict:
    payload = _parse_json_object(row["parsed_payload_json"])
    if payload:
        return payload
    return {
        "metadata": _parse_json_object(row["metadata_json"]),
        "content_markdown": row["content_markdown"] or "",
        "tables": [],
        "warnings": _parse_json_array(row["warnings_json"]),
        "export_payload": _parse_json_object(row["export_payload_json"]),
        "document_group": row["document_group"],
        "document_type": row["document_type"],
        "document_type_label": row["document_type_label"],
        "extraction_method": row["extraction_method"],
    }


def _find_material_ai_import_record(conn, material_id: int, teacher_id: int, *, completed_only: bool = False):
    status_clause = "AND parse_status = 'completed'" if completed_only else ""
    return conn.execute(
        f"""
        SELECT *
        FROM material_ai_import_records
        WHERE teacher_id = ?
          AND (
                parsed_material_id = ?
                OR package_material_id = ?
                OR source_material_id = ?
          )
          {status_clause}
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (int(teacher_id), int(material_id), int(material_id), int(material_id)),
    ).fetchone()


def _build_ai_import_preview(record, *, content_limit: int = 8000) -> dict:
    payload = _build_ai_import_payload_from_record(record)
    export_payload = _parse_json_object(payload.get("export_payload")) or _parse_json_object(record["export_payload_json"])
    fields = _parse_json_object(export_payload.get("fields"))
    structured = _parse_json_object(export_payload.get("structured"))
    content_markdown = str(payload.get("content_markdown") or record["content_markdown"] or "")
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else _parse_json_array(record["warnings_json"])
    return {
        "id": int(record["id"]),
        "document_group": record["document_group"] or "",
        "document_type": record["document_type"] or "",
        "document_type_label": record["document_type_label"] or "",
        "parse_mode": record["parse_mode"] or "",
        "extraction_method": record["extraction_method"] or "",
        "updated_at": record["updated_at"] or "",
        "completed_at": record["completed_at"] or "",
        "metadata": _parse_json_object(payload.get("metadata")) or _parse_json_object(record["metadata_json"]),
        "fields": fields,
        "structured": structured,
        "tables": payload.get("tables") if isinstance(payload.get("tables"), list) else [],
        "warnings": warnings,
        "content_markdown": content_markdown[:content_limit],
        "content_truncated": len(content_markdown) > content_limit,
        "export_url": f"/api/materials/ai-import-records/{int(record['id'])}/export?format=docx",
        "export_pdf_url": f"/api/materials/ai-import-records/{int(record['id'])}/export?format=pdf" if record["document_type"] == "exam_paper" else "",
    }


def _load_final_material_classroom_context(conn, class_offering_id: int, user: dict) -> dict[str, Any]:
    ensure_classroom_access(conn, class_offering_id, user)
    row = conn.execute(
        """
        SELECT o.id AS class_offering_id,
               o.semester,
               o.schedule_info,
               o.teacher_id,
               o.course_id,
               o.class_id,
               o.semester_id,
               o.academic_teaching_class_name,
               co.name AS course_name,
               co.description AS course_description,
               co.sect_name AS course_section,
               co.academic_course_code,
               co.school_code AS course_school_code,
               co.school_name AS course_school_name,
               co.college AS course_college,
               co.department AS course_department,
               cl.name AS class_name,
               cl.school_code AS class_school_code,
               cl.school_name AS class_school_name,
               cl.college AS class_college,
               cl.department AS class_department,
               t.name AS teacher_name,
               t.school_code AS teacher_school_code,
               t.school_name AS teacher_school_name,
               t.college AS teacher_college,
               t.department AS teacher_department
        FROM class_offerings o
        JOIN courses co ON co.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        JOIN teachers t ON t.id = o.teacher_id
        WHERE o.id = ?
        LIMIT 1
        """,
        (int(class_offering_id),),
    ).fetchone()
    if not row:
        raise HTTPException(404, "课堂不存在")
    data = dict(row)
    semester_text = str(data.get("semester") or "")
    academic_year = ""
    semester_label = semester_text
    year_match = re.search(r"(20\d{2})\s*[-—－]\s*(20\d{2})", semester_text)
    if year_match:
        academic_year = f"{year_match.group(1)}-{year_match.group(2)}"
    if re.search(r"(?:^|[-_])1(?:$|[-_])|第一|一", semester_text):
        semester_label = "第一学期"
    elif re.search(r"(?:^|[-_])2(?:$|[-_])|第二|二", semester_text):
        semester_label = "第二学期"
    academic_course = conn.execute(
        """
        SELECT course_nature, exam_method, exam_mode, teaching_class_name, class_composition, synced_at
        FROM teacher_academic_course_sync_items
        WHERE teacher_id = ?
          AND (? IS NULL OR semester_id = ? OR semester_id IS NULL)
          AND (
                course_id = ?
                OR TRIM(course_name) = TRIM(?)
                OR (? != '' AND TRIM(course_code) = TRIM(?))
          )
        ORDER BY
          CASE
            WHEN ? != '' AND TRIM(teaching_class_name) = TRIM(?) THEN 0
            WHEN ? != '' AND teaching_class_name LIKE ? THEN 1
            ELSE 2
          END,
          synced_at DESC,
          id DESC
        LIMIT 1
        """,
        (
            int(data["teacher_id"]),
            data.get("semester_id"),
            data.get("semester_id"),
            int(data["course_id"]),
            data.get("course_name") or "",
            data.get("academic_course_code") or "",
            data.get("academic_course_code") or "",
            data.get("academic_teaching_class_name") or "",
            data.get("academic_teaching_class_name") or "",
            data.get("class_name") or "",
            f"%{data.get('class_name') or ''}%",
        ),
    ).fetchone()
    academic_course_data = dict(academic_course) if academic_course else {}
    return {
        "class_offering_id": int(data["class_offering_id"]),
        "course_id": int(data["course_id"]),
        "class_id": int(data["class_id"]),
        "course_name": data.get("course_name") or "",
        "course_description": data.get("course_description") or "",
        "course_section": data.get("course_section") or "",
        "class_name": data.get("class_name") or "",
        "teacher_name": data.get("teacher_name") or "",
        "academic_year": academic_year,
        "semester": semester_label,
        "raw_semester": semester_text,
        "academic_teaching_class_name": data.get("academic_teaching_class_name") or "",
        "school_code": data.get("course_school_code") or data.get("class_school_code") or data.get("teacher_school_code") or "gxufl",
        "school_name": data.get("course_school_name") or data.get("class_school_name") or data.get("teacher_school_name") or "广西外国语学院",
        "college": data.get("course_college") or data.get("class_college") or data.get("teacher_college") or "",
        "department": data.get("course_department") or data.get("class_department") or data.get("teacher_department") or "",
        "schedule_info": data.get("schedule_info") or "",
        "course_nature": academic_course_data.get("course_nature") or "",
        "academic_exam_method": academic_course_data.get("exam_method") or "",
        "academic_exam_mode": academic_course_data.get("exam_mode") or "",
        "academic_course_synced_at": academic_course_data.get("synced_at") or "",
    }


def _load_latest_final_material_record_for_classroom(
    conn,
    *,
    class_offering_id: int,
    teacher_id: int,
    document_type: str,
):
    return conn.execute(
        """
        SELECT r.*
        FROM material_ai_import_records r
        WHERE r.teacher_id = ?
          AND r.document_group = 'final_material'
          AND r.document_type = ?
          AND r.parse_status = 'completed'
          AND EXISTS (
                SELECT 1
                FROM course_material_assignments a
                WHERE a.class_offering_id = ?
                  AND a.material_id IN (
                        COALESCE(r.package_material_id, -1),
                        COALESCE(r.parsed_material_id, -1),
                        COALESCE(r.source_material_id, -1)
                  )
          )
        ORDER BY r.updated_at DESC, r.id DESC
        LIMIT 1
        """,
        (int(teacher_id), str(document_type or "").strip(), int(class_offering_id)),
    ).fetchone()


def _final_material_record_context(record) -> dict[str, Any]:
    payload = _build_ai_import_payload_from_record(record)
    export_payload = _parse_json_object(payload.get("export_payload")) or _parse_json_object(record["export_payload_json"])
    fields = _parse_json_object(export_payload.get("fields")) or _parse_json_object(payload.get("metadata"))
    structured = _parse_json_object(export_payload.get("structured"))
    paper_sections = structured.get("paper_sections") if isinstance(structured.get("paper_sections"), list) else []
    assessment_items = structured.get("assessment_items") if isinstance(structured.get("assessment_items"), list) else []
    title = (
        fields.get("title")
        or fields.get("course_name")
        or record["document_type_label"]
        or final_material_label(record["document_type"])
    )
    content_markdown = str(payload.get("content_markdown") or record["content_markdown"] or "")
    return {
        "record_id": int(record["id"]),
        "document_type": record["document_type"] or "",
        "document_type_label": record["document_type_label"] or "",
        "title": title,
        "updated_at": record["updated_at"] or "",
        "fields": fields,
        "structured": structured,
        "paper_sections": paper_sections,
        "assessment_items": assessment_items,
        "content_markdown": content_markdown[:18000],
    }


def _load_final_material_examples(conn, *, teacher_id: int, document_type: str, course_name: str, limit: int = 2) -> list[dict[str, str]]:
    rows = conn.execute(
        """
        SELECT document_type_label, content_markdown, export_payload_json, updated_at
        FROM material_ai_import_records
        WHERE teacher_id = ?
          AND document_group = 'final_material'
          AND document_type = ?
          AND parse_status = 'completed'
        ORDER BY
          CASE WHEN content_markdown LIKE ? THEN 0 ELSE 1 END,
          updated_at DESC,
          id DESC
        LIMIT ?
        """,
        (int(teacher_id), document_type, f"%{course_name}%", int(limit)),
    ).fetchall()
    examples: list[dict[str, str]] = []
    for row in rows:
        content = str(row["content_markdown"] or "").strip()
        if len(content) > 2600:
            content = content[:2600] + "\n..."
        examples.append(
            {
                "document_type_label": row["document_type_label"] or final_material_label(document_type),
                "updated_at": row["updated_at"] or "",
                "content_markdown": content,
            }
        )
    return examples


def _build_final_material_ai_system_prompt(document_type: str) -> str:
    label = final_material_label(document_type)
    if str(document_type or "").strip() == "assessment_plan":
        return (
            "你是广西外国语学院课程考核计划表模板填写助手。你的任务不是自由撰写材料，而是只为固定模板补齐字段和考核项目。"
            "必须严格返回 JSON 对象，不要 Markdown 代码块。"
            "JSON 必须包含 metadata、content_markdown、tables、warnings、export_payload。"
            "export_payload.template_key 必须为 assessment_plan，document_group 必须为 final_material，document_type 必须为 assessment_plan。"
            "metadata 和 export_payload.fields 必须包含 school、course_name、class_name、teacher_name、examiner_name、reviewer_name、"
            "academic_year、semester、date、assessment_type、assessment_mode、assessment_mode_label、assessment_method、total_score。"
            "assessment_type 只能是“考查”或“考试”。如果教务或课堂信息显示考查，assessment_mode 必须是 non_written、"
            "assessment_mode_label 必须是“非笔试考核”。如果是考试，优先服从教师补充的笔试/非笔试；教师未说明时生成非笔试草稿，"
            "并在 warnings 中提醒教师确认。assessment_method 写具体形式，例如“机试”“闭卷笔试”“项目实操”。"
            "export_payload.structured.assessment_items 必须是数组，每项包含 assessment_form、content、score，分值合计必须为100。"
            "export_payload.structured.notes 必须原样包含：注：；1．课程名称必须与教学计划上的名称一致。；"
            "2．考核类型：考查、考试（按教学计划填写）。；"
            "3．命题教师：务必输入命题教师名字，打印纸质版后再手写签名；系（教研室）主任审核签字：须手写签名。；"
            "4．各专业根据教学大纲自行拟定考核形式、考核技能/内容、分值。；"
            "5. 该表文字部分均用五号宋体，使用A4纸双面打印。；"
            "6. 命题完成后将该表与评分细则（电子版及纸质版）交到二级学院（部），并装入试卷袋存档。"
            "content_markdown 只写模板字段摘要和考核项目表，不要添加模板之外的新段落。"
        )
    if str(document_type or "").strip() == "grading_rubric":
        return (
            "你是广西外国语学院课程考核评分细则模板填写助手。你的任务不是自由撰写材料，而是根据已存在的课程考核试卷生成固定模板的数据。"
            "必须严格返回 JSON 对象，不要 Markdown 代码块。"
            "JSON 必须包含 metadata、content_markdown、tables、warnings、export_payload。"
            "export_payload.template_key 必须为 grading_rubric，document_group 必须为 final_material，document_type 必须为 grading_rubric。"
            "metadata 和 export_payload.fields 必须包含 school、course_name、class_name、teacher_name、examiner_name、reviewer_name、"
            "academic_year、semester、date、assessment_type、assessment_mode、assessment_mode_label、assessment_method、total_score、"
            "source_exam_paper_record_id、source_exam_paper_title。"
            "评分细则必须依赖课堂上下文里的 source_exam_paper，逐题对应试卷 paper_sections 或试题正文；不得脱离试卷另起评分体系。"
            "export_payload.structured.rubric_items 必须是数组，每项包含 title、score、criteria；criteria 每项包含 score、text。"
            "rubric_items 分值合计必须为100，并尽量与试卷题目/任务的分值一致。"
            "content_markdown 必须包含：通用扣分项与给分原则、按试卷顺序展开的各大题/任务评分标准、例外情况、截图或提交材料要求。"
            "评分标准要能被教师直接用于批改，不能只写泛泛建议；如果试卷没有足够细节，warnings 中说明需要教师补充。"
            "export_payload.structured.notes 必须原样包含：注：；1．课程名称必须与教学计划上的名称一致。；"
            "2．命题教师：务必输入命题教师名字，打印纸质版后再手写签名；系（教研室）主任审核签字：须手写签名。；"
            "3．该表文字部分均用五号宋体，使用A4纸双面打印。；"
            "4．命题完成后将该表与命题计划表（电子版及纸质版）交到二级学院（部），并装入试卷袋存档。"
        )
    if str(document_type or "").strip() == "exam_paper":
        return (
            "你是广西外国语学院课程考核试卷模板填写助手。你的任务不是自由撰写材料，而是根据考核计划表和课程材料生成固定模板的数据。"
            "必须严格返回 JSON 对象，不要 Markdown 代码块。"
            "JSON 必须包含 metadata、content_markdown、tables、warnings、export_payload。"
            "export_payload.template_key 必须为 exam_paper，document_group 必须为 final_material，document_type 必须为 exam_paper。"
            "metadata 和 export_payload.fields 必须包含 school、course_name、class_name、teacher_name、examiner_name、reviewer_name、leader_name、"
            "academic_year、semester、exam_flags、education_level、assessment_type、assessment_mode、assessment_mode_label、assessment_method、"
            "paper_type、exam_duration、total_score、source_assessment_plan_record_id、source_assessment_plan_title。"
            "试卷必须优先继承 source_assessment_plan 中的 assessment_items 分值分布、考核形式和课程字段；不得脱离计划表另起分值体系。"
            "export_payload.structured.paper_sections 必须是数组，每项包含 title、score、content、tasks、screenshot_requirements、submission_requirements、command_blocks。"
            "paper_sections 分值合计必须为100；如计划表有多行考核项目，试题大题应尽量与计划表行一一对应。"
            "content_markdown 只写试卷正文题目，不要写导出模板已经负责的标题、元信息表、成绩表、页脚。"
            "题干要完整、可执行、可评分，明确考生需要做什么、交什么、截图/文件命名/压缩包要求是什么。"
            "如果来源材料不足以生成严谨试题，warnings 中说明教师需要补充的字段或题目依据。"
        )
    return (
        f"你是一名熟悉广西外国语学院期末材料格式的教务文档助手，正在生成《{label}》。"
        "请严格返回 JSON 对象，不要 Markdown 代码块。"
        "JSON 必须包含 metadata、content_markdown、tables、warnings、export_payload。"
        "metadata 和 export_payload.fields 要包含可替换字段：course_name、class_name、teacher_name、examiner_name、"
        "reviewer_name、leader_name、academic_year、semester、assessment_type、assessment_method、date、total_score。"
        "考核计划表必须给出 export_payload.structured.assessment_items；"
        "评分细则必须给出 export_payload.structured.rubric_items 和完整扣分/例外规则；"
        "课程考核试卷必须给出 export_payload.structured.paper_sections，题目、任务、截图/提交要求要完整。"
        "所有分值合计应为 100 分，内容要可直接导出为正式文档。"
    )


def _build_final_material_ai_user_prompt(
    *,
    document_type: str,
    classroom_context: dict[str, Any],
    prompt: str,
    examples: list[dict[str, str]],
) -> str:
    if str(document_type or "").strip() == "assessment_plan":
        return "\n\n".join(
            [
                "请根据课堂信息生成《广西外国语学院课程考核计划表》的结构化填表数据。",
                "固定模板要求：标题为“广西外国语学院课程考核计划表”；学年学期行由导出模板渲染下划线；基础信息表包含课程名称、专业年级班级、考核类型、命题教师、系主任审核签字、命题日期；考核信息表列为考核形式、考核技能/内容、分值；表后注释必须原样保留。",
                "不要输出自由发挥的长文。只给模板字段、考核项目和必要提醒。",
                f"课堂与教务上下文 JSON：\n{json.dumps(classroom_context, ensure_ascii=False, indent=2)}",
                f"教师补充要求：\n{prompt.strip() or '无'}",
                "历史材料片段仅用于学习考核项目拆分粒度，不可覆盖本课堂字段：\n"
                + (json.dumps(examples, ensure_ascii=False, indent=2) if examples else "暂无历史材料。"),
            ]
        )
    if str(document_type or "").strip() == "grading_rubric":
        return "\n\n".join(
            [
                "请根据课堂信息和已生成的课程考核试卷，生成《广西外国语学院课程考核评分细则》的结构化模板数据。",
                "固定模板要求：标题为“广西外国语学院课程考核评分细则”；学年学期行由导出模板渲染下划线；基础信息表包含课程名称、专业年级班级、考核形式、命题日期、命题教师、系主任审核签字；正文标题为“评分细则”，正文放在单列表格框内；表后注释必须原样保留。",
                "业务要求：评分细则必须先对应具体试卷。请按 source_exam_paper 中的 paper_sections/试题正文逐题给出评分标准、分值、扣分项、例外情况和截图/提交物要求。",
                f"课堂、教务与来源试卷上下文 JSON：\n{json.dumps(classroom_context, ensure_ascii=False, indent=2)}",
                f"教师补充要求：\n{prompt.strip() or '无'}",
                "历史评分细则片段仅用于学习表达粒度，不可覆盖本课堂字段和本次来源试卷：\n"
                + (json.dumps(examples, ensure_ascii=False, indent=2) if examples else "暂无历史材料。"),
            ]
        )
    if str(document_type or "").strip() == "exam_paper":
        return "\n\n".join(
            [
                "请根据课堂信息、考核计划表和可用课程材料，生成《广西外国语学院课程考核试卷》的结构化模板数据。",
                "固定模板要求：标题为“广西外国语学院课程考核试卷”；学年学期行由导出模板渲染下划线；顶部包含期末考试/补考/重新学习考试勾选；学生信息和密封线、元信息表、题号/满分/实得分表、每题得分/评卷人小表、页脚由导出模板渲染。",
                "业务要求：试卷通常先由考核计划表生成。请优先按 source_assessment_plan.structured.assessment_items 拆分大题，继承分值与考核形式；再结合关联课程材料或教师提示补齐具体题干、任务步骤、截图编号、命名规则、压缩包或提交要求。",
                "输出限制：content_markdown 只写试卷正文题目；不要重复写标题、学年学期、元信息表、成绩表、页码。题目必须能直接用于考试，不得只给命题建议。",
                f"课堂、教务与来源考核计划上下文 JSON：\n{json.dumps(classroom_context, ensure_ascii=False, indent=2)}",
                f"教师补充要求：\n{prompt.strip() or '无'}",
                "历史试卷片段仅用于学习题干粒度和版式，不可覆盖本课堂字段和本次考核计划：\n"
                + (json.dumps(examples, ensure_ascii=False, indent=2) if examples else "暂无历史材料。"),
            ]
        )
    return "\n\n".join(
        [
            "请根据课堂信息生成期末材料。",
            f"材料类型：{final_material_label(document_type)}",
            f"课堂信息 JSON：\n{json.dumps(classroom_context, ensure_ascii=False, indent=2)}",
            f"教师补充要求：\n{prompt.strip() or '无'}",
            "可参考的历史材料片段：\n"
            + (json.dumps(examples, ensure_ascii=False, indent=2) if examples else "暂无，请按课堂信息生成完整材料。"),
        ]
    )


async def _persist_final_material_record_update(record_id: int, record, parse_result, user: dict) -> dict:
    readme_content = build_import_readme(result=parse_result, original_name=record["source_file_name"] or parse_result.document_type_label)
    readme_bytes = readme_content.encode("utf-8")
    readme_hash = hashlib.sha256(readme_bytes).hexdigest()
    await _write_material_file(readme_hash, readme_bytes)

    parse_payload = _build_material_ai_parse_payload(parse_result)
    parse_payload_json = json.dumps(parse_payload, ensure_ascii=False)
    metadata_json = json.dumps(parse_result.metadata, ensure_ascii=False)
    export_payload_json = json.dumps(parse_result.export_payload, ensure_ascii=False)
    warnings_json = json.dumps(parse_result.warnings, ensure_ascii=False)
    content_quality_json = json.dumps(parse_result.content_quality, ensure_ascii=False)
    now = datetime.now().isoformat()
    parsed_id = int(record["parsed_material_id"] or 0) or None
    package_id = int(record["package_material_id"] or 0) or None

    with get_db_connection() as conn:
        current = conn.execute(
            "SELECT * FROM material_ai_import_records WHERE id = ? AND teacher_id = ?",
            (int(record_id), user["id"]),
        ).fetchone()
        if not current:
            raise HTTPException(404, "未找到可更新的解析记录")
        if parsed_id:
            material = ensure_teacher_material_owner(conn, parsed_id, user["id"])
            conn.execute(
                """
                UPDATE course_materials
                SET file_hash = ?,
                    file_size = ?,
                    ai_parse_status = 'completed',
                    ai_parse_result_json = ?,
                    ai_optimize_status = 'completed',
                    ai_optimized_markdown = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (readme_hash, len(readme_bytes), parse_payload_json, now, parsed_id),
            )
            refresh_root_git_metadata(conn, int(material["root_id"]))
        elif package_id:
            material = ensure_teacher_material_owner(conn, package_id, user["id"])
            refresh_root_git_metadata(conn, int(material["root_id"]))
        conn.execute(
            """
            UPDATE material_ai_import_records
            SET parse_status = 'completed',
                parse_mode = ?,
                extraction_method = ?,
                metadata_json = ?,
                content_markdown = ?,
                parsed_payload_json = ?,
                export_payload_json = ?,
                warnings_json = ?,
                content_quality_status = ?,
                content_quality_json = ?,
                error_message = '',
                updated_at = ?,
                completed_at = ?
            WHERE id = ?
            """,
            (
                "ai_optimized" if parse_result.ai_used else "local_fallback",
                parse_result.extraction_method,
                metadata_json,
                parse_result.content_markdown,
                parse_payload_json,
                export_payload_json,
                warnings_json,
                parse_result.content_quality.get("status", "ok"),
                content_quality_json,
                now,
                now,
                int(record_id),
            ),
        )
        conn.commit()
        refreshed = conn.execute(
            "SELECT * FROM material_ai_import_records WHERE id = ?",
            (int(record_id),),
        ).fetchone()
        return _serialize_material_ai_import_task(conn, refreshed, user)


async def _create_generated_final_material_package(
    *,
    class_offering_id: int,
    parent_id: int | None,
    parse_result,
    user: dict,
) -> dict:
    readme_content = build_import_readme(result=parse_result, original_name=f"{parse_result.document_type_label}.md")
    readme_bytes = readme_content.encode("utf-8")
    readme_hash = hashlib.sha256(readme_bytes).hexdigest()
    await _write_material_file(readme_hash, readme_bytes)

    readme_profile = infer_material_profile("readme.md", "text/markdown")
    parse_payload = _build_material_ai_parse_payload(parse_result)
    parse_payload_json = json.dumps(parse_payload, ensure_ascii=False)
    metadata_json = json.dumps(parse_result.metadata, ensure_ascii=False)
    export_payload_json = json.dumps(parse_result.export_payload, ensure_ascii=False)
    warnings_json = json.dumps(parse_result.warnings, ensure_ascii=False)
    content_quality_json = json.dumps(parse_result.content_quality, ensure_ascii=False)

    with get_db_connection() as conn:
        classroom_context = _load_final_material_classroom_context(conn, class_offering_id, user)
        base_parent = None
        base_prefix = ""
        inherited_root_id = None
        if parent_id is not None:
            base_parent = ensure_teacher_material_owner(conn, parent_id, user["id"])
            if base_parent["node_type"] != "folder":
                raise HTTPException(400, "只能生成到文件夹中")
            base_prefix = str(base_parent["material_path"])
            inherited_root_id = int(base_parent["root_id"])

        owner_scope = load_teacher_org_scope(conn, user["id"])
        now = datetime.now().isoformat()
        course_name = str(classroom_context.get("course_name") or "").strip()
        package_base_name = f"AI生成-{parse_result.document_type_label}-{course_name or '期末材料'}"
        package_name = make_unique_material_name(conn, user["id"], parent_id, package_base_name)
        package_path = normalize_material_path(f"{base_prefix}/{package_name}" if base_prefix else package_name)
        package_id, package_root_id = _insert_material_folder_row(
            conn,
            user=user,
            name=package_name,
            material_path=package_path,
            parent_id=base_parent["id"] if base_parent else None,
            inherited_root_id=inherited_root_id,
            owner_scope=owner_scope,
            now=now,
        )

        parsed_name = "readme.md"
        parsed_path = normalize_material_path(f"{package_path}/{parsed_name}")
        parsed_id = _insert_material_file_row(
            conn,
            user=user,
            name=parsed_name,
            material_path=parsed_path,
            parent_id=package_id,
            root_id=package_root_id,
            file_profile=readme_profile,
            file_hash=readme_hash,
            file_size=len(readme_bytes),
            owner_scope=owner_scope,
            now=now,
            ai_parse_status="completed",
            ai_parse_result_json=parse_payload_json,
        )
        cursor = conn.execute(
            """
            INSERT INTO material_ai_import_records
            (teacher_id, package_material_id, source_material_id, parsed_material_id,
             parent_material_id, document_group, document_type, document_type_label,
             parse_status, parse_mode, extraction_method, source_file_name,
             source_file_hash, source_file_size, source_mime_type, metadata_json, content_markdown,
             parsed_payload_json, export_payload_json, warnings_json, content_quality_status,
             content_quality_json, error_message, created_at, updated_at, completed_at)
            VALUES (?, ?, NULL, ?, ?, ?, ?, ?, 'completed', ?, ?, ?, '', 0, 'application/json',
                    ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?)
            """,
            (
                user["id"],
                package_id,
                parsed_id,
                base_parent["id"] if base_parent else None,
                parse_result.document_group,
                parse_result.document_type,
                parse_result.document_type_label,
                "ai_generated" if parse_result.ai_used else "local_fallback",
                parse_result.extraction_method,
                f"{parse_result.document_type_label}-{course_name or '期末材料'}.json",
                metadata_json,
                parse_result.content_markdown,
                parse_payload_json,
                export_payload_json,
                warnings_json,
                parse_result.content_quality.get("status", "ok"),
                content_quality_json,
                now,
                now,
                now,
            ),
        )
        record_id = int(cursor.lastrowid)
        conn.execute(
            """
            INSERT OR IGNORE INTO course_material_assignments
            (material_id, class_offering_id, assigned_by_teacher_id, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (package_id, int(class_offering_id), user["id"], now),
        )
        refresh_root_git_metadata(conn, package_root_id)
        conn.commit()
        record = conn.execute(
            "SELECT * FROM material_ai_import_records WHERE id = ?",
            (record_id,),
        ).fetchone()
        return _serialize_material_ai_import_task(conn, record, user)


async def _create_generated_final_material_library_package(
    *,
    parent_id: int | None,
    parse_result,
    user: dict,
) -> dict:
    readme_content = build_import_readme(result=parse_result, original_name=f"{parse_result.document_type_label}.md")
    readme_bytes = readme_content.encode("utf-8")
    readme_hash = hashlib.sha256(readme_bytes).hexdigest()
    await _write_material_file(readme_hash, readme_bytes)

    readme_profile = infer_material_profile("readme.md", "text/markdown")
    parse_payload = _build_material_ai_parse_payload(parse_result)
    parse_payload_json = json.dumps(parse_payload, ensure_ascii=False)
    metadata_json = json.dumps(parse_result.metadata, ensure_ascii=False)
    export_payload_json = json.dumps(parse_result.export_payload, ensure_ascii=False)
    warnings_json = json.dumps(parse_result.warnings, ensure_ascii=False)
    content_quality_json = json.dumps(parse_result.content_quality, ensure_ascii=False)

    with get_db_connection() as conn:
        base_parent = None
        base_prefix = ""
        inherited_root_id = None
        if parent_id is not None:
            base_parent = ensure_teacher_material_owner(conn, parent_id, user["id"])
            if base_parent["node_type"] != "folder":
                raise HTTPException(400, "只能生成到文件夹中")
            base_prefix = str(base_parent["material_path"])
            inherited_root_id = int(base_parent["root_id"])

        owner_scope = load_teacher_org_scope(conn, user["id"])
        now = datetime.now().isoformat()
        course_name = str(parse_result.metadata.get("course_name") or "").strip()
        package_base_name = f"AI生成-{parse_result.document_type_label}-{course_name or '期末材料'}"
        package_name = make_unique_material_name(conn, user["id"], parent_id, package_base_name)
        package_path = normalize_material_path(f"{base_prefix}/{package_name}" if base_prefix else package_name)
        package_id, package_root_id = _insert_material_folder_row(
            conn,
            user=user,
            name=package_name,
            material_path=package_path,
            parent_id=base_parent["id"] if base_parent else None,
            inherited_root_id=inherited_root_id,
            owner_scope=owner_scope,
            now=now,
        )
        parsed_name = "readme.md"
        parsed_path = normalize_material_path(f"{package_path}/{parsed_name}")
        parsed_id = _insert_material_file_row(
            conn,
            user=user,
            name=parsed_name,
            material_path=parsed_path,
            parent_id=package_id,
            root_id=package_root_id,
            file_profile=readme_profile,
            file_hash=readme_hash,
            file_size=len(readme_bytes),
            owner_scope=owner_scope,
            now=now,
            ai_parse_status="completed",
            ai_parse_result_json=parse_payload_json,
        )
        cursor = conn.execute(
            """
            INSERT INTO material_ai_import_records
            (teacher_id, package_material_id, source_material_id, parsed_material_id,
             parent_material_id, document_group, document_type, document_type_label,
             parse_status, parse_mode, extraction_method, source_file_name,
             source_file_hash, source_file_size, source_mime_type, metadata_json, content_markdown,
             parsed_payload_json, export_payload_json, warnings_json, content_quality_status,
             content_quality_json, error_message, created_at, updated_at, completed_at)
            VALUES (?, ?, NULL, ?, ?, ?, ?, ?, 'completed', ?, ?, ?, '', 0, 'application/json',
                    ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?)
            """,
            (
                user["id"],
                package_id,
                parsed_id,
                base_parent["id"] if base_parent else None,
                parse_result.document_group,
                parse_result.document_type,
                parse_result.document_type_label,
                "ai_generated" if parse_result.ai_used else "local_fallback",
                parse_result.extraction_method,
                f"{parse_result.document_type_label}-{course_name or '期末材料'}.json",
                metadata_json,
                parse_result.content_markdown,
                parse_payload_json,
                export_payload_json,
                warnings_json,
                parse_result.content_quality.get("status", "ok"),
                content_quality_json,
                now,
                now,
                now,
            ),
        )
        record_id = int(cursor.lastrowid)
        refresh_root_git_metadata(conn, package_root_id)
        conn.commit()
        record = conn.execute(
            "SELECT * FROM material_ai_import_records WHERE id = ?",
            (record_id,),
        ).fetchone()
        return _serialize_material_ai_import_task(conn, record, user)


@router.post("/api/materials/ai-import", response_class=JSONResponse)
async def ai_import_material(
    file: UploadFile = File(...),
    document_group: str = Form(...),
    document_type: str = Form(...),
    parent_id: int | None = Form(default=None),
    user: dict = Depends(get_current_teacher),
):
    original_name = _normalize_uploaded_filename(file.filename)
    type_meta = resolve_material_ai_import_type(document_group, document_type)

    if parent_id is not None:
        with get_db_connection() as conn:
            base_parent = ensure_teacher_material_owner(conn, parent_id, user["id"])
            if base_parent["node_type"] != "folder":
                raise HTTPException(400, "只能导入到文件夹中")

    payload_bytes = await file.read()
    if not payload_bytes:
        raise HTTPException(400, "请选择非空材料文件")

    file_hash = hashlib.sha256(payload_bytes).hexdigest()
    stored_path = await _write_material_file(file_hash, payload_bytes)
    source_file_size = len(payload_bytes)
    source_mime_type = str(file.content_type or "").strip()
    initial_metadata = {
        "source_file_hash": file_hash,
        "source_file_size": source_file_size,
        "source_mime_type": source_mime_type,
        "source_filename": original_name,
        "document_group": type_meta["group_label"],
        "document_type": type_meta["label"],
        "parent_material_id": parent_id,
        "storage_path": str(stored_path),
    }

    with get_db_connection() as conn:
        _recover_stale_material_ai_import_tasks(conn)
        active_count = conn.execute(
            """
            SELECT COUNT(*) AS active_count
            FROM material_ai_import_records
            WHERE parse_status IN ('queued', 'running')
            """,
        ).fetchone()["active_count"]
        if int(active_count or 0) >= MATERIAL_AI_IMPORT_QUEUE_MAX_PENDING:
            raise HTTPException(429, "当前 AI 材料解析任务较多，请稍后再试。")

        base_parent = None
        if parent_id is not None:
            base_parent = ensure_teacher_material_owner(conn, parent_id, user["id"])
            if base_parent["node_type"] != "folder":
                raise HTTPException(400, "只能导入到文件夹中")
        now = datetime.now().isoformat()
        record_cursor = conn.execute(
            """
            INSERT INTO material_ai_import_records
            (teacher_id, package_material_id, source_material_id, parsed_material_id,
             parent_material_id, document_group, document_type, document_type_label,
             parse_status, parse_mode, extraction_method, source_file_name,
             source_file_hash, source_file_size, source_mime_type, metadata_json, content_markdown,
             parsed_payload_json, export_payload_json, warnings_json, content_quality_status,
             content_quality_json, error_message, created_at, updated_at, completed_at)
            VALUES (?, NULL, NULL, NULL, ?, ?, ?, ?, 'queued', 'ai', '', ?, ?, ?, ?, ?, '',
                    NULL, NULL, '[]', 'unchecked', '{}', '', ?, ?, NULL)
            """,
            (
                user["id"],
                base_parent["id"] if base_parent else None,
                type_meta["group_key"],
                type_meta["key"],
                type_meta["label"],
                original_name,
                file_hash,
                source_file_size,
                source_mime_type,
                json.dumps(initial_metadata, ensure_ascii=False),
                now,
                now,
            ),
        )
        import_record_id = int(record_cursor.lastrowid)
        conn.commit()
        row = conn.execute(
            "SELECT * FROM material_ai_import_records WHERE id = ?",
            (import_record_id,),
        ).fetchone()
        task = _serialize_material_ai_import_task(conn, row, user)

    if not _enqueue_material_ai_import_task(import_record_id):
        _mark_material_ai_import_failed(
            import_record_id,
            "failed",
            "当前 AI 材料解析队列已满，请稍后重新发起。",
        )
        raise HTTPException(429, "当前 AI 材料解析队列已满，请稍后重新发起。")

    return {
        "status": "queued",
        "message": f"《{original_name}》已加入 AI 解析队列，完成后会自动出现在当前材料列表。",
        "import_record_id": import_record_id,
        "task": task,
    }


@router.get("/api/materials/ai-import-records/active", response_class=JSONResponse)
async def list_ai_import_records(
    parent_id: int | None = Query(default=None),
    recent_minutes: int = Query(default=MATERIAL_AI_IMPORT_RECENT_MINUTES, ge=1, le=1440),
    user: dict = Depends(get_current_teacher),
):
    cutoff = (datetime.now() - timedelta(minutes=max(1, recent_minutes))).isoformat()
    params: list[Any] = [user["id"]]
    parent_clause = "parent_material_id IS NULL"
    if parent_id is not None:
        with get_db_connection() as conn:
            parent_row = ensure_teacher_material_owner(conn, parent_id, user["id"])
            if parent_row["node_type"] != "folder":
                raise HTTPException(400, "只能查看文件夹下的解析任务")
        parent_clause = "parent_material_id = ?"
        params.append(int(parent_id))

    params.append(cutoff)
    with get_db_connection() as conn:
        _recover_stale_material_ai_import_tasks(conn)
        rows = conn.execute(
            f"""
            SELECT *
            FROM material_ai_import_records
            WHERE teacher_id = ?
              AND {parent_clause}
              AND (
                    parse_status IN ('queued', 'running')
                    OR updated_at >= ?
              )
            ORDER BY
                CASE WHEN parse_status IN ('queued', 'running') THEN 0 ELSE 1 END,
                updated_at DESC,
                id DESC
            LIMIT 20
            """,
            params,
        ).fetchall()
        conn.commit()
        tasks = [_serialize_material_ai_import_task(conn, row, user) for row in rows]

    for task in tasks:
        if task["parse_status"] == "queued":
            _enqueue_material_ai_import_task(int(task["id"]))

    return {
        "status": "success",
        "tasks": tasks,
        "poll_interval_ms": 3500,
    }


@router.get("/api/materials/ai-import-records/{record_id}/status", response_class=JSONResponse)
async def get_ai_import_record_status(
    record_id: int,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        _recover_stale_material_ai_import_tasks(conn)
        row = conn.execute(
            """
            SELECT *
            FROM material_ai_import_records
            WHERE id = ? AND teacher_id = ?
            """,
            (int(record_id), user["id"]),
        ).fetchone()
        if not row:
            raise HTTPException(404, "未找到该 AI 解析任务")
        conn.commit()
        task = _serialize_material_ai_import_task(conn, row, user)

    if task["parse_status"] == "queued":
        _enqueue_material_ai_import_task(int(task["id"]))

    return {
        "status": "success",
        "task": task,
    }


@router.get("/api/materials/ai-import-records/{record_id}/export", response_class=FileResponse)
async def export_ai_import_record(
    record_id: int,
    format: str = Query(default=""),
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM material_ai_import_records
            WHERE id = ? AND teacher_id = ?
            """,
            (record_id, user["id"]),
        ).fetchone()
        if not row:
            raise HTTPException(404, "未找到可导出的解析记录")
        payload = _build_ai_import_payload_from_record(row)
        fallback_filename = row["source_file_name"] or f"材料解析-{record_id}"

    artifact = build_material_export_artifact(
        payload,
        fallback_filename=fallback_filename,
        requested_format=format,
    )
    suffix = Path(artifact.filename).suffix or ".docx"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
        temp_file.write(artifact.content)
        temp_path = temp_file.name
    return FileResponse(
        temp_path,
        media_type=artifact.media_type,
        filename=artifact.filename,
        background=BackgroundTask(_cleanup_temp_file, temp_path),
    )


@router.get("/api/materials/{material_id}/ai-import/export", response_class=FileResponse)
async def export_ai_import_material(
    material_id: int,
    format: str = Query(default=""),
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        material = ensure_teacher_material_owner(conn, material_id, user["id"])
        row = conn.execute(
            """
            SELECT *
            FROM material_ai_import_records
            WHERE teacher_id = ?
              AND (
                    parsed_material_id = ?
                    OR package_material_id = ?
                    OR source_material_id = ?
              )
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (user["id"], material["id"], material["id"], material["id"]),
        ).fetchone()
        if not row:
            raise HTTPException(404, "该材料没有关联的 AI 解析导出记录")
        record_id = int(row["id"])
    return await export_ai_import_record(record_id=record_id, format=format, user=user)


@router.get("/api/materials/{material_id}/ai-import/preview", response_class=JSONResponse)
async def preview_ai_import_material(
    material_id: int,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        ensure_teacher_material_owner(conn, material_id, user["id"])
        record = _find_material_ai_import_record(conn, material_id, user["id"], completed_only=True)
        if not record:
            raise HTTPException(404, "该材料没有可预览的期末材料解析结果")
        task = _serialize_material_ai_import_task(conn, record, user)
        preview = _build_ai_import_preview(record)
    return {
        "status": "success",
        "task": task,
        "preview": preview,
    }


@router.post("/api/materials/{material_id}/ai-import/optimize", response_class=JSONResponse)
async def optimize_ai_import_material(
    material_id: int,
    payload: MaterialAiImportOptimizeRequest,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        ensure_teacher_material_owner(conn, material_id, user["id"])
        record = _find_material_ai_import_record(conn, material_id, user["id"], completed_only=True)
        if not record:
            raise HTTPException(404, "该材料没有可优化的 AI 解析结果")
        if str(record["document_type"] or "") not in FINAL_MATERIAL_TYPES:
            raise HTTPException(400, "当前仅支持对期末材料执行结构化优化")
        classroom_context: dict[str, Any] = {}
        if payload.class_offering_id:
            classroom_context = _load_final_material_classroom_context(conn, int(payload.class_offering_id), user)
        current_payload = _build_ai_import_payload_from_record(record)

    system_prompt = _build_final_material_ai_system_prompt(str(record["document_type"]))
    user_prompt = "\n\n".join(
        [
            "请优化这份已经解析入库的期末材料，修正字段缺漏、结构层次和导出字段，但不要删除原有关键内容。",
            f"教师优化要求：\n{payload.prompt.strip() or '请提升结构化完整性、导出可用性和表述规范性。'}",
            f"课堂关联信息：\n{json.dumps(classroom_context, ensure_ascii=False, indent=2) if classroom_context else '未提供'}",
            f"当前材料 JSON：\n{json.dumps(current_payload, ensure_ascii=False, indent=2)[:30000]}",
        ]
    )
    raw_result = await _call_ai_chat(
        system_prompt,
        user_prompt,
        capability="thinking",
        response_format="json",
        task_type="material_final_optimize",
        task_label="materials:final-optimize",
        timeout=240.0,
    )
    extraction = MaterialExtraction(
        text=str(raw_result.get("content_markdown") or current_payload.get("content_markdown") or ""),
        method="ai_optimize",
        source_kind="ai_generated",
        warnings=[],
        quality={"usable": True},
    )
    type_meta = resolve_material_ai_import_type("final_material", str(record["document_type"]))
    parse_result = normalize_ai_parse_result(
        raw_result,
        original_name=record["source_file_name"] or type_meta["label"],
        type_meta=type_meta,
        extraction=extraction,
        extra_warnings=[],
        ai_used=True,
    )
    if classroom_context:
        parse_result.export_payload = normalize_final_material_payload(
            document_type=parse_result.document_type,
            metadata=parse_result.metadata,
            content_markdown=parse_result.content_markdown,
            tables=parse_result.tables,
            export_payload=parse_result.export_payload,
            classroom_context=classroom_context,
        )
        parse_result.metadata.update(parse_result.export_payload.get("fields") or {})
        parse_result.parsed_payload["metadata"] = parse_result.metadata
        parse_result.parsed_payload["export_payload"] = parse_result.export_payload

    task = await _persist_final_material_record_update(int(record["id"]), record, parse_result, user)
    with get_db_connection() as conn:
        refreshed_record = conn.execute(
            "SELECT * FROM material_ai_import_records WHERE id = ? AND teacher_id = ?",
            (int(record["id"]), user["id"]),
        ).fetchone()
        preview = _build_ai_import_preview(refreshed_record) if refreshed_record else None
    return {
        "status": "success",
        "message": "期末材料已优化并更新导出字段",
        "task": task,
        "preview": preview,
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
        owner_scope = load_teacher_org_scope(conn, int(user["id"]))

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
                     ai_parse_status, ai_optimize_status, owner_role, owner_user_pk, scope_level,
                     school_code, school_name, college, department, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 'folder', 'inode/directory', 'folder', 'none', '', NULL, 0,
                            'idle', 'idle', 'teacher', ?, 'private', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user["id"],
                        folder_parent_id,
                        inherited_root_id,
                        folder_path,
                        folder_name,
                        user["id"],
                        owner_scope["school_code"],
                        owner_scope["school_name"],
                        owner_scope["college"],
                        owner_scope["department"],
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
                  ai_parse_status, ai_optimize_status, owner_role, owner_user_pk, scope_level,
                  school_code, school_name, college, department, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'file', ?, ?, ?, ?, ?, ?, 'idle', 'idle',
                        'teacher', ?, 'private', ?, ?, ?, ?, ?, ?)
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
                    user["id"],
                    owner_scope["school_code"],
                    owner_scope["school_name"],
                    owner_scope["college"],
                    owner_scope["department"],
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
            created_items = [_decorate_learning_document_item(item) for item in _serialize_material_items(conn, created_rows, user=user)]

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
        material = ensure_user_material_access(conn, material_id, user)
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


@router.patch("/api/materials/{material_id}/scope", response_class=JSONResponse)
async def update_material_scope(
    material_id: int,
    payload: MaterialScopeUpdateRequest,
    user: dict = Depends(get_current_teacher),
):
    normalized_scope = str(payload.scope_level or "private").strip().lower()
    if normalized_scope not in {"private", "school", "department"}:
        raise HTTPException(400, "Invalid material scope")
    now_text = datetime.now().isoformat()
    with get_db_connection() as conn:
        material = ensure_user_material_access(conn, material_id, user)
        owner_scope = load_teacher_org_scope(conn, int(material["teacher_id"]))
        conn.execute(
            """
            UPDATE course_materials
            SET scope_level = ?,
                owner_role = 'teacher',
                owner_user_pk = ?,
                school_code = ?,
                school_name = ?,
                college = ?,
                department = ?,
                published_at = CASE WHEN ? != 'private' THEN COALESCE(published_at, ?) ELSE published_at END,
                updated_at = ?
            WHERE root_id = ?
              AND (material_path = ? OR material_path LIKE ?)
            """,
            (
                normalized_scope,
                int(material["teacher_id"]),
                owner_scope["school_code"],
                owner_scope["school_name"],
                owner_scope["college"],
                owner_scope["department"],
                normalized_scope,
                now_text,
                now_text,
                int(material["root_id"]),
                material["material_path"],
                f"{material['material_path']}/%",
            ),
        )
        conn.commit()
        refreshed = ensure_teacher_material_owner(conn, material_id, user["id"])
        item = _serialize_material_items(conn, [refreshed], user=user)[0]
    return {"status": "success", "material": item}


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
                if await delete_global_file(file_hash):
                    removed_files += 1

    return {
        "status": "success",
        "message": f"《{material['name']}》已删除",
        "removed_file_count": removed_files,
    }


@router.post("/api/classrooms/{class_offering_id}/final-materials/generate", response_class=JSONResponse)
async def generate_classroom_final_material(
    class_offering_id: int,
    payload: ClassroomFinalMaterialGenerateRequest,
    user: dict = Depends(get_current_teacher),
):
    document_type = str(payload.document_type or "").strip()
    if document_type not in FINAL_MATERIAL_TYPES:
        raise HTTPException(400, "期末材料类型不受支持")
    type_meta = resolve_material_ai_import_type("final_material", document_type)

    with get_db_connection() as conn:
        classroom_context = _load_final_material_classroom_context(conn, class_offering_id, user)
        if document_type == "assessment_plan":
            assessment_mode = str(payload.assessment_mode or "").strip()
            assessment_method = str(payload.assessment_method or "").strip()
            if assessment_mode:
                classroom_context["assessment_mode"] = assessment_mode
                classroom_context["assessment_mode_label"] = "笔试考核" if assessment_mode == "written" else "非笔试考核"
            if assessment_method:
                classroom_context["assessment_method"] = assessment_method
        elif document_type == "exam_paper":
            assessment_plan_record = _load_latest_final_material_record_for_classroom(
                conn,
                class_offering_id=class_offering_id,
                teacher_id=user["id"],
                document_type="assessment_plan",
            )
            if not assessment_plan_record:
                raise HTTPException(409, "请先在本课堂导入或生成“课程考核计划表”，再根据计划表生成课程考核试卷。")
            classroom_context["source_assessment_plan"] = _final_material_record_context(assessment_plan_record)
        elif document_type == "grading_rubric":
            exam_paper_record = _load_latest_final_material_record_for_classroom(
                conn,
                class_offering_id=class_offering_id,
                teacher_id=user["id"],
                document_type="exam_paper",
            )
            if not exam_paper_record:
                raise HTTPException(409, "请先在本课堂导入或生成“课程考核试卷”，再根据具体试题生成评分细则。")
            classroom_context["source_exam_paper"] = _final_material_record_context(exam_paper_record)
        if payload.parent_id is not None:
            parent = ensure_teacher_material_owner(conn, payload.parent_id, user["id"])
            if parent["node_type"] != "folder":
                raise HTTPException(400, "只能生成到文件夹中")
        examples = _load_final_material_examples(
            conn,
            teacher_id=user["id"],
            document_type=document_type,
            course_name=str(classroom_context.get("course_name") or ""),
        )

    ai_used = True
    raw_result: dict[str, Any]
    try:
        raw_response = await _call_ai_chat(
            _build_final_material_ai_system_prompt(document_type),
            _build_final_material_ai_user_prompt(
                document_type=document_type,
                classroom_context=classroom_context,
                prompt=payload.prompt,
                examples=examples,
            ),
            capability="thinking",
            response_format="json",
            task_type="material_final_generate",
            task_label="materials:final-generate",
            timeout=300.0,
        )
        raw_result = raw_response if isinstance(raw_response, dict) else {}
        if not raw_result:
            raise HTTPException(500, "AI 未返回有效 JSON")
    except Exception as exc:
        ai_used = False
        raw_result = build_final_material_generation_seed(
            document_type=document_type,
            classroom_context=classroom_context,
            prompt=payload.prompt,
        )
        warning = exc.detail if isinstance(exc, HTTPException) else str(exc)
        raw_result.setdefault("warnings", [])
        if isinstance(raw_result["warnings"], list):
            raw_result["warnings"].append(f"AI 生成不可用，已使用本地草稿模板：{warning}")

    extraction = MaterialExtraction(
        text=str(raw_result.get("content_markdown") or ""),
        method="ai_generate" if ai_used else "local_generation_seed",
        source_kind="ai_generated" if ai_used else "local_generated",
        warnings=[],
        quality={"usable": True},
    )
    parse_result = normalize_ai_parse_result(
        raw_result,
        original_name=f"{type_meta['label']}-{classroom_context.get('course_name') or '期末材料'}.json",
        type_meta=type_meta,
        extraction=extraction,
        extra_warnings=[],
        ai_used=ai_used,
    )
    parse_result.export_payload = normalize_final_material_payload(
        document_type=document_type,
        metadata=parse_result.metadata,
        content_markdown=parse_result.content_markdown,
        tables=parse_result.tables,
        export_payload=parse_result.export_payload,
        classroom_context=classroom_context,
    )
    parse_result.metadata.update(parse_result.export_payload.get("fields") or {})
    parse_result.parsed_payload["metadata"] = parse_result.metadata
    parse_result.parsed_payload["export_payload"] = parse_result.export_payload

    task = await _create_generated_final_material_package(
        class_offering_id=class_offering_id,
        parent_id=payload.parent_id,
        parse_result=parse_result,
        user=user,
    )
    return {
        "status": "success",
        "message": f"{'AI' if ai_used else '本地草稿'}已生成{type_meta['label']}，并保存到课程材料。",
        "task": task,
        "ai_used": ai_used,
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
    class_offering_id: int | None = Query(default=None),
    session_id: int | None = Query(default=None),
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
                "class_offering_id": class_offering_id,
                "session_id": session_id,
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
            "learning_context": {
                "class_offering_id": class_offering_id,
                "session_id": session_id,
            },
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


async def _run_ai_material_rewrite(
    *,
    material_id: int,
    mode: str,
    prompt: str,
    user: dict,
) -> dict[str, Any]:
    normalized_mode = "regenerate" if str(mode or "").strip().lower() == "regenerate" else "optimize"
    with get_db_connection() as conn:
        material = dict(ensure_teacher_material_owner(conn, material_id, user["id"]))
        context_rows = _collect_material_context_rows(conn, material)
        conn.execute(
            "UPDATE course_materials SET ai_optimize_status = 'running', updated_at = ? WHERE id = ?",
            (datetime.now().isoformat(), material_id),
        )
        conn.commit()

    try:
        attachment = await _build_material_context_attachment(material, context_rows)
        raw_result = await _call_ai_chat(
            _build_ai_material_rewrite_system_prompt(normalized_mode),
            _build_ai_material_rewrite_user_prompt(
                mode=normalized_mode,
                material=material,
                prompt=prompt,
                attachment=attachment,
            ),
            capability="thinking",
            response_format="json",
            file_texts=[{"name": attachment.get("title") or material["name"], "content": attachment.get("content") or ""}],
            task_type="material_ai_regenerate" if normalized_mode == "regenerate" else "material_ai_optimize",
            task_label=f"materials:ai-{normalized_mode}",
            timeout=300.0,
        )
        fallback_title = str(material.get("name") or "课程材料")
        parse_result = _build_generic_material_parse_result(
            raw_result=raw_result,
            fallback_title=fallback_title,
            attachments=[attachment],
            ai_used=True,
        )
        markdown_content = build_import_readme(
            result=parse_result,
            original_name=f"{parse_result.metadata.get('title') or fallback_title}.md",
        )
        parse_payload_json = json.dumps(_build_material_ai_parse_payload(parse_result), ensure_ascii=False)

        if (
            normalized_mode == "optimize"
            and material["node_type"] == "file"
            and str(material.get("preview_type") or "") == "markdown"
        ):
            with get_db_connection() as conn:
                conn.execute(
                    """
                    UPDATE course_materials
                    SET ai_optimize_status = 'completed',
                        ai_optimized_markdown = ?,
                        ai_parse_status = 'completed',
                        ai_parse_result_json = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (markdown_content, parse_payload_json, datetime.now().isoformat(), material_id),
                )
                conn.commit()
                item = _fetch_material_response_item(conn, material_id, user)
            return {
                "status": "success",
                "message": "AI 已优化材料，并生成可查看优化稿",
                "mode": normalized_mode,
                "material": item,
                "viewer_url": f"/materials/view/{material_id}?variant=optimized",
                "updated_existing": True,
            }

        target_parent_id = int(material["id"]) if material["node_type"] == "folder" else (
            int(material["parent_id"]) if material.get("parent_id") is not None else None
        )
        generated = await _create_generated_markdown_material(
            user=user,
            parent_id=target_parent_id,
            title=str(parse_result.metadata.get("title") or fallback_title),
            markdown_content=markdown_content,
            parse_payload_json=parse_payload_json,
            name_prefix="AI重生成" if normalized_mode == "regenerate" else "AI优化",
        )
        with get_db_connection() as conn:
            conn.execute(
                "UPDATE course_materials SET ai_optimize_status = 'completed', updated_at = ? WHERE id = ?",
                (datetime.now().isoformat(), material_id),
            )
            conn.commit()
        return {
            "status": "success",
            "message": "AI 已生成新的 Markdown 材料",
            "mode": normalized_mode,
            "material": generated,
            "viewer_url": f"/materials/view/{generated['id']}",
            "updated_existing": False,
        }
    except Exception as exc:
        with get_db_connection() as conn:
            conn.execute(
                "UPDATE course_materials SET ai_optimize_status = 'failed', updated_at = ? WHERE id = ?",
                (datetime.now().isoformat(), material_id),
            )
            conn.commit()
        if isinstance(exc, HTTPException):
            raise exc
        raise HTTPException(500, f"AI 材料处理失败: {exc}")


@router.post("/api/materials/{material_id}/ai-rewrite", response_class=JSONResponse)
async def ai_rewrite_material(
    material_id: int,
    payload: MaterialAiRewriteRequest,
    user: dict = Depends(get_current_teacher),
):
    return await _run_ai_material_rewrite(
        material_id=material_id,
        mode=payload.mode,
        prompt=payload.prompt,
        user=user,
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
        await delete_global_file(old_hash)

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
    return await _run_ai_material_rewrite(
        material_id=material_id,
        mode="optimize",
        prompt="",
        user=user,
    )
