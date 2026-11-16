"""Lifecycle of a newly owned local PostgreSQL test cluster, retaining evidence.

This helper never adopts an existing cluster. Only its own ``cluster`` and
``runtime`` children are disposable; reports, logs and backup dumps stay beside
them. A failed ownership or shutdown check leaves both children in place.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import stat
import subprocess
import tempfile
import threading
import uuid


def _is_link(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    # is_junction is unavailable on Python 3.11; cloud placeholders carry a
    # different reparse tag and must not be mistaken for directory redirection.
    return stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", None) in {
        getattr(stat, "IO_REPARSE_TAG_SYMLINK", 0xA000000C),
        getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003),
    }


@contextmanager
def _finish_cleanup_uninterrupted():
    """A second Ctrl-C must not interrupt the bounded shutdown already running."""
    previous = {}
    if threading.current_thread() is threading.main_thread():
        for number in (signal.SIGINT, signal.SIGTERM):
            previous[number] = signal.signal(number, signal.SIG_IGN)
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


class OwnedPostgresRuntime:
    """Create one unique evidence folder and clean its verified disposable data."""

    def __init__(self, artifacts_root: Path, *, prefix: str = "native-pg-",
                 pg_ctl: Path | None = None, keep_runtime: bool = False):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", prefix):
            raise ValueError("A plain temporary-directory prefix is required")
        artifacts_root = Path(artifacts_root).absolute()
        if any(_is_link(path) for path in (artifacts_root, *artifacts_root.parents)):
            raise ValueError("The artifact root and its ancestors must not be links")
        # Resolve the port before creating anything. Constructor failures never
        # reach __exit__, so they must not leave newly created data directories.
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        if self.port == 5432:
            raise RuntimeError("Refuse the default PostgreSQL port")
        artifacts_root.mkdir(parents=True, exist_ok=True)
        self.artifacts_root = artifacts_root.resolve(strict=True)
        self.pg_ctl = Path(pg_ctl or shutil.which("pg_ctl") or
                           r"C:\Program Files\PostgreSQL\16\bin\pg_ctl.exe")
        self.initdb = self.pg_ctl.with_name("initdb.exe" if self.pg_ctl.suffix == ".exe" else "initdb")
        with ExitStack() as creation:
            self.target = Path(tempfile.mkdtemp(prefix=prefix, dir=self.artifacts_root))
            creation.callback(self.target.rmdir)
            self.cluster, self.runtime = self.target / "cluster", self.target / "runtime"
            for path in (self.cluster, self.runtime):
                path.mkdir()
                creation.callback(path.rmdir)
            self._identities = {path: (path.stat().st_dev, path.stat().st_ino)
                                for path in (self.target, self.cluster, self.runtime)}
            self._owner = {"kind": "owned-native-postgres-v1", "token": uuid.uuid4().hex,
                           "cluster": str(self.cluster), "runtime": str(self.runtime)}
            self._marker = self.target / ".native-pg-owner.json"
            with self._marker.open("x", encoding="utf-8") as stream:
                creation.callback(self._marker.unlink)
                json.dump(self._owner, stream, indent=2)
            command_log = self.target / "cluster-commands.log"
            self._command_log = command_log.open("xb")
            creation.callback(command_log.unlink)
            creation.callback(self._command_log.close)
            self._postgres_log = self.target / "postgres.log"
            with self._postgres_log.open("xb") as stream:
                creation.callback(self._postgres_log.unlink)
                info = os.fstat(stream.fileno())
                self._postgres_log_identity = (info.st_dev, info.st_ino)
            creation.pop_all()
        self.keep_runtime = keep_runtime
        self._start_attempted = False
        self.cleanup_result: dict | None = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        with _finish_cleanup_uninterrupted():
            result = self.cleanup()
        if result["status"] == "failed" and exc_type is None:
            raise RuntimeError("Owned PostgreSQL cleanup failed; inspect cleanup.json")
        return False

    def _assert_identity(self, path: Path) -> None:
        if _is_link(path) or path.resolve(strict=True) != path:
            raise ValueError("Owned runtime path was replaced by a link")
        current = path.stat()
        if (current.st_dev, current.st_ino) != self._identities[path]:
            raise ValueError("Owned runtime directory identity changed")

    def _assert_owned(self) -> None:
        if self.target.parent != self.artifacts_root:
            raise ValueError("Evidence directory escaped its artifact root")
        for path in (self.target, self.cluster, self.runtime):
            self._assert_identity(path)
        if _is_link(self._marker) or json.loads(self._marker.read_text(encoding="utf-8")) != self._owner:
            raise ValueError("Owned PostgreSQL marker mismatch")

    def _invoke(self, binary: Path, arguments: list[str]) -> subprocess.CompletedProcess:
        # Native PG children can inherit pipe handles on Windows. File-backed
        # output lets subprocess.run finish as soon as pg_ctl itself exits.
        environment = {key: value for key, value in os.environ.items()
                       if not key.upper().startswith("PG")}
        command = [str(binary), *map(str, arguments)]
        # Reuse the exclusively created handle, not a path that could later be
        # replaced by a symlink. PostgreSQL subprocesses write to this same file.
        return subprocess.run(command, stdout=self._command_log, stderr=self._command_log, env=environment,
                              timeout=45, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def write_evidence_json(self, name: str, value: dict) -> None:
        if name not in {"cleanup.json", "result.json"}:
            raise ValueError("Only the owned lifecycle reports can be written here")
        self._assert_identity(self.target)
        # Exclusive creation rejects every existing file, including hard links
        # and symlinks; no unrelated evidence is silently overwritten.
        with (self.target / name).open("x", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")

    def open_test_log(self):
        self._assert_identity(self.target)
        return (self.target / "tests.log").open("x", encoding="utf-8")

    def start(self) -> None:
        self._assert_owned()
        if self._start_attempted or self.cleanup_result is not None:
            raise RuntimeError("A disposable PostgreSQL runtime can only start once")
        info = self._postgres_log.lstat()
        if (_is_link(self._postgres_log)
                or (info.st_dev, info.st_ino) != self._postgres_log_identity):
            raise ValueError("Owned PostgreSQL log identity changed")
        self._invoke(self.initdb, ["-D", self.cluster, "-U", "rehearsal_admin", "--auth=trust",
                                  "--encoding=UTF8", "--no-locale"]).check_returncode()
        self._start_attempted = True
        self._invoke(self.pg_ctl, ["-D", self.cluster, "-l", self.target / "postgres.log",
                                   "-o", f"-h 127.0.0.1 -p {self.port}", "-w", "-t", "30", "start"]).check_returncode()

    def _assert_pid_identity(self) -> None:
        pid_file = self.cluster / "postmaster.pid"
        if _is_link(pid_file):
            raise ValueError("PostgreSQL PID file must not be a link")
        lines = pid_file.read_text(encoding="utf-8").splitlines()
        if (len(lines) < 6 or Path(lines[1]).resolve() != self.cluster
                or lines[3] != str(self.port) or lines[5] != "127.0.0.1"):
            raise ValueError("PostgreSQL process identity differs from the owned loopback cluster")

    def _status(self) -> int:
        return self._invoke(self.pg_ctl, ["-D", self.cluster, "status"]).returncode

    @staticmethod
    def _assert_no_links(path: Path) -> None:
        for parent, directories, files in os.walk(path, followlinks=False):
            for name in directories + files:
                if _is_link(Path(parent) / name):
                    raise ValueError("Disposable runtime contains a link; preserve it for inspection")

    def cleanup(self) -> dict:
        if self.cleanup_result is not None:
            return self.cleanup_result
        result = {"status": "failed", "cluster_stopped": False, "cluster_removed": False,
                  "runtime_removed": False, "keep_runtime": self.keep_runtime, "errors": []}
        try:
            self._assert_owned()
            pid_file = self.cluster / "postmaster.pid"
            if self._start_attempted or pid_file.exists():
                status = self._status()
                if status == 0:
                    self._assert_pid_identity()
                    self._invoke(self.pg_ctl, ["-D", self.cluster, "-m", "fast", "-w", "-t", "30",
                                               "stop"]).check_returncode()
                    status = self._status()
                if status != 3 or pid_file.exists():
                    raise RuntimeError("Cannot verify the owned PostgreSQL server is stopped")
            result["cluster_stopped"] = True
            if self.keep_runtime:
                result["status"] = "retained"
            else:
                # Validate both trees before deleting either. Evidence and dumps
                # are siblings and are deliberately outside this deletion set.
                self._assert_owned()
                for path in (self.cluster, self.runtime):
                    self._assert_no_links(path)
                for path, field in ((self.cluster, "cluster_removed"), (self.runtime, "runtime_removed")):
                    self._assert_identity(path)
                    shutil.rmtree(path)
                    result[field] = True
                result["status"] = "removed"
        except Exception as exc:
            result["errors"].append(f"{type(exc).__name__}: {exc}")
        finally:
            self._command_log.close()
        self.cleanup_result = result
        try:
            self.write_evidence_json("cleanup.json", result)
        except Exception as exc:
            result["status"] = "failed"
            result["errors"].append(f"Cannot write cleanup evidence: {type(exc).__name__}: {exc}")
        return result
