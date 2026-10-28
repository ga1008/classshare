"""An optional, budgeted tick action: decide whether to start talking to a friend."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from .social_common import blocked_either, dm_key
from .social_identity_service import load_people
from .social_relations import friendship_rows, other_side
from .wanderer_actions_common import ActionContext, behavior_on, day_start_iso, outcome, record_action, remember, run_turn, turn_parts
from .wanderer_actions_social import _counted, _gate, chat_scene, load_window, post_parts
from .wanderer_social_triggers import SOCIAL_BEHAVIOR, is_awake_now

OUTREACH_DAILY_LIMIT = 2
OUTREACH_TARGET_TYPE = "social_outreach"
FRIEND_COOLDOWN_HOURS = 12


def outreach_candidates(conn: Any, ctx: ActionContext) -> list[str]:
    """No nags: one consideration per friend per half day, at most two initiations daily."""
    sent = conn.execute(
        "SELECT COUNT(*) AS n FROM wanderer_action_log WHERE wanderer_id = ? AND target_type = ? "
        "AND action_kind = 'social_message' AND created_at >= ?",
        (ctx.wanderer_id, OUTREACH_TARGET_TYPE, day_start_iso(ctx.current_time())),
    ).fetchone()
    if int(sent["n"] or 0) >= OUTREACH_DAILY_LIMIT:
        return []
    considered = {str(row["target_id"]) for row in conn.execute(
        "SELECT target_id FROM wanderer_action_log WHERE wanderer_id = ? AND target_type = ? AND created_at >= ?",
        (ctx.wanderer_id, OUTREACH_TARGET_TYPE,
         (ctx.current_time() - timedelta(hours=FRIEND_COOLDOWN_HOURS)).isoformat(timespec="seconds")),
    ).fetchall()}
    pairs = friendship_rows(conn, ctx.identity, statuses=("accepted",))
    identities = [other_side(row, ctx.identity) for row in pairs]
    people = load_people(conn, identities)
    return [peer for peer in identities if (people.get(peer) or {}).get("active")
            and dm_key(ctx.identity, peer) not in considered and not blocked_either(conn, ctx.identity, peer)]


async def start_friend_conversation(ctx: ActionContext) -> list[dict[str, Any]]:
    if ctx.capped or not behavior_on(ctx.wanderer, SOCIAL_BEHAVIOR) or not is_awake_now(ctx.wanderer, ctx.current_time()):
        return []
    with ctx.connect() as conn:
        candidates = outreach_candidates(conn, ctx)
        ctx.rng.shuffle(candidates)
        window = None
        quota = 0
        for peer in candidates[:12]:
            queued = conn.execute("SELECT 1 FROM wanderer_social_inbox WHERE wanderer_id=? AND source='social' "
                "AND conversation_key=? AND (latest_message_id>handled_message_id OR state='running') LIMIT 1",
                (ctx.wanderer_id, dm_key(ctx.identity, peer))).fetchone()
            if queued:
                continue
            candidate = load_window(conn, ctx.identity, dm_key(ctx.identity, peer))
            if candidate is None or candidate["new"]:
                continue  # incoming messages have their own reply task
            counted = [row for row in candidate["rows"] if _counted(row)]
            if counted and counted[-1]["sender_identity"] == ctx.identity:
                continue  # wait for the friend rather than repeatedly demanding attention
            reason, quota = _gate(conn, ctx.identity, candidate, require_new=False)
            if not reason:
                window = candidate
                break
    if window is None:
        return []
    scene = chat_scene(ctx, window, "multi", False)
    scene["kind"] = "想起一位好友"
    scene["meta"] = "目前没有需要你回复的新消息，你可以自行决定是否主动找这位好友说话。"
    scene["task"] = ("依据你的人格、关系记忆、完整近期对话和对方当前可见资料，自选话题和表达。"
                     "分享想法、见闻或邀请都可以；没有想说的就 skip，不必硬找话题，不催促、不刷屏。")
    key = window["key"]
    result = await run_turn(ctx, scene=scene, thread_key=f"social:{key}", suffix="friend_outreach")
    turn = result.get("turn") or {}
    calls = int(result.get("ai_calls") or 0)
    parts = turn_parts(ctx, turn)[:quota] if result.get("status") == "ok" else []
    if not parts or turn.get("action") == "skip":
        record_action(ctx, "skip", target_type=OUTREACH_TARGET_TYPE, target_id=key, ai_calls=calls,
                      outcome="skipped", detail={"reason": "chose_silence", "status": result.get("status")})
        await remember(ctx, f"social:{key}")
        return [outcome("skip", "skipped", ai_calls=calls)]
    anchor = max((int(row["id"]) for row in window["rows"]), default=0)
    window["nonce_prefix"] = f"wanderer-outreach:{ctx.wanderer_id}:{anchor}:{ctx.current_time().date().isoformat()}"
    sent, error = await post_parts(ctx, window, parts)
    status = "ok" if sent == len(parts) else "failed"
    record_action(ctx, "social_message", target_type=OUTREACH_TARGET_TYPE, target_id=key, ai_calls=calls,
                  content_preview=" / ".join(parts[:sent]), outcome=status,
                  detail={"initiated": True, "parts": sent, "error": error, "decision": turn.get("action")})
    if sent:
        await remember(ctx, f"social:{key}", said=parts[:sent], counterpart_identity=window["parsed"]["peer"])
    return [outcome("social_message", status, ai_calls=calls, parts=sent)]
