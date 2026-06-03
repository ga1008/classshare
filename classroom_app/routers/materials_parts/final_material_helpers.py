from .common import *
from .generation_helpers import *
from .ai_import_helpers import *


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


__all__ = [name for name in globals() if not name.startswith("__")]
