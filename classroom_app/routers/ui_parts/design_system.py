"""Opt-in component laboratory. No application data or actions live here."""
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from ...core import templates
from ...dependencies import get_current_user_optional, validate_authenticated_user_identity

router = APIRouter()


@router.get("/dev/lq", response_class=HTMLResponse, include_in_schema=False)
async def liquid_glass_preview(request: Request, user=Depends(get_current_user_optional)):
    if os.getenv("LANSHARE_LQ_PREVIEW", "").strip().lower() not in {"1", "true", "yes", "on"}:
        raise HTTPException(status_code=404)
    if not user or user.get("role") != "teacher":
        raise HTTPException(status_code=403, detail="仅教师可访问组件预览")
    user = validate_authenticated_user_identity(user)
    layout = request.query_params.get("layout")
    shell = request.query_params.get("shell")
    layouts = {"list": "列表工作台", "dashboard": "概览", "detail": "详情", "editor": "三栏编辑器", "take": "作答工作区", "immersive": "沉浸活动", "reading": "阅读"}
    if layout is not None and layout not in layouts:
        raise HTTPException(status_code=404)
    if shell is not None and (shell != "centered" or layout is not None):
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request, "dev/lq_centered.html" if shell else "dev/lq_shell.html" if layout else "dev/lq.html",
        {"request": request, "user_info": user, "preview_layout": layout, "preview_layouts": layouts},
        headers={"Cache-Control": "no-store"},
    )
