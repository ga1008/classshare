from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from ..database import get_db_connection
from ..dependencies import (
    get_current_teacher,
    get_current_user,
    invalidate_session_for_user,
    list_active_session_roles_for_user,
    list_active_sessions,
)
from ..services.message_center_service import is_super_admin_teacher

router = APIRouter()


def _require_super_admin_teacher(user: dict) -> None:
    with get_db_connection() as conn:
        if not is_super_admin_teacher(conn, user.get("id")):
            raise HTTPException(status_code=403, detail="仅超管教师可管理系统会话。")


@router.get("/api/session/active")
def get_active_sessions(user: dict = Depends(get_current_teacher)):
    """获取当前活跃会话，仅超管教师可访问。"""
    _require_super_admin_teacher(user)

    sessions = list_active_sessions()

    return {
        "status": "success",
        "active_sessions": sessions,
        "total_sessions": len(sessions),
    }


@router.post("/api/session/invalidate/{user_id}")
def invalidate_session(
    user_id: str,
    role: Optional[str] = None,
    user: dict = Depends(get_current_teacher),
):
    """强制指定用户下线，仅超管教师可访问。"""
    _require_super_admin_teacher(user)

    normalized_role = role.strip().lower() if role else None
    if normalized_role and normalized_role not in {"student", "teacher"}:
        raise HTTPException(status_code=400, detail="role 仅支持 student 或 teacher")

    if not normalized_role:
        matching_roles = list_active_session_roles_for_user(user_id)
        if len(matching_roles) > 1:
            raise HTTPException(status_code=409, detail="该 user_id 同时存在学生和教师会话，请指定 role")
        normalized_role = next(iter(matching_roles), None)

    if invalidate_session_for_user(user_id, normalized_role):
        return {"status": "success", "message": f"用户 {user_id} 已被强制下线"}

    return {"status": "error", "message": "用户没有活跃会话"}


@router.get("/api/session/my-info")
def get_my_session_info(request: Request, user: dict = Depends(get_current_user)):
    """获取当前用户的会话信息。"""
    from ..dependencies import get_client_ip

    client_ip = get_client_ip(request)

    session_info = {
        "user_id": user.get("id"),
        "user_name": user.get("name"),
        "role": user.get("role"),
        "current_ip": client_ip,
        "login_time": user.get("login_time"),
        "session_active": True,
    }

    return {"status": "success", "session_info": session_info}


# ---------------------------------------------------------------- 保持登录设备

@router.get("/api/session/remembered")
def list_my_remembered_devices(request: Request, user: dict = Depends(get_current_user)):
    """当前账号已保持登录的设备（本人可见，IP 已打码）。"""
    from ..services import remember_login_service as remember

    with get_db_connection() as conn:
        items = remember.list_remembered_devices(conn, user, remember.current_selector(request))
        conn.commit()
    return {
        "status": "success",
        "enabled": remember.remember_enabled(),
        "idle_days": remember.remember_idle_days(),
        "max_days": remember.remember_max_days(),
        "items": items,
    }


@router.delete("/api/session/remembered/{token_id}")
def revoke_my_remembered_device(token_id: int, request: Request, user: dict = Depends(get_current_user)):
    """让某台设备不再保持登录；若是当前设备，同时清掉本机凭据。"""
    from fastapi.responses import JSONResponse
    from ..services import remember_login_service as remember

    current = remember.current_selector(request)
    with get_db_connection() as conn:
        items_before = remember.list_remembered_devices(conn, user, current)
        is_current = any(item["id"] == int(token_id) and item["current"] for item in items_before)
        revoked = remember.revoke_device(conn, user, int(token_id))
        conn.commit()
    if not revoked:
        raise HTTPException(status_code=404, detail="该设备已不在保持登录列表中。")
    response = JSONResponse({"status": "success", "message": "已取消该设备的保持登录。", "current": is_current})
    if is_current:
        remember.clear_remember_cookie(response)
    return response


@router.post("/api/session/remembered/revoke-all")
def sign_out_everywhere(request: Request, user: dict = Depends(get_current_user)):
    """退出所有设备：取消全部保持登录，并结束当前会话。"""
    from fastapi.responses import JSONResponse
    from ..dependencies import clear_access_token_cookie
    from ..services import remember_login_service as remember

    role = str(user.get("role") or "")
    invalidate_session_for_user(str(user.get("id")), role, revoke_remembered=True)
    login_url = "/teacher/login" if role == "teacher" else "/student/login"
    response = JSONResponse({"status": "success", "message": "已退出所有设备，请重新登录。", "redirect_to": login_url})
    clear_access_token_cookie(response)
    remember.clear_remember_cookie(response)
    return response
