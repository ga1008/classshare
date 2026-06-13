import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from classroom_app.services import agent_key_service, agent_platform_actions, agent_task_service


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
        raise AssertionError("Agent write paths must not use raw cursor()")

    def execute(self, sql, params=()):
        self.execute_calls.append((sql, tuple(params)))
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT id, name, nickname"):
            return FakeCursor(
                FakeRow(
                    {
                        "id": 3,
                        "name": "Teacher",
                        "nickname": "",
                        "avatar_file_hash": "",
                        "avatar_mime_type": "",
                    }
                )
            )
        if normalized.startswith("SELECT id, author_identity, status, allow_comments"):
            return FakeCursor(
                FakeRow(
                    {
                        "id": 55,
                        "author_identity": "teacher:3",
                        "status": "published",
                        "allow_comments": 1,
                    }
                )
            )
        if normalized.startswith("SELECT * FROM agent_runtime_api_keys"):
            return FakeCursor(FakeRow({"id": 44, "key_label": "DeepSeek"}))
        return FakeCursor()


def run_async(coro):
    return asyncio.run(coro)


class AgentPostgresWriteTests(unittest.TestCase):
    def test_create_agent_task_uses_insert_returning_helper(self):
        conn = FakeConnection()

        with patch.object(
            agent_task_service,
            "build_teacher_page_context",
            return_value={"server_context": {}},
        ), patch.object(
            agent_task_service,
            "_recent_agent_task_titles",
            return_value=[],
        ), patch.object(
            agent_task_service,
            "execute_insert_returning_id",
            return_value=77,
        ) as insert_helper, patch.object(
            agent_task_service,
            "get_agent_task",
            return_value={"id": 77, "status": "queued"},
        ) as get_task:
            result = agent_task_service.create_agent_task(
                conn,
                {"id": 3, "name": "Teacher"},
                {"task_type": "general_teaching_task", "instruction": "Prepare a classroom plan"},
            )

        self.assertEqual({"id": 77, "status": "queued"}, result)
        self.assertEqual(1, insert_helper.call_count)
        self.assertIn("INSERT INTO agent_tasks", insert_helper.call_args.args[1])
        get_task.assert_called_once_with(conn, 77, teacher_id=3)
        self.assertTrue(any("INSERT INTO agent_task_events" in sql for sql, _ in conn.execute_calls))

    def test_create_agent_api_key_uses_insert_returning_helper(self):
        conn = FakeConnection()

        with patch.object(
            agent_key_service,
            "execute_insert_returning_id",
            return_value=44,
        ) as insert_helper, patch.object(
            agent_key_service,
            "serialize_agent_api_key",
            return_value={"id": 44},
        ), patch.object(
            agent_key_service,
            "list_agent_api_keys",
            return_value=[{"id": 44}],
        ), patch.object(
            agent_key_service,
            "sync_active_agent_runtime_config",
            return_value={"status": "missing_active_key"},
        ):
            result = run_async(
                agent_key_service.create_agent_api_key(
                    conn,
                    {
                        "api_key": "sk-test-secret",
                        "test_on_save": False,
                        "make_active": False,
                    },
                    teacher_id=3,
                )
            )

        self.assertTrue(result["saved"])
        self.assertEqual({"id": 44}, result["key"])
        self.assertEqual(1, insert_helper.call_count)
        self.assertIn("INSERT INTO agent_runtime_api_keys", insert_helper.call_args.args[1])

    def test_teacher_blog_draft_uses_insert_returning_helper(self):
        conn = FakeConnection()

        with patch.object(
            agent_platform_actions,
            "execute_insert_returning_id",
            return_value=55,
        ) as insert_helper:
            result = agent_platform_actions._create_teacher_blog_draft(
                conn,
                teacher_id=3,
                title="Class reflection",
                content_md="Draft content",
                tags=["agent"],
            )

        self.assertEqual(55, result["id"])
        self.assertEqual("draft", result["status"])
        self.assertEqual(1, insert_helper.call_count)
        self.assertIn("INSERT INTO blog_posts", insert_helper.call_args.args[1])

    def test_teacher_blog_post_publish_uses_insert_returning_helper(self):
        conn = FakeConnection()

        with patch.object(
            agent_platform_actions,
            "execute_insert_returning_id",
            return_value=56,
        ) as insert_helper:
            result = agent_platform_actions._create_teacher_blog_post(
                conn,
                teacher_id=3,
                title="Published",
                content_md="Fresh content",
                tags=["agent"],
                status="published",
            )

        self.assertEqual(56, result["id"])
        self.assertEqual("published", result["status"])
        self.assertEqual(1, insert_helper.call_count)
        self.assertIn("INSERT INTO blog_posts", insert_helper.call_args.args[1])

    def test_teacher_blog_comment_uses_insert_returning_helper(self):
        conn = FakeConnection()

        with patch.object(
            agent_platform_actions,
            "execute_insert_returning_id",
            return_value=57,
        ) as insert_helper:
            result = agent_platform_actions._create_teacher_blog_comment(
                conn,
                teacher_id=3,
                post_id=55,
                content_md="Nice reflection",
            )

        self.assertEqual(57, result["id"])
        self.assertEqual(55, result["post_id"])
        self.assertEqual(1, insert_helper.call_count)
        self.assertIn("INSERT INTO blog_comments", insert_helper.call_args.args[1])
        self.assertTrue(any("UPDATE blog_posts SET comment_count" in sql for sql, _ in conn.execute_calls))

    def test_assignment_draft_uses_insert_returning_helper(self):
        conn = FakeConnection()

        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            agent_platform_actions,
            "HOMEWORK_SUBMISSIONS_DIR",
            Path(tmpdir),
        ), patch.object(
            agent_platform_actions,
            "execute_insert_returning_id",
            return_value=66,
        ) as insert_helper:
            result = agent_platform_actions._create_assignment_draft(
                conn,
                course_id=10,
                class_offering_id=20,
                title="Draft assignment",
                requirements_md="Requirements",
                rubric_md="Rubric",
            )

        self.assertEqual(66, result["id"])
        self.assertEqual("new", result["status"])
        self.assertEqual("/assignment/66", result["url"])
        self.assertEqual(1, insert_helper.call_count)
        self.assertIn("INSERT INTO assignments", insert_helper.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
