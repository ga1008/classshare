"""Real isolated SQLite/file readers, including blocked ASGI work units.

Each checkout creates a default check_same_thread=True connection in its owner
thread. This deliberately cannot pass by moving an already-open connection.
"""
import asyncio
from contextlib import ExitStack, closing, contextmanager
import hashlib
import inspect
import json
import re
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from fastapi import FastAPI
import httpx

from classroom_app.db import sessions
from classroom_app.db.errors import DatabaseBusyError
from classroom_app.dependencies import get_current_user
from classroom_app.routers.materials_parts import common, exports
from classroom_app.services import user_ui_preferences_service
from classroom_app.services.document_render_service import DocumentRenderService
from classroom_app.services.material_publication_service import record_classroom_material_publication
from classroom_app.services.material_render_grants import issue_material_render_grant
from tests.test_document_render_service import _build_pdf_bytes
from tests.test_session_learning_materials_service import _make_conn


class MaterialReadOffloadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.folder = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.database_path = self.folder / "reader.sqlite"
        self.loop_thread = threading.get_ident()
        self.opened = []
        self.closed = []
        self.sql_threads = []
        self.sql_operations = []
        self.storage_threads = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.returned = threading.Event()
        self.addCleanup(self.release.set)
        self.block_checkout = None
        self.fail_checkout = False
        self.user = {"id": 9, "role": "student", "session_id": "live-reader-session"}
        seed = _make_conn()
        try:
            seed.executescript("""
                ALTER TABLE course_materials ADD COLUMN file_size INTEGER DEFAULT 0;
                ALTER TABLE course_materials ADD COLUMN ai_optimized_markdown TEXT;
                ALTER TABLE course_materials ADD COLUMN ai_parse_result_json TEXT;
                CREATE TABLE classes(id INTEGER PRIMARY KEY, name TEXT);
                CREATE TABLE courses(id INTEGER PRIMARY KEY, name TEXT);
                CREATE TABLE user_ui_preferences(user_role TEXT, user_pk INTEGER,
                    palette_key TEXT, appearance TEXT, glass TEXT, backdrop TEXT,
                    backdrop_color TEXT, version INTEGER, updated_at TEXT);
                INSERT INTO classes VALUES(1,'Synthetic class');
                INSERT INTO courses VALUES(1,'Synthetic course');
                CREATE TABLE user_sessions(session_user_key TEXT PRIMARY KEY, session_id TEXT,
                    user_id TEXT, role TEXT, name TEXT, ip TEXT, last_login TEXT,
                    expires_at TEXT, updated_at TEXT);
                INSERT INTO user_sessions VALUES('student:9','live-reader-session','9','student',
                    'Synthetic','','','2999-01-01','2026-01-01');
            """)
            seed.executemany(
                "INSERT INTO course_materials(id,teacher_id,parent_id,root_id,name,material_path,node_type,preview_type,file_ext,mime_type) "
                "VALUES(?,1,200,200,?,?,'file',?,?,?)",
                [(202, "style.css", "site/style.css", "text", "css", "text/css"),
                 (203, "image.png", "site/image.png", "image", "png", "image/png"),
                 (204, "document.pdf", "site/document.pdf", "pdf", "pdf", "application/pdf")],
            )
            seed.executescript("""
                INSERT INTO course_materials(id,teacher_id,parent_id,root_id,name,material_path,node_type,preview_type)
                VALUES(205,1,200,200,'lesson_2','site/lesson_2','folder','folder');
                INSERT INTO course_materials(id,teacher_id,parent_id,root_id,name,material_path,node_type,preview_type,file_ext,mime_type)
                VALUES(206,1,205,200,'lesson_2.html','site/lesson_2/lesson_2.html','file','text','html','text/html');
            """)
            content = {100: "# 合成材料\n保留 UTF-8 内容".encode(),
                       201: b"<!doctype html><html><head></head><body>synthetic lesson</body></html>",
                       202: b"body { color: red; }", 203: b"synthetic-image",
                       204: _build_pdf_bytes(page_count=1),
                       206: b"<!doctype html><html><body>lesson two</body></html>"}
            for material_id, data in content.items():
                digest = hashlib.sha256(data).hexdigest()
                (self.folder / digest).write_bytes(data)
                seed.execute("UPDATE course_materials SET file_hash=?,file_size=? WHERE id=?", (digest, len(data), material_id))
            for material_id in (100, 200):
                record_classroom_material_publication(seed, material_id, 5, 1)
            seed.commit()
            with closing(sqlite3.connect(self.database_path)) as target:
                seed.backup(target)
        finally:
            seed.close()

        @contextmanager
        def database():
            tid = threading.get_ident()
            self.assertNotEqual(self.loop_thread, tid, "DB checkout ran on the ASGI loop")
            if self.fail_checkout:
                raise DatabaseBusyError("synthetic pressure")
            conn = sqlite3.connect(self.database_path)  # Thread ownership stays enforced.
            conn.row_factory = sqlite3.Row
            def trace(sql):
                self.sql_threads.append(threading.get_ident())
                self.sql_operations.append(sql.lstrip().split(None, 1)[0].upper())
            conn.set_trace_callback(trace)
            self.opened.append((id(conn), tid))
            try:
                if len(self.opened) == self.block_checkout:
                    self.entered.set()
                    if not self.release.wait(3):
                        raise AssertionError("ASGI loop did not release the blocked read")
                yield conn
            finally:
                self.assertEqual(tid, threading.get_ident())
                conn.close()
                self.closed.append((id(conn), tid))
                self.returned.set()

        def storage(digest):
            self.storage_threads.append(threading.get_ident())
            self.assertNotEqual(self.loop_thread, threading.get_ident())
            path = self.folder / digest
            return path if path.is_file() else None

        self.stack.enter_context(patch.object(exports, "get_db_connection", database))
        self.stack.enter_context(patch.object(sessions, "get_db_connection", database))
        self.stack.enter_context(patch.object(user_ui_preferences_service, "get_db_connection", database))
        self.stack.enter_context(patch.object(common, "resolve_global_file_path", storage))
        self.stack.enter_context(patch.object(exports, "document_render_service", DocumentRenderService(root=self.folder / "renders")))
        self.app = FastAPI()
        self.app.include_router(exports.router)

        async def actor():
            return self.user

        self.app.dependency_overrides[get_current_user] = actor

        @self.app.get("/live")
        async def live():
            return {"status": "ok"}

        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://synthetic.test")
        self.addAsyncCleanup(self.client.aclose)

    def mutate(self, sql, parameters=()):
        with closing(sqlite3.connect(self.database_path)) as conn:
            conn.execute(sql, parameters)
            conn.commit()

    async def blocked_request(self, method, path, *, checkout_offset=1, **kwargs):
        self.entered.clear()
        self.release.clear()
        self.block_checkout = len(self.opened) + checkout_offset
        pending = asyncio.create_task(self.client.request(method, path, **kwargs))
        try:
            self.assertTrue(await asyncio.to_thread(self.entered.wait, 1))
            self.assertFalse(pending.done())
            self.assertEqual(200, (await asyncio.wait_for(self.client.get("/live"), .5)).status_code)
            self.assertFalse(pending.done())
        finally:
            self.release.set()
        response = await pending
        self.assertCountEqual(self.opened, self.closed)
        self.assertTrue(all(tid != self.loop_thread for tid in self.sql_threads))
        return response

    async def test_real_readers_leave_loop_responsive_and_keep_connections_in_owner_thread(self):
        grant = issue_material_render_grant(200, self.user)
        endpoints = [
            ("GET", "/materials/view/100", {}),
            ("GET", "/materials/render-view/200", {}),
            ("GET", "/materials/raw/203", {}),
            ("GET", "/materials/download/100", {}),
            ("GET", "/materials/inline/204", {}),
            ("GET", "/materials/render/200/style.css", {}),
            ("GET", f"/materials/render-content/{grant}/style.css", {}),
            ("GET", "/materials/document-preview/204", {}),
            ("POST", "/api/materials/download", {"json": {"material_ids": [100, 100]}}),
        ]
        for method, path, kwargs in endpoints:
            with self.subTest(path=path):
                response = await self.blocked_request(method, path, **kwargs)
                self.assertEqual(200, response.status_code, response.text[:300])
                if path == "/materials/view/100":
                    payload = json.loads(response.text.split("window.MATERIAL_VIEWER = ", 1)[1].split(";", 1)[0])
                    self.assertIn("合成材料", payload["content"])
        self.assertTrue(self.storage_threads)
        self.assertFalse(set(self.sql_operations) & {"INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "REPLACE"})

    async def test_isolated_html_disk_read_stays_off_loop_after_returning_db_connections(self):
        grant = issue_material_render_grant(200, self.user)
        original = Path.read_bytes
        disk_threads = []

        def read(path):
            if path.parent == self.folder:
                disk_threads.append(threading.get_ident())
                self.entered.set()
                if not self.release.wait(3):
                    raise AssertionError("HTML read blocked the event loop")
            return original(path)

        with patch.object(Path, "read_bytes", read):
            pending = asyncio.create_task(self.client.get(f"/materials/render-content/{grant}/index.html"))
            try:
                self.assertTrue(await asyncio.to_thread(self.entered.wait, 1))
                self.assertCountEqual(self.opened, self.closed)
                self.assertFalse(pending.done())
                self.assertEqual(200, (await asyncio.wait_for(self.client.get("/live"), .5)).status_code)
            finally:
                self.release.set()
            response = await pending
        self.assertEqual(200, response.status_code)
        self.assertIn(b"material_render_frame.js", response.content)
        self.assertTrue(disk_threads)
        self.assertNotIn(self.loop_thread, disk_threads)

    async def test_verified_unavailable_fallback_and_template_are_also_off_loop(self):
        for route in ("view", "render-view", "document-preview"):
            response = await self.blocked_request(
                "GET", f"/materials/{route}/999?class_offering_id=5&session_id=10&classroom_reader_tab=1",
                checkout_offset=2,
            )
            self.assertEqual(404, response.status_code)
            self.assertIn('href="/classroom/5"', response.text)
            self.assertIn("L1", response.text)
            self.assertIn("no-store", response.headers["cache-control"])

    async def test_live_grant_parent_asset_acl_etag_and_range_remain_authoritative(self):
        grant = issue_material_render_grant(200, self.user)
        url = f"/materials/render-content/{grant}/style.css"
        first = await self.client.get(url)
        self.assertEqual(200, first.status_code)
        self.assertEqual("null", first.headers["access-control-allow-origin"])
        self.assertIn("sandbox", first.headers["content-security-policy"])
        self.assertEqual(304, (await self.client.get(url, headers={"If-None-Match": first.headers["etag"]})).status_code)
        ranged = await self.client.get(url, headers={"Range": "bytes=0-3"})
        self.assertEqual(206, ranged.status_code)
        self.assertEqual(b"body", ranged.content)
        self.assertEqual("bytes 0-3/20", ranged.headers["content-range"])
        html = await self.client.get(f"/materials/render-content/{grant}/index.html")
        self.assertEqual(200, html.status_code)
        self.assertIn(b"material_render_frame.js", html.content)
        self.assertIn("no-store", html.headers["cache-control"])

        # A child independently restricted after the first response is denied
        # even with a matching validator, while the package itself is readable.
        self.mutate("INSERT INTO material_ai_import_records(id,document_group,parsed_material_id) VALUES(1,'final_material',202)")
        self.assertEqual(403, (await self.client.get(url, headers={"If-None-Match": "*"})).status_code)
        self.mutate("DELETE FROM material_ai_import_records")
        self.mutate("DELETE FROM course_material_assignments WHERE material_id=200")
        self.mutate("INSERT INTO course_material_assignments(material_id,class_offering_id,publication_mode) VALUES(202,5,'explicit')")
        self.assertEqual(403, (await self.client.get(url)).status_code, "Asset permission cannot replace package permission")
        self.mutate("INSERT INTO course_material_assignments(material_id,class_offering_id,publication_mode) VALUES(200,5,'explicit')")
        self.mutate("UPDATE user_sessions SET session_id='new-session' WHERE session_user_key='student:9'")
        revoked = await self.client.get(url, headers={"If-None-Match": "*"})
        self.assertEqual(403, revoked.status_code)
        self.assertNotIn("location", revoked.headers)
        self.assertNotIn("set-cookie", revoked.headers)

    async def test_storage_and_decode_async_adapter_keep_existing_await_interface(self):
        with closing(sqlite3.connect(self.database_path)) as conn:
            conn.row_factory = sqlite3.Row
            material = dict(conn.execute("SELECT * FROM course_materials WHERE id=100").fetchone())
        text, encoding = await common._load_material_text_content(material)
        self.assertEqual("# 合成材料\n保留 UTF-8 内容", text)
        self.assertEqual("utf-8", encoding)
        self.assertTrue(all(tid != self.loop_thread for tid in self.storage_threads))

    async def test_html_package_lesson_navigation_and_verified_classroom_return_are_preserved(self):
        response = await self.client.get(
            "/materials/render-view/200?path=lesson_2/lesson_2.html&class_offering_id=5&session_id=10&classroom_reader_tab=1",
        )
        self.assertEqual(200, response.status_code)
        self.assertIn("isHtmlPackage: true", response.text)
        self.assertIn("lessonNumber: 2", response.text)
        source = json.loads(re.search(r"iframeSrc:\s*(\"[^\"]+\")", response.text)[1])
        self.assertTrue(source.startswith("/materials/render-content/"))
        self.assertTrue(source.endswith("/lesson_2/lesson_2.html"))
        content = await self.client.get(source)
        self.assertEqual(200, content.status_code)
        self.assertIn(b"lesson two", content.content)
        self.assertIn(b"material_render_frame.js", content.content)
        reader_return = json.loads(response.text.split("window.MATERIAL_READER_RETURN = ", 1)[1].split(";", 1)[0])
        self.assertEqual("/classroom/5", reader_return["url"])
        self.assertTrue(reader_return["close_tab"])
        # A direct iframe entry is still redirected into the opaque grant route.
        redirected = await self.client.get("/materials/render/200/lesson_2/lesson_2.html", headers={"Sec-Fetch-Dest": "iframe"})
        self.assertEqual(307, redirected.status_code)
        self.assertTrue(redirected.headers["location"].startswith("/materials/render-content/"))

    async def test_pressure_is_not_authentication_failure_or_unavailable_return_page(self):
        self.fail_checkout = True
        grant = issue_material_render_grant(200, self.user)
        for path in (f"/materials/render-content/{grant}/style.css", "/materials/view/100?class_offering_id=5"):
            with self.subTest(path=path), self.assertRaises(DatabaseBusyError):
                await self.client.get(path)

    async def test_cancelled_request_still_returns_its_own_connection(self):
        self.block_checkout = 1
        pending = asyncio.create_task(self.client.get("/materials/download/100"))
        try:
            self.assertTrue(await asyncio.to_thread(self.entered.wait, 1))
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await pending
        finally:
            self.release.set()
        self.assertTrue(await asyncio.to_thread(self.returned.wait, 1))
        self.assertCountEqual(self.opened, self.closed)

    async def assert_cancelled_zip_is_removed(self, *, worker_finished):
        created = threading.Event()
        release_worker = threading.Event()
        cleanup_entered = threading.Event()
        release_cleanup = threading.Event()
        cleaned = threading.Event()
        delivered = asyncio.Event()
        never_deliver = asyncio.Event()
        paths = []
        cleanup_calls = []
        original_zip = exports._create_material_zip
        original_cleanup = exports._cleanup_temp_file
        original_runner = exports.run_in_threadpool

        def create_zip(rows):
            path = original_zip(rows)
            paths.append(Path(path))
            created.set()
            if not worker_finished and not release_worker.wait(3):
                raise AssertionError("Cancelled ZIP worker was not released")
            return path

        def cleanup(path):
            cleanup_calls.append(threading.get_ident())
            cleanup_entered.set()
            if worker_finished and not release_cleanup.wait(3):
                raise AssertionError("Accepted cleanup was not released")
            original_cleanup(path)
            cleaned.set()

        async def delayed_delivery(operation, *args, **kwargs):
            result = await original_runner(operation, *args, **kwargs)
            if worker_finished:
                delivered.set()
                await never_deliver.wait()
            return result

        with (patch.object(exports, "_create_material_zip", create_zip),
              patch.object(exports, "_cleanup_temp_file", cleanup),
              patch.object(exports, "run_in_threadpool", delayed_delivery)):
            pending = asyncio.create_task(self.client.post("/api/materials/download", json={"material_ids": [100]}))
            try:
                self.assertTrue(await asyncio.to_thread(created.wait, 1))
                self.assertTrue(paths[0].is_file())
                if worker_finished:
                    await asyncio.wait_for(delivered.wait(), 1)
                pending.cancel()
                if worker_finished:
                    self.assertTrue(await asyncio.to_thread(cleanup_entered.wait, 1))
                    # A repeated cancel must not discard an accepted cleanup,
                    # including while the response awaits transfer to ASGI.
                    pending.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await pending
            finally:
                release_worker.set()
                release_cleanup.set()
            self.assertTrue(await asyncio.to_thread(cleaned.wait, 1))
        self.assertFalse(paths[0].exists())
        self.assertEqual(1, len(cleanup_calls))
        self.assertNotIn(self.loop_thread, cleanup_calls)
        self.assertCountEqual(self.opened, self.closed)

    async def test_cancel_before_zip_worker_finishes_removes_only_its_temporary_export(self):
        await self.assert_cancelled_zip_is_removed(worker_finished=False)

    async def test_cancel_after_zip_result_before_delivery_and_second_cancel_still_cleans_once(self):
        await self.assert_cancelled_zip_is_removed(worker_finished=True)

    def test_public_callers_keep_async_signatures_and_fastapi_parameters(self):
        for name in ("material_viewer_page", "material_render_shell_page", "material_document_preview_page",
                     "get_material_raw", "download_material", "inline_material", "render_isolated_material",
                     "batch_download_materials", "export_ai_import_record", "preview_ai_import_record_export",
                     "export_ai_import_material"):
            with self.subTest(name=name):
                self.assertTrue(inspect.iscoroutinefunction(getattr(exports, name)))
                parameters = inspect.signature(getattr(exports, name)).parameters
                self.assertIn("grant" if name == "render_isolated_material" else "user", parameters)


if __name__ == "__main__":
    unittest.main()
