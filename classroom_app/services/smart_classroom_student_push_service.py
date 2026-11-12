"""One teacher correction, one durable intent, no school rollcall lifecycle.

The receipt commits with the local edit. A separate worker may only modify its
pinned student in an already existing, exactly aligned school rollcall. Unknown
submissions remain read-only even after another edit, transfer or cancellation.
"""
from __future__ import annotations

import asyncio
import copy
import concurrent.futures
import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta

import httpx
from fastapi import HTTPException

from ..database import get_db_connection
from . import login_attendance_service as local
from . import smart_classroom_write_service as ledger
from . import smart_classroom_workflow_service as workflow
from . import smart_classroom_miniapp_adapter as mini
from .smart_classroom_session_service import SmartClassroomSessionError
from .smart_classroom_attendance_adapter import AttendanceSourceError
from .smart_classroom_integration_service import open_authenticated_smart_classroom_client

TASK_KIND = 'smart_attendance_student_push'
ACTIVE_STATES = ('queued', 'checking', 'outcome_unknown')
ORDINARY_REMOTE = frozenset(ledger.LOCAL_STATUS_MAP.values())
_CHECK_CACHE = {}
_CHECK_FLIGHTS = {}
_CHECK_LOCK = threading.Lock()


def _error(code, message, status=409):
    return HTTPException(status, {'code': code, 'message': message})


def _load(conn, intent_id):
    row = conn.execute('SELECT * FROM smart_attendance_student_corrections WHERE id=?', (intent_id,)).fetchone()
    if not row:
        raise HTTPException(404, '改签任务不存在。')
    return dict(row)


def _record(conn, round_id, student_id):
    row = conn.execute('SELECT * FROM login_attendance_records WHERE round_id=? AND student_id=?', (round_id, student_id)).fetchone()
    if not row:
        raise HTTPException(404, '学生不在本次点名名单中。')
    return dict(row)


def _context(conn, user, offering_id, round_id, student_id, *, lock=False):
    if user.get('role') != 'teacher' or not conn.execute('SELECT 1 FROM teachers WHERE id=? AND COALESCE(is_active,1)=1', (user['id'],)).fetchone():
        raise HTTPException(403, '当前教师身份无效。')
    row = local.load_round(conn, round_id, user, lock=lock)
    if row['class_offering_id'] != offering_id:
        raise HTTPException(404, '点名不属于此课堂。')
    return row, _record(conn, round_id, student_id)


def assert_no_active_push(conn, round_id=None, *, offering_id=None, session_id=None):
    # Legacy isolated fixtures need not initialize an unrelated new subsystem;
    # production initialization still requires this table on both engines.
    if isinstance(conn, sqlite3.Connection) and not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='smart_attendance_student_corrections'").fetchone():
        return
    clauses, args = [], []
    for field, value in (('local_round_id', str(round_id) if round_id is not None else None), ('class_offering_id', offering_id), ('session_id', session_id)):
        if value is not None:
            clauses.append(field+'=?'); args.append(value)
    if not clauses:
        raise ValueError('A correction scope is required')
    if conn.execute("SELECT 1 FROM smart_attendance_student_corrections WHERE "+' AND '.join(clauses)+" AND mode='push' AND state IN ('queued','checking','outcome_unknown') LIMIT 1", tuple(args)).fetchone():
        raise _error('student_push_busy', '本轮有单学生改签正在处理或等待回读，请待其完成后再同步整班。')


def _public(intent):
    if not intent or intent['mode'] != 'push':
        return None
    return {k: intent[k] for k in ('id', 'state', 'code', 'message', 'local_revision', 'target_status', 'remote_checkin_id')} | {
        'can_retry': intent['state'] == 'outcome_unknown' or (intent['state'] in {'blocked', 'conflict', 'failed'} and bool(intent['external_account_key']) and bool(intent['remote_checkin_id'])),
        'readback_only': intent['state'] == 'outcome_unknown',
        'idempotency_key': intent['request_key'],
    }


def _gate(conn, user, row, record):
    gate = {'allowed': False, 'code': 'checking', 'message': '正在核对学校是否已开始本次点名。', 'binding_revision': None, 'remote_checkin_id': None}
    try:
        if not row['is_current'] or row['state'] == 'void' or not row['session_id']:
            raise _error('historical_round', '历史或作废轮次只能保存本地更正。')
        from .login_attendance_settlement_service import assert_no_unpublished_settlement
        assert_no_unpublished_settlement(conn, row['id'])
        link = workflow._link(conn, user, row['session_id'])
        if not link:
            raise _error('alignment_required', '请先对齐课程和课次，再推送此学生的更正。')
        source, slot = json.loads(link['source_json']), json.loads(link['slot_json'])
        workflow.assert_current_alignment(conn, user, row['class_offering_id'], row['session_id'], source, slot, link['revision'])
        ledger.require_write_capability('update_student')
        access = ledger._account_access(conn, user['id'], source['external_account_key'])
        if not record['student_number'] or conn.execute('SELECT COUNT(*) FROM login_attendance_records WHERE round_id=? AND student_number=?', (row['id'], record['student_number'])).fetchone()[0] != 1:
            raise _error('student_identity_invalid', '学生学号缺失或重复，请核对本地名单。')
        gate['binding_revision'] = link['revision']
        return gate, (source, slot, access)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {'code': 'alignment_required', 'message': str(exc.detail)}
        gate.update(code=detail.get('code', 'unavailable'), message=detail.get('message', '暂不能推送。'))
        return gate, None


def status(conn, user, offering_id, round_id, student_id, *, intent=None):
    row, record = _context(conn, user, offering_id, round_id, student_id)
    explicit_push = intent is not None and intent['mode'] == 'push'
    if intent is None:
        found = conn.execute('SELECT * FROM smart_attendance_student_corrections WHERE owner_teacher_id=? AND local_round_id=? AND student_id=? ORDER BY created_at DESC,id DESC LIMIT 1', (user['id'], str(round_id), student_id)).fetchone()
        intent = dict(found) if found else None
    pending = conn.execute("SELECT * FROM smart_attendance_student_corrections WHERE owner_teacher_id=? AND local_round_id=? AND student_id=? AND mode='push' AND state IN ('outcome_unknown','queued','checking') ORDER BY CASE WHEN state='outcome_unknown' THEN 0 ELSE 1 END,created_at DESC LIMIT 1", (user['id'], str(round_id), student_id)).fetchone()
    gate, _ = _gate(conn, user, row, record)
    if conn.execute("SELECT 1 FROM smart_attendance_student_corrections WHERE local_round_id=? AND mode='push' AND state IN ('queued','checking','outcome_unknown') LIMIT 1", (str(round_id),)).fetchone():
        gate.update(allowed=False, code='student_push_busy', message='本轮单学生更正正在处理或回读；仍可保存本地修改。')
    shown = intent if explicit_push else dict(pending) if pending else intent
    push = _public(shown)
    if push:
        push['is_current_decision'] = _same_decision(conn, record, shown)
    return {'local_saved': bool(intent), 'saved_record_revision': intent['local_revision'] if intent else None,
            'record': local._public_record(record, teacher=True), 'push': push, 'gate': gate}


def _queue(conn, intent, *, delay=0):
    from .scheduled_task_service import schedule_task
    seq = intent['dispatch_seq'] + 1
    conn.execute('UPDATE smart_attendance_student_corrections SET dispatch_seq=? WHERE id=?', (seq, intent['id']))
    schedule_task(conn, task_kind=TASK_KIND, run_at=datetime.now()+timedelta(seconds=delay),
                  payload={'intent_id': intent['id'], 'teacher_id': intent['owner_teacher_id']},
                  dedupe_key=f"student-push:{intent['id']}:{seq}", replace=False, max_attempts=3,
                  owner_role='teacher', owner_user_pk=intent['owner_teacher_id'], title='推送单学生考勤更正')


def _revoke_batch(conn, round_id):
    from . import smart_classroom_auto_sync_service as auto
    plan = conn.execute('SELECT * FROM smart_attendance_auto_sync WHERE local_round_id=?', (str(round_id),)).fetchone()
    if not plan:
        return
    unknown = auto._lifecycle_unknown(plan) or plan['state'] == 'outcome_unknown' or bool(plan['last_run_id'] and conn.execute("SELECT 1 FROM smart_attendance_sync_operations WHERE run_id=? AND state='outcome_unknown'", (plan['last_run_id'],)).fetchone())
    conn.execute('UPDATE smart_attendance_auto_sync SET enabled=0,revision=revision+1,state=?,message=?,updated_at=? WHERE local_round_id=?',
                 ('outcome_unknown' if unknown else 'awaiting_push', '学生更正已保存到本地；旧整班推送授权已撤销，已提交请求仅继续回读。', ledger._now(), str(round_id)))
    if unknown:
        auto.schedule_due_auto_sync(conn, round_id, force=True)


def correct(conn, user, offering_id, round_id, student_id, payload):
    row, record = _context(conn, user, offering_id, round_id, student_id, lock=True)
    key = str(payload['idempotency_key'])
    fingerprint = ledger._hash({'offering_id': offering_id, 'round_id': round_id, 'student_id': student_id, **payload})
    previous = conn.execute('SELECT * FROM smart_attendance_student_corrections WHERE owner_teacher_id=? AND request_key=?', (user['id'], key)).fetchone()
    if previous:
        if previous['request_hash'] != fingerprint:
            raise _error('idempotency_conflict', '此操作标识已用于其他更正，请刷新后重新提交。')
        return {**status(conn, user, offering_id, round_id, student_id, intent=dict(previous)), 'round': local.round_view(conn, round_id, user)}
    if record['revision'] != payload['revision']:
        raise _error('record_conflict', '该学生记录已更新，请刷新后核对；当前选择未保存。')
    # Local-only remains legal for retained merged history, like the original
    # correction endpoint. Only a push needs a current alignment.
    gate, context = _gate(conn, user, row, record)
    if payload['mode'] == 'push' and (context is None or payload.get('expected_binding_revision') != gate['binding_revision']):
        context = None
        gate.update(code=gate['code'] if gate['code'] != 'checking' else 'alignment_changed', message=gate['message'] if gate['code'] != 'checking' else '课次对齐版本已变化，请刷新后再推送。')
    _revoke_batch(conn, round_id)
    # Supersede only unsent earlier authorizations for this student. A submitted
    # operation keeps its exact target and can only be read back.
    for previous_push in conn.execute("SELECT * FROM smart_attendance_student_corrections WHERE local_round_id=? AND student_id=? AND mode='push' AND state NOT IN ('outcome_unknown','confirmed','superseded')", (str(round_id), student_id)).fetchall():
        _set(conn, dict(previous_push), 'superseded', 'local_changed', '本地已保存更新的更正，旧目标不再发送。')
    changed = local.change_records(conn, round_id, user, {'records': [{'student_id': student_id, 'status': payload['status'], 'revision': payload['revision']}], 'reason': payload['reason']})
    if changed['results'][0]['result'] != 'updated':
        raise _error('record_conflict', '该学生记录已更新，请刷新后核对。')
    saved = _record(conn, round_id, student_id)
    source, slot, access = context if context else ({}, {}, {})
    expected_remote = payload.get('expected_remote_checkin_id') or slot.get('remote_checkin_id')
    if payload['mode'] == 'push' and context and not expected_remote:
        context = None
        gate.update(code='school_check_required', message='请先核对学校是否已开始点名，再推送；本地更正已保存。')
    state = 'local_only' if payload['mode'] == 'local' else 'queued' if context else 'blocked'
    intent_id, now = ledger._id(), ledger._now()
    inserted = conn.execute('''INSERT INTO smart_attendance_student_corrections(id,owner_teacher_id,request_key,request_hash,
        class_offering_id,session_id,origin_scope_json,local_round_id,student_id,student_number,local_revision,target_status,mode,state,
        source_json,slot_json,binding_revision,external_account_key,credential_generation,remote_checkin_id,code,message,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(owner_teacher_id,request_key) DO NOTHING RETURNING id''',
        (intent_id, user['id'], key, fingerprint, offering_id, row['session_id'], ledger._json({'offering_id': offering_id, 'session_id': row['session_id'], 'round_id': round_id, 'student_id': student_id}),
         str(round_id), student_id, saved['student_number'], saved['revision'], payload['status'], payload['mode'], state,
         ledger._json(source), ledger._json(slot), gate['binding_revision'], source.get('external_account_key'), access.get('credential_generation'), expected_remote,
         gate['code'] if state == 'blocked' else '', gate['message'] if state == 'blocked' else '本地已保存。' if state == 'local_only' else '本地已保存，正在排队推送此学生。', now, now)).fetchone()
    if not inserted:
        raise _error('idempotency_conflict', '此操作标识正由另一请求处理，请使用原请求重试。')
    intent = _load(conn, intent_id)
    if state == 'queued':
        _queue(conn, intent)
    return {**status(conn, user, offering_id, round_id, student_id, intent=intent), 'round': changed['round']}


def _same_decision(conn, record, intent):
    decision = conn.execute("SELECT after_json FROM login_attendance_audit WHERE record_id=? AND action='manual_decision' ORDER BY id DESC LIMIT 1", (record['id'],)).fetchone()
    # Evidence collection may advance a record revision without changing the
    # teacher decision. The immutable manual audit pins the authorized revision.
    decision_revision = json.loads(decision['after_json']).get('revision') if decision else record['revision']
    return decision_revision == intent['local_revision'] and bool(record['manual_confirmed']) and record['status'] == intent['target_status'] and record['student_number'] == intent['student_number']


def _current(conn, user, intent, *, lock=False):
    row, record = _context(conn, user, intent['class_offering_id'], int(intent['local_round_id']), intent['student_id'], lock=lock)
    from .login_attendance_settlement_service import assert_no_unpublished_settlement
    assert_no_unpublished_settlement(conn, row['id'])
    if not row['is_current'] or row['state'] == 'void':
        raise _error('historical_round', '本轮已作废或替代，停止推送；本地更正保留。')
    if not _same_decision(conn, record, intent):
        raise _error('local_changed', '本地记录已更新，旧更正不再发送。')
    workflow.assert_current_alignment(conn, user, row['class_offering_id'], row['session_id'], json.loads(intent['source_json']), json.loads(intent['slot_json']), intent['binding_revision'])
    ledger._account_access(conn, user['id'], intent['external_account_key'], intent['credential_generation'])
    return row, record


def _other_unknown(conn, intent):
    return conn.execute("SELECT 1 FROM smart_attendance_sync_runs r JOIN smart_attendance_write_bindings b ON b.id=r.binding_id WHERE b.external_account_key=? AND r.state='outcome_unknown' AND r.id<>? LIMIT 1", (intent['external_account_key'], intent.get('run_id') or '')).fetchone() or conn.execute("SELECT 1 FROM smart_attendance_auto_sync WHERE external_account_key=? AND state='outcome_unknown' LIMIT 1", (intent['external_account_key'],)).fetchone()


def _validate_remote(conn, user, row, record, source, snapshot):
    remote = snapshot.get('record')
    if not remote or not remote.get('id'):
        raise _error('school_not_started', '学校尚未开始本次点名；请在学校开始点名后重试推送，本地更正已保留。')
    from .smart_classroom_alignment import roster_evidence, roster_authorized
    numbers = [r['student_number'] for r in conn.execute('SELECT student_number FROM login_attendance_records WHERE round_id=?', (row['id'],)).fetchall()]
    students = snapshot['students']
    if not roster_authorized(roster_evidence(numbers, students), source, ledger._hash(sorted(students))):
        raise _error('roster_changed', '学校名单已超出已对齐范围，请重新核对课程；本地更正保留。')
    actual = students.get(record['student_number'])
    if actual is None:
        raise _error('student_not_enrolled', '学校本次名单中没有这名学生；本地更正保留，不会向学校添加学生。')
    if actual not in ORDINARY_REMOTE:
        raise _error('protected_remote_status', '学校记录为研学、实践、志愿或未知状态，请先在学校核对；本地更正保留。')
    return actual


async def _check_snapshot(source, slot, access, teacher_id, binding_revision):
    key = ledger._hash([teacher_id, source, slot, access['credential_generation'], binding_revision])
    with _CHECK_LOCK:
        now = time.monotonic()
        cached = _CHECK_CACHE.get(key)
        if cached and cached[0] > now:
            return copy.deepcopy(cached[1]), False
        flight = _CHECK_FLIGHTS.get(key)
        leader = flight is None
        if leader:
            if len(_CHECK_FLIGHTS) >= 128:
                raise _error('session_busy', '学校核对请求较多，请稍后重试。', 429)
            flight = concurrent.futures.Future()
            _CHECK_FLIGHTS[key] = flight
    if not leader:
        return copy.deepcopy(await asyncio.shield(asyncio.wrap_future(flight))), False
    try:
        async with ledger._account_lease(source['external_account_key']) as token:
            async with open_authenticated_smart_classroom_client(access, api='mini', allow_login=True) as (client, _, _):
                from .smart_classroom_auto_sync_service import _LeasedClient
                client = _LeasedClient(client, source['external_account_key'], token)
                snapshot = await mini.read_slot(client, slot, source, slot.get('remote_checkin_id'))
                snapshot['fetched_at'] = ledger._now()
        with _CHECK_LOCK:
            for old in [k for k, v in _CHECK_CACHE.items() if v[0] <= time.monotonic()]:
                del _CHECK_CACHE[old]
            if len(_CHECK_CACHE) >= 128:
                del _CHECK_CACHE[next(iter(_CHECK_CACHE))]
            # The teacher may start school attendance immediately after this
            # check. A negative observation must be refreshable at once.
            if snapshot.get('record'):
                _CHECK_CACHE[key] = (time.monotonic()+30, copy.deepcopy(snapshot))
        flight.set_result(snapshot)
        return copy.deepcopy(snapshot), True
    except BaseException as exc:
        flight.set_exception(exc)
        raise
    finally:
        with _CHECK_LOCK:
            _CHECK_FLIGHTS.pop(key, None)


async def check(user, offering_id, round_id, student_id):
    with get_db_connection() as conn:
        row, record = _context(conn, user, offering_id, round_id, student_id)
        result = status(conn, user, offering_id, round_id, student_id)
        gate, context = _gate(conn, user, row, record)
        if result['gate']['code'] == 'student_push_busy':
            return result
    if context is None:
        return result
    source, slot, access = context
    try:
        snapshot, fresh_read = await _check_snapshot(source, slot, access, user['id'], gate['binding_revision'])
        with get_db_connection() as conn:
            fresh, current = _context(conn, user, offering_id, round_id, student_id)
            workflow.assert_current_alignment(conn, user, offering_id, fresh['session_id'], source, slot, gate['binding_revision'])
            ledger._account_access(conn, user['id'], source['external_account_key'], access['credential_generation'])
            _validate_remote(conn, user, fresh, current, source, snapshot)
            assert_no_active_push(conn, round_id)
            if fresh_read:
                _project_snapshot(conn, user, offering_id, round_id, student_id, source, slot, gate['binding_revision'], snapshot)
                conn.commit()
        gate.update(allowed=True, code='ready', message='仅推送此学生；不会开始或结束学校点名。', remote_checkin_id=str(snapshot['record']['id']))
    except (HTTPException, SmartClassroomSessionError, AttendanceSourceError, httpx.HTTPError, TimeoutError) as exc:
        code, message = _diagnostic(exc)
        for saved_phrase in ('本地更正已保存', '本地更正已保留', '本地更正保留', '本地已保存'):
            message = message.replace(saved_phrase, '可继续保存本地')
        gate.update(allowed=False, code=code, message=message)
    result['gate'] = gate
    return result


def retry(conn, user, offering_id, round_id, student_id, intent_id):
    _context(conn, user, offering_id, round_id, student_id, lock=True)
    intent = _load(conn, intent_id)
    if intent['owner_teacher_id'] != user['id'] or intent['local_round_id'] != str(round_id) or intent['student_id'] != student_id:
        raise HTTPException(404, '改签任务不属于此学生。')
    if intent['state'] in {'queued', 'checking', 'confirmed', 'local_only', 'superseded'}:
        return status(conn, user, offering_id, round_id, student_id, intent=intent)
    if intent['state'] != 'outcome_unknown':
        # Same intent may resume only the same sealed alignment and local revision.
        # Re-alignment requires a new explicit correction authorization.
        _current(conn, user, intent)
        if not intent['remote_checkin_id']:
            raise _error('school_check_required', '请核对学校点名后重新明确保存并推送。')
        conn.execute("UPDATE smart_attendance_student_corrections SET state='queued',code='',message=?,updated_at=? WHERE id=?", ('正在重试此学生的学校推送，本地记录不会重复保存。', ledger._now(), intent_id))
    _queue(conn, intent)
    return status(conn, user, offering_id, round_id, student_id, intent=_load(conn, intent_id))


def _diagnostic(exc):
    if isinstance(exc, HTTPException):
        if exc.status_code == 429:
            return 'session_busy', '学校账号正在处理其他操作，系统会稍后继续；本地已保存。'
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        return detail.get('code', 'conflict'), detail.get('message', '课程、账号或本地记录已变化，请刷新后核对。')
    code = getattr(exc, 'code', '')
    messages = {'session_busy': '学校账号正在处理其他操作，稍后重试；本地已保存。',
                'needs_teacher_reauth': '学校登录正在自动恢复，稍后重试；本地已保存。',
                'session_refreshed': '学校登录已自动恢复，正在重新核对后继续。',
                'captcha_required': '学校登录需要验证码，请完成学校验证后重试；本地已保存。',
                'missing_credential': '请先配置智慧课堂账号密码；本地已保存。',
                'credential_invalid': '学校账号验证未通过，请检查账号配置；本地已保存。',
                'round_replaced': '学校已替换本次点名，旧目标已停止推送，请重新核对。',
                'round_identity_changed': '学校点名身份与已对齐课次不一致，已停止推送。',
                'slot_identity_changed': '学校学期、课程或排课身份已变化，请重新核对。',
                'slot_section_changed': '学校完整节次已变化，请重新对齐课次。',
                'duplicate_student': '学校名单的学号缺失或重复，已停止推送，请核对学校名单。',
                'invalid_roster': '学校名单不完整或超出核对范围，已停止推送，请核对学校名单。',
                'credential_changed': '学校账号配置已变化，请重新核对当前账号与推送授权。'}
    return (code, messages[code]) if code in messages else ('school_unavailable', '学校连接暂不可用，本地更正已保存；请稍后重试。')


def _set(conn, intent, state, code='', message='', actual=None):
    conn.execute('UPDATE smart_attendance_student_corrections SET state=?,code=?,message=?,updated_at=? WHERE id=?', (state, code, message, ledger._now(), intent['id']))
    if intent.get('run_id'):
        run_state = {'queued': 'pending', 'checking': 'pending', 'blocked': 'needs_teacher_action'}.get(state, state)
        conn.execute('UPDATE smart_attendance_sync_runs SET state=?,message=?,revision=revision+1,updated_at=? WHERE id=?', (run_state, message, ledger._now(), intent['run_id']))
        conn.execute('UPDATE smart_attendance_sync_operations SET state=?,remote_status=?,reason=?,updated_at=? WHERE run_id=?', (state, actual, message, ledger._now(), intent['run_id']))
        ledger._event(conn, intent['run_id'], 'student_push_'+state, intent['owner_teacher_id'], {'code': code, 'remote_status': actual})


def _project_snapshot(conn, user, offering_id, round_id, student_id, source, slot, binding_revision, snapshot):
    """Projection is secondary to the durable result, never permission to resend.

    A teacher may edit the local decision after submission; that does not erase
    the verified school fact. A moved/archived/rebound scope must not map that
    fact onto a new lesson. Savepoint failure preserves the submission ledger.
    """
    conn.execute('SAVEPOINT student_push_projection')
    try:
        row, _ = _context(conn, user, offering_id, int(round_id), student_id, lock=True)
        if not row['is_current'] or row['state'] == 'void' or not snapshot.get('record'):
            raise _error('historical_round', '历史轮次不更新现课堂学校投影。')
        workflow.assert_current_alignment(conn, user, offering_id, row['session_id'], source, slot, binding_revision)
        previous = conn.execute('SELECT synced_at FROM smart_classroom_checkin_sessions WHERE teacher_id=? AND remote_checkin_id=?', (user['id'], str(snapshot['record']['id']))).fetchone()
        if previous and snapshot.get('fetched_at') and previous['synced_at'] > snapshot['fetched_at']:
            # A later account-serialized read/write may have been projected
            # after this preflight released its lease. Never regress its facts.
            conn.execute('RELEASE SAVEPOINT student_push_projection')
            return True
        ctx = local.load_attendance_sync_context(conn, user, int(round_id))
        workflow.persist_remote_snapshot(conn, user, ctx, source, snapshot)
    except Exception:
        conn.execute('ROLLBACK TO SAVEPOINT student_push_projection')
        conn.execute('RELEASE SAVEPOINT student_push_projection')
        return False
    conn.execute('RELEASE SAVEPOINT student_push_projection')
    return True


def _project_intent(conn, user, intent, snapshot):
    updated = _project_snapshot(conn, user, intent['class_offering_id'], intent['local_round_id'], intent['student_id'],
        json.loads(intent['source_json']), json.loads(intent['slot_json']), intent['binding_revision'], snapshot)
    if not updated and intent.get('run_id'):
        ledger._event(conn, intent['run_id'], 'student_projection_skipped', user['id'], {'reason': 'projection_unavailable_or_scope_changed'})


def _ledger_run(conn, user, intent, snapshot, actual, access):
    """Reuse the immutable binding and ordinary operation/event ledger."""
    remote_id = str(snapshot['record']['id'])
    binding = conn.execute("SELECT * FROM smart_attendance_write_bindings WHERE external_account_key=? AND remote_checkin_id=? AND state<>'released'", (intent['external_account_key'], remote_id)).fetchone()
    now = ledger._now()
    if binding:
        if binding['owner_teacher_id'] != user['id'] or binding['local_round_id'] != intent['local_round_id'] or binding['state'] != 'active' or binding['credential_generation'] != intent['credential_generation']:
            raise _error('remote_round_busy', '学校点名已属于另一轮记录或绑定已变化，请先核对。')
        binding_id, binding_revision = binding['id'], binding['revision']
    else:
        binding_id, binding_revision = ledger._id(), 1
        conn.execute('''INSERT INTO smart_attendance_write_bindings(id,owner_teacher_id,class_offering_id,session_id,local_round_id,external_account_key,credential_id,credential_generation,source_json,remote_checkin_id,remote_snapshot_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (binding_id, user['id'], intent['class_offering_id'], intent['session_id'], intent['local_round_id'], intent['external_account_key'], access['credential_id'], intent['credential_generation'], intent['source_json'], remote_id, ledger._json(snapshot), now, now))
    if intent['run_id']:
        return intent['run_id']
    run_id = ledger._id()
    target = {'student_id': intent['student_id'], 'student_number': intent['student_number'], 'local_status': intent['target_status'], 'target_status': ledger.LOCAL_STATUS_MAP[intent['target_status']], 'revision': intent['local_revision']}
    conn.execute('''INSERT INTO smart_attendance_sync_runs(id,binding_id,owner_teacher_id,local_round_id,local_revision,context_fingerprint,credential_generation,binding_revision,target_json,baseline_json,state,request_key,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,'pending',?,?,?)''',
        (run_id, binding_id, user['id'], intent['local_round_id'], intent['local_revision'], ledger._hash(target), intent['credential_generation'], binding_revision, ledger._json([target]), ledger._json(snapshot), 'student:'+intent['id'], now, now))
    conn.execute('''INSERT INTO smart_attendance_sync_operations(id,run_id,student_id,student_number,operation_kind,target_revision,baseline_status,target_status,remote_status,state,reason,updated_at) VALUES(?,?,?,?,'student_status',?,?,?,?, 'pending','',?)''',
        (ledger._id(), run_id, intent['student_id'], intent['student_number'], intent['local_revision'], actual, target['target_status'], actual, now))
    conn.execute('UPDATE smart_attendance_student_corrections SET run_id=?,remote_checkin_id=?,baseline_status=? WHERE id=?', (run_id, remote_id, actual, intent['id']))
    ledger._event(conn, run_id, 'student_push_authorized', user['id'], {'intent_id': intent['id'], 'student_id': intent['student_id'], 'local_revision': intent['local_revision']})
    return run_id


async def process(teacher_id, intent_id):
    user = {'role': 'teacher', 'id': int(teacher_id)}
    with get_db_connection() as conn:
        intent = _load(conn, intent_id)
        if intent['owner_teacher_id'] != user['id']:
            raise HTTPException(403, '任务教师不匹配。')
        if intent['mode'] != 'push' or intent['state'] not in ACTIVE_STATES:
            return _public(intent)
    try:
        with get_db_connection() as conn:
            if not conn.execute('SELECT 1 FROM teachers WHERE id=? AND COALESCE(is_active,1)=1', (teacher_id,)).fetchone():
                raise _error('teacher_inactive', '当前教师账号已停用，停止学校连接；本地更正和原提交记录保留。', 403)
            # Updating the password for the SAME school account must not make
            # a previously submitted request impossible to read back. The old
            # authorization stays sealed; only the read-only unknown branch
            # can use a newer credential generation.
            access = ledger._account_access(conn, teacher_id, intent['external_account_key'], None if intent['state'] == 'outcome_unknown' else intent['credential_generation'])
        async with ledger._account_lease(intent['external_account_key']) as token:
            # Reread after acquiring the account lease: a previous worker may
            # already have committed a result while this handler was waiting.
            with get_db_connection() as conn:
                intent = _load(conn, intent_id)
                if intent['state'] not in ACTIVE_STATES:
                    return _public(intent)
                if intent['state'] != 'outcome_unknown':
                    _current(conn, user, intent, lock=True)
                    if _other_unknown(conn, intent):
                        raise _error('previous_outcome_unknown', '旧学校请求仍在回读核实，此学生已保存本地，请先等待核实完成。')
                    _set(conn, intent, 'checking', message='正在核对准确学校轮次及此学生记录。')
                    conn.commit()
                elif access['credential_generation'] != intent['credential_generation'] and intent.get('run_id'):
                    ledger._event(conn, intent['run_id'], 'student_readback_current_credentials', user['id'], {'same_account': True, 'readback_only': True})
                    conn.commit()
            async with open_authenticated_smart_classroom_client(access, api='mini', allow_login=True) as (client, _, _):
                from .smart_classroom_auto_sync_service import _LeasedClient
                client = _LeasedClient(client, intent['external_account_key'], token)
                source, slot = json.loads(intent['source_json']), json.loads(intent['slot_json'])
                snapshot = await mini.read_slot(client, slot, source, intent['remote_checkin_id'])
                snapshot['fetched_at'] = ledger._now()
                desired = ledger.LOCAL_STATUS_MAP[intent['target_status']]
                if intent['state'] == 'outcome_unknown':
                    actual = snapshot['students'].get(intent['student_number'])
                    with get_db_connection() as conn:
                        fresh = _load(conn, intent_id)
                        if fresh['state'] == 'outcome_unknown':
                            state = 'confirmed' if actual == desired else 'outcome_unknown'
                            _set(conn, fresh, state, '' if state == 'confirmed' else 'outcome_unknown', '已回读确认此学生的学校状态。' if state == 'confirmed' else '原提交结果仍不能确定，仅继续回读，不会重复发送。', actual)
                            _project_intent(conn, user, fresh, snapshot)
                            conn.commit()
                    return _public(_read(intent_id))
                with get_db_connection() as conn:
                    fresh = _load(conn, intent_id)
                    row, record = _current(conn, user, fresh, lock=True)
                    if fresh['state'] != 'checking':
                        return _public(fresh)
                    actual = _validate_remote(conn, user, row, record, source, snapshot)
                    if fresh['run_id'] and fresh['baseline_status'] != actual and actual != desired:
                        raise _error('remote_changed', '学校此学生状态已发生新的变化，请刷新核对后重新明确更正。')
                    if _other_unknown(conn, fresh):
                        raise _error('previous_outcome_unknown', '其他学校请求结果仍未知，请先回读核实。')
                    run_id = _ledger_run(conn, user, fresh, snapshot, actual, access)
                    fresh = _load(conn, intent_id)
                    _project_intent(conn, user, fresh, snapshot)
                    if actual == desired:
                        _set(conn, fresh, 'confirmed', message='学校此学生状态已一致，无需改签。', actual=actual)
                        conn.commit()
                        return _public(_load(conn, intent_id))
                    # Commit exact intent BEFORE the only external mutation.
                    # A crash after this point can only lead to a readback.
                    ledger.require_write_capability('update_student')
                    _set(conn, fresh, 'outcome_unknown', 'submission_pending', '已提交此学生更正，正在回读确认。', actual)
                    conn.commit()
                    intent = fresh
                try:
                    await mini.change_student(client, intent['remote_checkin_id'], intent['student_number'], desired)
                except (SmartClassroomSessionError, AttendanceSourceError) as exc:
                    if exc.code == 'session_refreshed':
                        # Protocol proves the write was rejected with 401, so
                        # this operation alone may be retried after a new read.
                        with get_db_connection() as conn:
                            fresh = _load(conn, intent_id)
                            try:
                                _current(conn, user, fresh, lock=True)
                            except HTTPException:
                                _set(conn, fresh, 'superseded', 'authorization_changed', '学校已明确拒绝旧请求，且本地授权已变化；不会重发。')
                            else:
                                if fresh['dispatch_seq'] < 3:
                                    _set(conn, fresh, 'queued', 'session_refreshed', '学校登录已恢复，重新回读后继续此学生。')
                                    _queue(conn, fresh, delay=2)
                                else:
                                    _set(conn, fresh, 'failed', 'session_refreshed', '学校会话连续变化，暂缓推送；请稍后核对并重试。')
                            conn.commit()
                        return _public(_read(intent_id))
                    raise
                # Exact id and full scope are checked again; no latest-round
                # substitution and no writes to any other student.
                after = await mini.read_slot(client, slot, source, intent['remote_checkin_id'])
                after['fetched_at'] = ledger._now()
                actual = after['students'].get(intent['student_number'])
                with get_db_connection() as conn:
                    fresh = _load(conn, intent_id)
                    state = 'confirmed' if actual == desired else 'outcome_unknown'
                    _set(conn, fresh, state, '' if state == 'confirmed' else 'outcome_unknown', '本地已保存，学校已回读确认此学生状态。' if state == 'confirmed' else '学校尚未确认更正结果，只能继续回读核实。', actual)
                    _project_intent(conn, user, fresh, after)
                    if state == 'outcome_unknown' and fresh['dispatch_seq'] < 2:
                        _queue(conn, fresh, delay=5)
                    conn.commit()
    except (HTTPException, SmartClassroomSessionError, AttendanceSourceError, httpx.HTTPError, TimeoutError) as exc:
        code, message = _diagnostic(exc)
        with get_db_connection() as conn:
            fresh = _load(conn, intent_id)
            if fresh['state'] == 'outcome_unknown':
                _set(conn, fresh, 'outcome_unknown', code, '学校提交结果尚未核实；本地已保存，重试只会回读。')
                if fresh['dispatch_seq'] < 2:
                    _queue(conn, fresh, delay=5)
            elif fresh['state'] in ACTIVE_STATES:
                state = 'superseded' if code == 'local_changed' else 'blocked' if isinstance(exc, HTTPException) else 'failed'
                _set(conn, fresh, state, code, message)
                if code in {'session_busy', 'needs_teacher_reauth'} and fresh['dispatch_seq'] < 3:
                    _set(conn, fresh, 'queued', code, message)
                    _queue(conn, fresh, delay=65)
            conn.commit()
    return _public(_read(intent_id))


def _read(intent_id):
    with get_db_connection() as conn:
        return _load(conn, intent_id)
