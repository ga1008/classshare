"""Synchronous-ish AI helpers for the resume console.

Three capabilities, all returning data (no DB writes — callers persist):

* ``optimize_self_intro``        — polish user-written text toward the target job.
* ``build_personal_info_suggestions`` — AI-refined values for the personal form.
* ``generate_tech_stack``        — derive a grouped tech stack from experience/education.

The deep, long-running self-introduction *generation* lives in
``resume_generation_service`` (background job). These helpers use the fast/standard
model so they can answer inline. All are graceful — never raise on AI failure;
the caller decides the fallback.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from ...core import ai_client

_FAST_TIMEOUT = 60.0
_THINK_TIMEOUT = 240.0


# ---------------------------------------------------------------------------
# AI plumbing
# ---------------------------------------------------------------------------
def _loads_json(text: Any) -> Any:
    if isinstance(text, (dict, list)):
        return text
    if not text:
        return None
    raw = str(text).strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1).strip()
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        pass
    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\[{]", raw):
        try:
            parsed, _ = decoder.raw_decode(raw[match.start():])
            return parsed
        except (TypeError, json.JSONDecodeError):
            continue
    return None


def _payload_text(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    return str(data.get("response_text") or data.get("text") or "").strip()


def _payload_json(data: Any) -> Any:
    if not isinstance(data, dict):
        return None
    for key in ("response_json", "json", "data"):
        parsed = _loads_json(data.get(key))
        if parsed is not None:
            return parsed
    return _loads_json(data.get("response_text"))


async def _chat(system_prompt: str, user_message: str, *, want_json: bool,
                capability: str = "standard", task_type: str = "fast_text_response",
                timeout: float = _FAST_TIMEOUT, label: str = "resume") -> Any:
    payload = {
        "system_prompt": system_prompt,
        "messages": [],
        "new_message": user_message,
        "file_texts": [],
        "model_capability": capability,
        "task_type": task_type,
        "response_format": "json" if want_json else "text",
        "web_search_enabled": False,
        "task_priority": "background",
        "task_label": label,
    }
    response = await ai_client.post("/api/ai/chat", json=payload, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    return _payload_json(data) if want_json else _payload_text(data)


def _personal_brief(personal: dict[str, Any]) -> str:
    keep = ("name", "gender", "expected_position", "expected_industry")
    parts = [f"{k}={personal.get(k)}" for k in keep if str(personal.get(k) or "").strip()]
    return "；".join(parts) or "（暂无个人信息）"


# ---------------------------------------------------------------------------
# 1) Optimize an existing self-introduction
# ---------------------------------------------------------------------------
async def optimize_self_intro(text: str, personal: dict[str, Any]) -> dict[str, Any]:
    text = str(text or "").strip()
    if not text:
        return {"ok": False, "error": "请先输入自我介绍内容"}
    system = (
        "你是资深简历顾问。请在不虚构事实的前提下，对学生的自我介绍进行润色优化："
        "结构清晰、语言专业、突出与目标岗位相关的能力与亮点，可用简洁的 Markdown。"
        "只返回优化后的正文，不要解释。"
    )
    user = f"目标岗位与个人信息：{_personal_brief(personal)}\n\n原始自我介绍：\n{text}"
    try:
        result = await _chat(system, user, want_json=False, label="resume:optimize-intro")
        result = str(result or "").strip()
        if not result:
            return {"ok": False, "error": "AI 未返回内容，请稍后重试"}
        return {"ok": True, "content": result}
    except (httpx.HTTPError, ValueError) as exc:
        return {"ok": False, "error": f"AI 优化暂不可用（{type(exc).__name__}）"}


# ---------------------------------------------------------------------------
# 2) Suggest refined personal-info field values
# ---------------------------------------------------------------------------
async def build_personal_info_suggestions(personal: dict[str, Any], student_context: dict[str, Any]) -> dict[str, Any]:
    system = (
        "你是简历填写助手。基于学生平台资料，给出适合简历的字段建议值，必须返回 JSON 对象，"
        "键可包含 expected_position（期望岗位）、expected_industry（行业）、email（规范化邮箱）。"
        "不要编造身份证、电话等敏感信息。只返回 JSON。"
    )
    context = {
        "已有个人信息": {k: personal.get(k) for k in ("name", "email", "expected_position", "expected_industry")},
        "专业": student_context.get("major_name"),
        "班级": student_context.get("class_name"),
    }
    user = "学生资料：\n" + json.dumps(context, ensure_ascii=False, indent=2)
    try:
        result = await _chat(system, user, want_json=True, label="resume:personal-suggest")
        if isinstance(result, dict):
            allowed = ("expected_position", "expected_industry", "email")
            return {"ok": True, "suggestions": {k: str(v).strip() for k, v in result.items()
                                                if k in allowed and str(v or "").strip()}}
        return {"ok": False, "error": "AI 未返回有效建议"}
    except (httpx.HTTPError, ValueError) as exc:
        return {"ok": False, "error": f"AI 建议暂不可用（{type(exc).__name__}）"}


# ---------------------------------------------------------------------------
# 3) Generate a grouped tech stack for the résumé builder
# ---------------------------------------------------------------------------
async def generate_tech_stack(bundle: dict[str, Any], student_context: dict[str, Any]) -> dict[str, Any]:
    system = (
        "你是技术简历顾问。请根据学生的项目/比赛经验、学习经历与技能，归纳出适合写进简历的技术栈。"
        "必须返回 JSON 数组，每个元素是对象：{\"group\":\"分组名\",\"items\":[\"技能1\",\"技能2\"]}。"
        "分组例如 编程语言 / 框架与工具 / 数据库 / 其他。只罗列有依据的技术，不要编造。只返回 JSON。"
    )
    digest = {
        "专业": student_context.get("major_name"),
        "技能": [s.get("name") for s in bundle.get("skill", [])][:30],
        "项目比赛": [
            {"标题": e.get("title"), "内容": (e.get("content") or "")[:200], "角色": e.get("role")}
            for e in bundle.get("experience", [])
        ][:12],
        "学习经历": [
            {"学校": e.get("school"), "专业": e.get("major"), "内容": (e.get("content") or "")[:160]}
            for e in bundle.get("education", [])
        ][:8],
    }
    user = "学生经历摘要：\n" + json.dumps(digest, ensure_ascii=False, indent=2)
    try:
        result = await _chat(
            system, user, want_json=True, capability="thinking",
            task_type="deep_text_reasoning", timeout=_THINK_TIMEOUT, label="resume:tech-stack",
        )
        groups = _coerce_tech_groups(result)
        if groups:
            return {"ok": True, "groups": groups}
        return {"ok": False, "error": "AI 未返回有效技术栈", "groups": _fallback_tech_stack(bundle)}
    except (httpx.HTTPError, ValueError) as exc:
        return {"ok": False, "error": f"AI 生成暂不可用（{type(exc).__name__}）", "groups": _fallback_tech_stack(bundle)}


def _coerce_tech_groups(result: Any) -> list[dict[str, Any]]:
    if not isinstance(result, list):
        if isinstance(result, dict) and isinstance(result.get("groups"), list):
            result = result["groups"]
        else:
            return []
    groups: list[dict[str, Any]] = []
    for entry in result:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("group") or entry.get("name") or "").strip()
        items = entry.get("items") if isinstance(entry.get("items"), list) else []
        items = [str(i).strip() for i in items if str(i or "").strip()]
        if name and items:
            groups.append({"group": name[:40], "items": items[:20]})
    return groups[:8]


def _fallback_tech_stack(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """Deterministic fallback: just list the named skills under one group."""
    skills = [str(s.get("name") or "").strip() for s in bundle.get("skill", []) if str(s.get("name") or "").strip()]
    if not skills:
        return []
    return [{"group": "技能", "items": skills[:20]}]
