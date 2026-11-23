"""A credential-free spool client and an external Docker probe broker.

The web app invokes 'submit'; only the separate broker can use Docker.
"""

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid


def report(path, value):
    temp = path.with_name(path.name + ".partial")
    handle = temp.open("x", encoding="utf-8")
    try:
        with handle:
            json.dump(value, handle)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def submit(args):
    root = args.runtime.resolve(strict=True)
    if (
        root.name != "runtime"
        or root.parent.name != "prepared"
        or not re.fullmatch("[a-f0-9]{32}", root.parent.parent.name)
    ):
        raise ValueError("unexpected job path")
    if not re.fullmatch("[a-f0-9]{64}", args.digest):
        raise ValueError("invalid digest")
    args.queue.mkdir(parents=True, exist_ok=True)
    job = uuid.uuid4().hex
    request = args.queue / (job + ".request")
    result = args.queue / (job + ".result")
    report(request, {"stage": root.parent.parent.name, "digest": args.digest, "until": int(time.time()) + 26})
    try:
        until = time.monotonic() + 26
        while time.monotonic() < until:
            if result.is_file() and result.stat().st_size <= 16384:
                print(result.read_text(encoding="utf-8"))
                return 0
            time.sleep(0.1)
        return 2
    finally:
        request.unlink(missing_ok=True)
        result.unlink(missing_ok=True)


def broker_job(args, entry):
    ticket = entry.stem
    if not re.fullmatch("[a-f0-9]{32}", ticket) or entry.is_symlink() or entry.stat().st_size > 2048:
        return
    result = entry.with_suffix(".result")
    data = json.loads(entry.read_text(encoding="utf-8"))
    stage, digest = data["stage"], data["digest"]
    if not re.fullmatch("[a-f0-9]{32}", stage) or not re.fullmatch("[a-f0-9]{64}", digest):
        return
    if data["until"] < time.time():
        entry.unlink(missing_ok=True)
        result.unlink(missing_ok=True)
        return
    if result.exists():
        return
    runtime = args.game_root.resolve(strict=True) / "staging" / stage / "prepared" / "runtime"
    if runtime.resolve(strict=True) != runtime or "," in str(runtime):
        raise ValueError("invalid mount")
    name = "lanshare-minigame-probe-" + ticket
    cmd = [
        "docker",
        "run",
        "--rm",
        "--init",
        "--name",
        name,
        "--network",
        "none",
        "--user",
        "pwuser",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--security-opt",
        "seccomp=" + str(args.seccomp.resolve(strict=True)),
        "--read-only",
        "--pids-limit",
        "192",
        "--memory",
        "512m",
        "--memory-swap",
        "512m",
        "--cpus",
        "0.75",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=160m",
        "--shm-size",
        "96m",
        "-e",
        "HOME=/tmp",
        "-e",
        "MINIGAMES_ISOLATED_BROWSER=1",
        "--mount",
        f"type=bind,src={runtime},dst=/input,readonly",
        args.image,
        "/input",
        digest,
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=max(1, min(22, data["until"] - time.time())), check=False
        )
        parsed = (
            json.loads(proc.stdout)
            if not proc.returncode and len(proc.stdout) < 16384
            else {"status": "unavailable"}
        )
    except (OSError, ValueError, subprocess.TimeoutExpired):
        parsed = {"status": "unavailable"}
    finally:
        # --rm handles normal exit. A timed-out Docker CLI can leave its container running.
        subprocess.run(
            ["docker", "rm", "-f", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=8,
            check=False,
        )
    if entry.exists():
        report(result, parsed)


def reap_orphan_reports(queue, cutoff):
    # A killed client or broker can leave an atomic-write temporary file or an
    # unclaimed result. Only the spool's own expired ticket files are eligible.
    for path in queue.iterdir():
        if not re.fullmatch(r"[0-9a-f]{32}\.(?:result|(?:request|result)\.partial|partial)", path.name):
            continue
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_mtime > cutoff:
                continue
            request = queue / (path.name.split(".", 1)[0] + ".request")
            if not request.exists():
                path.unlink(missing_ok=True)
        except FileNotFoundError:
            continue


def main():
    parser = argparse.ArgumentParser()
    roles = parser.add_subparsers(dest="role", required=True)
    client = roles.add_parser("submit")
    host = roles.add_parser("serve")
    for p in (client, host):
        p.add_argument("--queue", required=True, type=Path)
    client.add_argument("runtime", type=Path)
    client.add_argument("digest")
    host.add_argument("--game-root", required=True, type=Path)
    host.add_argument("--image", default="lanshare-minigame-probe:1")
    host.add_argument("--seccomp", required=True, type=Path)
    args = parser.parse_args()
    if args.role == "submit":
        return submit(args)
    args.queue.mkdir(parents=True, exist_ok=True)
    next_cleanup = 0
    while True:
        if time.monotonic() >= next_cleanup:
            reap_orphan_reports(args.queue, time.time() - 300)
            next_cleanup = time.monotonic() + 60
        for entry in sorted(args.queue.glob("*.request")):
            try:
                broker_job(args, entry)
            except (OSError, KeyError, ValueError, subprocess.TimeoutExpired):
                print("probe request requires retry", file=sys.stderr)
        time.sleep(0.25)


if __name__ == "__main__":
    raise SystemExit(main())
