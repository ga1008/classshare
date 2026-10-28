"""Ten daily searches per authenticated actor and channel, across all workers.

Callers own short transactions and must commit before network work. These
functions never commit, roll back, issue DDL or await. A daily bucket row is the
common mutex on SQLite and PostgreSQL. Dispatch is a one-shot CAS, not a lease
that another worker can steal. Unknown sent work consumes its original day's
budget permanently: a crash or lost response must never authorize another bill.
Only fingerprints and bounded, already-sanitized result JSON are persisted.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hmac
import json
import math
import re
import uuid

from fastapi import HTTPException

from ..time_utils import APP_TIMEZONE, app_timezone_name, aware_local_now

DAILY_LIMIT = 10
RESULT_BYTES_LIMIT = 32768
DEFAULT_LEASE_SECONDS = 90
CACHE_RETENTION_DAYS = 7
CACHE_CLEANUP_TASK_KIND = 'ai_search_cache_cleanup'
TERMINAL_STATES = frozenset({'succeeded', 'empty', 'failed', 'unknown', 'released'})


def _clock(now=None):
    if now is None:
        return aware_local_now()
    if isinstance(now, datetime):
        return (now.replace(tzinfo=APP_TIMEZONE) if now.tzinfo is None else now).astimezone(APP_TIMEZONE)
    if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
        raise ValueError('Invalid search budget clock')
    return datetime.fromtimestamp(now, APP_TIMEZONE)


def _stamp(now):
    return now.astimezone(timezone.utc).isoformat(timespec='microseconds')


def _actor(actor_kind, actor_id, channel):
    if isinstance(actor_id, bool):
        raise HTTPException(422, '搜索身份不正确')
    try:
        pk = int(actor_id)
    except (ValueError, TypeError, OverflowError):
        raise HTTPException(422, '搜索身份不正确') from None
    if pk <= 0 or str(pk) != str(actor_id) or actor_kind not in {'teacher', 'student', 'wanderer'}:
        raise HTTPException(422, '搜索身份不正确')
    if (actor_kind == 'wanderer' and channel != 'wanderer') or (actor_kind != 'wanderer' and channel not in {'chat', 'agent'}):
        raise HTTPException(422, '搜索身份与通道不匹配')
    return actor_kind, pk, channel


def _day(value, now):
    day = value or now.date().isoformat()
    try:
        if datetime.strptime(day, '%Y-%m-%d').date().isoformat() != day:
            raise ValueError()
    except (TypeError, ValueError):
        raise HTTPException(422, '搜索额度日期不正确') from None
    return day


def _key(row):
    return (row['budget_date'], row['actor_kind'], row['actor_id'], row['channel'])


def _lock_bucket(conn, key, stamp):
    conn.execute('INSERT INTO ai_web_search_daily(budget_date,actor_kind,actor_id,channel,updated_at) '
                 'VALUES(?,?,?,?,?) ON CONFLICT(budget_date,actor_kind,actor_id,channel) DO NOTHING', (*key, stamp))
    # The write locks the existing row on PG; SQLite reserves its writer. No
    # global account lock is needed, so unrelated actors continue independently.
    conn.execute('UPDATE ai_web_search_daily SET updated_at=updated_at '
                 'WHERE budget_date=? AND actor_kind=? AND actor_id=? AND channel=?', key)


def _expire(conn, key, stamp):
    released = conn.execute("UPDATE ai_web_search_calls SET state='released',error_code='lease_expired',"
        "updated_at=?,finished_at=? WHERE budget_date=? AND actor_kind=? AND actor_id=? AND channel=? "
        "AND state='reserved' AND lease_until<=?", (stamp, stamp, *key, stamp)).rowcount
    unknown = conn.execute("UPDATE ai_web_search_calls SET state='unknown',error_code='lease_expired',"
        "updated_at=?,finished_at=? WHERE budget_date=? AND actor_kind=? AND actor_id=? AND channel=? "
        "AND state='sent' AND lease_until<=?", (stamp, stamp, *key, stamp)).rowcount
    count = released + unknown
    if count:
        changed = conn.execute('UPDATE ai_web_search_daily SET reserved_count=reserved_count-?, '
            'unknown_count=unknown_count+?, released_count=released_count+?,updated_at=? '
            'WHERE budget_date=? AND actor_kind=? AND actor_id=? AND channel=? AND reserved_count>=?',
            (count, unknown, released, stamp, *key, count)).rowcount
        if changed != 1:
            raise RuntimeError('Search reservation accounting is inconsistent')


def snapshot(conn, *, actor_kind, actor_id, channel, now=None, budget_date=None):
    """Read-only quota, projecting expired reservations without request-time writes."""
    actor = _actor(actor_kind, actor_id, channel)
    clock = _clock(now)
    day, stamp = _day(budget_date, clock), _stamp(clock)
    key = (day, *actor)
    row = conn.execute('SELECT * FROM ai_web_search_daily WHERE budget_date=? AND actor_kind=? '
                       'AND actor_id=? AND channel=?', key).fetchone()
    counts = dict(row) if row else {}
    expired = {item['state']: int(item['n']) for item in conn.execute(
        "SELECT state,COUNT(*) AS n FROM ai_web_search_calls WHERE budget_date=? AND actor_kind=? "
        "AND actor_id=? AND channel=? AND state IN ('reserved','sent') AND lease_until<=? GROUP BY state",
        (*key, stamp)).fetchall()}
    used = int(counts.get('used_count', 0))
    unknown = int(counts.get('unknown_count', 0)) + expired.get('sent', 0)
    reserved = max(0, int(counts.get('reserved_count', 0)) - sum(expired.values()))
    occupied = used + unknown + reserved
    reset = datetime.strptime(day, '%Y-%m-%d').replace(tzinfo=APP_TIMEZONE) + timedelta(days=1)
    return {'limit': DAILY_LIMIT, 'used': used, 'reserved': reserved, 'unknown': unknown,
        'remaining': max(0, DAILY_LIMIT - occupied), 'occupied': occupied,
        'empty': int(counts.get('empty_count', 0)), 'failed': int(counts.get('failed_count', 0)),
        'released': int(counts.get('released_count', 0)) + expired.get('reserved', 0),
        'date': day, 'reset_at': reset.isoformat(), 'timezone': app_timezone_name(), 'channel': channel}


def get_call(conn, call_id):
    """Internal trusted receipt, including its token; never expose this row publicly."""
    row = conn.execute('SELECT * FROM ai_web_search_calls WHERE call_id=?', (str(call_id),)).fetchone()
    if row is None:
        return None
    item = dict(row)
    item['result'] = json.loads(item.pop('result_json'))
    return item


def _existing(conn, actor, logical_call_id):
    row = conn.execute('SELECT call_id FROM ai_web_search_calls WHERE actor_kind=? AND actor_id=? '
                       'AND channel=? AND logical_call_id=?', (*actor, logical_call_id)).fetchone()
    return get_call(conn, row['call_id']) if row else None


def _receipt(conn, row, clock, *, replay=True, include_token=False):
    result = {name: row[name] for name in ('call_id', 'state', 'budget_date', 'error_code', 'result')}
    result['replay'] = replay
    result['quota'] = snapshot(conn, actor_kind=row['actor_kind'], actor_id=row['actor_id'],
                               channel=row['channel'], budget_date=row['budget_date'], now=clock)
    if include_token:
        result['dispatch_token'] = row['dispatch_token']
    return result


def reserve(conn, *, actor_kind, actor_id, channel, logical_call_id, fingerprint,
            now=None, lease_seconds=DEFAULT_LEASE_SECONDS):
    actor = _actor(actor_kind, actor_id, channel)
    if not isinstance(logical_call_id, str) or not 1 <= len(logical_call_id) <= 200 or not logical_call_id.isascii() or any(ord(c) < 33 for c in logical_call_id):
        raise HTTPException(422, '搜索请求编号不正确')
    if not isinstance(fingerprint, str) or not re.fullmatch('[0-9a-f]{64}', fingerprint):
        raise HTTPException(422, '搜索请求摘要不正确')
    clock = _clock(now)
    stamp, day = _stamp(clock), clock.date().isoformat()
    previous = _existing(conn, actor, logical_call_id)
    key = _key(previous) if previous else (day, *actor)
    _lock_bucket(conn, key, stamp)
    _expire(conn, key, stamp)
    previous = _existing(conn, actor, logical_call_id)
    if previous:
        if not hmac.compare_digest(previous['fingerprint'], fingerprint):
            raise HTTPException(409, '同一搜索请求编号不能更改搜索内容')
        return _receipt(conn, previous, clock)
    quota = snapshot(conn, actor_kind=actor_kind, actor_id=actor_id, channel=channel, now=clock)
    if quota['remaining'] <= 0:
        reset = datetime.fromisoformat(quota['reset_at'])
        wait = max(1, math.ceil((reset-clock).total_seconds()))
        raise HTTPException(429, '今天的联网搜索额度已用完（每天10次，次日零点重置）', headers={'Retry-After': str(wait)})
    call_id, token = uuid.uuid4().hex, uuid.uuid4().hex
    lease = _stamp(clock + timedelta(seconds=max(5, min(300, int(lease_seconds)))))
    inserted = conn.execute('INSERT INTO ai_web_search_calls(call_id,actor_kind,actor_id,channel,logical_call_id,'
        'fingerprint,budget_date,state,dispatch_token,lease_until,created_at,updated_at) '
        "VALUES(?,?,?,?,?,?,?,'reserved',?,?,?,?) ON CONFLICT(actor_kind,actor_id,channel,logical_call_id) DO NOTHING",
        (call_id, *actor, logical_call_id, fingerprint, day, token, lease, stamp, stamp)).rowcount
    if inserted != 1:
        # The same operation raced midnight in a different day bucket. Do not
        # lock a second bucket or count a losing insert against the new day.
        previous = _existing(conn, actor, logical_call_id)
        if not previous or not hmac.compare_digest(previous['fingerprint'], fingerprint):
            raise HTTPException(409, '同一搜索请求编号不能更改搜索内容')
        return _receipt(conn, previous, clock)
    changed = conn.execute('UPDATE ai_web_search_daily SET reserved_count=reserved_count+1,updated_at=? '
        'WHERE budget_date=? AND actor_kind=? AND actor_id=? AND channel=? '
        'AND used_count+reserved_count+unknown_count<?', (stamp, *key, DAILY_LIMIT)).rowcount
    if changed != 1:
        raise RuntimeError('Search quota changed while holding its bucket')
    return _receipt(conn, get_call(conn, call_id), clock, replay=False, include_token=True)


def _owned(conn, call_id, token):
    row = get_call(conn, call_id)
    if not row or not isinstance(token, str) or not hmac.compare_digest(row['dispatch_token'], token):
        raise HTTPException(409, '搜索执行凭据无效或已失效')
    return row


def mark_sent(conn, call_id, dispatch_token, *, now=None, lease_seconds=DEFAULT_LEASE_SECONDS):
    clock = _clock(now)
    row = _owned(conn, call_id, dispatch_token)
    stamp, key = _stamp(clock), _key(row)
    _lock_bucket(conn, key, stamp)
    _expire(conn, key, stamp)
    lease = _stamp(clock + timedelta(seconds=max(5, min(300, int(lease_seconds)))))
    won = conn.execute("UPDATE ai_web_search_calls SET state='sent',sent_at=?,updated_at=?,lease_until=? "
        "WHERE call_id=? AND dispatch_token=? AND state='reserved' AND lease_until>?",
        (stamp, stamp, lease, call_id, dispatch_token, stamp)).rowcount == 1
    row = get_call(conn, call_id)
    return {'won': won, 'call': row, 'quota': snapshot(conn, actor_kind=row['actor_kind'], actor_id=row['actor_id'],
        channel=row['channel'], budget_date=row['budget_date'], now=clock)}


def _result(value):
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        if len(encoded.encode('utf-8')) > RESULT_BYTES_LIMIT:
            raise ValueError('oversized')
    except (ValueError, TypeError, UnicodeError):
        raise HTTPException(422, '搜索结果缓存格式不正确或超过32KB') from None
    return encoded


def _error(value):
    text = str(value or '')
    return text if re.fullmatch('[a-zA-Z0-9_.:-]{0,80}', text) else 'search_failed'


def _finish(conn, call_id, token, *, outcome, result=None, error='', now=None, unsent=False):
    clock = _clock(now)
    row = _owned(conn, call_id, token)
    stamp, key = _stamp(clock), _key(row)
    _lock_bucket(conn, key, stamp)
    _expire(conn, key, stamp)
    row = get_call(conn, call_id)
    if row['state'] != ('reserved' if unsent else 'sent'):
        return _receipt(conn, row, clock)
    encoded = _result(result)
    conn.execute('UPDATE ai_web_search_calls SET state=?,result_json=?,error_code=?,updated_at=?,finished_at=? '
                 'WHERE call_id=? AND dispatch_token=? AND state=?',
                 (outcome, encoded, _error(error), stamp, stamp, call_id, token, row['state']))
    column = {'succeeded': 'used_count', 'empty': 'empty_count', 'failed': 'failed_count',
              'unknown': 'unknown_count', 'released': 'released_count'}[outcome]
    changed = conn.execute(f'UPDATE ai_web_search_daily SET reserved_count=reserved_count-1,{column}={column}+1,updated_at=? '
        'WHERE budget_date=? AND actor_kind=? AND actor_id=? AND channel=? AND reserved_count>0', (stamp, *key)).rowcount
    if changed != 1:
        raise RuntimeError('Search completion accounting is inconsistent')
    return _receipt(conn, get_call(conn, call_id), clock, replay=False)


def settle(conn, call_id, dispatch_token, *, outcome, result=None, error='', now=None):
    if outcome not in {'succeeded', 'empty', 'failed', 'unknown'}:
        raise ValueError('Invalid search outcome')
    return _finish(conn, call_id, dispatch_token, outcome=outcome, result=result, error=error, now=now)


def release_unsent(conn, call_id, dispatch_token, *, error='', now=None):
    return _finish(conn, call_id, dispatch_token, outcome='released', error=error, now=now, unsent=True)


def purge_cached_results(conn, *, now=None, retention_days=CACHE_RETENTION_DAYS, limit=500):
    """Expire at most 500 old result bodies, retaining every replay tombstone.

    The partial expiry index makes this independent of accumulated empty calls
    and current work. It changes no bucket, dispatch token, state or fingerprint.
    Like ledger mutations, this function leaves transaction ownership to callers.
    """
    count = max(0, min(500, int(limit)))
    if not count:
        return 0
    cutoff = _stamp(_clock(now) - timedelta(days=max(1, int(retention_days))))
    rows = conn.execute("SELECT call_id FROM ai_web_search_calls WHERE result_json<>'null' "
        "AND state IN ('succeeded','empty','failed','unknown','released') AND finished_at<? "
        "ORDER BY finished_at,call_id LIMIT ?", (cutoff, count)).fetchall()
    cleared = 0
    for row in rows:
        cleared += conn.execute("UPDATE ai_web_search_calls SET result_json='null',"
            "error_code=CASE WHEN error_code='' THEN 'result_expired' ELSE error_code END "
            "WHERE call_id=? AND result_json<>'null' AND finished_at<? "
            "AND state IN ('succeeded','empty','failed','unknown','released')", (row['call_id'], cutoff)).rowcount
    return cleared


def ensure_ai_search_cleanup_task(conn):
    from .scheduled_task_service import schedule_task

    return schedule_task(conn, task_kind=CACHE_CLEANUP_TASK_KIND,
        run_at=_clock().replace(tzinfo=None) + timedelta(minutes=30), payload={},
        dedupe_key='ai-search:cache-cleanup', recurrence_seconds=3600,
        title='过期联网搜索缓存清理', priority=95, max_attempts=3, replace=False)


def handle_ai_search_cache_cleanup(task=None):
    from ..database import get_db_connection

    # One small transaction per hourly scheduler task, never a new resident poll.
    with get_db_connection() as conn:
        cleared = purge_cached_results(conn, limit=500)
        conn.commit()
    return f'expired search caches={cleared}; replay tombstones preserved'
