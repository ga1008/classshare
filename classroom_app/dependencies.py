import socket
import math
import uuid
import threading
import ipaddress
from typing import Optional, Dict
from fastapi import Request, HTTPException, Depends, status
from jose import jwt, JWTError
from passlib.context import CryptContext

from .config import SECRET_KEY, ALGORITHM

# --- 密码加密 ---

# 修复：将 "bcrypt" 更改为 "pbkdf2_sha256"
# 这是一个非常健壮的标准，它不依赖于 'bcrypt' C 库，
# 从而避免了您在 Conda 环境中遇到的 'AttributeError' 和 'ValueError: password cannot be longer than 72 bytes' 的问题。
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

active_sessions: Dict[str, Dict] = {}
_sessions_lock = threading.Lock()


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

    # 在token中包含会话信息
    token_data = data.copy()
    token_data["session_id"] = session_id
    token_data["ip"] = normalized_ip

    # 更新活跃会话 (线程安全)
    user_id = str(data["id"])  # 使用字符串作为键
    with _sessions_lock:
        active_sessions[user_id] = {
            "session_id": session_id,
            "ip": normalized_ip,
            "last_login": data.get("login_time", "")
        }

    print(f"[SESSION] 用户 {data.get('name')} 登录，IP: {normalized_ip}, 会话ID: {session_id}")
    return jwt.encode(token_data, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: Optional[str], client_ip: Optional[str] = None) -> Optional[dict]:
    """验证 JWT token，同时验证IP和会话有效性"""
    if token is None:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = str(payload.get("id"))
        session_id = payload.get("session_id")
        token_ip = normalize_ip(payload.get("ip"))
        normalized_client_ip = normalize_ip(client_ip)

        # 检查会话是否存在且匹配
        with _sessions_lock:
            if user_id not in active_sessions:
                print(f"[SESSION] 用户 {user_id} 没有活跃会话")
                return None

            current_session = active_sessions[user_id]
            session_ip = normalize_ip(current_session.get("ip"))

            # 如果提供了client_ip，则验证IP；否则只验证会话ID
            if normalized_client_ip is not None:
                # 完整验证：会话ID、IP地址
                if (current_session["session_id"] != session_id or
                        session_ip != token_ip or
                        token_ip != normalized_client_ip):
                    print(f"[SESSION] 会话验证失败 - 用户: {user_id}, 期望IP: {session_ip}, 实际IP: {normalized_client_ip}")
                    return None
            else:
                # 简化验证：只验证会话ID
                if current_session["session_id"] != session_id:
                    print(f"[SESSION] 会话ID不匹配 - 用户: {user_id}")
                    return None

        return payload
    except JWTError:
        return None


def invalidate_user_session(user_id: str):
    """使用户的所有会话失效"""
    with _sessions_lock:
        if user_id in active_sessions:
            del active_sessions[user_id]
            print(f"[SESSION] 用户 {user_id} 会话已失效")


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


async def get_current_user_optional(request: Request) -> Optional[dict]:
    """获取当前用户（如果已登录），但不强制。"""
    token = request.cookies.get("access_token")
    client_ip = get_client_ip(request)
    return verify_token(token, client_ip)

async def get_current_user(user: Optional[dict] = Depends(get_current_user_optional)) -> dict:
    """依赖项：强制用户必须登录"""
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user

async def get_current_teacher(user: dict = Depends(get_current_user)) -> dict:
    """依赖项：强制用户必须是教师"""
    if user.get("role") != "teacher":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied: Not a teacher")
    return user

async def get_current_student(user: dict = Depends(get_current_user)) -> dict:
    """依赖项：强制用户必须是学生"""
    if user.get("role") != "student":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied: Not a student")
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
