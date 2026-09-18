"""Explicit-term JWXT timetable/adjustment synchronization.

School reads run outside write transactions.  A successful snapshot is published
atomically by the prediction service; failed reads leave the active snapshot alone.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date
from typing import Any

import httpx

from ..database import get_db_connection
from ..db.connection import execute_insert_returning_id
from .academic_calendar_sync_service import (
    ZF_CALENDAR_GNMKDM, ZF_CALENDAR_INDEX_PATH, ZF_HOME_CALENDAR_PATHS,
    _generate_calendar_days, _parse_academic_calendar_alignment, _persist_sync_result,
)
from .academic_integration_service import (
    load_teacher_academic_access_method, open_authenticated_academic_client,
)
from .academic_roster_sync_service import _load_semester_by_id
from .academic_service import compute_semester_week_count, load_teacher_semester_rows
from .organization_scope_service import load_teacher_org_scope
from .semester_identity_service import (
    SemesterIdentity, identity_from_semester_record, identity_from_year_term,
    identity_from_xnm_xqm, parse_semester_identity,
)

logger = logging.getLogger(__name__)


def _selected_identity(year: str, term: str) -> SemesterIdentity | None:
    year, term = str(year or '').strip(), str(term or '').strip()
    if not year and not term:
        return None
    if not year or term not in {'1', '2', '3'}:
        raise ValueError('请选择完整、有效的学年和学期。')
    identity = identity_from_year_term(year, term)
    if identity is None or identity.as_year_term()[0] != year:
        raise ValueError('学年格式应为 2026-2027，学期应为 1、2 或 3。')
    return identity


def _find_semester(conn, teacher_id: int, identity: SemesterIdentity) -> dict | None:
    matches = [s for s in load_teacher_semester_rows(conn, teacher_id)
               if identity_from_semester_record(s) == identity]
    matches.sort(key=lambda s: (int(s['teacher_id']) != teacher_id, -int(s['id'])))
    if len({(str(s['start_date'])[:10], str(s['end_date'])[:10]) for s in matches}) > 1:
        raise ValueError('同一学年学期存在不同的校历日期，请先在学期设置中核对。')
    return matches[0] if matches else None


async def _fetch_term_alignment(client, identity: SemesterIdentity):
    """Reuse the calendar parser, but keep the same authenticated session/term."""
    xnm, xqm = identity.as_xnm_xqm()
    requests = [(ZF_CALENDAR_INDEX_PATH, {'xnm': xnm, 'xqm': xqm, 'gnmkdm': ZF_CALENDAR_GNMKDM}),
                (ZF_HOME_CALENDAR_PATHS[0], {})]
    for path, params in requests:
        try:
            response = await client.get(path, params=params, timeout=18.0,
                                        headers={'X-Requested-With': 'XMLHttpRequest'})
            response.raise_for_status()
        except httpx.HTTPError:
            # Some teacher roles cannot access the dedicated calendar query,
            # while the authenticated homepage still exposes the same official
            # term range. Do not turn that supported fallback into a dead path.
            continue
        alignments = [a for a in _parse_academic_calendar_alignment(response.text, source_url=str(response.url))
                      if parse_semester_identity(a.name) == identity]
        unique = {(a.start_date, a.end_date): a for a in alignments}
        if len(unique) > 1:
            raise ValueError('教务校历返回了相互冲突的学期起止日期。')
        if unique:
            alignment = next(iter(unique.values()))
            start, end = date.fromisoformat(alignment.start_date), date.fromisoformat(alignment.end_date)
            if not 0 < (end - start).days <= 370:
                raise ValueError('教务校历的学期日期范围无效。')
            return alignment, [{'source': 'academic_system', 'endpoint': path, 'xnm': xnm, 'xqm': xqm}]
    raise ValueError('尚未取得所选学期的官方起止日期，未生成推测日期的课表。')


def _initialize_semester(conn, teacher_id: int, identity: SemesterIdentity, alignment, sources: list) -> dict:
    """Same-school creation is serialized across workers and teachers."""
    scope = load_teacher_org_scope(conn, teacher_id)
    if isinstance(conn, sqlite3.Connection):
        if not conn.in_transaction:
            conn.execute('BEGIN IMMEDIATE')
    else:
        conn.execute('SELECT pg_advisory_xact_lock(hashtext(?))',
                     (f"academic-semester:{scope['school_code']}:{identity.code}",))
    existing = _find_semester(conn, teacher_id, identity)
    if existing:
        if (str(existing['start_date'])[:10], str(existing['end_date'])[:10]) != (alignment.start_date, alignment.end_date):
            raise ValueError('同校学期在同步期间采用了不同的校历日期，请重新同步并核对日期。')
        return existing
    start, end = date.fromisoformat(alignment.start_date), date.fromisoformat(alignment.end_date)
    semester_id = execute_insert_returning_id(conn, '''
        INSERT INTO academic_semesters (teacher_id,school_code,school_name,name,start_date,end_date,
          week_count,calendar_sync_status,calendar_sync_message,calendar_source_summary_json)
        VALUES (?,?,?,?,?,?,?,'partial',?,?)
    ''', (teacher_id, scope['school_code'], scope['school_name'], identity.canonical_name,
          start.isoformat(), end.isoformat(), compute_semester_week_count(start, end),
          '已按教务校历初始化学期日期；课程变更以正式课表及申请为准。', json.dumps(sources, ensure_ascii=False)))
    # Date/week scaffolding only.  Holidays from other schools must not move this
    # teacher's sessions, and a weekend can still have a real timetable entry.
    days = _generate_calendar_days(semester_id=semester_id, teacher_id=teacher_id,
                                  start_date=start, end_date=end, events=[])
    _persist_sync_result(conn, teacher_id=teacher_id, semester_id=semester_id,
                         alignment=alignment, days=days, source_summary=sources, status='partial',
                         message='已从教务校历初始化全部日期，调停课按本学期申请核对。')
    return _load_semester_by_id(conn, teacher_id, semester_id)


async def sync_teacher_academic_schedule(teacher_id: int, *, year: str = '', term: str = '',
                                         semester_id: int | None = None) -> dict[str, Any]:
    from .academic_schedule_adjustment_adapter import (
        build_official_occurrences, discover_current_term, fetch_adjustment_snapshot,
    )
    from .academic_schedule_prediction_service import (
        claim_schedule_sync, fail_schedule_sync, reconcile_and_publish_snapshot, release_schedule_sync,
    )
    teacher_id = int(teacher_id)
    try:
        identity = _selected_identity(year, term)
        with get_db_connection() as conn:
            credential = load_teacher_academic_access_method(conn, teacher_id, school_code='gxufl')
            if not credential:
                return {'status': 'missing_credential', 'message': '请先在教务系统对接设置中验证并保存账号。'}
            semester = _load_semester_by_id(conn, teacher_id, int(semester_id)) if semester_id else None
            if semester_id and not semester:
                raise ValueError('该学期不存在或不属于当前学校。')
            if semester:
                actual = identity_from_semester_record(semester)
                if not actual or (identity and identity != actual):
                    raise ValueError('学期编号与所选学年学期不一致。')
                identity = actual
            claim = claim_schedule_sync(conn, teacher_id, lease_seconds=600)
            conn.commit()
        if claim['status'] != 'claimed':
            return {'status': 'busy', 'message': '教务课表正在同步，请稍后查看结果。'}
    except ValueError as exc:
        return {'status': 'invalid_semester', 'message': str(exc)}

    token = claim['token']
    try:
        async with open_authenticated_academic_client(credential) as (client, profile, _login):
            if profile.school_code != 'gxufl':
                raise ValueError('当前学校尚未启用调停课申请适配器。')
            if identity is None:
                discovered = await discover_current_term(client)
                identity = identity_from_xnm_xqm(discovered['xnm'], discovered['xqm'])
                if identity is None:
                    raise ValueError('未能识别教务系统当前学年学期。')
            if semester is None:
                with get_db_connection() as conn:
                    semester = _find_semester(conn, teacher_id, identity)
            new_alignment = None
            alignment_sources = []
            if semester is None:
                new_alignment, alignment_sources = await _fetch_term_alignment(client, identity)
                # Fetch/validate everything before committing the new semester.
                semester = {'name': identity.canonical_name, 'start_date': new_alignment.start_date,
                            'end_date': new_alignment.end_date,
                            'week_count': compute_semester_week_count(date.fromisoformat(new_alignment.start_date),
                                                                     date.fromisoformat(new_alignment.end_date))}
            snapshot = await fetch_adjustment_snapshot(client, semester)
            snapshot['official'] = build_official_occurrences(snapshot['teaching_classes'], semester)
            snapshot['source_summary'] = alignment_sources + snapshot.get('source_summary', [])
        with get_db_connection() as conn:
            try:
                if new_alignment:
                    semester = _initialize_semester(conn, teacher_id, identity, new_alignment, alignment_sources)
                result = reconcile_and_publish_snapshot(conn, teacher_id, semester, snapshot, token)
                release_schedule_sync(conn, teacher_id, token)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {'status': 'success', 'message': '已同步教务正式课表与调停课申请。',
                'year': identity.as_year_term()[0], 'term': str(identity.term),
                'semester_id': int(semester['id']), 'semester_name': semester['name'], **result}
    except (ValueError, httpx.HTTPError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else '教务系统访问失败，本次未替换已有课表，请稍后重试。'
    except Exception:
        logger.exception('Academic schedule snapshot could not be published for teacher %s', teacher_id)
        message = '本次课表同步未完成，已有课表与课程材料保持可用，请稍后重试。'
    with get_db_connection() as conn:
        fail_schedule_sync(conn, teacher_id, token, error=message)
        conn.commit()
    return {'status': 'failed', 'message': message}
