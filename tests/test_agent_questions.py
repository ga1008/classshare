import contextlib
import json
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from classroom_app.db.schema_agent_authority import ensure_agent_authority_schema
from classroom_app.db.schema_agent_interactions import ensure_agent_interactions_schema
from classroom_app.db.schema_agent_request_budget import ensure_agent_request_budget_schema
from classroom_app.routers import agent_bridge, agent_tasks
from classroom_app.services import agent_gateway_budget, agent_question_service as questions
from classroom_app.services.agent_delegation_service import create_task_attempt, issue_task_delegation
from tests import test_agent_authority as fixture_module


class AgentQuestionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.AgentAuthorityTests()
        self.fixture.setUp(http_authority=False)
        self.conn = self.fixture.conn
        ensure_agent_authority_schema(self.conn)
        ensure_agent_interactions_schema(self.conn)
        ensure_agent_request_budget_schema(self.conn)
        self.conn.executescript("""
            CREATE TABLE user_sessions (session_id TEXT, session_user_key TEXT, user_id TEXT, role TEXT, expires_at TEXT);
            INSERT INTO user_sessions VALUES ('questions-session','teacher:1','1','teacher','2099-01-01T00:00:00+00:00');
            INSERT INTO user_sessions VALUES ('other-session','teacher:2','2','teacher','2099-01-01T00:00:00+00:00');
            INSERT INTO user_sessions VALUES ('student-session','student:1','1','student','2099-01-01T00:00:00+00:00');
            ALTER TABLE agent_tasks ADD COLUMN runtime_status TEXT;
            ALTER TABLE agent_tasks ADD COLUMN updated_at TEXT;
            CREATE TABLE agent_task_events (id INTEGER PRIMARY KEY,task_id INTEGER,event_type TEXT,message TEXT,detail_json TEXT,created_at TEXT);
        """)
        self.attempt = create_task_attempt(self.conn, task_id=7, worker_id="questions", startup_key="questions")
        self.token = issue_task_delegation(self.conn, task_id=7, attempt_id=self.attempt["id"], fencing_token=self.attempt["fencing_token"],
            purpose="tools", scopes=["platform:read"], source_session_id="questions-session")["token"]
        self.conn.commit()
        self.user = {"id": 1, "role": "teacher", "name": "A", "session_id": "questions-session"}
        self.patches = [patch.object(agent_gateway_budget, "get_db_connection", self.connection),
                        patch.object(agent_tasks, "get_db_connection", self.connection)]
        for item in self.patches:
            item.start()
        app = FastAPI()
        app.include_router(agent_bridge.router)
        app.include_router(agent_tasks.router)
        app.dependency_overrides[agent_tasks._current_agent_user] = lambda: self.user
        self.client = TestClient(app)

    @contextlib.contextmanager
    def connection(self):
        with self.conn:
            yield self.conn

    def tearDown(self):
        self.client.close()
        for item in reversed(self.patches):
            item.stop()
        self.fixture.tearDown()

    def create(self, request_id="question-fixture", *, multi=False):
        result = questions.create_question(self.conn, self.token, request_id=request_id, questions=[{
            "id": "format", "question": "希望用哪种格式？", "multiSelect": multi,
            "options": [{"label": "报告", "description": "完整说明"}, {"label": "表格"}],
        }])
        self.conn.commit()
        return result

    def answer(self, item, *, selected=None, custom=None, user=None):
        answer = {"id": "format", "selected": selected or [], **({"custom": custom} if custom is not None else {})}
        result = questions.answer_question(self.conn, user or self.user, 7, item["id"], [answer])
        self.conn.commit()
        return result

    def test_create_poll_user_answer_resumes_exact_question_over_http(self):
        response = self.client.post("/api/agent-bridge/questions", headers={"Authorization": "Bearer " + self.token},
            json={"request_id": "http-fixture", "questions": [{"id": "detail", "question": "请说明需要的时间范围。"}]})
        self.assertEqual(200, response.status_code, response.text)
        item = response.json()
        self.assertEqual("waiting_input", self.conn.execute("SELECT runtime_status FROM agent_tasks WHERE id=7").fetchone()[0])
        response = self.client.post(f"/api/agent-tasks/7/questions/{item['id']}/answer", json={"answers": [{"id": "detail", "selected": [], "custom": "最近一个月"}]})
        self.assertEqual(200, response.status_code, response.text)
        polled = self.client.get(f"/api/agent-bridge/questions/{item['id']}", headers={"Authorization": "Bearer " + self.token})
        self.assertEqual("最近一个月", polled.json()["answers"][0]["custom"])
        self.assertEqual("answered", polled.json()["status"])
        self.assertEqual("running", self.conn.execute("SELECT runtime_status FROM agent_tasks WHERE id=7").fetchone()[0])

    def test_create_idempotency_and_single_pending_batch(self):
        item = self.create()
        self.assertEqual(item["id"], self.create()["id"])
        with self.assertRaises(HTTPException) as error:
            self.create("another-fixture")
        self.assertEqual(409, error.exception.status_code)
        self.conn.rollback()
        with self.assertRaises(HTTPException):
            questions.create_question(self.conn, self.token, request_id="question-fixture", questions=[{"id": "other", "question": "changed"}])

    def test_single_custom_overrides_choice_multi_custom_supplements(self):
        item = self.create()
        result = self.answer(item, selected=["报告"], custom="摘要")
        self.assertEqual([], result["answers"][0]["selected"])
        self.assertEqual("摘要", result["answers"][0]["custom"])
        item = self.create("second-fixture", multi=True)
        result = self.answer(item, selected=["报告", "表格"], custom="也提供附件")
        self.assertEqual(["报告", "表格"], result["answers"][0]["selected"])

    def test_same_number_other_role_and_other_teacher_cannot_read_or_answer(self):
        item = self.create()
        for user in ({"id": 1, "role": "student", "session_id": "student-session"}, {"id": 2, "role": "teacher", "session_id": "other-session"}):
            with self.assertRaises(HTTPException):
                questions.list_user_questions(self.conn, user, 7)
            with self.assertRaises(HTTPException):
                self.answer(item, selected=["报告"], user=user)
            self.conn.rollback()
        self.assertEqual("pending", questions.poll_question(self.conn, self.token, item["id"])["status"])

    def test_duplicate_same_answer_replays_but_changed_answer_is_rejected(self):
        item = self.create()
        first = self.answer(item, selected=["表格"])
        self.assertEqual(first, self.answer(item, selected=["表格"]))
        with self.assertRaises(HTTPException) as error:
            self.answer(item, selected=["报告"])
        self.assertEqual(409, error.exception.status_code)

    def test_expiry_cancel_and_old_attempt_cannot_accept_answer(self):
        item = self.create()
        self.conn.execute("UPDATE agent_task_questions SET expires_at=0")
        self.conn.commit()
        self.assertEqual("expired", questions.poll_question(self.conn, self.token, item["id"])["status"])
        self.conn.commit()
        with self.assertRaises(HTTPException):
            self.answer(item, selected=["报告"])
        self.conn.rollback()
        item = self.create("cancel-fixture")
        questions.close_attempt_questions(self.conn, self.attempt["id"])
        self.conn.commit()
        with self.assertRaises(HTTPException):
            self.answer(item, selected=["报告"])
        self.conn.rollback()
        item = self.create("old-attempt-fixture")
        self.conn.execute("UPDATE agent_task_attempts SET lease_expires_at=0")
        self.conn.commit()
        with self.assertRaises(HTTPException):
            self.answer(item, selected=["报告"])

    def test_revoked_source_cannot_be_answered_even_if_question_is_pending(self):
        item = self.create()
        self.conn.execute("DELETE FROM user_sessions WHERE role='teacher' AND user_id='1'")
        self.conn.commit()
        with self.assertRaises(HTTPException) as error:
            self.answer(item, selected=["报告"])
        self.assertEqual(401, error.exception.status_code)

    def test_invalid_or_invented_answers_do_not_consume_pending_question(self):
        item = self.create()
        for answers in ([], [{"id": "format", "selected": ["不存在"]}], [{"id": "format", "selected": ["报告", "表格"]}], [{"id": "wrong", "custom": "x"}]):
            with self.assertRaises(HTTPException):
                questions.answer_question(self.conn, self.user, 7, item["id"], answers)
            self.conn.rollback()
        self.assertEqual("pending", questions.poll_question(self.conn, self.token, item["id"])["status"])

    def test_expiry_poll_losing_to_answer_does_not_emit_false_closed_event(self):
        item = self.create()
        self.conn.execute("UPDATE agent_task_questions SET expires_at=0")
        self.conn.commit()
        original = questions.verify_task_delegation
        def answer_wins(conn, token, **kwargs):
            if kwargs.get("lock_task"):
                conn.execute("UPDATE agent_task_questions SET status='answered',answers_json=? WHERE id=?",
                             ('[{"id":"format","selected":["报告"]}]', item["id"]))
                conn.commit()
            return original(conn, token, **kwargs)
        with patch.object(questions, "verify_task_delegation", side_effect=answer_wins):
            result = questions.poll_question(self.conn, self.token, item["id"])
        self.assertEqual("answered", result["status"])
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM agent_task_events WHERE event_type='question_closed'").fetchone()[0])

    def test_invalid_unicode_intent_and_option_types_return_validation_errors(self):
        for question in ({"id": "a", "question": "\ud800"},
                         {"id": "a", "question": "Format?", "options": {}},
                         {"id": "a", "question": "Format?", "intent": {"kind": "plan-review", "approve": []}}):
            with self.subTest(question=repr(question)), self.assertRaises(HTTPException) as error:
                questions.normalize_questions([question])
            self.assertEqual(400, error.exception.status_code)

    def test_new_attempt_does_not_accept_prior_pending_answer(self):
        item = self.create()
        self.conn.execute("UPDATE agent_task_attempts SET lease_expires_at=0")
        create_task_attempt(self.conn, task_id=7, worker_id="new-worker", startup_key="new-attempt")
        self.conn.commit()
        with self.assertRaises(HTTPException):
            self.answer(item, selected=["报告"])
        self.conn.rollback()
        self.assertEqual("pending", self.conn.execute("SELECT status FROM agent_task_questions WHERE id=?", (item["id"],)).fetchone()[0])
