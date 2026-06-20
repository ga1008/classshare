from .common import *


router = APIRouter()


@router.get("/manage/teaching/polls", response_class=HTMLResponse)
@router.get("/manage/polls", response_class=HTMLResponse)
async def manage_polls_page(request: Request, user: dict = Depends(get_current_teacher)):
    """投票活动管理页面（内容资产 · 投票）。

    The page is a thin shell — the poll list, create/edit dialogs and class
    assignment picker are driven client-side by ``static/js/manage_polls.js``
    against the ``/api/polls/manage/*`` endpoints, mirroring how other content
    asset pages defer their interactive data loading to the frontend.
    """
    return templates.TemplateResponse(
        request,
        "manage/polls.html",
        _build_manage_template_context(
            request,
            user,
            page_title="投票管理",
            active_page="polls",
        ),
    )
