from __future__ import annotations

import json
from typing import Any


VALID_QUESTION_TYPES = {"radio", "checkbox", "text", "textarea"}
EXAM_JSON_MAX_BYTES = 2 * 1024 * 1024


EXAM_JSON_TEMPLATE: dict[str, Any] = {
    "title": "试卷标题",
    "description": "试卷说明，可留空",
    "pages": [
        {
            "name": "第一部分",
            "questions": [
                {
                    "id": "p1_q1",
                    "type": "radio",
                    "text": "单选题题干",
                    "options": ["A. 选项一", "B. 选项二", "C. 选项三", "D. 选项四"],
                    "answer": "A",
                    "explanation": "解析说明，可留空",
                },
                {
                    "id": "p1_q2",
                    "type": "checkbox",
                    "text": "多选题题干",
                    "options": ["A. 选项一", "B. 选项二", "C. 选项三", "D. 选项四"],
                    "answer": ["A", "C"],
                    "explanation": "解析说明，可留空",
                },
                {
                    "id": "p1_q3",
                    "type": "text",
                    "text": "填空题题干",
                    "placeholder": "请输入简短答案",
                    "answer": "参考答案",
                    "explanation": "解析说明，可留空",
                },
                {
                    "id": "p1_q4",
                    "type": "textarea",
                    "text": "问答题题干",
                    "placeholder": "请写出完整作答过程",
                    "answer": "参考答案",
                    "explanation": "解析说明，可留空",
                },
            ],
        }
    ],
}


def get_exam_json_template_text() -> str:
    return json.dumps(EXAM_JSON_TEMPLATE, ensure_ascii=False, indent=2) + "\n"


def parse_exam_json_text(raw_text: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 格式错误：第 {exc.lineno} 行第 {exc.colno} 列，{exc.msg}") from exc
    return normalize_exam_json_payload(payload)


def normalize_exam_json_payload(payload: Any) -> dict[str, Any]:
    root = _unwrap_payload(payload)
    title = ""
    description = ""

    if isinstance(root, list):
        raw_pages: Any = [{"name": "试卷题目", "questions": root}]
    elif isinstance(root, dict):
        title = _first_text(root, ("title", "name", "试卷标题"))
        description = _first_text(root, ("description", "desc", "说明"))
        if "pages" in root:
            raw_pages = root["pages"]
        elif "questions" in root:
            raw_pages = [{"name": "试卷题目", "questions": root["questions"]}]
        else:
            raise ValueError("JSON 必须包含 pages 数组，或包含 questions 数组。")
    else:
        raise ValueError("JSON 根节点必须是对象或题目数组。")

    pages = _normalize_pages(raw_pages)
    stats = _collect_question_stats(pages)
    return {
        "title": title,
        "description": description,
        "questions": {"pages": pages},
        "stats": stats,
    }


def _unwrap_payload(payload: Any) -> Any:
    current = payload
    for _ in range(4):
        if not isinstance(current, dict):
            return current
        if "pages" in current or "questions" in current:
            return current
        for key in ("exam_data", "exam", "paper", "test", "quiz", "data", "result"):
            nested = current.get(key)
            if isinstance(nested, (dict, list)):
                current = nested
                break
        else:
            return current
    return current


def _normalize_pages(raw_pages: Any) -> list[dict[str, Any]]:
    if isinstance(raw_pages, dict):
        if "questions" in raw_pages:
            page_items = [raw_pages]
        else:
            page_items = [{"name": str(name), "questions": questions} for name, questions in raw_pages.items()]
    elif isinstance(raw_pages, list):
        if raw_pages and all(_looks_like_question(item) for item in raw_pages):
            page_items = [{"name": "试卷题目", "questions": raw_pages}]
        else:
            page_items = raw_pages
    else:
        raise ValueError("pages 必须是数组或对象。")

    pages: list[dict[str, Any]] = []
    total_index = 1
    for page_index, raw_page in enumerate(page_items, start=1):
        if isinstance(raw_page, list):
            page_name = f"第{page_index}部分"
            raw_questions = raw_page
        elif isinstance(raw_page, dict):
            page_name = _first_text(raw_page, ("name", "title", "section", "部分"), f"第{page_index}部分")
            raw_questions = raw_page.get("questions") or raw_page.get("items") or raw_page.get("题目") or []
        else:
            raise ValueError(f"第 {page_index} 个页面格式不正确。")

        if isinstance(raw_questions, dict):
            raw_questions = list(raw_questions.values())
        if not isinstance(raw_questions, list):
            raise ValueError(f"{page_name} 的 questions 必须是数组。")

        questions: list[dict[str, Any]] = []
        for question_index, raw_question in enumerate(raw_questions, start=1):
            question = _normalize_question(raw_question, page_index, question_index, total_index)
            questions.append(question)
            total_index += 1

        if questions:
            pages.append({"name": page_name, "questions": questions})

    if not pages:
        raise ValueError("JSON 中没有可导入的题目。")
    return pages


def _normalize_question(raw_question: Any, page_index: int, question_index: int, total_index: int) -> dict[str, Any]:
    if not isinstance(raw_question, dict):
        raise ValueError(f"第 {total_index} 题必须是对象。")

    question_type = _normalize_question_type(raw_question.get("type") or raw_question.get("question_type") or raw_question.get("题型"))
    options = _coerce_options(raw_question.get("options") or raw_question.get("choices") or raw_question.get("选项"))
    if question_type in {"radio", "checkbox"} and len(options) < 2:
        raise ValueError(f"第 {total_index} 题是选择题，至少需要 2 个选项。")

    text = _first_text(raw_question, ("text", "question", "question_text", "title", "stem", "content", "题目", "题干"))
    if not text:
        raise ValueError(f"第 {total_index} 题缺少题干 text。")

    answer = _normalize_answer(raw_question, question_type)
    question = dict(raw_question)
    question["id"] = str(question.get("id") or question.get("question_id") or f"p{page_index}_q{question_index}").strip()
    question["type"] = question_type
    question["text"] = text
    if options:
        question["options"] = options
    elif "options" in question:
        question.pop("options", None)
    question["answer"] = answer
    question["explanation"] = _first_text(question, ("explanation", "analysis", "解析"))

    if question_type in {"text", "textarea"}:
        placeholder = _first_text(question, ("placeholder", "hint", "提示"))
        if placeholder:
            question["placeholder"] = placeholder
    else:
        question.pop("placeholder", None)
    return question


def _normalize_question_type(raw_type: Any) -> str:
    normalized = str(raw_type or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "radio": {"radio", "single", "single_choice", "choice", "单选", "单选题", "选择题"},
        "checkbox": {"checkbox", "multiple", "multi", "multiple_choice", "multi_choice", "多选", "多选题"},
        "text": {"text", "fill", "fill_blank", "blank", "completion", "填空", "填空题"},
        "textarea": {"textarea", "essay", "short_answer", "qa", "question_answer", "问答", "问答题", "简答", "简答题", "主观题", "论述题"},
    }
    for canonical, values in aliases.items():
        if normalized in values:
            return canonical
    raise ValueError(f"不支持的题型：{raw_type!s}。题型必须是 radio、checkbox、text 或 textarea。")


def _coerce_options(raw_options: Any) -> list[str]:
    if raw_options is None:
        return []
    if isinstance(raw_options, dict):
        result = []
        for key, value in raw_options.items():
            key_text = str(key).strip()
            value_text = str(value).strip()
            result.append(f"{key_text}. {value_text}" if key_text and value_text else value_text or key_text)
        return [item for item in result if item]
    if isinstance(raw_options, list):
        return [str(item).strip() for item in raw_options if str(item).strip()]
    raise ValueError("选择题 options 必须是数组或对象。")


def _normalize_answer(raw_question: dict[str, Any], question_type: str) -> Any:
    if "answer" in raw_question:
        raw_answer = raw_question["answer"]
    else:
        raw_answer = raw_question.get("correct_answer", raw_question.get("correctAnswer", raw_question.get("答案", "")))

    if question_type == "checkbox":
        if isinstance(raw_answer, list):
            return [str(item).strip() for item in raw_answer if str(item).strip()]
        if isinstance(raw_answer, str):
            separators = [",", "，", ";", "；", "|", "、"]
            values = [raw_answer]
            for sep in separators:
                if sep in raw_answer:
                    values = raw_answer.split(sep)
                    break
            return [item.strip() for item in values if item.strip()]
        return []

    if isinstance(raw_answer, list):
        return str(raw_answer[0]).strip() if raw_answer else ""
    return "" if raw_answer is None else str(raw_answer).strip()


def _collect_question_stats(pages: list[dict[str, Any]]) -> dict[str, Any]:
    by_type = {question_type: 0 for question_type in VALID_QUESTION_TYPES}
    total = 0
    for page in pages:
        for question in page.get("questions", []):
            total += 1
            question_type = question.get("type")
            if question_type in by_type:
                by_type[question_type] += 1
    return {
        "pages": len(pages),
        "questions": total,
        "by_type": by_type,
    }


def _looks_like_question(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return any(key in value for key in ("type", "text", "question", "题干", "题目", "options", "choices"))


def _first_text(source: dict[str, Any], keys: tuple[str, ...], default: str = "") -> str:
    for key in keys:
        value = source.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return default
