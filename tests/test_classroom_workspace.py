import unittest
from datetime import date, datetime

from classroom_app.services.classroom_page_service import build_assignment_workspace_items, build_assignment_workspace_preview
from classroom_app.services.course_planning_service import decorate_offering_sessions


class ClassroomWorkspaceProjectionTests(unittest.TestCase):
    def _preview(self, assignments, role='student'):
        items = build_assignment_workspace_items(role=role, assignments=assignments)
        return build_assignment_workspace_preview(role=role, items=items, reference_time=datetime.fromisoformat('2026-09-05T12:00:00+08:00'))

    def test_ssr_teacher_rows_prioritize_grading_then_drafts_with_complete_counts(self):
        preview = self._preview([
            {'id': 1, 'status': 'new', 'countdown_at': '2026-09-05 12:30:00'},
            {'id': 2, 'teacher_submission_metrics': {'pending_grade_count': 3}, 'countdown_at': '2026-09-06 12:00:00'},
            {'id': 3, 'teacher_submission_metrics': {'grading_count': 2}},
            {'id': 4, 'status': 'closed'},
        ], 'teacher')
        self.assertEqual([row['task']['id'] for row in preview['rows']], [2, 1])
        self.assertEqual([row['action'] for row in preview['rows']], ['去批改', '继续编辑'])
        self.assertEqual((preview['total_count'], preview['actionable_count'], preview['draft_count']), (4, 2, 1))
        self.assertEqual(preview['rows'][0]['deadline_label'], '9/6 12:00')

    def test_ssr_student_preview_never_temporarily_offers_closed_submission_or_group_results(self):
        preview = self._preview([
            {'id': 1, 'submission_status': 'unsubmitted', 'is_accepting_submissions': False, 'deadline_phase': 'closed'},
            {'id': 2, 'submission_status': 'returned', 'can_resubmit_submission': True, 'resubmission_due_at': '2026-09-05 12:00:00'},
            {'id': 3, 'submission_status': 'graded', 'group_pending': True},
        ])
        self.assertEqual(preview['rows'], [])
        self.assertEqual((preview['total_count'], preview['actionable_count'], preview['submitted_count']), (3, 0, 1))

    def test_ssr_preview_uses_personal_deadline_and_retains_supplement_penalty(self):
        preview = self._preview([
            {'id': 1, 'is_accepting_submissions': True, 'is_late_submission_open': True, 'late_policy_label': '补交扣10分', 'countdown_at': '2026-09-07 12:00:00'},
            {'id': 2, 'exam_paper_id': 2, 'is_accepting_submissions': True, 'countdown_at': '2026-09-05 13:00:00'},
            {'id': 3, 'submission_status': 'returned', 'can_resubmit_submission': True, 'resubmission_due_at': '2026-09-05 12:30:00', 'countdown_at': '2026-09-09 12:00:00'},
        ])
        self.assertEqual([row['task']['id'] for row in preview['rows']], [3, 2, 1])
        self.assertEqual([row['action'] for row in preview['rows']], ['去重交', '进入考试', '去补交'])
        self.assertEqual(preview['rows'][2]['constraint'], '补交扣10分')

    def test_ssr_preview_reserves_only_actual_rows_and_keeps_urgent_overflow(self):
        preview = self._preview([{'id': i, 'is_accepting_submissions': True, 'countdown_at': '2026-09-05 13:00:00'} for i in range(1, 8)])
        self.assertEqual([row['task']['id'] for row in preview['rows']], [7, 6, 5, 4])
        self.assertEqual((len(preview['rows']), preview['actionable_count'], preview['urgent_overflow']), (4, 7, 3))
        empty = self._preview([])
        self.assertEqual((empty['rows'], empty['total_count']), ([], 0))

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
            'starts_at': '2026-09-05T02:00:00Z', 'created_at': '2026-09-01 09:00:00',
        }])[0]
        self.assertEqual(result['countdownAt'], '2026-09-05T12:00:00+08:00')
        self.assertEqual(result['serverNow'], '2026-09-05T11:00:00+08:00')
        self.assertEqual(result['startsAt'], '2026-09-05T10:00:00+08:00')
        self.assertEqual(result['createdAt'], '2026-09-01T09:00:00+08:00')

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
