import traceback

from .common import *
from .generation_helpers import *
from .ai_import_helpers import *
from .final_material_helpers import *
from .rewrite_helpers import *
from ...services.ordinary_grade_record_service import (
    ORDINARY_GRADE_RECORD_TYPE,
    build_ordinary_grade_record_payload,
    list_ordinary_grade_assignment_candidates,
)
from ...services.exam_grade_record_service import (
    EXAM_GRADE_RECORD_TYPE,
    build_exam_grade_record_payload,
    list_exam_grade_record_candidates,
)
from ...services.final_grade_transcript_service import (
    FINAL_GRADE_TRANSCRIPT_TYPE,
    build_final_grade_transcript_payload,
    build_final_grade_transcript_readiness,
)
from ...services.academic_exam_roster_sync_service import (
    ACADEMIC_EXAM_ROSTER_CACHE_SECONDS,
    sync_classroom_exam_roster_from_academic_system,
)
from ...services.smart_classroom_checkin_sync_service import (
    ORDINARY_GRADE_ATTENDANCE_CACHE_SECONDS,
    get_classroom_smart_attendance_freshness,
    sync_teacher_smart_classroom_checkins,
)


router = APIRouter()


async def _sync_fresh_attendance_for_ordinary_generation(user_id: int, class_offering_id: int) -> dict[str, Any]:
    attendance_sync = await sync_teacher_smart_classroom_checkins(
        int(user_id),
        class_offering_id=int(class_offering_id),
        min_refresh_interval_seconds=ORDINARY_GRADE_ATTENDANCE_CACHE_SECONDS,
    )
    sync_status = str(attendance_sync.get("status") or "").strip()
    if sync_status == "missing_credential":
        raise HTTPException(
            409,
            "生成前需要刷新智慧课堂考勤，但尚未配置可用的智慧课堂账号。请先在系统设置中完成账号验证。",
        )
    if sync_status == "failed":
        raise HTTPException(
            502,
            attendance_sync.get("message") or "智慧课堂考勤同步失败，请稍后重试。",
        )
    sync_freshness = attendance_sync.get("freshness") if isinstance(attendance_sync.get("freshness"), dict) else {}
    if sync_status not in {"cached", "success", "partial_success", "empty"} or not sync_freshness.get("is_fresh"):
        raise HTTPException(
            409,
            "智慧课堂同步已完成，但没有找到能与当前课堂可靠对应的最新考勤数据。"
            "请先核对课堂教学班、课程代码和智慧课堂课表，再重新生成，系统不会用旧数据冒险生成。",
        )
    return attendance_sync


def _local_grade_record_parse_result(
    *,
    document_type: str,
    export_payload: dict[str, Any],
    classroom_context: dict[str, Any],
):
    type_meta = resolve_material_ai_import_type("final_material", document_type)
    is_ordinary = document_type == ORDINARY_GRADE_RECORD_TYPE
    raw_result = {
        "metadata": export_payload.get("fields") or {},
        "content_markdown": export_payload.get("content_markdown") or "",
        "tables": export_payload.get("tables") or [],
        "warnings": (export_payload.get("structured") or {}).get("warnings") or [],
        "export_payload": export_payload,
    }
    extraction = MaterialExtraction(
        text=str(raw_result.get("content_markdown") or ""),
        method="ordinary_grade_local_generation" if is_ordinary else "exam_grade_local_generation",
        source_kind="classroom_scores" if is_ordinary else "classroom_exam_scores",
        warnings=[],
        quality={"usable": True},
    )
    parse_result = normalize_ai_parse_result(
        raw_result,
        original_name=f"{type_meta['label']}-{classroom_context.get('course_name') or '期末材料'}.json",
        type_meta=type_meta,
        extraction=extraction,
        extra_warnings=[],
        ai_used=False,
    )
    parse_result.export_payload = export_payload
    parse_result.metadata.update(export_payload.get("fields") or {})
    parse_result.parsed_payload["metadata"] = parse_result.metadata
    parse_result.parsed_payload["export_payload"] = parse_result.export_payload
    return parse_result


def _final_material_source_summary(record) -> dict[str, Any] | None:
    if not record:
        return None
    context = _final_material_record_context(record)
    return {
        "record_id": context.get("record_id"),
        "document_type": context.get("document_type") or "",
        "document_type_label": context.get("document_type_label") or "",
        "title": context.get("title") or "",
        "updated_at": context.get("updated_at") or "",
    }


@router.get("/api/classrooms/{class_offering_id}/ordinary-grade-record/candidates", response_class=JSONResponse)
async def list_classroom_ordinary_grade_record_candidates(
    class_offering_id: int,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        ensure_classroom_access(conn, class_offering_id, user)
        items = list_ordinary_grade_assignment_candidates(
            conn,
            class_offering_id=class_offering_id,
            teacher_id=user["id"],
        )
        attendance_sync = get_classroom_smart_attendance_freshness(
            conn,
            teacher_id=int(user["id"]),
            class_offering_id=int(class_offering_id),
        )
    return {"status": "success", "items": items, "attendance_sync": attendance_sync}


@router.get("/api/classrooms/{class_offering_id}/exam-grade-record/candidates", response_class=JSONResponse)
async def list_classroom_exam_grade_record_candidates(
    class_offering_id: int,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        ensure_classroom_access(conn, class_offering_id, user)
        items = list_exam_grade_record_candidates(
            conn,
            class_offering_id=class_offering_id,
            teacher_id=user["id"],
        )
    return {"status": "success", "items": items}


@router.post(
    "/api/classrooms/{class_offering_id}/final-grade-transcript/prepare",
    response_class=JSONResponse,
)
async def prepare_classroom_final_grade_transcript(
    class_offering_id: int,
    payload: FinalGradeTranscriptPrepareRequest,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        ensure_classroom_access(conn, class_offering_id, user)
    roster_sync = await sync_classroom_exam_roster_from_academic_system(
        int(user["id"]),
        int(class_offering_id),
        exam_course_key=str(payload.exam_course_key or "").strip(),
        min_refresh_interval_seconds=ACADEMIC_EXAM_ROSTER_CACHE_SECONDS,
    )
    sync_status = str(roster_sync.get("status") or "")
    if sync_status != "success":
        return {
            "status": sync_status or "failed",
            "ready": False,
            "message": roster_sync.get("message") or "考试名单同步未完成。",
            "roster_sync": roster_sync,
        }
    with get_db_connection() as conn:
        try:
            readiness = build_final_grade_transcript_readiness(
                conn,
                class_offering_id=int(class_offering_id),
                teacher_id=int(user["id"]),
            )
        except HTTPException:
            raise
        except Exception:
            traceback.print_exc()
            return {
                "status": "verification_failed",
                "ready": False,
                "message": (
                    "考试名单已保留，但成绩来源核对暂时失败。"
                    "系统没有把核对失败误判为材料缺失，请稍后重试。"
                ),
                "roster_sync": roster_sync,
            }
    return {
        "status": "success",
        **readiness,
        "roster_sync": {
            "status": sync_status,
            "message": roster_sync.get("message") or "",
            "alignment": roster_sync.get("alignment") or {},
            "synced_at": (readiness.get("roster") or {}).get("synced_at") or "",
            "cache_hit": bool(roster_sync.get("cache_hit")),
            "sync_mode": roster_sync.get("sync_mode") or "",
            "freshness": roster_sync.get("freshness") or {},
        },
    }


@router.get("/api/classrooms/{class_offering_id}/final-materials/prerequisites", response_class=JSONResponse)
async def get_classroom_final_material_prerequisites(
    class_offering_id: int,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        ensure_classroom_access(conn, class_offering_id, user)
        assessment_plan_record = _load_latest_final_material_record_for_classroom(
            conn,
            class_offering_id=class_offering_id,
            teacher_id=user["id"],
            document_type="assessment_plan",
        )
        exam_paper_record = _load_latest_final_material_record_for_classroom(
            conn,
            class_offering_id=class_offering_id,
            teacher_id=user["id"],
            document_type="exam_paper",
        )

    assessment_plan_source = _final_material_source_summary(assessment_plan_record)
    exam_paper_source = _final_material_source_summary(exam_paper_record)
    return {
        "status": "success",
        "prerequisites": {
            "exam_paper": {
                "ready": bool(assessment_plan_source),
                "source_type": "assessment_plan",
                "source_label": "课程考核计划表",
                "source_record": assessment_plan_source,
                "message": "" if assessment_plan_source else "请先在本课堂导入或生成“课程考核计划表”，再根据计划表生成课程考核试卷。",
            },
            "grading_rubric": {
                "ready": bool(exam_paper_source),
                "source_type": "exam_paper",
                "source_label": "课程考核试卷",
                "source_record": exam_paper_source,
                "message": "" if exam_paper_source else "请先在本课堂导入或生成“课程考核试卷”，再根据具体试题生成评分细则。",
            },
        },
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

    attendance_sync: dict[str, Any] | None = None
    if document_type == ORDINARY_GRADE_RECORD_TYPE:
        with get_db_connection() as conn:
            ensure_classroom_access(conn, class_offering_id, user)
        attendance_sync = await _sync_fresh_attendance_for_ordinary_generation(
            int(user["id"]),
            int(class_offering_id),
        )

    final_grade_roster_sync: dict[str, Any] | None = None
    if document_type == FINAL_GRADE_TRANSCRIPT_TYPE:
        with get_db_connection() as conn:
            ensure_classroom_access(conn, class_offering_id, user)
        final_grade_roster_sync = await sync_classroom_exam_roster_from_academic_system(
            int(user["id"]),
            int(class_offering_id),
            exam_course_key=str(payload.exam_course_key or "").strip(),
            min_refresh_interval_seconds=ACADEMIC_EXAM_ROSTER_CACHE_SECONDS,
        )
        if str(final_grade_roster_sync.get("status") or "") != "success":
            raise HTTPException(
                409,
                final_grade_roster_sync.get("message")
                or "生成前未能完成教务系统考试名单同步，请重新核对。",
            )

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
        if document_type == ORDINARY_GRADE_RECORD_TYPE:
            export_payload = build_ordinary_grade_record_payload(
                conn,
                class_offering_id=class_offering_id,
                teacher_id=user["id"],
                homework_assignment_ids=payload.homework_assignment_ids,
                assessment_assignment_id=payload.assessment_assignment_id or 0,
                classroom_context=classroom_context,
                attendance_sync=attendance_sync,
                generation_requirements=payload.prompt,
                minimum_ordinary_score_enabled=payload.minimum_ordinary_score_enabled,
                minimum_ordinary_score=payload.minimum_ordinary_score,
            )
            parse_result = _local_grade_record_parse_result(
                document_type=document_type,
                export_payload=export_payload,
                classroom_context=classroom_context,
            )
            task = await _create_generated_final_material_package(
                class_offering_id=class_offering_id,
                parent_id=payload.parent_id,
                parse_result=parse_result,
                user=user,
            )
            return {
                "status": "success",
                "message": (
                    "已使用 30 分钟内的智慧课堂考勤缓存、3 份作业和 1 份测评生成平时成绩记录表，并保存到课程材料。"
                    if attendance_sync and attendance_sync.get("cache_hit")
                    else "已在生成前刷新智慧课堂考勤，并根据 3 份作业和 1 份测评生成平时成绩记录表，保存到课程材料。"
                ),
                "task": task,
                "ai_used": False,
                "attendance_sync": attendance_sync,
            }
        if document_type == EXAM_GRADE_RECORD_TYPE:
            export_payload = build_exam_grade_record_payload(
                conn,
                class_offering_id=class_offering_id,
                teacher_id=user["id"],
                exam_assignment_id=payload.exam_assignment_id or 0,
                classroom_context=classroom_context,
            )
            parse_result = _local_grade_record_parse_result(
                document_type=document_type,
                export_payload=export_payload,
                classroom_context=classroom_context,
            )
            task = await _create_generated_final_material_package(
                class_offering_id=class_offering_id,
                parent_id=payload.parent_id,
                parse_result=parse_result,
                user=user,
            )
            return {
                "status": "success",
                "message": "已根据所选课堂考试成绩生成考核登分表 Excel，并保存到课程材料。",
                "task": task,
                "ai_used": False,
            }
        if document_type == FINAL_GRADE_TRANSCRIPT_TYPE:
            if not str(payload.expected_roster_signature or "").strip():
                raise HTTPException(409, "生成窗口的名单确认信息已失效，请重新同步并核对后生成。")
            try:
                export_payload = build_final_grade_transcript_payload(
                    conn,
                    class_offering_id=int(class_offering_id),
                    teacher_id=int(user["id"]),
                    expected_roster_synced_at=str(final_grade_roster_sync.get("synced_at") or ""),
                    expected_roster_signature=str(payload.expected_roster_signature or "").strip(),
                    expected_ordinary_record_id=payload.ordinary_grade_record_id,
                    expected_exam_record_id=payload.exam_grade_record_id,
                )
            except HTTPException:
                raise
            except Exception as exc:
                traceback.print_exc()
                raise HTTPException(
                    503,
                    "期末成绩单来源核对暂时失败，现有名单和成绩材料均未被修改，请稍后重试。",
                ) from exc
            raw_result = {
                "metadata": export_payload.get("fields") or {},
                "content_markdown": export_payload.get("content_markdown") or "",
                "tables": export_payload.get("tables") or [],
                "warnings": (export_payload.get("structured") or {}).get("warnings") or [],
                "export_payload": export_payload,
            }
            extraction = MaterialExtraction(
                text=str(raw_result.get("content_markdown") or ""),
                method="final_grade_transcript_local_generation",
                source_kind="academic_roster_and_grade_records",
                warnings=[],
                quality={"usable": True},
            )
            parse_result = normalize_ai_parse_result(
                raw_result,
                original_name=f"期末成绩单-{classroom_context.get('course_name') or '课程'}.json",
                type_meta=type_meta,
                extraction=extraction,
                extra_warnings=[],
                ai_used=False,
            )
            parse_result.export_payload = export_payload
            parse_result.metadata.update(export_payload.get("fields") or {})
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
                "message": "已按教务考试名单顺序逐人核对平时与期末成绩，生成 1 份期末成绩单 Excel。",
                "task": task,
                "ai_used": False,
                "roster_sync": final_grade_roster_sync,
            }
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


@router.post("/api/materials/{material_id}/final-material/refresh", response_class=JSONResponse)
async def refresh_generated_grade_record_material(
    material_id: int,
    user: dict = Depends(get_current_teacher),
):
    """一键更新：按材料原本记录的课堂来源（作业/考试选择、最低分策略等）
    重新读取最新成绩数据，原地更新已生成的平时成绩表/考核登分表，
    不新建材料、不改变材料位置与课堂绑定。"""
    with get_db_connection() as conn:
        ensure_teacher_material_owner(conn, int(material_id), user["id"])
        record = _find_material_ai_import_record(
            conn,
            int(material_id),
            int(user["id"]),
            completed_only=True,
        )
        plan = build_grade_record_refresh_plan(record)
        ensure_classroom_access(conn, int(plan["class_offering_id"]), user)
    record_id = int(record["id"])
    document_type = str(plan["document_type"])
    class_offering_id = int(plan["class_offering_id"])

    attendance_sync: dict[str, Any] | None = None
    if document_type == ORDINARY_GRADE_RECORD_TYPE:
        attendance_sync = await _sync_fresh_attendance_for_ordinary_generation(
            int(user["id"]),
            class_offering_id,
        )

    with get_db_connection() as conn:
        classroom_context = _load_final_material_classroom_context(conn, class_offering_id, user)
        try:
            if document_type == ORDINARY_GRADE_RECORD_TYPE:
                export_payload = build_ordinary_grade_record_payload(
                    conn,
                    class_offering_id=class_offering_id,
                    teacher_id=user["id"],
                    homework_assignment_ids=plan["homework_assignment_ids"],
                    assessment_assignment_id=plan["assessment_assignment_id"],
                    classroom_context=classroom_context,
                    attendance_sync=attendance_sync,
                    generation_requirements=plan["generation_requirements"],
                    minimum_ordinary_score_enabled=plan["minimum_ordinary_score_enabled"],
                    minimum_ordinary_score=plan["minimum_ordinary_score"],
                )
            else:
                export_payload = build_exam_grade_record_payload(
                    conn,
                    class_offering_id=class_offering_id,
                    teacher_id=user["id"],
                    exam_assignment_id=plan["exam_assignment_id"],
                    classroom_context=classroom_context,
                )
        except HTTPException:
            raise
        except Exception:
            traceback.print_exc()
            raise HTTPException(503, "一键更新暂时失败，原材料内容未被修改，请稍后重试。")

    parse_result = _local_grade_record_parse_result(
        document_type=document_type,
        export_payload=export_payload,
        classroom_context=classroom_context,
    )
    task = await _persist_final_material_record_update(record_id, record, parse_result, user)
    label = "平时成绩记录表" if document_type == ORDINARY_GRADE_RECORD_TYPE else "考核登分表"
    return {
        "status": "success",
        "message": f"已按原有作业/考试选择重新获取最新成绩，原地更新{label}；材料位置与课堂绑定保持不变。",
        "task": task,
        "document_type": document_type,
        "attendance_sync": attendance_sync,
    }
