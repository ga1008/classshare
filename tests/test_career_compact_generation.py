"""Compact generation preserves graph integrity and retries by persisted cause."""
import asyncio
import copy
import json
import os
import unittest
from contextlib import contextmanager
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault("DB_ENGINE", "sqlite")

from classroom_app.services import ai_durable_job_service as durable
from classroom_app.services import career_lifecycle_service as lifecycle
from classroom_app.services import career_path_service as career
from classroom_app.services import career_payload_service as payloads
from classroom_app.services.career_public_view_service import STAGES
from classroom_app.services.career_recommendation_service import baseline_network
from tests.test_career_lifecycle import fixture


def compact_candidate():
    graph = baseline_network("英语")
    return {
        "cats": [{key: cat[key] for key in ("id", "name")} for cat in graph["cats"]],
        "nodes": [{key: node[key] for key in ("tag", "cat", "name", "riasec", "lang", "pre", "know")}
                  for node in graph["nodes"]],
        "links": graph["links"],
    }


class CompactPayloadTests(unittest.TestCase):
    def test_server_fields_expand_without_mutation_and_stable_ids_survive(self):
        raw = compact_candidate()
        raw["nodes"][0].update(direction_id="direction-existing", rec=5,
                               tl="invalid model-owned stages", trend="年薪百万", salary=1000000)
        before = copy.deepcopy(raw)
        history = [{"name":raw["nodes"][0]["name"], "direction_id":"direction-reviewed"}]
        graph = payloads.expand_network_candidate(raw, "英语", previous_directions=history)
        self.assertEqual(raw, before)
        self.assertEqual(graph["nodes"][0]["direction_id"], "direction-reviewed")
        self.assertEqual(graph["links"], raw["links"])
        for node in graph["nodes"]:
            self.assertEqual(node["tl"], [list(stage) for stage in STAGES])
            self.assertEqual(node["rec"], 3)
            self.assertNotIn("salary", node)
        graph["nodes"][0]["tl"][0][0] = "changed locally"
        self.assertNotEqual(graph["nodes"][1]["tl"][0][0], "changed locally")
        self.assertNotEqual(STAGES[0][0], "changed locally")

    def test_oversized_collections_fail_before_traversal_or_expansion(self):
        class UntraversableList(list):
            def __iter__(self):
                raise AssertionError("oversized collection traversed")
        for key, size in (("cats", 13), ("nodes", 61), ("links", 241)):
            raw = compact_candidate()
            raw[key] = UntraversableList([{}] * size)
            with self.subTest(key=key), self.assertRaises(ValueError), patch.object(
                    payloads, "validate_network_payload", side_effect=AssertionError("expanded before bound check")):
                payloads.expand_network_candidate(raw, "英语")

    def test_duplicate_names_rejected_even_with_distinct_explicit_ids(self):
        for first, second in (("英语翻译", "英语翻译"), ("英语翻译", " 英语 翻译 "),
                              ("ＵＩ Designer", "ui designer")):
            raw = compact_candidate()
            raw["nodes"][0].update(name=first, direction_id="direction-first")
            raw["nodes"][-1].update(name=second, direction_id="direction-other")
            with self.subTest(names=(first, second)), self.assertRaisesRegex(ValueError, "名称重复"):
                payloads.expand_network_candidate(raw, "英语")
        # The shared full publication validator also guards explicit-ID graphs.
        graph = payloads.expand_network_candidate(compact_candidate(), "英语")
        graph["nodes"][-1]["name"] = graph["nodes"][0]["name"]
        with self.assertRaisesRegex(ValueError, "名称重复"):
            payloads.validate_network_payload(graph, "英语")

    def test_bad_identity_category_interest_and_language_are_not_repaired(self):
        mutations = [("tag", "bad/tag"), ("cat", "missing"), ("name", " "),
                      ("riasec", ["INVALID"]), ("lang", "false")]
        for key, value in mutations:
            raw = compact_candidate(); raw["nodes"][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                payloads.expand_network_candidate(raw, "英语")
        for value in ("", None, 0, [], "bad/id"):
            stored = payloads.expand_network_candidate(compact_candidate(), "英语")
            stored["nodes"][0]["direction_id"] = value
            with self.subTest(stored_id=value), self.assertRaises(ValueError):
                payloads.validate_network_payload(stored, "英语")
        stored = payloads.expand_network_candidate(compact_candidate(), "英语")
        stored["nodes"][0]["direction_id"] = stored["nodes"][1]["direction_id"] = "same-id"
        with self.assertRaisesRegex(ValueError, "稳定标识重复"):
            payloads.validate_network_payload(stored, "英语")

    def test_model_repeated_opaque_ids_cannot_assign_identity_or_break_valid_topology(self):
        raw = compact_candidate()
        expected = payloads.expand_network_candidate(raw, "英语")
        for node in raw["nodes"]:
            node["direction_id"] = "copied-model-id"
        actual = payloads.expand_network_candidate(raw, "英语")
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual["nodes"]), len({node["direction_id"] for node in actual["nodes"]}))

    def test_normalized_same_name_retains_history_but_renamed_model_id_does_not(self):
        raw = compact_candidate()
        raw["nodes"][0].update(name="ＵＩ Designer", direction_id="untrusted-model-id")
        old = [{"name":"ui designer", "direction_id":"direction-reviewed"}]
        retained = payloads.expand_network_candidate(raw, "英语", previous_directions=old)
        self.assertEqual(retained["nodes"][0]["direction_id"], "direction-reviewed")
        raw["nodes"][0].update(name="技术写作", direction_id="direction-reviewed")
        renamed = payloads.expand_network_candidate(raw, "英语", previous_directions=old)
        self.assertNotEqual(renamed["nodes"][0]["direction_id"], "direction-reviewed")
        raw["nodes"][0]["name"] = " 技术 写作 "
        spaced = payloads.expand_network_candidate(raw, "英语")
        self.assertEqual(renamed["nodes"][0]["direction_id"], spaced["nodes"][0]["direction_id"])

    def test_ambiguous_history_does_not_reassign_saved_feedback(self):
        raw = compact_candidate()
        name = raw["nodes"][0]["name"]
        for old in (
                [{"name":name,"direction_id":"old-first"},{"name":name,"direction_id":"old-second"}],
                [{"name":name,"direction_id":"old-first"},{"name":"其他方向","direction_id":"old-first"}]):
            graph = payloads.expand_network_candidate(raw, "英语", previous_directions=old)
            self.assertNotIn(graph["nodes"][0]["direction_id"], {"old-first","old-second"})

    def test_bad_links_are_not_dropped_or_repaired(self):
        tag = compact_candidate()["nodes"][0]["tag"]
        for links in (None, {}, [[tag, 0, "missing", 1]], [[tag, True, tag, 0]],
                      [[tag, 4, tag, 0]], [[tag, "1", tag, 0]], [[tag, 0, tag]]):
            raw = compact_candidate(); raw["links"] = links
            with self.subTest(links=links), self.assertRaises(ValueError):
                payloads.expand_network_candidate(raw, "英语")


class CompactRetryTests(unittest.TestCase):
    def setUp(self):
        self.conn = fixture()
        career.initialize_career(self.conn, 1)
        self.job = dict(self.conn.execute("SELECT * FROM ai_jobs").fetchone())
        self.conn.commit()
        @contextmanager
        def connect():
            yield self.conn
        self.connect = connect
        self.connection_patch = patch.object(career, "get_db_connection", connect)
        self.connection_patch.start()

    def tearDown(self):
        self.connection_patch.stop()
        self.conn.close()

    def previous_attempt(self, code, *, number=1, stage="execute", status="error"):
        self.conn.execute("""INSERT INTO ai_job_attempts(job_id,attempt_no,stage,status,error_code,started_at)
                             VALUES(?,?,?,?,?,?)""",
                          (self.job["id"], number, stage, status, code, "2026-01-01T00:00:00"))
        self.conn.commit()

    def test_persisted_schema_error_survives_claim_clearing_job_error(self):
        self.previous_attempt("ValueError")
        self.conn.execute("UPDATE ai_jobs SET attempt_count=1,status='retry_wait',last_error_code='ValueError'")
        self.conn.commit()
        with patch.object(durable, "get_db_connection", self.connect), patch.object(durable, "get_configured_db_engine", return_value="sqlite"):
            claimed = durable.claim_due_ai_jobs(limit=1, task_types=(career.NETWORK_GENERATE_TASK_KIND,))[0]
        self.assertEqual(claimed["attempt_count"], 2)
        self.assertEqual(claimed["last_error_code"], "")
        self.assertEqual(lifecycle._network_retry_capability(claimed), "thinking")

    def test_only_immediately_previous_schema_failure_upgrades(self):
        for code in ("ValueError", "TypeError", "HTTPStatusError", "TimeoutError", "ReadTimeout", "ConnectError", "RuntimeError"):
            self.conn.execute("DELETE FROM ai_job_attempts")
            self.previous_attempt(code)
            with self.subTest(code=code):
                self.assertEqual(lifecycle._network_retry_capability({**self.job, "attempt_count": 2}),
                                 "thinking" if code in {"ValueError", "TypeError"} else "standard")
        self.conn.execute("DELETE FROM ai_job_attempts")
        self.previous_attempt("ValueError", number=1)
        self.previous_attempt("TimeoutError", number=2)
        self.assertEqual(lifecycle._network_retry_capability({**self.job, "attempt_count": 3}), "standard")
        self.assertEqual(lifecycle._network_retry_capability({**self.job, "attempt_count": 4}), "standard")
        self.previous_attempt("ValueError", number=3, stage="apply")
        self.assertEqual(lifecycle._network_retry_capability({**self.job, "attempt_count": 4}), "standard")
        self.previous_attempt("ValueError", number=3, status="running")
        self.assertEqual(lifecycle._network_retry_capability({**self.job, "attempt_count": 4}), "standard")
        with patch.object(career, "get_db_connection", side_effect=AssertionError("first attempt should not read history")):
            self.assertEqual(lifecycle._network_retry_capability(self.job), "standard")

    def test_execution_uses_one_model_call_and_cause_specific_timeout(self):
        for previous, expected, timeout in ((None, "standard", 120), ("ValueError", "thinking", 180),
                                            ("HTTPStatusError", "standard", 120), ("TimeoutError", "standard", 120)):
            self.conn.execute("DELETE FROM ai_job_attempts")
            if previous:
                self.previous_attempt(previous)
            model = AsyncMock(return_value=compact_candidate())
            research = AsyncMock(return_value={"digest": "探索参考", "queries": [], "used": True})
            job = {**self.job, "attempt_count": 2 if previous else 1}
            with self.subTest(previous=previous), patch.object(career, "_call_career_ai", model), \
                    patch.object(career.ai_web_research, "gather", research):
                result = asyncio.run(lifecycle.execute_network(job, json.loads(self.job["payload_json"])))
                model.assert_awaited_once()
                self.assertEqual(model.call_args.kwargs["capability"], expected)
                self.assertEqual(model.call_args.kwargs["timeout"], timeout)
                self.assertEqual(result["sources"]["generation_contract"], payloads.GENERATION_CONTRACT)
                self.assertFalse(result["sources"]["verified"])
                self.assertEqual(result["sources"]["model_capability"], expected)

    def test_history_read_failure_keeps_fast_route_and_logs_no_error_body(self):
        with patch.object(career, "get_db_connection", side_effect=RuntimeError("private connection details")), \
                self.assertLogs(lifecycle.__name__, level="WARNING") as logs:
            self.assertEqual(lifecycle._network_retry_capability({**self.job, "attempt_count": 2}), "standard")
        self.assertEqual(len(logs.output), 1)
        self.assertIn("RuntimeError", logs.output[0])
        self.assertNotIn("private connection details", logs.output[0])


class CareerAIEnvelopeTests(unittest.TestCase):
    def test_schema_failures_are_classified_without_masking_transport_failure(self):
        for data in ([], {"status": "success", "response_text": "not json"},
                     {"status": "success", "response_json": []}):
            response = Mock(); response.json.return_value = data
            with self.subTest(data=data), patch.object(career.ai_client, "post", AsyncMock(return_value=response)), self.assertRaises(ValueError):
                asyncio.run(career._call_career_ai("s", "u", label="probe", capability="standard"))
        response = Mock(); response.json.side_effect = json.JSONDecodeError("bad", "", 0)
        with patch.object(career.ai_client, "post", AsyncMock(return_value=response)), self.assertRaises(ValueError) as caught:
            asyncio.run(career._call_career_ai("s", "u", label="probe"))
        self.assertIs(type(caught.exception), ValueError)
        failure = TimeoutError("upstream timeout")
        with patch.object(career.ai_client, "post", AsyncMock(side_effect=failure)), self.assertRaises(TimeoutError) as caught:
            asyncio.run(career._call_career_ai("s", "u", label="probe"))
        self.assertIs(caught.exception, failure)

    def test_fast_capability_routes_to_fast_task_type(self):
        response = Mock(); response.json.return_value = {"status": "success", "response_json": compact_candidate()}
        caller = AsyncMock(return_value=response)
        with patch.object(career.ai_client, "post", caller):
            result = asyncio.run(career._call_career_ai("s", "u", label="probe", capability="standard", timeout=120))
        self.assertTrue(result["nodes"])
        self.assertEqual(caller.call_args.kwargs["json"]["task_type"], "fast_text_response")
        self.assertEqual(caller.call_args.kwargs["timeout"], 120)


if __name__ == "__main__":
    unittest.main()
