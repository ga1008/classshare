"""Durable immediate settlement with cancellable generations and short locks.

Cancellation revokes publication authority; it never kills a SQL thread.
Prepared calculations stay in memory until one atomic, version-fenced commit.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime

from fastapi import HTTPException

from ..database import get_db_connection
from . import login_attendance_service as local

TASK_KIND = 'login_attendance_settle'
UNPUBLISHED = {'queued', 'running', 'failed'}
STEPS = 4


def _error(code, message, status=409):
    return HTTPException(status, {'code': code, 'message': message})


def _ready(conn):
    if isinstance(conn, sqlite3.Connection):
        return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='login_attendance_settlement_runs'").fetchone())
    return True  # PostgreSQL startup requires the additive schema.


def _latest(conn, round_id):
    if not _ready(conn):
        return None
    row = conn.execute('SELECT * FROM login_attendance_settlement_runs WHERE round_id=? ORDER BY generation DESC LIMIT 1', (int(round_id),)).fetchone()
    return dict(row) if row else None


def has_unpublished_settlement(conn, round_id):
    task = _latest(conn, round_id)
    return bool(task and task['state'] in UNPUBLISHED)


def assert_no_unpublished_settlement(conn, round_id):
    if has_unpublished_settlement(conn, round_id):
        raise _error('settlement_pending', '本轮立即结算尚未完成，请等待完成或重新结算；本地人工更正可以继续保存。')


def _active_teacher(conn, user):
    if user.get('role') != 'teacher' or not conn.execute('SELECT 1 FROM teachers WHERE id=? AND COALESCE(is_active,1)=1', (user['id'],)).fetchone():
        raise _error('teacher_inactive', '当前教师身份无效，不能执行结算。', 403)


def _round(conn, user, round_id, *, lock=False, offering_id=None):
    _active_teacher(conn, user)
    row = local.load_round(conn, round_id, user, lock=lock)
    if offering_id is not None and row['class_offering_id'] != offering_id:
        raise HTTPException(404, '点名不属于此课堂。')
    return row


def _public(task):
    if task is None:
        return None
    return {key: task[key] for key in ('id','generation','revision','state','stage','completed','total','message','updated_at_ms')} | {
        'percent': round(task['completed']*100/task['total']) if task['completed'] > 0 else None,
    }


def _gate(conn, row, task, *, timestamp=None):
    now = local.now_ms() if timestamp is None else timestamp
    gate = {'can_start':False, 'can_restart':False, 'code':'ready', 'message':'立即结束记录并结算当前已收到的证据，不再等待补报。'}
    if not row['is_current'] or row['state'] == 'void' or not row['class_offering_id'] or not row['session_id']:
        return gate | {'code':'historical_round', 'message':'历史或作废轮次不能重新结算；人工更正仍按原权限保留。'}
    if now < row['starts_at_ms']:
        return gate | {'code':'not_started', 'message':'尚未开始上课记录，开始后可立即结算。'}
    if now > row['window_end_ms']+7*86400_000:
        return gate | {'code':'evidence_expired', 'message':'原始证据回放期已结束，请通过学生卡片人工更正。'}
    try:
        from .login_attendance_settlement_guard import settlement_school_guard
        settlement_school_guard(conn, row['id'], pause=False)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail,dict) else {'code':'school_sync_busy','message':str(exc.detail)}
        return gate | detail
    restart = row['state'] == 'settling' or bool(task and task['state'] in UNPUBLISHED)
    return gate | {'can_start':not restart, 'can_restart':restart,
                   'message':'确认中断当前结算或等待，按当前已收到的证据重新结算；人工更正保留。' if restart else gate['message']}


def round_status(conn, row):
    task = _latest(conn, row['id'])
    return {'task':_public(task), 'gate':_gate(conn,row,task), 'round_revision':row['revision']}


def status(conn, user, offering_id, round_id):
    return round_status(conn, _round(conn,user,round_id,offering_id=offering_id))


def _scope(conn, row, user):
    offering = local.require_teacher(conn,row['class_offering_id'],user)
    session = local._session(conn,row['class_offering_id'],row['session_id'])
    return {key:row[key] for key in ('class_offering_id','session_id','starts_at_ms','ends_at_ms','window_start_ms','window_end_ms','settle_at_ms','ended_at_ms','policy_json')} | {
        'live_context':local._context(conn,offering,session), 'teacher_id':user['id'],
    }


def _cancel_task(conn, task):
    from .scheduled_task_service import cancel_tasks_by_dedupe
    cancel_tasks_by_dedupe(conn, f"attendance-settle:{task['id']}")


def stop_settlement(conn, round_id, reason):
    """Called under the parent's existing lifecycle transaction/lock."""
    task = _latest(conn, round_id)
    if not task or task['state'] not in UNPUBLISHED:
        return False
    conn.execute("UPDATE login_attendance_settlement_runs SET state='superseded',stage='superseded',revision=revision+1,worker_token='',message=?,updated_at_ms=? WHERE id=? AND state IN ('queued','running','failed')",
                 (reason,local.now_ms(),task['id']))
    conn.execute('UPDATE login_attendance_rounds SET settlement_generation=settlement_generation+1 WHERE id=?', (round_id,))
    _cancel_task(conn,task)
    return True


def request_settlement(conn, user, offering_id, round_id, payload, *, timestamp=None):
    row = _round(conn,user,round_id,offering_id=offering_id,lock=True)
    now = local.now_ms() if timestamp is None else timestamp
    fingerprint = hashlib.sha256(local._json({'round_id':int(round_id),'offering_id':offering_id,**payload}).encode()).hexdigest()
    previous = conn.execute('SELECT * FROM login_attendance_settlement_runs WHERE owner_teacher_id=? AND request_key=?', (user['id'],payload['idempotency_key'])).fetchone()
    if previous:
        if previous['request_hash'] != fingerprint:
            raise _error('idempotency_conflict','此操作标识已用于另一结算请求，请刷新后核对。')
        return {**round_status(conn,row), 'round':local.round_view(conn,round_id,user)}
    local._revision(row, {'revision':payload['expected_round_revision']})
    task = _latest(conn,round_id)
    if payload.get('expected_task_id') != (task['id'] if task else None) or payload.get('expected_task_generation') != (task['generation'] if task else None):
        raise _error('settlement_changed','结算任务已被另一操作更新，请刷新后再次确认。')
    gate = _gate(conn,row,task,timestamp=now)
    if not gate['can_start'] and not gate['can_restart']:
        raise _error(gate['code'],gate['message'])
    if bool(payload['restart']) != bool(gate['can_restart']):
        raise _error('restart_confirmation_required','当前结算或等待需要确认中断后重算，请先查看最新状态。')
    from .login_attendance_settlement_guard import settlement_school_guard
    settlement_school_guard(conn,round_id)
    previous_state = row['state']
    if task and task['state'] in UNPUBLISHED:
        conn.execute("UPDATE login_attendance_settlement_runs SET state='superseded',stage='superseded',revision=revision+1,worker_token='',message=?,updated_at_ms=? WHERE id=?",
                     ('教师已确认重新结算，旧任务不再发布结果。',now,task['id']))
        _cancel_task(conn,task)
    generation = int(row.get('settlement_generation') or 0)+1
    # A later recalculation may include delayed evidence inside the SAME ended
    # attendance window. It must never extend the original lesson or erase it.
    window_end = min(row['window_end_ms'],now) if row['ended_at_ms'] is None else row['window_end_ms']
    ended_at = row['ended_at_ms'] if row['ended_at_ms'] is not None else min(now,row['ends_at_ms'])
    conn.execute("UPDATE login_attendance_rounds SET state='settling',window_end_ms=?,ended_at_ms=?,settle_at_ms=?,settlement_generation=?,revision=revision+1,next_evaluation_ms=? WHERE id=?",
        (window_end,ended_at,now,generation,now,round_id))
    row = local.load_round(conn,round_id)
    task_id = uuid.uuid4().hex
    saved = conn.execute('''INSERT INTO login_attendance_settlement_runs(id,round_id,owner_teacher_id,generation,request_key,request_hash,state,stage,cutoff_ms,scope_json,message,created_at_ms,updated_at_ms)
        VALUES(?,?,?,?,?,?,'queued','queued',?,?,?,?,?) ON CONFLICT(owner_teacher_id,request_key) DO NOTHING RETURNING id''',
        (task_id,round_id,user['id'],generation,payload['idempotency_key'],fingerprint,now,local._json(_scope(conn,row,user)), '已停止本轮记录，结算任务已排队。',now,now)).fetchone()
    if not saved:
        raise _error('idempotency_conflict','此请求正在由另一操作处理，请使用原请求重试。')
    from .scheduled_task_service import schedule_task
    schedule_task(conn,task_kind=TASK_KIND,run_at=datetime.fromtimestamp(now/1000),payload={'run_id':task_id,'teacher_id':user['id']},
                  dedupe_key=f'attendance-settle:{task_id}',replace=False,priority=40,max_attempts=3,
                  owner_role='teacher',owner_user_pk=user['id'],title='立即结算课堂点名')
    local._audit(conn,round_id,'settlement_requested',user,before={'state':previous_state},
                 after={'task_id':task_id,'generation':generation,'restart':bool(payload['restart']),'cutoff_ms':now})
    return {**round_status(conn,row),'round':local.round_view(conn,round_id,user)}


def _load(conn, run_id):
    found = conn.execute('SELECT * FROM login_attendance_settlement_runs WHERE id=?', (run_id,)).fetchone()
    return dict(found) if found else None


def _task_owned(conn, scheduler_task):
    if scheduler_task is None:
        return True  # Internal deterministic tests; production passes its claim.
    return bool(conn.execute("SELECT 1 FROM scheduled_tasks WHERE id=? AND status='running' AND locked_by=? AND locked_at=?", (scheduler_task['id'],scheduler_task.get('locked_by'),scheduler_task.get('locked_at'))).fetchone())


def _fence(conn, user, run_id, token, *, scheduler_task=None):
    task = _load(conn,run_id)
    if not task or task['state'] not in {'queued','running','failed'}:
        return None
    row = _round(conn,user,task['round_id'],lock=True)
    # Reread task only AFTER the common parent/round lock.
    task = _load(conn,run_id)
    if not task or task['state'] not in {'queued','running','failed'} or task['worker_token'] != token or row['settlement_generation'] != task['generation'] or not _task_owned(conn,scheduler_task):
        return None
    if not row['is_current'] or row['state'] == 'void':
        return None
    if _scope(conn,row,user) != local._load(task['scope_json']):
        raise _error('settlement_scope_changed','课堂课表或记录时段已变化，请重新确认后结算。')
    if local.now_ms() > row['window_end_ms']+7*86400_000:
        raise _error('evidence_expired','原始证据回放期已结束，请人工更正。')
    return row,task


def _progress(user, run_id, token, stage, completed, message, *, scheduler_task=None):
    with get_db_connection() as conn:
        found = _fence(conn,user,run_id,token,scheduler_task=scheduler_task)
        if not found:
            return False
        conn.execute("UPDATE login_attendance_settlement_runs SET stage=?,completed=?,message=?,revision=revision+1,updated_at_ms=? WHERE id=? AND worker_token=? AND state='running'",
                     (stage,completed,message,local.now_ms(),run_id,token))
        conn.commit()
    return True


def _mark_unclaimed_permission_failure(conn, user, task, exc, scheduler_task):
    """Only annotate this queued receipt; no authority over classroom facts.

    The claim and generation predicates are in the same UPDATE so cancellation,
    replacement and a new worker cannot be overwritten by a delayed failure.
    """
    if task['owner_teacher_id'] != user['id'] or task['state'] not in {'queued','failed'} or not isinstance(exc,HTTPException) or exc.status_code not in {403,404}:
        return
    claim_sql,claim_args = '',()
    if scheduler_task is not None:
        claim_sql = " AND EXISTS(SELECT 1 FROM scheduled_tasks s WHERE s.id=? AND s.status='running' AND s.locked_by=? AND s.locked_at=?)"
        claim_args = (scheduler_task['id'],scheduler_task.get('locked_by'),scheduler_task.get('locked_at'))
    conn.execute("UPDATE login_attendance_settlement_runs SET state='failed',stage='failed',revision=revision+1,message=?,updated_at_ms=? "
        "WHERE id=? AND owner_teacher_id=? AND generation=? AND worker_token=? AND state IN ('queued','failed') "
        "AND EXISTS(SELECT 1 FROM login_attendance_rounds r WHERE r.id=login_attendance_settlement_runs.round_id "
        "AND r.settlement_generation=login_attendance_settlement_runs.generation AND r.is_current=1 AND r.state<>'void')"+claim_sql,
        ('教师权限或课堂归属已变化，旧任务未发布；请由当前任课教师核对后重新结算。',local.now_ms(),task['id'],user['id'],task['generation'],task['worker_token'],*claim_args))


def process_settlement(teacher_id, run_id, *, scheduler_task=None):
    user,token = {'id':int(teacher_id),'role':'teacher'},uuid.uuid4().hex
    task = None
    try:
        with get_db_connection() as conn:
            task = _load(conn,run_id)
            if not task or task['owner_teacher_id'] != user['id']:
                raise HTTPException(404,'结算任务不存在或无权限。')
            row = _round(conn,user,task['round_id'],lock=True)
            task = _load(conn,run_id)
            if task['state'] not in UNPUBLISHED or row['settlement_generation'] != task['generation'] or not _task_owned(conn,scheduler_task):
                return {'state':task['state'],'id':run_id}
            conn.execute("UPDATE login_attendance_settlement_runs SET state='running',stage='collecting',completed=0,worker_token=?,revision=revision+1,message=?,updated_at_ms=? WHERE id=?",
                (token,'正在读取本轮网络、课堂活动及补报回执。',local.now_ms(),run_id))
            conn.commit()
        # No parent/round write locks while gathering or calculating. Cancellation
        # can commit immediately; this worker must cross each fence again.
        with get_db_connection() as conn:
            prepared = local.collect_evaluation(conn,row,timestamp=task['cutoff_ms'])
        if prepared is None:
            raise _error('no_evaluation_input','本轮尚无可结算的记录时段或学生名单，请核对后重新结算。')
        if not _progress(user,run_id,token,'calculating',1,'已读取证据，正在计算全班时间基准与出勤结果。',scheduler_task=scheduler_task):
            return {'id':run_id,'state':'superseded'}
        result = local.compute_evaluation(prepared)
        if not _progress(user,run_id,token,'publishing',3,'已完成计算，正在保留人工更正并发布结果。',scheduler_task=scheduler_task):
            return {'id':run_id,'state':'superseded'}
        with get_db_connection() as conn:
            found = _fence(conn,user,run_id,token,scheduler_task=scheduler_task)
            if not found:
                return {'id':run_id,'state':'superseded'}
            current,_ = found
            local.apply_evaluation(conn,current,result)
            conn.execute("UPDATE login_attendance_settlement_runs SET state='completed',stage='completed',completed=total,revision=revision+1,message=?,updated_at_ms=? WHERE id=? AND worker_token=?",
                ('结算完成，人工更正已保留。',local.now_ms(),run_id,token))
            local._audit(conn,current['id'],'settlement_completed',user,after={'task_id':run_id,'generation':task['generation']})
            conn.commit()
        return {'id':run_id,'state':'completed'}
    except Exception as exc:
        # Old workers cannot overwrite progress/error on a replacement. Failure
        # does not allow ordinary scans to publish half-recalculated results.
        if task:
            with get_db_connection() as conn:
                _mark_unclaimed_permission_failure(conn,user,task,exc,scheduler_task)
                fresh = _load(conn,run_id)
                if fresh and fresh['state'] == 'running' and fresh['worker_token'] == token and _task_owned(conn,scheduler_task):
                    row = local.load_round(conn,fresh['round_id'])
                    if row.get('settlement_generation') == fresh['generation'] and row['is_current'] and row['state'] != 'void':
                        message = exc.detail.get('message') if isinstance(exc,HTTPException) and isinstance(exc.detail,dict) else '结算未完成，已保留原记录和人工更正，请重新结算。'
                        conn.execute("UPDATE login_attendance_settlement_runs SET state='failed',stage='failed',revision=revision+1,message=?,updated_at_ms=? WHERE id=? AND worker_token=? AND state='running'",
                            (message,local.now_ms(),run_id,token))
                conn.commit()
        if isinstance(exc,HTTPException):
            return {'id':run_id,'state':'failed'}
        raise RuntimeError('Attendance settlement failed; local decisions retained') from None
