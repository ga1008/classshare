"""Read-only adaptation of one published JWXT snapshot to the shared week deck."""
from __future__ import annotations

from datetime import date, timedelta

from .academic_service import china_now, load_teacher_semester_rows
from .semester_identity_service import identity_from_semester_record


def prediction_lesson_items(lessons: list[dict]) -> list[dict]:
    from .smart_classroom_schedule_sync_service import _section_label, _short_classroom, _weekday_label
    items = []
    for index, lesson in enumerate(lessons, 1):
        item = dict(lesson)
        sections = list(item.get('sections') or [])
        if not sections:
            continue
        actual = date.fromisoformat(str(item['actual_date'])[:10])
        item.update({
            'id': item.get('event_key') or f"academic-{index}",
            'weekday': actual.weekday() + 1, 'weekday_label': _weekday_label(actual.weekday() + 1),
            'weeks': [int(item['week_index'])], 'sections': sections, 'section_label': _section_label(sections),
            'classroom': str(item.get('classroom') or item.get('room') or ''),
            'course_code': str(item.get('course_code') or ''),
            'class_label': str(item.get('class_label') or item.get('teaching_class_name') or ''),
            'local_class_name': str(item.get('local_class_name') or ''),
            'class_is_fallback': False, 'class_offering_id': item.get('class_offering_id'),
            'classroom_url': str(item.get('classroom_url') or ''),
            'single_or_double': 'NONE', 'single_or_double_label': '', 'student_count': 0,
            'hours_per_meeting': len(sections), 'total_hours': len(sections),
            'counts_towards_total': item.get('counts_towards_total', True),
        })
        item['classroom_short'] = _short_classroom(item['classroom'])
        items.append(item)
    return items


def build_academic_prediction_overview(conn, teacher_id: int, *, year='', term='', course='', class_label=''):
    from .academic_schedule_prediction_service import load_teacher_prediction_snapshot, load_teacher_prediction_terms
    from .smart_classroom_schedule_sync_service import _build_course_stats, _build_week_deck, _offering_create_url
    known = load_teacher_prediction_terms(conn, teacher_id)
    if not known:
        return None
    known_ids = {int(r['semester_id']) for r in known}
    today = china_now().date()
    semesters = load_teacher_semester_rows(conn, teacher_id)
    # Shared school terms may have equivalent rows owned by different teachers.
    # Prefer the row that actually owns this teacher's published snapshot.
    semesters.sort(key=lambda row: (int(row['id']) not in known_ids,
                                    int(row['teacher_id']) != int(teacher_id)))
    entries = []
    seen = set()
    for semester in semesters:
        identity = identity_from_semester_record(semester)
        if identity is None:
            continue
        key = identity.as_year_term()
        if key in seen:
            continue
        seen.add(key)
        start, end = date.fromisoformat(str(semester['start_date'])[:10]), date.fromisoformat(str(semester['end_date'])[:10])
        monday = start - timedelta(days=start.weekday())
        status = 'current' if start <= today <= end else ('ended' if end < today else 'future')
        entries.append({'year': key[0], 'term': key[1], 'label': identity.canonical_name,
                        'status': status, 'week1_monday': monday.isoformat(),
                        'max_week': int(semester['week_count']), 'semester_id': int(semester['id']),
                        'anchor_source': 'platform', 'schedule_source': 'academic' if int(semester['id']) in known_ids else 'platform_offerings'})
    selected = next((t for t in entries if (t['year'], t['term']) == (year, term)), None)
    if year or term:
        if selected is None:
            return None  # legacy adapter emits the same explicit-term empty result
    else:
        selected = next((t for t in entries if t['status'] == 'current'), None)
        if selected is None:
            ended = [t for t in entries if t['status'] == 'ended']
            selected = max(ended, key=lambda t: t['week1_monday']) if ended else (entries[0] if entries else None)
    if selected is None or selected['semester_id'] not in known_ids:
        return None
    snapshot = load_teacher_prediction_snapshot(conn, teacher_id, selected['semester_id'])
    if snapshot is None:
        return None
    all_items = prediction_lesson_items(snapshot['lessons'])
    for item in all_items:
        item['create_url'] = '' if item.get('class_offering_id') else _offering_create_url(item, selected['year'], selected['term'])
    course_options = sorted({i['course_name'] for i in all_items})
    class_options = sorted({i['class_label'] for i in all_items})
    items = [i for i in all_items if (not course or i['course_name'] == course)
             and (not class_label or i['class_label'] == class_label)]
    official = [i for i in items if i['counts_towards_total']]
    monday = date.fromisoformat(selected['week1_monday'])
    live_week = (today - monday).days // 7 + 1 if selected['status'] == 'current' else 0
    weeks = _build_week_deck(items, max_week=selected['max_week'], cur_week=live_week, week1_monday=monday)
    selected.update({'live_cur_week': live_week,
                     'focus_week': len(weeks) if selected['status'] == 'ended' else min(max(live_week, 1), len(weeks))})
    stats = _build_course_stats(official)
    current = next((w for w in weeks if w['is_current']), {})
    warnings = snapshot.get('warnings') or []
    return {
        'status': 'success', 'has_data': bool(all_items), 'schedule_source': 'academic',
        'message': '；'.join(str(w.get('message', '')) if isinstance(w, dict) else str(w)
                           for w in warnings[:3]), 'warnings': warnings,
        'terms': entries, 'selected_term': selected, 'sync_state': snapshot.get('sync_state') or {},
        'filters': {'course': course, 'class_label': class_label, 'course_options': course_options, 'class_options': class_options},
        'summary': {'course_count': len(stats), 'class_count': len({i['class_label'] for i in official}),
                    'classroom_count': len({i['classroom'] for i in official}), 'slot_count': len(official),
                    'total_hours': sum(i['total_hours'] for i in official), 'current_week_hours': current.get('total_hours', 0),
                    'prediction_count': len(items) - len(official), 'cur_week': live_week, 'max_week': len(weeks),
                    'term_status': selected['status'], 'week1_monday': selected['week1_monday'],
                    'weekly_average_hours': round(sum(i['total_hours'] for i in official) / max(1, sum(bool(w['lesson_count']) for w in weeks)), 1)},
        'courses': stats, 'weeks': weeks,
        'section_range': {'min': 1, 'max': max(11, max((max(i['sections']) for i in items), default=11))},
    }
