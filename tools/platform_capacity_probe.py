"""Loopback-only TCP/PG capacity probe over an explicitly synthetic fixture.

Requires a dedicated non-default-port cluster and a prepare_ui_v3_runtime.py
fixture imported into its own database. It never discovers application DSNs.
This native Windows probe is NOT a Linux production-image capacity certificate.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from contextlib import AsyncExitStack
import hashlib
import json
import os
from pathlib import Path
import socket
import ssl
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _record_probe_exception(faults, diagnostics, label, exc, *, target, phase):
    from tools.platform_capacity_ambient import exception_diagnostic
    kind = type(exc).__name__
    diagnostics["fault_counts"][(label, kind)] += 1
    diagnostics["exception_counts"][(label, kind)] += 1
    detail = None
    if len(faults) < 30 or len(diagnostics["exception_details"]) < 30:
        detail = exception_diagnostic(exc, target=target)
    if len(diagnostics["exception_details"]) < 30:
        diagnostics["exception_details"].append({"flow": label, "phase": phase, **detail})
    if len(faults) < 30:
        faults.append({"label": label, "error": kind, "detail": detail["detail"], "diagnostic": detail})


def _fault_summary(diagnostics, statuses=None):
    from tools.platform_capacity_ambient import diagnostic_counts
    return {"fault_count": sum(diagnostics["fault_counts"].values()),
            "fault_counts": diagnostic_counts(diagnostics["fault_counts"]),
            "exception_count": sum(diagnostics["exception_counts"].values()),
            "exception_counts": diagnostic_counts(diagnostics["exception_counts"]),
            "exception_details": list(diagnostics["exception_details"]), "exception_details_limit": 30,
            "http_response_count": sum(count for (_flow, status), count in (statuses or {}).items() if type(status) is int),
            "status_counting": "integer rows are HTTP responses; legacy string rows are error events, not extra HTTP responses",
            "fault_counting": "HTTP status and caught workflow fault events; may overlap one request; checkpoint exceptions included"}


def arguments():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("seed", "serve", "run"))
    p.add_argument("--runtime", type=Path, required=True)
    p.add_argument("--cluster-dir", type=Path)
    p.add_argument("--pg-port", type=int)
    p.add_argument("--lab-manifest", type=Path, help="Explicit owned Linux lab client manifest; run only, never connects to PostgreSQL")
    p.add_argument("--http-port", type=int, default=18767)
    p.add_argument("--proxy-port", type=int, default=0, help="Optional loopback TLS port from prepare_capacity_nginx.py")
    p.add_argument("--users", type=int, default=200)
    p.add_argument("--duration", type=int, default=60)
    p.add_argument("--rps", type=float, default=40)
    p.add_argument("--http-pool-shards", type=int, choices=range(1, 17), default=1,
                   help="1 preserves the shared-pool baseline; >1 shards verified identities with aggregate limits400/200")
    p.add_argument("--realtime", action="store_true", help="Hold classroom and social WebSockets for every identity")
    p.add_argument("--socket-workers", type=int, choices=range(1, 17), default=1,
                   help="1 keeps the comparison baseline; >1 spawn-isolates WS receivers from HTTP load")
    p.add_argument("--socket-connect-limit", type=int, default=0,
                   help="Concurrent handshakes per WS worker; 0 keeps unbounded startup, established sockets remain unlimited")
    p.add_argument("--social", action="store_true", help="Seed/exercise synthetic DM and a 200-member group")
    p.add_argument("--reading", action="store_true", help="Read actual published synthetic blog and material content")
    p.add_argument("--ambient", action="store_true", help="Add per-user bell15s/blog60s/visible heartbeat60s completion-based HTTP traffic")
    p.add_argument("--max-api-p95-ms", type=float, default=500)
    p.add_argument("--max-page-p95-ms", type=float, default=1000)
    p.add_argument("--max-login-p95-ms", type=float, default=2000)
    args = p.parse_args()
    if args.action != "run" and args.http_pool_shards != 1:
        p.error("--http-pool-shards is a run-only client variant")
    if args.socket_connect_limit < 0:
        p.error("--socket-connect-limit must be nonnegative")
    if args.lab_manifest is not None:
        if args.action != "run" or args.cluster_dir is not None or args.pg_port is not None or args.users != 200:
            p.error("--lab-manifest requires run with exactly 200 users and no native DB arguments")
    elif args.cluster_dir is None or args.pg_port is None:
        p.error("Native mode requires --cluster-dir and --pg-port")
    return args


def establish_boundary(args):
    runtime = args.runtime.resolve(strict=True)
    cluster = args.cluster_dir.resolve(strict=True)
    temporary = (ROOT / ".codex-temp").resolve()
    if not all(path != temporary and path.is_relative_to(temporary) for path in (runtime, cluster)):
        raise ValueError("Only owned .codex-temp children are permitted")
    fixture = json.loads((runtime / "fixture.json").read_text(encoding="utf-8"))
    if fixture.get("uiV3Synthetic") is not True or Path(fixture["databasePath"]).resolve() != runtime / "db/classroom.db":
        raise ValueError("An explicitly synthetic fixture is required")
    if not (cluster / "PG_VERSION").is_file() or not 1024 <= args.pg_port <= 65535 or args.pg_port == 5432:
        raise ValueError("Dedicated non-default PostgreSQL port required")
    # This is a dedicated harness process. Preserve OS/runtime necessities,
    # never an inherited integration endpoint, credential or production flag.
    essential = {name: value for name, value in os.environ.items() if name.upper() in {
        "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "PATH", "PATHEXT", "COMSPEC", "TEMP", "TMP", "TMPDIR",
        "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA",
    }}
    os.environ.clear()
    os.environ.update(essential)
    from tools.isolated_environment import guard_dotenv_loading, isolate_sqlite_environment
    isolate_sqlite_environment(runtime)
    guard_dotenv_loading()
    os.environ.update({
        "DB_ENGINE": "postgres", "POSTGRES_BACKEND_READY": "true", "POSTGRES_POOL_ENABLED": "true",
        "DATABASE_URL": f"postgresql://rehearsal_admin@127.0.0.1:{args.pg_port}/lanshare_assessment_rehearsal_capacity",
        "POSTGRES_POOL_MIN": "1", "POSTGRES_POOL_MAX": "12", "MAIN_THREADPOOL_TOKENS": "32",
        "MAIN_WORKERS": "1", "MAIN_HOST": "127.0.0.1", "MAIN_PORT": str(args.http_port),
        "AI_ASSISTANT_URL": "http://127.0.0.1:1", "UI_COPY_GENERATION_ENABLED": "false",
        "SECRET_KEY": "platform-capacity-synthetic-secret-not-for-production",
        "LANSHARE_RELEASE_ID": "capacity-synthetic-20261105",
        "LANSHARE_LQ_FAMILIES": "manage-shell,navbar-shell,dashboard,calendar,profile,messages,centered,growth,manage-pages",
        "TZ": "CST-8" if os.name == "nt" else "Asia/Shanghai",
    })
    import psycopg
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=5) as conn:
        directory, listener, port = conn.execute(
            "SELECT current_setting('data_directory'),current_setting('listen_addresses'),current_setting('port')"
        ).fetchone()
        if Path(directory).resolve() != cluster or listener != "127.0.0.1" or int(port) != args.pg_port:
            raise ValueError("PostgreSQL identity does not match the dedicated cluster")
    connect, connect_ex, dns = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo

    def allowed(host):
        return host is None or str(host).lower() in {"localhost", "127.0.0.1", "::1"}

    def local_connect(sock, address):
        if not allowed(address[0]):
            raise RuntimeError("Capacity probe forbids external sockets")
        return connect(sock, address)

    def local_dns(host, *values, **kwargs):
        if not allowed(host):
            raise RuntimeError("Capacity probe forbids external DNS")
        return dns(host, *values, **kwargs)

    def local_connect_ex(sock, address):
        if not allowed(address[0]):
            raise RuntimeError("Capacity probe forbids external sockets")
        return connect_ex(sock, address)

    socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = local_connect, local_connect_ex, local_dns
    return runtime, fixture


def seed(args, runtime, fixture):
    from classroom_app.database import init_database, get_db_connection
    from classroom_app.services.learning_progress_service import refresh_pending_learning_snapshots
    init_database()
    with get_db_connection() as conn:
        base = dict(conn.execute("SELECT * FROM students WHERE id=?", (fixture["student"]["id"],)).fetchone())
        for index in range(args.users):
            identifier = f"CAPACITY-SYNTHETIC-{index:04d}"
            conn.execute("""INSERT INTO students(student_id_number,name,class_id,gender,email,hashed_password,
                            password_reset_required,password_updated_at,description)
                            VALUES(?,?,?,'unknown',?,?,0,?,?) ON CONFLICT(student_id_number) DO NOTHING""",
                         (identifier, f"容量合成学生{index:04d}", fixture["classId"], f"capacity-{index}@example.invalid",
                          base["hashed_password"], base.get("password_updated_at"), "owned synthetic capacity fixture"))
    # The real refresh worker, not read-side recalculation, creates projections.
    for _ in range(10):
        result = refresh_pending_learning_snapshots(limit=100)
        if not result.get("refreshed"):
            break
    if args.social:
        from tools.platform_capacity_social import seed_social
        seed_social(args, runtime, fixture)
    if args.reading:
        from tools.platform_capacity_reading import seed_reading
        seed_reading(runtime, fixture)
    print(json.dumps({"seeded_synthetic_users": args.users, "production_data_modified": False}))


async def run(args, runtime, fixture):
    import httpx
    from jose import jwt
    from tools.platform_capacity_http_pools import ShardedCapacityClient, pool_budget, harness_hashes
    shards = getattr(args, "http_pool_shards", 1)
    http_pool = {**pool_budget(shards), "harness_sha256": harness_hashes(Path(__file__).resolve().parents[1])}
    lab = getattr(args, "_lab", None)
    lab_evidence = lab.evidence() if lab is not None else None
    run_id = uuid.uuid4().hex
    run_started_epoch = time.time()
    stop_probe = runtime / "stop-capacity-probe"
    if stop_probe.exists():
        raise ValueError("Remove the previous explicit capacity stop marker before starting")
    (runtime / "capacity-report.json").write_text(json.dumps({
        "status": "running", "run_id": run_id, "run_started_epoch": run_started_epoch,
        "requested_duration_seconds": args.duration, "production_data_modified": False,
        "http_pool": http_pool,
        **({"lab": lab_evidence} if lab is not None else {})}), encoding="utf-8")
    samples, statuses, faults, dispatch = defaultdict(list), Counter(), [], []
    diagnostics = {"fault_counts": Counter(), "exception_counts": Counter(), "exception_details": []}
    lab_gate_errors = set()
    windows = defaultdict(lambda: defaultdict(list))
    steady_started = None
    tokens = []
    social = None
    socket_counts = Counter()
    sockets, readers = [], []
    socket_closing = False
    socket_workers = None
    socket_faults = []
    socket_timing = {"started_at": None, "first_application_ready_at": None, "last_application_ready_at": None,
                     "handshake_current": 0, "handshake_peak": 0, "handshakes_started": 0,
                     "handshakes_finished": 0, "handshakes_cancelled": 0, "connectors_cancelled": 0}
    ambient = None

    def socket_startup_snapshot():
        if socket_workers:
            return socket_workers.timing
        started = socket_timing["started_at"]
        return {"handshake_mode": "bounded" if args.socket_connect_limit else "unbounded",
                "fault_details_limit_per_worker": 30,
                "handshake_limit_per_worker": args.socket_connect_limit,
                "aggregate_handshake_limit": args.socket_connect_limit,
                "application_ready_deadline_seconds": 30,
                **{key: socket_timing[key] for key in ("handshake_peak", "handshakes_started", "handshakes_finished",
                                                      "handshakes_cancelled", "connectors_cancelled")},
                **{field + "_ms": round((socket_timing[field + "_at"] - started) * 1000, 2)
                   if started is not None and socket_timing[field + "_at"] is not None else None
                   for field in ("first_application_ready", "last_application_ready")}}

    async def close_local_sockets():
        nonlocal socket_closing
        socket_closing = True
        try:
            await asyncio.wait_for(asyncio.gather(*(websocket.close() for websocket in sockets),
                                                return_exceptions=True), timeout=8)
        finally:
            for task in readers:
                task.cancel()
            await asyncio.gather(*readers, return_exceptions=True)

    base = lab.base_url if lab is not None else f"https://127.0.0.1:{args.proxy_port}" if args.proxy_port else f"http://127.0.0.1:{args.http_port}"
    ca_file = lab.ca_file if lab is not None else (ROOT / ".codex-temp/nginx-capacity-native/nginx-1.27.5/capacity-test.crt") if args.proxy_port else None
    tls = ssl.create_default_context(cafile=str(ca_file)) if ca_file is not None else ssl.create_default_context()
    if lab is not None:
        identifiers = dict(lab.identifiers)
    else:
        import psycopg
        with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=5) as conn:
            identifiers = dict(conn.execute("SELECT student_id_number,id FROM students WHERE student_id_number LIKE 'CAPACITY-SYNTHETIC-%'").fetchall())

    def record(label, started, response):
        elapsed = (time.perf_counter() - started) * 1000
        samples[label].append(elapsed)
        if steady_started is not None:
            windows[int((started - steady_started) // 10) * 10][label].append(elapsed)
        statuses[(label, response.status_code)] += 1
        if lab is not None:
            lab.validate_response(response)
        if response.status_code not in ({200, 202} if label == "behavior" else {200}):
            diagnostics["fault_counts"][(label, "unexpected_status")] += 1
            if len(faults) < 30:
                request_id = response.headers.get("x-request-id")
                try:
                    if request_id in tokens or request_id == fixture["password"]:
                        request_id = None
                    if isinstance(request_id, str):
                        uuid.UUID(request_id)  # Validate, preserve original log-correlation spelling.
                    else:
                        request_id = None
                except ValueError:
                    request_id = None
                faults.append({"label": label, "status": response.status_code, "request_id": request_id})

    # No shared cookie jar between identities; every request supplies its own
    # exact Cookie header, including WebSockets in dedicated realtime probes.
    main_client = (ShardedCapacityClient(base_url=base, verify=tls, shards=shards) if shards > 1 else
                   httpx.AsyncClient(base_url=base, timeout=30, trust_env=False, verify=tls,
                                     limits=httpx.Limits(max_connections=400, max_keepalive_connections=200)))
    async with main_client as client, AsyncExitStack() as cleanup:
        if lab is not None:
            try:
                if not lab.artifacts_unchanged():
                    from tools.platform_capacity_lab_manifest import LabManifestError
                    raise LabManifestError("Lab client artifacts changed before attachment")
                await lab.verify_identity(client)
            except Exception as exc:
                _record_probe_exception(faults, diagnostics, "lab-identity", exc, target=base, phase="preflight")
                report = {"status": "failed_lab_identity", "run_id": run_id, "lab": lab_evidence,
                          "http_pool": http_pool,
                          "successful_logins": 0, "faults": faults, **_fault_summary(diagnostics, statuses),
                          "production_data_modified": False}
                (runtime / "capacity-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
                print(json.dumps(report))
                return 1
        async def login(index):
            identifier = f"CAPACITY-SYNTHETIC-{index:04d}"
            # Reuse one SSL context even for HTTP. Constructing 200 Windows
            # trust stores synchronously would block the generator's event loop
            # and falsely count its startup as server login latency.
            async with httpx.AsyncClient(base_url=base, timeout=30, trust_env=False, verify=tls) as login_client:
                started = time.perf_counter()
                response = await login_client.post("/api/student/login/password", data={
                    "identifier": identifier, "password": fixture["password"], "next": "/dashboard"})
            record("login", started, response)
            if response.status_code != 200 or response.json().get("status") != "success" or not response.cookies.get("access_token"):
                return None
            token = response.cookies["access_token"]
            claims = jwt.decode(token, os.environ["SECRET_KEY"], algorithms=["HS256"])
            if claims.get("role") != "student" or int(claims.get("id") or 0) != identifiers[identifier]:
                raise AssertionError("Login token belongs to another synthetic identity")
            return token

        login_results = await asyncio.gather(*(login(i) for i in range(args.users)), return_exceptions=True)
        tokens = [token for token in login_results if isinstance(token, str)]
        failures = [type(x).__name__ for x in login_results if isinstance(x, BaseException)]
        for result in login_results:
            if isinstance(result, BaseException):
                _record_probe_exception(faults, diagnostics, "login", result,
                    target=base, phase="login")
        if len(tokens) != args.users:
            report = {"status": "failed_login_burst", "run_id": run_id, "users": args.users, "successful_logins": len(tokens),
                      "http_pool": http_pool,
                      "exceptions": failures, "faults": faults, **_fault_summary(diagnostics, statuses),
                      **({"lab": lab_evidence} if lab is not None else {})}
            (runtime / "capacity-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report))
            return 1
        if shards > 1:
            client.bind_tokens(tokens)
        client.cookies.clear()
        offering = fixture["classOfferingId"]
        if args.social:
            from tools.platform_capacity_social import load_social, verify_group_exclusion
            social = load_social(runtime)
            await verify_group_exclusion(client, tokens[199], social)
        if args.realtime:
            startup_error = None
            if args.socket_workers > 1:
                from tools.platform_capacity_socket_workers import SocketWorkerGroup
                specs = [{"token": token, "path": path, "channel": label} for token in tokens
                         for path, label in ((f"/ws/{offering}", "classroom"), ("/ws/social", "social"))]
                socket_workers = SocketWorkerGroup(runtime, base, specs, workers=args.socket_workers,
                    cafile=ca_file,
                    connect_limit=args.socket_connect_limit, **(lab.socket_options() if lab is not None else {}))
                cleanup.push_async_callback(socket_workers.stop)
                try:
                    await socket_workers.start()
                    await socket_workers.wait_ready()
                except Exception as exc:
                    startup_error = type(exc).__name__
                    await socket_workers.stop()
                    socket_counts.update(socket_workers.counts)
            else:
                import websockets
                from tools.platform_capacity_socket_workers import socket_fault_detail
                socket_timing["started_at"] = time.perf_counter()
                handshake_slots = asyncio.Semaphore(args.socket_connect_limit) if args.socket_connect_limit else None
                cleanup.push_async_callback(close_local_sockets)
                async def consume(websocket, label, target):
                    ready = False
                    try:
                        async for message in websocket:
                            socket_counts[(label, "frames")] += 1
                            payload = json.loads(message)
                            if not ready and payload.get("type") == ("history" if label == "classroom" else "hello"):
                                ready = True
                                socket_counts[(label, "application_ready")] += 1
                                stamp = time.perf_counter()
                                if socket_timing["first_application_ready_at"] is None:
                                    socket_timing["first_application_ready_at"] = stamp
                                socket_timing["last_application_ready_at"] = stamp
                    except Exception as exc:
                        socket_counts[(label, type(exc).__name__)] += 1
                        if len(socket_faults) < 30:
                            socket_faults.append(socket_fault_detail(exc, channel=label, phase="receive", target=target, tokens=tokens))
                    finally:
                        if not socket_closing:
                            socket_counts[(label, "closed_early")] += 1
                async def connect_socket(token, path, label):
                    target = base.replace("https:", "wss:").replace("http:", "ws:") + path
                    async def handshake():
                        socket_timing["handshake_current"] += 1
                        socket_timing["handshakes_started"] += 1
                        socket_timing["handshake_peak"] = max(socket_timing["handshake_peak"], socket_timing["handshake_current"])
                        try:
                            websocket = await websockets.connect(target,
                                additional_headers={"Cookie": f"access_token={token}"}, open_timeout=30, close_timeout=2,
                                max_queue=32, ping_interval=20, proxy=None, **({"ssl": tls} if ca_file is not None else {}))
                        except asyncio.CancelledError:
                            socket_timing["handshakes_cancelled"] += 1
                            raise
                        except Exception:
                            socket_timing["handshakes_finished"] += 1
                            raise
                        else:
                            socket_timing["handshakes_finished"] += 1
                            return websocket
                        finally:
                            socket_timing["handshake_current"] -= 1
                    try:
                        if handshake_slots is None:
                            websocket = await handshake()
                        else:
                            async with handshake_slots:
                                websocket = await handshake()
                        sockets.append(websocket)
                        readers.append(asyncio.create_task(consume(websocket, label, target)))
                        socket_counts[(label, "connected")] += 1
                    except asyncio.CancelledError:
                        socket_timing["connectors_cancelled"] += 1
                        raise
                    except Exception as exc:
                        socket_counts[(label, type(exc).__name__)] += 1
                        if len(socket_faults) < 30:
                            socket_faults.append(socket_fault_detail(exc, channel=label, phase="connect", target=target, tokens=tokens))
                deadline = socket_timing["started_at"] + 30
                connectors = [asyncio.create_task(connect_socket(token, path, label)) for token in tokens
                              for path, label in ((f"/ws/{offering}", "classroom"), ("/ws/social", "social"))]
                try:
                    await asyncio.wait_for(asyncio.gather(*connectors), max(0.01, deadline - time.perf_counter()))
                except TimeoutError:
                    startup_error = "readiness_timeout"
                while sum(socket_counts[(label, "application_ready")] for label in ("classroom", "social")) < 2*args.users:
                    if time.perf_counter() >= deadline:
                        startup_error = "readiness_timeout"
                        break
                    await asyncio.sleep(.05)
                if (socket_timing["last_application_ready_at"] is not None
                        and socket_timing["last_application_ready_at"] > deadline):
                    startup_error = "readiness_timeout"
                if any(count for (_, event), count in socket_counts.items()
                       if event not in {"connected", "frames", "application_ready"}):
                    startup_error = startup_error or "socket_startup_error"
            if startup_error:
                if socket_workers is None:
                    await close_local_sockets()
                report = {"status": "failed_socket_startup", "run_id": run_id, "users": len(tokens), "error": startup_error,
                          "http_pool": http_pool,
                          "socket_workers": args.socket_workers, "http_burst_started": False,
                          "websockets": [{"channel": key[0], "event": key[1], "count": value}
                                         for key, value in socket_counts.items()],
                          "worker_processes": socket_workers.worker_status if socket_workers else [],
                          "socket_connect_limit": args.socket_connect_limit,
                          "socket_startup": socket_startup_snapshot(),
                          "websocket_faults": socket_workers.fault_details if socket_workers else socket_faults,
                          **({"lab": lab_evidence} if lab is not None else {}),
                          "production_data_modified": False, **_fault_summary(diagnostics, statuses)}
                (runtime / "capacity-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
                print(json.dumps(report))
                return 1
            # Established WS must not consume the entire ordinary HTTP budget.
            async def burst(index):
                started = time.perf_counter()
                try:
                    response = await client.get(f"/api/classroom-interactions/classrooms/{offering}/snapshot",
                        headers={"Cookie": f"access_token={tokens[index]}"})
                    record("classroom-http-burst", started, response)
                except Exception as exc:
                    statuses[("classroom-http-burst", type(exc).__name__)] += 1
                    _record_probe_exception(faults, diagnostics, "classroom-http-burst", exc,
                        target=base, phase="classroom-http-burst")
            await asyncio.gather(*(burst(index) for index in range(args.users)))
        paths = [
            ("dashboard", "/dashboard"), ("classroom", f"/classroom/{offering}"),
            ("materials", f"/api/classrooms/{offering}/materials"),
            ("progress", "/api/learning/cultivation-profile"),
            ("message-summary", "/api/message-center/summary"),
            ("conversations", "/api/social/conversations"),
            ("blog-summary", "/api/blog/summary"),
            ("interaction", f"/api/classroom-interactions/classrooms/{offering}/snapshot"),
            ("behavior", f"/api/classrooms/{offering}/behavior/batch"),
        ]
        if social:
            paths.extend([("group-write", ""), ("dm-write", ""), ("group-read", "")])
        reading_metadata = None
        if args.reading:
            from tools.platform_capacity_reading import reading_paths, verify_reading_response
            paths.extend(reading_paths(runtime, fixture))
            reading_metadata = json.loads((runtime / "capacity-reading.json").read_text(encoding="utf-8"))
        semaphore = asyncio.Semaphore(400)
        skipped = 0
        in_flight = set()
        async def action(index, due):
            dispatch.append(max(0, time.perf_counter() - due) * 1000)
            async with semaphore:
                label, path = paths[index % len(paths)]
                started = time.perf_counter()
                try:
                    headers = {"Cookie": f"access_token={tokens[index % len(tokens)]}"}
                    if label in {"group-write", "dm-write", "group-read"}:
                        from tools.platform_capacity_social import social_action
                        await social_action(client, tokens[index % len(tokens)], index % len(tokens), index,
                                            social, record, kind={"group-write": "group", "dm-write": "dm", "group-read": "read"}[label])
                        return
                    elif label == "behavior":
                        response = await client.post(path, headers=headers, json={
                            "batch_id": f"b1_{int(time.time()*1000)}_capacity{index}", "page_key": "capacity",
                            "events": [{"action_type": "page_action", "summary_text": "合成容量操作", "payload": {"probe": True}}]})
                    else:
                        response = await client.get(path, headers=headers)
                    record(label, started, response)
                    if reading_metadata is not None:
                        verify_reading_response(label, response, reading_metadata)
                except Exception as exc:
                    statuses[(label, type(exc).__name__)] += 1
                    _record_probe_exception(faults, diagnostics, label, exc, target=base, phase="action")
        began = time.perf_counter()
        steady_started = began
        count = 0
        if args.ambient:
            from tools.platform_capacity_ambient import AmbientLoad
            ambient = AmbientLoad(client, tokens, int(offering), synthetic=fixture.get("uiV3Synthetic") is True,
                user_keys=[f"student:{identifiers[f'CAPACITY-SYNTHETIC-{index:04d}']}" for index in range(len(tokens))])
            cleanup.push_async_callback(ambient.stop)
            ambient.start()
        async def checkpoints():
            while True:
                await asyncio.sleep(60)
                entry = {"run_id": run_id, "run_started_epoch": run_started_epoch,
                         "elapsed_seconds": round(time.perf_counter() - began, 2),
                         "scheduled_actions": count, "in_flight": len(in_flight),
                         **_fault_summary(diagnostics, statuses), "skipped_at_capacity": skipped}
                try:
                    health = (await client.get("/api/internal/health")).json()
                    if lab is not None:
                        lab.validate_health(health)
                    entry["health"] = {name: health.get(name) for name in (
                        "status", "release_id", "db_pool", "behavior_write_pipeline", "social_outbound", "diagnostics")}
                except Exception as exc:
                    entry["checkpoint_error"] = type(exc).__name__
                    if lab is not None:
                        from tools.platform_capacity_lab_manifest import LabManifestError
                        if isinstance(exc, LabManifestError):
                            lab_gate_errors.add("lab_checkpoint_identity_mismatch")
                    _record_probe_exception(faults, diagnostics, "checkpoint-health", exc,
                        target=base, phase="checkpoint")
                    entry.update(_fault_summary(diagnostics, statuses))
                current_counts = socket_workers.counts if socket_workers else socket_counts
                entry["websockets"] = [[channel, event, value] for (channel, event), value in current_counts.items()]
                entry["socket_startup"] = socket_startup_snapshot()
                if ambient:
                    entry["ambient"] = ambient.snapshot()
                with (runtime / "capacity-checkpoints.jsonl").open("a", encoding="utf-8") as output:
                    output.write(json.dumps(entry, ensure_ascii=True) + "\n")
        checkpoint_task = asyncio.create_task(checkpoints())
        async def stop_checkpoints():
            checkpoint_task.cancel()
            await asyncio.gather(checkpoint_task, return_exceptions=True)
        cleanup.push_async_callback(stop_checkpoints)
        while time.perf_counter() < began + args.duration and not stop_probe.exists():
            due = began + count / args.rps
            await asyncio.sleep(max(0, due - time.perf_counter()))
            if len(in_flight) >= 400:
                skipped += 1
            else:
                task = asyncio.create_task(action(count, due))
                in_flight.add(task)
                task.add_done_callback(in_flight.discard)
            count += 1
        if ambient:
            ambient.request_stop()
            await asyncio.gather(*in_flight, ambient.stop())
        else:
            await asyncio.gather(*in_flight)
        await stop_checkpoints()
        if socket_workers:
            await socket_workers.stop()
            socket_counts.update(socket_workers.counts)
        else:
            await close_local_sockets()
        snapshot = (await client.get("/api/internal/health")).json()
        drain_started = time.perf_counter()
        while (snapshot.get("behavior_write_pipeline", {}).get("queue_depth", 0)
               and not snapshot.get("behavior_write_pipeline", {}).get("quarantined_count", 0)
               and time.perf_counter() - drain_started < 60):
            await asyncio.sleep(.5)
            snapshot = (await client.get("/api/internal/health")).json()
        behavior = snapshot.get("behavior_write_pipeline", {})
        health_errors = []
        health_errors.extend(sorted(lab_gate_errors))
        if snapshot.get("status") != "ok":
            health_errors.append("runtime_degraded")
        if not behavior.get("alive") or not behavior.get("queue_depth_known"):
            health_errors.append("behavior_worker_unavailable")
        if behavior.get("queue_depth") or behavior.get("quarantined_count"):
            health_errors.append("behavior_not_drained")
        if lab is not None:
            try:
                lab.validate_health(snapshot)
                await lab.verify_identity(client)
            except Exception as exc:
                health_errors.append("lab_final_identity_mismatch")
                _record_probe_exception(faults, diagnostics, "lab-identity", exc, target=base, phase="final")
            if not lab.artifacts_unchanged():
                health_errors.append("lab_client_artifacts_changed")
        def distribution(values):
            values = sorted(values)
            return {"count": len(values), **{f"p{p}_ms": round(values[min(len(values)-1, int(len(values)*p/100))], 2)
                    for p in (50, 95, 99)}, "max_ms": round(values[-1], 2)} if values else {}
        errors = sum(n for (label, status), n in statuses.items()
                     if status not in ({200, 202} if label == "behavior" else {200}))
        ambient_report = ambient.snapshot() if ambient else {"enabled": False}
        errors += ambient_report.get("error_count", 0)
        if args.realtime:
            errors += sum(n for (_, kind), n in socket_counts.items() if kind not in {"connected", "frames", "application_ready"})
            errors += sum(abs(socket_counts[(label, kind)] - args.users) for label in ("classroom", "social")
                          for kind in ("connected", "application_ready"))
        if lab is not None:
            from tools.platform_capacity_lab_manifest import source_hashes
            sources = source_hashes(ROOT)
            server_sources = dict(lab.server_sources)
            source_changed = sources != lab.client_sources
            server_config = dict(lab.server_config)
        else:
            sources = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                       for path in sorted((ROOT / "classroom_app").rglob("*.py"))}
            server_sources = json.loads((runtime / "server-source-sha256.json").read_text(encoding="utf-8"))
            source_changed = sources != server_sources
            server_config = json.loads((runtime / "server-probe-config.json").read_text(encoding="utf-8"))
        if shards > 1:
            try:
                await client.aclose()
            except Exception as exc:
                # Never seal a successful load report before observing every
                # owned shard's closure. The context also retains the error.
                health_errors.append("http_pool_cleanup_failed")
                _record_probe_exception(faults, diagnostics, "http-pool-close", exc, target=base, phase="cleanup")
        latencies = {name: distribution(values) for name, values in samples.items()}
        load_errors = []
        expected_actions = int(args.duration * args.rps)
        if count < max(0, expected_actions - 1):
            load_errors.append("insufficient_offered_actions")
        if distribution(dispatch).get("p95_ms", 0) > 100:
            load_errors.append("generator_dispatch_lag")
        slow_flows = [name for name, value in latencies.items() if value["p95_ms"] >
                      (args.max_login_p95_ms if name == "login" else args.max_page_p95_ms if name in {"dashboard", "classroom", "blog-page", "material-view"} else args.max_api_p95_ms)]
        slow_flows.extend(name for name, value in ambient_report.get("latencies", {}).items()
                          if value["p95_ms"] > args.max_api_p95_ms)
        interrupted = stop_probe.exists()
        report = {"status": "aborted" if interrupted else "passed" if not errors and not skipped and not source_changed and not slow_flows and not health_errors and not load_errors else "failed",
                  "http_pool": http_pool,
                  "run_id": run_id, "run_started_epoch": run_started_epoch, "requested_duration_seconds": args.duration,
                  "stopped_by_marker": interrupted, "users": len(tokens),
                  "duration_seconds": round(time.perf_counter()-began, 2), "offered_rps": args.rps,
                  "scheduled_actions": count, "skipped_at_capacity": skipped,
                  "expected_scheduled_actions": expected_actions, "load_generator_gate_errors": load_errors,
                  "ambient": ambient_report,
                  "latencies": latencies, "slow_flows": slow_flows,
                  "health_gate_errors": health_errors, "behavior_drain_seconds": round(time.perf_counter()-drain_started, 2),
                  "ten_second_windows": {str(window): {label: distribution(values) for label, values in flows.items()}
                                         for window, flows in sorted(windows.items())},
                  "statuses": [{"flow": key[0], "status": key[1], "count": value} for key, value in statuses.items()],
                  "dispatch_lag": distribution(dispatch), "faults": faults, "health": snapshot,
                  **_fault_summary(diagnostics, statuses),
                  "websockets": [{"channel": k[0], "event": k[1], "count": v} for k,v in socket_counts.items()],
                  "socket_workers": args.socket_workers if args.realtime else 0,
                  "worker_processes": socket_workers.worker_status if socket_workers else [],
                  "socket_connect_limit": args.socket_connect_limit,
                  "socket_startup": socket_startup_snapshot() if args.realtime else None,
                  "websocket_faults": socket_workers.fault_details if socket_workers else socket_faults,
                  "source_sha256": sources,
                  "server_config": server_config,
                  "source_changed_since_server_start": source_changed,
                  **({"lab": lab_evidence, "client_export_source_sha256": sources,
                      "server_source_sha256": server_sources, "controller_final_verification_required": True} if lab is not None else {}),
                  "scope": ("Linux lab client load evidence via TLS; excludes controller final DB/image/resource and real-supplier certification"
                            if lab is not None else "native Windows single Uvicorn + pooled PG" + (" + native Nginx/TLS" if args.proxy_port else "") + "; excludes Linux-image and real-supplier certification"),
                  "production_data_modified": False}
        (runtime / "capacity-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({k: v for k, v in report.items() if k not in {"health", "source_sha256", "ten_second_windows"}}, ensure_ascii=True))
        return 0 if report["status"] == "passed" else 1


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    args = arguments()
    if args.lab_manifest is not None:
        from tools.platform_capacity_lab_manifest import load_lab_manifest, install_client_boundary
        args._lab = load_lab_manifest(args, ROOT)
        install_client_boundary(args._lab)
        runtime, fixture = args._lab.runtime, args._lab.fixture
    else:
        runtime, fixture = establish_boundary(args)
    try:
        if args.action == "seed":
            seed(args, runtime, fixture)
        elif args.action == "serve":
            from classroom_app.app import app
            from classroom_app import config as application_config
            import uvicorn
            sources = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                       for path in sorted((ROOT / "classroom_app").rglob("*.py"))}
            (runtime / "server-source-sha256.json").write_text(json.dumps(sources), encoding="utf-8")
            (runtime / "server-probe-config.json").write_text(json.dumps({
                "lq_families": os.environ["LANSHARE_LQ_FAMILIES"], "threadpool_tokens": 32,
                "postgres_pool_max": 12, "uvicorn_limit_concurrency": 1024,
                "workers": 1, "host": sys.platform,
                "backlog": application_config.MAIN_BACKLOG,
                "timeout_keep_alive": application_config.MAIN_TIMEOUT_KEEP_ALIVE,
                "ws_ping_interval": application_config.MAIN_WS_PING_INTERVAL,
                "ws_ping_timeout": application_config.MAIN_WS_PING_TIMEOUT,
            }), encoding="utf-8")
            stop_file = runtime / "stop-native-server"
            if stop_file.exists():
                raise ValueError("Previous stop marker must be explicitly removed before starting")
            server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.http_port,
                                    workers=1, limit_concurrency=application_config.MAIN_LIMIT_CONCURRENCY or None,
                                    backlog=application_config.MAIN_BACKLOG,
                                    timeout_keep_alive=application_config.MAIN_TIMEOUT_KEEP_ALIVE,
                                    ws_ping_interval=application_config.MAIN_WS_PING_INTERVAL,
                                    ws_ping_timeout=application_config.MAIN_WS_PING_TIMEOUT,
                                    log_level="warning"))
            async def serve():
                async def watch():
                    while not stop_file.exists():
                        await asyncio.sleep(.5)
                    server.should_exit = True
                watcher = asyncio.create_task(watch())
                try:
                    await server.serve()
                finally:
                    watcher.cancel()
                    await asyncio.gather(watcher, return_exceptions=True)
            asyncio.run(serve())
        else:
            return asyncio.run(run(args, runtime, fixture))
        return 0
    finally:
        if "classroom_app.db.postgres" in sys.modules:
            sys.modules["classroom_app.db.postgres"].close_connection_pool()


if __name__ == "__main__":
    raise SystemExit(main())
