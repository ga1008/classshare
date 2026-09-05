"""Seed the current semester only in an explicitly disposable P03 runtime."""
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

repo = Path(__file__).resolve().parents[3]
runtime = Path(sys.argv[1]).resolve()
assert runtime.is_relative_to(repo / '.codex-temp') and runtime != repo / '.codex-temp'
fixture = json.loads((runtime / 'fixture.json').read_text(encoding='utf-8'))
assert Path(fixture['databasePath']).resolve() == runtime / 'db/classroom.db'
today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()
year = today.year if today.month >= 8 else today.year - 1
term = 1 if today.month >= 8 or today.month <= 1 else 2
name = f'{year}-{year + 1}第{term}学期'
start = today - dt.timedelta(days=today.weekday() + 7)
with sqlite3.connect(fixture['databasePath']) as conn:
    # Reuse the fixture's authoritative current term; duplicate identities with
    # different anchors would exercise a different teacher calendar scenario.
    row = conn.execute('SELECT id,name FROM academic_semesters WHERE teacher_id=? AND start_date<=? AND end_date>=? ORDER BY end_date DESC, start_date DESC, id DESC LIMIT 1',
                       (fixture['teacher']['id'], today.isoformat(), today.isoformat())).fetchone()
    if row:
        semester = row[0]
        name = row[1]
    else:
        semester = conn.execute('INSERT INTO academic_semesters (teacher_id,school_code,school_name,name,start_date,end_date,week_count) VALUES (?,?,?,?,?,?,?)',
                                (fixture['teacher']['id'], 'p03-schedule-test', 'P03 QA School', name, start.isoformat(), (start + dt.timedelta(weeks=20)).isoformat(), 20)).lastrowid
    conn.execute('UPDATE class_offerings SET semester=?,semester_id=? WHERE id=?',
                 (name, semester, fixture['classOfferingId']))
