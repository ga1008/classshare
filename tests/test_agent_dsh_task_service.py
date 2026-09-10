import contextlib
from dataclasses import dataclass
import hashlib
import json
import unittest
from unittest.mock import patch

from classroom_app.db.schema_agent_authority import ensure_agent_authority_schema
from classroom_app.services import agent_dsh_task_service as service, agent_task_service, agent_key_service
from classroom_app.services.agent_runtime.dsh_provider import DshRunResult, DshSessionRef
from tests import test_agent_authority as authority_fixture


class DshTaskIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = authority_fixture.AgentAuthorityTests()
        self.fixture.setUp(http_authority=False)
        self.conn = self.fixture.conn
        ensure_agent_authority_schema(self.conn)
        from classroom_app.db.schema_agent_interactions import ensure_agent_interactions_schema
        ensure_agent_interactions_schema(self.conn)
        from classroom_app.db.schema_agent_platform_requests import ensure_agent_platform_requests_schema
        ensure_agent_platform_requests_schema(self.conn)
        for name in ("worker_id", "runtime_provider", "runtime_status", "runtime_thread_id", "started_at", "updated_at",
                     "source_session_hash", "source_session_key", "context_snapshot_json", "title", "origin", "result_summary",
                     "result_detail_json", "error_message", "completed_at"):
            self.conn.execute(f"ALTER TABLE agent_tasks ADD COLUMN {name} TEXT")
        self.conn.executescript("""
            CREATE TABLE user_sessions (session_id TEXT, session_user_key TEXT, user_id TEXT, role TEXT, expires_at TEXT);
            INSERT INTO user_sessions VALUES ('worker-session', 'teacher:1', '1', 'teacher', '2099-01-01T00:00:00+00:00');
            CREATE TABLE agent_task_events (id INTEGER PRIMARY KEY, task_id INTEGER, event_type TEXT,
                                           message TEXT, detail_json TEXT, created_at TEXT);
        """)
        self.conn.execute("UPDATE agent_tasks SET worker_id='worker', runtime_provider='deepseek-dsh', source_session_key='teacher:1', "
                          "source_session_hash=?, context_snapshot_json='{}', title='Fixture', origin='manual', started_at='2026-01-01T00:00:00+00:00' WHERE id=7",
                          (hashlib.sha256(b"worker-session").hexdigest(),))
        self.conn.commit()
        self.stopped = False
        self.patches = [patch.object(service, "get_db_connection", self.connection), patch.object(service, "AGENT_DSH_ENABLED", True),
                        patch.object(agent_task_service, "AGENT_TASK_WORKSPACE_ROOT", self.fixture.root),
                        patch.object(agent_task_service, "_notify_task_finished"),
                        patch.object(agent_task_service, "build_runtime_prompt", return_value="MCP fixture task"),
                        patch.object(agent_key_service, "get_active_agent_api_key", return_value=({"model": "deepseek-v4-pro"}, "SYNTHETIC-REAL-KEY")),
                        patch.object(service, "control", side_effect=self.control)]
        for item in self.patches:
            item.start()

    @contextlib.contextmanager
    def connection(self):
        try:
            yield self.conn
        except BaseException:
            self.conn.rollback()
            raise

    def control(self, payload, **kwargs):
        if payload["action"] == "probe":
            return {"status": "ready", "dsh_package_version": "0.1.5-rc.1", "profile_sha256": "a" * 64}
        if payload["action"] == "stop":
            self.stopped = True
            return {"status": "stopped"}
        raise AssertionError("Unexpected control action")

    def task(self):
        return dict(self.conn.execute("SELECT * FROM agent_tasks WHERE id=7").fetchone())

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.fixture.tearDown()

    async def test_success_uses_scoped_env_and_finalizes_only_after_stop(self):
        outer = self
        class Provider:
            def __init__(self, identity, options, **kwargs):
                self.identity, self.evidence = identity, kwargs["runtime_evidence"]
                outer.assertEqual("/workspace", kwargs["runtime_cwd"])
                outer.assertNotIn("SYNTHETIC-REAL-KEY", str(options.env))
                outer.assertNotIn("DATABASE_URL", options.env)
                outer.assertNotIn("SECRET_KEY", options.env)
                outer.assertEqual("teacher:1", identity.actor_id)
                outer.assertEqual("off", kwargs["reasoning_effort"])
                outer.assertEqual("http", kwargs["mcp_servers"][0]["type"])
            async def run(self, prompt, **kwargs):
                (outer.fixture.root / "tasks/7/RESULT.md").write_text("# Completed fixture\n\nResult", encoding="utf-8")
                return DshRunResult(DshSessionRef(self.identity, "session-fixture"), self.evidence, "end_turn", "Final", (), (), True)
            async def close(self):
                pass
        original = service._result_detail
        def collect_after_stop(*args, **kwargs):
            self.assertTrue(self.stopped)
            return original(*args, **kwargs)
        with patch.object(service, "DshProvider", Provider), patch.object(service, "_result_detail", side_effect=collect_after_stop):
            await service.run_dsh_task(self.task())
        self.assertEqual("completed", self.task()["status"])
        self.assertEqual("Completed fixture", self.task()["result_summary"])
        self.assertEqual({"revoked"}, {row[0] for row in self.conn.execute("SELECT status FROM agent_task_delegations")})

    async def test_unconfirmed_stop_keeps_task_active_for_reconciliation(self):
        class Provider:
            def __init__(self, *args, **kwargs):
                pass
            async def run(self, *args, **kwargs):
                raise RuntimeError("fixture failure")
            async def close(self):
                pass
        def control(payload, **kwargs):
            if payload["action"] == "stop":
                raise ConnectionError("fixture launcher unreachable")
            return self.control(payload, **kwargs)
        with patch.object(service, "DshProvider", Provider), patch.object(service, "control", side_effect=control):
            await service.run_dsh_task(self.task())
        self.assertEqual("running", self.task()["status"])
        self.assertIn("runtime_stop_pending", [row[0] for row in self.conn.execute("SELECT event_type FROM agent_task_events")])

    async def test_revoked_session_does_not_launch_or_commit_an_attempt(self):
        self.conn.execute("DELETE FROM user_sessions")
        self.conn.commit()
        with patch.object(service, "DshProvider") as provider:
            await service.run_dsh_task(self.task())
        provider.assert_not_called()
        self.assertEqual("failed", self.task()["status"])
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM agent_task_attempts").fetchone()[0])

    async def test_expired_attempt_is_stopped_before_recovery(self):
        task, actor, attempt, tokens, model = service._setup_attempt(7)
        self.conn.execute("UPDATE agent_task_attempts SET lease_expires_at=0")
        self.conn.commit()
        result = service.recover_stale_dsh_tasks()
        self.assertTrue(self.stopped)
        self.assertEqual(1, result["recovered"])
        self.assertEqual("failed", self.task()["status"])
        self.assertEqual("failed", self.conn.execute("SELECT status FROM agent_task_attempts").fetchone()[0])

    async def test_old_attempt_cannot_overwrite_newer_fence(self):
        task, actor, attempt, tokens, model = service._setup_attempt(7)
        self.conn.execute("UPDATE agent_task_attempts SET lease_expires_at=0")
        newer = service.create_task_attempt(self.conn, task_id=7, worker_id="new-worker", startup_key="newer")
        self.conn.commit()
        service._finish_fenced(attempt, status="failed", summary="Old must not win", detail={})
        self.assertEqual("running", self.task()["status"])
        self.assertEqual("running", self.conn.execute("SELECT status FROM agent_task_attempts WHERE id=?", (newer["id"],)).fetchone()[0])

    async def test_result_and_progress_redact_scoped_credentials(self):
        token = "lsagt_" + "a" * 43
        value = service.redact_runtime_value({"output": "echo " + token, "Authorization": "Bearer " + token})
        self.assertNotIn(token, str(value))
        self.assertNotIn("Authorization", value)

    async def test_running_supplement_is_delivered_to_same_provider_next_prompt(self):
        outer = self
        prompts = []
        class Provider:
            def __init__(self, identity, options, **kwargs):
                self.identity, self.evidence = identity, kwargs["runtime_evidence"]
            async def run(self, prompt, **kwargs):
                prompts.append(prompt[0]["text"])
                if len(prompts) == 1:
                    context = {"agent_options": {"pending_supplements": [{"id": "supplement-1", "message": "增加移动端版本", "delivery_status": "pending"}]}}
                    outer.conn.execute("UPDATE agent_tasks SET context_snapshot_json=? WHERE id=7", (json.dumps(context),))
                    outer.conn.commit()
                return DshRunResult(DshSessionRef(self.identity, "same-session"), self.evidence, "end_turn", "已纳入补充", (), (), True)
            async def close(self):
                pass
        with patch.object(service, "DshProvider", Provider):
            await service.run_dsh_task(self.task())
        self.assertEqual(2, len(prompts))
        self.assertIn("增加移动端版本", prompts[1])
        options = json.loads(self.task()["context_snapshot_json"])["agent_options"]
        self.assertEqual("delivered", options["pending_supplements"][0]["delivery_status"])
        self.assertEqual("completed", self.task()["status"])

    async def test_successful_model_turn_with_revoked_session_is_not_business_completion(self):
        outer = self
        class Provider:
            def __init__(self, identity, options, **kwargs):
                self.identity, self.evidence = identity, kwargs["runtime_evidence"]
            async def run(self, prompt, **kwargs):
                outer.conn.execute("DELETE FROM user_sessions")
                outer.conn.commit()
                return DshRunResult(DshSessionRef(self.identity, "session"), self.evidence, "end_turn", "Partial text", (), (), True)
            async def close(self):
                pass
        with patch.object(service, "DshProvider", Provider):
            await service.run_dsh_task(self.task())
        self.assertEqual("failed", self.task()["status"])
        self.assertTrue(json.loads(self.task()["result_detail_json"])["partial_result_available"])

    async def test_fourth_prompt_retains_unprocessed_supplement_and_all_round_receipts(self):
        outer, prompts = self, []
        class Provider:
            def __init__(self, identity, options, **kwargs):
                self.identity, self.evidence = identity, kwargs["runtime_evidence"]
            async def run(self, prompt, **kwargs):
                prompts.append(prompt[0]["text"])
                context = json.loads(outer.task()["context_snapshot_json"])
                pending = context.setdefault("agent_options", {}).setdefault("pending_supplements", [])
                pending.append({"id": "input-" + str(len(prompts)), "message": "New instruction", "delivery_status": "pending"})
                outer.conn.execute("UPDATE agent_tasks SET context_snapshot_json=? WHERE id=7", (json.dumps(context),))
                outer.conn.commit()
                return DshRunResult(DshSessionRef(self.identity, "one-session"), self.evidence, "end_turn", "Current result",
                    ({"tool_call_id": "call-" + str(len(prompts)), "status": "completed"},), (), True)
            async def close(self):
                pass
        with patch.object(service, "DshProvider", Provider):
            await service.run_dsh_task(self.task())
        self.assertEqual(4, len(prompts))
        detail = json.loads(self.task()["result_detail_json"])
        self.assertEqual("failed", self.task()["status"])
        self.assertEqual("input-4", detail["unprocessed_supplements"][0]["id"])
        self.assertEqual([1, 2, 3, 4], [item["prompt_number"] for item in detail["tool_receipts"]])
        self.assertEqual("partial", detail["completion_kind"])

    async def test_refusal_does_not_report_submitted_supplement_as_processed(self):
        _, _, attempt, tokens, _ = service._setup_attempt(7)
        self.conn.execute("UPDATE agent_tasks SET context_snapshot_json=? WHERE id=7", (json.dumps({"agent_options": {
            "pending_supplements": [{"id": "new-input", "message": "Need a report", "delivery_status": "pending"}]}}),))
        self.conn.commit()
        service._finish_prompt_or_next(attempt, tokens["tools"], {"new-input"}, submitted=False, allow_next=False)
        self.assertEqual("pending", json.loads(self.task()["context_snapshot_json"])["agent_options"]["pending_supplements"][0]["delivery_status"])

    async def test_stale_prior_result_cannot_complete_a_new_empty_run(self):
        outer = self
        class Provider:
            def __init__(self, identity, options, **kwargs):
                self.identity, self.evidence = identity, kwargs["runtime_evidence"]
            async def run(self, prompt, **kwargs):
                return DshRunResult(DshSessionRef(self.identity, "session"), self.evidence, "end_turn", "", (), (), True)
            async def close(self):
                pass
        with patch.object(service, "DshProvider", Provider):
            await service.run_dsh_task(self.task())
        self.assertEqual("failed", self.task()["status"])
        self.assertNotEqual("own result", self.task()["result_summary"])
        self.assertEqual("own result", (outer.fixture.root / "tasks/7/RESULT.md").read_text())

    async def test_current_response_wins_when_supplement_does_not_rewrite_result_file(self):
        _, _, attempt, _, _ = service._setup_attempt(7)
        result = DshRunResult(DshSessionRef(service.DshRunIdentity("7", "teacher:1", attempt["id"], "1"), "session"),
            service.DshRuntimeEvidence("fixture", "a" * 64), "end_turn", "New response to current instruction", (), (), True)
        _, summary, detail = service._result_detail(7, result, previous_deliverable="own result")
        self.assertEqual("New response to current instruction", summary)
        self.assertNotIn("own result", detail["deliverable_markdown"])

    def _generation_receipt(self, *, status="queued"):
        _, _, attempt, tokens, _ = service._setup_attempt(7)
        self.conn.executescript("""
            CREATE TABLE class_offering_sessions (id INTEGER PRIMARY KEY,class_offering_id INTEGER,learning_material_id INTEGER);
            INSERT INTO class_offering_sessions VALUES (100,10,500);
            CREATE TABLE session_material_generation_tasks (id INTEGER PRIMARY KEY,teacher_id INTEGER,class_offering_id INTEGER,
                session_id INTEGER,status TEXT,generated_material_id INTEGER,generated_material_path TEXT);
            CREATE TABLE course_materials (id INTEGER PRIMARY KEY,teacher_id INTEGER,material_path TEXT,file_hash TEXT,file_size INTEGER);
            CREATE TABLE class_offering_learning_materials (class_offering_id INTEGER,session_id INTEGER,material_id INTEGER);
            INSERT INTO class_offering_learning_materials VALUES (10,100,500);
        """)
        self.material_path = self.fixture.root / "generated-material.md"
        self.material_path.write_bytes(b"# Native generated material")
        self.conn.execute("INSERT INTO session_material_generation_tasks VALUES (90,1,10,100,?,500,'lesson.md')", (status,))
        self.conn.execute("INSERT INTO course_materials VALUES (500,1,'lesson.md',?,?)",
                          (hashlib.sha256(self.material_path.read_bytes()).hexdigest(), self.material_path.stat().st_size))
        from classroom_app.services.agent_operation_service import claim_agent_operation, complete_agent_operation
        claim_agent_operation(self.conn, token=tokens["tools"], operation_id="generate-90", action="generate_session_document",
            params={"session_id": 100, "class_offering_id": 10}, required_scope="platform:write")
        complete_agent_operation(self.conn, token=tokens["tools"], operation_id="generate-90", required_scope="platform:write", result={
            "ref_id": 90, "completion_status": "pending", "generation_task": {"id": 90, "teacher_id": 1, "class_offering_id": 10, "session_id": 100}})
        self.conn.commit()
        return attempt

    async def test_committed_generation_submission_remains_partial_while_domain_job_runs(self):
        attempt = self._generation_receipt(status="running")
        service._finish_fenced(attempt, status="completed", summary="Model says done", detail={"deliverable_markdown": "Done"})
        detail = json.loads(self.task()["result_detail_json"])
        self.assertEqual("failed", self.task()["status"])
        self.assertEqual("partial", detail["completion_kind"])
        self.assertIn("domain_job_pending", [item["code"] for item in detail["completion_blockers"]])
        self.assertEqual("completed", detail["platform_operations"][0]["status"])
        self.assertEqual("running", detail["platform_operations"][0]["domain_result"]["status"])
        self.assertEqual("running", self.conn.execute("SELECT status FROM session_material_generation_tasks").fetchone()[0])

    async def test_generated_document_needs_exact_job_actor_binding_and_file_integrity(self):
        attempt = self._generation_receipt(status="completed")
        rows = self.conn.execute("SELECT * FROM agent_action_executions").fetchall()
        from classroom_app.services import file_service
        with patch.object(file_service, "resolve_global_file_path", return_value=self.material_path):
            for statement, blocker in [
                ("UPDATE session_material_generation_tasks SET teacher_id=2", "domain_job_identity_mismatch"),
                ("UPDATE session_material_generation_tasks SET session_id=999", "domain_job_identity_mismatch"),
                ("DELETE FROM class_offering_learning_materials", "domain_material_binding_missing"),
                ("UPDATE class_offering_sessions SET learning_material_id=NULL", "domain_material_binding_missing"),
                ("UPDATE course_materials SET file_hash='forged'", "domain_material_integrity_failed"),
            ]:
                with self.subTest(blocker=blocker, statement=statement):
                    self.conn.execute("SAVEPOINT bad_evidence")
                    self.conn.execute(statement)
                    _, blockers = service._verified_platform_operations(self.conn, self.task(), rows)
                    self.assertEqual(blocker, blockers[0]["code"])
                    self.conn.execute("ROLLBACK TO bad_evidence")
                    self.conn.execute("RELEASE bad_evidence")
            service._finish_fenced(attempt, status="completed", summary="Done", detail={"deliverable_markdown": "Done"})
        detail = json.loads(self.task()["result_detail_json"])
        self.assertEqual("completed", self.task()["status"])
        self.assertEqual("verified_business", detail["completion_kind"])
        self.assertTrue(detail["platform_operations"][0]["domain_result"]["binding_verified"])

    async def test_pending_proposal_or_unknown_operation_is_not_completed_business(self):
        _, _, attempt, tokens, _ = service._setup_attempt(7)
        from classroom_app.services.agent_operation_service import claim_agent_operation
        claim_agent_operation(self.conn, token=tokens["tools"], operation_id="unknown-write", action="publish_blog_post",
                              params={"title": "Fixture"}, required_scope="platform:write")
        self.conn.commit()
        service._finish_fenced(attempt, status="completed", summary="Published", detail={
            "deliverable_markdown": "Published", "proposed_actions": [{"action": "publish_blog_post", "executed": None}]})
        detail = json.loads(self.task()["result_detail_json"])
        self.assertEqual("failed", self.task()["status"])
        self.assertEqual({"operation_executing", "unexecuted_proposals"}, {item["code"] for item in detail["completion_blockers"]})

    async def test_future_deferred_action_cannot_treat_submission_as_business_completion(self):
        rows = [{"operation_id": "future-job", "actor_role": "teacher", "actor_id": 1,
                 "action": "future_domain_generation", "status": "completed", "result_json": '{"completion_status":"pending"}'}]
        operations, blockers = service._verified_platform_operations(self.conn, self.task(), rows)
        self.assertEqual("unverified", operations[0]["completion_status"])
        self.assertEqual("domain_result_unverified", blockers[0]["code"])

    def _http_request(self, *, status):
        _, _, attempt, _, _ = service._setup_attempt(7)
        self.conn.execute("""INSERT INTO agent_platform_requests
            (id,operation_id,task_id,attempt_id,fencing_token,delegation_id,actor_role,actor_id,
             source_session_hash,authority_fingerprint,capability_key,method,path,route_source_sha256,
             request_hash,intent_hash,request_json,settlement_hash,status,result_json,created_at,updated_at)
            VALUES ('http-1','request-1',7,?,?,'grant','teacher',1,'session','authority','http.test','POST','/test',
                    'source','hash','intent','{}','settlement',?,?,1,1)""",
            (attempt["id"], attempt["fencing_token"], status, json.dumps({"http_status": 200, "data": {"status": "ok"}, "follow_up": "inspect_observed_result"})))
        self.conn.commit()
        return attempt

    async def test_observed_success_is_visible_but_never_promoted_to_verified_business(self):
        attempt = self._http_request(status="observed_http_result")
        service._finish_fenced(attempt, status="completed", summary="Verified all business", detail={})
        detail = json.loads(self.task()["result_detail_json"])
        self.assertEqual("completed", self.task()["status"])
        self.assertEqual("observed_http_result", detail["completion_kind"])
        self.assertFalse(detail["business_outcome_verified"])
        self.assertFalse(detail["platform_requests"][0]["verified_business"])
        self.assertNotIn("data", detail["platform_requests"][0]["observation"])

    async def test_stopped_attempt_preserves_uncertain_request_and_blocks_completion(self):
        attempt = self._http_request(status="executing")
        service._finish_fenced(attempt, status="completed", summary="Everything done", detail={})
        detail = json.loads(self.task()["result_detail_json"])
        self.assertEqual("failed", self.task()["status"])
        self.assertEqual("partial", detail["completion_kind"])
        self.assertEqual("uncertain", self.conn.execute("SELECT status FROM agent_platform_requests").fetchone()[0])
        self.assertIn("platform_request_uncertain", [item["code"] for item in detail["completion_blockers"]])

    async def test_committed_self_authority_stop_explains_result_without_hiding_pending_work(self):
        _, _, attempt, tokens, _ = service._setup_attempt(7)
        from classroom_app.services.agent_operation_service import claim_agent_operation, complete_agent_operation
        claim_agent_operation(self.conn, token=tokens["tools"], operation_id="self-role", action="set_teacher_super_admin",
                              params={"teacher_id": 1}, required_scope="platform:write")
        complete_agent_operation(self.conn, token=tokens["tools"], operation_id="self-role", required_scope="platform:write",
                                 result={"agent_stop_required": True, "authority_transition": True})
        self.conn.commit()
        service._finish_fenced(attempt, status="failed", summary="Authorization revoked", error="Authorization revoked",
                               detail={"proposed_actions": [{"action": "another_action", "executed": None}]})
        detail = json.loads(self.task()["result_detail_json"])
        self.assertEqual("failed", self.task()["status"])
        self.assertEqual("authority_changed_by_task", detail["stop_reason"])
        self.assertEqual("partial", detail["completion_kind"])
        self.assertIn("unexecuted_proposals", [item["code"] for item in detail["completion_blockers"]])
        self.assertIn("权限变更已提交", self.task()["result_summary"])
        self.assertFalse(self.task()["error_message"])
