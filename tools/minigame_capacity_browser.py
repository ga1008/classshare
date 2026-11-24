"""Test-only file handoff between the capacity fixture and its separate Chromium executor."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
PINNED_MANIFEST = "8a1d6e67814eea9c743df9afc77ffe5a98b4b024636cf83ee6df171fef5ea6c4"


def owned_directory(value):
    directory = Path(value).resolve(strict=True)
    if (
        directory.parent != (ROOT / ".codex-temp/scratch").resolve()
        or not directory.name.startswith("game-http-")
        or not (directory / ".owner").is_file()
    ):
        raise ValueError("Requires the newly owned capacity fixture")
    return directory


def checked_runtime(value, digest, directory):
    path = Path(value).resolve(strict=True)
    if digest != PINNED_MANIFEST or not path.is_relative_to(directory) or path.name != "runtime":
        raise ValueError("Requires the reviewed example in this synthetic fixture")
    return path


def request(directory, runtime, digest):
    path = checked_runtime(runtime, digest, directory)
    folder = directory / "browser-handoff" / uuid.uuid4().hex
    folder.mkdir()
    pending = folder / "new.json"
    pending.write_text(json.dumps({"path": str(path), "digest": digest}), encoding="utf8")
    pending.replace(folder / "request.json")
    deadline = time.monotonic() + 27
    while time.monotonic() < deadline:
        response = folder / "response.json"
        if response.is_file():
            print(response.read_text(encoding="utf8"))
            return 0
        time.sleep(0.04)
    return 2


def serve(directory, browser):
    root = directory / "browser-handoff"
    while True:
        for source in root.glob("*/request.json"):
            target = source.with_name("response.json")
            if target.is_file():
                continue
            supplied = json.loads(source.read_text(encoding="utf8"))
            path = checked_runtime(supplied["path"], supplied["digest"], directory)
            # This probe recomputes the manifest before opening the hard-pinned
            # repository example. Nothing in the package selects the executable.
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    str(ROOT / "tools/minigame_browser_probe.py"),
                    "--browser",
                    str(browser),
                    "--synthetic-digest",
                    PINNED_MANIFEST,
                    "--trusted-browser-harness",
                    str(path),
                    PINNED_MANIFEST,
                ],
                capture_output=True,
                timeout=25,
                check=False,
            )
            if result.returncode or len(result.stdout) > 16384:
                # Only this byte-pinned synthetic package reaches the executor.
                diagnostic = result.stderr[-4096:].decode("utf-8", errors="replace")
                print("Reviewed browser probe failed", result.returncode, diagnostic, file=sys.stderr, flush=True)
                answer = {"status": "failed", "digest": PINNED_MANIFEST}
            else:
                answer = json.loads(result.stdout)
            tmp = source.with_name("result.tmp")
            tmp.write_text(json.dumps(answer), encoding="utf8")
            os.replace(tmp, target)
        time.sleep(0.04)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("client", "server"))
    parser.add_argument("directory")
    parser.add_argument("argument")
    parser.add_argument("digest", nargs="?")
    parser.add_argument("--synthetic-digest", choices=(PINNED_MANIFEST,))
    args = parser.parse_args()
    directory = owned_directory(args.directory)
    if args.mode == "client":
        return request(directory, args.argument, args.digest)
    return serve(directory, Path(args.argument).resolve(strict=True))


if __name__ == "__main__":
    raise SystemExit(main())
