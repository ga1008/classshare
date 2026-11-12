"""Pure alignment evidence. Missing metadata is never positive equality evidence."""
from __future__ import annotations

import re

from .smart_classroom_miniapp_adapter import sections


def roster_evidence(local_numbers, remote_numbers):
    local = {str(n).strip() for n in local_numbers if n is not None and str(n).strip()}
    remote = {str(n).strip() for n in remote_numbers if n is not None and str(n).strip()}
    overlap = local & remote
    majority = bool(local and remote and len(overlap) * 2 > len(local) and len(overlap) * 2 > len(remote))
    difference = len(local ^ remote)
    return {'local_count': len(local), 'remote_count': len(remote), 'matched_count': len(overlap),
            'overlap': len(overlap), 'local_only_count': len(local - remote), 'remote_only_count': len(remote - local),
            'local_only': len(local - remote), 'remote_only': len(remote - local),
            'difference_count': difference, 'majority': majority, 'accepted': majority and difference < 5}


def positive_number(value):
    text = str(value or '').strip()
    if not re.fullmatch(r'\d+(?:\.\d+)?', text):
        return None
    number = float(text)
    return number if number > 0 else None


def course_metadata(row):
    def first(keys):
        return next((n for key in keys if (n := positive_number(row.get(key))) is not None), None)
    return {'total_hours': first(('total_hours', 'courseTotalHours', 'kczxs', 'course_total_hours_text', 'totalHours', 'zxs', 'total_hours_text')),
            'credits': first(('credits', 'credit', 'xf'))}


def deterministic_course(local, remote, roster):
    return bool(roster['accepted'] and local.get('course_code') and
                str(local['course_code']).casefold() == str(remote.get('course_code') or '').casefold() and
                local.get('academic_year') == remote.get('academic_year') and
                str(local.get('academic_term')) == str(remote.get('academic_term')) and
                all(positive_number(local.get(k)) is not None and positive_number(local.get(k)) == positive_number(remote.get(k))
                    for k in ('session_count', 'total_hours', 'credits')))


def occurrences(rows):
    """Expand complete valid timetable rows, never use attendance history counts."""
    result, seen = [], set()
    for row in rows:
        try:
            weeks = row.get('weeks')
            day = int(row.get('xqj'))
            parts = list(sections(row.get('sections')))
            if not isinstance(weeks, list) or not weeks or not parts or not 1 <= day <= 7:
                return [], False
            for week in weeks:
                week = int(week)
                if not 1 <= week <= 60 or not row.get('id'):
                    return [], False
                key = (str(row['id']), week, day, tuple(parts))
                if key not in seen:
                    seen.add(key)
                    result.append({'schedule_id': str(row['id']), 'week': week, 'day_of_week': day,
                                   'sections': parts, 'section': min(parts)})
        except (ValueError, TypeError):
            return [], False
    result.sort(key=lambda x: (x['week'], x['day_of_week'], x['section'], x['schedule_id']))
    # Duplicate simultaneous entries cannot establish an unambiguous lesson order.
    slots = [(x['week'], x['day_of_week'], tuple(x['sections'])) for x in result]
    complete = bool(result) and len(set(slots)) == len(slots)
    for i, item in enumerate(result, 1):
        item['order_index'] = i if complete else None
    return result, complete


def exact_session(local, remote):
    return all(local.get(k) is not None and local.get(k) == remote.get(k)
               for k in ('week', 'day_of_week', 'sections', 'order_index'))
