import unittest
from unittest.mock import patch

from classroom_app.services import collaboration_service as service


class FakeCursor:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class FakeConnection:
    def __init__(self):
        self.execute_calls = []

    def cursor(self):
        raise AssertionError("collaboration write paths must not use raw cursor()")

    def execute(self, sql, params=()):
        self.execute_calls.append((" ".join(str(sql).split()), tuple(params)))
        return FakeCursor(None)


def _group():
    return {"id": 10, "name": "Group", "class_offering_id": 20, "assignment_id": None, "teacher_id": 3}


class CollaborationPostgresWriteTests(unittest.TestCase):
    def test_create_group_uses_insert_returning_helper(self):
        conn = FakeConnection()

        with patch.object(service, "ensure_classroom_access", return_value={"teacher_id": 3}), patch.object(
            service,
            "_student_conflict_group",
            return_value=None,
        ), patch.object(
            service,
            "execute_insert_returning_id",
            return_value=101,
        ) as insert_helper, patch.object(
            service,
            "_upsert_member",
            return_value=None,
        ) as upsert_member, patch.object(
            service,
            "_load_group",
            return_value={"id": 101, "teacher_id": 3},
        ), patch.object(
            service,
            "_notify_teacher",
            return_value=None,
        ), patch.object(
            service,
            "_notify_group_members",
            return_value=None,
        ):
            result = service.create_group(
                conn,
                20,
                {"id": 5, "role": "student", "name": "Student"},
                {"name": "Group"},
            )

        self.assertEqual({"id": 101, "teacher_id": 3}, result)
        self.assertEqual(1, insert_helper.call_count)
        self.assertIn("INSERT INTO study_groups", insert_helper.call_args.args[1])
        upsert_member.assert_called_once()
        self.assertEqual(101, upsert_member.call_args.kwargs["group_id"])

    def test_add_group_file_uses_insert_returning_helper(self):
        conn = FakeConnection()

        with patch.object(service, "_ensure_group_access", return_value=_group()), patch.object(
            service,
            "_can_access_group_work",
            return_value=True,
        ), patch.object(
            service,
            "execute_insert_returning_id",
            return_value=202,
        ) as insert_helper, patch.object(
            service,
            "_load_group_file",
            return_value={"id": 202},
        ) as load_file, patch.object(
            service,
            "_notify_group_members",
            return_value=None,
        ), patch.object(
            service,
            "_notify_teacher",
            return_value=None,
        ), patch.object(
            service,
            "_serialize_file",
            return_value={"id": 202},
        ):
            result = service.add_group_file(
                conn,
                10,
                {"id": 5, "role": "student", "name": "Student"},
                file_hash="hash",
                original_filename="file.txt",
                mime_type="text/plain",
                file_size=10,
            )

        self.assertEqual({"id": 202}, result)
        self.assertEqual(1, insert_helper.call_count)
        self.assertIn("INSERT INTO study_group_files", insert_helper.call_args.args[1])
        load_file.assert_called_once_with(conn, 202)

    def test_upsert_group_submission_uses_insert_returning_helper(self):
        conn = FakeConnection()

        with patch.object(service, "_ensure_group_access", return_value=_group()), patch.object(
            service,
            "_can_manage_group",
            return_value=True,
        ), patch.object(
            service,
            "execute_insert_returning_id",
            return_value=303,
        ) as insert_helper, patch.object(
            service,
            "_load_group",
            return_value=_group(),
        ), patch.object(
            service,
            "_notify_teacher",
            return_value=None,
        ), patch.object(
            service,
            "_notify_group_members",
            return_value=None,
        ), patch.object(
            service,
            "_load_group_submission",
            return_value={"id": 303},
        ) as load_submission, patch.object(
            service,
            "_serialize_submission",
            return_value={"id": 303},
        ):
            result = service.upsert_group_submission(
                conn,
                10,
                {"id": 5, "role": "student", "name": "Student"},
                {"title": "Submission", "summary_md": "Summary"},
            )

        self.assertEqual({"id": 303}, result)
        self.assertEqual(1, insert_helper.call_count)
        self.assertIn("INSERT INTO group_submissions", insert_helper.call_args.args[1])
        load_submission.assert_called_once_with(conn, 303)

    def test_submit_peer_review_uses_insert_returning_helper(self):
        conn = FakeConnection()

        with patch.object(service, "_ensure_group_access", return_value=_group()), patch.object(
            service,
            "_is_active_member",
            return_value=True,
        ), patch.object(
            service,
            "execute_insert_returning_id",
            return_value=404,
        ) as insert_helper, patch.object(
            service,
            "_notify_teacher",
            return_value=None,
        ), patch.object(
            service,
            "_notify_group_members",
            return_value=None,
        ), patch.object(
            service,
            "_load_review",
            return_value={"id": 404},
        ) as load_review, patch.object(
            service,
            "_serialize_review",
            return_value={"id": 404},
        ):
            result = service.submit_peer_review(
                conn,
                10,
                {"id": 5, "role": "student", "name": "Student"},
                {
                    "reviewee_student_id": 6,
                    "responsibility_score": 5,
                    "collaboration_score": 5,
                    "quality_score": 5,
                    "comment": "Good",
                },
            )

        self.assertEqual({"id": 404}, result)
        self.assertEqual(1, insert_helper.call_count)
        self.assertIn("INSERT INTO peer_reviews", insert_helper.call_args.args[1])
        load_review.assert_called_once_with(conn, 404)


if __name__ == "__main__":
    unittest.main()
