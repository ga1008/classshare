"""Small, dependency-free question vocabulary shared by assessment consumers.

Legacy storage types remain valid. Numbered blanks are opt-in: a historical
``text`` question without ``blanks`` is never split or rewritten heuristically.
"""
from __future__ import annotations

import re
from typing import Any


QUESTION_TYPE_LABELS = {
    "radio": "单选题", "checkbox": "多选题", "boolean": "判断题",
    "text": "填空题", "textarea": "简答题", "analysis": "分析题",
    "code": "程序题", "comprehensive": "综合题",
}
VALID_QUESTION_TYPES = frozenset(QUESTION_TYPE_LABELS)
SINGLE_CHOICE_TYPES = frozenset({"radio", "boolean"})
CHOICE_QUESTION_TYPES = SINGLE_CHOICE_TYPES | {"checkbox"}
LONG_ANSWER_TYPES = frozenset({"textarea", "analysis", "code", "comprehensive"})
TEXT_QUESTION_TYPES = LONG_ANSWER_TYPES | {"text"}
_TYPE_ALIASES = {
    "radio": {"radio", "single", "single_choice", "choice", "单选", "单选题", "选择题"},
    "checkbox": {"checkbox", "multiple", "multi", "multiple_choice", "multiple_select", "multi_choice", "多选", "多选题"},
    "boolean": {"boolean", "bool", "true_false", "truefalse", "judge", "judgement", "judgment", "判断", "判断题"},
    "text": {"text", "fill", "fill_blank", "blank", "completion", "填空", "填空题"},
    "textarea": {"textarea", "essay", "short_answer", "qa", "question_answer", "问答", "问答题", "简答", "简答题", "主观题", "论述题"},
    "analysis": {"analysis", "analyze", "analyse", "case_analysis", "分析", "分析题"},
    "code": {"code", "program", "programming", "coding", "程序", "程序题", "编程", "编程题"},
    "comprehensive": {"comprehensive", "composite", "integrated", "综合", "综合题"},
}
_BLANK_MARKER = re.compile(r"\{\{\s*([1-9][0-9]*)\s*\}\}")


def normalize_question_type(raw_type: Any) -> str:
    token = str(raw_type or "").strip().lower().replace("-", "_").replace(" ", "_")
    for canonical, aliases in _TYPE_ALIASES.items():
        if token in aliases:
            return canonical
    raise ValueError(f"不支持的题型：{raw_type!s}。请使用单选、多选、判断、填空、简答、分析、程序或综合题。")


def normalize_numbered_blanks(question: dict[str, Any], *, require_complete: bool = False) -> dict[str, Any]:
    """Normalize explicit numbered blanks, retaining empty positions in drafts.

    Each blank owns its answer; top-level ``answer`` is the ordered compatibility
    projection used by older grading/export consumers. No blank count is fixed.
    """
    if question.get("type") != "text" or "blanks" not in question:
        return question
    raw_blanks = question["blanks"]
    if not isinstance(raw_blanks, list):
        raise ValueError("填空题 blanks 必须是数组。")
    mirror = question.get("answer")
    blanks: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_blanks):
        if not isinstance(raw, dict):
            raise ValueError("每个填空必须包含编号 id 和答案 answer。")
        blank_id = str(raw.get("id", "")).strip()
        if not re.fullmatch(r"[1-9][0-9]*", blank_id) or blank_id in seen:
            raise ValueError("填空编号必须是不重复的正整数。")
        seen.add(blank_id)
        fallback = mirror[index] if isinstance(mirror, list) and index < len(mirror) else ""
        value = raw.get("answer", fallback)
        if value is not None and not isinstance(value, (str, int, float)):
            raise ValueError(f"填空 {blank_id} 的答案必须是文本。")
        blank = {"id": blank_id, "answer": "" if value is None else str(value).strip()}
        if isinstance(raw.get("placeholder"), str) and raw["placeholder"].strip():
            blank["placeholder"] = raw["placeholder"].strip()
        if require_complete and not blank["answer"]:
            raise ValueError(f"填空 {blank_id} 缺少标准答案。")
        blanks.append(blank)
    if require_complete:
        if not blanks:
            raise ValueError("填空题至少需要一个填空。")
        markers = _BLANK_MARKER.findall(str(question.get("text") or ""))
        if len(markers) != len(set(markers)):
            raise ValueError("题干中的填空编号不能重复，每个 {{编号}} 对应一个输入框。")
        if set(markers) != seen:
            raise ValueError("题干中的 {{编号}} 必须与填空答案编号一一对应。")
    return {**question, "blanks": blanks, "answer": [blank["answer"] for blank in blanks]}


def numbered_blank_answer_text(question: dict[str, Any], answer: Any) -> str:
    """Preserve answer positions in human-readable rubric/export text."""
    blanks = question.get("blanks")
    if isinstance(blanks, list) and isinstance(answer, list):
        return "；".join(
            f"（{blank.get('id', index + 1)}）{answer[index] if index < len(answer) and answer[index] is not None else ''}"
            for index, blank in enumerate(blanks) if isinstance(blank, dict)
        )
    return ""


def numbered_blank_questions(exam_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(question.get("id") or ""): question
        for page in exam_data.get("pages", []) or [] if isinstance(page, dict)
        for question in page.get("questions", []) or [] if isinstance(question, dict)
        if question.get("type") == "text" and isinstance(question.get("blanks"), list) and question["blanks"]
    }


def normalize_numbered_answer_payload(exam_data: dict[str, Any], payload: Any) -> Any:
    """Validate only opt-in numbered answers; retain legacy payloads verbatim.

    Empty legacy initialization values mean all blanks unanswered. Explicit
    arrays must preserve every position, including empty strings. This runs
    before file writes so a malformed answer cannot retire a saved attachment.
    """
    definitions = numbered_blank_questions(exam_data)
    if not definitions:
        return payload

    def normalize(item: Any, fallback_id: str = "") -> Any:
        qid = str(item.get("question_id") or item.get("id") or fallback_id) if isinstance(item, dict) else fallback_id
        question = definitions.get(qid)
        if question is None:
            return item
        answer = item.get("answer", "") if isinstance(item, dict) else item
        ids = [str(blank["id"]) for blank in question["blanks"]]
        if answer is None or (isinstance(answer, str) and not answer.strip()):
            answer = [""] * len(ids)
        if not isinstance(answer, list) or len(answer) != len(ids) or any(not isinstance(value, str) for value in answer):
            raise ValueError(f"题目 {qid} 的填空答案必须按编号分别填写（共 {len(ids)} 空）。")
        supplied_ids = item.get("blank_ids") if isinstance(item, dict) else None
        if supplied_ids is not None and (not isinstance(supplied_ids, list) or [str(value) for value in supplied_ids] != ids):
            raise ValueError(f"题目 {qid} 的填空编号已变化，请重新载入并核对草稿。")
        return {**(item if isinstance(item, dict) else {"question_id": qid}), "answer": answer.copy(), "blank_ids": ids}

    if isinstance(payload, list):
        return [normalize(item) for item in payload]
    if isinstance(payload, dict):
        return {key: normalize(value, str(key)) for key, value in payload.items()}
    return payload
