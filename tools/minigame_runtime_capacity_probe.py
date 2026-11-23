"""Loopback-only minigame HTTP capacity probe on a freshly owned native PostgreSQL cluster.

Example: python tools/minigame_runtime_capacity_probe.py --seconds 1800
No input DSN, business data, production account, or external network is accepted.
"""

import argparse
import asyncio
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from contextlib import ExitStack, asynccontextmanager
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# An external Python executable may own the local temporary volume. Use the
# project's installed dependencies without placing third-party backports ahead
# of Python's standard library on Windows.
venv_site = ROOT / "venv/Lib/site-packages"
if venv_site.is_dir() and str(venv_site) not in sys.path:
    placement = next((i for i, p in enumerate(sys.path) if Path(p).name == "site-packages"), len(sys.path))
    sys.path.insert(placement, str(venv_site))
from tools.native_postgres_temp import OwnedPostgresRuntime

# The Windows browser exception is restricted to the reviewed repository example.
# Changing package bytes requires a new review and an explicit pin here.
SAMPLE_ZIP_SHA256 = "7e091377f709c3873220fce271c73e550d79033e9a664a9bc69aa82d49121b8a"
SAMPLE_MANIFEST_SHA256 = "8a1d6e67814eea9c743df9afc77ffe5a98b4b024636cf83ee6df171fef5ea6c4"


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class WindowsServerBudget:
    """An owned job constrains the two servers and their freshly created PostgreSQL cluster."""

    def __init__(self, processors=None):
        import psutil
        import win32job

        self.job = win32job.CreateJobObject(None, "Local\\games-probe-" + uuid.uuid4().hex)
        eligible = psutil.Process().cpu_affinity()
        if len(eligible) < 2:
            raise RuntimeError("Two logical processors are required")
        self.processors = processors or ([eligible[2], eligible[4]] if len(eligible) >= 5 else eligible[:2])
        config = win32job.QueryInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation)
        config["BasicLimitInformation"]["Affinity"] = sum(1 << n for n in self.processors)
        config["BasicLimitInformation"]["LimitFlags"] |= (
            win32job.JOB_OBJECT_LIMIT_AFFINITY | win32job.JOB_OBJECT_LIMIT_JOB_MEMORY
        )
        if processors is not None:
            config["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        config["JobMemoryLimit"] = 4 * 1024**3
        win32job.SetInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation, config)

    def add(self, pid):
        import win32api
        import win32con
        import win32job

        handle = win32api.OpenProcess(
            win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE | win32con.PROCESS_QUERY_INFORMATION,
            False,
            pid,
        )
        try:
            if not win32job.IsProcessInJob(handle, self.job):
                win32job.AssignProcessToJobObject(self.job, handle)
            if not win32job.IsProcessInJob(handle, self.job):
                raise RuntimeError("Server did not enter its resource budget")
        finally:
            handle.Close()

    def attach_database(self, cluster):
        import psutil

        metadata = (cluster.cluster / "postmaster.pid").read_text(encoding="utf8").splitlines()
        pid = int(metadata[0])
        postgres = psutil.Process(pid)
        if (
            Path(metadata[1]).resolve() != cluster.cluster.resolve()
            or int(metadata[3]) != cluster.port
            or Path(postgres.exe()).name.lower() != "postgres.exe"
            or abs(postgres.create_time() - int(metadata[2])) > 10
        ):
            raise RuntimeError("Unrecognized PostgreSQL owner")
        self.add(pid)
        for child in postgres.children(recursive=True):
            if child.is_running():
                self.add(child.pid)

    def metrics(self):
        import win32job

        config = win32job.QueryInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation)
        return {
            "mode": "Windows job",
            "logicalProcessors": self.processors,
            "jobMemoryLimit": config["JobMemoryLimit"],
            "peakCommittedBytes": config["PeakJobMemoryUsed"],
        }


def main_worker(owned_path, web_port, classroom_only=False, connections=2, production_api=False):
    marker = Path(owned_path) / "cluster" / "postmaster.pid"
    pid = marker.read_text(encoding="utf-8").splitlines()
    owner = json.loads((Path(owned_path) / ".native-pg-owner.json").read_text(encoding="utf8"))
    if (
        owner.get("kind") != "owned-native-postgres-v1"
        or Path(owner["cluster"]).resolve() != marker.parent.resolve()
        or Path(pid[1]).resolve() != marker.parent.resolve()
        or not marker.parent.is_dir()
        or pid[5] != "127.0.0.1"
        or pid[3] == "5432"
        or os.getenv("MINIGAME_PROBE_OWNER") != str(Path(owned_path).resolve())
    ):
        raise ValueError("Requires the newly owned loopback cluster")
    data = Path(os.environ["MINIGAME_PROBE_DATA"]).resolve(strict=True)
    if (
        not data.is_relative_to((ROOT / ".codex-temp/scratch").resolve())
        or (data / ".owner").read_text(encoding="utf8") != owner["token"]
    ):
        raise ValueError("Unrecognized synthetic storage directory")
    os.environ.update(
        DB_ENGINE="postgres",
        DATABASE_URL=f"postgresql://rehearsal_admin@127.0.0.1:{int(pid[3])}/postgres",
        POSTGRES_BACKEND_READY="true",
        POSTGRES_POOL_ENABLED="true",
        POSTGRES_POOL_MIN="1",
        POSTGRES_POOL_MAX="6",
        GAMES_DB_WORKERS=str(connections),
        LOGIN_ATTENDANCE_ENABLED="0",
        LANSHARE_DATA_ROOT=str(data),
    )
    from unittest.mock import patch

    with patch("dotenv.load_dotenv", return_value=False):
        from classroom_app.routers import minigame_runtime as api, classroom_group_qr, minigame_admin
        from classroom_app.services import (
            minigame_runtime as rt,
            minigame_platform as p,
            minigame_packages as packages,
        )
        from classroom_app.db.postgres import LanSharePostgresConnection, close_connection_pool
        from fastapi import FastAPI
        import uvicorn

        if production_api and not classroom_only:
            from classroom_app.minigame_api import app as deployed_api
    import threading
    from contextlib import contextmanager

    metric = {"active": 0, "peak": 0}
    sql_counts = Counter()
    active_call = threading.local()
    mutex = threading.Lock()
    original = p.game_db
    original_execute = LanSharePostgresConnection.execute
    original_commit = LanSharePostgresConnection.commit

    def counted_execute(conn, sql, params=None):
        local = getattr(active_call, "stats", None)
        if local is not None:
            verb = sql.lstrip().split(None, 1)[0].upper()
            local["statements"] += 1
            if verb in ("INSERT", "UPDATE", "DELETE") or (verb == "WITH" and "UPDATE mg_rt_claims" in sql):
                local["mutatingStatements"] += 1
                active_call.dirty = True
        return original_execute(conn, sql, params)

    def counted_commit(conn):
        result = original_commit(conn)
        if getattr(active_call, "dirty", False):
            active_call.stats["writeCommits"] += 1
            active_call.dirty = False
        return result

    LanSharePostgresConnection.execute = counted_execute
    LanSharePostgresConnection.commit = counted_commit

    @contextmanager
    def measured(*, snapshot=False):
        with original(snapshot=snapshot) as c:
            active_call.stats = Counter(checkouts=1)
            active_call.dirty = False
            with mutex:
                metric["active"] += 1
                metric["peak"] = max(metric["peak"], metric["active"])
            try:
                yield c
            finally:
                with mutex:
                    metric["active"] -= 1
                    sql_counts.update(active_call.stats)
                active_call.stats = None
                active_call.dirty = False

    p.game_db = measured

    @asynccontextmanager
    async def lifespan(app):
        if not classroom_only:
            rt.start_runtime()
            await packages.start_package_worker()
        try:
            yield
        finally:
            if not classroom_only:
                await packages.stop_package_worker()
                await rt.stop_runtime()
            close_connection_pool()

    if production_api and not classroom_only:
        app = deployed_api
    else:
        app = FastAPI(lifespan=lifespan)
        if not classroom_only:
            app.include_router(api.router)
            app.include_router(minigame_admin.router)
        app.include_router(classroom_group_qr.router)

    class ClassroomTiming:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http" or scope["path"] != "/api/classrooms/1/group-qr":
                return await self.app(scope, receive, send)
            start = time.perf_counter_ns()

            async def measured_send(message):
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    headers.append(
                        (b"x-probe-service-us", str((time.perf_counter_ns() - start) // 1000).encode())
                    )
                    message = {**message, "headers": headers}
                await send(message)

            return await self.app(scope, receive, measured_send)

    app.add_middleware(ClassroomTiming)

    @app.get("/__capacity/health")
    def health():
        with mutex:
            return {
                "ok": True,
                "gameConnections": dict(metric),
                "workerBudget": p._WORKERS,
                "runtimeSQL": dict(sql_counts),
                "processCPUSeconds": time.process_time(),
            }

    uvicorn.run(app, host="127.0.0.1", port=web_port, access_log=False, log_level="warning")


def seed(dsn, secret, app_data):
    from unittest.mock import patch

    os.environ.update(
        DB_ENGINE="postgres",
        DATABASE_URL=dsn,
        POSTGRES_BACKEND_READY="true",
        POSTGRES_POOL_ENABLED="false",
        SECRET_KEY=secret,
        LANSHARE_DATA_ROOT=str(app_data),
        LOGIN_ATTENDANCE_ENABLED="0",
    )
    with patch("dotenv.load_dotenv", return_value=False):
        from classroom_app.services import minigame_platform as p
        from jose import jwt
    with p.game_db() as c:
        p.ensure_all_schema(c)
        for sql in (
            "CREATE TABLE students(id integer primary key,name text,class_id integer,school_code text,college text,department text,enrollment_status text,control_mode text)",
            "CREATE TABLE teachers(id integer primary key,name text,is_active integer,is_super_admin integer)",
            "INSERT INTO teachers VALUES(1,'Synthetic owner',1,0)",
            "CREATE TABLE class_offerings(id integer primary key,class_id integer,teacher_id integer,group_qr_revision integer,group_qr_description text,group_qr_file_hash text,group_qr_mime_type text)",
            "CREATE TABLE class_offering_class_links(offering_id integer,class_id integer)",
            "CREATE TABLE learning_certificates(id integer,student_id integer,class_offering_id integer,tier integer)",
            "CREATE TABLE user_sessions(session_user_key text primary key,session_id text,user_id text,role text,name text,ip text,last_login text,expires_at text,updated_at text)",
            "CREATE TABLE private_message_blocks(owner_identity text,blocked_identity text)",
            "CREATE TABLE social_blocks(owner_identity text,blocked_identity text)",
            "CREATE TABLE wanderer_agents(student_id integer primary key)",
            "CREATE TABLE social_friendships(id integer,identity_a text,identity_b text,status text)",
            "INSERT INTO class_offerings VALUES(1,1,1,1,'fixture',NULL,NULL)",
        ):
            c.execute(sql)
        c.execute(
            "INSERT INTO mg_games(id,slug,owner_teacher_id,title,scope,status,current_version_id,created_at,updated_at) VALUES('probe','probe',1,'Synthetic','public','listed','v1',1,1)"
        )
        config = {
            "modes": {
                "solo": False,
                "multiplayer": {
                    "privateRooms": True,
                    "minPlayers": 2,
                    "maxPlayers": 10,
                    "randomSizes": [10],
                    "kind": "coop",
                },
            },
            "capabilities": [],
        }
        rules = {
            "engine": "finite-turn-v1",
            "initial": "live",
            "states": {"live": {"outcome": None}, "end": {"outcome": "completed"}},
            "transitions": [
                {"from": "live", "actor": "any", "type": "tick", "payload": {}, "to": "live", "turn": "keep"},
                {"from": "live", "actor": "any", "type": "end", "payload": {}, "to": "end", "turn": "keep"},
            ],
        }
        writeback = {
            "series": "capacity",
            "saveVersion": 1,
            "privateFields": {},
            "milestones": [{"id": "start", "resumeState": "live"}],
        }
        c.execute(
            "INSERT INTO mg_versions(id,game_id,status,created_at,config_json,rules_json,writeback_json) VALUES('v1','probe','ready',1,?,?,?)",
            tuple(json.dumps(v) for v in (config, rules, writeback)),
        )
        accounts = []
        for i in range(1, 202):
            sid = uuid.uuid4().hex
            actor = f"student:{i}"
            user = {
                "id": i,
                "role": "student",
                "name": f"synthetic-{i}",
                "actor": actor,
                "session_id": sid,
                "session_user_key": actor,
                "exp": int(time.time()) + 10800,
            }
            c.execute(
                "INSERT INTO students VALUES(?,?,1,'school','college','department','active','human')",
                (i, user["name"]),
            )
            c.execute(
                "INSERT INTO user_sessions VALUES(?,?,?,?,?,'127.0.0.1','','2099-01-01T00:00:00+00:00','')",
                (actor, sid, str(i), "student", user["name"]),
            )
            accounts.append((jwt.encode(user, secret, algorithm="HS256"), p.csrf_token(user)))
        sid = uuid.uuid4().hex
        teacher = {
            "id": 1,
            "role": "teacher",
            "name": "Synthetic owner",
            "actor": "teacher:1",
            "session_id": sid,
            "session_user_key": "teacher:1",
            "exp": int(time.time()) + 10800,
        }
        accounts.append((jwt.encode(teacher, secret, algorithm="HS256"), p.csrf_token(teacher)))
        c.execute(
            "INSERT INTO user_sessions VALUES('teacher:1',?,'1','teacher','Synthetic owner','127.0.0.1','','2099-01-01','')",
            (sid,),
        )
        c.execute("""INSERT INTO mg_games(id,slug,owner_teacher_id,title,scope,status,current_version_id,created_at,updated_at)
            VALUES('retire-probe','retire-probe',1,'Synthetic retirement','public','listed','retire-v1',1,1)""")
        c.execute("""INSERT INTO mg_versions(id,game_id,status,created_at,config_json,rules_json,writeback_json)
            SELECT 'retire-v1','retire-probe',status,created_at,config_json,rules_json,writeback_json FROM mg_versions WHERE id='v1'""")
        c.commit()
    return accounts


def stats(values):
    ordered = sorted(values)

    def q(frac):
        return round(ordered[int((len(ordered) - 1) * frac)], 2) if ordered else None

    return {"n": len(values), "p50_ms": q(0.5), "p95_ms": q(0.95), "p99_ms": q(0.99), "max_ms": q(1)}


def classroom_probe(port_number, credential, duration, log_path):
    """A separate OS process clocks real classroom requests at the same 5 Hz in both phases."""
    import httpx

    latency = []
    service = []
    observations = []
    errors = Counter()
    with httpx.Client(trust_env=False, timeout=8.0) as client:
        url = f"http://127.0.0.1:{port_number}/api/classrooms/1/group-qr"
        headers = {"Cookie": "access_token=" + credential}
        client.get(url, headers=headers).raise_for_status()
        until = time.monotonic() + duration
        next_log = time.monotonic() + 15
        while time.monotonic() < until:
            start = time.perf_counter()
            wall = time.time_ns() / 1e6
            try:
                result = client.get(url, headers=headers)
                latency.append((time.perf_counter() - start) * 1000)
                if result.is_success:
                    service.append(int(result.headers["x-probe-service-us"]) / 1000)
                    observations.append((wall, latency[-1], service[-1]))
                else:
                    errors[str(result.status_code)] += 1
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                errors[type(exc).__name__] += 1
            if time.monotonic() >= next_log:
                log_path.write_text(
                    json.dumps({"client": stats(latency), "server": stats(service), "errors": dict(errors)}),
                    encoding="utf8",
                )
                next_log = time.monotonic() + 15
            time.sleep(max(0, 0.2 - (time.perf_counter() - start)))
    return latency, service, dict(errors), os.getpid(), observations


async def verify_two_workers(req):
    room = await req(
        1, "POST", "/api/games/v1/games/probe/rooms", {"targetSize": 2, "hostInstance": "first"}, worker=0
    )
    path = "/api/games/v1/sync?roomId=" + room["roomId"] + "&after=" + str(room["seq"]) + "&waitMs=8000"
    observer = asyncio.create_task(req(1, "GET", path, worker=1))
    await asyncio.sleep(0.1)
    results = await asyncio.gather(
        *(
            req(
                i,
                "POST",
                "/api/games/v1/games/probe/rooms/join",
                {"code": room["code"], "hostInstance": str(i)},
                worker=i % 2,
                expected=(200, 404),
            )
            for i in (2, 3)
        )
    )
    if sum(x.get("phase") == "countdown" for x in results) != 1:
        raise AssertionError("Cross-worker collision allocated the final seat incorrectly")
    completed = (await observer)["snapshot"]
    if completed["phase"] != "countdown" or len(completed["members"]) != 2:
        raise AssertionError("Peer worker failed to observe filled room")
    await asyncio.sleep(max(0, (completed["startsAt"] - time.time_ns() / 1e6) / 1000) + 0.1)
    active = (
        await req(
            1,
            "POST",
            "/api/games/v1/sync",
            {"roomId": room["roomId"], "hostInstance": "first", "controlEpoch": room["controlEpoch"]},
            worker=1,
        )
    )["snapshot"]
    arguments = {
        "hostInstance": "first",
        "controlEpoch": room["controlEpoch"],
        "expectedRevision": active["revision"],
        "intent": {"type": "tick", "payload": {}},
    }
    moves = await asyncio.gather(
        *(
            req(
                1,
                "POST",
                "/api/games/v1/runs/" + active["runId"] + "/intents",
                arguments,
                worker=i,
                key="same-command",
            )
            for i in (0, 1)
        )
    )
    if (
        moves[0]["revision"] != active["revision"] + 1
        or moves[0]["revision"] != moves[1]["revision"]
        or moves[0]["seq"] != moves[1]["seq"]
    ):
        raise AssertionError("Cross-process idempotency produced duplicate effects")


async def probe(
    target, ports, accounts, seconds, baseline, classroom_pool, classroom_port, verify_http=False
):
    import httpx

    origin = f"http://127.0.0.1:{ports[0]}"
    errors = Counter()
    samples = {}
    snaps = {}
    async with (
        httpx.AsyncClient(
            trust_env=False,
            timeout=httpx.Timeout(8.0),
            limits=httpx.Limits(max_connections=160, max_keepalive_connections=80),
        ) as client,
    ):

        async def req(
            i,
            method,
            path,
            body=None,
            label=None,
            worker=None,
            key=None,
            upload=None,
            expected=(200, 201, 202, 204),
        ):
            worker = (i % 2) if worker is None else worker
            tok, csrf = accounts[i - 1]
            headers = {
                "Cookie": "access_token=" + tok,
                "Origin": origin,
                "Sec-Fetch-Site": "same-origin",
                "X-LanGame-CSRF": csrf,
                "Idempotency-Key": key or uuid.uuid4().hex,
            }
            start = time.perf_counter()
            try:
                options = (
                    {"json": body}
                    if upload is None
                    else {"data": body, "files": {"file": ("beacon-relay.zip", upload, "application/zip")}}
                )
                r = await client.request(
                    method, f"http://127.0.0.1:{ports[worker]}" + path, headers=headers, **options
                )
                ms = (time.perf_counter() - start) * 1000
                if label:
                    samples.setdefault(label, []).append(ms)
                if r.status_code not in expected:
                    raise ValueError(str(r.status_code) + ":" + r.text[:100])
                return r.json()
            except Exception as e:
                errors[str(label) + ":" + type(e).__name__ + ":" + str(e)[:110]] += 1
                raise

        for wp in set((*ports, classroom_port)):
            for _ in range(90):
                try:
                    if (await client.get(f"http://127.0.0.1:{wp}/__capacity/health")).status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                await asyncio.sleep(0.25)
            else:
                raise RuntimeError("Worker startup failed; see worker log")

        maintenance_window = []
        classroom_observations = {}

        async def administer():
            # Independent synthetic game: ensure revocation does not interrupt the measured 20 rooms.
            admin = "/api/games/v1/admin"
            await asyncio.sleep(min(15, seconds * 0.1))
            maintenance_window.append(time.time_ns() / 1e6)
            subject = await req(
                201,
                "POST",
                "/api/games/v1/games/retire-probe/rooms",
                {"targetSize": 2, "hostInstance": "retire-fixture"},
            )
            draft = (await req(202, "POST", admin + "/games", {}))["game"]
            package = (ROOT / "docs/minigames/downloads/beacon-relay.zip").read_bytes()
            uploaded = await req(
                202,
                "POST",
                admin + "/games/" + draft["id"] + "/versions",
                {"expectedRevision": str(draft["revision"])},
                "admin_upload",
                upload=package,
            )
            await req(
                202, "POST", admin + "/games/retire-probe/unlist", {"expectedRevision": 1}, "admin_unlist"
            )
            await req(201, "GET", "/api/games/v1/sync?roomId=" + subject["roomId"], worker=1, expected=(404,))
            deadline = time.monotonic() + 60
            while True:
                job = await req(202, "GET", admin + "/jobs/" + uploaded["jobId"], worker=1)
                if job["status"] == "succeeded":
                    maintenance_window.append(time.time_ns() / 1e6)
                    return
                if job["status"] in ("failed", "rejected") or time.monotonic() > deadline:
                    raise RuntimeError("Synthetic package validation did not succeed: " + str(job["status"]))
                await asyncio.sleep(0.5)

        if verify_http:
            await administer()
            await verify_two_workers(req)
            return {
                "scope": "isolated PostgreSQL, two real game HTTP workers",
                "result": "passed",
                "errors": dict(errors),
            }

        async def classroom(duration, label):
            latency, service, failures, pid, points = await asyncio.get_running_loop().run_in_executor(
                classroom_pool,
                classroom_probe,
                classroom_port,
                accounts[200][0],
                duration,
                target / (label + ".json"),
            )
            samples[label] = latency
            samples[label + "_service"] = service
            classroom_observations[label] = points
            errors.update({label + ":" + k: v for k, v in failures.items()})
            return pid

        baseline_pid = await classroom(baseline, "classroom_baseline")
        gate = asyncio.Semaphore(6)
        rooms = []

        async def form(group):
            first = group * 10 + 1
            start_at = None

            async def wait_for_countdown(i, initial):
                current = initial
                deadline = time.monotonic() + 60
                while current["phase"] == "waiting":
                    response = await req(
                        i,
                        "GET",
                        "/api/games/v1/sync?roomId="
                        + current["roomId"]
                        + "&after="
                        + str(current["seq"])
                        + "&waitMs=8000",
                        worker=(i + 1) % 2,
                    )
                    current = response["snapshot"]
                    if time.monotonic() > deadline:
                        raise RuntimeError("Room never reached countdown")
                delay = time.time_ns() / 1e6 - (current["startsAt"] - 5000)
                samples.setdefault("countdown_received", []).append(max(0, delay))
                return delay

            async with gate:
                room = await req(
                    first,
                    "POST",
                    "/api/games/v1/games/probe/rooms",
                    {"targetSize": 10, "hostInstance": str(first)},
                )
            snaps[first] = room
            countdown_tasks = [asyncio.create_task(wait_for_countdown(first, room))]
            for i in range(first + 1, first + 10):
                async with gate:
                    s = await req(
                        i,
                        "POST",
                        "/api/games/v1/games/probe/rooms/join",
                        {"code": room["code"], "hostInstance": str(i)},
                    )
                snaps[i] = s
                countdown_tasks.append(asyncio.create_task(wait_for_countdown(i, s)))
                if s["phase"] == "countdown":
                    start_at = s["startsAt"]
            if not start_at:
                raise RuntimeError("Missing countdown")
            delays = await asyncio.gather(*countdown_tasks)
            samples.setdefault("countdown_all_ten_received", []).append(max(delays))
            rooms.append((first, room["roomId"], start_at))

        await asyncio.gather(*(form(j) for j in range(20)))
        # Periodic members stay attached. Controllers cross between the two HTTP processes.
        before = [(await client.get(f"http://127.0.0.1:{p}/__capacity/health")).json() for p in ports]
        until = time.monotonic() + seconds
        stats_path = target / "progress.json"
        pending = {rid: {} for _, rid, _ in rooms}
        observations = {rid: {} for _, rid, _ in rooms}
        seen_start = set()

        def observed_peer(rid, i, through, observed, worker):
            observations[rid][i] = (through, observed, worker)
            for seq, (sent, sender_worker, remaining) in list(pending[rid].items()):
                if seq > through or i not in remaining or observed < sent:
                    continue
                delay = (observed - sent) * 1000
                if worker != sender_worker:
                    samples.setdefault("cross_worker_peer_e2e", []).append(delay)
                remaining.remove(i)
                if not remaining:
                    samples.setdefault("all_nine_peers_e2e", []).append(delay)
                    del pending[rid][seq]

        async def keep(i):
            initial = snaps[i]
            rid = initial["roomId"]
            n = 0
            await asyncio.sleep((i - 1) * 0.01)
            while time.monotonic() < until:
                body = {
                    "roomId": rid,
                    "hostInstance": str(i),
                    "controlEpoch": initial["controlEpoch"],
                    "after": snaps[i]["seq"],
                }
                try:
                    response = await req(
                        i, "POST", "/api/games/v1/sync", body, "game_sync", worker=(i + n) % 2
                    )
                    snaps[i] = response["snapshot"]
                    observed = time.perf_counter()
                    if snaps[i].get("startedAt") and rid not in seen_start:
                        seen_start.add(rid)
                        samples.setdefault("start_offset", []).append(
                            snaps[i]["startedAt"] - snaps[i]["startsAt"]
                        )
                    if (i - 1) % 10:
                        observed_peer(rid, i, snaps[i]["seq"], observed, (i + n) % 2)
                    if snaps[i]["phase"] not in ("waiting", "countdown", "running"):
                        errors["unexpected_phase:" + str(snaps[i]["phase"])] += 1
                except Exception:
                    pass
                n += 1
                # The current browser host schedules 1.7–1.9s after the previous reply.
                await asyncio.sleep(1.7 + 0.2 * ((i + n) % 17) / 16)

        async def command(first, rid, starts):
            await asyncio.sleep((first % 17) * 0.03)
            rev = None
            n = 0
            while time.monotonic() < until:
                t = time.monotonic()
                s = snaps[first]
                if s["phase"] == "running":
                    if rev is None:
                        rev = s["revision"]
                    try:
                        sent = time.perf_counter()
                        x = await req(
                            first,
                            "POST",
                            "/api/games/v1/runs/" + s["runId"] + "/intents",
                            {
                                "hostInstance": str(first),
                                "controlEpoch": s["controlEpoch"],
                                "expectedRevision": rev,
                                "intent": {"type": "tick", "payload": {}},
                            },
                            "game_intent",
                            worker=n % 2,
                        )
                        rev = x["revision"]
                        pending[rid][x["seq"]] = (sent, n % 2, set(range(first + 1, first + 10)))
                        # An observer may already have received the committed event
                        # while the sender's HTTP response was still in flight.
                        for peer, sighting in sorted(observations[rid].items(), key=lambda item: item[1][1]):
                            observed_peer(rid, peer, *sighting)
                    except ValueError as exc:
                        if str(exc).startswith("409:"):
                            rev = None
                    except httpx.HTTPError:
                        pass
                n += 1
                await asyncio.sleep(max(0, 0.5 - (time.monotonic() - t)))

        async def observe():
            while time.monotonic() < until:
                data = {
                    "remainingSeconds": round(until - time.monotonic(), 1),
                    "recentSamples": {k: {**stats(v[-5000:]), "total": len(v)} for k, v in samples.items()},
                    "errors": dict(errors),
                }
                stats_path.write_text(json.dumps(data), encoding="utf8")
                await asyncio.sleep(min(15, max(0.1, until - time.monotonic())))

        tasks = await asyncio.gather(
            *(keep(i) for i in range(1, 201)),
            *(command(*r) for r in rooms),
            administer(),
            classroom(seconds, "classroom_mixed"),
            observe(),
        )
        if tasks[-2] != baseline_pid:
            raise RuntimeError("Classroom observer changed between baseline and mixed phases")
        for at, client_ms, service_ms in classroom_observations["classroom_mixed"]:
            phase = "during_package" if maintenance_window[0] <= at <= maintenance_window[1] else "steady"
            samples.setdefault("classroom_" + phase, []).append(client_ms)
            samples.setdefault("classroom_" + phase + "_service", []).append(service_ms)
        peaks = [(await client.get(f"http://127.0.0.1:{p}/__capacity/health")).json() for p in ports]
    return {
        "seconds": seconds,
        "baselineSeconds": baseline,
        "packageWindowSeconds": round((maintenance_window[1] - maintenance_window[0]) / 1000, 3),
        "players": 200,
        "rooms": 20,
        "workers": 2,
        "scope": "Windows loopback HTTP, owned PostgreSQL, actual game/QR handlers, independent classroom measurement process; no production/Nginx",
        "samples": {k: stats(v) for k, v in samples.items()},
        "errors": dict(errors),
        "workersObserved": peaks,
        "gameWorkerCPUCoreAverage": round(
            sum(y["processCPUSeconds"] - x["processCPUSeconds"] for x, y in zip(before, peaks)) / seconds, 3
        ),
        "gameSQLPerSecond": {
            k: round(
                sum(
                    end["runtimeSQL"].get(k, 0) - start["runtimeSQL"].get(k, 0)
                    for start, end in zip(before, peaks)
                )
                / seconds,
                2,
            )
            for k in ("checkouts", "statements", "mutatingStatements", "writeCommits")
        },
        "pendingPeerObservationsAtCutoff": sum(
            len(waiting) for group in pending.values() for _, _, waiting in group.values()
        ),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=1800)
    ap.add_argument("--baseline", type=int, default=60)
    ap.add_argument("--connections-per-api", type=int, choices=(1, 2), default=2)
    ap.add_argument(
        "--production-api",
        action="store_true",
        help="Use classroom_app.minigame_api:app for the game workers",
    )
    ap.add_argument("--owned-worker")
    ap.add_argument("--port", type=int)
    ap.add_argument("--windows-2c4g", action="store_true")
    ap.add_argument("--separate-classroom", action="store_true")
    ap.add_argument(
        "--external-browser",
        action="store_true",
        help="Execute only the reviewed Chromium probe in a separate, disjoint two-CPU Windows job",
    )
    ap.add_argument(
        "--deprioritize-games",
        action="store_true",
        help="Windows: below-normal scheduling for dedicated game processes",
    )
    ap.add_argument("--classroom-only", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--verify-budget-only", action="store_true")
    ap.add_argument("--verify-http", action="store_true")
    args = ap.parse_args()
    if args.external_browser and not args.windows_2c4g:
        ap.error("External executor comparison requires the explicit 2c4g server budget")
    if args.deprioritize_games and not args.separate_classroom:
        ap.error("Scheduling priority only applies to separate game processes")
    if args.owned_worker:
        main_worker(
            args.owned_worker, args.port, args.classroom_only, args.connections_per_api, args.production_api
        )
        return
    if not 5 <= args.seconds <= 3600 or not 3 <= args.baseline <= 300:
        ap.error("duration outside isolation budget")
    root = ROOT / ".codex-temp/evidence/minigames-v21-runtime"
    # Keep live WAL outside the synced teaching checkout. Copy only measurement and cleanup evidence.
    from contextlib import contextmanager

    @contextmanager
    def isolated():
        root.mkdir(parents=True, exist_ok=True)
        configured = Path(os.environ["TEMP"])
        storage = configured
        if storage.resolve().is_relative_to(ROOT):
            raise RuntimeError("Live PostgreSQL storage must be outside the teaching checkout")
        runtime = OwnedPostgresRuntime(storage / "lanshare-game-native", prefix="http-mixed-")
        evidence = root / runtime.target.name
        evidence.mkdir()
        try:
            with runtime:
                yield runtime, evidence
        finally:
            for name in (
                "report.json",
                "progress.json",
                "cleanup.json",
                "worker-0.log",
                "worker-1.log",
                "worker-2.log",
                "browser-executor.log",
                "cluster-commands.log",
                "postgres.log",
            ):
                file = runtime.target / name
                if file.is_file():
                    shutil.copy2(file, evidence / name)
            if runtime.cleanup_result and runtime.cleanup_result.get("status") != "failed":
                # This run's remaining logs were copied; delete exactly its known files.
                for file in runtime.target.iterdir():
                    if file.is_file() and not file.is_symlink():
                        file.unlink()
                runtime.target.rmdir()

    scratch = ROOT / ".codex-temp/scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    with (
        isolated() as (cluster, evidence),
        tempfile.TemporaryDirectory(prefix="game-http-", dir=scratch) as temporary,
    ):
        cluster.start()
        app_data = Path(temporary)
        browser_temp = app_data / "browser-temp"
        browser_temp.mkdir()
        owner = json.loads((cluster.target / ".native-pg-owner.json").read_text(encoding="utf8"))
        (app_data / ".owner").write_text(owner["token"], encoding="utf8")
        budget = WindowsServerBudget() if args.windows_2c4g else None
        if budget:
            budget.attach_database(cluster)
        secret = secrets.token_hex(32)
        ports = [port() for _ in range(3 if args.separate_classroom else 2)]
        if len(ports) != len(set(ports)):
            raise RuntimeError("Port collision")
        accounts = seed(f"postgresql://rehearsal_admin@127.0.0.1:{cluster.port}/postgres", secret, app_data)
        print("Owned evidence", evidence, flush=True)
        files = sorted((ROOT / "classroom_app/services").glob("minigame*.py"))
        files.extend(
            (
                ROOT / "classroom_app" / name
                for name in ("routers/minigame_runtime.py", "services/minigame_platform.py", "db/postgres.py")
            )
        )
        files.append(Path(__file__).resolve())
        if args.production_api:
            files.append(ROOT / "classroom_app/minigame_api.py")
        source = {
            file.relative_to(ROOT).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest() for file in files
        }
        with ExitStack() as controls:

            def stop(p):
                if p.poll() is None:
                    p.terminate()
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait(timeout=10)

            browser_job = None
            for w, webport in enumerate(ports):
                env = os.environ.copy()
                env.update(
                    MINIGAME_PROBE_OWNER=str(cluster.target),
                    MINIGAME_PROBE_DATA=str(app_data),
                    MINIGAMES_APP_ORIGIN=f"http://127.0.0.1:{ports[0]}",
                    MINIGAMES_CONTENT_ORIGIN=f"http://localhost:{ports[0]}",
                    MINIGAMES_EXTERNAL_API="1" if args.production_api else "0",
                    TEMP=str(browser_temp),
                    TMP=str(browser_temp),
                    TMPDIR=str(browser_temp),
                )
                package = ROOT / "docs/minigames/downloads/beacon-relay.zip"
                if hashlib.sha256(package.read_bytes()).hexdigest() != SAMPLE_ZIP_SHA256:
                    raise RuntimeError("Review the changed example before using the Windows browser probe")
                browsers = sorted(
                    (Path(os.environ["LOCALAPPDATA"]) / "ms-playwright").glob(
                        "chromium-*/chrome-win64/chrome.exe"
                    )
                )
                if not browsers:
                    raise RuntimeError("Installed Chromium required for the reviewed example")
                browser_python = ROOT / "venv/Scripts/python.exe"
                env["MINIGAMES_PROBE_COMMAND"] = json.dumps(
                    [
                        str(browser_python) if browser_python.is_file() else sys.executable,
                        "-I",
                        str(ROOT / "tools/minigame_browser_probe.py"),
                        "--browser",
                        str(browsers[-1]),
                        "--synthetic-digest",
                        SAMPLE_MANIFEST_SHA256,
                        "--trusted-browser-harness",
                    ]
                )
                if args.external_browser:
                    relay = ROOT / "tools/minigame_capacity_browser.py"
                    executable = str(browser_python) if browser_python.is_file() else sys.executable
                    if browser_job is None:
                        import psutil

                        unused = [
                            core for core in psutil.Process().cpu_affinity() if core not in budget.processors
                        ]
                        if len(unused) < 2:
                            raise RuntimeError("Two disjoint executor processors are required")
                        browser_job = WindowsServerBudget(unused[-2:])
                        controls.callback(browser_job.job.Close)
                        (app_data / "browser-handoff").mkdir()
                        log = controls.enter_context(
                            (cluster.target / "browser-executor.log").open("w", encoding="utf8")
                        )
                        permitted = {
                            "SYSTEMROOT",
                            "WINDIR",
                            "TEMP",
                            "TMP",
                            "TMPDIR",
                            "PATH",
                            "PROGRAMFILES",
                            "PROGRAMFILES(X86)",
                            "LOCALAPPDATA",
                            "APPDATA",
                            "PROGRAMDATA",
                            "SYSTEMDRIVE",
                            "COMSPEC",
                            "USERPROFILE",
                            "HOMEDRIVE",
                            "HOMEPATH",
                        }
                        runner = subprocess.Popen(
                            [executable, "-I", str(relay), "server", str(app_data), str(browsers[-1])],
                            env={name: value for name, value in env.items() if name.upper() in permitted},
                            stdout=log,
                            stderr=subprocess.STDOUT,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                        )
                        controls.callback(stop, runner)
                        browser_job.add(runner.pid)
                    env["MINIGAMES_PROBE_COMMAND"] = json.dumps(
                        [
                            executable,
                            "-I",
                            str(relay),
                            "--synthetic-digest",
                            SAMPLE_MANIFEST_SHA256,
                            "client",
                            str(app_data),
                        ]
                    )
                stream = controls.enter_context(
                    (cluster.target / f"worker-{w}.log").open("w", encoding="utf8")
                )
                proc = subprocess.Popen(
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "--owned-worker",
                        str(cluster.target),
                        "--port",
                        str(webport),
                        "--connections-per-api",
                        str(args.connections_per_api),
                        *(["--production-api"] if args.production_api else []),
                        *(["--classroom-only"] if w == 2 else []),
                    ],
                    cwd=ROOT,
                    env=env,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )

                controls.callback(stop, proc)
                if budget:
                    budget.add(proc.pid)
                if args.deprioritize_games and w < 2:
                    import psutil

                    psutil.Process(proc.pid).nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
            if args.verify_budget_only:
                if not budget:
                    raise ValueError("Select --windows-2c4g when verifying the server budget")
                result = budget.metrics()
                (cluster.target / "report.json").write_text(json.dumps(result), encoding="utf8")
                print(json.dumps(result), flush=True)
                return
            classroom_pool = controls.enter_context(
                ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))
            )
            report = asyncio.run(
                probe(
                    evidence,
                    ports[:2],
                    accounts,
                    args.seconds,
                    args.baseline,
                    classroom_pool,
                    ports[-1],
                    args.verify_http,
                )
            )
            report["serverLimits"] = budget.metrics() if budget else {"mode": "unconstrained host"}
            report["classroomProcess"] = (
                "dedicated" if args.separate_classroom else "shared with second games worker"
            )
            report["sourceSha256"] = source
            report["apiEntrypoint"] = (
                "classroom_app.minigame_api:app"
                if args.production_api
                else "isolated runtime/admin router host"
            )
            report["connectionsPerGameWorker"] = args.connections_per_api
            report["sampleBrowserPlacement"] = (
                browser_job.metrics() if browser_job else "same budget as classroom, games and PostgreSQL"
            )
            report["gameProcessScheduling"] = "below-normal" if args.deprioritize_games else "default"
            from importlib.metadata import version

            report["software"] = {
                "python": sys.version.split()[0],
                **{name: version(name) for name in ("psycopg", "fastapi", "uvicorn", "httpx", "playwright")},
            }
            (cluster.target / "report.json").write_text(json.dumps(report, indent=2), encoding="utf8")
            print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
