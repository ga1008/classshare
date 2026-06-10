"""
Agent 桥接服务 —— 让独立运行时里的 Agent 把「平台本身」当成工具使用。

能力（全部只读，无任何写入路径）：
- 只读 SQL 查询：单条 SELECT/WITH，自动限行、敏感表拒绝、敏感列脱敏
- 数据库结构速查：表名 + 列名（排除凭据/会话等敏感表）
- 平台文件读取：仅限白名单数据目录内的文本文件
- 互联网访问：服务端代理抓取网页（SSRF 防护，仅公网 http/https）

鉴权：按任务签发 HMAC token（SECRET_KEY 派生，自带过期时间），写入任务
workspace，由运行时携带 Bearer 调用。任务结束后 token 自然过期。
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
import socket
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..config import SECRET_KEY
from .. import storage_paths

BRIDGE_TOKEN_SLACK_SECONDS = 1800
MAX_QUERY_ROWS = 200
MAX_CELL_CHARS = 2000
MAX_FILE_BYTES = 256 * 1024
MAX_WEB_BYTES = 600 * 1024

# 凭据、会话、密钥类表：结构与数据都不暴露给 Agent。
SENSITIVE_TABLES = frozenset({
    "user_sessions",
    "agent_runtime_api_keys",
    "agent_runtime_key_checks",
    "teacher_academic_system_credentials",
    "teacher_gongwen_credentials",
    "teacher_smart_classroom_credentials",
    "teacher_git_credentials",
    "teacher_email_configs",
    "student_password_reset_requests",
    "student_login_audit_logs",
})

# 命中这些名字的列在查询结果里脱敏。
SENSITIVE_COLUMN_PATTERN = re.compile(
    r"password|passwd|secret|token|credential|api_key|apikey|session_id|cookie",
    re.IGNORECASE,
)

FORBIDDEN_SQL_PATTERN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|truncate|grant|revoke|"
    r"attach|detach|pragma|vacuum|reindex|copy|merge|call|do|execute|set|lock|"
    r"begin|commit|rollback)\b",
    re.IGNORECASE,
)

# Agent 可读的平台文件根目录（材料、共享文件、教材附件、任务工作区）。
def allowed_file_roots() -> list[Path]:
    roots = [
        storage_paths.NEW_GLOBAL_FILES_DIR,
        storage_paths.LEGACY_GLOBAL_FILES_DIR,
        storage_paths.NEW_SHARE_DIR,
        storage_paths.LEGACY_SHARE_DIR,
        storage_paths.NEW_TEXTBOOK_ATTACHMENT_DIR,
        storage_paths.LEGACY_TEXTBOOK_ATTACHMENT_DIR,
        storage_paths.DATA_ROOT / "agent_tasks",
    ]
    return [root for root in roots if str(root)]


def _bridge_signature(task_id: int, expires_at: int) -> str:
    message = f"agent-bridge:{int(task_id)}:{int(expires_at)}".encode("utf-8")
    return hmac.new(SECRET_KEY.encode("utf-8"), message, hashlib.sha256).hexdigest()


def issue_bridge_token(task_id: int, *, ttl_seconds: int) -> str:
    expires_at = int(time.time()) + max(60, int(ttl_seconds)) + BRIDGE_TOKEN_SLACK_SECONDS
    return f"{int(task_id)}.{expires_at}.{_bridge_signature(task_id, expires_at)}"


def verify_bridge_token(token: str) -> int | None:
    """校验 token，返回 task_id；无效或过期返回 None。"""
    parts = str(token or "").strip().split(".")
    if len(parts) != 3:
        return None
    try:
        task_id = int(parts[0])
        expires_at = int(parts[1])
    except ValueError:
        return None
    if expires_at < time.time():
        return None
    expected = _bridge_signature(task_id, expires_at)
    if not hmac.compare_digest(expected, parts[2]):
        return None
    return task_id


def validate_readonly_sql(sql: str) -> str:
    """只放行单条 SELECT/WITH 查询，返回清洗后的 SQL；不合法抛 ValueError。"""
    cleaned = str(sql or "").strip().rstrip(";").strip()
    if not cleaned:
        raise ValueError("SQL 不能为空。")
    if ";" in cleaned:
        raise ValueError("只允许单条查询语句。")
    if not re.match(r"^(select|with)\b", cleaned, re.IGNORECASE):
        raise ValueError("只允许 SELECT / WITH 只读查询。")
    forbidden = FORBIDDEN_SQL_PATTERN.search(cleaned)
    if forbidden:
        raise ValueError(f"查询包含被禁止的关键字：{forbidden.group(0)}。本接口只读。")
    lowered = cleaned.lower()
    for table in SENSITIVE_TABLES:
        if re.search(rf"\b{table}\b", lowered):
            raise ValueError(f"表 {table} 涉及凭据/会话数据，不对 Agent 开放。")
    return cleaned


def mask_sensitive_cell(column_name: str, value: Any) -> Any:
    if value is None:
        return None
    if SENSITIVE_COLUMN_PATTERN.search(str(column_name or "")):
        return "[已脱敏]"
    text = value
    if isinstance(text, (bytes, bytearray)):
        return f"[二进制 {len(text)} 字节]"
    if isinstance(text, str) and len(text) > MAX_CELL_CHARS:
        return text[:MAX_CELL_CHARS] + "…[截断]"
    return text


def run_readonly_query(conn, sql: str, limit: int = MAX_QUERY_ROWS) -> dict[str, Any]:
    cleaned = validate_readonly_sql(sql)
    effective_limit = max(1, min(int(limit or MAX_QUERY_ROWS), MAX_QUERY_ROWS))
    cursor = conn.execute(cleaned)
    columns = [desc[0] for desc in cursor.description or []]
    rows = cursor.fetchmany(effective_limit + 1)
    truncated = len(rows) > effective_limit
    rows = rows[:effective_limit]
    payload_rows = [
        {col: mask_sensitive_cell(col, row[idx]) for idx, col in enumerate(columns)}
        for row in rows
    ]
    return {
        "columns": columns,
        "rows": payload_rows,
        "row_count": len(payload_rows),
        "truncated": truncated,
        "max_rows": effective_limit,
    }


def describe_schema(conn, engine: str) -> dict[str, list[str]]:
    """表 -> 列名列表（排除敏感表）。"""
    tables: dict[str, list[str]] = {}
    if engine == "postgres":
        rows = conn.execute(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
            ORDER BY table_name, ordinal_position
            """
        ).fetchall()
        for row in rows:
            name = str(row[0])
            if name in SENSITIVE_TABLES:
                continue
            tables.setdefault(name, []).append(str(row[1]))
        return tables

    table_rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    for table_row in table_rows:
        name = str(table_row[0])
        if name in SENSITIVE_TABLES:
            continue
        try:
            col_rows = conn.execute(f'PRAGMA table_info("{name}")').fetchall()
            tables[name] = [str(col[1]) for col in col_rows]
        except Exception:
            tables[name] = []
    return tables


def read_platform_file(raw_path: str) -> dict[str, Any]:
    """读取白名单根目录内的文本文件；越界/二进制/超限均拒绝。"""
    requested = Path(str(raw_path or "").strip())
    if not str(requested):
        raise ValueError("path 不能为空。")
    resolved = requested.resolve()
    allowed = False
    for root in allowed_file_roots():
        try:
            resolved.relative_to(root.resolve())
            allowed = True
            break
        except (ValueError, OSError):
            continue
    if not allowed:
        raise ValueError("路径不在允许的平台数据目录内（材料/共享文件/教材附件/Agent 工作区）。")
    if not resolved.is_file():
        raise ValueError("文件不存在。")
    size = resolved.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ValueError(f"文件超过 {MAX_FILE_BYTES // 1024}KB 上限（实际 {size} 字节）。")
    data = resolved.read_bytes()
    if b"\x00" in data[:4096]:
        raise ValueError("看起来是二进制文件，本接口只读取文本。")
    return {
        "path": str(resolved),
        "size": size,
        "content": data.decode("utf-8", errors="replace"),
    }


def assert_public_http_url(raw_url: str) -> str:
    """SSRF 防护：仅公网 http/https，拒绝内网/环回/链路本地地址。"""
    url = str(raw_url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("只支持 http/https URL。")
    host = parsed.hostname or ""
    if not host:
        raise ValueError("URL 缺少主机名。")
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except OSError as exc:
        raise ValueError(f"域名解析失败：{exc}")
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            raise ValueError("目标地址属于内网/保留网段，已拒绝（防 SSRF）。")
    return url


def strip_html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", str(html or ""))
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;?", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()
