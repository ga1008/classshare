"""Safe LibreOffice conversion helpers.

LibreOffice headless conversion is sensitive to the selected executable,
current working directory, and user profile URI on Windows. Keep that handling
centralized so document preview/export paths do not each reinvent it.
"""

from __future__ import annotations

import os
import json
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import psutil

from ..storage_paths import DATA_ROOT


class LibreOfficeUnavailable(RuntimeError):
    """Raised when no usable LibreOffice command can be found."""


class LibreOfficeConversionError(RuntimeError):
    """Raised when LibreOffice exits without producing the requested output."""


class LibreOfficeBusy(RuntimeError):
    """All shared conversion slots are occupied; callers should retry later."""

    retry_after = 10

    def __init__(self):
        super().__init__("文档转换处理中，请稍后重试。")


def _conversion_capacity() -> int:
    try:
        return max(1, min(4, int(os.getenv("LANSHARE_LIBREOFFICE_MAX_CONCURRENCY", "1"))))
    except (TypeError, ValueError):
        return 1


def _owned_processes(metadata):
    """Find only this conversion's unique profile, detecting PID reuse safely."""
    marker = metadata["profile_arg"]
    recorded = {int(item["pid"]):float(item["create_time"]) for item in metadata.get("processes", [])}
    found, uncertain = [], False
    names = {str(metadata.get("executable_name", "")).lower(),"soffice","soffice.bin","soffice.exe","soffice.com","oosplash"}
    # Query expensive command lines only for Office executables or recorded
    # PIDs. Reading every system process's command line is slow on Windows.
    for process in psutil.process_iter(["pid","name"],ad_value=None):
        info = process.info
        if info["pid"] not in recorded and str(info.get("name") or "").lower() not in names:
            continue
        try:
            if process.status() in {psutil.STATUS_ZOMBIE,psutil.STATUS_DEAD}:
                continue
            created = process.create_time()
            command = process.cmdline()
        except (psutil.NoSuchProcess,psutil.ZombieProcess):
            continue
        except psutil.AccessDenied:
            uncertain = True
            continue
        if marker not in command:
            continue
        if info["pid"] not in recorded or recorded[info["pid"]] == float(created):
            found.append({"pid":info["pid"],"create_time":float(created)})
        else:
            uncertain = True
    return found, uncertain


def _kill_verified_processes(metadata, processes):
    for item in processes:
        try:
            process = psutil.Process(item["pid"])
            if process.create_time() == item["create_time"] and metadata["profile_arg"] in process.cmdline():
                process.kill()
        except (psutil.NoSuchProcess,psutil.ZombieProcess):
            pass
        except psutil.AccessDenied:
            # Keep the reservation; an unverified process must never be killed.
            pass


class _ConversionLease:
    def __init__(self, path):
        self.path = path
        self.metadata = None

    def recover(self):
        if not self.path.exists():
            return True
        try:
            previous = json.loads(self.path.read_text(encoding="utf-8"))
            if not str(previous["profile_arg"]).startswith("-env:UserInstallation=file:"):
                return False
            owned, uncertain = _owned_processes(previous)
            if owned or uncertain:
                if owned and time.monotonic()>=float(previous["deadline_monotonic"]):
                    _kill_verified_processes(previous,owned)
                # No waiting while holding a caller's DB connection. A retry
                # observes termination before it may start another converter.
                return False
            self.path.unlink(missing_ok=True)
            temporary_root = Path(str(previous.get("temporary_root") or ""))
            if (temporary_root.name.startswith("lanshare-lo-") and not temporary_root.is_symlink()
                    and temporary_root.resolve().parent == Path(tempfile.gettempdir()).resolve()):
                shutil.rmtree(temporary_root,ignore_errors=True)
            return True
        except (OSError,ValueError,KeyError,TypeError):
            return False

    def _write(self):
        temporary = self.path.with_name(self.path.name+f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(self.metadata),encoding="utf-8")
        os.replace(temporary,self.path)

    def prepare(self, command, timeout, temporary_root):
        self.metadata = {"profile_arg":command[1],"executable_name":Path(command[0]).name,
                         "started_at":time.time(),"deadline_monotonic":time.monotonic()+max(0,float(timeout)),"processes":[],"temporary_root":str(temporary_root)}
        # Commit the unique marker before Popen: even a worker killed between
        # starting the child and recording its PID cannot free this capacity.
        self._write()

    def attach(self, pid):
        try:
            process = psutil.Process(pid)
            self.metadata["processes"] = [{"pid":pid,"create_time":process.create_time()}]
        except psutil.NoSuchProcess:
            return
        self._write()

    def release_if_finished(self):
        if self.metadata is None:
            return
        try:
            owned, uncertain = _owned_processes(self.metadata)
            if not owned and not uncertain:
                self.path.unlink(missing_ok=True)
        except (OSError,ValueError,psutil.Error):
            # Fail closed: recovery will validate the persistent reservation.
            pass


def _unlock_slot(handle):
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _conversion_slot():
    """Nonblocking OS locks shared by all callers/processes using DATA_ROOT.

    Locks are never unlinked: replacing their inode would allow two owners.
    This budget is distinct from the document renderer's PDF/image budget, so
    nested use of that renderer cannot attempt to acquire the same lock twice.
    """
    lock_root = Path(DATA_ROOT) / "tmp" / "libreoffice" / "_locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    handle = None
    lease = None
    for index in range(_conversion_capacity()):
        candidate = (lock_root / f"slot-{index}.lock").open("a+b")
        try:
            if candidate.seek(0, os.SEEK_END) == 0:
                candidate.write(b"0")
                candidate.flush()
            candidate.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(candidate.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(candidate.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            candidate.close()
            continue
        current_lease = _ConversionLease(lock_root / f"slot-{index}.json")
        if not current_lease.recover():
            _unlock_slot(candidate)
            candidate.close()
            continue
        handle = candidate
        lease = current_lease
        break
    if handle is None:
        raise LibreOfficeBusy()
    try:
        yield lease
    finally:
        try:
            lease.release_if_finished()
            _unlock_slot(handle)
        finally:
            handle.close()


@dataclass(frozen=True)
class LibreOfficeConversion:
    output_bytes: bytes
    output_name: str
    stdout: str
    stderr: str
    command: tuple[str, ...]


def resolve_soffice_command() -> str | None:
    """Resolve LibreOffice, preferring the console launcher on Windows."""

    candidates = (
        ("soffice.com", "soffice.exe", "soffice", "libreoffice")
        if os.name == "nt"
        else ("soffice", "libreoffice")
    )
    for candidate in candidates:
        path = shutil.which(candidate)
        if path:
            return path
    return None


@lru_cache(maxsize=1)
def soffice_is_runnable() -> bool:
    """Check that the resolved LibreOffice can actually start.

    A resolved binary can still fail to launch (e.g. a broken Windows install
    exits with STATUS_DLL_INIT_FAILED before parsing arguments), so probe with
    ``--version`` once and cache the result for the process lifetime.
    """

    soffice = resolve_soffice_command()
    if not soffice:
        return False
    try:
        completed = subprocess.run(
            [soffice, "--headless", "--version"],
            check=False,
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def user_installation_arg(profile_dir: Path) -> str:
    """Build a valid LibreOffice UserInstallation argument for this OS."""

    return f"-env:UserInstallation={profile_dir.resolve().as_uri()}"


def _build_env(profile_dir: Path, temp_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    # Stale bootstrap-related variables can override the installed bootstrap.ini.
    env.pop("URE_BOOTSTRAP", None)
    env.pop("UNO_PATH", None)
    env["HOME"] = str(profile_dir)
    env["XDG_CONFIG_HOME"] = str(profile_dir / "xdg_config")
    env["XDG_CACHE_HOME"] = str(profile_dir / "xdg_cache")
    env["TMP"] = str(temp_dir)
    env["TEMP"] = str(temp_dir)
    (profile_dir / "xdg_config").mkdir(parents=True, exist_ok=True)
    (profile_dir / "xdg_cache").mkdir(parents=True, exist_ok=True)
    return env


def _target_suffix(output_format: str) -> str:
    first = str(output_format or "").split(":", 1)[0].strip().lower().lstrip(".")
    return f".{first or 'pdf'}"


def _short_process_error(stdout: str, stderr: str) -> str:
    text = (stderr or stdout or "").strip()
    return text[:300] if text else "LibreOffice did not report an error"


def _run_conversion(command, *, timeout, cwd, env, lease):
    lease.prepare(command,timeout,Path(cwd).parent)
    process = subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,cwd=cwd,env=env)
    try:
        lease.attach(process.pid)
        stdout, stderr = process.communicate(timeout=timeout)
    except BaseException:
        owned, _ = _owned_processes(lease.metadata)
        _kill_verified_processes(lease.metadata,owned)
        if process.poll() is None:
            process.kill()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.stdout.close()
            process.stderr.close()
        raise
    return subprocess.CompletedProcess(command,process.returncode,stdout,stderr)


def convert_office_file(input_path: Path, output_format: str, *, timeout: int = 90) -> LibreOfficeConversion:
    """Convert an Office file with an isolated profile and ASCII temp filename."""

    source_path = Path(input_path)
    if not source_path.exists():
        raise FileNotFoundError(str(source_path))

    soffice = resolve_soffice_command()
    if not soffice:
        raise LibreOfficeUnavailable("当前服务器未安装 LibreOffice，无法执行 Office 转换。")

    target_suffix = _target_suffix(output_format)
    with _conversion_slot() as lease, tempfile.TemporaryDirectory(prefix="lanshare-lo-") as temp_root:
        root = Path(temp_root)
        work_dir = root / "work"
        out_dir = root / "out"
        profile_dir = root / "profile"
        work_dir.mkdir(parents=True, exist_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        profile_dir.mkdir(parents=True, exist_ok=True)

        # Keep LibreOffice away from Chinese/Nutstore/original upload paths.
        temp_source = work_dir / f"input{source_path.suffix or '.bin'}"
        shutil.copy2(source_path, temp_source)

        command = [
            soffice,
            user_installation_arg(profile_dir),
            "--headless",
            "--invisible",
            "--nologo",
            "--nodefault",
            "--nofirststartwizard",
            "--norestore",
            "--nolockcheck",
            "--convert-to",
            output_format,
            "--outdir",
            str(out_dir),
            str(temp_source),
        ]
        completed = _run_conversion(
            command,
            timeout=timeout,
            cwd=str(work_dir),
            env=_build_env(profile_dir, work_dir),
            lease=lease,
        )

        candidates = sorted(path for path in out_dir.iterdir() if path.suffix.lower() == target_suffix)
        if completed.returncode != 0 or not candidates:
            raise LibreOfficeConversionError(
                "LibreOffice 转换失败："
                + _short_process_error(completed.stdout, completed.stderr)
            )

        output_path = candidates[0]
        output_bytes = output_path.read_bytes()
        if not output_bytes:
            raise LibreOfficeConversionError("LibreOffice 转换失败：输出文件为空。")

        return LibreOfficeConversion(
            output_bytes=output_bytes,
            output_name=output_path.name,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            command=tuple(command),
        )


def convert_docx_bytes_to_pdf(docx_content: bytes, *, timeout: int = 90) -> bytes:
    """Convert DOCX bytes to PDF bytes through the safe LibreOffice path."""

    with tempfile.TemporaryDirectory(prefix="lanshare-docx-pdf-") as temp_root:
        input_path = Path(temp_root) / "input.docx"
        input_path.write_bytes(docx_content)
        return convert_office_file(input_path, "pdf", timeout=timeout).output_bytes
