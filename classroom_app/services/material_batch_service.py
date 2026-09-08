"""Owned-document selection, idempotent applications and export preflight."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta

from . import signature_service
from .ai_durable_job_service import create_ai_job
from .material_signature_service import DOCUMENT_POINTS, DOCUMENT_LABELS, dumps, load_material, material_reference


def references(values, maximum=200):
    if not isinstance(values, list) or not 1 <= len(values) <= maximum:
        raise signature_service.SignatureServiceError(400, f"请选择 1 至 {maximum} 份文档。")
    output, seen = [], set()
    for value in values:
        if not isinstance(value, dict):
            raise signature_service.SignatureServiceError(400, "材料选择格式无效。")
        ref = material_reference(value)
        key = (ref["material_type"], ref["material_id"])
        if key not in seen:
            output.append(ref)
            seen.add(key)
    return output


def document_properties(conn, material):
    if material["document_type"] == "assessment_plan":
        from .assessment_plan_service import get_assessment_plan, build_export_fields, _assert_export_score_balanced

        plan = get_assessment_plan(conn, material["material_id"])
        fields = build_export_fields(conn, plan)
        # Export validation is the authority for the score balance.
        try:
            _assert_export_score_balanced(plan)
            balanced = True
        except (ValueError, TypeError):
            balanced = False
        complete = balanced and bool(fields.get("examiner_personal_signature_count") and fields.get("reviewer_personal_signature_count"))
    else:
        from .academic_final_material_service import academic_exam_analysis_is_complete, hydrate_academic_final_material_signature_paths

        payload = hydrate_academic_final_material_signature_paths(conn, material["payload"], record=material["row"], include_images=False)
        fields = payload.get("fields") or {}
        complete = bool(fields.get("teacher_personal_signature_ids")) if material["document_type"] == "academic_grade_register" else academic_exam_analysis_is_complete(fields, payload.get("structured") or {})
    return {"material_type": material["material_type"], "material_id": material["material_id"],
        "document_type": material["document_type"], "title": material["title"], "complete": complete,
        "material_revision": material["material_revision"], "content_fingerprint": material["content_fingerprint"],
        "updated_at": str(material["row"].get("updated_at") or ""), "can_edit": material["can_edit"],
        "properties": {"文档类型": DOCUMENT_LABELS[material["document_type"]], "课程": str(fields.get("course_name") or ""),
            "班级": str(fields.get("class_name") or ""), "学期": str(fields.get("semester") or fields.get("academic_year") or "")}}


def selection_context(conn, user, values):
    materials = [load_material(conn, user, ref, write=False) for ref in references(values)]
    documents = [document_properties(conn, material) for material in materials]
    common = {key: (documents[0]["properties"][key] if all(doc["properties"][key] == documents[0]["properties"][key] for doc in documents) else "多种")
              for key in documents[0]["properties"]}
    same_type = len({item["document_type"] for item in documents}) == 1
    can_apply = same_type and len(documents) <= 50 and all(item["owner_id"] == item["actor"]["id"] for item in materials)
    return {"documents": documents, "common_properties": common, "can_apply": can_apply,
        "points": [{"key": point[0], "label": point[1], "opinion_key": point[3]} for point in DOCUMENT_POINTS[documents[0]["document_type"]]] if can_apply else [],
        "apply_reason": "" if can_apply else "请选择不超过50份本人同类型文档。"}


def create_application(conn, user, payload):
    refs = references(payload.get("documents"), 50)
    context = selection_context(conn, user, refs)
    if not context["can_apply"]:
        raise signature_service.SignatureServiceError(400, context["apply_reason"])
    allowed = {point["key"] for point in context["points"]}
    config = payload.get("points")
    if not isinstance(config, list) or not config or len(config) > len(allowed):
        raise signature_service.SignatureServiceError(400, "请选择需要配置的签名位置。")
    seen = set()
    for point in config:
        if not isinstance(point, dict) or point.get("key") not in allowed or point["key"] in seen:
            raise signature_service.SignatureServiceError(400, "签名位置无效或重复。")
        seen.add(point["key"])
        ids = point.get("signature_ids")
        if not isinstance(ids, list) or not 1 <= len(ids) <= 12 or any(type(value) is not int or value <= 0 for value in ids):
            raise signature_service.SignatureServiceError(400, "每个位置请选择1至12个有效签名。")
        if point.get("mode", "append") not in {"append", "replace"} or point.get("opinion_mode", "keep") not in {"keep", "stamp", "clear"}:
            raise signature_service.SignatureServiceError(400, "签名应用方式无效。")
    key = str(payload.get("idempotency_key") or "")[:120]
    if not key:
        raise signature_service.SignatureServiceError(400, "请刷新申请窗口后重新提交。")
    plan = {"documents": context["documents"], "points": config, "auto_apply": payload.get("auto_apply") is True, "note": str(payload.get("note") or "")[:300]}
    # The identity hash excludes mutable server-computed preflight metadata.
    digest = hashlib.sha256(dumps({"documents": refs, "points": config, "auto_apply": plan["auto_apply"], "note": plan["note"]}).encode()).hexdigest()
    batch_id = uuid.uuid4().hex
    conn.execute("""INSERT INTO signature_application_batches (id, requester_role, requester_id, idempotency_key, request_hash, document_type, request_json)
        VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (requester_role, requester_id, idempotency_key) DO NOTHING""",
        (batch_id, user["role"], user["id"], key, digest, context["documents"][0]["document_type"], dumps(plan)))
    row = conn.execute("SELECT * FROM signature_application_batches WHERE requester_role = ? AND requester_id = ? AND idempotency_key = ?", (user["role"], user["id"], key)).fetchone()
    if row["request_hash"] != digest:
        raise signature_service.SignatureServiceError(409, "此提交编号已用于另一份配置，请重新打开申请窗口。")
    create_ai_job(conn, task_type="material_signature_batch", dedupe_key=f"material-signature-batch:{row['id']}", payload={"batch_id": row["id"]},
                  owner_role=user["role"], owner_user_pk=user["id"], priority=35)
    return job_status(conn, user, row["id"], "application")


def job_status(conn, user, job_id, kind):
    table, prefix = ("signature_application_batches", "requester") if kind == "application" else ("material_export_bundles", "owner")
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ? AND {prefix}_role = ? AND {prefix}_id = ?", (job_id, user["role"], user["id"])).fetchone()
    if not row:
        raise signature_service.SignatureServiceError(404, "任务不存在或无权查看。")
    row = dict(row)
    result = {"id": row["id"], "status": row["status"], "results": json.loads(row["results_json"] or "[]"), "error_message": row["error_message"] or ""}
    if kind == "application":
        flows = conn.execute("SELECT id, material_id, function_point_key, status, apply_status, apply_error FROM signature_point_flows WHERE application_batch_id = ? ORDER BY id", (job_id,)).fetchall()
        result["flows"] = [dict(flow) for flow in flows]
    else:
        result["download_url"] = f"/api/signatures/materials/bundles/{job_id}/download" if row["status"] == "ready" else ""
    return result


def bundle_preflight(conn, user, payload):
    context = selection_context(conn, user, payload.get("documents"))
    bundle_id = uuid.uuid4().hex
    plan = {"documents": context["documents"], "allow_incomplete": False}
    digest = hashlib.sha256(dumps(plan).encode()).hexdigest()
    conn.execute("""INSERT INTO material_export_bundles (id, owner_role, owner_id, idempotency_key, request_hash, status, plan_json, expires_at)
        VALUES (?, ?, ?, ?, ?, 'preflight', ?, ?)""", (bundle_id, user["role"], user["id"], bundle_id, digest, dumps(plan), (datetime.now() + timedelta(days=7)).isoformat()))
    return {"id": bundle_id, "count": len(context["documents"]), "incomplete": [doc["title"] for doc in context["documents"] if not doc["complete"]]}


def submit_bundle(conn, user, bundle_id, allow_incomplete):
    job_status(conn, user, bundle_id, "bundle")
    conn.execute("UPDATE material_export_bundles SET id = id WHERE id = ?", (bundle_id,))
    row = conn.execute("SELECT * FROM material_export_bundles WHERE id = ?", (bundle_id,)).fetchone()
    if datetime.fromisoformat(str(row["expires_at"])) < datetime.now():
        raise signature_service.SignatureServiceError(410, "打包确认已过期，请重新选择。")
    if row["status"] != "preflight":
        return job_status(conn, user, bundle_id, "bundle")
    plan = json.loads(row["plan_json"])
    if any(not doc["complete"] for doc in plan["documents"]) and allow_incomplete is not True:
        raise signature_service.SignatureServiceError(409, "包含待补充文档，请确认后继续打包。")
    plan["allow_incomplete"] = allow_incomplete is True
    conn.execute("UPDATE material_export_bundles SET status = 'queued', plan_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (dumps(plan), bundle_id))
    create_ai_job(conn, task_type="material_export_bundle", dedupe_key=f"material-export-bundle:{bundle_id}", payload={"bundle_id": bundle_id},
                  owner_role=user["role"], owner_user_pk=user["id"], priority=60)
    return job_status(conn, user, bundle_id, "bundle")
