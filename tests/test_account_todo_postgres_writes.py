import unittest
from unittest.mock import patch

from classroom_app.services import (
    email_notification_service,
    smart_attendance_entry_service,
    student_auth_service,
    teacher_account_service,
    todo_service,
)


class FakeRow(dict):
    def keys(self):
        return super().keys()


class FakeCursor:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class FakeConnection:
    def __init__(self, row=None):
        self.execute_calls = []
        self.row = row

    def cursor(self):
        raise AssertionError("account/todo write paths must not use raw cursor()")

    def execute(self, sql, params=()):
        self.execute_calls.append((" ".join(str(sql).split()), tuple(params)))
        return FakeCursor(self.row)


class AccountTodoPostgresWriteTests(unittest.TestCase):
    def test_create_teacher_email_config_uses_insert_returning_helper(self):
        conn = FakeConnection()
        normalized = {
            "label": "School mail",
            "provider": "custom",
            "smtp_host": "smtp.example.test",
            "smtp_port": 465,
            "smtp_security": "ssl",
            "smtp_username": "teacher@example.test",
            "smtp_password_encrypted": "encrypted",
            "from_email": "teacher@example.test",
            "from_name": "Teacher",
            "imap_host": "imap.example.test",
            "imap_port": 993,
            "imap_security": "ssl",
            "imap_username": "teacher@example.test",
            "imap_password_encrypted": "encrypted",
            "enabled": 1,
            "is_default": 1,
            "per_minute_limit": 5,
            "daily_limit": 50,
        }

        with patch.object(
            email_notification_service,
            "_normalize_config_payload",
            return_value=normalized,
        ), patch.object(
            email_notification_service,
            "execute_insert_returning_id",
            return_value=111,
        ) as insert_helper, patch.object(
            email_notification_service,
            "get_teacher_email_config",
            return_value=FakeRow({"id": 111}),
        ) as get_config, patch.object(
            email_notification_service,
            "_serialize_email_config",
            return_value={"id": 111},
        ):
            result = email_notification_service.create_teacher_email_config(conn, 3, {})

        self.assertEqual({"id": 111}, result)
        self.assertEqual(1, insert_helper.call_count)
        self.assertIn("INSERT INTO teacher_email_configs", insert_helper.call_args.args[1])
        get_config.assert_called_once_with(conn, 3, 111)

    def test_create_password_reset_request_uses_insert_returning_helper(self):
        conn = FakeConnection()
        student_row = FakeRow(
            {
                "id": 10,
                "class_id": 20,
                "created_by_teacher_id": 30,
                "name": "Student",
                "student_id_number": "S001",
                "class_name": "Class",
            }
        )

        with patch.object(
            student_auth_service,
            "execute_insert_returning_id",
            return_value=222,
        ) as insert_helper:
            request_id = student_auth_service.create_password_reset_request(
                conn,
                student_row=student_row,
                requester_ip="127.0.0.1",
                requester_user_agent="Mozilla/5.0",
            )

        self.assertEqual(222, request_id)
        self.assertEqual(1, insert_helper.call_count)
        self.assertIn("INSERT INTO student_password_reset_requests", insert_helper.call_args.args[1])

    def test_create_teacher_account_uses_insert_returning_helper(self):
        conn = FakeConnection()

        with patch.object(teacher_account_service, "_teacher_exists_by_email", return_value=None), patch.object(
            teacher_account_service,
            "get_password_hash",
            return_value="hashed",
        ), patch.object(
            teacher_account_service,
            "execute_insert_returning_id",
            return_value=333,
        ) as insert_helper, patch.object(
            teacher_account_service,
            "upsert_teacher_membership",
            return_value=None,
        ) as upsert_membership, patch.object(
            teacher_account_service,
            "get_teacher_account",
            return_value={"id": 333},
        ):
            result = teacher_account_service.create_teacher_account(
                conn,
                actor_teacher_id=1,
                name="Teacher",
                email="teacher@example.test",
                password="password123",
                school_code="gxufl",
                school_name="School",
            )

        self.assertEqual({"id": 333}, result)
        self.assertEqual(1, insert_helper.call_count)
        self.assertIn("INSERT INTO teachers", insert_helper.call_args.args[1])
        upsert_membership.assert_called_once()
        self.assertEqual(333, upsert_membership.call_args.kwargs["teacher_id"])

    def test_create_manual_todo_uses_insert_returning_helper(self):
        conn = FakeConnection()

        with patch.object(todo_service, "execute_insert_returning_id", return_value=444) as insert_helper:
            result = todo_service.create_manual_todo(
                conn,
                class_offering_id=20,
                user={"id": 10, "role": "student", "name": "Student"},
                payload={"title": "Read chapter 1"},
            )

        self.assertEqual(444, result["id"])
        self.assertEqual(1, insert_helper.call_count)
        self.assertIn("INSERT INTO classroom_todos", insert_helper.call_args.args[1])

    def test_postgres_smart_attendance_daily_task_uses_conflict_returning(self):
        conn = FakeConnection(row=FakeRow({"id": 555}))

        with patch.object(smart_attendance_entry_service, "get_configured_db_engine", return_value="postgres"):
            task_id = smart_attendance_entry_service.maybe_enqueue_teacher_daily_checkin_sync(
                conn,
                class_offering_id=20,
                teacher_id=3,
            )

        self.assertEqual(555, task_id)
        self.assertEqual(1, len(conn.execute_calls))
        self.assertIn("ON CONFLICT", conn.execute_calls[0][0])
        self.assertIn("RETURNING id", conn.execute_calls[0][0])

    def test_postgres_smart_attendance_daily_task_duplicate_returns_none(self):
        conn = FakeConnection(row=None)

        with patch.object(smart_attendance_entry_service, "get_configured_db_engine", return_value="postgres"):
            task_id = smart_attendance_entry_service.maybe_enqueue_teacher_daily_checkin_sync(
                conn,
                class_offering_id=20,
                teacher_id=3,
            )

        self.assertIsNone(task_id)
        self.assertIn("DO NOTHING", conn.execute_calls[0][0])


if __name__ == "__main__":
    unittest.main()
