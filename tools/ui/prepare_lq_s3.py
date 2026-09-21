"""Add independent S3 scenarios only to an already asserted synthetic fixture."""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def insert(conn, table, values):
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if not set(values).issubset(columns):
        raise ValueError(f"Unexpected {table} seed columns: {set(values) - columns}")
    names = ','.join(values)
    placeholders = ','.join('?' for _ in values)
    return conn.execute(f"INSERT INTO {table} ({names}) VALUES ({placeholders})", tuple(values.values())).lastrowid


def clone(conn, table, source_id, **changes):
    source = conn.execute(f"SELECT * FROM {table} WHERE id=?", (source_id,)).fetchone()
    if source is None:
        raise ValueError(f"Missing synthetic source in {table}")
    values = dict(source)
    values.pop('id')
    values.update(changes)
    return insert(conn, table, values)


def prepare(runtime: Path):
    runtime = runtime.resolve()
    temporary = (ROOT / '.codex-temp').resolve()
    if runtime == temporary or not runtime.is_relative_to(temporary):
        raise ValueError('S3 seed must stay in an owned child of .codex-temp')
    fixture_path = runtime / 'fixture.json'
    fixture = json.loads(fixture_path.read_text(encoding='utf-8'))
    database = runtime / 'db/classroom.db'
    if fixture.get('uiV3Synthetic') is not True or Path(fixture['databasePath']).resolve() != database:
        raise ValueError('S3 seed requires the exact synthetic SQLite identity')
    if fixture.get('lqS3Synthetic') or fixture.get('s3'):
        raise ValueError('S3 scenarios already exist; use a fresh fixture rather than resetting results')
    teacher, student = fixture['teacher']['id'], fixture['student']['id']
    now = datetime.now().replace(microsecond=0)
    stamp = now.isoformat()
    questions = {'title': 'S3 协议分层试卷', 'grading': {'total_score': 100, 'description': '说明分工与协作。'},
                 'pages': [{'id': 'p1', 'name': '协议分层', 'questions': [{
                     'id': 'q1', 'type': 'textarea', 'text': '说明协议分层的作用。', 'points': 100,
                     'answer': '各层分工，通过接口协作。', 'grading_guidance': '分工50分，协作50分。',
                     'deduction_points': '每漏一个要点扣50分。'}]}]}
    s3 = {}
    with sqlite3.connect(database) as conn:
        conn.row_factory = sqlite3.Row
        # A failed prior run must be inspected, never silently overwrite its rows.
        if conn.execute("SELECT 1 FROM exam_papers WHERE id LIKE 'lq-s3-%' LIMIT 1").fetchone():
            raise ValueError('Partial S3 seed detected; retain it and use a fresh runtime')
        base_assignment = fixture['studentSubmissionAssignmentId']
        base_submission = fixture['teacherReviewSubmissionId']
        for name in ('authoring', 'examTake', 'examFailure', 'examDraft', 'examDeadline'):
            paper = f'lq-s3-{name}'
            insert(conn, 'exam_papers', {'id': paper, 'teacher_id': teacher, 'title': f'S3 {name} 试卷',
                   'description': '独立合成验收材料', 'questions_json': json.dumps(questions, ensure_ascii=False),
                   'exam_config_json': '{}', 'status': 'ready', 'owner_role': 'teacher',
                   'owner_user_pk': teacher, 'scope_level': 'private', 'created_at': stamp, 'updated_at': stamp})
            if name == 'authoring':
                s3['authoringPaperId'] = paper
            else:
                s3[f'{name}AssignmentId'] = clone(conn, 'assignments', base_assignment,
                    title=f'S3 {name} 独立任务', exam_paper_id=paper, due_at=None, created_at=stamp)
        for name in ('draft', 'concurrency', 'return', 'wrong'):
            paper = None
            if name == 'wrong':
                paper = 'lq-s3-wrong'
                insert(conn, 'exam_papers', {'id': paper, 'teacher_id': teacher, 'title': 'S3 错题归集试卷',
                    'questions_json': json.dumps(questions, ensure_ascii=False), 'exam_config_json': '{}',
                    'status': 'ready', 'owner_role': 'teacher', 'owner_user_pk': teacher, 'scope_level': 'private'})
            aid = clone(conn, 'assignments', base_assignment, title=f'S3 {name} 独立任务',
                        exam_paper_id=paper, due_at=None, created_at=stamp)
            s3[f'{name}AssignmentId'] = aid
            if name != 'draft':
                s3[f'{name}SubmissionId'] = clone(conn, 'submissions', base_submission,
                    assignment_id=str(aid), score=None if name == 'concurrency' else 40,
                    status='submitted' if name == 'concurrency' else 'graded',
                    feedback_md='## 第1题\n得分：40/100\n缺少协作的说明。', submitted_at=stamp,
                    answers_json=json.dumps({'answers': [{'question_id': 'q1', 'question': '说明协议分层的作用。',
                                                          'answer': '各层分别完成任务。'}]}, ensure_ascii=False))
        s3['textbookId'] = insert(conn, 'textbooks', {'teacher_id': teacher, 'title': 'S3 网络教材',
            'publisher': '合成出版社', 'authors_json': '["林老师"]', 'tags_json': '["S3"]',
            'owner_role': 'teacher', 'owner_user_pk': teacher, 'scope_level': 'private'})

        # Management form tests need an editable semester and a complete real
        # preview, independent of the dashboard's intentionally minimal term.
        base_offering = dict(conn.execute('SELECT * FROM class_offerings WHERE id=?', (fixture['classOfferingId'],)).fetchone())
        s3['semesterId'] = clone(conn, 'academic_semesters', base_offering['semester_id'],
            name='S3 独立表单学期', calendar_sync_status='generated', calendar_sync_message='独立合成校历',
            calendar_sync_at=stamp, updated_at=stamp)
        manage_course = clone(conn, 'courses', fixture['courseId'], name='S3 独立开课表单课程', total_hours=4)
        for index in (1, 2):
            insert(conn, 'course_lessons', {'course_id': manage_course, 'order_index': index,
                'title': f'S3 课堂内容 {index}', 'content': '协议分层与协作。', 'section_count': 2})
        s3['manageOfferingId'] = clone(conn, 'class_offerings', fixture['classOfferingId'],
            course_id=manage_course, semester_id=s3['semesterId'], semester='S3 独立表单学期',
            textbook_id=s3['textbookId'], weekly_schedule_json=json.dumps([{'weekday': 0, 'section_count': 2}]),
            schedule_info='S3 固定周循环', schedule_source='fixed_cycle', home_learning_material_id=None)

        course = clone(conn, 'courses', fixture['courseId'], name='S3 长课程名：网络协议与数据交换原理 Protocol Layering')
        offering = clone(conn, 'class_offerings', fixture['classOfferingId'], course_id=course,
                         schedule_info='', home_learning_material_id=None)
        cases = [('graded', 80, 'graded', 0, 0), ('zero', 0, 'graded', 1, 0),
                 ('pending', None, 'submitted', 0, 0), ('returned', 91, 'graded', 0, 1),
                 ('hiddenGroup', 92, 'graded', 0, 0), ('regrading', 70, 'grading', 0, 0)]
        report_assignments, report_submissions = {}, {}
        for index, (name, score, status, absence, returned) in enumerate(cases):
            date = (now - timedelta(days=12-index)).isoformat()
            aid = clone(conn, 'assignments', base_assignment, title=f'S3 成绩 {name}', course_id=course,
                        class_offering_id=offering, assessment_kind='homework', due_at=None, created_at=date)
            sid = clone(conn, 'submissions', base_submission, assignment_id=str(aid), score=score,
                        status=status, is_absence_score=absence, resubmission_allowed=returned,
                        returned_at=date if returned else None, submitted_at=date, feedback_md='S3 本人成绩')
            report_assignments[name], report_submissions[name] = aid, sid
        scheme = insert(conn, 'group_schemes', {'class_offering_id': offering, 'name': 'S3 未公布组',
                                                'created_by_teacher_id': teacher})
        group = insert(conn, 'study_groups', {'class_offering_id': offering, 'name': 'S3 未公布组',
                    'created_by_role': 'teacher', 'created_by_user_pk': teacher, 'scheme_id': scheme})
        insert(conn, 'study_group_members', {'group_id': group, 'student_id': student,
                                            'member_role': 'member', 'status': 'active'})
        hidden = report_assignments['hiddenGroup']
        insert(conn, 'assignment_group_bindings', {'assignment_id': str(hidden), 'class_offering_id': offering,
                                                  'scheme_id': scheme, 'created_by_teacher_id': teacher})
        insert(conn, 'group_assignment_member_results', {'assignment_id': str(hidden), 'class_offering_id': offering,
            'group_id': group, 'student_pk_id': student, 'submission_id': report_submissions['hiddenGroup'],
            'work_score': 92, 'final_score': 89, 'revealed': 0})
        semester = conn.execute('SELECT semester_id FROM class_offerings WHERE id=?', (offering,)).fetchone()[0]
        publication = insert(conn, 'grade_publications', {'class_offering_id': offering, 'semester_id': semester,
            'teacher_id': teacher, 'version': 1, 'status': 'active', 'source_record_id': 0,
            'source_document_type': 'synthetic_s3', 'source_hash': '0'*64,
            'source_snapshot_json': json.dumps({'course_name': 'S3 已公布课程', 'semester_name': 'S3 合成学期'}),
            'formula_json': json.dumps({'text': 'S3 冻结总评'}), 'published_at': stamp})
        for identity, score in ((fixture['student'], 0), (fixture['otherStudent'], 91)):
            insert(conn, 'grade_publication_students', {'publication_id': publication, 'student_pk_id': identity['id'],
                'student_number': identity['studentNumber'], 'scores_json': json.dumps({
                    'ordinary_score': score, 'final_exam_score': score, 'overall_score': score})})
        conn.commit()
    fixture.update(lqS3Synthetic=True, s3=s3, reportCard={'studentId': student, 'offeringId': offering,
        'publicationId': publication, 'assignmentIds': report_assignments, 'expectedMine': [80, 0, None, None, None, 70]})
    fixture_path.write_text(json.dumps(fixture, ensure_ascii=True, indent=2), encoding='utf-8')
    print(json.dumps({'s3_synthetic': True, 'runtime': str(runtime), 'independentScenarios': len(s3),
                      'reportRecords': len(cases), 'credentials': 'not printed'}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runtime', type=Path)
    prepare(parser.parse_args().runtime)
