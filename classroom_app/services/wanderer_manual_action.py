"""The wanderer's deliberate ``read_manual`` action.

The manual is also injected automatically for the current scene; this action
lets a wanderer decide on its own to study a topic it is curious about (at most
twice a day), then keeps the section as a knowledge memory so later prompts can
recall that it understood the mechanism.
"""
from __future__ import annotations

from .wanderer_actions_common import (
    ActionContext, behavior_on, day_start_iso, outcome, record_action, run_turn,
)
from .wanderer_identity_service import log_wanderer_action
from .wanderer_platform_manual import consult, menu, topic_keys
from .wanderer_prompt_service import extract_json_object
from .wanderer_social_triggers import is_awake_now

MANUAL_DAILY_LIMIT = 2
MANUAL_TARGET_TYPE = "manual"


def manual_reads_today(conn, ctx: ActionContext) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM wanderer_action_log WHERE wanderer_id = ? AND action_kind = 'read_manual' "
        "AND created_at >= ?", (ctx.wanderer_id, day_start_iso(ctx.current_time())),
    ).fetchone()
    try:
        return int(row["n"] or 0)
    except (TypeError, KeyError, IndexError):
        return 0


async def read_manual(ctx: ActionContext) -> list[dict]:
    if (not behavior_on(ctx.wanderer, "manual_read") or not is_awake_now(ctx.wanderer, ctx.current_time())):
        return []
    with ctx.connect() as conn:
        if manual_reads_today(conn, ctx) >= MANUAL_DAILY_LIMIT:
            return []
    scene = {
        "kind": "查阅平台手册", "title": "有想弄明白的平台机制吗",
        "body": menu(), "task": ("你可以挑一个现在真想知道的主题，比如好友申请为什么一直显示、"
                                 "课堂和私信有什么区别；不想查就 skip。"),
        "output_contract": ('只输出 JSON：{"topic":"主题 key 或标题","reason":"为什么想查"}；'
                            'skip 时输出 {"action":"skip","reason":"..."}。'),
    }
    result = await run_turn(ctx, scene=scene, thread_key="manual", suffix="read_manual")
    parsed = extract_json_object(result.get("raw_text")) or {}
    calls = int(result.get("ai_calls") or 0)
    action = str(parsed.get("action") or "").strip().lower()
    wanted = str(parsed.get("topic") or "").strip()
    if result.get("status") != "ok" or action == "skip" or not wanted:
        record_action(ctx, "read_manual", target_type=MANUAL_TARGET_TYPE, outcome="skipped", ai_calls=calls,
                      location="平台手册", detail={"reason": str(parsed.get("reason") or "这次不想查")[:200]})
        return [outcome("read_manual", "skipped", ai_calls=calls)]
    found = consult(wanted)
    if not found.get("topic"):
        record_action(ctx, "read_manual", target_type=MANUAL_TARGET_TYPE, outcome="failed", ai_calls=calls,
                      location="平台手册", detail={"reason": "没有这个主题", "query": wanted[:80]})
        return [outcome("read_manual", "failed", ai_calls=calls)]
    title = found["title"]
    location = f"平台手册 · {title}"
    with ctx.connect() as conn:
        log_wanderer_action(
            conn, ctx.wanderer_id, "read_manual", target_type=MANUAL_TARGET_TYPE, target_id=found["topic"],
            content_preview=f"查阅：{title}", location=location, ai_calls=calls,
            detail={"topic": found["topic"], "title": title,
                    "reason": str(parsed.get("reason") or "")[:200], "chars": len(found["text"])},
        )
        from .wanderer_episodic_memory_service import record_memory
        record_memory(conn, ctx.wanderer, kind="knowledge", title=f"读过平台手册：{title}",
                      content=(f"我查了平台手册的「{title}」：{found['text'][:700]}"),
                      importance=0.55, tags=["平台手册", found["topic"]], source_kind="manual",
                      source_ref=f"manual:{found['topic']}", location=location)
        conn.commit()
    return [outcome("read_manual", ai_calls=calls, topic=found["topic"])]


def topic_options() -> list[str]:
    return list(topic_keys())
