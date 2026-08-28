"""小程序认证端点：微信 code 登录、首绑、会话查询与登出。

Flow:
- POST /api/mp/auth/login  {code}
    → bound openid: issue bearer token + user + login_tip (welcome screen)
    → unknown openid: {status: "need_bind", bind_ticket}
- POST /api/mp/auth/bind/student  {bind_ticket, name, student_id_number}
- POST /api/mp/auth/bind/teacher  {bind_ticket, email, password}
- GET  /api/mp/auth/me
- POST /api/mp/auth/logout
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ...db.connection import get_db_connection
from ...dependencies import get_client_ip, verify_password
from ...services import wechat_mp_service
from ...services.life_tip_service import (
    build_login_tip_payload_for_student,
    build_login_tip_payload_for_teacher,
)
from ...services.student_auth_service import (
    get_student_auth_record_by_identity,
    record_student_login,
)
from ...services.student_lifecycle_service import (
    STUDENT_STATUS_ACTIVE,
    normalize_student_enrollment_status,
    student_enrollment_status_label,
)
from .deps import extract_bearer_token, get_current_mp_user

router = APIRouter(prefix="/auth")


class MpLoginRequest(BaseModel):
    code: str


class MpStudentBindRequest(BaseModel):
    bind_ticket: str
    name: str
    student_id_number: str


class MpTeacherBindRequest(BaseModel):
    bind_ticket: str
    email: str
    password: str


def _ensure_student_active(student_row) -> None:
    status_value = (
        student_row["enrollment_status"]
        if "enrollment_status" in student_row.keys()
        else STUDENT_STATUS_ACTIVE
    )
    normalized = normalize_student_enrollment_status(status_value)
    if normalized != STUDENT_STATUS_ACTIVE:
        raise HTTPException(
            status_code=403,
            detail=f"该学生已设置为{student_enrollment_status_label(normalized)}，暂不纳入课堂学习。",
        )


def _absolutize_tip_images(login_tip: Optional[dict], base_url: str) -> Optional[dict]:
    """小程序不共享站点 origin，登录载荷里的 /static/... 图片补成绝对地址。"""
    if not login_tip or not isinstance(login_tip.get("tips"), list):
        return login_tip
    prefix = base_url.rstrip("/")
    tips = []
    for tip in login_tip["tips"]:
        image_url = tip.get("image_url")
        if isinstance(image_url, str) and image_url.startswith("/"):
            tip = {**tip, "image_url": f"{prefix}{image_url}"}
        tips.append(tip)
    return {**login_tip, "tips": tips}


def _build_login_success_payload(
    conn: Any,
    *,
    user: dict,
    token: str,
    base_url: str,
) -> dict:
    login_tip = None
    try:
        if user["role"] == "student":
            login_tip = build_login_tip_payload_for_student(conn, int(user["id"]))
        else:
            login_tip = build_login_tip_payload_for_teacher(conn, int(user["id"]))
    except Exception as exc:
        # 提示语失败绝不阻断登录（与 Web 端约定一致）。
        print(f"[WECHAT_MP] 登录提示加载失败: {exc}")
    return {
        "success": True,
        "data": {
            "status": "success",
            "token": token,
            "user": user,
            "login_tip": _absolutize_tip_images(login_tip, base_url),
        },
        "error": None,
    }


def _need_bind_payload(openid: str, unionid: str) -> dict:
    return {
        "success": True,
        "data": {
            "status": "need_bind",
            "bind_ticket": wechat_mp_service.build_bind_ticket(openid, unionid),
        },
        "error": None,
    }


@router.post("/login")
def mp_login(request: Request, payload: MpLoginRequest):
    """微信静默登录：已绑定直接发 token，未绑定发绑定票据。"""
    try:
        identity = wechat_mp_service.exchange_code_for_openid(payload.code)
    except wechat_mp_service.WechatMpError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    openid = identity["openid"]
    unionid = identity.get("unionid", "")
    base_url = str(request.base_url)
    with get_db_connection() as conn:
        binding = wechat_mp_service.find_active_binding(conn, openid)
        if not binding:
            conn.commit()
            return _need_bind_payload(openid, unionid)

        user = wechat_mp_service.load_mp_user(
            conn, {"user_role": binding["user_role"], "user_pk": binding["user_pk"]}
        )
        if not user:
            # 账号已被删除/停用 → 撤销孤儿绑定，走重新绑定流程。
            wechat_mp_service.revoke_binding(
                conn, user_role=binding["user_role"], user_pk=binding["user_pk"]
            )
            conn.commit()
            return _need_bind_payload(openid, unionid)

        token = wechat_mp_service.issue_mp_session(
            conn, user_role=user["role"], user_pk=int(user["id"]), openid=openid
        )
        wechat_mp_service.touch_binding_login(conn, openid)
        result = _build_login_success_payload(conn, user=user, token=token, base_url=base_url)
        conn.commit()
    return result


@router.post("/bind/student")
def mp_bind_student(request: Request, payload: MpStudentBindRequest):
    """学生首次绑定：学号 + 姓名核验后建立 openid 绑定并登录。"""
    ticket = wechat_mp_service.decode_bind_ticket(payload.bind_ticket)
    if not ticket:
        raise HTTPException(status_code=400, detail="绑定凭证已失效，请重新进入小程序。")

    client_ip = get_client_ip(request)
    try:
        wechat_mp_service.check_bind_rate_limit(f"openid:{ticket['openid']}", f"ip:{client_ip}")
    except wechat_mp_service.WechatMpError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    name = payload.name.strip()
    student_id_number = payload.student_id_number.strip()
    if not name or not student_id_number:
        raise HTTPException(status_code=400, detail="请填写完整的姓名和学号。")

    base_url = str(request.base_url)
    with get_db_connection() as conn:
        student_row = get_student_auth_record_by_identity(conn, name, student_id_number)
        if not student_row:
            raise HTTPException(status_code=400, detail="绑定失败：姓名或学号错误。")
        _ensure_student_active(student_row)

        wechat_mp_service.create_binding(
            conn,
            user_role="student",
            user_pk=int(student_row["id"]),
            openid=ticket["openid"],
            unionid=str(ticket.get("unionid") or ""),
        )
        record_student_login(
            conn,
            student_row=student_row,
            login_method="wechat_mp_bind",
            identifier_type="name_and_student_id_number",
            identifier_value=f"{name} / {student_id_number}",
            client_ip=client_ip,
            user_agent=request.headers.get("user-agent", ""),
        )
        token = wechat_mp_service.issue_mp_session(
            conn, user_role="student", user_pk=int(student_row["id"]), openid=ticket["openid"]
        )
        user = wechat_mp_service.load_mp_user(
            conn, {"user_role": "student", "user_pk": int(student_row["id"])}
        )
        result = _build_login_success_payload(conn, user=user, token=token, base_url=base_url)
        conn.commit()
    return result


@router.post("/bind/teacher")
def mp_bind_teacher(request: Request, payload: MpTeacherBindRequest):
    """教师首次绑定：账号密码核验（教师身份敏感，比学生流程更严格）。"""
    ticket = wechat_mp_service.decode_bind_ticket(payload.bind_ticket)
    if not ticket:
        raise HTTPException(status_code=400, detail="绑定凭证已失效，请重新进入小程序。")

    client_ip = get_client_ip(request)
    try:
        wechat_mp_service.check_bind_rate_limit(f"openid:{ticket['openid']}", f"ip:{client_ip}")
    except wechat_mp_service.WechatMpError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    email = payload.email.strip().lower()
    if not email or not payload.password:
        raise HTTPException(status_code=400, detail="请填写完整的邮箱和密码。")

    base_url = str(request.base_url)
    with get_db_connection() as conn:
        teacher = conn.execute(
            """
            SELECT * FROM teachers
            WHERE lower(email) = ? AND COALESCE(is_active, 1) = 1
            LIMIT 1
            """,
            (email,),
        ).fetchone()
        if not teacher or not verify_password(payload.password, teacher["hashed_password"]):
            raise HTTPException(status_code=400, detail="绑定失败：邮箱或密码错误。")

        wechat_mp_service.create_binding(
            conn,
            user_role="teacher",
            user_pk=int(teacher["id"]),
            openid=ticket["openid"],
            unionid=str(ticket.get("unionid") or ""),
        )
        token = wechat_mp_service.issue_mp_session(
            conn, user_role="teacher", user_pk=int(teacher["id"]), openid=ticket["openid"]
        )
        user = wechat_mp_service.load_mp_user(
            conn, {"user_role": "teacher", "user_pk": int(teacher["id"])}
        )
        result = _build_login_success_payload(conn, user=user, token=token, base_url=base_url)
        conn.commit()
    return result


@router.get("/me")
def mp_me(user: dict = Depends(get_current_mp_user)):
    return {"success": True, "data": {"user": user}, "error": None}


@router.post("/logout")
def mp_logout(request: Request):
    """退出登录 = 注销会话 + 解除微信绑定。

    该端点只有"退出登录"按钮调用（401 清理不走这里），且产品文案
    承诺"退出后重新绑定"，因此必须一并撤销绑定——否则下次进入
    openid 仍命中旧绑定，会静默登回原账号，无法换绑。
    """
    token = extract_bearer_token(request)
    revoked = False
    if token:
        with get_db_connection() as conn:
            session = wechat_mp_service.resolve_mp_session(conn, token)
            if session:
                wechat_mp_service.revoke_binding(
                    conn,
                    user_role=str(session["user_role"]),
                    user_pk=int(session["user_pk"]),
                )
                revoked = True
            else:
                revoked = wechat_mp_service.revoke_mp_session(conn, token)
            conn.commit()
    return {"success": True, "data": {"revoked": revoked}, "error": None}
