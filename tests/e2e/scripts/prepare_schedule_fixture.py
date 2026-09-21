"""Seed the current semester and a dated timetable in a disposable P03 runtime.

Called by prepare_p03_runtime.py for every fresh runtime (so every spec sees the
same calendar) and kept runnable on its own for the schedule spec's beforeAll.
"""
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def _term_anchor():
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()
    year = today.year if today.month >= 8 else today.year - 1
    term = 1 if today.month >= 8 or today.month <= 1 else 2
    name = f'{year}-{year + 1}第{term}学期'
    start = today - dt.timedelta(days=today.weekday() + 7)
    return today, name, start


def seed_current_semester(runtime: Path) -> None:
    runtime = Path(runtime).resolve()
    assert runtime.is_relative_to(REPO / '.codex-temp') and runtime != REPO / '.codex-temp'
    fixture = json.loads((runtime / 'fixture.json').read_text(encoding='utf-8'))
    assert Path(fixture['databasePath']).resolve() == runtime / 'db/classroom.db'
    today, name, start = _term_anchor()
    with sqlite3.connect(fixture['databasePath']) as conn:
        teacher_id = fixture['teacher']['id']
        offering_id = fixture['classOfferingId']
        # Semester rows are shared per school: the dashboard only sees terms whose
        # school_code matches the teacher's own scope, so seed under that code.
        school = conn.execute('SELECT school_code, school_name FROM teachers WHERE id=?', (teacher_id,)).fetchone()
        school_code = (school[0] if school and school[0] else 'p03-school')
        school_name = (school[1] if school and school[1] else 'P03 QA School')
        # Reuse the fixture's authoritative current term. Prefer the term the
        # fixture offering already belongs to: later preparers (tools/ui/
        # prepare_lq_s3.py) clone that row into a deliberately independent term
        # sharing the same anchors, so a plain "newest row covering today" pick
        # would silently repoint the offering onto the clone and leave the
        # dashboard timetable empty.
        current = ('lower(TRIM(COALESCE(school_code, ?))) = lower(TRIM(?)) '
                   'AND start_date<=? AND end_date>=?')
        args = (school_code, school_code, today.isoformat(), today.isoformat())
        own = conn.execute('SELECT semester_id FROM class_offerings WHERE id=?', (offering_id,)).fetchone()
        row = None
        if own and own[0]:
            row = conn.execute(f'SELECT id,name FROM academic_semesters WHERE id=? AND {current}',
                               (own[0], *args)).fetchone()
        if row is None:
            row = conn.execute(f'SELECT id,name FROM academic_semesters WHERE {current} '
                               'ORDER BY end_date DESC, start_date DESC, id DESC LIMIT 1', args).fetchone()
        if row:
            semester = row[0]
            name = row[1]
        else:
            semester = conn.execute('INSERT INTO academic_semesters (teacher_id,school_code,school_name,name,start_date,end_date,week_count) VALUES (?,?,?,?,?,?,?)',
                                    (teacher_id, school_code, school_name, name, start.isoformat(), (start + dt.timedelta(weeks=20)).isoformat(), 20)).lastrowid
        conn.execute('UPDATE class_offerings SET semester=?,semester_id=?,first_class_date=? WHERE id=?',
                     (name, semester, start.isoformat(), offering_id))
        # The 3D schedule needs dated sessions; seed a twice-a-week timetable once.
        has_dated = conn.execute('SELECT COUNT(*) FROM class_offering_sessions WHERE class_offering_id=? AND session_date IS NOT NULL AND session_date != ""', (offering_id,)).fetchone()[0]
        if not has_dated:
            conn.execute('DELETE FROM class_offering_sessions WHERE class_offering_id=?', (offering_id,))
            for index in range(16):
                day = start + dt.timedelta(days=(index // 2) * 7 + (1 if index % 2 == 0 else 3))
                conn.execute(
                    'INSERT INTO class_offering_sessions (class_offering_id, order_index, title, content, section_count, slot_section_count, session_date, weekday, week_index, academic_section_text, academic_location, schedule_source, schedule_status) '
                    'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (offering_id, index + 1, f'第{index + 1}次课 · P03 课表夹具', '课表 e2e 夹具课次。', 2, 2, day.isoformat(), day.weekday(), index // 2 + 1, '3-4', 'P03 教学楼 101', 'academic_sync', 'active'))
        conn.commit()


if __name__ == '__main__':
    seed_current_semester(Path(sys.argv[1]))
