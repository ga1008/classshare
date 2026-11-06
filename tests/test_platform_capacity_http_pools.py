"""Offline pool budgets, wire-level identity routing and owned cleanup."""
from __future__ import annotations

import asyncio
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import ssl
import tempfile
from types import SimpleNamespace
from urllib.parse import parse_qs
import unittest
from unittest.mock import patch

import httpx
from jose import jwt

from tools import platform_capacity_probe as probe
from tools.platform_capacity_http_pools import ShardedCapacityClient, pool_budget
from tools.platform_capacity_lab_manifest import LabManifest
from tools.platform_capacity_ambient import AmbientLoad
from tools.platform_capacity_social import social_action

ORIGIN = "https://127.0.0.1:28769"


class BudgetTests(unittest.TestCase):
    def test_all_supported_shards_partition_the_same_physical_budgets(self):
        for count in (1, 2, 3, 7, 16):
            with self.subTest(count=count):
                rows = pool_budget(count)["pools"]
                self.assertEqual(sum(row["max_connections"] for row in rows), 400)
                self.assertEqual(sum(row["max_keepalive_connections"] for row in rows), 200)
                self.assertTrue(all(0 < row["max_keepalive_connections"] <= row["max_connections"] for row in rows))
                self.assertLessEqual(max(row["max_connections"] for row in rows)-min(row["max_connections"] for row in rows), 1)
        self.assertEqual({25}, {row["max_connections"] for row in pool_budget(16)["pools"]})
        for value in (0, 17, True, 1.5):
            with self.assertRaises(ValueError): pool_budget(value)

    def test_cli_defaults_to_original_pool_and_exposes_only_explicit_variant(self):
        argv = ["probe", "run", "--runtime", ".codex-temp/fixture", "--cluster-dir", ".codex-temp/pg", "--pg-port", "18766"]
        with patch("sys.argv", argv): self.assertEqual(probe.arguments().http_pool_shards, 1)
        with patch("sys.argv", argv+["--http-pool-shards", "16"]): self.assertEqual(probe.arguments().http_pool_shards, 16)


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_unconnected_httpx_pools_enforce_aggregate_budgets_and_one_tls_context(self):
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        async with ShardedCapacityClient(base_url=ORIGIN, verify=tls, shards=16) as client:
            pools = [item._transport._pool for item in client._clients]
            self.assertEqual(sum(pool._max_connections for pool in pools), 400)
            self.assertEqual(sum(pool._max_keepalive_connections for pool in pools), 200)
            self.assertEqual({pool._max_connections for pool in pools}, {25})
            self.assertTrue(all(pool._ssl_context is tls and pool._keepalive_expiry == 5 for pool in pools))
            self.assertTrue(all(not pool.connections for pool in pools), "Construction must not open sockets")
        self.assertTrue(all(item.is_closed for item in client._clients))

    async def test_200_actors_get_post_and_social_use_same_pool_with_original_timeouts_and_tls(self):
        clients, configurations, wire = [], [], []
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        async def app(scope, receive, send, shard):
            body = b""
            while True:
                item = await receive(); body += item.get("body", b"")
                if not item.get("more_body"): break
            cookie = httpx.Headers(scope["headers"]).get("cookie")
            wire.append((shard, scope["path"], scope["method"], cookie, body))
            payload = {"pool": shard}
            path = scope["path"]
            if path.endswith("/messages"):
                payload = ({"id": 41, "sender": {"identity": "student:1"}, "conversation_key": "dm:teacher:9:student:1"}
                           if scope["method"] == "POST" else {"items": [{"id": 41}]})
            elif path.endswith("/read"): payload = {"last_message_id": 41}
            await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": json.dumps(payload).encode()})
        def factory(**options):
            shard = len(clients)
            configurations.append(options)
            async def scoped(scope, receive, send): return await app(scope, receive, send, shard)
            async def timeout_check(request):
                self.assertEqual(request.extensions["timeout"], {key: 30 for key in ("connect", "read", "write", "pool")})
            client = httpx.AsyncClient(**options, transport=httpx.ASGITransport(app=scoped), event_hooks={"request": [timeout_check]})
            clients.append(client)
            return client
        tokens = [f"synthetic-token-{number}" for number in range(200)]
        async with ShardedCapacityClient(base_url=ORIGIN, verify=tls, shards=16, client_factory=factory) as client:
            self.assertEqual((await client.get("/api/internal/health")).json()["pool"], 0)
            client.bind_tokens(tokens)
            client.cookies.clear()
            async def actor(index):
                headers = {"Cookie": "access_token="+tokens[index]}
                for method, path in (("GET", "/dashboard"), ("POST", "/api/classrooms/7/behavior/batch")):
                    response = await client.request(method, path, headers=headers, **({"json": {"actor": index}} if method == "POST" else {}))
                    self.assertEqual(response.json()["pool"], index % 16)
            await asyncio.gather(*(actor(index) for index in range(200)))
            await social_action(client, tokens[0], 0, 1, {"synthetic": True, "student_ids": [1], "group_student_count": 0,
                "dm_keys": ["dm:teacher:9:student:1"], "nonce_prefix": "owned"}, lambda *args: None, kind="dm")
            self.assertEqual((await client.get("/api/internal/health", headers={"Cookie": "access_token="+tokens[31]})).json()["pool"], 0)
        self.assertTrue(all(client.is_closed for client in clients))
        self.assertEqual(len(configurations), 16)
        self.assertTrue(all(options["verify"] is tls and options["trust_env"] is False for options in configurations))
        self.assertEqual(sum(options["limits"].max_connections for options in configurations), 400)
        self.assertEqual(sum(options["limits"].max_keepalive_connections for options in configurations), 200)
        for shard, path, method, cookie, body in wire:
            if path not in {"/api/internal/health"}:
                self.assertEqual(shard, int(cookie.rsplit("-", 1)[1]) % 16)
        posts = [row for row in wire if row[1].endswith("/messages") and row[2] == "POST"]
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0][4], posts[1][4], "Original same-nonce concurrent send/retry bodies remain identical")

    async def test_missing_duplicate_unknown_and_cross_origin_identity_fail_before_dispatch(self):
        calls = []
        def factory(**options):
            return httpx.AsyncClient(**options, transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(200)))
        async with ShardedCapacityClient(base_url=ORIGIN, verify=False, shards=2, client_factory=factory) as client:
            with self.assertRaises(ValueError): await client.get("/dashboard", headers={"Cookie": "access_token=private-unbound"})
            client.bind_tokens(["private-bound"])
            cases = [("/dashboard", {}), ("/dashboard", {"headers": {"Cookie": "access_token=private-unknown"}}),
                ("/api/internal/health", {"headers": {"Cookie": "access_token=private-unknown"}}),
                ("/dashboard", {"headers": [("Cookie", "access_token=private-bound"), ("cookie", "private-extra")]}),
                ("/dashboard", {"headers": {"Cookie": "access_token=private-bound; private=extra"}}),
                ("https://external.invalid/private", {"headers": {"Cookie": "access_token=private-bound"}}),
                ("//external.invalid/private", {"headers": {"Cookie": "access_token=private-bound"}}),
                ("/dashboard", {"cookies": {"access_token": "private-bound"}})]
            for path, options in cases:
                with self.subTest(path=path), self.assertRaises(ValueError) as caught: await client.get(path, **options)
                self.assertNotIn("private", str(caught.exception))
            with self.assertRaises(RuntimeError): client.bind_tokens(["another"])
        self.assertFalse(calls)

    async def test_actual_ambient_tickers_share_actor_route_and_keep_receipt_contract(self):
        requests, clients = [], []
        def factory(**options):
            shard = len(clients)
            def respond(request):
                requests.append((shard, request))
                body = json.loads(request.content) if request.content else {}
                return httpx.Response(202 if body else 200, json={"status": "success", "batch_id": body.get("batch_id"), "write_mode": "durable_queued"})
            client = httpx.AsyncClient(**options, transport=httpx.MockTransport(respond))
            clients.append(client); return client
        async with ShardedCapacityClient(base_url=ORIGIN, verify=False, shards=2, client_factory=factory) as client:
            client.bind_tokens(["actor-zero", "actor-one"])
            async def ticker(index, channel):
                calls = 0
                async def once(seconds):
                    nonlocal calls
                    calls += 1
                    return calls > 1
                # Independent load instances avoid modifying a shared ticker clock.
                item = AmbientLoad(client, ["actor-zero", "actor-one"], 7, synthetic=True, user_keys=["student:1", "student:2"])
                item.started_at = item.clock(); item._wait = once
                await item._ticker(index, channel, 0)
                self.assertEqual(item.snapshot()["error_count"], 0)
            await asyncio.gather(*(ticker(index, channel) for index in range(2) for channel in ("bell", "blog", "heartbeat")))
        self.assertEqual(len(requests), 6)
        for shard, request in requests:
            self.assertEqual(shard, 0 if request.headers["cookie"] == "access_token=actor-zero" else 1)


class CleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_close_failure_still_closes_all_and_preserves_primary_error(self):
        created, closed = [], []
        class Client:
            cookies = {}
            async def __aenter__(self): return self
            async def __aexit__(self, *exc):
                closed.append(self)
                if self is created[1]: raise RuntimeError("private-cleanup-detail")
        def factory(**options):
            value=Client(); created.append(value); return value
        primary = LookupError("original-business-failure")
        with self.assertRaises(LookupError) as caught:
            async with ShardedCapacityClient(base_url=ORIGIN, verify=False, shards=3, client_factory=factory):
                raise primary
        self.assertIs(caught.exception, primary)
        self.assertEqual(len({id(value) for value in closed}), 3)
        self.assertEqual(primary.__notes__, ["HTTP pool cleanup also failed: RuntimeError"])
        created.clear(); closed.clear()
        with self.assertRaisesRegex(RuntimeError, "private-cleanup-detail"):
            async with ShardedCapacityClient(base_url=ORIGIN, verify=False, shards=3, client_factory=factory): pass
        self.assertEqual(len(closed), 3)

    async def test_partial_factory_failure_and_business_exception_close_every_owned_pool(self):
        clients = []
        def factory(**options):
            if len(clients) == 3: raise RuntimeError("constructor-failed")
            value = httpx.AsyncClient(**options, transport=httpx.MockTransport(lambda request: httpx.Response(200)))
            clients.append(value); return value
        with self.assertRaisesRegex(RuntimeError, "constructor-failed"):
            async with ShardedCapacityClient(base_url=ORIGIN, verify=False, shards=16, client_factory=factory): pass
        self.assertEqual(len(clients), 3)
        self.assertTrue(all(client.is_closed for client in clients))
        clients.clear()
        async def fail(request): raise RuntimeError("business-failed")
        def working(**options):
            value = httpx.AsyncClient(**options, transport=httpx.MockTransport(fail)); clients.append(value); return value
        with self.assertRaisesRegex(RuntimeError, "business-failed"):
            async with ShardedCapacityClient(base_url=ORIGIN, verify=False, shards=3, client_factory=working) as client:
                client.bind_tokens(["verified"])
                await client.get("/dashboard", headers={"Cookie": "access_token=verified"})
        self.assertTrue(all(client.is_closed for client in clients))

    async def test_repeated_cancellation_cannot_abandon_accepted_pool_cleanup(self):
        accepted, release, closed = asyncio.Event(), asyncio.Event(), []
        class Client:
            cookies = {}
            async def __aenter__(self): return self
            async def __aexit__(self, *exc):
                accepted.set(); await release.wait(); closed.append(self)
        client = ShardedCapacityClient(base_url=ORIGIN, verify=False, shards=3, client_factory=lambda **options: Client())
        await client.__aenter__()
        stopping = asyncio.create_task(client.aclose())
        await accepted.wait()
        stopping.cancel(); await asyncio.sleep(0); stopping.cancel(); await asyncio.sleep(0)
        self.assertFalse(stopping.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError): await stopping
        self.assertEqual(len({id(item) for item in closed}), 3)
        await client.aclose()
        self.assertEqual(len(closed), 3)


class ProbeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_200_login_jwt_verification_precedes_shard_binding_and_report_keeps_budget_and_hashes(self):
        await self._run_probe_case()

    async def test_final_report_cannot_pass_when_owned_shard_cleanup_fails(self):
        await self._run_probe_case(fail_close=True)

    async def _run_probe_case(self, *, fail_close=False):
        original = httpx.AsyncClient
        secret, release = "offline-only-jwt-secret", "capacity-test-release"
        observed, logins, created = [], [], []
        async def app(scope, receive, send, shard):
            content = b""
            while True:
                item = await receive(); content += item.get("body", b"")
                if not item.get("more_body"): break
            path = scope["path"]
            headers = [(b"content-type", b"application/json"), (b"x-lanshare-release", release.encode())]
            if path == "/api/student/login/password":
                self.assertIsNone(shard)
                identifier = parse_qs(content.decode())["identifier"][0]
                index = int(identifier.rsplit("-", 1)[1]); logins.append(index)
                token = jwt.encode({"role": "student", "id": index+1}, secret, algorithm="HS256")
                headers.append((b"set-cookie", ("access_token="+token+"; Path=/").encode()))
                body = {"status": "success"}
            elif path == "/api/internal/capacity-lab-identity":
                self.assertEqual(shard, 0); body = {"lab_nonce": "a"*32}
            elif path == "/api/internal/health":
                self.assertEqual(shard, 0)
                body = {"service": "main", "release_id": release, "status": "ok", "behavior_write_pipeline": {
                    "alive": True, "queue_depth_known": True, "queue_depth": 0, "quarantined_count": 0}}
            else:
                self.assertEqual(len(logins), 200)
                token = httpx.Headers(scope["headers"])["cookie"].removeprefix("access_token=")
                identity = int(jwt.decode(token, secret, algorithms=["HS256"])["id"])-1
                self.assertEqual(shard, identity % 16)
                observed.append((identity, shard, path))
                body = {"status": "success"}
            await send({"type": "http.response.start", "status": 200, "headers": headers})
            await send({"type": "http.response.body", "body": json.dumps(body).encode()})
        shard_count = 0
        def factory(**options):
            nonlocal shard_count
            shard = shard_count if "limits" in options else None
            if shard is not None: shard_count += 1
            async def scoped(scope, receive, send): return await app(scope, receive, send, shard)
            class Client(original):
                async def __aexit__(self, *exc):
                    await super().__aexit__(*exc)
                    if fail_close and shard == 1:
                        raise OSError("private-cleanup-detail")
            client = Client(**options, transport=httpx.ASGITransport(app=scoped)); created.append(client); return client
        with tempfile.TemporaryDirectory(prefix="capacity-pool-asgi-") as folder:
            runtime = Path(folder)
            fixture_path = runtime / "fixture.json"; fixture_path.write_text("{}")
            sources = {"classroom_app/synthetic.py": "b"*64}
            lab = LabManifest(path=fixture_path, runtime=runtime, root=runtime, base_url=ORIGIN,
                ca_file=fixture_path, release_id=release, expected_identity={"lab_nonce": "a"*32},
                client_sources=sources, server_sources=sources, server_config={}, fixture={}, jwt_secret=secret,
                identifiers={f"CAPACITY-SYNTHETIC-{index:04d}": index+1 for index in range(200)},
                file_hashes={fixture_path: hashlib.sha256(b"{}").hexdigest()})
            args = SimpleNamespace(_lab=lab, duration=.02, rps=100, users=200, realtime=False, social=False,
                reading=False, ambient=False, http_pool_shards=16, socket_workers=1, socket_connect_limit=0, max_api_p95_ms=500,
                max_page_p95_ms=1000, max_login_p95_ms=2000)
            with patch.object(httpx, "AsyncClient", factory), patch.dict(os.environ, {"SECRET_KEY": secret}), \
                 patch.object(probe.ssl, "create_default_context", return_value=False), \
                 patch("tools.platform_capacity_lab_manifest.source_hashes", return_value=sources), redirect_stdout(io.StringIO()):
                if fail_close:
                    with self.assertRaises(OSError):
                        await probe.run(args, runtime, {"password": "offline-synthetic-password", "classOfferingId": 7})
                else:
                    await probe.run(args, runtime, {"password": "offline-synthetic-password", "classOfferingId": 7})
            report = json.loads((runtime / "capacity-report.json").read_text())
        self.assertEqual(sorted(logins), list(range(200)))
        self.assertTrue(observed)
        self.assertEqual(report["http_pool"]["shards"], 16)
        self.assertEqual(report["successful_logins"] if "successful_logins" in report else report["users"], 200)
        self.assertEqual(len(report["http_pool"]["harness_sha256"]), 7)
        self.assertEqual(len(created), 216)
        self.assertTrue(all(client.is_closed for client in created))
        if fail_close:
            self.assertEqual(report["status"], "failed")
            self.assertIn("http_pool_cleanup_failed", report["health_gate_errors"])
            self.assertEqual(report["fault_counts"], [{"flow": "http-pool-close", "error": "OSError", "count": 1}])
            self.assertNotIn("private-cleanup-detail", json.dumps(report))
