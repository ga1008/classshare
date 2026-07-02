"""材料库节点管理：新建文件夹 / 新建 Markdown 文档 / 移动 / 文件夹选项 / AI 续写材料。"""

from .common import *
from .generation_helpers import *
from .final_material_helpers import *


router = APIRouter()

MATERIAL_NODE_NAME_MAX_LEN = 120


class MaterialFolderCreateRequest(BaseModel):
    name: str = ""
    parent_id: int | None = None


class MaterialFileCreateRequest(BaseModel):
    name: str = ""
    parent_id: int | None = None
    content: str = ""


class MaterialMoveRequest(BaseModel):
    target_parent_id: int | None = None


class MaterialAiExpandRequest(BaseModel):
    parent_id: int
    prompt: str = ""


def _normalize_node_name(raw_name: str, *, fallback: str = "") -> str:
    name = str(raw_name or "").strip()[:MATERIAL_NODE_NAME_MAX_LEN]
    if not name and fallback:
        name = fallback
    if not name:
        raise HTTPException(400, "请填写名称")
    normalized = normalize_material_path(name, fallback_name=name)
    if "/" in normalized or normalized in {"", ".", ".git"}:
        raise HTTPException(400, "名称不能包含 / 等路径字符，也不能叫 .git")
    return normalized


def _resolve_create_base(conn, parent_id: int | None, teacher_id: int):
    """Return (base_parent_row_or_None, base_prefix, inherited_root_id, inherited_scope)."""
    if parent_id is None:
        return None, "", None, "private"
    base_parent = ensure_teacher_material_owner(conn, parent_id, teacher_id)
    if base_parent["node_type"] != "folder":
        raise HTTPException(400, "只能在文件夹中新建")
    if is_git_internal_material_path(base_parent["material_path"]):
        raise HTTPException(400, "不能在 Git 内部目录中新建")
    inherited_scope = str(base_parent["scope_level"] or "private").strip().lower() or "private"
    return base_parent, str(base_parent["material_path"]), int(base_parent["root_id"]), inherited_scope


@router.post("/api/materials/folders", response_class=JSONResponse)
async def create_material_folder(
    payload: MaterialFolderCreateRequest,
    user: dict = Depends(get_current_teacher),
):
    name = _normalize_node_name(payload.name)
    with get_db_connection() as conn:
        base_parent, base_prefix, inherited_root_id, inherited_scope = _resolve_create_base(
            conn, payload.parent_id, user["id"]
        )
        unique_name = make_unique_material_name(
            conn, int(user["id"]), int(base_parent["id"]) if base_parent else None, name
        )
        material_path = normalize_material_path(f"{base_prefix}/{unique_name}" if base_prefix else unique_name)
        owner_scope = load_teacher_org_scope(conn, int(user["id"]))
        now = datetime.now().isoformat()
        folder_id, actual_root_id = _insert_material_folder_row(
            conn,
            user=user,
            name=unique_name,
            material_path=material_path,
            parent_id=int(base_parent["id"]) if base_parent else None,
            inherited_root_id=inherited_root_id,
            owner_scope=owner_scope,
            now=now,
            scope_level=inherited_scope,
        )
        refresh_root_git_metadata(conn, int(actual_root_id))
        conn.commit()
        item = _fetch_material_response_item(conn, folder_id, user)
    return {
        "status": "success",
        "message": f"文件夹《{unique_name}》已创建",
        "material": item,
    }


@router.post("/api/materials/files", response_class=JSONResponse)
async def create_material_markdown_file(
    payload: MaterialFileCreateRequest,
    user: dict = Depends(get_current_teacher),
):
    name = _normalize_node_name(payload.name)
    if not name.lower().endswith((".md", ".markdown")):
        name = f"{name}.md"
    content = str(payload.content or "")
    if not content.strip():
        title = name.rsplit(".", 1)[0]
        content = f"# {title}\n\n"
    payload_bytes = content.encode("utf-8")
    file_hash = hashlib.sha256(payload_bytes).hexdigest()
    await _write_material_file(file_hash, payload_bytes)

    with get_db_connection() as conn:
        base_parent, base_prefix, inherited_root_id, inherited_scope = _resolve_create_base(
            conn, payload.parent_id, user["id"]
        )
        unique_name = make_unique_material_name(
            conn, int(user["id"]), int(base_parent["id"]) if base_parent else None, name
        )
        material_path = normalize_material_path(f"{base_prefix}/{unique_name}" if base_prefix else unique_name)
        owner_scope = load_teacher_org_scope(conn, int(user["id"]))
        now = datetime.now().isoformat()
        file_profile = infer_material_profile(unique_name, "text/markdown")
        file_id = _insert_material_file_row(
            conn,
            user=user,
            name=unique_name,
            material_path=material_path,
            parent_id=int(base_parent["id"]) if base_parent else None,
            root_id=inherited_root_id,
            file_profile=file_profile,
            file_hash=file_hash,
            file_size=len(payload_bytes),
            owner_scope=owner_scope,
            now=now,
            scope_level=inherited_scope,
        )
        actual_root_id = inherited_root_id or file_id
        refresh_root_git_metadata(conn, int(actual_root_id))
        conn.commit()
        item = _fetch_material_response_item(conn, file_id, user)
    return {
        "status": "success",
        "message": f"文档《{unique_name}》已创建",
        "material": item,
        "viewer_url": f"/materials/view/{file_id}",
    }


@router.get("/api/materials/folder-options", response_class=JSONResponse)
async def list_material_folder_options(
    exclude_subtree_of: int | None = Query(default=None),
    user: dict = Depends(get_current_teacher),
):
    """教师自己的全部文件夹（用于“移动到 / 新建位置”下拉选择）。"""
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, parent_id, root_id, name, material_path
            FROM course_materials
            WHERE teacher_id = ?
              AND node_type = 'folder'
            ORDER BY material_path COLLATE NOCASE
            """,
            (int(user["id"]),),
        ).fetchall()
        excluded_prefix = None
        excluded_id = None
        if exclude_subtree_of is not None:
            excluded = ensure_teacher_material_owner(conn, int(exclude_subtree_of), user["id"])
            excluded_prefix = f"{excluded['material_path']}/"
            excluded_id = int(excluded["id"])

    options = []
    for row in rows:
        row_path = str(row["material_path"] or "")
        if is_git_internal_material_path(row_path):
            continue
        if excluded_id is not None and (int(row["id"]) == excluded_id or row_path.startswith(excluded_prefix)):
            continue
        options.append(
            {
                "id": int(row["id"]),
                "name": str(row["name"] or ""),
                "material_path": row_path,
                "depth": row_path.count("/"),
            }
        )
    return {"status": "success", "folders": options}


@router.post("/api/materials/{material_id}/move", response_class=JSONResponse)
async def move_material_node(
    material_id: int,
    payload: MaterialMoveRequest,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        material = ensure_teacher_material_owner(conn, material_id, user["id"])
        if is_git_internal_material_path(material["material_path"]):
            raise HTTPException(400, "不能移动 Git 内部内容")

        target_parent = None
        if payload.target_parent_id is not None:
            target_parent = ensure_teacher_material_owner(conn, int(payload.target_parent_id), user["id"])
            if target_parent["node_type"] != "folder":
                raise HTTPException(400, "只能移动到文件夹中")
            if is_git_internal_material_path(target_parent["material_path"]):
                raise HTTPException(400, "不能移动到 Git 内部目录")
            if int(target_parent["id"]) == int(material["id"]):
                raise HTTPException(400, "不能把项目移动到它自己里面")
            if is_descendant_path(str(target_parent["material_path"]), str(material["material_path"])):
                raise HTTPException(400, "不能移动到自己的子目录中")

        current_parent_id = int(material["parent_id"]) if material["parent_id"] is not None else None
        target_parent_id = int(target_parent["id"]) if target_parent else None
        if current_parent_id == target_parent_id:
            return {"status": "success", "message": "材料已在目标位置", "unchanged": True}

        old_root_id = int(material["root_id"])
        old_path = str(material["material_path"])
        subtree = [dict(row) for row in _collect_subtree_rows(conn, material)]

        unique_name = make_unique_material_name(
            conn, int(material["teacher_id"]), target_parent_id, str(material["name"])
        )
        renamed = unique_name != str(material["name"])
        base_prefix = str(target_parent["material_path"]) if target_parent else ""
        new_path = normalize_material_path(f"{base_prefix}/{unique_name}" if base_prefix else unique_name)
        new_root_id = int(target_parent["root_id"]) if target_parent else int(material["id"])

        # 移动后归属权随目标最外层文件夹；移动到根目录时自身成为最外层，保留当前设置。
        dest_scope = None
        if target_parent:
            dest_root = conn.execute(
                "SELECT scope_level, school_code, school_name, college, department FROM course_materials WHERE id = ?",
                (new_root_id,),
            ).fetchone()
            if dest_root:
                dest_scope = dict(dest_root)

        now_text = datetime.now().isoformat()
        for row in subtree:
            row_path = str(row["material_path"] or "")
            suffix = row_path[len(old_path):] if row_path.startswith(old_path) else ""
            conn.execute(
                """
                UPDATE course_materials
                SET material_path = ?,
                    root_id = ?,
                    parent_id = CASE WHEN id = ? THEN ? ELSE parent_id END,
                    name = CASE WHEN id = ? THEN ? ELSE name END,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    f"{new_path}{suffix}",
                    new_root_id,
                    int(material["id"]),
                    target_parent_id,
                    int(material["id"]),
                    unique_name,
                    now_text,
                    int(row["id"]),
                ),
            )
        if dest_scope:
            subtree_ids = [int(row["id"]) for row in subtree]
            placeholders = ",".join("?" for _ in subtree_ids)
            conn.execute(
                f"""
                UPDATE course_materials
                SET scope_level = ?, school_code = ?, school_name = ?, college = ?, department = ?
                WHERE id IN ({placeholders})
                """,
                [
                    str(dest_scope.get("scope_level") or "private"),
                    dest_scope.get("school_code"),
                    dest_scope.get("school_name"),
                    dest_scope.get("college"),
                    dest_scope.get("department"),
                    *subtree_ids,
                ],
            )

        for root_id in {old_root_id, new_root_id}:
            refresh_root_git_metadata(conn, int(root_id))
        conn.commit()
        item = _fetch_material_response_item(conn, int(material["id"]), user)

    target_label = target_parent["name"] if target_parent else "材料库根目录"
    message = f"《{material['name']}》已移动到 {target_label}"
    if renamed:
        message += f"，因重名自动改名为《{unique_name}》"
    return {"status": "success", "message": message, "material": item, "renamed": renamed}


def _serialize_tree_node(row: dict) -> dict[str, Any]:
    item = serialize_material_row(row)
    return {
        "id": int(row["id"]),
        "root_id": int(row["root_id"]) if row.get("root_id") is not None else int(row["id"]),
        "parent_id": int(row["parent_id"]) if row.get("parent_id") is not None else None,
        "name": str(row.get("name") or ""),
        "node_type": str(row.get("node_type") or "file"),
        "preview_type": str(row.get("preview_type") or ""),
        "type_label": str(item.get("type_label") or ""),
        "file_ext": str(row.get("file_ext") or ""),
        "file_size": int(row.get("file_size") or 0),
        "material_path": str(row.get("material_path") or ""),
        "created_at": str(row.get("created_at") or ""),
        "updated_at": str(row.get("updated_at") or ""),
        "preview_supported": bool(item.get("preview_supported")),
        "editable": bool(item.get("editable")),
        "is_markdown": bool(item.get("is_markdown")),
        "is_text": bool(item.get("is_text")),
        "is_image": bool(item.get("is_image")),
        "can_ai_parse": bool(item.get("can_ai_parse")),
        "can_ai_optimize": bool(item.get("can_ai_optimize")),
        "children": [],
    }


@router.get("/api/materials/{material_id}/tree", response_class=JSONResponse)
async def get_material_subtree(
    material_id: int,
    user: dict = Depends(get_current_user),
):
    """材料所属最外层节点的完整目录树（排除 Git 内部内容），供浮窗资源管理器使用。"""
    with get_db_connection() as conn:
        material = ensure_user_material_access(conn, material_id, user)
        root_row = conn.execute(
            "SELECT * FROM course_materials WHERE id = ?",
            (int(material["root_id"]),),
        ).fetchone()
        if not root_row:
            raise HTTPException(404, "材料根节点不存在")
        rows = [dict(row) for row in _collect_subtree_rows(conn, root_row, include_internal=False)]

    nodes: dict[int, dict[str, Any]] = {}
    for row in rows:
        nodes[int(row["id"])] = _serialize_tree_node(row)

    root_node = nodes.get(int(root_row["id"]))
    if not root_node:
        raise HTTPException(404, "材料根节点不存在")
    for node in nodes.values():
        parent_id = node["parent_id"]
        if node["id"] == root_node["id"] or parent_id is None:
            continue
        parent = nodes.get(parent_id)
        if parent:
            parent["children"].append(node)

    def _sort_children(node: dict[str, Any]):
        node["children"].sort(key=lambda item: (0 if item["node_type"] == "folder" else 1, item["name"].lower()))
        node["child_count"] = len(node["children"])
        for child in node["children"]:
            _sort_children(child)

    _sort_children(root_node)

    file_rows = [row for row in rows if row.get("node_type") == "file"]
    stats = {
        "folder_count": sum(1 for row in rows if row.get("node_type") == "folder"),
        "file_count": len(file_rows),
        "total_size": sum(int(row.get("file_size") or 0) for row in file_rows),
        "latest_updated_at": max((str(row.get("updated_at") or "") for row in rows), default=""),
    }
    return {
        "status": "success",
        "tree": root_node,
        "selected_id": int(material["id"]),
        "stats": stats,
    }


def _build_ai_material_expand_system_prompt() -> str:
    return (
        "你是一名深度思考型课程材料续写助手。"
        "教师会给你一个课程材料文件夹的完整目录结构和其中已有材料的内容，"
        "你的任务是根据这些已有材料的体例、深度、编号顺序和相对位置，续写出下一份材料"
        "（例如根据前三次课的材料和目录文档生成第四次课的材料）。"
        "请严格返回 JSON 对象，不要输出 Markdown 代码块，也不要输出任何与材料内容无关的说明、开场白或结尾语。"
        "JSON 必须包含 file_name（含 .md 后缀、遵循同目录已有文件的命名规律）、title、summary、"
        "content_markdown、metadata、outline、keywords、teaching_value、cautions、warnings。"
        "content_markdown 必须是可直接保存为课程材料的完整 Markdown 正文，只包含材料本身。"
        "体例、术语、难度递进要与同文件夹已有材料保持一致，不得编造未提供的事实。"
    )


def _build_ai_material_expand_user_prompt(
    *,
    prompt: str,
    folder_context: dict[str, Any],
    attachment: dict[str, Any],
) -> str:
    return "\n\n".join(
        [
            "请根据下面这个文件夹里的已有材料，续写下一份材料。",
            f"目标文件夹：\n{json.dumps(folder_context, ensure_ascii=False, indent=2)}",
            f"教师补充提示（可选）：\n{prompt.strip() or '无补充提示，请根据已有材料的顺序和目录规划自动判断下一份材料应当是什么。'}",
            f"已有材料上下文来源：{attachment.get('title')}",
            "再次强调：只输出 JSON；content_markdown 只包含材料正文本身，不要任何解释性文字。",
        ]
    )


@router.post("/api/materials/ai-expand", response_class=JSONResponse)
async def ai_expand_material_folder(
    payload: MaterialAiExpandRequest,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        folder = dict(ensure_teacher_material_owner(conn, int(payload.parent_id), user["id"]))
        if folder["node_type"] != "folder":
            raise HTTPException(400, "AI 续写只能基于文件夹")
        context_rows = _collect_material_context_rows(conn, folder)
        folder_context = {
            "id": int(folder["id"]),
            "name": folder["name"],
            "material_path": folder["material_path"],
            "item_count": max(0, len(context_rows) - 1),
        }

    attachment = await _build_material_context_attachment(folder, context_rows)
    raw_result = await _call_ai_chat(
        _build_ai_material_expand_system_prompt(),
        _build_ai_material_expand_user_prompt(
            prompt=payload.prompt,
            folder_context=folder_context,
            attachment=attachment,
        ),
        capability="thinking",
        response_format="json",
        file_texts=[{"name": attachment.get("title") or folder["name"], "content": attachment.get("content") or ""}],
        task_type="material_ai_generate",
        task_label="materials:ai-expand",
        timeout=300.0,
    )

    fallback_title = f"{folder['name']}-续写材料"
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

    raw_file_name = ""
    if isinstance(raw_result, dict):
        raw_file_name = str(raw_result.get("file_name") or raw_result.get("filename") or "").strip()
    file_stem = _safe_generated_material_base_name(
        raw_file_name.rsplit(".", 1)[0] if raw_file_name else "",
        fallback=str(parse_result.metadata.get("title") or fallback_title),
    )
    material = await _create_generated_markdown_material(
        user=user,
        parent_id=int(payload.parent_id),
        title=file_stem,
        markdown_content=markdown_content,
        parse_payload_json=parse_payload_json,
        name_prefix="",
    )
    return {
        "status": "success",
        "message": f"AI 已续写生成《{material.get('name')}》",
        "material": material,
        "viewer_url": f"/materials/view/{material['id']}",
    }


__all__ = [name for name in globals() if not name.startswith("__")]
