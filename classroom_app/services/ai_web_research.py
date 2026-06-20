"""Agentic web-research helper — reusable across any AI flow.

The platform's chat AI is not natively web-connected, but the AI worker exposes a
``/api/ai/web-search`` endpoint (Volcengine Responses API + search engine). This
module wraps that into an *agentic* two-step loop so a thinking model can decide,
for itself, whether it needs fresh web information before answering:

1. **Plan** — ask the thinking AI: given this objective + context, do you need to
   search the web? If so, what queries / sites / keywords, and why?
   (returns JSON ``{need_search, queries:[{q, site, why}], wait_seconds, notes}``)
2. **Search** — run the planned queries (bounded) through the web-search endpoint.
3. **Digest** — concatenate the results into a compact reference block that the
   caller injects into its real generation prompt.

Everything degrades gracefully: any failure (planning, search, AI disabled) yields
an empty digest so the caller keeps working exactly as before. Designed to be
called from anywhere — pass an objective + context, get back a text block.

See [[career-path-network]], [[agent-bridge-and-knowledge]].
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Optional

from ..core import ai_client

# Hard caps so an over-eager plan can never blow the latency / token budget.
MAX_QUERIES = 4
PER_QUERY_TIMEOUT = 70.0
PLAN_TIMEOUT = 90.0
DIGEST_CHAR_BUDGET = 4800
PER_RESULT_CHAR_BUDGET = 1500


def _extract_json_object(value: Any) -> Optional[dict[str, Any]]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


async def _plan_queries(objective: str, context: str, max_queries: int) -> dict[str, Any]:
    """Ask the thinking AI whether (and what) to search. Returns a normalized plan."""
    system = (
        "你是一名严谨的研究规划助手。你将判断：为了高质量地完成给定目标，是否需要先做联网检索"
        "获取最新、真实、地域相关的信息（如就业市场、薪资、城市政策、行业动态等）。"
        "必须严格输出 JSON，不要任何解释、不要 markdown 代码块。"
    )
    user = "\n\n".join([
        f"【目标】{objective}",
        f"【已知背景】{context}",
        "请输出一个 JSON 对象：\n"
        "need_search: 布尔，是否需要联网检索；\n"
        f"queries: 数组（最多 {max_queries} 条），每条 {{q: 检索词, site: 可选优先站点或留空, why: 为什么要查}}，"
        "检索词要具体、含地域/时间/岗位等关键词，避免空泛；\n"
        "wait_seconds: 你建议为这些检索等待的总秒数（整数，10–120）；\n"
        "notes: 一句话说明你的检索策略。\n"
        "若依据已知背景已足够、无需联网，则 need_search=false 且 queries=[]。\n"
        "只返回这个 JSON 对象。",
    ])
    try:
        resp = await ai_client.post(
            "/api/ai/chat",
            json={
                "system_prompt": system,
                "messages": [],
                "new_message": user,
                "model_capability": "thinking",
                "task_type": "deep_text_reasoning",
                "response_format": "json",
                "task_priority": "background",
                "task_label": "web_research_plan",
            },
            timeout=PLAN_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:  # noqa: BLE001
        return {"need_search": False, "queries": [], "wait_seconds": 0, "notes": ""}

    payload = data.get("response_json")
    if not isinstance(payload, dict):
        payload = _extract_json_object(data.get("response_text")) or {}

    raw_queries = payload.get("queries") if isinstance(payload.get("queries"), list) else []
    queries: list[dict[str, str]] = []
    for item in raw_queries[:max_queries]:
        if isinstance(item, dict) and str(item.get("q") or "").strip():
            queries.append({
                "q": str(item.get("q")).strip(),
                "site": str(item.get("site") or "").strip(),
                "why": str(item.get("why") or "").strip(),
            })
        elif isinstance(item, str) and item.strip():
            queries.append({"q": item.strip(), "site": "", "why": ""})
    return {
        "need_search": bool(payload.get("need_search")) and bool(queries),
        "queries": queries,
        "wait_seconds": payload.get("wait_seconds"),
        "notes": str(payload.get("notes") or ""),
    }


async def _run_search(spec: dict[str, str], instructions: str) -> str:
    query = spec.get("q") or ""
    site = spec.get("site") or ""
    full_query = f"{query}（优先参考站点：{site}）" if site else query
    try:
        resp = await ai_client.post(
            "/api/ai/web-search",
            json={"query": full_query, "instructions": instructions, "task_label": "web_research"},
            timeout=PER_QUERY_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return str(data.get("text") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


async def gather(
    *,
    objective: str,
    context: str = "",
    search_instructions: str = "",
    max_queries: int = 3,
) -> dict[str, Any]:
    """Plan → search → digest. Returns ``{digest, used, queries, notes}``.

    ``digest`` is a ready-to-inject reference block (empty string if no research
    was needed or everything degraded). Never raises — safe to await inline.
    """
    objective = str(objective or "").strip()
    if not objective:
        return {"digest": "", "used": False, "queries": [], "notes": ""}

    max_queries = max(1, min(MAX_QUERIES, int(max_queries or 1)))
    try:
        plan = await _plan_queries(objective, context, max_queries)
    except Exception:  # noqa: BLE001
        return {"digest": "", "used": False, "queries": [], "notes": ""}
    if not plan.get("need_search"):
        return {"digest": "", "used": False, "queries": [], "notes": plan.get("notes") or ""}

    specs = plan["queries"][:max_queries]
    results = await asyncio.gather(
        *(_run_search(spec, search_instructions) for spec in specs),
        return_exceptions=True,
    )

    blocks: list[str] = []
    used_queries: list[str] = []
    for spec, res in zip(specs, results):
        text = "" if isinstance(res, BaseException) else (res or "")
        if not text:
            continue
        used_queries.append(spec["q"])
        snippet = text[:PER_RESULT_CHAR_BUDGET].strip()
        blocks.append(f"◆ 检索「{spec['q']}」：\n{snippet}")
        if sum(len(b) for b in blocks) >= DIGEST_CHAR_BUDGET:
            break

    if not blocks:
        return {"digest": "", "used": False, "queries": [], "notes": plan.get("notes") or ""}

    digest = "\n\n".join(blocks)[:DIGEST_CHAR_BUDGET]
    return {
        "digest": digest,
        "used": True,
        "queries": used_queries,
        "notes": plan.get("notes") or "",
    }
