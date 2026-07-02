from .common import *
from .generation_helpers import *
from .ai_import_helpers import *
from .final_material_helpers import *
from .rewrite_helpers import *
from ...services.material_render_service import attach_render_metadata
from ...services.materials_service import is_bindable_learning_material
from ...services.session_learning_materials_service import (
    AI_BLURB_GENERATE_LIMIT,
    add_material as add_session_learning_material,
    build_material_entries,
    generate_material_blurb,
    remove_material as remove_session_learning_material,
    set_blurb as set_session_learning_material_blurb,
)


router = APIRouter()


class ClassroomLearningMaterialBindRequest(BaseModel):
    session_id: int = 0
    material_id: int


def _material_entry_type_label(entry: dict) -> str:
    if entry.get("is_renderable") and entry.get("render_kind") == "html":
        return "网页(HTML)"
    if entry.get("preview_type") == "markdown":
        return "Markdown"
    if entry.get("node_type") == "folder":
        return "文件夹"
    return str(entry.get("preview_type") or "文档")


def _build_session_material_patch(conn, class_offering_id: int, session_id: int, teacher_id: int) -> dict:
    """构造与旧 PUT 接口一致的补丁，供前端 applySessionPatch / applyHomeMaterialPatch 复用。"""
    normalized_session_id = int(session_id or 0)
    if normalized_session_id > 0:
        session_row = conn.execute(
            """
            SELECT id, class_offering_id, course_lesson_id, order_index, title, content,
                   section_count, slot_section_count, session_date, weekday, week_index, learning_material_id
            FROM class_offering_sessions
            WHERE id = ? AND class_offering_id = ?
            LIMIT 1
            """,
            (normalized_session_id, class_offering_id),
        ).fetchone()
        if not session_row:
            raise HTTPException(404, "课次不存在")
        session_item = attach_learning_material_briefs(
            conn,
            [dict(session_row)],
            teacher_id=teacher_id,
            markdown_only=True,
        )[0]
        return {"session": session_item}

    home_payload = attach_home_learning_material_briefs(
        conn,
        [{
            "home_learning_material_id": conn.execute(
                "SELECT home_learning_material_id FROM class_offerings WHERE id = ?",
                (class_offering_id,),
            ).fetchone()["home_learning_material_id"]
        }],
        teacher_id=teacher_id,
        markdown_only=True,
    )[0]
    home_material = home_payload.get("home_learning_material")
    return {
        "home_material": home_material,
        "has_home_material": bool(home_material),
        "home_entry": build_timeline_home_entry(home_material, include_placeholder=True),
    }


@router.get("/api/classrooms/{class_offering_id}/learning-materials", response_class=JSONResponse)
async def list_classroom_learning_materials(
    class_offering_id: int,
    session_id: int = Query(default=0),
    user: dict = Depends(get_current_user),
):
    with get_db_connection() as conn:
        ensure_classroom_access(conn, class_offering_id, user)
        offering = conn.execute(
            "SELECT teacher_id FROM class_offerings WHERE id = ? LIMIT 1",
            (class_offering_id,),
        ).fetchone()
        if not offering:
            raise HTTPException(404, "课堂不存在")
        teacher_id = int(offering["teacher_id"])
        entries = build_material_entries(conn, class_offering_id, session_id, teacher_id=teacher_id)
        can_manage = user["role"] == "teacher" and (
            int(user["id"]) == teacher_id or is_super_admin_teacher(conn, int(user["id"]))
        )
        conn.commit()

    # 懒生成尚未尝试过的一句话简介（快速版 AI，best-effort）。仅处理 status=='idle'，
    # 失败标记为 'failed' 不再每次重试；前端对 failed 用材料路径降级展示。
    pending = [
        entry for entry in entries
        if not entry.get("ai_blurb") and str(entry.get("ai_blurb_status") or "idle") == "idle"
    ][:AI_BLURB_GENERATE_LIMIT]
    if pending:
        generated: list[tuple[dict, str]] = []
        for entry in pending:
            blurb = await generate_material_blurb(
                name=entry["name"],
                type_label=_material_entry_type_label(entry),
                material_path=entry["material_path"],
            )
            generated.append((entry, blurb))
        with get_db_connection() as conn:
            for entry, blurb in generated:
                status = "ready" if blurb else "failed"
                set_session_learning_material_blurb(conn, entry["row_id"], blurb, status=status)
                entry["ai_blurb"] = blurb
                entry["ai_blurb_status"] = status
            conn.commit()

    for entry in entries:
        entry["type_label"] = _material_entry_type_label(entry)

    return {"status": "success", "materials": entries, "can_manage": can_manage}


@router.post("/api/classrooms/{class_offering_id}/learning-materials", response_class=JSONResponse)
async def add_classroom_learning_material(
    class_offering_id: int,
    payload: ClassroomLearningMaterialBindRequest,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        add_session_learning_material(
            conn,
            class_offering_id=class_offering_id,
            session_id=int(payload.session_id or 0),
            material_id=int(payload.material_id),
            teacher_id=int(user["id"]),
        )
        patch = _build_session_material_patch(conn, class_offering_id, int(payload.session_id or 0), int(user["id"]))
        conn.commit()
    return {"status": "success", "message": "材料已添加到列表", **patch}


@router.delete("/api/classrooms/{class_offering_id}/learning-materials", response_class=JSONResponse)
async def remove_classroom_learning_material(
    class_offering_id: int,
    payload: ClassroomLearningMaterialBindRequest,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        remove_session_learning_material(
            conn,
            class_offering_id=class_offering_id,
            session_id=int(payload.session_id or 0),
            material_id=int(payload.material_id),
            teacher_id=int(user["id"]),
        )
        patch = _build_session_material_patch(conn, class_offering_id, int(payload.session_id or 0), int(user["id"]))
        conn.commit()
    return {"status": "success", "message": "已解绑该材料", **patch}


class MaterialLearningBindingTarget(BaseModel):
    class_offering_id: int
    session_id: int = 0


class MaterialLearningBindingsUpdateRequest(BaseModel):
    targets: list[MaterialLearningBindingTarget] = []


def _load_material_learning_binding_context(conn, material_id: int, teacher_id: int) -> dict:
    """教师全部课堂 + 课次 + 该材料当前绑定到的目标（session_id=0 表示首页）。"""
    offering_rows = conn.execute(
        """
        SELECT o.id, o.semester, c.name AS class_name, co.name AS course_name
        FROM class_offerings o
        JOIN classes c ON c.id = o.class_id
        JOIN courses co ON co.id = o.course_id
        WHERE o.teacher_id = ?
        ORDER BY co.name, c.name
        """,
        (teacher_id,),
    ).fetchall()
    offerings = [dict(row) for row in offering_rows]
    offering_ids = [int(row["id"]) for row in offerings]

    sessions_by_offering: dict[int, list[dict]] = {oid: [] for oid in offering_ids}
    if offering_ids:
        placeholders = ",".join("?" for _ in offering_ids)
        session_rows = conn.execute(
            f"""
            SELECT id, class_offering_id, order_index, title
            FROM class_offering_sessions
            WHERE class_offering_id IN ({placeholders})
            ORDER BY class_offering_id, order_index
            """,
            offering_ids,
        ).fetchall()
        for row in session_rows:
            sessions_by_offering[int(row["class_offering_id"])].append(
                {
                    "id": int(row["id"]),
                    "order_index": int(row["order_index"] or 0),
                    "title": str(row["title"] or ""),
                }
            )

    bound_rows = conn.execute(
        """
        SELECT lm.class_offering_id, lm.session_id
        FROM class_offering_learning_materials lm
        JOIN class_offerings o ON o.id = lm.class_offering_id
        WHERE lm.material_id = ? AND o.teacher_id = ?
        """,
        (material_id, teacher_id),
    ).fetchall()
    bound = {(int(row["class_offering_id"]), int(row["session_id"] or 0)) for row in bound_rows}
    # 旧单列镜像也算已绑定（列表表可能尚未回填）
    legacy_home = conn.execute(
        "SELECT id FROM class_offerings WHERE teacher_id = ? AND home_learning_material_id = ?",
        (teacher_id, material_id),
    ).fetchall()
    for row in legacy_home:
        bound.add((int(row["id"]), 0))
    legacy_sessions = conn.execute(
        """
        SELECT s.id, s.class_offering_id
        FROM class_offering_sessions s
        JOIN class_offerings o ON o.id = s.class_offering_id
        WHERE o.teacher_id = ? AND s.learning_material_id = ?
        """,
        (teacher_id, material_id),
    ).fetchall()
    for row in legacy_sessions:
        bound.add((int(row["class_offering_id"]), int(row["id"])))

    for offering in offerings:
        oid = int(offering["id"])
        offering["sessions"] = sessions_by_offering.get(oid, [])
        offering["home_bound"] = (oid, 0) in bound
        offering["bound_session_ids"] = sorted(
            session_id for (offering_id, session_id) in bound if offering_id == oid and session_id > 0
        )
    return {"offerings": offerings, "bound_targets": sorted(bound)}


@router.get("/api/materials/{material_id}/learning-bindings", response_class=JSONResponse)
async def get_material_learning_bindings(
    material_id: int,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        material = ensure_teacher_material_owner(conn, material_id, user["id"])
        bindable = is_bindable_learning_material(conn, material)
        context = _load_material_learning_binding_context(conn, material_id, int(user["id"]))
    return {
        "status": "success",
        "material": {"id": int(material["id"]), "name": material["name"]},
        "bindable": bindable,
        "offerings": context["offerings"],
    }


@router.put("/api/materials/{material_id}/learning-bindings", response_class=JSONResponse)
async def update_material_learning_bindings(
    material_id: int,
    payload: MaterialLearningBindingsUpdateRequest,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        ensure_teacher_learning_material_owner(conn, material_id, user["id"])
        context = _load_material_learning_binding_context(conn, material_id, int(user["id"]))
        allowed_offering_ids = {int(item["id"]) for item in context["offerings"]}
        allowed_session_ids = {
            (int(item["id"]), int(session["id"]))
            for item in context["offerings"]
            for session in item["sessions"]
        }

        desired: set[tuple[int, int]] = set()
        for target in payload.targets:
            offering_id = int(target.class_offering_id)
            session_id = int(target.session_id or 0)
            if offering_id not in allowed_offering_ids:
                raise HTTPException(403, "包含无权绑定的课堂")
            if session_id > 0 and (offering_id, session_id) not in allowed_session_ids:
                raise HTTPException(400, "包含不存在的课次")
            desired.add((offering_id, session_id))

        current = set(context["bound_targets"])
        to_add = sorted(desired - current)
        to_remove = sorted(current - desired)

        for offering_id, session_id in to_add:
            add_session_learning_material(
                conn,
                class_offering_id=offering_id,
                session_id=session_id,
                material_id=material_id,
                teacher_id=int(user["id"]),
            )
        for offering_id, session_id in to_remove:
            remove_session_learning_material(
                conn,
                class_offering_id=offering_id,
                session_id=session_id,
                material_id=material_id,
                teacher_id=int(user["id"]),
            )
        refreshed = _load_material_learning_binding_context(conn, material_id, int(user["id"]))
        conn.commit()

    message_parts = []
    if to_add:
        message_parts.append(f"新增绑定 {len(to_add)} 处")
    if to_remove:
        message_parts.append(f"解绑 {len(to_remove)} 处")
    message = "，".join(message_parts) if message_parts else "绑定没有变化"
    return {
        "status": "success",
        "message": message,
        "added_count": len(to_add),
        "removed_count": len(to_remove),
        "offerings": refreshed["offerings"],
    }


@router.post("/api/materials/{material_id}/ai-assign-sessions", response_class=JSONResponse)
async def ai_assign_material_to_sessions(
    material_id: int,
    payload: MaterialAssignRequest,
    user: dict = Depends(get_current_teacher),
):
    """AI 分析文档结构并自动将文档文件绑定到对应课堂的课次（session）上。"""
    desired_ids = [int(item) for item in payload.class_offering_ids if item]
    if not desired_ids:
        raise HTTPException(400, "请至少选择一个课堂")

    with get_db_connection() as conn:
        material = ensure_teacher_material_owner(conn, material_id, user["id"])

        # 获取该材料下的所有文件（含子目录递归）
        subtree_rows = _collect_subtree_rows(conn, material, include_internal=False)
        file_rows = [
            dict(row) for row in subtree_rows
            if row["node_type"] == "file"
            and row["preview_type"] == "markdown"
            and not is_git_internal_material_path(row["material_path"])
        ]
        if not file_rows:
            raise HTTPException(400, "当前材料下没有可分配的 Markdown 文档")
        fallback_home_row = _infer_home_material_row(file_rows, material["material_path"])

        # 收集所有选中课堂的课次信息
        offering_rows = conn.execute(
            "SELECT id FROM class_offerings WHERE teacher_id = ?",
            (user["id"],),
        ).fetchall()
        allowed_ids = {int(row["id"]) for row in offering_rows}
        invalid_ids = set(desired_ids) - allowed_ids
        if invalid_ids:
            raise HTTPException(403, "包含无权分配的课堂")

        all_sessions_by_offering: dict[int, list[dict]] = {}
        for offering_id in desired_ids:
            sessions = conn.execute(
                """
                SELECT s.id, s.order_index, s.title, s.content, s.learning_material_id
                FROM class_offering_sessions s
                WHERE s.class_offering_id = ?
                ORDER BY s.order_index
                """,
                (offering_id,),
            ).fetchall()
            all_sessions_by_offering[offering_id] = [dict(s) for s in sessions]

    # 构建发送给 AI 的内容
    file_list_text = "\n".join(
        f"  - ID={row['id']}, path=\"{row['material_path']}\""
        for row in file_rows
    )

    sessions_context_parts: list[str] = []
    for offering_id in desired_ids:
        sessions = all_sessions_by_offering.get(offering_id, [])
        if not sessions:
            continue
        sessions_text = "\n".join(
            f"    - order_index={s['order_index']}, session_id={s['id']}, title=\"{s['title']}\""
            for s in sessions
        )
        sessions_context_parts.append(
            f"  课堂 ID={offering_id}（共 {len(sessions)} 次课）:\n{sessions_text}"
        )
    sessions_context_text = "\n".join(sessions_context_parts)

    if not sessions_context_text:
        raise HTTPException(400, "所选课堂暂无课次安排，请先配置课堂的课次拆分")

    system_prompt = (
        "你是一名教学材料匹配助手。你的任务是根据文档文件的完整路径和课堂课次的标题、顺序，"
        "将文档文件智能匹配到课程首页或对应的课次上。\n\n"
        "匹配规则：\n"
        "1. 先识别课程首页文档。根目录或课程目录下的 README.md、index.md、home.md、首页.md、目录.md、课程简介.md、overview.md 通常是首页；首页用于目录、课程简介和后续文档跳转，不绑定到第1次课。\n"
        "2. 再匹配课次文档。优先按路径中的序号（如 lesson01、L1、第1课、01 等）与课次的 order_index 对应。\n"
        "3. 如果 README.md、index.md 位于某个 lesson01/L1/第1课 目录内，它才属于该课次；如果位于根目录或课程总目录，它属于首页。\n"
        "4. 文档数量与课次数可能不完全对应，多余的说明文档不强行分配。\n"
        "5. 必须使用文档的完整路径进行识别和匹配。\n"
        "6. 每个课堂最多一个首页文档；每个课次只能匹配一个课次文档。\n\n"
        "输出格式：严格的 JSON 对象，包含 \"assignments\" 数组，每个元素包含：\n"
        "  - class_offering_id: 课堂 ID\n"
        "  - session_id: 课次 ID\n"
        "  - material_id: 文档文件 ID\n"
        "  - material_path: 文档完整路径\n"
        "  - confidence: 匹配置信度（high/medium/low）\n\n"
        "如识别到首页，还必须包含 \"home_assignments\" 数组，每个元素包含：\n"
        "  - class_offering_id: 课堂 ID\n"
        "  - material_id: 首页文档文件 ID\n"
        "  - material_path: 首页文档完整路径\n"
        "  - confidence: 匹配置信度（high/medium/low）\n\n"
        "只输出 JSON，不要输出任何其他解释文字或 Markdown 代码块。"
    )

    user_message = (
        f"请将以下文档文件匹配到对应课堂的课次上。\n\n"
        f"【文档文件列表】\n{file_list_text}\n\n"
        f"【课堂课次列表】\n{sessions_context_text}\n\n"
        f"请返回匹配结果 JSON。"
    )

    response_text = await _call_ai_chat(system_prompt, user_message, capability="thinking")
    parsed_result = _parse_ai_json(response_text)

    assignments_raw = parsed_result.get("assignments", [])
    if not isinstance(assignments_raw, list):
        raise HTTPException(500, "AI 未返回有效的匹配结果，请重试或手动分配")

    # 构建校验映射
    file_id_map = {int(row["id"]): row for row in file_rows}
    home_assignments_by_offering = _collect_ai_home_assignments(
        parsed_result,
        desired_offering_ids=desired_ids,
        file_id_map=file_id_map,
        fallback_home_row=fallback_home_row,
    )
    session_id_map: dict[int, dict] = {}
    for offering_id, sessions in all_sessions_by_offering.items():
        for s in sessions:
            session_id_map[int(s["id"])] = {**s, "class_offering_id": offering_id}

    # 过滤有效匹配并执行绑定
    valid_assignments: list[dict] = []
    valid_home_assignments: list[dict] = []
    bound_material_keys: set[tuple[int, int]] = set()
    bound_session_ids: set[int] = set()
    now = datetime.now().isoformat()

    with get_db_connection() as conn:
        for offering_id, home_item in home_assignments_by_offering.items():
            mat_id = int(home_item.get("material_id") or 0)
            if offering_id not in allowed_ids or mat_id not in file_id_map:
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
                "session_id": None,
                "session_title": "目录与简介",
                "order_index": 0,
                "material_id": mat_id,
                "material_path": file_id_map[mat_id]["material_path"],
                "confidence": home_item.get("confidence", "medium"),
                "source": home_item.get("source", "ai"),
            })

        for item in assignments_raw:
            target_type = str(item.get("target_type") or item.get("target") or "").strip().lower()
            if target_type in {"home", "homepage", "index", "intro", "introduction"}:
                continue
            offering_id = int(item.get("class_offering_id") or 0)
            session_id = int(item.get("session_id") or 0)
            mat_id = int(item.get("material_id") or 0)

            if offering_id not in allowed_ids:
                continue
            if session_id not in session_id_map:
                continue
            if mat_id not in file_id_map:
                continue
            session_info = session_id_map[session_id]
            if int(session_info["class_offering_id"]) != offering_id:
                continue
            if session_id in bound_session_ids:
                continue
            if (offering_id, mat_id) in bound_material_keys:
                continue

            bound_session_ids.add(session_id)
            bound_material_keys.add((offering_id, mat_id))


            # 绑定 learning_material_id 到 session
            conn.execute(
                """
                UPDATE class_offering_sessions
                SET learning_material_id = ?,
                    updated_at = ?
                WHERE id = ? AND class_offering_id = ?
                """,
                (mat_id, now, session_id, offering_id),
            )

            # 同步课堂材料访问权限
            sync_classroom_learning_material_assignments(
                conn,
                class_offering_id=offering_id,
                teacher_id=int(user["id"]),
                material_ids=[mat_id],
            )

            valid_assignments.append({
                "target_type": "lesson",
                "class_offering_id": offering_id,
                "session_id": session_id,
                "session_title": session_info.get("title", ""),
                "order_index": session_info.get("order_index", 0),
                "material_id": mat_id,
                "material_path": file_id_map[mat_id]["material_path"],
                "confidence": item.get("confidence", "medium"),
            })

        conn.commit()

    return {
        "status": "success",
        "message": (
            f"AI 已完成匹配，成功绑定 {len(valid_assignments)} 个课次文档"
            + (f"，并识别 {len(valid_home_assignments)} 个首页文档" if valid_home_assignments else "")
        ),
        "total_assignments": len(valid_assignments),
        "total_home_assignments": len(valid_home_assignments),
        "assignments": valid_home_assignments + valid_assignments,
        "lesson_assignments": valid_assignments,
        "home_assignments": valid_home_assignments,
    }


@router.put("/api/classrooms/{class_offering_id}/learning-home-material", response_class=JSONResponse)
async def update_classroom_home_learning_material(
    class_offering_id: int,
    payload: ClassroomHomeLearningMaterialUpdateRequest,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        offering_row = conn.execute(
            """
            SELECT id, teacher_id, home_learning_material_id
            FROM class_offerings
            WHERE id = ? AND teacher_id = ?
            LIMIT 1
            """,
            (class_offering_id, user["id"]),
        ).fetchone()
        if not offering_row:
            raise HTTPException(404, "课堂不存在或无权操作")

        learning_material_id = payload.learning_material_id
        if learning_material_id is not None:
            learning_material_id = int(learning_material_id)
            if learning_material_id <= 0:
                learning_material_id = None
            else:
                ensure_teacher_learning_material_owner(conn, learning_material_id, user["id"])

        conn.execute(
            """
            UPDATE class_offerings
            SET home_learning_material_id = ?
            WHERE id = ? AND teacher_id = ?
            """,
            (learning_material_id, class_offering_id, user["id"]),
        )

        if learning_material_id:
            sync_classroom_learning_material_assignments(
                conn,
                class_offering_id=class_offering_id,
                teacher_id=int(user["id"]),
                material_ids=[learning_material_id],
            )

        home_payload = attach_home_learning_material_briefs(
            conn,
            [{"home_learning_material_id": learning_material_id}],
            teacher_id=int(user["id"]),
            markdown_only=True,
        )[0]
        conn.commit()

    home_material = home_payload.get("home_learning_material")
    return {
        "status": "success",
        "message": "课程首页已更新" if home_material else "课程首页已移除",
        "home_material": home_material,
        "has_home_material": bool(home_material),
        "home_entry": build_timeline_home_entry(home_material, include_placeholder=True),
    }


@router.put("/api/classrooms/{class_offering_id}/sessions/{session_id}/learning-material", response_class=JSONResponse)
async def update_classroom_session_learning_material(
    class_offering_id: int,
    session_id: int,
    payload: ClassroomLearningMaterialUpdateRequest,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        session_row = conn.execute(
            """
            SELECT s.id,
                   s.class_offering_id,
                   s.course_lesson_id,
                   s.order_index,
                   s.title,
                   s.content,
                   s.section_count,
                   s.slot_section_count,
                   s.session_date,
                   s.weekday,
                   s.week_index,
                   s.learning_material_id
            FROM class_offering_sessions s
            JOIN class_offerings o ON o.id = s.class_offering_id
            WHERE s.id = ? AND s.class_offering_id = ? AND o.teacher_id = ?
            LIMIT 1
            """,
            (session_id, class_offering_id, user["id"]),
        ).fetchone()
        if not session_row:
            raise HTTPException(404, "课堂节点不存在或无权操作")

        learning_material_id = payload.learning_material_id
        if learning_material_id is not None:
            learning_material_id = int(learning_material_id)
            if learning_material_id <= 0:
                learning_material_id = None
            else:
                ensure_teacher_learning_material_owner(conn, learning_material_id, user["id"])

        conn.execute(
            """
            UPDATE class_offering_sessions
            SET learning_material_id = ?
            WHERE id = ? AND class_offering_id = ?
            """,
            (learning_material_id, session_id, class_offering_id),
        )

        if learning_material_id:
            sync_classroom_learning_material_assignments(
                conn,
                class_offering_id=class_offering_id,
                teacher_id=int(user["id"]),
                material_ids=[learning_material_id],
            )

        updated_row = conn.execute(
            """
            SELECT id,
                   class_offering_id,
                   course_lesson_id,
                   order_index,
                   title,
                   content,
                   section_count,
                   slot_section_count,
                   session_date,
                   weekday,
                   week_index,
                   learning_material_id
            FROM class_offering_sessions
            WHERE id = ? AND class_offering_id = ?
            LIMIT 1
            """,
            (session_id, class_offering_id),
        ).fetchone()
        session_item = attach_learning_material_briefs(
            conn,
            [dict(updated_row)],
            teacher_id=int(user["id"]),
            markdown_only=True,
        )[0]
        conn.commit()

    return {
        "status": "success",
        "message": "课堂材料已更新",
        "session": session_item,
    }


@router.get("/api/classrooms/{class_offering_id}/sessions/{session_id}/ai-material-task", response_class=JSONResponse)
async def get_classroom_session_ai_material_task(
    class_offering_id: int,
    session_id: int,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        session_item = get_teacher_session_with_material_state(
            conn,
            class_offering_id=class_offering_id,
            session_id=session_id,
            teacher_id=int(user["id"]),
        )
        if not session_item:
            raise HTTPException(404, "Session not found or access denied")
        conn.commit()

    return {
        "status": "success",
        "task": session_item.get("material_generation_task"),
        "session": session_item,
    }


@router.post("/api/classrooms/{class_offering_id}/sessions/{session_id}/ai-material-task", response_class=JSONResponse)
async def create_classroom_session_ai_material_task(
    class_offering_id: int,
    session_id: int,
    mode: str = Form(default="guided"),
    document_type: str = Form(default=""),
    requirement_text: str = Form(default=""),
    guided_document_type: str = Form(default=""),
    guided_requirement_text: str = Form(default=""),
    auto_document_type: str = Form(default=""),
    auto_requirement_text: str = Form(default=""),
    example_files: list[UploadFile] | None = File(default=None),
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        session_item = get_teacher_session_with_material_state(
            conn,
            class_offering_id=class_offering_id,
            session_id=session_id,
            teacher_id=int(user["id"]),
        )
        if not session_item:
            raise HTTPException(404, "Session not found or access denied")

        existing_task = session_item.get("material_generation_task")
        if existing_task and existing_task.get("is_active"):
            conn.commit()
            return {
                "status": "accepted",
                "message": "AI assistant is already generating material for this session.",
                "task": existing_task,
                "session": session_item,
            }

        normalized_mode = str(mode or "guided").strip().lower()
        if normalized_mode not in {"guided", "auto"}:
            normalized_mode = "guided"
        requested_document_type = (
            auto_document_type if normalized_mode == "auto" else guided_document_type
        ) or document_type
        requested_requirement_text = (
            auto_requirement_text if normalized_mode == "auto" else guided_requirement_text
        ) or requirement_text
        normalized_document_type = normalize_document_type(
            requested_document_type,
            session_title=session_item.get("title") or "",
            session_content=session_item.get("content") or "",
        )
        normalized_requirement_text = normalize_requirement_text(requested_requirement_text)
        conn.commit()

    example_documents = await extract_example_documents(
        example_files if normalized_mode == "guided" else None,
    )

    with get_db_connection() as conn:
        task = create_generation_task(
            conn,
            class_offering_id=class_offering_id,
            session_id=session_id,
            teacher_id=int(user["id"]),
            trigger_mode=normalized_mode,
            document_type=normalized_document_type,
            requirement_text=normalized_requirement_text,
            example_documents=example_documents,
        )
        session_item = get_teacher_session_with_material_state(
            conn,
            class_offering_id=class_offering_id,
            session_id=session_id,
            teacher_id=int(user["id"]),
        )
        conn.commit()

    if task and not task.get("already_running"):
        asyncio.create_task(run_generation_task(int(task["id"])))

    return {
        "status": "accepted",
        "message": "AI assistant started generating session material.",
        "task": task,
        "session": session_item,
    }


@router.get(
    "/api/classrooms/{class_offering_id}/materials",
    response_class=JSONResponse,
    response_model=ClassroomMaterialsResponse,
    response_model_exclude_unset=True,
)
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
                    "SELECT COUNT(*) FROM course_materials WHERE parent_id = ? AND name != '.git'",
                    (row["id"],),
                ).fetchone()[0]
                row_dict = dict(row)
                row_dict["child_count"] = int(child_count)
                items.append(_decorate_material_download_policy(serialize_material_row(row_dict)))
            items = attach_git_repository_metadata(conn, items)
            items = attach_render_metadata(conn, items)
            items = [_decorate_learning_document_item(item) for item in attach_learning_document_metadata(conn, items)]
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
                   (SELECT COUNT(*) FROM course_materials child WHERE child.parent_id = m.id AND child.name != '.git') AS child_count,
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
            if is_git_internal_material_path(row_dict["material_path"]):
                continue
            if is_descendant_path(row_dict["material_path"], anchor["material_path"]):
                items.append(_decorate_material_download_policy(serialize_material_row(row_dict)))
        items = attach_git_repository_metadata(conn, items)
        items = [_decorate_learning_document_item(item) for item in attach_learning_document_metadata(conn, items)]

        breadcrumbs = _slice_breadcrumbs_from_anchor(get_material_breadcrumbs(conn, parent_id), anchor["id"])
        return {
            "status": "success",
            "current_folder": _decorate_material_download_policy(
                attach_git_repository_metadata(conn, [serialize_material_row(folder)])[0]
            ),
            "breadcrumbs": breadcrumbs,
            "items": items,
        }
