import socket
import math
import uuid
import threading
import ipaddress
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict
from urllib.parse import urlencode, urlsplit
from fastapi import Request, HTTPException, Depends, status
from jose import jwt, JWTError
from passlib.context import CryptContext

from .config import SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES
from .database import (
    delete_user_sessions,
    get_user_session,
    list_user_session_roles,
    list_user_sessions,
    save_user_session,
)

# --- 密码加密 ---

# 修复：将 "bcrypt" 更改为 "pbkdf2_sha256"
# 这是一个非常健壮的标准，它不依赖于 'bcrypt' C 库，
# 从而避免了您在 Conda 环境中遇到的 'AttributeError' 和 'ValueError: password cannot be longer than 72 bytes' 的问题。
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

active_sessions: Dict[str, Dict] = {}
_sessions_lock = threading.Lock()

AUTH_ERROR_LOGIN_REQUIRED = "login_required"
AUTH_ERROR_PERMISSION_DENIED = "permission_denied"

_AUTH_PAGE_PATHS = {
    "/student/login",
    "/teacher/login",
    "/teacher/register",
    "/auth/forbidden",
    "/logout",
}

_TEACHER_ONLY_PREFIXES = (
    "/manage",
    "/materials/manage",
)

_REFERER_FIRST_PREFIXES = (
    "/api",
    "/download/",
    "/submissions/download/",
    "/materials/download/",
    "/materials/raw/",
)

_TEACHER_ONLY_PATTERNS = (
    re.compile(r"^/teacher(?:/|$)"),
    re.compile(r"^/exam/new$"),
    re.compile(r"^/exam/[^/]+/edit$"),
    re.compile(r"^/api/manage(?:/|$)"),
    re.compile(r"^/api/session/(?:active|invalidate)(?:/|$)"),
    re.compile(r"^/api/files(?:/|$)"),
    re.compile(r"^/api/courses/[^/]+/assignments$"),
    re.compile(r"^/api/courses/[^/]+/files/(?:upload|[^/]+)$"),
    re.compile(r"^/api/assignments/[^/]+(?:/submissions|/export/[^/]+)?$"),
    re.compile(r"^/api/submissions/(?!download(?:/|$))[^/]+(?:/(?:grade|regrade))?$"),
    re.compile(r"^/api/exam-papers(?:/|$)"),
    re.compile(r"^/api/ai/generate_assignment$"),
    re.compile(r"^/api/ai/exam(?:/|$)"),
    re.compile(r"^/api/materials/(?:library|upload|[^/]+(?:/(?:assign|ai-parse|ai-optimize|content|repository(?:/(?:command|credentials))?))?)$"),
)

_STUDENT_ONLY_PATTERNS = (
    re.compile(r"^/api/assignments/[^/]+/(?:submit|withdraw)$"),
    re.compile(r"^/api/student/password/change$"),
)

ACCESS_TOKEN_MAX_AGE_SECONDS = max(1, ACCESS_TOKEN_EXPIRE_MINUTES * 60)


def _build_session_snapshot(
    *,
    session_id: str,
    ip: str,
    last_login: Optional[str],
    user_id: str,
    role: Optional[str],
    name: Optional[str],
    expires_at: str,
    updated_at: str = "",
) -> dict:
    return {
        "session_id": str(session_id or ""),
        "ip": str(ip or ""),
        "last_login": str(last_login or ""),
        "user_id": str(user_id or ""),
        "role": str(role or ""),
        "name": str(name or ""),
        "expires_at": str(expires_at or ""),
        "updated_at": str(updated_at or ""),
    }


def _cache_session_snapshot(session_user_key: str, session_snapshot: dict) -> None:
    normalized_user_key = str(session_user_key or "").strip()
    if not normalized_user_key:
        return
    with _sessions_lock:
        active_sessions[normalized_user_key] = dict(session_snapshot)


def _drop_cached_sessions_for_user(user_id: str, role: Optional[str] = None) -> int:
    raw_user_id = str(user_id or "").strip()
    normalized_role = str(role or "").strip().lower()
    if not raw_user_id:
        return 0

    removed_count = 0
    with _sessions_lock:
        keys_to_remove = [
            key
            for key, session in active_sessions.items()
            if str(session.get("user_id") or "") == raw_user_id
            and (not normalized_role or str(session.get("role") or "").strip().lower() == normalized_role)
        ]
        for session_key in keys_to_remove:
            if session_key in active_sessions:
                del active_sessions[session_key]
                removed_count += 1
    return removed_count


def list_active_sessions() -> dict[str, dict]:
    sessions = list_user_sessions()
    with _sessions_lock:
        active_sessions.clear()
        active_sessions.update({key: dict(value) for key, value in sessions.items()})
    return sessions


def list_active_session_roles_for_user(user_id: str) -> set[str]:
    return set(list_user_session_roles(user_id))


def build_session_user_key(user_id: Optional[str], role: Optional[str] = None) -> Optional[str]:
    normalized_user_id = str(user_id).strip() if user_id is not None else ""
    normalized_role = str(role).strip() if role is not None else ""
    if not normalized_user_id:
        return None
    return f"{normalized_role}:{normalized_user_id}" if normalized_role else normalized_user_id


def get_session_user_key_from_payload(payload: Optional[dict]) -> Optional[str]:
    if not payload:
        return None
    session_user_key = payload.get("session_user_key")
    if session_user_key:
        return str(session_user_key)
    return build_session_user_key(payload.get("id"), payload.get("role"))


def normalize_ip(ip: Optional[str]) -> Optional[str]:
    """统一 IP 表示，避免 IPv4/IPv6 或代理格式差异导致鉴权误判。"""
    if ip is None:
        return None

    raw = str(ip).strip()
    if not raw:
        return None

    # 兼容 X-Forwarded-For 可能携带的多 IP 格式
    if "," in raw:
        raw = raw.split(",", 1)[0].strip()

    if raw.lower() == "localhost":
        return "127.0.0.1"

    # 去掉 IPv6 zone id，例如 fe80::1%lo0
    if "%" in raw:
        raw = raw.split("%", 1)[0]

    try:
        parsed = ipaddress.ip_address(raw)
        if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped:
            parsed = parsed.ipv4_mapped
        if parsed.is_loopback:
            return "127.0.0.1"
        return str(parsed)
    except ValueError:
        # 保留原值，避免因未知格式直接判空
        return raw

def verify_password(plain_password, hashed_password):
    # 验证逻辑保持不变
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    # 哈希逻辑保持不变
    return pwd_context.hash(password)

# --- 认证 ---
def create_access_token(data: dict, client_ip: str) -> str:
    """创建 JWT token，包含会话ID和IP信息"""
    session_id = str(uuid.uuid4())
    normalized_ip = normalize_ip(client_ip) or str(client_ip)
    session_user_key = build_session_user_key(data.get("id"), data.get("role")) or str(data["id"])
    issued_at = datetime.now(timezone.utc)
    expire_at = issued_at + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    # 在token中包含会话信息
    token_data = data.copy()
    token_data["session_id"] = session_id
    token_data["ip"] = normalized_ip
    token_data["session_user_key"] = session_user_key
    token_data["iat"] = issued_at
    token_data["exp"] = expire_at

    user_id = str(data["id"])
    session_snapshot = save_user_session(
        session_user_key=session_user_key,
        session_id=session_id,
        user_id=user_id,
        role=str(data.get("role") or ""),
        name=str(data.get("name") or ""),
        ip=normalized_ip,
        last_login=str(data.get("login_time") or ""),
        expires_at=expire_at.isoformat(),
    )
    _cache_session_snapshot(session_user_key, session_snapshot)

    print(f"[SESSION] 用户 {data.get('name')} 登录，IP: {normalized_ip}, 会话ID: {session_id}")
    return jwt.encode(token_data, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: Optional[str], client_ip: Optional[str] = None) -> Optional[dict]:
    """验证 JWT token，同时验证IP和会话有效性"""
    if token is None:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        session_user_key = get_session_user_key_from_payload(payload)
        if not session_user_key:
            return None
        user_id = str(payload.get("id"))
        session_id = payload.get("session_id")
        token_ip = normalize_ip(payload.get("ip"))
        normalized_client_ip = normalize_ip(client_ip)

        current_session = get_user_session(session_user_key)
        if current_session is None:
            _drop_cached_sessions_for_user(user_id, str(payload.get("role") or ""))
            print(f"[SESSION] 用户 {user_id} 没有活跃会话")
            return None

        _cache_session_snapshot(session_user_key, current_session)
        session_ip = normalize_ip(current_session.get("ip"))

        if normalized_client_ip is not None:
            if (
                current_session["session_id"] != session_id
                or session_ip != token_ip
                or token_ip != normalized_client_ip
            ):
                print(f"[SESSION] 会话验证失败 - 用户: {user_id}, 期望IP: {session_ip}, 实际IP: {normalized_client_ip}")
                return None
        else:
            if current_session["session_id"] != session_id:
                print(f"[SESSION] 会话ID不匹配 - 用户: {user_id}")
                return None

        return payload
    except JWTError:
        return None


def decode_token_payload(token: Optional[str]) -> Optional[dict]:
    """Decode a signed token without checking in-memory session state."""
    if not token:
        return None
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None


def get_user_hint_from_request(request: Request) -> Optional[dict]:
    """Best-effort token payload for redirect and warning page decisions."""
    return decode_token_payload(request.cookies.get("access_token"))


def apply_access_token_cookie(response, access_token: str) -> None:
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        samesite="lax",
        max_age=ACCESS_TOKEN_MAX_AGE_SECONDS,
        expires=ACCESS_TOKEN_MAX_AGE_SECONDS,
        path="/",
    )


def clear_access_token_cookie(response) -> None:
    response.delete_cookie("access_token", path="/")


def get_request_path_with_query(request: Request) -> str:
    path = request.url.path or "/"
    if request.url.query:
        return f"{path}?{request.url.query}"
    return path


def get_same_origin_referer_path(request: Request) -> Optional[str]:
    referer = request.headers.get("referer")
    if not referer:
        return None

    parsed = urlsplit(referer)
    if parsed.scheme and parsed.scheme != request.url.scheme:
        return None
    if parsed.netloc and parsed.netloc != request.url.netloc:
        return None

    candidate = parsed.path or "/"
    if parsed.query:
        candidate = f"{candidate}?{parsed.query}"

    sanitized = sanitize_next_path(candidate, fallback="")
    return sanitized or None


def get_auth_redirect_target(request: Request) -> str:
    current_path = get_request_path_with_query(request)
    if request.url.path.startswith(_REFERER_FIRST_PREFIXES):
        referer_path = get_same_origin_referer_path(request)
        if referer_path:
            return referer_path
        return "/dashboard"
    return current_path


def is_safe_local_path(target: Optional[str]) -> bool:
    if not target:
        return False

    raw = str(target).strip()
    if not raw or raw.startswith("//") or any(ch in raw for ch in "\r\n"):
        return False

    parsed = urlsplit(raw)
    if parsed.scheme or parsed.netloc:
        return False

    return raw.startswith("/")


def sanitize_next_path(target: Optional[str], fallback: str = "/dashboard") -> str:
    if not is_safe_local_path(target):
        return fallback

    parsed = urlsplit(str(target).strip())
    path = parsed.path or "/"
    if path in _AUTH_PAGE_PATHS:
        return fallback

    query = f"?{parsed.query}" if parsed.query else ""
    return f"{path}{query}"


def infer_required_role_from_path(path: Optional[str]) -> Optional[str]:
    normalized_path = (path or "/").strip() or "/"
    if normalized_path.startswith(_TEACHER_ONLY_PREFIXES):
        return "teacher"
    if any(pattern.match(normalized_path) for pattern in _TEACHER_ONLY_PATTERNS):
        return "teacher"
    if any(pattern.match(normalized_path) for pattern in _STUDENT_ONLY_PATTERNS):
        return "student"
    if normalized_path.startswith("/student"):
        return "student"
    if normalized_path == "/exam/new" or re.fullmatch(r"/exam/[^/]+/edit", normalized_path):
        return "teacher"
    return None


def build_login_url(login_path: str, next_path: Optional[str] = None) -> str:
    safe_next = sanitize_next_path(next_path, fallback="/dashboard")
    return f"{login_path}?{urlencode({'next': safe_next})}"


def get_login_path_for_request(request: Request) -> str:
    auth_target = get_auth_redirect_target(request)
    auth_target_path = urlsplit(auth_target).path or "/"
    user_hint = get_user_hint_from_request(request) or {}
    required_role = infer_required_role_from_path(auth_target_path) or infer_required_role_from_path(request.url.path)
    preferred_role = required_role or user_hint.get("role")
    return "/teacher/login" if preferred_role == "teacher" else "/student/login"


def build_login_redirect_url(request: Request) -> str:
    return build_login_url(get_login_path_for_request(request), get_auth_redirect_target(request))


def build_permission_warning_url(request: Request, required_role: Optional[str] = None) -> str:
    auth_target = get_auth_redirect_target(request)
    params = {
        "next": sanitize_next_path(auth_target, fallback="/dashboard")
    }
    effective_required_role = required_role or infer_required_role_from_path(urlsplit(auth_target).path) or infer_required_role_from_path(request.url.path)
    if effective_required_role:
        params["required_role"] = effective_required_role
    return f"/auth/forbidden?{urlencode(params)}"


def get_role_label(role: Optional[str]) -> str:
    if role == "teacher":
        return "教师"
    if role == "student":
        return "学生"
    return "访客"


def invalidate_user_session(
    user_id: str,
    role: Optional[str] = None,
    *,
    conn: sqlite3.Connection | None = None,
) -> bool:
    """使用户的所有会话失效"""
    return invalidate_session_for_user(user_id, role, conn=conn)


def invalidate_session_for_user(
    user_id: str,
    role: Optional[str] = None,
    *,
    conn: sqlite3.Connection | None = None,
) -> bool:
    raw_user_id = str(user_id).strip()
    normalized_role = role.strip().lower() if role else None
    removed_count = delete_user_sessions(raw_user_id, normalized_role, conn=conn)
    removed_cache_count = _drop_cached_sessions_for_user(raw_user_id, normalized_role)

    if removed_count > 0 or removed_cache_count > 0:
        print(f"[SESSION] Cleared session for {build_session_user_key(raw_user_id, normalized_role) or raw_user_id}")
        return True

    return False


def get_client_ip(request: Request) -> str:
    """获取客户端真实IP"""
    # 首先检查 X-Forwarded-For 头（反向代理情况）
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # 取第一个IP（客户端真实IP）
        client_ip = forwarded.split(',')[0].strip()
        return normalize_ip(client_ip) or client_ip

    # 检查 X-Real-IP 头
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return normalize_ip(real_ip) or real_ip

    # 最后使用连接IP
    host_ip = request.client.host
    return normalize_ip(host_ip) or host_ip


def get_active_user_from_request(request: Request) -> Optional[dict]:
    token = request.cookies.get("access_token")
    client_ip = get_client_ip(request)
    return verify_token(token, client_ip)


def get_current_user_optional(request: Request) -> Optional[dict]:
    """获取当前用户（如果已登录），但不强制。"""
    return get_active_user_from_request(request)

def get_current_user(user: Optional[dict] = Depends(get_current_user_optional)) -> dict:
    """依赖项：强制用户必须登录"""
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={
                "WWW-Authenticate": "Bearer",
                "X-Auth-Error": AUTH_ERROR_LOGIN_REQUIRED,
            },
        )
    return user

def get_current_teacher(user: dict = Depends(get_current_user)) -> dict:
    """依赖项：强制用户必须是教师"""
    if user.get("role") != "teacher":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied: Not a teacher",
            headers={
                "X-Auth-Error": AUTH_ERROR_PERMISSION_DENIED,
                "X-Required-Role": "teacher",
            },
        )
    return user

def get_current_student(user: dict = Depends(get_current_user)) -> dict:
    """依赖项：强制用户必须是学生"""
    if user.get("role") != "student":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied: Not a student",
            headers={
                "X-Auth-Error": AUTH_ERROR_PERMISSION_DENIED,
                "X-Required-Role": "student",
            },
        )
    return user

# --- 辅助工具 (来自旧版) ---
def get_local_ips() -> list[str]:
    ips = []
    try:
        host_name = socket.gethostname()
        for ip in socket.gethostbyname_ex(host_name)[2]:
            if not ip.startswith("127."): ips.append(ip)
    except socket.gaierror: pass
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ips.append(s.getsockname()[0])
            s.close()
        except Exception: return ["无法自动检测IP"]
    return sorted(list(set(ips)))

def human_readable_size(size_bytes: int) -> str:
    if size_bytes == 0: return "0B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"


def verify_token_legacy(token: Optional[str]) -> Optional[dict]:
    """
    向后兼容的函数，用于不需要IP验证的场景
    警告：这会绕过IP验证，只在确实不需要IP验证时使用
    """
    return verify_token(token, None)
