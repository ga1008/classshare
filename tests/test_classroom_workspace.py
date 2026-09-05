import unittest
from datetime import date

from classroom_app.services.classroom_page_service import build_assignment_workspace_items
from classroom_app.services.course_planning_service import decorate_offering_sessions


class ClassroomWorkspaceProjectionTests(unittest.TestCase):
    def test_student_projection_carries_permissions_without_feedback_or_inferred_session(self):
        result = build_assignment_workspace_items(role='student', assignments=[{
            'id': 7, 'title': '第2次课练习', 'status': 'published', 'submission_status': 'returned',
            'is_accepting_submissions': False, 'can_resubmit_submission': True,
            'resubmission_due_at': '2026-09-06 12:00:00', 'group_pending': True,
            'submission_score': 98, 'submission_feedback_md': 'private feedback', 'session_id': 99,
        }])[0]
        self.assertTrue(result['canResubmit'])
        self.assertFalse(result['accepting'])
        self.assertTrue(result['groupPending'])
        self.assertEqual(result['resubmissionDueAt'], '2026-09-06T12:00:00+08:00')
        self.assertFalse({'submission_score', 'submission_feedback_md', 'session_id', 'pendingGrade'} & result.keys())

    def test_teacher_projection_preserves_grading_counts(self):
        result = build_assignment_workspace_items(role='teacher', assignments=[{
            'id': 8, 'teacher_submission_metrics': {'pending_grade_count': '3', 'grading_count': 2, 'returned_count': 1}
        }])[0]
        self.assertEqual((result['pendingGrade'], result['grading'], result['returned']), (3, 2, 1))

    def test_projection_dates_preserve_the_same_instant_in_any_browser_timezone(self):
        result = build_assignment_workspace_items(role='student', assignments=[{
            'id': 1, 'countdown_at': '2026-09-05T04:00:00Z', 'server_now': '2026-09-05 11:00:00',
            'starts_at': '2026-09-05T02:00:00Z',
        }])[0]
        self.assertEqual(result['countdownAt'], '2026-09-05T12:00:00+08:00')
        self.assertEqual(result['serverNow'], '2026-09-05T11:00:00+08:00')
        self.assertEqual(result['startsAt'], '2026-09-05T10:00:00+08:00')

    def test_schedule_summary_does_not_repeat_generated_teacher_instructions(self):
        content = '第 1 次课，按教务实际排课自动生成，请补充本次课要讲的知识点、实验内容或案例任务。\n上课时间：2026-09-06\n上课地点：B310'
        row = {'id': 1, 'title': '网络 第 1 次课', 'order_index': 1, 'session_date': '2026-09-06',
               'section_count': 2, 'schedule_source': 'academic_sync', 'academic_section_text': '4-5',
               'academic_location': 'B310', 'content': content}
        session = decorate_offering_sessions([row], reference_date=date(2026, 9, 5))['sessions'][0]
        self.assertEqual(session['workspace_summary'], '')
        self.assertEqual(session['detail_content'], content)
        self.assertIn('B310', session['workspace_meta'])
        self.assertNotIn('教务周次', session['workspace_meta'])

    def test_user_authored_content_remains_available_in_details_and_summary(self):
        row = {'id': 1, 'title': '网络', 'order_index': 1, 'session_date': '2026-09-06', 'section_count': 2,
               'content': '理解网络协议分层，完成抓包实验。', 'schedule_source': 'academic_sync'}
        session = decorate_offering_sessions([row], reference_date=date(2026, 9, 5))['sessions'][0]
        self.assertEqual(session['workspace_summary'], row['content'])
        self.assertEqual(session['detail_content'], row['content'])

    def test_c18_teacher_text_with_schedule_prefix_is_not_deleted_from_mixed_summary(self):
        intro = '第 1 次课，按教务实际排课自动生成，请补充本次课要讲的知识点、实验内容或案例任务。'
        content = intro + '\n上课时间：2026-09-06\n上课地点：B310\n教师补充：准备抓包。\n上课地点：比较两校区的网络接入差异。'
        row = {'id': 1, 'title': '网络', 'order_index': 1, 'session_date': '2026-09-06',
               'schedule_source': 'academic_sync', 'academic_location': 'B310', 'content': content}
        result = decorate_offering_sessions([row], reference_date=date(2026, 9, 5))['sessions'][0]
        self.assertEqual('教师补充：准备抓包。 上课地点：比较两校区的网络接入差异。', result['workspace_summary'])
        self.assertEqual(content, result['detail_content'])


if __name__ == '__main__':
    unittest.main()
