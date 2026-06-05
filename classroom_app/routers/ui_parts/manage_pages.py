from .common import *


router = APIRouter()


@router.get("/manage", response_class=HTMLResponse)
async def manage_workflow_page(request: Request, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        workflow_snapshot = _build_classroom_opening_workflow_snapshot(conn, int(user["id"]))

    return templates.TemplateResponse(
        request,
        "manage/workflow.html",
        _build_manage_template_context(
            request,
            user,
            page_title="教学流程工作台",
            active_page="workflow",
            extra={
                "workflow_snapshot": workflow_snapshot,
            },
        ),
    )


@router.get("/manage/classes", response_class=HTMLResponse)
async def get_manage_classes_page(request: Request, user: dict = Depends(get_current_teacher)):
    """显示班级管理页面 (列表和新建)"""
    with get_db_connection() as conn:
        current_teacher_is_super_admin = is_super_admin_teacher(conn, user["id"])
        school_codes = _teacher_school_codes(conn, int(user["id"]))
        class_scope_where = "1 = 1" if current_teacher_is_super_admin else (
            "lower(TRIM(COALESCE(c.school_code, ''))) IN ("
            + ",".join("?" for _ in school_codes)
            + ")"
            if school_codes
            else "c.created_by_teacher_id = ?"
        )
        class_scope_params = [] if current_teacher_is_super_admin else (school_codes or [int(user["id"])])
        my_classes_cursor = conn.execute(
            f"""
            SELECT c.id,
                   c.name,
                   c.department,
                   c.description,
                   c.academic_source,
                   c.academic_class_code,
                   c.academic_class_name,
                   c.academic_college,
                   c.academic_grade,
                   c.academic_major,
                   c.school_code,
                   c.school_name,
                   c.college,
                   c.major,
                   c.owner_role,
                   c.owner_user_pk,
                   c.scope_level,
                   c.updated_at,
                   c.archived_at,
                   c.deleted_at,
                   c.academic_sync_at,
                   c.academic_sync_message,
                   c.created_at,
                   c.created_by_teacher_id,
                   t.name AS owner_teacher_name,
                   COUNT(DISTINCT CASE
                       WHEN COALESCE(s.enrollment_status, 'active') = 'active'
                       THEN s.id END
                   ) AS student_count,
                   COUNT(DISTINCT CASE
                       WHEN COALESCE(s.enrollment_status, 'active') = 'suspended'
                       THEN s.id END
                   ) AS suspended_student_count,
                   COUNT(DISTINCT s.id) AS total_student_count,
                   SUM(
                       CASE
                           WHEN s.id IS NOT NULL
                             AND COALESCE(s.enrollment_status, 'active') = 'active'
                             AND (s.email IS NULL OR TRIM(s.email) = '')
                            THEN 1 ELSE 0
                       END
                    ) AS missing_email_count,
                    COUNT(DISTINCT CASE
                       WHEN s.academic_source = 'gxufl_jwxt'
                       THEN s.id END
                    ) AS academic_synced_student_count,
                    COUNT(DISTINCT o.id) AS offering_count,
                    MAX(
                        CASE
                            WHEN COALESCE(s.enrollment_status, 'active') = 'active'
                            THEN s.created_at
                        END
                    ) AS latest_student_created_at,
                    MAX(s.academic_sync_at) AS latest_student_academic_sync_at
             FROM classes c
             LEFT JOIN teachers t ON t.id = c.created_by_teacher_id
             LEFT JOIN students s ON c.id = s.class_id
            LEFT JOIN class_offerings o
                   ON o.class_id = c.id
                  AND o.teacher_id = c.created_by_teacher_id
            WHERE {class_scope_where}
              GROUP BY c.id, c.name, c.department, c.description,
                       c.academic_source, c.academic_class_code, c.academic_class_name,
                       c.academic_college, c.academic_grade, c.academic_major,
                       c.school_code, c.school_name, c.college, c.major,
                       c.owner_role, c.owner_user_pk, c.scope_level,
                       c.updated_at, c.archived_at, c.deleted_at,
                       c.academic_sync_at, c.academic_sync_message, c.created_at,
                       c.created_by_teacher_id, t.name
             ORDER BY COALESCE(NULLIF(TRIM(c.department), ''), '未分类'), c.name
            """,
            class_scope_params,
        )
        my_classes = [
            dict(row)
            for row in my_classes_cursor.fetchall()
            if teacher_can_use_class(conn, int(user["id"]), row)
        ]
        manageable_or_taught_ids = []
        for class_item in my_classes:
            class_item["student_count"] = int(class_item.get("student_count") or 0)
            class_item["suspended_student_count"] = int(class_item.get("suspended_student_count") or 0)
            class_item["total_student_count"] = int(class_item.get("total_student_count") or 0)
            class_item["missing_email_count"] = int(class_item.get("missing_email_count") or 0)
            class_item["academic_synced_student_count"] = int(class_item.get("academic_synced_student_count") or 0)
            class_item["offering_count"] = int(class_item.get("offering_count") or 0)
            class_item["is_owned"] = int(class_item.get("created_by_teacher_id") or 0) == int(user["id"])
            class_item["can_manage"] = class_item["is_owned"] or current_teacher_is_super_admin
            teaches_class = conn.execute(
                """
                SELECT 1
                FROM class_offerings
                WHERE teacher_id = ? AND class_id = ?
                LIMIT 1
                """,
                (int(user["id"]), int(class_item["id"])),
            ).fetchone() is not None
            class_item["can_view_content"] = bool(class_item["can_manage"] or teaches_class)
            if class_item["can_view_content"]:
                manageable_or_taught_ids.append(int(class_item["id"]))
            class_item["is_shared_class"] = not class_item["is_owned"]
            class_item["owner_teacher_name"] = str(class_item.get("owner_teacher_name") or "").strip()
            class_item["department_label"] = str(class_item.get("department") or "").strip() or "未分类"
            class_item["organization_label"] = organization_label(
                {
                    "school_code": class_item.get("school_code"),
                    "school_name": class_item.get("school_name"),
                    "college": class_item.get("college") or class_item.get("academic_college"),
                    "department": class_item.get("department"),
                }
            )
            class_item["is_academic_synced"] = str(class_item.get("academic_source") or "").strip() == "gxufl_jwxt"
            class_item["latest_academic_sync_at"] = (
                class_item.get("latest_student_academic_sync_at")
                or class_item.get("academic_sync_at")
                or ""
            )
            class_item["email_coverage_percent"] = (
                round(
                    (class_item["student_count"] - class_item["missing_email_count"])
                    / class_item["student_count"]
                    * 100
                )
                if class_item["student_count"]
                else 0
            )
        students_by_class = _load_teacher_class_student_rows(
            conn,
            int(user["id"]),
            manageable_or_taught_ids,
        )
        for class_item in my_classes:
            class_item["students"] = students_by_class.get(int(class_item["id"]), [])
            class_item["active_students"] = [
                student
                for student in class_item["students"]
                if student.get("enrollment_status") == STUDENT_STATUS_ACTIVE
            ]

    missing_email_total = sum(int(item.get("missing_email_count") or 0) for item in my_classes)
    active_class_count = sum(1 for item in my_classes if int(item.get("offering_count") or 0) > 0)
    class_stats = {
        "class_count": len(my_classes),
        "student_count": sum(int(item.get("student_count") or 0) for item in my_classes),
        "suspended_student_count": sum(int(item.get("suspended_student_count") or 0) for item in my_classes),
        "largest_class_size": max((int(item.get("student_count") or 0) for item in my_classes), default=0),
        "missing_email_count": missing_email_total,
        "active_class_count": active_class_count,
        "department_count": len({item.get("department_label") for item in my_classes if item.get("department_label")}),
        "academic_synced_class_count": sum(1 for item in my_classes if item.get("is_academic_synced")),
        "academic_synced_student_count": sum(int(item.get("academic_synced_student_count") or 0) for item in my_classes),
    }

    return templates.TemplateResponse(
        request,
        "manage/classes.html",
        _build_manage_template_context(
            request,
            user,
            page_title="班级管理",
            active_page="classes",
            extra={
                "my_classes": my_classes,
                "class_stats": class_stats,
                "department_options": collect_department_options(
                    (item.get("department") for item in my_classes),
                ),
            },
        ),
    )


@router.get("/manage/students/{student_id}", response_class=HTMLResponse)
async def get_manage_student_detail_page(
    request: Request,
    student_id: int,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        insight = build_teacher_student_insight(conn, int(user["id"]), int(student_id))
        if not insight:
            raise HTTPException(status_code=404, detail="学生不存在或无权查看")
        conn.commit()

    student = insight.get("student") or {}
    return templates.TemplateResponse(
        request,
        "manage/student_detail.html",
        _build_manage_template_context(
            request,
            user,
            page_title=f"{student.get('name') or '学生'} · 学生洞察",
            active_page="classes",
            extra={
                "insight": insight,
            },
        ),
    )


@router.get("/manage/classrooms", response_class=HTMLResponse)
async def get_manage_classrooms_page(request: Request, user: dict = Depends(get_current_teacher)):
    """教学场地与空闲教室查询页面。"""
    initial_page_size = 10
    with get_db_connection() as conn:
        teaching_place_count = count_teacher_teaching_places(conn, int(user["id"]))
        teaching_places = load_teacher_teaching_places(conn, int(user["id"]), limit=initial_page_size)
        classroom_dashboard = load_teacher_teaching_place_dashboard(conn, int(user["id"]))
        semester_options = [
            serialize_semester_row(row)
            for row in load_teacher_semester_rows(conn, int(user["id"]))
        ]

    return templates.TemplateResponse(
        request,
        "manage/classrooms.html",
        _build_manage_template_context(
            request,
            user,
            page_title="教室管理",
            active_page="classrooms",
            extra={
                "teaching_places": teaching_places,
                "teaching_place_pagination": {
                    "page": 1,
                    "page_size": initial_page_size,
                    "total_count": teaching_place_count,
                    "total_page": max(1, (teaching_place_count + initial_page_size - 1) // initial_page_size),
                },
                "classroom_dashboard": classroom_dashboard,
                "semester_options": semester_options,
                "default_semester_id": choose_default_semester_id(semester_options),
            },
        ),
    )


@router.get("/manage/courses", response_class=HTMLResponse)
async def get_manage_courses_page(request: Request, user: dict = Depends(get_current_teacher)):
    """显示课程管理页面 (列表和新建)"""
    with get_db_connection() as conn:
        my_courses = _load_teacher_course_rows(conn, int(user["id"]))
        semesters = load_teacher_semester_rows(conn, int(user["id"]))
        _decorate_course_grouping_context(my_courses, semesters)
        textbooks = [
            {
                "id": item["id"],
                "title": item["title"],
                "author_display": item["author_display"],
                "publisher": item["publisher"],
                "publication_year": item["publication_year"],
            }
            for item in (serialize_textbook_row(row) for row in _load_teacher_textbook_rows(conn, int(user["id"])))
        ]

    course_stats = {
        "course_count": len(my_courses),
        "active_course_count": sum(1 for item in my_courses if item.get("is_in_use")),
        "academic_synced_course_count": sum(1 for item in my_courses if item.get("academic_is_synced")),
        "lesson_count": sum(int(item.get("lesson_count") or 0) for item in my_courses),
        "total_hours": sum(int(item.get("total_hours") or 0) for item in my_courses),
    }

    return templates.TemplateResponse(
        request,
        "manage/courses.html",
        _build_manage_template_context(
            request,
            user,
            page_title="课程管理",
            active_page="courses",
            extra={
                "my_courses": my_courses,
                "courses_json": my_courses,
                "textbooks_json": textbooks,
                "course_stats": course_stats,
                "semester_calendar": build_semester_calendar_payload(semesters),
                "department_options": collect_department_options(
                    (item.get("department") for item in my_courses),
                ),
            },
        ),
    )


@router.get("/manage/semesters", response_class=HTMLResponse)
async def get_manage_semesters_page(request: Request, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        semester_calendar = build_semester_calendar_payload(
            load_teacher_semester_rows(conn, int(user["id"])),
        )

    current_date = china_today()
    semesters = semester_calendar["semesters"]

    return templates.TemplateResponse(
        request,
        "manage/semesters.html",
        _build_manage_template_context(
            request,
            user,
            page_title="学期管理",
            active_page="semesters",
            extra={
                "semesters": semesters,
                "semester_calendar": semester_calendar,
                "semester_defaults": build_semester_defaults(current_date),
            },
        ),
    )


@router.get("/manage/textbooks", response_class=HTMLResponse)
async def get_manage_textbooks_page(request: Request, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        current_teacher_is_super_admin = is_super_admin_teacher(conn, user["id"])
        textbooks = [
            serialize_textbook_row(row)
            for row in _load_teacher_textbook_rows(conn, int(user["id"]))
        ]
        for item in textbooks:
            item["is_owned"] = int(item.get("teacher_id") or 0) == int(user["id"])
            item["can_manage"] = item["is_owned"] or current_teacher_is_super_admin
            item["owner_teacher_name"] = str(item.get("owner_teacher_name") or "").strip()

    return templates.TemplateResponse(
        request,
        "manage/textbooks.html",
        _build_manage_template_context(
            request,
            user,
            page_title="教材管理",
            active_page="textbooks",
            extra={
                "textbooks": textbooks,
                "textbooks_json": textbooks,
            },
        ),
    )


@router.get("/manage/signatures", response_class=HTMLResponse)
async def get_manage_signatures_page(request: Request, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        signature_context = build_signature_dashboard_context(conn, user)

    return templates.TemplateResponse(
        request,
        "manage/signatures.html",
        _build_manage_template_context(
            request,
            user,
            page_title="电子签名",
            active_page="signatures",
            extra=signature_context,
        ),
    )


@router.get("/manage/offerings", response_class=HTMLResponse)
async def get_manage_offerings_page(request: Request, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        school_codes = _teacher_school_codes(conn, int(user["id"]))
        class_where = (
            "1 = 1"
            if is_super_admin_teacher(conn, user["id"])
            else (
                "lower(TRIM(COALESCE(school_code, ''))) IN ("
                + ",".join("?" for _ in school_codes)
                + ")"
                if school_codes
                else "created_by_teacher_id = ?"
            )
        )
        class_params = [] if is_super_admin_teacher(conn, user["id"]) else (school_codes or [int(user["id"])])
        my_classes = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT id, name, department, created_by_teacher_id,
                       owner_role, owner_user_pk, scope_level,
                       school_code, school_name, college
                FROM classes
                WHERE {class_where}
                ORDER BY name
                """,
                class_params,
            ).fetchall()
            if teacher_can_use_class(conn, int(user["id"]), row)
        ]
        my_courses = _load_teacher_course_rows(conn, int(user["id"]))
        semester_rows = load_teacher_semester_rows(conn, int(user["id"]))
        textbook_rows = _load_teacher_textbook_rows(conn, int(user["id"]))
        my_semesters = [serialize_semester_row(row) for row in semester_rows]
        my_textbooks = [
            {
                "id": item["id"],
                "title": item["title"],
                "author_display": item["author_display"],
                "publication_year": item["publication_year"],
                "publisher": item["publisher"],
            }
            for item in (serialize_textbook_row(row) for row in textbook_rows)
        ]
        my_offerings = _load_teacher_offering_rows(conn, int(user["id"]))

    return templates.TemplateResponse(
        request,
        "manage/offerings.html",
        _build_manage_template_context(
            request,
            user,
            page_title="开设课堂",
            active_page="offerings",
            extra={
                "my_classes": my_classes,
                "my_courses": my_courses,
                "my_semesters": my_semesters,
                "my_textbooks": my_textbooks,
                "my_offerings": my_offerings,
                "default_semester_id": choose_default_semester_id(my_semesters),
                "department_options": collect_department_options(
                    (item.get("department") for item in my_classes),
                    (item.get("department") for item in my_courses),
                ),
            },
        ),
    )


@router.get("/manage/ai", response_class=HTMLResponse)
async def get_manage_ai_page(request: Request, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        my_offerings = _load_teacher_offering_rows(conn, int(user["id"]))
        my_textbooks = [
            serialize_textbook_row(row)
            for row in _load_teacher_textbook_rows(conn, int(user["id"]))
        ]

    return templates.TemplateResponse(
        request,
        "manage/ai.html",
        _build_manage_template_context(
            request,
            user,
            page_title="课堂 AI 助教",
            active_page="ai",
            extra={
                "my_offerings": my_offerings,
                "my_textbooks": my_textbooks,
            },
        ),
    )


@router.get("/manage/system", response_class=HTMLResponse)
async def get_manage_system_redirect(request: Request, user: dict = Depends(get_current_teacher)):
    """重定向旧的系统管理页面到当前教师可访问的系统页。"""
    with get_db_connection() as conn:
        if is_super_admin_teacher(conn, user["id"]):
            return RedirectResponse(url="/manage/system/users", status_code=302)
    return RedirectResponse(url="/manage/system/password-resets", status_code=302)


@router.get("/manage/system/academic-integrations", response_class=HTMLResponse)
async def get_manage_system_academic_integrations_page(request: Request, user: dict = Depends(get_current_teacher)):
    """教师个人教务系统账号与适配器管理页面。"""
    profiles = list_academic_system_profiles()
    with get_db_connection() as conn:
        credentials = list_teacher_academic_credentials(conn, int(user["id"]))

    return templates.TemplateResponse(
        request,
        "manage/system/academic_integrations.html",
        _build_manage_template_context(
            request,
            user,
            page_title="教务系统对接",
            active_page="system_academic_integrations",
            extra={
                "academic_profiles": profiles,
                "academic_credentials": credentials,
            },
        ),
    )


@router.get("/manage/system/smart-classroom-integrations", response_class=HTMLResponse)
async def get_manage_system_smart_classroom_integrations_page(request: Request, user: dict = Depends(get_current_teacher)):
    """教师个人智慧课堂账号与点名同步管理页面。"""
    profiles = list_smart_classroom_profiles()
    with get_db_connection() as conn:
        credentials = list_teacher_smart_classroom_credentials(conn, int(user["id"]))

    return templates.TemplateResponse(
        request,
        "manage/system/smart_classroom_integrations.html",
        _build_manage_template_context(
            request,
            user,
            page_title="智慧课堂对接",
            active_page="system_smart_classroom_integrations",
            extra={
                "smart_classroom_profiles": profiles,
                "smart_classroom_credentials": credentials,
            },
        ),
    )


@router.get("/manage/system/users", response_class=HTMLResponse)
async def get_manage_system_users_page(request: Request, user: dict = Depends(get_current_teacher)):
    """教师账号与超管授权管理页面。"""
    with get_db_connection() as conn:
        _ensure_manage_super_admin(conn, user)
        teacher_accounts = list_teacher_accounts(conn)
        teacher_account_summary = build_teacher_account_summary(conn)

    return templates.TemplateResponse(
        request,
        "manage/system/users.html",
        _build_manage_template_context(
            request,
            user,
            page_title="用户管理",
            active_page="system_users",
            extra={
                "teacher_accounts": teacher_accounts,
                "teacher_account_summary": teacher_account_summary,
                "teacher_password_hint": TEACHER_PASSWORD_HINT,
                "initial_super_admin_email": INITIAL_SUPER_ADMIN_EMAIL,
                "initial_super_admin_name": INITIAL_SUPER_ADMIN_NAME,
            },
        ),
    )


@router.get("/manage/system/super-admin", response_class=HTMLResponse)
async def get_manage_system_super_admin_page(request: Request, user: dict = Depends(get_current_teacher)):
    """兼容旧超管设置入口，统一进入用户管理页。"""
    return RedirectResponse(url="/manage/system/users", status_code=302)


@router.get("/manage/system/organizations", response_class=HTMLResponse)
async def get_manage_system_organizations_page(request: Request, user: dict = Depends(get_current_teacher)):
    """学校、学院、系部组织目录管理页面。"""
    with get_db_connection() as conn:
        _ensure_manage_super_admin(conn, user)
        organization_payload = list_organization_tree(conn)
        current_teacher_is_super_admin = is_super_admin_teacher(conn, user["id"])

    return templates.TemplateResponse(
        request,
        "manage/system/organizations.html",
        _build_manage_template_context(
            request,
            user,
            page_title="学校组织",
            active_page="system_organizations",
            extra={
                "organization_payload": organization_payload,
                "current_teacher_is_super_admin": current_teacher_is_super_admin,
            },
        ),
    )


@router.get("/manage/system/feedback", response_class=HTMLResponse)
async def get_manage_system_feedback_page(request: Request, user: dict = Depends(get_current_teacher)):
    """问题反馈查看页面，仅超管教师可查看完整内容。"""
    with get_db_connection() as conn:
        _ensure_manage_super_admin(conn, user)
        current_teacher_is_super_admin = is_super_admin_teacher(conn, user["id"])

        feedback_items = []
        feedback_attachments = {}
        if current_teacher_is_super_admin:
            feedback_items = conn.execute(
                """
                SELECT f.id, f.user_id, f.user_role, f.user_name, f.feedback_type,
                       f.section, f.title, f.description, f.page_url, f.status,
                       f.created_at, f.updated_at,
                       COUNT(a.id) AS attachment_count
                FROM app_feedback f
                LEFT JOIN app_feedback_attachments a ON a.feedback_id = f.id
                GROUP BY f.id
                ORDER BY f.created_at DESC, f.id DESC
                LIMIT 120
                """
            ).fetchall()
            feedback_ids = [int(row["id"]) for row in feedback_items]
            if feedback_ids:
                placeholders = ",".join("?" for _ in feedback_ids)
                attachment_rows = conn.execute(
                    f"""
                    SELECT id, feedback_id, file_hash, original_filename, file_size, mime_type, created_at
                    FROM app_feedback_attachments
                    WHERE feedback_id IN ({placeholders})
                    ORDER BY feedback_id DESC, id ASC
                    """,
                    tuple(feedback_ids),
                ).fetchall()
                for attachment in attachment_rows:
                    feedback_attachments.setdefault(int(attachment["feedback_id"]), []).append(dict(attachment))

    return templates.TemplateResponse(
        request,
        "manage/system/feedback.html",
        _build_manage_template_context(
            request,
            user,
            page_title="问题反馈",
            active_page="system_feedback",
            extra={
                "current_teacher_is_super_admin": current_teacher_is_super_admin,
                "feedback_items": feedback_items,
                "feedback_attachments": feedback_attachments,
            },
        ),
    )


@router.get("/manage/system/diagnostics", response_class=HTMLResponse)
async def get_manage_system_diagnostics_page(request: Request, user: dict = Depends(get_current_teacher)):
    """压测与诊断页面，展示后端健康状态、运行时指标和压测工具。"""
    with get_db_connection() as conn:
        _ensure_manage_super_admin(conn, user)
    return templates.TemplateResponse(
        request,
        "manage/system/diagnostics.html",
        _build_manage_template_context(
            request,
            user,
            page_title="压测与诊断",
            active_page="system_diagnostics",
        ),
    )


@router.get("/manage/system/agent-keys", response_class=HTMLResponse)
async def get_manage_system_agent_keys_page(request: Request, user: dict = Depends(get_current_teacher)):
    """Agent runtime API key management page."""
    with get_db_connection() as conn:
        _ensure_manage_super_admin(conn, user)
        dashboard = build_agent_key_dashboard(conn)

    return templates.TemplateResponse(
        request,
        "manage/system/agent_keys.html",
        _build_manage_template_context(
            request,
            user,
            page_title="Agent Key 管理",
            active_page="system_agent_keys",
            extra={
                "agent_key_dashboard": dashboard,
            },
        ),
    )


@router.get("/manage/system/blog-crawler", response_class=HTMLResponse)
async def get_manage_system_blog_crawler_page(request: Request, user: dict = Depends(get_current_teacher)):
    """AI blog news crawler management page."""
    with get_db_connection() as conn:
        _ensure_manage_super_admin(conn, user)
        dashboard = load_blog_news_crawler_dashboard(conn)
        current_teacher_is_super_admin = is_super_admin_teacher(conn, user["id"])

    return templates.TemplateResponse(
        request,
        "manage/system/blog_crawler.html",
        _build_manage_template_context(
            request,
            user,
            page_title="AI博客管家",
            active_page="system_blog_crawler",
            extra={
                "crawler_dashboard": dashboard,
                "current_teacher_is_super_admin": current_teacher_is_super_admin,
            },
        ),
    )


@router.get("/manage/system/password-resets", response_class=HTMLResponse)
async def get_manage_system_password_resets_page(request: Request, user: dict = Depends(get_current_teacher)):
    """学生找回密码申请审核页面。"""
    with get_db_connection() as conn:
        system_summary = conn.execute(
            """
            SELECT
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending_count,
                SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS approved_count,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_count,
                SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS rejected_count
            FROM student_password_reset_requests
            WHERE ? = 1
               OR teacher_id = ?
               OR class_id IN (
                  SELECT id FROM classes WHERE created_by_teacher_id = ?
               )
               OR class_id IN (
                  SELECT class_id FROM class_offerings WHERE teacher_id = ?
               )
            """,
            (
                1 if is_super_admin_teacher(conn, user["id"]) else 0,
                user["id"],
                user["id"],
                user["id"],
            ),
        ).fetchone()

        login_summary = conn.execute(
            """
            SELECT
                COUNT(*) AS total_logins,
                SUM(CASE WHEN date(logged_at) = date('now', 'localtime') THEN 1 ELSE 0 END) AS today_logins
            FROM student_login_audit_logs logs
            JOIN students s ON s.id = logs.student_id
            JOIN classes c ON c.id = s.class_id
            WHERE ? = 1
               OR c.created_by_teacher_id = ?
               OR EXISTS (
                    SELECT 1 FROM class_offerings o
                    WHERE o.class_id = c.id
                      AND o.teacher_id = ?
               )
            """,
            (1 if is_super_admin_teacher(conn, user["id"]) else 0, user["id"], user["id"]),
        ).fetchone()

        reset_requests = conn.execute(
            """
            SELECT r.id, r.status, r.submitted_at, r.reviewed_at, r.completed_at,
                   s.name AS student_name,
                   s.student_id_number,
                   c.name AS class_name,
                   (
                       SELECT COUNT(*)
                       FROM student_login_audit_logs logs
                       WHERE logs.student_id = s.id
                   ) AS total_logins,
                   (
                       SELECT MAX(logged_at)
                       FROM student_login_audit_logs logs
                       WHERE logs.student_id = s.id
                   ) AS last_login_at
            FROM student_password_reset_requests r
            JOIN students s ON s.id = r.student_id
            JOIN classes c ON c.id = r.class_id
            WHERE ? = 1
               OR r.teacher_id = ?
               OR c.created_by_teacher_id = ?
               OR EXISTS (
                    SELECT 1 FROM class_offerings o
                    WHERE o.class_id = r.class_id
                      AND o.teacher_id = ?
               )
            ORDER BY
                CASE r.status
                    WHEN 'pending' THEN 0
                    WHEN 'approved' THEN 1
                    WHEN 'completed' THEN 2
                    ELSE 3
                END,
                r.submitted_at DESC,
                r.id DESC
            """,
            (
                1 if is_super_admin_teacher(conn, user["id"]) else 0,
                user["id"],
                user["id"],
                user["id"],
            ),
        ).fetchall()

    return templates.TemplateResponse(
        request,
        "manage/system/password_resets.html",
        _build_manage_template_context(
            request,
            user,
            page_title="找回密码申请",
            active_page="system_password_resets",
            extra={
                "system_summary": dict(system_summary) if system_summary else {},
                "login_summary": dict(login_summary) if login_summary else {},
                "reset_requests": reset_requests,
            },
        ),
    )
