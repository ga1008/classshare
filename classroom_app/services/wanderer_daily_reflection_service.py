"""Daily free thoughts use the ordinary runtime, with durable bounded entitlement.

The entitlement is registered only by the daily-reflection scheduler. A model's
source/context fields never grant it. Callers own all transaction boundaries.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP

from ..db.schema_wanderer_reflection import ensure_wanderer_reflection_schema
from ..time_utils import local_iso
from .social_common import SocialError, serialize_on

FREE_CALL_LIMIT = 3
RULE_VERSION = 'daily-affinity-v1'
GRADES = {'very_good': ('感觉非常好', 12), 'good': ('不错', 5), 'neutral': ('一般', 0),
          'bad': ('不太好', -5), 'averse': ('反感', -12)}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def rules():
    return {'version': RULE_VERSION, 'range': [0, 100], 'precision': 0.1,
            'grades': [{'value': key, 'label': label, 'base_delta': step} for key, (label, step) in GRADES.items()],
            'formula': '正向基础步长×(1-A/200)，负向基础步长×(0.5+A/200)；一般严格不变；四舍五入到0.1，限制在0至100。',
            'daily_policy': '同一好友每天一个有效评级；修改时从当日首次评分前的基线重算，不重复累加。旧分值不重置。'}


def calculate_affinity(baseline, grade):
    if not isinstance(grade, str) or grade not in GRADES:
        raise SocialError('请选择感觉非常好、不错、一般、不太好或反感。', 400)
    value = Decimal(str(baseline))
    if not value.is_finite() or not 0 <= value <= 100:
        raise SocialError('好感度基线无效。', 400)
    step = Decimal(GRADES[grade][1])
    if not step:
        return float(value)
    factor = Decimal(1) - value / 200 if step > 0 else Decimal('0.5') + value / 200
    return float(max(Decimal(0), min(Decimal(100), (value + step * factor).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP))))


def _friends(conn, wanderer):
    from .social_identity_service import friend_people, other_side
    me = f"student:{wanderer['student_id']}"
    rows, people = friend_people(conn, me)
    return [{'peer_identity': other_side(row, me), 'name': people[other_side(row, me)]['name']}
            for row in rows if other_side(row, me) in people]


def register_session(conn, wanderer, date, event):
    """Called by the trusted daily-reflection entry, not by general wake()."""
    ensure_wanderer_reflection_schema(conn)
    wid = int(wanderer['id'])
    if event['source'] != 'reflection' or event['event_key'] != date or int(event['wanderer_id']) != wid:
        raise PermissionError('每日自省来源不匹配')
    serialize_on(conn, 'wanderer-reflection-session', [f'{wid}:{date}'])
    now = local_iso()
    conn.execute('INSERT INTO wanderer_reflection_sessions(wanderer_id,reflection_date,root_event_id,chain_id,life_epoch,'
                 'authority_version,roster_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?) '
                 'ON CONFLICT(wanderer_id,reflection_date) DO NOTHING',
                 (wid, date, event['event_id'], event['chain_id'], int(wanderer.get('life_epoch') or 1),
                  int(wanderer.get('authority_version') or 1), _json(_friends(conn, wanderer)), now, now))
    return session_for_run(conn, {'wanderer': wanderer, **dict(event)})


def session_for_run(conn, run):
    """Verify the persisted ancestry, including a context switch, to its daily root."""
    ensure_wanderer_reflection_schema(conn)
    wid = int(run['wanderer']['id'])
    row = conn.execute('SELECT * FROM wanderer_reflection_sessions WHERE wanderer_id=? AND chain_id=?',
                       (wid, run['chain_id'])).fetchone()
    if row is None:
        return None
    session = dict(row)
    if int(run['wanderer'].get('life_epoch') or 1) != int(row['life_epoch']):
        raise PermissionError('本次每日自省所属生命已结束')
    # A pause/resume changes authority_version, not today's allowance. Current
    # authority is revalidated at dispatch and at every command by the runtime.
    current = str(run['event_id'])
    seen = set()
    for _ in range(80):
        if current in seen:
            break
        seen.add(current)
        event = conn.execute('SELECT * FROM wanderer_wake_events WHERE event_id=? AND wanderer_id=? AND chain_id=?',
                             (current, wid, session['chain_id'])).fetchone()
        if event is None:
            break
        if current == session['root_event_id']:
            if event['source'] == 'reflection' and event['event_key'] == session['reflection_date'] and not event['parent_turn_id']:
                return session
            break
        if event['source'] not in {'callback', 'protocol_repair', 'model_retry', 'context'} or not event['parent_turn_id']:
            break
        parent = conn.execute('SELECT event_id FROM wanderer_turns WHERE turn_id=? AND wanderer_id=? AND chain_id=?',
                              (event['parent_turn_id'], wid, session['chain_id'])).fetchone()
        if parent is None:
            break
        current = parent['event_id']
    raise PermissionError('每日自省回调来源链不完整')


def reserve_free_call(conn, run):
    session = session_for_run(conn, run)
    if session is None:
        return None
    wid, date = int(session['wanderer_id']), session['reflection_date']
    serialize_on(conn, 'wanderer-reflection-session', [f'{wid}:{date}'])
    if conn.execute('SELECT 1 FROM wanderer_reflection_dispatches WHERE turn_id=?', (run['turn_id'],)).fetchone():
        raise PermissionError('此自省回合已经派发，不得重复请求模型')
    row = conn.execute('UPDATE wanderer_reflection_sessions SET ai_calls=ai_calls+1,status=\'running\',updated_at=? '
                       'WHERE wanderer_id=? AND reflection_date=? AND ai_calls<? RETURNING ai_calls',
                       (local_iso(), wid, date, FREE_CALL_LIMIT)).fetchone()
    if row is None:
        return {'ok': False, 'reflection_date': date, 'limit': FREE_CALL_LIMIT}
    conn.execute('INSERT INTO wanderer_reflection_dispatches(turn_id,wanderer_id,reflection_date,event_id,ordinal,dispatched_at) '
                 'VALUES(?,?,?,?,?,?)', (run['turn_id'], wid, date, run['event_id'], int(row['ai_calls']), local_iso()))
    return {'ok': True, 'reflection_date': date, 'used': int(row['ai_calls']), 'remaining': FREE_CALL_LIMIT-int(row['ai_calls'])}


def friend_ratings(conn, wanderer, *, date=None, offset=0, limit=50, pending_only=False):
    from .wanderer_relationship_service import affinity_states
    ensure_wanderer_reflection_schema(conn)
    date = date or local_iso()[:10]
    peers = _friends(conn, wanderer)
    states = affinity_states(conn, int(wanderer['id']), [p['peer_identity'] for p in peers])
    ratings = {r['peer_identity']: dict(r) for r in conn.execute('SELECT * FROM wanderer_affinity_ratings '
               'WHERE wanderer_id=? AND reflection_date=?', (int(wanderer['id']), date)).fetchall()}
    values = []
    for peer in peers:
        rating = ratings.get(peer['peer_identity'])
        state = states[peer['peer_identity']]
        values.append({**peer, 'affinity': state['affinity'], 'revision': state['revision'], 'rated': bool(rating),
                       'grade': rating['grade'] if rating else None, 'reason': rating['reason'] if rating else '',
                       'label': GRADES[rating['grade']][0] if rating else '今日未评分'})
    values.sort(key=lambda item: (item['rated'], item['peer_identity']))
    count, rated = len(values), sum(int(item['rated']) for item in values)
    if pending_only:
        values = [item for item in values if not item['rated']]
    offset, limit = max(0, int(offset)), max(1, min(500, int(limit)))
    return {'reflection_date': date, 'items': values[offset:offset+limit], 'total': len(values),
            'next_offset': offset+limit if offset+limit < len(values) else None,
            'friend_count': count, 'rated_count': rated, 'missing_count': count-rated}


def snapshot(conn, wanderer, *, date=None):
    ensure_wanderer_reflection_schema(conn)
    date = date or local_iso()[:10]
    row = conn.execute('SELECT * FROM wanderer_reflection_sessions WHERE wanderer_id=? AND reflection_date=?',
                       (int(wanderer['id']), date)).fetchone()
    coverage = friend_ratings(conn, wanderer, date=date, limit=1)
    used = int(row['ai_calls']) if row else 0
    return {'reflection_date': date, 'status': row['status'] if row else 'not_started',
            'free_limit': FREE_CALL_LIMIT, 'ai_calls': used, 'free_remaining': FREE_CALL_LIMIT-used,
            'friend_count': coverage['friend_count'], 'rated_count': coverage['rated_count'],
            'missing_count': coverage['missing_count'], 'ordinary_wake_quota_exempt': True}


def rate_friend(conn, run, peer_identity, grade, *, expected_revision, operation_id, reason='',
                _session_record=None, _eligible_peers=None):
    from .wanderer_relationship_service import affinity_state, set_affinity
    session = _session_record or session_for_run(conn, run)
    if session is None:
        raise PermissionError('好感评级只能在系统登记的每日自省中提交')
    if run.get('scope', {}).get('kind') != 'private':
        raise PermissionError('好友评分属于本人私密自省，请在私有场景执行')
    wid, date, peer = int(run['wanderer']['id']), session['reflection_date'], str(peer_identity)
    grade = next((key for key, (label, _) in GRADES.items() if isinstance(grade, str) and grade in {key, label}), None)
    if grade is None:
        raise SocialError('评级只接受五个规定档位，不接受绝对好感数值。', 400)
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
        raise SocialError('请携带当前好感记录的revision。', 400)
    if not operation_id:
        raise SocialError('评分需要幂等指令编号。', 400)
    reason = str(reason or '').strip()[:200]
    request_hash = hashlib.sha256(_json([wid, date, peer, grade, expected_revision, reason]).encode()).hexdigest()
    serialize_on(conn, 'wanderer-affinity-rating-operation', [str(operation_id)])
    done = conn.execute('SELECT request_hash,result_json FROM wanderer_affinity_rating_operations WHERE operation_id=?', (operation_id,)).fetchone()
    if done:
        if done['request_hash'] != request_hash:
            raise SocialError('评分指令编号已用于不同内容。', 409)
        return {**json.loads(done['result_json']), 'replayed': True}
    serialize_on(conn, 'wanderer-reflection-session', [f'{wid}:{date}'])
    eligible = _eligible_peers if _eligible_peers is not None else {p['peer_identity'] for p in _friends(conn, run['wanderer'])}
    if peer not in eligible:
        raise SocialError('此成员已不在当前好友名单中，保留既有好感记录。', 409)
    current = affinity_state(conn, wid, peer)
    if current['revision'] != expected_revision:
        raise SocialError('好感记录已变化，请读取最新revision后重新评分。', 409)
    if conn.execute('SELECT 1 FROM wanderer_affinity_ratings WHERE wanderer_id=? AND peer_identity=? '
                    'AND reflection_date>? LIMIT 1', (wid, peer, date)).fetchone():
        raise SocialError('新的自省日已经对此好友评分，旧日回调不能覆盖新日结果。', 409)
    prior = conn.execute('SELECT * FROM wanderer_affinity_ratings WHERE wanderer_id=? AND reflection_date=? AND peer_identity=?',
                         (wid, date, peer)).fetchone()
    baseline = float(prior['baseline_affinity']) if prior else current['affinity']
    value = calculate_affinity(baseline, grade)
    written = set_affinity(conn, run['wanderer'], peer, value, expected_revision=expected_revision,
                           source_turn_id=run['turn_id'], operation_id=operation_id, reason=reason)
    conn.execute("UPDATE wanderer_relationship_versions SET origin='daily_reflection' WHERE wanderer_id=? AND peer_identity=?", (wid, peer))
    now = local_iso()
    conn.execute('INSERT INTO wanderer_affinity_ratings(wanderer_id,reflection_date,peer_identity,grade,baseline_affinity,new_affinity,'
                 'revision,rule_version,source_turn_id,reason,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) '
                 'ON CONFLICT(wanderer_id,reflection_date,peer_identity) DO UPDATE SET grade=excluded.grade,new_affinity=excluded.new_affinity,'
                 'revision=excluded.revision,source_turn_id=excluded.source_turn_id,reason=excluded.reason,updated_at=excluded.updated_at',
                 (wid, date, peer, grade, baseline, value, written['revision'], RULE_VERSION, run['turn_id'], reason, now, now))
    result = {**written, 'grade': grade, 'grade_label': GRADES[grade][0], 'reflection_date': date,
              'baseline_affinity': baseline, 'rule_version': RULE_VERSION, 'corrected': bool(prior)}
    conn.execute('INSERT INTO wanderer_affinity_rating_operations(operation_id,wanderer_id,reflection_date,peer_identity,request_hash,result_json,created_at) '
                 'VALUES(?,?,?,?,?,?,?)', (operation_id, wid, date, peer, request_hash, _json(result), now))
    return result


def unresolved_failures(conn, wanderer_id, chain_id):
    """A successful correction also recovers failed correction ancestors.

    A model retry may itself need protocol repair (or the reverse). Only these
    explicit correction edges count; an unrelated completed callback cannot
    erase an earlier failure. All failed turns remain in their original ledger.
    """
    events = [dict(item) for item in conn.execute('SELECT * FROM wanderer_wake_events '
        'WHERE wanderer_id=? AND chain_id=? ORDER BY depth DESC,updated_at DESC,event_id',
        (int(wanderer_id), chain_id)).fetchall()]
    recovered = set()
    for _ in range(len(events)):
        previous = len(recovered)
        for event in events:
            if (event['source'] in {'protocol_repair', 'model_retry'} and event['parent_turn_id']
                    and (event['state'] == 'completed' or event['turn_id'] in recovered)):
                recovered.add(event['parent_turn_id'])
        if len(recovered) == previous:
            break
    return [event for event in events if event['state'] in {'failed', 'blocked'} and event['turn_id'] not in recovered]


def sync_session(conn, run):
    """Observe the whole chain, including deferred callbacks, instead of its first turn."""
    session = session_for_run(conn, run)
    if session is None:
        return None
    wid, date = int(session['wanderer_id']), session['reflection_date']
    serialize_on(conn, 'wanderer-reflection-session', [f'{wid}:{date}'])
    row = conn.execute('SELECT ai_calls FROM wanderer_reflection_sessions WHERE wanderer_id=? AND reflection_date=?', (wid, date)).fetchone()
    coverage = friend_ratings(conn, run['wanderer'], date=date, limit=1)
    pending = conn.execute("SELECT 1 FROM wanderer_wake_events WHERE wanderer_id=? AND chain_id=? AND state IN ('pending','processing','deferred') LIMIT 1", (wid, session['chain_id'])).fetchone()
    completed = conn.execute("SELECT 1 FROM wanderer_turns WHERE wanderer_id=? AND chain_id=? AND state='completed' LIMIT 1", (wid, session['chain_id'])).fetchone()
    unresolved = len(unresolved_failures(conn, wid, session['chain_id']))
    status = 'running' if pending else ('partial' if coverage['missing_count'] or unresolved else 'applied') if completed else 'failed'
    now = local_iso()
    conn.execute('UPDATE wanderer_reflection_sessions SET status=?,updated_at=?,closed_at=? WHERE wanderer_id=? AND reflection_date=?',
                 (status, now, None if pending else now, wid, date))
    prior = conn.execute('SELECT applied_json,output_json FROM wanderer_daily_reflections WHERE wanderer_id=? AND reflection_date=?', (wid,date)).fetchone()
    applied = json.loads(prior['applied_json'] or '{}') if prior else {}
    output = json.loads(prior['output_json'] or '{}') if prior else {}
    turns = conn.execute('SELECT turn_id,state,error FROM wanderer_turns WHERE wanderer_id=? AND chain_id=? '
                         'ORDER BY created_at,turn_id', (wid, session['chain_id'])).fetchall()
    output['runtime_turns'] = [item['turn_id'] for item in turns]
    # Project only successful real receipts. This also runs for queued roots and
    # late callbacks, which do not return through run_reflection's manual wrapper.
    memories = []
    for item in conn.execute("SELECT c.capability,c.command_json,c.receipt_json FROM wanderer_commands c "
            "JOIN wanderer_turns t ON t.turn_id=c.turn_id WHERE t.wanderer_id=? AND t.chain_id=? "
            "AND c.state='succeeded' AND c.capability IN ('memory.write','self.reflect') "
            'ORDER BY c.created_at,c.command_id', (wid, session['chain_id'])).fetchall():
        receipt = json.loads(item['receipt_json'] or '{}')
        value = receipt.get('result') or {}
        if item['capability'] == 'memory.write' and isinstance(value, dict) and value.get('memory_id'):
            memories.append(value['memory_id'])
        elif item['capability'] == 'self.reflect':
            args = json.loads(item['command_json'] or '{}').get('args') or {}
            output.update({key: args[key] for key in ('mood', 'self_review', 'tomorrow') if key in args})
    if memories:
        summaries = [item['content'] for item in conn.execute('SELECT content FROM wanderer_episodic_memories '
            f"WHERE wanderer_id=? AND id IN ({','.join('?' for _ in memories)}) ORDER BY occurred_at,id", (wid, *memories)).fetchall()]
        output['today'] = {'summary': '\n'.join(summaries)}
        applied['new_episodes'] = list(dict.fromkeys([*(applied.get('new_episodes') or []), *memories]))
    applied.update({'runtime_event':session['root_event_id'], 'friend_count':coverage['friend_count'],
                    'rated_count':coverage['rated_count'], 'missing_count':coverage['missing_count'],
                    'unresolved_events':unresolved})
    error = next((str(item['error'])[:500] for item in reversed(turns) if item['error']), '') if unresolved else ''
    conn.execute('UPDATE wanderer_daily_reflections SET status=?,ai_calls=?,applied_json=?,output_json=?,error=?,applied_at=? '
                 'WHERE wanderer_id=? AND reflection_date=?',
                 (status, int(row['ai_calls']), _json(applied), _json(output), error, None if pending else now, wid, date))
    if completed:
        conn.execute('UPDATE wanderer_agents SET last_reflection_at=? WHERE id=?', (now, wid))
    return {**snapshot(conn, run['wanderer'], date=date), 'status':status}


def sync_event_session(conn, event):
    """Close a registered session even when claiming its event fails before a run exists."""
    ensure_wanderer_reflection_schema(conn)
    session=conn.execute('SELECT * FROM wanderer_reflection_sessions WHERE wanderer_id=? AND chain_id=?',
        (event['wanderer_id'],event['chain_id'])).fetchone()
    if session is None:
        return None
    actor=conn.execute('SELECT * FROM wanderer_agents WHERE id=?',(event['wanderer_id'],)).fetchone()
    if actor is None or int(actor['life_epoch'] or 1) != int(session['life_epoch']):
        conn.execute("UPDATE wanderer_reflection_sessions SET status='failed',updated_at=?,closed_at=? "
            'WHERE wanderer_id=? AND reflection_date=?',(local_iso(),local_iso(),event['wanderer_id'],session['reflection_date']))
        conn.execute("UPDATE wanderer_daily_reflections SET status='failed',error='本次自省所属生命已结束' "
            'WHERE wanderer_id=? AND reflection_date=?',(event['wanderer_id'],session['reflection_date']))
        return None
    return sync_session(conn,{'wanderer':dict(actor),**dict(event)})
