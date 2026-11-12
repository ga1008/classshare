"""Course defaults remain reusable without overwriting another teacher tab."""
import unittest

from fastapi import HTTPException
from classroom_app.services import login_attendance_service as service
from test_login_attendance_service import LoginAttendanceServiceTests as Fixture


class AttendanceWorkflowDefaultsTests(unittest.TestCase):
    def setUp(self):
        Fixture.setUp(self)

    def test_begin_remembers_network_and_retry_does_not_rewrite_policy(self):
        first = Fixture.create(self, policy_revision=0)
        policy = service.load_policy(self.conn, 10)
        self.assertEqual(['192.0.2.0/24'], policy['networks'])
        self.assertEqual(1, policy['revision'])
        retry = Fixture.create(self, policy_revision=0)
        self.assertEqual(first['id'], retry['id'])
        self.assertEqual(1, service.load_policy(self.conn, 10)['revision'])
        next_lesson = service.session_view(self.conn, 10, 22, self.user)
        self.assertEqual(policy['networks'], next_lesson['policy']['networks'])
        self.assertEqual(service.rules.to_ms('2026-11-08T10:20:00+08:00'),
                         next_lesson['window']['starts_at_ms'])
        self.assertIsNone(next_lesson['window_error'])

    def test_stale_start_cannot_override_saved_network_or_create_round(self):
        service.save_policy(self.conn, 10, self.user,
                            {'revision': 0, 'networks': ['198.51.100.0/24']})
        self.conn.commit()
        with self.assertRaises(HTTPException) as error:
            Fixture.create(self, policy_revision=0)
        self.assertEqual(409, error.exception.status_code)
        self.assertEqual(0, self.conn.execute('SELECT COUNT(*) FROM login_attendance_rounds').fetchone()[0])
        self.assertEqual(['198.51.100.0/24'], service.load_policy(self.conn, 10)['networks'])

    def test_legacy_start_can_use_one_off_network_without_overwriting_default(self):
        service.save_policy(self.conn, 10, self.user,
                            {'revision': 0, 'networks': ['198.51.100.0/24']})
        self.conn.commit()
        created = Fixture.create(self)
        self.assertEqual(['192.0.2.0/24'], created['policy']['networks'])
        self.assertEqual(['198.51.100.0/24'], service.load_policy(self.conn, 10)['networks'])

    def test_unknown_schedule_still_opens_and_accepts_actual_duration(self):
        self.conn.execute("UPDATE class_offering_sessions SET academic_section_text='' WHERE id=21")
        self.conn.commit()
        view = service.session_view(self.conn, 10, 21, self.user)
        self.assertIsNone(view['window'])
        self.assertEqual(90,view['default_duration_minutes'])
        created = Fixture.create(self,duration_minutes=80)
        self.assertEqual(self.mock_now.return_value,created['starts_at_ms'])
        self.assertEqual(80*60000,created['ends_at_ms']-created['starts_at_ms'])

    def test_partial_time_edit_is_not_silently_ignored(self):
        with self.assertRaises(HTTPException) as error:
            Fixture.create(self, starts_at='2026-11-08T09:00:00+08:00')
        self.assertEqual(422, error.exception.status_code)
        self.assertEqual([], service.load_policy(self.conn, 10)['networks'])
