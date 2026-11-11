"""Audited administrator corrections, preserving original content and source ACLs.

This is maintenance of an existing record, never a learning/reward operation.
The caller owns the transaction and must enforce administrator authorization.
"""
from __future__ import annotations

import hashlib
import json
import uuid

from fastapi import HTTPException

from ..db.schema_wanderer_runtime import ensure_wanderer_runtime_schema
from ..time_utils import local_iso

TARGETS = {
    'episode': ('wanderer_episodic_memories', 'content', 3200),
    'knowledge': ('wanderer_knowledge_entries', 'digest', 12000),
}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)


def content_hash(row, kind):
    return hashlib.sha256(_json({'title': row.get('title') or '',
        'content': row.get(TARGETS[kind][1]) or '', 'status': row.get('status') or ''}).encode()).hexdigest()


def _target(kind):
    if kind not in TARGETS:
        raise ValueError('只能修订经历记忆或知识笔记')
    return TARGETS[kind]


def validate_request(wanderer_id, payload, *, actor):
    """Pure request contract shared by writes and read-only release preflight."""
    if not isinstance(payload, dict):
        raise ValueError('修订请求须为对象')
    kind = str(payload.get('target_kind') or '')
    table, field, maximum = _target(kind)
    ident = int(payload.get('target_id') or 0)
    if ident <= 0:
        raise ValueError('缺少有效记忆编号')
    try:
        operation = str(uuid.UUID(str(payload.get('request_id') or '')))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError('request_id须为稳定的UUID，重试请保持同一编号') from exc
    text, title = str(payload.get('content') or '').strip(), str(payload.get('title') or '').strip()
    reason = str(payload.get('reason') or '').strip()
    if not 1 <= len(text) <= maximum or len(title) > 120 or not 1 <= len(reason) <= 600:
        raise ValueError(f'正文须1至{maximum}字，标题最多120字，修订原因须1至600字')
    actor_id = int(actor.get('id') or 0)
    actor_kind = 'administrator' if actor_id > 0 else str(actor.get('kind') or '')
    if actor_id <= 0 and actor_kind != 'release_operator':
        raise PermissionError('缺少可追溯的修订操作者')
    request_hash = hashlib.sha256(_json({'wid': int(wanderer_id), 'kind': kind, 'id': ident,
        'text': text, 'title': title, 'reason': reason, 'actor_id': actor_id, 'actor_kind': actor_kind,
        'expected_revision': payload.get('expected_revision'),
        'expected_content_hash': payload.get('expected_content_hash')}).encode()).hexdigest()
    return {'kind': kind, 'table': table, 'field': field, 'ident': ident, 'operation': operation,
            'text': text, 'title': title, 'reason': reason, 'actor_id': actor_id,
            'actor_kind': actor_kind, 'request_hash': request_hash}


def _revision(conn, kind, ident):
    if kind == 'episode':
        row = conn.execute('SELECT revision FROM wanderer_memory_sources WHERE memory_id=?', (ident,)).fetchone()
        return int(row['revision']) if row else 1
    return max(1, int(conn.execute('SELECT COALESCE(MAX(revision),0) AS n FROM wanderer_knowledge_revisions '
                                 'WHERE entry_id=?', (ident,)).fetchone()['n']))


def _snapshot(row, kind, revision):
    return {'title': row.get('title') or '', 'content': row.get(TARGETS[kind][1]) or '',
            'status': row.get('status') or '', 'content_hash': content_hash(row, kind), 'revision': revision}


def annotate_items(conn, wanderer_id, kind, items):
    """A constant number of bounded queries for the administrator's current page."""
    if not items:
        return items
    _target(kind)
    ensure_wanderer_runtime_schema(conn)
    ids = list(dict.fromkeys(int(item['id']) for item in items))
    marks = ','.join('?' for _ in ids)
    if kind == 'episode':
        rows = conn.execute(f'SELECT memory_id AS id,revision FROM wanderer_memory_sources WHERE wanderer_id=? '
                            f'AND memory_id IN ({marks})', (int(wanderer_id), *ids)).fetchall()
    else:
        rows = conn.execute(f'SELECT entry_id AS id,MAX(revision) AS revision FROM wanderer_knowledge_revisions '
                            f'WHERE wanderer_id=? AND entry_id IN ({marks}) GROUP BY entry_id',
                            (int(wanderer_id), *ids)).fetchall()
    versions = {int(row['id']): int(row['revision']) for row in rows}
    corrections = conn.execute(f'SELECT c.* FROM wanderer_memory_corrections c WHERE wanderer_id=? AND target_kind=? '
        f'AND target_id IN ({marks}) AND revision=(SELECT MAX(n.revision) FROM wanderer_memory_corrections n '
        'WHERE n.wanderer_id=c.wanderer_id AND n.target_kind=c.target_kind AND n.target_id=c.target_id)',
        (int(wanderer_id), kind, *ids)).fetchall()
    latest = {int(row['target_id']): dict(row) for row in corrections}
    for item in items:
        item.update(editor_title=item.get('title') or '', editor_content=item.get(TARGETS[kind][1]) or '',
                    content_hash=content_hash(item, kind), correction_revision=versions.get(int(item['id']), 1))
        last = latest.get(int(item['id']))
        item['correction'] = ({key: last[key] for key in ('id', 'revision', 'reason', 'created_at', 'actor_id', 'actor_kind')}
                              if last else None)
    return items


def history(conn, wanderer_id, kind, target_id, *, before=None, limit=30):
    table, _, _ = _target(kind)
    ensure_wanderer_runtime_schema(conn)
    if not conn.execute(f'SELECT 1 FROM {table} WHERE id=? AND wanderer_id=?',
                        (int(target_id), int(wanderer_id))).fetchone():
        raise LookupError('这条记忆不属于该散修或已不存在')
    size = max(1, min(100, int(limit)))
    clause, args = '', [int(wanderer_id), kind, int(target_id)]
    if before is not None:
        clause = ' AND revision<?'
        args.append(int(before))
    rows = conn.execute('SELECT * FROM wanderer_memory_corrections WHERE wanderer_id=? AND target_kind=? '
        'AND target_id=?' + clause + ' ORDER BY revision DESC LIMIT ?', (*args, size + 1)).fetchall()
    items = []
    for row in rows[:size]:
        item = dict(row)
        item['before'] = json.loads(item.pop('before_json'))
        item['after'] = json.loads(item.pop('after_json'))
        item.pop('request_hash', None)
        items.append(item)
    return {'items': items, 'next_cursor': items[-1]['revision'] if len(rows) > size else None}


def correct(conn, wanderer_id, payload, *, actor):
    from .wanderer_admin_service import require_wanderer
    from .wanderer_resource_service import lock_actor_resources, resize_capacity
    ensure_wanderer_runtime_schema(conn)
    w = require_wanderer(conn, wanderer_id)
    request = validate_request(wanderer_id, payload, actor=actor)
    kind, table, field, ident = (request[key] for key in ('kind', 'table', 'field', 'ident'))
    operation, request_hash = request['operation'], request['request_hash']
    text, title, reason = (request[key] for key in ('text', 'title', 'reason'))
    actor_id, actor_kind = request['actor_id'], request['actor_kind']
    lock_actor_resources(conn, int(wanderer_id))
    previous = conn.execute('SELECT * FROM wanderer_memory_corrections WHERE id=?', (operation,)).fetchone()
    if previous:
        if previous['request_hash'] != request_hash:
            raise HTTPException(409, '修订请求编号已用于另一份内容')
        current = conn.execute(f'SELECT * FROM {table} WHERE id=? AND wanderer_id=?',
                               (ident, int(wanderer_id))).fetchone()
        item = dict(current) if current else None
        if item:
            annotate_items(conn, int(wanderer_id), kind, [item])
        correction = {key: previous[key] for key in
                      ('id', 'revision', 'reason', 'created_at', 'actor_id', 'actor_kind')}
        correction.update(before=json.loads(previous['before_json']), after=json.loads(previous['after_json']))
        return {'correction': correction, 'replayed': True, 'item': item}
    row = conn.execute(f'SELECT * FROM {table} WHERE id=? AND wanderer_id=?',
                       (ident, int(wanderer_id))).fetchone()
    if row is None:
        raise LookupError('这条记忆不属于该散修或已不存在')
    row = dict(row)
    revision = _revision(conn, kind, ident)
    if (int(payload.get('expected_revision') or 0) != revision
            or str(payload.get('expected_content_hash') or '') != content_hash(row, kind)):
        raise HTTPException(409, '记忆已被修改，请保留草稿并重新读取当前版本')
    if row.get('status') == 'superseded':
        raise HTTPException(409, '该记忆已有新版本，请修订当前有效记忆')
    before = _snapshot(row, kind, revision)
    before['record'] = dict(row)
    stamp = local_iso()
    # Preserve existing capacity ownership; administrator maintenance never
    # creates learning credit or silently evicts another memory to make space.
    resource = 'memory_chars' if kind == 'episode' else 'knowledge_chars'
    object_key = f'memory:{ident}' if kind == 'episode' else f'knowledge:{ident}'
    allocation = conn.execute('SELECT 1 FROM wanderer_capacity_allocations WHERE actor_id=? AND resource=? AND object_key=?',
                              (int(wanderer_id), resource, object_key)).fetchone()
    if allocation and not resize_capacity(conn, int(wanderer_id), resource, object_key, len(text))['ok']:
        raise HTTPException(409, '修订后超过现有记忆容量，请缩短正文；原文与其他记忆均已保留')
    if kind == 'episode':
        source = conn.execute('SELECT * FROM wanderer_memory_sources WHERE memory_id=?', (ident,)).fetchone()
        before['source'] = dict(source) if source else None
        if source:
            conn.execute("UPDATE wanderer_memory_sources SET revision=?,author_kind='admin_correction',updated_at=? WHERE memory_id=?",
                         (revision + 1, stamp, ident))
        else:
            # Unknown historical provenance remains private; an administrator
            # cannot turn a private conversation into public memory by editing.
            conn.execute('INSERT INTO wanderer_memory_sources(memory_id,wanderer_id,command_key,author_kind,origin_author,turn_id,'
                'revision,visibility,scope_json,evidence_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (ident, int(wanderer_id), 'admin-memory:' + operation, 'admin_correction', int(wanderer_id), '',
                 revision + 1, 'private', '{"kind":"private"}', '[]', stamp, stamp))
        conn.execute('UPDATE wanderer_episodic_memories SET title=?,content=? WHERE id=? AND wanderer_id=?',
                     (title, text, ident, int(wanderer_id)))
    else:
        from .wanderer_knowledge_service import _rewarded_chars
        learned_watermark = max(_rewarded_chars(conn, row), len(text))
        if not conn.execute('SELECT 1 FROM wanderer_knowledge_revisions WHERE entry_id=?', (ident,)).fetchone():
            conn.execute('INSERT INTO wanderer_knowledge_revisions(entry_id,revision,wanderer_id,turn_id,record_json,created_at) VALUES(?,1,?,?,?,?)',
                         (ident, int(wanderer_id), 'admin:' + operation, _json(row), stamp))
        conn.execute('UPDATE wanderer_knowledge_entries SET title=?,digest=?,chars=?,updated_at=? WHERE id=? AND wanderer_id=?',
                     (title, text, len(text), stamp, ident, int(wanderer_id)))
        updated = dict(conn.execute('SELECT * FROM wanderer_knowledge_entries WHERE id=?', (ident,)).fetchone())
        conn.execute('INSERT INTO wanderer_knowledge_revisions(entry_id,revision,wanderer_id,turn_id,record_json,created_at) VALUES(?,?,?,?,?,?)',
                     (ident, revision + 1, int(wanderer_id), 'admin:' + operation, _json(updated), stamp))
        conn.execute('INSERT INTO wanderer_knowledge_growth(entry_id,wanderer_id,rewarded_chars,updated_at) VALUES(?,?,?,?) '
                     'ON CONFLICT(entry_id) DO UPDATE SET rewarded_chars=excluded.rewarded_chars,updated_at=excluded.updated_at',
                     (ident, int(wanderer_id), learned_watermark, stamp))
        conn.execute("UPDATE wanderer_agents SET knowledge_chars=(SELECT COALESCE(SUM(chars),0) FROM wanderer_knowledge_entries "
                     "WHERE wanderer_id=? AND status='active') WHERE id=?", (int(wanderer_id), int(wanderer_id)))
    after = _snapshot({**row, 'title': title, field: text}, kind, revision + 1)
    conn.execute('INSERT INTO wanderer_memory_corrections(id,wanderer_id,target_kind,target_id,revision,actor_id,actor_kind,reason,'
        'request_hash,before_json,after_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
        (operation, int(wanderer_id), kind, ident, revision + 1, actor_id, actor_kind, reason,
         request_hash, _json(before), _json(after), stamp))
    item = dict(conn.execute(f'SELECT * FROM {table} WHERE id=?', (ident,)).fetchone())
    annotate_items(conn, int(wanderer_id), kind, [item])
    from .wanderer_identity_service import invalidate_wanderer_cache
    invalidate_wanderer_cache([w['student_id']])
    return {'correction': {'id': operation, 'revision': revision + 1, 'reason': reason, 'created_at': stamp,
                          'actor_id': actor_id, 'actor_kind': actor_kind, 'before': before, 'after': after},
            'item': item, 'replayed': False}
