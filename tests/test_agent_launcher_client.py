"""Worker control handshake and stdio transport against real Unix socket peers."""
import contextlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from classroom_app.services.agent_runtime import launcher_client


@unittest.skipUnless(hasattr(socket, 'AF_UNIX'), 'Unix socket support required')
class LauncherClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / 'launcher.sock')
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(self.path)
        self.server.listen(1)
        self.evidence = {'status': 'ready', 'dsh_package_version': '0.1.5-rc.1', 'profile_sha256': 'a' * 64,
                         'image': 'sha256:' + 'b' * 64}

    def tearDown(self):
        self.server.close()
        self.temp.cleanup()

    def peer(self, response=None, *, slow=False, echo=False):
        response = self.evidence if response is None else response
        def serve():
            with self.server.accept()[0] as connection:
                file = connection.makefile('rb')
                try:
                    self.received = json.loads(file.readline())
                    data = json.dumps(response).encode() + b'\n'
                    if slow:
                        for byte in data:
                            time.sleep(.02)
                            connection.sendall(bytes([byte]))
                    else:
                        connection.sendall(data)
                    if echo:
                        while line := file.readline(): connection.sendall(line)
                except OSError:
                    pass
                finally:
                    file.close()
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        self.addCleanup(lambda: thread.join(timeout=1))

    def test_probe_validates_immutable_runtime_evidence(self):
        self.peer()
        response = launcher_client.control({'action': 'probe', 'expected_evidence': self.evidence}, socket_path=self.path)
        self.assertEqual(response['profile_sha256'], 'a' * 64)
        self.assertEqual(self.received['version'], 1)

    def test_handshake_rejects_wrong_action_status(self):
        self.peer({'status': 'stopped'})
        with self.assertRaises(RuntimeError):
            launcher_client.control({'action': 'run'}, socket_path=self.path)

    def test_slow_byte_stream_cannot_extend_total_handshake_deadline(self):
        self.peer(slow=True)
        start = time.monotonic()
        with self.assertRaises(TimeoutError):
            launcher_client.connect_launcher({'action': 'probe'}, socket_path=self.path, timeout=.08)
        self.assertLess(time.monotonic() - start, .5)

    def test_real_stdio_child_hides_control_envelope_and_drains_acp(self):
        self.peer(echo=True)
        env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
               'LANSHARE_DSH_LAUNCH_REQUEST': json.dumps({'action': 'run'}),
               'LANSHARE_DSH_LAUNCHER_SOCKET': self.path}
        acp = b'{"jsonrpc":"2.0","id":8,"method":"initialize","params":{}}\n'
        child = subprocess.run([sys.executable, str(Path(launcher_client.__file__).resolve())],
                               env=env, input=acp, capture_output=True, timeout=3)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(child.stdout, acp)
        self.assertEqual(child.stderr, b'')
