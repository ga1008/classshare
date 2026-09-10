#!/usr/bin/env python3
"""Narrow host control plane for isolated DSH runners (Python stdlib only).

Only this service uses Docker. Worker IPC is a protected Unix socket; a runner
receives a different HTTP gateway socket and has no external network interface.
Caller input cannot choose an image, executable, mount, URL or resource budget.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import http.client
import http.server
import json
import os
from pathlib import Path
import re
import select
import signal
import socket
import socketserver
import stat
import subprocess
import threading
import time
from urllib.parse import urlsplit
import uuid

MAX_CONTROL_BYTES = 16384
MAX_HTTP_BYTES = 2 * 1024 * 1024
MAX_WORKSPACE_BYTES = 128 * 1024 * 1024
MAX_WORKSPACE_ENTRIES = 20000
SOCKET_ROOT = "/run/lanshare-agent"
TOKEN_PATTERN = re.compile(r"lsagt_[A-Za-z0-9_-]{32,128}")
LABEL = "lanshare.agent.runtime=deepseek-dsh"
DOCKER = ("/usr/bin/docker", "--host", "unix:///var/run/docker.sock")


def validate_identity(value):
    if not isinstance(value, dict):
        raise ValueError("Invalid launcher request")
    task_id = value.get("task_id")
    fence = value.get("fencing_token")
    if any(isinstance(n, bool) or not isinstance(n, int) or not 0 < n < 2**63 for n in (task_id, fence)):
        raise ValueError("Invalid task or fencing identity")
    attempt = str(value.get("attempt_id") or "")
    if str(uuid.UUID(attempt)) != attempt:
        raise ValueError("Invalid attempt identity")
    if not re.fullmatch(r"(?:teacher|student):[1-9][0-9]{0,18}", str(value.get("actor_id") or "")):
        raise ValueError("Invalid actor identity")
    return task_id, attempt, fence


def safe_child(root: Path, *parts: str) -> Path:
    root = root.resolve(strict=True)
    path = root.joinpath(*parts)
    if root not in path.resolve().parents:
        raise ValueError("Path escapes launcher root")
    for ancestor in (path, *path.parents):
        if ancestor == root:
            break
        if ancestor.is_symlink():
            raise ValueError("Symlink in launcher path")
    return path


def runner_name(task_id, attempt_id):
    return f"lanshare-dsh-{task_id}-{attempt_id}"


def approved_environment(request):
    task_id, attempt, fence = validate_identity(request)
    for name in ("model_token", "tools_token"):
        if not TOKEN_PATTERN.fullmatch(str(request.get(name) or "")):
            raise ValueError("Invalid task credential")
    if request["model_token"] == request["tools_token"]:
        raise ValueError("Task credential purposes must be separate")
    for key in ("model", "search_model"):
        if key == "search_model" and not request.get(key):
            continue
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", str(request.get(key) or "")):
            raise ValueError("Invalid model setting")
    env = {
        "DSH_TASK_ID": str(task_id), "DSH_ACTOR_ID": request["actor_id"],
        "DSH_ATTEMPT_ID": attempt, "DSH_FENCING_TOKEN": str(fence),
        "DSH_GATEWAY_BASE_URL": "http://127.0.0.1:8787/api/agent-model",
        "DSH_GATEWAY_MODEL": request["model"],
        "DSH_GATEWAY_TOKEN": request["model_token"],
        "DSH_BROKER_MCP_URL": "http://127.0.0.1:8787/api/agent-bridge/mcp",
        "DSH_BROKER_TOKEN": request["tools_token"],
    }
    if request.get("search_model"):
        env["DSH_SEARCH_MODEL"] = request["search_model"]
    return env


def container_arguments(config, request, env_file, home):
    task_id, attempt, fence = validate_identity(request)
    workspace = safe_child(config.task_root, "tasks", str(task_id))
    profile = config.profile.resolve(strict=True)
    gateway = config.socket_root / "gateway.sock"
    for path in (workspace, home, profile, gateway):
        if "," in str(path) or "\n" in str(path):
            raise ValueError("Unsupported host mount path")
    return [
        "create", "--name", runner_name(task_id, attempt), "--interactive", "--log-driver", "none",
        "--label", LABEL, "--label", f"lanshare.agent.task={task_id}",
        "--label", f"lanshare.agent.attempt={attempt}", "--label", f"lanshare.agent.fence={fence}",
        "--label", f"lanshare.agent.actor={request['actor_id']}",
        "--network", "none", "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true", "--pids-limit", "128",
        "--memory", "1024m", "--memory-swap", "1024m", "--cpus", "1",
        "--ulimit", "nofile=1024:1024", "--user", "10001:10001",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=128m,mode=1777",
        "--mount", f"type=bind,src={workspace},dst=/workspace",
        "--mount", f"type=bind,src={home},dst=/var/lib/dsh",
        "--mount", f"type=bind,src={profile},dst=/var/lib/dsh/profiles/lanshare,readonly",
        "--mount", f"type=bind,src={home / 'runtime-cordis.yml'},dst=/var/lib/dsh/profiles/lanshare/cordis.yml",
        "--mount", f"type=bind,src={gateway},dst={SOCKET_ROOT}/gateway.sock,readonly",
        "--env-file", str(env_file), config.image,
    ]


def docker(*args, timeout=30):
    result = subprocess.run([*DOCKER, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=timeout, check=False, env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"})
    if result.returncode:
        # Docker errors may include container env or request data. Log neither.
        raise RuntimeError("Container control operation failed")
    return result.stdout


def workspace_usage(root):
    total, count = 0, 0
    pending = [Path(root)]
    while pending:
        # scandir streams a large directory; os.walk first materializes every
        # entry and permits zero-byte files to exhaust scanner memory.
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                count += 1
                if count > MAX_WORKSPACE_ENTRIES:
                    return total, count
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISREG(info.st_mode):
                    total += info.st_size
                    if total > MAX_WORKSPACE_BYTES:
                        return total, count
                elif stat.S_ISDIR(info.st_mode):
                    pending.append(Path(entry.path))
    return total, count


def workspace_size(root):
    return workspace_usage(root)[0]


def workspace_exceeded(root):
    size, entries = workspace_usage(root)
    return size > MAX_WORKSPACE_BYTES or entries > MAX_WORKSPACE_ENTRIES


class Launcher:
    def __init__(self, config):
        self.config = config
        if not re.fullmatch(r"(?:[^\s]+@)?sha256:[0-9a-f]{64}", config.image):
            raise ValueError("Launcher requires an immutable image digest")
        self.lock = threading.Lock()
        self.lifecycle_lock = threading.RLock()
        self.active = set()
        self.stopping = threading.Event()
        self.unhealthy = threading.Event()
        if not 1 <= config.max_concurrency <= 2 or not 1 <= config.max_runtime_seconds <= 7200:
            raise ValueError("Invalid runner capacity or deadline")
        # Official 0.1.5's loader mkdirs these even for an in-tree-only profile.
        # Precreate them as root before exposing the directory read-only.
        for relative in ("node_modules", ".dsh-module-fallback/node_modules"):
            (config.profile / relative).mkdir(parents=True, exist_ok=True)
        # The pinned official launcher always resets this generated root file.
        # Its per-attempt writable bind does not include the patch or manifest.
        if not (config.profile / "cordis.yml").exists():
            (config.profile / "cordis.yml").touch()
        evidence = json.loads(docker("run", "--rm", "--network", "none", "--read-only",
                                     "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                                     "--memory", "256m", "--pids-limit", "32", config.image, "--evidence"))
        if (evidence.get("dsh_package_version") != "0.1.5-rc.1"
                or not re.fullmatch(r"[0-9a-f]{64}", str(evidence.get("profile_sha256") or ""))):
            raise ValueError("Image evidence is invalid")
        digest = hashlib.sha256()
        for name in ("package.json", "cordis.patch.yml"):
            digest.update(name.encode() + b"\0")
            digest.update((config.profile / name).read_bytes().replace(b"\r\n", b"\n") + b"\0")
        if digest.hexdigest() != evidence["profile_sha256"]:
            raise ValueError("Deployed profile does not match runner image")
        self.evidence = {**evidence, "image": config.image, "network": "none", "max_concurrency": config.max_concurrency}

    def names(self):
        return set(docker("ps", "--all", "--filter", "label=" + LABEL, "--format", "{{.Names}}").decode().splitlines())

    def remove_confirmed(self, name):
        with self.lifecycle_lock:
            self._remove_confirmed(name)

    def _remove_confirmed(self, name):
        # Even a successful stop is insufficient: confirm the container no
        # longer exists before releasing capacity or acknowledging termination.
        with contextlib.suppress(RuntimeError, subprocess.TimeoutExpired):
            docker("rm", "--force", name, timeout=10)
        try:
            deadline = time.monotonic() + 5
            while name in self.names():
                if time.monotonic() >= deadline:
                    raise RuntimeError("Runner termination is unconfirmed")
                time.sleep(.1)
        except Exception:
            self.unhealthy.set()
            raise

    def stop_orphans(self):
        # An IPC disconnect cannot be resumed. A launcher restart ends old
        # attempts; the platform must reconcile and issue fresh fenced ones.
        for name in self.names():
            self.remove_confirmed(name)

    def shutdown(self):
        self.stopping.set()
        with self.lock:
            self.stop_orphans()

    def stop(self, request):
        with self.lifecycle_lock:
            return self._stop_owned(request)

    def _stop_owned(self, request):
        task_id, attempt, fence = validate_identity(request)
        name = runner_name(task_id, attempt)
        if name not in self.names():
            return {"status": "stopped", "name": name}
        try:
            inspected = json.loads(docker("inspect", "--type", "container", name))
        except RuntimeError:
            if name not in self.names():
                return {"status": "stopped", "name": name}
            raise
        labels = inspected[0].get("Config", {}).get("Labels", {})
        expected = {"lanshare.agent.runtime": "deepseek-dsh", "lanshare.agent.task": str(task_id),
                    "lanshare.agent.attempt": attempt, "lanshare.agent.fence": str(fence),
                    "lanshare.agent.actor": request["actor_id"]}
        if any(labels.get(key) != value for key, value in expected.items()):
            raise ValueError("Stop identity does not match runner ownership")
        try:
            docker("stop", "--time", "5", name, timeout=10)
        except (RuntimeError, subprocess.TimeoutExpired):
            pass
        self.remove_confirmed(name)
        return {"status": "stopped", "name": name}

    def run(self, request, connection):
        task_id, attempt, _ = validate_identity(request)
        env = approved_environment(request)
        name = runner_name(task_id, attempt)
        home = safe_child(self.config.state_root, str(task_id), attempt)
        workspace = safe_child(self.config.task_root, "tasks", str(task_id))
        if not workspace.is_dir():
            raise ValueError("Task workspace does not exist")
        if workspace_exceeded(workspace):
            raise ValueError("Task workspace exceeds byte or entry quota")
        with self.lock:
            if self.stopping.is_set() or self.unhealthy.is_set():
                raise ValueError("Launcher is not accepting tasks")
            # Includes created/starting containers after a launcher restart.
            existing = self.names()
            if len(existing | self.active) >= self.config.max_concurrency:
                raise ValueError("Runner capacity is full")
            if any(value.startswith(f"lanshare-dsh-{task_id}-") for value in existing | self.active):
                raise ValueError("Task already has a runner")
            if home.exists():
                raise ValueError("Attempt already started; recover with a new fenced attempt")
            home.mkdir(parents=True, mode=0o750)
            (home / "profiles" / "lanshare").mkdir(parents=True)
            (home / "runtime-cordis.yml").write_text("[]\n")
            for root in (home, workspace):
                for base, dirs, files in os.walk(root, followlinks=False):
                    if any((Path(base) / n).is_symlink() for n in dirs):
                        raise ValueError("Symlink directory in task workspace")
                    os.chown(base, 10001, 10001)
                    for filename in files:
                        path = Path(base) / filename
                        info = path.lstat()
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                            raise ValueError("Unsafe file in task workspace")
                        os.chown(path, 10001, 10001, follow_symlinks=False)
            env_file = self.config.socket_root / (attempt + ".env")
            try:
                descriptor = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w") as handle:
                    handle.write("\n".join(f"{key}={value}" for key, value in env.items()) + "\n")
                try:
                    docker(*container_arguments(self.config, request, env_file, home))
                except Exception:
                    self.remove_confirmed(name)
                    raise
            finally:
                env_file.unlink(missing_ok=True)
            self.active.add(name)
        process = None
        stopped = threading.Event()
        try:
            process = subprocess.Popen([*DOCKER, "start", "--attach", "--interactive", name],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"})
            connection.sendall(json.dumps({"status": "ready", **self.evidence}).encode() + b"\n")

            def ingress():
                try:
                    while data := connection.recv(65536):
                        process.stdin.write(data)
                        process.stdin.flush()
                except (BrokenPipeError, OSError):
                    pass
                finally:
                    stopped.set()
                    with contextlib.suppress(OSError):
                        process.stdin.close()

            def monitor():
                deadline = time.monotonic() + self.config.max_runtime_seconds
                try:
                    while not stopped.wait(1):
                        if (self.stopping.is_set() or time.monotonic() >= deadline
                                or workspace_exceeded(workspace) or workspace_exceeded(home)):
                            stopped.set()
                except OSError:
                    stopped.set()
                try:
                    self.stop(request)
                except Exception:
                    self.unhealthy.set()
                finally:
                    # Interrupt a caller that is not reading ACP output and a
                    # docker-attach process stuck after container termination.
                    with contextlib.suppress(OSError):
                        connection.shutdown(socket.SHUT_RDWR)
                    if process.poll() is None:
                        process.kill()

            threading.Thread(target=ingress, daemon=True).start()
            monitor_thread = threading.Thread(target=monitor, daemon=True)
            monitor_thread.start()
            try:
                while data := process.stdout.read1(65536):
                    connection.sendall(data)
            finally:
                stopped.set()
                monitor_thread.join(timeout=25)
        finally:
            stopped.set()
            if process:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            try:
                self.remove_confirmed(name)
            finally:
                if not self.unhealthy.is_set():
                    with self.lock:
                        self.active.discard(name)


class ControlHandler(socketserver.BaseRequestHandler):
    def handle(self):
        streaming = False
        try:
            self.request.settimeout(10)
            deadline = time.monotonic() + 10
            # Read exactly one frame without buffering any initial ACP request.
            frame = bytearray()
            while not frame.endswith(b"\n"):
                if len(frame) >= MAX_CONTROL_BYTES:
                    raise ValueError("Launcher frame too large")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Launcher control frame timed out")
                self.request.settimeout(remaining)
                chunk = self.request.recv(1)
                if not chunk:
                    return
                frame.extend(chunk)
            request = json.loads(frame)
            if not isinstance(request, dict):
                raise ValueError("Invalid launcher frame")
            if request.get("version") != 1:
                raise ValueError("Unsupported launcher protocol")
            action = request.get("action")
            if action == "probe":
                if self.server.launcher.unhealthy.is_set() or self.server.launcher.stopping.is_set():
                    raise ValueError("Launcher is unhealthy")
                result = {"status": "ready", **self.server.launcher.evidence}
            elif action == "stop":
                result = self.server.launcher.stop(request)
            elif action == "run":
                self.request.settimeout(None)
                streaming = True
                self.server.launcher.run(request, self.request)
                return
            else:
                raise ValueError("Unsupported launcher action")
            self.request.sendall(json.dumps(result).encode() + b"\n")
        except Exception:
            # Fixed text only: request contains task credentials.
            if not streaming:
                with contextlib.suppress(OSError):
                    self.request.sendall(b'{"status":"error","message":"Launcher request refused"}\n')


class UnixServer(socketserver.ThreadingMixIn, getattr(socketserver, "UnixStreamServer", socketserver.TCPServer)):
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        if not hasattr(socketserver, "UnixStreamServer"):
            raise RuntimeError("The launcher service requires Linux Unix sockets")
        self.slots = threading.BoundedSemaphore(16)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class GatewayHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def setup(self):
        self.request.settimeout(15)
        super().setup()

    def handle_one_request(self):
        def expire_headers():
            with contextlib.suppress(OSError):
                self.connection.shutdown(socket.SHUT_RDWR)
        self.header_deadline = threading.Timer(15, expire_headers)
        self.header_deadline.daemon = True
        self.header_deadline.start()
        try:
            super().handle_one_request()
        finally:
            self.header_deadline.cancel()

    def log_message(self, *args):
        pass  # Never log token-bearing request URLs or headers.

    def do_GET(self):
        self.proxy()

    def do_POST(self):
        self.proxy()

    def do_DELETE(self):
        self.proxy()

    def proxy(self):
        self.header_deadline.cancel()
        parsed = urlsplit(self.path)
        allowed = {"/api/agent-bridge/meta": {"GET"}, "/api/agent-bridge/schema": {"GET"},
                   "/api/agent-bridge/query": {"POST"}, "/api/agent-bridge/search": {"POST"},
                   "/api/agent-bridge/file": {"POST"}, "/api/agent-bridge/web": {"POST"},
                   "/api/agent-bridge/mcp": {"GET", "POST", "DELETE"},
                   "/api/agent-bridge/questions": {"POST"},
                   "/api/agent-bridge/children/admit": {"POST"},
                   "/api/agent-model/chat/completions": {"POST"}, "/api/agent-model/messages": {"POST"}}
        question = re.fullmatch(r"/api/agent-bridge/questions/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(/cancel)?", parsed.path)
        if question:
            allowed[parsed.path] = {"POST" if question[1] else "GET"}
        if re.fullmatch(r"/api/agent-bridge/children/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/finish", parsed.path):
            allowed[parsed.path] = {"POST"}
        if (parsed.scheme or parsed.netloc or parsed.path not in allowed or parsed.query or parsed.fragment
                or self.command not in allowed.get(parsed.path, ())):
            self.send_error(404)
            return
        if self.headers.get("Transfer-Encoding"):
            self.send_error(400)
            return
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) > 1 or (lengths and not re.fullmatch(r"[0-9]{1,10}", lengths[0])):
            self.send_error(400)
            return
        size = int(lengths[0]) if lengths else 0
        if self.command == "POST" and not lengths:
            self.send_error(411)
            return
        if not 0 <= size <= MAX_HTTP_BYTES:
            self.send_error(413)
            return
        deadline = time.monotonic() + 30
        body = bytearray()
        while len(body) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.send_error(408)
                return
            self.connection.settimeout(remaining)
            chunk = self.rfile.read1(min(65536, size - len(body)))
            if not chunk:
                break
            body.extend(chunk)
        if len(body) != size:
            self.send_error(400)
            return
        headers = {key: self.headers[key] for key in ("Authorization", "x-api-key", "Content-Type", "Accept",
                   "MCP-Protocol-Version", "Mcp-Session-Id", "anthropic-version", "anthropic-beta",
                   "Last-Event-ID") if self.headers.get(key)}
        if any(len(self.headers.get_all(key, [])) > 1 for key in headers):
            self.send_error(400)
            return
        connection = http.client.HTTPConnection(self.server.upstream_host, self.server.upstream_port, timeout=90)
        finished = threading.Event()
        caller_gone = threading.Event()
        upstream_socket = [None]
        started_response = False

        def disconnected():
            while not finished.wait(.2):
                try:
                    readable, _, _ = select.select([self.connection], [], [], .2)
                    if readable and not self.connection.recv(1, socket.MSG_PEEK):
                        caller_gone.set()
                        if upstream_socket[0]:
                            upstream_socket[0].shutdown(socket.SHUT_RDWR)
                        return
                except OSError:
                    return

        watcher = threading.Thread(target=disconnected, daemon=True)
        watcher.start()
        try:
            connection.request(self.command, parsed.path, body=bytes(body), headers=headers)
            upstream_socket[0] = connection.sock
            if caller_gone.is_set():
                return
            response = connection.getresponse()
            self.send_response(response.status)
            started_response = True
            for key, value in response.getheaders():
                if key.lower() in {"content-type", "content-length", "mcp-session-id", "x-agent-request-id", "cache-control", "retry-after"}:
                    self.send_header(key, value)
            self.send_header("Connection", "close")
            self.end_headers()
            while chunk := response.read1(65536):
                self.wfile.write(chunk)
                self.wfile.flush()
        except (OSError, http.client.HTTPException):
            self.close_connection = True
            if not started_response:
                with contextlib.suppress(OSError):
                    self.send_error(502)
        finally:
            finished.set()
            connection.close()
            watcher.join(timeout=1)


def main():
    if os.name != "posix":
        raise SystemExit("The launcher service requires the Linux deployment host")
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--task-root", type=Path, default=Path("/lanshare/data/agent_tasks"))
    parser.add_argument("--state-root", type=Path, default=Path("/lanshare/data/agent_dsh_state"))
    parser.add_argument("--profile", type=Path, default=Path("/lanshare/deployment/dsh/profile"))
    parser.add_argument("--socket-root", type=Path, default=Path(SOCKET_ROOT))
    parser.add_argument("--upstream-port", type=int, default=18000)
    parser.add_argument("--control-gid", type=int, default=0)
    parser.add_argument("--max-concurrency", type=int, choices=(1, 2), default=1)
    parser.add_argument("--max-runtime-seconds", type=int, default=1800)
    config = parser.parse_args()
    if os.getuid() != 0:
        raise SystemExit("Launcher must run as its dedicated host service")
    if not 1 <= config.upstream_port <= 65535 or not 0 <= config.control_gid < 2**31:
        raise ValueError("Invalid trusted service configuration")
    for root in (config.task_root, config.state_root, config.socket_root):
        if root.is_symlink():
            raise ValueError("Launcher roots must not be symbolic links")
        root.mkdir(parents=True, exist_ok=True)
        if root.stat().st_uid != 0 or root.stat().st_mode & 0o022:
            raise ValueError("Launcher roots must be root-owned and not group/other-writable")
    if config.profile.is_symlink() or config.profile.stat().st_uid != 0 or config.profile.stat().st_mode & 0o022:
        raise ValueError("Runner profile must be controlled by the host service")
    import fcntl
    descriptor = os.open(config.socket_root / "service.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1:
        os.close(descriptor)
        raise ValueError("Invalid launcher singleton lock")
    service_lock = os.fdopen(descriptor, "r+")
    fcntl.flock(service_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.chmod(config.socket_root, 0o751)
    control_root = safe_child(config.socket_root, "control")
    control_root.mkdir(mode=0o750, exist_ok=True)
    os.chmod(control_root, 0o750)
    os.chown(control_root, 0, config.control_gid)
    launcher = Launcher(config)
    launcher.stop_orphans()
    servers = []
    ending = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: ending.set())
    signal.signal(signal.SIGINT, lambda *_: ending.set())
    for name, handler in (("launcher.sock", ControlHandler), ("gateway.sock", GatewayHandler)):
        path = (control_root if name == "launcher.sock" else config.socket_root) / name
        if os.path.lexists(path):
            if not stat.S_ISSOCK(path.lstat().st_mode):
                raise ValueError("Refusing to replace non-socket IPC path")
            path.unlink()
        server = UnixServer(str(path), handler)
        server.launcher = launcher
        server.upstream_host, server.upstream_port = "127.0.0.1", config.upstream_port
        os.chmod(path, 0o660)
        os.chown(path, 0, 10001 if name == "gateway.sock" else config.control_gid)
        servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        ending.wait()
    finally:
        launcher.shutdown()
        for server in servers:
            server.shutdown()
            server.server_close()
        service_lock.close()


if __name__ == "__main__":
    main()
