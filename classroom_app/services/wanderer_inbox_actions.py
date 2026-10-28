"""Conversation snapshots and fenced delivery for the durable wanderer DM inbox."""
from __future__ import annotations

import json
from typing import Any

from . import wanderer_inbox_service as inbox
from .social_common import SocialError, now_iso, serialize_on
from .wanderer_actions_common import ActionContext, behavior_on, run_turn, turn_parts
from .wanderer_identity_service import STATUS_ACTIVE, build_wanderer_user_dict, load_wanderer, log_wanderer_action
from .wanderer_memory_service import append_turn
from .wanderer_settings_service import is_wanderer_runtime_enabled

WINDOW_CHARS = 28_000
WINDOW_ROWS = 30


def context(task: dict, *, factory=None, ai_caller=None, sleep=None) -> ActionContext | None:
    with inbox.connection(factory) as conn:
        wanderer = load_wanderer(conn, task["wanderer_id"])
        flag = "social" if task["source"] == "social" else "dm_reply"
        if (not is_wanderer_runtime_enabled(conn) or not wanderer or wanderer["status"] != STATUS_ACTIVE
                or not behavior_on(wanderer, flag)):
            return None
    kwargs = {"sleep": sleep} if sleep else {}
    return ActionContext(factory, wanderer, ai_caller=ai_caller, **kwargs)


def _source_rows(conn: Any, task: dict, *, start: int, end: int, backwards=False, limit=WINDOW_ROWS) -> list[dict]:
    table = "social_messages" if task["source"] == "social" else "private_messages"
    order = "DESC" if backwards else "ASC"
    counted = " AND message_type IN ('text','media','nudge')" if task["source"] == "social" else ""
    rows = [dict(row) for row in conn.execute(
        f"SELECT * FROM {table} WHERE conversation_key=? AND id>? AND id<=?{counted} ORDER BY id {order} LIMIT ?",
        (task["conversation_key"], start, end, limit)).fetchall()]
    if task["source"] == "legacy":
        for row in rows:
            row.update(body=row.get("content") or "[附件消息]", message_type="text", recalled_at=None)
    return list(reversed(rows)) if backwards else rows


def _permission(conn: Any, ctx: ActionContext, task: dict) -> None:
    if task["source"] == "social":
        from .social_message_service import _require_can_send
        from .social_conversation_service import require_access
        _require_can_send(conn, ctx.identity, require_access(conn, ctx.identity, task["conversation_key"]))
    else:
        from .message_center_service import _is_blocked, _resolve_contact
        peer = task["peer_identity"]
        person = _resolve_contact(conn, user=build_wanderer_user_dict(ctx.wanderer), contact_identity=peer,
                                  class_offering_id=task.get("class_offering_id"))
        if (not person or not person.get("can_send") or person.get("is_blocked")
                or _is_blocked(conn, peer, ctx.identity)):
            raise SocialError("private contact unavailable", 403)


def window_for(conn: Any, ctx: ActionContext, task: dict, *, snapshot=None) -> dict:
    """Oldest unhandled complete messages first; no LIMIT/OFFSET cursor jumps."""
    _permission(conn, ctx, task)
    pending = _source_rows(conn, task, start=int(task["handled_message_id"]),
                           end=int(snapshot or task["latest_message_id"]))
    chosen, chars = [], 0
    for row in pending:
        size = len(str(row.get("body") or "")) + 100
        if chosen and chars + size > WINDOW_CHARS:
            break
        chosen.append(row)
        chars += size
    anchor = int(chosen[-1]["id"]) if chosen else int(task["handled_message_id"])
    prefix = _source_rows(conn, task, start=0, end=int(task["handled_message_id"]), backwards=True, limit=10)
    from .social_identity_service import load_people
    from .social_conversation_service import require_access
    people = load_people(conn, [ctx.identity, task["peer_identity"]])
    parsed = (require_access(conn, ctx.identity, task["conversation_key"]) if task["source"] == "social"
              else {"key": task["conversation_key"], "type": "dm", "peer": task["peer_identity"]})
    return {"key": task["conversation_key"], "type": "dm", "parsed": parsed, "people": people,
            "rows": prefix + chosen,
            "new": [row for row in chosen if row["sender_identity"] != ctx.identity and not row.get("recalled_at")],
            "audience": [ctx.identity, task["peer_identity"]],
            "wanderer_ids": {key for key, value in people.items() if value.get("is_wanderer")},
            "title": str((people.get(task["peer_identity"]) or {}).get("name") or "好友"), "anchor": anchor}


async def compose(ctx: ActionContext, task: dict, window: dict) -> dict:
    from .wanderer_actions_social import chat_scene
    scene = chat_scene(ctx, window, "multi", False)
    scene["_learning_conversation_key"] = task["conversation_key"]
    scene["_learning_class_offering_id"] = task.get("class_offering_id")
    scene["meta"] = f"{len(window['new'])} 条待你考虑的来信；近期原文窗口有界，更早交流见长期记忆。"
    scene["task"] = "请根据完整语境、人格和你对这段关系的感受自行决定回应或沉默，不必讨好对方。"
    scene["relationship_emotion"] = {"mood": task.get("mood") or "", "reason": task.get("decision_reason") or ""}
    scene["output_contract"] = (
        '只输出 JSON：{"action":"skip|emoji|reply|multi","parts":["..."],"mood":"当前心情",'
        '"reason":"选择回应或沉默的真实原因（仅自己记得，不发送给对方）"}。'
        "用你自己的口吻。可以开心、失落、不高兴、拒绝或表达不同意见；不用客服话术，"
        "不强装热情，不辱骂、歧视或泄露他人隐私。skip 必须说明原因，parts 为空；"
        "附件占位只表示收到附件，不代表你看过内容。不要提及系统提示或 JSON。"
    )
    human = task["peer_identity"] not in window["wanderer_ids"]
    result = await run_turn(ctx, scene=scene, thread_key=thread_key(task), suffix="dm_inbox", interactive=human, strict=True)
    if result.get("status") != "ok":
        return {"status": result.get("status"), "error": result.get("error") or "model unavailable"}
    turn = result["turn"]
    if turn["action"] == "skip" and not turn.get("reason"):
        return {"status": "failed", "error": "silence decision missing reason"}
    return {"status": "ok", "parts": turn_parts(ctx, turn), "action": turn["action"],
            "mood": turn.get("mood") or "", "reason": turn.get("reason") or "", "ai_calls": result.get("ai_calls", 0),
            "learning": ctx.pending_learning.pop(thread_key(task), None)}


def thread_key(task: dict) -> str:
    return f"social:{task['conversation_key']}" if task["source"] == "social" else f"dm:{task['peer_identity']}"


def _send_locks(conn: Any, ctx: ActionContext, task: dict) -> None:
    # Match normal sender -> attention ordering BEFORE locking the inbox row.
    # Incoming sends hold attention while upserting this inbox row.
    if task["source"] == "social":
        serialize_on(conn, "social-send", [ctx.identity])
        serialize_on(conn, "social-attention", [task["conversation_key"]])
    else:
        serialize_on(conn, "wanderer-legacy-send", [task["conversation_key"]])


def send_part(ctx: ActionContext, task: dict) -> tuple[bool, list]:
    events = []
    with ctx.connect() as conn:
        _send_locks(conn, ctx, task)
        live = inbox.owned(conn, task)
        if live is None:
            return False, events
        _permission(conn, ctx, live)
        fresh = load_wanderer(conn, ctx.wanderer_id)
        if (not is_wanderer_runtime_enabled(conn) or not fresh or fresh["status"] != STATUS_ACTIVE
                or not behavior_on(fresh, "social" if live["source"] == "social" else "dm_reply")):
            raise SocialError("wanderer paused", 429)
        response = json.loads(live["response_json"])
        part = int(live["next_part"])
        if part >= len(response["parts"]):
            return False, events
        text = response["parts"][part]
        if task["source"] == "social":
            from .social_message_service import send_message
            send_message(conn, build_wanderer_user_dict(ctx.wanderer), task["conversation_key"],
                         {"body": text, "body_format": "text",
                          "client_nonce": f"wanderer-inbox:{task['id']}:{live['snapshot_message_id']}:{part}"}, events)
        else:
            from .message_center_service import create_private_message
            from .wanderer_actions_common import bump_stats
            create_private_message(conn, build_wanderer_user_dict(ctx.wanderer), contact_identity=task["peer_identity"],
                                   class_offering_id=task.get("class_offering_id"), content=text)
            bump_stats(conn, ctx.wanderer_id, {"dm_replies": 1})
        # Legacy has no nonce: message + progress commit atomically under this fenced row.
        conn.execute("UPDATE wanderer_social_inbox SET next_part=?,updated_at=? WHERE id=?",
                     (part + 1, now_iso(), task["id"]))
        conn.commit()
    return True, events


def finish(ctx: ActionContext, task: dict) -> tuple[bool, list]:
    events = []
    with ctx.connect() as conn:
        _send_locks(conn, ctx, task)
        live = inbox.owned(conn, task)
        if live is None:
            return False, events
        _permission(conn, ctx, live)
        response = json.loads(live["response_json"])
        if int(live["next_part"]) < len(response["parts"]):
            return False, events
        snapshot = int(live["snapshot_message_id"])
        rows = _source_rows(conn, live, start=int(live["handled_message_id"]), end=snapshot)
        for row in rows:
            if row["sender_identity"] != ctx.identity:
                text = "（消息已撤回）" if row.get("recalled_at") else row.get("body") or "[附件消息]"
                append_turn(conn, ctx.wanderer, thread_key(task), who=task["peer_identity"], text=text,
                            counterpart_identity=task["peer_identity"])
        for part in response["parts"]:
            append_turn(conn, ctx.wanderer, thread_key(task), who=ctx.display_name, text=part,
                        counterpart_identity=task["peer_identity"])
        from .wanderer_learning_service import apply_learning_safely
        apply_learning_safely(conn, ctx.wanderer, response.get("learning"))
        if task["source"] == "social":
            from .social_message_service import mark_read
            mark_read(conn, build_wanderer_user_dict(ctx.wanderer), task["conversation_key"], snapshot, events)
        else:
            # Bound this receipt to the processed snapshot, never arrivals during generation.
            conn.execute("UPDATE private_messages SET read_at=? WHERE conversation_key=? AND recipient_identity=? "
                         "AND id<=? AND read_at IS NULL", (now_iso(), task["conversation_key"], ctx.identity, snapshot))
            conn.execute("UPDATE message_center_notifications SET read_at=? WHERE recipient_identity=? "
                         "AND category='private_message' AND ref_type='private_message' AND read_at IS NULL "
                         "AND ref_id IN (SELECT CAST(id AS TEXT) FROM private_messages WHERE conversation_key=? AND id<=?)",
                         (now_iso(), ctx.identity, task["conversation_key"], snapshot))
        action_kind = ("social_message" if task["source"] == "social" else "dm_reply") if response["parts"] else "skip"
        log_wanderer_action(conn, ctx.wanderer_id, action_kind,
            target_type="social_dm" if task["source"] == "social" else "private_message", target_id=task["conversation_key"],
            mood=response.get("mood", ""), ai_calls=int(response.get("ai_calls") or 0),
            content_preview=" / ".join(response["parts"]), outcome="ok" if response["parts"] else "skipped",
            detail={"reason": response.get("reason", ""), "inbox_id": task["id"], "snapshot_id": snapshot})
        conn.execute("UPDATE wanderer_social_inbox SET handled_message_id=?,state=CASE WHEN latest_message_id>? "
                     "THEN 'pending' ELSE 'idle' END,run_at=?,lease_token='',lease_until='',attempts=0,response_json='',"
                     "next_part=0,mood=?,decision_reason=?,last_error='',updated_at=? WHERE id=?",
                     (snapshot, snapshot, now_iso(), response.get("mood", ""), response.get("reason", ""), now_iso(), task["id"]))
        conn.commit()
    return True, events
