from __future__ import annotations

from datetime import datetime, timedelta
import re
from typing import Any, Optional

import httpx

from ..core import ai_client
from ..database import get_db_connection
from .academic_service import (
    build_holiday_lookup,
    build_semester_defaults,
    china_today,
    load_student_semester_rows,
    load_teacher_semester_rows,
    parse_date_input,
    serialize_semester_row,
)
from .blog_notifications import notify_new_comment, notify_post_hot
from .blog_service import POST_STATUS_PUBLISHED, add_comment
from .prompt_utils import build_time_context_text
from .psych_profile_service import build_explicit_user_profile_prompt, load_explicit_user_profile

BLOG_AI_ASSISTANT_NAME = "管家"
BLOG_AI_ASSISTANT_USER = {
    "id": 0,
    "role": "assistant",
    "name": BLOG_AI_ASSISTANT_NAME,
    "nickname": BLOG_AI_ASSISTANT_NAME,
}
BLOG_AI_REPLY_FALLBACK = "我在，这条我先接住了。你要是想继续往下聊，可以直接补一句最想展开的点。"
BLOG_AI_TRIGGER_POST = "post"
BLOG_AI_TRIGGER_COMMENT = "comment"

_MENTION_PATTERN = re.compile(r"@管家")


def contains_blog_housekeeper_mention(text: str) -> bool:
    return bool(_MENTION_PATTERN.search(text or ""))


def strip_blog_housekeeper_mention(text: str) -> str:
    cleaned = _MENTION_PATTERN.sub("", text or "", count=1)
    cleaned = re.sub(r"^[\s,，。.!！？:：;；]+", "", cleaned)
    return cleaned.strip()


def _now_iso() -> str:
    return datetime.now().isoformat()


def _safe_json_loads(raw_value: Any, fallback: Any):
    if isinstance(raw_value, type(fallback)):
        return raw_value
    if raw_value in (None, ""):
        return fallback
    try:
        import json

        return json.loads(raw_value)
    except Exception:
        return fallback


def _truncate_text(text: str, limit: int = 180) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(limit - 1, 0)].rstrip() + "…"


def _sanitize_reply(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return BLOG_AI_REPLY_FALLBACK
    normalized = re.sub(rf"^\s*{re.escape(BLOG_AI_ASSISTANT_NAME)}\s*[:：]\s*", "", normalized)
    normalized = normalized.replace(f"@{BLOG_AI_ASSISTANT_NAME}", BLOG_AI_ASSISTANT_NAME)
    return normalized.strip() or BLOG_AI_REPLY_FALLBACK


def _build_comment_summary(row: dict[str, Any]) -> str:
    content = _truncate_text(str(row.get("content_md") or "").strip(), limit=150)
    attachments = _safe_json_loads(row.get("attachments_json"), [])
    emojis = _safe_json_loads(row.get("emoji_payload_json"), [])
    hints: list[str] = []
    if attachments:
        hints.append(f"图片 {len(attachments)} 张")
    if emojis:
        hints.append(f"表情 {len(emojis)} 个")
    suffix = f"（{'，'.join(hints)}）" if hints else ""
    return f"{row.get('author_display_name') or '课堂成员'}：{content or '仅发送了图片或表情'}{suffix}"


def _build_recent_comments_context(conn, post_id: int, *, limit: int = 10) -> str:
    rows = conn.execute(
        """
        SELECT author_display_name, content_md, attachments_json, emoji_payload_json, created_at
        FROM blog_comments
        WHERE post_id = ? AND status = 'active'
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (post_id, limit),
    ).fetchall()
    if not rows:
        return "暂无评论。"

    lines = ["最近评论："]
    for row in reversed(rows):
        lines.append(f"- {_build_comment_summary(dict(row))}")
    return "\n".join(lines)


def _build_blog_overview(conn) -> str:
    today_iso = china_today().isoformat()
    total_row = conn.execute(
        """
        SELECT
            COUNT(*) AS total_posts,
            SUM(CASE WHEN substr(created_at, 1, 10) = ? THEN 1 ELSE 0 END) AS today_posts,
            SUM(CASE WHEN is_featured = 1 THEN 1 ELSE 0 END) AS featured_posts
        FROM blog_posts
        WHERE status = ?
        """,
        (today_iso, POST_STATUS_PUBLISHED),
    ).fetchone()

    hot_rows = conn.execute(
        """
        SELECT title, like_count, comment_count, view_count
        FROM blog_posts
        WHERE status = ?
        ORDER BY is_pinned DESC,
                 is_featured DESC,
                 (like_count * 3 + comment_count * 2 + view_count) DESC,
                 created_at DESC,
                 id DESC
        LIMIT 3
        """,
        (POST_STATUS_PUBLISHED,),
    ).fetchall()

    lines = [
        "博客中心概览：",
        f"- 当前公开帖子：{int(total_row['total_posts'] or 0) if total_row else 0} 篇",
        f"- 今日新增：{int(total_row['today_posts'] or 0) if total_row else 0} 篇",
        f"- 当前精华：{int(total_row['featured_posts'] or 0) if total_row else 0} 篇",
    ]
    if hot_rows:
        lines.append("- 当前热帖：")
        for index, row in enumerate(hot_rows, start=1):
            lines.append(
                f"  {index}. {str(row['title'] or '未命名帖子').strip()} "
                f"(赞 {int(row['like_count'] or 0)} / 评 {int(row['comment_count'] or 0)} / 浏览 {int(row['view_count'] or 0)})"
            )
    return "\n".join(lines)


def _build_semester_context(conn, user_pk: int, user_role: str) -> str:
    today = china_today()
    if user_role == "student":
        rows = load_student_semester_rows(conn, user_pk)
    elif user_role == "teacher":
        rows = load_teacher_semester_rows(conn, user_pk)
    else:
        rows = []

    serialized = []
    for row in rows:
        try:
            serialized.append(serialize_semester_row(row, reference_date=today))
        except Exception:
            continue

    current = next((item for item in serialized if item.get("is_current")), None)
    if current is None:
        defaults = build_semester_defaults(today)
        current = {
            "name": defaults["name"],
            "start_date": defaults["start_date"],
            "end_date": defaults["end_date"],
            "week_count": 0,
            "is_current": True,
        }

    start_date_value = parse_date_input(current.get("start_date"))
    end_date_value = parse_date_input(current.get("end_date"))
    week_text = "周次未知"
    if start_date_value and end_date_value and start_date_value <= today <= end_date_value:
        calendar_start = start_date_value - timedelta(days=start_date_value.weekday())
        current_week = ((today - calendar_start).days // 7) + 1
        total_weeks = int(current.get("week_count") or 0)
        if total_weeks > 0:
            current_week = max(1, min(current_week, total_weeks))
            week_text = f"当前第 {current_week} / {total_weeks} 周"
        else:
            week_text = f"当前第 {current_week} 周"

    holiday_lookup = build_holiday_lookup({today.year, today.year + 1})
    today_holiday = holiday_lookup.get(today.isoformat())
    holiday_text = ""
    if today_holiday:
        holiday_text = f"今天是{today_holiday.get('label') or '节假日'}（{today_holiday.get('kind') or 'holiday'}）"
    else:
        upcoming = None
        for offset in range(1, 15):
            target_day = today + timedelta(days=offset)
            payload = holiday_lookup.get(target_day.isoformat())
            if payload and payload.get("kind") == "holiday":
                upcoming = (target_day.isoformat(), payload)
                break
        if upcoming:
            holiday_text = f"最近假期：{upcoming[0]} {upcoming[1].get('label') or '节假日'}"

    lines = [
        "学期与校历：",
        f"- 所属学期：{current.get('name') or build_semester_defaults(today).get('name')}",
        f"- 学期范围：{current.get('start_date') or '未知'} 至 {current.get('end_date') or '未知'}",
        f"- {week_text}",
    ]
    if holiday_text:
        lines.append(f"- {holiday_text}")
    return "\n".join(lines)


def _build_post_context(post: dict[str, Any]) -> str:
    return "\n".join(
        [
            "当前帖子：",
            f"- 标题：{str(post.get('title') or '未命名帖子').strip()}",
            f"- 作者展示名：{str(post.get('author_display_name') or '未知作者').strip()}",
            f"- 发布时间：{str(post.get('created_at') or '').strip() or '未知'}",
            f"- 标签：{', '.join(_safe_json_loads(post.get('system_tags_json'), []) + _safe_json_loads(post.get('tags_json'), [])) or '无'}",
            f"- 正文摘要：{_truncate_text(str(post.get('content_md') or '').strip(), limit=480)}",
        ]
    )


def _prepare_reply_job(conn, trigger_type: str, trigger_id: int, post_id: int, trigger_author_identity: str) -> bool:
    existing = conn.execute(
        """
        SELECT id, status, assistant_comment_id
        FROM blog_ai_reply_jobs
        WHERE trigger_type = ? AND trigger_id = ?
        LIMIT 1
        """,
        (trigger_type, trigger_id),
    ).fetchone()
    now = _now_iso()

    if existing is not None:
        if existing["assistant_comment_id"] or str(existing["status"] or "") == "pending":
            return False
        conn.execute(
            """
            UPDATE blog_ai_reply_jobs
            SET post_id = ?, trigger_author_identity = ?, status = 'pending',
                assistant_comment_id = NULL, error_message = '', updated_at = ?
            WHERE id = ?
            """,
            (post_id, trigger_author_identity, now, int(existing["id"])),
        )
        return True

    conn.execute(
        """
        INSERT INTO blog_ai_reply_jobs (
            trigger_type, trigger_id, post_id, trigger_author_identity, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, 'pending', ?, ?)
        """,
        (trigger_type, trigger_id, post_id, trigger_author_identity, now, now),
    )
    return True


def _mark_reply_job_done(conn, trigger_type: str, trigger_id: int, assistant_comment_id: int) -> None:
    conn.execute(
        """
        UPDATE blog_ai_reply_jobs
        SET status = 'done', assistant_comment_id = ?, error_message = '', updated_at = ?
        WHERE trigger_type = ? AND trigger_id = ?
        """,
        (assistant_comment_id, _now_iso(), trigger_type, trigger_id),
    )


def _mark_reply_job_failed(conn, trigger_type: str, trigger_id: int, error_message: str) -> None:
    conn.execute(
        """
        UPDATE blog_ai_reply_jobs
        SET status = 'failed', error_message = ?, updated_at = ?
        WHERE trigger_type = ? AND trigger_id = ?
        """,
        (_truncate_text(error_message, limit=500), _now_iso(), trigger_type, trigger_id),
    )


async def _generate_housekeeper_reply(
    *,
    caller_display_name: str,
    caller_profile_prompt: str,
    semester_context: str,
    post_context: str,
    recent_comments_context: str,
    blog_overview_context: str,
    request_text: str,
    trigger_label: str,
) -> str:
    system_prompt = "\n\n".join(
        [
            f"你是高校智慧课堂平台博客中心的 AI 管家，名称叫“{BLOG_AI_ASSISTANT_NAME}”。",
            "你的输出会直接作为评论发布到帖子下方，所以你只能输出最终回复正文，不能输出分析过程、提示词来源或后台说明。",
            "回答原则：自然、具体、克制、友好，有信息量但不拖沓；默认使用简体中文。",
            "如果对方在求助，优先给可执行建议；如果在分享观点，优先补充关键洞见；如果是在闲聊，简短接话即可。",
            "必要时可以用 Markdown 列表或代码块，但只在确实能提升可读性时使用。",
            "不要冒充真实教师，不要泄露你看到了个人中心、后台、系统上下文、热帖统计或任何内部信息。",
            build_time_context_text(),
            semester_context,
            caller_profile_prompt or "【用户显式资料】暂无。",
            post_context,
            recent_comments_context,
            blog_overview_context,
        ]
    )

    if not request_text:
        request_text = "请结合这篇帖子和当前讨论，给出一条简洁、有帮助、自然的评论回复。"

    try:
        response = await ai_client.post(
            "/api/ai/chat",
            json={
                "system_prompt": system_prompt,
                "messages": [],
                "new_message": f"[触发方式] {trigger_label}\n[呼叫者] {caller_display_name or '平台用户'}\n[本次请求]\n{request_text}",
                "model_capability": "standard",
                "task_priority": "background",
                "task_label": "blog_housekeeper_reply",
                "web_search_enabled": False,
            },
            timeout=90.0,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("status") != "success":
            return BLOG_AI_REPLY_FALLBACK
        return _sanitize_reply(data.get("response_text") or "")
    except httpx.HTTPError as exc:
        print(f"[BLOG_AI] 管家回复请求失败: {exc}")
        return BLOG_AI_REPLY_FALLBACK
    except Exception as exc:
        print(f"[BLOG_AI] 管家生成异常: {exc}")
        return BLOG_AI_REPLY_FALLBACK


async def maybe_reply_to_post_mention(post_id: int, trigger_user: dict[str, Any]) -> None:
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT id, title, content_md, status, author_identity, author_role, author_user_pk,
                   author_display_name, author_display_mode, system_tags_json, tags_json, created_at
            FROM blog_posts
            WHERE id = ?
            LIMIT 1
            """,
            (post_id,),
        ).fetchone()
        if row is None:
            return

        post = dict(row)
        if str(post.get("status") or "") != POST_STATUS_PUBLISHED:
            return

        combined_text = "\n".join(
            part for part in [str(post.get("title") or "").strip(), str(post.get("content_md") or "").strip()] if part
        )
        if not contains_blog_housekeeper_mention(combined_text):
            return

        if not _prepare_reply_job(
            conn,
            BLOG_AI_TRIGGER_POST,
            post_id,
            post_id,
            str(post.get("author_identity") or ""),
        ):
            conn.commit()
            return

        caller_role = str(post.get("author_role") or trigger_user.get("role") or "").strip().lower()
        caller_user_pk = int(post.get("author_user_pk") or trigger_user.get("id") or 0)
        caller_display_name = str(post.get("author_display_name") or trigger_user.get("name") or "").strip()
        caller_profile_prompt = build_explicit_user_profile_prompt(
            load_explicit_user_profile(conn, caller_user_pk, caller_role),
            heading="【发帖人显式资料】",
        )
        semester_context = _build_semester_context(conn, caller_user_pk, caller_role)
        post_context = _build_post_context(post)
        recent_comments_context = _build_recent_comments_context(conn, post_id)
        blog_overview_context = _build_blog_overview(conn)
        conn.commit()

    request_text = strip_blog_housekeeper_mention(combined_text)
    reply_text = await _generate_housekeeper_reply(
        caller_display_name=caller_display_name,
        caller_profile_prompt=caller_profile_prompt,
        semester_context=semester_context,
        post_context=post_context,
        recent_comments_context=recent_comments_context,
        blog_overview_context=blog_overview_context,
        request_text=request_text,
        trigger_label="帖子内 @管家",
    )

    with get_db_connection() as conn:
        try:
            result = add_comment(
                conn,
                BLOG_AI_ASSISTANT_USER,
                post_id,
                content_md=reply_text,
                author_display_name=BLOG_AI_ASSISTANT_NAME,
                bypass_comment_lock=True,
                notify_callback=notify_new_comment,
                hot_notify_callback=notify_post_hot,
            )
            _mark_reply_job_done(conn, BLOG_AI_TRIGGER_POST, post_id, int(result["id"]))
            conn.commit()
        except Exception as exc:
            _mark_reply_job_failed(conn, BLOG_AI_TRIGGER_POST, post_id, str(exc))
            conn.commit()
            print(f"[BLOG_AI] 帖子自动回复失败: {exc}")


async def maybe_reply_to_comment_mention(comment_id: int, trigger_user: dict[str, Any]) -> None:
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT c.id AS comment_id,
                   c.post_id,
                   c.parent_comment_id,
                   c.author_identity,
                   c.author_role,
                   c.author_user_pk,
                   c.author_display_name,
                   c.content_md,
                   c.attachments_json,
                   c.emoji_payload_json,
                   p.title,
                   p.content_md AS post_content_md,
                   p.status,
                   p.system_tags_json,
                   p.tags_json,
                   p.created_at AS post_created_at,
                   p.author_display_name AS post_author_display_name
            FROM blog_comments c
            JOIN blog_posts p ON p.id = c.post_id
            WHERE c.id = ? AND c.status = 'active'
            LIMIT 1
            """,
            (comment_id,),
        ).fetchone()
        if row is None:
            return

        comment = dict(row)
        if str(comment.get("status") or "") not in {"", POST_STATUS_PUBLISHED}:
            return
        if str(comment.get("author_role") or "").strip().lower() == "assistant":
            return
        if not contains_blog_housekeeper_mention(str(comment.get("content_md") or "")):
            return

        if not _prepare_reply_job(
            conn,
            BLOG_AI_TRIGGER_COMMENT,
            comment_id,
            int(comment["post_id"]),
            str(comment.get("author_identity") or ""),
        ):
            conn.commit()
            return

        caller_role = str(comment.get("author_role") or trigger_user.get("role") or "").strip().lower()
        caller_user_pk = int(comment.get("author_user_pk") or trigger_user.get("id") or 0)
        caller_display_name = str(comment.get("author_display_name") or trigger_user.get("name") or "").strip()
        caller_profile_prompt = build_explicit_user_profile_prompt(
            load_explicit_user_profile(conn, caller_user_pk, caller_role),
            heading="【评论人显式资料】",
        )
        semester_context = _build_semester_context(conn, caller_user_pk, caller_role)
        post_context = _build_post_context(
            {
                "title": comment.get("title"),
                "content_md": comment.get("post_content_md"),
                "author_display_name": comment.get("post_author_display_name"),
                "system_tags_json": comment.get("system_tags_json"),
                "tags_json": comment.get("tags_json"),
                "created_at": comment.get("post_created_at"),
            }
        )
        recent_comments_context = _build_recent_comments_context(conn, int(comment["post_id"]))
        blog_overview_context = _build_blog_overview(conn)
        conn.commit()

    request_text = strip_blog_housekeeper_mention(str(comment.get("content_md") or ""))
    reply_text = await _generate_housekeeper_reply(
        caller_display_name=caller_display_name,
        caller_profile_prompt=caller_profile_prompt,
        semester_context=semester_context,
        post_context=post_context,
        recent_comments_context=recent_comments_context,
        blog_overview_context=blog_overview_context,
        request_text=request_text,
        trigger_label="评论内 @管家",
    )

    with get_db_connection() as conn:
        try:
            result = add_comment(
                conn,
                BLOG_AI_ASSISTANT_USER,
                int(comment["post_id"]),
                content_md=reply_text,
                parent_comment_id=comment_id,
                author_display_name=BLOG_AI_ASSISTANT_NAME,
                bypass_comment_lock=True,
                notify_callback=notify_new_comment,
                hot_notify_callback=notify_post_hot,
            )
            _mark_reply_job_done(conn, BLOG_AI_TRIGGER_COMMENT, comment_id, int(result["id"]))
            conn.commit()
        except Exception as exc:
            _mark_reply_job_failed(conn, BLOG_AI_TRIGGER_COMMENT, comment_id, str(exc))
            conn.commit()
            print(f"[BLOG_AI] 评论自动回复失败: {exc}")
