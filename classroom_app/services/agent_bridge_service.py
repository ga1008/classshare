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
MAX_DOCUMENT_FILE_BYTES = 10 * 1024 * 1024
MAX_DOCUMENT_TEXT_BYTES = 2 * 1024 * 1024
MAX_WEB_BYTES = 600 * 1024
SQLITE_QUERY_TIMEOUT_SECONDS = 5.0

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


_NAMED_PARAM_PATTERN = re.compile(r"(?<![:\w]):([a-zA-Z_][a-zA-Z0-9_]*)")


def bind_named_params(sql: str, params: dict[str, Any] | None) -> tuple[str, tuple[Any, ...]]:
    """把 ``:name`` 占位符按出现顺序转换为 ``?`` + 参数元组（引擎无关）。

    字符串字面量内的冒号不会被误判（先剥离单引号字面量再定位占位符）。
    """
    if not params:
        return sql, ()
    if not isinstance(params, dict):
        raise ValueError("params 必须是 JSON 对象（名称 -> 值）。")

    # 标出单引号字符串区间，区间内的 :xxx 不是占位符。
    literal_spans: list[tuple[int, int]] = []
    for match in re.finditer(r"'(?:[^']|'')*'", sql):
        literal_spans.append(match.span())

    def in_literal(position: int) -> bool:
        return any(start <= position < end for start, end in literal_spans)

    ordered: list[Any] = []
    out: list[str] = []
    last = 0
    for match in _NAMED_PARAM_PATTERN.finditer(sql):
        if in_literal(match.start()):
            continue
        name = match.group(1)
        if name not in params:
            raise ValueError(f"SQL 引用了未提供的参数 :{name}。")
        value = params[name]
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise ValueError(f"参数 :{name} 只支持字符串/数字/布尔/null。")
        out.append(sql[last:match.start()])
        out.append("?")
        ordered.append(value)
        last = match.end()
    out.append(sql[last:])
    return "".join(out), tuple(ordered)


def _limited_readonly_sql(sql: str) -> str:
    return f"SELECT * FROM ({sql}) AS agent_bridge_readonly_query LIMIT ?"


def _install_sqlite_query_timeout(conn, timeout_seconds: float):
    set_progress_handler = getattr(conn, "set_progress_handler", None)
    if not callable(set_progress_handler):
        return None
    deadline = time.monotonic() + max(float(timeout_seconds or 0), 0.1)

    def _abort_when_expired() -> int:
        return 1 if time.monotonic() > deadline else 0

    set_progress_handler(_abort_when_expired, 10_000)
    return lambda: set_progress_handler(None, 0)


def run_readonly_query(
    conn,
    sql: str,
    limit: int = MAX_QUERY_ROWS,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cleaned = validate_readonly_sql(sql)
    cleaned, bound_params = bind_named_params(cleaned, params)
    effective_limit = max(1, min(int(limit or MAX_QUERY_ROWS), MAX_QUERY_ROWS))
    reset_timeout = _install_sqlite_query_timeout(conn, SQLITE_QUERY_TIMEOUT_SECONDS)
    try:
        cursor = conn.execute(_limited_readonly_sql(cleaned), (*bound_params, effective_limit + 1))
        columns = [desc[0] for desc in cursor.description or []]
        rows = cursor.fetchmany(effective_limit + 1)
    finally:
        if reset_timeout:
            reset_timeout()
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
    """读取白名单根目录内的文本/文档文件；越界/二进制/超限均拒绝。"""
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
    ext = resolved.suffix.lower()
    # docx/pdf 等文档：复用聊天链路的文本抽取器返回纯文本。
    if ext in {".docx", ".doc", ".pdf", ".pptx", ".ppt", ".xlsx", ".xls"}:
        if size > MAX_DOCUMENT_FILE_BYTES:
            raise ValueError(
                f"文档超过 {MAX_DOCUMENT_FILE_BYTES // (1024 * 1024)}MB 上限（实际 {size} 字节）。"
            )
        from ai_assistant_doc_extract import extract_document_text

        result = extract_document_text(resolved, ext, max_bytes=MAX_DOCUMENT_TEXT_BYTES)
        text = result.text or ""
        if not text.strip():
            raise ValueError("无法从该文档抽取文本。")
        return {
            "path": str(resolved),
            "size": size,
            "extracted": True,
            "truncated": bool(result.truncated),
            "content": text,
        }
    if size > MAX_FILE_BYTES:
        raise ValueError(f"文件超过 {MAX_FILE_BYTES // 1024}KB 上限（实际 {size} 字节）。")
    data = resolved.read_bytes()
    if b"\x00" in data[:4096]:
        raise ValueError("看起来是二进制文件，本接口只读取文本（docx/pdf 等文档会自动抽取）。")
    return {
        "path": str(resolved),
        "size": size,
        "content": data.decode("utf-8", errors="replace"),
    }


SEARCH_SCOPES = ("gongwen", "materials", "assignments", "all")
MAX_SEARCH_LIMIT = 20


def _search_snippet(text: Any, keyword: str, *, limit: int = 160) -> str:
    body = re.sub(r"\s+", " ", str(text or "")).strip()
    if not body:
        return ""
    lowered = body.lower()
    position = lowered.find(keyword.lower())
    if position < 0:
        return body[:limit]
    start = max(0, position - 40)
    return ("…" if start > 0 else "") + body[start:start + limit]


def unified_search(conn, *, teacher_id: int, scope: str, keyword: str, limit: int = 20) -> list[dict[str, Any]]:
    """统一关键词检索（G7）：把容易写错的多表 LIKE 拼接产品化。

    返回统一结构 {type, title, snippet, url, date}，全部限定在任务发起教师可见范围。
    """
    normalized_scope = str(scope or "all").strip().lower()
    if normalized_scope not in SEARCH_SCOPES:
        raise ValueError(f"scope 必须是 {'/'.join(SEARCH_SCOPES)} 之一。")
    term = str(keyword or "").strip()
    if not term:
        raise ValueError("keyword 不能为空。")
    effective_limit = max(1, min(int(limit or MAX_SEARCH_LIMIT), MAX_SEARCH_LIMIT))
    pattern = f"%{term.lower()}%"
    results: list[dict[str, Any]] = []

    if normalized_scope in ("gongwen", "all"):
        from .gongwen_ai_search_service import _fetch_candidate_documents
        from .organization_scope_service import load_teacher_org_scope
        from .resource_access_service import is_super_admin_teacher

        scope_row = load_teacher_org_scope(conn, int(teacher_id))
        try:
            is_admin = bool(is_super_admin_teacher(conn, int(teacher_id)))
        except Exception:
            is_admin = False
        docs = _fetch_candidate_documents(
            conn,
            scope_row,
            is_super_admin=is_admin,
            keywords=[term],
            recent_months=0,
        )
        for doc in docs[:effective_limit]:
            results.append(
                {
                    "type": "gongwen",
                    "title": str(doc.get("title") or ""),
                    "snippet": _search_snippet(
                        doc.get("parsed_summary") or doc.get("parsed_text") or doc.get("keywords"), term
                    ),
                    "url": f"/manage/gongwen?doc={int(doc.get('id') or 0)}",
                    "date": str(doc.get("publish_time") or ""),
                }
            )

    if normalized_scope in ("materials", "all"):
        rows = conn.execute(
            """
            SELECT id, name, material_path, preview_type, updated_at
            FROM course_materials
            WHERE teacher_id = ?
              AND node_type = 'file'
              AND (lower(name) LIKE ? OR lower(COALESCE(material_path, '')) LIKE ?)
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (int(teacher_id), pattern, pattern, effective_limit),
        ).fetchall()
        for row in rows:
            results.append(
                {
                    "type": "material",
                    "title": str(row["name"] or ""),
                    "snippet": str(row["material_path"] or ""),
                    "url": f"/materials/view/{int(row['id'])}",
                    "date": str(row["updated_at"] or ""),
                }
            )

    if normalized_scope in ("assignments", "all"):
        rows = conn.execute(
            """
            SELECT a.id, a.title, a.status, a.created_at, c.name AS course_name
            FROM assignments a
            JOIN courses c ON c.id = a.course_id
            LEFT JOIN class_offerings co ON co.id = a.class_offering_id
            WHERE (co.teacher_id = ? OR c.created_by_teacher_id = ?)
              AND (lower(a.title) LIKE ? OR lower(COALESCE(a.requirements_md, '')) LIKE ?)
            ORDER BY a.created_at DESC, a.id DESC
            LIMIT ?
            """,
            (int(teacher_id), int(teacher_id), pattern, pattern, effective_limit),
        ).fetchall()
        for row in rows:
            results.append(
                {
                    "type": "assignment",
                    "title": str(row["title"] or ""),
                    "snippet": f"{row['course_name'] or ''} · 状态 {row['status'] or ''}",
                    "url": f"/assignment/{int(row['id'])}",
                    "date": str(row["created_at"] or ""),
                }
            )

    return results[: effective_limit * (3 if normalized_scope == "all" else 1)]


# G7：实测可跑的示例 SQL（CI 冒烟测试会逐条在测试库上执行，防 schema 腐化）。
EXAMPLE_QUERIES: tuple[dict[str, str], ...] = (
    {
        "purpose": "我名下的课堂列表（课程 + 班级）",
        "sql": "SELECT co.id AS class_offering_id, c.name AS course_name, cl.name AS class_name FROM class_offerings co JOIN courses c ON c.id = co.course_id JOIN classes cl ON cl.id = co.class_id WHERE co.teacher_id = :teacher_id ORDER BY co.id DESC LIMIT 20",
    },
    {
        "purpose": "某课堂最近作业及提交数",
        "sql": "SELECT a.id, a.title, a.status, a.due_at, COUNT(s.id) AS submission_count FROM assignments a LEFT JOIN submissions s ON s.assignment_id = a.id WHERE a.class_offering_id = :class_offering_id GROUP BY a.id ORDER BY a.created_at DESC LIMIT 10",
    },
    {
        "purpose": "某作业未提交的学生名单",
        "sql": "SELECT st.id, st.name FROM students st JOIN class_offerings co ON co.class_id = st.class_id WHERE co.id = :class_offering_id AND st.id NOT IN (SELECT s.student_pk_id FROM submissions s WHERE s.assignment_id = :assignment_id) ORDER BY st.name LIMIT 100",
    },
    {
        "purpose": "按关键词检索公文（标题/正文/摘要）",
        "sql": "SELECT id, title, sn, author, publish_time FROM gongwen_documents WHERE lower(COALESCE(title,'') || COALESCE(parsed_summary,'') || COALESCE(parsed_text,'')) LIKE :pattern ORDER BY publish_time DESC LIMIT 20",
    },
    {
        "purpose": "某课堂的课时与已绑定学习文档",
        "sql": "SELECT s.order_index, s.title, s.session_date, lm.name AS learning_material_name FROM class_offering_sessions s LEFT JOIN course_materials lm ON lm.id = s.learning_material_id WHERE s.class_offering_id = :class_offering_id ORDER BY s.order_index LIMIT 60",
    },
    {
        "purpose": "我最近的课堂材料",
        "sql": "SELECT id, name, material_path, preview_type, updated_at FROM course_materials WHERE teacher_id = :teacher_id AND node_type = 'file' ORDER BY updated_at DESC LIMIT 20",
    },
    {
        "purpose": "我未来的考试/监考/待办日程",
        "sql": "SELECT title, starts_at, location, source_type FROM teacher_calendar_events WHERE teacher_id = :teacher_id AND status = 'active' AND deleted_at IS NULL AND starts_at >= :start_at ORDER BY starts_at ASC LIMIT 20",
    },
    {
        "purpose": "我名下低于指定分数的提交",
        "sql": "SELECT a.title AS assignment_title, s.student_name, s.score FROM submissions s JOIN assignments a ON CAST(a.id AS TEXT) = CAST(s.assignment_id AS TEXT) LEFT JOIN class_offerings co ON co.id = a.class_offering_id JOIN courses c ON c.id = a.course_id WHERE (co.teacher_id = :teacher_id OR c.created_by_teacher_id = :teacher_id) AND s.score IS NOT NULL AND s.score < :threshold ORDER BY a.created_at DESC, s.score ASC LIMIT 50",
    },
)


def example_queries_payload() -> list[dict[str, str]]:
    return [dict(item) for item in EXAMPLE_QUERIES]


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
