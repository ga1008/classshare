"""散修 study, promotion requests, reflection and the lifecycle actions (design §5.2 items 4-6).

* Study: pick an indexed material the wanderer may read — only materials that
  are themselves 完全公开 (``course_materials.scope_level='public'``) or whose
  outermost folder is — take 1-3 consecutive chunks (<= 6000 chars), and let
  one AI turn write "what I remember" (<= 800 字) into the knowledge base.
  Classroom-assigned (non-public) materials are never read. The gained
  cultivation is scaled by 悟性 (and 气运 may double it).
* Promotion: ``wanderer_life_service.promotion_requirements`` decides readiness
  (knowledge ratio by tier, absolute cultivation, 口才-adjusted interactions,
  7-day cooldown); the persona decides whether to apply.
* Reflection: every unacknowledged 天道 verdict is read, reflected on (thread
  ``dao`` + a ``reflection`` knowledge entry) and acknowledged.
* Lifecycle (docs/wanderer-lifecycle-2026-10-30.md): 延寿 when time runs out,
  抗辩 (tier ≥ 5) against the latest warning, 复活 (tier 10) of a dead peer.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Optional

from ..time_utils import local_iso, parse_datetime
from .wanderer_actions_common import (
    ActionContext,
    as_int,
    behavior_on,
    bump_stats,
    interest_score,
    outcome,
    record_action,
    remember,
    run_turn,
    turn_parts,
    weighted_choice,
)
from .wanderer_identity_service import (
    MAX_TIER,
    clamp_tier,
    current_life,
    invalidate_wanderer_cache,
    load_wanderer,
)
from .wanderer_knowledge_service import add_knowledge
from .wanderer_life_core import APPEAL_MIN_TIER, can_extend_life
from .wanderer_life_service import (
    INTERACTION_KINDS,
    can_resurrect,
    extend_life as apply_life_extension,
    life_extension_cost,
    promotion_requirements,
    resurrect,
)
from .wanderer_prompt_service import INJECTION_GUARD, extract_json_object

READING_MAX_CHARS = 6000
READING_MAX_CHUNKS = 3
KNOWLEDGE_DIGEST_LIMIT = 800
APPEAL_WARNING_WINDOW_DAYS = 7
APPEAL_COOLDOWN_DAYS = 30
REFLECTION_KINDS = ("promotion", "discipline", "admin_override", "revive", "appeal")
REFLECTIONS_PER_TICK = 2


# ---------------------------------------------------------------------------
# Study
# ---------------------------------------------------------------------------

def readable_materials(conn: Any, wanderer: dict[str, Any]) -> list[dict[str, Any]]:
    """Indexed materials a wanderer may read: public material or public outermost folder only."""
    from .material_search_schema import ensure_material_search_schema

    ensure_material_search_schema(conn)  # runtime-owned index; the scheduler may be first to touch it
    rows = conn.execute(
        """
        SELECT d.material_id, m.name AS material_name
        FROM material_search_documents d
        JOIN course_materials m ON m.id = d.material_id
        LEFT JOIN course_materials r ON r.id = m.root_id
        WHERE d.status = 'ready' AND d.chunk_count > 0
          AND (LOWER(COALESCE(m.scope_level, '')) = 'public'
               OR LOWER(COALESCE(r.scope_level, '')) = 'public')
        ORDER BY d.material_id DESC
        LIMIT 200
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _read_refs(conn: Any, wanderer_id: int, material_id: int) -> set[str]:
    rows = conn.execute(
        "SELECT source_ref FROM wanderer_knowledge_entries WHERE wanderer_id = ? AND source_kind = 'material_page' "
        "AND status = 'active' AND source_ref LIKE ?",
        (int(wanderer_id), f"{int(material_id)}:%"),
    ).fetchall()
    return {str(row["source_ref"]) for row in rows}


def _page_of(chunk: dict[str, Any]) -> int:
    return as_int(chunk.get("page_number")) or as_int(chunk.get("ordinal"))


def _chunk_window(chunks: list[dict[str, Any]], start: int) -> list[dict[str, Any]]:
    window: list[dict[str, Any]] = []
    total = 0
    for chunk in chunks[start:start + READING_MAX_CHUNKS]:
        size = len(str(chunk.get("text") or ""))
        if window and total + size > READING_MAX_CHARS:
            break
        window.append(chunk)
        total += size
    return window


def pick_reading(ctx: ActionContext, conn: Any) -> Optional[dict[str, Any]]:
    """``{"material", "chunks"}`` for an unread passage, weighted by interest overlap."""
    materials = readable_materials(conn, ctx.wanderer)
    if not materials:
        return None
    interests = ctx.wanderer.get("interests") or []
    material = weighted_choice(ctx.rng, materials,
                               [1.0 + interest_score(interests, item.get("material_name")) for item in materials])
    chunks = [dict(row) for row in conn.execute(
        "SELECT ordinal, page_number, locator_label, heading, text FROM material_search_chunks "
        "WHERE material_id = ? ORDER BY ordinal",
        (int(material["material_id"]),),
    ).fetchall()]
    read = _read_refs(conn, ctx.wanderer_id, int(material["material_id"]))
    starts = [index for index, chunk in enumerate(chunks)
              if f"{material['material_id']}:{_page_of(chunk)}" not in read and str(chunk.get("text") or "").strip()]
    if not starts:
        return None
    start = weighted_choice(ctx.rng, starts, [
        1.0 + interest_score(interests, chunks[index].get("heading"), str(chunks[index].get("text") or "")[:500])
        for index in starts
    ])
    return {"material": material, "chunks": _chunk_window(chunks, start)}


def _reading_text(chunks: list[dict[str, Any]]) -> str:
    pieces = []
    for chunk in chunks:
        label = str(chunk.get("locator_label") or chunk.get("heading") or "")
        pieces.append(f"【{label}】\n{chunk.get('text') or ''}" if label else str(chunk.get("text") or ""))
    return "\n\n".join(pieces)[:READING_MAX_CHARS]


def _knowledge_tags(interests: list[Any], *texts: Any) -> list[str]:
    haystack = " ".join(str(text or "") for text in texts).lower()
    return [str(tag) for tag in interests if str(tag).strip() and str(tag).lower() in haystack]


def _study_scene(material: dict[str, Any], first: dict[str, Any], interests: list[Any]) -> dict[str, Any]:
    return {
        "kind": "研读学习文档",
        "title": f"{material.get('material_name') or '学习文档'} · {first.get('locator_label') or ''}",
        "meta": "你在自学，没有人在看你。",
        "tags": [*interests, str(first.get("heading") or "")],
        "task": "固化知识：读完用户消息里的文档片段后，用你自己的话写下你记住的要点（合计不超过 800 字），"
                "分成 2-3 条放进 parts（action 用 multi 或 reply）；实在读不进去就 skip。",
    }


async def study_material(ctx: ActionContext) -> list[dict[str, Any]]:
    if not behavior_on(ctx.wanderer, "knowledge_read") or ctx.capped:
        return []
    with ctx.connect() as conn:
        reading = pick_reading(ctx, conn)
    if reading is None:
        record_action(ctx, "skip", target_type="material", outcome="skipped", detail={"reason": "no_readable_material"})
        return [outcome("skip", "skipped")]
    material, chunks = reading["material"], reading["chunks"]
    first = chunks[0]
    interests = list(ctx.wanderer.get("interests") or [])
    message = f"{INJECTION_GUARD}\n<<<文档开始>>>\n{_reading_text(chunks)}\n<<<文档结束>>>"
    result = await run_turn(ctx, scene=_study_scene(material, first, interests), thread_key="study",
                            suffix="knowledge_read", user_message=message)
    calls = int(result.get("ai_calls") or 0)
    parts = turn_parts(ctx, result.get("turn") or {}, public=False) if result.get("status") == "ok" else []
    target = {"target_type": "material", "target_id": f"{material['material_id']}:{_page_of(first)}"}
    if not parts or (result.get("turn") or {}).get("action") == "skip":
        record_action(ctx, "knowledge_read", **target, ai_calls=calls, outcome="skipped",
                      detail={"reason": result.get("status") or "skip"})
        return [outcome("knowledge_read", "skipped", ai_calls=calls)]
    digest = "\n".join(parts)[:KNOWLEDGE_DIGEST_LIMIT]
    title = str(first.get("heading") or first.get("locator_label") or material.get("material_name") or "")
    tags = _knowledge_tags(interests, first.get("heading"), material.get("material_name"), digest)
    with ctx.connect() as conn:
        stored = add_knowledge(conn, ctx.wanderer, source_kind="material_page", source_ref=target["target_id"],
                               title=title, digest=digest, tags=tags, rng=ctx.rng.random)
        from .wanderer_episodic_memory_service import record_memory
        record_memory(conn, ctx.wanderer, kind="knowledge", title=f"读了《{title or '一份材料'}》",
                      content=digest[:1200], importance=0.58, tags=tags,
                      source_kind="knowledge", source_ref=str(target["target_id"]))
        bump_stats(conn, ctx.wanderer_id, {"knowledge_reads": 1})
        conn.commit()
    record_action(ctx, "knowledge_read", **target, content_preview=title, ai_calls=calls,
                  detail={"chars": len(digest), "pages": [_page_of(chunk) for chunk in chunks],
                          "forgotten": stored.get("forgotten") or [],
                          "score_delta": stored.get("score_delta"), "crit": bool(stored.get("crit"))})
    ctx.reload()
    return [outcome("knowledge_read", ai_calls=calls)]


# ---------------------------------------------------------------------------
# Promotion request
# ---------------------------------------------------------------------------

def promotion_eligible(ctx: ActionContext, conn: Any) -> bool:
    wanderer = ctx.wanderer
    if (not behavior_on(wanderer, "promotion") or as_int(wanderer.get("promotion_requested"))
            or clamp_tier(wanderer.get("tier")) >= MAX_TIER):
        return False
    return bool(promotion_requirements(conn, wanderer, now=ctx.current_time()).get("ready"))


async def request_promotion(ctx: ActionContext) -> list[dict[str, Any]]:
    if ctx.capped:
        return []
    with ctx.connect() as conn:
        requirements = promotion_requirements(conn, ctx.wanderer, now=ctx.current_time())
    scene = {
        "kind": "升阶申请", "title": "要不要向天道申请升阶",
        "meta": (f"知识 {requirements['knowledge']} / {requirements['knowledge_target']} 字；"
                 f"修为 {requirements['cultivation']} / {requirements['cultivation_target']}；"
                 f"近七日互动 {requirements['interactions']} / {requirements['interactions_needed']} 次。"),
        "tags": list(ctx.wanderer.get("interests") or []),
        "task": "条件已满足。按你的性格决定是否向天道申请升阶：申请就用 reply，在 parts 里写一段申请理由（不超过 200 字）；"
                "不想晋升就 skip。你也可以选择一直不申请。",
    }
    result = await run_turn(ctx, scene=scene, thread_key="dao", suffix="request_promotion")
    calls = int(result.get("ai_calls") or 0)
    turn = result.get("turn") or {}
    parts = turn_parts(ctx, turn, public=False)
    if turn.get("action") == "skip" or not parts:
        record_action(ctx, "request_promotion", target_type="dao", ai_calls=calls, outcome="skipped",
                      detail={"reason": result.get("status") or "declined"})
        return [outcome("request_promotion", "skipped", ai_calls=calls)]
    note = "\n".join(parts)[:500]
    now = local_iso()
    with ctx.connect() as conn:
        conn.execute(
            "UPDATE wanderer_agents SET promotion_requested = 1, promotion_requested_at = ?, promotion_note = ?, "
            "updated_at = ? WHERE id = ?",
            (now, note, now, ctx.wanderer_id),
        )
        conn.commit()
    invalidate_wanderer_cache([ctx.student_id])
    record_action(ctx, "request_promotion", target_type="dao", content_preview=note, ai_calls=calls,
                  mood=str(turn.get("mood") or ""), detail={"tier": tier})
    await remember(ctx, "dao", said=[note])
    ctx.reload()
    return [outcome("request_promotion", ai_calls=calls)]


# ---------------------------------------------------------------------------
# Reflection on 天道 verdicts
# ---------------------------------------------------------------------------

def pending_reviews(conn: Any, wanderer_id: int) -> list[dict[str, Any]]:
    marks = ",".join("?" for _ in REFLECTION_KINDS)
    rows = conn.execute(
        "SELECT id, review_kind, verdict, reason, advice, from_tier, to_tier FROM wanderer_dao_reviews "
        f"WHERE wanderer_id = ? AND acknowledged_at IS NULL AND review_kind IN ({marks}) ORDER BY id LIMIT ?",
        (int(wanderer_id), *REFLECTION_KINDS, REFLECTIONS_PER_TICK),
    ).fetchall()
    return [dict(row) for row in rows]


DAO_NOTIFICATION_CATEGORY = "wanderer_dao"


def _acknowledge(ctx: ActionContext, review_id: int) -> None:
    """Acknowledge the verdict and mark its unread 天道 inbox row(s) as read."""
    now = local_iso()
    with ctx.connect() as conn:
        conn.execute("UPDATE wanderer_dao_reviews SET acknowledged_at = ? WHERE id = ? AND acknowledged_at IS NULL",
                     (now, int(review_id)))
        conn.execute(
            "UPDATE message_center_notifications SET read_at = ? WHERE recipient_identity = ? AND category = ? "
            "AND ref_type = 'wanderer_dao_review' AND ref_id = ? AND read_at IS NULL",
            (now, ctx.identity, DAO_NOTIFICATION_CATEGORY, str(int(review_id))),
        )
        conn.commit()


def _verdict_text(review: dict[str, Any]) -> str:
    text = f"{review.get('review_kind')}/{review.get('verdict')}：{review.get('reason') or ''}"
    return f"{text}；建议：{review['advice']}" if review.get("advice") else text


async def _reflect_once(ctx: ActionContext, review: dict[str, Any]) -> dict[str, Any]:
    verdict_text = _verdict_text(review)
    scene = {
        "kind": "读到天道裁决", "title": "天道的裁决", "body": verdict_text, "tags": ["天道", "反思"],
        "task": "用第一人称写一段内心反思（不超过 200 字）放进 parts（reply）：你怎么看这个裁决、之后打算怎么做。",
    }
    result = await run_turn(ctx, scene=scene, thread_key="dao", suffix="reflect")
    if result.get("status") == "capped":
        return outcome("reflect", "skipped")
    calls = int(result.get("ai_calls") or 0)
    parts = turn_parts(ctx, result.get("turn") or {}, public=False)
    _acknowledge(ctx, int(review["id"]))
    target = {"target_type": "dao_review", "target_id": review["id"]}
    if not parts:
        record_action(ctx, "reflect", **target, ai_calls=calls, outcome="skipped", detail={"reason": "no_reflection"})
        return outcome("reflect", "skipped", ai_calls=calls)
    text = "\n".join(parts)[:KNOWLEDGE_DIGEST_LIMIT]
    with ctx.connect() as conn:
        add_knowledge(conn, ctx.wanderer, source_kind="reflection", source_ref=f"review:{review['id']}",
                      title=f"天道裁决反思·{review.get('verdict') or ''}", digest=text, tags=["天道", "反思"])
        conn.commit()
    await remember(ctx, "dao", heard=("天道", verdict_text), said=[text])
    record_action(ctx, "reflect", **target, content_preview=text, ai_calls=calls,
                  detail={"verdict": review.get("verdict"), "kind": review.get("review_kind")})
    return outcome("reflect", ai_calls=calls)


async def reflect_on_reviews(ctx: ActionContext) -> list[dict[str, Any]]:
    """Runs before any other action; a capped day leaves verdicts pending for tomorrow."""
    with ctx.connect() as conn:
        reviews = pending_reviews(conn, ctx.wanderer_id)
    done = []
    for review in reviews:
        if ctx.capped:
            break
        done.append(await _reflect_once(ctx, review))
    if done:
        ctx.reload()
    return done


# ---------------------------------------------------------------------------
# 寿元延寿 / 抗辩 / 复活 (lifecycle doc §2.2, §4.4, §6)
# ---------------------------------------------------------------------------

async def maybe_extend_life(ctx: ActionContext) -> list[dict[str, Any]]:
    """When time is short, the persona decides whether to burn cultivation for a week."""
    life = current_life(ctx.wanderer)
    if not can_extend_life(life, ctx.wanderer.get("cultivation_score")):
        return []
    cost = life_extension_cost(ctx.wanderer.get("cultivation_score"))
    scene = {
        "kind": "寿元将尽", "title": "还要不要继续活下去",
        "meta": (f"你只剩约 {life['remaining_days']} 天寿元，本世已延寿 {life['extensions']} 次。"
                 f"燃烧当前修为（约 {cost} 点）可以换一周寿命，也可以把钱花在修行上赌一次突破。"),
        "tags": ["寿元", "选择"],
        "task": "按你的性格和处境决定：延寿（extend）还是拒绝（refuse），只输出 JSON。",
        "output_contract": '只输出 JSON：{"decision":"extend|refuse","reason":"你自己的理由"}。',
    }
    result = await run_turn(ctx, scene=scene, thread_key="life", suffix="extend_life")
    calls = int(result.get("ai_calls") or 0)
    parsed = extract_json_object(result.get("raw_text")) or {}
    decision = str(parsed.get("decision") or "").strip().lower()
    reason = str(parsed.get("reason") or "").strip()[:300]
    if result.get("status") != "ok" or decision not in {"extend", "refuse"}:
        record_action(ctx, "extend_life", target_type="life", ai_calls=calls, outcome="skipped",
                      detail={"reason": result.get("status") or "unparsable", "decision": decision})
        return [outcome("extend_life", "skipped", ai_calls=calls)]
    if decision == "refuse":
        record_action(ctx, "extend_life", target_type="life", ai_calls=calls, outcome="skipped",
                      detail={"decision": "refuse", "reason": reason})
        return [outcome("extend_life", "skipped", ai_calls=calls)]
    with ctx.connect() as conn:
        fresh = load_wanderer(conn, ctx.wanderer_id) or ctx.wanderer
        applied = apply_life_extension(conn, fresh)
        conn.commit()
    record_action(ctx, "extend_life", target_type="life", ai_calls=calls,
                  outcome="ok" if applied.get("ok") else "skipped",
                  detail={**applied, "reason": reason})
    ctx.reload()
    return [outcome("extend_life", "ok" if applied.get("ok") else "skipped", ai_calls=calls)]


def appeal_eligible(conn: Any, wanderer: dict[str, Any], now: Any = None) -> bool:
    if clamp_tier(wanderer.get("tier")) < APPEAL_MIN_TIER:
        return False
    if as_int(wanderer.get("warnings_count")) <= 0 or as_int(wanderer.get("appeal_requested")):
        return False
    moment = now or ctx_now()
    warned_at = parse_datetime(wanderer.get("last_warning_at"))
    if warned_at is None:
        return False
    warned_naive = warned_at.replace(tzinfo=None) if warned_at.tzinfo else warned_at
    if warned_naive < moment - timedelta(days=APPEAL_WARNING_WINDOW_DAYS):
        return False
    last = parse_datetime(wanderer.get("last_appeal_at"))
    if last is None:
        return True
    last_naive = last.replace(tzinfo=None) if last.tzinfo else last
    return last_naive <= moment - timedelta(days=APPEAL_COOLDOWN_DAYS)


def ctx_now() -> Any:
    from ..time_utils import local_now

    return local_now()


async def request_appeal(ctx: ActionContext) -> list[dict[str, Any]]:
    """Tier ≥ 5: contest the most recent warning; 天道 reviews it in the daily audit."""
    with ctx.connect() as conn:
        if not appeal_eligible(conn, ctx.wanderer, ctx.current_time()):
            return []
        row = conn.execute(
            "SELECT reason FROM wanderer_dao_reviews WHERE wanderer_id = ? AND verdict = 'warning' "
            "ORDER BY id DESC LIMIT 1", (ctx.wanderer_id,),
        ).fetchone()
        warning = str(row["reason"] or "") if row is not None else "你最近被天道警告过。"
    if ctx.capped:
        return []
    scene = {
        "kind": "天道抗辩", "title": "要不要为自己辩白",
        "body": warning[:600], "tags": ["天道", "抗辩"],
        "task": "你对这次警告不服，可以写一段抗辩（不超过 200 字）；也可以认罚不辩。只输出 JSON。",
        "output_contract": '只输出 JSON：{"decision":"appeal|accept","reason":"你的抗辩或认罚理由"}。',
    }
    result = await run_turn(ctx, scene=scene, thread_key="dao", suffix="appeal")
    calls = int(result.get("ai_calls") or 0)
    parsed = extract_json_object(result.get("raw_text")) or {}
    decision = str(parsed.get("decision") or "").strip().lower()
    note = str(parsed.get("reason") or "").strip()[:300]
    if result.get("status") != "ok" or decision not in {"appeal", "accept"}:
        record_action(ctx, "appeal", target_type="dao", ai_calls=calls, outcome="skipped",
                      detail={"reason": result.get("status") or "unparsable"})
        return [outcome("appeal", "skipped", ai_calls=calls)]
    if decision == "accept" or not note:
        record_action(ctx, "appeal", target_type="dao", ai_calls=calls, outcome="skipped",
                      detail={"decision": "accept", "reason": note})
        return [outcome("appeal", "skipped", ai_calls=calls)]
    now = local_iso()
    with ctx.connect() as conn:
        conn.execute(
            "UPDATE wanderer_agents SET appeal_requested = 1, appeal_requested_at = ?, appeal_note = ?, "
            "last_appeal_at = ?, updated_at = ? WHERE id = ?",
            (now, note, now, now, ctx.wanderer_id),
        )
        conn.commit()
    invalidate_wanderer_cache([ctx.student_id])
    record_action(ctx, "appeal", target_type="dao", content_preview=note, ai_calls=calls, detail={"submitted": True})
    await remember(ctx, "dao", said=[note])
    ctx.reload()
    return [outcome("appeal", ai_calls=calls)]


async def try_resurrect(ctx: ActionContext) -> list[dict[str, Any]]:
    """Tier 10: spend 8 weeks of your own life to bring one dead wanderer back."""
    with ctx.connect() as conn:
        check = can_resurrect(conn, ctx.wanderer)
    if not check.get("ok") or ctx.capped:
        return []
    candidates = (check.get("candidates") or [])[:5]
    table = "\n".join(
        f"- id={item['id']} {item['display_name']}（{item['archetype']}，第 {item['final_tier']} 境，"
        f"卒于 {item['died_at'] or '不详'}，你的好感 {item['affinity']:+g}）"
        for item in candidates
    )
    scene = {
        "kind": "复活同修", "title": "要不要救回一位陨落的同修",
        "body": table,
        "meta": f"复活要消耗你自己 {check['cost_weeks']:g} 周寿元。",
        "tags": ["复活", "同修", "寿元"],
        "task": "按你的性格决定救不救、救谁；不想救就选 none。只输出 JSON。",
        "output_contract": '只输出 JSON：{"resurrect": 散修id 或 "none", "reason":"你的理由"}。',
    }
    result = await run_turn(ctx, scene=scene, thread_key="dao", suffix="resurrect")
    calls = int(result.get("ai_calls") or 0)
    parsed = extract_json_object(result.get("raw_text")) or {}
    raw = str(parsed.get("resurrect") or "").strip().lower()
    reason = str(parsed.get("reason") or "").strip()[:300]
    if result.get("status") != "ok" or not raw:
        record_action(ctx, "resurrect", target_type="dao", ai_calls=calls, outcome="skipped",
                      detail={"reason": result.get("status") or "unparsable"})
        return [outcome("resurrect", "skipped", ai_calls=calls)]
    if raw == "none":
        record_action(ctx, "resurrect", target_type="dao", ai_calls=calls, outcome="skipped",
                      detail={"decision": "none", "reason": reason})
        return [outcome("resurrect", "skipped", ai_calls=calls)]
    valid_ids = {int(item["id"]) for item in candidates}
    try:
        target_id = int(raw)
    except (TypeError, ValueError):
        target_id = 0
    if target_id not in valid_ids:
        record_action(ctx, "resurrect", target_type="dao", ai_calls=calls, outcome="failed",
                      detail={"reason": "unknown target", "target": raw})
        return [outcome("resurrect", "failed", ai_calls=calls)]
    with ctx.connect() as conn:
        target = load_wanderer(conn, target_id)
        fresh_reviver = load_wanderer(conn, ctx.wanderer_id) or ctx.wanderer
        if target is None:
            record_action(ctx, "resurrect", target_type="dao", target_id=target_id, ai_calls=calls,
                          outcome="failed", detail={"reason": "target vanished"})
            return [outcome("resurrect", "failed", ai_calls=calls)]
        try:
            result_payload = resurrect(conn, fresh_reviver, target, reason=reason, now=ctx.current_time())
            conn.commit()
        except ValueError as exc:
            conn.rollback()
            record_action(ctx, "resurrect", target_type="dao", target_id=target_id, ai_calls=calls,
                          outcome="failed", detail={"reason": str(exc)[:200]})
            return [outcome("resurrect", "failed", ai_calls=calls)]
    record_action(ctx, "resurrect", target_type="wanderer", target_id=target_id, content_preview=reason,
                  ai_calls=calls, detail=result_payload)
    ctx.reload()
    return [outcome("resurrect", ai_calls=calls, target=result_payload.get("target"))]
