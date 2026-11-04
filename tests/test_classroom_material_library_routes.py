import hashlib
import io
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI, HTTPException

from classroom_app import config
from classroom_app.routers.materials_parts import learning, library
from classroom_app.services.material_publication_service import record_classroom_material_publication
from classroom_app.services.materials_service import ensure_user_material_access
from tests.test_session_learning_materials_service import _make_conn


class ClassroomMaterialLibraryRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.conn = _make_conn()
        self.addCleanup(self.conn.close)
        self.conn.executescript("""
            ALTER TABLE class_offerings ADD COLUMN textbook_id INTEGER;
            ALTER TABLE class_offerings ADD COLUMN semester TEXT;
            ALTER TABLE course_materials ADD COLUMN file_size INTEGER DEFAULT 0;
            ALTER TABLE course_materials ADD COLUMN ai_capability TEXT;
            ALTER TABLE course_materials ADD COLUMN ai_parse_status TEXT;
            ALTER TABLE course_materials ADD COLUMN ai_parse_result_json TEXT;
            ALTER TABLE course_materials ADD COLUMN ai_optimize_status TEXT;
            ALTER TABLE course_materials ADD COLUMN check_questions_json TEXT;
            ALTER TABLE course_materials ADD COLUMN check_questions_status TEXT;
            ALTER TABLE course_materials ADD COLUMN check_questions_error TEXT;
            ALTER TABLE course_materials ADD COLUMN check_questions_generated_at TEXT;
            ALTER TABLE course_materials ADD COLUMN created_at TEXT;
            ALTER TABLE course_materials ADD COLUMN updated_at TEXT;
            ALTER TABLE course_materials ADD COLUMN published_at TEXT;
            CREATE TABLE classes (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE courses (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE textbooks (
                id INTEGER PRIMARY KEY, teacher_id INTEGER, title TEXT, authors_json TEXT, publisher TEXT,
                attachment_path TEXT, attachment_name TEXT, attachment_size INTEGER, attachment_mime_type TEXT
            );
            INSERT INTO classes VALUES (1,'First'), (2,'Second');
            INSERT INTO courses VALUES (1,'Course');
            INSERT INTO teachers (id) VALUES (2);
            INSERT INTO students(id,class_id) VALUES (8,2);
            INSERT INTO class_offerings (id,class_id,course_id,teacher_id) VALUES (6,2,1,2);
        """)
        self.teacher = {"role": "teacher", "id": 1}
        self.student = {"role": "student", "id": 9}
        self.other_teacher = {"role": "teacher", "id": 2}
        self.other_student = {"role": "student", "id": 8}

        @contextmanager
        def connection():
            try:
                yield self.conn
            except BaseException:
                self.conn.rollback()
                raise

        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(config, "DB_ENGINE", "sqlite"))
        stack.enter_context(patch.object(learning, "get_db_connection", connection))
        stack.enter_context(patch.object(library, "get_db_connection", connection))
        stack.enter_context(patch.object(library, "load_teacher_org_scope", return_value={
            "school_code": "", "school_name": "", "college": "", "department": "",
        }))

    async def test_classroom_projection_hides_archive_and_internal_fields_for_both_roles(self):
        self.conn.executescript("""
            UPDATE course_materials SET check_questions_json='teacher answer key' WHERE id=201;
            INSERT INTO course_materials(id,teacher_id,parent_id,root_id,name,material_path,node_type,preview_type)
            VALUES (202,1,200,200,'records.xlsx','site/records.xlsx','file','spreadsheet');
            INSERT INTO material_ai_import_records(document_group,package_material_id) VALUES ('final_material',202);
        """)
        record_classroom_material_publication(self.conn, 200, 5, 1)
        self.conn.commit()
        for user in (self.teacher, self.student):
            root = await learning.get_classroom_materials(5, None, user)
            self.assertEqual([200], [item["id"] for item in root["items"]])
            self.assertEqual(1, root["items"][0]["child_count"])
            self.assertEqual(user == self.teacher, root["capabilities"]["can_upload"])
            folder = await learning.get_classroom_materials(5, 200, user)
            self.assertEqual([201], [item["id"] for item in folder["items"]])
            self.assertFalse(folder["capabilities"]["can_upload"])
            self.assertNotIn("check_questions_json", folder["items"][0])
            self.assertIn("/materials/render-view/", folder["items"][0]["preview_url"])
            with self.assertRaises(HTTPException) as denied:
                await learning.get_classroom_material_detail(5, 202, user)
            self.assertEqual(403, denied.exception.status_code)

    async def test_classroom_upload_defaults_to_current_class_and_requires_its_teacher(self):
        async def store(file):
            content = await file.read()
            return {"hash": hashlib.sha256(content).hexdigest(), "size": len(content)}

        with (patch.object(library, "save_file_globally", store),
              patch.object(library, "refresh_root_git_metadata"),
              patch.object(library, "_serialize_material_items", side_effect=lambda conn, rows, **kwargs: [dict(row) for row in rows])):
            app = FastAPI()
            app.include_router(library.router)
            actor = self.other_teacher
            app.dependency_overrides[library.get_current_teacher] = lambda: actor
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                async def upload(content):
                    return await client.post(
                        "/api/materials/upload", data={"class_offering_id": "5"},
                        files={"files": ("handout.md", io.BytesIO(content), "text/markdown")},
                    )

                self.assertEqual(403, (await upload(b"not allowed")).status_code)
                actor = self.teacher
                first = await upload(b"# Handout")
                second_response = await upload(b"# Revised")
                self.assertEqual(200, first.status_code, first.text)
                self.assertEqual(200, second_response.status_code, second_response.text)
                uploaded = first.json()
                second_upload = second_response.json()
        material = uploaded["created_items"][0]
        self.assertEqual("classroom", material["scope_level"])
        self.assertEqual(0, material["student_visible"])
        second = second_upload["created_items"][0]
        self.assertNotEqual(material["name"], second["name"])
        self.assertEqual(
            {material["id"], second["id"]},
            {row["id"] for row in (await learning.get_classroom_materials(5, None, self.student))["items"]},
        )
        self.assertFalse((await learning.get_classroom_materials(6, None, self.other_student))["items"])

    async def test_owner_only_publication_and_explicit_withdrawal(self):
        self.conn.execute("UPDATE course_materials SET scope_level='public' WHERE id=100")
        self.conn.commit()
        with self.assertRaises(HTTPException) as denied:
            await library.assign_material_to_classrooms(100, library.MaterialAssignRequest(class_offering_ids=[6]), self.other_teacher)
        self.assertEqual(404, denied.exception.status_code)
        await library.assign_material_to_classrooms(100, library.MaterialAssignRequest(class_offering_ids=[5]), self.teacher)
        repeated = await library.assign_material_to_classrooms(
            100, library.MaterialAssignRequest(class_offering_ids=[5, 5]), self.teacher,
        )
        self.assertEqual(0, repeated["added_count"])
        self.assertEqual(1, len(repeated["assignments"]))
        self.assertEqual(100, (await learning.get_classroom_material_detail(5, 100, self.student))["material"]["id"])
        await library.assign_material_to_classrooms(100, library.MaterialAssignRequest(class_offering_ids=[]), self.teacher)
        with self.assertRaises(HTTPException) as withdrawn:
            await learning.get_classroom_material_detail(5, 100, self.student)
        self.assertEqual(403, withdrawn.exception.status_code)

    async def test_organization_student_opt_in_preserves_scope_and_honors_revision(self):
        self.conn.execute("UPDATE course_materials SET scope_level='public',updated_at='r1' WHERE id=100")
        self.conn.commit()
        with self.assertRaises(HTTPException):
            ensure_user_material_access(self.conn, 100, self.other_student)
        with patch.object(library, "_serialize_material_items", side_effect=lambda conn, rows, **kwargs: [dict(row) for row in rows]):
            await library.update_material_scope(
                100, library.MaterialScopeUpdateRequest(student_visible=True, expected_updated_at="r1"), self.teacher,
            )
            visible = ensure_user_material_access(self.conn, 100, self.other_student)
            self.assertEqual("public", visible["scope_level"])
            with self.assertRaises(HTTPException) as stale:
                await library.update_material_scope(
                    100, library.MaterialScopeUpdateRequest(student_visible=False, expected_updated_at="r1"), self.teacher,
                )
            self.assertEqual(409, stale.exception.status_code)
        self.assertEqual(1, ensure_user_material_access(self.conn, 100, self.other_student)["student_visible"])

    async def test_textbook_attachment_is_scoped_and_rechecks_current_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic-textbook.pdf"
            path.write_bytes(b"%PDF-synthetic")
            self.conn.execute(
                "INSERT INTO textbooks VALUES (1,1,'Course book','[\"Author\"]','Publisher',?,?,?,'application/pdf')",
                (str(path), path.name, path.stat().st_size),
            )
            self.conn.execute("UPDATE class_offerings SET textbook_id=1 WHERE id=5")
            root = await learning.get_classroom_materials(5, None, self.student)
            self.assertEqual("Course book", root["textbook"]["title"])
            self.assertTrue(root["textbook"]["preview_url"].endswith("?preview=true"))
            self.assertNotIn("attachment_path", root["textbook"])
            with patch.object(learning, "TEXTBOOK_ATTACHMENT_DIR", Path(directory)):
                response = await learning.get_classroom_textbook_attachment(5, 1, True, self.student)
                self.assertEqual("application/pdf", response.media_type)
                self.assertTrue(response.headers["content-disposition"].startswith("inline"))
                with self.assertRaises(HTTPException) as denied:
                    await learning.get_classroom_textbook_attachment(5, 1, True, self.other_student)
                self.assertEqual(403, denied.exception.status_code)
                self.conn.execute("UPDATE class_offerings SET textbook_id=NULL WHERE id=5")
                with self.assertRaises(HTTPException) as replaced:
                    await learning.get_classroom_textbook_attachment(5, 1, True, self.student)
                self.assertEqual(404, replaced.exception.status_code)
