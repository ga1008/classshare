"""Offline HTTP failure evidence: full counts, bounded causes, no raw data."""
from __future__ import annotations

from collections import Counter
from contextlib import redirect_stdout
import errno
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

from tools.platform_capacity_ambient import AmbientLoad, exception_diagnostic
from tools import platform_capacity_probe as probe


SECRET = "sensitive-token-password-payload-not-for-report"


def connection_failure():
    native = OSError(errno.ENOBUFS, SECRET)
    native.winerror = 10055
    group = ExceptionGroup(SECRET, [native, ConnectionRefusedError(errno.ECONNREFUSED, SECRET)])
    all_failed = OSError(SECRET)
    all_failed.__cause__ = group
    failure = httpx.ConnectError(SECRET)
    failure.__cause__ = all_failed
    return failure


def state():
    return {"fault_counts": Counter(), "exception_counts": Counter(), "exception_details": []}


class NativeCauseDiagnosticTests(unittest.TestCase):
    def test_nested_anyio_group_retains_native_codes_without_messages_or_url_data(self):
        result = exception_diagnostic(connection_failure(),
            target=f"https://user:{SECRET}@127.0.0.1:18769/private/{SECRET}?token={SECRET}#{SECRET}")
        leaf = result["cause"]["cause"]["exceptions"][0]
        self.assertEqual((leaf["errno"], leaf["winerror"]), (errno.ENOBUFS, 10055))
        self.assertEqual(result["target"], "https://127.0.0.1:18769")
        encoded = json.dumps(result)
        self.assertNotIn(SECRET, encoded)
        self.assertNotIn("/private", encoded)
        self.assertNotIn("?token", encoded)
        self.assertNotIn("user:", encoded)

    def test_context_cycles_depth_and_large_groups_are_bounded(self):
        root = RuntimeError(SECRET)
        root.__context__ = root
        self.assertTrue(exception_diagnostic(root)["context"]["cycle"])
        for _ in range(20):
            wrapper = RuntimeError(SECRET)
            wrapper.__cause__ = root
            root = wrapper
        self.assertIn('"truncated": true', json.dumps(exception_diagnostic(root)))
        group = ExceptionGroup(SECRET, [OSError(errno.ECONNREFUSED, SECRET) for _ in range(100)])
        detail = exception_diagnostic(group)
        self.assertEqual(8, len(detail["exceptions"]))
        self.assertTrue(detail["children_truncated"])
        self.assertLess(len(json.dumps(detail)), 10000)

    def test_diagnostic_never_stringifies_exception_or_copies_untyped_codes(self):
        class NoMessageError(Exception):
            winerror = SECRET
            @property
            def errno(self):
                raise AssertionError("Do not expose arbitrary properties")
            def __str__(self):
                raise AssertionError("Exception messages must never be rendered")
        detail = exception_diagnostic(NoMessageError(SECRET))
        self.assertEqual((None, None), (detail["errno"], detail["winerror"]))
        self.assertNotIn(SECRET, json.dumps(detail))
        self.assertIsNone(exception_diagnostic(ValueError(SECRET), target=f"https://{SECRET}.invalid/path")["target"])

    def test_main_counts_all_exceptions_after_legacy_fault_samples_are_full(self):
        faults = [{"label": "login", "status": 503, "request_id": None} for _ in range(30)]
        previous = list(faults)
        diagnostics = state()
        diagnostics["fault_counts"][("login", "unexpected_status")] = 30
        for index in range(70):
            probe._record_probe_exception(faults, diagnostics, "materials" if index < 45 else "group-write",
                connection_failure(), target="http://127.0.0.1:18767/?secret="+SECRET, phase="action")
        report = probe._fault_summary(diagnostics)
        self.assertEqual((100, 70), (report["fault_count"], report["exception_count"]))
        self.assertEqual(previous, faults)
        self.assertEqual(30, len(report["exception_details"]))
        self.assertEqual({("materials", "ConnectError"): 45, ("group-write", "ConnectError"): 25},
            {(row["flow"], row["error"]): row["count"] for row in report["exception_counts"]})
        self.assertEqual(10055, report["exception_details"][0]["cause"]["cause"]["exceptions"][0]["winerror"])
        self.assertNotIn(SECRET, json.dumps(report))

    def test_main_legacy_exception_keys_stay_available_and_snapshots_do_not_change(self):
        diagnostics, faults = state(), []
        probe._record_probe_exception(faults, diagnostics, "classroom-http-burst", connection_failure(),
            target="http://127.0.0.1:18767", phase="burst")
        first = probe._fault_summary(diagnostics)
        self.assertEqual(("classroom-http-burst", "ConnectError"), (faults[0]["label"], faults[0]["error"]))
        self.assertIsInstance(faults[0]["detail"], str)
        probe._record_probe_exception(faults, diagnostics, "checkpoint-health", RuntimeError(SECRET),
            target="http://127.0.0.1:18767", phase="checkpoint")
        self.assertEqual((1, 1), (first["exception_count"], len(first["exception_details"])))
        self.assertEqual(2, probe._fault_summary(diagnostics)["exception_count"])


class AmbientAndLoginFakeDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def test_ambient_preserves_cadence_and_all_counts_after_30_http_failures(self):
        clock = [100.0]
        starts = []
        class Client:
            base_url = "http://127.0.0.1:18767/"
            async def get(self, *_args, **_kwargs):
                starts.append(clock[0])
                clock[0] += 0.25
                if len(starts) <= 35:
                    return SimpleNamespace(status_code=503)
                raise connection_failure()
        load = AmbientLoad(Client(), ["synthetic-identity"], 42, synthetic=True, clock=lambda: clock[0])
        load.started_at = clock[0]
        waits = [0]
        async def wait(seconds):
            waits[0] += 1
            if waits[0] > 90:
                return True
            clock[0] += max(0, seconds)
            return False
        load._wait = wait
        await load._ticker(0, "bell", 0)
        report = load.snapshot()
        self.assertEqual((90, 90, 90, 55), (report["started_requests"], report["completed_requests"], report["error_count"], report["exception_count"]))
        self.assertEqual(90, sum(row["count"] for row in report["statuses"]))
        self.assertEqual(30, len(report["faults"]))
        self.assertTrue(all(row["error"] == "unexpected_status" for row in report["faults"]))
        self.assertEqual(30, len(report["exception_details"]))
        self.assertEqual(10055, report["exception_details"][0]["cause"]["cause"]["exceptions"][0]["winerror"])
        self.assertTrue(all(abs(b-a-15.25) < 1e-7 for a, b in zip(starts, starts[1:])))
        self.assertNotIn(SECRET, json.dumps(report))

    async def test_failed_login_burst_report_has_complete_exception_counts_without_network(self):
        import psycopg
        calls = []
        class Connection:
            def __enter__(self): return self
            def __exit__(self, *_args): pass
            def execute(self, *_args): return self
            def fetchall(self): return [(f"CAPACITY-SYNTHETIC-{i:04d}", i+1) for i in range(35)]
        class Client:
            def __init__(self, **_kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *_args): pass
            async def post(self, path, **_kwargs):
                calls.append(path)
                raise connection_failure()
        args = SimpleNamespace(duration=1, proxy_port=0, http_port=18767, users=35)
        with TemporaryDirectory(prefix="capacity-fake-diagnostics-") as folder, \
             patch.dict(os.environ, {"DATABASE_URL": "postgresql://127.0.0.1:1/synthetic-never-connected"}), \
             patch.object(psycopg, "connect", return_value=Connection()) as fake_db, \
             patch.object(httpx, "AsyncClient", Client), \
             patch.object(probe.ssl, "create_default_context", return_value=object()), \
             redirect_stdout(io.StringIO()):
            runtime = Path(folder)
            result = await probe.run(args, runtime, {"password": SECRET})
            report = json.loads((runtime/"capacity-report.json").read_text(encoding="utf-8"))
        self.assertEqual(1, result)
        self.assertEqual("failed_login_burst", report["status"])
        self.assertEqual((35, 35, 30, 30), (len(report["exceptions"]), report["exception_count"], len(report["faults"]), len(report["exception_details"])))
        self.assertEqual(35, len(calls), "Diagnostics must not add retry or extra HTTP attempts")
        fake_db.assert_called_once()
        self.assertNotIn(SECRET, json.dumps(report))

    async def test_http_200_then_validation_failure_is_not_another_http_response(self):
        import psycopg
        from jose import jwt
        from tools import platform_capacity_reading as reading
        recorded_responses = []
        class Connection:
            def __enter__(self): return self
            def __exit__(self, *_args): pass
            def execute(self, *_args): return self
            def fetchall(self): return [("CAPACITY-SYNTHETIC-0000", 1)]
        class Response:
            status_code = 200
            headers = {"x-request-id": "00000000-0000-0000-0000-000000000001"}
            cookies = {"access_token": "offline-only-synthetic-token"}
            def __init__(self, body): self.body = body
            def json(self): return self.body
        class Client:
            def __init__(self, **_kwargs): self.cookies = {}
            async def __aenter__(self): return self
            async def __aexit__(self, *_args): pass
            async def post(self, path, **_kwargs):
                recorded_responses.append(path)
                return Response({"status": "success"})
            async def get(self, path, **_kwargs):
                if path == "/api/internal/health":
                    return Response({"status": "ok", "behavior_write_pipeline": {
                        "alive": True, "queue_depth_known": True, "queue_depth": 0}})
                recorded_responses.append(path)
                return Response({})
        args = SimpleNamespace(duration=0.03, rps=100, proxy_port=0, http_port=18767, users=1,
            social=False, reading=True, realtime=False, ambient=False,
            max_api_p95_ms=500, max_page_p95_ms=1000, max_login_p95_ms=2000,
            socket_workers=1, socket_connect_limit=0)
        with TemporaryDirectory(prefix="capacity-fake-validation-") as folder, \
             patch.dict(os.environ, {"DATABASE_URL": "postgresql://127.0.0.1:1/synthetic-never-connected", "SECRET_KEY": "offline-only"}), \
             patch.object(psycopg, "connect", return_value=Connection()), \
             patch.object(httpx, "AsyncClient", Client), \
             patch.object(jwt, "decode", return_value={"role": "student", "id": 1}), \
             patch.object(probe.ssl, "create_default_context", return_value=object()), \
             patch.object(reading, "reading_paths", return_value=[]), \
             patch.object(reading, "verify_reading_response", side_effect=AssertionError(SECRET)), \
             redirect_stdout(io.StringIO()):
            runtime = Path(folder)
            (runtime/"classroom_app").mkdir()
            for name in ("server-source-sha256.json", "server-probe-config.json", "capacity-reading.json"):
                (runtime/name).write_text("{}", encoding="utf-8")
            with patch.object(probe, "ROOT", runtime):
                result = await probe.run(args, runtime, {"password": SECRET, "classOfferingId": 42})
            report = json.loads((runtime/"capacity-report.json").read_text(encoding="utf-8"))
        self.assertEqual((1, "failed"), (result, report["status"]))
        actual_http = sum(row["count"] for row in report["statuses"] if type(row["status"]) is int)
        legacy_workflow_errors = sum(row["count"] for row in report["statuses"] if row["status"] == "AssertionError")
        self.assertEqual(len(recorded_responses), actual_http)
        self.assertEqual(actual_http, report["http_response_count"])
        self.assertGreater(report["exception_count"], 0)
        self.assertEqual(report["exception_count"], legacy_workflow_errors)
        self.assertTrue(all(row["error"] == "AssertionError" for row in report["exception_counts"]))
        self.assertNotIn(SECRET, json.dumps(report))
