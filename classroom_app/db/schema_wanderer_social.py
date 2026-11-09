"""Versioned, self-authored relationship decisions; caller owns the transaction."""
from __future__ import annotations

from typing import Any

from .connection import get_configured_db_engine


def ensure_wanderer_social_schema(conn: Any, *, engine: str | None = None,
                                  allow_schema_changes: bool = False) -> None:
    engine = engine or get_configured_db_engine()
    if engine == "postgres" and not allow_schema_changes:
        conn.execute("SELECT revision FROM wanderer_relationship_versions WHERE 1=0")
        conn.execute("SELECT operation_id FROM wanderer_relationship_changes WHERE 1=0")
        conn.execute("SELECT handled_message_id FROM wanderer_group_observations WHERE 1=0")
        conn.execute("SELECT operation_id FROM wanderer_private_message_operations WHERE 1=0")
        return
    conn.execute("""CREATE TABLE IF NOT EXISTS wanderer_relationship_versions (
        wanderer_id INTEGER NOT NULL, peer_identity TEXT NOT NULL,
        revision INTEGER NOT NULL DEFAULT 0, origin TEXT NOT NULL DEFAULT 'self',
        legacy_affinity REAL, source_turn_id TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL, PRIMARY KEY (wanderer_id, peer_identity))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS wanderer_relationship_changes (
        operation_id TEXT PRIMARY KEY, wanderer_id INTEGER NOT NULL, peer_identity TEXT NOT NULL,
        old_affinity REAL NOT NULL, new_affinity REAL NOT NULL, revision INTEGER NOT NULL,
        source_turn_id TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS wanderer_group_observations (
        wanderer_id INTEGER NOT NULL, group_id INTEGER NOT NULL,
        membership_stamp TEXT NOT NULL DEFAULT '', handled_message_id INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL, PRIMARY KEY(wanderer_id,group_id))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS wanderer_private_message_operations (
        operation_id TEXT PRIMARY KEY, wanderer_id INTEGER NOT NULL,
        request_hash TEXT NOT NULL, message_id INTEGER NOT NULL UNIQUE,
        created_at TEXT NOT NULL)""")



def migrate_relationship_affinities(conn: Any) -> int:
    """Map legacy -100..100 exactly once; preserve its value and historical text."""
    from ..services.social_common import serialize_on
    from ..time_utils import local_iso
    ensure_wanderer_social_schema(conn)
    serialize_on(conn, "wanderer-relationship-migration", ["v2"])
    rows = conn.execute("SELECT r.wanderer_id,r.peer_identity,r.affinity FROM wanderer_relationships r "
                        "LEFT JOIN wanderer_relationship_versions v ON v.wanderer_id=r.wanderer_id "
                        "AND v.peer_identity=r.peer_identity WHERE v.wanderer_id IS NULL").fetchall()
    for row in rows:
        old = float(row["affinity"] or 0)
        conn.execute("UPDATE wanderer_relationships SET affinity=? WHERE wanderer_id=? AND peer_identity=?",
                     (max(0.0, min(100.0, (old + 100.0) / 2.0)), row["wanderer_id"], row["peer_identity"]))
        conn.execute("INSERT INTO wanderer_relationship_versions "
                     "(wanderer_id,peer_identity,revision,origin,legacy_affinity,updated_at) VALUES (?,?,0,'legacy',?,?)",
                     (row["wanderer_id"], row["peer_identity"], old, local_iso()))
    return len(rows)
