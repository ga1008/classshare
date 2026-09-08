"""Real Git pulls with the material publication boundary observed separately."""

import hashlib
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from contextlib import ExitStack, nullcontext
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

from classroom_app.services import materials_git_service as service
from classroom_app.services.lessondoc import git_sync


@unittest.skipUnless(shutil.which("git"), "Git is required")
class MaterialRepositoryUpdateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="materials-git-update-test-")
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name)
        empty_config = self.base / "empty.gitconfig"
        empty_config.write_text("", encoding="utf-8")
        env = {
            key: value for key, value in os.environ.items()
            if not key.startswith("GIT_") and key not in {"GCM_INTERACTIVE"}
        }
        env.update(GIT_CONFIG_GLOBAL=str(empty_config), GIT_CONFIG_NOSYSTEM="1", LC_ALL="C")
        environment = mock.patch.dict(os.environ, env, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

        self.remote = self.base / "remote.git"
        self.author = self.base / "author"
        self.server = self.base / "server"
        self.published = self.base / "published"
        self.git(self.base, "init", "--bare", "--initial-branch=master", str(self.remote))
        self.git(self.base, "clone", str(self.remote), str(self.author))
        self.commit_file(self.author, "lesson.md", "baseline\n", "initial lesson")
        self.git(self.author, "push", "origin", "master")
        self.git(self.base, "clone", str(self.remote), str(self.server))
        self.root = {"id": 17, "teacher_id": 9, "material_path": "python-course"}
        self.detail = {
            "material_id": 17,
            "remote_url": str(self.remote),
            "remote_name": "origin",
            "default_branch": "master",
            "can_update": True,
        }
        self.conn = mock.MagicMock()
        self.conn.execute.return_value.fetchone.return_value = self.root

    def git(self, cwd, *args):
        completed = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        return completed.stdout.strip()

    def commit_file(self, repo, name, content, message):
        (repo / name).write_text(content, encoding="utf-8")
        self.git(repo, "add", "--", name)
        self.git(repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-m", message)
        return self.git(repo, "rev-parse", "HEAD")

    def push_file(self, name="lesson.md", content="remote lesson\n"):
        head = self.commit_file(self.author, name, content, "remote update")
        self.git(self.author, "push", "origin", "master")
        return head

    def snapshot(self):
        return {
            path.relative_to(self.server).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.server.rglob("*") if path.is_file()
        }

    async def update(self):
        def export(_conn, _root, workspace):
            shutil.copytree(self.server, workspace)

        def publish(_conn, _root, workspace, **_kwargs):
            shutil.copytree(workspace, self.published)
            return {"inserted": 0, "updated": 1, "deleted": 0, "unchanged": 0}, [], []

        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(service, "get_material_repository_detail", return_value=self.detail))
            stack.enter_context(mock.patch.object(service, "_load_saved_git_credential", return_value=None))
            stack.enter_context(mock.patch.object(service, "_export_repository_workspace", side_effect=export))
            stack.enter_context(mock.patch.object(service, "_fetch_subtree_rows", return_value=[]))
            stack.enter_context(mock.patch.object(service, "refresh_root_git_metadata", return_value={}))
            stack.enter_context(mock.patch.object(git_sync, "capture", return_value={}))
            stack.enter_context(mock.patch.object(git_sync, "lock_and_check"))
            self.prepare = stack.enter_context(mock.patch.object(git_sync, "prepare", wraps=git_sync.prepare))
            self.publish = stack.enter_context(mock.patch.object(service, "_sync_workspace_to_repository", side_effect=publish))
            self.run_command = stack.enter_context(mock.patch.object(service, "_run_git_command", wraps=service._run_git_command))
            return await service.execute_material_repository_action(
                lambda: nullcontext(self.conn), 17,
                {"id": 9, "name": "Course Teacher", "email": "teacher@example.test"}, "update",
            )

    def assert_not_published(self, result, before):
        self.prepare.assert_not_called()
        self.publish.assert_not_called()
        self.conn.commit.assert_not_called()
        self.assertFalse(self.published.exists())
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(result["sync_summary"], {"inserted": 0, "updated": 0, "deleted": 0, "unchanged": 0})
        self.assertEqual(result["readme_candidates"], [])

    async def test_divergent_pull_merges_both_histories_and_overrides_imported_pull_settings(self):
        server_head = self.commit_file(self.server, "server-only.md", "server material\n", "server change")
        remote_head = self.push_file()
        self.git(self.server, "config", "pull.ff", "only")
        self.git(self.server, "config", "pull.rebase", "true")
        result = await self.update()
        self.assertEqual(result["status"], "success", result["combined_output"])
        self.assertEqual(self.git(self.published, "rev-list", "--parents", "-n", "1", "HEAD").split()[1:], [server_head, remote_head])
        self.assertEqual((self.published / "lesson.md").read_text(), "remote lesson\n")
        self.assertEqual((self.published / "server-only.md").read_text(), "server material\n")
        self.assertEqual(self.git(self.published, "status", "--porcelain"), "")
        self.assertEqual(self.git(self.published, "log", "-1", "--format=%cn <%ce>"), "Course Teacher <teacher@example.test>")
        command = service._repository_command_strings({"git_remote_name": "origin", "git_default_branch": "master"})["update"]
        self.assertIn(command, result["combined_output"])
        self.publish.assert_called_once()

    async def test_fast_forward_reaches_remote_without_creating_a_merge_commit(self):
        remote_head = self.push_file()
        result = await self.update()
        self.assertEqual(result["status"], "success", result["combined_output"])
        self.assertEqual(self.git(self.published, "rev-parse", "HEAD"), remote_head)
        self.assertEqual((self.published / "lesson.md").read_text(), "remote lesson\n")

    async def test_merge_conflict_does_not_publish_markers_or_fetched_git_state(self):
        self.commit_file(self.server, "lesson.md", "server lesson\n", "server change")
        self.push_file()
        before = self.snapshot()
        result = await self.update()
        self.assertEqual(result["status"], "failed")
        self.assertIn("CONFLICT", result["combined_output"])
        self.assertIn("合并冲突", result["message"])
        self.assert_not_published(result, before)

    async def test_dirty_tracked_material_is_preserved_when_pull_would_overwrite_it(self):
        self.push_file()
        (self.server / "lesson.md").write_text("unsaved server lesson\n", encoding="utf-8")
        before = self.snapshot()
        result = await self.update()
        self.assertEqual(result["status"], "failed")
        self.assertIn("未提交修改", result["message"])
        self.assert_not_published(result, before)

    async def test_untracked_material_is_preserved_when_remote_adds_the_same_path(self):
        self.push_file("new.md", "remote new lesson\n")
        (self.server / "new.md").write_text("server untracked lesson\n", encoding="utf-8")
        before = self.snapshot()
        result = await self.update()
        self.assertEqual(result["status"], "failed")
        self.assertIn("未提交修改", result["message"])
        self.assert_not_published(result, before)

    async def test_unavailable_remote_does_not_publish_identity_or_git_metadata_changes(self):
        self.git(self.server, "remote", "set-url", "origin", str(self.base / "missing.git"))
        before = self.snapshot()
        result = await self.update()
        self.assertEqual(result["status"], "failed")
        self.assertIn("服务器材料保持不变", result["message"])
        self.assert_not_published(result, before)

    async def test_identity_configuration_failure_stops_before_pull(self):
        (self.server / ".git" / "config.lock").write_text("locked", encoding="utf-8")
        before = self.snapshot()
        result = await self.update()
        self.assertEqual(result["status"], "failed")
        self.assertFalse(any(call.args[0][1] == "pull" for call in self.run_command.call_args_list))
        self.assert_not_published(result, before)

    async def test_authentication_failure_keeps_credential_retry_and_skips_publication(self):
        class RequireAuthentication(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="course-test"')
                self.end_headers()

            def log_message(self, *_args):
                pass

        httpd = HTTPServer(("127.0.0.1", 0), RequireAuthentication)
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        try:
            url = f"http://127.0.0.1:{httpd.server_port}/course.git"
            self.git(self.server, "remote", "set-url", "origin", url)
            self.detail["remote_url"] = url
            before = self.snapshot()
            result = await self.update()
            self.assertEqual(result["status"], "auth_required", result["combined_output"])
            self.assertTrue(result["credential_supported"])
            self.assertFalse(result["credential_saved"])
            self.assert_not_published(result, before)
        finally:
            httpd.shutdown()
            httpd.server_close()
            worker.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
