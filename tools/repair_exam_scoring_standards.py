from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from classroom_app.config import DB_PATH
from classroom_app.services.exam_json_service import (
    build_exam_rubric_md,
    looks_like_garbled_scoring_text,
    normalize_exam_scoring_payload,
)


DEFAULT_TOTAL_SCORE = 100
VALID_STYLES = {"strict", "medium", "loose", "rescue"}


def _has_text(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text) and not looks_like_garbled_scoring_text(text)


def _contains_garbled_text(value: Any) -> bool:
    if isinstance(value, str):
        return looks_like_garbled_scoring_text(value)
    if isinstance(value, list):
        return any(_contains_garbled_text(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_garbled_text(item) for item in value.values())
    return False


def _coerce_score(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    return score if score > 0 else None


def _compact_score(score: float) -> int | float:
    if abs(score - round(score)) < 0.0001:
        return int(round(score))
    return round(score, 2)


def _question_answer(question: dict[str, Any]) -> Any:
    for key in (
        "answer",
        "correct_answer",
        "correctAnswer",
        "reference_answer",
        "standard_answer",
        "expected_answer",
    ):
        value = question.get(key)
        if isinstance(value, list) and any(str(item or "").strip() for item in value):
            return value
        if isinstance(value, dict) and value:
            return value
        if str(value or "").strip():
            return value
    explanation = str(question.get("explanation") or question.get("analysis") or "").strip()
    if explanation:
        return explanation
    return "按题目要求、教师原始说明和学生提交内容综合判断。"


def _has_attachment_requirement(question: dict[str, Any]) -> bool:
    attachment = question.get("attachment_requirements")
    if isinstance(attachment, dict):
        return bool(
            attachment.get("enabled")
            or attachment.get("required")
            or int(attachment.get("min_count") or 0) > 0
            or int(attachment.get("max_count") or 0) > 0
        )
    return False


def _default_guidance(question: dict[str, Any]) -> str:
    question_type = str(question.get("type") or "").strip().lower()
    if question_type == "radio":
        return "选择标准答案得满分；判断题或单选题以选项结果为主，解析用于理解核对。"
    if question_type == "checkbox":
        return "正确选项全部选中且无错选得满分；漏选、错选按扣分点处理。"
    if question_type == "text":
        return "答案与参考答案含义一致得满分；大小写、同义表达或合理格式差异可酌情接受。"
    if _has_attachment_requirement(question):
        return "围绕标准答案、关键步骤和附件证据评分；截图、源码、日志或报告需能支撑题目要求。"
    return "围绕标准答案中的关键概念、步骤、结论和证据评分；表达等价且逻辑合理可得相应分。"


def _default_deductions(question: dict[str, Any]) -> str:
    question_type = str(question.get("type") or "").strip().lower()
    if question_type == "radio":
        return "选错不得分；只写解释但选项错误不加分。"
    if question_type == "checkbox":
        return "漏选按比例扣分；错选关键项最多得一半；全部选错或与题意无关不得分。"
    if question_type == "text":
        return "核心概念错误不得分；仅有格式瑕疵或同义表达差异可少量扣分。"
    if _has_attachment_requirement(question):
        return "缺少关键附件、证据无法对应题目要求、运行结果错误或源码结构不完整时按比例扣分。"
    return "缺少关键步骤、核心结论错误、证据不足或答非所问时按比例扣分。"


def _iter_questions(data: dict[str, Any]) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    for page in data.get("pages", []) or []:
        if not isinstance(page, dict):
            continue
        for question in page.get("questions", []) or []:
            if isinstance(question, dict):
                questions.append(question)
    return questions


def _distribute_missing_points(questions: list[dict[str, Any]], desired_total: float) -> None:
    missing_questions = []
    existing_total = 0.0
    for question in questions:
        points = _coerce_score(question.get("points"))
        if points is None and isinstance(question.get("grading"), dict):
            points = _coerce_score(question["grading"].get("points"))
        if points is None:
            missing_questions.append(question)
        else:
            question["points"] = _compact_score(points)
            existing_total += points

    if not missing_questions:
        return

    remaining = desired_total - existing_total
    if remaining <= 0:
        remaining = desired_total
        for question in questions:
            question.pop("points", None)
        missing_questions = questions

    base = round(remaining / len(missing_questions), 2)
    allocated = 0.0
    for index, question in enumerate(missing_questions):
        if index == len(missing_questions) - 1:
            points = remaining - allocated
        else:
            points = base
            allocated += points
        question["points"] = _compact_score(max(points, 0.01))


def _repair_exam_data(data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    changed = False
    questions = _iter_questions(data)
    if not questions:
        return data, False

    grading = data.get("grading") if isinstance(data.get("grading"), dict) else {}
    total_score = _coerce_score(grading.get("total_score")) or DEFAULT_TOTAL_SCORE
    description = grading.get("description") if _has_text(grading.get("description")) else ""
    style = str(grading.get("style") or "").strip().lower()

    if not isinstance(data.get("grading"), dict) or not description or style not in VALID_STYLES:
        changed = True
    data["grading"] = {
        "total_score": _compact_score(total_score),
        "description": description
        or "按标准答案、关键步骤、运行结果和提交证据综合评分；每题按分值独立评分，重点看得分点和失分点。",
        "style": style if style in VALID_STYLES else "medium",
    }

    _distribute_missing_points(questions, total_score)

    for question in questions:
        original = json.dumps(question, ensure_ascii=False, sort_keys=True)
        question["answer"] = _question_answer(question)
        if not _has_text(question.get("grading_guidance")):
            question["grading_guidance"] = _default_guidance(question)
        if not _has_text(question.get("deduction_points")):
            question["deduction_points"] = _default_deductions(question)

        grading_payload = question.get("grading") if isinstance(question.get("grading"), dict) else {}
        grading_payload.update(
            {
                "points": question.get("points"),
                "guidance": question.get("grading_guidance"),
                "deduction_points": question.get("deduction_points"),
            }
        )
        question["grading"] = {
            key: value
            for key, value in grading_payload.items()
            if value not in (None, "")
        }
        if json.dumps(question, ensure_ascii=False, sort_keys=True) != original:
            changed = True

    normalized = normalize_exam_scoring_payload(data, require_complete=True)
    return normalized, changed


def repair(db_path: Path, *, apply: bool) -> dict[str, Any]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    report: dict[str, Any] = {
        "db_path": str(db_path),
        "apply": apply,
        "scanned": 0,
        "repaired": 0,
        "garbled": 0,
        "errors": [],
        "items": [],
    }
    try:
        rows = conn.execute(
            """
            SELECT ep.id, ep.title, ep.description, ep.questions_json,
                   COUNT(a.id) AS assignment_count
            FROM exam_papers ep
            LEFT JOIN assignments a ON a.exam_paper_id = ep.id
            GROUP BY ep.id
            ORDER BY ep.updated_at DESC, ep.created_at DESC, ep.id
            """
        ).fetchall()
        now = datetime.now().isoformat()
        for row in rows:
            report["scanned"] += 1
            try:
                data = json.loads(row["questions_json"] or "{}")
                if not isinstance(data, dict):
                    raise ValueError("questions_json root is not an object")
                before = json.dumps(data, ensure_ascii=False, sort_keys=True)
                was_garbled = _contains_garbled_text(data)
                repaired, changed = _repair_exam_data(data)
                after = json.dumps(repaired, ensure_ascii=False, sort_keys=True)
                if was_garbled:
                    report["garbled"] += 1
                if changed or after != before:
                    rubric_md = build_exam_rubric_md(
                        title=row["title"] or "",
                        description=row["description"] or "",
                        exam_data=repaired,
                        require_complete=True,
                    )
                    report["repaired"] += 1
                    report["items"].append(
                        {
                            "id": row["id"],
                            "title": row["title"],
                            "garbled": was_garbled,
                            "assignment_count": row["assignment_count"],
                            "rubric_chars": len(rubric_md),
                        }
                    )
                    if apply:
                        conn.execute(
                            "UPDATE exam_papers SET questions_json = ?, updated_at = ? WHERE id = ?",
                            (json.dumps(repaired, ensure_ascii=False), now, row["id"]),
                        )
                        conn.execute(
                            "UPDATE assignments SET rubric_md = ? WHERE exam_paper_id = ?",
                            (rubric_md, row["id"]),
                        )
            except Exception as exc:  # noqa: BLE001 - maintenance script should keep scanning.
                report["errors"].append({"id": row["id"], "title": row["title"], "error": str(exc)})

        if apply:
            conn.commit()
        else:
            conn.rollback()
    finally:
        conn.close()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair historical exam scoring standards.")
    parser.add_argument("--db", default=str(DB_PATH), help="SQLite database path")
    parser.add_argument("--apply", action="store_true", help="Write changes instead of dry-run")
    args = parser.parse_args()
    report = repair(Path(args.db), apply=bool(args.apply))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
