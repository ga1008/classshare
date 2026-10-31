"""Shared plumbing for 散修 actions (docs/wanderer-ai-free-agents-2026-10-25.md §5).

Every action follows the same discipline:

* short DB transactions only — a connection is never held across a model call
  or a pause (``ActionContext.connect`` opens a fresh one each time);
* at most one AI call per action, through ``wanderer_ai_service.generate_turn``
  (decision + generation in one call);
* every outcome is written to ``wanderer_action_log`` by the action itself.

``ActionContext`` carries the injectable seams (connection factory, AI caller,
sleep, RNG) so tests never reach a model, the network or the wall clock.
"""
from __future__ import annotations

import asyncio
import json
import random
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Iterable, Optional, Sequence

from ..time_utils import local_iso, local_now
from .wanderer_ai_service import AiCaller, generate_turn, summarize_text
from .wanderer_identity_service import (
    clamp_tier,
    load_wanderer,
    load_wanderers_by_student_ids,
    log_wanderer_action,
    reply_char_limit,
    tier_level,
)
from .wanderer_memory_service import append_turn, compact_thread, load_thread, needs_compaction, thread_history_text
from .wanderer_prompt_service import build_system_prompt
from .wanderer_text_style import strip_ai_formatting

ACTION_PAUSE_SECONDS = (1.0, 4.0)
TURN_USER_MESSAGE = "请依据系统提示里的人格、记忆与当前场景做出你的决定，只按输出合同输出一个 JSON 对象。"
DEFAULT_EMOJIS = ("👍", "😂", "🤔", "👀", "😄", "🙌")
_EMOJI_PATTERN = re.compile("[\U0001F300-\U0001FAFF☀-➿]")

SleepFn = Callable[[float], Awaitable[Any]]


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    try:
        return row[key]
    except (TypeError, KeyError, IndexError):
        return row[index]


@dataclass
class ActionContext:
    """Per-wanderer, per-tick state. ``capped`` flips once the daily AI cap is hit."""

    conn_factory: Optional[Callable[[], Any]]
    wanderer: dict[str, Any]
    ai_caller: Optional[AiCaller] = None
    sleep: SleepFn = asyncio.sleep
    rng: random.Random = field(default_factory=random.Random)
    capped: bool = False
    now: Optional[datetime] = None
    ai_calls: int = 0
    # Admin "run now" turns off rare self-directed moves (resurrect/appeal) so a
    # manual run stays predictable; regular ticks keep them on (lifecycle §11).
    special: bool = True
    pending_learning: dict[str, dict[str, Any]] = field(default_factory=dict)
    support_threads: set[str] = field(default_factory=set)

    def connect(self) -> Any:
        if self.conn_factory is not None:
            return self.conn_factory()
        from ..database import get_db_connection

        return get_db_connection()

    @property
    def wanderer_id(self) -> int:
        return int(self.wanderer["id"])

    @property
    def student_id(self) -> int:
        return int(self.wanderer["student_id"])

    @property
    def identity(self) -> str:
        return f"student:{self.student_id}"

    @property
    def display_name(self) -> str:
        return str(self.wanderer.get("display_name") or "散修")

    def current_time(self) -> datetime:
        return self.now or local_now()

    async def pause(self) -> None:
        await self.sleep(self.rng.uniform(*ACTION_PAUSE_SECONDS))

    def reload(self) -> None:
        with self.connect() as conn:
            fresh = load_wanderer(conn, self.wanderer_id)
        if fresh is not None:
            self.wanderer = fresh


def outcome(kind: str, status: str = "ok", *, ai_calls: int = 0, **extra: Any) -> dict[str, Any]:
    return {"kind": kind, "outcome": status, "ai_calls": int(ai_calls), **extra}


def behavior_on(wanderer: dict[str, Any], key: str) -> bool:
    try:
        return int((wanderer.get("behaviors") or {}).get(key, 1)) == 1
    except (TypeError, ValueError):
        return False


def record_action(ctx: ActionContext, action_kind: str, **fields: Any) -> int:
    """Log one action in its own short transaction."""
    with ctx.connect() as conn:
        action_id = log_wanderer_action(conn, ctx.wanderer_id, action_kind, **fields)
        conn.commit()
    return action_id


# ---------------------------------------------------------------------------
# AI turn + memory
# ---------------------------------------------------------------------------

def _capped_result() -> dict[str, Any]:
    return {"status": "capped", "turn": {"action": "skip", "parts": [], "mood": "", "like": False},
            "ai_calls": 0, "error": ""}


async def run_turn(
    ctx: ActionContext, *, scene: dict[str, Any], thread_key: str, suffix: str, user_message: str = "",
    interactive: bool = False,
    strict: bool = False,
    passive: bool = False,
) -> dict[str, Any]:
    """Assemble the prompt (short transaction), then make one AI call with no connection held.

    ``passive`` means someone directly asked/mentioned the wanderer: the reply
    is not capped by the tier's active-speech length and the prompt says so.
    ``user_message`` carries long reading material (knowledge study) that would
    not fit the scene body; it must already be wrapped in the injection guard.
    """
    scene = dict(scene)
    from .wanderer_student_support import fallback_for_scene, prepare_student_support
    fallback = fallback_for_scene(ctx.wanderer, scene, thread_key)
    if fallback:
        ctx.pending_learning.pop(thread_key, None)
        ctx.support_threads.add(thread_key)
        return fallback
    if ctx.capped:
        ctx.pending_learning.pop(thread_key, None)
        fallback = fallback_for_scene(ctx.wanderer, scene, thread_key, on_failure=True)
        if fallback:
            ctx.support_threads.add(thread_key)
        return fallback or _capped_result()
    with ctx.connect() as conn:
        from .wanderer_memory_service import invalidate_thread_cache
        from .wanderer_learning_service import prepare_learning_safely
        invalidate_thread_cache(ctx.wanderer_id, thread_key)
        prepare_student_support(conn, ctx.wanderer, thread_key, scene)
        prepare_learning_safely(conn, ctx.wanderer, thread_key, scene)
        system_prompt = build_system_prompt(conn, ctx.wanderer, scene=scene, thread_key=thread_key, passive=passive)
        conn.commit()  # persists the knowledge use_count bump
    message = "\n\n".join(part for part in (user_message, TURN_USER_MESSAGE) if part)
    result = await generate_turn(
        ctx.conn_factory, ctx.wanderer, system_prompt=system_prompt, user_message=message,
        task_label_suffix=suffix, ai_caller=ctx.ai_caller,
        interactive=interactive,
        strict=strict,
    )
    if result.get("status") == "capped":
        ctx.capped = True
    ctx.ai_calls += int(result.get("ai_calls") or 0)
    if result.get("status") == "ok":
        with ctx.connect() as conn:
            from .wanderer_emotion_service import apply_turn_mood
            from .wanderer_episodic_memory_service import record_memory
            turn = result.get("turn") or {}
            apply_turn_mood(conn, ctx.wanderer, turn)
            inner = str(turn.get("inner") or "").strip()
            if inner:
                where = str(scene.get("kind") or "内心")
                if scene.get("title"):
                    where = f"{where} · {str(scene.get('title'))[:40]}"
                record_memory(conn, ctx.wanderer, kind="reflection", title="没发出去的心里话",
                              content=inner, thread_key=thread_key, importance=0.55,
                              source_kind="inner", source_ref=f"{thread_key}:{local_iso()}", location=where)
            conn.commit()
    if result.get("status") != "ok" or (result.get("turn") or {}).get("action") in {"skip", "emoji"}:
        fallback = fallback_for_scene(ctx.wanderer, scene, thread_key, on_failure=True)
        if fallback:
            ctx.pending_learning.pop(thread_key, None)
            ctx.support_threads.add(thread_key)
            return {**fallback, "ai_calls": int(result.get("ai_calls") or 0)}
    from .wanderer_learning_service import parse_learning
    if result.get("status") == "ok":
        ctx.pending_learning[thread_key] = parse_learning(result.get("raw_text") or "", scene.get("_learning_snapshot"))
    else:
        ctx.pending_learning.pop(thread_key, None)
    return result


def clip_part(ctx: ActionContext, text: Any, *, limit: bool = True) -> str:
    """Public text: AI formatting stripped, then (active speech only) clipped to the tier's limit.

    Passive replies (a question/@-mention) pass ``limit=False``: they keep their
    full length but still lose Markdown formatting and keep the persona voice.
    """
    value = strip_ai_formatting(text)
    if not limit:
        return value
    cap = reply_char_limit(ctx.wanderer.get("tier"))
    return value if len(value) <= cap else value[:cap]


def turn_parts(ctx: ActionContext, turn: dict[str, Any], *, public: bool = True, limit: bool = True) -> list[str]:
    """Non-empty parts; public active speech is clipped to the tier's limit, passive speech is not."""
    items = turn.get("parts") or []
    if not public:
        return [str(item or "").strip() for item in items if str(item or "").strip()]
    cleaned = (clip_part(ctx, item, limit=limit) for item in items)
    return [part for part in cleaned if part]


def thread_location(thread_key: Any) -> str:
    """Human-readable place for a conversation thread (used on memories and the trace)."""
    key = str(thread_key or "")
    if key.startswith("social:group"):
        return "群聊"
    if key.startswith("social:friend"):
        return "好友申请"
    if key.startswith("social:"):
        return "私信"
    if key.startswith("blog"):
        return "博客"
    if key.startswith("classroom"):
        return "课堂"
    if key.startswith("knowledge") or key.startswith("material"):
        return "知识库"
    if key == "manual" or key.startswith("manual:"):
        return "平台手册"
    if key == "dao" or key.startswith("dao"):
        return "天道台"
    if key.startswith("reflection"):
        return "自省"
    return "平台"


async def remember(
    ctx: ActionContext,
    thread_key: str,
    *,
    said: Sequence[str] = (),
    heard: Optional[tuple[str, str]] = None,
    counterpart_identity: str = "",
    location: str = "",
) -> None:
    """Append what was heard/said to the thread, then compact if it grew too large."""
    thread: Optional[dict[str, Any]] = None
    with ctx.connect() as conn:
        if heard and str(heard[1] or "").strip():
            thread = append_turn(conn, ctx.wanderer, thread_key, who=heard[0], text=heard[1],
                                 counterpart_identity=counterpart_identity)
        for part in said:
            thread = append_turn(conn, ctx.wanderer, thread_key, who=ctx.display_name, text=part,
                                 counterpart_identity=counterpart_identity)
        if heard or said:
            from .wanderer_emotion_service import mood_from_wanderer
            from .wanderer_episodic_memory_service import record_memory
            mood = mood_from_wanderer(ctx.wanderer)
            if not location:
                location = thread_location(thread_key)
            pieces = []
            if heard and str(heard[1] or "").strip():
                pieces.append(f"{heard[0]}对我说：{str(heard[1]).strip()[:800]}")
            if said:
                pieces.append("我回应：" + " / ".join(str(item)[:500] for item in said))
            if pieces:
                record_memory(conn, ctx.wanderer, kind="conversation",
                              title=f"与{heard[0] if heard else '对方'}的交流",
                              content="\n".join(pieces), thread_key=thread_key,
                              counterpart_identity=counterpart_identity, location=location,
                              people=[counterpart_identity] if counterpart_identity else [],
                              source_kind="thread", source_ref=f"{thread_key}:{local_iso()}",
                              valence=mood.get("valence", 0.0), arousal=mood.get("arousal", 0.0))
        commit_learning(ctx, conn, thread_key)
        conn.commit()
    support = thread_key in ctx.support_threads
    ctx.support_threads.discard(thread_key)
    if not support and thread is not None and needs_compaction(thread):
        await compact_memory(ctx, thread_key)


def commit_learning(ctx: ActionContext, conn: Any, thread_key: str) -> dict:
    from .wanderer_learning_service import apply_learning_safely
    return apply_learning_safely(conn, ctx.wanderer, ctx.pending_learning.pop(thread_key, None))


async def compact_memory(ctx: ActionContext, thread_key: str) -> bool:
    """One AI summarization (outside any transaction), then fold the thread."""
    if ctx.capped:
        return False
    with ctx.connect() as conn:
        from .wanderer_memory_service import invalidate_thread_cache
        invalidate_thread_cache(ctx.wanderer_id, thread_key)
        snapshot = load_thread(conn, ctx.wanderer_id, thread_key)
        text = thread_history_text(snapshot)
    summary = await summarize_text(ctx.conn_factory, ctx.wanderer, text, ai_caller=ctx.ai_caller)
    ctx.ai_calls += 1
    if not summary:
        return False
    with ctx.connect() as conn:
        compact_thread(conn, ctx.wanderer, thread_key, lambda _history: summary, expected_thread=snapshot)
        conn.commit()
    return True


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------

def interest_score(interests: Iterable[Any], *texts: Any) -> float:
    """Whole-tag hits count 2, a shared 2-char fragment counts 0.5."""
    haystack = " ".join(str(text or "") for text in texts).lower()
    if not haystack.strip():
        return 0.0
    score = 0.0
    for interest in interests or []:
        tag = str(interest or "").strip().lower()
        if len(tag) < 2:
            continue
        if tag in haystack:
            score += 2.0
        elif any(tag[i:i + 2] in haystack for i in range(len(tag) - 1)):
            score += 0.5
    return score


def weighted_choice(rng: random.Random, items: Sequence[Any], weights: Sequence[float]) -> Any:
    if not items:
        return None
    safe = [max(0.0, float(weight)) for weight in weights]
    if sum(safe) <= 0:
        return rng.choice(list(items))
    return rng.choices(list(items), weights=safe, k=1)[0]


def emoji_for(ctx: ActionContext) -> str:
    """An emoji from the persona's own emoji habit, else a neutral default (no AI)."""
    habit = str((ctx.wanderer.get("speech_style") or {}).get("emoji") or "")
    found = _EMOJI_PATTERN.findall(habit)
    return ctx.rng.choice(found or list(DEFAULT_EMOJIS))


def counterpart_profile(conn: Any, *, role: str, user_pk: Any, name: str, real_name: bool = True) -> dict[str, Any]:
    """Public info about whoever the wanderer is talking to (design §4 item 5)."""
    role = str(role or "").strip().lower()
    if role == "student" and real_name:
        other = next(iter(load_wanderers_by_student_ids(conn, [user_pk]).values()), None)
        if other is not None:
            tier = clamp_tier(other.get("tier"))
            return {"name": other.get("display_name") or name, "is_wanderer": True,
                    "tier_name": tier_level(tier).get("name") or ""}
    label = {"teacher": "教师", "assistant": "平台 AI 助手"}.get(role, "真实学生")
    return {"name": name or "对方", "is_wanderer": False, "role_label": label}


def speaker_label(*, own_identity: str, identity: str, role: str, user_pk: Any, name: str,
                  real_name: bool, wanderer_ids: set[int]) -> str:
    if identity == own_identity:
        return f"{name}（你自己）"
    if role == "student" and real_name and as_int(user_pk) in wanderer_ids:
        return f"{name}（散修）"
    if role == "teacher":
        return f"{name}（教师）"
    if role == "assistant":
        return f"{name}（AI 助手）"
    return name or "同学"


def as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def day_start_iso(now: datetime) -> str:
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")


def today_action_count(conn: Any, wanderer_id: int, now: datetime) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM wanderer_action_log WHERE wanderer_id = ? AND created_at >= ? "
        "AND action_kind NOT IN ('knowledge_forget','experience_learn','reflect','daily_reflection')",
        (int(wanderer_id), day_start_iso(now)),
    ).fetchone()
    return as_int(_row_value(row, "n"))


def bump_stats(conn: Any, wanderer_id: int, deltas: dict[str, int], *, visited_offering: Optional[int] = None) -> None:
    """Add to the cached counters in ``stats_json`` and touch ``last_active_at``. Caller commits."""
    # A row lock serializes the read/modify/write with concurrent inbox lanes.
    conn.execute("UPDATE wanderer_agents SET stats_json=stats_json WHERE id=?", (int(wanderer_id),))
    row = conn.execute("SELECT stats_json FROM wanderer_agents WHERE id = ?", (int(wanderer_id),)).fetchone()
    try:
        stats = json.loads(_row_value(row, "stats_json") or "{}") if row is not None else {}
    except (TypeError, ValueError):
        stats = {}
    stats = dict(stats) if isinstance(stats, dict) else {}
    for key, delta in deltas.items():
        stats[key] = as_int(stats.get(key)) + int(delta)
    if visited_offering is not None:
        visited = [as_int(item) for item in stats.get("visited_offerings") or [] if as_int(item) != int(visited_offering)]
        stats["visited_offerings"] = [*visited, int(visited_offering)][-20:]
    now = local_iso()
    conn.execute(
        "UPDATE wanderer_agents SET stats_json = ?, last_active_at = ?, updated_at = ? WHERE id = ?",
        (json.dumps(stats, ensure_ascii=False, sort_keys=True), now, now, int(wanderer_id)),
    )
