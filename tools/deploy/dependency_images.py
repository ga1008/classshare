"""Versioned, locally retained dependency images; no application imports or DB access."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFY_SCRIPT = "deployment/docker/verify-dependencies.sh"
RECIPES = {
    "runtime": ("DockerfileBase", "requirements.txt", "requirements.lock.txt", "requirements-docker.txt", VERIFY_SCRIPT),
    "frontend": ("deployment/docker/Dockerfile.frontend-deps", "package.json", "package-lock.json", VERIFY_SCRIPT),
}
LABEL_PREFIX = "io.lanshare.dependencies."


def normalized_bytes(path: Path) -> bytes:
    return path.read_bytes().replace(b"\r\n", b"\n")


def image_plan(root: Path = REPO_ROOT) -> dict:
    result = {}
    for kind, inputs in RECIPES.items():
        checksums = "".join(
            f"{hashlib.sha256(normalized_bytes(root / name)).hexdigest()}  {name}\n"
            for name in sorted(inputs)
        )
        fingerprint = hashlib.sha256(checksums.encode()).hexdigest()
        result[kind] = {
            "kind": kind,
            "dockerfile": inputs[0],
            "inputs": list(inputs),
            "checksums": checksums,
            "fingerprint": fingerprint,
            "image": f"lanshare-{kind}-deps:{fingerprint[:20]}",
        }
    return result


def write_context(root: Path, destination: Path, spec: dict) -> None:
    # A small allowlist prevents source code, runtime data and secrets entering a base.
    for name in spec["inputs"]:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(normalized_bytes(root / name))
    (destination / "dependency-inputs.sha256").write_bytes(spec["checksums"].encode())


def inspect_labels(image: str) -> dict | None:
    result = subprocess.run(
        ["docker", "image", "inspect", image, "--format", "{{json .Config.Labels}}"],
        text=True, capture_output=True, check=False,
    )
    if result.returncode:
        if "No such image" in result.stderr or "No such object" in result.stderr:
            return None
        raise RuntimeError(result.stderr.strip() or f"Cannot inspect {image}")
    return json.loads(result.stdout) or {}


def verify_labels(spec: dict, labels: dict) -> None:
    if (labels.get(LABEL_PREFIX + "fingerprint") != spec["fingerprint"]
            or labels.get(LABEL_PREFIX + "kind") != spec["kind"]):
        raise RuntimeError(f"Dependency image metadata mismatch: {spec['image']}; refusing to reuse or overwrite it")


def ensure_images(root: Path = REPO_ROOT) -> dict:
    # Detect daemon/permission errors before treating an image as missing.
    subprocess.run(["docker", "info", "--format", "{{.OSType}}"], check=True)
    plan = image_plan(root)
    for spec in plan.values():
        started = time.monotonic()
        labels = inspect_labels(spec["image"])
        if labels is None:
            print(f"DEPENDENCY_BUILD={spec['image']}", flush=True)
            with tempfile.TemporaryDirectory(prefix="lanshare-dependency-context-") as temp:
                context = Path(temp)
                write_context(root, context, spec)
                subprocess.run([
                    "docker", "build", "--pull=false", "--progress=plain",
                    "--build-arg", f"DEPENDENCY_FINGERPRINT={spec['fingerprint']}",
                    "--file", str(context / spec["dockerfile"]),
                    "--tag", spec["image"], str(context),
                ], check=True)
            labels = inspect_labels(spec["image"])
            if labels is None:
                raise RuntimeError(f"Built image is missing: {spec['image']}")
        else:
            print(f"DEPENDENCY_REUSED={spec['image']}", flush=True)
        verify_labels(spec, labels)
        print(f"DEPENDENCY_READY={spec['image']} seconds={time.monotonic() - started:.2f}", flush=True)

    # Promote convenience aliases only once BOTH versions are valid. Builds use
    # the fingerprinted tags; old tags remain available for application rollback.
    for kind, spec in plan.items():
        subprocess.run(["docker", "image", "tag", spec["image"], f"lanshare-{kind}-deps:current"], check=True)
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "ensure", "ref"))
    parser.add_argument("kind", nargs="?", choices=tuple(RECIPES))
    args = parser.parse_args()
    if args.command == "ensure":
        ensure_images()
    elif args.command == "ref":
        if not args.kind:
            parser.error("ref requires runtime or frontend")
        print(image_plan()[args.kind]["image"])
    else:
        print(json.dumps(image_plan(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
