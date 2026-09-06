from .common import *
from .generation_helpers import *
from .ai_import_helpers import *
from .final_material_helpers import *
from .rewrite_helpers import *
from ..ui_parts.common import _build_manage_template_context
from ...db.connection import begin_immediate_transaction, get_configured_db_engine
from ...services.base_resource_modes_service import (
    build_mode_permissions,
)
from ...services.material_delete_service import build_material_delete_impact, unlink_material_delete_references
from ...services.ordinary_grade_record_service import classify_ordinary_grade_assignment


router = APIRouter()

# zip 自动解压护栏：压缩包 ≤200MB，解压后总量 ≤500MB、单文件 ≤100MB、条目 ≤3000。
ZIP_UPLOAD_MAX_COMPRESSED_BYTES = 200 * 1024 * 1024
ZIP_UPLOAD_MAX_TOTAL_BYTES = 500 * 1024 * 1024
ZIP_UPLOAD_MAX_FILE_BYTES = 100 * 1024 * 1024
ZIP_UPLOAD_MAX_ENTRY_COUNT = 3000
_ZIP_IGNORED_SEGMENTS = {"__macosx", ".ds_store", ".git", "thumbs.db"}


def _decode_zip_member_name(info: zipfile.ZipInfo) -> str:
    """zip 条目名编码兜底：无 UTF-8 标记时按 GBK 还原中文文件名。"""
    name = info.filename
    if info.flag_bits & 0x800:
        return name
    try:
        return name.encode("cp437").decode("gbk")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def _extract_zip_upload_entries(zip_name: str, payload: bytes) -> list[dict]:
    """把上传的 zip 解压成上传条目列表（内存解压 + 路径/体积护栏）。

    返回 [{"relative_path": str, "payload": bytes, "content_type": None}]。
    """
    if len(payload) > ZIP_UPLOAD_MAX_COMPRESSED_BYTES:
        raise HTTPException(400, f"压缩包 {zip_name} 超过 {ZIP_UPLOAD_MAX_COMPRESSED_BYTES // (1024 * 1024)}MB 限制")

    import io

    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile:
        raise HTTPException(400, f"压缩包 {zip_name} 无法解析，请确认是有效的 .zip 文件")

    stem = Path(zip_name).stem.strip() or "archive"
    raw_entries: list[tuple[str, zipfile.ZipInfo]] = []
    total_bytes = 0
    with archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            member_name = _decode_zip_member_name(info).replace("\\", "/").strip("/")
            if not member_name:
                continue
            segments = [seg for seg in member_name.split("/") if seg and seg != "."]
            if not segments:
                continue
            if any(seg == ".." for seg in segments):
                raise HTTPException(400, f"压缩包 {zip_name} 含有非法路径（..）")
            if any(seg.lower() in _ZIP_IGNORED_SEGMENTS for seg in segments):
                continue
            if re.match(r"^[a-zA-Z]:", segments[0]):
                raise HTTPException(400, f"压缩包 {zip_name} 含有非法路径（盘符）")
            if info.file_size > ZIP_UPLOAD_MAX_FILE_BYTES:
                raise HTTPException(400, f"压缩包内文件 {member_name} 超过单文件大小限制")
            total_bytes += int(info.file_size)
            if total_bytes > ZIP_UPLOAD_MAX_TOTAL_BYTES:
                raise HTTPException(400, f"压缩包 {zip_name} 解压后总体积超过限制")
            raw_entries.append(("/".join(segments), info))
            if len(raw_entries) > ZIP_UPLOAD_MAX_ENTRY_COUNT:
                raise HTTPException(400, f"压缩包 {zip_name} 文件数量超过 {ZIP_UPLOAD_MAX_ENTRY_COUNT} 限制")

        if not raw_entries:
            raise HTTPException(400, f"压缩包 {zip_name} 中没有可导入的文件")

        # 若 zip 内已有唯一顶层目录则直接使用；否则用压缩包名做顶层目录。
        top_levels = {path.split("/", 1)[0] for path, _info in raw_entries}
        wrap_with_stem = len(top_levels) != 1

        entries: list[dict] = []
        for member_path, info in raw_entries:
            relative_path = f"{stem}/{member_path}" if wrap_with_stem else member_path
            with archive.open(info) as handle:
                data = handle.read(ZIP_UPLOAD_MAX_FILE_BYTES + 1)
            if len(data) > ZIP_UPLOAD_MAX_FILE_BYTES:
                raise HTTPException(400, f"压缩包内文件 {member_path} 超过单文件大小限制")
            entries.append({"relative_path": relative_path, "payload": data, "content_type": None})
    return entries


def _save_payload_bytes_globally(payload: bytes) -> dict:
    """按内容哈希落盘（与 save_file_globally 同一存储布局），返回 {hash, size}。"""
    file_hash = hashlib.sha256(payload).hexdigest()
    target_path = global_file_write_path(file_hash)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if not target_path.exists():
        existing = resolve_global_file_path(file_hash)
        if existing and existing != target_path:
            target_path.write_bytes(existing.read_bytes())
        else:
            target_path.write_bytes(payload)
    return {"hash": file_hash, "size": target_path.stat().st_size}


def _serialize_material_attributes(conn, material, user: dict) -> dict[str, Any]:
    child_count = conn.execute(
        "SELECT COUNT(*) FROM course_materials WHERE parent_id = ? AND name != '.git'",
        (int(material["id"]),),
    ).fetchone()[0]
    assignment_count = conn.execute(
        "SELECT COUNT(*) FROM course_material_assignments WHERE material_id = ?",
        (int(material["id"]),),
    ).fetchone()[0]
    item = serialize_material_row(
        material,
        {
            "child_count": int(child_count or 0),
            "assignment_count": int(assignment_count or 0),
            "breadcrumbs": get_material_breadcrumbs(conn, int(material["id"])),
        },
    )
    item = _decorate_material_ownership(conn, item, user)
    item = attach_git_repository_metadata(conn, [item])[0]
    can_manage = bool(item.get("can_manage"))
    item["permissions"] = build_mode_permissions(can_use=True, can_manage=can_manage)
    ai_import_record = _find_material_ai_import_record(
        conn,
        int(material["id"]),
        int(material["teacher_id"]),
        completed_only=True,
    )
    if ai_import_record:
        preview = _build_ai_import_preview(ai_import_record, content_limit=0)
        fields = preview.get("fields") if isinstance(preview.get("fields"), dict) else {}
        structured = preview.get("structured") if isinstance(preview.get("structured"), dict) else {}
        item["document_type_label"] = preview.get("document_type_label") or ""
        for key in (
            "academic_year",
            "semester",
            "course_name",
            "class_name",
            "teacher_name",
            "class_size",
            "course_hours",
            "credits",
            "export_filename",
        ):
            item[key] = fields.get(key) if fields.get(key) not in (None, "") else ""
        if preview.get("document_type") == "ordinary_grade_record":
            item["academic_year"] = item.get("academic_year") or "未设置"
            item["semester"] = item.get("semester") or "未设置"
        if not item.get("class_size"):
            students = structured.get("students") if isinstance(structured.get("students"), list) else []
            item["class_size"] = len(students) if students else ""
    return item


def _rename_material_subtree(conn, material, new_name: str) -> None:
    normalized_name = normalize_material_path(new_name, fallback_name=str(material["name"] or "material"))
    if "/" in normalized_name or normalized_name in {"", ".git"}:
        raise HTTPException(400, "材料名称不合法")
    parent_id = material["parent_id"]
    if parent_id is None:
        conflict = conn.execute(
            """
            SELECT id
            FROM course_materials
            WHERE parent_id IS NULL
              AND teacher_id = ?
              AND LOWER(name) = LOWER(?)
              AND id != ?
            LIMIT 1
            """,
            (int(material["teacher_id"]), normalized_name, int(material["id"])),
        ).fetchone()
    else:
        conflict = conn.execute(
            """
            SELECT id
            FROM course_materials
            WHERE parent_id = ?
              AND LOWER(name) = LOWER(?)
              AND id != ?
            LIMIT 1
            """,
            (int(parent_id), normalized_name, int(material["id"])),
        ).fetchone()
    if conflict:
        raise HTTPException(409, "同一目录下已有同名材料")

    old_path = str(material["material_path"] or "").strip()
    if not old_path:
        raise HTTPException(400, "材料路径异常，不能重命名")
    prefix = old_path.rsplit("/", 1)[0] if "/" in old_path else ""
    new_path = f"{prefix}/{normalized_name}" if prefix else normalized_name
    subtree = _collect_subtree_rows(conn, material)
    now_text = datetime.now().isoformat()
    for row in subtree:
        row_path = str(row["material_path"] or "")
        suffix = row_path[len(old_path):] if row_path.startswith(old_path) else ""
        conn.execute(
            """
            UPDATE course_materials
            SET name = CASE WHEN id = ? THEN ? ELSE name END,
                material_path = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (int(material["id"]), normalized_name, f"{new_path}{suffix}", now_text, int(row["id"])),
        )


def _build_material_type_registry() -> list[dict[str, Any]]:
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
    return type_registry


GRADE_RECORD_IMPORT_PRESETS: dict[str, dict[str, Any]] = {
    "ordinary_grade_record": {
        "document_group": "final_material",
        "document_type": "ordinary_grade_record",
        "status": "可上传平时成绩记录表 Excel 进行结构化解析；如需从课堂数据生成，可在当前列表选择课堂、3 份作业和 1 份测评。",
    },
    "exam_grade_record": {
        "document_group": "final_material",
        "document_type": "exam_grade_record",
        "status": "可上传考核登分表 Excel 进行结构化解析；如需从课堂考试成绩生成，可在当前列表选择课堂和已绑定试卷的考试。",
    },
    "final_grade_transcript": {
        "document_group": "final_material",
        "document_type": "final_grade_transcript",
        "status": "上传教务学生成绩录入模板前需确认关联课堂、学年和学期；系统会自动补全组织与课程信息并逐行保存。",
    },
}


GRADE_RECORD_GENERATE_BLOCKERS: dict[str, dict[str, Any]] = {
    "ordinary_grade_record": {
        "document_group": "final_material",
        "document_type": "ordinary_grade_record",
        "blocked": True,
        "status": "平时成绩表必须基于真实课堂考勤、3 份作业和 1 份测评生成，不能在材料库中泛化生成；本页支持上传 Excel 后解析入库并导出 Excel。",
    },
    "exam_grade_record": {
        "document_group": "final_material",
        "document_type": "exam_grade_record",
        "blocked": True,
        "status": "考核登分表必须基于具体课堂考试成绩生成，不能在材料库中泛化生成；本页支持上传 Excel 后解析入库并导出 Excel。",
    },
    "final_grade_transcript": {
        "document_group": "final_material",
        "document_type": "final_grade_transcript",
        "blocked": True,
        "status": "期末成绩单必须先同步教务考试名单，再关联同课堂、同学年学期的平时成绩表与考核登分表；不使用本地名单或模糊姓名推断，最终仅生成 1 份学校模板 Excel。",
    },
}


def _render_manage_materials_page(
    request: Request,
    user: dict,
    *,
    page_title: str = "课程材料",
    active_page: str = "materials",
    page_heading: str = "课程资料与 Git 教学仓库",
    page_lead: str = "批量上传、目录浏览、模糊搜索与排序，保留课堂分配、AI 解析与仓库同步。",
    initial_ai_generate: dict[str, Any] | None = None,
    initial_ai_import: dict[str, Any] | None = None,
    initial_library_filter: dict[str, Any] | None = None,
    library_view: str = "",
):
    normalized_library_view = _normalize_material_library_view(library_view)
    if normalized_library_view:
        initial_library_filter = {**(initial_library_filter or {}), "library_view": normalized_library_view}
    with get_db_connection() as conn:
        offerings = conn.execute(
            """
            SELECT o.id,
                   o.semester_id,
                   COALESCE(s.name, o.semester) AS semester,
                   COALESCE(s.start_date, '') AS semester_start_date,
                   COALESCE(s.end_date, '') AS semester_end_date,
                   c.name AS class_name,
                   co.name AS course_name,
                   COALESCE(co.school_name, c.school_name, '广西外国语学院') AS school_name,
                   COALESCE(co.college, c.college, '') AS college,
                   COALESCE(co.department, c.department, '') AS department,
                   COALESCE((
                       SELECT sync.course_nature
                       FROM teacher_academic_course_sync_items sync
                       WHERE sync.teacher_id = o.teacher_id
                         AND (sync.course_id = o.course_id OR TRIM(sync.course_name) = TRIM(co.name))
                         AND (o.semester_id IS NULL OR sync.semester_id = o.semester_id OR sync.semester_id IS NULL)
                       ORDER BY sync.synced_at DESC, sync.id DESC
                       LIMIT 1
                   ), '') AS course_nature,
                   COALESCE((
                       SELECT sync.academic_year_name
                       FROM teacher_academic_course_sync_items sync
                       WHERE sync.teacher_id = o.teacher_id
                         AND (sync.course_id = o.course_id OR TRIM(sync.course_name) = TRIM(co.name))
                         AND (o.semester_id IS NULL OR sync.semester_id = o.semester_id OR sync.semester_id IS NULL)
                       ORDER BY sync.synced_at DESC, sync.id DESC
                       LIMIT 1
                   ), '') AS academic_year_name,
                   COALESCE((
                       SELECT sync.academic_term_name
                       FROM teacher_academic_course_sync_items sync
                       WHERE sync.teacher_id = o.teacher_id
                         AND (sync.course_id = o.course_id OR TRIM(sync.course_name) = TRIM(co.name))
                         AND (o.semester_id IS NULL OR sync.semester_id = o.semester_id OR sync.semester_id IS NULL)
                       ORDER BY sync.synced_at DESC, sync.id DESC
                       LIMIT 1
                   ), '') AS academic_term_name
            FROM class_offerings o
            JOIN classes c ON o.class_id = c.id
            JOIN courses co ON o.course_id = co.id
            LEFT JOIN academic_semesters s ON s.id = o.semester_id
            WHERE o.teacher_id = ?
            ORDER BY COALESCE(s.start_date, o.created_at) DESC, co.name, c.name
            """,
            (user["id"],),
        ).fetchall()
        ordinary_source_rows = conn.execute(
            """
            SELECT a.class_offering_id,
                   a.title,
                   a.exam_paper_id,
                   a.ordinary_grade_kind_override,
                   a.ordinary_grade_kind_updated_at,
                   a.ordinary_grade_kind_updated_by_teacher_id
            FROM assignments a
            JOIN class_offerings o ON o.id = a.class_offering_id
            WHERE o.teacher_id = ?
            """,
            (user["id"],),
        ).fetchall()
        stats = _get_teacher_material_stats(
            conn,
            user["id"],
            document_type=str((initial_library_filter or {}).get("document_type") or ""),
            library_view=normalized_library_view,
        )

    source_counts: dict[int, dict[str, int]] = {}
    for row in ordinary_source_rows:
        item = dict(row)
        offering_id = int(item["class_offering_id"])
        bucket = source_counts.setdefault(offering_id, {"assignment": 0, "exam": 0})
        kind = classify_ordinary_grade_assignment(item)
        bucket[kind] = bucket.get(kind, 0) + 1
    offering_items = []
    for row in offerings:
        item = dict(row)
        counts = source_counts.get(int(item["id"]), {"assignment": 0, "exam": 0})
        item["ordinary_homework_count"] = int(counts.get("assignment") or 0)
        item["ordinary_assessment_count"] = int(counts.get("exam") or 0)
        item["ordinary_grade_ready"] = (
            item["ordinary_homework_count"] >= 3 and item["ordinary_assessment_count"] >= 1
        )
        offering_items.append(item)

    return templates.TemplateResponse(
        request,
        "manage/materials.html",
        _build_manage_template_context(
            request,
            user,
            page_title=page_title,
            active_page=active_page,
            extra={
                "offerings": offering_items,
                "material_stats": stats,
                "type_registry": _build_material_type_registry(),
                "material_ai_import_registry": get_material_ai_import_registry(),
                "materials_page_heading": page_heading,
                "materials_page_lead": page_lead,
                "initial_ai_generate": initial_ai_generate or {},
                "initial_ai_import": initial_ai_import or {},
                "initial_library_filter": initial_library_filter or {},
            },
        ),
    )


@router.get("/manage/teaching/materials", response_class=HTMLResponse)
@router.get("/manage/materials", response_class=HTMLResponse)
async def manage_materials_page(request: Request, user: dict = Depends(get_current_teacher)):
    # 内容资产 → 材料：只保留上课使用的学习文档；课后材料在过程材料的「课后材料」页。
    return _render_manage_materials_page(
        request,
        user,
        page_heading="学习文档与 Git 教学仓库",
        page_lead="批量上传、目录浏览、模糊搜索与排序，保留课堂分配、AI 解析与仓库同步；课堂生成与上传解析的材料已移至「课后材料」。",
        library_view="learning",
    )


@router.get("/manage/teaching/postclass-materials", response_class=HTMLResponse)
async def manage_postclass_materials_page(request: Request, user: dict = Depends(get_current_teacher)):
    """过程材料 → 课后材料：课堂生成 + 上传解析（AI解析/导入）材料包的统一入口。"""
    return _render_manage_materials_page(
        request,
        user,
        page_title="课后材料",
        active_page="postclass_materials",
        page_heading="课后材料",
        page_lead="集中管理课堂结束后生成的材料与上传解析（AI解析/导入）的材料包；上课用的学习文档请到内容资产的「材料」。",
        library_view="postclass",
    )


@router.get("/manage/teaching/grading-rubrics", response_class=HTMLResponse)
async def manage_grading_rubrics_page(request: Request, user: dict = Depends(get_current_teacher)):
    return _render_manage_materials_page(
        request,
        user,
        page_title="评分细则表",
        active_page="grading_rubrics",
        page_heading="评分细则表",
        page_lead="关联具体试卷或题目附件，按试题顺序生成评分标准、扣分项、例外情况和截图/提交物要求。",
        initial_library_filter={"document_type": "grading_rubric"},
        initial_ai_generate={
            "document_group": "final_material",
            "document_type": "grading_rubric",
            "status": "请关联具体试卷或上传试卷题目后生成评分细则，系统会按试题逐项拆分给分点和扣分项。",
        },
    )


@router.get("/manage/teaching/ordinary-grade-records", response_class=HTMLResponse)
async def manage_ordinary_grade_records_page(request: Request, user: dict = Depends(get_current_teacher)):
    return _render_manage_materials_page(
        request,
        user,
        page_title="平时成绩表",
        active_page="ordinary_grade_records",
        page_heading="平时成绩表",
        page_lead="管理由课堂考勤、作业与测评生成，或从 Excel 导入解析的学生平时成绩记录表；可设置私有、系部、院级、校级公开并导出 Excel。",
        initial_library_filter={"document_type": "ordinary_grade_record"},
        initial_ai_import=GRADE_RECORD_IMPORT_PRESETS["ordinary_grade_record"],
        initial_ai_generate=GRADE_RECORD_GENERATE_BLOCKERS["ordinary_grade_record"],
    )


@router.get("/manage/teaching/exam-grade-records", response_class=HTMLResponse)
async def manage_exam_grade_records_page(request: Request, user: dict = Depends(get_current_teacher)):
    return _render_manage_materials_page(
        request,
        user,
        page_title="考核登分表",
        active_page="exam_grade_records",
        page_heading="考核登分表",
        page_lead="管理由课堂考试生成，或从 Excel 导入解析的期末考核登分表；保留大题列、总分校验、开放范围与 Excel 导出。",
        initial_library_filter={"document_type": "exam_grade_record"},
        initial_ai_import=GRADE_RECORD_IMPORT_PRESETS["exam_grade_record"],
        initial_ai_generate=GRADE_RECORD_GENERATE_BLOCKERS["exam_grade_record"],
    )


@router.get("/manage/teaching/final-grade-transcripts", response_class=HTMLResponse)
async def manage_final_grade_transcripts_page(request: Request, user: dict = Depends(get_current_teacher)):
    return _render_manage_materials_page(
        request,
        user,
        page_title="期末成绩单",
        active_page="final_grade_transcripts",
        page_heading="期末成绩单",
        page_lead="即时同步教务考试名单，按原始顺序逐人核对平时成绩表与考核登分表；支持导入教务模板、结构化解析、渲染预览与像素级 Excel 导出。",
        initial_library_filter={"document_type": "final_grade_transcript"},
        initial_ai_import=GRADE_RECORD_IMPORT_PRESETS["final_grade_transcript"],
        initial_ai_generate=GRADE_RECORD_GENERATE_BLOCKERS["final_grade_transcript"],
    )


@router.get(
    "/api/materials/library",
    response_class=JSONResponse,
    response_model=MaterialLibraryResponse,
    response_model_exclude_unset=True,
)
async def get_teacher_material_library(
    parent_id: int | None = Query(default=None),
    keyword: str = Query(default=""),
    document_type: str = Query(default=""),
    library_view: str = Query(default=""),
    scope_level: str = Query(default="all"),
    school: str = Query(default=""),
    department: str = Query(default=""),
    college: str = Query(default=""),
    course: str = Query(default=""),
    class_name: str = Query(default=""),
    sort_by: str = Query(default=MATERIAL_LIBRARY_DEFAULT_SORT_BY),
    sort_order: str = Query(default=MATERIAL_LIBRARY_DEFAULT_SORT_ORDER),
    user: dict = Depends(get_current_teacher),
):
    normalized_keyword = _normalize_material_keyword(keyword)
    normalized_document_type_filter = _normalize_material_document_type_filter(document_type)
    normalized_library_view = _normalize_material_library_view(library_view)
    normalized_scope_filter = _normalize_material_scope_filter(scope_level)
    normalized_school_filter = _normalize_material_org_filter(school)
    normalized_department_filter = _normalize_material_org_filter(department)
    normalized_college_filter = _normalize_material_org_filter(college)
    normalized_course_filter = _normalize_material_org_filter(course)
    normalized_class_filter = _normalize_material_org_filter(class_name)
    normalized_sort_by, normalized_sort_order = _normalize_material_sort(sort_by, sort_order)

    with get_db_connection() as conn:
        current_folder = None
        breadcrumbs = []
        if parent_id is not None:
            current_folder = ensure_user_material_access(conn, parent_id, user)
            if current_folder["node_type"] != "folder":
                raise HTTPException(400, "只能打开文件夹")
            breadcrumbs = get_material_breadcrumbs(conn, parent_id)

        all_rows = _list_material_rows_for_parent(
            conn,
            user["id"],
            current_folder,
            keyword=normalized_keyword,
            document_type=normalized_document_type_filter,
            sort_by=normalized_sort_by,
            sort_order=normalized_sort_order,
            library_view=normalized_library_view,
        )
        facets = _build_material_filter_facets(all_rows, int(user["id"]))
        rows = _apply_material_library_filters(
            all_rows,
            teacher_id=int(user["id"]),
            scope_filter=normalized_scope_filter,
            school=normalized_school_filter,
            department=normalized_department_filter,
            college=normalized_college_filter,
            course=normalized_course_filter,
            class_name=normalized_class_filter,
        )
        items = [_decorate_learning_document_item(item) for item in _serialize_material_items(conn, rows, user=user)]
        current_folder_item = None
        if current_folder:
            current_folder_item = attach_git_repository_metadata(
                conn,
                [_decorate_material_ownership(conn, serialize_material_row(current_folder), user)],
            )[0]
        stats = _get_teacher_material_stats(
            conn,
            user["id"],
            document_type=normalized_document_type_filter,
            library_view=normalized_library_view,
        )
        overview = _build_teacher_library_overview(
            current_folder,
            normalized_keyword,
            normalized_sort_by,
            normalized_sort_order,
            len(items),
            document_type=normalized_document_type_filter,
        )

    return {
        "status": "success",
        "current_folder": current_folder_item,
        "breadcrumbs": breadcrumbs,
        "items": items,
        "stats": stats,
        "filters": {
            "keyword": normalized_keyword,
            "document_type": normalized_document_type_filter,
            "library_view": normalized_library_view,
            "scope_level": normalized_scope_filter,
            "school": normalized_school_filter,
            "department": normalized_department_filter,
            "college": normalized_college_filter,
            "course": normalized_course_filter,
            "class_name": normalized_class_filter,
            "sort_by": normalized_sort_by,
            "sort_order": normalized_sort_order,
        },
        "facets": facets,
        "overview": overview,
    }


@router.get(
    "/api/materials/{material_id}",
    response_class=JSONResponse,
    response_model=MaterialDetailResponse,
    response_model_exclude_unset=True,
)
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
        detail = attach_render_metadata(conn, [detail])[0]
        if material["node_type"] == "folder":
            detail = attach_learning_document_metadata(conn, [detail])[0]
            detail = _decorate_learning_document_item(detail)
        detail = _decorate_material_download_policy(detail)
        ai_import_record = _find_material_ai_import_record(conn, material_id, material["teacher_id"])
        if ai_import_record:
            detail["ai_import_record"] = _build_ai_import_record_detail_payload(ai_import_record)
            detail["ai_import_record"]["preview_url"] = f"/api/materials/{material_id}/ai-import/preview"
        else:
            detail["ai_import_record"] = None

    return {"status": "success", "material": detail}


@router.get("/api/materials/{material_id}/attributes", response_class=JSONResponse)
async def get_material_attributes(material_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        material = ensure_user_material_access(conn, material_id, user)
        attributes = _serialize_material_attributes(conn, material, user)
    return {"status": "success", "resource_type": "material", "attributes": attributes}


@router.patch("/api/materials/{material_id}/attributes", response_class=JSONResponse)
async def patch_material_attributes(
    material_id: int,
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(400, "请求数据格式错误")
    normalized_scope = None
    if "scope_level" in payload:
        normalized_scope = str(payload.get("scope_level") or "private").strip().lower()
        if normalized_scope not in {"private", "school", "college", "department", "public"}:
            raise HTTPException(400, "Invalid material scope")

    with get_db_connection() as conn:
        material = ensure_teacher_material_owner(conn, material_id, user["id"])
        if "name" in payload:
            _rename_material_subtree(conn, material, str(payload.get("name") or ""))
            material = ensure_teacher_material_owner(conn, material_id, user["id"])
        if normalized_scope is not None and material["parent_id"] is not None:
            raise HTTPException(400, "开放范围由最外层文件夹统一决定，请在最外层文件夹上设置")
        if normalized_scope is not None:
            owner_scope = load_teacher_org_scope(conn, int(material["teacher_id"]))
            now_text = datetime.now().isoformat()
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
        attributes = _serialize_material_attributes(conn, refreshed, user)
    return {"status": "success", "message": "材料属性已保存", "attributes": attributes}


@router.get(
    "/api/materials/{material_id}/repository",
    response_class=JSONResponse,
    response_model=MaterialRepositoryResponse,
    response_model_exclude_unset=True,
)
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
    extracted_zip_count = 0
    for index, file in enumerate(files):
        manifest_item = manifest_items[index] if index < len(manifest_items) else {}
        raw_path = manifest_item.get("relative_path") or file.filename
        normalized_path = normalize_material_path(raw_path, fallback_name=file.filename or f"file-{index + 1}")

        # 顶层上传的 .zip 自动解压导入（文件夹上传里的 zip 保持原样存档）。
        if "/" not in normalized_path and normalized_path.lower().endswith(".zip"):
            payload = await file.read()
            zip_entries = _extract_zip_upload_entries(normalized_path, payload)
            extracted_zip_count += 1
            for zip_entry in zip_entries:
                prepared_entries.append(
                    {
                        "file": None,
                        "payload": zip_entry["payload"],
                        "relative_path": normalize_material_path(zip_entry["relative_path"]),
                        "content_type": None,
                    }
                )
            continue

        prepared_entries.append(
            {
                "file": file,
                "relative_path": normalized_path,
                "content_type": manifest_item.get("content_type") or file.content_type,
            }
        )

    if not prepared_entries:
        raise HTTPException(400, "没有可导入的材料")

    with get_db_connection() as conn:
        base_parent = None
        base_prefix = ""
        base_root_id = None
        inherited_scope = "private"
        if parent_id is not None:
            base_parent = ensure_teacher_material_owner(conn, parent_id, user["id"])
            if base_parent["node_type"] != "folder":
                raise HTTPException(400, "只能上传到文件夹中")
            base_prefix = str(base_parent["material_path"])
            base_root_id = int(base_parent["root_id"])
            inherited_scope = str(base_parent["scope_level"] or "private")

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

                folder_id, actual_root_id = _insert_material_folder_row(
                    conn,
                    user=user,
                    name=folder_name,
                    material_path=folder_path,
                    parent_id=folder_parent_id,
                    inherited_root_id=inherited_root_id,
                    owner_scope=owner_scope,
                    now=now,
                    scope_level=inherited_scope,
                )

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

            file_profile = infer_material_profile(
                (file.filename if file else "") or full_segments[-1],
                entry["content_type"],
            )
            if file is not None:
                file_info = await save_file_globally(file)
            else:
                file_info = await asyncio.to_thread(_save_payload_bytes_globally, entry["payload"])
            if not file_info:
                raise HTTPException(500, f"保存材料失败: {full_segments[-1]}")

            file_id = _insert_material_file_row(
                conn,
                user=user,
                name=full_segments[-1],
                material_path=full_path,
                parent_id=file_parent_id,
                root_id=inherited_root_id,
                file_profile=file_profile,
                file_hash=file_info["hash"],
                file_size=file_info["size"],
                owner_scope=owner_scope,
                now=now,
                scope_level=inherited_scope,
            )
            actual_root_id = inherited_root_id or file_id

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

    message = f"已导入 {uploaded_file_count} 个文件"
    if extracted_zip_count:
        message += f"（含 {extracted_zip_count} 个压缩包自动解压）"
    return {
        "status": "success",
        "message": message,
        "uploaded_file_count": uploaded_file_count,
        "uploaded_folder_count": uploaded_folder_count,
        "extracted_zip_count": extracted_zip_count,
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
            if get_configured_db_engine() == "postgres":
                conn.execute(
                    """
                    INSERT INTO course_material_assignments (material_id, class_offering_id, assigned_by_teacher_id, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT (material_id, class_offering_id) DO NOTHING
                    """,
                    (material_id, class_offering_id, user["id"], now),
                )
            else:
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


@router.patch("/api/materials/{material_id}/scope", response_class=JSONResponse)
async def update_material_scope(
    material_id: int,
    payload: MaterialScopeUpdateRequest,
    user: dict = Depends(get_current_teacher),
):
    normalized_scope = str(payload.scope_level or "private").strip().lower()
    if normalized_scope not in {"private", "school", "college", "department", "public"}:
        raise HTTPException(400, "Invalid material scope")
    now_text = datetime.now().isoformat()
    with get_db_connection() as conn:
        material = ensure_teacher_material_owner(conn, material_id, user["id"])
        if material["parent_id"] is not None:
            raise HTTPException(400, "开放范围由最外层文件夹统一决定，请在最外层文件夹上设置")
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


@router.get("/api/materials/{material_id}/delete-impact", response_class=JSONResponse)
def get_material_delete_impact(material_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        material = ensure_teacher_material_owner(conn, material_id, user["id"])
        impact = build_material_delete_impact(conn, material)
    return {"status": "success", "impact": impact}


@router.delete("/api/materials/{material_id}", response_class=JSONResponse)
async def delete_material(
    material_id: int,
    unlink_references: bool = Query(False),
    impact_token: str = Query("", max_length=64),
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        begin_immediate_transaction(conn)
        material = ensure_teacher_material_owner(conn, material_id, user["id"])
        if get_configured_db_engine() == "postgres":
            conn.execute(
                """
                SELECT id
                FROM course_materials
                WHERE root_id = ?
                  AND (material_path = ? OR material_path LIKE ?)
                FOR UPDATE
                """,
                (
                    int(material["root_id"]),
                    material["material_path"],
                    f"{material['material_path']}/%",
                ),
            ).fetchall()

        impact = build_material_delete_impact(conn, material)
        if impact["total_reference_count"] and not unlink_references:
            raise HTTPException(
                409,
                detail={
                    "code": "material_delete_references",
                    "message": (
                        f"材料“{material['name']}”仍有 {impact['total_reference_count']} 项关联，"
                        "请查看关联数据后选择“解除关联并删除”。"
                    ),
                    "impact": impact,
                },
            )

        unlinked_impact = None
        if unlink_references:
            if not impact_token or impact_token != impact["impact_token"]:
                raise HTTPException(
                    409,
                    detail={
                        "code": "material_delete_impact_changed",
                        "message": "材料关联情况已变化，请重新查看并确认后再删除。",
                        "impact": impact,
                    },
                )
            unlinked_impact = unlink_material_delete_references(conn, material, impact=impact)
            remaining_impact = build_material_delete_impact(conn, material, include_items=False)
            if remaining_impact["total_reference_count"]:
                raise HTTPException(
                    409,
                    detail={
                        "code": "material_delete_unlink_incomplete",
                        "message": "部分材料关联未能安全解除，删除已取消，请刷新后重试。",
                        "impact": build_material_delete_impact(conn, material),
                    },
                )

        subtree_rows = _collect_subtree_rows(conn, material)
        file_hashes = {row["file_hash"] for row in subtree_rows if row["node_type"] == "file" and row["file_hash"]}
        # LessonDoc 学习文档包:包根被删时把 pack 登记置 archived(留审计,不删行)。
        try:
            from ...services.lessondoc.pack_service import archive_pack_for_material

            archive_pack_for_material(conn, material_id)
        except Exception:
            pass
        conn.execute("DELETE FROM course_materials WHERE id = ?", (material_id,))
        conn.commit()

        removed_files = 0
        for file_hash in file_hashes:
            if _count_global_file_references(conn, file_hash) <= 0:
                if await delete_global_file(file_hash, conn=conn):
                    removed_files += 1

    return {
        "status": "success",
        "message": f"《{material['name']}》已删除",
        "removed_file_count": removed_files,
        "unlinked_reference_count": int((unlinked_impact or {}).get("total_reference_count") or 0),
        "deleted_learning_progress_count": int((unlinked_impact or {}).get("destructive_reference_count") or 0),
    }


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
        strictness=payload.strictness,
        class_offering_id=payload.class_offering_id,
    )


@router.get("/api/materials/{material_id}/content", response_class=JSONResponse)
async def get_material_content(material_id: int, user: dict = Depends(get_current_teacher)):
    from ...services.lessondoc import source_edit
    with get_db_connection() as conn:
        material = ensure_user_material_access(conn, material_id, user)
        if not is_editable_material(material):
            raise HTTPException(400, "当前仅支持编辑文本类材料")
        registered = source_edit.context(conn, material)

    content, encoding = await _load_material_text_content(material, prefer_optimized=False)
    return {
        "status": "success",
        "material": {
            "id": material["id"],
            "name": material["name"],
            "preview_type": material["preview_type"],
            "updated_at": material["updated_at"],
            "revision": registered["revision"] if registered else material["file_hash"],
            "source_revision": material["file_hash"],
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
    from ...services.lessondoc import editor_service, pack_service, source_edit
    normalized_encoding = str(payload.encoding or "utf-8").strip().lower()
    if normalized_encoding not in TEXT_CONTENT_ENCODINGS:
        raise HTTPException(400, "当前文本编码暂不支持保存")

    # Validation/rendering of a whole LessonDoc can be substantial; keep it off
    # the async event loop and open the transaction in that same worker.
    document_result = await asyncio.to_thread(_save_registered_document_source, material_id, payload, user)
    if document_result is not None:
        return document_result
    with get_db_connection() as conn:
        material = ensure_teacher_material_owner(conn, material_id, user["id"])
        if not is_editable_material(material):
            raise HTTPException(400, "当前仅支持编辑文本类材料")

        registration = source_edit.context(conn, material)
        if registration and registration["lesson_no"] is not None:
            raise HTTPException(409, "材料结构已更新，请重新打开源码")
        try:
            saved = source_edit.save(conn, material=material, teacher_id=user["id"], content=payload.content, revision=payload.revision,
                                     source_revision=payload.source_revision, operation_id=payload.operation_id)
        except (editor_service.EditorError, pack_service.LessonDocWriteConflict) as exc:
            raise HTTPException(getattr(exc, "status", 409), str(exc)) from exc
        if payload.revision is not None and material["file_hash"] != payload.revision:
            raise HTTPException(409, "材料源码已被修改，请重新读取后再保存")
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
                    "revision": old_hash,
                    "source_revision": old_hash,
                },
            }

        await _write_material_file(new_hash, payload_bytes)

        updated_at = datetime.now().isoformat()
        cursor = conn.execute(
            """
            UPDATE course_materials
            SET file_hash = ?,
                file_size = ?,
                ai_parse_status = 'idle',
                ai_parse_result_json = NULL,
                check_questions_json = '',
                check_questions_status = 'idle',
                check_questions_error = '',
                check_questions_generated_at = NULL,
                ai_optimize_status = 'idle',
                ai_optimized_markdown = NULL,
                updated_at = ?
            WHERE id = ? AND file_hash = ?
            """,
            (new_hash, len(payload_bytes), updated_at, material_id, old_hash),
        )
        if cursor.rowcount != 1:
            raise HTTPException(409, "材料源码已被修改，请重新读取后再保存")
        if saved and saved.get("engine_asset"):
            conn.execute("UPDATE course_doc_packs SET assets_fingerprint='' WHERE id=?", (saved["pack_id"],))
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
            "revision": new_hash,
            "source_revision": new_hash,
            "viewer_url": f"/materials/view/{material_id}",
        },
    }


def _save_registered_document_source(material_id, payload, user):
    from ...services.lessondoc import editor_service, pack_service, source_edit
    with get_db_connection() as conn:
        material = ensure_teacher_material_owner(conn, material_id, user["id"])
        registered = source_edit.context(conn, material)
        if not registered or registered["lesson_no"] is None:
            return None
        if not is_editable_material(material):
            raise HTTPException(400, "当前仅支持编辑文本类材料")
        try:
            saved = source_edit.save(conn, material=material, teacher_id=user["id"], content=payload.content,
                                     revision=payload.revision, source_revision=payload.source_revision, operation_id=payload.operation_id)
        except (editor_service.EditorError, pack_service.LessonDocWriteConflict) as exc:
            raise HTTPException(getattr(exc, "status", 409), str(exc)) from exc
        if saved is None or saved.get("raw_asset"):
            raise HTTPException(409, "材料结构已更新，请重新打开源码")
        updated = conn.execute("SELECT * FROM course_materials WHERE id=?", (material_id,)).fetchone()
        registered = source_edit.context(conn, updated)
        normalized_content = pack_service._load_file_text(conn, updated)
        conn.commit()
        return {"status": "success", "message": "学习文档源码已保存", "unchanged": saved["unchanged"], "warnings": saved["warnings"],
                "content": normalized_content, "material": {"id": material_id, "name": updated["name"], "updated_at": updated["updated_at"],
                "revision": registered["revision"], "source_revision": updated["file_hash"], "viewer_url": f"/materials/view/{material_id}"}}
