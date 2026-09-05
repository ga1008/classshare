from .common import *
from fastapi import Query
from typing import Literal


router = APIRouter()


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    filter: Optional[str] = None,
    q: Optional[str] = None,
    search: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    """V4.0: 仪表盘，显示用户所有相关的 "班级课堂" """
    with get_db_connection() as conn:
        dashboard_context = build_dashboard_context(
            conn,
            user,
            initial_filter=filter,
            initial_search=q if q is not None else search,
            include_workspace=True,
        )

    current_search = str(dashboard_context.get("dashboard_initial_search") or "")
    for item in dashboard_context.get("dashboard_filters", []):
        params: dict[str, str] = {}
        filter_value = str(item.get("value") or "all")
        if filter_value:
            params["filter"] = filter_value
        if current_search:
            params["q"] = current_search
        item["href"] = "/dashboard" if not params else f"/dashboard?{urlencode(params)}"

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "request": request,
            "user_info": user,
            **dashboard_context,
        },
    )


@router.get("/api/dashboard/workspace", response_class=JSONResponse)
def dashboard_workspace(
    offering_id: int = Query(0, ge=0),
    offering_ids: str = Query("", max_length=4000, pattern=r"^(\s*\d+\s*(,\s*\d+\s*)*)?$"),
    kind: Literal["all", "class", "exam", "invigilation", "assignment", "exam_task", "stage", "manual", "material", "review", "teacher_work", "poll"] = "all",
    date_scope: Literal["all", "today", "upcoming", "overdue", "undated", "history", "this_week", "next_seven_days"] = "all",
    status: Literal["all", "actionable", "completed"] = "all",
    q: str = Query("", max_length=200),
    cursor: str = Query("", max_length=4096),
    offset: int = Query(0, ge=0, le=10000),
    limit: int = Query(100, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    """Read one bounded page of the caller's facts, without homepage side effects."""
    from ...services.dashboard_workspace_service import load_dashboard_workspace, WorkspaceCursorError

    if user.get("role") not in {"student", "teacher"}:
        raise HTTPException(status_code=403, detail="当前账号不能读取首页事项")
    try:
        with get_db_connection() as conn:
            workspace = load_dashboard_workspace(
                conn, user=user, offering_id=offering_id, kind=kind,
                offering_ids={int(value) for value in offering_ids.split(",") if value.strip().isdigit()} if offering_ids else None,
                date_scope=date_scope, status=status, keyword=q, cursor=cursor,
                offset=offset, limit=limit,
            )
    except WorkspaceCursorError as exc:
        raise HTTPException(status_code=409 if exc.expired else 400, detail=str(exc)) from exc
    return {"status": "success", "workspace": workspace}


@router.get("/api/dashboard/calendar", response_class=JSONResponse)
def dashboard_calendar(user: dict = Depends(get_current_user)):
    """Refresh all authorized calendar facts without building the dashboard."""
    from ...services.dashboard_calendar_service import load_dashboard_calendar

    if user.get("role") not in {"student", "teacher"}:
        raise HTTPException(status_code=403, detail="当前账号不能读取首页日历")
    with get_db_connection() as conn:
        calendar = load_dashboard_calendar(conn, user=user)
    return {"status": "success", "calendar": calendar}


@router.get("/api/dashboard/course-schedule/overview", response_class=JSONResponse)
def dashboard_student_course_schedule(
    year: str = Query("", max_length=32),
    term: str = Query("", max_length=8),
    user: dict = Depends(get_current_user),
):
    """Only the active student's authorized platform sessions; no academic sync."""
    from ...services.student_course_schedule_service import build_student_course_schedule_overview

    if user.get("role") != "student":
        raise HTTPException(status_code=403, detail="当前账号不能读取学生课表")
    with get_db_connection() as conn:
        overview = build_student_course_schedule_overview(
            conn, int(user["id"]), year=year.strip(), term=term.strip(),
        )
    return {"status": "success", "overview": overview}
