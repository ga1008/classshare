from .common import *
from .generation_helpers import *
from .ai_import_helpers import *
from .final_material_helpers import *
from .rewrite_helpers import *


router = APIRouter()


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
