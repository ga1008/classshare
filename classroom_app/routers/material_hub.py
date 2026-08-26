"""材料中心（「材料」大类，/manage/library）。

页面：搜索/筛选卡 + 分组结果；左栏由 layout.html 渲染为分类多选按钮
（数据源 ``manage_nav_service.MATERIAL_HUB_CATEGORIES``）。
接口：
- ``GET  /api/materials/hub/search``     普通模糊搜索（标题/内容/属性/标签/归属人/归属层级）
- ``POST /api/materials/hub/ai-search``  AI 理解需求 → 提炼关键词/分类/范围 → 复用同一检索链
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from ..core import templates
from ..database import get_db_connection
from ..dependencies import require_teacher_domain
from ..services.material_hub_service import (
    ai_understand_hub_query,
    normalize_hub_categories,
    search_material_hub,
)
from .ui_parts.common import _build_manage_template_context

router = APIRouter()


@router.get("/manage/library", response_class=HTMLResponse)
async def manage_material_hub_page(
    request: Request,
    user: dict = Depends(require_teacher_domain("library")),
):
    """材料中心页：无首页设计，直接落在搜索/筛选台上。"""
    return templates.TemplateResponse(
        request,
        "manage/material_hub.html",
        _build_manage_template_context(
            request,
            user,
            page_title="材料中心",
            active_page="material_hub",
        ),
    )


@router.get("/api/materials/hub/search", response_class=JSONResponse)
async def material_hub_search(
    q: str = Query(default=""),
    categories: str = Query(default=""),
    scope: str = Query(default="all"),
    user: dict = Depends(require_teacher_domain("library")),
):
    with get_db_connection() as conn:
        result = search_material_hub(
            conn,
            user,
            query=q,
            categories=categories,
            scope_filter=scope,
        )
    return {"status": "success", **result}


class MaterialHubAiSearchRequest(BaseModel):
    query: str = ""
    categories: list[str] | str | None = None
    scope: str = "all"


@router.post("/api/materials/hub/ai-search", response_class=JSONResponse)
async def material_hub_ai_search(
    payload: MaterialHubAiSearchRequest,
    user: dict = Depends(require_teacher_domain("library")),
):
    """AI 搜索：先让快速 AI 理解需求，再执行统一检索；AI 不可用时降级为普通搜索。"""
    query = str(payload.query or "").strip()
    selected = normalize_hub_categories(payload.categories)
    intent = await ai_understand_hub_query(query)

    if intent:
        # AI 给出的分类是「建议范围」，仍受用户勾选约束（交集为空则尊重用户勾选）。
        suggested = [key for key in (intent.get("categories") or []) if key in selected]
        effective_categories = suggested or selected
        effective_query = " ".join(intent.get("keywords") or []) or query
        effective_scope = intent.get("scope") or payload.scope
    else:
        effective_categories = selected
        effective_query = query
        effective_scope = payload.scope

    with get_db_connection() as conn:
        result = search_material_hub(
            conn,
            user,
            query=effective_query,
            categories=effective_categories,
            scope_filter=effective_scope,
        )
    return {
        "status": "success",
        "ai": {
            "used": bool(intent),
            "explanation": (intent or {}).get("explanation") or ("已按 AI 理解的关键词检索" if intent else "AI 暂不可用，已按原文模糊搜索"),
            "keywords": (intent or {}).get("keywords") or [],
            "categories": effective_categories,
            "scope": effective_scope if intent else payload.scope,
        },
        **result,
    }
