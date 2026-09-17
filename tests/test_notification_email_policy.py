"""Notification delivery regressions; SQLite and mocked SMTP keep these isolated."""
import json
import sqlite3
import unittest
from unittest.mock import MagicMock, patch

from classroom_app.services import email_notification_service as emails
from classroom_app.services import message_center_service as notifications
from tests import test_score_projection_service as score_fixtures


def notification_payload(category, *, metadata=None, **overrides):
    return {
        "category": category,
        "severity": "important",
        "recipient_role": "student",
        "recipient_user_pk": 8,
        "recipient_identity": "student:8",
        "actor_role": "teacher",
        "actor_user_pk": 7,
        "ref_type": category,
        "ref_id": "31",
        "metadata_json": json.dumps(metadata or {}),
        **overrides,
    }


class NotificationEmailPolicyTests(unittest.TestCase):
    def assert_delivery(self, expected, payload):
        self.assertEqual(expected, emails.notification_email_required(
            payload["category"], payload["severity"], payload=payload,
        ))

    def test_routine_events_stay_in_app_even_if_important_or_opted_in(self):
        for category in ("submission", "learning_progress", "discussion_mention", "attendance_alert", "poll", "unknown"):
            for severity in ("normal", "important", "system"):
                with self.subTest(category=category, severity=severity):
                    self.assert_delivery(False, notification_payload(
                        category, severity=severity, email_notification_allowed=True,
                        metadata={"send_email_notification": True},
                    ))

    def test_only_real_grading_failures_email_the_teacher(self):
        for issue in ("grading_failed", "grading_stale", "grading_queue_failed", "grading_regrade_failed_preserved"):
            for role in ("teacher", "student"):
                with self.subTest(issue=issue, role=role):
                    self.assert_delivery(role == "teacher", notification_payload(
                        "ai_feedback", recipient_role=role,
                        metadata={"grading_issue_type": issue},
                    ))

    def test_low_confidence_and_unknown_grading_issues_stay_in_app(self):
        for issue in ("grading_review_required", "low_confidence", "failed", "", "grading_failed_extra"):
            with self.subTest(issue=issue):
                self.assert_delivery(False, notification_payload(
                    "ai_feedback", recipient_role="teacher", severity="system",
                    metadata={"grading_issue_type": issue},
                ))

    def test_legacy_grading_failure_reference_remains_supported(self):
        self.assert_delivery(True, notification_payload(
            "ai_feedback", recipient_role="teacher", ref_id="31:grading_failed:2026-09-18T10:00:00",
        ))
        self.assert_delivery(False, notification_payload(
            "ai_feedback", recipient_role="teacher", ref_id="31:grading_review_required:grading_failed",
        ))

    def test_explicit_review_metadata_overrides_old_failure_reference(self):
        self.assert_delivery(False, notification_payload(
            "ai_feedback", recipient_role="teacher", ref_id="31:grading_failed:2026-09-18",
            metadata={"grading_issue_type": "grading_review_required"},
        ))

    def test_visible_student_scores_including_zero_receive_email(self):
        for score in (0, 65.5, 100):
            with self.subTest(score=score):
                self.assert_delivery(True, notification_payload(
                    "grading_result", metadata={"score": score, "score_visible": True, "grade_display_state": "graded"},
                ))

    def test_pending_or_hidden_scores_never_receive_email(self):
        variants = [
            {}, {"score": None}, {"score": 80, "score_visible": False},
            {"score": 80, "score_visible": 0},
            *({"score": 80, "grade_display_state": state} for state in ("hidden", "pending", "group_pending", "returned")),
        ]
        for metadata in variants:
            with self.subTest(metadata=metadata):
                self.assert_delivery(False, notification_payload("grading_result", metadata=metadata))
        self.assert_delivery(False, notification_payload("grading_result", recipient_role="teacher", metadata={"score": 80}))

    def test_only_teacher_private_messages_to_students_receive_email(self):
        for actor in ("teacher", "student", "assistant"):
            for recipient in ("student", "teacher"):
                with self.subTest(actor=actor, recipient=recipient):
                    self.assert_delivery(actor == "teacher" and recipient == "student", notification_payload(
                        "private_message", actor_role=actor, recipient_role=recipient, severity="normal",
                    ))

    def test_assignment_requires_explicit_teacher_email_choice(self):
        for choice in (None, False, True):
            for ref_type in ("assignment", "assignment_due"):
                with self.subTest(choice=choice, ref_type=ref_type):
                    self.assert_delivery(choice is True and ref_type == "assignment", notification_payload(
                        "assignment", ref_type=ref_type, metadata={"send_email_notification": choice},
                    ))

    def test_feedback_only_emails_teacher_replies(self):
        for actor in ("teacher", "student", "assistant"):
            for event in ("reply", "created", "closed", "reopened"):
                with self.subTest(actor=actor, event=event):
                    self.assert_delivery(actor == "teacher" and event == "reply", notification_payload(
                        "app_feedback", ref_type="app_feedback_message", actor_role=actor,
                        metadata={"event_type": event},
                    ))
        self.assert_delivery(False, notification_payload("app_feedback", metadata={"event_type": "reply"}))

    def test_agent_tasks_email_only_failures(self):
        for status in ("failed", "completed", "running", "waiting_for_user", "cancelled", ""):
            with self.subTest(status=status):
                self.assert_delivery(status == "failed", notification_payload(
                    "agent_task", recipient_role="teacher", metadata={"status": status},
                ))

    def test_group_grade_publication_emails_students_but_group_activity_does_not(self):
        for role in ("student", "teacher"):
            self.assert_delivery(role == "student", notification_payload(
                "collaboration", recipient_role=role, ref_id="group-final:31:8:2026-09-18", severity="normal",
            ))
        self.assert_delivery(False, notification_payload("collaboration", ref_id="group-submission:31:8"))

    def test_important_workflows_and_account_recovery_remain_deliverable(self):
        for category in ("signature_workflow", "approval_workflow", "academic_exam", "gongwen_follow", "password_reset_request"):
            for severity in ("normal", "important", "system"):
                with self.subTest(category=category, severity=severity):
                    self.assert_delivery(severity != "normal", notification_payload(category, severity=severity))

    def test_explicit_email_veto_always_wins(self):
        for payload in (
            notification_payload("grading_result", metadata={"score": 80}),
            notification_payload("ai_feedback", recipient_role="teacher", metadata={"grading_issue_type": "grading_failed"}),
            notification_payload("private_message"),
            notification_payload("assignment", metadata={"send_email_notification": True}),
            notification_payload("signature_workflow"),
        ):
            with self.subTest(category=payload["category"]):
                self.assert_delivery(False, {**payload, "email_notification_allowed": False})

    def test_malformed_metadata_does_not_make_contextual_events_emailable(self):
        for raw in ("{broken", "[]", "null", "123"):
            for category in ("grading_result", "assignment", "ai_feedback", "agent_task"):
                with self.subTest(raw=raw, category=category):
                    self.assert_delivery(False, notification_payload(category, metadata_json=raw))

    def test_malformed_grading_metadata_is_denied_without_raising(self):
        for score in (True, False, "80", [], {}, float("nan"), float("inf")):
            with self.subTest(score=score):
                self.assert_delivery(False, notification_payload("grading_result", metadata={"score": score}))
        for value in ([], {}, "unknown"):
            with self.subTest(value=value):
                self.assert_delivery(False, notification_payload("grading_result", metadata={"score": 80, "grade_display_state": value}))
                self.assert_delivery(False, notification_payload("ai_feedback", recipient_role="teacher", metadata={"grading_issue_type": value}))

    def test_grading_review_producer_keeps_in_app_notice_with_explicit_issue_type(self):
        captured = []
        context = {"id": 31, "teacher_id": 7, "student_name": "Student", "submitted_at": "2026-09-18", "class_offering_id": 1}
        with patch.object(notifications, "_load_submission_notification_context", return_value=context), \
             patch.object(notifications, "_insert_notification_if_allowed", side_effect=lambda conn, payload: captured.append(payload) or True):
            created = notifications.create_teacher_grading_issue_notification(
                MagicMock(), 31, issue_detail="Evidence requires review", ref_suffix="grading_review_required",
            )
        self.assertEqual(1, created)
        self.assertEqual(1, len(captured))
        self.assertEqual("normal", captured[0]["severity"])
        self.assertEqual("grading_review_required", json.loads(captured[0]["metadata_json"])["grading_issue_type"])
        self.assert_delivery(False, captured[0])


class NotificationEmailQueueTests(unittest.TestCase):
    def test_routine_notice_does_not_lookup_email_or_create_outbox_job(self):
        conn = MagicMock()
        with patch.object(emails, "_load_recipient_email") as recipient, \
             patch.object(emails, "_insert_email_outbox_job_if_absent") as insert, \
             patch.object(emails, "_mark_notification_email_status") as mark:
            queued = emails.queue_notification_email_if_applicable(
                conn, notification_id=31, payload=notification_payload("submission", recipient_role="teacher"),
            )
        self.assertFalse(queued)
        recipient.assert_not_called()
        insert.assert_not_called()
        mark.assert_called_once_with(conn, 31, emails.EMAIL_STATUS_NOT_REQUIRED)

    def test_zero_score_is_queued_once_using_existing_dedupe_contract(self):
        conn = MagicMock()
        payload = notification_payload("grading_result", metadata={"score": 0, "score_visible": True})
        with patch.object(emails, "_load_recipient_email", return_value={"name": "Student", "email": "student@example.test"}), \
             patch.object(emails, "_resolve_sender_teacher_id", return_value=7), \
             patch.object(emails, "_load_default_email_config", return_value={"id": 2}), \
             patch.object(emails, "_build_email_content", return_value=("score", "<p>score</p>")), \
             patch.object(emails, "_insert_email_outbox_job_if_absent", return_value=91) as insert, \
             patch.object(emails, "_mark_notification_email_status") as mark:
            self.assertTrue(emails.queue_notification_email_if_applicable(conn, notification_id=31, payload=payload))
        self.assertEqual("notification:31:student@example.test", insert.call_args.kwargs["dedupe_key"])
        mark.assert_called_once_with(conn, 31, emails.EMAIL_STATUS_QUEUED, job_id=91)


class NotificationEmailWorkerTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE message_center_notifications (
                id INTEGER PRIMARY KEY, category TEXT, severity TEXT, recipient_role TEXT,
                actor_role TEXT, ref_type TEXT, ref_id TEXT, metadata_json TEXT,
                email_status TEXT, email_job_id INTEGER, email_sent_at TEXT
            );
            CREATE TABLE email_outbox (
                id INTEGER PRIMARY KEY, notification_id INTEGER, dedupe_key TEXT, config_id INTEGER,
                category TEXT, severity TEXT, status TEXT, next_attempt_at TEXT,
                locked_at TEXT, last_error TEXT, updated_at TEXT, sent_at TEXT
            );
            CREATE TABLE teacher_email_configs (
                id INTEGER PRIMARY KEY, enabled INTEGER, sent_success_count INTEGER DEFAULT 0,
                last_status TEXT, last_status_at TEXT, last_error TEXT, last_sent_at TEXT, updated_at TEXT
            );
            INSERT INTO teacher_email_configs (id, enabled) VALUES (2, 1);
        """)

    def tearDown(self):
        self.conn.close()

    def add_job(self, payload, *, with_notification=True, notification_id=31, dedupe_key="notification:31:student@example.test"):
        if with_notification:
            fields = ("category", "severity", "recipient_role", "actor_role", "ref_type", "ref_id", "metadata_json")
            self.conn.execute(
                "INSERT INTO message_center_notifications VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', 91, NULL)",
                (notification_id, *(payload[key] for key in fields)),
            )
        self.conn.execute(
            "INSERT INTO email_outbox VALUES (91, ?, ?, 2, ?, ?, 'sending', '2026-09-18', '2026-09-18', 'old error', NULL, NULL)",
            (notification_id, dedupe_key, payload["category"], payload["severity"]),
        )
        self.conn.commit()
        return dict(self.conn.execute("SELECT * FROM email_outbox WHERE id = 91").fetchone())

    def run_job(self, job):
        with patch.object(emails, "get_db_connection", return_value=self.conn), \
             patch.object(emails, "_rate_limit_delay_seconds", return_value=0), \
             patch.object(emails, "_send_outbox_message") as send:
            result = emails.process_email_job(job)
        return result, send

    def test_old_queued_submission_is_suppressed_and_keeps_in_app_notice(self):
        job = self.add_job(notification_payload("submission", recipient_role="teacher"))
        result, send = self.run_job(job)
        self.assertEqual("skipped", result)
        send.assert_not_called()
        outbox = dict(self.conn.execute("SELECT * FROM email_outbox WHERE id = 91").fetchone())
        self.assertEqual("skipped", outbox["status"])
        self.assertIsNone(outbox["locked_at"])
        self.assertIsNone(outbox["next_attempt_at"])
        notice = self.conn.execute("SELECT * FROM message_center_notifications WHERE id = 31").fetchone()
        self.assertEqual("not_required", notice["email_status"])
        self.assertEqual("submission", notice["category"])

    def test_old_low_confidence_job_is_suppressed_using_legacy_reference(self):
        job = self.add_job(notification_payload(
            "ai_feedback", recipient_role="teacher", ref_id="31:grading_review_required:2026-09-18", severity="system",
        ))
        result, send = self.run_job(job)
        self.assertEqual("skipped", result)
        send.assert_not_called()

    def test_current_hidden_grade_state_vetoes_previously_queued_score_email(self):
        job = self.add_job(notification_payload("grading_result", metadata={"score": 80}))
        self.conn.execute("UPDATE message_center_notifications SET metadata_json = ? WHERE id = 31", (
            json.dumps({"score": None, "score_visible": False, "grade_display_state": "group_pending"}),
        ))
        result, send = self.run_job(job)
        self.assertEqual("skipped", result)
        send.assert_not_called()

    def test_deleted_notification_job_never_sends_stale_content(self):
        job = self.add_job(notification_payload("grading_result", metadata={"score": 80}), with_notification=False, notification_id=None)
        result, send = self.run_job(job)
        self.assertEqual("skipped", result)
        send.assert_not_called()

    def test_real_grading_failure_in_existing_queue_remains_deliverable(self):
        job = self.add_job(notification_payload("ai_feedback", recipient_role="teacher", ref_id="31:grading_failed:2026-09-18"))
        result, send = self.run_job(job)
        self.assertEqual("sent", result)
        send.assert_called_once()
        notice = self.conn.execute("SELECT email_status FROM message_center_notifications WHERE id = 31").fetchone()
        self.assertEqual("sent", notice["email_status"])

    def test_explicit_custom_email_without_notification_remains_deliverable(self):
        job = self.add_job(notification_payload("assignment"), with_notification=False, notification_id=None, dedupe_key="custom:teacher:7:explicit")
        result, send = self.run_job(job)
        self.assertEqual("sent", result)
        send.assert_called_once()


class GradingNotificationEmailPrivacyTests(unittest.TestCase):
    def setUp(self):
        score_fixtures.ScoreProjectionTests.setUp(self)
        self.conn.execute("ALTER TABLE class_offerings ADD COLUMN semester TEXT")

    def tearDown(self):
        score_fixtures.ScoreProjectionTests.tearDown(self)

    def capture_grading_notice(self, submission_id):
        captured = []
        with patch.object(notifications, "_load_student_support_profile", return_value=""), \
             patch.object(notifications, "_insert_notification_if_allowed", side_effect=lambda conn, payload, **kwargs: captured.append(payload) or True), \
             patch("classroom_app.services.wechat_mp_subscribe_service.send_subscribe_message"):
            self.assertEqual(1, notifications.create_student_grading_notification(self.conn, submission_id, actor_role="teacher"))
        self.assertEqual(1, len(captured))
        return captured[0]

    def test_hidden_group_result_creates_in_app_notice_without_email_or_score_leak(self):
        sid = score_fixtures.ScoreProjectionTests.add(self, 1, 93)
        score_fixtures.ScoreProjectionTests.bind(self, 1, sid, revealed=0)
        payload = self.capture_grading_notice(sid)
        metadata = json.loads(payload["metadata_json"])
        self.assertIsNone(metadata["score"])
        self.assertFalse(metadata["score_visible"])
        self.assertEqual("group_pending", metadata["grade_display_state"])
        self.assertNotIn("93", payload["body_preview"])
        self.assertFalse(emails.notification_email_required(payload["category"], payload=payload))

    def test_visible_zero_score_from_projection_remains_emailable(self):
        sid = score_fixtures.ScoreProjectionTests.add(self, 1, 0)
        payload = self.capture_grading_notice(sid)
        self.assertEqual(0, json.loads(payload["metadata_json"])["score"])
        self.assertTrue(emails.notification_email_required(payload["category"], payload=payload))


if __name__ == "__main__":
    unittest.main()
