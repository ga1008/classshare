"""Teacher-owned course/lesson alignment, persisted review candidates and fences.

School reads run outside DB transactions. The owner lock, local input fingerprint,
account generation and both optimistic versions are checked again before saving.
"""
from __future__ import annotations

from datetime import date, timedelta
import json
import time

from fastapi import HTTPException

from ..database import get_db_connection
from . import login_attendance_service as local
from . import smart_classroom_alignment as rules
from . import smart_classroom_auto_sync_service as auto
from . import smart_classroom_miniapp_adapter as mini
from . import smart_classroom_write_service as ledger
from . import smart_classroom_workflow_service as flow

TTL_SECONDS = 900
ACTIVE = "('pending','running','reading','comparing','outcome_unknown')"


def _load(value, default=None):
    return json.loads(value) if value else ({} if default is None else default)


def _course_link(conn, user, offering_id):
    row = conn.execute('SELECT * FROM smart_attendance_course_links WHERE class_offering_id=? AND owner_teacher_id=?', (offering_id, user['id'])).fetchone()
    return dict(row) if row else None


def offering_alignment_context(conn, user, offering_id):
    offering = local.require_teacher(conn, offering_id, user)
    course = dict(conn.execute('SELECT * FROM courses WHERE id=?', (offering['course_id'],)).fetchone())
    all_rows = [dict(r) for r in conn.execute('SELECT * FROM class_offering_sessions WHERE class_offering_id=? ORDER BY session_date,order_index,id', (offering_id,)).fetchall()]
    rows = [r for r in all_rows if r.get('schedule_status') not in {'cancelled', 'canceled', 'suspended'}]
    base = local._context(conn, offering, rows[0] if rows else {'id': 0, 'title': '', 'session_date': '', 'weekday': 0})
    contexts, sessions = {}, []
    for index, row in enumerate(rows, 1):
        ctx = {**base, 'session_id': row['id'], 'session_title': row['title'], 'session_date': row['session_date'],
               'weekday': row['weekday'], 'week_index': row.get('week_index'), 'section_text': row.get('academic_section_text') or '',
               'course_code': row.get('academic_course_code') or ''}
        contexts[row['id']] = ctx
        try:
            day = date.fromisoformat(ctx['session_date']).isoweekday()
        except (TypeError, ValueError):
            day = None
        sessions.append({'id': row['id'], 'session_date': ctx['session_date'], 'week': ctx['week_index'],
                         'day_of_week': day, 'sections': list(mini.sections(ctx['section_text'])), 'order_index': index})
    numbers = {str(r.get('student_id_number') or '').strip() for r in local._roster(conn, offering_id) if not r.get('confirmed_retake')}
    info = {'course_name': course.get('name') or '', 'course_code': course.get('academic_course_code') or base['course_code'],
            'academic_year': base['year'], 'academic_term': base['term'], 'student_count': len(numbers),
            'session_count': len(sessions) if sessions and all(s['week'] and s['sections'] and s['day_of_week'] for s in sessions) else None,
            **rules.course_metadata(course)}
    try:
        access = ledger._account_access(conn, user['id'])
        account_identity = [ledger.source_adapter.external_account_key(access), access['credential_generation']]
        account_error = None
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        access, account_identity = None, None
        account_error = exc.detail if isinstance(exc.detail, dict) else {'code': 'missing_credential', 'message': str(exc.detail)}
    course_fp = ledger._hash({'offering': offering_id, 'teacher': user['id'], 'course': {k:v for k,v in info.items() if k != 'session_count'}, 'numbers': sorted(numbers), 'account': account_identity})
    return {'fingerprint': ledger._hash({'course': course_fp, 'sessions': sessions}), 'course_fingerprint': course_fp,
            'course': info, 'sessions': sessions, 'contexts': contexts, 'numbers': numbers, 'access': access, 'account_error': account_error}


def _local_session(context, session_id):
    return next((s for s in context['sessions'] if s['id'] == session_id), None)


def alignment_view(conn, user, offering_id, session_id, context=None):
    context = context or offering_alignment_context(conn, user, offering_id)
    course = _course_link(conn, user, offering_id)
    session = flow._link(conn, user, session_id)
    def view(row, local_info, fingerprint):
        data = _load(row.get('alignment_json')) if row else {}
        stale = bool(row and row['context_fingerprint'] != fingerprint)
        return {'state': 'stale' if stale else row['state'] if row else 'unmatched', 'revision': row['revision'] if row else 0,
                'local': local_info, 'selected_id': data.get('selected_id'), 'candidates': data.get('candidates', []),
                'message': ('本地信息已变化，请重新对齐。' if stale else row['message'] if row else '尚未对齐，可先记录本地点名。'),
                'expires_at_ms': data.get('expires_at_ms'), 'error_code': data.get('error_code'), 'ai':data.get('ai')}
    cview = view(course, context['course'], context['course_fingerprint'])
    ctx = context['contexts'].get(session_id)
    sview = view(session, _local_session(context, session_id), flow._identity(ctx) if ctx else '')
    if course and session and session.get('course_link_revision') != course['revision']:
        sview['state'] = 'stale'
    if session and sview['state'] == 'matched' and _load(session.get('slot_json')).get('local_session_identity') != {k:v for k,v in (sview['local'] or {}).items() if k != 'id'}:
        sview['state'] = 'stale'
        sview['message'] = '课表或顺延课次序号已变化，请重新对齐本课。'
    if cview['state'] != 'matched' and sview['state'] == 'matched':
        sview['state'] = 'stale'
    return {'needs_review': cview['state'] in {'review', 'stale'} or sview['state'] in {'review', 'stale'}, 'course': cview, 'session': sview}


def _busy(conn, offering_id, session_id=None):
    clause, values = ('class_offering_id=?', [offering_id]) if session_id is None else ('session_id=?', [session_id])
    if conn.execute(f'SELECT 1 FROM smart_attendance_auto_sync WHERE {clause} AND (state=\'outcome_unknown\' OR (enabled=1 AND state IN {ACTIVE}))', values).fetchone():
        raise HTTPException(409, {'code': 'alignment_busy', 'message': '学校同步正在执行或等待回读，请完成后再对齐；当前选择保留。'})


def _source(group, context):
    access, course = context['access'], context['course']
    return {'remote_schedule_id': str(group['id']), 'remote_class_id': str(group.get('claId') or ''),
            'course_name': str(group.get('course') or ''), 'course_code': str(group.get('courseId') or ''),
            'teaching_class_name': str(group.get('claName') or group.get('chooseCourseNo') or ''),
            'academic_year': course['academic_year'], 'academic_term': course['academic_term'],
            'external_account_key': ledger.source_adapter.external_account_key(access),
            'credential_id': access['credential_id'], 'platform_code': access['platform_code'], 'school_code': 'gxufl'}


def _public(candidate):
    return {k: v for k, v in candidate.items() if not k.startswith('_')}


def _candidate_id(scope, target):
    return scope + '-' + ledger._hash(target)[:24]


async def _read_candidates(context, session_id=None, selected_session=None):
    access, info = context['access'], context['course']
    if not access or not info['academic_year'] or info['academic_term'] not in (1, 2):
        return [], {'status': 'unavailable', 'reason': '请先配置学校账号及本地学年学期。'}
    if not context['numbers'] or '' in context['numbers']:
        return [], {'status': 'unavailable', 'reason': '本地名单为空或学号缺失，无法核对。'}
    account = ledger.source_adapter.external_account_key(access)
    candidates = []
    async with ledger._account_lease(account) as lease:
        async with flow.open_authenticated_smart_classroom_client(access) as (raw, _, _):
            groups = await ledger.source_adapter.fetch_source_schedules(auto._LeasedClient(raw, account, lease), info['academic_year'], info['academic_term'])
        if len(groups) > 40:
            raise HTTPException(409, '学校授课班超过完整对齐范围，请先核对授课班；未截断后猜测绑定。')
        async with flow.open_authenticated_smart_classroom_client(access, api='mini') as (raw, _, _):
            client = auto._LeasedClient(raw, account, lease)
            payload = await mini.list_schedules(client)
            if not isinstance(payload, dict) or (str(payload.get('year')), str(payload.get('semester'))) != (info['academic_year'], str(info['academic_term'])):
                raise HTTPException(409, '学校当前学期与本地不一致，未绑定；本地记录不受影响。')
            rows = auto._schedule_rows(payload)
            if len(rows) > 80:
                raise HTTPException(409, '学校排课超过完整对齐范围，未截断猜测绑定。')
            snapshots, histories = {}, {}
            for group in groups:
                source = _source(group, context)
                if not source['remote_class_id'] or not source['course_code']:
                    continue
                related = []
                code_rows = [r for r in rows if str(r.get('courseCode')) == source['course_code']]
                unique_group = sum(str(g.get('courseId')) == source['course_code'] for g in groups) == 1
                for row in code_rows:
                    same = str(row['id']) == source['remote_schedule_id'] or str(row.get('claId') or row.get('classId') or '') == source['remote_class_id']
                    # On a new semester the representative PC id may differ
                    # from all mini timetable row ids and no rollcall exists.
                    # One sole teaching group for this course, plus the actual
                    # view roster checked below, establishes this association.
                    if not same and unique_group and not row.get('claId') and not row.get('classId'):
                        same = True
                    if not same:
                        key = str(row['id'])
                        if key not in histories:
                            histories[key] = await mini.read_history(client, key)
                        same = any(str(r.get('claId')) == source['remote_class_id'] and str(r.get('courseId')) == source['course_code'] for r in histories[key])
                    if same:
                        related.append(row)
                occurrences, complete = rules.occurrences(related)
                if not occurrences:
                    continue
                preferred = selected_session or _local_session(context, session_id)
                sample = next((s for s in occurrences if preferred and all(s[k] == preferred[k] for k in ('week', 'day_of_week', 'sections'))), occurrences[0])
                slot = {'kind': 'smart_attendance_slot', 'source_id': source['remote_schedule_id'], 'external_account_key': account, **sample}
                key = (sample['schedule_id'], sample['week'], sample['day_of_week'], tuple(sample['sections']), source['remote_class_id'])
                read_error = None
                try:
                    if key not in snapshots:
                        snapshots[key] = await mini.read_alignment_slot(client, slot, source)
                    snap = snapshots[key]
                except ledger.source_adapter.AttendanceSourceError as exc:
                    if exc.code not in {'slot_identity_changed','slot_section_changed','round_identity_changed','invalid_slot','invalid_roster','duplicate_student','missing_round_id'}:
                        raise
                    # A malformed/unrelated course must not abort discovery of
                    # all other classes. Keep an ineligible diagnostic card.
                    read_error = {'code':exc.code,'message':str(exc)}
                    snap = {'record':None,'students':{},'can_checkin':False}
                roster = rules.roster_evidence(context['numbers'], snap['students'])
                metadata = rules.course_metadata({**(snap.get('schedule') or {}), **{k: v for k, v in group.items() if v is not None}})
                target = {'course_name': source['course_name'], 'course_code': source['course_code'],
                          'academic_year': source['academic_year'], 'academic_term': source['academic_term'],
                          'teaching_class_name': source['teaching_class_name'], 'student_count': len(snap['students']),
                          'session_count': len(occurrences) if complete else None, **metadata}
                cid = _candidate_id('course', [source['external_account_key'], source['academic_year'], source['academic_term'], source['remote_class_id'], source['remote_schedule_id']])
                reasons = [f"名单交集 {roster['matched_count']} 人，双向未对齐 {roster['difference_count']} 人；学校名单为准。"]
                if read_error:
                    reasons = ['学校候选未通过身份核对：' + read_error['message']]
                elif snap.get('alignment_note'):
                    reasons.append(snap['alignment_note'])
                if not roster['majority']:
                    reasons.append('名单交集未超过双方人数的一半，不能认定同一教学班。')
                if not all(target.get(k) for k in ('session_count', 'total_hours', 'credits')):
                    reasons.append('学校课次数、学时或学分证据不完整。')
                candidates.append({'id': cid, 'label': source['course_name'] + ' · ' + source['teaching_class_name'],
                                   'eligible': roster['majority'], 'target': target, 'reasons': reasons,
                                   'evidence': {'roster': roster, 'roster_fingerprint': ledger._hash(sorted(snap['students'])), 'read_error': read_error, 'method': 'deterministic' if rules.deterministic_course(info, target, roster) else 'review'},
                                   '_source': source, '_occurrences': occurrences, '_sample': sample, '_snapshot': snap,
                                   '_roster_fingerprint': ledger._hash(sorted(snap['students']))})
    deterministic = [c for c in candidates if c['evidence']['method'] == 'deterministic']
    unresolved_same_course = any(c['evidence'].get('read_error') and
        (str(c['target']['course_code']).casefold() == str(info['course_code']).casefold() or flow._name(c['target']['course_name']) == flow._name(info['course_name'])) for c in candidates)
    ai = {'status': 'not_needed', 'decision': deterministic[0]['id'] if len(deterministic) == 1 else None}
    if unresolved_same_course:
        ai = {'status':'unavailable','decision':None,'reason':'另有同课程候选未完成学校身份核对，请查看候选说明。'}
        return candidates, ai
    if not deterministic:
        allowed = [c for c in candidates if c['evidence']['roster']['accepted']]
        if allowed:
            ai = await flow._ai_assist(
                '系统已逐项验证以下硬前置条件：同一学年学期，名单双向未对齐总数小于5，交集分别超过双方人数的一半。'
                '少于5人的名单双向差异（1至4人）是已允许的正常退学/转入情况，不是反对同课的证据，不得单独作为uncertain理由。'
                '你负责结合课程语义判断是否为同一门课。课程编号一致、课程名称或常见后缀别名一致、课程课次数/学时/学分等证据一致，'
                '任一路充分证据均可支持同课，不要求所有字段同时齐全或一致。null、缺失、空白表示未知，属于中性信息，'
                '不得仅因另一字段缺失（尤其学校学时或学分缺失）判uncertain。'
                '例如编号及名称均相同、完整排课次数同为30次、名单相差2人且学校学时学分为null，可据已有证据选该候选。'
                '已有明确相互矛盾的课程身份、多个同样可能候选或确实没有充分同课证据时才选uncertain；'
                '不得因只有一个候选而忽略真实冲突。只选择allowed_decisions中的候选id或uncertain，并简短说明证据。',
                {'local_course': info, 'candidates': [{'id':c['id'],'target':c['target'],'roster':c['evidence']['roster']} for c in allowed],
                 'allowed_decisions': ['uncertain', *[c['id'] for c in allowed]]})
            for c in allowed:
                if ai.get('decision') == c['id']:
                    c['evidence']['method'] = 'ai'
                    c['reasons'].append('AI核对：' + str(ai.get('reason') or '课程证据支持同课。'))
    return candidates, ai


def _session_candidates(context, local_session, course_candidate):
    source = course_candidate['_source']
    candidates = []
    for occurrence in course_candidate['_occurrences']:
        sample = course_candidate['_sample']
        is_sample = all(occurrence[k] == sample[k] for k in ('schedule_id', 'week', 'day_of_week', 'sections'))
        snap = course_candidate['_snapshot'] if is_sample else {}
        record = snap.get('record')
        anchor = date.fromisoformat(local_session['session_date']) - timedelta(weeks=int(local_session['week']) - 1, days=local_session['day_of_week'] - 1) if local_session['week'] and local_session['day_of_week'] else None
        remote_date = (anchor + timedelta(weeks=occurrence['week'] - 1, days=occurrence['day_of_week'] - 1)).isoformat() if anchor else None
        target = {**occurrence, 'session_date': remote_date, 'remote_checkin_id': str(record['id']) if record else None}
        exact = rules.exact_session(local_session, target)
        cid = _candidate_id('session', [course_candidate['id'], occurrence])
        slot = {'kind': 'smart_attendance_slot', 'source_id': source['remote_schedule_id'], 'external_account_key': source['external_account_key'],
                **target, 'can_create': bool(is_sample and not record and snap.get('can_checkin') and mini.LIFECYCLE_VERIFIED),
                'local_session_identity': {k: v for k, v in local_session.items() if k != 'id'}, 'alignment_method': 'exact_slot' if exact else 'manual'}
        reasons = ['教学周、星期、完整节次及顺延课次序号均一致。'] if exact else [f'{k}不同，需教师核对。' for k in ('week', 'day_of_week', 'sections', 'order_index') if local_session.get(k) != target.get(k)]
        candidates.append({'id': cid, 'label': f"第{target.get('order_index') or '?'}次 · 第{target['week']}周 星期{target['day_of_week']} · {'、'.join(map(str,target['sections']))}节",
                           'eligible': True, 'target': target, 'reasons': reasons, 'evidence': {'method': 'exact_slot' if exact else 'manual', 'roster': course_candidate['evidence']['roster']}, '_slot': slot})
    candidates.sort(key=lambda c: (0 if c['evidence']['method'] == 'exact_slot' else 1, abs((c['target'].get('order_index') or 0) - local_session['order_index'])))
    return candidates


def _save_course(conn, user, offering_id, context, candidates, chosen, message, error_code=None, ai=None):
    previous = _course_link(conn, user, offering_id)
    revision = (previous['revision'] if previous else 0) + 1
    source = dict(chosen['_source']) if chosen else {}
    if chosen:
        source.update(course_match_evidence='verified_course_alignment', local_course_code=context['course']['course_code'],
                      local_course_codes=sorted({c['course_code'] for c in context['contexts'].values()}),
                      local_course_name=context['course']['course_name'], course_link_revision=revision,
                      course_alignment_method=chosen['evidence']['method'], roster_evidence=chosen['evidence']['roster'],
                      remote_roster_fingerprint=chosen['_roster_fingerprint'])
    data = {'candidates': [_public(c) for c in candidates], 'selected_id': chosen['id'] if chosen else None,
            'expires_at_ms': int(time.time() * 1000) + TTL_SECONDS * 1000, 'error_code': error_code, 'input_fingerprint': context['fingerprint'],
            'ai':{k:ai.get(k) for k in ('status','decision','code','reason')} if ai else None}
    conn.execute("""INSERT INTO smart_attendance_course_links(class_offering_id,owner_teacher_id,revision,state,context_fingerprint,source_json,alignment_json,message,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(class_offering_id) DO UPDATE SET owner_teacher_id=excluded.owner_teacher_id,revision=excluded.revision,
        state=excluded.state,context_fingerprint=excluded.context_fingerprint,source_json=excluded.source_json,alignment_json=excluded.alignment_json,message=excluded.message,updated_at=excluded.updated_at""",
        (offering_id, user['id'], revision, 'matched' if chosen else 'review' if candidates else 'unmatched', context['course_fingerprint'], ledger._json(source), ledger._json(data), message, ledger._now()))
    return revision, source


def _save_session(conn, user, offering_id, context, session, revision, source, candidates, chosen):
    sid = session['id']
    old = flow._link(conn, user, sid)
    slot = chosen['_slot'] if chosen else {}
    # An executed school round cannot be silently reassigned even when idle.
    plans = conn.execute('SELECT source_json,slot_json,binding_id FROM smart_attendance_auto_sync WHERE session_id=? AND binding_id IS NOT NULL', (sid,)).fetchall()
    if chosen and any((_load(p['source_json']).get('remote_class_id'), _load(p['slot_json']).get('schedule_id'), _load(p['slot_json']).get('week'), _load(p['slot_json']).get('day_of_week'), _load(p['slot_json']).get('sections')) !=
                      (source.get('remote_class_id'), slot.get('schedule_id'), slot.get('week'), slot.get('day_of_week'), slot.get('sections')) for p in plans):
        chosen, slot = None, {}
        message = '本课已有学校写入记录，不能改绑其他课次；请先核对学校记录。'
    else:
        message = '课次已对齐，可开始本地记录；下课后同步与推送。' if chosen else ('课次存疑，请选择学校课次卡片确认；本地记录不受影响。' if candidates else '课程尚未对齐；本地记录不受影响。')
    data = {'candidates': [_public(c) for c in candidates], 'selected_id': chosen['id'] if chosen else None, 'expires_at_ms': int(time.time() * 1000) + TTL_SECONDS * 1000, 'input_fingerprint': context['fingerprint']}
    conn.execute("""INSERT INTO smart_attendance_session_links(session_id,class_offering_id,owner_teacher_id,state,context_fingerprint,source_json,slot_json,diagnostics_json,message,updated_at,alignment_json,course_link_revision)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET owner_teacher_id=excluded.owner_teacher_id,
        class_offering_id=excluded.class_offering_id,revision=smart_attendance_session_links.revision+1,state=excluded.state,
        context_fingerprint=excluded.context_fingerprint,source_json=excluded.source_json,slot_json=excluded.slot_json,
        diagnostics_json=excluded.diagnostics_json,message=excluded.message,updated_at=excluded.updated_at,alignment_json=excluded.alignment_json,course_link_revision=excluded.course_link_revision""",
        (sid, offering_id, user['id'], 'matched' if chosen else 'review' if candidates else 'unmatched', flow._identity(context['contexts'][sid]), ledger._json(source), ledger._json(slot), ledger._json(chosen['reasons'] if chosen else []), message, ledger._now(), ledger._json(data), revision))


def _mark_occupied_slots(conn, offering_id, session, context, source, options):
    keys = ('schedule_id','week','day_of_week','sections')
    for row in conn.execute("SELECT * FROM smart_attendance_session_links WHERE class_offering_id=? AND session_id<>? AND state='matched'", (offering_id,session['id'])).fetchall():
        current = _local_session(context,row['session_id'])
        other_source, other_slot = _load(row['source_json']), _load(row['slot_json'])
        if not current or row['context_fingerprint'] != flow._identity(context['contexts'][row['session_id']]) or other_slot.get('local_session_identity') != {k:v for k,v in current.items() if k != 'id'}:
            continue
        if other_source.get('external_account_key') != source.get('external_account_key') or other_source.get('remote_class_id') != source.get('remote_class_id'):
            continue
        for option in options:
            if all(option['_slot'].get(k) == other_slot.get(k) for k in keys):
                option['eligible'] = False
                option['reasons'].append(f"已绑定本地第{current['order_index']}次课，请先核对原课次，不能重复绑定。")


def _recheck(conn, user, offering_id, initial):
    local.require_teacher(conn, offering_id, user, lock=True)
    fresh = offering_alignment_context(conn, user, offering_id)
    if fresh['fingerprint'] != initial['fingerprint']:
        raise HTTPException(409, '核对期间课程、课表、名单已变化或账号已更新，请重试对齐；本地记录不受影响。')
    _busy(conn, offering_id)
    return fresh


def _stale_roster(conn, user, offering_id, initial, old, candidates, ai):
    """Keep the last authorized mapping, but revoke its green/write state."""
    message = '学校名单已超出原确认范围，请在课程卡片重新确认；原绑定、人工选择和本地记录已保留。'
    data = _load(old['alignment_json'])
    data.update(candidates=[_public(c) for c in candidates], input_fingerprint=initial['fingerprint'],
                expires_at_ms=int(time.time()*1000)+TTL_SECONDS*1000, error_code='roster_changed',
                ai={k:ai.get(k) for k in ('status','decision','code','reason')})
    changed = conn.execute("UPDATE smart_attendance_course_links SET state='stale',revision=revision+1,alignment_json=?,message=?,updated_at=? WHERE class_offering_id=? AND owner_teacher_id=? AND revision=?",
        (ledger._json(data),message,ledger._now(),offering_id,user['id'],old['revision']))
    if changed.rowcount != 1:
        raise HTTPException(409,'课程对齐版本已变化，请刷新后核对。')
    conn.execute("UPDATE smart_attendance_session_links SET state='stale',revision=revision+1,message=?,updated_at=? WHERE class_offering_id=? AND owner_teacher_id=? AND course_link_revision=?",
        (message,ledger._now(),offering_id,user['id'],old['revision']))
    return message


async def align_offering(user, offering_id, *, background=False, session_id=None, confirmation=None):
    background_missing = None
    with get_db_connection() as conn:
        # Short SQL-only snapshot and preservation updates share the same parent
        # lock. A background run must not stale a foreground confirmation that
        # committed between an unlocked snapshot and this preservation branch.
        local.require_teacher(conn, offering_id, user, lock=True)
        initial = offering_alignment_context(conn, user, offering_id)
        if session_id is not None:
            flow._lesson(conn, user, offering_id, session_id)
        old = _course_link(conn, user, offering_id)
        any_owner = conn.execute('SELECT owner_teacher_id FROM smart_attendance_course_links WHERE class_offering_id=?', (offering_id,)).fetchone()
        if background and any_owner and int(any_owner['owner_teacher_id']) != int(user['id']):
            local.require_teacher(conn, offering_id, user, lock=True)
            conn.execute("UPDATE smart_attendance_course_links SET state='stale',message='授课教师已变化，请现任教师重新核对绑定。' WHERE class_offering_id=?", (offering_id,))
            conn.commit()
            return {'status': 'preserved', 'message': '授课教师已变化，保留原绑定供核对；后台未改绑。'}
        before_course_revision = old['revision'] if old else 0
        before_session = flow._link(conn, user, session_id) if session_id else None
        before_session_revision = before_session['revision'] if before_session else 0
        _busy(conn, offering_id)
        if background and old and old['state'] in {'matched', 'stale'}:
            local.require_teacher(conn, offering_id, user, lock=True)
            if old['context_fingerprint'] != initial['course_fingerprint']:
                conn.execute("UPDATE smart_attendance_course_links SET state='stale',message='课程信息已变化，请教师核对后重新对齐。' WHERE class_offering_id=?", (offering_id,))
            background_missing = []
            for s in initial['sessions']:
                link = flow._link(conn, user, s['id'])
                if not link:
                    background_missing.append(s['id'])
                elif link['context_fingerprint'] != flow._identity(initial['contexts'][s['id']]) or _load(link['slot_json']).get('local_session_identity') != {k:v for k,v in s.items() if k != 'id'}:
                    conn.execute("UPDATE smart_attendance_session_links SET state='stale',message='课表已变化，请重新对齐本课。' WHERE session_id=?", (s['id'],))
            conn.commit()
            if not background_missing or old['state'] == 'stale' or old['context_fingerprint'] != initial['course_fingerprint']:
                return {'status': 'preserved', 'fingerprint': initial['fingerprint']}
        if confirmation:
            if confirmation['expected_course_revision'] != before_course_revision or confirmation['expected_binding_revision'] != before_session_revision:
                raise HTTPException(409, '候选版本已变化，请刷新后重新确认；选择未提交。')
            row = old if confirmation['scope'] == 'course' else before_session
            data = _load(row.get('alignment_json')) if row else {}
            if data.get('input_fingerprint') != initial['fingerprint']:
                raise HTTPException(409, '本地课程、名单或课表已变化，请重新查看候选后确认。')
            candidate = next((c for c in data.get('candidates', []) if c['id'] == confirmation['candidate_id']), None)
            if not candidate or not candidate.get('eligible') or data.get('expires_at_ms', 0) < time.time() * 1000:
                raise HTTPException(409, '候选已过期或不可确认，请重新对齐后选择。')
    candidates, ai = await _read_candidates(initial, session_id, candidate['target'] if confirmation and confirmation['scope'] == 'session' else None)
    previous_candidate = next((c for c in candidates if old and c['id'] == _load(old['alignment_json']).get('selected_id')),None)
    old_source = _load(old['source_json']) if old else {}
    roster_changed = bool(old_source and previous_candidate and not rules.roster_authorized(
        previous_candidate['evidence']['roster'],old_source,previous_candidate['_roster_fingerprint']))
    chosen = next((c for c in candidates if c['id'] == ai.get('decision') and c['evidence']['method'] in {'deterministic', 'ai'}), None)
    if not confirmation and old and old['state'] == 'matched' and old['context_fingerprint'] == initial['course_fingerprint']:
        previous = previous_candidate if previous_candidate and previous_candidate['eligible'] and not roster_changed else None
        if previous:
            chosen = previous
            chosen['evidence']['method'] = _load(old['source_json']).get('course_alignment_method', 'manual')
    if confirmation:
        if confirmation['scope'] == 'course':
            chosen = next((c for c in candidates if c['id'] == confirmation['candidate_id'] and c['eligible']), None)
            if chosen:
                if chosen['target'] != candidate['target'] or chosen['evidence']['roster_fingerprint'] != candidate['evidence']['roster_fingerprint']:
                    raise HTTPException(409, '学校课程或名单证据已变化，请重新查看候选后确认。')
                chosen['evidence']['method'] = 'manual'
        else:
            chosen = next((c for c in candidates if c['id'] == _load(old['alignment_json']).get('selected_id') and c['eligible']), None)
            if chosen:
                chosen['evidence']['method'] = _load(old['source_json']).get('course_alignment_method', 'manual')
        if not chosen and not (roster_changed and confirmation['scope']=='session'):
            raise HTTPException(409, '学校课程候选已变化，请重新对齐；未保存旧选择。')
    with get_db_connection() as conn:
        _recheck(conn, user, offering_id, initial)
        latest = _course_link(conn, user, offering_id)
        latest_session = flow._link(conn, user, session_id) if session_id else None
        if (latest['revision'] if latest else 0) != before_course_revision or (latest_session['revision'] if latest_session else 0) != before_session_revision:
            raise HTTPException(409, '另一操作已更新对齐结果，请刷新后核对。')
        if roster_changed and not (confirmation and confirmation['scope']=='course'):
            message = _stale_roster(conn,user,offering_id,initial,old,candidates,ai)
            conn.commit()
            if confirmation:
                raise HTTPException(409,{'code':'roster_changed','message':message})
            return flow.workflow(conn,user,offering_id,session_id) if session_id is not None else {'status':'stale','message':message}
        if confirmation:
            inserted = conn.execute('INSERT INTO smart_attendance_alignment_requests(owner_teacher_id,request_key,request_hash,class_offering_id,session_id,created_at) VALUES(?,?,?,?,?,?) ON CONFLICT(owner_teacher_id,request_key) DO NOTHING RETURNING request_key',
                (user['id'], confirmation['idempotency_key'], ledger._hash({'offering_id': offering_id, 'session_id': session_id, **confirmation}), offering_id, session_id, ledger._now())).fetchone()
            if not inserted:
                raise HTTPException(409, '该确认操作标识已使用，请刷新后重试。')
        message = '课程已对齐；学校名单为准。' if chosen else '课程存疑，请比较课程卡片后确认。' if candidates else str((initial.get('account_error') or {}).get('message') or ai.get('reason') or '未找到同学期学校课程，请核对课表；本地记录继续保留。')
        if candidates and not chosen and ai.get('reason'):
            message += ' AI核对：' + str(ai['reason'])[:160]
        # A lesson-only refresh preserves the already verified course revision and
        # does not stale other lessons merely because this lesson was inspected.
        reuse = bool(old and old['state'] == 'matched' and old['context_fingerprint'] == initial['course_fingerprint'] and chosen and
                     _load(old['alignment_json']).get('selected_id') == chosen['id'] and not (confirmation and confirmation['scope'] == 'course'))
        if reuse:
            revision, source = old['revision'], _load(old['source_json'])
        else:
            revision, source = _save_course(conn, user, offering_id, initial, candidates, chosen, message, (initial.get('account_error') or {}).get('code'), ai)
        if background_missing is not None and not reuse:
            # Do not replace a teacher's existing course decision during bootstrap.
            raise HTTPException(409, '原课程目标的学校证据已变化，请教师重新核对。')
        has_previous_course_target = bool(old and _load(old['source_json']))
        targets = [s for s in initial['sessions'] if (background_missing is None or s['id'] in background_missing) and (session_id is None or not has_previous_course_target or s['id'] == session_id)]
        for s in targets:
            options = _session_candidates(initial, s, chosen) if chosen else []
            _mark_occupied_slots(conn,offering_id,s,initial,source,options)
            exact = [c for c in options if c['eligible'] and c['evidence']['method'] == 'exact_slot']
            selected = exact[0] if len(exact) == 1 else None
            if confirmation and confirmation['scope'] == 'session' and s['id'] == session_id:
                selected = next((c for c in options if c['eligible'] and c['id'] == confirmation['candidate_id']), None)
                if not selected:
                    raise HTTPException(409, '学校课次候选已变化，请重新对齐。')
                if selected['target'] != candidate['target']:
                    raise HTTPException(409, '学校课次记录已变化，请重新查看候选后确认。')
                selected['_slot']['alignment_method'] = 'manual'
            _save_session(conn, user, offering_id, initial, s, revision, source, options, selected)
        conn.commit()
        return flow.workflow(conn, user, offering_id, session_id) if session_id is not None else {'status': 'matched' if chosen else 'review' if candidates else 'unmatched', 'course_revision': revision, 'session_count': len(targets), 'message': message}


async def confirm_alignment(user, offering_id, session_id, payload):
    with get_db_connection() as conn:
        flow._lesson(conn, user, offering_id, session_id)
        row = conn.execute('SELECT request_hash FROM smart_attendance_alignment_requests WHERE owner_teacher_id=? AND request_key=?', (user['id'], payload['idempotency_key'])).fetchone()
        if row:
            if row['request_hash'] != ledger._hash({'offering_id': offering_id, 'session_id': session_id, **payload}):
                raise HTTPException(409, '该操作标识已用于其他确认，请刷新后重试。')
            return flow.workflow(conn, user, offering_id, session_id)
    return await align_offering(user, offering_id, session_id=session_id, confirmation=payload)


def assert_current_alignment(conn, user, offering_id, session_id, source, slot, expected_binding_revision=None):
    context = offering_alignment_context(conn, user, offering_id)
    course = _course_link(conn, user, offering_id)
    link = flow._link(conn, user, session_id)
    current = _local_session(context, session_id)
    if not course or not link or course['state'] != 'matched' or link['state'] != 'matched' or not current:
        raise HTTPException(409, '课程或课次尚未对齐，已暂停学校写入；本地记录保留。')
    if course['context_fingerprint'] != context['course_fingerprint'] or link['context_fingerprint'] != flow._identity(context['contexts'][session_id]) or link['course_link_revision'] != course['revision']:
        raise HTTPException(409, '课程、课表或名单已变化，请重新对齐后推送。')
    if expected_binding_revision is not None and expected_binding_revision != link['revision']:
        raise HTTPException(409, '课次绑定版本已变化，旧同步任务不能继续写入。')
    sealed_source, sealed_slot = _load(link['source_json']), _load(link['slot_json'])
    if source != sealed_source or _load(course['source_json']) != sealed_source or any(slot.get(k) != sealed_slot.get(k) for k in ('schedule_id', 'week', 'day_of_week', 'sections', 'local_session_identity', 'alignment_method')):
        raise HTTPException(409, '同步任务的课程课次身份与当前绑定不同，已暂停写入。')
    if slot.get('local_session_identity') != {k: v for k, v in current.items() if k != 'id'}:
        raise HTTPException(409, '当前课表顺延课次已变化，请重新对齐。')
    return {'course_revision': course['revision'], 'binding_revision': link['revision']}
