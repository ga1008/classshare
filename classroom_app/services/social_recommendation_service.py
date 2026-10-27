"""好友推荐 (social doc §12.2): daily ranked pairs per scope bucket, served per 境界.

Daily task ``social_recommendations_daily`` (also ``run_recommendations_now``
for QA / super admin):

1. ``social_trait_service.refresh_trait_cards`` — only changed sources re-extracted.
2. Buckets: ``class:<id>``, ``course:<normalised name>``, ``college:<name>``,
   ``school:<code>``, ``platform`` (incl. 散修). A bucket whose members/cards
   did not change is skipped. ≤ 120 members → one deep AI call over anonymous
   numeric ids + trait cards (complement Top 8, similar Top 8 per person);
   larger buckets, or when the daily AI cap is spent, are scored by the backend
   (similar = cosine of centred trait vectors + interest overlap; complement =
   social-dimension distance, halved without shared interests).
3. Pairs land in ``social_recommendation_pairs``; a failed AI call keeps
   yesterday's rows for that bucket.

Serving merges the buckets my tier allows, drops self / friends / pending /
blocked either way / declined cool-down / out-of-scope people, and returns the
top 5 of each kind. The only explanation ever shown is one of the two fixed
hints below; trait data never leaves the backend.
"""
from __future__ import annotations

import hashlib
import math
import random
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable, Optional

from .ai_model_policy import AI_TASK_DEEP_TEXT
from .social_common import as_int, dumps, ensure_schema, env_int, in_clause, user_identity, utc_date
from .social_trait_service import TRAIT_KEYS, load_cards, refresh_trait_cards

TASK_KIND = "social_recommendations_daily"
COMPLEMENT = "complement"
SIMILAR = "similar"
HINTS = {COMPLEMENT: "你们擅长的方向正好互补", SIMILAR: "你们的兴趣和节奏很接近"}
SERVE_LIMIT = 5
STORE_LIMIT = 8
AI_BUCKET_MAX = 120
BACKEND_SAMPLE = 300
DEFAULT_DAILY_AI_CAP = 60
AI_CALLS_KEY = "rec_ai_calls"

METHOD_AI = "ai"
METHOD_BACKEND = "backend"            # large bucket: backend scoring is the method
METHOD_FALLBACK = "backend_fallback"  # small bucket scored without AI (cap spent / AI failed)
METHOD_EMPTY = "empty"

RANK_SYSTEM_PROMPT = (
    "你是学习社区的交友推荐助手。下面是一组成员，每人只有匿名编号与结构化特质卡"
    "（五个 0..1 维度、学习风格、兴趣标签）。请为每位成员各挑出最多 8 位："
    "complement＝擅长方向或性格互补、能互相带动的人；similar＝兴趣与学习节奏接近的人。"
    "不能选自己，编号必须来自名单。只输出 JSON："
    '{"items":[{"id":1,"complement":[2,3],"similar":[4]}]}'
)


def daily_ai_cap() -> int:
    return env_int("SOCIAL_REC_DAILY_AI_CAP", DEFAULT_DAILY_AI_CAP, low=0, high=10_000)


# ---------------------------------------------------------------------------
# Buckets
# ---------------------------------------------------------------------------

def _bucket_members(conn: Any, identities: Iterable[str]) -> dict[str, list[str]]:
    from .social_scope_service import normalize_course_name, student_course_names, target_facts

    wanted = sorted(set(identities))
    buckets: dict[str, list[str]] = {"platform": list(wanted)}
    for start in range(0, len(wanted), 500):
        chunk = wanted[start:start + 500]
        facts = target_facts(conn, chunk)
        courses = student_course_names(conn, [int(item.split(":")[1]) for item in chunk])
        for identity in chunk:
            item = facts.get(identity)
            if not item or item.get("kind") != "student" or item.get("is_wanderer"):
                continue
            keys = []
            if item["class_id"]:
                keys.append(f"class:{item['class_id']}")
            names = courses.get(int(identity.split(":")[1]), [])
            keys += sorted({f"course:{key}" for key in (normalize_course_name(name) for name in names) if key})
            if item["college"] and item["school_code"]:
                keys.append(f"college:{item['school_code']}:{item['college']}")
            if item["school_code"]:
                keys.append(f"school:{item['school_code']}")
            for key in keys:
                buckets.setdefault(key, []).append(identity)
    return buckets


def _bucket_order(key: str) -> int:
    return {"class": 0, "course": 1, "college": 2, "school": 3, "platform": 4}.get(key.split(":", 1)[0], 9)


def _members_hash(members: list[str], cards: dict[str, dict[str, Any]]) -> str:
    text = "\n".join(f"{identity}={cards[identity]['source_hash']}" for identity in sorted(members))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Backend scoring
# ---------------------------------------------------------------------------

def _vector(card: dict[str, Any]) -> list[float]:
    return [float(card["traits"].get(key, 0.5)) - 0.5 for key in TRAIT_KEYS]


def _cosine(left: list[float], right: list[float]) -> float:
    norm = math.sqrt(sum(x * x for x in left)) * math.sqrt(sum(y * y for y in right))
    return sum(x * y for x, y in zip(left, right)) / norm if norm else 0.0


def _overlap(left: dict[str, Any], right: dict[str, Any]) -> float:
    a, b = set(left.get("interests") or []), set(right.get("interests") or [])
    return len(a & b) / len(a | b) if a and b else 0.0


def similar_score(left: dict[str, Any], right: dict[str, Any]) -> float:
    return _cosine(_vector(left), _vector(right)) + 0.5 * _overlap(left, right)


def complement_score(left: dict[str, Any], right: dict[str, Any]) -> float:
    a, b = left["traits"], right["traits"]
    social = (abs(a["extraversion"] - b["extraversion"]) + abs(a["conscientiousness"] - b["conscientiousness"])
              + abs(a["openness"] - b["openness"])) / 3
    return social * (1.0 if _overlap(left, right) > 0 else 0.5)


def backend_rank(members: list[str], cards: dict[str, dict[str, Any]], *,
                 seed: str = "") -> dict[str, dict[str, list[str]]]:
    """``{identity: {complement: [...], similar: [...]}}`` (Top 8 each), sampling large buckets."""
    ranked: dict[str, dict[str, list[str]]] = {}
    for identity in members:
        others = [item for item in members if item != identity]
        if len(others) > BACKEND_SAMPLE:
            others = random.Random(f"{seed}:{identity}").sample(others, BACKEND_SAMPLE)
        mine = cards[identity]
        ranked[identity] = {
            SIMILAR: sorted(others, key=lambda other: (-similar_score(mine, cards[other]), other))[:STORE_LIMIT],
            COMPLEMENT: sorted(others, key=lambda other: (-complement_score(mine, cards[other]), other))[:STORE_LIMIT],
        }
    return ranked


def _parse_ai_rank(parsed: Any, numbered: dict[int, str]) -> dict[str, dict[str, list[str]]]:
    ranked: dict[str, dict[str, list[str]]] = {}
    items = parsed.get("items") if isinstance(parsed, dict) else None
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or as_int(item.get("id")) not in numbered:
            continue
        identity = numbered[as_int(item.get("id"))]
        entry: dict[str, list[str]] = {}
        for kind in (COMPLEMENT, SIMILAR):
            values = item.get(kind) if isinstance(item.get(kind), list) else []
            picks = [numbered[as_int(value)] for value in values if as_int(value) in numbered]
            entry[kind] = [pick for pick in dict.fromkeys(picks) if pick != identity][:STORE_LIMIT]
        ranked[identity] = entry
    return ranked


async def _ai_rank(key: str, members: list[str], cards: dict[str, dict[str, Any]],
                   ai_caller: Any) -> dict[str, dict[str, list[str]]]:
    from .social_ai_service import call_json

    shuffled = list(members)
    random.Random(f"{utc_date()}:{key}").shuffle(shuffled)
    numbered = {index + 1: identity for index, identity in enumerate(shuffled)}
    message = dumps({"members": [{"id": number, "traits": cards[identity]["traits"],
                                  "learning_style": cards[identity]["learning_style"],
                                  "interests": cards[identity]["interests"]}
                                 for number, identity in numbered.items()]})
    parsed = await call_json(system_prompt=RANK_SYSTEM_PROMPT, user_message=message, task_type=AI_TASK_DEEP_TEXT,
                             task_label=f"social:recs:{key.split(':', 1)[0]}",
                             gateway_task_type="social_recommendations", ai_caller=ai_caller)
    return _parse_ai_rank(parsed, numbered)


def _store_pairs(conn: Any, key: str, ranked: dict[str, dict[str, list[str]]], *, members_hash: str,
                 member_count: int, method: str) -> int:
    today = utc_date()
    conn.execute("DELETE FROM social_recommendation_pairs WHERE scope_key = ?", (key,))
    rows = [(key, identity, candidate, kind, rank, today)
            for identity, entry in ranked.items() for kind in (COMPLEMENT, SIMILAR)
            for rank, candidate in enumerate(entry.get(kind) or [], start=1)]
    if rows:
        conn.executemany(
            "INSERT INTO social_recommendation_pairs (scope_key, identity, candidate_identity, kind, rank, computed_on) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (scope_key, identity, candidate_identity, kind) DO NOTHING", rows)
    conn.execute(
        "INSERT INTO social_recommendation_buckets (scope_key, members_hash, member_count, method, computed_on) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT (scope_key) DO UPDATE SET members_hash = excluded.members_hash, "
        "member_count = excluded.member_count, method = excluded.method, computed_on = excluded.computed_on",
        (key, members_hash, member_count, method, today),
    )
    return len(rows)


# ---------------------------------------------------------------------------
# Daily run
# ---------------------------------------------------------------------------

def _default_factory() -> Any:
    from ..database import get_db_connection

    return get_db_connection()


def _load_state(factory: Callable[[], Any]) -> tuple[dict, dict, dict]:
    with factory() as conn:
        ensure_schema(conn)
        cards = load_cards(conn)
        buckets = _bucket_members(conn, cards.keys())
        stored = {str(row["scope_key"]): (str(row["members_hash"]), str(row["method"]))
                  for row in conn.execute("SELECT scope_key, members_hash, method FROM social_recommendation_buckets")}
        for key in [key for key in stored if key not in buckets]:  # vanished buckets stop recommending
            conn.execute("DELETE FROM social_recommendation_pairs WHERE scope_key = ?", (key,))
            conn.execute("DELETE FROM social_recommendation_buckets WHERE scope_key = ?", (key,))
        conn.commit()
    return cards, buckets, stored


async def run_recommendations_now(conn_factory: Optional[Callable[[], Any]] = None, *,
                                  ai_caller: Any = None) -> dict[str, Any]:
    """The whole daily pipeline; safe to re-run (unchanged buckets are skipped)."""
    from .social_ai_service import counter_value, reserve

    factory = conn_factory or _default_factory
    cap = daily_ai_cap()

    def reserve_call() -> bool:
        with factory() as conn:
            ok = reserve(conn, AI_CALLS_KEY, cap)
            conn.commit()
        return ok

    def budget_left() -> bool:
        with factory() as conn:
            return counter_value(conn, AI_CALLS_KEY) < cap

    traits = await refresh_trait_cards(factory, reserve_call=reserve_call, ai_caller=ai_caller)
    cards, buckets, stored = _load_state(factory)
    summary: dict[str, Any] = {"traits": traits, "buckets": len(buckets), "skipped": 0, "ai": 0, "backend": 0,
                               "fallback": 0, "kept": 0, "pairs": 0}
    for key in sorted(buckets, key=lambda item: (_bucket_order(item), item)):
        members = sorted(buckets[key])
        digest = _members_hash(members, cards)
        previous = stored.get(key)
        small = 2 <= len(members) <= AI_BUCKET_MAX
        unchanged = bool(previous and previous[0] == digest)
        if unchanged and not (previous[1] == METHOD_FALLBACK and small and budget_left()):
            summary["skipped"] += 1
            continue
        method, ranked = METHOD_EMPTY, {}
        if small and reserve_call():
            ranked = await _ai_rank(key, members, cards, ai_caller)
            if ranked:
                method = METHOD_AI
                missing = [identity for identity in members if identity not in ranked]
                if missing:
                    filled = backend_rank(members, cards, seed=utc_date())
                    ranked.update({identity: filled[identity] for identity in missing})
            elif previous:
                summary["kept"] += 1  # AI failed: keep yesterday's rows, retry tomorrow
                continue
        if not ranked and len(members) >= 2:
            ranked = backend_rank(members, cards, seed=utc_date())
            method = METHOD_BACKEND if len(members) > AI_BUCKET_MAX else METHOD_FALLBACK
        with factory() as conn:
            summary["pairs"] += _store_pairs(conn, key, ranked, members_hash=digest, member_count=len(members),
                                             method=method)
            conn.commit()
        summary[{METHOD_AI: "ai", METHOD_BACKEND: "backend"}.get(method, "fallback")] += 1
    with factory() as conn:
        summary["ai_calls_today"] = counter_value(conn, AI_CALLS_KEY)
    summary["ai_cap"] = cap
    print(f"[SOCIAL] recommendations: buckets={summary['buckets']} ai={summary['ai']} backend={summary['backend']} "
          f"fallback={summary['fallback']} skipped={summary['skipped']} pairs={summary['pairs']}")
    return summary


async def handle_social_recommendations_daily(task: dict[str, Any]) -> str:
    summary = await run_recommendations_now()
    return (f"social recommendations: buckets={summary['buckets']} ai={summary['ai']} "
            f"backend={summary['backend']} fallback={summary['fallback']} skipped={summary['skipped']}")


def ensure_social_recommendation_task(conn: Any) -> None:
    """Arm the daily run at 03:40 local time (idempotent)."""
    from .scheduled_task_service import schedule_task

    now = datetime.now()
    first = now.replace(hour=3, minute=40, second=0, microsecond=0)
    if first <= now:
        first += timedelta(days=1)
    schedule_task(conn, task_kind=TASK_KIND, run_at=first, payload={}, dedupe_key="social:recommendations-daily",
                  recurrence_seconds=86400, title="好友推荐每日计算", priority=95, max_attempts=2, replace=False)


# ---------------------------------------------------------------------------
# Serving
# ---------------------------------------------------------------------------

def excluded_identities(conn: Any, me: str) -> set[str]:
    """Self, blocked either way, friends, pending either way, and my declined requests still cooling down."""
    from .social_common import blocked_by, blockers_of, parse_iso, utc_now
    from .social_relations import STATUS_ACCEPTED, STATUS_DECLINED, STATUS_PENDING, friendship_rows, other_side

    excluded = {me} | blocked_by(conn, me) | blockers_of(conn, me)
    for row in friendship_rows(conn, me, statuses=(STATUS_PENDING, STATUS_ACCEPTED, STATUS_DECLINED)):
        if row["status"] != STATUS_DECLINED:
            excluded.add(other_side(row, me))
            continue
        until = parse_iso(row.get("declined_until"))
        if row["requester_identity"] == me and until and until > utc_now():
            excluded.add(other_side(row, me))
    return excluded


def _allowed_keys(ctx: Any) -> list[str]:
    keys: list[str] = []
    if ctx.tier >= 1 and ctx.class_id:
        keys.append(f"class:{ctx.class_id}")
    if ctx.tier >= 2:
        keys += [f"course:{key}" for key in sorted(ctx.course_keys)]
    if ctx.tier >= 3 and ctx.college and ctx.school_code:
        keys.append(f"college:{ctx.school_code}:{ctx.college}")
    if ctx.tier >= 4 and ctx.school_code:
        keys.append(f"school:{ctx.school_code}")
    if ctx.tier >= 3:  # 散修 live only in the platform bucket; the scope check trims everyone else
        keys.append("platform")
    return keys


def _rec_users(conn: Any, me: str, identities: list[str], kind: str) -> list[dict[str, Any]]:
    from .social_identity_service import public_users

    users = [item for item in public_users(conn, me, identities) if item["relation"] == "none" and item["can_add"]]
    return [{**item, "match": kind, "hint": HINTS[kind]} for item in users[:SERVE_LIMIT]]


def _teacher_payload(conn: Any, user: dict, me: str) -> dict[str, Any]:
    """Teachers: ordinary suggestions from the students they teach (no AI, no trait data)."""
    from .offering_membership_service import offering_student_where

    rows = conn.execute(
        f"SELECT DISTINCT s.id FROM class_offerings o JOIN students s ON {offering_student_where()} "
        "WHERE o.teacher_id = ? AND COALESCE(s.enrollment_status, 'active') = 'active' ORDER BY s.id LIMIT 200",
        (as_int(user.get("id")),),
    ).fetchall()
    excluded = excluded_identities(conn, me)
    candidates = [f"student:{int(row['id'])}" for row in rows if f"student:{int(row['id'])}" not in excluded]
    random.Random(f"{utc_date()}:{me}").shuffle(candidates)
    return {"complement": [], "similar": _rec_users(conn, me, candidates[:40], SIMILAR),
            "generated_on": utc_date(), "locked": False}


def recommendations_payload(conn: Any, user: dict) -> dict[str, Any]:
    """``GET /api/social/recommendations`` -> ``{complement, similar, generated_on, locked}``."""
    from .social_scope_service import scope_context

    ensure_schema(conn)
    me = user_identity(user)
    ctx = scope_context(conn, user)
    if ctx.is_teacher:
        return _teacher_payload(conn, user, me)
    if ctx.is_wanderer or not ctx.can_add_anyone:
        return {"complement": [], "similar": [], "generated_on": None, "locked": not ctx.is_wanderer}
    keys = list(dict.fromkeys(_allowed_keys(ctx) + (["platform"] if ctx.tier >= 5 else [])))
    marks, params = in_clause(keys)
    rows = conn.execute(
        "SELECT scope_key, candidate_identity, kind, rank, computed_on FROM social_recommendation_pairs "
        f"WHERE identity = ? AND scope_key IN ({marks}) ORDER BY rank",
        (me, *params),
    ).fetchall()
    excluded = excluded_identities(conn, me)
    ordered: dict[str, list[str]] = {COMPLEMENT: [], SIMILAR: []}
    generated = ""
    for row in sorted(rows, key=lambda item: (as_int(item["rank"]), _bucket_order(str(item["scope_key"])))):
        candidate, kind = str(row["candidate_identity"]), str(row["kind"])
        if kind in ordered and candidate not in excluded and candidate not in ordered[kind]:
            ordered[kind].append(candidate)
            generated = max(generated, str(row["computed_on"] or ""))
    complement = _rec_users(conn, me, ordered[COMPLEMENT], COMPLEMENT)
    taken = {item["identity"] for item in complement}
    similar = _rec_users(conn, me, [item for item in ordered[SIMILAR] if item not in taken], SIMILAR)
    return {"complement": complement, "similar": similar, "generated_on": generated or None, "locked": False}
