"""Durable, per-conversation DM work. Read receipts are never the work cursor."""
from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any

from ..db.schema_wanderers import ensure_wanderer_schema
from .social_common import env_int, now_iso, serialize_on, utc_now

LEASE_SECONDS = 150
DEBOUNCE_SECONDS = 2
MAX_ATTEMPTS = 5


def connection(factory=None):
    if factory:
        return factory()
    from ..database import get_db_connection
    return get_db_connection()


def concurrency() -> int:
    return env_int("WANDERER_DM_CONCURRENCY", 2, low=1, high=4)


def stamp_after(seconds: float) -> str:
    return (utc_now() + timedelta(seconds=seconds)).isoformat(timespec="microseconds")


def enqueue(conn: Any, wanderer_id: int, source: str, key: str, peer: str, message_id: int,
            *, class_offering_id=None, handled_id: int = 0) -> int:
    """Called in the message transaction. Never resets a running claim or generated answer."""
    ensure_wanderer_schema(conn)
    now = now_iso()
    conn.execute("""INSERT INTO wanderer_social_inbox
        (wanderer_id,source,conversation_key,peer_identity,class_offering_id,latest_message_id,
         handled_message_id,run_at,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(wanderer_id,source,conversation_key) DO UPDATE SET
          latest_message_id=CASE WHEN excluded.latest_message_id>wanderer_social_inbox.latest_message_id
            THEN excluded.latest_message_id ELSE wanderer_social_inbox.latest_message_id END,
          state=CASE WHEN wanderer_social_inbox.state IN ('idle','failed','blocked')
            AND excluded.latest_message_id>wanderer_social_inbox.latest_message_id THEN 'pending'
            ELSE wanderer_social_inbox.state END,
          attempts=CASE WHEN excluded.latest_message_id>wanderer_social_inbox.latest_message_id
            AND wanderer_social_inbox.state IN ('idle','failed','blocked') THEN 0 ELSE wanderer_social_inbox.attempts END,
          run_at=CASE WHEN wanderer_social_inbox.state IN ('idle','failed','blocked')
            AND excluded.latest_message_id>wanderer_social_inbox.latest_message_id THEN excluded.run_at
            ELSE wanderer_social_inbox.run_at END, updated_at=excluded.updated_at""",
        (int(wanderer_id), source, key, peer, class_offering_id, int(message_id), int(handled_id),
         stamp_after(DEBOUNCE_SECONDS), now, now))
    return int(conn.execute("SELECT id FROM wanderer_social_inbox WHERE wanderer_id=? AND source=? AND conversation_key=?",
                            (int(wanderer_id), source, key)).fetchone()[0])


def enqueue_social(conn: Any, wanderer: dict, key: str, peer: str) -> int:
    row = conn.execute("SELECT MAX(id) AS id FROM social_messages WHERE conversation_key=? AND sender_identity=? "
                       "AND message_type IN ('text','media','nudge')", (key, peer)).fetchone()
    if not row or not row["id"]:
        return 0
    from .social_conversation_service import dm_read_cursor
    handled = dm_read_cursor(conn, key, f"student:{wanderer['student_id']}")
    return enqueue(conn, int(wanderer["id"]), "social", key, peer, int(row["id"]), handled_id=handled)


def enqueue_legacy(conn: Any, message: dict) -> int:
    from .wanderer_identity_service import load_wanderers_by_student_ids
    if str(message.get("recipient_role")) != "student":
        return 0
    student_id = int(message.get("recipient_user_pk") or 0)
    wanderer = load_wanderers_by_student_ids(conn, [student_id]).get(student_id)
    if not wanderer:
        return 0
    first = conn.execute("SELECT MIN(id) AS id FROM private_messages WHERE conversation_key=? "
                         "AND recipient_identity=? AND read_at IS NULL",
                         (message["conversation_key"], message["recipient_identity"])).fetchone()
    return enqueue(conn, int(wanderer["id"]), "legacy", str(message["conversation_key"]),
                   str(message["sender_identity"]), int(message["id"]),
                   class_offering_id=message.get("class_offering_id"), handled_id=int(first["id"] or message["id"]) - 1)


def claim(limit: int, *, factory=None, inbox_id=None) -> list[dict]:
    """Global admission is serialized briefly, including across scheduler processes."""
    now = now_iso()
    with connection(factory) as conn:
        ensure_wanderer_schema(conn)
        serialize_on(conn, "wanderer-inbox-admission", ["global"])
        active = int(conn.execute("SELECT COUNT(*) FROM wanderer_social_inbox WHERE state='running' AND lease_until>?",
                                  (now,)).fetchone()[0])
        room = min(max(0, int(limit)), max(0, concurrency() - active))
        if not room:
            return []
        where = " AND id=?" if inbox_id is not None else ""
        args = [now, now, *([int(inbox_id)] if inbox_id is not None else []), room]
        rows = conn.execute("SELECT * FROM wanderer_social_inbox WHERE "
            "((state IN ('pending','retry') AND run_at<=?) OR (state='running' AND lease_until<=?))"
            + where + " ORDER BY CASE WHEN EXISTS (SELECT 1 FROM wanderer_agents w "
            "WHERE wanderer_social_inbox.peer_identity='student:' || CAST(w.student_id AS TEXT)) THEN 1 ELSE 0 END,"
            "run_at,id LIMIT ?", args).fetchall()
        result = []
        for raw in rows:
            row = dict(raw)
            token = uuid.uuid4().hex
            until = stamp_after(LEASE_SECONDS)
            conn.execute("UPDATE wanderer_social_inbox SET state='running',lease_token=?,lease_until=?,updated_at=? WHERE id=?",
                         (token, until, now, row["id"]))
            result.append({**row, "state": "running", "lease_token": token, "lease_until": until})
        conn.commit()
        return result


def owned(conn: Any, task: dict) -> dict | None:
    """Fence and lock the claim in a short transaction before any write/send."""
    result = conn.execute("UPDATE wanderer_social_inbox SET lease_until=lease_until "
        "WHERE id=? AND state='running' AND lease_token=? AND lease_until>?",
        (task["id"], task["lease_token"], now_iso()))
    if not result.rowcount:
        return None
    row = conn.execute("SELECT * FROM wanderer_social_inbox WHERE id=?", (task["id"],)).fetchone()
    return dict(row)


def renew(task: dict, *, factory=None) -> bool:
    with connection(factory) as conn:
        row = owned(conn, task)
        if not row:
            return False
        conn.execute("UPDATE wanderer_social_inbox SET lease_until=?,updated_at=? WHERE id=?",
                     (stamp_after(LEASE_SECONDS), now_iso(), task["id"]))
        conn.commit()
        return True


def release(task: dict, error: str, *, factory=None, delay: float | None = None, blocked=False) -> None:
    with connection(factory) as conn:
        row = owned(conn, task)
        if not row:
            return
        attempts = int(row["attempts"]) + (0 if delay is not None or blocked else 1)
        state = "blocked" if blocked else "retry"
        wait = delay if delay is not None else (900 if attempts >= MAX_ATTEMPTS else 5 * 2 ** max(0, attempts - 1))
        conn.execute("UPDATE wanderer_social_inbox SET state=?,attempts=?,last_error=?,run_at=?,lease_token='',"
                     "lease_until='',updated_at=? WHERE id=?",
                     (state, attempts, error[:400], stamp_after(wait), now_iso(), task["id"]))
        conn.commit()


def save_response(task: dict, snapshot_id: int, response: dict, *, factory=None) -> bool:
    with connection(factory) as conn:
        if not owned(conn, task):
            return False
        conn.execute("UPDATE wanderer_social_inbox SET snapshot_message_id=?,response_json=?,next_part=0,updated_at=? WHERE id=?",
                     (int(snapshot_id), json.dumps(response, ensure_ascii=False), now_iso(), task["id"]))
        conn.commit()
        return True


def recover(*, factory=None, limit: int = 40) -> int:
    """Bounded backfill of old unread DMs; existing work is never reset or claimed twice."""
    count = 0
    with connection(factory) as conn:
        ensure_wanderer_schema(conn)
        from .social_common import ensure_schema
        ensure_schema(conn)
        rows = conn.execute("""SELECT w.id AS wanderer_id,w.student_id,p.conversation_key,
            COALESCE(r.last_read_message_id,0) AS read_id
            FROM social_dm_participants p JOIN wanderer_agents w ON p.identity='student:' || CAST(w.student_id AS TEXT)
            LEFT JOIN social_dm_reads r ON r.reader_identity=p.identity AND r.conversation_key=p.conversation_key
            LEFT JOIN wanderer_social_inbox i ON i.wanderer_id=w.id AND i.source='social' AND i.conversation_key=p.conversation_key
            WHERE w.status='active' AND p.last_message_id>COALESCE(r.last_read_message_id,0) AND i.id IS NULL
            AND EXISTS (SELECT 1 FROM social_messages m WHERE m.conversation_key=p.conversation_key
              AND m.sender_identity<>p.identity AND m.id>COALESCE(r.last_read_message_id,0)
              AND m.message_type IN ('text','media','nudge'))
            ORDER BY p.last_message_id LIMIT ?""", (int(limit),)).fetchall()
        for raw in rows:
            row = dict(raw)
            me = f"student:{row['student_id']}"
            peers = row["conversation_key"].removeprefix("dm:").split("|")
            peer = next((p for p in peers if p != me), "")
            latest = conn.execute("SELECT MAX(id) AS id FROM social_messages WHERE conversation_key=? AND sender_identity=?",
                                  (row["conversation_key"], peer)).fetchone()
            if peer and latest["id"] and int(latest["id"]) > int(row["read_id"]):
                enqueue(conn, row["wanderer_id"], "social", row["conversation_key"], peer, latest["id"], handled_id=row["read_id"])
                count += 1
        rows = conn.execute("""SELECT w.id AS wanderer_id,m.conversation_key,m.sender_identity,m.class_offering_id,
            MAX(m.id) AS last_id,MIN(m.id) AS first_id FROM private_messages m
            JOIN wanderer_agents w ON m.recipient_identity='student:' || CAST(w.student_id AS TEXT)
            LEFT JOIN wanderer_social_inbox i ON i.wanderer_id=w.id AND i.source='legacy' AND i.conversation_key=m.conversation_key
            WHERE w.status='active' AND m.read_at IS NULL AND i.id IS NULL
            GROUP BY w.id,m.conversation_key,m.sender_identity,m.class_offering_id ORDER BY MIN(m.id) LIMIT ?""",
            (max(0, int(limit) - count),)).fetchall()
        for row in rows:
            enqueue(conn, row["wanderer_id"], "legacy", row["conversation_key"], row["sender_identity"], row["last_id"],
                    class_offering_id=row["class_offering_id"], handled_id=int(row["first_id"]) - 1)
            count += 1
        conn.commit()
    return count


def health_snapshot(*, factory=None) -> dict:
    """Operational metadata only: never expose messages, moods or private decision reasons."""
    now = utc_now()
    with connection(factory) as conn:
        ensure_wanderer_schema(conn)
        counts = {str(row["state"]): int(row["n"]) for row in conn.execute(
            "SELECT state,COUNT(*) AS n FROM wanderer_social_inbox GROUP BY state").fetchall()}
        row = conn.execute("SELECT MIN(run_at) AS oldest FROM wanderer_social_inbox "
                           "WHERE state IN ('pending','retry','running') AND latest_message_id>handled_message_id").fetchone()
        from .social_common import parse_iso
        oldest = parse_iso(row["oldest"]) if row and row["oldest"] else None
        active = int(conn.execute("SELECT COUNT(*) FROM wanderer_social_inbox WHERE state='running' AND lease_until>?",
                                  (now.isoformat(timespec="microseconds"),)).fetchone()[0])
    return {"counts": counts, "active_leases": active, "concurrency": concurrency(),
            "oldest_due_seconds": max(0, int((now - oldest).total_seconds())) if oldest else 0}
