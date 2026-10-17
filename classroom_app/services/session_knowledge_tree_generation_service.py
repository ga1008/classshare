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
from . import session_knowledge_tree_prompts as prompts
from . import session_knowledge_tree_service as kt
from .academic_service import build_classroom_ai_context

_DOC_CHAR_BUDGET = 18000
_AI_TIMEOUT = 240.0
_AI_RETRY_TIMEOUT = 150.0
_RETRY_DOC_BUDGET = 9000

SYSTEM_PROMPT = prompts.SYSTEM_PROMPT



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


async def _post_tree(payload: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any] | None:
    """One thinking-tier call; a 5xx/timeout or unusable reply falls back to the standard tier once."""
    try:
        response = await ai_client.post("/api/ai/chat", json=payload, timeout=_AI_TIMEOUT)
        response.raise_for_status()
        parsed = parse_ai_payload(response.json())
        if parsed and parsed.get("nodes"):
            return parsed
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if status < 500:
            raise
    except (httpx.TimeoutException, httpx.TransportError):
        pass
    response = await ai_client.post("/api/ai/chat", json=fallback, timeout=_AI_RETRY_TIMEOUT)
    response.raise_for_status()
    return parse_ai_payload(response.json())


def _payloads(message: str, retry_message: str, label: str) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = {
        "system_prompt": SYSTEM_PROMPT,
        "messages": [],
        "new_message": message,
        "file_texts": [],
        "model_capability": "thinking",
        "task_type": "deep_text_reasoning",
        "response_format": "json",
        "web_search_enabled": False,
        "task_priority": "background",
        "task_label": label,
    }
    fallback = {
        **payload,
        "new_message": retry_message,
        "model_capability": "standard",
        "task_type": "fast_text_response",
        "task_label": f"{label}:standard-retry",
    }
    return payload, fallback


async def _chat_tree(user_message: str, *, label: str, retry_message: str) -> dict[str, Any] | None:
    """Generate, then re-ask once with the concrete defects if the tree breaks the rules."""
    first = await _post_tree(*_payloads(user_message, retry_message, label))
    issues = prompts.quality_issues(first)
    if not issues:
        return first
    print(f"[KNOWLEDGE_TREE] {label} quality re-ask: {'; '.join(issues)}")
    try:
        second = await _post_tree(*_payloads(
            prompts.build_repair_message(user_message, first or {}, issues),
            prompts.build_repair_message(retry_message, first or {}, issues),
            f"{label}:repair",
        ))
    except Exception as exc:  # the first answer may still be usable after normalization
        print(f"[KNOWLEDGE_TREE] {label} repair failed: {exc!r}")
        return first
    if second and len(prompts.quality_issues(second)) <= len(issues):
        return second
    return first


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
