from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from ..dependencies import active_sessions, get_current_user, invalidate_session_for_user

router = APIRouter()


@router.get("/api/session/active")
async def get_active_sessions(user: dict = Depends(get_current_user)):
    """获取当前活跃会话，仅教师可访问。"""
    if user.get("role") != "teacher":
        raise HTTPException(status_code=403, detail="仅教师可查看活跃会话")

    return {
        "status": "success",
        "active_sessions": active_sessions,
        "total_sessions": len(active_sessions),
    }


@router.post("/api/session/invalidate/{user_id}")
async def invalidate_session(
    user_id: str,
    role: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    """强制指定用户下线，仅教师可访问。"""
    if user.get("role") != "teacher":
        raise HTTPException(status_code=403, detail="仅教师可强制下线用户")

    normalized_role = role.strip().lower() if role else None
    if normalized_role and normalized_role not in {"student", "teacher"}:
        raise HTTPException(status_code=400, detail="role 仅支持 student 或 teacher")

    if not normalized_role:
        matching_roles = {
            str(session.get("role")).strip().lower()
            for session in active_sessions.values()
            if str(session.get("user_id")) == str(user_id) and session.get("role")
        }
        if len(matching_roles) > 1:
            raise HTTPException(status_code=409, detail="该 user_id 同时存在学生和教师会话，请指定 role")
        normalized_role = next(iter(matching_roles), None)

    if invalidate_session_for_user(user_id, normalized_role):
        return {"status": "success", "message": f"用户 {user_id} 已被强制下线"}

    return {"status": "error", "message": "用户没有活跃会话"}


@router.get("/api/session/my-info")
async def get_my_session_info(request: Request, user: dict = Depends(get_current_user)):
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
