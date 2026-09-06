"""Create an entirely synthetic UI v3 fixture; never copy or write the live DB."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
TEMP = (REPO / '.codex-temp').resolve()


def prepare(runtime: Path) -> None:
    runtime = runtime.resolve()
    if runtime == TEMP or not runtime.is_relative_to(TEMP) or runtime.exists():
        raise SystemExit('Use a new, task-owned child directory under .codex-temp')
    runtime.parent.mkdir(parents=True, exist_ok=True)
    template = runtime.parent / (runtime.name + '-empty.db')
    if template.exists():
        raise SystemExit('Synthetic source already exists; choose a new runtime name')
    with closing(sqlite3.connect(template)) as conn:
        conn.execute('PRAGMA user_version=0')
        conn.commit()
    os.environ.update({
        'PYTHON_DOTENV_DISABLED': '1', 'DB_ENGINE': 'sqlite', 'POSTGRES_BACKEND_READY': 'false',
        'LANSHARE_DATA_ROOT': str(runtime), 'MAIN_DATA_DIR': str(runtime),
        'MAIN_DB_PATH': str(runtime / 'db/classroom.db'), 'PYTHONIOENCODING': 'utf-8',
        'AI_HOST': '127.0.0.1', 'AI_PORT': '1', 'AI_ASSISTANT_URL': 'http://127.0.0.1:1',
    })
    sys.path.insert(0, str(REPO))
    spec = importlib.util.spec_from_file_location('p03_seed', Path(__file__).with_name('prepare_p03_runtime.py'))
    seed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed)
    # Reuse its genuine schema, identity and assignment factories on an empty
    # synthetic source. Its destructive reset is unreachable for an existing path.
    seed._source_db_path = lambda: template
    try:
        fixture = seed.prepare(runtime)
    finally:
        template.unlink(missing_ok=True)
    from classroom_app.db.schema_session_learning_materials import ensure_session_learning_materials_schema

    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()
    monday = today - dt.timedelta(days=today.weekday())
    start = monday - dt.timedelta(days=7)
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).replace(tzinfo=None, microsecond=0)
    year = today.year if today.month >= 8 else today.year - 1
    term = 1 if today.month >= 8 or today.month <= 1 else 2
    term_name = f'{year}-{year+1}第{term}学期'
    offering, teacher, student = fixture['classOfferingId'], fixture['teacher']['id'], fixture['student']['id']
    with closing(sqlite3.connect(fixture['databasePath'])) as conn:
        conn.row_factory = sqlite3.Row
        ensure_session_learning_materials_schema(conn)
        seed._update(conn, 'students', student, {'name': '张三'})
        seed._update(conn, 'teachers', teacher, {'name': '林老师'})
        seed._update(conn, 'classes', fixture['classId'], {'name': '计算机科学2606班（专升本）'})
        seed._update(conn, 'courses', fixture['courseId'], {'name': '计算机网络原理'})
        semester = seed._insert(conn, 'academic_semesters', {
            'teacher_id': teacher, 'school_code': seed.SCHOOL_CODE, 'school_name': seed.SCHOOL_NAME,
            'name': term_name, 'start_date': start.isoformat(),
            'end_date': (start + dt.timedelta(weeks=20)).isoformat(), 'week_count': 20,
        })
        seed._update(conn, 'class_offerings', offering, {'semester': term_name, 'semester_id': semester, 'first_class_date': start.isoformat()})
        session_ids = []
        for index in range(32):
            day = start + dt.timedelta(days=(index // 2) * 7 + 3 + index % 2)
            session_ids.append(seed._insert(conn, 'class_offering_sessions', {
                'class_offering_id': offering, 'order_index': index+1,
                'title': f'第{index+1}次课 · 网络基础与实践',
                'content': '观察网络连接，理解协议分层。\n完成网络命令实验。\n记录地址与网关。\n整理实验结果。\n核对长说明末行可读。',
                'section_count': 2, 'slot_section_count': 2, 'session_date': day.isoformat(),
                'weekday': day.weekday(), 'week_index': index//2+1,
                'academic_section_text': '4-5', 'academic_location': '知新楼B310 金融科技综合实验室',
                'academic_campus': '五合校区', 'schedule_source': 'academic_sync', 'schedule_status': 'active',
            }))
        material_ids = []
        for index, name in enumerate(['协议分层学习讲义.md', '网络命令实验指引.md']):
            content = f'# {name[:-3]}\n\n这是第三版真实阅读验证使用的合成材料。\n\n## 实验步骤\n\n1. 观察IP地址。\n2. 解释网关。\n3. 记录网络连通性。\n'.encode('utf-8')
            digest = hashlib.sha256(content).hexdigest()
            blob = runtime / 'media/blobs/sha256' / digest[:2] / digest[2:4] / digest
            blob.parent.mkdir(parents=True, exist_ok=True)
            blob.write_bytes(content)
            mid = seed._insert(conn, 'course_materials', {
                'teacher_id': teacher, 'name': name, 'material_path': name, 'node_type': 'file',
                'preview_type': 'markdown', 'file_ext': 'md', 'mime_type': 'text/markdown',
                'owner_role': 'teacher', 'owner_user_pk': teacher, 'scope_level': 'private',
                'file_hash': digest, 'file_size': len(content), 'ai_blurb': '理解网络基础并进行实验。',
            })
            material_ids.append(mid)
            seed._update(conn, 'course_materials', mid, {'root_id': mid})
            seed._insert(conn, 'course_material_assignments', {'material_id': mid, 'class_offering_id': offering, 'assigned_by_teacher_id': teacher})
            bindings = [0, session_ids[2]] + ([session_ids[1]] if index == 0 else [])
            for sid in bindings:
                seed._insert(conn, 'class_offering_learning_materials', {
                    'class_offering_id': offering, 'session_id': sid, 'material_id': mid,
                    'sort_order': index, 'ai_blurb_status': 'ready', 'ai_blurb': '网络学习材料',
                })
        seed._update(conn, 'class_offerings', offering, {'home_learning_material_id': material_ids[0]})
        for index in [1, 2]:
            seed._update(conn, 'class_offering_sessions', session_ids[index], {'learning_material_id': material_ids[0]})
        additional = []
        for name, semester_label, semester_id in [('学术写作', term_name, semester), ('高等数学（往期）', f'{year-1}-{year}第2学期', None)]:
            course_id = seed._insert(conn, 'courses', {'name': name, 'description': '合成未排课或历史课程', 'created_by_teacher_id': teacher})
            additional.append(seed._insert(conn, 'class_offerings', {
                'course_id': course_id, 'class_id': fixture['classId'], 'teacher_id': teacher,
                'semester': semester_label, 'semester_id': semester_id, 'schedule_info': '',
            }))
        for index in range(5):
            seed._insert(conn, 'classroom_todos', {
                'class_offering_id': offering, 'owner_role': 'student', 'owner_user_pk': student,
                'title': f'整理第{index+1}章知识结构', 'notes': '第三版合成待办',
                'due_at': (now+dt.timedelta(days=index+1)).isoformat() if index < 4 else None,
                'metadata_json': '{}', 'created_at': now.isoformat(), 'updated_at': now.isoformat(),
            })
        conn.commit()
    fixture.update({'visualSessionIds': session_ids, 'visualMaterialIds': material_ids,
                    'additionalOfferingIds': additional, 'visualClock': now.isoformat(), 'uiV3Synthetic': True})
    (runtime/'fixture.json').write_text(json.dumps(fixture, ensure_ascii=True, indent=2), encoding='utf-8')
    enrich_history(runtime)
    print(json.dumps({'runtime': str(runtime), 'synthetic': True, 'lessons': len(session_ids), 'materials': len(material_ids)}))


def enrich_history(runtime: Path) -> None:
    """Add synthetic history directly to the isolated DB; never send messages."""
    runtime = runtime.resolve()
    assert runtime != TEMP and runtime.is_relative_to(TEMP)
    fixture = json.loads((runtime/'fixture.json').read_text(encoding='utf-8'))
    assert fixture.get('uiV3Synthetic') is True
    database = Path(fixture['databasePath']).resolve()
    assert database == runtime/'db/classroom.db'
    with closing(sqlite3.connect(database)) as conn:
        # Match the actual planning contract: weekday is zero based and these
        # academic fields belong to an academic-schedule occurrence.
        for row in conn.execute('SELECT id,session_date FROM class_offering_sessions WHERE class_offering_id=?', (fixture['classOfferingId'],)).fetchall():
            if row[0] in fixture.get('visualSessionIds', []):
                conn.execute('UPDATE class_offering_sessions SET weekday=?,schedule_source=? WHERE id=?', (dt.date.fromisoformat(row[1]).weekday(), 'academic_sync', row[0]))
        prefix = 'UIV3_SYNTHETIC_HISTORY '
        found = conn.execute('SELECT COUNT(*) FROM chat_logs WHERE class_offering_id=? AND message LIKE ?', (fixture['classOfferingId'], prefix+'%')).fetchone()[0]
        if not found:
            for index in range(55):
                timestamp = (dt.datetime.now()-dt.timedelta(minutes=60-index)).isoformat(timespec='seconds')
                conn.execute('INSERT INTO chat_logs (class_offering_id,user_id,user_name,user_role,message,timestamp,logged_at,message_type) VALUES (?,?,?,?,?,?,?,?)', (
                    fixture['classOfferingId'], str(fixture['teacher']['id']), '林老师', 'teacher',
                    prefix+f'{index+1}：请解释协议分层，并记录网络命令实验的观察结果。', timestamp, timestamp, 'text'))
        conn.commit()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime-root', required=True, type=Path)
    parser.add_argument('--enrich-history', action='store_true')
    args = parser.parse_args()
    (enrich_history if args.enrich_history else prepare)(args.runtime_root)
