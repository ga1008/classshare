"""散修 public homepage data (``GET /wanderers/{slug}``, docs/wanderer-ai-free-agents-2026-10-25.md §10.3).

Read-only aggregation for any logged-in viewer. Only public facts leave this
module: the persona's public face (name, archetype, self-intro, origin), tier
and counts, its own recent public actions (blog links only to posts that are
still published and public), its public blog posts (through
``blog_service.list_posts`` with the viewer's own visibility), and the 天道
verdict history without internal notes, evidence or admin reasons.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from .blog_persona_service import wanderer_avatar_url, wanderer_badge
from .wanderer_identity_service import STATUS_DEAD, clamp_tier, load_wanderer_by_slug, tier_level

RECENT_ACTIONS_LIMIT = 20
POSTS_LIMIT = 10
REVIEWS_LIMIT = 20
PUBLIC_ACTION_KINDS = ("comment", "reply", "classroom_message", "write_post", "like_post")
PUBLIC_ACTION_LABELS = {
    "comment": "评论了帖子", "reply": "回复了别人", "classroom_message": "在课堂留言",
    "write_post": "发布了帖子", "like_post": "点赞了帖子",
}
# Verdicts shown publicly (internal "noted" audit rows and admin overrides stay private).
PUBLIC_REVIEWS = {
    ("promotion", "approved"): "升阶获准", ("promotion", "rejected"): "升阶被驳回",
    ("discipline", "warning"): "天道警告", ("discipline", "demoted"): "天道降阶",
    ("death", "died"): "陨落", ("revive", "revived"): "复活", ("genesis", "created"): "天道造人",
}
ORIGIN_LABELS = {"seed": "种子散修", "dao": "天道所造"}


def public_interests(wanderer: dict[str, Any]) -> list[str]:
    """The persona's already public interests, shared by homepage and social cards."""
    return [str(item) for item in wanderer.get("interests") or []][:6]


def _row_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    return {key: row[key] for key in row.keys()}


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _count(conn: Any, sql: str, params: tuple[Any, ...]) -> int:
    row = conn.execute(sql, params).fetchone()
    return _as_int(_row_dict(row).get("n")) if row is not None else 0


def _date(value: Any) -> str:
    return str(value or "")[:10]


def _post_id_of(item: dict[str, Any]) -> int:
    try:
        detail = json.loads(item.get("detail_json") or "{}")
    except (TypeError, ValueError):
        detail = {}
    post_id = _as_int(detail.get("post_id") if isinstance(detail, dict) else 0)
    if not post_id and str(item.get("target_type") or "") == "blog_post":
        post_id = _as_int(item.get("target_id"))
    return post_id


def _public_post_ids(conn: Any, post_ids: set[int]) -> set[int]:
    if not post_ids:
        return set()
    ids = sorted(post_ids)
    rows = conn.execute(
        f"SELECT id FROM blog_posts WHERE id IN ({','.join('?' for _ in ids)}) "
        "AND status = 'published' AND visibility = 'public'",
        tuple(ids),
    ).fetchall()
    return {_as_int(_row_dict(row).get("id")) for row in rows}


def recent_public_actions(conn: Any, wanderer_id: int) -> list[dict[str, Any]]:
    marks = ",".join("?" for _ in PUBLIC_ACTION_KINDS)
    rows = [_row_dict(row) for row in conn.execute(
        "SELECT action_kind, target_type, target_id, content_preview, detail_json, created_at "
        f"FROM wanderer_action_log WHERE wanderer_id = ? AND outcome = 'ok' AND action_kind IN ({marks}) "
        f"ORDER BY id DESC LIMIT {RECENT_ACTIONS_LIMIT * 2}",
        (int(wanderer_id), *PUBLIC_ACTION_KINDS),
    ).fetchall()]
    visible = _public_post_ids(conn, {_post_id_of(item) for item in rows} - {0})
    actions = []
    for item in rows:
        kind = str(item.get("action_kind") or "")
        post_id = _post_id_of(item)
        if kind != "classroom_message" and post_id not in visible:
            continue  # the post was removed, withheld or is not public
        actions.append({
            "kind": kind, "label": PUBLIC_ACTION_LABELS.get(kind, kind),
            "preview": str(item.get("content_preview") or "")[:120],
            "href": f"/blog?post={post_id}" if post_id else "",
            "created_at": str(item.get("created_at") or "")[:16].replace("T", " "),
        })
        if len(actions) >= RECENT_ACTIONS_LIMIT:
            break
    return actions


def _stats(conn: Any, wanderer: dict[str, Any]) -> dict[str, Any]:
    wanderer_id, identity = int(wanderer["id"]), f"student:{int(wanderer['student_id'])}"
    return {
        "knowledge": _count(conn, "SELECT COUNT(*) AS n FROM wanderer_knowledge_entries WHERE wanderer_id = ? "
                                  "AND status = 'active'", (wanderer_id,)),
        "actions": _count(conn, "SELECT COUNT(*) AS n FROM wanderer_action_log WHERE wanderer_id = ? "
                                "AND outcome = 'ok' AND action_kind NOT IN ('skip', 'experience_learn')", (wanderer_id,)),
        "comments": _count(conn, "SELECT COUNT(*) AS n FROM blog_comments WHERE author_identity = ? "
                                 "AND status = 'active'", (identity,)),
        "posts": _count(conn, "SELECT COUNT(*) AS n FROM blog_posts WHERE author_identity = ? "
                              "AND status = 'published'", (identity,)),
        "likes_received": _count(
            conn,
            "SELECT COUNT(*) AS n FROM blog_likes WHERE (target_type = 'post' AND target_id IN "
            "(SELECT id FROM blog_posts WHERE author_identity = ?)) OR (target_type = 'comment' AND "
            "target_id IN (SELECT id FROM blog_comments WHERE author_identity = ?))",
            (identity, identity),
        ),
    }


def _public_posts(conn: Any, viewer: dict[str, Any], student_id: int) -> list[dict[str, Any]]:
    from .blog_service import list_posts

    result = list_posts(conn, viewer, author_identity=f"student:{int(student_id)}", limit=POSTS_LIMIT)
    return [
        {"id": _as_int(post.get("id")), "title": str(post.get("title") or ""),
         "summary": str(post.get("summary") or "")[:120], "like_count": _as_int(post.get("like_count")),
         "comment_count": _as_int(post.get("comment_count")), "href": f"/blog?post={_as_int(post.get('id'))}",
         "date": _date(post.get("published_at") or post.get("created_at"))}
        for post in result.get("posts") or [] if str(post.get("visibility") or "public") == "public"
    ]


def public_reviews(conn: Any, wanderer_id: int) -> list[dict[str, Any]]:
    rows = [_row_dict(row) for row in conn.execute(
        "SELECT review_kind, verdict, from_tier, to_tier, reason, created_at FROM wanderer_dao_reviews "
        f"WHERE wanderer_id = ? ORDER BY id DESC LIMIT {REVIEWS_LIMIT * 3}",
        (int(wanderer_id),),
    ).fetchall()]
    reviews = []
    for item in rows:
        label = PUBLIC_REVIEWS.get((str(item.get("review_kind") or ""), str(item.get("verdict") or "")))
        if label is None:
            continue
        reviews.append({"kind": str(item.get("review_kind")), "verdict": str(item.get("verdict")), "label": label,
                        "reason": str(item.get("reason") or "")[:300], "date": _date(item.get("created_at"))})
        if len(reviews) >= REVIEWS_LIMIT:
            break
    return reviews


def build_wanderer_home(conn: Any, viewer: dict[str, Any], slug: str) -> Optional[dict[str, Any]]:
    """The homepage payload, or None when ``slug`` is not a wanderer."""
    clean = str(slug or "").strip()
    if not clean or len(clean) > 40:
        return None
    wanderer = load_wanderer_by_slug(conn, clean)
    if wanderer is None:
        return None
    tier = clamp_tier(wanderer.get("tier"))
    origin = str(wanderer.get("origin") or "seed")
    status = str(wanderer.get("status") or "")
    return {
        "slug": clean,
        "student_id": int(wanderer["student_id"]),
        "display_name": str(wanderer.get("display_name") or ""),
        "archetype": str(wanderer.get("archetype") or ""),
        "intro": str(wanderer.get("public_intro") or ""),
        "interests": public_interests(wanderer),
        "avatar_url": wanderer_avatar_url(wanderer["student_id"]),
        "badge": wanderer_badge(tier),
        "tier": tier,
        "tier_name": str(tier_level(tier).get("name") or "未入道"),
        "cultivation_score": round(float(wanderer.get("cultivation_score") or 0), 1),
        "origin": origin,
        "origin_label": ORIGIN_LABELS.get(origin, origin),
        "born_on": _date(wanderer.get("created_at")),
        "is_dead": status == STATUS_DEAD,
        "status_label": {"dead": "已陨落", "paused": "闭关中"}.get(status, "修行中"),
        "stats": _stats(conn, wanderer),
        "actions": recent_public_actions(conn, int(wanderer["id"])),
        "posts": _public_posts(conn, viewer, int(wanderer["student_id"])),
        "reviews": public_reviews(conn, int(wanderer["id"])),
    }
