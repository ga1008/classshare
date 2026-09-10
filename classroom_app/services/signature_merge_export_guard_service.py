"""Refuse a merge that would silently remove a live document's signature.

The merge cannot rebuild documents or obtain fresh approvals. Only relational
history can be repointed here; frozen artifacts/snapshots are never rewritten.
"""
import json
import sqlite3

from .signature_service import SignatureServiceError


def _contains(value, targets):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return False
    values = value if isinstance(value, list) else [value]
    for item in values:
        try:
            if not isinstance(item, bool) and int(item) in targets:
                return True
        except (TypeError, ValueError):
            pass
    return False


def assert_no_live_export_references(conn, duplicate_ids):
    targets = {int(value) for value in duplicate_ids}
    if not targets:
        return
    marks = ','.join('?' for _ in targets)
    params = tuple(sorted(targets))
    # Dedicated columns are the normal plan renderer's source of truth.
    plans = conn.execute(
        'SELECT id,title,examiner_signature_id,reviewer_signature_id,examiner_signature_ids_json,reviewer_signature_ids_json '
        'FROM assessment_plans WHERE examiner_signature_id IS NOT NULL OR reviewer_signature_id IS NOT NULL '
        "OR COALESCE(examiner_signature_ids_json,'[]')<>'[]' OR COALESCE(reviewer_signature_ids_json,'[]')<>'[]' "
        'ORDER BY id LIMIT 10001',
    ).fetchall()
    if len(plans) > 10000:
        raise SignatureServiceError(409, '签名材料引用较多，请联系管理员在维护窗口核对归并。')
    for row in plans:
        if any(_contains(row[key], targets) for key in ('examiner_signature_id','reviewer_signature_id',
                                                       'examiner_signature_ids_json','reviewer_signature_ids_json')):
            raise SignatureServiceError(409, f"考核计划表 {row['id']}（{str(row['title'] or '')[:100]}）仍使用待归并签名，请先在原材料重新选择主签名并确认授权。")
    # Current point bindings override serialized fields for versioned materials.
    binding = conn.execute(
        'SELECT m.id FROM signature_point_bindings b JOIN material_ai_import_records m '
        "ON CAST(m.id AS TEXT)=b.material_id WHERE b.material_type='academic_final_material' "
        f"AND b.signature_id IN ({marks}) AND b.material_revision=TRIM(COALESCE(m.signature_revision,'')) ORDER BY m.id LIMIT 1", params,
    ).fetchone()
    if binding:
        raise SignatureServiceError(409, f"期末材料 {binding['id']} 仍绑定待归并签名，请先在原材料重新选择主签名并确认授权。")
    # Old unversioned materials render the six explicit legacy fields. Extract
    # only those small values in SQL; do not load full documents into memory.
    keys = [f'{role}_signature{suffix}' for role in ('teacher','department','dean') for suffix in ('_id','_ids')]
    if isinstance(conn, sqlite3.Connection):
        safe = "CASE WHEN json_valid(export_payload_json) THEN export_payload_json ELSE '{}' END"
        values = ','.join(f"COALESCE(json_extract({safe}, '$.export_payload.fields.{key}'),json_extract({safe}, '$.fields.{key}')) AS {key}" for key in keys)
    else:
        safe = "CASE WHEN export_payload_json IS JSON THEN export_payload_json::jsonb ELSE '{}'::jsonb END"
        values = ','.join(f"COALESCE(({safe})->'export_payload'->'fields'->>'{key}',({safe})->'fields'->>'{key}') AS {key}" for key in keys)
    rows = conn.execute(
        f'SELECT id,{values} FROM material_ai_import_records '
        "WHERE TRIM(COALESCE(signature_revision,''))='' AND export_payload_json LIKE '%signature_id%' ORDER BY id LIMIT 10001",
    ).fetchall()
    if len(rows) > 10000:
        raise SignatureServiceError(409, '历史签名材料较多，请联系管理员在维护窗口核对归并。')
    for row in rows:
        if any(_contains(row[key], targets) for key in keys):
            raise SignatureServiceError(409, f"历史期末材料 {row['id']} 仍使用待归并签名，请先打开原材料重新绑定主签名；原文档不会被自动重写。")
