"""Read-only daily-reflection acceptance; no application imports or AI calls.

Returns aggregate coverage, free-dispatch accounting and independently checked
affinity arithmetic. Never reads message bodies, persona, memory or rating reasons.
Run inside the configured container with --date YYYY-MM-DD. Missing schema fails.
"""
from __future__ import annotations
import argparse
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import json
import os
import sys


def valid_rating(grade, baseline, value):
    steps = {'very_good': 12, 'good': 5, 'neutral': 0, 'bad': -5, 'averse': -12}
    if grade not in steps:
        return False
    a, actual = Decimal(str(baseline)), Decimal(str(value))
    if not a.is_finite() or not actual.is_finite() or not 0 <= a <= 100 or not 0 <= actual <= 100:
        return False
    step = steps[grade]
    expected = a if not step else max(Decimal(0), min(Decimal(100),
        (a + step * (1 - a/200 if step > 0 else Decimal('.5') + a/200)).quantize(Decimal('.1'), rounding=ROUND_HALF_UP)))
    return actual == expected


def collect(conn, day):
    def count(sql, params=()):
        return int(conn.execute(sql, params).fetchone()[0] or 0)
    def rows(sql, params=()):
        return conn.execute(sql, params).fetchall()
    checks = {
        'over_three_calls': count('SELECT COUNT(*) FROM wanderer_reflection_sessions WHERE reflection_date=%s AND (ai_calls<0 OR ai_calls>3)', (day,)),
        'dispatch_count_mismatch': count('SELECT COUNT(*) FROM wanderer_reflection_sessions s WHERE s.reflection_date=%s AND s.ai_calls<>(SELECT COUNT(*) FROM wanderer_reflection_dispatches d WHERE d.wanderer_id=s.wanderer_id AND d.reflection_date=s.reflection_date)', (day,)),
        'free_calls_charged_ordinary_budget': count("SELECT COUNT(*) FROM wanderer_reflection_dispatches d JOIN wanderer_resource_reservations r ON r.operation_id='wake:' || d.turn_id WHERE d.reflection_date=%s AND r.resource='ai_wake' AND r.state<>'released'", (day,)),
        'dispatch_without_model_record': count("SELECT COUNT(*) FROM wanderer_reflection_dispatches d LEFT JOIN wanderer_turns t ON t.turn_id=d.turn_id WHERE d.reflection_date=%s AND (t.turn_id IS NULL OR t.dispatched_at IS NULL OR t.dispatched_at='')", (day,)),
        'legacy_session_call_mismatch': count("SELECT COUNT(*) FROM wanderer_reflection_sessions s JOIN wanderer_daily_reflections d ON d.wanderer_id=s.wanderer_id AND d.reflection_date=s.reflection_date WHERE s.reflection_date=%s AND s.status NOT IN ('pending','running') AND s.ai_calls<>d.ai_calls", (day,)),
    }
    ratings = rows('SELECT grade,baseline_affinity,new_affinity,COUNT(*) FROM wanderer_affinity_ratings WHERE reflection_date=%s GROUP BY grade,baseline_affinity,new_affinity', (day,))
    checks['invalid_rating_arithmetic'] = sum(int(r[3]) for r in ratings if not valid_rating(*r[:3]))
    grades = {}
    for grade, baseline, value, n in ratings:
        entry = grades.setdefault(grade, {'count': 0, 'total_delta': 0.0})
        entry['count'] += int(n)
        entry['total_delta'] = round(entry['total_delta']+(float(value)-float(baseline))*int(n), 4)
    sessions = [dict(zip(('status','sessions','ai_calls'), r)) for r in rows('SELECT status,COUNT(*),SUM(ai_calls) FROM wanderer_reflection_sessions WHERE reflection_date=%s GROUP BY status ORDER BY status', (day,))]
    records = [dict(zip(('status','records','ai_calls'), r)) for r in rows('SELECT status,COUNT(*),SUM(ai_calls) FROM wanderer_daily_reflections WHERE reflection_date=%s GROUP BY status ORDER BY status', (day,))]
    return {'ok': not any(checks.values()), 'date': day, 'invariants': checks, 'sessions': sessions,
        'daily_records': records, 'grades': grades,
        'active_wanderers': count("SELECT COUNT(*) FROM wanderer_agents WHERE status='active'"),
        'active_without_session': count("SELECT COUNT(*) FROM wanderer_agents w WHERE w.status='active' AND NOT EXISTS(SELECT 1 FROM wanderer_reflection_sessions s WHERE s.wanderer_id=w.id AND s.reflection_date=%s)", (day,)),
        'rated_wanderers': count('SELECT COUNT(DISTINCT wanderer_id) FROM wanderer_affinity_ratings WHERE reflection_date=%s', (day,)),
        'interpretation': 'No session before the configured daily time means not yet observed. Active population may change during a day. No grade is not neutral.'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--date', required=True)
    a = p.parse_args()
    day = date.fromisoformat(a.date).isoformat()
    dsn = os.environ.get('DATABASE_URL', '')
    if not dsn.startswith(('postgresql://', 'postgres://')):
        p.error('Use the already configured PostgreSQL container; no dotenv or fallback')
    import psycopg
    try:
        with psycopg.connect(dsn, autocommit=True, connect_timeout=10,
            options='-c default_transaction_read_only=on -c statement_timeout=15000 -c lock_timeout=1000') as conn:
            conn.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
            try:
                if conn.execute('SHOW transaction_read_only').fetchone()[0] != 'on':
                    raise RuntimeError('Read-only guard failed')
                result = collect(conn, day)
            finally:
                conn.execute('ROLLBACK')
        result.update(read_only=True, private_content_selected=False, generated_at=datetime.now(timezone.utc).isoformat())
        print(json.dumps(result, ensure_ascii=True, indent=2, default=str))
        return 0 if result['ok'] else 1
    except Exception as exc:
        print(json.dumps({'ok': False, 'read_only': True, 'error_type': type(exc).__name__, 'sqlstate': getattr(exc,'sqlstate',None)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
