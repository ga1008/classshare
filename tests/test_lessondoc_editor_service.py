import copy
import json
from unittest import mock

from classroom_app.db import schema_lessondoc_editor
from classroom_app.services.lessondoc import editor_service as editor, pack_service
from tests.test_lessondoc_service import _PackFixture


class TestEditorSave(_PackFixture):
    def setUp(self):
        super().setUp()
        schema_lessondoc_editor.reset_schema_ready_for_tests()
        self.addCleanup(schema_lessondoc_editor.reset_schema_ready_for_tests)
        patch = mock.patch.object(schema_lessondoc_editor, "get_configured_db_engine", return_value="sqlite")
        patch.start()
        self.addCleanup(patch.stop)
        schema_lessondoc_editor.ensure_lessondoc_editor_schema(self.conn)
        self.pack = self._create_pack()["pack"]
        self.conn.commit()
        self.operation = 0

    def load(self, lesson=1):
        return editor.load_document(self.conn, pack_id=self.pack["id"], teacher_id=9, lesson_no=lesson)

    def save(self, loaded, *, operation=None, commit=True, **kwargs):
        self.operation += 1
        result = editor.save_document(self.conn, pack_id=self.pack["id"], teacher_id=9, lesson_no=loaded["lesson_no"],
                                      document=loaded["document"], expected_revision=loaded["revision"], operation_id=operation or f"operation_{self.operation}", **kwargs)
        if commit:
            self.conn.commit()
        return result

    def fill(self):
        loaded = self.load()
        loaded["document"]["slides"][1].pop("empty", None)
        loaded["document"]["slides"][1]["blocks"] = [{"type": "text", "md": "第一版内容"}]
        return loaded

    def history(self):
        return editor.list_revisions(self.conn, pack_id=self.pack["id"], teacher_id=9, lesson_no=1)

    def test_flow_sizing_persists_through_save_reload_and_history_restore(self):
        initial = self.fill()
        block = initial["document"]["slides"][1]["blocks"][0]
        block.update(id="resizable", natural=dict(w=600, h=120), flowFrame=dict(x=0, y=0, w=300, h=60))
        saved = self.save(initial)
        self.assertEqual(self.load()["document"], saved["document"])
        changed = self.load()
        changed["document"]["slides"][1]["blocks"][0]["flowFrame"]["w"] = 450
        self.save(changed)
        self.assertEqual(self.load()["document"]["slides"][1]["blocks"][0]["flowFrame"]["w"], 450)
        prior = editor.preview_revision(self.conn, pack_id=self.pack["id"], teacher_id=9, lesson_no=1, revision_id=self.history()[0]["id"])
        self.assertEqual(prior["document"]["slides"][1]["blocks"][0]["flowFrame"]["w"], 300)
        restored = editor.restore_revision(self.conn, pack_id=self.pack["id"], teacher_id=9, lesson_no=1,
                                           revision_id=self.history()[0]["id"], expected_revision=self.load()["revision"], operation_id="restore_sizing")
        self.conn.commit()
        self.assertEqual(restored["document"], saved["document"])

    def test_first_save_publishes_and_home_projection_matches(self):
        initial = self.fill()
        self.assertEqual(initial["state"], "absent")
        saved = self.save(initial)
        self.assertNotEqual(saved["revision"], "absent")
        self.assertEqual(self.load()["document"], saved["document"])
        state = self.conn.execute("SELECT gen_status,warnings_json FROM course_doc_pack_lessons WHERE pack_id=? AND lesson_no=1", (self.pack["id"],)).fetchone()
        self.assertEqual(state["gen_status"], "ready")
        self.assertEqual(json.loads(state["warnings_json"]), [])
        manifest = self.load(0)["document"]
        self.assertEqual(manifest["lessons"][0]["status"], "ready")
        self.assertIn("第一版内容", manifest["lessons"][0]["summary"])
        self.assertEqual(self.history(), [])

    def test_empty_draft_does_not_publish_and_excluded_stays_excluded(self):
        self.save(self.load())
        self.assertEqual(self.load(0)["document"]["lessons"][0]["status"], "pending")
        self.conn.execute("UPDATE course_doc_pack_lessons SET gen_status='excluded' WHERE pack_id=? AND lesson_no=1", (self.pack["id"],))
        self.conn.commit()
        self.save(self.fill())
        row = self.conn.execute("SELECT gen_status FROM course_doc_pack_lessons WHERE pack_id=? AND lesson_no=1", (self.pack["id"],)).fetchone()
        self.assertEqual(row["gen_status"], "excluded")

    def test_stale_write_is_rejected_without_history_or_content_change(self):
        stale = self.fill()
        saved = self.save(copy.deepcopy(stale))
        stale["document"]["title"] = "过期提交"
        with self.assertRaises(editor.EditorError) as error:
            self.save(stale)
        self.conn.rollback()
        self.assertEqual(error.exception.code, "REVISION_CONFLICT")
        self.assertEqual(self.load()["revision"], saved["revision"])
        self.assertFalse(self.history())

    def test_clearing_published_lesson_returns_it_to_pending_and_can_restore(self):
        published = self.save(self.fill())
        blank = self.load()
        blank["document"]["slides"][1] = {"id": "s2", "layout": "content", "empty": True, "blocks": []}
        self.save(blank)
        self.assertEqual(self.load(0)["document"]["lessons"][0]["status"], "pending")
        restored = self.load()
        restored["document"] = published["document"]
        self.save(restored)
        self.assertEqual(self.load(0)["document"]["lessons"][0]["status"], "ready")

    def test_retry_is_idempotent_and_reused_token_cannot_change_content(self):
        loaded = self.fill()
        first = self.save(loaded, operation="same_operation")
        again = self.save(loaded, operation="same_operation")
        self.assertTrue(again["replayed"])
        self.assertEqual(again["revision"], first["revision"])
        loaded["document"]["title"] = "different"
        with self.assertRaises(editor.EditorError) as error:
            self.save(loaded, operation="same_operation")
        self.conn.rollback()
        self.assertEqual(error.exception.code, "OPERATION_REUSED")

    def test_noop_does_not_add_history_or_refresh_current_assets(self):
        saved = self.save(self.fill())
        with mock.patch.object(pack_service, "refresh_pack_assets") as refresh:
            result = self.save(saved)
        self.assertTrue(result["unchanged"])
        refresh.assert_not_called()
        self.assertFalse(self.history())

    def test_loss_is_blocked_but_author_deletion_is_allowed(self):
        saved = self.save(self.fill())
        changed = copy.deepcopy(saved)
        changed["document"]["slides"][1]["blocks"].append({"type": "code", "code": ""})
        with self.assertRaises(editor.EditorError) as error:
            self.save(changed)
        self.conn.rollback()
        self.assertEqual(error.exception.code, "CONTENT_LOSS")
        self.assertFalse(self.history())
        saved["document"]["slides"] = saved["document"]["slides"][1:]
        result = self.save(saved)
        self.assertEqual(len(result["document"]["slides"]), 1)

    def test_restore_saves_current_as_another_recoverable_snapshot(self):
        first = self.save(self.fill())
        second_input = copy.deepcopy(first)
        second_input["document"]["title"] = "第二版"
        second = self.save(second_input)
        history = self.history()
        restored = editor.restore_revision(self.conn, pack_id=self.pack["id"], teacher_id=9, lesson_no=1,
                                           revision_id=history[0]["id"], expected_revision=second["revision"], operation_id="restore_operation")
        self.conn.commit()
        self.assertEqual(restored["document"], first["document"])
        snapshot = editor.preview_revision(self.conn, pack_id=self.pack["id"], teacher_id=9, lesson_no=1, revision_id=self.history()[0]["id"])
        self.assertEqual(snapshot["document"]["title"], "第二版")

    def test_projection_failure_rolls_back_document_history_and_receipt(self):
        first = self.save(self.fill())
        changed = copy.deepcopy(first)
        changed["document"]["title"] = "故障版本"
        with mock.patch.object(editor, "_projection", side_effect=RuntimeError("fault")):
            with self.assertRaises(RuntimeError):
                self.save(changed)
        self.conn.rollback()
        self.assertEqual(self.load()["revision"], first["revision"])
        self.assertFalse(self.history())
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM lessondoc_save_operations").fetchone()[0], 1)

    def test_other_teacher_and_other_lesson_revision_are_rejected(self):
        with self.assertRaises(editor.EditorError) as error:
            editor.load_document(self.conn, pack_id=self.pack["id"], teacher_id=99, lesson_no=1)
        self.assertEqual(error.exception.status, 403)
        first = self.save(self.fill())
        first["document"]["title"] = "修改"
        self.save(first)
        with self.assertRaises(editor.EditorError) as error:
            editor.preview_revision(self.conn, pack_id=self.pack["id"], teacher_id=9, lesson_no=2, revision_id=self.history()[0]["id"])
        self.assertEqual(error.exception.status, 404)

    def test_home_save_cannot_change_publication_state(self):
        loaded = self.load(0)
        loaded["document"]["lessons"][1]["status"] = "ready"
        loaded["document"]["course"]["intro"] = "课程简介"
        saved = self.save(loaded)
        self.assertEqual(saved["document"]["lessons"][1]["status"], "pending")
        self.assertEqual(saved["document"]["course"]["intro"], "课程简介")

    def test_corrupt_existing_file_is_not_silently_replaced_by_skeleton(self):
        saved = self.save(self.fill())
        self.blob_store[saved["revision"]] = "broken html"
        with self.assertRaises(editor.EditorError) as error:
            self.load()
        self.assertEqual(error.exception.code, "DOCUMENT_CORRUPT")

    def test_history_is_bounded_to_twenty_previous_versions(self):
        loaded = self.save(self.fill())
        for n in range(23):
            loaded["document"]["title"] = str(n)
            loaded = self.save(loaded)
        self.assertEqual(len(self.history()), 20)

    def test_settings_rename_and_exclusion_keep_projection_and_history_consistent(self):
        first = self.save(self.fill())
        editor.update_settings(self.conn, pack_id=self.pack["id"], teacher_id=9, lesson_no=1, title="管理端改名", excluded=True)
        self.conn.commit()
        self.assertEqual(self.load()["document"]["title"], "管理端改名")
        self.assertEqual(self.load(0)["document"]["lessons"][0]["status"], "pending")
        self.assertEqual(self.history()[0]["revision"], first["revision"])
        editor.update_settings(self.conn, pack_id=self.pack["id"], teacher_id=9, lesson_no=1, excluded=False)
        self.conn.commit()
        self.assertEqual(self.load(0)["document"]["lessons"][0]["status"], "ready")
        self.assertEqual(self.load()["document"]["title"], "管理端改名")

    def test_settings_home_revision_conflicts_with_stale_editor(self):
        stale = self.load(0)
        editor.update_settings(self.conn, pack_id=self.pack["id"], teacher_id=9, theme="sky")
        self.conn.commit()
        self.assertEqual(self.load(0)["document"]["theme"], "sky")
        with self.assertRaises(editor.EditorError) as error:
            self.save(stale)
        self.conn.rollback()
        self.assertEqual(error.exception.code, "REVISION_CONFLICT")

    def test_conditional_material_update_rejects_stale_hash_and_clears_derived_caches(self):
        saved = self.save(self.fill())
        row = pack_service.find_lesson_entry(self.conn, self.pack, 1)
        self.conn.execute("UPDATE course_materials SET ai_parse_status='done',ai_parse_result_json='old',check_questions_json='old' WHERE id=?", (row["id"],))
        saved["document"]["title"] = "更新"
        self.save(saved)
        changed = pack_service.find_lesson_entry(self.conn, self.pack, 1)
        self.assertEqual(changed["ai_parse_status"], "idle")
        self.assertIsNone(changed["ai_parse_result_json"])
        self.assertEqual(changed["check_questions_json"], "")
        with self.assertRaises(pack_service.LessonDocWriteConflict):
            pack_service._update_file_content(self.conn, row["id"], "stale", pack_service._now_iso(), expected_hash=row["file_hash"])
        self.conn.rollback()
        self.assertEqual(self.load()["revision"], changed["file_hash"])

    def test_two_connections_cannot_create_the_same_lesson_twice(self):
        import concurrent.futures
        import sqlite3
        import tempfile
        import threading
        from pathlib import Path
        # File-backed connections exercise SQLite's real busy timeout and commit
        # visibility; shared in-memory cache uses different table-lock semantics.
        with tempfile.TemporaryDirectory(prefix="lessondoc-concurrency-") as directory:
            database = Path(directory) / "case.sqlite3"
            with sqlite3.connect(database) as initial:
                self.conn.backup(initial)
            initial.close()
            barrier = threading.Barrier(2)
            def worker(number):
                conn = sqlite3.connect(database, timeout=5)
                conn.row_factory = sqlite3.Row
                try:
                    loaded = editor.load_document(conn, pack_id=self.pack["id"], teacher_id=9, lesson_no=1)
                    loaded["document"]["slides"][1].pop("empty", None)
                    loaded["document"]["slides"][1]["blocks"] = [{"type": "text", "md": str(number)}]
                    conn.commit()
                    barrier.wait(timeout=5)
                    result = editor.save_document(conn, pack_id=self.pack["id"], teacher_id=9, lesson_no=1, document=loaded["document"],
                                                  expected_revision=loaded["revision"], operation_id=f"parallel_{number}")
                    conn.commit()
                    return result["revision"]
                except editor.EditorError as exc:
                    conn.rollback()
                    return exc.code
                finally:
                    conn.close()
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(worker, (1, 2)))
            self.assertEqual(results.count("REVISION_CONFLICT"), 1)
            verify = sqlite3.connect(database)
            try:
                self.assertEqual(verify.execute("SELECT COUNT(*) FROM course_materials WHERE name='lesson_1.html'").fetchone()[0], 1)
                self.assertEqual(verify.execute("SELECT COUNT(*) FROM lessondoc_save_operations").fetchone()[0], 1)
            finally:
                verify.close()

    def test_late_ai_page_rewrite_cannot_overwrite_manual_edit(self):
        import asyncio
        import contextlib
        from fastapi import HTTPException
        from classroom_app.services.lessondoc import generate
        self.save(self.fill())
        @contextlib.contextmanager
        def connection():
            try:
                yield self.conn
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        async def answer(**kwargs):
            manual = self.load()
            manual["document"]["title"] = "教师并发修改"
            self.save(manual)
            return {"layout": "content", "blocks": [{"type": "text", "md": "迟到的 AI"}]}
        with mock.patch("classroom_app.database.get_db_connection", connection), mock.patch.object(generate, "_call_lessondoc_ai", side_effect=answer):
            with self.assertRaises(HTTPException) as error:
                asyncio.run(generate.rewrite_slide_with_ai(pack_id=self.pack["id"], lesson_no=1, slide_no=2, user_hint="改写"))
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.load()["document"]["title"], "教师并发修改")
        self.assertNotIn("迟到的 AI", json.dumps(self.load()["document"], ensure_ascii=False))

    def test_late_full_generation_does_not_downgrade_saved_lesson_to_failed(self):
        import asyncio
        import contextlib
        from classroom_app.services.lessondoc import generate
        self.conn.execute("UPDATE course_doc_pack_lessons SET gen_status='queued' WHERE pack_id=? AND lesson_no=1", (self.pack["id"],))
        self.conn.commit()
        @contextlib.contextmanager
        def connection():
            try:
                yield self.conn
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        async def answer(**kwargs):
            manual = self.fill()
            manual["document"]["title"] = "教师已保存"
            self.save(manual)
            generated = copy.deepcopy(manual["document"])
            generated["title"] = "过期整课结果"
            return generated
        with mock.patch("classroom_app.database.get_db_connection", connection), mock.patch.object(generate, "build_generation_context", return_value=("guide", "request")), mock.patch.object(generate, "_call_lessondoc_ai", side_effect=answer):
            asyncio.run(generate.run_lessondoc_task(self.pack["id"], 1))
        self.assertEqual(self.load()["document"]["title"], "教师已保存")
        state = self.conn.execute("SELECT gen_status FROM course_doc_pack_lessons WHERE pack_id=? AND lesson_no=1", (self.pack["id"],)).fetchone()
        self.assertEqual(state["gen_status"], "ready")
