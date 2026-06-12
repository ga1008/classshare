"""公文关注 — per-teacher follow settings + match-on-parse notifications.

每位教师可配置：
- 关注项目（语义条目，如「师范认证」「教学比赛」）→ 公文解析完成后调用快速 AI
  判断公文内容（正文 + 附件解析文本）是否与条目相关；
- 关注关键字（硬匹配）→ 解析文本/标题/文号 中出现该字符串即命中。

匹配由后台 worker（统一调度器 ``gongwen_follow_scan`` 任务）在公文解析完成后
执行：``gongwen_documents.reminder_status`` = ``none`` → 待匹配，``done`` → 已匹配，
``skipped`` → 历史公文不回扫，``failed`` → 匹配出错（终态，避免循环重试）。
每篇公文对所有教师的关注项 **合并为一次** 快速 AI 调用（background 优先级），
再按教师拆分命中；关键字命中纯本地完成，AI 不可用时关键字提醒不受影响。

命中写入 ``gongwen_follow_hits``（teacher_id+document_id 唯一，天然去重），
首次命中发送站内通知（category ``gongwen_follow``，重要级 → 自动入邮件队列），
首页「您的关注」与公文列表「我的关注」筛选都从 hits 读取。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any

from ..database import get_db_connection
from ..db.schema_gongwen import ensure_gongwen_schema
from . import material_scope_service as ms

GONGWEN_FOLLOW_TASK_KIND = "gongwen_follow_scan"
GONGWEN_FOLLOW_INTERVAL_SECONDS = 180
FOLLOW_SCAN_BATCH_SIZE = 8
FOLLOW_SCAN_ITEM_DELAY_SECONDS = 0.5
# 重新发现：把当前教师可见的所有已解析公文重新过一遍关注项/关键字。
# 关键字硬匹配用全文；AI 项匹配为控制调用量用「标题+文号+关键词+摘要」摘要体，
# 每次 AI 调用打包 RESCAN_AI_CHUNK_SIZE 篇。
GONGWEN_FOLLOW_RESCAN_TASK_KIND = "gongwen_follow_rescan"
RESCAN_FETCH_CHUNK_SIZE = 100
RESCAN_AI_CHUNK_SIZE = 20
RESCAN_DIGEST_MAX_CHARS = 240
RESCAN_CHUNK_DELAY_SECONDS = 0.3
# 仅匹配「新」公文：发布超过该天数的视为历史公文，标记 skipped 不回扫，
# 避免首次启用时把几百篇旧公文一次性轰炸成提醒。
FOLLOW_SCAN_MAX_AGE_DAYS = 30
FOLLOW_MAX_ITEMS = 20
FOLLOW_MAX_KEYWORDS = 30
FOLLOW_ITEM_MAX_LENGTH = 60
FOLLOW_KEYWORD_MAX_LENGTH = 40
AI_MATCH_TEXT_LIMIT = 6000

MATCH_TYPE_KEYWORD = "keyword"
MATCH_TYPE_AI = "ai"
MATCH_TYPE_BOTH = "both"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe_json_list(raw: Any) -> list[str]:
    try:
        data = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [str(item).strip() for item in data if str(item).strip()]


def _normalize_terms(values: Any, *, max_count: int, max_length: int, label: str) -> list[str]:
    """Validate + dedupe a list of follow terms (boundary validation)."""
    if values is None:
        return []
    if not isinstance(values, (list, tuple)):
        raise ValueError(f"{label}格式无效，应为字符串列表。")
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = " ".join(str(value or "").split()).strip()
        if not text:
            continue
        if len(text) > max_length:
            raise ValueError(f"单个{label}不能超过 {max_length} 个字符：{text[:20]}…")
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(text)
    if len(normalized) > max_count:
        raise ValueError(f"{label}最多保存 {max_count} 个，请精简后再试。")
    return normalized


def get_teacher_display_name(conn, teacher_id: int) -> str:
    """教师姓名（自动加入关键字关注；过短/缺失时返回空串不参与匹配）。"""
    try:
        row = conn.execute("SELECT name FROM teachers WHERE id = ? LIMIT 1", (int(teacher_id),)).fetchone()
    except Exception:  # noqa: BLE001 — 姓名拿不到时退化为只用配置的关键字
        return ""
    name = str(dict(row).get("name") or "").strip() if row else ""
    return name if len(name) >= 2 else ""


def _load_teacher_names(conn, teacher_ids: list[int]) -> dict[int, str]:
    ids = [int(tid) for tid in teacher_ids]
    if not ids:
        return {}
    placeholders = ", ".join("?" for _ in ids)
    try:
        rows = conn.execute(f"SELECT id, name FROM teachers WHERE id IN ({placeholders})", ids).fetchall()
    except Exception:  # noqa: BLE001 — 姓名拿不到时退化为只用配置的关键字
        return {}
    result: dict[int, str] = {}
    for row in rows:
        data = dict(row)
        name = str(data.get("name") or "").strip()
        if len(name) >= 2:
            result[int(data["id"])] = name
    return result


def effective_keywords(keywords: list[str], teacher_name: str) -> list[str]:
    """配置的关键字 + 教师本人姓名（自动、去重）。"""
    merged = list(keywords)
    if teacher_name and teacher_name.lower() not in {k.lower() for k in merged}:
        merged.append(teacher_name)
    return merged


# --------------------------------------------------------------------------- #
# Settings CRUD
# --------------------------------------------------------------------------- #


def get_teacher_follow_settings(conn, teacher_id: int) -> dict[str, Any]:
    ensure_gongwen_schema(conn)
    row = conn.execute(
        "SELECT * FROM teacher_gongwen_follow_settings WHERE teacher_id = ? LIMIT 1",
        (int(teacher_id),),
    ).fetchone()
    if row is None:
        return {"items": [], "keywords": [], "enabled": True, "updated_at": ""}
    data = dict(row)
    return {
        "items": _safe_json_list(data.get("follow_items_json")),
        "keywords": _safe_json_list(data.get("follow_keywords_json")),
        "enabled": bool(data.get("enabled", 1)),
        "updated_at": str(data.get("updated_at") or ""),
    }


def save_teacher_follow_settings(
    conn,
    teacher_id: int,
    *,
    items: Any = None,
    keywords: Any = None,
    enabled: bool = True,
) -> dict[str, Any]:
    """Upsert the teacher's follow settings. Raises ValueError on bad input."""
    ensure_gongwen_schema(conn)
    normalized_items = _normalize_terms(
        items, max_count=FOLLOW_MAX_ITEMS, max_length=FOLLOW_ITEM_MAX_LENGTH, label="关注项目"
    )
    normalized_keywords = _normalize_terms(
        keywords, max_count=FOLLOW_MAX_KEYWORDS, max_length=FOLLOW_KEYWORD_MAX_LENGTH, label="关注关键字"
    )
    now = _now_iso()
    items_json = json.dumps(normalized_items, ensure_ascii=False)
    keywords_json = json.dumps(normalized_keywords, ensure_ascii=False)
    enabled_int = 1 if enabled else 0
    updated = conn.execute(
        "UPDATE teacher_gongwen_follow_settings "
        "SET follow_items_json = ?, follow_keywords_json = ?, enabled = ?, updated_at = ? "
        "WHERE teacher_id = ?",
        (items_json, keywords_json, enabled_int, now, int(teacher_id)),
    )
    if not getattr(updated, "rowcount", 0):
        conn.execute(
            "INSERT INTO teacher_gongwen_follow_settings "
            "(teacher_id, follow_items_json, follow_keywords_json, enabled, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (int(teacher_id), items_json, keywords_json, enabled_int, now, now),
        )
    return {"items": normalized_items, "keywords": normalized_keywords, "enabled": bool(enabled), "updated_at": now}


def _load_active_follow_settings(conn) -> list[dict[str, Any]]:
    """启用了关注的教师（匹配候选）。空配置也算 —— 教师姓名会自动作为关键字。"""
    ensure_gongwen_schema(conn)
    rows = conn.execute(
        "SELECT teacher_id, follow_items_json, follow_keywords_json "
        "FROM teacher_gongwen_follow_settings WHERE enabled = 1"
    ).fetchall()
    result = []
    for row in rows:
        data = dict(row)
        result.append(
            {
                "teacher_id": int(data["teacher_id"]),
                "items": _safe_json_list(data.get("follow_items_json")),
                "keywords": _safe_json_list(data.get("follow_keywords_json")),
            }
        )
    return result


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #


def build_document_match_text(doc: dict[str, Any]) -> str:
    """标题/文号/摘要/关键词 + 解析全文（正文 + 附件文本）= 匹配语料。"""
    chunks = [
        str(doc.get("title") or ""),
        str(doc.get("sn") or ""),
        str(doc.get("parsed_title") or ""),
        str(doc.get("parsed_summary") or ""),
        str(doc.get("parsed_keywords") or ""),
        str(doc.get("keywords") or ""),
        str(doc.get("parsed_text") or ""),
    ]
    return "\n".join(chunk for chunk in chunks if chunk.strip())


def match_keywords(text: str, keywords: list[str]) -> list[str]:
    """硬匹配：大小写不敏感的子串命中。"""
    haystack = (text or "").lower()
    if not haystack:
        return []
    return [keyword for keyword in keywords if keyword and keyword.lower() in haystack]


async def ai_match_follow_items(text: str, items: list[str]) -> dict[str, str]:
    """一次快速 AI 调用判断哪些关注项与公文相关 → {命中条目: 简短理由}。

    AI 不可用 / 输出异常时返回 {}（关键字硬匹配不受影响）。"""
    unique_items = list(dict.fromkeys(item for item in items if item.strip()))
    head = (text or "").strip()
    if not head or not unique_items:
        return {}
    if len(head) > AI_MATCH_TEXT_LIMIT:
        head = head[: AI_MATCH_TEXT_LIMIT - 600] + "\n……\n" + head[-500:]
    item_lines = "\n".join(f"- {item}" for item in unique_items)
    payload = {
        "system_prompt": (
            "你是公文关注匹配助手。给定一篇公文的文本和若干教师关注项，判断公文内容"
            "是否与每个关注项实质相关（主题、对象、活动或要求相关才算，仅出现个别字眼不算）。"
            "只输出 JSON：{\"matches\": [{\"item\": \"命中的关注项原文\", \"reason\": \"30字以内的命中理由\"}]}。"
            "没有命中时输出 {\"matches\": []}。不要编造关注项。"
        ),
        "messages": [],
        "new_message": f"【关注项列表】\n{item_lines}\n\n【公文文本】\n{head}",
        "base64_urls": [],
        "file_texts": [],
        "model_capability": "standard",
        "task_type": "fast_text_response",
        "response_format": "json",
        "task_priority": "background",
        "task_label": "gongwen_follow_match",
    }
    try:
        from ..core import ai_client

        resp = await ai_client.post("/api/ai/chat", json=payload, timeout=60.0)
        resp.raise_for_status()
        data = resp.json()
    except Exception:  # noqa: BLE001 — AI 故障降级为只有关键字匹配
        return {}
    parsed = data.get("response_json") if isinstance(data, dict) else None
    matches = parsed.get("matches") if isinstance(parsed, dict) else None
    if not isinstance(matches, list):
        return {}
    allowed = {item.lower(): item for item in unique_items}
    result: dict[str, str] = {}
    for entry in matches:
        if not isinstance(entry, dict):
            continue
        item = str(entry.get("item") or "").strip()
        canonical = allowed.get(item.lower())
        if canonical:
            result[canonical] = str(entry.get("reason") or "").strip()[:120]
    return result


# --------------------------------------------------------------------------- #
# Hits + notifications
# --------------------------------------------------------------------------- #


def _insert_follow_hit(
    conn,
    *,
    teacher_id: int,
    document_id: int,
    matched_keywords: list[str],
    matched_items: list[str],
    ai_reason: str,
    notified: int = 0,
) -> bool:
    """Insert a hit (idempotent on the unique index). Returns True when new."""
    if matched_keywords and matched_items:
        match_type = MATCH_TYPE_BOTH
    elif matched_items:
        match_type = MATCH_TYPE_AI
    else:
        match_type = MATCH_TYPE_KEYWORD
    from ..db.connection import get_configured_db_engine

    conflict = "ON CONFLICT (teacher_id, document_id) DO NOTHING" if get_configured_db_engine() == "postgres" else ""
    verb = "INSERT" if conflict else "INSERT OR IGNORE"
    cursor = conn.execute(
        f"""
        {verb} INTO gongwen_follow_hits
            (teacher_id, document_id, match_type, matched_keywords_json, matched_items_json,
             ai_reason, notified, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?) {conflict}
        """,
        (
            int(teacher_id),
            int(document_id),
            match_type,
            json.dumps(matched_keywords, ensure_ascii=False),
            json.dumps(matched_items, ensure_ascii=False),
            str(ai_reason or "")[:400],
            int(notified),
            _now_iso(),
        ),
    )
    return bool(getattr(cursor, "rowcount", 0))


def _notify_follow_hit(
    conn,
    *,
    teacher_id: int,
    doc: dict[str, Any],
    matched_keywords: list[str],
    matched_items: list[str],
    ai_reason: str,
) -> None:
    """站内通知（重要级 → 自动进入邮件队列）。"""
    from .message_center_service import (
        MESSAGE_CATEGORY_GONGWEN_FOLLOW,
        _build_notification_payload,
        _insert_notification,
    )

    fragments: list[str] = []
    if matched_keywords:
        fragments.append("命中关键字：" + "、".join(matched_keywords[:6]))
    if matched_items:
        fragments.append("匹配关注项：" + "、".join(matched_items[:6]))
    if ai_reason:
        fragments.append(ai_reason)
    body = "；".join(fragments)[:200] or "该公文与你的关注设置相关。"
    title = f"公文关注提醒：{str(doc.get('title') or '(无标题)')[:80]}"
    payload = _build_notification_payload(
        recipient_role="teacher",
        recipient_user_pk=int(teacher_id),
        category=MESSAGE_CATEGORY_GONGWEN_FOLLOW,
        title=title,
        body_preview=body,
        actor_role="",
        actor_user_pk=None,
        actor_display_name="公文中心",
        link_url=f"/manage/academic/gongwen?follow=1&doc={int(doc['id'])}",
        ref_type="gongwen_document",
        ref_id=str(doc.get("id") or ""),
        metadata={
            "sn": str(doc.get("sn") or ""),
            "author": str(doc.get("author") or ""),
            "matched_keywords": matched_keywords,
            "matched_items": matched_items,
        },
    )
    _insert_notification(conn, payload)
    conn.execute(
        "UPDATE gongwen_follow_hits SET notified = 1 WHERE teacher_id = ? AND document_id = ?",
        (int(teacher_id), int(doc["id"])),
    )


async def match_document_for_followers(document_id: int) -> dict[str, Any]:
    """对一篇已解析公文执行全部教师的关注匹配，写命中并通知。"""
    with get_db_connection() as conn:
        ensure_gongwen_schema(conn)
        row = conn.execute("SELECT * FROM gongwen_documents WHERE id = ? LIMIT 1", (int(document_id),)).fetchone()
        if row is None:
            return {"status": "not_found", "hits": 0}
        doc = dict(row)
        followers = _load_active_follow_settings(conn)

    if not followers:
        return {"status": "no_followers", "hits": 0}

    # 可见性：逐教师按归属/开放范围过滤（与列表页一致）。
    from .organization_scope_service import load_teacher_org_scope

    text = build_document_match_text(doc)
    candidates: list[dict[str, Any]] = []
    with get_db_connection() as conn:
        names = _load_teacher_names(conn, [f["teacher_id"] for f in followers])
        for follower in followers:
            try:
                scope = load_teacher_org_scope(conn, follower["teacher_id"])
            except Exception:  # noqa: BLE001 — 单个教师档案异常不拖垮整批
                continue
            if ms.can_view(doc, scope, is_super_admin=False):
                candidates.append(follower)
    if not candidates:
        return {"status": "no_visible_followers", "hits": 0}

    # 所有候选教师的关注项合并为一次快速 AI 调用，再按教师拆分。
    union_items = [item for follower in candidates for item in follower["items"]]
    ai_matches = await ai_match_follow_items(text, union_items) if union_items else {}

    new_hits = 0
    with get_db_connection() as conn:
        for follower in candidates:
            keywords = effective_keywords(follower["keywords"], names.get(follower["teacher_id"], ""))
            matched_keywords = match_keywords(text, keywords)
            matched_items = [item for item in follower["items"] if item in ai_matches]
            if not matched_keywords and not matched_items:
                continue
            reason = "；".join(filter(None, (ai_matches.get(item, "") for item in matched_items)))[:200]
            inserted = _insert_follow_hit(
                conn,
                teacher_id=follower["teacher_id"],
                document_id=int(document_id),
                matched_keywords=matched_keywords,
                matched_items=matched_items,
                ai_reason=reason,
            )
            if inserted:
                try:
                    _notify_follow_hit(
                        conn,
                        teacher_id=follower["teacher_id"],
                        doc=doc,
                        matched_keywords=matched_keywords,
                        matched_items=matched_items,
                        ai_reason=reason,
                    )
                except Exception as exc:  # noqa: BLE001 — 命中已落库，通知失败不回滚
                    print(f"[GONGWEN-FOLLOW] notify teacher {follower['teacher_id']} failed: {exc}")
                new_hits += 1
        conn.commit()
    return {"status": "done", "hits": new_hits}


# --------------------------------------------------------------------------- #
# Background worker (统一调度器)
# --------------------------------------------------------------------------- #


def _skip_stale_documents(conn) -> int:
    """历史公文（发布超过 FOLLOW_SCAN_MAX_AGE_DAYS 天）不回扫，避免提醒轰炸。"""
    cutoff = (datetime.now() - timedelta(days=FOLLOW_SCAN_MAX_AGE_DAYS)).strftime("%Y-%m-%d")
    cursor = conn.execute(
        "UPDATE gongwen_documents SET reminder_status = 'skipped' "
        "WHERE reminder_status = 'none' AND parsed_status = 'done' "
        "AND publish_time <> '' AND publish_time < ?",
        (cutoff,),
    )
    return int(getattr(cursor, "rowcount", 0) or 0)


def _claim_scan_ids(conn, limit: int) -> list[int]:
    rows = conn.execute(
        "SELECT id FROM gongwen_documents "
        "WHERE reminder_status = 'none' AND parsed_status = 'done' "
        "ORDER BY publish_time DESC, id DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    return [int(dict(r)["id"]) for r in rows]


def _set_reminder_status(conn, document_id: int, status: str) -> None:
    conn.execute(
        "UPDATE gongwen_documents SET reminder_status = ?, updated_at = ? WHERE id = ?",
        (status, _now_iso(), int(document_id)),
    )


async def scan_pending_follow_matches(limit: int = FOLLOW_SCAN_BATCH_SIZE) -> dict[str, Any]:
    """匹配一小批待处理公文（worker 主体）。"""
    with get_db_connection() as conn:
        ensure_gongwen_schema(conn)
        skipped = _skip_stale_documents(conn)
        conn.commit()
        has_followers = bool(_load_active_follow_settings(conn))
        ids = _claim_scan_ids(conn, limit)
        if ids and not has_followers:
            # 没有任何教师配置关注 → 直接标记完成，避免空转。
            for doc_id in ids:
                _set_reminder_status(conn, doc_id, "done")
            conn.commit()
            return {"scanned": len(ids), "hits": 0, "skipped": skipped}

    total_hits = 0
    scanned = 0
    for index, doc_id in enumerate(ids):
        try:
            result = await match_document_for_followers(doc_id)
            total_hits += int(result.get("hits") or 0)
            status = "done"
        except Exception as exc:  # noqa: BLE001 — 终态 failed，避免坏文档阻塞队列
            print(f"[GONGWEN-FOLLOW] scan document {doc_id} failed: {exc}")
            status = "failed"
        with get_db_connection() as conn:
            _set_reminder_status(conn, doc_id, status)
            conn.commit()
        scanned += 1
        if index < len(ids) - 1:
            await asyncio.sleep(FOLLOW_SCAN_ITEM_DELAY_SECONDS)
    return {"scanned": scanned, "hits": total_hits, "skipped": skipped}


async def handle_gongwen_follow_task(task: dict[str, Any]) -> str:
    result = await scan_pending_follow_matches()
    return f"scanned={result['scanned']} hits={result['hits']} skipped={result['skipped']}"


def schedule_gongwen_follow_worker(conn) -> int:
    """Arm the recurring follow-match worker (idempotent, one global task)."""
    from .scheduled_task_service import schedule_task

    run_at = datetime.now() + timedelta(seconds=60)
    return schedule_task(
        conn,
        task_kind=GONGWEN_FOLLOW_TASK_KIND,
        run_at=run_at,
        payload={},
        dedupe_key="gongwen-follow-scan",
        recurrence_seconds=GONGWEN_FOLLOW_INTERVAL_SECONDS,
        owner_role="system",
        title="公文关注匹配",
        replace=True,
    )


# --------------------------------------------------------------------------- #
# 重新发现：把可见的所有已解析公文重新过一遍当前教师的关注设置
# --------------------------------------------------------------------------- #


def _doc_digest(doc: dict[str, Any]) -> str:
    """AI 批量匹配用的公文摘要体（标题/文号/分类/关键词/摘要，控制 token）。"""
    summary = str(doc.get("parsed_summary") or doc.get("summary") or "")[:RESCAN_DIGEST_MAX_CHARS]
    pieces = [
        str(doc.get("title") or ""),
        str(doc.get("sn") or ""),
        str(doc.get("category_name") or ""),
        str(doc.get("parsed_keywords") or doc.get("keywords") or ""),
        summary,
    ]
    return "｜".join(piece.strip() for piece in pieces if piece.strip())


async def ai_match_follow_items_bulk(docs: list[dict[str, Any]], items: list[str]) -> dict[int, dict[str, str]]:
    """一次 AI 调用对多篇公文摘要做关注项匹配 → {doc_id: {命中条目: 理由}}。

    与单篇匹配一样：AI 不可用 / 输出异常返回 {}，编造的 doc/item 被白名单过滤。"""
    unique_items = list(dict.fromkeys(item for item in items if item.strip()))
    if not docs or not unique_items:
        return {}
    item_lines = "\n".join(f"- {item}" for item in unique_items)
    doc_lines = "\n".join(f"[{int(doc['id'])}] {_doc_digest(doc)}" for doc in docs)
    payload = {
        "system_prompt": (
            "你是公文关注匹配助手。给定教师的关注项列表和多篇公文的摘要（每行以 [公文ID] 开头），"
            "判断每篇公文是否与某些关注项实质相关（主题、对象、活动或要求相关才算，仅出现个别字眼不算）。"
            "只输出 JSON：{\"matches\": [{\"doc\": 公文ID数字, \"item\": \"命中的关注项原文\", \"reason\": \"30字以内理由\"}]}。"
            "没有命中时输出 {\"matches\": []}。不要编造公文ID或关注项。"
        ),
        "messages": [],
        "new_message": f"【关注项列表】\n{item_lines}\n\n【公文列表】\n{doc_lines}",
        "base64_urls": [],
        "file_texts": [],
        "model_capability": "standard",
        "task_type": "fast_text_response",
        "response_format": "json",
        "task_priority": "background",
        "task_label": "gongwen_follow_rescan",
    }
    try:
        from ..core import ai_client

        resp = await ai_client.post("/api/ai/chat", json=payload, timeout=90.0)
        resp.raise_for_status()
        data = resp.json()
    except Exception:  # noqa: BLE001 — AI 故障降级为只有关键字匹配
        return {}
    parsed = data.get("response_json") if isinstance(data, dict) else None
    matches = parsed.get("matches") if isinstance(parsed, dict) else None
    if not isinstance(matches, list):
        return {}
    allowed_items = {item.lower(): item for item in unique_items}
    allowed_ids = {int(doc["id"]) for doc in docs}
    result: dict[int, dict[str, str]] = {}
    for entry in matches:
        if not isinstance(entry, dict):
            continue
        try:
            doc_id = int(entry.get("doc"))
        except (TypeError, ValueError):
            continue
        canonical = allowed_items.get(str(entry.get("item") or "").strip().lower())
        if doc_id not in allowed_ids or not canonical:
            continue
        result.setdefault(doc_id, {})[canonical] = str(entry.get("reason") or "").strip()[:120]
    return result


def _notify_rescan_summary(conn, *, teacher_id: int, new_hits: int, scanned: int) -> None:
    """重新发现只发一条汇总提醒（避免几十条命中逐条轰炸站内信/邮箱）。"""
    from .message_center_service import (
        MESSAGE_CATEGORY_GONGWEN_FOLLOW,
        _build_notification_payload,
        _insert_notification,
    )

    payload = _build_notification_payload(
        recipient_role="teacher",
        recipient_user_pk=int(teacher_id),
        category=MESSAGE_CATEGORY_GONGWEN_FOLLOW,
        title=f"公文重新发现完成：新命中 {new_hits} 篇",
        body_preview=f"已重新扫描 {scanned} 篇已解析公文，新发现 {new_hits} 篇与你的关注项/关键字相关，点击查看。",
        actor_role="",
        actor_user_pk=None,
        actor_display_name="公文中心",
        link_url="/manage/academic/gongwen?follow=1",
        ref_type="gongwen_follow_rescan",
        ref_id="",
        metadata={"new_hits": int(new_hits), "scanned": int(scanned)},
    )
    _insert_notification(conn, payload)


async def rescan_teacher_follow_matches(teacher_id: int) -> dict[str, Any]:
    """重新发现：当前教师可见的全部已解析公文 × 关注项/关键字。

    关键字（含自动加入的教师姓名）用全文硬匹配；关注项用 AI 批量摘要匹配
    （每 RESCAN_AI_CHUNK_SIZE 篇一次调用）。命中直接落库（notified=1，
    不逐篇提醒），最后发一条汇总站内信（→ 邮件）。"""
    from .organization_scope_service import load_teacher_org_scope

    with get_db_connection() as conn:
        ensure_gongwen_schema(conn)
        settings = get_teacher_follow_settings(conn, int(teacher_id))
        name = get_teacher_display_name(conn, int(teacher_id))
        keywords = effective_keywords(settings["keywords"], name)
        items = settings["items"]
        if not settings["enabled"] or (not items and not keywords):
            return {"status": "no_settings", "scanned": 0, "new_hits": 0}
        scope = load_teacher_org_scope(conn, int(teacher_id))
        visibility_sql, params = ms.build_visibility_filter(scope, is_super_admin=False)
        id_rows = conn.execute(
            f"SELECT id FROM gongwen_documents WHERE {visibility_sql} AND parsed_status = 'done' "
            "ORDER BY publish_time DESC, id DESC",
            params,
        ).fetchall()
        doc_ids = [int(dict(row)["id"]) for row in id_rows]

    scanned = 0
    new_hits = 0
    for start in range(0, len(doc_ids), RESCAN_FETCH_CHUNK_SIZE):
        chunk_ids = doc_ids[start : start + RESCAN_FETCH_CHUNK_SIZE]
        with get_db_connection() as conn:
            placeholders = ", ".join("?" for _ in chunk_ids)
            rows = conn.execute(
                f"SELECT * FROM gongwen_documents WHERE id IN ({placeholders})", chunk_ids
            ).fetchall()
        docs = [dict(row) for row in rows]

        ai_matches: dict[int, dict[str, str]] = {}
        if items:
            for ai_start in range(0, len(docs), RESCAN_AI_CHUNK_SIZE):
                ai_chunk = docs[ai_start : ai_start + RESCAN_AI_CHUNK_SIZE]
                ai_matches.update(await ai_match_follow_items_bulk(ai_chunk, items))
                await asyncio.sleep(RESCAN_CHUNK_DELAY_SECONDS)

        with get_db_connection() as conn:
            for doc in docs:
                scanned += 1
                matched_keywords = match_keywords(build_document_match_text(doc), keywords)
                doc_ai = ai_matches.get(int(doc["id"]), {})
                matched_items = [item for item in items if item in doc_ai]
                if not matched_keywords and not matched_items:
                    continue
                reason = "；".join(filter(None, (doc_ai.get(item, "") for item in matched_items)))[:200]
                inserted = _insert_follow_hit(
                    conn,
                    teacher_id=int(teacher_id),
                    document_id=int(doc["id"]),
                    matched_keywords=matched_keywords,
                    matched_items=matched_items,
                    ai_reason=reason,
                    notified=1,
                )
                if inserted:
                    new_hits += 1
            conn.commit()

    if new_hits > 0:
        with get_db_connection() as conn:
            try:
                _notify_rescan_summary(conn, teacher_id=int(teacher_id), new_hits=new_hits, scanned=scanned)
                conn.commit()
            except Exception as exc:  # noqa: BLE001 — 命中已落库，汇总通知失败不回滚
                print(f"[GONGWEN-FOLLOW] rescan summary notify teacher {teacher_id} failed: {exc}")
    return {"status": "done", "scanned": scanned, "new_hits": new_hits}


async def handle_gongwen_follow_rescan_task(task: dict[str, Any]) -> str:
    payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
    teacher_id = int(payload.get("teacher_id") or 0)
    if teacher_id <= 0:
        return "skipped: no teacher_id"
    result = await rescan_teacher_follow_matches(teacher_id)
    return f"status={result['status']} scanned={result['scanned']} new_hits={result['new_hits']}"


def schedule_gongwen_follow_rescan(conn, teacher_id: int) -> int:
    """One-shot rescan task（按教师去重，重复点击只保留一个）。"""
    from .scheduled_task_service import schedule_task

    return schedule_task(
        conn,
        task_kind=GONGWEN_FOLLOW_RESCAN_TASK_KIND,
        run_at=datetime.now(),
        payload={"teacher_id": int(teacher_id)},
        dedupe_key=f"gongwen-follow-rescan:{int(teacher_id)}",
        owner_role="teacher",
        owner_user_pk=int(teacher_id),
        title="公文关注重新发现",
        replace=True,
    )


# --------------------------------------------------------------------------- #
# Read interfaces (dashboard「您的关注」 + 列表页「我的关注」)
# --------------------------------------------------------------------------- #


def _hit_payload(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "document_id": int(data["document_id"]),
        "match_type": str(data.get("match_type") or MATCH_TYPE_KEYWORD),
        "matched_keywords": _safe_json_list(data.get("matched_keywords_json")),
        "matched_items": _safe_json_list(data.get("matched_items_json")),
        "ai_reason": str(data.get("ai_reason") or ""),
        "seen": bool(data.get("seen_at")),
        "created_at": str(data.get("created_at") or ""),
    }


def load_follow_hits_for_documents(conn, teacher_id: int, document_ids: list[int]) -> dict[int, dict[str, Any]]:
    """当前页公文的命中信息（列表行徽标用）。"""
    ensure_gongwen_schema(conn)
    ids = [int(doc_id) for doc_id in document_ids]
    if not ids:
        return {}
    placeholders = ", ".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT * FROM gongwen_follow_hits WHERE teacher_id = ? AND document_id IN ({placeholders})",
        [int(teacher_id), *ids],
    ).fetchall()
    return {int(dict(row)["document_id"]): _hit_payload(dict(row)) for row in rows}


def list_teacher_follow_hits(conn, teacher_id: int, *, limit: int = 20) -> list[dict[str, Any]]:
    """最近命中（含公文标题），供「您的关注」展示。"""
    ensure_gongwen_schema(conn)
    rows = conn.execute(
        """
        SELECT h.*, d.title AS doc_title, d.sn AS doc_sn, d.author AS doc_author,
               d.publish_time AS doc_publish_time
        FROM gongwen_follow_hits h
        JOIN gongwen_documents d ON d.id = h.document_id
        WHERE h.teacher_id = ?
        ORDER BY h.created_at DESC, h.id DESC
        LIMIT ?
        """,
        (int(teacher_id), int(limit)),
    ).fetchall()
    result = []
    for row in rows:
        data = dict(row)
        payload = _hit_payload(data)
        payload.update(
            {
                "title": str(data.get("doc_title") or ""),
                "sn": str(data.get("doc_sn") or ""),
                "author": str(data.get("doc_author") or ""),
                "publish_time": str(data.get("doc_publish_time") or ""),
            }
        )
        result.append(payload)
    return result


def count_follow_hits(conn, teacher_id: int) -> dict[str, int]:
    """命中总数 + 未读数（统计板块用）。"""
    ensure_gongwen_schema(conn)
    row = conn.execute(
        "SELECT COUNT(*) AS total, SUM(CASE WHEN seen_at IS NULL THEN 1 ELSE 0 END) AS unseen "
        "FROM gongwen_follow_hits WHERE teacher_id = ?",
        (int(teacher_id),),
    ).fetchone()
    data = dict(row) if row else {}
    return {"total": int(data.get("total") or 0), "unseen": int(data.get("unseen") or 0)}


def count_unseen_follow_hits(conn, teacher_id: int) -> int:
    ensure_gongwen_schema(conn)
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM gongwen_follow_hits WHERE teacher_id = ? AND seen_at IS NULL",
        (int(teacher_id),),
    ).fetchone()
    return int(dict(row)["c"]) if row else 0


def mark_follow_hit_seen(conn, teacher_id: int, document_id: int) -> None:
    ensure_gongwen_schema(conn)
    conn.execute(
        "UPDATE gongwen_follow_hits SET seen_at = ? "
        "WHERE teacher_id = ? AND document_id = ? AND seen_at IS NULL",
        (_now_iso(), int(teacher_id), int(document_id)),
    )
