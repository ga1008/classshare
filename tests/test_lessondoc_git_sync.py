import hashlib
import json
import tempfile
from pathlib import Path
from unittest import mock

from classroom_app.db import schema_lessondoc_editor
from classroom_app.services import materials_git_service as git
from classroom_app.services.lessondoc import editor_service as editor, git_sync, pack_service, render
from tests.test_lessondoc_service import _PackFixture, _deck


class TestLessonDocGitSync(_PackFixture):
    def setUp(self):
        super().setUp()
        schema_lessondoc_editor.reset_schema_ready_for_tests()
        self.addCleanup(schema_lessondoc_editor.reset_schema_ready_for_tests)
        schema_lessondoc_editor.ensure_lessondoc_editor_schema(self.conn)
        self.conn.execute("CREATE TABLE course_files(file_hash TEXT)")
        self.pack = self._create_pack()["pack"]
        self.first = editor.save_document(self.conn, pack_id=self.pack["id"], teacher_id=9, lesson_no=1, document=_deck(),
                                          expected_revision="absent", operation_id="initial_save")
        self.conn.commit()
        self.root = dict(self.conn.execute("SELECT * FROM course_materials WHERE id=?", (self.pack["root_material_id"],)).fetchone())
        self.directory = tempfile.TemporaryDirectory(prefix="lessondoc-git-")
        self.addCleanup(self.directory.cleanup)
        self.workspace = Path(self.directory.name) / "workspace"
        self.baseline = git_sync.capture(self.conn, self.root, git._fetch_subtree_rows(self.conn, self.root))
        with mock.patch.object(git, "_load_file_bytes", side_effect=lambda value: self.blob_store[value].encode("utf-8")):
            git._export_repository_workspace(self.conn, self.root, self.workspace)

    def _sync(self):
        prepared = git_sync.prepare(self.workspace, self.baseline)
        git_sync.lock_and_check(self.conn, self.root, git._fetch_subtree_rows, self.baseline)
        def store(payload):
            digest = hashlib.sha256(payload).hexdigest()
            self.blob_store[digest] = payload.decode("utf-8")
            return digest, len(payload)
        with mock.patch.object(git, "_store_bytes_globally", side_effect=store):
            git._sync_workspace_to_repository(self.conn, self.root, self.workspace, protected_paths=prepared["protected"])
        result = git_sync.apply(self.conn, prepared)
        self.conn.commit()
        return result

    def _load(self):
        return editor.load_document(self.conn, pack_id=self.pack["id"], teacher_id=9, lesson_no=1)

    def test_git_change_uses_unified_history_publication_and_cache(self):
        (self.workspace / "lesson_1/lesson_1.html").write_text(render.render_lesson_html(_deck(title="Git 新内容")), encoding="utf-8")
        self._sync()
        self.assertEqual(self._load()["document"]["title"], "Git 新内容")
        home = editor.load_document(self.conn, pack_id=self.pack["id"], teacher_id=9)["document"]
        self.assertEqual(home["lessons"][0]["title"], "Git 新内容")
        history = editor.list_revisions(self.conn, pack_id=self.pack["id"], teacher_id=9, lesson_no=1)
        self.assertEqual(history[0]["source"], "git")
        self.assertEqual(history[0]["revision"], self.first["revision"])
        pack = pack_service.get_pack(self.conn, self.pack["id"])
        self.assertEqual(pack["manifest_cache"], home)

    def test_git_network_baseline_cannot_overwrite_later_manual_save(self):
        latest = self._load()
        latest["document"]["title"] = "在线修改"
        editor.save_document(self.conn, pack_id=self.pack["id"], teacher_id=9, lesson_no=1, document=latest["document"],
                             expected_revision=latest["revision"], operation_id="manual_later")
        self.conn.commit()
        (self.workspace / "lesson_1/lesson_1.html").write_text(render.render_lesson_html(_deck(title="迟到的 Git")), encoding="utf-8")
        with self.assertRaises(editor.EditorError) as error:
            self._sync()
        self.conn.rollback()
        self.assertEqual(error.exception.code, "GIT_REVISION_CONFLICT")
        self.assertEqual(self._load()["document"]["title"], "在线修改")

    def test_invalid_git_document_and_registered_delete_never_mutate_database(self):
        path = self.workspace / "lesson_1/lesson_1.html"
        path.write_text("<html>lost data</html>", encoding="utf-8")
        with self.assertRaises(editor.EditorError):
            self._sync()
        self.assertEqual(self._load()["revision"], self.first["revision"])
        path.unlink()
        with self.assertRaises(editor.EditorError) as error:
            self._sync()
        self.assertEqual(error.exception.code, "REGISTERED_DOCUMENT_REMOVED")

    def test_changed_untrusted_shell_is_rebuilt_from_json(self):
        path = self.workspace / "lesson_1/lesson_1.html"
        path.write_text(path.read_text(encoding="utf-8").replace("</body>", '<script>window.evil=true</script></body>'), encoding="utf-8")
        self._sync()
        saved = self._load()
        self.assertNotIn("window.evil", self.blob_store[saved["revision"]])

    def test_home_and_lesson_git_edits_merge_with_current_ready_states(self):
        path = self.workspace / "course.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["course"]["intro"] = "Git 修改简介"
        manifest["lessons"][1]["status"] = "ready"
        path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        self._sync()
        home = editor.load_document(self.conn, pack_id=self.pack["id"], teacher_id=9)["document"]
        self.assertEqual(home["course"]["intro"], "Git 修改简介")
        self.assertEqual(home["lessons"][1]["status"], "pending")
