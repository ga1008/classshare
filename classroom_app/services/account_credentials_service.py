"""Credential changes revoke existing Agent grants in the caller's transaction.

Ordinary logout deliberately does not call this helper: persistent authority has
its own user-visible lifetime. No password or hash is accepted or returned here.
"""
from __future__ import annotations

import hashlib
import sqlite3
import time


def lock_actor_authorization_transition(conn, *, role: str, user_id: int) -> None:
    """Serialize issuance and credential revocation, including logged-out actors.

    PostgreSQL's two-int advisory namespace is separate from existing bigint
    account/task locks. A hash collision only serializes unrelated actors; it
    never grants rights. SQLite already serializes writers. Call after any task
    locks and before account writes; never acquire another running task after it.
    No user_sessions row is required for a persistent authorization source.
    """
    if role not in {"teacher", "student"} or type(user_id) is not int or user_id <= 0:
        raise ValueError("Invalid authorization owner")
    if isinstance(conn, sqlite3.Connection):
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        return
    key = int.from_bytes(hashlib.sha256(f'{role}:{user_id}'.encode()).digest()[:4], 'big', signed=True)
    conn.execute("SELECT pg_advisory_xact_lock(?, ?)", (0x4C534143, key))


def prepare_credentials_change(conn, *, role: str, user_id: int) -> None:
    """Take transition locks before reading/updating any password/account row."""
    prepare_actor_authority_changes(conn, role=role, user_ids=[user_id])


def prepare_actor_authority_changes(conn, *, role: str, user_ids) -> frozenset[int]:
    """Prepare a bounded set with one task lookup per 400 ids, not per person.

    All running task locks precede all actor mutexes, in stable order. Call at
    the beginning of the domain transaction, before any account/resource writes.
    """
    values = list(user_ids)
    if role not in {"teacher", "student"} or any(type(value) is not int or value <= 0 for value in values):
        raise ValueError("Invalid credential owner")
    ids = sorted(set(values))
    if not ids:
        return frozenset()
    if role == "teacher":
        from .teacher_account_service import lock_teacher_account_management
        lock_teacher_account_management(conn)
    # A write already holding its task lock finishes before this revocation;
    # later ledger claims observe revoked authority. Do not stop domain jobs
    # already submitted with ordinary Web semantics.
    tasks = {}
    for offset in range(0, len(ids), 400):
        chunk = ids[offset:offset + 400]
        placeholders = ','.join('?' for _ in chunk)
        rows = conn.execute(f"SELECT id,actor_id FROM agent_tasks WHERE actor_role=? AND actor_id IN ({placeholders}) AND status='running' ORDER BY id", (role, *chunk)).fetchall()
        tasks.update({int(row['id']): int(row['actor_id']) for row in rows})
    for task_id, actor_id in sorted(tasks.items()):
        conn.execute("UPDATE agent_tasks SET status=status WHERE id=? AND actor_role=? AND actor_id=? AND status='running'", (task_id, role, actor_id))
    # A newly running task can miss the snapshot above. The stable actor mutex
    # still orders its grant INSERT against the following revoke UPDATEs.
    for user_id in ids:
        lock_actor_authorization_transition(conn, role=role, user_id=user_id)
    return frozenset(ids)


def credentials_changed(conn, *, role: str, user_id: int, invalidate_sessions: bool = False) -> dict[str, int]:
    """Revoke in the prepared transition; never acquire task locks after writes."""
    return actor_authority_changed(conn, role=role, user_id=user_id,
                                   reason="account_credential_changed", invalidate_sessions=invalidate_sessions)


def actor_authority_changed(conn, *, role: str, user_id: int, reason: str,
                            invalidate_sessions: bool = False) -> dict[str, int]:
    """Permanently revoke grants after a prepared account/lifecycle transition.

    The caller takes prepare_credentials_change's task/actor locks before any
    account update. Restoring a former status never restores these grants.
    """
    if role not in {"teacher", "student"} or type(user_id) is not int or user_id <= 0:
        raise ValueError("Invalid credential owner")
    if not isinstance(reason, str) or not 1 <= len(reason) <= 100 or any(ord(c) < 32 for c in reason):
        raise ValueError("Invalid authority transition reason")
    now = int(time.time())
    grants = conn.execute(
        "UPDATE agent_task_delegations SET status='revoked',revoked_at=?,revoke_reason=? "
        "WHERE actor_role=? AND actor_id=? AND status='active'", (now, reason, role, user_id),
    ).rowcount
    persistent = conn.execute(
        "UPDATE agent_persistent_authorizations SET status='revoked',revoked_at=?,revoke_reason=? "
        "WHERE actor_role=? AND actor_id=? AND status='active'", (now, reason, role, user_id),
    ).rowcount
    if invalidate_sessions:
        from ..dependencies import invalidate_session_for_user
        invalidate_session_for_user(str(user_id), role, conn=conn)
    return {"delegations_revoked": grants, "persistent_authorizations_revoked": persistent}
