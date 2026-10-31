"""Wanderer-initiated social maintenance: block, unblock, group create/invite/leave.

Replies and group watching already live in ``wanderer_actions_social``. These
actions close the remaining parity gap. Every call goes through the normal
social services (friendship/block/membership checks), and the wanderer's own
personality decides through one JSON turn; failures degrade to a logged skip.
"""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from ..time_utils import local_iso
from .wanderer_actions_common import ActionContext, behavior_on, outcome, record_action, run_turn
from .wanderer_identity_service import build_wanderer_user_dict
from .wanderer_prompt_service import extract_json_object

BLOCK_AFFINITY = -60.0
UNBLOCK_AFFINITY = -20.0
GROUP_CREATE_WEEKLY_LIMIT = 1
GROUP_INVITE_DAILY_LIMIT = 2


def _me(ctx: ActionContext) -> str:
    return f"student:{ctx.student_id}"


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    try:
        return row[key]
    except (TypeError, KeyError, IndexError):
        return row[index]


def _people(conn: Any, identities: list[str]) -> dict[str, dict[str, Any]]:
    from .social_identity_service import load_people
    return load_people(conn, identities)


def _friends(conn: Any, ctx: ActionContext) -> list[dict[str, Any]]:
    me = _me(ctx)
    try:
        rows = conn.execute(
            "SELECT identity_a, identity_b FROM social_friendships WHERE status = 'accepted' "
            "AND (identity_a = ? OR identity_b = ?)", (me, me)).fetchall()
    except Exception:  # noqa: BLE001
        return []
    peers = [str(_row_value(row, "identity_b") if _row_value(row, "identity_a") == me else _row_value(row, "identity_a"))
             for row in rows]
    peers = [peer for peer in dict.fromkeys(peers) if peer.startswith("student:")]
    try:
        people = _people(conn, peers)
    except Exception:  # noqa: BLE001
        people = {}
    affinities = {}
    for row in conn.execute(
            "SELECT peer_identity, affinity, interactions FROM wanderer_relationships WHERE wanderer_id = ?",
            (ctx.wanderer_id,)).fetchall():
        affinities[str(_row_value(row, "peer_identity"))] = (float(_row_value(row, "affinity") or 0),
                                                             int(_row_value(row, "interactions") or 0))
    result = []
    for peer in peers:
        affinity, interactions = affinities.get(peer, (0.0, 0))
        result.append({"identity": peer, "name": (people.get(peer) or {}).get("name") or peer,
                       "affinity": affinity, "interactions": interactions,
                       "is_wanderer": bool((people.get(peer) or {}).get("is_wanderer"))})
    return result


def _recent_count(conn: Any, ctx: ActionContext, kind: str, days: int) -> int:
    since = (ctx.current_time() - timedelta(days=days)).isoformat(timespec="seconds")
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM wanderer_action_log WHERE wanderer_id = ? AND action_kind = ? "
        "AND outcome = 'ok' AND created_at >= ?", (ctx.wanderer_id, kind, since)).fetchone()
    return int(_row_value(row, "n") or 0)


async def _decide(ctx: ActionContext, *, scene: dict[str, Any], suffix: str) -> tuple[dict[str, Any], int]:
    result = await run_turn(ctx, scene=scene, thread_key=f"social_admin:{suffix}", suffix=suffix)
    payload = extract_json_object(result.get("raw_text")) if result.get("status") == "ok" else None
    return (payload if isinstance(payload, dict) else {}), int(result.get("ai_calls") or 0)


def _block_candidates(conn: Any, ctx: ActionContext) -> list[dict[str, Any]]:
    if _recent_count(conn, ctx, "social_block", 7) >= 1:
        return []
    return [item for item in _friends(conn, ctx) if item["affinity"] <= BLOCK_AFFINITY
            and item["interactions"] >= 2]


def _unblock_candidates(conn: Any, ctx: ActionContext) -> list[str]:
    try:
        from .social_common import blocked_by
        blocked = blocked_by(conn, _me(ctx))
    except Exception:  # noqa: BLE001
        return []
    found = []
    for peer in blocked:
        if not peer.startswith("student:"):
            continue
        rel = conn.execute("SELECT affinity FROM wanderer_relationships WHERE wanderer_id = ? AND peer_identity = ?",
                           (ctx.wanderer_id, peer)).fetchone()
        if float(_row_value(rel, "affinity") or 0) > UNBLOCK_AFFINITY:
            found.append(peer)
    return found


def _group_context(conn: Any, ctx: ActionContext) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[int, set[str]]]:
    me = _me(ctx)
    try:
        rows = conn.execute(
            "SELECT g.id, g.name, m.role FROM social_group_members m JOIN social_groups g ON g.id = m.group_id "
            "WHERE m.member_identity = ? AND m.left_at IS NULL AND g.dissolved_at IS NULL", (me,)).fetchall()
    except Exception:  # noqa: BLE001 - social tables may be absent in a minimal deployment
        return [], [], {}
    groups = [{"id": int(_row_value(row, "id")), "name": str(_row_value(row, "name") or ""),
               "role": str(_row_value(row, "role") or "")} for row in rows]
    friends = _friends(conn, ctx)
    members = {group["id"]: {str(_row_value(r, "member_identity")) for r in conn.execute(
        "SELECT member_identity FROM social_group_members WHERE group_id = ? AND left_at IS NULL",
        (group["id"],)).fetchall()} for group in groups}
    return groups, friends, members


def available_actions(conn: Any, ctx: ActionContext) -> list[str]:
    """Cheap preconditions for the weighted chooser; no model call."""
    options: list[str] = []
    if behavior_on(ctx.wanderer, "social_block"):
        if _block_candidates(conn, ctx):
            options.append("social_block")
        if _unblock_candidates(conn, ctx):
            options.append("social_unblock")
    if behavior_on(ctx.wanderer, "social_group"):
        groups, friends, members = _group_context(conn, ctx)
        if len(friends) >= 2 and _recent_count(conn, ctx, "social_group_create", 7) < GROUP_CREATE_WEEKLY_LIMIT:
            options.append("social_group_create")
        if any(friend["identity"] not in members[group["id"]] for group in groups
               for friend in friends if group["role"] in {"owner", "admin"}):
            options.append("social_group_invite")
        from .wanderer_emotion_service import tuning_for_today
        if groups and tuning_for_today(ctx.wanderer).get("initiative", 0) < 0:
            options.append("social_group_leave")
    return options


async def _push(events: list) -> None:
    if not events:
        return
    from .social_push_service import push_events
    await push_events(events)


async def maybe_block(ctx: ActionContext) -> list[dict[str, Any]]:
    if not behavior_on(ctx.wanderer, "social_block"):
        return []
    with ctx.connect() as conn:
        if _recent_count(conn, ctx, "social_block", 7) >= 1:
            return []
        candidates = [item for item in _friends(conn, ctx) if item["affinity"] <= BLOCK_AFFINITY
                      and item["interactions"] >= 2]
    if not candidates:
        return []
    candidate = min(candidates, key=lambda item: item["affinity"])
    scene = {
        "kind": "处理一段让你不舒服的关系", "title": "要不要拉黑",
        "meta": f"{candidate['name']} 让你很反感（好感 {candidate['affinity']:+.0f}）",
        "body": "你反复感到被冒犯或消耗。拉黑会让对方从你的好友列表消失；也可以只是暂时不理。",
        "task": "按你的性格决定是否拉黑对方。不要因为一次小摩擦就拉黑，也不要在长期越界后还勉强自己。",
        "output_contract": '只输出 JSON：{"action":"block|skip","target":"student:12","reason":"你的真实理由"}。',
    }
    payload, calls = await _decide(ctx, scene=scene, suffix="social_block")
    action = str(payload.get("action") or "skip").lower()
    if action != "block" or str(payload.get("target") or candidate["identity"]) != candidate["identity"]:
        record_action(ctx, "skip", target_type="social_block", target_id=candidate["identity"], ai_calls=calls,
                      outcome="skipped", detail={"reason": payload.get("reason") or "chose_not_to_block"})
        return [outcome("skip", "skipped", ai_calls=calls)]
    events: list = []
    try:
        from .social_friend_service import block_user
        with ctx.connect() as conn:
            block_user(conn, build_wanderer_user_dict(ctx.wanderer), candidate["identity"], events)
            conn.commit()
            from .wanderer_episodic_memory_service import record_memory
            record_memory(conn, ctx.wanderer, kind="relationship", title=f"我拉黑了{candidate['name']}",
                          content=f"我决定拉黑{candidate['name']}。理由：{str(payload.get('reason') or '')[:300]}",
                          importance=0.9, people=[candidate["identity"]], tags=["冲突", "拉黑"],
                          source_kind="social_block", source_ref=f"{candidate['identity']}:{local_iso()}")
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        record_action(ctx, "social_block", target_type="social_block", target_id=candidate["identity"],
                      ai_calls=calls, outcome="failed", detail={"error": str(exc)[:240]})
        return [outcome("social_block", "failed", ai_calls=calls)]
    record_action(ctx, "social_block", target_type="social_block", target_id=candidate["identity"],
                  ai_calls=calls, content_preview=str(payload.get("reason") or "")[:200], outcome="ok")
    await _push(events)
    return [outcome("social_block", ai_calls=calls)]


async def maybe_unblock(ctx: ActionContext) -> list[dict[str, Any]]:
    if not behavior_on(ctx.wanderer, "social_block"):
        return []
    with ctx.connect() as conn:
        from .social_common import blocked_by
        candidates = []
        for peer in blocked_by(conn, _me(ctx)):
            if not peer.startswith("student:"):
                continue
            rel = conn.execute("SELECT affinity FROM wanderer_relationships WHERE wanderer_id = ? AND peer_identity = ?",
                               (ctx.wanderer_id, peer)).fetchone()
            affinity = float(_row_value(rel, "affinity") or 0) if rel else 0.0
            if affinity > UNBLOCK_AFFINITY:
                candidates.append(peer)
        people = _people(conn, candidates)
    if not candidates:
        return []
    peer = candidates[0]
    name = (people.get(peer) or {}).get("name") or peer
    scene = {
        "kind": "想起一个被你拉黑的人", "title": "要不要解除拉黑",
        "meta": f"你拉黑了{name}，已经过了一段时间", "body": "气消了一些，也可能仍然不想再接触。",
        "task": "按你现在的感受决定是否解除拉黑；解除不代表重新成为好友。",
        "output_contract": '只输出 JSON：{"action":"unblock|skip","target":"student:12","reason":"你的真实理由"}。',
    }
    payload, calls = await _decide(ctx, scene=scene, suffix="social_unblock")
    if str(payload.get("action") or "skip").lower() != "unblock":
        record_action(ctx, "skip", target_type="social_unblock", target_id=peer, ai_calls=calls,
                      outcome="skipped", detail={"reason": payload.get("reason") or ""})
        return [outcome("skip", "skipped", ai_calls=calls)]
    try:
        from .social_friend_service import unblock_user
        with ctx.connect() as conn:
            unblock_user(conn, build_wanderer_user_dict(ctx.wanderer), peer)
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        record_action(ctx, "social_unblock", target_type="social_unblock", target_id=peer, ai_calls=calls,
                      outcome="failed", detail={"error": str(exc)[:240]})
        return [outcome("social_unblock", "failed", ai_calls=calls)]
    record_action(ctx, "social_unblock", target_type="social_unblock", target_id=peer, ai_calls=calls, outcome="ok")
    return [outcome("social_unblock", ai_calls=calls)]


async def maybe_create_group(ctx: ActionContext) -> list[dict[str, Any]]:
    if not behavior_on(ctx.wanderer, "social_group"):
        return []
    with ctx.connect() as conn:
        if _recent_count(conn, ctx, "social_group_create", 7) >= GROUP_CREATE_WEEKLY_LIMIT:
            return []
        friends = _friends(conn, ctx)
    if len(friends) < 2:
        return []
    roster = "；".join(f"{item['identity']}={item['name']}（{item['is_wanderer'] and '散修' or '真实学生'}）"
                      for item in friends[:8])
    scene = {
        "kind": "想和几个朋友一起聊点事", "title": "要不要建一个群",
        "meta": "你最近和几个人聊得来", "body": f"好友：{roster}",
        "task": "按你的性格决定是否建一个小群；如果建，取一个自然的名字，并选 1–4 位好友。",
        "output_contract": (
            '只输出 JSON：{"action":"create|skip","name":"群名","members":["student:1"],"reason":"你的理由"}。'
            "members 只能从上面的好友里选。"),
    }
    payload, calls = await _decide(ctx, scene=scene, suffix="social_group_create")
    if str(payload.get("action") or "skip").lower() != "create":
        record_action(ctx, "skip", target_type="social_group", ai_calls=calls, outcome="skipped",
                      detail={"reason": payload.get("reason") or ""})
        return [outcome("skip", "skipped", ai_calls=calls)]
    allowed = {item["identity"] for item in friends}
    members = [str(item) for item in (payload.get("members") or []) if str(item) in allowed][:4]
    name = str(payload.get("name") or "").strip()[:40]
    if not members or not name:
        record_action(ctx, "skip", target_type="social_group", ai_calls=calls, outcome="skipped",
                      detail={"reason": "invalid_group_draft"})
        return [outcome("skip", "skipped", ai_calls=calls)]
    events: list = []
    try:
        from .social_group_service import create_group
        with ctx.connect() as conn:
            created = create_group(conn, build_wanderer_user_dict(ctx.wanderer), name, members, events)
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        record_action(ctx, "social_group_create", target_type="social_group", ai_calls=calls,
                      outcome="failed", detail={"error": str(exc)[:240]})
        return [outcome("social_group_create", "failed", ai_calls=calls)]
    record_action(ctx, "social_group_create", target_type="social_group", target_id=created.get("id"),
                  ai_calls=calls, content_preview=name, outcome="ok",
                  detail={"skipped": created.get("skipped") or []})
    await _push(events)
    return [outcome("social_group_create", ai_calls=calls, created=created.get("id"),
                    skipped=created.get("skipped") or [])]


async def maybe_invite_group(ctx: ActionContext) -> list[dict[str, Any]]:
    if not behavior_on(ctx.wanderer, "social_group"):
        return []
    me = _me(ctx)
    with ctx.connect() as conn:
        if _recent_count(conn, ctx, "social_group_invite", 1) >= GROUP_INVITE_DAILY_LIMIT:
            return []
        rows = conn.execute(
            "SELECT g.id, g.name, m.role FROM social_group_members m JOIN social_groups g ON g.id = m.group_id "
            "WHERE m.member_identity = ? AND m.left_at IS NULL AND g.dissolved_at IS NULL "
            "AND m.role IN ('owner','admin')", (me,)).fetchall()
        groups = [{"id": int(_row_value(row, "id")), "name": str(_row_value(row, "name") or ""),
                   "role": str(_row_value(row, "role") or "")} for row in rows]
        friends = _friends(conn, ctx)
        member_sets = {}
        for group in groups:
            member_sets[group["id"]] = {str(_row_value(r, "member_identity")) for r in conn.execute(
                "SELECT member_identity FROM social_group_members WHERE group_id = ? AND left_at IS NULL",
                (group["id"],)).fetchall()}
    if not groups:
        return []
    available = {group["id"]: [item for item in friends if item["identity"] not in member_sets[group["id"]]]
                 for group in groups}
    available = {key: value for key, value in available.items() if value}
    if not available:
        return []
    lines = "；".join(f"group:{g['id']}={g['name']}" for g in groups if g["id"] in available)
    friend_line = "；".join(f"{item['identity']}={item['name']}" for item in friends[:8])
    scene = {
        "kind": "想起群里还可以叫谁", "title": "要不要邀请好友进群",
        "meta": "你是其中一些群的群主或管理员", "body": f"群：{lines}\n好友：{friend_line}",
        "task": "按你的判断决定是否邀请一位好友进某个群；不想就不做。",
        "output_contract": '只输出 JSON：{"action":"invite|skip","group_id":1,"members":["student:1"],"reason":"..."}。',
    }
    payload, calls = await _decide(ctx, scene=scene, suffix="social_group_invite")
    if str(payload.get("action") or "skip").lower() != "invite":
        record_action(ctx, "skip", target_type="social_group", ai_calls=calls, outcome="skipped",
                      detail={"reason": payload.get("reason") or ""})
        return [outcome("skip", "skipped", ai_calls=calls)]
    try:
        group_id = int(payload.get("group_id") or 0)
    except (TypeError, ValueError):
        group_id = 0
    if group_id not in available:
        record_action(ctx, "skip", target_type="social_group", ai_calls=calls, outcome="skipped",
                      detail={"reason": "invalid_group"})
        return [outcome("skip", "skipped", ai_calls=calls)]
    allowed = {item["identity"] for item in available[group_id]}
    members = [str(item) for item in (payload.get("members") or []) if str(item) in allowed][:3]
    if not members:
        record_action(ctx, "skip", target_type="social_group", ai_calls=calls, outcome="skipped",
                      detail={"reason": "no_valid_members"})
        return [outcome("skip", "skipped", ai_calls=calls)]
    events: list = []
    try:
        from .social_group_service import add_members
        with ctx.connect() as conn:
            add_members(conn, build_wanderer_user_dict(ctx.wanderer), group_id, members, events)
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        record_action(ctx, "social_group_invite", target_type="social_group", target_id=group_id, ai_calls=calls,
                      outcome="failed", detail={"error": str(exc)[:240]})
        return [outcome("social_group_invite", "failed", ai_calls=calls)]
    record_action(ctx, "social_group_invite", target_type="social_group", target_id=group_id, ai_calls=calls, outcome="ok")
    await _push(events)
    return [outcome("social_group_invite", ai_calls=calls)]


async def maybe_leave_group(ctx: ActionContext) -> list[dict[str, Any]]:
    if not behavior_on(ctx.wanderer, "social_group"):
        return []
    me = _me(ctx)
    with ctx.connect() as conn:
        rows = conn.execute(
            "SELECT g.id, g.name, m.role FROM social_group_members m JOIN social_groups g ON g.id = m.group_id "
            "WHERE m.member_identity = ? AND m.left_at IS NULL AND g.dissolved_at IS NULL", (me,)).fetchall()
        groups = [{"id": int(_row_value(row, "id")), "name": str(_row_value(row, "name") or ""),
                   "role": str(_row_value(row, "role") or "")} for row in rows]
    if not groups:
        return []
    lines = "；".join(f"group:{g['id']}={g['name']}（{g['role']}）" for g in groups)
    scene = {
        "kind": "在一群人的热闹里有点累", "title": "要不要退群",
        "meta": "你在这些群里", "body": lines,
        "task": "按你现在的感受决定退不退；群主不能直接退，需要转让或解散。不要因为一时情绪就退掉重要的群。",
        "output_contract": (
            '只输出 JSON：{"action":"leave|transfer|dissolve|skip","group_id":1,"reason":"..."}。'
            "group_id 必须是上面列出的群。"),
    }
    payload, calls = await _decide(ctx, scene=scene, suffix="social_group_leave")
    action = str(payload.get("action") or "skip").lower()
    try:
        group_id = int(payload.get("group_id") or 0)
    except (TypeError, ValueError):
        group_id = 0
    group = next((item for item in groups if item["id"] == group_id), None)
    if action == "skip" or group is None:
        record_action(ctx, "skip", target_type="social_group", ai_calls=calls, outcome="skipped",
                      detail={"reason": payload.get("reason") or "no_group"})
        return [outcome("skip", "skipped", ai_calls=calls)]
    events: list = []
    try:
        from .social_group_service import dissolve_group, leave_group, transfer_owner
        with ctx.connect() as conn:
            user = build_wanderer_user_dict(ctx.wanderer)
            if group["role"] == "owner":
                members = [str(_row_value(r, "member_identity")) for r in conn.execute(
                    "SELECT member_identity FROM social_group_members WHERE group_id = ? AND left_at IS NULL "
                    "AND member_identity != ? ORDER BY joined_at", (group_id, me)).fetchall()]
                if action == "dissolve" or not members:
                    dissolve_group(conn, user, group_id, events)
                else:
                    transfer_owner(conn, user, group_id, members[0], events)
                    leave_group(conn, user, group_id, events)
            else:
                leave_group(conn, user, group_id, events)
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        record_action(ctx, "social_group_leave", target_type="social_group", target_id=group_id, ai_calls=calls,
                      outcome="failed", detail={"error": str(exc)[:240]})
        return [outcome("social_group_leave", "failed", ai_calls=calls)]
    record_action(ctx, "social_group_leave", target_type="social_group", target_id=group_id, ai_calls=calls,
                  content_preview=group["name"], outcome="ok")
    await _push(events)
    return [outcome("social_group_leave", ai_calls=calls)]
