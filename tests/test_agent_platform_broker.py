"""Real SQL + real normal FastAPI handlers, without starting the main app."""
import asyncio
from contextlib import contextmanager
from contextvars import copy_context
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from classroom_app import dependencies
from classroom_app.routers import global_search, message_center
from classroom_app.services import agent_platform_broker as broker
from classroom_app.services.agent_actor_service import resolve_agent_actor
from classroom_app.services.agent_delegation_service import create_task_attempt, issue_task_delegation
from classroom_app.services.agent_platform_registry import build_platform_route_inventory, platform_read_catalog
from classroom_app.services.agent_request_context import _BrokerIdentity, _broker_identity, current_agent_broker_user
from tests.test_agent_delegation_service import fixture_connection


class AgentPlatformBrokerTests(unittest.TestCase):
    def test_normal_sensitive_management_remains_reviewable_without_granting_execution(self):
        app = FastAPI()
        for path in ("/api/manage/password", "/api/materials/1/repository/credentials", "/api/manage/agent/settings", "/api/manage/system_monitor", "/login", "/api/agent-bridge/tools", "/api/agent-tasks"):
            app.add_api_route(path, lambda: {}, methods=["POST"])
        inventory = {item["path"]: item for item in build_platform_route_inventory(app)}
        for path in ("/api/manage/password", "/api/materials/1/repository/credentials"):
            self.assertEqual(("needs_adapter", "special_secure_input_required"), (inventory[path]["status"], inventory[path]["reason"]))
        for path in ("/api/manage/agent/settings", "/api/manage/system_monitor"):
            self.assertEqual(("needs_adapter", "control_plane_review_required"), (inventory[path]["status"], inventory[path]["reason"]))
        for path in ("/login", "/api/agent-bridge/tools", "/api/agent-tasks"):
            self.assertEqual("excluded", inventory[path]["status"])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = str(Path(self.directory.name) / "broker.sqlite")
        conn = fixture_connection(self.path)
        conn.executescript("""
            CREATE TABLE classes(id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE courses(id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE class_offerings(id INTEGER PRIMARY KEY, teacher_id INTEGER, class_id INTEGER, course_id INTEGER, semester_id INTEGER, semester TEXT);
            CREATE TABLE class_offering_class_links(offering_id INTEGER,class_id INTEGER);
            CREATE TABLE course_materials(id INTEGER PRIMARY KEY,name TEXT,node_type TEXT);
            CREATE TABLE course_material_assignments(material_id INTEGER,class_offering_id INTEGER);
            CREATE TABLE assignments(id INTEGER PRIMARY KEY,title TEXT,course_id INTEGER,class_offering_id INTEGER,status TEXT,exam_paper_id INTEGER,
                assessment_kind TEXT,assessment_kind_version INTEGER,assessment_kind_source TEXT,created_at TEXT);
            CREATE TABLE learning_stage_exam_attempts(assignment_id INTEGER);
            CREATE TABLE blog_posts(id INTEGER PRIMARY KEY,title TEXT,author_display_name TEXT,summary TEXT,status TEXT,visibility TEXT,created_at TEXT);
            CREATE TABLE message_center_notifications(id INTEGER PRIMARY KEY,recipient_identity TEXT,recipient_role TEXT,recipient_user_pk INTEGER,
                category TEXT,severity TEXT,actor_identity TEXT,actor_role TEXT,actor_user_pk INTEGER,actor_display_name TEXT,
                title TEXT,body_preview TEXT,link_url TEXT,class_offering_id INTEGER,ref_type TEXT,ref_id TEXT,
                metadata_json TEXT,read_at TEXT,created_at TEXT);
            INSERT INTO classes VALUES(30,'Class A'),(31,'Class B');
            INSERT INTO courses VALUES(1,'Networks A'),(2,'Networks B');
            INSERT INTO class_offerings VALUES(1,7,30,1,1,'Term'),(2,7,31,2,1,'Term');
            INSERT INTO course_materials VALUES(1,'Networks own material','file'),(2,'Networks other class material','file');
            INSERT INTO course_material_assignments VALUES(1,1),(2,2);
            INSERT INTO assignments VALUES(1,'Networks published',1,1,'published',NULL,'assignment',1,'teacher','now'),
                (2,'Networks draft',1,1,'new',NULL,'assignment',1,'teacher','now');
            INSERT INTO message_center_notifications VALUES(1,'teacher:7','teacher',7,'agent_task','normal','','',NULL,'Agent',
                'Teacher secret','Teacher private result','/dashboard',NULL,'agent','1','{}',NULL,'now');
            INSERT INTO message_center_notifications VALUES(2,'student:7','student',7,'agent_task','normal','','',NULL,'Agent',
                'Student secret','Student private result','/dashboard',NULL,'agent','2','{}',NULL,'now');
        """)
        self.tokens = {}
        for role, task_id in (("teacher", 10), ("student", 11)):
            attempt = create_task_attempt(conn, task_id=task_id, worker_id="broker-test", startup_key=f"test-{task_id}", lease_seconds=300)
            self.tokens[role] = issue_task_delegation(conn, task_id=task_id, attempt_id=attempt["id"], fencing_token=attempt["fencing_token"],
                                                     purpose="tools", scopes=["platform:read"], source_session_id=role + "-session")["token"]
        conn.commit()
        conn.close()
        for target in ("classroom_app.database.get_db_connection", "classroom_app.dependencies.get_db_connection",
                       "classroom_app.services.agent_platform_broker.get_db_connection",
                       "classroom_app.routers.global_search.get_db_connection", "classroom_app.routers.message_center.get_db_connection"):
            item = patch(target, self.connection)
            item.start()
            self.addCleanup(item.stop)
        engine = patch("classroom_app.services.organization_scope_service.get_configured_db_engine", return_value="sqlite")
        engine.start()
        self.addCleanup(engine.stop)
        verifier = patch.object(dependencies, "verify_token", side_effect=lambda token, _ip: {"role": token, "id": 7, "name": token} if token in self.tokens else None)
        verifier.start()
        self.addCleanup(verifier.stop)
        self.app = FastAPI()
        self.app.include_router(global_search.router)
        self.app.include_router(message_center.router)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def read(self, role, operation_key, **kwargs):
        return asyncio.run(broker.dispatch_read(self.app, self.tokens[role], operation_key, **kwargs))

    def test_timeout_and_caller_cancel_hold_capacity_until_actual_asgi_thread_finishes(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                started, release = threading.Event(), threading.Event()
                identities = []
                capacity = threading.BoundedSemaphore(1)

                def delayed_handler():
                    identities.append(_broker_identity.get())
                    started.set()
                    release.wait(timeout=2)
                    return {"status": "success"}

                delayed_handler.__module__ = "classroom_app.routers.message_center"
                delayed_handler.__qualname__ = "api_message_center_items"
                app = FastAPI()
                app.get("/api/message-center/items")(delayed_handler)

                async def exercise():
                    call = asyncio.create_task(broker.dispatch_read(app, self.tokens["teacher"], "messages.items"))
                    self.assertTrue(await asyncio.to_thread(started.wait, 1))
                    if cancel:
                        call.cancel()
                    await asyncio.sleep(0.05)
                    self.assertFalse(call.done())
                    self.assertFalse(capacity.acquire(blocking=False))
                    self.assertTrue(identities[0].active)
                    release.set()
                    if cancel:
                        with self.assertRaises(asyncio.CancelledError):
                            await call
                    else:
                        with self.assertRaises(HTTPException) as timeout:
                            await call
                        self.assertEqual(504, timeout.exception.status_code)
                    self.assertFalse(identities[0].active)
                    self.assertTrue(capacity.acquire(blocking=False))
                    capacity.release()

                with patch.object(broker, "_READ_CAPACITY", capacity), patch.object(broker, "MAX_READ_SECONDS", 0.02):
                    asyncio.run(exercise())

    def web_read(self, role, path, params=None):
        self.client.cookies.set("access_token", role)
        return self.client.get(path, params=params)

    def test_teacher_student_search_matches_web_resource_visibility(self):
        for role in ("teacher", "student"):
            normal = self.web_read(role, "/api/global-search", {"q": "Networks"})
            delegated = self.read(role, "search.everything", query_params={"q": "Networks"})
            self.assertEqual(200, normal.status_code)
            self.assertEqual(normal.json(), delegated["data"])
            text = json.dumps(delegated)
            self.assertEqual(role == "teacher", "Networks other class material" in text)
            self.assertEqual(role == "teacher", "Networks draft" in text)

    def test_same_numbered_users_message_results_match_web_without_marking_read(self):
        for role in ("teacher", "student"):
            normal = self.web_read(role, "/api/message-center/items", {"limit": 20})
            delegated = self.read(role, "messages.items", query_params={"limit": 20})
            self.assertEqual(200, normal.status_code)
            self.assertEqual(normal.json(), delegated["data"])
            self.assertEqual([role], [item["recipient_role"] for item in delegated["data"]["items"]])
        with self.connection() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM message_center_notifications WHERE read_at IS NOT NULL").fetchone()[0])

    def test_every_dispatch_rechecks_live_session_task_scope_and_role(self):
        self.read("student", "messages.items")
        with self.connection() as conn:
            conn.execute("UPDATE user_sessions SET session_id='replacement' WHERE session_user_key='student:7'")
            conn.commit()
        with self.assertRaises(HTTPException) as error:
            self.read("student", "messages.items")
        self.assertEqual(401, error.exception.status_code)
        self.read("teacher", "messages.items")
        with self.connection() as conn:
            conn.execute("UPDATE agent_task_delegations SET scopes_json='[\"resources.read\"]' WHERE actor_role='teacher'")
            conn.commit()
        with self.assertRaises(HTTPException) as error:
            self.read("teacher", "messages.items")
        self.assertEqual(403, error.exception.status_code)

    def test_url_path_identity_query_and_unreviewed_get_are_not_executable(self):
        for key, kwargs in (("https://example.com", {}), ("/api/message-center/private/conversation", {}),
                            ("search.everything", {"query_params": {"q": "Networks", "teacher_id": 8}}),
                            ("classroom.contacts", {"path_params": {"class_offering_id": "../internal/health"}}),
                            ("messages.items", {"query_params": {"limit": True}}),
                            ("assignment.assessment_kind", {"path_params": {"assignment_id": 1}})):
            with self.subTest(key=key), self.assertRaises(HTTPException):
                self.read("student", key, **kwargs)

    def test_catalog_does_not_promote_unreviewed_routes_or_side_effect_get(self):
        inventory = build_platform_route_inventory(self.app)
        side_effect = next(item for item in inventory if item["path"] == "/api/message-center/private/conversation")
        self.assertEqual(("needs_adapter", "side_effect_get"), (side_effect["status"], side_effect["reason"]))
        catalog = platform_read_catalog(self.app, actor_role="student")
        self.assertEqual(len(inventory), catalog["mounted_operation_count"])
        self.assertEqual("partial_reviewed_reads", catalog["coverage_status"])
        self.assertNotIn("assignment.assessment_kind", [item["key"] for item in catalog["operations"]])

    def test_external_headers_cannot_inject_process_local_identity(self):
        self.client.cookies.clear()
        response = self.client.get("/api/message-center/items", headers={"x-agent-actor-role": "teacher", "x-agent-actor-id": "7"})
        self.assertEqual(401, response.status_code)
        self.read("teacher", "messages.items")
        self.assertIsNone(_broker_identity.get())

    def test_inherited_context_is_inactive_after_invocation_and_nonce_is_required(self):
        with self.connection() as conn:
            actor = resolve_agent_actor(conn, "teacher", 7)
        identity = _BrokerIdentity("teacher", 7, actor.authority_fingerprint, 10, "attempt", 1, "/api/message-center/items", object())
        token = _broker_identity.set(identity)
        request = Request({"type": "http", "path": identity.path, "method": "GET", "headers": []})
        try:
            with self.assertRaises(HTTPException):
                current_agent_broker_user(request)
            inherited = copy_context()
        finally:
            identity.active = False
            _broker_identity.reset(token)
        with self.assertRaises(HTTPException):
            inherited.run(current_agent_broker_user, request)

    def test_parallel_actors_keep_separate_contexts(self):
        async def parallel():
            return await asyncio.gather(*(broker.dispatch_read(self.app, self.tokens[role], "messages.items") for role in ("teacher", "student")))
        teacher, student = asyncio.run(parallel())
        self.assertEqual("teacher", teacher["data"]["items"][0]["recipient_role"])
        self.assertEqual("student", student["data"]["items"][0]["recipient_role"])
        self.assertIsNone(_broker_identity.get())

    def test_response_limit_fails_closed_and_releases_identity(self):
        with patch.object(broker, "MAX_RESPONSE_BYTES", 10):
            with self.assertRaises(HTTPException) as error:
                self.read("teacher", "messages.items")
        self.assertEqual(502, error.exception.status_code)
        self.assertIsNone(_broker_identity.get())
        self.assertEqual(200, self.read("teacher", "messages.items")["status_code"])


if __name__ == "__main__":
    unittest.main()
