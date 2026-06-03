from .common import *


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
        )

    current_search = str(dashboard_context.get("dashboard_initial_search") or "")
    for item in dashboard_context.get("dashboard_filters", []):
        params: dict[str, str] = {}
        filter_value = str(item.get("value") or "all")
        if filter_value and filter_value != "all":
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
