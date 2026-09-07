"""Authoritative classroom assessment classification, separate from answer format.

Read helpers never infer a formal category from a title, paper, or legacy grade
override. Mutation helpers participate in the caller's transaction and never
change submissions, grading attempts, or previously generated materials.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable, Mapping

from fastapi import HTTPException

ASSESSMENT_KIND_LABELS = {
    "homework": "平时作业",
    "midterm": "期中测验",
    "final": "期末测验",
}
ASSESSMENT_KINDS = frozenset(ASSESSMENT_KIND_LABELS)
MAX_CLASSIFICATION_BATCH = 100


def normalize_assessment_kind(value: Any, *, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or value not in ASSESSMENT_KINDS:
        raise ValueError("任务分类必须是平时作业、期中测验或期末测验")
    return value


def assessment_kind_info(
    row: Mapping[str, Any], *, source_feature: str | None = None,
) -> dict[str, Any]:
    item = dict(row)
    source = source_feature or item.get("source_feature")
    personal = source == "personal_stage" or bool(item.get("personal_stage_attempt_id"))
    kind = item.get("assessment_kind") if item.get("assessment_kind") in ASSESSMENT_KINDS else None
    if personal:
        kind = None
    has_paper = bool(item.get("exam_paper_id")) if "exam_paper_id" in item else bool(item.get("has_exam_paper"))
    return {
        "assessment_kind": kind,
        "assessment_kind_label": "个人阶段试炼" if personal else ASSESSMENT_KIND_LABELS.get(kind, "历史任务"),
        "classification_status": "not_applicable" if personal else ("confirmed" if kind else "legacy_unknown"),
        "assessment_kind_version": int(item.get("assessment_kind_version") or 0),
        "classification_source": "personal_stage" if personal else (item.get("assessment_kind_source") or item.get("classification_source") or "legacy_unknown"),
        "source_feature": "personal_stage" if personal else "classroom_assignment",
        "has_exam_paper": has_paper,
        "answer_mode": "exam_paper" if has_paper else "submission",
    }


def enrich_assessment_classifications(conn: Any, rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Batch source lookup; callers must first apply their ordinary access rules."""
    items = [dict(row) for row in rows]
    ids = list(dict.fromkeys(str(item.get("assignment_id") or item.get("id")) for item in items
                             if item.get("id") is not None or item.get("assignment_id") is not None))
    personal_ids: set[str] = set()
    # Keep within SQLite's bind limits and avoid one scope query per task.
    for start in range(0, len(ids), 400):
        chunk = ids[start:start + 400]
        placeholders = ",".join("?" for _ in chunk)
        personal_ids.update(str(row["assignment_id"]) for row in conn.execute(
            f"SELECT DISTINCT assignment_id FROM learning_stage_exam_attempts WHERE assignment_id IN ({placeholders})",
            tuple(chunk),
        ).fetchall())
    for item in items:
        assignment_id = str(item.get("assignment_id") or item.get("id"))
        item.update(assessment_kind_info(item, source_feature="personal_stage" if assignment_id in personal_ids else None))
    return items


def _expected_version(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise HTTPException(400, "请提供当前任务分类版本 expected_version")
    return value


def set_assignment_assessment_kind(
    conn: Any, assignment: Mapping[str, Any], *, assessment_kind: Any,
    expected_version: Any, teacher_id: int, source: str = "teacher_edit", reason: str = "",
) -> dict[str, Any]:
    """CAS update plus append-only audit; caller verifies ownership and commits."""
    try:
        kind = normalize_assessment_kind(assessment_kind)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    expected = _expected_version(expected_version)
    item = dict(assignment)
    if item.get("source_feature") == "personal_stage" or item.get("personal_stage_attempt_id"):
        raise HTTPException(400, "个人阶段试炼不适用正式课堂任务分类")
    if conn.execute("SELECT 1 FROM learning_stage_exam_attempts WHERE assignment_id = ? LIMIT 1", (item["id"],)).fetchone():
        raise HTTPException(400, "个人阶段试炼不适用正式课堂任务分类")
    current = conn.execute("SELECT * FROM assignments WHERE id = ?", (item["id"],)).fetchone()
    if not current:
        raise HTTPException(404, "课堂任务不存在")
    item = dict(current)
    version = int(item.get("assessment_kind_version") or 0)
    if version != expected:
        raise HTTPException(409, {"code": "classification_conflict", "message": "任务分类已被修改，请刷新后重试", **assessment_kind_info(item)})
    if item.get("assessment_kind") == kind:
        return {"assignment_id": item["id"], "changed": False, **assessment_kind_info(item)}
    timestamp = datetime.now().isoformat()
    reason = str(reason or "").strip()
    if len(reason) > 500:
        raise HTTPException(400, "分类修改说明不能超过500字")
    updated = conn.execute(
        """UPDATE assignments SET assessment_kind = ?, assessment_kind_version = ?,
               assessment_kind_source = ?, assessment_kind_updated_at = ?,
               assessment_kind_updated_by_teacher_id = ?
           WHERE id = ? AND COALESCE(assessment_kind_version, 0) = ?""",
        (kind, version + 1, source, timestamp, int(teacher_id), item["id"], expected),
    )
    if updated.rowcount != 1:
        raise HTTPException(409, "任务分类已被修改，请刷新后重试")
    conn.execute(
        """INSERT INTO assignment_classification_revisions
           (assignment_id, class_offering_id, previous_kind, assessment_kind,
            previous_version, version, source, changed_by_teacher_id, changed_at, reason)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (str(item["id"]), item.get("class_offering_id"), item.get("assessment_kind"), kind,
         version, version + 1, source, int(teacher_id), timestamp, reason),
    )
    item.update(assessment_kind=kind, assessment_kind_version=version + 1, assessment_kind_source=source)
    return {"assignment_id": item["id"], "changed": True, **assessment_kind_info(item)}


def initialize_assignment_assessment_kind(conn: Any, assignment_id: Any, *, assessment_kind: Any, teacher_id: int, source: str) -> dict[str, Any]:
    return set_assignment_assessment_kind(conn, {"id": assignment_id}, assessment_kind=assessment_kind,
                                          expected_version=0, teacher_id=teacher_id, source=source)


def assessment_classification_impact(conn: Any, assignment: Mapping[str, Any], *, teacher_id: int) -> dict[str, Any]:
    return assessment_classification_impacts(conn, [assignment], teacher_id=teacher_id)[str(assignment["id"])]


def assessment_classification_impacts(conn: Any, assignments: Iterable[Mapping[str, Any]], *, teacher_id: int) -> dict[str, dict[str, Any]]:
    """Bounded pre-edit review; material snapshots remain untouched.

    Material references are structured source IDs, never substring/title guesses.
    Report truncation explicitly instead of claiming a complete dependency scan.
    """
    items = [dict(item) for item in assignments]
    ids = list(dict.fromkeys(str(item["id"]) for item in items))
    if not ids:
        return {}
    if len(ids) > MAX_CLASSIFICATION_BATCH:
        raise ValueError("一次最多查看100个任务的分类影响")
    placeholders = ",".join("?" for _ in ids)
    counts = {str(row["assignment_id"]): dict(row) for row in conn.execute(
        f"""SELECT assignment_id, COUNT(*) AS submission_count,
                  SUM(CASE WHEN score IS NOT NULL THEN 1 ELSE 0 END) AS scored_count,
                  SUM(CASE WHEN status = 'grading' THEN 1 ELSE 0 END) AS grading_count
           FROM submissions WHERE assignment_id IN ({placeholders}) GROUP BY assignment_id""", tuple(ids),
    ).fetchall()}
    rows = conn.execute(
        """SELECT id, document_type, export_payload_json FROM material_ai_import_records
           WHERE teacher_id = ? AND document_group = 'final_material'
           ORDER BY updated_at DESC, id DESC LIMIT 201""", (int(teacher_id),),
    ).fetchall()
    direct_sources: dict[str, set[str]] = {}
    parents: dict[str, set[str]] = {}
    records: dict[str, dict[str, Any]] = {}
    for row in rows[:200]:
        try:
            payload = json.loads(row["export_payload_json"] or "{}")
        except (TypeError, ValueError):
            continue
        structured = payload.get("structured") if isinstance(payload, dict) else None
        if not isinstance(structured, dict):
            continue
        sources = structured.get("source_assignments") or {}
        exam = structured.get("source_exam") or {}
        source_ids = list(sources.get("homework_assignment_ids") or []) if isinstance(sources, dict) else []
        if isinstance(sources, dict):
            source_ids.append(sources.get("assessment_assignment_id"))
        if isinstance(exam, dict):
            source_ids.append(exam.get("assignment_id"))
        record_id = str(row["id"])
        direct_sources[record_id] = {str(value) for value in source_ids if value is not None}
        lineage = structured.get("source_lineage") or {}
        parents[record_id] = {str(value["record_id"]) for key, value in lineage.items()
                              if key in {"ordinary_grade_record", "exam_grade_record"}
                              and isinstance(value, dict) and value.get("record_id") is not None} if isinstance(lineage, dict) else set()
        records[record_id] = {"record_id": row["id"], "document_type": row["document_type"]}
    impacts = {}
    for assignment_id in ids:
        references = {key for key, source_ids in direct_sources.items() if assignment_id in source_ids}
        # Include derived transcript records without following cycles indefinitely.
        while True:
            expanded = references | {key for key, source_ids in parents.items() if source_ids & references}
            if expanded == references:
                break
            references = expanded
        count = counts.get(assignment_id, {})
        impacts[assignment_id] = {
            "submission_count": int(count.get("submission_count") or 0),
            "scored_count": int(count.get("scored_count") or 0),
            "grading_count": int(count.get("grading_count") or 0),
            "referenced_materials": [value for key, value in records.items() if key in references],
            "material_scan_complete": len(rows) <= 200,
            "preserves_existing_scores": True,
            "preserves_running_grading_snapshot": True,
            "message": "分类影响任务标签、统计分组和后续视觉档位；现有分数、批改快照和已生成材料保持不变。",
        }
    return impacts
