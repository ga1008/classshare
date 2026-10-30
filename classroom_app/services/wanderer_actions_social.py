"""散修聊天：好友私聊回复与群聊观察 (docs/social-friends-groups-2026-10-27.md §10).

Group handlers and shared scene helpers. Private replies run in ``wanderer_inbox_worker``.
Old persisted DM tasks only hand off to that inbox.

Scheduler-side group handlers registered by ``wanderer_social_triggers``:

* ``wanderer_social_reply`` — one wanderer, one conversation. Reads the last
  30 messages + the memory thread ``social:<conversation_key>``, marks them read
  (the peer sees the read receipt), then one ``generate_turn`` decides
  skip / emoji / reply / multi. Parts go out as separate messages, each after a
  ``typing`` push and a pause of ``len(part) × 0.12 s`` (1–8 s). Occasionally
  nudges back (拍一拍).
* ``wanderer_group_watch`` — one group, debounced. Every wanderer member looks at
  what is new since its own read cursor and decides on its own (being @-mentioned
  → always considered; topic interest; talkativeness). Chosen ones get a
  ``wanderer_social_reply`` task, staggered 5–40 s apart; the others just read.

Cost and loop guards: the daily AI cap (capped → emoji in DMs, silence in
groups); ≤ 6 wanderer messages per conversation per 10 min; ≤ 20 messages per
wanderer per group per day; never answers its own messages; a wanderer message
is answered by another wanderer only with probability 0.25 and never after two
wanderer-only turns in a row. Sends go through ``social_message_service`` with
the wanderer's student-shaped user, so friendship, block, membership and rate
rules apply exactly as for a person. No connection is held across a model call
or a pause.
"""
from __future__ import annotations

import asyncio
import random
from datetime import datetime
from typing import Any, Callable, Optional

from . import social_push_service
from .rate_limit_service import RateLimitExceededError
from .social_common import SocialError, as_int, blocked_either, iso_before, loads_list, utc_day_start_iso, serialize_on
from .social_conversation_service import dm_read_cursor, load_group, load_membership, participants, require_access
from .social_identity_service import load_people
from .social_message_service import mark_read, nudge, send_message
from .social_relations import are_friends, group_member_identities
from .wanderer_actions_blog import policy_roll
from .wanderer_actions_common import (
    ActionContext,
    behavior_on,
    compact_memory,
    emoji_for,
    interest_score,
    outcome,
    record_action,
    run_turn,
    turn_parts,
)
from .wanderer_identity_service import STATUS_ACTIVE, build_wanderer_user_dict, load_wanderer
from .wanderer_prompt_service import SCENE_GROUP_CHAT, SCENE_SOCIAL_CHAT
from .wanderer_memory_service import HISTORY_DB_LIMIT, append_turn, invalidate_thread_cache, load_thread
from .wanderer_social_context import context_block, conversation_text, friend_context
from .wanderer_settings_service import daily_ai_calls, daily_cap, is_wanderer_runtime_enabled
from .wanderer_social_triggers import (
    SOCIAL_BEHAVIOR,
    WAKE_JITTER_SECONDS,
    is_awake_now,
    schedule_social_reply,
    seconds_until_awake,
    sociability,
    social_wanderers,
)

CONTEXT_LIMIT = 30
ROSTER_LIMIT = 30
RATE_WINDOW_SECONDS = 600
RATE_LIMIT = 6
GROUP_DAILY_CAP = 20
TYPING_SECONDS_PER_CHAR = 0.12
TYPING_BOUNDS = (1.0, 8.0)
WANDERER_REACT_PROB = 0.25
# 直接对话（私聊 / 被 @）不设会话与群日限额，条数上限只由输出合同约束。
DIRECT_REPLY_QUOTA = 5
WANDERER_STREAK_LIMIT = 2
NUDGE_BACK_PROB = 0.04
NUDGED_BACK_PROB = 0.5
STAGGER_SECONDS = (5, 40)
COUNTED_TYPES = ("text", "media", "nudge")
MENTION_ALL = "all"

ConnFactory = Optional[Callable[[], Any]]


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------

def _connect(conn_factory: ConnFactory) -> Any:
    if conn_factory is not None:
        return conn_factory()
    from ..database import get_db_connection

    return get_db_connection()


def social_context(wanderer_id: Any, *, conn_factory: ConnFactory = None, ai_caller: Any = None,
                   sleep: Any = None, rng: Optional[random.Random] = None) -> tuple[Optional[ActionContext], str]:
    """A ready ActionContext, or ``(None, reason)`` when the system/wanderer/behavior is off."""
    with _connect(conn_factory) as conn:
        if not is_wanderer_runtime_enabled(conn):
            return None, "disabled"
        wanderer = load_wanderer(conn, as_int(wanderer_id))
        if wanderer is None or str(wanderer.get("status") or "") != STATUS_ACTIVE:
            return None, "inactive"
        if not behavior_on(wanderer, SOCIAL_BEHAVIOR):
            return None, "social_off"
        capped = daily_ai_calls(conn) >= daily_cap(conn)
    return ActionContext(conn_factory=conn_factory, wanderer=wanderer, ai_caller=ai_caller,
                         sleep=sleep or asyncio.sleep, rng=rng or random.Random(), capped=capped), ""


def typing_seconds(text: Any) -> float:
    low, high = TYPING_BOUNDS
    return round(max(low, min(high, len(str(text or "")) * TYPING_SECONDS_PER_CHAR)), 2)


# ---------------------------------------------------------------------------
# Reading a conversation
# ---------------------------------------------------------------------------

def _recent_rows(conn: Any, key: str) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM social_messages WHERE conversation_key = ? ORDER BY id DESC LIMIT ?",
                        (key, CONTEXT_LIMIT)).fetchall()
    return [dict(row) for row in reversed(rows)]


def _counted(row: dict[str, Any]) -> bool:
    return str(row.get("message_type") or "") in COUNTED_TYPES and not row.get("recalled_at")


def wanderer_streak(rows: list[dict[str, Any]], wanderer_identities: set[str]) -> int:
    """How many of the latest counted messages in a row were written by 散修."""
    streak = 0
    for row in reversed(rows):
        if not _counted(row):
            continue
        if str(row["sender_identity"]) not in wanderer_identities:
            break
        streak += 1
    return streak


def is_mentioned(row: dict[str, Any], identity: str) -> bool:
    mentions = [str(item) for item in loads_list(row.get("mentions_json"))]
    return identity in mentions or (row.get("message_type") != "nudge" and MENTION_ALL in mentions)


def _sent_since(conn: Any, identity: str, key: str, since: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM social_messages WHERE sender_identity = ? AND conversation_key = ? "
        "AND created_at >= ? AND message_type IN (?, ?, ?)",
        (identity, key, since, *COUNTED_TYPES),
    ).fetchone()
    return as_int(row["n"]) if row else 0


def _read_cursor(conn: Any, parsed: dict[str, Any], identity: str) -> int:
    if parsed["type"] == "dm":
        return dm_read_cursor(conn, parsed["key"], identity)
    return as_int(parsed["membership"].get("last_read_message_id"))


def _new_rows(rows: list[dict[str, Any]], cursor: int, identity: str) -> list[dict[str, Any]]:
    return [row for row in rows if int(row["id"]) > cursor and row["sender_identity"] != identity and _counted(row)]


def load_window(conn: Any, identity: str, key: str) -> Optional[dict[str, Any]]:
    """Everything one decision needs, read in one short transaction; None without access."""
    try:
        parsed = require_access(conn, identity, key)
    except SocialError:
        return None
    rows = _recent_rows(conn, parsed["key"])
    if parsed["type"] == "group":
        joined_at = str(parsed["membership"].get("joined_at") or "")
        rows = [row for row in rows if str(row.get("created_at") or "") >= joined_at]
    audience = participants(conn, parsed)
    speakers = {str(row["sender_identity"]) for row in rows}
    people = load_people(conn, speakers | set(audience[:ROSTER_LIMIT]) | {identity})
    wanderer_ids = {item for item, person in people.items() if person.get("is_wanderer")}
    return {
        "parsed": parsed, "key": parsed["key"], "type": parsed["type"], "rows": rows, "people": people,
        "audience": audience, "new": _new_rows(rows, _read_cursor(conn, parsed, identity), identity),
        "wanderer_ids": wanderer_ids, "streak": wanderer_streak(rows, wanderer_ids),
        "title": str((parsed.get("group") or {}).get("name") or "") if parsed["type"] == "group"
        else str((people.get(parsed["peer"]) or {}).get("name") or "好友"),
    }


def _gate(conn: Any, identity: str, window: dict[str, Any], *, require_new: bool = True,
          check_streak: bool = True, direct: bool = False) -> tuple[str, int]:
    """(skip reason or '', how many messages may still be sent).

    ``direct`` marks a message addressed to the wanderer (a DM or an @-mention).
    Those bypass the per-conversation rate limit and the per-group daily cap:
    被问/被@ 不计入互动限额 (docs/wanderer-reply-priority-2026-10-28.md). The AI
    daily cap and the AI-to-AI loop guard still apply.
    """
    parsed, key = window["parsed"], window["key"]
    if parsed["type"] == "dm":
        if blocked_either(conn, identity, parsed["peer"]) or not are_friends(conn, identity, parsed["peer"]):
            return "not_friends", 0
    elif parsed["group"].get("dissolved_at"):
        return "dissolved", 0
    if require_new and not window["new"]:
        return "nothing_new", 0
    if check_streak and window["streak"] >= WANDERER_STREAK_LIMIT:
        return "wanderer_loop", 0
    if direct:
        return "", DIRECT_REPLY_QUOTA
    quota = RATE_LIMIT - _sent_since(conn, identity, key, iso_before(seconds=RATE_WINDOW_SECONDS))
    if parsed["type"] == "group":
        quota = min(quota, GROUP_DAILY_CAP - _sent_since(conn, identity, key, utc_day_start_iso()))
        if quota <= 0:
            return "group_daily_cap", 0
    return ("rate_limited", 0) if quota <= 0 else ("", quota)


# ---------------------------------------------------------------------------
# Scene
# ---------------------------------------------------------------------------

def _tier_name(person: dict[str, Any]) -> str:
    return str((person.get("tier_badge") or {}).get("name") or "")


def _speaker(people: dict[str, Any], identity: str, me: str) -> str:
    if identity == me:
        return "你"
    person = people.get(identity) or {}
    name = str(person.get("name") or "某人")
    if person.get("role") == "teacher":
        return f"{name}（老师）"
    return f"{name}（散修）" if person.get("is_wanderer") else name


def _message_text(row: dict[str, Any]) -> str:
    if row.get("recalled_at"):
        return "（撤回了一条消息）"
    body = str(row.get("body") or "").strip()
    if row.get("message_type") == "media" and not body:
        return "[发了一个文件/图片]"
    return body or "[空消息]"


def _roster(window: dict[str, Any], me: str) -> list[dict[str, Any]]:
    wanted = [me, *[row["sender_identity"] for row in reversed(window["rows"])], *window["audience"]]
    roster = []
    for identity in dict.fromkeys(wanted):
        person = window["people"].get(identity)
        if person is None:
            continue
        roster.append({"name": person.get("name") or "", "is_self": identity == me, "role": person.get("role"),
                       "is_wanderer": bool(person.get("is_wanderer")), "tier_name": _tier_name(person)})
        if len(roster) >= ROSTER_LIMIT:
            break
    return roster


def _counterpart(window: dict[str, Any]) -> Optional[dict[str, Any]]:
    if window["type"] != "dm":
        return None
    person = window["people"].get(window["parsed"]["peer"]) or {}
    if person.get("is_wanderer"):
        return {"name": person.get("name") or "对方", "is_wanderer": True, "tier_name": _tier_name(person)}
    label = "老师" if person.get("role") == "teacher" else "真实学生"
    return {"name": person.get("name") or "对方", "is_wanderer": False, "role_label": label}


def chat_scene(ctx: ActionContext, window: dict[str, Any], mode: str, mentioned: bool) -> dict[str, Any]:
    group = window["type"] == "group"
    more = "一句能说清就用 reply；确有不同内容再用 multi，不为凑条数连续发送。"
    if group and mentioned:
        task = ("群里有新消息，有人 @ 了你，认真想想要不要回。要回就按自己的方式把话说完，"
                "不必为了简短而省略；也可以只回表情（emoji）。" + more)
    elif group:
        task = ("群里有新消息，没人点你，不想插话就 skip。要说就简短自然，可以只回表情（emoji）。" + more)
    else:
        task = "好友给你发来了消息。按你的性格决定回不回、怎么回：skip、只回表情（emoji）或回话。" + more
    context = ""
    if not group:
        with ctx.connect() as conn:
            context = context_block(friend_context(conn, ctx.wanderer, window["parsed"]["peer"]))
    return {
        "scene_type": SCENE_GROUP_CHAT if group else SCENE_SOCIAL_CHAT,
        "kind": "群聊" if group else "好友私聊",
        "title": f"群「{window['title']}」" if group else f"与 {window['title']} 的私聊",
        "meta": f"{len(window['new'])} 条新消息",
        "conversation_text": conversation_text(window["rows"], window["people"], ctx.identity),
        "friend_context": context,
        "tags": list(ctx.wanderer.get("interests") or []),
        "counterpart": _counterpart(window),
        "participants": _roster(window, ctx.identity),
        "_learning_message_ids": [int(row["id"]) for row in window["rows"]],
        "_support_messages": [{"id": row["id"], "body": row.get("body") or "", "sender_identity": row["sender_identity"],
                               "is_wanderer": row["sender_identity"] in window["wanderer_ids"],
                               "support_already_handled": int(row["id"]) <= int(window.get("support_handled_message_id") or 0)}
                              for row in window["new"] if not row.get("recalled_at")],
        "_support_user_identity": str(window["new"][-1]["sender_identity"]) if window["new"] else "",
        "task": task,
    }


# ---------------------------------------------------------------------------
# Acting
# ---------------------------------------------------------------------------

async def _mark_read(ctx: ActionContext, window: dict[str, Any]) -> None:
    if not window["rows"]:
        return
    events: list = []
    with ctx.connect() as conn:
        mark_read(conn, build_wanderer_user_dict(ctx.wanderer), window["key"], window["rows"][-1]["id"], events)
        conn.commit()
    await social_push_service.push_events(events)


async def _compose(ctx: ActionContext, window: dict[str, Any], mode: str,
                   mentioned: bool) -> tuple[list[str], int, str, str, dict[str, Any]]:
    """(parts, ai_calls, mood, action, turn). Emoji mode costs no AI call; when capped DMs get an emoji."""
    if window.get("_support_fallback"):
        turn = window["_support_fallback"]["turn"]
        ctx.support_threads.add(f"social:{window['key']}")
        return list(turn["parts"]), 0, str(turn.get("mood") or ""), str(turn["action"]), turn
    if mode == "emoji" or (ctx.capped and window["type"] == "dm"):
        return [emoji_for(ctx)], 0, "", "emoji", {}
    if ctx.capped:
        return [], 0, "", "capped", {}
    scene = chat_scene(ctx, window, mode, mentioned)
    suffix = SCENE_GROUP_CHAT if window["type"] == "group" else SCENE_SOCIAL_CHAT
    passive = window["type"] == "dm" or mentioned
    result = await run_turn(ctx, scene=scene, thread_key=f"social:{window['key']}", suffix=suffix, passive=passive)
    turn = result.get("turn") or {}
    action = str(turn.get("action") or "skip")
    if result.get("status") == "capped":
        return ([emoji_for(ctx)] if window["type"] == "dm" else []), 0, "", "capped", {}
    parts = turn_parts(ctx, turn, limit=not passive)
    return ([] if action == "skip" else parts), int(result.get("ai_calls") or 0), str(turn.get("mood") or ""), action, turn


async def post_parts(ctx: ActionContext, window: dict[str, Any], parts: list[str], *,
                      direct: bool = False) -> tuple[int, str]:
    """Send each part as its own message after a typing push and a typing-length pause."""
    user = build_wanderer_user_dict(ctx.wanderer)
    key = window["key"]
    audience = [item for item in window["audience"] if item != ctx.identity]
    anchor = int(window["new"][-1]["id"]) if window["new"] else 0
    for index, part in enumerate(parts):
        await social_push_service.push(audience, {"type": "typing", "conversation_key": key, "identity": ctx.identity})
        await ctx.sleep(typing_seconds(part))
        events: list = []
        nonce = window.get("nonce_prefix") or f"wanderer:{ctx.wanderer_id}:{anchor}"
        payload = {"body": part, "body_format": "text", "client_nonce": f"{nonce}:{index}"}
        try:
            with ctx.connect() as conn:
                fresh = load_wanderer(conn, ctx.wanderer_id)
                if (not is_wanderer_runtime_enabled(conn) or not fresh or fresh.get("status") != STATUS_ACTIVE
                        or not behavior_on(fresh, SOCIAL_BEHAVIOR)):
                    return index, "inactive"
                current = load_window(conn, ctx.identity, key)
                own_part = bool(index > 0 and current and current["rows"]
                                and current["rows"][-1]["sender_identity"] == ctx.identity)
                reason, _quota = (_gate(conn, ctx.identity, current, require_new=False,
                                        check_streak=not own_part, direct=direct)
                                   if current else ("no_access", 0))
                if (window.get("nonce_prefix") and index == 0 and current
                        and [row["id"] for row in current["rows"]] != [row["id"] for row in window["rows"]]):
                    return index, "conversation_changed"
                if reason:
                    return index, reason
                send_message(conn, user, key, payload, events)
                conn.commit()
        except (SocialError, RateLimitExceededError) as exc:
            return index, f"{type(exc).__name__}: {exc}"[:200]
        await social_push_service.push_events(events)
    return len(parts), ""


async def _maybe_nudge(ctx: ActionContext, window: dict[str, Any]) -> bool:
    if window["type"] != "dm":
        return False
    nudged_me = any(row.get("message_type") == "nudge" for row in window["new"])
    chance = NUDGED_BACK_PROB * sociability(ctx.wanderer) if nudged_me else NUDGE_BACK_PROB
    if ctx.rng.random() >= chance:
        return False
    events: list = []
    try:
        with ctx.connect() as conn:
            nudge(conn, build_wanderer_user_dict(ctx.wanderer), window["key"], None, events)
            conn.commit()
    except (SocialError, RateLimitExceededError):
        return False
    await social_push_service.push_events(events)
    return True


async def _remember_exchange(ctx: ActionContext, window: dict[str, Any], said: tuple[str, ...] | list[str] = ()) -> None:
    """Store each newly heard message intact instead of clipping a combined transcript to 4k."""
    peer = window["parsed"].get("peer") or ""
    key = f"social:{window['key']}"
    entries = [(_speaker(window["people"], row["sender_identity"], ctx.identity), _message_text(row))
               for row in window["new"]] + [(ctx.display_name, text) for text in said]
    with ctx.connect() as conn:
        invalidate_thread_cache(ctx.wanderer_id, key)
        thread = load_thread(conn, ctx.wanderer_id, key)
    # At most one optional compaction per exchange, before evicting older work memory.
    # A failed/capped summary leaves the original mirror intact and never loops on a burst.
    if key not in ctx.support_threads and thread["history"] and len(thread["history"]) + len(entries) >= HISTORY_DB_LIMIT - 6:
        await compact_memory(ctx, key)
    with ctx.connect() as conn:
        for speaker, text in entries:
            append_turn(conn, ctx.wanderer, key, who=speaker, text=text, counterpart_identity=peer)
        from .wanderer_actions_common import commit_learning
        commit_learning(ctx, conn, key)
        conn.commit()
    ctx.support_threads.discard(key)


def _target(window: dict[str, Any]) -> dict[str, Any]:
    return {"target_type": "social_group" if window["type"] == "group" else "social_dm", "target_id": window["key"]}


async def _skip(ctx: ActionContext, window: Optional[dict[str, Any]], reason: str, *, ai_calls: int = 0,
                mood: str = "", heard: bool = True) -> list[dict[str, Any]]:
    target = _target(window) if window else {"target_type": "social_dm", "target_id": ""}
    record_action(ctx, "skip", **target, ai_calls=ai_calls, mood=mood, outcome="skipped", detail={"reason": reason})
    if window and heard and window["new"]:
        await _remember_exchange(ctx, window)
    return [outcome("skip", "skipped", ai_calls=ai_calls, reason=reason)]


def _pick_mode(ctx: ActionContext, window: dict[str, Any], mentioned: bool) -> str:
    """'' to stay silent (another 散修 spoke and the roll said no), else a policy mode."""
    latest = window["new"][-1]
    if (latest["sender_identity"] in window["wanderer_ids"] and window["type"] == "dm" and not mentioned
            and ctx.rng.random() >= WANDERER_REACT_PROB):
        return ""
    mode = policy_roll(ctx)
    return "reply" if mentioned and mode == "ignore" else mode


async def reply_in_conversation(ctx: ActionContext, key: str, *, mentioned: bool = False) -> list[dict[str, Any]]:
    """Read what is new in ``key`` and (maybe) answer it. Returns the action outcomes."""
    with ctx.connect() as conn:
        window = load_window(conn, ctx.identity, key)
        if window:
            mentioned = mentioned or any(is_mentioned(row, ctx.identity) for row in window["new"])
            direct = window["type"] == "dm" or mentioned
            reason, quota = _gate(conn, ctx.identity, window, direct=direct)
        else:
            direct, reason, quota = False, "no_access", 0
    if reason == "nothing_new":
        return []
    if reason:
        if window:
            await _mark_read(ctx, window)
        return await _skip(ctx, window, reason)
    from .wanderer_student_support import fallback_for_scene
    support = fallback_for_scene(ctx.wanderer, chat_scene(ctx, window, "reply", mentioned), f"social:{key}")
    if support:
        window["_support_fallback"] = support
    mode = "reply" if support else _pick_mode(ctx, window, mentioned)
    await _mark_read(ctx, window)
    if mode in {"", "ignore"}:
        return await _skip(ctx, window, "wanderer_pause" if not mode else "ignore_policy")
    parts, calls, mood, action, turn = await _compose(ctx, window, mode, mentioned)
    parts = parts[:quota]
    if not parts:
        return await _skip(ctx, window, f"turn:{action}", ai_calls=calls, mood=mood)
    sent, error = await post_parts(ctx, window, parts, direct=direct)
    nudged = await _maybe_nudge(ctx, window) if sent else False
    status = "ok" if sent == len(parts) else "failed"
    record_action(ctx, "social_message", **_target(window), content_preview=" / ".join(parts[:sent] or parts),
                  ai_calls=calls, mood=mood, outcome=status,
                  detail={"parts": sent, "mode": mode, "turn": action, "nudged": nudged, "error": error})
    if window.get("new"):
        from .wanderer_life_service import award_interaction_cultivation
        from .wanderer_relationship_service import apply_turn_feeling

        peer = str(window["new"][-1]["sender_identity"])
        scene = "social_group" if window["type"] == "group" else "social_dm"
        with ctx.connect() as conn:
            apply_turn_feeling(conn, ctx.wanderer, peer, turn, scene=scene)
            if sent:
                award_interaction_cultivation(conn, ctx.wanderer, ["social_message"])
            conn.commit()
    await _remember_exchange(ctx, window, said=parts[:sent])
    return [outcome("social_message", status, ai_calls=calls, parts=sent)]


# ---------------------------------------------------------------------------
# Group watch
# ---------------------------------------------------------------------------

def group_reply_probability(wanderer: dict[str, Any], *, interest: float, mentioned: bool, streak: int,
                            last_is_wanderer: bool, sent_today: int) -> float:
    """Chance that a wanderer joins in now (pure; no AI)."""
    if streak >= WANDERER_STREAK_LIMIT:
        return 0.0
    if mentioned:
        return 1.0  # @ 是直接对话，不受群日上限约束；AI 循环保护仍在上一条
    if sent_today >= GROUP_DAILY_CAP:
        return 0.0
    chance = 0.05 + 0.3 * sociability(wanderer) + min(0.3, 0.1 * float(interest))
    if last_is_wanderer:
        chance *= WANDERER_REACT_PROB
    return round(min(0.85, chance), 3)


def _mark_group_read(conn: Any, group_id: int, identity: str, last_id: int) -> None:
    conn.execute(
        "UPDATE social_group_members SET last_read_message_id = ? WHERE group_id = ? AND member_identity = ? "
        "AND last_read_message_id < ?",
        (int(last_id), int(group_id), identity, int(last_id)),
    )


def _consider(conn: Any, wanderer: dict[str, Any], identity: str, group_id: int, rows: list[dict[str, Any]],
              wanderer_ids: set[str], streak: int) -> Optional[dict[str, Any]]:
    membership = load_membership(conn, group_id, identity) or {}
    rows = [row for row in rows if str(row.get("created_at") or "") >= str(membership.get("joined_at") or "")]
    new = _new_rows(rows, as_int(membership.get("last_read_message_id")), identity)
    if not new:
        return None
    key = f"group:{int(group_id)}"
    from .wanderer_support_service import assess_support
    support = assess_support([{"id": row["id"], "body": row.get("body") or "", "sender_identity": row["sender_identity"],
                               "is_wanderer": row["sender_identity"] in wanderer_ids}
                              for row in new if not row.get("recalled_at")])
    urgent = support.get("urgency") == "immediate"
    mentioned = any(is_mentioned(row, identity) for row in new)
    interest = interest_score(wanderer.get("interests") or [], *(row.get("body") for row in new))
    chance = group_reply_probability(
        wanderer, interest=interest, mentioned=mentioned, streak=streak,
        last_is_wanderer=new[-1]["sender_identity"] in wanderer_ids,
        sent_today=_sent_since(conn, identity, key, utc_day_start_iso()),
    )
    return {"identity": identity, "mentioned": mentioned, "chance": chance, "last_id": int(rows[-1]["id"]), "support_urgent": urgent}


async def watch_group(group_id: int, *, conn_factory: ConnFactory = None, rng: Optional[random.Random] = None,
                      now: Optional[datetime] = None) -> dict[str, Any]:
    """Each wanderer member decides independently; chosen ones are scheduled with a stagger."""
    rng = rng or random.Random()
    moment = now or datetime.now()
    scheduled: list[dict[str, Any]] = []
    passed: list[str] = []
    with _connect(conn_factory) as conn:
        if not is_wanderer_runtime_enabled(conn):
            return {"status": "disabled", "scheduled": [], "passed": []}
        serialize_on(conn, "wanderer-group-support", [str(group_id)])
        group = load_group(conn, int(group_id))
        if not group or group.get("dissolved_at"):
            return {"status": "gone", "scheduled": [], "passed": []}
        key = f"group:{int(group_id)}"
        wanderers = social_wanderers(conn, group_member_identities(conn, int(group_id)))
        rows = _recent_rows(conn, key)
        people = load_people(conn, {str(row["sender_identity"]) for row in rows})
        wanderer_ids = {item for item, person in people.items() if person.get("is_wanderer")} | set(wanderers)
        streak = wanderer_streak(rows, wanderer_ids)
        order = list(wanderers.items())
        rng.shuffle(order)
        offset = 0
        support_taken = False
        for identity, wanderer in order:
            seen = _consider(conn, wanderer, identity, int(group_id), rows, wanderer_ids, streak)
            if seen is None:
                continue
            delay: Optional[float] = None
            if seen.get("support_urgent"):
                if not support_taken:
                    delay, support_taken = 0.0, True
            elif rng.random() < seen["chance"]:
                offset += rng.randint(*STAGGER_SECONDS)
                if seen["mentioned"]:
                    # 被 @ 等于有人点名问你：不看作息，错峰几秒就回。
                    delay = float(offset)
                elif is_awake_now(wanderer, moment):
                    delay = float(offset)
            if delay is None:
                _mark_group_read(conn, int(group_id), identity, seen["last_id"])  # read, chose silence
                passed.append(identity)
                continue
            schedule_social_reply(conn, wanderer, key, delay_seconds=delay, mentioned=seen["mentioned"], now=moment)
            scheduled.append({"identity": identity, "delay": round(delay, 1), "mentioned": seen["mentioned"]})
        conn.commit()
    return {"status": "ok", "scheduled": scheduled, "passed": passed}


# ---------------------------------------------------------------------------
# Scheduler handlers
# ---------------------------------------------------------------------------

async def handle_wanderer_social_reply(task: dict[str, Any], *, ai_caller: Any = None, sleep: Any = None,
                                       rng: Optional[random.Random] = None, conn_factory: ConnFactory = None) -> str:
    payload = task.get("payload") or {}
    key = str(payload.get("conversation_key") or "")
    if not key:
        return "skipped: missing conversation"
    if key.startswith("dm:"):
        # Compatibility with tasks persisted before the durable inbox release.
        # This handler only hands off; the dedicated lane owns model work.
        from .wanderer_inbox_service import enqueue_social
        from .social_common import dm_peer, parse_conversation_key
        with _connect(conn_factory) as conn:
            wanderer = load_wanderer(conn, as_int(payload.get("wanderer_id")))
            if wanderer:
                peer = dm_peer(parse_conversation_key(key), f"student:{wanderer['student_id']}")
                enqueue_social(conn, wanderer, key, peer)
                conn.commit()
        return "queued: durable dm inbox"
    ctx, reason = social_context(payload.get("wanderer_id"), conn_factory=conn_factory, ai_caller=ai_caller,
                                 sleep=sleep, rng=rng)
    if ctx is None:
        return f"skipped: {reason}"
    done = await reply_in_conversation(ctx, key, mentioned=bool(payload.get("mentioned")))
    if not done:
        return "skipped: nothing new"
    item = done[-1]
    return f"social reply: {item['kind']} {item['outcome']} parts={item.get('parts', 0)} ai_calls={ctx.ai_calls}"


async def handle_wanderer_group_watch(task: dict[str, Any], *, conn_factory: ConnFactory = None,
                                      rng: Optional[random.Random] = None, now: Optional[datetime] = None) -> str:
    payload = task.get("payload") or {}
    group_id = as_int(payload.get("group_id"))
    if group_id <= 0:
        return "skipped: missing group"
    summary = await watch_group(group_id, conn_factory=conn_factory, rng=rng, now=now)
    return f"group watch: {summary['status']} scheduled={len(summary['scheduled'])} passed={len(summary['passed'])}"
