"""Content identity is independent of signature binding and render caches."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any


def json_object(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def canonical_content(payload: dict) -> dict:
    """Exclude derived signature fields, but retain substantive review text."""
    value = payload.get("export_payload", payload)
    fields = {}
    for key, entry in (value.get("fields") or {}).items():
        if "signature" in key or key.endswith(("_review_stamp_ids", "_review_opinion_source", "_review_opinion_image_path")):
            continue
        if key.endswith("_review_opinion") and str(entry or "").strip() in {"", "已核", "同意"}:
            continue
        fields[key] = entry
    return {
        "template_key": value.get("template_key") or value.get("document_type") or payload.get("document_type", ""),
        "fields": fields,
        "structured": value.get("structured") or {},
    }


def content_fingerprint(payload: dict) -> str:
    data = json.dumps(canonical_content(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def ordered_binding_hash(signature_ids: list[int]) -> str:
    return hashlib.sha256(json.dumps([int(i) for i in signature_ids], separators=(",", ":")).encode()).hexdigest()


def update_record_revision(conn: Any, record: Any, payload: dict) -> str:
    """Called in the same transaction as the content write; never commits."""
    row = dict(record)
    current = str(row.get("signature_revision") or "")
    if "signature_revision" not in row or content_fingerprint(json_object(row.get("export_payload_json"))) == content_fingerprint(payload):
        return current
    revision = uuid.uuid4().hex
    conn.execute("UPDATE material_ai_import_records SET signature_revision = ? WHERE id = ?", (revision, int(row["id"])))
    invalidate_pending_material_plans(conn, "academic_final_material", str(row["id"]), revision)
    return revision


def invalidate_pending_material_plans(conn, material_type, material_id, current_revision):
    """Retain the audit trail while stopping approvals for obsolete content."""
    # Small import fixtures may not include the workflow module. Production
    # initializes this schema before any material can be changed.
    from ..db.connection import get_configured_db_engine

    if get_configured_db_engine() == "sqlite" and not conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'signature_point_flows'").fetchone():
        return
    from .signature_workflow_lock_service import lock_signature_materials
    lock_signature_materials(conn, [(material_type, material_id)])
    args = (material_type, str(material_id), current_revision)
    predicate = "material_type = ? AND material_id = ? AND material_revision <> ?"
    pending = f"SELECT id FROM signature_access_requests WHERE {predicate} AND status = 'pending'"
    conn.execute(f"UPDATE signature_access_request_reviewers SET status = 'cancelled' WHERE request_id IN ({pending}) AND status = 'pending'", args)
    conn.execute(f"UPDATE signature_access_request_items SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP WHERE request_id IN ({pending}) AND status = 'pending'", args)
    conn.execute(f"UPDATE signature_point_flow_items SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP WHERE request_id IN ({pending}) AND status = 'pending'", args)
    conn.execute(f"UPDATE signature_access_requests SET status = 'cancelled', invalidation_reason = '材料内容已更新，原申请失效', cancelled_at = CURRENT_TIMESTAMP WHERE {predicate} AND status = 'pending'", args)
    conn.execute(f"UPDATE signature_point_flows SET status = 'cancelled', apply_status = 'failed', apply_error = '材料内容已更新，原申请失效', ended_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE {predicate} AND (status IN ('pending','partially_approved') OR apply_status IN ('waiting','queued'))", args)


def plan_content(plan: dict) -> dict:
    def array(value):
        if isinstance(value, list):
            return value
        try:
            return json.loads(value or "[]")
        except (TypeError, ValueError):
            return []
    return {
        "template_key": "assessment_plan",
        "fields": plan.get("fields") or json_object(plan.get("fields_json")),
        "structured": {
            "assessment_items": plan.get("items") or array(plan.get("items_json")),
            "notes": plan.get("notes") or array(plan.get("notes_json")),
        },
    }
