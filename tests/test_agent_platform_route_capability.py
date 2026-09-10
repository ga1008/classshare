"""Generic route capability: real FastAPI dependencies, synthetic app and users.

Only JWT decoding and the DB connection factory are replaced. Route
classification, OpenAPI-derived validation, the durable request ledger, the
in-process identity binding and the human confirmation flow all run for real.
"""
import asyncio
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

from fastapi import APIRouter, Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from classroom_app.db.schema_agent_platform_requests import ensure_agent_platform_requests_schema
from classroom_app.dependencies import get_current_user
from classroom_app.services import agent_platform_request_service as service
from classroom_app.services import agent_platform_route_capability as routes
from classroom_app.services import agent_route_confirmation_service as confirmation
from classroom_app.services.agent_delegation_service import create_task_attempt, issue_task_delegation
from tests.test_agent_delegation_service import fixture_connection


class ItemBody(BaseModel):
    name: str
    count: int = 1


def build_app() -> FastAPI:
    app = FastAPI()
    api = APIRouter(prefix="/api/demo")

    @api.get("/items", summary="List demo items")
    def list_items(limit: int = 10, tag: str | None = None, user: dict = Depends(get_current_user)):
        return {"status": "ok", "actor": f"{user['role']}:{user['id']}", "limit": limit, "tag": tag, "channel": user.get("auth_channel")}

    @api.post("/items")
    def create_item(body: ItemBody, user: dict = Depends(get_current_user)):
        return {"status": "ok", "created": body.name, "count": body.count, "actor": user["id"]}

    @api.delete("/items/{item_id}")
    def delete_item(item_id: int, user: dict = Depends(get_current_user)):
        if user["id"] != 7 or user["role"] != "teacher":
            raise HTTPException(403, "not owner")
        return {"status": "ok", "deleted": item_id, "by": f"{user['role']}:{user['id']}", "channel": user.get("auth_channel")}

    @api.post("/items/{item_id}/publish")
    def publish_item(item_id: int, user: dict = Depends(get_current_user)):
        return {"status": "ok"}

    @api.post("/password")
    def change_password(user: dict = Depends(get_current_user)):
        return {"status": "ok"}

    @api.get("/hidden", include_in_schema=False)
    def hidden(user: dict = Depends(get_current_user)):
        return {"status": "ok"}

    @api.post("/upload")
    def upload(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
        return {"status": "ok"}

    @api.get("/follows")
    def plain_follows(user: dict = Depends(get_current_user)):
        return {"status": "ok"}

    app.include_router(api)

    @app.get("/manage/demo")
    def page(user: dict = Depends(get_current_user)):
        return HTMLResponse("<html></html>")

    @app.get("/api/demo/report", response_class=HTMLResponse)
    def html_report(user: dict = Depends(get_current_user)):
        return HTMLResponse("<html></html>")

    @app.get("/api/blog/follows")
    def reviewed_path(user: dict = Depends(get_current_user)):
        return {"status": "ok"}

    @app.get("/api/agent-tasks/demo")
    def control_plane(user: dict = Depends(get_current_user)):
        return {"status": "ok"}

    return app


class RouteCapabilityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = str(Path(temp.name) / "routes.sqlite")
        self.patched("classroom_app.services.organization_scope_service.get_configured_db_engine", return_value="sqlite")
        conn = fixture_connection(self.path)
        ensure_agent_platform_requests_schema(conn)
        self.tokens = {}
        for role, task_id, session in (("teacher", 10, "teacher-session"), ("student", 11, "student-session")):
            attempt = create_task_attempt(conn, task_id=task_id, worker_id="route-fixture", startup_key=role, lease_seconds=300)
            self.tokens[role] = issue_task_delegation(conn, task_id=task_id, attempt_id=attempt["id"], fencing_token=attempt["fencing_token"],
                purpose="tools", scopes=["platform:read", "platform:write"], source_session_id=session)["token"]
        conn.commit()
        conn.close()
        for target in ("classroom_app.database.get_db_connection", "classroom_app.dependencies.get_db_connection",
                       "classroom_app.services.agent_platform_request_service.get_db_connection"):
            self.patched(target, self.connection)
        self.patched("classroom_app.dependencies.verify_token", side_effect=lambda token, _ip: None)
        self.app = build_app()

    def patched(self, *args, **kwargs):
        item = patch(*args, **kwargs)
        result = item.start()
        self.addCleanup(item.stop)
        return result

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def key(self, method, path):
        return routes.route_key(method, path)

    def dispatch(self, role, key, operation_id=None, **kwargs):
        return asyncio.run(service.dispatch_platform_request(self.app, self.tokens[role], key, operation_id or str(uuid.uuid4()), **kwargs))

    def error(self, role, key, operation_id=None, **kwargs):
        with self.assertRaises(HTTPException) as caught:
            self.dispatch(role, key, operation_id, **kwargs)
        return caught.exception

    def test_inventory_exposes_only_machine_readable_business_routes_with_stable_keys(self):
        listed = {(item["method"], item["path"]): item for item in routes.route_capability_inventory(self.app, actor_role="teacher")}
        self.assertEqual({("GET", "/api/demo/items"), ("POST", "/api/demo/items"), ("DELETE", "/api/demo/items/{item_id}"),
                          ("POST", "/api/demo/items/{item_id}/publish"), ("GET", "/api/demo/follows")}, set(listed))
        self.assertEqual("route_ready", listed[("GET", "/api/demo/items")]["status"])
        self.assertEqual("read", listed[("GET", "/api/demo/items")]["risk"])
        self.assertEqual("write", listed[("POST", "/api/demo/items")]["risk"])
        for destructive in (("DELETE", "/api/demo/items/{item_id}"), ("POST", "/api/demo/items/{item_id}/publish")):
            self.assertEqual("route_confirmation_required", listed[destructive]["status"])
            self.assertFalse(listed[destructive]["executable"])
        self.assertEqual(listed[("GET", "/api/demo/items")]["key"], self.key("GET", "/api/demo/items"))
        reasons = {(row["method"], row["path"]): routes.classify_route(self.app, row).reason for row in routes._rows(self.app)}
        self.assertEqual("special_secure_input_required", reasons[("POST", "/api/demo/password")])
        self.assertEqual("outside_openapi_schema", reasons[("GET", "/api/demo/hidden")])
        self.assertEqual("form_or_multipart_requires_reviewed_adapter", reasons[("POST", "/api/demo/upload")])
        self.assertEqual("page_route_not_machine_readable", reasons[("GET", "/manage/demo")])
        self.assertEqual("non_json_response", reasons[("GET", "/api/demo/report")])
        self.assertEqual("reviewed_adapter_takes_precedence", reasons[("GET", "/api/blog/follows")])
        self.assertEqual("agent_control_plane", reasons[("GET", "/api/agent-tasks/demo")])
        details, unavailable = routes.route_capability_details(self.app, [self.key("GET", "/api/demo/items"), self.key("GET", "/manage/demo"), "route.nothing"])
        self.assertEqual(["limit", "tag"], sorted(details[0]["parameters"]))
        self.assertEqual([self.key("GET", "/manage/demo"), "route.nothing"], unavailable)

    def test_read_route_runs_as_the_task_owner_through_normal_dependencies(self):
        teacher = self.dispatch("teacher", self.key("GET", "/api/demo/items"), query_params={"limit": 3, "tag": "x"})
        self.assertEqual("observed_http_result", teacher["status"])
        self.assertEqual({"status": "ok", "actor": "teacher:7", "limit": 3, "tag": "x", "channel": "agent_platform_request"}, teacher["result"]["data"])
        self.assertFalse(teacher["verified_business"])
        self.assertFalse(teacher["mutates"])
        student = self.dispatch("student", self.key("GET", "/api/demo/items"))
        self.assertEqual("student:7", student["result"]["data"]["actor"])
        self.assertEqual(400, self.error("teacher", self.key("GET", "/api/demo/items"), query_params={"limit": "3"}).status_code)
        self.assertEqual(400, self.error("teacher", self.key("GET", "/api/demo/items"), query_params={"Authorization": "x"}).status_code)
        self.assertEqual(400, self.error("teacher", self.key("GET", "/api/demo/items"), body={"unexpected": 1}).status_code)

    def test_write_route_is_ledgered_idempotent_and_validated_by_the_route_itself(self):
        key, operation_id = self.key("POST", "/api/demo/items"), str(uuid.uuid4())
        first = self.dispatch("teacher", key, operation_id, body={"name": "A", "count": 2})
        self.assertEqual("observed_http_result", first["status"])
        self.assertTrue(first["mutates"])
        self.assertEqual({"status": "ok", "created": "A", "count": 2, "actor": 7}, first["result"]["data"])
        replay = self.dispatch("teacher", key, operation_id, body={"name": "A", "count": 2})
        self.assertEqual(first["request_id"], replay["request_id"])
        self.assertEqual(409, self.error("teacher", key, operation_id, body={"name": "B"}).status_code)
        invalid = self.dispatch("teacher", key, body={"name": "C", "count": "many"})
        self.assertEqual("uncertain", invalid["status"])
        self.assertEqual(422, invalid["result"]["http_status"])
        self.assertEqual(400, self.error("teacher", key).status_code)
        with self.connection() as conn:
            rows = conn.execute("SELECT capability_key, method, mutates, route_source_sha256 FROM agent_platform_requests").fetchall()
        self.assertTrue(rows)
        self.assertTrue(all(row["capability_key"] == key and row["method"] == "POST" and row["mutates"] == 1
                            and len(row["route_source_sha256"]) == 64 for row in rows))

    def test_destructive_blocked_and_reviewed_routes_are_refused_with_guidance(self):
        destructive = self.error("teacher", self.key("DELETE", "/api/demo/items/{item_id}"), path_params={"item_id": 5})
        self.assertEqual(403, destructive.status_code)
        self.assertEqual("platform_route_request", destructive.detail["proposal_action"])
        self.assertEqual(403, self.error("teacher", self.key("POST", "/api/demo/password")).status_code)
        self.assertEqual(403, self.error("teacher", self.key("GET", "/manage/demo")).status_code)
        reviewed = self.error("teacher", self.key("GET", "/api/blog/follows"))
        self.assertEqual(409, reviewed.status_code)
        self.assertEqual(["blog.follows", "http.blog.follows.list"], reviewed.detail["use_capability_keys"])
        self.assertEqual(404, self.error("teacher", "route.xyz").status_code)
        with self.connection() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM agent_platform_requests").fetchone()[0])

    def test_user_confirmation_executes_destructive_route_as_the_confirming_user_once(self):
        user = {"id": 7, "role": "teacher", "session_id": "teacher-session"}
        params = {"capability_key": self.key("DELETE", "/api/demo/items/{item_id}"), "path_params": {"item_id": 9}}
        with self.connection() as conn:
            conn.execute("UPDATE agent_tasks SET status='completed' WHERE id IN (10, 11)")
            conn.commit()
            prepared = confirmation.prepare_user_confirmation(conn, action="platform_route_request", params=params, user=user, app=self.app)
        review = prepared["review"]
        self.assertEqual(("DELETE", "/api/demo/items/9", "destructive", True), (review["method"], review["path"], review["risk"], review["can_execute"]))
        self.assertEqual(["destructive_route"], [item["code"] for item in review["warnings"]])
        confirmed = prepared["params"]
        good_inputs = {"accepted_warning_codes": ["destructive_route"], "confirmation_note": "checked"}

        def run(params_used, inputs, *, actor=user, session="teacher-session", task_id=10, operation_id="proposal:10:0"):
            with self.connection() as conn:
                result = confirmation.dispatch_user_confirmation(conn, user=actor, source_session_id=session, task_id=task_id,
                    operation_id=operation_id, action="platform_route_request", params=params_used, confirmation_inputs=inputs, app=self.app)
                conn.commit()
                return result

        with self.assertRaises(HTTPException) as stale:
            run({**confirmed, "expected_review_hash": "0" * 64}, good_inputs)
        self.assertEqual(409, stale.exception.status_code)
        with self.assertRaises(HTTPException) as unaccepted:
            run(confirmed, {"accepted_warning_codes": [], "confirmation_note": "checked"})
        self.assertEqual(400, unaccepted.exception.status_code)
        with self.assertRaises(HTTPException) as other_session:
            run(confirmed, good_inputs, actor={**user, "session_id": "wrong"}, session="wrong")
        self.assertEqual(401, other_session.exception.status_code)
        outcome = run(confirmed, good_inputs)
        self.assertFalse(outcome["replayed"])
        self.assertEqual({"status": "ok", "deleted": 9, "by": "teacher:7", "channel": "agent_user_confirmation"}, outcome["result"]["observation"]["data"])
        self.assertEqual(200, outcome["result"]["observation"]["http_status"])
        self.assertFalse(outcome["result"]["verified_business"])
        replay = run(confirmed, good_inputs)
        self.assertTrue(replay["replayed"])
        self.assertEqual(outcome["result"], replay["result"])
        with self.connection() as conn:
            rows = conn.execute("SELECT status, source_kind, action FROM agent_action_executions").fetchall()
        self.assertEqual([("completed", "user_confirmation", "platform_route_request")], [tuple(row) for row in rows])
        # A student confirming the same route is stopped by the route's own ownership rule.
        student = {"id": 7, "role": "student", "session_id": "student-session"}
        with self.connection() as conn:
            student_params = confirmation.prepare_user_confirmation(conn, action="platform_route_request", params=params, user=student, app=self.app)["params"]
        denied = run(student_params, good_inputs, actor=student, session="student-session", task_id=11, operation_id="proposal:11:0")
        self.assertEqual(403, denied["result"]["observation"]["http_status"])


if __name__ == "__main__":
    unittest.main()
