import unittest
from unittest.mock import patch

from classroom_app.services import behavior_tracking_service


class FakeCursor:
    def fetchone(self):
        return None


class FakeConnection:
    def __init__(self):
        self.execute_calls = []

    def cursor(self):
        raise AssertionError("behavior write paths must not use raw cursor()")

    def execute(self, sql, params=()):
        self.execute_calls.append((" ".join(str(sql).split()), tuple(params)))
        return FakeCursor()


class BehaviorPostgresWriteTests(unittest.TestCase):
    def test_behavior_event_batch_uses_insert_returning_helper(self):
        conn = FakeConnection()

        with patch.object(behavior_tracking_service, "_ensure_behavior_state_row", return_value=None), patch.object(
            behavior_tracking_service,
            "execute_insert_returning_id",
            return_value=777,
        ) as insert_helper:
            result = behavior_tracking_service._record_behavior_batch_in_connection(
                conn,
                class_offering_id=20,
                user_pk=10,
                user_role="student",
                display_name="Student",
                page_key="classroom",
                events=[{"action_type": "page_action", "summary_text": "Opened page"}],
            )

        self.assertEqual([777], result["logged_event_ids"])
        self.assertEqual(1, insert_helper.call_count)
        self.assertIn("INSERT INTO classroom_behavior_events", insert_helper.call_args.args[1])
        self.assertTrue(
            any(call[0].startswith("UPDATE classroom_behavior_states") for call in conn.execute_calls)
        )


if __name__ == "__main__":
    unittest.main()
