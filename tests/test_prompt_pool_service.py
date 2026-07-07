import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from classroom_app.app import app
from classroom_app.db.schema_prompt_pool import ensure_prompt_pool_schema
from classroom_app.dependencies import get_current_user
from classroom_app.routers import prompt_pool as router_mod
from classroom_app.services import prompt_pool_service as svc


class PromptPoolServiceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

    def tearDown(self):
        self.conn.close()

    def _ensure_schema(self):
        ensure_prompt_pool_schema(self.conn)
        self.conn.commit()

    def test_record_deduplicates_prompt_and_increments_use_count(self):
        self._ensure_schema()

        first = svc.record_prompt(self.conn, "teacher_evaluation.rewrite_analysis", "make it more detailed")
        second = svc.record_prompt(self.conn, "teacher_evaluation.rewrite_analysis", "make it more detailed")

        self.assertEqual(first["use_count"], 1)
        self.assertEqual(second["use_count"], 2)
        rows = self.conn.execute("SELECT COUNT(*) AS count FROM ai_prompt_pool").fetchone()
        self.assertEqual(rows["count"], 1)

    def test_feature_key_isolation_and_search_order(self):
        self._ensure_schema()

        svc.record_prompt(self.conn, "materials.ai_generate", "review outline")
        svc.record_prompt(self.conn, "materials.ai_generate", "review outline")
        svc.record_prompt(self.conn, "materials.ai_generate", "classroom drill")
        svc.record_prompt(self.conn, "exam.generate_scope", "review outline")

        prompts = svc.search_prompts(self.conn, "materials.ai_generate", "outline", limit=20)

        self.assertEqual([item["prompt"] for item in prompts], ["review outline"])
        self.assertEqual([item["use_count"] for item in prompts], [2])

    def test_multi_term_fuzzy_search_requires_all_terms_and_keeps_hot_order(self):
        self._ensure_schema()

        svc.record_prompt(self.conn, "materials.ai_generate", "homework exam evidence")
        svc.record_prompt(self.conn, "materials.ai_generate", "homework exam evidence")
        svc.record_prompt(self.conn, "materials.ai_generate", "exam homework evidence with examples")
        svc.record_prompt(self.conn, "materials.ai_generate", "homework classroom participation")

        prompts = svc.search_prompts(self.conn, "materials.ai_generate", "homework exam", limit=20)

        self.assertEqual(
            [item["prompt"] for item in prompts],
            ["homework exam evidence", "exam homework evidence with examples"],
        )
        self.assertEqual([item["use_count"] for item in prompts], [2, 1])

    def test_search_treats_like_wildcards_as_plain_text(self):
        self._ensure_schema()

        svc.record_prompt(self.conn, "materials.ai_generate", "100% homework review")
        svc.record_prompt(self.conn, "materials.ai_generate", "100 point homework review")

        prompts = svc.search_prompts(self.conn, "materials.ai_generate", "100%", limit=20)

        self.assertEqual([item["prompt"] for item in prompts], ["100% homework review"])

    def test_record_prompt_if_shared_respects_opt_out_and_empty_prompt(self):
        self.assertIsNone(svc.record_prompt_if_shared(self.conn, "materials.ai_generate", "private prompt", False))
        self.assertIsNone(svc.record_prompt_if_shared(self.conn, "materials.ai_generate", "private prompt", 0))
        self.assertIsNone(svc.record_prompt_if_shared(self.conn, "materials.ai_generate", "private prompt", "false"))
        self.assertIsNone(svc.record_prompt_if_shared(self.conn, "materials.ai_generate", "private prompt", "否"))
        self.assertIsNone(svc.record_prompt_if_shared(self.conn, "materials.ai_generate", "   ", True))

        rows = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'ai_prompt_pool'"
        ).fetchall()
        self.assertEqual(len(rows), 0)

    def test_record_prompt_creates_missing_schema_without_breaking_submit_flow(self):
        result = svc.record_prompt(self.conn, "teacher_evaluation.rewrite_analysis", "写得更具体")

        self.assertEqual(result["prompt"], "写得更具体")
        row = self.conn.execute(
            "SELECT use_count FROM ai_prompt_pool WHERE feature_key = ?",
            ("teacher_evaluation.rewrite_analysis",),
        ).fetchone()
        self.assertEqual(row["use_count"], 1)

    def test_search_missing_schema_returns_empty_without_running_schema_write(self):
        prompts = svc.search_prompts(self.conn, "materials.ai_generate", "anything")

        self.assertEqual(prompts, [])
        rows = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'ai_prompt_pool'"
        ).fetchall()
        self.assertEqual(len(rows), 0)

    def test_sensitive_prompt_text_is_not_recorded(self):
        self._ensure_schema()

        result = svc.record_prompt(
            self.conn,
            "materials.ai_generate",
            "请按这个 token 调试：Bearer abcdefghijklmnopqrstuvwxyz123456",
        )
        second = svc.record_prompt(
            self.conn,
            "materials.ai_generate",
            "api_key=sk-abcdefghijklmnopqrstuvwxyz123456",
        )

        self.assertIsNone(result)
        self.assertIsNone(second)
        rows = self.conn.execute("SELECT COUNT(*) AS count FROM ai_prompt_pool").fetchone()
        self.assertEqual(rows["count"], 0)

    def test_invalid_feature_key_is_rejected(self):
        with self.assertRaises(ValueError):
            svc.record_prompt(self.conn, "../bad", "prompt")


class PromptPoolApiTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        ensure_prompt_pool_schema(self.conn)
        self.conn.commit()
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
            opted_out = self.client.post(
                "/api/prompt-pool/record",
                json={"feature_key": "exam.generate_scope", "prompt": "do not share", "share": False},
            )
            after_opt_out = self.client.get(
                "/api/prompt-pool",
                params={"feature_key": "exam.generate_scope", "q": "do not share", "limit": 20},
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.json()["prompt"]["use_count"], 2)
        self.assertEqual(searched.status_code, 200)
        self.assertEqual(searched.json()["prompts"][0]["prompt"], "network protocol focus")
        self.assertEqual(opted_out.status_code, 200)
        self.assertIsNone(opted_out.json()["prompt"])
        self.assertEqual(after_opt_out.json()["prompts"], [])


class PromptPoolFrontendContractTests(unittest.TestCase):
    def test_prompt_pool_frontend_keeps_share_and_suggestion_contract(self):
        source = Path("static/js/prompt_pool.js").read_text(encoding="utf-8")

        self.assertIn('data-prompt-pool-share checked', source)
        self.assertIn("aria-activedescendant", source)
        self.assertIn("prompt-pool-item__text", source)
        self.assertIn("highlightPrompt", source)
        self.assertIn("share: controller.getShareEnabled()", source)

    def test_prompt_pool_styles_keep_panel_below_input_and_mobile_safe(self):
        source = Path("static/css/ui-system.src.css").read_text(encoding="utf-8")

        self.assertIn(".prompt-pool-panel {", source)
        self.assertIn("margin-top: 10px", source)
        self.assertIn(".prompt-pool-item__meta", source)
        self.assertIn("@media (max-width: 560px)", source)


if __name__ == "__main__":
    unittest.main()
