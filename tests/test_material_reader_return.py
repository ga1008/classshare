import asyncio
import json
import sqlite3
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from fastapi.responses import HTMLResponse

from classroom_app.dependencies import get_current_user
from classroom_app.routers.materials_parts import exports as reader_mod


class MaterialReaderReturnTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("CREATE TABLE class_offering_sessions(id INTEGER PRIMARY KEY, class_offering_id INTEGER, title TEXT)")
        self.conn.executemany("INSERT INTO class_offering_sessions VALUES(?,?,?)", [(12, 7, "第三次课"), (13, 8, "其他课堂私密名称")])
        self.user = {"id": 17, "role": "student"}
        app = FastAPI()
        app.include_router(reader_mod.router)
        app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.conn.close()

    @contextmanager
    def database(self):
        yield self.conn

    def request_unavailable(self, route, status, query="class_offering_id=7&session_id=12", classroom_error=None):
        with patch.object(reader_mod, "get_db_connection", side_effect=self.database), patch.object(reader_mod, "ensure_user_material_access", side_effect=HTTPException(status, "private material name must not leak")), patch.object(reader_mod, "ensure_classroom_access", side_effect=classroom_error, return_value={"id": 7, "course_name": "网络原理"}):
            return self.client.get(f"/materials/{route}/99?{query}")

    def test_deleted_or_revoked_material_returns_verified_original_classroom(self):
        for route in ("view", "render-view"):
            for status in (403, 404):
                with self.subTest(route=route, status=status):
                    response = self.request_unavailable(route, status)
                    self.assertEqual(response.status_code, status)
                    self.assertIn('href="/classroom/7"', response.text)
                    self.assertIn("返回课次详情", response.text)
                    self.assertIn("第三次课", response.text)
                    self.assertNotIn("private material name", response.text)
                    section = response.text.split('<section class="reader-unavailable"', 1)[1].split('</section>', 1)[0]
                    self.assertNotIn('href="https://', section)
                    self.assertIn("no-store", response.headers["cache-control"])

    def test_home_material_returns_classroom_without_fake_session(self):
        response = self.request_unavailable("view", 404, query="class_offering_id=7")
        self.assertIn('href="/classroom/7"', response.text)
        self.assertIn("返回课堂", response.text)
        self.assertNotIn("返回课次详情", response.text)

    def test_unverified_foreign_deleted_or_negative_session_uses_original_error(self):
        for session_id in (13, 999, -1):
            with self.subTest(session=session_id):
                response = self.request_unavailable("view", 404, query=f"class_offering_id=7&session_id={session_id}")
                self.assertEqual(response.status_code, 404)
                self.assertTrue(response.headers["content-type"].startswith("application/json"))
                self.assertNotIn("其他课堂私密名称", response.text)
        response = self.request_unavailable("view", 403, classroom_error=HTTPException(403, "classroom access revoked"))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(response.headers["content-type"].startswith("application/json"))

    def test_no_source_teacher_and_authentication_keep_existing_exception_flow(self):
        response = self.request_unavailable("view", 404, query="")
        self.assertTrue(response.headers["content-type"].startswith("application/json"))
        self.user = {"id": 17, "role": "teacher"}
        response = self.request_unavailable("view", 403)
        self.assertTrue(response.headers["content-type"].startswith("application/json"))
        self.user = {"id": 17, "role": "student"}
        response = self.request_unavailable("render-view", 401)
        self.assertEqual(response.status_code, 401)
        self.assertTrue(response.headers["content-type"].startswith("application/json"))
        response = self.request_unavailable("view", 404, classroom_error=HTTPException(401, "session expired"))
        self.assertEqual(response.status_code, 401)

    def test_external_return_parameters_cannot_control_the_link(self):
        response = self.request_unavailable("view", 404, query="class_offering_id=7&session_id=12&return_to=https%3A%2F%2Fevil.example%2F")
        self.assertIn('href="/classroom/7"', response.text)
        self.assertNotIn("evil.example", response.text)

    def test_successful_reader_is_unchanged_and_does_not_read_return_context(self):
        result = object()
        async def operation(request):
            return result
        with patch.object(reader_mod, "get_db_connection") as database:
            response = asyncio.run(reader_mod._with_reader_return_on_unavailable(operation)(object()))
        self.assertIs(response, result)
        database.assert_not_called()

    def test_new_tab_error_flag_only_applies_after_verifying_classroom_source(self):
        response = self.request_unavailable("render-view", 404, query="class_offering_id=7&session_id=12&classroom_reader_tab=1")
        self.assertIn('"close_tab": true', response.text)
        self.assertIn("material_reader_return.js", response.text)
        response = self.request_unavailable("render-view", 404, query="class_offering_id=7&session_id=13&classroom_reader_tab=1")
        self.assertNotIn("MATERIAL_READER_RETURN", response.text)

    def test_successful_html_reader_validates_source_before_enabling_tab_return(self):
        material = {"id": 99, "node_type": "file", "name": "lesson.html", "material_path": "lesson.html"}
        def render(request, template, context, **kwargs):
            return HTMLResponse(json.dumps(context["reader_return"]))
        with patch.object(reader_mod, "get_db_connection", side_effect=self.database), patch.object(reader_mod, "ensure_user_material_access", return_value=material), patch.object(reader_mod, "resolve_render_target", return_value={"entry_id": 99}), patch.object(reader_mod, "resolve_render_file", return_value=material), patch.object(reader_mod, "find_html_package_root", return_value=None), patch.object(reader_mod.templates, "TemplateResponse", side_effect=render), patch.object(reader_mod, "ensure_classroom_access", return_value={"id": 7, "course_name": "网络原理"}) as access:
            response = self.client.get("/materials/render-view/99?class_offering_id=7&session_id=12&classroom_reader_tab=1")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["url"], "/classroom/7")
            self.assertTrue(response.json()["close_tab"])
            self.assertEqual(access.call_count, 1)
            response = self.client.get("/materials/render-view/99?class_offering_id=7&session_id=13&classroom_reader_tab=1")
            self.assertIsNone(response.json())
            access.reset_mock()
            response = self.client.get("/materials/render-view/99?class_offering_id=7&session_id=12")
            self.assertIsNone(response.json())
            access.assert_not_called()


if __name__ == "__main__":
    unittest.main()
