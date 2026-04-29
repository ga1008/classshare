from __future__ import annotations

import html
import re
import sqlite3
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from ..core import templates
from ..database import get_db_connection
from ..dependencies import get_current_user, get_password_hash, verify_password
from ..services.emoji_service import get_custom_emoji_path, validate_and_store_custom_emoji
from ..services.profile_service import (
    build_profile_page_context,
    get_user_profile,
    normalize_profile_section,
    update_basic_profile,
    update_profile_avatar,
    update_profile_mood,
)
from ..services.student_auth_service import get_student_auth_record_by_pk, validate_student_password

router = APIRouter()


def _build_avatar_text(profile: dict[str, Any]) -> str:
    source = re.sub(r"\s+", "", str(profile.get("name") or profile.get("nickname") or profile.get("role_label") or "用户"))
    if not source:
        return "用户"

    has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in source)
    if has_cjk:
        if len(source) == 1:
            return source
        if len(source) == 2:
            return source
        return source[-2:]

    return source[:2]


def _build_avatar_svg(profile: dict[str, Any]) -> str:
    initials = html.escape(_build_avatar_text(profile))
    is_teacher = profile.get("role") == "teacher"
    primary = "#0f766e" if is_teacher else "#4f46e5"
    secondary = "#14b8a6" if is_teacher else "#0ea5e9"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" role="img" aria-label="avatar">
<defs><linearGradient id="g" x1="0" x2="1" y1="0" y2="1"><stop stop-color="{primary}"/><stop offset="1" stop-color="{secondary}"/></linearGradient></defs>
<rect width="128" height="128" rx="32" fill="url(#g)"/>
<circle cx="96" cy="28" r="18" fill="#ffffff" opacity=".18"/>
<text x="64" y="76" text-anchor="middle" font-family="Segoe UI, Microsoft YaHei, sans-serif" font-size="38" font-weight="700" fill="#fff">{initials}</text>
</svg>"""


def _load_avatar_profile(conn, *, role: str, user_id: int) -> dict[str, Any] | None:
    normalized_role = str(role or "").strip().lower()
    if normalized_role == "teacher":
        row = conn.execute(
            """
            SELECT id, name, nickname, avatar_file_hash, avatar_mime_type
            FROM teachers
            WHERE id = ?
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
    elif normalized_role == "student":
        row = conn.execute(
            """
            SELECT id, name, nickname, avatar_file_hash, avatar_mime_type
            FROM students
            WHERE id = ?
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
    else:
        return None

    if row is None:
        return None

    return {
        "id": int(row["id"]),
        "role": normalized_role,
        "role_label": "教师" if normalized_role == "teacher" else "学生",
        "name": str(row["name"] or ""),
        "nickname": str(row["nickname"] or ""),
        "avatar_file_hash": str(row["avatar_file_hash"] or ""),
        "avatar_mime_type": str(row["avatar_mime_type"] or ""),
    }


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(
    request: Request,
    section: str = "overview",
    tab: str = "all",
    contact: str = "",
    scope: int | None = None,
    user: dict = Depends(get_current_user),
):
    active_section = normalize_profile_section(section)
    initial_tab = "private_message" if active_section == "private" else str(tab or "all")
    if active_section == "notifications" and initial_tab == "private_message":
        initial_tab = "all"

    with get_db_connection() as conn:
        profile_context = build_profile_page_context(conn, user, active_section)

    return templates.TemplateResponse(
        request,
        "profile.html",
        {
            "request": request,
            "user_info": user,
            "page_title": "个人中心",
            "profile_context": profile_context,
            "profile": profile_context["profile"],
            "overview": profile_context["overview"],
            "nav_items": profile_context["nav_items"],
            "active_section": active_section,
            "initial_tab": initial_tab,
            "initial_contact": str(contact or ""),
            "initial_scope": scope,
        },
    )


@router.get("/api/profile/bootstrap", response_class=JSONResponse)
def api_profile_bootstrap(section: str = "overview", user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        return {
            "status": "success",
            **build_profile_page_context(conn, user, section),
        }


@router.put("/api/profile/basic", response_class=JSONResponse)
async def api_update_basic_profile(request: Request, user: dict = Depends(get_current_user)):
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="资料格式不正确。")

    with get_db_connection() as conn:
        try:
            profile = update_basic_profile(conn, user, data)
            conn.commit()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=400, detail="邮箱已被其他账号使用。") from exc
    return {
        "status": "success",
        "message": "基础信息已保存。",
        "profile": profile,
    }


@router.put("/api/profile/mood", response_class=JSONResponse)
async def api_update_profile_mood(request: Request, user: dict = Depends(get_current_user)):
    data = await request.json()
    mood = data.get("mood") if isinstance(data, dict) else ""
    with get_db_connection() as conn:
        profile = update_profile_mood(conn, user, mood)
        conn.commit()
    return {
        "status": "success",
        "message": "今日心情已更新。",
        "profile": profile,
    }


@router.post("/api/profile/avatar", response_class=JSONResponse)
async def api_upload_profile_avatar(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    stored_file = await validate_and_store_custom_emoji(file)
    with get_db_connection() as conn:
        profile = update_profile_avatar(
            conn,
            user,
            file_hash=stored_file["hash"],
            mime_type=stored_file["mime_type"],
        )
        conn.commit()
    return {
        "status": "success",
        "message": "头像已更新。",
        "profile": profile,
    }


@router.get("/api/profile/avatar")
def api_profile_avatar(
    role: str | None = None,
    user_id: int | None = None,
    user: dict = Depends(get_current_user),
):
    requested_role = str(role or "").strip().lower()
    requested_user_id = int(user_id) if user_id is not None else None

    with get_db_connection() as conn:
        if requested_role in {"teacher", "student"} and requested_user_id is not None:
            profile = _load_avatar_profile(conn, role=requested_role, user_id=requested_user_id)
            if profile is None:
                raise HTTPException(status_code=404, detail="头像用户不存在。")
        else:
            profile = get_user_profile(conn, user)

    file_hash = str(profile.get("avatar_file_hash") or "").strip()
    if file_hash:
        try:
            return FileResponse(
                get_custom_emoji_path(file_hash),
                media_type=str(profile.get("avatar_mime_type") or "application/octet-stream"),
                filename="avatar",
            )
        except HTTPException:
            pass

    return Response(
        content=_build_avatar_svg(profile),
        media_type="image/svg+xml",
        headers={"Cache-Control": "no-store"},
    )


@router.put("/api/profile/password", response_class=JSONResponse)
async def api_change_profile_password(request: Request, user: dict = Depends(get_current_user)):
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="密码格式不正确。")

    current_password = str(data.get("current_password") or "")
    new_password = str(data.get("new_password") or "")
    confirm_password = str(data.get("confirm_password") or "")
    if new_password != confirm_password:
        raise HTTPException(status_code=400, detail="两次输入的新密码不一致。")
    if current_password == new_password:
        raise HTTPException(status_code=400, detail="新密码不能与当前密码相同。")

    role = str(user.get("role") or "").strip().lower()
    user_id = int(user["id"])
    now_value = datetime.now().isoformat()

    with get_db_connection() as conn:
        if role == "student":
            password_error = validate_student_password(new_password)
            if password_error:
                raise HTTPException(status_code=400, detail=password_error)
            student_row = get_student_auth_record_by_pk(conn, user_id)
            if not student_row:
                raise HTTPException(status_code=404, detail="学生账号不存在。")
            if student_row["password_reset_required"]:
                raise HTTPException(status_code=400, detail="当前账号处于重置流程，请重新登录后设置密码。")
            if not student_row["hashed_password"] or not verify_password(current_password, student_row["hashed_password"]):
                raise HTTPException(status_code=400, detail="当前密码错误。")
            conn.execute(
                """
                UPDATE students
                SET hashed_password = ?, password_updated_at = ?, password_reset_required = 0
                WHERE id = ?
                """,
                (get_password_hash(new_password), now_value, user_id),
            )
        else:
            if len(new_password) < 6:
                raise HTTPException(status_code=400, detail="教师密码至少需要 6 位。")
            teacher_row = conn.execute(
                "SELECT id, hashed_password FROM teachers WHERE id = ? LIMIT 1",
                (user_id,),
            ).fetchone()
            if not teacher_row:
                raise HTTPException(status_code=404, detail="教师账号不存在。")
            if not verify_password(current_password, teacher_row["hashed_password"]):
                raise HTTPException(status_code=400, detail="当前密码错误。")
            conn.execute(
                """
                UPDATE teachers
                SET hashed_password = ?, password_updated_at = ?
                WHERE id = ?
                """,
                (get_password_hash(new_password), now_value, user_id),
            )
        conn.commit()

    return {
        "status": "success",
        "message": "密码已更新。",
    }
