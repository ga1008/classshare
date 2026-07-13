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

    def commit(self):
        pass


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

    def test_create_manual_todo_persists_priority_in_metadata(self):
        conn = FakeConnection()

        with patch.object(todo_service, "execute_insert_returning_id", return_value=445) as insert_helper:
            todo_service.create_manual_todo(
                conn,
                class_offering_id=20,
                user={"id": 10, "role": "student", "name": "Student"},
                payload={"title": "Revise lab report", "priority": "high"},
            )

        params = insert_helper.call_args.args[2]
        metadata_blob = next((p for p in params if isinstance(p, str) and "priority" in p), "")
        self.assertIn('"priority": "high"', metadata_blob)

    def test_create_manual_todo_defaults_priority_to_normal(self):
        conn = FakeConnection()

        with patch.object(todo_service, "execute_insert_returning_id", return_value=446):
            todo_service.create_manual_todo(
                conn,
                class_offering_id=20,
                user={"id": 10, "role": "student", "name": "Student"},
                payload={"title": "Plain todo", "priority": "bogus"},
            )

        # An unknown priority falls back to the default rather than raising.
        self.assertEqual("normal", todo_service.normalize_priority("bogus"))

    def test_teacher_can_create_manual_todo(self):
        conn = FakeConnection()

        with patch.object(todo_service, "execute_insert_returning_id", return_value=447) as insert_helper:
            result = todo_service.create_manual_todo(
                conn,
                class_offering_id=20,
                user={"id": 3, "role": "teacher", "name": "Teacher"},
                payload={"title": "准备教研材料"},
            )

        self.assertEqual(447, result["id"])
        params = insert_helper.call_args.args[2]
        self.assertIn("teacher", params)

    def test_teacher_personal_todo_persists_without_classroom(self):
        conn = FakeConnection()

        with patch.object(todo_service, "execute_insert_returning_id", return_value=448) as insert_helper:
            result = todo_service.create_manual_todo(
                conn,
                class_offering_id=None,
                user={"id": 3, "role": "teacher", "name": "Teacher"},
                payload={"title": "Prepare annual review"},
            )

        self.assertEqual(448, result["id"])
        params = insert_helper.call_args.args[2]
        self.assertIsNone(params[0])

    def test_teacher_email_reminder_requires_ready_email_pipeline(self):
        from datetime import timedelta
        from classroom_app.services.academic_service import china_now

        conn = FakeConnection()
        due = (china_now().replace(tzinfo=None) + timedelta(days=2)).isoformat(timespec="minutes")
        with patch.object(todo_service, "get_teacher_email_reminder_readiness", return_value={
            "available": False,
            "reason": "请先配置并启用默认发信邮箱。",
        }), self.assertRaises(todo_service.TodoValidationError):
            todo_service.create_manual_todo(
                conn,
                class_offering_id=20,
                user={"id": 3, "role": "teacher", "name": "Teacher"},
                payload={"title": "准备教研材料", "due_at": due, "email_reminder_enabled": True},
            )

    def test_manual_items_surface_priority_fields(self):
        from datetime import datetime

        now = datetime(2026, 6, 21, 12, 0)
        rows = [
            {
                "id": 7,
                "title": "High priority task",
                "notes": "",
                "start_at": None,
                "due_at": "2026-06-25T23:59",
                "created_at": "2026-06-21T12:00",
                "completed_at": None,
                "metadata_json": '{"priority": "high"}',
            }
        ]
        items = todo_service._manual_items(rows, now)
        self.assertEqual(1, len(items))
        self.assertEqual("high", items[0]["priority"])
        self.assertTrue(items[0]["is_high_priority"])
        self.assertEqual(0, items[0]["priority_rank"])

    def test_normalize_reminder_disables_without_deadline_and_clamps(self):
        off = todo_service.normalize_reminder({"reminder_enabled": True}, has_deadline=False)
        self.assertFalse(off["enabled"])
        clamped = todo_service.normalize_reminder(
            {"reminder_enabled": True, "reminder_lead_minutes": 9_999_999},
            has_deadline=True,
        )
        self.assertTrue(clamped["enabled"])
        self.assertEqual(todo_service.TODO_REMINDER_MAX_LEAD_MINUTES, clamped["lead_minutes"])
        teacher_email = todo_service.normalize_reminder(
            {"email_reminder_enabled": True},
            has_deadline=True,
            allow_email=True,
        )
        self.assertTrue(teacher_email["email_enabled"])
        student_email = todo_service.normalize_reminder(
            {"email_reminder_enabled": True},
            has_deadline=True,
            allow_email=False,
        )
        self.assertFalse(student_email["email_enabled"])

    def test_create_manual_todo_schedules_due_reminder(self):
        from datetime import timedelta
        from classroom_app.services.academic_service import china_now

        conn = FakeConnection()
        due = (china_now().replace(tzinfo=None) + timedelta(days=3)).isoformat(timespec="minutes")

        with patch.object(todo_service, "execute_insert_returning_id", return_value=501), patch.object(
            todo_service, "create_todo_notification", return_value=1,
        ), patch.object(todo_service, "schedule_task", return_value=9) as sched, patch.object(
            todo_service, "cancel_tasks_by_dedupe",
        ) as cancel:
            todo_service.create_manual_todo(
                conn,
                class_offering_id=20,
                user={"id": 10, "role": "student", "name": "S"},
                payload={"title": "复习", "due_at": due, "reminder_enabled": True, "reminder_lead_minutes": 60},
            )

        self.assertEqual(1, sched.call_count)
        self.assertEqual(0, cancel.call_count)
        kwargs = sched.call_args.kwargs
        self.assertEqual(todo_service.TODO_DUE_REMINDER_TASK_KIND, kwargs["task_kind"])
        self.assertEqual("todo-reminder:student:10:501", kwargs["dedupe_key"])

    def test_personal_todo_reminder_keeps_null_classroom_scope(self):
        from datetime import timedelta
        from classroom_app.services.academic_service import china_now

        conn = FakeConnection()
        due = (china_now().replace(tzinfo=None) + timedelta(days=3)).isoformat(timespec="minutes")

        with patch.object(todo_service, "execute_insert_returning_id", return_value=503), patch.object(
            todo_service, "create_todo_notification", return_value=1,
        ), patch.object(todo_service, "schedule_task", return_value=10) as sched:
            todo_service.create_manual_todo(
                conn,
                class_offering_id=None,
                user={"id": 3, "role": "teacher", "name": "Teacher"},
                payload={"title": "Private deadline", "due_at": due, "reminder_enabled": True},
            )

        self.assertIsNone(sched.call_args.kwargs["payload"]["class_offering_id"])

    def test_create_manual_todo_without_reminder_cancels(self):
        from datetime import timedelta
        from classroom_app.services.academic_service import china_now

        conn = FakeConnection()
        due = (china_now().replace(tzinfo=None) + timedelta(days=3)).isoformat(timespec="minutes")

        with patch.object(todo_service, "execute_insert_returning_id", return_value=502), patch.object(
            todo_service, "create_todo_notification", return_value=1,
        ), patch.object(todo_service, "schedule_task") as sched, patch.object(
            todo_service, "cancel_tasks_by_dedupe", return_value=0,
        ) as cancel:
            todo_service.create_manual_todo(
                conn,
                class_offering_id=20,
                user={"id": 10, "role": "student", "name": "S"},
                payload={"title": "复习", "due_at": due, "reminder_enabled": False},
            )

        self.assertEqual(0, sched.call_count)
        self.assertEqual(1, cancel.call_count)

    def test_handle_todo_due_reminder_creates_notification(self):
        import contextlib

        from classroom_app.services import message_center_service, scheduled_task_handlers

        fake = FakeConnection(
            row=FakeRow({
                "title": "读完第三章",
                "due_at": "2999-01-01T08:00",
                "completed_at": None,
                "deleted_at": None,
            })
        )

        @contextlib.contextmanager
        def fake_conn():
            yield fake

        with patch.object(scheduled_task_handlers, "get_db_connection", fake_conn), patch.object(
            message_center_service, "create_todo_notification", return_value=1,
        ) as note:
            result = scheduled_task_handlers.handle_todo_due_reminder(
                {"payload": {"todo_id": 1, "class_offering_id": 20, "owner_role": "student", "owner_user_pk": 10}}
            )

        note.assert_called_once()
        self.assertEqual("student", note.call_args.kwargs["recipient_role"])
        self.assertIn("读完第三章", note.call_args.kwargs["title"])
        self.assertIn("notified", result)

    def test_handle_personal_todo_due_reminder_matches_null_scope(self):
        import contextlib

        from classroom_app.services import message_center_service, scheduled_task_handlers

        fake = FakeConnection(
            row=FakeRow({
                "title": "Private deadline",
                "notes": "",
                "due_at": "2999-01-01T08:00",
                "completed_at": None,
                "deleted_at": None,
                "metadata_json": '{"reminder":{"enabled":true}}',
            })
        )

        @contextlib.contextmanager
        def fake_conn():
            yield fake

        with patch.object(scheduled_task_handlers, "get_db_connection", fake_conn), patch.object(
            message_center_service, "create_todo_notification", return_value=1,
        ) as note:
            result = scheduled_task_handlers.handle_todo_due_reminder(
                {"payload": {"todo_id": 2, "class_offering_id": None, "owner_role": "teacher", "owner_user_pk": 3}}
            )

        self.assertIn("class_offering_id IS NULL", fake.execute_calls[0][0])
        self.assertEqual((2, None, None, "teacher", 3), fake.execute_calls[0][1])
        self.assertIsNone(note.call_args.kwargs["class_offering_id"])
        self.assertIn("notified", result)

    def test_handle_todo_due_reminder_skips_completed(self):
        import contextlib

        from classroom_app.services import message_center_service, scheduled_task_handlers

        fake = FakeConnection(
            row=FakeRow({
                "title": "x",
                "due_at": "2999-01-01T08:00",
                "completed_at": "2026-06-21T10:00",
                "deleted_at": None,
            })
        )

        @contextlib.contextmanager
        def fake_conn():
            yield fake

        with patch.object(scheduled_task_handlers, "get_db_connection", fake_conn), patch.object(
            message_center_service, "create_todo_notification", return_value=1,
        ) as note:
            result = scheduled_task_handlers.handle_todo_due_reminder(
                {"payload": {"todo_id": 1, "class_offering_id": 20, "owner_role": "student", "owner_user_pk": 10}}
            )

        note.assert_not_called()
        self.assertIn("completed", result)

    def test_handle_teacher_todo_due_reminder_queues_email(self):
        import contextlib

        from classroom_app.services import message_center_service, scheduled_task_handlers

        fake = FakeConnection(
            row=FakeRow({
                "title": "提交课程总结",
                "notes": "附上数据表",
                "due_at": "2999-01-01T08:00",
                "completed_at": None,
                "deleted_at": None,
                "metadata_json": '{"reminder":{"enabled":true,"email_enabled":true,"lead_minutes":1440}}',
            })
        )

        @contextlib.contextmanager
        def fake_conn():
            yield fake

        with patch.object(scheduled_task_handlers, "get_db_connection", fake_conn), patch.object(
            message_center_service, "create_todo_notification", return_value=1,
        ), patch.object(
            scheduled_task_handlers,
            "queue_custom_teacher_email",
            return_value={"job_id": 88},
        ) as queue_email:
            result = scheduled_task_handlers.handle_todo_due_reminder(
                {"payload": {"todo_id": 9, "class_offering_id": 20, "owner_role": "teacher", "owner_user_pk": 3}}
            )

        queue_email.assert_called_once()
        self.assertEqual("teacher", queue_email.call_args.kwargs["recipient_role"])
        self.assertIn("email_job=88", result)

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
