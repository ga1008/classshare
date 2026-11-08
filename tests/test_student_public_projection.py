"""P0 public-boundary contracts, using synthetic records in owned SQLite only."""
from __future__ import annotations

import json
import sqlite3
import unittest
from unittest.mock import patch
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient

from classroom_app import database
from classroom_app.dependencies import get_current_user
from classroom_app.routers import learning as routes
from classroom_app.services import ai_workspace_service as workspace
from classroom_app.services import learning_progress_service as learning
from classroom_app.services import platform_knowledge_service as knowledge
from classroom_app.services.psych_profile_service import (
    HiddenProfileLeakGuard, compose_classroom_chat_system_prompt,
    contains_hidden_profile_marker, load_explicit_user_profile,
)
from classroom_app.services.student_public_projection_service import public_student_learning_state, public_student_legacy_tool_result
from tests.sqlite_database_fixture import isolated_sqlite_database
from tests.test_learning_progress_snapshots import _metrics


CANARY = "PRIVATE_CANARY_INFERRED_20261109"


class PublicProjectionTests(unittest.TestCase):
    def test_old_tool_receipts_are_projected_without_changing_audit(self):
        source = {"status": "observed", "result": {"score": 52.4, "interaction_quality_reason": CANARY,
                  "profile_summary": CANARY, "note": "--- 全量画像 --- " + CANARY}}
        result = public_student_legacy_tool_result(source)
        self.assertEqual(52.4, result["result"]["score"])
        self.assertNotIn(CANARY, json.dumps(result, ensure_ascii=False))
        self.assertEqual(CANARY, source["result"]["profile_summary"])

    def test_grading_rejects_inference_input_even_for_old_queued_jobs(self):
        import ai_assistant
        from classroom_app.services import ai_grading_service
        class NoDatabase:
            def execute(self, *_args):
                raise AssertionError("Grading read a private profile")
        self.assertEqual("", ai_grading_service._build_hidden_student_profile_context(NoDatabase(), {"class_offering_id": 1, "student_pk_id": 1}))
        self.assertEqual("", ai_assistant._build_hidden_grading_profile_prompt(CANARY))
    def test_recursive_allowlist_keeps_unknown_zero_and_public_display(self):
        state = {"score": None, "snapshot_status": "building", "raw_payload": CANARY,
                 "metrics": {"score": None, "components": {"interaction": 0, "private": CANARY},
                             "interactions": {"focus_seconds": 0, "interaction_quality_reason": CANARY,
                                              "interaction_quality_source": "profile", "extra": CANARY}},
                 "stages": [{"key": "qi", "status": "passed", "certificate": {"id": 7, "issued_at": "2026-01-01", "metadata": {"secret": CANARY}}}],
                 "class_position": {"leader": {"name": "Public synthetic peer", "score": 42, "is_self": False},
                                    "ranking": [{"name": "Public synthetic peer", "score": 42}]},
                 "recent_score_events": [{"id": 1, "delta": 2, "source_ref": CANARY,
                                          "metadata": {"previous": 2, "current": 4, "private": CANARY}}]}
        public = public_student_learning_state(state)
        self.assertIsNone(public["score"])
        self.assertEqual(0, public["metrics"]["interactions"]["focus_seconds"])
        self.assertEqual(7, public["stages"][0]["certificate"]["id"])
        self.assertEqual("Public synthetic peer", public["class_position"]["leader"]["name"])
        self.assertNotIn(CANARY, json.dumps(public))
        self.assertNotIn("interaction_quality", json.dumps(public))
        self.assertEqual(CANARY, state["raw_payload"], "Projection must not rewrite the stored/source object")

    def test_public_chat_composer_does_not_read_hidden_argument(self):
        class Unreadable(dict):
            def get(self, *_args, **_kwargs):
                raise AssertionError("Private inference was read by public chat")
        value = compose_classroom_chat_system_prompt("Course rules", "Syllabus", "Student facts", Unreadable())
        self.assertIn("Student facts", value)
        self.assertNotIn("personality", value)
        self.assertFalse(contains_hidden_profile_marker(value))
        from classroom_app.routers.ai import _compose_classroom_chat_system_prompt
        self.assertIn("Student facts", _compose_classroom_chat_system_prompt("Course", "Syllabus", "Student facts", Unreadable()))

    def test_student_workspace_never_loads_legacy_profile_fallback(self):
        class NoDatabase:
            def execute(self, *_args):
                raise AssertionError("Legacy private store must not be read")
        self.assertIsNone(workspace.load_personal_hidden_profile(NoDatabase(), 1, "student"))

    def test_stream_guard_blocks_internal_identifiers_at_every_boundary(self):
        text = "前缀\npsych_profile hidden_premise_prompt interaction_quality_reason 心理侧写师\n后缀"
        for size in range(1, 25):
            guard = HiddenProfileLeakGuard()
            value = "".join(guard.feed(text[index:index + size]) for index in range(0, len(text), size)) + guard.flush()
            self.assertFalse(contains_hidden_profile_marker(value), (size, value))
            self.assertTrue(value.startswith("前缀\n"))
            self.assertTrue(value.endswith("\n后缀"))

    def test_student_greeting_invalidates_old_derived_cache_only_once(self):
        from classroom_app.services import personal_greeting_service as greeting
        from classroom_app.services.academic_service import china_today
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE personal_greetings(user_role TEXT,user_pk INTEGER,greet_date TEXT,status TEXT,greeting_text TEXT,persona TEXT,UNIQUE(user_role,user_pk,greet_date))")
        today = china_today().isoformat()
        conn.execute("INSERT INTO personal_greetings VALUES('student',1,?,'ready',?,'old persona')", (today, CANARY))
        with patch('classroom_app.services.life_tip_service.ensure_life_tip_runtime'), \
             patch('classroom_app.services.scheduled_task_service.schedule_task') as schedule:
            self.assertEqual({"status": "pending"}, greeting.get_or_request_personal_greeting(conn, user_role="student", user_pk=1))
            self.assertEqual({"status": "pending"}, greeting.get_or_request_personal_greeting(conn, user_role="student", user_pk=1))
        self.assertEqual(1, schedule.call_count)
        self.assertEqual("", conn.execute("SELECT greeting_text FROM personal_greetings").fetchone()[0])
        conn.execute("UPDATE personal_greetings SET status='ready',greeting_text='从今天的一小步开始学习吧',persona=?", (greeting.GREETING_CONTEXT_VERSION + "温柔学姐",))
        with patch('classroom_app.services.life_tip_service.ensure_life_tip_runtime'):
            result = greeting.get_or_request_personal_greeting(conn, user_role="student", user_pk=1)
        self.assertEqual("温柔学姐", result["persona"])
        class NoDatabase:
            def execute(self, *_args):
                raise AssertionError("Student greeting read private profile")
        self.assertEqual("", greeting._load_profiler_hint(NoDatabase(), "student", 1))

    def test_old_workspace_history_and_retry_are_sanitized_without_rewriting_rows(self):
        from classroom_app.db.schema_ai_workspace import ensure_ai_workspace_schema
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE classroom_behavior_profiles(id INTEGER PRIMARY KEY,user_role TEXT,user_pk INTEGER,created_at TEXT)")
        conn.execute("CREATE TABLE ai_psychology_profiles(id INTEGER PRIMARY KEY,user_role TEXT,user_pk INTEGER,created_at TEXT)")
        ensure_ai_workspace_schema(conn, engine="sqlite")
        session = workspace.create_session(conn, {"id": 1, "role": "student"})
        text = "侧写师使用 mental_state_summary 给出的旧回答"
        conn.execute("INSERT INTO ai_workspace_messages(session_id,request_id,role,message,final_answer,status,created_at) VALUES(?,'old','assistant',?,?,'completed',datetime('now'))", (session['id'], text, text))
        for value in (workspace.load_history(conn, session['session_uuid'], {"id": 1, "role": "student"}),
                      workspace.completed_reply(conn, session['id'], 'old'), workspace.ai_history(conn, session['id'], 'new')):
            self.assertFalse(contains_hidden_profile_marker(json.dumps(value, ensure_ascii=False)))
        self.assertEqual(text, conn.execute("SELECT message FROM ai_workspace_messages").fetchone()[0])


class PublicLearningHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.enterClassContext(isolated_sqlite_database())
        with database.get_db_connection() as conn:
            cls.teacher = conn.execute("INSERT INTO teachers(name,email,hashed_password) VALUES('Synthetic teacher','p0@example.invalid','unused') RETURNING id").fetchone()[0]
            class_id = conn.execute("INSERT INTO classes(name,created_by_teacher_id) VALUES('Synthetic class',?) RETURNING id", (cls.teacher,)).fetchone()[0]
            cls.student = conn.execute("INSERT INTO students(name,student_id_number,class_id,description,nickname,today_mood) VALUES('Synthetic student','P0-STUDENT',?,?,'Explicit nickname','开心') RETURNING id", (class_id, CANARY)).fetchone()[0]
            course_id = conn.execute("INSERT INTO courses(name,created_by_teacher_id) VALUES('Synthetic course',?) RETURNING id", (cls.teacher,)).fetchone()[0]
            cls.offering = conn.execute("INSERT INTO class_offerings(class_id,course_id,teacher_id) VALUES(?,?,?) RETURNING id", (class_id, course_id, cls.teacher)).fetchone()[0]
            metrics = _metrics(52.4, material=20, task=20, interaction=10, consistency=2.4)
            metrics["interactions"].update(interaction_quality=.2, interaction_quality_factor=.6,
                                           interaction_quality_source="profile", interaction_quality_reason=CANARY)
            metrics["private"] = {"hidden": CANARY}
            conn.execute("INSERT INTO learning_progress_snapshots(class_offering_id,student_id,score,progress_percent,components_json,metrics_json,calculated_at,dirty) VALUES(?,?,52.4,60,?,?, '2026-01-01',0)",
                         (cls.offering, cls.student, json.dumps(metrics["components"]), json.dumps(metrics)))
            conn.execute("INSERT INTO cultivation_score_events(class_offering_id,student_id,event_type,delta,component,source_ref,created_at,metadata_json) VALUES(?,?,'interaction',2,'interaction',?,datetime('now'),?)",
                         (cls.offering, cls.student, CANARY, json.dumps({"previous": 8, "current": 10, "secret": CANARY})))
            conn.commit()

    def test_http_progress_snapshot_and_ledger_keep_published_score_without_private_fields(self):
        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[get_current_user] = lambda: {"id": self.student, "role": "student", "name": "Synthetic student"}
        with database.get_db_connection() as conn:
            before = dict(conn.execute("SELECT * FROM learning_progress_snapshots WHERE class_offering_id=? AND student_id=?", (self.offering, self.student)).fetchone())
        with TestClient(app) as client:
            for suffix in ("progress", "snapshot", "score-events"):
                response = client.get(f"/api/classrooms/{self.offering}/learning/{suffix}")
                self.assertEqual(200, response.status_code, response.text)
                self.assertNotIn(CANARY, response.text)
                self.assertNotIn("interaction_quality", response.text)
                if suffix != "score-events":
                    self.assertEqual(52.4, response.json()["progress"]["score"])
        with database.get_db_connection() as conn:
            after = dict(conn.execute("SELECT * FROM learning_progress_snapshots WHERE class_offering_id=? AND student_id=?", (self.offering, self.student)).fetchone())
        self.assertEqual(before, after, "Read-time privacy repair must preserve historical published scores and source metadata")

    def test_student_agent_meta_context_and_explicit_preferences_are_factual(self):
        with database.get_db_connection() as conn:
            context = knowledge.build_user_knowledge_block(conn, self.student, "student")
            explicit = load_explicit_user_profile(conn, self.student, "student")
        self.assertNotIn(CANARY, context)
        self.assertNotIn("画像", context)
        self.assertNotIn("画像", knowledge.build_platform_overview_block("student"))
        self.assertEqual("", explicit["description"])
        self.assertEqual("Explicit nickname", explicit["nickname"])
        self.assertEqual("开心", explicit["today_mood"])

    def test_actual_agent_meta_response_does_not_return_legacy_description(self):
        from classroom_app.routers import agent_bridge
        from classroom_app.services.agent_actor_service import AgentActor
        actor = AgentActor("student", self.student, "Synthetic student", False, (), 1)
        with patch.object(agent_bridge, '_require_task_id', return_value=99), \
             patch.object(agent_bridge, 'resolve_live_task_actor', return_value=({"id": 99}, actor)):
            result = agent_bridge.bridge_meta(authorization="Bearer synthetic")
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(CANARY, serialized)
        self.assertNotIn("画像", result["task_owner_profile"])

    def test_student_attendance_advice_keeps_facts_and_rejects_old_unknown_payload(self):
        from classroom_app.services import smart_attendance_advice_service as advice
        context = {"course": {"course_name": "Synthetic course", "private": CANARY},
                   "student_attendance": {"checked": 3, "total": 4, "mental_state_summary": CANARY},
                   "fallback_cards": [{"key": "personal_attention", "title": "查看到课记录", "fallback_text": "核对本课记录"}],
                   "hidden_profile": {"profile_summary": CANARY}}
        system, message, keys = advice._build_ai_messages({"class_offering_id": self.offering, "student_id": self.student,
                                                          "context_json": json.dumps(context)})
        self.assertNotIn(CANARY, message)
        self.assertIn("Synthetic course", message)
        facts = json.loads(message.split("课堂与出勤统计 JSON：\n", 1)[1].split("\n\n", 1)[0])
        self.assertEqual(3, facts["student_attendance"]["checked"])
        self.assertEqual(["personal_attention"], keys)

    def test_public_discussion_actor_context_excludes_private_and_contact_fields(self):
        from classroom_app.services.psych_profile_service import public_classroom_actor_context
        with database.get_db_connection() as conn:
            text = public_classroom_actor_context(conn, self.student, "student")
        self.assertIn("学生", text)
        self.assertNotIn(CANARY, text)
        self.assertNotIn("P0-STUDENT", text)

    def test_legacy_student_chat_rebuilds_poisoned_context_from_facts(self):
        from classroom_app.routers import ai
        app = FastAPI()
        app.include_router(ai.router)
        app.dependency_overrides[get_current_user] = lambda: {"id": self.student, "role": "student", "name": "Synthetic student"}
        calls = []
        class Response:
            is_success = True
            async def aiter_lines(self):
                for event in ({"event": "answer_delta", "delta": "先完成一个小练习。"}, {"event": "done"}):
                    yield json.dumps(event, ensure_ascii=False)
        @asynccontextmanager
        async def stream(*_args, **kwargs):
            calls.append(kwargs['json'])
            yield Response()
        async def no_retrieval(*_args, **_kwargs):
            if False:
                yield ''
        with patch.object(ai.ai_client, 'stream', stream), \
             patch.object(ai, 'record_behavior_event'), \
             patch.object(ai, '_gongwen_retrieval_events', no_retrieval), \
             patch.object(ai, '_platform_data_retrieval_events', no_retrieval), TestClient(app) as client:
            created = client.post(f'/api/ai/chat/session/new/{self.offering}')
            self.assertEqual(200, created.status_code, created.text)
            session_uuid = created.json()['session']['session_uuid']
            with database.get_db_connection() as conn:
                conn.execute("UPDATE ai_chat_sessions SET context_prompt=? WHERE session_uuid=?", (CANARY, session_uuid))
                conn.commit()
            response = client.post('/api/ai/chat', data={"session_uuid": session_uuid, "class_offering_id": self.offering,
                                                        "page_path": f"/classroom/{self.offering}", "message": "请讲解本课知识"})
            self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(1, len(calls))
        self.assertNotIn(CANARY, calls[0]['system_prompt'])
        self.assertNotIn("心理状态：", calls[0]['system_prompt'])
        self.assertIn("Explicit nickname", calls[0]['system_prompt'])

    def test_http_does_not_allow_student_to_request_teacher_overview(self):
        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[get_current_user] = lambda: {"id": self.student + 99999, "role": "student"}
        with TestClient(app) as client:
            self.assertEqual(403, client.get(f"/api/classrooms/{self.offering}/learning/progress").status_code)


class FactScoringTests(unittest.TestCase):
    def test_new_calculation_ignores_all_private_quality_values(self):
        from tests.test_interaction_quality_scoring import _build_conn, _seed_full_interaction_counts, FULL_SEED_BASE
        conn = _build_conn()
        self.addCleanup(conn.close)
        _seed_full_interaction_counts(conn)
        for quality in (0, .2, 1):
            conn.execute("DELETE FROM classroom_behavior_profiles")
            conn.execute("INSERT INTO classroom_behavior_profiles(class_offering_id,user_pk,user_role,interaction_quality,interaction_quality_reason,created_at) VALUES(1,7,'student',?,?,datetime('now'))", (quality, CANARY))
            statements = []
            conn.set_trace_callback(statements.append)
            metrics = learning._load_interaction_metrics(conn, 1, 7)
            conn.set_trace_callback(None)
            self.assertAlmostEqual(FULL_SEED_BASE, metrics["interaction_ratio"])
            self.assertNotIn("interaction_quality", json.dumps(metrics))
            self.assertFalse(any('classroom_behavior_profiles' in query.lower() for query in statements))
