# -*- coding: utf-8 -*-
"""公文 → 人生一言提示语的离线生成管线.

递归调度任务（``life_tip_gongwen_refresh``，默认每天一跑）从
``gongwen_documents`` 里增量取"已解析、正文足够长、未挖掘过"的公文，喂给
思考型文本模型，抽取对学生真正有用的硬信息（毕业条件 / 奖学金门槛 /
报名截止 …），改写成游戏加载屏句式后写入 ``life_tips``
（scope='school' 或 'department'，source_kind='ai_gongwen'）。

关键约束：

- **绝不在登录路径调 AI**——只在 scheduler worker 里跑，且单次 run 限
  ``DOCS_PER_RUN`` 篇，AI 走 gateway 的 background 优先级；
- 每篇公文只挖一次（``life_tip_source_ledger`` 台账），产出 0 条也入账；
- 提示语经 content_hash 去重（``insert_life_tip``），重复表述自然合并；
- 任何单篇失败不影响本次 run 的其余公文，也不让任务整体失败。

Arming: ``schedule_life_tip_generation_worker`` 由
``gongwen_parse_service.schedule_gongwen_parse_worker`` 链式布防（与
follow worker 同模式）；handler 在 ``scheduled_task_handlers`` 注册。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any

from ..database import get_db_connection

LIFE_TIP_GENERATION_TASK_KIND = "life_tip_gongwen_refresh"
LIFE_TIP_GENERATION_INTERVAL_SECONDS = 24 * 3600
DOCS_PER_RUN = 6
MIN_DOC_TEXT_CHARS = 200
MAX_DOC_TEXT_CHARS = 6000
MAX_TIPS_PER_DOC = 5
TIP_MIN_CHARS = 15
TIP_MAX_CHARS = 120

ALLOWED_CATEGORIES = (
    "学业规则", "毕业条件", "奖学金", "论文写作", "考研", "考公考编",
    "实习", "简历面试", "合同五险", "行业城市", "职业路径", "人生大实话",
)

_PROMPT_TEMPLATE = """你是一所高校的学生事务顾问。下面是一篇校园公文的正文，请从中提取对**学生本人**真正有用的硬信息（截止日期、分数线、申请条件、办理流程、隐藏福利等），改写成"游戏加载界面提示"式的一句话。

要求：
1. 每条提示一句话、30-90 字、具体可执行，最好带数字/门槛/时间点；
2. 只提取公文里明确写了的信息，不得编造或外推；公文里没有对学生有用的信息就返回空数组；
3. 语气像有经验的学长学姐在给建议，不要官腔；
4. category 只能从这些里选：{categories}；
5. scope：适用于全校学生填 "school"，只适用于某个学院/系部填 "department" 并给出 department 名称。

公文标题：{title}
发文单位：{sender}
公文正文：
{content}

只输出合法 JSON，格式：
{{"tips": [{{"tip_text": "...", "category": "...", "scope": "school", "department": ""}}]}}"""


def schedule_life_tip_generation_worker(conn) -> int:
    """布防每日提示语挖掘任务（幂等，全局一条）。"""
    from .scheduled_task_service import schedule_task

    run_at = datetime.now() + timedelta(seconds=120)
    return schedule_task(
        conn,
        task_kind=LIFE_TIP_GENERATION_TASK_KIND,
        run_at=run_at,
        payload={},
        dedupe_key="life-tip-gongwen-refresh",
        recurrence_seconds=LIFE_TIP_GENERATION_INTERVAL_SECONDS,
        owner_role="system",
        title="公文提示语挖掘",
        replace=True,
    )


def _doc_body(row: Any) -> str:
    text = str(row["parsed_text"] or "").strip() or str(row["content_text"] or "").strip()
    return re.sub(r"\s+", " ", text)[:MAX_DOC_TEXT_CHARS]


def _load_unmined_documents(conn) -> list[dict[str, Any]]:
    from .life_tip_service import ensure_life_tip_runtime

    ensure_life_tip_runtime(conn)
    rows = conn.execute(
        f"""
        SELECT d.id, d.title, d.sender_name, d.attr_school_code, d.attr_college,
               d.attr_department, d.content_text, d.parsed_text
        FROM gongwen_documents d
        LEFT JOIN life_tip_source_ledger l ON l.doc_id = d.id
        WHERE l.doc_id IS NULL
          AND (LENGTH(COALESCE(d.parsed_text, '')) >= {MIN_DOC_TEXT_CHARS}
               OR LENGTH(COALESCE(d.content_text, '')) >= {MIN_DOC_TEXT_CHARS})
        ORDER BY d.id DESC
        LIMIT {DOCS_PER_RUN}
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _mark_mined(conn, doc_id: int, tips_created: int) -> None:
    from ..db.connection import get_configured_db_engine

    # `?` 占位走连接门面转换；db.sql 的 $n 构建器与门面不兼容，勿用。
    sql = "INSERT INTO life_tip_source_ledger (doc_id, tips_created) VALUES (?, ?)"
    if get_configured_db_engine() == "postgres":
        sql += " ON CONFLICT (doc_id) DO NOTHING"
    else:
        sql = sql.replace("INSERT INTO", "INSERT OR IGNORE INTO", 1)
    conn.execute(sql, (int(doc_id), int(tips_created)))


def _validated_tips(payload: Any) -> list[dict[str, str]]:
    tips = payload.get("tips") if isinstance(payload, dict) else None
    if not isinstance(tips, list):
        return []
    cleaned: list[dict[str, str]] = []
    for item in tips[:MAX_TIPS_PER_DOC]:
        if not isinstance(item, dict):
            continue
        text = re.sub(r"\s+", " ", str(item.get("tip_text") or "")).strip()
        if not (TIP_MIN_CHARS <= len(text) <= TIP_MAX_CHARS):
            continue
        category = str(item.get("category") or "").strip()
        if category not in ALLOWED_CATEGORIES:
            category = "学业规则"
        scope = "department" if str(item.get("scope") or "") == "department" else "school"
        department = re.sub(r"\s+", "", str(item.get("department") or ""))
        if scope == "department" and not department:
            scope = "school"
        cleaned.append({
            "tip_text": text,
            "category": category,
            "scope": scope,
            "department": department if scope == "department" else "",
        })
    return cleaned


async def _mine_document(doc: dict[str, Any]) -> int:
    """对单篇公文跑提取并入库，返回新增条数。"""
    from ..core import ai_client
    from .ai_gateway_service import ai_gateway_post
    from .life_tip_service import insert_life_tip

    prompt = _PROMPT_TEMPLATE.format(
        categories="、".join(ALLOWED_CATEGORIES),
        title=str(doc.get("title") or "")[:200],
        sender=str(doc.get("sender_name") or "")[:100],
        content=_doc_body(doc),
    )
    response = await ai_gateway_post(
        ai_client,
        "/api/ai/chat",
        json_payload={
            "system_prompt": "你是高校学生事务顾问，只允许输出合法 JSON。",
            "messages": [],
            "new_message": prompt,
            "model_capability": "thinking",
            "task_type": "deep_text_reasoning",
            "response_format": "json",
            "task_priority": "background",
            "task_label": "life_tip_mining",
            "web_search_enabled": False,
        },
        timeout=180.0,
        task_type="life_tip_mining",
        priority="P1",
        source_ref=f"life-tip:gongwen:{int(doc['id'])}",
    )
    response.raise_for_status()
    data = response.json()
    if data.get("status") != "success":
        raise RuntimeError(f"AI 返回失败: {str(data)[:300]}")
    payload = data.get("response_json")
    if not isinstance(payload, dict):
        try:
            payload = json.loads(str(data.get("response") or ""))
        except (TypeError, ValueError):
            payload = {}

    tips = _validated_tips(payload)
    school_code = str(doc.get("attr_school_code") or "").strip() or "gxufl"
    created = 0
    with get_db_connection() as conn:
        for tip in tips:
            if insert_life_tip(
                conn,
                scope=tip["scope"],
                school_code=school_code,
                department=tip["department"],
                category=tip["category"],
                tip_text=tip["tip_text"],
                audience="student",
                source_kind="ai_gongwen",
                source_ref=str(doc.get("title") or "")[:120],
            ):
                created += 1
        _mark_mined(conn, int(doc["id"]), created)
        conn.commit()
    return created


async def handle_life_tip_generation_task(task: dict[str, Any]) -> str:
    with get_db_connection() as conn:
        docs = _load_unmined_documents(conn)
        conn.commit()
    if not docs:
        return "no unmined documents"

    mined = 0
    created = 0
    for doc in docs:
        try:
            created += await _mine_document(doc)
            mined += 1
        except Exception as exc:  # noqa: BLE001 - 单篇失败不拖垮整个 run
            print(f"[LIFE_TIP] 公文 {doc.get('id')} 提示语挖掘失败: {exc}")
    return f"mined={mined}/{len(docs)} tips_created={created}"
