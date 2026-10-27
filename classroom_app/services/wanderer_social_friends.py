"""散修好友：处理好友申请、偶尔主动加人、在线状态推送 (docs/social-friends-groups-2026-10-27.md §10).

* ``wanderer_friend_request`` — one personality-driven model decision with a
  private reason, current public profile and relationship memory. A short CAS
  transaction applies the decision and records its reason; failures defer.
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
from .wanderer_identity_service import STATUS_ACTIVE, build_wanderer_user_dict, list_wanderers, load_wanderer, log_wanderer_action
from .wanderer_memory_service import append_turn
from .wanderer_prompt_service import extract_json_object
from .wanderer_social_context import context_block, friend_context
from .wanderer_settings_service import wanderer_runtime_setting_get, wanderer_runtime_setting_set
from .wanderer_social_triggers import (
    SOCIAL_BEHAVIOR,
    TASK_KIND_FRIEND_REQUEST,
    TASK_MAX_ATTEMPTS,
    TASK_PRIORITY,
    friend_dedupe_key,
    is_awake_now,
    on_friend_request,
    seconds_until_awake,
)

PROACTIVE_PROB = 0.04
PROACTIVE_WEEKLY_LIMIT = 2
PROACTIVE_LOOKBACK_DAYS = 30
PROACTIVE_CANDIDATES = 50
REQUEST_NOTE_MAX = 40
SETTING_PRESENCE_STATE = "social_presence_state"
TARGET_REQUEST_IN = "friend_request"
TARGET_REQUEST_OUT = "friend_request_out"

ConnFactory = Optional[Callable[[], Any]]


def _connect(conn_factory: ConnFactory) -> Any:
    if conn_factory is not None:
        return conn_factory()
    from ..database import get_db_connection

    return get_db_connection()


# ---------------------------------------------------------------------------
# Incoming requests
# ---------------------------------------------------------------------------

def _pending_request(conn: Any, request_id: int, me: str, requester: str = "") -> Optional[dict[str, Any]]:
    """The live pending request addressed to ``me``: by pair when known (ids change on re-request), else by id."""
    row = get_friendship(conn, me, requester) if requester else get_friendship_by_id(conn, int(request_id))
    if not row or row["status"] != STATUS_PENDING or row["requester_identity"] == me:
        return None
    return row if me in (row["identity_a"], row["identity_b"]) else None


def _defer(conn: Any, ctx: ActionContext, row: dict[str, Any], *, seconds: float = 3600) -> None:
    """Re-arm after a pause and inside the next preferred activity window."""
    from .scheduled_task_service import schedule_task
    moment = ctx.current_time() + timedelta(seconds=max(60, seconds))
    wait = seconds_until_awake(ctx.wanderer, moment)
    moment += timedelta(seconds=wait if wait is not None else 86400)
    schedule_task(conn, task_kind=TASK_KIND_FRIEND_REQUEST, run_at=moment,
                  payload={"wanderer_id": ctx.wanderer_id, "request_id": int(row["id"]),
                           "requester": row["requester_identity"], "deferred": True},
                  dedupe_key=friend_dedupe_key(ctx.wanderer_id, row["requester_identity"]), owner_role="system",
                  title=f"散修处理好友申请 · {ctx.display_name}", priority=TASK_PRIORITY,
                  max_attempts=TASK_MAX_ATTEMPTS, replace=True)


def _claim_request(conn: Any, row: dict[str, Any]) -> bool:
    """CAS + row lock: a model decision cannot apply to a withdrawn/replaced request."""
    changed = conn.execute(
        "UPDATE social_friendships SET request_message = request_message WHERE id = ? AND status = 'pending' "
        "AND requester_identity = ? AND created_at = ? AND request_message = ?",
        (int(row["id"]), row["requester_identity"], row["created_at"], row.get("request_message") or ""),
    )
    return changed.rowcount == 1


def _decision_memory(conn: Any, ctx: ActionContext, row: dict[str, Any], decision: str, reason: str,
                     *, calls: int = 0, source: str = "personality") -> None:
    peer = str(row["requester_identity"])
    detail = {"decision": decision, "reason": reason, "requester": peer,
              "request_created_at": row["created_at"], "source": source}
    log_wanderer_action(conn, ctx.wanderer_id, "social_friend", target_type=TARGET_REQUEST_IN,
                        target_id=str(row["id"]), ai_calls=calls,
                        outcome="skipped" if decision == "defer" else "ok", detail=detail)
    append_turn(conn, ctx.wanderer, f"social:friend:{peer}", who=ctx.display_name,
                text=f"收到好友申请，附言：{row.get('request_message') or '无'}。我的决定：{decision}。原因：{reason}",
                counterpart_identity=peer)


async def decide_friend_request(ctx: ActionContext, request_id: int, *, deferred: bool = False,
                                requester: str = "") -> dict[str, Any]:
    """One personality decision, committed together with its reason and relationship memory."""
    with ctx.connect() as conn:
        row = _pending_request(conn, request_id, ctx.identity, requester)
        if row and blocked_either(conn, ctx.identity, row["requester_identity"]):
            return {"decision": "gone"}
        context = friend_context(conn, ctx.wanderer, row["requester_identity"]) if row else None
    if row is None:
        return {"decision": "gone"}
    calls, source, seconds = 0, "personality", 3600.0
    if not is_awake_now(ctx.wanderer, ctx.current_time()):
        decision, reason, source = "defer", "现在不在我偏好的上线时段，留到下次上线再考虑。", "schedule"
        seconds = 60
    else:
        scene = {"kind": "审核好友申请", "title": "由你自己决定是否交这个朋友",
                 "body": str(row.get("request_message") or "无附言"), "friend_context": context_block(context or {}),
                 "task": "依据你的人格、喜好、对方资料和结交记忆决定接受、拒绝或稍后考虑。教师也可以拒绝。理由仅供你的记忆和后台记录，不对申请人公开。",
                 "output_contract": '只输出 JSON：{"decision":"accept|decline|defer","reason":"具体的个人原因"}。不得省略原因。'}
        result = await run_turn(ctx, scene=scene, thread_key=f"social:friend:{row['requester_identity']}",
                                suffix="friend_decision")
        parsed = extract_json_object(result.get("raw_text")) or {}
        decision = str(parsed.get("decision") or "").strip().lower()
        reason = str(parsed.get("reason") or "").strip()[:500]
        calls = int(result.get("ai_calls") or 0)
        if result.get("status") != "ok" or decision not in {"accept", "decline", "defer"} or not reason:
            decision, reason, source = "defer", "这次未能完成判断，保留申请稍后重新考虑。", "retry"
            seconds = 86400 if result.get("status") == "capped" else 1800
    events: list = []
    user = build_wanderer_user_dict(ctx.wanderer)
    try:
        with ctx.connect() as conn:
            from .wanderer_settings_service import is_wanderer_runtime_enabled
            fresh = load_wanderer(conn, ctx.wanderer_id)
            if (not is_wanderer_runtime_enabled(conn) or not fresh or fresh.get("status") != STATUS_ACTIVE
                    or not behavior_on(fresh, SOCIAL_BEHAVIOR)):
                return {"decision": "gone"}
            if not _claim_request(conn, row) or blocked_either(conn, ctx.identity, row["requester_identity"]):
                return {"decision": "gone"}
            ctx.wanderer = fresh
            if decision != "defer" and not is_awake_now(fresh, ctx.current_time()):
                decision, reason, source, seconds = "defer", "上线时段已改变，留到下次上线再考虑。", "schedule", 60
            if decision == "defer":
                _defer(conn, ctx, row, seconds=seconds)
            else:
                (accept_request if decision == "accept" else decline_request)(conn, user, int(row["id"]), events)
            _decision_memory(conn, ctx, row, decision, reason, calls=calls, source=source)
            conn.commit()
    except SocialError as exc:
        record_action(ctx, "social_friend", target_type=TARGET_REQUEST_IN, target_id=str(row["id"]),
                      outcome="failed", ai_calls=calls,
                      detail={"decision": decision, "reason": reason, "error": str(exc.detail)[:200]})
        return {"decision": "failed"}
    await social_push_service.push_events(events)
    return {"decision": decision, "reason": reason}


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
    summary: dict[str, Any] = {"presence": await sync_wanderer_presence(conn_factory, now=now), "friend_requests": 0,
                               "recovered_requests": recover_pending_requests(conn_factory, now=now)}
    for wanderer_id in wanderer_ids:
        ctx, _reason = social_context(wanderer_id, conn_factory=conn_factory, ai_caller=ai_caller, sleep=sleep, rng=rng)
        if ctx is None:
            continue
        ctx.now = now
        done = await maybe_befriend(ctx)
        summary["friend_requests"] += sum(1 for item in done if item["outcome"] == "ok")
    return summary


def recover_pending_requests(conn_factory: ConnFactory = None, *, now: Optional[datetime] = None) -> int:
    """Bounded recovery after an old missed hook, pause, disabled runtime or exhausted task retry."""
    from .wanderer_settings_service import is_wanderer_runtime_enabled
    with _connect(conn_factory) as conn:
        if not is_wanderer_runtime_enabled(conn):
            return 0
        rows = conn.execute(
            "SELECT f.id, f.requester_identity, w.student_id FROM social_friendships f "
            "JOIN wanderer_agents w ON (f.identity_a = 'student:' || CAST(w.student_id AS TEXT) "
            "OR f.identity_b = 'student:' || CAST(w.student_id AS TEXT)) "
            "WHERE f.status = 'pending' AND w.status = 'active' "
            "AND f.requester_identity <> 'student:' || CAST(w.student_id AS TEXT) "
            "AND NOT EXISTS (SELECT 1 FROM scheduled_tasks t WHERE t.dedupe_key = "
            "'wanderer:friend:' || CAST(w.id AS TEXT) || ':' || f.requester_identity "
            "AND t.status IN ('pending', 'running')) ORDER BY f.created_at LIMIT 40"
        ).fetchall()
        recovered = 0
        for row in rows:
            task = on_friend_request(conn, int(row["id"]), str(row["requester_identity"]),
                                     f"student:{int(row['student_id'])}", now=now, recovery=True)
            recovered += int(task is not None)
        conn.commit()
    return recovered


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
