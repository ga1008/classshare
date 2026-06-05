from .common import *
from ...services.base_resource_modes_service import (
    build_exam_delete_blockers,
    ensure_teacher_can_manage_exam_attributes,
    ensure_teacher_can_view_exam_attributes,
    raise_if_delete_blocked,
    serialize_exam_attributes,
    serialize_exam_content,
    update_exam_attributes,
)


router = APIRouter()


def _count_exam_assignments(conn, paper_id: str) -> int:
    row = conn.execute("SELECT COUNT(*) FROM assignments WHERE exam_paper_id = ?", (str(paper_id),)).fetchone()
    return int(row[0] or 0) if row else 0


def _count_exam_submissions(conn, paper_id: str) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM submissions s
        JOIN assignments a ON a.id = s.assignment_id
        WHERE a.exam_paper_id = ?
        """,
        (str(paper_id),),
    ).fetchone()
    return int(row[0] or 0) if row else 0


def _count_exam_drafts(conn, paper_id: str) -> int:
    try:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM submission_drafts sd
            JOIN assignments a ON a.id = sd.assignment_id
            WHERE a.exam_paper_id = ?
            """,
            (str(paper_id),),
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0] or 0) if row else 0


def _sync_exam_assignment_content(conn, *, paper_id: str, title: str, description: str, exam_data: dict[str, Any]) -> int:
    rubric_md = build_exam_rubric_md(
        title=title,
        description=description,
        exam_data=exam_data,
        require_complete=True,
    )
    requirements_md = f"**试卷**: {title}\n\n{description or ''}"
    cursor = conn.execute(
        """
        UPDATE assignments
        SET title = COALESCE(NULLIF(title, ''), ?),
            requirements_md = ?,
            rubric_md = ?
        WHERE exam_paper_id = ?
        """,
        (title, requirements_md, rubric_md, str(paper_id)),
    )
    return int(cursor.rowcount or 0)


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
async def put_exam_paper_content(
    paper_id: str,
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(400, "请求数据格式错误")
    title = str(payload.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "试卷标题不能为空")
    description = str(payload.get("description") or "").strip()
    try:
        questions_payload = normalize_exam_scoring_payload(payload.get("questions", {"pages": []}))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    config_payload = payload.get("config", {})
    if not isinstance(config_payload, dict):
        raise HTTPException(400, "试卷配置必须是对象")

    with get_db_connection() as conn:
        paper = ensure_teacher_can_manage_exam_attributes(conn, paper_id, int(user["id"]))
        submission_count = _count_exam_submissions(conn, paper_id)
        draft_count = _count_exam_drafts(conn, paper_id)
        if submission_count > 0 or draft_count > 0:
            raise HTTPException(
                409,
                "试卷已有学生提交或草稿，不能原地修改题目、分值和评分标准；请创建新版本后再编辑。",
            )
        assignment_count = _count_exam_assignments(conn, paper_id)
        synced_assignment_count = 0
        if assignment_count > 0:
            try:
                complete_questions = normalize_exam_scoring_payload(questions_payload, require_complete=True)
                synced_assignment_count = _sync_exam_assignment_content(
                    conn,
                    paper_id=paper_id,
                    title=title,
                    description=description,
                    exam_data=complete_questions,
                )
                questions_payload = complete_questions
            except ValueError as exc:
                raise HTTPException(
                    400,
                    f"试卷已分配到课堂，修改内容前必须补齐评分标准：{exc}",
                ) from exc
        conn.execute(
            """
            UPDATE exam_papers
            SET title = ?,
                description = ?,
                questions_json = ?,
                exam_config_json = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                title,
                description,
                json.dumps(questions_payload, ensure_ascii=False),
                json.dumps(config_payload, ensure_ascii=False),
                datetime.now().isoformat(),
                str(paper["id"]),
            ),
        )
        conn.commit()
        refreshed = ensure_teacher_can_view_exam_attributes(conn, paper_id, int(user["id"]))
        content = serialize_exam_content(conn, refreshed, int(user["id"]))
    return {
        "status": "success",
        "message": "试卷内容已保存",
        "synced_assignment_count": synced_assignment_count,
        "content": content,
    }


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
    """创建新试卷"""
    data = await request.json()
    paper_id = data.get('id') or str(uuid.uuid4())
    now = datetime.now().isoformat()
    scope_level = _normalize_exam_open_scope(data.get("scope_level"), default=SCOPE_DEPARTMENT)
    try:
        questions_payload = normalize_exam_scoring_payload(data.get('questions', {"pages": []}))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    with get_db_connection() as conn:
        teacher_scope = load_teacher_org_scope(conn, int(user["id"]))
        conn.execute(
            """INSERT INTO exam_papers (
                    id, teacher_id, title, description, questions_json, exam_config_json, status,
                    owner_role, owner_user_pk, scope_level, school_code, school_name, college, department,
                    created_at, updated_at
               )
               VALUES (?, ?, ?, ?, ?, ?, ?, 'teacher', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (paper_id, user['id'], data['title'], data.get('description', ''),
             json.dumps(questions_payload, ensure_ascii=False),
             json.dumps(data.get('config', {}), ensure_ascii=False),
             data.get('status', 'draft'),
             user["id"],
             scope_level,
             teacher_scope["school_code"],
             teacher_scope["school_name"],
             teacher_scope["college"],
             teacher_scope["department"],
             now, now)
        )
        conn.commit()
    return {"status": "success", "paper_id": paper_id}


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
    try:
        questions_payload = normalize_exam_scoring_payload(data.get('questions', {"pages": []}))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    with get_db_connection() as conn:
        paper = _get_exam_paper_for_teacher(conn, paper_id, int(user["id"]), manage=True)
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
            (data['title'], data.get('description', ''),
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
    """将试卷分配给指定课堂（创建 assignment）"""
    data = await request.json()
    class_offering_id = data.get('class_offering_id')
    if not class_offering_id:
        raise HTTPException(400, "请指定课堂")
    learning_stage_key = _get_learning_stage_key(data, class_offering_id=class_offering_id)
    try:
        schedule_fields = build_assignment_schedule_fields(
            data,
            default_status="published",
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    with get_db_connection() as conn:
        close_overdue_assignments(conn)
        paper = _get_exam_paper_for_teacher(conn, paper_id, int(user["id"]))

        # 获取课堂信息
        offering = conn.execute(
            "SELECT * FROM class_offerings WHERE id = ? AND teacher_id = ?",
            (class_offering_id, user['id'])
        ).fetchone()
        if not offering:
            raise HTTPException(404, "课堂不存在或无权操作")

        # 创建作业记录
        existing_assignment = conn.execute(
            "SELECT id FROM assignments WHERE exam_paper_id = ? AND class_offering_id = ?",
            (paper_id, int(class_offering_id))
        ).fetchone()
        if existing_assignment:
            raise HTTPException(409, "该试卷已添加到当前课堂，请勿重复发布")

        created_at = datetime.now().isoformat()
        try:
            paper_questions = json.loads(paper["questions_json"] or "{}")
            if not isinstance(paper_questions, dict):
                paper_questions = {"pages": []}
            paper_questions = normalize_exam_scoring_payload(paper_questions, require_complete=True)
            exam_rubric_md = build_exam_rubric_md(
                title=str(paper["title"] or ""),
                description=str(paper["description"] or ""),
                exam_data=paper_questions,
                require_complete=True,
            )
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            raise HTTPException(
                400,
                f"试卷评分标准不完整，请先回到试卷编辑器补齐标准答案、分值、评分指导和扣分点：{exc}",
            ) from exc

        if teacher_can_manage_exam_paper(conn, int(user["id"]), paper):
            conn.execute(
                "UPDATE exam_papers SET questions_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(paper_questions, ensure_ascii=False), created_at, paper_id),
            )

        allowed_file_types_json = encode_allowed_file_types_json(_get_allowed_file_types(data))
        cursor = conn.execute(
            """
            INSERT INTO assignments (
                course_id, title, status, requirements_md, rubric_md, grading_mode,
                exam_paper_id, class_offering_id, created_at, allowed_file_types_json,
                availability_mode, starts_at, due_at, duration_minutes, auto_close, closed_at,
                late_submission_enabled, late_submission_until, late_penalty_strategy,
                late_penalty_interval_hours, late_penalty_points, late_penalty_min_score, late_score_cap,
                learning_stage_key
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(offering['course_id']),
                data.get('title', paper['title']),
                schedule_fields["status"],
                f"**试卷**: {paper['title']}\n\n{paper['description'] or ''}",
                exam_rubric_md,
                'ai',
                paper_id,
                int(class_offering_id),
                created_at,
                allowed_file_types_json,
                schedule_fields["availability_mode"],
                schedule_fields["starts_at"],
                schedule_fields["due_at"],
                schedule_fields["duration_minutes"],
                schedule_fields["auto_close"],
                schedule_fields["closed_at"],
                schedule_fields["late_submission_enabled"],
                schedule_fields["late_submission_until"],
                schedule_fields["late_penalty_strategy"],
                schedule_fields["late_penalty_interval_hours"],
                schedule_fields["late_penalty_points"],
                schedule_fields["late_penalty_min_score"],
                schedule_fields["late_score_cap"],
                learning_stage_key,
            )
        )
        new_assignment_id = cursor.lastrowid
        if schedule_fields["status"] == "published":
            try:
                create_assignment_published_notifications(
                    conn,
                    new_assignment_id,
                    send_email_notification=_wants_assignment_email_notification(data),
                )
            except Exception as exc:
                print(f"[MESSAGE_CENTER] exam assignment publish notify failed: {exc}")

        # 自动将课堂名称添加为试卷标签
        _auto_add_class_name_tag(conn, paper, offering['class_id'])

        conn.commit()
        assignment_dir = _build_assignment_storage_dir(offering['course_id'], new_assignment_id)
        assignment_dir.mkdir(parents=True, exist_ok=True)
    return {
        "status": "success",
        "assignment_id": new_assignment_id,
        "assignment_status": schedule_fields["status"],
        "due_at": schedule_fields["due_at"],
        "message": "试卷已成功发布到当前课堂"
    }
