"""Real material rows, normal Web services, and transactional Agent receipts."""
import asyncio
from pathlib import Path
import tempfile
from unittest.mock import patch

from fastapi import HTTPException

from classroom_app.db import schema_session_learning_materials
from classroom_app.services import agent_material_actions as actions
from classroom_app.services import agent_platform_write_service as writes
from classroom_app.services import material_attributes_service as attributes
from classroom_app.services import session_learning_materials_service as bindings
from classroom_app.services import session_material_generation_service as generation
from classroom_app.services.agent_actor_service import resolve_agent_actor
from tests.test_agent_platform_writes import PlatformWriteFixture, Request


class MaterialActionTests(PlatformWriteFixture):
    def setUp(self):
        super().setUp()
        with patch.object(schema_session_learning_materials, "_SCHEMA_READY", False):
            schema_session_learning_materials.ensure_session_learning_materials_schema(self.conn)
        self.conn.execute("INSERT INTO class_offering_sessions(id,class_offering_id,order_index,title,session_date,weekday,week_index) VALUES(50,40,1,'Lesson','2026-09-10',4,1)")
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for target, value in (("classroom_app.services.file_service.GLOBAL_FILES_DIR", Path(directory.name)),
                              ("classroom_app.services.file_service.GLOBAL_FILES_LEGACY_DIRS", ())):
            guard = patch(target, value)
            guard.start()
            self.addCleanup(guard.stop)
        self.first = self.file("one.md")
        self.second = self.file("two.md")
        self.conn.commit()

    def file(self, name, teacher_id=7, parent_id=None, root_id=None, material_path=None):
        return generation._create_file_row(self.conn, teacher_id=teacher_id, parent_id=parent_id, root_id=root_id,
            material_path=material_path or name, name=name, content="# Fixture " + name, now="2026-09-10T00:00:00")

    def binding_params(self, material=None, session_id=50):
        return {"class_offering_id": 40, "session_id": session_id, "material_id": int((material or self.first)["id"]),
                "expected_binding_version": bindings.material_binding_version(self.conn, 40, session_id)}

    def execute(self, action, params, *, actor_id=7, actor_role="teacher"):
        return actions.execute_material_action(self.conn, actor=resolve_agent_actor(self.conn, actor_role, actor_id), action=action, params=params)

    def test_binding_receipt_and_business_change_commit_once_and_rollback_together(self):
        token = self.token()
        params = self.binding_params()
        result = writes.dispatch_write(self.conn, token, "material-bind-one", "bind_learning_material", params)
        self.assertEqual(self.first["id"], result["result"]["primary_material_id"])
        self.conn.rollback()
        self.assertEqual(0, self.count("class_offering_learning_materials"))
        self.assertEqual(0, self.count("agent_action_executions"))
        result = writes.dispatch_write(self.conn, token, "material-bind-one", "bind_learning_material", params)
        self.conn.commit()
        repeated = writes.dispatch_write(self.conn, token, "material-bind-one", "bind_learning_material", params)
        self.assertTrue(repeated["replayed"])
        self.assertEqual(result["result"], repeated["result"])
        self.assertEqual(1, self.count("class_offering_learning_materials"))

    def test_stale_list_conflict_and_unbind_preserves_other_binding_file_and_access(self):
        stale = self.binding_params(self.second)
        self.execute("bind_learning_material", self.binding_params())
        with self.assertRaises(HTTPException) as caught:
            self.execute("bind_learning_material", stale)
        self.assertEqual(409, caught.exception.status_code)
        self.execute("bind_learning_material", self.binding_params(self.second))
        self.conn.commit()
        result = self.execute("unbind_learning_material", self.binding_params())
        self.assertEqual(self.second["id"], result["primary_material_id"])
        self.assertEqual(2, self.count("course_material_assignments"))
        self.assertEqual(2, self.count("course_materials"))
        self.conn.rollback()
        self.assertEqual(self.first["id"], bindings._primary_material_id(self.conn, 40, 50))
        self.assertEqual(2, self.count("class_offering_learning_materials"))

    def test_home_binding_student_other_teacher_and_foreign_session(self):
        params = self.binding_params(session_id=0)
        for identity in (("student", 7), ("teacher", 8)):
            with self.assertRaises(HTTPException):
                self.execute("bind_learning_material", params, actor_role=identity[0], actor_id=identity[1])
        self.conn.execute("INSERT INTO class_offering_sessions(id,class_offering_id,order_index,title,session_date,weekday,week_index) VALUES(51,999,1,'Foreign','2026-09-10',4,1)")
        with self.assertRaises(HTTPException):
            self.execute("bind_learning_material", self.binding_params(session_id=51))
        result = self.execute("bind_learning_material", params)
        self.assertEqual(0, result["session_id"])
        self.assertEqual(self.first["id"], self.conn.execute("SELECT home_learning_material_id FROM class_offerings WHERE id=40").fetchone()[0])

    def test_rename_real_subtree_does_not_match_sql_wildcard_sibling(self):
        root = generation._create_folder_row(self.conn, teacher_id=7, parent_id=None, root_id=None,
                                               material_path="root", name="root", now="2026-09-10T00:00:00")
        target = generation._create_folder_row(self.conn, teacher_id=7, parent_id=root["id"], root_id=root["id"],
                                                 material_path="root/a_b", name="a_b", now="2026-09-10T00:00:00")
        child = self.file("child.md", parent_id=target["id"], root_id=root["id"], material_path="root/a_b/child.md")
        sibling = self.file("keep.md", parent_id=root["id"], root_id=root["id"], material_path="root/axb/keep.md")
        self.conn.commit()
        result = self.execute("update_material_attributes", {"material_id": target["id"], "expected_updated_at": target["updated_at"], "name": "renamed"})
        self.assertEqual("root/renamed", result["attributes"]["material_path"])
        self.assertEqual("root/renamed/child.md", self.conn.execute("SELECT material_path FROM course_materials WHERE id=?", (child["id"],)).fetchone()[0])
        self.assertEqual("root/axb/keep.md", self.conn.execute("SELECT material_path FROM course_materials WHERE id=?", (sibling["id"],)).fetchone()[0])
        self.conn.rollback()
        self.assertEqual("root/a_b", self.conn.execute("SELECT material_path FROM course_materials WHERE id=?", (target["id"],)).fetchone()[0])

    def test_attributes_enforce_ownership_revision_conflicts_and_superadmin_web_policy(self):
        foreign = self.file("foreign.md", teacher_id=8)
        params = {"material_id": foreign["id"], "expected_updated_at": foreign["updated_at"], "name": "new.md"}
        with self.assertRaises(HTTPException):
            self.execute("update_material_attributes", params)
        self.conn.execute("UPDATE teachers SET is_super_admin=1 WHERE id=7")
        self.execute("update_material_attributes", params)
        with self.assertRaises(HTTPException) as caught:
            self.execute("update_material_attributes", params)
        self.assertEqual(409, caught.exception.status_code)

    def test_normal_web_uses_shared_service_and_rejects_invalid_scope_without_rename(self):
        from classroom_app.routers.materials_parts import library
        with patch.object(library, "get_db_connection", self.connection), patch.object(library, "_serialize_material_attributes", side_effect=lambda conn, row, user: dict(row)):
            result = asyncio.run(library.patch_material_attributes(int(self.first["id"]), Request({"name": "from-web.md"}), self.teacher))
        self.assertEqual("from-web.md", result["attributes"]["name"])
        with self.assertRaises(HTTPException):
            attributes.update_material_attributes(self.conn, material_id=self.first["id"], teacher_id=7, payload={"name": "bad.md", "scope_level": "bad"})
        self.assertEqual("from-web.md", self.conn.execute("SELECT name FROM course_materials WHERE id=?", (self.first["id"],)).fetchone()[0])
