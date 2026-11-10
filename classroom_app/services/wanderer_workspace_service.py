"""Paginated management projection. Callers must enforce wanderer-admin rights.

Reading here never advances a conversation cursor, wakes a model or consumes a
world resource. Full retained records are available without impersonating the AI.
"""
from __future__ import annotations

import json
from typing import Any

from .wanderer_admin_service import require_wanderer, social_conversations, _identity_names
from .wanderer_autobiography_service import timeline
from .wanderer_runtime_service import status_snapshot

RECORDS = {
    'turns': ('wanderer_turns','wanderer_id','created_at,turn_id'),
    'events': ('wanderer_wake_events','wanderer_id','created_at,event_id'),
    'actions': ('wanderer_action_log','wanderer_id','id'),
    'reviews': ('wanderer_dao_reviews','wanderer_id','id'),
    'knowledge': ('wanderer_knowledge_entries','wanderer_id','id'),
    'reflections': ('wanderer_daily_reflections','wanderer_id','id'),
    'reflection_sessions': ('wanderer_reflection_sessions','wanderer_id','reflection_date'),
    'reflection_dispatches': ('wanderer_reflection_dispatches','wanderer_id','dispatched_at,turn_id'),
    'affinity_ratings': ('wanderer_affinity_ratings','wanderer_id','reflection_date,peer_identity'),
    'affinity_rating_operations': ('wanderer_affinity_rating_operations','wanderer_id','created_at,operation_id'),
    'message_deliveries': ('wanderer_message_deliveries','wanderer_id','created_at,operation_id'),
    'memory_corrections': ('wanderer_memory_corrections','wanderer_id','created_at,id'),
    'threads': ('wanderer_memory_threads','wanderer_id','id'),
    'chapters': ('wanderer_memory_chapters','wanderer_id','id'),
    'todos': ('wanderer_todos','wanderer_id','id'),
    'relationships': ('wanderer_relationships','wanderer_id','id'),
    'relationship_changes': ('wanderer_relationship_changes','wanderer_id','created_at,operation_id'),
    'resource_usage': ('wanderer_resource_usage','owner_id','budget_date,resource'),
    'resource_reservations': ('wanderer_resource_reservations',None,'created_at,operation_id'),
    'capacity': ('wanderer_capacity_allocations',None,'resource,object_key'),
    'life_ledger': ('wanderer_life_ledger','owner_id','created_at,source_key'),
    'penalties': ('wanderer_penalty_allocations','beneficiary_id','created_at,root_id,component'),
    'debts': ('wanderer_penalty_debts','wanderer_id','component'),
    'cultivation': ('wanderer_cultivation_awards','wanderer_id','created_at,event_id'),
    'legacy': ('wanderer_legacies','wanderer_id','id'),
    'knowledge_sources': ('wanderer_knowledge_sources','wanderer_id','entry_id'),
    'memory_sources': ('wanderer_memory_sources','wanderer_id','memory_id'),
    'admin_access': ('wanderer_admin_access','wanderer_id','created_at,id'),
    'todo_evidence': ('wanderer_todo_evidence','wanderer_id','created_at,todo_id'),
    'self_state_sources': ('wanderer_self_state_sources','wanderer_id','updated_at'),
    'knowledge_revisions': ('wanderer_knowledge_revisions','wanderer_id','created_at,entry_id,revision'),
    'evidence_snapshots': ('wanderer_evidence_snapshots','wanderer_id','created_at,id'),
    'private_message_operations': ('wanderer_private_message_operations','wanderer_id','created_at,operation_id'),
    'knowledge_growth': ('wanderer_knowledge_growth','wanderer_id','updated_at,entry_id'),
    'episodes': ('wanderer_episodic_memories','wanderer_id','occurred_at,id'),
    'experience_notes': ('wanderer_experience_notes','wanderer_id','id'),
    'social_inbox': ('wanderer_social_inbox','wanderer_id','created_at,id'),
    'relationship_versions': ('wanderer_relationship_versions','wanderer_id','updated_at,peer_identity'),
    'group_observations': ('wanderer_group_observations','wanderer_id','updated_at,group_id'),
    'runtime_leases': ('wanderer_runtime_leases','wanderer_id','lease_until,turn_id'),
    'commands': ('wanderer_commands',None,'created_at,operation_id'),
    'bonds': ('wanderer_bonds',None,'created_at,id'),
    'bond_requests': ('wanderer_bond_requests',None,'created_at,id'),
    'gain_debt_payments': ('wanderer_gain_debt_payments','owner_id','created_at,gain_key,component'),
    'talent_awards': ('wanderer_talent_awards','owner_id','created_at,event_id'),
    'penalty_roots': ('wanderer_penalty_roots',None,'created_at,id'),
    'penalty_reversals': ('wanderer_penalty_reversals',None,'created_at,root_id'),
    'dao_history_blocks': ('wanderer_dao_history_blocks',None,'created_at,block_id'),
}

# Reuse the adjudicator's participant/beneficiary association rules. History
# blocks are reached through their request; they cannot belong to the manifest
# itself, which would make its review recursively create new evidence.
RELATED_RECORDS = {
    'resource_reservations': ('wanderer_resource_reservations', ('actor_id','lines_json'), None),
    'capacity': ('wanderer_capacity_allocations', ('actor_id','lines_json'), None),
    'commands': ('wanderer_commands', (), None),
    'bonds': ('wanderer_bonds', ('first_id','second_id'), None),
    'bond_requests': ('wanderer_bond_requests', ('first_id','second_id'), None),
    'penalty_roots': ('wanderer_penalty_roots', ('offender_id',), None),
    'penalty_reversals': ('wanderer_penalty_reversals', (), None),
    'dao_history_blocks': ('wanderer_bond_requests', ('first_id','second_id'), ('request_id','id')),
}


def _record_scope(wid, kind):
    table,owner,_sort=RECORDS[kind]
    if owner is not None:
        return f'{owner}=?',[wid]
    from .heavenly_dao_bond_service import _condition
    related,columns,via=RELATED_RECORDS[kind]
    condition,params=_condition(related,set(columns),(wid,wid),(0,0))
    if not condition:
        raise ValueError('当前分类没有明确的散修归属')
    if via:
        foreign,primary=via
        condition=f'{foreign} IN (SELECT {primary} FROM {related} WHERE {condition})'
    return f'({condition})',list(params)


def bond_projection(conn, wanderer_id: int, *, admin=False):
    from .wanderer_bond_service import list_bonds
    from .wanderer_admin_service import avatar_url
    rows = list_bonds(conn,wanderer_id)
    ids = {int(row['second_id'] if int(row['first_id'])==int(wanderer_id) else row['first_id']) for row in rows}
    peers = {int(r['id']):dict(r) for r in conn.execute('SELECT id,student_id,display_name,slug FROM wanderer_agents '
             f"WHERE id IN ({','.join('?' for _ in ids)})",tuple(ids)).fetchall()} if ids else {}
    result=[]
    for row in rows:
        peer_id = int(row['second_id'] if int(row['first_id'])==int(wanderer_id) else row['first_id'])
        peer = peers.get(peer_id,{})
        base = dict(row) if admin else {key:row.get(key) for key in ('id','first_id','second_id','state','created_at')}
        result.append({**base,'peer_id':peer_id,'peer_name':peer.get('display_name') or '道侣',
                       'peer_student_id':peer.get('student_id'),'peer_avatar_url':avatar_url(peer.get('student_id')),
                       'peer_url':'/wanderers/'+peer.get('slug','')})
    return result


def resource_projection(conn, wanderer_id: int):
    from .wanderer_resource_service import limits
    resources=limits(conn,wanderer_id)
    ids={int(owner['owner_id']) for item in resources.values() for owner in item.get('owners',[])}
    names={int(r['id']):r['display_name'] for r in conn.execute('SELECT id,display_name FROM wanderer_agents '
           f"WHERE id IN ({','.join('?' for _ in ids)})",tuple(ids)).fetchall()} if ids else {}
    for item in resources.values():
        for owner in item.get('owners',[]):
            owner['owner_name']=names.get(int(owner['owner_id']),'散修')
    return resources


def _record(row):
    protected={'password','hashed_password','secret','api_key','access_token','refresh_token',
               'review_token','lease_token','reasoning_content','reasoning_text','thinking'}
    def sanitize(value):
        if isinstance(value,dict):
            return {key:sanitize(part) for key,part in value.items() if key.lower() not in protected}
        if isinstance(value,list):
            return [sanitize(part) for part in value]
        return value
    item = sanitize(dict(row))
    for key, value in list(item.items()):
        if key.endswith('_json'):
            try:
                decoded=json.loads(value or '{}')
                item[key[:-5]] = sanitize(decoded)
                if item[key[:-5]] != decoded:
                    item[key]=json.dumps(item[key[:-5]],ensure_ascii=False)
            except (TypeError,ValueError):
                pass
    return item


def _records(conn, wid, kind, offset, limit, query=''):
    if kind not in RECORDS:
        raise ValueError('未知数据分区')
    table, owner, sort = RECORDS[kind]
    condition,params=_record_scope(wid,kind)
    clauses = [condition]
    # Search only documented text columns, never a model-provided SQL column.
    search = {'actions':'content_preview','reviews':'reason','knowledge':'digest','todos':'title',
              'relationships':'peer_identity','relationship_changes':'reason','turns':'prompt_json',
              'events':'context_json','reflections':'output_json','threads':'summary',
              'chapters':'summary','resource_reservations':'operation_id','life_ledger':'detail_json',
              'penalties':'root_id','debts':'component','cultivation':'event_id',
              'episodes':'content','experience_notes':'content','social_inbox':'conversation_key',
              'relationship_versions':'peer_identity','commands':'command_json',
              'bonds':'reason','bond_requests':'reason','gain_debt_payments':'gain_key',
              'talent_awards':'result_json','penalty_roots':'reason','penalty_reversals':'reason',
              'dao_history_blocks':'content_json','memory_corrections':'reason',
              'affinity_ratings':'peer_identity','message_deliveries':'conversation_key',
              'reflection_sessions':'reflection_date','reflection_dispatches':'event_id',
              'affinity_rating_operations':'peer_identity'}.get(kind)
    if query and not search:
        raise ValueError('当前分类不支持全文检索，请清空查询或切换分类')
    if query and search:
        clauses.append(f"{search} LIKE ? ESCAPE '!'")
        params.append('%'+query.replace('!','!!').replace('%','!%').replace('_','!_')+'%')
    where = ' AND '.join(clauses)
    total = int(conn.execute(f'SELECT COUNT(*) AS n FROM {table} WHERE {where}',params).fetchone()['n'])
    ordering = ','.join(column+' DESC' for column in sort.split(','))
    rows = conn.execute(f'SELECT * FROM {table} WHERE {where} ORDER BY {ordering} LIMIT ? OFFSET ?',
                        (*params,limit,offset)).fetchall()
    items = [_record(row) for row in rows]
    if kind == 'turns':
        identifiers=[item['turn_id'] for item in items]
        commands=conn.execute(f"SELECT * FROM wanderer_commands WHERE turn_id IN ({','.join('?' for _ in identifiers)}) "
                              'ORDER BY created_at,command_id',identifiers).fetchall() if identifiers else []
        grouped={key:[] for key in identifiers}
        for command in commands:
            grouped[command['turn_id']].append(_record(command))
        for item in items:
            item['commands']=grouped[item['turn_id']]
    return {'items':items,'total':total,'next_cursor':offset+limit if offset+limit<total else None}


def _record_counts(conn,wid):
    queries,params=[],[]
    for kind,(table,_owner,_sort) in RECORDS.items():
        condition,values=_record_scope(wid,kind)
        queries.append(f"SELECT '{kind}' AS kind,COUNT(*) AS n FROM {table} WHERE {condition}")
        params.extend(values)
    return {row['kind']:int(row['n']) for row in conn.execute(' UNION ALL '.join(queries),params).fetchall()}


def workspace_page(conn: Any, wanderer_id: int, *, section='overview', cursor=None, limit=30, q='', kind='') -> dict:
    w = require_wanderer(conn,wanderer_id)
    wid, size, offset = int(w['id']), max(1,min(100,int(limit))), max(0,int(cursor or 0))
    query = str(q or '')[:120]
    from .wanderer_bond_service import list_bonds
    from .wanderer_resource_service import limits
    result = {'section':section,'items':[],'next_cursor':None,'total':0,'summary':{}}
    if section == 'overview':
        result['summary'] = {'status':status_snapshot(conn,w,include_relationships=True),'resources':resource_projection(conn,wid),
                             'bonds':bond_projection(conn,wid,admin=True),
                             'counts':_record_counts(conn,wid)}
        result.update(_records(conn,wid,'turns',offset,min(size,10)))
    elif section == 'memory':
        if kind and kind != 'episodes':
            if kind not in {'knowledge','threads','chapters','reflections'}:
                raise ValueError('未知记忆分类')
            result.update(_records(conn,wid,kind,offset,size,query))
        else:
            result.update(timeline(conn,w,viewer='admin',before=offset or None,limit=size,query=query))
            result['total'] = int(conn.execute('SELECT COUNT(*) AS n FROM wanderer_episodic_memories WHERE wanderer_id=?',(wid,)).fetchone()['n'])
        result['summary']={'kinds':['episodes','knowledge','threads','chapters','reflections']}
        if not kind or kind in {'episodes','knowledge'}:
            from .wanderer_memory_correction_service import annotate_items
            annotate_items(conn, wid, 'knowledge' if kind == 'knowledge' else 'episode', result['items'])
    elif section == 'conversations':
        page = social_conversations(conn,wid,page=offset//size+1,limit=size,q=query)
        result.update(page)
        result['next_cursor'] = offset+size if offset+size < page['total'] else None
        result['summary'] = {'viewer_identity':page['viewer_identity']}
    elif section == 'relations':
        if kind in {'outgoing','incoming'}:
            from .wanderer_relationship_service import relationship_rankings
            page = relationship_rankings(conn,wid,direction=kind,offset=offset,limit=size,query=query)
            result.update(items=page['items'],total=page['total'],next_cursor=page['next_offset'])
            result['summary'] = {'direction':kind,'rules':page['rules'],'reflection':page['reflection'],
                                 'bonds':bond_projection(conn,wid,admin=True),
                                 'kinds':['outgoing','incoming','relationships','relationship_changes','friends','requests','groups','blocked','bond_requests']}
            return result
        if kind in {'friends','requests','groups','blocked','bond_requests'}:
            result.update(_relations(conn,w,kind,offset,size,query))
        else:
            result.update(_records(conn,wid,kind if kind in {'relationships','relationship_changes'} else 'relationships',offset,size,query))
        identities = [item['peer_identity'] for item in result['items'] if item.get('peer_identity')]
        people = _identity_names(conn,identities)
        for item in result['items']:
            item['name'] = (people.get(item.get('peer_identity')) or {}).get('name') or item.get('name') or item.get('peer_identity')
            if item.get('peer_identity'):
                version=conn.execute('SELECT revision FROM wanderer_relationship_versions WHERE wanderer_id=? AND peer_identity=?',
                                     (wid,item['peer_identity'])).fetchone()
                item['revision']=version['revision'] if version else 0
        result['summary']={'bonds':bond_projection(conn,wid,admin=True),
                           'kinds':['relationships','relationship_changes','friends','requests','groups','blocked','bond_requests']}
    elif section == 'activity':
        valid = ['turns','events','actions','reviews','todos']
        result.update(_records(conn,wid,kind if kind in valid else 'turns',offset,size,query))
        result['summary']={'kinds':valid}
    elif section == 'resources':
        valid = ['resource_usage','resource_reservations','capacity','life_ledger','penalties','debts','cultivation']
        result.update(_records(conn,wid,kind if kind in valid else 'resource_usage',offset,size))
        result['summary']={'resources':resource_projection(conn,wid),'status':status_snapshot(conn,w),'kinds':valid}
    elif section == 'identity':
        # Only AI profile data. Authentication credentials are never included.
        result['summary']={'profile':{key:value for key,value in w.items()
                            if not any(token in key.lower() for token in ('password','secret','token'))},
                           'record_kinds':list(RECORDS)}
        if kind:
            result.update(_records(conn,wid,kind,offset,size,query))
    else:
        raise ValueError('未知工作区分区')
    return result


def _relations(conn,w,kind,offset,limit,query):
    identity=f"student:{w['student_id']}"
    if kind in {'friends','requests'}:
        base='SELECT *,CASE WHEN identity_a=? THEN identity_b ELSE identity_a END AS peer_identity FROM social_friendships '
        where="WHERE (identity_a=? OR identity_b=?) AND status"+ ("='accepted'" if kind=='friends' else "<>'accepted'")
        sql=base+where
        args=[identity,identity,identity]
    elif kind=='groups':
        sql='SELECT g.*,m.role,m.joined_at,m.left_at FROM social_group_members m JOIN social_groups g ON g.id=m.group_id WHERE m.member_identity=?'
        args=[identity]
    elif kind=='blocked':
        sql="SELECT 'social' AS source,blocked_identity AS peer_identity,created_at FROM social_blocks WHERE owner_identity=? UNION ALL " \
            "SELECT 'legacy' AS source,blocked_identity AS peer_identity,created_at FROM private_message_blocks WHERE owner_identity=?"
        args=[identity,identity]
    else:
        sql='SELECT * FROM wanderer_bond_requests WHERE first_id=? OR second_id=?'
        args=[int(w['id']),int(w['id'])]
    if query:
        field='name' if kind=='groups' else 'reason' if kind=='bond_requests' else 'peer_identity'
        sql=f"SELECT * FROM ({sql}) relation_source WHERE {field} LIKE ? ESCAPE '!'"
        args.append('%'+query.replace('!','!!').replace('%','!%').replace('_','!_')+'%')
    total=int(conn.execute(f'SELECT COUNT(*) AS n FROM ({sql}) relation_count',args).fetchone()['n'])
    rows=conn.execute(f'SELECT * FROM ({sql}) relation_page ORDER BY created_at DESC LIMIT ? OFFSET ?',(*args,limit,offset)).fetchall()
    return {'items':[_record(row) for row in rows],'total':total,'next_cursor':offset+limit if offset+limit<total else None}


def audit_access(conn,wanderer_id,actor_id,section,selector=''):
    import uuid
    from ..time_utils import local_iso
    from ..db.schema_wanderer_runtime import ensure_wanderer_runtime_schema
    ensure_wanderer_runtime_schema(conn)
    conn.execute('INSERT INTO wanderer_admin_access(id,wanderer_id,actor_id,section,selector,created_at) VALUES(?,?,?,?,?,?)',
                 (uuid.uuid4().hex,int(wanderer_id),int(actor_id),str(section)[:80],str(selector)[:200],local_iso()))


def attachment(conn,wanderer_id,source,attachment_id):
    w=require_wanderer(conn,wanderer_id)
    identity=f"student:{w['student_id']}"
    if source=='social':
        from .social_media_service import load_media, resolve_media_file
        from .social_common import SocialError
        try:
            media=load_media(conn,int(attachment_id))
            key=media['conversation_key']
            if key.startswith('group:'):
                owns=conn.execute('SELECT 1 FROM social_group_members WHERE group_id=? AND member_identity=?',
                                   (int(key.split(':',1)[1]),identity)).fetchone()
            else:
                owns=conn.execute('SELECT 1 FROM social_dm_participants WHERE conversation_key=? AND identity=?',(key,identity)).fetchone()
            if not owns:
                raise PermissionError('附件不属于该散修的历史会话')
            return resolve_media_file(conn,media)
        except SocialError as exc:
            raise LookupError(str(exc)) from exc
    if source=='legacy':
        from .message_center_service import resolve_private_message_attachment_file_payload
        row=conn.execute('SELECT a.* FROM private_message_attachments a JOIN private_messages m ON m.id=a.message_id '
                         'WHERE a.id=? AND (m.sender_identity=? OR m.recipient_identity=?)',
                         (int(attachment_id),identity,identity)).fetchone()
        if not row:
            raise PermissionError('附件不属于该散修的历史会话')
        data=resolve_private_message_attachment_file_payload(row,'original')
        if not data:
            raise LookupError('历史附件字节已不再保留')
        return data
    raise ValueError('未知附件来源')
