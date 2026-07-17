from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any

from ..db.connection import get_configured_db_engine


MAX_MEMORY_POSTS = 5
MAX_MEMORY_CANDIDATES = 300
WORD_PATTERN = re.compile(r"[a-zA-Z0-9+#.]{2,}|[\u4e00-\u9fff]{2,}")

SECTION_WRITING_GUIDANCE: dict[str, str] = {
    "general": "从校园日常、成长体验或生活观察切入，重在共鸣和故事感，不要硬装专业。",
    "technology": "讲清科学发现、工程突破和产业影响，区分实验室演示、产品发布与真实落地。",
    "computer": "面向开发、开源、安全和基础设施，解释关键原理、风险与可验证的行动，不堆教程。",
    "ai": "说清能力、边界、证据和治理，明确它能做什么、不能做什么，拒绝把发布会口号当事实。",
    "humanities": "围绕人、历史、社会与文化来叙事，补足语境和多种视角，避免技术圈黑话。",
    "career": "突出单位、对象、地区、截止时间、官方入口和下一步行动，并提醒核验域名与求职诈骗。",
}


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _tokens(*values: Any) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        if isinstance(value, (list, tuple, set)):
            text = " ".join(str(item or "") for item in value)
        else:
            text = str(value or "")
        lowered = text.lower()
        tokens.update(match.group(0) for match in WORD_PATTERN.finditer(lowered))
        chinese = "".join(re.findall(r"[\u4e00-\u9fff]", lowered))
        for size in (2, 3):
            tokens.update(chinese[index : index + size] for index in range(max(0, len(chinese) - size + 1)))
    return {token for token in tokens if token}


def normalize_editorial_profile(
    raw: Any,
    *,
    allowed_sections: set[str],
    fallback_section: str = "general",
) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    section_key = str(data.get("section_key") or fallback_section or "general").strip().lower()
    if section_key not in allowed_sections:
        section_key = fallback_section if fallback_section in allowed_sections else "general"
    keywords: list[str] = []
    seen: set[str] = set()
    raw_keywords = data.get("keywords") if isinstance(data.get("keywords"), list) else []
    for value in raw_keywords:
        keyword = re.sub(r"\s+", " ", str(value or "")).strip()[:40]
        lowered = keyword.lower()
        if len(keyword) < 2 or lowered in seen:
            continue
        seen.add(lowered)
        keywords.append(keyword)
        if len(keywords) >= 8:
            break
    try:
        confidence = float(data.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "topic": re.sub(r"\s+", " ", str(data.get("topic") or "")).strip()[:120],
        "keywords": keywords,
        "section_key": section_key,
        "confidence": max(0.0, min(confidence, 1.0)),
        "reason": re.sub(r"\s+", " ", str(data.get("reason") or "")).strip()[:500],
    }


def upsert_editorial_metadata(
    conn,
    post_id: int,
    profile: dict[str, Any],
    *,
    source_title: str = "",
    source_name: str = "",
    source_url: str = "",
    source_published_at: str = "",
    memory_post_ids: list[int] | None = None,
) -> None:
    params = (
        int(post_id),
        str(profile.get("topic") or ""),
        json.dumps(profile.get("keywords") or [], ensure_ascii=False),
        source_title,
        source_name,
        source_url,
        source_published_at,
        float(profile.get("confidence") or 0.0),
        str(profile.get("reason") or ""),
        json.dumps([int(item) for item in (memory_post_ids or []) if int(item) > 0], ensure_ascii=False),
    )
    sql = """
        INSERT INTO blog_post_editorial_metadata (
            post_id, topic, keywords_json, source_title, source_name, source_url,
            source_published_at, classification_confidence, classification_reason,
            memory_post_ids_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """
    if get_configured_db_engine() == "postgres":
        sql += """
            ON CONFLICT (post_id) DO UPDATE SET
                topic = EXCLUDED.topic,
                keywords_json = EXCLUDED.keywords_json,
                source_title = EXCLUDED.source_title,
                source_name = EXCLUDED.source_name,
                source_url = EXCLUDED.source_url,
                source_published_at = EXCLUDED.source_published_at,
                classification_confidence = EXCLUDED.classification_confidence,
                classification_reason = EXCLUDED.classification_reason,
                memory_post_ids_json = EXCLUDED.memory_post_ids_json,
                updated_at = CURRENT_TIMESTAMP
        """
    else:
        sql += """
            ON CONFLICT(post_id) DO UPDATE SET
                topic = excluded.topic,
                keywords_json = excluded.keywords_json,
                source_title = excluded.source_title,
                source_name = excluded.source_name,
                source_url = excluded.source_url,
                source_published_at = excluded.source_published_at,
                classification_confidence = excluded.classification_confidence,
                classification_reason = excluded.classification_reason,
                memory_post_ids_json = excluded.memory_post_ids_json,
                updated_at = CURRENT_TIMESTAMP
        """
    conn.execute(sql, params)


def find_related_posts(
    conn,
    profile: dict[str, Any],
    *,
    limit: int = MAX_MEMORY_POSTS,
    exclude_post_ids: set[int] | None = None,
) -> list[dict[str, Any]]:
    excluded = {int(value) for value in (exclude_post_ids or set())}
    rows = conn.execute(
        """
        SELECT p.id, p.section_key, p.title, p.content_md, p.created_at,
               p.view_count, p.like_count, p.comment_count, p.bookmark_count,
               m.topic, m.keywords_json, m.source_title, m.source_name,
               m.source_url, m.source_published_at
        FROM blog_posts p
        LEFT JOIN blog_post_editorial_metadata m ON m.post_id = p.id
        WHERE p.status = 'published' AND p.author_role = 'assistant'
        ORDER BY p.created_at DESC, p.id DESC
        LIMIT ?
        """,
        (MAX_MEMORY_CANDIDATES,),
    ).fetchall()
    query_keywords = profile.get("keywords") or []
    query_topic = str(profile.get("topic") or "")
    query_tokens = _tokens(query_topic, query_keywords)
    scored: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        item = _row_dict(row)
        post_id = int(item.get("id") or 0)
        if not post_id or post_id in excluded:
            continue
        item_keywords = _json_list(item.get("keywords_json"))
        title = str(item.get("title") or "")
        topic = str(item.get("topic") or title)
        item_tokens = _tokens(topic, item_keywords, title, str(item.get("content_md") or "")[:3000])
        overlap = len(query_tokens & item_tokens) / max(1, len(query_tokens))
        topic_ratio = SequenceMatcher(None, query_topic.lower(), topic.lower()).ratio() if query_topic else 0.0
        exact_hits = sum(
            1 for keyword in query_keywords
            if str(keyword or "").lower() in f"{title} {topic} {item.get('content_md') or ''}".lower()
        )
        same_section = str(item.get("section_key") or "") == str(profile.get("section_key") or "")
        score = overlap * 64 + topic_ratio * 18 + min(exact_hits, 4) * 5 + (8 if same_section else 0)
        if score > 4:
            item["memory_score"] = round(score, 3)
            item["keywords"] = item_keywords
            scored.append((score, item))
    scored.sort(key=lambda pair: (pair[0], str(pair[1].get("created_at") or "")), reverse=True)
    selected = [item for _, item in scored[: max(0, min(int(limit), MAX_MEMORY_POSTS))]]
    if not selected:
        return []

    selected_ids = [int(item["id"]) for item in selected]
    placeholders = ",".join("?" for _ in selected_ids)
    comments = conn.execute(
        f"""
        SELECT post_id, author_display_name, content_md, like_count, created_at
        FROM blog_comments
        WHERE status = 'active' AND post_id IN ({placeholders})
        ORDER BY created_at ASC, id ASC
        """,
        tuple(selected_ids),
    ).fetchall()
    comments_by_post: dict[int, list[dict[str, Any]]] = {post_id: [] for post_id in selected_ids}
    for row in comments:
        comment = _row_dict(row)
        comments_by_post[int(comment["post_id"])].append(comment)
    for item in selected:
        post_id = int(item["id"])
        item["comments"] = comments_by_post.get(post_id, [])
        item["internal_url"] = f"/blog?section={item.get('section_key') or 'general'}&post={post_id}"
    return selected


def format_memory_for_ai(posts: list[dict[str, Any]]) -> str:
    if not posts:
        return "（暂无足够相关的历史文章。这篇可以独立讲清楚，不要假装有前情。）"
    blocks: list[str] = []
    for rank, post in enumerate(posts, start=1):
        comments = post.get("comments") or []
        comment_text = "\n".join(
            f"  - {comment.get('author_display_name') or '读者'}：{comment.get('content_md') or ''}"
            f"（{int(comment.get('like_count') or 0)} 赞，{comment.get('created_at') or ''}）"
            for comment in comments
        ) or "  - 暂无评论"
        blocks.append(
            "\n".join(
                [
                    f"[记忆 {rank} | post_id={post['id']} | 相关度={post.get('memory_score')} ]",
                    f"标题：{post.get('title') or ''}",
                    f"板块：{post.get('section_key') or 'general'}",
                    f"主题：{post.get('topic') or ''}",
                    f"关键词：{'、'.join(str(item) for item in (post.get('keywords') or []))}",
                    f"系统发布日期：{post.get('created_at') or ''}",
                    f"原始平台：{post.get('source_name') or '未知'}",
                    f"原始文章日期：{post.get('source_published_at') or '未知'}",
                    f"原始链接：{post.get('source_url') or '未知'}",
                    f"站内链接：{post.get('internal_url')}",
                    "互动："
                    f"{int(post.get('view_count') or 0)} 阅读 / {int(post.get('like_count') or 0)} 赞 / "
                    f"{int(post.get('comment_count') or 0)} 评论 / {int(post.get('bookmark_count') or 0)} 收藏",
                    "全文：",
                    str(post.get("content_md") or ""),
                    "本系统评论：",
                    comment_text,
                ]
            )
        )
    return "\n\n===== 下一篇记忆 =====\n\n".join(blocks)


def append_internal_reading_links(
    content_md: str,
    related_posts: list[dict[str, Any]],
    selected_post_ids: list[int],
) -> tuple[str, list[int]]:
    allowed = {int(post["id"]): post for post in related_posts}
    used: list[int] = []
    for raw_id in selected_post_ids:
        post_id = int(raw_id or 0)
        if post_id in allowed and post_id not in used:
            used.append(post_id)
        if len(used) >= 3:
            break
    if not used:
        return content_md.strip(), []
    lines = ["### 接着读"]
    for post_id in used:
        post = allowed[post_id]
        title = str(post.get("title") or "往期文章").replace("[", "［").replace("]", "］")
        lines.append(f"- [{title}]({post.get('internal_url')})")
    return f"{content_md.strip()}\n\n" + "\n".join(lines), used
