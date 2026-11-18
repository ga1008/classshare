"""Read school attendance for an exact semester lesson, then authorize pinned differences."""
from collections import Counter
from contextlib import AsyncExitStack, asynccontextmanager
import asyncio
import json

from fastapi import HTTPException

from ..database import get_db_connection
from . import login_attendance_service as local
from . import smart_classroom_alignment as rules
from . import smart_classroom_auto_sync_service as auto
from . import smart_classroom_miniapp_adapter as mini
from . import smart_classroom_workflow_service as flow
from . import smart_classroom_write_service as ledger
from .attendance_report_service import _decode_option, sign_attendance_source_option
from .login_attendance_calendar_service import semester_sessions
from .login_attendance_settlement_service import has_unpublished_settlement

PREVIEW_PURPOSE = 'attendance-calendar-preview-v1'


@asynccontextmanager
async def _account_turn(key):
    # Several selected lessons can share an account with an earlier lesson's
    # worker. Only retry acquisition; no request body or school write is replayed.
    async with AsyncExitStack() as stack:
        for attempt in range(11):
            try:
                lease = await stack.enter_async_context(ledger._account_lease(key))
                break
            except HTTPException as exc:
                if exc.status_code != 429 or attempt == 10:
                    raise
                await asyncio.sleep(3)
        yield lease
def _result(state, message, rows=(), **extra):
    return {'state': state, 'message': message, 'counts': dict(Counter(r['state'] for r in rows)),
            'rows': [{k: r[k] for k in ('student_id', 'local_status', 'remote_status', 'target_status', 'state', 'reason')}
                     for r in rows], **extra}


def _session(conn, user, offering_id, session_id, *, lock=False):
    offering = local.require_teacher(conn, offering_id, user, lock=lock)
    _, lessons = semester_sessions(conn, offering, session_id, lock=lock)
    return lessons[0]


def _round(conn, offering_id, session_id):
    return conn.execute("SELECT id,state FROM login_attendance_rounds WHERE class_offering_id=? AND session_id=? "
                        "AND is_current=1 AND state<>'void'", (offering_id, session_id)).fetchone()


def comparisons(ctx, snapshot, selection):
    if selection.get('bidirectional'):
        from .smart_classroom_reconciliation_service import compare_for_push
        return compare_for_push(ctx, snapshot, selection)
    # The signed preview pins both the eligible students and the observed school
    # values. Only collect targets within that scope, even after a worker restart.
    baseline = selection['baseline']
    ids = {int(sid) for sid in baseline}
    targets, _ = flow.comparison_targets(ctx, snapshot, selected_ids=ids)
    by_number = {t['student_number']: baseline[str(t['student_id'])] for t in targets}
    rows = ledger.compare_students(targets, by_number, snapshot['students'], school_authoritative=True)
    for row in rows:
        if row.get('preserved_remote'):
            row.update(state='protected', reason='学校修改时间较新或无法判定先后，已保留学校原值。')
        elif row['state'] == 'needs_teacher_action' and row['student_id'] not in selection.get('writable_ids', ids):
            row.update(state='conflict', reason='该学生不在本次预览的可写范围，请重新核对。')
    return targets, by_number, rows


async def preview(user, offering_id, session_id, student_ids):
    if student_ids is not None and len(set(student_ids)) != len(student_ids):
        raise HTTPException(422, '选择中含有重复学生。')
    return await read_lesson_snapshot(user, offering_id, session_id,
        lambda *args: _review_snapshot(user, offering_id, session_id, *args, student_ids))


async def read_lesson_snapshot(user, offering_id, session_id, consume):
    with get_db_connection() as conn:
        lesson = _session(conn, user, offering_id, session_id)
        if lesson['schedule_status'] in {'cancelled', 'canceled', 'suspended'}:
            return _result('blocked', '此课次已停课。')
        link = flow._link(conn, user, session_id)
        if not link or link['state'] != 'matched':
            return _result('alignment_required', '请先对齐此课次，再核对学校记录。')
        source, slot = json.loads(link['source_json']), json.loads(link['slot_json'])
        try:
            flow.assert_current_alignment(conn, user, offering_id, session_id, source, slot, link['revision'])
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
            return _result('alignment_required', str(exc.detail))
        access = ledger._account_access(conn, user['id'], source['external_account_key'])
        plan = conn.execute("SELECT remote_checkin_id FROM smart_attendance_auto_sync a JOIN login_attendance_rounds r "
                            "ON a.local_round_id=CAST(r.id AS TEXT) WHERE r.class_offering_id=? AND r.session_id=? "
                            "AND r.is_current=1 AND r.state<>'void'", (offering_id, session_id)).fetchone()
        remote_id = slot.get('remote_checkin_id') or (plan['remote_checkin_id'] if plan else None)
    async with _account_turn(source['external_account_key']) as lease:
        async with flow.open_authenticated_smart_classroom_client(access, api='mini') as (raw, _, _):
            snapshot = await mini.read_slot(auto._LeasedClient(raw, source['external_account_key'], lease), slot, source, remote_id)
        return consume(link['revision'], source, slot, access, snapshot)


def _review_snapshot(user, offering_id, session_id, binding_revision, source, slot, access, snapshot, student_ids):
    with get_db_connection() as conn:
        _session(conn, user, offering_id, session_id, lock=True)
        flow.assert_current_alignment(conn, user, offering_id, session_id, source, slot, binding_revision)
        ledger._account_access(conn, user['id'], source['external_account_key'], access['credential_generation'])
        current = _round(conn, offering_id, session_id)
        record = snapshot.get('record')
        if not record and not current:
            return _result('empty', '两端均无点名。')
        if not record and (student_ids is not None or not snapshot.get('can_checkin') or not mini.LIFECYCLE_VERIFIED):
            message = ('学校未点名。新建点名会涉及全班，需选择每课全班名单。' if student_ids is not None else
                       '仅本地有点名。学校尚未开放创建。')
            return _result('school_missing', message)
        roster = local._roster(conn, offering_id)
        numbers = [r['student_id_number'] for r in roster if not r.get('confirmed_retake')]
        if not rules.roster_authorized(rules.roster_evidence(numbers, snapshot['students']), source,
                                       ledger._hash(sorted(snapshot['students']))):
            return _result('alignment_required', '学校名单已变化，请重新对齐授课班。')
        ctx = ledger.load_read_context(conn, user, current['id']) if current else {
            'class_offering_id': offering_id, 'session_id': session_id,
            'students': [{'student_number': r['student_id_number'], 'name': r['name']} for r in roster]}
        if record:
            flow.persist_remote_snapshot(conn, user, ctx, source, snapshot)
        if not current:
            by_id = {r['id']: r['student_id_number'] for r in roster}
            by_id.update({r['student_id']: r['student_number'] for r in conn.execute(
                """SELECT st.student_id,st.student_number FROM smart_classroom_checkin_students st
                   JOIN smart_classroom_checkin_sessions c ON c.id=st.checkin_session_id
                   WHERE c.teacher_id=? AND c.class_offering_id=? AND c.session_id=? AND c.remote_checkin_id=?
                   AND c.platform_code='gxufl_smart_classroom' AND st.student_id IS NOT NULL AND st.local_match_status='matched'""",
                (user['id'], offering_id, session_id, str(record['id'])))})
            chosen = sorted(by_id) if student_ids is None else student_ids
            rows = []
            for sid in chosen:
                actual = snapshot['students'].get(by_id.get(sid))
                rows.append({'student_id': sid, 'local_status': None, 'target_status': None, 'remote_status': actual,
                             'state': 'school_only' if actual is not None else 'not_enrolled',
                             'reason': '已读取学校现值，本地尚未点名。' if actual is not None else '本课学校名单无对应学生。'})
            incomplete = any(r['state'] == 'not_enrolled' for r in rows)
            return _result('partial' if incomplete else 'school_only', '已读取学校点名，部分学生缺少对应记录。'
                           if incomplete else '已读取学校点名，可在日历查看。', rows)
        if current['state'] != 'published' or has_unpublished_settlement(conn, current['id']):
            return _result('local_pending', '等待本地结算发布。')
        if record and record.get('isStop') is not True:
            return _result('local_pending', '学校点名进行中，结束后可推送。')
        if ctx.get('closed'):
            return _result('blocked', '课程已结课。')
        if not record:
            targets = ledger._targets(ctx)
            pushable = sum(bool(r['target_status'] and r['student_number'] in snapshot['students']) for r in targets)
            if not pushable:
                return _result('blocked', '本课缺少可推送的已确认本地结果。')
            selection = {'kind': 'attendance_calendar_create_v1', 'session_id': session_id, 'offering_id': offering_id,
                         'round_id': current['id'], 'binding_revision': binding_revision, 'remote_checkin_id': None,
                         'local_fingerprint': ledger.result_fingerprint(ctx), 'roster_hash': ledger._hash(sorted(snapshot['students'])),
                         'credential_generation': access['credential_generation']}
            remainder = len(snapshot['students']) - pushable
            message = f'学校可新建全班点名。将推送 {pushable} 人的本地结果。'
            if remainder:
                message += f'另有 {remainder} 人缺少对应结果，保留学校新点名的初始状态。'
            return _result('create_available', message,
                           preview_token=sign_attendance_source_option(user['id'], selection, purpose=PREVIEW_PURPOSE))
        lookup = {row['student_id']: row for row in ctx['students']}
        chosen = sorted(lookup) if student_ids is None else sorted(student_ids)
        # Historical round membership may differ between lessons. Never broaden
        # a selected person to the whole class when absent from a past register.
        missing = [sid for sid in chosen if sid not in lookup]
        selection = {'kind': 'attendance_calendar_v1', 'session_id': session_id, 'offering_id': offering_id,
                     'round_id': current['id'], 'binding_revision': binding_revision,
                     'remote_checkin_id': str(record['id']), 'local_fingerprint': ledger.result_fingerprint(ctx),
                     'baseline': {str(sid): snapshot['students'].get(lookup[sid]['student_number']) for sid in chosen if sid in lookup}}
        _, _, rows = comparisons(ctx, snapshot, selection)
        rows.extend({'student_id': sid, 'local_status': None, 'remote_status': None, 'target_status': None,
                     'state': 'no_local_record', 'reason': '该学生不在本次有效本地点名名单。'} for sid in missing)
        writable = [r['student_id'] for r in rows if r['state'] == 'needs_teacher_action']
        if not writable:
            if rows and all(r['state'] == 'confirmed' for r in rows):
                return _result('confirmed', '所选学生已回读一致。', rows)
            return _result('partial', '学校记录已读取，请核对保留的差异。', rows)
        selection['writable_ids'] = writable
        selection['excluded_ids'] = missing
        return _result('ready', f'可推送 {len(writable)} 人。', rows,
                       preview_token=sign_attendance_source_option(user['id'], selection, purpose=PREVIEW_PURPOSE))


def _key(value):
    # Keep the ledger's worker-only namespace, including settlement interlocks
    # and the legacy teacher handoff's prohibition on modifying these runs.
    return 'auto:calendar:' + value


def status(conn, user, offering_id, session_id, key):
    _session(conn, user, offering_id, session_id)
    receipt = conn.execute("SELECT result_json FROM smart_attendance_calendar_receipts WHERE owner_teacher_id=? "
                           "AND class_offering_id=? AND session_id=? AND request_key=?", (user['id'], offering_id, session_id, key)).fetchone()
    if receipt:
        result = json.loads(receipt['result_json'])
        result['has_receipt'] = True
        if result['receipt']:
            job = _workflow_status(conn, user, offering_id, session_id, key)
            result.update(job)
            if result['rows']:
                operations = {r['student_id']: r for r in conn.execute(
                    "SELECT o.student_id,o.state,o.remote_status,o.reason FROM smart_attendance_sync_runs s "
                    "JOIN smart_attendance_sync_operations o ON o.run_id=s.id "
                    "JOIN login_attendance_rounds r ON CAST(r.id AS TEXT)=s.local_round_id "
                    "WHERE s.owner_teacher_id=? AND s.request_key=? AND r.class_offering_id=? AND r.session_id=?",
                    (user['id'], _key(key), offering_id, session_id))}
                for row in result['rows']:
                    op = operations.get(row['student_id'])
                    if op and row['state'] == 'needs_teacher_action':
                        row.update(state=op['state'], remote_status=op['remote_status'], reason=op['reason'])
                result['counts'] = dict(Counter(r['state'] for r in result['rows']))
            if result['state'] == 'confirmed' and result.get('requires_review'):
                result.update(state='partial', message='可同步的学生已完成，仍有待核对的记录。')
        return result
    return _workflow_status(conn, user, offering_id, session_id, key)


def _workflow_status(conn, user, offering_id, session_id, key):
    row = conn.execute("SELECT w.local_round_id FROM smart_attendance_workflow_requests w JOIN login_attendance_rounds r "
                       "ON CAST(r.id AS TEXT)=w.local_round_id WHERE w.owner_teacher_id=? AND w.request_key=? "
                       "AND r.class_offering_id=? AND r.session_id=?", (user['id'], _key(key), offering_id, session_id)).fetchone()
    if not row:
        raise HTTPException(404, '未找到本课的这次推送。')
    plan = conn.execute("SELECT * FROM smart_attendance_auto_sync WHERE local_round_id=? AND owner_teacher_id=?",
                        (row['local_round_id'], user['id'])).fetchone()
    run = conn.execute("SELECT id,state,message,baseline_json FROM smart_attendance_sync_runs WHERE owner_teacher_id=? "
                       "AND local_round_id=? AND request_key=?", (user['id'], row['local_round_id'], _key(key))).fetchone()
    historical = not plan or plan['request_key'] != _key(key)
    if historical and not run:
        return _result('superseded', '本课已启动另一推送任务，请重新核对当前结果。')
    state = run['state'] if historical else plan['state']
    message = run['message'] if historical else plan['message']
    counts = Counter()
    if run:
        counts.update({r['state']: r['n'] for r in conn.execute("SELECT o.state,COUNT(*) AS n "
                      "FROM smart_attendance_sync_operations o WHERE run_id=? GROUP BY o.state", (run['id'],))})
    if counts['outcome_unknown'] or (not historical and auto._lifecycle_unknown(plan)):
        state, message = 'outcome_unknown', '学校请求已提交，须回读本次结果。'
    excluded = (json.loads(run['baseline_json']).get('no_local_count', 0) if historical else
                len(json.loads(plan['intent_json']).get('calendar', {}).get('excluded_ids', [])))
    if excluded:
        counts['no_local_record'] += excluded
    if state == 'confirmed' and (excluded or counts.get('not_enrolled')):
        state = 'partial'
    state = {'pending': 'queued', 'needs_teacher_action': 'partial'}.get(state, state)
    return {'state': state, 'message': message or '已排队，等候学校回读。', 'counts': dict(counts), 'historical': historical}


def _selection(user, offering_id, session_id, token, kind):
    try:
        selection = _decode_option(token, user['id'], purpose=PREVIEW_PURPOSE)
    except HTTPException as exc:
        raise HTTPException(409, '本次预览已失效，请重新核对后推送。') from exc
    if (selection.get('kind'), selection.get('offering_id'), selection.get('session_id')) != (
            kind, offering_id, session_id):
        raise HTTPException(409, '预览不属于本课，请重新核对。')
    return selection


def _existing(conn, user, offering_id, session_id, selection, key):
    _session(conn, user, offering_id, session_id, lock=True)
    # A lost HTTP response can be recovered without authorizing any new write.
    receipt = conn.execute("SELECT context_fingerprint FROM smart_attendance_workflow_requests WHERE owner_teacher_id=? "
                           "AND request_key=?", (user['id'], _key(key))).fetchone()
    if receipt and receipt['context_fingerprint'] != ledger._hash(selection):
        raise HTTPException(409, '该请求标识已用于另一份预览。')
    return bool(receipt)


def dispatch(conn, user, offering_id, session_id, token, key):
    selection = _selection(user, offering_id, session_id, token, 'attendance_calendar_v1')
    if not _existing(conn, user, offering_id, session_id, selection, key):
        flow.enqueue_sync(conn, user, offering_id, selection['round_id'], {'expected_binding_revision': selection['binding_revision'],
                          'idempotency_key': _key(key)}, calendar=selection)
    return status(conn, user, offering_id, session_id, key)


async def create(user, offering_id, session_id, token, key, location):
    selection = _selection(user, offering_id, session_id, token, 'attendance_calendar_create_v1')
    with get_db_connection() as conn:
        if _existing(conn, user, offering_id, session_id, selection, key):
            return status(conn, user, offering_id, session_id, key)
        auto._location(location)
        link = flow._link(conn, user, session_id)
        if not link or link['revision'] != selection['binding_revision']:
            raise HTTPException(409, '课次对应已更新，请重新核对。')
        source, slot = json.loads(link['source_json']), json.loads(link['slot_json'])
        flow.assert_current_alignment(conn, user, offering_id, session_id, source, slot, link['revision'])
        access = ledger._account_access(conn, user['id'], source['external_account_key'], selection['credential_generation'])
    async with _account_turn(source['external_account_key']) as lease:
        async with flow.open_authenticated_smart_classroom_client(access, api='mini') as (raw, _, _):
            snapshot = await mini.read_slot(auto._LeasedClient(raw, source['external_account_key'], lease), slot, source)
        if snapshot.get('record') or not snapshot.get('can_checkin') or ledger._hash(sorted(snapshot['students'])) != selection['roster_hash']:
            raise HTTPException(409, '学校点名、名单或开放状态已变化，请重新预览。')
        with get_db_connection() as conn:
            if not _existing(conn, user, offering_id, session_id, selection, key):
                flow.enqueue_sync(conn, user, offering_id, selection['round_id'], {'expected_binding_revision': link['revision'],
                                  'idempotency_key': _key(key), 'location': location}, calendar=selection)
            return status(conn, user, offering_id, session_id, key)


async def readback(user, offering_id, session_id, key):
    with get_db_connection() as conn:
        current = status(conn, user, offering_id, session_id, key)
        if current['state'] != 'outcome_unknown' or current.get('historical'):
            return current
        plan = dict(conn.execute("SELECT * FROM smart_attendance_auto_sync WHERE owner_teacher_id=? AND request_key=?",
                                 (user['id'], _key(key))).fetchone())
        run = conn.execute("SELECT * FROM smart_attendance_sync_runs WHERE id=? AND request_key=?",
                           (plan['last_run_id'], _key(key))).fetchone()
        access = ledger._account_access(conn, user['id'], plan['external_account_key'])
    async with ledger._account_lease(plan['external_account_key']) as lease:
        with get_db_connection() as conn:
            current = status(conn, user, offering_id, session_id, key)
            if current['state'] != 'outcome_unknown' or current.get('historical'):
                return current
            _, plan = auto._plan(conn, user, plan['local_round_id'], lock=True)
            if plan['request_key'] != _key(key):
                return status(conn, user, offering_id, session_id, key)
            if access['credential_generation'] != plan['credential_generation'] and plan['enabled']:
                conn.execute("UPDATE smart_attendance_auto_sync SET enabled=0,state='outcome_unknown',revision=revision+1 "
                             "WHERE local_round_id=? AND revision=?", (plan['local_round_id'], plan['revision']))
                plan.update(enabled=0, revision=plan['revision']+1)
        async with flow.open_authenticated_smart_classroom_client(access, api='mini') as (raw, _, _):
            client = auto._LeasedClient(raw, plan['external_account_key'], lease)
            if run:
                result = await auto._reconcile_unknown(client, user, plan, dict(run), read_generation=access['credential_generation'])
            else:
                return await _read_creation_receipt(client, user, plan, offering_id, session_id, key)
        with get_db_connection() as conn:
            if result['state'] == 'running':
                # Remaining, never-submitted differences retain their original
                # authorization. A still-unknown operation cannot get here.
                auto.schedule_due_auto_sync(conn, plan['local_round_id'], force=True)
            return status(conn, user, offering_id, session_id, key)


async def _read_creation_receipt(client, user, plan, offering_id, session_id, key):
    intent = json.loads(plan['intent_json'])
    source, slot = json.loads(plan['source_json']), json.loads(plan['slot_json'])
    owned = plan['remote_checkin_id'] or intent.get('created_id')
    snapshot = await mini.read_slot(client, slot, source, owned)
    actual = snapshot.get('record') or {}
    verified = False
    if owned and actual and str(actual['id']) == str(owned):
        if intent.get('operation') == 'start' and intent.get('acknowledged'):
            history = await mini.read_history(client, slot['schedule_id'])
            verified = [str(r['id']) for r in history if str(r['id']) not in intent.get('before_ids', [])] == [str(owned)]
        elif intent.get('operation') == 'stop':
            verified = actual.get('isStop') is True
    if not verified:
        return _result('outcome_unknown', '学校结果尚不能证明本次操作。请在智慧课堂核实后，从本课设置核对此轮点名。')
    with get_db_connection() as conn:
        _, fresh = auto._plan(conn, user, plan['local_round_id'], lock=True)
        if fresh and fresh['request_key'] == _key(key) and fresh['revision'] == plan['revision']:
            intent['verified'] = True
            conn.execute("UPDATE smart_attendance_auto_sync SET remote_checkin_id=?,intent_json=?,state=?,message=?,updated_at=? "
                         "WHERE local_round_id=? AND revision=?", (str(owned), ledger._json(intent),
                          'pending' if fresh['enabled'] else 'paused', '学校操作已回读确认。', ledger._now(),
                          plan['local_round_id'], plan['revision']))
            if fresh['enabled']:
                auto.schedule_due_auto_sync(conn, plan['local_round_id'], force=True)
        return status(conn, user, offering_id, session_id, key)
