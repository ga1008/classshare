"""散修 classroom visits: sit in on a current-semester classroom's 互动区 (design §5.2 item 3, §5.3).

The tick runs in the scheduler container, which has no websocket state, so the
online count and the live broadcast go through the main app's internal
endpoints (``/api/internal/wanderers/rooms`` and ``.../classroom-broadcast``,
token-guarded). Both are best effort: a failed count reads as 0 and a failed
broadcast never undoes the stored message. The message itself is written with
``chat_handler.save_chat_message`` exactly like the websocket path, as the
wanderer's student identity.

Throttle: a wanderer never posts twice in one room within five minutes, so a
multi-part turn is joined into one message.
"""
from __future__ import annotations

import re
import json
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx

from ..time_utils import parse_datetime
from .semester_identity_service import current_identity, parse_semester_identity
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
    speaker_label,
    turn_parts,
    weighted_choice,
)
from .wanderer_identity_service import WANDERER_CLASS_NAME, load_wanderers_by_student_ids
from .wanderer_settings_service import (
    INTERNAL_TOKEN_HEADER,
    classroom_visits_enabled,
    main_app_internal_url,
    wanderer_internal_token,
)

INTERNAL_HTTP_TIMEOUT_SECONDS = 5.0
MESSAGE_THROTTLE_SECONDS = 300
CHAT_CONTEXT_LIMIT = 30
SPEAK_PROBABILITY_IN_CLASS = 0.8
SPEAK_PROBABILITY_IDLE = 0.4
_TIME_RANGE = re.compile(r"(\d{1,2}):(\d{2})\s*[-~–—至]\s*(\d{1,2}):(\d{2})")


# ---------------------------------------------------------------------------
# Internal HTTP (scheduler -> app)
# ---------------------------------------------------------------------------

def _internal_headers() -> dict[str, str]:
    return {INTERNAL_TOKEN_HEADER: wanderer_internal_token()}


async def fetch_online_counts() -> dict[int, int]:
    """``{class_offering_id: online participants}``; any failure reads as no one online."""
    url = f"{main_app_internal_url()}/api/internal/wanderers/rooms"
    try:
        async with httpx.AsyncClient(timeout=INTERNAL_HTTP_TIMEOUT_SECONDS) as client:
            response = await client.get(url, headers=_internal_headers())
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError):
        return {}
    rooms = payload.get("rooms") if isinstance(payload, dict) else None
    counts: dict[int, int] = {}
    for key, value in (rooms or {}).items():
        if as_int(key) > 0:
            counts[as_int(key)] = max(0, as_int(value))
    return counts


async def broadcast_classroom_message(class_offering_id: int, message: dict[str, Any]) -> bool:
    url = f"{main_app_internal_url()}/api/internal/wanderers/classroom-broadcast"
    try:
        async with httpx.AsyncClient(timeout=INTERNAL_HTTP_TIMEOUT_SECONDS) as client:
            response = await client.post(url, headers=_internal_headers(),
                                         json={"class_offering_id": int(class_offering_id), "message": message})
            response.raise_for_status()
    except httpx.HTTPError:
        return False
    return True


# ---------------------------------------------------------------------------
# Choosing a classroom
# ---------------------------------------------------------------------------

def current_offerings(conn: Any) -> list[dict[str, Any]]:
    """Offerings of the current semester (never the wanderers' own placeholder class)."""
    rows = conn.execute(
        """
        SELECT o.id, o.semester, c.name AS course_name, cl.name AS class_name
        FROM class_offerings o
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        WHERE cl.name <> ?
        ORDER BY o.id DESC
        LIMIT 300
        """,
        (WANDERER_CLASS_NAME,),
    ).fetchall()
    code = current_identity().code
    offerings = []
    for row in rows:
        identity = parse_semester_identity(row["semester"])
        if identity is not None and identity.code == code:
            offerings.append(dict(row))
    return offerings


def _pick_offering(ctx: ActionContext, offerings: list[dict[str, Any]], online: dict[int, int]) -> dict[str, Any]:
    interests = ctx.wanderer.get("interests") or []
    weights = [
        1.0 + interest_score(interests, item.get("course_name")) + min(3.0, online.get(int(item["id"]), 0) / 10)
        for item in offerings
    ]
    return weighted_choice(ctx.rng, offerings, weights)


def session_state(conn: Any, class_offering_id: int, now: datetime) -> dict[str, Any]:
    """Best effort: is there a session today, and is it running right now?"""
    rows = conn.execute(
        "SELECT title, academic_time_text FROM class_offering_sessions "
        "WHERE class_offering_id = ? AND session_date = ? ORDER BY order_index",
        (int(class_offering_id), now.date().isoformat()),
    ).fetchall()
    for row in rows:
        match = _TIME_RANGE.search(str(row["academic_time_text"] or ""))
        if not match:
            continue
        start = now.replace(hour=int(match.group(1)), minute=int(match.group(2)), second=0, microsecond=0)
        end = now.replace(hour=int(match.group(3)), minute=int(match.group(4)), second=0, microsecond=0)
        if start <= now <= end:
            return {"today": True, "in_progress": True, "title": str(row["title"] or "")}
    return {"today": bool(rows), "in_progress": False, "title": ""}


def _chat_lines(ctx: ActionContext, conn: Any, class_offering_id: int) -> list[str]:
    rows = [dict(row) for row in conn.execute(
        "SELECT user_id, user_name, user_role, message FROM chat_logs WHERE class_offering_id = ? "
        f"ORDER BY id DESC LIMIT {CHAT_CONTEXT_LIMIT}",
        (int(class_offering_id),),
    ).fetchall()]
    student_ids = [row["user_id"] for row in rows if row.get("user_role") == "student"]
    wanderer_ids = set(load_wanderers_by_student_ids(conn, student_ids).keys())
    lines = []
    for row in reversed(rows):
        role = str(row.get("user_role") or "")
        label = speaker_label(own_identity=ctx.identity, identity=f"{role}:{row.get('user_id')}", role=role,
                              user_pk=row.get("user_id"), name=str(row.get("user_name") or ""), real_name=True,
                              wanderer_ids=wanderer_ids)
        lines.append(f"{label}：{str(row.get('message') or '')[:200]}")
    return lines


def recently_posted(conn: Any, class_offering_id: int, student_id: int, now: datetime) -> bool:
    row = conn.execute(
        "SELECT logged_at FROM chat_logs WHERE class_offering_id = ? AND user_id = ? AND user_role = 'student' "
        "ORDER BY id DESC LIMIT 1",
        (int(class_offering_id), str(student_id)),
    ).fetchone()
    posted_at = parse_datetime(row["logged_at"]) if row is not None else None
    if posted_at is None:
        return False
    return posted_at.replace(tzinfo=None) > now - timedelta(seconds=MESSAGE_THROTTLE_SECONDS)


# ---------------------------------------------------------------------------
# Speaking
# ---------------------------------------------------------------------------

def _status_text(state: dict[str, Any]) -> str:
    if state["in_progress"]:
        return "正在上课" + (f"（{state['title']}）" if state.get("title") else "")
    return "今天有课，但现在不在上课时间" if state["today"] else "今天没有课"


def _classroom_scene(ctx: ActionContext, offering: dict[str, Any], lines: list[str], online: int,
                     state: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "课堂互动区（旁听）",
        "title": f"{offering.get('course_name') or '课程'} · {offering.get('class_name') or ''}",
        "meta": f"当前在线 {online} 人；{_status_text(state)}",
        "context_lines": lines,
        "tags": [*list(ctx.wanderer.get("interests") or []), str(offering.get("course_name") or "")],
        "task": "你在旁听这门课的互动区，你不是这个班的学生。按性格决定要不要留言一句（reply）或 skip；"
                "上课时别刷屏、别跑题，没人说话时也可以安静地走。",
    }


def _support_seen(conn: Any, offering_id: int, ctx: ActionContext) -> int:
    visit = conn.execute("SELECT detail_json FROM wanderer_action_log WHERE wanderer_id=? AND action_kind='classroom_visit' "
                         "AND target_id=? ORDER BY id DESC LIMIT 1", (ctx.wanderer_id, str(offering_id))).fetchone()
    try:
        return int(json.loads(visit["detail_json"] or "{}").get("support_seen_id") or 0) if visit else 0
    except (ValueError, TypeError):
        return 0


def _support_messages(conn: Any, offering_id: int, ctx: ActionContext, *, through_id: int | None = None) -> list[dict]:
    seen = _support_seen(conn, offering_id, ctx)
    bound = " AND id<=?" if through_id is not None else ""
    rows = [dict(r) for r in conn.execute("SELECT id,user_id,user_role,message FROM chat_logs WHERE class_offering_id=? "
        "AND logged_at>=? AND id>?" + bound + " ORDER BY id DESC LIMIT ?",
        [offering_id, (ctx.current_time()-timedelta(minutes=10)).isoformat(timespec="seconds"),
         seen, *([through_id] if through_id is not None else []), CHAT_CONTEXT_LIMIT])]
    bots = load_wanderers_by_student_ids(conn, [r["user_id"] for r in rows if r["user_role"] == "student"])
    return [{"id": r["id"], "body": r["message"], "sender_identity": f"{r['user_role']}:{r['user_id']}",
             "is_wanderer": r["user_role"] == "student" and int(r["user_id"]) in bots}
            for r in reversed(rows) if r["user_role"] in {"student", "teacher"} and str(r.get("user_id") or "").isdigit()]


async def _post_message(ctx: ActionContext, offering_id: int, text: str) -> dict[str, Any]:
    from .chat_handler import save_chat_message

    now = ctx.current_time()
    stored = await save_chat_message(offering_id, {
        "type": "chat", "sender": ctx.display_name, "role": "student", "message": text, "message_type": "text",
        "timestamp": now.strftime("%H:%M"), "class_offering_id": offering_id, "user_id": str(ctx.student_id),
        "logged_at": now.isoformat(timespec="seconds"),
    })
    await broadcast_classroom_message(offering_id, stored)
    return stored


async def _speak(ctx: ActionContext, offering: dict[str, Any], online: int, state: dict[str, Any],
                 lines: list[str], *, prepared_scene: dict | None = None) -> dict[str, Any]:
    offering_id = int(offering["id"])
    thread_key = f"classroom:{offering_id}"
    scene = prepared_scene or _classroom_scene(ctx, offering, lines, online, state)
    if "_support_messages" not in scene:
        with ctx.connect() as conn:
            scene["_support_messages"] = _support_messages(conn, offering_id, ctx)
        scene["_support_user_identity"] = scene["_support_messages"][-1]["sender_identity"] if scene["_support_messages"] else ""
    result = await run_turn(ctx, scene=scene,
                            thread_key=thread_key, suffix="classroom")
    turn = result.get("turn") or {}
    calls = int(result.get("ai_calls") or 0)
    parts = turn_parts(ctx, turn)
    if turn.get("action") == "skip" or not parts:
        await remember(ctx, thread_key)
        return outcome("skip", "skipped", ai_calls=calls)
    text = " ".join(parts)
    try:
        stored = await _post_message(ctx, offering_id, text)
    except Exception as exc:  # noqa: BLE001 - storage failure is logged, the tick goes on
        record_action(ctx, "classroom_message", target_type="classroom", target_id=offering_id,
                      class_offering_id=offering_id, content_preview=text, ai_calls=calls, outcome="failed",
                      detail={"error": f"{type(exc).__name__}: {exc}"[:240]})
        return outcome("classroom_message", "failed", ai_calls=calls, support_pending=bool(result.get("support_fallback")))
    record_action(ctx, "classroom_message", target_type="chat_log", target_id=stored.get("id"),
                  class_offering_id=offering_id, content_preview=text, ai_calls=calls,
                  mood=str(turn.get("mood") or ""), detail={"online": online, "in_progress": state["in_progress"]})
    with ctx.connect() as conn:
        bump_stats(conn, ctx.wanderer_id, {"classroom_messages": 1})
        conn.commit()
    await remember(ctx, thread_key, said=[text])
    return outcome("classroom_message", ai_calls=calls)


async def _maybe_speak(ctx: ActionContext, offering: dict[str, Any], online: int,
                       state: dict[str, Any], *, support_anchor: int | None = None) -> Optional[dict[str, Any]]:
    """None when the wanderer stays silent without asking the model (throttle, cap, switch, mood)."""
    offering_id = int(offering["id"])
    with ctx.connect() as conn:
        throttled = recently_posted(conn, offering_id, ctx.student_id, ctx.current_time())
        lines = _chat_lines(ctx, conn, offering_id)
        messages = _support_messages(conn, offering_id, ctx, through_id=support_anchor)
    scene = _classroom_scene(ctx, offering, lines, online, state)
    scene["_support_messages"] = messages
    scene["_support_user_identity"] = messages[-1]["sender_identity"] if messages else ""
    from .wanderer_student_support import fallback_for_scene
    support = fallback_for_scene(ctx.wanderer, scene, f"classroom:{offering_id}")
    if throttled or not behavior_on(ctx.wanderer, "classroom_message"):
        return outcome("skip", "skipped", reason="classroom_throttled_or_disabled", support_pending=True) if support else None
    if support:
        return await _speak(ctx, offering, online, state, lines, prepared_scene=scene)
    if ctx.capped:
        return None
    speak_probability = SPEAK_PROBABILITY_IN_CLASS if state["in_progress"] else SPEAK_PROBABILITY_IDLE
    if ctx.rng.random() >= speak_probability:
        return None
    return await _speak(ctx, offering, online, state, lines, prepared_scene=scene)


async def visit_classroom(ctx: ActionContext) -> list[dict[str, Any]]:
    """Visit one current-semester classroom; maybe leave one message."""
    with ctx.connect() as conn:
        allowed = classroom_visits_enabled(conn) and behavior_on(ctx.wanderer, "classroom_visit")
        offerings = current_offerings(conn) if allowed else []
    if not allowed:
        return []
    if not offerings:
        record_action(ctx, "skip", target_type="classroom", outcome="skipped", detail={"reason": "no_current_offerings"})
        return [outcome("skip", "skipped")]
    online = await fetch_online_counts()
    offering = _pick_offering(ctx, offerings, online)
    offering_id = int(offering["id"])
    online_count = online.get(offering_id, 0)
    with ctx.connect() as conn:
        state = session_state(conn, offering_id, ctx.current_time())
        support_seen = _support_seen(conn, offering_id, ctx)
        support_anchor = int(conn.execute("SELECT COALESCE(MAX(id),0) AS id FROM chat_logs WHERE class_offering_id=?", (offering_id,)).fetchone()["id"])
        bump_stats(conn, ctx.wanderer_id, {"classroom_visits": 1}, visited_offering=offering_id)
        conn.commit()
    spoken = await _maybe_speak(ctx, offering, online_count, state, support_anchor=support_anchor)
    silent_calls = int(spoken["ai_calls"]) if spoken and spoken["kind"] == "skip" else 0
    record_action(ctx, "classroom_visit", target_type="classroom", target_id=offering_id,
                  class_offering_id=offering_id, content_preview=str(offering.get("course_name") or ""),
                  ai_calls=silent_calls, detail={"online": online_count, "in_progress": state["in_progress"],
                  "support_seen_id": support_seen if spoken and spoken.get("support_pending") else support_anchor})
    done = [outcome("classroom_visit", ai_calls=silent_calls)]
    if spoken and spoken["kind"] != "skip":
        done.append(spoken)
    return done
