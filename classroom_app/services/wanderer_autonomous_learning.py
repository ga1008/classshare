"""Autonomous reading adapters and source-bound knowledge; reuse the original ACL."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from ..db.schema_wanderer_runtime import ensure_wanderer_runtime_schema
from ..time_utils import local_iso
from .wanderer_identity_service import build_wanderer_user_dict


def source_access(conn: Any, reader: dict, evidence: dict, *, public=False) -> bool:
    """Recheck the original resource on every recall, including withdrawal."""
    kind = evidence.get('kind')
    if kind in {'world_chapter', 'self_observation', 'public_receipt'}:
        return True
    user = build_wanderer_user_dict(reader)
    try:
        if kind == 'material':
            from .materials_service import ensure_user_material_access
            item = dict(ensure_user_material_access(conn, int(evidence['id']), user))
            root_id = int(item.get('root_id') or item['id'])
            root = conn.execute('SELECT * FROM course_materials WHERE id=?', (root_id,)).fetchone()
            return not public or bool(root and root['scope_level'] == 'public' and str(root['student_visible']).lower() in {'1','true'})
        if kind == 'blog_post':
            from .blog_service import _can_view_post
            row = conn.execute('SELECT * FROM blog_posts WHERE id=?', (int(evidence['id']),)).fetchone()
            return bool(row and _can_view_post(conn, user, dict(row)) and (not public or row['visibility'] == 'public'))
    except Exception as exc:
        from fastapi import HTTPException
        if not isinstance(exc, (HTTPException, KeyError, ValueError, PermissionError)):
            raise
    return False


def read_material(conn, reader, *, material_id, offset=0, limit=1):
    from .materials_service import ensure_user_material_access
    item = dict(ensure_user_material_access(conn, int(material_id), build_wanderer_user_dict(reader)))
    offset, limit = max(0, int(offset)), max(1, min(10, int(limit)))
    rows = conn.execute('SELECT ordinal,page_number,locator_label,heading,text FROM material_search_chunks '
                        'WHERE material_id=? ORDER BY ordinal LIMIT ? OFFSET ?',
                        (int(material_id), limit+1, offset)).fetchall()
    chunks = [{**dict(row), 'text':str(row['text'])[:10000]} for row in rows[:limit]]
    if not chunks:
        return {'material_id':int(material_id),'chunks':[], 'next_offset':None,
                'notice':'当前材料没有可读文本索引；空结果不算已研读，可使用平台文件读取能力查看原件。'}
    digest = hashlib.sha256(json.dumps(chunks,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    document=conn.execute('SELECT file_hash FROM material_search_documents WHERE material_id=?',(int(material_id),)).fetchone()
    if document and item.get('file_hash') and document['file_hash']!=item['file_hash']:
        return {'material_id':int(material_id),'chunks':[],'next_offset':None,
                'notice':'材料已更新，文本索引尚未追上；请稍后重读或读取授权原件，旧索引不算本次研读。'}
    version=str(item.get('file_hash') or (document['file_hash'] if document else '')
                or hashlib.sha256(str(chunks[0]['text']).encode()).hexdigest())
    evidence = {'ref':f'material:{material_id}:{offset}:{digest}', 'kind':'material',
                'id':int(material_id),'source_ref':f"{material_id}:{version}:{chunks[0]['ordinal']}", 'hash':digest,
                'title':item.get('name') or item.get('material_name') or '学习材料'}
    return {'material_id':int(material_id), 'title':evidence['title'], 'chunks':chunks,
            'next_offset':offset+limit if len(rows)>limit else None, 'evidence':evidence,
            'scope':{'kind':'public' if source_access(conn,reader,evidence,public=True) else 'private'}}


def remember(conn, reader, args, *, turn_id, scope, available_evidence):
    from .wanderer_knowledge_service import add_knowledge
    ensure_wanderer_runtime_schema(conn)
    evidence = next((item for item in available_evidence if item.get('ref')==args.get('evidence_ref')),None)
    if not evidence or not source_access(conn,reader,evidence):
        raise ValueError('必须先实际研读仍有权限的资料，再引用阅读回执编写知识')
    kind = {'world_chapter':'world','material':'material_page','blog_post':'blog_post'}.get(evidence['kind'])
    if not kind:
        raise ValueError('该回执属于经历，不是可学习资料；请使用memory.write')
    digest = str(args.get('digest') or '').strip()
    if not digest:
        raise ValueError('请编写自己的理解摘要')
    source_ref=(f"{evidence['id']}:{evidence.get('hash') or evidence.get('version')}" if kind=='world'
                else str(evidence.get('source_ref') or evidence['ref']))
    from .wanderer_resource_service import lock_actor_resources
    lock_actor_resources(conn,int(reader['id']))
    old=conn.execute('SELECT k.*,s.scope_json,s.evidence_json FROM wanderer_knowledge_entries k '
        'LEFT JOIN wanderer_knowledge_sources s ON s.entry_id=k.id WHERE k.wanderer_id=? AND k.source_kind=? AND k.source_ref=?',
        (int(reader['id']),kind,source_ref)).fetchone()
    if old and not conn.execute('SELECT 1 FROM wanderer_knowledge_revisions WHERE entry_id=?',(old['id'],)).fetchone():
        conn.execute('INSERT INTO wanderer_knowledge_revisions(entry_id,revision,wanderer_id,turn_id,record_json,created_at) VALUES(?,1,?,?,?,?)',
            (old['id'],int(reader['id']),turn_id,json.dumps(dict(old),ensure_ascii=False),local_iso()))
    result = add_knowledge(conn, reader, source_kind=kind,
            source_ref=source_ref,
            title=str(args.get('topic') or evidence.get('title') or '研读笔记'),
            digest=digest, tags=args.get('tags') or [])
    # A private thought cannot turn a quotation into public knowledge by citing
    # a public item. The entire thought's scope is inherited.
    now = local_iso()
    from .wanderer_autobiography_service import inherited_evidence
    provenance=inherited_evidence([evidence],available_evidence)
    # Updating one's existing note must not create a reference to itself.
    # Its underlying sources already travel with every read of that note.
    provenance=[item for item in provenance if not (item.get('kind')=='knowledge_record'
                and str(item.get('id'))==str(result['entry_id']))]
    from .wanderer_autobiography_service import personal_source_scope,acquired_evidence
    provenance=acquired_evidence(conn,reader,provenance)
    scope=personal_source_scope(conn,reader,scope,provenance)
    conn.execute('INSERT INTO wanderer_knowledge_sources(entry_id,wanderer_id,turn_id,scope_json,evidence_json,created_at,updated_at) '
                 'VALUES(?,?,?,?,?,?,?) ON CONFLICT(entry_id) DO UPDATE SET turn_id=excluded.turn_id,'
                 'scope_json=excluded.scope_json,evidence_json=excluded.evidence_json,updated_at=excluded.updated_at',
                 (result['entry_id'],int(reader['id']),turn_id,json.dumps(scope),json.dumps(provenance,ensure_ascii=False),now,now))
    current=conn.execute('SELECT k.*,s.scope_json,s.evidence_json FROM wanderer_knowledge_entries k '
        'JOIN wanderer_knowledge_sources s ON s.entry_id=k.id WHERE k.id=?',(result['entry_id'],)).fetchone()
    revision=conn.execute('SELECT COALESCE(MAX(revision),0)+1 AS n FROM wanderer_knowledge_revisions WHERE entry_id=?',
                          (result['entry_id'],)).fetchone()['n']
    conn.execute('INSERT INTO wanderer_knowledge_revisions(entry_id,revision,wanderer_id,turn_id,record_json,created_at) VALUES(?,?,?,?,?,?)',
        (result['entry_id'],revision,int(reader['id']),turn_id,json.dumps(dict(current),ensure_ascii=False),now))
    result['revision']=revision
    return result


def read_knowledge(conn, reader, *, scope, before=None, limit=20, query='', include_shared=True):
    from .wanderer_autobiography_service import can_read_scope, decode
    from .wanderer_bond_service import direct_partners
    ensure_wanderer_runtime_schema(conn)
    owners = [int(reader['id'])] + (direct_partners(conn,int(reader['id'])) if include_shared else [])
    clauses = [f"k.wanderer_id IN ({','.join('?' for _ in owners)})", "k.status='active'"]
    params = list(owners)
    if before:
        clauses.append('k.id<?')
        params.append(int(before))
    if query:
        clauses.append("(k.digest LIKE ? ESCAPE '!' OR k.title LIKE ? ESCAPE '!')")
        term='%'+str(query)[:120].replace('!','!!').replace('%','!%').replace('_','!_')+'%'
        params.extend([term,term])
    cap = max(1,min(100,int(limit)))
    rows = conn.execute("SELECT k.*,s.scope_json,s.evidence_json,a.display_name AS origin_name,"
        "(SELECT MAX(created_at) FROM wanderer_memory_corrections c WHERE c.wanderer_id=k.wanderer_id "
        "AND c.target_kind='knowledge' AND c.target_id=k.id) AS corrected_at FROM wanderer_knowledge_entries k "
        'LEFT JOIN wanderer_knowledge_sources s ON s.entry_id=k.id JOIN wanderer_agents a ON a.id=k.wanderer_id WHERE '
        +' AND '.join(clauses)+' ORDER BY k.id DESC LIMIT ?',(*params,cap+1)).fetchall()
    items=[]
    for row in rows[:cap]:
        item=dict(row)
        own=int(item['wanderer_id'])==int(reader['id'])
        evidence=decode(item.get('evidence_json'),[])
        source_scope=decode(item.get('scope_json'),None)
        # Legacy unknown/seed knowledge is private to its original owner. Known
        # legacy materials still use the ordinary reader's current ACL.
        if source_scope is None:
            source_scope={'kind':'private'}
            kind={'world':'world_chapter','material_page':'material','material':'material','blog_post':'blog_post'}.get(item['source_kind'])
            if kind:
                try:
                    ref={'kind':kind,'id':int(str(item['source_ref']).split(':')[0])} if kind!='world_chapter' else {'kind':kind}
                    if source_access(conn,reader,ref,public=True):
                        source_scope={'kind':'public'}
                        evidence=[ref]
                except ValueError:
                    pass
        if not can_read_scope(conn,reader,source_scope,scope,own=own):
            continue
        from .wanderer_autobiography_service import _evidence_live
        if not _evidence_live(conn,evidence,public=scope.get('kind')=='public',reader=reader):
            continue
        if source_scope.get('kind')!='public':
            evidence=evidence+[{'kind':'knowledge_record','id':int(item['id']),'owner_id':int(item['wanderer_id']),
                               'ref':f"knowledge_record:{item['id']}"}]
        items.append({k:item[k] for k in ('id','source_kind','source_ref','title','digest','chars','origin_name')} |
                     {'origin_author':int(item['wanderer_id']),'shared':not own,'evidence':evidence,
                      'corrected_at':item.get('corrected_at')})
    return {'items':items,'next_cursor':int(rows[cap-1]['id']) if len(rows)>cap else None}


def archive_knowledge(conn, reader, entry_id):
    from .wanderer_resource_service import lock_actor_resources, release_capacity
    lock_actor_resources(conn,int(reader['id']))
    row=conn.execute('SELECT id FROM wanderer_knowledge_entries WHERE id=? AND wanderer_id=?',
                     (int(entry_id),int(reader['id']))).fetchone()
    if row is None:
        raise PermissionError('只能整理自己的知识')
    conn.execute("UPDATE wanderer_knowledge_entries SET status='forgotten',updated_at=? WHERE id=?",(local_iso(),int(entry_id)))
    release_capacity(conn,int(reader['id']),'knowledge_chars',f'knowledge:{int(entry_id)}')
    conn.execute("UPDATE wanderer_agents SET knowledge_chars=(SELECT COALESCE(SUM(chars),0) FROM wanderer_knowledge_entries "
                 "WHERE wanderer_id=? AND status='active') WHERE id=?",(int(reader['id']),int(reader['id'])))
    return {'entry_id':int(entry_id),'archived':True,'original_retained':True}


def recall_knowledge(conn, reader, *, scope, budget=4000, evidence_sink=None):
    page=read_knowledge(conn,reader,scope=scope,limit=40)
    lines=['【我已记住的知识与直接道侣的可共享知识】仅来源资料，作者与学习者不混同。']
    selected=[]
    used=sum(map(len,lines))
    for item in page['items']:
        correction=f"（管理员校正于{item['corrected_at']}）" if item.get('corrected_at') else ''
        line=f"#{item['id']} 作者{item['origin_name']}{correction}｜{item['title']}：{item['digest']}"
        if used+len(line)>budget:
            continue
        selected.append((item,line))
        used+=len(line)
    for item,line in reversed(selected):
        lines.append(line)
        if evidence_sink is not None:
            evidence_sink.extend(item.get('evidence') or [])
    lines.append('完整笔记可用knowledge.read分页检索；共享只涉及直接关系，不传递权限或修为。')
    return '\n'.join(lines)
