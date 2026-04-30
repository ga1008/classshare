#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bidisync.py - Windows 本地项目 <-> Ubuntu 远程服务器项目 双向增量同步器

适用场景：
- 本地：Windows 11，脚本放在本地项目根目录运行。
- 远程：Ubuntu，通过 SSH root + 密码访问。
- 项目：Python + Flask + SQLite + HTML 等文件型项目。
- 策略：文件级“双向增量同步”，默认 last-writer-wins；谁的内容变化更新，就以谁为准。

核心能力：
1. 本地和远程并发扫描，计算 SHA-256、mtime、size、路径。
2. 使用上次同步清单判断：新增、修改、删除、双方冲突。
3. 增量传输：只上传/下载/删除/对齐真正变化的文件。
4. 传输前显示计划列表；支持 --dry-run 和 --yes。
5. 多线程传输，每个线程独立 SSH/SFTP 连接。
6. 失败重试、日志记录、失败清单记录。
7. 传输后哈希校验，并尽量对齐 mtime，避免反复同步。
8. 删除默认进入 .sync_trash，不直接永久删除。
9. 支持远程 Docker Compose 自动编排：数据库上传前先 down，同步后 up -d --build；非数据库上传则同步成功后再重建启动。
10. 支持 Flask Web 控制台：双栏目录差异视图、方向箭头、勾选式选择同步、Web 输入 SSH 密码和进度条。
11. 对 SQLite 给出明确保护：默认按文件同步，建议同步前停服务；可按需开启快照，但文件级镜像不推荐。

依赖安装：
    pip install flask paramiko tqdm

首次使用：
    1. 修改脚本顶部 CONFIG 区域：REMOTE_HOST、REMOTE_PROJECT_ROOT。
    2. 把本脚本放到本地项目根目录。
    3. 命令行试运行：python bidisync.py --dry-run
    4. 命令行同步：python bidisync.py
    5. Web 控制台：python bidisync.py --web，然后打开 http://127.0.0.1:8765

重要提醒：
- 这是“文件级同步器”，不是数据库行级合并器。如果本地和远程 SQLite 在两边同时被用户修改，
  本脚本只能按文件整体的 mtime/哈希决策，无法自动合并两边数据库中的不同业务记录。
- 如果启用 Docker Compose 自动编排，脚本会在“上传数据库到服务器”前自动停止远程容器，
  同步完成后重新执行 docker compose up -d --build。
- 如果只上传代码/模板/静态资源等非数据库文件，脚本会先完成同步，再重建并后台启动容器，
  尽量缩短服务中断时间。
- Web 控制台的 SSH 密码由用户在页面输入，后端只在当前请求/同步线程内存中使用，不写入配置、日志或同步清单。
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import dataclasses
import fnmatch
import getpass
import hashlib
import io
import json
import logging
import os
import posixpath
import shutil
import socket
import stat as statmod
import sys
import tempfile
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

try:
    import paramiko
except ImportError as exc:  # pragma: no cover
    print("缺少依赖 paramiko，请先执行：pip install flask paramiko tqdm", file=sys.stderr)
    raise

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover
    tqdm = None


# =========================
# CONFIG：按你的服务器修改
# =========================
REMOTE_HOST = "192.168.1.100"              # 远程服务器 IP，示例："123.123.123.123"
REMOTE_PORT = 22
REMOTE_USER = "root"
REMOTE_PROJECT_ROOT = "/root/your_flask_project"  # 远程项目绝对路径

# 脚本所在目录即本地项目根目录
LOCAL_PROJECT_ROOT = Path(__file__).resolve().parent

# 并发参数：过大可能触发服务器 SSH 限制；可通过 --workers 覆盖
SCAN_WORKERS = min(32, (os.cpu_count() or 4) * 4)
TRANSFER_WORKERS = 6
REMOTE_SCAN_WORKERS = min(16, (os.cpu_count() or 4) * 2)

# 传输重试
RETRY_TIMES = 3
RETRY_BASE_SLEEP = 1.5
CONNECT_TIMEOUT = 20
SSH_BANNER_TIMEOUT = 35
SSH_AUTH_TIMEOUT = 20
COMMAND_TIMEOUT = 60 * 60

# 哈希与进度参数
HASH_BLOCK_SIZE = 4 * 1024 * 1024
MTIME_TOLERANCE_SECONDS = 2.0

# 删除策略："trash" 安全移动到 .sync_trash；"delete" 直接删除
DELETE_MODE = "trash"

# 冲突同秒策略：双方哈希不同，mtime 极接近时默认跳过，避免误覆盖
# 可选："skip" / "local" / "remote"
TIE_BREAKER = "skip"

# SQLite 文件扩展名。默认不启用 sqlite backup 快照，因为 backup 生成的文件通常不是 bitwise identical，
# 会破坏“文件镜像 + 哈希对齐”的语义。强烈建议同步前停服务或启用维护模式。
SQLITE_EXTENSIONS = {".db", ".sqlite", ".sqlite3"}
USE_SQLITE_BACKUP_SNAPSHOT = False

# SQLite side-car 文件；SQLite WAL 模式下可能同时存在，需要和主库一起处理。
SQLITE_SIDE_CAR_SUFFIXES = (
    ".db-wal", ".db-shm",
    ".sqlite-wal", ".sqlite-shm",
    ".sqlite3-wal", ".sqlite3-shm",
)

# Docker Compose 自动编排。
# - 上传 SQLite 数据库或 WAL/SHM 到服务器：先 down，再同步，再 up -d --build。
# - 只上传代码/模板/静态资源等非数据库文件：先同步，再 down + up -d --build。
# - 只下载远程变化到本地：不重启远程服务。
DOCKER_COMPOSE_ENABLED = True
DOCKER_COMPOSE_PROJECT_DIR = REMOTE_PROJECT_ROOT
DOCKER_COMPOSE_DOWN_COMMAND = "docker compose down"
DOCKER_COMPOSE_UP_COMMAND = "docker compose up -d --build"
DOCKER_COMPOSE_TIMEOUT = 60 * 20

# Web 控制台默认监听本机。生产环境建议仅在可信局域网使用，或通过 SSH 隧道访问。
WEB_HOST = "127.0.0.1"
WEB_PORT = 8765

# 同步器自身目录，必须排除，避免清单/日志被同步或引发递归
SYNC_DIR_NAME = ".sync_state"
CONFLICT_DIR_NAME = ".sync_conflicts"
TRASH_DIR_NAME = ".sync_trash"

# 默认排除项：你可以按需删减。注意：不排除 .db/.sqlite/.wal/.shm。
EXCLUDE_DIR_NAMES = {
    SYNC_DIR_NAME,
    CONFLICT_DIR_NAME,
    TRASH_DIR_NAME,
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "env",
}
EXCLUDE_FILE_GLOBS = {
    "*.pyc",
    "*.pyo",
    "*.tmp",
    "~$*",
    ".DS_Store",
    "Thumbs.db",
}

MANIFEST_NAME = "sync_manifest.json"
LOG_DIR_NAME = "logs"
FAILURES_NAME = "sync_failures.jsonl"


# =========================
# 数据结构
# =========================

@dataclass(frozen=True)
class FileMeta:
    rel: str
    size: int
    mtime_ns: int
    sha256: str
    mode: int = 0

    @property
    def mtime_s(self) -> float:
        return self.mtime_ns / 1_000_000_000

    def to_json(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @staticmethod
    def from_json(data: Dict[str, Any]) -> "FileMeta":
        return FileMeta(
            rel=str(data["rel"]),
            size=int(data["size"]),
            mtime_ns=int(data["mtime_ns"]),
            sha256=str(data["sha256"]),
            mode=int(data.get("mode", 0)),
        )


@dataclass
class Snapshot:
    files: Dict[str, FileMeta]
    dirs: Set[str]


@dataclass
class Manifest:
    version: int
    saved_at: str
    files: Dict[str, FileMeta]
    dirs: Set[str]

    @staticmethod
    def empty() -> "Manifest":
        return Manifest(version=1, saved_at="", files={}, dirs=set())

    def to_json(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "saved_at": self.saved_at,
            "files": {rel: meta.to_json() for rel, meta in sorted(self.files.items())},
            "dirs": sorted(self.dirs),
        }

    @staticmethod
    def from_json(data: Dict[str, Any]) -> "Manifest":
        return Manifest(
            version=int(data.get("version", 1)),
            saved_at=str(data.get("saved_at", "")),
            files={rel: FileMeta.from_json(meta) for rel, meta in data.get("files", {}).items()},
            dirs=set(data.get("dirs", [])),
        )


@dataclass
class Action:
    kind: str                  # upload/download/delete_local/delete_remote/align_mtime_*/mkdir_*/rmdir_*/conflict_skip
    rel: str
    reason: str
    local: Optional[FileMeta] = None
    remote: Optional[FileMeta] = None
    baseline: Optional[FileMeta] = None
    backup_loser: bool = False

    @property
    def bytes(self) -> int:
        if self.kind == "upload" and self.local:
            return self.local.size
        if self.kind == "download" and self.remote:
            return self.remote.size
        return 0


@dataclass
class SyncConfig:
    remote_host: str = REMOTE_HOST
    remote_port: int = REMOTE_PORT
    remote_user: str = REMOTE_USER
    remote_root: str = REMOTE_PROJECT_ROOT
    local_root: Path = LOCAL_PROJECT_ROOT
    scan_workers: int = SCAN_WORKERS
    transfer_workers: int = TRANSFER_WORKERS
    remote_scan_workers: int = REMOTE_SCAN_WORKERS
    dry_run: bool = False
    yes: bool = False
    no_delete: bool = False
    only: str = "all"  # all/upload/download
    docker_enabled: bool = DOCKER_COMPOSE_ENABLED
    docker_project_dir: str = DOCKER_COMPOSE_PROJECT_DIR
    docker_down_cmd: str = DOCKER_COMPOSE_DOWN_COMMAND
    docker_up_cmd: str = DOCKER_COMPOSE_UP_COMMAND
    docker_timeout: int = DOCKER_COMPOSE_TIMEOUT
    web: bool = False
    web_host: str = WEB_HOST
    web_port: int = WEB_PORT


# =========================
# 基础工具
# =========================

def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_relpath(path: Path, root: Path) -> str:
    rel = path.relative_to(root).as_posix()
    if rel in {".", ""} or rel.startswith("../") or rel.startswith("/"):
        raise ValueError(f"非法相对路径：{rel}")
    return rel


def local_path(root: Path, rel: str) -> Path:
    # 只接受 POSIX 风格相对路径，禁止逃逸根目录
    p = root.joinpath(*PurePosixPath(rel).parts)
    resolved_root = root.resolve()
    try:
        resolved = p.resolve(strict=False)
    except RuntimeError:
        raise ValueError(f"非法本地路径：{rel}")
    if os.name == "nt":
        if not str(resolved).lower().startswith(str(resolved_root).lower()):
            raise ValueError(f"路径逃逸本地根目录：{rel}")
    else:
        if not str(resolved).startswith(str(resolved_root)):
            raise ValueError(f"路径逃逸本地根目录：{rel}")
    return p


def remote_path(remote_root: str, rel: str) -> str:
    pp = PurePosixPath(rel)
    if pp.is_absolute() or ".." in pp.parts:
        raise ValueError(f"路径逃逸远程根目录：{rel}")
    return posixpath.join(remote_root.rstrip("/"), pp.as_posix())


def should_exclude_rel(rel: str, is_dir: bool) -> bool:
    parts = PurePosixPath(rel).parts
    if any(part in EXCLUDE_DIR_NAMES for part in parts):
        return True
    name = parts[-1] if parts else rel
    if not is_dir:
        return any(fnmatch.fnmatch(name, pat) for pat in EXCLUDE_FILE_GLOBS)
    return False


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(HASH_BLOCK_SIZE)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def is_sqlite_path(path_str: str) -> bool:
    lower = path_str.lower()
    if PurePosixPath(lower).suffix in SQLITE_EXTENSIONS:
        return True
    return any(lower.endswith(suffix) for suffix in SQLITE_SIDE_CAR_SUFFIXES)


def mtime_diff_seconds(a_ns: int, b_ns: int) -> float:
    return abs(a_ns - b_ns) / 1_000_000_000


def retryable(fn: Callable[..., Any]) -> Callable[..., Any]:
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        last_exc = None
        for attempt in range(1, RETRY_TIMES + 1):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt >= RETRY_TIMES:
                    break
                sleep = RETRY_BASE_SLEEP * (2 ** (attempt - 1))
                logging.warning("操作失败，%.1fs 后重试 %s/%s：%s", sleep, attempt, RETRY_TIMES, exc)
                time.sleep(sleep)
        assert last_exc is not None
        raise last_exc
    return wrapper


class SimpleProgress:
    """tqdm 不存在时的极简进度输出。"""

    def __init__(self, total: int, desc: str):
        self.total = total
        self.desc = desc
        self.n = 0
        self._last = 0.0
        print(f"{desc}: 0/{total}")

    def update(self, delta: int) -> None:
        self.n += delta
        now = time.time()
        if now - self._last > 1 or self.n >= self.total:
            pct = (self.n / self.total * 100) if self.total else 100
            print(f"\r{self.desc}: {self.n}/{self.total} ({pct:.1f}%)", end="", flush=True)
            self._last = now

    def close(self) -> None:
        print()

    def __enter__(self) -> "SimpleProgress":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def make_progress(total: int, desc: str, unit: str = "B"):
    if tqdm:
        return tqdm(total=total, desc=desc, unit=unit, unit_scale=(unit == "B"), unit_divisor=1024)
    return SimpleProgress(total=total, desc=desc)


# =========================
# 日志和清单
# =========================

def ensure_state_dirs(local_root: Path) -> Tuple[Path, Path, Path]:
    state = local_root / SYNC_DIR_NAME
    logs = state / LOG_DIR_NAME
    state.mkdir(exist_ok=True)
    logs.mkdir(exist_ok=True)
    return state, logs, state / FAILURES_NAME


def setup_logging(local_root: Path) -> Path:
    _, logs, _ = ensure_state_dirs(local_root)
    log_file = logs / f"sync_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(threadName)s %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
    )
    return log_file


def read_local_manifest(local_root: Path) -> Optional[Manifest]:
    path = local_root / SYNC_DIR_NAME / MANIFEST_NAME
    if not path.exists():
        return None
    try:
        return Manifest.from_json(json.loads(path.read_text(encoding="utf-8")))
    except Exception as exc:  # noqa: BLE001
        logging.warning("本地清单读取失败，将尝试使用远程清单或空清单：%s", exc)
        return None


def write_local_manifest(local_root: Path, manifest: Manifest) -> None:
    state = local_root / SYNC_DIR_NAME
    state.mkdir(exist_ok=True)
    path = state / MANIFEST_NAME
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def record_failure(local_root: Path, action: Action, exc: BaseException) -> None:
    _, _, failures = ensure_state_dirs(local_root)
    row = {
        "time": now_utc_iso(),
        "action": dataclasses.asdict(action),
        "error": repr(exc),
        "traceback": traceback.format_exc(),
    }
    with failures.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


# =========================
# SSH / SFTP 客户端
# =========================

class SSHConnectionError(RuntimeError):
    """带中文诊断信息的 SSH 连接错误。"""


def probe_ssh_banner(cfg: SyncConfig, timeout: float = 5.0) -> Dict[str, Any]:
    """
    只做 TCP + SSH banner 预检，不验证密码。
    SSH 服务正常时，连接建立后服务端会先发类似：SSH-2.0-OpenSSH_xxx。
    """
    result: Dict[str, Any] = {
        "host": cfg.remote_host,
        "port": cfg.remote_port,
        "tcp_ok": False,
        "ssh_banner_ok": False,
        "banner": "",
        "error": "",
    }
    sock: Optional[socket.socket] = None
    try:
        sock = socket.create_connection((cfg.remote_host, cfg.remote_port), timeout=timeout)
        result["tcp_ok"] = True
        sock.settimeout(timeout)
        data = sock.recv(256)
        banner = data.decode("utf-8", errors="replace").strip()
        result["banner"] = banner
        result["ssh_banner_ok"] = banner.startswith("SSH-")
        if not result["ssh_banner_ok"]:
            result["error"] = "端口可连接，但返回内容不是 SSH 协议横幅。"
    except socket.timeout:
        result["error"] = "TCP 可连接但等待 SSH 协议横幅超时。" if result["tcp_ok"] else "连接服务器超时。"
    except OSError as exc:
        result["error"] = f"TCP 连接失败：{exc}"
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"SSH 预检失败：{exc}"
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
    return result


def format_ssh_exception(cfg: SyncConfig, exc: BaseException) -> str:
    raw = str(exc) or exc.__class__.__name__
    lower = raw.lower()
    prefix = f"无法连接 SSH：{cfg.remote_user}@{cfg.remote_host}:{cfg.remote_port}。"

    if isinstance(exc, paramiko.AuthenticationException):
        return prefix + "认证失败，请检查用户名、密码，以及服务器是否允许该用户使用密码登录。"
    if isinstance(exc, paramiko.BadHostKeyException):
        return prefix + "服务器主机密钥校验失败。"
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return prefix + "连接或握手超时，请检查 IP、端口、防火墙和服务器负载。"
    if isinstance(exc, OSError):
        return prefix + f"TCP 连接失败：{raw}"

    if "error reading ssh protocol banner" in lower or "banner" in lower or "eof" in lower:
        probe = probe_ssh_banner(cfg, timeout=5.0)
        tips = [
            prefix + "服务器在 SSH 握手阶段没有返回有效的 SSH 协议横幅。",
            f"预检结果：TCP={'正常' if probe.get('tcp_ok') else '失败'}；SSH横幅={'正常' if probe.get('ssh_banner_ok') else '异常'}。",
        ]
        if probe.get("banner"):
            shown = str(probe["banner"]).replace("\n", " ")[:160]
            tips.append(f"该端口返回：{shown!r}。这通常说明端口填成了网站/应用/Docker 服务端口，而不是 SSH 端口。")
        if probe.get("error"):
            tips.append(f"预检说明：{probe['error']}")
        tips.append("请优先检查：远程 IP 是否正确、端口是否为 SSH 端口（通常是 22）、云服务器安全组/防火墙是否放行、sshd 是否运行、root 是否允许密码登录。")
        return "\n".join(tips)

    return prefix + raw


def test_ssh_connection(cfg: SyncConfig, password: str) -> Dict[str, Any]:
    """Web 控制台使用的连接测试：先读 SSH banner，再执行一个极轻量命令。"""
    probe = probe_ssh_banner(cfg, timeout=5.0)
    if not probe.get("tcp_ok") or not probe.get("ssh_banner_ok"):
        return {"ok": False, "stage": "banner", **probe}
    ctx: Optional[SSHContext] = None
    try:
        ctx = SSHContext(cfg, password)
        code, out, err = ctx.exec("printf __BIDISYNC_SSH_OK__", timeout=15)
        ok = code == 0 and "__BIDISYNC_SSH_OK__" in out
        return {"ok": ok, "stage": "auth_exec", "banner": probe.get("banner", ""), "code": code, "stdout": out.strip(), "stderr": err.strip()}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "stage": "auth_exec", "banner": probe.get("banner", ""), "error": str(exc)}
    finally:
        if ctx is not None:
            ctx.close()


class SSHContext:
    def __init__(self, cfg: SyncConfig, password: str):
        self.cfg = cfg
        self.password = password
        self.client = self._connect()

    def _connect(self) -> "paramiko.SSHClient":
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=self.cfg.remote_host,
                port=self.cfg.remote_port,
                username=self.cfg.remote_user,
                password=self.password,
                timeout=CONNECT_TIMEOUT,
                banner_timeout=SSH_BANNER_TIMEOUT,
                auth_timeout=SSH_AUTH_TIMEOUT,
                look_for_keys=False,
                allow_agent=False,
                compress=True,
            )
            transport = client.get_transport()
            if transport is not None:
                transport.set_keepalive(30)
            return client
        except Exception as exc:  # noqa: BLE001
            try:
                client.close()
            finally:
                raise SSHConnectionError(format_ssh_exception(self.cfg, exc)) from exc

    def sftp(self) -> "paramiko.SFTPClient":
        return self.client.open_sftp()

    def close(self) -> None:
        self.client.close()

    def exec(self, command: str, timeout: int = COMMAND_TIMEOUT) -> Tuple[int, str, str]:
        stdin, stdout, stderr = self.client.exec_command(command, timeout=timeout)
        stdin.close()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        code = stdout.channel.recv_exit_status()
        return code, out, err


class ThreadLocalRemote:
    def __init__(self, cfg: SyncConfig, password: str):
        self.cfg = cfg
        self.password = password
        self.local = threading.local()

    def get(self) -> SSHContext:
        ctx = getattr(self.local, "ctx", None)
        if ctx is None:
            ctx = SSHContext(self.cfg, self.password)
            self.local.ctx = ctx
        return ctx


def remote_quote(s: str) -> str:
    import shlex
    return shlex.quote(s)


def sftp_mkdirs(sftp: "paramiko.SFTPClient", path: str) -> None:
    parts = PurePosixPath(path).parts
    if not parts:
        return
    cur = "/" if path.startswith("/") else ""
    for part in parts:
        if part == "/":
            continue
        cur = posixpath.join(cur, part) if cur else part
        try:
            sftp.stat(cur)
        except IOError:
            try:
                sftp.mkdir(cur)
            except IOError:
                try:
                    sftp.stat(cur)
                except IOError:
                    raise


# =========================
# 本地扫描
# =========================

def _local_hash_task(path_str: str, root_str: str) -> Optional[FileMeta]:
    path = Path(path_str)
    root = Path(root_str)
    try:
        if path.is_symlink():
            return None
        st = path.stat()
        if not path.is_file():
            return None
        rel = safe_relpath(path, root)
        if should_exclude_rel(rel, is_dir=False):
            return None
        return FileMeta(
            rel=rel,
            size=st.st_size,
            mtime_ns=st.st_mtime_ns,
            sha256=sha256_file(path),
            mode=statmod.S_IMODE(st.st_mode),
        )
    except Exception as exc:  # noqa: BLE001
        logging.error("扫描本地文件失败 %s: %s", path, exc)
        return None


def scan_local(root: Path, workers: int) -> Snapshot:
    logging.info("开始扫描本地：%s", root)
    file_paths: List[Path] = []
    dirs: Set[str] = set()

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dpath = Path(dirpath)
        keep_dirs = []
        for d in dirnames:
            child = dpath / d
            if child.is_symlink():
                continue
            rel = safe_relpath(child, root)
            if not should_exclude_rel(rel, is_dir=True):
                keep_dirs.append(d)
                dirs.add(rel)
        dirnames[:] = keep_dirs

        for name in filenames:
            path = dpath / name
            try:
                rel = safe_relpath(path, root)
            except ValueError:
                continue
            if should_exclude_rel(rel, is_dir=False):
                continue
            file_paths.append(path)

    files: Dict[str, FileMeta] = {}
    with make_progress(len(file_paths), "本地哈希扫描", unit="file") as pbar:
        with cf.ThreadPoolExecutor(max_workers=workers, thread_name_prefix="local-scan") as ex:
            futures = [ex.submit(_local_hash_task, str(p), str(root)) for p in file_paths]
            for fut in cf.as_completed(futures):
                meta = fut.result()
                if meta:
                    files[meta.rel] = meta
                pbar.update(1)

    collisions = detect_case_collisions(files.keys())
    if collisions:
        logging.warning("发现 Windows 大小写路径冲突，Linux 可能视为不同文件：%s", collisions[:10])

    logging.info("本地扫描完成：%d 文件，%d 目录", len(files), len(dirs))
    return Snapshot(files=files, dirs=dirs)


def detect_case_collisions(paths: Iterable[str]) -> List[List[str]]:
    buckets: Dict[str, List[str]] = {}
    for p in paths:
        buckets.setdefault(p.lower(), []).append(p)
    return [v for v in buckets.values() if len(v) > 1]


# =========================
# 远程辅助脚本
# =========================

REMOTE_SCANNER_PY = r'''
import concurrent.futures as cf
import fnmatch
import hashlib
import json
import os
import stat
import sys

ROOT = os.path.abspath(sys.argv[1])
SCAN_WORKERS = int(sys.argv[2])
HASH_BLOCK_SIZE = 4 * 1024 * 1024
EXCLUDE_DIR_NAMES = set(json.loads(sys.argv[3]))
EXCLUDE_FILE_GLOBS = set(json.loads(sys.argv[4]))


def norm_rel(path):
    rel = os.path.relpath(path, ROOT)
    if rel == '.':
        return ''
    return rel.replace(os.sep, '/')


def should_exclude_rel(rel, is_dir):
    parts = rel.split('/') if rel else []
    if any(part in EXCLUDE_DIR_NAMES for part in parts):
        return True
    name = parts[-1] if parts else rel
    if not is_dir:
        return any(fnmatch.fnmatch(name, pat) for pat in EXCLUDE_FILE_GLOBS)
    return False


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            block = f.read(HASH_BLOCK_SIZE)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def scan_file(rel):
    path = os.path.join(ROOT, *rel.split('/'))
    st_l = os.lstat(path)
    if stat.S_ISLNK(st_l.st_mode):
        return None
    st = os.stat(path)
    if not stat.S_ISREG(st.st_mode):
        return None
    return {
        'type': 'file',
        'rel': rel,
        'size': st.st_size,
        'mtime_ns': st.st_mtime_ns,
        'sha256': sha256_file(path),
        'mode': stat.S_IMODE(st.st_mode),
    }


def main():
    files = []
    dirs = []
    for dirpath, dirnames, filenames in os.walk(ROOT, followlinks=False):
        keep = []
        for d in dirnames:
            abs_d = os.path.join(dirpath, d)
            try:
                if stat.S_ISLNK(os.lstat(abs_d).st_mode):
                    continue
            except FileNotFoundError:
                continue
            rel = norm_rel(abs_d)
            if not should_exclude_rel(rel, True):
                keep.append(d)
                dirs.append(rel)
        dirnames[:] = keep
        for name in filenames:
            rel = norm_rel(os.path.join(dirpath, name))
            if not should_exclude_rel(rel, False):
                files.append(rel)

    for rel in dirs:
        print(json.dumps({'type': 'dir', 'rel': rel}, ensure_ascii=False), flush=False)

    with cf.ThreadPoolExecutor(max_workers=SCAN_WORKERS) as ex:
        for item in ex.map(scan_file, files):
            if item is not None:
                print(json.dumps(item, ensure_ascii=False), flush=False)

if __name__ == '__main__':
    main()
'''

REMOTE_HASH_ONE_PY = r'''
import hashlib, json, os, stat, sys
path = sys.argv[1]
block = 4 * 1024 * 1024
h = hashlib.sha256()
with open(path, 'rb') as f:
    while True:
        b = f.read(block)
        if not b:
            break
        h.update(b)
st = os.stat(path)
print(json.dumps({
    'size': st.st_size,
    'mtime_ns': st.st_mtime_ns,
    'sha256': h.hexdigest(),
    'mode': stat.S_IMODE(st.st_mode),
}, ensure_ascii=False))
'''

REMOTE_COPY_FILE_PY = r'''
import os, shutil, sys
src = sys.argv[1]
dst = sys.argv[2]
os.makedirs(os.path.dirname(dst), exist_ok=True)
shutil.copy2(src, dst)
print(dst)
'''

REMOTE_REPLACE_AND_UTIME_PY = r'''
import os, sys
src_tmp = sys.argv[1]
dst = sys.argv[2]
mtime_ns = int(sys.argv[3])
mode = int(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4] else 0
os.makedirs(os.path.dirname(dst), exist_ok=True)
os.replace(src_tmp, dst)
os.utime(dst, ns=(mtime_ns, mtime_ns))
if mode:
    try:
        os.chmod(dst, mode)
    except Exception:
        pass
print('ok')
'''

REMOTE_MOVE_TO_TRASH_PY = r'''
import os, shutil, sys
src = sys.argv[1]
trash_root = sys.argv[2]
rel = sys.argv[3]
mode = sys.argv[4]
if not os.path.exists(src):
    print('missing')
    sys.exit(0)
if mode == 'delete':
    os.remove(src)
    print('deleted')
else:
    dst = os.path.join(trash_root, *rel.split('/'))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    base = dst
    i = 1
    while os.path.exists(dst):
        dst = base + f'.{i}'
        i += 1
    shutil.move(src, dst)
    print(dst)
'''

REMOTE_RMDIR_IF_EMPTY_PY = r'''
import os, sys
path = sys.argv[1]
try:
    os.rmdir(path)
    print('removed')
except FileNotFoundError:
    print('missing')
except OSError:
    print('not_empty')
'''


def upload_remote_helper(ctx: SSHContext, name: str, content: str) -> str:
    remote_file = f"/tmp/bidisync_{os.getpid()}_{name}.py"
    with ctx.sftp() as sftp:
        with io.BytesIO(content.encode("utf-8")) as bio:
            sftp.putfo(bio, remote_file)
        sftp.chmod(remote_file, 0o700)
    return remote_file


def run_remote_python(ctx: SSHContext, code: str, args: Sequence[str], timeout: int = COMMAND_TIMEOUT) -> Tuple[int, str, str]:
    helper = upload_remote_helper(ctx, f"helper_{threading.get_ident()}", code)
    try:
        cmd = " ".join(["python3", remote_quote(helper), *[remote_quote(a) for a in args]])
        return ctx.exec(cmd, timeout=timeout)
    finally:
        try:
            ctx.exec(f"rm -f {remote_quote(helper)}", timeout=30)
        except Exception:
            pass


# =========================
# 远程扫描与清单读写
# =========================

def scan_remote(cfg: SyncConfig, password: str) -> Snapshot:
    logging.info("开始扫描远程：%s:%s", cfg.remote_host, cfg.remote_root)
    ctx = SSHContext(cfg, password)
    try:
        args = [
            cfg.remote_root,
            str(cfg.remote_scan_workers),
            json.dumps(sorted(EXCLUDE_DIR_NAMES)),
            json.dumps(sorted(EXCLUDE_FILE_GLOBS)),
        ]
        code, out, err = run_remote_python(ctx, REMOTE_SCANNER_PY, args, timeout=COMMAND_TIMEOUT)
        if code != 0:
            raise RuntimeError(f"远程扫描失败 code={code}, stderr={err[:2000]}")
        files: Dict[str, FileMeta] = {}
        dirs: Set[str] = set()
        for line in out.splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            if item.get("type") == "dir":
                rel = item["rel"]
                if rel and not should_exclude_rel(rel, True):
                    dirs.add(rel)
            elif item.get("type") == "file":
                meta = FileMeta(
                    rel=item["rel"],
                    size=int(item["size"]),
                    mtime_ns=int(item["mtime_ns"]),
                    sha256=item["sha256"],
                    mode=int(item.get("mode", 0)),
                )
                files[meta.rel] = meta
        logging.info("远程扫描完成：%d 文件，%d 目录", len(files), len(dirs))
        return Snapshot(files=files, dirs=dirs)
    finally:
        ctx.close()


def read_remote_manifest(cfg: SyncConfig, password: str) -> Optional[Manifest]:
    ctx = SSHContext(cfg, password)
    try:
        manifest_path = remote_path(cfg.remote_root, f"{SYNC_DIR_NAME}/{MANIFEST_NAME}")
        with ctx.sftp() as sftp:
            try:
                with sftp.open(manifest_path, "r") as f:
                    raw_obj = f.read()
                    raw = raw_obj.decode("utf-8") if isinstance(raw_obj, bytes) else raw_obj
            except Exception:
                return None
        return Manifest.from_json(json.loads(raw))
    except Exception as exc:  # noqa: BLE001
        logging.warning("远程清单读取失败：%s", exc)
        return None
    finally:
        ctx.close()


def write_remote_manifest(cfg: SyncConfig, password: str, manifest: Manifest) -> None:
    ctx = SSHContext(cfg, password)
    try:
        manifest_rel = f"{SYNC_DIR_NAME}/{MANIFEST_NAME}"
        manifest_path = remote_path(cfg.remote_root, manifest_rel)
        tmp_path = manifest_path + ".tmp"
        data = json.dumps(manifest.to_json(), ensure_ascii=False, indent=2).encode("utf-8")
        with ctx.sftp() as sftp:
            sftp_mkdirs(sftp, posixpath.dirname(manifest_path))
            with io.BytesIO(data) as bio:
                sftp.putfo(bio, tmp_path)
        code, out, err = run_remote_python(
            ctx,
            "import os, sys; os.replace(sys.argv[1], sys.argv[2]); print('ok')",
            [tmp_path, manifest_path],
            timeout=60,
        )
        if code != 0:
            raise RuntimeError(err)
    finally:
        ctx.close()


def choose_manifest(local_m: Optional[Manifest], remote_m: Optional[Manifest]) -> Manifest:
    if local_m and remote_m:
        return local_m if local_m.saved_at >= remote_m.saved_at else remote_m
    return local_m or remote_m or Manifest.empty()


# =========================
# 同步计划
# =========================

def changed_from_baseline(current: Optional[FileMeta], baseline: Optional[FileMeta]) -> bool:
    if current is None:
        return baseline is not None
    if baseline is None:
        return True
    return current.sha256 != baseline.sha256


def plan_sync(local: Snapshot, remote: Snapshot, baseline: Manifest, cfg: SyncConfig) -> List[Action]:
    actions: List[Action] = []
    all_rels = sorted(set(local.files) | set(remote.files) | set(baseline.files))

    for rel in all_rels:
        l = local.files.get(rel)
        r = remote.files.get(rel)
        b = baseline.files.get(rel)

        if l and r:
            if l.sha256 == r.sha256:
                if mtime_diff_seconds(l.mtime_ns, r.mtime_ns) > MTIME_TOLERANCE_SECONDS:
                    if l.mtime_ns >= r.mtime_ns:
                        actions.append(Action("align_mtime_remote", rel, "内容相同但远程 mtime 落后", l, r, b))
                    else:
                        actions.append(Action("align_mtime_local", rel, "内容相同但本地 mtime 落后", l, r, b))
                continue

            l_changed = changed_from_baseline(l, b)
            r_changed = changed_from_baseline(r, b)

            if l_changed and not r_changed:
                actions.append(Action("upload", rel, "本地相对上次同步发生变化", l, r, b, backup_loser=True))
            elif r_changed and not l_changed:
                actions.append(Action("download", rel, "远程相对上次同步发生变化", l, r, b, backup_loser=True))
            elif l_changed and r_changed:
                if mtime_diff_seconds(l.mtime_ns, r.mtime_ns) <= MTIME_TOLERANCE_SECONDS:
                    if TIE_BREAKER == "local":
                        actions.append(Action("upload", rel, "双方都变更且时间接近；按 TIE_BREAKER=local", l, r, b, backup_loser=True))
                    elif TIE_BREAKER == "remote":
                        actions.append(Action("download", rel, "双方都变更且时间接近；按 TIE_BREAKER=remote", l, r, b, backup_loser=True))
                    else:
                        actions.append(Action("conflict_skip", rel, "双方都变更且时间接近；跳过以避免误覆盖", l, r, b))
                elif l.mtime_ns > r.mtime_ns:
                    actions.append(Action("upload", rel, "双方都变更；本地 mtime 更新，保留本地", l, r, b, backup_loser=True))
                else:
                    actions.append(Action("download", rel, "双方都变更；远程 mtime 更新，保留远程", l, r, b, backup_loser=True))
            else:
                if l.mtime_ns >= r.mtime_ns:
                    actions.append(Action("upload", rel, "内容不同但无法判定；按较新 mtime 选择本地", l, r, b, backup_loser=True))
                else:
                    actions.append(Action("download", rel, "内容不同但无法判定；按较新 mtime 选择远程", l, r, b, backup_loser=True))

        elif l and not r:
            if b is None:
                actions.append(Action("upload", rel, "远程缺失，本地新增", l, r, b))
            else:
                l_changed = l.sha256 != b.sha256
                if l_changed:
                    actions.append(Action("upload", rel, "远程缺失，但本地已更新；恢复到远程", l, r, b))
                else:
                    actions.append(Action("delete_local", rel, "远程删除且本地未改；删除传播到本地", l, r, b))

        elif r and not l:
            if b is None:
                actions.append(Action("download", rel, "本地缺失，远程新增", l, r, b))
            else:
                r_changed = r.sha256 != b.sha256
                if r_changed:
                    actions.append(Action("download", rel, "本地缺失，但远程已更新；恢复到本地", l, r, b))
                else:
                    actions.append(Action("delete_remote", rel, "本地删除且远程未改；删除传播到远程", l, r, b))

    all_dirs = sorted(set(local.dirs) | set(remote.dirs) | set(baseline.dirs))
    local_files_under = build_dir_file_index(local.files.keys())
    remote_files_under = build_dir_file_index(remote.files.keys())

    for rel in all_dirs:
        in_l = rel in local.dirs
        in_r = rel in remote.dirs
        in_b = rel in baseline.dirs
        l_has_files = rel in local_files_under
        r_has_files = rel in remote_files_under

        if in_l and not in_r:
            if in_b and not l_has_files:
                actions.append(Action("rmdir_local", rel, "远程空目录删除传播到本地"))
            else:
                actions.append(Action("mkdir_remote", rel, "远程缺失目录，本地存在"))
        elif in_r and not in_l:
            if in_b and not r_has_files:
                actions.append(Action("rmdir_remote", rel, "本地空目录删除传播到远程"))
            else:
                actions.append(Action("mkdir_local", rel, "本地缺失目录，远程存在"))

    filtered: List[Action] = []
    for a in actions:
        if cfg.no_delete and a.kind in {"delete_local", "delete_remote", "rmdir_local", "rmdir_remote"}:
            continue
        if cfg.only == "upload" and a.kind not in {"upload", "delete_remote", "mkdir_remote", "rmdir_remote", "align_mtime_remote"}:
            continue
        if cfg.only == "download" and a.kind not in {"download", "delete_local", "mkdir_local", "rmdir_local", "align_mtime_local"}:
            continue
        filtered.append(a)
    return filtered


def build_dir_file_index(files: Iterable[str]) -> Set[str]:
    dirs: Set[str] = set()
    for rel in files:
        p = PurePosixPath(rel)
        parts = p.parts[:-1]
        for i in range(1, len(parts) + 1):
            dirs.add(PurePosixPath(*parts[:i]).as_posix())
    return dirs


def print_plan(actions: List[Action]) -> None:
    counts: Dict[str, int] = {}
    bytes_by_kind: Dict[str, int] = {}
    for a in actions:
        counts[a.kind] = counts.get(a.kind, 0) + 1
        bytes_by_kind[a.kind] = bytes_by_kind.get(a.kind, 0) + a.bytes

    print("\n========== 同步计划 ==========")
    if not actions:
        print("没有需要同步的变化。")
        return

    order = [
        "upload", "download", "delete_local", "delete_remote",
        "align_mtime_local", "align_mtime_remote",
        "mkdir_local", "mkdir_remote", "rmdir_local", "rmdir_remote",
        "conflict_skip",
    ]
    for kind in order:
        if counts.get(kind):
            size = bytes_by_kind.get(kind, 0)
            suffix = f"，{human_bytes(size)}" if size else ""
            print(f"{kind:20s}: {counts[kind]}{suffix}")

    print("\n--- 上传列表 ---")
    show_action_list([a for a in actions if a.kind == "upload"])
    print("\n--- 下载列表 ---")
    show_action_list([a for a in actions if a.kind == "download"])
    print("\n--- 删除/目录/时间对齐/冲突列表 ---")
    show_action_list([a for a in actions if a.kind not in {"upload", "download"}], max_items=300)
    print("==============================\n")


def show_action_list(items: List[Action], max_items: int = 300) -> None:
    if not items:
        print("  无")
        return
    for i, a in enumerate(items[:max_items], 1):
        size = human_bytes(a.bytes) if a.bytes else ""
        print(f"  {i:4d}. {a.rel} {size}  # {a.reason}")
    if len(items) > max_items:
        print(f"  ... 其余 {len(items) - max_items} 项省略")


def human_bytes(n: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    x = float(n)
    for u in units:
        if x < 1024 or u == units[-1]:
            return f"{x:.1f}{u}" if u != "B" else f"{int(x)}B"
        x /= 1024
    return f"{n}B"


# =========================
# 传输与文件操作
# =========================

class TransferEngine:
    def __init__(self, cfg: SyncConfig, password: str):
        self.cfg = cfg
        self.password = password
        self.remote_pool = ThreadLocalRemote(cfg, password)
        self.progress_lock = threading.Lock()
        self.conflict_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.trash_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    def execute(
        self,
        actions: List[Action],
        progress_callback: Optional[Callable[[int], None]] = None,
        event_callback: Optional[Callable[[str, Action, Optional[BaseException]], None]] = None,
        use_tqdm: bool = True,
    ) -> List[Tuple[Action, Optional[BaseException]]]:
        if not actions:
            return []

        total_bytes = sum(a.bytes for a in actions if a.kind in {"upload", "download"})
        results: List[Tuple[Action, Optional[BaseException]]] = []
        result_lock = threading.Lock()

        pbar_cm = make_progress(max(total_bytes, 1), "同步传输", unit="B") if use_tqdm else None

        def progress(delta: int) -> None:
            if delta <= 0:
                return
            with self.progress_lock:
                if pbar_cm is not None:
                    pbar_cm.update(delta)
                if progress_callback is not None:
                    progress_callback(delta)

        def run_one(a: Action) -> None:
            if event_callback is not None:
                event_callback("start", a, None)
            try:
                self._execute_one(a, progress)
                if event_callback is not None:
                    event_callback("ok", a, None)
                with result_lock:
                    results.append((a, None))
            except Exception as exc:  # noqa: BLE001
                logging.error("执行失败 [%s] %s: %s", a.kind, a.rel, exc)
                record_failure(self.cfg.local_root, a, exc)
                if event_callback is not None:
                    event_callback("error", a, exc)
                with result_lock:
                    results.append((a, exc))

        try:
            if pbar_cm is not None:
                pbar_cm.__enter__()

            immediate = [a for a in actions if a.kind == "conflict_skip"]
            for a in immediate:
                logging.warning("跳过冲突：%s - %s", a.rel, a.reason)
                if event_callback is not None:
                    event_callback("skip", a, None)
                results.append((a, None))

            todo = [a for a in actions if a.kind != "conflict_skip"]
            with cf.ThreadPoolExecutor(max_workers=self.cfg.transfer_workers, thread_name_prefix="transfer") as ex:
                futs = [ex.submit(run_one, a) for a in todo]
                for fut in cf.as_completed(futs):
                    fut.result()

            if total_bytes == 0:
                if pbar_cm is not None:
                    pbar_cm.update(1)
                if progress_callback is not None:
                    progress_callback(1)
        finally:
            if pbar_cm is not None:
                pbar_cm.__exit__(None, None, None)
        return results

    def _execute_one(self, a: Action, progress: Callable[[int], None]) -> None:
        method = getattr(self, f"do_{a.kind}", None)
        if method is None:
            raise ValueError(f"未知动作类型：{a.kind}")
        retryable(method)(a, progress)

    def do_upload(self, a: Action, progress: Callable[[int], None]) -> None:
        assert a.local is not None
        src = local_path(self.cfg.local_root, a.rel)
        dst = remote_path(self.cfg.remote_root, a.rel)
        ctx = self.remote_pool.get()
        tmp_remote = dst + f".bidisync_tmp_{os.getpid()}_{threading.get_ident()}"

        try:
            with ctx.sftp() as sftp:
                sftp_mkdirs(sftp, posixpath.dirname(dst))
                transferred = 0

                def cb(sent: int, total: int) -> None:
                    nonlocal transferred
                    delta = sent - transferred
                    transferred = sent
                    progress(delta)

                sftp.put(str(src), tmp_remote, callback=cb, confirm=True)

            remote_meta = self.remote_hash_path(ctx, tmp_remote)
            if remote_meta["sha256"] != a.local.sha256:
                raise RuntimeError(f"上传校验失败：{a.rel}")

            if a.backup_loser and a.remote:
                self.backup_remote_file(ctx, a.rel)

            code, out, err = run_remote_python(
                ctx,
                REMOTE_REPLACE_AND_UTIME_PY,
                [tmp_remote, dst, str(a.local.mtime_ns), str(a.local.mode or 0)],
                timeout=120,
            )
            if code != 0:
                raise RuntimeError(f"远程替换失败：{err}")
            logging.info("上传完成：%s", a.rel)
        finally:
            try:
                ctx.exec(f"rm -f {remote_quote(tmp_remote)}", timeout=30)
            except Exception:
                pass

    def do_download(self, a: Action, progress: Callable[[int], None]) -> None:
        assert a.remote is not None
        dst = local_path(self.cfg.local_root, a.rel)
        src = remote_path(self.cfg.remote_root, a.rel)
        ctx = self.remote_pool.get()
        tmp_local = dst.with_name(dst.name + f".bidisync_tmp_{os.getpid()}_{threading.get_ident()}")

        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            with ctx.sftp() as sftp:
                transferred = 0

                def cb(done: int, total: int) -> None:
                    nonlocal transferred
                    delta = done - transferred
                    transferred = done
                    progress(delta)

                sftp.get(src, str(tmp_local), callback=cb, prefetch=True)

            local_hash = sha256_file(tmp_local)
            if local_hash != a.remote.sha256:
                raise RuntimeError(f"下载校验失败：{a.rel}")

            if a.backup_loser and a.local and dst.exists():
                self.backup_local_file(a.rel)

            os.replace(tmp_local, dst)
            os.utime(dst, ns=(a.remote.mtime_ns, a.remote.mtime_ns))
            logging.info("下载完成：%s", a.rel)
        finally:
            tmp_local.unlink(missing_ok=True)

    def do_delete_local(self, a: Action, progress: Callable[[int], None]) -> None:
        path = local_path(self.cfg.local_root, a.rel)
        if not path.exists():
            return
        if DELETE_MODE == "delete":
            path.unlink()
            logging.info("本地删除：%s", a.rel)
        else:
            trash = self.cfg.local_root / TRASH_DIR_NAME / self.trash_stamp / Path(*PurePosixPath(a.rel).parts)
            trash.parent.mkdir(parents=True, exist_ok=True)
            trash = unique_local_path(trash)
            shutil.move(str(path), str(trash))
            logging.info("本地移入回收目录：%s -> %s", a.rel, trash)

    def do_delete_remote(self, a: Action, progress: Callable[[int], None]) -> None:
        ctx = self.remote_pool.get()
        src = remote_path(self.cfg.remote_root, a.rel)
        trash_root = remote_path(self.cfg.remote_root, f"{TRASH_DIR_NAME}/{self.trash_stamp}")
        code, out, err = run_remote_python(
            ctx,
            REMOTE_MOVE_TO_TRASH_PY,
            [src, trash_root, a.rel, DELETE_MODE],
            timeout=120,
        )
        if code != 0:
            raise RuntimeError(f"远程删除失败：{err}")
        logging.info("远程删除/移入回收目录：%s -> %s", a.rel, out.strip())

    def do_mkdir_local(self, a: Action, progress: Callable[[int], None]) -> None:
        path = local_path(self.cfg.local_root, a.rel)
        path.mkdir(parents=True, exist_ok=True)
        logging.info("创建本地目录：%s", a.rel)

    def do_mkdir_remote(self, a: Action, progress: Callable[[int], None]) -> None:
        ctx = self.remote_pool.get()
        path = remote_path(self.cfg.remote_root, a.rel)
        with ctx.sftp() as sftp:
            sftp_mkdirs(sftp, path)
        logging.info("创建远程目录：%s", a.rel)

    def do_rmdir_local(self, a: Action, progress: Callable[[int], None]) -> None:
        path = local_path(self.cfg.local_root, a.rel)
        try:
            path.rmdir()
            logging.info("删除本地空目录：%s", a.rel)
        except FileNotFoundError:
            pass
        except OSError:
            logging.info("本地目录非空，跳过删除：%s", a.rel)

    def do_rmdir_remote(self, a: Action, progress: Callable[[int], None]) -> None:
        ctx = self.remote_pool.get()
        path = remote_path(self.cfg.remote_root, a.rel)
        code, out, err = run_remote_python(ctx, REMOTE_RMDIR_IF_EMPTY_PY, [path], timeout=60)
        if code != 0:
            raise RuntimeError(err)
        logging.info("远程空目录处理：%s -> %s", a.rel, out.strip())

    def do_align_mtime_local(self, a: Action, progress: Callable[[int], None]) -> None:
        assert a.remote is not None
        path = local_path(self.cfg.local_root, a.rel)
        if path.exists():
            os.utime(path, ns=(a.remote.mtime_ns, a.remote.mtime_ns))
            logging.info("对齐本地 mtime：%s", a.rel)

    def do_align_mtime_remote(self, a: Action, progress: Callable[[int], None]) -> None:
        assert a.local is not None
        ctx = self.remote_pool.get()
        path = remote_path(self.cfg.remote_root, a.rel)
        code, out, err = run_remote_python(
            ctx,
            "import os, sys; p=sys.argv[1]; ns=int(sys.argv[2]); os.utime(p, ns=(ns, ns)); print('ok')",
            [path, str(a.local.mtime_ns)],
            timeout=60,
        )
        if code != 0:
            raise RuntimeError(err)
        logging.info("对齐远程 mtime：%s", a.rel)

    def remote_hash_path(self, ctx: SSHContext, path: str) -> Dict[str, Any]:
        code, out, err = run_remote_python(ctx, REMOTE_HASH_ONE_PY, [path], timeout=300)
        if code != 0:
            raise RuntimeError(f"远程哈希失败：{err}")
        return json.loads(out.strip())

    def backup_local_file(self, rel: str) -> None:
        src = local_path(self.cfg.local_root, rel)
        if not src.exists():
            return
        dst = self.cfg.local_root / CONFLICT_DIR_NAME / self.conflict_stamp / "local_loser" / Path(*PurePosixPath(rel).parts)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst = unique_local_path(dst)
        shutil.copy2(src, dst)
        logging.info("备份本地被覆盖版本：%s -> %s", rel, dst)

    def backup_remote_file(self, ctx: SSHContext, rel: str) -> None:
        src = remote_path(self.cfg.remote_root, rel)
        dst = remote_path(self.cfg.remote_root, f"{CONFLICT_DIR_NAME}/{self.conflict_stamp}/remote_loser/{rel}")
        code, out, err = run_remote_python(ctx, REMOTE_COPY_FILE_PY, [src, dst], timeout=300)
        if code != 0:
            raise RuntimeError(f"远程冲突备份失败：{err}")
        logging.info("备份远程被覆盖版本：%s -> %s", rel, dst)


def unique_local_path(path: Path) -> Path:
    if not path.exists():
        return path
    base = path
    i = 1
    while True:
        candidate = base.with_name(base.name + f".{i}")
        if not candidate.exists():
            return candidate
        i += 1


# =========================
# Docker Compose 编排
# =========================

REMOTE_COMPOSE_RUNNER_PY = r"""
import os, subprocess, sys
workdir = sys.argv[1]
cmd = sys.argv[2]
timeout = int(sys.argv[3])
if not os.path.isdir(workdir):
    print(f"compose workdir not found: {workdir}", file=sys.stderr)
    sys.exit(2)
proc = subprocess.run(cmd, shell=True, cwd=workdir, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
if proc.stdout:
    print(proc.stdout, end="")
if proc.stderr:
    print(proc.stderr, end="", file=sys.stderr)
sys.exit(proc.returncode)
"""


class DockerComposeManager:
    def __init__(self, cfg: SyncConfig, password: str, log_callback: Optional[Callable[[str], None]] = None):
        self.cfg = cfg
        self.password = password
        self.log_callback = log_callback

    def _log(self, msg: str) -> None:
        logging.info(msg)
        if self.log_callback:
            self.log_callback(msg)

    def run(self, command: str, label: str) -> None:
        if not self.cfg.docker_enabled:
            return
        self._log(f"Docker Compose {label}: cd {self.cfg.docker_project_dir} && {command}")
        ctx = SSHContext(self.cfg, self.password)
        try:
            code, out, err = run_remote_python(
                ctx,
                REMOTE_COMPOSE_RUNNER_PY,
                [self.cfg.docker_project_dir, command, str(self.cfg.docker_timeout)],
                timeout=self.cfg.docker_timeout + 30,
            )
            if out.strip():
                self._log(out.strip()[-4000:])
            if err.strip():
                self._log(err.strip()[-4000:])
            if code != 0:
                raise RuntimeError(f"Docker Compose {label} 失败，退出码 {code}")
        finally:
            ctx.close()

    def down(self) -> None:
        self.run(self.cfg.docker_down_cmd, "down")

    def up(self) -> None:
        self.run(self.cfg.docker_up_cmd, "up")


def action_mutates_remote(a: Action) -> bool:
    return a.kind in {"upload", "delete_remote", "mkdir_remote", "rmdir_remote", "align_mtime_remote"}


def action_uploads_database(a: Action) -> bool:
    return a.kind == "upload" and is_sqlite_path(a.rel)


def docker_plan_for_actions(actions: List[Action], cfg: SyncConfig) -> str:
    if not cfg.docker_enabled:
        return "disabled"
    if any(action_uploads_database(a) for a in actions):
        return "down_before_sync_then_up"
    if any(action_mutates_remote(a) for a in actions):
        return "sync_then_restart"
    return "no_remote_change"


def execute_actions_with_docker(
    cfg: SyncConfig,
    password: str,
    actions: List[Action],
    progress_callback: Optional[Callable[[int], None]] = None,
    event_callback: Optional[Callable[[str, Action, Optional[BaseException]], None]] = None,
    log_callback: Optional[Callable[[str], None]] = None,
    use_tqdm: bool = True,
) -> List[Tuple[Action, Optional[BaseException]]]:
    plan = docker_plan_for_actions(actions, cfg)
    docker = DockerComposeManager(cfg, password, log_callback=log_callback) if cfg.docker_enabled else None

    def log(msg: str) -> None:
        logging.info(msg)
        if log_callback:
            log_callback(msg)

    if plan == "down_before_sync_then_up" and docker:
        log("检测到数据库文件将上传到服务器：先停止远程 Docker Compose。")
        docker.down()
        try:
            engine = TransferEngine(cfg, password)
            return engine.execute(actions, progress_callback=progress_callback, event_callback=event_callback, use_tqdm=use_tqdm)
        finally:
            log("恢复远程 Docker Compose：执行重建并后台启动。")
            docker.up()

    engine = TransferEngine(cfg, password)
    results = engine.execute(actions, progress_callback=progress_callback, event_callback=event_callback, use_tqdm=use_tqdm)

    failed = [exc for _, exc in results if exc is not None]
    if plan == "sync_then_restart" and docker:
        if failed:
            log("存在同步失败项，跳过 Docker Compose 重启，避免部署半成品。")
        else:
            log("远程文件已变更：同步完成后重建并后台启动 Docker Compose。")
            docker.down()
            docker.up()
    return results


# =========================
# Web 控制台
# =========================

SCAN_CACHE: Dict[str, Dict[str, Any]] = {}
JOB_CACHE: Dict[str, Dict[str, Any]] = {}
CACHE_LOCK = threading.RLock()


def meta_to_web(meta: Optional[FileMeta]) -> Optional[Dict[str, Any]]:
    if meta is None:
        return None
    return {
        "size": meta.size,
        "mtime_ns": meta.mtime_ns,
        "mtime": datetime.fromtimestamp(meta.mtime_s).strftime("%Y-%m-%d %H:%M:%S"),
        "sha256": meta.sha256,
    }


def action_id(index: int, a: Action) -> str:
    return f"{index}:{a.kind}:{a.rel}"


def action_to_web(index: int, a: Action) -> Dict[str, Any]:
    arrow = {
        "upload": "→", "download": "←", "delete_remote": "→ 删除远程", "delete_local": "← 删除本地",
        "mkdir_remote": "→ 建目录", "mkdir_local": "← 建目录", "rmdir_remote": "→ 删目录", "rmdir_local": "← 删目录",
        "align_mtime_remote": "→ 对齐时间", "align_mtime_local": "← 对齐时间", "conflict_skip": "⚠",
    }.get(a.kind, "•")
    depth = max(0, len(PurePosixPath(a.rel).parts) - 1)
    return {
        "id": action_id(index, a), "index": index, "kind": a.kind, "rel": a.rel,
        "name": PurePosixPath(a.rel).name, "depth": depth, "arrow": arrow, "reason": a.reason,
        "bytes": a.bytes, "bytes_human": human_bytes(a.bytes) if a.bytes else "",
        "local": meta_to_web(a.local), "remote": meta_to_web(a.remote),
        "is_conflict": a.kind == "conflict_skip", "is_db_upload": action_uploads_database(a),
        "default_selected": a.kind != "conflict_skip",
    }


def summarize_actions(actions: List[Action]) -> Dict[str, Any]:
    counts: Dict[str, int] = {}
    bytes_total = 0
    for a in actions:
        counts[a.kind] = counts.get(a.kind, 0) + 1
        bytes_total += a.bytes
    return {
        "total": len(actions), "counts": counts, "bytes": bytes_total, "bytes_human": human_bytes(bytes_total),
        "db_upload_count": sum(1 for a in actions if action_uploads_database(a)),
        "remote_mutation_count": sum(1 for a in actions if action_mutates_remote(a)),
    }


def cfg_from_web_payload(data: Dict[str, Any], base: SyncConfig) -> SyncConfig:
    def as_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except Exception:
            return default
    return SyncConfig(
        remote_host=str(data.get("remote_host") or base.remote_host).strip(),
        remote_port=as_int(data.get("remote_port"), base.remote_port),
        remote_user=str(data.get("remote_user") or base.remote_user).strip(),
        remote_root=str(data.get("remote_root") or base.remote_root).strip(),
        local_root=base.local_root,
        scan_workers=base.scan_workers,
        transfer_workers=max(1, as_int(data.get("workers"), base.transfer_workers)),
        remote_scan_workers=base.remote_scan_workers,
        dry_run=False,
        yes=True,
        no_delete=bool(data.get("no_delete", base.no_delete)),
        only=str(data.get("only") or base.only),
        docker_enabled=bool(data.get("docker_enabled", base.docker_enabled)),
        docker_project_dir=str(data.get("docker_project_dir") or base.docker_project_dir).strip(),
        docker_down_cmd=str(data.get("docker_down_cmd") or base.docker_down_cmd).strip(),
        docker_up_cmd=str(data.get("docker_up_cmd") or base.docker_up_cmd).strip(),
        docker_timeout=base.docker_timeout,
        web=True,
        web_host=base.web_host,
        web_port=base.web_port,
    )


def run_scan_for_web(cfg: SyncConfig, password: str) -> Dict[str, Any]:
    preflight(cfg)
    local_manifest = read_local_manifest(cfg.local_root)
    remote_manifest = read_remote_manifest(cfg, password)
    baseline = choose_manifest(local_manifest, remote_manifest)
    with cf.ThreadPoolExecutor(max_workers=2) as ex:
        fut_local = ex.submit(scan_local, cfg.local_root, cfg.scan_workers)
        fut_remote = ex.submit(scan_remote, cfg, password)
        local_snap = fut_local.result()
        remote_snap = fut_remote.result()
    actions = plan_sync(local_snap, remote_snap, baseline, cfg)
    scan_id = uuid.uuid4().hex
    with CACHE_LOCK:
        SCAN_CACHE[scan_id] = {"cfg": cfg, "baseline": baseline, "local_snap": local_snap, "remote_snap": remote_snap, "actions": actions, "created_at": time.time()}
        if len(SCAN_CACHE) > 10:
            old_ids = sorted(SCAN_CACHE, key=lambda k: SCAN_CACHE[k]["created_at"])[:-10]
            for old_id in old_ids:
                SCAN_CACHE.pop(old_id, None)
    return {
        "scan_id": scan_id,
        "local_root": str(cfg.local_root),
        "remote": f"{cfg.remote_user}@{cfg.remote_host}:{cfg.remote_root}",
        "baseline_saved_at": baseline.saved_at,
        "actions": [action_to_web(i, a) for i, a in enumerate(actions)],
        "summary": summarize_actions(actions),
        "docker_plan": docker_plan_for_actions(actions, cfg),
    }


def start_web_sync_job(scan_id: str, selected_ids: List[str], password: str) -> str:
    with CACHE_LOCK:
        scan = SCAN_CACHE.get(scan_id)
        if not scan:
            raise KeyError("扫描结果已过期，请重新扫描。")
        cfg: SyncConfig = scan["cfg"]
        baseline: Manifest = scan["baseline"]
        local_snap: Snapshot = scan["local_snap"]
        remote_snap: Snapshot = scan["remote_snap"]
        actions: List[Action] = scan["actions"]
        by_id = {action_id(i, a): a for i, a in enumerate(actions)}
        selected = [by_id[x] for x in selected_ids if x in by_id]

    job_id = uuid.uuid4().hex
    job = {"id": job_id, "status": "queued", "created_at": now_utc_iso(), "updated_at": now_utc_iso(),
           "total_bytes": max(1, sum(a.bytes for a in selected if a.kind in {"upload", "download"})),
           "done_bytes": 0, "total_items": len(selected), "done_items": 0, "failed_items": 0,
           "logs": [], "error": None, "docker_plan": docker_plan_for_actions(selected, cfg)}
    with CACHE_LOCK:
        JOB_CACHE[job_id] = job

    def log(msg: str) -> None:
        with CACHE_LOCK:
            job["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
            job["logs"] = job["logs"][-500:]
            job["updated_at"] = now_utc_iso()

    def add_progress(delta: int) -> None:
        with CACHE_LOCK:
            job["done_bytes"] = min(job["total_bytes"], int(job["done_bytes"]) + max(0, int(delta)))
            job["updated_at"] = now_utc_iso()

    def on_event(state: str, action: Action, exc: Optional[BaseException]) -> None:
        if state == "start":
            log(f"开始 {action.kind}: {action.rel}")
        elif state in {"ok", "skip"}:
            with CACHE_LOCK:
                job["done_items"] += 1
            log(("完成" if state == "ok" else "跳过冲突") + f" {action.kind}: {action.rel}")
        elif state == "error":
            with CACHE_LOCK:
                job["done_items"] += 1
                job["failed_items"] += 1
            log(f"失败 {action.kind}: {action.rel} - {exc}")

    def worker() -> None:
        try:
            with CACHE_LOCK:
                job["status"] = "running"
                job["updated_at"] = now_utc_iso()
            if not selected:
                log("没有选择任何同步项。")
                with CACHE_LOCK:
                    job["status"] = "done"
                return
            log(f"开始执行选中同步项：{len(selected)} 项；Docker 策略：{job['docker_plan']}")
            results = execute_actions_with_docker(cfg, password, selected, progress_callback=add_progress, event_callback=on_event, log_callback=log, use_tqdm=False)
            failed_rels = {a.rel for a, exc in results if exc is not None}
            new_manifest = build_new_manifest(local_snap, remote_snap, baseline, selected, failed_rels)
            write_local_manifest(cfg.local_root, new_manifest)
            write_remote_manifest(cfg, password, new_manifest)
            with CACHE_LOCK:
                job["status"] = "failed" if failed_rels else "done"
                job["error"] = f"{len(failed_rels)} 项失败" if failed_rels else None
                job["updated_at"] = now_utc_iso()
            log(f"同步结束：成功 {len(results) - len(failed_rels)} 项，失败 {len(failed_rels)} 项。")
        except Exception as exc:  # noqa: BLE001
            logging.exception("Web 同步任务失败：%s", exc)
            with CACHE_LOCK:
                job["status"] = "failed"
                job["error"] = str(exc)
                job["updated_at"] = now_utc_iso()
            log(f"任务异常：{exc}")

    threading.Thread(target=worker, name=f"web-sync-{job_id[:8]}", daemon=True).start()
    return job_id


WEB_HTML = r"""
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>bidisync 可视化同步控制台</title>
<style>
:root{--bg:#0f172a;--panel:#111827;--line:#243044;--text:#e5e7eb;--muted:#94a3b8;--blue:#60a5fa;--green:#34d399;--yellow:#fbbf24}*{box-sizing:border-box}body{margin:0;background:linear-gradient(135deg,#0f172a,#111827 60%,#172036);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Microsoft YaHei",Arial,sans-serif;font-size:14px}header{padding:22px 28px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;background:rgba(15,23,42,.86);position:sticky;top:0;z-index:5}h1{font-size:20px;margin:0}.sub{color:var(--muted);font-size:12px;margin-top:4px}main{padding:22px;display:grid;gap:16px}.card{background:rgba(17,24,39,.94);border:1px solid var(--line);border-radius:18px;box-shadow:0 18px 60px rgba(0,0,0,.25);overflow:hidden}.card h2{font-size:15px;margin:0;padding:14px 18px;border-bottom:1px solid var(--line)}.form{display:grid;grid-template-columns:repeat(12,1fr);gap:12px;padding:16px 18px}.field{display:flex;flex-direction:column;gap:6px;grid-column:span 3}.w2{grid-column:span 2}.w4{grid-column:span 4}.w5{grid-column:span 5}.w12{grid-column:span 12}label{color:var(--muted);font-size:12px}input,select{width:100%;background:#0b1220;color:var(--text);border:1px solid #263449;border-radius:10px;padding:10px 11px;outline:none}.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.btn{background:#2563eb;border:0;color:white;border-radius:11px;padding:10px 15px;cursor:pointer;font-weight:700}.btn.secondary{background:#334155}.btn.green{background:#059669}.btn:disabled{opacity:.55;cursor:not-allowed}.check{display:flex;align-items:center;gap:8px}.stats{display:flex;gap:10px;flex-wrap:wrap;padding:14px 18px;border-top:1px solid var(--line)}.pill{background:#0b1220;border:1px solid var(--line);border-radius:999px;padding:7px 10px;color:var(--muted)}.pill b{color:white}.toolbar{display:flex;gap:10px;justify-content:space-between;align-items:center;padding:12px 18px;border-top:1px solid var(--line);border-bottom:1px solid var(--line);background:#0b1220}.toolbar input{max-width:360px}.diff-head,.diff-row{display:grid;grid-template-columns:minmax(280px,1fr) 110px minmax(280px,1fr) 250px;align-items:center}.diff-head{position:sticky;top:72px;z-index:3;background:#111827;color:var(--muted);border-bottom:1px solid var(--line);font-size:12px}.diff-head div{padding:10px 14px}.diff-list{max-height:60vh;overflow:auto}.diff-row{border-bottom:1px solid rgba(36,48,68,.72)}.diff-row:hover{background:rgba(96,165,250,.07)}.cell{padding:8px 14px;min-width:0}.path{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.name{font-family:Consolas,"JetBrains Mono",monospace}.muted{color:var(--muted)}.arrow{text-align:center;font-size:18px;font-weight:900}.arrow.upload{color:var(--green)}.arrow.download{color:var(--blue)}.arrow.warn{color:var(--yellow)}.kind{font-size:12px;border:1px solid var(--line);padding:4px 7px;border-radius:8px;color:var(--muted)}.db{color:var(--yellow);font-weight:800}.reason{font-size:12px;color:var(--muted);margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.progress{height:14px;background:#0b1220;border:1px solid var(--line);border-radius:999px;overflow:hidden}.bar{height:100%;width:0;background:linear-gradient(90deg,#2563eb,#34d399);transition:width .25s}.log{background:#020617;color:#cbd5e1;font-family:Consolas,monospace;font-size:12px;line-height:1.55;padding:14px;height:230px;overflow:auto;white-space:pre-wrap}@media(max-width:980px){.field,.w2,.w4,.w5{grid-column:span 12}.diff-head,.diff-row{grid-template-columns:1fr 70px 1fr}.diff-head div:nth-child(4),.diff-row .meta{display:none}}
</style>
</head>
<body>
<header><div><h1>bidisync 可视化同步控制台</h1><div class="sub">左侧本地项目，右侧云服务器项目；只比较文件状态、路径、mtime、size 和 SHA-256。</div></div><div class="pill">SSH 密码仅内存使用</div></header>
<main>
<section class="card"><h2>连接与同步配置</h2><div class="form">
<div class="field"><label>远程 IP</label><input id="remote_host" value="{{ remote_host }}"></div><div class="field w2"><label>端口</label><input id="remote_port" value="{{ remote_port }}"></div><div class="field w2"><label>用户</label><input id="remote_user" value="{{ remote_user }}"></div><div class="field w5"><label>远程项目路径</label><input id="remote_root" value="{{ remote_root }}"></div>
<div class="field w4"><label>SSH 密码</label><input id="password" type="password" autocomplete="current-password" placeholder="在这里输入服务器密码"></div><div class="field w2"><label>方向过滤</label><select id="only"><option value="all">全部</option><option value="upload">只看上传</option><option value="download">只看下载</option></select></div><div class="field w2"><label>并发传输</label><input id="workers" type="number" min="1" max="32" value="{{ workers }}"></div><div class="field w4"><label>选项</label><div class="row"><label class="check"><input id="no_delete" type="checkbox">不执行删除传播</label><label class="check"><input id="docker_enabled" type="checkbox" checked>启用 Docker Compose</label></div></div>
<div class="field w4"><label>Docker Compose 目录</label><input id="docker_project_dir" value="{{ docker_project_dir }}"></div><div class="field w4"><label>down 命令</label><input id="docker_down_cmd" value="{{ docker_down_cmd }}"></div><div class="field w4"><label>up 命令</label><input id="docker_up_cmd" value="{{ docker_up_cmd }}"></div>
<div class="field w12"><div class="row"><button class="btn secondary" id="testBtn">测试 SSH 连接</button><button class="btn" id="scanBtn">扫描差异</button><button class="btn green" id="syncBtn" disabled>同步选中项</button><button class="btn secondary" id="selectAllBtn" disabled>全选</button><button class="btn secondary" id="selectNoneBtn" disabled>全不选</button><span class="muted" id="status">等待扫描</span></div></div>
</div><div class="stats" id="stats"></div></section>
<section class="card"><h2>差异对比</h2><div class="toolbar"><input id="filter" placeholder="过滤路径，例如 templates、.py、db"><div class="muted" id="selectedInfo">未选择</div></div><div class="diff-head"><div>本地 Local</div><div class="arrow">操作</div><div>云服务器 Remote</div><div>状态</div></div><div class="diff-list" id="diffList"></div></section>
<section class="card"><h2>同步进度</h2><div style="padding:16px 18px;display:grid;gap:12px"><div class="progress"><div class="bar" id="bar"></div></div><div class="muted" id="progressText">尚未开始</div><div class="log" id="log"></div></div></section>
</main>
<script>
let scanId=null,actions=[],selected=new Set(),currentJob=null;const $=id=>document.getElementById(id);
function payload(){return{remote_host:$('remote_host').value.trim(),remote_port:$('remote_port').value.trim(),remote_user:$('remote_user').value.trim(),remote_root:$('remote_root').value.trim(),password:$('password').value,only:$('only').value,workers:$('workers').value,no_delete:$('no_delete').checked,docker_enabled:$('docker_enabled').checked,docker_project_dir:$('docker_project_dir').value.trim(),docker_down_cmd:$('docker_down_cmd').value.trim(),docker_up_cmd:$('docker_up_cmd').value.trim()};}
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function setBusy(b,msg){$('testBtn').disabled=b;$('scanBtn').disabled=b;$('syncBtn').disabled=b||!scanId||selected.size===0;$('status').textContent=msg||'';}
function renderStats(summary,dockerPlan){const c=summary.counts||{};$('stats').innerHTML=[`<span class="pill">总差异 <b>${summary.total}</b></span>`,`<span class="pill">上传 <b>${c.upload||0}</b></span>`,`<span class="pill">下载 <b>${c.download||0}</b></span>`,`<span class="pill">冲突 <b>${c.conflict_skip||0}</b></span>`,`<span class="pill">传输量 <b>${summary.bytes_human}</b></span>`,`<span class="pill">数据库上传 <b>${summary.db_upload_count}</b></span>`,`<span class="pill">Docker 策略 <b>${dockerPlan}</b></span>`].join('');}
function renderActions(){const q=$('filter').value.trim().toLowerCase(),box=$('diffList');let html='',shown=0;for(const a of actions){if(q&&!a.rel.toLowerCase().includes(q))continue;shown++;const checked=selected.has(a.id)?'checked':'';const cls=a.kind==='upload'?'upload':(a.kind==='download'?'download':(a.kind==='conflict_skip'?'warn':''));const indent=a.depth*18;const lm=a.local?`${esc(a.local.mtime)} · ${esc(a.bytes_human||'')}`:'不存在';const rm=a.remote?`${esc(a.remote.mtime)} · ${esc(a.bytes_human||'')}`:'不存在';html+=`<div class="diff-row" title="${esc(a.reason)}"><div class="cell"><label class="row" style="gap:8px"><input type="checkbox" data-id="${esc(a.id)}" ${checked}><span class="path name" style="padding-left:${indent}px">${esc(a.rel)}</span></label><div class="reason">${lm}</div></div><div class="cell arrow ${cls}">${esc(a.arrow)}</div><div class="cell"><div class="path name" style="padding-left:${indent}px">${esc(a.rel)}</div><div class="reason">${rm}</div></div><div class="cell meta"><span class="kind">${esc(a.kind)}</span> ${a.is_db_upload?'<span class="db">DB</span>':''}<div class="reason">${esc(a.reason)}</div></div></div>`}box.innerHTML=html||'<div class="cell muted">没有匹配的差异。</div>';box.querySelectorAll('input[type=checkbox]').forEach(cb=>cb.onchange=()=>{cb.checked?selected.add(cb.dataset.id):selected.delete(cb.dataset.id);updateSelectedInfo();});updateSelectedInfo(shown);}
function updateSelectedInfo(shown){$('selectedInfo').textContent=`已选 ${selected.size} 项${shown!==undefined?'，当前显示 '+shown+' 项':''}`;$('syncBtn').disabled=!scanId||selected.size===0;$('selectAllBtn').disabled=!scanId;$('selectNoneBtn').disabled=!scanId;}
$('filter').oninput=renderActions;$('selectAllBtn').onclick=()=>{actions.forEach(a=>{if(a.kind!=='conflict_skip')selected.add(a.id)});renderActions()};$('selectNoneBtn').onclick=()=>{selected.clear();renderActions()};
$('testBtn').onclick=async()=>{if(!$('password').value){alert('请先输入 SSH 密码');return}setBusy(true,'正在测试 SSH 连接...');$('log').textContent='';try{const r=await fetch('/api/test-ssh',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload())});const data=await r.json();if(!r.ok||!data.ok)throw new Error(data.error||data.message||'SSH 连接测试失败');$('status').textContent='SSH 连接正常';$('log').textContent=`SSH 连接正常\nBanner: ${data.banner||''}`;}catch(e){$('status').textContent='SSH 连接失败';$('log').textContent=e.message;alert(e.message)}finally{setBusy(false,$('status').textContent)}};
$('scanBtn').onclick=async()=>{if(!$('password').value){alert('请先输入 SSH 密码');return}setBusy(true,'正在扫描本地和远程...');$('log').textContent='';try{const r=await fetch('/api/scan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload())});const data=await r.json();if(!r.ok)throw new Error(data.error||'扫描失败');scanId=data.scan_id;actions=data.actions;selected=new Set(actions.filter(a=>a.default_selected).map(a=>a.id));renderStats(data.summary,data.docker_plan);renderActions();$('status').textContent=`扫描完成：${actions.length} 项差异`}catch(e){$('status').textContent='扫描失败';alert(e.message)}finally{setBusy(false,$('status').textContent)}};
$('syncBtn').onclick=async()=>{if(!scanId){alert('请先扫描');return}if(!$('password').value){alert('请先输入 SSH 密码');return}if(selected.size===0){alert('没有选择同步项');return}if(!confirm(`确认同步选中的 ${selected.size} 项？`))return;setBusy(true,'正在创建同步任务...');$('bar').style.width='0%';$('progressText').textContent='任务已提交';$('log').textContent='';try{const body=payload();body.scan_id=scanId;body.selected_ids=[...selected];const r=await fetch('/api/start-sync',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await r.json();if(!r.ok)throw new Error(data.error||'启动失败');currentJob=data.job_id;pollJob()}catch(e){alert(e.message);setBusy(false,'同步启动失败')}};
async function pollJob(){if(!currentJob)return;const r=await fetch('/api/job/'+currentJob);const j=await r.json();const pct=Math.min(100,Math.round((j.done_bytes||0)/(j.total_bytes||1)*100));$('bar').style.width=pct+'%';$('progressText').textContent=`状态：${j.status}；文件 ${j.done_items||0}/${j.total_items||0}；失败 ${j.failed_items||0}；传输 ${pct}%`+(j.error?`；${j.error}`:'');$('log').textContent=(j.logs||[]).join('\n');$('log').scrollTop=$('log').scrollHeight;if(j.status==='running'||j.status==='queued')setTimeout(pollJob,1000);else setBusy(false,j.status==='done'?'同步完成':'同步失败')}
</script>
</body></html>
"""


def run_web_server(base_cfg: SyncConfig) -> int:
    try:
        from flask import Flask, jsonify, render_template_string, request
    except ImportError:
        print("缺少依赖 flask，请先执行：pip install flask paramiko tqdm", file=sys.stderr)
        return 1

    preflight(base_cfg)
    setup_logging(base_cfg.local_root)
    app = Flask(__name__)

    @app.get("/")
    def index():
        return render_template_string(
            WEB_HTML,
            remote_host=base_cfg.remote_host,
            remote_port=base_cfg.remote_port,
            remote_user=base_cfg.remote_user,
            remote_root=base_cfg.remote_root,
            workers=base_cfg.transfer_workers,
            docker_project_dir=base_cfg.docker_project_dir,
            docker_down_cmd=base_cfg.docker_down_cmd,
            docker_up_cmd=base_cfg.docker_up_cmd,
        )

    @app.post("/api/test-ssh")
    def api_test_ssh():
        try:
            data = request.get_json(force=True) or {}
            password = str(data.get("password") or "")
            if not password:
                return jsonify(ok=False, error="请在页面输入 SSH 密码。"), 400
            cfg = cfg_from_web_payload(data, base_cfg)
            result = test_ssh_connection(cfg, password)
            if result.get("ok"):
                return jsonify(ok=True, message="SSH 连接正常。", banner=result.get("banner", ""))
            message = result.get("error") or result.get("message") or "SSH 连接测试失败。"
            if result.get("stage") == "banner":
                message = format_ssh_exception(cfg, paramiko.SSHException("Error reading SSH protocol banner"))
            return jsonify(ok=False, error=message, detail=result), 400
        except Exception as exc:  # noqa: BLE001
            logging.exception("Web SSH 测试失败：%s", exc)
            return jsonify(ok=False, error=str(exc)), 500

    @app.post("/api/scan")
    def api_scan():
        try:
            data = request.get_json(force=True) or {}
            password = str(data.get("password") or "")
            if not password:
                return jsonify(error="请在页面输入 SSH 密码。"), 400
            cfg = cfg_from_web_payload(data, base_cfg)
            result = run_scan_for_web(cfg, password)
            return jsonify(result)
        except SSHConnectionError as exc:
            logging.warning("Web 扫描 SSH 连接失败：%s", exc)
            return jsonify(error=str(exc)), 400
        except Exception as exc:  # noqa: BLE001
            logging.exception("Web 扫描失败：%s", exc)
            return jsonify(error=str(exc)), 500

    @app.post("/api/start-sync")
    def api_start_sync():
        try:
            data = request.get_json(force=True) or {}
            password = str(data.get("password") or "")
            if not password:
                return jsonify(error="请在页面输入 SSH 密码。"), 400
            scan_id = str(data.get("scan_id") or "")
            selected_ids = list(data.get("selected_ids") or [])
            with CACHE_LOCK:
                if scan_id in SCAN_CACHE:
                    SCAN_CACHE[scan_id]["cfg"] = cfg_from_web_payload(data, base_cfg)
            job_id = start_web_sync_job(scan_id, selected_ids, password)
            return jsonify(job_id=job_id)
        except SSHConnectionError as exc:
            logging.warning("Web 同步 SSH 连接失败：%s", exc)
            return jsonify(error=str(exc)), 400
        except Exception as exc:  # noqa: BLE001
            logging.exception("Web 同步启动失败：%s", exc)
            return jsonify(error=str(exc)), 500

    @app.get("/api/job/<job_id>")
    def api_job(job_id: str):
        with CACHE_LOCK:
            job = JOB_CACHE.get(job_id)
            if not job:
                return jsonify(error="任务不存在或已过期"), 404
            return jsonify(dict(job))

    print(f"\nWeb 控制台已启动：http://{base_cfg.web_host}:{base_cfg.web_port}")
    print("注意：Web 页面会让你输入 SSH 密码；密码只在请求内存中使用，不写入文件。\n")
    app.run(host=base_cfg.web_host, port=base_cfg.web_port, debug=False, threaded=True)
    return 0

# =========================
# 同步后清单生成
# =========================

def build_new_manifest(
    local: Snapshot,
    remote: Snapshot,
    baseline: Manifest,
    actions: List[Action],
    failed: Set[str],
) -> Manifest:
    """
    根据扫描结果和成功动作生成下一次同步的 baseline。
    注意：未同步、失败、跳过冲突的文件不写入“新状态”，尽量保持旧 baseline，避免下一次误判。
    """
    action_by_rel = {a.rel: a for a in actions}
    files: Dict[str, FileMeta] = {}
    all_rels = sorted(set(local.files) | set(remote.files) | set(baseline.files))

    for rel in all_rels:
        old = baseline.files.get(rel)
        a = action_by_rel.get(rel)
        l = local.files.get(rel)
        r = remote.files.get(rel)

        if rel in failed:
            if old:
                files[rel] = old
            continue

        if a:
            if a.kind == "upload" and a.local:
                files[rel] = a.local
            elif a.kind == "download" and a.remote:
                files[rel] = a.remote
            elif a.kind in {"delete_local", "delete_remote"}:
                continue
            elif a.kind == "align_mtime_local" and a.remote:
                files[rel] = a.remote
            elif a.kind == "align_mtime_remote" and a.local:
                files[rel] = a.local
            elif a.kind == "conflict_skip":
                if old:
                    files[rel] = old
            else:
                if l and r and l.sha256 == r.sha256:
                    files[rel] = l if l.mtime_ns >= r.mtime_ns else r
                elif old:
                    files[rel] = old
            continue

        # 无动作：只有当两边内容确实一致时，才更新 baseline。
        if l and r and l.sha256 == r.sha256:
            files[rel] = l if l.mtime_ns >= r.mtime_ns else r
        elif not l and not r:
            continue
        elif old:
            files[rel] = old

    dirs = build_dir_file_index(files.keys())
    action_dir_by_rel = {a.rel: a for a in actions if a.kind.startswith("mkdir_") or a.kind.startswith("rmdir_")}
    all_dirs = sorted(set(local.dirs) | set(remote.dirs) | set(baseline.dirs))
    for rel in all_dirs:
        a = action_dir_by_rel.get(rel)
        if rel in failed:
            if rel in baseline.dirs:
                dirs.add(rel)
            continue
        if a:
            if a.kind in {"mkdir_local", "mkdir_remote"}:
                dirs.add(rel)
            elif a.kind in {"rmdir_local", "rmdir_remote"}:
                dirs.discard(rel)
        else:
            if rel in local.dirs and rel in remote.dirs:
                dirs.add(rel)
            elif rel in baseline.dirs:
                dirs.add(rel)

    return Manifest(version=1, saved_at=now_utc_iso(), files=files, dirs=dirs)


# =========================
# 主流程
# =========================

def parse_args() -> SyncConfig:
    p = argparse.ArgumentParser(description="本地 <-> 远程 双向增量同步器")
    p.add_argument("--dry-run", action="store_true", help="只显示同步计划，不执行")
    p.add_argument("--yes", "-y", action="store_true", help="跳过人工确认")
    p.add_argument("--no-delete", action="store_true", help="不执行删除传播")
    p.add_argument("--only", choices=["all", "upload", "download"], default="all", help="只执行指定方向")
    p.add_argument("--workers", type=int, default=TRANSFER_WORKERS, help="传输并发数")
    p.add_argument("--remote-host", default=REMOTE_HOST, help="覆盖脚本内 REMOTE_HOST")
    p.add_argument("--remote-root", default=REMOTE_PROJECT_ROOT, help="覆盖脚本内 REMOTE_PROJECT_ROOT")
    p.add_argument("--remote-user", default=REMOTE_USER, help="覆盖脚本内 REMOTE_USER")
    p.add_argument("--remote-port", type=int, default=REMOTE_PORT, help="覆盖脚本内 REMOTE_PORT")
    p.add_argument("--no-docker", action="store_true", help="禁用远程 Docker Compose 自动编排")
    p.add_argument("--docker-dir", default=DOCKER_COMPOSE_PROJECT_DIR, help="远程 docker compose 执行目录")
    p.add_argument("--docker-down-cmd", default=DOCKER_COMPOSE_DOWN_COMMAND, help="远程停止命令")
    p.add_argument("--docker-up-cmd", default=DOCKER_COMPOSE_UP_COMMAND, help="远程重建启动命令")
    p.add_argument("--web", action="store_true", help="启动 Flask Web 控制台")
    p.add_argument("--web-host", default=WEB_HOST, help="Web 控制台监听地址")
    p.add_argument("--web-port", type=int, default=WEB_PORT, help="Web 控制台监听端口")
    args = p.parse_args()

    return SyncConfig(
        remote_host=args.remote_host,
        remote_port=args.remote_port,
        remote_user=args.remote_user,
        remote_root=args.remote_root,
        local_root=LOCAL_PROJECT_ROOT,
        transfer_workers=max(1, args.workers),
        dry_run=args.dry_run,
        yes=args.yes,
        no_delete=args.no_delete,
        only=args.only,
        docker_enabled=not args.no_docker,
        docker_project_dir=args.docker_dir,
        docker_down_cmd=args.docker_down_cmd,
        docker_up_cmd=args.docker_up_cmd,
        web=args.web,
        web_host=args.web_host,
        web_port=args.web_port,
    )

def preflight(cfg: SyncConfig) -> None:
    if not cfg.local_root.exists():
        raise RuntimeError(f"本地根目录不存在：{cfg.local_root}")
    if not cfg.remote_root.startswith("/"):
        raise RuntimeError("远程项目路径必须是 Ubuntu 绝对路径，例如 /root/app")
    if cfg.docker_enabled and not cfg.docker_project_dir.startswith("/"):
        raise RuntimeError("DOCKER_COMPOSE_PROJECT_DIR / --docker-dir 必须是 Ubuntu 绝对路径，例如 /root/app")
    if DELETE_MODE not in {"trash", "delete"}:
        raise RuntimeError("DELETE_MODE 只能是 trash 或 delete")
    if TIE_BREAKER not in {"skip", "local", "remote"}:
        raise RuntimeError("TIE_BREAKER 只能是 skip/local/remote")


def confirm_or_exit(cfg: SyncConfig, actions: List[Action]) -> None:
    if cfg.dry_run or cfg.yes or not actions:
        return
    ans = input("确认执行以上同步计划？输入 yes 继续，其它任意内容取消：").strip().lower()
    if ans != "yes":
        print("已取消。")
        sys.exit(0)


def warn_sqlite_actions(actions: List[Action]) -> None:
    db_actions = [a for a in actions if a.kind in {"upload", "download"} and is_sqlite_path(a.rel)]
    if not db_actions:
        return
    print("\n注意：同步计划中包含 SQLite 数据库文件：")
    for a in db_actions[:20]:
        print(f"  - {a.kind}: {a.rel}")
    if len(db_actions) > 20:
        print(f"  ... 其余 {len(db_actions) - 20} 个数据库文件省略")
    print("建议先停止 Flask/Gunicorn/后台任务，或确认数据库没有写入，再执行同步。\n")


def main() -> int:
    cfg = parse_args()
    if cfg.web:
        return run_web_server(cfg)
    preflight(cfg)
    log_file = setup_logging(cfg.local_root)

    print("\n========== bidisync 配置 ==========")
    print(f"本地根目录: {cfg.local_root}")
    print(f"远程目标  : {cfg.remote_user}@{cfg.remote_host}:{cfg.remote_root}")
    print(f"删除策略  : {DELETE_MODE}")
    print(f"传输并发  : {cfg.transfer_workers}")
    print(f"Docker编排: {'启用' if cfg.docker_enabled else '禁用'}")
    if cfg.docker_enabled:
        print(f"Compose目录: {cfg.docker_project_dir}")
    print(f"日志文件  : {log_file}")
    print("==================================\n")

    password = getpass.getpass(f"请输入 {cfg.remote_user}@{cfg.remote_host} 的 SSH 密码：")

    try:
        local_manifest = read_local_manifest(cfg.local_root)
        remote_manifest = read_remote_manifest(cfg, password)
        baseline = choose_manifest(local_manifest, remote_manifest)
        logging.info("使用 baseline 清单：saved_at=%s, files=%d, dirs=%d", baseline.saved_at, len(baseline.files), len(baseline.dirs))

        with cf.ThreadPoolExecutor(max_workers=2) as ex:
            fut_local = ex.submit(scan_local, cfg.local_root, cfg.scan_workers)
            fut_remote = ex.submit(scan_remote, cfg, password)
            local_snap = fut_local.result()
            remote_snap = fut_remote.result()

        actions = plan_sync(local_snap, remote_snap, baseline, cfg)
        print_plan(actions)
        warn_sqlite_actions(actions)

        conflict_skips = [a for a in actions if a.kind == "conflict_skip"]
        if conflict_skips:
            print("警告：存在无法自动判定的双向冲突，已默认跳过。请查看上方 conflict_skip 项和日志。")

        if cfg.dry_run:
            print("dry-run 模式：未执行任何同步。")
            return 0

        confirm_or_exit(cfg, actions)

        if not actions:
            new_manifest = build_new_manifest(local_snap, remote_snap, baseline, [], set())
            write_local_manifest(cfg.local_root, new_manifest)
            write_remote_manifest(cfg, password, new_manifest)
            return 0

        logging.info("Docker Compose 策略：%s", docker_plan_for_actions(actions, cfg))
        results = execute_actions_with_docker(cfg, password, actions, use_tqdm=True)
        failed_rels = {a.rel for a, exc in results if exc is not None}
        failed_count = len(failed_rels)
        ok_count = len(results) - failed_count

        if failed_count:
            logging.warning("同步完成但有失败：成功 %d，失败 %d。失败详情见 %s", ok_count, failed_count, cfg.local_root / SYNC_DIR_NAME / FAILURES_NAME)
        else:
            logging.info("同步全部完成：%d 项", ok_count)

        new_manifest = build_new_manifest(local_snap, remote_snap, baseline, actions, failed_rels)
        write_local_manifest(cfg.local_root, new_manifest)
        write_remote_manifest(cfg, password, new_manifest)
        print(f"\n同步完成：成功 {ok_count} 项，失败 {failed_count} 项。")
        if failed_count:
            print(f"失败详情：{cfg.local_root / SYNC_DIR_NAME / FAILURES_NAME}")
            return 2
        return 0

    except KeyboardInterrupt:
        print("\n用户中断。")
        return 130
    except (socket.timeout, paramiko.SSHException) as exc:
        logging.error("SSH/网络错误：%s", exc)
        return 3
    except Exception as exc:  # noqa: BLE001
        logging.exception("同步器异常退出：%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
