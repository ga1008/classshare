from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any


MATERIAL_CHECK_VERSION = "material_mastery_check_v1"
MATERIAL_CHECK_PASS_COUNT = 2
OPTION_IDS = ("A", "B", "C", "D")


def _json_loads(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _clean_text(value: Any, *, limit: int = 96) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^[#>\-\s*\d.、]+", "", text)
    text = re.sub(r"\s+", " ", text).strip(" -:：，。；;")
    if len(text) > limit:
        text = text[: max(1, limit - 1)].rstrip() + "..."
    return text


def _first_sentence(value: Any, *, limit: int = 96) -> str:
    text = _clean_text(value, limit=240)
    if not text:
        return ""
    parts = re.split(r"[。！？!?]\s*", text, maxsplit=1)
    return _clean_text(parts[0] if parts else text, limit=limit)


def _extract_keywords(parse_payload: dict[str, Any]) -> list[str]:
    keywords: list[str] = []
    raw_keywords = parse_payload.get("keywords")
    if isinstance(raw_keywords, str):
        raw_items = re.split(r"[,，、;\s]+", raw_keywords)
    else:
        raw_items = _as_list(raw_keywords)
    for item in raw_items:
        text = _clean_text(item, limit=28)
        if text and text not in keywords:
            keywords.append(text)
    return keywords[:6]


def _extract_outline_titles(parse_payload: dict[str, Any]) -> list[str]:
    titles: list[str] = []
    for item in _as_list(parse_payload.get("outline")):
        title = item.get("title") if isinstance(item, dict) else item
        text = _clean_text(title, limit=64)
        if text and text not in titles:
            titles.append(text)
    return titles[:6]


def _option(option_id: str, text: str) -> dict[str, str]:
    return {"id": option_id, "text": text}


def _unique_options(correct_text: str, distractors: list[str], *, seed_key: str = "") -> tuple[list[dict[str, str]], str]:
    import hashlib

    options: list[str] = []
    clean_correct = _clean_text(correct_text, limit=88)
    for item in [clean_correct, *distractors]:
        text = _clean_text(item, limit=88)
        if text and text not in options:
            options.append(text)
        if len(options) >= 4:
            break
    generic = [
        "仅记住个别术语定义，未梳理概念之间的前后关联",
        "直接套用经验结论，跳过材料中的前提条件与例证",
        "只关注局部细节，未结合课程目标判断适用范围",
        "把不同场景下的步骤混为一谈，未区分关键差异",
    ]
    for item in generic:
        if len(options) >= 4:
            break
        if item not in options:
            options.append(item)
    # 确定性轮换正确选项位置，避免每道题答案固定在 A
    if len(options) > 1 and seed_key:
        digest = int(hashlib.sha256(seed_key.encode("utf-8")).hexdigest()[:8], 16)
        correct_index = digest % len(options)
        if correct_index != 0:
            options[0], options[correct_index] = options[correct_index], options[0]
    rendered = [_option(option_id, text) for option_id, text in zip(OPTION_IDS, options)]
    answer_id = next((opt["id"] for opt in rendered if opt["text"] == clean_correct), rendered[0]["id"])
    return rendered, answer_id


def _question(
    question_id: str,
    prompt: str,
    correct_text: str,
    distractors: list[str],
    explanation: str,
) -> dict[str, Any]:
    options, answer_id = _unique_options(correct_text, distractors, seed_key=f"{question_id}:{prompt}:{correct_text}")
    return {
        "id": question_id,
        "type": "single_choice",
        "prompt": _clean_text(prompt, limit=140),
        "options": options,
        "answer": answer_id,
        "explanation": _clean_text(explanation, limit=180),
    }


def build_material_check_failure_payload(reason: str, *, generated_at: str | None = None) -> dict[str, Any]:
    return {
        "version": MATERIAL_CHECK_VERSION,
        "status": "fallback",
        "reason": _clean_text(reason, limit=120) or "missing_parse_signal",
        "generated_at": generated_at or datetime.now().isoformat(),
        "questions": [],
        "pass_count": MATERIAL_CHECK_PASS_COUNT,
    }


def build_material_mastery_check_payload(
    parse_payload: dict[str, Any] | str | None,
    *,
    material_name: str = "",
    generated_at: str | None = None,
) -> dict[str, Any]:
    payload = _json_loads(parse_payload, {})
    if not isinstance(payload, dict):
        return build_material_check_failure_payload("invalid_parse_payload", generated_at=generated_at)

    summary = _first_sentence(payload.get("summary") or payload.get("content_summary"))
    keywords = _extract_keywords(payload)
    outline_titles = _extract_outline_titles(payload)
    teaching_value = _first_sentence(payload.get("teaching_value"), limit=90)
    cautions = _first_sentence(payload.get("cautions"), limit=90)
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    title = _clean_text(payload.get("title") or metadata.get("title"), limit=64)
    material_label = _clean_text(material_name or title or "这份材料", limit=56)

    questions: list[dict[str, Any]] = []
    if summary:
        questions.append(
            _question(
                "q1",
                f"以下哪一项最准确概括了《{material_label}》的核心主线？",
                f"围绕“{summary}”建立理解与联系",
                [
                    "仅记忆零散定义，无需关联具体案例与应用场景",
                    "将材料内容当作固定结论直接照搬，不分析前提条件",
                    "只关注局部操作步骤，忽略概念之间的因果逻辑",
                ],
                f"本题用于确认你是否抓住材料主线：{summary}",
            )
        )

    if keywords:
        keyword_text = "、".join(keywords[:3])
        questions.append(
            _question(
                "q2",
                "复盘这份材料时，哪一组关键词最能串联起其中的核心知识点？",
                keyword_text,
                [
                    "通用背景、泛化结论、课外拓展",
                    "常规格式、排版规范、篇幅要求",
                    "课前预习、课堂笔记、课后归档",
                ],
                f"抓住核心关键词（{keyword_text}）有助于把零散内容串成可迁移的知识框架。",
            )
        )

    if outline_titles or teaching_value or cautions:
        focus = outline_titles[0] if outline_titles else teaching_value or cautions
        questions.append(
            _question(
                "q3",
                "把这份材料运用到后续课堂任务时，第一步更稳妥的做法是？",
                f"先围绕“{focus}”梳理关键依据与典型例子",
                [
                    "直接套用以往类似题目的结论，不核对本课材料前提",
                    "先跳过核心概念，等做到具体题目时再临时回查",
                    "只摘抄最后一段总结，省略推导与论证过程",
                ],
                f"真正掌握材料，意味着能围绕“{focus}”拿出依据、讲清逻辑并应用到新情境。",
            )
        )

    if len(questions) < 2:
        return build_material_check_failure_payload("insufficient_parse_signal", generated_at=generated_at)

    questions = questions[:3]
    return {
        "version": MATERIAL_CHECK_VERSION,
        "status": "ready",
        "source": "ai_parse_result",
        "generated_at": generated_at or datetime.now().isoformat(),
        "pass_count": min(MATERIAL_CHECK_PASS_COUNT, len(questions)),
        "questions": questions,
    }


def normalize_material_mastery_check_payload(value: Any) -> dict[str, Any]:
    payload = _json_loads(value, {})
    if not isinstance(payload, dict):
        return build_material_check_failure_payload("invalid_check_payload")
    questions: list[dict[str, Any]] = []
    for index, item in enumerate(_as_list(payload.get("questions")), start=1):
        if not isinstance(item, dict):
            continue
        options = []
        seen_option_ids = set()
        for option in _as_list(item.get("options")):
            if isinstance(option, dict):
                option_id = _clean_text(option.get("id"), limit=4).upper()
                text = _clean_text(option.get("text"), limit=120)
            else:
                option_id = OPTION_IDS[len(options)] if len(options) < len(OPTION_IDS) else ""
                text = _clean_text(option, limit=120)
            if option_id not in OPTION_IDS or option_id in seen_option_ids or not text:
                continue
            seen_option_ids.add(option_id)
            options.append(_option(option_id, text))
        answer = _clean_text(item.get("answer"), limit=4).upper()
        if len(options) < 2 or answer not in {option["id"] for option in options}:
            continue
        prompt = _clean_text(item.get("prompt") or item.get("text"), limit=160)
        if not prompt:
            continue
        questions.append(
            {
                "id": _clean_text(item.get("id"), limit=32) or f"q{index}",
                "type": "single_choice",
                "prompt": prompt,
                "options": options[:4],
                "answer": answer,
                "explanation": _clean_text(item.get("explanation"), limit=220) or "回到材料的关键词和例证复核即可。",
            }
        )
    status = str(payload.get("status") or "").strip().lower()
    if status not in {"ready", "fallback", "disabled", "failed", "pending"}:
        status = "ready" if questions else "fallback"
    if status == "ready" and len(questions) < 2:
        status = "fallback"
    try:
        pass_count = int(payload.get("pass_count") or MATERIAL_CHECK_PASS_COUNT)
    except (TypeError, ValueError):
        pass_count = MATERIAL_CHECK_PASS_COUNT
    return {
        "version": str(payload.get("version") or MATERIAL_CHECK_VERSION),
        "status": status,
        "source": str(payload.get("source") or ""),
        "reason": str(payload.get("reason") or ""),
        "generated_at": str(payload.get("generated_at") or ""),
        "pass_count": max(1, min(pass_count, len(questions) or MATERIAL_CHECK_PASS_COUNT)),
        "questions": questions[:3],
    }


def public_material_mastery_check_payload(value: Any) -> dict[str, Any]:
    payload = normalize_material_mastery_check_payload(value)
    public_questions = []
    for item in payload.get("questions") or []:
        public_questions.append(
            {
                "id": item["id"],
                "type": "single_choice",
                "prompt": item["prompt"],
                "options": item["options"],
            }
        )
    return {
        "version": payload["version"],
        "status": payload["status"],
        "generated_at": payload["generated_at"],
        "pass_count": payload["pass_count"],
        "question_count": len(public_questions),
        "questions": public_questions,
    }


def grade_material_mastery_check(value: Any, answers: dict[str, Any]) -> dict[str, Any]:
    payload = normalize_material_mastery_check_payload(value)
    if payload.get("status") != "ready" or not payload.get("questions"):
        raise ValueError("当前材料没有可用的心法检验")
    normalized_answers = {
        str(key): _clean_text(value, limit=4).upper()
        for key, value in (answers or {}).items()
    }
    results = []
    correct_count = 0
    for question in payload["questions"]:
        selected = normalized_answers.get(str(question["id"]), "")
        correct = selected == question["answer"]
        correct_count += 1 if correct else 0
        results.append(
            {
                "id": question["id"],
                "selected": selected,
                "correct": correct,
                "correct_answer": question["answer"],
                "explanation": question["explanation"],
            }
        )
    pass_count = min(int(payload.get("pass_count") or MATERIAL_CHECK_PASS_COUNT), len(payload["questions"]))
    passed = correct_count >= pass_count
    return {
        "status": "passed" if passed else "retry",
        "passed": passed,
        "correct_count": correct_count,
        "total_count": len(payload["questions"]),
        "pass_count": pass_count,
        "results": results,
    }
