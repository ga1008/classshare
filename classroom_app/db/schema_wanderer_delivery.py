"""AI delivery evidence attached to the original ordinary-message transaction."""
from __future__ import annotations

from typing import Any
from .connection import get_configured_db_engine


def ensure_wanderer_delivery_schema(conn: Any, *, engine: str | None = None,
                                    allow_schema_changes: bool = False) -> None:
    engine = engine or get_configured_db_engine()
    if engine == 'postgres' and not allow_schema_changes:
        conn.execute('SELECT operation_id FROM wanderer_message_deliveries WHERE 1=0')
        return
    conn.execute("""CREATE TABLE IF NOT EXISTS wanderer_message_deliveries (
        operation_id TEXT PRIMARY KEY, wanderer_id BIGINT NOT NULL, life_epoch BIGINT NOT NULL,
        turn_id TEXT NOT NULL, chain_id TEXT NOT NULL, channel TEXT NOT NULL,
        conversation_key TEXT NOT NULL, payload_hash TEXT NOT NULL, peer_watermark BIGINT NOT NULL,
        message_id BIGINT NOT NULL, origin_operation_id TEXT NOT NULL,
        delivery_kind TEXT NOT NULL CHECK(delivery_kind IN ('sent','reused')), created_at TEXT NOT NULL)""")
    conn.execute('CREATE INDEX IF NOT EXISTS idx_wanderer_delivery_repeat ON wanderer_message_deliveries '
                 '(wanderer_id,life_epoch,channel,conversation_key,payload_hash,created_at)')
