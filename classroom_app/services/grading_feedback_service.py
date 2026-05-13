from __future__ import annotations

import json
import re
from typing import Any


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

