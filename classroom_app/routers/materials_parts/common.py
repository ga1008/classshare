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

from ...core import ai_client, templates
from ...database import get_db_connection
from ...dependencies import get_current_teacher, get_current_user
from ...schemas.materials_contracts import (
    ClassroomMaterialsResponse,
    MaterialAiGenerationCandidatesResponse,
    MaterialAiImportActiveResponse,
    MaterialAiImportPreviewResponse,
    MaterialAiImportStatusResponse,
    MaterialDetailResponse,
    MaterialLibraryResponse,
    MaterialRepositoryResponse,
)
from ...services.file_service import delete_global_file, global_file_write_path, resolve_global_file_path, save_file_globally
from ...services.download_policy import apply_download_policy, ensure_download_allowed
from ...services.file_preview_service import TEXT_CONTENT_ENCODINGS
from ...services.material_ai_import_service import (
    MaterialExtraction,
    build_import_readme,
    extract_material_content,
    get_material_ai_import_registry,
    normalize_ai_parse_result,
    parse_material_document,
    resolve_material_ai_import_type,
)
from ...services.material_export_template_service import build_material_export_artifact
from ...services.material_final_document_service import (
    FINAL_MATERIAL_TYPES,
    build_final_material_generation_seed,
    final_material_label,
    normalize_final_material_payload,
)
from ...services.materials_service import (
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
from ...services.course_planning_service import build_timeline_home_entry
from ...services.materials_git_service import (
    attach_git_repository_metadata,
    execute_material_repository_action,
    get_material_repository_detail,
    refresh_root_git_metadata,
    save_material_repository_credential,
)
from ...services.message_center_service import is_super_admin_teacher
from ...services.organization_scope_service import load_teacher_org_memberships, load_teacher_org_scope
from ...services.session_material_generation_service import (
    create_generation_task,
    extract_example_documents,
    get_teacher_session_with_material_state,
    normalize_document_type,
    normalize_requirement_text,
    run_generation_task,
)



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


def _normalize_material_scope_filter(value: str | None) -> str:
    scope = str(value or "all").strip().lower()
    return scope if scope in {"all", "private", "department", "school", "shared", "owned"} else "all"


def _normalize_material_org_filter(value: str | None) -> str:
    return " ".join(str(value or "").split())[:80]


def _build_material_filter_facets(rows, teacher_id: int) -> dict[str, Any]:
    scopes: dict[str, int] = {}
    schools: dict[str, str] = {}
    departments: dict[str, str] = {}
    for row in rows:
        row_dict = dict(row)
        scope = str(row_dict.get("scope_level") or "private").strip().lower() or "private"
        scopes[scope] = scopes.get(scope, 0) + 1
        school_label = str(row_dict.get("school_name") or row_dict.get("school_code") or "").strip()
        if school_label:
            schools.setdefault(school_label.lower(), school_label)
        department_label = str(row_dict.get("department") or "").strip()
        if department_label:
            departments.setdefault(department_label.lower(), department_label)
    return {
        "scopes": scopes,
        "schools": sorted(schools.values(), key=lambda item: item.lower()),
        "departments": sorted(departments.values(), key=lambda item: item.lower()),
    }


def _apply_material_library_filters(rows, *, teacher_id: int, scope_filter: str, school: str, department: str) -> list:
    scope_filter = _normalize_material_scope_filter(scope_filter)
    school_filter = _normalize_material_org_filter(school).lower()
    department_filter = _normalize_material_org_filter(department).lower()
    filtered = []
    for row in rows:
        row_scope = str(row["scope_level"] or "private").strip().lower() or "private"
        owned = int(row["teacher_id"] or 0) == int(teacher_id)
        if scope_filter == "owned" and not owned:
            continue
        if scope_filter == "shared" and owned:
            continue
        if scope_filter in {"private", "department", "school"} and row_scope != scope_filter:
            continue
        row_school = str(row["school_name"] or row["school_code"] or "").strip().lower()
        if school_filter and row_school != school_filter:
            continue
        row_department = str(row["department"] or "").strip().lower()
        if department_filter and row_department != department_filter:
            continue
        filtered.append(row)
    return filtered


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


__all__ = [name for name in globals() if not name.startswith("__")]
