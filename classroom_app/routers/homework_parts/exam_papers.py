from .common import *
from ...services.assessment_classification_service import initialize_assignment_assessment_kind, normalize_assessment_kind
from ...services.base_resource_modes_service import (
    build_exam_delete_blockers,
    ensure_teacher_can_manage_exam_attributes,
    ensure_teacher_can_view_exam_attributes,
    raise_if_delete_blocked,
    serialize_exam_attributes,
    serialize_exam_content,
    update_exam_attributes,
)
from ...services.exam_material_reverse_service import (
    create_assessment_plan_reverse_placeholder,
    create_grading_rubric_reverse_placeholder,
    run_assessment_plan_reverse_job,
    run_grading_rubric_reverse_job,
)


from ...services.exam_paper_management_service import (
    create_exam_paper_record, update_exam_content_record, assign_exam_paper_record,
    lock_exam_paper, get_exam_review, list_exam_reviews, _count_exam_assignments, _count_exam_submissions,
    _count_exam_drafts, _sync_exam_assignment_content,
)

router = APIRouter()


@router.get("/exam-papers/review-catalog")
def review_exam_catalog(limit: int = 30, offset: int = 0, q: str = "", user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        return list_exam_reviews(conn, teacher_id=int(user["id"]), limit=limit, offset=offset, q=q)


@router.get("/exam-papers/{paper_id}/review")
def review_exam_paper(paper_id: str, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        return get_exam_review(conn, paper_id=paper_id, teacher_id=int(user["id"]))










@router.get(
    "/exam-papers",
    response_class=JSONResponse,
    response_model=ExamPapersResponse,
    response_model_exclude_unset=True,
)
async def list_exam_papers(user: dict = Depends(get_current_teacher)):
    """获取当前教师的所有试卷"""
    with get_db_connection() as conn:
        super_row = conn.execute(
            "SELECT COALESCE(is_super_admin, 0) AS is_super_admin FROM teachers WHERE id = ?",
            (int(user["id"]),),
        ).fetchone()
        is_super_admin = bool(super_row and int(super_row["is_super_admin"] or 0) == 1)
        cursor = conn.execute(
            """SELECT ep.*,
                      t.name AS owner_teacher_name,
                      (SELECT COUNT(*)
                       FROM assignments a
                       WHERE a.exam_paper_id = ep.id
                         AND NOT EXISTS (
                             SELECT 1 FROM learning_stage_exam_attempts lsea
                             WHERE lsea.assignment_id = a.id
                         )) as assigned_count
               FROM exam_papers ep
               LEFT JOIN teachers t ON t.id = ep.teacher_id
               WHERE (? = 1 OR ep.teacher_id = ? OR COALESCE(ep.scope_level, 'private') != 'private')
                 AND NOT EXISTS (
                     SELECT 1 FROM learning_stage_exam_attempts lsea
                     WHERE lsea.exam_paper_id = ep.id
                 )
               ORDER BY ep.updated_at DESC""",
            (
                1 if is_super_admin else 0,
                user["id"],
            )
        )
        papers = []
        for row in cursor:
            item = dict(row)
            if not teacher_can_use_exam_paper(conn, int(user["id"]), item):
                continue
            item["is_owned"] = int(item.get("teacher_id") or 0) == int(user["id"])
            item["can_manage"] = item["is_owned"] or is_super_admin
            item["is_shared_paper"] = not item["is_owned"]
            item["scope_level"] = _normalize_exam_open_scope(item.get("scope_level"), default=SCOPE_PRIVATE)
            item["scope_label"] = _exam_scope_label(item["scope_level"])
            papers.append(item)
    return {"status": "success", "papers": papers}


@router.put("/exam-papers/{paper_id}/tags", response_class=JSONResponse)
async def update_exam_paper_tags(paper_id: str, request: Request, user: dict = Depends(get_current_teacher)):
    """更新试卷标签"""
    data = await request.json()
    tags = data.get('tags', [])
    if not isinstance(tags, list) or len(tags) > 20:
        raise HTTPException(400, "标签格式不正确")
    for t in tags:
        if not isinstance(t, str) or len(t) == 0 or len(t) > 10:
            raise HTTPException(400, "每个标签长度应为1-10个字符")

    now = datetime.now().isoformat()
    with get_db_connection() as conn:
        lock_exam_paper(conn, paper_id)
        _get_exam_paper_for_teacher(conn, paper_id, int(user["id"]), manage=True)
        conn.execute(
            "UPDATE exam_papers SET tags_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(tags, ensure_ascii=False), now, paper_id)
        )
        conn.commit()
    return {"status": "success", "tags": tags}


@router.get("/exam-papers/{paper_id}/attributes", response_class=JSONResponse)
async def get_exam_paper_attributes(paper_id: str, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        paper = ensure_teacher_can_view_exam_attributes(conn, paper_id, int(user["id"]))
        attributes = serialize_exam_attributes(conn, paper, int(user["id"]))
    return {"status": "success", "resource_type": "exam_paper", "attributes": attributes}


@router.patch("/exam-papers/{paper_id}/attributes", response_class=JSONResponse)
async def patch_exam_paper_attributes(
    paper_id: str,
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(400, "请求数据格式错误")
    with get_db_connection() as conn:
        lock_exam_paper(conn, paper_id)
        paper = ensure_teacher_can_manage_exam_attributes(conn, paper_id, int(user["id"]))
        update_exam_attributes(conn, paper_row=paper, teacher_id=int(user["id"]), payload=payload)
        conn.commit()
        refreshed = ensure_teacher_can_view_exam_attributes(conn, paper_id, int(user["id"]))
        attributes = serialize_exam_attributes(conn, refreshed, int(user["id"]))
    return {"status": "success", "message": "试卷属性已保存", "attributes": attributes}


@router.get("/exam-papers/{paper_id}/content", response_class=JSONResponse)
async def get_exam_paper_content(paper_id: str, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        paper = ensure_teacher_can_view_exam_attributes(conn, paper_id, int(user["id"]))
        content = serialize_exam_content(conn, paper, int(user["id"]))
    return {"status": "success", "resource_type": "exam_paper", "content": content}


@router.put("/exam-papers/{paper_id}/content", response_class=JSONResponse)
async def put_exam_paper_content(paper_id: str, request: Request, user: dict = Depends(get_current_teacher)):
    payload = await request.json()
    with get_db_connection() as conn:
        result = update_exam_content_record(conn, paper_id=paper_id, teacher_id=int(user["id"]), payload=payload)
        conn.commit()
    return result


@router.get("/exam-papers/json-template")
async def download_exam_json_template(user: dict = Depends(get_current_teacher)):
    """下载原生 JSON 试卷模板。"""
    content = get_exam_json_template_text().encode("utf-8")
    filename = quote("试卷原生JSON导入模板.json")
    return StreamingResponse(
        io.BytesIO(content),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@router.post("/exam-papers/import-json", response_class=JSONResponse)
async def import_exam_paper_json(
    file: UploadFile | None = File(default=None),
    material_id: int | None = Form(default=None),
    user: dict = Depends(get_current_teacher),
):
    """解析原生 JSON 试卷文件，不调用内置 AI。"""
    has_upload = bool(file and str(file.filename or "").strip())
    has_material = material_id is not None
    if has_upload == has_material:
        raise HTTPException(400, "请上传 1 份 JSON 文件，或从本站材料中选择 1 份 JSON，不能同时选择。")

    if has_material:
        with get_db_connection() as conn:
            material = ensure_user_material_access(conn, int(material_id), user)
            if str(material["node_type"] or "") != "file":
                raise HTTPException(400, "只能选择 JSON 文件材料")
            filename = Path(str(material["name"] or material["material_path"] or "exam.json")).name
            file_hash = str(material["file_hash"] or "").strip()
        if Path(filename).suffix.lower() != ".json":
            raise HTTPException(400, "请选择 .json 材料")
        source_path = resolve_global_file_path(file_hash)
        if source_path is None:
            raise HTTPException(404, "材料文件不存在或尚未完成存储")
        if source_path.stat().st_size > EXAM_JSON_MAX_BYTES:
            raise HTTPException(413, "JSON 文件不能超过 2MB")
        raw = source_path.read_bytes()
    else:
        assert file is not None
        filename = Path(str(file.filename or "exam.json")).name
        if Path(filename).suffix.lower() != ".json":
            raise HTTPException(400, "请上传 .json 文件")
        raw = await file.read()

    if not raw:
        raise HTTPException(400, "JSON 文件为空")
    if len(raw) > EXAM_JSON_MAX_BYTES:
        raise HTTPException(413, "JSON 文件不能超过 2MB")

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(400, "JSON 文件必须使用 UTF-8 编码") from exc

    try:
        imported = parse_exam_json_text(text)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    imported["source_filename"] = filename
    return {"status": "success", "imported": imported}


@router.post("/exam-papers", response_class=JSONResponse)
async def create_exam_paper(request: Request, user: dict = Depends(get_current_teacher)):
    """创建新试卷，与 Agent 共用事务内业务服务。"""
    data = await request.json()
    with get_db_connection() as conn:
        result = create_exam_paper_record(conn, teacher_id=int(user["id"]), data=data)
        conn.commit()
    return result


@router.post("/exam-papers/material-reverse", response_class=JSONResponse)
async def reverse_exam_paper_to_material(request: Request, user: dict = Depends(get_current_teacher)):
    """从具体试卷反推考核计划表或评分细则表。"""
    data = await request.json()
    paper_id = str(data.get("paper_id") or "").strip()
    target_type = str(data.get("target_type") or data.get("document_type") or "").strip()
    prompt = str(data.get("prompt") or "").strip()
    if not paper_id:
        raise HTTPException(400, "请选择要反推的试卷")
    if target_type not in {"assessment_plan", "grading_rubric"}:
        raise HTTPException(400, "材料反推类型不受支持")

    with get_db_connection() as conn:
        if target_type == "assessment_plan":
            result = create_assessment_plan_reverse_placeholder(
                conn,
                teacher=user,
                paper_id=paper_id,
                prompt=prompt,
            )
            conn.commit()
            asyncio.create_task(
                run_assessment_plan_reverse_job(
                    str(result["plan_id"]),
                    paper_id,
                    int(user["id"]),
                    prompt,
                )
            )
            return {
                "status": "success",
                "message": "已开始从试卷反推考核计划表，列表中会显示生成进度。",
                **result,
            }

        result = create_grading_rubric_reverse_placeholder(
            conn,
            teacher=user,
            paper_id=paper_id,
            prompt=prompt,
        )
        conn.commit()
        asyncio.create_task(
            run_grading_rubric_reverse_job(
                int(result["record_id"]),
                paper_id,
                int(user["id"]),
                prompt,
            )
        )
        return {
            "status": "success",
            "message": "已开始从试卷反推评分细则表，列表中会显示生成进度。",
            **result,
        }


@router.get(
    "/exam-papers/{paper_id}",
    response_class=JSONResponse,
    response_model=ExamPaperDetailResponse,
    response_model_exclude_unset=True,
)
async def get_exam_paper(paper_id: str, user: dict = Depends(get_current_user)):
    """获取试卷详情"""
    if str(user.get("role") or "").lower() != "teacher":
        raise HTTPException(403, "无权查看此试卷")
    with get_db_connection() as conn:
        result = _get_exam_paper_for_teacher(conn, paper_id, int(user["id"]))
        # 获取已分配的课堂列表
        assignments = conn.execute(
            """SELECT a.id, a.status, a.title, o.id as offering_id, c.name as course_name, cl.name as class_name
               FROM assignments a
               LEFT JOIN class_offerings o ON a.class_offering_id = o.id
               LEFT JOIN courses c ON o.course_id = c.id
               LEFT JOIN classes cl ON o.class_id = cl.id
               WHERE a.exam_paper_id = ?
                 AND NOT EXISTS (
                     SELECT 1 FROM learning_stage_exam_attempts lsea
                     WHERE lsea.assignment_id = a.id
                 )""",
            (paper_id,)
        ).fetchall()
        result['assignments'] = [dict(row) for row in assignments]
        result["is_owned"] = int(result.get("teacher_id") or 0) == int(user["id"])
        result["can_manage"] = teacher_can_manage_exam_paper(conn, int(user["id"]), result)
        result["scope_level"] = _normalize_exam_open_scope(result.get("scope_level"), default=SCOPE_PRIVATE)
        result["scope_label"] = _exam_scope_label(result["scope_level"])
    return {"status": "success", "paper": result}


@router.put("/exam-papers/{paper_id}", response_class=JSONResponse)
async def update_exam_paper(paper_id: str, request: Request, user: dict = Depends(get_current_teacher)):
    """更新试卷"""
    data = await request.json()
    now = datetime.now().isoformat()
    requested_scope = _normalize_exam_open_scope(data.get("scope_level"), default=SCOPE_DEPARTMENT)
    with get_db_connection() as conn:
        lock_exam_paper(conn, paper_id)
        paper = _get_exam_paper_for_teacher(conn, paper_id, int(user["id"]), manage=True)
        try:
            previous_questions = normalize_exam_scoring_payload(json.loads(paper.get("questions_json") or "{}"))
            questions_payload = normalize_exam_scoring_payload(data.get('questions', previous_questions))
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        description = str(data.get('description', paper.get('description')) or '')
        grading_inputs_changed = questions_payload != previous_questions or description != str(paper.get('description') or '')
        if grading_inputs_changed:
            if _count_exam_submissions(conn, paper_id) > 0 or _count_exam_drafts(conn, paper_id) > 0:
                raise HTTPException(409, "试卷已有学生提交或草稿，不能原地修改题目、分值和评分标准；请创建新版本后再编辑。")
            if _count_exam_assignments(conn, paper_id) > 0:
                try:
                    questions_payload = normalize_exam_scoring_payload(questions_payload, require_complete=True)
                    _sync_exam_assignment_content(conn, paper_id=paper_id, title=str(data.get('title') or paper['title']),
                                                  description=description, exam_data=questions_payload)
                except ValueError as exc:
                    raise HTTPException(400, f"试卷已分配到课堂，修改内容前必须补齐评分标准：{exc}") from exc
        owner_scope = load_teacher_org_scope(conn, int(paper.get("teacher_id") or user["id"]))

        conn.execute(
            """UPDATE exam_papers
               SET title = ?, description = ?, questions_json = ?, exam_config_json = ?, status = ?,
                   owner_role = 'teacher',
                   owner_user_pk = ?,
                   scope_level = ?,
                   school_code = ?,
                   school_name = ?,
                   college = ?,
                   department = ?,
                   updated_at = ?
               WHERE id = ?""",
             (data['title'], description,
             json.dumps(questions_payload, ensure_ascii=False),
             json.dumps(data.get('config', {}), ensure_ascii=False),
             data.get('status', 'draft'),
             int(paper.get("teacher_id") or user["id"]),
             requested_scope,
             owner_scope["school_code"],
             owner_scope["school_name"],
             owner_scope["college"],
             owner_scope["department"],
             now, paper_id)
        )
        conn.commit()
    return {"status": "success", "paper_id": paper_id}


@router.delete("/exam-papers/{paper_id}", response_class=JSONResponse)
async def delete_exam_paper(paper_id: str, user: dict = Depends(get_current_teacher)):
    """删除试卷"""
    with get_db_connection() as conn:
        lock_exam_paper(conn, paper_id)
        paper = _get_exam_paper_for_teacher(conn, paper_id, int(user["id"]), manage=True)
        raise_if_delete_blocked(
            f"试卷“{paper['title']}”",
            build_exam_delete_blockers(conn, str(paper_id)),
        )
        conn.execute("DELETE FROM exam_papers WHERE id = ?", (paper_id,))
        conn.commit()
    return {"status": "success"}


@router.post("/exam-papers/{paper_id}/assign", response_class=JSONResponse)
async def assign_exam_paper(paper_id: str, request: Request, user: dict = Depends(get_current_teacher)):
    data = await request.json()
    # Reject missing/invalid formal classification before opening the DB, as before.
    try:
        normalize_assessment_kind(data.get("assessment_kind"))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    with get_db_connection() as conn:
        close_overdue_assignments(conn)
        result = assign_exam_paper_record(conn, paper_id=paper_id, teacher_id=int(user["id"]), data=data)
        conn.commit()
        course_id = conn.execute("SELECT course_id FROM assignments WHERE id=?", (result["assignment_id"],)).fetchone()[0]
        _build_assignment_storage_dir(course_id, result["assignment_id"]).mkdir(parents=True, exist_ok=True)
    return result
