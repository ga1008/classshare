"""散修好友：处理好友申请、偶尔主动加人、在线状态推送 (docs/social-friends-groups-2026-10-27.md §10).

* ``wanderer_friend_request`` — one personality-driven model decision with a
  private reason, current public profile and relationship memory. A short CAS
  transaction applies the decision and records its reason; failures defer.
* ``maybe_befriend`` — during the regular tick, rarely (4 % per awake wanderer
  per tick, ≤ 2 per wanderer per week) the wanderer adds a real student who
  commented on / replied to its blog content or sent it a classroom private
  message in the last 30 days, with a short note in its own voice (one AI turn;
  a template when capped).
* ``sync_wanderer_presence`` — the simulated online state (active + inside
  active_hours) is recomputed every tick; only changes are pushed, and only to
  the wanderer's friends and co-group members. Going offline records
  ``user_presence.last_seen_at`` so friend cards show "last seen".
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from . import social_push_service
from .social_common import SocialError, blocked_either, ensure_schema, parse_iso, utc_day_start_iso
from .social_friend_service import accept_request, decline_request, send_request
from .social_identity_service import load_people
from .social_relations import (
    STATUS_PENDING,
    active_group_ids,
    count_friends,
    get_friendship,
    get_friendship_by_id,
    presence_audience,
)
from .wanderer_actions_common import ActionContext, as_int, behavior_on, outcome, record_action, run_turn, turn_parts
from .wanderer_actions_social import social_context
from .wanderer_identity_service import STATUS_ACTIVE, build_wanderer_user_dict, list_wanderers, load_wanderer, log_wanderer_action
from .wanderer_memory_service import append_turn
from .wanderer_prompt_service import extract_json_object
from .wanderer_social_context import context_block, friend_context
from .wanderer_settings_service import wanderer_runtime_setting_get, wanderer_runtime_setting_set
from .wanderer_social_triggers import (
    SOCIAL_BEHAVIOR,
    TASK_KIND_FRIEND_REQUEST,
    TASK_MAX_ATTEMPTS,
    TASK_PRIORITY,
    friend_dedupe_key,
    is_awake_now,
    on_friend_request,
    seconds_until_awake,
)

PROACTIVE_PROB = 0.04
IGNORE_DAYS = 7
IGNORE_SECONDS = IGNORE_DAYS * 86400
DEFER_SECONDS = 3600
PROACTIVE_WEEKLY_LIMIT = 2
PROACTIVE_WANDERER_WEEKLY_LIMIT = 1
PROACTIVE_LOOKBACK_DAYS = 30
PROACTIVE_CANDIDATES = 50
WANDERER_AFFINITY_THRESHOLD = 40.0
REQUEST_NOTE_MAX = 40
SETTING_PRESENCE_STATE = "social_presence_state"
TARGET_REQUEST_IN = "friend_request"
TARGET_REQUEST_OUT = "friend_request_out"

ConnFactory = Optional[Callable[[], Any]]


def _connect(conn_factory: ConnFactory) -> Any:
    if conn_factory is not None:
        return conn_factory()
    from ..database import get_db_connection

    return get_db_connection()


# ---------------------------------------------------------------------------
# Incoming requests
# ---------------------------------------------------------------------------

def _pending_request(conn: Any, request_id: int, me: str, requester: str = "") -> Optional[dict[str, Any]]:
    """The live pending request addressed to ``me``: by pair when known (ids change on re-request), else by id."""
    row = get_friendship(conn, me, requester) if requester else get_friendship_by_id(conn, int(request_id))
    if not row or row["status"] != STATUS_PENDING or row["requester_identity"] == me:
        return None
    return row if me in (row["identity_a"], row["identity_b"]) else None


def _defer(conn: Any, ctx: ActionContext, row: dict[str, Any], *, seconds: float = DEFER_SECONDS,
           ignored: bool = False) -> None:
    """Re-arm after a pause and inside the next preferred activity window.

    ``ignore`` parks the same pending request for a week; the request row is
    untouched, so the requester still sees it waiting and cannot re-send it.
    """
    from .scheduled_task_service import schedule_task
    moment = ctx.current_time() + timedelta(seconds=max(60, seconds))
    wait = seconds_until_awake(ctx.wanderer, moment)
    moment += timedelta(seconds=wait if wait is not None else 86400)
    schedule_task(conn, task_kind=TASK_KIND_FRIEND_REQUEST, run_at=moment,
                  payload={"wanderer_id": ctx.wanderer_id, "request_id": int(row["id"]),
                           "requester": row["requester_identity"], "deferred": not ignored,
                           "ignored": ignored},
                  dedupe_key=friend_dedupe_key(ctx.wanderer_id, row["requester_identity"]), owner_role="system",
                  title=f"散修处理好友申请 · {ctx.display_name}", priority=TASK_PRIORITY,
                  max_attempts=TASK_MAX_ATTEMPTS, replace=True)


def _request_history(conn: Any, wanderer_id: int, request_id: int, created_at: str = "") -> dict[str, Any]:
    """How often this *same* request instance was considered, and the last decision."""
    sql = ("SELECT created_at, outcome, detail_json FROM wanderer_action_log WHERE wanderer_id = ? "
           "AND action_kind = 'social_friend' AND target_type = ? AND target_id = ?")
    params: list[Any] = [int(wanderer_id), TARGET_REQUEST_IN, str(int(request_id))]
    if created_at:
        # A declined request that is re-sent reuses the row id; the timestamp separates the instances.
        sql += " AND detail_json LIKE ?"
        params.append(f'%"request_created_at": "{created_at}"%')
    rows = conn.execute(sql + " ORDER BY id DESC LIMIT 20", tuple(params)).fetchall()
    decisions = []
    for row in rows:
        detail = _loads_detail(row)
        decisions.append(str(detail.get("decision") or row_outcome(row)))
    return {"seen_count": len(rows), "last_decision": decisions[0] if decisions else "",
            "decisions": decisions}


def _field(row: Any, key: str) -> Any:
    try:
        return row[key]
    except (TypeError, KeyError, IndexError):
        return ""


def _loads_detail(row: Any) -> dict[str, Any]:
    try:
        value = json.loads(_field(row, "detail_json") or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def row_outcome(row: Any) -> str:
    return str(_field(row, "outcome") or "")


def _local_stamp(value: Any) -> str:
    created = parse_iso(value)
    if created is None:
        return str(value or "")
    return created.astimezone().strftime("%Y-%m-%d %H:%M")


def _request_age_hours(row: dict[str, Any], now: datetime) -> float:
    created = parse_iso(row.get("created_at"))
    if created is None:
        return 0.0
    if created.tzinfo is not None:
        created = created.astimezone().replace(tzinfo=None)
    return max(0.0, (now - created).total_seconds() / 3600.0)


def _request_scene(row: dict[str, Any], history: dict[str, Any], now: datetime,
                   context: Optional[dict[str, Any]], *, peer_name: str = "") -> dict[str, Any]:
    """The scene must make one request unmistakably one request (not a daily repeat)."""
    created = _local_stamp(row.get("created_at"))
    age_hours = _request_age_hours(row, now)
    seen = int(history.get("seen_count") or 0)
    age_text = f"约 {age_hours / 24:.1f} 天前" if age_hours >= 24 else f"约 {age_hours:.0f} 小时前"
    last = str(history.get("last_decision") or "")
    last_text = {"ignore": "你上次选择先忽略", "defer": "你上次选择稍后再看",
                 "decline": "你上次拒绝了旧的那次申请；这是后来重新发起的",
                 "accept": "你上次接受了"}.get(last, "")
    meta = (f"这是同一条好友申请，编号 {row.get('id')}，发起于 {created}（{age_text}）。"
            f"这是你第 {seen + 1} 次看到它（此前处理过 {seen} 次）；对方没有重复发送，"
            "也不能再重复发送这条申请。" + (f" {last_text}。" if last_text else ""))
    return {
        "kind": "审核好友申请", "title": f"{peer_name or row.get('requester_identity')} 的好友申请（同一条，等待中）",
        "meta": meta, "body": str(row.get("request_message") or "（没有附言）"),
        "friend_context": context_block(context or {}),
        "request": {"id": int(row.get("id") or 0), "created_at": created, "age_hours": round(age_hours, 1),
                    "seen_count": seen, "last_decision": last, "note": str(row.get("request_message") or "")},
        "task": ("依据你的人格、喜好、对方资料和结交记忆决定：accept 接受、decline 拒绝、"
                 "ignore 暂时不处理（申请会继续挂着，对方不会收到拒绝）、defer 过一小时再看。"
                 "这是一条旧申请，不要当成对方今天又发了一次。理由仅供你的记忆和后台记录，不对申请人公开。"),
        "output_contract": '只输出 JSON：{"decision":"accept|decline|ignore|defer","reason":"具体的个人原因"}。不得省略原因。',
    }


def _claim_request(conn: Any, row: dict[str, Any]) -> bool:
    """CAS + row lock: a model decision cannot apply to a withdrawn/replaced request."""
    changed = conn.execute(
        "UPDATE social_friendships SET request_message = request_message WHERE id = ? AND status = 'pending' "
        "AND requester_identity = ? AND created_at = ? AND request_message = ?",
        (int(row["id"]), row["requester_identity"], row["created_at"], row.get("request_message") or ""),
    )
    return changed.rowcount == 1


def _decision_memory(conn: Any, ctx: ActionContext, row: dict[str, Any], decision: str, reason: str,
                     *, calls: int = 0, source: str = "personality", peer_name: str = "",
                     history: Optional[dict[str, Any]] = None) -> None:
    peer = str(row["requester_identity"])
    seen = int((history or {}).get("seen_count") or 0)
    created = _local_stamp(row.get("created_at"))
    detail = {"decision": decision, "reason": reason, "requester": peer, "requester_name": peer_name,
              "request_created_at": row["created_at"], "request_created_local": created,
              "request_age_hours": round(_request_age_hours(row, ctx.current_time()), 1),
              "seen_count": seen, "same_request": True, "source": source}
    location = f"好友申请 · {peer_name or peer}"
    log_wanderer_action(conn, ctx.wanderer_id, "social_friend", target_type=TARGET_REQUEST_IN,
                        target_id=str(row["id"]), ai_calls=calls, location=location,
                        outcome="skipped" if decision in {"defer", "ignore"} else "ok", detail=detail)
    stamp = (f"这是一条 {created} 发来的同一条好友申请（编号 {row['id']}），你已看过 {seen} 次；"
             "对方没有重复发送。") if created else ""
    append_turn(conn, ctx.wanderer, f"social:friend:{peer}", who=ctx.display_name,
                text=f"{stamp}附言：{row.get('request_message') or '无'}。我的决定：{decision}。原因：{reason}",
                counterpart_identity=peer)
    from .wanderer_episodic_memory_service import record_memory
    record_memory(conn, ctx.wanderer, kind="relationship", title="一条好友申请的处置",
                  content=f"{peer_name or peer} 在 {created or '此前'} 发来好友申请，附言："
                          f"{row.get('request_message') or '无'}。这是同一条申请，我第 {seen + 1} 次考虑，"
                          f"决定：{decision}。原因：{reason}",
                  importance=0.72, people=[peer], source_kind="friend_request",
                  source_ref=f"request:{row['id']}", counterpart_identity=peer, location=location)


async def decide_friend_request(ctx: ActionContext, request_id: int, *, deferred: bool = False,
                                requester: str = "") -> dict[str, Any]:
    """One personality decision, committed together with its reason and relationship memory."""
    with ctx.connect() as conn:
        row = _pending_request(conn, request_id, ctx.identity, requester)
        if row and blocked_either(conn, ctx.identity, row["requester_identity"]):
            return {"decision": "gone"}
        context = friend_context(conn, ctx.wanderer, row["requester_identity"]) if row else None
        history = (_request_history(conn, ctx.wanderer_id, int(row["id"]), str(row.get("created_at") or ""))
                   if row else {})
    if row is None:
        return {"decision": "gone"}
    peer_name = str(((context or {}).get("profile") or {}).get("name") or "")
    calls, source, seconds = 0, "personality", DEFER_SECONDS
    if not is_awake_now(ctx.wanderer, ctx.current_time()):
        decision, reason, source = "defer", "现在不在我偏好的上线时段，留到下次上线再考虑。", "schedule"
        seconds = 60
    else:
        scene = _request_scene(row, history, ctx.current_time(), context or {}, peer_name=peer_name)
        result = await run_turn(ctx, scene=scene, thread_key=f"social:friend:{row['requester_identity']}",
                                suffix="friend_decision")
        parsed = extract_json_object(result.get("raw_text")) or {}
        decision = str(parsed.get("decision") or "").strip().lower()
        reason = str(parsed.get("reason") or "").strip()[:500]
        calls = int(result.get("ai_calls") or 0)
        if result.get("status") != "ok" or decision not in {"accept", "decline", "ignore", "defer"} or not reason:
            decision, reason, source = "defer", "这次未能完成判断，保留申请稍后重新考虑。", "retry"
            seconds = 86400 if result.get("status") == "capped" else 1800
    events: list = []
    user = build_wanderer_user_dict(ctx.wanderer)
    try:
        with ctx.connect() as conn:
            from .wanderer_settings_service import is_wanderer_runtime_enabled
            fresh = load_wanderer(conn, ctx.wanderer_id)
            if (not is_wanderer_runtime_enabled(conn) or not fresh or fresh.get("status") != STATUS_ACTIVE
                    or not behavior_on(fresh, SOCIAL_BEHAVIOR)):
                return {"decision": "gone"}
            if not _claim_request(conn, row) or blocked_either(conn, ctx.identity, row["requester_identity"]):
                return {"decision": "gone"}
            ctx.wanderer = fresh
            if decision == "defer" and not is_awake_now(fresh, ctx.current_time()):
                decision, reason, source, seconds = "defer", "上线时段已改变，留到下次上线再考虑。", "schedule", 60
            if decision == "ignore":
                _defer(conn, ctx, row, seconds=IGNORE_SECONDS, ignored=True)
            elif decision == "defer":
                _defer(conn, ctx, row, seconds=seconds)
            else:
                (accept_request if decision == "accept" else decline_request)(conn, user, int(row["id"]), events)
            _decision_memory(conn, ctx, row, decision, reason, calls=calls, source=source,
                             peer_name=peer_name, history=history)
            conn.commit()
    except SocialError as exc:
        record_action(ctx, "social_friend", target_type=TARGET_REQUEST_IN, target_id=str(row["id"]),
                      outcome="failed", ai_calls=calls,
                      detail={"decision": decision, "reason": reason, "error": str(exc.detail)[:200]})
        return {"decision": "failed"}
    await social_push_service.push_events(events)
    return {"decision": decision, "reason": reason}


async def handle_wanderer_friend_request(task: dict[str, Any], *, ai_caller: Any = None, sleep: Any = None,
                                         rng: Optional[random.Random] = None, conn_factory: ConnFactory = None) -> str:
    payload = task.get("payload") or {}
    request_id = as_int(payload.get("request_id"))
    requester = str(payload.get("requester") or "")
    if request_id <= 0 and not requester:
        return "skipped: missing request"
    ctx, reason = social_context(payload.get("wanderer_id"), conn_factory=conn_factory, ai_caller=ai_caller,
                                 sleep=sleep, rng=rng)
    if ctx is None:
        return f"skipped: {reason}"
    result = await decide_friend_request(ctx, request_id, deferred=bool(payload.get("deferred")), requester=requester)
    return f"friend request: {result['decision']}"


# ---------------------------------------------------------------------------
# Proactive requests (tick)
# ---------------------------------------------------------------------------

def week_requests(conn: Any, wanderer_id: int, now: datetime) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM wanderer_action_log WHERE wanderer_id = ? "
        "AND action_kind IN ('social_friend', 'social_seek', 'social_search') "
        "AND target_type = ? AND outcome = 'ok' AND created_at >= ?",
        (int(wanderer_id), TARGET_REQUEST_OUT, (now - timedelta(days=7)).isoformat(timespec="seconds")),
    ).fetchone()
    return as_int(row["n"]) if row else 0


_ENGAGED_QUERIES = (
    "SELECT DISTINCT c.author_identity AS who FROM blog_comments c JOIN blog_posts p ON p.id = c.post_id "
    "WHERE p.author_identity = ? AND c.author_role = 'student' AND c.status = 'active' "
    "AND COALESCE(c.author_display_mode, 'real_name') = 'real_name' AND c.created_at >= ?",
    "SELECT DISTINCT c.author_identity AS who FROM blog_comments c JOIN blog_comments parent "
    "ON parent.id = c.parent_comment_id WHERE parent.author_identity = ? AND c.author_role = 'student' "
    "AND c.status = 'active' AND COALESCE(c.author_display_mode, 'real_name') = 'real_name' AND c.created_at >= ?",
    "SELECT DISTINCT sender_identity AS who FROM private_messages WHERE recipient_identity = ? "
    "AND sender_role = 'student' AND created_at >= ?",
)


def engaged_students(conn: Any, identity: str, now: datetime) -> list[str]:
    """Real students who commented on/replied to this wanderer's blog content or messaged it (30 days).

    Anonymous / pen-name comments are excluded: the wanderer must not learn who wrote them.
    """
    since = (now - timedelta(days=PROACTIVE_LOOKBACK_DAYS)).isoformat(timespec="seconds")
    found: list[str] = []
    for sql in _ENGAGED_QUERIES:
        found.extend(str(row["who"]) for row in conn.execute(sql, (identity, since)).fetchall())
    return list(dict.fromkeys(item for item in found if item and item != identity))[:PROACTIVE_CANDIDATES]


def _wanderer_peer_candidates(conn: Any, wanderer: dict[str, Any], identity: str, now: datetime) -> list[str]:
    """Other 散修 this wanderer likes *and* who likes it back (lifecycle doc §5)."""
    from .wanderer_relationship_service import mutual_affinity

    week = (now - timedelta(days=7)).isoformat(timespec="seconds")
    outgoing = conn.execute(
        "SELECT COUNT(*) AS n FROM wanderer_action_log WHERE wanderer_id = ? AND action_kind = 'social_friend' "
        "AND target_type = ? AND target_id LIKE 'student:%' AND outcome = 'ok' AND created_at >= ?",
        (int(wanderer["id"]), TARGET_REQUEST_OUT, week),
    ).fetchone()
    if as_int(outgoing["n"]) >= PROACTIVE_WANDERER_WEEKLY_LIMIT:
        return []
    rows = conn.execute(
        "SELECT peer_identity FROM wanderer_relationships WHERE wanderer_id = ? AND affinity >= ? "
        "ORDER BY affinity DESC LIMIT ?",
        (int(wanderer["id"]), WANDERER_AFFINITY_THRESHOLD, PROACTIVE_CANDIDATES),
    ).fetchall()
    peers = []
    for row in rows:
        peer = str(row["peer_identity"] or "")
        if not peer.startswith("student:") or peer == identity:
            continue
        if get_friendship(conn, identity, peer) is not None or blocked_either(conn, identity, peer):
            continue
        if mutual_affinity(conn, wanderer, peer, minimum=WANDERER_AFFINITY_THRESHOLD):
            peers.append(peer)
    return peers


def befriend_candidates(conn: Any, identity: str, now: datetime,
                        wanderer: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    ensure_schema(conn)
    wanted = engaged_students(conn, identity, now)
    if wanderer is not None:
        wanted = [*wanted, *_wanderer_peer_candidates(conn, wanderer, identity, now)]
    people = load_people(conn, wanted)
    return [
        people[item] for item in wanted
        if item in people and people[item]["active"]
        and (wanderer is not None or not people[item]["is_wanderer"])
        and get_friendship(conn, identity, item) is None and not blocked_either(conn, identity, item)
    ]


async def _request_note(ctx: ActionContext, person: dict[str, Any]) -> tuple[str, int]:
    is_wanderer = bool(person.get("is_wanderer"))
    scene = {
        "kind": "加好友", "title": f"想加 {person['name']} 为好友",
        "meta": ("你们互相有好感，聊过几次" if is_wanderer
                 else "对方在你的帖子或评论下和你互动过，或者给你发过私信"),
        "counterpart": {"name": person["name"], "is_wanderer": is_wanderer,
                        "tier_name": str((person.get("tier_badge") or {}).get("name") or "")} if is_wanderer
        else {"name": person["name"], "is_wanderer": False, "role_label": "真实学生"},
        "task": f"写一句很短的好友申请附言（不超过 {REQUEST_NOTE_MAX // 2} 字），像你平时说话；用 reply 输出一条。",
    }
    result = await run_turn(ctx, scene=scene, thread_key=f"social:friend:{person['identity']}", suffix="friend_note")
    parts = turn_parts(ctx, result.get("turn") or {})
    if parts:
        return parts[0][:REQUEST_NOTE_MAX], int(result.get("ai_calls") or 0)
    return f"我是{ctx.display_name}，看过你的留言，交个朋友？", int(result.get("ai_calls") or 0)


async def maybe_befriend(ctx: ActionContext, *, chance: float = PROACTIVE_PROB) -> list[dict[str, Any]]:
    """Rarely send one friend request to a real student who engaged with this wanderer."""
    if not behavior_on(ctx.wanderer, SOCIAL_BEHAVIOR) or ctx.rng.random() >= chance:
        return []
    now = ctx.current_time()
    with ctx.connect() as conn:
        if week_requests(conn, ctx.wanderer_id, now) >= PROACTIVE_WEEKLY_LIMIT:
            return []
        candidates = befriend_candidates(conn, ctx.identity, now, ctx.wanderer)
    if not candidates:
        return []
    person = ctx.rng.choice(candidates)
    note, calls = await _request_note(ctx, person)
    target = {"target_type": TARGET_REQUEST_OUT, "target_id": person["identity"]}
    events: list = []
    try:
        with ctx.connect() as conn:
            result = send_request(conn, build_wanderer_user_dict(ctx.wanderer), person["identity"], note, events)
            conn.commit()
    except SocialError as exc:
        record_action(ctx, "social_friend", **target, content_preview=note, ai_calls=calls, outcome="failed",
                      detail={"error": str(exc.detail)[:200]})
        return [outcome("social_friend", "failed", ai_calls=calls)]
    await social_push_service.push_events(events)
    record_action(ctx, "social_friend", **target, content_preview=note, ai_calls=calls,
                  location=f"好友申请 · {person['name']}",
                  detail={"decision": "request", "status": result.get("status")})
    return [outcome("social_friend", ai_calls=calls)]


def seek_candidates(conn: Any, ctx: ActionContext, *, limit: int = 8) -> list[dict[str, Any]]:
    """People this wanderer could deliberately approach: recommendations, engaged users, 散修 peers."""
    from .social_identity_service import suggest_users

    now = ctx.current_time()
    found: dict[str, dict[str, Any]] = {}
    try:
        for person in suggest_users(conn, build_wanderer_user_dict(ctx.wanderer)):
            if person.get("can_add") and person.get("relation") in (None, "", "none"):
                found[str(person["identity"])] = dict(person)
    except Exception as exc:  # noqa: BLE001 - recommendations are optional; friends still work
        print(f"[WANDERER] seek suggestions skipped: {type(exc).__name__}: {exc}")
    for person in befriend_candidates(conn, ctx.identity, now, ctx.wanderer):
        found.setdefault(str(person["identity"]), dict(person))
    for person in list_wanderers(conn, statuses=[STATUS_ACTIVE]):
        identity = f"student:{int(person['student_id'])}"
        if identity == ctx.identity or identity in found:
            continue
        if get_friendship(conn, ctx.identity, identity) is not None or blocked_either(conn, ctx.identity, identity):
            continue
        found[identity] = {"identity": identity, "name": str(person.get("display_name") or "散修"),
                           "is_wanderer": True, "can_add": True, "relation": "none",
                           "tier_name": str(person.get("tier_name") or "")}
    people: list[dict[str, Any]] = []
    for identity, person in found.items():
        if not person.get("active", True) or identity == ctx.identity:
            continue
        people.append({"identity": identity, "name": str(person.get("name") or identity)[:40],
                       "is_wanderer": bool(person.get("is_wanderer")),
                       "tier_name": str(person.get("tier_name") or "")[:20],
                       "relation": str(person.get("relation") or "none")})
        if len(people) >= max(1, int(limit)):
            break
    return people


async def maybe_seek_friend(ctx: ActionContext) -> list[dict[str, Any]]:
    """Deliberately look through candidates and decide whether to send one friend request."""
    if (not behavior_on(ctx.wanderer, SOCIAL_BEHAVIOR) or not behavior_on(ctx.wanderer, "social_seek")
            or not is_awake_now(ctx.wanderer, ctx.current_time())):
        return []
    now = ctx.current_time()
    with ctx.connect() as conn:
        if week_requests(conn, ctx.wanderer_id, now) >= PROACTIVE_WEEKLY_LIMIT:
            return []
        candidates = seek_candidates(conn, ctx)
    if not candidates:
        return []
    scene = {
        "kind": "寻友", "title": "看看有没有想认识的人",
        "meta": ("下面是平台按公开资料、共同课堂和近期互动推荐的人，不全是熟人；"
                 "你可以选一个发出好友申请，也可以觉得没意思就跳过。"),
        "body": json.dumps(candidates, ensure_ascii=False),
        "task": ("依据你的人格、兴趣和当前心情，从候选人里挑一个真正想认识的人，写一句不超过 20 字的附言；"
                 "没有想认识的就 skip，不必为了社交而社交。"),
        "output_contract": ('只输出 JSON：{"action":"request|skip","target":"候选人里的 identity",'
                            '"note":"附言","reason":"你自己的理由"}。'),
    }
    result = await run_turn(ctx, scene=scene, thread_key="social:seek", suffix="seek_friend")
    parsed = extract_json_object(result.get("raw_text")) or {}
    calls = int(result.get("ai_calls") or 0)
    action = str(parsed.get("action") or "skip").strip().lower()
    target = str(parsed.get("target") or "").strip()
    note = str(parsed.get("note") or "").strip()[:REQUEST_NOTE_MAX]
    reason = str(parsed.get("reason") or "").strip()[:300]
    chosen = next((item for item in candidates if item["identity"] == target), None)
    if result.get("status") != "ok" or action != "request" or chosen is None:
        record_action(ctx, "social_seek", target_type="seek", outcome="skipped", ai_calls=calls,
                      location="寻友 · 候选人名单",
                      detail={"reason": reason or "没有想认识的人", "status": result.get("status")})
        return [outcome("social_seek", "skipped", ai_calls=calls)]
    events: list = []
    try:
        with ctx.connect() as conn:
            sent = send_request(conn, build_wanderer_user_dict(ctx.wanderer), target, note, events)
            conn.commit()
    except SocialError as exc:
        record_action(ctx, "social_seek", target_type=TARGET_REQUEST_OUT, target_id=target,
                      outcome="failed", ai_calls=calls, location=f"寻友 · {chosen['name']}",
                      detail={"error": str(exc.detail)[:200], "reason": reason})
        return [outcome("social_seek", "failed", ai_calls=calls)]
    await social_push_service.push_events(events)
    record_action(ctx, "social_seek", target_type=TARGET_REQUEST_OUT, target_id=target,
                  content_preview=note, outcome="ok", ai_calls=calls,
                  location=f"寻友 · {chosen['name']}",
                  detail={"decision": "request", "reason": reason, "status": sent.get("status"),
                          "is_wanderer": chosen["is_wanderer"]})
    from .wanderer_episodic_memory_service import record_memory
    with ctx.connect() as conn:
        record_memory(conn, ctx.wanderer, kind="relationship", title="主动认识一个人",
                      content=f"我在寻友时看到 {chosen['name']}，决定发出好友申请：{note or '无附言'}。原因：{reason}",
                      importance=0.62, people=[target], source_kind="friend_seek",
                      source_ref=f"seek:{target}:{now.isoformat(timespec='seconds')}",
                      counterpart_identity=target, location=f"寻友 · {chosen['name']}")
        conn.commit()
    return [outcome("social_seek", ai_calls=calls)]


# ---------------------------------------------------------------------------
# Presence (tick)
# ---------------------------------------------------------------------------

def _presence_state(wanderers: list[dict[str, Any]], now: datetime) -> dict[str, int]:
    return {str(item["student_id"]): int(str(item.get("status") or "") == STATUS_ACTIVE and is_awake_now(item, now))
            for item in wanderers}


def _load_previous_state(conn: Any) -> Optional[dict[str, Any]]:
    raw = wanderer_runtime_setting_get(conn, SETTING_PRESENCE_STATE)
    try:
        previous = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        return None
    return previous if isinstance(previous, dict) else None


async def sync_wanderer_presence(conn_factory: ConnFactory = None, *, now: Optional[datetime] = None) -> dict[str, Any]:
    """Push simulated online/offline changes since the previous tick to friends and co-group members.

    The first run only records the state (no burst of pushes after a deploy).
    """
    moment = now or datetime.now()
    pushes: list[tuple[list[str], dict[str, Any]]] = []
    with _connect(conn_factory) as conn:
        ensure_schema(conn)
        state = _presence_state(list_wanderers(conn), moment)
        previous = _load_previous_state(conn)
        wanderer_runtime_setting_set(conn, SETTING_PRESENCE_STATE, json.dumps(state, sort_keys=True))
        stamp = datetime.now(timezone.utc).isoformat()
        for student_id, online in state.items():
            if previous is None or previous.get(student_id) == online:
                continue
            identity = f"student:{student_id}"
            conn.execute("INSERT INTO user_presence (identity, last_seen_at) VALUES (?, ?) "
                         "ON CONFLICT (identity) DO UPDATE SET last_seen_at = excluded.last_seen_at", (identity, stamp))
            audience = sorted(presence_audience(conn, identity))
            if audience:
                pushes.append((audience, {"type": "presence", "identity": identity, "online": bool(online),
                                          "last_seen_at": stamp}))
        conn.commit()
    for audience, event in pushes:
        await social_push_service.push(audience, event)
    return {"wanderers": len(state), "pushed": len(pushes), "first_run": previous is None}


async def run_social_upkeep(conn_factory: ConnFactory, wanderer_ids: list[int], *, ai_caller: Any = None,
                            sleep: Any = None, rng: Optional[random.Random] = None,
                            now: Optional[datetime] = None) -> dict[str, Any]:
    """Tick step: presence changes for everyone, a rare proactive request per processed wanderer."""
    summary: dict[str, Any] = {"presence": await sync_wanderer_presence(conn_factory, now=now), "friend_requests": 0,
                               "recovered_requests": recover_pending_requests(conn_factory, now=now)}
    for wanderer_id in wanderer_ids:
        ctx, _reason = social_context(wanderer_id, conn_factory=conn_factory, ai_caller=ai_caller, sleep=sleep, rng=rng)
        if ctx is None:
            continue
        ctx.now = now
        done = await maybe_befriend(ctx)
        summary["friend_requests"] += sum(1 for item in done if item["outcome"] == "ok")
    return summary


def recover_pending_requests(conn_factory: ConnFactory = None, *, now: Optional[datetime] = None) -> int:
    """Bounded recovery after an old missed hook, pause, disabled runtime or exhausted task retry."""
    from .wanderer_settings_service import is_wanderer_runtime_enabled
    with _connect(conn_factory) as conn:
        if not is_wanderer_runtime_enabled(conn):
            return 0
        ignored_since = (now or datetime.now()) - timedelta(days=IGNORE_DAYS)
        rows = conn.execute(
            "SELECT f.id, f.requester_identity, w.student_id FROM social_friendships f "
            "JOIN wanderer_agents w ON (f.identity_a = 'student:' || CAST(w.student_id AS TEXT) "
            "OR f.identity_b = 'student:' || CAST(w.student_id AS TEXT)) "
            "WHERE f.status = 'pending' AND w.status = 'active' "
            "AND f.requester_identity <> 'student:' || CAST(w.student_id AS TEXT) "
            "AND NOT EXISTS (SELECT 1 FROM scheduled_tasks t WHERE t.dedupe_key = "
            "'wanderer:friend:' || CAST(w.id AS TEXT) || ':' || f.requester_identity "
            "AND t.status IN ('pending', 'running')) "
            "AND NOT EXISTS (SELECT 1 FROM wanderer_action_log l WHERE l.wanderer_id = w.id "
            "AND l.action_kind = 'social_friend' AND l.target_type = 'friend_request' "
            "AND l.target_id = CAST(f.id AS TEXT) AND l.created_at >= ? "
            "AND l.detail_json LIKE '%\"decision\": \"ignore\"%' "
            "AND l.detail_json LIKE '%\"request_created_at\": \"' || f.created_at || '\"%') "
            "ORDER BY f.created_at LIMIT 40",
            (ignored_since.isoformat(timespec="seconds"),),
        ).fetchall()
        recovered = 0
        for row in rows:
            task = on_friend_request(conn, int(row["id"]), str(row["requester_identity"]),
                                     f"student:{int(row['student_id'])}", now=now, recovery=True)
            recovered += int(task is not None)
        conn.commit()
    return recovered


# ---------------------------------------------------------------------------
# Admin stats
# ---------------------------------------------------------------------------

REAL_USER_SEARCH_LIMIT = 12
SEARCH_TARGET_TYPE = "social_search"


def real_user_candidates(conn: Any, ctx: ActionContext, query: str = "", *, limit: int = REAL_USER_SEARCH_LIMIT) -> list[dict[str, Any]]:
    """Real students at or below this wanderer's tier, with the public card fields they may see.

    This is deliberately global (not the normal class/college social scope): a
    wanderer may look for anyone in the platform, but never above its own tier.
    """
    from .social_card_service import user_card
    from .social_scope_service import student_global_tier
    from .wanderer_life_core import clamp_tier

    my_tier = clamp_tier(ctx.wanderer.get("tier"))
    needle = str(query or "").strip()[:40]
    params: list[Any] = []
    sql = ("SELECT s.id FROM students s WHERE COALESCE(s.enrollment_status, 'active') = 'active' "
           "AND s.enrollment_status <> 'wanderer'")
    if needle:
        sql += " AND s.name LIKE ?"
        params.append(f"%{needle}%")
    sql += " ORDER BY s.id LIMIT 160"
    rows = conn.execute(sql, tuple(params)).fetchall()
    user = build_wanderer_user_dict(ctx.wanderer)
    found: list[dict[str, Any]] = []
    for row in rows:
        student_id = as_int(row["id"] if not isinstance(row, tuple) else row[0])
        if student_id <= 0 or student_global_tier(conn, student_id) > my_tier:
            continue
        identity = f"student:{student_id}"
        if identity == ctx.identity or get_friendship(conn, ctx.identity, identity) is not None \
                or blocked_either(conn, ctx.identity, identity):
            continue
        card = user_card(conn, user, identity)
        if not card.get("can_add"):
            continue
        radar = card.get("radar") or {}
        cultivation = radar.get("overall") if isinstance(radar, dict) else None
        found.append({
            "identity": identity,
            "name": str(card.get("name") or identity)[:40],
            "gender": str(card.get("gender") or ""),
            "tier": student_global_tier(conn, student_id),
            "tier_name": str((card.get("tier_badge") or {}).get("name") or ""),
            "cultivation": cultivation,
            "cultivation_hidden": bool(card.get("cultivation_hidden")),
            "courses": card.get("courses") or [],
            "radar": radar if isinstance(radar, dict) else None,
        })
        if len(found) >= max(1, int(limit)):
            break
    return found


async def maybe_search_friends(ctx: ActionContext) -> list[dict[str, Any]]:
    """Search the platform for real users and decide whether to send one request."""
    if (not behavior_on(ctx.wanderer, SOCIAL_BEHAVIOR) or not behavior_on(ctx.wanderer, "social_search")
            or not is_awake_now(ctx.wanderer, ctx.current_time())):
        return []
    with ctx.connect() as conn:
        if week_requests(conn, ctx.wanderer_id, ctx.current_time()) >= PROACTIVE_WEEKLY_LIMIT:
            return []
        candidates = real_user_candidates(conn, ctx)
    if not candidates:
        record_action(ctx, "social_search", target_type=SEARCH_TARGET_TYPE, outcome="skipped",
                      location="好友搜索", detail={"reason": "没有符合条件的真实用户"})
        return [outcome("social_search", "skipped")]
    scene = {
        "kind": "好友搜索", "title": "在平台上找同境界或更低境界的真实用户",
        "meta": ("搜索范围：全平台真实学生中，境界不高于你的那些人。下面是公开资料；"
                 "你可以挑一个发好友申请，也可以 skip。"),
        "body": json.dumps(candidates, ensure_ascii=False),
        "task": ("依据你的人格、兴趣和对方资料，挑一个真的想认识的人，写一句不超过 20 字的附言；"
                 "没有想认识的就 skip。真实用户也是现实里的人，按你自己的方式表达。"),
        "output_contract": ('只输出 JSON：{"action":"request|skip","target":"候选人里的 identity",'
                            '"note":"附言","reason":"你自己的理由"}。'),
    }
    result = await run_turn(ctx, scene=scene, thread_key="social:search", suffix="search_friends")
    parsed = extract_json_object(result.get("raw_text")) or {}
    calls = int(result.get("ai_calls") or 0)
    action = str(parsed.get("action") or "skip").strip().lower()
    target = str(parsed.get("target") or "").strip()
    note = str(parsed.get("note") or "").strip()[:REQUEST_NOTE_MAX]
    reason = str(parsed.get("reason") or "").strip()[:300]
    chosen = next((item for item in candidates if item["identity"] == target), None)
    if result.get("status") != "ok" or action != "request" or chosen is None:
        record_action(ctx, "social_search", target_type=SEARCH_TARGET_TYPE, outcome="skipped", ai_calls=calls,
                      location="好友搜索", detail={"reason": reason or "没有想认识的人"})
        return [outcome("social_search", "skipped", ai_calls=calls)]
    events: list = []
    try:
        with ctx.connect() as conn:
            sent = send_request(conn, build_wanderer_user_dict(ctx.wanderer), target, note, events)
            conn.commit()
    except SocialError as exc:
        record_action(ctx, "social_search", target_type=TARGET_REQUEST_OUT, target_id=target,
                      outcome="failed", ai_calls=calls, location=f"好友搜索 · {chosen['name']}",
                      detail={"error": str(exc.detail)[:200], "reason": reason})
        return [outcome("social_search", "failed", ai_calls=calls)]
    await social_push_service.push_events(events)
    record_action(ctx, "social_search", target_type=TARGET_REQUEST_OUT, target_id=target,
                  content_preview=note, outcome="ok", ai_calls=calls,
                  location=f"好友搜索 · {chosen['name']}",
                  detail={"decision": "request", "reason": reason, "status": sent.get("status"),
                          "target_tier": chosen["tier"], "cultivation": chosen["cultivation"]})
    from .wanderer_episodic_memory_service import record_memory
    with ctx.connect() as conn:
        record_memory(conn, ctx.wanderer, kind="relationship", title="搜索并结识一位真实用户",
                      content=(f"我在好友搜索里看到 {chosen['name']}（第 {chosen['tier']} 境，"
                               f"修为 {chosen['cultivation'] if chosen['cultivation'] is not None else '未公开'}），"
                               f"发出好友申请：{note or '无附言'}。原因：{reason}"),
                      importance=0.65, people=[target], source_kind="friend_search",
                      source_ref=f"search:{target}:{ctx.current_time().isoformat(timespec='seconds')}",
                      counterpart_identity=target, location=f"好友搜索 · {chosen['name']}")
        conn.commit()
    return [outcome("social_search", ai_calls=calls)]


def social_counts(conn: Any, student_id: int) -> dict[str, int]:
    """Friends, live groups, messages sent today (UTC day), pending incoming requests."""
    ensure_schema(conn)
    identity = f"student:{int(student_id)}"
    sent = conn.execute(
        "SELECT COUNT(*) AS n FROM social_messages WHERE sender_identity = ? AND created_at >= ? "
        "AND message_type IN ('text', 'media', 'nudge')", (identity, utc_day_start_iso()),
    ).fetchone()
    pending = conn.execute(
        "SELECT COUNT(*) AS n FROM social_friendships WHERE status = 'pending' AND requester_identity <> ? "
        "AND (identity_a = ? OR identity_b = ?)", (identity, identity, identity),
    ).fetchone()
    return {"friends": count_friends(conn, identity), "groups": len(active_group_ids(conn, identity)),
            "messages_today": as_int(sent["n"]) if sent else 0,
            "pending_requests": as_int(pending["n"]) if pending else 0}
