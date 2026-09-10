"""Shared structural locks and registered-document guards for material trees.

Callers own their transactions. Owner namespaces, registered packs, then tree
roots are locked in ascending ID order. Names/parents must be re-read after the
lock; a tree relocated while waiting yields 409 rather than using stale paths.
"""
from datetime import datetime, timedelta

from fastapi import HTTPException

from ..db.connection import get_configured_db_engine


def has_pack_table(conn):
    if get_configured_db_engine() == 'postgres':
        return conn.execute("SELECT to_regclass('public.course_doc_packs')").fetchone()[0] is not None
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='course_doc_packs'").fetchone() is not None


def lock_material_trees(conn, *, teacher_id, material_ids=()):
    from .materials_service import ensure_teacher_material_owner

    ids = sorted({int(value) for value in material_ids if value is not None})
    before = {mid: dict(ensure_teacher_material_owner(conn, mid, teacher_id)) for mid in ids}
    owners = sorted({int(row['teacher_id']) for row in before.values()} or {int(teacher_id)})
    for owner in owners:
        conn.execute('UPDATE teachers SET id=id WHERE id=?', (owner,))
    # LessonDoc's editor takes the pack row before updating material rows.
    # Preserve that order, without adding request-time schema migrations.
    if has_pack_table(conn):
        for owner in owners:
            packs = conn.execute('SELECT id FROM course_doc_packs WHERE teacher_id=? ORDER BY id', (owner,)).fetchall()
            for pack in packs:
                conn.execute('UPDATE course_doc_packs SET updated_at=updated_at WHERE id=?', (pack['id'],))
    roots = sorted({int(row.get('root_id') or row['id']) for row in before.values()})
    for root in roots:
        conn.execute('UPDATE course_materials SET id=id WHERE id=?', (root,))
    current = {mid: dict(ensure_teacher_material_owner(conn, mid, teacher_id)) for mid in ids}
    if any((row['teacher_id'], row['root_id']) != (before[mid]['teacher_id'], before[mid]['root_id'])
           for mid, row in current.items()):
        raise HTTPException(409, '材料归属或目录已变化，请刷新后重试。')
    return current


def related_active_packs(conn, material):
    if not has_pack_table(conn):
        return []
    path = str(material['material_path'])
    return [dict(row) for row in conn.execute('''
        SELECT p.id, p.root_material_id, p.teacher_id, p.status, m.material_path
        FROM course_doc_packs p JOIN course_materials m ON m.id=p.root_material_id
        WHERE p.status='active' AND m.root_id=? AND
          (m.material_path=? OR SUBSTR(m.material_path,1,LENGTH(?)+1)=? || '/'
             OR SUBSTR(?,1,LENGTH(m.material_path)+1)=m.material_path || '/')
        ORDER BY p.id
    ''', (material['root_id'], path, path, path, path)).fetchall()]


def ensure_plain_tree_operation(conn, material, *, action):
    ensure_subtree_owner(conn, material)
    packs = related_active_packs(conn, material)
    for pack in packs:
        # Renaming a complete pack's outer folder does not change its relative
        # registered sources. Moving or editing its internal structure does.
        if action == 'rename' and int(pack['root_material_id']) == int(material['id']):
            continue
        raise HTTPException(409, '该操作会改变已登记学习文档包的结构，请使用学习文档编辑功能。')


def archive_deleted_packs(conn, material):
    ensure_subtree_owner(conn, material)
    packs = related_active_packs(conn, material)
    path = str(material['material_path'])
    for pack in packs:
        if not (pack['material_path'] == path or pack['material_path'].startswith(path + '/')):
            raise HTTPException(409, '不能单独删除学习文档包内部材料，请使用学习文档编辑功能。')
        conn.execute("UPDATE course_doc_packs SET status='archived',updated_at=? WHERE id=? AND status='active'",
                     (datetime.now().isoformat(), pack['id']))
    return len(packs)


def ensure_subtree_owner(conn, material):
    path = str(material['material_path'])
    if conn.execute('''SELECT id FROM course_materials WHERE root_id=?
        AND (material_path=? OR SUBSTR(material_path,1,LENGTH(?)+1)=? || '/')
        AND COALESCE(teacher_id,0)<>? LIMIT 1''',
        (material['root_id'],path,path,path,material['teacher_id'])).fetchone():
        raise HTTPException(409, '材料树存在混合归属，请先由管理员核对结构。')


def touch_material_nodes(conn, ids, now):
    """Structural changes invalidate both source and target picker revisions."""
    for mid in sorted({int(value) for value in ids if value is not None}):
        row = conn.execute('SELECT updated_at FROM course_materials WHERE id=?', (mid,)).fetchone()
        if row is None:
            continue
        stamp = now
        previous = str(row['updated_at'] or '')
        if previous and stamp <= previous:
            try:
                stamp = (datetime.fromisoformat(previous) + timedelta(microseconds=1)).isoformat()
            except ValueError:
                pass
        conn.execute('UPDATE course_materials SET updated_at=? WHERE id=?', (stamp, mid))


def check_material_revision(material, expected, *, target=False):
    if expected is None:
        return  # Older Web callers remain supported; Agent capabilities require it.
    actual = str(material.get('updated_at') or 'legacy') if material else 'root'
    if expected != actual:
        raise HTTPException(409, '目标目录已变化，请刷新后重试。' if target else '材料已变化，请刷新后重试。')
