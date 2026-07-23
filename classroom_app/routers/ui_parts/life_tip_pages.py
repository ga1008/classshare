from .common import *
from ...services.life_tip_service import (
    ALLOWED_TIP_STATUSES,
    insert_life_tip,
    list_life_tips_for_manage,
    set_life_tip_status,
)
from ...services.life_tip_generation_service import ALLOWED_CATEGORIES

router = APIRouter()


@router.get("/manage/teaching/life-tips", response_class=HTMLResponse)
async def manage_life_tips_page(request: Request, user: dict = Depends(get_current_teacher)):
    """一言提示治理页（内容资产 · 一言提示）。

    Thin shell：列表/筛选/新增/状态切换全部由 ``static/js/manage_life_tips.js``
    驱动 ``/api/life-tips/manage/*``，与投票管理页同模式。
    """
    return templates.TemplateResponse(
        request,
        "manage/life_tips.html",
        _build_manage_template_context(
            request,
            user,
            page_title="一言提示",
            active_page="life_tips",
            extra={"life_tip_categories": list(ALLOWED_CATEGORIES)},
        ),
    )


@router.get("/api/life-tips/manage/list", response_class=JSONResponse)
async def api_manage_life_tips_list(
    scope: str = "",
    category: str = "",
    status: str = "",
    source_kind: str = "",
    audience: str = "",
    keyword: str = "",
    page: int = 1,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        result = list_life_tips_for_manage(
            conn,
            scope=scope.strip(),
            category=category.strip(),
            status=status.strip(),
            source_kind=source_kind.strip(),
            audience=audience.strip(),
            keyword=keyword,
            page=page,
        )
        conn.commit()
    return {"status": "success", **result}


@router.post("/api/life-tips/manage/create", response_class=JSONResponse)
async def api_manage_life_tips_create(
    tip_text: str = Form(),
    category: str = Form(),
    scope: str = Form(default="school"),
    department: str = Form(default=""),
    audience: str = Form(default="student"),
    user: dict = Depends(get_current_teacher),
):
    text = " ".join(str(tip_text or "").split()).strip()
    if not (10 <= len(text) <= 150):
        raise HTTPException(status_code=400, detail="提示语长度需在 10-150 字之间。")
    if scope not in ("global", "school", "department"):
        raise HTTPException(status_code=400, detail="非法作用域。")
    if scope == "department" and not department.strip():
        raise HTTPException(status_code=400, detail="系部层提示需要填写系部名称。")
    if audience not in ("student", "teacher", "all"):
        raise HTTPException(status_code=400, detail="非法受众。")

    with get_db_connection() as conn:
        school_code = ""
        if scope in ("school", "department"):
            row = conn.execute(
                "SELECT school_code FROM teachers WHERE id = ?",
                (int(user["id"]),),
            ).fetchone()
            school_code = (row["school_code"] if row else "") or "gxufl"
        created = insert_life_tip(
            conn,
            scope=scope,
            school_code=school_code,
            department=department.strip() if scope == "department" else "",
            category=category.strip() or "人生大实话",
            tip_text=text,
            audience=audience,
            source_kind="manual",
            source_ref=f"手工录入·{user.get('name') or ''}",
        )
        conn.commit()
    if not created:
        raise HTTPException(status_code=409, detail="已存在内容相同的提示语。")
    return {"status": "success", "message": "提示语已入库并即刻生效。"}


@router.post("/api/life-tips/manage/{tip_id}/status", response_class=JSONResponse)
async def api_manage_life_tips_status(
    tip_id: int,
    status_value: str = Form(alias="status"),
    user: dict = Depends(get_current_teacher),
):
    if status_value not in ALLOWED_TIP_STATUSES:
        raise HTTPException(status_code=400, detail="非法状态。")
    with get_db_connection() as conn:
        changed = set_life_tip_status(conn, tip_id=int(tip_id), status=status_value)
        conn.commit()
    if not changed:
        raise HTTPException(status_code=404, detail="提示不存在。")
    return {"status": "success", "message": "状态已更新。"}
