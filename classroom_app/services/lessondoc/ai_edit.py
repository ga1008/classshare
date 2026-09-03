"""AI returns a validated proposal. Only the ordinary versioned save can publish it."""

import copy
import json

from . import editor_service as editor, generate, media
from .model import check_budget, walk_model

# Content fields are explicit: a prompt can never replace frame/style/identity or
# insert another element through a container. Select a container's child to polish.
CONTENT_FIELDS = {
    "text": {"md"}, "cards": {"items"}, "bignum": {"items"}, "bigmark": {"mark", "line"},
    "timeline": {"items"}, "table": {"head", "rows"}, "callout": {"md"},
    "code": {"code", "output"}, "media": {"caption"}, "svg": {"body", "viewBox"},
    "diagram": {"nodes", "edges", "actors", "messages", "layers", "links", "root", "children"},
    "quiz": {"q", "options", "answer", "explain"}, "tasklist": {"items"},
    "reveal": {"items"}, "button": {"label"}, "codewalk": {"title", "lines"}, "html": {"body", "css"},
}


def prepare(conn, *, pack_id, teacher_id, lesson_no, document, revision, slide_id="", element_id="", user_hint=""):
    pack = editor.owned_pack(conn, pack_id, teacher_id)
    state = editor._lesson_state(conn, pack, lesson_no)
    if state and state.get("gen_status") in {"excluded", "queued", "running"}:
        raise editor.EditorError("LESSON_BUSY", "此课次已排除或正在生成，请先处理课次状态", 409)
    if revision != editor.lesson_revision(conn, pack, lesson_no):
        raise editor.EditorError("REVISION_CONFLICT", "服务器正文已变化，请先处理版本冲突", 409)
    clean, warnings, diagnostics = editor.normalize_document(document, lesson_no)
    if any(d["destructive"] for d in diagnostics):
        raise editor.EditorError("CONTENT_LOSS", "请先修正当前正文，再使用 AI 改进", 422, diagnostics=diagnostics)
    if element_id:
        target = next((n for _, n in walk_model(clean) if n.get("id") == element_id and n.get("type") in CONTENT_FIELDS), None)
        if target is None:
            raise editor.EditorError("ELEMENT_UNSUPPORTED", "请选中具体内容元素；组合、标签页和分步演示可选择其中的内容进行润色", 422)
        allowed = sorted(CONTENT_FIELDS[target["type"]])
        instruction = "只返回当前元素内容字段的 JSON 对象。允许字段：" + ", ".join(allowed) + "。不改类型、位置、尺寸、外观、标识和动作。"
    else:
        target = next((s for s in clean.get("slides") or [] if s.get("id") == slide_id), None)
        if not lesson_no or target is None:
            raise editor.EditorError("SLIDE_NOT_FOUND", "请选择明确的课次页面", 422)
        instruction = "只返回一个 Slide JSON 对象。保留讲对的内容，根据要求改进当前页。不要返回整课或数组。"
    hint = str(user_hint or "").strip()
    if len(hint) > 3000:
        raise editor.EditorError("HINT_TOO_LONG", "改进要求最多 3000 字", 422)
    return dict(document=clean, revision=revision, slide_id=slide_id, element_id=element_id, target=copy.deepcopy(target),
                system_prompt=generate.load_guide_summary() + "\n\n" + instruction,
                user_message="【当前内容】\n" + json.dumps(target, ensure_ascii=False) + "\n【教师要求】\n" + (hint or "保持知识点正确，改善表达和教学清晰度。"), warnings=warnings)


def apply_proposal(prepared, raw, lesson_no):
    try:
        check_budget(raw)
    except ValueError as exc:
        raise editor.EditorError("AI_BUDGET", str(exc), 422) from exc
    candidate = copy.deepcopy(prepared["document"])
    if prepared["element_id"]:
        value = raw.get("element", raw) if isinstance(raw, dict) else None
        if not isinstance(value, dict):
            raise editor.EditorError("AI_SHAPE", "AI 未返回可用的元素对象", 422)
        target = next(n for _, n in walk_model(candidate) if n.get("id") == prepared["element_id"] and n.get("type") in CONTENT_FIELDS)
        allowed = CONTENT_FIELDS[target["type"]]
        if not any(key in value for key in allowed):
            raise editor.EditorError("AI_SHAPE", "AI 未返回可用的内容字段", 422)
        for key in allowed:
            if key in value:
                target[key] = copy.deepcopy(value[key])
    else:
        index = next(i for i, slide in enumerate(candidate["slides"]) if slide["id"] == prepared["slide_id"])
        target = generate._unwrap_slide_payload(raw, slide_index=index)
        if target is None:
            raise editor.EditorError("AI_SHAPE", "AI 未返回可用的单页对象", 422)
        target = copy.deepcopy(target)
        target["id"] = prepared["slide_id"]
        candidate["slides"][index] = target
    clean, warnings, diagnostics = editor.normalize_document(candidate, lesson_no)
    if any(d["destructive"] for d in diagnostics):
        raise editor.EditorError("AI_CONTENT_INVALID", "AI 结果未通过内容校验，原文未修改", 422, diagnostics=diagnostics)
    return dict(document=clean, warnings=prepared["warnings"] + warnings, revision=prepared["revision"],
                slide_id=prepared["slide_id"], element_id=prepared["element_id"])


def finish(conn, *, pack_id, teacher_id, lesson_no, proposal):
    pack = editor.owned_pack(conn, pack_id, teacher_id)
    state = editor._lesson_state(conn, pack, lesson_no)
    proposal["stale"] = editor.lesson_revision(conn, pack, lesson_no) != proposal["revision"] or bool(state and state.get("gen_status") in {"excluded", "queued", "running"})
    diagnostics = media.check_references(conn, pack, lesson_no, proposal["document"])
    if diagnostics:
        raise editor.EditorError("MEDIA_MISSING", "AI 结果引用了不可用素材，原文未修改", 422, diagnostics=diagnostics)
    return proposal
