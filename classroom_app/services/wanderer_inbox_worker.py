"""Small independent scheduler lane for human-waiting DM work (no web model calls)."""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import suppress

from . import wanderer_inbox_actions as actions
from . import wanderer_inbox_service as inbox
from .social_common import SocialError


async def _heartbeat(task, factory):
    while True:
        await asyncio.sleep(20)
        try:
            if not inbox.renew(task, factory=factory):
                return
        except Exception as exc:
            print(f"[WANDERER_INBOX] lease renewal failed: {type(exc).__name__}")


async def run_claim(task: dict, *, factory=None, ai_caller=None, sleep=None) -> str:
    heartbeat = asyncio.create_task(_heartbeat(task, factory))
    try:
        return await _run_claim(task, factory=factory, ai_caller=ai_caller, sleep=sleep)
    except SocialError as exc:
        inbox.release(task, str(exc), factory=factory, blocked=exc.status_code in {400, 403, 404},
                      delay=60 if exc.status_code == 429 else None)
        return "blocked" if exc.status_code in {400, 403, 404} else "retry"
    except Exception as exc:
        from .rate_limit_service import RateLimitExceededError
        delay = 60 if isinstance(exc, RateLimitExceededError) else None
        inbox.release(task, f"{type(exc).__name__}: {exc}", factory=factory, delay=delay)
        return "retry"
    finally:
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat


async def _run_claim(task: dict, *, factory=None, ai_caller=None, sleep=None) -> str:
    from . import social_push_service
    from .wanderer_actions_social import _sent_since, RATE_LIMIT, RATE_WINDOW_SECONDS, wanderer_streak
    from .social_common import iso_before
    ctx = actions.context(task, factory=factory, ai_caller=ai_caller, sleep=sleep)
    if ctx is None:
        inbox.release(task, "wanderer disabled or paused", factory=factory, delay=60)
        return "deferred"
    with ctx.connect() as conn:
        # Fresh snapshot, independent of the human-facing read receipt.
        live = inbox.owned(conn, task)
        if live is None:
            return "lease_lost"
        conn.commit()
        window = actions.window_for(conn, ctx, live, snapshot=live.get("snapshot_message_id") if live["response_json"] else None)
    if not live["response_json"]:
        if live.get("lease_lane") == "safety":
            with ctx.connect() as conn:
                safety_window = actions.latest_safety_window(conn, ctx, live, window)
            response = actions.safety_response(ctx, live, safety_window)
        else:
            response = None
        if live.get("lease_lane") == "safety" and response is None:
            inbox.release(task, "support triage no longer urgent", factory=factory, delay=0, clear_support=True)
            return "normal_pending"
        if task["source"] == "social":
            with ctx.connect() as conn:
                sent = _sent_since(conn, ctx.identity, task["conversation_key"], iso_before(seconds=RATE_WINDOW_SECONDS))
            if sent >= RATE_LIMIT:
                inbox.release(task, "conversation rate limit", factory=factory, delay=RATE_WINDOW_SECONDS)
                return "deferred"
        if response is not None:
            pass
        elif not window["new"]:
            response = {"status": "ok", "parts": [], "action": "skip", "mood": live["mood"], "reason": "没有待回应的有效消息"}
        elif (window["new"][-1]["sender_identity"] in window["wanderer_ids"]
              and wanderer_streak(window["rows"], window["wanderer_ids"]) >= 2):
            response = {"status": "ok", "parts": [], "action": "skip", "mood": live["mood"], "reason": "结束散修之间的连续互答"}
        else:
            response = await _compose_or_handoff(ctx, live, window, factory=factory)
            if response is None:
                return "support_pending"
        if response.get("status") != "ok":
            capped = response.get("status") == "capped"
            # Preserve the platform daily cap; never impersonate a decision with an emoji.
            if capped:
                from ..time_utils import local_now
                from datetime import timedelta
                now = local_now()
                wait = ((now + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0) - now).total_seconds()
            else:
                wait = None
            inbox.release(task, response.get("error") or response.get("status") or "failed", factory=factory, delay=wait)
            return "deferred" if capped else "retry"
        if not inbox.save_response(task, window["anchor"], response, factory=factory):
            return "lease_lost"
        live["response_json"] = json.dumps(response, ensure_ascii=False)
        live["snapshot_message_id"] = window["anchor"]
    response = json.loads(live["response_json"])
    for index in range(int(live["next_part"]), len(response["parts"])):
        if task["source"] == "social":
            with ctx.connect() as conn:
                sent = _sent_since(conn, ctx.identity, task["conversation_key"], iso_before(seconds=RATE_WINDOW_SECONDS))
            if sent >= RATE_LIMIT:
                inbox.release(task, "conversation rate limit", factory=factory, delay=RATE_WINDOW_SECONDS)
                return "deferred"
            await social_push_service.push([task["peer_identity"]],
                {"type": "typing", "conversation_key": task["conversation_key"], "identity": ctx.identity})
        await ctx.sleep(min(1.5, max(0.3, len(response["parts"][index]) * 0.025)))
        sent, events = actions.send_part(ctx, task)
        if not sent:
            finished, completion_events = actions.finish(ctx, task)
            await social_push_service.push_events(completion_events)
            return "done" if finished else "lease_lost"
        await social_push_service.push_events(events)
    # Optional compaction follows delivery, so it cannot delay the first answer.
    # The existing archive/source-message truth is retained on summary failure.
    from .wanderer_memory_service import invalidate_thread_cache, load_thread, HISTORY_DB_LIMIT
    from .wanderer_actions_common import compact_memory
    with ctx.connect() as conn:
        key = actions.thread_key(task)
        invalidate_thread_cache(ctx.wanderer_id, key)
        thread = load_thread(conn, ctx.wanderer_id, key)
    if (live.get("lease_lane") != "safety" and not response.get("support_fallback") and thread["history"]
            and len(thread["history"]) + len(window["new"]) + len(response["parts"]) >= HISTORY_DB_LIMIT - 6):
        await compact_memory(ctx, key)
    finished, events = actions.finish(ctx, task)
    await social_push_service.push_events(events)
    return "done" if finished else "lease_lost"


async def _compose_or_handoff(ctx, task, window, *, factory=None):
    """Cooperative handoff, never a token steal or a refund of a reserved AI call."""
    generation = asyncio.create_task(actions.compose(ctx, task, window))
    try:
        while True:
            done, _pending = await asyncio.wait({generation}, timeout=2)
            with ctx.connect() as conn:
                fresh = inbox.owned(conn, task)
                conn.commit()
            if (fresh and fresh.get("support_urgent") and int(fresh["latest_message_id"]) > int(window["anchor"])):
                generation.cancel()
                with suppress(asyncio.CancelledError):
                    await generation
                inbox.release(task, "new current safety request; model await interrupted", factory=factory, delay=0)
                return None
            if done:
                return await generation
    finally:
        if not generation.done():
            generation.cancel()
            with suppress(asyncio.CancelledError):
                await generation


async def process_once(*, factory=None, ai_caller=None, sleep=None, limit=None) -> list[str]:
    tasks = inbox.claim_safety(factory=factory) + inbox.claim(limit or inbox.concurrency(), factory=factory)
    return await asyncio.gather(*(run_claim(task, factory=factory, ai_caller=ai_caller, sleep=sleep) for task in tasks))


async def run_forever() -> None:
    active: set[asyncio.Task] = set()
    recovered_at = 0.0
    try:
        while True:
            for completed in active:
                if completed.done() and not completed.cancelled():
                    error = completed.exception()
                    if error:
                        print(f"[WANDERER_INBOX] execution interrupted: {type(error).__name__}")
            active = {task for task in active if not task.done()}
            try:
                if time.monotonic() - recovered_at >= 60:
                    inbox.recover()
                    recovered_at = time.monotonic()
                normal_active = sum(getattr(task, "_wanderer_lane", "normal") == "normal" for task in active)
                tasks = inbox.claim_safety() + inbox.claim(max(0, inbox.concurrency() - normal_active))
                for task in tasks:
                    pending = asyncio.create_task(run_claim(task))
                    pending._wanderer_lane = task.get("lease_lane", "normal")
                    active.add(pending)
            except Exception as exc:
                print(f"[WANDERER_INBOX] poll failed: {type(exc).__name__}: {exc}")
            await asyncio.sleep(2)
    finally:
        for task in active:
            task.cancel()
        await asyncio.gather(*active, return_exceptions=True)
