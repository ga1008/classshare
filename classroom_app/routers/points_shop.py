"""学分币商店页面与兑换接口。

- ``GET /points``：学分币主页（余额/赚取规则/商品/流水，仅学生）。
- ``GET /api/points``：同源 JSON。
- ``POST /api/points/redeem``：兑换商品（效果成功才扣费，失败整体回滚）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from ..core import templates
from ..database import get_db_connection
from ..dependencies import get_current_user
from ..services.student_points_service import build_points_home, redeem_shop_item

router = APIRouter()


class RedeemRequest(BaseModel):
    item_key: str = Field(min_length=1, max_length=64)


def _ensure_student(user: dict) -> None:
    if str(user.get("role") or "").strip().lower() != "student":
        raise HTTPException(status_code=403, detail="学分币商店仅面向学生本人开放。")


@router.get("/points", response_class=HTMLResponse)
async def points_page(request: Request, user: dict = Depends(get_current_user)):
    _ensure_student(user)
    with get_db_connection() as conn:
        points_home = build_points_home(conn, int(user["id"]))
        conn.commit()
    return templates.TemplateResponse(
        request,
        "points_shop.html",
        {
            "request": request,
            "user_info": user,
            "points_home": points_home,
        },
    )


@router.get("/api/points", response_class=JSONResponse)
async def api_points(user: dict = Depends(get_current_user)):
    _ensure_student(user)
    with get_db_connection() as conn:
        points_home = build_points_home(conn, int(user["id"]))
        conn.commit()
    return {"status": "success", "points_home": points_home}


@router.post("/api/points/redeem", response_class=JSONResponse)
async def api_redeem(payload: RedeemRequest, user: dict = Depends(get_current_user)):
    _ensure_student(user)
    with get_db_connection() as conn:
        result = redeem_shop_item(conn, int(user["id"]), payload.item_key)
        if result.get("ok"):
            conn.commit()
        else:
            conn.rollback()
    if not result.get("ok"):
        return JSONResponse({"status": "failed", "message": result.get("message", "兑换失败")}, status_code=400)
    return {"status": "success", "message": result.get("message", ""), "balance": result.get("balance")}
