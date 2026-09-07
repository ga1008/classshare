"""Review the exact local grade-material projection before creating or replacing it."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json

from fastapi import HTTPException


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _object(value) -> dict:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def build_grade_material_preflight(export_payload: dict, *, existing_record=None) -> dict:
    fields = _object(export_payload.get("fields"))
    structured = _object(export_payload.get("structured"))
    source = _object(structured.get("source_preflight"))
    if not source.get("version"):
        raise HTTPException(409, "本次材料没有完整来源预检，请重新选择课堂来源。")
    warnings = []
    source_warning_codes = [key for key, count in (source.get("counts") or {}).items() if count]
    for code, message in zip(source_warning_codes, source.get("warnings") or []):
        warnings.append({"code": code, "message": message})
    notes = [text for text in structured.get("warnings") or [] if text not in (source.get("warnings") or [])]
    if notes:
        warnings.append({"code": "material_calculation_notes", "message": "材料计算与缺分处理需要核对。", "details": notes})
    floor = _object(structured.get("score_floor_policy"))
    if floor.get("enabled"):
        warnings.append({"code": "score_floor_enabled", "message": f"已启用平时成绩最低分保护（{floor.get('minimum_score')} 分）；请核对调整结果。"})
    old = dict(existing_record) if existing_record is not None else None
    old_payload = _object(old.get("export_payload_json")) if old else {}
    old_structured = _object(old_payload.get("structured"))
    if old and (old_structured.get("manual_edit_log") or any(row.get("manual_edit") for row in old_structured.get("students") or [])):
        warnings.append({"code": "refresh_replaces_manual_scores", "message": "原材料含教师人工改分。本次更新将按课堂来源重新计算并覆盖这些材料内改分，请先核对下方前后分数。"})
    if old and structured.get("score_adjustment_policy") != old_structured.get("score_adjustment_policy"):
        warnings.append({"code": "refresh_score_scale_conversion", "message": "本次更新将按现行分制换算规则重新生成；例如百分制80分在50分卷登记为40分，请核对更新前后分数。"})
    if old:
        warnings.append({"code": "replace_saved_material", "message": "确认后将原地更新这份材料；已公布课程成绩保持原快照，需要另行确认公布新版本。"})
    warnings = list({item["code"]: item for item in warnings}.values())
    previous = {str(row.get("student_number") or ""): row for row in old_structured.get("students") or []}
    is_ordinary = export_payload.get("document_type") == "ordinary_grade_record"
    def total(row):
        return _object(row.get("calculated_scores")).get("ordinary_score") if is_ordinary else row.get("total_score")
    students = [{"student_id": row.get("student_id"), "student_number": row.get("student_number"),
                 "student_name": row.get("student_name"), "score": total(row),
                 "previous_score": total(previous.get(str(row.get("student_number") or ""), {})),
                 "source_score_missing": row.get("source_score_missing")}
                for row in structured.get("students") or []]
    # Cache freshness timestamps are not score facts. The exact row calculations,
    # roster, revision/review snapshots, source choices and policies are included.
    fingerprint = {"fields": fields, "structured": {key: value for key, value in structured.items()
                   if key not in {"attendance_sync", "generation_confirmation"}},
                   "previous_record": {"id": old.get("id"), "updated_at": old.get("updated_at"),
                                       "payload": old_payload} if old else None}
    selections = _object(structured.get("source_assignments"))
    source_items = [*(selections.get("homework_assignments") or []), _object(selections.get("assessment_assignment"))]
    labels = {int(item["id"]): item.get("title") for item in source_items if item.get("id")}
    exam = _object(structured.get("source_exam"))
    if exam.get("assignment_id"):
        labels[int(exam["assignment_id"])] = exam.get("assignment_title")
    return {"version": "grade-material-preflight-v1", "source_hash": _hash(fingerprint),
            "document_type": export_payload.get("document_type"), "class_offering_id": fields.get("class_offering_id"),
            "course_name": fields.get("course_name"), "class_name": fields.get("class_name"),
            "counts": source.get("counts") or {}, "issues": source.get("issues") or [],
            "source_snapshots": [{**item, "assignment_title": labels.get(int(item["assignment_id"]))}
                                 for item in source.get("source_snapshots") or []], "warnings": warnings,
            "students": students, "student_count": len(students), "is_refresh": bool(old),
            "full_score": 100 if is_ordinary else fields.get("total_score"),
            "missing_score_policy": "平时成绩表缺分沿用草稿规则暂按0计算，考核登分表缺分留空；均不把缺分改成任务有效零分。",
            "formula": "出勤×40% + 三份平时作业均分×30% + 期中测验×30%" if is_ordinary else "按试卷大题结构登记；任务百分制分数换算为本卷分数。"}


def confirm_grade_material_preflight(preflight: dict, confirmation: dict, *, teacher_id: int) -> dict:
    if not confirmation.get("preflight_confirmed") or not confirmation.get("expected_preflight_hash"):
        raise HTTPException(409, "请先预检并确认成绩来源、分数与警告，再生成或更新材料。")
    if confirmation.get("expected_preflight_hash") != preflight["source_hash"]:
        raise HTTPException(409, "成绩、名单、复核状态或原材料已变化，请重新预检并确认。")
    required = {item["code"] for item in preflight["warnings"]}
    if not required.issubset(set(confirmation.get("accepted_preflight_warning_codes") or [])):
        raise HTTPException(409, "请逐项确认本次来源警告后再继续。")
    return {"version": preflight["version"], "source_hash": preflight["source_hash"],
            "teacher_id": int(teacher_id), "confirmed_at": datetime.now().isoformat(),
            "accepted_warning_codes": sorted(required),
            "note": str(confirmation.get("preflight_confirmation_note") or "").strip()[:2000]}
