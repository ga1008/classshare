"""公文 AI 检索 — 让 AI 对话和 Agent 能基于校园公文回答问题。

流程（教师对话触发）：
1. ``message_may_mention_gongwen`` 本地正则粗筛（高召回，避免每条消息都打 AI）；
2. ``detect_gongwen_intent`` 快速 AI 判断是否真的在问 公文/学校规定/通知，
   并提炼检索关键词与时间范围（「最近」「上个月」→ recent_months）；
3. ``search_gongwen_for_question`` 在教师可见范围内取候选（关键词 LIKE + 时间过滤
   + 最近兜底），再用快速 AI 按摘要体批量挑出真正相关的公文；
4. ``build_gongwen_context_block`` 把命中公文（含解析正文摘录与链接）拼成
   回答 AI 的上下文块。

AI 任一环节失败都降级：意图判断失败 → 强关键词兜底；相关性筛选失败 →
关键词候选直接进上下文。绝不让公文检索故障拖垮普通对话。

Agent 任务中心通过 ``run_gongwen_retrieval`` 复用同一条链路（公文存放在
``gongwen_documents`` 表，校区共享、按归属/开放范围过滤，页面入口
``/manage/academic/gongwen``）。
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta
from typing import Any

from ..database import get_db_connection
from ..db.schema_gongwen import ensure_gongwen_schema
from . import material_scope_service as ms

# 粗筛正则：宁可多放行（后面还有快速 AI 把关），不可漏掉真正的公文问题。
_GONGWEN_HINT_PATTERN = re.compile(
    r"公文|发文|文号|红头|校发|院发|教学发|教务处|党办|校办|院办"
    r"|通知|通告|公告|文件|规定|办法|细则|条例|制度|章程|实施方案"
    r"|学校.{0,6}(要求|安排|规定|发布|下发|说)"
    r"|学院.{0,6}(要求|安排|规定|发布|下发|说)"
    r"|(申报|评审|评比|立项|报名|遴选|推荐).{0,8}(通知|文件|要求|截止)"
)

# 强信号：意图 AI 不可用时仍按公文问题处理（降级兜底）。
_GONGWEN_STRONG_PATTERN = re.compile(r"公文|发文|文号|红头|校发|院发|教学发")

# 时间词 → 大概率是「最近的公文」类问题，给 AI 的提示里会强调。
_RECENT_PATTERN = re.compile(r"最近|近期|这(几|两)(天|周|个月)|本月|上个月|这学期|本学期")

INTENT_MESSAGE_LIMIT = 600
CANDIDATE_LIMIT = 60
RECENT_FALLBACK_LIMIT = 40
AI_SELECT_CHUNK_SIZE = 20
MAX_RESULT_DOCS = 6
PER_DOC_TEXT_LIMIT = 1600
CONTEXT_BLOCK_LIMIT = 9000
DIGEST_SUMMARY_LIMIT = 240
DEFAULT_RECENT_MONTHS = 3


def message_may_mention_gongwen(message: str) -> bool:
    """本地粗筛：消息里是否可能提及公文/规定/通知。"""
    return bool(_GONGWEN_HINT_PATTERN.search(str(message or "")))


def _strong_gongwen_signal(message: str) -> bool:
    return bool(_GONGWEN_STRONG_PATTERN.search(str(message or "")))


_KEYWORD_SEPARATORS = re.compile(
    r"(?:的|了|吗|呢|啊|吧|请问|帮我|查一下|看看|找找|有没有|有哪些|哪些|什么|怎么|是否"
    r"|关于|最近|近期|相关|内容|要求|这|那|和|与|或|及|在|对|有|个|我|你|他|们"
    r"|老师|学校|学院|公文|通知|文件|规定|红头|发文)"
)


def _extract_local_keywords(message: str) -> list[str]:
    """降级用：按常见虚词/泛化词切分，剩下的词块当检索词（非分词，够用即可）。"""
    text = re.sub(r"\s+", "", str(message or ""))
    picked: list[str] = []
    for chunk in _KEYWORD_SEPARATORS.split(text):
        if not chunk or not re.fullmatch(r"[一-鿿A-Za-z0-9]{2,12}", chunk):
            continue
        if chunk in picked:
            continue
        picked.append(chunk)
        if len(picked) >= 4:
            break
    return picked


async def detect_gongwen_intent(message: str) -> dict[str, Any] | None:
    """快速 AI 判断是否在问公文，并提炼检索词/时间范围。

    返回 ``{"related": True, "query": str, "keywords": [..], "recent_months": int}``，
    与公文无关时返回 ``None``。AI 不可用时按强关键词降级。"""
    cleaned = str(message or "").strip()
    if not cleaned:
        return None
    head = cleaned[:INTENT_MESSAGE_LIMIT]
    today = datetime.now().strftime("%Y-%m-%d")
    payload = {
        "system_prompt": (
            "你是校园公文检索意图识别器。判断教师的这句话是否在询问/查找学校或学院的"
            "公文、红头文件、规章制度、办法细则、官方通知公告、申报评审文件等内容。"
            f"今天是 {today}。只输出 JSON："
            '{"related": true/false, "query": "用于检索的核心问题(20字内)", '
            '"keywords": ["2-4个检索关键词"], "recent_months": 数字}。'
            "recent_months 表示用户限定的时间范围（如「最近的通知」→ 3，「上个月」→ 2，"
            "「这学期」→ 6，未限定时间 → 0）。与公文无关（闲聊、课堂教学、作业批改等）"
            '时输出 {"related": false}。'
        ),
        "messages": [],
        "new_message": head,
        "base64_urls": [],
        "file_texts": [],
        "model_capability": "standard",
        "task_type": "fast_text_response",
        "response_format": "json",
        "task_priority": "interactive",
        "task_label": "gongwen_chat_intent",
    }
    try:
        from ..core import ai_client

        resp = await ai_client.post("/api/ai/chat", json=payload, timeout=30.0)
        resp.raise_for_status()
        data = resp.json()
    except Exception:  # noqa: BLE001 — 意图 AI 故障时按强关键词降级
        if _strong_gongwen_signal(cleaned):
            return {
                "related": True,
                "query": head[:80],
                "keywords": _extract_local_keywords(cleaned),
                "recent_months": DEFAULT_RECENT_MONTHS if _RECENT_PATTERN.search(cleaned) else 0,
                "fallback": True,
            }
        return None

    parsed = data.get("response_json") if isinstance(data, dict) else None
    if not isinstance(parsed, dict) or not parsed.get("related"):
        return None
    keywords = parsed.get("keywords")
    if not isinstance(keywords, list):
        keywords = []
    keywords = [str(item).strip() for item in keywords if str(item or "").strip()][:6]
    try:
        recent_months = max(0, min(int(parsed.get("recent_months") or 0), 24))
    except (TypeError, ValueError):
        recent_months = 0
    if not recent_months and _RECENT_PATTERN.search(cleaned):
        recent_months = DEFAULT_RECENT_MONTHS
    return {
        "related": True,
        "query": str(parsed.get("query") or "").strip()[:80] or head[:80],
        "keywords": keywords or _extract_local_keywords(cleaned),
        "recent_months": recent_months,
        "fallback": False,
    }


# --------------------------------------------------------------------------- #
# 候选获取 + AI 相关性筛选
# --------------------------------------------------------------------------- #

_DOC_COLUMNS = (
    "id, remote_id, sn, title, author, sender_name, category_name, publish_time, "
    "keywords, parsed_status, parsed_title, parsed_summary, parsed_keywords, parsed_text"
)


def _fetch_candidate_documents(
    conn,
    teacher_scope: dict[str, str],
    *,
    is_super_admin: bool,
    keywords: list[str],
    recent_months: int,
) -> list[dict[str, Any]]:
    """教师可见范围内的候选公文：关键词 LIKE 命中优先，不足时补最近公文。"""
    ensure_gongwen_schema(conn)
    visibility_sql, params = ms.build_visibility_filter(teacher_scope, is_super_admin=is_super_admin)

    time_sql = ""
    time_params: list[Any] = []
    if recent_months > 0:
        cutoff = (datetime.now() - timedelta(days=recent_months * 31)).strftime("%Y-%m-%d")
        time_sql = " AND publish_time >= ?"
        time_params.append(cutoff)

    docs: list[dict[str, Any]] = []
    seen: set[int] = set()

    terms = [term for term in keywords if term][:6]
    if terms:
        like_fields = ("title", "sn", "author", "category_name", "keywords", "parsed_keywords", "parsed_summary", "parsed_text")
        term_groups = []
        like_params: list[Any] = []
        for term in terms:
            pattern = f"%{term.lower()}%"
            term_groups.append("(" + " OR ".join(f"lower(COALESCE({field}, '')) LIKE ?" for field in like_fields) + ")")
            like_params.extend([pattern] * len(like_fields))
        keyword_sql = (
            f"SELECT {_DOC_COLUMNS} FROM gongwen_documents "
            f"WHERE {visibility_sql}{time_sql} AND ({' OR '.join(term_groups)}) "
            "ORDER BY publish_time DESC, id DESC LIMIT ?"
        )
        rows = conn.execute(keyword_sql, [*params, *time_params, *like_params, CANDIDATE_LIMIT]).fetchall()
        for row in rows:
            data = dict(row)
            docs.append(data)
            seen.add(int(data["id"]))

    # 关键词命中太少（或没有关键词）时补最近公文，保证「最近有什么通知」也能答。
    if len(docs) < 10:
        recent_sql = (
            f"SELECT {_DOC_COLUMNS} FROM gongwen_documents "
            f"WHERE {visibility_sql}{time_sql} "
            "ORDER BY publish_time DESC, id DESC LIMIT ?"
        )
        rows = conn.execute(recent_sql, [*params, *time_params, RECENT_FALLBACK_LIMIT]).fetchall()
        for row in rows:
            data = dict(row)
            if int(data["id"]) in seen:
                continue
            docs.append(data)
            seen.add(int(data["id"]))
    return docs


def _doc_digest(doc: dict[str, Any]) -> str:
    summary = str(doc.get("parsed_summary") or "")[:DIGEST_SUMMARY_LIMIT]
    pieces = [
        str(doc.get("title") or ""),
        str(doc.get("sn") or ""),
        str(doc.get("author") or ""),
        str(doc.get("category_name") or ""),
        str(doc.get("publish_time") or "")[:10],
        str(doc.get("parsed_keywords") or doc.get("keywords") or ""),
        summary,
    ]
    return "｜".join(piece.strip() for piece in pieces if piece.strip())


async def _ai_select_relevant_chunk(question: str, docs: list[dict[str, Any]]) -> dict[int, str]:
    doc_lines = "\n".join(f"[{int(doc['id'])}] {_doc_digest(doc)}" for doc in docs)
    payload = {
        "system_prompt": (
            "你是公文检索助手。给定教师的问题和多篇公文的摘要（每行以 [公文ID] 开头，"
            "字段为 标题｜文号｜发文单位｜分类｜发布日期｜关键词｜摘要），挑出最可能"
            "回答该问题的公文（主题、对象、时间、活动实质相关才算）。只输出 JSON："
            '{"matches": [{"doc": 公文ID数字, "reason": "30字以内的相关理由"}]}。'
            '没有相关公文时输出 {"matches": []}。不要编造公文ID。'
        ),
        "messages": [],
        "new_message": f"【教师的问题】\n{question}\n\n【公文列表】\n{doc_lines}",
        "base64_urls": [],
        "file_texts": [],
        "model_capability": "standard",
        "task_type": "fast_text_response",
        "response_format": "json",
        "task_priority": "interactive",
        "task_label": "gongwen_chat_select",
    }
    try:
        from ..core import ai_client

        resp = await ai_client.post("/api/ai/chat", json=payload, timeout=60.0)
        resp.raise_for_status()
        data = resp.json()
    except Exception:  # noqa: BLE001 — 筛选 AI 故障时由调用方降级
        return {}
    parsed = data.get("response_json") if isinstance(data, dict) else None
    matches = parsed.get("matches") if isinstance(parsed, dict) else None
    if not isinstance(matches, list):
        return {}
    allowed = {int(doc["id"]) for doc in docs}
    result: dict[int, str] = {}
    for entry in matches:
        if not isinstance(entry, dict):
            continue
        try:
            doc_id = int(entry.get("doc"))
        except (TypeError, ValueError):
            continue
        if doc_id in allowed:
            result[doc_id] = str(entry.get("reason") or "").strip()[:120]
    return result


async def ai_select_relevant_documents(question: str, docs: list[dict[str, Any]]) -> dict[int, str]:
    """快速 AI 按摘要体批量挑相关公文 → {doc_id: 理由}；AI 故障返回 {}。"""
    if not docs or not str(question or "").strip():
        return {}
    result: dict[int, str] = {}
    for start in range(0, len(docs), AI_SELECT_CHUNK_SIZE):
        chunk = docs[start : start + AI_SELECT_CHUNK_SIZE]
        result.update(await _ai_select_relevant_chunk(question, chunk))
        if start + AI_SELECT_CHUNK_SIZE < len(docs):
            await asyncio.sleep(0.2)
    return result


# --------------------------------------------------------------------------- #
# 上下文块
# --------------------------------------------------------------------------- #


def build_gongwen_context_block(docs: list[dict[str, Any]], *, intent: dict[str, Any] | None = None) -> str:
    """命中公文 → 回答 AI 的上下文文本（含解析正文摘录与平台链接）。"""
    if not docs:
        return ""
    sections: list[str] = []
    budget = CONTEXT_BLOCK_LIMIT
    for index, doc in enumerate(docs, start=1):
        if budget <= 0:
            break
        header_bits = [
            f"《{str(doc.get('title') or '(无标题)')}》",
            str(doc.get("sn") or ""),
            f"发文单位：{doc.get('author')}" if doc.get("author") else "",
            f"分类：{doc.get('category_name')}" if doc.get("category_name") else "",
            f"发布时间：{str(doc.get('publish_time') or '')[:10]}" if doc.get("publish_time") else "",
        ]
        lines = [f"### 公文{index}：" + " ".join(bit for bit in header_bits if bit)]
        if doc.get("relevance_reason"):
            lines.append(f"相关性：{doc['relevance_reason']}")
        summary = str(doc.get("parsed_summary") or "").strip()
        if summary:
            lines.append(f"摘要：{summary[:300]}")
        text = str(doc.get("parsed_text") or "").strip()
        if text:
            excerpt = text[: min(PER_DOC_TEXT_LIMIT, budget)]
            lines.append(f"正文摘录：\n{excerpt}")
        elif str(doc.get("parsed_status") or "") != "done":
            lines.append("（该公文尚未完成解析，只有标题与元数据可用。）")
        lines.append(f"平台链接：/manage/academic/gongwen?doc={int(doc['id'])}")
        section = "\n".join(lines)
        budget -= len(section)
        sections.append(section)

    time_note = ""
    if intent and int(intent.get("recent_months") or 0) > 0:
        time_note = f"（检索范围：最近 {int(intent['recent_months'])} 个月）"
    return (
        "--- 校园公文检索结果 ---\n"
        f"以下是平台公文库中与用户问题可能相关的公文{time_note}，按发布时间倒序。"
        "请基于这些公文内容回答；如内容不足以回答，请如实说明并提示用户到 公文中心"
        "（/manage/academic/gongwen）查看原文。引用公文时给出标题和文号，并附上平台链接。\n\n"
        + "\n\n".join(sections)
    )


def _doc_public_payload(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(doc["id"]),
        "title": str(doc.get("title") or ""),
        "sn": str(doc.get("sn") or ""),
        "author": str(doc.get("author") or ""),
        "category": str(doc.get("category_name") or ""),
        "publish_time": str(doc.get("publish_time") or "")[:10],
        "summary": str(doc.get("parsed_summary") or "")[:300],
        "relevance_reason": str(doc.get("relevance_reason") or ""),
        "url": f"/manage/academic/gongwen?doc={int(doc['id'])}",
    }


# --------------------------------------------------------------------------- #
# 编排入口（对话 + Agent 共用）
# --------------------------------------------------------------------------- #


async def search_gongwen_for_question(
    teacher_id: int,
    question: str,
    intent: dict[str, Any],
) -> dict[str, Any]:
    """候选获取 + AI 相关性筛选 → 命中公文列表（AI 故障降级为关键词命中）。"""
    from .organization_scope_service import load_teacher_org_scope
    from .resource_access_service import is_super_admin_teacher

    keywords = list(intent.get("keywords") or [])
    recent_months = int(intent.get("recent_months") or 0)
    with get_db_connection() as conn:
        scope = load_teacher_org_scope(conn, int(teacher_id))
        try:
            is_admin = bool(is_super_admin_teacher(conn, int(teacher_id)))
        except Exception:  # noqa: BLE001 — 管理员判定失败时按普通教师范围检索
            is_admin = False
        candidates = _fetch_candidate_documents(
            conn,
            scope,
            is_super_admin=is_admin,
            keywords=keywords,
            recent_months=recent_months,
        )

    if not candidates:
        return {"documents": [], "candidate_count": 0, "ai_selected": False}

    reasons = await ai_select_relevant_documents(str(intent.get("query") or question), candidates)
    if reasons:
        picked = [doc for doc in candidates if int(doc["id"]) in reasons]
        for doc in picked:
            doc["relevance_reason"] = reasons.get(int(doc["id"]), "")
        ai_selected = True
    else:
        # AI 筛选不可用 → 直接用候选（关键词/最近排序已经是合理兜底）。
        picked = list(candidates)
        ai_selected = False
    picked = picked[:MAX_RESULT_DOCS]
    return {"documents": picked, "candidate_count": len(candidates), "ai_selected": ai_selected}


async def run_gongwen_retrieval(
    teacher_id: int,
    message: str,
    *,
    intent: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """完整链路：意图识别 → 检索 → 上下文块。与公文无关时返回 None。"""
    if intent is None:
        if not message_may_mention_gongwen(message):
            return None
        intent = await detect_gongwen_intent(message)
    if not intent:
        return None
    result = await search_gongwen_for_question(int(teacher_id), message, intent)
    docs = result["documents"]
    return {
        "intent": intent,
        "documents": [_doc_public_payload(doc) for doc in docs],
        "context_block": build_gongwen_context_block(docs, intent=intent),
        "doc_count": len(docs),
        "candidate_count": int(result.get("candidate_count") or 0),
        "ai_selected": bool(result.get("ai_selected")),
    }


def summarize_retrieval_for_log(result: dict[str, Any] | None) -> str:
    if not result:
        return "no_intent"
    return (
        f"docs={result.get('doc_count')} candidates={result.get('candidate_count')} "
        f"ai_selected={result.get('ai_selected')} recent_months={result.get('intent', {}).get('recent_months')}"
    )
