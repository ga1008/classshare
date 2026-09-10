"""Shared material -> claim signature -> flow -> request decision locks.

Material saves/application already lock the material before the flow. Review,
cancellation and batch review must use the same order before changing any child
decision row. Locks are per material/flow/request; unrelated documents proceed.
"""
from .signature_service import SignatureServiceError


def lock_signature_materials(conn, references, *, legacy_context=False):
    """Lock fixed, known material tables before binding or invalidation writes."""
    materials = set()
    for material_type, identifier in references:
        table = {'academic_final_material':'material_ai_import_records', 'assessment_plan':'assessment_plans'}.get(material_type)
        if table and identifier not in (None, ''):
            try:
                key = str(int(identifier)) if material_type == 'academic_final_material' else str(identifier)
            except (TypeError, ValueError) as exc:
                if legacy_context:
                    # Historical generic usage events can have descriptive
                    # context ids. They are not references to material rows.
                    continue
                raise SignatureServiceError(409, '签章材料编号无效，请核对申请记录。') from exc
            materials.add((table, key))
    for table, identifier in sorted(materials):
        conn.execute(f'UPDATE {table} SET id=id WHERE id=?', (identifier,))


_REQUEST_COLUMNS = 'id,flow_id,snapshot_id,signature_id,request_kind,requester_role,requester_id,material_type,material_id'
_FLOW_COLUMNS = 'id,snapshot_id,material_type,material_id'


def lock_signature_workflows(conn, *, request_ids=(), flow_ids=(), claim_signature_ids=(), account_holders=()):
    requested = sorted(set(int(value) for value in request_ids if int(value) > 0))
    flows = set(int(value) for value in flow_ids if int(value) > 0)
    requests = {}
    if requested:
        marks = ','.join('?' for _ in requested)
        rows = conn.execute(f'SELECT {_REQUEST_COLUMNS} FROM signature_access_requests WHERE id IN ({marks})', tuple(requested)).fetchall()
        requests.update({int(row['id']):dict(row) for row in rows})
        flows.update(int(row['flow_id']) for row in rows if row['flow_id'])
    flow_rows = {}
    if flows:
        ordered = sorted(flows)
        marks = ','.join('?' for _ in ordered)
        rows = conn.execute(f'SELECT {_FLOW_COLUMNS} FROM signature_point_flows WHERE id IN ({marks})', tuple(ordered)).fetchall()
        flow_rows = {int(row['id']):dict(row) for row in rows}
        # An end-flow operation covers all requests in that flow, including an
        # approval that may have just committed; never read only stale pending.
        if flow_ids:
            explicit = sorted(set(int(value) for value in flow_ids if int(value) > 0))
            marks = ','.join('?' for _ in explicit)
            rows = conn.execute(f'SELECT {_REQUEST_COLUMNS} FROM signature_access_requests WHERE flow_id IN ({marks})', tuple(explicit)).fetchall()
            requests.update({int(row['id']):dict(row) for row in rows})
    snapshots = sorted({row['snapshot_id'] for row in [*requests.values(),*flow_rows.values()] if row['snapshot_id']})
    # Historical flows may predate immutable snapshots but still refer to the
    # same material and are invalidated by its ordinary content editor.
    materials = {(row['material_type'],row['material_id']) for row in [*requests.values(),*flow_rows.values()]}
    if snapshots:
        marks = ','.join('?' for _ in snapshots)
        rows = conn.execute(f'SELECT material_type,material_id FROM signature_material_snapshots WHERE id IN ({marks})', tuple(snapshots)).fetchall()
        for row in rows:
            materials.add((row['material_type'],row['material_id']))
    claims = {int(value) for value in claim_signature_ids if int(value)>0}
    claims.update(int(row['signature_id']) for row in requests.values() if row['request_kind']=='claim')
    from .signature_account_lock_service import prepare_signature_accounts, lock_signature_rows, assert_signature_bindings
    holders = [*account_holders, *((row['requester_role'], row['requester_id'])
                for row in requests.values() if row['request_kind']=='claim')]
    before_bindings = prepare_signature_accounts(conn, claims, additional_holders=holders) if claims else {}
    item_rows = []
    if flows:
        marks = ','.join('?' for _ in flows)
        item_rows = [dict(row) for row in conn.execute(
            f'SELECT id,flow_id,signature_id,request_id FROM signature_point_flow_items WHERE flow_id IN ({marks}) ORDER BY id',
            tuple(sorted(flows)),
        ).fetchall()]
    signatures = claims | {int(row['signature_id']) for row in requests.values()}
    signatures.update(int(row['signature_id']) for row in item_rows)
    lock_signature_materials(conn, materials)
    lock_signature_rows(conn, signatures)
    assert_signature_bindings(conn, before_bindings)
    if claims:
        marks = ','.join('?' for _ in claims)
        # A claim transfer cancels its competitors. Lock them only after their
        # shared signature, so competing reviewers cannot each hold one request.
        rows = conn.execute(f"SELECT {_REQUEST_COLUMNS} FROM signature_access_requests WHERE signature_id IN ({marks}) AND request_kind='claim' AND status='pending'", tuple(sorted(claims))).fetchall()
        for row in rows:
            if row['flow_id'] or row['snapshot_id'] or row['material_type'] or row['material_id']:
                raise SignatureServiceError(409, '认领申请包含异常材料关联，请先核对申请记录。')
            requests.setdefault(int(row['id']),dict(row))
    for identifier in sorted(flows):
        conn.execute('UPDATE signature_point_flows SET id=id WHERE id=?', (identifier,))
    for identifier in sorted(requests):
        conn.execute('UPDATE signature_access_requests SET id=id WHERE id=?', (identifier,))
    # Associations are immutable in normal workflows. Fail closed if an import
    # or another writer did change them while the lock acquisition was waiting.
    for identifier, before in flow_rows.items():
        after = conn.execute(f'SELECT {_FLOW_COLUMNS} FROM signature_point_flows WHERE id=?', (identifier,)).fetchone()
        if after is None or dict(after) != before:
            raise SignatureServiceError(409, '签章流程材料已变化，请重新读取后操作。')
    for identifier, before in requests.items():
        after = conn.execute(f'SELECT {_REQUEST_COLUMNS} FROM signature_access_requests WHERE id=?', (identifier,)).fetchone()
        if after is None or dict(after) != before:
            raise SignatureServiceError(409, '签章申请材料已变化，请重新读取后操作。')
    if flows:
        marks = ','.join('?' for _ in flows)
        current_items = [dict(row) for row in conn.execute(
            f'SELECT id,flow_id,signature_id,request_id FROM signature_point_flow_items WHERE flow_id IN ({marks}) ORDER BY id',
            tuple(sorted(flows)),
        ).fetchall()]
        if current_items != item_rows:
            raise SignatureServiceError(409, '签章流程的签名集合已变化，请重新读取后操作。')
