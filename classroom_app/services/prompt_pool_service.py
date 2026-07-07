"""Shared prompt-pool service for non-chat AI prompt inputs."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from ..db.schema_prompt_pool import ensure_prompt_pool_schema
from ..db.row import row_to_mapping, rows_to_mappings

_FEATURE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{1,96}$")
_MAX_PROMPT_CHARS = 3000
_MAX_QUERY_CHARS = 200
_MAX_SEARCH_TERMS = 5
_MAX_SEARCH_TERM_CHARS = 80
_LIKE_ESCAPE = "/"
_SHARE_DISABLED_VALUES = {"0", "false", "no", "off", "unchecked", "disabled", "不", "否"}
_SENSITIVE_PROMPT_PATTERNS = (
    re.compile(r"(?i)-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"(?i)\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16})\b"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password|passwd|cookie|sessionid|private[_-]?key)\b\s*[:=]\s*\S+"
    ),
    re.compile(r"(?:密码|口令|令牌|密钥|私钥|会话|cookie)\s*[:：=]\s*\S+"),
)


def _is_missing_prompt_pool_table_error(exc: Exception) -> bool:
    message = str(exc).lower()
    error_name = type(exc).__name__.lower()
    return "ai_prompt_pool" in message and (
        "no such table" in message
        or "does not exist" in message
        or "undefinedtable" in error_name
    )


def normalize_feature_key(value: Any) -> str:
    key = str(value or "").strip()
    if not _FEATURE_KEY_RE.fullmatch(key):
        raise ValueError("invalid prompt pool feature key")
    return key


def normalize_prompt_text(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""
    return text[:_MAX_PROMPT_CHARS]


def prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def prompt_looks_sensitive(text: str) -> bool:
    """Return True when a shared prompt appears to contain credentials."""
    return any(pattern.search(text or "") for pattern in _SENSITIVE_PROMPT_PATTERNS)


def share_enabled(value: Any = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() not in _SHARE_DISABLED_VALUES
    return True


def search_terms(value: Any) -> list[str]:
    query = normalize_prompt_text(value)[:_MAX_QUERY_CHARS]
    if not query:
        return []
    parts = [part for part in re.split(r"\s+", query) if part]
    if len(parts) <= 1:
        return [query[:_MAX_SEARCH_TERM_CHARS]]
    return [part[:_MAX_SEARCH_TERM_CHARS] for part in parts[:_MAX_SEARCH_TERMS]]


def like_contains_pattern(term: str) -> str:
    escaped = (
        term.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", f"{_LIKE_ESCAPE}%")
        .replace("_", f"{_LIKE_ESCAPE}_")
    )
    return f"%{escaped}%"


def serialize_prompt(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "feature_key": row.get("feature_key") or "",
        "prompt": row.get("prompt_text") or "",
        "use_count": int(row.get("use_count") or 0),
        "created_at": row.get("created_at") or "",
    }


def record_prompt(conn: Any, feature_key: Any, prompt: Any) -> dict[str, Any] | None:
    """Insert or count a shared prompt for a feature scope."""
    key = normalize_feature_key(feature_key)
    text = normalize_prompt_text(prompt)
    if not text:
        return None
    if prompt_looks_sensitive(text):
        return None
    digest = prompt_hash(text)
    ensure_prompt_pool_schema(conn)
    conn.execute(
        """
        INSERT INTO ai_prompt_pool (feature_key, prompt_hash, prompt_text, use_count, created_at)
        VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP)
        ON CONFLICT (feature_key, prompt_hash)
        DO UPDATE SET use_count = ai_prompt_pool.use_count + 1
        """,
        (key, digest, text),
    )
    row = conn.execute(
        """
        SELECT feature_key, prompt_text, use_count, created_at
        FROM ai_prompt_pool
        WHERE feature_key = ? AND prompt_hash = ?
        """,
        (key, digest),
    ).fetchone()
    return serialize_prompt(row_to_mapping(row))


def record_prompt_if_shared(conn: Any, feature_key: Any, prompt: Any, share: Any = True) -> dict[str, Any] | None:
    if not share_enabled(share):
        return None
    return record_prompt(conn, feature_key, prompt)


def search_prompts(conn: Any, feature_key: Any, query: Any = "", *, limit: int = 20) -> list[dict[str, Any]]:
    key = normalize_feature_key(feature_key)
    terms = search_terms(query)
    limit = max(1, min(int(limit or 20), 20))
    try:
        if terms:
            filters = " AND ".join(f"LOWER(prompt_text) LIKE LOWER(?) ESCAPE '{_LIKE_ESCAPE}'" for _ in terms)
            rows = conn.execute(
                f"""
                SELECT feature_key, prompt_text, use_count, created_at
                FROM ai_prompt_pool
                WHERE feature_key = ? AND {filters}
                ORDER BY use_count DESC, created_at DESC
                LIMIT ?
                """,
                (key, *[like_contains_pattern(term) for term in terms], limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT feature_key, prompt_text, use_count, created_at
                FROM ai_prompt_pool
                WHERE feature_key = ?
                ORDER BY use_count DESC, created_at DESC
                LIMIT ?
                """,
                (key, limit),
            ).fetchall()
    except Exception as exc:
        if _is_missing_prompt_pool_table_error(exc):
            return []
        raise
    return [item for row in rows_to_mappings(rows) if (item := serialize_prompt(row))]
