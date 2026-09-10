"""Freeze the relational references changed by the ordinary signature merge.

Immutable rendered documents/snapshots are not rewritten. New reference writers
take their material then signature locks and re-read the live signature, so a
waiter cannot insert a new active reference after a merge soft-deletes its source.
"""
from .signature_account_lock_service import (
    assert_signature_bindings, lock_identity_accounts, lock_signature_rows, signature_bindings,
)
from .signature_workflow_lock_service import lock_signature_materials
from .signature_service import SignatureServiceError


def _rows(conn, table, columns, predicate, values):
    rows = [dict(row) for row in conn.execute(
        f'SELECT {columns} FROM {table} WHERE {predicate} ORDER BY id LIMIT 10001', values,
    ).fetchall()]
    if len(rows) > 10000:
        raise SignatureServiceError(409, '签名历史引用较多，请联系管理员在维护窗口处理归并。')
    return rows


def _references(conn, ids):
    marks = ','.join('?' for _ in ids)
    predicate = f'signature_id IN ({marks})'
    result = {
        'requests': _rows(conn, 'signature_access_requests',
                         'id,signature_id,flow_id,snapshot_id,request_kind,requester_role,requester_id,material_type,material_id', predicate, ids),
        'bindings': _rows(conn, 'signature_point_bindings', 'id,signature_id,material_type,material_id', predicate, ids),
        'flow_items': _rows(conn, 'signature_point_flow_items', 'id,signature_id,flow_id,request_id', predicate, ids),
        'usage': _rows(conn, 'signature_usage_logs', 'id,signature_id,context_type,context_id', predicate, ids),
    }
    flow_ids = sorted({int(row['flow_id']) for row in [*result['requests'], *result['flow_items']] if row['flow_id']})
    result['flows'] = _rows(conn, 'signature_point_flows', 'id,snapshot_id,material_type,material_id',
                            f"id IN ({','.join('?' for _ in flow_ids)})", tuple(flow_ids)) if flow_ids else []
    request_ids = sorted({int(row['id']) for row in result['requests']})
    result['request_items'] = _rows(conn, 'signature_access_request_items', 'id,request_id,material_type,material_id',
                                    f"request_id IN ({','.join('?' for _ in request_ids)})", tuple(request_ids)) if request_ids else []
    snapshots = sorted({row['snapshot_id'] for row in [*result['requests'], *result['flows']] if row['snapshot_id']})
    result['snapshots'] = _rows(conn, 'signature_material_snapshots', 'id,material_type,material_id',
                                f"id IN ({','.join('?' for _ in snapshots)})", tuple(snapshots)) if snapshots else []
    return result


def lock_signature_merge(conn, signature_ids, *, duplicate_ids):
    ids = tuple(sorted({int(value) for value in signature_ids}))
    from .signature_merge_export_guard_service import assert_no_live_export_references
    assert_no_live_export_references(conn, duplicate_ids)
    before_bindings = signature_bindings(conn, ids)
    references = _references(conn, ids)
    lock_identity_accounts(conn, before_bindings.values())
    materials = {(row['material_type'], row['material_id']) for key in
                 ('requests','bindings','flows','request_items','snapshots') for row in references[key]}
    # Usage contexts include historical non-material labels. Only canonical
    # known material identifiers participate in the material lock order.
    for row in references['usage']:
        if row['context_type'] == 'academic_final_material':
            try:
                int(row['context_id'])
            except (ValueError, TypeError):
                continue
        materials.add((row['context_type'], row['context_id']))
    lock_signature_materials(conn, materials)
    lock_signature_rows(conn, ids)
    assert_signature_bindings(conn, before_bindings)
    if _references(conn, ids) != references:
        raise SignatureServiceError(409, '签名引用已变化，请重新读取后归并。')
    assert_no_live_export_references(conn, duplicate_ids)
    # Locks remain held until the caller's commit/rollback, including history.
    for row in references['flows']:
        conn.execute('UPDATE signature_point_flows SET id=id WHERE id=?', (row['id'],))
    for row in references['requests']:
        conn.execute('UPDATE signature_access_requests SET id=id WHERE id=?', (row['id'],))
    if _references(conn, ids) != references:
        raise SignatureServiceError(409, '签名引用已变化，请重新读取后归并。')
