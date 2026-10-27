"""散修社交触发 (docs/social-friends-groups-2026-10-27.md §10).

Runs inside the business transaction that created a friend request or a chat
message (app process) and only registers one-shot scheduler tasks — no model
call, no sleep. The scheduler container does the work later
(``wanderer_actions_social`` / ``wanderer_social_friends``):

* ``wanderer_friend_request``  per (wanderer, requester), immediate or delayed by personality / active window;
* ``wanderer_social_reply``    per (wanderer, conversation), delayed 20 s–10 min
  (or to the next active window), bursts coalesce into the pending task;
* ``wanderer_group_watch``     per group, debounced 30–120 s; when it fires each
  wanderer in the group decides independently and is staggered.

Coalescing: a *pending* task with the same dedupe key is kept as is (its run
time is not pushed back, so a chatty peer cannot postpone the answer forever);
a running/finished one is re-armed — the scheduler's success update only
matches the claim it made, so a task re-armed while running runs once more.

Nothing is scheduled while the 散修 system is switched off, for paused/dead
wanderers, or when the wanderer's ``social`` behavior is off.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

TASK_KIND_FRIEND_REQUEST = "wanderer_friend_request"
TASK_KIND_SOCIAL_REPLY = "wanderer_social_reply"
TASK_KIND_GROUP_WATCH = "wanderer_group_watch"
SOCIAL_BEHAVIOR = "social"

FRIEND_DELAY_SECONDS = (0, 1800)
REPLY_DELAY_SECONDS = (20, 600)
GROUP_DEBOUNCE_SECONDS = (30, 120)
WAKE_JITTER_SECONDS = (60, 900)
NEXT_WINDOW_HORIZON_HOURS = 48
TASK_PRIORITY = 110
TASK_MAX_ATTEMPTS = 2
_SHY_MARKS = ("社恐", "寡言", "冷淡", "慢热", "沉默", "内向")
_OUTGOING_MARKS = ("社牛", "元气", "热血", "段子", "组织者", "乐天")


# ---------------------------------------------------------------------------
# Personality math (pure)
# ---------------------------------------------------------------------------

def _prob(policy: dict[str, Any], key: str, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(policy.get(key, default))))
    except (TypeError, ValueError):
        return default


def sociability(wanderer: dict[str, Any]) -> float:
    """0 (社恐) … 1 (社牛) from activity_level, reply_policy.ignore_prob and the archetype."""
    try:
        level = max(1, min(5, int(wanderer.get("activity_level") or 3)))
    except (TypeError, ValueError):
        level = 3
    ignore = _prob(wanderer.get("reply_policy") or {}, "ignore_prob", 0.3)
    score = 0.5 * (level - 1) / 4 + 0.5 * (1.0 - ignore)
    archetype = str(wanderer.get("archetype") or "")
    if any(mark in archetype for mark in _SHY_MARKS):
        score -= 0.25
    if any(mark in archetype for mark in _OUTGOING_MARKS):
        score += 0.2
    return round(max(0.0, min(1.0, score)), 3)


def _scaled_delay(wanderer: dict[str, Any], bounds: tuple[int, int], rng: random.Random) -> int:
    low, high = bounds
    shyness = 1.0 - sociability(wanderer)
    base = low + (high - low) * shyness * shyness
    return int(max(low, min(high, base * rng.uniform(0.6, 1.3))))


def friend_request_delay(wanderer: dict[str, Any], rng: random.Random) -> int:
    """An outgoing personality may consider a request immediately; others take their time."""
    if rng.random() < sociability(wanderer) * 0.5:
        return 0
    return _scaled_delay(wanderer, FRIEND_DELAY_SECONDS, rng)


def reply_delay(wanderer: dict[str, Any], rng: random.Random) -> int:
    """20 s … 10 min."""
    return _scaled_delay(wanderer, REPLY_DELAY_SECONDS, rng)


def _hours(wanderer: dict[str, Any]) -> list[Any]:
    return list((wanderer.get("schedule") or {}).get("active_hours") or [])


def is_awake_now(wanderer: dict[str, Any], now: datetime) -> bool:
    from .wanderer_behavior_service import _in_active_hours

    return _in_active_hours(_hours(wanderer), now)


def seconds_until_awake(wanderer: dict[str, Any], now: datetime) -> Optional[float]:
    """0 inside an active window; seconds to the next preferred window within two days."""
    if is_awake_now(wanderer, now):
        return 0.0
    probe = now.replace(second=0, microsecond=0)
    for minutes in range(10, NEXT_WINDOW_HORIZON_HOURS * 60 + 1, 10):
        moment = probe + timedelta(minutes=minutes)
        if is_awake_now(wanderer, moment):
            return (moment - now).total_seconds()
    return None


def delay_with_wakeup(wanderer: dict[str, Any], delay: float, now: datetime, rng: random.Random) -> Optional[float]:
    """``delay`` when awake; otherwise the next preferred window plus a small jitter."""
    wait = seconds_until_awake(wanderer, now)
    if wait is None:
        return None
    if wait <= 0:
        return float(delay)
    return wait + rng.uniform(*WAKE_JITTER_SECONDS)


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------

def reply_dedupe_key(wanderer_id: int, conversation_key: str) -> str:
    return f"wanderer:social:{int(wanderer_id)}:{conversation_key}"


def group_watch_dedupe_key(group_id: int) -> str:
    return f"wanderer:group-watch:{int(group_id)}"


def friend_dedupe_key(wanderer_id: int, requester: str) -> str:
    """Per (wanderer, requester): withdraw + re-request cycles coalesce into one pending decision."""
    return f"wanderer:friend:{int(wanderer_id)}:{requester}"


def _merge_flags(conn: Any, task: dict[str, Any], merge: dict[str, Any]) -> None:
    try:
        current = json.loads(task.get("payload_json") or "{}")
    except (TypeError, ValueError):
        current = {}
    current = current if isinstance(current, dict) else {}
    updated = {**current, **{key: bool(current.get(key)) or bool(value) for key, value in merge.items()}}
    if updated != current:
        conn.execute("UPDATE scheduled_tasks SET payload_json = ? WHERE id = ?",
                     (json.dumps(updated, ensure_ascii=False, separators=(",", ":")), int(task["id"])))


def schedule_coalesced(conn: Any, *, task_kind: str, dedupe_key: str, run_at: datetime, payload: dict[str, Any],
                       title: str, merge: Optional[dict[str, Any]] = None) -> int:
    """Keep a pending task (OR-merging ``merge`` flags into its payload); re-arm anything else."""
    from .scheduled_task_service import get_owner_task_by_dedupe, schedule_task

    existing = get_owner_task_by_dedupe(conn, dedupe_key)
    if existing and str(existing.get("status") or "") == "pending":
        if merge:
            _merge_flags(conn, existing, merge)
        return int(existing["id"])
    return schedule_task(conn, task_kind=task_kind, run_at=run_at, payload=payload, dedupe_key=dedupe_key,
                         owner_role="system", title=title, priority=TASK_PRIORITY, max_attempts=TASK_MAX_ATTEMPTS,
                         replace=True)


def schedule_social_reply(conn: Any, wanderer: dict[str, Any], conversation_key: str, *, delay_seconds: float,
                          mentioned: bool = False, now: Optional[datetime] = None) -> int:
    moment = now or datetime.now()
    payload = {"wanderer_id": int(wanderer["id"]), "conversation_key": conversation_key, "mentioned": bool(mentioned)}
    return schedule_coalesced(
        conn, task_kind=TASK_KIND_SOCIAL_REPLY, dedupe_key=reply_dedupe_key(int(wanderer["id"]), conversation_key),
        run_at=moment + timedelta(seconds=max(0.0, float(delay_seconds))), payload=payload,
        title=f"散修聊天回复 · {wanderer.get('display_name') or ''}", merge={"mentioned": mentioned},
    )


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

def _enabled(conn: Any) -> bool:
    from .wanderer_settings_service import is_wanderer_runtime_enabled

    return is_wanderer_runtime_enabled(conn)


def social_wanderers(conn: Any, identities: Iterable[str]) -> dict[str, dict[str, Any]]:
    """``{identity: wanderer}`` for active 散修 among ``identities`` whose ``social`` behavior is on."""
    from .wanderer_actions_common import behavior_on
    from .wanderer_identity_service import STATUS_ACTIVE, load_wanderers_by_student_ids

    ids = []
    for identity in {str(item) for item in identities if item}:
        role, _sep, pk = identity.partition(":")
        if role == "student" and pk.isdigit():
            ids.append(int(pk))
    if not ids:
        return {}
    found = load_wanderers_by_student_ids(conn, ids)
    return {
        f"student:{student_id}": wanderer for student_id, wanderer in found.items()
        if str(wanderer.get("status") or "") == STATUS_ACTIVE and behavior_on(wanderer, SOCIAL_BEHAVIOR)
    }


# ---------------------------------------------------------------------------
# Hooks (called inside the caller's transaction; never commit)
# ---------------------------------------------------------------------------

def on_message_created(conn: Any, parsed: dict[str, Any], sender: str, *, rng: Optional[random.Random] = None,
                       now: Optional[datetime] = None) -> list[int]:
    """A text/media/nudge message was stored in ``parsed['key']`` by ``sender``."""
    rng = rng or random.Random()
    moment = now or datetime.now()
    if parsed["type"] == "dm":
        peer = parsed.get("peer") or ""
        wanderers = social_wanderers(conn, [peer]) if peer != sender else {}
        if not wanderers or not _enabled(conn):
            return []
        wanderer = wanderers[peer]
        delay = delay_with_wakeup(wanderer, reply_delay(wanderer, rng), moment, rng)
        if delay is None:
            return []  # no preferred activity window within the bounded lookahead
        return [schedule_social_reply(conn, wanderer, parsed["key"], delay_seconds=delay, now=moment)]
    from .social_relations import group_member_identities

    members = [item for item in group_member_identities(conn, parsed["group_id"]) if item != sender]
    if not social_wanderers(conn, members) or not _enabled(conn):
        return []
    group_id = int(parsed["group_id"])
    return [schedule_coalesced(
        conn, task_kind=TASK_KIND_GROUP_WATCH, dedupe_key=group_watch_dedupe_key(group_id),
        run_at=moment + timedelta(seconds=rng.randint(*GROUP_DEBOUNCE_SECONDS)), payload={"group_id": group_id},
        title="散修群聊观察",
    )]


def on_friend_request(conn: Any, request_id: int, requester: str, target: str, *,
                      rng: Optional[random.Random] = None, now: Optional[datetime] = None,
                      recovery: bool = False) -> Optional[int]:
    """A pending request from ``requester`` to ``target`` exists; schedule the 散修's decision."""
    wanderers = social_wanderers(conn, [target]) if requester != target else {}
    if not wanderers or not _enabled(conn):
        return None
    from .social_common import serialize_on
    from .scheduled_task_service import get_owner_task_by_dedupe

    dedupe = friend_dedupe_key(int(wanderers[target]["id"]), requester)
    serialize_on(conn, "wanderer-friend-schedule", [dedupe])
    existing = get_owner_task_by_dedupe(conn, dedupe)
    if recovery and existing and existing.get("status") in {"pending", "running"}:
        return None  # another recovery already queued/claimed it since our candidate snapshot
    rng = rng or random.Random()
    wanderer = wanderers[target]
    moment = now or datetime.now()
    delay = delay_with_wakeup(wanderer, friend_request_delay(wanderer, rng), moment, rng)
    if delay is None:
        delay = NEXT_WINDOW_HORIZON_HOURS * 3600.0  # decided later; the request just waits
    return schedule_coalesced(
        conn, task_kind=TASK_KIND_FRIEND_REQUEST, dedupe_key=dedupe,
        run_at=moment + timedelta(seconds=delay),
        payload={"wanderer_id": int(wanderer["id"]), "request_id": int(request_id), "requester": requester},
        title=f"散修处理好友申请 · {wanderer.get('display_name') or ''}",
    )
