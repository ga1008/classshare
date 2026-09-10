from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import tempfile
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import BackgroundTasks, HTTPException, FastAPI
from fastapi.testclient import TestClient

from classroom_app.db import schema_agent_ext, schema_scheduler, schema_session_learning_materials
from classroom_app.db.schema_agent_authority import ensure_agent_authority_schema
from classroom_app.db.schema_assignments import ensure_assignment_schema
from classroom_app.db.schema_classroom_activity import ensure_classroom_activity_schema
from classroom_app.db.schema_foundation import ensure_foundation_schema
from classroom_app.db.schema_learning_blog import ensure_learning_blog_signature_schema
from classroom_app.db.schema_materials_integrations import ensure_materials_integrations_schema
from classroom_app.db.schema_offering_class_links import ensure_offering_class_links_schema
from classroom_app.db.migrations import _ensure_organization_catalog_schema
from classroom_app import dependencies
from classroom_app.routers.manage_parts import base_resource_modes, system_config
from classroom_app.services import agent_platform_broker as broker
from classroom_app.routers import agent_tasks, blog
from classroom_app.services import agent_platform_write_service as writes
from classroom_app.services import blog_ai_service, blog_effects_service as effects, blog_service
from classroom_app.services import session_material_generation_jobs as session_jobs, session_material_generation_service as generation
from classroom_app.services.agent_delegation_service import create_task_attempt, issue_task_delegation


class Request:
    def __init__(self, data):
        self.data = data

    async def json(self):
        return self.data


class PlatformWriteFixture(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        for name in ("classroom_app.services.organization_scope_service.get_configured_db_engine",
                     "classroom_app.db.schema_agent_ext.get_configured_db_engine",
                     "classroom_app.db.schema_scheduler.get_configured_db_engine"):
            guard = patch(name, return_value="sqlite")
            guard.start()
            self.addCleanup(guard.stop)
        guard = patch.object(schema_scheduler, "_SCHEMA_READY", False)
        guard.start()
        self.addCleanup(guard.stop)
        ensure_foundation_schema(self.conn)
        _ensure_organization_catalog_schema(self.conn)
        ensure_classroom_activity_schema(self.conn)
        ensure_assignment_schema(self.conn)
        ensure_materials_integrations_schema(self.conn)
        ensure_offering_class_links_schema(self.conn, force=True)
        ensure_learning_blog_signature_schema(self.conn)
        schema_agent_ext.ensure_agent_task_extension_schema(self.conn, force=True)
        ensure_agent_authority_schema(self.conn)
        schema_scheduler.ensure_scheduler_schema(self.conn)
        for pk in (7, 8):
            self.conn.execute("INSERT INTO teachers(id,name,email,hashed_password,is_active) VALUES(?,?,?,'test',1)", (pk, f"Teacher {pk}", f"{pk}@example.test"))
        self.conn.execute("INSERT INTO classes(id,name,created_by_teacher_id) VALUES(30,'Class 30',7)")
        self.conn.execute("INSERT INTO courses(id,name,created_by_teacher_id) VALUES(20,'Course',7)")
        self.conn.execute("INSERT INTO class_offerings(id,course_id,class_id,teacher_id) VALUES(40,20,30,7)")
        for pk in (7, 9):
            self.conn.execute("INSERT INTO students(id,student_id_number,name,class_id,enrollment_status) VALUES(?,?,?,30,'active')", (pk, f"S{pk}", f"Student {pk}"))
        expiry = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        self.teacher = {"id": 7, "role": "teacher", "name": "Teacher 7", "session_id": "teacher-session"}
        self.student = {"id": 7, "role": "student", "name": "Student 7", "session_id": "student-session"}
        for user, task_id in ((self.teacher, 10), (self.student, 11)):
            self.conn.execute("INSERT INTO user_sessions(session_user_key,session_id,user_id,role,expires_at) VALUES(?,?,?,?,?)", (f"{user['role']}:7", user["session_id"], "7", user["role"], expiry))
            self.conn.execute("INSERT INTO agent_tasks(id,task_uuid,teacher_id,actor_role,actor_id,teacher_name,task_type,title,private_instruction,status) VALUES(?,?,?,?,?,?,'general','Test task','Test instruction','running')",
                              (task_id, f"task-{task_id}", 7 if user["role"] == "teacher" else None, user["role"], 7, user["name"]))
        self.conn.commit()

    @contextmanager
    def connection(self):
        try:
            yield self.conn
        except Exception:
            self.conn.rollback()
            raise

    def token(self, user=None, scopes=None):
        user = user or self.teacher
        task_id = 10 if user["role"] == "teacher" else 11
        attempt = create_task_attempt(self.conn, task_id=task_id, worker_id="test-worker", startup_key=f"start-{task_id}")
        grant = issue_task_delegation(self.conn, task_id=task_id, attempt_id=attempt["id"], fencing_token=attempt["fencing_token"], purpose="tools",
                                      scopes=scopes or ["platform:write"], source_session_id=user["session_id"])
        self.conn.commit()
        return grant["token"]

    def count(self, table):
        return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

class PlatformWriteTests(PlatformWriteFixture):
    def test_resource_update_matches_normal_policy_and_rejects_stale_version(self):
        token = self.token()
        params = {"class_id": 30, "expected_updated_at": "legacy", "description": "Updated classroom description"}
        result = writes.dispatch_write(self.conn, token, "class-update", "update_class_attributes", params)
        self.conn.commit()
        self.assertEqual("Updated classroom description", result["result"]["attributes"]["description"])
        with self.assertRaises(HTTPException) as error:
            writes.dispatch_write(self.conn, token, "class-stale", "update_class_attributes", params)
        self.assertEqual(409, error.exception.status_code)
        self.conn.rollback()
        self.conn.execute("UPDATE classes SET created_by_teacher_id=8,owner_user_pk=8,scope_level='private' WHERE id=30")
        self.conn.commit()
        with self.assertRaises(HTTPException) as error:
            writes.dispatch_write(self.conn, token, "class-denied", "update_class_attributes", {**params, "expected_updated_at": result["result"]["attributes"]["updated_at"]})
        self.assertEqual(404, error.exception.status_code)
        self.conn.rollback()
        self.assertEqual(1, self.count("agent_action_executions"))

    def test_super_admin_organization_write_is_live_and_atomic(self):
        params = {"school_code": "another-school", "school_name": "New School"}
        token = self.token()
        with self.assertRaises(HTTPException) as denied:
            writes.dispatch_write(self.conn, token, "org-denied", "create_organization_school", params)
        self.assertEqual(403, denied.exception.status_code)
        self.conn.rollback()
        self.conn.execute("UPDATE teachers SET is_super_admin=1 WHERE id=7")
        self.conn.commit()
        # New authority requires a fresh token after the role attribute changes.
        attempt = self.conn.execute("SELECT * FROM agent_task_attempts WHERE task_id=10").fetchone()
        token = issue_task_delegation(self.conn, task_id=10, attempt_id=attempt["id"], fencing_token=attempt["fencing_token"], purpose="tools", scopes=["platform:write"], source_session_id="teacher-session")["token"]
        self.conn.commit()
        original_school_count = self.count("organization_schools")
        result = writes.dispatch_write(self.conn, token, "org-create", "create_organization_school", params)
        self.assertEqual("New School", result["result"]["item"]["school_name"])
        self.conn.rollback()
        self.assertEqual(original_school_count, self.count("organization_schools"))
        self.assertEqual(0, self.count("agent_action_executions"))
        self.assertNotIn("create_organization_school", [item["action"] for item in writes.platform_write_catalog(actor_role="teacher")["actions"]])
        self.assertIn("create_organization_school", [item["action"] for item in writes.platform_write_catalog(actor_role="teacher", is_super_admin=True)["actions"]])

    def test_assignment_draft_uses_web_classification_and_rolls_back_with_receipt(self):
        token = self.token()
        params = {"class_offering_id": 40, "title": "Reflection", "requirements_md": "Please reflect."}
        result = writes.dispatch_write(self.conn, token, "assignment-one", "create_assignment_draft", params)
        row = self.conn.execute("SELECT status,assessment_kind,assessment_kind_version,availability_mode FROM assignments WHERE id=?", (result["result"]["ref_id"],)).fetchone()
        self.assertEqual(("new", "homework", 1, "permanent"), tuple(row))
        self.assertEqual(1, self.count("assignment_classification_revisions"))
        self.conn.rollback()
        self.assertEqual((0, 0, 0), (self.count("assignments"), self.count("assignment_classification_revisions"), self.count("agent_action_executions")))
        student_token = self.token(self.student)
        with self.assertRaises(HTTPException) as error:
            writes.dispatch_write(self.conn, student_token, "assignment-student", "create_assignment_draft", params)
        self.assertEqual(403, error.exception.status_code)

    def test_material_blob_verified_receipt_replay_and_rollback_keep_shared_storage(self):
        token = self.token()
        with tempfile.TemporaryDirectory() as directory, patch("classroom_app.services.file_service.GLOBAL_FILES_DIR", Path(directory)), patch("classroom_app.services.file_service.GLOBAL_FILES_LEGACY_DIRS", ()):
            params = {"title": "Reflection", "content_md": "# Reflection"}
            result = writes.dispatch_write(self.conn, token, "material-one", "save_material_draft", params)
            receipt = result["result"]
            self.assertEqual("verified_immutable_blob", receipt["storage_status"])
            self.conn.commit()
            self.assertTrue(writes.dispatch_write(self.conn, token, "material-one", "save_material_draft", params)["replayed"])
            duplicate = writes.dispatch_write(self.conn, token, "material-two", "save_material_draft", params)["result"]
            self.assertEqual(receipt["file_hash"], duplicate["file_hash"])
            self.assertNotEqual(receipt["label"], duplicate["label"])
            self.conn.rollback()
            self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM course_materials WHERE node_type='file'").fetchone()[0])
            self.assertTrue(writes.dispatch_write(self.conn, token, "material-one", "save_material_draft", params)["replayed"])
            files = [path for path in Path(directory).rglob("*") if path.is_file()]
            self.assertEqual(1, len(files))
            files[0].write_bytes(b"corrupt")
            with self.assertRaises(HTTPException) as error:
                writes.dispatch_write(self.conn, token, "material-one", "save_material_draft", params)
            self.assertEqual(409, error.exception.status_code)
            with self.assertRaises(HTTPException):
                writes.dispatch_write(self.conn, token, "material-three", "save_material_draft", params)
            self.conn.rollback()
            self.assertEqual(1, self.count("agent_action_executions"))

    def test_actor_blog_writes_replay_one_receipt_and_keep_role_identity(self):
        for user in (self.teacher, self.student):
            token = self.token(user)
            result = writes.dispatch_write(self.conn, token, "draft-one", "create_blog_draft", {"title": "Learning notes", "content_md": "My reflection"})
            self.conn.commit()
            replay = writes.dispatch_write(self.conn, token, "draft-one", "create_blog_draft", {"title": "Learning notes", "content_md": "My reflection"})
            self.assertTrue(replay["replayed"])
            self.assertEqual(result["result"], replay["result"])
            row = self.conn.execute("SELECT author_identity,status FROM blog_posts WHERE id=?", (result["result"]["ref_id"],)).fetchone()
            self.assertEqual((f"{user['role']}:7", "draft"), tuple(row))
        self.assertEqual(2, self.count("blog_posts"))
        self.assertEqual(2, self.count("agent_action_executions"))
        self.assertEqual(0, self.count("scheduled_tasks"))

    def test_blog_receipt_outbox_and_notifications_rollback_together(self):
        token = self.token()
        result = writes.dispatch_write(self.conn, token, "post-one", "publish_blog_post", {"title": "Review", "content_md": "@管家 请帮我复盘"})
        self.assertIsNotNone(result["result"]["effect_task_id"])
        self.assertEqual(1, self.count("scheduled_tasks"))
        self.conn.rollback()
        self.assertEqual((0, 0, 0), (self.count("blog_posts"), self.count("scheduled_tasks"), self.count("agent_action_executions")))
        result = writes.dispatch_write(self.conn, token, "post-one", "publish_blog_post", {"title": "Review", "content_md": "@管家 请帮我复盘"})
        self.conn.commit()
        post_id = result["result"]["ref_id"]
        student_token = self.token(self.student)
        writes.dispatch_write(self.conn, student_token, "comment-one", "create_blog_comment", {"post_id": post_id, "content_md": "@管家 我有补充"})
        self.assertGreater(self.count("message_center_notifications"), 0)
        self.conn.rollback()
        self.assertEqual(0, self.count("blog_comments"))
        self.assertEqual(0, self.count("message_center_notifications"))
        self.assertEqual(1, self.count("scheduled_tasks"))

    def test_student_comment_matches_normal_visibility_and_comment_lock(self):
        post = blog_service.create_post(self.conn, self.teacher, title="Private", content_md="Private body", visibility="selected_users", visible_user_identities=["teacher:7", "student:9"])
        self.conn.commit()
        with self.assertRaises(PermissionError):
            blog_service.add_comment(self.conn, self.student, post["id"], content_md="Denied")
        token = self.token(self.student)
        with self.assertRaises(HTTPException) as error:
            writes.dispatch_write(self.conn, token, "private-comment", "create_blog_comment", {"post_id": post["id"], "content_md": "Denied"})
        self.assertEqual(403, error.exception.status_code)
        self.conn.rollback()
        self.assertEqual(0, self.count("agent_action_executions"))
        self.assertEqual(0, self.count("blog_comments"))

    def test_revoked_source_at_completion_rolls_back_blog_and_outbox(self):
        token = self.token()
        original = writes.execute_actor_action

        def revoke(*args, **kwargs):
            result = original(*args, **kwargs)
            self.conn.execute("DELETE FROM user_sessions WHERE session_user_key='teacher:7'")
            return result

        with patch.object(writes, "execute_actor_action", side_effect=revoke), self.assertRaises(HTTPException) as error:
            writes.dispatch_write(self.conn, token, "revoked-post", "publish_blog_post", {"title": "Review", "content_md": "@管家 请帮我复盘"})
        self.assertEqual(401, error.exception.status_code)
        self.conn.rollback()
        self.assertEqual((0, 0, 0), (self.count("blog_posts"), self.count("scheduled_tasks"), self.count("agent_action_executions")))

    def test_private_message_uses_normal_contact_policy_audit_and_notification(self):
        token = self.token(self.student)
        params = {"contact_identity": "teacher:7", "class_offering_id": 40, "content": "请问本次课堂重点是什么？"}
        result = writes.dispatch_write(self.conn, token, "message-one", "send_private_message", params)
        self.conn.commit()
        self.assertEqual(1, len(result["result"]["message_ids"]))
        row = self.conn.execute("SELECT sender_identity,recipient_identity FROM private_messages").fetchone()
        self.assertEqual(("student:7", "teacher:7"), tuple(row))
        self.assertGreater(self.count("private_message_audit_logs"), 0)
        self.assertGreater(self.count("message_center_notifications"), 0)
        self.assertTrue(writes.dispatch_write(self.conn, token, "message-one", "send_private_message", params)["replayed"])
        self.assertEqual(1, self.count("private_messages"))

    def test_notification_never_guesses_names_or_partially_sends_invalid_batch(self):
        token = self.token()
        for params in ({"content_md": "通知", "student_names": ["Student 7"]},
                       {"content_md": "通知", "recipient_identities": ["student:7", "student:999999"], "class_offering_id": 40}):
            with self.assertRaises(HTTPException):
                writes.dispatch_write(self.conn, token, "batch-one", "send_student_notification", params)
            self.conn.rollback()
            self.assertEqual(0, self.count("private_messages"))
            self.assertEqual(0, self.count("agent_action_executions"))

    def test_legacy_confirmation_uses_fresh_student_session_without_runner_and_replays(self):
        params = {"title": "Student draft", "content_md": "Saved through fresh user confirmation"}
        proposal = {"action": "create_blog_draft", "label": "保存草稿", "params": params}
        self.conn.execute("UPDATE agent_tasks SET status='completed',result_detail_json=? WHERE id=11", (json.dumps({"proposed_actions": [proposal]}),))
        self.conn.commit()
        with patch.object(agent_tasks, "get_db_connection", self.connection):
            preview = asyncio.run(agent_tasks.api_preview_agent_task_action(11, 0, Request({}), self.student))
            request = Request({"confirmation_token": preview["confirmation_token"]})
            first = asyncio.run(agent_tasks.api_execute_agent_task_action(11, 0, request, self.student))
            second = asyncio.run(agent_tasks.api_execute_agent_task_action(11, 0, request, self.student))
        self.assertEqual(first["result"], second["result"])
        self.assertTrue(second["replayed"])
        self.assertEqual(1, self.count("blog_posts"))
        self.assertEqual(0, self.count("agent_task_attempts"))
        self.assertEqual(0, self.count("agent_task_delegations"))
        receipt = dict(self.conn.execute("SELECT * FROM agent_action_executions").fetchone())
        self.assertEqual("user_confirmation", receipt["source_kind"])
        self.assertEqual("student", receipt["actor_role"])
        self.assertNotIn("source_session", json.dumps(second))


class SessionDocumentJobsTests(PlatformWriteFixture):
    def setUp(self):
        super().setUp()
        with patch.object(schema_session_learning_materials, "_SCHEMA_READY", False):
            schema_session_learning_materials.ensure_session_learning_materials_schema(self.conn)
        self.conn.execute("INSERT INTO class_offering_sessions(id,class_offering_id,order_index,title,content,section_count,session_date,weekday,week_index) VALUES(50,40,1,'Introduction','Learning reflection',2,'2026-09-10',4,1)")
        self.conn.commit()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for target, value in (("classroom_app.services.file_service.GLOBAL_FILES_DIR", Path(directory.name)),
                              ("classroom_app.services.file_service.GLOBAL_FILES_LEGACY_DIRS", ()),
                              ("classroom_app.database.get_db_connection", self.connection)):
            guard = patch(target, value)
            guard.start()
            self.addCleanup(guard.stop)

    def submit(self, token):
        return writes.dispatch_write(self.conn, token, "session-document-one", "generate_session_document", {
            "class_offering_id": 40, "session_id": 50, "document_type": "学习文档", "requirement_text": "请生成课堂导学与练习"})

    def delivery(self, result):
        row = dict(self.conn.execute("SELECT * FROM scheduled_tasks WHERE id=?", (result["result"]["generation_task"]["delivery_task_id"],)).fetchone())
        row["payload"] = json.loads(row["payload_json"])
        return row

    def model_result(self):
        return {"target_parent_key": "teacher_root", "bind_path": "lesson.md", "summary": "课时导学", "nodes": [{"path": "lesson.md", "type": "markdown", "content": "# 学习任务\n请先预习，再完成练习。"}]}

    def test_domain_task_outbox_and_operation_are_atomic_then_bind_once_after_agent_ends(self):
        token = self.token()
        self.submit(token)
        self.assertEqual((1, 1, 1), (self.count("session_material_generation_tasks"), self.count("scheduled_tasks"), self.count("agent_action_executions")))
        self.conn.rollback()
        self.assertEqual((0, 0, 0), (self.count("session_material_generation_tasks"), self.count("scheduled_tasks"), self.count("agent_action_executions")))
        result = self.submit(token)
        self.conn.commit()
        self.assertEqual("pending", result["result"]["completion_status"])
        task = self.delivery(result)
        self.conn.execute("UPDATE agent_tasks SET status='canceled' WHERE id=10")
        self.conn.execute("DELETE FROM user_sessions WHERE session_user_key='teacher:7'")
        self.conn.commit()
        with patch.object(generation, "_call_generation_ai", AsyncMock(return_value=self.model_result())) as model:
            self.assertIn("completed", asyncio.run(session_jobs.handle_session_material_generation(task)))
            self.assertIn("existing", asyncio.run(session_jobs.handle_session_material_generation(task)))
        self.assertEqual(1, model.await_count)
        receipt = self.conn.execute("SELECT status,generated_material_id FROM session_material_generation_tasks").fetchone()
        self.assertEqual("completed", receipt["status"])
        self.assertIsNotNone(receipt["generated_material_id"])
        self.assertEqual(receipt["generated_material_id"], self.conn.execute("SELECT learning_material_id FROM class_offering_sessions WHERE id=50").fetchone()[0])
        self.assertEqual(receipt["generated_material_id"], self.conn.execute("SELECT material_id FROM class_offering_learning_materials WHERE class_offering_id=40 AND session_id=50").fetchone()[0])
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM course_materials WHERE node_type='file'").fetchone()[0])

    def test_generated_primary_enters_real_list_preserves_prior_bindings_and_rolls_back_atomically(self):
        from classroom_app.services.session_learning_materials_service import bind_material_in_transaction
        old = generation._create_file_row(self.conn, teacher_id=7, parent_id=None, root_id=None,
            material_path="existing.md", name="existing.md", content="# Existing material", now="2026-09-10T00:00:00")
        bind_material_in_transaction(self.conn, 40, 50, old["id"], 7)
        self.conn.commit()
        with patch.object(generation, "refresh_root_git_metadata"):
            generated = generation.persist_generated_materials(self.conn, teacher_id=7, class_offering_id=40, session_id=50,
                base_parent_id=None, nodes=[{"path": "new.md", "type": "markdown", "content": "# New", "bind": True}])
        material_id = generated["id"]
        rows = self.conn.execute("SELECT material_id FROM class_offering_learning_materials WHERE class_offering_id=40 AND session_id=50 ORDER BY sort_order,id").fetchall()
        self.assertEqual([material_id, old["id"]], [row[0] for row in rows])
        self.assertEqual(material_id, self.conn.execute("SELECT learning_material_id FROM class_offering_sessions WHERE id=50").fetchone()[0])
        self.conn.rollback()
        self.assertEqual([old["id"]], [row[0] for row in self.conn.execute("SELECT material_id FROM class_offering_learning_materials").fetchall()])
        self.assertEqual(old["id"], self.conn.execute("SELECT learning_material_id FROM class_offering_sessions WHERE id=50").fetchone()[0])
        self.assertIsNone(self.conn.execute("SELECT id FROM course_materials WHERE id=?", (material_id,)).fetchone())

    def test_late_model_result_after_expiry_or_teacher_revocation_cannot_publish_material(self):
        for mutation in ("UPDATE session_material_generation_tasks SET status='failed'", "UPDATE teachers SET is_active=0 WHERE id=7"):
            with self.subTest(mutation=mutation):
                self.conn.execute("UPDATE teachers SET is_active=1 WHERE id=7")
                self.conn.execute("DELETE FROM session_material_generation_tasks")
                self.conn.execute("DELETE FROM scheduled_tasks")
                self.conn.execute("DELETE FROM agent_action_executions")
                self.conn.commit()
                task_row = session_jobs.create_scheduled_generation_task(self.conn, teacher_id=7, class_offering_id=40, session_id=50, trigger_mode="guided", document_type="学习文档", requirement_text="请生成")
                self.conn.commit()
                task = dict(self.conn.execute("SELECT * FROM scheduled_tasks WHERE id=?", (task_row["delivery_task_id"],)).fetchone())
                task["payload"] = json.loads(task["payload_json"])

                async def model(**kwargs):
                    self.conn.execute(mutation)
                    self.conn.commit()
                    return self.model_result()

                with patch.object(generation, "_call_generation_ai", AsyncMock(side_effect=model)), self.assertRaises(RuntimeError):
                    asyncio.run(session_jobs.handle_session_material_generation(task))
                self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM course_materials WHERE node_type='file'").fetchone()[0])
                self.assertIsNone(self.conn.execute("SELECT learning_material_id FROM class_offering_sessions WHERE id=50").fetchone()[0])
                self.assertEqual("failed", self.conn.execute("SELECT status FROM session_material_generation_tasks").fetchone()[0])


class CoreBrokerReadTests(PlatformWriteFixture):
    def setUp(self):
        super().setUp()
        self.tokens = {"teacher": self.token(scopes=["platform:read"]), "student": self.token(self.student, scopes=["platform:read"])}
        for module in ("classroom_app.database", "classroom_app.dependencies", "classroom_app.services.agent_platform_broker", "classroom_app.routers.blog",
                       "classroom_app.routers.manage_parts.base_resource_modes", "classroom_app.routers.manage_parts.system_config"):
            guard = patch(module + ".get_db_connection", self.connection)
            guard.start()
            self.addCleanup(guard.stop)
        verifier = patch.object(dependencies, "verify_token", side_effect=lambda token, _ip: self.student if token == "student" else self.teacher if token == "teacher" else None)
        verifier.start()
        self.addCleanup(verifier.stop)
        self.app = FastAPI()
        self.app.include_router(blog.router)
        self.app.include_router(base_resource_modes.router, prefix="/api/manage")
        self.app.include_router(system_config.router, prefix="/api/manage")
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def test_student_own_drafts_read_matches_web_and_does_not_record_view(self):
        for user in (self.teacher, self.student):
            blog_service.create_post(self.conn, user, title=user["role"] + " private draft", content_md="Private", status="draft")
        self.conn.commit()
        for role in ("student", "teacher"):
            self.client.cookies.set("access_token", role)
            web = self.client.get("/api/blog/my-posts", params={"status": "draft"})
            result = asyncio.run(broker.dispatch_read(self.app, self.tokens[role], "blog.mine", query_params={"status": "draft"}))
            self.assertEqual(200, web.status_code)
            self.assertEqual(web.json(), result["data"])
            self.assertIn(role + " private draft", json.dumps(result))
            self.assertNotIn(("teacher" if role == "student" else "student") + " private draft", json.dumps(result))
        self.assertEqual(0, self.count("blog_post_views"))

    def test_class_attributes_match_web_and_admin_gate_has_no_student_or_teacher_bypass(self):
        self.client.cookies.set("access_token", "teacher")
        web = self.client.get("/api/manage/classes/30/attributes")
        result = asyncio.run(broker.dispatch_read(self.app, self.tokens["teacher"], "class.attributes", path_params={"class_id": 30}))
        self.assertEqual(200, web.status_code)
        self.assertEqual(web.json(), result["data"])
        for role, key in (("student", "class.attributes"), ("teacher", "organization.schools")):
            with self.assertRaises(HTTPException) as denied:
                asyncio.run(broker.dispatch_read(self.app, self.tokens[role], key, path_params={"class_id": 30} if key == "class.attributes" else {}))
            self.assertEqual(403, denied.exception.status_code)


class BlogOutboxTests(PlatformWriteFixture):
    def test_web_create_update_and_comment_enqueue_inside_commit_without_inline_model(self):
        background = BackgroundTasks()
        with patch.object(blog, "get_db_connection", self.connection):
            post = asyncio.run(blog.api_create_post(Request({"title": "Draft", "content_md": "@管家 请点评", "status": "draft"}), background, self.student))
            self.assertEqual(0, self.count("scheduled_tasks"))
            asyncio.run(blog.api_update_post(post["id"], Request({"status": "published"}), background, self.student))
            self.assertEqual(1, self.count("scheduled_tasks"))
            comment = asyncio.run(blog.api_add_comment(post["id"], Request({"content_md": "@管家 我再补充"}), background, self.teacher))
        self.assertEqual(2, self.count("scheduled_tasks"))
        self.assertEqual([], background.tasks)
        self.assertIsNotNone(self.conn.execute("SELECT id FROM message_center_notifications WHERE ref_type='blog_comment' AND ref_id=?", (str(comment["id"]),)).fetchone())

    def test_outbox_failure_rolls_back_normal_web_post(self):
        with patch.object(blog, "get_db_connection", self.connection), patch.object(blog, "enqueue_blog_mention", side_effect=RuntimeError("outbox unavailable")), self.assertRaises(RuntimeError):
            asyncio.run(blog.api_create_post(Request({"title": "Review", "content_md": "@管家 请点评"}), BackgroundTasks(), self.student))
        self.assertEqual(0, self.count("blog_posts"))

    def mention(self):
        post = blog_service.create_post(self.conn, self.student, title="Review", content_md="@管家 请点评")
        task_id = effects.enqueue_blog_mention(self.conn, self.student, trigger_type="post", trigger_id=post["id"])
        self.conn.commit()
        row = dict(self.conn.execute("SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone())
        row["payload"] = json.loads(row["payload_json"])
        return post, row

    def test_duplicate_enqueue_keeps_live_claim_and_completed_publication_receipt(self):
        post, task = self.mention()
        self.conn.execute("UPDATE scheduled_tasks SET status='running',attempt_count=2,locked_by='worker' WHERE id=?", (task["id"],))
        self.assertEqual(task["id"], effects.enqueue_blog_mention(self.conn, self.student, trigger_type="post", trigger_id=post["id"]))
        self.assertEqual(("running", 2, "worker"), tuple(self.conn.execute("SELECT status,attempt_count,locked_by FROM scheduled_tasks").fetchone()))
        blog_ai_service._prepare_reply_job(self.conn, "post", post["id"], post["id"], "student:7")
        blog_ai_service._mark_reply_job_done(self.conn, "post", post["id"], 123)
        self.assertIsNone(effects.enqueue_blog_mention(self.conn, self.student, trigger_type="post", trigger_id=post["id"]))
        self.assertEqual(1, self.count("scheduled_tasks"))

    def test_stale_pending_retries_once_and_real_publication_cas_prevents_duplicate_reply(self):
        post, task = self.mention()
        blog_ai_service._prepare_reply_job(self.conn, "post", post["id"], post["id"], "student:7")
        self.conn.execute("UPDATE blog_ai_reply_jobs SET updated_at='2000-01-01T00:00:00'")
        self.conn.commit()

        async def generate(post_id, user):
            self.assertTrue(blog_ai_service._prepare_reply_job(self.conn, "post", post_id, post_id, "student:7"))
            source = "Review\n@管家 请点评"
            self.assertTrue(blog_ai_service._publish_blog_ai_reply(self.conn, trigger_type="post", trigger_id=post_id, post_id=post_id, source_text=source, reply_text="这是课堂建议"))
            self.assertFalse(blog_ai_service._publish_blog_ai_reply(self.conn, trigger_type="post", trigger_id=post_id, post_id=post_id, source_text=source, reply_text="重复建议"))
            self.conn.commit()

        generator = AsyncMock(side_effect=generate)
        with patch("classroom_app.database.get_db_connection", self.connection), patch.object(blog_ai_service, "maybe_reply_to_post_mention", generator):
            self.assertIn("published", asyncio.run(effects.handle_blog_mention_reply(task)))
            self.assertIn("existing", asyncio.run(effects.handle_blog_mention_reply(task)))
        self.assertEqual(1, generator.await_count)
        self.assertEqual(1, self.count("blog_comments"))

    def test_fresh_pending_and_failed_generation_raise_for_bounded_scheduler_retry(self):
        post, task = self.mention()
        blog_ai_service._prepare_reply_job(self.conn, "post", post["id"], post["id"], "student:7")
        self.conn.commit()
        generator = AsyncMock()
        with patch("classroom_app.database.get_db_connection", self.connection), patch.object(blog_ai_service, "maybe_reply_to_post_mention", generator):
            with self.assertRaisesRegex(RuntimeError, "still running"):
                asyncio.run(effects.handle_blog_mention_reply(task))
            self.assertEqual(0, generator.await_count)
            self.conn.execute("UPDATE blog_ai_reply_jobs SET status='failed'")
            self.conn.commit()
            with self.assertRaisesRegex(RuntimeError, "no completed"):
                asyncio.run(effects.handle_blog_mention_reply(task))
        self.assertEqual(8, task["max_attempts"])
        self.assertEqual(1, generator.await_count)

    def test_deleted_or_deactivated_source_never_generates_reply(self):
        post, task = self.mention()
        blog_ai_service._prepare_reply_job(self.conn, "post", post["id"], post["id"], "student:7")
        self.conn.execute("UPDATE blog_posts SET content_md='已取消提及' WHERE id=?", (post["id"],))
        self.conn.commit()
        with patch("classroom_app.database.get_db_connection", self.connection), patch.object(blog_ai_service, "maybe_reply_to_post_mention", AsyncMock()) as generator:
            self.assertIn("skipped", asyncio.run(effects.handle_blog_mention_reply(task)))
            self.assertEqual(0, generator.await_count)
        self.assertEqual("failed", self.conn.execute("SELECT status FROM blog_ai_reply_jobs").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
