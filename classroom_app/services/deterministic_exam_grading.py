from __future__ import annotations

import json
import math
import re
from typing import Any


OBJECTIVE_TYPES = {"radio", "checkbox", "text"}


def _load_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value) if value.strip() else {}
        except json.JSONDecodeError:
            return {}
    return value


def _answer_items(answers_json: Any) -> list[dict[str, Any]]:
    payload = _load_json(answers_json)
    answers = payload.get("answers", payload) if isinstance(payload, dict) else payload
    if isinstance(answers, dict):
        result = []
        for key, value in answers.items():
            result.append({"question_id": key, **value} if isinstance(value, dict) else {"question_id": key, "answer": value})
        return result
    return [item for item in (answers or []) if isinstance(item, dict)] if isinstance(answers, list) else []


def _question_items(exam_scoring_json: Any) -> tuple[list[dict[str, Any]], float | None]:
    payload = _load_json(exam_scoring_json)
    if not isinstance(payload, dict):
        return [], None
    grading = payload.get("grading") if isinstance(payload.get("grading"), dict) else {}
    try:
        total_score = float(grading.get("total_score"))
    except (TypeError, ValueError):
        total_score = None
    questions: list[dict[str, Any]] = []
    for page in payload.get("pages") or []:
        if not isinstance(page, dict):
            continue
        questions.extend(item for item in (page.get("questions") or []) if isinstance(item, dict))
    return questions, total_score


def _compact_text(value: Any) -> str:
    if isinstance(value, list):
        return "、".join(_compact_text(item) for item in value if _compact_text(item))
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalized_text(value: Any) -> str:
    text = _compact_text(value).casefold()
    return re.sub(r"[\s，,。；;：:、]+", "", text)


def _choice_token(value: Any, options: list[Any]) -> str:
    text = _compact_text(value).strip()
    if not text:
        return ""
    upper = text.upper()
    match = re.match(r"^\s*([A-Z])(?:[.、:：)）\s]|$)", upper)
    if match:
        return match.group(1)
    for index, option in enumerate(options):
        if _normalized_text(text) == _normalized_text(option):
            return chr(ord("A") + index)
    return upper


def _choice_set(value: Any, options: list[Any]) -> set[str]:
    if isinstance(value, list):
        raw_items = value
    else:
        text = _compact_text(value)
        raw_items = re.split(r"[,，;；|、\s]+", text) if text else []
    return {token for token in (_choice_token(item, options) for item in raw_items) if token}


def _number(value: Any) -> float | None:
    text = _compact_text(value).replace(",", "")
    match = re.fullmatch(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", text)
    if not match:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _has_answer_content(item: dict[str, Any]) -> bool:
    answer = item.get("answer", item.get("content", item.get("text", "")))
    if isinstance(answer, list):
        answer_present = any(_compact_text(value) for value in answer)
    else:
        answer_present = bool(_compact_text(answer))
    attachments = item.get("attachments") if isinstance(item.get("attachments"), list) else []
    return answer_present or bool(attachments)


def build_deterministic_grading_evidence(
    exam_scoring_json: Any,
    answers_json: Any,
) -> dict[str, Any]:
    """Build safe fixed scores and objective facts without subjective guessing.

    Only indisputable outcomes are fixed: blank-without-attachment is zero,
    radio is exact, checkbox exact matches get full credit, and exact/numeric
    short answers get full credit. Partial checkbox, non-equal text and essay
    answers remain for the multimodal model to judge against the rubric.
    """
    questions, total_score = _question_items(exam_scoring_json)
    answers = _answer_items(answers_json)
    if not questions or not answers:
        return {
            "available": False,
            "total_score": total_score,
            "questions": [],
            "fixed_scores": {},
        }

    answers_by_id: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(answers, start=1):
        question_id = str(item.get("question_id") or item.get("id") or item.get("question_no") or index).strip()
        answers_by_id[question_id.casefold()] = item

    evidence_questions: list[dict[str, Any]] = []
    fixed_scores: dict[str, dict[str, Any]] = {}
    for index, question in enumerate(questions, start=1):
        question_id = str(question.get("id") or f"q{index}").strip()
        answer_item = answers_by_id.get(question_id.casefold())
        if answer_item is None and index <= len(answers):
            answer_item = answers[index - 1]
        if answer_item is None:
            continue

        question_type = str(question.get("type") or answer_item.get("type") or "").strip().lower()
        try:
            points = float(question.get("points") or (question.get("grading") or {}).get("points") or 0)
        except (TypeError, ValueError):
            points = 0.0
        expected = question.get("answer")
        actual = answer_item.get("answer", answer_item.get("content", answer_item.get("text", "")))
        options = question.get("options") if isinstance(question.get("options"), list) else []
        fixed_score: float | None = None
        reason = "requires_model_judgment"

        if not _has_answer_content(answer_item):
            fixed_score = 0.0
            reason = "blank_without_attachment"
        elif question_type == "radio":
            fixed_score = points if _choice_token(actual, options) == _choice_token(expected, options) else 0.0
            reason = "exact_radio_match" if fixed_score == points else "wrong_radio_choice"
        elif question_type == "checkbox":
            expected_set = _choice_set(expected, options)
            actual_set = _choice_set(actual, options)
            if expected_set and actual_set == expected_set:
                fixed_score = points
                reason = "exact_checkbox_match"
            else:
                reason = "partial_or_wrong_checkbox_requires_rubric"
        elif question_type == "text":
            expected_number = _number(expected)
            actual_number = _number(actual)
            if expected_number is not None and actual_number is not None:
                if math.isclose(expected_number, actual_number, rel_tol=1e-9, abs_tol=1e-9):
                    fixed_score = points
                    reason = "exact_numeric_match"
                else:
                    reason = "numeric_mismatch_requires_rubric"
            elif _normalized_text(expected) and _normalized_text(actual) == _normalized_text(expected):
                fixed_score = points
                reason = "exact_text_match"

        item = {
            "question_no": index,
            "question_id": question_id,
            "type": question_type,
            "max_score": points,
            "expected": expected,
            "actual": actual,
            "reason": reason,
            "fixed_score": fixed_score,
        }
        evidence_questions.append(item)
        if fixed_score is not None:
            fixed_scores[question_id.casefold()] = item

    return {
        "available": bool(evidence_questions),
        "total_score": total_score,
        "questions": evidence_questions,
        "fixed_scores": fixed_scores,
    }


def format_deterministic_evidence_prompt(evidence: dict[str, Any]) -> str:
    if not evidence.get("available"):
        return ""
    lines = [
        "【系统确定性判分与核验证据】",
        "以下客观结果由服务端依据试卷标准答案计算。fixed_score 非空时必须原样采用；",
        "fixed_score 为空时仍需按评分指导判断，不得仅因表面完成就给满分。不要在学生反馈中暴露标准答案。",
    ]
    for item in evidence.get("questions") or []:
        expected_text = _compact_text(item.get("expected"))[:240]
        actual_text = _compact_text(item.get("actual"))[:240]
        fixed = item.get("fixed_score")
        fixed_text = "待模型按规则评分" if fixed is None else f"固定得分 {fixed:g}/{float(item.get('max_score') or 0):g}"
        lines.append(
            f"- 第{item.get('question_no')}题 id={item.get('question_id')} type={item.get('type')}；"
            f"{fixed_text}；判定={item.get('reason')}；标准={expected_text!r}；作答={actual_text!r}"
        )
    return "\n".join(lines)


def apply_deterministic_grading_result(
    result: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """Apply fixed objective scores, clamp per-question scores and recompute total."""
    if not evidence.get("available"):
        return result
    output = dict(result)
    raw_questions = [dict(item) for item in (result.get("questions") or []) if isinstance(item, dict)]
    evidence_questions = evidence.get("questions") or []
    evidence_by_id = {str(item.get("question_id") or "").casefold(): item for item in evidence_questions}
    fixed_changes: list[dict[str, Any]] = []

    for index, question in enumerate(raw_questions, start=1):
        question_id = str(question.get("question_id") or question.get("id") or "").casefold()
        item = evidence_by_id.get(question_id)
        if item is None and index <= len(evidence_questions):
            item = evidence_questions[index - 1]
        if item is None:
            continue
        max_score = float(item.get("max_score") or 0)
        question["max_score"] = max_score
        try:
            model_score = float(question.get("score") or 0)
        except (TypeError, ValueError):
            model_score = 0.0
        question["score"] = min(max_score, max(0.0, model_score))
        fixed_score = item.get("fixed_score")
        if fixed_score is not None:
            fixed_score = float(fixed_score)
            if not math.isclose(question["score"], fixed_score, abs_tol=1e-9):
                fixed_changes.append(
                    {
                        "question_id": item.get("question_id"),
                        "model_score": question["score"],
                        "fixed_score": fixed_score,
                    }
                )
            question["score"] = fixed_score
            if fixed_score == max_score:
                question["deduction_points"] = "无"
            elif item.get("reason") == "blank_without_attachment":
                question["deduction_points"] = "本题未作答且未提交对应附件"
            elif item.get("reason") == "wrong_radio_choice":
                question["deduction_points"] = "客观题答案不正确"

    total_max = sum(float(item.get("max_score") or 0) for item in evidence_questions)
    question_sum = sum(float(item.get("score") or 0) for item in raw_questions)
    original_score = float(result.get("score") or 0)
    normalized_score = question_sum
    if total_max > 0 and not math.isclose(total_max, 100.0, abs_tol=1e-9):
        normalized_score = question_sum / total_max * 100.0
    output["questions"] = raw_questions
    output["score"] = max(0, min(100, int(round(normalized_score))))
    score_sum_delta = abs(original_score - output["score"])

    audit = dict(output.get("_quality_audit") or {})
    audit.update(
        {
            "deterministic_fixed_count": len(evidence.get("fixed_scores") or {}),
            "deterministic_changes": fixed_changes,
            "score_before_recompute": original_score,
            "score_after_recompute": output["score"],
            "score_sum_delta": score_sum_delta,
        }
    )
    output["_quality_audit"] = audit
    return output
