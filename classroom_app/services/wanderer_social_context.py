"""Authorised, bounded social context for a wanderer's decisions.

The same public/friend card used by a person is the source of profile facts.
No contact details, anonymous authorship or unrelated private conversations
are queried. Recent messages remain verbatim; earlier exchanges use memory.
"""
from __future__ import annotations

import json
from typing import Any

from .social_card_service import user_card
from .wanderer_identity_service import build_wanderer_user_dict
from .wanderer_memory_service import invalidate_thread_cache, load_thread, thread_context_block

PROFILE_FIELDS = ("name", "role_label", "is_wanderer", "tier_badge", "archetype", "org", "mood", "online",
                  "courses", "radar", "cultivation_hidden", "relation", "friend_extras")
CONVERSATION_CHARS = 32_000


def friend_context(conn: Any, wanderer: dict[str, Any], identity: str) -> dict[str, Any]:
    """Current visibility rules are applied before selecting the allowed profile fields."""
    card = user_card(conn, build_wanderer_user_dict(wanderer), identity)
    profile = {key: card.get(key) for key in PROFILE_FIELDS}
    key = f"social:friend:{identity}"
    invalidate_thread_cache(int(wanderer["id"]), key)
    memory = load_thread(conn, int(wanderer["id"]), key)
    return {"profile": profile, "relationship_memory": thread_context_block(memory)}


def context_block(context: dict[str, Any]) -> str:
    return ("【对方当前向你可见的资料】\n" + json.dumps(context.get("profile") or {}, ensure_ascii=False)
            + "\n【你对结交过程的记忆】\n" + str(context.get("relationship_memory") or ""))


def conversation_text(rows: list[dict[str, Any]], people: dict[str, Any], me: str) -> str:
    """Newest complete messages fitting the budget; never silently cut individual text."""
    chosen: list[str] = []
    used = 0
    for row in reversed(rows):
        person = people.get(row.get("sender_identity")) or {}
        speaker = "你" if row.get("sender_identity") == me else str(person.get("name") or "群成员")
        body = "（消息已撤回）" if row.get("recalled_at") else str(row.get("body") or "[附件或系统消息]")
        line = f"[{row.get('created_at') or ''}] {speaker}：{body}"
        if used + len(line) > CONVERSATION_CHARS:
            break
        chosen.append(line)
        used += len(line) + 1
    prefix = f"近期完整原文 {len(chosen)} 条；更早交流以长期记忆为准，不代表无界全历史。\n"
    return prefix + "\n".join(reversed(chosen))
