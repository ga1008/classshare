"""Per-question grading data: one shape for AI results, teacher edits and appeals.

A grade used to be stored only as Markdown (``feedback_md``) and parsed back
with regular expressions wherever per-question scores were needed. Grade
revisions now also keep the structured list in
``quality_audit_json["questions"]``; this module normalizes it, reads the
current list for a submission (falling back to parsing older Markdown), and
turns a teacher's per-question edits into a server-computed total plus the
canonical Markdown.

Question item shape::

    {"question_no": 1, "question_id": "q1", "score": 8.0, "max_score": 10.0,
     "deduction_points": "…", "evaluation": "…"}
"""
from __future__ import annotations

import json
import math
import re
from typing import Any, Iterable, Mapping

from fastapi import HTTPException

from .grading_feedback_service import (
    QUESTION_DEDUCTION_MAX_CHARS,
    QUESTION_EVALUATION_MAX_CHARS,
    SUMMARY_MAX_CHARS,
    build_structured_feedback_markdown,
)

MAX_QUESTIONS = 200
SCORE_SCALE_MAX = 100.0

# Same grammar as static/js/grading_feedback.js: "### 第 N 题" sections with
# 本题得分 / 扣分点 / 评价 lines.
_Q_HEADING_RE = re.compile(
    r"^#{1,6}\s*(?:第\s*)?(\d+)\s*(?:题|问|小题)?(?:\s*[：:.\-、]\s*(.*))?$", re.I
)
_Q_ALT_HEADING_RE = re.compile(
    r"^#{1,6}\s*(?:q|question)\s*\.?\s*(\d+)(?:\s*[：:.\-、]\s*(.*))?$", re.I
)
_FB_LABEL_RE = re.compile(
    r"^(本题得分|得分|score|扣分点描述|扣分点|失分点|评价|评语|evaluation)\s*[：:]\s*(.*)$",
    re.I,
)
_FB_BULLET_PREFIX_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)、])\s*")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_OVERVIEW_HEADING_RE = re.compile(r"^#{1,6}\s*(总览评语|总评|总体评价)\s*$")


def parse_question_feedback(feedback_md: str | None) -> dict[int, dict]:
    """feedback_md → {题号: {score, max_score, deduction, evaluation}}.

    Only well-formed sections are recognised; anything else returns empty so
    callers fall back to other evidence instead of losing content.
    """
    sections: dict[int, dict] = {}
    current_no: int | None = None
    for raw_line in str(feedback_md or "").splitlines():
        stripped = raw_line.strip()
        heading = _Q_HEADING_RE.match(stripped) or _Q_ALT_HEADING_RE.match(stripped)
        if heading:
            current_no = int(heading.group(1))
            sections.setdefault(current_no, {"score": None, "max_score": None, "deduction": "", "evaluation": ""})
            continue
        if current_no is None:
            continue
        normalized = _FB_BULLET_PREFIX_RE.sub("", stripped).replace("**", "").strip()
        labeled = _FB_LABEL_RE.match(normalized)
        if not labeled:
            continue
        label = labeled.group(1).lower()
        value = labeled.group(2).strip()
        section = sections[current_no]
        if label in ("本题得分", "得分", "score"):
            numbers = _NUMBER_RE.findall(value)
            section["score"] = float(numbers[0]) if numbers else None
            section["max_score"] = float(numbers[1]) if len(numbers) > 1 else None
        elif label in ("评价", "评语", "evaluation"):
            section["evaluation"] = value
        else:
            section["deduction"] = value
    return {
        no: section
        for no, section in sections.items()
        if section["score"] is not None or section["deduction"] or section["evaluation"]
    }


def overview_from_feedback(feedback_md: str | None) -> str:
    """The 总览评语 paragraph of a structured feedback, if any."""
    collected: list[str] = []
    inside = False
    for line in str(feedback_md or "").splitlines():
        stripped = line.strip()
        if _OVERVIEW_HEADING_RE.match(stripped):
            inside = True
            continue
        if inside and stripped.startswith("#"):
            break
        if inside and stripped:
            collected.append(stripped)
    return " ".join(collected)[:SUMMARY_MAX_CHARS]


def _number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def normalize_question_items(items: Iterable[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """Clean an untrusted question list (AI output, teacher form, stored JSON)."""
    normalized: list[dict[str, Any]] = []
    seen: set[int] = set()
    for index, raw in enumerate(list(items or [])[:MAX_QUESTIONS], start=1):
        if not isinstance(raw, Mapping):
            continue
        try:
            number = int(raw.get("question_no") or index)
        except (TypeError, ValueError):
            number = index
        if number <= 0 or number in seen:
            number = max(seen | {0}) + 1
        seen.add(number)
        max_score = _number(raw.get("max_score"))
        if max_score is not None and max_score < 0:
            max_score = None
        normalized.append({
            "question_no": number,
            "question_id": _text(raw.get("question_id") or f"q{number}", 80),
            "score": _number(raw.get("score")),
            "max_score": max_score,
            "deduction_points": _text(raw.get("deduction_points") or raw.get("deduction") or "", QUESTION_DEDUCTION_MAX_CHARS),
            "evaluation": _text(raw.get("evaluation") or "", QUESTION_EVALUATION_MAX_CHARS),
        })
    return sorted(normalized, key=lambda item: item["question_no"])


def questions_from_feedback_md(feedback_md: str | None) -> list[dict[str, Any]]:
    parsed = parse_question_feedback(feedback_md)
    return normalize_question_items(
        {"question_no": number, "score": section["score"], "max_score": section["max_score"],
         "deduction_points": section["deduction"], "evaluation": section["evaluation"]}
        for number, section in sorted(parsed.items())
    )


def current_grade_questions(conn, submission: Mapping[str, Any]) -> dict[str, Any]:
    """Structured questions of the submission's visible grade.

    Prefers the active revision's stored list; older grades are parsed from
    their Markdown. ``source`` tells the editor whether totals can be trusted.
    """
    revision_id = submission.get("active_grade_revision_id")
    if revision_id:
        row = conn.execute(
            "SELECT quality_audit_json FROM submission_grade_revisions WHERE id = ? AND submission_id = ? LIMIT 1",
            (int(revision_id), int(submission["id"])),
        ).fetchone()
        if row:
            try:
                audit = json.loads(row["quality_audit_json"] or "{}") or {}
            except (TypeError, ValueError):
                audit = {}
            stored = normalize_question_items(audit.get("questions"))
            if stored:
                return {"source": "revision", "questions": stored,
                        "summary": overview_from_feedback(submission.get("feedback_md"))}
    parsed = questions_from_feedback_md(submission.get("feedback_md"))
    return {"source": "feedback" if parsed else "none", "questions": parsed,
            "summary": overview_from_feedback(submission.get("feedback_md"))}


def compose_grade_from_questions(questions: Iterable[Mapping[str, Any]] | None, *, summary: Any = "",
                                 override_score: Any = None, override_reason: Any = "") -> dict[str, Any]:
    """Teacher per-question edit → validated items, server total, canonical Markdown.

    Each score must lie within [0, its max]. The total is their sum (clamped to
    the 0–100 grade scale) unless the teacher overrides it with a reason.
    """
    items = normalize_question_items(questions)
    if not items:
        raise HTTPException(400, "请至少填写一道题的得分")
    for item in items:
        if item["score"] is None:
            raise HTTPException(400, f"第 {item['question_no']} 题缺少得分")
        if item["score"] < 0:
            raise HTTPException(400, f"第 {item['question_no']} 题得分不能为负数")
        if item["max_score"] is not None and item["score"] > item["max_score"] + 1e-9:
            raise HTTPException(400, f"第 {item['question_no']} 题得分不能超过满分 {item['max_score']:g}")
        if not item["deduction_points"]:
            full = item["max_score"] is None or item["score"] >= item["max_score"]
            item["deduction_points"] = "无" if full else "见教师评语"
    computed = round(min(SCORE_SCALE_MAX, sum(item["score"] for item in items)), 2)
    total = computed
    reason = _text(override_reason, 200)
    override = _number(override_score)
    if override is not None and abs(override - computed) > 1e-9:
        if not reason:
            raise HTTPException(400, "手动调整总分时请填写调整原因")
        if not 0 <= override <= SCORE_SCALE_MAX:
            raise HTTPException(400, "总分必须在 0-100 之间")
        total = round(override, 2)
    summary_text = _text(summary, SUMMARY_MAX_CHARS) or "已按逐题评分复核。"
    feedback_md = build_structured_feedback_markdown({"summary": summary_text, "questions": items})
    if total != computed:
        feedback_md += f"\n\n> 教师调整总分：逐题合计 {computed:g} 分，调整为 {total:g} 分。原因：{reason}"
    return {"score": total, "computed_score": computed, "questions": items, "feedback_md": feedback_md,
            "summary": summary_text, "override_reason": reason if total != computed else ""}
