"""Serialize `npm run build` across parallel workers.

The static graph is content-hashed (old graphs stay available), but the build
itself writes shared outputs. Acquire an exclusive lock so two workers never
build at the same time. Prints the resulting graph revision.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / ".codex-temp" / ".lq-build.lock"
MANIFEST = ROOT / "static" / "assets" / "manifest.json"


def main() -> int:
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + float(os.environ.get("LQ_BUILD_LOCK_TIMEOUT", "1800"))
    while True:
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.time() > deadline:
                print("build lock timeout; inspect .codex-temp/.lq-build.lock", file=sys.stderr)
                return 2
            time.sleep(5)
    try:
        os.write(fd, json.dumps({"pid": os.getpid(), "started": time.time()}).encode())
        os.close(fd)
        command = ["npm.cmd" if os.name == "nt" else "npm", "run", "build"]
        result = subprocess.run(command, cwd=ROOT)
        if result.returncode != 0:
            return result.returncode
        if MANIFEST.is_file():
            revision = json.loads(MANIFEST.read_text(encoding="utf-8")).get("revision")
            print(f"LQ_GRAPH={revision}")
        return 0
    finally:
        try:
            LOCK.unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    sys.exit(main())
