"""散修 study, promotion requests and reflection (design §5.2 items 4-6).

* Study: pick an indexed material the wanderer may read — only materials that
  are themselves 完全公开 (``course_materials.scope_level='public'``) or whose
  outermost folder is — take 1-3 consecutive chunks (<= 6000 chars), and let
  one AI turn write "what I remember" (<= 800 字) into the knowledge base.
  Classroom-assigned (non-public) materials are never read.
* Promotion: when the knowledge base is >= 70% of the *current* tier's capacity
  (a wanderer can never hold more than its current capacity, so the design's
  "next tier" ratio is unreachable), with >= 5 interactions in 7 days and no
  request in the last 7 days, the persona decides whether to apply.
* Reflection: every unacknowledged 天道 verdict is read, reflected on (thread
  ``dao`` + a ``reflection`` knowledge entry) and acknowledged.
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
from .wanderer_identity_service import MAX_TIER, clamp_tier, invalidate_wanderer_cache, knowledge_capacity_chars
from .wanderer_knowledge_service import add_knowledge
from .wanderer_prompt_service import INJECTION_GUARD

READING_MAX_CHARS = 6000
READING_MAX_CHUNKS = 3
KNOWLEDGE_DIGEST_LIMIT = 800
PROMOTION_KNOWLEDGE_RATIO = 0.7
PROMOTION_MIN_INTERACTIONS = 5
PROMOTION_COOLDOWN_DAYS = 7
INTERACTION_KINDS = ("comment", "reply", "dm_reply", "classroom_message", "write_post")
REFLECTION_KINDS = ("promotion", "discipline", "admin_override", "revive")
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
                               title=title, digest=digest, tags=tags)
        bump_stats(conn, ctx.wanderer_id, {"knowledge_reads": 1})
        conn.commit()
    record_action(ctx, "knowledge_read", **target, content_preview=title, ai_calls=calls,
                  detail={"chars": len(digest), "pages": [_page_of(chunk) for chunk in chunks],
                          "forgotten": stored.get("forgotten") or []})
    ctx.reload()
    return [outcome("knowledge_read", ai_calls=calls)]


# ---------------------------------------------------------------------------
# Promotion request
# ---------------------------------------------------------------------------

def _recent_interactions(conn: Any, wanderer_id: int, since: str) -> int:
    marks = ",".join("?" for _ in INTERACTION_KINDS)
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM wanderer_action_log WHERE wanderer_id = ? AND outcome = 'ok' "
        f"AND action_kind IN ({marks}) AND created_at >= ?",
        (int(wanderer_id), *INTERACTION_KINDS, since),
    ).fetchone()
    return as_int(row["n"])


def promotion_eligible(ctx: ActionContext, conn: Any) -> bool:
    wanderer = ctx.wanderer
    tier = clamp_tier(wanderer.get("tier"))
    if not behavior_on(wanderer, "promotion") or as_int(wanderer.get("promotion_requested")) or tier >= MAX_TIER:
        return False
    if as_int(wanderer.get("knowledge_chars")) < PROMOTION_KNOWLEDGE_RATIO * knowledge_capacity_chars(tier):
        return False
    now = ctx.current_time()
    last = parse_datetime(wanderer.get("promotion_requested_at"))
    if last is not None and last.replace(tzinfo=None) > now - timedelta(days=PROMOTION_COOLDOWN_DAYS):
        return False
    since = (now - timedelta(days=7)).isoformat(timespec="seconds")
    return _recent_interactions(conn, ctx.wanderer_id, since) >= PROMOTION_MIN_INTERACTIONS


async def request_promotion(ctx: ActionContext) -> list[dict[str, Any]]:
    if ctx.capped:
        return []
    tier = clamp_tier(ctx.wanderer.get("tier"))
    capacity = knowledge_capacity_chars(tier)
    scene = {
        "kind": "升阶申请", "title": "要不要向天道申请升阶",
        "meta": f"你已固化知识 {as_int(ctx.wanderer.get('knowledge_chars'))} 字，当前境界容量 {capacity} 字。",
        "tags": list(ctx.wanderer.get("interests") or []),
        "task": "按你的性格决定是否向天道申请升阶：申请就用 reply，在 parts 里写一段申请理由（不超过 200 字）；不申请就 skip。",
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
