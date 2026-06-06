import unittest
from unittest.mock import patch

from classroom_app.services import classroom_interaction_service as service


class FakeRow(dict):
    def keys(self):
        return super().keys()


class FakeCursor:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class FakeConnection:
    def __init__(self):
        self.execute_calls = []

    def cursor(self):
        raise AssertionError("classroom interaction write paths must not use raw cursor()")

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        self.execute_calls.append((normalized, tuple(params)))
        if normalized.startswith("SELECT COUNT(*) AS total"):
            return FakeCursor(FakeRow({"total": 0}))
        if normalized.startswith("SELECT id FROM classroom_live_help_signals"):
            return FakeCursor(None)
        return FakeCursor()


class ClassroomInteractionPostgresWriteTests(unittest.TestCase):
    def test_create_activity_uses_insert_returning_helper(self):
        conn = FakeConnection()

        with patch.object(
            service,
            "ensure_classroom_interaction_access",
            return_value={"course_name": "Course", "class_name": "Class"},
        ), patch.object(
            service,
            "execute_insert_returning_id",
            return_value=101,
        ) as insert_helper, patch.object(
            service,
            "load_activity_detail",
            return_value={"id": 101},
        ) as load_detail:
            result = service.create_activity(
                conn,
                20,
                {"id": 3, "role": "teacher", "name": "Teacher"},
                {
                    "kind": service.ACTIVITY_KIND_POLL,
                    "prompt": "Ready?",
                    "options": [{"label": "Yes"}, {"label": "No"}],
                },
            )

        self.assertEqual({"id": 101}, result)
        self.assertEqual(1, insert_helper.call_count)
        self.assertIn("INSERT INTO classroom_live_activities", insert_helper.call_args.args[1])
        load_detail.assert_called_once_with(conn, 101, {"id": 3, "role": "teacher", "name": "Teacher"})

    def test_submit_question_uses_insert_returning_helper(self):
        conn = FakeConnection()
        activity = FakeRow(
            {
                "id": 20,
                "kind": service.ACTIVITY_KIND_QNA,
                "class_offering_id": 30,
                "allow_anonymous": 1,
                "status": service.ACTIVITY_STATUS_ACTIVE,
            }
        )

        with patch.object(service, "_ensure_activity_access", return_value=activity), patch.object(
            service,
            "execute_insert_returning_id",
            return_value=202,
        ) as insert_helper, patch.object(
            service,
            "load_question",
            return_value={"id": 202},
        ) as load_question:
            result = service.submit_question(
                conn,
                20,
                {"id": 10, "role": "student", "name": "Student"},
                {"question_text": "Why?", "is_anonymous": True},
            )

        self.assertEqual({"id": 202}, result)
        self.assertEqual(1, insert_helper.call_count)
        self.assertIn("INSERT INTO classroom_live_questions", insert_helper.call_args.args[1])
        load_question.assert_called_once()

    def test_set_help_signal_uses_insert_returning_helper(self):
        conn = FakeConnection()

        with patch.object(service, "ensure_classroom_interaction_access", return_value={}), patch.object(
            service,
            "execute_insert_returning_id",
            return_value=303,
        ) as insert_helper, patch.object(
            service,
            "load_signal",
            return_value={"id": 303},
        ) as load_signal:
            result = service.set_help_signal(
                conn,
                20,
                {"id": 10, "role": "student", "name": "Student"},
                {"signal_type": "help", "message": "Need help"},
            )

        self.assertEqual({"id": 303}, result)
        self.assertEqual(1, insert_helper.call_count)
        self.assertIn("INSERT INTO classroom_live_help_signals", insert_helper.call_args.args[1])
        load_signal.assert_called_once_with(conn, 303, {"id": 10, "role": "student", "name": "Student"})


if __name__ == "__main__":
    unittest.main()
