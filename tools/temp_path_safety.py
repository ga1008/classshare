"""Strict paths and exclusive, verified ownership for temporary CLI workspaces."""
from __future__ import annotations

from pathlib import Path
import stat
from contextlib import contextmanager
from functools import wraps
import hashlib
import inspect
import json
import os
import shutil
import uuid


def _reject_link_ancestors(path: Path) -> None:
    # Inspect the supplied path before resolve() erases a symlink/junction.
    # Nutstore/cloud placeholders also have ReparsePoint set: only the two
    # actual path-redirection tags are forbidden, including on Python 3.11.
    link_tags = {
        getattr(stat, "IO_REPARSE_TAG_SYMLINK", 0xA000000C),
        getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003),
    }
    for current in (path, *path.parents):
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", None) in link_tags:
            raise ValueError(f"temporary workspace cannot use a symlink or junction: {current}")


def resolve_temp_child(
    raw: str | Path | None, *, repo_root: Path, temp_root: Path, default: Path,
) -> Path:
    """Resolve a strict child without permitting redirected parent components."""
    candidate = Path(raw) if raw else default
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    _reject_link_ancestors(temp_root)
    _reject_link_ancestors(candidate)
    resolved = candidate.resolve()
    parent = temp_root.resolve()
    if resolved == parent or parent not in resolved.parents:
        raise ValueError(f"temporary workspace must be a strict child of {parent}; got {resolved}")
    return resolved


WORKSPACE_OWNER = ".lanshare-temp-workspace.json"


def _directory_identity(path: Path) -> list[int]:
    info = path.stat()
    return [info.st_dev, info.st_ino]


def _workspace_owner(path: Path, tool: str) -> dict:
    marker = path / WORKSPACE_OWNER
    _reject_link_ancestors(marker)
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(
            f"Unowned workspace preserved: {path}; choose a new --runtime-root/--lab-root"
        ) from error
    if (not isinstance(value, dict) or value.get("kind") != "lanshare-tool-workspace-v1"
            or value.get("tool") != tool or value.get("path") != str(path)
            or value.get("directory_identity") != _directory_identity(path)):
        raise ValueError(f"Workspace ownership changed or belongs to another tool: {path}")
    return value


def _check_workspace_tree(path: Path) -> None:
    def traversal_error(error):
        raise error

    for current, directories, files in os.walk(path, followlinks=False, onerror=traversal_error):
        parent = Path(current)
        for name in directories + files:
            _reject_link_ancestors(parent / name)
        if parent != path and WORKSPACE_OWNER in files:
            raise ValueError(f"Nested independently owned workspace preserved: {parent}")


def _workspace_lock(path: Path) -> Path:
    digest = hashlib.sha256(os.path.normcase(str(path)).encode("utf-8")).hexdigest()[:24]
    return path.parent / f".lanshare-workspace-{digest}.lock"


def _check_workspace_parents(path: Path, root: Path) -> None:
    for parent in path.parents:
        if parent == root:
            break
        if (parent / WORKSPACE_OWNER).exists() or _workspace_lock(parent).exists():
            raise ValueError(f"Cannot place a maintenance workspace inside another owned run: {parent}")


@contextmanager
def _claim_workspace(path: Path, *, temp_root: Path, tool: str, reset: bool):
    root = temp_root.resolve()
    path = resolve_temp_child(path, repo_root=root.parent, temp_root=temp_root, default=path)
    if path.relative_to(root).parts[0].casefold() in {"evidence", "scratch"}:
        raise ValueError("Maintenance workspaces cannot replace retained evidence or test scratch")
    _check_workspace_parents(path, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # The lock is outside the reset directory and belongs to this exact path.
    # Never remove a stale lock: an unconfirmed process may still own the run.
    lock = _workspace_lock(path)
    try:
        stream = lock.open("x", encoding="utf-8")
    except FileExistsError as error:
        raise ValueError(f"Workspace is locked; preserve it and choose a new runtime path: {path}") from error
    identity = _directory_identity(lock)
    try:
        json.dump({"tool": tool, "path": str(path), "pid": os.getpid(), "token": uuid.uuid4().hex}, stream)
        stream.flush()
        _reject_link_ancestors(path)
        # An ancestor may have been claimed after the pre-allocation check.
        _check_workspace_parents(path, root)
        if path.exists():
            if not path.is_dir():
                raise ValueError(f"Workspace must be a directory: {path}")
            if any(path.iterdir()):
                _workspace_owner(path, tool)
                _check_workspace_tree(path)
                if reset:
                    _workspace_owner(path, tool)
                    shutil.rmtree(path)
        if not path.exists():
            path.mkdir()
        marker = path / WORKSPACE_OWNER
        if not marker.exists():
            with marker.open("x", encoding="utf-8") as owner_stream:
                json.dump({"kind": "lanshare-tool-workspace-v1", "tool": tool, "path": str(path),
                           "directory_identity": _directory_identity(path)}, owner_stream, indent=2)
        yield path
    finally:
        stream.close()
        _reject_link_ancestors(lock)
        if _directory_identity(lock) != identity:
            raise ValueError(f"Workspace lock identity changed; preserve it: {lock}")
        lock.unlink()


def owned_temp_workspace(resolve, *, temp_root, tool: str, clean_argument: str | None = None):
    """Serialize a tool's operation and reset only its own verified workspace.

    Historical directories without this tool's marker are retained. Fixed CLI
    defaults can be rerun once created here; old paths need a new runtime name.
    Read-only resolvers and the SQLite test runner retain their existing API.
    """
    def decorate(function):
        signature = inspect.signature(function)
        path_argument = next(iter(signature.parameters))

        @wraps(function)
        def guarded(*args, **kwargs):
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            path = resolve(bound.arguments[path_argument])
            # Inputs cannot survive a reset if they live in the output tree.
            for name in ("source_db", "remediation_report", "postgres_drill_report",
                         "postgres_performance_report", "data_root", "exception_manifest", "search_roots"):
                raw = bound.arguments.get(name)
                values = raw if isinstance(raw, (list, tuple)) else (raw,)
                for value in values:
                    if value is not None:
                        candidate = Path(value)
                        candidate = (candidate if candidate.is_absolute() else temp_root().parent / candidate).resolve()
                        if candidate == path or path in candidate.parents:
                            raise ValueError(f"Input {name} must stay outside the disposable workspace: {candidate}")
            bound.arguments[path_argument] = path
            reset = bool(bound.arguments[clean_argument]) if clean_argument else True
            with _claim_workspace(path, temp_root=temp_root(), tool=tool, reset=reset):
                return function(*bound.args, **bound.kwargs)
        return guarded
    return decorate
