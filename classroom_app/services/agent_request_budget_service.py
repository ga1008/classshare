"""Persistent Agent capacity limits, independent of IP and Python worker count.

Verify the current delegation first, then reserve and COMMIT a short transaction
before network or tool work. Always finish in another short transaction, even
after cancellation. No caller may hold the DB connection while awaiting work.
The caller must enforce its own deadline at or before BudgetLease.expires_at;
expired leases may be reclaimed without waiting for a crashed worker.

The per-channel global bucket is the common mutex for all reservations in that
channel. Mutations are tiny, ordered and contain no domain queries or network.
The service never commits, rolls back, runs DDL, or resets history on failure.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import time
import uuid

from fastapi import HTTPException

from .agent_delegation_service import VerifiedDelegation


@dataclass(frozen=True)
class ScopeBudget:
    capacity: int
    refill_per_second: float
    concurrent: int


@dataclass(frozen=True)
class ChannelBudget:
    platform: ScopeBudget
    actor: ScopeBudget
    task: ScopeBudget
    task_total: int
    lease_seconds: int


# Bursts accommodate a normal parallel tool turn. Sustained shell loops cannot
# consume all workers, and another task cannot bypass the same user's budget.
CHANNEL_BUDGETS = {
    "tools": ChannelBudget(ScopeBudget(40, 12, 8), ScopeBudget(16, 4, 4), ScopeBudget(8, 2, 2), 600, 45),
    "web": ChannelBudget(ScopeBudget(8, 1, 4), ScopeBudget(4, .2, 2), ScopeBudget(2, .1, 1), 50, 25),
    "model": ChannelBudget(ScopeBudget(12, 2, 6), ScopeBudget(6, .4, 3), ScopeBudget(3, .2, 2), 120, 305),
}


@dataclass(frozen=True)
class BudgetLease:
    id: str
    channel: str
    task_id: int
    expires_at: float  # Unix seconds, for the caller's absolute work deadline.


def _milliseconds(now):
    timestamp = time.time() if now is None else now
    if isinstance(timestamp, bool) or not isinstance(timestamp, (float, int)) or not math.isfinite(timestamp) or timestamp < 0:
        raise ValueError("Invalid budget timestamp")
    return int(timestamp * 1000)


def _limited(message, retry_seconds):
    raise HTTPException(429, message, headers={"Retry-After": str(max(1, math.ceil(retry_seconds)))})


def reserve_agent_request_budget(conn, *, grant: VerifiedDelegation, channel: str = "tools",
                                  request_id: str | None = None, now: float | None = None) -> BudgetLease:
    """Reserve accepted work once; request_id must be generated server-side.

    Reusing even an active request_id is rejected: it must never authorize a
    second physical network call while counting only one concurrency slot.
    """
    policy = CHANNEL_BUDGETS.get(channel)
    if policy is None:
        raise ValueError("Unknown Agent budget channel")
    required_purpose = "model" if channel == "model" else "tools"
    if grant.delegation.get("purpose") != required_purpose:
        raise HTTPException(403, "该任务凭据不能使用此请求通道。")
    timestamp = _milliseconds(now)
    task_id = int(grant.task["id"])
    actor = grant.actor
    identifier = request_id if request_id is not None else str(uuid.uuid4())
    if not isinstance(identifier, str) or not 1 <= len(identifier) <= 100 or not identifier.isascii() or any(ord(c) < 33 for c in identifier):
        raise ValueError("Invalid server-generated budget request id")
    scopes = [("global", policy.platform), (f"actor:{actor.role}:{actor.id}", policy.actor), (f"task:{task_id}", policy.task)]

    # Always take the same global/channel mutex first. Its database row protects
    # the actor/task token buckets and lease count across every application host.
    conn.execute("""
        INSERT INTO agent_request_buckets (scope_key, channel, tokens, updated_at_ms)
        VALUES ('global', ?, ?, ?) ON CONFLICT (scope_key, channel) DO NOTHING
    """, (channel, policy.platform.capacity, timestamp))
    conn.execute("UPDATE agent_request_buckets SET tokens = tokens WHERE scope_key = 'global' AND channel = ?", (channel,))
    for scope, limit in scopes[1:]:
        conn.execute("""
            INSERT INTO agent_request_buckets (scope_key, channel, tokens, updated_at_ms)
            VALUES (?, ?, ?, ?) ON CONFLICT (scope_key, channel) DO NOTHING
        """, (scope, channel, limit.capacity, timestamp))
    if conn.execute("SELECT id FROM agent_request_budget_leases WHERE id = ?", (identifier,)).fetchone():
        raise HTTPException(409, "该预算请求编号已经使用，不能重复执行。")
    rows = {row["scope_key"]: dict(row) for row in conn.execute("""
        SELECT * FROM agent_request_buckets WHERE channel = ? AND scope_key IN (?, ?, ?)
    """, (channel, *(scope for scope, _ in scopes))).fetchall()}
    if int(rows[scopes[2][0]]["used_count"]) >= policy.task_total:
        _limited("本任务已达到该通道的请求总预算，请结束任务并核对执行结果。", 60)

    updated = []
    for scope, limit in scopes:
        row = rows[scope]
        elapsed = max(0, timestamp - int(row["updated_at_ms"])) / 1000
        available = min(float(limit.capacity), float(row["tokens"]) + elapsed * limit.refill_per_second)
        if available < 1:
            _limited("Agent 请求频率过高，请按返回的等待时间重试。", (1 - available) / limit.refill_per_second)
        updated.append((available - 1, max(timestamp, int(row["updated_at_ms"])), scope))

    counts = conn.execute("""
        SELECT COUNT(*) AS platform_running,
            COALESCE(SUM(CASE WHEN actor_role = ? AND actor_id = ? THEN 1 ELSE 0 END), 0) AS actor_running,
            COALESCE(SUM(CASE WHEN task_id = ? THEN 1 ELSE 0 END), 0) AS task_running,
            MIN(expires_at_ms) AS next_expiry
        FROM agent_request_budget_leases WHERE channel = ? AND status = 'active' AND expires_at_ms > ?
    """, (actor.role, actor.id, task_id, channel, timestamp)).fetchone()
    if (int(counts["platform_running"]) >= policy.platform.concurrent
            or int(counts["actor_running"]) >= policy.actor.concurrent
            or int(counts["task_running"]) >= policy.task.concurrent):
        _limited("Agent 请求并发已满，请等待正在执行的请求结束。", (int(counts["next_expiry"]) - timestamp) / 1000)

    for tokens, updated_at, scope in updated:
        conn.execute("""
            UPDATE agent_request_buckets SET tokens = ?, updated_at_ms = ?, used_count = used_count + 1
            WHERE scope_key = ? AND channel = ?
        """, (tokens, updated_at, scope, channel))
    expires_at = timestamp + policy.lease_seconds * 1000
    conn.execute("""
        INSERT INTO agent_request_budget_leases (
            id, channel, task_id, actor_role, actor_id, attempt_id, fencing_token,
            status, created_at_ms, expires_at_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
    """, (identifier, channel, task_id, actor.role, actor.id, grant.attempt["id"],
          grant.attempt["fencing_token"], timestamp, expires_at))
    return BudgetLease(identifier, channel, task_id, expires_at / 1000)


def finish_agent_request_budget(conn, lease_id: str, *, status: str = "completed", now: float | None = None) -> bool:
    """Release once without refunding rate/total budgets; no live token needed.

    This is an internal cleanup API, not an endpoint. A canceled request still
    occupies its slot until its actual work stops and cleanup reaches this call.
    """
    if status not in {"completed", "failed", "canceled"}:
        raise ValueError("Invalid Agent budget completion status")
    cursor = conn.execute("""
        UPDATE agent_request_budget_leases SET status = ?, finished_at_ms = ?
        WHERE id = ? AND status = 'active'
    """, (status, _milliseconds(now), str(lease_id)))
    return cursor.rowcount == 1


def renew_agent_request_budget(conn, lease_id: str, *, now: float | None = None) -> bool:
    """Keep capacity occupied while shielded work is physically still running.

    This does not renew a delegation, execution attempt, or permission. The
    trusted task supervisor alone calls it while it retains a live work handle.
    Expired/finished leases cannot be resurrected, and no counters are refunded.
    """
    timestamp = _milliseconds(now)
    row = conn.execute("SELECT channel, expires_at_ms FROM agent_request_budget_leases WHERE id = ? AND status = 'active'", (str(lease_id),)).fetchone()
    if row is None or int(row["expires_at_ms"]) <= timestamp:
        return False
    policy = CHANNEL_BUDGETS.get(row["channel"])
    if policy is None:
        return False
    expiry = max(int(row["expires_at_ms"]), timestamp + policy.lease_seconds * 1000)
    cursor = conn.execute("""
        UPDATE agent_request_budget_leases SET expires_at_ms = ?
        WHERE id = ? AND status = 'active' AND expires_at_ms = ? AND expires_at_ms > ?
    """, (expiry, str(lease_id), int(row["expires_at_ms"]), timestamp))
    return cursor.rowcount == 1
