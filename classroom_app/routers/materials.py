import json
import os
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
from ..services.materials_service import (
    MATERIAL_TYPE_REGISTRY,
    ensure_classroom_access,
    ensure_teacher_material_owner,
    ensure_user_material_access,
    get_effective_assignment_nodes,
    get_material_breadcrumbs,
    get_nearest_assignment_anchor,
    infer_material_profile,
    is_descendant_path,
    make_unique_material_name,
    normalize_material_path,
    serialize_material_row,
)

router = APIRouter()


class MaterialAssignRequest(BaseModel):
    class_offering_ids: list[int] = []


class MaterialBatchDownloadRequest(BaseModel):
    material_ids: list[int]


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
    optimized_content = _row_value(material_row, "ai_optimized_markdown")
    if prefer_optimized and optimized_content:
        return optimized_content

    file_path = _load_material_storage_path(material_row)
    async with aiofiles.open(file_path, "r", encoding="utf-8") as handle:
        return await handle.read()


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


def _list_material_rows_for_parent(conn, teacher_id: int, parent_id: int | None):
    if parent_id is None:
        query = """
            SELECT m.*,
                   (SELECT COUNT(*) FROM course_materials child WHERE child.parent_id = m.id) AS child_count,
                   (SELECT COUNT(*) FROM course_material_assignments a WHERE a.material_id = m.id) AS assignment_count
            FROM course_materials m
            WHERE m.teacher_id = ? AND m.parent_id IS NULL
            ORDER BY CASE WHEN m.node_type = 'folder' THEN 0 ELSE 1 END, m.name COLLATE NOCASE
        """
        return conn.execute(query, (teacher_id,)).fetchall()

    query = """
        SELECT m.*,
               (SELECT COUNT(*) FROM course_materials child WHERE child.parent_id = m.id) AS child_count,
               (SELECT COUNT(*) FROM course_material_assignments a WHERE a.material_id = m.id) AS assignment_count
        FROM course_materials m
        WHERE m.teacher_id = ? AND m.parent_id = ?
        ORDER BY CASE WHEN m.node_type = 'folder' THEN 0 ELSE 1 END, m.name COLLATE NOCASE
    """
    return conn.execute(query, (teacher_id, parent_id)).fetchall()


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


def _collect_subtree_rows(conn, material_row):
    return conn.execute(
        """
        SELECT *
        FROM course_materials
        WHERE root_id = ?
          AND (material_path = ? OR material_path LIKE ?)
        ORDER BY material_path
        """,
        (material_row["root_id"], material_row["material_path"], f"{material_row['material_path']}/%"),
    ).fetchall()


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
            subtree_rows = _collect_subtree_rows(conn, material_row)

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
        return [dict(row) for row in rows]

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
            SELECT o.id, o.semester, c.name AS class_name, co.name AS course_name
            FROM class_offerings o
            JOIN classes c ON o.class_id = c.id
            JOIN courses co ON o.course_id = co.id
            WHERE o.teacher_id = ?
            ORDER BY co.name, c.name
            """,
            (user["id"],),
        ).fetchall()
        stats = conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM course_materials WHERE teacher_id = ? AND parent_id IS NULL) AS root_count,
                (SELECT COUNT(*)
                    FROM course_material_assignments a
                    JOIN course_materials m ON m.id = a.material_id
                    WHERE m.teacher_id = ?) AS assignment_count
            """,
            (user["id"], user["id"]),
        ).fetchone()

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
            "offerings": [dict(row) for row in offerings],
            "material_stats": dict(stats) if stats else {"root_count": 0, "assignment_count": 0},
            "type_registry": type_registry,
        },
    )


@router.get("/api/materials/library", response_class=JSONResponse)
async def get_teacher_material_library(
    parent_id: int | None = Query(default=None),
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        current_folder = None
        breadcrumbs = []
        if parent_id is not None:
            current_folder = ensure_teacher_material_owner(conn, parent_id, user["id"])
            if current_folder["node_type"] != "folder":
                raise HTTPException(400, "只能打开文件夹")
            breadcrumbs = get_material_breadcrumbs(conn, parent_id)

        rows = _list_material_rows_for_parent(conn, user["id"], parent_id)
        items = [serialize_material_row(row) for row in rows]

    return {
        "status": "success",
        "current_folder": serialize_material_row(current_folder) if current_folder else None,
        "breadcrumbs": breadcrumbs,
        "items": items,
    }


@router.get("/api/materials/{material_id}", response_class=JSONResponse)
async def get_material_detail(material_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        material = ensure_teacher_material_owner(conn, material_id, user["id"])
        child_count = conn.execute(
            "SELECT COUNT(*) FROM course_materials WHERE parent_id = ?",
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

    return {"status": "success", "material": detail}


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

        conn.commit()

        created_items = []
        if top_level_created_ids:
            placeholders = ",".join("?" for _ in top_level_created_ids)
            created_rows = conn.execute(
                f"""
                SELECT m.*,
                       (SELECT COUNT(*) FROM course_materials child WHERE child.parent_id = m.id) AS child_count,
                       0 AS assignment_count
                FROM course_materials m
                WHERE m.id IN ({placeholders})
                ORDER BY CASE WHEN m.node_type = 'folder' THEN 0 ELSE 1 END, m.name COLLATE NOCASE
                """,
                top_level_created_ids,
            ).fetchall()
            created_items = [serialize_material_row(row) for row in created_rows]

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
                    "SELECT COUNT(*) FROM course_materials WHERE parent_id = ?",
                    (row["id"],),
                ).fetchone()[0]
                row_dict = dict(row)
                row_dict["child_count"] = int(child_count)
                items.append(serialize_material_row(row_dict))
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
                   (SELECT COUNT(*) FROM course_materials child WHERE child.parent_id = m.id) AS child_count,
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
            if is_descendant_path(row_dict["material_path"], anchor["material_path"]):
                items.append(serialize_material_row(row_dict))

        breadcrumbs = _slice_breadcrumbs_from_anchor(get_material_breadcrumbs(conn, parent_id), anchor["id"])
        return {
            "status": "success",
            "current_folder": serialize_material_row(folder),
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

        preview_payload = serialize_material_row(
            material,
            {
                "download_url": f"/materials/download/{material_id}",
                "raw_url": f"/materials/raw/{material_id}",
                "viewer_url": f"/materials/view/{material_id}",
                "preview_variant": preview_variant,
                "path_index": allowed_rows,
                "is_image": material["preview_type"] == "image",
                "is_markdown": material["preview_type"] == "markdown",
                "optimized_available": bool(material["ai_optimized_markdown"]),
                "ai_parse_result": json.loads(material["ai_parse_result_json"]) if material["ai_parse_result_json"] else None,
            },
        )

    if material["preview_type"] == "markdown":
        preview_payload["content"] = await _load_material_markdown(material, prefer_optimized=preview_variant == "optimized")
    else:
        preview_payload["content"] = None

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
    with get_db_connection() as conn:
        material = ensure_user_material_access(conn, material_id, user)
    if material["node_type"] != "file":
        raise HTTPException(400, "文件夹不能直接预览")
    file_path = _load_material_storage_path(material)
    return FileResponse(file_path, media_type=material["mime_type"] or "application/octet-stream")


@router.get("/materials/download/{material_id}", response_class=FileResponse)
async def download_material(material_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        material = ensure_user_material_access(conn, material_id, user)
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

        temp_path = _create_material_zip(conn, selected_rows)

    archive_title = f"course-materials-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    return FileResponse(
        temp_path,
        media_type="application/zip",
        filename=archive_title,
        background=BackgroundTask(_cleanup_temp_file, temp_path),
    )


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
