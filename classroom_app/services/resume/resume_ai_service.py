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


def _compact_self_intro(text: Any, *, limit: int = 180) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    raw = re.sub(r"```(?:markdown|md|text)?\s*|\s*```", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", raw)
    raw = re.sub(r"(?m)^\s*[-*]\s+", "", raw)
    raw = re.sub(r"^\s*(个人介绍|自我介绍|职业摘要|简历摘要)\s*\n+", "", raw)
    raw = re.sub(r"^\s*(个人介绍|自我介绍|职业摘要|简历摘要)\s*[:：]\s*", "", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    if len(raw) > limit:
        raw = raw[:limit].rstrip("，、；; ")
    if raw and raw[-1] not in "。！？!?":
        raw += "。"
    return raw


# ---------------------------------------------------------------------------
# 1) Optimize an existing self-introduction
# ---------------------------------------------------------------------------
async def optimize_self_intro(text: str, personal: dict[str, Any]) -> dict[str, Any]:
    text = str(text or "").strip()
    if not text:
        return {"ok": False, "error": "请先输入自我介绍内容"}
    system = (
        "你是资深简历顾问。请在不虚构事实的前提下，把学生输入改写为可直接放入简历"
        "“个人介绍/职业摘要”栏位的中文正文。要求：80-140 个中文字符，最多 3 句；"
        "专业、严谨、简洁，突出与目标岗位相关的技能、项目/学习成果和工作方式。"
        "删除聊天式自述、流水账、课堂任务过程、弱项说明、求职愿望和空泛套话；"
        "不要 Markdown、标题、称呼或解释，只返回正文。"
    )
    user = f"目标岗位与个人信息：{_personal_brief(personal)}\n\n原始自我介绍：\n{text}"
    try:
        result = await _chat(system, user, want_json=False, label="resume:optimize-intro")
        result = _compact_self_intro(result)
        if not _resume_summary_is_useful(result, str(personal.get("expected_position") or "")):
            result = ""
        if not result:
            return {"ok": False, "error": "AI 未返回可用于简历的专业内容，请稍后重试"}
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
async def generate_tech_stack(
    bundle: dict[str, Any],
    student_context: dict[str, Any],
    *,
    target_position: str = "",
) -> dict[str, Any]:
    target_position = str(target_position or (bundle.get("personal") or {}).get("expected_position") or "").strip()
    system = (
        "你是技术简历顾问。请根据学生的项目/比赛经验、学习经历与技能，归纳出适合写进简历的技术栈。"
        "技术栈必须围绕目标岗位筛选和排序：与目标岗位直接相关的能力优先，不相关或证据不足的能力不要硬塞。"
        "必须返回 JSON 数组，每个元素是对象：{\"group\":\"分组名\",\"items\":[\"技能1\",\"技能2\"]}。"
        "分组例如 编程语言 / 框架与工具 / 数据库 / 其他。只罗列有依据的技术，不要编造。只返回 JSON。"
    )
    digest = {
        "目标岗位": target_position,
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


async def optimize_resume_for_target(
    resume: dict[str, Any],
    bundle: dict[str, Any],
    student_context: dict[str, Any],
) -> dict[str, Any]:
    """Produce per-resume optimized summary + tech stack + human-readable notes."""
    target_position = str(
        resume.get("target_position") or (bundle.get("personal") or {}).get("expected_position") or "目标岗位"
    ).strip()
    layout = resume.get("layout") if isinstance(resume.get("layout"), dict) else {}
    selected = _selected_resume_digest(bundle, layout)
    system = (
        "你是资深校招简历顾问。请根据学生已选择进入简历的真实材料，把这份简历优化成更匹配目标岗位的版本。"
        "必须遵守：不虚构项目、证书、学校、奖项；不写空泛口号；不要暴露课堂表现、隐私推断或后台分析过程。"
        "返回 JSON 对象，格式为："
        "{\"summary_md\":\"80-140字职业摘要\","
        "\"tech_stack\":[{\"group\":\"分组名\",\"items\":[\"技能\"]}],"
        "\"notes\":[\"给学生看的优化说明，3-5条\"]}。"
        "summary_md 要专业、严谨、短促有力，可直接放入简历。"
        "tech_stack 只保留和目标岗位相关且材料中有依据的技能，并按岗位重要性排序。"
    )
    digest = {
        "目标岗位": target_position,
        "学生背景": {
            "专业": student_context.get("major_name"),
            "班级": student_context.get("class_name"),
            "姓名": (bundle.get("personal") or {}).get("name"),
        },
        "已放入简历的材料": selected,
        "全部技能候选": [s.get("name") for s in bundle.get("skill", [])][:30],
    }
    user = "简历优化输入：\n" + json.dumps(digest, ensure_ascii=False, indent=2)
    try:
        result = await _chat(
            system, user, want_json=True, capability="thinking",
            task_type="deep_text_reasoning", timeout=_THINK_TIMEOUT, label="resume:optimize-resume",
        )
        if not isinstance(result, dict):
            raise ValueError("AI returned non-object")
        summary = _compact_self_intro(result.get("summary_md") or result.get("summary") or "", limit=170)
        if not _resume_summary_is_useful(summary, target_position):
            summary = ""
        groups = _coerce_tech_groups(result.get("tech_stack") or result.get("groups"))
        notes = _coerce_notes(result.get("notes"))
        if not summary:
            summary = _fallback_targeted_summary(bundle, student_context, target_position)
        if not groups:
            groups = _fallback_tech_stack(bundle)
        if not notes:
            notes = _fallback_optimization_notes(target_position, bool(groups), bool(summary))
        return {"ok": True, "target_position": target_position, "summary_md": summary,
                "tech_stack": groups, "notes": notes}
    except (httpx.HTTPError, ValueError) as exc:
        return {
            "ok": False,
            "target_position": target_position,
            "summary_md": _fallback_targeted_summary(bundle, student_context, target_position),
            "tech_stack": _fallback_tech_stack(bundle),
            "notes": _fallback_optimization_notes(target_position, True, True),
            "error": f"AI 优化暂不可用（{type(exc).__name__}），已生成基础优化版",
        }


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


def _coerce_notes(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    notes: list[str] = []
    for item in raw:
        text = str(item or "").strip()
        if text:
            notes.append(text[:160])
        if len(notes) >= 5:
            break
    return notes


def _resume_summary_is_useful(text: Any, target_position: str = "") -> bool:
    raw = str(text or "").strip()
    if len(raw) < 28:
        return False
    lowered = raw.lower()
    bad_terms = ("mock", "压测", "测试响应", "占位", "示例响应", "ai 响应", "ai响应")
    if any(term in lowered for term in bad_terms):
        return False
    target = str(target_position or "").strip()
    if not target:
        return True
    target_keys = {target}
    core = re.sub(r"(开发工程师|工程师|实习生|岗位|方向|开发|助理)$", "", target).strip()
    if len(core) >= 2:
        target_keys.add(core)
    return any(key and key in raw for key in target_keys)


def _clean_resume_background_label(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    lowered = raw.lower()
    noisy_terms = ("regression", "fixture", "mock", "qa-", "qa ", "test", "p03")
    if any(term in lowered for term in noisy_terms):
        return ""
    if raw in {"待完善", "未知", "无", "暂无"}:
        return ""
    return raw[:24]


def _first_education_major(bundle: dict[str, Any]) -> str:
    for edu in bundle.get("education", []):
        if isinstance(edu, dict):
            major = _clean_resume_background_label(edu.get("major"))
            if major:
                return major
    return ""


def _fallback_targeted_summary(bundle: dict[str, Any], student_context: dict[str, Any], target_position: str) -> str:
    personal = bundle.get("personal") or {}
    target = str(target_position or personal.get("expected_position") or "相关岗位").strip()
    major = _clean_resume_background_label(student_context.get("major_name")) or _first_education_major(bundle)
    skills = [str(s.get("name") or "").strip() for s in bundle.get("skill", []) if str(s.get("name") or "").strip()]
    experiences = [
        str(e.get("title") or "").strip()
        for e in bundle.get("experience", [])
        if str(e.get("title") or "").strip()
    ]
    opening = f"具备{major}相关学习背景，求职目标为{target}" if major else f"求职目标为{target}"
    if skills:
        opening += f"，掌握{'、'.join(skills[:4])}等技能"
    opening += "。"
    if experiences:
        body = f"具备{experiences[0]}等实践经历，能够围绕需求拆解、功能实现与问题定位推进交付。"
    else:
        body = "具备扎实的专业学习基础，重视需求理解、代码质量与协作交付。"
    return _compact_self_intro(opening + body, limit=170)


def _fallback_optimization_notes(target_position: str, has_stack: bool, has_summary: bool) -> list[str]:
    notes = [f"已将简历摘要与技术栈优先对齐「{target_position or '目标岗位'}」。"]
    if has_stack:
        notes.append("技术栈按岗位相关性重新筛选和排序，避免把无关技能堆在前面。")
    if has_summary:
        notes.append("个人介绍已压缩为可直接放入简历的职业摘要，弱化流水账和口语化表达。")
    return notes


def _selected_resume_digest(bundle: dict[str, Any], layout: dict[str, Any]) -> dict[str, Any]:
    indexes = {
        "education": {int(i["id"]): i for i in bundle.get("education", []) if i.get("id") is not None},
        "experience": {int(i["id"]): i for i in bundle.get("experience", []) if i.get("id") is not None},
        "skill": {int(i["id"]): i for i in bundle.get("skill", []) if i.get("id") is not None},
        "certificate": {int(i["id"]): i for i in bundle.get("certificate", []) if i.get("id") is not None},
        "self_intro": {int(i["id"]): i for i in bundle.get("self_intro", []) if i.get("id") is not None},
    }

    def pick(kind: str, ids: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for raw in (ids if isinstance(ids, list) else []):
            try:
                item = indexes[kind].get(int(raw))
            except (TypeError, ValueError):
                item = None
            if item:
                out.append(item)
        return out

    digest: dict[str, Any] = {"个人信息": bundle.get("personal") or {}}
    for block in layout.get("blocks") if isinstance(layout.get("blocks"), list) else []:
        if not isinstance(block, dict):
            continue
        btype = str(block.get("type") or "")
        if btype == "education":
            digest["学习经历"] = [
                {"学校": i.get("school"), "专业": i.get("major"), "内容": (i.get("content") or "")[:180]}
                for i in pick("education", block.get("ids"))
            ]
        elif btype == "experience":
            digest["项目比赛经验"] = [
                {"标题": i.get("title"), "角色": i.get("role"), "内容": (i.get("content") or "")[:240],
                 "贡献": (i.get("contribution") or "")[:200], "成果": (i.get("achievement") or "")[:160]}
                for i in pick("experience", block.get("ids"))
            ]
        elif btype == "skill_cert":
            digest["技能"] = [i.get("name") for i in pick("skill", block.get("skill_ids"))]
            digest["证书"] = [i.get("name") for i in pick("certificate", block.get("cert_ids"))]
        elif btype == "self_intro":
            digest["原个人介绍"] = [(i.get("content_md") or "")[:240] for i in pick("self_intro", block.get("ids"))]
    return digest
