"""Read-only display metadata for the shared teacher/student schedule deck.

Period numbers are not clock times. Only explicit source ranges may populate
clock labels, and session metadata must describe this exact dated slot.
"""
from __future__ import annotations

import json
import re
import unicodedata

from .academic_course_sync_service import _parse_section_range


def _class_name_identity(value: str) -> str:
    """Compare display spelling, not punctuation or fragments of a class name."""
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value))


def _cached_class_names(value: str, known: dict[str, str]) -> list[str]:
    # The membership cache uses '·'; older imports used list punctuation.
    # Spaces, slashes and parentheses belong to names, never list boundaries.
    parts, current, depth = [], [], 0
    for char in value:
        if char in '(（[［【':
            depth += 1
        elif char in ')）]］】':
            depth = max(0, depth - 1)
        if depth == 0 and char in '、,，;；\r\n':
            parts.append(''.join(current).strip())
            current = []
        else:
            current.append(char)
    parts.append(''.join(current).strip())
    result = []
    for part in filter(None, parts):
        if _class_name_identity(part) in known:
            result.append(part)
            continue
        pieces = [piece.strip() for piece in part.split('·') if piece.strip()]
        index = 0
        while index < len(pieces):
            # A real class can itself contain a middle dot. Match known whole
            # names longest-first before treating cache dots as separators.
            end = next((end for end in range(len(pieces), index, -1)
                        if _class_name_identity('·'.join(pieces[index:end])) in known), index + 1)
            result.append('·'.join(pieces[index:end]))
            index = end
    return result


def load_offering_class_labels(conn, offering_ids, *, teacher_id=None) -> dict[int, str]:
    ids = sorted({int(value) for value in offering_ids if value})
    if not ids:
        return {}
    teacher_clause = ' AND o.teacher_id=?' if teacher_id is not None else ''
    rows = conn.execute(
        f"""SELECT o.id, o.combined_class_names, main.name AS main_name, linked.name AS linked_name
            FROM class_offerings o JOIN classes main ON main.id=o.class_id
            LEFT JOIN class_offering_class_links links ON links.offering_id=o.id
            LEFT JOIN classes linked ON linked.id=links.class_id
            WHERE o.id IN ({','.join('?' for _ in ids)}){teacher_clause}
            ORDER BY o.id, linked.name""", (*ids, *([int(teacher_id)] if teacher_id is not None else [])),
    ).fetchall()
    labels, names = {}, {}
    for row in rows:
        oid = int(row['id'])
        if str(row['combined_class_names'] or '').strip():
            labels[oid] = str(row['combined_class_names']).strip()
        parts = names.setdefault(oid, [])
        for key in ('main_name', 'linked_name'):
            value = str(row[key] or '').strip()
            if value and value not in parts:
                parts.append(value)
    result = {}
    for oid, parts in names.items():
        known = {_class_name_identity(name): name for name in parts}
        combined, seen = [], set()
        for name in [*_cached_class_names(labels.get(oid, ''), known), *parts]:
            identity = _class_name_identity(name)
            if identity and identity not in seen:
                seen.add(identity)
                combined.append(known.get(identity, name))
        result[oid] = '、'.join(combined)
    return result


def explicit_lesson_time(lesson: dict, session: dict | None = None) -> dict[str, str]:
    empty = {'start_time': '', 'end_time': '', 'time_label': ''}
    source = session if session is not None else lesson
    if session is not None:
        if str(session.get('session_date') or '')[:10] != str(lesson.get('actual_date') or '')[:10]:
            return empty
        start, end, _ = _parse_section_range(session.get('academic_section_text') or '')
        if list(range(start, end + 1)) != list(lesson.get('sections') or []):
            return empty
    try:
        metadata = json.loads(source.get('schedule_metadata_json') or '{}')
    except (ValueError, TypeError):
        metadata = {}
    # Synchronization retains the original metadata when a session is moved.
    # Never label a new period with a stale clock range from the original slot.
    if isinstance(metadata, dict) and metadata.get('section_text'):
        start, end, _ = _parse_section_range(metadata['section_text'])
        if list(range(start, end + 1)) != list(lesson.get('sections') or []):
            return empty
    text = str(source.get('academic_time_text') or source.get('time_text') or '')
    period_hint = re.search(r'(?:第\s*)?(\d{1,2}(?:\s*[-~－—]\s*\d{1,2})?)\s*节', text)
    if period_hint:
        start, end, _ = _parse_section_range(period_hint.group(1))
        if list(range(start, end + 1)) != list(lesson.get('sections') or []):
            return empty
    matches = re.findall(r'(?<!\d)([01]?\d|2[0-3])[:：]([0-5]\d)\s*[-–—~～至]\s*([01]?\d|2[0-3])[:：]([0-5]\d)(?!\d)', text)
    if len(matches) != 1:
        return empty
    h1, m1, h2, m2 = (int(value) for value in matches[0])
    if (h2, m2) <= (h1, m1):
        return empty
    start_time, end_time = f'{h1:02}:{m1:02}', f'{h2:02}:{m2:02}'
    return {'start_time': start_time, 'end_time': end_time, 'time_label': f'{start_time}–{end_time}'}
