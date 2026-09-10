"""Exact-source Linux file-reader probe, without importing or starting the app."""
import argparse
import ast
import hashlib
import json
import multiprocessing
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tempfile
import types
from typing import Any


class HTTPException(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code


def function(source, name, namespace):
    tree = ast.parse(source.read_text(encoding="utf-8-sig"))
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name]
    if len(selected) != 1:
        raise RuntimeError("Missing exact source function")
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(source), "exec"), namespace)
    return namespace[name]


def reader(upload_source, read_source, root):
    namespace = {"__package__": "lanshare_read_probe", "os": os, "Path": Path, "PurePosixPath": PurePosixPath, "Any": Any, "stat": stat, "hashlib": hashlib,
                 "tempfile": tempfile, "HTTPException": HTTPException, "MAX_FILE_BYTES": 4096,
                 "MAX_DOCUMENT_FILE_BYTES": 10485760, "MAX_DOCUMENT_TEXT_BYTES": 2097152,
                 "allowed_file_roots": lambda: [root]}
    module = types.ModuleType("lanshare_read_probe.agent_platform_multipart_service")
    module._open_confined = function(upload_source, "_open_confined", namespace)
    sys.modules[module.__name__] = module
    return function(read_source, "read_platform_file", namespace), module


def fifo_child(upload_source, read_source, root, queue):
    read, _ = reader(upload_source, read_source, root)
    try:
        read(str(root / "fifo.md"))
    except (ValueError, HTTPException):
        queue.put(True)
    else:
        queue.put(False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--upload-source", type=Path, required=True)
    parser.add_argument("--read-source", type=Path, required=True)
    args = parser.parse_args()
    if os.name != "posix":
        raise SystemExit("This syscall probe requires Linux")
    with tempfile.TemporaryDirectory(prefix="lanshare-read-probe-") as directory:
        root = Path(directory) / "workspace"
        root.mkdir()
        outside = Path(directory) / "outside"
        outside.mkdir()
        (outside / "secret.md").write_text("foreign synthetic secret")
        read, module = reader(args.upload_source, args.read_source, root)
        checks = {}
        (root / "normal.md").write_text("authorized fixture")
        regular = read(str(root / "normal.md"))
        checks["regular_snapshot"] = (regular["content"] == "authorized fixture"
            and regular["sha256"] == hashlib.sha256(b"authorized fixture").hexdigest())
        (root / "redirect.md").symlink_to(outside / "secret.md")
        (root / "redirect").symlink_to(outside, target_is_directory=True)
        for key, target in (("symlink_denied", root / "redirect.md"), ("directory_redirect_denied", root / "redirect" / "secret.md")):
            try:
                read(str(target))
            except (ValueError, HTTPException):
                checks[key] = True
            else:
                checks[key] = False
        (root / "oversize.md").write_bytes(b"x" * 4097)
        try:
            read(str(root / "oversize.md"))
        except ValueError:
            checks["oversize_denied"] = True
        else:
            checks["oversize_denied"] = False
        original_open = module._open_confined

        def swap_after_open(allowed_root, relative):
            descriptor = original_open(allowed_root, relative)
            (root / "normal.md").rename(root / "original.md")
            (root / "normal.md").symlink_to(outside / "secret.md")
            return descriptor

        module._open_confined = swap_after_open
        checks["post_open_swap_keeps_authorized_inode"] = read(str(root / "normal.md"))["content"] == "authorized fixture"
        os.mkfifo(root / "fifo.md", 0o600)
        context = multiprocessing.get_context("fork")
        queue = context.Queue()
        process = context.Process(target=fifo_child, args=(args.upload_source, args.read_source, root, queue))
        process.start()
        process.join(2)
        if process.is_alive():
            process.terminate()
            process.join(2)
            checks["fifo_without_writer_denied_without_blocking"] = False
        else:
            checks["fifo_without_writer_denied_without_blocking"] = process.exitcode == 0 and queue.get(timeout=1)
        queue.close()
    report = {"kind": "exact_source_linux_reader_component_probe", "checks": checks,
              "source_sha256": {str(path.name): hashlib.sha256(path.read_bytes()).hexdigest() for path in (args.upload_source, args.read_source)},
              "production_data_accessed": False, "paid_model_calls": 0, "full_authenticated_http_integration": False,
              "passed": all(checks.values())}
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
