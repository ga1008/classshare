"""Restart-safe material jobs using the existing durable queue and leases."""
from __future__ import annotations

import asyncio
import io
import json
import re
import zipfile

from ..database import get_db_connection
from . import signature_point_service as points, signature_service, signature_workflow_service as workflow
from .material_signature_service import build_document_artifact, dumps, load_material, prepare_snapshot, store_artifact


def process_application_batch(batch_id):
    with get_db_connection() as conn:
        row = dict(conn.execute("SELECT * FROM signature_application_batches WHERE id = ?", (batch_id,)).fetchone())
    if row["status"] in {"completed", "partial", "failed"}:
        return
    user = {"role": row["requester_role"], "id": row["requester_id"]}
    plan = json.loads(row["request_json"])
    results = json.loads(row["results_json"] or "[]")
    done = {(item["material_type"], item["material_id"]) for item in results}
    for doc in plan["documents"]:
        if (doc["material_type"], doc["material_id"]) in done:
            continue
        result = {key: doc[key] for key in ("material_type", "material_id", "title")}
        try:
            snapshot = prepare_snapshot(user, doc)
            if any(snapshot[key] != doc[key] for key in ("material_revision", "content_fingerprint")):
                raise signature_service.SignatureServiceError(409, "材料内容已变更，请重新申请。")
            with get_db_connection() as conn:
                from .signature_workflow_lock_service import lock_signature_materials
                from .signature_account_lock_service import lock_signature_rows
                lock_signature_materials(conn, [(doc['material_type'], doc['material_id'])])
                prepared = []
                for config in plan['points']:
                    scope = {**doc, 'function_point_key': config['key']}
                    current_ids = points._binding_ids(conn, scope)
                    ids = list(dict.fromkeys((current_ids if config.get('mode', 'append') == 'append' else []) + config['signature_ids']))
                    prepared.append((config, ids))
                lock_signature_rows(conn, [identifier for _, ids in prepared for identifier in ids])
                flow_ids = []
                for config, ids in prepared:
                    flow = points.create_point_flow(conn, user, function_point_key=config["key"],
                        material_type=doc["material_type"], material_id=doc["material_id"], signature_ids=ids,
                        note=plan["note"], snapshot=snapshot, auto_apply=plan["auto_apply"], application_batch_id=batch_id,
                        notify_reviewers=False, opinion_mode=config.get("opinion_mode", "keep"))
                    flow_ids.append(flow["flow"]["id"])
                result.update(status="submitted", flow_ids=flow_ids)
                results.append(result)
                # Persist the per-document outcome with its flows, so lease retry
                # never creates duplicate requests for completed documents.
                conn.execute("UPDATE signature_application_batches SET results_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (dumps(results), batch_id))
                conn.commit()
        except (signature_service.SignatureServiceError, ValueError) as exc:
            result.update(status="failed", error=getattr(exc, "message", str(exc))[:500])
            results.append(result)
            with get_db_connection() as conn:
                conn.execute("UPDATE signature_application_batches SET results_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (dumps(results), batch_id))
                conn.commit()
    with get_db_connection() as conn:
        # One notification per reviewer per batch, with only that reviewer's count.
        reviewers = conn.execute("""SELECT reviewer.reviewer_role AS role, reviewer.reviewer_id AS id, COUNT(DISTINCT request.snapshot_id) AS count
            FROM signature_access_request_reviewers reviewer
            JOIN signature_access_requests request ON request.id = reviewer.request_id
            JOIN signature_point_flows flow ON flow.id = request.flow_id
            WHERE flow.application_batch_id = ? AND reviewer.status = 'pending'
            GROUP BY reviewer.reviewer_role, reviewer.reviewer_id""", (batch_id,)).fetchall()
        actor = signature_service.build_signature_actor(conn, user)
        for reviewer in reviewers:
            workflow._notify(conn, recipients=[dict(reviewer)], actor=actor, title="材料签名待审批",
                body=f"{actor['name']} 提交了 {reviewer['count']} 份材料，请查看实际文档后审批。",
                ref_type="signature_application_batch", ref_id=batch_id, metadata={"application_batch_id": batch_id})
        failures = sum(result["status"] == "failed" for result in results)
        status = "failed" if failures == len(results) else "partial" if failures else "completed"
        conn.execute("UPDATE signature_application_batches SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (status, batch_id))
        conn.commit()


def process_export_bundle(bundle_id):
    with get_db_connection() as conn:
        row = dict(conn.execute("SELECT * FROM material_export_bundles WHERE id = ?", (bundle_id,)).fetchone())
        if row["status"] in {"ready", "failed", "expired"}:
            return
        conn.execute("UPDATE material_export_bundles SET status = 'running', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (bundle_id,))
        conn.commit()
    user = {"role": row["owner_role"], "id": row["owner_id"]}
    plan = json.loads(row["plan_json"])
    results, total_bytes = [], 0
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, doc in enumerate(plan["documents"], 1):
            result = {key: doc[key] for key in ("material_type", "material_id", "title")}
            try:
                with get_db_connection() as conn:
                    current = load_material(conn, user, doc, write=False)
                    if any(current[key] != doc[key] for key in ("material_revision", "content_fingerprint")) or str(current["row"].get("updated_at") or "") != doc["updated_at"]:
                        raise ValueError("材料在确认打包后已更新，请重新选择打包。")
                    artifact = build_document_artifact(conn, current, allow_incomplete=plan["allow_incomplete"])
                total_bytes += len(artifact.content)
                if total_bytes > 256 * 1024 * 1024:
                    raise ValueError("本次文档超过256MB，请分批打包。")
                safe_name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", artifact.filename)[:180]
                archive.writestr(f"{index:03d}-{safe_name}", artifact.content)
                result.update(status="included", filename=f"{index:03d}-{safe_name}", incomplete=not doc["complete"])
            except (signature_service.SignatureServiceError, ValueError) as exc:
                result.update(status="failed", error=getattr(exc, "message", str(exc))[:500])
            results.append(result)
            with get_db_connection() as conn:
                conn.execute("UPDATE material_export_bundles SET results_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (dumps(results), bundle_id))
                conn.commit()
        archive.writestr("打包清单.json", json.dumps(results, ensure_ascii=False, indent=2))
    success = any(result["status"] == "included" for result in results)
    stored = store_artifact(output.getvalue(), filename="材料打包.zip", media_type="application/zip", area="bundles") if success else {}
    with get_db_connection() as conn:
        conn.execute("UPDATE material_export_bundles SET status = ?, artifact_json = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            ("ready" if success else "failed", dumps(stored), "部分文档未能打包，请查看清单。" if any(result["status"] == "failed" for result in results) else "", bundle_id))
        conn.commit()


async def dispatch_material_job(task_type, payload):
    if task_type == "material_signature_apply":
        from .material_signature_apply_service import apply_flow

        try:
            await apply_flow(int(payload["flow_id"]))
        except signature_service.SignatureServiceError as exc:
            with get_db_connection() as conn:
                conn.execute("UPDATE signature_point_flows SET apply_status = 'failed', apply_error = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND apply_status <> 'applied'", (exc.message, payload["flow_id"]))
                conn.commit()
    elif task_type == "material_signature_batch":
        await asyncio.to_thread(process_application_batch, payload["batch_id"])
    elif task_type == "material_export_bundle":
        await asyncio.to_thread(process_export_bundle, payload["bundle_id"])
    else:
        raise ValueError("unsupported material job")


def mark_material_job_failed(task_type, payload, message):
    with get_db_connection() as conn:
        if task_type == "material_signature_apply":
            conn.execute("UPDATE signature_point_flows SET apply_status='failed', apply_error=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND apply_status<>'applied'", (message[:500], payload["flow_id"]))
        elif task_type == "material_signature_batch":
            conn.execute("UPDATE signature_application_batches SET status='failed', error_message=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='preparing'", (message[:500], payload["batch_id"]))
        elif task_type == "material_export_bundle":
            conn.execute("UPDATE material_export_bundles SET status='failed', error_message=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND status IN ('queued','running')", (message[:500], payload["bundle_id"]))
        conn.commit()
