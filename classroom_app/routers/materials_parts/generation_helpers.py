from .common import *
from ...db.connection import execute_insert_returning_id, get_configured_db_engine


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
    db_engine = get_configured_db_engine()
    insert_sql = """
        INSERT INTO course_materials
        (teacher_id, parent_id, root_id, material_path, name, node_type, mime_type,
         preview_type, ai_capability, file_ext, file_hash, file_size,
         ai_parse_status, ai_optimize_status, owner_role, owner_user_pk, scope_level,
         school_code, school_name, college, department, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'folder', 'inode/directory', 'folder', 'none', '', NULL, 0,
                'idle', 'idle', 'teacher', ?, 'private', ?, ?, ?, ?, ?, ?)
    """
    folder_id = execute_insert_returning_id(
        conn,
        insert_sql,
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
        engine=db_engine,
    )
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
    root_id: int | None,
    file_profile: dict,
    file_hash: str,
    file_size: int,
    owner_scope: dict,
    now: str,
    ai_parse_status: str = "idle",
    ai_parse_result_json: str | None = None,
) -> int:
    db_engine = get_configured_db_engine()
    insert_sql = """
        INSERT INTO course_materials
        (teacher_id, parent_id, root_id, material_path, name, node_type, mime_type,
         preview_type, ai_capability, file_ext, file_hash, file_size,
         ai_parse_status, ai_parse_result_json, ai_optimize_status, owner_role, owner_user_pk, scope_level,
         school_code, school_name, college, department, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'file', ?, ?, ?, ?, ?, ?, ?, ?, 'idle',
                'teacher', ?, 'private', ?, ?, ?, ?, ?, ?)
    """
    file_id = execute_insert_returning_id(
        conn,
        insert_sql,
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
        engine=db_engine,
    )
    if root_id is None:
        conn.execute("UPDATE course_materials SET root_id = ? WHERE id = ?", (file_id, file_id))
    return file_id


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


__all__ = [name for name in globals() if not name.startswith("__")]
