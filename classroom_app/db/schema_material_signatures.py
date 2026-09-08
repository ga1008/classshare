"""Additive storage for reviewable material signatures and export jobs."""

from __future__ import annotations

from typing import Any


def ensure_material_signature_schema(conn: Any, *, engine: str) -> None:
    from .schema_signature_workflow import _add_columns

    timestamp = "TIMESTAMP" if engine == "postgres" else "TEXT"
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS signature_application_batches (
            id TEXT PRIMARY KEY,
            requester_role TEXT NOT NULL,
            requester_id INTEGER NOT NULL,
            idempotency_key TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            document_type TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'preparing',
            request_json TEXT NOT NULL DEFAULT '{{}}',
            results_json TEXT NOT NULL DEFAULT '[]',
            error_message TEXT NOT NULL DEFAULT '',
            created_at {timestamp} NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at {timestamp} NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(requester_role, requester_id, idempotency_key)
        )
    """)
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS signature_material_snapshots (
            id TEXT PRIMARY KEY,
            material_type TEXT NOT NULL,
            material_id TEXT NOT NULL,
            material_revision TEXT NOT NULL,
            document_type TEXT NOT NULL,
            owner_role TEXT NOT NULL,
            owner_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            content_fingerprint TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            artifact_json TEXT NOT NULL,
            created_at {timestamp} NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    _add_columns(conn, "signature_point_flows", {
        "application_batch_id": "TEXT NOT NULL DEFAULT ''",
        "snapshot_id": "TEXT NOT NULL DEFAULT ''",
        "plan_revision": "TEXT NOT NULL DEFAULT ''",
        "base_binding_hash": "TEXT NOT NULL DEFAULT ''",
        "auto_apply": "INTEGER NOT NULL DEFAULT 0",
        "apply_status": "TEXT NOT NULL DEFAULT 'manual'",
        "apply_error": "TEXT NOT NULL DEFAULT ''",
        "applied_at": timestamp,
        "opinion_mode": "TEXT NOT NULL DEFAULT 'keep'",
        "opinion_text": "TEXT NOT NULL DEFAULT ''",
    }, engine=engine)
    _add_columns(conn, "signature_point_flow_items", {
        "signature_kind": "TEXT NOT NULL DEFAULT 'personal'",
        "signature_hash": "TEXT NOT NULL DEFAULT ''",
        "authorization_mode": "TEXT NOT NULL DEFAULT ''",
    }, engine=engine)
    _add_columns(conn, "signature_access_requests", {
        "snapshot_id": "TEXT NOT NULL DEFAULT ''",
        "signature_hash": "TEXT NOT NULL DEFAULT ''",
        "document_type": "TEXT NOT NULL DEFAULT ''",
        "requester_school": "TEXT NOT NULL DEFAULT ''",
        "requester_college": "TEXT NOT NULL DEFAULT ''",
        "requester_department": "TEXT NOT NULL DEFAULT ''",
        "requester_identities_json": "TEXT NOT NULL DEFAULT '[]'",
        "invalidation_reason": "TEXT NOT NULL DEFAULT ''",
    }, engine=engine)
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS material_export_bundles (
            id TEXT PRIMARY KEY,
            owner_role TEXT NOT NULL,
            owner_id INTEGER NOT NULL,
            idempotency_key TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            plan_json TEXT NOT NULL,
            results_json TEXT NOT NULL DEFAULT '[]',
            artifact_json TEXT NOT NULL DEFAULT '{{}}',
            error_message TEXT NOT NULL DEFAULT '',
            created_at {timestamp} NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at {timestamp} NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expires_at {timestamp},
            UNIQUE(owner_role, owner_id, idempotency_key)
        )
    """)
    for sql in (
        "CREATE INDEX IF NOT EXISTS idx_signature_batches_owner ON signature_application_batches(requester_role,requester_id,created_at)",
        "CREATE INDEX IF NOT EXISTS idx_signature_snapshot_material ON signature_material_snapshots(material_type,material_id,material_revision)",
        "CREATE INDEX IF NOT EXISTS idx_signature_flow_batch ON signature_point_flows(application_batch_id,apply_status)",
        "CREATE INDEX IF NOT EXISTS idx_signature_requests_documents ON signature_access_requests(document_type,status,requested_at,id)",
        "CREATE INDEX IF NOT EXISTS idx_signature_requests_outgoing ON signature_access_requests(requester_role,requester_id,status,requested_at,id)",
        "CREATE INDEX IF NOT EXISTS idx_signature_requests_org ON signature_access_requests(requester_school,requester_college,requester_department,status)",
        "CREATE INDEX IF NOT EXISTS idx_material_export_owner ON material_export_bundles(owner_role,owner_id,created_at)",
        "CREATE INDEX IF NOT EXISTS idx_material_export_expiry ON material_export_bundles(status,expires_at)",
    ):
        conn.execute(sql)
