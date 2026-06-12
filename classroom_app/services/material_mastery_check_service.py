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


def _unique_options(correct_text: str, distractors: list[str]) -> list[dict[str, str]]:
    options: list[str] = []
    for item in [correct_text, *distractors]:
        text = _clean_text(item, limit=88)
        if text and text not in options:
            options.append(text)
        if len(options) >= 4:
            break
    generic = [
        "只停留在页面位置和字数，不建立概念联系",
        "跳过材料证据，直接等待标准答案",
        "把材料当作无关背景，不联系课堂任务",
        "只记住个别词语，不追问适用场景",
    ]
    for item in generic:
        if len(options) >= 4:
            break
        if item not in options:
            options.append(item)
    return [_option(option_id, text) for option_id, text in zip(OPTION_IDS, options)]


def _question(
    question_id: str,
    prompt: str,
    correct_text: str,
    distractors: list[str],
    explanation: str,
) -> dict[str, Any]:
    options = _unique_options(correct_text, distractors)
    return {
        "id": question_id,
        "type": "single_choice",
        "prompt": _clean_text(prompt, limit=140),
        "options": options,
        "answer": options[0]["id"],
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
                f"以下哪一项最贴近《{material_label}》的核心内容？",
                f"围绕“{summary}”建立理解",
                [
                    "只把材料当作课后阅读，不联系课堂问题",
                    "优先记住文件名和目录位置",
                    "略过材料中的例证和关键词",
                ],
                f"本题检验你是否抓住材料主线：{summary}",
            )
        )

    if keywords:
        keyword_text = "、".join(keywords[:3])
        questions.append(
            _question(
                "q2",
                "研读完成后，最应该用哪组关键词回扣本材料？",
                keyword_text,
                [
                    "签到、下载、翻页",
                    "分数、排名、提交时间",
                    "文件格式、页面尺寸、附件名称",
                ],
                f"关键词能帮助你把零散内容收束成可复述的知识点：{keyword_text}",
            )
        )

    if outline_titles or teaching_value or cautions:
        focus = outline_titles[0] if outline_titles else teaching_value or cautions
        questions.append(
            _question(
                "q3",
                "如果要把这份材料用于课堂任务，第一步最合理的做法是？",
                f"先围绕“{focus}”整理证据和例子",
                [
                    "先完成页面滚动，再决定是否理解",
                    "直接照搬结论，不核对材料依据",
                    "只看最后一段，跳过前后逻辑",
                ],
                f"掌握材料不只是读完，而是能围绕“{focus}”组织证据并应用。",
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
