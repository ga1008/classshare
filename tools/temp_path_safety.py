"""Path checks for CLI-owned temporary database workspaces; no cleanup actions."""
from __future__ import annotations

from pathlib import Path
import stat


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
