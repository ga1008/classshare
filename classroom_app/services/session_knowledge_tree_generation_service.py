"""课次知识树的 AI 生成任务（durable process job 目标 ``session_knowledge_tree``）。

输入：课程简介/教材目录（``build_classroom_ai_context``）、本课次标题与说明、前后课次标题、
上一课次未完成的末梢（便于识别"分两次讲完"的延续）以及本课次绑定的学习文档全文。
输出经 ``session_knowledge_tree_service.normalize_tree`` 校验后整体替换旧树并清空进度。
结果用生成令牌（``ai_gen_task_id``）守护：迟到的旧任务不会覆盖新一轮生成。
"""

from __future__ import annotations

import asyncio
import json
import re
import traceback
from typing import Any

import httpx

from ..core import ai_client
from ..database import get_db_connection
from . import session_knowledge_tree_service as kt
from .academic_service import build_classroom_ai_context

_DOC_CHAR_BUDGET = 18000
_AI_TIMEOUT = 240.0
_AI_RETRY_TIMEOUT = 150.0
_RETRY_DOC_BUDGET = 9000

SYSTEM_PROMPT = """你是高校课程的教学设计专家，负责把"一次课"的学习文档整理成知识树（思维导图），供教师逐项勾选授课进度。

严格输出一个 JSON 对象，不要输出任何其他文字：
{
  "title": "本次课主题，不超过20字",
  "summary": "一句话概括本次课，不超过40字",
  "nodes": [
    {
      "label": "大知识点，一句话，不超过16字",
      "detail": "该知识点的详细介绍，60-120字",
      "example": "一个具体案例或应用场景，不超过80字",
      "question": "一个引导思考的问题，不超过50字",
      "prerequisite": "前置关联：学习它需要先掌握什么，不超过50字",
      "extension": "后续关联：它会引出或支撑什么，不超过50字",
      "children": [
        {"label": "细分知识", "detail": "...", "example": "...", "question": "...", "prerequisite": "...", "extension": "...",
         "children": [
           {"label": "基础知识单元", "weight": 8, "detail": "...", "example": "...", "question": "...", "prerequisite": "...", "extension": "..."}
         ]}
      ]
    }
  ]
}

结构规则（必须遵守）：
1. 顶层大知识点 1-4 个，2-3 个最佳；只围绕本次课的学习文档，不要扩展到文档没有讲的内容。
2. 整棵树两层或三层：大知识点 → 细分知识 → 基础知识单元；三层为主，内容简单时可两层（大知识点 → 基础知识单元）。不得超过三层，也不得只有一层。
3. 没有 children 的节点就是末梢（基础知识单元），只有末梢写 "weight"：按重要程度和授课耗时给出 1-30 的整数，越重要越大；系统会把所有末梢的比重归一化为合计 100%。
4. 末梢总数 6-24 个，每个末梢应是课堂上能明确"讲完/没讲完"的一个教学单元。
5. label 一律一句话，简洁具体，不加编号，不重复上级的字眼。
6. 同级节点按授课顺序排列。所有文字使用简体中文。"""


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _loads_json(text: Any) -> dict[str, Any] | None:
    if isinstance(text, dict):
        return text
    raw = _safe_text(text)
    if not raw:
        return None
    fence = re.search(r"```(?:json)?\s*(.+?)```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(raw[start : end + 1])
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def parse_ai_payload(data: Any) -> dict[str, Any] | None:
    """Tree JSON from the AI gateway reply (JSON mode or legacy text mode)."""
    if not isinstance(data, dict):
        return None
    for key in ("response_json", "json", "data"):
        parsed = _loads_json(data.get(key))
        if parsed and parsed.get("nodes"):
            return parsed
    return _loads_json(data.get("response_text"))


async def _chat_tree(user_message: str, *, label: str, retry_message: str) -> dict[str, Any] | None:
    payload = {
        "system_prompt": SYSTEM_PROMPT,
        "messages": [],
        "new_message": user_message,
        "file_texts": [],
        "model_capability": "thinking",
        "task_type": "deep_text_reasoning",
        "response_format": "json",
        "web_search_enabled": False,
        "task_priority": "background",
        "task_label": label,
    }
    retry_payload = {
        **payload,
        "new_message": retry_message,
        "model_capability": "standard",
        "task_type": "fast_text_response",
        "task_label": f"{label}:standard-retry",
    }
    try:
        response = await ai_client.post("/api/ai/chat", json=payload, timeout=_AI_TIMEOUT)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if status < 500:
            raise
        response = await ai_client.post("/api/ai/chat", json=retry_payload, timeout=_AI_RETRY_TIMEOUT)
        response.raise_for_status()
    except (httpx.TimeoutException, httpx.TransportError):
        response = await ai_client.post("/api/ai/chat", json=retry_payload, timeout=_AI_RETRY_TIMEOUT)
        response.raise_for_status()
    parsed = parse_ai_payload(response.json())
    if parsed and parsed.get("nodes"):
        return parsed
    # One standard-tier retry when the thinking reply was unusable.
    response = await ai_client.post("/api/ai/chat", json=retry_payload, timeout=_AI_RETRY_TIMEOUT)
    response.raise_for_status()
    return parse_ai_payload(response.json())


def _neighbor_context(conn, session: dict[str, Any]) -> tuple[str, str, list[str]]:
    rows = conn.execute(
        """
        SELECT id, title, session_date, order_index
        FROM class_offering_sessions
        WHERE class_offering_id = ? AND COALESCE(schedule_status, 'scheduled') NOT IN ('cancelled', 'canceled')
        ORDER BY session_date, order_index
        """,
        (int(session["class_offering_id"]),),
    ).fetchall()
    ids = [int(row["id"]) for row in rows]
    if int(session["id"]) not in ids:
        return "", "", []
    index = ids.index(int(session["id"]))
    previous = dict(rows[index - 1]) if index > 0 else None
    following = dict(rows[index + 1]) if index + 1 < len(rows) else None
    unfinished: list[str] = []
    if previous:
        prev_row = kt.load_row(conn, int(previous["id"]))
        if prev_row:
            tree = kt._loads(prev_row.get("tree_json"), {})
            done = set(kt._loads(prev_row.get("completed_json"), []))
            unfinished = [leaf["label"] for leaf in kt.iter_leaves(tree.get("nodes") or []) if leaf["id"] not in done][:12]
    return (
        _safe_text(previous.get("title")) if previous else "",
        _safe_text(following.get("title")) if following else "",
        unfinished,
    )


def build_generation_input(session_id: int) -> dict[str, Any]:
    """Collect everything the prompt needs (sync; runs in a worker thread)."""
    with get_db_connection() as conn:
        session = conn.execute("SELECT * FROM class_offering_sessions WHERE id = ? LIMIT 1", (int(session_id),)).fetchone()
        if not session:
            raise kt.KnowledgeTreeError("课次不存在")
        session = dict(session)
        offering = conn.execute(
            "SELECT teacher_id FROM class_offerings WHERE id = ? LIMIT 1", (int(session["class_offering_id"]),)
        ).fetchone()
        teacher_id = int(offering["teacher_id"]) if offering else 0
        material_ids = kt.session_material_ids(conn, int(session["class_offering_id"]), session, teacher_id)
        if not material_ids:
            raise kt.KnowledgeTreeError("本课次尚未绑定学习材料")
        doc_text, names = kt.read_source_texts(conn, material_ids, char_budget=_DOC_CHAR_BUDGET)
        if not doc_text.strip():
            raise kt.KnowledgeTreeError("绑定的学习材料没有可读取的文本（支持 Markdown 与 HTML 学习文档）")
        fingerprint = kt.source_fingerprint(conn, material_ids)
        context = build_classroom_ai_context(conn, int(session["class_offering_id"])) or {}
        previous_title, next_title, unfinished = _neighbor_context(conn, session)
    return {
        "session": session,
        "doc_text": doc_text,
        "source_label": "、".join(names),
        "fingerprint": fingerprint,
        "classroom_summary": _safe_text(context.get("classroom_summary")),
        "textbook_summary": _safe_text(context.get("textbook_summary")),
        "previous_title": previous_title,
        "next_title": next_title,
        "unfinished": unfinished,
    }


def build_user_message(data: dict[str, Any], *, doc_budget: int = _DOC_CHAR_BUDGET) -> str:
    session = data["session"]
    lines = ["# 课程信息", data.get("classroom_summary") or "（未提供）"]
    if data.get("textbook_summary"):
        lines += ["", "# 教材与目录", data["textbook_summary"][:4000]]
    lines += [
        "",
        "# 本次课",
        f"标题：{_safe_text(session.get('title'))}",
        f"日期：{_safe_text(session.get('session_date'))}",
    ]
    content = _safe_text(session.get("content"))
    if content:
        lines.append(f"课次说明：{content[:800]}")
    if data.get("previous_title"):
        lines.append(f"上一课次：{data['previous_title']}")
    if data.get("unfinished"):
        lines.append("上一课次尚未讲完的基础单元（若本次文档也涉及，可作为本次课的延续部分）：" + "；".join(data["unfinished"]))
    if data.get("next_title"):
        lines.append(f"下一课次：{data['next_title']}")
    lines += ["", "# 本次课学习文档（知识树必须以此为依据）", data["doc_text"][:doc_budget]]
    return "\n".join(lines)


async def run_generation_job(row_id: str, session_id: int, token: str) -> None:
    """Generate, normalize and store; failures keep any previous tree."""
    try:
        data = await asyncio.to_thread(build_generation_input, int(session_id))
        raw = await _chat_tree(
            build_user_message(data),
            label=f"knowledge_tree:session:{int(session_id)}",
            retry_message=build_user_message(data, doc_budget=_RETRY_DOC_BUDGET),
        )
        tree = kt.normalize_tree(raw)
        if not tree.get("title"):
            tree["title"] = _safe_text(data["session"].get("title"))[:60]
        with get_db_connection() as conn:
            stored = kt.store_generated_tree(
                conn, row_id, token, tree, fingerprint=data["fingerprint"], source_label=data["source_label"],
            )
            conn.commit()
        if not stored:
            print(f"[KNOWLEDGE_TREE] superseded result dropped for session {session_id}")
    except Exception as exc:
        if not isinstance(exc, kt.KnowledgeTreeError):
            traceback.print_exc()
        message = str(exc) if isinstance(exc, kt.KnowledgeTreeError) else "AI 生成知识树失败，请稍后重新生成。"
        with get_db_connection() as conn:
            kt.mark_generation_failed(conn, row_id, token, message)
            conn.commit()
