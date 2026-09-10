"""Runner launch contract. The privileged launcher owns mounts and networking."""

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

INSTALL = Path(__file__).resolve().parent
PROFILE_FILES = ("package.json", "cordis.patch.yml")
REQUIRED = (
    "DSH_TASK_ID", "DSH_ACTOR_ID", "DSH_ATTEMPT_ID", "DSH_FENCING_TOKEN",
    "DSH_GATEWAY_BASE_URL", "DSH_GATEWAY_MODEL", "DSH_GATEWAY_TOKEN",
    "DSH_BROKER_MCP_URL", "DSH_BROKER_TOKEN",
)
OPTIONAL = ("DSH_SEARCH_MODEL",)


def profile_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for name in PROFILE_FILES:
        digest.update(name.encode("utf-8") + b"\0")
        # Repository checkout line endings must not alter profile identity.
        digest.update((root / name).read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return digest.hexdigest()


def exact_mount(path: Path, *, readonly: bool) -> bool:
    target = str(path.resolve())
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        fields = line.split()
        if len(fields) > 5 and fields[4].replace("\\040", " ") == target:
            return ("ro" if readonly else "rw") in fields[5].split(",")
    return False


def runtime_evidence() -> dict[str, str]:
    package = json.loads((INSTALL / "node_modules/@deepseek-ai/dsh/package.json").read_text())
    if package["version"] != "0.1.5-rc.1":
        raise ValueError("Unexpected DSH package version")
    digest = profile_digest(INSTALL / "profile")
    if digest != (INSTALL / "profile.sha256").read_text().strip():
        raise ValueError("Image-owned profile digest differs from the build evidence")
    return {"dsh_package_version": package["version"], "profile_sha256": digest}


def main() -> int:
    if sys.argv[1:] == ["--write-profile-digest"]:
        (INSTALL / "profile.sha256").write_text(profile_digest(INSTALL / "profile") + "\n")
        return 0
    if sys.argv[1:] == ["--evidence"]:
        # A launcher can inspect immutable image evidence without task credentials,
        # writable mounts, gateway access, ACP startup or a model request.
        print(json.dumps(runtime_evidence(), separators=(",", ":")))
        return 0
    if len(sys.argv) != 1:
        raise ValueError("Runner accepts no caller command or profile arguments")
    if os.getuid() == 0:
        raise ValueError("DSH runner must use a non-root account")
    for name in REQUIRED:
        if not os.environ.get(name, "").strip():
            raise ValueError(f"Missing required task setting: {name}")
    if not re.fullmatch(r"[a-z][a-z0-9_-]*:[^\s:]+", os.environ["DSH_ACTOR_ID"]):
        raise ValueError("DSH_ACTOR_ID must use role:id")
    for name in ("DSH_GATEWAY_BASE_URL", "DSH_BROKER_MCP_URL"):
        url = urlsplit(os.environ[name])
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError(f"Invalid trusted service URL: {name}")
        if url.scheme != "http" or url.hostname != "127.0.0.1" or url.port != 8787:
            raise ValueError("Runner service URLs must use its local Unix-socket relay")
    if urlsplit(os.environ["DSH_GATEWAY_BASE_URL"]).path != "/api/agent-model":
        raise ValueError("Unexpected model gateway path")
    if not urlsplit(os.environ["DSH_BROKER_MCP_URL"]).path.startswith("/api/agent-bridge/"):
        raise ValueError("Unexpected platform MCP path")
    if os.environ["DSH_GATEWAY_TOKEN"] == os.environ["DSH_BROKER_TOKEN"]:
        raise ValueError("Model and Broker task credentials must be distinct")
    for boot_file in ("/var/lib/dsh/cordis.patch.yml", "/var/lib/dsh/.env", "/workspace/.env"):
        if os.path.lexists(boot_file):
            raise ValueError("Mutable home/project startup overlays are forbidden")
    profile = Path("/var/lib/dsh/profiles/lanshare")
    if not exact_mount(profile, readonly=True):
        raise ValueError("The exact lanshare profile directory requires a read-only mount")
    if not exact_mount(profile / "cordis.yml", readonly=False):
        raise ValueError("The generated Cordis root requires its exact per-attempt writable file mount")
    evidence = runtime_evidence()
    if profile_digest(profile) != evidence["profile_sha256"]:
        raise ValueError("Runner profile digest differs from the built image")
    # Do not inherit deployment secrets, user dotfiles or arbitrary Node options.
    env = {name: os.environ[name] for name in REQUIRED + OPTIONAL if os.environ.get(name)}
    env.update({"PATH": "/opt/lanshare-dsh/python/bin:/usr/local/bin:/usr/bin:/bin", "HOME": "/var/lib/dsh", "DSH_HOME": "/var/lib/dsh",
                "LANG": "C.UTF-8", "DSH_TELEMETRY_DISABLED": "1", "TMPDIR": "/tmp"})
    os.chdir("/workspace")
    os.execve("/usr/local/bin/node", ["node", str(INSTALL / "runner.mjs")], env)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"DSH runner refused startup: {exc}", file=sys.stderr)
        raise SystemExit(78)
