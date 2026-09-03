import contextlib
import io
import sqlite3
import tempfile
from pathlib import Path
from unittest import mock

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from classroom_app.db import schema_lessondoc_editor
from classroom_app.dependencies import get_current_user
from classroom_app.routers import lessondoc_editor as routes
from classroom_app.routers.materials_parts import library
from classroom_app.services import file_service
from tests.test_lessondoc_service import _PackFixture


class TestEditorApi(_PackFixture):
    def setUp(self):
        super().setUp()
        schema_lessondoc_editor.reset_schema_ready_for_tests()
        self.addCleanup(schema_lessondoc_editor.reset_schema_ready_for_tests)
        engine = mock.patch.object(schema_lessondoc_editor, "get_configured_db_engine", return_value="sqlite")
        engine.start()
        self.addCleanup(engine.stop)
        schema_lessondoc_editor.ensure_lessondoc_editor_schema(self.conn)
        self.conn.execute("CREATE TABLE teachers(id INTEGER PRIMARY KEY)")
        self.conn.execute("INSERT INTO teachers VALUES(9)")
        pack = self._create_pack()["pack"]
        self.pack = pack
        self.conn.commit()
        self.directory = tempfile.TemporaryDirectory(prefix="lessondoc-api-")
        self.addCleanup(self.directory.cleanup)
        for patch in (mock.patch.object(file_service, "GLOBAL_FILES_DIR", Path(self.directory.name) / "blobs"),
                      mock.patch.object(file_service, "GLOBAL_FILES_LEGACY_DIRS", ())):
            patch.start()
            self.addCleanup(patch.stop)
        path = Path(self.directory.name) / "case.sqlite3"
        dest = sqlite3.connect(path)
        self.conn.backup(dest)
        dest.close()
        @contextlib.contextmanager
        def connection():
            db = sqlite3.connect(path)
            db.row_factory = sqlite3.Row
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()
        patch = mock.patch.object(routes, "get_db_connection", connection)
        patch.start()
        self.addCleanup(patch.stop)
        def owner(conn, material_id, user):
            teacher_id = user["id"] if isinstance(user, dict) else user
            row = conn.execute("SELECT * FROM course_materials WHERE id=?", (material_id,)).fetchone()
            if row is None or row["teacher_id"] != teacher_id:
                raise HTTPException(403, "forbidden")
            return dict(row)
        async def load_source(row, **kwargs):
            return self.blob_store[row["file_hash"]], "utf-8"
        async def store_source(digest, payload):
            self.blob_store[digest] = payload.decode("utf-8")
        for patch in (mock.patch.object(library, "get_db_connection", connection),
                      mock.patch.object(library, "ensure_user_material_access", side_effect=owner),
                      mock.patch.object(library, "ensure_teacher_material_owner", side_effect=owner),
                      mock.patch.object(library, "_load_material_text_content", side_effect=load_source),
                      mock.patch.object(library, "_write_material_file", side_effect=store_source),
                      mock.patch.object(library, "_count_global_file_references", return_value=1)):
            patch.start()
            self.addCleanup(patch.stop)
        self.user = {"role": "teacher", "id": 9}
        app = FastAPI()
        app.include_router(routes.router)
        app.include_router(routes.page_router)
        app.include_router(library.router)
        app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.base = f"/api/lessondoc/editor/packs/{pack['id']}"

    def test_editor_page_owner_and_return_url(self):
        response = self.client.get(f"/materials/lessondoc-editor/{self.pack['id']}?lesson=1&return_to=https://example.invalid/")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('id="lessondoc-editor-config"', response.text)
        self.assertNotIn('https://example.invalid/', response.text)
        self.assertIn('"returnUrl": "/manage/materials"', response.text)
        self.assertEqual(response.headers['cache-control'], 'private, no-store')
        self.user = {"role": "teacher", "id": 10}
        self.assertEqual(self.client.get(f"/materials/lessondoc-editor/{self.pack['id']}", follow_redirects=False).status_code, 403)
        self.user = {"role": "student", "id": 9}
        self.assertEqual(self.client.get(f"/materials/lessondoc-editor/{self.pack['id']}", follow_redirects=False).status_code, 403)

    def test_http_read_validate_save_conflict_and_restore(self):
        state = self.client.get(self.base + "/document?lesson_no=1").json()["result"]
        doc = state["document"]
        doc["slides"][1].pop("empty")
        doc["slides"][1]["blocks"] = [{"type": "text", "md": "保存内容"}]
        check = self.client.post(self.base + "/validate?lesson_no=1", json={"document": doc})
        self.assertTrue(check.json()["result"]["valid"])
        body = {"document": doc, "revision": state["revision"], "operation_id": "api_operation_1"}
        response = self.client.put(self.base + "/document?lesson_no=1", json=body)
        self.assertEqual(response.status_code, 200)
        first = response.json()["result"]
        body["operation_id"] = "api_operation_stale"
        self.assertEqual(self.client.put(self.base + "/document?lesson_no=1", json=body).status_code, 409)
        doc["title"] = "新版"
        second = self.client.put(self.base + "/document?lesson_no=1", json={"document": doc, "revision": first["revision"], "operation_id": "api_operation_2"}).json()["result"]
        history = self.client.get(self.base + "/revisions?lesson_no=1").json()["result"]
        restored = self.client.post(self.base + f"/revisions/{history[0]['id']}/restore?lesson_no=1", json={"revision": second["revision"], "operation_id": "api_restore_1"})
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.json()["result"]["document"], first["document"])

    def test_teacher_owner_and_student_boundaries(self):
        self.user = {"role": "teacher", "id": 99}
        self.assertEqual(self.client.get(self.base + "/document?lesson_no=1").status_code, 403)
        self.assertEqual(self.client.get(self.base + "/revisions?lesson_no=1").status_code, 403)
        self.user = {"role": "student", "id": 9}
        self.assertEqual(self.client.get(self.base + "/document?lesson_no=1").status_code, 403)
        self.user = {"role": "teacher", "id": 9}
        self.assertEqual(self.client.get(self.base + "/document?lesson_no=199").status_code, 404)

    def test_trusted_preview_uses_platform_runtime_and_authorized_relative_media(self):
        response = self.client.get(f"/materials/lessondoc-editor/{self.pack['id']}/preview?lesson=1")
        self.assertEqual(response.status_code, 200)
        self.assertIn(f'<base href="/materials/render/{self.pack["root_material_id"]}/lesson_1/">', response.text)
        self.assertIn('src="/static/lessondoc/2.0/interact.js?v=', response.text)
        self.assertNotIn('src="../assets/', response.text)
        self.assertIn("no-store", response.headers["cache-control"])
        self.assertIn("script-src 'self'", response.headers["content-security-policy"])
        self.user = {"role": "student", "id": 9}
        self.assertEqual(self.client.get(f"/materials/lessondoc-editor/{self.pack['id']}/preview", follow_redirects=False).status_code, 403)
        self.user = {"role": "teacher", "id": 99}
        self.assertEqual(self.client.get(f"/materials/lessondoc-editor/{self.pack['id']}/preview", follow_redirects=False).status_code, 403)

    def test_editability_resolves_pending_root_and_nested_lesson_and_blocks_other_owner(self):
        root_id = self.pack["root_material_id"]
        endpoint = f"/api/lessondoc/editor/editability/{root_id}"
        self.assertEqual(self.client.get(endpoint).json()["result"]["kind"], "home")
        state = self.client.get(self.base + "/document?lesson_no=1").json()["result"]
        saved = self.client.put(self.base + "/document?lesson_no=1", json={"document": state["document"], "revision": state["revision"], "operation_id": "new_nested_entry"}).json()["result"]
        nested = self.client.get(f"/api/lessondoc/editor/editability/{saved['material_id']}").json()["result"]
        self.assertEqual(nested["lesson_no"], 1)
        self.assertEqual(nested["pack_id"], self.pack["id"])
        self.assertEqual(self.client.get(endpoint + "?path=../course.json").status_code, 400)
        self.user = {"role": "teacher", "id": 99}
        self.assertEqual(self.client.get(endpoint).status_code, 403)

    def test_raw_stream_upload_budget_type_and_owner_checks(self):
        stream = io.BytesIO()
        Image.new("RGB", (3, 4), "blue").save(stream, format="PNG")
        response = self.client.post(self.base + "/media?filename=picture.png&lesson_no=1", content=stream.getvalue(), headers={"content-type": "image/png"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["result"]["src"].startswith("../assets/media/"))
        self.assertEqual(len(self.client.get(self.base + "/media").json()["result"]["items"]), 1)
        bad = self.client.post(self.base + "/media?filename=picture.png", content=b"not a picture", headers={"content-type": "image/png"})
        self.assertEqual(bad.status_code, 422)
        large = self.client.post(self.base + "/media?filename=picture.png", content=b"x" * (8 * 1024 * 1024 + 1), headers={"content-type": "image/png"})
        self.assertEqual(large.status_code, 413)
        self.user = {"role": "teacher", "id": 99}
        self.assertEqual(self.client.post(self.base + "/media?filename=picture.png", content=stream.getvalue(), headers={"content-type": "image/png"}).status_code, 403)
        self.assertFalse(list(Path(self.directory.name).rglob(".upload-*")))

    def test_custom_element_http_lifecycle_and_scoping(self):
        endpoint = "/api/lessondoc/editor/custom-elements"
        body = {"pack_id": self.pack["id"], "lesson_no": 1, "name": "提示", "element": {"type": "text", "md": "示例"}}
        response = self.client.post(endpoint, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        element_id = response.json()["result"]["id"]
        self.assertEqual(len(self.client.get(endpoint).json()["result"]["items"]), 1)
        inserted = self.client.post(endpoint + f"/{element_id}/insert", json={"pack_id": self.pack["id"], "lesson_no": 1})
        self.assertEqual(inserted.status_code, 200)
        self.assertEqual(inserted.json()["result"]["element"]["md"], "示例")
        self.assertEqual(self.client.put(endpoint + f"/{element_id}", json={"name": "新名称"}).status_code, 200)
        self.user = {"role": "teacher", "id": 99}
        self.assertEqual(self.client.get(endpoint).json()["result"]["items"], [])
        self.assertEqual(self.client.delete(endpoint + f"/{element_id}").status_code, 404)
        self.user = {"role": "teacher", "id": 9}
        self.assertEqual(self.client.delete(endpoint + f"/{element_id}").status_code, 200)

    def test_material_source_editor_uses_versions_and_history_for_registered_document(self):
        state = self.client.get(self.base + "/document?lesson_no=1").json()["result"]
        doc = state["document"]
        doc["slides"][1].pop("empty")
        doc["slides"][1]["blocks"] = [{"type": "text", "md": "正文"}]
        first = self.client.put(self.base + "/document?lesson_no=1", json={"document": doc, "revision": state["revision"], "operation_id": "source_initial"}).json()["result"]
        endpoint = f"/api/materials/{first['material_id']}/content"
        source = self.client.get(endpoint).json()
        from classroom_app.services.lessondoc.render import render_lesson_html
        doc["title"] = "源码修改"
        body = {"content": render_lesson_html(doc), "encoding": "utf-8", "revision": source["material"]["revision"],
                "source_revision": source["material"]["source_revision"], "operation_id": "source_update_1"}
        saved = self.client.put(endpoint, json=body)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(self.client.get(self.base + "/document?lesson_no=1").json()["result"]["document"]["title"], "源码修改")
        self.assertEqual(self.client.get(self.base + "/revisions?lesson_no=1").json()["result"][0]["source"], "source")
        self.assertEqual(self.client.put(endpoint, json=body).status_code, 200)
        body["operation_id"] = "source_late_update"
        self.assertEqual(self.client.put(endpoint, json=body).status_code, 409)
        self.assertEqual(self.client.put(endpoint, json={"content": body["content"]}).status_code, 428)
        updated = saved.json()["material"]
        self.assertEqual(self.client.put(endpoint, json={"content": "<p>lost JSON</p>", "revision": updated["revision"], "source_revision": updated["source_revision"]}).status_code, 422)

    def test_material_source_preserves_generic_asset_editor_and_marks_engine_refresh(self):
        asset = self.conn.execute("SELECT id FROM course_materials WHERE name='course.js'").fetchone()[0]
        endpoint = f"/api/materials/{asset}/content"
        original = self.client.get(endpoint).json()
        body = {"content": original["content"] + "\n// saved through source editor", "revision": original["material"]["revision"], "encoding": "utf-8"}
        response = self.client.put(endpoint, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get(endpoint).json()["content"], body["content"])
        self.assertTrue(self.client.get(self.base + "/document?lesson_no=1").json()["result"]["assets_outdated"])
        self.assertEqual(self.client.put(endpoint, json=body).status_code, 409)

    def test_invalid_json_size_and_untrusted_save_policy(self):
        self.assertEqual(self.client.put(self.base + "/document", content=b"{").status_code, 400)
        self.assertEqual(self.client.put(self.base + "/document", content=b"x" * (routes.MAX_REQUEST_BYTES + 1)).status_code, 413)
        state = self.client.get(self.base + "/document?lesson_no=1").json()["result"]
        state["document"]["slides"][1].pop("empty")
        state["document"]["slides"][1]["blocks"] = [{"type": "hologram", "content": "不能丢掉"}]
        body = {"document": state["document"], "revision": state["revision"], "operation_id": "unsafe_operation", "source": "ai_generate", "allow_loss": True}
        result = self.client.put(self.base + "/document?lesson_no=1", json=body)
        self.assertEqual(result.status_code, 422)
        self.assertEqual(result.json()["error"]["code"], "CONTENT_LOSS")

    def test_ai_element_proposal_cannot_move_restyle_or_publish_content(self):
        state = self.client.get(self.base + "/document?lesson_no=1").json()["result"]
        state["document"]["slides"][1]["blocks"] = [{"type": "text", "id": "target", "md": "原文", "style": {"color": "#123456"}}]
        body = {"document": state["document"], "revision": state["revision"], "element_id": "target", "user_hint": "改进"}
        raw = {"type": "media", "id": "changed", "md": "润色后", "frame": {"x": 999, "y": 999, "w": 99, "h": 99}, "style": {"color": "red"}, "actions": [{"do": "next"}]}
        with mock.patch.object(routes.generate, "_call_lessondoc_ai", new=mock.AsyncMock(return_value=raw)):
            response = self.client.post(self.base + "/ai-proposal?lesson_no=1", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        proposed = response.json()["result"]
        block = proposed["document"]["slides"][1]["blocks"][0]
        self.assertEqual(block["type"], "text")
        self.assertEqual(block["id"], "target")
        self.assertEqual(block["style"]["color"], "#123456")
        self.assertEqual(block["md"], "润色后")
        self.assertNotIn("frame", block)
        self.assertNotIn("actions", block)
        self.assertFalse(proposed["stale"])
        self.assertEqual(self.client.get(self.base + "/document?lesson_no=1").json()["result"]["revision"], "absent")

    def test_feature_switch_blocks_editor_writes_but_keeps_read_and_preview(self):
        with mock.patch.dict("os.environ", {"LESSONDOC_EDITOR_ENABLED": "false"}):
            self.assertEqual(self.client.put(self.base + "/document?lesson_no=1", json={}).status_code, 503)
            self.assertEqual(self.client.get(self.base + "/document?lesson_no=1").status_code, 200)
            self.assertEqual(self.client.get(f"/materials/lessondoc-editor/{self.pack['id']}").status_code, 503)
            self.assertEqual(self.client.get(f"/materials/lessondoc-editor/{self.pack['id']}/preview?lesson=1").status_code, 200)

    def test_ai_late_proposal_reports_conflict_without_overwriting_newer_save(self):
        state = self.client.get(self.base + "/document?lesson_no=1").json()["result"]
        body = {"document": state["document"], "revision": state["revision"], "slide_id": state["document"]["slides"][1]["id"]}
        async def reply(**kwargs):
            state["document"]["title"] = "生成期间的新标题"
            response = self.client.put(self.base + "/document?lesson_no=1", json={"document": state["document"], "revision": state["revision"], "operation_id": "during_ai_proposal"})
            self.assertEqual(response.status_code, 200, response.text)
            return {"layout": "content", "blocks": [{"type": "text", "md": "候选正文"}]}
        with mock.patch.object(routes.generate, "_call_lessondoc_ai", new=reply):
            response = self.client.post(self.base + "/ai-proposal?lesson_no=1", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["result"]["stale"])
        self.assertEqual(self.client.get(self.base + "/document?lesson_no=1").json()["result"]["document"]["title"], "生成期间的新标题")
