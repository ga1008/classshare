"""散修 blog actions: browse/like/comment and answering pending replies (design §5.2 items 1-2).

Everything goes through the real platform entry points with the wanderer's
student-shaped user dict (``build_wanderer_user_dict``): ``blog_service.list_posts``
/ ``get_post_detail`` / ``add_comment`` / ``toggle_like`` and
``message_center_service.create_private_message``. Visibility, rate limits, L1
safety rules, content review and notifications therefore apply exactly as they
do for a real student.
"""
from __future__ import annotations

from typing import Any, Optional

from ..time_utils import local_iso
from . import blog_moderation_states as mod_states
from .blog_notifications import notify_new_comment, notify_post_hot
from .blog_service import add_comment, get_post_detail, list_posts, toggle_like
from .message_center_service import _mark_private_conversation_read, create_private_message
from .wanderer_actions_common import (
    ActionContext,
    as_int,
    behavior_on,
    bump_stats,
    counterpart_profile,
    emoji_for,
    interest_score,
    outcome,
    record_action,
    remember,
    run_turn,
    speaker_label,
    turn_parts,
    weighted_choice,
)
from .wanderer_identity_service import build_wanderer_user_dict, load_wanderers_by_student_ids
from .wanderer_memory_service import load_thread

REPLY_NOTIFICATION_CATEGORIES = ("blog_comment", "blog_like")
PENDING_ITEMS_PER_ACTION = 2
FLOOR_CONTEXT_LIMIT = 12
DM_CONTEXT_LIMIT = 10
RECENT_BROWSE_PENALTY = 0.2
_POST_COLUMNS = (
    "id, title, summary, tags_json, section_key, author_identity, author_role, author_user_pk, "
    "author_display_name, author_display_mode, like_count, comment_count, allow_comments"
)


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _thread_key(post_id: int) -> str:
    return f"blog_post:{int(post_id)}"


# ---------------------------------------------------------------------------
# Reading the blog
# ---------------------------------------------------------------------------

def _liked(conn: Any, identity: str, target_type: str, target_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM blog_likes WHERE target_type = ? AND target_id = ? AND user_identity = ? LIMIT 1",
        (target_type, int(target_id), identity),
    ).fetchone()
    return row is not None


def _recently_browsed(conn: Any, wanderer_id: int) -> set[int]:
    rows = conn.execute(
        "SELECT target_id FROM wanderer_action_log WHERE wanderer_id = ? AND action_kind = 'browse_post' "
        "ORDER BY id DESC LIMIT 60",
        (int(wanderer_id),),
    ).fetchall()
    return {as_int(row["target_id"]) for row in rows}


def _candidate_posts(ctx: ActionContext, conn: Any) -> list[dict[str, Any]]:
    user = build_wanderer_user_dict(ctx.wanderer)
    sort = ctx.rng.choice(("latest", "hot", "latest"))
    visible_ids = [int(item["id"]) for item in list_posts(conn, user, sort=sort, limit=20).get("posts") or []]
    if not visible_ids:
        return []
    placeholders = ",".join("?" for _ in visible_ids)
    rows = conn.execute(f"SELECT {_POST_COLUMNS} FROM blog_posts WHERE id IN ({placeholders})", tuple(visible_ids))
    return [_row_dict(row) for row in rows.fetchall() if str(row["author_identity"] or "") != ctx.identity]


def _pick_posts(ctx: ActionContext, conn: Any, posts: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    browsed = _recently_browsed(conn, ctx.wanderer_id)
    interests = ctx.wanderer.get("interests") or []
    pool = list(posts)
    picked: list[dict[str, Any]] = []
    while pool and len(picked) < count:
        weights = [
            (1.0 + interest_score(interests, post.get("title"), post.get("summary"), post.get("tags_json"),
                                  post.get("section_key")))
            * (RECENT_BROWSE_PENALTY if int(post["id"]) in browsed else 1.0)
            for post in pool
        ]
        choice = weighted_choice(ctx.rng, pool, weights)
        picked.append(choice)
        pool = [post for post in pool if post is not choice]
    return picked


def _floor_lines(ctx: ActionContext, conn: Any, post_id: int, *, root_id: Optional[int] = None) -> list[str]:
    where = "post_id = ? AND status = 'active' AND " + mod_states.public_state_sql()
    params: list[Any] = [int(post_id)]
    if root_id:
        where += " AND (id = ? OR root_comment_id = ?)"
        params.extend([int(root_id), int(root_id)])
    rows = [_row_dict(row) for row in conn.execute(
        "SELECT id, author_identity, author_role, author_user_pk, author_display_name, author_display_mode, "
        f"content_md FROM blog_comments WHERE {where} ORDER BY id DESC LIMIT {FLOOR_CONTEXT_LIMIT}",
        tuple(params),
    ).fetchall()]
    student_pks = [row["author_user_pk"] for row in rows if row.get("author_role") == "student"]
    wanderer_ids = set(load_wanderers_by_student_ids(conn, student_pks).keys())
    lines = []
    for row in reversed(rows):
        label = speaker_label(
            own_identity=ctx.identity, identity=str(row.get("author_identity") or ""),
            role=str(row.get("author_role") or ""), user_pk=row.get("author_user_pk"),
            name=str(row.get("author_display_name") or ""),
            real_name=str(row.get("author_display_mode") or "real_name") == "real_name", wanderer_ids=wanderer_ids,
        )
        lines.append(f"{label}：{str(row.get('content_md') or '')[:200]}")
    return lines


def _author_profile(conn: Any, post: dict[str, Any]) -> dict[str, Any]:
    mode = str(post.get("author_display_mode") or "real_name")
    name = "匿名同学" if mode == "anonymous" else str(post.get("author_display_name") or "作者")
    return counterpart_profile(conn, role=str(post.get("author_role") or ""), user_pk=post.get("author_user_pk"),
                               name=name, real_name=mode == "real_name")


def _post_scene(ctx: ActionContext, conn: Any, post: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any]:
    author = _author_profile(conn, post)
    who = f"{author['name']}（散修）" if author.get("is_wanderer") else author["name"]
    return {
        "kind": "博客帖子",
        "title": post.get("title") or "",
        "meta": f"作者：{who} · {as_int(post.get('like_count'))} 赞 · {as_int(post.get('comment_count'))} 条评论",
        "body": detail.get("content_md") or post.get("summary") or "",
        "context_lines": _floor_lines(ctx, conn, int(post["id"])),
        "tags": list(ctx.wanderer.get("interests") or []),
        "counterpart": author,
        "task": "你刚刷到这篇帖子。按你的性格决定：是否点赞（like）、是否评论（不想说就 skip，想说就 reply/emoji/multi）。",
    }


# ---------------------------------------------------------------------------
# Writing to the blog
# ---------------------------------------------------------------------------

def _like_post(ctx: ActionContext, post_id: int) -> Optional[dict[str, Any]]:
    user = build_wanderer_user_dict(ctx.wanderer)
    with ctx.connect() as conn:
        if _liked(conn, ctx.identity, "post", post_id):
            return None
        try:
            toggle_like(conn, user, "post", int(post_id), hot_notify_callback=notify_post_hot)
        except (ValueError, PermissionError) as exc:
            conn.rollback()
            record_action(ctx, "like_post", target_type="blog_post", target_id=post_id, outcome="failed",
                          detail={"error": str(exc)[:200]})
            return outcome("like_post", "failed")
        bump_stats(conn, ctx.wanderer_id, {"likes_given": 1})
        conn.commit()
    record_action(ctx, "like_post", target_type="blog_post", target_id=post_id)
    return outcome("like_post")


async def _post_comments(
    ctx: ActionContext, post_id: int, parts: list[str], *, parent_comment_id: Optional[int], action_kind: str,
    ai_calls: int, mood: str = "",
) -> list[dict[str, Any]]:
    """Publish ``parts`` in order (pausing between them); stop at the first failure."""
    user = build_wanderer_user_dict(ctx.wanderer)
    results: list[dict[str, Any]] = []
    for index, part in enumerate(parts):
        calls = ai_calls if index == 0 else 0
        if index:
            await ctx.pause()
        try:
            with ctx.connect() as conn:
                created = add_comment(conn, user, int(post_id), content_md=part, parent_comment_id=parent_comment_id,
                                      notify_callback=notify_new_comment, hot_notify_callback=notify_post_hot)
                bump_stats(conn, ctx.wanderer_id, {"comments": 1})
                conn.commit()
        except Exception as exc:  # noqa: BLE001 - rate limit / L1 block / closed post: log and stop
            record_action(ctx, action_kind, target_type="blog_post", target_id=post_id, content_preview=part,
                          ai_calls=calls, mood=mood, outcome="failed",
                          detail={"error": f"{type(exc).__name__}: {exc}"[:240], "part": index + 1})
            results.append(outcome(action_kind, "failed", ai_calls=calls))
            break
        record_action(ctx, action_kind, target_type="blog_comment", target_id=created.get("id"),
                      content_preview=part, ai_calls=calls, mood=mood,
                      detail={"post_id": int(post_id), "parent_comment_id": parent_comment_id, "part": index + 1,
                              "parts": len(parts), "moderation_state": created.get("moderation_state")})
        results.append(outcome(action_kind, ai_calls=calls))
    return results


def _heard_post(ctx: ActionContext, post: dict[str, Any]) -> Optional[tuple[str, str]]:
    """The post itself enters the thread memory only the first time the wanderer speaks there."""
    with ctx.connect() as conn:
        thread = load_thread(conn, ctx.wanderer_id, _thread_key(int(post["id"])))
    if thread.get("history"):
        return None
    return (str(post.get("author_display_name") or "楼主"), f"《{post.get('title') or ''}》{post.get('summary') or ''}")


async def _apply_post_turn(ctx: ActionContext, post: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    turn = result.get("turn") or {}
    calls = int(result.get("ai_calls") or 0)
    post_id = int(post["id"])
    done: list[dict[str, Any]] = []
    if turn.get("like") and behavior_on(ctx.wanderer, "blog_like"):
        liked = _like_post(ctx, post_id)
        if liked:
            done.append(liked)
    parts = turn_parts(ctx, turn)
    can_comment = behavior_on(ctx.wanderer, "blog_comment") and as_int(post.get("allow_comments")) == 1
    if turn.get("action") != "skip" and parts and can_comment:
        heard = _heard_post(ctx, post)
        done.extend(await _post_comments(ctx, post_id, parts, parent_comment_id=None, action_kind="comment",
                                         ai_calls=calls, mood=str(turn.get("mood") or "")))
        await remember(ctx, _thread_key(post_id), said=parts, heard=heard,
                       counterpart_identity=str(post.get("author_identity") or ""))
        return done
    record_action(ctx, "skip", target_type="blog_post", target_id=post_id, ai_calls=calls,
                  mood=str(turn.get("mood") or ""), outcome="skipped",
                  detail={"reason": f"turn:{turn.get('action')}", "status": result.get("status")})
    return [*done, outcome("skip", "skipped", ai_calls=calls)]


def _browse_without_ai(ctx: ActionContext, post: dict[str, Any]) -> list[dict[str, Any]]:
    """Capped / comment-off path: maybe like an interesting post, nothing else."""
    score = interest_score(ctx.wanderer.get("interests") or [], post.get("title"), post.get("tags_json"))
    if behavior_on(ctx.wanderer, "blog_like") and score > 0 and ctx.rng.random() < 0.5:
        liked = _like_post(ctx, int(post["id"]))
        if liked:
            return [liked]
    record_action(ctx, "skip", target_type="blog_post", target_id=post["id"], outcome="skipped",
                  detail={"reason": "capped" if ctx.capped else "no_ai_behaviors"})
    return [outcome("skip", "skipped")]


async def browse_blog(ctx: ActionContext) -> list[dict[str, Any]]:
    """Browse 1-3 posts (no AI), then one AI turn on the most interesting one."""
    if not behavior_on(ctx.wanderer, "blog_browse"):
        return []
    count = max(1, min(3, 1 + as_int(ctx.wanderer.get("activity_level")) // 2))
    user = build_wanderer_user_dict(ctx.wanderer)
    with ctx.connect() as conn:
        picked = _pick_posts(ctx, conn, _candidate_posts(ctx, conn), count)
        details = {int(post["id"]): get_post_detail(conn, user, int(post["id"])) for post in picked}
        if picked:
            bump_stats(conn, ctx.wanderer_id, {"posts_browsed": len(picked)})
        conn.commit()
    if not picked:
        record_action(ctx, "skip", target_type="blog", outcome="skipped", detail={"reason": "no_visible_posts"})
        return [outcome("skip", "skipped")]
    done = []
    for post in picked:
        record_action(ctx, "browse_post", target_type="blog_post", target_id=post["id"],
                      content_preview=str(post.get("title") or ""))
        done.append(outcome("browse_post"))
    focus = picked[0]
    wants_ai = behavior_on(ctx.wanderer, "blog_comment") or behavior_on(ctx.wanderer, "blog_like")
    if ctx.capped or not wants_ai:
        return done + _browse_without_ai(ctx, focus)
    with ctx.connect() as conn:
        scene = _post_scene(ctx, conn, focus, details[int(focus["id"])])
    result = await run_turn(ctx, scene=scene, thread_key=_thread_key(int(focus["id"])), suffix="blog_post")
    if result.get("status") == "capped":
        return done + _browse_without_ai(ctx, focus)
    return done + await _apply_post_turn(ctx, focus, result)


# ---------------------------------------------------------------------------
# Pending replies (blog notifications + private messages)
# ---------------------------------------------------------------------------

def pending_blog_notifications(conn: Any, identity: str, limit: int) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in REPLY_NOTIFICATION_CATEGORIES)
    rows = conn.execute(
        "SELECT id, category, ref_type, ref_id, actor_display_name FROM message_center_notifications "
        f"WHERE recipient_identity = ? AND read_at IS NULL AND category IN ({placeholders}) ORDER BY id LIMIT ?",
        (identity, *REPLY_NOTIFICATION_CATEGORIES, int(limit)),
    ).fetchall()
    return [_row_dict(row) for row in rows]


def pending_dm_conversations(conn: Any, identity: str, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT conversation_key, sender_identity, sender_role, sender_user_pk, class_offering_id,
               MAX(id) AS last_id
        FROM private_messages
        WHERE recipient_identity = ? AND read_at IS NULL
        GROUP BY conversation_key, sender_identity, sender_role, sender_user_pk, class_offering_id
        ORDER BY last_id
        LIMIT ?
        """,
        (identity, int(limit)),
    ).fetchall()
    return [_row_dict(row) for row in rows]


def has_pending_work(ctx: ActionContext) -> bool:
    with ctx.connect() as conn:
        return bool(pending_blog_notifications(conn, ctx.identity, 1) or pending_dm_conversations(conn, ctx.identity, 1))


def _mark_notification_read(ctx: ActionContext, notification_id: int) -> None:
    with ctx.connect() as conn:
        conn.execute("UPDATE message_center_notifications SET read_at = ? WHERE id = ? AND read_at IS NULL",
                     (local_iso(), int(notification_id)))
        conn.commit()


def _policy_prob(policy: dict[str, Any], key: str) -> float:
    try:
        return max(0.0, min(1.0, float(policy.get(key) or 0)))
    except (TypeError, ValueError):
        return 0.0


def policy_roll(ctx: ActionContext) -> str:
    """'ignore' | 'emoji' | 'multi' | 'reply' from reply_policy (design §5.2 item 1)."""
    policy = ctx.wanderer.get("reply_policy") or {}
    if ctx.rng.random() < _policy_prob(policy, "ignore_prob"):
        return "ignore"
    if ctx.rng.random() < _policy_prob(policy, "emoji_only_prob"):
        return "emoji"
    return "multi" if ctx.rng.random() < _policy_prob(policy, "multi_part_prob") else "reply"


def _load_comment(conn: Any, comment_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT c.id, c.post_id, c.parent_comment_id, c.root_comment_id, c.author_identity, c.author_role, "
        "c.author_user_pk, c.author_display_name, c.author_display_mode, c.content_md, p.title AS post_title, "
        "p.summary AS post_summary, p.allow_comments FROM blog_comments c JOIN blog_posts p ON p.id = c.post_id "
        f"WHERE c.id = ? AND c.status = 'active' AND {mod_states.public_state_sql('c')} AND p.status = 'published'",
        (int(comment_id),),
    ).fetchone()
    return _row_dict(row)


def _reply_scene(ctx: ActionContext, conn: Any, comment: dict[str, Any], mode: str) -> dict[str, Any]:
    real_name = str(comment.get("author_display_mode") or "real_name") == "real_name"
    counterpart = counterpart_profile(conn, role=str(comment.get("author_role") or ""),
                                      user_pk=comment.get("author_user_pk"),
                                      name=str(comment.get("author_display_name") or ""), real_name=real_name)
    task = "有人在博客里回复或提到了你。按你的性格决定回不回、怎么回。"
    task += "这次你想多说几句，可以用 multi 连发几条短消息。" if mode == "multi" else "这次最多回一条（reply），或者 skip。"
    root = as_int(comment.get("root_comment_id")) or int(comment["id"])
    return {
        "kind": "博客评论回复", "title": comment.get("post_title") or "",
        "meta": f"{counterpart['name']} 在帖子下对你说话", "body": comment.get("post_summary") or "",
        "context_lines": _floor_lines(ctx, conn, int(comment["post_id"]), root_id=root),
        "tags": list(ctx.wanderer.get("interests") or []), "counterpart": counterpart, "task": task,
    }


async def _generate_reply_parts(ctx: ActionContext, *, mode: str, scene_builder, thread_key: str,
                                suffix: str) -> tuple[list[str], int, str, str]:
    """(parts, ai_calls, mood, turn_action). Emoji mode and the capped path cost no AI call."""
    if mode == "emoji" or ctx.capped:
        return [emoji_for(ctx)], 0, "", "emoji"
    with ctx.connect() as conn:
        scene = scene_builder(conn)
    result = await run_turn(ctx, scene=scene, thread_key=thread_key, suffix=suffix)
    turn = result.get("turn") or {}
    parts = turn_parts(ctx, turn)
    if mode != "multi":
        parts = parts[:1]
    action = str(turn.get("action") or "skip")
    return ([] if action == "skip" else parts), int(result.get("ai_calls") or 0), str(turn.get("mood") or ""), action


async def _answer_comment(ctx: ActionContext, note: dict[str, Any]) -> list[dict[str, Any]]:
    with ctx.connect() as conn:
        comment = _load_comment(conn, as_int(note.get("ref_id"))) if note.get("ref_type") == "blog_comment" else {}
    if not comment or comment.get("author_identity") == ctx.identity or not as_int(comment.get("allow_comments")):
        return []
    target = {"target_type": "blog_comment", "target_id": comment["id"]}
    mode = policy_roll(ctx) if behavior_on(ctx.wanderer, "reply") else "disabled"
    if mode in {"ignore", "disabled"}:
        record_action(ctx, "skip", **target, outcome="skipped", detail={"reason": f"{mode}_policy"})
        return [outcome("skip", "skipped")]
    post_id = int(comment["post_id"])
    heard = (str(comment.get("author_display_name") or "对方"), str(comment.get("content_md") or ""))
    parts, calls, mood, action = await _generate_reply_parts(
        ctx, mode=mode, scene_builder=lambda conn: _reply_scene(ctx, conn, comment, mode),
        thread_key=_thread_key(post_id), suffix="blog_reply",
    )
    counterpart_identity = str(comment.get("author_identity") or "")
    if not parts:
        record_action(ctx, "skip", **target, ai_calls=calls, mood=mood, outcome="skipped",
                      detail={"reason": f"turn:{action}"})
        await remember(ctx, _thread_key(post_id), heard=heard, counterpart_identity=counterpart_identity)
        return [outcome("skip", "skipped", ai_calls=calls)]
    done = await _post_comments(ctx, post_id, parts, parent_comment_id=int(comment["id"]), action_kind="reply",
                                ai_calls=calls, mood=mood)
    await remember(ctx, _thread_key(post_id), said=parts, heard=heard, counterpart_identity=counterpart_identity)
    return done


async def _handle_notification(ctx: ActionContext, note: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        if note.get("category") == "blog_like":
            with ctx.connect() as conn:
                bump_stats(conn, ctx.wanderer_id, {"likes_received": 1})
                conn.commit()
            return []
        return await _answer_comment(ctx, note)
    finally:
        # Read even when answering failed, so one broken item never loops forever.
        _mark_notification_read(ctx, int(note["id"]))


def _dm_history(conn: Any, conversation_key: str, own_identity: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT sender_identity, sender_display_name, content FROM private_messages WHERE conversation_key = ? "
        f"ORDER BY id DESC LIMIT {DM_CONTEXT_LIMIT}",
        (conversation_key,),
    ).fetchall()
    return [
        {"self": str(row["sender_identity"]) == own_identity, "name": str(row["sender_display_name"] or ""),
         "text": str(row["content"] or "")}
        for row in reversed(rows)
    ]


def _mark_dm_read(ctx: ActionContext, conv: dict[str, Any]) -> None:
    with ctx.connect() as conn:
        _mark_private_conversation_read(
            conn, current_identity=ctx.identity, current_role="student", current_user_pk=ctx.student_id,
            contact_identity=str(conv["sender_identity"]), class_offering_id=conv.get("class_offering_id"),
        )
        conn.commit()


async def _send_dms(ctx: ActionContext, conv: dict[str, Any], parts: list[str], *, ai_calls: int,
                    mood: str) -> list[dict[str, Any]]:
    user = build_wanderer_user_dict(ctx.wanderer)
    target = {"target_type": "private_message", "target_id": conv["sender_identity"]}
    for index, part in enumerate(parts):
        if index:
            await ctx.pause()
        try:
            with ctx.connect() as conn:
                create_private_message(conn, user, contact_identity=str(conv["sender_identity"]),
                                       class_offering_id=conv.get("class_offering_id"), content=part)
                bump_stats(conn, ctx.wanderer_id, {"dm_replies": 1})
                conn.commit()
        except Exception as exc:  # noqa: BLE001 - blocked / rate limited / contact gone
            record_action(ctx, "dm_reply", **target, content_preview=part, ai_calls=ai_calls, mood=mood,
                          outcome="failed", detail={"error": f"{type(exc).__name__}: {exc}"[:240], "part": index + 1})
            return [outcome("dm_reply", "failed", ai_calls=ai_calls)]
    record_action(ctx, "dm_reply", **target, content_preview=" / ".join(parts), ai_calls=ai_calls, mood=mood,
                  detail={"parts": len(parts)})
    return [outcome("dm_reply", ai_calls=ai_calls)]


def _dm_scene(counterpart: dict[str, Any], history: list[dict[str, Any]], interests: list[Any], mode: str) -> dict[str, Any]:
    return {
        "kind": "私信", "title": f"与 {counterpart['name']} 的私信", "meta": "对方给你发来私信",
        "context_lines": [f"{'你' if item['self'] else item['name']}：{item['text'][:300]}" for item in history],
        "tags": list(interests), "counterpart": counterpart,
        "task": "回复这条私信；不想回就 skip。" + ("可以连发几条（multi）。" if mode == "multi" else "最多一条。"),
    }


async def _answer_dm(ctx: ActionContext, conv: dict[str, Any]) -> list[dict[str, Any]]:
    sender = str(conv.get("sender_identity") or "")
    thread_key = f"dm:{sender}"
    target = {"target_type": "private_message", "target_id": sender}
    with ctx.connect() as conn:
        history = _dm_history(conn, str(conv["conversation_key"]), ctx.identity)
    latest = next((item for item in reversed(history) if not item["self"]), {"name": "对方", "text": ""})
    supported = str(conv.get("sender_role") or "") in {"student", "teacher"}
    mode = policy_roll(ctx) if supported and behavior_on(ctx.wanderer, "dm_reply") else "disabled"
    if mode in {"ignore", "disabled"}:
        record_action(ctx, "skip", **target, outcome="skipped", detail={"reason": f"{mode}_policy"})
        await remember(ctx, thread_key, heard=(latest["name"], latest["text"]), counterpart_identity=sender)
        return [outcome("skip", "skipped")]
    with ctx.connect() as conn:
        counterpart = counterpart_profile(conn, role=str(conv.get("sender_role") or ""),
                                          user_pk=conv.get("sender_user_pk"), name=latest["name"])
    parts, calls, mood, action = await _generate_reply_parts(
        ctx, mode=mode, scene_builder=lambda _conn: _dm_scene(counterpart, history, ctx.wanderer.get("interests") or [], mode),
        thread_key=thread_key, suffix="dm_reply",
    )
    heard = (latest["name"], latest["text"])
    if not parts:
        record_action(ctx, "skip", **target, ai_calls=calls, mood=mood, outcome="skipped",
                      detail={"reason": f"turn:{action}"})
        await remember(ctx, thread_key, heard=heard, counterpart_identity=sender)
        return [outcome("skip", "skipped", ai_calls=calls)]
    done = await _send_dms(ctx, conv, parts, ai_calls=calls, mood=mood)
    await remember(ctx, thread_key, said=parts, heard=heard, counterpart_identity=sender)
    return done


async def handle_pending_replies(ctx: ActionContext) -> list[dict[str, Any]]:
    """Answer up to ``PENDING_ITEMS_PER_ACTION`` waiting items; blog first, then DMs."""
    done: list[dict[str, Any]] = []
    with ctx.connect() as conn:
        notes = pending_blog_notifications(conn, ctx.identity, PENDING_ITEMS_PER_ACTION * 3)
        convs = pending_dm_conversations(conn, ctx.identity, PENDING_ITEMS_PER_ACTION)
    handled = 0
    for note in notes:
        results = await _handle_notification(ctx, note)
        done.extend(results)
        handled += 1 if results else 0
        if handled >= PENDING_ITEMS_PER_ACTION:
            return done
    for conv in convs:
        try:
            done.extend(await _answer_dm(ctx, conv))
        finally:
            _mark_dm_read(ctx, conv)
        handled += 1
        if handled >= PENDING_ITEMS_PER_ACTION:
            break
    return done
