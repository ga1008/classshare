from __future__ import annotations

import html
import re
import sqlite3
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from ..core import templates
from ..database import get_db_connection
from ..dependencies import get_current_teacher, get_current_user, get_password_hash, verify_password
from ..services.emoji_service import get_custom_emoji_path, validate_and_store_custom_emoji
from ..services.profile_service import (
    build_profile_page_context,
    get_user_profile,
    normalize_profile_section,
    update_basic_profile,
    update_profile_avatar,
    update_profile_mood,
)
from ..services.portfolio_service import (
    add_portfolio_item,
    build_student_portfolio_context,
    remove_portfolio_item,
    update_portfolio_item,
)
from ..services.email_notification_service import (
    create_teacher_email_config,
    delete_teacher_email_config,
    list_teacher_email_configs,
    test_teacher_email_config,
    update_teacher_email_config,
)
from ..services.student_auth_service import get_student_auth_record_by_pk, validate_student_password
from ..services.teacher_account_service import TEACHER_PASSWORD_HINT, validate_teacher_password

router = APIRouter()


def _ensure_student_user(user: dict) -> int:
    if str(user.get("role") or "").strip().lower() != "student":
        raise HTTPException(status_code=403, detail="成长档案目前仅面向学生本人开放。")
    try:
        return int(user["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=403, detail="当前学生身份无效。") from exc


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


# 「我的」域在壳内的 section → 导航 key（docs/manage-center-improvement-plan §5.8）。
_SHELL_SECTION_NAV = {
    "overview": "teacher_profile",
    "settings": "me_settings",
    "security": "me_security",
    "notifications": "me_notifications",
    "private": "me_notifications",
    "email": "me_email",
}
_SHELL_SECTION_TITLES = {
    "overview": "我的概览",
    "settings": "基础资料",
    "security": "账号安全",
    "notifications": "通知与私信",
    "private": "私信",
    "email": "邮箱通知",
}


def shell_profile_href(section: str) -> str:
    section = normalize_profile_section(section)
    return "/manage/me" if section == "overview" else f"/manage/me/{section}"


def _render_profile(request: Request, user: dict, *, section: str, tab: str, contact: str, scope: int | None, in_shell: bool):
    active_section = normalize_profile_section(section)
    initial_tab = "private_message" if active_section == "private" else str(tab or "all")
    if active_section == "notifications" and initial_tab == "private_message":
        initial_tab = "all"

    with get_db_connection() as conn:
        profile_context = build_profile_page_context(conn, user, active_section)
        active_section = profile_context["active_section"]

    nav_items = profile_context["nav_items"]
    if in_shell:
        nav_items = [{**item, "href": shell_profile_href(item["section"])} for item in nav_items]
        profile_context = {**profile_context, "nav_items": nav_items}

    context = {
        "request": request,
        "user_info": user,
        "page_title": _SHELL_SECTION_TITLES.get(active_section, "个人中心") if in_shell else "个人中心",
        "profile_context": profile_context,
        "profile": profile_context["profile"],
        "overview": profile_context["overview"],
        "portfolio": profile_context.get("portfolio"),
        "nav_items": nav_items,
        "active_section": active_section,
        "initial_tab": initial_tab,
        "initial_contact": str(contact or ""),
        "initial_scope": scope,
        "profile_in_shell": in_shell,
        "profile_section_base": "/manage/me/" if in_shell else "/profile?section=",
    }
    if in_shell:
        from .ui_parts.common import _build_manage_template_context

        shell = _build_manage_template_context(
            request, user, page_title=context["page_title"], active_page=_SHELL_SECTION_NAV.get(active_section, "teacher_profile")
        )
        context = {**shell, **context}
    return templates.TemplateResponse(request, "profile.html", context)


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(
    request: Request,
    section: str = "overview",
    tab: str = "all",
    contact: str = "",
    scope: int | None = None,
    user: dict = Depends(get_current_user),
):
    if str(user.get("role") or "") == "teacher":
        # 教师的「我」统一住在工作台壳内；学生路径不变。
        query = request.url.query
        target = shell_profile_href(section)
        params = "&".join(part for part in query.split("&") if part and not part.startswith("section="))
        return RedirectResponse(url=f"{target}?{params}" if params else target, status_code=302)
    return _render_profile(request, user, section=section, tab=tab, contact=contact, scope=scope, in_shell=False)


def _shell_section_endpoint(section_name: str):
    async def manage_me_section_page(
        request: Request,
        tab: str = "all",
        contact: str = "",
        scope: int | None = None,
        user: dict = Depends(get_current_teacher),
    ):
        return _render_profile(request, user, section=section_name, tab=tab, contact=contact, scope=scope, in_shell=True)

    manage_me_section_page.__name__ = f"manage_me_{section_name}_page"
    return manage_me_section_page


# 显式注册每个 section（路由快照与导航契约都按字面路径校验，不用 {section} 通配）。
router.add_api_route("/manage/me", _shell_section_endpoint("overview"), methods=["GET"], response_class=HTMLResponse, name="manage_me_overview_page")
for _section in ("settings", "security", "notifications", "private", "email"):
    router.add_api_route(f"/manage/me/{_section}", _shell_section_endpoint(_section), methods=["GET"], response_class=HTMLResponse, name=f"manage_me_{_section}_page")


@router.get("/api/profile/bootstrap", response_class=JSONResponse)
def api_profile_bootstrap(section: str = "overview", user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        return {
            "status": "success",
            **build_profile_page_context(conn, user, section),
        }


@router.get("/api/profile/portfolio", response_class=JSONResponse)
def api_profile_portfolio(user: dict = Depends(get_current_user)):
    student_id = _ensure_student_user(user)
    with get_db_connection() as conn:
        return {
            "status": "success",
            "portfolio": build_student_portfolio_context(conn, student_id, include_candidates=True),
        }


@router.post("/api/profile/portfolio/items", response_class=JSONResponse)
async def api_add_profile_portfolio_item(request: Request, user: dict = Depends(get_current_user)):
    student_id = _ensure_student_user(user)
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="作品数据格式不正确。")
    with get_db_connection() as conn:
        try:
            result = add_portfolio_item(
                conn,
                student_id,
                source_type=str(data.get("source_type") or ""),
                source_id=data.get("source_id"),
                featured=bool(data.get("featured")),
            )
            portfolio = build_student_portfolio_context(conn, student_id, include_candidates=True)
            conn.commit()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "success",
        "message": "作品已收入成长档案。",
        **result,
        "portfolio": portfolio,
    }


@router.put("/api/profile/portfolio/items/{item_id}", response_class=JSONResponse)
async def api_update_profile_portfolio_item(
    item_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
):
    student_id = _ensure_student_user(user)
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="作品数据格式不正确。")
    with get_db_connection() as conn:
        try:
            result = update_portfolio_item(conn, student_id, item_id, data)
            portfolio = build_student_portfolio_context(conn, student_id, include_candidates=True)
            conn.commit()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "success",
        "message": "成长档案已更新。",
        **result,
        "portfolio": portfolio,
    }


@router.delete("/api/profile/portfolio/items/{item_id}", response_class=JSONResponse)
def api_remove_profile_portfolio_item(item_id: int, user: dict = Depends(get_current_user)):
    student_id = _ensure_student_user(user)
    with get_db_connection() as conn:
        try:
            removed_count = remove_portfolio_item(conn, student_id, item_id)
            portfolio = build_student_portfolio_context(conn, student_id, include_candidates=True)
            conn.commit()
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "status": "success",
        "message": "作品已移出成长档案。",
        "removed_count": removed_count,
        "portfolio": portfolio,
    }


@router.get("/api/profile/email-configs", response_class=JSONResponse)
def api_list_teacher_email_configs(user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        return {
            "status": "success",
            "configs": list_teacher_email_configs(conn, int(user["id"])),
        }


@router.post("/api/profile/email-configs", response_class=JSONResponse)
async def api_create_teacher_email_config(request: Request, user: dict = Depends(get_current_teacher)):
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="邮箱配置格式不正确。")
    with get_db_connection() as conn:
        try:
            config = create_teacher_email_config(conn, int(user["id"]), data)
            configs = list_teacher_email_configs(conn, int(user["id"]))
            conn.commit()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "success",
        "message": "邮箱配置已保存。",
        "config": config,
        "configs": configs,
    }


@router.put("/api/profile/email-configs/{config_id}", response_class=JSONResponse)
async def api_update_teacher_email_config(
    config_id: int,
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="邮箱配置格式不正确。")
    with get_db_connection() as conn:
        try:
            config = update_teacher_email_config(conn, int(user["id"]), config_id, data)
            configs = list_teacher_email_configs(conn, int(user["id"]))
            conn.commit()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "success",
        "message": "邮箱配置已更新。",
        "config": config,
        "configs": configs,
    }


@router.delete("/api/profile/email-configs/{config_id}", response_class=JSONResponse)
def api_delete_teacher_email_config(config_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        try:
            removed_count = delete_teacher_email_config(conn, int(user["id"]), config_id)
            configs = list_teacher_email_configs(conn, int(user["id"]))
            conn.commit()
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "status": "success",
        "message": "邮箱配置已删除。",
        "removed_count": removed_count,
        "configs": configs,
    }


@router.post("/api/profile/email-configs/{config_id}/test", response_class=JSONResponse)
async def api_test_teacher_email_config(
    config_id: int,
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    data = await request.json()
    mode = str(data.get("mode") if isinstance(data, dict) else "smtp").strip() or "smtp"
    with get_db_connection() as conn:
        try:
            result = test_teacher_email_config(conn, int(user["id"]), config_id, mode=mode)
            configs = list_teacher_email_configs(conn, int(user["id"]))
            conn.commit()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "success" if result["status"] == "ok" else "failed",
        "result": result,
        "configs": configs,
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


@router.get("/api/profile/identities", response_class=JSONResponse)
async def api_list_profile_identities(user: dict = Depends(get_current_user)):
    from ..services import signature_identity_service

    with get_db_connection() as conn:
        items = signature_identity_service.list_identity_appointments(
            conn, str(user.get("role") or ""), user.get("id")
        )
    return {
        "items": items,
        "options": signature_identity_service.identity_options(),
    }


@router.put("/api/profile/identities", response_class=JSONResponse)
async def api_update_profile_identities(request: Request, user: dict = Depends(get_current_user)):
    from ..services import signature_identity_service

    data = await request.json()
    raw_items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(raw_items, list):
        raise HTTPException(status_code=400, detail="任职身份格式不正确。")
    with get_db_connection() as conn:
        try:
            items = signature_identity_service.set_identity_appointments(
                conn, str(user.get("role") or ""), user.get("id"), raw_items
            )
            conn.commit()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "success", "message": "任职身份已保存。", "items": items}


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
    from ..services.account_credentials_service import credentials_changed, prepare_credentials_change
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
        prepare_credentials_change(conn, role=role, user_id=user_id)
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
            credentials_changed(conn, role="student", user_id=user_id)
            conn.execute(
                """
                UPDATE students
                SET hashed_password = ?, password_updated_at = ?, password_reset_required = 0
                WHERE id = ?
                """,
                (get_password_hash(new_password), now_value, user_id),
            )
        else:
            try:
                validate_teacher_password(new_password)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc) or TEACHER_PASSWORD_HINT) from exc
            teacher_row = conn.execute(
                "SELECT id, hashed_password FROM teachers WHERE id = ? LIMIT 1",
                (user_id,),
            ).fetchone()
            if not teacher_row:
                raise HTTPException(status_code=404, detail="教师账号不存在。")
            if not verify_password(current_password, teacher_row["hashed_password"]):
                raise HTTPException(status_code=400, detail="当前密码错误。")
            credentials_changed(conn, role="teacher", user_id=user_id)
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
