"""全局搜索接口。

``GET /api/global-search?q=``：登录用户跨域搜索（课堂/材料/作业考试/博客），
结果按角色圈定可见范围，分组返回。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from ..database import get_db_connection
from ..dependencies import get_current_user
from ..services.global_search_service import search_everything

router = APIRouter()


@router.get("/api/global-search", response_class=JSONResponse)
async def api_global_search(
    q: str = Query(default="", max_length=80),
    user: dict = Depends(get_current_user),
):
    with get_db_connection() as conn:
        payload = search_everything(conn, user, q)
        conn.commit()
    return {"status": "success", **payload}
