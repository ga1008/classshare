"""FastAPI dependencies for mini-program bearer-token auth."""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Request, status

from ...db.connection import get_db_connection
from ...services import wechat_mp_service


def extract_bearer_token(request: Request) -> Optional[str]:
    header = request.headers.get("authorization") or ""
    if not header.lower().startswith("bearer "):
        return None
    token = header[7:].strip()
    return token or None


def get_current_mp_user(request: Request) -> dict:
    """Resolve the mp session token into a user dict (401 otherwise)."""
    token = extract_bearer_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录，请重新进入小程序。",
        )
    with get_db_connection() as conn:
        session = wechat_mp_service.resolve_mp_session(conn, token)
        user = wechat_mp_service.load_mp_user(conn, session) if session else None
        conn.commit()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录已过期，请重新进入小程序。",
        )
    return user


def get_current_mp_student(request: Request) -> dict:
    user = get_current_mp_user(request)
    if user.get("role") != "student":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="该接口仅限学生使用。")
    return user


def get_current_mp_teacher(request: Request) -> dict:
    user = get_current_mp_user(request)
    if user.get("role") != "teacher":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="该接口仅限教师使用。")
    return user
