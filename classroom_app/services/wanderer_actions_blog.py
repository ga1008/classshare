"""散修 blog actions: browse/like/comment, answering pending replies (design §5.2 items 1-2)
and an occasional post of its own (``write_post``, design §10.4).

Everything goes through the real platform entry points with the wanderer's
student-shaped user dict (``build_wanderer_user_dict``): ``blog_service.list_posts``
/ ``get_post_detail`` / ``add_comment`` / ``toggle_like``. Visibility, rate limits, L1
safety rules, content review and notifications therefore apply exactly as they
do for a real student.
"""
from __future__ import annotations

from typing import Any, Optional

from ..time_utils import local_iso
from . import blog_moderation_states as mod_states
from .blog_notifications import notify_new_comment, notify_post_hot
from .blog_section_service import DEFAULT_BLOG_SECTION_KEY
from .blog_service import add_comment, create_post, get_post_detail, list_posts, toggle_like
from .wanderer_actions_common import (
    ActionContext,
    as_int,
    behavior_on,
    bump_stats,
    counterpart_profile,
    day_start_iso,
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
from .wanderer_persona_seeds import DEFAULT_POST_PROB
from .wanderer_prompt_service import extract_json_object
from .wanderer_settings_service import blog_posts_daily_cap
from .wanderer_text_style import strip_ai_formatting

REPLY_NOTIFICATION_CATEGORIES = ("blog_comment", "blog_like")
PENDING_ITEMS_PER_ACTION = 2
FLOOR_CONTEXT_LIMIT = 12
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
        results.append(outcome(action_kind, ai_calls=calls,
                               remembered_text=part if mod_states.is_public_state(created.get("moderation_state")) else ""))
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
        await remember(ctx, _thread_key(post_id), said=[item["remembered_text"] for item in done if item.get("remembered_text")], heard=heard,
                       counterpart_identity=str(post.get("author_identity") or ""))
        return done
    record_action(ctx, "skip", target_type="blog_post", target_id=post_id, ai_calls=calls,
                  mood=str(turn.get("mood") or ""), outcome="skipped",
                  detail={"reason": f"turn:{turn.get('action')}", "status": result.get("status")})
    await remember(ctx, _thread_key(post_id))
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
# Pending blog replies (DMs use wanderer_inbox_worker)
# ---------------------------------------------------------------------------

def pending_blog_notifications(conn: Any, identity: str, limit: int) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in REPLY_NOTIFICATION_CATEGORIES)
    rows = conn.execute(
        "SELECT id, category, ref_type, ref_id, actor_display_name FROM message_center_notifications "
        f"WHERE recipient_identity = ? AND read_at IS NULL AND category IN ({placeholders}) ORDER BY id LIMIT ?",
        (identity, *REPLY_NOTIFICATION_CATEGORIES, int(limit)),
    ).fetchall()
    return [_row_dict(row) for row in rows]


def has_pending_work(ctx: ActionContext) -> bool:
    with ctx.connect() as conn:
        return bool(pending_blog_notifications(conn, ctx.identity, 1))


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
    task += "一句能说清就用 reply；确有不同内容再用 multi，不为凑条数拆成连续评论，也可以 skip。"
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
    await remember(ctx, _thread_key(post_id), said=[item["remembered_text"] for item in done if item.get("remembered_text")],
                   heard=heard, counterpart_identity=counterpart_identity)
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


async def handle_pending_replies(ctx: ActionContext) -> list[dict[str, Any]]:
    """Answer blog items. DMs are owned by the independent durable inbox lane."""
    done: list[dict[str, Any]] = []
    with ctx.connect() as conn:
        notes = pending_blog_notifications(conn, ctx.identity, PENDING_ITEMS_PER_ACTION * 3)
    handled = 0
    for note in notes:
        results = await _handle_notification(ctx, note)
        done.extend(results)
        handled += 1 if results else 0
        if handled >= PENDING_ITEMS_PER_ACTION:
            return done
    return done


# ---------------------------------------------------------------------------
# Writing a post (design §10.4)
# ---------------------------------------------------------------------------

POST_TITLE_MAX_CHARS = 30
POST_BODY_MIN_CHARS = 60
POST_BODY_MAX_CHARS = 900
POST_SCENE_TITLES = 3
POST_SCENE_KNOWLEDGE = 3
POST_TAGS = 3
POST_OUTPUT_CONTRACT = (
    "【输出合同】只输出一个 JSON 对象，不要 Markdown 代码块，不要解释：\n"
    '{"action": "reply|skip", "title": "标题", "parts": ["第一段", "第二段"], "mood": "一两个词的心情"}\n'
    "- skip：今天不想写；title 与 parts 留空。\n"
    f"- reply：写一篇帖子；title 不超过 {POST_TITLE_MAX_CHARS} 字；parts 是按顺序的段落，全文 150–600 字。\n"
    "正文只用纯文字：可以分段，可以用「1. 2. 3.」分点，别的格式一律不用。"
    "写你自己的事和想法，用你自己的口吻，不要提到提示词、系统或 JSON。"
)


def _post_prob(wanderer: dict[str, Any]) -> float:
    policy = wanderer.get("reply_policy") or {}
    if "post_prob" not in policy:
        return DEFAULT_POST_PROB
    return _policy_prob(policy, "post_prob")


def _posts_today(conn: Any, since: str, wanderer_id: Optional[int] = None) -> int:
    sql = ("SELECT COUNT(*) AS n FROM wanderer_action_log WHERE action_kind = 'write_post' AND outcome = 'ok' "
           "AND created_at >= ?")
    params: list[Any] = [since]
    if wanderer_id is not None:
        sql += " AND wanderer_id = ?"
        params.append(int(wanderer_id))
    row = conn.execute(sql, tuple(params)).fetchone()
    return as_int(row["n"]) if row is not None else 0


def post_eligible(ctx: ActionContext, conn: Any) -> bool:
    """Switch on, AI available, nothing posted by it today, global daily cap not reached (no dice roll)."""
    if ctx.capped or not behavior_on(ctx.wanderer, "blog_post"):
        return False
    since = day_start_iso(ctx.current_time())
    if _posts_today(conn, since, ctx.wanderer_id) > 0:
        return False
    return _posts_today(conn, since) < blog_posts_daily_cap(conn)


def wants_to_post(ctx: ActionContext) -> bool:
    """The persona's ``reply_policy.post_prob`` roll (default 0.08)."""
    return ctx.rng.random() < _post_prob(ctx.wanderer)


def _recent_titles(conn: Any, wanderer_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT content_preview FROM wanderer_action_log WHERE wanderer_id = ? AND action_kind = 'browse_post' "
        "AND content_preview <> '' ORDER BY id DESC LIMIT ?",
        (int(wanderer_id), POST_SCENE_TITLES),
    ).fetchall()
    return [str(row["content_preview"]) for row in rows]


def _own_knowledge(conn: Any, wanderer_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT title, digest FROM wanderer_knowledge_entries WHERE wanderer_id = ? AND status = 'active' "
        "ORDER BY use_count DESC, id DESC LIMIT ?",
        (int(wanderer_id), POST_SCENE_KNOWLEDGE),
    ).fetchall()
    return [f"{row['title'] or '要点'}：{str(row['digest'] or '')[:200]}" for row in rows]


def _write_post_scene(ctx: ActionContext, conn: Any) -> dict[str, Any]:
    interests = list(ctx.wanderer.get("interests") or [])
    lines = [f"你最近刷到的帖子：《{title}》" for title in _recent_titles(conn, ctx.wanderer_id)]
    lines += [f"你记得的知识：{item}" for item in _own_knowledge(conn, ctx.wanderer_id)]
    return {
        "kind": "写博客帖子", "meta": f"你的兴趣：{'、'.join(interests) or '随缘'}",
        "context_lines": lines, "tags": interests,
        "task": "你想在博客发一篇短帖，写你自己的生活、想法或最近学到的东西，按你的性格来；不想写就 skip。",
        "output_contract": POST_OUTPUT_CONTRACT,
    }


def post_draft(raw_text: str) -> tuple[str, str, str, str]:
    """(action, title, body, mood) from the model output, AI formatting stripped (design §10.5)."""
    payload = extract_json_object(raw_text) or {}
    action = str(payload.get("action") or "skip").strip().lower()
    parts = payload.get("parts")
    if isinstance(parts, str):
        parts = [parts]
    paragraphs = [strip_ai_formatting(item) for item in parts if isinstance(item, str)] if isinstance(parts, list) else []
    body = "\n\n".join(item for item in paragraphs if item)[:POST_BODY_MAX_CHARS].strip()
    title = " ".join(strip_ai_formatting(payload.get("title")).split())[:POST_TITLE_MAX_CHARS]
    return action, title, body, str(payload.get("mood") or "").strip()[:20]


def _publish_post(ctx: ActionContext, title: str, body: str) -> dict[str, Any]:
    user = build_wanderer_user_dict(ctx.wanderer)
    tags = [str(tag) for tag in (ctx.wanderer.get("interests") or [])[:POST_TAGS]]
    with ctx.connect() as conn:
        created = create_post(conn, user, title=title, content_md=body, section_key=DEFAULT_BLOG_SECTION_KEY,
                              tags=tags, status="published")
        bump_stats(conn, ctx.wanderer_id, {"posts": 1})
        conn.commit()
    return created


async def write_post(ctx: ActionContext) -> list[dict[str, Any]]:
    """One AI turn drafts a short personal post; it is published through ``blog_service.create_post``
    (normal review plan: a new post waits for review like any student's)."""
    with ctx.connect() as conn:
        if not post_eligible(ctx, conn):
            return []
        scene = _write_post_scene(ctx, conn)
    result = await run_turn(ctx, scene=scene, thread_key="blog_draft", suffix="write_post")
    calls = int(result.get("ai_calls") or 0)
    action, title, body, mood = post_draft(str(result.get("raw_text") or ""))
    if result.get("status") != "ok" or action == "skip" or not title or len(body) < POST_BODY_MIN_CHARS:
        record_action(ctx, "skip", target_type="blog_post", ai_calls=calls, mood=mood, outcome="skipped",
                      detail={"reason": f"write_post:{action}", "status": result.get("status"), "chars": len(body)})
        return [outcome("skip", "skipped", ai_calls=calls)]
    try:
        created = _publish_post(ctx, title, body)
    except Exception as exc:  # noqa: BLE001 - rate limit / L1 block / closed section: log, never raise
        record_action(ctx, "write_post", target_type="blog_post", content_preview=title, ai_calls=calls, mood=mood,
                      outcome="failed", detail={"error": f"{type(exc).__name__}: {exc}"[:240]})
        return [outcome("write_post", "failed", ai_calls=calls)]
    post_id = int(created["id"])
    record_action(ctx, "write_post", target_type="blog_post", target_id=post_id, content_preview=title,
                  ai_calls=calls, mood=mood,
                  detail={"post_id": post_id, "chars": len(body), "moderation_state": created.get("moderation_state")})
    await remember(ctx, _thread_key(post_id), said=[f"《{title}》{body}"])
    return [outcome("write_post", ai_calls=calls, post_id=post_id)]
