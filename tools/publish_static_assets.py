"""Publish image-owned immutable files for nginx; preserve prior release graphs.

Only the main application runs this before accepting requests. The shared volume
contains code assets, never runtime/user data. No implicit retention deletion is
safe while an examination/editor can still import a previous release's chunk.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile
import tempfile
from typing import BinaryIO


def _publish_file(target: Path, content: bytes) -> int:
    if target.exists():
        if hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(content).digest():
            raise ValueError(f"Published immutable asset collision: {target}")
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".publishing-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        os.chmod(temporary, 0o644)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return 1


def seed_legacy_vite_assets(stream: BinaryIO, destination: Path) -> int:
    """Seed a pre-graph app's flat Vite assets from docker-cp's tar stream.

    This must run before replacing the old app container on the first upgrade.
    Archive members are validated and read directly; no tar extraction occurs.
    """
    hashed = re.compile(r"^[A-Za-z0-9_.-]+-[A-Za-z0-9_-]{8,}\.[A-Za-z0-9]+(?:\.gz)?$")
    count = 0
    matched = 0
    with tarfile.open(fileobj=stream, mode="r|") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or "\\" in member.name:
                raise ValueError("Legacy static archive path escapes asset directory")
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError("Legacy static archive contains a link or special file")
            # docker cp dir/. emits the contents, while some Docker versions
            # retain the assets/ prefix. Neither permits nested file trees.
            if len(path.parts) > 2 or (len(path.parts) == 2 and path.parts[0] != "assets"):
                raise ValueError("Legacy static archive has an unexpected directory")
            if not hashed.fullmatch(path.name):
                continue
            matched += 1
            if member.size > 64 * 1024 * 1024:
                raise ValueError("Legacy static asset exceeds the code-asset size bound")
            content = archive.extractfile(member).read()
            count += _publish_file(destination / "dist" / "assets" / path.name, content)
    if not matched:
        raise ValueError("Previous application archive contains no recognizable immutable Vite assets")
    return count


def publish_static_assets(source: Path, destination: Path) -> int:
    manifest_path = source / "assets" / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("Run npm run build before publishing static assets")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    revision = manifest.get("revision", "")
    if manifest.get("schema") != 1 or not re.fullmatch(r"[a-f0-9]{64}", revision):
        raise ValueError("Invalid static graph publication manifest")
    entries = manifest.get("entries")
    if not isinstance(entries, dict) or not entries:
        raise ValueError("Empty static graph publication manifest")
    for name, relative in entries.items():
        if (
            ".." in PurePosixPath(name).parts or "\\" in name
            or relative != f"assets/{revision}/{name}"
        ):
            raise ValueError(f"Static graph publication path escapes revision: {name}")
        if not (source / relative).is_file():
            raise FileNotFoundError(f"Incomplete static graph: {relative}")
    vite_manifest = json.loads((source / "dist" / "manifest.json").read_text(encoding="utf-8"))
    for entry in vite_manifest.values():
        for name in [entry.get("file"), *entry.get("css", [])]:
            if name and (
                ".." in PurePosixPath(name).parts or not str(name).startswith("assets/")
                or not (source / "dist" / name).is_file()
            ):
                raise FileNotFoundError(f"Incomplete Vite graph: {name}")
        for name in [*entry.get("imports", []), *entry.get("dynamicImports", [])]:
            if name not in vite_manifest:
                raise ValueError(f"Vite graph import missing from manifest: {name}")
    roots = [source / "assets" / revision, source / "dist" / "assets"]
    count = 0
    for root in roots:
        if not root.is_dir():
            raise FileNotFoundError(f"Missing built static graph: {root}")
        for file in root.rglob("*"):
            if file.is_symlink():
                raise ValueError(f"Refusing static asset symlink: {file}")
            if not file.is_file():
                continue
            target = destination / file.relative_to(source)
            content = file.read_bytes()
            count += _publish_file(target, content)
    return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("/app/static"))
    parser.add_argument("--destination", type=Path, default=Path("/srv/lanshare-static"))
    parser.add_argument("--seed-vite-tar", choices=["-"], help="Seed the previous app's immutable Vite files from stdin before first cutover")
    arguments = parser.parse_args()
    if arguments.seed_vite_tar:
        print(f"Seeded {seed_legacy_vite_assets(sys.stdin.buffer, arguments.destination)} previous Vite files")
    else:
        print(f"Published {publish_static_assets(arguments.source, arguments.destination)} immutable static files")
