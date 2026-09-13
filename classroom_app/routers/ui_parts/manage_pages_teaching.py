"""管理中心 · 教学域页面路由（由 manage_pages.py 按域拆出，函数体未改）。"""
from .common import *
from datetime import timedelta
from ...db.connection import get_configured_db_engine
from ...dependencies import require_teacher_domain
from ...services.ai_usage_budget_service import build_ai_usage_dashboard
from ...services.offering_hub_service import build_offering_hub_context
from ...services.profile_service import build_profile_page_context
from .manage_pages_shared import _table_has_column, _password_reset_login_summary_sql, _academic_event_label


router = APIRouter()


@router.get("/manage/teaching/workflow", response_class=HTMLResponse)
async def manage_workflow_page(request: Request, user: dict = Depends(require_teacher_domain("teaching"))):
    with get_db_connection() as conn:
        workflow_snapshot = _build_classroom_opening_workflow_snapshot(conn, int(user["id"]))

    return templates.TemplateResponse(
        request,
        "manage/workflow.html",
        _build_manage_template_context(
            request,
            user,
            page_title="开课向导",
            active_page="workflow",
            extra={
                "workflow_snapshot": workflow_snapshot,
            },
        ),
    )


@router.get("/manage/teaching/classes", response_class=HTMLResponse)
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
        has_class_kind = _table_has_column(conn, "classes", "class_kind")
        class_kind_select = "c.class_kind" if has_class_kind else "'administrative' AS class_kind"
        class_kind_group_by = ", c.class_kind" if has_class_kind else ""
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
                   {class_kind_select},
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
                       c.school_code, c.school_name, c.college, c.major{class_kind_group_by},
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
            class_item["class_kind"] = normalize_class_kind(class_item.get("class_kind"))
            class_item["class_kind_label"] = class_kind_label(class_item.get("class_kind"))
            class_item["is_custom_class"] = is_custom_class_kind(class_item.get("class_kind"))
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
        semester_calendar = build_semester_calendar_payload(
            load_teacher_semester_rows(conn, int(user["id"]))
        )

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
        "custom_class_count": sum(1 for item in my_classes if item.get("is_custom_class")),
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
                "academic_sync_semesters": build_academic_sync_semester_options(
                    semester_calendar.get("semesters") or []
                ),
                "academic_sync_default_semester_id": semester_calendar.get("default_semester_id"),
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


@router.get("/manage/teaching/semesters", response_class=HTMLResponse)
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


@router.get("/manage/teaching/offerings", response_class=HTMLResponse)
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


@router.get("/manage/teaching/classroom-hub", response_class=HTMLResponse)
@router.get("/manage/teaching", response_class=HTMLResponse)
@router.get("/manage", response_class=HTMLResponse)
async def get_manage_offering_hub_page(request: Request, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        semester_rows = load_teacher_semester_rows(conn, int(user["id"]))
        my_semesters = [serialize_semester_row(row) for row in semester_rows]
        default_semester_id = choose_default_semester_id(my_semesters)
        my_offerings = _load_teacher_offering_rows(conn, int(user["id"]))
        hub_context = build_offering_hub_context(
            conn,
            int(user["id"]),
            my_offerings,
            my_semesters,
            default_semester_id,
        )
    from ...services.teaching_stage_service import build_teaching_stage
    teaching_stage = build_teaching_stage(
        semesters=my_semesters,
        default_semester_id=default_semester_id,
        hub_stats=hub_context.get("hub_stats") or {},
        hub_todo=hub_context.get("hub_todo") or {},
        hub_bootstrap=hub_context.get("hub_bootstrap"),
        today=china_today(),
    )

    return templates.TemplateResponse(
        request,
        "manage/offering_hub.html",
        _build_manage_template_context(
            request,
            user,
            page_title="课堂管理",
            active_page="offering_hub",
            extra={
                **hub_context,
                "teaching_stage": teaching_stage,
                "default_semester_id": default_semester_id,
            },
        ),
    )


@router.get("/manage/teaching/offering-merge", response_class=HTMLResponse)
async def get_manage_offering_merge_page(request: Request, user: dict = Depends(get_current_teacher)):
    return templates.TemplateResponse(
        request,
        "manage/offering_merge.html",
        _build_manage_template_context(
            request,
            user,
            page_title="课堂合并",
            active_page="offering_merge",
        ),
    )


@router.get("/manage/teaching/ai", response_class=HTMLResponse)
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
