"""The canonical material-record payload used by previews, exports and reviews."""

from __future__ import annotations

import json

from .material_signature_revision_service import json_object


def build_record_export_payload(row, conn=None, *, for_export: bool = False) -> dict:
    from .academic_final_material_source_service import hydrate_academic_final_material_source, strip_academic_native_source

    try:
        warnings = json.loads(dict(row).get("warnings_json") or "[]")
    except (TypeError, ValueError):
        warnings = []
    payload = json_object(row["parsed_payload_json"])
    if not payload:
        payload = {
            "metadata": json_object(row["metadata_json"]),
            "content_markdown": row["content_markdown"] or "",
            "tables": [],
            "warnings": warnings if isinstance(warnings, list) else [],
            "export_payload": json_object(row["export_payload_json"]),
            "document_group": row["document_group"],
            "document_type": row["document_type"],
            "document_type_label": row["document_type_label"],
            "extraction_method": row["extraction_method"],
        }
    payload = strip_academic_native_source(payload)
    if conn is not None and str(row["document_type"] or "") in {"academic_grade_register", "academic_exam_analysis"}:
        from .academic_final_material_service import hydrate_academic_final_material_signature_paths, repair_legacy_grade_register_roster_order

        payload = repair_legacy_grade_register_roster_order(conn, row, payload)
        payload = hydrate_academic_final_material_signature_paths(conn, payload, record=row)
        if for_export:
            payload = hydrate_academic_final_material_source(conn, row, payload)
    return payload
