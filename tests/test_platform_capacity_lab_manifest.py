"""Linux attach mode over temporary files, fake transports and blocked PG only."""
from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from jose import jwt

from tools import platform_capacity_probe as probe
from tools import platform_capacity_socket_workers as sockets
from tools.platform_capacity_lab_manifest import (
    LabManifestError, fingerprint, load_lab_manifest, source_hashes, IDENTITY_PATH,
)


PASSWORD = "Synthetic-password-only-2026!"
JWT_KEY = "Synthetic-jwt-key-only-2026-unique"


class LabFiles(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="capacity-attach-offline-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "classroom_app").mkdir()
        (self.root / "classroom_app/auth.py").write_text("# host export\n", encoding="utf-8")
        (self.root / "scheduled_task_dispatcher.py").write_text("# entrypoint\n", encoding="utf-8")
        self.runtime = self.root / ".codex-temp/client-run"
        self.runtime.mkdir(parents=True)
        self.ca = self.runtime / "lab.crt"
        self.ca.write_bytes(b"offline fake certificate; never used for a real connection")
        self.path = self.runtime / "lab-manifest.json"
        self.fixture = {"kind": "capacity-attach-client", "uiV3Synthetic": True,
            "lab_nonce": "a" * 32, "classOfferingId": 42, "password": PASSWORD}
        self.fixture_path = self.runtime / "fixture.json"
        self.fixture_path.write_text(json.dumps(self.fixture), encoding="utf-8")
        client_sources = source_hashes(self.root)
        self.data = {"schema_version": 1, "kind": "capacity-attach-client", "synthetic": True, "uiV3Synthetic": True,
            "lab_nonce": "a" * 32, "release_id": "capacity-linux-offline", "base_url": "https://127.0.0.1:28769",
            "ca_file": str(self.ca), "ca_sha256": hashlib.sha256(self.ca.read_bytes()).hexdigest(),
            "users": [{"identifier": f"CAPACITY-SYNTHETIC-{index:04d}", "student_id": index + 1000} for index in range(200)],
            "offering_id": 42, "synthetic_password": PASSWORD, "synthetic_jwt_secret": JWT_KEY,
            "client_source_sha256": client_sources,
            "server_source_sha256": {name: "b" * 64 for name in client_sources},
            "server_config": {"host": "linux", "workers": 1, "threadpool_tokens": 32, "postgres_pool_max": 12},
            "artifact_sha256": {"fixture.json": hashlib.sha256(self.fixture_path.read_bytes()).hexdigest()}}
        self.identity()
        self.write()
        self.args = SimpleNamespace(action="run", runtime=self.runtime, lab_manifest=self.path,
            cluster_dir=None, pg_port=None, users=200, proxy_port=0, http_port=18767,
            social=False, reading=False, realtime=False, ambient=False, duration=0, rps=40,
            socket_workers=4, socket_connect_limit=8,
            max_api_p95_ms=500, max_page_p95_ms=1000, max_login_p95_ms=2000)

    def identity(self):
        self.data["expected_identity"] = {"schema_version": 1, "synthetic": True,
            "lab_nonce": self.data["lab_nonce"], "release_id": self.data["release_id"],
            "source_fingerprint": fingerprint(self.data["server_source_sha256"]),
            "config_fingerprint": fingerprint(self.data["server_config"]),
            "users_sha256": fingerprint(self.data["users"]), "offering_id": self.data["offering_id"],
            "static_graph_sha256": "c" * 64}

    def write(self):
        self.path.write_text(json.dumps(self.data), encoding="utf-8")

    def load(self):
        return load_lab_manifest(self.args, self.root)

    def test_valid_attachment_is_file_only_has_no_database_path_and_separates_source_snapshots(self):
        with patch("psycopg.connect", side_effect=AssertionError("No DB allowed")) as pg, \
             patch("socket.socket.connect", side_effect=AssertionError("No network allowed")):
            lab = self.load()
        pg.assert_not_called()
        self.assertFalse((self.runtime / "db").exists())
        self.assertNotIn("databasePath", lab.fixture)
        self.assertEqual(200, len(lab.identifiers))
        self.assertNotEqual(lab.client_sources, lab.server_sources)
        self.assertTrue(lab.artifacts_unchanged())
        self.assertNotIn(PASSWORD, json.dumps(lab.evidence()))
        self.assertNotIn(JWT_KEY, json.dumps(lab.evidence()))

    def test_wrong_kind_nonce_user_ids_source_or_config_fingerprint_is_rejected(self):
        original = json.loads(json.dumps(self.data))
        mutations = [lambda d: d.update(kind="native"), lambda d: d.update(lab_nonce="bad"),
            lambda d: d["users"][1].update(student_id=1000), lambda d: d["users"][0].update(identifier="real-person"),
            lambda d: d["client_source_sha256"].update({"classroom_app/auth.py": "0" * 64}),
            lambda d: d["expected_identity"].update(config_fingerprint="0" * 64),
            lambda d: d["server_config"].update(DATABASE_URL="never-report"),
            lambda d: d["expected_identity"].update(synthetic=1)]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.data = json.loads(json.dumps(original))
                mutate(self.data)
                self.write()
                with self.assertRaises(LabManifestError):
                    self.load()

    def test_loopback_https_owned_ca_and_artifact_fingerprints_are_mandatory(self):
        original = json.loads(json.dumps(self.data))
        for value in ("http://127.0.0.1:28769", "https://example.invalid:28769", "https://127.0.0.1:443",
                      "https://u:p@127.0.0.1:28769", "https://127.0.0.1:28769/path", "https://127.0.0.1:28769/?token=x"):
            self.data = json.loads(json.dumps(original)); self.data["base_url"] = value; self.write()
            with self.subTest(origin=value), self.assertRaises(LabManifestError): self.load()
        self.data = original; self.write()
        self.ca.write_bytes(b"changed")
        with self.assertRaises(LabManifestError): self.load()

    def test_native_arguments_or_wrong_action_cannot_select_attach_mode(self):
        for values in ({"action": "seed"}, {"action": "serve"}, {"users": 199}, {"pg_port": 28766},
                       {"cluster_dir": self.runtime}, {"proxy_port": 28769}):
            with self.subTest(values=values), self.assertRaises(LabManifestError):
                load_lab_manifest(SimpleNamespace(**{**vars(self.args), **values}), self.root)

    def test_fixture_manifest_and_ca_tampering_invalidate_parent_pins(self):
        lab = self.load()
        specs = [{"path": "/ws/42", "channel": "classroom", "token": "synthetic-token"}]
        with patch.object(sockets, "ROOT", self.root):
            self.assertEqual(self.runtime, sockets._validate_spec(self.runtime, lab.base_url, specs, self.ca, **lab.socket_options()))
            with self.assertRaises(ValueError):  # Native guard does not silently accept attach metadata.
                sockets._validate_spec(self.runtime, lab.base_url, specs, self.ca)
            self.data["lab_nonce"] = "d" * 32; self.write()
            with self.assertRaises(LabManifestError):
                sockets._validate_spec(self.runtime, lab.base_url, specs, self.ca, **lab.socket_options())
        self.assertFalse(lab.artifacts_unchanged())

    def test_socket_child_guard_rejects_wrong_pins_target_cert_or_channel_without_network(self):
        lab = self.load()
        specs = [{"path": "/ws/42", "channel": "classroom", "token": "synthetic-token"}]
        for values in ({"lab_nonce": "d" * 32}, {"lab_manifest_sha256": "0" * 64}):
            with patch.object(sockets, "ROOT", self.root), self.subTest(values=values), self.assertRaises(LabManifestError):
                sockets._validate_spec(self.runtime, lab.base_url, specs, self.ca, **{**lab.socket_options(), **values})
        with patch.object(sockets, "ROOT", self.root):
            with self.assertRaises(ValueError):
                sockets._validate_spec(self.runtime, "https://127.0.0.1:28770", specs, self.ca, **lab.socket_options())
            with self.assertRaises(ValueError):
                sockets._validate_spec(self.runtime, lab.base_url, [{**specs[0], "path": "/ws/43"}], self.ca, **lab.socket_options())

    def test_manifest_and_ca_outside_client_runtime_and_fake_database_path_are_rejected(self):
        outside = self.root / "outside-manifest.json"
        outside.write_bytes(self.path.read_bytes())
        with self.assertRaises(LabManifestError):
            load_lab_manifest(SimpleNamespace(**{**vars(self.args), "lab_manifest": outside}), self.root)
        outside_ca = self.root / "outside.crt"
        outside_ca.write_bytes(self.ca.read_bytes())
        self.data["ca_file"] = str(outside_ca); self.write()
        with self.assertRaises(LabManifestError): self.load()
        self.data["ca_file"] = str(self.ca)
        self.fixture["databasePath"] = str(self.runtime / "db/classroom.db")
        self.fixture_path.write_text(json.dumps(self.fixture), encoding="utf-8")
        self.data["artifact_sha256"]["fixture.json"] = hashlib.sha256(self.fixture_path.read_bytes()).hexdigest()
        self.write()
        with self.assertRaises(LabManifestError): self.load()

    def test_child_entry_rechecks_same_parent_manifest_before_receiving_any_socket(self):
        from tests.test_platform_capacity_socket_workers import Connection
        lab, connection = self.load(), Connection()
        specs = [{"path": "/ws/42", "channel": "classroom", "token": "synthetic-token"}]
        with patch.object(sockets, "ROOT", self.root), \
             patch.object(sockets, "_install_child_boundary") as boundary, \
             patch.object(sockets, "_receive_sockets", new_callable=AsyncMock) as receive:
            sockets._worker_entry(connection, self.runtime, lab.base_url, specs, self.ca, 8,
                *lab.socket_options().values())
        boundary.assert_called_once_with(self.runtime)
        receive.assert_awaited_once_with(connection, lab.base_url, specs, self.ca, 8)
        self.assertTrue(connection.closed)
        connection = Connection()
        self.path.write_text(self.path.read_text(encoding="utf-8") + " ", encoding="utf-8")
        with patch.object(sockets, "ROOT", self.root), \
             patch.object(sockets, "_install_child_boundary") as boundary, \
             patch.object(sockets, "_receive_sockets", new_callable=AsyncMock) as receive:
            sockets._worker_entry(connection, self.runtime, lab.base_url, specs, self.ca, 8,
                *lab.socket_options().values())
        boundary.assert_not_called(); receive.assert_not_awaited()
        self.assertEqual("fatal", connection.sent[0]["type"])

    def test_dedicated_client_boundary_discards_inherited_credentials_and_blocks_pg_and_dns(self):
        code = '''import json, os, socket, sys
from pathlib import Path
from types import SimpleNamespace
from tools.platform_capacity_lab_manifest import install_client_boundary, LabManifestError
install_client_boundary(SimpleNamespace(runtime=Path(sys.argv[1]),jwt_secret="synthetic-client-key"))
assert os.environ.get("DATABASE_URL")=="" and os.environ.get("DB_ENGINE")=="sqlite"
assert "PROVIDER_API_KEY" not in os.environ and "HTTP_PROXY" not in os.environ
import psycopg
try: psycopg.connect("postgresql://127.0.0.1:1/never-connect")
except RuntimeError: pass
else: raise AssertionError("PG guard missing")
try: socket.getaddrinfo("external.invalid",443)
except LabManifestError: pass
else: raise AssertionError("DNS guard missing")
print(json.dumps({"client_only":True,"pg_blocked":True,"dns_blocked":True}))
'''
        env = dict(os.environ, DATABASE_URL="postgresql://external.invalid/never-connect", DB_ENGINE="postgres",
                   PROVIDER_API_KEY=PASSWORD, HTTP_PROXY="http://external.invalid:1234")
        result = subprocess.run([sys.executable, "-B", "-c", code, str(self.runtime)], cwd=probe.ROOT,
                                env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(json.loads(result.stdout)["pg_blocked"])
        self.assertNotIn(PASSWORD, result.stdout + result.stderr)

    def test_native_cluster_boundary_still_rejects_wrong_physical_pg_identity(self):
        native_fixture = {"uiV3Synthetic": True, "databasePath": str(self.runtime / "db/classroom.db")}
        self.fixture_path.write_text(json.dumps(native_fixture), encoding="utf-8")
        cluster = self.root / ".codex-temp/mock-native-cluster"
        cluster.mkdir(); (cluster / "PG_VERSION").write_text("16", encoding="utf-8")
        wrong_directory = str(self.root / "wrong-cluster")
        class Connection:
            def __enter__(self): return self
            def __exit__(self, *_args): pass
            def execute(self, *_args): return self
            def fetchone(self): return (wrong_directory, "127.0.0.1", "28766")
        with patch.object(probe, "ROOT", self.root), patch.dict(os.environ, {}, clear=False), \
             patch("tools.isolated_environment.guard_dotenv_loading"), \
             patch("psycopg.connect", return_value=Connection()):
            with self.assertRaisesRegex(ValueError, "PostgreSQL identity"):
                probe.establish_boundary(SimpleNamespace(runtime=self.runtime, cluster_dir=cluster, pg_port=28766, http_port=28767))

    def test_cli_requires_native_db_arguments_or_explicit_run_only_lab_manifest(self):
        for arguments in (["run", "--runtime", str(self.runtime)],
                          ["seed", "--runtime", str(self.runtime), "--lab-manifest", str(self.path)],
                          ["run", "--runtime", str(self.runtime), "--lab-manifest", str(self.path), "--pg-port", "28766"]):
            with patch.object(sys, "argv", ["capacity-probe", *arguments]), redirect_stdout(io.StringIO()), \
                 patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit):
                probe.arguments()
        with patch.object(sys, "argv", ["capacity-probe", "run", "--runtime", str(self.runtime), "--lab-manifest", str(self.path)]):
            self.assertEqual(self.path, probe.arguments().lab_manifest)

    def test_actual_controller_handoff_creates_metadata_that_the_attach_loader_accepts(self):
        from tools.platform_capacity_linux_lab import create_client_runtime
        handoff_dir = self.root / ".codex-temp/verified-controller-handoff"
        handoff_dir.mkdir()
        handoff = {key: value for key, value in self.data.items()
                   if key not in {"ca_file", "artifact_sha256", "uiV3Synthetic"}}
        handoff["kind"] = "capacity-linux-controller-handoff"
        (handoff_dir / "handoff.json").write_text(json.dumps(handoff), encoding="utf-8")
        (handoff_dir / "capacity-lab.crt").write_bytes(self.ca.read_bytes())
        (handoff_dir / "capacity-reading.json").write_text(json.dumps({"synthetic": True,
            "material_id": 11, "class_offering_id": 42}), encoding="utf-8")
        (handoff_dir / "capacity-social.json").write_text(json.dumps({"synthetic": True,
            "identifiers": [row["identifier"] for row in self.data["users"]],
            "student_ids": [row["student_id"] for row in self.data["users"]]}), encoding="utf-8")
        runtime = self.root / ".codex-temp/generated-client"
        with patch("psycopg.connect", side_effect=AssertionError("No DB allowed")) as pg:
            result = create_client_runtime(handoff_dir, runtime, root=self.root)
            lab = load_lab_manifest(SimpleNamespace(**{**vars(self.args), "runtime": runtime,
                "lab_manifest": Path(result["manifest"]), "social": True, "reading": True}), self.root)
        pg.assert_not_called()
        self.assertFalse(result["database_created_or_accessed"])
        self.assertFalse((runtime / "db").exists())
        self.assertEqual(self.data["expected_identity"], lab.expected_identity)


class AttachRunTests(LabFiles, unittest.IsolatedAsyncioTestCase):
    async def run_fake(self, *, bad_identity=False, bad_login=False, change_final_identity=False,
                       health_http=200, health_status="ok", wrong_login_release=False):
        lab = self.load()
        self.args._lab = lab
        requests, tls_files = [], []
        identity_calls = [0]
        health = {"status": health_status, "service": "main", "release_id": lab.release_id,
            "behavior_write_pipeline": {"alive": True, "queue_depth_known": True, "queue_depth": 0}}
        class Response:
            status_code = 200
            headers = {"x-lanshare-release": lab.release_id}
            def __init__(self, body, cookies=None): self.body, self.cookies = body, cookies or {}
            def json(self): return self.body
        class Client:
            def __init__(self, **options):
                self.cookies = {}
                self.base_url = options["base_url"]
                if options.get("trust_env") is not False: raise AssertionError("Inherited proxy forbidden")
            async def __aenter__(self): return self
            async def __aexit__(self, *_args): pass
            async def get(self, path, **_options):
                requests.append(("GET", path))
                if path == IDENTITY_PATH:
                    identity_calls[0] += 1
                    body = dict(lab.expected_identity)
                    if bad_identity or change_final_identity and identity_calls[0] > 1:
                        body["lab_nonce"] = "wrong-lab"
                    return Response(body)
                response = Response(health if path == "/api/internal/health" else {})
                if path == "/api/internal/health": response.status_code = health_http
                return response
            async def post(self, path, **options):
                requests.append(("POST", path))
                index = int(options["data"]["identifier"].rsplit("-", 1)[1])
                identity = 1000 + index + (1 if bad_login and index == 0 else 0)
                token = jwt.encode({"role": "student", "id": identity}, JWT_KEY, algorithm="HS256")
                response = Response({"status": "success"}, {"access_token": token})
                if wrong_login_release: response.headers = {"x-lanshare-release": "another-release"}
                return response
        def context(**options):
            tls_files.append(options.get("cafile")); return object()
        with patch.object(probe, "ROOT", self.root), patch.dict(os.environ, {"SECRET_KEY": JWT_KEY}), \
             patch("psycopg.connect", side_effect=AssertionError("Attach mode must never connect to PostgreSQL")) as pg, \
             patch.object(httpx, "AsyncClient", Client), patch.object(probe.ssl, "create_default_context", side_effect=context), \
             redirect_stdout(io.StringIO()):
            result = await probe.run(self.args, self.runtime, self.fixture)
        pg.assert_not_called()
        report = json.loads((self.runtime / "capacity-report.json").read_text(encoding="utf-8"))
        self.assertNotIn(PASSWORD, json.dumps(report)); self.assertNotIn(JWT_KEY, json.dumps(report))
        self.assertEqual([str(self.ca)], tls_files)
        return result, report, requests

    async def test_real_run_path_uses_all_200_manifest_identities_without_pg_or_alternate_load_loop(self):
        result, report, requests = await self.run_fake()
        self.assertEqual((0, "passed"), (result, report["status"]))
        self.assertEqual(200, sum(method == "POST" for method, _path in requests))
        self.assertEqual(IDENTITY_PATH, requests[0][1])
        self.assertEqual(200, report["http_response_count"])
        self.assertTrue(report["controller_final_verification_required"])
        self.assertEqual(self.data["client_source_sha256"], report["client_export_source_sha256"])
        self.assertEqual(self.data["server_source_sha256"], report["server_source_sha256"])
        self.assertFalse(report["source_changed_since_server_start"])

    async def test_identity_mismatch_stops_before_password_or_cookie_is_submitted(self):
        result, report, requests = await self.run_fake(bad_identity=True)
        self.assertEqual((1, "failed_lab_identity"), (result, report["status"]))
        self.assertFalse(any(method == "POST" for method, _ in requests))

    async def test_unready_health_http_or_degraded_body_stops_before_login(self):
        for options in ({"health_http": 503}, {"health_status": "degraded"}):
            with self.subTest(options=options):
                result, report, requests = await self.run_fake(**options)
                self.assertEqual((1, "failed_lab_identity"), (result, report["status"]))
                self.assertFalse(any(method == "POST" for method, _ in requests))

    async def test_wrong_release_login_response_is_not_accepted_even_with_valid_jwt(self):
        result, report, _ = await self.run_fake(wrong_login_release=True)
        self.assertEqual((1, "failed_login_burst"), (result, report["status"]))
        self.assertEqual(0, report["successful_logins"])

    async def test_wrong_login_actor_stays_failed_and_no_retry_is_added(self):
        result, report, requests = await self.run_fake(bad_login=True)
        self.assertEqual((1, "failed_login_burst"), (result, report["status"]))
        self.assertEqual(199, report["successful_logins"])
        self.assertEqual(200, sum(method == "POST" for method, _ in requests))

    async def test_final_identity_change_cannot_receive_passing_client_report(self):
        result, report, _requests = await self.run_fake(change_final_identity=True)
        self.assertEqual((1, "failed"), (result, report["status"]))
        self.assertIn("lab_final_identity_mismatch", report["health_gate_errors"])

    async def test_parent_spawn_keeps_handshake_semantics_and_passes_exact_file_pins_to_every_child(self):
        from tests.test_platform_capacity_socket_workers import Context
        class CapturingContext(Context):
            def __init__(self): super().__init__(); self.arguments = []
            def Process(self, **options):
                self.arguments.append(options["args"])
                return super().Process(**options)
        lab, context = self.load(), CapturingContext()
        specs = [{"token": token, "path": path, "channel": channel} for token in ("a", "b")
                 for path, channel in (("/ws/42", "classroom"), ("/ws/social", "social"))]
        with patch.object(sockets, "ROOT", self.root):
            group = sockets.SocketWorkerGroup(self.runtime, lab.base_url, specs, workers=2, cafile=self.ca,
                connect_limit=8, context=context, **lab.socket_options())
            try:
                await group.start()
                self.assertEqual(2, len(context.arguments))
                for args in context.arguments:
                    self.assertEqual((8, lab.path, lab.file_hashes[lab.path], self.data["lab_nonce"]), args[5:])
            finally:
                await group.stop()
        self.assertTrue(all(process.closed for process in context.processes))


def load_tests(loader, standard_tests, pattern):
    return unittest.TestSuite([LabFiles(name) for name in loader.getTestCaseNames(LabFiles)] +
        [AttachRunTests(name) for name in loader.getTestCaseNames(AttachRunTests) if name in AttachRunTests.__dict__])
