"""Reconcile legacy JWXT occurrences without reusing a session for another lesson.

The prediction pipeline explains cross-date moves through verified applications.
Legacy roster refreshes have no such evidence: retain unmatched identities for
that pipeline, instead of assigning their content to the next chronological row.
"""
from __future__ import annotations

import re
from datetime import date

from .offering_plan_edit_service import lock_plan_row


def _sections(text) -> tuple[int, ...]:
    value = str(text or '').strip().replace('第', '').replace('节', '')
    result = []
    for part in re.split(r'[,，、]', value):
        match = re.fullmatch(r'\s*(\d{1,2})(?:\s*[-—~～]\s*(\d{1,2}))?\s*', part)
        if not match:
            return ()
        start, end = int(match[1]), int(match[2] or match[1])
        if not 1 <= start <= end <= 24:
            return ()
        result.extend(range(start, end + 1))
    return tuple(sorted(set(result)))


def reconcile_existing_academic_sessions(conn, *, offering: dict, occurrences: list[dict]) -> dict:
    """Only reconcile proven same-slot identities; caller owns the transaction.

    Cancellation is unambiguous only when all incoming coverage is accounted for
    by existing identities. A moved slot creates unaccounted coverage and leaves
    its original session intact until a verified application explains the move.
    """
    oid = int(offering['id'])
    lock_plan_row(conn, 'courses', int(offering['course_id']))
    lock_plan_row(conn, 'class_offerings', oid)
    sessions = [dict(r) for r in conn.execute(
        'SELECT * FROM class_offering_sessions WHERE class_offering_id = ? ORDER BY id', (oid,)).fetchall()]
    for session in sessions:
        lock_plan_row(conn, 'class_offering_sessions', int(session['id']))
    slots = []
    for row in occurrences:
        day = str(row.get('session_date') or '')[:10]
        sections = _sections(row.get('section_text'))
        if day and sections:
            slots.append((day, sections, row))
    proposals = []
    for s in sessions:
        day, periods = str(s.get('session_date') or '')[:10], _sections(s.get('academic_section_text'))
        candidates = [(n, row) for n, (d, p, row) in enumerate(slots)
                      if day == d and periods and set(periods).issubset(p)]
        if len(candidates) == 1:
            n, row = candidates[0]
            proposals.append((s['id'], row, {(n, p) for p in periods}))
    # Two existing sessions at the same slot are ambiguous.  Neither wins just
    # because it happens to have the smaller primary key.
    matches = {}
    used = set()
    for sid, row, atoms in proposals:
        if any(sid != other_id and atoms & other_atoms for other_id, _, other_atoms in proposals):
            continue
        matches[sid] = row
        used.update(atoms)
    incoming = {(n, p) for n, (_, sections, _) in enumerate(slots) for p in sections}
    unknown_coverage = incoming - used
    updated, cancelled = 0, 0
    warnings = []
    for session in sessions:
        sid = int(session['id'])
        row = matches.get(sid)
        if row is not None:
            # No title, content, template, order, section length or material fields
            # are overwritten here. A merged source block can cover two sessions.
            day = date.fromisoformat(str(session['session_date'])[:10])
            conn.execute('''UPDATE class_offering_sessions
                SET academic_occurrence_id = ?, academic_location = ?,
                    week_index = ?, weekday = ?, schedule_status = 'scheduled'
                WHERE id = ? AND class_offering_id = ?''',
                (row.get('id'), str(row.get('location') or ''), int(row.get('week_index') or session.get('week_index') or 0),
                 day.weekday(), sid, oid))
            updated += 1
        elif not unknown_coverage and slots and _sections(session.get('academic_section_text')):
            conn.execute("UPDATE class_offering_sessions SET schedule_status='cancelled' WHERE id=? AND class_offering_id=?",
                         (sid, oid))
            cancelled += 1
    if unknown_coverage:
        warnings.append('正式课表含变更或新增位置；已保留原课次身份，请同步教务课表及申请以精确对齐，未按日期重排教学内容。')
    return {'updated_count': updated, 'preserved_count': cancelled, 'warnings': warnings,
            'unresolved_slot_count': len({n for n, _ in unknown_coverage})}
