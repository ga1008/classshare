from __future__ import annotations

import json
from typing import Any


VALID_QUESTION_TYPES = {"radio", "checkbox", "text", "textarea"}
EXAM_JSON_MAX_BYTES = 2 * 1024 * 1024

GRADING_STYLE_LABELS = {
    "strict": "严格",
    "medium": "中等",
    "loose": "宽松",
    "rescue": "捞一捞",
}

GRADING_STYLE_NOTES = {
    "strict": "严格按标准答案、关键步骤和证据评分；无明确依据不加分。",
    "medium": "以得分点为准，等价表达和合理过程可得相应分。",
    "loose": "核心理解正确时可给适度过程分，但关键错误仍要扣分。",
    "rescue": "尽量挖掘学生的有效努力和部分正确思路给分，但不能突破题目核心标准。",
}


EXAM_JSON_TEMPLATE: dict[str, Any] = {
    "title": "试卷标题",
    "description": "试卷说明，可留空",
    "exam_config": {
        "allow_student_ai": False,
        "ai_policy_note": "开放性实验、项目题或综合论述题可设为 true；客观闭卷题建议保持 false",
    },
    "grading": {
        "total_score": 100,
        "description": "整卷按各题标准答案、关键步骤和附件要求综合评分；总分为每题 points 之和。",
        "style": "medium",
        "style_options": {
            "strict": "严格",
            "medium": "中等",
            "loose": "宽松",
            "rescue": "捞一捞",
        },
    },
    "json_authoring_notes": [
        "题干、选项、解析均支持简易 Markdown，例如 **重点**、`命令`、列表和 ```代码块```。",
        "Markdown 中的反斜杠、双引号和换行必须保持合法 JSON 转义；推荐先让第三方 AI 输出 JSON，再用 JSON 校验器检查。",
        "如果某题允许学生答题时使用课堂 AI，可在题目上写 allow_ai: true；也可以只在 exam_config.allow_student_ai 开启整卷允许。",
        "附件要求只应写在 textarea 问答题里；min_count 大于 0 会被视为必传附件并在学生端显示必传标记。",
        "导入 JSON 必须包含 grading.total_score、grading.description、grading.style，以及每题 answer、points、grading_guidance、deduction_points；points 合计必须等于 total_score。",
    ],
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
                    "points": 10,
                    "grading_guidance": "选 A 得满分；答案等价表达可视为正确。",
                    "deduction_points": "选错不得分；多写无关内容不加分。",
                },
                {
                    "id": "p1_q2",
                    "type": "checkbox",
                    "text": "多选题题干",
                    "options": ["A. 选项一", "B. 选项二", "C. 选项三", "D. 选项四"],
                    "answer": ["A", "C"],
                    "explanation": "解析说明，可留空",
                    "points": 20,
                    "grading_guidance": "A、C 全选且无错选得满分；漏选或错选按扣分点处理。",
                    "deduction_points": "漏选一个正确项扣 50%；错选无关项最多得一半；关键项全错不得分。",
                },
                {
                    "id": "p1_q3",
                    "type": "text",
                    "text": "填空题题干",
                    "placeholder": "请输入简短答案",
                    "answer": "参考答案",
                    "explanation": "解析说明，可留空",
                    "points": 20,
                    "grading_guidance": "答案与参考答案含义一致得满分；大小写、同义词等按学科规则酌情接受。",
                    "deduction_points": "核心概念错误不得分；仅格式瑕疵可少量扣分。",
                },
                {
                    "id": "p1_q4",
                    "type": "textarea",
                    "text": "### 问答题题干\n请结合实验现象说明：\n\n- 关键步骤\n- 结果截图或代码片段\n- 你对异常情况的分析",
                    "placeholder": "请写出完整作答过程",
                    "answer": "参考答案",
                    "explanation": "解析说明，可留空",
                    "points": 50,
                    "grading_guidance": "围绕参考答案中的关键步骤、结论和证据评分；能用附件支撑过程可计入得分点。",
                    "deduction_points": "缺少关键步骤、结论错误、证据与题目无关或未满足附件要求时按比例扣分。",
                    "allow_ai": False,
                    "attachment_requirements": {
                        "enabled": True,
                        "required": False,
                        "min_count": 0,
                        "max_count": 3,
                        "allowed_file_types": [".png", ".jpg", ".pdf", ".py", ".txt"],
                        "allow_drawing": True,
                        "description": "可选：如实验截图、代码文件或报告；不需要时可省略该字段",
                    },
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
    return normalize_exam_json_payload(payload, require_grading=True)


def normalize_exam_json_payload(payload: Any, *, require_grading: bool = False) -> dict[str, Any]:
    root = _unwrap_payload(payload)
    title = ""
    description = ""
    exam_config: dict[str, Any] = {}
    raw_grading: Any = None

    if isinstance(root, list):
        raw_pages: Any = [{"name": "试卷题目", "questions": root}]
    elif isinstance(root, dict):
        title = _first_text(root, ("title", "name", "试卷标题"))
        description = _first_text(root, ("description", "desc", "说明"))
        raw_grading = root.get("grading") or root.get("scoring") or root.get("rubric") or root.get("评分标准")
        raw_config = root.get("exam_config") or root.get("config")
        if isinstance(raw_config, dict):
            exam_config = {
                "allow_student_ai": _coerce_bool(
                    raw_config.get("allow_student_ai", raw_config.get("student_ai_enabled", raw_config.get("allow_ai"))),
                    False,
                )
            }
        if "pages" in root:
            raw_pages = root["pages"]
        elif "questions" in root:
            raw_pages = [{"name": "试卷题目", "questions": root["questions"]}]
        else:
            raise ValueError("JSON 必须包含 pages 数组，或包含 questions 数组。")
    else:
        raise ValueError("JSON 根节点必须是对象或题目数组。")

    pages = _normalize_pages(raw_pages)
    questions_payload: dict[str, Any] = {"pages": pages}
    if raw_grading is not None:
        questions_payload["grading"] = raw_grading
    questions_payload = normalize_exam_scoring_payload(questions_payload, require_complete=require_grading)
    stats = _collect_question_stats(questions_payload["pages"])
    return {
        "title": title,
        "description": description,
        "exam_config": exam_config,
        "questions": questions_payload,
        "grading": questions_payload.get("grading", {}),
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

    attachment_requirements = _normalize_attachment_requirements(raw_question, question_type)
    if attachment_requirements:
        question["attachment_requirements"] = attachment_requirements
    else:
        question.pop("attachment_requirements", None)
        question.pop("attachment_requirement", None)
        question.pop("answer_attachments", None)
    allow_ai = _coerce_bool(
        raw_question.get("allow_ai", raw_question.get("allow_student_ai", raw_question.get("ai_allowed"))),
        False,
    )
    if allow_ai:
        question["allow_ai"] = True
    else:
        question.pop("allow_ai", None)
        question.pop("allow_student_ai", None)
        question.pop("ai_allowed", None)
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


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on", "required", "必须", "需要", "是"}:
        return True
    if normalized in {"0", "false", "no", "n", "off", "否", "不需要"}:
        return False
    return default


def _coerce_optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return max(0, parsed)


def _coerce_allowed_file_types(raw_value: Any) -> list[str]:
    if not raw_value:
        return []
    if isinstance(raw_value, str):
        items = raw_value.replace("\r", "\n").replace(";", ",").replace("，", ",").replace("、", ",").replace("\n", ",").split(",")
    elif isinstance(raw_value, (list, tuple, set)):
        items = list(raw_value)
    else:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in items:
        token = str(item or "").strip().lower()
        if not token:
            continue
        token = token if "/" in token or token.startswith(".") else f".{token.lstrip('.')}"
        if token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    return normalized


def _normalize_attachment_requirements(raw_question: dict[str, Any], question_type: str) -> dict[str, Any] | None:
    if question_type != "textarea":
        return None

    raw = (
        raw_question.get("attachment_requirements")
        or raw_question.get("attachment_requirement")
        or raw_question.get("answer_attachments")
        or raw_question.get("attachments")
    )
    if raw is None:
        direct_required = raw_question.get("requires_attachment", raw_question.get("attachment_required"))
        if direct_required is None:
            return None
        raw = {"required": direct_required}
    elif isinstance(raw, bool):
        raw = {"required": raw}
    elif isinstance(raw, str):
        raw = {"enabled": True, "description": raw}
    elif not isinstance(raw, dict):
        return None

    required = _coerce_bool(
        raw.get("required", raw.get("requires_attachment", raw.get("attachment_required"))),
        False,
    )
    enabled = _coerce_bool(raw.get("enabled"), True) or required
    if not enabled:
        return None

    min_count = _coerce_optional_int(raw.get("min_count", raw.get("min")))
    max_count = _coerce_optional_int(raw.get("max_count", raw.get("max")))
    if min_count is not None and min_count > 0:
        required = True
    if required and (min_count is None or min_count < 1):
        min_count = 1
    if max_count is not None and min_count is not None and max_count < min_count:
        max_count = min_count

    description = _first_text(raw, ("description", "requirement", "prompt", "hint", "说明", "要求"))
    allowed_file_types = _coerce_allowed_file_types(raw.get("allowed_file_types", raw.get("file_types")))
    normalized: dict[str, Any] = {
        "enabled": True,
        "required": required,
        "allow_drawing": _coerce_bool(raw.get("allow_drawing"), True),
    }
    if min_count is not None:
        normalized["min_count"] = min_count
    if max_count is not None:
        normalized["max_count"] = max_count
    if allowed_file_types:
        normalized["allowed_file_types"] = allowed_file_types
    if description:
        normalized["description"] = description
    return normalized


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


def normalize_exam_scoring_payload(
    exam_data: dict[str, Any],
    *,
    require_complete: bool = False,
) -> dict[str, Any]:
    """Normalize scoring fields stored inside exam questions_json.

    The normalized shape is additive and keeps existing pages/questions intact:
    root grading metadata lives in ``grading`` and each question carries answer,
    points, grading_guidance and deduction_points fields. Legacy or third-party
    JSON aliases are accepted so templates, AI output and hand-written imports
    converge before the paper is assigned or sent for AI grading.
    """
    if not isinstance(exam_data, dict):
        raise ValueError("试卷数据必须是对象。")

    raw_pages = exam_data.get("pages") or []
    if not isinstance(raw_pages, list):
        raise ValueError("试卷数据 pages 必须是数组。")

    root_grading = exam_data.get("grading") if isinstance(exam_data.get("grading"), dict) else {}
    total_score_raw = _first_existing(
        root_grading,
        exam_data,
        keys=("total_score", "total_points", "max_score", "score", "总分"),
    )
    total_score = _coerce_score(total_score_raw)
    description = _first_text_multi(
        root_grading,
        exam_data,
        keys=("description", "overall", "overview", "criteria", "rubric", "评分描述", "整体评分描述"),
    )
    style_raw = _first_existing(
        root_grading,
        exam_data,
        keys=("style", "grading_style", "strictness", "评分风格"),
    )
    style = _normalize_grading_style(style_raw)

    normalized_pages: list[dict[str, Any]] = []
    question_total = 0.0
    question_count = 0
    errors: list[str] = []

    for page_index, raw_page in enumerate(raw_pages, start=1):
        if not isinstance(raw_page, dict):
            continue
        page = dict(raw_page)
        raw_questions = raw_page.get("questions") or []
        if not isinstance(raw_questions, list):
            raw_questions = []

        questions: list[dict[str, Any]] = []
        for question_index, raw_question in enumerate(raw_questions, start=1):
            if not isinstance(raw_question, dict):
                continue
            question_count += 1
            question = dict(raw_question)
            question_id = str(question.get("id") or f"p{page_index}_q{question_index}").strip()
            question_type = str(question.get("type") or "").strip().lower()
            raw_q_grading = question.get("grading") if isinstance(question.get("grading"), dict) else {}

            points = _coerce_score(
                _first_existing(
                    raw_q_grading,
                    question,
                    keys=("points", "score", "max_score", "full_score", "分值", "满分"),
                )
            )
            guidance = _first_text_multi(
                raw_q_grading,
                question,
                keys=(
                    "guidance",
                    "grading_guidance",
                    "scoring_guidance",
                    "criteria",
                    "score_points",
                    "得分点",
                    "评分指导",
                ),
            )
            deduction_points = _first_text_multi(
                raw_q_grading,
                question,
                keys=(
                    "deduction_points",
                    "deductions",
                    "loss_points",
                    "mistakes",
                    "失分点",
                    "扣分点",
                ),
            )

            if points is not None:
                compact_points = _compact_score(points)
                question["points"] = compact_points
                question_total += points
            if guidance:
                question["grading_guidance"] = guidance
            if deduction_points:
                question["deduction_points"] = deduction_points

            if points is not None or guidance or deduction_points or raw_q_grading:
                question["grading"] = {
                    "points": _compact_score(points) if points is not None else None,
                    "guidance": guidance,
                    "deduction_points": deduction_points,
                }
                question["grading"] = {key: value for key, value in question["grading"].items() if value not in (None, "")}

            if require_complete:
                label = f"第 {question_count} 题（{question_id}）"
                if points is None or points <= 0:
                    errors.append(f"{label} 缺少有效 points 分值")
                if not _answer_has_value(question.get("answer")):
                    errors.append(f"{label} 缺少标准答案 answer")
                if not guidance:
                    errors.append(f"{label} 缺少评分指导 grading_guidance")
                if not deduction_points:
                    errors.append(f"{label} 缺少扣分点 deduction_points")

            questions.append(question)

        page["questions"] = questions
        normalized_pages.append(page)

    normalized = dict(exam_data)
    normalized["pages"] = normalized_pages

    if question_total > 0:
        if total_score is None:
            total_score = question_total
        elif abs(total_score - question_total) > 0.01:
            errors.append(
                f"每题 points 合计为 {_format_score(question_total)} 分，"
                f"与 grading.total_score={_format_score(total_score)} 不一致"
            )

    if require_complete:
        if question_count <= 0:
            errors.append("试卷中没有可评分的题目")
        if total_score is None or total_score <= 0:
            errors.append("缺少有效 grading.total_score")
        if not description:
            errors.append("缺少 grading.description 整体评分描述")
        if not style_raw or not style:
            errors.append("缺少有效 grading.style（strict、medium、loose、rescue 或中文：严格、中等、宽松、捞一捞）")
        if errors:
            raise ValueError("评分标准不完整：" + "；".join(errors[:12]))

    if total_score is not None or description or style or root_grading:
        normalized["grading"] = {
            "total_score": _compact_score(total_score) if total_score is not None else None,
            "description": description,
            "style": style or "medium",
            "style_label": GRADING_STYLE_LABELS.get(style or "medium", "中等"),
        }
        normalized["grading"] = {key: value for key, value in normalized["grading"].items() if value not in (None, "")}

    return normalized


def build_exam_rubric_md(
    *,
    title: str,
    description: str = "",
    exam_data: dict[str, Any],
    require_complete: bool = True,
) -> str:
    normalized = normalize_exam_scoring_payload(exam_data, require_complete=require_complete)
    grading = normalized.get("grading") if isinstance(normalized.get("grading"), dict) else {}
    total_score = _coerce_score(grading.get("total_score"))
    style = _normalize_grading_style(grading.get("style")) or "medium"
    style_label = GRADING_STYLE_LABELS.get(style, "中等")
    grading_description = str(grading.get("description") or "").strip()

    lines = [
        "## 试卷评分标准",
        "",
        f"- 试卷：{title or '未命名试卷'}",
        f"- 总分：{_format_score(total_score or 0)} 分",
        f"- 评分风格：{style_label}",
        f"- 风格说明：{GRADING_STYLE_NOTES.get(style, GRADING_STYLE_NOTES['medium'])}",
    ]
    if description:
        lines.append(f"- 试卷说明：{_clip_text(description, 220)}")
    if grading_description:
        lines.append(f"- 整体评分描述：{grading_description}")

    lines.append("")
    lines.append("### 每题标准")

    ordinal = 1
    for page in normalized.get("pages", []) or []:
        if not isinstance(page, dict):
            continue
        page_name = str(page.get("name") or "").strip()
        for question in page.get("questions", []) or []:
            if not isinstance(question, dict):
                continue
            question_type = str(question.get("type") or "").strip().lower()
            points = _coerce_score(_first_existing(question.get("grading") if isinstance(question.get("grading"), dict) else {}, question, keys=("points", "score", "max_score")))
            answer_text = _answer_to_text(question.get("answer"))
            guidance = _first_text_multi(
                question.get("grading") if isinstance(question.get("grading"), dict) else {},
                question,
                keys=("guidance", "grading_guidance", "scoring_guidance", "criteria", "score_points", "评分指导", "得分点"),
            )
            deduction_points = _first_text_multi(
                question.get("grading") if isinstance(question.get("grading"), dict) else {},
                question,
                keys=("deduction_points", "deductions", "loss_points", "mistakes", "失分点", "扣分点"),
            )
            question_text = _clip_text(question.get("text") or "", 180)
            prefix = f"{ordinal}. [{question.get('id') or f'q{ordinal}'}]"
            if page_name:
                prefix += f"（{page_name}）"
            lines.extend(
                [
                    "",
                    f"{prefix} {QUESTION_TYPE_LABELS.get(question_type, question_type or '题目')}，{_format_score(points or 0)} 分",
                    f"   - 题干：{question_text or '未填写题干'}",
                    f"   - 标准答案：{answer_text or '未填写'}",
                    f"   - 得分点：{guidance or '按标准答案和题目要求评分'}",
                    f"   - 失分点：{deduction_points or '答案错误、遗漏关键步骤或证据不足时扣分'}",
                ]
            )
            attachment = question.get("attachment_requirements") if isinstance(question.get("attachment_requirements"), dict) else {}
            if attachment:
                attachment_note = _format_attachment_requirement(attachment)
                if attachment_note:
                    lines.append(f"   - 附件要求：{attachment_note}")
            ordinal += 1

    return "\n".join(lines).strip() + "\n"


def strip_exam_scoring_for_student(exam_data: dict[str, Any]) -> dict[str, Any]:
    """Return a student-safe copy of exam questions without answers/rubrics."""
    if not isinstance(exam_data, dict):
        return {"pages": []}
    safe = {key: value for key, value in exam_data.items() if key not in {"grading", "scoring", "rubric", "answer_key"}}
    pages: list[dict[str, Any]] = []
    for raw_page in exam_data.get("pages", []) or []:
        if not isinstance(raw_page, dict):
            continue
        page = {key: value for key, value in raw_page.items() if key != "grading"}
        questions: list[dict[str, Any]] = []
        for raw_question in raw_page.get("questions", []) or []:
            if not isinstance(raw_question, dict):
                continue
            question = dict(raw_question)
            for key in (
                "answer",
                "correct_answer",
                "correctAnswer",
                "答案",
                "explanation",
                "analysis",
                "解析",
                "points",
                "score",
                "max_score",
                "full_score",
                "grading",
                "grading_guidance",
                "scoring_guidance",
                "deduction_points",
                "deductions",
                "loss_points",
                "score_points",
            ):
                question.pop(key, None)
            questions.append(question)
        page["questions"] = questions
        pages.append(page)
    safe["pages"] = pages
    return safe


QUESTION_TYPE_LABELS = {
    "radio": "单选题",
    "checkbox": "多选题",
    "text": "填空题",
    "textarea": "问答题",
}


def _first_existing(*sources: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in keys:
            if key in source and source.get(key) not in (None, ""):
                return source.get(key)
    return None


def _first_text_multi(*sources: dict[str, Any], keys: tuple[str, ...]) -> str:
    value = _first_existing(*sources, keys=keys)
    text = "" if value is None else str(value).strip()
    return "" if looks_like_garbled_scoring_text(text) else text


def looks_like_garbled_scoring_text(value: Any) -> bool:
    """Detect text that was already damaged into question-mark placeholders."""
    text = str(value or "").strip()
    if not text:
        return False
    compact = "".join(ch for ch in text if not ch.isspace())
    if len(compact) < 8:
        return False
    question_marks = compact.count("?")
    if question_marks < 4:
        return False
    meaningful_chars = sum(ch.isalnum() or "\u4e00" <= ch <= "\u9fff" for ch in compact)
    if question_marks / max(len(compact), 1) >= 0.35:
        return True
    punctuation_only = all(ch in "?/\\|,.;:，。、；：、（）()[]{}-_" for ch in compact)
    return punctuation_only and meaningful_chars == 0


def _normalize_grading_style(raw_style: Any) -> str:
    normalized = str(raw_style or "").strip().lower()
    aliases = {
        "strict": {"strict", "hard", "rigorous", "严格", "严"},
        "medium": {"medium", "normal", "standard", "balanced", "中等", "中", "标准"},
        "loose": {"loose", "lenient", "easy", "宽松", "宽"},
        "rescue": {"rescue", "salvage", "very_lenient", "捞一捞", "捞捞", "捞", "鼓励"},
    }
    for style, values in aliases.items():
        if normalized in values:
            return style
    return ""


def _coerce_score(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if score != score or score in (float("inf"), float("-inf")):
        return None
    return max(0.0, score)


def _compact_score(score: float) -> int | float:
    if abs(score - round(score)) < 0.0001:
        return int(round(score))
    return round(score, 2)


def _format_score(score: float | int) -> str:
    value = float(score)
    if abs(value - round(value)) < 0.0001:
        return str(int(round(value)))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _answer_has_value(answer: Any) -> bool:
    if isinstance(answer, list):
        return any(str(item or "").strip() for item in answer)
    if isinstance(answer, dict):
        return any(_answer_has_value(value) for value in answer.values())
    return bool(str(answer or "").strip())


def _answer_to_text(answer: Any) -> str:
    if isinstance(answer, list):
        return "、".join(str(item).strip() for item in answer if str(item).strip())
    if isinstance(answer, dict):
        return json.dumps(answer, ensure_ascii=False)
    return str(answer or "").strip()


def _clip_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + "…"


def _format_attachment_requirement(raw: dict[str, Any]) -> str:
    parts = []
    min_count = raw.get("min_count") or raw.get("min")
    max_count = raw.get("max_count") or raw.get("max")
    required = _coerce_bool(raw.get("required"), False)
    try:
        min_count_int = int(min_count or 0)
    except (TypeError, ValueError):
        min_count_int = 0
    if required or min_count_int > 0:
        parts.append(f"至少 {max(min_count_int, 1)} 个")
    elif raw.get("enabled"):
        parts.append("可选")
    if max_count not in (None, ""):
        parts.append(f"最多 {max_count} 个")
    allowed = raw.get("allowed_file_types") or raw.get("file_types") or []
    if isinstance(allowed, list) and allowed:
        parts.append("类型：" + "、".join(str(item) for item in allowed[:8]))
    description = str(raw.get("description") or raw.get("requirement") or raw.get("prompt") or "").strip()
    if description:
        parts.append(description)
    return "；".join(parts)


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
