"""Explicit teacher publication of immutable, self-only official grade snapshots.

Material generation and AI callbacks never call this service. Reads never create
schema or mutate publication state; source drift is reported without rewriting
the already published per-student values.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from fastapi import HTTPException
from ..db.connection import execute_insert_returning_id, get_configured_db_engine
from .grade_source_preflight_service import build_grade_source_preflight
from .offering_membership_service import offering_student_where
from .semester_identity_service import parse_semester_identity

PUBLICATION_TYPES = {"final_grade_transcript", "academic_grade_register"}


def _object(value: Any) -> dict:
    if isinstance(value, dict):
        return dict(value)
    try:
        data = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(payload: dict) -> str:
    return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()


def _score(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and 0 <= number <= 100 else None


def _offering(conn, offering_id: int, teacher_id: int) -> dict:
    row = conn.execute("""SELECT o.*, c.name AS course_name, sem.name AS semester_name
        FROM class_offerings o JOIN courses c ON c.id = o.course_id
        LEFT JOIN academic_semesters sem ON sem.id = o.semester_id
        WHERE o.id = ? AND o.teacher_id = ?""", (int(offering_id), int(teacher_id))).fetchone()
    if not row:
        raise HTTPException(403, "无权公布该课堂成绩。")
    return dict(row)


def _material(conn, material_id: int, offering_id: int, teacher_id: int) -> dict:
    row = conn.execute("""SELECT r.* FROM material_ai_import_records r
        WHERE r.id = ? AND r.teacher_id = ? AND r.parse_status = 'completed'
          AND r.document_group = 'final_material'
          AND EXISTS (SELECT 1 FROM course_material_assignments link
              WHERE link.class_offering_id = ? AND
                (link.material_id = r.package_material_id OR link.material_id = r.parsed_material_id))
    """, (int(material_id), int(teacher_id), int(offering_id))).fetchone()
    if not row:
        raise HTTPException(404, "来源材料不属于本课堂、尚未完成或您无权使用。")
    return dict(row)


def _roster(conn, offering_id: int) -> list[dict]:
    return [dict(row) for row in conn.execute(f"""SELECT s.id, s.student_id_number, s.name FROM students s
        JOIN class_offerings o ON {offering_student_where()}
        WHERE o.id = ? AND COALESCE(s.enrollment_status, 'active') = 'active'
        ORDER BY s.student_id_number, s.id""", (int(offering_id),)).fetchall()]


def _source_review(conn, record: dict, payload: dict, offering_id: int, teacher_id: int) -> tuple[list, list]:
    structured = _object(payload.get("structured"))
    warnings, blockers = [], []
    if record["document_type"] == "academic_grade_register":
        validation = _object(structured.get("validation"))
        if validation.get("passed") is False:
            blockers.append({"code": "source_validation_failed", "message": "教务成绩材料尚未通过一致性核验。"})
        if not validation:
            warnings.append({"code": "legacy_validation", "message": "历史教务材料缺少核验记录，请核对分数与课程学期。"})
        return warnings, blockers
    lineage = _object(structured.get("source_lineage"))
    if not lineage:
        warnings.append({"code": "legacy_source_snapshot", "message": "该成绩单缺少课堂评分来源快照，请核对来源、缺分与人工调整。"})
    for key in ("ordinary_grade_record", "exam_grade_record"):
        source = _object(lineage.get(key))
        if not source.get("record_id"):
            continue
        dependency = _material(conn, int(source["record_id"]), offering_id, teacher_id)
        if str(source.get("updated_at") or "") != str(dependency.get("updated_at") or ""):
            blockers.append({"code": "source_material_changed", "message": "来源成绩材料已更新，请显式更新期末成绩单后再公布。"})
        source_payload = _object(dependency.get("export_payload_json"))
        source_structured = _object(source_payload.get("structured"))
        old = _object(source_structured.get("source_preflight"))
        snapshots = old.get("source_snapshots") or []
        if not snapshots:
            warnings.append({"code": "legacy_source_snapshot", "message": "来源材料缺少评分版本快照，请核对旧成绩与正式分类。"})
            legacy_sources = _object(source_structured.get("source_assignments"))
            legacy_exam = _object(source_structured.get("source_exam"))
            legacy_ids = [*(legacy_sources.get("homework_assignment_ids") or []),
                          legacy_sources.get("assessment_assignment_id"), legacy_exam.get("assignment_id")]
            assignment_ids = sorted({int(value) for value in legacy_ids if value})
            student_ids = [int(row["id"]) for row in _roster(conn, offering_id)]
            if not assignment_ids:
                continue
        else:
            assignment_ids = sorted({int(item["assignment_id"]) for item in snapshots})
            student_ids = sorted({int(item["student_id"]) for item in snapshots})
        current = build_grade_source_preflight(conn, assignment_ids=assignment_ids, student_ids=student_ids)
        if snapshots and sorted(snapshots, key=lambda item: (item["assignment_id"], item["student_id"])) != sorted(current["source_snapshots"], key=lambda item: (item["assignment_id"], item["student_id"])):
            blockers.append({"code": "source_scores_changed", "message": "评分版本、有效分或任务分类已变化，请显式更新来源材料及期末成绩单。"})
        warning_messages = dict(zip((code for code, count in current["counts"].items() if count), current["warnings"]))
        for code, count in current["counts"].items():
            if not count:
                continue
            item = {"code": code, "message": warning_messages.get(code, "来源成绩需复核，请查看材料完整性明细。")}
            (blockers if code == "group_unreleased" else warnings).append(item)
        if source_structured.get("manual_edit_log") or _object(source_structured.get("score_floor_policy")).get("adjusted_count") or _object(source_structured.get("retake_policy")).get("count"):
            warnings.append({"code": "material_adjustments", "message": "材料包含人工调整、最低分保护或重修规则，请核对调整后的公布分。"})
    return list({item["code"]: item for item in warnings}.values()), list({item["code"]: item for item in blockers}.values())


def preview_grade_publication(conn, *, class_offering_id: int, teacher_id: int, material_id: int) -> dict:
    offering = _offering(conn, class_offering_id, teacher_id)
    record = _material(conn, material_id, class_offering_id, teacher_id)
    if record["document_type"] not in PUBLICATION_TYPES:
        raise HTTPException(400, "只能从期末成绩单或教务期末成绩登记表公布课程成绩。")
    payload = _object(record.get("export_payload_json"))
    fields, structured = _object(payload.get("fields")), _object(payload.get("structured"))
    if fields.get("class_offering_id") and int(fields["class_offering_id"]) != int(class_offering_id):
        raise HTTPException(409, "材料课堂信息与当前课堂不一致。")
    if fields.get("semester_id") and offering.get("semester_id") and int(fields["semester_id"]) != int(offering["semester_id"]):
        raise HTTPException(409, "材料学期与当前开课学期不一致。")
    roster = _roster(conn, class_offering_id)
    if not roster:
        raise HTTPException(409, "课堂没有可公布成绩的学生。")
    warnings, blockers = _source_review(conn, record, payload, class_offering_id, teacher_id)
    source_semester = parse_semester_identity(f"{fields.get('academic_year') or ''} {fields.get('semester') or ''}")
    offering_semester = parse_semester_identity(offering.get("semester_name") or offering.get("semester") or "")
    if source_semester and offering_semester and source_semester != offering_semester:
        blockers.append({"code": "semester_mismatch", "message": "材料学年学期与本课堂不一致。"})
    elif not source_semester or not offering_semester:
        warnings.append({"code": "semester_confirmation", "message": "历史学期信息不完整，请确认材料属于本课堂当前开课学期。"})
    by_number, duplicates = {}, set()
    for student in structured.get("students") or []:
        number = str(student.get("student_number") or "").strip()
        if not number:
            continue
        if number in by_number:
            duplicates.add(number)
        by_number[number] = student
    roster_numbers = {str(s["student_id_number"] or "").strip() for s in roster}
    if duplicates or set(by_number) != roster_numbers or len(roster_numbers) != len(roster):
        blockers.append({"code": "roster_mismatch", "message": "材料名单与本课堂在读名单不完整对应，或含重复学号；请核对后更新材料。"})
    academic = record["document_type"] == "academic_grade_register"
    formula = {"kind": "source_report" if academic else "weighted", "text": structured.get("formula") if academic else "平时成绩 × 40% + 期末成绩 × 60%",
               "ordinary_weight": None if academic else 0.4, "final_weight": None if academic else 0.6,
               "source_final_score_meaning": "overall_score" if academic else "final_exam_score"}
    students = []
    for member in roster:
        number = str(member["student_id_number"] or "").strip()
        source = by_number.get(number, {})
        ordinary = _score(source.get("ordinary_score"))
        final = _score(source.get("final_exam_score" if academic else "final_score"))
        overall = _score(source.get("final_score")) if academic else (
            float((Decimal(str(ordinary)) * Decimal("0.4") + Decimal(str(final)) * Decimal("0.6")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            if ordinary is not None and final is not None else None)
        if overall is None or ordinary is None or final is None:
            blockers.append({"code": "missing_published_score", "message": "部分学生缺少有效平时、期末或总评分数；不自动将空值改成0。"})
        students.append({"student_pk_id": member["id"], "student_number": number, "student_name": member["name"],
            "ordinary_score": ordinary, "final_exam_score": final, "overall_score": overall,
            "midterm_score": _score(source.get("midterm_score")) if academic else None, "scale": 100})
    latest = conn.execute("SELECT COALESCE(MAX(version),0) AS version FROM grade_publications WHERE class_offering_id = ?", (int(class_offering_id),)).fetchone()
    blockers = list({item["code"]: item for item in blockers}.values())
    return {"material_id": int(material_id), "source_document_type": record["document_type"],
        "source_hash": _hash(payload), "source_updated_at": str(record.get("updated_at") or ""),
        "expected_version": int(latest["version"]), "class_offering_id": int(class_offering_id),
        "semester_id": offering.get("semester_id"), "semester_name": offering.get("semester_name") or offering.get("semester") or "未关联学期",
        "course_name": offering["course_name"], "students": students, "formula": formula,
        "warnings": warnings, "blocking_reasons": blockers, "can_publish": not blockers,
        "source_lineage": _object(structured.get("source_lineage"))}


def publish_grade_snapshot(conn, *, class_offering_id: int, teacher_id: int, material_id: int,
                           expected_source_hash: str, expected_version: int, confirmed: bool,
                           accepted_warning_codes: list[str] | None = None, confirmation_note: str = "") -> dict:
    if confirmed is not True:
        raise HTTPException(400, "须由教师明确确认公布本次成绩。")
    _offering(conn, class_offering_id, teacher_id)
    # Serialize new versions on the existing authorized offering, before reading
    # the latest version. SQLite serializes writers; PostgreSQL locks this row.
    conn.execute("UPDATE class_offerings SET id = id WHERE id = ? AND teacher_id = ?", (int(class_offering_id), int(teacher_id)))
    preview = preview_grade_publication(conn, class_offering_id=class_offering_id, teacher_id=teacher_id, material_id=material_id)
    if preview["source_hash"] != expected_source_hash or preview["expected_version"] != int(expected_version):
        raise HTTPException(409, "来源材料或已公布版本发生变化，请重新预览并确认。")
    if preview["blocking_reasons"]:
        raise HTTPException(409, {"message": "公布前仍有必须处理的问题。", "blocking_reasons": preview["blocking_reasons"]})
    required = {item["code"] for item in preview["warnings"]}
    if not required.issubset(set(accepted_warning_codes or [])) or (required and not str(confirmation_note).strip()):
        raise HTTPException(400, "请逐项确认来源警告并填写核对说明后公布。")
    now = datetime.now().isoformat(timespec="microseconds")
    conn.execute("UPDATE grade_publications SET status = 'superseded' WHERE class_offering_id = ? AND status = 'active'", (int(class_offering_id),))
    publication_id = execute_insert_returning_id(conn, """INSERT INTO grade_publications
        (class_offering_id, semester_id, teacher_id, version, source_record_id, source_document_type,
         source_updated_at, source_hash, source_snapshot_json, formula_json, confirmation_json, published_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (int(class_offering_id), preview["semester_id"], int(teacher_id), int(expected_version)+1, int(material_id),
         preview["source_document_type"], preview["source_updated_at"], preview["source_hash"],
         _json({key: preview[key] for key in ("course_name", "semester_name", "source_lineage")}),
         _json(preview["formula"]), _json({"accepted_warning_codes": sorted(required), "note": str(confirmation_note).strip()[:2000]}), now),
        engine=get_configured_db_engine())
    for student in preview["students"]:
        conn.execute("INSERT INTO grade_publication_students (publication_id, student_pk_id, student_number, scores_json) VALUES (?, ?, ?, ?)",
            (int(publication_id), int(student["student_pk_id"]), student["student_number"], _json({key: student[key] for key in ("ordinary_score", "final_exam_score", "overall_score", "midterm_score", "scale")})))
    return {"publication_id": int(publication_id), "version": int(expected_version)+1, "published_at": now, "student_count": len(preview["students"])}


def withdraw_grade_publication(conn, *, class_offering_id: int, teacher_id: int, publication_id: int, reason: str) -> dict:
    _offering(conn, class_offering_id, teacher_id)
    if not str(reason or "").strip():
        raise HTTPException(400, "请填写撤回原因。")
    # Match publishing and classroom merge before changing the snapshot state.
    # A merge cannot archive an active row while a concurrent withdrawal commits.
    conn.execute("UPDATE class_offerings SET id = id WHERE id = ? AND teacher_id = ?",
                 (int(class_offering_id), int(teacher_id)))
    updated = conn.execute("""UPDATE grade_publications SET status = 'withdrawn', withdrawn_at = ?,
        withdrawn_by_teacher_id = ?, withdrawal_reason = ?
        WHERE id = ? AND class_offering_id = ? AND teacher_id = ? AND status = 'active'""",
        (datetime.now().isoformat(timespec="microseconds"), int(teacher_id), str(reason).strip()[:2000], int(publication_id), int(class_offering_id), int(teacher_id)))
    if updated.rowcount != 1:
        raise HTTPException(409, "已公布版本已变化或已撤回，请刷新后重试。")
    return {"publication_id": int(publication_id), "status": "withdrawn"}


def teacher_grade_publication_status(conn, *, class_offering_id: int, teacher_id: int) -> dict:
    _offering(conn, class_offering_id, teacher_id)
    rows = conn.execute("""SELECT p.*, r.export_payload_json AS current_payload_json FROM grade_publications p
        LEFT JOIN material_ai_import_records r ON r.id = p.source_record_id
        WHERE p.class_offering_id = ? AND p.teacher_id = ?
          AND (p.status = 'active' OR p.id IN (
            SELECT recent.id FROM grade_publications recent
            WHERE recent.class_offering_id = ? AND recent.teacher_id = ?
            ORDER BY recent.version DESC LIMIT 50
          ))
        ORDER BY p.version DESC""", (int(class_offering_id), int(teacher_id),
                                      int(class_offering_id), int(teacher_id))).fetchall()
    history = []
    for row in rows:
        item = dict(row)
        stale = not item.get("current_payload_json") or _hash(_object(item["current_payload_json"])) != item["source_hash"]
        if not stale and item["status"] == "active":
            try:
                review = preview_grade_publication(conn, class_offering_id=class_offering_id, teacher_id=teacher_id, material_id=item["source_record_id"])
                stale = bool(review["blocking_reasons"])
            except HTTPException:
                stale = True
        history.append({"publication_id": item["id"], "version": item["version"], "status": item["status"],
            "source_record_id": item["source_record_id"], "published_at": item["published_at"], "withdrawn_at": item["withdrawn_at"],
            "withdrawal_reason": item["withdrawal_reason"], "source_stale": stale})
    return {"current": next((item for item in history if item["status"] == "active"), None), "history": history}


def student_published_grades(conn, *, student_id: int) -> list[dict]:
    rows = conn.execute("""SELECT p.id, p.class_offering_id, p.semester_id, p.version, p.published_at,
        p.source_snapshot_json, p.formula_json, s.scores_json
        FROM grade_publication_students s JOIN grade_publications p ON p.id = s.publication_id
        WHERE s.student_pk_id = ? AND p.status = 'active' ORDER BY p.published_at DESC""", (int(student_id),)).fetchall()
    result = []
    for row in rows:
        snapshot = _object(row["source_snapshot_json"])
        result.append({"publication_id": row["id"], "class_offering_id": row["class_offering_id"], "semester_id": row["semester_id"],
            "version": row["version"], "published_at": row["published_at"], "course_name": snapshot.get("course_name"),
            "semester_name": snapshot.get("semester_name"), "formula": _object(row["formula_json"]),
            **_object(row["scores_json"])})
    return result
