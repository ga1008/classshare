from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from typing import Any

from .academic_service import china_now


REVIEW_STATUS_OPEN = "open"
REVIEW_STATUS_REVIEWING = "reviewing"
REVIEW_STATUS_MASTERED = "mastered"
REVIEW_STATUSES = {REVIEW_STATUS_OPEN, REVIEW_STATUS_REVIEWING, REVIEW_STATUS_MASTERED}

REVIEW_STATUS_LABELS = {
    REVIEW_STATUS_OPEN: "待复盘",
    REVIEW_STATUS_REVIEWING: "复盘中",
    REVIEW_STATUS_MASTERED: "已掌握",
}

REVIEW_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
REVIEW_SEVERITY_LABELS = {
    "high": "优先复盘",
    "medium": "需要巩固",
    "low": "轻量复习",
}

NEUTRAL_FEEDBACK_PHRASES = (
    "无",
    "暂无",
    "没有",
    "未发现明显问题",
    "未发现问题",
    "无明显问题",
    "完全正确",
    "答案正确",
    "满分",
    "不影响",
)

GENERIC_FALLBACK_FEEDBACK_PHRASES = (
    "AI 未返回可精确对应到本题的错误点",
    "请检查是否漏写步骤、结论、截图或实验说明",
)

ISSUE_FIELD_LABELS = {
    "答题错误",
    "图片/附件问题",
    "多余或缺失内容",
    "扣分点",
    "扣分点描述",
    "失分点",
}


def _now_iso() -> str:
    return china_now().replace(tzinfo=None, microsecond=0).isoformat()


def _json_loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _compact_text(value: Any, *, limit: int = 240) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"`{3}[\s\S]*?`{3}", " ", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*_>#~]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if limit > 0 and len(text) > limit:
        return text[:limit].rstrip() + "..."
    return text


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_score(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return ""
    return str(int(number)) if number.is_integer() else f"{number:.1f}".rstrip("0").rstrip(".")


def _score_percent(score: Any, max_score: Any = None) -> int | None:
    score_value = _safe_float(score)
    if score_value is None:
        return None
    max_value = _safe_float(max_score)
    if max_value is None or max_value <= 0:
        max_value = 100
    return max(0, min(100, int(round(score_value / max_value * 100))))


def _is_neutral_feedback(value: Any) -> bool:
    text = _compact_text(value, limit=80).lower()
    if not text:
        return True
    return any(phrase.lower() in text for phrase in NEUTRAL_FEEDBACK_PHRASES)


def _extract_score_pair(text: str) -> tuple[float | None, float | None]:
    raw = str(text or "")
    pair = re.search(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", raw)
    if pair:
        return float(pair.group(1)), float(pair.group(2))
    bracket_pair = re.search(r"[（(]\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*[)）]", raw)
    if bracket_pair:
        return float(bracket_pair.group(1)), float(bracket_pair.group(2))
    number = re.search(r"(?:得分|score|本题得分)\s*[：:]\s*(\d+(?:\.\d+)?)", raw, flags=re.I)
    if number:
        return float(number.group(1)), None
    return None, None


def _normalize_question_key(raw: Any, fallback: int) -> str:
    text = _compact_text(raw, limit=48)
    if not text:
        return f"section-{fallback}"
    number = re.search(r"\d+", text)
    if number:
        return f"q{int(number.group(0))}"
    key = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "-", text).strip("-").lower()
    return key[:48] or f"section-{fallback}"


def _question_heading(line: str) -> tuple[str, str] | None:
    text = str(line or "").strip()
    match = re.match(
        r"^#{1,6}\s*(?:第\s*)?([0-9A-Za-z_-]+)\s*(?:题|问|小题)?(?:\s*[：:.\-、]\s*(.*))?$",
        text,
        flags=re.I,
    )
    if match:
        question_no = match.group(1).strip()
        title = match.group(2).strip() if match.group(2) else f"第 {question_no} 题"
        return question_no, title
    return None


def _split_question_sections(feedback_md: str) -> list[dict[str, Any]]:
    raw = str(feedback_md or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return []

    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in raw.split("\n"):
        heading = _question_heading(line)
        if heading:
            if current and _compact_text("\n".join(current["lines"]), limit=20):
                sections.append(current)
            question_no, title = heading
            current = {
                "question_no": question_no,
                "title": title,
                "lines": [],
            }
            continue
        if current is not None:
            current["lines"].append(line)
    if current and _compact_text("\n".join(current["lines"]), limit=20):
        sections.append(current)
    if sections:
        return sections

    bullet_sections: list[dict[str, Any]] = []
    for index, line in enumerate(raw.split("\n"), start=1):
        cleaned = line.strip()
        if not cleaned:
            continue
        match = re.match(r"^\s*(?:[-*+]|\d+[.)、])\s*(?:\*\*)?(.+?)(?:\*\*)?\s*[：:]\s*(.+)$", cleaned)
        if not match:
            continue
        title = _compact_text(match.group(1), limit=90)
        body = match.group(2).strip()
        score, max_score = _extract_score_pair(title)
        if score is None:
            score, max_score = _extract_score_pair(body)
        if score is None and not _looks_like_issue(body):
            continue
        bullet_sections.append(
            {
                "question_no": str(index),
                "title": title or f"复盘片段 {index}",
                "lines": [body],
                "score": score,
                "max_score": max_score,
            }
        )
    return bullet_sections


def _parse_labeled_feedback(lines: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "score": None,
        "max_score": None,
        "deduction_points": "",
        "evaluation": "",
        "suggestion": "",
        "issue_parts": [],
    }
    for line in lines:
        text = str(line or "").strip()
        if not text:
            continue
        text = re.sub(r"^\s*(?:[-*+]|\d+[.)、])\s*", "", text).replace("**", "").strip()
        label_match = re.match(
            r"^(本题得分|得分|score|扣分点描述|扣分点|失分点|答题错误|图片/附件问题|多余或缺失内容|改进建议|评价|评语|evaluation)\s*[：:]\s*(.*)$",
            text,
            flags=re.I,
        )
        if label_match:
            label = label_match.group(1)
            value = label_match.group(2).strip()
            if label.lower() in {"本题得分", "得分", "score"}:
                score, max_score = _extract_score_pair(value)
                result["score"] = score
                result["max_score"] = max_score
            elif label in {"扣分点描述", "扣分点", "失分点"}:
                result["deduction_points"] = _compact_text(value, limit=180)
                if not _is_neutral_feedback(value):
                    result["issue_parts"].append(result["deduction_points"])
            elif label in {"评价", "评语", "evaluation"}:
                result["evaluation"] = _compact_text(value, limit=120)
            elif label == "改进建议":
                result["suggestion"] = _compact_text(value, limit=180)
            elif label in ISSUE_FIELD_LABELS:
                value_text = _compact_text(value, limit=180)
                if not _is_neutral_feedback(value_text):
                    result["issue_parts"].append(value_text)
            continue

        score, max_score = _extract_score_pair(text)
        if score is not None and result["score"] is None:
            result["score"] = score
            result["max_score"] = max_score
        if _looks_like_issue(text):
            result["issue_parts"].append(_compact_text(text, limit=180))
    return result


def _looks_like_issue(text: Any) -> bool:
    compact = _compact_text(text, limit=240)
    if not compact or _is_neutral_feedback(compact):
        return False
    if any(phrase in compact for phrase in GENERIC_FALLBACK_FEEDBACK_PHRASES):
        return False
    return bool(
        re.search(
            r"(扣\d|扣\s*\d|错误|错选|漏选|遗漏|缺失|未完成|未提供|未清晰|不正确|不准确|不完整|无法|失败|偏差|混淆|漏写)",
            compact,
        )
    )


def _severity_for_item(score: Any, max_score: Any, has_issue: bool, fallback_score: Any = None) -> str:
    percent = _score_percent(score, max_score)
    if percent is None:
        percent = _score_percent(fallback_score, 100)
    if percent is not None:
        if percent < 60:
            return "high"
        if percent < 85:
            return "medium"
    return "medium" if has_issue else "low"


def _extract_exam_question_lookup(exam_questions_json: Any) -> dict[str, dict[str, Any]]:
    payload = _json_loads(exam_questions_json, {})
    pages = payload.get("pages") if isinstance(payload, dict) else []
    lookup: dict[str, dict[str, Any]] = {}
    index = 0
    for page in pages or []:
        if not isinstance(page, dict):
            continue
        for question in page.get("questions") or []:
            if not isinstance(question, dict):
                continue
            index += 1
            question_id = str(question.get("id") or f"q{index}").strip()
            title = _compact_text(question.get("text") or question.get("title") or question.get("question"), limit=220)
            item = {
                "question_id": question_id,
                "question_no": index,
                "title": title or f"第 {index} 题",
                "type": str(question.get("type") or ""),
            }
            for key in {question_id, f"q{index}", str(index)}:
                lookup[_normalize_question_key(key, index)] = item
    return lookup


def _format_answer_value(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, dict):
        for key in ("answer", "content", "text", "value"):
            if key in value:
                return _format_answer_value(value.get(key))
        if value.get("choices"):
            return _format_answer_value(value.get("choices"))
        return _compact_text(json.dumps(value, ensure_ascii=False), limit=220)
    if isinstance(value, list):
        return "、".join(_compact_text(item, limit=60) for item in value if item not in (None, ""))
    return _compact_text(value, limit=220)


def _extract_answer_lookup(answers_json: Any) -> dict[str, str]:
    payload = _json_loads(answers_json, {})
    answers = payload.get("answers", payload) if isinstance(payload, dict) else payload
    lookup: dict[str, str] = {}
    if isinstance(answers, list):
        for index, item in enumerate(answers, start=1):
            if not isinstance(item, dict):
                lookup[f"q{index}"] = _format_answer_value(item)
                continue
            question_id = item.get("question_id") or item.get("id") or item.get("question") or index
            answer_text = _format_answer_value(item)
            for key in {question_id, f"q{index}", str(index)}:
                lookup[_normalize_question_key(key, index)] = answer_text
    elif isinstance(answers, dict):
        for index, (key, value) in enumerate(answers.items(), start=1):
            answer_text = _format_answer_value(value)
            for candidate in {key, f"q{index}", str(index)}:
                lookup[_normalize_question_key(candidate, index)] = answer_text
    return lookup


def extract_feedback_review_items(
    *,
    feedback_md: Any,
    answers_json: Any = None,
    exam_questions_json: Any = None,
    submission_score: Any = None,
) -> list[dict[str, Any]]:
    sections = _split_question_sections(str(feedback_md or ""))
    question_lookup = _extract_exam_question_lookup(exam_questions_json)
    answer_lookup = _extract_answer_lookup(answers_json)
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, section in enumerate(sections, start=1):
        details = _parse_labeled_feedback(section.get("lines") or [])
        score = section.get("score") if section.get("score") is not None else details.get("score")
        max_score = section.get("max_score") if section.get("max_score") is not None else details.get("max_score")
        raw_markdown = "\n".join(section.get("lines") or []).strip()
        if any(phrase in raw_markdown for phrase in GENERIC_FALLBACK_FEEDBACK_PHRASES):
            continue
        title = _compact_text(section.get("title") or "", limit=120) or f"第 {index} 题"
        key = _normalize_question_key(section.get("question_no") or title, index)
        question_meta = question_lookup.get(key) or question_lookup.get(f"q{index}") or {}
        if question_meta.get("title"):
            title = question_meta["title"]
        has_issue = bool(details.get("issue_parts")) or (
            score is not None
            and max_score is not None
            and float(score) < float(max_score)
        ) or _looks_like_issue(raw_markdown)
        if not has_issue and score is not None and _score_percent(score, max_score) == 100:
            continue
        if not has_issue and not _looks_like_issue(title):
            continue
        unique_key = key
        suffix = 2
        while unique_key in seen:
            unique_key = f"{key}-{suffix}"
            suffix += 1
        seen.add(unique_key)
        deduction_points = details.get("deduction_points") or "; ".join(details.get("issue_parts") or [])
        items.append(
            {
                "question_key": unique_key,
                "question_no": section.get("question_no") or str(index),
                "title": title,
                "score": _format_score(score),
                "max_score": _format_score(max_score),
                "score_percent": _score_percent(score, max_score),
                "deduction_points": deduction_points or "这条反馈没有明确扣分点，请对照总评复核。",
                "evaluation": details.get("evaluation") or "",
                "suggestion": details.get("suggestion") or "先把扣分原因用自己的话复述一遍，再回到原题修正答案。",
                "answer_preview": answer_lookup.get(unique_key) or answer_lookup.get(key) or answer_lookup.get(f"q{index}") or "",
                "feedback_excerpt": _compact_text(raw_markdown, limit=260),
                "severity": _severity_for_item(score, max_score, has_issue, submission_score),
            }
        )

    if not items and _safe_float(submission_score) is not None and float(submission_score) < 100:
        items.append(
            {
                "question_key": "overall",
                "question_no": "",
                "title": "整体反馈复盘",
                "score": _format_score(submission_score),
                "max_score": "100",
                "score_percent": _score_percent(submission_score, 100),
                "deduction_points": "这次批改没有稳定拆出逐题扣分点，先从总评中提炼最需要修正的一点。",
                "evaluation": "",
                "suggestion": "读完总评后，写下一个下次提交前可检查的动作。",
                "answer_preview": "",
                "feedback_excerpt": _compact_text(feedback_md, limit=260),
                "severity": _severity_for_item(submission_score, 100, True),
            }
        )
    return items


def _fetch_review_rows(
    conn: sqlite3.Connection,
    *,
    student_id: int,
    course_id: int | None = None,
    limit: int = 120,
) -> list[dict[str, Any]]:
    params: list[Any] = [int(student_id)]
    course_filter = ""
    if course_id:
        course_filter = "AND a.course_id = ?"
        params.append(int(course_id))
    params.append(max(1, min(int(limit), 240)))
    rows = conn.execute(
        f"""
        SELECT s.id AS submission_id,
               s.assignment_id,
               s.student_pk_id,
               s.status AS submission_status,
               s.score,
               s.feedback_md,
               s.answers_json,
               s.submitted_at,
               s.score_before_late_penalty,
               s.late_penalty_points,
               a.title AS assignment_title,
               a.exam_paper_id,
               a.class_offering_id,
               a.course_id,
               c.name AS course_name,
               cl.name AS class_name,
               ep.questions_json AS exam_questions_json
        FROM submissions s
        JOIN assignments a ON a.id = s.assignment_id
        JOIN courses c ON c.id = a.course_id
        LEFT JOIN class_offerings o ON o.id = a.class_offering_id
        LEFT JOIN classes cl ON cl.id = o.class_id
        LEFT JOIN exam_papers ep ON ep.id = a.exam_paper_id
        WHERE s.student_pk_id = ?
          AND COALESCE(s.is_absence_score, 0) = 0
          AND (s.status = 'graded' OR s.score IS NOT NULL)
          AND s.feedback_md IS NOT NULL
          AND TRIM(s.feedback_md) != ''
          {course_filter}
        ORDER BY COALESCE(s.submitted_at, '') DESC, s.id DESC
        LIMIT ?
        """,
        tuple(params),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_review_states(
    conn: sqlite3.Connection,
    *,
    student_id: int,
    submission_ids: list[int],
) -> dict[tuple[int, str], dict[str, Any]]:
    if not submission_ids:
        return {}
    placeholders = ",".join("?" for _ in submission_ids)
    rows = conn.execute(
        f"""
        SELECT *
        FROM student_feedback_review_notes
        WHERE student_id = ?
          AND submission_id IN ({placeholders})
        """,
        (int(student_id), *submission_ids),
    ).fetchall()
    return {
        (int(row["submission_id"]), str(row["question_key"])): dict(row)
        for row in rows
    }


def _decorate_item(row: dict[str, Any], item: dict[str, Any], state: dict[str, Any] | None) -> dict[str, Any]:
    status = str((state or {}).get("status") or REVIEW_STATUS_OPEN)
    if status not in REVIEW_STATUSES:
        status = REVIEW_STATUS_OPEN
    is_exam = bool(row.get("exam_paper_id"))
    assignment_id = row.get("assignment_id")
    source_url = f"/exam/take/{assignment_id}" if is_exam else f"/assignment/{assignment_id}"
    score_text = item.get("score") or ""
    if item.get("max_score"):
        score_text = f"{score_text or '-'}/{item['max_score']}"
    return {
        **item,
        "id": f"{row['submission_id']}:{item['question_key']}",
        "submission_id": int(row["submission_id"]),
        "assignment_id": str(assignment_id),
        "assignment_title": str(row.get("assignment_title") or "未命名任务"),
        "course_id": _safe_int(row.get("course_id")),
        "course_name": str(row.get("course_name") or "未命名课程"),
        "class_name": str(row.get("class_name") or ""),
        "submitted_at": str(row.get("submitted_at") or ""),
        "submission_score": _format_score(row.get("score")),
        "score_text": score_text,
        "source_url": source_url,
        "source_label": "回到试卷" if is_exam else "回到作业",
        "source_type": "exam" if is_exam else "assignment",
        "status": status,
        "status_label": REVIEW_STATUS_LABELS.get(status, "待复盘"),
        "severity_label": REVIEW_SEVERITY_LABELS.get(str(item.get("severity") or "medium"), "需要巩固"),
        "pinned": bool((state or {}).get("pinned")),
        "reflection": str((state or {}).get("reflection") or ""),
        "next_action": str((state or {}).get("next_action") or ""),
        "reviewed_at": str((state or {}).get("reviewed_at") or ""),
        "mastered_at": str((state or {}).get("mastered_at") or ""),
        "updated_at": str((state or {}).get("updated_at") or ""),
    }


def _item_matches_keyword(item: dict[str, Any], keyword: str) -> bool:
    text = keyword.strip().lower()
    if not text:
        return True
    haystack = " ".join(
        str(item.get(key) or "")
        for key in (
            "assignment_title",
            "course_name",
            "class_name",
            "title",
            "deduction_points",
            "evaluation",
            "suggestion",
            "answer_preview",
            "feedback_excerpt",
            "reflection",
            "next_action",
        )
    ).lower()
    return text in haystack


def _datetime_sort_value(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        return datetime.fromisoformat(text.replace("Z", "")).timestamp()
    except ValueError:
        return 0


def _sort_review_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    status_order = {REVIEW_STATUS_OPEN: 0, REVIEW_STATUS_REVIEWING: 1, REVIEW_STATUS_MASTERED: 2}
    return sorted(
        items,
        key=lambda item: (
            0 if item.get("pinned") else 1,
            status_order.get(str(item.get("status")), 9),
            REVIEW_SEVERITY_ORDER.get(str(item.get("severity") or "medium"), 1),
            -_datetime_sort_value(item.get("submitted_at")),
            -int(item.get("submission_id") or 0),
        ),
        reverse=False,
    )


def build_feedback_review_context(
    conn: sqlite3.Connection,
    user: dict[str, Any],
    *,
    status: str = "all",
    course_id: int | None = None,
    keyword: str = "",
    limit: int = 120,
) -> dict[str, Any]:
    student_id = int(user["id"])
    rows = _fetch_review_rows(conn, student_id=student_id, course_id=course_id, limit=limit)
    states = _load_review_states(
        conn,
        student_id=student_id,
        submission_ids=[int(row["submission_id"]) for row in rows],
    )
    all_items: list[dict[str, Any]] = []
    course_counts: dict[int, dict[str, Any]] = {}
    graded_scores: list[float] = []
    for row in rows:
        if _safe_float(row.get("score")) is not None:
            graded_scores.append(float(row["score"]))
        course_key = _safe_int(row.get("course_id"))
        if course_key:
            course_counts.setdefault(
                course_key,
                {
                    "course_id": course_key,
                    "label": str(row.get("course_name") or "未命名课程"),
                    "count": 0,
                },
            )
        extracted = extract_feedback_review_items(
            feedback_md=row.get("feedback_md"),
            answers_json=row.get("answers_json"),
            exam_questions_json=row.get("exam_questions_json"),
            submission_score=row.get("score"),
        )
        for item in extracted:
            if course_key:
                course_counts[course_key]["count"] += 1
            state = states.get((int(row["submission_id"]), str(item["question_key"])))
            all_items.append(_decorate_item(row, item, state))

    normalized_status = str(status or "all")
    if normalized_status not in REVIEW_STATUSES and normalized_status not in {"all", "active"}:
        normalized_status = "all"

    filtered_items = []
    for item in all_items:
        if normalized_status == "active" and item["status"] == REVIEW_STATUS_MASTERED:
            continue
        if normalized_status in REVIEW_STATUSES and item["status"] != normalized_status:
            continue
        if not _item_matches_keyword(item, keyword):
            continue
        filtered_items.append(item)

    sorted_items = _sort_review_items(filtered_items)
    open_count = sum(1 for item in all_items if item["status"] == REVIEW_STATUS_OPEN)
    reviewing_count = sum(1 for item in all_items if item["status"] == REVIEW_STATUS_REVIEWING)
    mastered_count = sum(1 for item in all_items if item["status"] == REVIEW_STATUS_MASTERED)
    high_count = sum(1 for item in all_items if item.get("severity") == "high" and item["status"] != REVIEW_STATUS_MASTERED)
    pinned_count = sum(1 for item in all_items if item.get("pinned"))
    total_count = len(all_items)
    active_count = open_count + reviewing_count
    progress_percent = 100 if total_count == 0 else int(round(mastered_count / total_count * 100))
    average_score = round(sum(graded_scores) / len(graded_scores), 1) if graded_scores else None

    return {
        "title": "错题本 / 反馈复盘",
        "subtitle": "把老师和 AI 的批改反馈拆成可执行的复盘卡，写下自己的修正动作，再标记掌握。",
        "items": sorted_items,
        "all_count": total_count,
        "visible_count": len(sorted_items),
        "filters": [
            {"value": "all", "label": "全部", "count": total_count},
            {"value": "active", "label": "待推进", "count": active_count},
            {"value": REVIEW_STATUS_OPEN, "label": "待复盘", "count": open_count},
            {"value": REVIEW_STATUS_REVIEWING, "label": "复盘中", "count": reviewing_count},
            {"value": REVIEW_STATUS_MASTERED, "label": "已掌握", "count": mastered_count},
        ],
        "course_options": sorted(course_counts.values(), key=lambda item: (-int(item["count"]), str(item["label"]))),
        "active_status": normalized_status,
        "active_course_id": course_id or 0,
        "keyword": str(keyword or ""),
        "stats": [
            {"label": "待复盘", "value": open_count, "hint": "还没开始处理", "tone": "danger" if open_count else "success"},
            {"label": "复盘中", "value": reviewing_count, "hint": "已有反思动作", "tone": "warning" if reviewing_count else "neutral"},
            {"label": "已掌握", "value": mastered_count, "hint": f"掌握率 {progress_percent}%", "tone": "success"},
            {"label": "高优先", "value": high_count, "hint": "低分或强扣分", "tone": "danger" if high_count else "neutral"},
            {"label": "平均分", "value": average_score if average_score is not None else "-", "hint": "来自已批改任务", "tone": "primary"},
            {"label": "置顶", "value": pinned_count, "hint": "近期重点盯住", "tone": "warning" if pinned_count else "neutral"},
        ],
        "progress": {
            "total": total_count,
            "active": active_count,
            "mastered": mastered_count,
            "percent": progress_percent,
        },
        "primary_item": sorted_items[0] if sorted_items else None,
    }


def build_feedback_review_summary(
    conn: sqlite3.Connection,
    student_id: int,
    *,
    limit: int = 80,
) -> dict[str, Any]:
    context = build_feedback_review_context(
        conn,
        {"id": int(student_id), "role": "student"},
        status="active",
        limit=limit,
    )
    primary = context.get("primary_item") or {}
    return {
        "open_count": int(context["progress"]["active"]),
        "total_count": int(context["progress"]["total"]),
        "mastered_count": int(context["progress"]["mastered"]),
        "progress_percent": int(context["progress"]["percent"]),
        "high_count": int(next((item["value"] for item in context["stats"] if item["label"] == "高优先"), 0)),
        "href": "/feedback-review",
        "title": primary.get("title") or "错题本 / 反馈复盘",
        "description": (
            f"还有 {context['progress']['active']} 个反馈点可复盘"
            if context["progress"]["active"]
            else "当前反馈点都已收束，适合偶尔回看巩固。"
        ),
    }


def update_feedback_review_item(
    conn: sqlite3.Connection,
    user: dict[str, Any],
    *,
    submission_id: int,
    question_key: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    student_id = int(user["id"])
    row = conn.execute(
        """
        SELECT s.id AS submission_id,
               s.student_pk_id,
               s.status,
               s.score,
               s.feedback_md,
               s.answers_json,
               a.exam_paper_id,
               ep.questions_json AS exam_questions_json
        FROM submissions s
        JOIN assignments a ON a.id = s.assignment_id
        LEFT JOIN exam_papers ep ON ep.id = a.exam_paper_id
        WHERE s.id = ?
          AND s.student_pk_id = ?
          AND COALESCE(s.is_absence_score, 0) = 0
        LIMIT 1
        """,
        (int(submission_id), student_id),
    ).fetchone()
    if not row:
        raise ValueError("复盘项不存在或不属于当前学生")
    row_dict = dict(row)
    available_items = extract_feedback_review_items(
        feedback_md=row_dict.get("feedback_md"),
        answers_json=row_dict.get("answers_json"),
        exam_questions_json=row_dict.get("exam_questions_json"),
        submission_score=row_dict.get("score"),
    )
    available_keys = {str(item["question_key"]) for item in available_items}
    normalized_key = str(question_key or "").strip()
    if normalized_key not in available_keys:
        raise ValueError("当前反馈项已变化，请刷新页面后再操作")

    status = str(payload.get("status") or REVIEW_STATUS_OPEN).strip()
    if status not in REVIEW_STATUSES:
        raise ValueError("复盘状态不正确")
    reflection = _compact_text(payload.get("reflection"), limit=1200)
    next_action = _compact_text(payload.get("next_action"), limit=500)
    pinned = 1 if payload.get("pinned") else 0
    now = _now_iso()
    reviewed_at = now if status in {REVIEW_STATUS_REVIEWING, REVIEW_STATUS_MASTERED} else None
    mastered_at = now if status == REVIEW_STATUS_MASTERED else None
    metadata = {
        "updated_from": "feedback_review",
        "source_status": str(row_dict.get("status") or ""),
    }
    conn.execute(
        """
        INSERT INTO student_feedback_review_notes (
            student_id, submission_id, question_key, status, reflection,
            next_action, pinned, reviewed_at, mastered_at, created_at,
            updated_at, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(student_id, submission_id, question_key) DO UPDATE SET
            status = excluded.status,
            reflection = excluded.reflection,
            next_action = excluded.next_action,
            pinned = excluded.pinned,
            reviewed_at = COALESCE(student_feedback_review_notes.reviewed_at, excluded.reviewed_at),
            mastered_at = CASE
                WHEN excluded.status = 'mastered' THEN COALESCE(student_feedback_review_notes.mastered_at, excluded.mastered_at)
                ELSE NULL
            END,
            updated_at = excluded.updated_at,
            metadata_json = excluded.metadata_json
        """,
        (
            student_id,
            int(submission_id),
            normalized_key,
            status,
            reflection,
            next_action,
            pinned,
            reviewed_at,
            mastered_at,
            now,
            now,
            json.dumps(metadata, ensure_ascii=False),
        ),
    )
    saved = conn.execute(
        """
        SELECT *
        FROM student_feedback_review_notes
        WHERE student_id = ? AND submission_id = ? AND question_key = ?
        LIMIT 1
        """,
        (student_id, int(submission_id), normalized_key),
    ).fetchone()
    return {
        "status": "success",
        "item": dict(saved) if saved else {},
        "status_label": REVIEW_STATUS_LABELS.get(status, "待复盘"),
    }
