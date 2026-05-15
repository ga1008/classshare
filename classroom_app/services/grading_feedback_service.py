from __future__ import annotations

import json
import re
from typing import Any


QUESTION_EVALUATION_MAX_CHARS = 20
QUESTION_DEDUCTION_MAX_CHARS = 80
SUMMARY_MAX_CHARS = 120

FORBIDDEN_GRADING_FEEDBACK_MARKERS = (
    "测评师",
    "侧写",
    "心理画像",
    "隐藏画像",
    "后台画像",
    "隐藏心理",
    "内部分析",
    "隐藏提示",
    "系统提示",
    "profile_summary",
    "hidden_premise",
)


def clamp_score(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        score = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return max(0, min(100, score))


def extract_answer_question_targets(answers_json: str | dict[str, Any] | list[Any] | None) -> list[dict[str, str]]:
    payload: Any = answers_json
    if isinstance(answers_json, str):
        try:
            payload = json.loads(answers_json) if answers_json.strip() else {}
        except json.JSONDecodeError:
            return []
    answers = payload.get("answers", payload) if isinstance(payload, dict) else payload
    if isinstance(answers, dict):
        items = []
        for key, value in answers.items():
            if isinstance(value, dict):
                items.append({"question_id": key, **value})
            else:
                items.append({"question_id": key, "answer": value})
    elif isinstance(answers, list):
        items = [item for item in answers if isinstance(item, dict)]
    else:
        return []

    targets: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(items, start=1):
        raw_id = item.get("question_id") or item.get("question_no") or item.get("id") or index
        question_id = str(raw_id).strip() or str(index)
        question_text = str(item.get("question") or item.get("title") or "").strip()
        key = question_id.lower()
        if key in seen:
            continue
        seen.add(key)
        targets.append(
            {
                "index": str(index),
                "question_id": question_id,
                "question": question_text,
                "heading": f"第 {question_id} 题",
            }
        )
    return targets


def sanitize_student_feedback_text(feedback_md: Any) -> str:
    text = str(feedback_md or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""
    safe_lines: list[str] = []
    removed_any = False
    for line in text.split("\n"):
        if any(marker.lower() in line.lower() for marker in FORBIDDEN_GRADING_FEEDBACK_MARKERS):
            removed_any = True
            continue
        safe_lines.append(line)
    sanitized = "\n".join(safe_lines).strip()
    if removed_any and not sanitized:
        sanitized = "本次评语已完成安全清理，请结合题目反馈继续完善。"
    return sanitized


def _has_heading(text: str, heading: str) -> bool:
    return bool(re.search(rf"^\s*##\s*{re.escape(heading)}\s*$", text, flags=re.MULTILINE))


def _extract_existing_question_keys(text: str) -> set[str]:
    keys: set[str] = set()
    for match in re.finditer(r"^\s*###\s*(?:第\s*)?([A-Za-z0-9_\-一二三四五六七八九十百]+)\s*(?:题|Q)?", text, flags=re.MULTILINE):
        keys.add(match.group(1).strip().lower())
    return keys


def _default_overview(score: int | None, fallback_reason: str = "") -> str:
    if fallback_reason:
        return fallback_reason.strip()
    if score is None:
        return "本次批改已完成，但 AI 未返回可直接展示的总览评语。请结合逐题反馈继续修改。"
    return f"本次得分为 {score} 分。请根据下方逐题反馈检查错误点并继续完善。"


def _default_question_feedback(target: dict[str, str]) -> str:
    question_label = target.get("heading") or f"第 {target.get('index') or '?'} 题"
    return (
        f"### {question_label}\n"
        "- 答题错误：AI 未返回可精确对应到本题的错误点，请对照评分标准复核。\n"
        "- 图片/附件问题：未发现可自动归因的问题。\n"
        "- 多余或缺失内容：请检查是否漏写步骤、结论、截图或实验说明。\n"
        "- 改进建议：优先补齐题目要求中的关键步骤，并用清晰的截图或附件佐证。"
    )


def _compact_inline_text(value: Any, *, limit: int | None = None, default: str = "") -> str:
    text = sanitize_student_feedback_text(value)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return default
    if limit is not None and len(text) > limit:
        return text[:limit].rstrip()
    return text


def _coerce_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return round(number, 1) if number % 1 else int(number)


def _format_score_value(value: Any) -> str:
    number = _coerce_number(value)
    if number is None:
        return ""
    if isinstance(number, int):
        return str(number)
    return f"{number:.1f}".rstrip("0").rstrip(".")


def _extract_question_number(item: dict[str, Any], fallback: int) -> int:
    for key in ("question_no", "question_number", "question_index", "index", "no"):
        raw_value = item.get(key)
        if raw_value in (None, ""):
            continue
        match = re.search(r"\d+", str(raw_value))
        if match:
            return max(1, int(match.group(0)))

    raw_id = str(item.get("question_id") or item.get("id") or "").strip()
    match = re.search(r"\d+", raw_id)
    if match:
        return max(1, int(match.group(0)))
    return fallback


def _extract_question_items(payload: dict[str, Any]) -> list[Any]:
    for key in (
        "questions",
        "question_feedback",
        "question_feedbacks",
        "per_question_feedback",
        "per_question_feedbacks",
        "items",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def normalize_structured_grading_payload(
    payload: dict[str, Any] | None,
    *,
    answers_json: str | dict[str, Any] | list[Any] | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    item = payload or {}
    if not isinstance(item, dict):
        raise ValueError("批改结果必须是 JSON 对象")

    score = clamp_score(item.get("score"))
    if score is None:
        raise ValueError("总分 score 必须是 0-100 的数字")

    summary = _compact_inline_text(
        item.get("summary")
        or item.get("overall_feedback")
        or item.get("overall_comment")
        or item.get("general_comment")
        or item.get("总评"),
        limit=SUMMARY_MAX_CHARS if not strict else None,
    )
    if strict and not summary:
        raise ValueError("缺少总评 summary")
    if strict and len(summary) > SUMMARY_MAX_CHARS:
        raise ValueError(f"总评 summary 需控制在 {SUMMARY_MAX_CHARS} 字以内")

    targets = extract_answer_question_targets(answers_json)
    raw_questions = _extract_question_items(item)
    if strict and not raw_questions:
        raise ValueError("缺少逐题评分 questions 数组")
    if strict and targets and len(raw_questions) < len(targets):
        raise ValueError(f"逐题评分数量不足：期望至少 {len(targets)} 条，实际 {len(raw_questions)} 条")

    normalized_questions: list[dict[str, Any]] = []
    seen_numbers: set[int] = set()
    for index, raw_question in enumerate(raw_questions, start=1):
        if not isinstance(raw_question, dict):
            if strict:
                raise ValueError(f"第 {index} 条逐题评分必须是对象")
            continue

        question_no = _extract_question_number(raw_question, index)
        question_id = _compact_inline_text(
            raw_question.get("question_id") or raw_question.get("id") or f"q{question_no}",
            limit=40,
            default=f"q{question_no}",
        )
        question_score = _coerce_number(
            raw_question.get("score")
            if "score" in raw_question
            else raw_question.get("question_score")
            or raw_question.get("earned_score")
            or raw_question.get("points")
            or raw_question.get("得分")
        )
        max_score = _coerce_number(
            raw_question.get("max_score")
            or raw_question.get("full_score")
            or raw_question.get("total_score")
            or raw_question.get("满分")
        )
        deduction_points = _compact_inline_text(
            raw_question.get("deduction_points")
            or raw_question.get("deduction_point")
            or raw_question.get("deduction")
            or raw_question.get("lost_points")
            or raw_question.get("mistakes")
            or raw_question.get("扣分点")
            or raw_question.get("失分点"),
            default="无",
        )
        evaluation = _compact_inline_text(
            raw_question.get("evaluation")
            or raw_question.get("comment")
            or raw_question.get("feedback")
            or raw_question.get("评价"),
        )

        if strict:
            if question_no in seen_numbers:
                raise ValueError(f"第 {question_no} 题重复返回")
            if question_score is None:
                raise ValueError(f"第 {question_no} 题缺少本题得分 score")
            if max_score is not None and question_score > max_score:
                raise ValueError(f"第 {question_no} 题得分不能大于满分")
            if not deduction_points:
                raise ValueError(f"第 {question_no} 题缺少扣分点描述")
            if len(deduction_points) > QUESTION_DEDUCTION_MAX_CHARS:
                raise ValueError(f"第 {question_no} 题扣分点描述需控制在 {QUESTION_DEDUCTION_MAX_CHARS} 字以内")
            if not evaluation:
                raise ValueError(f"第 {question_no} 题缺少评价")
            if len(evaluation) > QUESTION_EVALUATION_MAX_CHARS:
                raise ValueError(f"第 {question_no} 题评价需控制在 {QUESTION_EVALUATION_MAX_CHARS} 字以内")
            if question_score == 0 and deduction_points == "无":
                raise ValueError(f"第 {question_no} 题为 0 分时必须写明扣分点")
            if max_score is not None and question_score < max_score and deduction_points == "无":
                raise ValueError(f"第 {question_no} 题非满分时扣分点不能写“无”")

        if not deduction_points:
            deduction_points = "无"
        normalized_questions.append(
            {
                "question_no": question_no,
                "question_id": question_id,
                "score": question_score,
                "max_score": max_score,
                "deduction_points": deduction_points,
                "evaluation": evaluation or "继续稳步完善",
            }
        )
        seen_numbers.add(question_no)

    if strict and not normalized_questions:
        raise ValueError("缺少可展示的逐题评分")

    return {
        **item,
        "score": score,
        "summary": summary,
        "questions": sorted(normalized_questions, key=lambda question: question["question_no"]),
    }


def build_structured_feedback_markdown(result: dict[str, Any]) -> str:
    summary = _compact_inline_text(result.get("summary"), default=_default_overview(result.get("score")))
    lines = ["## 总览评语", summary, "", "## 逐题反馈"]
    for question in result.get("questions") or []:
        question_no = question.get("question_no") or "?"
        score_text = _format_score_value(question.get("score")) or "-"
        max_score_text = _format_score_value(question.get("max_score"))
        if max_score_text:
            score_text = f"{score_text}/{max_score_text}"
        lines.extend(
            [
                "",
                f"### 第 {question_no} 题",
                f"- 本题得分：{score_text}",
                f"- 扣分点：{question.get('deduction_points') or '无'}",
                f"- 评价：{question.get('evaluation') or '继续稳步完善'}",
            ]
        )
    return "\n".join(lines).strip()


def validate_ai_grading_result(
    payload: dict[str, Any] | None,
    *,
    answers_json: str | dict[str, Any] | list[Any] | None = None,
) -> dict[str, Any]:
    result = normalize_structured_grading_payload(payload, answers_json=answers_json, strict=True)
    return {
        **result,
        "feedback_md": build_structured_feedback_markdown(result),
    }


def normalize_feedback_markdown(
    feedback_md: Any,
    *,
    answers_json: str | dict[str, Any] | list[Any] | None = None,
    score: int | None = None,
    fallback_reason: str = "",
) -> str:
    text = sanitize_student_feedback_text(feedback_md)
    targets = extract_answer_question_targets(answers_json)
    if not text:
        text = _default_overview(score, fallback_reason)

    has_overview = _has_heading(text, "总览评语")
    has_questions = _has_heading(text, "逐题反馈")

    if not has_overview or not has_questions:
        overview = text
        question_body = ""
        question_match = re.search(r"^\s*###\s+", text, flags=re.MULTILINE)
        if question_match:
            overview = text[: question_match.start()].strip()
            question_body = text[question_match.start():].strip()
        if not overview:
            overview = _default_overview(score, fallback_reason)
        text = f"## 总览评语\n{overview.strip()}\n\n## 逐题反馈"
        if question_body:
            text += f"\n\n{question_body}"

    existing_keys = _extract_existing_question_keys(text)
    missing_sections = []
    for target in targets:
        question_id = target["question_id"].lower()
        index = target["index"].lower()
        if question_id in existing_keys or index in existing_keys:
            continue
        missing_sections.append(_default_question_feedback(target))

    if missing_sections:
        text = text.rstrip() + "\n\n" + "\n\n".join(missing_sections)

    return text.strip()


def normalize_grading_result(
    payload: dict[str, Any] | None,
    *,
    answers_json: str | dict[str, Any] | list[Any] | None = None,
    fallback_reason: str = "",
) -> dict[str, Any]:
    item = payload or {}
    if isinstance(item, dict) and _extract_question_items(item):
        try:
            structured = normalize_structured_grading_payload(item, answers_json=answers_json, strict=False)
            return {
                **item,
                "score": structured["score"],
                "summary": structured.get("summary"),
                "questions": structured.get("questions") or [],
                "feedback_md": build_structured_feedback_markdown(structured),
            }
        except ValueError:
            pass

    score = clamp_score(item.get("score"))
    feedback_md = normalize_feedback_markdown(
        item.get("feedback_md") or item.get("feedback") or item.get("comment"),
        answers_json=answers_json,
        score=score,
        fallback_reason=fallback_reason,
    )
    return {
        **item,
        "score": score,
        "feedback_md": feedback_md,
    }

