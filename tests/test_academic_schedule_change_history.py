"""Approved display relations are scoped read data, never extra business lessons."""
import unittest
from datetime import datetime

from tests import test_academic_schedule_overview as fixture
from tests.test_academic_schedule_predictions import base_snapshot, official, request, slot
from classroom_app.services.academic_schedule_prediction_service import load_teacher_prediction_snapshot, load_authorized_prediction_lessons
from classroom_app.services.student_course_schedule_service import build_student_course_schedule_overview


class ApprovedScheduleHistoryTests(unittest.TestCase):
    setUp = fixture.AcademicScheduleOverviewTests.setUp
    publish = fixture.AcademicScheduleOverviewTests.publish
    teacher = fixture.AcademicScheduleOverviewTests.teacher

    def effective(self):
        self.publish(base_snapshot([request()]))
        snapshot = base_snapshot([request(status='approved')])
        snapshot['official'][0] = official('2026-10-11')
        self.publish(snapshot)
        return snapshot

    def test_approved_edges_do_not_add_sessions_predictions_or_hours(self):
        self.effective()
        stored = load_teacher_prediction_snapshot(self.conn, 1, 1)
        self.assertEqual(3, len(stored['lessons']))
        self.assertEqual([], stored['predicted_lessons'])
        self.assertEqual(1, len(stored['approved_changes']))
        relation = stored['approved_changes'][0]
        self.assertEqual(('approved', 'move', 101, 10, 3, 6), tuple(relation[key] for key in
                         ('phase', 'kind', 'session_id', 'class_offering_id', 'original_week_index', 'effective_week_index')))
        self.assertEqual('2026-09-20', relation['original']['date'])
        self.assertEqual('2026-10-11', relation['proposed']['date'])
        teacher = self.teacher()
        self.assertEqual((3, 6, 0), tuple(teacher['summary'][key] for key in ('slot_count', 'total_hours', 'prediction_count')))
        self.assertEqual(stored['approved_changes'], teacher['approved_changes'])
        self.assertEqual([], self.teacher(course='不在当前筛选')['approved_changes'])

    def test_approved_and_later_pending_relations_coexist(self):
        snapshot = self.effective()
        snapshot['requests'].append(request('R2', original=slot('2026-10-11'), proposed=slot('2026-10-18')))
        self.publish(snapshot)
        result = self.teacher()
        self.assertEqual(1, len(result['approved_changes']))
        self.assertEqual(1, result['summary']['prediction_count'])
        pending = [lesson for week in result['weeks'] for lesson in week['lessons'] if lesson.get('adjustment')]
        self.assertEqual(2, len(pending))
        self.assertEqual({'R2'}, {row['adjustment']['request_id'] for row in pending})

    def test_retained_approval_remains_when_remote_list_ages_out_but_not_after_another_move(self):
        snapshot = self.effective()
        snapshot['requests'] = []
        self.publish(snapshot)
        self.assertEqual(1, len(self.teacher()['approved_changes']))
        snapshot['official'][0] = official('2026-10-18')
        self.publish(snapshot)
        self.assertEqual([], self.teacher()['approved_changes'])

    def test_unconfirmed_approval_room_only_cancel_and_unknown_never_invent_time_edges(self):
        self.publish(base_snapshot([request(status='approved')]))
        self.assertEqual([], self.teacher()['approved_changes'])
        for change in [request(status='approved', proposed=slot('2026-09-20', room='B210')),
                       request(status='approved', kind='cancel'), request(status='unknown')]:
            self.publish(base_snapshot([change]))
            self.assertEqual([], self.teacher()['approved_changes'])

    def test_duplicate_approved_claims_do_not_choose_one_arbitrarily(self):
        snapshot = self.effective()
        snapshot['requests'].append(request('DUP', status='approved'))
        self.publish(snapshot)
        self.assertEqual([], self.teacher()['approved_changes'])

    def test_student_scope_includes_only_authorized_change_evidence(self):
        self.effective()
        authorized = load_authorized_prediction_lessons(self.conn, [10], semester_id=1)
        self.assertEqual(1, len(authorized['approved_changes']))
        denied = load_authorized_prediction_lessons(self.conn, [20], semester_id=2)
        self.assertEqual([], denied['approved_changes'])
        student = build_student_course_schedule_overview(self.conn, 1, year='2026-2027', term='1', now=datetime(2026, 9, 19))
        self.assertEqual(1, len(student['approved_changes']))
        self.assertEqual((3, 6, 0), tuple(student['summary'][key] for key in ('slot_count', 'total_hours', 'prediction_count')))
