from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from ..services.manage_nav_service import iter_manage_legacy_redirects


router = APIRouter(include_in_schema=False)


def _redirect_endpoint(canonical_href: str):
    async def redirect_legacy_manage_url(request: Request) -> RedirectResponse:
        query = request.url.query
        target = f"{canonical_href}?{query}" if query else canonical_href
        return RedirectResponse(url=target, status_code=301)

    return redirect_legacy_manage_url


for item in iter_manage_legacy_redirects():
    route_name = f"redirect_manage_legacy_{item['key']}"
    router.add_api_route(
        item["legacy_href"],
        _redirect_endpoint(item["canonical_href"]),
        methods=["GET"],
        name=route_name,
    )
