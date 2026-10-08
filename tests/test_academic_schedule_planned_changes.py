"""Approved-but-unapplied relations are scoped views of stored evidence only."""
import json
import unittest
from datetime import datetime

from tests import test_academic_schedule_overview as fixture
from tests.test_academic_schedule_predictions import base_snapshot, official, request, slot
from classroom_app.services.academic_schedule_prediction_service import load_teacher_prediction_snapshot, load_authorized_prediction_lessons
from classroom_app.services.student_course_schedule_service import build_student_course_schedule_overview
from classroom_app.services.schedule_editor_service import decorate_overview_with_drafts


class PlannedScheduleChangeTests(unittest.TestCase):
    setUp = fixture.AcademicScheduleOverviewTests.setUp
    publish = fixture.AcademicScheduleOverviewTests.publish
    teacher = fixture.AcademicScheduleOverviewTests.teacher

    def test_existing_snapshot_projects_plan_without_republishing_or_mutating_sessions_and_materials(self):
        self.publish(base_snapshot([request(status='approved')]))
        before = [tuple(row) for row in self.conn.execute('SELECT * FROM class_offering_sessions ORDER BY id')]
        materials = [tuple(row) for row in self.conn.execute('SELECT * FROM session_materials ORDER BY session_id')]
        writes = self.conn.total_changes
        result = self.teacher()
        plan, = result['planned_changes']
        self.assertEqual(('planned', 'approved', 'move', 101, 10, 3, 6), tuple(plan[key] for key in
                         ('phase', 'approval_status', 'kind', 'session_id', 'class_offering_id', 'original_week_index', 'proposed_week_index')))
        self.assertEqual('2026-09-20', plan['original']['date'])
        self.assertEqual('2026-10-11', plan['proposed']['date'])
        self.assertIn('待落实', result['message'])
        self.assertEqual((3, 6, 0), tuple(result['summary'][key] for key in ('slot_count', 'total_hours', 'prediction_count')))
        self.assertEqual(3, len([item for week in result['weeks'] for item in week['lessons']]))
        self.assertEqual(writes, self.conn.total_changes)
        self.assertEqual(before, [tuple(row) for row in self.conn.execute('SELECT * FROM class_offering_sessions ORDER BY id')])
        self.assertEqual(materials, [tuple(row) for row in self.conn.execute('SELECT * FROM session_materials ORDER BY session_id')])

    def test_transition_pending_to_planned_to_applied_retains_distinct_phases(self):
        snapshot = base_snapshot([request()])
        self.publish(snapshot)
        pending = self.teacher()
        self.assertEqual([], pending['planned_changes'])
        self.assertEqual(1, pending['summary']['prediction_count'])
        snapshot['requests'][0]['status'] = 'approved'
        self.publish(snapshot)
        planned = self.teacher()
        self.assertEqual(1, len(planned['planned_changes']))
        self.assertEqual([], planned['approved_changes'])
        snapshot['official'][0] = official('2026-10-11')
        self.publish(snapshot)
        applied = self.teacher()
        self.assertEqual([], applied['planned_changes'])
        self.assertEqual(1, len(applied['approved_changes']))
        self.assertEqual((3, 6, 0), tuple(applied['summary'][key] for key in ('slot_count', 'total_hours', 'prediction_count')))

    def test_room_change_and_cancel_have_one_origin_and_no_fake_time_destination(self):
        for change, kind in [(request(status='approved', proposed=slot('2026-09-20', room='B210')), 'room'),
                             (request(status='approved', kind='cancel'), 'cancel')]:
            with self.subTest(kind=kind):
                self.publish(base_snapshot([change]))
                result = self.teacher()
                self.assertEqual(kind, result['planned_changes'][0]['kind'])
                self.assertEqual(3, result['summary']['slot_count'])
                self.assertEqual([], result['approved_changes'])

    def test_applied_first_move_and_planned_next_move_coexist_without_a_second_resequence(self):
        snapshot = base_snapshot([request(status='approved')])
        snapshot['official'][0] = official('2026-10-11')
        self.publish(snapshot)
        before = [tuple(row) for row in self.conn.execute('SELECT id,session_date,order_index,learning_material_id FROM class_offering_sessions ORDER BY id')]
        snapshot['requests'].append(request('R2', status='approved', original=slot('2026-10-11'), proposed=slot('2026-10-18', (12, 13))))
        self.publish(snapshot)
        result = self.teacher()
        self.assertEqual('R1', result['approved_changes'][0]['request_id'])
        self.assertEqual('R2', result['planned_changes'][0]['request_id'])
        self.assertEqual(13, result['section_range']['max'])
        self.assertEqual(before, [tuple(row) for row in self.conn.execute('SELECT id,session_date,order_index,learning_material_id FROM class_offering_sessions ORDER BY id')])

    def test_ambiguous_requests_or_two_official_endpoints_do_not_choose_one(self):
        for changes in [[request(status='approved'), request('duplicate', status='approved')],
                        [request(status='approved'), request('pending', proposed=slot('2026-10-18'))]]:
            self.publish(base_snapshot(changes))
            self.assertEqual([], self.teacher()['planned_changes'])
            self.assertIn('planned_source_ambiguous', {row['code'] for row in self.teacher()['warnings']})
            self.assertEqual(0, self.teacher()['summary']['prediction_count'])
            self.assertFalse(any(item.get('adjustment') for week in self.teacher()['weeks'] for item in week['lessons']))
        snapshot = base_snapshot([request(status='approved')])
        snapshot['official'].append(official('2026-10-11'))
        self.publish(snapshot)
        self.assertEqual([], self.teacher()['planned_changes'])
        self.assertIn('planned_target_ambiguous', {row['code'] for row in self.teacher()['warnings']})

    def test_rejected_withdrawn_unknown_or_aged_out_approval_never_remains_a_live_plan(self):
        self.publish(base_snapshot([request(status='approved')]))
        for state in ['rejected', 'returned', 'draft', 'unknown', None]:
            self.publish(base_snapshot([request(status=state)] if state else []))
            self.assertEqual([], self.teacher()['planned_changes'])

    def test_teacher_filter_and_student_live_membership_limit_relations(self):
        self.publish(base_snapshot([request(status='approved')]))
        self.assertEqual([], self.teacher(course='其他')['planned_changes'])
        self.assertEqual([], load_authorized_prediction_lessons(self.conn, [20], semester_id=2)['planned_changes'])
        self.assertEqual(1, len(load_authorized_prediction_lessons(self.conn, [10], semester_id=1)['planned_changes']))
        for student_id, expected in [(1, 1), (2, 1), (3, 0)]:
            result = build_student_course_schedule_overview(self.conn, student_id, year='2026-2027', term='1', now=datetime(2026, 9, 19))
            self.assertEqual(expected, len(result.get('planned_changes', [])))

    def test_calendar_workday_is_independent_and_editor_keeps_plan_outside_business_lessons(self):
        self.conn.execute('INSERT INTO academic_semester_calendar_days VALUES(?,?,?,?,?,?,?,?,?,?)',
                          (1, '2026-10-10', 6, 6, 'workday', '调休上课', 'test', '', 1, json.dumps({'makeup_for_date': '2026-10-07'})))
        self.publish(base_snapshot([request(status='approved')]))
        result = self.teacher()
        decorated = decorate_overview_with_drafts(result, [])
        self.assertEqual(result['planned_changes'], decorated['planned_changes'])
        self.assertEqual(3, sum(len(week['lessons']) for week in decorated['weeks']))
        self.assertEqual('2026-10-11', decorated['planned_changes'][0]['proposed']['date'])
        self.assertNotIn('makeup_for_date', decorated['planned_changes'][0])
        self.assertEqual('2026-10-10', self.conn.execute('SELECT date FROM academic_semester_calendar_days').fetchone()[0])
