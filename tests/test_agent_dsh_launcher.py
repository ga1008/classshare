import tempfile
import contextlib
import hashlib
import http.client
import http.server
import json
import shutil
import socket
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

from tools import agent_dsh_launcher as launcher


class LauncherBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.config = SimpleNamespace(task_root=root / "tasks", profile=root / "profile", socket_root=root / "ipc",
                                      state_root=root / "state", max_concurrency=1, max_runtime_seconds=30,
                                      image="sha256:" + "a" * 64)
        for directory in (self.config.task_root, self.config.profile, self.config.socket_root):
            directory.mkdir()
        self.config.state_root.mkdir()
        (self.config.profile / "package.json").write_text('{}')
        (self.config.profile / "cordis.patch.yml").write_text('[]')
        digest = hashlib.sha256()
        for name in ("package.json", "cordis.patch.yml"):
            digest.update(name.encode() + b'\0')
            digest.update((self.config.profile / name).read_bytes() + b'\0')
        self.evidence = {"dsh_package_version": "0.1.5-rc.1", "profile_sha256": digest.hexdigest()}
        self.request = {"task_id": 7, "attempt_id": str(uuid.uuid4()), "fencing_token": 1, "actor_id": "teacher:7",
                        "model_token": "lsagt_" + "a" * 43, "tools_token": "lsagt_" + "b" * 43,
                        "model": "deepseek-v4-pro", "search_model": "deepseek-flash"}

    def tearDown(self):
        self.temp.cleanup()

    def test_only_explicit_scoped_settings_enter_runner(self):
        value = launcher.approved_environment({**self.request, "SECRET_KEY": "must not enter",
                                               "DATABASE_URL": "must not enter", "argv": ["bad"],
                                               "base_url": "http://host"})
        self.assertNotIn("SECRET_KEY", value)
        self.assertNotIn("DATABASE_URL", value)
        self.assertEqual("teacher:7", value["DSH_ACTOR_ID"])
        self.assertEqual("http://127.0.0.1:8787/api/agent-model", value["DSH_GATEWAY_BASE_URL"])

    def test_identity_and_credential_purpose_are_strict(self):
        for override in ({"task_id": "7"}, {"task_id": True}, {"fencing_token": -1},
                         {"attempt_id": "../../etc"}, {"actor_id": "admin:7"},
                         {"tools_token": self.request["model_token"]}, {"model_token": "real-key"},
                         {"model": "x\nSECRET_KEY=bad"}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                launcher.approved_environment({**self.request, **override})

    def test_container_has_no_network_root_capabilities_or_platform_mounts(self):
        arguments = launcher.container_arguments(self.config, self.request, Path(self.temp.name) / "one.env",
                                                  Path(self.temp.name) / "home")
        self.assertEqual("none", arguments[arguments.index("--network") + 1])
        self.assertEqual("ALL", arguments[arguments.index("--cap-drop") + 1])
        self.assertEqual("10001:10001", arguments[arguments.index("--user") + 1])
        self.assertIn("--read-only", arguments)
        joined = " ".join(arguments)
        for forbidden in ("docker.sock", "docker.env", "--privileged", "/root/.ssh", "SYNTHETIC-MODEL-SECRET"):
            self.assertNotIn(forbidden, joined)
        mounts = [arguments[i + 1] for i, item in enumerate(arguments) if item == "--mount"]
        self.assertEqual(5, len(mounts))
        self.assertTrue(any("gateway.sock" in mount and mount.endswith(",readonly") for mount in mounts))
        self.assertTrue(any('dst=/var/lib/dsh/profiles/lanshare/cordis.yml' in mount
                            and not mount.endswith(',readonly') for mount in mounts))

    def test_mount_path_traversal_and_symlink_are_rejected(self):
        with self.assertRaises(ValueError):
            launcher.safe_child(self.config.task_root, "..", "escape")
        path = self.config.task_root / "link"
        try:
            path.symlink_to(self.config.profile, target_is_directory=True)
        except OSError:
            self.skipTest("OS account does not allow symbolic link creation")
        with self.assertRaises(ValueError):
            launcher.safe_child(self.config.task_root, "link", "secret")

    def test_workspace_quota_counts_regular_files(self):
        (self.config.task_root / "artifact.txt").write_bytes(b"a" * 4096)
        self.assertEqual(4096, launcher.workspace_size(self.config.task_root))

    def test_zero_byte_files_and_directories_are_bounded(self):
        for index in range(5):
            (self.config.task_root / str(index)).touch()
        (self.config.task_root / 'empty-dir').mkdir()
        with patch.object(launcher, 'MAX_WORKSPACE_ENTRIES', 3):
            self.assertEqual(launcher.workspace_usage(self.config.task_root), (0, 4))
            self.assertTrue(launcher.workspace_exceeded(self.config.task_root))

    def test_search_is_explicitly_optional(self):
        self.assertNotIn('DSH_SEARCH_MODEL', launcher.approved_environment({**self.request, 'search_model': ''}))

    def launcher(self):
        with patch.object(launcher, 'docker', return_value=json.dumps(self.evidence).encode()):
            return launcher.Launcher(self.config)

    def test_stop_checks_actor_and_fence_then_confirms_removal(self):
        instance = self.launcher()
        name = launcher.runner_name(7, self.request['attempt_id'])
        labels = {'lanshare.agent.runtime': 'deepseek-dsh', 'lanshare.agent.task': '7',
                  'lanshare.agent.attempt': self.request['attempt_id'], 'lanshare.agent.fence': '1',
                  'lanshare.agent.actor': 'teacher:7'}
        present = {name}
        def control(*args, **kwargs):
            if args[0] == 'ps': return '\n'.join(present).encode()
            if args[0] == 'inspect': return json.dumps([{'Config': {'Labels': labels}}]).encode()
            if args[0] == 'rm': present.clear()
            return b''
        with patch.object(launcher, 'docker', side_effect=control):
            for override in ({'actor_id': 'student:7'}, {'fencing_token': 2}):
                with self.assertRaises(ValueError):
                    instance.stop({**self.request, **override})
            self.assertEqual(instance.stop(self.request)['status'], 'stopped')
            self.assertFalse(present)

    def test_unconfirmed_termination_poison_does_not_acknowledge(self):
        instance = self.launcher()
        with patch.object(launcher, 'docker', return_value=b'still-running\n'):
            with self.assertRaises(RuntimeError):
                instance.remove_confirmed('still-running')
        self.assertTrue(instance.unhealthy.is_set())

    def test_created_or_running_capacity_is_checked_before_home_mutation(self):
        instance = self.launcher()
        (self.config.task_root / 'tasks' / '7').mkdir(parents=True)
        with patch.object(instance, 'names', return_value={'created-or-running'}):
            with self.assertRaisesRegex(ValueError, 'capacity'):
                instance.run(self.request, None)
        self.assertFalse((self.config.state_root / '7').exists())

    def test_image_profile_mismatch_refuses_launcher(self):
        bad = {**self.evidence, 'profile_sha256': 'f' * 64}
        with patch.object(launcher, 'docker', return_value=json.dumps(bad).encode()), self.assertRaises(ValueError):
            launcher.Launcher(self.config)

    def test_actual_node_relay_framing_auth_sse_and_cancellation(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime unavailable')
        result = subprocess.run([node, str(Path(__file__).parent / 'fixtures/dsh_gateway_relay.mjs')],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('passed', result.stdout)


class LauncherGatewayTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        records = self.requests
        class Upstream(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                records.append({'path': self.path, 'body': body, 'headers': self.headers})
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Mcp-Session-Id', 'fixture-mcp')
                self.send_header('Set-Cookie', 'private-cookie')
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
            do_GET = do_POST
        self.upstream = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        self.gateway = http.server.ThreadingHTTPServer(('127.0.0.1', 0), launcher.GatewayHandler)
        self.gateway.upstream_host = '127.0.0.1'
        self.gateway.upstream_port = self.upstream.server_port
        for server in (self.upstream, self.gateway):
            threading.Thread(target=server.serve_forever, daemon=True).start()

    def tearDown(self):
        for server in (self.gateway, self.upstream):
            server.shutdown()
            server.server_close()

    def test_real_http_keeps_search_auth_and_rejects_unlisted_paths(self):
        connection = http.client.HTTPConnection('127.0.0.1', self.gateway.server_port, timeout=3)
        connection.request('POST', '/api/agent-model/messages', '{}', headers={
            'x-api-key': 'fixture-task-key', 'anthropic-version': '2023-06-01', 'Cookie': 'private-cookie'})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader('Mcp-Session-Id'), 'fixture-mcp')
        self.assertIsNone(response.getheader('Set-Cookie'))
        self.assertEqual(response.read(), b'{"ok":true}')
        self.assertEqual(self.requests[0]['headers']['x-api-key'], 'fixture-task-key')
        self.assertEqual(self.requests[0]['headers']['anthropic-version'], '2023-06-01')
        self.assertIsNone(self.requests[0]['headers'].get('Cookie'))
        connection.close()
        for path in ('/admin', '/api/agent-bridge/%2e%2e/admin', '/api/agent-model/messages?key=no'):
            connection = http.client.HTTPConnection('127.0.0.1', self.gateway.server_port, timeout=3)
            connection.request('POST', path, '{}')
            self.assertEqual(connection.getresponse().status, 404)
            connection.close()
        self.assertEqual(len(self.requests), 1)

    def test_questions_use_exact_uuid_paths_and_methods(self):
        question = '/api/agent-bridge/questions/12345678-1234-4234-8234-123456789abc'
        for method, path, expected in (
            ('POST', '/api/agent-bridge/questions', 200), ('GET', question, 200),
            ('POST', question + '/cancel', 200), ('POST', question, 404),
            ('GET', question + '/cancel', 404), ('GET', question + '?token=x', 404),
            ('GET', '/api/agent-bridge/questions/anything', 404),
        ):
            connection = http.client.HTTPConnection('127.0.0.1', self.gateway.server_port, timeout=3)
            connection.request(method, path, '' if method == 'GET' or expected == 404 else '{}')
            self.assertEqual(connection.getresponse().status, expected)
            connection.close()
        self.assertEqual(len(self.requests), 3)

    def test_ambiguous_overlimit_and_truncated_frames_never_reach_upstream(self):
        for headers, body, status in (
            ('Content-Length: 2\r\nContent-Length: 2\r\n', b'{}', b'400'),
            ('Transfer-Encoding: chunked\r\n', b'0\r\n\r\n', b'400'),
            ('Content-Length: 2097153\r\n', b'', b'413'),
            ('Content-Length: 20\r\n', b'{}', b'400'),
        ):
            with self.subTest(headers=headers), socket.create_connection(('127.0.0.1', self.gateway.server_port), timeout=3) as connection:
                connection.sendall(b'POST /api/agent-model/messages HTTP/1.0\r\n' + headers.encode() + b'\r\n' + body)
                connection.shutdown(socket.SHUT_WR)
                self.assertIn(status, connection.recv(1024).split(b'\r\n')[0])
        self.assertEqual(self.requests, [])

    def test_children_only_allow_exact_admit_and_finish_post_paths(self):
        child = '/api/agent-bridge/children/12345678-1234-4234-8234-123456789abc'
        for method, path, status in (
            ('POST', '/api/agent-bridge/children/admit', 200), ('POST', child + '/finish', 200),
            ('GET', '/api/agent-bridge/children/admit', 404), ('GET', child + '/finish', 404),
            ('POST', child + '/finish?task=other', 404), ('POST', child, 404),
            ('POST', '/api/agent-bridge/children/other/finish', 404),
        ):
            connection = http.client.HTTPConnection('127.0.0.1', self.gateway.server_port, timeout=3)
            connection.request(method, path, '{}' if method == 'POST' else '')
            self.assertEqual(status, connection.getresponse().status)
            connection.close()
        self.assertEqual(2, len(self.requests))
