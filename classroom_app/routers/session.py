import json
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse

from ..dependencies import get_current_user, active_sessions, invalidate_user_session

router = APIRouter()


@router.get("/api/session/active")
async def get_active_sessions(user: dict = Depends(get_current_user)):
    """获取当前活跃会话（仅限教师访问）"""
    if user.get("role") != "teacher":
        raise HTTPException(status_code=403, detail="仅教师可查看活跃会话")

    # 返回活跃会话信息（可以进一步丰富信息）
    return {
        "status": "success",
        "active_sessions": active_sessions,
        "total_sessions": len(active_sessions)
    }


@router.post("/api/session/invalidate/{user_id}")
async def invalidate_session(user_id: str, user: dict = Depends(get_current_user)):
    """强制使用户会话失效（仅限教师）"""
    if user.get("role") != "teacher":
        raise HTTPException(status_code=403, detail="仅教师可强制下线用户")

    if user_id in active_sessions:
        invalidate_user_session(user_id)
        return {"status": "success", "message": f"用户 {user_id} 已被强制下线"}
    else:
        return {"status": "error", "message": "用户没有活跃会话"}


@router.get("/api/session/my-info")
async def get_my_session_info(request: Request, user: dict = Depends(get_current_user)):
    """获取当前用户的会话信息"""
    from ..dependencies import get_client_ip
    client_ip = get_client_ip(request)

    session_info = {
        "user_id": user.get("id"),
        "user_name": user.get("name"),
        "role": user.get("role"),
        "current_ip": client_ip,
        "login_time": user.get("login_time"),
        "session_active": True
    }

    return {"status": "success", "session_info": session_info}