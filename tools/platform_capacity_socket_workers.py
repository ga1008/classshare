"""Spawn-isolated, loopback-only WebSocket receivers for synthetic capacity runs.

Only the parent generates HTTP load. Children decode socket frames and publish
small cumulative snapshots, keeping their JSON/GIL work off the HTTP event loop.
This module never imports the application or discovers a database/server URL.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
import json
import multiprocessing
import os
from pathlib import Path
import re
import socket
import ssl
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
NORMAL_EVENTS = {"connected", "application_ready", "frames"}
MAX_FAULT_DETAILS = 30


class SocketWorkerError(RuntimeError):
    pass


def _validate_spec(runtime, base_url, specs, cafile=None, *, lab_manifest=None, lab_manifest_sha256=None, lab_nonce=None):
    runtime = Path(runtime).resolve(strict=True)
    temporary = (ROOT / ".codex-temp").resolve()
    if runtime == temporary or not runtime.is_relative_to(temporary):
        raise ValueError("Socket workers require an owned .codex-temp fixture")
    if lab_manifest is not None:
        from tools.platform_capacity_lab_manifest import validate_socket_lab
        offering_id = validate_socket_lab(runtime, base_url, cafile, lab_manifest, lab_manifest_sha256, lab_nonce, ROOT)
    else:
        if lab_manifest_sha256 is not None or lab_nonce is not None:
            raise ValueError("Explicit lab manifest is required for lab socket pins")
        fixture = json.loads((runtime / "fixture.json").read_text(encoding="utf-8"))
        if (fixture.get("uiV3Synthetic") is not True
                or Path(fixture.get("databasePath", "")).resolve() != runtime / "db/classroom.db"):
            raise ValueError("Socket workers require an explicitly synthetic fixture")
        offering_id = int(fixture['classOfferingId'])
    url = urlsplit(base_url)
    if (url.scheme not in {"http", "https"} or url.hostname not in {"127.0.0.1", "::1"}
            or url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}
            or url.port is None or not 1024 <= url.port <= 65535):
        raise ValueError("Socket workers permit only an explicit loopback HTTP origin")
    if url.scheme == "https":
        certificate = Path(cafile or "").resolve(strict=True)
        if not certificate.is_file() or not certificate.is_relative_to(temporary):
            raise ValueError("Socket workers require an owned synthetic TLS certificate")
    elif cafile is not None:
        raise ValueError("A TLS certificate requires HTTPS")
    expected_paths = {"classroom": f"/ws/{offering_id}", "social": "/ws/social"}
    if not specs:
        raise ValueError("At least one synthetic socket is required")
    for spec in specs:
        token = spec.get("token")
        if (spec.get("path") != expected_paths.get(spec.get("channel")) or not isinstance(token, str)
                or not token or any(character in token for character in "\r\n")):
            raise ValueError("Socket target does not match the synthetic fixture")
    return runtime


def _install_child_boundary(runtime):
    # spawn imports this stdlib-only module before entering here. Keep OS
    # essentials, discard inherited integration credentials/proxies/DB settings.
    essential = {key: value for key, value in os.environ.items()
                 if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "COMSPEC", "PATHEXT"}}
    os.environ.clear()
    os.environ.update(essential)
    os.environ.update({"PYTHON_DOTENV_DISABLED": "1", "DB_ENGINE": "sqlite", "DATABASE_URL": "",
                       "POSTGRES_BACKEND_READY": "false", "POSTGRES_POOL_ENABLED": "false",
                       "LANSHARE_DATA_ROOT": str(runtime), "PYTHONIOENCODING": "utf-8"})
    original_connect, original_connect_ex, original_dns = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo

    def check(host):
        if str(host).lower() not in {"127.0.0.1", "::1", "localhost"}:
            raise RuntimeError("Socket worker forbids external sockets and DNS")

    def local_connect(sock, address):
        check(address[0])
        return original_connect(sock, address)

    def local_connect_ex(sock, address):
        check(address[0])
        return original_connect_ex(sock, address)

    def local_dns(host, *args, **kwargs):
        check(host)
        return original_dns(host, *args, **kwargs)

    socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = local_connect, local_connect_ex, local_dns


def socket_fault_detail(exc, *, channel, phase, target, tokens=()):
    """Keep native connection diagnostics without ever printing credentials."""
    try:
        detail = str(exc)
    except Exception:
        detail = type(exc).__name__
    for token in tokens:
        if isinstance(token, str) and token:
            detail = detail.replace(token, "[redacted]")
    detail = re.sub(r"(?i)((?:access_token|ls_remember|authorization)\s*[:=]\s*)[^\s;,]+", r"\1[redacted]", detail)
    return {"channel": channel, "phase": phase, "error": type(exc).__name__,
            "winerror": getattr(exc, "winerror", None), "errno": getattr(exc, "errno", None),
            "detail": detail[:200], "target": target}


def _snapshot(counts, *, kind="snapshot", ready=False, faults=(), timing=None):
    return {"type": kind, "ready": ready,
            "counts": [[channel, event, count] for (channel, event), count in sorted(counts.items())],
            "faults": list(faults), "timing": dict(timing or {})}


async def _receive_sockets(connection, base_url, specs, cafile, connect_limit=0):
    import websockets

    counts = Counter()
    sockets, readers = [], []
    closing = False
    faults = []
    timing = {"started_at": time.perf_counter(), "first_application_ready_at": None,
              "last_application_ready_at": None, "handshake_current": 0, "handshake_peak": 0,
              "handshakes_started": 0, "handshakes_finished": 0, "handshakes_cancelled": 0,
              "connectors_cancelled": 0, "expected_sockets": len(specs)}
    handshake_slots = asyncio.Semaphore(connect_limit) if connect_limit else None
    tokens = [spec["token"] for spec in specs]
    expected = Counter(spec["channel"] for spec in specs)
    tls = ssl.create_default_context(cafile=str(cafile)) if cafile else None
    ws_origin = base_url.rstrip("/").replace("https:", "wss:", 1).replace("http:", "ws:", 1)

    def fault(exc, channel, phase, target):
        if len(faults) < MAX_FAULT_DETAILS:
            faults.append(socket_fault_detail(exc, channel=channel, phase=phase, target=target, tokens=tokens))

    async def consume(websocket, channel, target):
        ready = False
        try:
            async for message in websocket:
                counts[(channel, "frames")] += 1
                frame = json.loads(message)
                if not ready and frame.get("type") == ("history" if channel == "classroom" else "hello"):
                    ready = True
                    counts[(channel, "application_ready")] += 1
                    stamp = time.perf_counter()
                    if timing["first_application_ready_at"] is None:
                        timing["first_application_ready_at"] = stamp
                    timing["last_application_ready_at"] = stamp
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            counts[(channel, type(exc).__name__)] += 1
            fault(exc, channel, "receive", target)
        finally:
            if not closing:
                counts[(channel, "closed_early")] += 1

    async def connect(spec):
        channel = spec["channel"]
        target = ws_origin + spec["path"]
        async def handshake():
            timing["handshake_current"] += 1
            timing["handshakes_started"] += 1
            timing["handshake_peak"] = max(timing["handshake_peak"], timing["handshake_current"])
            try:
                result = await websockets.connect(target,
                    additional_headers={"Cookie": f"access_token={spec['token']}"}, proxy=None,
                    open_timeout=30, close_timeout=2, max_queue=32, ping_interval=20,
                    **({"ssl": tls} if tls else {}))
            except asyncio.CancelledError:
                timing["handshakes_cancelled"] += 1
                raise
            except Exception:
                timing["handshakes_finished"] += 1
                raise
            else:
                timing["handshakes_finished"] += 1
                return result
            finally:
                timing["handshake_current"] -= 1
        try:
            if handshake_slots is None:
                websocket = await handshake()
            else:
                async with handshake_slots:
                    websocket = await handshake()
            sockets.append(websocket)
            counts[(channel, "connected")] += 1
            readers.append(asyncio.create_task(consume(websocket, channel, target)))
        except asyncio.CancelledError:
            timing["connectors_cancelled"] += 1
            raise
        except Exception as exc:
            counts[(channel, type(exc).__name__)] += 1
            fault(exc, channel, "connect", target)

    connectors = [asyncio.create_task(connect(spec)) for spec in specs]
    try:
        while True:
            if connection.poll():
                command = connection.recv()
                if command == "stop":
                    break
                raise SocketWorkerError("Unexpected socket worker command")
            ready = all(counts[(channel, "application_ready")] == total for channel, total in expected.items())
            connection.send(_snapshot(counts, ready=ready, faults=faults, timing=timing))
            await asyncio.sleep(.2)
    finally:
        closing = True
        for task in connectors:
            task.cancel()
        await asyncio.gather(*connectors, return_exceptions=True)
        try:
            await asyncio.wait_for(asyncio.gather(*(websocket.close() for websocket in sockets),
                                                return_exceptions=True), timeout=5)
        except TimeoutError:
            counts[("worker", "close_timeout")] += 1
        for task in readers:
            task.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        connection.send(_snapshot(counts, kind="done", faults=faults, timing=timing))


def _worker_entry(connection, runtime, base_url, specs, cafile, connect_limit=0,
                  lab_manifest=None, lab_manifest_sha256=None, lab_nonce=None):
    try:
        runtime = _validate_spec(runtime, base_url, specs, cafile, lab_manifest=lab_manifest,
                                 lab_manifest_sha256=lab_manifest_sha256, lab_nonce=lab_nonce)
        _install_child_boundary(runtime)
        asyncio.run(_receive_sockets(connection, base_url, specs, cafile, connect_limit))
    except (BrokenPipeError, EOFError):
        pass  # The parent is gone; never keep load running in the background.
    except BaseException as exc:
        try:
            connection.send({"type": "fatal", "error": type(exc).__name__, "faults": [socket_fault_detail(
                exc, channel="worker", phase="startup", target=base_url,
                tokens=[spec.get("token") for spec in specs if isinstance(spec, dict)])]})
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        connection.close()


@dataclass
class _Worker:
    process: object
    connection: object
    counts: Counter = field(default_factory=Counter)
    ready: bool = False
    finished: bool = False
    faulted: bool = False
    forced_stop: bool = False
    process_closed: bool = False
    pid: int | None = None
    exitcode: int | None = None
    final_snapshot: bool = False
    fault_details: list = field(default_factory=list)
    timing: dict = field(default_factory=dict)


class SocketWorkerGroup:
    """Own spawn children and aggregate cumulative counters exactly once."""
    def __init__(self, runtime, base_url, specs, *, workers=4, cafile=None, context=None, connect_limit=0,
                 lab_manifest=None, lab_manifest_sha256=None, lab_nonce=None):
        self.runtime = _validate_spec(runtime, base_url, specs, cafile, lab_manifest=lab_manifest,
                                     lab_manifest_sha256=lab_manifest_sha256, lab_nonce=lab_nonce)
        self.lab_pins = (lab_manifest, lab_manifest_sha256, lab_nonce) if lab_manifest is not None else ()
        if not 1 <= workers <= 16:
            raise ValueError("Socket worker count must be between 1 and 16")
        if type(connect_limit) is not int or connect_limit < 0:
            raise ValueError("Socket handshake limit must be a nonnegative integer")
        self.connect_limit = connect_limit
        self.started_at = None
        self.readiness_timeout = 30
        self.base_url, self.specs, self.cafile = base_url, list(specs), cafile
        self.worker_count = min(workers, len({spec["token"] for spec in specs}))
        self.context = context or multiprocessing.get_context("spawn")
        self.workers = []
        self.faults = Counter()
        self.monitor = None
        self.stopping = False
        self.stopped = False

    @property
    def counts(self):
        result = Counter(self.faults)
        for worker in self.workers:
            result.update(worker.counts)
        return result

    @property
    def worker_status(self):
        return [{"pid": worker.pid, "exitcode": worker.exitcode if worker.process_closed else worker.process.exitcode,
                 "ready": worker.ready, "finished": worker.finished, "forced_stop": worker.forced_stop,
                 "metrics_complete": worker.final_snapshot, "handshake_limit": self.connect_limit,
                 "handshake_peak": worker.timing.get("handshake_peak", 0),
                 "handshakes_started": worker.timing.get("handshakes_started", 0),
                 "handshakes_finished": worker.timing.get("handshakes_finished", 0),
                 "handshakes_cancelled": worker.timing.get("handshakes_cancelled", 0),
                 "connectors_cancelled": worker.timing.get("connectors_cancelled", 0),
                 "first_application_ready_ms": self._elapsed_ms(worker.timing.get("first_application_ready_at")),
                 "last_application_ready_ms": self._elapsed_ms(worker.timing.get("last_application_ready_at"))}
                for worker in self.workers]

    def _elapsed_ms(self, value):
        return round(max(0.0, value - self.started_at) * 1000, 2) if value is not None and self.started_at is not None else None

    @property
    def fault_details(self):
        return [{**detail, "worker_index": index, "pid": worker.pid}
                for index, worker in enumerate(self.workers) for detail in worker.fault_details]

    @property
    def timing(self):
        first = [worker.timing["first_application_ready_at"] for worker in self.workers
                 if worker.timing.get("first_application_ready_at") is not None]
        last = [worker.timing["last_application_ready_at"] for worker in self.workers
                if worker.timing.get("last_application_ready_at") is not None]
        return {"handshake_limit_per_worker": self.connect_limit,
                "aggregate_handshake_limit": self.connect_limit * len(self.workers),
                "handshake_mode": "bounded" if self.connect_limit else "unbounded",
                "fault_details_limit_per_worker": MAX_FAULT_DETAILS,
                "first_application_ready_ms": self._elapsed_ms(min(first)) if first else None,
                "last_application_ready_ms": self._elapsed_ms(max(last)) if last else None,
                "application_ready_deadline_seconds": self.readiness_timeout,
                "expected_sockets": len(self.specs),
                **{key: sum(worker.timing.get(key, 0) for worker in self.workers) for key in (
                    "handshakes_started", "handshakes_finished", "handshakes_cancelled", "connectors_cancelled")},
                "not_started_sockets": len(self.specs) - sum(worker.timing.get("handshakes_started", 0) for worker in self.workers)}

    def _fault(self, worker, event):
        if not worker.faulted:
            self.faults[("worker", event)] += 1
            worker.faulted = True

    def refresh(self):
        for worker in self.workers:
            if worker.process_closed:
                continue
            try:
                while worker.connection.poll():
                    message = worker.connection.recv()
                    kind = message.get("type")
                    details = message.get("faults", [])
                    if not isinstance(details, list) or len(details) > MAX_FAULT_DETAILS or any(not isinstance(detail, dict) for detail in details):
                        raise ValueError("Malformed socket worker fault details")
                    worker.fault_details = (worker.fault_details + details)[:MAX_FAULT_DETAILS] if kind == "fatal" else details
                    worker.timing = message.get("timing", worker.timing)
                    if not isinstance(worker.timing, dict):
                        raise ValueError("Malformed socket worker timing")
                    if kind == "fatal":
                        self._fault(worker, "fatal_error")
                        worker.finished = True
                    elif kind in {"snapshot", "done"}:
                        counts = Counter()
                        for channel, event, count in message["counts"]:
                            if (channel not in {"classroom", "social", "worker"} or not isinstance(event, str)
                                    or not isinstance(count, int) or count < 0):
                                raise ValueError("Malformed socket worker counters")
                            counts[(channel, event)] = count
                        worker.counts = counts
                        worker.ready = bool(message.get("ready")) or worker.ready
                        worker.finished = kind == "done"
                        if kind == "done":
                            worker.final_snapshot = True
                            if not self.stopping:
                                self._fault(worker, "unexpected_completion")
                    else:
                        raise ValueError("Malformed socket worker response")
            except (EOFError, OSError):
                if not worker.finished:
                    self._fault(worker, "pipe_closed")
                    worker.finished = True
            except (AttributeError, KeyError, TypeError, ValueError):
                self._fault(worker, "protocol_error")
                worker.finished = True
            if worker.process.exitcode is not None:
                if not worker.finished or worker.process.exitcode != 0:
                    self._fault(worker, "unexpected_exit")
                worker.finished = True

    async def start(self):
        if self.workers or self.stopped:
            raise SocketWorkerError("Socket worker group cannot be restarted")
        chunks = [[] for _ in range(self.worker_count)]
        self.started_at = time.perf_counter()
        owners = {}
        for spec in self.specs:
            owner = owners.setdefault(spec["token"], len(owners)) % self.worker_count
            chunks[owner].append(spec)
        try:
            for chunk in chunks:
                parent, child = self.context.Pipe(duplex=True)
                process = None
                try:
                    process = self.context.Process(target=_worker_entry,
                        args=(child, self.runtime, self.base_url, chunk, self.cafile, self.connect_limit, *self.lab_pins), daemon=True)
                    process.start()
                except BaseException:
                    parent.close()
                    if process is not None:
                        process.close()
                    raise
                finally:
                    child.close()
                self.workers.append(_Worker(process, parent, pid=process.pid))
            self.monitor = asyncio.create_task(self._monitor())
        except BaseException:
            self.faults[("worker", "spawn_error")] += 1
            await self.stop()
            raise

    async def _monitor(self):
        while not self.stopped:
            self.refresh()
            await asyncio.sleep(.1)

    async def wait_ready(self, timeout=30):
        self.readiness_timeout = timeout
        expected = Counter(spec["channel"] for spec in self.specs)
        deadline = (self.started_at if self.started_at is not None else time.perf_counter()) + timeout
        while True:
            self.refresh()
            counts = self.counts
            if (len(self.workers) != self.worker_count or any(worker.finished or worker.faulted for worker in self.workers)
                    or any(count for (_, event), count in counts.items() if event not in NORMAL_EVENTS)):
                raise SocketWorkerError("Socket workers failed before every application hello/history")
            if (all(worker.ready for worker in self.workers)
                    and all(counts[(channel, event)] == total for channel, total in expected.items()
                            for event in ("connected", "application_ready"))):
                last_ready = max((worker.timing.get("last_application_ready_at") or time.perf_counter()
                                  for worker in self.workers), default=time.perf_counter())
                if last_ready > deadline:
                    self.faults[("worker", "readiness_timeout")] += 1
                    raise SocketWorkerError("Socket workers completed application readiness after the deadline")
                return
            if time.perf_counter() >= deadline:
                self.faults[("worker", "readiness_timeout")] += 1
                raise SocketWorkerError("Socket workers timed out before every application hello/history")
            await asyncio.sleep(.05)

    async def _wait_exit(self, timeout):
        deadline = time.monotonic() + timeout
        while any(worker.process.is_alive() for worker in self.workers):
            self.refresh()
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(.05)
        self.refresh()
        return True

    async def stop(self):
        if self.stopped:
            return
        self.stopping = True
        for worker in self.workers:
            if worker.process.is_alive():
                try:
                    worker.connection.send("stop")
                except (BrokenPipeError, EOFError, OSError):
                    self._fault(worker, "stop_pipe_error")
        if not await self._wait_exit(10):
            for worker in self.workers:
                if worker.process.is_alive():
                    worker.forced_stop = True
                    self._fault(worker, "forced_terminate")
                    worker.process.terminate()
            if not await self._wait_exit(3):
                for worker in self.workers:
                    if worker.process.is_alive():
                        worker.process.kill()
                if not await self._wait_exit(2):
                    self.faults[("worker", "cleanup_failed")] += 1
        self.refresh()
        self.stopped = True
        if self.monitor:
            self.monitor.cancel()
            await asyncio.gather(self.monitor, return_exceptions=True)
        for worker in self.workers:
            worker.process.join(timeout=0)
            worker.connection.close()
            if not worker.process.is_alive():
                worker.exitcode = worker.process.exitcode
                worker.process.close()
                worker.process_closed = True
