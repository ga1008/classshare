"""散修好友：处理好友申请、偶尔主动加人、在线状态推送 (docs/social-friends-groups-2026-10-27.md §10).

* ``wanderer_friend_request`` handler — the personality decides: a teacher is
  always accepted; otherwise the chance grows with sociability (社牛 almost
  always, 社恐 often not). A shy wanderer may leave the request pending for a
  few hours first (once). Rude request notes are declined. Accept/decline go
  through ``social_friend_service`` with the wanderer's student-shaped user.
* ``maybe_befriend`` — during the regular tick, rarely (4 % per awake wanderer
  per tick, ≤ 2 per wanderer per week) the wanderer adds a real student who
  commented on / replied to its blog content or sent it a classroom private
  message in the last 30 days, with a short note in its own voice (one AI turn;
  a template when capped).
* ``sync_wanderer_presence`` — the simulated online state (active + inside
  active_hours) is recomputed every tick; only changes are pushed, and only to
  the wanderer's friends and co-group members. Going offline records
  ``user_presence.last_seen_at`` so friend cards show "last seen".
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from . import social_push_service
from .social_common import SocialError, blocked_either, ensure_schema, utc_day_start_iso
from .social_friend_service import accept_request, decline_request, send_request
from .social_identity_service import load_people
from .social_relations import (
    STATUS_PENDING,
    active_group_ids,
    count_friends,
    get_friendship,
    get_friendship_by_id,
    presence_audience,
)
from .wanderer_actions_common import ActionContext, as_int, behavior_on, outcome, record_action, run_turn, turn_parts
from .wanderer_actions_social import social_context
from .wanderer_identity_service import STATUS_ACTIVE, build_wanderer_user_dict, list_wanderers
from .wanderer_settings_service import wanderer_runtime_setting_get, wanderer_runtime_setting_set
from .wanderer_social_triggers import (
    SOCIAL_BEHAVIOR,
    TASK_KIND_FRIEND_REQUEST,
    TASK_MAX_ATTEMPTS,
    TASK_PRIORITY,
    friend_dedupe_key,
    is_awake_now,
    sociability,
)

DEFER_HOURS = (1.0, 4.0)
SHY_THRESHOLD = 0.35
DEFER_PROB = 0.4
PROACTIVE_PROB = 0.04
PROACTIVE_WEEKLY_LIMIT = 2
PROACTIVE_LOOKBACK_DAYS = 30
PROACTIVE_CANDIDATES = 50
REQUEST_NOTE_MAX = 40
SETTING_PRESENCE_STATE = "social_presence_state"
TARGET_REQUEST_IN = "friend_request"
TARGET_REQUEST_OUT = "friend_request_out"
_RUDE_WORDS = ("傻逼", "去死", "滚开", "废物", "垃圾人", "sb")

ConnFactory = Optional[Callable[[], Any]]


def _connect(conn_factory: ConnFactory) -> Any:
    if conn_factory is not None:
        return conn_factory()
    from ..database import get_db_connection

    return get_db_connection()


# ---------------------------------------------------------------------------
# Incoming requests
# ---------------------------------------------------------------------------

def accept_probability(wanderer: dict[str, Any], requester: dict[str, Any]) -> float:
    if str(requester.get("role") or "") == "teacher":
        return 1.0  # respect: a teacher is never turned down
    return round(min(0.98, 0.3 + 0.65 * sociability(wanderer)), 3)


def is_rude(note: Any) -> bool:
    text = str(note or "").lower()
    return any(word in text for word in _RUDE_WORDS)


def _pending_request(conn: Any, request_id: int, me: str, requester: str = "") -> Optional[dict[str, Any]]:
    """The live pending request addressed to ``me``: by pair when known (ids change on re-request), else by id."""
    row = get_friendship(conn, me, requester) if requester else get_friendship_by_id(conn, int(request_id))
    if not row or row["status"] != STATUS_PENDING or row["requester_identity"] == me:
        return None
    return row if me in (row["identity_a"], row["identity_b"]) else None


def _defer(ctx: ActionContext, request_id: int, requester: str) -> None:
    """Re-arm this very task a few hours later (the running claim no longer matches, so it stays pending)."""
    from .scheduled_task_service import schedule_task

    hours = ctx.rng.uniform(*DEFER_HOURS)
    with ctx.connect() as conn:
        schedule_task(conn, task_kind=TASK_KIND_FRIEND_REQUEST, run_at=datetime.now() + timedelta(hours=hours),
                      payload={"wanderer_id": ctx.wanderer_id, "request_id": int(request_id), "requester": requester,
                               "deferred": True},
                      dedupe_key=friend_dedupe_key(ctx.wanderer_id, requester), owner_role="system",
                      title=f"散修处理好友申请 · {ctx.display_name}", priority=TASK_PRIORITY,
                      max_attempts=TASK_MAX_ATTEMPTS, replace=True)
        conn.commit()


def _decide(ctx: ActionContext, row: dict[str, Any], role: str, deferred: bool) -> str:
    if is_rude(row.get("request_message")):
        return "decline"
    if role != "teacher" and not deferred and sociability(ctx.wanderer) < SHY_THRESHOLD and ctx.rng.random() < DEFER_PROB:
        return "defer"
    return "accept" if ctx.rng.random() < accept_probability(ctx.wanderer, {"role": role}) else "decline"


async def decide_friend_request(ctx: ActionContext, request_id: int, *, deferred: bool = False,
                                requester: str = "") -> dict[str, Any]:
    """Accept, decline or (shy, once) leave pending. Returns ``{"decision": ...}``."""
    with ctx.connect() as conn:
        row = _pending_request(conn, request_id, ctx.identity, requester)
        requester = (load_people(conn, [row["requester_identity"]]).get(row["requester_identity"]) or {}) if row else {}
    if row is None:
        return {"decision": "gone"}
    request_id = int(row["id"])
    target = {"target_type": TARGET_REQUEST_IN, "target_id": request_id}
    role = str(requester.get("role") or "")
    decision = _decide(ctx, row, role, deferred)
    if decision == "defer":
        _defer(ctx, request_id, str(row["requester_identity"]))
        record_action(ctx, "social_friend", **target, outcome="skipped", detail={"decision": "defer", "from": role})
        return {"decision": "defer"}
    events: list = []
    user = build_wanderer_user_dict(ctx.wanderer)
    try:
        with ctx.connect() as conn:
            (accept_request if decision == "accept" else decline_request)(conn, user, int(request_id), events)
            conn.commit()
    except SocialError as exc:
        record_action(ctx, "social_friend", **target, outcome="failed",
                      detail={"decision": decision, "error": str(exc.detail)[:200]})
        return {"decision": "failed"}
    await social_push_service.push_events(events)
    record_action(ctx, "social_friend", **target, detail={"decision": decision, "from": role})
    return {"decision": decision}


async def handle_wanderer_friend_request(task: dict[str, Any], *, ai_caller: Any = None, sleep: Any = None,
                                         rng: Optional[random.Random] = None, conn_factory: ConnFactory = None) -> str:
    payload = task.get("payload") or {}
    request_id = as_int(payload.get("request_id"))
    requester = str(payload.get("requester") or "")
    if request_id <= 0 and not requester:
        return "skipped: missing request"
    ctx, reason = social_context(payload.get("wanderer_id"), conn_factory=conn_factory, ai_caller=ai_caller,
                                 sleep=sleep, rng=rng)
    if ctx is None:
        return f"skipped: {reason}"
    result = await decide_friend_request(ctx, request_id, deferred=bool(payload.get("deferred")), requester=requester)
    return f"friend request: {result['decision']}"


# ---------------------------------------------------------------------------
# Proactive requests (tick)
# ---------------------------------------------------------------------------

def week_requests(conn: Any, wanderer_id: int, now: datetime) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM wanderer_action_log WHERE wanderer_id = ? AND action_kind = 'social_friend' "
        "AND target_type = ? AND outcome = 'ok' AND created_at >= ?",
        (int(wanderer_id), TARGET_REQUEST_OUT, (now - timedelta(days=7)).isoformat(timespec="seconds")),
    ).fetchone()
    return as_int(row["n"]) if row else 0


_ENGAGED_QUERIES = (
    "SELECT DISTINCT c.author_identity AS who FROM blog_comments c JOIN blog_posts p ON p.id = c.post_id "
    "WHERE p.author_identity = ? AND c.author_role = 'student' AND c.status = 'active' "
    "AND COALESCE(c.author_display_mode, 'real_name') = 'real_name' AND c.created_at >= ?",
    "SELECT DISTINCT c.author_identity AS who FROM blog_comments c JOIN blog_comments parent "
    "ON parent.id = c.parent_comment_id WHERE parent.author_identity = ? AND c.author_role = 'student' "
    "AND c.status = 'active' AND COALESCE(c.author_display_mode, 'real_name') = 'real_name' AND c.created_at >= ?",
    "SELECT DISTINCT sender_identity AS who FROM private_messages WHERE recipient_identity = ? "
    "AND sender_role = 'student' AND created_at >= ?",
)


def engaged_students(conn: Any, identity: str, now: datetime) -> list[str]:
    """Real students who commented on/replied to this wanderer's blog content or messaged it (30 days).

    Anonymous / pen-name comments are excluded: the wanderer must not learn who wrote them.
    """
    since = (now - timedelta(days=PROACTIVE_LOOKBACK_DAYS)).isoformat(timespec="seconds")
    found: list[str] = []
    for sql in _ENGAGED_QUERIES:
        found.extend(str(row["who"]) for row in conn.execute(sql, (identity, since)).fetchall())
    return list(dict.fromkeys(item for item in found if item and item != identity))[:PROACTIVE_CANDIDATES]


def befriend_candidates(conn: Any, identity: str, now: datetime) -> list[dict[str, Any]]:
    ensure_schema(conn)
    wanted = engaged_students(conn, identity, now)
    people = load_people(conn, wanted)
    return [
        people[item] for item in wanted
        if item in people and people[item]["active"] and not people[item]["is_wanderer"]
        and get_friendship(conn, identity, item) is None and not blocked_either(conn, identity, item)
    ]


async def _request_note(ctx: ActionContext, person: dict[str, Any]) -> tuple[str, int]:
    scene = {
        "kind": "加好友", "title": f"想加 {person['name']} 为好友",
        "meta": "对方在你的帖子或评论下和你互动过，或者给你发过私信",
        "counterpart": {"name": person["name"], "is_wanderer": False, "role_label": "真实学生"},
        "task": f"写一句很短的好友申请附言（不超过 {REQUEST_NOTE_MAX // 2} 字），像你平时说话；用 reply 输出一条。",
    }
    result = await run_turn(ctx, scene=scene, thread_key=f"social:friend:{person['identity']}", suffix="friend_note")
    parts = turn_parts(ctx, result.get("turn") or {})
    if parts:
        return parts[0][:REQUEST_NOTE_MAX], int(result.get("ai_calls") or 0)
    return f"我是{ctx.display_name}，看过你的留言，交个朋友？", int(result.get("ai_calls") or 0)


async def maybe_befriend(ctx: ActionContext, *, chance: float = PROACTIVE_PROB) -> list[dict[str, Any]]:
    """Rarely send one friend request to a real student who engaged with this wanderer."""
    if not behavior_on(ctx.wanderer, SOCIAL_BEHAVIOR) or ctx.rng.random() >= chance:
        return []
    now = ctx.current_time()
    with ctx.connect() as conn:
        if week_requests(conn, ctx.wanderer_id, now) >= PROACTIVE_WEEKLY_LIMIT:
            return []
        candidates = befriend_candidates(conn, ctx.identity, now)
    if not candidates:
        return []
    person = ctx.rng.choice(candidates)
    note, calls = await _request_note(ctx, person)
    target = {"target_type": TARGET_REQUEST_OUT, "target_id": person["identity"]}
    events: list = []
    try:
        with ctx.connect() as conn:
            result = send_request(conn, build_wanderer_user_dict(ctx.wanderer), person["identity"], note, events)
            conn.commit()
    except SocialError as exc:
        record_action(ctx, "social_friend", **target, content_preview=note, ai_calls=calls, outcome="failed",
                      detail={"error": str(exc.detail)[:200]})
        return [outcome("social_friend", "failed", ai_calls=calls)]
    await social_push_service.push_events(events)
    record_action(ctx, "social_friend", **target, content_preview=note, ai_calls=calls,
                  detail={"decision": "request", "status": result.get("status")})
    return [outcome("social_friend", ai_calls=calls)]


# ---------------------------------------------------------------------------
# Presence (tick)
# ---------------------------------------------------------------------------

def _presence_state(wanderers: list[dict[str, Any]], now: datetime) -> dict[str, int]:
    return {str(item["student_id"]): int(str(item.get("status") or "") == STATUS_ACTIVE and is_awake_now(item, now))
            for item in wanderers}


def _load_previous_state(conn: Any) -> Optional[dict[str, Any]]:
    raw = wanderer_runtime_setting_get(conn, SETTING_PRESENCE_STATE)
    try:
        previous = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        return None
    return previous if isinstance(previous, dict) else None


async def sync_wanderer_presence(conn_factory: ConnFactory = None, *, now: Optional[datetime] = None) -> dict[str, Any]:
    """Push simulated online/offline changes since the previous tick to friends and co-group members.

    The first run only records the state (no burst of pushes after a deploy).
    """
    moment = now or datetime.now()
    pushes: list[tuple[list[str], dict[str, Any]]] = []
    with _connect(conn_factory) as conn:
        ensure_schema(conn)
        state = _presence_state(list_wanderers(conn), moment)
        previous = _load_previous_state(conn)
        wanderer_runtime_setting_set(conn, SETTING_PRESENCE_STATE, json.dumps(state, sort_keys=True))
        stamp = datetime.now(timezone.utc).isoformat()
        for student_id, online in state.items():
            if previous is None or previous.get(student_id) == online:
                continue
            identity = f"student:{student_id}"
            conn.execute("INSERT INTO user_presence (identity, last_seen_at) VALUES (?, ?) "
                         "ON CONFLICT (identity) DO UPDATE SET last_seen_at = excluded.last_seen_at", (identity, stamp))
            audience = sorted(presence_audience(conn, identity))
            if audience:
                pushes.append((audience, {"type": "presence", "identity": identity, "online": bool(online),
                                          "last_seen_at": stamp}))
        conn.commit()
    for audience, event in pushes:
        await social_push_service.push(audience, event)
    return {"wanderers": len(state), "pushed": len(pushes), "first_run": previous is None}


async def run_social_upkeep(conn_factory: ConnFactory, wanderer_ids: list[int], *, ai_caller: Any = None,
                            sleep: Any = None, rng: Optional[random.Random] = None,
                            now: Optional[datetime] = None) -> dict[str, Any]:
    """Tick step: presence changes for everyone, a rare proactive request per processed wanderer."""
    summary: dict[str, Any] = {"presence": await sync_wanderer_presence(conn_factory, now=now), "friend_requests": 0}
    for wanderer_id in wanderer_ids:
        ctx, _reason = social_context(wanderer_id, conn_factory=conn_factory, ai_caller=ai_caller, sleep=sleep, rng=rng)
        if ctx is None:
            continue
        ctx.now = now
        done = await maybe_befriend(ctx)
        summary["friend_requests"] += sum(1 for item in done if item["outcome"] == "ok")
    return summary


# ---------------------------------------------------------------------------
# Admin stats
# ---------------------------------------------------------------------------

def social_counts(conn: Any, student_id: int) -> dict[str, int]:
    """Friends, live groups, messages sent today (UTC day), pending incoming requests."""
    ensure_schema(conn)
    identity = f"student:{int(student_id)}"
    sent = conn.execute(
        "SELECT COUNT(*) AS n FROM social_messages WHERE sender_identity = ? AND created_at >= ? "
        "AND message_type IN ('text', 'media', 'nudge')", (identity, utc_day_start_iso()),
    ).fetchone()
    pending = conn.execute(
        "SELECT COUNT(*) AS n FROM social_friendships WHERE status = 'pending' AND requester_identity <> ? "
        "AND (identity_a = ? OR identity_b = ?)", (identity, identity, identity),
    ).fetchone()
    return {"friends": count_friends(conn, identity), "groups": len(active_group_ids(conn, identity)),
            "messages_today": as_int(sent["n"]) if sent else 0,
            "pending_requests": as_int(pending["n"]) if pending else 0}
