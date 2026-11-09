"""Service principals traverse real auth and ordinary owner/version policies."""
import asyncio
import uuid

from fastapi import Depends, FastAPI, HTTPException, File, Form, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from classroom_app import database
from classroom_app.dependencies import get_current_user, get_current_teacher
from classroom_app.services.agent_platform_route_capability import route_key
from classroom_app.services import wanderer_platform_service as platform
from classroom_app.services.wanderer_runtime_service import enqueue_wake_event, _claim
from tests.wanderer_test_support import WandererDbTestCase


class ItemBody(BaseModel):
    name: str
    expected_version: int | None = None


def fixture_app():
    app = FastAPI()

    @app.get("/api/typed-items/{item_id:int}")
    def typed_item(item_id: int, user=Depends(get_current_user)):
        return {"status": "ok", "id": item_id}

    @app.post("/api/synthetic-teacher-only")
    def teacher_only(user=Depends(get_current_teacher)):
        return {"status": "ok"}

    @app.post("/api/ai/chat")
    def synthetic_stream(fail: bool = False, user=Depends(get_current_user)):
        import json
        events = [{"event": "thinking_delta", "delta": "internal reasoning"}, {"event": "answer_delta", "delta": "synthetic answer"}]
        if fail: events.append({"event": "error", "message": "synthetic error"})
        events.append({"event": "done"})
        return StreamingResponse(iter([json.dumps(item)+"\n" for item in events]), media_type="application/x-ndjson")

    @app.post("/api/owned-items/upload")
    async def upload_item(file: UploadFile = File(...), caption: str = Form(...), user=Depends(get_current_user)):
        return {"status": "ok", "filename": file.filename, "content": (await file.read()).decode(), "caption": caption, "owner": user["id"]}

    @app.post("/api/resume/personal/suggest")
    async def synthetic_job(user=Depends(get_current_user)):
        return JSONResponse({"ok": True, "job": {"id": 77, "task_type": "resume_suggestion", "status": "queued"}}, status_code=202)

    @app.get("/api/resume/suggestions/jobs/{job_id}")
    async def synthetic_job_result(job_id: int, user=Depends(get_current_user)):
        return {"ok": True, "job": {"id": job_id, "task_type": "resume_suggestion", "status": "succeeded"}, "result": {"ok": True, "content": "completed"}}

    @app.post("/api/owned-items")
    def create_item(body: ItemBody, user=Depends(get_current_user)):
        with database.get_db_connection() as conn:
            row = conn.execute("INSERT INTO synthetic_owned_items(owner_id,name,version) VALUES(?,?,1) RETURNING id", (int(user["id"]), body.name)).fetchone()
            conn.commit()
            return {"status": "ok", "id": row["id"], "owner": user["id"], "auth_channel": user.get("auth_channel")}

    @app.get("/api/owned-items/{item_id}")
    def read_item(item_id: int, user=Depends(get_current_user)):
        with database.get_db_connection() as conn:
            row = conn.execute("SELECT * FROM synthetic_owned_items WHERE id=?", (item_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "missing")
            if int(row["owner_id"]) != int(user["id"]):
                raise HTTPException(403, "not owner")
            return {"status": "ok", **dict(row)}

    @app.put("/api/owned-items/{item_id}")
    def update_item(item_id: int, body: ItemBody, user=Depends(get_current_user)):
        with database.get_db_connection() as conn:
            row = conn.execute("SELECT * FROM synthetic_owned_items WHERE id=?", (item_id,)).fetchone()
            if row is None or int(row["owner_id"]) != int(user["id"]):
                raise HTTPException(403, "not owner")
            if int(row["version"]) != body.expected_version:
                raise HTTPException(409, "version conflict")
            conn.execute("UPDATE synthetic_owned_items SET name=?,version=version+1 WHERE id=?", (body.name, item_id))
            conn.commit()
            return {"status": "ok", "id": item_id, "name": body.name}

    @app.delete("/api/owned-items/{item_id}")
    def delete_item(item_id: int, user=Depends(get_current_user)):
        with database.get_db_connection() as conn:
            row = conn.execute("SELECT owner_id FROM synthetic_owned_items WHERE id=?", (item_id,)).fetchone()
            if row is None or int(row["owner_id"]) != int(user["id"]):
                raise HTTPException(403, "not owner")
            conn.execute("DELETE FROM synthetic_owned_items WHERE id=?", (item_id,))
            conn.commit()
            return {"status": "ok", "deleted": item_id}

    @app.post("/api/owned-items-uncertain")
    def committed_without_response(body: ItemBody, user=Depends(get_current_user)):
        with database.get_db_connection() as conn:
            conn.execute("INSERT INTO synthetic_owned_items(owner_id,name,version) VALUES(?,?,1)", (int(user["id"]), body.name))
            conn.commit()
        raise RuntimeError("Synthetic lost response after commit")

    return app


class WandererPlatformExecutionTest(WandererDbTestCase):
    def setUp(self):
        super().setUp()
        self.app = fixture_app()
        with self.connect() as conn:
            self.wid = conn.execute("SELECT id FROM wanderer_agents ORDER BY id LIMIT 1").fetchone()[0]
            conn.execute("UPDATE wanderer_agents SET status='active' WHERE id=?", (self.wid,))
            conn.execute("CREATE TABLE IF NOT EXISTS synthetic_owned_items(id INTEGER PRIMARY KEY AUTOINCREMENT,owner_id INTEGER,name TEXT,version INTEGER)")
            conn.execute("DELETE FROM synthetic_owned_items")
            conn.execute("DELETE FROM wanderer_runtime_leases WHERE wanderer_id=?", (self.wid,))
            event = enqueue_wake_event(conn, self.wid, "fixture", uuid.uuid4().hex)
            self.run = _claim(conn, event["event_id"])
            conn.commit()
        self.authority = {key: self.run[key] for key in ("turn_id", "life_epoch", "fencing_token", "authority_version")}
        self.authority["wanderer_id"] = self.wid

    def execute(self, method, path, *, args=None, operation_id=None):
        return asyncio.run(platform.execute_platform_command(**self.authority, app=self.app,
            conn_factory=self.connect, operation_id=operation_id or str(uuid.uuid4()),
            command={"id": "fixture", "capability": route_key(method, path), "args": args or {}}))

    def test_full_crud_uses_bound_student_identity_and_no_human_session(self):
        key = str(uuid.uuid4())
        first = self.execute("POST", "/api/owned-items", args={"body": {"name": "created"}}, operation_id=key)
        again = self.execute("POST", "/api/owned-items", args={"body": {"name": "created"}}, operation_id=key)
        self.assertEqual(first["request_id"], again["request_id"])
        data = first["result"]["data"]
        self.assertEqual(data["auth_channel"], "wanderer_service")
        item_id = data["id"]
        read = self.execute("GET", "/api/owned-items/{item_id}", args={"path_params": {"item_id": item_id}})
        self.assertEqual(read["result"]["data"]["name"], "created")
        updated = self.execute("PUT", "/api/owned-items/{item_id}", args={"path_params": {"item_id": item_id}, "body": {"name": "changed", "expected_version": 1}})
        self.assertEqual(updated["result"]["http_status"], 200)
        deleted = self.execute("DELETE", "/api/owned-items/{item_id}", args={"path_params": {"item_id": item_id},
            "safety_check": {"confirmed": True, "reason": "删除本人不再需要的合成记录", "target_count": 1}})
        self.assertEqual(deleted["result"]["data"]["deleted"], item_id)
        with self.connect() as conn:
            receipt = conn.execute("SELECT * FROM agent_platform_requests WHERE operation_id=?", (key,)).fetchone()
            self.assertEqual(receipt["principal_kind"], "wanderer_service")
            for field in ("task_id", "attempt_id", "delegation_id", "source_session_hash"):
                self.assertIsNone(receipt[field])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM user_sessions WHERE role='student' AND user_id=?", (str(data["owner"]),)).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM synthetic_owned_items").fetchone()[0], 0)

    def test_ownership_conflict_and_self_confirmation_are_not_bypassed(self):
        with self.connect() as conn:
            conn.execute("INSERT INTO synthetic_owned_items(owner_id,name,version) VALUES(999999,'not mine',1)")
            other = conn.execute("SELECT id FROM synthetic_owned_items").fetchone()[0]
            conn.commit()
        denied = self.execute("GET", "/api/owned-items/{item_id}", args={"path_params": {"item_id": other}})
        self.assertEqual(denied["result"]["http_status"], 403)
        with self.assertRaises(HTTPException) as exc:
            self.execute("DELETE", "/api/owned-items/{item_id}", args={"path_params": {"item_id": other},
                "safety_check": {"user_confirmed": True, "question_id": "forged"}})
        self.assertEqual(exc.exception.status_code, 400)
        created = self.execute("POST", "/api/owned-items", args={"body": {"name": "mine"}})
        item_id = created["result"]["data"]["id"]
        conflict = self.execute("PUT", "/api/owned-items/{item_id}", args={"path_params": {"item_id": item_id}, "body": {"name": "changed", "expected_version": 8}})
        self.assertEqual(conflict["result"]["http_status"], 409)

    def test_stale_lifecycle_and_cross_turn_operation_replay_are_denied(self):
        op = str(uuid.uuid4())
        self.execute("POST", "/api/owned-items", args={"body": {"name": "one"}}, operation_id=op)
        with self.connect() as conn:
            conn.execute("DELETE FROM wanderer_runtime_leases WHERE wanderer_id=?", (self.wid,))
            event = enqueue_wake_event(conn, self.wid, "fixture", uuid.uuid4().hex)
            newer = _claim(conn, event["event_id"])
            conn.commit()
        self.authority.update({key: newer[key] for key in ("turn_id", "life_epoch", "fencing_token", "authority_version")})
        with self.assertRaises(HTTPException) as exc:
            self.execute("POST", "/api/owned-items", args={"body": {"name": "one"}}, operation_id=op)
        self.assertEqual(exc.exception.status_code, 409)
        with self.connect() as conn:
            conn.execute("UPDATE wanderer_agents SET authority_version=authority_version+1 WHERE id=?", (self.wid,))
            conn.commit()
        with self.assertRaises((HTTPException, PermissionError)):
            self.execute("POST", "/api/owned-items", args={"body": {"name": "stale"}})
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM synthetic_owned_items").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT state FROM wanderer_resource_reservations WHERE operation_id=?", (f"platform-action:{self.wid}:{op}:active_interactions",)).fetchone()[0], "consumed")

    def test_lost_response_cannot_duplicate_a_committed_mutation(self):
        op = str(uuid.uuid4())
        args = {"body": {"name": "committed"}}
        with self.assertRaises(RuntimeError):
            self.execute("POST", "/api/owned-items-uncertain", args=args, operation_id=op)
        receipt = self.execute("POST", "/api/owned-items-uncertain", args=args, operation_id=op)
        self.assertEqual(receipt["status"], "uncertain")
        with self.assertRaises(HTTPException) as exc:
            self.execute("POST", "/api/owned-items-uncertain", args=args)
        self.assertEqual(exc.exception.status_code, 409)
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM synthetic_owned_items").fetchone()[0], 1)

            self.assertEqual(conn.execute("SELECT state FROM wanderer_resource_reservations WHERE operation_id=?", (f"platform-action:{self.wid}:{op}:active_interactions",)).fetchone()[0], "reserved")

    def test_catalog_exposes_current_schema_and_role_cannot_be_supplied(self):
        with self.connect() as conn:
            catalog = platform.list_wanderer_capabilities(conn, self.run["wanderer"], app=self.app)
        self.assertTrue(any(item["method"] == "DELETE" and item["requires_self_confirmation"] for item in catalog))
        with self.assertRaises(HTTPException):
            self.execute("POST", "/api/owned-items", args={"actor_id": 999, "body": {"name": "forged"}})

    def test_native_multipart_uses_authored_bytes_and_forbids_server_paths(self):
        op = str(uuid.uuid4())
        args = {"body": {"caption": "native form"}, "files": [{"filename": "note.txt", "content_text": "authored"}]}
        uploaded = self.execute("POST", "/api/owned-items/upload", args=args, operation_id=op)
        replayed = self.execute("POST", "/api/owned-items/upload", args=args, operation_id=op)
        self.assertEqual(uploaded["status"], "succeeded")
        self.assertEqual(uploaded["result"]["data"]["content"], "authored")
        self.assertEqual(uploaded["request_id"], replayed["request_id"])
        with self.assertRaises(HTTPException):
            self.execute("POST", "/api/owned-items/upload", args={"body": {"caption": "bad"}, "files": [{"path": "server.env"}]})

    def test_accepted_job_has_readonly_tracker_and_exact_terminal_identity(self):
        from classroom_app.services.wanderer_platform_results import interpret_platform_job_result
        accepted = self.execute("POST", "/api/resume/personal/suggest")
        self.assertEqual(accepted["status"], "submitted")
        follow = accepted["follow_up"]
        result = asyncio.run(platform.execute_platform_command(**self.authority, app=self.app, conn_factory=self.connect,
            operation_id=str(uuid.uuid4()), command={"id": "poll", **follow}))
        self.assertFalse(result["mutates"])
        self.assertEqual(interpret_platform_job_result(result, accepted["job_identity"])["status"], "succeeded")
        self.assertEqual(interpret_platform_job_result(result, {"id": 78})["status"], "uncertain")

    def test_typed_path_and_role_catalog_use_original_declarations(self):
        read = self.execute("GET", "/api/typed-items/{item_id:int}", args={"path_params": {"item_id": 42}})
        self.assertEqual(read["result"]["data"]["id"], 42)
        with self.connect() as conn:
            catalog = platform.list_wanderer_capabilities(conn, self.run["wanderer"], app=self.app)
        self.assertFalse(any(item.get("path") == "/api/synthetic-teacher-only" for item in catalog))
        with self.assertRaises(HTTPException) as exc:
            self.execute("POST", "/api/synthetic-teacher-only")
        self.assertEqual(exc.exception.status_code, 403)

    def test_native_stream_terminal_and_error_are_observed_without_reasoning_text(self):
        success = self.execute("POST", "/api/ai/chat")
        self.assertEqual(success["status"], "succeeded")
        self.assertEqual(success["result"]["data"]["answer"], "synthetic answer")
        self.assertNotIn("internal reasoning", str(success))
        failed = self.execute("POST", "/api/ai/chat", args={"query_params": {"fail": True}})
        self.assertEqual(failed["status"], "failed")

    def test_native_transaction_guard_rejects_revocation_after_dependency_auth(self):
        from classroom_app.db.service_execution_guard import suspend_service_guard
        @self.app.post("/api/owned-items-revocation")
        def revoke_before_native_write(user=Depends(get_current_user)):
            # A trusted administrative transaction commits after the normal
            # dependency has authenticated this route, before its business write.
            with suspend_service_guard(), self.connect() as conn:
                conn.execute("UPDATE wanderer_agents SET authority_version=authority_version+1 WHERE id=?", (self.wid,))
                conn.commit()
            with self.connect() as conn:
                conn.execute("INSERT INTO synthetic_owned_items(owner_id,name,version) VALUES(?,?,1)", (int(user["id"]), "must rollback"))
                conn.commit()
            return {"status": "ok"}
        operation = str(uuid.uuid4())
        with self.assertRaises(HTTPException) as denied:
            self.execute("POST", "/api/owned-items-revocation", operation_id=operation,
                         args={"safety_check": {"confirmed": True, "reason": "验证合成事务权限隔离", "target_count": 1}})
        self.assertTrue(denied.exception.detail["requires_reconciliation"])
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM synthetic_owned_items").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT state FROM wanderer_resource_reservations WHERE operation_id=?",
                (f"platform-action:{self.wid}:{operation}:active_interactions",)).fetchone()[0], "released")
            row = conn.execute("SELECT * FROM agent_platform_requests WHERE operation_id=?", (operation,)).fetchone()
            self.assertIsNotNone(row["host_execution_finished_at"])

    def test_commit_guard_rolls_back_native_write_on_changed_authority(self):
        @self.app.post("/api/owned-items-commit-fence")
        def revoke_inside_native_transaction(user=Depends(get_current_user)):
            with self.connect() as conn:
                conn.cursor().execute("INSERT INTO synthetic_owned_items(owner_id,name,version) VALUES(?,?,1)", (int(user["id"]), "must rollback"))
                conn.execute("UPDATE wanderer_agents SET authority_version=authority_version+1 WHERE id=?", (self.wid,))
                conn.commit()
            return {"status": "ok"}
        result = self.execute("POST", "/api/owned-items-commit-fence",
                              args={"safety_check": {"confirmed": True, "reason": "验证合成事务提交回滚", "target_count": 1}})
        self.assertEqual(result["status"], "failed")
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM synthetic_owned_items").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT authority_version FROM wanderer_agents WHERE id=?", (self.wid,)).fetchone()[0], self.authority["authority_version"])
