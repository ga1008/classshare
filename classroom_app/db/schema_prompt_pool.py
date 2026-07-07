"""Global shared AI prompt pool schema.

The pool is intentionally partitioned by feature, not by user. It stores only
the prompt text, first-entered time, and a reuse counter; no author identity or
per-user history is retained.
"""

from __future__ import annotations

from typing import Any


def ensure_prompt_pool_schema(conn: Any) -> None:
    """Create the shared prompt pool table on SQLite or PostgreSQL."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_prompt_pool (
            feature_key TEXT NOT NULL,
            prompt_hash TEXT NOT NULL,
            prompt_text TEXT NOT NULL,
            use_count INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (feature_key, prompt_hash)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ai_prompt_pool_lookup "
        "ON ai_prompt_pool (feature_key, use_count DESC, created_at DESC)"
    )
