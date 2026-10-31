"""Daily self-reflection for 散修 (humanity v2).

One model turn per wanderer per local day, outside the action budget. It reviews
today's trace, mood, relationships and memories; writes bounded next-day tuning;
may request promotion; and decides whether old memories should be compressed.
The reflection row's unique ``(wanderer_id, reflection_date)`` is the idempotency
anchor.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import timedelta
from typing import Any, Callable, Optional

from ..time_utils import local_iso, local_now
from .wanderer_actions_common import ActionContext, record_action, run_turn
from .wanderer_identity_service import STATUS_ACTIVE, invalidate_wanderer_cache, list_wanderers
from .wanderer_prompt_service import extract_json_object
from .wanderer_settings_service import (
    is_wanderer_runtime_enabled,
    reflection_batch_size,
    reflection_enabled,
    tick_max_seconds,
)

TASK_KIND_WANDERER_DAILY_REFLECTION = "wanderer_daily_reflection"
REFLECTION_DEDUPE_KEY = "wanderer:daily-reflection"
REFLECTION_INTERVAL_SECONDS = 86_400
REFLECTION_MAX_ATTEMPTS = 24
REFLECTION_INPUT_CHARS = 60_000
REFLECTION_SUMMARY_MAX = 1_200
REFLECTION_CONTRACT = """【输出合同】只输出一个 JSON 对象，不要 Markdown，不要解释：
{
  "mood": {"label": "一两个词", "valence": -1到1, "energy": 0到1, "reason": "为什么"},
  "today": {"summary": "今天发生了什么，我此刻怎么看", "notable_actions": ["..."], "consequences": ["..."]},
  "relationships": [{"identity": "student:12", "affinity_delta": -2到2, "trust_delta": -0.2到0.2, "note": "..."}],
  "memory": {"new_episodes": ["今天值得记住的事"], "consolidate": [], "compress": false, "reason": "..."},
  "self_review": {"did_well": "...", "regret": "...", "patterns": ["..."], "values_unchanged": ["..."]},
  "tomorrow": {"proactive_multiplier": 0.7到1.3,
               "tone": {"warmth": -1到1, "directness": -1到1, "playfulness": -1到1, "verbosity": -1到1},
               "focus_topics": ["..."], "avoid_topics": ["..."], "people_to_contact": ["student:12"],
               "planned_actions": ["..."]},
  "promotion_intent": false
}
不必每项都填满；没有把握的字段可以省略，但不要编造没有发生的事。"""


def reflection_date(now: Any = None) -> str:
    return (now or local_now()).date().isoformat()


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    try:
        return row[key]
    except (TypeError, KeyError, IndexError):
        return row[index]


def _loads(raw: Any, fallback: type) -> Any:
    try:
        value = json.loads(raw) if isinstance(raw, str) and raw.strip() else None
    except (TypeError, ValueError):
        value = None
    return value if isinstance(value, fallback) else fallback()


def _clamp(value: Any, low: float, high: float, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _row(conn: Any, wanderer_id: int, date: str) -> Optional[dict[str, Any]]:
    from ..db.schema_wanderers import ensure_wanderer_schema

    ensure_wanderer_schema(conn)
    row = conn.execute(
        "SELECT id, status, output_json, applied_json, context_chars, prompt_chars, ai_calls, error, created_at "
        "FROM wanderer_daily_reflections WHERE wanderer_id = ? AND reflection_date = ? LIMIT 1",
        (int(wanderer_id), date),
    ).fetchone()
    if row is None:
        return None
    return {"id": int(_row_value(row, "id")), "status": str(_row_value(row, "status", 1) or ""),
            "output_json": str(_row_value(row, "output_json", 2) or "{}"),
            "applied_json": str(_row_value(row, "applied_json", 3) or "{}"),
            "context_chars": int(_row_value(row, "context_chars", 4) or 0),
            "prompt_chars": int(_row_value(row, "prompt_chars", 5) or 0),
            "ai_calls": int(_row_value(row, "ai_calls", 6) or 0),
            "error": str(_row_value(row, "error", 7) or ""),
            "created_at": str(_row_value(row, "created_at", 8) or "")}


def ensure_reflection(conn: Any, wanderer: dict[str, Any], date: str) -> dict[str, Any]:
    from ..db.schema_wanderers import ensure_wanderer_schema

    ensure_wanderer_schema(conn)
    conn.execute(
        "INSERT INTO wanderer_daily_reflections (wanderer_id, reflection_date, status, created_at) "
        "VALUES (?, ?, 'pending', ?) ON CONFLICT(wanderer_id, reflection_date) DO NOTHING",
        (int(wanderer["id"]), str(date), local_iso()),
    )
    return _row(conn, int(wanderer["id"]), str(date)) or {}


def _set_status(conn: Any, row_id: int, status: str, *, error: str = "", **fields: Any) -> None:
    assignments = ["status = ?", "error = ?"]
    values: list[Any] = [str(status), str(error or "")[:500]]
    for key, value in fields.items():
        assignments.append(f"{key} = ?")
        values.append(value)
    values.append(int(row_id))
    conn.execute(f"UPDATE wanderer_daily_reflections SET {', '.join(assignments)} WHERE id = ?", tuple(values))


def _requirements_text(conn: Any, wanderer: dict[str, Any]) -> str:
    from .wanderer_life_service import promotion_requirements
    try:
        req = promotion_requirements(conn, wanderer)
    except Exception:  # noqa: BLE001
        return ""
    if req.get("max_tier"):
        return "你已至最高境界，接下来只剩自己决定怎么活。"
    return (f"知识 {req['knowledge']}/{req['knowledge_target']} 字；修为 {req['cultivation']:g}/"
            f"{req['cultivation_target']}；近七日互动 {req['interactions']}/{req['interactions_needed']} 次；"
            f"升阶冷却 {req['cooldown_days_left']} 天" + ("；条件已满足" if req.get("ready") else ""))


def _life_line(wanderer: dict[str, Any]) -> str:
    life = wanderer.get("life") or {}
    if not life:
        return ""
    return (f"第 {int(wanderer.get('tier') or 0)} 境，剩余约 {life.get('remaining_days', 0)} 天寿元"
            f"（{life.get('remaining_weeks', 0)} 周）。")


def _input_text(conn: Any, wanderer: dict[str, Any], now: Any) -> str:
    from .wanderer_emotion_service import mood_block, tuning_block
    from .wanderer_episodic_memory_service import memory_block, today_trace
    from .wanderer_relationship_service import relationship_block

    previous = conn.execute(
        "SELECT output_json, applied_json FROM wanderer_daily_reflections "
        "WHERE wanderer_id = ? AND status = 'applied' ORDER BY reflection_date DESC LIMIT 1",
        (int(wanderer["id"]),),
    ).fetchone()
    previous_text = ""
    if previous is not None:
        output = _loads(_row_value(previous, "output_json", 0), dict)
        applied = _loads(_row_value(previous, "applied_json", 1), dict)
        summary = str((output.get("today") or {}).get("summary") or applied.get("summary") or "")[:500]
        intentions = (applied.get("self_tuning") or {}).get("intentions") or []
        previous_text = f"上次反省：{summary}\n上次给自己的计划：{'；'.join(str(item) for item in intentions)[:500]}"
    requirements = _requirements_text(conn, wanderer)
    since = (now - timedelta(days=7)).isoformat(timespec="seconds")
    verdicts = conn.execute(
        "SELECT verdict, reason, advice, created_at FROM wanderer_dao_reviews WHERE wanderer_id = ? "
        "AND created_at >= ? ORDER BY id DESC LIMIT 5", (int(wanderer["id"]), since),
    ).fetchall()
    dao_lines = [f"[{str(_row_value(row, 'created_at', 3) or '')[:16].replace('T', ' ')}] "
                 f"{_row_value(row, 'verdict', 0)}：{str(_row_value(row, 'reason', 1) or '')[:200]}"
                 + (f"（建议：{str(_row_value(row, 'advice', 2) or '')[:120]}）" if _row_value(row, "advice", 2) else "")
                 for row in verdicts]
    dao_block = "【最近的天道裁决】\n" + "\n".join(dao_lines) if dao_lines else ""
    blocks = [
        f"【你现在的状态】{_life_line(wanderer)}",
        f"【升阶条件】{requirements}" if requirements else "",
        mood_block(wanderer, now=now),
        tuning_block(wanderer),
        relationship_block(conn, wanderer),
        memory_block(conn, wanderer, budget_chars=12_000),
        today_trace(conn, wanderer, now=now, limit=40),
        dao_block,
        previous_text,
    ]
    return "\n\n".join(block for block in blocks if block)[:REFLECTION_INPUT_CHARS]


def _tomorrow_date(now: Any) -> str:
    return ((now or local_now()) + timedelta(days=1)).date().isoformat()


def _apply_payload(conn: Any, wanderer: dict[str, Any], payload: dict[str, Any], now: Any) -> dict[str, Any]:
    from .wanderer_emotion_service import save_mood
    from .wanderer_episodic_memory_service import record_memory
    from .wanderer_life_service import promotion_requirements
    from .wanderer_relationship_service import adjust_relationship

    applied: dict[str, Any] = {}
    mood = payload.get("mood") if isinstance(payload.get("mood"), dict) else {}
    if mood:
        label = str(mood.get("label") or mood.get("emotion") or "").strip()[:20]
        if label:
            applied["mood"] = save_mood(conn, wanderer, {
                "emotion": label,
                "valence": _clamp(mood.get("valence"), -1.0, 1.0),
                "arousal": _clamp(mood.get("energy", mood.get("arousal")), 0.0, 1.0, 0.35),
                "cause": str(mood.get("reason") or "")[:160],
            })
    tomorrow = payload.get("tomorrow") if isinstance(payload.get("tomorrow"), dict) else {}
    tone = tomorrow.get("tone") if isinstance(tomorrow.get("tone"), dict) else {}
    multiplier = _clamp(tomorrow.get("proactive_multiplier"), 0.7, 1.3, 1.0)
    intentions = [str(item)[:120] for item in (tomorrow.get("planned_actions") or []) if str(item).strip()][:3]
    next_day = {
        "effective_date": _tomorrow_date(now),
        "initiative": round(_clamp((multiplier - 1.0) * 2.5, -1.0, 1.0), 2),
        "warmth": round(_clamp(tone.get("warmth"), -1.0, 1.0), 2),
        "directness": round(_clamp(tone.get("directness"), -1.0, 1.0), 2),
        "focus": "、".join(str(item)[:30] for item in (tomorrow.get("focus_topics") or [])[:3])[:60],
        "intentions": intentions,
        "note": str((payload.get("self_review") or {}).get("regret") or "")[:200],
        "proactive_multiplier": round(multiplier, 2),
    }
    tuning = {**((wanderer.get("self_tuning") or {}) if isinstance(wanderer.get("self_tuning"), dict) else {}),
              "next_day": next_day, "updated_at": local_iso()}
    conn.execute("UPDATE wanderer_agents SET self_tuning_json = ?, last_reflection_at = ?, updated_at = ? "
                 "WHERE id = ?", (json.dumps(tuning, ensure_ascii=False, sort_keys=True), local_iso(),
                                  local_iso(), int(wanderer["id"])))
    wanderer["self_tuning"] = tuning
    applied["self_tuning"] = next_day

    relationships = payload.get("relationships") if isinstance(payload.get("relationships"), list) else []
    changes = []
    for item in relationships[:8]:
        if not isinstance(item, dict):
            continue
        result = adjust_relationship(
            conn, wanderer, item.get("identity"), affinity_delta=item.get("affinity_delta") or 0,
            trust_delta=item.get("trust_delta") or 0, note=item.get("note") or "",
        )
        if result:
            changes.append(result)
    applied["relationships"] = changes

    memory = payload.get("memory") if isinstance(payload.get("memory"), dict) else {}
    new_episodes = []
    for item in (memory.get("new_episodes") or [])[:8]:
        text = str(item if not isinstance(item, dict) else item.get("content") or item.get("summary") or "")
        if text.strip():
            memory_id = record_memory(conn, wanderer, kind="reflection", title="反省时想起的事",
                                      content=text, importance=0.62, source_kind="reflection",
                                      source_ref=f"{reflection_date(now)}:{len(new_episodes)}", location="自省")
            if memory_id:
                new_episodes.append(memory_id)
    applied["new_episodes"] = new_episodes

    today_summary = payload.get("today") if isinstance(payload.get("today"), dict) else {}
    summary = str(today_summary.get("summary") or "")[:REFLECTION_SUMMARY_MAX]
    if summary:
        record_memory(conn, wanderer, kind="reflection", title=f"{reflection_date(now)} 自省",
                      content=summary, importance=0.7, tags=["自省"],
                      source_kind="daily_reflection", source_ref=reflection_date(now), location="自省")

    promotion_requested = False
    if bool(payload.get("promotion_intent")):
        try:
            ready = bool(promotion_requirements(conn, wanderer).get("ready"))
        except Exception:  # noqa: BLE001
            ready = False
        if ready:
            note = "夜里想过之后，决定向天道申请升阶。"
            conn.execute("UPDATE wanderer_agents SET promotion_requested = 1, promotion_requested_at = ?, "
                         "promotion_note = ?, updated_at = ? WHERE id = ?",
                         (local_iso(), note, local_iso(), int(wanderer["id"])))
            promotion_requested = True
    applied["promotion_requested"] = promotion_requested
    applied["compress"] = bool(memory.get("compress"))
    applied["summary"] = summary
    return applied


async def _summarizer(ctx: ActionContext):
    from .wanderer_ai_service import summarize_text

    async def summarize(text: str) -> str:
        return await summarize_text(ctx.conn_factory, ctx.wanderer, text, ai_caller=ctx.ai_caller)
    return summarize


async def _maybe_compress(ctx: ActionContext, *, force: bool) -> dict[str, int]:
    from .wanderer_episodic_memory_service import compress_memories, memory_stats
    from .wanderer_settings_service import prompt_char_budget

    with ctx.connect() as conn:
        stats = memory_stats(conn, ctx.wanderer)
        budget = prompt_char_budget(conn)
    if not force and stats["active_chars"] <= int(budget * 0.75):
        return {"compressed": 0, "chapters": 0, "discarded": 0, "active": stats["active"], "active_chars": stats["active_chars"]}
    summarizer = await _summarizer(ctx)
    with ctx.connect() as conn:
        result = await compress_memories(conn, ctx.wanderer, summarizer=summarizer)
        conn.commit()
    if result.get("compressed"):
        ctx.ai_calls += 1
    return result


async def run_reflection(ctx: ActionContext, *, force: bool = False) -> dict[str, Any]:
    """One wanderer's daily reflection; safe to retry while the row is pending/failed."""
    now = ctx.current_time()
    date = reflection_date(now)
    ctx.reload()
    with ctx.connect() as conn:
        row = ensure_reflection(conn, ctx.wanderer, date)
        conn.commit()
    if not row:
        return {"status": "failed", "error": "reflection row unavailable", "ai_calls": 0}
    stale_running = False
    if row["status"] == "running" and row.get("created_at"):
        try:
            from ..time_utils import parse_datetime
            stale_running = (now - parse_datetime(row["created_at"])).total_seconds() > 900
        except (TypeError, ValueError):
            stale_running = True
    if (row["status"] == "applied" or (row["status"] == "running" and not stale_running)) and not force:
        return {"status": "skipped", "reason": row["status"], "ai_calls": 0}
    with ctx.connect() as conn:
        _set_status(conn, row["id"], "running")
        conn.commit()
    try:
        with ctx.connect() as conn:
            body = _input_text(conn, ctx.wanderer, now)
        scene = {
            "kind": "夜深了，你一个人坐着回想今天", "title": "每日自省",
            "meta": f"{date} · 不看别人，只看自己的一天", "body": body,
            "tags": ["自省", "今天"], "task": "回顾今天做过的事、说过的话和后果；诚实地看自己，然后决定明天怎么过。",
            "output_contract": REFLECTION_CONTRACT,
        }
        result = await run_turn(ctx, scene=scene, thread_key="reflection", suffix="daily_reflection")
        calls = int(result.get("ai_calls") or 0)
        if result.get("status") == "capped":
            with ctx.connect() as conn:
                _set_status(conn, row["id"], "skipped_capped", error="今日 AI 额度不足", ai_calls=calls,
                            context_chars=len(body))
                conn.commit()
            return {"status": "skipped_capped", "ai_calls": calls}
        payload = extract_json_object(result.get("raw_text")) if result.get("status") == "ok" else None
        if not isinstance(payload, dict):
            with ctx.connect() as conn:
                _set_status(conn, row["id"], "failed", error="无法解析反省 JSON", ai_calls=calls,
                            context_chars=len(body))
                conn.commit()
            return {"status": "failed", "error": "unparseable", "ai_calls": calls}
        with ctx.connect() as conn:
            applied = _apply_payload(conn, ctx.wanderer, payload, now)
            _set_status(conn, row["id"], "applied", ai_calls=calls, context_chars=len(body),
                        output_json=json.dumps(payload, ensure_ascii=False),
                        applied_json=json.dumps(applied, ensure_ascii=False), applied_at=local_iso())
            conn.commit()
        mood_payload = payload.get("mood") if isinstance(payload.get("mood"), dict) else {}
        record_action(ctx, "daily_reflection", mood=str(mood_payload.get("label") or ""),
                      ai_calls=calls, detail={"summary": applied.get("summary", "")[:200],
                                              "intentions": applied.get("self_tuning", {}).get("intentions", [])})
        compressed = await _maybe_compress(ctx, force=bool(applied.get("compress")))
        invalidate_wanderer_cache()
        return {"status": "applied", "ai_calls": calls + (1 if compressed.get("compressed") else 0),
                "applied": applied, "compressed": compressed}
    except Exception as exc:  # noqa: BLE001 - one failed reflection must not stop the batch
        with ctx.connect() as conn:
            _set_status(conn, row["id"], "failed", error=f"{type(exc).__name__}: {exc}")
            conn.commit()
        return {"status": "failed", "error": str(exc)[:300], "ai_calls": 0}


async def run_daily_reflections(
    conn_factory: Optional[Callable[[], Any]] = None,
    *,
    ai_caller: Any = None,
    sleep: Optional[Callable[[float], Any]] = None,
    now: Any = None,
    batch_size: Optional[int] = None,
    max_seconds: Optional[int] = None,
) -> dict[str, Any]:
    from ..database import get_db_connection

    factory = conn_factory or get_db_connection
    current = now or local_now()
    date = reflection_date(current)
    with factory() as conn:
        if not is_wanderer_runtime_enabled(conn) or not reflection_enabled(conn):
            return {"status": "disabled", "processed": 0, "applied": 0, "skipped": 0, "failed": 0, "ai_calls": 0}
        wanderers = list_wanderers(conn, statuses=[STATUS_ACTIVE])
        reflected = {str(row[0]) for row in conn.execute(
            "SELECT wanderer_id FROM wanderer_daily_reflections WHERE reflection_date = ? AND status IN ('applied','running')",
            (date,)).fetchall()}
        limit = int(batch_size or reflection_batch_size(conn))
        seconds = int(max_seconds or tick_max_seconds(conn))
    pending = [item for item in wanderers if str(item["id"]) not in reflected][:limit]
    started = time.monotonic()
    counts = {"applied": 0, "skipped": 0, "failed": 0, "skipped_capped": 0}
    total_calls = 0
    for index, wanderer in enumerate(pending):
        if time.monotonic() - started > seconds:
            break
        if index:
            await (sleep or asyncio.sleep)(1.2)
        ctx = ActionContext(factory, wanderer, ai_caller=ai_caller, sleep=sleep or asyncio.sleep,
                            now=current, special=False)
        result = await run_reflection(ctx)
        total_calls += int(result.get("ai_calls") or 0)
        status = str(result.get("status") or "failed")
        if status in counts:
            counts[status] += 1
        elif status == "skipped":
            counts["skipped"] += 1
        else:
            counts["failed"] += 1
        if ctx.capped:
            break
    return {"status": "ok", "date": date, "processed": len(pending), "ai_calls": total_calls, **counts}


async def run_reflection_now(wanderer_id: int, *, conn_factory: Optional[Callable[[], Any]] = None,
                             force: bool = True) -> dict[str, Any]:
    """Manual single-wanderer reflection (admin button / tests)."""
    from ..database import get_db_connection
    from .wanderer_identity_service import load_wanderer

    factory = conn_factory or get_db_connection
    with factory() as conn:
        wanderer = load_wanderer(conn, int(wanderer_id))
    if wanderer is None:
        raise LookupError("散修不存在")
    ctx = ActionContext(factory, wanderer, now=local_now(), special=False)
    return await run_reflection(ctx, force=force)


async def handle_wanderer_daily_reflection(task: dict[str, Any]) -> str:
    summary = await run_daily_reflections()
    return (f"wanderer reflection: status={summary.get('status')} processed={summary.get('processed')} "
            f"applied={summary.get('applied')} failed={summary.get('failed')} ai_calls={summary.get('ai_calls')}")
