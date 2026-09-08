"""Apply approved point plans through the canonical document save contracts."""
from __future__ import annotations

from ..database import get_db_connection
from . import signature_point_service as points, signature_service, signature_workflow_service as workflow
from .material_signature_service import DOCUMENT_POINTS, check_request_snapshot, load_material
from .material_signature_revision_service import ordered_binding_hash, content_fingerprint


def enqueue_application(conn, flow_id: int) -> None:
    row = conn.execute("SELECT * FROM signature_point_flows WHERE id = ?", (flow_id,)).fetchone()
    if not row or not row["auto_apply"] or row["status"] != "approved" or row["apply_status"] not in {"waiting", "queued"}:
        return
    from .ai_durable_job_service import create_ai_job

    create_ai_job(conn, task_type="material_signature_apply", dedupe_key=f"material-signature-apply:{flow_id}:{row['plan_revision']}",
                  payload={"flow_id": flow_id}, owner_role=row["requester_role"], owner_user_pk=row["requester_id"], priority=25)
    conn.execute("UPDATE signature_point_flows SET apply_status = 'queued', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (flow_id,))


def validate_application(conn, flow_id: int) -> tuple[dict, dict, list[int]]:
    row = conn.execute("SELECT * FROM signature_point_flows WHERE id = ?", (flow_id,)).fetchone()
    if not row:
        raise signature_service.SignatureServiceError(404, "申请流程不存在。")
    flow = dict(row)
    check_request_snapshot(conn, flow)
    conn.execute("UPDATE signature_point_flows SET id = id WHERE id = ?", (flow_id,))
    flow = dict(conn.execute("SELECT * FROM signature_point_flows WHERE id = ?", (flow_id,)).fetchone())
    if flow["status"] != "approved" or flow["apply_status"] not in {"waiting", "queued", "manual", "failed"}:
        raise signature_service.SignatureServiceError(409, "该签名配置尚未全部批准或已经应用。")
    user = {"role": flow["requester_role"], "id": flow["requester_id"]}
    check_request_snapshot(conn, flow)
    material = load_material(conn, user, flow)
    points._lock_binding_scope(conn, flow)
    if ordered_binding_hash(points._binding_ids(conn, flow)) != flow["base_binding_hash"]:
        raise signature_service.SignatureServiceError(409, "该位置的签名已被调整，请重新确认签名顺序。")
    entries = conn.execute("SELECT * FROM signature_point_flow_items WHERE flow_id = ? ORDER BY display_order, id", (flow_id,)).fetchall()
    if not entries or any(entry["status"] != "approved" for entry in entries):
        raise signature_service.SignatureServiceError(409, "该位置尚有未批准的签名。")
    for entry in entries:
        signature = workflow._signature_row(conn, entry["signature_id"])
        if entry["signature_hash"] != signature["file_hash"]:
            raise signature_service.SignatureServiceError(409, "签名图片已变更，请重新申请。")
    return flow, material, [int(entry["signature_id"]) for entry in entries]


def mark_applied(conn, flow_id: int) -> None:
    conn.execute("UPDATE signature_point_flows SET apply_status = 'applied', apply_error = '', applied_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (flow_id,))


async def apply_flow(flow_id: int) -> dict:
    with get_db_connection() as conn:
        existing = conn.execute("SELECT apply_status FROM signature_point_flows WHERE id = ?", (flow_id,)).fetchone()
        if existing and existing["apply_status"] == "applied":
            return {"flow_id": flow_id, "status": "applied"}
        flow, material, ids = validate_application(conn, flow_id)
        user = {"role": flow["requester_role"], "id": flow["requester_id"]}
        if material["document_type"] == "assessment_plan":
            from .assessment_plan_service import set_signatures

            points.bind_point_signatures(conn, user, function_point_key=flow["function_point_key"],
                material_type=flow["material_type"], material_id=flow["material_id"], signature_ids=ids)
            role = "examiner" if flow["function_point_key"].endswith("examiner_signature") else "reviewer"
            set_signatures(conn, flow["material_id"], role=role, signature_ids=ids)
            mark_applied(conn, flow_id)
            conn.commit()
            return {"flow_id": flow_id, "status": "applied"}
        payload = material["payload"]
        point = next(entry for entry in DOCUMENT_POINTS[material["document_type"]] if entry[0] == flow["function_point_key"])
        fields = payload.setdefault("fields", {})
        fields[point[2]] = ids
        fields[point[2].removesuffix("s")] = ids[0]
        if point[3] and flow["opinion_mode"] == "stamp":
            fields.pop(point[3], None)
        elif point[3] and flow["opinion_mode"] == "clear":
            fields[point[3]] = ""
        payload["review_opinion_policy"] = "optional"
        if content_fingerprint(payload) != material["content_fingerprint"]:
            raise signature_service.SignatureServiceError(409, "修改自定义批语会改变材料内容，请先在编辑器保存批语，再申请签名。")
        # End read transaction before filesystem work; final save validates again.
        conn.rollback()
    from ..routers.materials_parts.academic_final_materials import _make_parse_result
    from ..routers.materials_parts.final_material_helpers import _persist_final_material_record_update

    await _persist_final_material_record_update(int(flow["material_id"]), material["row"], _make_parse_result(payload), user,
        signature_use_intents=[{"signature_ids": ids, "function_point_key": flow["function_point_key"],
            "context_type": flow["material_type"], "context_id": flow["material_id"]}],
        require_unchanged_record=True, expected_signature_flow_id=flow_id)
    return {"flow_id": flow_id, "status": "applied"}
