"""个人日历订阅路由。

- ``GET /api/calendar-feed``：登录后获取（必要时签发）本人订阅链接。
- ``POST /api/calendar-feed/reset``：重置 token，旧链接立即失效。
- ``GET /calendar/feed/{token}.ics``：日历 App 拉取的公开只读 feed；
  token 即凭证，不依赖 Cookie（日历客户端不带会话）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from ..config import PUBLIC_SITE_BASE_URL
from ..database import get_db_connection
from ..dependencies import get_current_user
from ..services.calendar_feed_service import (
    build_ics_for_user,
    get_or_create_feed_token,
    reset_feed_token,
    resolve_feed_token,
)

router = APIRouter(tags=["calendar-feed"])


def _feed_urls(token: str) -> dict[str, str]:
    path = f"/calendar/feed/{token}.ics"
    base = str(PUBLIC_SITE_BASE_URL or "").rstrip("/")
    absolute = f"{base}{path}" if base else path
    # webcal:// 让 iOS/macOS 点开即弹"订阅日历"。
    webcal = absolute.replace("https://", "webcal://").replace("http://", "webcal://")
    return {"feed_path": path, "feed_url": absolute, "webcal_url": webcal}


@router.get("/api/calendar-feed")
async def get_calendar_feed_info(user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        token = get_or_create_feed_token(
            conn, role=str(user.get("role") or ""), user_pk=int(user["id"])
        )
        conn.commit()
    return {"status": "success", **_feed_urls(token)}


@router.post("/api/calendar-feed/reset")
async def reset_calendar_feed(user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        token = reset_feed_token(
            conn, role=str(user.get("role") or ""), user_pk=int(user["id"])
        )
        conn.commit()
    return {"status": "success", **_feed_urls(token)}


@router.get("/calendar/feed/{token}.ics")
async def calendar_feed(token: str):
    with get_db_connection() as conn:
        identity = resolve_feed_token(conn, token)
        if identity is None:
            raise HTTPException(404, "订阅链接不存在或已被重置")
        ics_text = build_ics_for_user(
            conn, role=identity["role"], user_pk=identity["user_pk"]
        )
        conn.commit()
    return Response(
        content=ics_text,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": 'inline; filename="lanshare-schedule.ics"',
            "Cache-Control": "private, max-age=300",
        },
    )
