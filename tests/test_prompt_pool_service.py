import sqlite3
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from classroom_app.app import app
from classroom_app.dependencies import get_current_user
from classroom_app.routers import prompt_pool as router_mod
from classroom_app.services import prompt_pool_service as svc


class PromptPoolServiceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

    def tearDown(self):
        self.conn.close()

    def test_record_deduplicates_prompt_and_increments_use_count(self):
        first = svc.record_prompt(self.conn, "teacher_evaluation.rewrite_analysis", "make it more detailed")
        second = svc.record_prompt(self.conn, "teacher_evaluation.rewrite_analysis", "make it more detailed")

        self.assertEqual(first["use_count"], 1)
        self.assertEqual(second["use_count"], 2)
        rows = self.conn.execute("SELECT COUNT(*) AS count FROM ai_prompt_pool").fetchone()
        self.assertEqual(rows["count"], 1)

    def test_feature_key_isolation_and_search_order(self):
        svc.record_prompt(self.conn, "materials.ai_generate", "review outline")
        svc.record_prompt(self.conn, "materials.ai_generate", "review outline")
        svc.record_prompt(self.conn, "materials.ai_generate", "classroom drill")
        svc.record_prompt(self.conn, "exam.generate_scope", "review outline")

        prompts = svc.search_prompts(self.conn, "materials.ai_generate", "outline", limit=20)

        self.assertEqual([item["prompt"] for item in prompts], ["review outline"])
        self.assertEqual([item["use_count"] for item in prompts], [2])

    def test_record_prompt_if_shared_respects_opt_out_and_empty_prompt(self):
        self.assertIsNone(svc.record_prompt_if_shared(self.conn, "materials.ai_generate", "private prompt", False))
        self.assertIsNone(svc.record_prompt_if_shared(self.conn, "materials.ai_generate", "private prompt", "false"))
        self.assertIsNone(svc.record_prompt_if_shared(self.conn, "materials.ai_generate", "   ", True))

        rows = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'ai_prompt_pool'"
        ).fetchall()
        self.assertEqual(len(rows), 0)

    def test_invalid_feature_key_is_rejected(self):
        with self.assertRaises(ValueError):
            svc.record_prompt(self.conn, "../bad", "prompt")


class PromptPoolApiTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.client = TestClient(app)
        self.previous_override = app.dependency_overrides.get(get_current_user)
        app.dependency_overrides[get_current_user] = lambda: {"id": 1, "role": "teacher"}

    def tearDown(self):
        if self.previous_override is None:
            app.dependency_overrides.pop(get_current_user, None)
        else:
            app.dependency_overrides[get_current_user] = self.previous_override
        self.conn.close()

    def test_record_and_search_prompt_pool_api(self):
        with patch.object(router_mod, "get_db_connection", return_value=self.conn):
            first = self.client.post(
                "/api/prompt-pool/record",
                json={"feature_key": "exam.generate_scope", "prompt": "network protocol focus"},
            )
            second = self.client.post(
                "/api/prompt-pool/record",
                json={"feature_key": "exam.generate_scope", "prompt": "network protocol focus"},
            )
            searched = self.client.get(
                "/api/prompt-pool",
                params={"feature_key": "exam.generate_scope", "q": "protocol", "limit": 20},
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.json()["prompt"]["use_count"], 2)
        self.assertEqual(searched.status_code, 200)
        self.assertEqual(searched.json()["prompts"][0]["prompt"], "network protocol focus")


if __name__ == "__main__":
    unittest.main()
