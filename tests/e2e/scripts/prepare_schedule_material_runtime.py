"""Repeatable schedule/Git scenarios in an explicitly owned synthetic runtime.

Create the base with prepare_ui_v3_runtime.py first. No real DB, credential,
remote Git server, AI service, or academic system is consulted by this script.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def insert(conn, table, values):
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if not set(values) <= columns:
        raise ValueError(f"Unknown fixture fields in {table}: {set(values) - columns}")
    return conn.execute(f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
                        tuple(values.values())).lastrowid


def clone(conn, table, source, **changes):
    row = dict(conn.execute(f"SELECT * FROM {table} WHERE id=?", (source,)).fetchone())
    row.pop('id')
    row.update(changes)
    return insert(conn, table, row)


def git(cwd, *args):
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_') and key != 'GCM_INTERACTIVE'}
    config = Path(cwd).parent / 'empty.gitconfig'
    config.write_text('', encoding='utf-8')
    env.update(GIT_CONFIG_GLOBAL=config.as_posix(), GIT_CONFIG_NOSYSTEM='1', GIT_TERMINAL_PROMPT='0',
               GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='protocol.allow', GIT_CONFIG_VALUE_0='never', LC_ALL='C')
    # Permit only the fixture's filesystem transport; every remote is seeded here.
    command = ['git', '-c', 'protocol.file.allow=always', *map(str, args)]
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=30)
    if result.returncode:
        raise RuntimeError(f"Synthetic Git failed ({args[0]}): {result.stderr}")
    return result.stdout.strip()


def publish(conn, fixture, phase):
    from classroom_app.services.academic_schedule_prediction_service import (
        claim_schedule_sync, reconcile_and_publish_snapshot, release_schedule_sync,
    )
    scenario = fixture['scheduleMaterial']
    snapshot = copy.deepcopy(scenario['baselineSnapshot'])
    if phase != 'baseline':
        request = copy.deepcopy(scenario['moveRequest'])
        request['status'] = 'approved' if phase == 'approved' else 'pending'
        request['raw_status'] = '3' if phase == 'approved' else '1'
        snapshot['requests'] = [request]
        if phase == 'approved':
            snapshot['official'][2].update(scenario['targetSlot'])
    teacher = fixture['teacher']['id']
    lease = claim_schedule_sync(conn, teacher)
    if lease.get('status') != 'claimed':
        raise RuntimeError('Synthetic fixture sync lease is busy')
    conn.commit()
    result = reconcile_and_publish_snapshot(conn, teacher, scenario['semesterId'], snapshot, lease['token'])
    release_schedule_sync(conn, teacher, lease['token'])
    conn.commit()
    return result


def seed(conn, runtime, fixture):
    if fixture.get('scheduleMaterial'):
        raise ValueError('Fixture already seeded; use a fresh runtime for a new QA run')
    from classroom_app.services import materials_git_service as material_git
    from classroom_app.services.git_learning_bindings_service import sync_repository_learning_bindings
    teacher, offering, course = fixture['teacher']['id'], fixture['classOfferingId'], fixture['courseId']
    original = dict(conn.execute('SELECT * FROM class_offerings WHERE id=?', (offering,)).fetchone())
    semester = dict(conn.execute('SELECT * FROM academic_semesters WHERE id=?', (original['semester_id'],)).fetchone())
    anchor = date.fromisoformat(semester['start_date'])
    conn.execute("UPDATE courses SET academic_course_code='QA-SEQ' WHERE id=?", (course,))
    conn.execute("UPDATE class_offerings SET academic_teaching_class_id='QA-SEQ-A',academic_teaching_class_name='QA-SEQ-A' WHERE id=?", (offering,))
    sibling_class = clone(conn, 'classes', original['class_id'], name='合成调课验收分班 QA-SEQ-B')
    sibling = clone(conn, 'class_offerings', offering, class_id=sibling_class, academic_teaching_class_id='QA-SEQ-B', academic_teaching_class_name='QA-SEQ-B')
    insert(conn, 'class_offering_class_links', {'offering_id': sibling, 'class_id': sibling_class,
        'teacher_id': teacher, 'is_primary': 1, 'source': 'manual'})
    for row in conn.execute('SELECT * FROM class_offering_sessions WHERE class_offering_id=? ORDER BY order_index', (offering,)).fetchall():
        clone(conn, 'class_offering_sessions', row['id'], class_offering_id=sibling, learning_material_id=None)
    session_ids = [row[0] for row in conn.execute('SELECT id FROM class_offering_sessions WHERE class_offering_id=? ORDER BY order_index', (offering,))]
    official = []
    for oid, code in [(offering, 'QA-SEQ-A'), (sibling, 'QA-SEQ-B')]:
        for row in conn.execute('SELECT * FROM class_offering_sessions WHERE class_offering_id=? ORDER BY order_index', (oid,)):
            day = date.fromisoformat(row['session_date'])
            official.append({'teaching_class_id': code, 'teaching_class_name': code,
                'course_code': 'QA-SEQ', 'course_name': '计算机网络原理', 'class_label': '合成调课验收班',
                'date': day.isoformat(), 'week': (day-anchor).days//7+1, 'weekday': day.isoweekday(),
                'sections': [4, 5], 'room': row['academic_location']})
    source = {key: official[2][key] for key in ['date', 'week', 'weekday', 'sections', 'room']}
    target_day = anchor + timedelta(weeks=16, days=4)
    target = {**source, 'date': target_day.isoformat(), 'week': 17, 'weekday': target_day.isoweekday()}
    request = {key: official[2][key] for key in ['teaching_class_id', 'teaching_class_name', 'course_code', 'course_name', 'class_label']}
    request.update(request_id='QA-MOVE-3-17', serial='QA-0001', status='pending', raw_status='1', kind='move',
                   reason='合成验收：第三次课移至第十七周', applied_at=datetime.now().isoformat(),
                   details=[{'detail_id': 'QA-MOVE-3-17-1', 'original': source, 'proposed': target}])

    git_root = runtime / 'git'
    git_root.mkdir()
    remote, author, imported = git_root/'remote.git', git_root/'author', git_root/'imported'
    git(git_root, 'init', '--bare', '--initial-branch=master', remote.as_posix())
    git(git_root, 'clone', '--local', remote.as_posix(), author.as_posix())
    (author/'main.html').write_text('<!doctype html><meta charset="utf-8"><h1>合成课程学习首页</h1>', encoding='utf-8')
    for number in [1, 2, 3, 4, 5, 31, 32]:
        folder = author/f'lesson_{number}'
        folder.mkdir()
        (folder/f'lesson_{number}.html').write_text(f'<!doctype html><meta charset="utf-8"><h1>教材第{number}课 v1</h1>', encoding='utf-8')
    git(author, 'add', '.')
    git(author, '-c', 'user.name=Schedule Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'Synthetic lesson baselines')
    git(author, 'push', 'origin', 'master')
    git(git_root, 'clone', '--local', remote.as_posix(), imported.as_posix())
    mid = insert(conn, 'course_materials', {'teacher_id': teacher, 'name': 'schedule-fixture-course',
        'material_path': 'schedule-fixture-course', 'node_type': 'folder', 'preview_type': 'folder',
        'mime_type': 'inode/directory', 'owner_role': 'teacher', 'owner_user_pk': teacher, 'scope_level': 'private'})
    conn.execute('UPDATE course_materials SET root_id=? WHERE id=?', (mid,mid))
    root = dict(conn.execute('SELECT * FROM course_materials WHERE id=?', (mid,)).fetchone())
    material_git._sync_workspace_to_repository(conn, root, imported)
    material_git.refresh_root_git_metadata(conn, mid)
    for oid in [offering,sibling]:
        insert(conn, 'course_material_assignments', {'material_id': mid, 'class_offering_id': oid, 'assigned_by_teacher_id': teacher})
    initial = sync_repository_learning_bindings(conn, root, material_git._fetch_subtree_rows(conn, root), teacher)
    manual = clone(conn, 'course_materials', fixture['visualMaterialIds'][0], name='合成手工附加.md', material_path='schedule-manual-extra.md')
    conn.execute('UPDATE course_materials SET root_id=? WHERE id=?', (manual, manual))
    conn.commit()
    materials = {row['material_path'].removeprefix('schedule-fixture-course/'): row['id'] for row in conn.execute('SELECT id,material_path FROM course_materials WHERE root_id=?', (mid,))}
    fixture['scheduleMaterial'] = {'synthetic': True, 'semesterId': semester['id'], 'siblingOfferingId': sibling,
        'sessionIds': session_ids, 'oldThird': session_ids[2], 'newThird': session_ids[3], 'manualMaterialId': manual,
        'originalSlot': source, 'targetSlot': target, 'rootMaterialId': mid, 'materialIds': materials,
        'gitRemote': remote.as_posix(), 'gitAuthor': author.as_posix(), 'baselineSnapshot': {'official': official, 'requests': [], 'source_summary': []},
        'moveRequest': request, 'initialBindingSummary': initial}
    publish(conn, fixture, 'baseline')
    fixture['scheduleMaterial']['beforeSessions'] = [dict(row) for row in conn.execute('SELECT * FROM class_offering_sessions WHERE class_offering_id=? ORDER BY order_index', (offering,))]
    return {'seeded': True, 'sessionCount': len(session_ids), 'offeringIds': [offering, sibling], 'rootMaterialId': mid}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime-root', required=True, type=Path)
    parser.add_argument('--action', choices=['seed','pending','approved','bump'], default='seed')
    args = parser.parse_args()
    runtime = args.runtime_root.resolve()
    if runtime == ROOT/'.codex-temp' or not runtime.is_relative_to(ROOT/'.codex-temp'):
        raise ValueError('Only owned synthetic child runtimes are allowed')
    fixture_path = runtime/'fixture.json'
    fixture = json.loads(fixture_path.read_text('utf-8'))
    if fixture.get('uiV3Synthetic') is not True or Path(fixture['databasePath']).resolve() != runtime/'db/classroom.db':
        raise ValueError('Synthetic fixture identity mismatch')
    from tools.isolated_environment import isolate_sqlite_environment, guard_dotenv_loading, guard_postgres_connections
    isolate_sqlite_environment(runtime); guard_dotenv_loading(); guard_postgres_connections()
    with sqlite3.connect(fixture['databasePath']) as conn:
        conn.row_factory = sqlite3.Row
        if args.action == 'seed':
            result = seed(conn, runtime, fixture)
            fixture_path.write_text(json.dumps(fixture, ensure_ascii=False, indent=2), encoding='utf-8')
        else:
            if not fixture.get('scheduleMaterial',{}).get('synthetic'):
                raise ValueError('Seed the owned schedule material scenario first')
            if args.action == 'bump':
                author = Path(fixture['scheduleMaterial']['gitAuthor']).resolve()
                if not author.is_relative_to(runtime): raise ValueError('Git author escaped fixture')
                number = len(git(author,'rev-list','HEAD').splitlines())+1
                (author/'lesson_3/lesson_3.html').write_text(f'<!doctype html><meta charset="utf-8"><h1>教材第3课 v{number}</h1>', encoding='utf-8')
                git(author,'add','lesson_3/lesson_3.html')
                git(author,'-c','user.name=Schedule Fixture','-c','user.email=fixture@example.invalid','commit','-m',f'Synthetic lesson revision {number}')
                git(author,'push','origin','master')
                result={'remoteUpdated':True,'revision':number}
            else:
                result = publish(conn,fixture,args.action)
    print(json.dumps(result,ensure_ascii=False))


if __name__ == '__main__':
    main()
