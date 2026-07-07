"""Shared prompt-pool service for non-chat AI prompt inputs."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from ..db.row import row_to_mapping, rows_to_mappings

_FEATURE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{1,96}$")
_MAX_PROMPT_CHARS = 3000
_MAX_QUERY_CHARS = 200
_MAX_SEARCH_TERMS = 5
_MAX_SEARCH_TERM_CHARS = 80
_LIKE_ESCAPE = "/"


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
    digest = prompt_hash(text)
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
    if share is False:
        return None
    if isinstance(share, str) and share.strip().lower() in {"0", "false", "no", "off", "unchecked"}:
        return None
    return record_prompt(conn, feature_key, prompt)


def search_prompts(conn: Any, feature_key: Any, query: Any = "", *, limit: int = 20) -> list[dict[str, Any]]:
    key = normalize_feature_key(feature_key)
    terms = search_terms(query)
    limit = max(1, min(int(limit or 20), 20))
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
    return [item for row in rows_to_mappings(rows) if (item := serialize_prompt(row))]
