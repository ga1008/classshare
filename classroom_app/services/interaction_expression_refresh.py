"""Background publication of a complete, privacy-scoped radar projection per offering."""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import logging
import time
import uuid

from ..database import get_db_connection
from ..db.connection import get_configured_db_engine
from ..db.schema_interaction_expression import EXPRESSION_MODEL_VERSION
from ..time_utils import local_now
from .account_control_service import table_exists
from .interaction_expression_facts import load_expression_facts
from .interaction_expression_opportunities import load_interaction_expression_opportunities
from .offering_membership_service import offering_student_where


logger = logging.getLogger(__name__)
TASK_KIND = 'interaction_expression_refresh'
TICK_SECONDS = 25
REFRESH_SECONDS = 120
RETRY_SECONDS = 45
LEASE_SECONDS = 180
MAX_OFFERINGS_PER_TICK = 6
SOFT_BUDGET_SECONDS = 3


class _MissingSources(Exception):
    """A required source could not be read; a genuine historical uncertainty is publishable."""


def _stamp(value):
    return value.isoformat(timespec='microseconds')


def _due_offerings(conn, now, limit):
    return [int(row['id']) for row in conn.execute(
        """SELECT o.id FROM class_offerings o
           LEFT JOIN interaction_expression_refresh_state r ON r.class_offering_id=o.id
           WHERE (r.class_offering_id IS NULL OR (r.retry_after<=? AND r.lease_until<=?))
           ORDER BY COALESCE(r.retry_after,''),o.id LIMIT ?""",
        (_stamp(now), _stamp(now), limit),
    ).fetchall()]


def _claim(conn, offering_id, now, *, force):
    token = uuid.uuid4().hex
    claimed = conn.execute(
        """INSERT INTO interaction_expression_refresh_state(class_offering_id,lease_token,lease_until)
           SELECT id,?,? FROM class_offerings WHERE id=?
           ON CONFLICT(class_offering_id) DO UPDATE SET
             lease_token=excluded.lease_token, lease_until=excluded.lease_until
           WHERE interaction_expression_refresh_state.lease_until<=?
             AND (?=1 OR interaction_expression_refresh_state.retry_after<=?)""",
        (token, _stamp(now + timedelta(seconds=LEASE_SECONDS)), offering_id,
         _stamp(now), int(force), _stamp(now)),
    )
    return token if claimed.rowcount == 1 else None


def _collect(conn, offering_id, as_of):
    agent_guard = ("AND NOT EXISTS (SELECT 1 FROM wanderer_agents w WHERE w.student_id=s.id)"
                   if table_exists(conn, 'wanderer_agents') else "")
    students = [int(row['id']) for row in conn.execute(
        f"""SELECT s.id FROM class_offerings o JOIN students s ON {offering_student_where()}
            WHERE o.id=? AND COALESCE(s.enrollment_status,'active')='active'
              AND LOWER(COALESCE(s.control_mode,'human'))<>'ai' {agent_guard} ORDER BY s.id""",
        (offering_id,),
    ).fetchall()]
    projections = {sid: {} for sid in students}
    for audience, own in (('self', True), ('teacher', False)):
        facts = load_expression_facts(conn, offering_id, students, include_private=own, include_anonymous=own)
        opportunities = load_interaction_expression_opportunities(
            conn, offering_id, students, as_of=as_of, include_anonymous=own)
        for sid in students:
            activity, opportunity = facts[sid], opportunities[sid]
            if (activity['unavailable_sources'] or opportunity['missing_sources']
                    or opportunity['coverage_status'] == 'ineligible'):
                raise _MissingSources()
            projections[sid][audience] = json.dumps(
                {'facts': activity, 'opportunities': opportunity},
                ensure_ascii=False, sort_keys=True, separators=(',', ':'),
            )
    return projections


def _publish(conn, offering_id, token, projections, *, calculated_at, now):
    # Fencing the course row first serializes publication with a lease takeover.
    # The update, departed-roster deletes and both audience views commit together.
    saved = conn.execute(
        """UPDATE interaction_expression_refresh_state SET model_version=?,calculated_at=?,retry_after=?,
                 lease_token='',lease_until=''
           WHERE class_offering_id=? AND lease_token=? AND lease_until>?""",
        (EXPRESSION_MODEL_VERSION, _stamp(calculated_at), _stamp(now + timedelta(seconds=REFRESH_SECONDS)),
         offering_id, token, _stamp(now)),
    )
    if saved.rowcount != 1:
        return {'status': 'superseded', 'students': 0}
    old_students = {int(row['student_id']) for row in conn.execute(
        'SELECT student_id FROM interaction_expression_snapshots WHERE class_offering_id=?', (offering_id,),
    ).fetchall()}
    departed = sorted(old_students - projections.keys())
    for offset in range(0, len(departed), 250):
        batch = departed[offset:offset + 250]
        marks = ','.join('?' for _ in batch)
        conn.execute(f'DELETE FROM interaction_expression_snapshots WHERE class_offering_id=? AND student_id IN ({marks})',
                     (offering_id, *batch))
    if projections:
        conn.executemany(
            """INSERT INTO interaction_expression_snapshots
                 (class_offering_id,student_id,model_version,self_json,teacher_json,calculated_at)
               VALUES (?,?,?,?,?,?) ON CONFLICT(class_offering_id,student_id) DO UPDATE SET
                 model_version=excluded.model_version,self_json=excluded.self_json,
                 teacher_json=excluded.teacher_json,calculated_at=excluded.calculated_at
               WHERE interaction_expression_snapshots.model_version<>excluded.model_version
                  OR interaction_expression_snapshots.self_json<>excluded.self_json
                  OR interaction_expression_snapshots.teacher_json<>excluded.teacher_json""",
            [(offering_id, sid, EXPRESSION_MODEL_VERSION, views['self'], views['teacher'], _stamp(calculated_at))
             for sid, views in projections.items()],
        )
    return {'status': 'published', 'students': len(projections)}


def _defer(conn, offering_id, token, now):
    conn.execute(
        """UPDATE interaction_expression_refresh_state SET retry_after=?,lease_token='',lease_until=''
           WHERE class_offering_id=? AND lease_token=?""",
        (_stamp(now + timedelta(seconds=RETRY_SECONDS)), offering_id, token),
    )


def refresh_offering_expression(conn, offering_id, *, as_of=None):
    """Explicit rebuild inside the caller's transaction (no commit). Used for migration/rehearsal.

    as_of sets the observation instant of opportunity windows. The source data
    are the business records present in this transaction, not a historical rewind.
    """
    offering_id = int(offering_id)
    now = local_now()
    token = _claim(conn, offering_id, now, force=True)
    if not token:
        return {'status': 'busy', 'students': 0}
    conn.execute('SAVEPOINT explicit_expression_refresh')
    try:
        projections = _collect(conn, offering_id, as_of or now)
        result = _publish(conn, offering_id, token, projections, calculated_at=now, now=local_now())
    except Exception as error:
        # A migration may catch a failed offering and commit other work. In
        # that case even a partially executed SQLite executemany must roll back.
        conn.execute('ROLLBACK TO SAVEPOINT explicit_expression_refresh')
        _defer(conn, offering_id, token, local_now())
        if not isinstance(error, _MissingSources):
            raise
        result = {'status': 'incomplete_sources', 'students': 0}
    finally:
        conn.execute('RELEASE SAVEPOINT explicit_expression_refresh')
    return result


def refresh_due_offerings(*, connection_factory=get_db_connection, limit=MAX_OFFERINGS_PER_TICK):
    """Keep the read snapshot separate from short claim/publication write transactions."""
    result = {'published': 0, 'retry': 0, 'superseded': 0, 'students': 0}
    limit = max(1, min(int(limit), MAX_OFFERINGS_PER_TICK))
    started = time.monotonic()
    with connection_factory() as conn:
        due = _due_offerings(conn, local_now(), limit)
    for offering_id in due:
        if time.monotonic() - started >= SOFT_BUDGET_SECONDS:
            break
        now = local_now()
        with connection_factory() as conn:
            token = _claim(conn, offering_id, now, force=False)
        if not token:
            continue
        try:
            with connection_factory() as conn:
                if get_configured_db_engine() == 'postgres':
                    conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                else:
                    conn.execute('BEGIN')
                projections = _collect(conn, offering_id, now)
            with connection_factory() as conn:
                published = _publish(conn, offering_id, token, projections, calculated_at=now, now=local_now())
            result[published['status']] += 1
            result['students'] += published['students']
        except Exception as error:
            # A single bad classroom must not stop other classes or the
            # recurrence. Only the exception class enters diagnostics.
            logger.warning('Interaction expression refresh deferred for offering %s (%s)',
                           offering_id, type(error).__name__)
            with connection_factory() as conn:
                _defer(conn, offering_id, token, local_now())
            result['retry'] += 1
    return result


def ensure_interaction_expression_refresh_task(conn):
    from ..db.schema_scheduler import ensure_scheduler_schema

    ensure_scheduler_schema(conn)
    now = datetime.now().isoformat(timespec='seconds')
    # Startup may be simultaneous across web workers. Preserve every live
    # scheduler claim, and rearm only this system timer if it exhausted retries.
    conn.execute(
        """INSERT INTO scheduled_tasks(task_kind,dedupe_key,title,priority,run_at,recurrence_seconds,created_at,updated_at)
           VALUES (?,?,?,85,?,?,?,?) ON CONFLICT(dedupe_key) DO UPDATE SET
             status='pending',run_at=excluded.run_at,attempt_count=0,next_attempt_at=NULL,last_error='',
             locked_by='',locked_at=NULL,updated_at=excluded.updated_at
           WHERE scheduled_tasks.task_kind=excluded.task_kind AND scheduled_tasks.status='failed'""",
        (TASK_KIND, TASK_KIND, '刷新课堂参与和互动表达记录', now, TICK_SECONDS, now, now))
    return int(conn.execute('SELECT id FROM scheduled_tasks WHERE dedupe_key=?', (TASK_KIND,)).fetchone()['id'])


def arm_expression_timer():
    with get_db_connection() as conn:
        return ensure_interaction_expression_refresh_task(conn)


def handle_interaction_expression_refresh(_task):
    try:
        result = refresh_due_offerings()
    except Exception as error:
        # Leave the recurring timer alive through a temporary database outage,
        # even when the failure happened before a specific course was claimed.
        logger.warning('Interaction expression timer deferred (%s)', type(error).__name__)
        return f'deferred ({type(error).__name__})'
    return f"published {result['published']} offering(s), {result['students']} student(s), retry {result['retry']}"
